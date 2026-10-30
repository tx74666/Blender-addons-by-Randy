"""Desired Dress choice stays independent of protected native application."""
import json
from types import SimpleNamespace
import unittest

import test_animation_physics as fixture


physics = fixture.physics


class SkirtChoiceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PhysicsTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.record['physics'] = None
        self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
        self.actual = 'MANUAL'
        self.original = False
        self.f.wiggle.mode = lambda source: self.actual
        self.f.wiggle._state = lambda source: (
            {'mode': self.actual} if source.get(self.f.wiggle.STATE_KEY) is not None else None)
        self.f.modules['skirt_rig']._require_controls_for_setup = self.require_controls
        self.f.wiggle.set_mode = self.apply_mode

    def require_controls(self, source):
        if self.original:
            raise ValueError('Original ownership is active.')

    def apply_mode(self, context, source, mode):
        self.require_controls(source)
        self.f.calls.append(('apply-mode', source.name, mode))
        self.actual = mode

    def select(self, mode):
        return physics.set_skirt_mode(self.f.context, self.f.source.name, mode,
                                      expected_owner='stable-owner')

    def choice(self):
        return physics._skirt_choice(self.f.source, self.f.record)

    def row(self):
        return physics.status(self.f.context)['dresses'][0]

    def native_calls(self):
        return [call for call in self.f.calls if call[0] == 'apply-mode']

    def finish_original_return_with_hook(self, *args):
        self.original = False
        physics.apply_pending_skirt_modes(self.f.context, self.f.rig, reconcile=True)

    def set_permission(self, key, enabled):
        state = physics._read(self.f.rig)
        state[key] = enabled
        physics._write(self.f.rig, state)

    def test_unset_choice_defaults_automatic_without_writing_or_starting(self):
        before = dict(self.f.source)
        row = self.row()
        self.assertEqual((row['mode'], row['applied_mode']), ('AUTOMATIC', 'MANUAL'))
        self.assertFalse(row['mode_pending'])
        self.assertEqual(dict(self.f.source), before)
        self.assertEqual(self.native_calls(), [])

    def test_existing_wiggle_manual_choice_is_preserved_without_migration(self):
        self.f.source[self.f.wiggle.STATE_KEY] = 'existing persisted backend choice'
        self.assertEqual(self.row()['mode'], 'MANUAL')
        self.assertNotIn(physics.SKIRT_CHOICE, self.f.source)

    def test_existing_cloth_manual_choice_is_preserved(self):
        self.f.record['physics'] = {'backend': 'LEGACY_CAGE', 'proxy': self.f.proxy.name}
        self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
        self.f.modules['skirt_motion_profiles'].read = lambda source, record: {'mode': 'MANUAL'}
        self.assertEqual(self.row()['mode'], 'MANUAL')
        self.assertNotIn(physics.SKIRT_CHOICE, self.f.source)

    def test_original_allows_both_choices_without_native_mutation(self):
        self.original = True
        source_before = dict(self.f.source)
        permission_before = self.f.rig.get(physics.STATE_KEY)
        flags_before = physics._flags(self.f.cloth)
        for mode in ('MANUAL', 'AUTOMATIC'):
            with self.subTest(mode=mode):
                row = self.select(mode)['dresses'][0]
                self.assertEqual((row['mode'], row['applied_mode']), (mode, 'MANUAL'))
                self.assertTrue(row['mode_pending'])
                self.assertTrue(row['can_change_mode'])
                self.assertTrue(row['requires_controls'])
        self.assertEqual(self.native_calls(), [])
        self.assertEqual(physics._flags(self.f.cloth), flags_before)
        self.assertEqual(self.f.rig.get(physics.STATE_KEY), permission_before)
        self.assertEqual({k:v for k,v in self.f.source.items() if k != physics.SKIRT_CHOICE}, source_before)

    def test_master_off_queues_and_on_applies_explicit_choice(self):
        self.set_permission('enabled', False)
        self.select('AUTOMATIC')
        self.assertTrue(self.choice()['pending'])
        self.assertEqual(self.native_calls(), [])
        row = physics.set_enabled(self.f.context, True)['dresses'][0]
        self.assertFalse(row['mode_pending'])
        self.assertEqual(row['applied_mode'], 'AUTOMATIC')
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])

    def test_skirt_off_choice_does_not_change_permission_then_explicit_on_chooses_automatic(self):
        self.set_permission('skirt_enabled', False)
        permission = self.f.rig[physics.STATE_KEY]
        self.select('MANUAL')
        self.assertEqual(self.f.rig[physics.STATE_KEY], permission)
        self.assertEqual(self.native_calls(), [])
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])
        self.assertEqual(self.choice()['mode'], 'AUTOMATIC')
        self.assertFalse(self.choice()['pending'])

    def test_pending_remains_deferred_while_original_even_after_permission_on(self):
        self.original = True
        self.set_permission('enabled', False)
        self.select('AUTOMATIC')
        raw = self.f.source[physics.SKIRT_CHOICE]
        physics.set_enabled(self.f.context, True)
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)
        self.assertEqual(self.native_calls(), [])

    def test_allowed_selection_applies_through_existing_backend(self):
        row = self.select('AUTOMATIC')['dresses'][0]
        self.assertEqual(row['applied_mode'], 'AUTOMATIC')
        self.assertFalse(row['mode_pending'])
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])

    def test_immediate_backend_failure_restores_absent_choice_exactly(self):
        self.f.wiggle.set_mode = lambda *args: (_ for _ in ()).throw(ValueError('backend refused'))
        with self.assertRaisesRegex(ValueError, 'backend refused'):
            self.select('AUTOMATIC')
        self.assertNotIn(physics.SKIRT_CHOICE, self.f.source)
        self.assertEqual(self.actual, 'MANUAL')

    def test_immediate_backend_failure_restores_previous_choice_bytes_exactly(self):
        self.original = True
        self.select('MANUAL')
        raw = self.f.source[physics.SKIRT_CHOICE]
        self.original = False
        self.f.wiggle.set_mode = lambda *args: (_ for _ in ()).throw(ValueError('backend refused'))
        with self.assertRaisesRegex(ValueError, 'backend refused'):
            self.select('AUTOMATIC')
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)

    def test_queued_backend_failure_retains_choice_and_source_error(self):
        self.original = True
        self.select('AUTOMATIC')
        raw = self.f.source[physics.SKIRT_CHOICE]
        self.original = False
        self.f.wiggle.set_mode = lambda *args: (_ for _ in ()).throw(ValueError('backend refused'))
        result = physics.apply_pending_skirt_modes(self.f.context)
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)
        self.assertEqual(result['applied'], [])
        self.assertEqual(result['failed'], [{'source': 'Dress', 'message': 'backend refused'}])
        self.assertIn('backend refused', self.row()['last_error'])

    def test_pending_application_runs_once_and_untouched_default_never_starts(self):
        self.assertEqual(physics.apply_pending_skirt_modes(self.f.context)['applied'], [])
        self.original = True
        self.select('AUTOMATIC')
        self.original = False
        self.assertEqual(physics.apply_pending_skirt_modes(self.f.context)['applied'], ['Dress'])
        self.assertEqual(physics.apply_pending_skirt_modes(self.f.context)['applied'], [])
        self.assertEqual(len(self.native_calls()), 1)

    def test_explicit_skirt_on_activates_truly_unset_automatic_default(self):
        row = physics.set_component(self.f.context, 'SKIRT', True)['dresses'][0]
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])
        self.assertEqual(row['applied_mode'], 'AUTOMATIC')
        self.assertFalse(row['mode_pending'])

    def test_explicit_skirt_on_in_original_queues_unset_default_without_native_write(self):
        self.original = True
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.choice()['mode'], 'AUTOMATIC')
        self.assertTrue(self.choice()['pending'])
        self.assertEqual(self.native_calls(), [])

    def test_explicit_skirt_on_under_master_off_queues_default_until_master_on(self):
        self.set_permission('enabled', False)
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertTrue(self.choice()['pending'])
        self.assertEqual(self.native_calls(), [])
        physics.set_enabled(self.f.context, True)
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])

    def test_explicit_skirt_on_starts_existing_manual_backend_preference(self):
        self.f.source[self.f.wiggle.STATE_KEY] = 'existing persisted backend choice'
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.choice()['mode'], 'AUTOMATIC')
        self.assertFalse(self.choice()['pending'])
        self.assertEqual(self.row()['applied_mode'], 'AUTOMATIC')
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])

    def test_explicit_skirt_on_restarts_persisted_automatic_without_choice_record(self):
        self.actual = 'AUTOMATIC'
        self.f.source[self.f.wiggle.STATE_KEY] = 'existing persisted automatic choice'
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])
        self.assertFalse(self.choice()['pending'])

    def test_explicit_skirt_on_restarts_explicit_automatic_after_preview_stopped(self):
        self.select('AUTOMATIC')
        self.f.calls.clear()
        self.assertFalse(self.choice()['pending'])
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])

    def test_explicit_skirt_on_starts_explicit_manual(self):
        self.select('MANUAL')
        self.f.calls.clear()
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])
        self.assertEqual(self.choice()['mode'], 'AUTOMATIC')
        self.assertFalse(self.choice()['pending'])

    def test_explicit_skirt_on_from_off_replaces_pending_manual(self):
        self.set_permission('skirt_enabled', False)
        self.select('MANUAL')
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])
        self.assertEqual(self.choice()['mode'], 'AUTOMATIC')
        self.assertFalse(self.choice()['pending'])

    def test_explicit_skirt_on_existing_manual_in_original_queues_automatic(self):
        self.original = True
        self.f.source[self.f.wiggle.STATE_KEY] = 'existing persisted backend choice'
        row = physics.set_component(self.f.context, 'SKIRT', True)['dresses'][0]
        self.assertEqual((row['mode'], row['applied_mode']), ('AUTOMATIC', 'MANUAL'))
        self.assertTrue(row['mode_pending'])
        self.assertTrue(row['context_blocked'])
        self.assertEqual(row['last_error'], '')
        self.assertEqual(self.native_calls(), [])

    def test_original_queued_on_can_be_cancelled_by_skirt_off_without_later_start(self):
        self.original = True
        physics.set_component(self.f.context, 'SKIRT', True)
        physics.set_component(self.f.context, 'SKIRT', False)
        self.assertFalse(physics._read(self.f.rig)['skirt_enabled'])
        self.original = False
        result = physics.apply_pending_skirt_modes(self.f.context, reconcile=True)
        self.assertEqual(result['deferred'], ['Dress'])
        self.assertEqual(self.native_calls(), [])
        physics.set_enabled(self.f.context, True)
        self.assertEqual(self.native_calls(), [])
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])

    def test_explicit_skirt_on_manual_setter_failure_keeps_retryable_intent_and_error(self):
        self.select('MANUAL')
        self.f.calls.clear()
        self.f.wiggle.set_mode = lambda *args: (_ for _ in ()).throw(ValueError('native start refused'))
        row = physics.set_component(self.f.context, 'SKIRT', True)['dresses'][0]
        self.assertEqual(self.actual, 'MANUAL')
        self.assertEqual(self.choice()['mode'], 'AUTOMATIC')
        self.assertTrue(self.choice()['pending'])
        self.assertFalse(row['active'])
        self.assertFalse(row['running'])
        self.assertIn('native start refused', row['last_error'])

    def test_explicit_skirt_on_foreign_choice_preserves_exact_bytes(self):
        raw = json.dumps({'version': 1, 'owner': 'foreign', 'mode': 'MANUAL', 'pending': True})
        self.f.source[physics.SKIRT_CHOICE] = raw
        result = physics.apply_pending_skirt_modes(self.f.context, include_unset=True)
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)
        self.assertEqual(self.native_calls(), [])
        self.assertEqual(result['applied'], [])
        self.assertIn('saved Dress mode choice', result['failed'][0]['message'])

    def test_explicit_skirt_on_noneditable_manual_preserves_choice(self):
        self.select('MANUAL')
        raw = self.f.source[physics.SKIRT_CHOICE]
        self.f.calls.clear()
        self.f.source.is_editable = False
        result = physics.apply_pending_skirt_modes(self.f.context, include_unset=True)
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)
        self.assertEqual(self.native_calls(), [])
        self.assertIn('local editable Dress', result['failed'][0]['message'])

    def test_explicit_skirt_checkbox_leaves_running_hair_untouched(self):
        hair = self.f.add_hair()
        self.f.hair_state.update(active=True, source=hair.name,
                                source_pointer=hair.as_pointer(), paused=False)
        hair_before = dict(self.f.hair_state)
        self.select('MANUAL')
        self.f.calls.clear()
        physics.set_component(self.f.context, 'SKIRT', True)
        physics.set_component(self.f.context, 'SKIRT', False)
        self.assertEqual(self.f.hair_state, hair_before)
        self.assertTrue(physics._read(self.f.rig)['hair_enabled'])
        self.assertFalse(any(call[0].startswith('hair') for call in self.f.calls))

    def test_explicit_skirt_on_manual_cloth_and_direct_use_tuning_automatic(self):
        for backend in ('LEGACY_CAGE', 'DIRECT_MAIN_CLOTH_V1'):
            with self.subTest(backend=backend):
                self.f.source.pop(physics.SKIRT_CHOICE, None)
                self.f.record['physics'] = {'backend': backend, 'proxy': self.f.proxy.name}
                self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
                self.actual = 'MANUAL'
                self.f.modules['skirt_motion_profiles'].read = lambda source, record: {'mode': self.actual}
                def apply(context, sources, *, mode):
                    self.f.calls.append(('cloth-mode', sources[0].name, mode))
                    self.actual = mode
                self.f.modules['skirt_motion_tuning'] = SimpleNamespace(apply=apply)
                self.f.calls.clear()
                row = physics.set_component(self.f.context, 'SKIRT', True)['dresses'][0]
                self.assertEqual(self.f.calls, [('cloth-mode', 'Dress', 'AUTOMATIC')])
                self.assertEqual(row['applied_mode'], 'AUTOMATIC')
                self.assertFalse(row['mode_pending'])

    def test_explicit_skirt_on_cloth_failure_retains_pending_and_backend_state(self):
        self.f.record['physics'] = {'backend': 'LEGACY_CAGE', 'proxy': self.f.proxy.name}
        self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
        self.f.modules['skirt_motion_profiles'].read = lambda source, record: {'mode': 'MANUAL'}
        self.f.modules['skirt_motion_tuning'] = SimpleNamespace(
            apply=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('cloth refused')))
        record_before = self.f.source[physics.SKIRT_RECORD]
        flags_before = physics._flags(self.f.cloth)
        row = physics.set_component(self.f.context, 'SKIRT', True)['dresses'][0]
        self.assertEqual(self.f.source[physics.SKIRT_RECORD], record_before)
        self.assertEqual(physics._flags(self.f.cloth), flags_before)
        self.assertEqual((row['mode'], row['applied_mode']), ('AUTOMATIC', 'MANUAL'))
        self.assertTrue(row['mode_pending'])
        self.assertIn('cloth refused', row['last_error'])

    def test_explicit_skirt_on_resumes_same_owned_paused_session_through_backend(self):
        self.actual = 'AUTOMATIC'
        lease = object()
        saved = {'source': self.f.source, 'scene': self.f.context.scene, 'paused': True, 'lease': lease}
        self.f.wiggle._SESSIONS[self.f.source.as_pointer()] = saved
        def resume_existing(context, source, mode):
            self.f.calls.append(('apply-mode', source.name, mode))
            self.f.wiggle.set_paused(context, source, False)
        self.f.wiggle.set_mode = resume_existing
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertIs(self.f.wiggle._SESSIONS[self.f.source.as_pointer()], saved)
        self.assertIs(saved['lease'], lease)
        self.assertFalse(saved['paused'])
        self.assertIn(('wiggle-pause', 'Dress', False), self.f.calls)

    def test_explicit_skirt_on_refuses_foreign_session_without_backend_retry(self):
        foreign = fixture.Object('Foreign Dress', 'MESH')
        saved = {'source': foreign, 'scene': self.f.context.scene, 'paused': False}
        self.f.wiggle._SESSIONS[self.f.source.as_pointer()] = saved
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [])
        self.assertIs(self.f.wiggle._SESSIONS[self.f.source.as_pointer()], saved)
        self.assertIn('Another Dress session', self.row()['last_error'])

    def test_explicit_skirt_on_refuses_foreign_scene_session(self):
        saved = {'source': self.f.source, 'scene': object(), 'paused': True}
        self.f.wiggle._SESSIONS[self.f.source.as_pointer()] = saved
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [])
        self.assertTrue(saved['paused'])
        self.assertIn('Another Dress session', self.row()['last_error'])

    def test_explicit_skirt_on_refuses_changed_owned_session(self):
        saved = {'source': self.f.source, 'scene': self.f.context.scene, 'paused': False}
        self.f.wiggle._SESSIONS[self.f.source.as_pointer()] = saved
        self.f.wiggle._changed = lambda saved: 'ownership changed'
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [])
        self.assertIn('owned Dress preview changed', self.row()['last_error'])

    def test_explicit_skirt_on_refuses_native_recovery_instead_of_cleaning_silently(self):
        self.f.wiggle.preview_state = lambda source: {
            'active': False, 'paused': False, 'running': False,
            'stopped_confirmed': False, 'pending_recovery': True, 'message': ''}
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [])
        self.assertTrue(self.choice()['pending'])
        self.assertIn('cleanup needs review', self.row()['last_error'])

    def test_explicit_skirt_on_refuses_pending_backend_teardown(self):
        self.f.wiggle._TEARDOWN_REASON = 'pending owned teardown'
        physics.set_component(self.f.context, 'SKIRT', True)
        self.assertEqual(self.native_calls(), [])
        self.assertTrue(self.choice()['pending'])
        self.assertIn('cleanup needs review', self.row()['last_error'])

    def test_queued_cloth_mode_dispatches_existing_tuning_transaction(self):
        self.f.record['physics'] = {'backend': 'LEGACY_CAGE', 'proxy': self.f.proxy.name}
        self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
        self.original = True
        self.select('MANUAL')
        self.f.modules['skirt_motion_tuning'] = SimpleNamespace(
            apply=lambda context, sources, *, mode: self.f.calls.append(('cloth-mode', sources[0].name, mode)))
        self.original = False
        result = physics.apply_pending_skirt_modes(self.f.context)
        self.assertEqual(result['applied'], ['Dress'])
        self.assertIn(('cloth-mode', 'Dress', 'MANUAL'), self.f.calls)
        self.assertEqual(self.native_calls(), [])

    def test_strict_original_return_completes_before_pending_application(self):
        self.original = True
        self.select('AUTOMATIC')
        def leave(context, rig):
            self.f.calls.append(('strict-leave', rig.name))
            self.finish_original_return_with_hook(context, rig)
        self.f.modules['body_original_mode'] = SimpleNamespace(active=lambda rig: True, leave=leave)
        self.f.modules['bone_display'] = SimpleNamespace(view_mode=lambda rig: None)
        physics.return_to_controls(self.f.context)
        self.assertLess(self.f.calls.index(('strict-leave', 'Rig')),
                        self.f.calls.index(('apply-mode', 'Dress', 'AUTOMATIC')))

    def test_failed_original_return_does_not_apply_or_clear_pending(self):
        self.original = True
        self.select('AUTOMATIC')
        raw = self.f.source[physics.SKIRT_CHOICE]
        self.f.modules['body_original_mode'] = SimpleNamespace(active=lambda rig: True,
            leave=lambda *args: (_ for _ in ()).throw(ValueError('Original return refused')))
        self.f.modules['bone_display'] = SimpleNamespace()
        with self.assertRaisesRegex(ValueError, 'Original return refused'):
            physics.return_to_controls(self.f.context)
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)
        self.assertEqual(self.native_calls(), [])

    def test_prior_explicit_automatic_reconciles_after_original_suspends_native_to_manual(self):
        self.select('AUTOMATIC')
        self.assertFalse(self.choice()['pending'])
        self.original = True
        self.actual = 'MANUAL'  # Existing Original suspension owns this change.
        self.f.calls.clear()
        self.f.modules['body_original_mode'] = SimpleNamespace(active=lambda rig: True,
            leave=self.finish_original_return_with_hook)
        self.f.modules['bone_display'] = SimpleNamespace()
        row = physics.return_to_controls(self.f.context)['dresses'][0]
        self.assertEqual(self.native_calls(), [('apply-mode', 'Dress', 'AUTOMATIC')])
        self.assertEqual((row['mode'], row['applied_mode']), ('AUTOMATIC', 'AUTOMATIC'))
        self.assertFalse(row['mode_pending'])

    def test_controls_return_never_creates_a_choice_or_starts_untouched_default(self):
        self.original = True
        self.f.modules['body_original_mode'] = SimpleNamespace(active=lambda rig: True,
            leave=self.finish_original_return_with_hook)
        self.f.modules['bone_display'] = SimpleNamespace()
        physics.return_to_controls(self.f.context)
        self.assertNotIn(physics.SKIRT_CHOICE, self.f.source)
        self.assertEqual(self.native_calls(), [])

    def test_matching_explicit_manual_choice_is_unchanged_on_controls_return(self):
        self.select('MANUAL')
        raw = self.f.source[physics.SKIRT_CHOICE]
        self.f.calls.clear()
        physics.apply_pending_skirt_modes(self.f.context, reconcile=True)
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)
        self.assertEqual(self.native_calls(), [])

    def test_reconcile_refusal_keeps_pending_explicit_intent_and_source_error(self):
        self.select('AUTOMATIC')
        self.actual = 'MANUAL'
        self.f.wiggle.set_mode = lambda *args: (_ for _ in ()).throw(ValueError('backend refused'))
        result = physics.apply_pending_skirt_modes(self.f.context, reconcile=True)
        self.assertEqual(result['failed'], [{'source': 'Dress', 'message': 'backend refused'}])
        self.assertTrue(self.choice()['pending'])
        self.assertEqual(self.choice()['mode'], 'AUTOMATIC')

    def test_pending_activation_failure_does_not_undo_successful_original_return(self):
        self.original = True
        self.select('AUTOMATIC')
        self.f.modules['body_original_mode'] = SimpleNamespace(active=lambda rig: True,
            leave=self.finish_original_return_with_hook)
        self.f.modules['bone_display'] = SimpleNamespace()
        def refused_backend(*args):
            self.f.calls.append(('refused-backend',))
            raise ValueError('backend refused')
        self.f.wiggle.set_mode = refused_backend
        physics.return_to_controls(self.f.context)
        self.assertFalse(self.original)
        self.assertTrue(self.choice()['pending'])
        self.assertIn('backend refused', self.row()['last_error'])
        self.assertEqual(self.f.calls.count(('refused-backend',)), 1)

    def test_legacy_return_controls_does_not_supply_its_own_sweep(self):
        self.original = True
        self.select('AUTOMATIC')
        self.f.modules['body_original_mode'] = SimpleNamespace(active=lambda rig: True,
            leave=lambda *args: setattr(self, 'original', False))
        self.f.modules['bone_display'] = SimpleNamespace()
        physics.return_to_controls(self.f.context)
        self.assertFalse(self.original)
        self.assertTrue(self.choice()['pending'])
        self.assertEqual(self.native_calls(), [])

    def test_foreign_owner_choice_refuses_without_overwrite(self):
        raw = json.dumps({'version': 1, 'owner': 'foreign', 'mode': 'MANUAL', 'pending': True})
        self.f.source[physics.SKIRT_CHOICE] = raw
        with self.assertRaisesRegex(ValueError, 'saved Dress mode choice'):
            self.select('AUTOMATIC')
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)
        self.assertEqual(self.native_calls(), [])

    def test_noneditable_source_refuses_preference_write(self):
        self.f.source.is_editable = False
        with self.assertRaisesRegex(ValueError, 'local editable Dress'):
            self.select('AUTOMATIC')
        self.assertNotIn(physics.SKIRT_CHOICE, self.f.source)

    def test_invalid_schema_refuses_and_preserves_original_bytes(self):
        for raw in ('[]', '{}', '{bad', json.dumps({'version': True, 'owner': 'stable-owner', 'mode': 'MANUAL', 'pending': True})):
            with self.subTest(raw=raw):
                self.f.source[physics.SKIRT_CHOICE] = raw
                with self.assertRaises(ValueError):
                    self.select('AUTOMATIC')
                self.assertEqual(self.f.source[physics.SKIRT_CHOICE], raw)

    def test_pending_application_invalid_explicit_rig_reports_without_raising(self):
        invalid = fixture.Object('Other Rig')
        result = physics.apply_pending_skirt_modes(self.f.context, invalid)
        self.assertEqual(result['applied'], [])
        self.assertEqual(result['failed'][0]['source'], None)
        self.assertIn('local Main Rig', physics._last_error(invalid, 'SKIRT'))
        self.assertEqual(physics._last_error(self.f.rig, 'SKIRT'), '')

    def test_pending_application_missing_rig_reports_without_native_mutation(self):
        self.f.context.scene.character_designer_setup.rig = None
        result = physics.apply_pending_skirt_modes(self.f.context)
        self.assertEqual(result['applied'], [])
        self.assertIn('Choose Main Rig', result['failed'][0]['message'])
        self.assertEqual(self.native_calls(), [])


if __name__ == '__main__':
    unittest.main()
