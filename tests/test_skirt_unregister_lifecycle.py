"""Actual Dress unregister with restricted-context and active-owner seams."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/skirt_wiggle.py'


class SkirtUnregisterTests(unittest.TestCase):
    def world(self, registered=False, sessions=False, error=None, restricted=False):
        names = ('_frame_pre', '_save_pre', '_save_post', '_lifecycle_pre', '_load_post', '_undo_post', '_timer', '_resume_saved')
        namespace = {name: (lambda: None) for name in names}
        mappings = {'frame_change_pre': '_frame_pre', 'save_pre': '_save_pre', 'save_post': '_save_post',
            'load_pre': '_lifecycle_pre', 'load_post': '_load_post', 'undo_pre': '_lifecycle_pre',
            'redo_pre': '_lifecycle_pre', 'undo_post': '_undo_post', 'redo_post': '_undo_post', 'render_pre': '_lifecycle_pre'}
        foreign = object()
        handlers = SimpleNamespace(**{key: [namespace[value], foreign] for key, value in mappings.items()})
        pending = [namespace['_timer'], namespace['_resume_saved']]
        calls = []
        def suspend(**kwargs):
            calls.append(kwargs)
            if error:
                raise RuntimeError(error)
        namespace.update(_REGISTERED=registered, _SESSIONS={1: object()} if sessions else {}, _SAVE_RESUME=['owned'],
            _TEARDOWN_REASON=None, _IN_TIMER=False, suspend=suspend, bpy=SimpleNamespace(
                context=SimpleNamespace() if restricted else SimpleNamespace(scene=object()),
                data=SimpleNamespace() if restricted else SimpleNamespace(objects=[], scenes=[]),
                app=SimpleNamespace(handlers=handlers, timers=SimpleNamespace(
                    is_registered=lambda callback: callback in pending, unregister=pending.remove,
                    register=lambda callback, **kwargs: pending.append(callback)))))
        names = {'unregister', '_scene_context_available', '_schedule_deferred_stop', '_finish_deferred_stop'}
        nodes = [n for n in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual(len(nodes), len(names))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        return namespace, calls, pending, handlers, foreign

    def test_failed_initialization_cleans_only_callbacks_without_suspending_artist(self):
        n, calls, pending, handlers, foreign = self.world()
        n['unregister']()
        self.assertEqual(calls, [])
        self.assertEqual(pending, [])
        self.assertTrue(all(value == [foreign] for value in vars(handlers).values()))
        self.assertEqual(n['_SAVE_RESUME'], [])
        self.assertFalse(n['_REGISTERED'])

    def test_registered_adapter_still_suspends_saved_automatic_state(self):
        n, calls, pending, _handlers, _foreign = self.world(registered=True)
        n['unregister']()
        self.assertEqual(len(calls), 1)
        self.assertIn('current shape is kept', calls[0]['reason'])
        self.assertEqual(pending, [])

    def test_partial_live_session_still_suspends_even_before_registered_flag(self):
        n, calls, pending, _handlers, _foreign = self.world(sessions=True)
        n['unregister']()
        self.assertEqual(len(calls), 1)
        self.assertEqual(pending, [])

    def test_active_unload_error_is_not_swallowed(self):
        n, calls, _pending, _handlers, _foreign = self.world(registered=True, error='owned suspend failed')
        with self.assertRaisesRegex(RuntimeError, 'owned suspend failed'):
            n['unregister']()
        self.assertEqual(len(calls), 1)

    def test_restricted_idle_unregister_cleans_callbacks_without_scene_access(self):
        n, calls, pending, handlers, foreign = self.world(registered=True, restricted=True)
        n['unregister']()
        self.assertEqual(calls, [])
        self.assertEqual(pending, [])
        self.assertTrue(all(value == [foreign] for value in vars(handlers).values()))
        self.assertIsNone(n['_TEARDOWN_REASON'])
        self.assertFalse(n['_REGISTERED'])

    def test_restricted_live_unregister_retains_ownership_until_safe_cleanup(self):
        n, calls, pending, handlers, foreign = self.world(registered=True, sessions=True, restricted=True)
        saved = n['_SESSIONS'][1]
        n['unregister']()
        self.assertIs(n['_SESSIONS'][1], saved)
        self.assertEqual(calls, [])
        self.assertEqual(pending, [n['_finish_deferred_stop']])
        self.assertTrue(all(value == [foreign] for value in vars(handlers).values()))
        self.assertEqual(n['_finish_deferred_stop'](), .05)
        n['unregister']()
        self.assertEqual(pending, [n['_finish_deferred_stop']], 'Only one deferred cleanup callback is owned')
        n['bpy'].context.scene = object()
        n['bpy'].data = SimpleNamespace(objects=[], scenes=[])
        stopped = []
        def stop(key, **kwargs):
            stopped.append((key, kwargs))
            n['_SESSIONS'].pop(key)
        n['_stop'] = stop
        self.assertIsNone(n['_finish_deferred_stop']())
        self.assertEqual(len(stopped), 1)
        self.assertIn('current shape is kept', stopped[0][1]['reason'])
        self.assertEqual(n['_SESSIONS'], {})
        self.assertIsNone(n['_TEARDOWN_REASON'])

    def test_deferred_stop_failure_preserves_exact_recovery_without_busy_loop(self):
        n, _calls, _pending, _handlers, _foreign = self.world(sessions=True, restricted=True)
        saved = n['_SESSIONS'][1]
        n['unregister']()
        n['bpy'].context.scene = object()
        n['bpy'].data = SimpleNamespace(objects=[], scenes=[])
        def fail(key, **kwargs):
            n['_SESSIONS'].pop(key)
            raise RuntimeError('native restore failed')
        n['_stop'] = fail
        with self.assertRaisesRegex(RuntimeError, 'native restore failed'):
            n['_finish_deferred_stop']()
        self.assertIs(n['_SESSIONS'][1], saved)
        self.assertIsNotNone(n['_TEARDOWN_REASON'])
        self.assertFalse(n['_IN_TIMER'])


if __name__ == '__main__':
    unittest.main()
