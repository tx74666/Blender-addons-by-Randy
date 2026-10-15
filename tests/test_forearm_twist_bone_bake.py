"""Pure input/proof regressions; no Blender bake or geometry claim."""

import ast
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/forearm_twist_bone_bake.py'


def namespace():
    tree = ast.parse(PATH.read_text(encoding='utf-8'))
    scope = {'dataclass': dataclass, 'hashlib': hashlib, 'json': json, 'math': math,
             '__name__': __name__, '__package__': 'bake_pure'}
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PATH), 'exec'), scope)
    return scope


def rna(**values):
    result = SimpleNamespace(**values)
    result.bl_rna = SimpleNamespace(properties=[SimpleNamespace(identifier=name,
        type='STRING' if isinstance(value, str) else 'BOOLEAN' if isinstance(value, bool)
        else 'INT' if isinstance(value, int) else 'FLOAT') for name, value in values.items()])
    return result


def curve(pointer, path='pose.bones["Original"].location'):
    result = rna(data_path=path, array_index=0, mute=False, extrapolation='CONSTANT')
    result.as_pointer = lambda: pointer
    result.group = rna(name='Original channels')
    result.driver = None
    result.keyframe_points = [rna(co=(1.0, .2), handle_left=(.5, .1), handle_right=(1.5, .3), interpolation='BEZIER')]
    result.sampled_points = []
    result.modifiers = [rna(type='GENERATOR', coefficients=(.0, 1.0))]
    return result


class Vector(tuple):
    def normalized(self):
        length = math.hypot(*self)
        return Vector(value / length for value in self)

    def __neg__(self):
        return Vector(-value for value in self)


class Matrix(tuple):
    @classmethod
    def identity(cls):
        return cls(tuple(float(row == column) for column in range(4)) for row in range(4))

    @classmethod
    def Translation(cls, vector):
        values = [list(row) for row in cls.identity()]
        for index, value in enumerate(vector):
            values[index][3] = value
        return cls(tuple(row) for row in values)

    @classmethod
    def LocRotScale(cls, location, quaternion, scale):
        rotation = quaternion.to_matrix()
        return cls(tuple(rotation[row][column] * scale[column] if column < 3
                         else location[row] if row < 3 else 1.0 for column in range(4))
                   if row < 3 else (0., 0., 0., 1.) for row in range(4))

    def __matmul__(self, other):
        return Matrix(tuple(sum(self[row][index] * other[index][column] for index in range(4))
                            for column in range(4)) for row in range(4))

    def inverted(self):
        # All stand-ins below are rigid matrices, including nonidentity Rest.
        rotation = [tuple(self[column][row] for column in range(3)) for row in range(3)]
        rows = tuple(tuple(rotation[row]) + (-sum(rotation[row][index] * self[index][3]
                                                 for index in range(3)),) for row in range(3))
        return Matrix(rows + ((0., 0., 0., 1.),))

    def to_4x4(self):
        return self


class Quaternion(tuple):
    def __new__(cls, values, angle=None):
        if angle is not None:
            axis = Vector(values).normalized()
            values = (math.cos(angle / 2), *(value * math.sin(angle / 2) for value in axis))
        return super().__new__(cls, values)

    def normalized(self):
        return Quaternion(Vector(self).normalized())

    def to_matrix(self):
        w, x, y, z = self.normalized()
        return Matrix(((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), 0.),
                       (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), 0.),
                       (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), 0.), (0., 0., 0., 1.)))


def native_id(pointer, **values):
    return SimpleNamespace(session_uid=pointer + 1000, as_pointer=lambda: pointer, **values)


def sample_modules():
    mathutils = ModuleType('mathutils')
    mathutils.Matrix, mathutils.Quaternion, mathutils.Vector = Matrix, Quaternion, Vector
    backend = ModuleType('bake_pure.forearm_twist_bone_preview')
    backend.MAX_ANGLE = math.radians(120)
    def difference(actual, expected, *, tolerance):
        maximum = max(abs(left-right) for row1, row2 in zip(actual, expected) for left, right in zip(row1, row2))
        return {'within_tolerance': math.isfinite(maximum) and maximum <= tolerance, 'max_abs': maximum}
    backend.matrix_difference = difference
    twist_math = ModuleType('bake_pure.forearm_twist_math')
    twist_math.TwistMathError = ValueError
    def twist_angle(lower, hand, axis):
        # Test poses rotate around Z; reading relative evaluated input is what
        # these freshness regressions exercise, rather than twist algebra.
        relative = lower.inverted() @ hand
        return math.atan2(relative[1][0], relative[0][0])
    twist_math.twist_angle = twist_angle
    package = ModuleType('bake_pure')
    package.forearm_twist_bone_preview = backend
    return {'mathutils': mathutils, 'bake_pure': package,
            'bake_pure.forearm_twist_bone_preview': backend,
            'bake_pure.forearm_twist_math': twist_math}


class BakePureTests(unittest.TestCase):
    def setUp(self):
        self.scope = namespace()

    def samples(self):
        sample = self.scope['HelperSample']
        return tuple(sample(frame, (('Helper', (frame / 10, 0.0, 0.0),
                                     (1.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),))
                     for frame in range(1, 31))

    def test_exact_integer_domain_produces_helper_only_ten_trs_channels(self):
        result = dict(self.scope['compile_channels'](self.samples(), ('Helper',)))
        self.assertEqual(len(result), 10)
        self.assertTrue(all(key[0] == 'Helper' for key in result))
        self.assertEqual(result[('Helper', 'location', 0)], tuple(frame / 10 for frame in range(1, 31)))
        self.assertTrue(all(len(values) == 30 for values in result.values()))

    def test_missing_duplicate_subframe_and_bool_frames_are_rejected(self):
        samples = self.samples()
        cases = [samples[:-1], (replace(samples[0], frame=1.5),) + samples[1:],
                 (replace(samples[0], frame=True),) + samples[1:],
                 samples[:-1] + (replace(samples[-1], frame=29),)]
        for values in cases:
            with self.subTest(frames=tuple(item.frame for item in values)):
                with self.assertRaises(self.scope['BoneBakeError']):
                    self.scope['compile_channels'](values, ('Helper',))

    def test_mapping_or_nonfinite_components_cannot_create_curves(self):
        samples = self.samples()
        for row in [('Other', (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)),
                    ('Helper', (math.nan, 0, 0), (1, 0, 0, 0), (1, 1, 1)),
                    ('Helper', (0, 0, 0), (0, 0, 0, 0), (1, 1, 1))]:
            with self.assertRaises(self.scope['BoneBakeError']):
                self.scope['compile_channels']((replace(samples[0], rows=(row,)),) + samples[1:], ('Helper',))

    def test_quaternion_sign_changes_are_unwrapped_without_changing_other_values(self):
        samples = tuple(replace(item, rows=(('Helper', item.rows[0][1],
                                             ((-1.0 if item.frame % 2 == 0 else 1.0), 0, 0, 0),
                                             item.rows[0][3]),)) for item in self.samples())
        result = dict(self.scope['compile_channels'](samples, ('Helper',)))
        self.assertEqual(result[('Helper', 'rotation_quaternion', 0)], (1.0,) * 30)
        self.assertEqual(result[('Helper', 'location', 0)], tuple(frame / 10 for frame in range(1, 31)))

    def action(self):
        first = curve(10)
        bag = SimpleNamespace(slot_handle=929201142, fcurves=[first])
        strip = rna(type='KEYFRAME')
        strip.channelbags = [bag]
        layer = rna(name='Original layer')
        layer.strips = [strip]
        action = rna(name='Private copied action', use_fake_user=False)
        action.session_uid, action.as_pointer = 123, lambda: 20
        action.slots = [rna(handle=929201142, identifier='OBCoshaRig')]
        action.layers = [layer]
        return action, bag, first

    def test_added_curve_exclusion_keeps_all_existing_action_and_slot_proof(self):
        action, bag, original = self.action()
        before = self.scope['_action_state'](action)
        added = curve(30, 'pose.bones["Helper"].location')
        bag.fcurves.append(added)
        self.assertEqual(self.scope['_action_state'](action, [30]), before)
        original.keyframe_points[0].handle_left = (.5, 100.0)
        self.assertNotEqual(self.scope['_action_state'](action, [30]), before)

    def test_existing_modifier_and_slot_changes_are_detected(self):
        action, bag, original = self.action()
        before = self.scope['_action_state'](action)
        original.modifiers[0].coefficients = (1.0, 1.0)
        self.assertNotEqual(self.scope['_action_state'](action), before)
        original.modifiers[0].coefficients = (.0, 1.0)
        action.slots[0].handle = 10
        self.assertNotEqual(self.scope['_action_state'](action), before)

    def test_original_bone_proof_excludes_only_declared_helper_names(self):
        def pose(name):
            return SimpleNamespace(name=name, rotation_mode='QUATERNION', location=(0., 0., 0.),
                rotation_quaternion=(1., 0., 0., 0.), rotation_euler=(0., 0., 0.),
                rotation_axis_angle=(0., 0., 1., 0.), scale=(1., 1., 1.),
                matrix_basis=((1., 0.), (0., 1.)))
        original, helper = pose('Original'), pose('Helper')
        rig = SimpleNamespace(pose=SimpleNamespace(bones=[original, helper]))
        before = self.scope['_old_bone_channels'](rig, ('Helper',))
        helper.location = (2., 0., 0.)
        self.assertEqual(self.scope['_old_bone_channels'](rig, ('Helper',)), before)
        original.location = (2., 0., 0.)
        self.assertNotEqual(self.scope['_old_bone_channels'](rig, ('Helper',)), before)

    def sampler(self):
        base = self.scope['HelperBakeSampler']
        class Sampler(base):
            def _guard(self):
                pass
        sampler = Sampler.__new__(Sampler)
        sampler._samples, sampler._last_report, sampler._helper_names = {}, None, ('Helper',)
        sampler.last_sampling_verification = None
        sampler.scene = native_id(10, frame_current=1, frame_subframe=0.0)
        helper = SimpleNamespace(key=('L', 'lower', .5), source_bone='lower', fraction=.5,
                                 axis=(0., 0., 1.), pivot=(0., 0., 0.))
        pose = SimpleNamespace(location=(0., 0., 0.), rotation_quaternion=(1., 0., 0., 0.),
                               scale=(1., 1., 1.), matrix_basis=Matrix.identity())
        bones = {name: SimpleNamespace(matrix_local=Matrix.identity()) for name in ('lower', 'hand')}
        evaluated = native_id(30, pose=SimpleNamespace(bones={name: SimpleNamespace(matrix=Matrix.identity())
                                                             for name in ('lower', 'hand', 'Helper')}))
        sampler.rig = native_id(20, data=SimpleNamespace(bones=bones),
                                pose=SimpleNamespace(bones={'Helper': pose}), evaluated_get=lambda graph: evaluated)
        evaluated.original = sampler.rig
        side = SimpleNamespace(name='L', lower='lower', hand='hand', axis=helper.axis, enabled=True)
        sampler.preview = SimpleNamespace(last_verification={'completed': True, 'within_tolerance': True},
            _verification_pending=False, _desired_deformations={helper.key: Matrix.identity()},
            _rest={helper.key: Matrix.identity()}, helper_names={helper.key: 'Helper'},
            plan=SimpleNamespace(sides=(side,), helpers=(helper,)), _helper=lambda item: (None, pose))
        graph = SimpleNamespace(scene=sampler.scene,
                                scene_eval=SimpleNamespace(original=sampler.scene, frame_current=1, frame_subframe=0.))
        return sampler, graph, evaluated, pose, helper

    def test_sampling_rejects_stale_verification_and_mismatched_scene_time(self):
        sampler, graph, evaluated, pose, helper = self.sampler()
        with patch.dict(sys.modules, sample_modules()):
            sampler.collect(1, graph)
            sampler.scene.frame_current = graph.scene_eval.frame_current = 2
            with self.assertRaisesRegex(self.scope['BoneBakeError'], 'fresh completed'):
                sampler.collect(2, graph)
            sampler.preview.last_verification = {'completed': True, 'within_tolerance': True}
            sampler.scene.frame_subframe = .5
            with self.assertRaisesRegex(self.scope['BoneBakeError'], 'without subframes'):
                sampler.collect(2, graph)
            sampler.scene.frame_subframe = 0.0
            self.assertEqual(sampler.collect(2, graph).frame, 2)

    def test_first_collect_cannot_use_previous_frame_receipt_after_seek(self):
        sampler, graph, evaluated, pose, helper = self.sampler()
        sampler.scene.frame_current = graph.scene_eval.frame_current = 2
        evaluated.pose.bones['hand'].matrix = Quaternion((0., 0., 1.), .6).to_matrix()
        with patch.dict(sys.modules, sample_modules()):
            with self.assertRaisesRegex(self.scope['BoneBakeError'], 'Verified source pose became stale'):
                sampler.collect(2, graph)
        self.assertFalse(sampler._samples)
        self.assertIsNone(sampler._last_report)

    def test_same_frame_helper_edit_is_rejected_before_sample_publication(self):
        for field, value, message in [('matrix_basis', Matrix.Translation((.1, 0., 0.)), 'basis changed'),
                                      ('location', (.1, 0., 0.), 'sampled helper TRS differs')]:
            sampler, graph, evaluated, pose, helper = self.sampler()
            setattr(pose, field, value)
            with self.subTest(field=field), patch.dict(sys.modules, sample_modules()):
                with self.assertRaisesRegex(self.scope['BoneBakeError'], message):
                    sampler.collect(1, graph)
                self.assertFalse(sampler._samples)

    def test_stale_evaluated_helper_is_rejected_even_if_current_basis_and_trs_match(self):
        sampler, graph, evaluated, pose, helper = self.sampler()
        evaluated.pose.bones['Helper'].matrix = Matrix.Translation((.1, 0., 0.))
        with patch.dict(sys.modules, sample_modules()):
            with self.assertRaisesRegex(self.scope['BoneBakeError'], 'evaluated helper became stale'):
                sampler.collect(1, graph)
        self.assertFalse(sampler._samples)

    def test_scene_identity_and_evaluated_frame_are_authenticated(self):
        for mutation, message in [(lambda graph: setattr(graph, 'scene', native_id(99)), 'isolated scene'),
                                  (lambda graph: setattr(graph.scene_eval, 'frame_current', 2), 'has not reached')]:
            sampler, graph, evaluated, pose, helper = self.sampler()
            mutation(graph)
            with self.subTest(message=message), patch.dict(sys.modules, sample_modules()):
                with self.assertRaisesRegex(self.scope['BoneBakeError'], message):
                    sampler.collect(1, graph)
                self.assertFalse(sampler._samples)

    def test_nonzero_pose_and_nonidentity_rest_collect_current_required_channels(self):
        sampler, graph, evaluated, pose, helper = self.sampler()
        angle = .6
        lower = Matrix.Translation((.2, -.1, .3))
        evaluated.pose.bones['lower'].matrix = lower
        evaluated.pose.bones['hand'].matrix = lower @ Quaternion((0., 0., 1.), angle).to_matrix()
        desired = lower @ Quaternion((0., 0., 1.), angle * .5).to_matrix()
        rest = Quaternion((0., 0., 1.), .2).to_matrix()
        sampler.preview._rest[helper.key] = rest
        basis = rest.inverted() @ desired @ rest
        pose.matrix_basis = basis
        pose.location = tuple(basis[row][3] for row in range(3))
        pose.rotation_quaternion = Quaternion((0., 0., 1.), angle * .5)
        sampler.preview._desired_deformations[helper.key] = desired
        evaluated.pose.bones['Helper'].matrix = desired @ rest
        with patch.dict(sys.modules, sample_modules()):
            sample = sampler.collect(1, graph)
        self.assertEqual(sample.rows[0][1], pose.location)
        self.assertEqual(sample.rows[0][2], pose.rotation_quaternion)
        self.assertTrue(sampler.last_sampling_verification['completed'])
        self.assertLess(sampler.last_sampling_verification['max_abs'], 2e-6)


if __name__ == '__main__':
    unittest.main()
