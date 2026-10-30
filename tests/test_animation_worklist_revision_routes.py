"""Narrow current-rig routing/glue contracts; no Blender or revision internals."""

from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

import test_animation_worklist as worklist_fixtures
import test_animation_worklist_collection as collection_fixtures
import test_animation_worklist_reconcile as reconcile_fixtures
import test_animation_worklist_ui as ui_fixtures


class RevisionRoutes(unittest.TestCase):
    def fixture(self, case):
        # Reuse setup only; never inherit or load the older test suites.
        value = case('runTest')
        self.addCleanup(value.doCleanups)
        value.setUp()
        return value

    def module(self, owner, short_name, **fields):
        name = owner.__package__ + '.' + short_name
        value = ModuleType(name)
        value.__dict__.update(fields)
        package = sys.modules[owner.__package__]
        self.enterContext(patch.dict(sys.modules, {name: value}))
        self.enterContext(patch.object(package, short_name, value, create=True))
        return value

    def worklist(self):
        f = self.fixture(worklist_fixtures.WorklistTests)
        rig, item, _other = f.populated()
        target = self.module(f.worklist, 'animation_worklist_target', ensure_active=Mock())
        composition = self.module(f.worklist, 'animation_composition',
                                  active=Mock(return_value=False), item_spec=Mock(return_value=None))
        revision = self.module(f.worklist, 'animation_worklist_revision',
                               activate=Mock(return_value=object()), create=Mock())
        return f, rig, item, target, composition, revision

    def test_only_marked_row_dispatches_actual_activation_with_exact_side(self):
        f, rig, item, target, _composition, revision = self.worklist()
        item.current_rig_revision = 'owned-current-revision'
        original = rig.animation_data.action
        with patch.object(f.worklist, '_check_baseline') as baseline:
            for side in ('CUSTOM', 'SOURCE'):
                with self.subTest(side=side):
                    result = f.worklist._activate_worklist_action(f.context, item.item_id, side)
                    self.assertIs(result, revision.activate.return_value)
                    revision.activate.assert_called_with(f.context, item.item_id, side)
            baseline.assert_not_called()
        self.assertEqual(revision.activate.call_count, 2)
        target.ensure_active.assert_not_called()
        self.assertIs(rig.animation_data.action, original)

    def test_empty_or_missing_marker_keeps_the_ordinary_strict_gate(self):
        f, rig, item, target, _composition, revision = self.worklist()
        target.ensure_active.side_effect = ValueError('ordinary saved Rest remains strict')
        original = rig.animation_data.action
        with patch.object(f.worklist, '_rigging_module', return_value=None):
            for marker in (None, ''):
                with self.subTest(marker=marker):
                    if marker is None:
                        vars(item).pop('current_rig_revision', None)
                    else:
                        item.current_rig_revision = marker
                    with self.assertRaisesRegex(ValueError, 'ordinary saved Rest remains strict'):
                        f.worklist._activate_worklist_action(f.context, item.item_id)
        self.assertEqual(target.ensure_active.call_count, 2)
        revision.activate.assert_not_called()
        self.assertIs(rig.animation_data.action, original)

    def test_local_revision_sync_refuses_before_export_or_old_target_updates(self):
        f, rig, item, target, composition, revision = self.worklist()
        item.current_rig_revision = 'local'
        original = rig.animation_data.action
        with patch.object(f.worklist, '_check_baseline') as baseline, \
                patch.object(f.worklist, '_rigging_entry_packet') as packet, \
                patch.object(f.links, 'begin_linked_export') as export:
            with self.assertRaisesRegex(ValueError, 'current rig.*matching Unity'):
                f.worklist.sync(f.context)
            for gate in (baseline, packet, export, target.ensure_active,
                         composition.item_spec, revision.activate, revision.create):
                gate.assert_not_called()
        self.assertIs(rig.animation_data.action, original)

    def test_local_revision_fingerprint_refuses_before_native_or_export_collection(self):
        f = self.fixture(collection_fixtures.CollectionTests)
        f.item.current_rig_revision = 'local'
        with patch.object(f.collection, '_composition_row') as composition:
            with self.assertRaisesRegex(ValueError, 'current rig.*matching Unity'):
                f.collection.item_fingerprint(f.context, f.item)
            composition.assert_not_called()
        f.fingerprints.fingerprint_native.assert_not_called()
        f.worklist.sync.assert_not_called()
        f.worklist.add.assert_not_called()
        self.assertIsNone(f.collection._batch)

    def test_local_revision_sync_list_refuses_before_files_import_or_session_update(self):
        f = self.fixture(reconcile_fixtures.ReconcileTests)
        item = f.row()
        item.current_rig_revision = 'local'
        with patch.object(f.workspace, 'load_workspace') as load, \
                patch.object(f.core, '_main_rig') as main, \
                patch.object(f.core, '_prepared') as prepared, \
                patch.object(f.target, '_store') as store:
            with self.assertRaisesRegex(ValueError, 'current rig.*matching Unity'):
                f.core._plan(f.context)
            for gate in (load, main, prepared, store, f.target.ensure_active,
                         f.collection.begin_add_ready, f.worklist.adopt_main_rig):
                gate.assert_not_called()
        self.assertIs(f.saved.items[0], item)
        self.assertEqual(f.saved.retired_items, [])

    def current_glue(self):
        f, rig, item, _target, _composition, revision = self.worklist()
        rig.as_pointer = lambda: id(rig)
        slot = object()
        rig.animation_data.action_slot = slot
        scratch = rig.animation_data.action
        key = 'test-owned-base-pose-token'
        rig[key] = 'original-token'
        archive = SimpleNamespace(users=1, use_fake_user=True, as_pointer=lambda: 131)
        actions = SimpleNamespace(remove=Mock())
        self.enterContext(patch.object(f.worklist.bpy, 'data',
                                      SimpleNamespace(actions=actions), create=True))
        pose = self.module(f.worklist, 'animation_pose_test', TOKEN_KEY=key,
                           _rig=Mock(return_value=rig), active=lambda value: key in value,
                           _restore=Mock(side_effect=AssertionError('old recovery must not run')))
        events = []

        def preserve(context):
            self.assertIs(context, f.context)
            events.append('archive')
            rig.animation_data.action = rig.animation_data.action_slot = None
            rig.pop(key)
            return {'archive': archive}

        pose.keep_stale_draft = Mock(side_effect=preserve)
        return f, rig, item, revision, pose, scratch, slot, archive, actions, events

    def test_current_glue_archives_then_creates_with_explicit_activation(self):
        f, rig, item, revision, pose, _scratch, _slot, _archive, actions, events = self.current_glue()
        created = object()

        def create(*args, **kwargs):
            events.append('create')
            self.assertIsNone(rig.animation_data.action)
            self.assertFalse(pose.active(rig))
            return created

        revision.create.side_effect = create
        self.assertIs(f.worklist.use_current_rig_version(f.context), created)
        revision.create.assert_called_once_with(f.context, item.item_id, activate_new=True)
        self.assertEqual(events, ['archive', 'create'])
        pose._restore.assert_not_called()
        actions.remove.assert_not_called()

    def test_current_glue_failed_create_restores_only_original_binding_and_token(self):
        f, rig, _item, revision, pose, scratch, slot, archive, actions, _events = self.current_glue()
        before_content = dict(scratch)
        revision.create.side_effect = ValueError('fresh creation rejected')
        with self.assertRaisesRegex(ValueError, 'fresh creation rejected'):
            f.worklist.use_current_rig_version(f.context)
        self.assertIs(rig.animation_data.action, scratch)
        self.assertIs(rig.animation_data.action_slot, slot)
        self.assertEqual(rig[pose.TOKEN_KEY], 'original-token')
        self.assertEqual(dict(scratch), before_content)
        self.assertFalse(archive.use_fake_user)
        actions.remove.assert_called_once_with(archive, do_unlink=False)
        pose._restore.assert_not_called()

    def assert_foreign_checkpoint_preserved(self, *, token):
        f, rig, _item, revision, pose, scratch, _slot, archive, actions, _events = self.current_glue()
        foreign = object()

        def fail(*_args, **_kwargs):
            if token:
                rig[pose.TOKEN_KEY] = 'foreign-token'
            else:
                rig.animation_data.action = foreign
            raise ValueError('fresh creation rejected')

        revision.create.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, 'archive was kept'):
            f.worklist.use_current_rig_version(f.context)
        self.assertIsNot(rig.animation_data.action, scratch)
        if token:
            self.assertEqual(rig[pose.TOKEN_KEY], 'foreign-token')
        else:
            self.assertIs(rig.animation_data.action, foreign)
            self.assertNotIn(pose.TOKEN_KEY, rig)
        self.assertTrue(archive.use_fake_user)
        actions.remove.assert_not_called()
        pose._restore.assert_not_called()

    def test_current_glue_foreign_binding_keeps_archive_without_forcing_old_state(self):
        self.assert_foreign_checkpoint_preserved(token=False)

    def test_current_glue_foreign_token_keeps_archive_without_forcing_old_state(self):
        self.assert_foreign_checkpoint_preserved(token=True)

    def test_use_current_ui_poll_needs_no_selected_bone_and_rejects_mesh_edit(self):
        f = self.fixture(ui_fixtures.WorklistUI)
        f.context.mode, f.context.selected_pose_bones = 'POSE', []
        operator = f.ui.CHARACTERDESIGNER_OT_worklist_use_current_revision
        self.assertTrue(operator.poll(f.context))
        f.context.mode = 'EDIT_MESH'
        self.assertFalse(operator.poll(f.context))

    def test_use_current_ui_real_run_delegation_and_error_preserve_data_and_mode(self):
        f = self.fixture(ui_fixtures.WorklistUI)
        f.context.mode = 'POSE'
        before = [(row.item_id, row.source_action, row.custom_action) for row in f.state.items]
        original = f.state.items[0].rig.animation_data.action
        f.backend.use_current_rig_version = Mock()
        operator = f.ui.CHARACTERDESIGNER_OT_worklist_use_current_revision()
        self.assertEqual(operator.execute(f.context), {'FINISHED'})
        f.backend.use_current_rig_version.assert_called_once_with(f.context)
        f.backend.use_current_rig_version.side_effect = ValueError('owned current rig refused')
        self.assertEqual(operator.execute(f.context), {'CANCELLED'})
        self.assertEqual(operator.last_report, ({'ERROR'}, 'owned current rig refused'))
        self.assertEqual(f.context.mode, 'POSE')
        self.assertEqual([(row.item_id, row.source_action, row.custom_action) for row in f.state.items], before)
        self.assertIs(f.state.items[0].rig.animation_data.action, original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
