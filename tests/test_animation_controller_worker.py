"""Pure ownership and publication-boundary checks for the synchronous worker."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_controller_worker.py'


class ControllerWorker(unittest.TestCase):
    def setUp(self):
        bpy = ModuleType('bpy')
        bpy.data = SimpleNamespace(actions=SimpleNamespace(remove=Mock()))
        self.bpy = bpy
        mock = patch.dict(sys.modules, {'bpy': bpy})
        mock.start()
        self.addCleanup(mock.stop)
        spec = importlib.util.spec_from_file_location('_controller_worker_test', SOURCE)
        self.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.worker)

    def test_successful_stop_confirms_exit_before_releasing_owner(self):
        process = Mock()
        process.poll.side_effect = [None, 0]
        job = dict(root=Path('owned-job'), process=process, log=Mock())
        self.worker._ACTIVE = job
        self.worker._stop(job)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        job['log'].close.assert_called_once_with()
        self.assertIsNone(self.worker._ACTIVE)

    def test_failed_stop_retains_live_owner_and_log(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('owned', 5)
        job = dict(root=Path('owned-job'), process=process, log=Mock())
        self.worker._ACTIVE = job
        with self.assertRaises(subprocess.TimeoutExpired):
            self.worker._stop(job)
        process.kill.assert_called_once_with()
        self.assertIs(self.worker._ACTIVE, job)
        job['log'].close.assert_not_called()

    def test_append_witness_allows_only_dirty_flag_change(self):
        before = dict(dirty=False, portable='proof', rigs=['held pose'], scene=42)
        after = dict(before, dirty=True)
        self.assertFalse(self.worker._same_witness(before, after))
        self.assertTrue(self.worker._same_witness(before, after, appended=True))
        after['rigs'] = ['changed pose']
        self.assertFalse(self.worker._same_witness(before, after, appended=True))

    def test_output_cleanup_never_removes_original_or_in_use_action(self):
        original = Mock()
        self.worker._remove_output(original, {original})
        self.bpy.data.actions.remove.assert_not_called()
        fresh = Mock(use_fake_user=True, users=1)
        with self.assertRaisesRegex(RuntimeError, 'gained a user'):
            self.worker._remove_output(fresh, set())
        self.bpy.data.actions.remove.assert_not_called()
        fresh.users = 0
        self.worker._remove_output(fresh, set())
        self.bpy.data.actions.remove.assert_called_once_with(fresh)

    def test_owned_receipt_replacement_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'process.json'
            self.worker._write(path, {'running': True, 'pid': 123})
            self.worker._write(path, {'running': False, 'pid': 123})
            self.assertIn('"running":false', path.read_text(encoding='utf8'))
            self.assertFalse(path.with_name('process.json.pending').exists())

    def job(self):
        self.worker._write = Mock()
        process = Mock(pid=123)
        process.poll.return_value = None
        return dict(root=Path('uncreated-owned-test-job'), process=process, cancel_requested=False,
                    started=0, phase='Creating controls', continuation=Mock())

    def test_child_progress_tracks_current_sample_and_independent_heartbeat(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(self.worker.time, 'monotonic') as tick, \
                patch.object(self.worker.time, 'time') as wall:
            root = Path(folder)
            record = root / 'progress.json'
            tick.return_value, wall.return_value = 0., 1000.
            progress = self.worker._ChildProgress(root)
            progress.report('Solve', 1, 50, 1)
            self.assertEqual(json.loads(record.read_text())['sample'], 1)
            tick.return_value, wall.return_value = .1, 1000.1
            progress.report('Solve', 2, 50, 1)
            progress.heartbeat()
            self.assertEqual(json.loads(record.read_text())['sample'], 1)
            tick.return_value, wall.return_value = 6., 1006.
            progress.heartbeat()
            held = json.loads(record.read_text())
            self.assertEqual((held['sample'], held['total'], held['round']), (2, 50, 1))
            self.assertEqual((held['updated'], held['heartbeat']), (1000.1, 1006.))
            self.assertEqual(progress.timings(), {'Solve': 6.})
            tick.return_value = 6.1
            progress.report('Check', 1, 197, 1)
            self.assertEqual(json.loads(record.read_text())['phase'], 'Check')
            self.assertAlmostEqual(progress.timings()['Solve'], 6.1)
            progress.report('Check', 1, 199, 2)
            self.assertEqual(json.loads(record.read_text())['round'], 2)

    def test_locked_progress_file_retries_latest_sample_and_keeps_nonsharing_errors_strict(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            progress = self.worker._ChildProgress(root)
            record=root/'progress.json'
            progress.report('Solve', 1, 3, 1)
            busy=PermissionError('destination open by Windows reader')
            busy.winerror=5
            with patch.object(Path,'replace',side_effect=busy):
                progress.report('Check', 1, 9, 1)
                self.assertEqual(json.loads(record.read_text())['phase'],'Solve')
                progress.written-=6.
                progress.heartbeat()
                self.assertEqual(json.loads(record.read_text())['phase'],'Solve')
            progress.report('Finish', 1, 1, 1)
            self.assertEqual(json.loads(record.read_text())['phase'], 'Finish')
            self.assertFalse((root/'progress.json.pending').exists())
            with patch.object(Path,'replace',side_effect=OSError('disk fault')), self.assertRaisesRegex(OSError,'disk fault'):
                progress.report('Store', 1, 1, 1)
            with patch.object(Path,'replace',side_effect=busy), self.assertRaises(PermissionError):
                self.worker._write(root/'result.json',{'state':'READY'})

    @unittest.skipUnless(sys.platform == 'win32', 'Windows file-sharing semantics')
    def test_real_reader_cannot_cancel_process_or_hold_result_verification(self):
        write=self.worker._write
        job=self.job()
        self.worker._write=write
        with tempfile.TemporaryDirectory() as folder, patch.object(self.worker.time,'monotonic') as tick:
            job['root']=root=Path(folder)
            record=root/'process.json'
            tick.return_value=1.
            self.assertIsNone(self.worker.poll(job))
            first=record.read_bytes()
            with record.open('rb'):
                tick.return_value=2.
                self.assertIsNone(self.worker.poll(job))
                self.assertEqual(record.read_bytes(),first)
            tick.return_value=3.
            self.assertIsNone(self.worker.poll(job))
            self.assertEqual(json.loads(record.read_bytes())['elapsed'],3.)
            with record.open('rb'):
                job['process'].poll.return_value=0
                self.assertIsNone(self.worker.poll(job))
                self.assertTrue(job['retried_exit_heartbeat'])
                self.assertEqual(self.worker.poll(job),{'state':'EXITED'})
            self.assertEqual(self.worker.poll(job),{'state':'EXITED'})
            self.assertFalse(json.loads(record.read_bytes())['running'])
            self.assertFalse((root/'process.json.pending').exists())

    def test_poll_status_io_is_throttled_and_exit_bypasses_cooldown(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(self.worker.time, 'monotonic') as tick:
            job = self.job()
            job['root'] = root = Path(folder)
            record = root / 'progress.json'
            tick.return_value = 1.
            record.write_text(json.dumps(dict(job=root.name, phase='Old worker')))
            self.assertIsNone(self.worker.poll(job))
            self.assertEqual(job['phase'], 'Old worker')
            self.assertEqual(job['progress_detail'], {})
            record.write_text(json.dumps(dict(job=root.name, phase='Solve', sample=2, total=5, round=1,
                                              heartbeat=900., updated=897.)))
            for i in range(40):
                tick.return_value = 1. + i * .02
                self.worker.poll(job)
            self.assertEqual(self.worker._write.call_count, 1)
            tick.return_value = 2.
            self.worker.poll(job)
            self.assertEqual(job['progress_detail']['sample'], 2)
            self.assertEqual(self.worker._write.call_count, 2)
            job['process'].poll.return_value = 0
            self.assertEqual(self.worker.poll(job), {'state': 'EXITED'})
            self.assertFalse(self.worker._write.call_args.args[1]['running'])
            self.assertEqual(self.worker._write.call_count, 3)

    def test_ui_cancel_never_waits_and_keeps_owner_until_exit(self):
        job = self.job()
        self.worker._ACTIVE = job
        self.worker.request_cancel(job)
        self.assertTrue(job['cancel_requested'])
        job['process'].terminate.assert_called_once_with()
        job['process'].wait.assert_not_called()
        self.assertIs(self.worker._ACTIVE, job)
        self.assertIsNone(self.worker.poll(job))
        job['continuation'].close.assert_not_called()

    def test_cancel_escalates_only_owned_child_after_grace(self):
        job = self.job()
        with patch.object(self.worker.time, 'monotonic', return_value=10):
            self.worker.request_cancel(job)
        with patch.object(self.worker.time, 'monotonic', return_value=14):
            self.worker.poll(job)
        job['process'].kill.assert_not_called()
        with patch.object(self.worker.time, 'monotonic', return_value=15):
            self.worker.poll(job)
        job['process'].kill.assert_called_once_with()
        job['process'].wait.assert_not_called()
        job['process'].poll.return_value = 0
        self.assertEqual(self.worker.poll(job)['state'], 'CANCELLED')
        job['continuation'].close.assert_called_once_with()

    def test_repeated_cancel_keeps_original_escalation_deadline(self):
        job = self.job()
        with patch.object(self.worker.time, 'monotonic', return_value=10):
            self.worker.request_cancel(job)
        with patch.object(self.worker.time, 'monotonic', return_value=14):
            self.worker.request_cancel(job)
        self.assertEqual(job['cancel_started'], 10)
        job['process'].terminate.assert_called_once_with()
        with patch.object(self.worker.time, 'monotonic', return_value=15):
            self.worker.poll(job)
        job['process'].kill.assert_called_once_with()

    def test_poll_exit_does_not_publish_and_cancel_wins_over_ready(self):
        job = self.job()
        job['process'].poll.return_value = 0
        published = []
        def transaction():
            yield job
            published.append(True)
            return 'result'
        continuation = transaction()
        next(continuation)
        job['continuation'] = continuation
        self.assertEqual(self.worker.poll(job)['state'], 'EXITED')
        self.assertEqual(published, [])
        self.worker.request_cancel(job)
        with self.assertRaisesRegex(RuntimeError, 'Cancellation wins'):
            self.worker.finish(job)
        self.assertEqual(self.worker.poll(job)['state'], 'CANCELLED')
        self.assertEqual(published, [])

    def test_only_finish_resumes_publication_once(self):
        job = self.job()
        job['process'].poll.return_value = 0
        published = []
        def transaction():
            yield job
            published.append(True)
            return 'verified result'
        job['continuation'] = transaction()
        next(job['continuation'])
        self.assertEqual(self.worker.finish(job), 'verified result')
        self.assertEqual(published, [True])

    def test_busy_scope_is_only_overlapping_source_or_target(self):
        source, target, other = object(), object(), object()
        job = dict(owners=(source, target))
        self.worker._JOBS['one'] = job
        self.assertIs(self.worker.conflicting(source, other), job)
        self.assertIsNone(self.worker.conflicting(object(), other))

    def test_parent_creation_mismatch_closes_handle_and_refuses_pid_reuse(self):
        kernel = Mock()
        self.worker._windows_process = Mock(return_value=(kernel, 123, 222))
        with patch.object(self.worker.os, 'name', 'nt'):
            with self.assertRaisesRegex(RuntimeError, 'PID was reused'):
                self.worker._parent_watch_backend(dict(pid=555, windows_creation=111))
        kernel.CloseHandle.assert_called_once_with(123)

    def test_parent_watcher_stops_cleanly_and_closes_only_its_handle(self):
        close = Mock()
        self.worker._parent_watch_backend = Mock(return_value=(lambda: (time.sleep(.001) or False), close))
        self.worker._write = Mock()
        with patch.object(self.worker.os, '_exit') as leave:
            stop = self.worker._watch_parent(dict(parent_pid=555, parent_identity=dict(pid=555)), Path('owned'))
            stop()
            leave.assert_not_called()
        close.assert_called_once_with()
        self.worker._write.assert_not_called()

    def test_parent_exit_only_self_terminates_even_if_diagnostic_write_fails(self):
        close, dead = Mock(), threading.Event()
        self.worker._parent_watch_backend = Mock(return_value=(lambda: True, close))
        self.worker._write = Mock(side_effect=OSError('Diagnostic storage failed'))
        with patch.object(self.worker.os, '_exit', side_effect=lambda code: dead.set()) as leave:
            stop = self.worker._watch_parent(dict(parent_pid=555, parent_identity=dict(pid=555)), Path('owned'))
            self.assertTrue(dead.wait(2))
            stop()
            leave.assert_called_once_with(1)
        close.assert_called_once_with()
        self.assertEqual(self.worker._write.call_args.args[0], Path('owned/parent_exit.json'))

    def test_parent_watch_setup_failure_does_not_create_a_background_thread(self):
        self.worker._parent_watch_backend = Mock(side_effect=RuntimeError('Cannot open exact parent'))
        with patch.object(self.worker.threading, 'Thread') as thread:
            with self.assertRaisesRegex(RuntimeError, 'exact parent'):
                self.worker._watch_parent(dict(parent_pid=555, parent_identity=dict(pid=555)), Path('owned'))
            thread.assert_not_called()

    def library_fixture(self, *, path='owned/controller.blend', users=0):
        class Library:
            def __init__(self):
                self.filepath = path
                self.users = users
                self.library = None
                self.use_fake_user = False
                self.use_extra_user = False
                self.parent = None
        library = Library()
        self.bpy.types = SimpleNamespace(Library=Library)
        self.bpy.path = SimpleNamespace(abspath=lambda value: value)
        self.bpy.data.libraries = Mock()
        self.bpy.data.libraries.__iter__ = Mock(return_value=iter((library,)))
        self.bpy.data.user_map = Mock(return_value={library: set()})
        return library

    def test_append_removes_only_new_exact_path_unused_library_stub(self):
        library = self.library_fixture()
        self.worker._remove_append_libraries(Path('owned/controller.blend'), set())
        self.bpy.data.libraries.remove.assert_called_once_with(library, do_unlink=True)

    def test_append_stub_intrinsic_extra_user_is_not_a_linked_dependency(self):
        library = self.library_fixture(users=1)
        library.use_extra_user = True
        self.worker._remove_append_libraries(Path('owned/controller.blend'), set())
        self.bpy.data.libraries.remove.assert_called_once_with(library, do_unlink=True)

    def test_append_preserves_preexisting_or_different_library(self):
        library = self.library_fixture()
        self.worker._remove_append_libraries(Path('owned/controller.blend'), {library})
        self.worker._remove_append_libraries(Path('different/controller.blend'), set())
        self.bpy.data.libraries.remove.assert_not_called()

    def test_append_preserves_library_with_native_users_or_linked_ids(self):
        library = self.library_fixture(users=1)
        with self.assertRaisesRegex(RuntimeError, 'still has references'):
            self.worker._remove_append_libraries(Path('owned/controller.blend'), set())
        library.users = 0
        linked = Mock(library=library)
        self.bpy.data.user_map.return_value = {library: set(), linked: set()}
        with self.assertRaisesRegex(RuntimeError, 'still has references'):
            self.worker._remove_append_libraries(Path('owned/controller.blend'), set())
        self.bpy.data.libraries.remove.assert_not_called()


if __name__ == '__main__':
    unittest.main()
