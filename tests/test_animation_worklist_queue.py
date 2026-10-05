"""Worklist batch sequencing and interruption contracts; no Blender process."""

import importlib.util
from pathlib import Path
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_worklist_queue.py'
SPEC = importlib.util.spec_from_file_location('_animation_worklist_queue_test', SOURCE)
queue = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = queue
SPEC.loader.exec_module(queue)


class QueueTests(unittest.TestCase):
    def make_queue(self, mode=queue.ADD, keys=('walk', 'turn', 'stop')):
        return queue.SerialQueue(mode, [queue.QueueItem(key) for key in keys])

    def launch(self, batch):
        attempts = []
        self.assertTrue(batch.tick(attempts.append))
        self.assertEqual(len(attempts), 1)
        return attempts[0]

    def test_empty_batch_is_finished_without_callbacks(self):
        batch = self.make_queue(keys=())
        self.assertEqual(batch.state, queue.COMPLETED)
        self.assertFalse(batch.busy)
        self.assertFalse(batch.tick(lambda _attempt: self.fail('Unexpected launch.')))
        self.assertEqual(batch.remaining_keys, ())

    def test_invalid_modes_keys_and_duplicate_identity_are_rejected(self):
        with self.assertRaises(ValueError):
            self.make_queue('EXPORT')
        for key in ('', '  ', None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.make_queue(keys=(key,))
        with self.assertRaises(ValueError):
            self.make_queue(keys=('same-guid:42', 'same-guid:42'))
        with self.assertRaises(TypeError):
            queue.SerialQueue(queue.ADD, ['display name'])

    def test_worker_launch_return_does_not_advance_or_start_another(self):
        batch = self.make_queue()
        attempt = self.launch(batch)
        self.assertEqual(attempt.key, 'walk')
        for _ in range(3):
            self.assertFalse(batch.tick(lambda _attempt: self.fail('Overlapping launch.')))
        self.assertEqual(batch.succeeded_keys, ())
        self.assertEqual(batch.remaining_keys, ('walk', 'turn', 'stop'))
        self.assertTrue(batch.busy)

    def test_successful_prefix_waits_for_next_tick_and_keeps_busy_gap(self):
        batch = self.make_queue()
        first = self.launch(batch)
        self.assertTrue(batch.complete(first.token, success=True, result={'revision': 1}))
        self.assertIsNone(batch.running)
        self.assertTrue(batch.busy)
        self.assertEqual(batch.succeeded_keys, ('walk',))
        self.assertEqual(batch.remaining_keys, ('turn', 'stop'))
        second = self.launch(batch)
        self.assertEqual(second.key, 'turn')
        self.assertNotEqual(second.token, first.token)
        batch.complete(second.token, success=True)
        final = self.launch(batch)
        batch.complete(final.token, success=True)
        self.assertEqual(batch.state, queue.COMPLETED)
        self.assertFalse(batch.busy)
        self.assertEqual(batch.successes[0].result, {'revision': 1})

    def test_stale_or_duplicate_completion_cannot_commit_new_running_item(self):
        batch = self.make_queue()
        first = self.launch(batch)
        self.assertFalse(batch.complete('stale-token', success=True))
        batch.complete(first.token, success=True)
        second = self.launch(batch)
        self.assertFalse(batch.complete(first.token, success=True))
        self.assertEqual(batch.running.token, second.token)
        self.assertEqual(batch.succeeded_keys, ('walk',))

    def test_start_proof_and_result_are_frozen_and_recorded_once(self):
        data = {'clip': {'slot': 5}}
        batch = queue.SerialQueue(queue.SYNC, [queue.QueueItem('slot', data)])
        data['clip']['slot'] = 99
        attempt = self.launch(batch)
        attempt.payload['clip']['slot'] = 88
        proof = {'content': 'authored', 'range': [1, 60], 'fps': 30, 'loop': False}
        self.assertTrue(batch.record_fingerprint(attempt.token, proof))
        proof['range'][1] = 80
        self.assertFalse(batch.record_fingerprint(attempt.token, {'content': 'later edit'}))
        result = {'path': ['returned']}
        batch.complete(attempt.token, success=True, result=result)
        result['path'].append('changed')
        saved = batch.successes[0]
        self.assertEqual(saved.payload['clip']['slot'], 5)
        self.assertEqual(saved.fingerprint['range'], [1, 60])
        self.assertEqual(saved.result, {'path': ['returned']})
        saved.fingerprint['range'][1] = 100
        self.assertEqual(batch.successes[0].fingerprint['range'], [1, 60])

    def test_sync_success_without_launch_proof_is_failed_and_not_remembered(self):
        batch = self.make_queue(queue.SYNC)
        attempt = self.launch(batch)
        self.assertTrue(batch.complete(attempt.token, success=True))
        self.assertEqual(batch.state, queue.FAILED)
        self.assertIn('fingerprint', batch.failure.message)
        self.assertEqual(batch.succeeded_keys, ())
        self.assertEqual(batch.remaining_keys, ('walk', 'turn', 'stop'))

    def test_explicit_unknown_proof_remains_unknown_in_success(self):
        batch = self.make_queue(queue.SYNC, keys=('unknown-slot',))
        attempt = self.launch(batch)
        proof = {'status': 'UNKNOWN', 'reason': 'Unsupported animated dependency'}
        batch.record_fingerprint(attempt.token, proof)
        batch.complete(attempt.token, success=True)
        self.assertEqual(batch.successes[0].fingerprint, proof)
        self.assertTrue(batch.successes[0].fingerprint_recorded)

    def test_failed_export_preserves_prior_success_and_reports_remaining(self):
        batch = self.make_queue()
        first = self.launch(batch)
        batch.complete(first.token, success=True)
        second = self.launch(batch)
        batch.complete(second.token, success=False, message='FBX exporter failed')
        self.assertEqual(batch.state, queue.FAILED)
        self.assertFalse(batch.busy)
        self.assertEqual(batch.failure.key, 'turn')
        self.assertEqual(batch.failure.message, 'FBX exporter failed')
        self.assertEqual(batch.succeeded_keys, ('walk',))
        self.assertEqual(batch.remaining_keys, ('turn', 'stop'))
        self.assertFalse(batch.tick(lambda _attempt: self.fail('Started after failure.')))

    def test_start_callback_error_stops_batch_without_marking_success(self):
        batch = self.make_queue()
        def start(_attempt):
            raise RuntimeError('Rig ownership changed')
        self.assertTrue(batch.tick(start))
        self.assertEqual(batch.state, queue.FAILED)
        self.assertEqual(batch.failure.message, 'Rig ownership changed')
        self.assertEqual(batch.succeeded_keys, ())

    def test_synchronous_callback_completion_still_starts_one_item_per_tick(self):
        batch = self.make_queue()
        calls = []
        def start(attempt):
            calls.append(attempt.key)
            batch.complete(attempt.token, success=True)
            self.assertFalse(batch.tick(start))
        self.assertTrue(batch.tick(start))
        self.assertEqual(calls, ['walk'])
        self.assertTrue(batch.busy)
        self.assertIsNone(batch.running)

    def test_callback_error_after_synchronous_success_keeps_success_but_stops(self):
        batch = self.make_queue()
        def start(attempt):
            batch.complete(attempt.token, success=True)
            raise RuntimeError('Post-publish status failed')
        batch.tick(start)
        self.assertEqual(batch.state, queue.FAILED)
        self.assertEqual(batch.succeeded_keys, ('walk',))
        self.assertEqual(batch.remaining_keys, ('turn', 'stop'))

    def test_cancel_between_items_keeps_success_and_never_starts_pending(self):
        batch = self.make_queue()
        first = self.launch(batch)
        batch.complete(first.token, success=True)
        self.assertTrue(batch.cancel())
        self.assertEqual(batch.state, queue.CANCELLED)
        self.assertFalse(batch.busy)
        self.assertEqual(batch.succeeded_keys, ('walk',))
        self.assertEqual(batch.remaining_keys, ('turn', 'stop'))
        self.assertFalse(batch.tick(lambda _attempt: self.fail('Started after cancellation.')))

    def test_cancel_running_keeps_busy_until_matching_stop_and_ignores_late_success(self):
        batch = self.make_queue(queue.SYNC)
        attempt = self.launch(batch)
        batch.record_fingerprint(attempt.token, {'content': 'before worker'})
        calls = []
        self.assertTrue(batch.cancel(calls.append))
        self.assertFalse(batch.cancel(calls.append))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].token, attempt.token)
        self.assertEqual(batch.state, queue.CANCELLING)
        self.assertTrue(batch.busy)
        self.assertFalse(batch.acknowledge_cancel('wrong-token'))
        self.assertFalse(batch.record_fingerprint(attempt.token, {'content': 'after cancel'}))
        self.assertTrue(batch.complete(attempt.token, success=True, result='worker output'))
        self.assertEqual(batch.state, queue.CANCELLED)
        self.assertFalse(batch.busy)
        self.assertEqual(batch.succeeded_keys, ())
        self.assertEqual(batch.remaining_keys, ('walk', 'turn', 'stop'))

    def test_cancel_callback_failure_does_not_pretend_worker_stopped(self):
        batch = self.make_queue()
        attempt = self.launch(batch)
        def cancel(_attempt):
            raise OSError('Cannot signal worker')
        batch.cancel(cancel)
        self.assertEqual(batch.cancel_error, 'Cannot signal worker')
        self.assertEqual(batch.state, queue.CANCELLING)
        self.assertTrue(batch.busy)
        self.assertTrue(batch.acknowledge_cancel(attempt.token, message='Worker exited later'))
        self.assertEqual(batch.cancel_message, 'Worker exited later')
        self.assertFalse(batch.busy)

    def test_cancelled_sync_retains_confirmed_publication_but_stops_later_items(self):
        batch = self.make_queue(queue.SYNC)
        attempt = self.launch(batch)
        proof = {'content': 'published input', 'slot': 2, 'range': [1, 60]}
        batch.record_fingerprint(attempt.token, proof)
        batch.cancel()
        batch.complete(attempt.token, success=True, published=True,
                       result={'link_manifest': 'committed_link.json'})
        self.assertEqual(batch.state, queue.CANCELLED)
        self.assertFalse(batch.busy)
        self.assertEqual(batch.succeeded_keys, ('walk',))
        self.assertEqual(batch.remaining_keys, ('turn', 'stop'))
        self.assertEqual(batch.successes[0].fingerprint, proof)
        self.assertFalse(batch.tick(lambda _attempt: self.fail('Started after cancellation.')))

    def test_cancelled_add_keeps_synchronous_committed_result(self):
        batch = self.make_queue()
        def start(attempt):
            batch.cancel()
            batch.complete(attempt.token, success=True, published=True,
                           result={'item_id': 'new local worklist item'})
        batch.tick(start)
        self.assertEqual(batch.state, queue.CANCELLED)
        self.assertEqual(batch.succeeded_keys, ('walk',))
        self.assertEqual(batch.remaining_keys, ('turn', 'stop'))

    def test_cancelled_publication_without_sync_proof_is_not_recorded_as_success(self):
        batch = self.make_queue(queue.SYNC)
        attempt = self.launch(batch)
        batch.cancel()
        batch.complete(attempt.token, success=True, published=True)
        self.assertEqual(batch.state, queue.FAILED)
        self.assertEqual(batch.succeeded_keys, ())
        self.assertEqual(batch.remaining_keys, ('walk', 'turn', 'stop'))

    def test_cancel_callback_can_confirm_stop_synchronously(self):
        batch = self.make_queue()
        attempt = self.launch(batch)
        self.assertFalse(batch.acknowledge_cancel(attempt.token))
        batch.cancel(lambda active: batch.acknowledge_cancel(active.token))
        self.assertEqual(batch.state, queue.CANCELLED)
        self.assertFalse(batch.busy)

    def test_cancelling_a_queued_or_terminal_batch_does_not_signal_a_worker(self):
        batch = self.make_queue()
        calls = []
        self.assertTrue(batch.cancel(calls.append))
        self.assertFalse(batch.cancel(calls.append))
        self.assertEqual(calls, [])
        self.assertEqual(batch.remaining_keys, ('walk', 'turn', 'stop'))
        finished = self.make_queue(keys=())
        self.assertFalse(finished.cancel(calls.append))


if __name__ == '__main__':
    unittest.main()
