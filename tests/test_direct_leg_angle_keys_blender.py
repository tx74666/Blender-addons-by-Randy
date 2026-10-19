"""Direct leg Pole Angle cuts retain earlier IK animation and roll back keys.

Run in a disposable background factory-startup Blender process. These generated
fixtures never open the artist's X file. The save/reopen check enables scripts
for its own trusted temporary file, following the X validation preference.
"""
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy
from mathutils import Euler

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import limb_ik, limb_ik_fk as match, limb_ik_fk_batch as batch
import test_limb_ik_fk_blender as fixture


def ik_constraint(data):
    constraints = [con for _owner, con, record in data['entries']
                   if record['role'] == 'IK']
    if len(constraints) != 1:
        raise AssertionError('The Direct fixture needs one native IK constraint.')
    return constraints[0]


def curve_signature(curve):
    return (curve.data_path, curve.array_index, curve.extrapolation, curve.lock, curve.mute,
            tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                   point.handle_left_type, point.handle_right_type, point.interpolation,
                   point.easing, point.type) for point in curve.keyframe_points))


def action_signature(action):
    if action is None:
        return None
    return (action.as_pointer(), action.name, bool(action.use_fake_user),
            tuple(curve_signature(curve) for curve in limb_ik._fcurves_for_action(action)))


class DirectLegAngleKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background or '--factory-startup' not in sys.argv:
            raise RuntimeError('Direct angle key tests need disposable background factory-startup Blender.')
        fixture.base.ensure_registered()

    def make(self, selected='LEFT_LEG', *, pair=False):
        armature, key, _data = fixture.build('DIRECT_PREROLL', selected, toes=True)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        keys = (key,)
        if pair:
            settings = limb_ik._settings(bpy.context)
            settings.selected_limb = 'RIGHT_LEG'
            self.assertEqual(bpy.ops.character_designer.limb_ik_direct_preroll_check(),
                             {'FINISHED'}, settings.last_message)
            self.assertEqual(bpy.ops.character_designer.limb_ik_build_selected(),
                             {'FINISHED'}, settings.last_message)
            keys = (('LEG', 'L'), ('LEG', 'R'))
        inventory = limb_ik._validate_inventory(armature)
        self.assertEqual(inventory['schema'], limb_ik.DIRECT_PREROLL_SCHEMA)
        return armature, keys, {key: inventory['rigs'][key] for key in keys}

    def frame(self, armature, value):
        integer = math.floor(value)
        bpy.context.scene.frame_set(integer, subframe=value - integer)
        fixture.update(armature)

    def pose(self, armature, data):
        fixture.update(armature)
        names = {name for row in data.values() for name in match.pose_names(row)}
        return match._matrices(armature, names)

    def angle_curve(self, armature, data):
        path = ik_constraint(data).path_from_id('pole_angle')
        action = armature.animation_data.action if armature.animation_data else None
        curves = [curve for curve in limb_ik._fcurves_for_action(action)
                  if curve.data_path == path and curve.array_index == 0] if action else []
        self.assertEqual(len(curves), 1, 'The actual IK Pole Angle needs one native scalar curve: ' + path)
        return curves[0]

    def assert_cut(self, armature, data, frame, old_angle, new_angle):
        curve = self.angle_curve(armature, data)
        for value, expected in ((frame - 1., old_angle), (frame, new_angle)):
            points = [point for point in curve.keyframe_points if abs(point.co.x - value) < 1e-5]
            self.assertEqual(len(points), 1, (curve.data_path, value))
            self.assertAlmostEqual(points[0].co.y, expected, delta=1e-6)
        previous = next(point for point in curve.keyframe_points
                        if abs(point.co.x - (frame - 1.)) < 1e-5)
        self.assertEqual(previous.interpolation, 'CONSTANT')
        self.assertAlmostEqual(curve.evaluate(frame - .25), old_angle, delta=1e-6)
        self.assertAlmostEqual(curve.evaluate(frame), new_angle, delta=1e-6)

    def switch(self, armature, keys, mode, *, batched):
        if batched:
            result = batch.switch_all(bpy.context, armature, mode, keys=keys, keyframe=True)
        else:
            self.assertEqual(len(keys), 1)
            result = match.switch_limb(bpy.context, armature, keys[0], mode, keyframe=True)
        self.assertTrue(result['changed'])
        self.assertTrue(result['keyed'])

    def author(self, armature, data, rotation):
        for row in data.values():
            upper = armature.pose.bones[row['chain'][0]]
            upper.rotation_mode = 'XYZ'
            upper.rotation_euler.rotate(Euler(rotation))
        fixture.update(armature)
        return self.pose(armature, data)

    def check_history(self, selected='LEFT_LEG', *, batched=False, reopen=False):
        armature, keys, data = self.make(selected, pair=batched)
        name = armature.name
        self.frame(armature, 1)
        original = self.pose(armature, data)
        changed_angle = {key: False for key in keys}
        for frame, mode, rotation in ((10, 'FK', None), (20, 'IK', (.1, .2, -.07)),
                                      (30, 'FK', None), (40, 'IK', (.03, -.3, .02))):
            self.frame(armature, frame)
            authored = self.author(armature, data, rotation) if rotation else self.pose(armature, data)
            old = {key: float(ik_constraint(data[key]).pole_angle) for key in keys}
            self.switch(armature, keys, mode, batched=batched)
            match._verify(armature, authored)
            for key in keys:
                new = float(ik_constraint(data[key]).pole_angle)
                self.assert_cut(armature, data[key], frame, old[key], new)
                changed_angle[key] |= abs(math.remainder(new - old[key], math.tau)) > 1e-4
            if frame == 20:
                first_ik = authored
            elif frame == 40:
                last_ik = authored
        self.assertTrue(all(changed_angle.values()),
                        'Each tested Direct leg must actually require distinct matched Pole Angles.')
        checks = ((1., original), (9., original), (9.5, original),
                  (20., first_ik), (20.25, first_ik), (25., first_ik),
                  (29.5, first_ik), (29.999, first_ik),
                  (40., last_ik), (40.5, last_ik), (45., last_ik))

        def verify_checks():
            for frame, desired in checks:
                self.frame(armature, frame)
                for row in data.values():
                    self.assertEqual(match.mode_for_rig(armature, row), 'IK')
                match._verify(armature, desired)

        verify_checks()
        if reopen:
            saved_curves = {key: curve_signature(self.angle_curve(armature, data[key])) for key in keys}
            with tempfile.TemporaryDirectory(prefix='cd-direct-leg-angle-keys-') as directory:
                path = str(Path(directory) / 'trusted-direct-leg-angle-keys.blend')
                self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {'FINISHED'})
                self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path, use_scripts=True), {'FINISHED'})
                armature = bpy.data.objects[name]
                inventory = limb_ik._validate_inventory(armature)
                data = {key: inventory['rigs'][key] for key in keys}
                self.assertEqual({key: curve_signature(self.angle_curve(armature, data[key]))
                                  for key in keys}, saved_curves)
                verify_checks()

    def test_single_direct_leg_cuts_keep_old_ik_frames_after_save_reopen(self):
        for selected in ('LEFT_LEG', 'RIGHT_LEG'):
            with self.subTest(selected=selected):
                self.check_history(selected, reopen=True)

    def test_batch_direct_legs_key_actual_angles_and_keep_old_ik_frames(self):
        self.check_history(batched=True)

    def test_existing_angle_curves_keep_prior_subframes_and_exact_bookends(self):
        cases = (('BEZIER', False), ('LINEAR', False), ('CONSTANT', False), ('BEZIER', True))
        for selected in ('LEFT_LEG', 'RIGHT_LEG'):
            for interpolation, exact_previous in cases:
                with self.subTest(selected=selected, interpolation=interpolation, exact=exact_previous):
                    armature, keys, data = self.make(selected)
                    key = keys[0]
                    row = data[key]
                    match.switch_limb(bpy.context, armature, key, 'FK', keyframe=False)
                    constraint = ik_constraint(row)
                    for frame, value in ((1., -.22), (20., .31)):
                        constraint.pole_angle = value
                        self.assertTrue(constraint.keyframe_insert(data_path='pole_angle', frame=frame))
                    curve = self.angle_curve(armature, row)
                    if exact_previous:
                        curve.keyframe_points.insert(9., .08)
                    for point in curve.keyframe_points:
                        point.interpolation = interpolation
                    curve.update()
                    samples = (1., 1.25, 2., 4.125, 5., 7.25, 8., 8.75, 9.)
                    before = {frame: float(curve.evaluate(frame)) for frame in samples}
                    self.frame(armature, 10)
                    authored = self.author(armature, data, (.1, .2, -.07))
                    self.switch(armature, keys, 'IK', batched=False)
                    new_angle = float(constraint.pole_angle)
                    self.assert_cut(armature, row, 10., before[9.], new_angle)
                    curve = self.angle_curve(armature, row)
                    for frame, expected in before.items():
                        self.assertAlmostEqual(curve.evaluate(frame), expected, delta=1e-6,
                                               msg=f'{selected} {interpolation} previous frame {frame}')
                    self.frame(armature, 10)
                    match._verify(armature, authored)

    def test_failed_angle_key_insert_restores_action_angles_and_pose(self):
        for selected in ('LEFT_LEG', 'RIGHT_LEG'):
            for failure_frame in (19., 20.):
                with self.subTest(selected=selected, failure_frame=failure_frame):
                    armature, keys, data = self.make(selected)
                    key = keys[0]
                    row = data[key]
                    self.frame(armature, 10)
                    self.switch(armature, keys, 'FK', batched=False)
                    self.frame(armature, 20)
                    self.author(armature, data, (.1, .2, -.07))
                    constraint = ik_constraint(row)
                    angles = {con.path_from_id('pole_angle'): float(con.pole_angle)
                              for pb in armature.pose.bones for con in pb.constraints if con.type == 'IK'}
                    bases = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in armature.pose.bones}
                    old_action = armature.animation_data.action
                    old_slot = armature.animation_data.action_slot
                    old_signature = action_signature(old_action)
                    actions = {action.as_pointer() for action in bpy.data.actions}
                    constraint_type = type(constraint)
                    insert = constraint_type.keyframe_insert
                    failures = []

                    def fail_angle_key(owner, *args, **kwargs):
                        path = kwargs.get('data_path', args[0] if args else '')
                        if path == 'pole_angle' and kwargs.get('frame') == failure_frame:
                            failures.append(owner.path_from_id(path))
                            return False
                        return insert(owner, *args, **kwargs)

                    with patch.object(constraint_type, 'keyframe_insert', new=fail_angle_key):
                        with self.assertRaisesRegex(limb_ik.LimbIKError, 'Could not key'):
                            self.switch(armature, keys, 'IK', batched=False)
                    self.assertEqual(failures, [constraint.path_from_id('pole_angle')])
                    self.assertEqual(armature.animation_data.action, old_action)
                    self.assertEqual(armature.animation_data.action_slot, old_slot)
                    self.assertEqual(action_signature(old_action), old_signature)
                    self.assertEqual({action.as_pointer() for action in bpy.data.actions}, actions)
                    self.assertEqual(match.mode_for_rig(armature, row), 'FK')
                    self.assertEqual({con.path_from_id('pole_angle'): float(con.pole_angle)
                                      for pb in armature.pose.bones for con in pb.constraints if con.type == 'IK'}, angles)
                    for pb in armature.pose.bones:
                        mode, basis = bases[pb.name]
                        self.assertEqual(pb.rotation_mode, mode)
                        self.assertLess(max(abs(pb.matrix_basis[i][j] - basis[i][j])
                                            for i in range(4) for j in range(4)), 1e-6, pb.name)
                    limb_ik._validate_inventory(armature)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
