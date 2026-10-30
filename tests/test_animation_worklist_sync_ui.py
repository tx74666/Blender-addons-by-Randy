"""Sync List UI contracts using the real operators, without Blender or workers."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_animation_worklist_ui as fixtures


class PopupLayout(fixtures.Layout):
    def separator(self):
        self.records.append(('separator', None, self.enabled))


class WorklistSyncUI(unittest.TestCase):
    def setUp(self):
        fixtures.WorklistUI.setUp(self)
        self.state.rig = None
        self.state.retired_items = []
        self.reconcile = SimpleNamespace(plan=Mock(return_value=self.plan()), begin=Mock(), restore_retired=Mock())
        backend = patch.object(self.ui, '_reconcile', return_value=self.reconcile)
        backend.start()
        self.addCleanup(backend.stop)
        self.review = Mock(return_value={'RUNNING_MODAL'})
        sys.modules['bpy'].ops = SimpleNamespace(
            character_designer=SimpleNamespace(worklist_sync_review=self.review))
        self.context.window_manager.invoke_popup = Mock(return_value={'RUNNING_MODAL'})

    @staticmethod
    def plan(*, extras=None, blocked=None):
        return dict(token='checked-plan-token', workspace_path='checked-workspace.json',
                    extras=[] if extras is None else extras, blocked=[] if blocked is None else blocked)

    def snapshot(self):
        records = lambda items: tuple((id(item), tuple(vars(item).items())) for item in items)
        return (records(self.state.items), records(self.state.retired_items), self.state.active_index,
                self.state.workspace_path, self.state.workspace_identity,
                tuple((item, item.source_action, item.custom_action, item.rig.animation_data.action)
                      for item in self.state.items))

    def queue(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_list()
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        return operator

    def confirm(self, *, token='checked-plan-token'):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_confirm()
        operator.plan_token, operator.workspace_path = token, 'checked-workspace.json'
        return operator

    def open_review(self, *, token='checked-plan-token', workspace_path='checked-workspace.json'):
        plan = self.plan(extras=[dict(item_id='draft-id', name='Draft', reason='Outside Unity.')])
        plan.update(token=token, workspace_path=workspace_path)
        self.reconcile.plan.return_value = plan
        self.queue()
        self.ui._poll_list_sync()
        self.assertIsNotNone(self.ui._LIST_SYNC_REVIEW)
        self.reconcile.plan.reset_mock()
        self.reconcile.begin.reset_mock()
        self.review.reset_mock()
        self.timers.register.reset_mock()

    def test_sync_paints_feedback_before_timer_and_performs_no_preflight_on_click(self):
        area = SimpleNamespace(type='VIEW_3D', tag_redraw=Mock())
        self.context.screen.areas = [area]
        before = self.snapshot()

        def register(callback, **options):
            self.assertEqual(callback, self.ui._poll_list_sync)
            self.assertEqual(options, {'first_interval': .05})
            self.assertIn('Checking Unity list', self.state.status)
            self.assertEqual(self.state.collection_status, self.state.status)
            self.assertFalse(self.state.has_error)
            area.tag_redraw.assert_called_once_with()
            self.reconcile.plan.assert_not_called()
            self.reconcile.begin.assert_not_called()

        self.timers.register.side_effect = register
        self.queue()
        self.assertTrue(self.ui.scan_pending())
        self.assertEqual(self.snapshot(), before)
        self.reconcile.plan.assert_not_called()
        self.reconcile.begin.assert_not_called()

    def test_no_extra_entries_begin_only_from_timer_with_checked_token_and_path(self):
        self.queue()
        self.reconcile.begin.assert_not_called()
        self.assertIsNone(self.ui._poll_list_sync())
        self.assertFalse(self.ui.scan_pending())
        self.reconcile.plan.assert_called_once_with(self.context, None)
        self.reconcile.begin.assert_called_once_with(
            self.context, 'checked-plan-token', workspace_path='checked-workspace.json')
        self.review.assert_not_called()
        self.backend.connect.assert_not_called()
        self.collection.begin_add_ready.assert_not_called()

    def test_callback_releases_pending_lock_before_plan_and_begin(self):
        def plan(context, path):
            self.assertIs(context, self.context)
            self.assertIsNone(path)
            self.assertFalse(self.ui.scan_pending())
            self.assertTrue(self.ui._idle(context))
            return self.plan()

        self.reconcile.plan.side_effect = plan
        self.queue()
        self.ui._poll_list_sync()
        self.reconcile.begin.assert_called_once()

    def test_pending_list_sync_blocks_duplicate_click_and_other_worklist_entry_points(self):
        operator = self.queue()
        message = self.state.status
        for cls in (self.ui.CHARACTERDESIGNER_OT_worklist_sync_list,
                    self.ui.CHARACTERDESIGNER_OT_worklist_connect,
                    self.ui.CHARACTERDESIGNER_OT_worklist_refresh,
                    self.ui.CHARACTERDESIGNER_OT_worklist_add_ready,
                    self.ui.CHARACTERDESIGNER_OT_worklist_scan_changes,
                    self.ui.CHARACTERDESIGNER_OT_worklist_sync_changed,
                    self.ui.CHARACTERDESIGNER_OT_worklist_activate):
            with self.subTest(operator=cls.bl_idname):
                self.assertFalse(cls.poll(self.context))
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(self.state.status, message)
        self.timers.register.assert_called_once()
        self.reconcile.plan.assert_not_called()

    def test_pending_export_scan_blocks_list_sync_without_replacing_its_timer(self):
        self.assertEqual(self.ui.CHARACTERDESIGNER_OT_worklist_scan_changes().execute(self.context), {'FINISHED'})
        status = self.state.status
        self.assertEqual(self.ui.CHARACTERDESIGNER_OT_worklist_sync_list().execute(self.context), {'CANCELLED'})
        self.assertEqual(self.state.status, status)
        self.timers.register.assert_called_once_with(self.ui._poll_scan, first_interval=.05)
        self.reconcile.plan.assert_not_called()

    def test_cancel_pending_removes_only_list_timer_without_cancelling_another_export(self):
        before = self.snapshot()
        self.queue()
        self.collection.running.return_value = True
        cancel = self.ui.CHARACTERDESIGNER_OT_worklist_cancel_collection()
        self.assertTrue(cancel.poll(self.context))
        self.assertEqual(cancel.execute(self.context), {'FINISHED'})
        self.timers.unregister.assert_called_once_with(self.ui._poll_list_sync)
        self.collection.cancel.assert_not_called()
        self.assertFalse(self.ui.scan_pending())
        self.assertIn('cancelled before it started', self.state.status)
        self.assertIsNone(self.ui._poll_list_sync())
        self.reconcile.plan.assert_not_called()
        self.reconcile.begin.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_changed_scene_cancels_before_plan_and_does_not_touch_new_scene(self):
        self.queue()
        other = SimpleNamespace(status='Other scene unchanged', has_error=False)
        self.window.scene = SimpleNamespace(character_designer_animation_worklist=other)
        self.ui._poll_list_sync()
        self.assertFalse(self.ui.scan_pending())
        self.assertIn('owning scene changed', self.state.status)
        self.assertEqual(other.status, 'Other scene unchanged')
        self.reconcile.plan.assert_not_called()
        self.reconcile.begin.assert_not_called()

    def test_removed_window_or_scene_cancels_before_plan(self):
        for removed in ('window', 'scene'):
            with self.subTest(removed=removed):
                self.context.window_manager.windows = [self.window]
                sys.modules['bpy'].data.scenes = [self.context.scene]
                self.queue()
                if removed == 'window':
                    self.context.window_manager.windows = []
                else:
                    sys.modules['bpy'].data.scenes = []
                self.ui._poll_list_sync()
                self.assertFalse(self.ui.scan_pending())
                self.reconcile.plan.assert_not_called()
                self.reconcile.begin.assert_not_called()

    def test_late_animation_job_blocks_preflight_without_cancelling_that_job(self):
        self.queue()
        self.animation._link_idle.return_value = False
        self.ui._poll_list_sync()
        self.assertFalse(self.ui.scan_pending())
        self.assertTrue(self.state.has_error)
        self.assertIn('current animation operation', self.state.status)
        self.reconcile.plan.assert_not_called()
        self.collection.cancel.assert_not_called()

    def test_lost_nonpersistent_timer_unlocks_without_writing_new_scene(self):
        self.queue()
        previous = self.state.status
        self.timers.is_registered.return_value = False
        self.assertFalse(self.ui.scan_pending())
        self.assertTrue(self.ui._idle(self.context))
        self.assertEqual(self.state.status, previous)
        self.timers.unregister.assert_not_called()
        self.reconcile.plan.assert_not_called()

    def test_reload_stop_clears_pending_list_sync_without_starting_it(self):
        self.queue()
        self.ui.stop_worklist_ui()
        self.assertFalse(self.ui.scan_pending())
        self.timers.unregister.assert_called_once_with(self.ui._poll_list_sync)
        self.collection.stop.assert_called_once_with()
        self.assertIsNone(self.ui._poll_list_sync())
        self.reconcile.plan.assert_not_called()

    def test_timer_registration_failure_reports_error_and_preserves_items(self):
        before = self.snapshot()
        self.timers.register.side_effect = RuntimeError('Timer registration failed')
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_list()
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertFalse(self.ui.scan_pending())
        self.assertTrue(self.state.has_error)
        self.assertEqual(operator.last_report, ({'ERROR'}, 'Timer registration failed'))
        self.timers.unregister.assert_not_called()
        self.reconcile.plan.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_missing_owning_window_reports_error_before_registering_timer(self):
        before = self.snapshot()
        self.context.window = None
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_list()
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertFalse(self.ui.scan_pending())
        self.assertTrue(self.state.has_error)
        self.assertIn('owning Blender window', operator.last_report[1])
        self.timers.register.assert_not_called()
        self.reconcile.plan.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_plan_failure_stops_sync_and_keeps_all_existing_records(self):
        before = self.snapshot()
        self.reconcile.plan.side_effect = ValueError('Workspace revision is no longer current.')
        self.queue()
        self.ui._poll_list_sync()
        self.assertTrue(self.state.has_error)
        self.assertEqual(self.state.status, 'Workspace revision is no longer current.')
        self.assertEqual(self.state.collection_status, self.state.status)
        self.assertFalse(self.ui.scan_pending())
        self.assertEqual(self.snapshot(), before)
        self.review.assert_not_called()
        self.reconcile.begin.assert_not_called()

    def test_blocked_plan_reports_names_and_reasons_without_review_or_begin(self):
        before = self.snapshot()
        self.reconcile.plan.return_value = self.plan(
            blocked=[dict(name='Walk', reason='Source proof is unavailable.')],
            extras=[dict(name='Draft', reason='Outside the Unity list.')])
        self.queue()
        self.ui._poll_list_sync()
        self.assertTrue(self.state.has_error)
        self.assertIn('Walk: Source proof is unavailable.', self.state.status)
        self.review.assert_not_called()
        self.reconcile.begin.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_begin_failure_releases_pending_state_and_keeps_existing_work(self):
        before = self.snapshot()
        self.reconcile.begin.side_effect = RuntimeError('A source changed after preflight; retry Sync List.')
        self.queue()
        self.ui._poll_list_sync()
        self.assertFalse(self.ui.scan_pending())
        self.assertTrue(self.state.has_error)
        self.assertIn('source changed after preflight', self.state.status)
        self.assertEqual(self.state.status, self.state.collection_status)
        self.assertEqual(self.snapshot(), before)
        self.review.assert_not_called()

    def test_extra_entries_open_review_with_original_plan_without_beginning_sync(self):
        before = self.snapshot()
        extras = [dict(item_id='draft-id', name='Custom 測試', reason='No longer in Unity.')]
        self.reconcile.plan.return_value = self.plan(extras=extras)
        self.queue()
        self.ui._poll_list_sync()
        self.assertFalse(self.ui.scan_pending())
        self.reconcile.begin.assert_not_called()
        self.review.assert_called_once()
        args, options = self.review.call_args
        self.assertEqual(args, ('INVOKE_DEFAULT',))
        self.assertEqual(options['plan_token'], 'checked-plan-token')
        self.assertEqual(options['workspace_path'], 'checked-workspace.json')
        self.assertEqual(json.loads(options['extras_json']), extras)
        self.assertIn('Custom 測試', options['extras_json'])
        self.assertEqual(self.snapshot(), before)

    def test_refresh_and_connected_workspace_choice_use_deferred_reconciliation(self):
        for kind, path in (('refresh', None), ('connect', 'selected-workspace.json')):
            with self.subTest(kind=kind):
                self.reconcile.plan.reset_mock()
                self.reconcile.begin.reset_mock()
                operator = getattr(self.ui, 'CHARACTERDESIGNER_OT_worklist_' + kind)()
                if kind == 'connect':
                    operator.filepath = path
                self.assertEqual(operator.execute(self.context), {'FINISHED'})
                self.reconcile.plan.assert_not_called()
                self.ui._poll_list_sync()
                self.reconcile.plan.assert_called_once_with(self.context, path)
                self.reconcile.begin.assert_called_once_with(
                    self.context, 'checked-plan-token', workspace_path='checked-workspace.json')
        self.backend.connect.assert_not_called()
        self.backend.refresh.assert_not_called()

    def test_first_connection_remains_direct_without_any_existing_worklist_or_rig(self):
        self.state.items = []
        self.state.connection_check_failed = True
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_connect()
        operator.filepath = 'first-workspace.json'
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.backend.connect.assert_called_once_with(self.context, 'first-workspace.json')
        self.timers.register.assert_not_called()
        self.reconcile.plan.assert_not_called()
        self.assertFalse(self.state.connection_check_failed)

    def test_existing_rig_alone_routes_connection_through_preserving_sync(self):
        self.state.items = []
        self.state.rig = object()
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_connect()
        operator.filepath = 'next-workspace.json'
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.backend.connect.assert_not_called()
        self.ui._poll_list_sync()
        self.reconcile.plan.assert_called_once_with(self.context, 'next-workspace.json')

    def test_review_popup_has_explicit_retire_and_back_controls_with_pinned_token(self):
        before = self.snapshot()
        self.open_review(token='original-token', workspace_path='original-workspace.json')
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_review()
        operator.plan_token, operator.workspace_path = 'original-token', 'original-workspace.json'
        operator.extras_json = json.dumps([dict(name='Draft Walk', reason='Not present in Unity.')])
        operator.layout = PopupLayout()
        self.assertEqual(operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.context.window_manager.invoke_popup.assert_called_once_with(operator, width=480)
        with patch.object(Path, 'read_text', side_effect=AssertionError('Popup read disk')), \
                patch.object(self.ui, '_reconcile', side_effect=AssertionError('Popup reconciled')):
            operator.draw(self.context)
        buttons = [value for kind, value, _enabled in operator.layout.records if kind == 'operator']
        self.assertEqual([(value[0], value[1]['text']) for value in buttons], [
            ('character_designer.worklist_sync_confirm', 'Retire and Sync'),
            ('character_designer.worklist_sync_discard_confirm', 'Discard and Sync'),
            ('character_designer.worklist_sync_back', 'Back')])
        self.assertEqual((buttons[0][2].plan_token, buttons[0][2].workspace_path),
                         ('original-token', 'original-workspace.json'))
        self.assertEqual((buttons[1][2].plan_token, buttons[1][2].workspace_path),
                         ('original-token', 'original-workspace.json'))
        labels = [value['text'] for kind, value, _enabled in operator.layout.records if kind == 'label']
        self.assertIn('Draft Walk', labels)
        self.assertIn('Not present in Unity.', labels)
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(self.snapshot(), before)
        self.reconcile.begin.assert_not_called()

    def test_review_rejects_missing_token_or_busy_editor(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_review()
        operator.workspace_path = 'checked-workspace.json'
        operator.plan_token = ''
        self.assertEqual(operator.invoke(self.context, None), {'CANCELLED'})
        operator.plan_token = 'old-plan'
        self.animation._link_idle.return_value = False
        self.assertEqual(operator.invoke(self.context, None), {'CANCELLED'})
        self.context.window_manager.invoke_popup.assert_not_called()
        self.reconcile.begin.assert_not_called()

    def test_back_changes_feedback_only_and_never_changes_records_or_actions(self):
        self.state.retired_items = [SimpleNamespace(item_id='retired-id', name='Retired Walk')]
        before = self.snapshot()
        self.open_review()
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_back()
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.state.has_error)
        self.assertIn('Existing animations were kept', self.state.status)
        self.assertEqual(self.state.status, self.state.collection_status)
        self.reconcile.plan.assert_not_called()
        self.reconcile.begin.assert_not_called()
        self.backend.remove.assert_not_called()
        self.assertIsNone(self.ui._LIST_SYNC_REVIEW)

    def test_confirm_paints_then_defers_original_token_and_workspace_without_preflight(self):
        before = self.snapshot()
        self.open_review(token='token-from-review')
        area = SimpleNamespace(type='VIEW_3D', tag_redraw=Mock())
        self.context.screen.areas = [area]
        actions = SimpleNamespace(new=Mock(side_effect=AssertionError('Click created an Action')))
        sys.modules['bpy'].data.actions = actions

        def register(callback, **options):
            self.assertEqual(callback, self.ui._poll_list_sync)
            self.assertEqual(options, {'first_interval': .05})
            self.assertIn('Retiring and syncing', self.state.status)
            self.assertIn('running checks must finish', self.state.status)
            self.assertEqual(self.state.collection_status, self.state.status)
            self.assertFalse(self.state.has_error)
            self.assertTrue(self.ui.scan_pending())
            self.assertIsNone(self.ui._LIST_SYNC_REVIEW)
            area.tag_redraw.assert_called_once_with()
            self.reconcile.plan.assert_not_called()
            self.reconcile.begin.assert_not_called()
            self.assertEqual(self.snapshot(), before)
            actions.new.assert_not_called()

        self.timers.register.side_effect = register
        operator = self.confirm(token='token-from-review')
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.reconcile.begin.assert_not_called()
        self.reconcile.plan.assert_not_called()
        actions.new.assert_not_called()
        self.assertEqual(self.snapshot(), before)

        def begin(context, token, **options):
            self.assertIs(context, self.context)
            self.assertEqual(token, 'token-from-review')
            self.assertEqual(options, dict(workspace_path='checked-workspace.json', deprecate_extras=True))
            self.assertFalse(self.ui.scan_pending())
            self.assertTrue(self.ui._idle(context))

        self.reconcile.begin.side_effect = begin
        self.assertIsNone(self.ui._poll_list_sync())
        self.reconcile.begin.assert_called_once_with(
            self.context, 'token-from-review', workspace_path='checked-workspace.json', deprecate_extras=True)
        self.reconcile.plan.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_confirm_callback_rechecks_stale_backend_token_and_preserves_error_from_back(self):
        before = self.snapshot()
        self.open_review(token='stale-after-review')
        self.reconcile.begin.side_effect = ValueError('The sync plan changed; review the list again.')
        operator = self.confirm(token='stale-after-review')
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.reconcile.begin.assert_not_called()
        self.ui._poll_list_sync()
        self.reconcile.begin.assert_called_once_with(
            self.context, 'stale-after-review', workspace_path='checked-workspace.json', deprecate_extras=True)
        self.assertTrue(self.state.has_error)
        self.assertEqual(self.state.status, 'The sync plan changed; review the list again.')
        status = self.state.status
        self.assertEqual(self.ui.CHARACTERDESIGNER_OT_worklist_sync_back().execute(self.context), {'CANCELLED'})
        self.assertTrue(self.state.has_error)
        self.assertEqual(self.state.status, status)
        self.assertEqual(self.snapshot(), before)
        self.reconcile.plan.assert_not_called()

    def test_confirm_rejects_wrong_review_token_or_path_before_timer_or_backend(self):
        self.open_review()
        before = self.snapshot()
        status = self.state.status
        for token, path in (('wrong-token', 'checked-workspace.json'),
                            ('checked-plan-token', 'another-workspace.json')):
            with self.subTest(token=token, path=path):
                operator = self.confirm(token=token)
                operator.workspace_path = path
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertEqual(self.state.status, status)
                self.assertIsNotNone(self.ui._LIST_SYNC_REVIEW)
        self.timers.register.assert_not_called()
        self.reconcile.plan.assert_not_called()
        self.reconcile.begin.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_confirm_wrong_window_or_removed_scene_cannot_consume_review(self):
        for owner_change in ('window', 'removed_scene'):
            with self.subTest(owner_change=owner_change):
                self.context.window = self.window
                self.context.window_manager.windows = [self.window]
                sys.modules['bpy'].data.scenes = [self.context.scene]
                self.open_review()
                lease = self.ui._LIST_SYNC_REVIEW
                status = self.state.status
                if owner_change == 'window':
                    other = SimpleNamespace(scene=self.context.scene, screen=SimpleNamespace(areas=[]),
                                            identity='another-window')
                    self.context.window_manager.windows.append(other)
                    self.context.window = other
                else:
                    sys.modules['bpy'].data.scenes = []
                self.assertEqual(self.confirm().execute(self.context), {'CANCELLED'})
                self.assertIs(self.ui._LIST_SYNC_REVIEW, lease)
                self.assertEqual(self.state.status, status)
                self.timers.register.assert_not_called()
                self.reconcile.plan.assert_not_called()
                self.reconcile.begin.assert_not_called()
        self.context.window = self.window

    def test_confirm_duplicate_and_pending_cancel_keep_records_and_release_only_own_timer(self):
        self.open_review()
        before = self.snapshot()
        operator = self.confirm()
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        status = self.state.status
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(self.state.status, status)
        self.timers.register.assert_called_once_with(self.ui._poll_list_sync, first_interval=.05)
        self.collection.running.return_value = True
        self.assertEqual(self.ui.CHARACTERDESIGNER_OT_worklist_cancel_collection().execute(self.context), {'FINISHED'})
        self.timers.unregister.assert_called_once_with(self.ui._poll_list_sync)
        self.collection.cancel.assert_not_called()
        self.assertFalse(self.ui.scan_pending())
        self.assertIsNone(self.ui._poll_list_sync())
        self.assertIn('cancelled before it started', self.state.status)
        self.assertEqual(self.snapshot(), before)
        self.reconcile.plan.assert_not_called()
        self.reconcile.begin.assert_not_called()

    def test_confirm_owner_scene_or_window_loss_cancels_before_fresh_begin(self):
        for removed in ('window', 'scene', 'changed_scene'):
            with self.subTest(removed=removed):
                self.context.window_manager.windows = [self.window]
                sys.modules['bpy'].data.scenes = [self.context.scene]
                self.window.scene = self.context.scene
                self.open_review()
                self.assertEqual(self.confirm().execute(self.context), {'FINISHED'})
                other = SimpleNamespace(status='Other scene unchanged', has_error=False)
                if removed == 'window':
                    self.context.window_manager.windows = []
                elif removed == 'scene':
                    sys.modules['bpy'].data.scenes = []
                else:
                    self.window.scene = SimpleNamespace(character_designer_animation_worklist=other)
                self.ui._poll_list_sync()
                self.assertFalse(self.ui.scan_pending())
                self.assertEqual(other.status, 'Other scene unchanged')
                self.reconcile.plan.assert_not_called()
                self.reconcile.begin.assert_not_called()

    def test_confirm_timer_registration_failure_keeps_data_and_cannot_be_hidden_by_back(self):
        self.open_review()
        before = self.snapshot()
        self.timers.register.side_effect = RuntimeError('Timer registration failed')
        operator = self.confirm()
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(operator.last_report, ({'ERROR'}, 'Timer registration failed'))
        self.assertFalse(self.ui.scan_pending())
        self.assertIsNone(self.ui._LIST_SYNC_REVIEW)
        self.assertTrue(self.state.has_error)
        self.assertEqual(self.state.status, 'Timer registration failed')
        self.assertEqual(self.ui.CHARACTERDESIGNER_OT_worklist_sync_back().execute(self.context), {'CANCELLED'})
        self.assertEqual(self.state.status, 'Timer registration failed')
        self.assertTrue(self.state.has_error)
        self.timers.unregister.assert_not_called()
        self.reconcile.plan.assert_not_called()
        self.reconcile.begin.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_confirm_late_job_or_lost_timer_does_not_start_backend(self):
        for late in ('job', 'lost_timer'):
            with self.subTest(late=late):
                self.animation._link_idle.return_value = True
                self.timers.is_registered.return_value = True
                self.open_review()
                self.assertEqual(self.confirm().execute(self.context), {'FINISHED'})
                if late == 'job':
                    self.animation._link_idle.return_value = False
                    self.ui._poll_list_sync()
                    self.assertTrue(self.state.has_error)
                else:
                    self.timers.is_registered.return_value = False
                    self.assertFalse(self.ui.scan_pending())
                    self.assertIsNone(self.ui._poll_list_sync())
                self.reconcile.plan.assert_not_called()
                self.reconcile.begin.assert_not_called()

    def test_back_cannot_overwrite_started_or_completed_confirm_result(self):
        for outcome in ('Sync running', 'Animation list synchronized.'):
            with self.subTest(outcome=outcome):
                self.open_review()
                back = self.ui.CHARACTERDESIGNER_OT_worklist_sync_back()
                self.assertEqual(self.confirm().execute(self.context), {'FINISHED'})
                pending_status = self.state.status
                self.assertEqual(back.execute(self.context), {'CANCELLED'})
                self.assertEqual(self.state.status, pending_status)

                def begin(*_args, **_kwargs):
                    self.state.status = self.state.collection_status = outcome

                self.reconcile.begin.side_effect = begin
                self.ui._poll_list_sync()
                self.assertEqual(back.execute(self.context), {'CANCELLED'})
                self.assertEqual(self.state.status, outcome)
                self.assertEqual(self.state.collection_status, outcome)
                self.reconcile.begin.side_effect = None

    def test_back_rejects_unowned_feedback_busy_or_wrong_scene_without_clearing_current_review(self):
        self.open_review()
        back = self.ui.CHARACTERDESIGNER_OT_worklist_sync_back()
        owner_review = self.ui._LIST_SYNC_REVIEW
        for change in ('feedback', 'busy', 'scene'):
            with self.subTest(change=change):
                self.state.status = self.state.collection_status = owner_review['feedback']
                self.state.has_error = False
                self.animation._link_idle.return_value = True
                original_scene = self.context.scene
                if change == 'feedback':
                    self.state.status = self.state.collection_status = 'Another operation finished.'
                elif change == 'busy':
                    self.animation._link_idle.return_value = False
                else:
                    self.context.scene = SimpleNamespace(character_designer_animation_worklist=SimpleNamespace(
                        status='Other scene unchanged', collection_status='Other scene unchanged', has_error=False))
                status = self.ui._state(self.context).status
                self.assertEqual(back.execute(self.context), {'CANCELLED'})
                self.assertEqual(self.ui._state(self.context).status, status)
                self.assertIs(self.ui._LIST_SYNC_REVIEW, owner_review)
                self.context.scene = original_scene

    def test_review_lease_consumes_only_when_requested_and_stop_clears_it(self):
        self.open_review()
        review = self.ui._LIST_SYNC_REVIEW
        self.assertIs(self.ui._list_sync_review(self.context), review)
        self.assertIs(self.ui._LIST_SYNC_REVIEW, review)
        self.assertIsNone(self.ui._list_sync_review(self.context, token='wrong', clear=True))
        self.assertIs(self.ui._LIST_SYNC_REVIEW, review)
        self.assertIs(self.ui._list_sync_review(self.context, clear=True), review)
        self.assertIsNone(self.ui._LIST_SYNC_REVIEW)
        self.open_review()
        self.ui.stop_worklist_ui()
        self.assertIsNone(self.ui._LIST_SYNC_REVIEW)

    def test_confirm_rejects_busy_editor_before_backend(self):
        before = self.snapshot()
        self.open_review()
        self.animation._link_idle.return_value = False
        self.assertEqual(self.confirm().execute(self.context), {'CANCELLED'})
        self.reconcile.begin.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_draw_has_sync_list_and_no_import_button_without_disk_or_backend_access(self):
        self.state.show_advanced = True
        self.state.retired_items = [SimpleNamespace(item_id='retired-walk', name='Old Walk')]
        before = self.snapshot()
        layout = fixtures.Layout()
        with patch.object(Path, 'open', side_effect=AssertionError('Draw read file')), \
                patch.object(Path, 'read_text', side_effect=AssertionError('Draw read text')), \
                patch.object(Path, 'stat', side_effect=AssertionError('Draw probed file')), \
                patch.object(self.ui, '_backend', side_effect=AssertionError('Draw called backend')), \
                patch.object(self.ui, '_reconcile', side_effect=AssertionError('Draw reconciled')):
            self.ui.draw_worklist(layout, self.context)
        buttons = [value for kind, value, _enabled in layout.records if kind == 'operator']
        self.assertIn(('character_designer.worklist_sync_list', 'Sync List'),
                      [(value[0], value[1]['text']) for value in buttons])
        self.assertFalse(any(value[0] == 'character_designer.worklist_add_ready'
                             or 'Import' in value[1]['text'] for value in buttons))
        restore = [value for value in buttons if value[0] == 'character_designer.worklist_restore_retired']
        self.assertEqual(len(restore), 1)
        self.assertEqual((restore[0][1]['text'], restore[0][2].item_id), ('Restore', 'retired-walk'))
        self.assertEqual(self.snapshot(), before)

    def test_retired_section_is_hidden_until_advanced_is_opened(self):
        self.state.retired_items = [SimpleNamespace(item_id='retired-walk', name='Old Walk')]
        for expanded in (False, True):
            with self.subTest(expanded=expanded):
                self.state.show_advanced = expanded
                layout = fixtures.Layout()
                self.ui.draw_worklist(layout, self.context)
                labels = [value['text'] for kind, value, _enabled in layout.records if kind == 'label']
                self.assertEqual('Retired Animations (1)' in labels, expanded)
                restores = [value for kind, value, _enabled in layout.records
                            if kind == 'operator' and value[0] == 'character_designer.worklist_restore_retired']
                self.assertEqual(len(restores), int(expanded))

    def test_restore_uses_stable_retired_item_id_without_activation_or_rebind(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_restore_retired()
        operator.item_id = 'saved-retired-id'
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.reconcile.restore_retired.assert_called_once_with(self.context, 'saved-retired-id')
        self.backend.activate.assert_not_called()
        self.backend.add.assert_not_called()
        self.reconcile.begin.assert_not_called()

    def test_restore_failure_is_reported_without_changing_existing_records(self):
        before = self.snapshot()
        self.reconcile.restore_retired.side_effect = ValueError('This retired record is unavailable.')
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_restore_retired()
        operator.item_id = 'missing-id'
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(operator.last_report, ({'ERROR'}, 'This retired record is unavailable.'))
        self.assertEqual(self.snapshot(), before)

    def test_retired_records_are_persistent_and_new_operators_are_registered(self):
        state = self.ui.CharacterDesignerAnimationWorklistState.__annotations__
        self.assertEqual(state['retired_items']['type'], self.ui.CharacterDesignerAnimationWorklistItem)
        self.assertNotIn('SKIP_SAVE', state['retired_items'].get('options', ()))
        self.assertNotIn('update', state['retired_items'])
        for cls in (self.ui.CHARACTERDESIGNER_OT_worklist_sync_list,
                    self.ui.CHARACTERDESIGNER_OT_worklist_sync_review,
                    self.ui.CHARACTERDESIGNER_OT_worklist_sync_confirm,
                    self.ui.CHARACTERDESIGNER_OT_worklist_sync_back,
                    self.ui.CHARACTERDESIGNER_OT_worklist_restore_retired):
            self.assertIn(cls, self.ui.WORKLIST_CLASSES)


if __name__ == '__main__':
    unittest.main()
