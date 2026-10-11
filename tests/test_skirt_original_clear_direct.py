"""Pure Clear transaction regression; execute extracted production functions.

Native ownership/graph preflight and Blender evaluation are fake endpoints.
This checks state, write order and rollback, not Cloth surface/collision results.
"""
import ast
import copy
import json
import math
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


class NoNative:
    def __getattr__(self, name):
        raise AssertionError("Unexpected native/cache/frame operation: " + name)


class Cache:
    is_baked = True

    def __setattr__(self, name, value):
        raise AssertionError("Clear must not edit cache RNA: " + name)


class Bone:
    def __init__(self, name, selected, harness):
        self.name, self.select, self.harness = name, selected, harness
        self.rotation_mode = "XYZ"
        self.location = (2., 3., 4.)
        self.rotation_euler = (.2, .3, .4)
        self.rotation_quaternion = (1., 0., 0., 0.)
        self.rotation_axis_angle = (0., 0., 1., 0.)
        self.scale = (1.2, 1.1, 1.)
        self.lock_rotation = (True, False, True)
        self.lock_rotation_w, self.lock_rotations_4d = True, False
        self.endpoint = None

    def __setattr__(self, name, value):
        endpoint = self.__dict__.get("endpoint")
        if endpoint is not None and name in self.harness.original["_CHANNELS"] + ("rotation_mode",):
            self.harness.events.append(("channel", self.name, endpoint.socket.value,
                                        endpoint.cloth.show_viewport, endpoint.cloth.show_render))
        object.__setattr__(self, name, value)


class Target:
    def __init__(self, bones, harness):
        self.pose, self.harness = NS(bones={bone.name: bone for bone in bones}), harness

    def update_tag(self, *, refresh):
        self.harness.events.append(("tag", self, refresh))


class ClearFixture:
    """Only the endpoints used by Clear and its two existing state providers."""
    def __init__(self):
        self.events, self.targets, self.endpoints = [], [], {}
        self.fail_update, self.fail_end = False, None
        self.main = object()
        self.context = NS(scene=NoNative(), view_layer=NS(update=self.update))
        self.package = ModuleType("clear_direct_test")
        self.package.__path__ = []
        self.body = ModuleType("clear_direct_test.body_original_mode")
        self.body.active = lambda _main: False
        self.package.body_original_mode = self.body
        shared = load("skirt_surface.py", {"_json"}, {"json": json})
        self.direct = load("skirt_surface_direct.py", {
            "SkirtDirectError", "_require", "_installation", "_state", "_write_state",
            "_use_cloth", "_mode_input", "_sync", "capture_mode", "restore_mode",
            "set_editing", "set_mode", "require_bake_ready"},
            dict(json=json, shared=NS(_json=shared["_json"]), bpy=NoNative(),
                 profiles=NS(MODES=("AUTOMATIC", "MANUAL")), skirt=NS(RIG_KEY="rig"),
                 validate=lambda *_args: None,
                 _overlay=lambda source, _record: self.endpoints[id(source)].output,
                 _object=lambda source, _record, _role: self.endpoints[id(source)].proxy),
            {"STATE_KEY", "VERSION", "BACKEND"})
        self.original = load("skirt_original_mode.py", {
            "_channels", "_restore", "_locks", "_restore_locks", "_update", "_corrections",
            "checkpoint", "rollback", "clear"},
            dict(json=json, math=math, __package__="clear_direct_test", bpy=NoNative(),
                 prepare=lambda *_args: [item[0] for item in self.targets],
                 _resolve=lambda *_args: self.targets,
                 _surface=lambda source, *_args, **_kwargs: (self.endpoints[id(source)].service, None)),
            {"CORRECTIONS", "_CHANNELS", "_LOCKS"})

    def update(self):
        self.events.append(("update",))
        if self.fail_update:
            self.fail_update = False  # The rollback's own update can complete.
            raise RuntimeError("input evaluation failed")

    def add(self, name, *, selected=(True,), backend="DIRECT_MAIN_CLOTH_V1", include=True,
            mode="AUTOMATIC", corrections=True):
        bones = [Bone(name + str(i), value, self) for i, value in enumerate(selected)]
        target = Target(bones, self)
        source = {"rig": target}
        record = {"owner": name, "chains": [{"def": [bone.name for bone in bones]}],
                  "physics": {"backend": backend, "surface": {"version": 1, "mode_socket": "use_cloth"}}}
        state = dict(version=1, owner=name, mode=mode, editing=False, pending=mode == "MANUAL")
        source[self.direct["STATE_KEY"]] = json.dumps(state, indent=1)
        socket = NS(type="VALUE", value=self.direct["_use_cloth"](state), attribute_name="", layer_name="")
        output = NS(node_group=object(), show_viewport=True, show_render=True,
                    properties=NS(inputs=NS(use_cloth=socket)))
        cloth = NS(type="CLOTH", show_viewport=socket.value, show_render=socket.value, point_cache=Cache())
        endpoint = NS(source=source, record=record, target=target, bones=bones,
                      socket=socket, output=output, cloth=cloth, proxy=NS(modifiers=[cloth]))
        for bone in bones:
            bone.endpoint = endpoint
        if backend == self.direct["BACKEND"]:
            def editing(source, record, value):
                self.events.append(("editing", name, value))
                self.direct["set_editing"](source, record, value)
                if not value and self.fail_end == name:
                    self.fail_end = None
                    raise RuntimeError("end editing failed")
            endpoint.service = NS(BACKEND=backend, capture_mode=self.direct["capture_mode"],
                                  restore_mode=self.direct["restore_mode"], set_editing=editing)
        elif backend == "ACTUAL_SURFACE_DELTA_V1":
            endpoint.service = NS(BACKEND=backend, capture_mode=lambda *_args: "unchanged-delta",
                                  restore_mode=lambda *_args: None,
                                  set_editing=lambda *_args: (_ for _ in ()).throw(AssertionError("Delta editing changed")))
        else:
            endpoint.service = None
        self.endpoints[id(source)] = endpoint
        relations = [(bone, NS(mute=False, mix_mode="BEFORE_FULL"), NS(mute=True)) for bone in bones]
        endpoint.relations = relations
        saved = {}
        if corrections:
            for bone in bones:
                channels = copy.deepcopy(self.original["_channels"](bone))
                channels["mode"] = "QUATERNION"
                channels["channels"]["location"] = [0., 0., 0.]
                channels["channels"]["scale"] = [1., 1., 1.]
                saved[bone.name] = {"mix_mode": "REPLACE", "channels": channels}
        source[self.original["CORRECTIONS"]] = json.dumps(dict(version=1, owner=name, bones=saved), indent=1)
        if include:
            self.targets.append(({"names": [bone.name for bone in bones]}, target, source, record, relations))
        return endpoint

    def snapshot(self, endpoint):
        return dict(raw_state=endpoint.source[self.direct["STATE_KEY"]],
                    correction=endpoint.source.get(self.original["CORRECTIONS"]),
                    channels=[self.original["_channels"](bone) for bone in endpoint.bones],
                    locks=[self.original["_locks"](bone) for bone in endpoint.bones],
                    constraints=[(copy.mute, copy.mix_mode, rotation.mute) for _, copy, rotation in endpoint.relations],
                    output=(endpoint.socket.value, endpoint.output.show_viewport, endpoint.output.show_render,
                            endpoint.cloth.show_viewport, endpoint.cloth.show_render),
                    baked=endpoint.cloth.point_cache.is_baked)

    def clear(self, *, selected_only=True):
        with patch.dict(sys.modules, {self.package.__name__: self.package, self.body.__name__: self.body}):
            return self.original["clear"](self.context, self.main, selected_only)


class DirectClearTests(unittest.TestCase):
    def test_direct_withdraws_output_before_first_channel_write_and_keeps_reset_pending(self):
        for mode in ("AUTOMATIC", "MANUAL"):
            with self.subTest(mode=mode):
                f = ClearFixture()
                e = f.add("owned", mode=mode)
                self.assertEqual(f.clear(), 1)
                writes = [event for event in f.events if event[0] == "channel"]
                self.assertTrue(writes)
                self.assertTrue(all(event[2:] == (False, False, False) for event in writes))
                self.assertEqual(f.events[0], ("editing", "owned", True))
                self.assertEqual(f.events[-1], ("editing", "owned", False))
                state = f.direct["_state"](e.source, e.record)
                self.assertEqual(state, dict(version=1, owner="owned", mode=mode, editing=False, pending=True))
                self.assertFalse(f.direct["_use_cloth"](state))
                self.assertNotIn(f.original["CORRECTIONS"], e.source)
                self.assertEqual(e.bones[0].location, [0., 0., 0.])
                self.assertEqual(e.relations[0][1].mix_mode, "REPLACE")
                self.assertTrue(e.cloth.point_cache.is_baked)  # Clear has no free/seek/RNA reset.
                for call in (lambda: f.direct["set_mode"](e.source, e.record, "AUTOMATIC"),
                             lambda: f.direct["require_bake_ready"](e.source, e.record)):
                    with self.assertRaises(f.direct["SkirtDirectError"]):
                        call()

    def test_selected_only_preserves_unselected_bone_other_dress_and_foreign_direct_state(self):
        f = ClearFixture()
        changed = f.add("changed", selected=(True, False))
        unselected = f.add("unselected", selected=(False,))
        foreign = f.add("foreign", include=False)
        before_other, before_foreign = f.snapshot(unselected), f.snapshot(foreign)
        bone_before = f.original["_channels"](changed.bones[1])
        correction_before = json.loads(changed.source[f.original["CORRECTIONS"]])["bones"][changed.bones[1].name]
        self.assertEqual(f.clear(), 1)
        self.assertEqual(f.snapshot(unselected), before_other)
        self.assertEqual(f.snapshot(foreign), before_foreign)
        self.assertEqual(f.original["_channels"](changed.bones[1]), bone_before)
        self.assertEqual(json.loads(changed.source[f.original["CORRECTIONS"]])["bones"],
                         {changed.bones[1].name: correction_before})
        self.assertEqual([event[1] for event in f.events if event[0] == "tag"], [changed.target])

    def test_zero_selection_or_zero_corrections_is_exact_noop_without_evaluation(self):
        for selected, corrections in ((False, True), (True, False)):
            with self.subTest(selected=selected, corrections=corrections):
                f = ClearFixture()
                e = f.add("noop", selected=(selected,), corrections=corrections)
                before = f.snapshot(e)
                self.assertEqual(f.clear(), 0)
                self.assertEqual(f.snapshot(e), before)
                self.assertEqual(f.events, [])

    def test_delta_and_legacy_keep_surface_state_and_do_not_enter_direct_editing(self):
        for backend in ("ACTUAL_SURFACE_DELTA_V1", "LEGACY_CAGE"):
            with self.subTest(backend=backend):
                f = ClearFixture()
                e = f.add("existing", backend=backend)
                before = f.snapshot(e)
                self.assertEqual(f.clear(selected_only=False), 1)
                after = f.snapshot(e)
                for key in ("raw_state", "output", "baked", "locks"):
                    self.assertEqual(after[key], before[key])
                self.assertFalse(any(event[0] == "editing" for event in f.events))
                self.assertEqual(e.bones[0].location, [0., 0., 0.])

    def test_input_update_exception_restores_complete_captured_transaction(self):
        f = ClearFixture()
        endpoints = [f.add("one"), f.add("two")]
        before = [f.snapshot(e) for e in endpoints]
        f.fail_update = True
        with self.assertRaisesRegex(RuntimeError, "input evaluation failed"):
            f.clear()
        self.assertEqual([f.snapshot(e) for e in endpoints], before)
        self.assertEqual(sum(event[0] == "update" for event in f.events), 2)

    def test_end_editing_exception_restores_already_committed_and_current_surface_states(self):
        f = ClearFixture()
        endpoints = [f.add("one"), f.add("two")]
        before = [f.snapshot(e) for e in endpoints]
        f.fail_end = "two"  # Throw after real set_editing(False) has changed its state.
        with self.assertRaisesRegex(RuntimeError, "end editing failed"):
            f.clear()
        self.assertEqual([f.snapshot(e) for e in endpoints], before)
        self.assertIn(("editing", "one", False), f.events)
        self.assertIn(("editing", "two", False), f.events)


if __name__ == "__main__":
    unittest.main()
