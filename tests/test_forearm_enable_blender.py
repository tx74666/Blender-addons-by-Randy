"""Focused native proof for the retained-calibration Forearm Enable switch.

Run only in a fresh private Blender process with isolated user configuration.
The 246-vertex bilateral fixture creates its own FK rig and action; no artist
file is read or saved. Disabled-path traps prove skipped work, not FPS. Native
skin/correction points use the existing independent articulated/LBS oracle.
"""
from contextlib import ExitStack
import copy
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import forearm_twist as runtime
import test_forearm_twist_blender as fixtures
from test_forearm_twist_ranges_blender import bilateral_fixture
from test_forearm_twist_follow_runtime_blender import (
    artist_state, expected_points, native_pose, side_fixture)
from test_forearm_twist_ownership_blender import key_snapshot


def author_state(fixture):
    """Full fixture author data, excluding the two derived correction outputs."""
    arm, mesh = fixture['armature'], fixture['mesh']
    ad = arm.animation_data
    return dict(existing=artist_state(fixture),
        data_ids=(arm.as_pointer(), arm.data.as_pointer(), mesh.as_pointer(),
                  mesh.data.as_pointer(), mesh.data.shape_keys.as_pointer()),
        locks=tuple((bone.name, tuple(bone.lock_location), tuple(bone.lock_rotation),
            bone.lock_rotation_w, bone.lock_rotations_4d, tuple(bone.lock_scale))
            for bone in arm.pose.bones),
        bindings=tuple((modifier.name, modifier.object.as_pointer() if modifier.object else None,
            modifier.show_viewport, modifier.show_render, modifier.use_vertex_groups,
            modifier.use_bone_envelopes, modifier.use_deform_preserve_volume)
            for modifier in mesh.modifiers if modifier.type == 'ARMATURE'),
        groups=tuple((group.name, group.lock_weight) for group in mesh.vertex_groups),
        animation=None if ad is None else (
            ad.action.as_pointer() if ad.action else None,
            ad.action_slot.handle if ad.action_slot else None,
            ad.use_nla, ad.action_blend_type, ad.action_extrapolation,
            ad.action_influence, ad.use_tweak_mode),
        selection=(bpy.context.object.as_pointer() if bpy.context.object else None,
            tuple(obj.as_pointer() for obj in bpy.context.selected_objects),
            arm.mode, mesh.mode),
        active_key=mesh.active_shape_key_index,
        autokey=bpy.context.scene.tool_settings.use_keyframe_insert_auto)


def calibration_without_flags(mesh):
    records = copy.deepcopy(runtime._records(mesh))
    for record in records.values():
        record.pop('enabled', None)
    return records


def handler_ids():
    return tuple((name, tuple(id(item) for item in getattr(bpy.app.handlers, name)))
        for name in ('depsgraph_update_post', 'frame_change_post', 'load_post',
                     'undo_post', 'redo_pre', 'animation_playback_pre',
                     'animation_playback_post'))


class NoEvaluationContext:
    def __init__(self, actual):
        self.actual = actual

    def __getattr__(self, name):
        if name == 'evaluated_depsgraph_get':
            raise AssertionError('Disabled owner requested a dependency graph')
        return getattr(self.actual, name)


class ForearmEnableTests(unittest.TestCase):
    def tearDown(self):
        if runtime._SESSION is not None:
            runtime.finish_test(bpy.context, confirm=False)
        self.assertFalse(runtime._BUSY, 'A failed switch left the shared runtime busy')

    def ready(self, *, animated=False):
        fixture = bilateral_fixture()
        mesh, arm = fixture['mesh'], fixture['armature']
        self.assertEqual(len(mesh.data.vertices), 246)
        right = side_fixture(fixture, 'R')
        poses = {1: {'L': (31., 42.), 'R': (-23., -34.)},
                 10: {'L': (-39., -61.), 'R': (44., 58.)},
                 20: {'L': (12., 23.), 'R': (-17., -46.)}}
        if animated:
            for frame, angles in poses.items():
                bpy.context.scene.frame_set(frame)
                for side, local in (('L', fixture), ('R', right)):
                    native_pose(local, *angles[side])
                    for name in (local['lower_name'], local['hand_name']):
                        arm.pose.bones[name].keyframe_insert(
                            data_path='rotation_quaternion', frame=frame)
            bpy.context.scene.frame_set(1)
        else:
            native_pose(fixture, *poses[1]['L'])
            native_pose(right, *poses[1]['R'])
        runtime.start_test(bpy.context, mesh, symmetry=True, continuous=True)
        runtime.set_ratio(bpy.context, 3, .29)
        runtime.finish_test(bpy.context, confirm=True)
        runtime.update_runtime(bpy.context.scene)
        self.assertEqual(set(runtime._records(mesh)), {'L', 'R'})
        self.assert_flags(mesh, True)
        self.assertNotIn(mesh.name, runtime._ERRORS)
        fixtures.assert_points_close(fixtures.evaluated_points(mesh),
            expected_points(fixture, poses[1]), 'Initial paired correction', tolerance=2e-5)
        return fixture, poses

    def assert_flags(self, mesh, enabled):
        records = runtime._records(mesh)
        self.assertEqual(set(records), {'L', 'R'})
        self.assertTrue(all(record.get('enabled', True) is enabled
                            for record in records.values()))
        self.assertTrue(all(runtime._managed_key(mesh, side, record, repair_name=False).mute is not enabled
                            for side, record in records.items()))

    def disabled_snapshot(self, fixture):
        mesh = fixture['mesh']
        return (mesh[runtime.RECORD_KEY], key_snapshot(mesh), author_state(fixture),
                fixtures.pose_snapshot(fixture['armature']))

    def test_native_animation_off_across_frames_then_on_reuses_both_calibrations(self):
        fixture, poses = self.ready(animated=True)
        mesh, arm = fixture['mesh'], fixture['armature']
        calibration = calibration_without_flags(mesh)
        authors = author_state(fixture)
        owned_ids = {side: mesh.data.shape_keys.key_blocks[record['key']].as_pointer()
                     for side, record in runtime._records(mesh).items()}
        before_pose, handlers = fixtures.pose_snapshot(arm), handler_ids()
        before_keys = key_snapshot(mesh)
        self.assertIn(mesh.as_pointer(), runtime._CACHE)
        runtime.toggle_paired_calibration(bpy.context, mesh)
        self.assert_flags(mesh, False)
        self.assertNotIn(mesh.as_pointer(), runtime._CACHE)
        self.assertNotIn(mesh.as_pointer(), runtime._OUTPUT_CACHE)
        self.assertNotIn(mesh.as_pointer(), runtime._PLAYBACK_PREPARED)
        for name, old in before_keys.items():
            current = key_snapshot(mesh)[name]
            if name in {row['key'] for row in runtime._records(mesh).values()}:
                self.assertEqual(current[:2] + current[3:], old[:2] + old[3:])
            else:
                self.assertEqual(current, old)
        fixtures.assert_pose_snapshot(arm, before_pose, 'Disable keeps raw pose')
        self.assertEqual(author_state(fixture), authors)
        off_keys = key_snapshot(mesh)
        previous_points = previous_pose = None
        for frame in (10, 20, 1):
            bpy.context.scene.frame_set(frame)
            pose = fixtures.pose_snapshot(arm)
            runtime.update_runtime(bpy.context.scene)
            actual = fixtures.evaluated_points(mesh)
            fixtures.assert_points_close(actual, expected_points(fixture, poses[frame]),
                'Disabled animation uses original LBS at frame ' + str(frame), tolerance=2e-5)
            self.assertEqual(key_snapshot(mesh), off_keys,
                'Disabled runtime changed a Shape Key coordinate/value/mute')
            self.assertEqual(author_state(fixture), authors)
            self.assertEqual(calibration_without_flags(mesh), calibration)
            fixtures.assert_pose_snapshot(arm, pose, 'Disabled animation keeps native channels')
            if previous_points is not None:
                self.assertGreater(max((a - b).length for a, b in zip(actual, previous_points)), 1e-3)
                self.assertNotEqual(pose, previous_pose, 'Native wrist action stopped while disabled')
            previous_points, previous_pose = actual, pose
        bpy.context.scene.frame_set(10)
        before_enable = fixtures.pose_snapshot(arm)
        runtime.toggle_paired_calibration(bpy.context, mesh)
        self.assert_flags(mesh, True)
        fixtures.assert_points_close(fixtures.evaluated_points(mesh), expected_points(fixture, poses[10]),
            'Enable recomputes current animated pose', tolerance=2e-5)
        self.assertEqual(calibration_without_flags(mesh), calibration)
        self.assertEqual({side: mesh.data.shape_keys.key_blocks[record['key']].as_pointer()
            for side, record in runtime._records(mesh).items()}, owned_ids)
        self.assertEqual(author_state(fixture), authors)
        fixtures.assert_pose_snapshot(arm, before_enable, 'Enable keeps animated raw pose')
        self.assertEqual(handler_ids(), handlers, 'Enable switch replaced shared lifecycle handlers')
        self.assertTrue(runtime._RUNTIME_REGISTERED)

    def test_disabled_entry_points_skip_geometry_and_evaluation_even_on_frame_events(self):
        fixture, _poses = self.ready()
        mesh = fixture['mesh']
        runtime.toggle_paired_calibration(bpy.context, mesh)
        before = self.disabled_snapshot(fixture)
        handlers = handler_ids()
        graph = bpy.context.evaluated_depsgraph_get()
        names = ('_prepare_runtime_records', '_check_mesh', '_topology', '_input_mix',
                 '_weights', '_managed_key', 'corrected_vertex', '_playback_token')
        with ExitStack() as stack:
            guards = [stack.enter_context(patch.object(runtime, name,
                side_effect=AssertionError('Disabled runtime entered ' + name))) for name in names]
            basis = stack.enter_context(patch.object(runtime.validation_cache, 'basis_coordinates',
                side_effect=AssertionError('Disabled runtime read Basis coordinates')))
            # Only the product module receives this proxy. Blender and the test
            # retain their real context for actual native frame evaluation.
            stack.enter_context(patch.object(runtime, 'bpy', SimpleNamespace(
                context=NoEvaluationContext(bpy.context), app=bpy.app, data=bpy.data)))
            for frame in (3, 7, 11):
                bpy.context.scene.frame_set(frame)
                runtime.update_runtime(bpy.context.scene)
                runtime.update_runtime(bpy.context.scene, graph, _playback=True)
                runtime._calculate_object(mesh, graph)
                runtime._calculate_object(mesh, graph, _playback_scene=bpy.context.scene)
                runtime._calculate_records(mesh, graph)
            self.assertTrue(all(guard.call_count == 0 for guard in guards))
            self.assertEqual(basis.call_count, 0)
        self.assertEqual(self.disabled_snapshot(fixture), before)
        self.assertEqual(handler_ids(), handlers)
        self.assert_flags(mesh, False)

    def test_enable_revalidates_actual_binding_support_and_owned_key_structure(self):
        for invalid in ('binding', 'support', 'owned_key', 'renamed_key'):
            with self.subTest(invalid=invalid):
                fixture, _poses = self.ready()
                mesh = fixture['mesh']
                runtime.toggle_paired_calibration(bpy.context, mesh)
                if invalid == 'binding':
                    mesh.modifiers[0].object = None
                elif invalid == 'support':
                    support = set(runtime._records(mesh)['R']['vertices'])
                    edge = next(edge for edge in mesh.data.edges if support.intersection(edge.vertices))
                    edge.vertices = tuple(edge.vertices)[::-1]
                elif invalid == 'owned_key':
                    right = runtime._records(mesh)['R']
                    mesh.data.shape_keys.key_blocks[right['key']].vertex_group = mesh.vertex_groups[0].name
                else:
                    right = runtime._records(mesh)['R']
                    owned = mesh.data.shape_keys.key_blocks[right['key']]
                    owned.name = 'Artist named retained right correction'
                before = self.disabled_snapshot(fixture)
                with self.assertRaises((runtime.ForearmTwistError, ValueError)):
                    runtime.toggle_paired_calibration(bpy.context, mesh)
                self.assertEqual(self.disabled_snapshot(fixture), before)
                self.assert_flags(mesh, False)

    def test_failed_second_side_compute_restores_exact_off_state_and_author_data(self):
        fixture, _poses = self.ready()
        mesh = fixture['mesh']
        runtime.toggle_paired_calibration(bpy.context, mesh)
        native_pose(fixture, -19., 37.)
        native_pose(side_fixture(fixture, 'R'), 24., -51.)
        before = self.disabled_snapshot(fixture)
        actual_correct = runtime.corrected_vertex
        seen = []

        def fail_right(*args, **kwargs):
            side = args[3].rsplit('.', 1)[-1]
            seen.append(side)
            if side == 'R':
                raise RuntimeError('injected second-side Enable compute failure')
            return actual_correct(*args, **kwargs)

        with patch.object(runtime, 'corrected_vertex', side_effect=fail_right):
            with self.assertRaisesRegex(RuntimeError, 'second-side Enable compute failure'):
                runtime.toggle_paired_calibration(bpy.context, mesh)
        self.assertIn('L', seen, 'Failure did not occur after first-side computation')
        self.assertIn('R', seen)
        self.assertEqual(self.disabled_snapshot(fixture), before)
        self.assert_flags(mesh, False)
        self.assertNotIn(mesh.as_pointer(), runtime._CACHE)
        self.assertNotIn(mesh.as_pointer(), runtime._OUTPUT_CACHE)
        runtime.update_runtime(bpy.context.scene)
        self.assertEqual(self.disabled_snapshot(fixture), before)
        runtime.toggle_paired_calibration(bpy.context, mesh)
        self.assert_flags(mesh, True)
        fixtures.assert_points_close(fixtures.evaluated_points(mesh), expected_points(fixture,
            {'L': (-19., 37.), 'R': (24., -51.)}), 'Enable recovers after compute fault', tolerance=2e-5)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
