"""Per-character native/control palette safety in disposable Blender scenes.

Run only in an isolated Blender process. These tests use the established Body,
Hair, and separate Dress builders, and never open an artist character file.
"""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (
    body_setup, body_original_mode as original, bone_collections, bone_color_palette as palette,
    control_colors, hair_bones_rig as hair, skirt_rig as skirt,
)
import test_body_calibration_blender as body_fixture
import test_bone_display_blender as display_fixture
from test_skirt_original_mode_blender import colors_state, constraints_state
from test_skirt_shared_rig_blender import (
    geometry_error, geometry_extent, matrix_error, meshes_content, pose_channels,
    rest_state, SOLVER_POSITION_RELATIVE, world_pose, world_vertices,
)


GROUPS = ('BODY', 'ARMS', 'LEGS', 'HAIR', 'DRESS')
COLORS = ('normal', 'select', 'active')
CUSTOM_ARMS = {'normal': (.21, .38, .54), 'select': (.40, .67, .85),
               'active': (.72, .86, .97)}
CUSTOM_DRESS = {'normal': (.60, .37, .47), 'select': (.86, .58, .71),
                'active': (.98, .79, .87)}


def add_native_arm_details(main):
    """Exercise both-side wrist/finger descendants beyond the saved arm chain."""
    display_fixture.activate(main, 'EDIT')
    bones = main.data.edit_bones
    names = set()
    for side, sign in (('L', 1), ('R', -1)):
        hand = bones['hand.' + side]
        wrist = display_fixture.base.add_bone(
            bones, 'wrist.' + side, hand.head,
            hand.head + Vector((.015 * sign, .01, 0)), hand)
        names.add(wrist.name)
        for finger, offset, segments in (('index', -.025, 3), ('thumb', .025, 2)):
            previous = hand
            position = hand.tail + Vector((0, offset, 0))
            for index in range(1, segments + 1):
                end = position + Vector((.035 * sign, -.004, -.002))
                previous = display_fixture.base.add_bone(
                    bones, f'{finger}{index}.{side}', position, end, previous)
                names.add(previous.name)
                position = end
    bpy.ops.object.mode_set(mode='OBJECT')
    return names


def fixture(*, generate_body=True, accessories=True):
    main, skin = body_fixture.fixture()
    native_details = add_native_arm_details(main)
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    body_fixture.prepare(main)
    if generate_body:
        body_setup.generate(bpy.context, main)
    bone_collections.simplify_body_collections(main)
    result = SimpleNamespace(main=main, skin=skin, native_details=native_details,
                             rigs=(main,), hair_names=set(), dress_names=set(),
                             mechanism_names=set())
    if accessories:
        hair_mesh, plans = display_fixture.make_hair('Palette Hair', strands=1)
        hair_result = display_fixture.build_hair(
            hair_mesh, plans, armature=main, parent_bone='Chest')
        result.hair_names = {name for chain in hair_result['chains'] for name in chain['bones']}
        display_fixture.activate(main, 'EDIT')
        helper = display_fixture.base.add_bone(
            main.data.edit_bones, 'MCH_Palette_Helper', (0, 0, 2), (0, 0, 2.1),
            main.data.edit_bones['Chest'], deform=False)
        helper[hair.OWNER_KEY] = hair.OWNER_VALUE
        helper_name = helper.name
        authored = display_fixture.base.add_bone(
            main.data.edit_bones, 'Hair_Artist_Native', (0, 0, 1.8), (.03, 0, 1.95),
            main.data.edit_bones['Chest'])
        authored_name = authored.name
        bpy.ops.object.mode_set(mode='OBJECT')
        hair_collection = next(collection for collection in main.data.collections_all
                               if collection.get(hair.OWNER_KEY) == hair.OWNER_VALUE)
        hair_collection.assign(main.data.bones[helper_name])
        hair_collection.assign(main.data.bones[authored_name])
        result.hair_names.add(authored_name)
        result.helper_name = helper_name
        bone_collections.simplify_body_collections(main)
        dress_mesh = display_fixture.frustum('Palette Dress', rows=6, sides=16)
        dress_record = skirt.build_skirt(bpy.context, dress_mesh, armature=main, shared=False)
        dress = dress_mesh[skirt.RIG_KEY]
        controls, native, mechanisms = skirt._bone_collection_layout(dress_record)
        result.dress_names = controls | native
        result.mechanism_names = mechanisms
        foreign = display_fixture.base.make_humanoid('Foreign Palette Character')
        bone_collections.simplify_body_collections(foreign)
        foreign_mesh = display_fixture.frustum('Foreign Palette Dress', rows=6, sides=16)
        skirt.build_skirt(bpy.context, foreign_mesh, armature=foreign, shared=False)
        result.dress = dress
        result.dress_mesh = dress_mesh
        result.foreign = foreign
        result.foreign_dress = foreign_mesh[skirt.RIG_KEY]
        result.rigs = (main, dress, result.foreign, result.foreign_dress)
    display_fixture.activate(main, 'POSE')
    bpy.context.view_layer.update()
    return result


def protected_state(rigs):
    """Palette-only operations preserve artist content, view, and context."""
    return {
        'content': display_fixture.content(rigs),
        'raw_meshes': meshes_content(),
        'channels': {rig.name: pose_channels(rig) for rig in rigs},
        'constraints': {rig.name: constraints_state(rig) for rig in rigs},
        'display': display_fixture.views(rigs),
        'selection': tuple(sorted(obj.name for obj in bpy.context.selected_objects)),
        'active': bpy.context.view_layer.objects.active.name,
        'mode': bpy.context.mode,
        'bone_selection': {rig.name: tuple((pb.name, pb.select) for pb in rig.pose.bones)
                           for rig in rigs},
    }


def plain_value(value):
    if isinstance(value, bpy.types.ID):
        return ('ID', value.name, value.as_pointer())
    if hasattr(value, 'to_dict'):
        return {key: plain_value(item) for key, item in value.to_dict().items()}
    if hasattr(value, 'to_list'):
        return tuple(plain_value(item) for item in value.to_list())
    if isinstance(value, (list, tuple)):
        return tuple(plain_value(item) for item in value)
    return value


def palette_state(rigs):
    """Include ownership/backup records, so a failed write cannot leave residue."""
    return {rig.name: {
        'colors': colors_state(rig),
        'show_bone_colors': rig.data.show_bone_colors,
        'data_properties': {key: plain_value(value) for key, value in rig.data.items()},
        'object_properties': {key: plain_value(value) for key, value in rig.items()},
        'pose_properties': {pb.name: {key: plain_value(value) for key, value in pb.items()}
                            for pb in rig.pose.bones},
    } for rig in rigs}


class Layout:
    def __init__(self, calls=None, operator_properties=None):
        self.calls = [] if calls is None else calls
        self.operator_properties = [] if operator_properties is None else operator_properties

    def row(self, **kwargs):
        return Layout(self.calls, self.operator_properties)

    column = box = row

    def label(self, **kwargs):
        self.calls.append(('label', kwargs))

    def separator(self, **kwargs):
        self.calls.append(('separator', kwargs))

    def prop(self, owner, name, **kwargs):
        if any(owner is properties for properties in self.operator_properties):
            raise AssertionError('Panel swatches must not bind transient operator properties.')
        self.calls.append(('prop', name, kwargs))

    def template_node_socket(self, *, color):
        self.calls.append(('node_socket', tuple(float(value) for value in color)))

    def operator(self, name, **kwargs):
        result = SimpleNamespace()
        self.operator_properties.append(result)
        self.calls.append(('operator', name, kwargs, result))
        return result


class BonePaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def assertColor(self, pb, expected):
        self.assertEqual(pb.color.palette, 'CUSTOM', pb.name)
        self.assertFalse(pb.color.custom.show_colored_constraints, pb.name)
        for state in COLORS:
            actual = tuple(getattr(pb.color.custom, state))
            # Blender stores bone display colors as normalized 8-bit channels.
            # Nearest-channel rounding can differ from the requested float by
            # at most half a byte step, plus float readback precision.
            self.assertLessEqual(max(abs(a - b) for a, b in zip(actual, expected[state])),
                                 .5 / 255 + 1e-6,
                                 (pb.name, state, actual, expected[state]))

    def test_five_groups_match_native_and_controls_without_other_character_changes(self):
        f = fixture()
        protected = protected_state(f.rigs)
        foreign = palette_state((f.foreign, f.foreign_dress))
        helper = colors_state(f.main)[f.helper_name]
        mechanisms = {name: colors_state(f.dress)[name] for name in f.mechanism_names}
        body_helpers = {
            pb.name: colors_state(f.main)[pb.name] for pb in f.main.pose.bones
            if pb.bone.get('character_designer_limb_ik_role') in
            {'HAND_ROTATION', 'FOOT_TARGET', 'POLE_DISPLAY'}
            or pb.bone.get('character_designer_foot_role') in
            {'FOOT_TARGET', 'TOE_SPACE', 'HEEL', 'BALL', 'BANK_IN', 'BANK_OUT'}
        }
        self.assertGreater(palette.apply_palette(f.main, bpy.context), 0)
        status = palette.group_status(f.main, bpy.context)
        self.assertEqual(set(status), set(GROUPS))
        self.assertTrue(all(status[group]['count'] > 0 for group in GROUPS))
        for name in ('Hips', 'Chest'):
            self.assertColor(f.main.pose.bones[name], palette.group_colors(f.main, 'BODY'))
        arm_names = f.native_details | {
            part + '.' + side for part in ('upper_arm', 'forearm', 'hand') for side in 'LR'}
        for name in arm_names:
            self.assertColor(f.main.pose.bones[name], palette.group_colors(f.main, 'ARMS'))
        for name in {part + '.' + side for part in ('thigh', 'shin', 'foot', 'toe') for side in 'LR'}:
            if name in f.main.pose.bones:
                self.assertColor(f.main.pose.bones[name], palette.group_colors(f.main, 'LEGS'))
        for name in f.hair_names:
            self.assertColor(f.main.pose.bones[name], palette.group_colors(f.main, 'HAIR'))
        for name in f.dress_names:
            self.assertColor(f.dress.pose.bones[name], palette.group_colors(f.main, 'DRESS'))
        self.assertEqual(colors_state(f.main)[f.helper_name], helper)
        self.assertEqual({name: colors_state(f.main)[name] for name in body_helpers}, body_helpers)
        self.assertEqual({name: colors_state(f.dress)[name] for name in f.mechanism_names}, mechanisms)
        self.assertEqual(palette_state((f.foreign, f.foreign_dress)), foreign)
        self.assertEqual(protected_state(f.rigs), protected)

    def test_group_edits_update_both_sides_and_dress_native_controls_only(self):
        f = fixture()
        palette.apply_palette(f.main, bpy.context)
        original.enter(bpy.context, f.main)
        original_session = f.main[original.SESSION]
        protected = protected_state(f.rigs)
        before = {rig.name: colors_state(rig) for rig in f.rigs}
        self.assertGreater(palette.edit_group(f.main, 'ARMS', CUSTOM_ARMS, bpy.context), 0)
        arm_names = f.native_details | {
            part + '.' + side for part in ('upper_arm', 'forearm', 'hand') for side in 'LR'}
        for name in arm_names:
            self.assertColor(f.main.pose.bones[name], CUSTOM_ARMS)
        self.assertEqual(colors_state(f.main)['Hips'], before[f.main.name]['Hips'])
        self.assertEqual(colors_state(f.dress), before[f.dress.name])
        self.assertGreater(palette.edit_group(f.main, 'DRESS', CUSTOM_DRESS, bpy.context), 0)
        for name in f.dress_names:
            self.assertColor(f.dress.pose.bones[name], CUSTOM_DRESS)
        for name in f.mechanism_names:
            self.assertEqual(colors_state(f.dress)[name], before[f.dress.name][name])
        for foreign in (f.foreign, f.foreign_dress):
            self.assertEqual(colors_state(foreign), before[foreign.name])
        self.assertEqual(protected_state(f.rigs), protected)
        self.assertEqual(f.main[original.SESSION], original_session)

    def test_untrusted_original_missing_dress_source_and_shared_data_are_refused(self):
        f = fixture()
        # A same-named artist collection is not proof of managed native bones.
        untrusted = display_fixture.base.make_humanoid('Untrusted Artist Character')
        untrusted.data.collections.new('Original')
        untrusted.pose.bones['upper_arm.L'].custom_shape = f.skin
        untrusted_colors = palette_state((untrusted,))
        with self.assertRaises(ValueError):
            palette.apply_palette(untrusted, bpy.context)
        self.assertEqual(palette_state((untrusted,)), untrusted_colors)
        display_fixture.activate(f.main, 'POSE')
        source_record = f.dress_mesh[skirt.RECORD_KEY]
        del f.dress_mesh[skirt.RECORD_KEY]
        before, protected = palette_state(f.rigs), protected_state(f.rigs)
        with self.assertRaises(ValueError):
            palette.apply_palette(f.main, bpy.context)
        self.assertEqual(palette_state(f.rigs), before)
        self.assertEqual(protected_state(f.rigs), protected)
        f.dress_mesh[skirt.RECORD_KEY] = source_record
        source = f.dress[skirt.SOURCE_KEY]
        del f.dress[skirt.SOURCE_KEY]
        before = palette_state(f.rigs)
        with self.assertRaises(ValueError):
            palette.apply_palette(f.main, bpy.context)
        self.assertEqual(palette_state(f.rigs), before)
        f.dress[skirt.SOURCE_KEY] = source
        alias = f.main.copy()
        alias.name = 'Shared Palette Armature Data'
        bpy.context.scene.collection.objects.link(alias)
        rigs = f.rigs + (alias,)
        self.assertGreater(f.main.data.users, 1)
        before = palette_state(rigs)
        with self.assertRaises(ValueError):
            palette.apply_palette(f.main, bpy.context)
        self.assertEqual(palette_state(rigs), before)
        bpy.data.objects.remove(alias, do_unlink=True)
        # Native library IDs must be rejected before attempting property writes.
        with tempfile.TemporaryDirectory(prefix='cd-palette-library-') as directory:
            path = str(Path(directory) / 'linked.blend')
            name = f.main.name
            bpy.data.libraries.write(path, {f.main})
            with bpy.data.libraries.load(path, link=True) as (available, requested):
                self.assertIn(name, available.objects)
                requested.objects = [name]
            linked = requested.objects[0]
            self.assertIsNotNone(linked.library)
            bpy.context.scene.collection.objects.link(linked)
            before = palette_state((linked,))
            with self.assertRaises(ValueError):
                palette.apply_palette(linked, bpy.context)
            self.assertEqual(palette_state((linked,)), before)

    def test_saved_palette_survives_owner_rename_reload_and_exact_restore(self):
        f = fixture()
        before_colors = {rig.name: colors_state(rig) for rig in f.rigs}
        before_backups = {rig.name: {pb.name: pb.get(control_colors.BACKUP_KEY)
                                    for pb in rig.pose.bones} for rig in f.rigs}
        palette.apply_palette(f.main, bpy.context)
        palette.edit_group(f.main, 'DRESS', CUSTOM_DRESS, bpy.context)
        old_name = f.main.name
        f.main.name = 'Artist Renamed Palette Character'
        before_colors[f.main.name] = before_colors.pop(old_name)
        before_backups[f.main.name] = before_backups.pop(old_name)
        names = tuple(rig.name for rig in f.rigs)
        saved_record = f.main[palette.CONFIG_KEY]
        colored = {rig.name: colors_state(rig) for rig in f.rigs}
        protected = protected_state(f.rigs)
        with tempfile.TemporaryDirectory(prefix='cd-bone-palette-') as directory:
            path = str(Path(directory) / 'palette.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path, copy=True), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path), {'FINISHED'})
            rigs = tuple(bpy.data.objects[name] for name in names)
            main = rigs[0]
            self.assertEqual(main[palette.CONFIG_KEY], saved_record)
            self.assertEqual({rig.name: colors_state(rig) for rig in rigs}, colored)
            self.assertGreater(palette.restore_palette(main, bpy.context), 0)
            self.assertEqual({rig.name: colors_state(rig) for rig in rigs}, before_colors)
            self.assertEqual({rig.name: {pb.name: pb.get(control_colors.BACKUP_KEY)
                                        for pb in rig.pose.bones} for rig in rigs}, before_backups)
            self.assertEqual(protected_state(rigs), protected)
            self.assertFalse(any(palette.BACKUP_KEY in pb for rig in rigs for pb in rig.pose.bones))

    def test_invalid_records_and_partial_color_failure_leave_every_target_unchanged(self):
        f = fixture()
        palette.apply_palette(f.main, bpy.context)
        valid_config = f.main[palette.CONFIG_KEY]
        invalid = json.loads(valid_config)
        invalid['groups']['DRESS']['normal'] = [2.0, .2, .3]
        f.main[palette.CONFIG_KEY] = json.dumps(invalid)
        for operation in (
            lambda: palette.apply_palette(f.main, bpy.context),
            lambda: palette.edit_group(f.main, 'ARMS', CUSTOM_ARMS, bpy.context),
            lambda: palette.restore_palette(f.main, bpy.context),
        ):
            before, protected = palette_state(f.rigs), protected_state(f.rigs)
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(palette_state(f.rigs), before)
            self.assertEqual(protected_state(f.rigs), protected)
        f.main[palette.CONFIG_KEY] = valid_config
        bone = f.main.pose.bones['forearm.L']
        valid_backup = bone[palette.BACKUP_KEY]
        bone[palette.BACKUP_KEY] = '{broken palette recovery record'
        for operation in (
            lambda: palette.edit_group(f.main, 'ARMS', CUSTOM_ARMS, bpy.context),
            lambda: palette.restore_palette(f.main, bpy.context),
        ):
            before = palette_state(f.rigs)
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(palette_state(f.rigs), before)
        bone[palette.BACKUP_KEY] = valid_backup
        orphan = f.main.pose.bones[f.helper_name]
        orphan_backup = json.loads(valid_backup)
        orphan_backup['owner'] = 'missing-character-palette'
        orphan[palette.BACKUP_KEY] = json.dumps(orphan_backup)
        before = palette_state(f.rigs)
        with self.assertRaises(ValueError):
            palette.restore_palette(f.main, bpy.context)
        self.assertEqual(palette_state(f.rigs), before)
        del orphan[palette.BACKUP_KEY]
        owner = f.dress[palette.MAIN_KEY]
        f.dress[palette.MAIN_KEY] = f.foreign
        for operation in (
            lambda: palette.edit_group(f.main, 'DRESS', CUSTOM_DRESS, bpy.context),
            lambda: palette.restore_palette(f.main, bpy.context),
        ):
            before = palette_state(f.rigs)
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(palette_state(f.rigs), before)
        f.dress[palette.MAIN_KEY] = owner
        before, protected = palette_state(f.rigs), protected_state(f.rigs)
        set_color = palette._set_color
        calls = []
        def fail_after_write(pb, state):
            set_color(pb, state)
            calls.append(pb.name)
            if len(calls) == 3:
                raise RuntimeError('injected palette write failure')
        with patch.object(palette, '_set_color', side_effect=fail_after_write):
            with self.assertRaisesRegex(RuntimeError, 'injected palette write failure'):
                palette.edit_group(f.main, 'ARMS', CUSTOM_ARMS, bpy.context)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(palette_state(f.rigs), before)
        self.assertEqual(protected_state(f.rigs), protected)

    def test_new_body_hair_and_separate_dress_controls_inherit_saved_groups(self):
        f = fixture(generate_body=False, accessories=False)
        palette.edit_group(f.main, 'ARMS', CUSTOM_ARMS, bpy.context)
        palette.edit_group(f.main, 'DRESS', CUSTOM_DRESS, bpy.context)
        body_setup.generate(bpy.context, f.main)
        generated_controls = [pb for pb in f.main.pose.bones
                              if control_colors.is_control(pb, owned_only=True)
                              and pb.bone.get(control_colors.OWNER_KEY) in control_colors.OWNERS]
        self.assertTrue(generated_controls)
        hand_controls = [pb for pb in generated_controls
                         if pb.bone.get('character_designer_limb_ik_role') == 'HAND_IK']
        self.assertEqual({pb.bone.get('character_designer_limb_ik_side') for pb in hand_controls},
                         {'L', 'R'})
        for pb in hand_controls:
            self.assertColor(pb, CUSTOM_ARMS)
        for pb in generated_controls:
            colors = palette.colors_for_control(pb)
            if colors is not None:
                self.assertColor(pb, colors)
        hair_mesh, plans = display_fixture.make_hair('Later Palette Hair', strands=1)
        hair_result = display_fixture.build_hair(
            hair_mesh, plans, armature=f.main, parent_bone='Chest')
        for chain in hair_result['chains']:
            for name in chain['bones']:
                self.assertColor(f.main.pose.bones[name], palette.group_colors(f.main, 'HAIR'))
        dress_mesh = display_fixture.frustum('Later Palette Dress', rows=6, sides=16)
        record = skirt.build_skirt(bpy.context, dress_mesh, armature=f.main, shared=False)
        dress = dress_mesh[skirt.RIG_KEY]
        controls, native, mechanisms = skirt._bone_collection_layout(record)
        self.assertTrue(controls)
        for name in controls | native:
            self.assertColor(dress.pose.bones[name], CUSTOM_DRESS)
        self.assertFalse(any(palette.BACKUP_KEY in dress.pose.bones[name] for name in mechanisms))
        # Removing an owned generated Dress must not strand a dead object ID in
        # the surviving main character's palette recovery references.
        palette.apply_palette(f.main, bpy.context)
        skirt.remove_skirt(bpy.context, dress_mesh)
        display_fixture.activate(f.main, 'POSE')
        protected = protected_state((f.main,))
        self.assertGreater(palette.edit_group(f.main, 'ARMS', CUSTOM_ARMS, bpy.context), 0)
        self.assertGreater(palette.restore_palette(f.main, bpy.context), 0)
        self.assertFalse(any(palette.BACKUP_KEY in pb for pb in f.main.pose.bones))
        self.assertEqual(protected_state((f.main,)), protected)

    def test_dedicated_dress_palette_migration_persists_refs_and_restores_baseline(self):
        f = fixture()
        # Remove the earlier semantic-color ownership so the explicit palette
        # alone owns this test's disabled bone-color display baseline.
        control_colors.restore(f.main)
        control_colors.restore(f.dress)
        f.main.data.show_bone_colors = False
        f.dress.data.show_bone_colors = False
        f.dress_mesh.shape_key_add(name='Basis')
        key = f.dress_mesh.shape_key_add(name='Artist Dress Detail')
        key.data[0].co.x += .01
        key.value = .35
        old_name = f.dress.name
        main_name = f.main.name
        source_name = f.dress_mesh.name
        foreign_names = (f.foreign.name, f.foreign_dress.name)
        main_names = tuple(f.main.data.bones.keys())
        dress_names = tuple(f.dress.data.bones.keys())
        before_colors = {**colors_state(f.main), **colors_state(f.dress)}
        before_meshes = meshes_content()
        before_main_rest = rest_state(f.main)
        before_main_channels = pose_channels(f.main)
        before_dress_rest = world_pose(f.dress, rest=True)
        before_vertices = world_vertices(f.dress_mesh)
        extent = geometry_extent(before_vertices)
        foreign = palette_state((f.foreign, f.foreign_dress))
        palette.apply_palette(f.main, bpy.context)
        palette.edit_group(f.main, 'DRESS', CUSTOM_DRESS, bpy.context)
        self.assertFalse(bool(f.main.data[palette.DISPLAY_KEY]))
        self.assertFalse(bool(f.dress.data[palette.DISPLAY_KEY]))
        self.assertIn(f.dress, f.main[palette.REFS_KEY].values())
        self.assertFalse(any(palette.BACKUP_KEY in f.dress.pose.bones[name]
                             for name in f.mechanism_names))
        # The validated palette reference may migrate, while any separate
        # artist reference to the old rig must still block its deletion.
        for artist_reference in (f.dress, {'rig': f.dress}):
            f.main['artist_extra_ref'] = artist_reference
            before = palette_state(f.rigs)
            protected = protected_state(f.rigs)
            source_record = f.dress_mesh[skirt.RECORD_KEY]
            with self.assertRaises(skirt.SkirtRigError):
                skirt.unify_skirt(bpy.context, f.dress_mesh, f.main)
            self.assertEqual(palette_state(f.rigs), before)
            self.assertEqual(protected_state(f.rigs), protected)
            self.assertEqual(f.dress_mesh[skirt.RECORD_KEY], source_record)
            self.assertIs(f.dress_mesh[skirt.RIG_KEY], f.dress)
            self.assertIn(old_name, bpy.data.objects)
            del f.main['artist_extra_ref']
        updated = skirt.unify_skirt(bpy.context, f.dress_mesh, f.main)
        self.assertTrue(skirt.is_shared(updated))
        self.assertIs(f.dress_mesh[skirt.RIG_KEY], f.main)
        self.assertNotIn(old_name, bpy.data.objects)
        self.assertEqual(set(f.main[palette.REFS_KEY].values()), {f.main})
        self.assertFalse(bool(f.main.data[palette.DISPLAY_KEY]))
        self.assertEqual(meshes_content(), before_meshes)
        self.assertEqual(rest_state(f.main, main_names), before_main_rest)
        self.assertEqual(pose_channels(f.main, main_names), before_main_channels)
        after_dress_rest = world_pose(f.main, dress_names, rest=True)
        self.assertLessEqual(max(matrix_error(before_dress_rest[name], after_dress_rest[name])
                                 for name in dress_names), 5e-5)
        # This is the existing migration solver's evaluated-space contract.
        # Palette-only operations below retain exact protected-content checks.
        self.assertLessEqual(geometry_error(before_vertices, world_vertices(f.dress_mesh)),
                             SOLVER_POSITION_RELATIVE * extent)
        for name in f.dress_names:
            self.assertColor(f.main.pose.bones[name], CUSTOM_DRESS)
        self.assertFalse(any(palette.BACKUP_KEY in f.main.pose.bones[name]
                             for name in f.mechanism_names))
        self.assertEqual(palette_state((f.foreign, f.foreign_dress)), foreign)
        display_fixture.activate(f.main, 'POSE')
        protected = protected_state((f.main, f.foreign, f.foreign_dress))
        with tempfile.TemporaryDirectory(prefix='cd-palette-migration-') as directory:
            path = str(Path(directory) / 'shared_palette.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path, copy=True), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path), {'FINISHED'})
            main = bpy.data.objects[main_name]
            source = bpy.data.objects[source_name]
            foreign_rigs = tuple(bpy.data.objects[name] for name in foreign_names)
            self.assertNotIn(old_name, bpy.data.objects)
            self.assertIs(source[skirt.RIG_KEY], main)
            self.assertEqual(set(main[palette.REFS_KEY].values()), {main})
            self.assertFalse(bool(main.data[palette.DISPLAY_KEY]))
            self.assertGreater(palette.edit_group(main, 'ARMS', CUSTOM_ARMS, bpy.context), 0)
            self.assertGreater(palette.restore_palette(main, bpy.context), 0)
            self.assertEqual(colors_state(main), before_colors)
            self.assertFalse(main.data.show_bone_colors)
            self.assertNotIn(palette.DISPLAY_KEY, main.data)
            self.assertNotIn(palette.REFS_KEY, main)
            self.assertFalse(any(palette.BACKUP_KEY in pb for pb in main.pose.bones))
            self.assertEqual(protected_state((main, *foreign_rigs)), protected)
            self.assertEqual({rig.name: colors_state(rig) for rig in foreign_rigs},
                             {name: state['colors'] for name, state in foreign.items()})
            for rig in foreign_rigs:
                self.assertNotIn(palette.CONFIG_KEY, rig)
                self.assertFalse(any(palette.BACKUP_KEY in pb for pb in rig.pose.bones))

    def test_status_and_panel_draw_are_read_only_and_edit_operators_support_undo(self):
        f = fixture()
        before, protected = palette_state(f.rigs), protected_state(f.rigs)
        for _ in range(3):
            palette.group_status(f.main, bpy.context)
            for group in GROUPS:
                palette.group_colors(f.main, group)
            layout = Layout()
            palette.draw(layout, bpy.context, f.main)
        group_buttons = [call[3] for call in layout.calls
                         if call[:2] == ('operator', 'character_designer.bone_color_group')]
        self.assertEqual({button.group for button in group_buttons}, set(GROUPS))
        self.assertTrue(all(button.rig_name == f.main.name for button in group_buttons))
        sockets = [call[1] for call in layout.calls if call[0] == 'node_socket']
        self.assertEqual(len(sockets), 15)
        self.assertTrue(all(len(rgba) == 4 and rgba[3] == 1.0 for rgba in sockets))
        self.assertTrue(all(0 <= component <= 1 for rgba in sockets for component in rgba))
        self.assertFalse(any(call[0] == 'prop' for call in layout.calls))
        self.assertFalse(any(hasattr(button, channel)
                             for button in group_buttons for channel in COLORS))
        self.assertEqual(palette_state(f.rigs), before)
        self.assertEqual(protected_state(f.rigs), protected)
        for cls in palette.BONE_COLOR_PALETTE_CLASSES:
            self.assertTrue({'REGISTER', 'UNDO'}.issubset(cls.bl_options))
            self.assertIs(character_designer._registered_rna_class(cls), cls)
        dialog_properties = bpy.ops.character_designer.bone_color_group.get_rna_type().properties
        native_properties = bpy.types.ThemeBoneColorSet.bl_rna.properties
        for channel in COLORS:
            dialog, native = dialog_properties[channel], native_properties[channel]
            self.assertEqual(dialog.subtype, 'COLOR_GAMMA', channel)
            self.assertEqual((dialog.type, dialog.subtype, dialog.array_length),
                             (native.type, native.subtype, native.array_length), channel)
        # Generator rollback restores its exact color snapshot and removes a
        # palette recovery record created after that snapshot was captured.
        palette.edit_group(f.main, 'DRESS', CUSTOM_DRESS, bpy.context)
        pb = f.main.pose.bones['forearm.L']
        saved_bone = control_colors.capture_bone(pb)
        self.assertNotIn('palette_backup', saved_bone)
        self.assertTrue(control_colors.style(pb, force=True))
        self.assertIn(palette.BACKUP_KEY, pb)
        control_colors.restore_bone_state(pb, saved_bone)
        self.assertEqual(control_colors.capture_bone(pb), saved_bone)
        self.assertNotIn(palette.BACKUP_KEY, pb)
        self.assertEqual(bpy.ops.character_designer.bone_color_palette(
            action='APPLY', rig_name=f.main.name), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.bone_color_group(
            group='ARMS', rig_name=f.main.name, **CUSTOM_ARMS), {'FINISHED'})
        for side in 'LR':
            self.assertColor(f.main.pose.bones['forearm.' + side], CUSTOM_ARMS)
        self.assertEqual(protected_state(f.rigs), protected)
        self.assertFalse(palette.group_status(f.main, bpy.context)['ARMS']['mixed'])
        # A later manual artist edit is reported as Mixed; drawing does not
        # silently reapply the persisted scheme or overwrite that native edit.
        f.main.pose.bones['forearm.L'].color.custom.normal = (.50, .20, .80)
        manual = palette_state(f.rigs)
        layout = Layout()
        palette.draw(layout, bpy.context, f.main)
        self.assertTrue(palette.group_status(f.main, bpy.context)['ARMS']['mixed'])
        self.assertTrue(any(call[0] == 'label' and call[1].get('text') == 'Mixed'
                            for call in layout.calls))
        self.assertEqual(palette_state(f.rigs), manual)


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise RuntimeError('Bone color palette tests failed')
