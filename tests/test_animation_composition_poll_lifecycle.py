"""Production composition launch/disposal on poll faults; no Blender or workers."""

import ast
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def definitions(module, filename, names):
    path = SOURCE / filename
    nodes = {node.name: node for node in ast.parse(path.read_bytes()).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    missing = set(names) - nodes.keys()
    if missing:
        raise AssertionError('Missing production definitions: ' + ', '.join(sorted(missing)))
    exec(compile(ast.Module(body=[nodes[name] for name in names], type_ignores=[]),
                 str(path), 'exec'), module.__dict__)


class Timers:
    def __init__(self):
        self.registered, self.removed, self.queries = set(), [], []
        self.mode = 'normal'
        self.failure = RuntimeError('Composition poll timer rejected')
        self.query_error = None

    def is_registered(self, callback):
        self.queries.append(callback)
        if self.query_error:
            raise self.query_error
        return callback in self.registered

    def register(self, callback, **_kwargs):
        if self.mode == 'before':
            raise self.failure
        self.registered.add(callback)
        if self.mode == 'after':
            raise self.failure

    def unregister(self, callback):
        self.removed.append(callback)
        self.registered.remove(callback)


class Process:
    def __init__(self):
        self.returncode = None
        self.terminate = Mock(side_effect=self._terminate)
        self.wait = Mock(side_effect=lambda **_kwargs: self.returncode)
        self.kill = Mock()

    def _terminate(self):
        self.returncode = -15

    def poll(self):
        return self.returncode


class CompositionPollLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='composition-poll-lifecycle-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.manifest = self.folder / 'character_animation_link.json'
        self.model = self.folder / 'Cosha.fbx'
        self.model.write_bytes(b'Current actual character model')
        self.spec = {'compositionId': uuid.uuid4().hex, 'timeMode': 'Action'}
        self.record = {'linkId': str(uuid.uuid4()), 'composition': self.spec,
                       'revision': 3, 'fbxFile': str(self.folder / 'previous.fbx')}
        self.manifest.write_text(json.dumps(self.record), encoding='utf-8')
        self.previous_publication = self.manifest.read_bytes()
        package_name = '_composition_poll_' + uuid.uuid4().hex
        self.package_name = package_name
        package = ModuleType(package_name)
        package.__path__ = [str(SOURCE)]
        self.exporter = ModuleType(package_name + '.animation_export')
        self.exporter.__dict__.update(subprocess=subprocess, _ACTIVE_JOB=None)
        definitions(self.exporter, 'animation_export.py', ['active_job', '_dispose', 'cancel_export'])
        link_spec = importlib.util.spec_from_file_location(package_name + '.animation_link', SOURCE / 'animation_link.py')
        self.links = importlib.util.module_from_spec(link_spec)
        modules = {package_name: package, self.exporter.__name__: self.exporter,
                   self.links.__name__: self.links}
        patched = patch.dict(sys.modules, modules)
        patched.start()
        self.addCleanup(patched.stop)
        link_spec.loader.exec_module(self.links)
        self.native_lock = self.links._LinkLock
        self.locks, self.jobs = [], []
        self.links._LinkLock = self.lock
        self.links.load_link = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
        self.links._revision_result = Mock(return_value={'revision': 4})
        self.timers = Timers()
        self.saved = NS(status='Previous completed status', has_error=False)
        self.scene = NS(character_designer_animation_worklist=self.saved)
        self.context = NS(scene=self.scene, window_manager=object())
        self.action = NS(name='Cosha · Aim · Custom', curves=[[1., .25], [61., .75]])
        self.base = NS(name='Linked A Source', curves=[[1., 0.], [61., 1.]])
        self.rig = object()
        self.item = NS(item_id='exact-B-row', clip_key='B', name='Aim', rig=self.rig,
            new_animation=False, custom_action=self.action, custom_slot=7,
            export_rig_name='CoshaRig', manifest_path=str(self.manifest),
            model_file=str(self.model), model_sha256=self.links._sha256(self.model),
            last_synced_receipt='Previous successful composition receipt')
        self.session = {'spec': self.spec, 'base_action': self.base, 'base_slot': 8,
                        'base_hash': self.motion(self.base, 8)}
        self.proof = {'fingerprint_known': True, 'composition_metadata': {'version': 1}}
        self.collector = NS(item_fingerprint=Mock(return_value=self.proof),
            export_implementation=lambda: {'semantics': 'unchanged'},
            unchanged_proof=lambda *_args: False, export_finished=Mock())
        self.worklist = NS(_idle=Mock(), current=lambda _context: self.item,
            _check_baseline=Mock(), state=lambda _context: self.saved,
            _fresh_workspace=lambda *_args: {'clips': [{'clip_key': 'B', 'linkManifest': str(self.manifest)}]},
            _clip_key=lambda clip: clip['clip_key'], _validate_route=Mock(),
            _encoded=lambda value: json.dumps(value, sort_keys=True), _item=lambda *_args: self.item)
        self.new = NS(_destination=lambda directory: directory / (uuid.uuid4().hex + '.fbx'),
            validate=lambda *_args: self.links.load_link(self.manifest),
            load=self.links.load_link, publish=Mock())
        self.service = ModuleType(package_name + '.animation_composition_export')
        self.service._host = lambda _name: self.exporter
        definitions(self.service, 'animation_composition_export.py', ['cancel_isolated_export'])
        self.service.cancel_isolated_export = Mock(wraps=self.service.cancel_isolated_export)
        self.service.begin_isolated_export = Mock(side_effect=self.launch)
        self.service.isolate_export = self.isolate
        self.service.poll_isolated_export = Mock(return_value=None)
        hosts = {'animation_worklist': self.worklist, 'animation_link': self.links,
            'animation_worklist_collection': self.collector, 'animation_worklist_new': self.new,
            'animation_export': self.exporter}
        self.composition = ModuleType(package_name + '.animation_composition')
        self.composition.__dict__.update(Path=Path, uuid=uuid,
            bpy=NS(app=NS(timers=self.timers), data=NS(filepath='', scenes=[self.scene])),
            _host=lambda name: hosts[name], _service=lambda _name: self.service,
            item_spec=lambda _item: self.spec, active=lambda _rig: False,
            contract=NS(identity=lambda value: json.dumps(value, sort_keys=True)),
            _export_session=lambda *_args: self.session, _motion_hash=self.motion)
        definitions(self.composition, 'animation_composition.py', ['begin_sync', '_poll_sync'])
        self.addCleanup(self.clean_jobs)

    @staticmethod
    def motion(action, slot):
        return hashlib.sha256(json.dumps([action.curves, slot]).encode('utf-8')).hexdigest()

    def lock(self, path):
        native = self.native_lock(path)
        wrapper = NS(native=native, release=Mock(wraps=native.release))
        self.locks.append(wrapper)
        return wrapper

    @contextmanager
    def isolate(self, *_args):
        yield object()

    def launch(self, *_args, **_kwargs):
        if self.exporter.active_job() is not None:
            raise RuntimeError('Another export still owns the worker')
        job = {'process': Process(), 'log': Mock(), 'temporary': NS(cleanup=Mock()),
               'composition_job': True, 'composition_export': {'isolated': True}}
        self.jobs.append(job)
        self.exporter._ACTIVE_JOB = job
        return job

    def clean_jobs(self):
        if self.exporter.active_job() is not None:
            self.exporter.cancel_export(self.exporter.active_job())
        for lock in self.locks:
            lock.native.release()

    def assert_disposed(self, job):
        self.assertIsNone(self.exporter.active_job())
        self.assertNotIn('_dispose_callback', job)
        job['process'].terminate.assert_called_once_with()
        job['temporary'].cleanup.assert_called_once_with()
        self.locks[-1].release.assert_called_once_with()
        self.assertFalse(Path(str(self.manifest) + '.blender.lock').exists())
        self.assertEqual(self.manifest.read_bytes(), self.previous_publication)
        self.assertEqual(self.item.last_synced_receipt, 'Previous successful composition receipt')
        self.links._revision_result.assert_not_called()
        self.new.publish.assert_not_called()
        self.collector.export_finished.assert_not_called()
        self.assertEqual(self.action.curves, [[1., .25], [61., .75]])

    def test_registration_failure_disposes_the_owned_worker_and_real_link_lock(self):
        self.timers.mode = 'before'
        with self.assertRaises(RuntimeError) as caught:
            self.composition.begin_sync(self.context)
        self.assertIs(caught.exception, self.timers.failure)
        self.service.cancel_isolated_export.assert_called_once_with(self.jobs[-1])
        self.assert_disposed(self.jobs[-1])
        self.assertEqual(self.timers.removed, [])
        self.assertEqual(self.saved.status, 'Previous completed status')

    def test_partial_registration_removes_only_the_new_composition_poll(self):
        foreign = lambda: None
        self.timers.registered.add(foreign)
        self.timers.mode = 'after'
        with self.assertRaises(RuntimeError):
            self.composition.begin_sync(self.context)
        self.assert_disposed(self.jobs[-1])
        self.assertEqual(self.timers.registered, {foreign})
        self.assertEqual(self.timers.removed, [self.composition._poll_sync])

    def test_existing_foreign_composition_poll_is_kept_and_only_new_job_is_rejected(self):
        self.timers.registered.add(self.composition._poll_sync)
        with self.assertRaisesRegex(RuntimeError, 'registered'):
            self.composition.begin_sync(self.context)
        self.assert_disposed(self.jobs[-1])
        self.assertEqual(self.timers.registered, {self.composition._poll_sync})
        self.assertEqual(self.timers.removed, [])

    def test_unknown_timer_query_cancels_new_job_without_touching_any_existing_timer(self):
        self.timers.registered.add(self.composition._poll_sync)
        self.timers.query_error = RuntimeError('Native timer ownership query failed')
        with self.assertRaises(RuntimeError) as caught:
            self.composition.begin_sync(self.context)
        self.assertIs(caught.exception, self.timers.query_error)
        self.assert_disposed(self.jobs[-1])
        self.assertEqual(self.timers.registered, {self.composition._poll_sync})
        self.assertEqual(self.timers.removed, [])
        self.assertEqual(self.timers.queries, [self.composition._poll_sync])

    def test_cleanup_query_error_still_disposes_worker_and_link_lock(self):
        self.timers.mode = 'before'
        failure = RuntimeError('Native cleanup ownership query failed')
        with patch.object(self.timers, 'is_registered', side_effect=[False, failure]):
            with self.assertRaises(RuntimeError) as caught:
                self.composition.begin_sync(self.context)
        self.assertIs(caught.exception, failure)
        self.assert_disposed(self.jobs[-1])
        self.assertEqual(self.timers.removed, [])

    def test_private_cleanup_error_still_releases_active_marker_and_exact_link_lock(self):
        self.timers.mode = 'before'
        failure = RuntimeError('Private composition temporary cleanup failed')
        original_launch = self.service.begin_isolated_export.side_effect

        def launch(*args, **kwargs):
            job = original_launch(*args, **kwargs)
            job['temporary'].cleanup.side_effect = failure
            return job

        self.service.begin_isolated_export.side_effect = launch
        with self.assertRaises(RuntimeError) as caught:
            self.composition.begin_sync(self.context)
        self.assertIs(caught.exception, failure)
        self.assert_disposed(self.jobs[-1])

    def test_normal_launch_preserves_callbacks_proof_and_job_until_normal_disposal(self):
        job = self.composition.begin_sync(self.context)
        self.service.cancel_isolated_export.assert_not_called()
        self.assertIs(self.exporter.active_job(), job)
        self.assertEqual(self.timers.registered, {self.composition._poll_sync})
        self.assertIs(job['_worklist_scene'], self.scene)
        self.assertIs(job['_composition_window_manager'], self.context.window_manager)
        self.assertIs(job['_worklist_fingerprint'], self.proof)
        self.assertEqual(job['_worklist_item_id'], self.item.item_id)
        self.assertEqual(self.composition._poll_sync(), .25)
        self.locks[-1].release.assert_not_called()
        # Exercise the production publication closure without writing a Link.
        self.assertEqual(job['_publish_callback'](job, {'isolated_result': True}), {'revision': 4})
        self.links._revision_result.assert_called_once()
        self.exporter._dispose(job)
        self.assertIsNone(self.exporter.active_job())
        self.locks[-1].release.assert_called_once_with()
        job['temporary'].cleanup.assert_called_once_with()
        job['process'].terminate.assert_not_called()
        self.service.cancel_isolated_export.assert_not_called()
        self.assertEqual(self.manifest.read_bytes(), self.previous_publication)

    def test_new_composition_action_poll_failure_uses_the_same_disposal_without_publishing(self):
        self.item.new_animation = True
        self.timers.mode = 'before'
        with self.assertRaises(RuntimeError):
            self.composition.begin_sync(self.context)
        self.assert_disposed(self.jobs[-1])

    def test_registration_failure_preserves_publication_and_allows_explicit_retry(self):
        self.timers.mode = 'before'
        with self.assertRaises(RuntimeError):
            self.composition.begin_sync(self.context)
        first = self.jobs[-1]
        self.assert_disposed(first)
        self.timers.mode = 'normal'
        second = self.composition.begin_sync(self.context)
        self.assertIsNot(first, second)
        self.assertIs(self.exporter.active_job(), second)
        self.assertEqual(self.service.cancel_isolated_export.call_count, 1)
        self.assertEqual(self.manifest.read_bytes(), self.previous_publication)
        self.assertEqual(self.item.last_synced_receipt, 'Previous successful composition receipt')


if __name__ == '__main__':
    unittest.main()
