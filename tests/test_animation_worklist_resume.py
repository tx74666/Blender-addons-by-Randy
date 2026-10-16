"""A restored artist can choose an animation, with recovery on rejection."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_worklist.py'

class ResumeTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType('_resume_fixture')
        package.__path__ = []
        target = ModuleType('_resume_fixture.animation_worklist_target')
        target.managed = Mock(return_value=True)
        target.is_adopted = Mock(return_value=False)
        composition = ModuleType('_resume_fixture.animation_composition')
        composition.active = Mock(return_value=False)
        self.item = NS(rig=object(), new_animation=False)
        self.context = object()
        self.functions = {name: Mock() for name in ('_idle', '_item', '_check_baseline',
            'state', 'adopt_main_rig', 'activate', 'restore_main_rig')}
        self.functions['_item'].return_value = self.item
        env = {'__package__': package.__name__, **self.functions}
        node = next(n for n in ast.parse(SOURCE.read_bytes()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'resume_and_activate')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), env)
        self.run_operation = env['resume_and_activate']
        modules = patch.dict(sys.modules, {package.__name__: package,
            target.__name__: target, composition.__name__: composition})
        modules.start()
        self.addCleanup(modules.stop)
        self.target, self.composition = target, composition

    def test_explicit_animation_choice_resumes_then_activates(self):
        calls = []
        self.functions['adopt_main_rig'].side_effect = lambda _: calls.append('resume')
        self.functions['activate'].side_effect = lambda *args: calls.append('activate') or 'action'
        self.assertEqual(self.run_operation(self.context, 'walk', 'SOURCE'), 'action')
        self.assertEqual(calls, ['resume', 'activate'])
        self.functions['activate'].assert_called_once_with(self.context, 'walk', 'SOURCE')
        self.functions['restore_main_rig'].assert_not_called()

    def test_failed_activation_restores_the_preceding_artist_state(self):
        self.functions['activate'].side_effect = ValueError('rejected clip')
        with self.assertRaisesRegex(ValueError, 'rejected clip'):
            self.run_operation(self.context, 'walk')
        self.functions['restore_main_rig'].assert_called_once_with(self.context)

    def test_already_active_worklist_does_not_replace_recovery_capture(self):
        self.target.is_adopted.return_value = True
        self.run_operation(self.context, 'walk')
        self.functions['adopt_main_rig'].assert_not_called()

    def test_invalid_baseline_is_rejected_before_resuming(self):
        self.functions['_check_baseline'].side_effect = ValueError('source changed')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            self.run_operation(self.context, 'walk')
        self.functions['adopt_main_rig'].assert_not_called()

    def test_recovery_failure_remains_visible_and_does_not_claim_rollback(self):
        self.functions['activate'].side_effect = ValueError('rejected clip')
        self.functions['restore_main_rig'].side_effect = ValueError('rest changed')
        with self.assertRaisesRegex(RuntimeError, 'could not be restored: rest changed'):
            self.run_operation(self.context, 'walk')

if __name__ == '__main__':
    unittest.main()
