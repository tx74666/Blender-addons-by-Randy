"""Intentional Direct Rest changes retain artist geometry and adapt Controls.

Run only in an isolated Blender --background --factory-startup process.
"""
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_mode as original, body_rest_resync as resync,
    body_setup, body_setup_removal, body_calibration, control_pose_assets as poses,
    limb_ik, limb_ik_fk)
import test_body_calibration_blender as setup
from test_control_pose_weights_blender import matrices


def token(value):
    if isinstance(value, bpy.types.ID):
        return (type(value).__name__, value.as_pointer())
    if hasattr(value, 'items'):
        return tuple((key, token(item)) for key, item in sorted(value.items()))
    if hasattr(value, 'to_list'):
        return tuple(token(item) for item in value.to_list())
    if isinstance(value, (list, tuple)):
        return tuple(token(item) for item in value)
    return value


def properties(owner):
    return tuple((key, token(owner[key])) for key in sorted(owner.keys()))


def mesh_raw(obj):
    data, keys = obj.data, obj.data.shape_keys
    return {
        'matrix': tuple(tuple(row) for row in obj.matrix_world),
        'properties': properties(obj), 'data_properties': properties(data),
        'vertices': tuple(tuple(v.co) for v in data.vertices),
        'edges': tuple(tuple(edge.vertices) for edge in data.edges),
        'faces': tuple(tuple(face.vertices) for face in data.polygons),
        'uv': tuple((layer.name, tuple(tuple(item.uv) for item in layer.data)) for layer in data.uv_layers),
        'groups': tuple((group.name, group.index, bool(group.lock_weight)) for group in obj.vertex_groups),
        'weights': tuple(tuple((item.group, float(item.weight)) for item in v.groups) for v in data.vertices),
        'keys': tuple((key.name, key.value, bool(key.mute), key.relative_key.name, key.vertex_group,
                       tuple(tuple(point.co) for point in key.data))
                      for key in keys.key_blocks) if keys else (),
        'key_properties': properties(keys) if keys else (),
        'binding': tuple((mod.name, mod.type, getattr(mod, 'object', None).as_pointer()
                          if getattr(mod, 'object', None) else None, mod.show_viewport, mod.show_render)
                         for mod in obj.modifiers),
    }


def bound_meshes(rig):
    return [obj for obj in bpy.data.objects if obj.type == 'MESH'
            and any(mod.type == 'ARMATURE' and mod.object == rig for mod in obj.modifiers)]


def world_surfaces(rig):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result = {}
    for obj in bound_meshes(rig):
        evaluated = obj.evaluated_get(depsgraph)
        data = evaluated.to_mesh()
        try:
            result[obj.name] = [evaluated.matrix_world @ vertex.co for vertex in data.vertices]
        finally:
            evaluated.to_mesh_clear()
    return result


def protected(rig):
    return {
        'object_properties': properties(rig), 'data_properties': properties(rig.data),
        'bone_properties': tuple((bone.name, properties(bone)) for bone in rig.data.bones),
        'pose_properties': tuple((pb.name, properties(pb)) for pb in rig.pose.bones),
        'rest': poses.native_rest(rig), 'channels': original._channels(rig),
        'all_bone_geometry': tuple((bone.name, tuple(bone.head_local), tuple(bone.tail_local),
                                    tuple(tuple(row) for row in bone.matrix_local),
                                    bone.parent.name if bone.parent else None, bool(bone.use_connect),
                                    bool(bone.use_deform)) for bone in rig.data.bones),
        'constraints': tuple((pb.name, con.name, con.type, bool(con.mute), float(con.influence),
                              getattr(con, 'subtarget', None), getattr(con, 'pole_subtarget', None),
                              getattr(con, 'pole_angle', None), getattr(con, 'owner_space', None),
                              getattr(con, 'target_space', None), getattr(con, 'mix_mode', None))
                             for pb in rig.pose.bones for con in pb.constraints),
        'drivers': tuple((curve.data_path, curve.array_index, bool(curve.mute), curve.driver.type,
                          curve.driver.expression,
                          tuple((variable.name, variable.type,
                                 tuple((target.id.as_pointer() if target.id else None,
                                        target.data_path, target.bone_target) for target in variable.targets))
                                for variable in curve.driver.variables))
                         for curve in rig.animation_data.drivers) if rig.animation_data else (),
        'meshes': {obj.name: mesh_raw(obj) for obj in bound_meshes(rig)},
    }


def add_surface(rig):
    """UV-bearing skin with several source weights and independent Shape Keys."""
    vertices, faces, assignments = [], [], []
    for side in ('L', 'R'):
        chain = ['upper_arm.' + side, 'forearm.' + side, 'hand.' + side]
        loops = []
        for row, name in enumerate(chain):
            bone = rig.data.bones[name]
            center = bone.head_local.lerp(bone.tail_local, .5)
            radial = bone.matrix_local.to_3x3() @ Vector((.025, 0, 0))
            other = bone.matrix_local.to_3x3() @ Vector((0, 0, .025))
            loop = []
            for spoke in range(4):
                angle = spoke * math.tau / 4
                index = len(vertices)
                loop.append(index)
                vertices.append(center + math.cos(angle) * radial + math.sin(angle) * other)
                assignments.append((name, index, .8 if row else 1.))
                if row:
                    assignments.append((chain[row - 1], index, .2))
            loops.append(loop)
        for row in range(2):
            for spoke in range(4):
                nxt = (spoke + 1) % 4
                faces.append((loops[row][spoke], loops[row][nxt], loops[row + 1][nxt], loops[row + 1][spoke]))
    data = bpy.data.meshes.new('Rest resync artist surface')
    data.from_pydata(vertices, [], faces)
    uv = data.uv_layers.new(name='Artist UV')
    for index, value in enumerate(uv.data):
        value.uv = ((index % 7) / 7, (index % 11) / 11)
    obj = bpy.data.objects.new('Rest resync artist surface', data)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = rig.matrix_world
    for name, index, weight in assignments:
        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        group.add([index], weight, 'REPLACE')
    obj.modifiers.new('Artist Armature', 'ARMATURE').object = rig
    obj.shape_key_add(name='Basis')
    smile = obj.shape_key_add(name='Smile_L')
    smile.data[0].co.z += .008
    smile.value = .35
    blink = obj.shape_key_add(name='Blink_R')
    blink.data[-1].co.y -= .006
    blink.value = .2
    return obj


def direct4(rig):
    """Exact schema-4 display contract with already-upgraded IK/FK features."""
    inventory = limb_ik._validate_inventory(rig)
    remove = []
    for entry in inventory['rigs'].values():
        display = entry['display']
        rig.pose.bones[entry['pole'].name].custom_shape_transform = None
        for pb, con, record in entry['entries']:
            if record['role'] == 'POLE_DISPLAY_TRACK':
                pb.constraints.remove(con)
        remove.append(display.name)
    bpy.ops.object.mode_set(mode='EDIT')
    for name in remove:
        rig.data.edit_bones.remove(rig.data.edit_bones[name])
    bpy.ops.object.mode_set(mode='POSE')
    rig.data[limb_ik.SCHEMA_KEY] = limb_ik.LEGACY_DIRECT_PREROLL_SCHEMA
    bpy.context.view_layer.update()
    return limb_ik._validate_inventory(rig)


class RestResyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def make(self, *, schema4=False, unconnected_forearm=False, corrective=False):
        rig, mesh = setup.fixture()
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        bpy.ops.object.mode_set(mode='EDIT')
        for side in ('L', 'R'):
            # Artist shoulder frames can move independently of shoulder tails,
            # while the elbow and wrist retain their contiguous source joints.
            rig.data.edit_bones['upper_arm.' + side].use_connect = False
            if unconnected_forearm:
                rig.data.edit_bones['forearm.' + side].use_connect = False
        bpy.ops.object.mode_set(mode='POSE')
        setup.prepare(rig)
        body_setup.generate(bpy.context, rig)
        inventory = direct4(rig) if schema4 else limb_ik._validate_inventory(rig)
        self.assertEqual(inventory['schema'], limb_ik.LEGACY_DIRECT_PREROLL_SCHEMA
                         if schema4 else limb_ik.DIRECT_PREROLL_SCHEMA)
        rig.location = (.13, -.04, .2)
        rig.rotation_euler = (.05, .1, -.15)
        rig.scale = (.83,) * 3
        bpy.context.view_layer.update()
        surface = add_surface(rig)
        if corrective:
            from character_designer import forearm_twist as runtime
            import test_forearm_original_mode_blender as forearm_original
            sleeve, _outside = forearm_original.sleeve(rig)
            runtime.start_test(bpy.context, sleeve, 'L', symmetry=False, recapture=True)
            runtime.finish_test(bpy.context, confirm=True)
            # Keep the visible artist surface under ordinary skinning. The
            # existing managed output and its metadata still migrate on flush,
            # and must be restored byte-for-byte after a failed transaction.
            runtime.toggle_paired_calibration(bpy.context, sleeve)
            self.assertTrue(all(not record.get('enabled', True)
                                for record in runtime._records(sleeve).values()))
        original.enter(bpy.context, rig)
        return rig, mesh, surface, inventory

    def edit(self, rig):
        bpy.ops.object.mode_set(mode='EDIT')
        for side, sign in (('L', 1), ('R', -1)):
            upper = rig.data.edit_bones['upper_arm.' + side]
            upper.head += Vector((sign * .007, 0, -.003))
        rig.data.edit_bones['upper_arm.R'].roll += math.pi
        bpy.ops.object.mode_set(mode='POSE')
        bpy.context.view_layer.update()

    def assertPose(self, rig, expected):
        actual = matrices(rig)
        self.assertLessEqual(max(poses._difference(matrix, actual[name])
                                 for name, matrix in expected.items()), 4e-4)

    def assertSurfaces(self, rig, expected):
        actual = world_surfaces(rig)
        self.assertEqual(set(actual), set(expected))
        for name, before in expected.items():
            self.assertEqual(len(actual[name]), len(before), name)
            maximum = max(((a - b).length for a, b in zip(actual[name], before)), default=0.)
            self.assertLessEqual(maximum, body_setup_removal.SURFACE_TOLERANCE * .83 + 2e-7, name)

    def success(self, *, schema4=False):
        rig, _mesh, _surface, inventory = self.make(schema4=schema4)
        previous_registry = limb_ik._load_direct_rest_registry(rig)
        historical_baseline = rig.get(poses.BASELINE)
        historical_calibration = rig.get(body_calibration.KEY)
        self.edit(rig)
        accepted, pose = poses.native_rest(rig), matrices(rig)
        meshes = {obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}
        surfaces = world_surfaces(rig)
        self.assertTrue(original.leave(bpy.context, rig))
        self.assertFalse(original.active(rig))
        self.assertEqual(poses.native_rest(rig), accepted)
        self.assertEqual({obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}, meshes)
        self.assertEqual(rig.get(poses.BASELINE), historical_baseline)
        self.assertEqual(rig.get(body_calibration.KEY), historical_calibration)
        self.assertPose(rig, pose)
        self.assertSurfaces(rig, surfaces)
        current = limb_ik._validate_inventory(rig)
        registry = limb_ik._load_direct_rest_registry(rig)
        event = json.loads(rig[resync.PROVENANCE_KEY])['events'][-1]
        self.assertEqual(set(event['changed']), {'upper_arm.L', 'upper_arm.R'})
        self.assertFalse(event['pose_assets_retargeted'])
        self.assertFalse(event['body_calibration_reconfirmed'])
        for key in (('ARM', 'L'), ('ARM', 'R')):
            rig_id = current['rigs'][key]['rig_id']
            entry = registry['limbs'][rig_id]
            self.assertEqual(entry['original'], entry['applied'])
            self.assertEqual(event['prior_direct_entries'][rig_id], previous_registry['limbs'][rig_id])
            self.assertEqual(event['accepted_direct_entries'][rig_id], entry)
            for name, state in entry['original'].items():
                self.assertTrue(limb_ik._rest_state_matches(rig.data.bones[name], state), name)
        for key in (('LEG', 'L'), ('LEG', 'R')):
            rig_id = inventory['rigs'][key]['rig_id']
            self.assertEqual(registry['limbs'][rig_id], previous_registry['limbs'][rig_id])
        # Inspect the removal endpoint without removing extensions or bones.
        removal = body_setup_removal._rest_plan(rig, current)
        for name in ('upper_arm.L', 'forearm.L', 'upper_arm.R', 'forearm.R'):
            self.assertTrue(limb_ik._rest_state_matches(rig.data.bones[name], removal[name]), name)
        history = rig[resync.PROVENANCE_KEY]
        original.enter(bpy.context, rig)
        self.assertEqual(poses.native_rest(rig), accepted)
        self.assertPose(rig, pose)
        self.assertSurfaces(rig, surfaces)
        original.leave(bpy.context, rig)
        self.assertEqual(poses.native_rest(rig), accepted)
        self.assertEqual(rig[resync.PROVENANCE_KEY], history, 'No-edit roundtrip must not add a resync event')
        self.assertEqual({obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}, meshes)
        self.assertPose(rig, pose)
        self.assertSurfaces(rig, surfaces)

    def test_direct5_retains_edited_rest_and_complete_artist_surface(self):
        self.success()

    def test_direct4_with_aligned_features_preserves_same_contract(self):
        self.success(schema4=True)

    def test_post_match_validation_failure_restores_exact_original_state(self):
        rig, _mesh, _surface, _inventory = self.make()
        self.edit(rig)
        before, surfaces = protected(rig), world_surfaces(rig)
        actual = resync.verify
        called = []
        def fail_after_verification(*args):
            called.append(True)
            actual(*args)
            raise ValueError('injected after native Rest resync verification')
        with patch.object(resync, 'verify', side_effect=fail_after_verification):
            with self.assertRaisesRegex(ValueError, 'injected after native Rest resync'):
                original.leave(bpy.context, rig)
        self.assertTrue(called, 'Injection must occur after an actual graph adaptation and match')
        self.assertEqual(protected(rig), before)
        self.assertTrue(original.active(rig))
        self.assertSurfaces(rig, surfaces)
        self.assertTrue(original.leave(bpy.context, rig), 'A rolled-back transaction must remain recoverable')

    def test_final_failure_restores_existing_corrective_record_and_managed_output(self):
        from character_designer import forearm_twist as runtime
        rig, _mesh, _surface, _inventory = self.make(corrective=True)
        self.edit(rig)
        managed = [obj for obj in bound_meshes(rig) if runtime.RECORD_KEY in obj]
        self.assertEqual(len(managed), 1)
        before = protected(rig)
        record_before = managed[0][runtime.RECORD_KEY]
        actual = resync.verify
        reached = []
        def fail_after_corrective_flush(*args):
            actual(*args)
            reached.append(True)
            self.assertNotEqual(managed[0][runtime.RECORD_KEY], record_before,
                                'Corrective checkpoint must be checked after the final runtime phase')
            raise ValueError('injected final resync failure with existing corrective')
        with patch.object(resync, 'verify', side_effect=fail_after_corrective_flush):
            with self.assertRaisesRegex(ValueError, 'injected final resync failure with existing corrective'):
                original.leave(bpy.context, rig)
        self.assertTrue(reached)
        self.assertEqual(protected(rig), before)
        self.assertTrue(original.active(rig))

    def test_owned_driver_edit_is_not_bypassed_by_old_rest_view(self):
        rig, _mesh, _surface, _inventory = self.make()
        self.edit(rig)
        curve = next(curve for curve in rig.animation_data.drivers if 'forearm.L' in curve.data_path)
        curve.driver.expression += ' + 0.0'  # Same output, different ownership contract.
        bpy.context.view_layer.update()
        before = protected(rig)
        with self.assertRaises(ValueError):
            original.leave(bpy.context, rig)
        self.assertEqual(protected(rig), before)
        self.assertTrue(original.active(rig))

    def test_owned_target_identity_edit_is_not_bypassed_by_old_rest_view(self):
        rig, _mesh, _surface, inventory = self.make()
        target_name = inventory['rigs'][('ARM', 'L')]['target'].name
        self.edit(rig)
        # Edit Mode replaces DataBone RNA handles. Tamper the actual current
        # target, not the pre-edit inventory wrapper retained by the fixture.
        target = rig.data.bones[target_name]
        target[limb_ik.ARMATURE_ID_KEY] = 'foreign-owner'
        self.assertEqual(rig.data.bones[target_name][limb_ik.ARMATURE_ID_KEY], 'foreign-owner')
        self.assertFalse(limb_ik._owned(target, rig.data[limb_ik.ARMATURE_ID_KEY]))
        before = protected(rig)
        with self.assertRaises(ValueError):
            original.leave(bpy.context, rig)
        self.assertEqual(protected(rig), before)
        self.assertTrue(original.active(rig))

    def test_authored_transform_driver_dependency_is_preserved_and_refused(self):
        rig, _mesh, _surface, _inventory = self.make()
        self.edit(rig)
        curve = rig.pose.bones['hand.L'].driver_add('location', 0)
        curve.driver.type = 'SCRIPTED'
        curve.driver.expression = '0.0'
        bpy.context.view_layer.update()
        before = protected(rig)
        with self.assertRaises(ValueError):
            original.leave(bpy.context, rig)
        self.assertEqual(protected(rig), before)
        self.assertTrue(original.active(rig))

    def test_parent_and_connection_edits_remain_structural_refusals(self):
        for field in ('parent', 'use_connect'):
            with self.subTest(field=field):
                rig, _mesh, _surface, _inventory = self.make()
                self.edit(rig)
                bpy.ops.object.mode_set(mode='EDIT')
                lower = rig.data.edit_bones['forearm.L']
                if field == 'parent':
                    lower.parent = rig.data.edit_bones['Hips']
                else:
                    lower.use_connect = not lower.use_connect
                bpy.ops.object.mode_set(mode='POSE')
                before = protected(rig)
                with self.assertRaisesRegex(ValueError, r'(structure|inventory|ownership)'):
                    original.leave(bpy.context, rig)
                self.assertEqual(protected(rig), before)
                self.assertTrue(original.active(rig))

    def test_native_rename_refuses_before_any_graph_adaptation(self):
        rig, _mesh, _surface, _inventory = self.make()
        self.edit(rig)
        rig.data.bones['upper_arm.L'].name = 'Artist Upper Arm'
        before = protected(rig)
        with self.assertRaisesRegex(ValueError, 'bone inventory changed'):
            original.leave(bpy.context, rig)
        self.assertEqual(protected(rig), before)
        self.assertTrue(original.active(rig))

    def test_geometrically_disconnected_chain_refuses_even_with_unchanged_flags(self):
        rig, _mesh, _surface, _inventory = self.make(unconnected_forearm=True)
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['upper_arm.L'].tail += Vector((.004, 0, 0))
        bpy.ops.object.mode_set(mode='POSE')
        gap = (rig.data.bones['upper_arm.L'].tail_local - rig.data.bones['forearm.L'].head_local).length
        self.assertGreater(gap, .001)
        before = protected(rig)
        with self.assertRaisesRegex(ValueError, r'(disconnect|contiguous|joint|Rest)'):
            original.leave(bpy.context, rig)
        self.assertEqual(protected(rig), before)
        self.assertTrue(original.active(rig))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RestResyncTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('Body Rest resync tests failed')
