"""Main Rig adoption ownership, persistence and non-mutating failure gates."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class ID(dict):
    __hash__ = object.__hash__

    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)

    def __bool__(self):
        return True

    def copy(self):
        copied = ID(**self.__dict__)
        copied.update(self)
        copied.users = 0
        if hasattr(self, 'bones'):
            copied.bones = Bones()
            for bone in self.bones:
                fields = dict(bone.__dict__)
                fields['id_data'] = copied
                clone = ID(**fields)
                clone.update(bone)
                copied.bones[clone.name] = clone
        return copied


class Bones(dict):
    def __iter__(self):
        return iter(self.values())


class TargetTests(unittest.TestCase):
    def setUp(self):
        name = '_main_target_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        self.ua = ModuleType(name + '.unity_animation')
        self.ua.active_preview = Mock(return_value=None)
        self.ua._mapping = Mock(return_value=SimpleNamespace(indices={'forearm.L': 0}))
        self.ua._rest_state = Mock(return_value={'Rest': 'unchanged'})
        self.ua._matches_rest_state = Mock(return_value=True)
        self.ua._same_rest_bone = Mock(return_value=True)
        self.ua.restore_preview = Mock()
        self.ua._swap_armature_data = Mock()
        self.ua.PREVIEW_DATA_KEY = 'preview_data'
        self.ua.ORIGINAL_DATA_KEY = 'original_data'
        self.ua.ORIGINAL_REST_KEY = 'original_rest'
        self.original = ModuleType(name + '.body_original_mode')
        self.original.active = Mock(return_value=True)
        self.original._validate_session_rest = Mock()
        self.original.leave = Mock()
        self.limb = ModuleType(name + '.limb_ik')
        self.limb.OWNER_KEY = 'character_designer_owner'
        self.limb._rest_state_matches = Mock(side_effect=lambda bone, state: bone is not None)
        self.limb._rest_state_mismatch_details = Mock(return_value='strict original details')
        self.skirt = ModuleType(name + '.skirt_rig')
        self.skirt.OWNER_KEY = 'character_designer_skirt_owner'
        self.worklist = ModuleType(name + '.animation_worklist')
        self.worklist._idle = Mock()
        self.worklist._slot = Mock()
        self.worklist._check_baseline = Mock()
        self.data = ID(name='Artist Data', use_fake_user=False, library=None, users=1)
        bone = ID(name='forearm.L', id_data=self.data, use_connect=True)
        self.data.bones = Bones({bone.name: bone})
        self.rig = ID(name='Artist Rig', type='ARMATURE', data=self.data,
                      library=None, override_library=None)
        self.scene = ID(name='Artist Scene', objects={self.rig.name: self.rig},
                        unit_settings=SimpleNamespace(scale_length=1.0))
        self.saved = SimpleNamespace(rig=None, items=[], catalog=[SimpleNamespace(clip_key='walk', ready=True)],
                                     workspace_path='workspace.json', workspace_identity='strict identity')
        self.scene.character_designer_setup = SimpleNamespace(rig=self.rig)
        self.scene.character_designer_animation_worklist = self.saved
        self.context = SimpleNamespace(scene=self.scene, mode='POSE',
            view_layer=SimpleNamespace(name='Artist Layer', objects={self.rig.name: self.rig}, update=Mock()))
        self.worklist.state = lambda context: context.scene.character_designer_animation_worklist
        self.packet = {'_path': 'packet.json', '_sha256': 'f' * 64}
        self.worklist._prepared = Mock(return_value=({}, {}, {}, self.packet, 'Rig', Path('historical.fbx')))
        self.armatures = SimpleNamespace(remove=Mock())
        self.bpy_data = SimpleNamespace(armatures=self.armatures, objects=[self.rig],
                                       user_map=Mock(return_value={self.data: {self.rig}}))
        bpy = SimpleNamespace(types=SimpleNamespace(ID=ID, Object=ID, Armature=ID, Action=ID),
                              data=self.bpy_data)
        modules = patch.dict(sys.modules, {name: package, 'bpy': bpy,
            self.ua.__name__: self.ua, self.original.__name__: self.original,
            self.limb.__name__: self.limb, self.skirt.__name__: self.skirt,
            self.worklist.__name__: self.worklist})
        modules.start()
        self.addCleanup(modules.stop)
        for module in (self.ua, self.original, self.worklist, self.limb, self.skirt):
            setattr(package, module.__name__.rsplit('.', 1)[-1], module)
        spec = importlib.util.spec_from_file_location(name + '.animation_worklist_target', SOURCE / 'animation_worklist_target.py')
        self.target = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.target
        spec.loader.exec_module(self.target)
        self.capture = {'targets': [{'target': self.rig, 'data': self.data, 'data_fake_user': False,
                                      'action': None, 'rest': {'Rest': 'unchanged'}}], 'metadata': [], 'context': {}}
        for method in ('_capture', '_validate_capture', '_apply_capture'):
            mocked = patch.object(self.target, method, return_value=self.capture if method == '_capture' else None)
            mocked.start()
            self.addCleanup(mocked.stop)

    def adopt(self):
        return self.target.adopt_main_rig(self.context)

    def motion_copy(self):
        self.adopt()
        original = ID(name='forearm.L', id_data=self.data, use_connect=True)
        self.data.bones = Bones({original.name: original})
        motion = ID(name='Owned Motion Copy', use_fake_user=False, library=None, users=1)
        bone = ID(name=original.name, id_data=motion, use_connect=False)
        motion.bones = Bones({bone.name: bone})
        record = self.target._load(self.rig)
        record['motion_data'] = motion
        record['motion_rest'] = {'Rest': 'motion copy'}
        self.target._store(self.rig, record)
        self.rig.data = motion
        return original, bone, motion

    def restored_draft(self, *, translated=True):
        if translated:
            original, bone, motion = self.motion_copy()
        else:
            self.adopt()
            original = self.data.bones['forearm.L']
            bone, motion = original, self.data
        action = ID(name='Retained Artist Draft', library=None)
        action['artist_curves'] = 'unchanged world-motion curves'
        item = SimpleNamespace(rig=self.rig, custom_action=action, custom_slot=11)
        self.saved.items = [item]
        self.target._apply_capture.side_effect = lambda context, rig, snapshot: setattr(
            rig, 'data', snapshot['targets'][0]['data'])
        with patch.object(self.target, '_validate_record'):
            self.target.restore_main_rig(self.context)

        def swap(context, rig, data, *, allow_joint_translation=False, joint_translation_names=None):
            rig.data = data
            if allow_joint_translation:
                for name in joint_translation_names:
                    data.bones[name].use_connect = False
        self.ua._swap_armature_data.side_effect = swap
        self.ua._swap_armature_data.reset_mock()
        self.original.leave.reset_mock()
        return original, motion, action

    def test_strict_rest_failure_precedes_original_leave_and_reference_creation(self):
        self.original._validate_session_rest.side_effect = ValueError('Rest changed')
        with self.assertRaisesRegex(ValueError, 'Rest changed'):
            self.adopt()
        self.original.leave.assert_not_called()
        self.ua._mapping.assert_not_called()
        self.assertFalse(self.target.is_adopted(self.rig))
        self.assertIsNone(self.saved.rig)

    def test_packet_failure_rolls_back_original_mode_before_any_adoption_commit(self):
        self.ua._mapping.side_effect = ValueError('bind mismatch')
        with self.assertRaisesRegex(ValueError, 'bind mismatch'):
            self.adopt()
        self.original.leave.assert_called_once_with(self.context, self.rig)
        self.target._apply_capture.assert_called_once_with(self.context, self.rig, self.capture)
        self.assertFalse(self.target.is_adopted(self.rig))
        self.assertIsNone(self.saved.rig)
        self.assertFalse(self.data.use_fake_user)

    def test_all_ready_packets_are_checked_and_historical_fbx_is_not_an_import_input(self):
        self.saved.catalog.append(SimpleNamespace(clip_key='run', ready=True))
        self.adopt()
        self.assertEqual(self.ua._mapping.call_count, 2)
        for call in self.worklist._prepared.call_args_list:
            self.assertEqual(call.kwargs, {'existing_target': True})
        self.assertIs(self.saved.rig, self.rig)
        self.assertTrue(self.data.use_fake_user)

    def test_persisted_references_roundtrip_preserves_id_identity(self):
        self.adopt()
        record = self.target._load(self.rig)
        self.assertIs(record['rig'], self.rig)
        self.assertIs(record['scene'], self.scene)
        self.assertIs(record['capture']['targets'][0]['data'], self.data)
        self.assertIs(record['motion_data'], self.data)
        self.assertEqual(record['packet_sha256'], self.packet['_sha256'])
        self.assertEqual(record['mapped_names'], ['forearm.L'])

    def test_freeze_detaches_nested_property_storage_and_retains_id_references(self):
        original_refs = {'target': self.rig}
        original_session = {'display': {'collections': {'Original': [True, False]}},
                            'refs': original_refs}
        frozen = self.target._freeze(original_session)
        original_refs.clear()
        original_session['display']['collections']['Original'][0] = False
        original_session.clear()
        self.assertIs(frozen['refs']['target'], self.rig)
        self.assertEqual(frozen['display']['collections']['Original'], [True, False])

    def test_missing_persisted_id_ref_rejected(self):
        self.adopt()
        self.rig[self.target.REFS].clear()
        with self.assertRaisesRegex(ValueError, 'removed'):
            self.target._load(self.rig)

    def test_incomplete_top_level_record_rejected(self):
        self.rig[self.target.SESSION] = '{"version":1}'
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.target._load(self.rig)

    def test_truncated_saved_snapshot_rejected_before_restore_mutations(self):
        self.adopt()
        encoded = json.loads(self.rig[self.target.SESSION])
        del encoded['capture']['targets'][0]['data_fake_user']
        self.rig[self.target.SESSION] = json.dumps(encoded)
        self.target._apply_capture.reset_mock()
        with self.assertRaisesRegex(ValueError, 'changed or was truncated'):
            self.target.restore_main_rig(self.context)
        self.target._apply_capture.assert_not_called()
        self.assertTrue(self.target.is_adopted(self.rig))

    def test_unrelated_worklist_rig_is_not_reassigned(self):
        other = ID(name='Other Rig')
        self.saved.rig = other
        with self.assertRaisesRegex(ValueError, 'another rig'):
            self.adopt()
        self.assertIs(self.saved.rig, other)
        self.original.leave.assert_not_called()

    def test_no_prepared_packets_does_not_leave_original(self):
        self.saved.catalog.clear()
        with self.assertRaisesRegex(ValueError, 'Prepare at least'):
            self.adopt()
        self.original.leave.assert_not_called()

    def test_restore_validation_failure_precedes_all_recovery_mutations(self):
        self.adopt()
        self.target._apply_capture.reset_mock()
        self.target._capture.reset_mock()
        with patch.object(self.target, '_validate_record', side_effect=ValueError('live graph changed')):
            with self.assertRaisesRegex(ValueError, 'live graph changed'):
                self.target.restore_main_rig(self.context)
        self.target._capture.assert_not_called()
        self.target._apply_capture.assert_not_called()
        self.assertTrue(self.target.is_adopted(self.rig))

    def test_restore_keeps_rows_and_requires_explicit_readoption(self):
        self.adopt()
        drafts = [SimpleNamespace(name='Artist draft', custom_action=ID(library=None))]
        self.saved.items = drafts
        with patch.object(self.target, '_validate_record'):
            self.target.restore_main_rig(self.context)
        self.assertIs(self.saved.items, drafts)
        self.assertTrue(self.target.managed(self.rig))
        self.assertFalse(self.target.is_adopted(self.rig))
        with self.assertRaisesRegex(ValueError, 'Use Main Rig'):
            self.target.ensure_active(self.context, self.rig)

    def test_no_joint_translation_never_swaps_or_copies_artist_data(self):
        self.adopt()
        self.target.restore_import_preview(self.context, self.rig, ID())
        self.ua.restore_preview.assert_called_once_with(self.context, self.rig)
        self.ua._swap_armature_data.assert_not_called()

    def test_necessary_joint_translation_copy_persists_only_after_native_restore(self):
        self.adopt()
        preview = ID(name='Translation Copy', use_fake_user=False)
        action = ID()
        action[self.ua.PREVIEW_DATA_KEY] = preview
        self.target.restore_import_preview(self.context, self.rig, action)
        self.ua.restore_preview.assert_called_once_with(self.context, self.rig)
        self.ua._swap_armature_data.assert_called_once_with(self.context, self.rig, preview)
        self.assertIs(self.target._load(self.rig)['motion_data'], preview)
        self.assertFalse(preview.use_fake_user)

    def test_rest_view_is_absent_for_ordinary_or_original_artist_data(self):
        self.assertIsNone(self.target.validation_rest_view(self.rig, ('forearm.L',)))
        self.adopt()
        self.assertIsNone(self.target.validation_rest_view(self.rig, ('forearm.L',)))
        self.ua._same_rest_bone.assert_not_called()

    def test_rest_view_delegates_to_protected_original_and_rejects_foreign_bone(self):
        original, bone, motion = self.motion_copy()
        view = self.target.validation_rest_view(self.rig, (bone.name,))
        state = {'use_connect': True}
        self.assertTrue(view.matches(bone, state))
        self.limb._rest_state_matches.assert_called_once_with(original, state)
        self.assertEqual(view.details(bone, state), 'strict original details')
        self.limb._rest_state_mismatch_details.assert_called_once_with(original, state)
        foreign = ID(name=bone.name, id_data=self.data, use_connect=True)
        self.assertFalse(view.matches(foreign, state))
        self.assertIn('outside', view.details(foreign, state))
        with self.assertRaises(AttributeError):
            view.motion_data = self.data

    def test_rest_view_rejects_unrecorded_motion_data(self):
        original, bone, motion = self.motion_copy()
        self.rig.data = ID(name='Unknown Copy', bones=motion.bones)
        with self.assertRaisesRegex(ValueError, 'owned motion-copy Rest changed'):
            self.target.validation_rest_view(self.rig, (bone.name,))

    def test_rest_view_source_projection_authenticates_current_motion_data(self):
        original, bone, motion = self.motion_copy()
        view = self.target.validation_rest_view(self.rig, (bone.name,))
        self.assertIs(view.source_bone(bone), original)
        outsiders = (None, original, ID(name=bone.name, id_data=self.data),
                     ID(name='Unknown Bone', id_data=motion))
        for outsider in outsiders:
            with self.subTest(bone=outsider):
                with self.assertRaisesRegex(ValueError, 'outside the validated Worklist motion data'):
                    view.source_bone(outsider)

    def test_rest_view_rejects_changed_original_or_motion_rest(self):
        original, bone, motion = self.motion_copy()
        for results in ((False,), (True, False)):
            with self.subTest(results=results):
                self.ua._matches_rest_state.side_effect = results
                with self.assertRaisesRegex(ValueError, 'owned motion-copy Rest changed'):
                    self.target.validation_rest_view(self.rig, (bone.name,))

    def test_rest_view_rejects_changed_geometry_or_inventory(self):
        original, bone, motion = self.motion_copy()
        self.ua._same_rest_bone.return_value = False
        with self.assertRaisesRegex(ValueError, 'geometry, parents or inheritance'):
            self.target.validation_rest_view(self.rig, (bone.name,))
        self.ua._same_rest_bone.return_value = True
        motion.bones['Foreign Bone'] = ID(name='Foreign Bone', id_data=motion, use_connect=False)
        with self.assertRaisesRegex(ValueError, 'bone inventory'):
            self.target.validation_rest_view(self.rig, (bone.name,))

    def test_rest_view_rejects_unmapped_connect_change_and_reverse_transition(self):
        original, bone, motion = self.motion_copy()
        with self.assertRaisesRegex(ValueError, 'unapproved connected-bone flag'):
            self.target.validation_rest_view(self.rig, ())
        original.use_connect, bone.use_connect = False, True
        with self.assertRaisesRegex(ValueError, 'unapproved connected-bone flag'):
            self.target.validation_rest_view(self.rig, (bone.name,))

    def test_rest_view_rejects_owned_control_or_dress_connect_change(self):
        original, bone, motion = self.motion_copy()
        for owner, value in ((self.limb.OWNER_KEY, 'limb_ik'), (self.skirt.OWNER_KEY, 'skirt_rig_v1')):
            for changed in (original, bone):
                with self.subTest(owner=owner, original=changed is original):
                    changed[owner] = value
                    with self.assertRaisesRegex(ValueError, 'unapproved connected-bone flag'):
                        self.target.validation_rest_view(self.rig, (bone.name,))
                    del changed[owner]

    def test_rest_view_allows_authored_hair_source_ownership(self):
        original, bone, motion = self.motion_copy()
        original['character_designer_hair_bones_owner'] = 'hair_bones_v1'
        bone['character_designer_hair_bones_owner'] = 'hair_bones_v1'
        self.assertTrue(self.target.validation_rest_view(self.rig, (bone.name,)).matches(bone, {}))

    def test_control_rest_view_uses_saved_mapping_without_packet_or_mapping_calls(self):
        self.assertIsNone(self.target.control_rest_view(self.rig))
        original, bone, motion = self.motion_copy()
        self.ua._mapping.reset_mock()
        self.ua.load_package = Mock(side_effect=AssertionError('Control checks may not read packets'))
        view = self.target.control_rest_view(self.rig)
        self.assertIs(view.source_bone(bone), original)
        self.ua._mapping.assert_not_called()
        self.ua.load_package.assert_not_called()
        self.rig.data = self.data
        self.assertIsNone(self.target.control_rest_view(self.rig))

    def test_control_rest_view_rejects_malformed_or_foreign_saved_names(self):
        original, bone, motion = self.motion_copy()
        record = self.target._load(self.rig)
        invalid = (None, [], [bone.name, bone.name], ['z', 'a'], [42], [''], ['Unknown'])
        for names in invalid:
            with self.subTest(names=names):
                record['mapped_names'] = names
                self.target._store(self.rig, record)
                with self.assertRaisesRegex(ValueError, 'mapped'):
                    self.target.control_rest_view(self.rig)

    def test_control_rest_view_rejects_record_rig_mismatch_and_foreign_data(self):
        original, bone, motion = self.motion_copy()
        record = self.target._load(self.rig)
        record['rig'] = ID(name='Foreign Rig')
        self.target._store(self.rig, record)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.target.control_rest_view(self.rig)
        record['rig'] = self.rig
        self.target._store(self.rig, record)
        self.rig.data = ID(name='Foreign Data', bones=motion.bones)
        with self.assertRaisesRegex(ValueError, 'owned motion-copy Rest changed'):
            self.target.control_rest_view(self.rig)

    def test_adoption_rejects_different_prepared_packet_bone_inventories(self):
        self.saved.catalog.append(SimpleNamespace(clip_key='run', ready=True))
        self.ua._mapping.side_effect = (SimpleNamespace(indices={'forearm.L': 0}),
                                       SimpleNamespace(indices={'forearm.R': 0}))
        with self.assertRaisesRegex(ValueError, 'same validated Main Rig bone inventory'):
            self.adopt()
        self.assertFalse(self.target.is_adopted(self.rig))
        self.assertIsNone(self.saved.rig)

    def test_restore_keeps_custom_owned_original_and_motion_data_references(self):
        original, motion, action = self.restored_draft()
        self.assertIs(self.rig.data, self.data)
        self.assertIs(action[self.ua.ORIGINAL_DATA_KEY], self.data)
        self.assertIs(action[self.ua.PREVIEW_DATA_KEY], motion)
        self.assertIs(action[self.target.ACTION_RIG_KEY], self.rig)
        self.assertIn(self.target.ACTION_MOTION_KEY, action)
        self.assertFalse(self.target.is_adopted(self.rig))
        self.assertEqual(action['artist_curves'], 'unchanged world-motion curves')

    def test_readoption_recreates_current_artist_data_and_keeps_action_curves(self):
        original, previous_motion, action = self.restored_draft()
        self.data['artist_colors_collections'] = 'updated while restored'
        self.adopt()
        editing = self.rig.data
        self.assertIsNot(editing, self.data)
        self.assertIsNot(editing, previous_motion)
        self.assertEqual(editing['artist_colors_collections'], 'updated while restored')
        self.assertTrue(original.use_connect)
        self.assertFalse(editing.bones[original.name].use_connect)
        self.assertIs(self.target._load(self.rig)['motion_data'], editing)
        self.assertIs(action[self.ua.PREVIEW_DATA_KEY], editing)
        self.assertIs(action[self.ua.ORIGINAL_DATA_KEY], self.data)
        self.assertEqual(action['artist_curves'], 'unchanged world-motion curves')
        self.worklist._slot.assert_called_with(action, 11)
        self.worklist._check_baseline.assert_called_once_with(self.saved.items[0])
        self.assertEqual(self.ua._swap_armature_data.call_args.kwargs,
            {'allow_joint_translation': True, 'joint_translation_names': frozenset({'forearm.L'})})

    def test_readoption_without_joint_translation_keeps_original_data(self):
        original, previous_motion, action = self.restored_draft(translated=False)
        self.adopt()
        self.assertIs(self.rig.data, self.data)
        self.ua._swap_armature_data.assert_not_called()
        self.assertIs(action[self.ua.PREVIEW_DATA_KEY], self.data)

    def test_readoption_rejects_wrong_rig_recovery_before_original_leave(self):
        original, previous_motion, action = self.restored_draft()
        action[self.target.ACTION_RIG_KEY] = ID(name='Foreign Rig')
        with self.assertRaisesRegex(ValueError, 'lost its original Main Rig'):
            self.adopt()
        self.original.leave.assert_not_called()
        self.ua._swap_armature_data.assert_not_called()
        self.assertIs(self.rig.data, self.data)

    def test_readoption_rejects_changed_motion_geometry_before_original_leave(self):
        original, previous_motion, action = self.restored_draft()
        self.ua._same_rest_bone.return_value = False
        with self.assertRaisesRegex(ValueError, 'geometry, parents or inheritance'):
            self.adopt()
        self.original.leave.assert_not_called()
        self.ua._swap_armature_data.assert_not_called()

    def test_readoption_rejects_changed_original_rest_record_before_original_leave(self):
        original, previous_motion, action = self.restored_draft()
        action[self.ua.ORIGINAL_REST_KEY] = '{"Rest":"tampered"}'
        with self.assertRaisesRegex(ValueError, 'recovery record is invalid'):
            self.adopt()
        self.original.leave.assert_not_called()
        self.ua._swap_armature_data.assert_not_called()

    def test_readoption_failure_preserves_prior_copy_refs_and_removes_only_new_unused_copy(self):
        original, previous_motion, action = self.restored_draft()
        self.ua._mapping.side_effect = (SimpleNamespace(indices={'forearm.L': 0}),
                                       ValueError('copied graph changed'))
        with self.assertRaisesRegex(ValueError, 'copied graph changed'):
            self.adopt()
        created = self.ua._swap_armature_data.call_args.args[2]
        self.assertIs(self.rig.data, self.data)
        self.assertIs(action[self.ua.PREVIEW_DATA_KEY], previous_motion)
        self.assertEqual(action['artist_curves'], 'unchanged world-motion curves')
        self.assertFalse(self.target.is_adopted(self.rig))
        self.assertTrue(self.rig[self.target.RESTORED])
        self.armatures.remove.assert_called_once_with(created)

    def test_single_armature_user_keeps_ordinary_single_user_fast_path(self):
        self.assertTrue(self.target.single_armature_user(self.rig))
        self.bpy_data.user_map.assert_not_called()
        self.data.users = 2
        self.assertFalse(self.target.single_armature_user(self.rig))

    def test_single_armature_user_accepts_authenticated_session_refs(self):
        original, bone, motion = self.motion_copy()
        motion.users = 3
        self.bpy_data.user_map.return_value = {motion: {self.rig}}
        self.assertTrue(self.target.single_armature_user(self.rig))

    def test_single_armature_user_accepts_restored_custom_recovery_refs(self):
        original, motion, action = self.restored_draft()
        self.data.users = 2
        self.bpy_data.user_map.return_value = {self.data: {self.rig, action}}
        self.assertTrue(self.target.single_armature_user(self.rig))

    def test_single_armature_user_rejects_real_shared_object_binding(self):
        original, motion, action = self.restored_draft()
        self.data.users = 3
        clone = ID(name='Another bound rig', type='ARMATURE', data=self.data)
        self.bpy_data.objects.append(clone)
        self.bpy_data.user_map.return_value = {self.data: {self.rig, action, clone}}
        self.assertFalse(self.target.single_armature_user(self.rig))

    def test_single_armature_user_rejects_foreign_action_or_object_reference(self):
        original, motion, action = self.restored_draft()
        self.data.users = 3
        for foreign in (ID(name='Unowned Action', library=None),
                        ID(name='Metadata-only foreign Object', type='EMPTY', data=None, library=None)):
            with self.subTest(owner=foreign.name):
                foreign['data_reference'] = self.data
                self.bpy_data.user_map.return_value = {self.data: {self.rig, action, foreign}}
                self.assertFalse(self.target.single_armature_user(self.rig))

    def test_single_armature_user_rejects_unknown_reference_on_owned_custom(self):
        original, motion, action = self.restored_draft()
        self.data.users = 3
        action['unowned_reference'] = {'nested': [self.data]}
        self.bpy_data.user_map.return_value = {self.data: {self.rig, action}}
        self.assertFalse(self.target.single_armature_user(self.rig))

    def test_single_armature_user_rejects_unknown_reference_on_main_rig(self):
        original, bone, motion = self.motion_copy()
        motion.users = 3
        self.rig['unowned_reference'] = motion
        self.bpy_data.user_map.return_value = {motion: {self.rig}}
        self.assertFalse(self.target.single_armature_user(self.rig))
        del self.rig['unowned_reference']
        self.rig[self.target.REFS]['unused_reference'] = motion
        self.assertFalse(self.target.single_armature_user(self.rig))

    def test_single_armature_user_rejects_invalid_rest_or_foreign_rig_record(self):
        original, motion, action = self.restored_draft()
        self.data.users = 2
        self.bpy_data.user_map.return_value = {self.data: {self.rig, action}}
        self.ua._matches_rest_state.return_value = False
        self.assertFalse(self.target.single_armature_user(self.rig))
        self.ua._matches_rest_state.return_value = True
        action[self.target.ACTION_RIG_KEY] = ID(name='Foreign Rig')
        self.assertFalse(self.target.single_armature_user(self.rig))

    def test_single_armature_user_rejects_restored_marker_without_authenticated_users(self):
        self.rig[self.target.RESTORED] = True
        self.data.users = 2
        self.assertFalse(self.target.single_armature_user(self.rig))

    def test_single_armature_user_accepts_removed_row_and_copied_recovery_actions(self):
        original, motion, action = self.restored_draft()
        copied = action.copy()
        copied.name = 'Artist copied local draft'
        self.saved.items = []
        self.data.users = 3
        self.bpy_data.user_map.return_value = {self.data: {self.rig, action, copied}}
        self.assertTrue(self.target.single_armature_user(self.rig))

    def test_single_armature_user_rejects_plaintext_recovery_owner(self):
        original, motion, action = self.restored_draft()
        self.data.users = 2
        action[self.target.ACTION_RIG_KEY] = self.rig.name
        self.bpy_data.user_map.return_value = {self.data: {self.rig, action}}
        self.assertFalse(self.target.single_armature_user(self.rig))


if __name__ == '__main__':
    unittest.main()
