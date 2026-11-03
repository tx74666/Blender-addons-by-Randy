"""Global mode entry and mixed-limb diagnostics without a Blender process."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/limb_ik.py'


class Layout:
    def __init__(self, parent=None):
        self.labels = parent.labels if parent else []
        self.buttons = parent.buttons if parent else []
        self.enabled = True
    def label(self, *, text, **_):
        self.labels.append(text)
    def row(self, **_):
        return Layout(self)
    def operator(self, identifier, **options):
        op = NS(mode=None)
        self.buttons.append((identifier, options, op))
        return op


class BodyLimbScopeTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType('_body_scope_test')
        package.__path__ = []
        self.global_mode = Mock(return_value='IK')
        package.limb_ik_fk_batch = NS(mode_for_keys=self.global_mode)
        package.limb_ik_fk = NS(PROPERTY='ik_fk', mode_for_rig=lambda _rig, entry: entry['mode'])
        package.body_original_mode = NS(active=lambda _rig: False)
        self.patch = patch.dict(sys.modules, {package.__name__: package})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        tree = ast.parse(SOURCE.read_bytes())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_draw_body_limb_modes')
        self.inventory = {'rigs': {}}
        self.rig = NS(type='ARMATURE', pose=NS(bones={}))
        for kind in ('ARM', 'LEG'):
            for side in ('L', 'R'):
                name = kind + side
                self.inventory['rigs'][kind, side] = {'mode': 'IK', 'target': NS(name=name)}
                self.rig.pose.bones[name] = {'ik_fk': 1.}
        self.validate = Mock(return_value=self.inventory)
        namespace = {'__name__': package.__name__, '__package__': package.__name__,
                     '_validate_inventory': self.validate, 'LimbIKError': ValueError}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.draw = namespace[fn.name]
        self.context = NS(active_object=self.rig, mode='POSE')

    def test_primary_buttons_switch_all_limbs(self):
        layout = Layout()
        self.assertIs(self.draw(layout, self.context, self.rig), self.inventory)
        self.assertIn('All Arms / Legs', layout.labels[0])
        self.assertEqual([i for i, _options, _op in layout.buttons],
                         ['character_designer.body_ik_fk_switch'] * 2)
        self.assertEqual([op.mode for _i, _options, op in layout.buttons], ['IK', 'FK'])
        self.assertEqual([options['depress'] for _i, options, _op in layout.buttons], [True, False])
        self.validate.assert_called_once_with(self.rig)
        self.global_mode.assert_called_once_with(self.rig, inventory=self.inventory)

    def test_mixed_display_explains_each_limb_using_same_inventory(self):
        self.global_mode.return_value = 'MIXED'
        self.inventory['rigs']['ARM', 'R']['mode'] = 'FK'
        self.inventory['rigs']['LEG', 'L']['mode'] = 'BLEND'
        self.rig.pose.bones['LEGL']['ik_fk'] = .5
        layout = Layout()
        self.draw(layout, self.context, self.rig)
        self.assertTrue(any('L: IK / R: FK' in text for text in layout.labels))
        self.assertTrue(any('L: Blend 50% / R: IK' in text for text in layout.labels))
        self.assertTrue(all(not options['depress'] for _i, options, _op in layout.buttons))
        self.validate.assert_called_once_with(self.rig)

    def test_no_armature_does_not_offer_a_mutation(self):
        layout = Layout()
        self.assertIsNone(self.draw(layout, self.context, None))
        self.assertEqual(layout.buttons, [])
        self.validate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
