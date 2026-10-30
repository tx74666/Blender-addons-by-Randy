"""Portable Undo/Redo callback safety checks of the actual runtime functions.

These fail if a history callback evaluates native data or runs preview rollback
before Blender supplies the next evaluated graph. Native geometry QA is separate.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/forearm_twist.py'
RECORD_KEY = 'character_designer_forearm_twist_v1'
PREVIEW_KEY = 'character_designer_forearm_twist_preview_v1'
LOCK_KEY = 'character_designer_twist_render_lock'
EXPECTED_HANDLERS = (
    ('depsgraph_update_post', '_graph_post'), ('frame_change_post', '_frame_post'),
    ('animation_playback_pre', '_playback_pre'), ('animation_playback_post', '_playback_post'),
    ('load_pre', '_restore_preview'), ('save_pre', '_restore_preview'),
    ('undo_pre', '_restore_preview'), ('redo_pre', '_redo_pre'), ('load_post', '_load_post'),
    ('undo_post', '_undo_post'), ('redo_post', '_undo_post'))


class ID(dict):
    def __init__(self, name, kind='MESH', **kwargs):
        super().__init__()
        self.name, self.type = name, kind
        self.__dict__.update(kwargs)


class Timers:
    def __init__(self):
        self.callbacks = []

    def is_registered(self, fn):
        return fn in self.callbacks

    def register(self, fn, **kwargs):
        self.callbacks.append(fn)

    def unregister(self, fn):
        self.callbacks.remove(fn)


def product():
    names = {'update_runtime', '_graph_post', '_frame_post', '_load_post',
             '_playback_token', '_playback_pre', '_playback_post',
             '_redo_pre', '_undo_post', '_complete_history_recovery', '_recover_previews',
             'finish_test', '_cleanup_runtime_scene', '_scene_data_available',
             '_complete_runtime_lifecycle', '_request_runtime_lifecycle',
             'register_forearm_twist_runtime', 'unregister_forearm_twist_runtime'}
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
             or isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == '_HANDLERS'
                     for target in node.targets)]
    scene = ID('Scene', 'SCENE', objects=[], render=NS(use_lock_interface=False))
    graph_get = Mock(side_effect=AssertionError('Native graph requested inside history callback'))
    handlers = NS(**{name: [] for name, _callback in EXPECTED_HANDLERS})
    context = NS(scene=scene, evaluated_depsgraph_get=graph_get,
                 view_layer=NS(update=Mock(side_effect=AssertionError('Native view update requested'))))
    bpy = NS(context=context, data=NS(objects=[], scenes=[scene]),
             app=NS(background=True, handlers=handlers, timers=Timers()))
    namespace = {'bpy': bpy, 'json': json, 'persistent': lambda fn: fn,
                 'RECORD_KEY': RECORD_KEY, 'PREVIEW_KEY': PREVIEW_KEY, 'LOCK_KEY': LOCK_KEY,
                 '_SESSION': None, '_BUSY': False, '_CACHE': {}, '_OUTPUT_CACHE': {},
                 '_KEY_REFERENCES': {}, '_ERRORS': {}, '_DRAW_HANDLE': None,
                 '_RUNTIME_REGISTERED': False, '_INITIALIZE_PENDING': False,
                 '_SCENE_CLEANUP_PENDING': False, '_SCENE_STATE_ACTIVE': False,
                 '_HISTORY_RECOVERY_PENDING': False, '_PLAYBACK_PREPARED': {},
                 'validation_cache': NS(clear=Mock()),
                 '_remember_named_keys': Mock(), '_set_render_lock': Mock(),
                 '_calculate_object': Mock(), '_restore_preview': Mock(),
                 '_finish_test_restore': Mock(), 'traceback': NS(print_exc=Mock())}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace, bpy, scene, graph_get


class HistorySafetyTests(unittest.TestCase):
    def owner(self, bpy, scene):
        owner = ID('Body', data=NS(shape_keys=None))
        owner[RECORD_KEY] = '{}'
        scene.objects.append(owner)
        bpy.data.objects.append(owner)
        return owner

    def test_undo_discards_references_without_evaluation_or_preview_mutation(self):
        ns, bpy, scene, graph_get = product()
        self.owner(bpy, scene)
        ns['_SESSION'] = {'stale': object()}
        for name in ('_CACHE', '_OUTPUT_CACHE', '_KEY_REFERENCES', '_ERRORS', '_PLAYBACK_PREPARED'):
            ns[name]['old'] = object()
        ns['_recover_previews'] = Mock(side_effect=AssertionError('Rollback ran during native Undo'))
        ns['_INITIALIZE_PENDING'] = True
        ns['_undo_post'](None)
        self.assertTrue(ns['_HISTORY_RECOVERY_PENDING'])
        self.assertIsNone(ns['_SESSION'])
        self.assertTrue(ns['_INITIALIZE_PENDING'])
        for name in ('_CACHE', '_OUTPUT_CACHE', '_KEY_REFERENCES', '_ERRORS', '_PLAYBACK_PREPARED'):
            self.assertEqual(ns[name], {})
        ns['_remember_named_keys'].assert_not_called()
        ns['_calculate_object'].assert_not_called()
        graph_get.assert_not_called()

    def test_redo_pre_only_clears_play_admission_without_scene_or_history_mutation(self):
        ns, bpy, scene, graph_get = product()
        self.owner(bpy, scene)
        session = {'live': object()}
        ns['_SESSION'] = session
        ns['_PLAYBACK_PREPARED']['old'] = object()
        old = {}
        for name in ('_CACHE', '_OUTPUT_CACHE', '_KEY_REFERENCES', '_ERRORS'):
            ns[name]['old'] = object()
            old[name] = dict(ns[name])
        ns['_recover_previews'] = Mock(side_effect=AssertionError('Preview rollback ran before native Redo'))
        ns['finish_test'] = Mock(side_effect=AssertionError('Preview finish ran before native Redo'))
        ns['_redo_pre'](None)
        self.assertEqual(ns['_PLAYBACK_PREPARED'], {})
        self.assertIs(ns['_SESSION'], session)
        self.assertFalse(ns['_HISTORY_RECOVERY_PENDING'])
        for name, values in old.items():
            self.assertEqual(ns[name], values)
        ns['_recover_previews'].assert_not_called()
        ns['finish_test'].assert_not_called()
        ns['_remember_named_keys'].assert_not_called()
        ns['_calculate_object'].assert_not_called()
        graph_get.assert_not_called()
        bpy.context.view_layer.update.assert_not_called()

    def test_no_graph_keeps_recovery_pending_without_requesting_one(self):
        ns, bpy, scene, graph_get = product()
        self.owner(bpy, scene)
        ns['_undo_post'](None)
        ns['update_runtime'](scene)
        ns['_frame_post'](scene)
        self.assertTrue(ns['_HISTORY_RECOVERY_PENDING'])
        ns['_calculate_object'].assert_not_called()
        graph_get.assert_not_called()

    def test_supplied_graph_resumes_output_once_for_graph_and_frame_callbacks(self):
        for callback in ('_graph_post', '_frame_post'):
            with self.subTest(callback=callback):
                ns, bpy, scene, graph_get = product()
                owner = self.owner(bpy, scene)
                owner.output = 'old correction'
                graph = NS(correction='current correction')
                ns['_calculate_object'].side_effect = lambda obj, current: setattr(obj, 'output', current.correction)
                ns['_undo_post'](None)
                ns[callback](scene, graph)
                self.assertEqual(owner.output, 'current correction')
                ns['_calculate_object'].assert_called_once_with(owner, graph)
                ns['_remember_named_keys'].assert_called_once_with()
                self.assertFalse(ns['_HISTORY_RECOVERY_PENDING'])
                self.assertFalse(ns['_BUSY'])
                graph_get.assert_not_called()

    def test_preview_rollback_waits_for_safe_graph_then_skips_stale_graph(self):
        ns, bpy, scene, graph_get = product()
        owner = self.owner(bpy, scene)
        owner[PREVIEW_KEY] = json.dumps({'armature_name': 'Rig', 'side': 'L'})
        bpy.data.objects = {'Body': owner, 'Rig': ID('Rig', 'ARMATURE')}
        # _recover_previews scans all IDs, as bpy.data.objects does.
        class Objects(dict):
            def __iter__(self):
                return iter(self.values())
        bpy.data.objects = Objects(bpy.data.objects)
        ns['_preview_key_name'] = lambda obj, marker: 'Owned preview'
        ns['_managed_key'] = lambda *args, **kwargs: None

        def restore(context, session, confirm):
            self.assertFalse(confirm)
            self.assertIs(session['mesh'], owner)
            owner.pop(PREVIEW_KEY)
            # A mode/key restoration can synchronously trigger another graph.
            ns['_graph_post'](scene, object())
            ns['_SESSION'] = None

        ns['_finish_test_restore'].side_effect = restore
        ns['_undo_post'](None)
        ns['_finish_test_restore'].assert_not_called()
        stale, fresh = object(), object()
        ns['_graph_post'](scene, stale)
        ns['_finish_test_restore'].assert_called_once()
        ns['_calculate_object'].assert_not_called()
        self.assertNotIn(PREVIEW_KEY, owner)
        ns['_graph_post'](scene, fresh)
        ns['_calculate_object'].assert_called_once_with(owner, fresh, _playback_scene=scene)
        graph_get.assert_not_called()
        bpy.context.view_layer.update.assert_not_called()

    def test_scene_without_owned_mesh_never_requests_native_graph(self):
        ns, bpy, scene, graph_get = product()
        scene.objects.extend([ID('Rig', 'ARMATURE'), ID('Ordinary mesh')])
        ns['update_runtime'](scene)
        graph_get.assert_not_called()
        ns['_calculate_object'].assert_not_called()

    def test_load_replaces_pending_history_and_register_unregister_leave_no_timer(self):
        ns, bpy, scene, graph_get = product()
        ns['_undo_post'](None)
        ns['_load_post'](None)
        self.assertFalse(ns['_HISTORY_RECOVERY_PENDING'])
        ns['register_forearm_twist_runtime']()
        self.assertTrue(ns['_RUNTIME_REGISTERED'])
        self.assertTrue(ns['_SCENE_STATE_ACTIVE'])
        self.assertEqual(ns['_HANDLERS'], tuple((name, ns[callback]) for name, callback in EXPECTED_HANDLERS))
        for name, callback in ns['_HANDLERS']:
            self.assertEqual(getattr(bpy.app.handlers, name).count(callback), 1)
        ns['_undo_post'](None)
        ns['unregister_forearm_twist_runtime']()
        self.assertFalse(ns['_HISTORY_RECOVERY_PENDING'])
        self.assertFalse(ns['_RUNTIME_REGISTERED'])
        self.assertFalse(ns['_SCENE_STATE_ACTIVE'])
        self.assertEqual(bpy.app.timers.callbacks, [])
        for name, callback in ns['_HANDLERS']:
            self.assertNotIn(callback, getattr(bpy.app.handlers, name))
        graph_get.assert_not_called()

    def test_restricted_registration_rollback_cancels_initialization_and_history(self):
        ns, bpy, scene, graph_get = product()
        bpy.data = NS()
        ns['register_forearm_twist_runtime']()
        self.assertTrue(ns['_INITIALIZE_PENDING'])
        self.assertEqual(bpy.app.timers.callbacks, [ns['_complete_runtime_lifecycle']])
        ns['_undo_post'](None)
        ns['unregister_forearm_twist_runtime']()
        self.assertFalse(ns['_INITIALIZE_PENDING'])
        self.assertFalse(ns['_HISTORY_RECOVERY_PENDING'])
        self.assertEqual(bpy.app.timers.callbacks, [])
        graph_get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
