"""Pure Direct selection transactions; production functions are AST extracted.

Native ownership/evaluation and _profile's native parameter read are endpoints.
No Blender import, Cloth surface/clearance claim or Native timing measurement.
"""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "addons/character_designer"


def load(filename, names, scope, constants=()):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                 and target.id in constants for target in node.targets)]
    found = {node.name for node in nodes if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert found == set(names), (filename, found, names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / filename), "exec"), scope)
    return scope


spec = importlib.util.spec_from_file_location("selection_profiles", ROOT / "skirt_motion_profiles.py")
PROFILES = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROFILES)


class NoTouch:
    def __getattr__(self, name):
        raise AssertionError("Unexpected material/vertex/cache/frame access: " + name)


class Cache:
    is_baked = True
    payload = b"retained owned cache"

    def __setattr__(self, name, value):
        raise AssertionError("Mode-only selection edited cache: " + name)


class Bones(dict):
    active = None

    def __iter__(self):
        return iter(self.values())


class Objects(list):
    active = None

    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


class Object(dict):
    def __init__(self, name, kind="ARMATURE"):
        super().__init__()
        self.name, self.type, self.mode = name, kind, "OBJECT"
        self.library, self.override_library, self.is_editable = None, None, True
        self.selected, self.hidden = False, True

    def __bool__(self):
        return True  # A live Blender Object is truthy even with no ID properties.

    def as_pointer(self):
        return id(self)

    def select_get(self):
        return self.selected

    def select_set(self, value):
        self.selected = value

    def hide_get(self):
        return self.hidden

    def hide_set(self, value):
        self.hidden = value

    def update_tag(self, *, refresh):
        pass


class SelectionFixture:
    def __init__(self, backend="DIRECT_MAIN_CLOTH_V1"):
        self.calls, self.snapshots = [], []
        self.fail_selection, self.fail_update = False, False
        self.rig, self.other = Object("Dress Rig"), Object("Artist", "MESH")
        self.source = Object("Dress", "MESH")
        self.objects = Objects([self.rig, self.other])
        self.objects.active = self.other
        self.other.selected, self.other.mode = True, "POSE"
        self.context = NS(scene=NoTouch(), view_layer=NS(objects=self.objects, update=self.update))
        collection = NS(is_visible=False)
        waist = NS(name="Waist", hide=True, collections=[collection])
        hem = NS(name="Hem", hide=False, collections=[])
        self.rig.data = NS(bones=Bones(Waist=waist, Hem=hem), library=None, override_library=None)
        self.rig.data.bones.active = hem
        self.rig.pose = NS(bones=Bones(Waist=NS(name="Waist", select=False, hide=True),
                                      Hem=NS(name="Hem", select=True, hide=False)))
        self.record = {"version": 1, "owner": "owned", "chain_count": 8, "segment_count": 4,
                       "controls": {"waist": "Waist"}, "physics": {"backend": backend,
                       "baked_range": [1, 60], "surface": {"version": 1, "mode_socket": "use_cloth"}}}
        self.source["rig"] = self.rig
        self.source["record"] = json.dumps(self.record, indent=1)
        self.source[PROFILES.PROFILE_KEY] = json.dumps(PROFILES.fresh(self.record), indent=1)
        self.rig["physics_influence"] = 0.0
        self.settings = NS(source=self.other)
        shared = load("skirt_surface.py", {"_json"}, {"json": json})
        self.socket = NS(type="VALUE", value=True, attribute_name="", layer_name="")
        self.output = NS(node_group=object(), show_viewport=True, show_render=True,
                         properties=NS(inputs=NS(use_cloth=self.socket)))
        self.cloth = NS(type="CLOTH", show_viewport=True, show_render=True,
                        settings=NoTouch(), collision_settings=NoTouch(), point_cache=Cache())
        self.proxy = NS(modifiers=[self.cloth], vertex_groups=NoTouch(), data=NoTouch())
        self.direct = load("skirt_surface_direct.py", {
            "SkirtDirectError", "_require", "_installation", "_state", "_write_state", "_use_cloth",
            "_mode_input", "_sync", "capture_mode", "restore_mode", "set_mode", "require_bake_ready"},
            dict(json=json, shared=NS(_json=shared["_json"]), profiles=PROFILES, skirt=NS(RIG_KEY="rig"),
                 validate=self.validate, _overlay=lambda *_args: self.output,
                 _object=lambda *_args: self.proxy), {"STATE_KEY", "VERSION", "BACKEND"})
        self.source[self.direct["STATE_KEY"]] = json.dumps(dict(version=1, owner="owned",
            mode="AUTOMATIC", editing=False, pending=False), indent=1)
        self.service = NS(BACKEND=backend, capture_mode=self.direct["capture_mode"],
                          restore_mode=self.direct["restore_mode"], set_mode=self.direct["set_mode"])
        bpy = NS(ops=NS(object=NS(mode_set=lambda *, mode: setattr(self.objects.active, "mode", mode))))
        self.skirt = load("skirt_rig.py", {"SkirtRigError", "_activate", "_context_state",
            "_restore_context", "select_controls"},
            dict(bpy=bpy, read_record=lambda source: json.loads(source["record"]),
                 _require_controls_for_setup=lambda *_args: None), {"RIG_KEY"})
        self.skirt["RIG_KEY"] = "rig"
        self.skirt.update(RECORD_KEY="record", physics_control=lambda *_args: (self.rig, self.rig, "influence"))
        real_select, real_context_restore = self.skirt["select_controls"], self.skirt["_restore_context"]

        def select(context, source):
            self.calls.append(("select", self.direct["_state"](source, self.record), self.socket.value,
                               self.cloth.show_viewport, self.cloth.show_render))
            result = real_select(context, source)
            if self.fail_selection:
                self.source["record"] = "selection wrote temporary record"
                self.source[PROFILES.PROFILE_KEY] = "selection wrote temporary profile"
                self.rig["physics_influence"] = .75
                raise RuntimeError("selection failed after native changes")
            return result

        def restore_context(*args):
            self.calls.append(("restore-context",))
            return real_context_restore(*args)

        self.skirt.update(select_controls=select, _restore_context=restore_context)
        self.physics = NS(DIRECT_SURFACE_BACKEND=self.direct["BACKEND"],
            SURFACE_BACKENDS=(self.direct["BACKEND"], "ACTUAL_SURFACE_DELTA_V1"),
            backend=lambda record: record.get("physics", {}).get("backend", "LEGACY_CAGE"),
            _surface_module=lambda _record: self.service, validate_physics=self.physics_validate,
            clear_cache=lambda *_args: (_ for _ in ()).throw(AssertionError("Cache freed")))
        self.tuning = load("skirt_motion_tuning.py", {"_local", "_weights", "_snapshot", "_restore",
            "apply", "select_manual_controls"},
            dict(copy=copy, profiles=PROFILES, _modules=lambda: (self.physics, NS(**self.skirt)),
                 _profile=lambda source, record, *_args: PROFILES.read(source, record),
                 _influence_editable=lambda *_args: None), {"_SETTINGS", "_COLLISION"})
        real_snapshot, real_restore, real_apply = (self.tuning[key] for key in ("_snapshot", "_restore", "apply"))

        def snapshot(*args):
            result = real_snapshot(*args)
            self.snapshots.append(result)
            return result

        def restore(item):
            self.calls.append(("restore-mode",))
            return real_restore(item)

        def apply(*args, **kwargs):
            self.calls.append(("apply",))
            return real_apply(*args, **kwargs)

        self.tuning.update(_snapshot=snapshot, _restore=restore, apply=apply)
        self.package = ModuleType("selection_test")
        self.package.__path__ = []
        self.module = ModuleType("selection_test.skirt_motion_tuning")
        self.module.select_manual_controls = self.tuning["select_manual_controls"]
        self.package.skirt_motion_tuning = self.module
        public = load("skirt.py", {"_restore_preview", "CHARACTERDESIGNER_OT_skirt_select_controls"},
            dict(Operator=object, json=json, __package__="selection_test", _rig=lambda: NS(**self.skirt),
                 _physics=lambda: self.physics, _source=lambda _context: self.source,
                 _settings=lambda _context: self.settings,
                 _report=lambda *_args, **_kwargs: self.calls.append(("report",))), {"PREVIEW_KEY"})
        self.operator = public["CHARACTERDESIGNER_OT_skirt_select_controls"]()

    def update(self):
        if self.fail_update:
            self.fail_update = False
            raise RuntimeError("apply evaluation failed")

    def validate(self, source, _rig, record):
        state = self.direct["_state"](source, record)
        enabled = self.direct["_use_cloth"](state)
        if not (self.socket.value == self.cloth.show_viewport == self.cloth.show_render == enabled):
            raise self.direct["SkirtDirectError"]("Native endpoint and saved state differ")

    def physics_validate(self, source):
        record = json.loads(source["record"])
        self.validate(source, self.rig, record)
        return record, self.rig, self.proxy, self.cloth

    def run(self):
        with patch.dict(sys.modules, {self.package.__name__: self.package, self.module.__name__: self.module}):
            return self.operator.execute(self.context)

    def state(self):
        return dict(record=self.source["record"], profile=self.source[PROFILES.PROFILE_KEY],
                    direct=self.source[self.direct["STATE_KEY"]], influence=dict(self.rig),
                    endpoint=(self.socket.value, self.output.show_viewport, self.output.show_render,
                              self.cloth.show_viewport, self.cloth.show_render),
                    objects=[(id(obj), obj.mode, obj.selected, obj.hidden) for obj in self.objects],
                    active=id(self.objects.active), bone_active=id(self.rig.data.bones.active),
                    selected=[bone.select for bone in self.rig.pose.bones],
                    bone_hidden=[bone.hide for bone in self.rig.data.bones],
                    pose_hidden=[bone.hide for bone in self.rig.pose.bones],
                    collections=[group.is_visible for group in self.rig.data.bones["Waist"].collections],
                    settings_source=id(self.settings.source), cache=(id(self.cloth.point_cache),
                    self.cloth.point_cache.is_baked, self.cloth.point_cache.payload))


class DirectSelectionTests(unittest.TestCase):
    def test_public_selector_enters_manual_pending_and_withdraws_output_before_selection(self):
        f = SelectionFixture()
        before, parameters = f.state(), PROFILES.read(f.source, f.record)["settings"]
        self.assertEqual(f.run(), {"FINISHED"})
        selection = next(call for call in f.calls if call[0] == "select")
        self.assertEqual(selection[1], dict(version=1, owner="owned", mode="MANUAL", editing=False, pending=True))
        self.assertEqual(selection[2:], (False, False, False))
        self.assertIs(f.settings.source, f.source)
        self.assertEqual(PROFILES.read(f.source, f.record)["settings"], parameters)
        self.assertEqual(f.source["record"], before["record"])
        self.assertEqual(f.state()["cache"], before["cache"])
        with self.assertRaises(f.direct["SkirtDirectError"]):
            f.direct["require_bake_ready"](f.source, f.record)

    def test_selection_failure_restores_raw_metadata_surface_context_and_all_display_state(self):
        f = SelectionFixture()
        before = f.state()
        f.fail_selection = True
        self.assertEqual(f.run(), {"CANCELLED"})
        self.assertEqual(f.state(), before)
        self.assertEqual(f.calls.count(("restore-mode",)), 1)
        self.assertEqual(f.calls.count(("restore-context",)), 1)

    def test_apply_failure_has_one_owned_rollback_and_never_selects_or_restores_selection(self):
        f = SelectionFixture()
        before = f.state()
        f.fail_update = True  # Real apply fails after changing mode/profile, then rolls back.
        self.assertEqual(f.run(), {"CANCELLED"})
        self.assertEqual(f.state(), before)
        self.assertEqual(f.calls.count(("restore-mode",)), 1)
        self.assertFalse(any(call[0] in {"select", "restore-context"} for call in f.calls))

    def test_legacy_and_delta_public_selection_do_not_switch_mode(self):
        for backend in ("LEGACY_CAGE", "ACTUAL_SURFACE_DELTA_V1"):
            with self.subTest(backend=backend):
                f = SelectionFixture(backend)
                before = f.state()
                self.assertEqual(f.run(), {"FINISHED"})
                after = f.state()
                for key in ("record", "profile", "direct", "influence", "endpoint", "cache"):
                    self.assertEqual(after[key], before[key])
                self.assertFalse(any(call[0] == "apply" for call in f.calls))
                self.assertEqual(f.snapshots, [])

    def test_malformed_direct_preflight_cancels_before_settings_or_selection_changes(self):
        for malformed in ("not-json", json.dumps(dict(version=1, owner="foreign", mode="AUTOMATIC",
                                                       editing=False, pending=False))):
            with self.subTest(malformed=malformed):
                f = SelectionFixture()
                f.source[f.direct["STATE_KEY"]] = malformed
                before = f.state()
                self.assertEqual(f.run(), {"CANCELLED"})
                self.assertEqual(f.state(), before)
                self.assertFalse(any(call[0] in {"apply", "select", "restore-mode"} for call in f.calls))

    def test_real_apply_mode_only_skips_material_vertex_snapshot_but_material_change_keeps_it(self):
        f = SelectionFixture()
        self.assertEqual(f.run(), {"FINISHED"})
        self.assertEqual(len(f.snapshots), 2)  # helper rollback plus real apply rollback
        native_keys = {"group", "weights", "native", "collision", "gravity"}
        for snapshot in f.snapshots:
            self.assertIsNone(snapshot["proxy"])
            self.assertIsNone(snapshot["cloth"])
            self.assertFalse(native_keys.intersection(snapshot))
        f = SelectionFixture()
        settings = {attribute: PROFILES.DEFAULTS[key] for key, attribute in f.tuning["_SETTINGS"].items()}
        settings.update(vertex_group_mass="Pin", compression_stiffness=25., compression_damping=5.,
                        shear_damping=5., effector_weights=NS(gravity=1.))
        collision = {attribute: PROFILES.DEFAULTS[key] for key, attribute in f.tuning["_COLLISION"].items()}
        collision.update(distance_min=.008, self_distance_min=.008)
        f.cloth.settings, f.cloth.collision_settings = NS(**settings), NS(**collision)
        f.proxy.vertex_groups = {"Pin": NS(index=0)}
        f.proxy.data = NS(vertices=[NS(groups=[NS(group=0, weight=.25)])])
        before = f.state()
        with self.assertRaisesRegex(PROFILES.DressMotionError, "Reset the baked Dress simulation"):
            f.tuning["apply"](f.context, (f.source,), {"bend": 2.})
        self.assertEqual(len(f.snapshots), 1)
        snapshot = f.snapshots[0]
        self.assertIs(snapshot["proxy"], f.proxy)
        self.assertIs(snapshot["cloth"], f.cloth)
        self.assertTrue(native_keys.issubset(snapshot))
        self.assertEqual(snapshot["weights"], [.25])
        self.assertEqual(f.state(), before)


if __name__ == "__main__":
    unittest.main()
