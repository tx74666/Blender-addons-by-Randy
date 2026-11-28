"""Explicit Prepare queue checks; no Blender, native import or child processes."""
from pathlib import Path
from contextlib import contextmanager
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

import test_animation_controller_edit as fixtures


class AsyncControllerEdit(unittest.TestCase):
    def setUp(self):
        fixtures.ControllerEdit.setUp(self)
        self.worker = ModuleType(self.service.__package__ + '.animation_controller_worker')
        self.job = dict(root=Path('private-async-job'), started=0, phase='Preparing inputs',
                        cancel_requested=False, process=None, state='PREPARING')
        self.worker.begin = Mock(return_value=self.job)
        self.worker.finish = Mock(return_value=self.built)
        self.worker.close = Mock()
        self.worker.process_running = Mock(side_effect=lambda job: job['process'] is not None and job['process'].poll() is None)
        self.worker.request_cancel = Mock(side_effect=lambda job: job.update(cancel_requested=True))
        self.worker.poll = Mock(return_value=None)
        sys.modules[self.worker.__name__] = self.worker
        setattr(sys.modules[self.service.__package__], 'animation_controller_worker', self.worker)
        self.service._start_runtime = Mock()
        self.service._redraw_pending = Mock()
        self.service._remove_handlers = Mock()
        self.service.return_to_character = Mock(side_effect=lambda context, home: setattr(context.window, 'scene', home))
        self.context.window.scene = self.source
        self.context.window_manager.windows = [self.context.window]
        self.finished = []
        self.bpy.app = SimpleNamespace(background=True)
        actual_finish = self.service.finalize_pending
        def record_finish(context, key):
            result = actual_finish(context, key)
            self.finished.append(result)
            return result
        self.service.finalize_pending = record_finish
        self.row.source_action = fixtures.Action('Read-only Source')
        self.edit._local_row.side_effect = lambda scene, key: next((r for r in scene.character_designer_animation_worklist.items if r.clip_key == key), None)

    def start(self, *, bulk=False, capture=True):
        result = self.service.prepare_missing(self.context, self.home) if bulk else self.service.prepare_motion(self.context, self.home, self.row.clip_key)
        self.key = result.get('job_id')
        if capture and result['state'] == 'PENDING':
            self.service._poll_pending()  # Visible name/phase, before import/capture.
            self.service._poll_pending()  # begin creates only the cooperative token.
        return result

    def complete(self):
        self.job['state'] = 'READY'
        self.worker.poll.return_value = {'state': 'EXITED'}
        self.service._poll_pending()
        self.worker.poll.return_value = None
        return self.finished[-1]

    def other_row(self, key='clip-run'):
        row = SimpleNamespace(**vars(self.row))
        row.clip_key, row.work_item_id, row.item_id, row.name = key, 'work-' + key, 'item-' + key, key
        row.custom_action = fixtures.Action(key + ' Custom')
        self.source.character_designer_animation_worklist.items.append(row)
        return row

    def test_sample_progress_is_only_shown_for_the_current_running_motion(self):
        other = self.other_row()
        self.start(bulk=True)
        self.job.update(state='RUNNING', progress_detail=dict(sample=17, total=72, round=1))
        self.assertEqual(self.service.pending(self.home)['progress_detail']['sample'], 17)
        self.assertEqual(self.service.pending(self.home, other.clip_key)['progress_detail'], {})
        self.service.cancel_pending(self.key)
        self.assertEqual(self.service.pending(self.home)['progress_detail'], {})

    def click_home(self):
        self.context.window.scene = self.home
        self.home.character_designer_animation_worklist.selected_motion_clip = self.row.clip_key
        self.service._open_context = Mock(side_effect=lambda ctx, target: (ctx.scene.frame_current, target.manual_pose))
        result = self.service.open_motion(self.context, self.home, self.row.clip_key, prepare_on_open=True)
        self.key = result['job_id']
        self.service._poll_pending()
        self.service._poll_pending()
        return result

    def test_home_click_prepares_daily_then_opens_verified_target(self):
        self.home.character_designer_animation_worklist.precise_imported_motions = True
        self.assertEqual(self.click_home()['state'], 'PENDING')
        self.assertTrue(self.worker.begin.call_args.kwargs['daily_edit'])
        self.assertIs(self.target.animation_data.action, self.previous_action)
        self.assertEqual(self.complete()['state'], 'CONTROLLER')
        self.assertIs(self.target.animation_data.action, self.records[0].controller_action)

    def test_edited_pose_while_waiting_restores_no_stale_selection_or_pose(self):
        self.click_home()
        self.target.manual_pose = 'new unkeyed edits'
        self.assertEqual(self.complete()['state'], 'READY')
        self.assertIs(self.target.animation_data.action, self.previous_action)
        self.assertEqual(self.target.manual_pose, 'new unkeyed edits')
        self.assertIs(self.records[0].controller_action, self.built.action)

    def test_updating_the_previously_bound_action_cancels_only_delayed_open(self):
        self.click_home()
        event = SimpleNamespace(updates=[SimpleNamespace(id=self.previous_action)])
        self.service._watch_open_action(self.home, event)
        self.assertEqual(self.complete()['state'], 'READY')
        self.assertIs(self.target.animation_data.action, self.previous_action)

    def test_changing_highlight_while_waiting_retains_the_new_choice(self):
        self.click_home()
        settings = self.home.character_designer_animation_worklist
        settings.catalog = [self.row, self.other_row()]
        settings.catalog_index = 1
        self.assertEqual(self.complete()['state'], 'READY')
        self.assertIs(self.target.animation_data.action, self.previous_action)

    def start_fk_conversion(self):
        self.context.window.scene = self.home
        self.home.render = SimpleNamespace(fps=30, fps_base=1.)
        self.home.character_designer_animation_worklist.selected_motion_clip = self.row.clip_key
        self.service._open_context = Mock(side_effect=lambda ctx, target: (ctx.scene.frame_current, target.manual_pose))
        self.service._require_keyed_inputs = Mock()
        self.context.screen = SimpleNamespace(is_animation_playing=False)
        ad = self.target.animation_data
        ad.use_tweak_mode = False
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.
        fk = fixtures.Action('My edited FK')
        fk.get = {'character_designer_controller_representation': 'FK'}.get
        fk.data['artist'] = 44
        record = self.service._record(self.home, self.row)
        self.service._publish(record, self.row, SimpleNamespace(action=fk, slot=5, source_fingerprint='source'))
        self.built.action.data['character_designer_controller_representation'] = 'IK'
        result = self.service.prepare_motion(self.context, self.home, self.row.clip_key, from_fk=True)
        self.key = result['job_id']
        self.service._poll_pending()
        self.service._poll_pending()
        self.worker.begin.assert_called_once_with(self.context, self.target, fk, 5, self.target,
                                                 source_fps=30., daily_edit=False)
        return fk, record

    def test_convert_fk_replaces_working_edit_and_opens_verified_ik(self):
        fk, record = self.start_fk_conversion()
        self.assertEqual(self.complete()['state'], 'CONTROLLER')
        self.assertIs(record.controller_action, self.built.action)
        self.assertEqual(record.controller_slot, self.built.slot)
        self.assertIsNone(record.alternate_action)
        self.assertTrue(fk.use_fake_user)
        self.assertEqual(fk.data['artist'], 44)
        self.browser.classify_action.assert_called_with(fk, self.target, role='ARCHIVED',
                                                       label=self.row.name, clip_id=self.row.work_item_id)
        self.assertIs(self.target.animation_data.action, self.built.action)
        self.assertFalse(self.service._needs_full_ik(record))

    def test_convert_fk_leaves_newly_edited_pose_and_previous_action_open(self):
        _fk, record = self.start_fk_conversion()
        self.target.manual_pose = 'New edits while converting'
        self.assertEqual(self.complete()['state'], 'READY')
        self.assertIs(record.controller_action, self.built.action)
        self.assertIs(self.target.animation_data.action, self.previous_action)
        self.assertEqual(self.target.manual_pose, 'New edits while converting')

    def test_convert_fk_leaves_existing_unkeyed_edits_open(self):
        _fk, record = self.start_fk_conversion()
        self.service._require_keyed_inputs.side_effect = ValueError('Unkeyed control change')
        self.assertEqual(self.complete()['state'], 'READY')
        self.assertIs(record.controller_action, self.built.action)
        self.assertIs(self.target.animation_data.action, self.previous_action)
        self.assertIn('Unkeyed control change', self.home.character_designer_animation_worklist.status)

    def test_switching_to_another_draft_before_full_ik_publication_keeps_edits(self):
        self.context.window.scene = self.home
        self.home.render = SimpleNamespace(fps=30, fps_base=1.)
        self.service._open_context = Mock()
        fk = fixtures.Action('FK')
        fk.get = {'character_designer_controller_representation': 'FK'}.get
        record = self.service._record(self.home, self.row)
        self.service._publish(record, self.row, SimpleNamespace(action=fk, slot=5, source_fingerprint='f'))
        legacy = fixtures.Action('Old independent IK edit')
        record.alternate_action, record.alternate_slot = legacy, 6
        self.service.prepare_motion(self.context, self.home, self.row.clip_key, from_fk=True)
        self.service._poll_pending()
        self.service._poll_pending()
        record.controller_slot = 8
        key = next(iter(self.service._PENDING))
        with self.assertRaisesRegex(ValueError, 'source work item changed'):
            self.service.finalize_pending(self.context, key)
        self.worker.finish.assert_not_called()
        self.assertIs(record.alternate_action, legacy)

    def test_prepare_freezes_character_accuracy_when_the_batch_is_queued(self):
        settings = self.home.character_designer_animation_worklist
        settings.precise_imported_motions = True
        self.start(bulk=True, capture=False)
        settings.precise_imported_motions = False
        self.service._poll_pending()
        self.service._poll_pending()
        self.assertFalse(self.worker.begin.call_args.kwargs['daily_edit'])

    def test_existing_character_defaults_to_daily_edit_accuracy(self):
        self.start(bulk=True)
        self.assertTrue(self.worker.begin.call_args.kwargs['daily_edit'])
        self.assertTrue(self.worker.begin.call_args.kwargs['align_loop_period'])

    def test_prepare_preserves_the_loop_timing_selected_for_the_batch(self):
        settings = self.home.character_designer_animation_worklist
        settings.align_loop_period = False
        self.start(bulk=True, capture=False)
        settings.align_loop_period = True
        self.service._poll_pending()
        self.service._poll_pending()
        self.assertFalse(self.worker.begin.call_args.kwargs['align_loop_period'])

    def test_prepare_enqueues_and_draws_before_import_or_capture(self):
        result = self.start(capture=False)
        self.assertEqual(result['state'], 'PENDING')
        self.assertEqual(self.service.pending(self.home)['phase'], 'Queued')
        self.worker.begin.assert_not_called()
        self.work.add.assert_not_called()
        self.service._poll_pending()
        self.worker.begin.assert_not_called()
        self.assertEqual(self.service.pending(self.home)['name'], self.row.name)
        self.service._poll_pending()
        self.worker.begin.assert_called_once()
        self.assertEqual(self.records, [])
        self.assertIs(self.context.scene, self.source)

    def test_duplicate_click_keeps_one_job_and_one_queue_entry(self):
        self.start()
        self.service.prepare_motion(self.context, self.home, self.row.clip_key)
        self.assertEqual(len(self.service._PENDING), 1)
        self.assertEqual(self.service.pending(self.home)['total'], 1)
        self.worker.begin.assert_called_once()
        self.assertIs(self.target.animation_data.action, self.previous_action)
        self.assertEqual(self.records, [])
        self.local.assign_character_action.assert_not_called()

    def test_completion_publishes_ready_unbound_even_in_unchanged_initiating_window(self):
        self.start()
        self.assertEqual(self.complete()['state'], 'READY')
        self.assertEqual(self.records[0].state, 'READY')
        self.assertIs(self.records[0].controller_action, self.built.action)
        self.assertIs(self.target.animation_data.action, self.previous_action)
        self.assertIs(self.context.scene, self.source)
        self.service.return_to_character.assert_not_called()
        self.local.assign_character_action.assert_not_called()
        self.work.activate.assert_not_called()
        self.assertFalse(self.service._PENDING)
        self.worker.close.assert_called_once_with(self.job)

    def test_legacy_unchecked_value_does_not_cancel_explicit_preparation(self):
        self.start()
        self.home.character_designer_animation_worklist.convert_imported_to_controls = False
        self.assertEqual(self.complete()['state'], 'READY')
        self.worker.finish.assert_called_once()

    def test_bulk_uses_all_rows_ignores_filters_and_prioritizes_cursor_not_binding(self):
        other = self.other_row()
        state = self.source.character_designer_animation_worklist
        state.active_index, state.list_cursor = 0, 1
        state.search, state.pinned_only = 'nothing-matches', True
        catalog = SimpleNamespace(clip_key='clip-jump', work_item_id='work-jump', ready=True, name='Jump')
        self.home.character_designer_animation_worklist.catalog = [self.row, catalog]
        self.row.ready = True
        self.start(bulk=True, capture=False)
        entries = self.service._PENDING[self.key]['remaining']
        self.assertEqual([r['clip_key'] for r in entries], [other.clip_key, self.row.clip_key, 'clip-jump'])
        self.assertEqual(self.service.pending(self.home)['total'], 3)
        self.assertEqual(state.active_index, 0)

    def test_bulk_skips_saved_edits_and_known_failures_but_explicit_retry_is_allowed(self):
        other = self.other_row()
        ready = self.service._record(self.home, self.row)
        self.service._publish(ready, self.row, self.built)
        ready.source_fingerprint = 'old source revision, user edited this output'
        failed = self.service._record(self.home, other)
        failed.state, failed.error = 'MAPPING_NEEDED', 'Unsupported mapping'
        self.assertEqual(self.service.missing_motions(self.home), ())
        self.assertEqual(self.service.prepare_missing(self.context, self.home)['state'], 'READY')
        self.assertEqual(self.service.prepare_motion(self.context, self.home, other.clip_key)['state'], 'PENDING')
        self.assertIs(ready.controller_action, self.built.action)
        self.worker.begin.assert_not_called()

    def test_existing_incomplete_output_is_retained_and_never_rebuilt(self):
        record = self.service._record(self.home, self.row)
        record.controller_action = self.built.action
        self.assertEqual(self.service.missing_motions(self.home), ())
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.service.prepare_motion(self.context, self.home, self.row.clip_key)
        self.assertIs(record.controller_action, self.built.action)
        self.worker.begin.assert_not_called()

    def test_bulk_and_explicit_prepare_exclude_authored_and_current_rig_rows(self):
        for field, value in (('new_animation', True), ('new_action_id', 'new-id'),
                             ('current_rig_revision', 'revision'), ('composition_json', '{}')):
            with self.subTest(field=field):
                setattr(self.row, field, value)
                self.row.ready = True
                self.home.character_designer_animation_worklist.catalog = [self.row]
                self.assertEqual(self.service.missing_motions(self.home), ())
                with self.assertRaisesRegex(ValueError, 'no longer available'):
                    self.service.prepare_motion(self.context, self.home, self.row.clip_key)
                setattr(self.row, field, False if field == 'new_animation' else '')
        self.row.source_action = None
        self.assertEqual(self.service.missing_motions(self.home), ())

    def test_duplicate_source_ids_are_not_bulk_candidates_or_explicitly_prepared(self):
        self.other_row(self.row.clip_key)
        self.assertEqual(self.service.missing_motions(self.home), ())
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            self.service.prepare_motion(self.context, self.home, self.row.clip_key)

    def test_queue_starts_next_only_after_first_result_is_published_and_closed(self):
        other = self.other_row()
        self.start(bulk=True)
        self.worker.begin.assert_called_once()
        self.complete()
        self.worker.close.assert_called_once()
        self.assertEqual(self.service.pending(self.home)['completed'], 1)
        self.assertEqual(self.service.pending(self.home, other.clip_key)['phase'], 'Queued')
        self.service._poll_pending()
        self.worker.begin.assert_called_once()
        self.service._poll_pending()
        self.assertEqual(self.worker.begin.call_count, 2)
        self.assertIs(self.worker.begin.call_args.args[2], other.custom_action)
        self.assertEqual(len(self.records), 1)

    def test_ready_edit_appearing_while_queued_is_skipped_before_begin(self):
        other = self.other_row()
        self.start(bulk=True)
        record = self.service._record(self.home, other)
        self.service._publish(record, other, self.built)
        self.complete()
        self.service._poll_pending()
        self.service._poll_pending()
        self.worker.begin.assert_called_once()
        self.assertFalse(self.service._PENDING)
        self.assertIn('1 skipped', self.home.character_designer_animation_worklist.status)

    def test_newly_known_mapping_failure_is_not_automatically_retried_by_queued_bulk(self):
        other = self.other_row()
        self.start(bulk=True)
        record = self.service._record(self.home, other)
        record.state, record.error = 'MAPPING_NEEDED', 'Known unsupported mapping'
        self.complete()
        self.service._poll_pending()
        self.service._poll_pending()
        self.worker.begin.assert_called_once()
        self.assertFalse(self.service._PENDING)
        self.assertIn('1 need attention', self.home.character_designer_animation_worklist.status)
        self.assertEqual(record.error, 'Known unsupported mapping')

    def test_preparing_and_verifying_poll_quickly_but_background_child_uses_slower_poll(self):
        self.start()
        self.assertEqual(self.service._poll_pending(), .016)
        self.job['state'] = 'RUNNING'
        self.assertEqual(self.service._poll_pending(), .25)
        self.job['state'] = 'VERIFYING'
        self.assertEqual(self.service._poll_pending(), .016)
        self.job['state'] = 'RUNNING'
        self.service.cancel_pending(self.key)
        self.assertEqual(self.service._poll_pending(), .016)

    def test_explicit_new_item_appends_to_same_queue_without_duplicate_job(self):
        self.start()
        other = self.other_row()
        result = self.service.prepare_motion(self.context, self.home, other.clip_key)
        self.assertEqual(result['job_id'], self.key)
        self.assertEqual(self.service.pending(self.home)['total'], 2)
        self.worker.begin.assert_called_once()

    def test_cancel_before_first_tick_launches_nothing(self):
        self.start(capture=False)
        self.service.cancel_pending(self.key)
        self.assertIsNone(self.service._poll_pending())
        self.worker.begin.assert_not_called()
        self.worker.finish.assert_not_called()
        self.work.add.assert_not_called()
        self.assertFalse(self.service._PENDING)

    def test_cancel_prelaunch_token_has_no_process_and_never_finishes(self):
        self.start()
        self.service.cancel_pending(self.key)
        self.worker.poll.return_value = {'state': 'CANCELLED'}
        self.assertIsNone(self.service._poll_pending())
        self.worker.finish.assert_not_called()
        self.worker.close.assert_called_once()
        self.assertFalse(self.service._PENDING)

    def test_cancel_keeps_completed_record_and_drops_remaining_work(self):
        self.other_row()
        self.start(bulk=True)
        self.complete()
        output = self.records[0].controller_action
        self.service.cancel_pending(self.key)
        self.assertIsNone(self.service._poll_pending())
        self.worker.begin.assert_called_once()
        self.assertIs(self.records[0].controller_action, output)
        self.assertEqual(self.records[0].state, 'READY')
        self.bpy.data.actions.remove.assert_not_called()
        self.assertIn('1 ready', self.home.character_designer_animation_worklist.status)

    def test_cancel_runtime_retains_owner_until_exact_child_exits(self):
        self.start()
        process = Mock()
        process.poll.return_value = None
        self.job['process'] = process
        self.service.stop_runtime()
        self.assertEqual(self.service._poll_pending(), .016)
        self.worker.close.assert_not_called()
        self.assertTrue(self.service._PENDING)
        process.poll.return_value = 1
        self.worker.poll.return_value = {'state': 'CANCELLED'}
        self.assertIsNone(self.service._poll_pending())
        self.worker.close.assert_called_once()

    def test_cancel_wins_before_verified_handoff(self):
        self.start()
        self.service.cancel_pending(self.key)
        result = self.service.finalize_pending(self.context, self.key)
        self.assertEqual(result['state'], 'CANCELLED')
        self.worker.finish.assert_not_called()
        self.assertEqual(self.records, [])

    def test_cancel_during_handoff_discards_unpublished_result_only(self):
        self.start()
        def finish(_job):
            self.service.cancel_pending(self.key)
            return self.built
        self.worker.finish.side_effect = finish
        result = self.service.finalize_pending(self.context, self.key)
        self.assertEqual(result['state'], 'CANCELLED')
        self.bpy.data.actions.remove.assert_called_once_with(self.built.action)
        self.assertEqual(self.records, [])
        self.assertIs(self.target.animation_data.action, self.previous_action)

    def test_mapping_failure_records_reason_without_opening_and_continues_batch(self):
        self.other_row()
        self.start(bulk=True)
        self.worker.poll.side_effect = self.mapping.MappingNeeded('Unsupported source')
        self.service._poll_pending()
        self.assertEqual(self.records[0].state, 'MAPPING_NEEDED')
        self.assertEqual(self.records[0].error, 'Unsupported source')
        self.assertEqual(self.service.pending(self.home)['failed'], 1)
        self.work.activate.assert_not_called()
        self.worker.poll.side_effect = None
        self.service._poll_pending()
        self.service._poll_pending()
        self.assertEqual(self.worker.begin.call_count, 2)

    def test_prelaunch_mapping_refusal_never_falls_back_or_starts_child(self):
        self.worker.begin.side_effect = self.mapping.MappingNeeded('Unsupported transform')
        self.start()
        self.assertFalse(self.service._PENDING)
        self.assertEqual(self.records[0].state, 'MAPPING_NEEDED')
        self.work.activate.assert_not_called()
        self.worker.finish.assert_not_called()

    def test_unexpected_poll_error_stops_queue_and_keeps_honest_error(self):
        self.other_row()
        self.start(bulk=True)
        self.worker.poll.side_effect = OSError('Snapshot I/O failed')
        self.service._poll_pending()
        self.assertFalse(self.service._PENDING)
        self.assertEqual(self.records, [])
        self.assertIn('Snapshot I/O failed', self.home.character_designer_animation_worklist.status)
        self.assertTrue(self.home.character_designer_animation_worklist.has_error)
        self.worker.begin.assert_called_once()

    def test_failed_cleanup_keeps_poll_owner_until_retry_succeeds(self):
        self.start()
        self.worker.poll.side_effect = OSError('Worker failed')
        self.worker.close.side_effect = RuntimeError('Owned process cleanup needs retry')
        self.assertEqual(self.service._poll_pending(), .016)
        self.assertTrue(self.service._PENDING)
        self.worker.close.side_effect = None
        self.worker.poll.side_effect = None
        self.worker.poll.return_value = {'state': 'CANCELLED'}
        self.assertIsNone(self.service._poll_pending())
        self.assertFalse(self.service._PENDING)
        self.assertTrue(self.home.character_designer_animation_worklist.has_error)

    def test_nested_poll_does_not_resume_the_same_worker_twice(self):
        self.start()
        self.worker.poll.side_effect = lambda _job: (self.assertEqual(self.service._poll_pending(), .016), None)[1]
        self.assertEqual(self.service._poll_pending(), .016)
        self.worker.poll.assert_called_once()
        self.assertFalse(self.service._POLLING)
        self.assertFalse(self.job['cancel_requested'])

    def test_pending_cancel_is_retried_before_poll_if_first_cancel_hook_failed(self):
        self.start()
        self.worker.request_cancel.side_effect = RuntimeError('One-shot cancellation signal failed')
        with self.assertRaisesRegex(RuntimeError, 'cancellation signal'):
            self.service.cancel_pending(self.key)
        self.assertFalse(self.job['cancel_requested'])
        self.worker.request_cancel.side_effect = lambda job: job.update(cancel_requested=True)
        self.worker.poll.return_value = {'state': 'CANCELLED'}
        self.assertIsNone(self.service._poll_pending())
        self.assertTrue(self.job['cancel_requested'])
        self.assertFalse(self.service._PENDING)

    def test_source_fps_change_refuses_before_handoff(self):
        self.start()
        self.source.render.fps = 30
        with self.assertRaisesRegex(ValueError, 'source motion timing changed'):
            self.service.finalize_pending(self.context, self.key)
        self.worker.finish.assert_not_called()
        self.assertEqual(self.records, [])

    def test_source_slot_change_refuses_before_handoff(self):
        self.start()
        self.row.custom_slot = 8
        with self.assertRaisesRegex(ValueError, 'source work item changed'):
            self.service.finalize_pending(self.context, self.key)
        self.worker.finish.assert_not_called()
        self.assertEqual(self.records, [])

    def test_duplicate_source_added_during_worker_refuses_publication(self):
        self.start()
        self.other_row(self.row.clip_key)
        with self.assertRaisesRegex(ValueError, 'multiple source work items'):
            self.service.finalize_pending(self.context, self.key)
        self.worker.finish.assert_not_called()
        self.assertEqual(self.records, [])

    def test_duplicate_source_added_during_handoff_discards_only_unpublished_output(self):
        self.start()
        self.browser.classify_action.side_effect = lambda *a, **kw: self.other_row(self.row.clip_key)
        with self.assertRaisesRegex(ValueError, 'multiple source work items'):
            self.service.finalize_pending(self.context, self.key)
        self.bpy.data.actions.remove.assert_called_once_with(self.built.action)
        self.assertEqual(self.records, [])

    def test_stale_native_inputs_keep_disk_result_and_do_not_publish(self):
        self.start()
        self.worker.finish.side_effect = ValueError('Input proof changed; disk result retained')
        with self.assertRaisesRegex(ValueError, 'Input proof changed'):
            self.service.finalize_pending(self.context, self.key)
        self.assertEqual(self.records, [])
        self.bpy.data.actions.remove.assert_not_called()
        self.local.assign_character_action.assert_not_called()
        self.assertTrue(self.home.character_designer_animation_worklist.has_error)

    def test_classification_failure_discards_only_owned_unpublished_output(self):
        self.start()
        self.browser.classify_action.side_effect = ValueError('Classification failed')
        with self.assertRaisesRegex(ValueError, 'Classification failed'):
            self.service.finalize_pending(self.context, self.key)
        self.assertEqual(self.records, [])
        self.bpy.data.actions.remove.assert_called_once_with(self.built.action)
        self.assertIs(self.target.animation_data.action, self.previous_action)

    def test_partial_publication_restores_existing_record_and_discards_output(self):
        record = self.service._record(self.home, self.row)
        record.error = 'Earlier diagnostic'
        record.fail_field = 'controller_slot'
        self.start()
        with self.assertRaisesRegex(RuntimeError, 'field write failure'):
            self.service.finalize_pending(self.context, self.key)
        self.assertIsNone(record.controller_action)
        self.assertIsNone(record.source_action)
        self.assertEqual(record.error, 'Earlier diagnostic')
        self.assertEqual(record.state, 'UNTRIED')
        self.bpy.data.actions.remove.assert_called_once_with(self.built.action)

    def test_ready_record_appearing_during_worker_is_not_overwritten(self):
        self.start()
        record = self.service._record(self.home, self.row)
        self.service._publish(record, self.row, self.built)
        self.assertEqual(self.service.finalize_pending(self.context, self.key)['state'], 'READY')
        self.worker.finish.assert_not_called()
        self.assertIs(record.controller_action, self.built.action)
        self.bpy.data.actions.remove.assert_not_called()

    def test_incomplete_saved_record_during_worker_reports_error(self):
        self.start()
        record = self.service._record(self.home, self.row)
        record.controller_action = self.previous_action
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.service.finalize_pending(self.context, self.key)
        self.worker.finish.assert_not_called()
        self.assertIs(record.controller_action, self.previous_action)
        self.assertTrue(self.home.character_designer_animation_worklist.has_error)

    def test_record_completed_during_handoff_keeps_its_action(self):
        self.start()
        record = self.service._record(self.home, self.row)
        def complete_record(*args, **kwargs):
            record.state, record.controller_action = 'READY', self.previous_action
        self.browser.classify_action.side_effect = complete_record
        self.assertEqual(self.service.finalize_pending(self.context, self.key)['state'], 'READY')
        self.assertIs(record.controller_action, self.previous_action)
        self.bpy.data.actions.remove.assert_called_once_with(self.built.action)

    def test_incomplete_record_during_handoff_discards_only_unpublished_output(self):
        self.start()
        record = self.service._record(self.home, self.row)
        self.browser.classify_action.side_effect = lambda *a, **kw: setattr(record, 'controller_action', self.previous_action)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.service.finalize_pending(self.context, self.key)
        self.assertIs(record.controller_action, self.previous_action)
        self.assertEqual(record.state, 'UNTRIED')
        self.bpy.data.actions.remove.assert_called_once_with(self.built.action)

    def test_removed_target_or_changed_workspace_stops_before_begin(self):
        self.start(capture=False)
        self.home.objects.clear()
        self.assertIsNone(self.service._poll_pending())
        self.worker.begin.assert_not_called()
        self.assertFalse(self.service._PENDING)

    def test_closed_initiating_window_stops_before_import_or_begin(self):
        self.start(capture=False)
        self.context.window_manager.windows = []
        self.assertIsNone(self.service._poll_pending())
        self.work.add.assert_not_called()
        self.worker.begin.assert_not_called()

    def interactive_undo(self):
        self.bpy.app.background = False
        self.context.preferences = SimpleNamespace(edit=SimpleNamespace(use_global_undo=True))
        push = Mock(return_value={'FINISHED'})
        self.bpy.ops = SimpleNamespace(ed=SimpleNamespace(undo_push=push))
        return push

    def test_new_publication_pushes_one_undo_in_its_initiating_window(self):
        push = self.interactive_undo()
        self.start()
        originating = self.context.window
        seen = []
        def inspect_push(**kwargs):
            seen.append((self.context._overrides.copy(), kwargs['message']))
            return {'FINISHED'}
        push.side_effect = inspect_push
        self.complete()
        self.assertEqual(seen, [(dict(window=originating, scene=self.home, view_layer=self.home.view_layer),
                                 'Prepare ' + self.row.name + ' Controller Motion')])

    def test_undo_failure_retains_published_edit_and_reports_recovery(self):
        push = self.interactive_undo()
        push.side_effect = RuntimeError('No Undo stack')
        self.start()
        self.assertTrue(self.complete()['published'])
        self.assertIs(self.records[0].controller_action, self.built.action)
        self.assertIn('No Undo stack', self.home.character_designer_animation_worklist.status)

    def test_window_closed_after_capture_cancels_without_pushing_undo(self):
        push = self.interactive_undo()
        self.start()
        self.context.window_manager.windows = []
        self.service._poll_pending()
        self.worker.finish.assert_not_called()
        push.assert_not_called()

    def idle_timer_context(self, second_window=False):
        self.click_home()
        original_override = self.context.temp_override
        if second_window:
            self.context.window_manager.windows.append(SimpleNamespace(scene=self.source))
        self.context.window = None
        @contextmanager
        def native_window_override(**changes):
            previous = self.context.window
            self.context.window = changes.get('window', previous)
            try:
                with original_override(**changes):
                    yield self.context
            finally:
                self.context.window = previous
        self.context.temp_override = native_window_override

    def test_idle_timer_uses_sole_window_and_autoopens_verified_edit(self):
        self.idle_timer_context()
        self.assertEqual(self.complete()['state'], 'CONTROLLER')
        self.assertIs(self.target.animation_data.action, self.built.action)

    def test_idle_timer_with_multiple_windows_publishes_without_autoopen(self):
        self.idle_timer_context(second_window=True)
        self.assertEqual(self.complete()['state'], 'READY')
        self.assertIs(self.target.animation_data.action, self.previous_action)

    def test_native_window_collection_uses_identity_iteration_not_object_membership(self):
        class RNAWindows(list):
            def __contains__(self, _value):
                raise TypeError('RNA collection expects a string or tuple, not a Window')
        self.context.window_manager.windows = RNAWindows([self.context.window])
        self.start()
        self.worker.begin.assert_called_once()
        self.assertTrue(self.service._PENDING)

    def test_load_cancellation_cleans_job_after_original_home_rna_is_gone(self):
        self.start()
        self.service._cancel_for_read(None)
        class RemovedHome:
            @property
            def character_designer_animation_worklist(self):
                raise ReferenceError('Scene was removed by load')
        self.service._PENDING[self.key]['home'] = RemovedHome()
        self.worker.poll.return_value = {'state': 'CANCELLED'}
        self.assertIsNone(self.service._poll_pending())
        self.assertFalse(self.service._PENDING)
        self.worker.close.assert_called_once()
        self.assertEqual(self.source.character_designer_animation_worklist.status, '')

    def test_unregister_cancellation_finishes_after_worklist_rna_property_is_removed(self):
        self.start()
        self.service.stop_runtime()
        del self.home.character_designer_animation_worklist
        self.worker.poll.return_value = {'state': 'CANCELLED'}
        self.assertIsNone(self.service._poll_pending())
        self.assertFalse(self.service._PENDING)
        self.worker.close.assert_called_once()

    def test_successful_publication_is_counted_once_if_cleanup_needs_retry(self):
        self.start()
        self.worker.close.side_effect = RuntimeError('Owned cleanup needs retry')
        with self.assertRaisesRegex(RuntimeError, 'cleanup needs retry'):
            self.service.finalize_pending(self.context, self.key)
        self.assertEqual(self.records[0].state, 'READY')
        self.assertIs(self.records[0].controller_action, self.built.action)
        self.worker.close.side_effect = None
        self.service.cancel_pending(self.key)
        self.worker.poll.return_value = {'state': 'CANCELLED'}
        self.service._poll_pending()
        self.assertIn('1 ready', self.home.character_designer_animation_worklist.status)
        self.assertNotIn('2 ready', self.home.character_designer_animation_worklist.status)
        self.bpy.data.actions.remove.assert_not_called()

    def test_catalog_import_is_deferred_and_explicitly_skips_hidden_conversion(self):
        self.source.character_designer_animation_worklist.items = []
        self.home.character_designer_animation_worklist.catalog = [SimpleNamespace(
            clip_key=self.row.clip_key, work_item_id=self.row.work_item_id, name=self.row.name, ready=True)]
        self.start(capture=False)
        self.work.add.assert_not_called()
        self.service._poll_pending()
        self.work.add.assert_not_called()
        self.service._poll_pending()
        self.work.add.assert_called_once_with(self.context, self.row.clip_key, activate_new=False, prepare_controls=False)
        self.worker.begin.assert_called_once()
        self.assertIs(self.context.scene, self.source)

    def test_legacy_catalog_without_work_id_accepts_verified_import_identity(self):
        self.source.character_designer_animation_worklist.items = []
        self.home.character_designer_animation_worklist.catalog = [SimpleNamespace(
            clip_key=self.row.clip_key, work_item_id='', name=self.row.name, ready=True)]
        self.start()
        self.worker.begin.assert_called_once()
        self.assertEqual(self.service._PENDING[self.key]['work_item_id'], self.row.work_item_id)

    def test_imported_row_identity_change_while_queued_is_refused(self):
        self.row.work_item_id = ''
        self.start(capture=False)
        self.row.work_item_id = 'different work identity'
        self.service._poll_pending()
        self.service._poll_pending()
        self.worker.begin.assert_not_called()
        self.assertFalse(self.service._PENDING)
        self.assertIn('queued motion identity changed', self.home.character_designer_animation_worklist.status)


class RuntimeLifecycle(unittest.TestCase):
    def setUp(self):
        fixtures.ControllerEdit.setUp(self)
        self.registered = set()
        self.bpy.app = SimpleNamespace(
            handlers=SimpleNamespace(load_pre=[], undo_pre=[], redo_pre=[], depsgraph_update_post=[], persistent=lambda fn: fn),
            timers=SimpleNamespace(is_registered=self.registered.__contains__,
                                   register=Mock(side_effect=lambda fn, **kw: self.registered.add(fn)),
                                   unregister=Mock(side_effect=self.registered.remove)))

    def test_runtime_start_is_idempotent_and_stop_removes_only_owned_callbacks(self):
        foreign = lambda _: None
        self.bpy.app.handlers.load_pre.append(foreign)
        self.service._start_runtime()
        self.service._start_runtime()
        self.bpy.app.timers.register.assert_called_once_with(self.service._poll_pending,
                                                            first_interval=.016, persistent=True)
        self.assertEqual(self.bpy.app.handlers.load_pre, [foreign, self.service._cancel_for_read])
        self.assertEqual(self.bpy.app.handlers.depsgraph_update_post, [self.service._watch_open_action])
        self.service.stop_runtime()
        self.assertEqual(self.bpy.app.handlers.load_pre, [foreign])
        self.assertEqual(self.bpy.app.handlers.undo_pre, [])
        self.assertEqual(self.bpy.app.handlers.depsgraph_update_post, [])
        self.assertFalse(self.registered)

    def test_timer_registration_failure_rolls_back_only_added_handlers(self):
        foreign = lambda _: None
        self.bpy.app.handlers.undo_pre.append(foreign)
        self.bpy.app.timers.register.side_effect = RuntimeError('Injected timer failure')
        with self.assertRaisesRegex(RuntimeError, 'timer failure'):
            self.service._start_runtime()
        self.assertEqual(self.bpy.app.handlers.undo_pre, [foreign])
        self.assertEqual(self.bpy.app.handlers.load_pre, [])
        self.assertEqual(self.bpy.app.handlers.redo_pre, [])
        self.assertFalse(self.registered)


if __name__ == '__main__':
    unittest.main()
