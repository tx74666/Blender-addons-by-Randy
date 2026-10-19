"""Leg Poles face anatomical front even when old controls/metadata disagree.

Use a disposable --background --factory-startup Blender process only. No
artist input is opened or saved. A release runner may preload a frozen package.
Front is observed independently from evaluated native hip/ankle geometry and
the rig's anatomical -Y axis, never from saved Pole-direction metadata.
"""
import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_mode as original,
    body_original_owner, body_region as region, body_setup, control_pose_assets,
    limb_ik, limb_ik_fk, limb_ik_fk_batch as batch, unity_animation)
import test_body_region_blender as regional
import test_body_region_pole_layout_blender as layout_tests
from test_body_region_styles_blender import resources, shape_state
from test_body_rest_resync_blender import bound_meshes, mesh_raw


def evaluated_front(rig):
    """Actual Pole radial and projected anatomical front in Armature space."""
    regional.update(rig)
    inventory = limb_ik._validate_inventory(rig)
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = {}
    for side in ('L', 'R'):
        data = inventory['rigs'][('LEG', side)]
        upper, lower = (evaluated.pose.bones[name] for name in data['chain'][:2])
        hip, ankle = upper.head.copy(), lower.tail.copy()
        axis = ankle - hip
        if axis.length <= 1e-8:
            raise AssertionError('Degenerate fixture hip/ankle axis: ' + side)
        axis.normalize()
        front = Vector((0., -1., 0.))
        front -= axis * front.dot(axis)
        if front.length <= 1e-8:
            raise AssertionError('Anatomical front is parallel to fixture leg: ' + side)
        front.normalize()
        head = evaluated.pose.bones[data['pole'].name].head.copy()
        offset = head - hip
        radial = offset - axis * offset.dot(axis)
        if radial.length <= 1e-8:
            raise AssertionError('Fixture Pole has no usable radial offset: ' + side)
        constraints = [con for _pb, con, record in data['entries']
                       if record['role'] == 'IK']
        if len(constraints) != 1:
            raise AssertionError('Expected one IK constraint for ' + side)
        result[side] = {
            'hip': hip, 'axis': axis, 'head': head, 'offset': offset,
            'radial': radial, 'front': front,
            'front_cosine': radial.normalized().dot(front),
            'pole_name': data['pole'].name,
            'owner_side': data['pole'].get(limb_ik.SIDE_KEY),
            'constraint': constraints[0],
        }
    return result


def native_matrices(rig):
    regional.update(rig)
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {name: evaluated.pose.bones[name].matrix.copy()
            for name in control_pose_assets.native_rest(rig)}


def ik_angles(rig):
    """Native constraint values, including dormant and unrelated IK chains."""
    return {(pb.name, con.name): float(con.pole_angle)
            for pb in rig.pose.bones for con in pb.constraints if con.type == 'IK'}


def authored_styles(rig):
    # IK/FK and Original deliberately switch display visibility. Geometry,
    # binding, colors, scale/offset and artist widget data must remain exact.
    return {name: {key: value for key, value in row.items()
                   if key not in {'hidden', 'rotation_mode'}}
            for name, row in shape_state(rig).items()}


class LegFrontPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background or '--factory-startup' not in sys.argv:
            raise RuntimeError('Leg front tests require disposable background factory-startup Blender.')
        character_designer.register()

    def make(self):
        self.keeper = layout_tests.BodyRegionPoleLayoutTests()
        rig, mesh = self.keeper.make()
        # Exercise a real retained Action, not merely an empty Action list.
        finger = rig.pose.bones['index01.L']
        finger.rotation_mode = 'XYZ'
        finger.rotation_euler.z = .08
        finger.keyframe_insert(data_path='rotation_euler', frame=1)
        finger.keyframe_insert(data_path='rotation_euler', frame=30)
        regional.update(rig)
        self.assertTrue(regional.actions(rig))
        self.assert_front(rig)
        return rig, mesh

    def assert_front(self, rig):
        rows = evaluated_front(rig)
        for side, sign in (('L', 1.), ('R', -1.)):
            row = rows[side]
            self.assertGreater(row['front_cosine'], .8,
                               side + ' Pole is behind anatomical front')
            self.assertGreater(sign * row['head'].x, .05,
                               side + ' Pole crossed to the other leg')
            self.assertEqual(row['owner_side'], side)
            self.assertIs(row['constraint'].pole_target, rig)
            self.assertEqual(row['constraint'].pole_subtarget, row['pole_name'])
            self.assertTrue(math.isfinite(float(row['constraint'].pole_angle)))
        return rows

    def stale_behind(self, rig, sides=('R',), *, reverse_metadata=False):
        """Old valid solver state: Pole is behind, angle compensates its side."""
        rows = evaluated_front(rig)
        inventory = limb_ik._validate_inventory(rig)
        for side in sides:
            row = rows[side]
            pole = rig.pose.bones[row['pole_name']]
            matrix = pole.matrix.copy()
            axial = row['axis'] * row['offset'].dot(row['axis'])
            matrix.translation = row['hip'] + axial - row['radial']
            pole.matrix = matrix
            con = row['constraint']
            con.pole_angle = (float(con.pole_angle) + 2. * math.pi) % (2. * math.pi) - math.pi
            if reverse_metadata:
                bone = inventory['rigs'][('LEG', side)]['pole']
                for key in (limb_ik.POLE_DIRECTION_KEY, limb_ik.POLE_CONFIGURED_DIRECTION_KEY):
                    bone[key] = list(-row['front'])
        regional.update(rig)
        current = evaluated_front(rig)
        for side in sides:
            self.assertLess(current[side]['front_cosine'], -.8,
                            'Setup did not put the old ' + side + ' Pole behind')

    def snapshot(self, rig, mesh):
        result = self.keeper.snapshot(rig, mesh)
        result['styles'] = authored_styles(rig)
        result['native_pose'] = native_matrices(rig)
        result['actions_complete'] = {action.name: copy.deepcopy(
            body_original_owner._animation_fingerprint(action)) for action in bpy.data.actions}
        return result

    def assert_protected(self, rig, mesh, before, *, rollback=False):
        self.keeper.assert_protected(rig, mesh, before, rollback=rollback)
        self.assertEqual(authored_styles(rig), before['styles'])
        self.assertEqual({action.name: body_original_owner._animation_fingerprint(action)
                          for action in bpy.data.actions}, before['actions_complete'])
        current = native_matrices(rig)
        for side in ('L', 'R'):
            # The native foot/toe frame includes the visible sole orientation.
            # Use the existing successful-bind matrix contract, not a new looser
            # threshold. A failure rollback uses its stricter separate check.
            for prefix in ('foot', 'toe'):
                name = prefix + '.' + side
                old = before['native_pose'][name]
                error = max(abs(old[i][j] - current[name][i][j])
                            for i in range(3) for j in range(3))
                self.assertLessEqual(error, 2e-5 if rollback else 2e-4,
                                     name + ' orientation changed')

    def test_repair_stale_right_pose_with_front_metadata_without_rebuild(self):
        rig, mesh = self.make()
        self.stale_behind(rig)
        before = self.snapshot(rig, mesh)
        all_bone_ids = {bone.name: bone.as_pointer() for bone in rig.data.bones}
        limb_ik_fk.repair_leg_poles(bpy.context, rig)
        self.assert_front(rig)
        self.assert_protected(rig, mesh, before)
        self.assertEqual({bone.name: bone.as_pointer() for bone in rig.data.bones}, all_bone_ids)
        once = self.snapshot(rig, mesh)
        heads = {side: row['head'].copy() for side, row in evaluated_front(rig).items()}
        limb_ik_fk.repair_leg_poles(bpy.context, rig)
        self.assert_front(rig)
        self.assert_protected(rig, mesh, once)
        for side, row in evaluated_front(rig).items():
            self.assertLessEqual((row['head'] - heads[side]).length, 1e-5,
                                 'Repeated repair moved the ' + side + ' Pole')

    def test_repair_does_not_trust_reversed_saved_metadata(self):
        rig, mesh = self.make()
        self.stale_behind(rig, ('L', 'R'), reverse_metadata=True)
        before = self.snapshot(rig, mesh)
        desired = native_matrices(rig)
        limb_ik_fk.repair_leg_poles(bpy.context, rig, desired_pose=desired)
        self.assert_front(rig)
        self.assert_protected(rig, mesh, before)

    def test_near_pi_right_rest_roll_rebind_keeps_both_front_and_foot_orientation(self):
        rig, mesh = self.make()
        saved_styles = authored_styles(rig)
        region.release(bpy.context, rig, 'LEGS')
        limb_ik._mode_set(bpy.context, rig, 'EDIT')
        mirror = rig.data.use_mirror_x
        rig.data.use_mirror_x = False
        try:
            rig.data.edit_bones['thigh.R'].roll += math.pi - .02
            rig.data.edit_bones['shin.R'].roll -= math.pi - .03
            limb_ik._mode_set(bpy.context, rig, 'POSE')
        finally:
            rig.data.use_mirror_x = mirror
        regional.update(rig)
        before = self.snapshot(rig, mesh)
        # Released helpers are absent from the interim scene. Their authored
        # styles must be restored from the regional receipt on bind.
        before['styles'] = saved_styles
        region.bind(bpy.context, rig, 'LEGS')
        self.assert_front(rig)
        self.assert_protected(rig, mesh, before)

    def test_front_points_with_zero_angles_restore_explicit_safe_native_pose(self):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEGS')
        limb_ik._mode_set(bpy.context, rig, 'EDIT')
        mirror = rig.data.use_mirror_x
        rig.data.use_mirror_x = False
        try:
            rig.data.edit_bones['thigh.R'].roll += math.pi - .02
            rig.data.edit_bones['shin.R'].roll -= math.pi - .03
            limb_ik._mode_set(bpy.context, rig, 'POSE')
        finally:
            rig.data.use_mirror_x = mirror
        regional.update(rig)
        region.bind(bpy.context, rig, 'LEGS')
        self.assert_front(rig)
        safe = self.snapshot(rig, mesh)
        desired = {name: matrix.copy() for name, matrix in safe['native_pose'].items()}
        inventory = limb_ik._validate_inventory(rig)
        right_ik = next(con for _pb, con, record
                        in inventory['rigs'][('LEG', 'R')]['entries'] if record['role'] == 'IK')
        self.assertGreater(abs(float(right_ik.pole_angle)), .5,
                           'Fixture needs nonzero compensation for the right Rest roll')
        for side in ('L', 'R'):
            for _pb, con, record in inventory['rigs'][('LEG', side)]['entries']:
                if record['role'] == 'IK':
                    con.pole_angle = 0.
        regional.update(rig)
        self.assert_front(rig)
        wrong = native_matrices(rig)
        mismatch = max(abs(wrong[name][i][j] - desired[name][i][j])
                       for name in ('thigh.R', 'shin.R')
                       for i in range(3) for j in range(3))
        self.assertGreater(mismatch, .05,
                           'Zero angles did not expose the incorrect native orientation')

        # Preserve the current authored inputs exactly, but select the accepted
        # safe deformation as the output baseline. Preserving the zero-angle
        # deformation here would accidentally certify the original fault.
        expected = self.snapshot(rig, mesh)
        expected['skin'] = safe['skin']
        expected['surfaces'] = safe['surfaces']
        expected['native_pose'] = desired
        limb_ik_fk.repair_leg_poles(bpy.context, rig, desired_pose=desired)
        self.assert_front(rig)
        self.assert_protected(rig, mesh, expected)
        repaired = native_matrices(rig)
        for side in ('L', 'R'):
            for prefix in ('thigh', 'shin', 'foot', 'toe'):
                name = prefix + '.' + side
                error = max(abs(repaired[name][i][j] - desired[name][i][j])
                            for i in range(4) for j in range(4))
                self.assertLessEqual(error, 2e-4, name + ' did not recover the selected safe pose')

    def test_repeated_regional_rebuild_repairs_old_behind_layout(self):
        rig, mesh = self.make()
        self.stale_behind(rig, ('L', 'R'), reverse_metadata=True)
        for _repeat in range(2):
            before = self.snapshot(rig, mesh)
            region.rebuild(bpy.context, rig, 'LEGS')
            self.assert_front(rig)
            self.assert_protected(rig, mesh, before)

    def test_ordinary_ik_fk_roundtrips_keep_front_without_fixed_pole_opt_in(self):
        rig, mesh = self.make()
        self.stale_behind(rig, ('L', 'R'), reverse_metadata=True)
        for _repeat in range(2):
            before = self.snapshot(rig, mesh)
            for mode in ('FK', 'IK'):
                for side in ('L', 'R'):
                    limb_ik_fk.switch_limb(bpy.context, rig, ('LEG', side), mode,
                                           keyframe=False)
            self.assert_front(rig)
            self.assert_protected(rig, mesh, before)

    def test_original_controls_return_replaces_old_behind_pole_pose(self):
        rig, mesh = self.make()
        self.stale_behind(rig, ('L', 'R'), reverse_metadata=True)
        before = self.snapshot(rig, mesh)
        original.enter(bpy.context, rig)
        self.assertTrue(original.active(rig))
        self.assertTrue(original.leave(bpy.context, rig))
        self.assertFalse(original.active(rig))
        self.assert_front(rig)
        self.assert_protected(rig, mesh, before)

    def test_batch_same_mode_ik_repairs_stale_back_poles(self):
        rig, mesh = self.make()
        keys = (('LEG', 'L'), ('LEG', 'R'))
        self.assertEqual(batch.mode_for_keys(rig, keys=keys), 'IK')
        self.stale_behind(rig, ('L', 'R'), reverse_metadata=True)
        before = self.snapshot(rig, mesh)
        batch.switch_all(bpy.context, rig, 'IK', keys=keys, keyframe=False)
        self.assertEqual(batch.mode_for_keys(rig, keys=keys), 'IK')
        self.assert_front(rig)
        self.assert_protected(rig, mesh, before)

    def test_batch_fk_and_same_mode_fk_repair_stale_poles_without_pose_drift(self):
        rig, mesh = self.make()
        keys = (('LEG', 'L'), ('LEG', 'R'))
        self.stale_behind(rig, ('L', 'R'), reverse_metadata=True)
        before = self.snapshot(rig, mesh)
        batch.switch_all(bpy.context, rig, 'FK', keys=keys, keyframe=False)
        self.assertEqual(batch.mode_for_keys(rig, keys=keys), 'FK')
        self.assert_front(rig)
        # Entering native FK changes its managed visual bindings. Preserve
        # native deformation, raw mesh/weights/keys, Actions and hands across
        # that transition; compare FK styles against an established FK state.
        self.keeper.assert_protected(rig, mesh, before)
        fk_before = self.snapshot(rig, mesh)
        # Dormant IK controls can become stale while the artist remains in FK.
        # A repeated FK click must repair that layout without enabling IK.
        self.stale_behind(rig, ('L', 'R'), reverse_metadata=True)
        self.assert_protected(rig, mesh, fk_before)
        batch.switch_all(bpy.context, rig, 'FK', keys=keys, keyframe=False)
        self.assertEqual(batch.mode_for_keys(rig, keys=keys), 'FK')
        self.assert_front(rig)
        self.assert_protected(rig, mesh, fk_before)

    def test_original_late_failure_restores_successfully_repaired_leg_angles_and_session(self):
        rig, mesh = self.make()
        self.stale_behind(rig, ('L', 'R'))
        original.enter(bpy.context, rig)
        before = self.snapshot(rig, mesh)
        scene = regional.scene_state(rig, mesh)
        channels = copy.deepcopy(original._channels(rig))
        session, angles = rig[original.SESSION], ik_angles(rig)
        actual_repair = limb_ik_fk.repair_leg_poles
        actual_completed = original.display._completed
        repairs, failures = [], []

        def observe_successful_repair(context, armature, **kwargs):
            result = actual_repair(context, armature, **kwargs)
            self.assert_front(armature)
            after = ik_angles(armature)
            self.assertTrue(any(abs(after[key] - value) > .1 for key, value in angles.items()),
                            'Original return must complete a real native Leg angle repair')
            repairs.append(after)
            return result

        def fail_after_completed(*args, **kwargs):
            result = actual_completed(*args, **kwargs)
            if not failures:
                self.assertTrue(repairs, 'Late failure ran before a successful Leg repair')
                failures.append(True)
                raise ValueError('Injected Original completion failure after Leg repair')
            return result

        with patch.object(limb_ik_fk, 'repair_leg_poles', side_effect=observe_successful_repair), \
             patch.object(original.display, '_completed', side_effect=fail_after_completed):
            with self.assertRaisesRegex(ValueError, 'Original completion failure after Leg repair'):
                original.leave(bpy.context, rig)
        self.assertTrue(repairs and failures)
        self.assertTrue(original.active(rig))
        self.assertEqual(rig[original.SESSION], session)
        self.assertEqual(original._channels(rig), channels)
        self.assertEqual(ik_angles(rig), angles)
        self.assert_protected(rig, mesh, before, rollback=True)
        regional.BodyRegionTests().assertRecoveredScene(regional.scene_state(rig, mesh), scene)

    def test_outer_pose_transaction_failure_restores_actual_leg_angle_repair(self):
        rig, mesh = self.make()
        self.stale_behind(rig, ('L', 'R'))
        before = self.snapshot(rig, mesh)
        scene = regional.scene_state(rig, mesh)
        channels, angles = copy.deepcopy(original._channels(rig)), ik_angles(rig)
        session = rig.get(original.SESSION)
        repaired = []
        # Exercise the real outer Pose application recovery boundary with a
        # successful nested Leg repair before the injected later-stage error.
        with self.assertRaisesRegex(ValueError, 'outer Pose failure after Leg repair'):
            with control_pose_assets._transaction(bpy.context, rig):
                limb_ik_fk.repair_leg_poles(bpy.context, rig)
                self.assert_front(rig)
                after = ik_angles(rig)
                self.assertTrue(any(abs(after[key] - value) > .1 for key, value in angles.items()),
                                'The outer transaction must contain real native angle writes')
                repaired.append(after)
                raise ValueError('Injected outer Pose failure after Leg repair')
        self.assertTrue(repaired)
        self.assertEqual(ik_angles(rig), angles)
        self.assertEqual(original._channels(rig), channels)
        self.assertEqual(rig.get(original.SESSION), session)
        self.assert_protected(rig, mesh, before, rollback=True)
        regional.BodyRegionTests().assertRecoveredScene(regional.scene_state(rig, mesh), scene)

    def test_second_leg_match_failure_restores_both_poles_and_all_author_inputs(self):
        rig, mesh = self.make()
        self.stale_behind(rig, ('L', 'R'), reverse_metadata=True)
        before = self.snapshot(rig, mesh)
        scene = regional.scene_state(rig, mesh)
        complete_rest = copy.deepcopy(unity_animation._rest_state(rig.data))
        styles, names = shape_state(rig), resources()
        author = {obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}
        actual = limb_ik_fk._match_fixed_pole_angle
        calls = []

        def reject_right_after_angle_write(context, armature, data, desired, **kwargs):
            result = actual(context, armature, data, desired, **kwargs)
            calls.append(data['pole'].name)
            if data['pole'].get(limb_ik.SIDE_KEY) == 'R':
                con = next(con for _pb, con, record in data['entries'] if record['role'] == 'IK')
                con.pole_angle = max(-math.pi, min(math.pi, float(con.pole_angle) + .123))
                regional.update(armature)
                raise ValueError('Injected right Leg front angle failure')
            return result

        with patch.object(limb_ik_fk, '_match_fixed_pole_angle',
                          side_effect=reject_right_after_angle_write):
            with self.assertRaisesRegex(ValueError, 'Injected right Leg front angle failure'):
                limb_ik_fk.repair_leg_poles(bpy.context, rig)
        self.assertTrue(any(name.endswith('.L') for name in calls),
                        'Fault must occur after a completed left Leg match')
        self.assertTrue(any(name.endswith('.R') for name in calls))
        self.assert_protected(rig, mesh, before, rollback=True)
        regional.BodyRegionTests().assertRecoveredScene(regional.scene_state(rig, mesh), scene)
        self.assertEqual(unity_animation._rest_state(rig.data), complete_rest)
        self.assertEqual(shape_state(rig), styles)
        self.assertEqual(resources(), names)
        self.assertEqual({obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}, author)
        for side, row in evaluated_front(rig).items():
            self.assertLess(row['front_cosine'], -.8,
                            'Rollback did not restore the old ' + side + ' Pole')
        self.assertFalse(body_setup._ATOMIC_STACK)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(LegFrontPolicyTests))
    if not result.wasSuccessful():
        raise RuntimeError('Leg front policy tests failed')
