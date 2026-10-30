"""Pure lifecycle regressions for the canonical Forearm playback preparation.

Only selected actual product definitions are executed through AST. Small native
ID/context doubles and numeric-call mocks exercise admission, invalidation and
legacy routing; no bpy import, native geometry validation or timing is involved.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "addons/character_designer/forearm_twist.py"
SOURCE_SHA256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
RECORD_KEY = "character_designer_forearm_twist_v1"
PREVIEW_KEY = "character_designer_forearm_twist_preview_v1"
LOCK_KEY = "character_designer_twist_render_lock"


class ID(dict):
    sequence = 100

    def __init__(self, name="ID", **fields):
        super().__init__()
        ID.sequence += 1
        self.pointer = ID.sequence
        self.session_uid = self.pointer + 10000
        self.name = name
        self.__dict__.update(fields)

    def as_pointer(self):
        return self.pointer

    def __bool__(self):
        return True

    def __eq__(self, other):
        return self is other

    __hash__ = object.__hash__


class Objects(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)


class Timers:
    def __init__(self):
        self.callbacks = []

    def is_registered(self, fn):
        return fn in self.callbacks

    def register(self, fn, **kwargs):
        self.callbacks.append(fn)

    def unregister(self, fn):
        self.callbacks.remove(fn)


class ReadScope:
    def __init__(self):
        self.depth = 0
        self.events = []

    def read_scope(self):
        return self

    def __enter__(self):
        self.depth += 1
        self.events.append(("enter", self.depth))

    def __exit__(self, kind, error, traceback):
        self.events.append(("exit", self.depth, None if kind is None else kind.__name__))
        self.depth -= 1
        return False

    def clear(self):
        pass


class Fixture:
    def __init__(self, *, enabled=True):
        self.graph = NS(mode="VIEWPORT")
        self.cache = ReadScope()
        self.key = NS(name="Owned", value=1.0, mute=False)
        self.keys = ID("Keys", reference_key=NS(name="Basis"))
        self.mesh = ID("Mesh", shape_keys=self.keys)
        self.arm_data = ID("ArmData", pose_position="POSE")
        self.arm = ID("Rig", type="ARMATURE", mode="POSE", data=self.arm_data)
        self.modifier = ID("Armature modifier", type="ARMATURE", object=self.arm)
        self.owner = ID("Body", type="MESH", mode="OBJECT", data=self.mesh, modifiers=[self.modifier])
        self.records = {"L": {"key": "Owned", "enabled": enabled, "chain": ["upper", "lower", "hand"]}}
        self.owner[RECORD_KEY] = json.dumps(self.records)
        self.scene = ID("Scene", type="SCENE", objects=Objects([self.owner, self.arm]),
                        render=NS(use_lock_interface=False))
        self.graph.scene = self.scene
        self.owner.users_scene = [self.scene]
        self.screen = NS(is_animation_playing=False)
        self.window = NS(scene=self.scene, screen=self.screen)
        self.manager = NS(windows=[self.window])
        self.graph_get = Mock(return_value=self.graph)
        self.view_update = Mock(side_effect=AssertionError("Unexpected explicit native view update"))
        self.bpy = NS(data=NS(objects=Objects([self.owner, self.arm]), scenes=[self.scene], window_managers=[self.manager]),
                      context=NS(scene=self.scene, evaluated_depsgraph_get=self.graph_get,
                                 view_layer=NS(update=self.view_update)),
                      app=NS(background=True, timers=Timers(), handlers=NS()))
        for event in ("depsgraph_update_post", "frame_change_post", "animation_playback_pre", "animation_playback_post",
                      "load_pre", "save_pre", "undo_pre", "redo_pre", "load_post", "undo_post", "redo_post"):
            setattr(self.bpy.app.handlers, event, [])
        self.events = []
        self.prepare_error = self.apply_error = self.math_error = None
        self.prepared_result = None
        self.prepare = Mock(side_effect=self.prepare_call)
        self.apply = Mock(side_effect=self.apply_call)
        self.numeric = Mock(side_effect=self.numeric_call)
        self.ns = {"__name__": __name__, "json": json, "bpy": self.bpy, "persistent": lambda fn: fn,
                   "contextmanager": contextmanager,
                   "RECORD_KEY": RECORD_KEY, "PREVIEW_KEY": PREVIEW_KEY, "LOCK_KEY": LOCK_KEY,
                   "_PLAYBACK_PREPARED": {}, "_SESSION": None, "_BUSY": False,
                   "_HISTORY_RECOVERY_PENDING": False, "_INITIALIZE_PENDING": False,
                   "_SCENE_CLEANUP_PENDING": False, "_SCENE_STATE_ACTIVE": False, "_RUNTIME_REGISTERED": True,
                   "_DRAW_HANDLE": None, "_CACHE": {}, "_OUTPUT_CACHE": {}, "_KEY_REFERENCES": {}, "_ERRORS": {},
                   "validation_cache": self.cache, "_prepare_runtime_records": self.prepare,
                   "_apply_runtime_records": self.apply, "_calculate_records": self.numeric,
                   "_managed_key": Mock(return_value=self.key), "_recover_previews": Mock(return_value=False),
                   "_remember_named_keys": Mock(), "_set_render_lock": Mock(), "finish_test": Mock(),
                   "ForearmTwistError": type("ForearmTwistError", (ValueError,), {}),
                   "traceback": NS(print_exc=Mock())}
        names = {"_records", "_write_records", "_calculate_object", "defer_runtime", "update_runtime", "_graph_post", "_frame_post",
                 "_load_post", "_redo_pre", "_undo_post", "_complete_history_recovery", "_restore_preview",
                 "_cleanup_runtime_scene", "_scene_data_available", "_complete_runtime_lifecycle",
                 "_request_runtime_lifecycle", "register_forearm_twist_runtime", "unregister_forearm_twist_runtime"}
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and (node.name in names or node.name.startswith("_playback_"))
                 or isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "_HANDLERS" for target in node.targets)]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), self.ns)

    def prepare_call(self, owner):
        self.events.append(("prepare", owner, bool(self.ns["_PLAYBACK_PREPARED"]), self.cache.depth))
        if self.prepare_error is not None:
            raise self.prepare_error
        return deepcopy(self.ns["_records"](owner) if self.prepared_result is None else self.prepared_result)

    def apply_call(self, owner, records, graph):
        self.events.append(("apply", owner, graph, self.cache.depth))
        if self.apply_error is not None:
            raise self.apply_error
        self.ns["_write_records"](owner, records)
        self.records = deepcopy(records)
        self.numeric(owner, graph)

    def numeric_call(self, owner, graph):
        self.events.append(("numeric", owner, graph, self.cache.depth))
        if self.math_error is not None:
            raise self.math_error
        self.ns["_ERRORS"].pop(owner.name, None)

    def arm_playback(self):
        # Blender invokes PRE before its screen playback timer exists.
        self.screen.is_animation_playing = False
        self.ns["_playback_pre"](self.scene, self.graph)
        self.screen.is_animation_playing = True

    def frame(self, graph=None):
        self.ns["_frame_post"](self.scene, self.graph if graph is None else graph)

    def assert_live(self, case):
        case.assertIn(self.owner.as_pointer(), self.ns["_PLAYBACK_PREPARED"])


class PlaybackLifecycleTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
            raise AssertionError("Forearm source changed while pure lifecycle tests were running")

    def test_entry_prepares_before_screen_playing_then_handlers_use_original_numeric(self):
        f = Fixture()
        f.arm_playback()
        f.assert_live(self)
        self.assertEqual(f.prepare.call_count, 1)
        self.assertEqual(f.numeric.call_count, 1)
        f.ns["_graph_post"](f.scene, f.graph)
        f.frame()
        self.assertEqual(f.prepare.call_count, 1)
        self.assertEqual(f.numeric.call_count, 3)
        self.assertIs(f.ns["_calculate_records"], f.numeric)
        self.assertTrue(all(event[2] is f.graph and event[3] == 1
                            for event in f.events if event[0] == "numeric"))
        self.assertEqual(f.cache.depth, 0)
        self.assertFalse(f.ns["_BUSY"])
        f.view_update.assert_not_called()

    def test_changed_preparation_is_applied_before_admission(self):
        f = Fixture()
        f.prepared_result = {"L": dict(f.records["L"], target="Current target")}
        f.arm_playback()
        self.assertEqual([event[0] for event in f.events], ["prepare", "apply", "numeric"])
        self.assertEqual(f.ns["_records"](f.owner), f.prepared_result)
        f.assert_live(self)
        f.frame()
        self.assertEqual(f.apply.call_count, 1)
        self.assertEqual(f.prepare.call_count, 1)

    def test_failed_prepare_apply_or_numeric_never_grants_entry(self):
        for stage in ("prepare", "apply", "math"):
            with self.subTest(stage=stage):
                f = Fixture()
                setattr(f, stage + "_error", ValueError(stage + " failed"))
                if stage == "apply":
                    f.prepared_result = {"L": dict(f.records["L"], target="Changed")}
                f.arm_playback()
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                self.assertIn(f.owner.name, f.ns["_ERRORS"])
                self.assertTrue(f.key.mute)
                self.assertFalse(f.ns["_BUSY"])
                self.assertEqual(f.cache.depth, 0)
                before = f.prepare.call_count
                f.frame()
                self.assertEqual(f.prepare.call_count, before + 1)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])

    def test_busy_unregistered_history_lifecycle_or_preview_entry_clears_without_work(self):
        cases = (("_BUSY", True), ("_RUNTIME_REGISTERED", False), ("_HISTORY_RECOVERY_PENDING", True),
                 ("_INITIALIZE_PENDING", True), ("_SCENE_CLEANUP_PENDING", True), ("_SESSION", {"preview": True}))
        for name, value in cases:
            with self.subTest(name=name):
                f = Fixture()
                f.ns["_PLAYBACK_PREPARED"]["old"] = ("primitive old token",)
                f.ns[name] = value
                f.ns["_playback_pre"](f.scene, f.graph)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                f.prepare.assert_not_called()
                f.numeric.assert_not_called()

    def test_no_owners_early_return_cannot_grant_entry_or_request_graph(self):
        f = Fixture()
        f.scene.objects = Objects([f.arm])
        f.ns["_playback_pre"](f.scene)
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        f.graph_get.assert_not_called()
        f.prepare.assert_not_called()

    def test_explicit_tool_update_and_direct_call_keep_legacy_during_play(self):
        for direct in (False, True):
            with self.subTest(direct=direct):
                f = Fixture()
                f.arm_playback()
                before = f.prepare.call_count
                if direct:
                    f.ns["_calculate_object"](f.owner, f.graph)
                else:
                    f.ns["update_runtime"](f.scene, f.graph)
                self.assertEqual(f.prepare.call_count, before + 1)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])

    def test_record_mode_and_binding_identity_changes_retire_without_regrant(self):
        changes = {
            "record": lambda f: f.owner.__setitem__(RECORD_KEY, json.dumps({"L": dict(f.records["L"], enabled=False)})),
            "object_mode": lambda f: setattr(f.owner, "mode", "EDIT"),
            "arm_mode": lambda f: setattr(f.arm, "mode", "OBJECT"),
            "pose_position": lambda f: setattr(f.arm_data, "pose_position", "REST"),
            "mesh_identity": lambda f: setattr(f.owner, "data", ID("NewMesh", shape_keys=f.keys)),
            "keys_identity": lambda f: setattr(f.mesh, "shape_keys", ID("NewKeys")),
            "modifier_identity": lambda f: f.owner.modifiers.__setitem__(0, ID("NewModifier", type="ARMATURE", object=f.arm)),
            "actual_rig": lambda f: setattr(f.modifier, "object", ID("OtherRig", type="ARMATURE", mode="POSE", data=f.arm_data)),
            "rig_data": lambda f: setattr(f.arm, "data", ID("OtherRigData", pose_position="POSE")),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                f = Fixture()
                f.arm_playback()
                change(f)
                before = f.prepare.call_count
                f.frame()
                f.frame()
                self.assertEqual(f.prepare.call_count, before + 2)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])

    def test_record_writer_clears_prepared_qualification(self):
        f = Fixture()
        f.arm_playback()
        f.ns["_write_records"](f.owner, {"L": dict(f.records["L"], enabled=False)})
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        before = f.prepare.call_count
        f.frame()
        self.assertEqual(f.prepare.call_count, before + 1)

    def test_render_and_unknown_graphs_do_not_borrow_viewport_admission(self):
        for mode in ("RENDER", None, "UNKNOWN"):
            with self.subTest(mode=mode):
                f = Fixture()
                f.arm_playback()
                before = f.prepare.call_count
                graph = NS(mode=mode)
                f.frame(graph)
                self.assertEqual(f.prepare.call_count, before + 1)
                self.assertIs(f.numeric.call_args.args[1], graph)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                f.frame()
                self.assertEqual(f.prepare.call_count, before + 2)

    def test_stopped_or_missing_playing_scene_witness_retires_fast_route(self):
        cases = (lambda f: setattr(f.screen, "is_animation_playing", False),
                 lambda f: setattr(f.window, "scene", ID("Another Scene")),
                 lambda f: setattr(f.window, "screen", None),
                 lambda f: setattr(f.bpy.data, "window_managers", None))
        for change in cases:
            with self.subTest(change=change):
                f = Fixture()
                f.arm_playback()
                change(f)
                self.assertIsNone(f.ns["_playback_token"](f.scene, f.owner, f.graph, require_playing=True))
                before = f.prepare.call_count
                f.frame()
                self.assertEqual(f.prepare.call_count, before + 1)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])

    def test_token_is_primitive_and_bad_actual_binding_has_no_token(self):
        def primitive(value):
            return all(primitive(item) for item in value) if isinstance(value, tuple) else type(value) in {str, int, float, bool, type(None)}
        f = Fixture()
        token = f.ns["_playback_token"](f.scene, f.owner, f.graph)
        self.assertIsNotNone(token)
        self.assertTrue(primitive(token))
        for change in (lambda f: f.owner.modifiers.clear(), lambda f: setattr(f.modifier, "type", "MIRROR"),
                       lambda f: setattr(f.modifier, "object", None), lambda f: setattr(f.arm, "mode", "EDIT"),
                       lambda f: f.owner.__setitem__(PREVIEW_KEY, "preview"),
                       lambda f: setattr(f.mesh, "shape_keys", None)):
            with self.subTest(change=change):
                current = Fixture()
                change(current)
                self.assertIsNone(current.ns["_playback_token"](current.scene, current.owner, current.graph))

    def test_missing_or_nonpositive_noninteger_lifetime_uid_forces_legacy(self):
        missing = object()
        for member in ("scene", "owner", "mesh", "keys", "arm", "arm_data"):
            for value in (missing, 0, -1, True, False, 1.5, "1"):
                with self.subTest(member=member, value="missing" if value is missing else value):
                    f = Fixture()
                    f.arm_playback()
                    native = getattr(f, member)
                    if value is missing:
                        del native.session_uid
                    else:
                        native.session_uid = value
                    self.assertIsNone(f.ns["_playback_token"](f.scene, f.owner, f.graph, require_playing=True))
                    before = f.prepare.call_count
                    f.frame()
                    f.frame()
                    self.assertEqual(f.prepare.call_count, before + 2)
                    self.assertFalse(f.ns["_PLAYBACK_PREPARED"])

    def test_controller_health_is_deferred_during_stable_watch_but_strict_at_stop(self):
        f = Fixture()
        f.arm_playback()
        f.assert_live(self)
        before_prepare, before_numeric = f.prepare.call_count, f.numeric.call_count
        # This changes the result of a full controller-definition check, not the
        # live matrices or a token identity. No numeric equivalence is simulated.
        f.prepare_error = ValueError("controller definition changed")
        f.ns["_graph_post"](f.scene, f.graph)
        f.frame()
        self.assertEqual(f.prepare.call_count, before_prepare)
        self.assertEqual(f.numeric.call_count, before_numeric + 2)
        self.assertIs(f.ns["_calculate_records"], f.numeric)
        self.assertEqual(f.ns["_ERRORS"], {})
        self.assertFalse(f.key.mute)
        f.assert_live(self)
        f.screen.is_animation_playing = False
        f.ns["_playback_post"](f.scene, f.graph)
        self.assertEqual(f.prepare.call_count, before_prepare + 1)
        self.assertEqual(f.numeric.call_count, before_numeric + 2)
        self.assertEqual(f.ns["_ERRORS"], {f.owner.name: "controller definition changed"})
        self.assertTrue(f.key.mute)
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        self.assertFalse(f.ns["_BUSY"])
        self.assertEqual(f.cache.depth, 0)
        f.view_update.assert_not_called()

    def test_defer_runtime_immediately_retires_admission_on_normal_exception_and_nested_exit(self):
        for flush in (False, True):
            for fail in (False, True):
                with self.subTest(flush=flush, exception=fail):
                    f = Fixture()
                    f.arm_playback()
                    before_prepare, before_numeric = f.prepare.call_count, f.numeric.call_count
                    propagated = False
                    try:
                        with f.ns["defer_runtime"](f.bpy.context, flush_on_exit=flush):
                            self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                            self.assertTrue(f.ns["_BUSY"])
                            if fail:
                                raise ValueError("synthetic transaction failure")
                    except ValueError as error:
                        propagated = True
                        self.assertTrue(fail)
                        self.assertEqual(str(error), "synthetic transaction failure")
                    self.assertEqual(propagated, fail)
                    self.assertEqual(f.prepare.call_count, before_prepare + int(flush))
                    self.assertEqual(f.numeric.call_count, before_numeric + int(flush))
                    self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                    self.assertFalse(f.ns["_BUSY"])
        f = Fixture()
        f.arm_playback()
        before_prepare, before_numeric = f.prepare.call_count, f.numeric.call_count
        f.ns["_BUSY"] = True
        with f.ns["defer_runtime"](f.bpy.context) as outer_refresh:
            self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
            with f.ns["defer_runtime"](f.bpy.context, flush_on_exit=False) as inner_refresh:
                outer_refresh()
                inner_refresh()
                self.assertTrue(f.ns["_BUSY"])
            self.assertTrue(f.ns["_BUSY"])
        self.assertTrue(f.ns["_BUSY"])
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        self.assertEqual(f.prepare.call_count, before_prepare)
        self.assertEqual(f.numeric.call_count, before_numeric)

    def test_only_actual_viewport_scene_graph_can_award_or_use_preparation(self):
        def wrong_graphs(f):
            other = ID("Other graph scene")
            return (NS(mode="VIEWPORT", scene=other), NS(mode="VIEWPORT", scene=None),
                    NS(mode="VIEWPORT"), NS(scene=f.scene), NS(mode="RENDER", scene=f.scene),
                    NS(mode="VIEWPORT", scene=NS(original=None)), None)

        # Both explicit supplied graphs and context fallback graphs must have
        # the actual scene identity. This is not a complete graph validator.
        for fallback in (False, True):
            seed = Fixture()
            for index in range(len(wrong_graphs(seed))):
                with self.subTest(context_fallback=fallback, graph_case=index):
                    f = Fixture()
                    graph = wrong_graphs(f)[index]
                    self.assertIsNone(f.ns["_playback_token"](f.scene, f.owner, graph))
                    if fallback:
                        f.graph_get.return_value = graph
                        f.ns["_playback_pre"](f.scene)
                    else:
                        # None necessarily exercises the same native fallback.
                        if graph is None:
                            f.graph_get.return_value = None
                        f.ns["_playback_pre"](f.scene, graph)
                    self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                    self.assertEqual(f.prepare.call_count, 1)

                    f = Fixture()
                    f.arm_playback()
                    graph = wrong_graphs(f)[index]
                    before = f.prepare.call_count
                    if fallback or graph is None:
                        f.graph_get.return_value = graph
                        f.ns["_frame_post"](f.scene, None)
                    else:
                        f.frame(graph)
                    self.assertEqual(f.prepare.call_count, before + 1)
                    self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                    f.frame()
                    self.assertEqual(f.prepare.call_count, before + 2)
        f = Fixture()
        original_viewport_graph = NS(mode="VIEWPORT", scene=NS(original=f.scene))
        f.ns["_playback_pre"](f.scene, original_viewport_graph)
        f.assert_live(self)
        f.screen.is_animation_playing = True
        f.frame(original_viewport_graph)
        self.assertEqual(f.prepare.call_count, 1)
        self.assertIs(f.numeric.call_args.args[1], original_viewport_graph)

    def test_stop_clears_before_one_legacy_refresh_with_supplied_graph(self):
        f = Fixture()
        f.arm_playback()
        f.screen.is_animation_playing = False
        before = f.prepare.call_count
        f.ns["_playback_post"](f.scene, f.graph)
        self.assertEqual(f.prepare.call_count, before + 1)
        self.assertFalse(f.events[-2][2])
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        f.graph_get.assert_not_called()
        f.view_update.assert_not_called()

    def test_numeric_error_uses_original_failure_policy_and_clears_qualification(self):
        f = Fixture()
        f.arm_playback()
        before = f.prepare.call_count
        f.math_error = ValueError("current numeric input failed")
        f.frame()
        self.assertEqual(f.prepare.call_count, before)
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        self.assertEqual(f.ns["_ERRORS"][f.owner.name], "current numeric input failed")
        self.assertTrue(f.key.mute)
        self.assertFalse(f.ns["_BUSY"])
        self.assertEqual(f.cache.depth, 0)
        self.assertEqual(f.cache.events[-1], ("exit", 1, "ValueError"))
        f.math_error = None
        f.frame()
        self.assertEqual(f.prepare.call_count, before + 1)

    def test_direct_legacy_math_exception_propagates_after_scope_exit(self):
        f = Fixture()
        f.math_error = ValueError("direct legacy error")
        with self.assertRaisesRegex(ValueError, "direct legacy error"):
            f.ns["_calculate_object"](f.owner, f.graph)
        self.assertEqual(f.cache.depth, 0)
        self.assertFalse(f.ns["_BUSY"])

    def test_undo_only_clears_then_fresh_graph_recovers_through_legacy(self):
        f = Fixture()
        f.arm_playback()
        before = f.prepare.call_count
        f.graph_get.side_effect = AssertionError("No graph request inside native history")
        f.ns["_undo_post"](None)
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        self.assertTrue(f.ns["_HISTORY_RECOVERY_PENDING"])
        self.assertEqual(f.prepare.call_count, before)
        f.ns["_frame_post"](f.scene)
        self.assertEqual(f.prepare.call_count, before)
        f.frame()
        self.assertEqual(f.prepare.call_count, before + 1)
        self.assertFalse(f.ns["_HISTORY_RECOVERY_PENDING"])
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        f.graph_get.assert_not_called()
        f.view_update.assert_not_called()

    def test_redo_pre_retires_before_interleaved_callback_and_stop_stays_strict(self):
        for callback in ("_graph_post", "_frame_post"):
            with self.subTest(callback=callback):
                f = Fixture()
                f.arm_playback()
                f.assert_live(self)
                before_prepare, before_numeric = f.prepare.call_count, f.numeric.call_count
                raw = f.owner[RECORD_KEY]
                f.ns["_redo_pre"](None)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                self.assertEqual(f.prepare.call_count, before_prepare)
                self.assertEqual(f.numeric.call_count, before_numeric)
                # Same IDs/modes/record and still-playing screen cannot borrow
                # the pre-Redo admission even before Redo's post callback.
                f.prepare_error = ValueError("restored controller needs strict validation")
                f.ns[callback](f.scene, f.graph)
                self.assertEqual(f.prepare.call_count, before_prepare + 1)
                self.assertEqual(f.numeric.call_count, before_numeric)
                self.assertEqual(f.owner[RECORD_KEY], raw)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                self.assertTrue(f.key.mute)
                f.prepare_error = None
                f.ns["_undo_post"](None)  # Native redo_post uses this handler.
                f.ns["_frame_post"](f.scene)
                self.assertTrue(f.ns["_HISTORY_RECOVERY_PENDING"])
                self.assertEqual(f.prepare.call_count, before_prepare + 1)
                f.frame()
                self.assertEqual(f.prepare.call_count, before_prepare + 2)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                f.screen.is_animation_playing = False
                f.ns["_playback_post"](f.scene, f.graph)
                self.assertEqual(f.prepare.call_count, before_prepare + 3)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
                f.graph_get.assert_not_called()
                f.view_update.assert_not_called()

    def test_load_preview_restore_and_scene_cleanup_clear_qualification(self):
        for callback in ("_load_post", "_restore_preview", "_cleanup_runtime_scene"):
            with self.subTest(callback=callback):
                f = Fixture()
                f.arm_playback()
                if callback == "_cleanup_runtime_scene":
                    f.ns[callback]()
                else:
                    f.ns[callback](None)
                self.assertFalse(f.ns["_PLAYBACK_PREPARED"])

    def test_registration_is_idempotent_and_unregister_removes_playback_hooks(self):
        f = Fixture()
        f.arm_playback()
        for _ in range(2):
            f.ns["register_forearm_twist_runtime"]()
            self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
            for event, callback in f.ns["_HANDLERS"]:
                self.assertEqual(getattr(f.bpy.app.handlers, event).count(callback), 1)
        f.arm_playback()
        f.ns["unregister_forearm_twist_runtime"]()
        self.assertFalse(f.ns["_PLAYBACK_PREPARED"])
        for event, callback in f.ns["_HANDLERS"]:
            self.assertNotIn(callback, getattr(f.bpy.app.handlers, event))
        self.assertFalse(f.bpy.app.timers.callbacks)
        self.assertFalse(f.ns["_RUNTIME_REGISTERED"])


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PlaybackLifecycleTests))
    print("forearm_source_sha256=" + SOURCE_SHA256)
    print("native_execution=false; bpy_import=false; numeric_geometry_or_speed_validation=false")
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
