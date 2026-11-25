"""Provisional Action changes cannot tear down the rig's bind session."""

from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class Rig(dict):
    def __init__(self, uid):
        super().__init__()
        self.session_uid = uid
        self.binding = 'original'


class BindExpiryDeferralTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType('_bind_expiry_deferral_test')
        package.__path__ = [str(ROOT)]
        bpy = ModuleType('bpy')
        bpy.app = ModuleType('bpy.app')
        bpy.app.handlers = ModuleType('bpy.app.handlers')
        bpy.app.handlers.persistent = lambda function: function
        bpy.context = NS(temp_override=lambda **kwargs: nullcontext())
        package.control_pose_assets = NS(_FIELDS={})
        package.natural_bind_graph = NS()
        self.keys = package.natural_bind_keys = NS(
            KEY='keyed', desired=Mock(return_value='new_route'),
            route=Mock(), input_values=Mock(return_value={}))
        self.enterContext(patch.dict(sys.modules, {
            package.__name__: package, 'bpy': bpy,
            'bpy.app': bpy.app, 'bpy.app.handlers': bpy.app.handlers,
        }))
        spec = importlib.util.spec_from_file_location(
            package.__name__ + '.natural_bind_mode', ROOT / 'natural_bind_mode.py')
        self.mode = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mode)
        self.mode._unwatch = Mock()
        self.mode._observe_key_operator = Mock()
        self.mode._match = lambda scene, rig, record: rig.binding == record['binding']
        self.mode._binding = lambda rig: rig.binding
        self.mode._release_inputs = Mock()
        self.ended = []

        def end(scene, rig, record, *, allow_keying):
            self.ended.append((rig.session_uid, allow_keying))
            del rig[self.mode.KEY]

        self.mode._end = Mock(side_effect=end)
        self.scene = NS(frame_current=1, frame_subframe=0.0)

    def preview(self, uid):
        rig = Rig(uid)
        rig[self.mode.KEY] = {'binding': 'original', 'token': object()}
        self.mode._sessions[uid] = (self.scene, rig)
        self.mode._preview_inputs[uid] = []
        self.mode._known_actions[uid] = set()
        self.mode._operators[uid] = set()
        return rig

    def keyed(self, uid):
        rig = Rig(uid)
        rig[self.keys.KEY] = {'token': object()}
        self.mode._keyed[uid] = (self.scene, rig)
        return rig

    def test_refresh_transfers_current_frame_and_keyed_ownership(self):
        rig = self.preview(1)
        keyed = self.keyed(2)
        mode = self.mode
        mode.bpy.app.timers = NS(is_registered=lambda cb: False)
        mode._watch = Mock()
        mode._route_inputs[2] = ((1, 0.0), 'original', {})
        mode._pose_inputs[2] = {('Foot', 'rotation_euler', 0): (-0.4, 0.0)}
        mode._applied[2] = 'active route'
        preview = rig[mode.KEY]
        with mode.defer_expiry(rig):
            self.assertFalse(mode.refresh_ready())
        self.assertTrue(mode.refresh_ready())
        handoff = mode.detach_for_refresh()
        self.assertFalse(any(mode._refresh_maps().values()))
        mode._expire(all_sessions=True)
        mode.resume_after_refresh(handoff)
        self.assertIs(rig[mode.KEY], preview)
        self.assertIs(mode._keyed[2][1], keyed)
        self.assertEqual(mode._pose_inputs[2][('Foot', 'rotation_euler', 0)], (-0.4, 0.0))
        self.assertEqual(self.ended, [])
        mode._watch.assert_called_once()
        mode._release_inputs.assert_not_called()

    def test_failed_refresh_subscription_can_reattach_to_previous_runtime(self):
        rig = self.preview(3)
        mode = self.mode
        mode.bpy.app.timers = NS(is_registered=lambda cb: False)
        handoff = mode.detach_for_refresh()
        mode._watch = Mock(side_effect=RuntimeError('subscription failed'))
        with self.assertRaisesRegex(RuntimeError, 'subscription failed'):
            mode.resume_after_refresh(handoff)
        self.assertFalse(any(mode._refresh_maps().values()))
        mode._watch = Mock()
        mode.resume_after_refresh(handoff)
        self.assertIs(mode._sessions[3][1], rig)
        self.assertIn(mode.KEY, rig)
        self.assertEqual(self.ended, [])

    def test_default_expiry_still_preserves_matching_and_removes_changed(self):
        changed, matching = self.preview(1), self.preview(2)
        changed.binding = 'new'
        self.mode._expire()
        self.assertEqual(self.ended, [(1, True)])
        self.assertNotIn(1, self.mode._sessions)
        self.assertNotIn(1, self.mode._preview_inputs)
        self.assertIn(self.mode.KEY, matching)

    def test_failed_activation_and_rollback_keep_exact_session_and_ownership(self):
        rig = self.preview(1)
        record = rig[self.mode.KEY]
        runtime = self.mode._sessions[1]
        with self.assertRaisesRegex(ValueError, 'late frame failure'):
            with self.mode.defer_expiry(rig):
                try:
                    rig.binding = 'new'
                    self.mode._expire()
                    raise ValueError('late frame failure')
                except ValueError:
                    rig.binding = 'original'
                    self.mode._expire()
                    raise
        self.mode._expire()
        self.assertIs(rig[self.mode.KEY], record)
        self.assertIs(self.mode._sessions[1], runtime)
        self.assertEqual(self.ended, [])
        self.assertEqual(self.mode._expiry_deferrals, {})

    def test_only_target_is_deferred_even_for_forced_expiry(self):
        held, other = self.preview(1), self.preview(2)
        with self.mode.defer_expiry(held):
            held.binding = other.binding = 'new'
            self.mode._expire(all_sessions=True)
            self.assertIn(self.mode.KEY, held)
            self.assertIn(1, self.mode._preview_inputs)
            self.assertNotIn(self.mode.KEY, other)
        self.assertEqual(self.ended, [(2, False)])

    def test_success_expires_target_once_after_commit_and_scope_exit(self):
        held, other = self.preview(1), self.preview(2)
        with self.mode.defer_expiry(held) as transition:
            held.binding = other.binding = 'new'
            transition.commit()
            self.assertEqual(self.ended, [])
        self.assertEqual(self.ended, [(1, True)])
        self.assertIn(self.mode.KEY, other)
        self.assertEqual(self.mode._expiry_deferrals, {})
        self.mode._expire()
        self.assertEqual(self.ended, [(1, True), (2, True)])

    def test_inner_commit_cannot_force_uncommitted_outer_transition(self):
        rig = self.preview(1)
        with self.mode.defer_expiry(rig):
            with self.mode.defer_expiry(rig) as inner:
                rig.binding = 'new'
                inner.commit()
            self.assertEqual(self.ended, [])
            rig.binding = 'original'
        self.assertEqual(self.ended, [])
        self.assertEqual(self.mode._expiry_deferrals, {})

    def test_outer_can_commit_after_caught_inner_failure_was_rolled_back(self):
        rig = self.preview(1)
        with self.mode.defer_expiry(rig) as outer:
            with self.assertRaises(ValueError):
                with self.mode.defer_expiry(rig):
                    raise ValueError('rolled back inner attempt')
            rig.binding = 'new'
            outer.commit()
        self.assertEqual(self.ended, [(1, True)])

    def test_exception_after_commit_flag_does_not_expire(self):
        rig = self.preview(1)
        with self.assertRaises(ValueError):
            with self.mode.defer_expiry(rig) as transition:
                transition.commit()
                raise ValueError('caller still must roll back before leaving')
        self.assertEqual(self.ended, [])
        self.assertEqual(self.mode._expiry_deferrals, {})

    def test_keyed_route_and_input_release_are_deferred_until_target_commit(self):
        held, other = self.keyed(1), self.keyed(2)
        with self.mode.defer_expiry(held) as transition:
            self.mode._expire()
            self.keys.route.assert_called_once()
            self.assertIs(self.keys.route.call_args.args[1], other)
            self.mode._release_inputs.assert_called_once_with(2, other)
            transition.commit()
        self.assertEqual(self.keys.route.call_count, 2)
        self.assertIs(self.keys.route.call_args.args[1], held)
        self.assertEqual(self.mode._route_inputs[1][:2], ((1, 0.0), 'original'))

    def test_keyed_cleanup_cannot_pop_a_held_target(self):
        rig = self.keyed(1)
        del rig[self.keys.KEY]
        with self.mode.defer_expiry(rig):
            self.mode._expire(all_sessions=True)
            self.assertIn(1, self.mode._keyed)
            self.mode._release_inputs.assert_not_called()
        self.mode._expire()
        self.assertNotIn(1, self.mode._keyed)

    def test_expiry_error_is_committed_recovery_with_no_leaked_deferral(self):
        rig = self.preview(1)
        self.mode._end.side_effect = ValueError('native teardown failed')
        with self.assertRaises(self.mode.ExpiryCommitError) as caught:
            with self.mode.defer_expiry(rig) as transition:
                rig.binding = 'new'
                transition.commit()
        self.assertTrue(caught.exception.committed)
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertEqual(rig.binding, 'new')
        self.assertEqual(self.mode._expiry_deferrals, {})
        self.assertFalse(self.mode._busy)

    def test_busy_commit_cannot_silently_skip_final_expiry(self):
        rig = self.preview(1)
        with self.assertRaises(self.mode.ExpiryCommitError):
            with self.mode.defer_expiry(rig) as transition:
                self.mode._busy = True
                transition.commit()
        self.assertEqual(self.mode._expiry_deferrals, {})

    def test_lifo_ownership_and_single_use(self):
        rig = self.preview(1)
        outer, inner = self.mode.defer_expiry(rig), self.mode.defer_expiry(rig)
        outer.__enter__()
        inner.__enter__()
        with self.assertRaisesRegex(RuntimeError, 'nesting order'):
            outer.__exit__(None, None, None)
        with self.assertRaisesRegex(RuntimeError, 'nesting order'):
            outer.commit()
        inner.__exit__(None, None, None)
        outer.__exit__(None, None, None)
        with self.assertRaisesRegex(RuntimeError, 'reused'):
            outer.__enter__()
        with self.assertRaisesRegex(RuntimeError, 'not active'):
            outer.commit()
        self.assertEqual(self.mode._expiry_deferrals, {})


if __name__ == '__main__':
    unittest.main()
