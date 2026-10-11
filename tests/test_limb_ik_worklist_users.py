"""Limb entry guards accept only authenticated extra armature references."""

import ast
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/limb_ik.py'


class Rig:
    pass


class WorklistArmatureUserTests(unittest.TestCase):
    def setUp(self):
        name = '_limb_worklist_users_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = []
        target = ModuleType(name + '.animation_worklist_target')
        self.proof = Mock(return_value=False)
        target.single_armature_user = self.proof
        package.animation_worklist_target = target
        environment = patch.dict(sys.modules, {name: package, target.__name__: target})
        environment.start()
        self.addCleanup(environment.stop)
        self.rig = Rig()
        self.rig.name, self.rig.type = 'Artist Rig', 'ARMATURE'
        self.rig.mode, self.rig.is_editable = 'POSE', True
        self.rig.library = self.rig.override_library = None
        self.rig.data = NS(users=1, library=None, override_library=None)
        self.rig.matrix_world = NS(to_3x3=lambda: NS(determinant=lambda: 1.0))
        self.rig.animation_data = None
        self.scene = NS(objects={self.rig.name: self.rig})
        self.context = NS(object=self.rig, scene=self.scene,
                          view_layer=NS(objects={self.rig.name: self.rig}))
        self.settings = NS(armature=self.rig, analysis_json=json.dumps({'version': 1, 'digest': 'same'}))
        self.bpy = NS(data=NS(scenes=[self.scene], objects=[], user_map=lambda: {}))
        self.namespace = {'__package__': name, 'bpy': self.bpy, 'json': json,
                          'LimbIKError': type('LimbIKError', (ValueError,), {}),
                          'EPSILON': 1e-8, 'RIG_VERSION': 1,
                          '_armature_digest': lambda _rig: 'same',
                          '_actions_for_id': lambda _rig: [],
                          '_constraint_references_controls': lambda *_args: True}
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        names = {'_single_armature_user', '_require_active_armature', '_wrist_rotation_upgrade_problems'}
        functions = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in functions}, names)
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), self.namespace)

    def require(self, *, analyzed=False):
        return self.namespace['_require_active_armature'](self.context, self.settings, analyzed=analyzed)

    def wrist(self):
        return self.namespace['_wrist_rotation_upgrade_problems'](self.rig, {'records': []}, [])

    def extension_active(self, module):
        path = SOURCE.parent / (module + '.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == '_active')
        namespace = {'_error': ValueError,
                     '_limb': lambda: NS(_single_armature_user=self.namespace['_single_armature_user'])}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        namespace['_active'](self.context, self.rig)

    def test_ordinary_single_user_keeps_lightweight_path(self):
        self.proof.side_effect = AssertionError('ordinary rig must not load Worklist references')
        self.assertIs(self.require(), self.rig)
        self.assertEqual(self.wrist(), [])
        self.proof.assert_not_called()

    def test_authenticated_active_and_restored_refs_allow_normal_controls(self):
        for state, users in (('active', 4), ('restored', 3)):
            with self.subTest(state=state):
                self.rig.data.users = users
                self.proof.return_value = True
                self.assertIs(self.require(), self.rig)
                self.proof.assert_called_with(self.rig)

    def test_foreign_extra_reference_keeps_both_single_user_guards(self):
        self.rig.data.users = 2
        with self.assertRaisesRegex(self.namespace['LimbIKError'], 'Data is shared'):
            self.require()
        self.assertEqual(self.wrist(), ['Use a local, single-user Armature before upgrading wrist rotation.'])

    def test_corrupt_owned_reference_proof_cannot_fall_back(self):
        self.rig.data.users = 3
        self.proof.side_effect = ValueError('recovery snapshot changed')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            self.require()
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            self.wrist()

    def test_authenticated_refs_do_not_relax_library_or_scene_checks(self):
        self.rig.data.users = 3
        self.proof.return_value = True
        self.rig.data.library = object()
        with self.assertRaisesRegex(self.namespace['LimbIKError'], 'Linked Armatures'):
            self.require()
        self.proof.assert_not_called()
        self.rig.data.library = None
        self.bpy.data.scenes.append(NS(objects={self.rig.name: self.rig}))
        with self.assertRaisesRegex(self.namespace['LimbIKError'], 'only to the current Scene'):
            self.require()

    def test_authenticated_refs_still_require_current_analysis(self):
        self.rig.data.users = 3
        self.proof.return_value = True
        self.settings.analysis_json = json.dumps({'version': 1, 'digest': 'foreign'})
        with self.assertRaisesRegex(self.namespace['LimbIKError'], 'rest pose changed'):
            self.require(analyzed=True)

    def test_wrist_authentication_does_not_remove_dependency_blockers(self):
        self.rig.data.users = 3
        self.proof.return_value = True
        constraint = NS(name='Foreign wrist dependency')
        self.bpy.data.objects = [NS(parent=None, constraints=[constraint], pose=None)]
        self.assertEqual(self.wrist(), ["Constraint 'Foreign wrist dependency' reads a wrist target."])
        self.proof.assert_called_once_with(self.rig)

    def test_torso_and_foot_normal_controls_use_the_same_authenticated_gate(self):
        for module in ('torso_controls', 'foot_controls'):
            for state, users in (('ordinary', 1), ('active', 4), ('restored', 3)):
                with self.subTest(module=module, state=state):
                    self.proof.reset_mock()
                    self.rig.data.users = users
                    self.proof.return_value = True
                    self.extension_active(module)
                    if state == 'ordinary':
                        self.proof.assert_not_called()
                    else:
                        self.proof.assert_called_once_with(self.rig)

    def test_torso_and_foot_reject_foreign_or_corrupt_extra_references(self):
        self.rig.data.users = 3
        for module in ('torso_controls', 'foot_controls'):
            with self.subTest(module=module, proof='foreign'):
                self.proof.return_value = False
                with self.assertRaisesRegex(ValueError, 'single-user armature'):
                    self.extension_active(module)
            with self.subTest(module=module, proof='corrupt'):
                self.proof.side_effect = ValueError('wrong recovery rig')
                with self.assertRaisesRegex(ValueError, 'wrong recovery rig'):
                    self.extension_active(module)
                self.proof.side_effect = None

    def test_torso_and_foot_keep_other_editability_guards(self):
        self.rig.data.users = 3
        self.proof.return_value = True
        self.rig.mode = 'EDIT'
        for module in ('torso_controls', 'foot_controls'):
            with self.subTest(module=module):
                with self.assertRaisesRegex(ValueError, 'Object or Pose Mode'):
                    self.extension_active(module)
        self.proof.assert_not_called()


if __name__ == '__main__':
    unittest.main()
