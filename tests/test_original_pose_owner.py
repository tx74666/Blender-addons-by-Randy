"""Original-only ownership admission and return boundaries, without Blender."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/body_original_owner.py'


class ID(dict):
    __hash__ = object.__hash__
    __eq__ = object.__eq__

    def __init__(self, name, **fields):
        super().__init__()
        self.name = self.name_full = name
        self.library = self.override_library = None
        self.is_editable = True
        self.use_fake_user = self.use_extra_user = False
        self.__dict__.update(fields)


class Action(ID):
    pass


class Data(ID):
    @property
    def users(self):
        return self.count_users() + getattr(self, 'unexplained_users', 0)


class OriginalPoseOwnerTests(unittest.TestCase):
    def setUp(self):
        self.package = '_original_pose_owner_' + uuid.uuid4().hex
        package = ModuleType(self.package)
        package.__path__ = []
        self.data = Data('metarig')
        self.bones = [NS(name='Bone', bone=ID('Bone')), NS(name='CTRL', bone=ID('CTRL')),
                      NS(name='Dress', bone=ID('Dress'))]
        self.bones[-1].bone['dress_owner'] = 'dress'
        self.rig = ID('Rig', type='ARMATURE', mode='OBJECT', data=self.data, pose=NS(bones=self.bones))
        scene = ID('Scene', objects={'Rig': self.rig})
        self.context = NS(mode='OBJECT', scene=scene,
                          view_layer=NS(name='ViewLayer', objects={'Rig': self.rig}),
                          screen=NS(is_animation_playing=False))
        self.objects, self.actions = [self.rig], []
        self.rest = {'Bone': dict(parent=None, head=[0., 0., 0.], tail=[0., 1., 0.],
            matrix=[1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.],
            use_connect=False, use_deform=True, use_inherit_rotation=True,
            use_local_location=True, use_relative_parent=False, inherit_scale='FULL')}
        self.original = NS(SESSION='original_session', active=lambda rig: 'original_session' in rig)
        self.target = NS(SESSION='work_session', REFS='work_refs', RESTORED='work_restored', ACTION_RIG_KEY='motion_rig')
        self.ua = NS(ORIGINAL_DATA_KEY='original_data', PREVIEW_DATA_KEY='preview_data',
                     _rest_state=lambda data: deepcopy(self.rest), _compile_saved_rest_state=lambda value: value or None)
        self.limb = NS(_single_armature_user=Mock(return_value=False))
        self.switches = Mock(return_value={'rigs': {}})
        self.bpy = NS(types=NS(ID=ID, Action=Action),
                      data=NS(objects=self.objects, scenes=[scene], user_map=Mock(side_effect=self.user_map)))
        modules = {'bpy': self.bpy, 'mathutils': NS(Matrix=object, Vector=object), self.package: package,
                   self.package + '.body_original_mode': self.original,
                   self.package + '.animation_worklist_target': self.target,
                   self.package + '.unity_animation': self.ua,
                   self.package + '.limb_ik': self.limb,
                   self.package + '.limb_ik_fk': NS(_require_existing_switches=self.switches),
                   self.package + '.skirt_rig': NS(OWNER_KEY='dress_owner'),
                   self.package + '.body_region': NS(RECORD_KEY='region', RECOVERY_OWNER_KEY='owner_revision')}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location(self.package + '.body_original_owner', SOURCE)
        self.owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.owner)
        self.data.count_users = lambda: sum(obj.data is self.data for obj in self.objects) + sum(
            len(self.paths(dict(holder), self.data)) for holder in self.objects + self.actions)
        self.rig[self.target.SESSION] = 'stale historical Rest is deliberately not authenticated'
        self.rig[self.target.REFS] = {key: self.data for key in ('id_3', 'id_5', 'id_8')}
        for name in ('Idle', 'Old Custom', 'Walk'):
            action = Action(name)
            action.update(motion_rig=self.rig, original_data=self.data, preview_data=ID(name + ' motion'),
                          original_rest='unchanged historical text', old_proof='stale proof')
            self.actions.append(action)

    def paths(self, value, data, path=()):
        if isinstance(value, ID):
            return [path] if value is data else []
        if isinstance(value, dict):
            return [leaf for key, child in value.items() for leaf in self.paths(child, data, path + (key,))]
        if isinstance(value, (list, tuple)):
            return [leaf for i, child in enumerate(value) for leaf in self.paths(child, data, path + (i,))]
        return []

    def user_map(self, *, subset):
        self.assertEqual(subset, {self.data})
        return {self.data: {holder for holder in self.objects + self.actions
                           if (holder is self.rig or self.paths(dict(holder), self.data))}}

    def entered(self):
        cap = self.owner.pose_capability(self.context, self.rig, entering=True)
        self.owner.install_pose_session(cap)
        self.rig[self.original.SESSION] = json.dumps({'pose_only_owner': self.owner.pose_session_fields(cap)})
        return cap

    def checkpoint(self):
        return ({'Bone': {}, 'CTRL': {}}, {}, {}, [], [], {})

    def test_ordinary_strict_and_unmarked_return_do_not_create_capability(self):
        self.limb._single_armature_user.return_value = True
        self.assertIsNone(self.owner.pose_capability(self.context, self.rig, entering=True))
        self.assertIsNone(self.owner.pose_capability(self.context, self.rig))
        self.bpy.data.user_map.assert_not_called()

    def test_unmarked_ordinary_return_keeps_existing_callback_and_rest_admission(self):
        self.rig[self.original.SESSION] = '{}'
        self.limb._single_armature_user.return_value = True
        callback = Mock(return_value='ordinary')
        self.assertEqual(self.owner.run_return(self.context, self.rig, callback), 'ordinary')
        callback.assert_called_once_with()
        self.limb._single_armature_user.return_value = False
        with patch.object(self.owner, '_admit', side_effect=ValueError('old Rest admission')) as admit:
            with self.assertRaisesRegex(ValueError, 'old Rest admission'):
                self.owner.run_return(self.context, self.rig, callback)
            admit.assert_called_once_with(self.context, self.rig)

    def test_exact_six_history_references_admit_pose_only_without_rewriting_them(self):
        before = [dict(action) for action in self.actions]
        cap = self.owner.pose_capability(self.context, self.rig, entering=True)
        self.assertEqual(self.data.users, 7)
        self.assertTrue(self.owner._check_pose_capability(cap, self.rig))
        self.assertEqual(before, [dict(action) for action in self.actions])
        self.assertNotIn(self.owner.POSE_REFS, self.rig)

    def test_hidden_unlinked_second_actual_binding_is_rejected(self):
        self.objects.append(ID('Hidden snapshot', type='ARMATURE', data=self.data))
        with self.assertRaisesRegex(ValueError, 'Another Object'):
            self.owner.pose_capability(self.context, self.rig, entering=True)

    def test_unknown_rig_reference_path_is_rejected(self):
        self.rig['unknown_backup'] = self.data
        with self.assertRaisesRegex(ValueError, 'unknown rig property'):
            self.owner.pose_capability(self.context, self.rig, entering=True)

    def test_unknown_action_path_and_foreign_action_owner_are_rejected(self):
        for change in ('path', 'rig'):
            with self.subTest(change=change):
                action = self.actions[0]
                if change == 'path':
                    action['unexpected'] = {'nested': self.data}
                else:
                    action['motion_rig'] = ID('Other rig')
                with self.assertRaisesRegex(ValueError, 'unknown owner or property'):
                    self.owner.pose_capability(self.context, self.rig, entering=True)
                action.pop('unexpected', None)
                action['motion_rig'] = self.rig

    def test_fake_extra_unexplained_users_and_overrides_are_rejected(self):
        for attr, value in [('use_fake_user', True), ('use_extra_user', True),
                            ('unexplained_users', 1), ('override_library', object())]:
            with self.subTest(attr=attr):
                setattr(self.data, attr, value)
                with self.assertRaises(ValueError):
                    self.owner.pose_capability(self.context, self.rig, entering=True)
                setattr(self.data, attr, None if attr == 'override_library' else 0)

    def test_carrier_string_and_native_identity_changes_are_rejected(self):
        cap = self.entered()
        self.actions[0]['original_rest'] += ' changed'
        with self.assertRaisesRegex(ValueError, 'Rest or retained history changed'):
            self.owner._check_pose_capability(cap, self.rig)
        self.actions[0]['original_rest'] = 'unchanged historical text'
        self.actions[0]['preview_data'] = ID('replacement with same label')
        with self.assertRaisesRegex(ValueError, 'native ID reference'):
            self.owner._check_pose_capability(cap, self.rig)

    def test_native_map_supports_fresh_capability_and_detects_tampering(self):
        self.entered()
        fresh = self.owner.pose_capability(self.context, self.rig)
        self.assertEqual(self.data.users, 8)
        self.assertTrue(self.owner._check_pose_capability(fresh, self.rig))
        self.rig[self.owner.POSE_REFS]['id_3'] = Action('replaced carrier')
        with self.assertRaises(ValueError):
            self.owner._check_pose_capability(fresh, self.rig)

    def test_return_removes_only_own_map_after_outer_checkpoint_and_final_validation(self):
        self.entered()
        before = [dict(action) for action in self.actions]
        def transfer(finalize, *, _original_pose):
            with self.owner.pose_return_checkpoint(_original_pose, self.checkpoint()):
                self.owner._check_pose_capability(_original_pose, self.rig, repair=True)
                self.rig.pop(self.original.SESSION)
                self.owner.finish_pose_session(_original_pose)
                finalize()
            return 'returned'
        self.assertEqual(self.owner.run_return(self.context, self.rig, transfer), 'returned')
        self.assertNotIn(self.owner.POSE_REFS, self.rig)
        self.assertEqual(self.data.users, 7)
        self.assertEqual(before, [dict(action) for action in self.actions])
        self.switches.assert_called_once_with(self.rig)

    def test_repair_needs_live_checkpoint_covering_body_and_excluding_dress(self):
        self.entered()
        cap = self.owner.pose_capability(self.context, self.rig)
        with self.assertRaisesRegex(ValueError, 'active outer pose checkpoint'):
            self.owner._check_pose_capability(cap, self.rig, repair=True)
        wrong = ({'Bone': {}, 'CTRL': {}, 'Dress': {}}, {}, {}, [], [], {})
        with self.owner.pose_return_checkpoint(cap, wrong):
            with self.assertRaisesRegex(ValueError, 'active outer pose checkpoint'):
                self.owner._check_pose_capability(cap, self.rig, repair=True)
        self.assertIsNone(cap.checkpoint)

    def test_rest_edit_refuses_before_return_callback_and_keeps_session(self):
        self.entered()
        raw, refs = self.rig[self.original.SESSION], dict(self.rig[self.owner.POSE_REFS])
        self.rest['Bone']['head'][0] = .01
        callback = Mock()
        with self.assertRaisesRegex(ValueError, 'Rest or retained history changed'):
            self.owner.run_return(self.context, self.rig, callback)
        callback.assert_not_called()
        self.assertEqual(self.rig[self.original.SESSION], raw)
        self.assertEqual(dict(self.rig[self.owner.POSE_REFS]), refs)

    def test_failed_return_restores_captured_refmap_inside_outer_rollback(self):
        self.entered()
        session, refs = self.rig[self.original.SESSION], dict(self.rig[self.owner.POSE_REFS])
        def transfer(finalize, *, _original_pose):
            with self.owner.pose_return_checkpoint(_original_pose, self.checkpoint()):
                try:
                    self.rig.pop(self.original.SESSION)
                    self.owner.finish_pose_session(_original_pose)
                    raise RuntimeError('late Original verification failed')
                except Exception:
                    self.rig[self.original.SESSION] = session
                    self.owner.restore_pose_session(_original_pose)
                    raise
        with self.assertRaisesRegex(RuntimeError, 'late Original'):
            self.owner.run_return(self.context, self.rig, transfer)
        self.assertEqual(dict(self.rig[self.owner.POSE_REFS]), refs)
        self.assertTrue(self.owner.pose_capability(self.context, self.rig).returning)

    def test_enter_rollback_removes_own_refs_and_does_not_restore_old_action_proofs(self):
        cap = self.owner.pose_capability(self.context, self.rig, entering=True)
        self.owner.install_pose_session(cap)
        self.owner.restore_pose_session(cap)
        self.assertNotIn(self.owner.POSE_REFS, self.rig)
        self.assertEqual(self.actions[1]['old_proof'], 'stale proof')


if __name__ == '__main__':
    unittest.main()
