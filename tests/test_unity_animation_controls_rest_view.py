"""Control inventory uses a proven managed Rest view without changing strict defaults."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class RestViewInventoryTests(unittest.TestCase):
    def setUp(self):
        name = '_controls_rest_view_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        modules = {name: package, 'bpy': ModuleType('bpy')}
        mathutils = ModuleType('mathutils')
        mathutils.Matrix = Mock()
        modules['mathutils'] = mathutils
        self.fake = {}
        for label in ('limb_ik', 'limb_ik_fk', 'root_control', 'torso_controls',
                      'foot_controls', 'spine_ik_fk', 'eye_controls',
                      'unity_animation_dress', 'animation_worklist_target', 'unity_animation'):
            module = ModuleType(name + '.' + label)
            self.fake[label] = module
            setattr(package, label, module)
            modules[module.__name__] = module
        self.fake['limb_ik'].LimbIKError = type('LimbIKError', (ValueError,), {})
        self.inventory = Mock(return_value={'bones': [], 'records': [], 'rigs': {}})
        self.fake['limb_ik']._validate_inventory = self.inventory
        for label in ('root_control', 'torso_controls', 'spine_ik_fk', 'eye_controls'):
            self.fake[label].validate = Mock(return_value=None)
        self.fake['foot_controls'].validate = Mock(return_value={})
        self.fake['unity_animation_dress'].DressAnimationError = type('DressAnimationError', (ValueError,), {})
        self.fake['unity_animation_dress'].build_plan = Mock(return_value=None)
        self.fake['unity_animation'].UnityAnimationError = type('UnityAnimationError', (ValueError,), {})
        self.rest_view = Mock(return_value=None)
        self.fake['animation_worklist_target'].validation_rest_view = self.rest_view
        spec = importlib.util.spec_from_file_location(name + '.unity_animation_controls', SOURCE / 'unity_animation_controls.py')
        self.controls = importlib.util.module_from_spec(spec)
        modules[spec.name] = self.controls
        environment = patch.dict(sys.modules, modules)
        environment.start()
        self.addCleanup(environment.stop)
        spec.loader.exec_module(self.controls)
        self.rig = SimpleNamespace(type='ARMATURE', pose=SimpleNamespace(bones={}), animation_data=None)

    def test_ordinary_target_keeps_strict_inventory_call(self):
        self.assertIsNone(self.controls.build_plan(self.rig, []))
        self.inventory.assert_called_once_with(self.rig)
        self.rest_view.assert_called_once_with(self.rig, frozenset())

    def test_validated_managed_data_passes_only_exact_returned_view(self):
        view = object()
        self.rest_view.return_value = view
        self.assertIsNone(self.controls.build_plan(self.rig, []))
        self.inventory.assert_called_once_with(self.rig, original_rest=view, source_rest=view)
        for label in ('root_control', 'torso_controls', 'spine_ik_fk', 'eye_controls', 'foot_controls'):
            self.fake[label].validate.assert_called_once_with(self.rig, inventory=self.inventory.return_value,
                                                             source_rest=view)

    def test_failed_recovery_proof_cannot_fall_back_to_inventory(self):
        self.rest_view.side_effect = ValueError('motion Rest changed')
        with self.assertRaisesRegex(self.fake['unity_animation'].UnityAnimationError, 'motion Rest changed'):
            self.controls.build_plan(self.rig, [])
        self.inventory.assert_not_called()

    def test_inventory_still_rejects_saved_source_rest_edits(self):
        self.rest_view.return_value = object()
        self.inventory.side_effect = self.fake['limb_ik'].LimbIKError('source Rest edited')
        with self.assertRaisesRegex(self.fake['unity_animation'].UnityAnimationError, 'source Rest edited'):
            self.controls.build_plan(self.rig, [])


if __name__ == '__main__':
    unittest.main()
