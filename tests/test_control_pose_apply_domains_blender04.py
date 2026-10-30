"""Real Apply-operator cases for the protected Auto Key domains.

This module does not import a historical product package or launch Blender.
The final release runner supplies the already-loaded domains03 fixture module
or an equivalent SimpleNamespace of its executed globals, then adds
``make_suite(domains)`` to its native suite. This exercises execute() directly
with a real local Pose Action; only the unavailable Asset Browser context field
is provided by a transparent Context proxy. No product admission is mocked.
"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def make_suite(domains):
    import bpy
    from character_designer import control_pose_assets as poses, control_pose_blend as blend
    from character_designer import animation_retarget as retarget

    class AssetContext:
        def __init__(self, asset):
            self.asset = asset
        def __getattr__(self, name):
            return getattr(bpy.context, name)

    def asset_for(case):
        action = bpy.data.actions.new('Native Apply domain Pose')
        slot, bag = retarget._new_channelbag(action, case.rig)
        for name, fields in case.values.items():
            for prop, components in fields.items():
                for index, value in components.items():
                    retarget._write_curve(bag, case.rig.pose.bones[name].path_from_id(prop), index, [1.], [value])
        action[poses.ASSET_METADATA] = json.dumps(dict(version=1, source_object=case.rig.name,
            source_slot=slot.identifier, rest=poses.native_rest(case.rig), names=list(case.values),
            scope='SELECTED', include_fingers=False))
        action.asset_mark()
        return AssetContext(SimpleNamespace(id_type='ACTION', local_id=action,
            name=action.name, full_library_path=''))

    def execute(case, context):
        operator = SimpleNamespace(flipped=False, report=Mock())
        result = poses.CHARACTERDESIGNER_OT_apply_control_pose.execute(operator, context)
        return result, operator

    class ApplyPoseTestKeying(unittest.TestCase):
        setUp = domains.PoseTestKeying.setUp

        def test_real_apply_rotates_token_retains_old_raw_and_reset_finish_work(self):
            context = asset_for(self)
            old_raw, old_refs = self.scratch[self.pose.SESSION_KEY], dict(self.scratch[self.pose.REFS_KEY])
            old_keys = domains.curve_content(self.scratch)
            frame = bpy.context.scene.frame_current + bpy.context.scene.frame_subframe
            result, operator = execute(self, context)
            self.assertEqual(result, {'FINISHED'}, operator.report.call_args_list)
            current, record, refs = self.pose._record(bpy.context, self.rig)
            self.assertNotEqual(current, self.scratch)
            self.assertNotEqual(record['token'], self.record['token'])
            self.assertNotEqual(record['action_id'], self.record['action_id'])
            self.assertEqual(old_raw, self.scratch[self.pose.SESSION_KEY])
            self.assertEqual(old_refs, dict(self.scratch[self.pose.REFS_KEY]))
            self.assertEqual(old_keys, domains.curve_content(self.scratch))
            self.assertTrue(self.scratch.use_fake_user)
            self.assertEqual(frame, bpy.context.scene.frame_current + bpy.context.scene.frame_subframe)
            reset = self.pose.start_or_reset(bpy.context)
            self.assertEqual(reset['draft'], current)
            self.assertEqual(self.pose._record(bpy.context, self.rig)[1]['token'], record['token'])
            finish = self.pose.finish(bpy.context)
            self.assertTrue(finish['changed'])
            self.assertEqual(self.rig.animation_data.action, self.animation)
            self.assertEqual(self.rig.animation_data.action_slot, self.animation_slot)
            self.assertEqual(old_raw, self.scratch[self.pose.SESSION_KEY])
            self.assertEqual(old_refs, dict(self.scratch[self.pose.REFS_KEY]))

        def test_real_apply_key_failure_returns_cancelled_after_exact_recovery(self):
            context = asset_for(self)
            before = blend._state(bpy.context, self.rig)
            ids = {action.as_pointer() for action in bpy.data.actions}
            old_fake = self.scratch.use_fake_user
            with patch.object(poses, '_insert_key', side_effect=RuntimeError('injected Apply domain key refusal')):
                with patch.object(blend, 'cancel', wraps=blend.cancel) as cancel:
                    result, operator = execute(self, context)
            self.assertEqual(result, {'CANCELLED'})
            self.assertIn('Apply domain key refusal', operator.report.call_args.args[1])
            cancel.assert_not_called()  # confirm already closed/restored its owned session
            self.assertEqual(before, blend._state(bpy.context, self.rig))
            self.assertEqual(ids, {action.as_pointer() for action in bpy.data.actions})
            self.assertEqual(self.pose._record(bpy.context, self.rig)[0], self.scratch)
            self.assertEqual(self.scratch.use_fake_user, old_fake)

    class ApplyWorklistKeying(unittest.TestCase):
        setUp = domains.WorklistKeying.setUp
        tearDown = domains.WorklistKeying.tearDown

        def test_real_apply_publishes_custom_and_preserves_old_action_source_and_fake(self):
            context = asset_for(self)
            keys, source_keys = domains.curve_content(self.original), domains.curve_content(self.source)
            old_id = self.row.item_id
            frame = bpy.context.scene.frame_current + bpy.context.scene.frame_subframe
            result, operator = execute(self, context)
            self.assertEqual(result, {'FINISHED'}, operator.report.call_args_list)
            current = self.worklist.current(bpy.context)
            action, new_id = current.custom_action, current.item_id
            self.assertNotEqual(action, self.original)
            self.assertTrue(action.use_fake_user)
            self.assertEqual(self.row.custom_action, self.original)
            self.assertEqual(keys, domains.curve_content(self.original))
            self.assertEqual(source_keys, domains.curve_content(self.source))
            self.assertEqual(frame, bpy.context.scene.frame_current + bpy.context.scene.frame_subframe)
            self.worklist.activate(bpy.context, old_id, 'CUSTOM')
            self.assertEqual(self.rig.animation_data.action, self.original)
            self.worklist.activate(bpy.context, new_id, 'CUSTOM')
            self.assertEqual(self.rig.animation_data.action, action)

        def test_real_apply_source_refuses_without_preview_or_new_ids(self):
            context = asset_for(self)
            self.rig.animation_data.action = self.source
            self.rig.animation_data.action_slot = self.worklist._slot(self.source, self.row.source_slot)
            self.row.side = 'SOURCE'
            before = blend._state(bpy.context, self.rig)
            ids = {action.as_pointer() for action in bpy.data.actions}
            with patch.object(blend, 'preview', wraps=blend.preview) as preview:
                result, operator = execute(self, context)
            self.assertEqual(result, {'CANCELLED'})
            self.assertIn('Source is read-only', operator.report.call_args.args[1])
            preview.assert_not_called()
            self.assertEqual(before, blend._state(bpy.context, self.rig))
            self.assertEqual(ids, {action.as_pointer() for action in bpy.data.actions})

        def test_real_apply_publication_failure_rolls_back_binding_rows_and_ids(self):
            context = asset_for(self)
            before = blend._state(bpy.context, self.rig)
            ids = {action.as_pointer() for action in bpy.data.actions}
            real_publish = domains.offset.publish_variant
            def fail(*args, **kwargs):
                real_publish(*args, **kwargs)
                raise RuntimeError('injected Apply variant publication refusal')
            with patch.object(domains.offset, 'publish_variant', side_effect=fail):
                result, operator = execute(self, context)
            self.assertEqual(result, {'CANCELLED'})
            self.assertIn('Apply variant publication refusal', operator.report.call_args.args[1])
            self.assertEqual(before, blend._state(bpy.context, self.rig))
            self.assertEqual(ids, {action.as_pointer() for action in bpy.data.actions})
            self.assertEqual(len(self.saved.items), 1)
            self.assertEqual(self.rig.animation_data.action, self.original)

        def test_native_destination_fallback_remains_before_source_policy_spy(self):
            context = asset_for(self)
            self.rig.animation_data.action = self.source
            self.rig.animation_data.action_slot = self.worklist._slot(self.source, self.row.source_slot)
            self.row.side = 'SOURCE'
            with patch.object(poses, '_asset_channels', return_value=self.values):
                with patch.object(poses, '_needs_control_matching', return_value=False):
                    with patch.object(poses, '_apply_native', return_value={'FINISHED'}) as native:
                        with patch.object(blend, 'begin', wraps=blend.begin) as begin:
                            result, _operator = execute(self, context)
            self.assertEqual(result, {'FINISHED'})
            native.assert_called_once()
            begin.assert_not_called()

    suite = unittest.TestSuite()
    for cls in (ApplyPoseTestKeying, ApplyWorklistKeying):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    return suite
