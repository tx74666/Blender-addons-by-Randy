"""Analytic, exact-rational and bounded-work checks; no bpy/mathutils import."""
from bisect import bisect_right
from copy import deepcopy
from fractions import Fraction
import importlib.util
import math
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_key_reduce_math.py'
spec = importlib.util.spec_from_file_location('_animation_key_reduce_math_tested', SOURCE)
kernel = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = kernel
spec.loader.exec_module(kernel)


class ReductionMathTests(unittest.TestCase):
    def check_bound(self, times, values, result):
        """Verify the reported bound independently using exact binary rationals."""
        self.assertEqual(result.indices, tuple(sorted(set(result.indices))))
        if not times:
            self.assertEqual(result.indices, ())
            return
        self.assertEqual((result.indices[0], result.indices[-1]), (0, len(times) - 1))
        squared_bound = Fraction(result.max_error) ** 2
        for first, last in zip(result.indices, result.indices[1:]):
            for index in range(first + 1, last):
                alpha = ((Fraction(times[index]) - Fraction(times[first])) /
                         (Fraction(times[last]) - Fraction(times[first])))
                squared = sum((Fraction(actual) - (Fraction(left) +
                    (Fraction(right) - Fraction(left)) * alpha)) ** 2
                    for actual, left, right in zip(values[index], values[first], values[last]))
                self.assertLessEqual(squared, squared_bound)

    def test_empty_single_and_pair_keep_original_indices(self):
        for times, values in (([], []), ([.125], [(2., 3.)]),
                              ([.125, .5], [(2., 3.), (4., 5.)])):
            with self.subTest(times=times):
                result = kernel.reduce_linear(times, values, .1)
                self.assertEqual(result.indices, tuple(range(len(times))))
                self.assertEqual((result.max_error, result.work), (0., 0))

    def test_exact_linear_subframes_reduce_to_endpoints(self):
        times = [-.5, -.25, 0., .125, .25, .375, .5]
        values = [(t, 2*t + 1, -4*t) for t in times]
        result = kernel.reduce_linear(times, values, 1e-10)
        self.assertEqual(result.indices, (0, 6))
        self.assertLessEqual(result.max_error, 1e-10)
        self.check_bound(times, values, result)

    def test_peak_is_retained_when_tolerance_is_smaller(self):
        self.assertEqual(kernel.reduce_linear([0., 1., 2.], [(0.,), (1.,), (0.,)], .9).indices,
                         (0, 1, 2))

    def test_analytic_peak_error_is_bounded_and_reported(self):
        times, values = [0., 1., 2.], [(0.,), (1.,), (0.,)]
        result = kernel.reduce_linear(times, values, 1.1)
        self.assertEqual(result.indices, (0, 2))
        self.assertGreaterEqual(result.max_error, 1.)
        self.assertLessEqual(result.max_error, 1.1)
        self.check_bound(times, values, result)

    def test_vector_tolerance_is_euclidean_not_per_component(self):
        values = [(0., 0.), (.8, .8), (0., 0.)]
        self.assertEqual(kernel.reduce_linear([0., 1., 2.], values, 1.).indices, (0, 1, 2))

    def test_tie_splits_at_earliest_original_key(self):
        times, values = list(map(float, range(5))), [(0.,), (1.,), (0.,), (1.,), (0.,)]
        result = kernel.reduce_linear(times, values, .9)
        self.assertEqual(result.indices, (0, 1, 4))
        self.assertEqual(result, kernel.reduce_linear(times, values, .9))
        self.check_bound(times, values, result)

    def test_zero_tolerance_preserves_every_key(self):
        result = kernel.reduce_linear([0., .5, 1.], [(0.,), (.5,), (1.,)], 0.)
        self.assertEqual(result.indices, (0, 1, 2))
        self.assertEqual((result.max_error, result.work, result.reason), (0., 0, 'zero_tolerance'))

    def test_boundary_tolerance_does_not_understate_error(self):
        times, values = [0., 1., 2.], [(0.,), (1.,), (0.,)]
        result = kernel.reduce_linear(times, values, 1.)
        self.assertLessEqual(result.max_error, 1.)
        self.check_bound(times, values, result)

    def test_raw_nonunit_quaternion_turn_is_not_sign_flipped_or_normalized(self):
        times = [0., 1., 2.]
        values = [(2., 0., 0., 0.), (0., 0., 0., 2.), (-2., 0., 0., 0.)]
        before = deepcopy((times, values))
        result = kernel.reduce_linear(times, values, .01)
        self.assertEqual(result.indices, (0, 1, 2))
        self.assertEqual((times, values), before)

    def test_group_dimensions_one_three_and_four(self):
        for dimension in (1, 3, 4):
            with self.subTest(dimension=dimension):
                times = [0., .25, .5, 1.]
                values = [tuple((axis + 1)*t for axis in range(dimension)) for t in times]
                result = kernel.reduce_linear(times, values, 1e-9)
                self.assertEqual(result.indices, (0, 3))
                self.check_bound(times, values, result)

    def test_final_certificate_covers_irregular_times_and_dense_between_knots(self):
        rng = random.Random(371)
        times = [i*i / 64 for i in range(33)]
        values = [(t + rng.uniform(-.01, .01), 2*t + rng.uniform(-.01, .01),
                   -.5*t + rng.uniform(-.01, .01)) for t in times]
        result = kernel.reduce_linear(times, values, .06)
        self.assertLess(len(result.indices), len(times))
        self.check_bound(times, values, result)
        retained_times = [times[i] for i in result.indices]
        def interpolate(clock, rows, t):
            left = max(0, min(len(clock) - 2, bisect_right(clock, t) - 1))
            alpha = (t - clock[left]) / (clock[left + 1] - clock[left])
            return tuple((1 - alpha)*a + alpha*b for a, b in zip(rows[left], rows[left + 1]))
        for i in range(1001):
            t = times[-1] * i / 1000
            original = interpolate(times, values, t)
            rebuilt = interpolate(retained_times, [values[k] for k in result.indices], t)
            self.assertLessEqual(math.dist(original, rebuilt), result.max_error + 1e-13)

    def test_error_certificate_with_large_offset_and_small_variation(self):
        times = [0., .125, .5, .875, 1.]
        values = [(1e12 + t, -1e12 + t*.5) for t in times]
        result = kernel.reduce_linear(times, values, .1)
        self.assertLess(len(result.indices), len(times))
        self.check_bound(times, values, result)

    def test_work_cap_returns_all_original_keys(self):
        times = list(map(float, range(1000)))
        values = [(float(i % 2),) for i in range(1000)]
        result = kernel.reduce_linear(times, values, .01, max_work=23)
        self.assertEqual(result.indices, tuple(range(1000)))
        self.assertEqual((result.max_error, result.work, result.budget_exhausted, result.reason),
                         (0., 23, True, 'work_limit'))

    def test_work_cap_also_covers_final_reconstruction(self):
        result = kernel.reduce_linear(list(map(float, range(10))), [(float(i),) for i in range(10)],
                                      .1, max_work=8)
        self.assertEqual(result.indices, tuple(range(10)))
        self.assertEqual((result.work, result.budget_exhausted, result.reason), (8, True, 'work_limit'))

    def test_zero_work_is_safe_and_does_not_mutate_input(self):
        times, values = [0., 1., 2.], [[0.], [1.], [0.]]
        before = deepcopy((times, values))
        result = kernel.reduce_linear(times, values, .1, max_work=0)
        self.assertEqual(result.indices, (0, 1, 2))
        self.assertEqual((result.work, result.budget_exhausted), (0, True))
        self.assertEqual((times, values), before)

    def test_finite_time_span_overflow_falls_back(self):
        result = kernel.reduce_linear([-1e308, 0., 1e308], [(0.,), (1.,), (2.,)], .1)
        self.assertEqual((result.indices, result.max_error, result.reason), ((0, 1, 2), 0., 'numeric_guard'))

    def test_finite_value_interval_overflow_falls_back(self):
        result = kernel.reduce_linear([0., 1., 2.], [(-1e308,), (0.,), (1e308,)], .1)
        self.assertEqual((result.indices, result.max_error, result.reason), ((0, 1, 2), 0., 'numeric_guard'))

    def test_validation_rejects_bad_clocks_vectors_and_tolerances(self):
        cases = [([0., 0.], [(0.,), (1.,)], .1), ([1., 0.], [(0.,), (1.,)], .1),
            ([0., math.inf], [(0.,), (1.,)], .1), ([False, 1.], [(0.,), (1.,)], .1),
            ([0.], [(math.nan,)], .1), ([0.], [(True,)], .1), ([0.], [()], .1),
            ([0., 1.], [(0.,), (1., 2.)], .1), ([0.], [], .1), ([0.], ['1'], .1),
            ([0.], [(0.,)], -.1), ([0.], [(0.,)], math.nan), ([0.], [(0.,)], True),
            ('01', [(0.,), (1.,)], .1), ([0., 1.], [(0.,), (1.,)], '1')]
        for times, values, tolerance in cases:
            with self.subTest(times=times, values=values, tolerance=tolerance):
                with self.assertRaises(ValueError):
                    kernel.reduce_linear(times, values, tolerance)

    def test_key_dimension_and_work_caps_are_checked(self):
        with patch.object(kernel, 'MAX_KEYS', 2):
            with self.assertRaises(ValueError):
                kernel.reduce_linear([0., 1., 2.], [(0.,), (1.,), (2.,)], .1)
        with self.assertRaises(ValueError):
            kernel.reduce_linear([0.], [tuple(range(kernel.MAX_COMPONENTS + 1))], .1)
        for budget in (-1, True, 1.5, kernel.MAX_WORK + 1):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    kernel.reduce_linear([0.], [(0.,)], .1, max_work=budget)


if __name__ == '__main__':
    unittest.main()
