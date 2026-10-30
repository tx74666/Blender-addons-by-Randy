"""Original return authenticates paused source mutes without repairing drivers."""
import ast
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
SESSION = 'character_designer_body_original_mode_v1'


class AfterPreflight(Exception):
    pass


class ID(dict):
    pass


@contextmanager
def read_scope():
    yield


def definitions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('Missing exact product definitions.')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


class OriginalReturnMutedInventory(unittest.TestCase):
    def setUp(self):
        package = ModuleType('_original_return_mutes_' + uuid.uuid4().hex)
        package.__path__ = []
        self.rig = ID()
        self.rig.current_rest = {'forearm.L': {'unchanged': True}}
        self.saved = {'version': 1, 'names': ['forearm.L'], 'rest': self.rig.current_rest,
            'constraints': [{'bone': 'forearm.L', 'name': 'Owned IK', 'type': 'IK', 'mute': False}]}
        self.rig[SESSION] = json.dumps(self.saved)
        self.constraint = NS(name='Owned IK', type='IK', mute=True,
            path_from_id=lambda field: 'pose.bones["forearm.L"].constraints["Owned IK"].' + field)
        self.source_bone = NS(name='forearm.L')
        self.target = ID(ik_fk=1.0)
        self.target.name = 'hand.L'
        self.target.path_from_id = lambda: 'pose.bones["hand.L"]'
        self.target.bone = {'character_designer_limb_ik_fk_version': 1}
        self.rig.pose = NS(bones={'hand.L': self.target})
        self.record = {'role': 'IK', 'target': 'hand.L', 'chain': ['upper_arm.L', 'forearm.L', 'hand.L']}
        self.entry = (self.source_bone, self.constraint, self.record)
        self.inventory = {'rigs': {('ARM', 'L'): {'target': NS(name='hand.L'), 'entries': [self.entry]}}}
        self.driver = NS(array_index=0, mute=False, modifiers=[], extrapolation='LINEAR',
            keyframe_points=[NS(co=(0.0, 0.0), interpolation='LINEAR'), NS(co=(1.0, 1.0), interpolation='LINEAR')],
            driver=NS(type='SCRIPTED', expression='ik_fk', variables=[NS(name='ik_fk', type='SINGLE_PROP',
                targets=[NS(id=self.rig, data_path='pose.bones["hand.L"]["ik_fk"]')])]))
        self.drivers = {self.constraint.path_from_id('influence'): self.driver}
        self.inventory_calls = []

        def validate_inventory(rig, *, original_mutes=None, original_rest=None):
            self.assertIs(rig, self.rig)
            self.inventory_calls.append(original_mutes)
            muted = self.constraint.mute if original_mutes is None else original_mutes.mute(self.source_bone, self.constraint)
            if muted:
                raise ValueError('Owned IK on forearm.L is muted.')
            return self.inventory

        def validate_rest(rig, saved=None):
            expected = json.loads(rig[SESSION]) if saved is None else saved
            if rig.current_rest != expected['rest']:
                raise ValueError('Original Rest changed; keep the current edit.')
            return rig.current_rest

        self.limb = NS(_validate_inventory=validate_inventory,
            _owned_constraint_records=lambda rig, strict: [self.entry])
        self.original = NS(SESSION=SESSION, active=lambda rig: SESSION in rig, _validate_session_rest=validate_rest)
        self.owner = NS(_check_pose_capability=Mock())
        self.resync = NS(prepare=Mock())
        package.validation_json = package.validation_native = NS(read_scope=read_scope)
        package.body_original_mode, package.limb_ik = self.original, self.limb
        package.body_original_owner, package.body_rest_resync = self.owner, self.resync
        for name in ('torso_controls', 'spine_ik_fk', 'eye_controls', 'root_control'):
            setattr(package, name, NS(get_record=lambda rig: None))
        package.foot_controls = NS(records=lambda rig: {})
        modules = patch.dict(sys.modules, {package.__name__: package})
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location(package.__name__ + '.forearm_original_inventory',
                                                      SOURCE / 'forearm_original_inventory.py')
        self.authenticated_inventory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.authenticated_inventory)
        package.forearm_original_inventory = self.authenticated_inventory
        self.authenticate = Mock(wraps=self.authenticated_inventory.validate)
        self.authenticated_inventory.validate = self.authenticate
        self.match_namespace = {'_limb': lambda: self.limb, '_error': ValueError,
            'VERSION_KEY': 'character_designer_limb_ik_fk_version', 'VERSION': 1, 'PROPERTY': 'ik_fk',
            '_find_driver': lambda rig, path: self.drivers.get(path), 'math': __import__('math')}
        definitions(SOURCE / 'limb_ik_fk.py', {'property_path', 'is_switch_constraint',
                    'validate_constraint_influence', '_require_existing_switches'}, self.match_namespace)
        self.existing_switches = Mock(wraps=self.match_namespace['_require_existing_switches'])
        self.after = Mock(side_effect=AfterPreflight)
        self.mode_namespace = {'__package__': package.__name__, '_require': lambda *args, **kwargs: self.saved,
            'active': self.original.active, 'match': NS(_require_existing_switches=self.existing_switches),
            '_validate_session_rest': validate_rest, '_ensure_dress_editable': self.after}
        definitions(SOURCE / 'body_original_mode.py', {'_leave'}, self.mode_namespace)
        self.context, self.capability = NS(), object()

    def invoke(self, capability=True):
        return self.mode_namespace['_leave'](self.context, self.rig, Mock(),
            _original_pose=self.capability if capability else None)

    def original_native_inputs(self):
        return (self.rig[SESSION], self.constraint.type, self.constraint.mute,
                dict(self.target), dict(self.target.bone), tuple(self.drivers))

    def test_normal_original_mutes_are_authenticated_without_writing_rna(self):
        with self.assertRaisesRegex(ValueError, 'is muted'):
            self.limb._validate_inventory(self.rig)
        before = self.original_native_inputs()
        with self.assertRaises(AfterPreflight):
            self.invoke()
        self.authenticate.assert_called_once_with(self.rig)
        self.existing_switches.assert_called_once_with(self.rig, inventory=self.inventory)
        self.assertIsInstance(self.inventory_calls[-1], self.authenticated_inventory._MuteView)
        self.owner._check_pose_capability.assert_called_once_with(self.capability, self.rig)
        self.assertEqual(before, self.original_native_inputs())
        self.assertTrue(self.constraint.mute)

    def test_changed_live_mute_is_rejected_without_restore(self):
        self.constraint.mute = False
        before = self.original_native_inputs()
        with self.assertRaisesRegex(ValueError, 'was changed'):
            self.invoke()
        self.assertEqual(before, self.original_native_inputs())
        self.existing_switches.assert_not_called()
        self.after.assert_not_called()

    def test_changed_constraint_type_is_not_authorized_by_saved_name(self):
        self.constraint.type = 'COPY_ROTATION'
        with self.assertRaisesRegex(ValueError, 'ownership no longer matches'):
            self.invoke()
        self.existing_switches.assert_not_called()
        self.after.assert_not_called()
        self.assertTrue(self.constraint.mute)

    def test_missing_driver_is_rejected_after_mute_authentication(self):
        self.drivers.clear()
        before = self.original_native_inputs()
        with self.assertRaisesRegex(ValueError, 'driver.*missing'):
            self.invoke()
        self.authenticate.assert_called_once_with(self.rig)
        self.after.assert_not_called()
        self.assertEqual(before, self.original_native_inputs())

    def test_boolean_switch_version_is_not_a_valid_installed_driver_version(self):
        self.target.bone['character_designer_limb_ik_fk_version'] = True
        with self.assertRaisesRegex(ValueError, 'existing, validated'):
            self.invoke()
        self.after.assert_not_called()

    def test_current_rest_change_cannot_use_the_mute_view_as_a_rest_exception(self):
        self.rig.current_rest = {'forearm.L': {'unchanged': False}}
        with self.assertRaisesRegex(ValueError, 'Rest changed'):
            self.invoke()
        self.existing_switches.assert_not_called()
        self.after.assert_not_called()
        self.resync.prepare.assert_not_called()

    def test_capability_refusal_still_precedes_any_mute_or_driver_admission(self):
        self.owner._check_pose_capability.side_effect = ValueError('Original capability changed')
        with self.assertRaisesRegex(ValueError, 'capability changed'):
            self.invoke()
        self.authenticate.assert_not_called()
        self.existing_switches.assert_not_called()
        self.after.assert_not_called()

    def test_default_return_does_not_acquire_pose_only_mute_or_driver_permission(self):
        with self.assertRaises(AfterPreflight):
            self.invoke(capability=False)
        self.authenticate.assert_not_called()
        self.existing_switches.assert_not_called()
        self.owner._check_pose_capability.assert_not_called()


if __name__ == '__main__':
    unittest.main()
