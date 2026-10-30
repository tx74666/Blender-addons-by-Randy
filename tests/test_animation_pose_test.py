"""Persistent ownership and authored-draft transaction tests, without Blender."""

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


class Slots(list):
    def new(self, *, id_type, name):
        slot = NS(identifier='OB' + name, handle=len(self) + 1, target_id_type=id_type)
        self.append(slot)
        return slot


class Action(ID):
    def __init__(self, name):
        super().__init__(name=name, slots=Slots(), layers=[], fcurves=[],
                         is_action_layered=True, library=None, asset_data=None,
                         binding_users=0, use_fake_user=False)

    @property
    def users(self):
        return self.binding_users + int(self.use_fake_user)


class Actions(list):
    def new(self, name):
        action = Action(name)
        self.append(action)
        return action

    def remove(self, action):
        for index, value in enumerate(self):
            if value is action:
                del self[index]
                return
        raise ValueError('not present')


class Animation:
    def __init__(self):
        self._action = None
        self.action_slot = None
        self.use_nla, self.action_blend_type, self.action_influence = True, 'ADD', .7
        self.action_extrapolation = 'NOTHING'

    @property
    def action(self):
        return self._action

    @action.setter
    def action(self, value):
        if self._action is not None:
            self._action.binding_users -= 1
        self._action = value
        if value is not None:
            value.binding_users += 1


def authored(action):
    action.layers = [NS(strips=[NS(type='KEYFRAME', channelbags=[NS(fcurves=[object()])])])]


class PoseTestTests(unittest.TestCase):
    def setUp(self):
        name = '_base_pose_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        ua = ModuleType(name + '.unity_animation')
        ua._set_playing = Mock()
        rest = ModuleType(name + '.animation_rest_pose')
        rest.PREVIEW_KEY = 'rest_marker'
        self.actions = Actions()
        self.bpy = NS(data=NS(actions=self.actions))
        modules = patch.dict(sys.modules, {name: package, 'bpy': self.bpy,
                                          ua.__name__: ua, rest.__name__: rest})
        modules.start()
        self.addCleanup(modules.stop)
        package.unity_animation, package.animation_rest_pose = ua, rest
        spec = importlib.util.spec_from_file_location(name + '.animation_pose_test',
                                                      SOURCE / 'animation_pose_test.py')
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)
        self.original = self.actions.new('Idle')
        self.original.slots.new(id_type='OBJECT', name='Main')
        self.original['artist_keys'] = [('frame', 1.0, 'untouched')]
        self.ad = Animation()
        self.ad.action, self.ad.action_slot = self.original, self.original.slots[0]
        self.rig = ID(name='Main', data=NS(pose_position='REST'), animation_data=self.ad)
        self.rig.animation_data_create = lambda: self.ad
        self.context = NS(scene=NS(tool_settings=NS(use_keyframe_insert_auto=True)),
                          view_layer=NS(update=Mock()), pose='original')
        self.neutral = Mock(side_effect=lambda c, r: setattr(c, 'pose', 'neutral'))
        for attr, value in {
                '_rig': lambda c: self.rig, '_admission': Mock(),
                '_structure': lambda r: 'structure', '_validate_restore': Mock(),
                '_enter_pose': Mock(), '_neutral': self.neutral,
                '_capture': self.capture, '_restore': self.restore}.items():
            p = patch.object(self.module, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def capture(self, context, rig):
        action = rig.animation_data.action
        state = {'label': context.pose, 'had_action': action is not None}
        refs = {'rig': rig}
        if action is not None:
            refs['previous_action'] = action
        return state, refs

    def restore(self, context, rig, state, refs):
        self.ad.action = refs.get('previous_action')
        self.ad.action_slot = self.ad.action.slots[0] if self.ad.action else None
        context.pose = state['label']

    def test_reset_reuses_empty_action_and_preserves_first_checkpoint(self):
        self.module.start_or_reset(self.context)
        scratch = self.ad.action
        self.context.pose = 'bent'
        result = self.module.start_or_reset(self.context)
        self.assertTrue(result['reset'])
        self.assertIs(self.ad.action, scratch)
        self.assertEqual(self.context.pose, 'neutral')
        self.assertEqual(self.module._unpack(scratch[self.module.SESSION_KEY])['before']['label'],
                         'original')
        self.module.finish(self.context)
        self.assertIs(self.ad.action, self.original)
        self.assertEqual(self.context.pose, 'original')
        self.assertEqual(self.original['artist_keys'], [('frame', 1.0, 'untouched')])
        self.assertFalse(self.module.active(self.rig))
        self.assertTrue(all(a is not scratch for a in self.actions))

    def test_keyed_reset_keeps_draft_and_original_action_reference(self):
        self.module.start_or_reset(self.context)
        old = self.ad.action
        authored(old)
        curve = old.layers[0].strips[0].channelbags[0].fcurves[0]
        result = self.module.start_or_reset(self.context)
        self.assertIs(result['draft'], old)
        self.assertIsNot(self.ad.action, old)
        self.assertTrue(old.use_fake_user)
        self.assertIs(old.layers[0].strips[0].channelbags[0].fcurves[0], curve)
        self.assertNotIn(self.module.SESSION_KEY, old)
        self.assertIs(self.ad.action[self.module.REFS_KEY]['previous_action'], self.original)
        self.module.finish(self.context)
        self.assertIs(self.ad.action, self.original)
        self.assertTrue(any(a is old for a in self.actions))

    def test_finish_retains_current_authored_draft(self):
        self.module.start_or_reset(self.context)
        scratch = self.ad.action
        authored(scratch)
        result = self.module.finish(self.context)
        self.assertIs(result['draft'], scratch)
        self.assertTrue(scratch.use_fake_user)
        self.assertNotIn(self.module.REFS_KEY, scratch)

    def test_foreign_user_and_slot_refuse_without_destroying_recovery(self):
        self.module.start_or_reset(self.context)
        scratch = self.ad.action
        scratch.binding_users += 1
        with self.assertRaisesRegex(ValueError, 'Another user'):
            self.module.finish(self.context)
        self.assertTrue(self.module.active(self.rig))
        self.assertIs(self.ad.action, scratch)
        scratch.binding_users -= 1
        scratch.slots.new(id_type='OBJECT', name='Foreign')
        with self.assertRaisesRegex(ValueError, 'foreign slots'):
            self.module.finish(self.context)
        self.assertIn(self.module.SESSION_KEY, scratch)

    def test_failed_initial_match_restores_binding_and_removes_empty_scratch(self):
        self.neutral.side_effect = ValueError('unsupported IK')
        with self.assertRaisesRegex(ValueError, 'unsupported IK'):
            self.module.start_or_reset(self.context)
        self.assertIs(self.ad.action, self.original)
        self.assertEqual(self.context.pose, 'original')
        self.assertFalse(self.module.active(self.rig))
        self.assertEqual(len(self.actions), 1)

    def test_failed_keyed_reset_keeps_current_draft_session_and_first_checkpoint(self):
        self.module.start_or_reset(self.context)
        scratch = self.ad.action
        authored(scratch)
        self.context.pose = 'user bend'
        self.neutral.side_effect = ValueError('unsupported IK')
        with self.assertRaisesRegex(ValueError, 'unsupported IK'):
            self.module.start_or_reset(self.context)
        self.assertIs(self.ad.action, scratch)
        self.assertTrue(self.module.active(self.rig))
        self.assertEqual(self.context.pose, 'user bend')
        self.assertEqual(len(self.actions), 2)
        self.assertEqual(self.module._unpack(scratch[self.module.SESSION_KEY])['before']['label'],
                         'original')

    def test_suspended_failed_operation_restores_test_pose_and_binding(self):
        self.module.start_or_reset(self.context)
        scratch = self.ad.action
        self.context.pose = 'user bend'
        with self.assertRaisesRegex(RuntimeError, 'rejected'):
            with self.module.suspended_for_operation(self.context, self.rig):
                self.assertIs(self.ad.action, self.original)
                raise RuntimeError('rejected')
        self.assertIs(self.ad.action, scratch)
        self.assertTrue(self.module.active(self.rig))
        self.assertEqual(self.context.pose, 'user bend')

    def test_suspended_success_commits_exit_and_keeps_operation_binding(self):
        self.module.start_or_reset(self.context)
        scratch = self.ad.action
        authored(scratch)
        next_action = self.actions.new('Other Motion')
        with self.module.suspended_for_operation(self.context, self.rig):
            self.assertIs(self.ad.action, self.original)
            self.ad.action = next_action
        self.assertIs(self.ad.action, next_action)
        self.assertFalse(self.module.active(self.rig))
        self.assertTrue(scratch.use_fake_user)

    def test_saved_payload_tamper_is_refused(self):
        self.module.start_or_reset(self.context)
        scratch = self.ad.action
        raw = scratch[self.module.SESSION_KEY]
        scratch[self.module.SESSION_KEY] = raw.replace('original', 'changed')
        with self.assertRaisesRegex(ValueError, 'record changed'):
            self.module.finish(self.context)
        self.assertIs(self.ad.action, scratch)

    def test_action_reload_needs_no_process_cache(self):
        self.module.start_or_reset(self.context)
        self.context.pose = 'later pose'
        action, payload, refs = self.module._record(self.context, self.rig)
        self.assertEqual(payload['before']['label'], 'original')
        self.assertIs(refs['previous_action'], self.original)
        self.assertIs(action, self.ad.action)

    def test_poll_never_decodes_session_or_scans_structure(self):
        self.rig.type, self.rig.library, self.rig.override_library = 'ARMATURE', None, None
        self.rig.data.library, self.rig.data.users = None, 1
        self.context.mode = 'POSE'
        self.context.scene.character_designer_setup = NS(rig=self.rig)
        self.context.view_layer.objects = {'Main': self.rig}
        self.rig[self.module.TOKEN_KEY] = 'invalid recovery still needs an execute error'
        with patch.object(self.module, '_record', side_effect=AssertionError('expensive')):
            with patch.object(self.module, '_structure', side_effect=AssertionError('expensive')):
                self.assertTrue(self.module.can_start(self.context))

    def test_empty_curve_and_pose_marker_are_retained_as_authored_content(self):
        action = self.actions.new('Artist Draft')
        action.layers = [NS(strips=[NS(type='KEYFRAME', channelbags=[NS(fcurves=[NS(keyframe_points=[])])])])]
        self.assertTrue(self.module._has_content(action))
        action.layers = []
        action.pose_markers = [NS(name='Artist marker')]
        self.assertTrue(self.module._has_content(action))


class NativeIdentity(NS):
    """Native Blender IDs compare by identity, unlike dict/namespace stubs."""

    def __eq__(self, other):
        return self is other


class PoseOwnerTests(unittest.TestCase):
    """Pose isolation depends on actual Object bindings, not recovery history."""

    def setUp(self):
        name = '_pose_owner_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        self.data = NativeIdentity(library=None, override_library=None,
                                   is_editable=True, users=7)
        self.rig = NativeIdentity(type='ARMATURE', data=self.data, library=None,
                                  override_library=None, is_editable=True)
        self.objects = [self.rig]
        self.history_proof = Mock(side_effect=AssertionError('Rest recovery is outside pose isolation'))
        self.bpy = NS(data=NS(objects=self.objects, user_map=self.history_proof))
        modules = patch.dict(sys.modules, {name: package, 'bpy': self.bpy})
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location(name + '.animation_pose_test',
                                                      SOURCE / 'animation_pose_test.py')
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

    def second_object(self, **fields):
        return NativeIdentity(type='ARMATURE', data=self.data, library=None,
                              override_library=None, is_editable=True, **fields)

    def test_extra_retained_id_users_do_not_require_rest_recovery_proof(self):
        self.assertTrue(self.module._pose_owner(self.rig))
        self.assertEqual(self.data.users, 7)
        self.history_proof.assert_not_called()

    def test_raw_user_count_is_never_the_pose_ownership_proof(self):
        class DataWithoutRawCount(NativeIdentity):
            @property
            def users(self):
                raise AssertionError('Raw ID user count is not an Object binding inventory')
        self.rig.data = DataWithoutRawCount(library=None, override_library=None,
                                            is_editable=True)
        self.assertTrue(self.module._pose_owner(self.rig))
        self.history_proof.assert_not_called()

    def test_hidden_second_object_is_rejected_even_if_raw_count_claims_one(self):
        self.data.users = 1
        hidden = self.second_object(hide_viewport=True, hide_render=True)
        self.objects.append(hidden)
        self.assertFalse(self.module._pose_owner(self.rig))

    def test_unlinked_second_object_is_rejected_outside_the_current_scene(self):
        unlinked = self.second_object(users_collection=(), users_scene=())
        self.objects.append(unlinked)
        self.assertFalse(self.module._pose_owner(self.rig))

    def test_local_editable_rig_and_data_are_both_required(self):
        cases = [(self.rig, 'library', object()), (self.data, 'library', object()),
                 (self.rig, 'override_library', object()),
                 (self.data, 'override_library', object()),
                 (self.rig, 'is_editable', False), (self.data, 'is_editable', False)]
        for owner, field, bad in cases:
            with self.subTest(owner='rig' if owner is self.rig else 'data', field=field):
                old = getattr(owner, field)
                setattr(owner, field, bad)
                self.assertFalse(self.module._pose_owner(self.rig))
                setattr(owner, field, old)

    def test_the_one_actual_object_must_be_the_requested_rig(self):
        self.objects.clear()
        self.assertFalse(self.module._pose_owner(self.rig))
        self.objects.append(self.second_object())
        self.assertFalse(self.module._pose_owner(self.rig))

    def test_actual_binding_inventory_is_rechecked_on_every_call(self):
        self.assertTrue(self.module._pose_owner(self.rig))
        other = self.second_object()
        self.objects.append(other)
        self.assertFalse(self.module._pose_owner(self.rig))
        self.objects.remove(other)
        self.assertTrue(self.module._pose_owner(self.rig))


if __name__ == '__main__':
    unittest.main()
