"""Native regressions for RR Helper's non-invasive sidebar anchor ordering.

Run with tools/run_trusted_blender_validation.py in its fresh CONFIG.
Arguments after Blender's --: --report PATH and optional --module-path PATH.
Only rr_sidebar_tabs is imported. The rejected re-registration tests were
archived separately. Actual visual tab order still requires a GUI check.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import _bpy
import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODULE = ROOT / 'addons' / 'random_realm_builder_exporter' / 'rr_sidebar_tabs.py'
OPTIONS = SUBJECT = None
FOREIGN_OWNER = 'rr.sidebar.native.foreign.fixture'
A, B = 'RR Sidebar Test A', 'RR Sidebar Test B'
HIDDEN, CONTEXT = 'RR Sidebar Test Hidden', 'RR Sidebar Test Context'
RAISING, PINNED = 'RR Sidebar Test Raising', 'RR Sidebar Test Pinned'
POSITIVE_HEADERLESS, LATE = 'RR Sidebar Test Positive Headerless', 'RR Sidebar Test Late'


def native_class(cls):
    rna = cls.__dict__.get('bl_rna')
    if rna is None:
        return None
    for base in (bpy.types.PropertyGroup, bpy.types.UIList, bpy.types.Operator, bpy.types.Panel):
        if issubclass(cls, base):
            return base.bl_rna_get_subclass_py(rna.identifier, None)
    raise AssertionError('Unexpected RNA kind: ' + repr(cls))


def registered_panels():
    result, seen = [], set()
    pending = list(bpy.types.Panel.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        pending.extend(cls.__subclasses__())
        if native_class(cls) is cls:
            result.append(cls)
    return result


def callback(cls, name):
    value = getattr(cls, name, None)
    return getattr(value, '__func__', value)


def panel_state(classes):
    return tuple((cls, native_class(cls) is cls, getattr(cls, 'bl_parent_id', ''),
                  getattr(cls, 'bl_category', ''), getattr(cls, 'bl_space_type', ''),
                  getattr(cls, 'bl_region_type', ''), getattr(cls, 'bl_context', ''),
                  getattr(cls, 'bl_label', ''), getattr(cls, 'bl_description', ''),
                  getattr(cls, 'bl_translation_context', ''), getattr(cls, 'bl_owner_id', None),
                  'bl_owner_id' in cls.__dict__, cls.__dict__.get('bl_owner_id'),
                  getattr(cls, 'bl_order', 0), 'bl_order' in cls.__dict__,
                  cls.__dict__.get('bl_order'), frozenset(getattr(cls, 'bl_options', ())),
                  callback(cls, 'draw'), callback(cls, 'poll'),
                  callback(cls, 'register'), callback(cls, 'unregister'))
                 for cls in classes)


def handler_state():
    return {name: tuple(value) for name in dir(bpy.app.handlers)
            if isinstance(value := getattr(bpy.app.handlers, name), list)}


def workspace_state():
    return tuple((workspace.as_pointer(), workspace.use_filter_by_owner,
                  tuple(owner.name for owner in workspace.owner_ids))
                 for workspace in bpy.data.workspaces)


def freeze(value):
    if hasattr(value, 'items'):
        return tuple(sorted((str(key), freeze(item)) for key, item in value.items()))
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return tuple(freeze(item) for item in value)


def edit_mesh_state(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    deform = bm.verts.layers.deform.active
    return {
        'mode': bpy.context.mode,
        'active': bpy.context.view_layer.objects.active.as_pointer(),
        'selected_objects': tuple(sorted(item.as_pointer() for item in bpy.context.selected_objects)),
        'matrix': tuple(value for row in obj.matrix_world for value in row),
        'vertices': tuple((tuple(v.co), v.select, v.hide) for v in bm.verts),
        'edges': tuple((tuple(v.index for v in edge.verts), edge.select, edge.hide) for edge in bm.edges),
        'faces': tuple((tuple(v.index for v in face.verts), face.select, face.hide) for face in bm.faces),
        'history': tuple((type(item).__name__, item.index) for item in bm.select_history),
        'active_face': bm.faces.active.index if bm.faces.active else None,
        'deform': tuple(tuple(sorted(v[deform].items())) for v in bm.verts) if deform is not None else (),
        'integer_layers': tuple((name, tuple(v[bm.verts.layers.int[name]] for v in bm.verts))
                               for name in sorted(bm.verts.layers.int.keys())),
        'float_layers': tuple((name, tuple(v[bm.verts.layers.float[name]] for v in bm.verts))
                             for name in sorted(bm.verts.layers.float.keys())),
        'groups': tuple((group.name, group.lock_weight) for group in obj.vertex_groups),
        'active_group': obj.vertex_groups.active_index,
        'object_properties': freeze(obj), 'mesh_properties': freeze(obj.data),
        'attributes': tuple((attr.name, attr.data_type, attr.domain) for attr in obj.data.attributes),
        'mesh_identity': obj.data.as_pointer(),
        'scene_objects': tuple(sorted(item.as_pointer() for item in bpy.context.scene.objects)),
    }


class DrawRecorder:
    """Record draw-only action state; visual rendering is checked separately."""
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls
        self.enabled = True

    def box(self):
        return DrawRecorder(self.calls)

    def row(self, **kwargs):
        return DrawRecorder(self.calls)

    def column(self, **kwargs):
        return DrawRecorder(self.calls)

    def label(self, **kwargs):
        pass

    def template_list(self, *args, **kwargs):
        pass

    def operator(self, identifier, **kwargs):
        self.calls.append((identifier, self.enabled))
        return SimpleNamespace()


class SidebarTabsNativeTests(unittest.TestCase):
    def setUp(self):
        self.tabs = SUBJECT
        self.classes, self.objects, self.poll_calls = [], [], []
        self.hooks = {'register': 0, 'unregister': 0}
        self.workspace_cleanup = []
        self.baseline_handlers = handler_state()
        self.baseline_workspaces = workspace_state()
        self.baseline_owner = _bpy._bl_owner_id_get()
        directory = OPTIONS.report.parent if OPTIONS.report else Path(tempfile.gettempdir())
        directory.mkdir(parents=True, exist_ok=True)
        self.case_directory = Path(tempfile.mkdtemp(prefix=self._testMethodName + '_', dir=directory))
        self.config = self.case_directory / 'rr_helper_sidebar_tabs.json'
        self.path_patch = mock.patch.object(self.tabs, '_config_path', return_value=self.config)
        self.path_patch.start()
        self.addCleanup(self.cleanup_case)
        self.tabs.unregister()
        self.create_fixtures()
        self.original_classes = tuple(registered_panels())
        self.original_state = panel_state(self.original_classes)
        self.original_hooks = dict(self.hooks)
        self.tabs.register()

    def cleanup_case(self):
        failures = []
        try:
            self.tabs.unregister()
            self.tabs.unregister()
        except Exception as exc:
            failures.append('subject cleanup: ' + repr(exc))
        for obj in reversed(self.objects):
            try:
                if obj.mode != 'OBJECT':
                    bpy.context.view_layer.objects.active = obj
                    bpy.ops.object.mode_set(mode='OBJECT')
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data.users == 0:
                    bpy.data.meshes.remove(data)
            except Exception as exc:
                failures.append('mesh cleanup: ' + repr(exc))
        for workspace, previous_filter, added in self.workspace_cleanup:
            workspace.use_filter_by_owner = previous_filter
            if added and (owner := workspace.owner_ids.get(FOREIGN_OWNER)) is not None:
                workspace.owner_ids.remove(owner)
        for cls in reversed(self.classes):
            try:
                if native_class(cls) is cls:
                    bpy.utils.unregister_class(cls)
            except Exception as exc:
                failures.append('fixture cleanup ' + cls.__name__ + ': ' + repr(exc))
        self.path_patch.stop()
        if _bpy._bl_owner_id_get() != self.baseline_owner:
            failures.append('global owner guard changed')
            _bpy._bl_owner_id_set(self.baseline_owner)
        if workspace_state() != self.baseline_workspaces:
            failures.append('workspace owner filters changed')
        if handler_state() != self.baseline_handlers:
            failures.append('application handlers leaked')
        if bpy.app.timers.is_registered(self.tabs._startup):
            failures.append('startup timer leaked')
        if getattr(self.tabs, '_anchors', ()):
            failures.append('anchor references leaked')
        if any(getattr(cls, '_rr_helper_sidebar_anchor', False) for cls in registered_panels()):
            failures.append('native anchor RNA classes leaked')
        if any(native_class(cls) is cls for cls in self.classes):
            failures.append('test Panel classes leaked')
        if failures:
            raise AssertionError('; '.join(failures))

    def make_panel(self, name, category='', *, bases=None, owner='', declared_owner=True, **extra):
        namespace = {'__module__': __name__, 'bl_idname': name, 'bl_label': name,
                     'bl_space_type': 'VIEW_3D', 'bl_region_type': 'UI', 'bl_category': category}
        if bases is None:
            bases = (bpy.types.Panel,)
            namespace['draw'] = lambda self, context: None
        namespace.update(extra)
        if declared_owner:
            namespace['bl_owner_id'] = owner
        cls = type(name, bases, namespace)
        self.classes.append(cls)
        previous = _bpy._bl_owner_id_get()
        try:
            _bpy._bl_owner_id_set(owner)
            bpy.utils.register_class(cls)
        finally:
            _bpy._bl_owner_id_set(previous)
        return cls

    def create_fixtures(self):
        self.primary = self.make_panel('RR_SIDEBAR_TEST_PT_primary', A, bl_order=12)
        self.child = self.make_panel('RR_SIDEBAR_TEST_PT_child', A, bl_parent_id=self.primary.__name__)
        self.grandchild = self.make_panel('RR_SIDEBAR_TEST_PT_grandchild', A,
                                         bl_parent_id=self.child.__name__, bl_order=3)
        self.inherited_child = self.make_panel('RR_SIDEBAR_TEST_PT_inherited_child', A,
                                              bases=(self.primary,), bl_parent_id=self.primary.__name__)
        self.template = type('RRSidebarUnregisteredTemplate', (bpy.types.Panel,), {
            '__module__': __name__, 'bl_space_type': 'VIEW_3D', 'bl_region_type': 'UI',
            'bl_order': 31, 'draw': lambda self, context: None,
        })
        self.indirect = self.make_panel('RR_SIDEBAR_TEST_PT_indirect', B, bases=(self.template,))

        def hidden_poll(cls, context):
            self.poll_calls.append(cls.__name__)
            return False

        def raising_poll(cls, context):
            self.poll_calls.append(cls.__name__)
            raise RuntimeError('fixture poll fails in this context')

        self.hidden = self.make_panel('RR_SIDEBAR_TEST_PT_hidden', HIDDEN, poll=classmethod(hidden_poll))
        self.raising = self.make_panel('RR_SIDEBAR_TEST_PT_raising', RAISING, poll=classmethod(raising_poll))
        self.contextual = self.make_panel('RR_SIDEBAR_TEST_PT_contextual', CONTEXT,
                                         bl_context='mesh_edit', poll=classmethod(lambda cls, context: True))
        self.pinned = self.make_panel('RR_SIDEBAR_TEST_PT_pinned', PINNED,
                                      bl_options={'HIDE_HEADER'}, bl_order=0)
        self.positive_headerless = self.make_panel('RR_SIDEBAR_TEST_PT_positive_headerless',
                                                   POSITIVE_HEADERLESS,
                                                   bl_options={'HIDE_HEADER'}, bl_order=7)
        # Unknown, undeclared native owners are safe because they are never
        # inferred, unregistered, or re-registered by the order manager.
        self.foreign = self.make_panel('RR_SIDEBAR_TEST_PT_foreign', B,
                                       owner=FOREIGN_OWNER, declared_owner=False)
        self.unknown = self.make_panel('RR_SIDEBAR_TEST_PT_unknown', A,
                                       owner='unknown.script.native.owner', declared_owner=False)

        def register_hook(cls):
            self.hooks['register'] += 1

        def unregister_hook(cls):
            self.hooks['unregister'] += 1

        self.hooked = self.make_panel('RR_SIDEBAR_TEST_PT_hooked', B,
                                      register=classmethod(register_hook), unregister=classmethod(unregister_hook))

    def desired(self):
        return [B, HIDDEN, A, CONTEXT, RAISING, POSITIVE_HEADERLESS]

    @contextmanager
    def originals_untouchable(self):
        real_register, real_unregister = bpy.utils.register_class, bpy.utils.unregister_class

        def guarded(function, cls):
            if issubclass(cls, bpy.types.Panel):
                self.assertTrue(cls.__dict__.get('_rr_helper_sidebar_anchor', False),
                                'Order manager touched an original Panel: ' + cls.__name__)
            return function(cls)

        with mock.patch.object(bpy.utils, 'register_class', side_effect=lambda cls: guarded(real_register, cls)):
            with mock.patch.object(bpy.utils, 'unregister_class', side_effect=lambda cls: guarded(real_unregister, cls)):
                yield

    def assert_originals_exact(self):
        self.assertEqual(panel_state(self.original_classes), self.original_state)
        self.assertEqual(self.hooks, self.original_hooks)
        self.assertEqual(_bpy._bl_owner_id_get(), self.baseline_owner)

    def anchor(self, category):
        matches = [cls for cls in self.tabs._anchors if cls.bl_category == category]
        self.assertEqual(len(matches), 1, 'Expected one owned anchor for ' + category)
        self.assertIs(native_class(matches[0]), matches[0])
        self.assertTrue(matches[0].__dict__.get('_rr_helper_sidebar_anchor', False))
        return matches[0]

    def anchor_state(self):
        return panel_state(tuple(self.tabs._anchors))

    def test_originals_owner_hooks_hierarchy_and_orders_are_untouched(self):
        with self.originals_untouchable():
            names = self.tabs.apply_order(self.desired())
            self.assertEqual(names[:len(self.desired())], self.desired())
            self.assertEqual(self.tabs.get_order()[:len(self.desired())], self.desired())
            self.tabs.refresh_view()
            self.assert_originals_exact()
            self.assertNotIn('bl_rna', self.template.__dict__)
            self.assertNotIn('bl_order', self.indirect.__dict__)
            self.assertNotIn('bl_order', self.inherited_child.__dict__)
            self.assertNotIn('bl_owner_id', self.foreign.__dict__)
            self.assertNotIn('bl_owner_id', self.unknown.__dict__)
            self.assertEqual(self.child.bl_parent_id, self.primary.__name__)
            self.assertEqual(self.grandchild.bl_parent_id, self.child.__name__)
            self.assertTrue(self.tabs._anchors)
            self.tabs.unregister()
            self.assert_originals_exact()

    def test_pinned_native_headerless_categories_are_not_movable(self):
        self.assertIn(PINNED, self.tabs.get_pinned())
        self.assertNotIn(PINNED, self.tabs.get_order())
        self.assertIn(POSITIVE_HEADERLESS, self.tabs.get_order())
        with self.originals_untouchable():
            with self.assertRaises(self.tabs.SidebarTabsError):
                self.tabs.apply_order([PINNED] + self.desired())
        self.assert_originals_exact()
        self.assertFalse(self.config.exists())

    def test_anchor_registration_failure_restores_previous_anchors_and_json(self):
        with self.originals_untouchable():
            self.tabs.apply_order(self.desired())
            old_order, old_anchors = self.tabs.get_order(), self.anchor_state()
            old_file = self.config.read_bytes()
            native_register = self.tabs._register_anchor
            calls = []

            def fail_third(cls):
                calls.append(cls)
                if len(calls) == 3:
                    raise RuntimeError('injected one anchor registration failure')
                return native_register(cls)

            with mock.patch.object(self.tabs, '_register_anchor', side_effect=fail_third):
                with self.assertRaises(self.tabs.SidebarTabsError):
                    self.tabs.apply_order(list(reversed(self.desired())))
            self.assertGreaterEqual(len(calls), 3)
            self.assertEqual(self.tabs.get_order(), old_order)
            self.assertEqual(self.anchor_state(), old_anchors)
            self.assertEqual(self.config.read_bytes(), old_file)
            self.assert_originals_exact()
            self.assertTrue(self.tabs.get_error())

    def test_partial_old_anchor_removal_restores_native_registration_sequence(self):
        with self.originals_untouchable():
            self.tabs.apply_order(self.desired())
            old_order, old_anchors = self.tabs.get_order(), self.anchor_state()
            old_classes = tuple(self.tabs._anchors)
            old_file = self.config.read_bytes()
            native_unregister, native_register = self.tabs._unregister_anchor, self.tabs._register_anchor
            old_removals, restored = [], []

            def fail_second_old(cls):
                if cls in old_classes:
                    old_removals.append(cls)
                    if len(old_removals) == 2:
                        raise RuntimeError('injected partial old-anchor removal failure')
                return native_unregister(cls)

            def record_register(cls):
                if cls in old_classes:
                    restored.append(cls)
                return native_register(cls)

            with mock.patch.object(self.tabs, '_unregister_anchor', side_effect=fail_second_old), \
                    mock.patch.object(self.tabs, '_register_anchor', side_effect=record_register):
                with self.assertRaises(self.tabs.SidebarTabsError):
                    self.tabs.apply_order(list(reversed(self.desired())))
            self.assertEqual(tuple(restored), old_classes)
            self.assertEqual(self.tabs.get_order(), old_order)
            self.assertEqual(self.anchor_state(), old_anchors)
            self.assertEqual(self.config.read_bytes(), old_file)
            self.assert_originals_exact()

    def test_config_write_failure_restores_anchors_without_touching_originals(self):
        with self.originals_untouchable():
            self.tabs.apply_order(self.desired())
            old_order, old_anchors = self.tabs.get_order(), self.anchor_state()
            old_file = self.config.read_bytes()
            with mock.patch.object(self.tabs, '_write_config', side_effect=OSError('injected order-file write failure')):
                with self.assertRaises(self.tabs.SidebarTabsError):
                    self.tabs.apply_order(list(reversed(self.desired())))
            self.assertEqual(self.tabs.get_order(), old_order)
            self.assertEqual(self.anchor_state(), old_anchors)
            self.assertEqual(self.config.read_bytes(), old_file)
            self.assert_originals_exact()

    def test_saved_order_restores_on_reenable_and_removes_only_owned_anchors(self):
        with self.originals_untouchable():
            self.tabs.apply_order(self.desired())
            before_file = self.config.read_bytes()
            payload = json.loads(before_file)
            self.assertEqual(payload['schema'], 'rr-helper.sidebar-tabs')
            self.assertEqual(payload['version'], 1)
            self.assertEqual(payload['order'][:len(self.desired())], self.desired())
            self.tabs.unregister()
            self.assertFalse(self.tabs._anchors)
            self.assert_originals_exact()
            self.tabs.register()
            bpy.app.timers.unregister(self.tabs._startup)
            delays = []
            for _index in range(5):
                delays.append(self.tabs._startup())
                if delays[-1] is None:
                    break
            self.assertIsNone(delays[-1])
            self.assertLessEqual(len(delays), 3)
            self.assertEqual(self.tabs.get_order()[:len(self.desired())], self.desired())
            self.assertEqual(self.config.read_bytes(), before_file)
            self.assert_originals_exact()

    def test_false_exception_and_mode_context_polls_do_not_create_ghost_tabs(self):
        with self.originals_untouchable():
            self.tabs.apply_order(self.desired())
        plain_workspace = SimpleNamespace(use_filter_by_owner=False)
        object_context = SimpleNamespace(mode='OBJECT', workspace=plain_workspace, object=None, active_object=None)
        edit_context = SimpleNamespace(mode='EDIT_MESH', workspace=plain_workspace,
                                       object=SimpleNamespace(type='MESH'), active_object=SimpleNamespace(type='MESH'))
        self.assertFalse(self.anchor(HIDDEN).poll(object_context))
        self.assertFalse(self.anchor(RAISING).poll(object_context))
        self.assertFalse(self.anchor(CONTEXT).poll(object_context))
        self.assertTrue(self.anchor(CONTEXT).poll(edit_context))
        self.assertTrue(self.anchor(A).poll(object_context))
        self.assertFalse(self.tabs._new_anchor('Item', ()).poll(object_context))
        self.assertFalse(self.tabs._new_anchor('View', ()).poll(object_context))
        self.assertTrue(self.tabs._new_anchor('Tool', ()).poll(object_context))
        self.assert_originals_exact()

    def test_dynamic_replacement_disable_and_new_root_refresh_do_not_resurrect(self):
        self.tabs.apply_order(self.desired())
        old_anchor = self.anchor(HIDDEN)
        bpy.utils.unregister_class(self.hidden)
        self.assertFalse(old_anchor.poll(bpy.context))
        replacement = self.make_panel(self.hidden.__name__, HIDDEN, poll=classmethod(lambda cls, context: True))
        self.assertFalse(old_anchor.poll(bpy.context), 'An old class reference must not impersonate its replacement')
        with self.originals_untouchable():
            self.tabs.refresh_view()
            self.tabs.apply_order(self.desired())
        self.assertTrue(self.anchor(HIDDEN).poll(bpy.context))
        late = self.make_panel('RR_SIDEBAR_TEST_PT_late', LATE)
        with self.originals_untouchable():
            self.assertIn(LATE, self.tabs.refresh_view())
            self.tabs.apply_order([LATE] + self.desired())
        self.assertEqual(self.tabs.get_order()[0], LATE)
        bpy.utils.unregister_class(replacement)
        with self.originals_untouchable():
            self.tabs.refresh_view()
            self.tabs.apply_order(self.desired())
            self.tabs.unregister()
        self.assertNotIn(HIDDEN, self.tabs.get_order())
        self.assertIsNone(native_class(self.hidden))
        self.assertIsNone(native_class(replacement))
        self.assertIs(native_class(late), late)

    def test_owner_filtered_workspace_disables_all_anchors_and_keeps_originals(self):
        self.tabs.apply_order(self.desired())
        before_anchors, before_file = self.anchor_state(), self.config.read_bytes()
        workspace = bpy.context.workspace or next(iter(bpy.data.workspaces))
        added = workspace.owner_ids.get(FOREIGN_OWNER) is None
        self.workspace_cleanup.append((workspace, workspace.use_filter_by_owner, added))
        if added:
            workspace.owner_ids.new(FOREIGN_OWNER)
        workspace.use_filter_by_owner = True
        before_workspaces = workspace_state()
        self.assertTrue(self.tabs._workspace_filtered(bpy.context))
        self.assertTrue(all(not cls.poll(bpy.context) for cls in self.tabs._anchors))
        with self.originals_untouchable():
            with self.assertRaises(self.tabs.SidebarTabsError):
                self.tabs.apply_order(list(reversed(self.desired())), context=bpy.context)
        self.assertEqual(self.anchor_state(), before_anchors)
        self.assertEqual(self.config.read_bytes(), before_file)
        self.assertEqual(workspace_state(), before_workspaces)
        self.assert_originals_exact()

    def test_filtered_draw_disables_both_arrows_without_scanning_or_writing(self):
        self.tabs.apply_order(self.desired())
        workspace = bpy.context.workspace or next(iter(bpy.data.workspaces))
        self.workspace_cleanup.append((workspace, workspace.use_filter_by_owner, False))
        workspace.use_filter_by_owner = True
        setattr(bpy.context.window_manager, self.tabs.INDEX_PROPERTY, 1)
        before_file, before_anchors = self.config.read_bytes(), self.anchor_state()
        before_polls = list(self.poll_calls)
        layout = DrawRecorder()
        with self.originals_untouchable():
            with mock.patch.object(self.tabs, '_collect', side_effect=AssertionError('draw scanned panels')):
                with mock.patch.object(self.tabs, '_register_anchor', side_effect=AssertionError('draw registered panels')):
                    self.tabs.draw_sidebar_tabs(layout, bpy.context)
        actions = [enabled for identifier, enabled in layout.calls if identifier == 'rr_helper.move_sidebar_tab']
        self.assertEqual(actions, [False, False])
        self.assertEqual(self.config.read_bytes(), before_file)
        self.assertEqual(self.anchor_state(), before_anchors)
        self.assertEqual(self.poll_calls, before_polls)
        self.assert_originals_exact()

    def test_edit_mesh_weights_attributes_mode_and_selection_are_exact(self):
        data = bpy.data.meshes.new('RR Sidebar Native Mesh')
        data.from_pydata([(0, 0, 0), (1, 0, 0.2), (1, 1, 0.4), (0, 1, 0)], [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new('RR Sidebar Native Object', data)
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
        before = edit_mesh_state(obj)
        with self.originals_untouchable():
            self.tabs.apply_order(self.desired())
            self.tabs.refresh_view()
            self.assertEqual(edit_mesh_state(obj), before)
            self.tabs.unregister()
        self.assertEqual(edit_mesh_state(obj), before)
        self.assert_originals_exact()

    def test_corrupt_json_is_not_overwritten_and_does_not_modify_anchors(self):
        self.tabs.apply_order(self.desired())
        self.config.write_bytes(b'{"schema": "rr-helper.sidebar-tabs", broken')
        before_file, before_anchors = self.config.read_bytes(), self.anchor_state()
        with self.originals_untouchable():
            with mock.patch.object(self.tabs, '_register_anchor', wraps=self.tabs._register_anchor) as register_spy:
                with mock.patch.object(self.tabs, '_unregister_anchor', wraps=self.tabs._unregister_anchor) as unregister_spy:
                    with self.assertRaises(self.tabs.SidebarTabsError):
                        self.tabs.apply_order(self.desired())
                    self.assertIsNone(self.tabs._startup())
                    self.assertEqual(register_spy.call_count, 0)
                    self.assertEqual(unregister_spy.call_count, 0)
        self.assertEqual(self.config.read_bytes(), before_file)
        self.assertEqual(self.anchor_state(), before_anchors)
        self.assertTrue(self.tabs.get_error())
        self.assert_originals_exact()

    def test_invalid_order_fails_before_any_anchor_or_file_mutation(self):
        for invalid in ([A, A], ['Item'], ['Tool'], ['View'], [''], [42], 'abc'):
            with self.subTest(invalid=invalid), self.originals_untouchable():
                with self.assertRaises(self.tabs.SidebarTabsError):
                    self.tabs.apply_order(invalid)
                self.assertFalse(self.config.exists())
                self.assertFalse(self.tabs._anchors)
                self.assert_originals_exact()

    def test_idempotent_lifecycle_bounded_startup_and_no_timer_handler_leaks(self):
        self.assertTrue(bpy.app.timers.is_registered(self.tabs._startup))
        classes = tuple(native_class(cls) for cls in self.tabs.CLASSES)
        self.assertEqual(classes, self.tabs.CLASSES)
        with self.originals_untouchable():
            self.tabs.register()
            self.assertEqual(tuple(native_class(cls) for cls in self.tabs.CLASSES), classes)
            self.assertEqual(handler_state(), self.baseline_handlers)
            bpy.app.timers.unregister(self.tabs._startup)
            delays = [self.tabs._startup() for _index in range(3)]
            self.assertIsNone(delays[-1])
            self.assertFalse(bpy.app.timers.is_registered(self.tabs._startup))
            self.tabs.unregister()
            self.tabs.unregister()
        self.assertFalse(bpy.app.timers.is_registered(self.tabs._startup))
        self.assertEqual(handler_state(), self.baseline_handlers)
        self.assertFalse(self.tabs._anchors)
        self.assertFalse(hasattr(bpy.types.WindowManager, self.tabs.VIEW_PROPERTY))
        self.assertFalse(hasattr(bpy.types.WindowManager, self.tabs.INDEX_PROPERTY))
        self.assertTrue(all(native_class(cls) is None for cls in self.tabs.CLASSES))
        self.assert_originals_exact()


def main():
    global OPTIONS, SUBJECT
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--module-path', type=Path, default=DEFAULT_MODULE)
    OPTIONS = parser.parse_args(arguments)
    module_path = OPTIONS.module_path.resolve(strict=True)
    spec = importlib.util.spec_from_file_location('rr_sidebar_tabs_native_test_subject', module_path)
    SUBJECT = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = SUBJECT
    spec.loader.exec_module(SUBJECT)
    report = {
        'schema': 'rr-helper.sidebar-tabs.native-anchor-regression.v1',
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'blender': bpy.app.version_string,
        'blender_build_hash': bpy.app.build_hash.decode('ascii', errors='replace'),
        'module_path': str(module_path),
        'module_sha256': hashlib.sha256(module_path.read_bytes()).hexdigest(),
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'autoexec_failed': bpy.app.autoexec_fail,
        'runtime_autoexec_preference': bpy.context.preferences.filepaths.use_scripts_auto_execute,
        'isolated_config': os.environ.get('BLENDER_USER_CONFIG', ''),
        'original_panel_scope': 'All original Panel identities/metadata/orders/hooks exact; wrappers reject original registration changes.',
        'owner_scope': 'Originals never re-registered; no private owner inference. Workspace filters untouched.',
        'native_visual_tab_order': 'Complete drawn strip not exposed through public RNA; separate actual GUI validation required.',
        'timer_scope': 'Native timer registration/removal; bounded callback drained directly, not an event-loop scheduling claim.',
        'scene_scope': 'Disposable EditMesh only; artist files and real preferences not opened or saved.',
        'rejected_predecessor': 'Archived rejected_reregister_tests.py passes are not anchor acceptance.',
    }
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SidebarTabsNativeTests))
    report.update({'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
                   'failures': [{'test': str(test), 'traceback': detail} for test, detail in result.failures],
                   'errors': [{'test': str(test), 'traceback': detail} for test, detail in result.errors],
                   'skipped': [{'test': str(test), 'reason': reason} for test, reason in result.skipped]})
    if OPTIONS.report:
        OPTIONS.report.parent.mkdir(parents=True, exist_ok=True)
        OPTIONS.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('RR_SIDEBAR_TABS_NATIVE ' + json.dumps(report, ensure_ascii=False), flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
