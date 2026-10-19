"""Native mesh-part visibility preserves authored mesh data and Edit history.

Run only in disposable processes through run_trusted_blender_validation.py.
Legacy Custom_ visibility remains supported beside the independent Part_ catalog. These fixtures never
open the artist scene, change preferences on disk, or launch Blender themselves.
"""
from pathlib import Path
import sys
import tempfile
import unittest

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_custom_vertex_groups_blender as base
from character_designer import custom_vertex_groups as groups
from character_designer import custom_vertex_parts as parts
from character_designer import custom_vertex_visibility as visibility


HIDE_ATTRIBUTES = {'.hide_vert', '.hide_edge', '.hide_poly'}


def bm_for(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    for elements in (bm.verts, bm.edges, bm.faces):
        elements.ensure_lookup_table()
        elements.index_update()
    return bm


def visibility_state(obj):
    bm = bm_for(obj)
    return tuple(tuple(bool(element.hide) for element in elements)
                 for elements in (bm.verts, bm.edges, bm.faces))


def hidden_vertices(obj):
    return {index for index, hidden in enumerate(visibility_state(obj)[0]) if hidden}


def choose(obj, indices):
    """Select visible vertices without revealing the artist's hidden geometry."""
    bm = bm_for(obj)
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    bm.select_mode = {'VERT'}
    for elements in (bm.faces, bm.edges, bm.verts):
        for element in elements:
            element.select_set(False)
    for index in indices:
        if bm.verts[index].hide:
            raise AssertionError('Test attempted to select a hidden vertex.')
        bm.verts[index].select_set(True)
    bm.select_history.clear()
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def author_state(obj):
    """Exclude exact native visibility outputs and authenticated owned metadata."""
    result = base.protected(obj)
    excluded = HIDE_ATTRIBUTES | set(visibility.owned_attribute_names(obj.data))
    excluded.update(item.name for item in parts.part_attributes(obj.data))
    result['attributes'] = tuple(row for row in result['attributes'] if row[0] not in excluded)
    return result


def live_author_state(obj):
    result = base.bmesh_protected(obj)
    excluded = HIDE_ATTRIBUTES | set(visibility.owned_attribute_names(obj.data))
    excluded.update(item.name for item in parts.part_attributes(obj.data))
    for domain in ('VERT', 'EDGE', 'FACE', 'LOOP'):
        result[domain] = tuple(row for row in result[domain] if row[1] not in excluded)
    return result


def native_state(obj):
    """All integer layers and visibility, including the feature's own state."""
    bm = bm_for(obj)
    rows = []
    for domain, elements in (('VERT', bm.verts), ('EDGE', bm.edges), ('FACE', bm.faces)):
        rows.append((domain, tuple(sorted((name, tuple(element[elements.layers.int[name]]
            for element in elements)) for name in elements.layers.int.keys()))))
    return {'visibility': visibility_state(obj), 'integer_layers': tuple(rows),
            'author': base.bmesh_protected(obj)}


def coordinate_values(obj, name):
    if obj.mode == 'EDIT':
        bm = bm_for(obj)
        layer = bm.verts.layers.int[name]
        return tuple(sorted((tuple(vertex.co), vertex[layer]) for vertex in bm.verts))
    attribute = obj.data.attributes[name]
    return tuple(sorted((tuple(vertex.co), item.value)
                        for vertex, item in zip(obj.data.vertices, attribute.data)))


def hidden_coordinates(obj):
    return {tuple(vertex.co) for vertex in bm_for(obj).verts if vertex.hide}


def native_visibility(obj, state):
    """Give a control fixture identical native flags before the same mesh edit."""
    bm = bm_for(obj)
    for elements, values in zip((bm.verts, bm.edges, bm.faces), state):
        if len(elements) != len(values):
            raise AssertionError('Native reference topology changed before the edit.')
        for element, hidden in zip(elements, values):
            element.hide = hidden
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


class _VisibilityFixture(base._CustomVertexGroupFixture):
    def make_groups(self, definitions=(('Skirt', {0, 1, 4}), ('Straps', {1, 2, 5}))):
        names = [self.create(label).attribute_name for label, _ in definitions]
        self.edit()
        for index, (_, members) in enumerate(definitions):
            base.activate_group(self.obj, index)
            choose(self.obj, members)
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
        choose(self.obj, set())
        return names

    def assert_no_hidden_selection(self):
        bm = bm_for(self.obj)
        self.assertFalse(any(element.hide and element.select
                             for elements in (bm.verts, bm.edges, bm.faces) for element in elements))

    def copied_control(self):
        self.object()
        control = self.obj.copy()
        control.data = self.obj.data.copy()
        control.name = 'Native edit reference'
        bpy.context.scene.collection.objects.link(control)
        base.activate(self.obj)
        self.edit()
        return control


class CustomVertexVisibilityTests(_VisibilityFixture):
    def test_eye_overlap_uses_same_catalog_without_changing_authored_inputs(self):
        original = author_state(self.obj)
        first, second = self.make_groups()
        membership = base.semantic_members(self.obj)
        live = live_author_state(self.obj)
        initial = visibility_state(self.obj)
        visibility.refresh(bpy.context)
        self.assertFalse(visibility.owned_attribute_names(self.obj.data))
        visibility.toggle_hidden(bpy.context, first)
        self.assertTrue(visibility.is_hidden(self.obj.data, first))
        self.assertEqual(hidden_vertices(self.obj), {0, 1, 4})
        self.assert_no_hidden_selection()
        visibility.toggle_hidden(bpy.context, second)
        self.assertEqual(hidden_vertices(self.obj), {0, 1, 2, 4, 5})
        visibility.toggle_hidden(bpy.context, first)
        self.assertEqual(hidden_vertices(self.obj), {1, 2, 5})
        self.assertFalse(visibility.is_hidden(self.obj.data, first))
        visibility.toggle_hidden(bpy.context, second)
        self.assertEqual(visibility_state(self.obj), initial)
        self.assertEqual(base.semantic_members(self.obj), membership)
        self.assertEqual(live_author_state(self.obj), live)
        self.object()
        self.assertEqual(author_state(self.obj), original)

    def test_multiple_solo_stars_union_and_restore_eye_and_manual_baseline(self):
        first, second = self.make_groups()
        bm = bm_for(self.obj)
        bm.verts[1].hide_set(True)
        bm.verts[8].hide_set(True)
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        baseline = visibility_state(self.obj)
        live = live_author_state(self.obj)
        visibility.toggle_hidden(bpy.context, first)
        eye_state = visibility_state(self.obj)
        visibility.toggle_solo(bpy.context, first)
        self.assertTrue(visibility.is_solo(self.obj.data, first))
        self.assertEqual(hidden_vertices(self.obj), set(range(9)) - {0, 4})
        visibility.toggle_solo(bpy.context, second)
        self.assertEqual(hidden_vertices(self.obj), set(range(9)) - {0, 2, 4, 5})
        visibility.toggle_solo(bpy.context, first)
        self.assertFalse(visibility.is_solo(self.obj.data, first))
        self.assertEqual(hidden_vertices(self.obj), set(range(9)) - {2, 5})
        visibility.toggle_solo(bpy.context, second)
        self.assertEqual(visibility_state(self.obj), eye_state)
        visibility.reset_visibility(bpy.context)
        self.assertEqual(visibility_state(self.obj), baseline)
        self.assertFalse(visibility.is_hidden(self.obj.data, first))
        self.assertFalse(visibility.is_solo(self.obj.data, second))
        self.assertFalse(visibility.owned_attribute_names(self.obj.data))
        self.assertEqual(live_author_state(self.obj), live)

    def test_preexisting_hidden_edges_faces_and_vertices_restore_exactly(self):
        first, second = self.make_groups()
        bm = bm_for(self.obj)
        bm.verts[8].hide_set(True)
        bm.edges[0].hide_set(True)
        bm.faces[0].hide_set(True)
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        baseline = visibility_state(self.obj)
        self.assertTrue(any(baseline[1]))
        self.assertTrue(any(baseline[2]))
        live = live_author_state(self.obj)
        visibility.toggle_solo(bpy.context, first)
        visibility.toggle_hidden(bpy.context, second)
        visibility.toggle_solo(bpy.context, second)
        visibility.reset_visibility(bpy.context)
        self.assertEqual(visibility_state(self.obj), baseline)
        self.assertEqual(live_author_state(self.obj), live)
        self.assert_no_hidden_selection()

    def test_native_hide_and_reveal_are_merged_at_next_explicit_operation(self):
        first, _ = self.make_groups()
        live = live_author_state(self.obj)
        visibility.toggle_hidden(bpy.context, first)
        choose(self.obj, {8})
        self.assertEqual(bpy.ops.mesh.hide(unselected=False), {'FINISHED'})
        visibility.refresh(bpy.context)
        self.assertEqual(hidden_vertices(self.obj), {0, 1, 4, 8})
        visibility.toggle_hidden(bpy.context, first)
        self.assertEqual(hidden_vertices(self.obj), {8})
        visibility.toggle_hidden(bpy.context, first)
        self.assertEqual(bpy.ops.mesh.reveal(select=False), {'FINISHED'})
        self.assertEqual(hidden_vertices(self.obj), set())
        visibility.refresh(bpy.context)
        self.assertEqual(hidden_vertices(self.obj), {0, 1, 4})
        visibility.toggle_hidden(bpy.context, first)
        self.assertEqual(hidden_vertices(self.obj), set())
        self.assertEqual(live_author_state(self.obj), live)

    def test_lock_preserves_membership_while_allowing_visibility_and_selection(self):
        first, _ = self.make_groups()
        base.activate_group(self.obj, 0)
        groups.set_locked(bpy.context, first, True)
        before = base.semantic_members(self.obj)
        live = live_author_state(self.obj)
        choose(self.obj, {3})
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_assign)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_remove)
        self.assert_refused(bpy.ops.character_designer.custom_vertex_group_action, action='CLEAR')
        visibility.toggle_hidden(bpy.context, first)
        self.assertEqual(hidden_vertices(self.obj), {0, 1, 4})
        visibility.toggle_hidden(bpy.context, first)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(), {'FINISHED'})
        self.assertEqual(base.selected(self.obj), {0, 1, 4})
        visibility.toggle_solo(bpy.context, first)
        visibility.reset_visibility(bpy.context)
        self.assertEqual(base.semantic_members(self.obj), before)
        self.assertEqual(live_author_state(self.obj), live)

    def test_public_rename_and_delete_keep_visibility_markers_with_native_groups(self):
        first, second = self.make_groups()
        baseline = visibility_state(self.obj)
        live = live_author_state(self.obj)
        visibility.toggle_hidden(bpy.context, first)
        visibility.toggle_solo(bpy.context, second)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_rename(
            'EXEC_DEFAULT', True, group_name=first, name='裙子 配件'), {'FINISHED'})
        renamed_first = groups.PREFIX + '裙子 配件'
        self.assertTrue(visibility.is_hidden(self.obj.data, renamed_first))
        self.assertEqual(base.members(self.obj, 0), {0, 1, 4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_rename(
            'EXEC_DEFAULT', True, group_name=second, name='All straps'), {'FINISHED'})
        renamed_second = groups.PREFIX + 'All straps'
        self.assertTrue(visibility.is_solo(self.obj.data, renamed_second))
        base.activate_group(self.obj, 1)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertEqual(hidden_vertices(self.obj), {0, 1, 4})
        base.activate_group(self.obj, 0)
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
        self.assertEqual(visibility_state(self.obj), baseline)
        self.assertFalse(visibility.owned_attribute_names(self.obj.data))
        self.assertEqual(live_author_state(self.obj), live)

    def test_assign_remove_and_clear_refresh_active_overlay_immediately(self):
        first, _ = self.make_groups()
        base.activate_group(self.obj, 0)
        live = live_author_state(self.obj)
        visibility.toggle_hidden(bpy.context, first)
        choose(self.obj, {3})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_assign(), {'FINISHED'})
        self.assertEqual(hidden_vertices(self.obj), {0, 1, 3, 4})
        visibility.toggle_hidden(bpy.context, first)
        visibility.toggle_solo(bpy.context, first)
        choose(self.obj, {4})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_remove(), {'FINISHED'})
        self.assertEqual(base.members(self.obj, 0), {0, 1, 3})
        self.assertEqual(hidden_vertices(self.obj), set(range(9)) - {0, 1, 3})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_action(action='CLEAR'), {'FINISHED'})
        self.assertEqual(base.members(self.obj, 0), set())
        self.assertEqual(hidden_vertices(self.obj), set(range(9)))
        visibility.reset_visibility(bpy.context)
        self.assertEqual(hidden_vertices(self.obj), set())
        self.assertEqual(live_author_state(self.obj), live)

    def test_native_delete_reindexes_members_and_visibility_without_stale_indices(self):
        first, _ = self.make_groups()
        control = self.copied_control()
        visibility.toggle_hidden(bpy.context, first)
        native_before = visibility_state(self.obj)
        choose(self.obj, {3})
        self.assertEqual(bpy.ops.mesh.delete(type='VERT'), {'FINISHED'})
        visibility.refresh(bpy.context)
        actual_values = coordinate_values(self.obj, first)
        self.assertEqual(hidden_coordinates(self.obj), {co for co, value in actual_values if value})
        self.object()
        actual_author = author_state(self.obj)
        base.activate(control)
        self.assertEqual(bpy.ops.object.mode_set(mode='EDIT'), {'FINISHED'})
        native_visibility(control, native_before)
        choose(control, {3})
        self.assertEqual(bpy.ops.mesh.delete(type='VERT'), {'FINISHED'})
        self.assertEqual(coordinate_values(control, first), actual_values)
        self.assertEqual(bpy.ops.object.mode_set(mode='OBJECT'), {'FINISHED'})
        self.assertEqual(author_state(control), actual_author)

    def test_native_subdivide_propagation_matches_equivalent_native_reference(self):
        first, _ = self.make_groups()
        control = self.copied_control()
        visibility.toggle_hidden(bpy.context, first)
        native_before = visibility_state(self.obj)
        choose(self.obj, {6, 7})
        self.assertEqual(bpy.ops.mesh.subdivide(number_cuts=1), {'FINISHED'})
        visibility.refresh(bpy.context)
        self.assertGreater(len(bm_for(self.obj).verts), 9)
        actual_values = coordinate_values(self.obj, first)
        self.assertEqual(hidden_coordinates(self.obj), {co for co, value in actual_values if value})
        self.object()
        actual_author = author_state(self.obj)
        base.activate(control)
        self.assertEqual(bpy.ops.object.mode_set(mode='EDIT'), {'FINISHED'})
        native_visibility(control, native_before)
        choose(control, {6, 7})
        self.assertEqual(bpy.ops.mesh.subdivide(number_cuts=1), {'FINISHED'})
        self.assertEqual(coordinate_values(control, first), actual_values)
        self.assertEqual(bpy.ops.object.mode_set(mode='OBJECT'), {'FINISHED'})
        self.assertEqual(author_state(control), actual_author)

    def test_shared_mesh_multi_edit_and_metadata_collision_refuse_without_writes(self):
        first, _ = self.make_groups()
        self.object()
        shared = bpy.data.objects.new('Shared mesh visibility refusal', self.obj.data)
        bpy.context.scene.collection.objects.link(shared)
        self.edit()
        before = native_state(self.obj)
        for operation in (visibility.toggle_hidden, visibility.toggle_solo):
            with self.assertRaises((ValueError, RuntimeError)):
                operation(bpy.context, first)
            self.assertEqual(native_state(self.obj), before)
        self.object()
        bpy.data.objects.remove(shared, do_unlink=True)
        other = base.fixture('Other mesh visibility refusal')
        base.activate(self.obj)
        other.select_set(True)
        self.edit()
        before = native_state(self.obj), native_state(other)
        for operation in (visibility.toggle_hidden, visibility.toggle_solo):
            with self.assertRaises((ValueError, RuntimeError)):
                operation(bpy.context, first)
            self.assertEqual((native_state(self.obj), native_state(other)), before)
        self.object()
        base.activate(self.obj)
        # The namespace is reserved, but a wrong-typed collision is foreign
        # data and must never be removed, converted, or silently claimed.
        import hashlib
        collision_name = '.cdvg_hidden_' + hashlib.sha256(first.encode('utf-8')).hexdigest()[:20]
        self.obj.data.attributes.new(collision_name, 'FLOAT', 'POINT')
        self.edit()
        before = native_state(self.obj)
        for operation in (visibility.toggle_hidden, visibility.toggle_solo):
            with self.assertRaises((ValueError, RuntimeError)):
                operation(bpy.context, first)
            self.assertEqual(native_state(self.obj), before)

    def test_native_save_reopen_keeps_catalog_stars_eyes_and_manual_baseline(self):
        first, second = self.make_groups()
        bm_for(self.obj).verts[8].hide_set(True)
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        baseline = visibility_state(self.obj)
        visibility.toggle_hidden(bpy.context, first)
        visibility.toggle_solo(bpy.context, second)
        expected_visibility = visibility_state(self.obj)
        self.object()
        expected_author = author_state(self.obj)
        expected_values = coordinate_values(self.obj, first), coordinate_values(self.obj, second)
        object_name = self.obj.name
        with tempfile.TemporaryDirectory(prefix='cd-custom-visibility-save-') as folder:
            path = str(Path(folder) / 'parts_visibility.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path, use_scripts=True), {'FINISHED'})
            self.obj = bpy.data.objects[object_name]
            self.assertEqual(author_state(self.obj), expected_author)
            self.assertEqual((coordinate_values(self.obj, first), coordinate_values(self.obj, second)), expected_values)
            self.assertTrue(visibility.is_hidden(self.obj.data, first))
            self.assertTrue(visibility.is_solo(self.obj.data, second))
            base.activate(self.obj)
            self.edit()
            self.assertEqual(visibility_state(self.obj), expected_visibility)
            visibility.reset_visibility(bpy.context)
            self.assertEqual(visibility_state(self.obj), baseline)
            self.object()
            self.assertEqual(author_state(self.obj), expected_author)

    def test_new_part_from_selection_assigns_visible_members_in_independent_catalog(self):
        original = author_state(self.obj)
        existing = self.create('Existing group').attribute_name
        self.edit()
        choose(self.obj, {0, 4, 8})
        bm_for(self.obj).verts[8].hide_set(True)
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        before_visibility = visibility_state(self.obj)
        live = live_author_state(self.obj)
        self.assertTrue(bpy.ops.character_designer.custom_vertex_part_new.poll())
        self.assertEqual(bpy.ops.character_designer.custom_vertex_part_new(), {'FINISHED'})
        first_part = base.active_name(self.obj)
        self.assertEqual(first_part, parts.PREFIX + 'Part')
        self.assertEqual([row.attribute_name for row in base.registry(self.obj)], [existing])
        self.assertEqual(base.members(self.obj, 0), set())
        self.assertEqual({i for i, value in enumerate(parts._values(self.obj.data, first_part, bm_for(self.obj))) if value}, {0, 4})
        self.assertEqual(visibility_state(self.obj), before_visibility)
        choose(self.obj, {2, 5})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_part_new(), {'FINISHED'})
        second_part = parts.PREFIX + 'Part.001'
        self.assertEqual(base.active_name(self.obj), second_part)
        self.assertEqual({i for i, value in enumerate(parts._values(self.obj.data, first_part, bm_for(self.obj))) if value}, {0, 4})
        self.assertEqual({i for i, value in enumerate(parts._values(self.obj.data, second_part, bm_for(self.obj))) if value}, {2, 5})
        self.assertEqual(live_author_state(self.obj), live)
        self.object()
        self.assertEqual(author_state(self.obj), original)

    def test_new_part_empty_selection_refuses_without_creating_group_and_modes_poll(self):
        original = base.feature_state(self.obj)
        original_author = author_state(self.obj)
        for operator in (bpy.ops.character_designer.custom_vertex_part_new,
                         bpy.ops.character_designer.custom_vertex_group_select,
                         bpy.ops.character_designer.custom_vertex_group_visibility):
            self.assertFalse(operator.poll())
        self.assertTrue(bpy.ops.character_designer.custom_vertex_group_new.poll())
        self.assert_refused(bpy.ops.character_designer.custom_vertex_part_new)
        self.assertEqual(base.feature_state(self.obj), original)
        self.edit()
        choose(self.obj, set())
        before = native_state(self.obj)
        for operator in (bpy.ops.character_designer.custom_vertex_part_new,
                         bpy.ops.character_designer.custom_vertex_group_select,
                         bpy.ops.character_designer.custom_vertex_group_visibility):
            self.assertTrue(operator.poll())
        self.assertEqual(bpy.ops.character_designer.custom_vertex_part_new(), {'CANCELLED'})
        self.assertEqual(base.registry(self.obj), [])
        self.assertEqual(native_state(self.obj), before)
        self.object()
        self.assertEqual(author_state(self.obj), original_author)

    def test_extend_select_union_keeps_existing_visible_selection_and_manual_hides(self):
        first, second = self.make_groups()
        bm_for(self.obj).verts[1].hide_set(True)
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
        before_visibility = visibility_state(self.obj)
        membership = base.semantic_members(self.obj)
        live = live_author_state(self.obj)
        choose(self.obj, {3, 8})
        base.activate_group(self.obj, 0)
        groups.select_group(bpy.context, extend=True)
        self.assertEqual(base.selected(self.obj), {0, 3, 4, 8})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(
            group_name=second, extend=True), {'FINISHED'})
        self.assertEqual(base.selected(self.obj), {0, 2, 3, 4, 5, 8})
        self.assertEqual(bpy.ops.character_designer.custom_vertex_group_select(
            group_name=first, extend=False), {'FINISHED'})
        self.assertEqual(base.selected(self.obj), {0, 4})
        self.assertEqual(visibility_state(self.obj), before_visibility)
        self.assertEqual(base.semantic_members(self.obj), membership)
        self.assertEqual(live_author_state(self.obj), live)

    def test_public_rename_failure_restores_membership_lock_and_visibility_names(self):
        from unittest import mock

        first, _ = self.make_groups()
        groups.set_locked(bpy.context, first, True)
        visibility.toggle_hidden(bpy.context, first)
        visibility.toggle_solo(bpy.context, first)
        before = native_state(self.obj)
        with mock.patch.object(visibility, 'rename_state',
                               side_effect=RuntimeError('injected visibility rename failure')):
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_rename(
                'EXEC_DEFAULT', True, group_name=first, name='Failed renamed part'), {'CANCELLED'})
        self.assertEqual(native_state(self.obj), before)
        self.assertIn(first, base.group_names(self.obj))
        self.assertNotIn(groups.PREFIX + 'Failed renamed part', base.group_names(self.obj))
        self.assertEqual(base.members(self.obj, 0), {0, 1, 4})
        self.assertTrue(groups.is_locked(self.obj.data.attributes[first]))
        self.assertTrue(visibility.is_hidden(self.obj.data, first))
        self.assertTrue(visibility.is_solo(self.obj.data, first))
        # Original names remain usable after cancellation, rather than merely
        # looking correct in a snapshot while leaving orphan marker metadata.
        visibility.toggle_hidden(bpy.context, first)
        visibility.toggle_solo(bpy.context, first)
        self.assertFalse(visibility.owned_attribute_names(self.obj.data))
        self.assertTrue(groups.is_locked(self.obj.data.attributes[first]))

    def test_native_hide_adjacent_to_overlay_restores_complete_native_graph(self):
        first, _ = self.make_groups()
        control = self.copied_control()
        # Vertex 4 in this part already hides the (4, 5, 8, 7) face.  Native H
        # on adjacent visible vertex 8 must still become a complete manual
        # baseline, including edges/faces that were already hidden by 4.
        visibility.toggle_hidden(bpy.context, first)
        choose(self.obj, {8})
        self.assertEqual(bpy.ops.mesh.hide(unselected=False), {'FINISHED'})
        visibility.reset_visibility(bpy.context)
        restored_graph = visibility_state(self.obj)
        self.assertFalse(visibility.owned_attribute_names(self.obj.data))
        self.object()
        restored_author = author_state(self.obj)

        base.activate(control)
        self.assertEqual(bpy.ops.object.mode_set(mode='EDIT'), {'FINISHED'})
        choose(control, {8})
        self.assertEqual(bpy.ops.mesh.hide(unselected=False), {'FINISHED'})
        native_manual_graph = visibility_state(control)
        self.assertEqual(restored_graph, native_manual_graph)
        choose(control, {0, 1, 4})
        self.assertEqual(bpy.ops.mesh.hide(unselected=False), {'FINISHED'})
        native_overlay_graph = visibility_state(control)
        self.assertEqual(bpy.ops.object.mode_set(mode='OBJECT'), {'FINISHED'})
        self.assertEqual(author_state(control), restored_author)

        base.activate(self.obj)
        self.edit()
        visibility.toggle_hidden(bpy.context, first)
        self.assertEqual(visibility_state(self.obj), native_overlay_graph)
        visibility.toggle_hidden(bpy.context, first)
        self.assertEqual(visibility_state(self.obj), native_manual_graph)
        self.assert_no_hidden_selection()


class CustomVertexVisibilityHistoryTests(_VisibilityFixture):
    """Root runs these through an isolated native GUI event loop."""

    @unittest.skipIf(bpy.app.background, 'Native Edit history requires the separate disposable GUI launch.')
    def test_public_eye_and_union_solo_undo_redo_restore_native_visibility_state(self):
        first, second = self.make_groups()
        obj_name = self.obj.name
        original = live_author_state(self.obj)
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True

        def current():
            self.obj = bpy.data.objects[obj_name]
            self.assertEqual(self.obj.mode, 'EDIT')
            return native_state(self.obj)

        def history_step(label, operation):
            self.assertEqual(bpy.ops.ed.undo_push(message='Before ' + label), {'FINISHED'})
            before = current()
            self.assertEqual(operation(), {'FINISHED'})
            self.assertEqual(bpy.ops.ed.undo_push(message='After ' + label), {'FINISHED'})
            after = current()
            self.assertNotEqual(after, before)
            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.assertEqual(current(), before)
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.assertEqual(current(), after)
            self.assertEqual(live_author_state(self.obj), original)

        try:
            history_step('eye hide', lambda: bpy.ops.character_designer.custom_vertex_group_visibility(
                group_name=first, action='HIDE'))
            history_step('first solo', lambda: bpy.ops.character_designer.custom_vertex_group_visibility(
                group_name=first, action='SOLO'))
            history_step('second union solo', lambda: bpy.ops.character_designer.custom_vertex_group_visibility(
                group_name=second, action='SOLO'))
            history_step('visibility reset', lambda: bpy.ops.character_designer.custom_vertex_group_visibility(
                action='RESET'))
        finally:
            bpy.context.preferences.edit.use_global_undo = previous

    @unittest.skipIf(bpy.app.background, 'Native Edit history requires the separate disposable GUI launch.')
    def test_delete_active_solo_undo_redo_restores_group_markers_members_and_author_inputs(self):
        first, second = self.make_groups()
        visibility.toggle_hidden(bpy.context, first)
        visibility.toggle_solo(bpy.context, second)
        base.activate_group(self.obj, 1)
        obj_name = self.obj.name
        original = live_author_state(self.obj)
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True

        def current():
            self.obj = bpy.data.objects[obj_name]
            self.assertEqual(self.obj.mode, 'EDIT')
            return native_state(self.obj)

        try:
            self.assertEqual(bpy.ops.ed.undo_push(message='Before active solo group delete'), {'FINISHED'})
            before = current()
            self.assertEqual(bpy.ops.character_designer.custom_vertex_group_delete(), {'FINISHED'})
            self.assertEqual(bpy.ops.ed.undo_push(message='After active solo group delete'), {'FINISHED'})
            after = current()
            self.assertEqual(hidden_vertices(self.obj), {0, 1, 4})
            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            self.assertEqual(current(), before)
            self.assertTrue(visibility.is_solo(self.obj.data, second))
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            self.assertEqual(current(), after)
            self.assertEqual(live_author_state(self.obj), original)
        finally:
            bpy.context.preferences.edit.use_global_undo = previous


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CustomVertexVisibilityTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
