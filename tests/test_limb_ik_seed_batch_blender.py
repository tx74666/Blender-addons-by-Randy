"""Native equivalence and dependency guards for batching a Direct IK seed.

Run in a disposable Blender --background --factory-startup process. These
checks compare native poses and graph-update calls; they are not a wall-clock
benchmark or evidence about the artist scene's GUI latency.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (
    bone_collections, bone_display, foot_controls, limb_ik,
    limb_ik_fk as match, root_control,
)
import test_limb_ik_fk_blender as fixtures


def _direct4(armature):
    """Preserve supported upgraded switches with the older display schema."""
    inventory = limb_ik._validate_inventory(armature)
    remove = []
    for data in inventory['rigs'].values():
        armature.pose.bones[data['pole'].name].custom_shape_transform = None
        for pb, con, record in data['entries']:
            if record['role'] == 'POLE_DISPLAY_TRACK':
                registry = limb_ik._constraint_registry(pb, strict=True)
                registry.pop(con.name)
                limb_ik._write_constraint_registry(pb, registry)
                pb.constraints.remove(con)
        remove.append(data['display'].name)
    bpy.ops.object.mode_set(mode='EDIT')
    for name in remove:
        armature.data.edit_bones.remove(armature.data.edit_bones[name])
    bpy.ops.object.mode_set(mode='POSE')
    armature.data[limb_ik.SCHEMA_KEY] = limb_ik.LEGACY_DIRECT_PREROLL_SCHEMA
    match._update(bpy.context, armature)


def _checkpoint(armature, data):
    target = armature.pose.bones[data['target'].name]
    return {
        'poses': {pb.name: (pb.rotation_mode, pb.matrix_basis.copy())
                  for pb in armature.pose.bones},
        'value': target[match.PROPERTY],
        'mutes': [(con, con.mute) for _pb, con, _record in data['entries']],
        'display': bone_display._snapshot(armature),
    }


def _restore(armature, data, state):
    bone_display._restore(armature, state['display'])
    bone_collections._FRAME_CACHE.pop(armature.as_pointer(), None)
    for con, mute in state['mutes']:
        con.mute = mute
    armature.pose.bones[data['target'].name][match.PROPERTY] = state['value']
    for name, (rotation_mode, basis) in state['poses'].items():
        pb = armature.pose.bones[name]
        pb.rotation_mode = rotation_mode
        pb.matrix_basis = basis
    match._update(bpy.context, armature)


def _channel_state(armature, data):
    return (
        tuple((pb.name, pb.rotation_mode, tuple(tuple(row) for row in pb.matrix_basis))
              for pb in armature.pose.bones),
        armature.pose.bones[data['target'].name][match.PROPERTY],
        tuple((con.name, con.mute) for _pb, con, _record in data['entries']),
    )


def _legacy_seed(armature, data, desired, solver_position, *, calibrated_rest=False):
    """The established evaluated writes, retained only as the test reference."""
    target = armature.pose.bones[data['target'].name]
    pole = armature.pose.bones[data['pole'].name]
    match._set_solver_position(bpy.context, armature, data, solver_position)
    if not calibrated_rest:
        matrix = pole.matrix.copy()
        matrix.translation = match._pole_position(armature, data, desired)
        match._set_matrix(bpy.context, armature, pole, matrix)
    if not calibrated_rest or target.get(match.PROPERTY) != 1.0:
        target[match.PROPERTY] = 1.0
        match._update(bpy.context, armature)


class DirectIKSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Use an isolated background Blender for these fixtures.')
        character_designer.register()

    def make(self, selected='LEFT_ARM', *, auto=True, root=False, schema4=False):
        armature, key, data = fixtures.build('DIRECT_PREROLL', selected)
        if schema4:
            _direct4(armature)
        if not auto:
            self.assertEqual(bpy.ops.character_designer.limb_ik_auto_align_target(action='DISABLE'),
                             {'FINISHED'})
        if root:
            record = root_control.build(bpy.context, armature)
            master = armature.pose.bones[record['master']]
            master.rotation_mode = 'XYZ'
            master.location = (.13, -.06, .09)
            master.rotation_euler = (.21, -.17, .32)
            master[root_control.SCALE_PROPERTY] = 1.17
        armature.location = (.2, -.3, .1)
        armature.rotation_euler = (.19, -.11, .27)
        armature.scale = (.83,) * 3
        data = limb_ik._validate_inventory(armature)['rigs'][key]
        target = armature.pose.bones[data['target'].name]
        target.location += Vector((.025, -.035, .035))
        target.rotation_mode = 'XYZ'
        target.rotation_euler = (.15, -.08, .04)
        match._update(bpy.context, armature)
        match.switch_limb(bpy.context, armature, key, 'FK', keyframe=False)
        inventory = limb_ik._validate_inventory(armature)
        return armature, key, inventory, inventory['rigs'][key]

    def assert_fallback(self, armature, inventory, data):
        match._update(bpy.context, armature)
        desired = match._matrices(armature, match.pose_names(data))
        before = _channel_state(armature, data)
        original_update = match._update
        with patch.object(match, '_update', wraps=original_update) as updates:
            accepted = match._seed_ik(bpy.context, armature, inventory, data, desired,
                                      desired[data['chain'][2]].translation.copy())
        self.assertFalse(accepted)
        self.assertEqual(updates.call_count, 0, 'Fallback selection must precede every mutation/update')
        self.assertEqual(_channel_state(armature, data), before)

    def test_native_seed_matches_evaluated_reference_with_one_update(self):
        cases = (
            ('LEFT_ARM', True, False, False),
            ('RIGHT_ARM', False, True, False),
            ('LEFT_LEG', False, False, False),
            ('RIGHT_LEG', True, True, True),
        )
        for selected, auto, root, schema4 in cases:
            with self.subTest(selected=selected, auto=auto, root=root, schema4=schema4):
                armature, _key, inventory, data = self.make(
                    selected, auto=auto, root=root, schema4=schema4)
                desired = match._matrices(armature, match.pose_names(data))
                solver_position = desired[data['chain'][2]].translation.copy()
                checkpoint = _checkpoint(armature, data)
                original_update = match._update
                with patch.object(match, '_update', wraps=original_update) as legacy_updates:
                    _legacy_seed(armature, data, desired, solver_position)
                self.assertEqual(legacy_updates.call_count, 3)
                reference = match._matrices(armature, armature.pose.bones.keys())
                control_bases = {data[role].name: armature.pose.bones[data[role].name].matrix_basis.copy()
                                 for role in ('target', 'pole')}
                _restore(armature, data, checkpoint)
                with patch.object(match, '_update', wraps=original_update) as updates:
                    accepted = match._seed_ik(bpy.context, armature, inventory, data, desired,
                                              solver_position)
                self.assertTrue(accepted)
                self.assertEqual(updates.call_count, 1)
                match._verify(armature, reference)
                for name, expected in control_bases.items():
                    actual = armature.pose.bones[name].matrix_basis
                    self.assertLess(max(abs(actual[i][j] - expected[i][j])
                                        for i in range(4) for j in range(4)), 2e-6)
                limb_ik._validate_inventory(armature)

    def test_full_auto_manual_match_equals_forced_sequential_seed(self):
        for selected, auto, root, schema4 in (
            ('LEFT_ARM', True, True, False),
            ('RIGHT_ARM', False, False, True),
            ('LEFT_LEG', False, True, False),
            ('RIGHT_LEG', True, False, False),
        ):
            with self.subTest(selected=selected, auto=auto, root=root, schema4=schema4):
                armature, key, _inventory, data = self.make(
                    selected, auto=auto, root=root, schema4=schema4)
                desired = match._matrices(armature, match.pose_names(data))
                checkpoint = _checkpoint(armature, data)
                rest = {bone.name: tuple(tuple(row) for row in bone.matrix_local)
                        for bone in armature.data.bones}
                with patch.object(match, '_seed_ik', return_value=False):
                    match.switch_limb(bpy.context, armature, key, 'IK', keyframe=False)
                reference = match._matrices(armature, armature.pose.bones.keys())
                _restore(armature, data, checkpoint)
                original_seed = match._seed_ik
                with patch.object(match, '_seed_ik', wraps=original_seed) as seed:
                    result = match.switch_limb(bpy.context, armature, key, 'IK', keyframe=False)
                self.assertEqual(seed.call_count, 1)
                self.assertEqual(result['mode'], 'IK')
                match._verify(armature, desired)
                match._verify(armature, reference)
                self.assertEqual({bone.name: tuple(tuple(row) for row in bone.matrix_local)
                                  for bone in armature.data.bones}, rest)
                limb_ik._validate_inventory(armature)

    def test_calibrated_seed_preserves_existing_pole(self):
        armature, _key, inventory, data = self.make()
        target = armature.pose.bones[data['target'].name]
        target[match.PROPERTY] = 1.0
        match._update(bpy.context, armature)
        desired = match._matrices(armature, match.pose_names(data))
        pole = armature.pose.bones[data['pole'].name]
        pole_before = pole.matrix_basis.copy()
        original_update = match._update
        with patch.object(match, '_update', wraps=original_update) as updates:
            accepted = match._seed_ik(bpy.context, armature, inventory, data, desired,
                                      desired[data['chain'][2]].translation.copy(), calibrated_rest=True)
        self.assertTrue(accepted)
        self.assertEqual(updates.call_count, 1)
        self.assertEqual(pole.matrix_basis, pole_before)

    def test_controller_constraints_and_transform_drivers_select_fallback(self):
        for role in ('target', 'pole'):
            for dependency in ('constraint', 'driver'):
                with self.subTest(role=role, dependency=dependency):
                    armature, _key, _inventory, data = self.make()
                    pb = armature.pose.bones[data[role].name]
                    if dependency == 'constraint':
                        pb.constraints.new('LIMIT_LOCATION')
                    else:
                        pb.driver_add('location', 0).driver.expression = '.021'
                    inventory = limb_ik._validate_inventory(armature)
                    self.assert_fallback(armature, inventory, inventory['rigs'][_key])

    def test_parent_and_object_feedback_select_fallback(self):
        for dependency in ('root_transform_driver', 'object_driver', 'object_constraint', 'object_parent'):
            with self.subTest(dependency=dependency):
                armature, key, _inventory, data = self.make(root=True)
                if dependency == 'root_transform_driver':
                    master = armature.pose.bones[data['target'].parent.name]
                    curve = master.driver_add('location', 0)
                    curve.driver.expression = 'input_position'
                    variable = curve.driver.variables.new()
                    variable.name, variable.type = 'input_position', 'SINGLE_PROP'
                    variable.targets[0].id = armature
                    variable.targets[0].data_path = armature.pose.bones[data['pole'].name].path_from_id('location') + '[0]'
                elif dependency == 'object_driver':
                    armature.driver_add('location', 0).driver.expression = '.02'
                elif dependency == 'object_constraint':
                    armature.constraints.new('LIMIT_LOCATION')
                else:
                    parent = bpy.data.objects.new('External seed-test parent', None)
                    bpy.context.scene.collection.objects.link(parent)
                    armature.parent = parent
                inventory = limb_ik._validate_inventory(armature)
                self.assert_fallback(armature, inventory, inventory['rigs'][key])

    def test_other_schemas_data_drivers_and_reverse_foot_select_fallback(self):
        armature, key, data = fixtures.build('ROLL_DECOUPLED')
        inventory = limb_ik._validate_inventory(armature)
        self.assert_fallback(armature, inventory, inventory['rigs'][key])

        armature, key, _inventory, data = self.make()
        armature.data.bones[data['target'].name].driver_add('bbone_easein').driver.expression = '.5'
        inventory = limb_ik._validate_inventory(armature)
        self.assert_fallback(armature, inventory, inventory['rigs'][key])

        armature, key, data = fixtures.build('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
        foot_controls.build(bpy.context, armature, key, toe_name='toe.L')
        inventory = limb_ik._validate_inventory(armature)
        self.assertNotEqual(inventory['rigs'][key]['solver_target'].name,
                            inventory['rigs'][key]['target'].name)
        self.assert_fallback(armature, inventory, inventory['rigs'][key])

    def test_late_failure_restores_batched_seed_and_owned_mode(self):
        armature, key, _inventory, data = self.make(root=True)
        before = _checkpoint(armature, data)
        desired = match._matrices(armature, match.pose_names(data))
        original_seed = match._seed_ik
        with patch.object(match, '_seed_ik', wraps=original_seed) as seed, \
             patch.object(match, '_match_pole_plane', side_effect=RuntimeError('Injected post-seed failure')):
            with self.assertRaisesRegex(RuntimeError, 'post-seed failure'):
                match.switch_limb(bpy.context, armature, key, 'IK', keyframe=False)
        self.assertEqual(seed.call_count, 1)
        self.assertEqual(armature.pose.bones[data['target'].name][match.PROPERTY], before['value'])
        self.assertEqual([con.mute for con, _mute in before['mutes']],
                         [mute for _con, mute in before['mutes']])
        for name, (rotation_mode, basis) in before['poses'].items():
            pb = armature.pose.bones[name]
            self.assertEqual(pb.rotation_mode, rotation_mode)
            self.assertLess(max(abs(pb.matrix_basis[i][j] - basis[i][j])
                                for i in range(4) for j in range(4)), 1e-6)
        match._verify(armature, desired)
        limb_ik._validate_inventory(armature)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DirectIKSeedTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('Direct IK seed batching native checks failed.')
    print('LIMB_IK_SEED_BATCH_PASSED', result.testsRun)
