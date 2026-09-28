"""Integration tests using Blender's real pose evaluation and Weight Paint modes."""
import json
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import bpy
from mathutils import Vector
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import control_pose_assets as poses, control_weight_paint as weights
from character_designer import body_setup, limb_ik, limb_ik_fk, bone_display, bone_display_sync
import test_body_calibration_blender as setup


def update(rig):
    limb_ik_fk._update(bpy.context, rig)


def matrices(rig):
    update(rig)
    return {name: rig.pose.bones[name].matrix.copy() for name in poses.native_rest(rig)}


def asset(rig, changes):
    before = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in rig.pose.bones}
    for name, value in changes.items():
        pb = rig.pose.bones[name]
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = value
        pb.keyframe_insert('rotation_euler', frame=1)
    action = rig.animation_data.action
    action.asset_mark()
    action.use_fake_user = True
    update(rig)
    expected = matrices(rig)
    rig.animation_data.action = None
    for name, (mode, basis) in before.items():
        rig.pose.bones[name].rotation_mode = mode
        rig.pose.bones[name].matrix_basis = basis
    update(rig)
    return action, expected


def original_reference(rig, action):
    """Independent Blender evaluation on an unconstrained disposable copy."""
    current = {p.name: p.matrix.copy() for p in rig.pose.bones}
    copy = rig.copy()
    copy.data = rig.data.copy()
    bpy.context.scene.collection.objects.link(copy)
    copy.animation_data_clear()
    for pb in copy.pose.bones:
        for con in list(pb.constraints):
            pb.constraints.remove(con)
    for pb in sorted(copy.pose.bones, key=lambda p: len(p.parent_recursive)):
        pb.matrix = current[pb.name]
        update(copy)
    copy.animation_data_create().action = action
    copy.animation_data.action_slot = action.slots[0]
    copy.pose.apply_pose_from_action(action, evaluation_time=1.0)
    update(copy)
    result = {n: copy.pose.bones[n].matrix.copy() for n in poses.native_rest(rig)}
    data = copy.data
    bpy.data.objects.remove(copy, do_unlink=True)
    bpy.data.armatures.remove(data)
    return result


class Workflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        if weights.active(bpy.context):
            weights.leave(bpy.context)
        self.rig, self.mesh = setup.fixture()
        setup.prepare(self.rig)

    def assertPose(self, expected, tolerance=4e-4):
        update(self.rig)
        errors = {n: poses._difference(m, self.rig.pose.bones[n].matrix) for n, m in expected.items()}
        name = max(errors, key=errors.get)
        self.assertLess(errors[name], tolerance, (name, errors[name]))

    def test_old_arm_pose_generate_apply_and_continue(self):
        rig = self.rig
        action, expected = asset(rig, {'upper_arm.L': (.08, -.1, .13), 'forearm.L': (.2, .0, .0), 'hand.L': (.05, .02, .0)})
        data_before = [(c.data_path, c.array_index, [tuple(p.co) for p in c.keyframe_points]) for c in poses._curves(action, rig)]
        rest = poses.native_rest(rig)
        body_setup.generate(bpy.context, rig)
        right = rig.pose.bones['upper_arm.R'].matrix.copy()
        result = poses.apply(bpy.context, rig, action)
        print('POSE_MATCH', result, flush=True)
        self.assertPose(expected)
        self.assertLess(poses._difference(right, rig.pose.bones['upper_arm.R'].matrix), 1e-6)
        self.assertEqual(rest, poses.native_rest(rig))
        self.assertEqual(data_before, [(c.data_path,c.array_index,[tuple(p.co) for p in c.keyframe_points]) for c in poses._curves(action,rig)])
        bpy.context.scene.frame_set(2)
        self.assertPose(expected)
        entry = limb_ik._validate_inventory(rig)['rigs'][('ARM', 'L')]
        if limb_ik_fk.mode_for_rig(rig, entry) == 'IK':
            control = rig.pose.bones[entry['target'].name]
            before = rig.pose.bones['hand.L'].matrix.translation.copy()
            control.location.x += .001
            update(rig)
            self.assertLess((rig.pose.bones['hand.L'].matrix.translation - before).length, .01)
        else:
            before = rig.pose.bones['hand.L'].matrix.copy()
            rig.pose.bones['forearm.L'].rotation_euler.x += .001
            update(rig)
            self.assertLess(poses._difference(before, rig.pose.bones['hand.L'].matrix), .01)

    def test_rebuild_keeps_pose_baseline_and_old_asset(self):
        action, expected = asset(self.rig, {'hand.R': (.13, -.07, .03)})
        body_setup.generate(bpy.context, self.rig)
        baseline = self.rig[poses.BASELINE]
        body_setup.remove(bpy.context, self.rig)
        body_setup.generate(bpy.context, self.rig)
        self.assertEqual(baseline, self.rig[poses.BASELINE])
        poses.apply(bpy.context, self.rig, action)
        self.assertPose(expected)

    def test_rest_changed_refuses_without_pose_writes(self):
        action, _ = asset(self.rig, {'hand.L': (.05, .02, .0)})
        body_setup.generate(bpy.context, self.rig)
        bpy.ops.object.mode_set(mode='EDIT')
        self.rig.data.edit_bones['hand.L'].roll += .02
        bpy.ops.object.mode_set(mode='POSE')
        before = matrices(self.rig)
        with self.assertRaisesRegex(ValueError, 'Rest or structure changed'):
            poses.apply(bpy.context, self.rig, action)
        self.assertPose(before, 1e-7)

    def test_match_failure_rolls_back_all_controls(self):
        action, _ = asset(self.rig, {'hand.L': (.12, .01, .03)})
        body_setup.generate(bpy.context, self.rig)
        before = {p.name: (p.matrix_basis.copy(), p.get('ik_fk')) for p in self.rig.pose.bones}
        real = poses._match
        def fail(*args):
            real(*args)
            raise ValueError('injected after matching')
        with patch.object(poses, '_match', side_effect=fail):
            with self.assertRaisesRegex(ValueError, 'injected'):
                poses.apply(bpy.context, self.rig, action)
        for name, (basis, value) in before.items():
            self.assertLess(poses._difference(basis, self.rig.pose.bones[name].matrix_basis), 1e-6)
            self.assertEqual(value, self.rig.pose.bones[name].get('ik_fk'))

    def test_full_body_parents_spine_feet_and_original_reference(self):
        import test_root_control_blender as full
        from character_designer import root_control, spine_ik_fk, torso_controls
        self.rig = rig = full.fixture()
        root_control.build(bpy.context, rig)
        poses.capture_baseline(rig)
        action, _ = asset(rig, {'Hips': (.08,.02,.07), 'spine': (.13,.02,-.04),
                              'UpperChest': (.07,.04,.03), 'hand.L': (.1,.05,-.02),
                              'foot.R': (.1,.03,.02), 'toe.R': (.06,0,0)})
        expected = original_reference(rig, action)
        result = poses.apply(bpy.context, rig, action)
        print('FULL_BODY_MATCH', result, flush=True)
        self.assertPose(expected)
        bpy.context.scene.frame_set(2)
        self.assertPose(expected)

    def test_foreign_slot_and_multiframe_are_rejected(self):
        action, _ = asset(self.rig, {'hand.L': (.05,0,0)})
        body_setup.generate(bpy.context, self.rig)
        action.slots[0].name_display = 'AnotherCharacter'
        with self.assertRaisesRegex(ValueError, 'another or an ambiguous rig'):
            poses.apply(bpy.context, self.rig, action)

    def test_auto_key_does_not_modify_pose_asset(self):
        action, expected = asset(self.rig, {'hand.L': (.12,0,.03)})
        body_setup.generate(bpy.context, self.rig)
        before = [(c.data_path,[tuple(p.co) for p in c.keyframe_points]) for c in poses._curves(action,self.rig)]
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        poses.apply(bpy.context, self.rig, action)
        self.assertIsNotNone(self.rig.animation_data.action)
        self.assertNotEqual(self.rig.animation_data.action, action)
        bpy.context.scene.frame_set(2)
        self.assertPose(expected)
        self.assertEqual(before, [(c.data_path,[tuple(p.co) for p in c.keyframe_points]) for c in poses._curves(action,self.rig)])

    def test_bbone_channels_are_applied_and_rolled_back(self):
        action, _ = asset(self.rig, {'hand.L': (.08,0,0)})
        self.rig.animation_data.action = action
        pb = self.rig.pose.bones['hand.L']
        pb.bbone_curveinx = .07
        pb.keyframe_insert('bbone_curveinx', frame=1)
        self.rig.animation_data.action = None
        pb.bbone_curveinx = .0
        body_setup.generate(bpy.context, self.rig)
        poses.apply(bpy.context, self.rig, action)
        self.assertAlmostEqual(pb.bbone_curveinx, .07, places=6)
        pb.bbone_curveinx = .12
        with patch.object(poses,'_auto_key',side_effect=ValueError('injected key failure')):
            with self.assertRaisesRegex(ValueError,'injected'):
                poses.apply(bpy.context,self.rig,action)
        self.assertAlmostEqual(pb.bbone_curveinx, .12, places=6)

    def test_actual_partial_key_insertion_failure_restores_action(self):
        action, _ = asset(self.rig, {'hand.L': (.18,.04,0)})
        body_setup.generate(bpy.context, self.rig)
        pb = self.rig.pose.bones['Hips']
        pb.keyframe_insert('location', frame=1)
        previous, slot = self.rig.animation_data.action, self.rig.animation_data.action_slot
        before = {p.name:p.matrix_basis.copy() for p in self.rig.pose.bones}
        curves = [(c.data_path,[tuple(p.co) for p in c.keyframe_points]) for c in poses._curves(previous,self.rig)]
        actions = set(bpy.data.actions.keys())
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        original = poses._insert_key
        count = []
        def fail(pb,path,frame):
            original(pb,path,frame)
            count.append(path)
            if len(count)==2:raise ValueError('injected after two real inserted channels')
        with patch.object(poses,'_insert_key',side_effect=fail):
            with self.assertRaisesRegex(ValueError,'after two'):
                poses.apply(bpy.context,self.rig,action)
        self.assertEqual(self.rig.animation_data.action,previous)
        self.assertEqual(self.rig.animation_data.action_slot,slot)
        self.assertEqual(actions,set(bpy.data.actions.keys()))
        self.assertEqual(curves,[(c.data_path,[tuple(p.co) for p in c.keyframe_points]) for c in poses._curves(previous,self.rig)])
        self.assertTrue(all(poses._difference(matrix,self.rig.pose.bones[n].matrix_basis)<1e-6 for n,matrix in before.items()))

    def test_external_asset_read_is_isolated(self):
        action, _ = asset(self.rig, {'hand.L': (.08,0,0)})
        body_setup.generate(bpy.context,self.rig)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'Pose.blend')
            bpy.data.libraries.write(path,{action})
            before = set(bpy.data.actions.keys()),set(bpy.data.objects.keys())
            values = poses._asset_channels(SimpleNamespace(local_id=None,name=action.name,full_library_path=path),self.rig)
            self.assertEqual(before,(set(bpy.data.actions.keys()),set(bpy.data.objects.keys())))
            poses.apply_channels(bpy.context,self.rig,values)

    def test_bbone_driver_is_rejected(self):
        action, _ = asset(self.rig, {'hand.L': (.02,0,0)})
        body_setup.generate(bpy.context,self.rig)
        pb=self.rig.pose.bones['hand.L']
        driver=pb.driver_add('bbone_curveinx').driver
        driver.expression='.12'
        update(self.rig)
        with self.assertRaisesRegex(ValueError,'driver controls'):
            poses.apply_channels(bpy.context,self.rig,{'hand.L':{'bbone_curveinx':{0:.03}}})

    def _weight_setup(self):
        body_setup.generate(bpy.context, self.rig)
        bpy.context.scene.character_designer_setup.body = self.mesh
        bpy.context.scene.character_designer_setup.rig = self.rig
        bpy.ops.object.mode_set(mode='POSE')
        entry = limb_ik._validate_inventory(self.rig)['rigs'][('ARM', 'L')]
        target = self.rig.pose.bones[entry['target'].name]
        target.location += Vector((.0, -.03, .02))
        for pb in self.rig.pose.bones:
            pb.select = pb == target
        self.rig.data.bones.active = target.bone
        update(self.rig)
        return target

    def test_weight_workspace_preserves_pose_binding_and_new_weights(self):
        target = self._weight_setup()
        before, display = matrices(self.rig), bone_display._snapshot(self.rig)
        structure = poses.native_rest(self.rig)
        controls = [(pb.name, [(c.name, c.type, c.mute, c.influence) for c in pb.constraints]) for pb in self.rig.pose.bones]
        weights.enter(bpy.context)
        self.assertEqual(bpy.context.mode, 'PAINT_WEIGHT')
        self.assertEqual(bpy.context.object, self.mesh)
        self.assertEqual(self.rig.data.bones.active.name, 'hand.L')
        self.assertTrue(all(pb.hide for pb in self.rig.pose.bones if pb.bone.get(limb_ik.OWNER_KEY) in limb_ik.GENERATED_CONTROL_OWNERS))
        bone_display_sync.sync_pending()
        self.assertFalse(self.rig.pose.bones['hand.L'].hide)
        self.assertPose(before)
        self.mesh.vertex_groups['hand.L'].add([2], .37, 'REPLACE')
        weights.leave(bpy.context)
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertEqual(self.rig.data.bones.active.name, target.name)
        self.assertEqual(display, bone_display._snapshot(self.rig))
        self.assertAlmostEqual(self.mesh.vertex_groups['hand.L'].weight(2), .37, places=6)
        self.assertEqual(structure, poses.native_rest(self.rig))
        self.assertEqual(controls, [(pb.name, [(c.name,c.type,c.mute,c.influence) for c in pb.constraints]) for pb in self.rig.pose.bones])
        self.assertPose(before)

    def test_weight_enter_failure_restores_workspace(self):
        self._weight_setup()
        display = bone_display._snapshot(self.rig)
        with patch.object(weights.paint, '_enter_pose_mode', side_effect=ValueError('injected mode failure')):
            with self.assertRaisesRegex(ValueError, 'injected'):
                weights.enter(bpy.context)
        self.assertFalse(weights.active(bpy.context))
        self.assertEqual(display, bone_display._snapshot(self.rig))
        self.assertEqual(bpy.context.mode, 'POSE')

    def test_weight_unmapped_control_and_object_mode(self):
        self._weight_setup()
        bpy.ops.object.mode_set(mode='OBJECT')
        self.rig.data.bones.active = None
        weights.enter(bpy.context)
        self.assertEqual(bpy.context.mode, 'PAINT_WEIGHT')
        self.assertIsNone(self.rig.data.bones.active)
        weights.leave(bpy.context)
        self.assertEqual(bpy.context.mode, 'OBJECT')


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise RuntimeError('Control Pose / Weights integration tests failed')
