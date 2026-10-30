"""Trusted derivative for actual Pose-test and worklist Auto Key owners.

Reuses the unchanged core04 fixture helper after approved117 is loaded. Only
the two new blend/offset modules are explicit canonical overlays. No artist,
GUI input, native Undo, FPS or performance-branch investigation is performed.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

# domains03: retain domains01/02; cover control-asset fallback before our Source gate.
from unittest.mock import patch
from types import SimpleNamespace

import bpy
from mathutils import Euler

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))
import test_control_pose_blend_service_blender as base
blend, poses, character_designer = base.blend, base.poses, base.character_designer
helper_pin = hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest()
assert helper_pin == '025784040e5506d7044af992e76dd405d66e9f9e1285c12d80a04d7d8476a666'
OFFSET = REPO / 'addons/character_designer/control_pose_offset.py'
core_pin = hashlib.sha256(base.CORE.read_bytes()).hexdigest()
offset_pin = hashlib.sha256(OFFSET.read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location('character_designer.control_pose_offset', OFFSET)
offset = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = offset
spec.loader.exec_module(offset)
character_designer.control_pose_offset = offset
assert hashlib.sha256(OFFSET.read_bytes()).hexdigest() == offset_pin


def curve_content(action):
    return tuple((layer_index, strip_index, bag.slot_handle, curve.data_path, curve.array_index,
                  tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                         point.interpolation) for point in curve.keyframe_points))
                 for layer_index, layer in enumerate(action.layers)
                 for strip_index, strip in enumerate(layer.strips)
                 for bag in strip.channelbags for curve in bag.fcurves)


class PoseTestKeying(unittest.TestCase):
    def setUp(self):
        import test_body_calibration_blender as setup
        from character_designer import body_setup, animation_pose_test as pose
        self.pose = pose
        self.rig, self.mesh = setup.fixture()
        setup.prepare(self.rig)
        body_setup.generate(bpy.context, self.rig)
        bpy.context.scene.character_designer_setup.rig = self.rig
        poses.limb_ik._mode_set(bpy.context, self.rig, 'POSE')
        poses.match.switch_limb(bpy.context, self.rig, ('ARM', 'L'), 'FK', keyframe=False)
        self.rig.pose.bones['forearm.L'].keyframe_insert('rotation_quaternion', frame=1.)
        self.animation = self.rig.animation_data.action
        self.animation_slot = self.rig.animation_data.action_slot
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        bpy.context.scene.frame_set(3, subframe=.25)
        pose.start_or_reset(bpy.context)
        self.scratch, self.record, self.refs = pose._record(bpy.context, self.rig)
        self.rig.pose.bones['forearm.L'].keyframe_insert('rotation_quaternion', frame=3.25)
        for pb in self.rig.pose.bones:
            blend._selection_bone(pb).select = False
        base.update(self.rig)
        pb = self.rig.pose.bones['forearm.L']
        local = poses._convert(pb.bone, pb.matrix, pb.parent.matrix, invert=True)
        rotation = local.to_quaternion() @ Euler((.13, .02, 0.)).to_quaternion()
        self.values = {'forearm.L': {'rotation_quaternion': dict(enumerate(rotation))}}

    def test_confirm_uses_new_token_keeps_old_recovery_and_reset_return_work(self):
        old_raw = self.scratch[self.pose.SESSION_KEY]
        old_refs = dict(self.scratch[self.pose.REFS_KEY])
        old_keys = curve_content(self.scratch)
        old_name = self.scratch.name
        frame = bpy.context.scene.frame_current + bpy.context.scene.frame_subframe
        selected = tuple(obj.as_pointer() for obj in bpy.context.selected_objects)
        session = blend.begin(bpy.context, values=self.values)
        blend.preview(bpy.context, session, .5)
        result = blend.confirm(bpy.context, session)
        current, record, refs = self.pose._record(bpy.context, self.rig)
        self.assertEqual(current, result['action'])
        self.assertNotEqual(current, self.scratch)
        self.assertNotEqual(record['token'], self.record['token'])
        self.assertNotEqual(record['action_id'], self.record['action_id'])
        self.assertEqual(record['before'], self.record['before'])
        self.assertEqual(dict(refs), dict(self.refs))
        self.assertEqual(old_raw, self.scratch[self.pose.SESSION_KEY])
        self.assertEqual(old_refs, dict(self.scratch[self.pose.REFS_KEY]))
        self.assertEqual(old_keys, curve_content(self.scratch))
        self.assertEqual(old_name, self.scratch.name)
        self.assertTrue(self.scratch.use_fake_user)
        self.assertEqual(frame, bpy.context.scene.frame_current + bpy.context.scene.frame_subframe)
        self.assertEqual(selected, tuple(obj.as_pointer() for obj in bpy.context.selected_objects))
        self.assertTrue(bpy.context.scene.tool_settings.use_keyframe_insert_auto)
        reset = self.pose.start_or_reset(bpy.context)
        reset_action, reset_record, _refs = self.pose._record(bpy.context, self.rig)
        self.assertNotEqual(reset_action, current)
        self.assertEqual(reset['draft'], current)
        self.assertEqual(reset_record['token'], record['token'])
        finish = self.pose.finish(bpy.context)
        self.assertTrue(finish['changed'])
        self.assertFalse(self.pose.active(self.rig))
        self.assertEqual(self.rig.animation_data.action, self.animation)
        self.assertEqual(self.rig.animation_data.action_slot, self.animation_slot)
        self.assertEqual(old_raw, self.scratch[self.pose.SESSION_KEY])
        self.assertEqual(old_refs, dict(self.scratch[self.pose.REFS_KEY]))

    def test_failure_restores_exact_original_carrier_and_no_duplicate_owner(self):
        before = blend._state(bpy.context, self.rig)
        actions = {action.as_pointer() for action in bpy.data.actions}
        old_fake = self.scratch.use_fake_user
        session = blend.begin(bpy.context, values=self.values)
        blend.preview(bpy.context, session, .5)
        with patch.object(poses, '_insert_key', side_effect=RuntimeError('injected domain key refusal')):
            with self.assertRaisesRegex(RuntimeError, 'domain key refusal'):
                blend.confirm(bpy.context, session)
        self.assertEqual(actions, {action.as_pointer() for action in bpy.data.actions})
        self.assertEqual(before, blend._state(bpy.context, self.rig))
        self.assertEqual(self.pose._record(bpy.context, self.rig)[0], self.scratch)
        self.assertEqual(self.scratch.use_fake_user, old_fake)


class WorklistKeying(unittest.TestCase):
    def setUp(self):
        base.FreeBlendTests.setUp(self)
        from character_designer import animation_retarget as retarget, animation_link as links
        from character_designer import animation_worklist as worklist
        self.worklist = worklist
        bpy.context.scene.character_designer_setup.rig = self.rig
        self.rig.pose.bones['finger.L'].keyframe_insert('rotation_euler', frame=1.)
        self.original = self.rig.animation_data.action
        self.original.use_fake_user = True
        self.slot = self.rig.animation_data.action_slot
        source_export = self.original.copy()
        source_export.name = 'Blend immutable Source'
        self.cache = report_path.parent / (report_path.stem + '_' + self._testMethodName + '.blend')
        self.assertFalse(self.cache.exists())
        bpy.data.libraries.write(str(self.cache), {source_export}, fake_user=True)
        with bpy.data.libraries.load(str(self.cache), link=True) as (_src, dst):
            dst.actions = [source_export.name]
        self.source = dst.actions[0]
        saved = bpy.context.scene.character_designer_animation_worklist
        saved.items.clear()
        saved.rig = self.rig
        self.row = saved.items.add()
        self.row.item_id, self.row.name, self.row.rig, self.row.side = '1' * 32, 'Walk', self.rig, 'CUSTOM'
        self.row.custom_action, self.row.custom_slot = self.original, self.slot.handle
        self.row.source_action, self.row.source_slot = self.source, self.slot.handle
        self.row.source_file = str(self.cache)
        self.row.source_hash = hashlib.sha256(self.cache.read_bytes()).hexdigest()
        self.row.link_identity = json.dumps(dict(schema=links.SCHEMA,
            linkId='2' * 32, targetGuid='3' * 32, clipGuid='4' * 32, clipLocalId=1,
            sourcePackage=str(report_path.parent / 'unused_packet.json'), sourcePackageSha256='5' * 64))
        saved.active_index = 0
        self.saved = saved
        ad = self.rig.animation_data
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.
        bpy.context.scene.frame_set(3, subframe=.25)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        base.update(self.rig)

    def tearDown(self):
        self.saved.items.clear()
        self.saved.rig = None
        base.FreeBlendTests.tearDown(self)

    def test_control_asset_routes_native_before_source_autokey_policy(self):
        self.rig.animation_data.action = self.source
        self.rig.animation_data.action_slot = self.worklist._slot(self.source, self.row.source_slot)
        self.row.side = 'SOURCE'
        before = blend._state(bpy.context, self.rig)
        actions = {action.as_pointer() for action in bpy.data.actions}
        asset = SimpleNamespace(id_type='ACTION')
        with patch.object(poses, '_asset_channels', return_value=None):
            with self.assertRaises(blend.NativeBlendRequired):
                blend.begin(bpy.context, asset)
        self.assertEqual(before, blend._state(bpy.context, self.rig))
        self.assertEqual(actions, {action.as_pointer() for action in bpy.data.actions})

    def test_source_autokey_refused_before_preview(self):
        self.rig.animation_data.action = self.source
        self.rig.animation_data.action_slot = self.worklist._slot(self.source, self.row.source_slot)
        self.row.side = 'SOURCE'
        before = blend._state(bpy.context, self.rig)
        actions = {action.as_pointer() for action in bpy.data.actions}
        with self.assertRaisesRegex(ValueError, 'Source is read-only'):
            blend.begin(bpy.context, values=self.values)
        self.assertEqual(before, blend._state(bpy.context, self.rig))
        self.assertEqual(actions, {action.as_pointer() for action in bpy.data.actions})

    def test_confirm_publishes_new_custom_at_same_subframe_and_switches(self):
        old_keys = curve_content(self.original)
        source_keys = curve_content(self.source)
        old_id = self.row.item_id
        frame = bpy.context.scene.frame_current + bpy.context.scene.frame_subframe
        old_selection = tuple(obj.as_pointer() for obj in bpy.context.selected_objects)
        session = blend.begin(bpy.context, values=self.values)
        blend.preview(bpy.context, session, .5)
        result = blend.confirm(bpy.context, session)
        item_id = result['publication']['item_id']
        self.assertTrue(result['action'].use_fake_user)
        self.assertEqual(len(self.saved.items), 2)
        self.assertEqual(self.worklist.current(bpy.context).item_id, item_id)
        self.assertEqual(self.worklist.current(bpy.context).custom_action, result['action'])
        self.assertEqual(self.rig.animation_data.action, result['action'])
        self.assertEqual(self.row.custom_action, self.original)
        self.assertEqual(old_keys, curve_content(self.original))
        self.assertEqual(source_keys, curve_content(self.source))
        self.assertEqual(frame, bpy.context.scene.frame_current + bpy.context.scene.frame_subframe)
        self.assertEqual(old_selection, tuple(obj.as_pointer() for obj in bpy.context.selected_objects))
        self.assertTrue(bpy.context.scene.tool_settings.use_keyframe_insert_auto)
        self.worklist.activate(bpy.context, old_id, 'CUSTOM')
        self.assertEqual(self.rig.animation_data.action, self.original)
        self.worklist.activate(bpy.context, item_id, 'CUSTOM')
        self.assertEqual(self.rig.animation_data.action, result['action'])

    def test_staged_copy_failure_restores_binding_rows_pose_and_owned_ids(self):
        before = blend._state(bpy.context, self.rig)
        actions = {action.as_pointer() for action in bpy.data.actions}
        session = blend.begin(bpy.context, values=self.values)
        blend.preview(bpy.context, session, .5)
        real_copy = offset.new_variant_action
        def fail(plan):
            real_copy(plan)
            raise RuntimeError('injected after staged copy before return')
        with patch.object(offset, 'new_variant_action', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'staged copy before return'):
                blend.confirm(bpy.context, session)
        self.assertEqual(actions, {action.as_pointer() for action in bpy.data.actions})
        self.assertEqual(len(self.saved.items), 1)
        self.assertEqual(before, blend._state(bpy.context, self.rig))

    def test_publication_failure_restores_binding_rows_pose_and_owned_ids(self):
        before = blend._state(bpy.context, self.rig)
        actions = {action.as_pointer() for action in bpy.data.actions}
        session = blend.begin(bpy.context, values=self.values)
        blend.preview(bpy.context, session, .5)
        real_publish = offset.publish_variant
        def fail(*args, **kwargs):
            real_publish(*args, **kwargs)
            raise RuntimeError('injected after keyed variant publication')
        with patch.object(offset, 'publish_variant', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'keyed variant publication'):
                blend.confirm(bpy.context, session)
        self.assertEqual(actions, {action.as_pointer() for action in bpy.data.actions})
        self.assertEqual(len(self.saved.items), 1)
        self.assertEqual(before, blend._state(bpy.context, self.rig))


if __name__ == '__main__':
    tail = sys.argv[sys.argv.index('--') + 1:]
    report_path = Path(tail[tail.index('--report') + 1])
    assert not report_path.exists()
    character_designer.register()
    result = None
    try:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    finally:
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        character_designer.unregister()
        report_path.write_text(json.dumps({
            'passed': bool(result and result.wasSuccessful()), 'tests_run': result.testsRun if result else 0,
            'errors': [(str(case), error) for case, error in result.errors] if result else [],
            'failures': [(str(case), error) for case, error in result.failures] if result else [],
            'package': str(base.PACKAGE), 'projection_sha256': base.PIN, 'base_harness_sha256': helper_pin,
            'core_sha256': core_pin, 'offset_sha256': offset_pin,
            'source_unchanged': hashlib.sha256(base.CORE.read_bytes()).hexdigest() == core_pin and hashlib.sha256(OFFSET.read_bytes()).hexdigest() == offset_pin,
            'limits': 'Real scratch-owner and ordinary linked-Source worklist fixtures; not artist/GUI Undo/FPS acceptance.'},
            indent=2) + '\n', encoding='utf8')
    if not result or not result.wasSuccessful():
        raise RuntimeError('Pose blend domain tests failed; original evidence retained.')
