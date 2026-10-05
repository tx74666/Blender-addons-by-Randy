"""Transactional cleanup of owned legacy Dress bone and Hook names.

Run only in a disposable Blender process. The fixtures build their own small
characters and save temporary files; this suite never opens the artist scene.
"""
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (
    body_original_mode as original, bone_color_palette as palette,
    generated_names, limb_ik, skirt_names, skirt_original_mode as dress_original,
    skirt_rig as skirt,
)
from test_skirt_original_mode_blender import (
    direct_rotation, fixture as dress_fixture, update,
)
from test_skirt_shared_rig_blender import (
    geometry_error, matrix_error, matrix_values, mesh_content, world_pose,
    world_vertices,
)


def normalize(value, mapping):
    """Independent comparison of exact bone names and quoted RNA references.

    Do not replace arbitrary text containing a name. Hook labels have their
    own exact generated format; JSON recovery strings are compared as data.
    """
    if isinstance(value, dict):
        return {mapping.get(key, key): normalize(item, mapping)
                for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(normalize(item, mapping) for item in value)
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        for old, new in mapping.items():
            label = re.fullmatch(re.escape('Control ' + old) + r'(\.\d{3})?', value)
            if label:
                return 'Control ' + new + (label.group(1) or '')
            for selector in ('pose.bones', 'bones'):
                value = value.replace(selector + '[' + json.dumps(old) + ']',
                                      selector + '[' + json.dumps(new) + ']')
        if value.startswith(('{', '[')):
            try:
                return normalize(json.loads(value), mapping)
            except (ValueError, TypeError):
                pass
    return value


def plain(value):
    if isinstance(value, bpy.types.ID):
        return (type(value).__name__, value.name,
                value.library.filepath if value.library else None)
    if isinstance(value, bpy.types.PoseBone):
        return ('PoseBone', value.id_data.name, value.name)
    if isinstance(value, bpy.types.Bone):
        return ('Bone', value.id_data.name, value.name)
    if hasattr(value, 'items'):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, '__iter__'):
        return tuple(plain(item) for item in value)
    return (type(value).__name__, getattr(value, 'name', None))


def props(item):
    if item is None:
        return {}
    try:
        items = item.items()
    except TypeError:  # Native modifiers do not expose IDProperties in 5.1.
        return {}
    return {key: plain(value) for key, value in items}


def scalar_rna(item):
    return {prop.identifier: plain(getattr(item, prop.identifier))
            for prop in item.bl_rna.properties
            if prop.identifier != 'rna_type' and prop.type != 'COLLECTION'
            and (not prop.is_readonly or prop.identifier in {'name', 'type'})}


def color(color_set):
    return (color_set.palette, color_set.custom.show_colored_constraints,
            tuple(color_set.custom.normal), tuple(color_set.custom.select),
            tuple(color_set.custom.active))


def colors(rig):
    return {bone.name: (color(bone.color), color(rig.pose.bones[bone.name].color))
            for bone in rig.data.bones}


def animation(item):
    data = getattr(item, 'animation_data', None)
    if data is None:
        return None
    drivers = []
    for curve in data.drivers:
        driver = curve.driver
        drivers.append((curve.data_path, curve.array_index, curve.mute,
                        driver.type, driver.expression, driver.use_self,
                        tuple((variable.name, variable.type,
                               tuple(scalar_rna(target) for target in variable.targets))
                              for variable in driver.variables)))
    action = data.action
    channels = () if action is None else tuple(
        (curve.data_path, curve.array_index, curve.mute, curve.extrapolation,
         tuple((tuple(key.co), tuple(key.handle_left), tuple(key.handle_right),
                key.interpolation, key.handle_left_type, key.handle_right_type)
               for key in curve.keyframe_points))
        for curve in limb_ik._fcurves_for_action(action))
    return {'drivers': tuple(drivers), 'action': plain(action), 'channels': channels}


def scene_state(mapping=None):
    """Raw geometry, group indices/weights and all generated reference state."""
    result = {'objects': {}, 'collections': {}, 'armatures': {}}
    for obj in bpy.context.scene.objects:
        state = {
            'type': obj.type, 'props': props(obj), 'data_props': props(obj.data),
            'data_name': obj.data.name, 'data_users': obj.data.users,
            'parent': plain(obj.parent), 'parent_type': obj.parent_type,
            'parent_bone': obj.parent_bone,
            'basis': matrix_values(obj.matrix_basis),
            'parent_inverse': matrix_values(obj.matrix_parent_inverse),
            'visible': (obj.hide_viewport, obj.hide_render, obj.hide_get(),
                        obj.select_get(), obj.display_type, obj.show_in_front),
            'collections': tuple(group.name for group in obj.users_collection),
            'modifiers': tuple((scalar_rna(modifier), props(modifier),
                                tuple(modifier.vertex_indices)
                                if modifier.type == 'HOOK' else None)
                               for modifier in obj.modifiers),
            'constraints': tuple(scalar_rna(constraint) for constraint in obj.constraints),
            'animation': animation(obj), 'data_animation': animation(obj.data),
        }
        if obj.type == 'MESH':
            state['mesh'] = mesh_content(obj)
            state['keys_animation'] = animation(obj.data.shape_keys) if obj.data.shape_keys else None
        if obj.type == 'CURVE':
            state['curve'] = tuple(
                (spline.type, spline.use_cyclic_u, spline.resolution_u,
                 tuple((tuple(point.co), point.radius, point.tilt, point.weight_softbody)
                       for point in spline.points),
                 tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                        point.handle_left_type, point.handle_right_type, point.radius, point.tilt)
                       for point in spline.bezier_points))
                for spline in obj.data.splines)
        if obj.type == 'ARMATURE':
            state['display'] = (obj.data.display_type, obj.data.show_bone_colors,
                                obj.data.show_bone_custom_shapes, obj.data.pose_position,
                                obj.data.bones.active.name if obj.data.bones.active else None)
            state['bone_collections'] = tuple(
                (group.name, props(group), group.is_visible, group.is_solo,
                 tuple(group.bones.keys())) for group in obj.data.collections_all)
            state['bones'] = {}
            for bone in obj.data.bones:
                pb = obj.pose.bones[bone.name]
                state['bones'][bone.name] = {
                    'rest': (plain(bone.parent), matrix_values(bone.matrix_local),
                             tuple(bone.head_local), tuple(bone.tail_local), bone.length,
                             bone.use_connect, bone.use_deform, bone.inherit_scale,
                             bone.use_inherit_rotation, bone.use_local_location),
                    'bone_props': props(bone), 'pose_props': props(pb),
                    'channels': (pb.rotation_mode, tuple(pb.location), tuple(pb.rotation_euler),
                                 tuple(pb.rotation_quaternion), tuple(pb.rotation_axis_angle),
                                 tuple(pb.scale), matrix_values(pb.matrix_basis)),
                    'locks': (tuple(pb.lock_location), tuple(pb.lock_rotation), tuple(pb.lock_scale),
                              pb.lock_rotation_w, pb.lock_rotations_4d),
                    'constraints': tuple(scalar_rna(constraint) for constraint in pb.constraints),
                    'shape': (plain(pb.custom_shape), plain(pb.custom_shape_transform),
                              tuple(pb.custom_shape_translation), tuple(pb.custom_shape_rotation_euler),
                              tuple(pb.custom_shape_scale_xyz), pb.use_custom_shape_bone_size),
                    'colors': (color(bone.color), color(pb.color)),
                    'visibility': (bone.hide, bone.hide_select, getattr(pb, 'hide', None)),
                    'selected': getattr(pb, 'select', getattr(bone, 'select', None)),
                }
        result['objects'][obj.name] = state
    for group in bpy.data.collections:
        result['collections'][group.name] = (props(group), group.hide_viewport,
                                            group.hide_render, group.hide_select,
                                            tuple(group.objects.keys()), tuple(group.children.keys()))
    result['context'] = (bpy.context.mode,
                         bpy.context.view_layer.objects.active.name
                         if bpy.context.view_layer.objects.active else None,
                         bpy.context.scene.frame_current)
    return normalize(result, mapping or {})


def bound_mesh(rig, name, group_name):
    mesh = bpy.data.meshes.new(name + ' Data')
    mesh.from_pydata(((0, 0, 1), (.1, 0, 1), (.1, 0, 1.1)), (), ((0, 1, 2),))
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.modifiers.new('Artist Armature', 'ARMATURE').object = rig
    obj.vertex_groups.new(name=group_name).add((0, 1, 2), .43, 'REPLACE')
    obj.vertex_groups.new(name='Artist pin').add((1,), .19, 'REPLACE')
    return obj


def fixture(*, shared=False):
    source, rig, main, foreign, record = dress_fixture(inputs=True, legacy=not shared)
    prefix = record['controls']['waist'].removesuffix('_Waist')
    legacy_prefix = prefix + '_' + record['owner'][:6]
    owned = set.union(*skirt._bone_collection_layout(record))
    clean_to_legacy = {bone.name: legacy_prefix + bone.name[len(prefix):]
                       for bone in rig.data.bones if bone.name in owned}
    assert clean_to_legacy
    first_native = record['chains'][0]['def'][1]
    pose = rig.pose.bones[first_native]
    for index, color_set in enumerate((pose.bone.color, pose.color)):
        color_set.palette = 'CUSTOM'
        color_set.custom.normal = (.21 + index * .07, .35, .62)
        color_set.custom.select = (.46, .65, .83)
        color_set.custom.active = (.77, .86, .97)
    for old, new in clean_to_legacy.items():
        rig.data.bones[old].name = new
        assert new in rig.data.bones
    for name in record['cage']:
        for modifier in bpy.data.objects[name].modifiers:
            if modifier.type == 'HOOK':
                modifier.name = 'Control ' + modifier.subtarget
    skirt.write_record(source, skirt_names._remap(record, clean_to_legacy))
    record = skirt.read_record(source)
    skirt._check_existing_geometry(source, record)
    source['artist_note'] = 'Keep the text ' + clean_to_legacy[first_native] + ' unchanged.'
    rig.pose.bones[clean_to_legacy[first_native]]['artist_note'] = source['artist_note']
    update(rig)
    return source, rig, main, foreign, record, {new: old for old, new in clean_to_legacy.items()}


class SkirtNamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def assert_evaluated_unchanged(self, source, rig, before_vertices, before_pose, mapping):
        update(rig)
        self.assertLessEqual(geometry_error(before_vertices, world_vertices(source)), 5e-5)
        actual = world_pose(rig, tuple(mapping.get(name, name) for name in before_pose))
        self.assertLessEqual(max((matrix_error(matrix, actual[mapping.get(name, name)])
                                  for name, matrix in before_pose.items()), default=0), 5e-5)

    def assert_clean(self, source, rig, record, mapping):
        self.assertFalse(set(mapping) & set(rig.data.bones.keys()))
        self.assertTrue(set(mapping.values()).issubset(rig.data.bones.keys()))
        self.assertEqual(skirt.read_record(source),
                         json.loads(json.dumps(normalize(record, mapping))))
        skirt._check_existing_geometry(source, skirt.read_record(source))
        for name in record['cage']:
            for modifier in bpy.data.objects[name].modifiers:
                if modifier.type == 'HOOK':
                    self.assertIn(modifier.subtarget, rig.data.bones)
                    self.assertRegex(modifier.name, '^' + re.escape('Control ' + modifier.subtarget)
                                     + r'(\.\d{3})?$')

    def test_owned_bones_hooks_refs_and_bound_groups_clean_once(self):
        source, rig, _main, _foreign, record, mapping = fixture()
        native = record['chains'][0]['def'][1]
        other = bound_mesh(rig, 'Artist second bound mesh', native)
        old_index = other.vertex_groups[native].index
        hem = rig.pose.bones[record['controls']['hem']]
        hem.keyframe_insert('location', frame=1)
        hem.location.x += .015
        hem.keyframe_insert('location', frame=12)
        bpy.context.scene.frame_set(1)
        update(rig)
        protected, vertices, pose = scene_state(mapping), world_vertices(source), world_pose(rig)
        renamed = skirt_names.clean(source)
        self.assertTrue(renamed)
        self.assert_clean(source, rig, record, mapping)
        self.assertEqual(other.vertex_groups[mapping[native]].index, old_index)
        self.assertEqual(scene_state(), protected)
        self.assert_evaluated_unchanged(source, rig, vertices, pose, mapping)
        settled = scene_state()
        self.assertEqual(skirt_names.clean(source), [])
        result = generated_names.clean_generated_names()
        self.assertEqual(result, {'renamed': [], 'skipped': []})
        self.assertEqual(scene_state(), settled)

    def test_destination_group_on_other_bound_mesh_refuses_atomically(self):
        source, rig, _main, _foreign, record, mapping = fixture(shared=True)
        old = record['chains'][0]['def'][1]
        artist = bound_mesh(rig, 'Artist collision mesh', mapping[old])
        before, vertices = scene_state(), world_vertices(source)
        group = artist.vertex_groups[mapping[old]]
        with self.assertRaises((ValueError, skirt.SkirtRigError)):
            skirt_names.clean(source)
        self.assertEqual(scene_state(), before)
        self.assertEqual(group.name, mapping[old])
        self.assertLessEqual(geometry_error(vertices, world_vertices(source)), 5e-5)

    def test_validation_failure_after_rename_restores_all_records_and_names(self):
        source, rig, main, _foreign, _record, mapping = fixture(shared=True)
        palette.apply_palette(main, bpy.context)
        original.enter(bpy.context, main)
        before, vertices, pose = scene_state(), world_vertices(source), world_pose(rig)
        validate, saw_rename = skirt_names._validate, []

        def fail_after_rename(candidate):
            result = validate(candidate)
            if any(name in rig.data.bones for name in mapping.values()):
                saw_rename.append(True)
                raise ValueError('injected post-rename validation failure')
            return result

        with patch.object(skirt_names, '_validate', side_effect=fail_after_rename):
            with self.assertRaisesRegex(ValueError, 'injected post-rename validation failure'):
                skirt_names.clean(source)
        self.assertTrue(saw_rename, 'Failure must occur after native names were changed.')
        self.assertEqual(scene_state(), before)
        self.assert_evaluated_unchanged(source, rig, vertices, pose, {})
        self.assertTrue(original.active(main))
        self.assertTrue(json.loads(main[palette.CONFIG_KEY])['enabled'])
        original.leave(bpy.context, main)
        palette.restore_palette(main, bpy.context)

    def test_active_original_palette_and_corrections_survive_cleanup_save_reopen(self):
        source, rig, main, foreign, record, mapping = fixture()
        native = record['chains'][0]['def'][1]
        original.enter(bpy.context, main)
        direct_rotation(rig, native, .085)
        original.leave(bpy.context, main)
        self.assertIn(dress_original.CORRECTIONS, source)
        baseline_colors = normalize({rig.name: colors(rig), main.name: colors(main)}, mapping)
        palette.edit_group(main, 'DRESS', {
            'normal': [.72, .44, .58], 'select': [.87, .64, .77], 'active': [1., .82, .91],
        }, bpy.context)
        original.enter(bpy.context, main)
        wanted_session = normalize(json.loads(main[original.SESSION]), mapping)
        protected, vertices, pose = scene_state(mapping), world_vertices(source), world_pose(rig)
        self.assertTrue(skirt_names.clean(source))
        self.assert_clean(source, rig, record, mapping)
        self.assertEqual(normalize(json.loads(main[original.SESSION]), {}), wanted_session)
        self.assertEqual(scene_state(), protected)
        self.assert_evaluated_unchanged(source, rig, vertices, pose, mapping)
        object_names = source.name, rig.name, main.name, foreign.name
        saved_record = source[skirt.RECORD_KEY]
        saved_session = main[original.SESSION]
        saved_corrections = source[dress_original.CORRECTIONS]
        with tempfile.TemporaryDirectory(prefix='cd-dress-name-cleanup-') as directory:
            path = str(Path(directory) / 'cleaned_dress.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path, copy=True), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path, load_ui=False,
                                                   use_scripts=False), {'FINISHED'})
            source, rig, main, foreign = (bpy.data.objects[name] for name in object_names)
            skirt._activate(bpy.context, main, 'POSE')
            self.assertTrue(original.active(main))
            self.assertEqual(source[skirt.RECORD_KEY], saved_record)
            self.assertEqual(main[original.SESSION], saved_session)
            self.assertEqual(source[dress_original.CORRECTIONS], saved_corrections)
            self.assertEqual(skirt_names.clean(source), [])
            self.assert_evaluated_unchanged(source, rig, vertices, pose, mapping)
            original.leave(bpy.context, main)
            self.assertFalse(original.active(main))
            self.assert_evaluated_unchanged(source, rig, vertices, pose, mapping)
            raw_before_restore = mesh_content(source)
            self.assertGreater(palette.restore_palette(main, bpy.context), 0)
            self.assertEqual({rig.name: colors(rig), main.name: colors(main)}, baseline_colors)
            self.assertEqual(mesh_content(source), raw_before_restore)
            self.assertFalse(any(palette.BACKUP_KEY in pb for pb in rig.pose.bones))
            self.assert_evaluated_unchanged(source, rig, vertices, pose, mapping)


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise RuntimeError('Dress legacy bone name cleanup tests failed')
