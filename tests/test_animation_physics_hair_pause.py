"""Current Hair2 pause boundary; real runtime/native acceptance is separate."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer/hair_wiggle2_adapter.py'
PACKAGE = '_animation_physics_hair2_pause_fixture'


class HairWiggleError(ValueError):
    pass


def load_boundary():
    names = {'_fail', 'status', 'set_preview_paused', 'start_preview', 'resume_preview'}
    tree = ast.parse(SOURCE.read_text(encoding='utf8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    ns = dict(__name__=PACKAGE + '.hair_wiggle2_adapter', __package__=PACKAGE,
              HairWiggleError=HairWiggleError, _SESSION=None, _STOPPING=False, _LAST_REASON='',
              BACKEND_VERSION='2.2.4', MAPPING_VERSION=2,
              _LAST_SOURCE_POINTER=None, _HISTORY_PENDING=False, _RECOVERY_KEY='test_marker',
              _changed=Mock(return_value=''), _backend=Mock(), _start_playback=Mock(),
              _animation_same=Mock(return_value=True),
              _write_marker=Mock(), runtime=NS(set_paused=Mock(), _live=Mock(return_value='before'), _rollback=Mock()))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


class ReadOnlyOwner:
    def __init__(self, **values):
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AssertionError('Unexpected author/context write: ' + name)

    def get(self, key, default=None):
        return default

    def pop(self, key, default=None):
        return default

    def as_pointer(self):
        return id(self)


class HairPauseBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_boundary()
        self.source = ReadOnlyOwner(name='Hair')
        self.scene = ReadOnlyOwner(frame_current=37, frame_subframe=.25)
        self.screen = ReadOnlyOwner(is_animation_playing=True)
        self.context = ReadOnlyOwner(scene=self.scene, screen=self.screen)
        self.lease = {'paused': False}
        self.session = dict(source=self.source, scene=self.scene, ids=('left', 'right'),
                            started_playback=True, lease=self.lease, physics_paused=False,
                            source_pointer=self.source.as_pointer(), actions='associations proof')
        self.ns['_SESSION'] = self.session
        self.runtime = self.ns['runtime']
        self.runtime.set_paused.side_effect = lambda lease, value: lease.update(paused=value)

    def pause(self, value, *, source=None, context=None):
        return self.ns['set_preview_paused'](context or self.context, source or self.source, value)

    def assert_context(self):
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (37, .25))
        self.assertTrue(self.screen.is_animation_playing)
        self.ns['_start_playback'].assert_not_called()

    def test_pause_resume_changes_only_own_lease_and_marker(self):
        peer = {'paused': False, 'pose': (1, 2, 3)}
        for value in (True, False):
            result = self.pause(value)
            self.assertEqual(result['paused'], value)
            self.assertEqual(self.lease['paused'], value)
            self.assertEqual(peer, {'paused': False, 'pose': (1, 2, 3)})
            self.assert_context()
        self.assertEqual(self.runtime.set_paused.call_args_list[0].args, (self.lease, True))

    def test_repeated_pause_and_resume_are_idempotent(self):
        for value in (False, False, True, True, False):
            self.assertEqual(self.pause(value)['paused'], value)
        self.assert_context()

    def test_wrong_source_same_name_refuses_before_native_write(self):
        with self.assertRaises(HairWiggleError):
            self.pause(True, source=ReadOnlyOwner(name='Hair'))
        self.runtime.set_paused.assert_not_called()
        self.ns['_changed'].assert_not_called()

    def test_wrong_scene_refuses_before_native_write(self):
        with self.assertRaises(HairWiggleError):
            self.pause(True, context=ReadOnlyOwner(scene=object()))
        self.runtime.set_paused.assert_not_called()
        self.ns['_changed'].assert_not_called()

    def test_no_session_refuses_and_does_not_claim_paused(self):
        self.ns['_SESSION'] = None
        with self.assertRaises(HairWiggleError):
            self.pause(True)
        self.assertFalse(self.ns['status']()['paused'])
        self.assertFalse(self.ns['status']()['active'])
        self.runtime.set_paused.assert_not_called()

    def test_stopping_session_refuses(self):
        self.ns['_STOPPING'] = True
        with self.assertRaises(HairWiggleError):
            self.pause(True)
        self.runtime.set_paused.assert_not_called()

    def test_non_boolean_pause_refuses(self):
        for value in (0, 1, None, 'false', (), []):
            with self.subTest(value=value), self.assertRaises(HairWiggleError):
                self.pause(value)
        self.runtime.set_paused.assert_not_called()

    def test_changed_registry_refuses_before_runtime(self):
        self.ns['_changed'].return_value = 'Hair Rest changed'
        with self.assertRaisesRegex(HairWiggleError, 'Hair Rest'):
            self.pause(True)
        self.runtime.set_paused.assert_not_called()

    def test_changed_proof_exception_preserves_marker(self):
        self.ns['_changed'].side_effect = ValueError('invalid registry')
        with self.assertRaisesRegex(ValueError, 'invalid registry'):
            self.pause(True)
        self.assertFalse(self.session['physics_paused'])
        self.runtime.set_paused.assert_not_called()

    def test_runtime_rejects_foreign_change_without_committing_marker(self):
        self.runtime.set_paused.side_effect = ValueError('foreign mute changed')
        with self.assertRaisesRegex(ValueError, 'foreign mute'):
            self.pause(True)
        self.assertFalse(self.session['physics_paused'])
        self.assert_context()

    def test_native_setter_failure_does_not_commit_marker(self):
        self.runtime.set_paused.side_effect = RuntimeError('rolled back own setter')
        with self.assertRaisesRegex(RuntimeError, 'own setter'):
            self.pause(True)
        self.assertFalse(self.session['physics_paused'])
        self.assert_context()

    def test_changed_associations_refuse_before_owned_native_mutation(self):
        self.ns['_animation_same'].return_value = False
        with self.assertRaisesRegex(HairWiggleError, 'associations changed'):
            self.pause(True)
        self.runtime.set_paused.assert_not_called()
        self.runtime._live.assert_not_called()
        self.assertFalse(self.session['physics_paused'])

    def test_status_uses_live_session_only(self):
        self.assertEqual(self.ns['status']()['version'], '2.2.4')
        self.pause(True)
        self.assertTrue(self.ns['status']()['paused'])
        self.ns['_SESSION'] = None
        self.assertFalse(self.ns['status']()['paused'])

    def gate_fixture(self):
        package = ModuleType(PACKAGE)
        package.__path__ = []
        gate = ModuleType(PACKAGE + '.animation_physics')
        gate.require_enabled = Mock()
        return package, gate

    def test_start_gate_rejects_before_backend_or_lease_assignment(self):
        self.ns['_SESSION'] = None
        package, gate = self.gate_fixture()
        gate.require_enabled.side_effect = HairWiggleError('Physics disabled')
        with patch.dict(sys.modules, {PACKAGE: package, gate.__name__: gate}):
            with self.assertRaisesRegex(HairWiggleError, 'Physics disabled'):
                self.ns['start_preview'](self.context, self.source, {}, {}, ('left',))
        self.ns['_backend'].assert_not_called()
        self.assertIsNone(self.ns['_SESSION'])

    def test_explicit_resume_plays_but_permission_resume_does_not(self):
        package, gate = self.gate_fixture()
        with patch.dict(sys.modules, {PACKAGE: package, gate.__name__: gate}):
            self.ns['resume_preview'](self.context, self.source)
        self.ns['_start_playback'].assert_called_once_with(self.context, self.session)
        gate.require_enabled.assert_called_once_with(self.source, context=self.context, component='HAIR')

    def test_explicit_resume_playback_failure_rolls_back_only_owned_pause(self):
        package, gate = self.gate_fixture()
        self.session['physics_paused'] = True
        self.lease['paused'] = True
        self.runtime._rollback.side_effect = lambda lease, saved: lease.update(paused=True)
        self.ns['_start_playback'].side_effect = RuntimeError('GUI playback refused')
        with patch.dict(sys.modules, {PACKAGE: package, gate.__name__: gate}):
            with self.assertRaisesRegex(RuntimeError, 'GUI playback refused'):
                self.ns['resume_preview'](self.context, self.source)
        self.assertTrue(self.session['physics_paused'])
        self.assertTrue(self.lease['paused'])
        self.assertTrue(self.session['started_playback'])
        self.runtime._rollback.assert_called_once_with(self.lease, 'before')
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (37, .25))
        self.assertTrue(self.screen.is_animation_playing)


if __name__ == '__main__':
    unittest.main()
