"""Pure contracts for edit-key selection; native motion gates remain separate."""
import copy
import importlib.util
from pathlib import Path
import struct
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
PACKAGE = '_controller_sampling_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package


def load(name):
    spec = importlib.util.spec_from_file_location(PACKAGE + '.' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sampling = load('controller_animation_sampling')
curves = load('controller_animation_curves')


def native(value):
    return struct.unpack('f', struct.pack('f', value))[0]


class ControllerSamplingTest(unittest.TestCase):
    def test_native_near_integer_pairs_keep_actual_integer_not_moved_times(self):
        times = (1., native(19.999998), 20., 21., native(21.000002),
                 native(25.999998), 26., 27., native(27.000002), 30., native(30.000002))
        before = tuple(times)
        result = sampling.legal_indices(times)
        self.assertEqual(tuple(times[index] for index in result), (1., 20., 21., 26., 27., 30.))
        self.assertEqual(times, before)
        self.assertEqual(times[-1], native(30.000002))

    def test_no_invented_integer_and_no_single_key_clip(self):
        for times in ((13., native(14.004), 15.), (native(1.0001), 2.),
                      (30., native(30.000002))):
            with self.subTest(times=times), self.assertRaises(ValueError):
                sampling.legal_indices(times)

    def test_native_gap_must_exceed_point_zero_one(self):
        first = .25
        below = native(first + .01)
        above = native(first + .010002)
        self.assertLessEqual(below - first, .01)
        self.assertGreater(above - first, .01)
        times = (0., first, below, above, 1.)
        result = sampling.legal_indices(times)
        self.assertNotIn(2, result)
        self.assertIn(3, result)
        self.assertTrue(all(times[b] - times[a] > .01 for a, b in zip(result, result[1:])))

    def test_non_native_alias_prefers_exact_integer_without_changing_V(self):
        times = (1., 2., 2. + 2.**-25, 3.)
        self.assertNotEqual(native(times[2]), times[2])
        self.assertEqual(sampling.legal_indices(times), (0, 1, 3))
        self.assertEqual(times[2], 2. + 2.**-25)

    def test_non_native_distinct_solver_samples_keep_their_original_values(self):
        times = (18.,18.249999046325684,18.499998092651367,
                 18.624998569488525,18.749999046325684,18.874999523162842,19.)
        key = ('pose.bones["CTRL_foot_IK.R"].location',2)
        values = [0.,1.,0.,2.,3.,2.,0.]
        observed = {}
        def compress(data, actual_times):
            observed.update(times=actual_times,values=data[key])
            return {'indices':{key:tuple(range(len(actual_times)))}}
        result = sampling.compress_legal({key:values},times,types.SimpleNamespace(compress=compress))
        self.assertEqual(result['legal_indices'],tuple(range(len(times))))
        self.assertEqual(observed['times'],tuple(native(t) for t in times))
        self.assertEqual(observed['values'],tuple(values))
        self.assertEqual(result['indices'][key],tuple(range(len(times))))
        self.assertEqual(times[1],18.249999046325684)

    def test_native_endpoint_quantization_has_bounded_constant_coverage(self):
        times = (1.,2.,2.1)
        self.assertEqual(sampling.legal_indices(times),(0,1,2))
        self.assertLess(native(times[-1]),times[-1])
        self.assertLess(times[-1]-native(times[-1]),.01)

    def test_native_alias_can_use_existing_solve_but_never_invents_value(self):
        times = (13.,14.+2.**-25,15.)
        self.assertEqual(native(times[1]),14.)
        self.assertEqual(sampling.legal_indices(times),(0,1,2))

    def test_mapping_preserves_values_axes_and_full_V_for_validation(self):
        times = (1., 2., 3., 4., 5., native(5.000002))
        path = 'pose.bones["CTRL_hand_IK.L"].location'
        data = {(path, axis): [value * (axis + 1) for value in times] for axis in range(3)}
        before = copy.deepcopy(data)
        result = sampling.compress_legal(data, times, curves)
        self.assertEqual(result['legal_indices'], (0, 1, 2, 3, 4))
        self.assertEqual(result['indices'][path, 0], result['indices'][path, 1])
        self.assertEqual(result['indices'][path, 1], result['indices'][path, 2])
        self.assertEqual(result['fallback_indices'], {key: (0, 1, 2, 3, 4) for key in data})
        self.assertEqual(result['validation_sample_count'], 6)
        self.assertEqual(result['before_points'], 18)
        self.assertTrue(result['native_verification_required'])
        self.assertEqual(data, before)

    def test_incomplete_axis_angle_and_scalar_groups_cannot_restore_unsafe_V(self):
        times = (1., 2., 3., 4., native(4.000002))
        data = {('pose.bones["x"].location', 1): [1., 2., 3., 4., 4.],
                **{('pose.bones["x"].rotation_axis_angle', i): [float(i)] * 5 for i in range(4)},
                ('pose.bones["x"]["ik_fk"]', 0): [1.] * 5,
                ('unknown_numeric', 0): [0., 1., 0., 1., 1.]}
        result = sampling.compress_legal(data, times, curves)
        self.assertEqual(set(result['indices']), set(data))
        self.assertTrue(all(4 not in indices and len(indices) >= 2
                            for indices in result['indices'].values()))
        self.assertTrue(all(indices == (0, 1, 2, 3) for indices in result['fallback_indices'].values()))

    def test_unknown_reducer_indices_fail_closed(self):
        key = ('x', 0)
        for proposed in ({}, {key: (0,)}, {key: (0, 0, 2)}, {key: (0, 3)},
                         {key: (0, True, 2)}, {key: (1, 2)}):
            module = types.SimpleNamespace(compress=lambda *_args, value=proposed: {'indices': value})
            with self.subTest(proposed=proposed), self.assertRaises(ValueError):
                sampling.compress_legal({key: [0., 1., 2.]}, (1., 2., 3.), module)

    def test_bad_bounds_and_data_refuse_without_mutating_inputs(self):
        for times in ((), (1.,), (1., 1.), (2., 1.), (False, 1.),
                      (1., float('inf')), (1., 1.e100), tuple(range(sampling.MAX_TIMES + 1))):
            with self.subTest(count=len(times)), self.assertRaises(ValueError):
                sampling.legal_indices(times)
        for data in ({}, {('x', True): [0., 1.]}, {('x', 0): [0.]},
                     {('x', 0): [0., float('nan')]}, {('x', 0): [False, 1.]}):
            with self.subTest(keys=list(data)), self.assertRaises(ValueError):
                sampling.compress_legal(data, (1., 2.), curves)
        data = {('x', i): [0., 0.] for i in range(3)}
        before = copy.deepcopy(data)
        old_limit = sampling.MAX_POINTS
        try:
            sampling.MAX_POINTS = 5
            with self.assertRaises(ValueError):
                sampling.compress_legal(data, (1., 2.), curves)
        finally:
            sampling.MAX_POINTS = old_limit
        self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main()
