"""Pure backend, raw-index pin and batch mode rollback checks; no Blender import.

Runtime coordinator functions come from their actual AST. Fake RNA replaces
native evaluation only; graph, Cloth collision and persistence require Blender.
"""
import ast
import copy
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "addons/character_designer"
spec = importlib.util.spec_from_file_location("surface_test_profiles", ROOT / "skirt_motion_profiles.py")
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


def functions(filename, names, scope):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / filename), "exec"), scope)
    return scope


def physics_scope():
    scope = {"LEGACY_BACKEND": "LEGACY_CAGE", "ACTUAL_SURFACE_BACKEND": "ACTUAL_SURFACE_DELTA_V1"}
    return functions("skirt_physics.py", {"SkirtPhysicsError", "_record_backend", "backend", "_physics_graph",
                                         "add_physics", "bake_steps"}, scope)


def fit(count=15):
    # Deliberately permuted raw IDs: row number is not native vertex index.
    indices = list(reversed(range(count)))
    width = count // 3
    return {"vertices": [[0., 0., 0.] for _ in range(count)], "rings":
            [indices[i * width:(i + 1) * width] for i in range(3)], "ring_t": [0., .375, 1.], "height_world": 1.}


class SurfacePinMap(unittest.TestCase):
    def test_interpolation_scatter_and_periodic_column_wrap_use_saved_ids(self):
        chart = fit()
        grid = [1.] * 4 + [0., .2, .4, .6] + [.2, .4, .6, .8] + [.1, .2, .3, .4] + [.1, .3, .5, .7]
        with patch.object(profiles, "pin_weights", return_value=grid):
            result = profiles.surface_pin_weights(profiles.DEFAULTS, chart, 5, 4)
        for index in chart["rings"][0]:
            self.assertEqual(result[index], 1.)
        self.assertAlmostEqual(result[chart["rings"][1][0]], .1)
        self.assertAlmostEqual(result[chart["rings"][1][1]], .26)
        self.assertAlmostEqual(result[chart["rings"][1][4]], .58)
        self.assertAlmostEqual(result[chart["rings"][2][4]], .58)
        self.assertEqual(len(result), len(chart["vertices"]))

    def test_real_goal_model_keeps_waist_and_does_not_fully_pin_hem(self):
        chart = fit(800)
        # Use the real garment's ten rings with nonuniform native progression.
        order = list(reversed(range(800)))
        chart["rings"] = [order[row * 80:(row + 1) * 80] for row in range(10)]
        chart["ring_t"] = [0., .07, .16, .27, .39, .52, .66, .78, .9, 1.]
        original = copy.deepcopy(chart)
        values = {**profiles.DEFAULTS, "recovery": 1., "waist_depth": .17}
        result = profiles.surface_pin_weights(values, chart, 13, 32)
        self.assertTrue(all(result[index] == 1. for index in chart["rings"][0]))
        self.assertTrue(all(0. < result[index] < .08 for index in chart["rings"][-1]))
        self.assertEqual(chart, original)

    def test_invalid_or_ambiguous_mapping_is_rejected(self):
        cases = []
        for mutate in (lambda x: x["rings"][1].__setitem__(0, x["rings"][0][0]),
                       lambda x: x["rings"][1].__setitem__(0, 15),
                       lambda x: x["rings"][1].__setitem__(0, True),
                       lambda x: x["ring_t"].__setitem__(1, float("nan")),
                       lambda x: x["ring_t"].__setitem__(1, 0.),
                       lambda x: x["ring_t"].__setitem__(2, .9),
                       lambda x: x["rings"].pop()):
            case = fit(); mutate(case); cases.append(case)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(profiles.DressMotionError):
                profiles.surface_pin_weights(profiles.DEFAULTS, case, 5, 4)


class BackendDispatch(unittest.TestCase):
    def setUp(self):
        self.scope = physics_scope()
        self.calls = []
        self.scope["_verify_physics_graph"] = lambda *args: self.calls.append("legacy") or ("oldProxy", "oldCloth")
        self.service = SimpleNamespace(validate=lambda *args: self.calls.append("surface") or ("C", "cloth"),
                                       install=lambda *args, **kwargs: self.calls.append(("install", kwargs)) or "installed")
        self.scope["_surface_module"] = lambda: self.calls.append("import") or self.service

    def test_legacy_without_marker_does_not_load_surface_and_actual_dispatches(self):
        self.assertEqual(self.scope["_physics_graph"]("S", "rig", {"physics": {}}), ("oldProxy", "oldCloth"))
        self.assertEqual(self.calls, ["legacy"])
        self.calls.clear()
        self.assertEqual(self.scope["_physics_graph"]("S", "rig", {
            "physics": {"backend": self.scope["ACTUAL_SURFACE_BACKEND"]}}), ("C", "cloth"))
        self.assertEqual(self.calls, ["import", "surface"])

    def test_unknown_markers_cannot_fall_back_to_legacy(self):
        for value in (None, True, {}, "FUTURE_BACKEND"):
            with self.subTest(value=value), self.assertRaises(self.scope["SkirtPhysicsError"]):
                self.scope["_physics_graph"]("S", "rig", {"physics": {"backend": value}})
        self.assertEqual(self.calls, [])

    def test_explicit_install_delegates_before_any_legacy_creation(self):
        self.scope["skirt_rig"] = SimpleNamespace(_require_controls_for_setup=lambda *_: None)
        self.scope["_record"] = lambda *_: ({}, "rig")
        self.assertEqual(self.scope["add_physics"](None, "S", backend=self.scope["ACTUAL_SURFACE_BACKEND"], body="Body"), "installed")
        self.assertEqual(self.calls, ["import", ("install", {"body": "Body"})])
        self.calls.clear()
        self.scope["add_physics"](None, "S", backend=self.scope["ACTUAL_SURFACE_BACKEND"], body="Body", capability="BOTH")
        self.assertEqual(self.calls, ["import", ("install", {"body": "Body", "capability": "BOTH"})])
        for value in ({}, True, "FUTURE"):
            with self.assertRaises(self.scope["SkirtPhysicsError"]):
                self.scope["add_physics"](None, "S", backend=value)

    def test_actual_bone_copy_is_rejected_before_cache_or_context_mutation(self):
        self.scope["skirt_rig"] = SimpleNamespace(_require_controls_for_setup=lambda *_: None)
        self.scope["_record"] = lambda *_: ({"physics": {"backend": self.scope["ACTUAL_SURFACE_BACKEND"]}}, "rig")
        self.scope["_cloth"] = lambda *_: ("C", "cloth")
        self.scope["clear_cache"] = lambda *_: self.fail("Cache must remain untouched")
        with self.assertRaisesRegex(self.scope["SkirtPhysicsError"], "bone-only"):
            next(self.scope["bake_steps"](None, "S", 1, 10, kind="ANIMATION"))


class Rig(dict):
    library = override_library = None
    is_editable = True
    animation_data = None

    def __init__(self):
        super().__init__(physics_influence=1.)
        self.data = SimpleNamespace(library=None, override_library=None)
        self.tags = 0

    def update_tag(self, **_kwargs):
        self.tags += 1


class Source(dict):
    library = override_library = None
    is_editable = True

    def as_pointer(self):
        return id(self)


class Pin:
    index = 1
    name = "Pin"

    def __init__(self, vertices):
        self.vertices = vertices

    def remove(self, indices):
        for index in indices:
            self.vertices[index].groups = [entry for entry in self.vertices[index].groups if entry.group != self.index]

    def add(self, indices, weight, _mode):
        for index in indices:
            self.vertices[index].groups.append(SimpleNamespace(group=self.index, weight=weight))


class AtomicModes(unittest.TestCase):
    def setUp(self):
        self.physics = SimpleNamespace(**physics_scope())
        self.surface_calls = []
        self.fail_source = None
        self.fail_validation_source = None
        self.sources = [self.make_source(str(i)) for i in range(3)]
        self.surface = SimpleNamespace(capture_mode=lambda source, _record: source["overlay"],
                                       set_mode=self.set_mode, restore_mode=self.restore_mode)
        self.physics._surface_module = lambda: self.surface
        self.physics.validate_physics = self.validate
        self.physics.clear_cache = lambda *_: self.fail("A mode-only transaction must not clear cache")
        self.skirt = SimpleNamespace(RECORD_KEY="record", RIG_KEY="rig", read_record=lambda source: json.loads(source["record"]),
            _require_controls_for_setup=lambda *_: None,
            physics_control=lambda source: (source["rig"], source["rig"], '["physics_influence"]'))
        scope = {"copy": copy, "math": math, "profiles": profiles}
        tree = ast.parse((ROOT / "skirt_motion_tuning.py").read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in {"_SETTINGS", "_COLLISION"} for target in node.targets)]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / "skirt_motion_tuning.py"), "exec"), scope)
        scope["_modules"] = lambda: (self.physics, self.skirt)
        # No Action channels in this fake fixture; avoid importing Blender's
        # unrelated limb Action reader while exercising the mode transaction.
        scope["_actions"] = lambda _rig: ()
        self.runtime = scope
        self.context = SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None))

    def make_source(self, name):
        chart = fit()
        record = {"owner": name, "chain_count": 8, "segment_count": 4, "fit": chart, "physics": {
            "backend": "ACTUAL_SURFACE_DELTA_V1", "rows": 5, "columns": 4,
            "pin_weights": profiles.surface_pin_weights(profiles.DEFAULTS, chart, 5, 4), "baked_range": [1, 60]}}
        vertices = [SimpleNamespace(groups=[SimpleNamespace(group=1, weight=w)]) for w in record["physics"]["pin_weights"]]
        pin = Pin(vertices)
        proxy = SimpleNamespace(data=SimpleNamespace(vertices=vertices), vertex_groups={"Pin": pin})
        native = {"quality": 8, "mass": .15, "tension_stiffness": 25., "compression_stiffness": 25.,
                  "shear_stiffness": 10., "bending_stiffness": .8, "tension_damping": 5.,
                  "compression_damping": 5., "shear_damping": 5., "bending_damping": 1., "air_damping": 3.,
                  "pin_stiffness": 1., "vertex_group_mass": "Pin", "effector_weights": SimpleNamespace(gravity=1.)}
        collision = {"collision_quality": 4, "use_self_collision": False, "self_friction": 5.,
                     "distance_min": .008, "self_distance_min": .008}
        cloth = SimpleNamespace(settings=SimpleNamespace(**native), collision_settings=SimpleNamespace(**collision),
                                point_cache=SimpleNamespace(is_baked=True))
        source = Source(name=name, record=json.dumps(record), overlay=(True, True), rig=Rig(), proxy=proxy, cloth=cloth)
        profiles.write(source, profiles.fresh(record), record)
        return source

    def validate(self, source):
        record = json.loads(source["record"])
        influence = source["rig"]["physics_influence"]
        active = influence == 1.
        if (influence in (0., 1.) and source["overlay"] != (active, active)
                or source is self.fail_validation_source and not active):
            raise self.physics.SkirtPhysicsError("Native mode contract failed")
        return record, source["rig"], source["proxy"], source["cloth"]

    def set_mode(self, source, _record, mode):
        source["overlay"] = (mode == "AUTOMATIC",) * 2
        self.surface_calls.append(("set", source["name"]))
        if source is self.fail_source:
            raise RuntimeError("Native assignment rejected after partial overlay write")

    def restore_mode(self, source, _record, state):
        source["overlay"] = state
        self.surface_calls.append(("restore", source["name"]))

    def state(self):
        return [(source["record"], source[profiles.PROFILE_KEY], source["overlay"],
                 source["rig"]["physics_influence"], source["cloth"].point_cache.is_baked) for source in self.sources]

    def test_mode_only_success_retains_sealed_C_cache_and_switches_both_displays(self):
        result = self.runtime["apply"](self.context, self.sources, mode="MANUAL")
        self.assertTrue(all(profile["mode"] == "MANUAL" for profile in result))
        self.assertTrue(all(source["overlay"] == (False, False) for source in self.sources))
        self.assertTrue(all(source["cloth"].point_cache.is_baked for source in self.sources))
        self.assertTrue(all(json.loads(source["record"])["physics"]["baked_range"] == [1, 60] for source in self.sources))

    def manual_only(self):
        source = self.sources[0]
        record = json.loads(source["record"])
        profiles.write(source, profiles.fresh(record, capability="MANUAL", mode="MANUAL"), record)
        source["rig"]["physics_influence"] = 0.
        source["overlay"] = (False, False)
        return source

    def test_manual_add_physics_promotes_profile_and_native_output_atomically(self):
        source = self.manual_only()
        result = self.runtime["initialize"](source, capability="BOTH", context=self.context)
        self.assertEqual((result["capability"], result["mode"]), ("BOTH", "AUTOMATIC"))
        self.assertEqual(source["overlay"], (True, True))
        self.assertEqual(source["rig"]["physics_influence"], 1.)
        self.assertTrue(source["cloth"].point_cache.is_baked)
        self.assertEqual(json.loads(source["record"])["physics"]["baked_range"], [1, 60])

    def test_initialization_partial_endpoint_failure_restores_manual_setup(self):
        source = self.manual_only()
        before = self.state()
        self.fail_source = source
        with self.assertRaisesRegex(RuntimeError, "partial overlay"):
            self.runtime["initialize"](source, capability="BOTH", context=self.context)
        self.assertEqual(self.state(), before)

    def test_initialization_profile_failure_restores_native_endpoint_and_cache(self):
        source = self.manual_only()
        before = self.state()
        with patch.object(profiles, "write", side_effect=RuntimeError("Profile rejected")):
            with self.assertRaisesRegex(RuntimeError, "Profile rejected"):
                self.runtime["initialize"](source, capability="BOTH", context=self.context)
        self.assertEqual(self.state(), before)

    def test_initialization_post_update_failure_restores_manual_setup(self):
        source = self.manual_only()
        before = self.state()
        old_validate = self.physics.validate_physics

        def fail_automatic(item):
            if item["rig"]["physics_influence"] == 1.:
                raise RuntimeError("Post-update validation rejected")
            return old_validate(item)

        self.physics.validate_physics = fail_automatic
        with self.assertRaisesRegex(RuntimeError, "Post-update"):
            self.runtime["initialize"](source, capability="BOTH", context=self.context)
        self.assertEqual(self.state(), before)

    def test_initialization_preserves_existing_manual_choice(self):
        source = self.manual_only()
        before = self.state()
        result = self.runtime["initialize"](source, context=self.context)
        self.assertEqual(result["mode"], "MANUAL")
        self.assertEqual(self.state(), before)

    def test_partial_second_assignment_rolls_back_all_touched_sources(self):
        before = self.state(); self.fail_source = self.sources[1]
        with self.assertRaisesRegex(RuntimeError, "partial overlay"):
            self.runtime["apply"](self.context, self.sources, mode="MANUAL")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.surface_calls, [("set", "0"), ("set", "1"), ("restore", "1"), ("restore", "0")])
        self.assertGreater(self.sources[0]["rig"].tags, 0)

    def test_post_update_native_validation_failure_also_rolls_back(self):
        before = self.state(); self.fail_validation_source = self.sources[1]
        with self.assertRaisesRegex(self.physics.SkirtPhysicsError, "Native mode"):
            self.runtime["apply"](self.context, self.sources, mode="MANUAL")
        self.assertEqual(self.state(), before)
        self.assertEqual([name for kind, name in self.surface_calls if kind == "restore"], ["2", "1", "0"])

    def test_intermediate_blend_rejects_before_any_mode_write(self):
        self.sources[1]["rig"]["physics_influence"] = .5
        before = self.state()
        with self.assertRaisesRegex(profiles.DressMotionError, "intermediate Physics Blend"):
            self.runtime["apply"](self.context, self.sources, mode="MANUAL")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.surface_calls, [])

    def test_native_pin_tuning_uses_raw_count_and_restores_goals_and_record(self):
        source = self.sources[0]
        record, rig, proxy, cloth = self.validate(source)
        cloth.point_cache.is_baked = False
        before = self.state()
        item = self.runtime["_snapshot"](source, copy.deepcopy(record), rig, proxy, cloth)
        item.update(change_native=True, assignments=[])
        original = list(item["weights"])
        values = {**profiles.DEFAULTS, "recovery": .8}
        self.runtime["_apply_native"](item, values, {"recovery": .8})
        result = self.runtime["_weights"](proxy, item["group"])
        expected = profiles.surface_pin_weights(values, record["fit"], 5, 4)
        self.assertEqual(len(result), 15)  # Raw mesh differs from 5 x 4 cage.
        self.assertEqual(result, expected)
        self.assertNotEqual(result, original)
        self.assertEqual(item["record"]["physics"]["pin_weights"], result)
        self.runtime["_restore"](item)
        self.assertEqual(self.runtime["_weights"](proxy, item["group"]), original)
        self.assertEqual(self.state(), before)


if __name__ == "__main__":
    unittest.main()
