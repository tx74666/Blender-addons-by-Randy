"""Legacy native Pose selection on a generated Character Designer rig.

Use real Blender bones and selection state, with a spy replacing only the final
Asset Browser operator. These tests prove the wrapper's destination selection
and restoration contract; they do not prove GUI event delivery or native Action
application. The external Fist asset needs a separate live integration check.
"""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import body_setup, control_pose_assets as poses


class NativePoseSelectionTests(unittest.TestCase):
    def setUp(self):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.original_frame = (bpy.context.scene.frame_current,
                               bpy.context.scene.frame_subframe)
        self.original_auto_key = bpy.context.scene.tool_settings.use_keyframe_insert_auto
        data = bpy.data.armatures.new('Native Pose selection test')
        self.rig = bpy.data.objects.new('Native Pose selection test', data)
        bpy.context.scene.collection.objects.link(self.rig)
        self.rig.select_set(True)
        bpy.context.view_layer.objects.active = self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        names = ('root', 'forearm.L', 'forearm.R', 'index.L', 'middle.L',
                 'index.R', 'middle.R', 'hand control.L', 'unrelated')
        for index, name in enumerate(names):
            bone = data.edit_bones.new(name)
            bone.head = (float(index), 0., 0.)
            bone.tail = (float(index), 1., 0.)
            if name != 'root':
                bone.parent = data.edit_bones['root']
        bpy.ops.object.mode_set(mode='POSE')
        self.values = {
            'index.L': {'rotation_euler': {0: .65}},
            'middle.L': {'rotation_euler': {0: .8}},
        }
        self.context = SimpleNamespace(
            object=self.rig, mode='POSE',
            asset=SimpleNamespace(id_type='ACTION', local_id=None),
            scene=bpy.context.scene, view_layer=bpy.context.view_layer,
        )
        self.native = Mock(return_value={'FINISHED'})
        self.stack = ExitStack()
        api = SimpleNamespace(
            ops=SimpleNamespace(poselib=SimpleNamespace(apply_pose_asset=self.native)),
            utils=bpy.utils, types=bpy.types, data=bpy.data, context=bpy.context,
            app=bpy.app,
        )
        self.stack.enter_context(patch.object(poses, 'bpy', api))
        self.select({'forearm.L'}, active='forearm.L')

    def tearDown(self):
        try:
            self.stack.close()
            if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            data = self.rig.data
            bpy.data.objects.remove(self.rig, do_unlink=True)
            bpy.data.armatures.remove(data)
        finally:
            bpy.context.scene.frame_set(self.original_frame[0],
                                        subframe=self.original_frame[1])
            bpy.context.scene.tool_settings.use_keyframe_insert_auto = self.original_auto_key

    def selection_bones(self):
        # Blender 5.2 owns Pose selection on PoseBone; older supported versions
        # expose the equivalent artist state on Bone.
        return [pb if hasattr(pb, 'select') else pb.bone for pb in self.rig.pose.bones]

    def select(self, names, *, active=None):
        for bone in self.selection_bones():
            bone.select = bone.name in names
        self.rig.data.bones.active = self.rig.data.bones.get(active or '')

    def selected(self):
        return {bone.name for bone in self.selection_bones() if bone.select}

    def artist_state(self):
        active = self.rig.data.bones.active
        return {
            'selection': {bone.name: bone.select for bone in self.selection_bones()},
            'active_bone': active.name if active else None,
            'active_object': bpy.context.view_layer.objects.active,
            'mode': self.rig.mode,
            'frame': (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe),
            'auto_key': bpy.context.scene.tool_settings.use_keyframe_insert_auto,
            'basis': {pb.name: tuple(value for row in pb.matrix_basis for value in row)
                      for pb in self.rig.pose.bones},
        }

    def apply_and_observe(self, expected, *, flipped=False, values=None,
                          outcome=frozenset({'FINISHED'}), failure=None):
        before = self.artist_state()
        def native_apply(**kwargs):
            self.assertEqual(kwargs, {'flipped': flipped})
            self.assertEqual(self.selected(), set(expected))
            active = self.rig.data.bones.active
            self.assertEqual(active.name if active else None, before['active_bone'])
            if failure is not None:
                raise failure
            return set(outcome)
        self.native.side_effect = native_apply
        self.native.reset_mock()
        if failure is None:
            result = poses._apply_native(self.context, values or self.values, flipped)
            self.assertEqual(result, set(outcome))
        else:
            with self.assertRaisesRegex(type(failure), str(failure)):
                poses._apply_native(self.context, values or self.values, flipped)
        self.native.assert_called_once_with(flipped=flipped)
        self.assertEqual(self.artist_state(), before)

    def execute(self, *, flipped=False):
        operator = SimpleNamespace(flipped=flipped, report=Mock())
        with patch.object(body_setup, 'has_generated', return_value=True), \
             patch.object(poses, '_asset_channels', return_value=self.values), \
             patch.object(poses, 'apply_channels', side_effect=AssertionError('Unexpected control matching')):
            result = poses.CHARACTERDESIGNER_OT_apply_control_pose.execute(operator, self.context)
        return result, operator.report

    def test_unrelated_original_selection_targets_all_authored_fingers(self):
        self.select({'forearm.L', 'unrelated'}, active='forearm.L')
        self.apply_and_observe({'index.L', 'middle.L'})

    def test_unrelated_generated_control_selection_targets_all_authored_fingers(self):
        self.select({'hand control.L'}, active='hand control.L')
        self.apply_and_observe({'index.L', 'middle.L'})

    def test_generated_rig_execute_uses_the_native_selection_fallback(self):
        before = self.artist_state()
        def native_apply(**kwargs):
            self.assertEqual(kwargs, {'flipped': False})
            self.assertEqual(self.selected(), {'index.L', 'middle.L'})
            return {'FINISHED'}
        self.native.side_effect = native_apply
        result, report = self.execute()
        self.assertEqual(result, {'FINISHED'})
        self.native.assert_called_once_with(flipped=False)
        report.assert_not_called()
        self.assertEqual(self.artist_state(), before)

    def test_authored_finger_intersection_preserves_partial_selection(self):
        self.select({'index.L', 'forearm.L'}, active='index.L')
        self.apply_and_observe({'index.L', 'forearm.L'})

    def test_unflipped_opposite_finger_is_not_an_authored_intersection(self):
        self.select({'index.R'}, active='index.R')
        self.apply_and_observe({'index.L', 'middle.L'})

    def test_no_selection_keeps_native_apply_all_behavior(self):
        for flipped in (False, True):
            with self.subTest(flipped=flipped):
                self.select(set())
                self.apply_and_observe(set(), flipped=flipped)

    def test_mirrored_unrelated_selection_targets_all_destination_fingers(self):
        self.select({'forearm.L', 'hand control.L'}, active='hand control.L')
        self.apply_and_observe({'index.R', 'middle.R'}, flipped=True)

    def test_mirrored_author_subset_transfers_only_that_subset(self):
        self.select({'index.L', 'unrelated'}, active='index.L')
        self.apply_and_observe({'index.R', 'unrelated'}, flipped=True)

    def test_mirrored_destination_intersection_preserves_partial_selection(self):
        self.select({'middle.R', 'forearm.R'}, active='middle.R')
        self.apply_and_observe({'middle.R', 'forearm.R'}, flipped=True)

    def test_mirrored_both_sides_keep_existing_destination_selection(self):
        self.select({'index.L', 'middle.R', 'unrelated'}, active='index.L')
        self.apply_and_observe({'index.L', 'middle.R', 'unrelated'}, flipped=True)

    def test_center_target_intersection_does_not_expand_to_unselected_fingers(self):
        values = dict(self.values, root={'rotation_euler': {0: .1}})
        self.select({'root', 'unrelated'}, active='root')
        for flipped in (False, True):
            with self.subTest(flipped=flipped):
                self.apply_and_observe({'root', 'unrelated'}, flipped=flipped, values=values)

    def test_mirrored_author_subset_keeps_selected_center_target(self):
        values = dict(self.values, root={'rotation_euler': {0: .1}})
        self.select({'root', 'index.L', 'unrelated'}, active='root')
        self.apply_and_observe({'root', 'index.R', 'unrelated'}, flipped=True, values=values)

    def test_fallback_includes_center_channels_among_actual_destinations(self):
        values = dict(self.values, root={'rotation_euler': {0: .1}})
        for flipped, expected in ((False, {'root', 'index.L', 'middle.L'}),
                                  (True, {'root', 'index.R', 'middle.R'})):
            with self.subTest(flipped=flipped):
                self.select({'hand control.L'}, active='hand control.L')
                self.apply_and_observe(expected, flipped=flipped, values=values)

    def test_cancelled_native_call_restores_fallback_selection(self):
        for flipped, expected in ((False, {'index.L', 'middle.L'}),
                                  (True, {'index.R', 'middle.R'})):
            with self.subTest(flipped=flipped):
                self.select({'forearm.L'}, active='forearm.L')
                self.apply_and_observe(expected, flipped=flipped, outcome={'CANCELLED'})

    def test_native_exception_restores_fallback_selection(self):
        for flipped, expected in ((False, {'index.L', 'middle.L'}),
                                  (True, {'index.R', 'middle.R'})):
            with self.subTest(flipped=flipped):
                self.select({'hand control.L'}, active='hand control.L')
                self.apply_and_observe(expected, flipped=flipped,
                                       failure=RuntimeError('Injected native apply failure'))

    def test_cancelled_and_exception_restore_mirrored_author_subset(self):
        for failure in (None, RuntimeError('Injected mirror apply failure')):
            with self.subTest(failure=bool(failure)):
                self.select({'index.L', 'unrelated'}, active='index.L')
                self.apply_and_observe({'index.R', 'unrelated'}, flipped=True,
                                       outcome={'CANCELLED'}, failure=failure)

    def test_execute_native_exception_reports_and_preserves_artist_selection(self):
        before = self.artist_state()
        def native_fail(**kwargs):
            self.assertEqual(kwargs, {'flipped': False})
            self.assertEqual(self.selected(), {'index.L', 'middle.L'})
            raise RuntimeError('Injected routed native failure')
        self.native.side_effect = native_fail
        result, report = self.execute()
        self.assertEqual(result, {'CANCELLED'})
        self.native.assert_called_once_with(flipped=False)
        report.assert_called_once_with({'WARNING'}, 'Injected routed native failure')
        self.assertEqual(self.artist_state(), before)

    def test_missing_opposite_bone_cancels_before_changing_any_selection(self):
        bpy.ops.object.mode_set(mode='EDIT')
        self.rig.data.edit_bones.remove(self.rig.data.edit_bones['middle.R'])
        bpy.ops.object.mode_set(mode='POSE')
        self.select({'index.L', 'hand control.L'}, active='hand control.L')
        before = self.artist_state()
        result, report = self.execute(flipped=True)
        self.assertEqual(result, {'CANCELLED'})
        self.native.assert_not_called()
        report.assert_called_once()
        self.assertIn('middle.R', report.call_args.args[1])
        self.assertEqual(self.artist_state(), before)


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    print('NATIVE_SELECTION_BOUNDARY: real bone selection and routing; native Asset Browser '
          'operator is spied; GUI event delivery and actual Action evaluation are not tested.',
          flush=True)
    if not result.wasSuccessful():
        raise RuntimeError('Control pose native selection tests failed')
