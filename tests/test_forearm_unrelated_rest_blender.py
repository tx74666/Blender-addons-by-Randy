"""Scoped Original forearm proofs retain unrelated authored Rest edits.

Disposable native suite. Run through tools/run_trusted_blender_validation.py
with a fresh isolated configuration; never open or save the artist scene.
"""
import copy
import math
import sys
import unittest
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_mode as original,
    control_pose_assets as poses, forearm_original_inventory as inventory,
    forearm_twist as runtime, limb_ik, limb_ik_fk, unity_forearm)
import test_body_rest_resync_blender as preservation
import test_forearm_original_mode_blender as original_fixtures
import test_forearm_twist_blender as fixtures


def mirrored_sleeve(rig, source):
    """Two real, disjoint sleeve surfaces with mirrored authored inputs."""
    count = len(source.data.vertices)
    vertices = [vertex.co.copy() for vertex in source.data.vertices]
    mirror = lambda point: (-point[0], point[1], point[2])
    faces = [tuple(face.vertices) for face in source.data.polygons]
    data = bpy.data.meshes.new('Paired Original sleeves')
    data.from_pydata(vertices + [mirror(point) for point in vertices], [],
                     faces + [tuple(count + index for index in reversed(face)) for face in faces])
    obj = bpy.data.objects.new('Paired Original sleeves', data)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = source.matrix_world.copy()
    for group in source.vertex_groups:
        for name in {group.name, group.name[:-2] + '.R' if group.name.endswith('.L') else group.name}:
            if name not in obj.vertex_groups:
                obj.vertex_groups.new(name=name)
    for index, vertex in enumerate(source.data.vertices):
        for item in vertex.groups:
            name = source.vertex_groups[item.group].name
            obj.vertex_groups[name].add([index], item.weight, 'REPLACE')
            other = name[:-2] + '.R' if name.endswith('.L') else name
            obj.vertex_groups[other].add([count + index], item.weight, 'REPLACE')
    uv = data.uv_layers.new(name='Artist UV')
    for index, item in enumerate(uv.data):
        item.uv = ((index % 5) / 5, (index % 7) / 7)
    for source_key in source.data.shape_keys.key_blocks:
        key = obj.shape_key_add(name=source_key.name)
        key.value, key.mute = source_key.value, source_key.mute
        for index, point in enumerate(source_key.data):
            key.data[index].co = point.co
            key.data[count + index].co = mirror(point.co)
    obj.modifiers.new('Artist skin', 'ARMATURE').object = rig
    old_data = source.data
    bpy.data.objects.remove(source, do_unlink=True)
    if old_data.users == 0:
        bpy.data.meshes.remove(old_data)
    fixtures.activate_mesh(obj)
    return obj


class ForearmUnrelatedRestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        # Reuse the accepted complete Direct rig and authored sleeve fixture,
        # without inheriting/rerunning the fixture class's existing tests.
        original_fixtures.ForearmOriginalTests.setUp(self)
        fixtures.activate_mesh(self.rig)
        bpy.ops.object.mode_set(mode='EDIT')
        hand = self.rig.data.edit_bones['hand.L']
        finger = self.rig.data.edit_bones.new('f_index.01.L')
        finger.head, finger.tail = hand.tail, hand.tail + Vector((.045, -.01, 0))
        finger.parent, finger.use_connect = hand, True
        bpy.ops.object.mode_set(mode='OBJECT')
        fixtures.activate_mesh(self.mesh)

    def tearDown(self):
        if runtime._SESSION is not None:
            runtime.finish_test(bpy.context, confirm=False)

    def ready(self, *, paired=False, weighted_finger=False):
        if paired:
            self.mesh = mirrored_sleeve(self.rig, self.mesh)
        if weighted_finger:
            group = self.mesh.vertex_groups.new(name='f_index.01.L')
            group.add(list(range(24, 72)), .12, 'REPLACE')
        runtime.start_test(bpy.context, self.mesh, 'L', symmetry=paired,
                           initial_angle=math.pi / 4)
        runtime.set_ratio(bpy.context, 3, .34)
        runtime.finish_test(bpy.context, confirm=True)
        fixtures.activate_mesh(self.rig)
        original.enter(bpy.context, self.rig)
        fixtures.activate_mesh(self.mesh)
        runtime.update_runtime(bpy.context.scene)
        self.depsgraph = bpy.context.evaluated_depsgraph_get()
        self.assert_active()

    def assert_active(self):
        self.assertNotIn(self.mesh.name, runtime._ERRORS)
        records = runtime._records(self.mesh)
        self.assertTrue(records)
        for record in records.values():
            self.assertTrue(record.get('enabled', True))
            key = self.mesh.data.shape_keys.key_blocks[record['key']]
            self.assertFalse(key.mute)
            self.assertEqual(key.value, 1.)

    def edit(self, callback):
        fixtures.activate_mesh(self.rig)
        bpy.ops.object.mode_set(mode='EDIT')
        callback(self.rig.data.edit_bones)
        bpy.ops.object.mode_set(mode='OBJECT')
        fixtures.activate_mesh(self.mesh)

    def edit_leg(self):
        def knee(bones):
            point = bones['thigh.L'].tail + Vector((0, -.006, .015))
            bones['thigh.L'].tail = point
            bones['shin.L'].head = point
        self.edit(knee)

    def snapshot(self):
        """Exact authored inputs; only managed output coordinates may refresh."""
        state = preservation.protected(self.rig)
        for obj in preservation.bound_meshes(self.rig):
            records = runtime._records(obj) if runtime.RECORD_KEY in obj else {}
            managed = {record['key'] for record in records.values()}
            mesh = state['meshes'][obj.name]
            mesh['keys'] = tuple(entry for entry in mesh['keys'] if entry[0] not in managed)
            mesh['managed_ids'] = tuple((name, obj.data.shape_keys.key_blocks[name].as_pointer(),
                                         obj.data.shape_keys.key_blocks[name].relative_key.name,
                                         obj.data.shape_keys.key_blocks[name].vertex_group)
                                        for name in sorted(managed))
        state['rig_matrix'] = tuple(tuple(row) for row in self.rig.matrix_world)
        state['actions'] = tuple((action.name, action.as_pointer(),
            tuple((curve.data_path, curve.array_index, bool(curve.mute),
                   tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                          point.interpolation) for point in curve.keyframe_points))
                  for curve in limb_ik._fcurves_for_action(action)))
            for action in bpy.data.actions)
        return state

    def assert_global_still_rejects(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Native Rest changed'):
            original._validate_session_rest(self.rig)
        with self.assertRaisesRegex(ValueError, 'Native Rest changed'):
            inventory.validate(self.rig)
        self.assertEqual(self.snapshot(), before)

    def assert_local_refuses(self, *, pattern='(?i)forearm'):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, pattern):
            runtime._resolve_rig(self.mesh, 'L')
        self.assertEqual(self.snapshot(), before)
        # Model the real handler's already supplied graph. Do not first force
        # evaluation of an intentionally damaged influence driver in the test.
        runtime.update_runtime(bpy.context.scene, self.depsgraph)
        self.assertRegex(runtime._ERRORS.get(self.mesh.name, ''), pattern)
        self.assertEqual(self.snapshot(), before)
        for record in runtime._records(self.mesh).values():
            self.assertTrue(self.mesh.data.shape_keys.key_blocks[record['key']].mute)

    def test_leg_geometry_allows_runtime_preview_cancel_and_readonly_export(self):
        self.ready()
        self.edit_leg()
        self.assert_global_still_rejects()
        before = self.snapshot()
        old_profile = copy.deepcopy(runtime._records(self.mesh))
        runtime.update_runtime(bpy.context.scene)
        self.assert_active()
        _arm, route = runtime._resolve_rig(self.mesh, 'L')
        self.assertTrue(route['original_source'])
        self.assertEqual(route['target'].name, 'hand.L')
        runtime.start_test(bpy.context, self.mesh, 'L', recapture=True,
                           initial_angle=math.pi / 3)
        self.assertEqual(runtime._SESSION['target'], 'hand.L')
        self.assertAlmostEqual(runtime.current_twist_angle(bpy.context), math.pi / 3, delta=1e-4)
        runtime.finish_test(bpy.context, confirm=False)
        self.assert_active()
        self.assertEqual(runtime._records(self.mesh), old_profile)
        self.assertEqual(self.snapshot(), before)
        export_before = self.snapshot()
        payload = unity_forearm.capture(self.mesh)
        self.assertTrue(payload['sources'])
        self.assertEqual([side['name'] for side in payload['sides']], ['L'])
        self.assertEqual(self.snapshot(), export_before)
        self.assert_global_still_rejects()

    def test_unweighted_finger_rest_is_unrelated_to_forearm_support(self):
        self.ready()
        self.edit(lambda bones: setattr(bones['f_index.01.L'], 'roll',
                                         bones['f_index.01.L'].roll + .21))
        self.assert_global_still_rejects()
        before = self.snapshot()
        inventory.validate_forearm(self.rig, sides={'L'})
        runtime._resolve_rig(self.mesh, 'L')
        runtime.update_runtime(bpy.context.scene)
        self.assert_active()
        self.assertEqual(self.snapshot(), before)

    def test_arm_chain_and_native_ancestor_geometry_remain_protected(self):
        self.ready()
        for name in ('upper_arm.L', 'forearm.L', 'hand.L', 'Hips'):
            with self.subTest(bone=name):
                old_roll = self.rig.data.bones[name].matrix_local.copy()
                self.edit(lambda bones: setattr(bones[name], 'roll', bones[name].roll + .17))
                self.assert_local_refuses()
                self.edit(lambda bones: setattr(bones[name], 'matrix', old_roll))
                runtime.update_runtime(bpy.context.scene)
                self.assert_active()

    def test_positive_weighted_finger_is_a_required_local_dependency(self):
        self.ready(weighted_finger=True)
        self.edit(lambda bones: setattr(bones['f_index.01.L'], 'roll',
                                         bones['f_index.01.L'].roll + .21))
        # A bare arm-chain query cannot know mesh weights; the mesh resolver must.
        inventory.validate_forearm(self.rig, sides={'L'})
        self.assert_local_refuses()

    def test_paired_runtime_protects_right_arm_even_from_left_resolver(self):
        self.ready(paired=True)
        self.assertEqual(set(runtime._records(self.mesh)), {'L', 'R'})
        self.edit_leg()
        runtime.update_runtime(bpy.context.scene)
        self.assert_active()
        self.edit(lambda bones: setattr(bones['forearm.R'], 'roll',
                                         bones['forearm.R'].roll + .19))
        before = self.snapshot()
        inventory.validate_forearm(self.rig, sides={'L'})
        self.assertEqual(self.snapshot(), before)
        self.assert_local_refuses()

    def test_unrelated_rest_does_not_relax_global_relations_or_inventory(self):
        self.ready()
        self.edit_leg()
        bone = self.rig.data.bones['shin.L']
        old_scale = bone.inherit_scale
        bone.inherit_scale = 'NONE' if old_scale != 'NONE' else 'FULL'
        self.assert_local_refuses(pattern='(?i)(structure|inherit_scale)')
        bone.inherit_scale = old_scale
        runtime.update_runtime(bpy.context.scene)
        self.assert_active()
        bone = self.rig.data.bones['toe.R']
        name = bone.name
        bone.name = 'Artist renamed toe.R'
        self.assert_local_refuses(pattern='(?i)(inventory|ownership|missing|renamed)')
        bone.name = name
        runtime.update_runtime(bpy.context.scene)
        self.assert_active()

    def test_unrelated_rest_keeps_influence_driver_and_original_mute_checks(self):
        self.ready()
        self.edit_leg()
        relations = limb_ik._owned_constraint_records(self.rig)
        helper_pb, helper, helper_record = next(entry for entry in relations
            if entry[2].get('kind') == 'ARM' and entry[2].get('side') == 'L'
            and entry[0].name not in entry[2]['chain']
            and not limb_ik_fk.is_switch_constraint(self.rig, *entry))
        self.assertFalse(limb_ik_fk.is_switch_constraint(self.rig, helper_pb, helper, helper_record))
        self.assertEqual(helper.influence, 1.)
        self.assertFalse(any(curve.data_path == helper.path_from_id('influence')
                             for curve in self.rig.animation_data.drivers))
        # A helper has a fixed full influence; a source's legitimate switch
        # driver output is ephemeral and is not a numeric tamper contract.
        helper.influence = .17
        self.assert_local_refuses(pattern='(?i)constraint.*edited')
        helper.influence = 1.
        pb, con, record = next(entry for entry in relations if entry[0].name == 'forearm.L')
        self.assertTrue(limb_ik_fk.is_switch_constraint(self.rig, pb, con, record))
        driver = next(curve for curve in self.rig.animation_data.drivers
                      if curve.data_path == con.path_from_id('influence'))
        expression = driver.driver.expression
        driver.driver.expression = 'ik_fk * 0.5'
        self.assert_local_refuses(pattern='(?i)driver.*edited')
        driver.driver.expression = expression
        con.mute = False
        self.assert_local_refuses(pattern='(?i)Original constraint.*changed')
        con.mute = True
        runtime.update_runtime(bpy.context.scene)
        self.assert_active()


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ForearmUnrelatedRestTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('FOREARM_UNRELATED_REST_PASS', result.testsRun, flush=True)
