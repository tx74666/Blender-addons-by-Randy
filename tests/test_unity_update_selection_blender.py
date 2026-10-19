"""Native mesh/rig update-intent tests in a disposable Blender process.

Launch with tools/run_trusted_blender_validation.py and pass --addon-parent and
--report after Blender's --. Only the new module and legacy export PropertyGroups
are registered manually; the complete Character Designer lifecycle is not run.
These tests verify native callback Undo/Redo in Object and Pose modes, without
claiming visual drag paint, gesture grouping, selective Unity publication, or
GUI layout acceptance. No artist file or Unity project opens.
"""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import bmesh
import bpy


ROOT = Path(__file__).resolve().parents[1]
SUBJECT = EXPORTER = EXPORT_UI = SETUP = OPTIONS = None
SELECTION_ATTRIBUTES = {'.select_vert', '.select_edge', '.select_poly',
                        '.uv_select_vert', '.uv_select_edge', '.uv_select_face'}


def frozen(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bpy.types.ID):
        return (type(value).__name__, value.as_pointer(), value.name)
    if hasattr(value, 'items'):
        return tuple(sorted((str(name), frozen(item)) for name, item in value.items()))
    return tuple(frozen(item) for item in value)


def rig(name):
    data = bpy.data.armatures.new(name + 'Data')
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    hips = data.edit_bones.new('Hips')
    hips.head, hips.tail = (0, 0, 0), (0, 0, .5)
    hand = data.edit_bones.new('Hand')
    hand.head, hand.tail, hand.parent = (0, 0, .5), (0, 0, 1), hips
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)
    obj['artist_note'] = 'preserve rig metadata'
    return obj


def mesh(name, arm=None, *, authored=False):
    data = bpy.data.meshes.new(name + 'Data')
    data.from_pydata([(0, 0, 0), (1, 0, .1), (1, 1, .2), (0, 1, 0)], [], [(0, 1, 2, 3)])
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    if arm is not None:
        group = obj.vertex_groups.new(name='Hips')
        group.add([0, 1], .625, 'REPLACE')
        group.lock_weight = True
        obj.vertex_groups.new(name='Hand').add([2, 3], .875, 'REPLACE')
        obj.modifiers.new('Artist Armature', 'ARMATURE').object = arm
    if authored:
        obj.shape_key_add(name='Basis')
        key = obj.shape_key_add(name='Artist Bend')
        key.data[2].co.z += .25
        key.value = .375
        uv = data.uv_layers.new(name='Artist UV')
        for index, item in enumerate(uv.data):
            item.uv = (index / 4.0, (index % 2) / 2.0)
        color = data.color_attributes.new(name='Artist Color', type='FLOAT_COLOR', domain='POINT')
        for index, item in enumerate(color.data):
            item.color = (index / 4.0, .25, .75, 1)
        custom = data.attributes.new('Custom_Strap', 'INT', 'POINT')
        for index, item in enumerate(custom.data):
            item.value = index % 2
        foreign = data.attributes.new('Artist Float', 'FLOAT', 'POINT')
        for index, item in enumerate(foreign.data):
            item.value = index * .125
        obj.modifiers.new('Artist Mirror', 'MIRROR').use_clip = True
    obj['artist_note'] = 'preserve mesh metadata'
    return obj


def attribute_state(data):
    result = []
    for attribute in data.attributes:
        if attribute.name in SELECTION_ATTRIBUTES:
            continue
        cells = []
        for cell in attribute.data:
            fields = []
            for prop in cell.bl_rna.properties:
                if prop.identifier == 'rna_type' or prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
                    continue
                fields.append((prop.identifier, frozen(getattr(cell, prop.identifier))))
            cells.append(tuple(fields))
        result.append((attribute.name, attribute.domain, attribute.data_type, tuple(cells)))
    return tuple(result)


def edit_state(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    vertices, edges, faces = tuple(bm.verts), tuple(bm.edges), tuple(bm.faces)
    vi, ei, fi = ({item: index for index, item in enumerate(elements)} for elements in (vertices, edges, faces))
    deform = bm.verts.layers.deform.active
    history = tuple((type(item).__name__, (vi if isinstance(item, bmesh.types.BMVert)
                                         else ei if isinstance(item, bmesh.types.BMEdge) else fi)[item])
                    for item in bm.select_history)
    uv = tuple((name, tuple((tuple(loop[bm.loops.layers.uv[name]].uv), loop[bm.loops.layers.uv[name]].pin_uv)
                           for face in faces for loop in face.loops)) for name in bm.loops.layers.uv.keys())
    colors = tuple((name, tuple(tuple(item[bm.verts.layers.float_color[name]]) for item in vertices))
                   for name in bm.verts.layers.float_color.keys())
    integers = tuple((name, tuple(item[bm.verts.layers.int[name]] for item in vertices))
                     for name in bm.verts.layers.int.keys())
    floats = tuple((name, tuple(item[bm.verts.layers.float[name]] for item in vertices))
                   for name in bm.verts.layers.float.keys())
    return (tuple((tuple(item.co), item.select, item.hide,
                   tuple(sorted(item[deform].items())) if deform is not None else ()) for item in vertices),
            tuple((tuple(vi[v] for v in item.verts), item.select, item.hide) for item in edges),
            tuple((tuple(vi[v] for v in item.verts), item.select, item.hide) for item in faces),
            history, fi.get(bm.faces.active), uv, colors, integers, floats)


def protected_state():
    result = {'filepath': bpy.data.filepath, 'mode': bpy.context.mode,
              'active': frozen(bpy.context.view_layer.objects.active),
              'selected': tuple(sorted(item.as_pointer() for item in bpy.context.selected_objects)),
              'frame': bpy.context.scene.frame_current,
              'mesh_select_mode': tuple(bpy.context.scene.tool_settings.mesh_select_mode),
              'scene': bpy.context.scene.as_pointer(),
              'window': bpy.context.window.as_pointer() if bpy.context.window else None,
              'area': bpy.context.area.as_pointer() if bpy.context.area else None,
              'region': bpy.context.region.as_pointer() if bpy.context.region else None,
              'objects': []}
    for obj in sorted(bpy.data.objects, key=lambda item: item.as_pointer()):
        state = {'identity': frozen(obj), 'data': frozen(obj.data), 'parent': frozen(obj.parent),
                 'matrix': tuple(value for row in obj.matrix_world for value in row),
                 'hidden': (obj.hide_get(), obj.hide_viewport, obj.hide_render),
                 'note': obj.get('artist_note'),
                 'modifiers': tuple((item.name, item.type, item.show_viewport, item.show_render,
                                     frozen(getattr(item, 'object', None))) for item in obj.modifiers)}
        if obj.type == 'MESH':
            state.update(groups=tuple((item.name, item.lock_weight) for item in obj.vertex_groups),
                         active_group=obj.vertex_groups.active_index,
                         vertices=tuple((tuple(item.co), item.select, item.hide,
                                         tuple((weight.group, weight.weight) for weight in item.groups))
                                        for item in obj.data.vertices),
                         edges=tuple((tuple(item.vertices), item.select, item.hide) for item in obj.data.edges),
                         faces=tuple((tuple(item.vertices), item.select, item.hide) for item in obj.data.polygons),
                         attributes=attribute_state(obj.data),
                         keys=tuple((key.name, key.value, key.mute, key.relative_key.name,
                                     tuple(tuple(item.co) for item in key.data))
                                    for key in obj.data.shape_keys.key_blocks) if obj.data.shape_keys else None,
                         edit=edit_state(obj) if obj.mode == 'EDIT' else None)
        if obj.type == 'ARMATURE':
            state.update(bones=tuple((item.name, item.parent.name if item.parent else None,
                                      tuple(value for row in item.matrix_local for value in row), item.use_deform)
                                     for item in obj.data.bones),
                         pose=tuple((item.name, tuple(value for row in item.matrix_basis for value in row))
                                    for item in obj.pose.bones), position=obj.data.pose_position)
        result['objects'].append(state)
    return result


class DrawRecorder:
    def __init__(self, labels=None, *, parent=None):
        self.labels = [] if labels is None else labels
        self.parent = parent
        self.controls = [] if parent is None else parent.controls
        self.operator_records = [] if parent is None else parent.operator_records
        self.enabled = True

    def effective_enabled(self):
        return self.enabled and (self.parent is None or self.parent.effective_enabled())

    def row(self, **kwargs):
        return type(self)(self.labels, parent=self)

    def column(self, **kwargs):
        return type(self)(self.labels, parent=self)

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))

    def operator(self, *args, **kwargs):
        self.controls.append(('operator', args[0], self.effective_enabled()))
        self.operator_records.append((args[0], kwargs))
        return SimpleNamespace()

    def prop(self, owner, name, **kwargs):
        self.controls.append(('property', name, self.effective_enabled()))
        getattr(owner, name)  # Exercise the native proxy getter as drawing does.

    def separator(self, **kwargs):
        pass


class UnityUpdateSelectionTests(unittest.TestCase):
    def setUp(self):
        self.ui = SUBJECT
        getattr(bpy.context.window_manager, self.ui.VIEW_PROPERTY).clear()
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in tuple(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.rig = rig('MainRig')
        self.body = mesh('Body', self.rig, authored=True)
        self.hair = mesh('Hair', self.rig)
        self.dress = mesh('Skirt', self.rig)
        self.other_rig = rig('OtherRig')
        self.other_body = mesh('OtherBody', self.other_rig)
        self.unbound = mesh('UnboundAccessory')
        self.preferred = mock.patch.object(SETUP, 'preferred_rig', return_value=self.rig).start()
        self.addCleanup(mock.patch.stopall)
        self.config = self.rig.character_designer_unity_export
        self.config.directory = 'artist-preserved-directory'
        self.config.filename, self.config.asset_id = 'ArtistSavedName', 'artist-profile'
        first = self.config.extras.add()
        first.object, first.enabled = self.hair, False
        second = self.config.extras.add()
        second.object, second.enabled = self.unbound, True
        self.legacy = self.legacy_state()
        self.eligible = (self.body, self.hair, self.dress)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.body.select_set(True)
        bpy.context.view_layer.objects.active = self.body
        directory = OPTIONS.report.parent if OPTIONS.report else Path(tempfile.gettempdir())
        self.temporary = tempfile.TemporaryDirectory(prefix='unity_update_selection_', dir=directory)
        self.addCleanup(self.temporary.cleanup)

    def tearDown(self):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        getattr(bpy.context.window_manager, self.ui.VIEW_PROPERTY).clear()
        for obj in tuple(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def legacy_state(self):
        config = self.rig.character_designer_unity_export
        return (config.directory, config.filename, config.asset_id,
                tuple((frozen(entry.object), entry.enabled) for entry in config.extras))

    def intent(self, owner=None):
        owner = owner or self.rig
        if not owner.is_property_set(self.ui.SETTINGS_PROPERTY):
            # Reading the PointerProperty first would create precisely the
            # persistent settings whose absence these tests must preserve.
            return {'property_set': False}
        settings = getattr(owner, self.ui.SETTINGS_PROPERTY)
        return {'property_set': True,
                'keys': tuple(sorted(settings.keys())),
                'meshes_property_set': settings.is_property_set('meshes'),
                'meshes': tuple((frozen(entry.object), entry.is_property_set('object'),
                                 bool(entry.selected), entry.is_property_set('selected'),
                                 tuple(sorted(entry.keys()))) for entry in settings.meshes),
                'flags': tuple((name, bool(getattr(settings, name)), settings.is_property_set(name))
                               for name in ('update_rig', 'show_meshes', 'show_rig'))}

    def choices(self, owner=None):
        return getattr(owner or self.rig, self.ui.SETTINGS_PROPERTY)

    def read(self, owner=None, eligible=None):
        return self.ui.get_selection(bpy.context, owner or self.rig, eligible)

    def set(self, selected, *, objects=None, include_rig=False, owner=None):
        return self.ui.set_selection(bpy.context, owner or self.rig, selected=selected,
                                     objects=objects, include_rig=include_rig)

    def assert_legacy(self):
        self.assertEqual(self.legacy_state(), self.legacy)

    def assert_rejected(self, call):
        before, intent = protected_state(), self.intent()
        with self.assertRaises((ValueError, RuntimeError, ReferenceError, TypeError)):
            call()
        self.assertEqual(protected_state(), before)
        self.assertEqual(self.intent(), intent)
        self.assert_legacy()

    def native_set(self, *, target='OBJECT', selected=False, obj=None, **pins):
        values = {'target': target, 'selected': selected, 'rig_name': self.rig.name,
                  'rig_token': self.ui._token(self.rig),
                  'scene_token': self.ui._token(bpy.context.scene),
                  'object_name': obj.name if obj else '', 'object_token': self.ui._token(obj)}
        values.update(pins)
        return bpy.ops.character_designer.unity_update_selection_set(**values)

    def test_default_read_and_draw_do_not_materialize_persistent_settings(self):
        before = frozen(self.rig)
        id_properties = frozen(dict(self.rig.items()))
        self.assertFalse(self.rig.is_property_set(self.ui.SETTINGS_PROPERTY))
        native_intent = self.intent()
        scene = protected_state()
        selection = self.read()
        self.assertEqual(set(selection['meshes']), set(self.eligible))
        self.assertTrue(selection['update_rig'])
        self.assertEqual(frozen(dict(self.rig.items())), id_properties,
                         'Reading defaults allocated persistent update settings')
        self.assertEqual(self.intent(), native_intent,
                         'Reading defaults materialized the native settings PointerProperty')
        layout = DrawRecorder()
        self.ui.draw_selection(layout, bpy.context, self.rig, selection['eligible'])
        self.assertEqual(frozen(dict(self.rig.items())), id_properties,
                         'Drawing defaults allocated persistent update settings')
        self.assertEqual(self.intent(), native_intent,
                         'Drawing defaults materialized the native settings PointerProperty')
        self.assertEqual(frozen(self.rig), before)
        self.assertEqual(protected_state(), scene)
        self.assert_legacy()
        self.assertIn('Selective export is not enabled yet.', layout.labels)

    def test_direct_native_false_choices_are_read_back_without_default_fallback(self):
        settings = self.choices()
        entry = settings.meshes.add()
        entry.object, entry.selected = self.body, False
        settings.update_rig = False
        self.assertTrue(self.rig.is_property_set(self.ui.SETTINGS_PROPERTY))
        before = self.intent()
        selection = self.read()
        self.assertEqual(set(selection['meshes']), {self.hair, self.dress})
        self.assertFalse(selection['update_rig'])
        self.assertEqual(self.ui.selection_counts(self.rig, self.eligible), (2, 3, True))
        self.assertEqual(self.intent(), before)
        self.assert_legacy()

    def test_mesh_and_rig_all_none_mixed_are_independent(self):
        self.set(False)
        selection = self.read()
        self.assertEqual(selection['meshes'], ())
        self.assertTrue(selection['update_rig'])
        self.assertEqual(self.ui.selection_counts(self.rig, self.eligible), (0, 3, False))
        self.set(False, objects=(), include_rig=True)
        self.assertFalse(self.read()['update_rig'])
        self.set(True, objects=(self.body,))
        self.assertEqual(self.read()['meshes'], (self.body,))
        self.assertEqual(self.ui.selection_counts(self.rig, self.eligible), (1, 3, True))
        self.assertFalse(self.read()['update_rig'])
        layout = DrawRecorder()
        self.ui.draw_selection(layout, bpy.context, self.rig, self.read()['eligible'])
        headers = [kwargs for identifier, kwargs in layout.operator_records
                   if identifier == 'character_designer.unity_update_selection_expand']
        self.assertEqual([kwargs['text'] for kwargs in headers], ['Rig (1)', 'Mesh (3)'])
        self.assertTrue(all(kwargs.get('icon', 'NONE') == 'NONE' for kwargs in headers))
        self.assertFalse(any('Partial' in text or '/3' in text for text in layout.labels))
        self.set(True, include_rig=True)
        self.assertEqual(set(self.read()['meshes']), set(self.eligible))
        self.assertTrue(self.read()['update_rig'])
        self.assertEqual(self.ui.selection_counts(self.rig, self.eligible), (3, 3, False))
        self.assert_legacy()

    def test_body_can_be_unchecked_without_writing_legacy_extras(self):
        self.set(False, objects=(self.body,))
        self.assertEqual(set(self.read()['meshes']), {self.hair, self.dress})
        self.assertTrue(self.read()['update_rig'])
        self.assert_legacy()
        self.assertEqual(set(self.read()['eligible']), set(self.eligible))

    def test_duplicates_are_conservative_and_batch_synchronizes_them(self):
        settings = self.choices()
        for selected in (True, False):
            entry = settings.meshes.add()
            entry.object, entry.selected = self.body, selected
        unrelated = settings.meshes.add()
        unrelated.object, unrelated.selected = self.unbound, False
        self.assertNotIn(self.body, self.read()['meshes'])
        self.set(True, objects=(self.body, self.body))
        self.assertTrue(all(entry.selected for entry in settings.meshes if entry.object == self.body))
        self.assertEqual(len(settings.meshes), 3)
        self.assertFalse(unrelated.selected)
        self.assert_legacy()

    def test_foreign_unbound_missing_and_stale_targets_refuse_before_writes(self):
        for obj in (self.other_body, self.unbound, None):
            with self.subTest(obj=obj.name if obj else None):
                self.assert_rejected(lambda: self.set(False, objects=(self.body, obj), include_rig=True))
        self.body.modifiers['Artist Armature'].show_viewport = False
        self.assert_rejected(lambda: self.set(False, objects=(self.body,)))
        self.assert_rejected(lambda: self.read(eligible=self.eligible))
        self.assert_rejected(lambda: self.read(eligible=(self.other_body,)))
        self.assert_rejected(lambda: self.set(1, objects=(self.hair,)))

    def test_running_export_blocks_writes_but_allows_read_only_inspection(self):
        self.set(True)
        before = self.intent()
        with mock.patch.object(EXPORTER, 'export_running', return_value=True):
            self.assertEqual(set(self.read()['meshes']), set(self.eligible))
            self.assertEqual(self.intent(), before)
            self.assert_rejected(lambda: self.set(False, include_rig=True))

    def test_native_linked_rig_and_linked_mesh_data_refuse_writes(self):
        donor_rig = rig('LinkedDonorRig')
        donor_mesh = mesh('LinkedDonorMesh', donor_rig)
        library_path = Path(self.temporary.name) / 'linked_sources.blend'
        bpy.data.libraries.write(str(library_path), {donor_rig, donor_mesh})
        names = donor_rig.name, donor_mesh.name
        bpy.data.objects.remove(donor_mesh, do_unlink=True)
        bpy.data.objects.remove(donor_rig, do_unlink=True)
        with bpy.data.libraries.load(str(library_path), link=True) as (available, loaded):
            loaded.objects = list(names)
        linked_rig, linked_mesh = loaded.objects
        for obj in loaded.objects:
            bpy.context.scene.collection.objects.link(obj)
        self.assertIsNotNone(linked_rig.library)
        self.assertIsNotNone(linked_mesh.data.library)
        local = mesh('LocalObjectLinkedMeshData', self.rig)
        local.data = linked_mesh.data
        self.assert_rejected(lambda: self.set(False, objects=(local,), include_rig=True))
        with mock.patch.object(SETUP, 'preferred_rig', return_value=linked_rig):
            self.assert_rejected(lambda: self.set(False, owner=linked_rig, include_rig=True))

    def test_batch_second_append_failure_restores_existing_flags_and_length(self):
        settings = self.choices()
        entry = settings.meshes.add()
        entry.object = self.body
        self.assertNotIn('selected', entry, 'Fixture must retain an implicit default flag')
        self.assertFalse(entry.is_property_set('selected'))
        before, authored = self.intent(), protected_state()
        append = self.ui._append_mesh_choice
        count = []

        def fail_after_second_append(*args):
            entry = append(*args)
            count.append(True)
            if len(count) == 2:
                raise RuntimeError('injected failure after second native collection append')
            return entry

        with mock.patch.object(self.ui, '_append_mesh_choice', side_effect=fail_after_second_append):
            with self.assertRaises(RuntimeError):
                self.set(False, include_rig=True)
        self.assertEqual(len(count), 2)
        self.assertEqual(self.intent(), before)
        self.assertEqual(len(settings.meshes), 1)
        self.assertTrue(settings.meshes[0].selected)
        self.assertTrue(settings.update_rig)
        self.assertEqual(protected_state(), authored)
        self.assert_legacy()

    def test_failed_first_batch_does_not_leave_new_persistent_settings(self):
        before = frozen(dict(self.rig.items()))
        self.assertNotIn(self.ui.SETTINGS_PROPERTY, self.rig)
        self.assertFalse(self.rig.is_property_set(self.ui.SETTINGS_PROPERTY))
        native_intent = self.intent()
        append = self.ui._append_mesh_choice
        count = []

        def fail_after_second_append(*args):
            entry = append(*args)
            count.append(True)
            if len(count) == 2:
                raise RuntimeError('injected failure after first-use native writes')
            return entry

        with mock.patch.object(self.ui, '_append_mesh_choice', side_effect=fail_after_second_append):
            self.assert_rejected(lambda: self.set(False, include_rig=True))
        self.assertEqual(len(count), 2)
        self.assertEqual(frozen(dict(self.rig.items())), before)
        self.assertNotIn(self.ui.SETTINGS_PROPERTY, self.rig)
        self.assertFalse(self.rig.is_property_set(self.ui.SETTINGS_PROPERTY))
        self.assertEqual(self.intent(), native_intent)

    def test_edit_geometry_weights_keys_uv_colors_selection_and_context_preserved(self):
        bpy.context.view_layer.objects.active = self.body
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(self.body.data)
        bm.verts.ensure_lookup_table()
        for vertex in bm.verts:
            vertex.select_set(False)
        bm.verts[2].select_set(True)
        bm.select_history.clear()
        bm.select_history.add(bm.verts[2])
        bm.verts[0].hide_set(True)
        bmesh.update_edit_mesh(self.body.data, loop_triangles=False, destructive=False)
        before = protected_state()
        self.assertFalse(self.rig.is_property_set(self.ui.SETTINGS_PROPERTY))
        intent = self.intent()
        self.assert_rejected(lambda: self.set(False, include_rig=True))
        self.assert_rejected(lambda: self.set(True, objects=(self.hair,)))
        self.assertEqual(set(self.read()['meshes']), set(self.eligible))
        layout = DrawRecorder()
        self.ui.draw_selection(layout, bpy.context, self.rig, self.read()['eligible'])
        self.assertIn('Use Object or Pose Mode to select updates.', layout.labels)
        selection_controls = [enabled for kind, name, enabled in layout.controls
                              if kind == 'property' or name.endswith('unity_update_selection_set')]
        self.assertTrue(selection_controls)
        self.assertFalse(any(selection_controls), 'Edit Mode selection controls must be disabled')
        self.assertTrue(any(enabled for kind, name, enabled in layout.controls
                            if kind == 'operator' and name.endswith('unity_update_selection_expand')))
        self.assertEqual(self.intent(), intent)
        self.assertFalse(self.rig.is_property_set(self.ui.SETTINGS_PROPERTY))
        self.assertEqual(protected_state(), before)
        self.assert_legacy()

    def test_edit_native_operator_and_proxy_setters_refuse_without_undo_or_writes(self):
        view, rows = self.ui._sync_view(bpy.context, self.rig, self.read()['eligible'])
        row = next(item for item in rows if item.object == self.body)
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(self.body.data)
        bm.verts.ensure_lookup_table()
        for vertex in bm.verts:
            vertex.select_set(False)
        bm.verts[2].select_set(True)
        bm.select_history.clear()
        bm.select_history.add(bm.verts[2])
        bmesh.update_edit_mesh(self.body.data, loop_triangles=False, destructive=False)
        before, intent = protected_state(), self.intent()
        self.assertFalse(self.rig.is_property_set(self.ui.SETTINGS_PROPERTY))
        self.assert_rejected(lambda: self.native_set(target='ALL'))
        self.assert_rejected(lambda: self.native_set(obj=self.body))
        self.assert_rejected(lambda: self.ui._call_set(view, False, 'RIG'))
        # RNA may report callback exceptions instead of propagating them to the
        # assignment. Either path must refuse, without allocating rig settings.
        for proxy, field in ((row, 'selected'), (view, 'mesh_selected'), (view, 'rig_selected')):
            with self.subTest(field=field):
                try:
                    setattr(proxy, field, False)
                except (RuntimeError, ValueError):
                    pass
                self.assertEqual(self.intent(), intent)
                self.assertFalse(self.rig.is_property_set(self.ui.SETTINGS_PROPERTY))
                self.assertEqual(protected_state(), before)
                self.assert_legacy()

    def test_native_proxy_callbacks_change_intent_without_authoring_mesh_data(self):
        self.set(True, include_rig=True)
        view, rows = self.ui._sync_view(bpy.context, self.rig, self.read()['eligible'])
        before = protected_state()
        row = next(item for item in rows if item.object == self.body)
        row.selected = False
        self.assertNotIn(self.body, self.read()['meshes'])
        self.assertTrue(view.rig_selected)
        view.mesh_selected = False
        self.assertEqual(self.read()['meshes'], ())
        self.assertTrue(self.read()['update_rig'])
        view.rig_selected = False
        self.assertFalse(self.read()['update_rig'])
        self.assertEqual(protected_state(), before)
        self.assert_legacy()

    def test_native_operator_rejects_stale_object_scene_and_current_character_pins(self):
        self.set(True, include_rig=True)
        self.assert_rejected(lambda: self.native_set(obj=self.body, object_token='stale-object'))
        self.assert_rejected(lambda: self.native_set(obj=self.body, scene_token='stale-scene'))
        self.assert_rejected(lambda: self.native_set(obj=self.body, rig_token='stale-rig'))
        old_view, rows = self.ui._sync_view(bpy.context, self.rig, self.read()['eligible'])
        before_old, before_other = self.intent(), self.intent(self.other_rig)
        with mock.patch.object(SETUP, 'preferred_rig', return_value=self.other_rig):
            self.assert_rejected(lambda: self.set(False, include_rig=True))
            self.assert_rejected(lambda: self.native_set(target='ALL'))
            self.assert_rejected(lambda: self.ui._call_set(old_view, False, 'MESH'))
            selection = self.read(owner=self.other_rig)
            self.assertEqual(selection['meshes'], (self.other_body,))
            new_view, new_rows = self.ui._sync_view(bpy.context, self.other_rig, selection['eligible'])
            self.assertNotEqual(old_view.as_pointer(), new_view.as_pointer())
            self.assertTrue(all(row.rig == self.other_rig for row in new_rows))
        self.assertEqual(self.intent(), before_old)
        self.assertEqual(self.intent(self.other_rig), before_other)

    def test_native_undo_redo_restores_proxy_intent_in_object_and_pose_modes(self):
        # Global Undo can replace IDs. Compare every authored/context value by
        # logical identity and reacquire all RNA references after each step.
        def logical(value):
            if isinstance(value, tuple):
                if len(value) == 3 and isinstance(value[0], str) and isinstance(value[1], int) and isinstance(value[2], str):
                    return (value[0], value[2])
                return tuple(logical(item) for item in value)
            if isinstance(value, list):
                return [logical(item) for item in value]
            if isinstance(value, dict):
                return {name: logical(item) for name, item in value.items()}
            return value

        def authored():
            result = protected_state()
            result['selected'] = tuple(sorted(item.name for item in bpy.context.selected_objects))
            result['scene'] = bpy.context.scene.name
            result['objects'].sort(key=lambda item: item['identity'][2])
            return logical(result)

        def reacquire():
            for field, name in (('rig', 'MainRig'), ('body', 'Body'), ('hair', 'Hair'),
                                ('dress', 'Skirt'), ('other_rig', 'OtherRig'),
                                ('other_body', 'OtherBody'), ('unbound', 'UnboundAccessory')):
                setattr(self, field, bpy.data.objects[name])
            self.eligible = (self.body, self.hair, self.dress)

        saved_undo = bpy.context.preferences.edit.use_global_undo
        self.preferred.side_effect = lambda context: bpy.data.objects.get('MainRig')
        try:
            bpy.context.preferences.edit.use_global_undo = True
            for mode in ('OBJECT', 'POSE'):
                with self.subTest(mode=mode):
                    if bpy.context.mode != 'OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')
                    reacquire()
                    self.set(True, include_rig=True)
                    for obj in bpy.context.selected_objects:
                        obj.select_set(False)
                    active = self.body if mode == 'OBJECT' else self.rig
                    active.select_set(True)
                    bpy.context.view_layer.objects.active = active
                    if mode == 'POSE':
                        bpy.ops.object.mode_set(mode='POSE')
                    self.assertEqual(bpy.ops.ed.undo_push(message='Before Unity update checkbox'), {'FINISHED'})
                    before, before_intent = authored(), logical(self.intent())
                    legacy = logical(self.legacy_state())
                    view, rows = self.ui._sync_view(bpy.context, self.rig, self.read()['eligible'])
                    next(item for item in rows if item.object == self.body).selected = False
                    self.assertNotIn(self.body, self.read()['meshes'])
                    self.assertTrue(self.read()['update_rig'])
                    changed_intent = logical(self.intent())
                    self.assertNotEqual(changed_intent, before_intent)
                    self.assertEqual(authored(), before)
                    self.assertTrue(bpy.ops.ed.undo.poll())
                    self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
                    reacquire()
                    self.assertEqual(logical(self.intent()), before_intent)
                    self.assertEqual(authored(), before)
                    self.assertEqual(logical(self.legacy_state()), legacy)
                    self.assertTrue(bpy.ops.ed.redo.poll())
                    self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
                    reacquire()
                    self.assertEqual(logical(self.intent()), changed_intent)
                    self.assertEqual(authored(), before)
                    self.assertEqual(logical(self.legacy_state()), legacy)
        finally:
            bpy.context.preferences.edit.use_global_undo = saved_undo

    def test_rename_deleted_reference_and_reused_name_do_not_retarget_cached_rows(self):
        self.set(False, objects=(self.body,))
        view, rows = self.ui._sync_view(bpy.context, self.rig, self.read()['eligible'])
        original = next(item for item in rows if item.object == self.body)
        original_pointer, original_token = original.as_pointer(), original.object_token
        self.body.name = 'Renamed Body'
        _view, refreshed = self.ui._sync_view(bpy.context, self.rig, self.read()['eligible'])
        row = next(item for item in refreshed if item.object == self.body)
        self.assertEqual(row.as_pointer(), original_pointer)
        self.assertFalse(row.selected)
        self.assertEqual(row.object_token, original_token)
        old_name = self.body.name
        self.body = None
        obj = original.object
        bpy.data.objects.remove(obj, do_unlink=True)
        intent = self.intent()
        replacement = mesh(old_name, self.rig)
        _view, refreshed = self.ui._sync_view(bpy.context, self.rig, self.read()['eligible'])
        self.assertIsNone(original.object)
        self.assertFalse(original.present)
        new_row = next(item for item in refreshed if item.object == replacement)
        self.assertNotEqual(new_row.as_pointer(), original_pointer)
        self.assertNotEqual(new_row.object_token, original_token)
        self.assertTrue(new_row.selected)
        self.assertEqual(self.intent(), intent, 'Cache refresh rewrote saved missing references')
        self.assertTrue(any(entry.object is None and not entry.selected for entry in self.choices().meshes))
        self.assert_legacy()


def main():
    global SUBJECT, EXPORTER, EXPORT_UI, SETUP, OPTIONS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--addon-parent', type=Path, default=ROOT / 'addons')
    parser.add_argument('--report', type=Path)
    OPTIONS = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    assert not bpy.data.filepath, 'Use a disposable factory-startup process, not an artist file'
    if OPTIONS.report:
        OPTIONS.report.parent.mkdir(parents=True, exist_ok=True)
    addon_parent = OPTIONS.addon_parent.resolve(strict=True)
    sys.path.insert(0, str(addon_parent))
    SUBJECT = importlib.import_module('character_designer.unity_update_selection_ui')
    EXPORTER = importlib.import_module('character_designer.unity_export')
    EXPORT_UI = importlib.import_module('character_designer.unity_export_ui')
    SETUP = importlib.import_module('character_designer.character_setup')
    assert Path(SUBJECT.__file__).resolve().is_relative_to(addon_parent)
    legacy_classes = (EXPORT_UI.CharacterDesignerUnityExtra, EXPORT_UI.CharacterDesignerUnitySimpleMaterial,
                      EXPORT_UI.CharacterDesignerUnityMaterialMatch, EXPORT_UI.CharacterDesignerUnityExport)
    registered = []
    result = None
    try:
        for cls in (*legacy_classes, *SUBJECT.CLASSES):
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.Object.character_designer_unity_export = bpy.props.PointerProperty(type=EXPORT_UI.CharacterDesignerUnityExport)
        SUBJECT.register_properties()
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UnityUpdateSelectionTests))
    finally:
        SUBJECT.unregister_properties()
        if hasattr(bpy.types.Object, 'character_designer_unity_export'):
            del bpy.types.Object.character_designer_unity_export
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
    report = {
        'schema': 'character-designer.unity-update-selection.native-tests/1',
        'blender': bpy.app.version_string,
        'blender_build_hash': bpy.app.build_hash.decode('ascii', errors='replace'),
        'module': str(SUBJECT.__file__),
        'module_sha256': hashlib.sha256(Path(SUBJECT.__file__).read_bytes()).hexdigest(),
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'autoexec_failed': bpy.app.autoexec_fail,
        'script_auto_execution': 'Use trusted launcher --enable-autoexec',
        'isolated_config': os.environ.get('BLENDER_USER_CONFIG'),
        'artist_opened': False, 'preferences_saved': False, 'unity_project_opened': False,
        'selective_publication_validated': False,
        'gui_drag_undo_layout_validated': False,
        'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
        'failures': [{'test': str(test), 'traceback': detail} for test, detail in result.failures],
        'errors': [{'test': str(test), 'traceback': detail} for test, detail in result.errors],
        'skipped': [{'test': str(test), 'reason': reason} for test, reason in result.skipped],
    }
    if OPTIONS.report:
        OPTIONS.report.parent.mkdir(parents=True, exist_ok=True)
        OPTIONS.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('UNITY_UPDATE_SELECTION_NATIVE ' + json.dumps(report, ensure_ascii=False), flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
