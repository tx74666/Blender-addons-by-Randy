"""Pure tuning transactions against the real Direct preview-state functions.

Native RNA/cache execution and graph ownership are fake endpoints. These tests
prove failure ordering and readiness, not native Cloth replay or clearance.
"""
import copy
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "direct_selection_fixture", Path(__file__).with_name("test_skirt_direct_control_selection.py"))
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)
PROFILES = selection.PROFILES


class NativeRNA:
    def __init__(self, fixture, label, values):
        object.__setattr__(self, "fixture", fixture)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "values", dict(values))
        object.__setattr__(self, "bl_rna", NS(properties={name: NS(
            is_readonly=False, hard_min=-1000., hard_max=1000.) for name in values}))

    def __getattr__(self, name):
        return self.values[name]

    def __setattr__(self, name, value):
        self.values[name] = value
        self.fixture.mutation(self.label + ":" + name)


class Pin:
    name, index = "Pin", 0

    def __init__(self, fixture):
        self.fixture = fixture

    def remove(self, indices):
        for index in indices:
            vertex = self.fixture.proxy.data.vertices[index]
            vertex.groups = [entry for entry in vertex.groups if entry.group != self.index]
        self.fixture.mutation("pin:remove")

    def add(self, indices, weight, _mode):
        for index in indices:
            self.fixture.proxy.data.vertices[index].groups.append(NS(group=self.index, weight=weight))
        self.fixture.mutation("pin:add")


class Fixture(selection.SelectionFixture):
    def __init__(self, owner="owned", backend="DIRECT_MAIN_CLOTH_V1"):
        super().__init__()
        self.events, self.fail_mutation, self.fail_clear = [], None, False
        self.fail_final_validation = False
        self.invalidate_calls = 0
        self.record.update(owner=owner, source=self.source.name, fit={
            "vertices": [[0., 0., 0.] for _ in range(15)], "rings": [
                list(range(14, 9, -1)), list(range(9, 4, -1)), list(range(4, -1, -1))],
            "ring_t": [0., .375, 1.], "height_world": 1.})
        self.record["physics"].update(backend=backend, rows=5, columns=4, baked_range=None)
        self.source["record"] = json.dumps(self.record, indent=1)
        self.source[PROFILES.PROFILE_KEY] = json.dumps(PROFILES.fresh(self.record), indent=1)
        state = json.loads(self.source[self.direct["STATE_KEY"]])
        state["owner"] = owner
        self.source[self.direct["STATE_KEY"]] = json.dumps(state, indent=1)
        self.source.data = NS(artist_vertices=((1., 2., 3.),),
            shape_keys={"Blink_L": (0.1, 0.2, 0.3)}, skin_weights={"Thigh.L": (1.,)},
            uv=((.1, .2),), action="artist action")
        self.cloth.point_cache = NS(is_baked=False, is_outdated=False,
            payload=b"old owned RAM payload", frame_start=1, frame_end=60, frame_step=1)
        values = {attribute: PROFILES.DEFAULTS[key] for key, attribute in self.tuning["_SETTINGS"].items()}
        values.update(compression_stiffness=25., compression_damping=5., shear_damping=5.,
                      vertex_group_mass="Pin")
        self.cloth.settings = NativeRNA(self, "native", values)
        self.cloth.settings.values["effector_weights"] = NativeRNA(self, "effectors", {"gravity": 1.})
        collision = {attribute: PROFILES.DEFAULTS[key] for key, attribute in self.tuning["_COLLISION"].items()}
        collision.update(distance_min=.008, self_distance_min=.008)
        self.cloth.collision_settings = NativeRNA(self, "collision", collision)
        self.proxy.data = NS(vertices=[NS(groups=[NS(group=0, weight=.25)]) for _ in range(15)])
        self.proxy.vertex_groups = {"Pin": Pin(self)}
        self.physics.clear_cache = self.clear_cache
        self.skirt.update(physics_control=lambda source: (source["rig"], source["rig"], "influence"),
                          write_record=self.write_record)
        self.tuning["math"] = math
        selection.load("skirt_motion_tuning.py", {"_height", "_set_weights", "_assignments", "_apply_native"}, self.tuning)
        selection.load("skirt_surface_direct.py", {"invalidate_result"}, self.direct)
        self.service.invalidate_result = self.invalidate

    def mutation(self, label):
        self.events.append(label)
        self.cloth.point_cache.is_outdated = True
        if self.fail_mutation == label:
            self.fail_mutation = None
            raise RuntimeError("partial " + label)

    def write_record(self, source, record):
        source["record"] = json.dumps(record)

    def clear_cache(self, _context, source):
        self.events.append("clear")
        self.cloth.point_cache.is_outdated = True
        record = json.loads(source["record"])
        record["physics"]["baked_range"] = None
        self.write_record(source, record)
        if self.fail_clear:
            self.fail_clear = False
            raise RuntimeError("partial cache invalidation")

    def invalidate(self, source, record):
        self.invalidate_calls += 1
        self.events.append("invalidate")
        return self.direct["invalidate_result"](source, record)

    def physics_validate(self, source):
        record = json.loads(source["record"])
        if record["physics"]["backend"] == self.direct["BACKEND"]:
            self.validate(source, self.rig, record)
            if self.fail_final_validation and self.direct["_state"](source, record)["pending"]:
                self.fail_final_validation = False
                raise RuntimeError("final endpoint validation")
        return record, source["rig"], self.proxy, self.cloth

    def apply(self, changes=None, mode=None):
        return self.tuning["apply"](self.context, (self.source,), changes, mode=mode)

    def author(self):
        state = self.state()
        return {key: value for key, value in state.items() if key not in {
            "record", "profile", "direct", "endpoint", "cache"}} | {
                "artist_mesh_keys_weights_uv_action": copy.deepcopy(vars(self.source.data))}

    def parameters(self):
        return {"native": {key: value for key, value in self.cloth.settings.values.items()
                           if key != "effector_weights"},
                "collision": dict(self.cloth.collision_settings.values),
                "gravity": self.cloth.settings.effector_weights.gravity,
                "pins": self.tuning["_weights"](self.proxy, self.proxy.vertex_groups["Pin"])}

    def pending(self, test, mode="AUTOMATIC", editing=False):
        state = self.direct["_state"](self.source, json.loads(self.source["record"]))
        test.assertEqual((state["mode"], state["editing"], state["pending"]), (mode, editing, True))
        test.assertEqual((self.socket.value, self.cloth.show_viewport, self.cloth.show_render), (False,) * 3)
        test.assertEqual(self.rig["physics_influence"], 0.)


class DirectTuningReadinessTests(unittest.TestCase):
    def baseline(self, fixture):
        return fixture.author(), fixture.parameters(), fixture.source["record"], fixture.source[PROFILES.PROFILE_KEY]

    def restored_pending(self, fixture, before):
        fixture.pending(self)
        self.assertEqual(fixture.author(), before[0])
        self.assertEqual(fixture.parameters(), before[1])
        self.assertEqual((fixture.source["record"], fixture.source[PROFILES.PROFILE_KEY]), before[2:])
        self.assertEqual(fixture.cloth.point_cache.payload, b"old owned RAM payload")
        self.assertFalse(fixture.cloth.point_cache.is_baked)

    def test_scalar_success_withdraws_old_result_and_requires_reset(self):
        f = Fixture(); author = f.author()
        result = f.apply({"mass": .24})
        self.assertEqual(f.cloth.settings.mass, .24)
        self.assertEqual(result[0]["mode"], "AUTOMATIC")
        f.pending(self)
        self.assertEqual(f.author(), author)
        self.assertEqual(f.events[-2:], ["clear", "invalidate"])
        with self.assertRaisesRegex(ValueError, "Reset the simulation explicitly"):
            f.apply(mode="AUTOMATIC")

    def test_pin_only_success_marks_native_touch_and_preserves_artist_skin(self):
        f = Fixture(); author = f.author()
        f.apply({"recovery": .8})
        f.pending(self)
        self.assertTrue(any(event.startswith("pin:") for event in f.events))
        self.assertEqual(f.author(), author)
        self.assertEqual(f.tuning["_weights"](f.proxy, f.proxy.vertex_groups["Pin"]),
            PROFILES.surface_pin_weights({**PROFILES.DEFAULTS, "recovery": .8}, f.record["fit"], 5, 4))

    def test_same_value_rna_write_also_withdraws_cache(self):
        f = Fixture(); f.apply({"mass": PROFILES.DEFAULTS["mass"]})
        f.pending(self)
        self.assertEqual(f.invalidate_calls, 1)

    def test_combined_parameter_and_mode_commits_requested_mode_but_not_readiness(self):
        for mode in ("AUTOMATIC", "MANUAL"):
            with self.subTest(mode=mode):
                f = Fixture(); f.apply({"mass": .24}, mode=mode)
                f.pending(self, mode=mode)
                self.assertEqual(PROFILES.read(f.source, json.loads(f.source["record"]))["mode"], mode)

    def test_existing_editing_flag_is_preserved_by_parameter_invalidation(self):
        f = Fixture(); state = json.loads(f.source[f.direct["STATE_KEY"]])
        state.update(editing=True, pending=True)
        f.source[f.direct["STATE_KEY"]] = json.dumps(state)
        f.direct["_sync"](f.source, f.record, state)
        f.apply({"bend": 2.})
        f.pending(self, editing=True)

    def test_partial_scalar_and_pin_writes_roll_back_but_remain_pending(self):
        for label, changes in (("native:mass", {"mass": .24}),
                               ("pin:remove", {"recovery": .8}),
                               ("pin:add", {"recovery": .8})):
            with self.subTest(label=label):
                f = Fixture(); before = self.baseline(f); f.fail_mutation = label
                with self.assertRaisesRegex(RuntimeError, "partial"):
                    f.apply(changes)
                self.restored_pending(f, before)

    def test_failure_before_first_native_write_does_not_invalidate_through_restore(self):
        f = Fixture(); before = f.state(); parameters = f.parameters()
        with patch.dict(f.tuning, _apply_native=lambda *_args: (_ for _ in ()).throw(RuntimeError("before native"))):
            with self.assertRaisesRegex(RuntimeError, "before native"):
                f.apply({"mass": .24})
        self.assertEqual(f.state(), before)
        self.assertEqual(f.parameters(), parameters)
        self.assertEqual((f.events, f.invalidate_calls), ([], 0))

    def test_pin_weight_calculation_failure_is_untouched(self):
        f = Fixture(); before = f.state()
        with patch.object(PROFILES, "surface_pin_weights", side_effect=RuntimeError("before pins")):
            with self.assertRaisesRegex(RuntimeError, "before pins"):
                f.apply({"recovery": .8})
        self.assertEqual(f.state(), before)
        self.assertEqual((f.events, f.invalidate_calls), ([], 0))

    def test_baked_and_native_range_preflight_failures_are_exact(self):
        for case in ("baked", "readonly"):
            with self.subTest(case=case):
                f = Fixture()
                if case == "baked":
                    f.cloth.point_cache.is_baked = True
                else:
                    f.cloth.settings.bl_rna.properties["mass"].is_readonly = True
                before = f.state(); parameters = f.parameters()
                with self.assertRaises(ValueError):
                    f.apply({"mass": .24})
                self.assertEqual(f.state(), before)
                self.assertEqual(f.parameters(), parameters)
                self.assertEqual((f.events, f.invalidate_calls), ([], 0))

    def test_late_record_profile_cache_update_and_validation_failures_withdraw_after_restore(self):
        for phase in ("record", "profile", "cache", "update", "validation"):
            with self.subTest(phase=phase):
                f = Fixture(); before = self.baseline(f)
                if phase == "record":
                    real = f.skirt["write_record"]
                    def fail_record(*args):
                        real(*args)
                        raise RuntimeError("late record")
                    scope = patch.dict(f.skirt, write_record=fail_record)
                elif phase == "profile":
                    real = PROFILES.write
                    def fail_profile(*args):
                        real(*args)
                        raise RuntimeError("late profile")
                    scope = patch.object(PROFILES, "write", side_effect=fail_profile)
                else:
                    f.fail_clear = phase == "cache"
                    f.fail_update = phase == "update"
                    f.fail_final_validation = phase == "validation"
                    scope = patch.dict(f.tuning, {})
                with scope, self.assertRaises(RuntimeError):
                    f.apply({"mass": .24, "recovery": .8}, mode="MANUAL")
                self.restored_pending(f, before)

    def test_partial_invalidation_failure_restores_fields_then_retries_withdrawal(self):
        f = Fixture(); before = self.baseline(f); real = f.service.invalidate_result
        def fail_once(*args):
            result = real(*args)
            if f.invalidate_calls == 1:
                raise RuntimeError("partial output withdrawal")
            return result
        f.service.invalidate_result = fail_once
        with self.assertRaisesRegex(RuntimeError, "partial output"):
            f.apply({"mass": .24})
        self.restored_pending(f, before)
        self.assertEqual(f.invalidate_calls, 2)

    def test_restore_failure_still_withdraws_result_and_retains_original_cause(self):
        f = Fixture(); f.fail_clear = True; real = f.tuning["_restore"]
        def fail_restore(item):
            real(item)
            raise RuntimeError("late restore failure")
        with patch.dict(f.tuning, _restore=fail_restore):
            with self.assertRaisesRegex(ValueError, "rollback also needs recovery") as caught:
                f.apply({"mass": .24})
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertRegex(str(caught.exception.__cause__), "partial cache")
        f.pending(self)
        self.assertEqual(f.invalidate_calls, 1)

    def test_native_restore_failure_does_not_skip_independent_output_withdrawal(self):
        f = Fixture(); f.fail_clear = True
        real = f.clear_cache
        def fail_then_restore(*args):
            f.fail_mutation = "native:mass"
            return real(*args)
        f.physics.clear_cache = fail_then_restore
        with self.assertRaisesRegex(ValueError, "rollback also needs recovery"):
            f.apply({"mass": .24}, mode="MANUAL")
        f.pending(self, mode="MANUAL")
        self.assertEqual(f.invalidate_calls, 1)

    def test_batch_partial_later_item_only_invalidates_native_touched_sources(self):
        fixtures = [Fixture(str(index)) for index in range(3)]
        first = fixtures[0]; lookup = {id(f.source): f for f in fixtures}
        owners = {f.record["owner"]: f for f in fixtures}
        first.physics.validate_physics = lambda source: lookup[id(source)].physics_validate(source)
        first.physics._surface_module = lambda record: owners[record["owner"]].service
        first.physics.clear_cache = lambda context, source: lookup[id(source)].clear_cache(context, source)
        fixtures[1].fail_mutation = "native:mass"
        before = [self.baseline(f) for f in fixtures]; last_state = fixtures[2].state()
        with self.assertRaisesRegex(RuntimeError, "partial native"):
            first.tuning["apply"](first.context, tuple(f.source for f in fixtures), {"mass": .24})
        for f, saved in zip(fixtures[:2], before[:2]):
            self.restored_pending(f, saved)
        self.assertEqual(fixtures[2].state(), last_state)
        self.assertEqual(fixtures[2].invalidate_calls, 0)

    def test_mode_only_and_legacy_keep_existing_cache_behavior(self):
        f = Fixture(); f.apply(mode="AUTOMATIC")
        self.assertEqual((f.events, f.invalidate_calls), ([], 0))
        self.assertFalse(f.direct["_state"](f.source, f.record)["pending"])
        f.apply(mode="MANUAL")
        self.assertEqual((f.events, f.invalidate_calls), ([], 0))
        legacy = Fixture(backend="LEGACY_CAGE")
        legacy.apply({"mass": .24})
        self.assertEqual(legacy.invalidate_calls, 0)
        self.assertIn("clear", legacy.events)
        self.assertEqual(legacy.cloth.settings.mass, .24)

    def test_batch_cache_failure_withdraws_earlier_restored_ready_output_again(self):
        fixtures = [Fixture(str(index)) for index in range(3)]
        first = fixtures[0]; lookup = {id(f.source): f for f in fixtures}
        owners = {f.record["owner"]: f for f in fixtures}
        first.physics.validate_physics = lambda source: lookup[id(source)].physics_validate(source)
        first.physics._surface_module = lambda record: owners[record["owner"]].service
        first.physics.clear_cache = lambda context, source: lookup[id(source)].clear_cache(context, source)
        fixtures[1].fail_clear = True
        before = [self.baseline(f) for f in fixtures]
        with self.assertRaisesRegex(RuntimeError, "partial cache"):
            first.tuning["apply"](first.context, tuple(f.source for f in fixtures), {"mass": .24})
        for f, saved in zip(fixtures, before):
            self.restored_pending(f, saved)
        self.assertEqual([f.invalidate_calls for f in fixtures], [2, 1, 1])


if __name__ == "__main__":
    unittest.main()
