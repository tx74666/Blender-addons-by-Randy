"""Independent Custom Vertex/Parts regressions in a trusted disposable Blender.

Use tools/run_trusted_blender_validation.py with a fresh CONFIG. Arguments after
Blender's --: --report PATH and --source-root PATH (addons, approved_source, or
the character_designer package directory). Only the three feature modules load;
the full add-on and artist files are not opened. Native history is a separate
GUI validation, not claimed by this background suite.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = GROUPS = PARTS = VISIBILITY = PACKAGE = None
CUSTOM_NAMES = ('Custom_Strap1', 'Custom_Strap2', 'Custom_Strap3')
PART_NAME = 'Part_Skirt'


def freeze(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, 'items'):
        return tuple(sorted((str(key), freeze(item)) for key, item in value.items()))
    return tuple(freeze(item) for item in value)


def bm_for(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    return bm


def attribute_values(obj, name):
    if obj.mode == 'EDIT':
        bm = bm_for(obj)
        layer = bm.verts.layers.int.get(name)
        if layer is None:
            raise AssertionError('Missing live member layer: ' + name)
        return tuple(vertex[layer] for vertex in bm.verts)
    return tuple(item.value for item in obj.data.attributes[name].data)


def members(obj, name):
    return {index for index, value in enumerate(attribute_values(obj, name)) if value != 0}


def namespace_state(obj, prefix, lock_prefix):
    names = [attr.name for attr in obj.data.attributes
             if attr.domain == 'POINT' and attr.data_type == 'INT' and attr.name.startswith(prefix)]
    markers = [attr.name for attr in obj.data.attributes if attr.name.startswith(lock_prefix)]
    return (tuple((name, attribute_values(obj, name)) for name in names),
            tuple(sorted((name, attribute_values(obj, name)) for name in markers)))


def visibility_state(obj):
    bm = bm_for(obj)
    return tuple(tuple(bool(item.hide) for item in elements) for elements in (bm.verts, bm.edges, bm.faces))


def overlay_state(obj):
    bm = bm_for(obj)
    domains = {'POINT': bm.verts, 'EDGE': bm.edges, 'FACE': bm.faces}
    rows = []
    for name in VISIBILITY.owned_attribute_names(obj.data):
        attr = obj.data.attributes[name]
        elements = domains[attr.domain]
        layer = elements.layers.int.get(name)
        rows.append((name, attr.domain, attr.data_type, tuple(item[layer] for item in elements)))
    return visibility_state(obj), tuple(sorted(rows))


def all_selection_state(obj):
    bm = bm_for(obj)
    return (namespace_state(obj, 'Custom_', '.cdvg_lock_'),
            namespace_state(obj, 'Part_', '.cdpart_lock_'), overlay_state(obj),
            tuple(tuple(item.select for item in elements) for elements in (bm.verts, bm.edges, bm.faces)),
            tuple(bpy.context.tool_settings.mesh_select_mode),
            obj.data.attributes.active.name if obj.data.attributes.active else '')


def author_state(obj):
    mesh = obj.data
    keys = mesh.shape_keys
    if obj.mode == 'EDIT':
        bm = bm_for(obj)
        deform = bm.verts.layers.deform.active
        coords = tuple(tuple(vertex.co) for vertex in bm.verts)
        edges = tuple(tuple(vertex.index for vertex in edge.verts) for edge in bm.edges)
        faces = tuple(tuple(vertex.index for vertex in face.verts) for face in bm.faces)
        weights = tuple(tuple(sorted(vertex[deform].items())) for vertex in bm.verts)
        shapes = tuple((name, tuple(tuple(vertex[bm.verts.layers.shape[name]]) for vertex in bm.verts))
                       for name in bm.verts.layers.shape.keys())
        artist = bm.verts.layers.float.get('Artist point')
        artist_values = tuple(vertex[artist] for vertex in bm.verts)
    else:
        coords = tuple(tuple(vertex.co) for vertex in mesh.vertices)
        edges = tuple(tuple(edge.vertices) for edge in mesh.edges)
        faces = tuple(tuple(face.vertices) for face in mesh.polygons)
        weights = tuple(tuple((entry.group, entry.weight) for entry in vertex.groups) for vertex in mesh.vertices)
        shapes = ()
        artist_values = tuple(item.value for item in mesh.attributes['Artist point'].data)
    return {'coordinates': coords, 'edges': edges, 'faces': faces, 'weights': weights,
            'deform_groups': tuple((group.name, group.index, group.lock_weight) for group in obj.vertex_groups),
            'active_deform_group': obj.vertex_groups.active_index, 'live_shapes': shapes,
            'shape_keys': tuple((key.name, key.relative_key.name, key.value, key.mute, key.vertex_group,
                                 tuple(tuple(item.co) for item in key.data)) for key in keys.key_blocks),
            'shape_inputs': (keys.use_relative, obj.active_shape_key_index),
            'artist_values': artist_values, 'matrix': freeze(obj.matrix_world),
            'artist_note': obj.get('Artist note'), 'mesh_note': mesh.get('Artist note')}


class LayoutRecorder:
    def __init__(self, operators=None):
        self.operators = [] if operators is None else operators
        self.enabled = True

    def row(self, **kwargs):
        return LayoutRecorder(self.operators)

    def column(self, **kwargs):
        return LayoutRecorder(self.operators)

    def separator(self):
        pass

    def label(self, **kwargs):
        pass

    def operator(self, identifier, **kwargs):
        item = SimpleNamespace(identifier=identifier, arguments=kwargs)
        self.operators.append(item)
        return item


class PartsIndependenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registered = []
        try:
            for module in (GROUPS, PARTS):
                for item in module.CLASSES:
                    bpy.utils.register_class(item)
                    cls.registered.append(item)
                module.register_properties()
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        for module in (PARTS, GROUPS):
            module.unregister_properties()
        for item in reversed(cls.registered):
            if item.__dict__.get('bl_rna'):
                bpy.utils.unregister_class(item)

    def setUp(self):
        if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        mesh = bpy.data.meshes.new('Parts independence mesh')
        mesh.from_pydata([(column * .17, row * .23, (row + column) * .013)
                          for row in range(3) for column in range(3)], [],
                         [(row * 3 + column, row * 3 + column + 1,
                           (row + 1) * 3 + column + 1, (row + 1) * 3 + column)
                          for row in range(2) for column in range(2)])
        self.obj = bpy.data.objects.new('Parts independence object', mesh)
        self.obj_name = self.obj.name
        bpy.context.scene.collection.objects.link(self.obj)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj
        # Blender reserves attribute names for deform groups too; deliberately
        # distinct paint names avoid native auto-renaming the seeded catalogs.
        for number, name in enumerate(('shin.L', 'foot.L', 'Artist paint', 'Paint strap', 'Paint skirt')):
            group = self.obj.vertex_groups.new(name=name)
            for vertex in mesh.vertices:
                group.add([vertex.index], (vertex.index + number + 1) / 23., 'REPLACE')
            group.lock_weight = number != 1
        self.obj.vertex_groups.active_index = 1
        self.obj.shape_key_add(name='Basis')
        key = self.obj.shape_key_add(name='Artist shoe correction')
        key.data[4].co.z += .021
        key.value, key.vertex_group = .37, 'Artist paint'
        self.obj.active_shape_key_index = 0
        for name, indices in ((CUSTOM_NAMES[0], {0, 1}), (CUSTOM_NAMES[1], {2, 3}),
                              (CUSTOM_NAMES[2], {4}), (PART_NAME, {1, 4, 5})):
            attr = mesh.attributes.new(name, 'INT', 'POINT')
            for index, item in enumerate(attr.data):
                item.value = int(index in indices)
        artist = mesh.attributes.new('Artist point', 'FLOAT', 'POINT')
        for index, item in enumerate(artist.data):
            item.value = index / 13.
        self.obj.location = (.3, -.1, .7)
        self.obj['Artist note'], mesh['Artist note'] = 'Keep paint', 'Keep topology'
        GROUPS.set_locked(bpy.context, CUSTOM_NAMES[1], True)
        PARTS.set_locked(bpy.context, PART_NAME, True)
        self.edit()
        self.authored = author_state(self.obj)

    def tearDown(self):
        obj = bpy.data.objects.get(self.obj_name)
        if obj is not None:
            if obj.mode != 'OBJECT':
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='OBJECT')
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)

    def edit(self):
        if self.obj.mode != 'EDIT':
            self.assertEqual(bpy.ops.object.mode_set(mode='EDIT'), {'FINISHED'})

    def object(self):
        if self.obj.mode != 'OBJECT':
            self.assertEqual(bpy.ops.object.mode_set(mode='OBJECT'), {'FINISHED'})

    def activate(self, name):
        self.obj.data.attributes.active_index = self.obj.data.attributes.find(name)

    def choose(self, indices):
        bpy.context.tool_settings.mesh_select_mode = (True, False, False)
        bm = bm_for(self.obj)
        bm.select_mode = {'VERT'}
        for domain in (bm.faces, bm.edges, bm.verts):
            for item in domain:
                item.select_set(False)
        for index in indices:
            self.assertFalse(bm.verts[index].hide, 'Test cannot select a hidden vertex')
            bm.verts[index].select_set(True)
        bm.select_flush_mode()
        bm.select_history.clear()
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)

    def assert_no_hidden_selection(self):
        bm = bm_for(self.obj)
        invalid = [(domain, index)
                   for domain, elements in (('POINT', bm.verts), ('EDGE', bm.edges), ('FACE', bm.faces))
                   for index, element in enumerate(elements) if element.hide and element.select]
        self.assertEqual(invalid, [], 'Hidden mesh elements must not remain selected')

    def assert_authored(self):
        self.assertEqual(author_state(self.obj), self.authored)

    def assert_cancelled(self, operator, **kwargs):
        try:
            result = operator(**kwargs)
        except RuntimeError:
            return
        self.assertEqual(result, {'CANCELLED'})

    def test_existing_straps_and_part_catalogs_are_independent(self):
        self.assertEqual([attr.name for attr in GROUPS.group_attributes(self.obj.data)], list(CUSTOM_NAMES))
        self.assertEqual([attr.name for attr in PARTS.part_attributes(self.obj.data)], [PART_NAME])
        self.assertEqual(members(self.obj, PART_NAME), {1, 4, 5})
        self.assertTrue(GROUPS.is_locked(self.obj.data.attributes[CUSTOM_NAMES[1]]))
        self.assertTrue(PARTS.is_locked(self.obj.data.attributes[PART_NAME]))
        self.choose({6, 7})
        before = namespace_state(self.obj, 'Custom_', '.cdvg_lock_')
        self.assertEqual(bpy.ops.character_designer.vertex_part_new(name='Boot'), {'FINISHED'})
        self.assertEqual(members(self.obj, 'Part_Boot'), {6, 7})
        self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), before)
        self.assertNotIn('Custom_Boot', self.obj.data.attributes)
        self.assert_authored()

    def test_assign_remove_and_clear_touch_only_their_own_members(self):
        before_parts = namespace_state(self.obj, 'Part_', '.cdpart_lock_')
        self.activate(CUSTOM_NAMES[0])
        self.choose({6})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
        self.assertEqual(members(self.obj, CUSTOM_NAMES[0]), {0, 1, 6})
        self.choose({0})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_remove(), {'FINISHED'})
        self.assertEqual(members(self.obj, CUSTOM_NAMES[0]), {1, 6})
        self.assertEqual(namespace_state(self.obj, 'Part_', '.cdpart_lock_'), before_parts)
        before_custom = namespace_state(self.obj, 'Custom_', '.cdvg_lock_')
        PARTS.set_locked(bpy.context, PART_NAME, False)
        self.activate(PART_NAME)
        self.choose({7})
        self.assertEqual(bpy.ops.character_designer.vertex_part_assign(), {'FINISHED'})
        self.assertEqual(members(self.obj, PART_NAME), {1, 4, 5, 7})
        self.assertEqual(bpy.ops.character_designer.vertex_part_action(action='CLEAR'), {'FINISHED'})
        self.assertEqual(members(self.obj, PART_NAME), set())
        self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), before_custom)
        self.assert_authored()

    def test_custom_rename_move_sort_lock_and_delete_leave_parts_exact(self):
        expected = namespace_state(self.obj, 'Part_', '.cdpart_lock_')
        self.activate(CUSTOM_NAMES[0])
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_rename(
            group_name=CUSTOM_NAMES[0], name='Cuff'), {'FINISHED'})
        self.assertEqual(members(self.obj, 'Custom_Cuff'), {0, 1})
        for action in ('SORT_NAME', 'LOCK_ALL', 'INVERT_LOCKS', 'UNLOCK_ALL'):
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action=action), {'FINISHED'})
            self.assertEqual(namespace_state(self.obj, 'Part_', '.cdpart_lock_'), expected)
        self.activate('Custom_Cuff')
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_move(direction='DOWN'), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action='DELETE_ALL'), {'FINISHED'})
        self.assertFalse(GROUPS.group_attributes(self.obj.data))
        self.assertEqual(namespace_state(self.obj, 'Part_', '.cdpart_lock_'), expected)
        self.assert_authored()

    def test_part_rename_move_sort_lock_and_delete_leave_custom_exact(self):
        expected = namespace_state(self.obj, 'Custom_', '.cdvg_lock_')
        self.choose({6, 7})
        self.assertEqual(bpy.ops.character_designer.vertex_part_new(name='Boot'), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.vertex_part_rename(part_name=PART_NAME, name='Strap1'), {'FINISHED'})
        self.assertIn('Custom_Strap1', self.obj.data.attributes)
        self.assertIn('Part_Strap1', self.obj.data.attributes)
        self.assertTrue(PARTS.is_locked(self.obj.data.attributes['Part_Strap1']))
        self.assertEqual(members(self.obj, 'Part_Strap1'), {1, 4, 5})
        for action in ('SORT_NAME', 'LOCK_ALL', 'INVERT_LOCKS', 'UNLOCK_ALL'):
            self.assertEqual(bpy.ops.character_designer.vertex_part_action(action=action), {'FINISHED'})
            self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), expected)
        self.activate('Part_Strap1')
        self.assertEqual(bpy.ops.character_designer.vertex_part_move(direction='UP'), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.vertex_part_delete(), {'FINISHED'})
        self.assertEqual(bpy.ops.character_designer.vertex_part_action(action='DELETE_ALL'), {'FINISHED'})
        self.assertFalse(PARTS.part_attributes(self.obj.data))
        self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), expected)
        self.assert_authored()

    def test_wrong_prefix_mutating_backend_and_public_calls_are_refused(self):
        for module, wrong, own, remove, swap in (
                (GROUPS, PART_NAME, CUSTOM_NAMES[0], GROUPS.delete_group, GROUPS._swap_groups),
                (PARTS, CUSTOM_NAMES[0], PART_NAME, PARTS.delete_part, PARTS._swap_parts)):
            self.activate(wrong)
            bm = bm_for(self.obj)
            wrong_lock = (PARTS._lock_name(PART_NAME) if module is GROUPS
                          else GROUPS._lock_name(CUSTOM_NAMES[1]))
            calls = (lambda: module.set_locked(bpy.context, wrong, True),
                     lambda: module._set_label(self.obj.data.attributes[wrong], 'Wrong'),
                     lambda: module._write_values(self.obj.data, wrong, [0] * 9, bm),
                     lambda: module._remove_native(self.obj.data, wrong, bm),
                     lambda: module._remove_native(self.obj.data, wrong_lock, bm),
                     lambda: swap(self.obj.data, own, wrong, bm),
                     lambda: module._view_attribute(SimpleNamespace(native_name=wrong,
                         mesh_token=module._mesh_token(self.obj.data)), bpy.context),
                     lambda: module.edit_membership(bpy.context, True),
                     lambda: remove(bpy.context))
            for call in calls:
                with self.subTest(module=module.__name__, call=repr(call)):
                    before = all_selection_state(self.obj)
                    with self.assertRaises((ValueError, RuntimeError)):
                        call()
                    self.assertEqual(all_selection_state(self.obj), before)
                    self.assert_authored()
        with self.assertRaises(ValueError):
            VISIBILITY.preflight_rename(self.obj.data, CUSTOM_NAMES[0], PART_NAME)
        public = ((bpy.ops.character_designer.custom_vertex_group_rename, {'group_name': PART_NAME, 'name': 'Wrong'}),
                  (bpy.ops.character_designer.custom_vertex_group_lock, {'group_name': PART_NAME}),
                  (bpy.ops.character_designer.custom_vertex_group_select, {'group_name': PART_NAME}),
                  (bpy.ops.character_designer.vertex_part_rename, {'part_name': CUSTOM_NAMES[0], 'name': 'Wrong'}),
                  (bpy.ops.character_designer.vertex_part_lock, {'part_name': CUSTOM_NAMES[0]}),
                  (bpy.ops.character_designer.vertex_part_select, {'part_name': CUSTOM_NAMES[0]}),
                  (bpy.ops.character_designer.vertex_part_visibility, {'part_name': CUSTOM_NAMES[0], 'action': 'HIDE'}))
        for operator, kwargs in public:
            before = all_selection_state(self.obj)
            self.assert_cancelled(operator, **kwargs)
            self.assertEqual(all_selection_state(self.obj), before)
        self.assert_authored()

    def test_independent_rows_active_memory_rename_and_menu_scope(self):
        manager = bpy.context.window_manager
        self.activate(CUSTOM_NAMES[1])
        GROUPS.sync_view(bpy.context)
        group_index = getattr(manager, GROUPS.VIEW_INDEX_PROPERTY)
        self.activate(PART_NAME)
        PARTS.sync_view(bpy.context)
        part_index = getattr(manager, PARTS.VIEW_INDEX_PROPERTY)
        active_before_sync = self.obj.data.attributes.active.name
        GROUPS.sync_view(bpy.context)
        self.assertEqual(getattr(manager, GROUPS.VIEW_INDEX_PROPERTY), group_index)
        self.assertEqual(getattr(manager, PARTS.VIEW_INDEX_PROPERTY), part_index)
        self.assertEqual(self.obj.data.attributes.active.name, active_before_sync)
        self.assertTrue(GROUPS.activate_view(bpy.context))
        self.assertEqual(self.obj.data.attributes.active.name, CUSTOM_NAMES[1])
        active_before_sync = self.obj.data.attributes.active.name
        PARTS.sync_view(bpy.context)
        self.assertEqual(getattr(manager, PARTS.VIEW_INDEX_PROPERTY), part_index)
        self.assertEqual(getattr(manager, GROUPS.VIEW_INDEX_PROPERTY), group_index)
        self.assertEqual(self.obj.data.attributes.active.name, active_before_sync)
        self.assertTrue(PARTS.activate_view(bpy.context))
        self.assertEqual(self.obj.data.attributes.active.name, PART_NAME)
        group_rows = getattr(manager, GROUPS.VIEW_PROPERTY)
        part_rows = getattr(manager, PARTS.VIEW_PROPERTY)
        self.assertEqual([row.native_name for row in group_rows], list(CUSTOM_NAMES))
        self.assertEqual([row.native_name for row in part_rows], [PART_NAME])
        before = namespace_state(self.obj, 'Custom_', '.cdvg_lock_')
        part_rows[0].name = 'Dress'
        self.assertIn('Part_Dress', self.obj.data.attributes)
        self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), before)
        for menu, allowed in ((GROUPS.CHARACTERDESIGNER_MT_custom_vertex_groups,
                               'character_designer.custom_vertex_group_action'),
                              (PARTS.CHARACTERDESIGNER_MT_vertex_parts, 'character_designer.vertex_part_action')):
            recorder = LayoutRecorder()
            menu.draw(SimpleNamespace(layout=recorder), bpy.context)
            self.assertTrue(recorder.operators)
            self.assertTrue(all(item.identifier == allowed for item in recorder.operators))
        self.assert_authored()

    def test_legacy_and_part_requests_share_one_baseline_with_scoped_restores(self):
        bm = bm_for(self.obj)
        self.choose(range(len(bm.verts)))
        bm.verts[8].hide_set(True)
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        baseline = visibility_state(self.obj)
        custom = namespace_state(self.obj, 'Custom_', '.cdvg_lock_')
        part = namespace_state(self.obj, 'Part_', '.cdpart_lock_')
        VISIBILITY.toggle_hidden(bpy.context, CUSTOM_NAMES[0])
        self.assert_no_hidden_selection()
        self.assertEqual(bpy.ops.character_designer.vertex_part_visibility(part_name=PART_NAME, action='HIDE'), {'FINISHED'})
        self.assert_no_hidden_selection()
        self.assertTrue(VISIBILITY.has_legacy_visibility(self.obj.data))
        self.assertEqual({index for index, hidden in enumerate(visibility_state(self.obj)[0]) if hidden}, {0, 1, 4, 5, 8})
        self.assertEqual(bpy.ops.character_designer.vertex_part_visibility(part_name=PART_NAME, action='SOLO'), {'FINISHED'})
        self.assert_no_hidden_selection()
        self.assertEqual({index for index, hidden in enumerate(visibility_state(self.obj)[0]) if hidden}, {0, 2, 3, 6, 7, 8})
        state_names = {name for name in VISIBILITY.owned_attribute_names(self.obj.data)
                       if name.startswith(VISIBILITY.STATE_PREFIX)}
        self.assertEqual(state_names, set(VISIBILITY.STATE_NAMES.values()))
        VISIBILITY.reset_parts_visibility(bpy.context)
        self.assert_no_hidden_selection()
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, CUSTOM_NAMES[0]))
        self.assertFalse(VISIBILITY.is_hidden(self.obj.data, PART_NAME))
        self.assertFalse(VISIBILITY.is_solo(self.obj.data, PART_NAME))
        self.assertEqual({i for i, hidden in enumerate(visibility_state(self.obj)[0]) if hidden}, {0, 1, 8})
        VISIBILITY.toggle_hidden(bpy.context, PART_NAME)
        self.assert_no_hidden_selection()
        VISIBILITY.reset_legacy_visibility(bpy.context)
        self.assert_no_hidden_selection()
        self.assertFalse(VISIBILITY.has_legacy_visibility(self.obj.data))
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, PART_NAME))
        self.assertEqual({i for i, hidden in enumerate(visibility_state(self.obj)[0]) if hidden}, {1, 4, 5, 8})
        VISIBILITY.reset_parts_visibility(bpy.context)
        self.assert_no_hidden_selection()
        self.assertEqual(visibility_state(self.obj), baseline)
        self.assertFalse(VISIBILITY.owned_attribute_names(self.obj.data))
        self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), custom)
        self.assertEqual(namespace_state(self.obj, 'Part_', '.cdpart_lock_'), part)
        self.assert_authored()

    def test_custom_assign_without_legacy_does_not_refresh_part_overlay(self):
        VISIBILITY.toggle_hidden(bpy.context, PART_NAME)
        self.assertFalse(VISIBILITY.has_legacy_visibility(self.obj.data))
        self.activate(CUSTOM_NAMES[0])
        self.choose({6})
        before = overlay_state(self.obj)
        with mock.patch.object(VISIBILITY, 'refresh_custom', wraps=VISIBILITY.refresh_custom) as refresh_spy:
            with mock.patch.object(VISIBILITY, '_apply', side_effect=AssertionError('Custom Assign refreshed a Parts-only overlay')):
                self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
            self.assertEqual(refresh_spy.call_count, 1)
        self.assertEqual(overlay_state(self.obj), before)
        self.assertEqual(members(self.obj, CUSTOM_NAMES[0]), {0, 1, 6})
        self.assert_authored()

    def test_part_assign_and_clear_without_part_requests_keep_legacy_overlay_exact(self):
        PARTS.set_locked(bpy.context, PART_NAME, False)
        VISIBILITY.toggle_hidden(bpy.context, CUSTOM_NAMES[0])
        self.assertTrue(VISIBILITY.has_legacy_visibility(self.obj.data))
        self.assertFalse(VISIBILITY.is_hidden(self.obj.data, PART_NAME))
        self.assertFalse(VISIBILITY.is_solo(self.obj.data, PART_NAME))
        self.activate(PART_NAME)
        self.choose({6})
        before_overlay = overlay_state(self.obj)
        before_custom = namespace_state(self.obj, 'Custom_', '.cdvg_lock_')
        with mock.patch.object(VISIBILITY, 'refresh_parts', wraps=VISIBILITY.refresh_parts) as refresh_spy:
            with mock.patch.object(VISIBILITY, '_apply', side_effect=AssertionError('Parts edit refreshed a legacy-only overlay')):
                self.assertEqual(bpy.ops.character_designer.vertex_part_assign(), {'FINISHED'})
                self.assertEqual(members(self.obj, PART_NAME), {1, 4, 5, 6})
                self.assertEqual(overlay_state(self.obj), before_overlay)
                self.assertEqual(bpy.ops.character_designer.vertex_part_action(action='CLEAR'), {'FINISHED'})
                self.assertEqual(members(self.obj, PART_NAME), set())
            self.assertEqual(refresh_spy.call_count, 2)
        self.assertEqual(overlay_state(self.obj), before_overlay)
        self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), before_custom)
        self.assert_authored()

    def test_rename_delete_markers_follow_only_their_own_catalog(self):
        VISIBILITY.toggle_hidden(bpy.context, CUSTOM_NAMES[0])
        VISIBILITY.toggle_hidden(bpy.context, PART_NAME)
        part_before = namespace_state(self.obj, 'Part_', '.cdpart_lock_')
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_rename(
            group_name=CUSTOM_NAMES[0], name='Cuff'), {'FINISHED'})
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, 'Custom_Cuff'))
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, PART_NAME))
        self.assertEqual(namespace_state(self.obj, 'Part_', '.cdpart_lock_'), part_before)
        self.assertEqual(bpy.ops.character_designer.vertex_part_rename(part_name=PART_NAME, name='Dress'), {'FINISHED'})
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, 'Part_Dress'))
        self.assertTrue(PARTS.is_locked(self.obj.data.attributes['Part_Dress']))
        self.activate('Custom_Cuff')
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertFalse(VISIBILITY.has_legacy_visibility(self.obj.data))
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, 'Part_Dress'))
        self.activate('Part_Dress')
        self.assertEqual(bpy.ops.character_designer.vertex_part_delete(), {'FINISHED'})
        self.assertFalse(VISIBILITY.owned_attribute_names(self.obj.data))
        self.assert_authored()

    def test_save_reopen_preserves_independent_names_members_locks_and_authored_data(self):
        self.object()
        expected_custom = namespace_state(self.obj, 'Custom_', '.cdvg_lock_')
        expected_part = namespace_state(self.obj, 'Part_', '.cdpart_lock_')
        expected_author = author_state(self.obj)
        directory = OPTIONS.report.parent if OPTIONS.report else Path(tempfile.gettempdir())
        directory.mkdir(parents=True, exist_ok=True)
        case = Path(tempfile.mkdtemp(prefix='parts-save-reopen-', dir=directory))
        path = case / 'parts_independence.blend'
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False, compress=True), {'FINISHED'})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=True), {'FINISHED'})
        self.obj = bpy.data.objects[self.obj_name]
        bpy.context.view_layer.objects.active = self.obj
        self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), expected_custom)
        self.assertEqual(namespace_state(self.obj, 'Part_', '.cdpart_lock_'), expected_part)
        self.assertEqual(author_state(self.obj), expected_author)
        self.assertTrue(GROUPS.is_locked(self.obj.data.attributes[CUSTOM_NAMES[1]]))
        self.assertTrue(PARTS.is_locked(self.obj.data.attributes[PART_NAME]))
        GROUPS.sync_view(bpy.context)
        PARTS.sync_view(bpy.context)
        self.assertEqual([row.native_name for row in getattr(bpy.context.window_manager, GROUPS.VIEW_PROPERTY)], list(CUSTOM_NAMES))
        self.assertEqual([row.native_name for row in getattr(bpy.context.window_manager, PARTS.VIEW_PROPERTY)], [PART_NAME])


@unittest.skipIf(bpy.app.background, 'Native Undo/Redo needs the separately launched GUI event loop.')
class PartHistoryTests(unittest.TestCase):
    """Reuse the tiny fixture without re-running the core suite in the GUI.

    The root runner calls load_feature_modules(source_root), sets OPTIONS with
    its report path, then loads this class once its native event loop is active.
    Main below deliberately continues to load only PartsIndependenceTests.
    """
    setUpClass = classmethod(PartsIndependenceTests.setUpClass.__func__)
    tearDownClass = classmethod(PartsIndependenceTests.tearDownClass.__func__)
    tearDown = PartsIndependenceTests.tearDown
    edit = PartsIndependenceTests.edit
    object = PartsIndependenceTests.object
    activate = PartsIndependenceTests.activate
    choose = PartsIndependenceTests.choose
    assert_authored = PartsIndependenceTests.assert_authored

    def setUp(self):
        PartsIndependenceTests.setUp(self)
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True
        self.addCleanup(setattr, bpy.context.preferences.edit, 'use_global_undo', previous)
        self.custom_authority = namespace_state(self.obj, 'Custom_', '.cdvg_lock_')

    def current(self):
        # Native Edit Undo can replace the mesh/object wrappers. Never keep a
        # BMesh, attribute, row, or object reference across an Undo/Redo call.
        self.obj = bpy.data.objects[self.obj_name]
        self.assertEqual(self.obj.mode, 'EDIT')
        bm = bm_for(self.obj)
        layers = []
        for domain, elements in (('POINT', bm.verts), ('EDGE', bm.edges), ('FACE', bm.faces)):
            for name in elements.layers.int.keys():
                layer = elements.layers.int[name]
                layers.append((domain, name, tuple(element[layer] for element in elements)))
        # A pure native INT-layer swap already demonstrates that Edit Undo
        # restores the BMesh layers but not Mesh.attributes.active_index:
        # X/validation/independent_mesh_parts_20261019/candidate02/native_characterization.json.
        # Exclude only that Mesh-side name from history equality. Core isolation
        # tests still compare the complete all_selection_state, including it.
        return {'selection_catalogs_and_visibility': all_selection_state(self.obj)[:-1],
                'all_native_integer_layers': tuple(sorted(layers)),
                'authored_inputs': author_state(self.obj), 'mode': bpy.context.mode}

    def assert_authority(self):
        self.assertEqual(namespace_state(self.obj, 'Custom_', '.cdvg_lock_'), self.custom_authority)
        self.assert_authored()

    def history_step(self, label, operation):
        self.assertEqual(bpy.ops.ed.undo_push(message='Before Parts ' + label), {'FINISHED'})
        before = self.current()
        self.assertEqual(operation(), {'FINISHED'})
        self.assertEqual(bpy.ops.ed.undo_push(message='After Parts ' + label), {'FINISHED'})
        after = self.current()
        self.assertNotEqual(after, before, label + ' did not change native feature state')
        self.assert_authority()
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        self.assertEqual(self.current(), before, label + ' Undo changed native state')
        self.assert_authority()
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        self.assertEqual(self.current(), after, label + ' Redo changed native state')
        self.assert_authority()

    def test_part_create_members_rename_lock_move_delete_undo_redo(self):
        self.choose({6, 7})
        self.history_step('create', lambda: bpy.ops.character_designer.vertex_part_new(name='Boot'))
        self.assertEqual(members(self.obj, 'Part_Boot'), {6, 7})
        self.activate('Part_Boot')
        self.choose({8})
        self.history_step('assign', lambda: bpy.ops.character_designer.vertex_part_assign())
        self.assertEqual(members(self.obj, 'Part_Boot'), {6, 7, 8})
        self.choose({6})
        self.history_step('remove', lambda: bpy.ops.character_designer.vertex_part_remove())
        self.assertEqual(members(self.obj, 'Part_Boot'), {7, 8})
        self.history_step('rename', lambda: bpy.ops.character_designer.vertex_part_rename(
            part_name='Part_Boot', name='Panel'))
        self.assertEqual(members(self.obj, 'Part_Panel'), {7, 8})
        self.history_step('lock', lambda: bpy.ops.character_designer.vertex_part_lock(part_name='Part_Panel'))
        self.assertTrue(PARTS.is_locked(self.obj.data.attributes['Part_Panel']))
        self.activate('Part_Panel')
        self.history_step('move', lambda: bpy.ops.character_designer.vertex_part_move(direction='UP'))
        self.assertEqual(PARTS.part_attributes(self.obj.data)[0].name, 'Part_Panel')
        self.history_step('delete', lambda: bpy.ops.character_designer.vertex_part_delete())
        self.assertNotIn('Part_Panel', self.obj.data.attributes)
        self.assertEqual([attr.name for attr in PARTS.part_attributes(self.obj.data)], [PART_NAME])

    def test_mixed_legacy_part_hide_solo_and_scoped_resets_undo_redo(self):
        bm = bm_for(self.obj)
        bm.verts[8].hide_set(True)
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        manual_baseline = visibility_state(self.obj)
        VISIBILITY.toggle_hidden(bpy.context, CUSTOM_NAMES[0])
        self.history_step('hide', lambda: bpy.ops.character_designer.vertex_part_visibility(
            part_name=PART_NAME, action='HIDE'))
        self.history_step('solo', lambda: bpy.ops.character_designer.vertex_part_visibility(
            part_name=PART_NAME, action='SOLO'))
        self.history_step('reset only Parts', lambda: bpy.ops.character_designer.vertex_part_visibility(action='RESET'))
        self.assertTrue(VISIBILITY.has_legacy_visibility(self.obj.data))
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, CUSTOM_NAMES[0]))
        self.assertFalse(VISIBILITY.is_hidden(self.obj.data, PART_NAME))
        self.assertFalse(VISIBILITY.is_solo(self.obj.data, PART_NAME))
        self.history_step('hide while legacy remains', lambda: bpy.ops.character_designer.vertex_part_visibility(
            part_name=PART_NAME, action='HIDE'))
        self.history_step('reset only legacy', lambda: bpy.ops.character_designer.vertex_part_visibility(action='RESET_LEGACY'))
        self.assertFalse(VISIBILITY.has_legacy_visibility(self.obj.data))
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, PART_NAME))
        self.history_step('reset final Parts requests', lambda: bpy.ops.character_designer.vertex_part_visibility(action='RESET'))
        self.assertEqual(visibility_state(self.obj), manual_baseline)
        self.assertFalse(VISIBILITY.owned_attribute_names(self.obj.data))

    def test_actual_inline_rna_rename_pushes_one_native_undo_step(self):
        self.activate(PART_NAME)
        VISIBILITY.toggle_hidden(bpy.context, PART_NAME)
        PARTS.sync_view(bpy.context)
        rows = getattr(bpy.context.window_manager, PARTS.VIEW_PROPERTY)
        row = next(item for item in rows if item.native_name == PART_NAME)
        self.assertEqual(row.bl_rna.properties['name'].type, 'STRING')
        self.assertEqual(row.name, 'Skirt')
        self.assertEqual(bpy.ops.ed.undo_push(message='Before actual Parts row RNA rename'), {'FINISHED'})
        before = self.current()
        # No After marker: the callback's own UNDO operator must provide the
        # history entry. An extra undo_push would hide a broken RNA callback.
        row.name = 'Panel 鞋'
        renamed = 'Part_Panel 鞋'
        after = self.current()
        self.assertNotEqual(after, before)
        self.assertIn(renamed, self.obj.data.attributes)
        self.assertTrue(PARTS.is_locked(self.obj.data.attributes[renamed]))
        self.assertTrue(VISIBILITY.is_hidden(self.obj.data, renamed))
        self.assert_authority()
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        self.assertEqual(self.current(), before)
        self.assert_authority()
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        self.assertEqual(self.current(), after)
        self.assert_authority()
        PARTS.sync_view(bpy.context)
        rows = getattr(bpy.context.window_manager, PARTS.VIEW_PROPERTY)
        self.assertEqual(next(item for item in rows if item.native_name == renamed).name, 'Panel 鞋')


def load_feature_modules(source_root):
    """Load the same isolated feature package for background or GUI runners."""
    global GROUPS, PARTS, VISIBILITY, PACKAGE
    source = Path(source_root).resolve(strict=True)
    PACKAGE = source if (source / 'custom_vertex_groups.py').is_file() else source / 'character_designer'
    if not (PACKAGE / 'custom_vertex_parts.py').is_file():
        raise RuntimeError('The requested source root does not contain the Parts module.')
    if 'character_designer' in sys.modules:
        raise RuntimeError('Run in a fresh process without the full Character Designer already loaded.')
    package = ModuleType('character_designer')
    package.__path__ = [str(PACKAGE)]
    sys.modules['character_designer'] = package
    GROUPS = importlib.import_module('character_designer.custom_vertex_groups')
    PARTS = importlib.import_module('character_designer.custom_vertex_parts')
    VISIBILITY = importlib.import_module('character_designer.custom_vertex_visibility')
    return GROUPS, PARTS, VISIBILITY


def main():
    global OPTIONS, GROUPS, PARTS, VISIBILITY, PACKAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--source-root', type=Path, default=ROOT / 'addons')
    OPTIONS = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    if not os.environ.get('BLENDER_USER_CONFIG'):
        raise RuntimeError('Use the trusted launcher with its dedicated isolated CONFIG.')
    load_feature_modules(OPTIONS.source_root)
    report = {'schema': 'character-designer.parts-independence.native.v1',
              'recorded_at': datetime.now(timezone.utc).isoformat(), 'blender': bpy.app.version_string,
              'build_hash': bpy.app.build_hash.decode('ascii', errors='replace'),
              'source_root': str(PACKAGE),
              'source_sha256': {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                                for module in (GROUPS, PARTS, VISIBILITY)},
              'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'isolated_config': os.environ['BLENDER_USER_CONFIG'],
              'autoexec_failed': bpy.app.autoexec_fail,
              'scene_scope': 'Generated 9-vertex fixture only; saved/reopened disposable blend with use_scripts=True.',
              'history_scope': 'No native Undo/Redo claim; separately verified through GUI.',
              'registration_scope': 'Only Group/Parts classes and properties; full addon init not executed.'}
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PartsIndependenceTests))
    report.update({'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
                   'failures': [{'test': str(test), 'traceback': detail} for test, detail in result.failures],
                   'errors': [{'test': str(test), 'traceback': detail} for test, detail in result.errors],
                   'skipped': [{'test': str(test), 'reason': reason} for test, reason in result.skipped]})
    if OPTIONS.report:
        OPTIONS.report.parent.mkdir(parents=True, exist_ok=True)
        OPTIONS.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('CD_PARTS_INDEPENDENCE_NATIVE ' + json.dumps(report, ensure_ascii=False), flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
