"""Actual Dress unregister with restricted-context and active-owner seams."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/skirt_wiggle.py'


class SkirtUnregisterTests(unittest.TestCase):
    def world(self, registered=False, sessions=False, error=None):
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
            suspend=suspend, bpy=SimpleNamespace(context=SimpleNamespace(), app=SimpleNamespace(handlers=handlers,
                timers=SimpleNamespace(is_registered=lambda callback: callback in pending, unregister=pending.remove))))
        nodes = [n for n in ast.parse(SOURCE.read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef) and n.name == 'unregister']
        self.assertEqual(len(nodes), 1)
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


if __name__ == '__main__':
    unittest.main()
