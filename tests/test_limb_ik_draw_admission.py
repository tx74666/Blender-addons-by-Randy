"""Presentation must not invoke deep admission; explicit strict callers still do."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/limb_ik.py'


class Layout:
    def __init__(self):
        self.labels = []

    def label(self, **values):
        self.labels.append(values)


class DisplayAdmissionTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType('_limb_display_admission_test')
        package.__path__ = []
        self.display = {'presentation_only': True, 'rigs': {}}
        self.reader = Mock(return_value=self.display)
        self.draw_modes = Mock()
        self.original = Mock(return_value=False)
        package.limb_ik_ui_state = NS(inventory=self.reader)
        package.controller_animation_ui = NS(draw_current_frame_modes=self.draw_modes)
        package.body_original_mode = NS(active=self.original)
        self.patcher = patch.dict(sys.modules, {package.__name__: package})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        tree = ast.parse(SOURCE.read_bytes())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in {'_draw_body_limb_modes', '_active_limb_target'}]
        self.validator = Mock(side_effect=AssertionError('deep admission reached from display'))
        self.rig = NS(type='ARMATURE')
        self.target = NS(name='CTRL_hand_IK.L', bone={'character_designer_limb_ik_role': 'HAND_IK'})
        self.context = NS(active_object=self.rig, mode='POSE')
        self.visual = Mock(return_value=(self.rig, self.target))
        namespace = {'__name__': package.__name__, '__package__': package.__name__,
                     'LimbIKError': ValueError, '_validate_inventory': self.validator,
                     '_active_control_visual': self.visual,
                     'ROLE_KEY': 'character_designer_limb_ik_role'}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.draw = namespace['_draw_body_limb_modes']
        self.resolve = namespace['_active_limb_target']

    def test_daily_draw_routes_only_presentation(self):
        layout = Layout()
        self.assertIs(self.draw(layout, self.context, self.rig), self.display)
        self.reader.assert_called_once_with(self.rig)
        self.draw_modes.assert_called_once_with(layout, self.context, self.rig, self.display,
                                                original=False, enabled=True)
        self.validator.assert_not_called()

    def test_original_draw_does_not_read_control_or_deep_inventory(self):
        self.original.return_value = True
        layout = Layout()
        self.assertIsNone(self.draw(layout, self.context, self.rig))
        self.reader.assert_not_called()
        self.validator.assert_not_called()
        self.assertIsNone(self.draw_modes.call_args.args[3])

    def test_unavailable_display_reports_without_deep_fallback(self):
        self.reader.side_effect = ValueError('Ambiguous controls')
        layout = Layout()
        self.assertIsNone(self.draw(layout, self.context, self.rig))
        self.assertEqual(layout.labels, [{'text': 'Ambiguous controls', 'icon': 'ERROR'}])
        self.draw_modes.assert_not_called()
        self.validator.assert_not_called()

    def test_target_rotation_nonstrict_routes_only_presentation(self):
        entry = {'target': NS(name=self.target.name)}
        self.display['rigs'][('ARM', 'L')] = entry
        self.assertEqual(self.resolve(self.context), (self.rig, self.target, self.display, entry))
        self.visual.assert_called_once_with(self.context, strict=False)
        self.reader.assert_called_once_with(self.rig)
        self.validator.assert_not_called()

    def test_target_rotation_invalid_display_never_retries_deep(self):
        self.reader.side_effect = ValueError('Control changed')
        self.assertIsNone(self.resolve(self.context))
        self.validator.assert_not_called()

    def test_target_rotation_explicit_strict_refusal_is_preserved(self):
        self.validator.side_effect = ValueError('Rest proof refused')
        with self.assertRaisesRegex(ValueError, 'Rest proof refused'):
            self.resolve(self.context, strict=True)
        self.validator.assert_called_once_with(self.rig)
        self.reader.assert_not_called()
        self.visual.assert_called_once_with(self.context, strict=True)

    def test_target_rotation_strict_uses_real_admission_result(self):
        entry = {'target': NS(name=self.target.name)}
        inventory = {'rigs': {('ARM', 'L'): entry}, 'strict_owner_proof': object()}
        self.validator.side_effect = None
        self.validator.return_value = inventory
        self.assertEqual(self.resolve(self.context, strict=True),
                         (self.rig, self.target, inventory, entry))
        self.reader.assert_not_called()

    def test_panel_source_does_not_admit_on_draw_and_reset_stays_strict(self):
        tree = ast.parse(SOURCE.read_bytes())
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        panel = next(n for n in classes['CHARACTERDESIGNER_PT_limb_ik'].body
                     if isinstance(n, ast.FunctionDef) and n.name == 'draw')
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                             and n.func.id == '_validate_inventory' for n in ast.walk(panel)))
        reset = next(n for n in classes['CHARACTERDESIGNER_OT_limb_ik_reset_target_rotation'].body
                     if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        calls = [n for n in ast.walk(reset) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == '_active_limb_target']
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(k.arg == 'strict' and isinstance(k.value, ast.Constant)
                            and k.value.value is True for k in calls[0].keywords))


if __name__ == '__main__':
    unittest.main()
