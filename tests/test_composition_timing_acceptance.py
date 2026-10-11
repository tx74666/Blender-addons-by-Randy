"""Independent time oracle/config checks; no native Blender evidence."""
import importlib.util
from pathlib import Path
import unittest

loader = importlib.util.spec_from_file_location('composition_timing_acceptance',
    Path(__file__).parents[1] / 'tools' / 'composition_timing_acceptance.py')
timing = importlib.util.module_from_spec(loader)
loader.loader.exec_module(timing)


def spec(mode='HoldLoop', *, start=.2, end=1.4, speed=1.25, phase=.19, fps=60):
    return {'timeMode': mode, 'sampleStart': start, 'sampleEnd': end, 'editSpeed': speed,
            'editPhase': phase, 'fps': fps, 'referenceSpeed': .8, 'referencePhase': .07}


class TimingOracle(unittest.TestCase):
    def test_loop_uses_B_seconds_speed_and_phase_without_A_or_rest_frame(self):
        source = spec()
        actual = timing.b_clock(source, .6)
        self.assertAlmostEqual(actual['seconds'], .2 + ((.6 * 1.25 + .19 * 1.2) % 1.2))
        self.assertAlmostEqual(1 + actual['seconds'] * source['fps'], 71.68)
        source.update(referenceSpeed=3., referencePhase=.93, fps=30)
        self.assertEqual(timing.b_clock(source, .6), actual)

    def test_nonzero_sample_start_wraps_in_local_interval(self):
        source = spec(start=2., end=3., speed=2., phase=.25)
        actual = timing.b_clock(source, 1.)
        self.assertEqual(actual, {'role': 'B', 'seconds': 2.25, 'cycle': 2})

    def test_raise_boundary_switches_to_actual_Hold_own_phase_rate_speed(self):
        raise_spec = spec('Raise', start=.3, end=1.3, speed=2., phase=.1, fps=60)
        hold = spec('HoldLoop', start=2., end=2.8, speed=.75, phase=.4, fps=30)
        transition = .45
        self.assertEqual(timing.b_clock(raise_spec, transition - 1e-6, following=hold)['role'], 'B')
        at = timing.b_clock(raise_spec, transition, following=hold)
        self.assertEqual(at['role'], 'HOLD')
        self.assertAlmostEqual(at['seconds'], 2.32)
        after = timing.b_clock(raise_spec, transition + .3, following=hold)
        self.assertAlmostEqual(after['seconds'], 2.545)
        self.assertAlmostEqual(1 + after['seconds'] * hold['fps'], 77.35)
        hold['fps'] = 120
        self.assertEqual(timing.b_clock(raise_spec, transition + .3, following=hold), after)

    def test_HoldPose_remains_actual_constant_sample_start_positive_duration(self):
        hold = spec('HoldPose', start=.35, end=1.35, speed=3., phase=.91)
        self.assertEqual(timing.b_clock(hold, 17), {'role': 'B', 'seconds': .35, 'cycle': 0})

    def test_A_uses_actual_Action_rate_independent_B_fps(self):
        source = spec(fps=60)
        actual = timing.a_clock(1., 31., 30., source, .5)
        self.assertAlmostEqual(actual, 15.1)
        source['fps'] = 120
        self.assertEqual(timing.a_clock(1., 31., 30., source, .5), actual)
        self.assertAlmostEqual(timing.a_clock(1., 61., 60., source, .5), 29.2)

    def test_invalid_seek_and_zero_visible_Raise_reject(self):
        for seconds in (-1, True, float('nan'), float('inf')):
            with self.subTest(seconds=seconds), self.assertRaises(AssertionError):
                timing.b_clock(spec(), seconds)
        with self.assertRaisesRegex(AssertionError, 'positive Raise'):
            timing.b_clock(spec('Raise', phase=1.), 0, following=spec('HoldPose'))

    def test_raw_export_clock_keeps_trimmed_duration_without_speed_phase_or_rest_offset(self):
        source = spec(start=.2, end=1.4, speed=1.25, phase=.19)
        inputs = {'effective_values': {'timesSeconds': [.2, .8, 1.4]},
                  'timing': {'frame_start': 13., 'frame_end': 85., 'fps': 60, 'fps_base': 1., 'sample_rate': 60}}
        proof = timing._raw_timing(inputs, source)
        self.assertAlmostEqual(proof['duration'], 1.2)
        self.assertTrue(proof['phase_speed_not_baked'])
        for changed in ({**inputs, 'effective_values': {'timesSeconds': [.2 + 1 / 60, .8, 1.4]}},
                        {**inputs, 'timing': {**inputs['timing'], 'frame_end': 13. + 72 / 1.25}},
                        {**inputs, 'timing': {**inputs['timing'], 'sample_rate': 30}}):
            with self.assertRaisesRegex(AssertionError, 'Raw B export'):
                timing._raw_timing(changed, source)

    def test_fixture_requires_actual_collected_Loop_as_Hold_and_distinct_Raise(self):
        candidate = {'schema': timing.SCHEMA, 'evidenceDirectory': 'D:/isolated',
            'holdLoopWorkItemId': '11111111-1111-4111-8111-111111111111',
            'raiseWorkItemId': '22222222-2222-4222-8222-222222222222',
            'followingHoldWorkItemId': '11111111-1111-4111-8111-111111111111',
            'expectedModelSha256': 'a' * 64, 'poseTolerance': 2e-5,
            'inputTolerance': 1e-6,
            'followingHoldPublication': {'manifestPath': str(Path.cwd() / 'Hold.link.json'),
                'manifestSha256': 'b' * 64, 'revision': 1,
                'fbxPath': str(Path.cwd() / 'Hold.fbx'), 'fbxSha256': 'c' * 64,
                'metadataPath': str(Path.cwd() / 'Hold.json'), 'metadataSha256': 'd' * 64}}
        self.assertIs(timing.validate_config(candidate), candidate)
        for key, value in [('raiseWorkItemId', candidate['holdLoopWorkItemId']),
                           ('followingHoldWorkItemId', '33333333-3333-4333-8333-333333333333'),
                           ('expectedModelSha256', ''), ('poseTolerance', float('nan')),
                           ('inputTolerance', .1),
                           ('followingHoldPublication', {**candidate['followingHoldPublication'], 'revision': 0})]:
            changed = {**candidate, key: value}
            with self.subTest(key=key), self.assertRaises(AssertionError):
                timing.validate_config(changed)

    def test_prepublication_author_config_does_not_require_unprepared_Raise_or_fake_receipt(self):
        candidate = {'schema': timing.AUTHOR_SCHEMA, 'evidenceDirectory': 'D:/isolated',
            'holdLoopWorkItemId': '11111111-1111-4111-8111-111111111111',
            'expectedModelSha256': 'a' * 64, 'holdLoopRotationControl': None}
        self.assertIs(timing.validate_author_config(candidate), candidate)
        for changed in ({**candidate, 'holdLoopRotationControl': ['Arm']},
                        {**candidate, 'followingHoldPublication': {}},
                        {**candidate, 'expectedModelSha256': ''}):
            with self.assertRaises(AssertionError):
                timing.validate_author_config(changed)

    def test_actual_sealed_prepare_clocks_required_without_editing_DTO(self):
        loop, raised = spec(), spec('Raise', speed=1.7, phase=.17)
        timing._sealed_clocks(loop, raised, loop)
        # The parent emits JSON from float32 fields; only serialization noise is accepted.
        timing._sealed_raise({**raised, 'editSpeed': 1.7000000476837158})
        for name, value in [('editSpeed', 1.), ('editPhase', .19),
                            ('referenceSpeed', 1.), ('referencePhase', 0.), ('editSpeed', float('nan'))]:
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, 'sealed Raise clock'):
                timing._sealed_clocks(loop, {**raised, name: value}, loop)
        with self.assertRaisesRegex(AssertionError, 'sealed Loop clock'):
            timing._sealed_clocks({**loop, 'editPhase': 0.}, raised, loop)


if __name__ == '__main__':
    unittest.main()
