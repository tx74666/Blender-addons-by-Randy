"""Explicit Control Mode and owned Hair recovery at the service boundary."""
import json
from types import SimpleNamespace
import unittest

import test_animation_physics as fixture

physics = fixture.physics
ORIGINAL_KEY = 'character_designer_body_original_mode_v1'


class RecoveryControlTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PhysicsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def mode_fixture(self):
        f = self.f
        def enter(context, rig):
            f.calls.append(('original-enter', rig.name))
            rig[ORIGINAL_KEY] = '{"session":"existing guarded enter"}'
        def leave(context, rig):
            f.calls.append(('original-leave', rig.name))
            rig.pop(ORIGINAL_KEY)
        f.modules['body_original_mode'] = SimpleNamespace(
            active=lambda rig: ORIGINAL_KEY in rig, enter=enter, leave=leave)
        view = {'mode': None}
        def restore(rig):
            f.calls.append(('display-restore', rig.name))
            view['mode'] = None
        f.modules['bone_display'] = SimpleNamespace(view_mode=lambda rig: view['mode'], restore_view=restore)
        return view

    def pending_hair(self, *, active=False, cleanup=False):
        f = self.f
        source = f.add_hair()
        pending = {'value': not active}
        f.hair_state.update(active=active, source=source.name,
            source_pointer=source.as_pointer(), paused=False, cleanup_pending=cleanup)
        def preview(source):
            return dict(active=f.hair_state['active'], paused=False,
                pending_recovery=pending['value'], stopped_confirmed=not pending['value'], message='')
        def stop(context, *, source):
            f.calls.append(('recover-own-hair', source.name))
            f.hair_state.update(active=False, cleanup_pending=False)
            pending['value'] = False
        f.hair.preview_state = preview
        f.hair.stop_preview = stop
        return source, pending

    def test_control_mode_uses_actual_session_and_native_display_without_saving(self):
        f = self.f
        view = self.mode_fixture()
        self.assertTrue(physics.status(f.context)['control_mode'])
        view['mode'] = 'HAIR'
        self.assertFalse(physics.status(f.context)['control_mode'])
        view['mode'] = None
        f.rig[ORIGINAL_KEY] = '{}'
        self.assertFalse(physics.status(f.context)['control_mode'])
        self.assertNotIn(physics.STATE_KEY, f.rig)
        self.assertFalse(f.calls)

    def test_proved_wiggle_layer_does_not_require_control_mode(self):
        f = self.f
        self.mode_fixture()
        f.rig[ORIGINAL_KEY] = '{"session":"real owner token"}'
        f.record['physics'] = None
        f.source[physics.SKIRT_RECORD] = json.dumps(f.record)
        f.modules['skirt_rig']._require_controls_for_setup = lambda source: (_ for _ in ()).throw(ValueError('Original owns display'))
        f.modules['skirt_original_mode'].wiggle_original_context = lambda source: (f.rig, f.rig[ORIGINAL_KEY])
        row = physics.status(f.context)['dresses'][0]
        self.assertFalse(physics.status(f.context)['control_mode'])
        self.assertFalse(row['requires_controls'])
        self.assertFalse(row['context_blocked'])
        self.assertTrue(row['available'])
        self.assertFalse(f.calls)

    def test_unsupported_original_layer_retains_existing_context_guard(self):
        f = self.f
        self.mode_fixture()
        f.rig[ORIGINAL_KEY] = '{}'
        f.modules['skirt_rig']._require_controls_for_setup = lambda source: (_ for _ in ()).throw(ValueError('Original owns display'))
        row = physics.status(f.context)['dresses'][0]
        self.assertTrue(row['requires_controls'])
        self.assertTrue(row['context_blocked'])
        self.assertFalse(row['available'])
        self.assertFalse(f.calls)

    def test_changed_lightweight_original_proof_does_not_bypass_context_guard(self):
        f = self.f
        self.mode_fixture()
        f.rig[ORIGINAL_KEY] = '{}'
        f.modules['skirt_rig']._require_controls_for_setup = lambda source: (_ for _ in ()).throw(ValueError('Original owns display'))
        f.modules['skirt_original_mode'].wiggle_original_context = lambda source: (_ for _ in ()).throw(ValueError('Changed source owner'))
        self.assertTrue(physics.status(f.context)['dresses'][0]['requires_controls'])
        self.assertFalse(f.calls)

    def test_control_off_calls_existing_original_enter_once(self):
        f = self.f
        self.mode_fixture()
        for _ in range(2):
            self.assertFalse(physics.set_control_mode(f.context, False)['control_mode'])
        self.assertEqual(f.calls, [('original-enter', 'Rig')])
        self.assertNotIn(physics.STATE_KEY, f.rig)

    def test_control_on_calls_strict_original_leave_and_not_display_restore(self):
        f = self.f
        self.mode_fixture()
        f.rig[ORIGINAL_KEY] = '{}'
        for _ in range(2):
            self.assertTrue(physics.set_control_mode(f.context, True)['control_mode'])
        self.assertEqual(f.calls, [('original-leave', 'Rig')])

    def test_control_on_restores_existing_native_view(self):
        f = self.f
        view = self.mode_fixture()
        view['mode'] = 'DRESS'
        self.assertTrue(physics.set_control_mode(f.context, True)['control_mode'])
        self.assertEqual(f.calls, [('display-restore', 'Rig')])

    def test_control_off_delegates_hair_view_to_original_enter_transaction(self):
        f = self.f
        view = self.mode_fixture()
        view['mode'] = 'HAIR'
        physics.set_control_mode(f.context, False)
        self.assertEqual(f.calls, [('original-enter', 'Rig')])

    def test_control_mode_is_available_while_physics_permissions_are_off(self):
        f = self.f
        self.mode_fixture()
        state = physics._fresh()
        state['enabled'] = state['skirt_enabled'] = state['hair_enabled'] = False
        physics._write(f.rig, state)
        raw = f.rig[physics.STATE_KEY]
        physics.set_control_mode(f.context, False)
        physics.set_control_mode(f.context, True)
        self.assertEqual(f.rig[physics.STATE_KEY], raw)

    def test_control_success_clears_only_its_error(self):
        f = self.f
        self.mode_fixture()
        for component in ('CONTROL', 'ALL', 'SKIRT', 'HAIR'):
            physics.record_error(f.context, component, component+' error')
        physics.record_error(f.context, 'SKIRT', 'owned source error', source_name=f.source.name)
        physics.set_control_mode(f.context, True)
        self.assertEqual(physics._last_error(f.rig, 'CONTROL'), '')
        for component in ('ALL', 'SKIRT', 'HAIR'):
            self.assertEqual(physics._last_error(f.rig, component), component+' error')
        self.assertEqual(physics._last_error(f.rig, 'SKIRT', f.source), 'owned source error')

    def test_control_refusal_is_specific_and_does_not_change_modes_or_permission(self):
        f = self.f
        self.mode_fixture()
        f.rig[ORIGINAL_KEY] = '{"original":"unchanged"}'
        before = f.rig[ORIGINAL_KEY]
        def refuse(context, rig):
            raise ValueError('Original edited Rest cannot be matched')
        f.modules['body_original_mode'].leave = refuse
        with self.assertRaisesRegex(ValueError, 'edited Rest'):
            physics.set_control_mode(f.context, True)
        self.assertEqual(f.rig[ORIGINAL_KEY], before)
        self.assertNotIn(physics.STATE_KEY, f.rig)
        self.assertEqual(physics.status(f.context)['control_error'], 'Original edited Rest cannot be matched')

    def test_control_edit_mode_refuses_before_existing_actions(self):
        f = self.f
        self.mode_fixture()
        f.context.mode = 'EDIT_ARMATURE'
        for value in (False, True):
            with self.assertRaisesRegex(ValueError, 'Object or Pose'):
                physics.set_control_mode(f.context, value)
        self.assertFalse(f.calls)

    def test_control_non_boolean_refuses_without_actions(self):
        self.mode_fixture()
        for value in (None, 0, 1, 'true'):
            with self.assertRaises(ValueError):
                physics.set_control_mode(self.f.context, value)
        self.assertFalse(self.f.calls)

    def test_hair_on_recovers_history_then_starts_owned_source(self):
        f = self.f
        source, pending = self.pending_hair()
        state = physics.set_component(f.context, 'HAIR', True)
        self.assertFalse(pending['value'])
        self.assertTrue(state['hair']['running'])
        self.assertEqual(f.calls, [('recover-own-hair', source.name), ('hair-start', source.name, ['one'])])

    def test_hair_on_retries_partial_cleanup_before_permission_resume(self):
        f = self.f
        source, _pending = self.pending_hair(active=True, cleanup=True)
        state = physics._fresh()
        state['hair_enabled'] = False
        physics._write(f.rig, state)
        result = physics.set_component(f.context, 'HAIR', True)
        self.assertTrue(result['hair']['running'])
        self.assertEqual(f.calls, [('recover-own-hair', source.name), ('hair-start', source.name, ['one'])])
        self.assertFalse(any(call[0]=='hair' for call in f.calls))

    def test_hair_recovery_refusal_leaves_exact_permission_marker_and_native_state(self):
        f = self.f
        source, pending = self.pending_hair()
        before = json.dumps(physics._fresh(), indent=2)
        f.rig[physics.STATE_KEY] = before
        def refuse(*args, **kwargs):
            raise ValueError('Saved Hair ownership changed')
        f.hair.stop_preview = refuse
        with self.assertRaisesRegex(ValueError, 'ownership changed'):
            physics.set_component(f.context, 'HAIR', True)
        self.assertEqual(f.rig[physics.STATE_KEY], before)
        self.assertTrue(pending['value'])
        self.assertFalse(f.hair_state['active'])
        self.assertFalse(f.calls)
        self.assertEqual(physics._last_error(f.rig, 'HAIR', source), 'Saved Hair ownership changed')

    def test_hair_start_failure_after_recovery_stays_stopped_with_exact_prior_permission(self):
        f = self.f
        source, pending = self.pending_hair()
        state = physics._fresh()
        state['hair_enabled'] = False
        before = json.dumps(state, indent=2)
        f.rig[physics.STATE_KEY] = before
        def fail(*args, **kwargs):
            raise ValueError('New Hair start failed')
        f.hair.start_preview = fail
        with self.assertRaisesRegex(ValueError, 'New Hair start failed'):
            physics.set_component(f.context, 'HAIR', True)
        self.assertEqual(f.rig[physics.STATE_KEY], before)
        self.assertFalse(pending['value'])
        self.assertFalse(f.hair_state['active'])
        self.assertTrue(f.hair.preview_state(source)['stopped_confirmed'])
        self.assertEqual(f.calls, [('recover-own-hair', source.name)])

    def test_hair_foreign_active_session_never_recovered_or_stopped(self):
        f = self.f
        source, _pending = self.pending_hair()
        foreign = fixture.Object(source.name, 'MESH')
        f.hair_state.update(active=True, source=source.name, source_pointer=foreign.as_pointer(), cleanup_pending=True)
        before = dict(f.hair_state)
        with self.assertRaisesRegex(ValueError, 'Another Hair preview'):
            physics.set_component(f.context, 'HAIR', True)
        self.assertEqual(f.hair_state, before)
        self.assertFalse(f.calls)
        self.assertNotIn(physics.STATE_KEY, f.rig)

    def test_hair_ambiguous_inactive_sources_are_not_guessed_during_recovery(self):
        f = self.f
        self.pending_hair()
        source2 = f.add_hair()
        source2.name = 'Second Hair'
        with self.assertRaisesRegex(ValueError, 'one Hair source'):
            physics.set_component(f.context, 'HAIR', True)
        self.assertFalse(f.calls)

    def test_hair_on_with_master_off_only_stores_preference(self):
        f = self.f
        _source, pending = self.pending_hair()
        state = physics._fresh()
        state['enabled'] = state['hair_enabled'] = False
        physics._write(f.rig, state)
        result = physics.set_component(f.context, 'HAIR', True)
        self.assertTrue(result['hair_enabled'])
        self.assertTrue(pending['value'])
        self.assertFalse(f.calls)

    def test_direct_hair_start_also_recovers_before_start_without_permission_write(self):
        f = self.f
        source, pending = self.pending_hair()
        physics.start_hair_preview(f.context)
        self.assertFalse(pending['value'])
        self.assertNotIn(physics.STATE_KEY, f.rig)
        self.assertEqual(f.calls, [('recover-own-hair', source.name), ('hair-start', source.name, ['one'])])


if __name__ == '__main__':
    unittest.main()
