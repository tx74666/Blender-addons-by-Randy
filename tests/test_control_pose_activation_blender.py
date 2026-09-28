"""Pose activation routing with real bones and native-operator spies.

These tests do not simulate GUI double clicks or execute the native asset
operator with a fabricated Asset Browser context. They check dispatch, the
existing compatibility guard, and menu arguments; GUI event delivery is a
separate integration check.
"""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
from character_designer import body_setup, control_pose_assets as poses, control_pose_mirror as mirror


class PoseActivationTests(unittest.TestCase):
    def setUp(self):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        data = bpy.data.armatures.new('Pose activation test')
        self.rig = bpy.data.objects.new('Pose activation test', data)
        bpy.context.scene.collection.objects.link(self.rig)
        self.rig.select_set(True)
        bpy.context.view_layer.objects.active = self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        for name, x in (('parent', 0.), ('finger.L', 1.),
                        ('finger.R', -1.), ('unrelated', 2.)):
            bone = data.edit_bones.new(name)
            bone.head = (x, 0., 0.)
            bone.tail = (x, 1., 0.)
            if name != 'parent':
                bone.parent = data.edit_bones['parent']
        bpy.ops.object.mode_set(mode='POSE')
        self.values = {'finger.L': {'rotation_euler': {0: .4, 1: -.1, 2: .2}}}
        self.context = SimpleNamespace(
            object=self.rig, mode='POSE', asset=SimpleNamespace(id_type='ACTION'),
            scene=bpy.context.scene, view_layer=bpy.context.view_layer,
            evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get,
        )
        self.native = Mock(return_value={'FINISHED'})
        self.stack = ExitStack()
        # Preserve the Blender APIs used by real routing/compatibility code,
        # replacing only the final native Asset Browser operator call.
        api = SimpleNamespace(
            ops=SimpleNamespace(poselib=SimpleNamespace(apply_pose_asset=self.native)),
            utils=bpy.utils, types=bpy.types, data=bpy.data, context=bpy.context,
            app=bpy.app,
        )
        self.stack.enter_context(patch.object(poses, 'bpy', api))

    def tearDown(self):
        self.stack.close()
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        data = self.rig.data
        bpy.data.objects.remove(self.rig, do_unlink=True)
        bpy.data.armatures.remove(data)

    def execute(self, *, flipped=False):
        operator = SimpleNamespace(flipped=flipped, report=Mock())
        result = poses.CHARACTERDESIGNER_OT_apply_control_pose.execute(operator, self.context)
        return result, operator.report

    def constrain(self, name):
        return self.rig.pose.bones[name].constraints.new('LIMIT_ROTATION')

    def basis(self):
        return {pb.name: pb.matrix_basis.copy() for pb in self.rig.pose.bones}

    def test_unconstrained_fingers_without_baseline_use_native_both_sides(self):
        self.assertNotIn(poses.BASELINE, self.rig)
        before = self.basis()
        with patch.object(body_setup, 'has_generated', return_value=True), \
             patch.object(poses, '_asset_channels', return_value=self.values), \
             patch.object(poses, 'apply_channels', side_effect=AssertionError('Unneeded control matching')):
            for flipped in (False, True):
                result, report = self.execute(flipped=flipped)
                self.assertEqual(result, {'FINISHED'})
                self.native.assert_called_with(flipped=flipped)
                report.assert_not_called()
        self.assertEqual(self.native.call_count, 2)
        self.assertNotIn(poses.BASELINE, self.rig)
        self.assertEqual(before, self.basis())

    def test_ordinary_rig_forwards_mirror_without_parsing_asset(self):
        with patch.object(body_setup, 'has_generated', return_value=False), \
             patch.object(poses, '_asset_channels', side_effect=AssertionError('Ordinary rig parsed')):
            for flipped in (False, True):
                result, report = self.execute(flipped=flipped)
                self.assertEqual(result, {'FINISHED'})
                self.native.assert_called_with(flipped=flipped)
                report.assert_not_called()
        self.assertEqual(self.native.call_count, 2)

    def test_generated_control_asset_forwards_mirror_to_native(self):
        with patch.object(body_setup, 'has_generated', return_value=True), \
             patch.object(poses, '_asset_channels', return_value=None), \
             patch.object(poses, 'apply_channels', side_effect=AssertionError('Control asset rematched')):
            for flipped in (False, True):
                result, report = self.execute(flipped=flipped)
                self.assertEqual(result, {'FINISHED'})
                self.native.assert_called_with(flipped=flipped)
                report.assert_not_called()
        self.assertEqual(self.native.call_count, 2)

    def test_constrained_target_keeps_missing_baseline_guard(self):
        self.constrain('finger.L')
        before = self.basis()
        with patch.object(body_setup, 'has_generated', return_value=True), \
             patch.object(poses, '_asset_channels', return_value=self.values):
            result, report = self.execute()
        self.assertEqual(result, {'CANCELLED'})
        self.native.assert_not_called()
        report.assert_called_once()
        self.assertIn('baseline', report.call_args.args[1].lower())
        self.assertNotIn(poses.BASELINE, self.rig)
        self.assertEqual(before, self.basis())

    def test_matching_requirement_uses_destination_bones(self):
        self.constrain('parent')
        self.constrain('unrelated')
        self.assertFalse(poses._needs_control_matching(self.rig, self.values))
        self.assertFalse(poses._needs_control_matching(self.rig, self.values, flipped=True))
        self.constrain('finger.L')
        self.assertTrue(poses._needs_control_matching(self.rig, self.values))
        self.assertFalse(poses._needs_control_matching(self.rig, self.values, flipped=True))
        self.constrain('finger.R')
        self.assertTrue(poses._needs_control_matching(self.rig, self.values, flipped=True))

    def test_missing_mirror_target_reports_instead_of_applying_original(self):
        bpy.ops.object.mode_set(mode='EDIT')
        self.rig.data.edit_bones.remove(self.rig.data.edit_bones['finger.R'])
        bpy.ops.object.mode_set(mode='POSE')
        before = self.basis()
        with patch.object(body_setup, 'has_generated', return_value=True), \
             patch.object(poses, '_asset_channels', return_value=self.values), \
             patch.object(poses, 'apply_channels', side_effect=AssertionError('Missing target matched')):
            result, report = self.execute(flipped=True)
        self.assertEqual(result, {'CANCELLED'})
        self.native.assert_not_called()
        report.assert_called_once()
        self.assertIn('finger.R', report.call_args.args[1])
        self.assertEqual(before, self.basis())

    def test_constrained_mirror_converts_before_matching(self):
        self.constrain('finger.R')
        mirrored = {'finger.R': {'rotation_euler': {0: .4, 1: .1, 2: -.2}}}
        with patch.object(body_setup, 'has_generated', return_value=True), \
             patch.object(poses, '_asset_channels', return_value=self.values), \
             patch.object(mirror, 'mirrored_channels', return_value=mirrored) as convert, \
             patch.object(poses.match, '_update'), \
             patch.object(poses, 'apply_channels') as apply:
            result, report = self.execute(flipped=True)
        self.assertEqual(result, {'FINISHED'})
        convert.assert_called_once()
        apply.assert_called_once_with(self.context, self.rig, mirrored)
        self.native.assert_not_called()
        report.assert_not_called()

    def test_native_cancelled_result_is_preserved(self):
        self.native.return_value = {'CANCELLED'}
        with patch.object(body_setup, 'has_generated', return_value=True), \
             patch.object(poses, '_asset_channels', return_value=self.values):
            result, report = self.execute(flipped=True)
        self.assertEqual(result, {'CANCELLED'})
        self.native.assert_called_once_with(flipped=True)
        report.assert_not_called()

    def test_source_only_selection_temporarily_targets_opposite_side(self):
        bones = [pb if hasattr(pb, 'select') else pb.bone for pb in self.rig.pose.bones]
        for bone in bones:
            bone.select = bone.name in {'finger.L', 'unrelated'}
        before = {bone.name: bone.select for bone in bones}
        def apply(**kwargs):
            self.assertEqual(kwargs, {'flipped': True})
            selected = {bone.name for bone in bones if bone.select}
            self.assertEqual(selected, {'finger.R', 'unrelated'})
            return {'FINISHED'}
        self.native.side_effect = apply
        result = poses._apply_native(self.context, self.values, True)
        self.assertEqual(result, {'FINISHED'})
        self.native.assert_called_once_with(flipped=True)
        self.assertEqual(before, {bone.name: bone.select for bone in bones})

    def test_existing_destination_selection_and_unrelated_bones_are_preserved(self):
        bones = [pb if hasattr(pb, 'select') else pb.bone for pb in self.rig.pose.bones]
        for bone in bones:
            bone.select = bone.name in {'finger.R', 'unrelated'}
        before = {bone.name: bone.select for bone in bones}
        def apply(**kwargs):
            self.assertEqual(kwargs, {'flipped': True})
            self.assertEqual(before, {bone.name: bone.select for bone in bones})
            return {'FINISHED'}
        self.native.side_effect = apply
        result = poses._apply_native(self.context, self.values, True)
        self.assertEqual(result, {'FINISHED'})
        self.native.assert_called_once_with(flipped=True)
        self.assertEqual(before, {bone.name: bone.select for bone in bones})

    def test_native_failure_restores_source_selection(self):
        bones = [pb if hasattr(pb, 'select') else pb.bone for pb in self.rig.pose.bones]
        for bone in bones:
            bone.select = bone.name in {'finger.L', 'unrelated'}
        before = {bone.name: bone.select for bone in bones}
        def fail(**kwargs):
            self.assertEqual(kwargs, {'flipped': True})
            selected = {bone.name for bone in bones if bone.select}
            self.assertEqual(selected, {'finger.R', 'unrelated'})
            raise RuntimeError('injected native apply failure')
        self.native.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, 'injected native apply failure'):
            poses._apply_native(self.context, self.values, True)
        self.native.assert_called_once_with(flipped=True)
        self.assertEqual(before, {bone.name: bone.select for bone in bones})

    def test_selected_center_bone_does_not_block_opposite_side_selection(self):
        bones = [pb if hasattr(pb, 'select') else pb.bone for pb in self.rig.pose.bones]
        for bone in bones:
            bone.select = bone.name in {'parent', 'finger.L', 'unrelated'}
        before = {bone.name: bone.select for bone in bones}
        values = dict(self.values, parent={'rotation_euler': {0: .1}})
        def apply(**kwargs):
            self.assertEqual(kwargs, {'flipped': True})
            selected = {bone.name for bone in bones if bone.select}
            self.assertEqual(selected, {'parent', 'finger.R', 'unrelated'})
            return {'FINISHED'}
        self.native.side_effect = apply
        result = poses._apply_native(self.context, values, True)
        self.assertEqual(result, {'FINISHED'})
        self.native.assert_called_once_with(flipped=True)
        self.assertEqual(before, {bone.name: bone.select for bone in bones})

    def test_menu_exposes_normal_and_mirror_properties(self):
        entries = []
        def operator(idname, **kwargs):
            props = SimpleNamespace()
            entries.append((idname, kwargs, props))
            return props
        menu = SimpleNamespace(layout=SimpleNamespace(operator=operator, separator=lambda: None))
        with patch.object(body_setup, 'has_generated', return_value=True):
            poses._menu(menu, self.context)
        self.assertEqual(len(entries), 2)
        self.assertEqual({entry[0] for entry in entries}, {'character_designer.apply_control_pose'})
        self.assertEqual([entry[2].flipped for entry in entries], [False, True])
        self.assertTrue(all(entry[1].get('text') for entry in entries))
        self.assertNotEqual(entries[0][1]['text'], entries[1][1]['text'])


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    print('ACTIVATION_BOUNDARY: real pose-bone routing; native operator is spied; GUI event delivery not tested.',
          flush=True)
    if not result.wasSuccessful():
        raise RuntimeError('Control pose activation tests failed')
