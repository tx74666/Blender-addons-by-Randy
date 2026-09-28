"""Blender integration checks for rest-aware opposite-side Pose application."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Euler, Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import control_pose_assets as poses, control_pose_mirror as mirror
from character_designer import body_setup
import test_control_pose_weights_blender as workflow
import test_body_calibration_blender as setup


def action_state(action, rig):
    return [(curve.data_path, curve.array_index, [tuple(point.co) for point in curve.keyframe_points])
            for curve in poses._curves(action, rig)]


def native_flipped_reference(rig, action):
    """Evaluate native flip with the Euler mode used to author this fixture.

    workflow.asset restores the fixture's original QUATERNION modes after
    creating Euler curves. Native Action.flip_with_pose and direct evaluation
    respect the active mode, so those curves otherwise are silently ignored.
    The production service explicitly interprets the asset's representation.
    """
    before = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in rig.pose.bones}
    reference = action.copy()
    try:
        for pb in rig.pose.bones:
            pb.rotation_mode = 'XYZ'
            pb.matrix_basis = before[pb.name][1]
        workflow.update(rig)
        reference.flip_with_pose(rig)
        return workflow.original_reference(rig, reference)
    finally:
        bpy.data.actions.remove(reference)
        for name, (mode, basis) in before.items():
            rig.pose.bones[name].rotation_mode = mode
            rig.pose.bones[name].matrix_basis = basis
        workflow.update(rig)


class MirroredPose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        self.rig, self.mesh = setup.fixture()
        setup.prepare(self.rig)

    def assertMatrices(self, expected, tolerance=4e-4):
        workflow.update(self.rig)
        errors = {name: poses._difference(matrix, self.rig.pose.bones[name].matrix)
                  for name, matrix in expected.items()}
        worst = max(errors, key=errors.get)
        self.assertLess(errors[worst], tolerance, (worst, errors[worst]))

    def test_native_flip_reference_and_only_opposite_limb_changes(self):
        rig = self.rig
        action, _ = workflow.asset(rig, {'upper_arm.L': (.08, -.1, .13),
                                        'forearm.L': (.2, .0, .0), 'hand.L': (.05, .02, .0)})
        original = action_state(action, rig)
        expected = native_flipped_reference(rig, action)
        body_setup.generate(bpy.context, rig)
        source = {name: rig.pose.bones[name].matrix.copy()
                  for name in ('upper_arm.L', 'forearm.L', 'hand.L', 'Hips')}
        actions = set(bpy.data.actions.keys())
        values = poses.channels(action, rig)
        unchanged = copy.deepcopy(values)
        flipped = mirror.mirrored_channels(rig, values)
        self.assertEqual(set(flipped), {'upper_arm.R', 'forearm.R', 'hand.R'})
        self.assertEqual(values, unchanged)
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        self.assertEqual(action_state(action, rig), original)
        poses.apply_channels(bpy.context, rig, flipped)
        self.assertMatrices(expected)
        self.assertMatrices(source, 2e-6)

    def test_rotation_order_conversion_and_asymmetric_rest_roll(self):
        rig = self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['hand.R'].roll += .43
        bpy.ops.object.mode_set(mode='OBJECT')
        poses.capture_baseline(rig)
        left, right = rig.pose.bones['hand.L'], rig.pose.bones['hand.R']
        left.rotation_mode = 'ZXY'
        right.rotation_mode = 'YZX'
        values = {'hand.L': {'rotation_euler': dict(enumerate((.21, -.12, .33)))}}
        flipped = mirror.mirrored_channels(rig, values)
        # Direct Rest-space reflection, independently of the scratch Action.
        reflection = Matrix.Diagonal((-1., 1., 1., 1.))
        source = rig.data.bones['hand.L'].matrix_local @ Euler((.21, -.12, .33), 'ZXY').to_matrix().to_4x4()
        basis = rig.data.bones['hand.R'].matrix_local.inverted() @ reflection @ source @ reflection
        actual = mirror._quaternion(flipped['hand.R']['rotation_quaternion'], 'QUATERNION').to_matrix()
        self.assertLess(poses._difference(actual, basis.to_quaternion().to_matrix()), 3e-6)
        self.assertEqual(right.rotation_mode, 'YZX')

    def test_partial_location_scale_and_bbone_preserve_unkeyed_fields(self):
        rig = self.rig
        poses.capture_baseline(rig)
        values = {'hand.L': {'location': {0: .07}, 'scale': {1: 1.13},
                            'bbone_curveinx': {0: .04}, 'bbone_scaleout': {2: .9}}}
        flipped = mirror.mirrored_channels(rig, values)
        self.assertEqual(set(flipped), {'hand.R'})
        self.assertEqual(set(flipped['hand.R']['location']), {0})
        self.assertEqual(set(flipped['hand.R']['scale']), {1})
        self.assertNotIn('rotation_quaternion', flipped['hand.R'])
        self.assertEqual(flipped['hand.R']['bbone_curveinx'], {0: .04})
        self.assertEqual(flipped['hand.R']['bbone_scaleout'], {2: .9})
        self.assertEqual(mirror.mirrored_channels(rig, {'hand.L': {'bbone_rollin': {0: .2}}}),
                         {'hand.R': {'bbone_rollin': {0: .2}}})

    def test_partial_transform_values_match_native_flip_with_asymmetric_rest(self):
        rig = self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['hand.R'].roll += .27
        bpy.ops.object.mode_set(mode='OBJECT')
        poses.capture_baseline(rig)
        pb = rig.pose.bones['hand.L']
        pb.rotation_mode = 'ZYX'
        pb.rotation_euler = (.17, -.21, .12)
        pb.scale = (1.03, 1.07, .98)
        workflow.update(rig)
        action = bpy.data.actions.new('Sparse native flip reference')
        try:
            slot = action.slots.new(id_type='OBJECT', name=rig.name)
            layer = action.layers.new(name='Sparse')
            bag = layer.strips.new(type='KEYFRAME').channelbag(slot, ensure=True)
            for prop, index, value in (('location', 0, .07), ('scale', 1, 1.13)):
                bag.fcurves.new(data_path=pb.path_from_id(prop), index=index).keyframe_points.insert(1, value)
            original = poses.channels(action, rig)
            action.flip_with_pose(rig)
            native = poses.channels(action, rig)
            mirrored = mirror.mirrored_channels(rig, original)
            self.assertEqual(set(mirrored['hand.R']), {'location', 'scale'})
            for prop, entries in native['hand.R'].items():
                for index, value in entries.items():
                    self.assertAlmostEqual(mirrored['hand.R'][prop][index], value, places=5)
        finally:
            bpy.data.actions.remove(action)

    def test_partial_rotation_uses_evaluated_generated_controls(self):
        rig = self.rig
        body_setup.generate(bpy.context, rig)
        poses.apply_channels(bpy.context, rig,
                             {'hand.L': {'rotation_euler': dict(enumerate((.12, -.18, .23)))}})
        before = workflow.matrices(rig)
        pb = rig.pose.bones['hand.L']
        bone = pb.bone
        parent_matrix = rig.pose.bones[bone.parent.name].matrix
        effective = poses._convert(bone, pb.matrix, parent_matrix, invert=True)
        rotation = effective.to_quaternion().to_euler('XYZ')
        rotation.x = .07
        full = {'hand.L': {'rotation_euler': dict(enumerate(rotation))}}
        expected = mirror.mirrored_channels(rig, full)
        actual = mirror.mirrored_channels(rig, {'hand.L': {'rotation_euler': {0: .07}}})
        expected_rotation = mirror._quaternion(expected['hand.R']['rotation_quaternion'], 'QUATERNION')
        actual_rotation = mirror._quaternion(actual['hand.R']['rotation_quaternion'], 'QUATERNION')
        self.assertLess(expected_rotation.rotation_difference(actual_rotation).angle, 1e-5)
        self.assertMatrices(before, 1e-7)

    def test_center_bone_matches_blender_native_flip(self):
        rig = self.rig
        action, _ = workflow.asset(rig, {'Hips': (.12, -.04, .08)})
        poses.capture_baseline(rig)
        expected = native_flipped_reference(rig, action)
        actual = poses.desired_pose(rig, mirror.mirrored_channels(rig, poses.channels(action, rig)))
        for name in expected:
            self.assertLess(poses._difference(expected[name], actual[name]), 4e-6, name)

    def test_mirror_failure_cleans_temporary_action_and_preserves_rig(self):
        rig = self.rig
        poses.capture_baseline(rig)
        before = workflow.matrices(rig)
        actions = set(bpy.data.actions.keys())
        with patch.object(poses, 'channels', side_effect=ValueError('injected after native flip')):
            with self.assertRaisesRegex(ValueError, 'injected'):
                mirror.mirrored_channels(rig, {'hand.L': {'rotation_euler': {0: .1}}})
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        self.assertMatrices(before, 1e-7)

    def test_missing_opposite_bone_refuses_before_creating_action(self):
        rig = self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['hand.R'].name = 'Other hand'
        bpy.ops.object.mode_set(mode='OBJECT')
        poses.capture_baseline(rig)
        before = workflow.matrices(rig)
        actions = set(bpy.data.actions.keys())
        with self.assertRaisesRegex(ValueError, 'no opposite native bone'):
            mirror.mirrored_channels(rig, {'hand.L': {'rotation_euler': {0: .1}}})
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        self.assertMatrices(before, 1e-7)

    def test_mirrored_auto_key_and_transaction_rollback(self):
        rig = self.rig
        action, _ = workflow.asset(rig, {'hand.L': (.12, .01, .03)})
        body_setup.generate(bpy.context, rig)
        flipped = mirror.mirrored_channels(rig, poses.channels(action, rig))
        before = workflow.matrices(rig)
        bases = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
        actions = set(bpy.data.actions.keys())
        with patch.object(poses, '_auto_key', side_effect=ValueError('injected mirror key failure')):
            with self.assertRaisesRegex(ValueError, 'injected mirror'):
                poses.apply_channels(bpy.context, rig, flipped)
        self.assertMatrices(before, 2e-6)
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        for name, basis in bases.items():
            self.assertLess(poses._difference(basis, rig.pose.bones[name].matrix_basis), 2e-6)
        original = action_state(action, rig)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        poses.apply_channels(bpy.context, rig, flipped)
        result = workflow.matrices(rig)
        self.assertIsNotNone(rig.animation_data.action)
        self.assertNotEqual(rig.animation_data.action, action)
        self.assertEqual(action_state(action, rig), original)
        bpy.context.scene.frame_set(2)
        self.assertMatrices(result)


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise RuntimeError('Mirrored Pose integration tests failed')
