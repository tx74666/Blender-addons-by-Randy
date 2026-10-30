"""Finite Pose blend service tests in an empty, isolated native process.

The complete approved117 package is loaded first. Only the new core under test
is imported from canonical source; paused candidates are never package inputs.
No artist file, GUI events, Undo history, playback cadence or FPS claim.
"""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Euler

REPO = Path(__file__).resolve().parents[1]
PROJECT = Path('D:/Blender/Projects/Character/X')
PROJECTION = PROJECT / 'validation/physics_simple117_20261029/stage117_02/projection.json'
PIN = '9dac3fcfdc9673dca1eda7a14b3b6f298287d365ab4e2f29ee18db2d91932a15'
assert hashlib.sha256(PROJECTION.read_bytes()).hexdigest() == PIN
manifest = json.loads(PROJECTION.read_text(encoding='utf8'))
PACKAGE = Path(manifest['source_root'])
for relative, expected in manifest['files'].items():
    assert hashlib.sha256((PACKAGE / relative).read_bytes()).hexdigest() == expected
sys.path[:0] = [str(PACKAGE.parent), str(REPO / 'tests')]
import character_designer
assert Path(character_designer.__file__).resolve() == (PACKAGE / '__init__.py').resolve()
from character_designer import control_pose_assets as poses
CORE = REPO / 'addons/character_designer/control_pose_blend.py'
spec = importlib.util.spec_from_file_location('character_designer.control_pose_blend', CORE)
blend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = blend
spec.loader.exec_module(blend)
character_designer.control_pose_blend = blend


def update(rig):
    poses.match._update(bpy.context, rig)


def raw(rig):
    return {pb.name: (pb.rotation_mode, tuple(pb.location), tuple(pb.rotation_euler),
                      tuple(pb.rotation_quaternion), tuple(pb.rotation_axis_angle), tuple(pb.scale),
                      tuple((key, pb[key]) for key in ('ik_fk', 'uniform_scale') if key in pb))
            for pb in rig.pose.bones}


class FreeBlendTests(unittest.TestCase):
    def setUp(self):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.old_actions = set(bpy.data.actions)
        data = bpy.data.armatures.new('Blend fixture')
        self.rig = bpy.data.objects.new('Blend fixture', data)
        bpy.context.scene.collection.objects.link(self.rig)
        self.rig.select_set(True)
        bpy.context.view_layer.objects.active = self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        for name, x in (('root', 0.), ('finger.L', 1.), ('finger.R', -1.), ('unrelated', 2.)):
            bone = data.edit_bones.new(name)
            bone.head, bone.tail = (x, 0., 0.), (x, 1., 0.)
            if name != 'root':
                bone.parent = data.edit_bones['root']
        bpy.ops.object.mode_set(mode='POSE')
        for pb in self.rig.pose.bones:
            pb.rotation_mode = 'XYZ'
            blend._selection_bone(pb).select = False
        blend._selection_bone(self.rig.pose.bones['unrelated']).select = True
        self.rig.data.bones.active = self.rig.data.bones['unrelated']
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        update(self.rig)
        self.values = {'finger.L': {'rotation_euler': {0: 1.}},
                       'finger.R': {'rotation_euler': {0: .6}}}

    def tearDown(self):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        data = self.rig.data
        bpy.data.objects.remove(self.rig, do_unlink=True)
        bpy.data.armatures.remove(data)
        for action in set(bpy.data.actions) - self.old_actions:
            if action.users == 0:
                bpy.data.actions.remove(action)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False

    def begin(self, **kwargs):
        return blend.begin(bpy.context, values=kwargs.pop('values', self.values), **kwargs)

    def test_half_uses_fixed_start_and_preserves_unrelated_channels(self):
        initial = raw(self.rig)
        session = self.begin()
        blend.preview(bpy.context, session, .5)
        self.assertAlmostEqual(self.rig.pose.bones['finger.L'].rotation_euler.x, .5, places=5)
        half = raw(self.rig)
        blend.preview(bpy.context, session, .9)
        blend.preview(bpy.context, session, .2)
        blend.preview(bpy.context, session, .5)
        self.assertEqual(half, raw(self.rig))
        self.assertEqual(initial['unrelated'], raw(self.rig)['unrelated'])
        blend.cancel(bpy.context, session)
        self.assertEqual(initial, raw(self.rig))

    def test_zero_restores_all_rotation_representations_and_selection(self):
        pb = self.rig.pose.bones['finger.L']
        pb.rotation_euler = (.17, -.21, .1)
        pb.rotation_quaternion = (.98, .05, .02, .1)
        pb.rotation_axis_angle = (.3, .1, .8, .2)
        update(self.rig)
        session = self.begin()
        initial = blend._state(bpy.context, self.rig)
        blend.preview(bpy.context, session, .7)
        blend.preview(bpy.context, session, 0.)
        self.assertEqual(initial, blend._state(bpy.context, self.rig))
        blend.confirm(bpy.context, session)
        with self.assertRaises(blend.BlendStateChanged):
            blend.confirm(bpy.context, session)

    def test_partial_native_selection_and_unrelated_fallback(self):
        for pb in self.rig.pose.bones:
            blend._selection_bone(pb).select = pb.name == 'finger.L'
        session = self.begin()
        self.assertEqual(set(session['values']), {'finger.L'})
        blend.preview(bpy.context, session, 1.)
        self.assertEqual(self.rig.pose.bones['finger.R'].rotation_euler.x, 0.)
        blend.cancel(bpy.context, session)

    def test_mirror_targets_authored_selected_subset(self):
        poses.capture_baseline(self.rig)
        for pb in self.rig.pose.bones:
            blend._selection_bone(pb).select = pb.name == 'finger.L'
        session = self.begin(values={'finger.L': self.values['finger.L']}, flipped=True)
        self.assertEqual(set(session['values']), {'finger.R'})
        blend.preview(bpy.context, session, .5)
        self.assertGreater(abs(self.rig.pose.bones['finger.R'].rotation_euler.x), .1)
        self.assertEqual(self.rig.pose.bones['finger.L'].rotation_euler.x, 0.)
        blend.cancel(bpy.context, session)

    def test_quaternion_shortest_path_and_axis_angle_destination(self):
        pb = self.rig.pose.bones['finger.L']
        pb.rotation_mode = 'QUATERNION'
        rotation = Euler((.8, -.2, .4)).to_quaternion()
        values = {'finger.L': {'rotation_quaternion': dict(enumerate(-rotation))}}
        session = self.begin(values=values)
        blend.preview(bpy.context, session, .5)
        expected = Euler((0., 0., 0.)).to_quaternion().slerp(rotation, .5)
        self.assertLess(pb.rotation_quaternion.rotation_difference(expected).angle, 1e-5)
        blend.cancel(bpy.context, session)

    def test_bbone_fields_interpolate_and_cancel(self):
        pb = self.rig.pose.bones['finger.L']
        initial = pb.bbone_curveinx
        session = self.begin(values={'finger.L': {'bbone_curveinx': {0: initial + .4}}})
        blend.preview(bpy.context, session, .5)
        self.assertAlmostEqual(pb.bbone_curveinx, initial + .2, places=5)
        blend.cancel(bpy.context, session)
        self.assertEqual(pb.bbone_curveinx, initial)

    def test_invalid_factor_cannot_mutate_preview(self):
        session = self.begin()
        initial = raw(self.rig)
        for value in (-.1, 1.1, float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                blend.preview(bpy.context, session, value)
            self.assertEqual(initial, raw(self.rig))
        blend.cancel(bpy.context, session)

    def test_foreign_edit_is_never_overwritten_by_preview_or_cancel(self):
        session = self.begin()
        blend.preview(bpy.context, session, .5)
        self.rig.pose.bones['unrelated'].rotation_euler.x = .7
        update(self.rig)
        foreign = raw(self.rig)
        for operation in (lambda: blend.preview(bpy.context, session, .9),
                          lambda: blend.cancel(bpy.context, session)):
            with self.assertRaises(blend.BlendStateChanged):
                operation()
            self.assertEqual(foreign, raw(self.rig))

    def test_frame_change_refuses_without_rewinding(self):
        session = self.begin()
        bpy.context.scene.frame_set(bpy.context.scene.frame_current + 1)
        frame = bpy.context.scene.frame_current
        with self.assertRaises(blend.BlendStateChanged):
            blend.cancel(bpy.context, session)
        self.assertEqual(bpy.context.scene.frame_current, frame)

    def test_driver_owned_channel_refuses_before_mutation(self):
        self.rig.pose.bones['finger.L'].driver_add('rotation_euler', 0)
        initial = raw(self.rig)
        with self.assertRaisesRegex(ValueError, 'driver'):
            self.begin()
        self.assertEqual(initial, raw(self.rig))

    def test_autokey_only_once_and_original_action_exact(self):
        self.rig.pose.bones['finger.L'].keyframe_insert('rotation_euler', frame=1.)
        ad = self.rig.animation_data
        previous = ad.action
        previous_curves = tuple((c.data_path, c.array_index,
                                 tuple(tuple(p.co) for p in c.keyframe_points))
                                for c in poses._curves(previous, self.rig))
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        count = len(bpy.data.actions)
        session = self.begin()
        for value in (.1, .7, .5):
            blend.preview(bpy.context, session, value)
            self.assertEqual(len(bpy.data.actions), count)
            self.assertEqual(ad.action, previous)
        result = blend.confirm(bpy.context, session)
        self.assertNotEqual(result['action'], previous)
        self.assertEqual(ad.action, result['action'])
        self.assertEqual(len(bpy.data.actions), count + 1)
        self.assertEqual(previous_curves, tuple((c.data_path, c.array_index,
                                 tuple(tuple(p.co) for p in c.keyframe_points))
                                for c in poses._curves(previous, self.rig)))

    def test_failed_confirm_removes_only_owned_staged_action(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        actions = set(bpy.data.actions)
        initial = raw(self.rig)
        session = self.begin()
        blend.preview(bpy.context, session, .5)
        with patch.object(poses, '_insert_key', side_effect=RuntimeError('injected owned key failure')):
            with self.assertRaisesRegex(RuntimeError, 'owned key failure'):
                blend.confirm(bpy.context, session)
        self.assertEqual(actions, set(bpy.data.actions))
        self.assertIsNone(self.rig.animation_data)
        self.assertEqual(initial, raw(self.rig))

    def test_shared_data_rejects_real_second_object_but_allows_reference_user(self):
        reference = bpy.data.actions.new('Data reference only')
        reference['rig_data'] = self.rig.data
        session = self.begin()
        blend.cancel(bpy.context, session)
        other = bpy.data.objects.new('Second actual object', self.rig.data)
        bpy.context.scene.collection.objects.link(other)
        try:
            with self.assertRaisesRegex(ValueError, 'shared'):
                self.begin()
        finally:
            bpy.data.objects.remove(other, do_unlink=True)
            del reference['rig_data']


class GeneratedBlendTests(unittest.TestCase):
    def setUp(self):
        import test_control_pose_arm_flat_blender as helper
        self.helper = helper
        self.rig = helper.fixture(sides='LR', fingers=True)
        self.action, self.target = helper.authored_asset(self.rig, near_flat=False)
        self.asset = type('Asset', (), {'id_type': 'ACTION', 'local_id': self.action,
                                      'full_library_path': '', 'name': self.action.name})()
        for pb in self.rig.pose.bones:
            blend._selection_bone(pb).select = False
        update(self.rig)

    def test_real_ik_fixed_start_modes_target_and_cancel(self):
        initial = raw(self.rig)
        rest = poses.native_rest(self.rig)
        collections = tuple((c.name, c.as_pointer()) for c in self.rig.data.collections_all)
        mode = {key: poses.match.mode_for_rig(self.rig, entry)
                for key, entry in poses.limb_ik._validate_inventory(self.rig)['rigs'].items()}
        session = blend.begin(bpy.context, self.asset)
        for factor in (.5, .8, .5):
            blend.preview(bpy.context, session, factor)
            self.assertEqual(collections, tuple((c.name, c.as_pointer()) for c in self.rig.data.collections_all))
            if factor == .5:
                current = raw(self.rig)
                if 'half' in locals():
                    self.assertEqual(half, current)
                half = current
        blend.preview(bpy.context, session, 1.)
        actual = self.helper.matrices(self.rig)
        self.assertLess(max(self.helper.matrix_error(actual[name], matrix)
                            for name, matrix in self.target.items()), 4e-4)
        self.assertEqual(mode, {key: poses.match.mode_for_rig(self.rig, entry)
                for key, entry in poses.limb_ik._validate_inventory(self.rig)['rigs'].items()})
        blend.cancel(bpy.context, session)
        self.assertEqual(initial, raw(self.rig))
        self.assertEqual(rest, poses.native_rest(self.rig))

    def test_fk_keeps_mode_and_dormant_controls_restore(self):
        poses.match.switch_limb(bpy.context, self.rig, ('ARM', 'L'), 'FK', keyframe=False)
        initial = blend._state(bpy.context, self.rig)
        session = blend.begin(bpy.context, self.asset)
        blend.preview(bpy.context, session, .5)
        inventory = poses.limb_ik._validate_inventory(self.rig)
        self.assertEqual(poses.match.mode_for_rig(self.rig, inventory['rigs'][('ARM', 'L')]), 'FK')
        blend.cancel(bpy.context, session)
        self.assertEqual(initial, blend._state(bpy.context, self.rig))

    def test_match_failure_restores_raw_channels_constraints_and_collection_identity(self):
        initial = blend._state(bpy.context, self.rig)
        session = blend.begin(bpy.context, self.asset)
        def fail(*args, **kwargs):
            self.rig.pose.bones['upper_arm.L'].location.x += .2
            next(iter(self.rig.pose.bones['forearm.L'].constraints)).mute = True
            raise RuntimeError('injected native match refusal')
        with patch.object(poses, '_match', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'native match refusal'):
                blend.preview(bpy.context, session, .5)
        self.assertEqual(initial, blend._state(bpy.context, self.rig))
        blend.cancel(bpy.context, session)

    def test_tagged_near_flat_endpoint_uses_existing_stable_ik_semantics(self):
        action, _target = self.helper.authored_asset(self.rig, near_flat=True, stable_ik_bend=True)
        asset = type('Asset', (), {'id_type': 'ACTION', 'local_id': action,
                                   'full_library_path': '', 'name': action.name})()
        initial = raw(self.rig)
        session = blend.begin(bpy.context, asset)
        blend.preview(bpy.context, session, .5)
        blend.preview(bpy.context, session, 1.)
        inventory = poses.limb_ik._validate_inventory(self.rig)
        self.assertEqual(poses.match.mode_for_rig(self.rig, inventory['rigs'][('ARM', 'L')]), 'IK')
        blend.cancel(bpy.context, session)
        self.assertEqual(initial, raw(self.rig))

    def test_generated_hand_target_selects_its_complete_native_arm(self):
        inventory = poses.limb_ik._validate_inventory(self.rig)
        entry = inventory['rigs'][('ARM', 'L')]
        blend._selection_bone(self.rig.pose.bones[entry['target'].name]).select = True
        session = blend.begin(bpy.context, self.asset)
        self.assertEqual(set(session['values']), set(entry['chain']))
        blend.preview(bpy.context, session, .5)
        blend.cancel(bpy.context, session)


class OriginalBlendTests(unittest.TestCase):
    def test_original_fixed_start_and_cancel_keep_session_mesh_weights_keys_and_rest(self):
        import test_body_calibration_blender as setup
        from character_designer import body_setup, body_original_mode as original
        rig, mesh = setup.fixture()
        setup.prepare(rig)
        body_setup.generate(bpy.context, rig)
        poses.limb_ik._mode_set(bpy.context, rig, 'POSE')
        poses.match.switch_limb(bpy.context, rig, ('ARM', 'L'), 'FK', keyframe=False)
        original.enter(bpy.context, rig)
        for pb in rig.pose.bones:
            blend._selection_bone(pb).select = False
        update(rig)
        initial = blend._state(bpy.context, rig)
        mesh_before = setup.mesh_state(mesh)
        native_before = poses.native_rest(rig)
        local = poses._convert(rig.data.bones['forearm.L'], rig.pose.bones['forearm.L'].matrix,
                               rig.pose.bones['forearm.L'].parent.matrix, invert=True)
        target = local.to_quaternion() @ Euler((.18, .03, 0.)).to_quaternion()
        session = blend.begin(bpy.context, values={'forearm.L': {'rotation_quaternion': dict(enumerate(target))}})
        blend.preview(bpy.context, session, .5)
        self.assertTrue(original.active(rig))
        self.assertEqual(native_before, poses.native_rest(rig))
        self.assertEqual(mesh_before, setup.mesh_state(mesh))
        half = raw(rig)
        blend.preview(bpy.context, session, .8)
        blend.preview(bpy.context, session, .5)
        self.assertEqual(half, raw(rig))
        blend.cancel(bpy.context, session)
        self.assertEqual(initial, blend._state(bpy.context, rig))
        self.assertEqual(mesh_before, setup.mesh_state(mesh))


if __name__ == '__main__':
    tail = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
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
            'package': str(PACKAGE), 'projection_sha256': PIN,
            'core_sha256': hashlib.sha256(CORE.read_bytes()).hexdigest(),
            'limits': 'Synthetic native functionality only; no artist, mouse event, Undo, FPS or collision acceptance.'},
            indent=2) + '\n', encoding='utf8')
    if not result or not result.wasSuccessful():
        raise RuntimeError('Pose blend service tests failed; original failure retained.')
