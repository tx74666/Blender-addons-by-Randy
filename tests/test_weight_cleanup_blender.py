"""Native weight-cleanup safety tests in an isolated, disposable Blender.

Use tools/run_trusted_blender_validation.py and pass --addon-parent/--report
after Blender's --. This script creates its own fixtures, never opens an artist
file, and never saves preferences. It exercises real native memberships,
operator Undo/Redo, the mirrored source Mesh, and failure rollback. Preview
cancellation is tested through the operator's dialog boundary, not OS input.
"""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import bmesh
import bpy


ROOT = Path(__file__).resolve().parents[1]
SUBJECT = None
OPTIONS = None
ATTRIBUTE_STORAGE_ORDERS = {}
SELECTION_ATTRIBUTES = {'.select_vert', '.select_edge', '.select_poly',
                        '.uv_select_vert', '.uv_select_edge', '.uv_select_face'}


def frozen(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bpy.types.ID):
        return (type(value).__name__, value.name)
    if hasattr(value, 'items'):
        return tuple(sorted((str(name), frozen(item)) for name, item in value.items()))
    return tuple(frozen(item) for item in value)


def reset_scene():
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for blocks in (bpy.data.meshes, bpy.data.armatures):
        for block in tuple(blocks):
            if block.users == 0:
                blocks.remove(block)


def make_rig():
    data = bpy.data.armatures.new('Cleanup Rig Data')
    obj = bpy.data.objects.new('Cleanup Rig', data)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    for name, head, tail in (
        ('Foot.L', (1, 0, 0), (1, 0, .5)),
        ('Foot.R', (-1, 0, 0), (-1, 0, .5)),
        ('Pinky.L', (1, 0, 4), (1, 0, 4.5)),
        ('Other', (0, 0, 1), (0, 0, 2)),
        ('Helper', (0, 1, 0), (0, 1, 1)),
    ):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
    bpy.ops.object.mode_set(mode='OBJECT')
    data.bones['Helper'].use_deform = False
    obj['artist_note'] = 'Keep the current artist Rest and pose'
    obj.select_set(False)
    return obj


def add_group(obj, name, values=(), *, locked=False):
    group = obj.vertex_groups.new(name=name)
    for index, value in dict(values).items():
        group.add([index], value, 'REPLACE')
    group.lock_weight = locked
    return group


def make_mesh(name, arm, positions, edges, *, authored=False):
    data = bpy.data.meshes.new(name + ' Data')
    faces = [(0, 1, 2)] if len(positions) >= 3 else []
    if len(positions) >= 4:
        faces.append((1, 2, 3))
    data.from_pydata(positions, edges, faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    for group in ('Foot.L', 'Foot.R', 'Pinky.L', 'Other', 'Helper', 'Artist Mask'):
        add_group(obj, group)
    obj.vertex_groups['Foot.R'].lock_weight = True
    obj.vertex_groups['Helper'].lock_weight = True
    if authored:
        mirror = obj.modifiers.new('Artist Mirror', 'MIRROR')
        mirror.use_axis = (True, False, False)
        mirror.use_mirror_vertex_groups = True
        mirror.use_clip = True
        obj.shape_key_add(name='Basis')
        key = obj.shape_key_add(name='Artist Shoe Curl')
        key.data[2].co.z += .1875
        key.value = .3125
        key.slider_min = -.25
        color = data.color_attributes.new(name='Artist Color', type='FLOAT_COLOR', domain='POINT')
        for index, item in enumerate(color.data):
            item.color = (index / 8.0, .25, .75, 1.0)
        custom = data.attributes.new('Custom_Strap', 'INT', 'POINT')
        for index, item in enumerate(custom.data):
            item.value = index % 2
        uv = data.uv_layers.new(name='Artist Shoe UV')
        for index, loop in enumerate(uv.data):
            loop.uv = (index / 8.0, (index % 3) / 4.0)
            loop.pin_uv = index % 2 == 0
        obj.location = (.125, -.25, .0625)
    modifier = obj.modifiers.new('Existing Artist Armature', 'ARMATURE')
    modifier.object = arm
    modifier.use_bone_envelopes = False
    obj['artist_note'] = 'Keep authored keys and helper memberships'
    return obj


def make_fixture(*, islands=False):
    reset_scene()
    arm = make_rig()
    source = make_mesh('Cleanup Shoe', arm,
                       ((1, 0, 0), (1.125, 0, 0), (1.25, .125, 0), (4, .25, 0)),
                       ((0, 1), (1, 2), (2, 3)), authored=True)
    hand = make_mesh('Reference Hand', arm,
                     ((1, 0, 4), (1.1, 0, 4), (1.2, 0, 4)), ((0, 1), (1, 2)))
    hand.vertex_groups['Pinky.L'].add([0, 1, 2], .8125, 'REPLACE')
    hand.vertex_groups['Artist Mask'].add([1], .4375, 'REPLACE')
    if islands:
        source.vertex_groups['Pinky.L'].add([0, 1], 1.0, 'REPLACE')
        source.vertex_groups['Foot.L'].add([2], .625, 'REPLACE')
        source.vertex_groups['Foot.L'].add([3], .375, 'REPLACE')
    else:
        source.vertex_groups['Foot.L'].add([0], .625, 'REPLACE')
        source.vertex_groups['Foot.L'].add([3], .125, 'REPLACE')
        source.vertex_groups['Pinky.L'].add([3], .75, 'REPLACE')
        source.vertex_groups['Foot.L'].add([2], 0.0, 'REPLACE')
    source.vertex_groups['Foot.R'].add([1, 2], 0.0, 'REPLACE')
    source.vertex_groups['Helper'].add([1], .75, 'REPLACE')
    source.vertex_groups['Artist Mask'].add([2], 0.0, 'REPLACE')
    source.vertex_groups.active_index = source.vertex_groups['Pinky.L'].index
    source.data.vertices[0].select = True
    source.data.vertices[3].hide = True
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.context.view_layer.update()
    return source, arm, hand


def weights(obj):
    names = {group.index: group.name for group in obj.vertex_groups}
    return tuple(tuple(sorted((names[item.group], item.weight) for item in vertex.groups))
                 for vertex in obj.data.vertices)


def attribute_state(data, *, include_selection=True):
    values = []
    storage_order = []
    for attribute in data.attributes:
        if not include_selection and attribute.name in SELECTION_ATTRIBUTES:
            continue
        storage_order.append((attribute.name, attribute.domain, attribute.data_type))
        cells = []
        for cell in attribute.data:
            fields = []
            for prop in cell.bl_rna.properties:
                if prop.identifier == 'rna_type' or prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
                    continue
                fields.append((prop.identifier, frozen(getattr(cell, prop.identifier))))
            cells.append(tuple(fields))
        values.append((attribute.name, attribute.domain, attribute.data_type, tuple(cells)))
    order_key = data.name + (' [all layers]' if include_selection else ' [authored layers]')
    observed = ATTRIBUTE_STORAGE_ORDERS.setdefault(order_key, [])
    if tuple(storage_order) not in observed:
        observed.append(tuple(storage_order))
    # Native Edit-Mesh exit may reorder the attribute storage layers. Identity
    # is the full named attribute, and every domain/type/cell value is still
    # compared; preserving storage position is not an authored-data guarantee.
    return tuple(sorted(values, key=lambda item: item[0]))


def edit_state(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    vertices, edges, faces = tuple(bm.verts), tuple(bm.edges), tuple(bm.faces)
    vi = {value: index for index, value in enumerate(vertices)}
    deform = bm.verts.layers.deform.active
    history = tuple((type(item).__name__, getattr(item, 'index', None)) for item in bm.select_history)
    return (tuple((tuple(vertex.co), vertex.select, vertex.hide,
                   tuple(sorted(vertex[deform].items())) if deform else ()) for vertex in vertices),
            tuple((tuple(vi[value] for value in edge.verts), edge.select, edge.hide) for edge in edges),
            tuple((tuple(vi[value] for value in face.verts), face.select, face.hide) for face in faces), history)


def protected_state(*, include_weights=True, include_selection=True):
    result = {'filepath': bpy.data.filepath, 'mode': bpy.context.mode,
              'active': frozen(bpy.context.view_layer.objects.active),
              'selected': tuple(sorted(obj.name for obj in bpy.context.selected_objects)),
              'frame': bpy.context.scene.frame_current, 'scene': bpy.context.scene.name,
              'mesh_select_mode': tuple(bpy.context.scene.tool_settings.mesh_select_mode), 'objects': []}
    for obj in sorted(bpy.data.objects, key=lambda value: value.name):
        state = {'identity': frozen(obj), 'data': frozen(obj.data), 'parent': frozen(obj.parent),
                 'matrix': tuple(value for row in obj.matrix_world for value in row),
                 'hidden': (obj.hide_get(), obj.hide_viewport, obj.hide_render),
                 'note': obj.get('artist_note'),
                 'modifiers': tuple((modifier.name, modifier.type, modifier.show_viewport,
                                     modifier.show_render, frozen(getattr(modifier, 'object', None)),
                                     frozen(getattr(modifier, 'use_axis', ())),
                                     getattr(modifier, 'use_mirror_vertex_groups', None),
                                     getattr(modifier, 'use_bone_envelopes', None)) for modifier in obj.modifiers)}
        if obj.type == 'MESH':
            state.update(groups=tuple((group.name, group.lock_weight) for group in obj.vertex_groups),
                         active_group=obj.vertex_groups.active_index,
                         paint_masks=(obj.data.use_paint_mask, obj.data.use_paint_mask_vertex),
                         coords=tuple(tuple(vertex.co) for vertex in obj.data.vertices),
                         edges=tuple(tuple(edge.vertices) for edge in obj.data.edges),
                         faces=tuple(tuple(face.vertices) for face in obj.data.polygons),
                         attributes=attribute_state(obj.data, include_selection=include_selection),
                         uv=tuple((layer.name, tuple((tuple(loop.uv), loop.pin_uv)
                                                     for loop in layer.data)) for layer in obj.data.uv_layers),
                         keys=tuple((key.name, key.value, key.mute, key.slider_min, key.slider_max,
                                     key.relative_key.name, tuple(tuple(point.co) for point in key.data))
                                    for key in obj.data.shape_keys.key_blocks) if obj.data.shape_keys else None)
            if include_selection:
                state['vertex_selection'] = tuple((vertex.select, vertex.hide) for vertex in obj.data.vertices)
                state['edge_selection'] = tuple((edge.select, edge.hide) for edge in obj.data.edges)
                state['face_selection'] = tuple((face.select, face.hide) for face in obj.data.polygons)
            if include_weights:
                state['weights'] = weights(obj)
            if obj.mode == 'EDIT':
                state['edit'] = edit_state(obj)
        if obj.type == 'ARMATURE':
            state.update(bones=tuple((bone.name, bone.parent.name if bone.parent else None,
                                      tuple(value for row in bone.matrix_local for value in row), bone.use_deform,
                                      bool(getattr(bone, 'select', False))) for bone in obj.data.bones),
                         pose=tuple((bone.name, tuple(value for row in bone.matrix_basis for value in row),
                                     bool(getattr(bone, 'select', False))) for bone in obj.pose.bones),
                         active_bone=obj.data.bones.active.name if obj.data.bones.active else None,
                         position=obj.data.pose_position)
        result['objects'].append(state)
    return result


def authoring_state():
    """Authored data excluding explicit diagnostic navigation/selection intent."""
    result = protected_state(include_selection=False)
    for name in ('mode', 'active', 'selected', 'mesh_select_mode'):
        result.pop(name, None)
    for obj in result['objects']:
        for name in ('active_group', 'active_bone', 'paint_masks', 'edit'):
            obj.pop(name, None)
        if 'bones' in obj:
            obj['bones'] = tuple(value[:-1] for value in obj['bones'])
            obj['pose'] = tuple(value[:-1] for value in obj['pose'])
    return result


def selected_vertices(obj):
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        return tuple(vertex.index for vertex in bm.verts if vertex.select)
    return tuple(vertex.index for vertex in obj.data.vertices if vertex.select)


class LayoutRecorder:
    """Read-only recording surface for the native carrier's draw callback."""
    def __init__(self, records=None):
        self.records = records if records is not None else []
        self.enabled = True

    def row(self, **_kwargs):
        return LayoutRecorder(self.records)

    def column(self, **_kwargs):
        return LayoutRecorder(self.records)

    def split(self, **_kwargs):
        return LayoutRecorder(self.records)

    def label(self, *, text='', **kwargs):
        self.records.append(('label', text, kwargs))

    def prop(self, owner, name, **kwargs):
        self.records.append(('prop', name, frozen(getattr(owner, name)), kwargs, self.enabled))

    def template_list(self, *args, **kwargs):
        self.records.append(('template_list', args[0], kwargs))


def set_mode(mode):
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    source, arm = bpy.data.objects['Cleanup Shoe'], bpy.data.objects['Cleanup Rig']
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    if mode in {'POSE', 'WEIGHT_PAINT'}:
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='POSE')
        for bone in arm.data.bones:
            if hasattr(bone, 'select'):
                bone.select = bone.name == 'Pinky.L'
        for bone in arm.pose.bones:
            if hasattr(bone, 'select'):
                bone.select = bone.name == 'Pinky.L'
        arm.data.bones.active = arm.data.bones['Pinky.L']
        if mode == 'WEIGHT_PAINT':
            bpy.context.view_layer.objects.active = source
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
    elif mode == 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')


class NativeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.source, self.arm, self.hand = make_fixture()

    def test_preview_is_read_only_and_fill_preserves_mirror_keys_locks_and_authored_weights(self):
        before = protected_state()
        prepared = SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        self.assertEqual(protected_state(), before)
        self.assertTrue(any('Mirror' in warning for warning in prepared.warnings))
        original, geometry = weights(self.source), protected_state(include_weights=False)
        hand_weights = weights(self.hand)
        self.assertEqual(SUBJECT.apply_cleanup_plan(bpy.context, prepared), 2)
        after = weights(self.source)
        self.assertEqual(after[0], original[0])
        self.assertEqual(after[3], original[3])
        self.assertEqual(dict(after[1])['Helper'], .75)
        self.assertEqual(dict(after[1])['Foot.R'], 0.0)
        self.assertEqual(dict(after[2])['Foot.R'], 0.0)
        self.assertEqual(dict(after[2])['Artist Mask'], 0.0)
        self.assertEqual(dict(after[1])['Foot.L'], 1.0)
        self.assertEqual(dict(after[2])['Foot.L'], 1.0)
        self.assertEqual(weights(self.hand), hand_weights)
        self.assertEqual(protected_state(include_weights=False), geometry)

    def test_character_islands_reference_uses_unselected_hand_but_only_changes_shoe(self):
        self.source, self.arm, self.hand = make_fixture(islands=True)
        geometry, hand_weights = protected_state(include_weights=False), weights(self.hand)
        original = weights(self.source)
        prepared = SUBJECT.build_cleanup_plan(bpy.context, 'ISLANDS', scope='CHARACTER')
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        self.assertIn(self.hand.name, prepared.reference_names)
        self.assertEqual(SUBJECT.apply_cleanup_plan(bpy.context, prepared), 2)
        for index in (0, 1):
            self.assertNotIn('Pinky.L', dict(weights(self.source)[index]))
            self.assertEqual(dict(weights(self.source)[index])['Foot.L'], 1.0)
        self.assertEqual(weights(self.source)[2:], original[2:])
        self.assertEqual(weights(self.hand), hand_weights)
        self.assertEqual(protected_state(include_weights=False), geometry)

    def test_missing_seed_component_refuses_without_a_partial_write(self):
        data = self.source.data
        # An extra disconnected component is genuinely unweighted. Other gaps
        # are fillable; this one must prevent *all* of the writes.
        data.vertices[3].co.x = 8.0
        for group in self.source.vertex_groups:
            group.remove([3])
        # Remove the edge between vertex 2 and 3 through a private mesh rebuild.
        rebuilt = bpy.data.meshes.new('Disconnected Cleanup Data')
        rebuilt.from_pydata([tuple(vertex.co) for vertex in data.vertices], [(0, 1), (1, 2)], [])
        self.source.data = rebuilt
        group = self.source.vertex_groups.get('Foot.L') or self.source.vertex_groups.new(name='Foot.L')
        group.add([0], .625, 'REPLACE')
        before = protected_state()
        prepared = SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
        self.assertFalse(prepared.plan.ok)
        with self.assertRaises(SUBJECT.WeightCleanupError):
            SUBJECT.apply_cleanup_plan(bpy.context, prepared)
        self.assertEqual(protected_state(), before)

    def test_second_vertex_write_failure_restores_exact_memberships_and_all_other_data(self):
        prepared = SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        before = protected_state()
        original_write = SUBJECT._write_vertex_memberships
        count = 0

        def fail_second(*args):
            nonlocal count
            count += 1
            original_write(*args)
            if count == 2:
                raise RuntimeError('Injected failure after the second native vertex write')

        with mock.patch.object(SUBJECT, '_write_vertex_memberships', side_effect=fail_second):
            with self.assertRaises(SUBJECT.WeightCleanupError):
                SUBJECT.apply_cleanup_plan(bpy.context, prepared)
        self.assertEqual(count, 2)
        self.assertEqual(protected_state(), before)

    def test_stale_preview_detects_weight_lock_geometry_pose_and_selection_changes(self):
        for mutation in ('weight', 'lock', 'geometry', 'basis', 'pose', 'selection', 'rest', 'group_name'):
            with self.subTest(mutation=mutation):
                self.source, self.arm, self.hand = make_fixture()
                prepared = SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
                self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
                if mutation == 'weight':
                    self.source.vertex_groups['Foot.L'].add([0], .8125, 'REPLACE')
                elif mutation == 'lock':
                    self.source.vertex_groups['Foot.L'].lock_weight = True
                elif mutation == 'geometry':
                    self.source.matrix_world.translation.x += .25
                elif mutation == 'basis':
                    self.source.data.shape_keys.key_blocks[0].data[1].co.z += .25
                elif mutation == 'pose':
                    self.arm.pose.bones['Foot.L'].location.z += .125
                elif mutation == 'selection':
                    self.hand.select_set(True)
                elif mutation == 'rest':
                    for obj in bpy.context.selected_objects:
                        obj.select_set(False)
                    self.arm.select_set(True)
                    bpy.context.view_layer.objects.active = self.arm
                    bpy.ops.object.mode_set(mode='EDIT')
                    self.arm.data.edit_bones['Foot.L'].tail.z += .125
                    bpy.ops.object.mode_set(mode='OBJECT')
                    self.arm.select_set(False)
                    self.source.select_set(True)
                    bpy.context.view_layer.objects.active = self.source
                else:
                    self.source.vertex_groups['Artist Mask'].name = 'Renamed Artist Mask'
                before = protected_state()
                with self.assertRaises(SUBJECT.WeightCleanupError):
                    SUBJECT.apply_cleanup_plan(bpy.context, prepared)
                self.assertEqual(protected_state(), before)

    def test_cancelled_preview_dialog_boundary_never_writes(self):
        before = protected_state()
        recorded = []
        proxy = SimpleNamespace(window_manager=SimpleNamespace(
            invoke_props_dialog=lambda *args, **kwargs: recorded.append((args, kwargs)) or {'CANCELLED'}))
        fake = SimpleNamespace(_prepare=lambda _context: SUBJECT.build_cleanup_plan(bpy.context, 'FILL'),
                               report=lambda *args: self.fail('Valid preview unexpectedly reported an error'))
        self.assertEqual(SUBJECT._CleanupOperator.invoke(fake, proxy, None), {'CANCELLED'})
        self.assertEqual(len(recorded), 1)
        self.assertTrue(fake._prepared_cleanup.plan.ok)
        self.assertEqual(protected_state(), before)

    def test_edit_mode_write_operators_refuse_without_consuming_vertex_selection(self):
        set_mode('EDIT')
        bm = bmesh.from_edit_mesh(self.source.data)
        bm.verts.ensure_lookup_table()
        for vertex in bm.verts:
            vertex.select_set(False)
        bm.verts[2].select_set(True)
        bm.select_history.clear()
        bm.select_history.add(bm.verts[2])
        bmesh.update_edit_mesh(self.source.data, loop_triangles=False, destructive=False)
        before = protected_state()
        for operator in (bpy.ops.character_designer.fill_unweighted,
                         bpy.ops.character_designer.clean_weight_islands):
            self.assertFalse(operator.poll())
            try:
                result = operator('EXEC_DEFAULT')
                self.assertEqual(result, {'CANCELLED'})
            except RuntimeError:
                pass
            self.assertEqual(protected_state(), before)

    def test_locked_selected_group_and_shared_mesh_data_refuse_read_only(self):
        self.source.vertex_groups['Pinky.L'].lock_weight = True
        before = protected_state()
        with self.assertRaises(SUBJECT.WeightCleanupError):
            SUBJECT.build_cleanup_plan(bpy.context, 'ISLANDS')
        self.assertEqual(protected_state(), before)
        self.source.vertex_groups['Pinky.L'].lock_weight = False
        duplicate = self.source.copy()
        duplicate.data = self.source.data
        bpy.context.scene.collection.objects.link(duplicate)
        before = protected_state()
        with self.assertRaises(SUBJECT.WeightCleanupError):
            SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
        self.assertEqual(protected_state(), before)

    def test_find_unweighted_visibly_selects_gaps_but_preserves_all_authored_data(self):
        for mode in ('OBJECT', 'POSE', 'WEIGHT_PAINT', 'EDIT'):
            with self.subTest(mode=mode):
                self.source, self.arm, self.hand = make_fixture()
                before_weights = weights(self.source), weights(self.hand)
                protected = authoring_state()
                set_mode(mode)
                self.assertEqual(bpy.ops.character_designer.find_unweighted('EXEC_DEFAULT'), {'FINISHED'})
                self.assertEqual(selected_vertices(self.source), (1, 2))
                if mode == 'WEIGHT_PAINT':
                    self.assertEqual(bpy.context.mode, 'PAINT_WEIGHT')
                    self.assertTrue(self.source.data.use_paint_mask_vertex)
                    self.assertTrue(self.source.data.vertices[3].hide)
                else:
                    self.assertEqual(bpy.context.mode, 'EDIT_MESH')
                    bm = bmesh.from_edit_mesh(self.source.data)
                    bm.verts.ensure_lookup_table()
                    self.assertTrue(bm.verts[3].hide)
                bpy.ops.object.mode_set(mode='OBJECT')
                self.assertEqual((weights(self.source), weights(self.hand)), before_weights)
                self.assertEqual(authoring_state(), protected)

    def test_strict_zero_fill_and_find_preserve_tiny_positive_native_paint(self):
        self.source.vertex_groups['Foot.L'].add([2], 1e-10, 'REPLACE')
        original = weights(self.source)
        self.assertGreater(dict(original[2])['Foot.L'], 0.0)
        prepared = SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        self.assertEqual(tuple(change.vertex_index for change in prepared.plan.changes), (1,))
        self.assertEqual(SUBJECT.apply_cleanup_plan(bpy.context, prepared), 1)
        self.assertEqual(weights(self.source)[2], original[2])
        before_find = protected_state()
        self.assertEqual(bpy.ops.character_designer.find_unweighted('EXEC_DEFAULT'), {'FINISHED'})
        self.assertEqual(protected_state(), before_find)
        self.assertEqual(weights(self.source)[2], original[2])

    def test_find_with_no_true_zero_gaps_preserves_mode_selection_and_paint_masks(self):
        for mode in ('OBJECT', 'POSE', 'WEIGHT_PAINT', 'EDIT'):
            with self.subTest(mode=mode):
                self.source, self.arm, self.hand = make_fixture()
                prepared = SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
                self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
                self.assertEqual(SUBJECT.apply_cleanup_plan(bpy.context, prepared), 2)
                set_mode(mode)
                if mode == 'EDIT':
                    bm = bmesh.from_edit_mesh(self.source.data)
                    bm.verts.ensure_lookup_table()
                    for vertex in bm.verts:
                        vertex.select_set(False)
                    bm.verts[2].select_set(True)
                    bm.select_history.clear()
                    bm.select_history.add(bm.verts[2])
                    bmesh.update_edit_mesh(self.source.data, loop_triangles=False, destructive=False)
                elif mode == 'WEIGHT_PAINT':
                    self.source.data.use_paint_mask = True
                    self.source.data.use_paint_mask_vertex = False
                before = protected_state()
                selected_before = selected_vertices(self.source)
                self.assertEqual(bpy.ops.character_designer.find_unweighted('EXEC_DEFAULT'), {'FINISHED'})
                self.assertEqual(selected_vertices(self.source), selected_before)
                self.assertEqual(protected_state(), before)

    def test_inspect_current_mesh_discovers_unknown_bone_without_selecting_or_writing(self):
        self.source, self.arm, self.hand = make_fixture(islands=True)
        self.source.vertex_groups.active_index = self.source.vertex_groups['Artist Mask'].index
        for pose in self.arm.pose.bones:
            if hasattr(pose, 'select'):
                pose.select = False
            elif hasattr(pose.bone, 'select'):
                pose.bone.select = False
        self.arm.data.bones.active = None
        before = protected_state()
        prepared = SUBJECT.build_inspection(bpy.context, scope='CHARACTER')
        self.assertTrue(prepared.scan.ok, prepared.scan.to_dict())
        records = {record.bone_name: record for record in prepared.scan.records}
        self.assertEqual(set(records), {'Pinky.L', 'Foot.L'})
        self.assertEqual(records['Pinky.L'].status, 'STRAY')
        self.assertEqual(records['Pinky.L'].suspect_vertex_count, 2)
        self.assertEqual(records['Pinky.L'].main_mesh_name, 'Reference Hand')
        self.assertTrue(records['Pinky.L'].removable)
        self.assertEqual(tuple(prepared.scan.changes), ())
        self.assertEqual(protected_state(), before)
        self.assertEqual(bpy.ops.character_designer.inspect_weight_islands('EXEC_DEFAULT'), {'FINISHED'})
        rna = bpy.ops.character_designer.inspect_weight_islands.get_rna_type()
        self.assertEqual(rna.properties['active_index'].default, -1)
        self.assertEqual(protected_state(), before)
        # With current-Mesh authority there is only one Pinky support region;
        # a distant warning must never become an automatic deletion candidate.
        local = SUBJECT.build_inspection(bpy.context, scope='CURRENT')
        record = next(row for row in local.scan.records if row.bone_name == 'Pinky.L')
        self.assertEqual(record.status, 'DISTANT')
        self.assertFalse(record.removable)
        self.assertEqual(tuple(local.scan.changes), ())
        self.assertEqual(protected_state(), before)
        # There is no selected Deform group. Inspect must not silently turn its
        # discovered candidates into permission to clean all of them.
        with self.assertRaises(SUBJECT.WeightCleanupError):
            SUBJECT.build_cleanup_plan(bpy.context, 'ISLANDS')
        self.assertEqual(protected_state(), before)

    def test_connected_main_influence_with_tiny_bridge_still_reports_its_distant_point(self):
        source = make_mesh('Continuous Influence', self.arm,
                           ((1, 0, 4), (1, 0, 3.5), (1, 0, 0)), ((0, 1), (1, 2)))
        source.vertex_groups['Pinky.L'].add([0], 1.0, 'REPLACE')
        source.vertex_groups['Pinky.L'].add([1], 1e-12, 'REPLACE')
        source.vertex_groups['Pinky.L'].add([2], .875, 'REPLACE')
        source.vertex_groups.active_index = source.vertex_groups['Artist Mask'].index
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        source.select_set(True)
        bpy.context.view_layer.objects.active = source
        bpy.context.view_layer.update()
        before = protected_state()
        inspection = SUBJECT.build_inspection(bpy.context, scope='CURRENT')
        self.assertTrue(inspection.scan.ok, inspection.scan.to_dict())
        record = next(item for item in inspection.scan.records if item.bone_name == 'Pinky.L')
        self.assertEqual(record.status, 'DISTANT')
        self.assertEqual(tuple(record.distant_vertex_indices), (2,))
        self.assertEqual(record.stray_vertex_count, 0)
        self.assertFalse(record.removable)
        self.assertEqual(tuple(inspection.scan.changes), ())
        self.assertEqual(protected_state(), before)

    def test_locate_inspection_row_visibly_selects_suspect_vertices_without_changing_weights(self):
        for mode in ('OBJECT', 'POSE', 'WEIGHT_PAINT'):
            with self.subTest(mode=mode):
                self.source, self.arm, self.hand = make_fixture(islands=True)
                set_mode(mode)
                self.source.vertex_groups.active_index = self.source.vertex_groups['Artist Mask'].index
                inspection = SUBJECT.build_inspection(bpy.context)
                before = authoring_state()
                SUBJECT.locate_weight_group(bpy.context, inspection.target_name, 'Pinky.L',
                                            context_token=inspection.context_token)
                self.assertEqual(self.source.vertex_groups.active.name, 'Pinky.L')
                self.assertEqual(self.arm.data.bones.active.name, 'Pinky.L')
                self.assertEqual(selected_vertices(self.source), (0, 1))
                if mode == 'WEIGHT_PAINT':
                    self.assertEqual(bpy.context.mode, 'PAINT_WEIGHT')
                    self.assertTrue(self.source.data.use_paint_mask_vertex)
                else:
                    self.assertEqual(bpy.context.mode, 'EDIT_MESH')
                bpy.ops.object.mode_set(mode='OBJECT')
                self.assertEqual(authoring_state(), before)
        for target, bone, token in ((self.hand.name, 'Pinky.L', inspection.context_token),
                                    (self.source.name, 'Helper', inspection.context_token),
                                    (self.source.name, 'Missing', inspection.context_token),
                                    (self.source.name, 'Pinky.L', 'stale context')):
            with self.subTest(target=target, bone=bone, token=token):
                before = protected_state()
                with self.assertRaises(SUBJECT.WeightCleanupError):
                    SUBJECT.locate_weight_group(bpy.context, target, bone, context_token=token)
                self.assertEqual(protected_state(), before)

    def test_native_undo_redo_in_object_pose_and_weight_paint_modes(self):
        saved_undo = bpy.context.preferences.edit.use_global_undo
        try:
            bpy.context.preferences.edit.use_global_undo = True
            for mode in ('OBJECT', 'POSE', 'WEIGHT_PAINT'):
                with self.subTest(mode=mode):
                    self.source, self.arm, self.hand = make_fixture()
                    set_mode(mode)
                    self.assertEqual(bpy.ops.ed.undo_push(message='Before weight cleanup'), {'FINISHED'})
                    before = protected_state()
                    # Python callers opt into the public operator's native
                    # UNDO push with this documented second positional flag.
                    self.assertEqual(bpy.ops.character_designer.fill_unweighted('EXEC_DEFAULT', True), {'FINISHED'})
                    after = protected_state()
                    self.assertNotEqual(after, before)
                    self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
                    self.source = bpy.data.objects['Cleanup Shoe']
                    self.arm = bpy.data.objects['Cleanup Rig']
                    self.hand = bpy.data.objects['Reference Hand']
                    self.assertEqual(protected_state(), before)
                    self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
                    self.source = bpy.data.objects['Cleanup Shoe']
                    self.arm = bpy.data.objects['Cleanup Rig']
                    self.hand = bpy.data.objects['Reference Hand']
                    self.assertEqual(protected_state(), after)
        finally:
            bpy.context.preferences.edit.use_global_undo = saved_undo

    def test_allowed_bones_default_all_kept_is_exact_read_only_noop(self):
        allowed = {bone.name for bone in self.arm.data.bones if bone.use_deform}
        before = protected_state()
        prepared = SUBJECT.build_allowed_plan(bpy.context, allowed)
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        self.assertEqual(tuple(prepared.plan.changes), ())
        self.assertEqual(SUBJECT.apply_cleanup_plan(bpy.context, prepared), 0)
        self.assertEqual(protected_state(), before)
        self.assertEqual(bpy.ops.character_designer.clear_bone_influences('EXEC_DEFAULT'), {'FINISHED'})
        self.assertEqual(protected_state(), before)

    def test_allowed_bones_clear_preserves_raw_survivors_and_preexisting_zero_vertices(self):
        allowed = {bone.name for bone in self.arm.data.bones if bone.use_deform} - {'Pinky.L'}
        before_weights = weights(self.source)
        protected = protected_state(include_weights=False)
        hand_weights = weights(self.hand)
        prepared = SUBJECT.build_allowed_plan(bpy.context, allowed)
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        self.assertEqual(tuple(change.vertex_index for change in prepared.plan.changes), (3,))
        self.assertEqual(SUBJECT.apply_cleanup_plan(bpy.context, prepared), 1)
        self.assertEqual(weights(self.source)[:3], before_weights[:3])
        self.assertEqual(dict(weights(self.source)[3]), {'Foot.L': .125})
        self.assertEqual(weights(self.hand), hand_weights)
        self.assertEqual(protected_state(include_weights=False), protected)

    def test_allowed_bones_new_zero_fill_uses_local_kept_bone_and_preserves_authored_data(self):
        self.source, self.arm, self.hand = make_fixture(islands=True)
        allowed = {bone.name for bone in self.arm.data.bones if bone.use_deform} - {'Pinky.L'}
        protected = protected_state(include_weights=False)
        before, hand_weights = weights(self.source), weights(self.hand)
        prepared = SUBJECT.build_allowed_plan(bpy.context, allowed)
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        self.assertEqual(SUBJECT.apply_cleanup_plan(bpy.context, prepared), 2)
        for index in (0, 1):
            self.assertNotIn('Pinky.L', dict(weights(self.source)[index]))
            self.assertEqual(dict(weights(self.source)[index])['Foot.L'], 1.0)
        self.assertEqual(dict(weights(self.source)[1])['Helper'], .75)
        self.assertEqual(dict(weights(self.source)[1])['Foot.R'], 0.0)
        self.assertEqual(weights(self.source)[2:], before[2:])
        self.assertEqual(weights(self.hand), hand_weights)
        self.assertEqual(protected_state(include_weights=False), protected)

    def test_allowed_bones_selected_vertices_scope_and_empty_selection_never_fall_back_to_all(self):
        self.source, self.arm, self.hand = make_fixture(islands=True)
        allowed = {bone.name for bone in self.arm.data.bones if bone.use_deform} - {'Pinky.L'}
        for vertex in self.source.data.vertices:
            vertex.select = vertex.index == 1
        before = weights(self.source)
        prepared = SUBJECT.build_allowed_plan(bpy.context, allowed, selected_vertices_only=True)
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        self.assertEqual(tuple(change.vertex_index for change in prepared.plan.changes), (1,))
        self.assertEqual(SUBJECT.apply_cleanup_plan(bpy.context, prepared), 1)
        self.assertEqual(weights(self.source)[0], before[0])
        self.assertEqual(weights(self.source)[2:], before[2:])
        self.assertNotIn('Pinky.L', dict(weights(self.source)[1]))
        for vertex in self.source.data.vertices:
            vertex.select = False
        protected = protected_state()
        try:
            empty = SUBJECT.build_allowed_plan(bpy.context, allowed, selected_vertices_only=True)
        except SUBJECT.WeightCleanupError:
            pass
        else:
            self.assertEqual(tuple(empty.plan.changes), ())
        self.assertEqual(protected_state(), protected)

    def test_allowed_bones_locked_excluded_positive_or_zero_membership_refuses_all_writes(self):
        for positive in (True, False):
            with self.subTest(positive=positive):
                self.source, self.arm, self.hand = make_fixture(islands=True)
                allowed = {bone.name for bone in self.arm.data.bones if bone.use_deform} - {'Pinky.L'}
                if not positive:
                    self.source.vertex_groups['Pinky.L'].add([1], 0.0, 'REPLACE')
                self.source.vertex_groups['Pinky.L'].lock_weight = True
                for vertex in self.source.data.vertices:
                    vertex.select = vertex.index == 1
                protected = protected_state()
                try:
                    prepared = SUBJECT.build_allowed_plan(bpy.context, allowed, selected_vertices_only=True)
                except SUBJECT.WeightCleanupError:
                    pass
                else:
                    self.assertFalse(prepared.plan.ok)
                    with self.assertRaises(SUBJECT.WeightCleanupError):
                        SUBJECT.apply_cleanup_plan(bpy.context, prepared)
                self.assertEqual(protected_state(), protected)

    def test_allowed_bones_disconnected_missing_donor_refuses_the_complete_plan(self):
        original_data = self.source.data
        rebuilt = bpy.data.meshes.new('Allowed Disconnected Data')
        rebuilt.from_pydata([tuple(vertex.co) for vertex in original_data.vertices], [(0, 1), (2, 3)], [])
        self.source.data = rebuilt
        for name in ('Foot.L', 'Pinky.L'):
            if self.source.vertex_groups.get(name) is None:
                self.source.vertex_groups.new(name=name)
        self.source.vertex_groups['Foot.L'].add([0], .625, 'REPLACE')
        self.source.vertex_groups['Pinky.L'].add([1, 2, 3], 1.0, 'REPLACE')
        allowed = {bone.name for bone in self.arm.data.bones if bone.use_deform} - {'Pinky.L'}
        protected = protected_state()
        prepared = SUBJECT.build_allowed_plan(bpy.context, allowed)
        self.assertFalse(prepared.plan.ok)
        self.assertEqual(tuple(prepared.plan.changes), ())
        with self.assertRaises(SUBJECT.WeightCleanupError):
            SUBJECT.apply_cleanup_plan(bpy.context, prepared)
        self.assertEqual(protected_state(), protected)

    def test_allowed_bones_stale_scope_preview_and_partial_native_write_failure_are_transactional(self):
        self.source, self.arm, self.hand = make_fixture(islands=True)
        allowed = {bone.name for bone in self.arm.data.bones if bone.use_deform} - {'Pinky.L'}
        prepared = SUBJECT.build_allowed_plan(bpy.context, allowed, selected_vertices_only=True)
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        self.source.data.vertices[1].select = not self.source.data.vertices[1].select
        stale_state = protected_state()
        with self.assertRaises(SUBJECT.WeightCleanupError):
            SUBJECT.apply_cleanup_plan(bpy.context, prepared)
        self.assertEqual(protected_state(), stale_state)
        prepared = SUBJECT.build_allowed_plan(bpy.context, allowed)
        self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
        protected = protected_state()
        original_write, count = SUBJECT._write_vertex_memberships, 0

        def fail_second(*args):
            nonlocal count
            count += 1
            original_write(*args)
            if count == 2:
                raise RuntimeError('Injected allowed-bone failure after partial native writes')

        with mock.patch.object(SUBJECT, '_write_vertex_memberships', side_effect=fail_second):
            with self.assertRaises(SUBJECT.WeightCleanupError):
                SUBJECT.apply_cleanup_plan(bpy.context, prepared)
        self.assertEqual(count, 2)
        self.assertEqual(protected_state(), protected)

    def test_allowed_bones_native_operator_rejects_invalid_or_unknown_explicit_exclusions(self):
        before = protected_state()
        for value in ('not json', '{}', '["Pinky.L", "Pinky.L"]', '["Missing"]',
                      '["Helper"]', '["Foot.R"]'):
            with self.subTest(exclude_bones=value):
                self.assertEqual(bpy.ops.character_designer.clear_bone_influences(
                    'EXEC_DEFAULT', exclude_bones=value), {'CANCELLED'})
                self.assertEqual(protected_state(), before)

    def test_allowed_bones_native_keep_rows_are_editable_default_kept_and_cancel_is_read_only(self):
        self.source, self.arm, self.hand = make_fixture(islands=True)
        row_type = SUBJECT.CHARACTERDESIGNER_PG_weight_keep_row
        self.assertTrue(row_type.bl_rna.properties['keep'].default)
        self.assertFalse(row_type.bl_rna.properties['keep'].is_readonly)
        property_name = 'character_designer_test_weight_keep_rows'
        self.assertFalse(hasattr(bpy.types.WindowManager, property_name))
        setattr(bpy.types.WindowManager, property_name,
                bpy.props.CollectionProperty(type=row_type, options={'SKIP_SAVE'}))
        try:
            native_rows = getattr(bpy.context.window_manager, property_name)
            fake = SimpleNamespace(rows=native_rows, exclude_bones='', selected_vertices_only=False,
                                   report=lambda *args: None)
            clear_type = SUBJECT.CHARACTERDESIGNER_OT_clear_bone_influences
            fake._initialize_rows = lambda _context: clear_type._initialize_rows(fake, bpy.context)
            fake._prepare = lambda _context: clear_type._prepare(fake, bpy.context)
            before = protected_state()
            fake._initialize_rows(bpy.context)
            self.assertEqual({row.bone_name for row in native_rows}, {'Foot.L', 'Pinky.L'})
            self.assertTrue(all(row.keep for row in native_rows))
            self.assertEqual(fake.active_index, -1)
            self.assertEqual(tuple(fake._prepare(bpy.context).plan.changes), ())
            pinky = next(row for row in native_rows if row.bone_name == 'Pinky.L')
            self.assertEqual(pinky.vertex_count, 2)
            self.assertEqual(pinky.weight_sum, 2.0)
            pinky.keep = False
            self.assertFalse(pinky.keep, 'The native Keep control is editable')
            prepared = fake._prepare(bpy.context)
            self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
            self.assertEqual(tuple(change.vertex_index for change in prepared.plan.changes), (0, 1))
            self.assertEqual(protected_state(), before, 'Changing Keep choices must only preview')
            calls = []
            proxy = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=
                lambda *args, **kwargs: calls.append((args, kwargs)) or {'CANCELLED'}))
            self.assertEqual(clear_type.invoke(fake, proxy, None), {'CANCELLED'})
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(row.keep for row in native_rows), 'A fresh invocation starts with every bone kept')
            self.assertEqual(protected_state(), before)
            # A source/context change after opening the Keep table refuses the
            # cached preview even when the user subsequently changes a checkbox.
            native_rows[0].keep = False
            self.source.data.vertices[1].select = not self.source.data.vertices[1].select
            stale = protected_state()
            with self.assertRaises(SUBJECT.WeightCleanupError):
                fake._prepare(bpy.context)
            self.assertEqual(protected_state(), stale)
        finally:
            delattr(bpy.types.WindowManager, property_name)

    def test_allowed_bones_native_operator_undo_redo_restores_exact_paint_and_context(self):
        saved_undo = bpy.context.preferences.edit.use_global_undo
        try:
            bpy.context.preferences.edit.use_global_undo = True
            for mode in ('OBJECT', 'POSE', 'WEIGHT_PAINT'):
                with self.subTest(mode=mode):
                    self.source, self.arm, self.hand = make_fixture(islands=True)
                    set_mode(mode)
                    self.assertEqual(bpy.ops.ed.undo_push(message='Before explicit kept-bone cleanup'), {'FINISHED'})
                    before = protected_state()
                    self.assertEqual(bpy.ops.character_designer.clear_bone_influences(
                        'EXEC_DEFAULT', True, exclude_bones='["Pinky.L"]'), {'FINISHED'})
                    after = protected_state()
                    self.assertNotEqual(after, before)
                    self.assertNotIn('Pinky.L', dict(weights(self.source)[0]))
                    self.assertEqual(dict(weights(self.source)[0])['Foot.L'], 1.0)
                    self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
                    self.source = bpy.data.objects['Cleanup Shoe']
                    self.arm = bpy.data.objects['Cleanup Rig']
                    self.hand = bpy.data.objects['Reference Hand']
                    self.assertEqual(protected_state(), before)
                    self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
                    self.source = bpy.data.objects['Cleanup Shoe']
                    self.arm = bpy.data.objects['Cleanup Rig']
                    self.hand = bpy.data.objects['Reference Hand']
                    self.assertEqual(protected_state(), after)
        finally:
            bpy.context.preferences.edit.use_global_undo = saved_undo

    def test_many_unweighted_errors_use_bounded_actual_rejection_and_preserve_all_weights(self):
        count = 11930
        old_data = self.source.data
        data = bpy.data.meshes.new('Many Empty Cleanup Vertices')
        data.from_pydata([(float(index), 0.0, 0.0) for index in range(count)], [], [])
        self.source.data = data
        self.assertIsNot(self.source.data, old_data)
        before = protected_state()
        prepared = SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
        self.assertFalse(prepared.plan.ok)
        self.assertEqual(len(prepared.plan.errors), count)
        self.assertEqual(tuple(prepared.plan.changes), ())
        with self.assertRaises(SUBJECT.WeightCleanupError) as raised:
            SUBJECT.apply_cleanup_plan(bpy.context, prepared)
        message = str(raised.exception)
        self.assertLessEqual(len(message), 600)
        self.assertIn(str(count), message.replace(',', ''))
        self.assertIn('No weights changed.', message)
        self.assertIn(self.source.name, message)
        self.assertIn('vertex 0', message)
        self.assertNotIn(f'vertex {count - 1}', message, 'The rejection must not enumerate every failed vertex')
        reports = []
        fake = SimpleNamespace(_prepare=lambda _context: prepared,
                               report=lambda level, text: reports.append((level, text)))
        self.assertEqual(SUBJECT._CleanupOperator.execute(fake, bpy.context), {'CANCELLED'})
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][1], message)
        lines = SUBJECT.cleanup_error_lines(prepared.plan.errors, review_keep=True)
        self.assertTrue(lines)
        self.assertTrue(all(len(line) <= 82 for line in lines))
        self.assertLessEqual(len(' '.join(lines)), 600)
        self.assertIn(str(count), ' '.join(lines).replace(',', ''))
        self.assertIn('Review Keep choices.', ' '.join(lines))
        self.assertEqual(protected_state(), before)

    def test_single_error_rejection_keeps_precise_detail_and_does_not_claim_failed_rollback_is_safe(self):
        data = bpy.data.meshes.new('One Empty Cleanup Vertex')
        data.from_pydata([(0.0, 0.0, 0.0)], [], [])
        self.source.data = data
        before = protected_state()
        prepared = SUBJECT.build_cleanup_plan(bpy.context, 'FILL')
        self.assertFalse(prepared.plan.ok)
        self.assertEqual(len(prepared.plan.errors), 1)
        with self.assertRaises(SUBJECT.WeightCleanupError) as raised:
            SUBJECT.apply_cleanup_plan(bpy.context, prepared)
        message = str(raised.exception)
        self.assertLessEqual(len(message), 600)
        self.assertIn('1 error.', message)
        self.assertIn(prepared.plan.errors[0], message)
        self.assertIn('No weights changed.', message)
        unsafe = SUBJECT.format_cleanup_errors(('Injected checkpoint restore failure',), weights_unchanged=False)
        self.assertIn('Injected checkpoint restore failure', unsafe)
        self.assertIn('Inspect the Mesh before continuing.', unsafe)
        self.assertNotIn('No weights changed.', unsafe)
        self.assertEqual(protected_state(), before)

    def test_keep_table_draw_previews_clear_names_counts_total_weight_and_source_mirror_without_writes(self):
        self.source, self.arm, self.hand = make_fixture(islands=True)
        property_name = 'character_designer_test_weight_keep_rows'
        self.assertFalse(hasattr(bpy.types.WindowManager, property_name))
        setattr(bpy.types.WindowManager, property_name, bpy.props.CollectionProperty(
            type=SUBJECT.CHARACTERDESIGNER_PG_weight_keep_row, options={'SKIP_SAVE'}))
        try:
            rows = getattr(bpy.context.window_manager, property_name)
            clear_type = SUBJECT.CHARACTERDESIGNER_OT_clear_bone_influences
            fake = SimpleNamespace(rows=rows, exclude_bones='', selected_vertices_only=False,
                                   layout=LayoutRecorder(), report=lambda *args: None)
            fake._initialize_rows = lambda _context: clear_type._initialize_rows(fake, bpy.context)
            fake._prepare = lambda _context: clear_type._prepare(fake, bpy.context)
            fake._initialize_rows(bpy.context)
            pinky = next(row for row in rows if row.bone_name == 'Pinky.L')
            pinky.keep = False
            before = protected_state()
            before_choices = tuple((row.bone_name, row.keep, row.locked) for row in rows)
            prepared = fake._prepare(bpy.context)
            self.assertTrue(prepared.plan.ok, prepared.plan.to_dict())
            self.assertTrue(any('Source vertices only' in warning and 'Mirror' in warning
                                for warning in prepared.warnings))
            self.assertTrue(all('.L' not in warning and '.R' not in warning for warning in prepared.warnings))
            clear_type.draw(fake, bpy.context)
            # Draw the actual UIList row callback against real native row RNA,
            # so a visual preview never changes the editable Keep value.
            for row in rows:
                SUBJECT.CHARACTERDESIGNER_UL_weight_keep.draw_item(
                    None, bpy.context, fake.layout, fake, row, 0, fake, 'active_index')
            labels = [record[1] for record in fake.layout.records if record[0] == 'label']
            self.assertIn('Keep 1 bones / Clear 1 bones', labels)
            self.assertIn('Clear: Pinky.L', labels)
            self.assertIn('Total Weight', labels)
            self.assertIn('Checked = KEEP · Unchecked = CLEAR', labels)
            self.assertTrue(any('Source vertices only' in label and 'Mirror' in label for label in labels))
            keep_controls = [record for record in fake.layout.records if record[:2] == ('prop', 'keep')]
            self.assertEqual(len(keep_controls), len(rows))
            self.assertEqual(tuple((row.bone_name, row.keep, row.locked) for row in rows), before_choices)
            self.assertEqual(protected_state(), before)
        finally:
            delattr(bpy.types.WindowManager, property_name)


def main():
    global SUBJECT, OPTIONS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--addon-parent', type=Path, default=ROOT / 'addons')
    parser.add_argument('--report', type=Path)
    OPTIONS = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    assert not bpy.data.filepath, 'Run only in a disposable factory-startup process'
    config = os.environ.get('BLENDER_USER_CONFIG')
    assert config and Path(config).is_dir(), 'The trusted launcher must isolate BLENDER_USER_CONFIG'
    if OPTIONS.report:
        OPTIONS.report.parent.mkdir(parents=True, exist_ok=True)
    addon_parent = OPTIONS.addon_parent.resolve(strict=True)
    sys.path.insert(0, str(addon_parent))
    SUBJECT = importlib.import_module('character_designer.weight_cleanup')
    assert Path(SUBJECT.__file__).resolve().is_relative_to(addon_parent)
    registered, result = [], None
    try:
        for cls in SUBJECT.CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeCleanupTests))
    finally:
        if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
    report = {
        'schema': 'character-designer.weight-cleanup.native-tests/1',
        'blender': bpy.app.version_string,
        'blender_build_hash': bpy.app.build_hash.decode('ascii', errors='replace'),
        'module': str(SUBJECT.__file__),
        'module_sha256': hashlib.sha256(Path(SUBJECT.__file__).read_bytes()).hexdigest(),
        'core_sha256': hashlib.sha256(Path(SUBJECT.core.__file__).read_bytes()).hexdigest(),
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'autoexec_failed': bpy.app.autoexec_fail,
        'script_auto_execution': 'Trusted launcher --enable-autoexec',
        'isolated_config': config,
        'artist_opened': False, 'preferences_saved': False, 'unity_project_opened': False,
        'os_desktop_input_used': False, 'full_addon_lifecycle_validated': False,
        'native_dialog_cancel_event_validated': False,
        'attribute_comparison': 'All named domains, types and cell values compared; native storage order recorded separately',
        'attribute_storage_orders': ATTRIBUTE_STORAGE_ORDERS,
        'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
        'failures': [{'test': str(test), 'traceback': detail} for test, detail in result.failures],
        'errors': [{'test': str(test), 'traceback': detail} for test, detail in result.errors],
        'skipped': [{'test': str(test), 'reason': reason} for test, reason in result.skipped],
    }
    if OPTIONS.report:
        OPTIONS.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('WEIGHT_CLEANUP_NATIVE ' + json.dumps(report, ensure_ascii=False), flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
