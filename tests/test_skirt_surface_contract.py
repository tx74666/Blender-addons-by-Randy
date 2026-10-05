"""Pure boundary checks; native geometry/binding is deliberately not mocked.

Load only the module's Python declarations so the tests do not import Blender
or register the add-on. Native effect acceptance belongs to the isolated
Blender integration harness.
"""
import ast
import copy
import hashlib
import json
import math
from pathlib import Path
import types
import unittest
from unittest import mock
import sys


SOURCE = Path(__file__).resolve().parents[1] / "addons/character_designer/skirt_surface.py"


def load():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    declarations = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.ClassDef))]
    space = {"copy": copy, "hashlib": hashlib, "json": json, "math": math,
             "bpy": types.SimpleNamespace(types=types.SimpleNamespace(ID=type("ID", (), {}))),
             "skirt": types.SimpleNamespace(RIG_KEY="rig", OWNER_KEY="owner", SOURCE_KEY="source")}
    exec(compile(ast.Module(body=declarations, type_ignores=[]), str(SOURCE), "exec"), space)
    return space


class SurfaceContract(unittest.TestCase):
    def setUp(self):
        self.s = load()

    def record(self):
        return {"owner": "exact-owner", "physics": {"backend": self.s["BACKEND"], "surface": {
            "version": 1, "owner": "exact-owner", "roles": {role: [role] for role in self.s["OBJECT_ROLES"]}}}}

    def test_roles_are_exact_unique_and_complete(self):
        value = self.record()
        self.assertIs(self.s["_surface_record"](value), value["physics"]["surface"])
        for change in ("missing", "unknown", "duplicate", "owner", "backend"):
            broken = copy.deepcopy(value)
            surface = broken["physics"]["surface"]
            if change == "missing": del surface["roles"]["TRACKER"]
            if change == "unknown": surface["roles"]["ARTIST"] = ["artist"]
            if change == "duplicate": surface["roles"]["TRACKER"] = surface["roles"]["CLOTH_PROXY"]
            if change == "owner": surface["owner"] = "other-owner"
            if change == "backend": broken["physics"]["backend"] = "UNKNOWN"
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.s["_surface_record"](broken)

    def test_cache_rejection_has_no_native_mutation(self):
        for reason in ("is_baked", "is_baking", "use_external", "use_disk_cache", "baked_range"):
            cache = types.SimpleNamespace(is_baked=False, is_baking=False, use_external=False, use_disk_cache=False)
            physics = {"baked_range": None}
            if reason == "baked_range": physics[reason] = [1, 60]
            else: setattr(cache, reason, True)
            before = copy.deepcopy(vars(cache))
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                self.s["_cache_upgrade_safe"](physics, types.SimpleNamespace(point_cache=cache))
            self.assertEqual(vars(cache), before)

    def test_clean_cache_is_read_only(self):
        cache = types.SimpleNamespace(is_baked=False, is_baking=False, use_external=False, use_disk_cache=False)
        self.s["_cache_upgrade_safe"]({}, types.SimpleNamespace(point_cache=cache))
        self.assertEqual(vars(cache), {"is_baked": False, "is_baking": False, "use_external": False, "use_disk_cache": False})

    def test_early_object_data_and_link_failures_enrol_exact_returned_copy_ids(self):
        # Exercise the real transaction copy method at its early native ID
        # allocation boundaries. Geometric evaluation/binding is not mocked.
        for stage in ("data_copy", "data_assignment", "collection_link"):
            with self.subTest(stage=stage):
                original = types.SimpleNamespace(name="Artist data", library=None, override_library=None)
                copied = types.SimpleNamespace(name="Private data", library=None, override_library=None)

                class NativeObject:
                    def __init__(self): self.name, self._data = "Private object", original
                    @property
                    def data(self): return self._data
                    @data.setter
                    def data(self, value):
                        if stage == "data_assignment": raise RuntimeError(stage)
                        self._data = value

                clone = NativeObject()
                def copy_data():
                    if stage == "data_copy": raise RuntimeError(stage)
                    return copied
                original.copy = copy_data
                source = types.SimpleNamespace(data=original, copy=lambda: clone)
                def link(_obj):
                    if stage == "collection_link": raise RuntimeError(stage)
                transaction = self.s["_Transaction"].__new__(self.s["_Transaction"])
                transaction.objects, transaction.data = [], []
                collection = types.SimpleNamespace(objects=types.SimpleNamespace(link=link))
                with self.assertRaisesRegex(RuntimeError, stage):
                    transaction.copy(source, "Private object", collection)
                self.assertEqual(transaction.objects, [clone])
                self.assertEqual(transaction.data, [] if stage == "data_copy" else [copied])
                self.assertNotIn(original, transaction.data)

    def test_other_scene_cloth_rejects_without_toggling_unknown_flags(self):
        class Cloth:
            type = "CLOTH"
            name = "Artist cache"
            show_viewport = True
            show_render = True
        owned, other = Cloth(), Cloth()
        context = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[
            types.SimpleNamespace(name="Owned old proxy", modifiers=[owned]),
            types.SimpleNamespace(name="Other garment", modifiers=[other])]))
        before = (other.show_viewport, other.show_render)
        with self.assertRaisesRegex(ValueError, "Other garment"):
            self.s["_other_cloth_preflight"](context, (owned,))
        self.assertEqual((other.show_viewport, other.show_render), before)
        other.show_viewport = other.show_render = False
        self.s["_other_cloth_preflight"](context, (owned,))

    def test_owned_reference_pose_mode_is_explicit_and_validated(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        reference = ast.unparse(functions["_reference"])
        self.assertIn("reference.data.pose_position = 'POSE'", reference)
        self.assertIn("reference.data.pose_position == 'POSE'", ast.unparse(functions["validate"]))
        self.assertNotIn("rig.data.pose_position = 'POSE'", reference)

    def test_copied_pose_dependencies_are_cleared_before_edit_update(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        reference = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_reference")
        first_update = next(node.lineno for node in ast.walk(reference) if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute) and node.func.attr == "_activate")
        pose_clear = next(node.lineno for node in ast.walk(reference) if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Name) and node.func.id == "_neutral")
        shape_clear = next(node.lineno for node in ast.walk(reference) if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Attribute) and target.attr == "custom_shape" for target in node.targets))
        self.assertLess(pose_clear, first_update)
        self.assertLess(shape_clear, first_update)

    def test_edit_inputs_and_hair_preview_refuse_before_mutation(self):
        objects = [types.SimpleNamespace(name=name, mode="OBJECT", modifiers=[])
                   for name in ("Source", "Main", "Body", "Body armature", "Active")]
        source, rig, body, body_rig, active = objects
        body.modifiers = [types.SimpleNamespace(type="ARMATURE", object=body_rig)]
        context = types.SimpleNamespace(view_layer=types.SimpleNamespace(objects=types.SimpleNamespace(active=active)))
        package = types.ModuleType("surface_context_test")
        adapter = types.ModuleType("surface_context_test.hair_wiggle_adapter")
        adapter._SESSION = None
        calls = []
        adapter.status = lambda context: calls.append("read status") or {"active": False}
        adapter.stop_preview = lambda *args: self.fail("Installation cannot stop an artist preview")
        self.s["__package__"] = package.__name__
        with mock.patch.dict(sys.modules, {package.__name__: package, adapter.__name__: adapter}):
            self.s["_installation_context_preflight"](context, source, rig, body)
            self.assertEqual(calls, ["read status"])
            for obj in objects:
                obj.mode = "EDIT"
                with self.subTest(name=obj.name), self.assertRaisesRegex(ValueError, "Finish Edit Mode"):
                    self.s["_installation_context_preflight"](context, source, rig, body)
                obj.mode = "OBJECT"
            self.assertEqual(calls, ["read status"])
            adapter._SESSION = {"source": "stale native ID must not be dereferenced"}
            with self.assertRaisesRegex(ValueError, "Hair live preview"):
                self.s["_installation_context_preflight"](context, source, rig, body)
            self.assertEqual(calls, ["read status"])
            self.assertIsNotNone(adapter._SESSION)
            adapter._SESSION = None
            adapter.status = lambda context: {"active": True}
            with self.assertRaisesRegex(ValueError, "Hair live preview"):
                self.s["_installation_context_preflight"](context, source, rig, body)

    def test_rna_enum_and_string_without_is_array_are_not_skipped(self):
        props = [types.SimpleNamespace(identifier=name, type=kind, is_readonly=False)
                 for name, kind in (("mode", "ENUM"), ("name", "STRING"), ("enabled", "BOOLEAN"))]
        owner = types.SimpleNamespace(bl_rna=types.SimpleNamespace(properties=props), mode="LOCAL", name="Native", enabled=True)
        self.assertEqual(self.s["_rna"](owner), {"mode": "LOCAL", "name": "Native", "enabled": True})

    def test_rna_array_and_failed_reads_fail_closed(self):
        prop = types.SimpleNamespace(identifier="vector", type="FLOAT", is_readonly=False, is_array=True)
        owner = types.SimpleNamespace(bl_rna=types.SimpleNamespace(properties=[prop]), vector=(1., 2., 3.))
        self.assertEqual(self.s["_rna"](owner), {"vector": [1., 2., 3.]})
        del owner.vector
        with self.assertRaises(AttributeError): self.s["_rna"](owner)

    def test_digest_is_order_stable_and_rejects_nonfinite(self):
        self.assertEqual(self.s["_digest"]({"a": 1, "b": [2]}), self.s["_digest"]({"b": [2], "a": 1}))
        with self.assertRaises(ValueError): self.s["_digest"]({"coordinate": float("nan")})

    def test_set_mode_allows_coordinator_partial_holder_state(self):
        overlay = types.SimpleNamespace(show_viewport=True, show_render=True)
        self.s["_overlay"] = lambda source, record: overlay
        self.s["validate"] = lambda *args: self.fail("Must not require old holder/endpoint mode equality")
        self.s["set_mode"](object(), {"pin_weights": "transitional"}, "MANUAL")
        self.assertFalse(overlay.show_viewport)
        self.assertFalse(overlay.show_render)
        self.s["set_mode"](object(), {}, "AUTOMATIC")
        self.assertTrue(overlay.show_viewport and overlay.show_render)
        with self.assertRaises(ValueError): self.s["set_mode"](object(), {}, "UNKNOWN")

    def test_capture_mode_requires_complete_proof_before_capturing(self):
        calls = []
        overlay = types.SimpleNamespace(node_group=object(), show_viewport=True, show_render=True)
        self.s["validate"] = lambda *args: calls.append("validate")
        self.s["_overlay"] = lambda *args: calls.append("endpoint") or overlay
        source = {"rig": object()}
        result = self.s["capture_mode"](source, {"owner": "owner"})
        self.assertEqual(calls, ["validate", "endpoint"])
        self.assertEqual(result["flags"], (True, True))
        self.assertIs(result["modifier"], overlay)

    def test_restore_mode_uses_exact_refs_not_mutated_item_record(self):
        group = {"owner": "mine"}
        modifier = types.SimpleNamespace(name="overlay", node_group=group, show_viewport=False, show_render=False)
        source = types.SimpleNamespace(modifiers=types.SimpleNamespace(get=lambda name: modifier))
        state = {"source": source, "modifier": modifier, "group": group, "owner": "mine", "flags": (True, True)}
        self.s["_surface_record"] = lambda *args: self.fail("Rollback cannot depend on coordinator's partially restored record")
        self.s["restore_mode"](source, {"pin_weights": "new-value"}, state)
        self.assertTrue(modifier.show_viewport and modifier.show_render)
        modifier.node_group = {"owner": "other"}
        with self.assertRaises(ValueError): self.s["restore_mode"](source, {}, state)

    def test_export_strip_validates_before_any_deletion(self):
        calls = []
        self.s["validate_snapshot"] = lambda *args: (_ for _ in ()).throw(ValueError("tampered"))
        source = types.SimpleNamespace(modifiers=types.SimpleNamespace(remove=lambda *args: calls.append("delete")))
        with self.assertRaises(ValueError): self.s["strip_export_snapshot"](source, {})
        self.assertEqual(calls, [])

    def test_outside_users_fail_before_cleanup(self):
        class Named:
            name = "QA Helper"
        helper, allowed, artist = Named(), object(), object()
        self.s["bpy"].data = types.SimpleNamespace(user_map=lambda **kwargs: {helper: {allowed, artist}})
        # Real native IDs have names; this fake exercises only the allowed case,
        # then the rejection with a named stable object.
        self.s["bpy"].data.user_map = lambda **kwargs: {helper: {allowed}}
        self.s["_outside_users"]([helper], {allowed})
        named = Named()
        self.s["bpy"].data.user_map = lambda **kwargs: {named: {allowed, artist}}
        with self.assertRaises(ValueError): self.s["_outside_users"]([named], {allowed})

    def test_home_membership_does_not_allow_added_artist_collection(self):
        class Native:
            def __init__(self, name):
                self.name = name
        root, collision, artist = (Native(name) for name in ("Root", "Collision", "Artist"))
        home = Native("Home")
        home.collection = root
        cloth, body = Native("Cloth"), Native("Body")
        cloth.get = lambda key: "CLOTH_PROXY"
        body.get = lambda key: "BODY_ATTACHMENT"
        cloth.users_collection, body.users_collection = (root,), (collision,)
        home.objects = {cloth.name: cloth, body.name: body}
        self.s["bpy"].data = types.SimpleNamespace(scenes={"Home": home}, collections={"Collision": collision})
        self.s["_collection_parents"] = lambda obj: {root}
        record = self.record()
        record["physics"]["collection"] = collision.name
        record["physics"]["surface"]["home_scene"] = home.name
        self.assertEqual(self.s["_home_memberships"](record, [cloth, body]), (home, collision))
        cloth.users_collection = (root, artist)
        with self.assertRaisesRegex(ValueError, "home Scene membership"):
            self.s["_home_memberships"](record, [cloth, body])
        cloth.users_collection = (root,)
        self.s["_collection_parents"] = lambda obj: {root, artist}
        with self.assertRaisesRegex(ValueError, "unique installation Scene"):
            self.s["_home_memberships"](record, [cloth, body])

    def test_foreign_scene_user_is_not_implicitly_allowed(self):
        class Native:
            bl_rna = types.SimpleNamespace(identifier="Scene")
            def __init__(self, name): self.name = name
        helper, home, foreign = Native("Helper"), Native("Home"), Native("Foreign")
        self.s["bpy"].data = types.SimpleNamespace(user_map=lambda **kw: {helper: {home, foreign}})
        with self.assertRaisesRegex(ValueError, "Scene 'Foreign'"):
            self.s["_outside_users"]([helper], {home})

    def test_scene_artist_references_are_not_membership_exemptions(self):
        native_id = self.s["bpy"].types.ID
        class Native(native_id):
            def __init__(self, name):
                self.name, self.animation_data = name, None
                self.bl_rna = types.SimpleNamespace(identifier="Scene", properties=[])
            def as_pointer(self): return id(self)
        helper, scene = Native("Helper"), Native("Home")
        properties = {}
        scene.items = properties.items
        scene.keying_sets, scene.keying_sets_all = [], []
        package = types.ModuleType("surface_boundary_test")
        shared = types.ModuleType("surface_boundary_test.skirt_shared_rig")
        shared._id_ref_paths = lambda value: (((key,), item) for key, item in value.items())
        self.s["__package__"] = package.__name__
        with mock.patch.dict(sys.modules, {package.__name__: package, shared.__name__: shared}):
            self.s["_scene_reference_guard"](scene, [helper])
            properties["artist_target"] = helper
            with self.assertRaisesRegex(ValueError, "Scene artist data"):
                self.s["_scene_reference_guard"](scene, [helper])
            properties.clear()
            scene.keying_sets = [types.SimpleNamespace(paths=[types.SimpleNamespace(id=helper)])]
            with self.assertRaisesRegex(ValueError, "Keying Set"):
                self.s["_scene_reference_guard"](scene, [helper])
            scene.keying_sets = []
            scene.animation_data = types.SimpleNamespace(drivers=[types.SimpleNamespace(driver=types.SimpleNamespace(
                variables=[types.SimpleNamespace(targets=[types.SimpleNamespace(id=helper)])]))])
            with self.assertRaisesRegex(ValueError, "Scene driver"):
                self.s["_scene_reference_guard"](scene, [helper])
            scene.animation_data = None
            scene.camera = helper
            scene.bl_rna.properties = [types.SimpleNamespace(identifier="camera", type="POINTER")]
            with self.assertRaisesRegex(ValueError, "Scene setting"):
                self.s["_scene_reference_guard"](scene, [helper])

    def test_view_layer_membership_does_not_hide_artist_references(self):
        native_id = self.s["bpy"].types.ID
        class Native(native_id):
            def __init__(self, name):
                self.name, self.animation_data = name, None
                self.bl_rna = types.SimpleNamespace(identifier="Scene", properties=[])
            def as_pointer(self): return id(self)
        class Struct:
            def __init__(self, kind, **fields):
                self.custom = {}
                self.bl_rna = types.SimpleNamespace(identifier=kind, properties=[
                    types.SimpleNamespace(identifier=name, type="POINTER") for name in fields])
                self.__dict__.update(fields)
            def as_pointer(self): return id(self)
            def items(self): return self.custom.items()
        endpoint, scene = Native("Collision"), Native("Home")
        scene.items = lambda: ()
        scene.keying_sets, scene.keying_sets_all = [], []
        layer_collection = Struct("LayerCollection", collection=endpoint)
        view_layer = Struct("ViewLayer", objects=[endpoint], layer_collection=layer_collection)
        scene.view_layers = [view_layer]
        scene.bl_rna.properties = [types.SimpleNamespace(identifier="view_layers", type="COLLECTION")]
        package = types.ModuleType("surface_layers_test")
        shared = types.ModuleType("surface_layers_test.skirt_shared_rig")
        shared._id_ref_paths = lambda value: (((key,), item) for key, item in value.items())
        self.s["__package__"] = package.__name__
        with mock.patch.dict(sys.modules, {package.__name__: package, shared.__name__: shared}):
            self.s["_scene_reference_guard"](scene, [endpoint])
            layer_collection.custom["artist_collection"] = endpoint
            with self.assertRaisesRegex(ValueError, "Scene artist data"):
                self.s["_scene_reference_guard"](scene, [endpoint])
            layer_collection.custom.clear()
            view_layer.freestyle_settings = Struct("FreestyleSettings", collection=endpoint)
            view_layer.bl_rna.properties.append(types.SimpleNamespace(identifier="freestyle_settings", type="POINTER"))
            view_layer.freestyle_settings.items = lambda: (_ for _ in ()).throw(
                TypeError("bpy_struct.items(): this type doesn't support IDProperties"))
            with self.assertRaisesRegex(ValueError, "Scene setting"):
                self.s["_scene_reference_guard"](scene, [endpoint])
            view_layer.freestyle_settings.items = lambda: (_ for _ in ()).throw(TypeError("unexpected native read error"))
            with self.assertRaisesRegex(TypeError, "unexpected native read"):
                self.s["_scene_reference_guard"](scene, [endpoint])

    def test_removal_proves_collision_collection_external_users(self):
        class Native:
            def __init__(self, name): self.name, self.users = name, 1
        helper, collection, home, source, rig, group, artist = (Native(name) for name in
            ("Helper", "Collision", "Home", "Source", "Main", "Nodes", "Artist"))
        home.collection = Native("Root")
        record = self.record()
        record["physics"]["collection"] = collection.name
        record["physics"]["surface"]["roles"] = {"CLOTH_PROXY": [helper.name]}
        self.s["validate"] = lambda *args: None
        self.s["_surface_record"] = lambda value: value["physics"]["surface"]
        self.s["_overlay"] = lambda *args: types.SimpleNamespace(node_group=group)
        self.s["_home_memberships"] = lambda *args: (home, collection)
        captured = []
        self.s["_scene_reference_guard"] = lambda home, endpoints: captured.extend(endpoints)
        self.s["bpy"].data = types.SimpleNamespace(objects={helper.name: helper}, collections={collection.name: collection},
            user_map=lambda **kwargs: {helper: {home}, collection: {home, artist}})
        with self.assertRaisesRegex(ValueError, "Collision.*Artist"):
            self.s["preflight_remove"](types.SimpleNamespace(scene=home), source, rig, record)
        self.assertEqual(captured, [helper, collection])

    def test_deletion_boundary_precedes_partial_error_and_not_hide_work(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        install = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install")
        boundary = next(node.lineno for node in ast.walk(install) if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "commit_started" for target in node.targets)
                        and isinstance(node.value, ast.Constant) and node.value.value is True)
        hide_lines = [node.lineno for node in ast.walk(install) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute) and node.func.attr == "hide_set"]
        self.assertTrue(hide_lines and max(hide_lines) < boundary)
        handler = next(node for node in ast.walk(install) if isinstance(node, ast.ExceptHandler))
        self.assertIsInstance(handler.body[0], ast.If)
        self.assertEqual(ast.unparse(handler.body[0].test), "commit_started")
        self.assertIn("partially committed", ast.unparse(handler.body[0]))

    def test_no_runtime_pose_handler_or_action_copy(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("frame_change", text)
        self.assertNotIn(".action.copy(", text)
        self.assertNotIn("bpy.data.actions.new", text)
        self.assertIn('copy_transform.mix_mode = "REPLACE"', text)
        self.assertIn('variable.targets[0].id, variable.targets[0].data_path = identifier, path', text)


if __name__ == "__main__":
    unittest.main()
