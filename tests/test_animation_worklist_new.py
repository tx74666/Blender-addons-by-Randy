"""Real bounded new-Action publication and provenance contracts, no worker."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class NewActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cd-new-action-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        package = '_new_action_' + uuid.uuid4().hex
        module = ModuleType(package)
        module.__path__ = [str(SOURCE)]
        sys.modules[package] = module
        for name in ('animation_workspace', 'animation_link', 'animation_worklist_new'):
            spec = importlib.util.spec_from_file_location(package + '.' + name, SOURCE / (name + '.py'))
            value = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = value
            spec.loader.exec_module(value)
            setattr(module, name, value)
            self.addCleanup(sys.modules.pop, spec.name, None)
        self.addCleanup(sys.modules.pop, package, None)
        self.new = module.animation_worklist_new
        self.action_id, self.item_id = uuid.uuid4().hex, uuid.uuid4().hex
        self.workspace = self.root / 'character_animation_workspace.json'
        self.workspace.write_text('{}', encoding='utf-8')
        self.model = self.root / 'Model.fbx'
        self.model.write_bytes(b'Verified model fixture')
        self.folder = self.root / 'new_actions' / self.action_id
        self.folder.mkdir(parents=True)
        self.manifest = self.folder / self.new.FILE_NAME
        self.record = {'schema': self.new.SCHEMA, 'newActionId': self.action_id, 'workItemId': self.item_id,
            'workspaceId': str(uuid.uuid4()), 'projectId': str(uuid.uuid4()), 'targetGuid': 'a' * 32,
            'targetHash': 'b' * 32, 'baseControllerGuid': 'c' * 32, 'avatarGuid': 'd' * 32,
            'avatarHash': 'e' * 32, 'modelFile': str(self.model), 'modelSha256': self.hash(self.model),
            'workspaceTemplateLinkGuid': 'f' * 32, 'workspaceManifest': str(self.workspace),
            'targetName': 'Cosha', 'sourceAction': 'NewC', 'editScope': 'All', 'revision': 0}
        self.new._write(self.manifest, self.record)

    @staticmethod
    def hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def result(self, label='one'):
        directory = self.folder / 'revisions'
        directory.mkdir(exist_ok=True)
        fbx = directory / (label + '.fbx')
        metadata = fbx.with_suffix('.animation.json')
        fbx.write_bytes(b'Complete playable Action fixture ' + label.encode())
        metadata.write_text(json.dumps({'fbx_sha256': self.hash(fbx), 'action': 'NewC',
                                       'channels': 'skeletal-only', 'duration': 1.0}), encoding='utf-8')
        return {'filepath': str(fbx), 'metadata': str(metadata), 'fbx_sha256': self.hash(fbx)}

    def publish(self, record, result):
        return self.new.publish(self.manifest, record, result, blend_file='', source_action='NewC')

    def test_new_c_publishes_without_inventing_source_original_or_controller_slot(self):
        result = self.result()
        returned = self.publish(self.record, result)
        saved = self.new.load(self.manifest)
        self.assertEqual(saved['newActionId'], self.action_id)
        self.assertEqual(saved['workItemId'], self.item_id)
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(returned['revision'], 1)
        self.assertEqual(saved['fbxSha256'], self.hash(Path(result['filepath'])))
        for field in ('clipGuid', 'clipLocalId', 'baseClipGuid', 'sourcePackage', 'originalClip'):
            self.assertNotIn(field, saved)
        self.assertTrue(Path(result['filepath']).is_file())

    def test_new_c_revision_keeps_the_previous_complete_export_immutable(self):
        first = self.result()
        self.publish(self.record, first)
        previous_bytes = Path(first['filepath']).read_bytes()
        record = self.new.load(self.manifest)
        second = self.result('two')
        self.publish(record, second)
        self.assertEqual(self.new.load(self.manifest)['revision'], 2)
        self.assertEqual(Path(first['filepath']).read_bytes(), previous_bytes)
        self.assertNotEqual(first['filepath'], second['filepath'])

    def test_stale_publication_cannot_advance_or_overwrite_a_newer_revision(self):
        self.publish(self.record, self.result())
        before = self.manifest.read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.publish(self.record, self.result('stale'))
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_missing_template_link_is_not_treated_as_verified_new_action(self):
        malformed = {**self.record, 'workspaceTemplateLinkGuid': ''}
        with self.assertRaisesRegex(ValueError, 'workspaceTemplateLinkGuid'):
            self.new.identity(malformed)

    def test_modified_model_unsupported_channels_and_bad_metadata_keep_revision_zero(self):
        result = self.result()
        before = self.manifest.read_bytes()
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            self.publish(self.record, {**result, 'unsupported_channels': ['bad channel']})
        Path(result['metadata']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'metadata'):
            self.publish(self.record, result)
        self.assertEqual(self.manifest.read_bytes(), before)
        result = self.result('model')
        self.model.write_bytes(b'different model')
        with self.assertRaisesRegex(ValueError, 'model changed'):
            self.publish(self.record, result)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_controller_apply_hash_change_does_not_replace_avatar_or_model_provenance(self):
        item = SimpleNamespace(manifest_path=str(self.manifest), link_identity=json.dumps(self.record),
                               new_action_id=self.action_id, work_item_id=self.item_id)
        saved = SimpleNamespace(workspace_path=str(self.workspace))
        catalog = {**self.record, 'targetHash': '0' * 32}
        self.assertEqual(self.new.validate(saved, item, catalog), self.record)
        for field in ('avatarHash', 'modelSha256', 'workspaceTemplateLinkGuid'):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'different workspace'):
                    self.new.validate(saved, item, {**catalog, field: '1' * len(catalog[field])})

    def test_register_artist_new_action_creates_one_stable_work_item_without_source(self):
        class Items(list):
            def __iter__(self):
                # Native CollectionProperty iteration may wrap the same saved
                # RNA record in a new Python object on every access.
                return iter(SimpleNamespace(**vars(item)) for item in list.__iter__(self))

            def add(self):
                item = SimpleNamespace(source_action=None, source_file='', source_hash='', export_rig_name='')
                self.append(item)
                return item
        action = SimpleNamespace(name='Artist New C', library=None, frame_range=(1.0, 24.0), use_fake_user=False)
        rig = SimpleNamespace(name='Editing Rig', type='ARMATURE', library=None,
                              animation_data=SimpleNamespace(action=action))
        saved = SimpleNamespace(rig=rig, items=Items(), workspace_path=str(self.workspace), active_index=-1)
        context = SimpleNamespace(scene=SimpleNamespace(objects={rig.name: rig}))
        package = self.new.__package__
        worklist = ModuleType(package + '.animation_worklist')
        worklist._idle = lambda _context: None
        worklist.state = lambda _context: saved
        worklist._fresh_workspace = lambda _saved: {**self.record, 'clips': [{'linkManifest': 'verified fixture'}]}
        worklist._clip_key = lambda _clip: 'verified template'
        worklist._prepared = lambda _saved, _key: (None, None, None, None, 'VerifiedModelRig', self.model)
        worklist._slot = lambda _action: SimpleNamespace(handle=9)
        worklist._encoded = lambda value: json.dumps(value)
        bpy = ModuleType('bpy')
        with patch.dict(sys.modules, {'bpy': bpy, worklist.__name__: worklist}), \
                patch.object(sys.modules[package], 'animation_worklist', worklist, create=True):
            first = self.new.register(context)
            second = self.new.register(context)
        self.assertIsNot(first, second)
        self.assertEqual(first.item_id, second.item_id)
        self.assertEqual(first.manifest_path, second.manifest_path)
        self.assertEqual(saved.active_index, 0)
        self.assertEqual(len(saved.items), 1)
        self.assertTrue(first.new_animation)
        self.assertIs(first.custom_action, action)
        self.assertIsNone(first.source_action)
        self.assertEqual(first.source_file, '')
        self.assertEqual(first.source_hash, '')
        self.assertEqual(first.custom_slot, 9)
        self.assertEqual(self.new.load(first.manifest_path)['workItemId'], first.work_item_id)
        self.assertTrue(action.use_fake_user)

    def test_short_immutable_export_labels_retry_collisions_without_shortening_ids(self):
        directory = self.folder / 'revisions'
        directory.mkdir()
        first = uuid.UUID('1' * 32)
        second = uuid.UUID('2' * 32)
        occupied = directory / ('a-' + first.hex[:16] + '.fbx')
        occupied.write_bytes(b'Keep existing immutable revision')
        with patch.object(self.new.uuid, 'uuid4', side_effect=[first, second]):
            selected = self.new._destination(directory)
        self.assertNotEqual(selected, occupied)
        self.assertEqual(occupied.read_bytes(), b'Keep existing immutable revision')
        self.assertEqual(self.new.load(self.manifest)['newActionId'], self.action_id)
        self.assertEqual(self.new.load(self.manifest)['workItemId'], self.item_id)
        with patch.object(self.new.uuid, 'uuid4', return_value=first):
            with self.assertRaisesRegex(ValueError, 'unique immutable'):
                self.new._destination(directory)


if __name__ == '__main__':
    unittest.main()
