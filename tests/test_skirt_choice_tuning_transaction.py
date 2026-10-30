"""Choice intent participates in existing native mode/selection rollback."""
import copy
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import test_animation_physics as choice_fixture
import test_animation_physics_ui as layout_fixture
import test_skirt_direct_control_selection as selection


choices = choice_fixture.physics


class TuningChoiceTransactionTests(unittest.TestCase):
    def setUp(self):
        errors = patch.dict(choices._LAST_ERRORS, clear=True)
        errors.start()
        self.addCleanup(errors.stop)

    def prior_choice(self, fixture, mode='AUTOMATIC'):
        raw = json.dumps({'version': 1, 'owner': fixture.record['owner'], 'mode': mode, 'pending': False}, indent=3)
        fixture.source[choices.SKIRT_CHOICE] = raw
        return raw

    def test_successful_direct_control_selection_updates_latest_user_intent(self):
        f = selection.SelectionFixture()
        self.prior_choice(f)
        self.assertEqual(f.run(), {'FINISHED'})
        choice = json.loads(f.source[choices.SKIRT_CHOICE])
        self.assertEqual((choice['mode'], choice['pending']), ('MANUAL', False))
        self.assertEqual(selection.PROFILES.read(f.source, f.record)['mode'], 'MANUAL')
        self.assertEqual(f.state()['cache'][1:], (True, b'retained owned cache'))

    def test_selection_failure_restores_previous_choice_bytes_with_native_state(self):
        f = selection.SelectionFixture()
        raw = self.prior_choice(f)
        before = f.state()
        f.fail_selection = True
        self.assertEqual(f.run(), {'CANCELLED'})
        self.assertEqual(f.state(), before)
        self.assertEqual(f.source[choices.SKIRT_CHOICE], raw)

    def test_choice_write_failure_rolls_back_mode_and_does_not_start_selection(self):
        f = selection.SelectionFixture()
        self.prior_choice(f)
        before = f.state()
        with patch.object(choices, '_write_skirt_choice', side_effect=RuntimeError('intent write refused')):
            self.assertEqual(f.run(), {'CANCELLED'})
        self.assertEqual(f.state(), before)
        self.assertFalse(any(call[0] in {'select', 'restore-context'} for call in f.calls))
        self.assertEqual(f.calls.count(('restore-mode',)), 1)

    def test_foreign_choice_is_refused_before_native_mode_or_selection_writes(self):
        f = selection.SelectionFixture()
        f.source[choices.SKIRT_CHOICE] = json.dumps({'version': 1, 'owner': 'foreign', 'mode': 'AUTOMATIC', 'pending': False})
        before = f.state()
        self.assertEqual(f.run(), {'CANCELLED'})
        self.assertEqual(f.state(), before)
        self.assertFalse(any(call[0] in {'select', 'restore-mode', 'restore-context'} for call in f.calls))

    def test_material_only_or_no_mode_apply_does_not_rewrite_choice(self):
        f = selection.SelectionFixture()
        raw = self.prior_choice(f, 'MANUAL')
        with patch.object(choices, '_write_skirt_choice', side_effect=AssertionError('No mode was requested')):
            f.tuning['apply'](f.context, (f.source,))
        self.assertEqual(f.source[choices.SKIRT_CHOICE], raw)

    def batch(self):
        first, second = selection.SelectionFixture(), selection.SelectionFixture()
        second.source.name = 'Second Dress'
        endpoints = {'first': first, 'second': second}
        for name, fixture in endpoints.items():
            fixture.record['physics']['fixture_id'] = name
            fixture.source['record'] = json.dumps(fixture.record, indent=1)
        skirt = NS(**first.skirt)
        skirt.physics_control = lambda source: (source['rig'], source['rig'], 'influence')
        physics = NS(**vars(first.physics))
        physics._surface_module = lambda record: endpoints[record['physics']['fixture_id']].service
        physics.validate_physics = lambda source: next(f for f in endpoints.values() if f.source is source).physics_validate(source)
        first.tuning['_modules'] = lambda: (physics, skirt)
        return first, second

    def test_second_source_intent_failure_restores_entire_native_batch_and_both_choices(self):
        first, second = self.batch()
        self.prior_choice(first)
        self.prior_choice(second)
        before = first.state(), second.state()
        write = choices._write_skirt_choice
        def write_choice(source, choice):
            if source is second.source:
                raise RuntimeError('second intent write refused')
            write(source, choice)
        with patch.object(choices, '_write_skirt_choice', side_effect=write_choice):
            with self.assertRaisesRegex(RuntimeError, 'second intent write refused'):
                first.tuning['apply'](first.context, (first.source, second.source), mode='MANUAL')
        self.assertEqual((first.state(), second.state()), before)
        self.assertEqual(first.calls.count(('restore-mode',)), 2)

    def test_second_source_foreign_intent_refuses_whole_batch_before_first_write(self):
        first, second = self.batch()
        self.prior_choice(first)
        second.source[choices.SKIRT_CHOICE] = json.dumps({'version': 1, 'owner': 'foreign', 'mode': 'MANUAL', 'pending': False})
        before = first.state(), second.state()
        with patch.object(choices, '_write_skirt_choice') as write:
            with self.assertRaises(ValueError):
                first.tuning['apply'](first.context, (first.source, second.source), mode='MANUAL')
        write.assert_not_called()
        self.assertEqual((first.state(), second.state()), before)
        self.assertFalse(any(call[0] == 'restore-mode' for call in first.calls))

    def test_successful_mode_clears_only_proved_source_error_not_generic_or_other_owner(self):
        f = selection.SelectionFixture()
        unrelated = selection.Object('Unrelated selected character')
        own_key = choices._error_key(f.rig, 'SKIRT', f.source)
        other_key = choices._error_key(unrelated, 'SKIRT', f.source)
        generic_key = choices._error_key(f.rig, 'SKIRT')
        permission_key = choices._error_key(f.rig, 'ALL')
        choices._LAST_ERRORS.update({own_key: 'previous activation refused', other_key: 'other character error',
                                     generic_key: 'unrelated generic guard', permission_key: 'permission error'})
        with patch.object(choices, '_owner', return_value=f.rig):
            f.tuning['apply'](f.context, (f.source,), mode='MANUAL')
        self.assertNotIn(own_key, choices._LAST_ERRORS)
        self.assertEqual(choices._LAST_ERRORS[other_key], 'other character error')
        self.assertEqual(choices._LAST_ERRORS[generic_key], 'unrelated generic guard')
        self.assertEqual(choices._LAST_ERRORS[permission_key], 'permission error')

    def test_batch_failure_keeps_both_prior_source_activation_errors(self):
        first, second = self.batch()
        keys = [choices._error_key(f.rig, 'SKIRT', f.source) for f in (first, second)]
        choices._LAST_ERRORS.update({keys[0]: 'first previous activation error', keys[1]: 'second previous activation error'})
        errors_before = dict(choices._LAST_ERRORS)
        write = choices._write_skirt_choice
        def write_choice(source, choice):
            if source is second.source:
                raise RuntimeError('second intent refused')
            write(source, choice)
        with patch.object(choices, '_owner', side_effect=lambda source, component: source['rig']), \
             patch.object(choices, '_write_skirt_choice', side_effect=write_choice):
            with self.assertRaisesRegex(RuntimeError, 'second intent refused'):
                first.tuning['apply'](first.context, (first.source, second.source), mode='MANUAL')
        self.assertEqual(choices._LAST_ERRORS, errors_before)

    def test_late_selection_failure_keeps_activation_error_until_whole_action_succeeds(self):
        f = selection.SelectionFixture()
        key = choices._error_key(f.rig, 'SKIRT', f.source)
        choices._LAST_ERRORS[key] = 'previous activation refused'
        f.fail_selection = True
        with patch.object(choices, '_owner', return_value=f.rig):
            self.assertEqual(f.run(), {'CANCELLED'})
        self.assertEqual(choices._LAST_ERRORS[key], 'previous activation refused')
        f.fail_selection = False
        with patch.object(choices, '_owner', return_value=f.rig):
            self.assertEqual(f.run(), {'FINISHED'})
        self.assertNotIn(key, choices._LAST_ERRORS)

    def test_unproven_source_error_owner_is_preserved_after_success(self):
        f = selection.SelectionFixture()
        key = choices._error_key(f.rig, 'SKIRT', f.source)
        choices._LAST_ERRORS[key] = 'unproven source activation error'
        with patch.object(choices, '_owner', return_value=None):
            f.tuning['apply'](f.context, (f.source,), mode='MANUAL')
        self.assertEqual(choices._LAST_ERRORS[key], 'unproven source activation error')

    def draw(self, fixture):
        scope = selection.load('skirt_motion_ui.py', {'_metadata_mode', 'draw_motion'}, {
            'json': json, '__package__': 'choice_draw_fixture',
            'importlib': NS(import_module=lambda name: choices),
            '_profiles': lambda: selection.PROFILES,
            '_rig': lambda: NS(RIG_KEY='rig'),
            '_skirt': lambda: NS(_idle=lambda context: True),
            '_metadata_controls': lambda source: True,
        })
        layout = layout_fixture.Layout()
        scope['draw_motion'](layout, fixture.context, fixture.source, fixture.record)
        return layout.events

    def test_motion_ui_buttons_show_desired_while_bake_keeps_native_readiness(self):
        f = selection.SelectionFixture()
        self.prior_choice(f, 'MANUAL')
        events = self.draw(f)
        self.assertTrue(next(event for event in events if event[1] == 'Manual')[3]['depress'])
        self.assertFalse(next(event for event in events if event[1] == 'Automatic')[3]['depress'])
        self.assertTrue(next(event for event in events if event[1] == 'Bake')[2])
        profile = selection.PROFILES.read(f.source, f.record)
        selection.PROFILES.write(f.source, selection.PROFILES.edited(profile, f.record, mode='MANUAL'), f.record)
        self.prior_choice(f, 'AUTOMATIC')
        events = self.draw(f)
        self.assertTrue(next(event for event in events if event[1] == 'Automatic')[3]['depress'])
        self.assertFalse(next(event for event in events if event[1] == 'Bake')[2])

    def test_invalid_choice_draw_shows_error_without_mutating_native_or_intent(self):
        f = selection.SelectionFixture()
        f.source[choices.SKIRT_CHOICE] = 'invalid choice JSON'
        before = f.state()
        events = self.draw(f)
        self.assertIn('Saved motion settings need review.', [event[1] for event in events])
        self.assertFalse(any(event[0] == 'character_designer.dress_motion_mode' for event in events))
        self.assertEqual(f.state(), before)


if __name__ == '__main__':
    unittest.main()
