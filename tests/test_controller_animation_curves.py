"""Pure checks for motion preservation in solved controller reduction."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
PACKAGE = '_controller_curve_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + '.controller_animation_curves',
                                            ROOT / 'controller_animation_curves.py')
curves = importlib.util.module_from_spec(spec)
spec.loader.exec_module(curves)


class ControllerCurvesTest(unittest.TestCase):
    def test_ik_native_angle_seam_holds_only_when_linear_interpolation_would_take_the_long_arc(self):
        for first, last, expected in ((3.13, -3.12, 'CONSTANT'), (-3.13, 3.12, 'CONSTANT'),
                                      (-.02, .01, 'LINEAR'), (2.8, 3.1, 'LINEAR')):
            with self.subTest(first=first, last=last):
                self.assertEqual(curves.pole_angle_interpolation(first, last), expected)

    def test_large_signed_ik_angle_changes_insert_short_arc_editable_guards(self):
        import math
        cases = ((1., 5., 145., -135.), (1., 2., -170., 140.),
                 (10., 11., 179.99, -177.), (-2., 2., -140., 140.))
        for a, b, u, v in cases:
            with self.subTest(a=a, b=b, u=u, v=v):
                u, v = math.radians(u), math.radians(v)
                keys = curves.pole_angle_keys(((a, u), (b, v)))
                self.assertEqual(keys[0][:2], (a, u))
                self.assertEqual(keys[-1][:2], (b, v))
                self.assertTrue(all(y[0] - x[0] > .01 for x, y in zip(keys, keys[1:])))
                self.assertTrue(all(t == round(t) or abs(t-round(t)) > .01 for t, _, _ in keys))
                hold, = [(x, y) for x, y in zip(keys, keys[1:]) if x[2] == 'CONSTANT']
                self.assertLess(hold[1][0] - hold[0][0], .032)
                step = math.remainder(v - u, math.tau)
                for i in range(1001):
                    t = a + (b - a) * i/1000
                    left, right = next((x,y) for x,y in zip(keys,keys[1:]) if x[0] <= t <= y[0])
                    real = (left[1] if left[2] == 'CONSTANT' and t < right[0] else
                            left[1]+(right[1]-left[1])*(t-left[0])/(right[0]-left[0]))
                    expected = u + step * (t-a)/(b-a)
                    self.assertLess(abs(math.remainder(real-expected, math.tau)), math.radians(.5))

    def test_corner_in_one_axis_preserves_whole_target(self):
        path = 'pose.bones["hand\\\".L"].location'
        values = [0., 0., 0., 1., 0., 0., 0., 0., 0.]
        data = {(path, 0): list(range(9)), (path, 1): values, (path, 2): [0.] * 9}
        before = {key: list(value) for key, value in data.items()}
        result = curves.compress(data, list(range(9)))
        axes = [result['indices'][path, i] for i in range(3)]
        self.assertEqual(axes[0], axes[1])
        self.assertEqual(axes[0], axes[2])
        self.assertTrue({0, 1, 2, 3, 4, 7, 8} <= set(axes[0]))
        self.assertEqual(data, before)
        self.assertTrue(result['native_verification_required'])

    def test_linear_motion_reduces_and_keeps_seam(self):
        path = 'pose.bones["CTRL_hand_IK.L"].location'
        data = {(path, axis): [float(i * (axis + 1)) for i in range(31)] for axis in range(3)}
        result = curves.compress(data, list(range(31)))
        self.assertEqual(result['indices'][path, 0], (0, 1, 29, 30))
        self.assertLess(result['after_points'], result['before_points'])

    def test_constant_mode_does_not_create_single_frame_takeover(self):
        key = ('pose.bones["CTRL_hand_IK.L"]["ik_fk"]', 0)
        self.assertEqual(curves.compress({key: [1.] * 31}, list(range(31)))['indices'][key], (0, 30))
        pulse = [0.] * 31
        pulse[14] = 1.
        self.assertEqual(curves.compress({key: pulse}, list(range(31)))['indices'][key], tuple(range(31)))

    def test_quaternion_values_are_not_normalized_or_sign_flipped(self):
        path = 'pose.bones["CTRL_hand_IK.L"].rotation_quaternion'
        data = {(path, axis): ([2., 2., -2., -2., -2.] if axis == 0 else [0.] * 5) for axis in range(4)}
        before = {key: list(value) for key, value in data.items()}
        result = curves.compress(data, list(range(5)))
        self.assertTrue({1, 2} <= set(result['indices'][path, 0]))
        self.assertEqual(data, before)

    def test_bad_samples_refuse(self):
        key = ('pose.bones["hand"].location', 0)
        for times, values in (([1., 1.], [0., 0.]), ([1., 2.], [0., float('nan')]),
                              ([1., 2.], [0.]), ([1., 2.], [True, 0.])):
            with self.assertRaises(ValueError):
                curves.compress({key: values}, times)


if __name__ == '__main__':
    unittest.main()
