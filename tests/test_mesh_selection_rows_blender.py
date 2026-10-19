"""Native regressions for independent Custom Vertex/Parts row selections.

Run through tools/run_trusted_blender_validation.py with a fresh CONFIG and
--source-root PATH --report PATH after Blender's --. Only the four feature
modules load. The tiny authored mesh fixture is imported from the independence
suite without running that suite. MeshSelectionRowsHistoryTests is a separate
GUI-event-loop suite; main() deliberately runs only the core tests.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
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
_fixture_spec = importlib.util.spec_from_file_location(
    '_cd_parts_independence_fixture', Path(__file__).with_name('test_custom_vertex_parts_independence_blender.py'))
FIXTURE = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(FIXTURE)
OPTIONS = GROUPS = PARTS = VISIBILITY = ROW_SELECTION = PACKAGE = None
BOOT = 'Part_Boot'


def load_feature_modules(source_root):
    """Fresh synthetic package, without Character Designer's full __init__."""
    global GROUPS, PARTS, VISIBILITY, ROW_SELECTION, PACKAGE
    GROUPS, PARTS, VISIBILITY = FIXTURE.load_feature_modules(source_root)
    PACKAGE = FIXTURE.PACKAGE
    ROW_SELECTION = importlib.import_module('character_designer.mesh_selection_rows')
    for module in (GROUPS, PARTS, VISIBILITY, ROW_SELECTION):
        if Path(module.__file__).resolve().parent != PACKAGE:
            raise RuntimeError('A feature module was imported from outside the requested source root.')
    return GROUPS, PARTS, VISIBILITY, ROW_SELECTION


def selected_vertices(obj):
    return {index for index, vertex in enumerate(FIXTURE.bm_for(obj).verts) if vertex.select}


def active_attribute(obj):
    return obj.data.attributes.active.name if obj.data.attributes.active else ''


class _LayoutRecorder:
    def __init__(self):
        self.operators, self.properties = [], []

    def row(self, **kwargs):
        return self

    def split(self, **kwargs):
        return self

    def prop(self, data, property, **kwargs):
        self.properties.append((data, property, kwargs))

    def label(self, **kwargs):
        pass

    def operator(self, identifier, **kwargs):
        operator = SimpleNamespace(identifier=identifier, arguments=kwargs)
        self.operators.append(operator)
        return operator


class _RowsFixture:
    setUpClass = classmethod(FIXTURE.PartsIndependenceTests.setUpClass.__func__)
    tearDownClass = classmethod(FIXTURE.PartsIndependenceTests.tearDownClass.__func__)
    tearDown = FIXTURE.PartsIndependenceTests.tearDown
    edit = FIXTURE.PartsIndependenceTests.edit
    object = FIXTURE.PartsIndependenceTests.object
    activate = FIXTURE.PartsIndependenceTests.activate
    choose = FIXTURE.PartsIndependenceTests.choose
    assert_authored = FIXTURE.PartsIndependenceTests.assert_authored
    assert_cancelled = FIXTURE.PartsIndependenceTests.assert_cancelled

    def setUp(self):
        FIXTURE.PartsIndependenceTests.setUp(self)
        self.choose({6, 7})
        self.assertEqual(bpy.ops.character_designer.vertex_part_new(name='Boot'), {'FINISHED'})
        self.assertEqual(FIXTURE.members(self.obj, BOOT), {6, 7})
        GROUPS.sync_view(bpy.context)
        PARTS.sync_view(bpy.context)
        self.initial_catalogs = self.catalogs()
        self.assert_authored()

    def cases(self):
        return (
            SimpleNamespace(module=GROUPS, names=FIXTURE.CUSTOM_NAMES[:2], other=PARTS,
                            select=bpy.ops.character_designer.custom_vertex_group_select,
                            deselect=bpy.ops.character_designer.custom_vertex_group_deselect,
                            pick=bpy.ops.character_designer.custom_vertex_group_pick,
                            pick_id='character_designer.custom_vertex_group_pick',
                            rename=bpy.ops.character_designer.custom_vertex_group_rename,
                            move=bpy.ops.character_designer.custom_vertex_group_move,
                            new=bpy.ops.character_designer.custom_vertex_group_new,
                            assign=bpy.ops.character_designer.custom_vertex_group_assign,
                            delete=bpy.ops.character_designer.custom_vertex_group_delete,
                            name_property='group_name'),
            SimpleNamespace(module=PARTS, names=(FIXTURE.PART_NAME, BOOT), other=GROUPS,
                            select=bpy.ops.character_designer.vertex_part_select,
                            deselect=bpy.ops.character_designer.vertex_part_deselect,
                            pick=bpy.ops.character_designer.vertex_part_pick,
                            pick_id='character_designer.vertex_part_pick',
                            rename=bpy.ops.character_designer.vertex_part_rename,
                            move=bpy.ops.character_designer.vertex_part_move,
                            new=bpy.ops.character_designer.vertex_part_new,
                            assign=bpy.ops.character_designer.vertex_part_assign,
                            delete=bpy.ops.character_designer.vertex_part_delete,
                            name_property='part_name'))

    def catalogs(self):
        return (FIXTURE.namespace_state(self.obj, 'Custom_', '.cdvg_lock_'),
                FIXTURE.namespace_state(self.obj, 'Part_', '.cdpart_lock_'))

    def row_state(self, module):
        return tuple((row.native_name, row.mesh_token, bool(row.selected), bool(row.anchor))
                     for row in getattr(bpy.context.window_manager, module.VIEW_PROPERTY))

    def anchor(self, module):
        return ROW_SELECTION.anchor_name(getattr(bpy.context.window_manager, module.VIEW_PROPERTY),
                                         self.obj.data, self.native_names(module))

    def native_names(self, module):
        attributes = module.group_attributes if module is GROUPS else module.part_attributes
        return [attribute.name for attribute in attributes(self.obj.data)]

    def expanded_names(self, case):
        """Five or more distinct rows, without weakening authored data checks."""
        for index in range(3):
            self.choose({index * 2})
            self.assertEqual(case.new(name='Range ' + str(index + 1)), {'FINISHED'})
            if case.module is GROUPS:  # Custom groups are deliberately created empty.
                self.assertEqual(case.assign(), {'FINISHED'})
        case.module.sync_view(bpy.context)
        self.initial_catalogs = self.catalogs()
        self.assert_authored()
        return self.native_names(case.module)

    def chosen(self, module):
        return set(module.selected_rows(bpy.context))

    def pick_both(self, case):
        case.module.pick_row(bpy.context, case.names[0])
        case.module.pick_row(bpy.context, case.names[1], extend=True)
        self.assertEqual(self.chosen(case.module), set(case.names))

    def union(self, names):
        return set().union(*(FIXTURE.members(self.obj, name) for name in names))

    def assert_inputs_unchanged(self):
        self.assertEqual(self.catalogs(), self.initial_catalogs)
        self.assert_authored()


class MeshSelectionRowsTests(_RowsFixture, unittest.TestCase):
    def test_single_pick_ctrl_toggle_and_empty_rows_are_independent(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                other = self.row_state(case.other)
                self.assertEqual(case.pick(**{case.name_property: case.names[0]}), {'FINISHED'})
                self.assertEqual(case.pick(extend=True, **{case.name_property: case.names[1]}), {'FINISHED'})
                self.assertEqual(self.chosen(case.module), set(case.names))
                case.module.pick_row(bpy.context, case.names[0], extend=True)
                self.assertEqual(self.chosen(case.module), {case.names[1]})
                case.module.pick_row(bpy.context, case.names[1], extend=True)
                self.assertEqual(self.chosen(case.module), set())
                self.choose({8})
                before = FIXTURE.all_selection_state(self.obj)
                self.assert_cancelled(case.select, use_rows=True)
                self.assert_cancelled(case.deselect, use_rows=True)
                self.assertEqual(FIXTURE.all_selection_state(self.obj), before)
                self.pick_both(case)
                case.module.pick_row(bpy.context, case.names[0])
                self.assertEqual(self.chosen(case.module), {case.names[0]})
                self.assertEqual(self.row_state(case.other), other)
                self.assert_inputs_unchanged()

    def test_shift_ranges_extend_in_both_directions_and_shrink_at_stable_anchor(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                names = self.expanded_names(case)
                other, vertices = self.row_state(case.other), selected_vertices(self.obj)
                case.module.pick_row(bpy.context, names[1])
                self.assertEqual(case.pick(range_select=True,
                    **{case.name_property: names[4]}), {'FINISHED'})
                self.assertEqual(self.chosen(case.module), set(names[1:5]))
                self.assertEqual(self.anchor(case.module), names[1])
                case.module.pick_row(bpy.context, names[2], range_select=True)
                self.assertEqual(self.chosen(case.module), set(names[1:3]))
                self.assertEqual(self.anchor(case.module), names[1])
                case.module.pick_row(bpy.context, names[0], range_select=True)
                self.assertEqual(self.chosen(case.module), set(names[:2]))
                self.assertEqual(self.anchor(case.module), names[1])
                case.module.pick_row(bpy.context, names[1], range_select=True)
                self.assertEqual(self.chosen(case.module), {names[1]})
                case.module.pick_row(bpy.context, names[4])
                case.module.pick_row(bpy.context, names[1], range_select=True)
                self.assertEqual(self.chosen(case.module), set(names[1:5]))
                self.assertEqual(self.anchor(case.module), names[4])
                self.assertEqual(selected_vertices(self.obj), vertices)
                self.assertEqual(self.row_state(case.other), other)
                self.assert_inputs_unchanged()

    def test_ctrl_disjoint_toggle_updates_anchor_and_ctrl_shift_unions_range(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                names = self.expanded_names(case)
                other = self.row_state(case.other)
                case.module.pick_row(bpy.context, names[0])
                case.module.pick_row(bpy.context, names[3], extend=True)
                self.assertEqual(self.chosen(case.module), {names[0], names[3]})
                self.assertEqual(self.anchor(case.module), names[3])
                case.module.pick_row(bpy.context, names[3], extend=True)
                self.assertEqual(self.chosen(case.module), {names[0]})
                self.assertEqual(self.anchor(case.module), names[3])
                case.module.pick_row(bpy.context, names[1], extend=True, range_select=True)
                self.assertEqual(self.chosen(case.module), set(names[:4]))
                self.assertEqual(self.anchor(case.module), names[3])
                # Shift alone now replaces that union and contracts from the same anchor.
                case.module.pick_row(bpy.context, names[2], range_select=True)
                self.assertEqual(self.chosen(case.module), set(names[2:4]))
                self.assertEqual(self.row_state(case.other), other)
                self.assert_inputs_unchanged()

    def test_filtered_sorted_range_uses_visible_order_and_rejects_hidden_target(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                names = self.expanded_names(case)
                visible = (names[4], names[0], names[2])
                case.module.pick_row(bpy.context, names[4])
                self.assertEqual(case.pick(range_select=True, visible_names=json.dumps(visible),
                    **{case.name_property: names[2]}), {'FINISHED'})
                self.assertEqual(self.chosen(case.module), set(visible))
                self.assertNotIn(names[1], self.chosen(case.module))
                self.assertNotIn(names[3], self.chosen(case.module))
                case.module.pick_row(bpy.context, names[0], range_select=True, visible_names=visible)
                self.assertEqual(self.chosen(case.module), set(visible[:2]))
                before, active = self.row_state(case.module), active_attribute(self.obj)
                with self.assertRaises(ValueError):
                    case.module.pick_row(bpy.context, names[1], range_select=True, visible_names=visible)
                self.assertEqual(self.row_state(case.module), before)
                self.assertEqual(active_attribute(self.obj), active)
                # The old anchor is filtered out: the active visible row is the fallback.
                case.module.pick_row(bpy.context, names[2], range_select=True,
                                     visible_names=(names[0], names[2]))
                self.assertEqual(self.chosen(case.module), {names[0], names[2]})
                self.assertEqual(self.anchor(case.module), names[0])
                self.assert_inputs_unchanged()

    def test_range_select_deselect_apply_union_and_preserve_painted_weights(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                names = self.expanded_names(case)
                other = self.row_state(case.other)
                case.module.pick_row(bpy.context, names[0])
                case.module.pick_row(bpy.context, names[3], range_select=True)
                targets = names[:4]
                self.assertEqual(self.chosen(case.module), set(targets))
                union = self.union(targets)
                self.assertNotIn(8, union)
                self.choose({8})
                self.assertEqual(case.select(use_rows=True, extend=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), union | {8})
                self.assertEqual(case.deselect(use_rows=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), {8})
                self.assertEqual(self.row_state(case.other), other)
                self.assert_inputs_unchanged()

    def test_renamed_reordered_anchor_retains_identity_for_next_range(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                names = self.expanded_names(case)
                other = self.row_state(case.other)
                case.module.pick_row(bpy.context, names[1])
                renamed = case.module.PREFIX + 'Range Anchor'
                values = FIXTURE.attribute_values(self.obj, names[1])
                self.assertEqual(case.rename(name='Range Anchor',
                    **{case.name_property: names[1]}), {'FINISHED'})
                case.module.sync_view(bpy.context)
                self.assertEqual(self.anchor(case.module), renamed)
                self.activate(renamed)
                self.assertEqual(case.move(direction='DOWN'), {'FINISHED'})
                case.module.sync_view(bpy.context)
                self.assertEqual(self.anchor(case.module), renamed)
                order = self.native_names(case.module)
                target = order[-1]
                case.module.pick_row(bpy.context, target, range_select=True)
                self.assertEqual(self.chosen(case.module), set(order[order.index(renamed):]))
                self.assertEqual(self.anchor(case.module), renamed)
                self.assertEqual(FIXTURE.attribute_values(self.obj, renamed), values)
                self.assertEqual(self.row_state(case.other), other)
                self.assert_authored()

    def test_deleted_anchor_falls_back_to_active_row_then_keeps_new_anchor(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                names = self.expanded_names(case)
                other = self.row_state(case.other)
                case.module.pick_row(bpy.context, names[2])
                self.assertEqual(case.delete(), {'FINISHED'})
                self.activate(names[0])
                case.module.sync_view(bpy.context)
                self.assertEqual(self.anchor(case.module), '')
                order = self.native_names(case.module)
                case.module.pick_row(bpy.context, order[-1], range_select=True)
                self.assertEqual(self.chosen(case.module), set(order))
                self.assertEqual(self.anchor(case.module), names[0])
                case.module.pick_row(bpy.context, order[1], range_select=True)
                self.assertEqual(self.chosen(case.module), set(order[:2]))
                self.assertEqual(self.row_state(case.other), other)
                self.assert_authored()

    def test_union_select_extend_and_deselect_preserve_external_vertices(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                self.pick_both(case)
                union = self.union(case.names)
                self.assertNotIn(8, union)
                self.choose({8})
                self.assertEqual(case.select(use_rows=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), union)
                self.choose({8})
                self.assertEqual(case.select(use_rows=True, extend=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), union | {8})
                self.assertEqual(case.deselect(use_rows=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), {8})
                self.assert_inputs_unchanged()

    def test_hidden_vertices_are_excluded_without_changing_visibility(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                self.pick_both(case)
                union = self.union(case.names)
                hidden = min(union)
                self.choose(set(range(9)))
                bm = FIXTURE.bm_for(self.obj)
                bm.verts[hidden].hide_set(True)
                bm.select_flush_mode()
                bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
                before_hide = FIXTURE.visibility_state(self.obj)
                self.assertEqual(case.select(use_rows=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), union - {hidden})
                self.assertEqual(case.deselect(use_rows=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), set())
                self.assertEqual(FIXTURE.visibility_state(self.obj), before_hide)
                bm = FIXTURE.bm_for(self.obj)
                for elements in (bm.verts, bm.edges, bm.faces):
                    for element in elements:
                        element.hide = False
                bmesh.update_edit_mesh(self.obj.data, loop_triangles=False, destructive=False)
                self.assert_inputs_unchanged()

    def test_locked_rows_can_select_and_deselect_their_vertices(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                for name in case.names:
                    case.module.set_locked(bpy.context, name, True)
                self.pick_both(case)
                catalogs = self.catalogs()
                self.choose({8})
                self.assertEqual(case.select(use_rows=True, extend=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), self.union(case.names) | {8})
                self.assertEqual(case.deselect(use_rows=True), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), {8})
                self.assertEqual(self.catalogs(), catalogs)
                self.assert_authored()

    def test_wrong_prefix_pick_and_legacy_named_select_are_refused(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                case.module.pick_row(bpy.context, case.names[0])
                wrong = FIXTURE.PART_NAME if case.module is GROUPS else FIXTURE.CUSTOM_NAMES[0]
                before, rows = FIXTURE.all_selection_state(self.obj), self.row_state(case.module)
                for extend in (False, True):
                    with self.assertRaises((ValueError, RuntimeError)):
                        case.module.pick_row(bpy.context, wrong, extend=extend)
                    self.assertEqual(self.row_state(case.module), rows)
                    self.assertEqual(FIXTURE.all_selection_state(self.obj), before)
                with self.assertRaises(ValueError):
                    ROW_SELECTION.select_vertices(bpy.context, self.obj.data,
                        (case.names[0], wrong), case.module.PREFIX, select=True)
                self.assertEqual(FIXTURE.all_selection_state(self.obj), before)
                self.assert_cancelled(case.select, **{case.name_property: wrong})
                self.assertEqual(FIXTURE.all_selection_state(self.obj), before)
                self.assert_inputs_unchanged()

    def test_rename_reorder_and_inline_rename_keep_chosen_names(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                self.pick_both(case)
                other = self.row_state(case.other)
                original_values = {name: FIXTURE.attribute_values(self.obj, name) for name in case.names}
                self.activate(case.names[1])
                self.assertEqual(case.move(direction='UP'), {'FINISHED'})
                case.module.sync_view(bpy.context)
                self.assertEqual(self.chosen(case.module), set(case.names))
                for name, values in original_values.items():
                    self.assertEqual(FIXTURE.attribute_values(self.obj, name), values)
                renamed = case.module.PREFIX + 'Panel'
                self.assertEqual(case.rename(**{case.name_property: case.names[1], 'name': 'Panel'}), {'FINISHED'})
                case.module.sync_view(bpy.context)
                self.assertEqual(self.chosen(case.module), {case.names[0], renamed})
                row = next(row for row in getattr(bpy.context.window_manager, case.module.VIEW_PROPERTY)
                           if row.native_name == renamed)
                row.name = 'Panel artist'
                case.module.sync_view(bpy.context)
                final = case.module.PREFIX + 'Panel artist'
                self.assertEqual(self.chosen(case.module), {case.names[0], final})
                self.assertEqual(FIXTURE.attribute_values(self.obj, final), original_values[case.names[1]])
                self.assertEqual(self.row_state(case.other), other)
                self.assert_authored()

    def test_sync_keeps_each_catalog_rows_and_does_not_activate_a_native_attribute(self):
        cases = self.cases()
        for case in cases:
            self.pick_both(case)
        expected = {case.module.__name__: self.row_state(case.module) for case in cases}
        active = active_attribute(self.obj)
        for case in reversed(cases):
            case.module.sync_view(bpy.context)
            self.assertEqual(self.row_state(case.module), expected[case.module.__name__])
            self.assertEqual(self.chosen(case.module), set(case.names))
            self.assertEqual(active_attribute(self.obj), active)
            rows = getattr(bpy.context.window_manager, case.module.VIEW_PROPERTY)
            self.assertTrue(rows[0].bl_rna.properties['selected'].is_skip_save)
            self.assertTrue(rows[0].bl_rna.properties['anchor'].is_skip_save)
        self.assert_inputs_unchanged()

    def test_switching_meshes_invalidates_same_named_row_selections(self):
        for case in self.cases():
            self.pick_both(case)
        self.object()
        original = self.obj
        duplicate = original.copy()
        duplicate.data = original.data.copy()
        bpy.context.scene.collection.objects.link(duplicate)
        try:
            original.select_set(False)
            duplicate.select_set(True)
            bpy.context.view_layer.objects.active = duplicate
            duplicate.data.attributes.active_index = duplicate.data.attributes.find('Artist point')
            for case in self.cases():
                case.module.sync_view(bpy.context)
                self.assertEqual(self.chosen(case.module), set())
                self.assertTrue(all(not row.selected for row in getattr(
                    bpy.context.window_manager, case.module.VIEW_PROPERTY)))
            duplicate.select_set(False)
            original.select_set(True)
            bpy.context.view_layer.objects.active = original
            original.data.attributes.active_index = original.data.attributes.find('Artist point')
            for case in self.cases():
                case.module.sync_view(bpy.context)
                self.assertEqual(self.chosen(case.module), set())
        finally:
            bpy.context.view_layer.objects.active = original
            original.select_set(True)
            mesh = duplicate.data
            bpy.data.objects.remove(duplicate, do_unlink=True)
            if not mesh.users:
                bpy.data.meshes.remove(mesh)
            self.obj = original
            self.edit()
        self.assert_inputs_unchanged()

    def test_legacy_default_select_and_deselect_still_use_only_active_group(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                self.pick_both(case)
                self.activate(case.names[0])
                self.choose({8})
                self.assertEqual(case.select(), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj), FIXTURE.members(self.obj, case.names[0]))
                self.choose(self.union(case.names) | {8})
                self.assertEqual(case.deselect(), {'FINISHED'})
                self.assertEqual(selected_vertices(self.obj),
                                 (self.union(case.names) | {8}) - FIXTURE.members(self.obj, case.names[0]))
                self.assertEqual(self.chosen(case.module), set(case.names))
                self.assert_inputs_unchanged()

    def test_whole_row_pick_combines_label_and_icon_with_selected_background_only(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                case.module.pick_row(bpy.context, case.names[0])
                list_class = (GROUPS.CHARACTERDESIGNER_UL_custom_vertex_groups if case.module is GROUPS
                              else PARTS.CHARACTERDESIGNER_UL_vertex_parts)
                ui_list = SimpleNamespace(filter_items=lambda *_: ([], []),
                    bitflag_filter_item=1, bitflag_item_never_show=2, use_filter_sort_reverse=False)
                for name in case.names:
                    row = next(row for row in getattr(bpy.context.window_manager, case.module.VIEW_PROPERTY)
                               if row.native_name == name)
                    recorder = _LayoutRecorder()
                    list_class.draw_item(ui_list, bpy.context, recorder, bpy.context.window_manager,
                                         row, 0, bpy.context.window_manager, case.module.VIEW_INDEX_PROPERTY, 0)
                    pick = next(op for op in recorder.operators if op.identifier == case.pick_id)
                    self.assertEqual(pick.arguments.get('depress'), bool(row.selected))
                    self.assertEqual(recorder.emboss, 'PULLDOWN_MENU')
                    self.assertNotIn('emboss', pick.arguments)
                    self.assertEqual(pick.arguments.get('icon'),
                                     'GROUP_VERTEX' if case.module is GROUPS else 'RESTRICT_SELECT_OFF')
                    self.assertEqual(pick.arguments.get('text'), getattr(row, case.module.LABEL_PROPERTY))
                    self.assertEqual(getattr(pick, case.name_property), name)
                    self.assertEqual(json.loads(pick.visible_names), self.native_names(case.module))
                    # Names use the same event-aware button as the icon; scripted row.name remains
                    # covered by test_rename_reorder_and_inline_rename_keep_chosen_names.
                    self.assertFalse(any(prop in ('selected', case.module.LABEL_PROPERTY)
                                         for _data, prop, _kwargs in recorder.properties))
                self.assert_inputs_unchanged()

    def test_visible_order_serializer_honors_filter_sort_and_reverse(self):
        for case in self.cases():
            with self.subTest(catalog=case.module.__name__):
                names = self.expanded_names(case)
                # Hide row 1 by filter, row 3 with NEVER_SHOW, and reverse a non-native sort.
                flags = [1] * len(names)
                flags[1], flags[3] = 0, 3
                order = list(reversed(range(len(names))))
                ui_list = SimpleNamespace(filter_items=lambda *_: (flags, order),
                    bitflag_filter_item=1, bitflag_item_never_show=2, use_filter_sort_reverse=False)
                result = ROW_SELECTION.visible_names(ui_list, bpy.context,
                    bpy.context.window_manager, case.module.VIEW_PROPERTY)
                expected = [name for index, name in reversed(list(enumerate(names))) if index not in (1, 3)]
                self.assertEqual(json.loads(result), expected)
                ui_list.use_filter_sort_reverse = True
                result = ROW_SELECTION.visible_names(ui_list, bpy.context,
                    bpy.context.window_manager, case.module.VIEW_PROPERTY)
                self.assertEqual(json.loads(result), list(reversed(expected)))
                self.assert_inputs_unchanged()

    def test_pick_invoke_routes_shift_to_range_and_ctrl_to_toggle(self):
        for case in self.cases():
            operator_class = (GROUPS.CHARACTERDESIGNER_OT_custom_vertex_group_pick if case.module is GROUPS
                              else PARTS.CHARACTERDESIGNER_OT_vertex_part_pick)
            for ctrl, shift in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(catalog=case.module.__name__, ctrl=ctrl, shift=shift):
                    wait = mock.Mock(return_value={'FINISHED'})
                    operator = SimpleNamespace(extend=None, range_select=None,
                        execute=lambda _: {'FINISHED'}, wait_for_double_click=wait,
                        **{case.name_property: case.names[0]})
                    event = SimpleNamespace(ctrl=ctrl, shift=shift, type='LEFTMOUSE', value='RELEASE')
                    self.assertEqual(operator_class.invoke(operator, bpy.context, event), {'FINISHED'})
                    self.assertEqual(operator.extend, ctrl)
                    self.assertEqual(operator.range_select, shift)
                    wait.assert_called_once_with(bpy.context, event, self.obj.data)
        self.assert_inputs_unchanged()

    def rename_click_fixture(self):
        class ClickOperator(ROW_SELECTION.RowRenameClick):
            pass

        timer = object()
        window_manager = SimpleNamespace(event_timer_add=mock.Mock(return_value=timer),
            event_timer_remove=mock.Mock(), modal_handler_add=mock.Mock())
        context = SimpleNamespace(window=SimpleNamespace(as_pointer=lambda: 17),
            area=SimpleNamespace(as_pointer=lambda: 23),
            active_object=self.obj, window_manager=window_manager,
            preferences=SimpleNamespace(inputs=SimpleNamespace(
                mouse_double_click_time=500, drag_threshold_mouse=3)))
        operator = ClickOperator()
        operator.rename_row = mock.Mock()
        event = SimpleNamespace(ctrl=False, shift=False, type='LEFTMOUSE', value='RELEASE',
                                mouse_x=100, mouse_y=150, timer=None)
        return context, operator, event, timer

    def test_row_rename_second_press_renames_and_removes_timer_once(self):
        for value in ('PRESS', 'DOUBLE_CLICK'):
            with self.subTest(value=value), \
                    mock.patch.object(ROW_SELECTION.time, 'monotonic', return_value=10.) as clock:
                context, operator, event, timer = self.rename_click_fixture()
                self.assertEqual(operator.wait_for_double_click(context, event, self.obj.data), {'RUNNING_MODAL'})
                context.window_manager.event_timer_add.assert_called_once_with(.5, window=context.window)
                context.window_manager.modal_handler_add.assert_called_once_with(operator)
                event.value, event.mouse_x = value, 102
                clock.return_value = 10.2
                self.assertEqual(operator.modal(context, event), {'FINISHED'})
                operator.rename_row.assert_called_once_with(context)
                context.window_manager.event_timer_remove.assert_called_once_with(timer)
                self.assertIsNone(operator._click_timer)
                operator.cancel(context)
                context.window_manager.event_timer_remove.assert_called_once_with(timer)
        self.assert_inputs_unchanged()

    def test_row_rename_cancellation_passes_input_without_renaming(self):
        reasons = ('timeout', 'move', 'ctrl', 'shift', 'window', 'area', 'no_area', 'mesh',
                   'escape', 'right_click', 'middle_click')
        for reason in reasons:
            with self.subTest(reason=reason), \
                    mock.patch.object(ROW_SELECTION.time, 'monotonic', return_value=10.) as clock:
                context, operator, event, timer = self.rename_click_fixture()
                self.assertEqual(operator.wait_for_double_click(context, event, self.obj.data), {'RUNNING_MODAL'})
                clock.return_value = 10.2
                event.value = 'PRESS'
                if reason == 'timeout':
                    clock.return_value, event.type, event.timer = 10.6, 'TIMER', timer
                elif reason == 'move':
                    event.type, event.mouse_x = 'MOUSEMOVE', 120
                elif reason in ('ctrl', 'shift'):
                    setattr(event, reason, True)
                elif reason == 'window':
                    context.window = SimpleNamespace(as_pointer=lambda: 18)
                elif reason == 'area':
                    context.area = SimpleNamespace(as_pointer=lambda: 24)
                elif reason == 'no_area':
                    context.area = None
                elif reason == 'mesh':
                    context.active_object = SimpleNamespace(type='MESH', data=SimpleNamespace(session_uid=-999))
                else:
                    event.type = {'escape': 'ESC', 'right_click': 'RIGHTMOUSE',
                                  'middle_click': 'MIDDLEMOUSE'}[reason]
                self.assertEqual(operator.modal(context, event), {'FINISHED', 'PASS_THROUGH'})
                operator.rename_row.assert_not_called()
                context.window_manager.event_timer_remove.assert_called_once_with(timer)
                operator.cancel(context)
                context.window_manager.event_timer_remove.assert_called_once_with(timer)
        self.assert_inputs_unchanged()

    def test_row_rename_unrelated_timer_passes_through_until_deadline(self):
        with mock.patch.object(ROW_SELECTION.time, 'monotonic', return_value=10.) as clock:
            context, operator, event, timer = self.rename_click_fixture()
            self.assertEqual(operator.wait_for_double_click(context, event, self.obj.data), {'RUNNING_MODAL'})
            clock.return_value = 10.1
            event.type, event.value, event.timer = 'TIMER', 'NOTHING', object()
            self.assertEqual(operator.modal(context, event), {'PASS_THROUGH'})
            operator.rename_row.assert_not_called()
            context.window_manager.event_timer_remove.assert_not_called()
            self.assertIs(operator._click_timer, timer)
            operator.cancel(context)
            context.window_manager.event_timer_remove.assert_called_once_with(timer)
        self.assert_inputs_unchanged()

    def test_row_rename_only_unmodified_left_click_starts_waiting(self):
        for ctrl, shift, type in ((True, False, 'LEFTMOUSE'), (False, True, 'LEFTMOUSE'),
                                  (False, False, 'RIGHTMOUSE'), (False, False, 'RET')):
            with self.subTest(ctrl=ctrl, shift=shift, type=type):
                context, operator, event, _timer = self.rename_click_fixture()
                event.ctrl, event.shift, event.type = ctrl, shift, type
                self.assertEqual(operator.wait_for_double_click(context, event, self.obj.data), {'FINISHED'})
                context.window_manager.event_timer_add.assert_not_called()
                context.window_manager.modal_handler_add.assert_not_called()
                context.window_manager.event_timer_remove.assert_not_called()
                operator.rename_row.assert_not_called()
        self.assert_inputs_unchanged()


@unittest.skipIf(bpy.app.background, 'Native Undo/Redo needs a separately launched GUI event loop.')
class MeshSelectionRowsHistoryTests(_RowsFixture, unittest.TestCase):
    """Native mesh selection history, without claiming transient row caches undo."""
    def setUp(self):
        super().setUp()
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True
        self.addCleanup(setattr, bpy.context.preferences.edit, 'use_global_undo', previous)

    def current(self):
        self.obj = bpy.data.objects[self.obj_name]
        self.assertEqual(self.obj.mode, 'EDIT')
        # Native active attribute index is not part of Edit BMesh Undo, as
        # independently characterized in X/validation/independent_mesh_parts_20261019/
        # candidate02/native_characterization.json. Core checks still include it.
        return (FIXTURE.all_selection_state(self.obj)[:-1], FIXTURE.author_state(self.obj), bpy.context.mode)

    def history_step(self, label, operation):
        self.assertEqual(bpy.ops.ed.undo_push(message='Before Rows ' + label), {'FINISHED'})
        before = self.current()
        self.assertEqual(operation(), {'FINISHED'})
        self.assertEqual(bpy.ops.ed.undo_push(message='After Rows ' + label), {'FINISHED'})
        after = self.current()
        self.assertNotEqual(after, before)
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        self.assertEqual(self.current(), before)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        self.assertEqual(self.current(), after)
        self.assert_inputs_unchanged()

    def test_union_select_undo_redo_for_both_catalogs(self):
        for case in self.cases():
            self.pick_both(case)
            self.choose({8})
            self.history_step(case.module.PREFIX + 'Select', lambda: case.select(use_rows=True, extend=True))
            self.assertEqual(selected_vertices(self.obj), self.union(case.names) | {8})

    def test_union_deselect_undo_redo_for_both_catalogs(self):
        for case in self.cases():
            self.pick_both(case)
            self.choose(self.union(case.names) | {8})
            self.history_step(case.module.PREFIX + 'Deselect', lambda: case.deselect(use_rows=True))
            self.assertEqual(selected_vertices(self.obj), {8})


def main():
    global OPTIONS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT / 'addons')
    parser.add_argument('--report', type=Path)
    OPTIONS = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    if not os.environ.get('BLENDER_USER_CONFIG'):
        raise RuntimeError('Use the trusted launcher with its dedicated isolated CONFIG.')
    load_feature_modules(OPTIONS.source_root)
    report = {'schema': 'character-designer.mesh-selection-rows.native.v1',
              'recorded_at': datetime.now(timezone.utc).isoformat(),
              'blender': bpy.app.version_string, 'build_hash': bpy.app.build_hash.decode('ascii', errors='replace'),
              'source_root': str(PACKAGE),
              'source_sha256': {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                                for module in (GROUPS, PARTS, VISIBILITY, ROW_SELECTION)},
              'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'fixture_sha256': hashlib.sha256(Path(FIXTURE.__file__).read_bytes()).hexdigest(),
              'isolated_config': os.environ['BLENDER_USER_CONFIG'], 'autoexec_failed': bpy.app.autoexec_fail,
              'artist_opened': False, 'desktop_input': False,
              'registration_scope': 'Only Group/Parts classes/properties; no full addon init.',
              'history_scope': 'Core suite makes no native Undo/Redo claim; GUI class is separate.'}
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(MeshSelectionRowsTests))
    report.update({'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
                   'failures': [{'test': str(test), 'traceback': detail} for test, detail in result.failures],
                   'errors': [{'test': str(test), 'traceback': detail} for test, detail in result.errors],
                   'skipped': [{'test': str(test), 'reason': reason} for test, reason in result.skipped]})
    if OPTIONS.report:
        OPTIONS.report.parent.mkdir(parents=True, exist_ok=True)
        OPTIONS.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('CD_MESH_SELECTION_ROWS_NATIVE ' + json.dumps(report, ensure_ascii=False), flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
