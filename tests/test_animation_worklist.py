"""Pure worklist state/guard tests; no Blender, model import or export worker."""

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


class ID(dict):
    __hash__ = object.__hash__

    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


class Collection(list):
    def add(self):
        item = SimpleNamespace()
        self.append(item)
        return item

    def move(self, old, new):
        self.insert(new, self.pop(old))

    def remove(self, index):
        del self[index]


class WorklistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cd-worklist-state-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        name = '_worklist_state_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda path: path))
        ua = ModuleType(name + '.unity_animation')
        ua._set_playing = lambda *_args: None
        source = ModuleType(name + '.animation_link_source')
        modules = patch.dict(sys.modules, {name: package, 'bpy': bpy,
            ua.__name__: ua, source.__name__: source})
        modules.start()
        self.addCleanup(modules.stop)

        def load(module_name):
            spec = importlib.util.spec_from_file_location(name + '.' + module_name, SOURCE / (module_name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            setattr(package, module_name, module)
            return module

        self.links = load('animation_link')
        self.workspace = load('animation_workspace')
        self.worklist = load('animation_worklist')
        self.saved = SimpleNamespace(items=Collection(), catalog=Collection(), rig=None,
            active_index=-1, catalog_index=-1, workspace_path='', workspace_identity='',
            status='', has_error=False)
        self.context = SimpleNamespace(scene=SimpleNamespace(character_designer_animation_worklist=self.saved))
        idle = patch.object(self.worklist, '_idle')
        idle.start()
        self.addCleanup(idle.stop)
        self.manifest = self.folder / 'character_animation_workspace.json'
        self.record = {'schema': self.workspace.SCHEMA, 'workspaceId': str(uuid.uuid4()),
            'targetGuid': 'a' * 32, 'targetName': 'Character',
            'modelFile': str(self.folder / 'Model.fbx'), 'modelSha256': 'f' * 64,
            'clips': [{'clipGuid': guid * 32, 'clipLocalId': 1657602633327794031 + index,
                       'clipName': clip} for index, (guid, clip) in enumerate((('b', 'Walk'), ('c', 'Turn')))]}
        self.write_workspace()
        self.worklist.connect(self.context, str(self.manifest))

    def write_workspace(self):
        self.manifest.write_text(json.dumps(self.record), encoding='utf-8')

    def item(self, index):
        source = ID(name='Source ' + str(index), library=SimpleNamespace(filepath='source.blend'))
        custom = ID(name='Custom ' + str(index), library=None)
        custom['artist_edit'] = 17 + index
        item = SimpleNamespace(item_id=uuid.uuid4().hex, clip_key=self.saved.catalog[index].clip_key,
            name=self.saved.catalog[index].name, source_action=source, custom_action=custom,
            source_hash='frozen baseline', side='CUSTOM', rig=self.saved.rig,
            source_slot=1, custom_slot=2)
        self.saved.items.append(item)
        return item

    def populated(self):
        rig = ID(animation_data=SimpleNamespace(action=None))
        self.saved.rig = rig
        first, second = self.item(0), self.item(1)
        self.saved.active_index = 0
        rig.animation_data.action = first.custom_action
        rig[self.links.LINK_KEY], rig[self.links.ACTION_KEY] = 'selected row association', first.custom_action
        return rig, first, second

    def test_refresh_preserves_selected_clip_identity_and_local_work(self):
        rig, first, second = self.populated()
        self.saved.catalog_index = 0
        key = self.saved.catalog[0].clip_key
        self.record['clips'].reverse()
        self.record['clips'][1]['clipName'] = 'Renamed Walk Display'
        self.write_workspace()
        self.worklist.refresh(self.context)
        self.assertEqual(self.saved.catalog[self.saved.catalog_index].clip_key, key)
        self.assertEqual(self.saved.catalog[self.saved.catalog_index].name, 'Renamed Walk Display')
        self.assertEqual([item.item_id for item in self.saved.items], [first.item_id, second.item_id])
        self.assertIs(rig.animation_data.action, first.custom_action)
        self.assertEqual(first.custom_action['artist_edit'], 17)

    def test_changed_workspace_identity_does_not_replace_local_membership(self):
        rig, first, second = self.populated()
        before = (self.saved.workspace_identity, self.saved.workspace_path,
                  tuple(item.clip_key for item in self.saved.catalog))
        self.record['modelSha256'] = 'd' * 64
        self.write_workspace()
        with self.assertRaisesRegex(ValueError, 'another workspace|model revision'):
            self.worklist.refresh(self.context)
        self.assertEqual((self.saved.workspace_identity, self.saved.workspace_path,
                          tuple(item.clip_key for item in self.saved.catalog)), before)
        self.assertEqual(self.saved.items, [first, second])
        self.assertIs(rig.animation_data.action, first.custom_action)

    def test_blender_relative_connection_persists_the_actual_loaded_path(self):
        with patch.object(self.worklist.bpy.path, 'abspath', return_value=str(self.manifest)):
            self.worklist.connect(self.context, '//character_animation_workspace.json')
        self.assertEqual(self.saved.workspace_path, str(self.manifest))
        self.worklist.refresh(self.context)
        self.assertEqual(len(self.saved.catalog), 2)

    def test_avatar_and_controller_provenance_changes_keep_existing_edits(self):
        metadata = {'baseControllerGuid': 'd' * 32, 'avatarGuid': 'e' * 32,
                    'avatarHash': 'saved Avatar dependency', 'projectId': 'saved Unity project'}
        self.record.update(metadata)
        self.write_workspace()
        self.worklist.connect(self.context, str(self.manifest))
        rig, first, second = self.populated()
        before = (self.saved.workspace_identity, self.saved.workspace_path,
                  tuple(item.clip_key for item in self.saved.catalog), self.saved.active_index)
        for field in metadata:
            for removed in (False, True):
                with self.subTest(field=field, removed=removed):
                    if removed:
                        del self.record[field]
                    else:
                        self.record[field] = 'f' * 32 if field.endswith('Guid') else 'changed dependency'
                    self.write_workspace()
                    with self.assertRaisesRegex(ValueError, 'another workspace|model revision'):
                        self.worklist.refresh(self.context)
                    self.assertEqual((self.saved.workspace_identity, self.saved.workspace_path,
                        tuple(item.clip_key for item in self.saved.catalog), self.saved.active_index), before)
                    self.assertEqual(self.saved.items, [first, second])
                    self.assertIs(rig.animation_data.action, first.custom_action)
                    self.assertEqual(first.custom_action['artist_edit'], 17)
                    self.assertEqual(first.source_hash, 'frozen baseline')
                    self.record[field] = metadata[field]
                    self.write_workspace()

    def test_apply_display_metadata_can_refresh_without_replacing_edits_or_context(self):
        self.record['targetHash'] = 'Prefab dependency before Apply'
        display = {'currentClipGuid': 'd' * 32, 'currentClipLocalId': 1657602633327794041,
                   'currentClipName': 'Applied custom Walk', 'isCustom': True}
        self.record['clips'][0].update(display)
        self.write_workspace()
        self.worklist.connect(self.context, str(self.manifest))
        rig, first, second = self.populated()
        self.saved.catalog_index = 1
        scene = self.context.scene
        source_action, custom_action = first.source_action, first.custom_action
        before = (self.saved.workspace_identity, self.saved.active_index,
                  self.saved.catalog[self.saved.catalog_index].clip_key)
        for removed in (False, True):
            with self.subTest(removed=removed):
                if removed:
                    del self.record['targetHash']
                    for field in display:
                        del self.record['clips'][0][field]
                else:
                    self.record['targetHash'] = 'Prefab dependency after Apply'
                    self.record['clips'][0].update(currentClipGuid='e' * 32,
                        currentClipLocalId=1657602633327794042, currentClipName='Original Walk', isCustom=False)
                self.write_workspace()
                self.worklist.refresh(self.context)
                self.assertEqual((self.saved.workspace_identity, self.saved.active_index,
                    self.saved.catalog[self.saved.catalog_index].clip_key), before)
                self.assertEqual(self.saved.items, [first, second])
                self.assertIs(self.context.scene, scene)
                self.assertIs(self.saved.rig, rig)
                self.assertIs(rig.animation_data.action, custom_action)
                self.assertIs(first.source_action, source_action)
                self.assertIs(first.custom_action, custom_action)
                self.assertEqual(custom_action['artist_edit'], 17)
                self.assertEqual(first.source_hash, 'frozen baseline')
                self.assertEqual(rig[self.links.LINK_KEY], 'selected row association')

    def test_adding_existing_clip_never_replaces_source_or_custom(self):
        _rig, first, _second = self.populated()
        with patch.object(self.worklist, 'activate') as activate, \
                patch.object(self.worklist, '_prepared', side_effect=AssertionError('Existing item was rebaked')):
            self.assertIs(self.worklist.add(self.context, first.clip_key), first)
        activate.assert_called_once_with(self.context, first.item_id, 'CUSTOM')
        self.assertEqual(first.source_hash, 'frozen baseline')
        self.assertEqual(first.custom_action['artist_edit'], 17)
        self.assertEqual(len(self.saved.items), 2)

    def test_move_preserves_active_identity_and_exact_action(self):
        rig, first, second = self.populated()
        self.worklist.move(self.context, first.item_id, 1)
        self.assertEqual(self.saved.items, [second, first])
        self.assertIs(self.worklist.current(self.context), first)
        self.assertIs(rig.animation_data.action, first.custom_action)
        self.assertEqual(rig[self.links.LINK_KEY], 'selected row association')
        with self.assertRaises(ValueError):
            self.worklist.move(self.context, first.item_id, True)

    def test_remove_changes_membership_without_changing_retained_actions(self):
        rig, first, second = self.populated()
        self.worklist.remove(self.context, second.item_id)
        self.assertEqual(self.saved.items, [first])
        self.assertIs(rig.animation_data.action, first.custom_action)
        self.assertEqual(rig[self.links.LINK_KEY], 'selected row association')
        self.assertEqual(second.custom_action['artist_edit'], 18)
        self.worklist.remove(self.context, first.item_id)
        self.assertFalse(self.saved.items)
        self.assertIsNone(rig.animation_data.action)
        self.assertNotIn(self.links.LINK_KEY, rig)
        self.assertNotIn(self.links.ACTION_KEY, rig)
        self.assertEqual(first.custom_action['artist_edit'], 17)

    def test_remove_does_not_detach_an_artist_selected_foreign_action(self):
        rig, first, _second = self.populated()
        artist = ID(name='Chosen manually')
        rig.animation_data.action = artist
        self.worklist.remove(self.context, first.item_id)
        self.assertIs(rig.animation_data.action, artist)
        self.assertEqual(first.custom_action['artist_edit'], 17)

    def test_source_sync_is_rejected_before_disk_or_export_operations(self):
        rig, first, _second = self.populated()
        first.side = 'SOURCE'
        rig.animation_data.action = first.source_action
        with patch.object(self.worklist, '_check_baseline') as check, \
                patch.object(self.links, 'begin_linked_export') as begin:
            with self.assertRaisesRegex(ValueError, 'Custom|read-only'):
                self.worklist.sync(self.context)
            check.assert_not_called()
            begin.assert_not_called()
        self.assertIs(rig.animation_data.action, first.source_action)

    def test_baseline_guard_rejects_cache_tamper_and_foreign_linked_pointer(self):
        _rig, first, _second = self.populated()
        path = self.folder / 'Source.blend'
        path.write_bytes(b'Exact immutable baseline fixture')
        first.source_file, first.source_hash = str(path), self.links._sha256(path)
        first.source_action.library.filepath = str(path)
        self.worklist._check_baseline(first)
        first.source_action.library.filepath = str(self.folder / 'Another Source.blend')
        with self.assertRaises(ValueError):
            self.worklist._check_baseline(first)
        first.source_action.library.filepath = str(path)
        path.write_bytes(b'Changed baseline')
        with self.assertRaisesRegex(ValueError, 'changed|missing'):
            self.worklist._check_baseline(first)
        self.assertEqual(first.custom_action['artist_edit'], 17)


if __name__ == '__main__':
    unittest.main(verbosity=2)
