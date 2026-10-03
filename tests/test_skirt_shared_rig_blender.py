"""Shared Dress migration: protected mesh data, pose, ownership and rollback.

Run in an isolated Blender, never in the artist's open scene.  The helper
snapshots are also used by X's saved-Cosha integration verification.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import skirt_rig as service

# Independent bounds backed by unchanged native Spline IK re-evaluation and
# measured small-physics/Cosha cases. Coordinates, channels and rest stay exact.
CONTROL_POSITION_RELATIVE = 3e-6
CAGE_POSITION_RELATIVE = 2e-6
SOLVER_POSITION_RELATIVE = 5e-4
CONTROL_AXIS_LIMIT = 1e-5
SOLVER_AXIS_LIMIT = 1.2e-3


def matrix_values(matrix):
    return tuple(tuple(row) for row in matrix)


def matrix_error(left, right):
    return max(abs(left[i][j] - right[i][j]) for i in range(4) for j in range(4))


def mesh_content(obj):
    """Only data migration must never touch; parent/modifier targets are separate."""
    mesh = obj.data
    keys = mesh.shape_keys
    return {
        'vertices': tuple(tuple(vertex.co) for vertex in mesh.vertices),
        'edges': tuple(tuple(edge.vertices) for edge in mesh.edges),
        'polygons': tuple((tuple(face.vertices), face.material_index, face.use_smooth)
                          for face in mesh.polygons),
        'loops': tuple((loop.vertex_index, loop.edge_index) for loop in mesh.loops),
        'uv': tuple((layer.name, layer.active_render,
                     tuple(tuple(loop.uv) for loop in layer.data)) for layer in mesh.uv_layers),
        'groups': tuple((group.name, group.index, group.lock_weight) for group in obj.vertex_groups),
        # Native restore can change internal entry storage order. The mapping
        # from each original group index to its exact weight is the contract.
        'weights': tuple(tuple(sorted((entry.group, entry.weight) for entry in vertex.groups))
                         for vertex in mesh.vertices),
        'materials': tuple(material.name if material else None for material in mesh.materials),
        'keys': None if keys is None else (keys.use_relative, keys.eval_time,
            keys.reference_key.name,
            tuple((key.name, key.relative_key.name, key.value, key.mute, key.slider_min,
                   key.slider_max, key.vertex_group, key.interpolation,
                   tuple(tuple(point.co) for point in key.data)) for key in keys.key_blocks)),
    }


def meshes_content(scene=None):
    return {obj.name: mesh_content(obj) for obj in (scene or bpy.context.scene).objects
            if obj.type == 'MESH'}


def content_digest(content):
    return hashlib.sha256(repr(content).encode('utf-8')).hexdigest()


def rest_state(rig, names=None):
    names = names or tuple(rig.data.bones.keys())
    return {name: (rig.data.bones[name].parent.name if rig.data.bones[name].parent else None,
                   rig.data.bones[name].use_connect, rig.data.bones[name].use_deform,
                   matrix_values(rig.data.bones[name].matrix_local),
                   rig.data.bones[name].length) for name in names}


def pose_channels(rig, names=None):
    names = names or tuple(rig.pose.bones.keys())
    return {name: (rig.pose.bones[name].rotation_mode,
                   tuple(rig.pose.bones[name].location), tuple(rig.pose.bones[name].rotation_euler),
                   tuple(rig.pose.bones[name].rotation_quaternion),
                   tuple(rig.pose.bones[name].rotation_axis_angle), tuple(rig.pose.bones[name].scale))
            for name in names}


def world_pose(rig, names=None, rest=False):
    names = names or tuple(rig.data.bones.keys())
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {name: rig.matrix_world @ (rig.data.bones[name].matrix_local if rest
                                    else evaluated.pose.bones[name].matrix)
            for name in names}


def world_vertices(source):
    evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return tuple(evaluated.matrix_world @ vertex.co for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()


def world_cage(source, record):
    return {name: world_vertices(bpy.data.objects[name]) for name in record['cage']}


def geometry_error(before, after):
    if len(before) != len(after):
        raise AssertionError(f'Evaluated vertex count changed: {len(before)} -> {len(after)}')
    return max(((left - right).length for left, right in zip(before, after)), default=0.0)


def geometry_extent(vertices):
    return max((max(point[axis] for point in vertices) - min(point[axis] for point in vertices)
                for axis in range(3)), default=1e-4)


def world_frames(rig, names=None, rest=False):
    """Physical bone heads/tails and axes; object scale belongs to bone length."""
    names = names or tuple(rig.data.bones.keys())
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = {}
    for name in names:
        bone = rig.data.bones[name] if rest else evaluated.pose.bones[name]
        head = bone.head_local if rest else bone.head
        tail = bone.tail_local if rest else bone.tail
        matrix = rig.matrix_world @ (bone.matrix_local if rest else bone.matrix)
        result[name] = (rig.matrix_world @ head, rig.matrix_world @ tail,
                        tuple(matrix.to_3x3().col[axis].normalized() for axis in range(3)))
    return result


def world_rest_frames(main, rig, names=None):
    """Rest frame includes the attached rig's original anchor inheritance."""
    position = main.data.pose_position
    try:
        main.data.pose_position = 'REST'
        bpy.context.view_layer.update()
        return world_frames(rig, names, rest=True)
    finally:
        main.data.pose_position = position
        bpy.context.view_layer.update()


def frame_errors(actual, before):
    return {name: {'head': (actual[name][0] - state[0]).length,
                   'tail': (actual[name][1] - state[1]).length,
                   'axis': max(math.atan2(left.cross(right).length, left.dot(right))
                               for left, right in zip(actual[name][2], state[2]))}
            for name, state in before.items()}


def export_skeleton_state(main, accessory=None):
    """Use the actual export cleaner/merger on disposable armature copies only."""
    from character_designer import unity_export_worker as worker
    saved_context = service._context_state(bpy.context)
    old_position = main.data.pose_position
    copies = []
    try:
        main.data.pose_position = 'REST'
        bpy.context.view_layer.update()
        clone = main.copy()
        clone.data = main.data.copy()
        clone.name = '__Disposable Character Export'
        bpy.context.collection.objects.link(clone)
        copies.append(clone)
        if accessory is not None:
            world = accessory.matrix_world.copy()
            extra = accessory.copy()
            extra.data = accessory.data.copy()
            extra.name = '__Disposable Dress Export'
            bpy.context.collection.objects.link(extra)
            copies.append(extra)
            extra.parent = clone
            bpy.context.view_layer.update()
            extra.matrix_world = world
        service._activate(bpy.context, clone, 'OBJECT')
        for rig in copies:
            worker._clean_skeleton(bpy.context, rig, copies)
        worker._merge_accessory_skeletons(bpy.context, clone, copies)
        return rest_state(clone)
    finally:
        current = bpy.context.view_layer.objects.active
        if current and current.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in reversed(copies):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                bpy.data.armatures.remove(data)
        main.data.pose_position = old_position
        service._restore_context(bpy.context, saved_context)
        bpy.context.view_layer.update()


def rig_fixture():
    data = bpy.data.armatures.new('Character Skeleton')
    rig = bpy.data.objects.new('Character', data)
    bpy.context.collection.objects.link(rig)
    service._activate(bpy.context, rig, 'EDIT')
    for name, head, tail, parent in (
            ('Hips', (0, 0, 1), (0, 0, 1.2), None),
            ('Body', (0, 0, 1.2), (0, 0, 1.7), 'Hips'),
            ('Hair', (0, 0, 1.7), (0.1, 0, 1.9), 'Body')):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    for collection_name, bone_names in (('Body', ('Hips', 'Body')), ('Hair', ('Hair',))):
        collection = data.collections.new(collection_name)
        for name in bone_names:
            collection.assign(data.bones[name])
    data.collections.active = data.collections_all['Body']
    rig.location = (0.2, -0.4, 0.05)
    rig.rotation_euler = (0.14, -0.09, 0.31)
    return rig


def fixture(posed=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rig = rig_fixture()
    sides, rows = 16, 6
    vertices = []
    for row in range(rows):
        fraction = row / (rows - 1)
        for col in range(sides):
            angle = math.tau * col / sides
            vertices.append(((0.35 + fraction * 0.3) * math.cos(angle),
                             (0.3 + fraction * 0.2) * math.sin(angle), 1.2 - fraction * 0.8))
    faces = [(row * sides + col, row * sides + (col + 1) % sides,
              (row + 1) * sides + (col + 1) % sides, (row + 1) * sides + col)
             for row in range(rows - 1) for col in range(sides)]
    mesh = bpy.data.meshes.new('Dress Mesh')
    mesh.from_pydata(vertices, [], faces)
    source = bpy.data.objects.new('Dress', mesh)
    bpy.context.collection.objects.link(source)
    source.location = (0.2, -0.1, 0.15)
    uv = mesh.uv_layers.new(name='Artist UV')
    for index, loop in enumerate(uv.data):
        loop.uv = ((index % 11) / 11, (index % 17) / 17)
    source.vertex_groups.new(name='Artist pin').add([1, 3], 0.42, 'REPLACE')
    basis = source.shape_key_add(name='Basis')
    left = source.shape_key_add(name='Blink_L')
    right = source.shape_key_add(name='Smile_R')
    left.data[1].co.z += 0.021
    right.data[3].co.y -= 0.016
    left.value, right.value = 0.35, 0.22
    left.relative_key = basis
    right.relative_key = left
    record = service.build_skirt(bpy.context, source, armature=rig, shared=False)
    skirt = source[service.RIG_KEY]
    if posed:
        rig.pose.bones['Hips'].rotation_mode = 'XYZ'
        rig.pose.bones['Hips'].rotation_euler = (0.07, 0.04, -0.12)
        control = skirt.pose.bones[record['controls']['hem']]
        control.location.x = 0.025
        control.rotation_euler.y = 0.06
    bpy.context.view_layer.update()
    return source, skirt, rig, record


class SharedRigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def assert_world_pose(self, actual, before, tolerance=5e-5):
        errors = {name: matrix_error(before[name], actual[name]) for name in before}
        self.assertLessEqual(max(errors.values(), default=0), tolerance, errors)

    def assert_skirt_frames(self, rig, before, record, extent):
        controls = service._bone_collection_layout(record)[0]
        errors = frame_errors(world_frames(rig, before), before)
        for name, error in errors.items():
            position_limit = (CONTROL_POSITION_RELATIVE if name in controls else SOLVER_POSITION_RELATIVE) * extent
            angle_limit = CONTROL_AXIS_LIMIT if name in controls else SOLVER_AXIS_LIMIT
            self.assertLessEqual(max(error['head'], error['tail']), position_limit,
                                 (name, error, position_limit))
            self.assertLessEqual(error['axis'], angle_limit, (name, error, angle_limit))

    def test_migration_preserves_mesh_keys_weights_pose_and_rewires_native_graph(self):
        source, skirt, main, record = fixture()
        old_name = skirt.name
        names = tuple(skirt.data.bones.keys())
        before_content = meshes_content()
        before_rest, before_channels = rest_state(main), pose_channels(main)
        before_pose, before_vertices = world_frames(skirt), world_vertices(source)
        extent = geometry_extent(before_vertices)
        before_cage = world_cage(source, record)
        count = len(bpy.data.objects)
        updated = service.unify_skirt(bpy.context, source, main)
        self.assertTrue(service.is_shared(updated))
        self.assertIs(source[service.RIG_KEY], main)
        self.assertNotIn(old_name, bpy.data.objects)
        self.assertEqual(len(main.data.bones), len(names) + len(before_rest))
        self.assertEqual(len(bpy.data.objects), count - 1)
        self.assertEqual(meshes_content(), before_content)
        self.assertEqual(rest_state(main, before_rest), before_rest)
        self.assertEqual(pose_channels(main, before_channels), before_channels)
        self.assert_skirt_frames(main, before_pose, record, extent)
        self.assertLessEqual(geometry_error(before_vertices, world_vertices(source)), SOLVER_POSITION_RELATIVE * extent)
        for name, before in before_cage.items():
            self.assertLessEqual(geometry_error(before, world_vertices(bpy.data.objects[name])), CAGE_POSITION_RELATIVE * extent)
        self.assertIs(source.modifiers[updated['modifier']].object, main)
        owned_names = set(updated['shared']['names'])
        self.assertEqual(owned_names, set(names))
        collection = main.data.collections_all[updated['shared']['collection']]
        self.assertEqual(set(collection.bones.keys()), owned_names)
        for name in names:
            self.assertEqual(main.data.bones[name].get(service.OWNER_KEY), updated['owner'])
            self.assertIs(main.data.bones[name].get(service.SOURCE_KEY), source)
            self.assertEqual(set(main.data.bones[name].collections), {collection}, name)
        for chain in updated['chains']:
            for name in chain['def']:
                for constraint in main.pose.bones[name].constraints:
                    if constraint.type in {'COPY_TRANSFORMS', 'COPY_ROTATION'}:
                        self.assertIs(constraint.target, main)
                        self.assertIn(constraint.subtarget, owned_names)
        waist = main.pose.bones[updated['controls']['waist']]
        prop_path = waist.path_from_id() + '["physics_influence"]'
        self.assertIn('physics_influence', waist)
        driver_targets = [target for curve in main.animation_data.drivers
                          for variable in curve.driver.variables for target in variable.targets]
        self.assertTrue(driver_targets)
        for target in driver_targets:
            self.assertIs(target.id, main)
            self.assertEqual(target.data_path, prop_path)
        for name in updated['cage']:
            for modifier in bpy.data.objects[name].modifiers:
                if modifier.type == 'HOOK':
                    self.assertIs(modifier.object, main)
                    self.assertIn(modifier.subtarget, owned_names)

    def test_main_anchor_motion_and_character_animation_remain_valid(self):
        source, skirt, main, record = fixture()
        main.pose.bones['Hips'].keyframe_insert('rotation_euler', frame=1)
        main.pose.bones['Hips'].rotation_euler.z += 0.12
        main.pose.bones['Hips'].keyframe_insert('rotation_euler', frame=9)
        samples = {}
        for frame in (1, 4, 9):
            bpy.context.scene.frame_set(frame)
            samples[frame] = (world_vertices(source), world_frames(skirt))
        bpy.context.scene.frame_set(4, subframe=0.375)
        subframe_world = world_vertices(source)
        subframe_pose = world_frames(skirt)
        extent = geometry_extent(subframe_world)
        action = main.animation_data.action
        action_name = action.name
        updated = service.unify_skirt(bpy.context, source, main)
        self.assertEqual(bpy.context.scene.frame_current, 4)
        self.assertEqual(bpy.context.scene.frame_subframe, 0.375)
        self.assertLessEqual(geometry_error(subframe_world, world_vertices(source)), SOLVER_POSITION_RELATIVE * extent)
        self.assert_skirt_frames(main, subframe_pose, record, extent)
        self.assertIs(main.animation_data.action, action)
        self.assertEqual(action.name, action_name)
        for frame, (vertices, pose) in samples.items():
            bpy.context.scene.frame_set(frame)
            self.assertLessEqual(geometry_error(vertices, world_vertices(source)), SOLVER_POSITION_RELATIVE * extent)
            self.assert_skirt_frames(main, pose, record, extent)
        before_rest = rest_state(main, ('Hips', 'Body', 'Hair'))
        service.remove_skirt(bpy.context, source)
        self.assertIs(main.animation_data.action, action)
        self.assertEqual(rest_state(main), before_rest)

    def test_late_validation_failure_restores_all_refs_pose_mesh_and_records(self):
        from character_designer import skirt_shared_rig as shared
        source, skirt, main, record = fixture()
        before_record = source[service.RECORD_KEY]
        before_objects = tuple(sorted(bpy.data.objects.keys()))
        before_content, before_rest = meshes_content(), rest_state(main)
        before_pose, before_vertices = world_frames(skirt), world_vertices(source)
        extent = geometry_extent(before_vertices)
        with patch.object(shared, '_validate_result', side_effect=RuntimeError('Injected validation failure')):
            with self.assertRaises(service.SkirtRigError):
                service.unify_skirt(bpy.context, source, main)
        self.assertIs(source[service.RIG_KEY], skirt)
        self.assertEqual(source[service.RECORD_KEY], before_record)
        self.assertEqual(tuple(sorted(bpy.data.objects.keys())), before_objects)
        self.assertEqual(meshes_content(), before_content)
        self.assertEqual(rest_state(main), before_rest)
        self.assert_skirt_frames(skirt, before_pose, record, extent)
        self.assertLessEqual(geometry_error(before_vertices, world_vertices(source)), SOLVER_POSITION_RELATIVE * extent)
        self.assertIs(source.modifiers[record['modifier']].object, skirt)
        self.assertFalse(service.shared_sources(main))
        service.unify_skirt(bpy.context, source, main)

    def test_preflight_refuses_active_original_independent_animation_and_name_collision(self):
        source, skirt, main, record = fixture()
        baseline = (source[service.RECORD_KEY], rest_state(main), meshes_content())
        main[service._ORIGINAL_SESSION_KEY] = '{}'
        with self.assertRaises(service.SkirtRigError):
            service.unify_skirt(bpy.context, source, main)
        del main[service._ORIGINAL_SESSION_KEY]
        skirt.pose.bones[record['controls']['hem']].keyframe_insert('location', frame=1)
        with self.assertRaises(service.SkirtRigError):
            service.unify_skirt(bpy.context, source, main)
        skirt.animation_data_clear()
        self.assertEqual((source[service.RECORD_KEY], rest_state(main), meshes_content()), baseline)
        service._activate(bpy.context, main, 'EDIT')
        collision = main.data.edit_bones.new(record['controls']['waist'])
        collision.head, collision.tail = (0, 0, 0), (0, 0, 0.1)
        bpy.ops.object.mode_set(mode='OBJECT')
        collision_baseline = rest_state(main)
        with self.assertRaises(service.SkirtRigError):
            service.unify_skirt(bpy.context, source, main)
        self.assertEqual(rest_state(main), collision_baseline)
        self.assertIs(source[service.RIG_KEY], skirt)

    def test_uniform_scale_preserves_world_frames_and_local_control_units(self):
        from character_designer import skirt_shared_rig as shared
        source, skirt, main, record = fixture()
        skirt.scale = (0.933, 0.933, 0.933)
        hem_name = record['controls']['hem']
        hem = skirt.pose.bones[hem_name]
        hem.location = (0.03, -0.02, 0.01)
        hem.custom_shape_translation = (0.01, 0.02, 0.03)
        hem.custom_shape_scale_xyz = (0.2, 0.3, 0.4)
        hem.use_custom_shape_bone_size = False
        bpy.context.view_layer.update()
        before_export = export_skeleton_state(main, skirt)
        before_rest_frames = world_rest_frames(main, skirt)
        before_frames = world_frames(skirt)
        before_vertices, before_cage = world_vertices(source), world_cage(source, record)
        extent = geometry_extent(before_vertices)
        before_data, before_main_rest = meshes_content(), rest_state(main)
        before_main_channels, before_channels = pose_channels(main), pose_channels(skirt)
        old_location = hem.location.copy()
        old_shape_location = hem.custom_shape_translation.copy()
        old_shape_scale = hem.custom_shape_scale_xyz.copy()
        plan = shared.preflight(bpy.context, source, main)
        scale = plan['unit_scale']
        self.assertAlmostEqual(scale, 0.933, places=6)
        updated = service.unify_skirt(bpy.context, source, main)
        self.assertEqual(meshes_content(), before_data)
        self.assertEqual(rest_state(main, before_main_rest), before_main_rest)
        self.assertEqual(pose_channels(main, before_main_channels), before_main_channels)
        new_hem = main.pose.bones[hem_name]
        self.assertEqual(tuple(new_hem.location), tuple(old_location * scale))
        self.assertEqual(tuple(new_hem.custom_shape_translation), tuple(old_shape_location * scale))
        self.assertEqual(tuple(new_hem.custom_shape_scale_xyz), tuple(old_shape_scale * scale))
        for name, old in before_channels.items():
            actual = pose_channels(main, (name,))[name]
            self.assertEqual(actual[0], old[0], name)
            self.assertEqual(actual[2:], old[2:], name)
        self.assert_skirt_frames(main, before_frames, record, extent)
        for name, error in frame_errors(world_rest_frames(main, main, before_rest_frames),
                                         before_rest_frames).items():
            self.assertLessEqual(max(error['head'], error['tail']), 2e-6 * extent, (name, error))
            self.assertLessEqual(error['axis'], 1e-5, (name, error))
        self.assertLessEqual(geometry_error(before_vertices, world_vertices(source)), SOLVER_POSITION_RELATIVE * extent)
        for name, vertices in before_cage.items():
            self.assertLessEqual(geometry_error(vertices, world_vertices(bpy.data.objects[name])), 2e-6 * extent)
        after_export = export_skeleton_state(main)
        self.assertEqual(set(after_export), set(before_export))
        for name, old in before_export.items():
            new = after_export[name]
            self.assertEqual(new[:3], old[:3], name)
            self.assertLessEqual(matrix_error(new[3], old[3]), 5e-6, name)
            self.assertLessEqual(abs(new[4] - old[4]), 5e-6, name)
        self.assertIs(source.modifiers[updated['modifier']].object, main)

    def test_unsupported_spaces_and_envelope_bindings_are_refused_without_mutation(self):
        from character_designer import skirt_shared_rig as shared
        for distortion in ('nonuniform', 'shear', 'source_envelope', 'host_envelope',
                           'disabled_vertex_groups', 'disabled_binding',
                           'disabled_render_binding', 'double_binding'):
            with self.subTest(distortion=distortion):
                source, skirt, main, record = fixture()
                skirt.scale = ((0.933, 0.913, 0.953) if distortion == 'nonuniform'
                               else (0.933, 0.933, 0.933))
                if distortion == 'shear':
                    parent_inverse = skirt.matrix_parent_inverse.copy()
                    parent_inverse[0][1] += 0.1
                    skirt.matrix_parent_inverse = parent_inverse
                if distortion == 'source_envelope':
                    source.modifiers[record['modifier']].use_bone_envelopes = True
                elif distortion == 'host_envelope':
                    mesh = bpy.data.meshes.new('Envelope Body Mesh')
                    mesh.from_pydata([(0, 0, 1), (0.1, 0, 1), (0, 0, 1.2)], [], [(0, 1, 2)])
                    body = bpy.data.objects.new('Envelope Body', mesh)
                    bpy.context.collection.objects.link(body)
                    body.vertex_groups.new(name='Hips').add([0, 1, 2], 1.0, 'REPLACE')
                    modifier = body.modifiers.new('Envelope Body Skin', 'ARMATURE')
                    modifier.object = main
                    modifier.use_bone_envelopes = True
                elif distortion == 'disabled_vertex_groups':
                    source.modifiers[record['modifier']].use_vertex_groups = False
                elif distortion == 'disabled_binding':
                    source.modifiers[record['modifier']].show_viewport = False
                elif distortion == 'disabled_render_binding':
                    source.modifiers[record['modifier']].show_render = False
                elif distortion == 'double_binding':
                    modifier = source.modifiers.new('Existing Main Rig Skin', 'ARMATURE')
                    modifier.object = main
                bpy.context.view_layer.update()
                before_bindings = tuple((modifier.name, modifier.object,
                                         modifier.use_vertex_groups, modifier.use_bone_envelopes,
                                         modifier.show_viewport, modifier.show_render)
                                        for modifier in source.modifiers if modifier.type == 'ARMATURE')
                before = (source[service.RECORD_KEY], meshes_content(), rest_state(main),
                          pose_channels(main), tuple(sorted(bpy.data.objects.keys())))
                message = ('(?i)envelope' if distortion.endswith('envelope') else
                           '(?i)scale|shear' if distortion in {'nonuniform', 'shear'} else '.*')
                with self.assertRaisesRegex(service.SkirtRigError, message):
                    shared.preflight(bpy.context, source, main)
                self.assertEqual((source[service.RECORD_KEY], meshes_content(), rest_state(main),
                                  pose_channels(main), tuple(sorted(bpy.data.objects.keys()))), before)
                self.assertIs(source[service.RIG_KEY], skirt)
                self.assertFalse(service.shared_sources(main))
                self.assertEqual(tuple((modifier.name, modifier.object,
                                        modifier.use_vertex_groups, modifier.use_bone_envelopes,
                                        modifier.show_viewport, modifier.show_render)
                                       for modifier in source.modifiers if modifier.type == 'ARMATURE'),
                                 before_bindings)

    def test_save_reopen_rename_and_owned_remove_keep_character_and_artist_data(self):
        source, skirt, main, record = fixture()
        character_rest = rest_state(main)
        source_content = mesh_content(source)
        artist_keys = source_content['keys']
        updated = service.unify_skirt(bpy.context, source, main)
        main.name = 'Renamed Character'
        source.name = 'Renamed Dress'
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'shared-dress.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            main = bpy.data.objects['Renamed Character']
            source = bpy.data.objects['Renamed Dress']
            self.assertIs(source[service.RIG_KEY], main)
            self.assertIs(service.shared_sources(main)[0], source)
            self.assertTrue(service.is_shared(service.read_record(source)))
            self.assertEqual(mesh_content(source), source_content)
            service.remove_skirt(bpy.context, source)
            self.assertIn(main.name, bpy.data.objects)
            self.assertEqual(rest_state(main), character_rest)
            self.assertEqual(list(source.vertex_groups.keys()), ['Artist pin'])
            self.assertEqual(mesh_content(source)['keys'], artist_keys)
            self.assertFalse(service.shared_sources(main))
            self.assertNotIn(service.RIG_KEY, source)
            self.assertFalse(any(obj.get(service.OWNER_KEY) == updated['owner']
                                 for obj in bpy.data.objects))

    def test_default_build_uses_character_and_repeat_build_does_not_duplicate(self):
        source, skirt, main, record = fixture(posed=False)
        service.remove_skirt(bpy.context, source)
        updated = service.build_skirt(bpy.context, source, armature=main)
        self.assertTrue(service.is_shared(updated))
        self.assertIs(source[service.RIG_KEY], main)
        counts = (len(bpy.data.objects), len(main.data.bones))
        repeated = service.build_skirt(bpy.context, source, armature=main)
        self.assertEqual(repeated['owner'], updated['owner'])
        self.assertEqual((len(bpy.data.objects), len(main.data.bones)), counts)

    def test_shared_export_keeps_weighted_dress_and_body_hair_without_mechanisms(self):
        from character_designer import unity_export_worker as worker
        source, skirt, main, record = fixture(posed=False)
        before_character = set(main.data.bones.keys())
        legacy_export = export_skeleton_state(main, skirt)
        updated = service.unify_skirt(bpy.context, source, main)
        controls, deform, mechanism = service._bone_collection_layout(updated)
        expected_dress = deform | {updated['controls']['waist']}
        before_content, before_rest = meshes_content(), rest_state(main)
        clone = main.copy()
        clone.data = main.data.copy()
        clone.name = 'Disposable Export Skeleton'
        bpy.context.collection.objects.link(clone)
        data = clone.data
        try:
            service._activate(bpy.context, clone, 'OBJECT')
            worker._clean_skeleton(bpy.context, clone, [clone])
            self.assertEqual(set(clone.data.bones.keys()), before_character | expected_dress)
            self.assertFalse(set(clone.data.bones.keys()) & mechanism)
            self.assertFalse(set(clone.data.bones.keys()) & (controls - expected_dress))
        finally:
            if clone.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.data.objects.remove(clone, do_unlink=True)
            if data.users == 0:
                bpy.data.armatures.remove(data)
        self.assertEqual(meshes_content(), before_content)
        self.assertEqual(rest_state(main), before_rest)
        shared_export = export_skeleton_state(main)
        self.assertEqual(set(shared_export), set(legacy_export))
        for name, previous in legacy_export.items():
            actual = shared_export[name]
            self.assertEqual(actual[:3], previous[:3], name)
            self.assertLessEqual(matrix_error(actual[3], previous[3]), 5e-6, name)
            self.assertLessEqual(abs(actual[4] - previous[4]), 5e-6, name)

    def test_two_owned_dresses_share_host_without_cross_removal_or_display(self):
        from character_designer import bone_display as display
        source, skirt, main, record = fixture(posed=False)
        first = service.unify_skirt(bpy.context, source, main)
        second = source.copy()
        second.data = source.data.copy()
        second.name = 'Second Dress'
        bpy.context.collection.objects.link(second)
        world = second.matrix_world.copy()
        second.parent = None
        second.matrix_world = world
        second.location.z += 0.05
        for key in list(second.keys()):
            del second[key]
        second.modifiers.clear()
        second.vertex_groups.clear()
        second.vertex_groups.new(name='Second artist pin').add([2], 0.31, 'REPLACE')
        second_record = service.build_skirt(bpy.context, second, armature=main)
        self.assertTrue(service.is_shared(second_record))
        self.assertIs(second[service.RIG_KEY], main)
        self.assertEqual(set(service.shared_sources(main)), {source, second})
        targets = display._native_targets(bpy.context, main, 'DRESS')
        expected = set()
        for entry in (first, second_record):
            expected |= service._bone_collection_layout(entry)[1] | {entry['controls']['waist']}
        self.assertEqual(targets, {main: expected})
        first_rest = rest_state(main, first['shared']['names'])
        first_content = mesh_content(source)
        first_vertices = world_vertices(source)
        service.remove_skirt(bpy.context, second)
        self.assertEqual(tuple(service.shared_sources(main)), (source,))
        self.assertEqual(rest_state(main, first['shared']['names']), first_rest)
        self.assertEqual(mesh_content(source), first_content)
        self.assertLessEqual(geometry_error(first_vertices, world_vertices(source)), 5e-5)
        self.assertTrue(service.is_shared(service.read_record(source)))

    def test_organize_and_weight_domains_keep_dress_out_of_body_hair(self):
        from character_designer import bone_display as display
        from character_designer import bone_collections as collections
        from character_designer import body_calibration as calibration
        from character_designer import selected_bone_weights as weights
        source, skirt, main, record = fixture(posed=False)
        before_native = calibration.native_rest(main)
        updated = service.unify_skirt(bpy.context, source, main)
        names = set(updated['shared']['names'])
        controls, deform, mechanism = service._bone_collection_layout(updated)
        collections.simplify_body_collections(main)
        self.assertEqual(calibration.native_rest(main), before_native)
        dress = main.data.collections_all[updated['shared']['collection']]
        self.assertEqual(set(dress.bones.keys()), names)
        self.assertFalse(names & set(main.data.collections_all['Original'].bones.keys()))
        other = main.data.collections_all.get('_Other')
        if other:
            self.assertFalse(names & set(other.bones.keys()))
        self.assertEqual(set(weights._mesh_deform_bone_names(source, main)),
                         deform | {updated['controls']['waist']})
        body_mesh = bpy.data.meshes.new('Body Domain Mesh')
        body = bpy.data.objects.new('Body Domain', body_mesh)
        bpy.context.collection.objects.link(body)
        self.assertEqual(set(weights._mesh_deform_bone_names(body, main)),
                         {'Hips', 'Body', 'Hair'})
        display.show_controls(bpy.context, main, 'DRESS', toggle=False)
        pairs = display._control_collections(bpy.context, main, 'DRESS')
        self.assertTrue(display._controls_visible(pairs))
        display.show_controls(bpy.context, main, 'DRESS', toggle=True)
        self.assertFalse(display._controls_visible(pairs))
        self.assertTrue(main.data.collections_all['Body'].is_visible_effectively)
        self.assertFalse(main.data.bones['Hips'].hide)
        self.assertFalse(main.data.bones['Hair'].hide)
        # Fail after the native solver's cross-domain exclusions are applied.
        # Body/Hair deform flags and every painted group must be restored.
        service._activate(bpy.context, main, 'POSE')
        before_flags = {bone.name: bone.use_deform for bone in main.data.bones}
        before_content = mesh_content(source)
        observed = []
        def fail_after_exclusion(*_args):
            observed.append(True)
            self.assertFalse(main.data.bones['Hips'].use_deform)
            self.assertFalse(main.data.bones['Hair'].use_deform)
            self.assertTrue(main.data.bones[updated['controls']['waist']].use_deform)
            raise RuntimeError('Injected solver failure after mesh-domain exclusion')
        modifier = source.modifiers[updated['modifier']]
        with patch.object(weights, '_preflight', return_value=(
                source, main, modifier, (updated['controls']['waist'],))):
            with patch.object(weights, '_enter_weight_paint', side_effect=fail_after_exclusion):
                self.assertEqual(bpy.ops.character_designer.auto_weight_selected_bones(), {'CANCELLED'})
        self.assertTrue(observed)
        self.assertEqual({bone.name: bone.use_deform for bone in main.data.bones}, before_flags)
        self.assertEqual(mesh_content(source), before_content)

    def test_native_undo_restores_original_independent_rig_and_redo_shared_record(self):
        source, skirt, main, record = fixture(posed=False)
        source_name, skirt_name, main_name = source.name, skirt.name, main.name
        before = meshes_content()
        bpy.context.preferences.edit.use_global_undo = True
        service._activate(bpy.context, main, 'OBJECT')
        bpy.ops.ed.undo_push(message='Before shared Dress')
        service.unify_skirt(bpy.context, source, main)
        bpy.ops.ed.undo_push(message='Shared Dress completed')
        if not bpy.ops.ed.undo.poll():
            self.skipTest('Native Undo requires an interactive Blender event loop')
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        source, main = bpy.data.objects[source_name], bpy.data.objects[main_name]
        self.assertIs(source[service.RIG_KEY], bpy.data.objects[skirt_name])
        self.assertFalse(service.is_shared(service.read_record(source)))
        self.assertEqual(meshes_content(), before)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        source, main = bpy.data.objects[source_name], bpy.data.objects[main_name]
        self.assertTrue(service.is_shared(service.read_record(source)))
        self.assertIs(source[service.RIG_KEY], main)
        self.assertNotIn(skirt_name, bpy.data.objects)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SharedRigTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
