"""Dress native posing, local correction return and transactional recovery.

Run only in an isolated Blender process.  This suite creates its own small
shared character and saves disposable files; it never opens the artist scene.
Physics coverage drives the real existing Skirt physics delta constraints with
deterministic physics-chain rotations, without a cloth bake or frame handler.
"""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy
from mathutils import Quaternion

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (
    body_original_mode as original, bone_collections as collections,
    bone_display as display, control_colors, skirt_rig as skirt,
    skirt_original_mode as dress_original,
)
from test_skirt_shared_rig_blender import (
    fixture as migration_fixture, geometry_error, matrix_values, mesh_content,
    meshes_content, pose_channels, rest_state, rig_fixture, world_pose,
    world_vertices,
)


POSE_LIMIT = 5e-5
GEOMETRY_LIMIT = 5e-5


def update(rig):
    rig.update_tag(refresh={'OBJECT', 'DATA', 'TIME'})
    bpy.context.view_layer.update()


def native_names(record):
    return tuple(name for chain in record['chains'] for name in chain['def']) + (
        record['controls']['waist'],)


def constraints_state(rig, names=None):
    names = names or tuple(rig.pose.bones.keys())
    return {name: tuple((constraint.name, constraint.type, constraint.mute,
                        constraint.influence,
                        getattr(constraint, 'mix_mode', None),
                        getattr(constraint, 'owner_space', None),
                        getattr(constraint, 'target_space', None),
                        getattr(getattr(constraint, 'target', None), 'name', None),
                        getattr(constraint, 'subtarget', None))
                       for constraint in rig.pose.bones[name].constraints)
            for name in names}


def colors_state(rig):
    def color_state(color):
        return (color.palette, color.custom.show_colored_constraints,
                tuple(color.custom.normal), tuple(color.custom.select),
                tuple(color.custom.active))
    return {bone.name: (color_state(bone.color),
                        color_state(rig.pose.bones[bone.name].color))
            for bone in rig.data.bones}


def direct_rotation(rig, name, angle=.14):
    """Rotate a native local channel while keeping its current location/scale."""
    pose = rig.pose.bones[name]
    rotation = pose.matrix_basis.to_quaternion()
    pose.rotation_mode = 'QUATERNION'
    pose.rotation_quaternion = rotation @ Quaternion((1.0, 0.0, 0.0), angle)
    update(rig)


def protected_state(main, foreign):
    native = ('Hips', 'Body', 'Hair')
    return {
        'raw_meshes': meshes_content(),
        'main_rest': rest_state(main),
        'main_native_channels': pose_channels(main, native),
        'main_native_constraints': constraints_state(main, native),
        'colors': colors_state(main),
        'foreign_rest': rest_state(foreign),
        'foreign_channels': pose_channels(foreign),
        'foreign_constraints': constraints_state(foreign),
        'foreign_display': display._snapshot(foreign),
        'foreign_colors': colors_state(foreign),
        'foreign_object': (matrix_values(foreign.matrix_basis),
                           matrix_values(foreign.matrix_parent_inverse),
                           foreign.parent.name if foreign.parent else None),
        'object_inventory': tuple(sorted((obj.name, obj.type) for obj in bpy.data.objects)),
    }


def fixture(*, inputs=True, legacy=False):
    source, rig, main, _record = migration_fixture(posed=False)
    if not legacy:
        skirt.remove_skirt(bpy.context, source)
        record = skirt.build_skirt(bpy.context, source, chain_count=4,
                                  segment_count=3, armature=main)
        rig = main
    else:
        record = skirt.read_record(source)
    # A real Original transaction also works for characters without generated
    # Body controls.  The fixture deliberately keeps existing Body and Hair.
    group = main.data.collections.new('Original')
    group[collections.GROUP_KEY] = 'Original'
    for name in ('Hips', 'Body', 'Hair'):
        group.assign(main.data.bones[name])
    main.data.collections_all['Body'][collections.GROUP_KEY] = 'Body'
    main.pose.bones['Body'].rotation_mode = 'XYZ'
    main.pose.bones['Body'].rotation_euler.y = .021
    main.pose.bones['Hair'].rotation_mode = 'XYZ'
    main.pose.bones['Hair'].rotation_euler.z = -.017
    for name in native_names(record):
        pose = rig.pose.bones[name]
        pose.bone.color.palette = 'CUSTOM'
        pose.color.palette = 'CUSTOM'
        for color in (pose.bone.color, pose.color):
            for channel, value in zip(('normal', 'select', 'active'),
                                      control_colors.PALETTES['ROSE']):
                setattr(color.custom, channel, value)
        pose.lock_rotation = (True, False, True)
        pose.lock_rotation_w = True
        pose.lock_rotations_4d = True
    if inputs:
        waist, _driver_id, _path = skirt.physics_control(source)
        waist['physics_influence'] = .65
        hem = rig.pose.bones[record['controls']['hem']]
        hem.location.x = .025
        hem.rotation_euler.y = .06
        physics = rig.pose.bones[record['chains'][0]['phys'][1]]
        physics.rotation_mode = 'XYZ'
        physics.rotation_euler.x = .09
    foreign = rig_fixture()
    foreign.name = 'Untouched Foreign Character'
    foreign.pose.bones['Body'].rotation_mode = 'XYZ'
    foreign.pose.bones['Body'].rotation_euler.z = .043
    mesh = bpy.data.meshes.new('Foreign Skin Data')
    mesh.from_pydata(((0, 0, 1), (.1, 0, 1), (.1, 0, 1.4), (0, 0, 1.4)),
                     (), ((0, 1, 2, 3),))
    foreign_mesh = bpy.data.objects.new('Foreign Skin', mesh)
    bpy.context.collection.objects.link(foreign_mesh)
    foreign_mesh.vertex_groups.new(name='Body').add((0, 1, 2, 3), 1.0, 'REPLACE')
    foreign_mesh.modifiers.new('Foreign Skin', 'ARMATURE').object = foreign
    foreign_mesh.shape_key_add(name='Basis')
    foreign_mesh.shape_key_add(name='Foreign Smile').data[0].co.x += .01
    skirt._activate(bpy.context, main, 'POSE')
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    update(rig)
    return source, rig, main, foreign, skirt.read_record(source)


class DressOriginalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def assert_pose(self, rig, expected, limit=POSE_LIMIT):
        actual = world_pose(rig, expected)
        errors = {name: max(abs(matrix[row][column] - actual[name][row][column])
                            for row in range(4) for column in range(4))
                  for name, matrix in expected.items()}
        self.assertLessEqual(max(errors.values(), default=0), limit, errors)

    def assert_geometry(self, source, expected, limit=GEOMETRY_LIMIT):
        self.assertLessEqual(geometry_error(expected, world_vertices(source)), limit)

    def test_nonzero_curve_and_physics_enter_without_jump_and_return_exactly(self):
        source, rig, main, foreign, record = fixture()
        before = protected_state(main, foreign)
        channels = pose_channels(rig)
        constraints = constraints_state(rig)
        view = display._snapshot(main)
        vertices = world_vertices(source)
        pose = world_pose(rig, native_names(record))
        locks = {name: original._locks(rig, (name,)) for name in native_names(record)}
        influence = skirt.physics_control(source)[0]['physics_influence']
        original.enter(bpy.context, main)
        self.assertTrue(json.loads(main[original.SESSION]).get('dress_edit'))
        self.assert_pose(rig, pose)
        self.assert_geometry(source, vertices)
        for name in native_names(record):
            native = rig.pose.bones[name]
            self.assertFalse(any(native.lock_rotation), name)
            self.assertFalse(native.lock_rotation_w, name)
            self.assertFalse(native.lock_rotations_4d, name)
            if name != record['controls']['waist']:
                before_constraints = {entry[0]: entry for entry in constraints[name]}
                self.assertEqual(native.constraints['Skirt manual pose'].mute,
                                 before_constraints['Skirt manual pose'][2])
                self.assertEqual(native.constraints['Skirt physics delta'].mute,
                                 before_constraints['Skirt physics delta'][2])
                self.assertEqual(native.constraints['Skirt manual pose'].mix_mode, 'BEFORE_FULL')
                self.assertEqual(native.constraints['Skirt physics delta'].mix_mode, 'BEFORE')
        mechanism = [name for chain in record['chains']
                     for layer in ('manual', 'phys') for name in chain[layer]]
        self.assertEqual(pose_channels(rig, mechanism),
                         {name: channels[name] for name in mechanism})
        original.leave(bpy.context, main)
        self.assertEqual(skirt.physics_control(source)[0]['physics_influence'], influence)
        self.assertEqual(pose_channels(rig), channels)
        self.assertEqual(constraints_state(rig), constraints)
        self.assertEqual(display._snapshot(main), view)
        self.assertEqual(protected_state(main, foreign), before)
        self.assertNotIn(dress_original.CORRECTIONS, source)
        self.assertEqual({name: original._locks(rig, (name,)) for name in locks}, locks)
        self.assert_pose(rig, pose)
        self.assert_geometry(source, vertices)

    def test_native_rotation_changes_skin_return_keeps_pose_and_both_inputs_work(self):
        source, rig, main, foreign, record = fixture()
        protected = protected_state(main, foreign)
        original.enter(bpy.context, main)
        name = record['chains'][0]['def'][1]
        before = world_vertices(source)
        before_pose = world_pose(rig, (name,))[name]
        direct_rotation(rig, name)
        edited = world_vertices(source)
        desired = world_pose(rig, native_names(record))
        self.assertGreater(geometry_error(before, edited), 1e-3)
        self.assertGreater(max(abs(before_pose[i][j] - desired[name][i][j])
                               for i in range(4) for j in range(4)), .01)
        original.leave(bpy.context, main)
        self.assertFalse(original.active(main))
        self.assertIn(dress_original.CORRECTIONS, source)
        correction = source[dress_original.CORRECTIONS]
        self.assert_pose(rig, desired)
        self.assert_geometry(source, edited)
        self.assertEqual(protected_state(main, foreign), protected)
        for chain in record['chains']:
            for bone_name in chain['def']:
                self.assertFalse(rig.pose.bones[bone_name].constraints['Skirt manual pose'].mute)
                self.assertFalse(rig.pose.bones[bone_name].constraints['Skirt physics delta'].mute)
        hem = rig.pose.bones[record['controls']['hem']]
        hem.location.x += .03
        update(rig)
        moved_curve = world_vertices(source)
        self.assertGreater(geometry_error(edited, moved_curve), 1e-3)
        self.assertEqual(source[dress_original.CORRECTIONS], correction)
        physics = rig.pose.bones[record['chains'][0]['phys'][1]]
        physics.rotation_euler.x += .12
        update(rig)
        self.assertGreater(geometry_error(moved_curve, world_vertices(source)), 1e-3)
        self.assertEqual(source[dress_original.CORRECTIONS], correction)
        self.assertEqual(mesh_content(source), protected['raw_meshes'][source.name])

    def test_second_original_session_does_not_accumulate_correction(self):
        source, rig, main, foreign, record = fixture()
        original.enter(bpy.context, main)
        direct_rotation(rig, record['chains'][0]['def'][1], .11)
        original.leave(bpy.context, main)
        correction = source[dress_original.CORRECTIONS]
        channels, constraints = pose_channels(rig), constraints_state(rig)
        wanted, vertices = world_pose(rig, native_names(record)), world_vertices(source)
        colors, rest = colors_state(rig), rest_state(rig)
        for _repeat in range(3):
            original.enter(bpy.context, main)
            self.assert_pose(rig, wanted)
            self.assert_geometry(source, vertices)
            original.leave(bpy.context, main)
            self.assertEqual(source[dress_original.CORRECTIONS], correction)
            self.assertEqual(pose_channels(rig), channels)
            self.assertEqual(constraints_state(rig), constraints)
            self.assertEqual(colors_state(rig), colors)
            self.assertEqual(rest_state(rig), rest)
            self.assert_pose(rig, wanted)
            self.assert_geometry(source, vertices)

    def test_clear_selected_correction_preserves_other_dress_edits_and_raw_data(self):
        source, rig, main, foreign, record = fixture(inputs=False)
        protected = protected_state(main, foreign)
        first, second = record['chains'][0]['def'][1], record['chains'][2]['def'][1]
        baseline = world_pose(rig, (first, second))
        original.enter(bpy.context, main)
        direct_rotation(rig, first, .12)
        direct_rotation(rig, second, -.10)
        original.leave(bpy.context, main)
        second_pose = world_pose(rig, (second,))
        for bone in rig.data.bones:
            pose = rig.pose.bones[bone.name]
            if hasattr(pose, 'select'):
                pose.select = bone.name == first
            elif hasattr(bone, 'select'):
                bone.select = bone.name == first
        rig.data.bones.active = rig.data.bones[first]
        dress_original.clear(bpy.context, main, selected_only=True)
        self.assert_pose(rig, {first: baseline[first]})
        self.assert_pose(rig, second_pose)
        self.assertIn(dress_original.CORRECTIONS, source)
        self.assertEqual(protected_state(main, foreign), protected)
        dress_original.clear(bpy.context, main, selected_only=False)
        self.assert_pose(rig, baseline)
        self.assertNotIn(dress_original.CORRECTIONS, source)
        self.assertEqual(protected_state(main, foreign), protected)

    def test_failed_enter_restores_artist_scene_channels_constraints_and_display(self):
        source, rig, main, foreign, record = fixture()
        before = protected_state(main, foreign)
        channels, constraints, view = pose_channels(rig), constraints_state(rig), display._snapshot(main)
        vertices = world_vertices(source)
        with patch.object(dress_original, 'verify', side_effect=ValueError('Injected Dress enter failure')):
            with self.assertRaisesRegex(ValueError, 'Injected Dress enter failure'):
                original.enter(bpy.context, main)
        self.assertFalse(original.active(main))
        self.assertNotIn(dress_original.CORRECTIONS, source)
        self.assertEqual(pose_channels(rig), channels)
        self.assertEqual(constraints_state(rig), constraints)
        self.assertEqual(display._snapshot(main), view)
        self.assertEqual(protected_state(main, foreign), before)
        self.assert_geometry(source, vertices)

    def test_failed_return_operator_cancels_and_keeps_authored_original_pose(self):
        source, rig, main, foreign, record = fixture()
        original.enter(bpy.context, main)
        direct_rotation(rig, record['chains'][0]['def'][1], .12)
        protected = protected_state(main, foreign)
        channels, constraints, view = pose_channels(rig), constraints_state(rig), display._snapshot(main)
        raw = main[original.SESSION]
        correction = source.get(dress_original.CORRECTIONS)
        wanted, vertices = world_pose(rig, native_names(record)), world_vertices(source)
        with patch.object(dress_original, 'verify', side_effect=ValueError('Injected Dress return failure')):
            self.assertEqual(bpy.ops.character_designer.body_original_mode(action='CONTROLS'), {'CANCELLED'})
        self.assertTrue(original.active(main))
        self.assertEqual(main[original.SESSION], raw)
        self.assertEqual(source.get(dress_original.CORRECTIONS), correction)
        self.assertEqual(pose_channels(rig), channels)
        self.assertEqual(constraints_state(rig), constraints)
        self.assertEqual(display._snapshot(main), view)
        self.assertEqual(protected_state(main, foreign), protected)
        self.assert_pose(rig, wanted)
        self.assert_geometry(source, vertices)
        original.leave(bpy.context, main)
        self.assert_pose(rig, wanted)
        self.assert_geometry(source, vertices)

    def test_save_reopen_active_original_keeps_edit_return_and_character_pink(self):
        source, rig, main, foreign, record = fixture()
        protected = protected_state(main, foreign)
        original.enter(bpy.context, main)
        direct_rotation(rig, record['chains'][0]['def'][1], .13)
        desired, vertices = world_pose(rig, native_names(record)), world_vertices(source)
        names = source.name, rig.name, main.name, foreign.name
        session = main[original.SESSION]
        with tempfile.TemporaryDirectory(prefix='cd-dress-original-') as directory:
            path = Path(directory) / 'dress-original.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            source, rig, main, foreign = (bpy.data.objects[name] for name in names)
            skirt._activate(bpy.context, main, 'POSE')
            self.assertTrue(original.active(main))
            self.assertEqual(main[original.SESSION], session)
            self.assert_pose(rig, desired)
            self.assert_geometry(source, vertices)
            original.leave(bpy.context, main)
            correction = source[dress_original.CORRECTIONS]
            self.assert_pose(rig, desired)
            self.assert_geometry(source, vertices)
            self.assertEqual(protected_state(main, foreign), protected)
            bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            source, rig, main, foreign = (bpy.data.objects[name] for name in names)
            skirt._activate(bpy.context, main, 'POSE')
            importlib.reload(dress_original)
            self.assertFalse(original.active(main))
            self.assertEqual(source[dress_original.CORRECTIONS], correction)
            self.assert_pose(rig, desired)
            self.assert_geometry(source, vertices)
            self.assertEqual(protected_state(main, foreign), protected)

    def test_upgrade_old_active_original_is_idempotent_and_restores_controls(self):
        source, rig, main, foreign, record = fixture()
        protected = protected_state(main, foreign)
        # Return only Dress to the preceding Controls state, leaving Body's
        # saved display session active. This reproduces the old display-only
        # Original contract without weakening exact ownership validation.
        checkpoint = dress_original.checkpoint(
            bpy.context, main, dress_original.prepare(bpy.context, main))
        original.enter(bpy.context, main)
        dress_original.rollback(bpy.context, checkpoint)
        saved = json.loads(main[original.SESSION])
        saved.pop('dress_edit', None)
        main[original.SESSION] = json.dumps(saved, separators=(',', ':'))
        pose, vertices = world_pose(rig, native_names(record)), world_vertices(source)
        original.ensure_dress_editable(bpy.context, main)
        self.assertTrue(json.loads(main[original.SESSION]).get('dress_edit'))
        self.assert_pose(rig, pose)
        self.assert_geometry(source, vertices)
        upgraded = main[original.SESSION]
        channels = pose_channels(rig)
        original.ensure_dress_editable(bpy.context, main)
        self.assertEqual(main[original.SESSION], upgraded)
        self.assertEqual(pose_channels(rig), channels)
        direct_rotation(rig, record['chains'][0]['def'][1], .13)
        edited, wanted = world_vertices(source), world_pose(rig, native_names(record))
        self.assertGreater(geometry_error(vertices, edited), 1e-3)
        original.leave(bpy.context, main)
        self.assert_pose(rig, wanted)
        self.assert_geometry(source, edited)
        self.assertEqual(protected_state(main, foreign), protected)

    def test_waist_direct_rotation_keeps_pose_without_a_new_def_correction(self):
        source, rig, main, foreign, record = fixture()
        protected = protected_state(main, foreign)
        original.enter(bpy.context, main)
        vertices = world_vertices(source)
        direct_rotation(rig, record['controls']['waist'], .09)
        edited = world_vertices(source)
        desired = world_pose(rig, native_names(record))
        waist = pose_channels(rig, (record['controls']['waist'],))
        self.assertGreater(geometry_error(vertices, edited), 1e-3)
        original.leave(bpy.context, main)
        self.assert_pose(rig, desired)
        self.assert_geometry(source, edited)
        self.assertEqual(pose_channels(rig, waist), waist)
        self.assertNotIn(dress_original.CORRECTIONS, source)
        self.assertEqual(protected_state(main, foreign), protected)

    def test_correction_survives_frame_changes_and_later_manual_physics_input(self):
        source, rig, main, foreign, record = fixture()
        hem = rig.pose.bones[record['controls']['hem']]
        hem.location.x = .025
        hem.keyframe_insert('location', frame=1)
        hem.location.x = .07
        hem.keyframe_insert('location', frame=3)
        bpy.context.scene.frame_set(1)
        update(rig)
        original.enter(bpy.context, main)
        name = record['chains'][0]['def'][1]
        direct_rotation(rig, name, .1)
        original.leave(bpy.context, main)
        correction = source[dress_original.CORRECTIONS]
        basis = matrix_values(rig.pose.bones[name].matrix_basis)
        at_one = world_vertices(source)
        bpy.context.scene.frame_set(3)
        update(rig)
        at_three = world_vertices(source)
        self.assertGreater(geometry_error(at_one, at_three), 1e-3)
        self.assertEqual(source[dress_original.CORRECTIONS], correction)
        self.assertEqual(matrix_values(rig.pose.bones[name].matrix_basis), basis)
        physics = rig.pose.bones[record['chains'][0]['phys'][1]]
        physics.rotation_euler.x += .11
        update(rig)
        self.assertGreater(geometry_error(at_three, world_vertices(source)), 1e-3)
        self.assertEqual(source[dress_original.CORRECTIONS], correction)
        self.assertEqual(matrix_values(rig.pose.bones[name].matrix_basis), basis)
        bpy.context.scene.frame_set(1)
        update(rig)
        self.assertEqual(source[dress_original.CORRECTIONS], correction)
        self.assertEqual(matrix_values(rig.pose.bones[name].matrix_basis), basis)

    def test_small_upstream_solver_change_is_compensated_without_double_physics(self):
        source, rig, main, foreign, record = fixture()
        protected = protected_state(main, foreign)
        entries = dress_original.prepare(bpy.context, main)
        dress_original.enter(bpy.context, main, entries)
        direct_rotation(rig, record['chains'][0]['def'][1], .11)
        wanted = dress_original.capture(bpy.context, main, entries)
        wanted_world = {name: rig.matrix_world @ matrix
                        for name, matrix in wanted[record['owner']].items()}
        vertices = world_vertices(source)
        physics_names = tuple(name for chain in record['chains'] for name in chain['phys'])
        physics_channels = pose_channels(rig, physics_names)
        influence = skirt.physics_control(source)[0]['physics_influence']
        # Reproduce a small curve solver input change during another group's
        # transfer, with nonzero existing physics and manual input throughout.
        hem = rig.pose.bones[record['controls']['hem']]
        hem.location.x += 2e-5
        update(rig)
        shifted = dress_original.capture(bpy.context, main, entries)
        initial_error = max(dress_original._difference(matrix, shifted[owner][name])
                            for owner, names in wanted.items() for name, matrix in names.items())
        self.assertGreater(initial_error, 2e-6, 'Fixture did not enter the native increment branch')
        with patch.object(dress_original, '_preserve', wraps=dress_original._preserve) as preserve:
            dress_original.leave(bpy.context, main, entries, desired=wanted)
        self.assertEqual(preserve.call_count, 1)
        final = dress_original.capture(bpy.context, main, entries)
        final_error = max(dress_original._difference(matrix, final[owner][name])
                          for owner, names in wanted.items() for name, matrix in names.items())
        self.assertLess(final_error, initial_error * .25)
        self.assert_pose(rig, wanted_world)
        self.assert_geometry(source, vertices)
        self.assertEqual(pose_channels(rig, physics_names), physics_channels)
        self.assertEqual(skirt.physics_control(source)[0]['physics_influence'], influence)
        self.assertEqual(protected_state(main, foreign), protected)
        correction = source[dress_original.CORRECTIONS]
        hem.location.x += .02
        update(rig)
        curve_moved = world_vertices(source)
        self.assertGreater(geometry_error(vertices, curve_moved), 1e-3)
        self.assertEqual(source[dress_original.CORRECTIONS], correction)
        physics = rig.pose.bones[record['chains'][0]['phys'][1]]
        physics.rotation_euler.x += .11
        update(rig)
        self.assertGreater(geometry_error(curve_moved, world_vertices(source)), 1e-3)
        self.assertEqual(source[dress_original.CORRECTIONS], correction)
        print('DRESS_FORCED_NATIVE_INCREMENT', initial_error, final_error, flush=True)

    def test_artist_transform_driver_is_refused_before_muting_or_overwriting(self):
        source, rig, main, foreign, record = fixture()
        pose = rig.pose.bones[record['chains'][0]['def'][1]]
        curve = pose.driver_add('rotation_euler', 0)
        curve.driver.type = 'SCRIPTED'
        curve.driver.expression = '0.123'
        update(rig)
        protected = protected_state(main, foreign)
        channels, constraints, view = pose_channels(rig), constraints_state(rig), display._snapshot(main)
        vertices = world_vertices(source)
        driver = curve.data_path, curve.array_index, curve.mute, curve.driver.expression
        raw = source[skirt.RECORD_KEY]
        with self.assertRaisesRegex(ValueError, 'animated|driven'):
            original.enter(bpy.context, main)
        self.assertFalse(original.active(main))
        self.assertNotIn(dress_original.CORRECTIONS, source)
        self.assertEqual(source[skirt.RECORD_KEY], raw)
        self.assertEqual((curve.data_path, curve.array_index, curve.mute, curve.driver.expression), driver)
        self.assertEqual(pose_channels(rig), channels)
        self.assertEqual(constraints_state(rig), constraints)
        self.assertEqual(display._snapshot(main), view)
        self.assertEqual(protected_state(main, foreign), protected)
        self.assert_geometry(source, vertices)

    def test_artist_extra_constraint_is_refused_before_scene_changes(self):
        source, rig, main, foreign, record = fixture()
        pose = rig.pose.bones[record['chains'][0]['def'][1]]
        artist = pose.constraints.new('LIMIT_ROTATION')
        artist.name = 'Artist keep this limit'
        artist.use_limit_x = True
        artist.min_x, artist.max_x = -.2, .2
        update(rig)
        protected = protected_state(main, foreign)
        channels, constraints, view = pose_channels(rig), constraints_state(rig), display._snapshot(main)
        raw = source[skirt.RECORD_KEY]
        artist_state = artist.mute, artist.influence, artist.min_x, artist.max_x
        with self.assertRaisesRegex(ValueError, 'constraint'):
            original.enter(bpy.context, main)
        self.assertFalse(original.active(main))
        self.assertNotIn(dress_original.CORRECTIONS, source)
        self.assertEqual(source[skirt.RECORD_KEY], raw)
        self.assertEqual((artist.mute, artist.influence, artist.min_x, artist.max_x), artist_state)
        self.assertEqual(pose_channels(rig), channels)
        self.assertEqual(constraints_state(rig), constraints)
        self.assertEqual(display._snapshot(main), view)
        self.assertEqual(protected_state(main, foreign), protected)

    def test_legacy_separate_dress_uses_same_direct_pose_contract(self):
        source, rig, main, foreign, record = fixture(legacy=True)
        raw, rest, colors = mesh_content(source), rest_state(rig), colors_state(rig)
        constraints = constraints_state(rig)
        pose = world_pose(rig, native_names(record))
        vertices = world_vertices(source)
        original.enter(bpy.context, main)
        self.assert_pose(rig, pose)
        self.assert_geometry(source, vertices)
        direct_rotation(rig, record['chains'][0]['def'][1], .13)
        wanted, vertices = world_pose(rig, native_names(record)), world_vertices(source)
        original.leave(bpy.context, main)
        self.assert_pose(rig, wanted)
        self.assert_geometry(source, vertices)
        self.assertEqual(mesh_content(source), raw)
        self.assertEqual(rest_state(rig), rest)
        self.assertEqual(colors_state(rig), colors)
        self.assertEqual(tuple((name, tuple((entry[0], entry[1], entry[7], entry[8])
                                          for entry in states)) for name, states in constraints.items()),
                         tuple((name, tuple((entry[0], entry[1], entry[7], entry[8])
                                          for entry in states))
                               for name, states in constraints_state(rig).items()))

    def test_physics_add_is_refused_in_original_before_creating_any_helpers(self):
        from character_designer import skirt_physics as physics
        source, rig, main, foreign, record = fixture()
        original.enter(bpy.context, main)
        direct_rotation(rig, record['chains'][0]['def'][1], .1)
        protected = protected_state(main, foreign)
        record_raw, session = source[skirt.RECORD_KEY], main[original.SESSION]
        channels, constraints = pose_channels(rig), constraints_state(rig)
        with self.assertRaisesRegex(ValueError, 'Controls|Original'):
            physics.add_physics(bpy.context, source)
        self.assertEqual(source[skirt.RECORD_KEY], record_raw)
        self.assertEqual(main[original.SESSION], session)
        self.assertEqual(pose_channels(rig), channels)
        self.assertEqual(constraints_state(rig), constraints)
        self.assertEqual(protected_state(main, foreign), protected)

    def test_physics_bake_is_refused_in_original_before_frame_or_artist_changes(self):
        from character_designer import skirt_physics as physics
        source, rig, main, foreign, record = fixture()
        original.enter(bpy.context, main)
        direct_rotation(rig, record['chains'][0]['def'][1], .1)
        protected = protected_state(main, foreign)
        record_raw, session = source[skirt.RECORD_KEY], main[original.SESSION]
        channels, constraints = pose_channels(rig), constraints_state(rig)
        frame = bpy.context.scene.frame_current, bpy.context.scene.frame_subframe
        with self.assertRaisesRegex(ValueError, 'Controls|Original'):
            physics.bake_animation(bpy.context, source, 1, 2)
        self.assertEqual((bpy.context.scene.frame_current, bpy.context.scene.frame_subframe), frame)
        self.assertEqual(source[skirt.RECORD_KEY], record_raw)
        self.assertEqual(main[original.SESSION], session)
        self.assertEqual(pose_channels(rig), channels)
        self.assertEqual(constraints_state(rig), constraints)
        self.assertEqual(protected_state(main, foreign), protected)

    def test_legacy_migration_refuses_persistent_correction_before_mutation(self):
        source, rig, main, foreign, record = fixture(legacy=True)
        original.enter(bpy.context, main)
        direct_rotation(rig, record['chains'][0]['def'][1], .11)
        original.leave(bpy.context, main)
        protected = protected_state(main, foreign)
        raw, correction = source[skirt.RECORD_KEY], source[dress_original.CORRECTIONS]
        channels, constraints, rest = pose_channels(rig), constraints_state(rig), rest_state(rig)
        pose, vertices = world_pose(rig, native_names(record)), world_vertices(source)
        with self.assertRaisesRegex(ValueError, '(?i)correction|original|clear'):
            skirt.unify_skirt(bpy.context, source, main)
        self.assertEqual(source[skirt.RECORD_KEY], raw)
        self.assertEqual(source[dress_original.CORRECTIONS], correction)
        self.assertEqual(pose_channels(rig), channels)
        self.assertEqual(constraints_state(rig), constraints)
        self.assertEqual(rest_state(rig), rest)
        self.assertEqual(protected_state(main, foreign), protected)
        self.assert_pose(rig, pose)
        self.assert_geometry(source, vertices)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DressOriginalTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f'PASS Dress Original {result.testsRun} tests', flush=True)
