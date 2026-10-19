"""Public generation must retain a bound shoe's unkeyed Object adjustment.

Disposable --background --factory-startup fixtures only. No artist file is
opened/saved, no Undo is invoked, and no keyed animation is deleted or muted.
The optional observer is used only by an external evidence runner.
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_calibration, body_original_owner,
    body_region, body_setup, body_setup_removal, control_pose_assets, limb_ik)
import test_body_calibration_blender as calibration
import test_body_region_blender as regional
import test_body_setup_plan_blender as planning
from test_body_region_styles_blender import resources, selected_generated, shape_state
from test_body_rest_resync_blender import bound_meshes, mesh_raw, world_surfaces
from test_body_setup_removal_blender import bind_probe_surface


OBSERVER = None
FRAME, SUBFRAME, UNKEYED_Z = 20, .375, .0745561
FAILURE = 'Injected generation failure after actual control topology commit'


def notify(stage, rig, shoes, **details):
    if OBSERVER is not None:
        OBSERVER(stage, rig, shoes, details)


def object_channels(obj):
    return {field: tuple(getattr(obj, field)) for field in
            ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle',
             'scale', 'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')
            } | {'rotation_mode': obj.rotation_mode}


class BodyShoeTransformPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background or '--factory-startup' not in sys.argv or bpy.data.filepath:
            raise RuntimeError('Shoe transform checks require an empty disposable factory-startup background process.')
        character_designer.register()

    def make(self, *, controls):
        if controls:
            rig, mesh = regional.BodyRegionTests().make(corrective=False)
        else:
            rig, mesh = planning.fixture()
            rig.pose.bones['Hips'].rotation_euler = (0, 0, 0)
            regional.update(rig)
            calibration.palms(rig)
            calibration.prepare(rig)
            self.assertFalse(body_setup.has_generated(rig))
            self.assertTrue(body_calibration.generation_ready(bpy.context, rig))
        shoes = bind_probe_surface(rig)
        shoes.name = 'Animated bound shoe Object'
        shoes.shape_key_add(name='Basis')
        authored = shoes.shape_key_add(name='Keep authored shoe geometry')
        authored.data[-1].co.z += .013
        authored.value = .35
        limb_ik._mode_set(bpy.context, rig, 'OBJECT')
        shoes.rotation_mode = 'XYZ'
        shoes.location = (.021, -.031, .008)
        self.assertTrue(shoes.keyframe_insert(data_path='location', frame=24))
        shoes.location.z = .018
        self.assertTrue(shoes.keyframe_insert(data_path='location', frame=25))
        action = shoes.animation_data.action
        action.name = 'ShoesAction with Object location keys'
        for curve in limb_ik._fcurves_for_action(action):
            for point in curve.keyframe_points:
                point.interpolation = 'LINEAR'
        scene = bpy.context.scene
        scene.frame_set(FRAME, subframe=SUBFRAME)
        keyed_location = tuple(shoes.location)
        shoes.location.z += UNKEYED_Z
        shoes.update_tag(refresh={'OBJECT'})
        regional.update(rig)
        # Establish the intended evaluated override before the tested action.
        # Its Object animation still points at its earlier keyed position.
        intended = tuple(shoes.location)
        self.assertAlmostEqual(intended[2] - keyed_location[2], UNKEYED_Z, delta=1e-7)
        graph = bpy.context.evaluated_depsgraph_get()
        evaluated = shoes.evaluated_get(graph)
        self.assertLessEqual(max(abs(a - b) for row_a, row_b in
            zip(shoes.matrix_world, evaluated.matrix_world) for a, b in zip(row_a, row_b)), 1e-6)
        self.assertEqual((scene.frame_current, scene.frame_subframe), (FRAME, SUBFRAME))
        self.assertEqual(shoes.animation_data.action, action)
        self.assertTrue(all(point.co.x in {24., 25.}
            for curve in limb_ik._fcurves_for_action(action) for point in curve.keyframe_points))
        return rig, mesh, shoes

    def snapshot(self, rig, mesh, shoes):
        regional.update(rig)
        action = shoes.animation_data.action
        result = {'channels': object_channels(shoes), 'world': regional.matrix(shoes.matrix_world),
            'basis': regional.matrix(shoes.matrix_basis), 'parent_inverse': regional.matrix(shoes.matrix_parent_inverse),
            'frame': (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe),
            'action': action, 'action_fingerprint': body_original_owner._animation_fingerprint(action),
            'rest': copy.deepcopy(control_pose_assets.native_rest(rig)),
            'ids': regional.native_ids(rig, mesh),
            'shoe_ids': (shoes.as_pointer(), shoes.data.as_pointer(), shoes.data.shape_keys.as_pointer()),
            'author_meshes': {obj.name: mesh_raw(obj) for obj in bound_meshes(rig)},
            'skin': {n: m.copy() for n, m in body_setup._native_skin(rig).items()},
            'surfaces': world_surfaces(rig)}
        self.assertEqual(result['channels'], object_channels(shoes), 'Preparing the reference reset the unkeyed Object input.')
        return result

    def assert_preserved(self, rig, mesh, shoes, before, *, rollback):
        regional.update(rig)
        self.assertEqual(object_channels(shoes), before['channels'],
            'Controller generation reset the shoe Object transform to its Action value.')
        self.assertEqual(regional.matrix(shoes.matrix_world), before['world'])
        self.assertEqual(regional.matrix(shoes.matrix_basis), before['basis'])
        self.assertEqual(regional.matrix(shoes.matrix_parent_inverse), before['parent_inverse'])
        self.assertEqual((bpy.context.scene.frame_current, bpy.context.scene.frame_subframe), before['frame'])
        self.assertIs(shoes.animation_data.action, before['action'])
        self.assertEqual(body_original_owner._animation_fingerprint(shoes.animation_data.action),
                         before['action_fingerprint'])
        self.assertEqual(control_pose_assets.native_rest(rig), before['rest'])
        self.assertEqual(regional.native_ids(rig, mesh), before['ids'])
        self.assertEqual((shoes.as_pointer(), shoes.data.as_pointer(), shoes.data.shape_keys.as_pointer()),
                         before['shoe_ids'])
        self.assertEqual({obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}, before['author_meshes'])
        if rollback:
            regional.BodyRegionTests().assertDeformationRecovered(rig, before['skin'], before['surfaces'])
        else:
            skin = body_setup._native_skin(rig)
            self.assertEqual(set(skin), set(before['skin']))
            skin_max = max((abs(a - b) for name, expected in before['skin'].items()
                for first, second in zip(skin[name], expected) for a, b in zip(first, second)), default=0.)
            current = world_surfaces(rig)
            self.assertEqual(set(current), set(before['surfaces']))
            surface_max = 0.
            for name, expected in before['surfaces'].items():
                self.assertEqual(len(current[name]), len(expected), name)
                surface_max = max(surface_max,
                    max(((a - b).length for a, b in zip(current[name], expected)), default=0.))
            print('SHOE_OBJECT_GENERATION_PRESERVATION', json.dumps({
                'skin_matrix_max': skin_max, 'skin_limit': body_setup_removal.SKIN_MATRIX_TOLERANCE,
                'world_surface_max': surface_max, 'surface_limit': 1e-4}), flush=True)
            self.assertLessEqual(skin_max, body_setup_removal.SKIN_MATRIX_TOLERANCE)
            self.assertLessEqual(surface_max, 1e-4)
        self.assertFalse(body_setup._ATOMIC_STACK)
        self.assertIsNone(body_region._OWNER_OPERATION)
        self.assertIsNone(body_region._BIND_AUTH)

    def exercise(self, *, controls, failure):
        rig, mesh, shoes = self.make(controls=controls)
        before = self.snapshot(rig, mesh, shoes)
        scene = regional.scene_state(rig, mesh)
        old_shapes, old_resources = shape_state(rig), resources()
        removed = selected_generated(rig, 'LEGS') if controls else ()
        old_ids = {key: data['rig_id'] for key, data in limb_ik._validate_inventory(rig)['rigs'].items()}
        calls = []
        actual = limb_ik._create_control_bones

        def fail_after_real_commit(context, armature, *args, **kwargs):
            result = actual(context, armature, *args, **kwargs)
            calls.append(True)
            notify('fault_after_actual_control_commit', rig, shoes)
            raise ValueError(FAILURE)

        notify('before_public_operation', rig, shoes, case=self.id(), controls=controls, failure=failure)
        try:
            if failure:
                with patch.object(limb_ik, '_create_control_bones', side_effect=fail_after_real_commit):
                    result = (bpy.ops.character_designer.body_region(action='REBUILD', region='LEGS') if controls else
                              bpy.ops.character_designer.body_setup(action='GENERATE'))
            else:
                result = (bpy.ops.character_designer.body_region(action='REBUILD', region='LEGS') if controls else
                          bpy.ops.character_designer.body_setup(action='GENERATE'))
            notify('after_public_operation', rig, shoes, operator_result=sorted(result))
            # Position is the first assertion: a warning or complete rig
            # rollback cannot excuse losing an unrelated authored Object input.
            self.assert_preserved(rig, mesh, shoes, before, rollback=failure)
            if failure:
                self.assertTrue(calls, 'The operation never reached the injected native commit failure.')
                self.assertIn(result, ({'CANCELLED'}, {'FINISHED'}))
                regional.BodyRegionTests().assertRecoveredScene(regional.scene_state(rig, mesh), scene,
                                                              recreated=removed)
                self.assertEqual(shape_state(rig), old_shapes)
                self.assertEqual(resources(), old_resources)
                self.assertEqual({key: data['rig_id'] for key, data in
                    limb_ik._validate_inventory(rig)['rigs'].items()}, old_ids)
            else:
                self.assertEqual(result, {'FINISHED'})
                inventory = limb_ik._validate_inventory(rig)
                self.assertTrue(body_setup.has_generated(rig), 'The public generator did not apply its requested operation.')
                for side in ('L', 'R'):
                    key = ('LEG', side)
                    self.assertIn(key, inventory['rigs'])
                    if controls:
                        self.assertNotEqual(inventory['rigs'][key]['rig_id'], old_ids[key],
                                            'A recovered rejection must not count as successful Rebuild.')
        finally:
            notify('case_end', rig, shoes)

    def test_public_regional_rebuild_keeps_unkeyed_shoe_object_location_at_subframe(self):
        self.exercise(controls=True, failure=False)

    def test_public_regional_failure_restores_unkeyed_shoe_object_location_at_subframe(self):
        self.exercise(controls=True, failure=True)

    def test_public_whole_generate_keeps_unkeyed_shoe_object_location_at_subframe(self):
        self.exercise(controls=False, failure=False)

    def test_public_whole_generate_failure_restores_unkeyed_shoe_object_location_at_subframe(self):
        self.exercise(controls=False, failure=True)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BodyShoeTransformPreservationTests))
    if not result.wasSuccessful():
        raise RuntimeError('Public generation changed a bound shoe Object adjustment.')
