"""Original Pole repair requires its live outer checkpoint and existing drivers."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/limb_ik_fk.py'


class OriginalPoleTransaction(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        names = {'repair_leg_poles', '_repair_original_leg_poles', '_require_existing_switches'}
        definitions = [node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name in names]
        self.package = ModuleType('_original_pole_test')
        self.package.__path__ = []
        self.check = Mock()
        self.atomic = Mock()
        self.package.body_original_owner = NS(_check_pose_capability=self.check)
        self.package.body_setup = NS(_atomic=self.atomic)
        modules = patch.dict(sys.modules, {self.package.__name__: self.package})
        modules.start()
        self.addCleanup(modules.stop)
        self.core = Mock(return_value={'repaired': [], 'changed': False})
        self.globals = {'__package__': self.package.__name__, '_error': ValueError,
                        'VERSION_KEY': 'version', 'VERSION': 1,
                        '_repair_leg_poles_with_transaction': self.core}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), self.globals)
        self.context, self.armature, self.cap, self.desired = NS(), NS(), object(), {'pose': object()}

    def invoke(self):
        return self.globals['_repair_original_leg_poles'](
            self.context, self.armature, desired_pose=self.desired, _original_pose=self.cap)

    def install_preflight_mock(self):
        self.drivers = Mock()
        self.globals['_require_existing_switches'] = self.drivers

    def install_mutation(self):
        self.mutate = Mock(return_value={'repaired': ['LEG:L'], 'changed': True})
        def run(context, armature, desired, transaction):
            return transaction(context, armature, self.mutate)
        self.core.side_effect = run

    def test_public_route_keeps_body_transaction_and_does_not_use_pose_admission(self):
        self.globals['repair_leg_poles'](self.context, self.armature, desired_pose=self.desired)
        self.core.assert_called_once_with(self.context, self.armature, self.desired, self.atomic)
        self.check.assert_not_called()

    def test_missing_capability_refuses_before_driver_preflight_or_mutation(self):
        self.install_preflight_mock()
        self.check.side_effect = ValueError('No active Original checkpoint')
        with self.assertRaisesRegex(ValueError, 'checkpoint'):
            self.invoke()
        self.drivers.assert_not_called()
        self.core.assert_not_called()
        self.atomic.assert_not_called()

    def test_missing_driver_refuses_before_repair_preflight(self):
        self.install_preflight_mock()
        self.drivers.side_effect = ValueError('Existing driver required')
        with self.assertRaisesRegex(ValueError, 'Existing driver'):
            self.invoke()
        self.core.assert_not_called()
        self.atomic.assert_not_called()

    def test_capability_is_rechecked_at_the_actual_mutation_boundary(self):
        self.install_preflight_mock()
        self.install_mutation()
        self.check.side_effect = [None, ValueError('Checkpoint was revoked')]
        with self.assertRaisesRegex(ValueError, 'revoked'):
            self.invoke()
        self.mutate.assert_not_called()
        self.atomic.assert_not_called()

    def test_successful_real_repair_is_returned_to_original_outer_transaction(self):
        self.install_preflight_mock()
        self.install_mutation()
        result = self.invoke()
        self.assertTrue(result['changed'])
        self.mutate.assert_called_once_with()
        self.atomic.assert_not_called()
        self.assertTrue(all(call.kwargs == {'repair': True} for call in self.check.call_args_list))

    def test_changed_identity_after_mutation_raises_for_the_outer_rollback(self):
        self.install_preflight_mock()
        self.install_mutation()
        self.check.side_effect = [None, None, ValueError('Rest changed during repair')]
        with self.assertRaisesRegex(ValueError, 'Rest changed'):
            self.invoke()
        self.mutate.assert_called_once_with()
        self.atomic.assert_not_called()

    def test_healthy_noop_still_revalidates_capability_after_core_returns(self):
        self.install_preflight_mock()
        self.check.side_effect = [None, ValueError('Rest changed during preflight')]
        with self.assertRaisesRegex(ValueError, 'Rest changed'):
            self.invoke()
        self.assertEqual(self.core.call_count, 1)
        self.atomic.assert_not_called()


class ExistingSwitches(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        definition = next(node for node in tree.body
                          if isinstance(node, ast.FunctionDef) and node.name == '_require_existing_switches')
        self.upper, self.lower = NS(name='hand'), NS(name='foot')
        self.armature = NS(pose=NS(bones={
            'hand': NS(bone={'version': 1}), 'foot': NS(bone={'version': 1})}))
        self.inventory = {'rigs': {
            ('ARM', 'L'): {'target': self.upper, 'entries': [('upper', 'constraint', 'owned')]},
            ('LEG', 'R'): {'target': self.lower, 'entries': [('lower', 'constraint', 'owned')]}}}
        self.validate = Mock(return_value=True)
        self.inventory_read = Mock(return_value=self.inventory)
        self.globals = {'VERSION_KEY': 'version', 'VERSION': 1, '_error': ValueError,
                        '_limb': lambda: NS(_validate_inventory=self.inventory_read),
                        'is_switch_constraint': Mock(return_value=True),
                        'validate_constraint_influence': self.validate}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(SOURCE), 'exec'), self.globals)

    def invoke(self, **kwargs):
        return self.globals['_require_existing_switches'](self.armature, **kwargs)

    def test_validates_both_arm_and_leg_switches(self):
        self.assertIs(self.invoke(), self.inventory)
        self.assertEqual(self.validate.call_count, 2)

    def test_explicit_prevalidated_inventory_avoids_a_second_inventory_read(self):
        self.assertIs(self.invoke(inventory=self.inventory), self.inventory)
        self.inventory_read.assert_not_called()

    def test_bool_version_cannot_be_mistaken_for_installed_version_one(self):
        self.armature.pose.bones['hand'].bone['version'] = True
        with self.assertRaisesRegex(ValueError, 'existing'):
            self.invoke()
        self.validate.assert_not_called()

    def test_uninstalled_switch_is_refused_instead_of_adding_a_driver(self):
        self.armature.pose.bones['hand'].bone.clear()
        with self.assertRaisesRegex(ValueError, 'existing'):
            self.invoke()
        self.validate.assert_not_called()

    def test_legacy_or_edited_influence_relation_cannot_pass(self):
        self.validate.return_value = False
        with self.assertRaisesRegex(ValueError, 'cannot install or replace'):
            self.invoke()

    def test_no_recognized_switch_relations_cannot_pass(self):
        self.globals['is_switch_constraint'].return_value = False
        with self.assertRaisesRegex(ValueError, 'cannot install or replace'):
            self.invoke()
        self.validate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
