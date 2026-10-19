"""Regional Pole layout survives new native proportions and arbitrary roll.

Run only in disposable --background --factory-startup Blender. These fixtures
use public regional release/bind/rebuild; no artist file is opened or saved.
A release runner may preload its frozen package before importing this file.
"""
import copy
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_region as region, body_setup,
    body_setup_transaction as transaction, control_pose_assets, limb_ik,
    limb_ik_fk, root_control, unity_animation)
import test_body_region_blender as regional
from test_body_region_styles_blender import (backup_names, resources, refs,
    selected_generated, shape_state)
from test_body_rest_resync_blender import bound_meshes, mesh_raw, world_surfaces
from test_body_setup_removal_blender import bind_probe_surface


LAYOUT_TOLERANCE = 2e-5


def pole_layouts(rig):
    """Independent geometric observations, using evaluated Armature space.

    Ratios are measured about the hip, not the knee. They describe controller
    layout independently of the modeled knee bend and the IK Pole Angle.
    """
    regional.update(rig)
    inventory = limb_ik._validate_inventory(rig)
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = rig.evaluated_get(graph)
    result = {}
    for side in ('L', 'R'):
        data = inventory['rigs'][('LEG', side)]
        upper, lower = (evaluated.pose.bones[name] for name in data['chain'][:2])
        start, joint, end = upper.head.copy(), lower.head.copy(), lower.tail.copy()
        axis = end - start
        total = (joint - start).length + (end - joint).length
        if min(axis.length, total) <= 1e-8:
            raise AssertionError('Fixture has no usable leg axis: ' + side)
        axis.normalize()
        pole = evaluated.pose.bones[data['pole'].name]
        offset = pole.head - start
        radial = offset - axis * offset.dot(axis)
        configured = data['configured_pole_direction']
        if configured is None:
            configured = data['pole_direction']
        configured = Vector(configured)
        configured -= axis * configured.dot(axis)
        if min(radial.length, configured.length) <= 1e-8:
            raise AssertionError('Fixture has no usable Pole direction: ' + side)
        constraints = [con for _owner, con, record in data['entries']
                       if record['role'] == 'IK']
        if len(constraints) != 1:
            raise AssertionError('Fixture needs one actual IK constraint per leg.')
        con = constraints[0]
        result[side] = {
            'axis': axis.copy(), 'direction': radial.normalized(),
            'configured': configured.normalized(), 'head': pole.head.copy(),
            'hip': start, 'total': total,
            'axial_ratio': offset.dot(axis) / total,
            'radial_ratio': radial.length / total,
            'pole_name': data['pole'].name, 'pole_side': data['pole'].get(limb_ik.SIDE_KEY),
            'constraint_side': con.pole_subtarget,
            'constraint_target_is_rig': con.pole_target is rig,
            'angle': float(con.pole_angle),
        }
    return result


class BodyRegionPoleLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background or '--factory-startup' not in sys.argv:
            raise RuntimeError('Pole layout tests require disposable background factory-startup Blender.')
        character_designer.register()

    def make(self):
        rig, mesh = regional.BodyRegionTests().make(corrective=False)
        skin = bind_probe_surface(rig)
        skin.name = 'Pole layout weighted artist surface'
        skin.shape_key_add(name='Basis')
        authored = skin.shape_key_add(name='Keep authored shoe offset')
        authored.data[0].co.z += .009
        authored.value = .35
        regional.update(rig)
        # Healthy incoming controls on their own sides avoid relying on the
        # separate policy for an older right Pole already compensated behind.
        self.assert_own_sides(pole_layouts(rig))
        return rig, mesh

    def assert_own_sides(self, layouts):
        for side, sign in (('L', 1), ('R', -1)):
            row = layouts[side]
            self.assertGreater(sign * row['hip'].x, .05, side)
            self.assertGreater(sign * row['head'].x, .05,
                               side + ' Pole crossed to the opposite leg side')
            self.assertLess(row['direction'].y, -.8, side + ' Pole is no longer in front')
            self.assertEqual(row['pole_side'], side)
            self.assertEqual(row['constraint_side'], row['pole_name'])
            self.assertTrue(row['constraint_target_is_rig'])
            self.assertTrue(math.isfinite(row['angle']))
            self.assertLessEqual(abs(row['angle']), math.pi + 1e-6)

    def assert_layout_preserved(self, rig, before):
        current = pole_layouts(rig)
        self.assert_own_sides(current)
        compact = {}
        for side, old in before.items():
            new = current[side]
            # A shortest rotation transports the old controller intent when
            # proportions change the hip-to-ankle chord. No knee residual is
            # used to infer a new controller direction.
            wanted = old['axis'].rotation_difference(new['axis']) @ old['configured']
            wanted -= new['axis'] * wanted.dot(new['axis'])
            wanted.normalize()
            self.assertLessEqual((new['direction'] - wanted).length, LAYOUT_TOLERANCE, side)
            self.assertLessEqual((new['configured'] - wanted).length, LAYOUT_TOLERANCE, side)
            for key in ('axial_ratio', 'radial_ratio'):
                self.assertAlmostEqual(new[key], old[key], delta=LAYOUT_TOLERANCE,
                                       msg=side + '.' + key)
            compact[side] = {'head': list(new['head']), 'direction': list(new['direction']),
                'radial_ratio': new['radial_ratio'], 'axial_ratio': new['axial_ratio'],
                'pole_angle_degrees': math.degrees(new['angle'])}
        print('REGION_POLE_LAYOUT', json.dumps(compact), flush=True)
        return current

    def snapshot(self, rig, mesh):
        regional.update(rig)
        unrelated = set(rig.data.bones.keys()) - regional.leg_names(rig)
        return {'rest': copy.deepcopy(control_pose_assets.native_rest(rig)),
            'ids': regional.native_ids(rig, mesh),
            'unrelated': regional.bone_state(rig, unrelated),
            'meshes': {obj.name: mesh_raw(obj) for obj in bound_meshes(rig)},
            'skin': {name: value.copy() for name, value in body_setup._native_skin(rig).items()},
            'surfaces': world_surfaces(rig), 'actions': regional.actions(rig)}

    def assert_protected(self, rig, mesh, before, *, rollback=False):
        regional.update(rig)
        self.assertEqual(control_pose_assets.native_rest(rig), before['rest'])
        self.assertEqual(regional.native_ids(rig, mesh), before['ids'])
        self.assertEqual(regional.bone_state(rig, before['unrelated']), before['unrelated'])
        self.assertEqual({obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}, before['meshes'])
        self.assertEqual(regional.actions(rig), before['actions'])
        if rollback:
            regional.BodyRegionTests().assertDeformationRecovered(rig, before['skin'], before['surfaces'])
        else:
            from character_designer import body_setup_removal
            # Baseline04 and fixed-Pole08 share an approximately 3e-5 float32
            # scale residual after a successful shortened-leg bind. Success
            # uses its existing public 2e-4 skin contract; it is not failed-
            # operation recovery, whose separate 2e-5 check remains above.
            current_skin = body_setup._native_skin(rig)
            self.assertEqual(set(current_skin), set(before['skin']))
            skin_errors = {name: max(abs(a - b) for row_a, row_b in zip(saved, current_skin[name])
                                    for a, b in zip(row_a, row_b))
                           for name, saved in before['skin'].items()}
            current_surfaces = world_surfaces(rig)
            self.assertEqual(set(current_surfaces), set(before['surfaces']))
            surface_errors = {}
            for name, saved in before['surfaces'].items():
                self.assertEqual(len(current_surfaces[name]), len(saved), name)
                surface_errors[name] = max(((a - b).length
                    for a, b in zip(current_surfaces[name], saved)), default=0.)
            skin_max = max(skin_errors.values(), default=0.)
            surface_max = max(surface_errors.values(), default=0.)
            print('REGION_SUCCESS_DEFORMATION', json.dumps({
                'max_native_skin_matrix_error': skin_max,
                'native_bones_checked': len(skin_errors),
                'skin_matrix_limit': body_setup_removal.SKIN_MATRIX_TOLERANCE,
                'max_world_surface_error': surface_max,
                'bound_meshes_checked': len(surface_errors), 'surface_limit': 1e-4,
            }), flush=True)
            self.assertLessEqual(skin_max, body_setup_removal.SKIN_MATRIX_TOLERANCE,
                                 f'Public successful-bind skin matrix drift: {skin_errors!r}')
            self.assertLessEqual(surface_max, 1e-4,
                                 f'Public successful-bind world surface drift: {surface_errors!r}')
        self.assertFalse(refs(rig))
        self.assertFalse(backup_names())
        self.assertFalse(body_setup._ATOMIC_STACK)
        self.assertIsNone(region._OWNER_OPERATION)
        self.assertIsNone(region._BIND_AUTH)
        self.assertTrue(limb_ik._single_armature_user(rig))

    def move_knees_inward(self, rig, amount=.035):
        limb_ik._mode_set(bpy.context, rig, 'EDIT')
        mirror = rig.data.use_mirror_x
        rig.data.use_mirror_x = False
        try:
            for side, sign in (('L', 1), ('R', -1)):
                upper, lower = (rig.data.edit_bones[name + '.' + side]
                                for name in ('thigh', 'shin'))
                connected = lower.use_connect
                lower.use_connect = False
                joint = upper.tail.copy()
                joint.x -= sign * amount
                upper.tail, lower.head = joint, joint
                lower.use_connect = connected
            limb_ik._mode_set(bpy.context, rig, 'POSE')
        finally:
            rig.data.use_mirror_x = mirror
        regional.update(rig)

    def test_inward_knees_and_repeated_rebuild_keep_poles_on_own_side(self):
        rig, mesh = self.make()
        layout = pole_layouts(rig)
        region.release(bpy.context, rig, 'LEGS')
        self.move_knees_inward(rig)
        # The former knee-residual placement would put both pivots across X=0.
        # Assert that this fixture actually exposes that geometry before bind.
        for side, sign in (('L', 1), ('R', -1)):
            chain = limb_ik.LimbChain('LEG', side, *('thigh.' + side, 'shin.' + side, 'foot.' + side))
            _start, joint, _end, bend = regional.body_calibration.direction(rig, chain)
            self.assertIsNotNone(bend)
            former_x = joint.x + bend.x * (layout[side]['total'] * .75)
            self.assertLess(sign * former_x, 0., 'Fixture no longer exposes the centerline regression')
        before = self.snapshot(rig, mesh)
        region.bind(bpy.context, rig, 'LEGS')
        self.assert_protected(rig, mesh, before)
        current = self.assert_layout_preserved(rig, layout)
        before = self.snapshot(rig, mesh)
        region.rebuild(bpy.context, rig, 'LEGS')
        self.assert_protected(rig, mesh, before)
        self.assert_layout_preserved(rig, current)

    def test_shortened_legs_keep_saved_direction_and_distance_ratios(self):
        rig, mesh = self.make()
        initial = pole_layouts(rig)
        # Keep two distinct authored distances; a UI default must not replace
        # them when the controls are removed and built again.
        for side, factor in (('L', 1.13), ('R', 1.29)):
            row = initial[side]
            pole = rig.pose.bones[row['pole_name']]
            matrix = pole.matrix.copy()
            axial = row['axis'] * (row['axial_ratio'] * row['total'])
            radial = row['direction'] * (row['radial_ratio'] * row['total'] * factor)
            matrix.translation = row['hip'] + axial + radial
            pole.matrix = matrix
        regional.update(rig)
        layout = pole_layouts(rig)
        region.release(bpy.context, rig, 'LEGS')
        regional.BodyRegionTests().shorten(rig)
        limb_ik._settings(bpy.context).pole_distance_ratio = .25
        before = self.snapshot(rig, mesh)
        region.bind(bpy.context, rig, 'LEGS')
        self.assert_protected(rig, mesh, before)
        current = self.assert_layout_preserved(rig, layout)
        self.assertLess(current['L']['total'], layout['L']['total'] * .9)
        self.assertLess(current['R']['total'], layout['R']['total'] * .9)

    def test_near_pi_source_roll_preserves_fixed_layout_and_native_deformation(self):
        rig, mesh = self.make()
        layout = pole_layouts(rig)
        region.release(bpy.context, rig, 'LEGS')
        self.move_knees_inward(rig, .02)
        limb_ik._mode_set(bpy.context, rig, 'EDIT')
        for side, sign in (('L', 1), ('R', -1)):
            rig.data.edit_bones['thigh.' + side].roll += sign * (math.pi - .02)
            rig.data.edit_bones['shin.' + side].roll -= sign * (math.pi - .03)
        limb_ik._mode_set(bpy.context, rig, 'POSE')
        regional.update(rig)
        before = self.snapshot(rig, mesh)
        actual = limb_ik_fk._match_fixed_pole_angle
        fixed_calls = []

        def observe_fixed(context, armature, data, desired, **kwargs):
            name = data['pole'].name
            start = armature.pose.bones[name].matrix.translation.copy()
            result = actual(context, armature, data, desired, **kwargs)
            self.assertLessEqual((armature.pose.bones[name].matrix.translation - start).length, 1e-6)
            fixed_calls.append(name)
            return result

        with patch.object(limb_ik_fk, '_match_fixed_pole_angle', side_effect=observe_fixed):
            region.bind(bpy.context, rig, 'LEGS')
        self.assertEqual(set(fixed_calls), {layout[side]['pole_name'] for side in ('L', 'R')})
        self.assert_protected(rig, mesh, before)
        self.assert_layout_preserved(rig, layout)

    def test_nonunit_root_scale_and_object_transform_preserve_layout_and_world_skin(self):
        rig, mesh = self.make()
        record = root_control.validate(rig)
        master = rig.pose.bones[record['master']]
        master[root_control.SCALE_PROPERTY] = 1.27
        master.rotation_mode = 'XYZ'
        master.rotation_euler = (.025, -.02, .025)
        master.location = (.015, -.035, .11)
        rig.location = (.27, -.14, .33)
        rig.rotation_euler = (.21, -.16, .31)
        rig.scale = (1.07,) * 3
        regional.update(rig)
        self.assertAlmostEqual(master.scale.x, 1.27, delta=1e-6)
        uniform_scale = master[root_control.SCALE_PROPERTY]
        layout, before = pole_layouts(rig), self.snapshot(rig, mesh)
        world = rig.matrix_world.copy()
        region.rebuild(bpy.context, rig, 'LEGS')
        self.assert_protected(rig, mesh, before)
        self.assert_layout_preserved(rig, layout)
        self.assertEqual(rig.matrix_world, world)
        self.assertEqual(rig.pose.bones[record['master']][root_control.SCALE_PROPERTY], uniform_scale)

    def test_boolean_version_and_finite_overflow_layout_refuse_before_checkpoint(self):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEGS')
        original = rig.data[region.RECORD_KEY]
        try:
            for field, value in (('version', True), ('axial_ratio', 1e300),
                                 ('radial_ratio', 1e300)):
                with self.subTest(field=field):
                    packet = json.loads(original)
                    packet['limbs']['LEG:L']['pole_layout'][field] = value
                    rig.data[region.RECORD_KEY] = json.dumps(packet, sort_keys=True, allow_nan=False)
                    before = regional.scene_state(rig, mesh)
                    rest = copy.deepcopy(unity_animation._rest_state(rig.data))
                    author = {obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}
                    resource_names, saved_refs = resources(), dict(refs(rig))
                    with patch.object(transaction, 'capture',
                            side_effect=AssertionError('Malformed layout reached a mutation checkpoint')) as captured:
                        with self.assertRaises(ValueError):
                            region.bind(bpy.context, rig, 'LEGS')
                    captured.assert_not_called()
                    self.assertEqual(regional.scene_state(rig, mesh), before)
                    self.assertEqual(unity_animation._rest_state(rig.data), rest)
                    self.assertEqual({obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}, author)
                    self.assertEqual(resources(), resource_names)
                    self.assertEqual(refs(rig), saved_refs)
                    self.assertFalse(body_setup._ATOMIC_STACK)
                    self.assertIsNone(region._OWNER_OPERATION)
                    self.assertIsNone(region._BIND_AUTH)
        finally:
            rig.data[region.RECORD_KEY] = original

    def test_isolated_fixed_pole_switch_restores_angle_without_outer_transaction(self):
        rig, mesh = self.make()
        inventory = limb_ik._validate_inventory(rig)
        data = inventory['rigs'][('LEG', 'L')]
        desired = {name: rig.pose.bones[name].matrix.copy()
                   for name in limb_ik_fk.pose_names(data)}
        expected_native = {name: rig.pose.bones[name].matrix.copy()
                           for name in control_pose_assets.native_rest(rig)}
        constraints = [(con, bool(con.mute), float(con.influence),
                        float(con.pole_angle) if record['role'] == 'IK' else None)
                       for _pb, con, record in data['entries']]
        target = rig.pose.bones[data['target'].name]
        switch_value = target[limb_ik_fk.PROPERTY]
        pole_head = rig.pose.bones[data['pole'].name].matrix.translation.copy()
        rest = copy.deepcopy(unity_animation._rest_state(rig.data))
        author = {obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}
        skin = {name: value.copy() for name, value in body_setup._native_skin(rig).items()}
        surfaces, shapes, resource_names = world_surfaces(rig), shape_state(rig), resources()
        ids = regional.native_ids(rig, mesh)
        actual = limb_ik_fk._match_fixed_pole_angle
        writes = []

        def reject_after_angle_write(context, armature, entry, wanted, **kwargs):
            actual(context, armature, entry, wanted, **kwargs)
            con = next(con for _pb, con, record in entry['entries'] if record['role'] == 'IK')
            con.pole_angle = max(-math.pi, min(math.pi, float(con.pole_angle) + .345))
            regional.update(armature)
            writes.append(float(con.pole_angle))
            raise ValueError('Injected isolated fixed Pole angle failure')

        with patch.object(limb_ik_fk, '_match_fixed_pole_angle', side_effect=reject_after_angle_write), \
             patch.object(transaction, 'capture',
                 side_effect=AssertionError('An isolated switch must not depend on a Body checkpoint')) as captured:
            with self.assertRaisesRegex(ValueError, 'Injected isolated fixed Pole angle failure'):
                limb_ik_fk.switch_limb(bpy.context, rig, ('LEG', 'L'), 'IK',
                    keyframe=False, desired_pose=desired, fixed_pole=True,
                    calibrated_rest=True, sync_display=False)
        self.assertTrue(writes)
        captured.assert_not_called()
        regional.update(rig)
        for con, mute, influence, angle in constraints:
            self.assertEqual(con.mute, mute)
            self.assertEqual(con.influence, influence)
            if angle is not None:
                self.assertEqual(float(con.pole_angle), angle)
                self.assertNotEqual(writes[0], angle, 'The fault must actually change a native angle.')
        self.assertEqual(target[limb_ik_fk.PROPERTY], switch_value)
        self.assertLessEqual((rig.pose.bones[data['pole'].name].matrix.translation - pole_head).length, 1e-6)
        regional.BodyRegionTests().assertMatrices(rig, expected_native)
        self.assertEqual(unity_animation._rest_state(rig.data), rest)
        self.assertEqual({obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}, author)
        self.assertEqual(shape_state(rig), shapes)
        self.assertEqual(resources(), resource_names)
        self.assertEqual(regional.native_ids(rig, mesh), ids)
        regional.BodyRegionTests().assertDeformationRecovered(rig, skin, surfaces)
        self.assertFalse(body_setup._ATOMIC_STACK)

    def test_angle_matching_failure_restores_previous_layout_and_complete_region(self):
        rig, mesh = self.make()
        before = self.snapshot(rig, mesh)
        full_rest = copy.deepcopy(unity_animation._rest_state(rig.data))
        scene, shapes, resource_names = regional.scene_state(rig, mesh), shape_state(rig), resources()
        recreated = selected_generated(rig, 'LEGS')
        actual = limb_ik_fk._match_fixed_pole_angle
        calls = []

        def reject_after_native_angle_write(context, armature, data, desired, **kwargs):
            actual(context, armature, data, desired, **kwargs)
            con = next(con for _owner, con, record in data['entries'] if record['role'] == 'IK')
            con.pole_angle = max(-math.pi, min(math.pi, float(con.pole_angle) + .123))
            regional.update(armature)
            calls.append(data['pole'].name)
            raise ValueError('Injected fixed Pole angle failure after native write')

        with patch.object(limb_ik_fk, '_match_fixed_pole_angle', side_effect=reject_after_native_angle_write):
            with self.assertRaisesRegex(ValueError, 'Injected fixed Pole angle failure') as refusal:
                region.rebuild(bpy.context, rig, 'LEGS')
        self.assertTrue(calls)
        self.assertIs(getattr(refusal.exception, '_character_designer_body_rollback_restored', None), True)
        self.assert_protected(rig, mesh, before, rollback=True)
        regional.BodyRegionTests().assertRecoveredScene(regional.scene_state(rig, mesh), scene,
                                                      recreated=recreated)
        current_rest = unity_animation._rest_state(rig.data)
        self.assertEqual(set(current_rest), set(full_rest))
        for name, old in full_rest.items():
            self.assertTrue(unity_animation._same_current_rest_bone(old, current_rest[name]), name)
            self.assertEqual(old['use_connect'], current_rest[name]['use_connect'], name)
        self.assertEqual(shape_state(rig), shapes)
        self.assertEqual(resources(), resource_names)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BodyRegionPoleLayoutTests))
    if not result.wasSuccessful():
        raise RuntimeError('Regional fixed Pole layout tests failed')
