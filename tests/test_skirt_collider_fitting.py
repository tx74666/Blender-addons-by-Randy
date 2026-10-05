"""Pure collider fitting boundaries; native closed-volume proof is not mocked."""
import ast
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "addons/character_designer"


def functions(filename, names, scope):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / filename), "exec"), scope)
    return scope


def surface_scope():
    return functions("skirt_surface.py", {"SkirtSurfaceError", "_require", "_json", "_digest",
        "_collider_contract", "_collider_contract_matches", "_collider_inventory_contract", "prepare_collider_fitting"},
        {"copy": copy, "json": json, "hashlib": hashlib, "COLLIDER_CONTRACT_VERSION": 2})


class ColliderContract(unittest.TestCase):
    def setUp(self):
        self.s = surface_scope()
        self.graph = {"type": "MESH", "data": "exact-mesh", "frame": "exact-frame",
                      "modifiers": ["exact-skin", "exact-collision"], "groups": {"Waist": [1.]}, "mesh": "old-coordinates"}
        self.topology = {"count": 4, "edges": [[0, 1]], "faces": [[0, 1, 2]], "loops": [[0, 0]]}
        self.s["_helper_contract"] = lambda obj: copy.deepcopy(self.graph)
        self.s["_topology"] = lambda data: copy.deepcopy(self.topology)
        self.obj = SimpleNamespace(data=object())

    def test_v2_allows_coordinate_fitting_and_keeps_topology(self):
        saved = self.s["_collider_contract"](self.obj)
        self.graph["mesh"] = "legally-fitted-coordinates"
        self.assertTrue(self.s["_collider_contract_matches"](self.obj, saved))
        for field in ("count", "edges", "faces", "loops"):
            old = copy.deepcopy(self.topology)
            self.topology[field] = "changed"
            with self.subTest(field=field):
                self.assertFalse(self.s["_collider_contract_matches"](self.obj, saved))
            self.topology = old

    def test_binding_weights_modifiers_and_data_stay_fixed(self):
        saved = self.s["_collider_contract"](self.obj)
        for field in ("data", "frame", "modifiers", "groups"):
            old = copy.deepcopy(self.graph)
            self.graph[field] = "changed"
            with self.subTest(field=field):
                self.assertFalse(self.s["_collider_contract_matches"](self.obj, saved))
            self.graph = old

    def test_v1_requires_original_coordinate_proof_before_migration(self):
        saved = copy.deepcopy(self.graph)
        self.assertTrue(self.s["_collider_contract_matches"](self.obj, saved))
        self.graph["mesh"] = "already-edited-old-mesh"
        self.assertFalse(self.s["_collider_contract_matches"](self.obj, saved))
        for version in (True, 1, 3, "2"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.s["_collider_contract_matches"](self.obj, {"version": version})

    def test_contract_inventory_is_complete_and_migration_cannot_be_partial(self):
        body = SimpleNamespace(hide_select=True)
        surface = {"colliders": {"Pelvis": {"version": 2}, "Thigh": {"version": 2}}}
        names = ["Pelvis", "Thigh", "Body"]
        self.s["_collider_inventory_contract"](surface, names, body)
        for change in ("missing", "extra", "partial", "unsupported"):
            broken = copy.deepcopy(surface)
            if change == "missing": del broken["colliders"]["Thigh"]
            if change == "extra": broken["colliders"]["Body"] = {"version": 2}
            if change == "partial": del broken["colliders"]["Thigh"]["version"]
            if change == "unsupported": broken["colliders"]["Thigh"]["version"] = 3
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.s["_collider_inventory_contract"](broken, names, body)

    def test_v2_shared_body_is_unselectable_but_unmigrated_v1_remains_readable(self):
        body = SimpleNamespace(hide_select=False)
        legacy = {"colliders": {"Pelvis": {"mesh": "exact-old-hash"}}}
        self.s["_collider_inventory_contract"](legacy, ["Pelvis", "Body"], body)
        with self.assertRaisesRegex(ValueError, "shared Body"):
            self.s["_collider_inventory_contract"]({"colliders": {"Pelvis": {"version": 2}}}, ["Pelvis", "Body"], body)

    def fitting_input(self):
        cache = SimpleNamespace(is_baked=False, is_baking=False, use_external=False, use_disk_cache=True)
        body = SimpleNamespace(name="read-only-body")
        collider = SimpleNamespace(name="fitting-collider", data=object())
        self.s["validate"] = lambda *args: ("actual", SimpleNamespace(point_cache=cache))
        self.s["_object"] = lambda *args: body
        self.s["bpy"] = SimpleNamespace(data=SimpleNamespace(objects={collider.name: collider}))
        record = {"physics": {"colliders": [collider.name, body.name], "baked_range": None,
                              "surface": {"colliders": {collider.name: copy.deepcopy(self.graph)}}}}
        return cache, body, collider, record

    def test_preparation_is_readonly_and_ordinary_disk_cache_is_allowed(self):
        cache, body, collider, record = self.fitting_input()
        before = copy.deepcopy(record)
        updated, candidates, protected = self.s["prepare_collider_fitting"]("source", "rig", record)
        self.assertEqual(record, before)
        self.assertIsNot(updated, record)
        self.assertEqual(candidates, [collider])
        self.assertIs(protected, body)
        self.assertEqual(set(updated["physics"]["surface"]["colliders"]), {collider.name})
        self.assertTrue(cache.use_disk_cache)

    def test_sealed_external_or_active_bake_refuses_without_metadata_write(self):
        for field in ("is_baked", "is_baking", "use_external", "baked_range"):
            cache, body, collider, record = self.fitting_input()
            if field == "baked_range": record["physics"][field] = [1, 60]
            else: setattr(cache, field, True)
            before = copy.deepcopy(record)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Reset Dress"):
                self.s["prepare_collider_fitting"]("source", "rig", record)
            self.assertEqual(record, before)


class NativeObject(dict):
    def __init__(self, name, role=None):
        super().__init__()
        self.name, self.mode, self.hidden, self.hide_select, self.selected = name, "OBJECT", True, True, False
        if role: self["character_designer_skirt_surface_role"] = role

    def __bool__(self): return True  # Native bpy IDs are not empty mapping sentinels.
    def hide_get(self, **kwargs): return self.hidden
    def hide_set(self, value, **kwargs): self.hidden = value
    def select_set(self, value): self.selected = value


class Context:
    def __init__(self, objects, active):
        self.all_objects = objects
        self.view_layer = SimpleNamespace(objects=SimpleNamespace(active=active))
        # Native view-layer membership uses names, and active remains mutable.
        class LayerObjects(dict): pass
        self.view_layer.objects = LayerObjects((obj.name, obj) for obj in objects)
        self.view_layer.objects.active = active
        self.mode = "OBJECT"

    @property
    def selected_objects(self): return [obj for obj in self.all_objects if obj.selected]


class ColliderUI(unittest.TestCase):
    def setUp(self):
        self.pelvis, self.thigh = NativeObject("Pelvis"), NativeObject("Thigh")
        self.body = NativeObject("Read-only collision", "BODY_ATTACHMENT")
        self.artist = NativeObject("Artist")
        self.source = NativeObject("Dress")
        self.source["record"] = "old-exact-metadata"
        self.objects = [self.pelvis, self.thigh, self.body, self.artist, self.source]
        self.record = {"physics": {"backend": "ACTUAL_SURFACE_DELTA_V1",
            "colliders": [obj.name for obj in (self.pelvis, self.thigh, self.body)],
            "surface": {"roles": {"BODY_ATTACHMENT": [self.body.name]}, "body": self.artist.name}}}
        self.settings = SimpleNamespace(source=self.artist)
        self.context = Context(self.objects, self.artist)
        self.artist.selected, self.artist.hidden, self.artist.hide_select = True, False, False
        cache = SimpleNamespace(is_baked=False, is_baking=False, use_external=False)
        self.rig = SimpleNamespace(RECORD_KEY="record", write_record=lambda source, record: source.__setitem__("record", "new-metadata"))
        package = ModuleType("collider_ui_test")
        self.surface = ModuleType("collider_ui_test.skirt_surface")
        self.surface.prepare_collider_fitting = lambda *args: (copy.deepcopy(self.record), [self.pelvis, self.thigh], self.body)
        self.surface.validate = lambda *args: None
        self.modules = {package.__name__: package, self.surface.__name__: self.surface}
        self.scope = {"__package__": package.__name__, "bpy": SimpleNamespace(data=SimpleNamespace(
            objects={obj.name: obj for obj in self.objects}), ops=SimpleNamespace(object=SimpleNamespace(mode_set=self.mode_set))),
            "_source": lambda context: self.source, "_settings": lambda context: self.settings,
            "_rig": lambda: self.rig, "_physics": lambda: SimpleNamespace(validate_physics=lambda source:
                (self.record, self.rig, "proxy", SimpleNamespace(point_cache=cache))), "_report": lambda *args, **kwargs: None}
        functions("skirt.py", {"_colliders", "_helper_objects"}, self.scope)
        tree = ast.parse((ROOT / "skirt.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CHARACTERDESIGNER_OT_skirt_select_colliders")
        execute = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "execute")
        exec(compile(ast.Module(body=[execute], type_ignores=[]), "collider operator", "exec"), self.scope)

    def mode_set(self, mode):
        self.context.mode = mode
        if self.context.view_layer.objects.active is not None:
            self.context.view_layer.objects.active.mode = mode

    def snapshot(self):
        return (self.source["record"], self.settings.source.name, self.context.view_layer.objects.active.name,
                self.context.mode, tuple((obj.name, obj.hidden, obj.hide_select, obj.selected) for obj in self.objects))

    def test_display_and_selection_exclude_shared_body_even_with_saved_role_missing(self):
        self.assertEqual([obj.name for obj in self.scope["_helper_objects"](self.record, "COLLIDERS")], ["Pelvis", "Thigh"])
        del self.record["physics"]["surface"]["roles"]["BODY_ATTACHMENT"]
        self.assertEqual([obj.name for obj in self.scope["_colliders"](self.record)], ["Pelvis", "Thigh"])
        with patch.dict(sys.modules, self.modules):
            self.assertEqual(self.scope["execute"](None, self.context), {"FINISHED"})
        self.assertEqual([obj.name for obj in self.context.selected_objects], ["Pelvis", "Thigh"])
        self.assertTrue(self.body.hide_select and self.body.hidden)
        self.assertEqual(self.source["record"], "new-metadata")

    def test_late_migration_failure_restores_metadata_selection_visibility_and_mode(self):
        self.context.mode = self.artist.mode = "POSE"
        self.body.hide_select = False
        before = self.snapshot()
        def fail(*args): raise RuntimeError("Injected post-record proof failure")
        self.surface.validate = fail
        with patch.dict(sys.modules, self.modules):
            self.assertEqual(self.scope["execute"](None, self.context), {"CANCELLED"})
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
