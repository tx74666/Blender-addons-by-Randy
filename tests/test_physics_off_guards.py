"""Paused frame/timer callbacks must not retain deep running-solver work."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def boundary(file, names, **state):
    nodes = [item for item in ast.parse((ROOT / file).read_text(encoding='utf8')).body
             if isinstance(item, ast.FunctionDef) and item.name in names]
    for item in nodes:
        item.decorator_list = []
    state.update(_changed=Mock(return_value=''), _stop=Mock(), stop_preview=Mock())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), file, 'exec'), state)
    return state


class OffGuardTests(unittest.TestCase):
    def test_paused_hair_frames_and_timer_never_deep_scan_or_cleanup_author(self):
        scene = NS(frame_current=82)
        session = dict(scene=scene, physics_paused=True, lastframe=2)
        ns = boundary('hair_wiggle2_adapter.py', {'_frame_guard', '_mode_timer'},
                      _SESSION=session, _STOPPING=False, _GUARDS_REGISTERED=True)
        ns['_frame_guard'](scene)
        self.assertEqual(ns['_mode_timer'](), .1)
        self.assertEqual(session['lastframe'], 82)
        ns['_changed'].assert_not_called()
        ns['stop_preview'].assert_not_called()

    def test_paused_skirt_frames_and_timer_skip_deep_scan_but_running_peer_remains_guarded(self):
        scene = NS(frame_current=82)
        paused = dict(scene=scene, paused=True, frame=2)
        running = dict(scene=scene, paused=False, frame=81)
        ns = boundary('skirt_wiggle.py', {'_frame_pre', '_timer'},
                      _SESSIONS={'paused': paused, 'running': running})
        ns['_frame_pre'](scene)
        self.assertEqual(ns['_timer'](), .25)
        self.assertEqual(paused['frame'], 82)
        self.assertEqual(running['frame'], 82)
        self.assertEqual(ns['_changed'].call_count, 2)
        self.assertTrue(all(call.args == (running,) for call in ns['_changed'].call_args_list))
        ns['_stop'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
