"""Display owner guards and playback deferral preserve explicit rig operations."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class ID(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


class FrameRejectionTests(unittest.TestCase):
    def setUp(self):
        name = '_frame_rejection_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        bpy, types = ModuleType('bpy'), ModuleType('bpy.types')
        types.Operator = object
        bpy.types = types
        bpy.app = NS(handlers=NS(persistent=lambda function: function, **{
            event: [] for event in ('frame_change_post', 'load_post', 'load_pre',
                                   'undo_pre', 'redo_pre', 'animation_playback_pre',
                                   'animation_playback_post')}))
        limb = ModuleType(name + '.limb_ik')
        limb.OWNER_KEY, limb.OWNER_VALUE, limb.ROLE_KEY = 'owner', 'limb', 'role'
        limb._single_armature_user = Mock(return_value=False)
        limb._validate_inventory = Mock(return_value={'verified': True})
        visuals = ModuleType(name + '.limb_fk_visuals')
        visuals.NATIVE_DISPLAY_KEY = 'native_display'
        spine = ModuleType(name + '.spine_ik_fk')
        spine.OWNER_VALUE = 'spine'
        spec = importlib.util.spec_from_file_location(
            name + '.bone_collections', SOURCE / 'bone_collections.py')
        self.groups = importlib.util.module_from_spec(spec)
        modules = patch.dict(sys.modules, {
            name: package, 'bpy': bpy, 'bpy.types': types, spec.name: self.groups,
            limb.__name__: limb, visuals.__name__: visuals, spine.__name__: spine})
        modules.start()
        self.addCleanup(modules.stop)
        spec.loader.exec_module(self.groups)
        clock = patch('time.monotonic', return_value=10.0)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.owner, self.inventory = limb._single_armature_user, limb._validate_inventory
        self.visuals = visuals
        self.data = ID(library=None, users=2, session_uid=18, as_pointer=lambda: 4321)
        self.data[self.groups.PROFILE_KEY] = True
        self.data[self.groups.AUTO_KEY] = 1
        self.data[visuals.NATIVE_DISPLAY_KEY] = 'original token'
        self.collection = ID(bones={'Root': object()})
        self.collection[self.groups.GROUP_KEY] = 'Body'
        self.data.collections_all = {'Body': self.collection}
        bone = ID()
        bone.update(owner='limb', role='HAND_IK')
        self.pose_bone = ID(name='Hand control', bone=bone)
        self.pose_bone['ik_fk'] = 0.0
        self.rig = ID(type='ARMATURE', mode='POSE', library=None, is_editable=True,
                      data=self.data, pose=NS(bones=[self.pose_bone]), as_pointer=lambda: 1234)
        self.scene = ID(objects=[self.rig], session_uid=27, as_pointer=lambda: 987)
        self.groups.has_layout_backup = Mock(return_value=True)
        self.sync = self.groups.sync_limb_display = Mock()

    def frame(self, **options):
        self.groups._frame_visibility(self.scene, **options)

    def assert_no_display_write(self):
        self.sync.assert_not_called()
        self.inventory.assert_not_called()
        self.assertEqual(self.pose_bone['ik_fk'], 0.0)
        self.assertEqual(set(self.collection.bones), {'Root'})
        self.assertEqual(self.data[self.visuals.NATIVE_DISPLAY_KEY], 'original token')

    def test_rejected_same_state_checks_owner_once_and_never_syncs(self):
        before_data, before_collection = dict(self.data), dict(self.collection)
        expected = self.groups._display_frame_state(self.rig, self.collection)
        for _frame in range(5):
            self.frame()
        self.owner.assert_called_once_with(self.rig)
        self.assertEqual(self.groups._FRAME_CACHE, {})
        self.assertEqual(self.groups._FRAME_OWNER_REJECTIONS[1234][0], expected)
        self.assertEqual(dict(self.data), before_data)
        self.assertEqual(dict(self.collection), before_collection)
        self.assert_no_display_write()

    def test_each_display_state_component_change_retries_fresh_owner_proof(self):
        changes = (
            lambda: self.pose_bone.__setitem__('ik_fk', 1.0),
            lambda: self.collection.bones.__setitem__('Extra', object()),
            lambda: self.data.__setitem__(self.visuals.NATIVE_DISPLAY_KEY, 'changed token'),
        )
        for change in changes:
            with self.subTest(change=changes.index(change)):
                self.groups._FRAME_CACHE.clear()
                self.groups._FRAME_OWNER_REJECTIONS.clear()
                self.owner.reset_mock()
                self.pose_bone['ik_fk'] = 0.0
                self.collection.bones = {'Root': object()}
                self.data[self.visuals.NATIVE_DISPLAY_KEY] = 'original token'
                self.frame()
                change()
                self.frame()
                self.frame()
                self.assertEqual(self.owner.call_count, 2)
                self.sync.assert_not_called()
                self.inventory.assert_not_called()

    def test_explicit_rejection_pop_retries_without_accepting_rejected_owner(self):
        self.frame()
        self.groups._FRAME_OWNER_REJECTIONS.pop(1234)
        self.frame()
        self.assertEqual(self.owner.call_count, 2)
        self.assert_no_display_write()

    def test_changed_request_after_rejection_can_pass_only_full_owner_and_inventory(self):
        events = []

        def owner(_rig):
            events.append('owner')
            return len(events) > 1

        self.owner.side_effect = owner
        self.inventory.side_effect = lambda _rig: events.append('inventory') or {'fresh': True}
        self.sync.side_effect = lambda *_args, **_options: events.append('sync')
        self.frame()
        self.pose_bone['ik_fk'] = 1.0
        self.frame()
        self.assertEqual(events, ['owner', 'owner', 'inventory', 'sync'])
        self.sync.assert_called_once_with(self.rig, {'fresh': True}, force_flags=False)
        self.frame()
        self.assertEqual(events, ['owner', 'owner', 'inventory', 'sync'])

    def test_same_state_foreign_reference_removal_recovers_immediately(self):
        self.pose_bone['ik_fk'] = 1.0
        self.groups._FRAME_CACHE[1234] = self.groups._display_frame_state(self.rig, self.collection)
        cached = dict(self.groups._FRAME_CACHE)
        self.pose_bone['ik_fk'] = 0.0
        self.frame()
        self.assertEqual(self.groups._FRAME_CACHE, cached)
        self.assert_no_display_write()
        # Releasing the foreign ID-property reference decreases native users,
        # while the refused FK request and monotonic time remain unchanged.
        self.data.users = 1
        self.owner.return_value = True
        self.frame()
        self.assertEqual(self.owner.call_count, 2)
        self.inventory.assert_called_once_with(self.rig)
        self.sync.assert_called_once_with(self.rig, {'verified': True}, force_flags=True)
        self.assertNotIn(1234, self.groups._FRAME_OWNER_REJECTIONS)

    def test_same_users_metadata_repair_retries_at_deadline(self):
        self.frame()
        self.owner.return_value = True
        self.clock.return_value = 10.99
        self.frame()
        self.owner.assert_called_once_with(self.rig)
        self.sync.assert_not_called()
        self.clock.return_value = 11.0
        self.frame()
        self.assertEqual(self.owner.call_count, 2)
        self.inventory.assert_called_once_with(self.rig)
        self.sync.assert_called_once_with(self.rig, {'verified': True}, force_flags=False)
        self.assertNotIn(1234, self.groups._FRAME_OWNER_REJECTIONS)

    def test_data_identity_or_users_change_retries_refusal_immediately(self):
        for field, replacement in (('session_uid', 19), ('as_pointer', lambda: 9876), ('users', 3)):
            with self.subTest(field=field):
                self.groups._FRAME_OWNER_REJECTIONS.clear()
                self.owner.reset_mock()
                self.frame()
                setattr(self.data, field, replacement)
                self.frame()
                self.assertEqual(self.owner.call_count, 2)
                self.sync.assert_not_called()
                self.inventory.assert_not_called()

    def test_unknown_shared_owner_stays_rejected_even_with_caller_inventory(self):
        supplied = {'caller_owned_inventory': True}
        self.frame(objects=(self.rig,), verified_inventory=supplied)
        self.frame(objects=(self.rig,), verified_inventory=supplied)
        self.owner.assert_called_once_with(self.rig)
        self.assert_no_display_write()

    def test_existing_positive_cache_still_skips_only_unchanged_display(self):
        self.owner.return_value = True
        self.frame()
        self.frame()
        self.owner.assert_called_once_with(self.rig)
        self.inventory.assert_called_once_with(self.rig)
        self.sync.assert_called_once_with(self.rig, {'verified': True}, force_flags=False)
        self.pose_bone['ik_fk'] = 1.0
        self.frame()
        self.assertEqual(self.owner.call_count, 2)
        self.assertEqual(self.inventory.call_count, 2)
        self.assertEqual(self.sync.call_count, 2)
        self.assertTrue(self.sync.call_args.kwargs['force_flags'])

    def test_inventory_failure_cache_semantics_remain_unchanged(self):
        self.owner.return_value = True
        self.inventory.side_effect = ValueError('unowned rig')
        self.frame()
        self.frame()
        self.owner.assert_called_once_with(self.rig)
        self.inventory.assert_called_once_with(self.rig)
        self.sync.assert_not_called()
        self.assertEqual(self.groups._FRAME_CACHE[1234],
                         self.groups._display_frame_state(self.rig, self.collection))
        self.assertEqual(self.groups._FRAME_OWNER_REJECTIONS, {})

    def test_handler_cleanup_clears_positive_and_rejected_requests(self):
        self.frame()
        self.groups._FRAME_CACHE[1234] = 'previous display'
        bpy = sys.modules['bpy']
        bpy.app.handlers.frame_change_post, bpy.app.handlers.load_post = [], []
        self.groups.unregister_handlers()
        self.assertEqual(self.groups._FRAME_CACHE, {})
        self.assertEqual(self.groups._FRAME_OWNER_REJECTIONS, {})

    def test_playback_skips_even_display_reads_and_preserves_all_caches(self):
        self.groups._FRAME_CACHE[1234] = 'previous display'
        self.groups._FRAME_OWNER_REJECTIONS[1234] = 'previous refusal'
        self.groups._playback_visibility_pre(self.scene)
        with patch.object(self.groups, '_display_frame_state') as state:
            for _frame in range(5):
                self.frame()
            state.assert_not_called()
        self.groups.has_layout_backup.assert_not_called()
        self.owner.assert_not_called()
        self.assert_no_display_write()
        self.assertEqual(self.groups._FRAME_CACHE, {1234: 'previous display'})
        self.assertEqual(self.groups._FRAME_OWNER_REJECTIONS, {1234: 'previous refusal'})
        self.assertEqual(self.groups._FRAME_PLAYBACK_DEFERRED, {(27, 987)})

    def test_stop_checks_fresh_owner_and_inventory_once_even_for_cached_state(self):
        self.owner.return_value = True
        state = self.groups._display_frame_state(self.rig, self.collection)
        self.groups._FRAME_CACHE[1234] = state
        self.groups._FRAME_OWNER_REJECTIONS[1234] = (state, (18, 4321, 2), 11.0)
        self.groups._playback_visibility_pre(self.scene)
        self.frame()
        self.groups._playback_visibility_post(self.scene)
        self.groups._playback_visibility_post(self.scene)
        self.frame()
        self.owner.assert_called_once_with(self.rig)
        self.inventory.assert_called_once_with(self.rig)
        self.sync.assert_called_once_with(self.rig, {'verified': True}, force_flags=False)
        self.assertEqual(self.groups._FRAME_PLAYBACK_SCENES, set())
        self.assertEqual(self.groups._FRAME_PLAYBACK_DEFERRED, set())
        self.assertEqual(self.groups._FRAME_OWNER_REJECTIONS, {})

    def test_stop_preserves_previous_mode_to_force_changed_display_flags(self):
        self.owner.return_value = True
        self.groups._FRAME_CACHE[1234] = self.groups._display_frame_state(self.rig, self.collection)
        self.groups._playback_visibility_pre(self.scene)
        self.pose_bone['ik_fk'] = 1.0
        self.frame()
        self.groups._playback_visibility_post(self.scene)
        self.sync.assert_called_once_with(self.rig, {'verified': True}, force_flags=True)

    def test_stop_still_rejects_unknown_owner_without_success_cache_or_display_write(self):
        self.groups._playback_visibility_pre(self.scene)
        self.frame()
        self.groups._playback_visibility_post(self.scene)
        self.owner.assert_called_once_with(self.rig)
        self.assertEqual(self.groups._FRAME_CACHE, {})
        self.assert_no_display_write()
        self.assertEqual(self.groups._FRAME_PLAYBACK_SCENES, set())
        self.assertEqual(self.groups._FRAME_PLAYBACK_DEFERRED, set())

    def test_stop_preserves_structural_display_guards(self):
        changes = (
            lambda: self.rig.__setitem__('character_designer_body_original_mode_v1', True),
            lambda: self.scene.__setitem__('character_designer_weight_workspace_v1', {'rig': self.rig}),
            lambda: setattr(self.rig, 'mode', 'EDIT'),
            lambda: setattr(self.data, 'library', object()),
            lambda: self.data.__setitem__(self.groups.AUTO_KEY, 0),
            lambda: self.data.__setitem__(self.groups.VIEW_KEY, True),
        )
        for change in changes:
            with self.subTest(change=changes.index(change)):
                self.rig.pop('character_designer_body_original_mode_v1', None)
                self.scene.pop('character_designer_weight_workspace_v1', None)
                self.rig.mode, self.data.library = 'POSE', None
                self.data[self.groups.AUTO_KEY] = 1
                self.data.pop(self.groups.VIEW_KEY, None)
                self.groups._playback_visibility_pre(self.scene)
                self.frame()
                change()
                self.groups._playback_visibility_post(self.scene)
                self.owner.assert_not_called()
                self.assert_no_display_write()

    def test_explicit_transaction_during_playback_keeps_its_original_sync_contract(self):
        self.owner.return_value = True
        supplied = {'caller_owned_inventory': True}
        self.groups._playback_visibility_pre(self.scene)
        self.frame(objects=(self.rig,), verified_inventory=supplied)
        self.owner.assert_called_once_with(self.rig)
        self.inventory.assert_not_called()
        self.sync.assert_called_once_with(self.rig, supplied, force_flags=False)
        self.frame()
        self.groups._playback_visibility_post(self.scene)
        self.assertEqual(self.owner.call_count, 2)
        self.inventory.assert_called_once_with(self.rig)
        self.assertEqual(self.sync.call_count, 2)

    def test_playing_scene_is_independent_of_other_scenes_and_context(self):
        other = ID(objects=[self.rig], session_uid=28, as_pointer=lambda: 988)
        self.owner.return_value = True
        self.groups._playback_visibility_pre(self.scene)
        self.frame()
        self.groups._frame_visibility(other)
        self.owner.assert_called_once_with(self.rig)
        self.groups._playback_visibility_pre(other)
        self.groups._frame_visibility(other)
        self.groups._playback_visibility_post(other)
        self.assertEqual(self.groups._FRAME_PLAYBACK_SCENES, {(27, 987)})
        self.assertEqual(self.groups._FRAME_PLAYBACK_DEFERRED, {(27, 987)})
        self.groups._playback_visibility_post(self.scene)
        self.assertEqual(self.owner.call_count, 3)

    def test_scene_identity_reuse_does_not_defer_a_different_scene(self):
        self.groups._playback_visibility_pre(self.scene)
        replaced = ID(objects=[self.rig], session_uid=29, as_pointer=lambda: 987)
        self.groups._frame_visibility(replaced)
        self.owner.assert_called_once_with(self.rig)

    def test_restore_frame_after_stop_uses_normal_frame_logic(self):
        self.owner.return_value = True
        self.groups._playback_visibility_pre(self.scene)
        self.pose_bone['ik_fk'] = 1.0
        self.frame()
        self.groups._playback_visibility_post(self.scene)
        self.pose_bone['ik_fk'] = 0.0
        self.frame()
        self.assertEqual(self.owner.call_count, 2)
        self.assertEqual(self.sync.call_count, 2)
        self.assertTrue(self.sync.call_args.kwargs['force_flags'])

    def test_unknown_or_no_frame_stop_does_not_sync(self):
        self.groups._playback_visibility_post(self.scene)
        self.groups._playback_visibility_pre(self.scene)
        self.groups._playback_visibility_post(self.scene)
        self.owner.assert_not_called()
        self.assert_no_display_write()

    def test_lifecycle_clear_discards_deferred_work_without_reading_rna(self):
        self.groups._playback_visibility_pre(self.scene)
        self.frame()
        self.groups._clear_playback_visibility(object())
        self.groups._playback_visibility_post(self.scene)
        self.owner.assert_not_called()
        self.assert_no_display_write()
        self.assertEqual(self.groups._FRAME_PLAYBACK_SCENES, set())
        self.assertEqual(self.groups._FRAME_PLAYBACK_DEFERRED, set())

    def test_register_unregister_keeps_exactly_one_owned_handler_and_cleans_playback(self):
        handlers = sys.modules['bpy'].app.handlers
        foreign = lambda *_args: None
        events = ('frame_change_post', 'load_post', 'load_pre', 'undo_pre', 'redo_pre',
                  'animation_playback_pre', 'animation_playback_post')
        for event in events:
            getattr(handlers, event).append(foreign)
        with patch.object(self.groups, '_schedule_native_migration'):
            self.groups.register_handlers()
            self.groups.register_handlers()
        for event in events:
            self.assertEqual(len(getattr(handlers, event)), 2)
        self.groups._playback_visibility_pre(self.scene)
        self.frame()
        self.groups.unregister_handlers()
        for event in events:
            self.assertEqual(getattr(handlers, event), [foreign])
        self.assertEqual(self.groups._FRAME_PLAYBACK_SCENES, set())
        self.assertEqual(self.groups._FRAME_PLAYBACK_DEFERRED, set())


if __name__ == '__main__':
    unittest.main()
