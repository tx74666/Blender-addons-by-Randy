"""Actual Bake generator/Direct state with explicitly fake native endpoints.

No Blender import or Cloth simulation. These tests prove dispatch, failure-state
and commit ordering; native cache/geometry behavior needs separate Blender QA.
"""
import ast
from contextlib import nullcontext
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[1] / "addons/character_designer"


def load(filename, names, scope):
    path = ROOT / filename
    nodes = [node for node in ast.parse(path.read_text(encoding="utf-8")).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError((filename, names))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)
    return scope


class FakeSource(dict):
    name = "Artist Dress"


class FakeCache:
    """Record native requests/failures; do not claim RAM payload rollback."""
    def __init__(self, calls):
        self.calls = calls
        self.frame_start, self._end, self._step = 1, 6, 2
        self.is_baked = self.is_outdated = False
        self.fail_step = self.fail_end = False

    @property
    def frame_step(self):
        return self._step

    @frame_step.setter
    def frame_step(self, value):
        self.calls.append(("step", value))
        self.is_outdated = True
        if self.fail_step:
            self.fail_step = False
            raise RuntimeError("Fake cache step rejected after mutation began")
        self._step = value

    @property
    def frame_end(self):
        return self._end

    @frame_end.setter
    def frame_end(self, value):
        self.calls.append(("end", value))
        if self.fail_end:
            self.fail_end = False
            raise RuntimeError("Fake cache range rejected")
        self._end = value


class DirectBakeFailureStateTests(unittest.TestCase):
    def prepare(self, *, backend="DIRECT_MAIN_CLOTH_V1", mode="AUTOMATIC",
                editing=False, pending=False, reject_graph=False,
                evaluation_error=None, seal_result=None, seals=True,
                commit_fails=False):
        self.calls, self.module_calls, self.invalidations = [], [], []
        self.state_key = "character_designer_dress_direct_state_v1"
        self.source = FakeSource({self.state_key: json.dumps({
            "version": 1, "owner": "owned-source", "mode": mode,
            "editing": editing, "pending": pending}),
            "artist_raw": ("mesh", "UV", "weights"),
            "artist_rest": ("parent", "rest-matrix"), "artist_actions": ("Body", "Manual")})
        self.record = {"owner": "owned-source", "artist": {"protected": True},
                       "physics": {"backend": backend, "baked_range": [1, 6],
                                   "surface": {"version": 1, "mode_socket": "UseCloth"}}}
        self.before_source, self.before_record = copy.deepcopy(self.source), copy.deepcopy(self.record)
        self.cache = FakeCache(self.calls)
        self.cloth = NS(type="CLOTH", point_cache=self.cache,
                        show_viewport=True, show_render=True)
        self.socket = NS(type="VALUE", attribute_name="", layer_name="", value=True)
        self.overlay = NS(properties=NS(inputs=NS(UseCloth=self.socket)),
                          show_viewport=True, show_render=True)
        self.scene = NS(frame_current=7, frame_subframe=.25)

        def frame_set(frame, *, subframe=0.):
            self.calls.append(("frame", frame, subframe))
            self.scene.frame_current, self.scene.frame_subframe = frame, subframe

        self.scene.frame_set = frame_set
        self.graph = object()

        def to_mesh():
            self.calls.append(("mesh", self.scene.frame_current))
            if evaluation_error == "raise" and self.scene.frame_current == 2:
                raise RuntimeError("Fake native evaluation failed")
            value = math.nan if evaluation_error == "nonfinite" and self.scene.frame_current == 2 else 0.
            return NS(vertices=[NS(co=(value, 0., 0.))])

        self.proxy = NS(name="Owned Cloth", modifiers=[self.cloth],
                        to_mesh=to_mesh,
                        to_mesh_clear=lambda: self.calls.append(("mesh_clear", self.scene.frame_current)))
        self.proxy.evaluated_get = lambda graph: self.proxy if graph is self.graph else self.fail("Wrong fake graph")
        self.rig = NS(evaluated_get=lambda graph: NS())

        def override(**values):
            self.assertEqual(values, dict(object=self.proxy, active_object=self.proxy, point_cache=self.cache))
            return nullcontext()

        self.context = NS(scene=self.scene, object=None, selected_objects=[],
                          view_layer=NS(objects=NS(active=None), update=lambda: None),
                          evaluated_depsgraph_get=lambda: self.graph, temp_override=override)

        def object_endpoint(source, record, role):
            self.assertIs(source, self.source)
            self.assertIs(record, self.record)
            self.assertEqual(role, "CLOTH_PROXY")
            return self.proxy

        self.direct = load("skirt_surface_direct.py", {
            "SkirtDirectError", "_require", "_installation", "_state", "_write_state",
            "_use_cloth", "_mode_input", "_sync", "invalidate_result", "require_bake_ready"},
            dict(BACKEND="DIRECT_MAIN_CLOTH_V1", VERSION=1, STATE_KEY=self.state_key,
                 json=json, profiles=NS(MODES={"AUTOMATIC", "MANUAL"}),
                 shared=NS(_json=json.dumps), _object=object_endpoint,
                 _overlay=lambda source, record: self.overlay))
        if backend == "DIRECT_MAIN_CLOTH_V1":
            self.direct["_sync"](self.source, self.record, self.state())

        def invalidate(source, record):
            self.invalidations.append((source, record))
            self.direct["invalidate_result"](source, record)

        def surface_module(record):
            self.module_calls.append(record["physics"]["backend"])
            self.assertEqual(record["physics"]["backend"], "DIRECT_MAIN_CLOTH_V1")
            return NS(require_bake_ready=self.direct["require_bake_ready"], invalidate_result=invalidate)

        def cloth_endpoint(record):
            if reject_graph:
                raise ValueError("Fake graph validation rejected")
            return self.proxy, self.cloth

        def write(source, record):
            self.calls.append(("write", copy.deepcopy(record["physics"]["baked_range"])))
            if commit_fails and record["physics"]["baked_range"] == [1, 3]:
                raise RuntimeError("Fake metadata commit rejected")

        def seal():
            self.calls.append(("seal",))
            self.cache.is_baked = seals
            return {"FINISHED"} if seal_result is None else seal_result

        def free():
            self.calls.append(("free",))
            self.cache.is_baked = False
            return {"FINISHED"}

        self.physics = load("skirt_physics.py", {
            "SkirtPhysicsError", "_require", "_record_backend", "backend",
            "_set_object_mode", "clear_cache", "bake_steps"},
            dict(math=math, LEGACY_BACKEND="LEGACY_CAGE",
                 ACTUAL_SURFACE_BACKEND="ACTUAL_SURFACE_DELTA_V1",
                 DIRECT_SURFACE_BACKEND="DIRECT_MAIN_CLOTH_V1",
                 SURFACE_BACKENDS={"ACTUAL_SURFACE_DELTA_V1", "DIRECT_MAIN_CLOTH_V1"},
                 skirt_rig=NS(_require_controls_for_setup=lambda source: None, write_record=write),
                 _record=lambda source: (self.record, self.rig), _cloth=cloth_endpoint,
                 validate_physics=lambda source: (self.record, self.rig, self.proxy, self.cloth),
                 _surface_module=surface_module,
                 bpy=NS(ops=NS(ptcache=NS(bake_from_cache=seal, free_bake=free)))))

    def state(self):
        return json.loads(self.source[self.state_key])

    def iterator(self, kind="SIMULATION", start=1, end=3):
        return self.physics["bake_steps"](self.context, self.source, start, end, kind)

    def consume(self, iterator):
        progress = []
        while True:
            try:
                progress.append(next(iterator))
            except StopIteration as done:
                return progress, done.value

    def assert_artist_preserved(self):
        self.assertEqual({k: v for k, v in self.source.items() if k != self.state_key},
                         {k: v for k, v in self.before_source.items() if k != self.state_key})
        current, original = copy.deepcopy(self.record), copy.deepcopy(self.before_record)
        current["physics"].pop("baked_range")
        original["physics"].pop("baked_range")
        self.assertEqual(current, original)

    def assert_failed_direct(self):
        expected = json.loads(self.before_source[self.state_key])
        expected["pending"] = True
        self.assertEqual(self.state(), expected)
        self.assertEqual(len(self.invalidations), 1)
        self.assertFalse(self.socket.value)
        self.assertFalse(self.cloth.show_viewport)
        self.assertFalse(self.cloth.show_render)
        self.assertTrue(self.overlay.show_viewport and self.overlay.show_render)
        self.assertIsNone(self.record["physics"]["baked_range"])
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (7, .25))
        self.assert_artist_preserved()

    def test_invalidate_result_only_sets_pending_and_switches_output(self):
        for mode, editing in (("AUTOMATIC", False), ("MANUAL", False), ("AUTOMATIC", True)):
            with self.subTest(mode=mode, editing=editing):
                self.prepare(mode=mode, editing=editing)
                before = (self.cache.frame_start, self.cache.frame_end, self.cache.frame_step,
                          self.cache.is_baked, self.cache.is_outdated)
                self.direct["invalidate_result"](self.source, self.record)
                expected = json.loads(self.before_source[self.state_key])
                expected["pending"] = True
                self.assertEqual(self.state(), expected)
                self.assertFalse(self.socket.value or self.cloth.show_viewport or self.cloth.show_render)
                self.assertEqual(self.calls, [])
                self.assertEqual((self.cache.frame_start, self.cache.frame_end, self.cache.frame_step,
                                  self.cache.is_baked, self.cache.is_outdated), before)
                self.assertEqual(self.record, self.before_record)
                self.assert_artist_preserved()

    def test_close_after_yield_invalidates_partial_result(self):
        self.prepare()
        iterator = self.iterator()
        self.assertEqual(next(iterator), (1, 3, "Frame 1 / 3"))
        iterator.close()
        self.assert_failed_direct()
        self.assertNotIn(("seal",), self.calls)

    def test_invalidation_sync_error_is_reported_after_frame_and_step_cleanup(self):
        self.prepare()
        iterator = self.iterator()
        next(iterator)

        def reject_sync(source, record, state):
            raise RuntimeError("Fake output sync rejected")

        self.direct["_sync"] = reject_sync
        with self.assertRaisesRegex(self.physics["SkirtPhysicsError"], "output also needs recovery"):
            iterator.close()
        self.assertTrue(self.state()["pending"])
        self.assertEqual(len(self.invalidations), 1)
        self.assertIsNone(self.record["physics"]["baked_range"])
        self.assertEqual(self.cache.frame_step, 2)
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (7, .25))
        self.assertNotIn(("seal",), self.calls)
        self.assert_artist_preserved()

    def test_native_evaluation_failure_and_nonfinite_geometry_invalidate(self):
        for failure, exception in (("raise", RuntimeError), ("nonfinite", ValueError)):
            with self.subTest(failure=failure):
                self.prepare(evaluation_error=failure)
                with self.assertRaises(exception):
                    self.consume(self.iterator())
                self.assert_failed_direct()
                self.assertNotIn(("seal",), self.calls)
                if failure == "nonfinite":
                    self.assertIn(("mesh_clear", 2), self.calls)

    def test_clear_or_cache_range_failure_after_mutation_invalidates(self):
        for setting in ("fail_step", "fail_end"):
            with self.subTest(setting=setting):
                self.prepare()
                setattr(self.cache, setting, True)
                with self.assertRaises(RuntimeError):
                    self.consume(self.iterator())
                self.assert_failed_direct()
                self.assertTrue(self.cache.is_outdated)
                self.assertNotIn(("seal",), self.calls)

    def test_success_requires_all_frames_seal_and_metadata_commit(self):
        self.prepare()
        progress, result = self.consume(self.iterator())
        self.assertEqual(progress, [(i, 3, f"Frame {i} / 3") for i in range(1, 4)])
        self.assertEqual(result, {"start": 1, "end": 3, "frames": 3, "kind": "SIMULATION",
                                  "backend": "DIRECT_MAIN_CLOTH_V1", "surface": "Owned Cloth"})
        self.assertTrue(self.cache.is_baked)
        self.assertEqual(self.record["physics"]["baked_range"], [1, 3])
        self.assertEqual(self.source, self.before_source)
        self.assertEqual(self.invalidations, [])
        self.assertEqual(self.module_calls, ["DIRECT_MAIN_CLOTH_V1"])
        self.assertTrue(self.socket.value and self.cloth.show_viewport and self.cloth.show_render)
        self.assertLess(self.calls.index(("seal",)), self.calls.index(("write", [1, 3])))
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (7, .25))
        self.assert_artist_preserved()

    def test_seal_operator_and_native_baked_flag_both_remain_required(self):
        for result, seals in (({"CANCELLED"}, False), ({"CANCELLED"}, True), ({"FINISHED"}, False)):
            with self.subTest(result=result, seals=seals):
                self.prepare(seal_result=result, seals=seals)
                with self.assertRaises(ValueError):
                    self.consume(self.iterator())
                self.assert_failed_direct()
                self.assertNotIn(("write", [1, 3]), self.calls)

    def test_metadata_commit_failure_hides_even_already_sealed_result(self):
        self.prepare(commit_fails=True)
        with self.assertRaisesRegex(RuntimeError, "metadata commit rejected"):
            self.consume(self.iterator())
        self.assertTrue(self.cache.is_baked)
        self.assert_failed_direct()

    def test_preflight_graph_admission_and_direct_readiness_refuse_without_mutation(self):
        cases = (({"reject_graph": True}, "SIMULATION", 1, 3),
                 ({}, "ANIMATION", 1, 3), ({}, "SIMULATION", 3, 1),
                 ({"pending": True}, "SIMULATION", 1, 3),
                 ({"mode": "MANUAL"}, "SIMULATION", 1, 3),
                 ({"editing": True}, "SIMULATION", 1, 3))
        for options, kind, start, end in cases:
            with self.subTest(options=options, kind=kind, start=start):
                self.prepare(**options)
                with self.assertRaises(ValueError):
                    self.consume(self.iterator(kind, start, end))
                self.assertEqual(self.source, self.before_source)
                self.assertEqual(self.record, self.before_record)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.invalidations, [])

    def test_object_mode_failure_before_cache_touch_preserves_metadata_and_step(self):
        self.prepare()
        attempts = []

        def mode(context):
            attempts.append(context)
            if len(attempts) == 1:
                raise RuntimeError("Fake Object Mode refused")

        self.physics["_set_object_mode"] = mode
        with self.assertRaisesRegex(RuntimeError, "Object Mode refused"):
            self.consume(self.iterator())
        self.assertEqual(self.source, self.before_source)
        self.assertEqual(self.record, self.before_record)
        self.assertEqual(self.cache.frame_step, 2)
        self.assertFalse(self.cache.is_baked or self.cache.is_outdated)
        self.assertEqual(self.invalidations, [])
        self.assertEqual(self.calls, [("frame", 7, .25)])

    def test_delta_and_legacy_failure_never_dispatch_direct_service(self):
        for backend in ("ACTUAL_SURFACE_DELTA_V1", "LEGACY_CAGE"):
            with self.subTest(backend=backend):
                self.prepare(backend=backend, evaluation_error="nonfinite")
                # These backends have no Direct provider contract.
                with self.assertRaises(ValueError):
                    self.consume(self.iterator())
                self.assertEqual(self.source, self.before_source)
                self.assertEqual(self.module_calls, [])
                self.assertEqual(self.invalidations, [])
                self.assertTrue(self.socket.value and self.cloth.show_viewport and self.cloth.show_render)
                self.assertIsNone(self.record["physics"]["baked_range"])
                self.assert_artist_preserved()


if __name__ == "__main__":
    unittest.main()
