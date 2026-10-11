"""Pure packet/driver mapping guards for owned static Dress delegation."""

import copy
import importlib.util
import math
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'cd_unity_animation_dress_test', ROOT / 'addons/character_designer/unity_animation_dress.py')
dress = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dress
SPEC.loader.exec_module(dress)


def flatten(matrix):
    return [value for row in matrix for value in row]


def translate(x=0., y=0., z=0.):
    return [[1., 0., 0., x], [0., 1., 0., y], [0., 0., 1., z], [0., 0., 0., 1.]]


def packet():
    # Arbitrary artist bone names prove the gate is not a prefix exception.
    rests = [translate(), translate(.2, .3), translate(.2, .4)]
    parent_motion = [[0., -1., 0., 2.], [1., 0., 0., 3.], [0., 0., 1., 4.], [0., 0., 0., 1.]]
    return {'bones': [{'name': name, 'parent': parent, 'rest': flatten(rest)}
                      for name, parent, rest in zip(('hip', '裙腰', 'cloth joint'), (-1, 0, 1), rests)],
            'frames': [{'poses': [{'matrix': flatten(rest)} for rest in rests]},
                       {'poses': [{'matrix': flatten(dress._multiply(parent_motion, rest))} for rest in rests]}]}


class StaticDressPacketTests(unittest.TestCase):
    def test_parent_motion_includes_rotation_translation_and_scale(self):
        data = packet()
        self.assertLess(dress.static_packet_proof(data, {'裙腰', 'cloth joint'}), 1.e-12)
        # Uniform parent scale and transport are also inherited, not Dress keys.
        world = [[2., 0., 0., 1.], [0., 2., 0., 2.], [0., 0., 2., 3.], [0., 0., 0., 1.]]
        data['frames'].append({'poses': [{'matrix': flatten(dress._multiply(world, dress._matrix(bone['rest'])))}
                                        for bone in data['bones']]})
        self.assertLess(dress.static_packet_proof(data, {'裙腰', 'cloth joint'}), 1.e-12)

    def test_last_sample_independent_animation_rejected(self):
        data = packet()
        data['frames'][-1]['poses'][2]['matrix'][3] += .01
        with self.assertRaisesRegex(dress.DressAnimationError, 'sample 1'):
            dress.static_packet_proof(data, {'裙腰', 'cloth joint'})

    def test_constant_non_rest_offset_is_not_discarded(self):
        data = packet()
        for frame in data['frames']:
            frame['poses'][2]['matrix'][11] += .02
        with self.assertRaises(dress.DressAnimationError):
            dress.static_packet_proof(data, {'cloth joint'})

    def test_incomplete_or_ambiguous_inventory_rejected(self):
        for change in ('missing_name', 'duplicate_name', 'missing_sample', 'invalid_parent'):
            data = packet()
            if change == 'missing_name':
                data['bones'][2]['name'] = 'renamed'
            elif change == 'duplicate_name':
                data['bones'][2]['name'] = 'hip'
            elif change == 'missing_sample':
                data['frames'][-1]['poses'].pop()
            else:
                data['bones'][2]['parent'] = 2
            with self.subTest(change=change), self.assertRaises(dress.DressAnimationError):
                dress.static_packet_proof(data, {'cloth joint'})

    def test_non_finite_and_singular_parent_rejected(self):
        for change in ('non_finite', 'singular'):
            data = packet()
            if change == 'non_finite':
                data['frames'][-1]['poses'][2]['matrix'][4] = math.nan
            else:
                data['frames'][-1]['poses'][1]['matrix'] = [0.] * 16
            with self.subTest(change=change), self.assertRaises(dress.DressAnimationError):
                dress.static_packet_proof(data, {'cloth joint'})

    def test_package_required_only_for_delegated_names(self):
        self.assertEqual(dress.static_packet_proof(None, set()), 0.)
        with self.assertRaises(dress.DressAnimationError):
            dress.static_packet_proof(None, {'cloth joint'})

    def test_serialization_noise_is_bounded(self):
        data = packet()
        data['frames'][0]['poses'][2]['matrix'][11] += 3.e-6
        self.assertAlmostEqual(dress.static_packet_proof(data, {'cloth joint'}), 3.e-6)
        data['frames'][0]['poses'][2]['matrix'][11] += 1.e-3
        with self.assertRaises(dress.DressAnimationError):
            dress.static_packet_proof(data, {'cloth joint'})


def driver_curve():
    points = [NS(co=(0., 0.), interpolation='BEZIER', handle_left=(-1./3, -1./3),
                 handle_right=(1./3, 1./3)),
              NS(co=(1., 1.), interpolation='BEZIER', handle_left=(2./3, 2./3),
                 handle_right=(4./3, 4./3))]
    return NS(array_index=0, mute=False, modifiers=[], extrapolation='LINEAR', keyframe_points=points)


class IdentityDriverCurveTests(unittest.TestCase):
    def test_verified_identity_bezier_and_linear_curves(self):
        curve = driver_curve()
        dress._identity_driver_curve(curve)
        for point in curve.keyframe_points:
            point.interpolation = 'LINEAR'
        dress._identity_driver_curve(curve)
        curve.keyframe_points.clear()
        dress._identity_driver_curve(curve)

    def test_foreign_curve_evaluation_is_rejected(self):
        for change in ('modifier', 'mute', 'index', 'keys', 'extrapolation', 'handle', 'value', 'interpolation'):
            curve = copy.deepcopy(driver_curve())
            if change == 'modifier':
                curve.modifiers = [NS(type='NOISE')]
            elif change == 'mute':
                curve.mute = True
            elif change == 'index':
                curve.array_index = 1
            elif change == 'keys':
                curve.keyframe_points.pop()
            elif change == 'extrapolation':
                curve.extrapolation = 'CONSTANT'
            elif change == 'handle':
                curve.keyframe_points[0].handle_right = (.3, .4)
            elif change == 'value':
                curve.keyframe_points[-1].co = (1., .8)
            else:
                curve.keyframe_points[0].interpolation = 'CONSTANT'
            with self.subTest(change=change), self.assertRaises(dress.DressAnimationError):
                dress._identity_driver_curve(curve)


if __name__ == '__main__':
    unittest.main()
