"""Lightweight modal bake lifecycle checks, without importing or running Blender.

Load the actual coordinator and its small helpers from the runtime AST. Scene
evaluation is replaced with a sequential generator; Event only has type/value.
Native Event RNA and real cloth evaluation remain in the Blender UI suite.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "addons/character_designer/skirt.py"


class Event:
    __slots__ = ("type", "value")

    def __init__(self, event_type, value="NOTHING"):
        self.type, self.value = event_type, value


class WindowManager:
    def __init__(self):
        self.character_designer_skirt = SimpleNamespace(source=None, last_message="")
        self.timers, self.removed, self.handlers, self.progress = [], [], [], []
        self.end_calls = 0
        self.invalid = False
        self.fail_handler = False

    def as_pointer(self):
        if self.invalid:
            raise ReferenceError("Window removed")
        return id(self)

    def event_timer_add(self, seconds, **_kwargs):
        assert seconds == 0.01
        timer = object()
        self.timers.append(timer)
        return timer

    def event_timer_remove(self, timer):
        self.removed.append(timer)
        if self.invalid:
            raise ReferenceError("Window removed")

    def modal_handler_add(self, operator):
        if self.fail_handler:
            raise RuntimeError("Cannot install handler")
        self.handlers.append(operator)

    def progress_begin(self, *_args):
        pass

    def progress_update(self, value):
        if self.invalid:
            raise ReferenceError("Window removed during progress update")
        self.progress.append(value)

    def progress_end(self):
        self.end_calls += 1
        if self.invalid:
            raise ReferenceError("Window removed")


class SkirtBakeModalTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.frames, self.closed, self.remembered = [], [], []
        self.source = object()
        self.error = False
        self.step_work = 0.0
        self.wm = WindowManager()
        self.context = SimpleNamespace(window_manager=self.wm, window=object(), area=None)
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        names = {"_settings", "_report", "_idle", "_SkirtBakeOperator", "stop_skirt_runtime"}
        nodes = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
        assert len(nodes) == len(names)
        self.runtime = {
            "_ACTIVE_BAKES": {},
            "bpy": SimpleNamespace(app=SimpleNamespace(background=False)),
            "time": SimpleNamespace(monotonic=lambda: self.now),
            "_source": lambda _context: self.source,
            "_restore_preview": lambda *_args: None,
            "_bake_range": lambda _context: (1, 3),
            "_physics": lambda: SimpleNamespace(bake_steps=self.steps),
            "_remember_bake": lambda source, result: self.remembered.append((source, result)),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), self.runtime)

    def steps(self, *_args, **_kwargs):
        try:
            for frame in range(1, 4):
                if self.error and frame == 2:
                    raise self.error
                self.frames.append(frame)
                self.now += self.step_work
                yield frame, 3, f"Frame {frame} / 3"
            return {"mesh": "Independent baked mesh"}
        finally:
            self.closed.append(True)

    def begin(self, kind="SIMULATION"):
        operator = self.runtime["_SkirtBakeOperator"]()
        operator.kind = kind
        operator.reports = []
        operator.report = lambda severity, message: operator.reports.append((severity, message))
        self.assertEqual(operator.invoke(self.context, Event("LEFTMOUSE", "PRESS")), {"RUNNING_MODAL"})
        self.assertEqual(self.frames, [1])
        return operator

    def tick(self, operator, moment):
        self.now = moment
        return operator.modal(self.context, Event("TIMER"))

    def assert_finished(self, operator):
        self.assertIsNone(operator._timer)
        self.assertIsNone(operator._next_step_time)
        self.assertIsNone(operator._steps)
        self.assertIsNone(operator._wm)
        self.assertIsNone(operator._bake_source)
        self.assertFalse(self.runtime["_ACTIVE_BAKES"])
        self.assertEqual(self.wm.removed, self.wm.timers)
        self.assertEqual(self.wm.end_calls, 1)
        self.assertEqual(self.closed, [True])

    def test_timer_without_identity_is_throttled_and_never_catches_up(self):
        operator = self.begin()
        self.now = 0.002
        self.assertEqual(operator.modal(self.context, Event("MOUSEMOVE")), {"RUNNING_MODAL"})
        self.assertEqual(self.frames, [1])
        for moment in (0.003, 0.005, 0.009):
            self.assertEqual(self.tick(operator, moment), {"RUNNING_MODAL"})
        self.assertEqual(self.frames, [1])
        self.assertEqual(self.tick(operator, 0.01), {"RUNNING_MODAL"})
        for moment in (0.01, 0.01, 0.019):
            self.assertEqual(self.tick(operator, moment), {"RUNNING_MODAL"})
        self.assertEqual(self.frames, [1, 2])
        self.assertEqual(self.tick(operator, 20.0), {"RUNNING_MODAL"})
        self.assertEqual(self.frames, [1, 2, 3], "Late timer must advance only one frame")
        self.assertEqual(self.tick(operator, 20.0), {"RUNNING_MODAL"})
        self.assertEqual(self.tick(operator, 20.011), {"FINISHED"})
        self.assert_finished(operator)
        self.assertEqual(self.tick(operator, 30.0), {"CANCELLED"})
        self.assertEqual(self.frames, [1, 2, 3])

    def test_esc_closes_started_generator_and_cleanup_is_idempotent(self):
        operator = self.begin()
        self.assertEqual(operator.modal(self.context, Event("ESC", "PRESS")), {"CANCELLED"})
        operator.cancel(self.context)
        self.assertEqual(self.tick(operator, 1.0), {"CANCELLED"})
        self.assert_finished(operator)
        self.assertEqual(self.frames, [1])

    def test_queued_timers_wait_after_a_slow_frame(self):
        operator = self.begin()
        self.step_work = 0.2
        self.assertEqual(self.tick(operator, 0.01), {"RUNNING_MODAL"})
        self.assertEqual(self.frames, [1, 2])
        self.assertAlmostEqual(self.now, 0.21)
        for moment in (0.21, 0.21, 0.219):
            self.assertEqual(self.tick(operator, moment), {"RUNNING_MODAL"})
        self.assertEqual(self.frames, [1, 2])
        self.assertEqual(self.tick(operator, 0.221), {"RUNNING_MODAL"})
        self.assertEqual(self.frames, [1, 2, 3])
        operator.cancel(self.context)
        self.assert_finished(operator)

    def test_unregister_closes_and_removes_timer_before_late_event(self):
        operator = self.begin()
        self.runtime["stop_skirt_runtime"]()
        self.runtime["stop_skirt_runtime"]()
        self.assert_finished(operator)
        self.assertEqual(self.tick(operator, 1.0), {"CANCELLED"})
        self.assertEqual(self.frames, [1])

    def test_frame_failure_cleans_up_and_reports_cancelled(self):
        operator = self.begin()
        self.error = ValueError("Synthetic frame failure")
        self.assertEqual(self.tick(operator, 0.01), {"CANCELLED"})
        self.assert_finished(operator)
        self.assertIn("Synthetic frame failure", operator.reports[-1][1])

    def test_animation_completion_remembers_result_before_releasing_source(self):
        operator = self.begin(kind="ANIMATION")
        self.tick(operator, 0.01)
        self.tick(operator, 0.021)
        self.assertEqual(self.tick(operator, 0.032), {"FINISHED"})
        self.assert_finished(operator)
        self.assertEqual(self.remembered, [(self.source, {"mesh": "Independent baked mesh"})])

    def test_handler_failure_cleans_up_after_timer_creation(self):
        self.wm.fail_handler = True
        operator = self.runtime["_SkirtBakeOperator"]()
        operator.report = lambda *_args: None
        self.assertEqual(operator.invoke(self.context, Event("LEFTMOUSE")), {"CANCELLED"})
        self.assert_finished(operator)

    def test_invalid_window_does_not_interrupt_registry_and_generator_cleanup(self):
        operator = self.begin()
        self.wm.invalid = True
        operator.cancel(self.context)
        operator.cancel(self.context)
        self.assert_finished(operator)

    def test_invalid_window_during_timer_progress_still_closes_and_cleans_up(self):
        operator = self.begin()
        self.wm.invalid = True
        self.assertEqual(self.tick(operator, 0.01), {"CANCELLED"})
        self.assert_finished(operator)
        self.assertIn("Window removed during progress update", operator.reports[-1][1])

    def test_reference_error_in_frame_generator_cleans_up(self):
        operator = self.begin()
        self.error = ReferenceError("Lost scene reference")
        self.assertEqual(self.tick(operator, 0.01), {"CANCELLED"})
        self.assert_finished(operator)
        self.assertIn("Lost scene reference", operator.reports[-1][1])


if __name__ == "__main__":
    unittest.main()
