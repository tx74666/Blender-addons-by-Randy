"""Actual Clear Cache service/operator with explicitly mocked native endpoints.

No Blender import. A recording RNA setter proves invocation/commit order, not
native RAM-cache disposal; that behavior has a separate pending Blender test.
"""
import ast
from contextlib import nullcontext
import copy
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[1] / "addons/character_designer"


def load(filename, names, scope):
    path = ROOT / filename
    nodes = [node for node in ast.parse(path.read_text(encoding="utf-8")).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert {node.name for node in nodes} == set(names), (filename, names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)
    return scope


class NativeCacheMock:
    """Record a native invalidation request; do not simulate Cloth geometry."""
    def __init__(self, calls, *, baked=False, setter_fails=False):
        self.calls = calls
        self.is_baked = baked
        self.is_outdated = False
        self.setter_fails = setter_fails
        self._step = 2

    @property
    def frame_step(self):
        return self._step

    @frame_step.setter
    def frame_step(self, value):
        self.calls.append("invalidate")
        if self.setter_fails:
            raise RuntimeError("Native step setter rejected")
        self._step = value
        self.is_outdated = True


class ClearCacheTests(unittest.TestCase):
    def prepare(self, *, baked=False, free_result=None, releases=True, setter_fails=False):
        self.calls, self.reports = [], []
        self.source, self.proxy = object(), object()
        self.record = {"physics": {"baked_range": [1, 6]}, "artist": "protected"}
        self.before = copy.deepcopy(self.record)
        self.cache = NativeCacheMock(self.calls, baked=baked, setter_fails=setter_fails)
        self.cloth = NS(point_cache=self.cache)

        def validate(source):
            self.assertIs(source, self.source)
            return self.record, object(), self.proxy, self.cloth

        def override(**values):
            self.assertEqual(values, dict(object=self.proxy, active_object=self.proxy,
                                          point_cache=self.cache))
            return nullcontext()

        def free():
            self.calls.append("free")
            if releases:
                self.cache.is_baked = False
            return {"FINISHED"} if free_result is None else free_result

        def write(source, record):
            self.assertIs(source, self.source)
            self.assertIs(record, self.record)
            self.assertFalse(self.cache.is_baked)
            self.assertTrue(self.cache.is_outdated)
            self.calls.append("write")

        self.context = NS(temp_override=override)
        self.physics = load("skirt_physics.py", {"SkirtPhysicsError", "_require", "clear_cache"},
                            dict(validate_physics=validate, skirt_rig=NS(write_record=write),
                                 bpy=NS(ops=NS(ptcache=NS(free_bake=free)))))
        ui = load("skirt.py", {"CHARACTERDESIGNER_OT_skirt_clear_cache"},
                  dict(Operator=object, _idle=lambda _context: True,
                       _has_physics=lambda _context: True, _source=lambda _context: self.source,
                       _physics=lambda: NS(clear_cache=self.physics["clear_cache"]),
                       _report=lambda _op, _context, message, error=False:
                           self.reports.append((message, error))))
        self.operator = ui["CHARACTERDESIGNER_OT_skirt_clear_cache"]()

    def clear(self):
        self.physics["clear_cache"](self.context, self.source)

    def test_unbaked_native_step_setter_runs_before_metadata_commit(self):
        self.prepare()
        self.clear()
        self.assertEqual(self.calls, ["invalidate", "write"])
        self.assertEqual(self.cache.frame_step, 2)
        self.assertIsNone(self.record["physics"]["baked_range"])
        self.assertEqual(self.record["artist"], self.before["artist"])

    def test_sealed_release_is_verified_then_invalidated_before_commit(self):
        self.prepare(baked=True)
        self.clear()
        self.assertEqual(self.calls, ["free", "invalidate", "write"])
        self.assertFalse(self.cache.is_baked)
        self.assertEqual(self.cache.frame_step, 2)

    def test_cancelled_free_or_still_sealed_keeps_metadata_and_does_not_invalidate(self):
        for result, releases in (({"CANCELLED"}, False), ({"CANCELLED"}, True),
                                 ({"FINISHED"}, False)):
            with self.subTest(result=result, releases=releases):
                self.prepare(baked=True, free_result=result, releases=releases)
                with self.assertRaises(self.physics["SkirtPhysicsError"]):
                    self.clear()
                self.assertEqual(self.calls, ["free"])
                self.assertEqual(self.record, self.before)

    def test_step_setter_failure_keeps_metadata_even_after_native_release(self):
        for baked in (False, True):
            with self.subTest(baked=baked):
                self.prepare(baked=baked, setter_fails=True)
                with self.assertRaisesRegex(RuntimeError, "Native step setter rejected"):
                    self.clear()
                self.assertEqual(self.calls, (["free"] if baked else []) + ["invalidate"])
                self.assertEqual(self.record, self.before)
                # The mock records a completed release, not a payload rollback.
                self.assertFalse(self.cache.is_baked)

    def test_public_operator_reports_success_only_after_native_invalidation_and_commit(self):
        self.prepare()
        self.assertTrue(self.operator.poll(self.context))
        self.assertEqual(self.operator.execute(self.context), {"FINISHED"})
        self.assertEqual(self.calls, ["invalidate", "write"])
        self.assertEqual(len(self.reports), 1)
        self.assertFalse(self.reports[0][1])

    def test_public_operator_reports_cancelled_for_free_and_setter_failures(self):
        cases = (dict(baked=True, free_result={"CANCELLED"}, releases=False),
                 dict(baked=True, free_result={"FINISHED"}, releases=False),
                 dict(setter_fails=True))
        for values in cases:
            with self.subTest(values=values):
                self.prepare(**values)
                self.assertEqual(self.operator.execute(self.context), {"CANCELLED"})
                self.assertEqual(self.record, self.before)
                self.assertNotIn("write", self.calls)
                self.assertEqual(len(self.reports), 1)
                self.assertTrue(self.reports[0][1])
                self.assertNotIn("cache cleared", self.reports[0][0])

    def test_graph_rejection_precedes_native_operations_and_public_success_report(self):
        self.prepare()

        def reject(_source):
            raise ValueError("Owned graph rejected")

        self.physics["validate_physics"] = reject
        self.assertEqual(self.operator.execute(self.context), {"CANCELLED"})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.record, self.before)
        self.assertEqual(self.reports, [("Owned graph rejected", True)])


if __name__ == "__main__":
    unittest.main()
