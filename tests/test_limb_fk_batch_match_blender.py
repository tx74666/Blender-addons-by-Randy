"""Native FK basis batching, dependency fallback, and transaction protection.

Run only in an isolated Blender --background --factory-startup process.
Update counts describe native solve requests; no wall-clock threshold is used.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_mode as original, control_pose_assets,
    limb_ik, limb_ik_fk as match, limb_ik_fk_batch as batch, root_control)
import test_body_rest_resync_blender as rest
from test_limb_ik_fk_batch_blender import BodyLimbBatchTests, complete
from test_limb_ik_fk_blender import build


class NativeFKBasisBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Use an isolated background Blender for these fixtures.')
        character_designer.register()

    def make(self):
        return BodyLimbBatchTests.make(self)

    def assertSurface(self, rig, desired):
        BodyLimbBatchTests.assertSurface(self, rig, desired)

    def settle(self, rig):
        match._update(bpy.context, rig)
        return original._pose(rig)

    def fk_chain(self, rig, key=('ARM', 'L')):
        inventory = limb_ik._validate_inventory(rig)
        item = inventory['rigs'][key]
        rig.pose.bones[item['target'].name][match.PROPERTY] = 0.
        match._update(bpy.context, rig)
        return item, match._matrices(rig, item['chain'])

    def test_four_direct_chains_match_reference_with_eight_fewer_solves(self):
        rig, _skin, _surface = self.make()
        inventory = limb_ik._validate_inventory(rig)
        root = rig.pose.bones[root_control.control_name(rig)]
        root.rotation_mode = 'XYZ'
        root.rotation_euler = (.04, -.03, .02)
        root[root_control.SCALE_PROPERTY] = 1.07
        for index, item in enumerate(inventory['rigs'].values()):
            target = rig.pose.bones[item['target'].name]
            target.location += Vector((.005, -.008, .007))
            target.rotation_euler = (.01 * index, -.015, .008)
        desired = self.settle(rig)
        surfaces = rest.world_surfaces(rig)
        raw = {obj.name: rest.mesh_raw(obj) for obj in rest.bound_meshes(rig)}
        geometry = control_pose_assets.native_rest(rig)
        before = batch._checkpoint(bpy.context, rig, None)

        with patch.object(match, '_native_fk_bases', return_value=None), \
                patch.object(match, '_update', wraps=match._update) as reference_updates:
            batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        reference_pose = original._pose(rig)
        reference_basis = {name: rig.pose.bones[name].matrix_basis.copy()
                           for item in inventory['rigs'].values() for name in item['chain']}
        match._verify(rig, desired)
        self.assertSurface(rig, surfaces)
        batch._rollback(bpy.context, rig, before, ())

        candidates = []
        native = match._native_fk_bases
        def observe(*args):
            result = native(*args)
            candidates.append(result is not None)
            return result
        with patch.object(match, '_native_fk_bases', side_effect=observe), \
                patch.object(match, '_update', wraps=match._update) as optimized_updates:
            batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(candidates, [True] * 4)
        self.assertEqual(reference_updates.call_count - optimized_updates.call_count, 8)
        match._verify(rig, desired)
        match._verify(rig, reference_pose)
        for name, basis in reference_basis.items():
            self.assertLess(max(abs(basis[i][j] - rig.pose.bones[name].matrix_basis[i][j])
                                for i in range(4) for j in range(4)), 4e-5, name)
        self.assertSurface(rig, surfaces)
        self.assertEqual(control_pose_assets.native_rest(rig), geometry)
        self.assertEqual({obj.name: rest.mesh_raw(obj) for obj in rest.bound_meshes(rig)}, raw)
        limb_ik._validate_inventory(rig)

    def test_connected_inheritance_and_local_location_use_desired_parent_frames(self):
        rig, _skin, _surface = self.make()
        item, _desired = self.fk_chain(rig)
        bones = [rig.pose.bones[name] for name in item['chain']]
        self.assertTrue(bones[1].bone.use_connect)
        self.assertTrue(bones[2].bone.use_connect)
        bones[0].parent.scale = (1.08, .94, 1.03)
        bones[0].parent.rotation_mode = 'XYZ'
        bones[0].parent.rotation_euler = (.025, -.02, .01)
        for inheritance in ('FULL', 'FIX_SHEAR', 'ALIGNED', 'AVERAGE', 'NONE', 'NONE_LEGACY'):
            with self.subTest(inherit_scale=inheritance):
                for index, pb in enumerate(bones):
                    pb.bone.inherit_scale = inheritance
                    pb.bone.use_local_location = index != 1
                    pb.bone.use_inherit_rotation = index != 2
                    pb.rotation_mode = 'XYZ'
                    pb.rotation_euler = (.015 * (index + 1), -.01, .02)
                    pb.scale = (1.015, .99, 1.005)
                    pb.location = (0., 0., 0.)
                self.settle(rig)
                desired = match._matrices(rig, item['chain'])
                surfaces = rest.world_surfaces(rig)
                raw = {obj.name: rest.mesh_raw(obj) for obj in rest.bound_meshes(rig)}
                flags = [(pb.bone.inherit_scale, pb.bone.use_local_location,
                          pb.bone.use_inherit_rotation, pb.bone.use_connect) for pb in bones]
                for pb in bones:
                    pb.rotation_euler = (-.01, .005, -.015)
                    pb.scale = (1.,) * 3
                self.settle(rig)
                self.assertIsNotNone(match._native_fk_bases(rig, item, desired))
                with patch.object(match, '_update', wraps=match._update) as updates:
                    match._match_fk(bpy.context, rig, item, desired)
                self.assertEqual(updates.call_count, 2)
                match._verify(rig, desired)
                self.assertSurface(rig, surfaces)
                self.assertEqual([(pb.bone.inherit_scale, pb.bone.use_local_location,
                                   pb.bone.use_inherit_rotation, pb.bone.use_connect) for pb in bones], flags)
                self.assertEqual({obj.name: rest.mesh_raw(obj) for obj in rest.bound_meshes(rig)}, raw)
                limb_ik._validate_inventory(rig)

    def test_foreign_constraints_and_parent_feedback_decline_before_writes(self):
        rig, _skin, _surface = self.make()
        item, desired = self.fk_chain(rig)
        source = rig.pose.bones[item['chain'][0]]
        for owner, muted in ((source, False), (source.parent, False), (source.parent, True)):
            with self.subTest(owner=owner.name, muted=muted):
                con = owner.constraints.new('COPY_ROTATION')
                con.target, con.subtarget = rig, item['chain'][2]
                con.mute = muted
                before = complete(rig)
                self.assertIsNone(match._native_fk_bases(rig, item, desired))
                self.assertEqual(complete(rig), before)
                owner.constraints.remove(con)

    def test_transform_and_data_drivers_decline_but_owned_root_scale_is_allowed(self):
        rig, _skin, _surface = self.make()
        item, desired = self.fk_chain(rig)
        self.assertIsNotNone(match._native_fk_bases(rig, item, desired))
        root = rig.pose.bones[root_control.control_name(rig)]
        for owner in (rig.pose.bones[item['chain'][0]], rig.pose.bones[item['chain'][0]].parent, root, rig):
            with self.subTest(owner=owner.name):
                curve = owner.driver_add('location', 0)
                curve.driver.type, curve.driver.expression = 'SCRIPTED', '0.003'
                before = complete(rig)
                self.assertIsNone(match._native_fk_bases(rig, item, desired))
                self.assertEqual(complete(rig), before)
                owner.driver_remove('location', 0)
        rig.data['gate_probe'] = 0.
        curve = rig.data.driver_add('["gate_probe"]')
        curve.driver.type, curve.driver.expression = 'SCRIPTED', '0.0'
        before = complete(rig)
        self.assertIsNone(match._native_fk_bases(rig, item, desired))
        self.assertEqual(complete(rig), before)
        rig.data.driver_remove('["gate_probe"]')

    def test_unrepresentable_shear_and_stable_rig_keep_sequential_path(self):
        rig, _skin, _surface = self.make()
        item, desired = self.fk_chain(rig)
        sheared = dict(desired)
        shear = Matrix.Identity(4)
        shear[0][1] = .025
        sheared[item['chain'][0]] = desired[item['chain'][0]] @ shear
        before = complete(rig)
        self.assertIsNone(match._native_fk_bases(rig, item, sheared))
        self.assertEqual(complete(rig), before)

        rig, _key, item = build('ROLL_DECOUPLED', 'LEFT_ARM')
        desired = match._matrices(rig, item['chain'])
        before = complete(rig)
        self.assertIsNone(match._native_fk_bases(rig, item, desired))
        self.assertEqual(complete(rig), before)
        with patch.object(match, '_update', wraps=match._update) as updates:
            match._match_fk(bpy.context, rig, item, desired)
        self.assertEqual(updates.call_count, 4)
        match._verify(rig, desired)

    def test_rotation_modes_locks_and_autokey_keep_native_channel_paths(self):
        rig, _skin, _surface = self.make()
        inventory = limb_ik._validate_inventory(rig)
        for item in inventory['rigs'].values():
            for index, name in enumerate(item['chain']):
                pb = rig.pose.bones[name]
                pb.rotation_mode = ('XYZ', 'QUATERNION', 'AXIS_ANGLE')[index]
                pb.lock_location = (True, False, True)
                pb.lock_rotation = (False, True, False)
                pb.lock_scale = (True, False, True)
                pb.lock_rotation_w = True
                pb.lock_rotations_4d = True
            rig.pose.bones[item['target'].name].location += Vector((.004, -.005, .006))
        bpy.context.scene.frame_set(12)
        desired = self.settle(rig)
        locks = {name: tuple((prop, tuple(getattr(rig.pose.bones[name], prop))
                            if hasattr(getattr(rig.pose.bones[name], prop), '__len__')
                            else getattr(rig.pose.bones[name], prop)) for prop in batch._LOCKS)
                 for item in inventory['rigs'].values() for name in item['chain']}
        modes = {name: rig.pose.bones[name].rotation_mode for name in locks}
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        with patch.object(match, '_native_fk_bases', wraps=match._native_fk_bases) as candidates:
            result = batch.switch_all(bpy.context, rig, 'FK')
        self.assertTrue(result['keyed'])
        self.assertEqual(candidates.call_count, 4)
        match._verify(rig, desired)
        curves = limb_ik._fcurves_for_action(rig.animation_data.action)
        paths = {curve.data_path for curve in curves}
        for name, mode in modes.items():
            pb = rig.pose.bones[name]
            self.assertEqual(pb.rotation_mode, mode)
            self.assertEqual(tuple((prop, tuple(getattr(pb, prop)) if hasattr(getattr(pb, prop), '__len__')
                                   else getattr(pb, prop)) for prop in batch._LOCKS), locks[name])
            rotation = {'XYZ': 'rotation_euler', 'QUATERNION': 'rotation_quaternion',
                        'AXIS_ANGLE': 'rotation_axis_angle'}[mode]
            self.assertIn(pb.path_from_id(rotation), paths)
        limb_ik._validate_inventory(rig)

    def test_failure_after_fourth_batched_write_rolls_back_native_scene_exactly(self):
        rig, _skin, _surface = self.make()
        before, desired = complete(rig), self.settle(rig)
        surfaces = rest.world_surfaces(rig)
        previous, calls = match._match_fk, []
        def fail_after_last(*args, **kwargs):
            result = previous(*args, **kwargs)
            calls.append(args[2]['chain'])
            if len(calls) == 4:
                raise ValueError('injected after final FK basis assignment')
            return result
        with patch.object(match, '_match_fk', side_effect=fail_after_last):
            with self.assertRaisesRegex(ValueError, 'final FK basis'):
                batch.switch_all(bpy.context, rig, 'FK', keyframe=True)
        self.assertEqual(len(calls), 4)
        self.assertEqual(complete(rig), before)
        match._verify(rig, desired)
        self.assertSurface(rig, surfaces)
        limb_ik._validate_inventory(rig)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeFKBasisBatchTests))
    if not result.wasSuccessful():
        raise RuntimeError('Native FK basis batch tests failed')
    print('LIMB_FK_BATCH_MATCH_PASSED', result.testsRun)
