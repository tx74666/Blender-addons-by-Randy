"""Exact Discard membership and conservative native-ID cleanup, without Blender.

Reuse the existing reconciliation fixture without inheriting its test methods.
The Action model counts managed row pointers and fake users independently.
"""

from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_animation_worklist_reconcile as fixtures


class Action(fixtures.ID):
    __eq__ = object.__eq__
    __hash__ = object.__hash__

    def __init__(self, owner, name, *, fake=False):
        super().__init__(name=name, name_full=name, library=None, override_library=None,
                         asset_data=None, slots=[SimpleNamespace(handle=1, target_id_type='OBJECT')],
                         pose_markers=[], native_users=0, native_owners=set(), _fake=fake)
        self.owner = owner

    def __bool__(self):
        return True  # An empty native ID is still a valid Action.

    @property
    def use_fake_user(self):
        return self._fake

    @use_fake_user.setter
    def use_fake_user(self, value):
        self._fake = value

    @property
    def users(self):
        return self.native_users + int(self._fake) + self.owner.row_users(self)


class Actions(list):
    def __init__(self):
        super().__init__()
        self.removals = []
        self.failure = None
        self.before_remove = None

    def get(self, name):
        return next((action for action in self if action.name_full == name), None)

    def remove(self, action, *, do_unlink):
        self.removals.append((action, do_unlink, action.users, action.use_fake_user))
        if self.before_remove:
            self.before_remove(action)
        if self.failure:
            raise self.failure
        if do_unlink or action.users:
            raise AssertionError('Deletion must have zero users and never unlink owners.')
        super().remove(action)


class ActionAlias:
    """A distinct Python wrapper over the same native Action state."""

    def __init__(self, action):
        object.__setattr__(self, '_action', action)

    def __getattr__(self, name):
        return getattr(self._action, name)

    def __setattr__(self, name, value):
        setattr(self._action, name, value)


class DiscardTests(unittest.TestCase):
    sha = staticmethod(fixtures.ReconcileTests.sha)
    clip = fixtures.ReconcileTests.clip
    row = fixtures.ReconcileTests.row
    fill_catalog = fixtures.ReconcileTests.fill_catalog
    capture = fixtures.ReconcileTests.capture
    restore = fixtures.ReconcileTests.restore
    restore_metadata = fixtures.ReconcileTests.restore_metadata
    adopt = fixtures.ReconcileTests.adopt

    def setUp(self):
        fixtures.ReconcileTests.setUp(self)
        self.target.ACTION_RIG_KEY = 'character_designer_worklist_motion_rig_v1'
        self.actions = Actions()
        self.bpy.data = SimpleNamespace(actions=self.actions, scenes=[self.scene],
            user_map=lambda *, subset: {action: set(action.native_owners) for action in subset})
        spec = importlib.util.spec_from_file_location(self.package_name + '.baseline_real',
            fixtures.SOURCE / 'animation_worklist.py')
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        self.worklist._check_baseline = baseline._check_baseline

    def row_users(self, action):
        pointer = lambda value: value.as_pointer() if hasattr(value, 'as_pointer') else id(value)
        settings = [self.saved]
        for scene in self.bpy.data.scenes:
            candidate = getattr(scene, 'character_designer_animation_worklist', None)
            if candidate is not None and all(pointer(candidate) != pointer(entry) for entry in settings):
                settings.append(candidate)
        return sum(getattr(row, field) is action for state in settings
                   for row in [*state.items, *state.retired_items]
                   for field in ('source_action', 'custom_action'))

    def make_row(self, key='extra', *, retired=True):
        row = self.row(key)
        row.source_action = Action(self, 'Source ' + key)
        row.custom_action = Action(self, 'Custom ' + key, fake=True)
        cache = self.root / (row.item_id + '.blend')
        cache.write_bytes(b'physical read-only Source cache: ' + key.encode())
        row.source_file, row.source_hash = str(cache), self.sha(cache)
        row.source_action.library = SimpleNamespace(filepath=str(cache))
        self.actions.extend((row.source_action, row.custom_action))
        if retired:
            self.saved.items.remove(len(self.saved.items) - 1)
            self.saved.retired_items.append(row)
        return row

    @staticmethod
    def action_snapshot(action):
        return action.name_full, deepcopy(dict(action)), action.use_fake_user

    def discard(self, row):
        return self.core.discard_retired(self.context, row.item_id)

    def test_exact_row_only_is_removed_with_no_restore_or_action_rebinding(self):
        row = self.make_row()
        other = self.make_row('other')
        current = self.make_row('current', retired=False)
        self.rig.animation_data.action = current.custom_action
        self.worklist._restore_rig = Mock(side_effect=AssertionError('Discard restored old pose'))
        self.target.restore_main_rig = Mock(side_effect=AssertionError('Discard restored old rig'))
        result = self.discard(row)
        self.assertEqual(result['item_ids'], [row.item_id])
        self.assertEqual(self.saved.retired_items, [other])
        self.assertEqual(self.saved.items, [current])
        self.assertIs(self.rig.animation_data.action, current.custom_action)
        self.assertEqual([record[0] for record in self.actions.removals],
                         [row.custom_action, row.source_action])
        self.assertTrue(all(record[1:] == (False, 0, False) for record in self.actions.removals))
        self.assertFalse(self.saved.scan_completed)
        self.assertFalse(self.saved.has_error)
        with self.assertRaisesRegex(ValueError, 'no longer exists'):
            self.core.restore_retired(self.context, row.item_id)
        self.worklist._restore_rig.assert_not_called()
        self.target.restore_main_rig.assert_not_called()

    def test_custom_motion_proof_is_complete_before_any_source_deletion(self):
        row = self.make_row()
        with patch.object(self.core, 'fingerprint_action', wraps=self.core.fingerprint_action) as proof:
            def before_remove(action):
                if action is row.source_action:
                    self.assertEqual(proof.call_args_list,
                        [unittest.mock.call(row.source_action, 1), unittest.mock.call(row.custom_action, 1)])
            self.actions.before_remove = before_remove
            result = self.discard(row)
        self.assertEqual(len(result['removed_actions']), 2)
        self.assertEqual(result['kept_actions'], [])

    def test_distinct_python_wrappers_of_same_native_settings_do_not_self_protect(self):
        row = self.make_row()
        self.saved.as_pointer = lambda: 0xCAFE
        wrapper = SimpleNamespace(**vars(self.saved))
        self.assertIsNot(wrapper, self.saved)
        self.assertEqual(wrapper.as_pointer(), self.saved.as_pointer())
        self.scene.character_designer_animation_worklist = wrapper
        result = self.discard(row)
        self.assertEqual(result['kept_actions'], [])
        self.assertEqual(len(result['removed_actions']), 2)
        self.assertEqual(wrapper.retired_items, [])

    def test_other_current_retired_or_scene_membership_protects_exact_ids(self):
        for location in ('current', 'retired', 'other_scene'):
            for field in ('source_action', 'custom_action'):
                with self.subTest(location=location, field=field):
                    row = self.make_row(location + field)
                    action = getattr(row, field)
                    extra = SimpleNamespace(item_id='shared-' + row.item_id,
                        rig=self.rig, source_action=None, custom_action=action)
                    if location == 'current':
                        self.saved.items.append(extra)
                    elif location == 'retired':
                        self.saved.retired_items.append(extra)
                    else:
                        state = SimpleNamespace(items=fixtures.Rows([extra]), retired_items=fixtures.Rows())
                        self.bpy.data.scenes.append(SimpleNamespace(character_designer_animation_worklist=state))
                    before = self.action_snapshot(action)
                    result = self.discard(row)
                    self.assertIs(self.actions.get(action.name_full), action)
                    self.assertEqual(self.action_snapshot(action), before)
                    detail = next(item for item in result['kept_actions'] if item['name'] == action.name_full)
                    self.assertIn('Another active or retired', detail['reason'])

    def test_native_users_user_map_asset_override_and_history_are_preserved(self):
        cases = {
            'native_user': lambda action: setattr(action, 'native_users', 1),
            'user_map': lambda action: action.native_owners.add(object()),
            'asset': lambda action: setattr(action, 'asset_data', object()),
            'override': lambda action: setattr(action, 'override_library', object()),
            'historical_carrier': lambda action: action.__setitem__(self.target.ACTION_RIG_KEY,
                {'captured_name': action.name_full, 'rest': 'current-Rest receipt authenticates this'}),
        }
        for label, mutate in cases.items():
            for field in ('source_action', 'custom_action'):
                with self.subTest(case=label, field=field):
                    row = self.make_row(label + field)
                    action = getattr(row, field)
                    mutate(action)
                    before = self.action_snapshot(action)
                    result = self.discard(row)
                    self.assertIs(self.actions.get(action.name_full), action)
                    self.assertEqual(self.action_snapshot(action), before)
                    self.assertTrue(any(item['name'] == action.name_full and item['reason']
                                        for item in result['kept_actions']))
                    if label == 'historical_carrier':
                        self.assertEqual(action.users - int(action.use_fake_user), 0)

    def test_custom_properties_markers_and_both_saved_slot_guards_preserve_data(self):
        cases = {
            'unknown_property': lambda action: action.__setitem__('author_data', {'keep': [1, 2]}),
            'marker': lambda action: action.pose_markers.append(SimpleNamespace(name='Artist note')),
            'extra_slot': lambda action: action.slots.append(SimpleNamespace(handle=2, target_id_type='OBJECT')),
            'wrong_slot': lambda action: setattr(action.slots[0], 'handle', 9),
            'wrong_target': lambda action: setattr(action.slots[0], 'target_id_type', 'MESH'),
        }
        for label, mutate in cases.items():
            for field in ('source_action', 'custom_action'):
                if field == 'source_action' and label in ('unknown_property', 'marker'):
                    continue  # Verified immutable cache content may have metadata.
                with self.subTest(case=label, field=field):
                    row = self.make_row(label + field)
                    action = getattr(row, field)
                    mutate(action)
                    before = self.action_snapshot(action)
                    result = self.discard(row)
                    self.assertIs(self.actions.get(action.name_full), action)
                    self.assertEqual(self.action_snapshot(action), before)
                    self.assertTrue(any(item['name'] == action.name_full and item['reason']
                                        for item in result['kept_actions']))

    def test_source_must_be_linked_to_exact_physical_cache_before_either_id_is_deleted(self):
        for mode in ('local', 'missing_path', 'wrong_library', 'wrong_hash', 'missing_file'):
            with self.subTest(mode=mode):
                row = self.make_row(mode)
                source, custom = row.source_action, row.custom_action
                if mode == 'local':
                    source.library = None
                elif mode == 'missing_path':
                    row.source_file = ''
                elif mode == 'wrong_library':
                    source.library.filepath = str(self.root / 'foreign.blend')
                elif mode == 'wrong_hash':
                    row.source_hash = '0' * 64
                else:
                    Path(row.source_file).unlink()
                before = [self.action_snapshot(action) for action in (source, custom)]
                result = self.discard(row)
                self.assertEqual(result['removed_actions'], [])
                self.assertEqual(len(result['kept_actions']), 2)
                self.assertEqual([self.action_snapshot(action) for action in (source, custom)], before)
                self.assertIs(self.actions.get(source.name_full), source)
                self.assertIs(self.actions.get(custom.name_full), custom)

    def test_verified_readonly_source_metadata_can_be_unloaded_but_cache_file_is_kept(self):
        row = self.make_row()
        row.source_action['immutable_source_note'] = 'from hashed library'
        row.source_action.pose_markers.append(SimpleNamespace(name='Source marker'))
        cache_bytes = Path(row.source_file).read_bytes()
        result = self.discard(row)
        self.assertTrue(any(item['name'] == row.source_action.name_full for item in result['removed_actions']))
        self.assertIs(self.actions.get(row.custom_action.name_full), row.custom_action)
        self.assertEqual(Path(row.source_file).read_bytes(), cache_bytes)

    def test_changed_unknown_or_unprovable_custom_is_kept_without_renaming(self):
        for mode in ('changed', 'unknown_source', 'unknown_custom', 'empty_hash', 'linked', 'no_source', 'same_id'):
            with self.subTest(mode=mode):
                row = self.make_row(mode)
                custom = row.custom_action
                before = self.action_snapshot(custom)
                if mode == 'linked':
                    custom.library = object()
                elif mode == 'no_source':
                    row.source_action = None
                elif mode == 'same_id':
                    row.source_action = custom
                def proof(action, _slot):
                    return {'fingerprint_known': not (mode == 'unknown_source' and action is row.source_action
                        or mode == 'unknown_custom' and action is custom),
                        'action_fingerprint': '' if mode == 'empty_hash' else
                        ('changed' if mode == 'changed' and action is custom else 'original')}
                with patch.object(self.core, 'fingerprint_action', side_effect=proof):
                    result = self.discard(row)
                self.assertIs(self.actions.get(custom.name_full), custom)
                self.assertEqual(self.action_snapshot(custom), before)
                self.assertTrue(any(item['name'] == custom.name_full and item['reason']
                                    for item in result['kept_actions']))

    def test_cleanup_failure_restores_fake_user_but_commits_row_removal_with_reason(self):
        row = self.make_row()
        self.actions.failure = RuntimeError('native remove refused')
        before = [self.action_snapshot(action) for action in (row.source_action, row.custom_action)]
        result = self.discard(row)
        self.assertEqual(self.saved.retired_items, [])
        self.assertEqual(result['removed_actions'], [])
        self.assertEqual(len(result['kept_actions']), 2)
        self.assertTrue(all('native remove refused' in item['reason'] for item in result['kept_actions']))
        self.assertEqual([self.action_snapshot(action) for action in (row.source_action, row.custom_action)], before)
        self.assertTrue(all(record[1:] == (False, 0, False) for record in self.actions.removals))

    def test_cleanup_failure_restores_fake_user_through_different_wrapper_of_same_id(self):
        row = self.make_row()
        row.source_action.use_fake_user = True
        aliases = {}
        def replace_wrapper(action):
            index = next(i for i, value in enumerate(self.actions) if value is action)
            alias = ActionAlias(action)
            self.assertIsNot(alias, action)
            self.assertEqual(alias.as_pointer(), action.as_pointer())
            aliases[action.as_pointer()] = alias
            self.actions[index] = alias
        self.actions.before_remove = replace_wrapper
        self.actions.failure = RuntimeError('remove refused after wrapper refresh')
        result = self.discard(row)
        self.assertEqual(self.saved.retired_items, [])
        self.assertEqual(result['removed_actions'], [])
        self.assertEqual(len(result['kept_actions']), 2)
        for action in (row.source_action, row.custom_action):
            alias = aliases[action.as_pointer()]
            self.assertIs(self.actions.get(action.name_full), alias)
            self.assertTrue(alias.use_fake_user)
            self.assertTrue(action.use_fake_user)
        self.assertTrue(all('wrapper refresh' in detail['reason'] for detail in result['kept_actions']))
        self.assertTrue(all(record[1:] == (False, 0, False) for record in self.actions.removals))

    def test_cleanup_failure_ignores_same_name_different_library_and_pointer(self):
        for field in ('source_action', 'custom_action'):
            with self.subTest(field=field):
                row = self.make_row(field)
                action = getattr(row, field)
                action.use_fake_user = True
                foreign = Action(self, action.name_full, fake=False)
                foreign.library = None if action.library is not None else SimpleNamespace(filepath='foreign.blend')
                foreign['foreign_author_data'] = {'keep': True}
                self.actions.insert(0, foreign)
                self.assertIs(self.actions.get(action.name_full), foreign)
                self.assertNotEqual(foreign.as_pointer(), action.as_pointer())
                before = self.action_snapshot(foreign)
                self.actions.failure = RuntimeError('native removal failed')
                result = self.discard(row)
                self.assertTrue(action.use_fake_user)
                self.assertEqual(self.action_snapshot(foreign), before)
                self.assertTrue(any(detail['name'] == action.name_full and 'native removal failed' in detail['reason']
                                    for detail in result['kept_actions']))
                self.assertFalse(any(record[0] is foreign for record in self.actions.removals))
                self.assertTrue(all(record[1:] == (False, 0, False) for record in self.actions.removals))

    def test_cleanup_exception_after_actual_deletion_reports_removed_with_warning(self):
        row = self.make_row()
        foreign = Action(self, row.custom_action.name_full, fake=True)
        foreign.library = SimpleNamespace(filepath='unrelated.blend')
        foreign['foreign_author_data'] = 'must remain intact'
        self.actions.insert(0, foreign)
        before = self.action_snapshot(foreign)
        self.actions.before_remove = lambda action: list.remove(self.actions, action)
        self.actions.failure = RuntimeError('native error after removal')
        result = self.discard(row)
        self.assertEqual(self.saved.retired_items, [])
        self.assertEqual(result['kept_actions'], [])
        self.assertEqual(len(result['removed_actions']), 2)
        self.assertTrue(all('native error after removal' in detail.get('warning', '')
                            for detail in result['removed_actions']))
        remaining_pointers = {action.as_pointer() for action in self.actions}
        self.assertNotIn(row.source_action.as_pointer(), remaining_pointers)
        self.assertNotIn(row.custom_action.as_pointer(), remaining_pointers)
        self.assertIn(foreign.as_pointer(), remaining_pointers)
        self.assertEqual(self.action_snapshot(foreign), before)
        self.assertIn('removed 2 unused Action(s)', self.saved.status)
        self.assertTrue(all(record[1:] == (False, 0, False) for record in self.actions.removals))

    def test_foreign_retired_rig_is_rejected_before_membership_or_id_changes(self):
        row = self.make_row()
        foreign = fixtures.ID(name='Foreign Rig', type='ARMATURE')
        self.assertNotEqual(foreign.as_pointer(), self.rig.as_pointer())
        row.rig = foreign
        before_row = dict(vars(row))
        before_actions = [self.action_snapshot(action) for action in self.actions]
        before_feedback = self.saved.status, self.saved.has_error, self.saved.scan_completed
        with self.assertRaisesRegex(ValueError, 'another Main Rig'):
            self.discard(row)
        self.assertEqual(self.saved.retired_items, [row])
        self.assertEqual(dict(vars(row)), before_row)
        self.assertEqual([self.action_snapshot(action) for action in self.actions], before_actions)
        self.assertEqual((self.saved.status, self.saved.has_error, self.saved.scan_completed), before_feedback)
        self.assertEqual(self.actions.removals, [])

    def test_retired_rig_same_native_pointer_different_wrapper_is_allowed(self):
        row = self.make_row()
        owner = SimpleNamespace(as_pointer=self.rig.as_pointer)
        self.assertIsNot(owner, self.rig)
        self.assertEqual(owner.as_pointer(), self.rig.as_pointer())
        row.rig = owner
        result = self.discard(row)
        self.assertEqual(self.saved.retired_items, [])
        self.assertEqual(result['kept_actions'], [])
        self.assertEqual(len(result['removed_actions']), 2)
        self.assertIs(self.saved.rig, self.rig)
        self.assertIs(self.scene.character_designer_setup.rig, self.rig)
        self.assertTrue(all(record[1:] == (False, 0, False) for record in self.actions.removals))

    def test_bad_id_and_busy_admission_do_not_touch_membership_or_ids(self):
        row = self.make_row()
        before = [dict(vars(entry)) for entry in self.saved.retired_items]
        for item_id in ('missing', None, 42):
            with self.subTest(item_id=item_id), self.assertRaises(ValueError):
                self.core.discard_retired(self.context, item_id)
        self.worklist._idle.side_effect = ValueError('Finish current operation first')
        with self.assertRaisesRegex(ValueError, 'Finish current'):
            self.discard(row)
        self.assertEqual([dict(vars(entry)) for entry in self.saved.retired_items], before)
        self.assertEqual(self.actions.removals, [])

    def test_sync_discard_is_explicit_retire_remains_recoverable_and_options_exclusive(self):
        row = self.make_row(retired=False)
        preview = self.core.plan(self.context)
        with self.assertRaisesRegex(ValueError, 'either Retire or Discard'):
            self.core.begin(self.context, preview['token'], deprecate_extras=True, discard_extras=True)
        self.assertEqual(self.saved.items, [row])
        self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.assertEqual(len(self.saved.retired_items), 1)
        self.assertEqual(self.actions.removals, [])
        restored = self.core.restore_retired(self.context, row.item_id)
        self.assertIs(restored.custom_action, row.custom_action)
        preview = self.core.plan(self.context)
        self.core.begin(self.context, preview['token'], discard_extras=True)
        self.assertEqual(self.saved.retired_items, [])
        self.assertEqual(len(self.actions.removals), 2)

    def test_sync_pretransaction_and_finalize_failures_cannot_delete_native_ids(self):
        for stage in ('stale_token', 'queue', 'finish'):
            with self.subTest(stage=stage):
                row = self.make_row(stage, retired=False)
                before = self.core._snapshot(self.context, self.saved, self.rig)
                preview = self.core.plan(self.context)
                token = preview['token']
                if stage == 'stale_token':
                    token = 'old-token'
                elif stage == 'queue':
                    self.collection.begin_add_ready.side_effect = RuntimeError('queue rejected')
                else:
                    self.worklist._finish_rig.side_effect = RuntimeError('finish rejected')
                with self.assertRaises((ValueError, RuntimeError)):
                    self.core.begin(self.context, token, discard_extras=True)
                self.assertEqual(self.core._snapshot(self.context, self.saved, self.rig), before)
                self.assertEqual(self.actions.removals, [])
                self.assertIs(self.actions.get(row.custom_action.name_full), row.custom_action)
                self.collection.begin_add_ready.side_effect = None
                self.worklist._finish_rig.side_effect = None


if __name__ == '__main__':
    unittest.main()
