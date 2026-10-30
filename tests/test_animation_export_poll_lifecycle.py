"""Native export ownership on poll-registration faults; no Blender or workers."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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
        self.registered, self.removed = set(), []
        self.mode = 'normal'
        self.failure = RuntimeError('Native export poll timer rejected')

    def is_registered(self, callback):
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


class MemoryPath:
    def __truediv__(self, name):
        return NS(is_file=lambda: True,
                  read_text=lambda **_kwargs: json.dumps({'ok': True}) if name == 'result.json' else '')


class Action(dict):
    library = None
    name = 'Edited Custom'
    frame_range = (1., 61.)


class ExportPollLifecycleTests(unittest.TestCase):
    def setUp(self):
        package_name = '_export_poll_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = []
        self.package_name = package_name
        modules = {package_name: package}
        for name in ('animation', 'animation_export', 'animation_link',
                     'animation_worklist', 'animation_worklist_collection',
                     'animation_worklist_target', 'animation_composition'):
            module = ModuleType(package_name + '.' + name)
            module.__package__ = package_name
            modules[module.__name__] = module
            setattr(package, name, module)
            setattr(self, name, module)
        self.timers = Timers()
        self.action = Action(unity_sample_rate=60.)
        self.rig = NS(animation_data=NS(action=self.action))
        self.settings = NS(status='Previous export status', has_error=False,
                           export_result_path='Previous.fbx')
        self.saved = NS(status='Previous row status', has_error=False,
                        scan_completed=True, collection_status='')
        self.scene = NS(character_designer_animation_worklist=self.saved,
                        render=NS(fps=60, fps_base=1.))
        self.manager = NS(character_designer_animation=self.settings)
        self.context = NS(scene=self.scene, window_manager=self.manager)
        self.bpy = ModuleType('bpy')
        self.bpy.app = NS(timers=self.timers)
        self.bpy.data = NS(filepath='', objects=NS(get=lambda _name: self.rig),
                           actions=NS(get=lambda _name: self.action))
        modules['bpy'] = self.bpy
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        temporary = tempfile.TemporaryDirectory(prefix='cd-export-poll-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.item = NS(item_id='owned-row', clip_key='walk', side='CUSTOM',
            custom_action=self.action, rig=self.rig, name='Walk', workspace_path='',
            workspace_identity='', new_animation=False, manifest_path=str(self.folder / 'link.json'),
            export_rig_name='CoshaRig', link_identity='{}', last_synced_receipt='previous successful receipt')
        self.saved.items, self.saved.active_index = [self.item], 0
        self.jobs = []
        exporter = self.animation_export
        exporter.__dict__.update(subprocess=subprocess, json=json, time=time,
                                 _ACTIVE_JOB=None, _publish=Mock())
        definitions(exporter, 'animation_export.py',
                    ['AnimationExportError', 'active_job', '_dispose', 'cancel_export', 'poll_export'])
        exporter.begin_export = Mock(side_effect=self.launch)
        self.animation_link.begin_linked_export = Mock(side_effect=self.launch)
        self.animation_link.load_link = lambda _path: {}
        ui = self.animation
        ui.__dict__.update(bpy=self.bpy, _export_window_manager=None,
            _settings=lambda context: context.window_manager.character_designer_animation,
            _target=lambda _context: self.rig, _redraw=Mock())
        # Missing helper is deliberately a red test on the pre-fix production.
        definitions(ui, 'animation.py', ['_track_action_export', '_poll_action_export'])
        self.animation_worklist_target.ensure_active = lambda *_args: None
        self.animation_composition.item_spec = lambda _item: None
        collection = self.animation_worklist_collection
        collection.__dict__.update(
            export_implementation=lambda: {'implementation': 'frozen'},
            item_fingerprint=lambda *_args: {'fingerprint': 'edited'},
            unchanged_proof=lambda *_args: False,
            export_finished=Mock(return_value=''))
        worklist = self.animation_worklist
        worklist.__dict__.update(bpy=self.bpy, Path=Path, links=self.animation_link,
            _idle=lambda *_args, **_kwargs: None, state=lambda _context: self.saved,
            current=lambda _context: self.item, _check_baseline=lambda _item: None,
            _fresh_workspace=lambda *_args: {'clips': [
                {'clip_key': 'walk', 'linkManifest': self.item.manifest_path}]},
            _clip_key=lambda clip: clip['clip_key'], _validate_route=lambda *_args: None,
            _association=lambda _item: None, _item=lambda *_args: self.item,
            activate=lambda *_args, **_kwargs: None)
        definitions(worklist, 'animation_worklist.py', ['_item_workspace', '_rigging_module', 'sync'])

    def launch(self, *_args, **_kwargs):
        if self.animation_export.active_job() is not None:
            raise ValueError('Another native export is still active')
        job = {'process': Process(), 'log': Mock(), 'temporary': NS(cleanup=Mock()),
            '_dispose_callback': Mock(), 'stage': MemoryPath(), 'root': MemoryPath(),
            'started': time.monotonic()}
        self.jobs.append(job)
        self.animation_export._ACTIVE_JOB = job
        return job

    def assert_released(self, job, *, previous_owner=None):
        self.assertIsNone(self.animation_export.active_job())
        self.assertNotIn('_dispose_callback', job)
        self.assertEqual(job['process'].terminate.call_count, 1)
        job['temporary'].cleanup.assert_called_once_with()
        self.assertIs(self.animation._export_window_manager, previous_owner)
        self.assertFalse(self.timers.is_registered(self.animation._poll_action_export))
        self.assertEqual(self.item.last_synced_receipt, 'previous successful receipt')

    def execute_operator(self, class_name):
        tree = ast.parse((SOURCE / 'animation.py').read_bytes())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
        execute = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'execute')
        exec(compile(ast.Module(body=[execute], type_ignores=[]), str(SOURCE / 'animation.py'), 'exec'),
             self.animation.__dict__)
        operator = NS(rig_name='CoshaRig', action_name=self.action.name, filepath=str(self.folder / 'out.fbx'),
            frame_start=1., frame_end=61., loop=False, report=Mock())
        return self.animation.execute(operator, self.context), operator

    def new_action_sync(self):
        module = ModuleType(self.package_name + '.animation_worklist_new')
        module.__package__ = self.package_name
        self.action.name = 'Local New Action'
        lock = NS(release=Mock())
        self.new_lock = lock
        self.animation_link._LinkLock = lambda _path: lock
        module.__dict__.update(Path=Path, json=json, links=self.animation_link,
            _composition=lambda _record: None, validate=lambda *_args: {'identity': 'frozen'},
            load=lambda _path: {'identity': 'frozen'},
            _destination=lambda directory: directory / 'new.fbx', publish=Mock())
        definitions(module, 'animation_worklist_new.py', ['sync'])
        return module.sync(self.context, self.item)

    def test_normal_tracking_publishes_through_the_same_callback_and_disposes_once(self):
        job = self.launch()
        release = job['_dispose_callback']
        published = {'filepath': 'Completed.fbx', 'metadata': 'Completed.animation.json'}
        linked = {**published, 'link_manifest': 'character_animation_link.json', 'revision': 3}
        self.animation_export._publish.return_value = published
        job['_publish_callback'] = Mock(return_value=linked)
        self.animation._track_action_export(self.context, job)
        self.assertTrue(self.timers.is_registered(self.animation._poll_action_export))
        self.assertIs(self.animation._export_window_manager, self.manager)
        self.assertEqual(self.animation._poll_action_export(), .25)
        self.animation_worklist_collection.export_finished.assert_not_called()
        job['process'].returncode = 0
        self.assertIsNone(self.animation._poll_action_export())
        job['_publish_callback'].assert_called_once_with(job, published)
        release.assert_called_once_with()
        job['temporary'].cleanup.assert_called_once_with()
        job['process'].terminate.assert_not_called()
        self.animation_worklist_collection.export_finished.assert_called_once_with(job, linked, '')
        self.assertEqual(self.settings.export_result_path, linked['filepath'])
        self.assertFalse(self.settings.has_error)
        self.assertIsNone(self.animation_export.active_job())

    def test_registration_failure_cancels_owned_worker_and_restores_previous_owner(self):
        old_owner = object()
        self.animation._export_window_manager = old_owner
        job = self.launch()
        release = job['_dispose_callback']
        self.timers.mode = 'before'
        with self.assertRaises(RuntimeError) as caught:
            self.animation._track_action_export(self.context, job)
        self.assertIs(caught.exception, self.timers.failure)
        self.assert_released(job, previous_owner=old_owner)
        release.assert_called_once_with()
        self.assertEqual(self.timers.removed, [])

    def test_partial_registration_removes_only_this_new_callback(self):
        foreign = lambda: None
        self.timers.registered.add(foreign)
        job = self.launch()
        release = job['_dispose_callback']
        self.timers.mode = 'after'
        with self.assertRaises(RuntimeError) as caught:
            self.animation._track_action_export(self.context, job)
        self.assertIs(caught.exception, self.timers.failure)
        self.assert_released(job)
        release.assert_called_once_with()
        self.assertEqual(self.timers.removed, [self.animation._poll_action_export])
        self.assertEqual(self.timers.registered, {foreign})

    def stranded_generic_poll_does_not_consume_composition(self, cleanup_operation):
        old_owner = object()
        self.animation._export_window_manager = old_owner
        callback = self.animation._poll_action_export
        abandoned = self.launch()
        old_release = abandoned['_dispose_callback']
        self.timers.mode = 'after'
        cleanup_failure = RuntimeError('Native timer cleanup unavailable')
        cleanup_patch = (patch.object(self.timers, 'is_registered', side_effect=[False, cleanup_failure])
                         if cleanup_operation == 'query' else
                         patch.object(self.timers, 'unregister', side_effect=cleanup_failure))
        with cleanup_patch:
            with self.assertRaises(RuntimeError) as caught:
                self.animation._track_action_export(self.context, abandoned)
            self.assertIs(caught.exception, cleanup_failure)
        self.assertIn(callback, self.timers.registered)
        self.assertIsNone(self.animation_export.active_job())
        self.assertIs(self.animation._export_window_manager, old_owner)
        old_release.assert_called_once_with()
        self.timers.mode = 'normal'
        composition = self.launch()
        composition['composition_job'] = True
        composition['_composition_window_manager'] = self.manager
        composition['process'].returncode = 0
        publication = {'filepath': 'Composition.fbx', 'metadata': 'Composition.animation.json'}
        composition['_publish_callback'] = Mock(return_value=publication)
        self.animation_export._publish.return_value = publication
        original_job = dict(composition)
        try:
            with patch.object(self.animation_export, 'poll_export', wraps=self.animation_export.poll_export) as poll, \
                    patch.object(self.animation_export, 'cancel_export', wraps=self.animation_export.cancel_export) as cancel:
                self.assertIsNone(callback())
                poll.assert_not_called()
                cancel.assert_not_called()
            self.animation_export._publish.assert_not_called()
            composition['_publish_callback'].assert_not_called()
            composition['temporary'].cleanup.assert_not_called()
            self.animation_worklist_collection.export_finished.assert_not_called()
            self.assertIs(self.animation_export.active_job(), composition)
            self.assertEqual(composition, original_job)
            self.assertIs(self.animation._export_window_manager, old_owner)
            self.assertEqual(self.item.last_synced_receipt, 'previous successful receipt')
        finally:
            # Only in-memory fixtures are cleaned here; no native timer or
            # process exists in these tests.
            if self.animation_export.active_job() is composition:
                self.animation_export.cancel_export(composition)
            self.timers.registered.discard(callback)

    def test_cleanup_query_failure_residual_generic_poll_cannot_consume_fresh_composition(self):
        self.stranded_generic_poll_does_not_consume_composition('query')

    def test_cleanup_unregister_failure_residual_generic_poll_cannot_consume_fresh_composition(self):
        self.stranded_generic_poll_does_not_consume_composition('unregister')

    def test_composition_dedicated_poll_still_uses_its_isolated_publication_service(self):
        job = self.launch()
        job['composition_job'] = True
        job['_worklist_scene'] = self.scene
        release = job['_dispose_callback']
        publication = {'filepath': 'VerifiedComposition.fbx', 'composition_verified': True}
        service = NS(poll_isolated_export=Mock(return_value=None))
        hosts = {'animation_export': self.animation_export,
                 'animation_worklist_collection': self.animation_worklist_collection}
        self.animation_composition.__dict__.update(
            bpy=self.bpy, _host=hosts.__getitem__, _service=lambda _name: service)
        definitions(self.animation_composition, 'animation_composition.py', ['_poll_sync'])
        self.bpy.data.scenes = [self.scene]
        with patch.object(self.animation_export, 'poll_export', wraps=self.animation_export.poll_export) as generic:
            self.assertEqual(self.animation_composition._poll_sync(), .25)
            self.animation_worklist_collection.export_finished.assert_not_called()
            def complete(expected):
                self.assertIs(expected, job)
                self.animation_export._dispose(job)
                return publication
            service.poll_isolated_export.side_effect = complete
            self.assertIsNone(self.animation_composition._poll_sync())
            generic.assert_not_called()
        self.animation_export._publish.assert_not_called()
        release.assert_called_once_with()
        self.animation_worklist_collection.export_finished.assert_called_once_with(job, result=publication)
        self.assertFalse(self.saved.has_error)
        self.assertIsNone(self.animation_export.active_job())

    def test_preexisting_poll_callback_keeps_its_timer_and_owner(self):
        old_owner = object()
        self.animation._export_window_manager = old_owner
        self.timers.registered.add(self.animation._poll_action_export)
        job = self.launch()
        release = job['_dispose_callback']
        with self.assertRaises((ValueError, RuntimeError)):
            self.animation._track_action_export(self.context, job)
        self.assertTrue(self.timers.is_registered(self.animation._poll_action_export))
        self.assertEqual(self.timers.removed, [])
        self.assertIs(self.animation._export_window_manager, old_owner)
        self.assertIsNone(self.animation_export.active_job())
        release.assert_called_once_with()
        job['process'].terminate.assert_called_once_with()

    def test_timer_query_failure_keeps_unknown_existing_callback_and_disposes_only_new_job(self):
        old_owner = object()
        callback = self.animation._poll_action_export
        self.animation._export_window_manager = old_owner
        self.timers.registered.add(callback)
        job = self.launch()
        release = job['_dispose_callback']
        failure = RuntimeError('Native timer registration query failed')
        with patch.object(self.timers, 'is_registered', side_effect=failure) as query, \
                patch.object(self.timers, 'register', wraps=self.timers.register) as register:
            with self.assertRaises(RuntimeError) as caught:
                self.animation._track_action_export(self.context, job)
            self.assertIs(caught.exception, failure)
            query.assert_called_once_with(callback)
            register.assert_not_called()
        self.assertEqual(self.timers.registered, {callback})
        self.assertEqual(self.timers.removed, [])
        self.assertIs(self.animation._export_window_manager, old_owner)
        self.assertIsNone(self.animation_export.active_job())
        release.assert_called_once_with()
        job['temporary'].cleanup.assert_called_once_with()
        self.assertEqual(self.item.last_synced_receipt, 'previous successful receipt')

    def test_real_disposal_error_still_releases_active_marker_lock_and_previous_owner(self):
        old_owner = object()
        self.animation._export_window_manager = old_owner
        job = self.launch()
        release = job['_dispose_callback']
        self.timers.mode = 'before'
        cleanup_failure = RuntimeError('Private export temporary cleanup failed')
        job['temporary'].cleanup.side_effect = cleanup_failure
        with self.assertRaises(RuntimeError) as caught:
            self.animation._track_action_export(self.context, job)
        self.assertIs(caught.exception, cleanup_failure)
        self.assert_released(job, previous_owner=old_owner)
        release.assert_called_once_with()

    def test_operator_entry_points_report_timer_failure_and_release_the_worker(self):
        for name in ('CHARACTERDESIGNER_OT_animation_link_sync', 'CHARACTERDESIGNER_OT_animation_export'):
            with self.subTest(operator=name):
                self.timers.mode = 'before'
                result, operator = self.execute_operator(name)
                self.assertEqual(result, {'CANCELLED'})
                operator.report.assert_called_once_with({'ERROR'}, str(self.timers.failure))
                self.assert_released(self.jobs[-1])

    def test_new_action_failure_releases_its_publication_lock_and_can_retry(self):
        self.timers.mode = 'before'
        with self.assertRaises(RuntimeError):
            self.new_action_sync()
        self.new_lock.release.assert_called_once_with()
        self.assert_released(self.jobs[-1])
        self.timers.mode = 'normal'
        job = self.new_action_sync()
        self.assertIs(self.animation_export.active_job(), job)
        self.animation_export.cancel_export(job)
        self.new_lock.release.assert_called_once_with()

    def test_queued_sync_timer_failure_preserves_receipt_and_allows_a_fresh_retry(self):
        path = SOURCE / 'animation_worklist_queue.py'
        spec = importlib.util.spec_from_file_location(self.package_name + '.queue', path)
        queue_module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: queue_module}):
            spec.loader.exec_module(queue_module)
        collection = self.animation_worklist_collection
        collection.__dict__.update(worklist=self.animation_worklist, ADD=queue_module.ADD,
            _current_link=lambda *_args: {}, _record_launch_proof=lambda *_args: True)
        definitions(collection, 'animation_worklist_collection.py', ['_start'])
        first = queue_module.SerialQueue(queue_module.SYNC, [queue_module.QueueItem(self.item.item_id)])
        batch = {'queue': first, 'job': None}
        self.timers.mode = 'before'
        first.tick(lambda attempt: collection._start(self.context, batch, attempt))
        self.assertEqual(first.state, queue_module.FAILED)
        self.assertIsNone(batch['job'])
        self.assert_released(self.jobs[-1])
        self.timers.mode = 'normal'
        retry = queue_module.SerialQueue(queue_module.SYNC, [queue_module.QueueItem(self.item.item_id)])
        retry_batch = {'queue': retry, 'job': None}
        retry.tick(lambda attempt: collection._start(self.context, retry_batch, attempt))
        self.assertEqual(retry.state, queue_module.RUNNING)
        self.assertIs(retry_batch['job'], self.animation_export.active_job())
        self.assertEqual(retry_batch['job']['_collection_token'], retry.running.token)
        self.assertEqual(self.item.last_synced_receipt, 'previous successful receipt')
        self.animation_export.cancel_export(retry_batch['job'])


if __name__ == '__main__':
    unittest.main()
