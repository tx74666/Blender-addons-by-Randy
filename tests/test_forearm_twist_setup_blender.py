"""Preview entry, solver work, and continuous loop distribution regressions.

Run only in a disposable Blender process; fixtures replace the test scene.
"""
import math
import os
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Quaternion, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import forearm_twist as runtime
from character_designer import forearm_twist_math as geometry
from character_designer import forearm_twist_topology as topology
from character_designer import forearm_twist_profile as profile
import test_forearm_twist_blender as fixtures


def angle_and_swing(fixture):
    transforms = fixtures.deformation_matrices(fixture['armature'])
    lower, hand = (transforms[fixture[name]] for name in ('lower_name', 'hand_name'))
    angle = geometry.twist_angle(lower, hand, fixture['axis'])
    relative = lower.to_quaternion().normalized().conjugated() @ hand.to_quaternion().normalized()
    return angle, (relative @ Quaternion(fixture['axis'], -angle)).normalized()


def ui_settings(side='L'):
    settings = bpy.context.window_manager.character_designer_forearm_twist
    old_busy = runtime._UI_BUSY
    runtime._UI_BUSY = True
    try:
        settings.side, settings.symmetry = side, False
    finally:
        runtime._UI_BUSY = old_busy
    return settings


class SetupTests(unittest.TestCase):
    def tearDown(self):
        if runtime._SESSION is not None:
            runtime.finish_test(bpy.context, False)

    def test_default_entry_preserves_current_pose_and_rotation_channels(self):
        for generated, mode in ((False, 'XYZ'), (False, 'QUATERNION'), (False, 'AXIS_ANGLE'),
                                (True, 'XYZ')):
            with self.subTest(generated=generated, mode=mode):
                f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=generated)
                target = f['target']
                target.rotation_euler = (.12, .21, -.08)
                target.rotation_quaternion = Quaternion(Vector((.2, .8, -.3)).normalized(), .31)
                target.rotation_axis_angle = (.27, 0., 1., 0.)
                target.rotation_mode = mode
                bpy.context.view_layer.update()
                before = fixtures.pose_snapshot(f['armature'])
                structure = fixtures.structure_snapshot(f['armature'], f['mesh'])
                angle, _swing = angle_and_swing(f)
                with patch.object(runtime, '_test_pose', side_effect=AssertionError('Setup must not rotate')):
                    runtime.start_test(bpy.context, f['mesh'])
                fixtures.assert_pose_snapshot(f['armature'], before, 'Neutral setup entry')
                self.assertEqual(fixtures.structure_snapshot(f['armature'], f['mesh']), structure)
                self.assertAlmostEqual(bpy.context.window_manager.character_designer_forearm_twist.test_angle,
                                       angle, places=5)
                bpy.context.window_manager.character_designer_forearm_twist.test_angle = -.65
                turned, _swing = angle_and_swing(f)
                self.assertAlmostEqual(turned, -.65, delta=1e-4)
                runtime.finish_test(bpy.context, False)
                fixtures.assert_pose_snapshot(f['armature'], before, 'Neutral setup cancel')

    def test_explicit_angles_keep_original_wrist_swing_for_fk_and_generated_targets(self):
        for generated in (False, True):
            with self.subTest(generated=generated):
                f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=generated)
                f['target'].rotation_mode = 'XYZ'
                f['target'].rotation_euler = (.19, .23, -.13)
                bpy.context.view_layer.update()
                before = fixtures.pose_snapshot(f['armature'])
                _initial_angle, original_swing = angle_and_swing(f)
                runtime.start_test(bpy.context, f['mesh'], initial_angle=math.pi / 2)
                for wanted in (math.pi / 2, -math.pi / 2, .0):
                    runtime._test_pose(bpy.context, wanted)
                    actual, swing = angle_and_swing(f)
                    self.assertAlmostEqual(actual, wanted, delta=1e-4)
                    self.assertLess(swing.rotation_difference(original_swing).angle, 5e-4)
                    fixtures.assert_runtime_geometry(f, f'Explicit angle {wanted} generated={generated}')
                runtime.finish_test(bpy.context, False)
                fixtures.assert_pose_snapshot(f['armature'], before, 'Explicit setup cancel')

    def test_solver_trial_poses_do_not_recalculate_correction(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=True)
        runtime.start_test(bpy.context, f['mesh'])
        original_solver = runtime._solve_test_pose
        original_calculate = runtime._calculate_object
        inside_solver = False
        completed = []

        def solve(*args, **kwargs):
            nonlocal inside_solver
            self.assertTrue(runtime._BUSY)
            inside_solver = True
            try:
                return original_solver(*args, **kwargs)
            finally:
                inside_solver = False

        def calculate(*args, **kwargs):
            self.assertFalse(inside_solver, 'An invisible solver trial recalculated all mesh corrections')
            completed.append(True)
            return original_calculate(*args, **kwargs)

        with patch.object(runtime, '_solve_test_pose', side_effect=solve), \
             patch.object(runtime, '_calculate_object', side_effect=calculate):
            runtime._test_pose(bpy.context, math.pi / 2)
        self.assertTrue(completed, 'The final pose must still update its visible correction')
        self.assertFalse(runtime._BUSY)
        with patch.object(runtime, '_solve_test_pose', side_effect=RuntimeError('injected solver failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected solver failure'):
                runtime._test_pose(bpy.context, -.5)
        self.assertFalse(runtime._BUSY, 'A failed slider solve must not leave runtime updates blocked')

    def test_selected_seed_is_the_actual_start_and_current_loop(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False)
        seed = set(f['rings'][2])
        ui_settings()
        for vertex in f['mesh'].data.vertices:
            vertex.select = vertex.index in seed
        for edge in f['mesh'].data.edges:
            edge.select = set(edge.vertices) <= seed
        for polygon in f['mesh'].data.polygons:
            polygon.select = False
        before = fixtures.pose_snapshot(f['armature'])
        record = runtime.start_paired_test(bpy.context, seed=True)
        self.assertGreater(record['range_start'], 0, 'Expansion should retain preceding loops for later edits')
        self.assertEqual(set(record['rings'][record['range_start']]['vertices']), seed)
        self.assertEqual(record['current_ring'], record['range_start'])
        first, last = record['range_start'], record['range_end']
        self.assertAlmostEqual(record['rings'][first]['ratio'], 0.)
        self.assertAlmostEqual(record['rings'][last]['ratio'], 1.)
        span = record['rings'][last]['position'] - record['rings'][first]['position']
        for ring in record['rings'][first:last + 1]:
            amount = (ring['position'] - record['rings'][first]['position']) / span
            self.assertAlmostEqual(ring['ratio'], profile.ease_ratio(amount), places=7)
        fixtures.assert_pose_snapshot(f['armature'], before, 'Seed setup entry')

    def test_fresh_ui_continuous_profile_does_not_return_twist_at_forearm_weighted_wrist(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False, forearm_key=False)
        mesh = f['mesh']
        # The user's mesh has forearm-only loops even at the last captured
        # cross-section. Reproduce that dependency independently of weights.
        for indices in f['rings']:
            mesh.vertex_groups[f['lower_name']].add(indices, 1., 'REPLACE')
            mesh.vertex_groups[f['hand_name']].add(indices, 0., 'REPLACE')
        ui_settings()
        record = runtime.start_paired_test(bpy.context)
        self.assertEqual(record.get('distribution'), 'WRIST_CONTINUOUS')
        first, last = record['range_start'], record['range_end']
        self.assertAlmostEqual(record['rings'][first]['ratio'], 0.)
        self.assertAlmostEqual(record['rings'][last]['ratio'], 1.)
        raw = fixtures.uncorrected_points(mesh)
        for wanted in (math.pi / 2, -math.pi / 2):
            runtime._test_pose(bpy.context, wanted)
            corrected = fixtures.evaluated_points(mesh)
            measured = []
            for ring in record['rings'][first:last + 1]:
                index = ring['vertices'][0]
                a, b = raw[index] - f['pivot'], corrected[index] - f['pivot']
                a -= f['axis'] * a.dot(f['axis'])
                b -= f['axis'] * b.dot(f['axis'])
                self.assertAlmostEqual(a.length, b.length, delta=1e-5)
                angle = math.atan2(f['axis'].dot(a.cross(b)), a.dot(b))
                self.assertAlmostEqual(angle, wanted * ring['ratio'], delta=2e-4)
                measured.append(angle / wanted)
            self.assertTrue(all(a <= b + 1e-5 for a, b in zip(measured, measured[1:])))
            self.assertAlmostEqual(measured[-1], 1., delta=2e-4)
            self.assertNotIn(mesh.name, runtime._ERRORS)

    def test_existing_bounded_profile_keeps_its_original_support_on_ui_reentry(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False)
        mesh = f['mesh']
        record = runtime.start_test(bpy.context, mesh, initial_angle=math.pi / 2)
        self.assertNotEqual(record.get('distribution'), 'WRIST_CONTINUOUS')
        runtime.set_range(bpy.context, 1, 6)
        runtime.set_ratio(bpy.context, 3, .27)
        runtime.finish_test(bpy.context, True)
        saved = runtime._records(mesh)['L']
        ui_settings()
        reopened = runtime.start_paired_test(bpy.context)
        for name in ('distribution', 'rings', 'vertices', 'positions', 'range_start', 'range_end'):
            self.assertEqual(reopened.get(name), saved.get(name))
        runtime._test_pose(bpy.context, math.pi / 2)
        fixtures.assert_runtime_geometry(f, 'Existing bounded profile UI reentry')

    def test_explicit_recapture_anchors_old_profile_at_zero_and_full_wrist(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False)
        mesh = f['mesh']
        record = runtime.start_test(bpy.context, mesh)
        first, last = record['range_start'], record['range_end']
        runtime.set_ratio(bpy.context, first, .2)
        runtime.set_ratio(bpy.context, last, .8)
        runtime.set_ratio(bpy.context, 3, .37)
        runtime.finish_test(bpy.context, True)
        ui_settings()
        fresh = runtime.start_paired_test(bpy.context, recapture=True)
        self.assertEqual(fresh['distribution'], 'WRIST_CONTINUOUS')
        self.assertEqual(fresh['rings'][fresh['range_start']]['ratio'], 0.)
        self.assertEqual(fresh['rings'][fresh['range_end']]['ratio'], 1.)
        self.assertEqual(fresh['rings'][3]['ratio'], .37)

    def test_continuous_start_loop_is_fixed_even_with_original_hand_weight(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False, forearm_key=False)
        mesh = f['mesh']
        ui_settings()
        record = runtime.start_paired_test(bpy.context)
        first = record['rings'][record['range_start']]['vertices']
        mesh.vertex_groups[f['lower_name']].add(first, .75, 'REPLACE')
        mesh.vertex_groups[f['hand_name']].add(first, .25, 'REPLACE')
        raw = fixtures.uncorrected_points(mesh)
        runtime._test_pose(bpy.context, math.pi / 2)
        result = fixtures.evaluated_points(mesh)
        for index in first:
            self.assertLess((result[index]-raw[index]).length, 1e-5)

    def test_expansion_reuses_connectivity_only_within_one_read(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False)
        mesh, arm = f['mesh'], f['armature']
        original = topology._mesh_graph
        with patch.object(topology, '_mesh_graph', wraps=original) as graph:
            first = topology.expand_rings(mesh, arm, f['lower_name'], f['rings'][3])
            self.assertEqual(graph.call_count, 1)
            second = topology.expand_rings(mesh, arm, f['lower_name'], f['rings'][3])
            self.assertEqual(graph.call_count, 2, 'Do not retain mesh topology across calls')
        self.assertEqual(first, second)
        self.assertGreater(len(first), 3)

    def test_manual_missing_loop_keeps_continuous_tail_and_saved_anchor_identities(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False)
        mesh, arm = f['mesh'], f['armature']
        captured = topology.detect_rings(mesh, arm, f['lower_name'], f['hand_name'])
        missing = captured.pop(4)
        runtime.start_test(bpy.context, mesh, rings_override=captured, continuous=True)
        runtime.finish_test(bpy.context, True)
        before = runtime._records(mesh)['L']
        anchors = {name: set(before['rings'][before[name]]['vertices'])
                   for name in ('range_start', 'range_end', 'current_ring')}
        last_position = before['rings'][-1]['position']
        tail = {index for index, position in zip(before['vertices'], before['positions'])
                if position > last_position}
        self.assertTrue(tail, 'Fixture must contain a connected hand tail beyond the last loop')
        runtime.editor.manual_add_loop(bpy.context, mesh, 'L', missing)
        after = runtime._records(mesh)['L']
        self.assertEqual(after.get('distribution'), 'WRIST_CONTINUOUS')
        self.assertEqual({index for index, position in zip(after['vertices'], after['positions'])
                          if position > after['rings'][-1]['position']}, tail)
        for name, vertices in anchors.items():
            self.assertEqual(set(after['rings'][after[name]]['vertices']), vertices)
        self.assertTrue(any(set(ring['vertices']) == set(missing['vertices']) for ring in after['rings']))

    def test_continuous_range_edits_keep_authored_interiors_and_protect_anchors(self):
        f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False)
        mesh = f['mesh']
        runtime.start_test(bpy.context, mesh, continuous=True)
        runtime.set_ratio(bpy.context, 3, .27)
        original = runtime._records(mesh)
        runtime.set_range(bpy.context, 1, 6)
        changed = runtime._records(mesh)
        self.assertEqual(changed['L']['rings'][1]['ratio'], 0.)
        self.assertEqual(changed['L']['rings'][6]['ratio'], 1.)
        for index, old in enumerate(original['L']['rings']):
            if index not in (1, 6):
                self.assertEqual(changed['L']['rings'][index], old)
        for action in (lambda: runtime.set_ratio(bpy.context, 1, .2),
                       lambda: runtime.set_ratio(bpy.context, 6, .8),
                       lambda: runtime.editor.apply_batch(bpy.context, 1, 3, 1, .4),
                       lambda: runtime.editor.apply_batch(bpy.context, 4, 3, 1, .4)):
            with self.assertRaisesRegex(ValueError, 'Start loop stays'):
                action()
            self.assertEqual(runtime._records(mesh), changed, 'Rejected anchor edits must be atomic')
        runtime.editor.apply_batch(bpy.context, 1, 1, 1, 0.)
        runtime.editor.apply_batch(bpy.context, 6, 1, 1, 1.)
        self.assertEqual(runtime._records(mesh), changed)
        runtime.editor.undo(bpy.context)
        self.assertEqual(runtime._records(mesh), original, 'Undo must restore prior boundary ratios as well as indices')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SetupTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
