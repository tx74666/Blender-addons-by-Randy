"""Original's explicit display handoff never becomes playback ownership."""

import ast
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, call, patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons' / 'character_designer'
fixture_spec = importlib.util.spec_from_file_location(
    '_original_display_frame_fixture', ROOT / 'tests' / 'test_bone_collection_frame_rejection.py')
frame_fixture = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(frame_fixture)


class ID(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


def function_namespace(package, names, **bindings):
    tree = ast.parse((SOURCE / 'bone_display.py').read_text(encoding='utf-8-sig'))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = dict(__package__=package, **bindings)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE / 'bone_display.py'), 'exec'), namespace)
    return namespace


class OriginalFrameScopeTests(unittest.TestCase):
    def setUp(self):
        frame_fixture.FrameRejectionTests.setUp(self)
        self.cap = object()
        self.owner_module = ModuleType(self.groups.__package__ + '.body_original_owner')

        def admitted(cap, rig):
            if cap is not self.cap:
                raise ValueError('stale Original capability')
            return rig is self.rig

        self.cap_check = self.owner_module._display_pose_capability = Mock(side_effect=admitted)
        modules = patch.dict(sys.modules, {self.owner_module.__name__: self.owner_module})
        modules.start()
        self.addCleanup(modules.stop)

    def frame(self, **options):
        self.groups._frame_visibility(self.scene, **options)

    def snapshots(self):
        return dict(self.groups._FRAME_CACHE), dict(self.groups._FRAME_OWNER_REJECTIONS)

    def test_cap_syncs_despite_equal_cached_state_without_publishing_owner_proof(self):
        state = self.groups._display_frame_state(self.rig, self.collection)
        self.groups._FRAME_CACHE[1234] = state
        self.groups._FRAME_OWNER_REJECTIONS[1234] = (state, ('data', 'users'), 5000.0)
        before = self.snapshots()
        self.frame(objects=(self.rig,), _original_pose=self.cap)
        self.sync.assert_called_once_with(self.rig, {'verified': True}, force_flags=False)
        self.owner.assert_not_called()
        self.inventory.assert_called_once_with(self.rig)
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(self.cap_check.call_count, 3)

    def test_cap_inventory_can_only_be_reused_in_qualified_single_rig_call(self):
        proof = {'strict_inventory_from_current_operation': True}
        self.frame(objects=(self.rig,), verified_inventory=proof, _original_pose=self.cap)
        self.sync.assert_called_once_with(self.rig, proof, force_flags=False)
        self.inventory.assert_not_called()
        self.assertEqual(self.snapshots(), ({}, {}))

    def test_wrong_scope_never_reaches_native_sync_or_default_caches(self):
        for objects in (None, (), (self.rig, self.rig), (ID(),)):
            with self.subTest(objects=objects), self.assertRaises(ValueError):
                self.frame(objects=objects, _original_pose=self.cap)
        self.sync.assert_not_called()
        self.inventory.assert_not_called()
        self.owner.assert_not_called()
        self.assertEqual(self.snapshots(), ({}, {}))

    def test_stale_cap_rejects_before_inventory(self):
        with self.assertRaisesRegex(ValueError, 'stale Original capability'):
            self.frame(objects=(self.rig,), _original_pose=object())
        self.sync.assert_not_called()
        self.inventory.assert_not_called()

    def test_cap_change_after_inventory_refuses_before_display_write(self):
        self.cap_check.side_effect = [True, False]
        with self.assertRaisesRegex(ValueError, 'before synchronization'):
            self.frame(objects=(self.rig,), _original_pose=self.cap)
        self.sync.assert_not_called()
        self.assertEqual(self.snapshots(), ({}, {}))

    def test_cap_change_during_sync_propagates_for_original_outer_rollback(self):
        self.cap_check.side_effect = [True, True, False]
        with self.assertRaisesRegex(ValueError, 'during synchronization'):
            self.frame(objects=(self.rig,), _original_pose=self.cap)
        self.sync.assert_called_once()
        self.assertEqual(self.snapshots(), ({}, {}))

    def test_explicit_sync_failure_propagates_and_does_not_poison_caches(self):
        self.sync.side_effect = RuntimeError('native display failed')
        with self.assertRaisesRegex(RuntimeError, 'native display failed'):
            self.frame(objects=(self.rig,), _original_pose=self.cap)
        self.assertEqual(self.snapshots(), ({}, {}))

    def test_cap_keeps_existing_changed_mode_force_flags_rule(self):
        self.groups._FRAME_CACHE[1234] = self.groups._display_frame_state(self.rig, self.collection)
        self.pose_bone['ik_fk'] = 1.0
        before = self.snapshots()
        self.frame(objects=(self.rig,), _original_pose=self.cap)
        self.sync.assert_called_once_with(self.rig, {'verified': True}, force_flags=True)
        self.assertEqual(self.snapshots(), before)

    def test_auto_disabled_still_does_not_sync_or_publish(self):
        self.data[self.groups.AUTO_KEY] = 0
        self.frame(objects=(self.rig,), _original_pose=self.cap)
        self.sync.assert_not_called()
        self.inventory.assert_not_called()
        self.assertEqual(self.snapshots(), ({}, {}))

    def test_default_call_still_uses_strict_owner_refusal(self):
        self.frame(objects=(self.rig,))
        self.owner.assert_called_once_with(self.rig)
        self.cap_check.assert_not_called()
        self.sync.assert_not_called()
        self.assertIn(1234, self.groups._FRAME_OWNER_REJECTIONS)


class OriginalEditableScopeTests(unittest.TestCase):
    def setUp(self):
        self.package_name = '_original_editable_' + uuid.uuid4().hex
        self.package = ModuleType(self.package_name)
        self.package.__path__ = [str(SOURCE)]
        self.owner = ModuleType(self.package_name + '.body_original_owner')
        self.cap = object()
        self.main = ID(type='ARMATURE', mode='OBJECT', library=None, is_editable=True,
                       data=ID(library=None))
        self.other = ID(type='ARMATURE', mode='OBJECT', library=None, is_editable=True,
                        data=ID(library=None))

        def admitted(cap, rig):
            if cap is not self.cap:
                raise ValueError('invalid Original capability')
            return rig is self.main

        self.owner._display_pose_capability = Mock(side_effect=admitted)
        modules = patch.dict(sys.modules, {self.package_name: self.package,
                                          self.owner.__name__: self.owner})
        modules.start()
        self.addCleanup(modules.stop)
        self.strict = Mock(return_value=False)
        self.ns = function_namespace(self.package_name, {'_editable'}, _single_data_user=self.strict)

    def test_default_has_unchanged_strict_owner_requirement(self):
        with self.assertRaisesRegex(ValueError, 'single-user'):
            self.ns['_editable'](self.main)
        self.strict.assert_called_once_with(self.main)
        self.owner._display_pose_capability.assert_not_called()

    def test_explicit_cap_allows_only_its_proven_main(self):
        self.ns['_editable'](self.main, _original_pose=self.cap)
        self.strict.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'single-user'):
            self.ns['_editable'](self.other, _original_pose=self.cap)
        self.strict.assert_called_once_with(self.other)

    def test_invalid_cap_is_not_silently_replaced_with_default_permission(self):
        self.strict.return_value = True
        with self.assertRaisesRegex(ValueError, 'invalid Original capability'):
            self.ns['_editable'](self.main, _original_pose=object())
        self.strict.assert_not_called()

    def test_missing_view_still_validates_before_returning(self):
        restore = function_namespace(self.package_name, {'restore_view'},
                                     _editable=self.ns['_editable'], VIEW_KEY='view')['restore_view']
        with self.assertRaisesRegex(ValueError, 'single-user'):
            restore(self.main)
        self.assertFalse(restore(self.main, _original_pose=self.cap))

    def test_view_restore_scopes_explicit_sync_without_touching_default_cache(self):
        self.strict.return_value = True
        self.main.data.update(view=json.dumps({'rigs': {'0': {}, '1': {}}}),
                              refs={'0': self.main, '1': self.other})
        groups = NS(_FRAME_CACHE={'other': 'keep'}, _frame_visibility=Mock())
        scene = object()
        restore = function_namespace(self.package_name, {'restore_view'},
                                     _editable=self.ns['_editable'], VIEW_KEY='view', REFS_KEY='refs',
                                     _check_object_restore=Mock(), _snapshot=Mock(return_value={}),
                                     _restore=Mock(), _completed=Mock(), groups=groups, json=json,
                                     bpy=NS(context=NS(scene=scene)))['restore_view']
        self.assertTrue(restore(self.main, _original_pose=self.cap))
        self.assertEqual(groups._FRAME_CACHE, {'other': 'keep'})
        self.assertEqual(groups._frame_visibility.call_args_list,
                         [call(scene, objects=(self.main,), _original_pose=self.cap)])
        self.assertNotIn('view', self.main.data)
        self.assertNotIn('refs', self.main.data)


if __name__ == '__main__':
    unittest.main()
