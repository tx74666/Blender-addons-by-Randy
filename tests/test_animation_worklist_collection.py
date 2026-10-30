"""Serial collection ownership/publication contracts without Blender workers."""

from contextlib import contextmanager
from copy import deepcopy
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class NativeAction(SimpleNamespace):
    def get(self, key, default=None):
        return default  # These plain legacy fixtures have no native ID properties.


class Timers:
    def __init__(self):
        self.registered = set()
        self.unregistered = []

    def register(self, callback, **_options):
        self.registered.add(callback)

    def unregister(self, callback):
        self.registered.discard(callback)
        self.unregistered.append(callback)

    def is_registered(self, callback):
        return callback in self.registered


class Context:
    def __init__(self, window, manager):
        self.window = window
        self.window_manager = manager

    @property
    def scene(self):
        return self.window.scene

    @contextmanager
    def temp_override(self, *, window):
        previous = self.window
        self.window = window
        try:
            yield self
        finally:
            self.window = previous


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cd-worklist-collection-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.manifest = self.folder / 'character_animation_link.json'
        self.manifest.write_text('{}', encoding='utf-8')
        self.fbx = self.folder / 'Custom.fbx'
        self.metadata = self.folder / 'Custom.animation.json'
        self.fbx.write_bytes(b'published native FBX')
        self.metadata.write_bytes(b'published native metadata')
        self.proof = {'schema': 'character-designer.animation-worklist-fingerprint/1',
                      'fingerprint_known': True, 'fingerprint': 'a' * 64,
                      'proof_reason': 'Complete plain FK inputs.'}
        self.active_job = None
        self.rig = SimpleNamespace(animation_data=SimpleNamespace(action=object()))
        self.item = self.make_item('walk')
        self.saved = self.make_state([self.item])
        self.original_scene = SimpleNamespace(name='Artist Scene', character_designer_animation_worklist=self.saved)
        self.area = SimpleNamespace(type='VIEW_3D', tag_redraw=Mock())
        self.window = SimpleNamespace(scene=self.original_scene,
                                      screen=SimpleNamespace(areas=[self.area]))
        self.manager = SimpleNamespace(windows=[self.window])
        self.context = Context(self.window, self.manager)
        self.timers = Timers()
        self.catalog = {'clips': [self.clip('walk')]}
        self.link = {'clip_key': 'walk', 'clipGuid': 'a' * 32, 'clipLocalId': 1, 'targetGuid': 'f' * 32,
                     'revision': 1, 'fbxFile': str(self.fbx),
                     'fbxSha256': self.digest(self.fbx),
                     'metadataFile': str(self.metadata),
                     'metadataSha256': self.digest(self.metadata)}
        self.item.link_identity = json.dumps(self.link)

        name = '_worklist_collection_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        bpy.context, bpy.app = self.context, SimpleNamespace(timers=self.timers)
        self.worklist = ModuleType(name + '.animation_worklist')
        self.worklist.state = lambda context: context.scene.character_designer_animation_worklist
        self.worklist._fresh_workspace = Mock(side_effect=lambda _saved, _item=None: deepcopy(self.catalog))
        self.worklist._clip_key = lambda clip: clip['clip_key']
        self.worklist._validate_route = Mock()
        self.worklist._check_baseline = Mock()
        self.worklist._item = lambda saved, key: next(item for item in saved.items if item.item_id == key)
        self.worklist._slot = Mock(return_value=SimpleNamespace(handle=3))
        self.worklist._encoded = lambda value: json.dumps(value, sort_keys=True)
        self.worklist.add = Mock(side_effect=self.add_item)
        self.worklist.activate = Mock(side_effect=self.activate)
        self.worklist.sync = Mock(side_effect=self.sync)
        self.worklist._idle = Mock(side_effect=self.idle)
        self.links = ModuleType(name + '.animation_link')
        self.links.load_link = Mock(side_effect=lambda _path: deepcopy(self.link))
        self.links._identity = lambda link: (link['targetGuid'], link['clip_key'],
            link.get('workItemId', ''), link.get('baseClipGuid', ''), link.get('baseClipLocalId', 0))
        self.links._sha256 = self.digest
        self.fingerprints = ModuleType(name + '.animation_worklist_fingerprint')
        self.fingerprints.SCHEMA = self.proof['schema']
        self.fingerprints.fingerprint_native = Mock(side_effect=lambda *_args: deepcopy(self.proof))
        hair = ModuleType(name + '.hair_wiggle_adapter')
        hair.status = Mock(return_value={'active': False})
        hair.stop = Mock()
        self.hair = hair
        self.animation = ModuleType(name + '.animation')
        self.animation._link_idle = Mock(side_effect=self.link_idle)
        self.animation._poll_action_export = Mock()
        self.animation._export_window_manager = None
        self.exporter = ModuleType(name + '.animation_export')
        self.exporter.active_job = lambda: self.active_job
        self.exporter.cancel_export = Mock(side_effect=self.dispose)
        modules = patch.dict(sys.modules, {'bpy': bpy, name: package,
            self.worklist.__name__: self.worklist, self.links.__name__: self.links,
            self.fingerprints.__name__: self.fingerprints,
            hair.__name__: hair,
            self.animation.__name__: self.animation, self.exporter.__name__: self.exporter})
        modules.start()
        self.addCleanup(modules.stop)
        for module in (self.worklist, self.links, self.fingerprints, hair, self.animation, self.exporter):
            setattr(package, module.__name__.rsplit('.', 1)[1], module)
        self.queue_module = self.load(name, 'animation_worklist_queue')
        self.collection = self.load(name, 'animation_worklist_collection')
        self.real_export_implementation = self.collection.export_implementation
        self.implementation = {file: '1' * 64 for file in (
            'animation_export.py', 'animation_export_worker.py', 'unity_export_worker.py',
            'animation_worklist_fingerprint.py', 'dress_export_snapshot.py', 'dress_export_guard.py')}
        self.collection.export_implementation = Mock(side_effect=lambda: deepcopy(self.implementation))
        self.addCleanup(self.cleanup_batch)

    @staticmethod
    def load(package, short_name):
        name = package + '.' + short_name
        spec = importlib.util.spec_from_file_location(name, SOURCE / (short_name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        setattr(sys.modules[package], short_name, module)
        return module

    @staticmethod
    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def make_item(self, key):
        return SimpleNamespace(item_id='item-' + key, clip_key=key, work_item_id='', name=key,
            custom_action=NativeAction(library=None), custom_slot=3, rig=self.rig,
            manifest_path=str(self.manifest), source_hash='saved source baseline',
            link_identity='', last_synced_receipt='', scan_state='NOT_SCANNED',
            scan_reason='', sync_selected=False, side='CUSTOM')

    def make_state(self, items):
        return SimpleNamespace(items=items, rig=self.rig, active_index=0,
            scan_completed=False, collection_status='', status='', has_error=False)

    def clip(self, key, *, prepared=True, stale=False):
        return {'clip_key': key, 'clipGuid': 'a' * 32, 'clipLocalId': 1, 'needsRebuild': stale,
                'linkManifest': str(self.manifest) if prepared else ''}

    def idle(self, _context, *, _collection=False):
        if not self.link_idle(include_collection=not _collection):
            raise ValueError('Finish the current animation or collection first.')

    def link_idle(self, *, include_collection=True):
        return self.active_job is None and (not include_collection or not self.collection.running())

    def add_item(self, context, key, *, activate_new, _collection):
        saved = self.worklist.state(context)
        item = self.make_item(key)
        item.link_identity = json.dumps(self.link)
        saved.items.append(item)
        return item

    def activate(self, context, item_id, side, *, _collection):
        saved = self.worklist.state(context)
        item = self.worklist._item(saved, item_id)
        saved.active_index = saved.items.index(item)
        item.side = side
        item.rig.animation_data.action = item.custom_action

    def sync(self, context, *, _collection, _collection_proof=None):
        saved = self.worklist.state(context)
        item = saved.items[saved.active_index]
        proof = self.collection.item_fingerprint(context, item)
        implementation = self.collection.export_implementation()
        if _collection_proof is not None and (not _collection or not _collection_proof(proof)):
            raise ValueError('The collection launch was cancelled or lost its fingerprint owner.')
        self.active_job = {'_worklist_scene': context.scene,
                           '_worklist_item_id': item.item_id,
                           '_worklist_fingerprint': proof,
                           '_worklist_export_implementation': implementation}
        self.animation._export_window_manager = context.window_manager
        self.timers.register(self.animation._poll_action_export)
        return self.active_job

    def real_sync(self):
        """Execute the production launch function with only native boundaries replaced."""
        path = SOURCE / 'animation_worklist.py'
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'sync')
        rigging_dispatch = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                                and node.name == '_rigging_module')
        item_workspace = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                              and node.name == '_item_workspace')
        namespace = dict(__name__=self.worklist.__name__,
            __package__=self.worklist.__name__.rsplit('.', 1)[0],
            bpy=sys.modules['bpy'], Path=Path, json=json, links=self.links,
            _idle=self.worklist._idle, state=self.worklist.state,
            current=lambda context: self.worklist.state(context).items[
                self.worklist.state(context).active_index],
            _check_baseline=self.worklist._check_baseline,
            _fresh_workspace=self.worklist._fresh_workspace,
            _clip_key=self.worklist._clip_key, _validate_route=self.worklist._validate_route,
            _association=Mock())
        exec(compile(ast.Module(body=[item_workspace, rigging_dispatch, function], type_ignores=[]), str(path), 'exec'), namespace)
        ui_path = SOURCE / 'animation.py'
        track = next(node for node in ast.parse(ui_path.read_bytes()).body
                     if isinstance(node, ast.FunctionDef) and node.name == '_track_action_export')
        self.animation.__dict__.update(
            __package__=self.animation.__name__.rsplit('.', 1)[0], bpy=sys.modules['bpy'])
        exec(compile(ast.Module(body=[track], type_ignores=[]), str(ui_path), 'exec'),
             self.animation.__dict__)
        def begin_export(_context, _rig):
            self.active_job = {}
            return self.active_job
        self.links.begin_linked_export = Mock(side_effect=begin_export)
        return namespace['sync'], namespace['_association']

    def dispose(self, job):
        if self.active_job is job:
            self.active_job = None

    def cleanup_batch(self):
        batch = self.collection._batch
        if batch is not None:
            self.active_job = batch['job']
            self.collection.stop()

    def publication(self):
        return {'link_manifest': str(self.manifest), 'revision': self.link['revision'],
                'fbx_sha256': self.link['fbxSha256'], 'filepath': str(self.fbx)}

    def ordinary_job(self):
        return {'_worklist_scene': self.context.scene, '_worklist_item_id': self.item.item_id,
                '_worklist_fingerprint': deepcopy(self.proof),
                '_worklist_export_implementation': deepcopy(self.implementation)}

    def publish_receipt(self):
        self.assertEqual(self.collection.export_finished(self.ordinary_job(), self.publication()), '')
        self.assertTrue(self.item.last_synced_receipt)

    def begin_sync(self):
        self.item.scan_state, self.item.sync_selected = 'CHANGED', True
        self.saved.scan_completed = True
        queue = self.collection.begin_sync_changed(self.context)
        self.assertEqual(self.collection._poll_collection(), 0.25)
        self.assertIsNotNone(queue.running)
        return queue, self.collection._batch['job']

    def test_add_ready_skips_existing_unprepared_and_stale_clips_without_activation(self):
        self.catalog['clips'] = [self.clip('walk'), self.clip('turn'),
                                 self.clip('unprepared', prepared=False), self.clip('stale', stale=True)]
        before_action = self.rig.animation_data.action
        queue = self.collection.begin_add_ready(self.context)
        self.assertEqual(queue.remaining_keys, ('turn',))
        self.assertIsNone(self.collection._poll_collection())
        self.worklist.add.assert_called_once_with(self.context, 'turn', activate_new=False, _collection=True)
        self.worklist.activate.assert_not_called()
        self.assertIs(self.rig.animation_data.action, before_action)
        self.assertEqual([item.clip_key for item in self.saved.items], ['walk', 'turn'])
        self.assertIn('Added 1 / 1; Completed.', self.saved.collection_status)
        self.assertIn('Used animation scene "Artist Scene".', self.saved.collection_status)

    def test_first_add_hands_ownership_to_one_new_worklist_scene(self):
        self.saved.items = []
        self.catalog['clips'] = [self.clip('walk'), self.clip('turn')]
        artist_action = self.rig.animation_data.action
        first_scene = SimpleNamespace(name='Animation Worklist · Cosha',
                                      character_designer_animation_worklist=self.make_state([]))
        def add(context, key, **kwargs):
            if context.scene is self.original_scene:
                context.window.scene = first_scene
            return self.add_item(context, key, **kwargs)
        self.worklist.add.side_effect = add
        queue = self.collection.begin_add_ready(self.context)
        self.assertEqual(self.collection._poll_collection(), 0.25)
        self.assertIs(self.collection._batch['scene'], first_scene)
        self.assertEqual(queue.succeeded_keys, ('walk',))
        self.assertTrue(queue.busy)
        self.assertIsNone(self.collection._poll_collection())
        self.assertEqual(queue.succeeded_keys, ('walk', 'turn'))
        self.assertEqual(self.saved.items, [])
        self.assertEqual([item.clip_key for item in first_scene.character_designer_animation_worklist.items],
                         ['walk', 'turn'])
        self.worklist.activate.assert_not_called()
        self.assertIs(self.rig.animation_data.action, artist_action)
        self.assertIn('Added 2 / 2; Completed.', first_scene.character_designer_animation_worklist.collection_status)
        self.assertIn('Created animation scene "Animation Worklist · Cosha".',
                      first_scene.character_designer_animation_worklist.collection_status)

    def test_add_without_saved_rig_reports_reused_scene_when_owned_rig_is_adopted(self):
        self.saved.rig = None
        self.catalog['clips'].append(self.clip('turn'))
        self.collection.begin_add_ready(self.context)
        self.assertIsNone(self.collection._poll_collection())
        self.assertIn('Used animation scene "Artist Scene".', self.saved.collection_status)
        self.assertNotIn('Created animation scene', self.saved.collection_status)

    def test_partial_add_cancel_keeps_count_state_and_actual_scene_feedback(self):
        self.catalog['clips'].extend([self.clip('turn'), self.clip('jump')])
        queue = self.collection.begin_add_ready(self.context)
        self.assertEqual(self.collection._poll_collection(), 0.25)
        self.assertTrue(self.collection.cancel(message='Artist stopped this import.'))
        self.assertIsNone(self.collection._poll_collection())
        self.assertEqual(queue.succeeded_keys, ('turn',))
        self.assertIn('Added 1 / 2; Cancelled.', self.saved.collection_status)
        self.assertIn('Used animation scene "Artist Scene".', self.saved.collection_status)
        self.assertFalse(queue.cancel_message)  # Existing between-item cancellation has no worker message.

    def test_failed_first_add_does_not_claim_a_scene_was_created_or_used(self):
        self.catalog['clips'].append(self.clip('turn'))
        self.worklist.add.side_effect = ValueError('The linked model changed.')
        queue = self.collection.begin_add_ready(self.context)
        self.assertIsNone(self.collection._poll_collection())
        self.assertEqual(queue.succeeded_keys, ())
        self.assertEqual(self.saved.collection_status, 'Added 0 / 1; Failed. The linked model changed.')

    def test_sync_completion_feedback_remains_unchanged(self):
        queue, job = self.begin_sync()
        self.dispose(job)
        self.assertEqual(self.collection.export_finished(job, self.publication()), '')
        self.assertIsNone(self.collection._poll_collection())
        self.assertEqual(queue.succeeded_keys, ('item-walk',))
        self.assertEqual(self.saved.collection_status, 'Synced 1 / 1; Completed.')

    def test_deleted_scene_after_successful_add_does_not_strand_batch(self):
        class DeletedScene:
            @property
            def name(self):
                raise ReferenceError('Scene was removed.')

            @property
            def character_designer_animation_worklist(self):
                raise ReferenceError('Scene was removed.')

        self.catalog['clips'].append(self.clip('turn'))
        queue = self.collection.begin_add_ready(self.context)
        batch = self.collection._batch
        queue.tick(lambda attempt: self.collection._start(self.context, batch, attempt))
        self.assertFalse(queue.busy)
        self.assertEqual(queue.succeeded_keys, ('turn',))
        self.assertEqual([item.clip_key for item in self.saved.items], ['walk', 'turn'])
        batch['scene'] = DeletedScene()
        self.assertIsNone(self.collection._poll_collection())
        self.assertIsNone(self.collection._batch)
        self.assertFalse(self.collection.running())

    def test_selected_three_request_ignores_historical_ready_and_preserves_existing_custom(self):
        ids = [uuid.uuid4().hex for _ in range(5)]
        self.catalog['clips'] = [{**self.clip(key), 'workItemId': value}
                                  for key, value in zip(('walk', 'turn', 'jump', 'old', 'other'), ids)]
        self.catalog.update(prepareRequestId=uuid.uuid4().hex, preparedWorkItemIds=ids[:3])
        self.worklist._clip_key = self.collection.workspace.row_key
        self.item.clip_key, self.item.work_item_id = 'work:' + ids[0], ids[0]
        original = self.item.custom_action
        queue = self.collection.begin_add_ready(self.context)
        self.assertEqual(queue.remaining_keys, tuple('work:' + value for value in ids[1:3]))
        self.collection._poll_collection()
        self.collection._poll_collection()
        self.assertEqual([item.clip_key for item in self.saved.items], ['work:' + value for value in ids[:3]])
        self.assertIs(self.saved.items[0].custom_action, original)
        self.worklist.activate.assert_not_called()

    def test_explicit_empty_request_and_new_rows_without_request_add_nothing(self):
        self.catalog['clips'] = [{**self.clip('turn'), 'workItemId': uuid.uuid4().hex}]
        for request in ({'prepareRequestId': uuid.uuid4().hex, 'preparedWorkItemIds': []}, {}):
            with self.subTest(request=request):
                self.catalog.pop('prepareRequestId', None)
                self.catalog.pop('preparedWorkItemIds', None)
                self.catalog.update(request)
                queue = self.collection.begin_add_ready(self.context)
                self.assertEqual(queue.total, 0)
                self.assertIsNone(self.collection._poll_collection())
        self.worklist.add.assert_not_called()

    def test_changed_preparation_request_stops_remaining_imports(self):
        ids = [uuid.uuid4().hex for _ in range(2)]
        self.saved.items = []
        self.catalog['clips'] = [{**self.clip(key), 'workItemId': value}
                                  for key, value in zip(('walk', 'turn'), ids)]
        self.catalog.update(prepareRequestId=uuid.uuid4().hex, preparedWorkItemIds=ids)
        queue = self.collection.begin_add_ready(self.context)
        self.collection._poll_collection()
        self.catalog['prepareRequestId'] = uuid.uuid4().hex
        self.collection._poll_collection()
        self.assertEqual(queue.succeeded_keys, ('walk',))
        self.assertEqual([item.clip_key for item in self.saved.items], ['walk'])
        self.assertIn('Added 1 / 2; Failed.', self.saved.collection_status)
        self.assertIn('Used animation scene "Artist Scene".', self.saved.collection_status)
        self.assertIn('request changed', self.saved.collection_status)

    def test_partial_return_records_only_the_original_row_and_rejects_swapped_variant(self):
        self.item.work_item_id = uuid.uuid4().hex
        self.link.update(workItemId=self.item.work_item_id, baseClipGuid='b' * 32, baseClipLocalId=22)
        self.item.link_identity = json.dumps(self.link)
        second = self.make_item('turn')
        second.work_item_id = uuid.uuid4().hex
        second.last_synced_receipt = 'artist existing receipt'
        self.saved.items.append(second)
        self.publish_receipt()
        self.assertEqual(json.loads(self.item.last_synced_receipt)['workItemId'], self.item.work_item_id)
        self.assertEqual(second.last_synced_receipt, 'artist existing receipt')
        previous = self.item.last_synced_receipt
        tampered = json.loads(previous)
        tampered['workItemId'] = second.work_item_id
        self.item.last_synced_receipt = json.dumps(tampered)
        self.assertIsNone(self.collection._receipt(self.item, self.link, self.implementation))
        self.item.last_synced_receipt = previous
        self.link['workItemId'] = second.work_item_id
        self.assertIn('differs', self.collection.export_finished(self.ordinary_job(), self.publication()))
        self.assertEqual(self.item.last_synced_receipt, previous)
        self.assertEqual(second.last_synced_receipt, 'artist existing receipt')

    def test_scan_does_not_switch_action_or_evaluate_an_export(self):
        action = self.rig.animation_data.action
        self.collection.scan_changes(self.context)
        self.assertIs(self.rig.animation_data.action, action)
        self.worklist.activate.assert_not_called()
        self.worklist.sync.assert_not_called()
        self.worklist.add.assert_not_called()
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(self.item.sync_selected)

    def test_only_a_successful_matching_receipt_can_claim_unchanged(self):
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.publish_receipt()
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNCHANGED')
        self.proof['fingerprint'] = 'b' * 64
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'CHANGED')
        self.assertTrue(self.item.sync_selected)
        self.item.sync_selected = False
        self.collection.scan_changes(self.context)
        self.assertFalse(self.item.sync_selected)

    def test_prepared_baseline_skips_unchanged_a_and_selects_only_edited_b(self):
        second = self.make_item('turn')
        self.saved.items.append(second)
        for item in self.saved.items:
            item.source_action, item.source_slot = object(), 3
            item.link_identity = json.dumps(self.link)
            self.collection.remember_source_baseline(self.context, item)
        self.catalog['clips'].append(self.clip('turn'))
        self.fingerprints.fingerprint_native.side_effect = lambda action, *_args: {
            **self.proof, 'fingerprint': ('b' if action is second.custom_action else 'a') * 64}
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNCHANGED')
        self.assertFalse(self.item.sync_selected)
        self.assertEqual(second.scan_state, 'CHANGED')
        self.assertTrue(second.sync_selected)
        self.assertEqual(self.item.last_synced_receipt, '')
        self.assertEqual(second.last_synced_receipt, '')
        self.worklist.activate.assert_not_called()

    def test_unknown_source_dependencies_never_skip_a_new_or_legacy_entry(self):
        self.item.source_action, self.item.source_slot = object(), 3
        self.proof.update(fingerprint_known=False, fingerprint='', proof_reason='Unproven constraints')
        self.collection.remember_source_baseline(self.context, self.item)
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(self.item.sync_selected)
        self.assertFalse(self.collection.unchanged_proof(self.item, self.link, self.implementation, self.proof))

    def test_registered_new_c_is_changed_then_unchanged_after_its_own_complete_return(self):
        action_id, work_id = uuid.uuid4().hex, uuid.uuid4().hex
        folder = self.folder / 'new_actions' / action_id
        folder.mkdir(parents=True)
        model = self.folder / 'model.fbx'
        model.write_bytes(b'Workspace model')
        workspace_path = self.folder / 'character_animation_workspace.json'
        workspace_path.write_text('{}', encoding='utf-8')
        self.saved.workspace_path = str(workspace_path)
        self.manifest = folder / 'character_animation_new.json'
        self.link = {'schema': 'randomrealm.animation-new-action/1', 'newActionId': action_id,
            'workItemId': work_id, 'workspaceId': str(uuid.uuid4()), 'projectId': str(uuid.uuid4()),
            'targetGuid': 'a' * 32, 'targetHash': 'b' * 32, 'baseControllerGuid': 'c' * 32,
            'avatarGuid': 'd' * 32, 'avatarHash': 'e' * 32, 'workspaceTemplateLinkGuid': 'f' * 32,
            'modelFile': str(model), 'modelSha256': self.digest(model),
            'workspaceManifest': str(workspace_path), 'editScope': 'All', 'revision': 0}
        self.manifest.write_text(json.dumps(self.link), encoding='utf-8')
        self.item.new_animation, self.item.new_action_id, self.item.work_item_id = True, action_id, work_id
        self.item.manifest_path, self.item.link_identity = str(self.manifest), json.dumps(self.link)
        self.item.source_hash = ''
        self.catalog.update(self.link)
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'CHANGED')
        self.assertTrue(self.item.sync_selected)
        self.link.update(revision=1, fbxFile=str(self.fbx), fbxSha256=self.digest(self.fbx),
                         metadataFile=str(self.metadata), metadataSha256=self.digest(self.metadata))
        self.manifest.write_text(json.dumps(self.link), encoding='utf-8')
        self.assertEqual(self.collection.export_finished(self.ordinary_job(), self.publication()), '')
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNCHANGED')
        self.assertFalse(self.item.sync_selected)

    def test_invalid_later_publication_cannot_fall_back_to_source(self):
        self.item.source_action, self.item.source_slot = object(), 3
        self.collection.remember_source_baseline(self.context, self.item)
        self.item.last_synced_receipt = 'invalid later publication'
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(self.collection.unchanged_proof(self.item, self.link, self.implementation, self.proof))

    def test_real_sync_of_proven_unchanged_action_starts_no_export(self):
        self.item.source_action, self.item.source_slot = object(), 3
        self.collection.remember_source_baseline(self.context, self.item)
        self.rig.animation_data.action = self.item.custom_action
        sync, association = self.real_sync()
        with self.assertRaisesRegex(ValueError, 'unchanged'):
            sync(self.context)
        association.assert_not_called()
        self.links.begin_linked_export.assert_not_called()

    def test_actual_guard_bytes_change_implementation_and_invalidate_receipt(self):
        # Execute the actual IO fingerprint and actual receipt/scan functions;
        # only their __file__ root points to a private copy of the runtime files.
        folder = self.folder / 'implementation'
        folder.mkdir()
        names = self.real_export_implementation().keys()
        for name in names:
            if (SOURCE / name).is_file():
                (folder / name).write_bytes((SOURCE / name).read_bytes())
        # A release without Dress integration is supported. This private fixture
        # adds the pair only to exercise actual file-byte receipt invalidation.
        for name in ('dress_export_snapshot.py', 'dress_export_guard.py'):
            if not (folder / name).exists():
                (folder / name).write_bytes(b'# owned optional guard fingerprint fixture\n')
        with patch.object(self.collection, '__file__', str(folder / 'animation_worklist_collection.py')), \
                patch.object(self.collection, 'export_implementation', self.real_export_implementation):
            before = self.collection.export_implementation()
            self.implementation = before
            self.publish_receipt()
            saved_receipt = self.item.last_synced_receipt
            self.collection.scan_changes(self.context)
            self.assertEqual(self.item.scan_state, 'UNCHANGED')
            self.assertIsNotNone(self.collection._receipt(self.item, self.link, before))
            guard = folder / 'dress_export_guard.py'
            guard.write_bytes(guard.read_bytes() + b'\n# changed private guard bytes\n')
            after = self.collection.export_implementation()
            self.assertEqual([name for name in before if before[name] != after[name]], ['dress_export_guard.py'])
            self.assertIsNone(self.collection._receipt(self.item, self.link, after))
            self.collection.scan_changes(self.context)
            self.assertEqual(self.item.scan_state, 'UNKNOWN')
            self.assertEqual(self.item.last_synced_receipt, saved_receipt)
            self.worklist.activate.assert_not_called()
            self.worklist.sync.assert_not_called()

    def test_absent_optional_guards_are_bound_and_partial_install_is_rejected(self):
        folder = self.folder / 'implementation-without-dress'
        folder.mkdir()
        required = ('animation_export.py', 'animation_export_worker.py',
                    'unity_export_worker.py', 'animation_worklist_fingerprint.py')
        for name in required:
            (folder / name).write_bytes((SOURCE / name).read_bytes())
        with patch.object(self.collection, '__file__', str(folder / 'animation_worklist_collection.py')):
            before = self.real_export_implementation()
            self.assertEqual(before['dress_export_snapshot.py'], 'absent')
            self.assertEqual(before['dress_export_guard.py'], 'absent')
            (folder / 'dress_export_snapshot.py').write_bytes(b'# owned snapshot\n')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                self.real_export_implementation()
            (folder / 'dress_export_guard.py').write_bytes(b'# owned guard\n')
            after = self.real_export_implementation()
            self.assertEqual([name for name in before if before[name] != after[name]],
                             ['dress_export_snapshot.py', 'dress_export_guard.py'])
            (folder / 'animation_export.py').unlink()
            with self.assertRaises(FileNotFoundError):
                self.real_export_implementation()

    def test_missing_or_modified_published_output_cannot_claim_unchanged(self):
        self.publish_receipt()
        self.fbx.write_bytes(b'changed after publication')
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(self.item.sync_selected)

    def test_incomplete_published_proof_stays_unknown(self):
        self.proof.update(fingerprint_known=False, fingerprint='', proof_reason='Unsupported rig dependency')
        self.publish_receipt()
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertIn('Unsupported', self.item.scan_reason)
        self.assertFalse(self.item.sync_selected)

    def test_changed_export_implementation_invalidates_unchanged_receipt(self):
        self.publish_receipt()
        self.implementation['animation_export_worker.py'] = '2' * 64
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(self.item.sync_selected)

    def test_malformed_known_fingerprint_receipt_cannot_claim_unchanged(self):
        self.publish_receipt()
        receipt = json.loads(self.item.last_synced_receipt)
        receipt['proof']['fingerprint'] = 'not a SHA256 proof'
        self.item.last_synced_receipt = json.dumps(receipt)
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')

    def test_non_dictionary_receipt_proof_is_unknown_without_crashing_scan(self):
        self.publish_receipt()
        receipt = json.loads(self.item.last_synced_receipt)
        receipt['proof'] = []
        self.item.last_synced_receipt = json.dumps(receipt)
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(self.item.sync_selected)

    def test_active_hair_preview_makes_scan_unknown_without_stopping_or_switching(self):
        self.publish_receipt()
        self.hair.status.return_value = {'active': True}
        before_action = self.rig.animation_data.action
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertIn('Active Hair preview', self.item.scan_reason)
        self.assertFalse(self.item.sync_selected)
        self.assertIs(self.rig.animation_data.action, before_action)
        self.assertTrue(self.proof['fingerprint_known'])
        self.hair.stop.assert_not_called()
        self.worklist.activate.assert_not_called()
        self.worklist.sync.assert_not_called()

    def test_unsupported_export_channels_never_produce_complete_unchanged_proof(self):
        result = self.publication()
        result['unsupported_channels'] = ['pose.bones["Jaw"].custom_value']
        self.collection.export_finished(self.ordinary_job(), result)
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(json.loads(self.item.last_synced_receipt)['proof']['fingerprint_known'])

    def test_unknown_transition_cannot_inherit_automatic_changed_selection(self):
        self.publish_receipt()
        self.proof['fingerprint'] = 'b' * 64
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'CHANGED')
        self.assertTrue(self.item.sync_selected)
        self.proof.update(fingerprint_known=False, fingerprint='', proof_reason='New unsupported constraint')
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(self.item.sync_selected)
        self.item.sync_selected = True  # The artist acknowledges Unknown explicitly.
        self.collection.scan_changes(self.context)
        self.assertTrue(self.item.sync_selected)

    def test_sync_queue_contains_only_selected_changed_or_explicit_unknown_items(self):
        self.saved.scan_completed = True
        self.item.scan_state = 'UNKNOWN'
        second = self.make_item('turn')
        second.scan_state, second.sync_selected = 'CHANGED', True
        self.saved.items.append(second)
        queue = self.collection.begin_sync_changed(self.context)
        self.assertEqual(queue.remaining_keys, ('item-turn',))
        self.collection.cancel()
        self.collection._poll_collection()
        self.item.sync_selected = True
        second.scan_state = 'BLOCKED'
        queue = self.collection.begin_sync_changed(self.context)
        self.assertEqual(queue.remaining_keys, ('item-walk',))

    def test_automatic_export_includes_fresh_changes_despite_old_selection(self):
        self.publish_receipt()
        self.proof['fingerprint'] = 'b' * 64
        for old_state in ('CHANGED', 'UNKNOWN', 'BLOCKED'):
            with self.subTest(old_state=old_state):
                self.item.scan_state, self.item.sync_selected = old_state, False
                self.collection.scan_changes(self.context)
                self.assertEqual(self.item.scan_state, 'CHANGED')
                queue = self.collection.begin_sync_changed(self.context, changed_only=True)
                self.assertEqual(queue.remaining_keys, ('item-walk',))
                self.collection.cancel()
                self.collection._poll_collection()

    def test_automatic_export_excludes_unknown_blocked_and_unchanged_even_if_selected(self):
        self.saved.scan_completed = True
        self.item.scan_state, self.item.sync_selected = 'CHANGED', False
        for key, state in (('uncertain', 'UNKNOWN'), ('blocked', 'BLOCKED'), ('same', 'UNCHANGED')):
            item = self.make_item(key)
            item.scan_state, item.sync_selected = state, True
            self.saved.items.append(item)
        previous_items = list(self.saved.items)
        queue = self.collection.begin_sync_changed(self.context, changed_only=True)
        self.assertEqual(queue.remaining_keys, ('item-walk',))
        self.collection.cancel()
        self.collection._poll_collection()
        self.assertIn('2 animation(s) need attention', self.saved.collection_status)
        self.assertEqual(self.saved.items, previous_items)
        self.worklist.sync.assert_not_called()

    def test_automatic_export_without_proven_changes_starts_no_worker_and_explains_unknown(self):
        self.saved.scan_completed = True
        self.item.scan_state, self.item.sync_selected = 'UNKNOWN', True
        queue = self.collection.begin_sync_changed(self.context, changed_only=True)
        self.assertFalse(queue.busy)
        self.assertIn('Nothing ready to export.', self.saved.collection_status)
        self.assertIn('1 animation(s) need attention', self.saved.collection_status)
        self.assertTrue(self.saved.collection_feedback)
        self.worklist.sync.assert_not_called()
        self.item.scan_state = 'UNCHANGED'
        queue = self.collection.begin_sync_changed(self.context, changed_only=True)
        self.assertFalse(queue.busy)
        self.assertEqual(self.saved.collection_status, 'No changes to export.')
        self.worklist.sync.assert_not_called()

    def test_automatic_export_rechecks_complete_comparison_before_worker_launch(self):
        for invalidation in ('unknown_inputs', 'lost_receipt', 'reverted_edits'):
            with self.subTest(invalidation=invalidation):
                self.proof.update(fingerprint_known=True, fingerprint='a' * 64)
                self.publish_receipt()
                previous_receipt = self.item.last_synced_receipt
                self.proof['fingerprint'] = 'b' * 64
                self.collection.scan_changes(self.context)
                self.assertEqual(self.item.scan_state, 'CHANGED')
                queue = self.collection.begin_sync_changed(self.context, changed_only=True)
                sync, association = self.real_sync()
                self.worklist.sync.side_effect = sync
                if invalidation == 'unknown_inputs':
                    self.proof.update(fingerprint_known=False, fingerprint='', proof_reason='Unsupported input')
                elif invalidation == 'lost_receipt':
                    self.item.last_synced_receipt = '{}'
                else:
                    self.proof['fingerprint'] = 'a' * 64
                retained_receipt = self.item.last_synced_receipt
                self.assertIsNone(self.collection._poll_collection())
                association.assert_not_called()
                self.links.begin_linked_export.assert_not_called()
                self.assertEqual(queue.state, self.queue_module.FAILED)
                self.assertEqual(queue.succeeded_keys, ())
                self.assertEqual(self.item.last_synced_receipt, retained_receipt)
                self.item.last_synced_receipt = previous_receipt

    def test_automatic_export_launches_confirmed_change_with_frozen_proof(self):
        self.publish_receipt()
        self.proof['fingerprint'] = 'b' * 64
        self.collection.scan_changes(self.context)
        queue = self.collection.begin_sync_changed(self.context, changed_only=True)
        sync, association = self.real_sync()
        self.worklist.sync.side_effect = sync
        self.assertEqual(self.collection._poll_collection(), 0.25)
        association.assert_called_once()
        self.links.begin_linked_export.assert_called_once()
        self.assertEqual(self.collection._batch['job']['_worklist_fingerprint'], self.proof)

    def test_publication_uses_launch_proof_and_does_not_mark_later_edits_unchanged(self):
        queue, job = self.begin_sync()
        self.proof['fingerprint'] = 'b' * 64
        self.dispose(job)  # The native coordinator disposes before completion.
        self.collection.export_finished(job, self.publication())
        self.assertEqual(queue.succeeded_keys, ('item-walk',))
        self.collection._poll_collection()
        self.assertEqual(json.loads(self.item.last_synced_receipt)['proof']['fingerprint'], 'a' * 64)
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'CHANGED')

    def test_real_sync_hashes_once_before_launch_and_shares_that_proof_with_queue_and_receipt(self):
        sync, association = self.real_sync()
        self.worklist.sync.side_effect = sync
        events = []
        fresh_proof = deepcopy(self.proof)
        self.fingerprints.fingerprint_native.side_effect = lambda *_args: (
            events.append('fingerprint') or deepcopy(fresh_proof))
        self.collection.export_implementation.side_effect = lambda: (
            events.append('implementation') or deepcopy(self.implementation))
        association.side_effect = lambda _item: events.append('association')
        start_export = self.links.begin_linked_export.side_effect
        self.links.begin_linked_export.side_effect = lambda *args: (
            events.append('worker') or start_export(*args))
        self.item.scan_state, self.item.sync_selected = 'CHANGED', True
        self.saved.scan_completed = True
        queue = self.collection.begin_sync_changed(self.context)
        record = queue.record_fingerprint
        with patch.object(queue, 'record_fingerprint', side_effect=lambda *args: (
                events.append('queue proof') or record(*args))) as callback:
            self.assertEqual(self.collection._poll_collection(), 0.25)
        job = self.collection._batch['job']
        self.fingerprints.fingerprint_native.assert_called_once_with(
            self.item.custom_action, self.item.rig, self.context.scene, self.item.custom_slot)
        self.assertEqual(events, ['fingerprint', 'implementation', 'queue proof', 'association', 'worker'])
        callback.assert_called_once_with(queue.running.token, job['_worklist_fingerprint'])
        self.assertEqual(job['_worklist_fingerprint'], fresh_proof)
        self.proof['fingerprint'] = 'b' * 64  # An artist edit after the snapshot froze.
        self.fingerprints.fingerprint_native.side_effect = lambda *_args: deepcopy(self.proof)
        self.dispose(job)
        self.assertEqual(self.collection.export_finished(job, self.publication()), '')
        self.collection._poll_collection()
        self.assertEqual(queue.successes[0].fingerprint, fresh_proof)
        self.assertEqual(json.loads(self.item.last_synced_receipt)['proof'], fresh_proof)
        self.collection.scan_changes(self.context)
        self.assertEqual(self.item.scan_state, 'CHANGED')

    def test_real_sync_rejected_queue_proof_starts_no_worker_and_keeps_previous_receipt(self):
        self.publish_receipt()
        previous = self.item.last_synced_receipt
        sync, association = self.real_sync()
        self.worklist.sync.side_effect = sync
        self.item.scan_state, self.item.sync_selected = 'CHANGED', True
        self.saved.scan_completed = True
        queue = self.collection.begin_sync_changed(self.context)
        with patch.object(queue, 'record_fingerprint', return_value=False) as callback:
            self.assertIsNone(self.collection._poll_collection())
        self.fingerprints.fingerprint_native.assert_called_once()
        callback.assert_called_once()
        association.assert_not_called()
        self.links.begin_linked_export.assert_not_called()
        self.assertIsNone(self.active_job)
        self.assertFalse(self.timers.is_registered(self.animation._poll_action_export))
        self.assertEqual(queue.state, self.queue_module.FAILED)
        self.assertIn('fingerprint owner', queue.failure.message)
        self.assertEqual(queue.succeeded_keys, ())
        self.assertEqual(self.item.last_synced_receipt, previous)

    def test_failed_worker_never_creates_receipt_or_advances_batch(self):
        queue, job = self.begin_sync()
        self.dispose(job)
        self.collection.export_finished(job, error='Worker failed before publication')
        self.assertEqual(self.item.last_synced_receipt, '')
        self.assertEqual(queue.succeeded_keys, ())
        self.assertEqual(queue.remaining_keys, ('item-walk',))
        self.assertEqual(queue.state, self.queue_module.FAILED)
        self.assertIsNone(self.collection._poll_collection())

    def test_disposed_worker_without_completion_fails_without_receipt_or_stranding(self):
        queue, job = self.begin_sync()
        self.dispose(job)  # External disposal supplied no successful result.
        self.assertIsNone(self.collection._poll_collection())
        self.assertEqual(queue.state, self.queue_module.FAILED)
        self.assertFalse(queue.busy)
        self.assertEqual(queue.succeeded_keys, ())
        self.assertEqual(queue.remaining_keys, ('item-walk',))
        self.assertEqual(self.item.last_synced_receipt, '')
        self.assertIn('without a publication result', queue.failure.message)
        self.assertIsNone(self.collection._batch)
        self.assertFalse(self.collection.running())
        self.exporter.cancel_export.assert_not_called()

    def test_failed_or_cancelled_sync_keeps_the_previous_successful_receipt(self):
        self.publish_receipt()
        previous = self.item.last_synced_receipt
        queue, job = self.begin_sync()
        self.dispose(job)
        self.collection.export_finished(job, error='Worker failed')
        self.collection._poll_collection()
        self.assertEqual(self.item.last_synced_receipt, previous)
        queue, job = self.begin_sync()
        self.collection.cancel()
        self.collection._poll_collection()
        self.assertEqual(self.item.last_synced_receipt, previous)
        self.assertEqual(queue.succeeded_keys, ())

    def test_timer_waits_for_worker_completion_before_activating_another_item(self):
        second = self.make_item('turn')
        second.scan_state, second.sync_selected = 'CHANGED', True
        second.link_identity = json.dumps(self.link)
        self.saved.items.append(second)
        self.catalog['clips'].append(self.clip('turn'))
        queue, job = self.begin_sync()
        for _ in range(3):
            self.assertEqual(self.collection._poll_collection(), 0.25)
        self.assertEqual(self.worklist.activate.call_count, 1)
        self.assertEqual(self.worklist.sync.call_count, 1)
        self.assertEqual(queue.succeeded_keys, ())
        self.assertEqual(queue.remaining_keys, ('item-walk', 'item-turn'))
        self.assertIs(self.collection._batch['job'], job)

    def test_timer_registration_failure_does_not_leave_busy_collection(self):
        self.catalog['clips'].append(self.clip('turn'))
        with patch.object(self.timers, 'register', side_effect=RuntimeError('Timer registration failed')):
            with self.assertRaisesRegex(RuntimeError, 'Timer registration failed'):
                self.collection.begin_add_ready(self.context)
        self.assertIsNone(self.collection._batch)
        self.assertFalse(self.collection.running())
        self.worklist.add.assert_not_called()
        self.exporter.cancel_export.assert_not_called()

    def test_cancel_disposes_only_owned_worker_without_receipt(self):
        queue, job = self.begin_sync()
        self.assertTrue(self.collection.cancel(self.context))
        self.exporter.cancel_export.assert_called_once_with(job)
        self.assertEqual(self.item.last_synced_receipt, '')
        self.assertEqual(queue.succeeded_keys, ())
        self.assertEqual(queue.state, self.queue_module.CANCELLED)
        self.assertIsNone(self.active_job)
        self.assertFalse(self.timers.is_registered(self.animation._poll_action_export))
        self.assertIsNone(self.animation._export_window_manager)
        self.assertIsNone(self.collection._poll_collection())

    def test_stale_completion_token_cannot_write_receipt_clear_job_or_advance(self):
        queue, job = self.begin_sync()
        original_token = job['_collection_token']
        job['_collection_token'] = 'stale-token'
        error = self.collection.export_finished(job, self.publication())
        self.assertTrue(error)
        self.assertEqual(self.item.last_synced_receipt, '')
        self.assertIs(self.collection._batch['job'], job)
        self.assertEqual(queue.running.token, original_token)
        self.assertEqual(queue.succeeded_keys, ())
        job['_collection_token'] = original_token

    def test_changed_owning_scene_cancels_worker_but_keeps_foreign_scene_feedback(self):
        queue, job = self.begin_sync()
        foreign = self.make_state([])
        foreign.status = 'Foreign artist feedback'
        self.window.scene = SimpleNamespace(character_designer_animation_worklist=foreign)
        self.assertIsNone(self.collection._poll_collection())
        self.exporter.cancel_export.assert_called_once_with(job)
        self.assertEqual(queue.state, self.queue_module.CANCELLED)
        self.assertEqual(foreign.status, 'Foreign artist feedback')
        self.assertIn('Cancelled', self.saved.status)

    def test_closed_owning_window_cancels_and_disposes_its_worker(self):
        queue, job = self.begin_sync()
        self.manager.windows = []
        self.assertIsNone(self.collection._poll_collection())
        self.exporter.cancel_export.assert_called_once_with(job)
        self.assertEqual(queue.state, self.queue_module.CANCELLED)
        self.assertFalse(self.collection.running())

    def test_reload_stop_disposes_owned_worker_and_unregisters_both_timers(self):
        queue, job = self.begin_sync()
        self.collection.stop()
        self.exporter.cancel_export.assert_called_once_with(job)
        self.assertEqual(queue.state, self.queue_module.CANCELLED)
        self.assertIsNone(self.collection._batch)
        self.assertFalse(self.timers.is_registered(self.collection._poll_collection))
        self.assertFalse(self.timers.is_registered(self.animation._poll_action_export))
        self.assertEqual(self.item.last_synced_receipt, '')

    def test_reload_never_disposes_a_foreign_worker_after_losing_ownership(self):
        queue, owned_job = self.begin_sync()
        foreign_job = {'owner': 'another export'}
        self.active_job = foreign_job
        self.collection.stop()
        self.exporter.cancel_export.assert_not_called()
        self.assertIs(self.active_job, foreign_job)
        self.assertTrue(queue.busy)
        self.assertIn('owner', queue.cancel_error)
        self.active_job = owned_job  # Restore the substitute for safe test cleanup.
        # A repeated cancel does not call a worker twice; explicitly confirm its
        # shutdown in this test process before module cleanup.
        self.dispose(owned_job)
        queue.acknowledge_cancel(queue.running.token)
        self.collection._poll_collection()


if __name__ == '__main__':
    unittest.main()
