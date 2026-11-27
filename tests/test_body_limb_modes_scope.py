"""The limb mode picker belongs to Animation. Rig only draws setup and recovery."""
import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import sys
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/limb_ik.py'


class BodyLimbScopeTests(unittest.TestCase):
    def setUp(self):
        pkg = ModuleType('_rig_page_draw_test')
        pkg.__path__ = []
        self.context = NS(mode='POSE')
        self.layout = Mock()
        self.settings = NS(show_body_setup_advanced=False)
        rig = object()
        self.original = Mock(return_value=False)
        self.calibrate = Mock(return_value=False)
        self.inventory = Mock(side_effect=AssertionError('Rig read limb modes during draw'))
        self.capture = Mock()
        pkg.body_setup_ui = NS()
        pkg.body_original_mode = NS(active=self.original, display=NS(character_rig=lambda ctx:rig))
        pkg.body_calibration_ui = NS(draw=self.calibrate)
        pkg.limb_ik_ui_state = NS(inventory=self.inventory)
        self.modules = patch.dict(sys.modules, {pkg.__name__: pkg,
            pkg.__name__ + '.control_pose_capture': NS(draw=self.capture)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        cls = next(n for n in ast.parse(SOURCE.read_bytes()).body
                   if isinstance(n, ast.ClassDef) and n.name == 'CHARACTERDESIGNER_PT_limb_ik')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'draw')
        namespace = dict(__package__=pkg.__name__, _settings=lambda ctx:self.settings)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.draw = lambda: namespace['draw'](NS(layout=self.layout), self.context)

    def test_body_setup_keeps_recovery_and_does_not_draw_mode_buttons(self):
        self.draw()
        self.capture.assert_called_once_with(self.layout, self.context)
        self.calibrate.assert_called_once_with(self.layout, self.context)
        self.assertEqual(self.layout.prop.call_args.args[1], 'show_body_setup_advanced')
        self.layout.row.assert_not_called()
        self.layout.operator.assert_not_called()
        self.inventory.assert_not_called()

    def test_original_bones_keeps_its_pose_entry_without_animation_controls(self):
        self.original.return_value = True
        self.draw()
        self.capture.assert_called_once_with(self.layout, self.context)
        self.calibrate.assert_not_called()
        self.inventory.assert_not_called()
        self.assertFalse(self.layout.mock_calls)
