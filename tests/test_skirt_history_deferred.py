"""Dress history callbacks must not touch partially restored native RNA."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/skirt_wiggle.py'


class ForbiddenNative:
    def __getattribute__(self, name):
        raise AssertionError('History callback accessed native RNA: ' + name)


class NativeBoundary:
    def __init__(self, app):
        self.app = app

    @property
    def context(self):
        raise AssertionError('History callback accessed bpy.context')

    @property
    def data(self):
        raise AssertionError('History callback accessed bpy.data')


class Source(dict):
    type = 'MESH'

    def as_pointer(self):
        return 17


def subject():
    pending, registrations = set(), []

    def register(callback, **kwargs):
        pending.add(callback)
        registrations.append((callback, kwargs))

    timers = SimpleNamespace(is_registered=lambda callback: callback in pending,
                             register=register, unregister=pending.remove)
    namespace = {
        'persistent': lambda function: function, '_REGISTERED': True,
        '_SESSIONS': {1: {'source': ForbiddenNative(), 'bone_refs': ForbiddenNative()}},
        '_SAVE_RESUME': [ForbiddenNative()], '_BACKEND_CACHE': ForbiddenNative(),
        '_BACKEND_CACHE_TIME': 123.0, '_HISTORY_RECOVERY_PENDING': False,
        'bpy': NativeBoundary(SimpleNamespace(timers=timers)), 'STATE_KEY': 'dress_state',
        '_scene_context_available': Mock(return_value=True),
        '_recover_source': Mock(), 'mode': Mock(return_value='AUTOMATIC'),
        '_start': Mock(), '_state': Mock(return_value={'mode': 'AUTOMATIC', 'reason': ''}),
        'available': Mock(return_value={'available': True, 'reason': ''}),
    }
    names = {'_history_pre', '_undo_post', '_load_post', '_queue_resume_saved', '_resume_saved', 'status'}
    nodes = [node for node in ast.parse(SOURCE.read_text(encoding='utf-8')).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace, pending, registrations


class SkirtHistoryDeferredTests(unittest.TestCase):
    def test_pre_stops_active_leases_without_scanning_scene_or_changing_preference(self):
        namespace, _pending, _registrations = subject()
        namespace['_stop'] = Mock(side_effect=lambda key, **kwargs: namespace['_SESSIONS'].pop(key))
        namespace['_history_pre']()
        namespace['_stop'].assert_called_once_with(1, reason='', preserve_preference=True)
        namespace['_recover_source'].assert_not_called()
        namespace['_start'].assert_not_called()

    def test_idle_pre_does_not_access_scene_or_restore_any_source(self):
        namespace, _pending, _registrations = subject()
        namespace['_SESSIONS'].clear()
        namespace['_stop'] = Mock()
        namespace['_history_pre']()
        namespace['_stop'].assert_not_called()
        namespace['_recover_source'].assert_not_called()

    def test_post_discards_stale_python_refs_without_any_native_context_access(self):
        namespace, pending, registrations = subject()
        namespace['_undo_post']()
        self.assertEqual(namespace['_SESSIONS'], {})
        self.assertEqual(namespace['_SAVE_RESUME'], [])
        self.assertIsNone(namespace['_BACKEND_CACHE'])
        self.assertEqual(namespace['_BACKEND_CACHE_TIME'], 0.0)
        self.assertTrue(namespace['_HISTORY_RECOVERY_PENDING'])
        self.assertFalse(pending)
        self.assertFalse(registrations)
        namespace['_recover_source'].assert_not_called()
        namespace['_start'].assert_not_called()

    def test_repeated_history_cancels_only_its_pending_callback(self):
        namespace, pending, registrations = subject()
        foreign = lambda: None
        pending.update((foreign, namespace['_resume_saved']))
        namespace['_undo_post']()
        namespace['_SESSIONS'][2] = ForbiddenNative()
        namespace['_SAVE_RESUME'] = [ForbiddenNative()]
        namespace['_undo_post']()
        self.assertEqual(pending, {foreign})
        self.assertFalse(registrations)
        self.assertEqual(namespace['_SESSIONS'], {})
        self.assertEqual(namespace['_SAVE_RESUME'], [])

    def test_disabled_adapter_neither_schedules_nor_reads_native_state(self):
        namespace, pending, _registrations = subject()
        namespace['_REGISTERED'] = False
        namespace['_undo_post']()
        self.assertFalse(pending)
        self.assertIsNone(namespace['_resume_saved']())
        self.assertFalse(namespace['_HISTORY_RECOVERY_PENDING'])

    def test_dispatched_history_timer_returns_before_any_native_context_access(self):
        namespace, _pending, _registrations = subject()
        namespace['_undo_post']()
        self.assertIsNone(namespace['_resume_saved']())
        self.assertTrue(namespace['_HISTORY_RECOVERY_PENDING'])
        namespace['_scene_context_available'].assert_not_called()
        namespace['_recover_source'].assert_not_called()
        namespace['_start'].assert_not_called()

    def test_status_keeps_automatic_preference_and_does_not_claim_native_flags_paused(self):
        namespace, _pending, _registrations = subject()
        namespace['_undo_post']()
        source = Source(dress_state='opaque saved Automatic state')
        before = dict(source)
        result = namespace['status'](None, source)
        self.assertEqual(result['mode'], 'AUTOMATIC')
        self.assertFalse(result['running'])
        self.assertIn('choose Automatic or Manual', result['message'])
        self.assertIn('Saved Wiggle motion may still run', result['message'])
        self.assertEqual(dict(source), before)
        namespace['_recover_source'].assert_not_called()
        namespace['_start'].assert_not_called()
        namespace['_SESSIONS'][source.as_pointer()] = {}
        self.assertTrue(namespace['status'](None, source)['running'])

    def test_interface_lock_defers_without_enumerating_scene_objects(self):
        namespace, _pending, _registrations = subject()
        namespace['_load_post']()
        namespace['bpy'] = SimpleNamespace(context=SimpleNamespace(
            window_manager=SimpleNamespace(is_interface_locked=True), scene=ForbiddenNative()))
        self.assertEqual(namespace['_resume_saved'](), .05)
        self.assertFalse(namespace['_HISTORY_RECOVERY_PENDING'])
        namespace['_recover_source'].assert_not_called()

    def test_file_load_timer_keeps_its_separate_fresh_source_recovery(self):
        namespace, _pending, _registrations = subject()
        namespace['_undo_post']()
        namespace['_load_post']()
        automatic, manual = Source(dress_state='automatic'), Source(dress_state='manual')
        context = SimpleNamespace(window_manager=SimpleNamespace(is_interface_locked=False),
                                  scene=SimpleNamespace(objects=[automatic, manual]))
        namespace['bpy'] = SimpleNamespace(context=context)
        namespace['mode'].side_effect = lambda source: 'AUTOMATIC' if source is automatic else 'MANUAL'
        self.assertIsNone(namespace['_resume_saved']())
        self.assertFalse(namespace['_HISTORY_RECOVERY_PENDING'])
        self.assertEqual([call.args[0] for call in namespace['_recover_source'].call_args_list],
                         [automatic, manual])
        namespace['_start'].assert_called_once_with(context, automatic)


if __name__ == '__main__':
    unittest.main()
