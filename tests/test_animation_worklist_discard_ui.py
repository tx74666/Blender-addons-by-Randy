"""Explicit Discard gestures reuse Sync's actual review lease and timer guards."""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import test_animation_worklist_sync_ui as sync_fixtures
import test_animation_worklist_ui as ui_fixtures


class DiscardUI(unittest.TestCase):
    plan = staticmethod(sync_fixtures.WorklistSyncUI.plan)
    snapshot = sync_fixtures.WorklistSyncUI.snapshot
    queue = sync_fixtures.WorklistSyncUI.queue
    open_review = sync_fixtures.WorklistSyncUI.open_review

    def setUp(self):
        sync_fixtures.WorklistSyncUI.setUp(self)
        self.reconcile.discard_retired = Mock(return_value={'item_ids': ['exact-old-id'],
            'removed_actions': [], 'kept_actions': [{'name': 'Recovery carrier', 'reason': 'History required.'}]})

    def confirm(self, *, token='checked-plan-token', path='checked-workspace.json'):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_discard_confirm()
        operator.plan_token, operator.workspace_path = token, path
        return operator

    def test_review_and_advanced_buttons_have_distinct_exact_routes(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync_review()
        operator.plan_token, operator.workspace_path = 'review-token', 'review-path'
        operator.extras_json = json.dumps([{'name': 'Old', 'reason': 'Outside Unity.'}])
        operator.layout = sync_fixtures.PopupLayout()
        operator.draw(self.context)
        buttons = {value[0]: value for kind, value, _enabled in operator.layout.records if kind == 'operator'}
        for suffix, title in (('sync_confirm', 'Retire and Sync'),
                              ('sync_discard_confirm', 'Discard and Sync')):
            _name, options, payload = buttons['character_designer.worklist_' + suffix]
            self.assertEqual(options['text'], title)
            self.assertEqual((payload.plan_token, payload.workspace_path), ('review-token', 'review-path'))
        self.state.retired_items = [SimpleNamespace(name='Old', item_id='exact-old-id')]
        for advanced in (False, True):
            with self.subTest(advanced=advanced):
                self.state.show_advanced = advanced
                layout = ui_fixtures.Layout()
                self.ui.draw_worklist(layout, self.context)
                discards = [value for kind, value, _enabled in layout.records if kind == 'operator'
                    and value[0] == 'character_designer.worklist_discard_retired']
                self.assertEqual(len(discards), int(advanced))
                if advanced:
                    self.assertEqual(discards[0][2].item_id, 'exact-old-id')
        for cls in (self.ui.CHARACTERDESIGNER_OT_worklist_sync_discard_confirm,
                    self.ui.CHARACTERDESIGNER_OT_worklist_discard_retired):
            self.assertIn(cls, self.ui.WORKLIST_CLASSES)

    def test_single_discard_forwards_stable_id_and_explains_kept_actions_without_restore(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_discard_retired()
        operator.item_id = 'exact-old-id'
        operator.report = Mock()
        self.state.status = 'Discarded one row; recovery data kept.'
        before = self.snapshot()
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.reconcile.discard_retired.assert_called_once_with(self.context, 'exact-old-id')
        self.assertIn(unittest.mock.call({'INFO'}, 'Recovery carrier: History required.'), operator.report.call_args_list)
        self.reconcile.restore_retired.assert_not_called()
        self.reconcile.begin.assert_not_called()
        self.backend.activate.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_single_discard_failure_is_reported_and_busy_poll_remains_strict(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_discard_retired()
        operator.item_id = 'missing-id'
        before = self.snapshot()
        self.reconcile.discard_retired.side_effect = ValueError('No exact retired row.')
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(operator.last_report, ({'ERROR'}, 'No exact retired row.'))
        self.assertEqual(self.snapshot(), before)
        self.animation._link_idle.return_value = False
        self.assertFalse(operator.poll(self.context))
        self.reconcile.restore_retired.assert_not_called()

    def test_discard_sync_defers_exact_review_with_only_discard_option(self):
        self.open_review(token='exact-review-token')
        before = self.snapshot()
        operator = self.confirm(token='exact-review-token')
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.assertIn('Discarding old entries', self.state.status)
        self.reconcile.begin.assert_not_called()
        self.reconcile.plan.assert_not_called()
        self.reconcile.discard_retired.assert_not_called()
        self.timers.register.assert_called_once_with(self.ui._poll_list_sync, first_interval=.05)
        self.ui._poll_list_sync()
        self.reconcile.begin.assert_called_once_with(self.context, 'exact-review-token',
            workspace_path='checked-workspace.json', discard_extras=True)
        self.assertNotIn('deprecate_extras', self.reconcile.begin.call_args.kwargs)
        self.assertEqual(self.snapshot(), before)

    def test_discard_sync_missing_or_stale_review_and_busy_editor_never_reach_backend(self):
        self.assertEqual(self.confirm().execute(self.context), {'CANCELLED'})
        self.open_review()
        for token, path in (('wrong', 'checked-workspace.json'),
                            ('checked-plan-token', 'wrong-path')):
            with self.subTest(token=token, path=path):
                self.assertEqual(self.confirm(token=token, path=path).execute(self.context), {'CANCELLED'})
        self.animation._link_idle.return_value = False
        self.assertEqual(self.confirm().execute(self.context), {'CANCELLED'})
        self.timers.register.assert_not_called()
        self.reconcile.begin.assert_not_called()
        self.reconcile.discard_retired.assert_not_called()


if __name__ == '__main__':
    unittest.main()
