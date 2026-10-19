"""Native regressions for explicitly enabling RR Helper sidebar sorting.

Run through tools/run_trusted_blender_validation.py with a fresh CONFIG.
Arguments after Blender's --: --module-path PATH and --report PATH.
The runner executes the original anchor suite once, then only the new methods
defined here. It never loads an artist file or saves native preferences.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

import bmesh
import bpy


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_MODULE = ROOT / 'addons' / 'random_realm_builder_exporter' / 'rr_sidebar_tabs.py'
BASELINE_PATH = HERE / 'test_rr_sidebar_tabs_blender.py'
_baseline_spec = importlib.util.spec_from_file_location('rr_sidebar_sorting_baseline', BASELINE_PATH)
baseline = importlib.util.module_from_spec(_baseline_spec)
sys.modules[_baseline_spec.name] = baseline
_baseline_spec.loader.exec_module(baseline)


class DrawRecorder(baseline.DrawRecorder):
    """Track visible action order as well as inherited enabled state."""

    def __init__(self, calls=None, events=None, enabled=True):
        super().__init__(calls)
        self.events = [] if events is None else events
        self.enabled = enabled

    def box(self):
        return type(self)(self.calls, self.events, self.enabled)

    def row(self, **kwargs):
        return type(self)(self.calls, self.events, self.enabled)

    def column(self, **kwargs):
        return type(self)(self.calls, self.events, self.enabled)

    def label(self, **kwargs):
        self.events.append(('label', kwargs.get('text', '')))

    def template_list(self, *args, **kwargs):
        self.events.append(('list', args[0]))

    def operator(self, identifier, **kwargs):
        self.events.append(('operator', identifier, kwargs.get('text', ''), self.enabled))
        return super().operator(identifier, **kwargs)


class SidebarSortingEnableTests(baseline.SidebarTabsNativeTests):
    """Reuse the native fixtures without rerunning inherited test methods."""

    def filter_workspace(self, filtered):
        workspace = bpy.context.workspace or next(iter(bpy.data.workspaces))
        if not any(item[0] == workspace for item in self.workspace_cleanup):
            self.workspace_cleanup.append((workspace, workspace.use_filter_by_owner, False))
        workspace.use_filter_by_owner = filtered
        return workspace

    def protected_mesh(self):
        data = bpy.data.meshes.new('RR Sorting Enable Native Mesh')
        data.from_pydata([(0, 0, 0), (1, 0, 0.2), (1, 1, 0.4), (0, 1, 0)],
                         [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new('RR Sorting Enable Native Object', data)
        bpy.context.scene.collection.objects.link(obj)
        self.objects.append(obj)
        for item in bpy.context.selected_objects:
            item.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        group = obj.vertex_groups.new(name='foot.L')
        group.add([0, 2], 0.375, 'REPLACE')
        group.lock_weight = True
        custom = data.attributes.new('Custom_Strap', 'INT', 'POINT')
        for index, item in enumerate(custom.data):
            item.value = index % 2
        data.attributes.new('Artist Float', 'FLOAT', 'POINT')
        uv = data.uv_layers.new(name='Artist UV')
        for index, loop in enumerate(uv.data):
            loop.uv = (index / 4.0, (index % 2) / 2.0)
        colors = data.color_attributes.new(name='Artist Color', type='FLOAT_COLOR', domain='POINT')
        for index, item in enumerate(colors.data):
            item.color = (index / 4.0, 0.25, 0.75, 1.0)
        obj.shape_key_add(name='Basis')
        key = obj.shape_key_add(name='Artist Bend')
        key.data[2].co.z += 0.25
        key.value = 0.35
        obj['artist_note'] = 'keep this exact'
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(data)
        bm.verts.ensure_lookup_table()
        for vertex in bm.verts:
            vertex.select_set(False)
        bm.verts[2].select_set(True)
        bm.select_history.clear()
        bm.select_history.add(bm.verts[2])
        bmesh.update_edit_mesh(data, loop_triangles=False, destructive=False)
        return obj

    def mesh_inputs(self, obj):
        data = obj.data
        bm = bmesh.from_edit_mesh(data)
        uv_layers = tuple((name, tuple(tuple(loop[bm.loops.layers.uv[name]].uv)
                                       for face in bm.faces for loop in face.loops))
                          for name in bm.loops.layers.uv.keys())
        colors = tuple((name, tuple(tuple(vertex[bm.verts.layers.float_color[name]])
                                   for vertex in bm.verts))
                       for name in bm.verts.layers.float_color.keys())
        keys = tuple((block.name, block.value, block.mute, block.vertex_group,
                      tuple(tuple(item.co) for item in block.data))
                     for block in data.shape_keys.key_blocks)
        return baseline.edit_mesh_state(obj), uv_layers, colors, keys

    def draw_actions(self, *, index, busy=False):
        setattr(bpy.context.window_manager, self.tabs.INDEX_PROPERTY, index)
        layout = DrawRecorder()
        with mock.patch.object(self.tabs, '_busy', busy), self.originals_untouchable():
            with mock.patch.object(self.tabs, '_collect', side_effect=AssertionError('draw scanned panels')):
                with mock.patch.object(self.tabs, '_register_anchor', side_effect=AssertionError('draw registered panels')):
                    self.tabs.draw_sidebar_tabs(layout, bpy.context)
        return [enabled for identifier, enabled in layout.calls
                if identifier == 'rr_helper.move_sidebar_tab'], layout

    def test_plain_arrow_states_cover_first_middle_last_and_busy(self):
        self.filter_workspace(False)
        self.tabs.apply_order(self.desired(), context=bpy.context)
        count = len(getattr(bpy.context.window_manager, self.tabs.VIEW_PROPERTY))
        self.assertGreaterEqual(count, 3)
        before_file, before_anchors = self.config.read_bytes(), self.anchor_state()
        before_polls = list(self.poll_calls)
        for index, busy, expected in ((0, False, [False, True]),
                                      (1, False, [True, True]),
                                      (count - 1, False, [True, False]),
                                      (1, True, [False, False])):
            with self.subTest(index=index, busy=busy):
                actions, _layout = self.draw_actions(index=index, busy=busy)
                self.assertEqual(actions, expected)
        self.assertEqual(self.config.read_bytes(), before_file)
        self.assertEqual(self.anchor_state(), before_anchors)
        self.assertEqual(self.poll_calls, before_polls)
        self.assert_originals_exact()

    def test_filtered_enable_action_is_above_list_and_explains_scope(self):
        self.filter_workspace(True)
        self.tabs.refresh_view()
        actions, layout = self.draw_actions(index=1)
        self.assertEqual(actions, [False, False])
        enable = [event for event in layout.events
                  if event[:2] == ('operator', 'rr_helper.enable_sidebar_sorting')]
        self.assertEqual(len(enable), 1)
        self.assertEqual(enable[0][2], 'Enable Tab Reordering')
        self.assertTrue(enable[0][3])
        self.assertLess(layout.events.index(enable[0]),
                        next(index for index, event in enumerate(layout.events) if event[0] == 'list'))
        self.assertIn(('label', 'Show all add-on tabs in this workspace.'), layout.events)
        self.assert_originals_exact()

    def test_native_enable_changes_only_current_filter_and_preserves_mesh_inputs(self):
        workspace = self.filter_workspace(False)
        self.tabs.apply_order(self.desired(), context=bpy.context)
        workspace.use_filter_by_owner = True
        before_workspaces = baseline.workspace_state()
        before_order = self.tabs.get_order()
        before_file = self.config.read_bytes()
        before_owners = tuple(owner.name for owner in workspace.owner_ids)
        obj = self.protected_mesh()
        before_mesh = self.mesh_inputs(obj)
        with self.originals_untouchable():
            with mock.patch.object(self.tabs, 'enable_sorting', wraps=self.tabs.enable_sorting) as enabled:
                self.assertEqual(bpy.ops.rr_helper.enable_sidebar_sorting(), {'FINISHED'})
                enabled.assert_called_once()
        self.assertFalse(workspace.use_filter_by_owner)
        self.assertEqual(tuple(owner.name for owner in workspace.owner_ids), before_owners)
        after_workspaces = baseline.workspace_state()
        expected = tuple((pointer, False if pointer == workspace.as_pointer() else filtered, owners)
                         for pointer, filtered, owners in before_workspaces)
        self.assertEqual(after_workspaces, expected)
        self.assertEqual(self.tabs.get_order(), before_order)
        self.assertEqual(self.config.read_bytes(), before_file)
        self.assertEqual(self.mesh_inputs(obj), before_mesh)
        self.assertFalse(self.tabs._busy)
        self.assert_originals_exact()

    def test_enable_write_failure_restores_filter_anchors_and_saved_order(self):
        workspace = self.filter_workspace(False)
        self.tabs.apply_order(self.desired(), context=bpy.context)
        workspace.use_filter_by_owner = True
        before_workspaces = baseline.workspace_state()
        before_file, before_anchors = self.config.read_bytes(), self.anchor_state()
        before_order = self.tabs.get_order()
        with self.originals_untouchable():
            with mock.patch.object(self.tabs, '_write_config', side_effect=OSError('injected enable write failure')):
                with self.assertRaises(self.tabs.SidebarTabsError):
                    self.tabs.enable_sorting(bpy.context)
        self.assertEqual(baseline.workspace_state(), before_workspaces)
        self.assertEqual(self.config.read_bytes(), before_file)
        self.assertEqual(self.anchor_state(), before_anchors)
        self.assertEqual(self.tabs.get_order(), before_order)
        self.assertFalse(self.tabs._busy)
        self.assertTrue(self.tabs.get_error())
        self.assert_originals_exact()

    def test_enable_registration_failure_restores_filter_anchors_and_saved_order(self):
        workspace = self.filter_workspace(False)
        self.tabs.apply_order(self.desired(), context=bpy.context)
        workspace.use_filter_by_owner = True
        before_workspaces = baseline.workspace_state()
        before_file, before_anchors = self.config.read_bytes(), self.anchor_state()
        before_order = self.tabs.get_order()
        native_register = self.tabs._register_anchor
        calls = []

        def fail_third(cls):
            calls.append(cls)
            if len(calls) == 3:
                raise RuntimeError('injected enable anchor registration failure')
            return native_register(cls)

        with self.originals_untouchable():
            with mock.patch.object(self.tabs, '_register_anchor', side_effect=fail_third):
                with self.assertRaises(self.tabs.SidebarTabsError):
                    self.tabs.enable_sorting(bpy.context)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(baseline.workspace_state(), before_workspaces)
        self.assertEqual(self.config.read_bytes(), before_file)
        self.assertEqual(self.anchor_state(), before_anchors)
        self.assertEqual(self.tabs.get_order(), before_order)
        self.assertFalse(self.tabs._busy)
        self.assertTrue(self.tabs.get_error())
        self.assert_originals_exact()

    def test_native_move_changes_order_and_keeps_active_tab_selected(self):
        self.filter_workspace(False)
        self.tabs.apply_order(self.desired(), context=bpy.context)
        before_order, before_file = self.tabs.get_order(), self.config.read_bytes()
        before_workspaces = baseline.workspace_state()
        manager = bpy.context.window_manager
        setattr(manager, self.tabs.INDEX_PROPERTY, 1)
        selected = before_order[1]
        expected = list(before_order)
        expected[1], expected[2] = expected[2], expected[1]
        with self.originals_untouchable():
            self.assertEqual(bpy.ops.rr_helper.move_sidebar_tab(direction='DOWN'), {'FINISHED'})
        self.assertEqual(self.tabs.get_order(), expected)
        self.assertEqual(getattr(manager, self.tabs.INDEX_PROPERTY), 2)
        rows = getattr(manager, self.tabs.VIEW_PROPERTY)
        self.assertEqual([item.name for item in rows], expected)
        self.assertEqual(rows[2].name, selected)
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['order'], expected)
        self.assertNotEqual(self.config.read_bytes(), before_file)
        with self.originals_untouchable():
            self.assertEqual(bpy.ops.rr_helper.move_sidebar_tab(direction='UP'), {'FINISHED'})
        self.assertEqual(self.tabs.get_order(), before_order)
        self.assertEqual(getattr(manager, self.tabs.INDEX_PROPERTY), 1)
        self.assertEqual(getattr(manager, self.tabs.VIEW_PROPERTY)[1].name, selected)
        self.assertEqual(self.config.read_bytes(), before_file)
        self.assertEqual(baseline.workspace_state(), before_workspaces)
        self.assert_originals_exact()


def new_suite():
    """Do not let TestLoader include the inherited original fourteen tests."""
    return unittest.TestSuite(SidebarSortingEnableTests(name) for name in sorted(
        name for name, value in SidebarSortingEnableTests.__dict__.items()
        if name.startswith('test_') and callable(value)))


def load_tests(loader, tests, pattern):
    return new_suite()


def main():
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module-path', type=Path, default=DEFAULT_MODULE)
    parser.add_argument('--report', type=Path)
    options = parser.parse_args(arguments)
    module_path = options.module_path.resolve(strict=True)
    spec = importlib.util.spec_from_file_location('rr_sidebar_sorting_enable_subject', module_path)
    subject = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = subject
    spec.loader.exec_module(subject)
    baseline.OPTIONS, baseline.SUBJECT = options, subject
    original_suite = unittest.defaultTestLoader.loadTestsFromTestCase(baseline.SidebarTabsNativeTests)
    additional_suite = new_suite()
    original_count, additional_count = original_suite.countTestCases(), additional_suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite((original_suite, additional_suite)))
    report = {
        'schema': 'rr-helper.sidebar-sorting-enable.native-regression.v1',
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'blender': bpy.app.version_string,
        'blender_build_hash': bpy.app.build_hash.decode('ascii', errors='replace'),
        'module_path': str(module_path),
        'module_sha256': hashlib.sha256(module_path.read_bytes()).hexdigest(),
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'baseline_test_sha256': hashlib.sha256(BASELINE_PATH.read_bytes()).hexdigest(),
        'autoexec_failed': bpy.app.autoexec_fail,
        'runtime_autoexec_preference': bpy.context.preferences.filepaths.use_scripts_auto_execute,
        'isolated_config': os.environ.get('BLENDER_USER_CONFIG', ''),
        'artist_opened': False,
        'native_preferences_saved': False,
        'original_suite_count': original_count,
        'additional_suite_count': additional_count,
        'inherited_originals_repeated': False,
        'native_visual_tab_order': 'Separate GUI verification is required.',
        'passed': result.wasSuccessful(),
        'tests_run': result.testsRun,
        'failures': [{'test': str(test), 'traceback': detail} for test, detail in result.failures],
        'errors': [{'test': str(test), 'traceback': detail} for test, detail in result.errors],
        'skipped': [{'test': str(test), 'reason': reason} for test, reason in result.skipped],
    }
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('RR_SIDEBAR_SORTING_ENABLE_NATIVE ' + json.dumps(report, ensure_ascii=False), flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
