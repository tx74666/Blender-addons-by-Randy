"""Real native recovery owners remain intact across intentional Original edits.

Run only in an isolated Blender --background --factory-startup process.
"""
import hashlib
import json
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (animation_worklist_target as target,
    body_original_mode as original, body_original_owner as owner,
    body_region, control_pose_assets as poses, foot_controls, limb_ik, unity_animation as ua)
from test_original_leg_rest_return_blender import make_fixture, edit_legs
from test_body_rest_resync_blender import protected


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def retain_action(rig):
    """Native Actions own real ID references and first pass the old full proof."""
    rig.pose.bones['hand.L'].keyframe_insert(data_path='rotation_euler', frame=1)
    action = rig.animation_data.action
    action.name = 'Retained Custom artist animation'
    rig.animation_data.action = None
    motion = rig.data.copy()
    motion.name = 'Retained unchanged motion data'
    motion.use_fake_user = False
    old_raw = canonical(ua._rest_state(rig.data))
    action[target.ACTION_RIG_KEY] = rig
    action[ua.ORIGINAL_DATA_KEY] = rig.data
    action[ua.PREVIEW_DATA_KEY] = motion
    action[ua.ORIGINAL_REST_KEY] = old_raw
    action[target.ACTION_MOTION_KEY] = canonical({
        'version': 1, 'mapped_names': sorted(poses.native_rest(rig)),
        'motion_rest': ua._rest_state(motion),
        'original_rest_sha256': hashlib.sha256(old_raw.encode('utf-8')).hexdigest()})
    action['artist_note'] = 'Keep every authored key and this exact recovery proof.'
    rig[target.RESTORED] = True
    if not target.single_armature_user(rig):
        raise AssertionError('The fixture must authenticate before any native Rest is edited.')
    if rig.data.users <= 1 or action not in bpy.data.user_map().get(rig.data, set()):
        raise AssertionError('The fixture needs actual native Action ID references.')
    return action, motion


def motion_edit(rig, motion):
    """Change the separate historical data through actual EditBones."""
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    temporary = bpy.data.objects.new('Foreign edit of historical motion data', motion)
    bpy.context.scene.collection.objects.link(temporary)
    bpy.context.view_layer.objects.active = temporary
    temporary.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    temporary.data.edit_bones['hand.L'].tail += Vector((.01, 0, 0))
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.objects.remove(temporary, do_unlink=True)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)


class OriginalReturnOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def make(self):
        rig, mesh = make_fixture()
        action, motion = retain_action(rig)
        original.enter(bpy.context, rig)
        edit_legs(rig)
        return rig, mesh, action, motion

    def assertRefusedUnchanged(self, rig, action, motion):
        before = protected(rig)
        rest = ua._rest_state(motion)
        action_before = owner._animation_fingerprint(action)
        with self.assertRaises(ValueError):
            original.leave(bpy.context, rig)
        self.assertEqual(protected(rig), before)
        self.assertEqual(ua._rest_state(motion), rest)
        self.assertEqual(owner._animation_fingerprint(action), action_before)
        self.assertTrue(original.active(rig))
        self.assertNotIn(body_region.RECOVERY_OWNER_KEY, rig.data)

    def reference_rows(self):
        rig, _mesh = make_fixture()
        record = foot_controls.records(rig)['L']
        name = record['bones']['FK_TOE_REF']
        current = copy.deepcopy(ua._rest_state(rig.data)[name])
        _matrix, basis, _error = owner._frame(current)
        canonical_matrix = basis.to_4x4()
        canonical_matrix.translation = Vector(current['head'])
        current['matrix'] = [value for part in canonical_matrix for value in part]
        state = {'head': current['head'], 'tail': current['tail'],
                 'parent': current['parent'], 'matrix': [list(part) for part in canonical_matrix]}
        old = copy.deepcopy(current)
        for index in range(3):
            old['matrix'][index * 4] += basis[index][0] * 8e-6
        return name, old, current, {'role': 'FK_TOE_REF', 'state': state}

    def test_two_foot_roles_allow_only_bounded_native_x_column_residue(self):
        name, old, current, footprint = self.reference_rows()
        for role in ('FK_TOE_REF', 'AUTO_TOE_REF'):
            candidate = dict(footprint, role=role)
            result = owner._reference_roundtrip(old, current, candidate)
            self.assertIsNotNone(result)
            self.assertLessEqual(result['canonical_basis_error'], 1e-6)
            self.assertLessEqual(result['maximum_x_column_residue'], 2e-5)
            accepted = owner._match_preceding({name: current}, {name: old}, {name: candidate})
            self.assertEqual(len(accepted), 1)
        self.assertIsNone(owner._match_preceding({name: current}, {name: old}, {}))
        self.assertIsNone(owner._reference_roundtrip(old, current, dict(footprint, role='HEEL_PIVOT')))

    def test_reference_roundtrip_refuses_orientation_geometry_and_relation_changes(self):
        _name, old, current, footprint = self.reference_rows()
        for field in ('head', 'tail'):
            edited = copy.deepcopy(current)
            edited[field][0] += 3e-6
            self.assertIsNone(owner._reference_roundtrip(old, edited, footprint), field)
        edited = dict(current, parent='Another native bone')
        self.assertIsNone(owner._reference_roundtrip(old, edited, footprint))
        edited = dict(current, use_connect=not current['use_connect'])
        self.assertIsNone(owner._reference_roundtrip(old, edited, footprint))
        edited = copy.deepcopy(current)
        edited['matrix'][2] += 3e-5
        self.assertIsNone(owner._reference_roundtrip(old, edited, footprint))
        # Equal endpoint frames cannot grant a moving baseline: both must
        # independently agree with the immutable third Foot footprint.
        shifted = copy.deepcopy(current)
        shifted['matrix'][2] += 3e-6
        self.assertIsNone(owner._reference_roundtrip(shifted, shifted, footprint))
        wrong = copy.deepcopy(footprint)
        wrong['state']['tail'][0] += 3e-6
        self.assertIsNone(owner._reference_roundtrip(old, current, wrong))

    def test_reference_roundtrip_refuses_large_x_singular_and_nonfinite_matrices(self):
        _name, old, current, footprint = self.reference_rows()
        edited = copy.deepcopy(old)
        for index in range(3):
            edited['matrix'][index * 4] *= 1.0001
        self.assertIsNone(owner._reference_roundtrip(edited, current, footprint))
        self.assertIsNone(owner._reference_roundtrip(current, edited, footprint))
        for failure in ('zero', 'nonfinite'):
            edited = copy.deepcopy(old)
            for index in range(3):
                edited['matrix'][index * 4] = 0.0 if failure == 'zero' else float('nan')
            self.assertIsNone(owner._reference_roundtrip(edited, current, footprint))

    def test_first_and_repeated_returns_preserve_historical_animation(self):
        rig, _mesh, action, motion = self.make()
        action_before = owner._animation_fingerprint(action)
        motion_before = ua._rest_state(motion)
        for index in range(2):
            if index:
                original.enter(bpy.context, rig)
                edit_legs(rig)
            authored = ua._rest_state(rig.data)
            identities = {bone.name: bone.as_pointer() for bone in rig.data.bones}
            self.assertTrue(original.leave(bpy.context, rig))
            self.assertFalse(original.active(rig))
            self.assertEqual(ua._rest_state(rig.data), authored)
            self.assertEqual({bone.name: bone.as_pointer() for bone in rig.data.bones}, identities)
            revision = json.loads(rig.data[body_region.RECOVERY_OWNER_KEY])
            self.assertEqual(revision['rest'], authored)
            self.assertIn(action.name, revision['stale_actions'])
            self.assertTrue(body_region.verified_single_armature_user(rig))
            self.assertFalse(target.single_armature_user(rig))
            self.assertEqual(owner._animation_fingerprint(action), action_before)
            self.assertEqual(ua._rest_state(motion), motion_before)
            self.assertNotIn(body_region.RECORD_KEY, rig.data)

    def test_foreign_id_property_reference_is_not_an_owner(self):
        rig, _mesh, action, motion = self.make()
        foreign = bpy.data.objects.new('Foreign data reference', None)
        bpy.context.scene.collection.objects.link(foreign)
        foreign['borrowed_armature'] = rig.data
        self.assertRefusedUnchanged(rig, action, motion)

    def test_second_actual_armature_binding_is_refused(self):
        rig, _mesh, action, motion = self.make()
        foreign = bpy.data.objects.new('Second actual armature binding', rig.data)
        bpy.context.scene.collection.objects.link(foreign)
        self.assertRefusedUnchanged(rig, action, motion)

    def test_native_hand_edit_is_outside_the_admitted_scope(self):
        rig, _mesh, action, motion = self.make()
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['hand.L'].tail += Vector((.02, 0, 0))
        bpy.ops.object.mode_set(mode='POSE')
        self.assertRefusedUnchanged(rig, action, motion)

    def test_changed_historical_motion_rest_is_refused(self):
        rig, _mesh, action, motion = self.make()
        motion_edit(rig, motion)
        self.assertRefusedUnchanged(rig, action, motion)

    def test_changed_original_action_proof_is_refused(self):
        rig, _mesh, action, motion = self.make()
        action[ua.ORIGINAL_REST_KEY] += ' '
        self.assertRefusedUnchanged(rig, action, motion)

    def test_post_revision_write_failure_restores_the_edited_original(self):
        rig, _mesh, action, motion = self.make()
        before = protected(rig)
        action_before = owner._animation_fingerprint(action)
        motion_before = ua._rest_state(motion)
        finish = body_region._finish_owner_revision
        def fail_after_write(*args, **kwargs):
            finish(*args, **kwargs)
            raise ValueError('Injected failure after the accepted owner revision write')
        with patch.object(body_region, '_finish_owner_revision', side_effect=fail_after_write):
            with self.assertRaisesRegex(ValueError, 'Injected failure'):
                original.leave(bpy.context, rig)
        self.assertEqual(protected(rig), before)
        self.assertTrue(original.active(rig))
        self.assertNotIn(body_region.RECOVERY_OWNER_KEY, rig.data)
        self.assertNotIn(body_region.RECORD_KEY, rig.data)
        self.assertEqual(owner._animation_fingerprint(action), action_before)
        self.assertEqual(ua._rest_state(motion), motion_before)
        self.assertIsNone(body_region._OWNER_OPERATION)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(OriginalReturnOwnerTests))
    if not result.wasSuccessful():
        raise RuntimeError('Original-return recovery owner tests failed')
