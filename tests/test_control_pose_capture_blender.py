"""Isolated Blender tests for evaluated native Pose capture and control reuse.

These exercise real dependency-graph evaluation and Action data. Library export,
Asset Browser GUI events and the artist's current scene are outside this suite.
"""
import ast
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__' and '--presentation-only' in sys.argv:
    # This entry point runs the browser boundary without importing Blender.
    source = Path(__file__).read_text(encoding='utf-8')
    tree = ast.parse(source, filename=__file__)
    tests = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == 'BrowserPresentationTests')
    exec(compile(ast.Module(body=[tests], type_ignores=[]), __file__, 'exec'))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BrowserPresentationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert 'bpy' not in sys.modules and 'character_designer' not in sys.modules
    print('BROWSER_PRESENTATION_BOUNDARY: pure-Python spies; native GUI runtime not tested.', flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)

import bpy
from mathutils import Vector

sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import body_calibration, body_original_mode as original
from character_designer import body_setup, bone_collections, bone_display, control_pose_assets as poses
from character_designer import control_pose_mirror as mirror
from character_designer import control_pose_capture as capture, limb_ik, limb_ik_fk
import test_body_calibration_blender as setup
import test_limb_ik_fk_blender as legacy


def update(rig):
    limb_ik_fk._update(bpy.context, rig)


def evaluated(rig):
    """Read evaluated matrices, including the current generated IK result."""
    update(rig)
    obj = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {name: obj.pose.bones[name].matrix.copy() for name in poses.native_rest(rig)}


def selection(rig, names, active=None):
    names = set(names)
    for pb in rig.pose.bones:
        (pb if hasattr(pb, 'select') else pb.bone).select = pb.name in names
    rig.data.bones.active = rig.data.bones.get(active or next(iter(names), ''))


def curve_state(action, rig):
    if action is None:
        return ()
    return tuple((c.data_path, c.array_index, c.mute,
                  tuple((tuple(p.co), p.interpolation,
                         tuple(p.handle_left), tuple(p.handle_right)) for p in c.keyframe_points))
                 for c in poses._curves(action, rig))


def workspace(rig):
    """Artist state whose preservation is independent of capture internals."""
    update(rig)
    animation = rig.animation_data
    action = animation.action if animation else None
    return {
        'mode': bpy.context.mode,
        'active_object': bpy.context.view_layer.objects.active,
        'selected_objects': tuple(sorted(o.name for o in bpy.context.selected_objects)),
        'active_bone': rig.data.bones.active.name if rig.data.bones.active else None,
        'selected_bones': tuple(sorted(p.name for p in rig.pose.bones
                                      if (p if hasattr(p, 'select') else p.bone).select)),
        'action': action,
        'slot': animation.action_slot.identifier if animation and animation.action_slot else None,
        'keys': curve_state(action, rig),
        'auto_key': bpy.context.scene.tool_settings.use_keyframe_insert_auto,
        'frame': (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe),
        'channels': {p.name: (p.rotation_mode, tuple(p.location), tuple(p.rotation_euler),
                              tuple(p.rotation_quaternion), tuple(p.rotation_axis_angle),
                              tuple(p.scale), p.get('ik_fk')) for p in rig.pose.bones},
        'constraints': tuple((p.name, c.name, c.mute, c.influence)
                             for p in rig.pose.bones for c in p.constraints),
        'original': rig.get(original.SESSION),
        'rest': poses.native_rest(rig),
        'objects': tuple(sorted(bpy.data.objects.keys())),
        'armatures': tuple(sorted(bpy.data.armatures.keys())),
    }


def native_action_reference(rig, action):
    """Evaluate the saved channels with Blender on a disposable FK copy.

    This does not call Character Designer's desired-pose conversion/matcher.
    Complete quaternion capture also needs its matching native rotation mode.
    """
    current = evaluated(rig)
    copy = rig.copy()
    copy.data = rig.data.copy()
    bpy.context.scene.collection.objects.link(copy)
    try:
        copy.animation_data_clear()
        for pb in copy.pose.bones:
            for constraint in list(pb.constraints):
                pb.constraints.remove(constraint)
        for name in sorted(current, key=lambda n: len(copy.pose.bones[n].parent_recursive)):
            copy.pose.bones[name].matrix = current[name]
            update(copy)
        for name in json.loads(action[capture.METADATA])['names']:
            copy.pose.bones[name].rotation_mode = 'QUATERNION'
        copy.animation_data_create().action = action
        copy.animation_data.action_slot = action.slots[0]
        copy.pose.apply_pose_from_action(action, evaluation_time=1.)
        update(copy)
        return {name: copy.pose.bones[name].matrix.copy() for name in current}
    finally:
        data = copy.data
        bpy.data.objects.remove(copy, do_unlink=True)
        bpy.data.armatures.remove(data)


class CaptureWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        bpy.context.scene.frame_set(1)
        self.actions_before = set(bpy.data.actions.keys())
        self.rig, self.mesh = setup.fixture()
        setup.prepare(self.rig)
        body_setup.generate(bpy.context, self.rig)
        limb_ik._mode_set(bpy.context, self.rig, 'POSE')

    def tearDown(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        if original.active(self.rig):
            # Test failures must not leave a session in the next fixture.
            del self.rig[original.SESSION]
        if self.rig.animation_data:
            self.rig.animation_data.action = None
        for action in list(bpy.data.actions):
            if action.name not in self.actions_before and action.users <= int(action.use_fake_user):
                bpy.data.actions.remove(action)

    def arm(self, side='L'):
        return limb_ik._validate_inventory(self.rig)['rigs'][('ARM', side)]

    def select_arm(self, side='L'):
        entry = self.arm(side)
        selection(self.rig, [entry['target'].name], entry['target'].name)
        return entry

    def pose_ik(self):
        entry = self.select_arm()
        target = self.rig.pose.bones[entry['target'].name]
        target.location += Vector((-.025, -.035, .03))
        target.rotation_mode = 'XYZ'
        target.rotation_euler = (.1, -.06, .04)
        update(self.rig)
        self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, entry), 'IK')
        return entry

    def save(self, name='Saved arm', **kwargs):
        return capture.save_pose(bpy.context, self.rig, name, scope='SELECTED', **kwargs)

    def assertPose(self, expected, tolerance=4e-4):
        actual = evaluated(self.rig)
        errors = {name: poses._difference(matrix, actual[name]) for name, matrix in expected.items()}
        self.assertLess(max(errors.values()), tolerance, errors)

    def disturb(self, mode):
        entry = self.arm()
        limb_ik_fk.switch_limb(bpy.context, self.rig, ('ARM', 'L'), mode, keyframe=False)
        if mode == 'IK':
            self.rig.pose.bones[entry['target'].name].location += Vector((-.025, .02, -.02))
        else:
            pb = self.rig.pose.bones['forearm.L']
            pb.rotation_mode = 'XYZ'
            pb.rotation_euler.x += .13
        update(self.rig)

    def test_ik_capture_records_evaluated_native_pose_with_native_blender_reference(self):
        entry = self.pose_ik()
        wanted = evaluated(self.rig)
        dormant = {n: self.rig.pose.bones[n].matrix_basis.copy() for n in entry['chain']}
        action = self.save()
        metadata = json.loads(action[capture.METADATA])
        self.assertEqual(metadata['version'], 1)
        self.assertEqual(metadata['source_object'], self.rig.name)
        self.assertEqual(metadata['source_slot'], action.slots[0].identifier)
        self.assertEqual(metadata['rest'], poses.native_rest(self.rig))
        self.assertEqual(set(metadata['names']), set(entry['chain']))
        self.assertEqual(metadata['control_modes']['ARM/L']['mode'], 'IK')
        self.assertTrue(action.asset_data)
        self.assertEqual(set(poses.channels(action, self.rig)), set(entry['chain']))
        self.assertEqual(dormant, {n: self.rig.pose.bones[n].matrix_basis for n in dormant})
        reference = native_action_reference(self.rig, action)
        self.assertLess(max(poses._difference(wanted[n], reference[n]) for n in wanted), 4e-4)

    def test_ik_capture_applies_in_fk_and_fk_control_can_continue(self):
        self.pose_ik()
        wanted = evaluated(self.rig)
        action = self.save()
        self.disturb('FK')
        poses.apply(bpy.context, self.rig, action)
        self.assertPose(wanted)
        self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, self.arm()), 'FK')
        before = evaluated(self.rig)['hand.L']
        self.rig.pose.bones['forearm.L'].rotation_euler.x += .002
        after = evaluated(self.rig)['hand.L']
        delta = poses._difference(before, after)
        self.assertGreater(delta, 1e-5)
        self.assertLess(delta, .01)

    def test_ik_capture_applies_in_ik_across_native_rotation_mode_and_target_continues(self):
        self.pose_ik()
        wanted = evaluated(self.rig)
        action = self.save()
        self.disturb('IK')
        for name in self.arm()['chain']:
            self.rig.pose.bones[name].rotation_mode = 'ZXY'
        poses.apply(bpy.context, self.rig, action)
        self.assertPose(wanted)
        entry = self.arm()
        self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, entry), 'IK')
        before = evaluated(self.rig)['hand.L'].translation.copy()
        self.rig.pose.bones[entry['target'].name].location.x += .002
        delta = (evaluated(self.rig)['hand.L'].translation - before).length
        self.assertGreater(delta, 1e-5)
        self.assertLess(delta, .01)

    def test_fk_capture_applies_in_current_ik(self):
        self.pose_ik()
        limb_ik_fk.switch_limb(bpy.context, self.rig, ('ARM', 'L'), 'FK', keyframe=False)
        selection(self.rig, ['forearm.L'], 'forearm.L')
        wanted = evaluated(self.rig)
        action = self.save('Saved FK arm')
        self.assertEqual(json.loads(action[capture.METADATA])['control_modes']['ARM/L']['mode'], 'FK')
        self.disturb('IK')
        poses.apply(bpy.context, self.rig, action)
        self.assertPose(wanted)
        self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, self.arm()), 'IK')

    def test_original_capture_and_apply_keep_original_session_and_return_pose(self):
        limb_ik_fk.switch_limb(bpy.context, self.rig, ('ARM', 'L'), 'FK', keyframe=False)
        original.enter(bpy.context, self.rig)
        pb = self.rig.pose.bones['forearm.L']
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler.x += .12
        selection(self.rig, ['forearm.L'], 'forearm.L')
        wanted = evaluated(self.rig)
        session = self.rig[original.SESSION]
        action = self.save('Saved Original arm')
        self.assertEqual(session, self.rig[original.SESSION])
        pb.rotation_euler.x -= .2
        update(self.rig)
        display = bone_display._snapshot(self.rig)
        before = workspace(self.rig)
        poses.apply(bpy.context, self.rig, action)
        self.assertPose(wanted)
        self.assertTrue(original.active(self.rig))
        self.assertEqual(display, bone_display._snapshot(self.rig))
        after = workspace(self.rig)
        for key in ('mode', 'active_object', 'selected_objects', 'active_bone',
                    'selected_bones', 'action', 'slot', 'keys', 'auto_key', 'constraints', 'rest'):
            self.assertEqual(before[key], after[key], key)
        saved, current = json.loads(session), json.loads(self.rig[original.SESSION])
        for key in ('version', 'rest', 'bones', 'names', 'constraints', 'locks',
                    'display', 'native_groups', 'extra_display', 'extra_bones', 'dress_edit'):
            self.assertEqual(saved[key], current[key], key)
        original.leave(bpy.context, self.rig)
        self.assertPose(wanted)

    def test_managed_mirror_wrapper_uses_asset_rest_without_legacy_baseline(self):
        self.pose_ik()
        action = self.save('Managed mirrored arm')
        curves = curve_state(action, self.rig)
        meta = poses.asset_metadata(action)
        values = poses.channels(action, self.rig)
        del self.rig[poses.BASELINE]
        mirrored = mirror.mirrored_channels(self.rig, values, metadata=meta)
        wanted = poses.desired_pose(self.rig, mirrored, metadata=meta)
        before = workspace(self.rig)
        left = {name: matrix for name, matrix in evaluated(self.rig).items() if name.endswith('.L')}
        context = SimpleNamespace(
            object=self.rig, mode='POSE',
            asset=SimpleNamespace(id_type='ACTION', local_id=action, name=action.name,
                                  full_library_path=''),
            scene=bpy.context.scene, view_layer=bpy.context.view_layer,
            evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get,
        )
        operator = SimpleNamespace(flipped=True, report=Mock())
        result = poses.CHARACTERDESIGNER_OT_apply_control_pose.execute(operator, context)
        self.assertEqual(result, {'FINISHED'}, operator.report.call_args_list)
        self.assertPose(wanted)
        self.assertPose(left, tolerance=2e-6)
        after = workspace(self.rig)
        for key in ('mode', 'active_object', 'selected_objects', 'active_bone', 'selected_bones',
                    'action', 'slot', 'keys', 'auto_key', 'frame', 'rest', 'objects', 'armatures'):
            self.assertEqual(before[key], after[key], key)
        self.assertNotIn(poses.BASELINE, self.rig)
        self.assertEqual(curves, curve_state(action, self.rig))
        self.assertTrue(all('WARNING' not in call.args[0] for call in operator.report.call_args_list))

    def test_native_source_target_and_pole_capture_identical_native_regions(self):
        entry = self.pose_ik()
        recorded = []
        for source in ('forearm.L', entry['target'].name, entry['pole'].name):
            selection(self.rig, [source], source)
            action = self.save('Selection ' + source)
            values = poses.channels(action, self.rig)
            self.assertEqual(set(values), set(entry['chain']))
            recorded.append(values)
        for values in recorded[1:]:
            self.assertEqual(set(recorded[0]), set(values))
            for name, fields in recorded[0].items():
                self.assertEqual(set(fields), set(values[name]))
                for prop, components in fields.items():
                    self.assertEqual(set(components), set(values[name][prop]))
                    for index, value in components.items():
                        self.assertAlmostEqual(value, values[name][prop][index], places=6)

    def test_identical_visible_fk_pose_repairs_dormant_ik_target_before_reuse(self):
        entry = self.pose_ik()
        action = self.save()
        wanted = evaluated(self.rig)
        limb_ik_fk.switch_limb(bpy.context, self.rig, ('ARM', 'L'), 'FK', keyframe=False)
        target = self.rig.pose.bones[entry['target'].name]
        target.location += Vector((.2, .1, -.12))
        stale = target.matrix_basis.copy()
        self.assertPose(wanted)
        poses.apply(bpy.context, self.rig, action)
        self.assertPose(wanted)
        self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, self.arm()), 'FK')
        self.assertGreater(poses._difference(stale, target.matrix_basis), .01)
        # Inspect the matched dormant graph directly. Calling Switch with Match
        # here would repair an implementation that failed to synchronize it.
        target[limb_ik_fk.PROPERTY] = 1.
        self.assertPose(wanted)
        self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, self.arm()), 'IK')

    def test_saved_asset_survives_generated_control_rebuild_without_rest_changes(self):
        # Use the existing stable rig fixture and its supported Rebuild operator.
        # Calibrated Remove -> Generate has a separate Body-collection lifecycle
        # failure; it is outside this asset compatibility regression.
        self.rig, _key, _entry = legacy.build('ROLL_DECOUPLED', 'LEFT_ARM')
        self.mesh = None
        bone_collections.simplify_body_collections(self.rig)
        poses.capture_baseline(self.rig)
        self.pose_ik()
        wanted = evaluated(self.rig)
        action = self.save()
        rest = poses.native_rest(self.rig)
        baseline = self.rig[poses.BASELINE]
        curves = curve_state(action, self.rig)
        before_ids = {entry['rig_id'] for entry in limb_ik._validate_inventory(self.rig)['rigs'].values()}
        result, settings = setup.base.analyze(self.rig)
        self.assertEqual(result, {'FINISHED'}, settings.last_message)
        self.assertEqual(bpy.ops.character_designer.limb_ik_rebuild(), {'FINISHED'}, settings.last_message)
        after_ids = {entry['rig_id'] for entry in limb_ik._validate_inventory(self.rig)['rigs'].values()}
        self.assertFalse(before_ids.intersection(after_ids))
        self.assertEqual(rest, poses.native_rest(self.rig))
        self.assertEqual(baseline, self.rig[poses.BASELINE])
        self.disturb('FK')
        poses.apply(bpy.context, self.rig, action)
        self.assertPose(wanted)
        self.assertEqual(curves, curve_state(action, self.rig))

    def test_selected_arm_includes_whole_chain_and_fingers_are_optional(self):
        body_setup.remove(bpy.context, self.rig)
        # Separate fixture preparation keeps authoring Rest consistent with the
        # generation baseline; this is not a post-generation skeleton edit.
        self.rig, self.mesh = setup.fixture()
        limb_ik._mode_set(bpy.context, self.rig, 'EDIT')
        for side, sign in (('L', 1.), ('R', -1.)):
            parent = self.rig.data.edit_bones['hand.' + side]
            for index in (1, 2):
                pb = self.rig.data.edit_bones.new(f'f_index.{index:02d}.{side}')
                pb.head = parent.tail
                pb.tail = pb.head + Vector((.055 * sign, 0., 0.))
                pb.parent = parent
                pb.use_connect = True
                parent = pb
        limb_ik._mode_set(bpy.context, self.rig, 'OBJECT')
        body_calibration.settings(self.rig).include_fingers = False
        setup.prepare(self.rig)
        body_setup.generate(bpy.context, self.rig)
        limb_ik._mode_set(bpy.context, self.rig, 'POSE')
        entry = self.select_arm()
        plain = self.save('Arm only', include_fingers=False)
        fingers = self.save('Arm and fingers', include_fingers=True)
        self.assertEqual(set(poses.channels(plain, self.rig)), set(entry['chain']))
        self.assertEqual(set(poses.channels(fingers, self.rig)),
                         set(entry['chain']) | {'f_index.01.L', 'f_index.02.L'})
        self.assertFalse(any(name.endswith('.R') for name in poses.channels(fingers, self.rig)))

    def test_save_preserves_animation_selection_pose_and_autokey_with_one_asset(self):
        entry = self.pose_ik()
        target = self.rig.pose.bones[entry['target'].name]
        target.keyframe_insert('location', frame=1)
        target.location.x += .004
        target.keyframe_insert('location', frame=7)
        bpy.context.scene.frame_set(1)
        selection(self.rig, [entry['target'].name, entry['pole'].name], entry['target'].name)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        before = workspace(self.rig)
        before_pose = evaluated(self.rig)
        actions = set(bpy.data.actions.keys())
        assets = {a.name for a in bpy.data.actions if a.asset_data}
        action = self.save('Capture with existing animation')
        self.assertEqual(before, workspace(self.rig))
        self.assertPose(before_pose, tolerance=2e-6)
        self.assertEqual(set(bpy.data.actions.keys()) - actions, {action.name})
        self.assertEqual({a.name for a in bpy.data.actions if a.asset_data} - assets, {action.name})
        self.assertIsNot(action, self.rig.animation_data.action)

    def test_changed_authoring_rest_refuses_apply_without_mutation(self):
        self.pose_ik()
        action = self.save()
        limb_ik._mode_set(bpy.context, self.rig, 'EDIT')
        self.rig.data.edit_bones['Chest'].roll += .04
        limb_ik._mode_set(bpy.context, self.rig, 'POSE')
        # A refreshed generation baseline cannot bless an asset's older Rest.
        self.rig[poses.BASELINE] = json.dumps({'version': 1, 'object': self.rig.name,
                                              'rest': poses.native_rest(self.rig)})
        before = workspace(self.rig)
        actions = set(bpy.data.actions.keys())
        with self.assertRaisesRegex(ValueError, '[Rr]est|skeleton|structure'):
            poses.apply(bpy.context, self.rig, action)
        self.assertEqual(before, workspace(self.rig))
        self.assertEqual(actions, set(bpy.data.actions.keys()))

    def test_managed_asset_mixed_channels_or_generated_bone_metadata_are_rejected(self):
        entry = self.pose_ik()
        action = self.save()
        for corruption in ('object_channel', 'generated_channel', 'generated_metadata'):
            bad = action.copy()
            try:
                bag = bad.layers[0].strips[0].channelbag(bad.slots[0], ensure=True)
                if corruption == 'object_channel':
                    bag.fcurves.new(data_path='location', index=0).keyframe_points.insert(1., .1)
                elif corruption == 'generated_channel':
                    path = self.rig.pose.bones[entry['target'].name].path_from_id('location')
                    bag.fcurves.new(data_path=path, index=0).keyframe_points.insert(1., .1)
                else:
                    metadata = json.loads(bad[capture.METADATA])
                    metadata['names'].append(entry['target'].name)
                    bad[capture.METADATA] = json.dumps(metadata)
                before = workspace(self.rig)
                with self.subTest(corruption=corruption), self.assertRaises(ValueError):
                    poses.apply(bpy.context, self.rig, bad)
                self.assertEqual(before, workspace(self.rig))
            finally:
                bpy.data.actions.remove(bad)

    def test_empty_selection_failure_creates_no_asset_and_keeps_workspace(self):
        selection(self.rig, [])
        before = workspace(self.rig)
        actions = set(bpy.data.actions.keys())
        with self.assertRaises(ValueError):
            self.save()
        self.assertEqual(before, workspace(self.rig))
        self.assertEqual(actions, set(bpy.data.actions.keys()))

    def test_creation_failure_removes_partial_action_and_preserves_artist_state(self):
        entry = self.pose_ik()
        self.rig.pose.bones[entry['target'].name].keyframe_insert('location', frame=1)
        before = workspace(self.rig)
        actions = set(bpy.data.actions.keys())
        with patch.object(capture.json, 'dumps',
                          side_effect=ValueError('injected metadata serialization failure')):
            with self.assertRaisesRegex(ValueError, 'injected metadata'):
                self.save('Asset that cannot be finalized')
        self.assertEqual(before, workspace(self.rig))
        self.assertEqual(actions, set(bpy.data.actions.keys()))

    def _managed_active_action_pair(self):
        """A real active managed source plus a different saved destination pose."""
        self.pose_ik()
        source = self.save('Managed active source')
        self.disturb('FK')
        expected = evaluated(self.rig)
        destination = self.save('Different managed destination')
        # Complete quaternion channels need a matching native rotation mode
        # when the source is deliberately assigned as an animation Action.
        for name in json.loads(source[capture.METADATA])['names']:
            self.rig.pose.bones[name].rotation_mode = 'QUATERNION'
        animation = self.rig.animation_data_create()
        animation.action = source
        animation.action_slot = source.slots[0]
        bpy.context.scene.frame_set(8)
        update(self.rig)
        return source, destination, expected

    def test_autokey_managed_active_source_becomes_plain_animation_without_asset_metadata(self):
        source, destination, expected = self._managed_active_action_pair()
        source_metadata = source[capture.METADATA]
        source_curves = curve_state(source, self.rig)
        destination_metadata = destination[capture.METADATA]
        destination_curves = curve_state(destination, self.rig)
        source_fake = source.use_fake_user
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        existing_actions = set(bpy.data.actions.keys())

        poses.apply(bpy.context, self.rig, destination)

        staged = self.rig.animation_data.action
        self.assertIsNot(staged, source)
        self.assertIsNot(staged, destination)
        self.assertEqual(set(bpy.data.actions.keys()) - existing_actions, {staged.name})
        self.assertIsNone(staged.asset_data)
        self.assertNotIn(capture.METADATA, staged)
        self.assertEqual(source_metadata, source[capture.METADATA])
        self.assertEqual(source_curves, curve_state(source, self.rig))
        self.assertEqual(source_fake, source.use_fake_user)
        self.assertIsNotNone(source.asset_data)
        self.assertEqual(destination_metadata, destination[capture.METADATA])
        self.assertEqual(destination_curves, curve_state(destination, self.rig))
        frames = {float(point.co.x) for curve in poses._curves(staged, self.rig)
                  for point in curve.keyframe_points}
        self.assertIn(1., frames)
        self.assertIn(8., frames)
        self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, self.arm()), 'FK')
        self.assertPose(expected)
        # Subsequent ordinary animation evaluation must retain the result.
        bpy.context.scene.frame_set(9)
        self.assertPose(expected)

    def test_managed_autokey_two_real_key_insertions_then_failure_restore_everything(self):
        source, destination, _expected = self._managed_active_action_pair()
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        before = workspace(self.rig)
        before_pose = evaluated(self.rig)
        before_display = bone_display._snapshot(self.rig)
        actions = set(bpy.data.actions.keys())
        source_metadata = source[capture.METADATA]
        source_curves = curve_state(source, self.rig)
        destination_metadata = destination[capture.METADATA]
        destination_curves = curve_state(destination, self.rig)
        insert = poses._insert_key
        inserted = []

        def fail_after_real_insert(pb, path, frame):
            insert(pb, path, frame)
            inserted.append((pb.name, path, frame))
            if len(inserted) == 2:
                raise ValueError('injected after two real managed animation keys')

        with patch.object(poses, '_insert_key', side_effect=fail_after_real_insert):
            with self.assertRaisesRegex(ValueError, 'two real managed animation keys'):
                poses.apply(bpy.context, self.rig, destination)

        self.assertEqual(len(inserted), 2)
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        self.assertEqual(before, workspace(self.rig))
        self.assertEqual(before_display, bone_display._snapshot(self.rig))
        self.assertPose(before_pose, tolerance=2e-6)
        self.assertEqual(source_metadata, source[capture.METADATA])
        self.assertEqual(source_curves, curve_state(source, self.rig))
        self.assertEqual(destination_metadata, destination[capture.METADATA])
        self.assertEqual(destination_curves, curve_state(destination, self.rig))

    def test_managed_apply_failure_after_real_ik_to_fk_stage_restores_native_display(self):
        self.pose_ik()
        action = self.save('Managed pose for failed IK handoff')
        self.disturb('IK')
        before = workspace(self.rig)
        before_pose = evaluated(self.rig)
        before_display = bone_display._snapshot(self.rig)
        actions = set(bpy.data.actions.keys())
        metadata = action[capture.METADATA]
        curves = curve_state(action, self.rig)
        switch = limb_ik_fk.switch_limb
        completed_fk = []

        def fail_after_fk_handoff(*args, **kwargs):
            result = switch(*args, **kwargs)
            requested_mode = kwargs.get('mode', args[3] if len(args) > 3 else None)
            if requested_mode == 'FK':
                completed_fk.append(result)
                self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, self.arm()), 'FK')
                raise ValueError('injected after real IK to FK display handoff')
            return result

        with patch.object(limb_ik_fk, 'switch_limb', side_effect=fail_after_fk_handoff):
            with self.assertRaisesRegex(ValueError, 'real IK to FK display handoff'):
                poses.apply(bpy.context, self.rig, action)

        self.assertEqual(len(completed_fk), 1)
        self.assertEqual(limb_ik_fk.mode_for_rig(self.rig, self.arm()), 'IK')
        self.assertEqual(before, workspace(self.rig))
        self.assertEqual(before_display, bone_display._snapshot(self.rig))
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        self.assertPose(before_pose, tolerance=2e-6)
        self.assertEqual(metadata, action[capture.METADATA])
        self.assertEqual(curves, curve_state(action, self.rig))

    def test_failure_after_original_checkpoint_publication_restores_exact_session(self):
        self.pose_ik()
        action = self.save('Managed target before Original')
        # Original enters a different FK pose, so successful checkpoint
        # publication is observably different from its preceding raw text.
        self.disturb('FK')
        original.enter(bpy.context, self.rig)
        pb = self.rig.pose.bones['forearm.L']
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler.x += .04
        update(self.rig)
        session = self.rig[original.SESSION]
        before = workspace(self.rig)
        before_pose = evaluated(self.rig)
        before_display = bone_display._snapshot(self.rig)
        actions = set(bpy.data.actions.keys())
        metadata = action[capture.METADATA]
        curves = curve_state(action, self.rig)
        observed_publication = []

        def fail_after_publication(*_args, **_kwargs):
            self.assertTrue(original.active(self.rig))
            self.assertNotEqual(session, self.rig[original.SESSION])
            observed_publication.append(self.rig[original.SESSION])
            raise ValueError('injected after Original checkpoint publication')

        with patch.object(poses, '_auto_key', side_effect=fail_after_publication):
            with self.assertRaisesRegex(ValueError, 'Original checkpoint publication'):
                poses.apply(bpy.context, self.rig, action)

        self.assertEqual(len(observed_publication), 1)
        self.assertTrue(original.active(self.rig))
        self.assertEqual(session, self.rig[original.SESSION])
        self.assertEqual(before, workspace(self.rig))
        self.assertEqual(before_display, bone_display._snapshot(self.rig))
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        self.assertPose(before_pose, tolerance=2e-6)
        self.assertEqual(metadata, action[capture.METADATA])
        self.assertEqual(curves, curve_state(action, self.rig))
        # The recovered return checkpoint must remain operational.
        original.leave(bpy.context, self.rig)
        self.assertPose(before_pose)

    def _assert_user_switch_failure_keeps_workspace(self, before):
        """Matrix assignment may round-trip floats; every channel is checked."""
        after = workspace(self.rig)
        for key in before:
            if key != 'channels':
                self.assertEqual(before[key], after[key], key)
        self.assertEqual(set(before['channels']), set(after['channels']))
        for name, expected in before['channels'].items():
            actual = after['channels'][name]
            self.assertEqual(expected[0], actual[0], (name, 'rotation_mode'))
            for index in range(1, 6):
                self.assertEqual(len(expected[index]), len(actual[index]))
                for wanted, got in zip(expected[index], actual[index]):
                    self.assertLessEqual(abs(wanted - got), 2e-6, (name, index, wanted, got))
            self.assertEqual(expected[6], actual[6], (name, 'ik_fk'))

    def test_default_user_switch_rejects_missing_managed_body_without_repair(self):
        self.pose_ik()
        body = bone_collections.body_collection(self.rig)
        self.assertIsNotNone(body)
        self.rig.data.collections.remove(body)
        update(self.rig)
        before = workspace(self.rig)
        before_pose = evaluated(self.rig)
        before_display = bone_display._snapshot(self.rig)
        before_layout = bone_collections.snapshot_layout(self.rig)
        actions = set(bpy.data.actions.keys())

        # No construction-only display-deferral argument is passed.
        with self.assertRaisesRegex(ValueError, 'managed Body collection is missing'):
            limb_ik_fk.switch_limb(bpy.context, self.rig, ('ARM', 'L'), 'FK', keyframe=False)

        self.assertIsNone(bone_collections.body_collection(self.rig))
        self._assert_user_switch_failure_keeps_workspace(before)
        self.assertEqual(before_display, bone_display._snapshot(self.rig))
        self.assertEqual(before_layout, bone_collections.snapshot_layout(self.rig))
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        self.assertPose(before_pose, tolerance=2e-6)

    def test_default_user_switch_rejects_repurposed_body_membership_without_repair(self):
        self.pose_ik()
        body = bone_collections.body_collection(self.rig)
        self.assertIsNotNone(body)
        members = set(body.bones.keys())
        self.assertTrue(members)
        removed = sorted(members)[0]
        body.unassign(self.rig.data.bones[removed])
        repurposed_members = members - {removed}
        self.assertEqual(set(body.bones.keys()), repurposed_members)
        update(self.rig)
        before = workspace(self.rig)
        before_pose = evaluated(self.rig)
        before_display = bone_display._snapshot(self.rig)
        before_layout = bone_collections.snapshot_layout(self.rig)
        actions = set(bpy.data.actions.keys())

        # Interactive switching must retain the existing strict layout guard.
        with self.assertRaisesRegex(ValueError, 'managed Body collection was repurposed'):
            limb_ik_fk.switch_limb(bpy.context, self.rig, ('ARM', 'L'), 'FK', keyframe=False)

        self.assertEqual(set(body.bones.keys()), repurposed_members)
        self._assert_user_switch_failure_keeps_workspace(before)
        self.assertEqual(before_display, bone_display._snapshot(self.rig))
        self.assertEqual(before_layout, bone_collections.snapshot_layout(self.rig))
        self.assertEqual(actions, set(bpy.data.actions.keys()))
        self.assertPose(before_pose, tolerance=2e-6)

class BrowserPresentationTests(unittest.TestCase):
    """Exercise the actual presentation code without a native browser runtime.

    These boundary checks intentionally use browser spies. A hidden Blender
    SpaceFile can have params but no runtime, and calling its activation RNA
    crashes the process before Python can catch an exception. GUI integration
    still needs a separate disposable Blender process.
    """

    def setUp(self):
        source = ROOT / 'addons' / 'character_designer' / 'control_pose_capture.py'
        tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
        show = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == '_show_current_file')
        operator = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef)
                        and node.name == 'CHARACTERDESIGNER_OT_save_pose_asset')
        execute = next(node for node in operator.body
                       if isinstance(node, ast.FunctionDef) and node.name == 'execute')

        class HiddenScreens:
            @property
            def screens(self):
                raise AssertionError('Saved workspace screens must not be visited')

        self.namespace = {'bpy': SimpleNamespace(data=HiddenScreens())}
        # Compile only these original function definitions: neither bpy nor
        # Character Designer is imported by the lightweight test runner.
        definitions = ast.Module(body=[show, execute], type_ignores=[])
        self.definitions_code = compile(definitions, str(source), 'exec')
        exec(self.definitions_code, self.namespace)
        self.show = self.namespace['_show_current_file']

    def area(self, *, browse_mode='ASSETS', params='DEFAULT', area_type='FILE_BROWSER'):
        if params == 'DEFAULT':
            params = SimpleNamespace(asset_library_reference='ALL', filter_search='artist search',
                                     filter_asset_id=SimpleNamespace(filter_action=False),
                                     catalog_id='artist catalog', asset_catalog_visibility='SELECTED')
        space = SimpleNamespace(browse_mode=browse_mode, params=params,
                                activate_asset_by_id=Mock(
                                    side_effect=AssertionError('Native asset activation is unsafe here')))
        return SimpleNamespace(type=area_type, spaces=SimpleNamespace(active=space), tag_redraw=Mock())

    def assertLocalPosesVisible(self, area):
        params = area.spaces.active.params
        self.assertEqual(params.asset_library_reference, 'LOCAL')
        self.assertEqual(params.filter_search, '')
        self.assertTrue(params.filter_asset_id.filter_action)
        self.assertEqual(params.catalog_id, '00000000-0000-0000-0000-000000000000')
        self.assertEqual(params.asset_catalog_visibility, 'ALL')
        area.tag_redraw.assert_called_once_with()
        area.spaces.active.activate_asset_by_id.assert_not_called()

    def test_show_current_file_updates_only_current_window_visible_assets_without_activation(self):
        visible = self.area()

        class OtherScreen:
            @property
            def areas(self):
                raise AssertionError('A screen outside the current window must not be visited')

        context = SimpleNamespace(window=SimpleNamespace(screen=SimpleNamespace(areas=[visible])),
                                  screen=OtherScreen())
        action = SimpleNamespace(name='Saved Pose', use_fake_user=True, asset_data=object())
        before = vars(action).copy()
        self.assertIsNone(self.show(context, action))
        self.assertLocalPosesVisible(visible)
        self.assertEqual(vars(action), before)

    def test_show_current_file_skips_missing_window_and_screen(self):
        for context in (SimpleNamespace(), SimpleNamespace(window=None),
                        SimpleNamespace(window=SimpleNamespace(screen=None))):
            with self.subTest(context=context):
                self.assertIsNone(self.show(context, SimpleNamespace(name='Saved Pose')))

    def test_show_current_file_skips_none_or_uninitialized_asset_params(self):
        none = self.area(params=None)
        uninitialized = self.area(params=SimpleNamespace())
        context = SimpleNamespace(window=SimpleNamespace(screen=SimpleNamespace(areas=[none, uninitialized])))
        self.assertIsNone(self.show(context))
        for area in (none, uninitialized):
            area.tag_redraw.assert_not_called()
            area.spaces.active.activate_asset_by_id.assert_not_called()

    def test_show_current_file_skips_normal_file_browser_before_reading_its_params(self):
        class FilesSpace:
            browse_mode = 'FILES'

            @property
            def params(self):
                raise AssertionError('Normal file browser params must not be changed or read')

        normal = SimpleNamespace(type='FILE_BROWSER', spaces=SimpleNamespace(active=FilesSpace()),
                                 tag_redraw=Mock())
        view = SimpleNamespace(type='VIEW_3D', tag_redraw=Mock())
        context = SimpleNamespace(window=SimpleNamespace(screen=SimpleNamespace(areas=[normal, view])))
        self.assertIsNone(self.show(context))
        normal.tag_redraw.assert_not_called()
        view.tag_redraw.assert_not_called()

    def test_show_current_file_continues_after_browser_setting_or_redraw_failure(self):
        for failure in (AttributeError, RuntimeError, TypeError, ValueError):
            with self.subTest(failure=failure.__name__):
                def reject(_params, _value):
                    raise failure('The browser is rebuilding')

                params = type('RejectingParams', (), {
                    'asset_library_reference': property(lambda _params: 'ALL', reject)})()
                broken = self.area(params=params)
                healthy = self.area()
                context = SimpleNamespace(window=SimpleNamespace(screen=SimpleNamespace(areas=[broken, healthy])))
                self.assertIsNone(self.show(context))
                broken.tag_redraw.assert_not_called()
                broken.spaces.active.activate_asset_by_id.assert_not_called()
                self.assertLocalPosesVisible(healthy)

        broken = self.area()
        broken.tag_redraw.side_effect = RuntimeError('The area was resized')
        healthy = self.area()
        context = SimpleNamespace(window=SimpleNamespace(screen=SimpleNamespace(areas=[broken, healthy])))
        self.assertIsNone(self.show(context))
        self.assertLocalPosesVisible(healthy)

    def test_save_operator_finishes_and_keeps_completed_pose_when_browser_update_fails(self):
        action = SimpleNamespace(name='Saved arm', use_fake_user=True, asset_data=object(),
                                 metadata='saved compatibility data', channels=('native channels',))
        before = vars(action).copy()
        rig = object()
        display = SimpleNamespace(character_rig=Mock(return_value=rig))

        def import_display(name, globals=None, locals=None, fromlist=(), level=0):
            if name == '' and level == 1 and tuple(fromlist) == ('bone_display',):
                return SimpleNamespace(bone_display=display)
            raise AssertionError(f'Unexpected import: {name!r}, {fromlist!r}, {level}')

        builtins = dict(vars(__import__('builtins')))
        builtins['__import__'] = import_display
        self.namespace['__builtins__'] = builtins
        self.namespace['save_pose'] = Mock(return_value=action)
        # Python binds a function's builtins when it is created. Recreate the
        # unchanged definitions with only the local import adapter installed.
        exec(self.definitions_code, self.namespace)

        def reject(_params, _value):
            raise RuntimeError('Asset browser settings are unavailable')

        params = type('RejectingParams', (), {
            'asset_library_reference': property(lambda _params: 'ALL', reject)})()
        browser = self.area(params=params)
        context = SimpleNamespace(window=SimpleNamespace(screen=SimpleNamespace(areas=[browser])))
        operator = SimpleNamespace(name='Saved arm', scope='SELECTED', include_fingers=False, report=Mock())
        result = self.namespace['execute'](operator, context)

        self.assertEqual(result, {'FINISHED'})
        self.namespace['save_pose'].assert_called_once_with(
            context, rig, 'Saved arm', scope='SELECTED', include_fingers=False)
        self.assertEqual(vars(action), before)
        self.assertEqual(operator.report.call_args.args[0], {'INFO'})
        self.assertIn(action.name, operator.report.call_args.args[1])
        browser.spaces.active.activate_asset_by_id.assert_not_called()


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    print('CAPTURE_BOUNDARY: evaluated native IK/FK/Original channels and local Actions; '
          'Asset Browser GUI/export and live artist scene not tested.', flush=True)
    if not result.wasSuccessful():
        raise RuntimeError('Control Pose capture integration tests failed')
