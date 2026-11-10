"""Closed legal-key endpoint coverage; clip clock and legacy guards stay exact."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/controller_animation_move.py'
spec = importlib.util.spec_from_file_location('_move_legal_endpoint_tests', PATH)
move = importlib.util.module_from_spec(spec)
spec.loader.exec_module(move)


def policy(start=1., end=30.000001907348633, first=1., last=30.):
    return dict(schema='character-designer.editable-key-grid/1',
        minimum_native_gap_exclusive=.01, integer_edit_zone=.01,
        validation_range=[start, end], key_range=[first, last],
        endpoint_extension='CONSTANT; at most .01 frame; original matched endpoints verified',
        verification='all original samples and quarter probes retained')


def curve(first=1., last=30., extrapolation='CONSTANT'):
    points = [NS(co=[time, value], handle_left=[time-.1, value],
        handle_right=[time+.1, value], interpolation='LINEAR')
        for time, value in ((first, .1), (last, .2))]
    return NS(modifiers=[], sampled_points=[], mute=False, lock=False,
        group=None, is_driver=False, keyframe_points=points, extrapolation=extrapolation)


class MoveLegalEndpointTests(unittest.TestCase):
    def test_legacy_exact_coverage_is_unchanged_without_new_policy(self):
        self.assertIsNone(move._editable_key_policy({}, 1., 30.))
        value = curve(extrapolation='LINEAR')
        before = copy.deepcopy(vars(value))
        move._curve_ready(value, location=True, start=1., end=30.)
        self.assertEqual(vars(value), before)
        with self.assertRaisesRegex(ValueError, 'complete controller clip'):
            move._curve_ready(value, location=True, start=1., end=30.000001907348633)

    def test_closed_policy_certifies_native_integer_constant_extension(self):
        record = {'editable_key_policy': policy()}
        before = copy.deepcopy(record)
        approved = move._editable_key_policy(record, 1., 30.000001907348633)
        value = curve(); original = copy.deepcopy(vars(value))
        move._curve_ready(value, location=True, start=1., end=30.000001907348633, key_policy=approved)
        self.assertEqual(record, before)
        self.assertEqual(vars(value), original)

    def test_an_exact_fractional_other_endpoint_keeps_legacy_coverage(self):
        start, end = 1.5, 30.000001907348633
        approved = move._editable_key_policy({'editable_key_policy': policy(start=start, first=start)}, start, end)
        move._curve_ready(curve(first=start), location=True, start=start, end=end, key_policy=approved)

    def test_unknown_missing_extra_or_changed_policy_refuses(self):
        for field, value in (('schema', 'unknown'), ('minimum_native_gap_exclusive', .011),
                             ('integer_edit_zone', True), ('endpoint_extension', 'LINEAR'),
                             ('verification', 'not verified')):
            proposal = policy(); proposal[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                move._editable_key_policy({'editable_key_policy': proposal}, 1., proposal['validation_range'][1])
        for proposal in (None, {}, dict(policy(), extra=True)):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                move._editable_key_policy({'editable_key_policy': proposal}, 1., 30.000001907348633)
        proposal = policy(); del proposal['verification']
        with self.assertRaises(ValueError):
            move._editable_key_policy({'editable_key_policy': proposal}, 1., 30.000001907348633)

    def test_policy_bounds_are_exact_finite_and_inside_original_clock(self):
        for key, values in (('validation_range', [1., 30.]), ('key_range', [1., 31.]),
                            ('key_range', [1., 29.98]), ('key_range', [1., 1.]),
                            ('key_range', [True, 30.]), ('key_range', [1., float('nan')]),
                            ('validation_range', (1., 30.000001907348633))):
            proposal = policy(); proposal[key] = values
            with self.subTest(key=key, values=values), self.assertRaises(ValueError):
                move._editable_key_policy({'editable_key_policy': proposal}, 1., 30.000001907348633)

    def test_nonconstant_extrapolation_and_wrong_actual_bounds_refuse(self):
        approved = move._editable_key_policy({'editable_key_policy': policy()}, 1., 30.000001907348633)
        for value in (curve(extrapolation='LINEAR'), curve(last=29.), curve(first=2.)):
            with self.subTest(value=vars(value)), self.assertRaisesRegex(ValueError, 'complete controller clip'):
                move._curve_ready(value, location=True, start=1., end=30.000001907348633, key_policy=approved)

    def test_changed_noninteger_endpoint_is_not_a_rounding_exemption(self):
        start, end, last = 1., 30.250001907348633, 30.25
        approved = move._editable_key_policy({'editable_key_policy': policy(end=end, last=last)}, start, end)
        with self.assertRaisesRegex(ValueError, 'complete controller clip'):
            move._curve_ready(curve(last=last), location=True, start=start, end=end, key_policy=approved)

    def test_exact_point_zero_one_distance_and_crossed_integer_refuse(self):
        for end, last in ((30.01, 30.), (30.999, 30.99)):
            proposal = policy(end=end, last=last)
            with self.subTest(end=end), self.assertRaises(ValueError):
                approved = move._editable_key_policy({'editable_key_policy': proposal}, 1., end)
                move._curve_ready(curve(last=last), location=True, start=1., end=end, key_policy=approved)

    def test_existing_locks_and_mode_value_guards_still_apply(self):
        approved = move._editable_key_policy({'editable_key_policy': policy()}, 1., 30.000001907348633)
        value = curve(); value.lock = True
        with self.assertRaisesRegex(ValueError, 'Unlock'):
            move._curve_ready(value, location=True, start=1., end=30.000001907348633, key_policy=approved)
        with self.assertRaisesRegex(ValueError, 'stay in IK'):
            move._curve_ready(curve(), key_policy=approved)


class MovePrepareEndpointTests(unittest.TestCase):
    def setUp(self):
        fixture_path = Path(__file__).with_name('test_controller_animation_move.py')
        fixture_spec = importlib.util.spec_from_file_location('_existing_move_fixture_legal', fixture_path)
        self.existing = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(self.existing)
        self.fixture = self.existing.MoveTests('test_zero_delta_refuses_without_guessing_AutoKey_previous_value')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.end = 10.000001907348633
        self.fixture.action['controller_record'] = json.dumps(dict(frame_start=1., frame_end=self.end,
            limbs=['ARM.L'], editable_key_policy=policy(end=self.end, last=10.)))
        for value in self.fixture.curves[:3]:
            value.extrapolation = 'CONSTANT'

    def test_prepare_reads_the_closed_policy_without_creating_an_Action(self):
        self.fixture.action.frame_range = (1., self.end)
        plan = self.existing.move.prepare(self.fixture.context)
        self.assertEqual(plan['frame_end'], self.end)
        self.fixture.offset.new_variant_action.assert_not_called()

    def test_legal_key_policy_does_not_bypass_Action_frame_range(self):
        self.assertEqual(self.fixture.action.frame_range, (1., 10.))
        with self.assertRaisesRegex(ValueError, 'unchanged complete controller clip'):
            self.existing.move.prepare(self.fixture.context)
        self.fixture.offset.new_variant_action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
