"""Original forearm capture keeps owned mutes and drives the native hand.

Run only in an isolated Blender --background --factory-startup process.
"""
import json
import math
import sys
import unittest
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_mode as original, body_setup, limb_ik,
    forearm_original_inventory as inventory, forearm_twist as runtime,
    torso_controls, spine_ik_fk, eye_controls, root_control, foot_controls)
import test_body_setup_plan_blender as planning
import test_body_calibration_blender as calibration
import test_forearm_twist_blender as fixtures


def sleeve(rig):
    lower = rig.data.bones['forearm.L']
    axis = (lower.tail_local - lower.head_local).normalized()
    radial_a = lower.matrix_local.to_3x3() @ Vector((1, 0, 0))
    radial_b = lower.matrix_local.to_3x3() @ Vector((0, 0, 1))
    vertices, faces, loops = [], [], []
    for row in range(8):
        t = row / 7
        center = lower.head_local.lerp(lower.tail_local, t)
        loop = []
        for spoke in range(12):
            angle = spoke * math.tau / 12
            loop.append(len(vertices))
            vertices.append(center + .03 * (math.cos(angle) * radial_a + math.sin(angle) * radial_b))
        loops.append(loop)
    for row in range(7):
        for spoke in range(12):
            neighbor = (spoke + 1) % 12
            faces.append((loops[row][spoke], loops[row + 1][spoke], loops[row + 1][neighbor], loops[row][neighbor]))
    outside = list(range(len(vertices), len(vertices) + 3))
    vertices += [(0, 0, .95), (.02, 0, .95), (0, .02, .95)]
    faces.append(tuple(outside))
    data = bpy.data.meshes.new('Original sleeve')
    data.from_pydata(vertices, [], faces)
    uv = data.uv_layers.new(name='Artist UV')
    for index, value in enumerate(uv.data):
        value.uv = ((index % 5) / 5, (index % 7) / 7)
    mesh = bpy.data.objects.new('Original sleeve', data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.matrix_world = rig.matrix_world
    for name in ('forearm.L', 'hand.L', 'Hips'):
        mesh.vertex_groups.new(name=name)
    for row, loop in enumerate(loops):
        share = (row / 7) ** 3
        mesh.vertex_groups['forearm.L'].add(loop, 1 - share, 'REPLACE')
        mesh.vertex_groups['hand.L'].add(loop, share, 'REPLACE')
    mesh.vertex_groups['Hips'].add(outside, 1, 'REPLACE')
    mesh.modifiers.new('Artist skin', 'ARMATURE').object = rig
    mesh.shape_key_add(name='Basis')
    blink = mesh.shape_key_add(name='Blink_L')
    blink.data[outside[0]].co.x += .012
    blink.value = .3
    smile = mesh.shape_key_add(name='Smile_R')
    smile.data[loops[2][0]].co += radial_a * .004
    smile.value = .2
    fixtures.activate_mesh(mesh)
    return mesh, outside


def protected(rig, mesh):
    return {'structure': fixtures.structure_snapshot(rig, mesh),
            'geometry': (tuple(tuple(vertex.co) for vertex in mesh.data.vertices),
                         tuple(tuple(edge.vertices) for edge in mesh.data.edges),
                         tuple((layer.name, tuple(tuple(item.uv) for item in layer.data))
                               for layer in mesh.data.uv_layers)),
            'keys': fixtures.key_snapshot(mesh, ('Basis', 'Blink_L', 'Smile_R')),
            'mutes': {(pb.name, con.name): con.mute for pb in rig.pose.bones for con in pb.constraints},
            'pose': fixtures.pose_snapshot(rig),
            'session': rig.get(original.SESSION), 'calibration': mesh.get(runtime.RECORD_KEY),
            'drivers': tuple((fc.data_path, fc.array_index, fc.mute, fc.driver.expression)
                             for fc in rig.animation_data.drivers)}


class ForearmOriginalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        self.rig, _body = planning.fixture()
        # The discovery fixture includes a posed Hips. This disposable rig must
        # complete the real calibration workflow before generating Direct controls.
        self.rig.pose.bones['Hips'].matrix_basis = Matrix.Identity(4)
        torso_controls._update(bpy.context, self.rig)
        calibration.palms(self.rig)
        calibration.prepare(self.rig)
        body_setup.generate(bpy.context, self.rig)
        self.mesh, self.outside = sleeve(self.rig)

    def tearDown(self):
        if runtime._SESSION is not None:
            runtime.finish_test(bpy.context, confirm=False)

    def enter(self):
        fixtures.activate_mesh(self.rig)
        original.enter(bpy.context, self.rig)
        fixtures.activate_mesh(self.mesh)

    def test_all_body_extensions_validate_without_toggling_constraints(self):
        self.enter()
        modules = (torso_controls, spine_ik_fk, eye_controls, root_control)
        self.assertTrue(all(module.get_record(self.rig) for module in modules))
        self.assertEqual(set(foot_controls.records(self.rig)), {'L', 'R'})
        before = protected(self.rig, self.mesh)
        with self.assertRaises(ValueError):
            limb_ik._validate_inventory(self.rig)
        resolved = inventory.validate(self.rig)
        self.assertEqual(set(resolved['rigs']), {('ARM', 'L'), ('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R')})
        arm, route = runtime._resolve_rig(self.mesh, 'L')
        self.assertEqual(arm, self.rig)
        self.assertTrue(route['fk_source'])
        self.assertEqual(route['target'].name, 'hand.L')
        self.assertEqual(protected(self.rig, self.mesh), before)

    def test_basis_edit_recapture_native_preview_cancel_and_controls_resume(self):
        runtime.start_test(bpy.context, self.mesh)
        runtime.set_ratio(bpy.context, 3, .34)
        runtime.finish_test(bpy.context, confirm=True)
        rings = runtime._records(self.mesh)['L']['rings']
        managed = runtime._records(self.mesh)['L']['key']
        managed_id = self.mesh.data.shape_keys.key_blocks[managed].as_pointer()
        self.enter()
        # An artist edit outside the calibrated sleeve must survive recapture.
        self.mesh.data.shape_keys.reference_key.data[self.outside[0]].co.z -= .004
        self.mesh.data.update()
        before = protected(self.rig, self.mesh)
        control = next(record['target'] for pb, _con, record in limb_ik._owned_constraint_records(self.rig)
                       if pb.name == 'forearm.L')
        generated_pose = fixtures.pose_snapshot(self.rig)[control]
        runtime.start_test(bpy.context, self.mesh, recapture=True, initial_angle=math.pi / 2)
        self.assertEqual(runtime._SESSION['target'], 'hand.L')
        self.assertFalse(runtime._SESSION['pose_locked'], 'Owned influence drivers must not lock native hand preview.')
        self.assertAlmostEqual(runtime.current_twist_angle(bpy.context), math.pi / 2, delta=1e-4)
        self.assertEqual(runtime._records(self.mesh)['L']['rings'], rings)
        self.assertEqual(self.mesh.data.shape_keys.key_blocks[managed].as_pointer(), managed_id)
        self.assertNotEqual(fixtures.pose_snapshot(self.rig)['hand.L'], before['pose']['hand.L'])
        self.assertEqual(fixtures.pose_snapshot(self.rig)[control], generated_pose)
        bpy.context.window_manager.character_designer_forearm_twist.test_angle = -math.pi / 2
        self.assertAlmostEqual(runtime.current_twist_angle(bpy.context), -math.pi / 2, delta=1e-4)
        self.assertEqual(fixtures.pose_snapshot(self.rig)[control], generated_pose)
        self.assertNotIn(self.mesh.name, runtime._ERRORS)
        runtime.finish_test(bpy.context, confirm=False)
        after = protected(self.rig, self.mesh)
        self.assertEqual(after['structure'], before['structure'])
        self.assertEqual(after['geometry'], before['geometry'])
        self.assertEqual(after['keys'], before['keys'])
        self.assertEqual(after['mutes'], before['mutes'])
        self.assertEqual(after['session'], before['session'])
        fixtures.assert_pose_snapshot(self.rig, before['pose'], 'Original preview cancelled')
        fixtures.activate_mesh(self.rig)
        original.leave(bpy.context, self.rig)
        runtime.update_runtime(bpy.context.scene)
        self.assertNotIn(self.mesh.name, runtime._ERRORS)
        arm, route = runtime._resolve_rig(self.mesh, 'L')
        self.assertFalse(route.get('fk_source', False))
        self.assertEqual(runtime._records(self.mesh)['L']['target'], route['target'].name)
        self.assertEqual(fixtures.key_snapshot(self.mesh, ('Basis', 'Blink_L', 'Smile_R')), before['keys'])

    def test_influence_driver_and_unexpected_mute_edits_remain_rejected(self):
        self.enter()
        rig = self.rig
        torso = torso_controls.get_record(rig)
        source = next(entry for entry in torso['constraints'] if entry['owner'] in torso['sources'])
        con = rig.pose.bones[source['owner']].constraints[source['name']]
        baseline = con.influence
        con.influence = .17
        before = protected(rig, self.mesh)
        with self.assertRaisesRegex(ValueError, 'constraint.*edited'):
            runtime._resolve_rig(self.mesh, 'L')
        self.assertEqual(protected(rig, self.mesh), before)
        con.influence = baseline
        relations = limb_ik._owned_constraint_records(rig)
        pb, con, record = next(entry for entry in relations if entry[0].name == 'forearm.L')
        driver = next(fc for fc in rig.animation_data.drivers if fc.data_path == con.path_from_id('influence'))
        expression = driver.driver.expression
        driver.driver.expression = 'ik_fk * 0.5'
        before = protected(rig, self.mesh)
        with self.assertRaisesRegex(ValueError, 'driver.*edited'):
            runtime._resolve_rig(self.mesh, 'L')
        self.assertEqual(protected(rig, self.mesh), before)
        driver.driver.expression = expression
        con.mute = False
        with self.assertRaisesRegex(ValueError, 'Original constraint.*changed'):
            runtime._resolve_rig(self.mesh, 'L')
        con.mute = True
        helper_pb, helper_con, _record = next(entry for entry in relations
                                            if entry[0].name not in json.loads(rig[original.SESSION])['names'])
        helper_con.mute = True
        with self.assertRaises(ValueError):
            runtime._resolve_rig(self.mesh, 'L')
        helper_con.mute = False
        armature, route = runtime._resolve_rig(self.mesh, 'L')
        hand = rig.pose.bones['hand.L']
        self.assertFalse(runtime._preview_pose_locked(armature, hand, route))
        # Unlike the intentionally suspended influence driver, a real native
        # rotation driver or key must retain the existing settings-only preview.
        transform_driver = hand.driver_add('rotation_quaternion', 0)
        transform_driver.driver.expression = '1.0'
        self.assertTrue(runtime._preview_pose_locked(armature, hand, route))
        hand.driver_remove('rotation_quaternion', 0)
        hand.keyframe_insert('rotation_quaternion', frame=bpy.context.scene.frame_current)
        self.assertTrue(runtime._preview_pose_locked(armature, hand, route))
        before = protected(rig, self.mesh)
        runtime.start_test(bpy.context, self.mesh, initial_angle=math.pi / 2)
        self.assertTrue(runtime._SESSION['pose_locked'])
        self.assertEqual(fixtures.pose_snapshot(rig), before['pose'])
        runtime.finish_test(bpy.context, confirm=False)
        self.assertEqual(protected(rig, self.mesh), before)

    def test_forged_foreign_constraint_entry_is_not_accepted_as_original_owned(self):
        self.enter()
        rig = self.rig
        foreign = rig.pose.bones['hand.L'].constraints.new('COPY_ROTATION')
        foreign.name, foreign.target, foreign.subtarget, foreign.mute = 'Artist relation', rig, 'Hips', True
        saved = json.loads(rig[original.SESSION])
        saved['constraints'].append({'bone': 'hand.L', 'name': foreign.name, 'type': foreign.type, 'mute': False})
        rig[original.SESSION] = json.dumps(saved)
        before = protected(rig, self.mesh)
        with self.assertRaisesRegex(ValueError, 'ownership no longer matches'):
            runtime._resolve_rig(self.mesh, 'L')
        self.assertEqual(protected(rig, self.mesh), before)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ForearmOriginalTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('FOREARM_ORIGINAL_MODE_PASS', result.testsRun, flush=True)
