"""Dependency move/tamper/rollback contracts without launching Blender."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class Owner(dict):
    __hash__ = object.__hash__

    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)

    def __bool__(self):
        return True


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cd-dependency-contract-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.name = '_dependencies_' + uuid.uuid4().hex
        package = ModuleType(self.name)
        package.__path__ = [str(SOURCE)]
        self.bpy = ModuleType('bpy')
        self.bpy.path = NS(abspath=str)
        self.bpy.data = NS(scenes=[])
        self.links = ModuleType(self.name + '.animation_link')
        self.source = ModuleType(self.name + '.animation_link_source')
        self.workspace = ModuleType(self.name + '.animation_workspace')
        self.ua = ModuleType(self.name + '.unity_animation')
        fingerprint = ModuleType(self.name + '.animation_worklist_fingerprint')
        fingerprint.fingerprint_action = lambda action, handle: dict(fingerprint_known=True,
            action_fingerprint=hashlib.sha256(str(action.get('native_content', action.name)).encode()).hexdigest())
        animation = ModuleType(self.name + '.animation')
        animation._link_idle = lambda **_options: True
        target = self.target = ModuleType(self.name + '.animation_worklist_target')
        target.SESSION, target.REFS = 'session', 'refs'
        target.is_adopted = lambda rig: target.SESSION in rig
        target._load = lambda rig: dict(json.loads(rig[target.SESSION]))
        target._validate_record = Mock()
        self.ua.load_package = lambda path: {'_sha256': self.dep._hash(path)}
        modules = {self.name: package, 'bpy': self.bpy}
        for module in (self.links, self.source, self.workspace, self.ua, animation, target, fingerprint):
            modules[module.__name__] = module
            setattr(package, module.__name__.rsplit('.', 1)[-1], module)
        mocked = patch.dict(sys.modules, modules)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.dep = self.load('animation_worklist_dependencies')
        self.links._sha256 = self.dep._hash
        self.worklist = self.load('animation_worklist')
        self.worklist._slot = Mock()
        self.rig = Owner(name='Local Rig')
        self.saved = NS(rig=self.rig, workspace_identity='exact workspace', items=[], retired_items=[])
        self.scene = NS(character_designer_animation_worklist=self.saved)
        self.bpy.data.scenes = [self.scene]
        self.context = NS(scene=self.scene, object=NS(mode='POSE'), view_layer=NS(name='Layer'))
        self.row = self.row_fixture('walk')
        self.saved.items.append(self.row)

    def load(self, short):
        spec = importlib.util.spec_from_file_location(self.name + '.' + short, SOURCE / (short + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        setattr(sys.modules[self.name], short, module)
        return module

    def row_fixture(self, key):
        path = self.root / (key + '.blend')
        path.write_bytes(('Read-only native Source ' + key).encode())
        library = Owner(filepath=str(path), is_missing=False)
        action = Owner(name='Source ' + key, library=library, is_missing=False)
        return NS(item_id=key, work_item_id='item-' + key, clip_key='clip-' + key,
                  rig=self.rig, source_file=str(path), source_hash=self.dep._hash(path),
                  source_action=action, source_slot=3, custom_action=Owner(library=None),
                  current_rig_revision='', new_animation=False)

    def package(self, name='package'):
        return self.dep.package_dependencies(self.context, self.root / name)

    def snapshot(self):
        return (self.row.source_file, self.row.source_action.library.filepath,
                dict(self.rig), self.row.source_action, self.row.custom_action)

    def test_source_relocation_keeps_logical_row_and_read_only_ID(self):
        before = self.snapshot()
        package = self.package()
        Path(self.row.source_file).unlink()
        result = self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertEqual(result['sources'], 1)
        self.assertEqual(self.row.source_file, before[0])
        self.assertIs(self.row.source_action, before[3])
        self.assertIs(self.row.custom_action, before[4])
        self.assertIsNotNone(self.row.source_action.library)
        self.worklist._check_baseline(self.row)

    def test_package_itself_can_move_then_be_rebound_again(self):
        package = self.package()
        self.dep.rebind_dependencies(self.context, package['manifest_path'])
        moved = self.root / 'moved'
        shutil.move(str(Path(package['manifest_path']).parent), moved)
        Path(self.row.source_file).unlink()
        self.dep.rebind_dependencies(self.context, moved / self.dep.MANIFEST)
        self.worklist._check_baseline(self.row)
        self.assertTrue(Path(self.dep.source_path(self.row)).is_relative_to(moved))

    def test_save_as_keeps_exact_source_and_packet_when_relative_json_cannot_remap(self):
        self.bpy.data.filepath = str(self.root / 'editing.blend')
        self.bpy.path.abspath = lambda path: str((Path(self.bpy.data.filepath).parent / path[2:]).resolve()) if path.startswith('//') else path
        self.bpy.path.relpath = lambda path: '//' + os.path.relpath(path, Path(self.bpy.data.filepath).parent)
        packet = self.root / 'packet.json'
        packet.write_bytes(b'exact saved packet')
        self.rig[self.target.SESSION] = json.dumps(dict(packet_path=str(packet), packet_sha256=self.dep._hash(packet)))
        package = self.package()
        result = self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertTrue(result['portable_paths'])
        copied_source = self.dep.source_path(self.row)
        copied_packet = self.dep.packet_path(self.rig, self.target._load(self.rig))
        self.bpy.data.filepath = str(self.root / 'saveas' / 'editing.blend')
        # This remap is performed natively by Blender; opaque JSON is unchanged.
        self.row.source_action.library.filepath = self.bpy.path.relpath(copied_source)
        self.worklist._check_baseline(self.row)
        self.assertEqual(self.dep.source_path(self.row), copied_source)
        self.assertEqual(self.dep.packet_path(self.rig, self.target._load(self.rig)), copied_packet)
        packet.unlink()
        self.assertEqual(self.dep._hash(copied_packet), self.target._load(self.rig)['packet_sha256'])

    def test_directory_move_then_save_as_uses_verified_native_source_as_packet_anchor(self):
        initial = self.root / 'initial'
        initial.mkdir()
        self.bpy.data.filepath = str(initial / 'editing.blend')
        self.bpy.path.abspath = lambda path: str((Path(self.bpy.data.filepath).parent / path[2:]).resolve()) if path.startswith('//') else path
        self.bpy.path.relpath = lambda path: '//' + os.path.relpath(path, Path(self.bpy.data.filepath).parent)
        packet = self.root / 'packet.json'
        packet.write_bytes(b'exact current packet')
        self.rig[self.target.SESSION] = json.dumps(dict(packet_path=str(packet), packet_sha256=self.dep._hash(packet)))
        package = self.dep.package_dependencies(self.context, initial / 'dependencies')
        self.dep.rebind_dependencies(self.context, package['manifest_path'])
        moved = self.root / 'moved'
        shutil.copytree(initial, moved)
        initial.rename(self.root / 'initial-retained')
        self.bpy.data.filepath = str(moved / 'editing.blend')
        actual_source = self.bpy.path.abspath(self.row.source_action.library.filepath)
        self.worklist._check_baseline(self.row)
        self.bpy.data.filepath = str(self.root / 'saveas' / 'editing.blend')
        self.row.source_action.library.filepath = self.bpy.path.relpath(actual_source)
        self.worklist._check_baseline(self.row)
        found_packet = Path(self.dep.packet_path(self.rig, self.target._load(self.rig)))
        self.assertTrue(found_packet.is_relative_to(moved))
        self.assertEqual(self.dep._hash(found_packet), self.target._load(self.rig)['packet_sha256'])

    def test_tampered_packaged_source_refuses_before_any_scene_write(self):
        package = self.package()
        source = next((self.root / 'package' / 'sources').iterdir())
        source.write_bytes(b'changed keys')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'packaged Source changed'):
            self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertEqual(self.snapshot(), before)

    def test_loaded_source_curve_change_refuses_even_with_identical_packaged_file(self):
        package = self.package()
        self.row.source_action['native_content'] = 'foreign reloaded motion'
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'loaded Source curves differ'):
            self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertEqual(self.snapshot(), before)

    def test_missing_original_without_locator_still_refuses(self):
        Path(self.row.source_file).unlink()
        with self.assertRaisesRegex(ValueError, 'changed or is missing'):
            self.worklist._check_baseline(self.row)
        self.assertNotIn(self.dep.LOCATIONS, self.rig)

    def test_existing_rigless_bind_template_keeps_strict_original_baseline_check(self):
        template = NS(source_action=self.row.source_action, source_file=self.row.source_file,
                      source_hash=self.row.source_hash, new_animation=False)
        self.worklist._check_baseline(template)
        Path(self.row.source_file).write_bytes(b'changed original template')
        with self.assertRaisesRegex(ValueError, 'changed or is missing'):
            self.worklist._check_baseline(template)

    def test_failed_postcheck_restores_library_and_prior_locator_exactly(self):
        package = self.package()
        self.rig[self.dep.LOCATIONS] = self.dep._json(dict(
            schema=self.dep.LOCATION_SCHEMA, sources=[], packets=[]))
        before = self.snapshot()
        with patch.object(self.worklist, '_check_baseline', side_effect=ValueError('postcheck refused')):
            with self.assertRaisesRegex(ValueError, 'postcheck refused'):
                self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertEqual(self.snapshot(), before)

    def test_active_and_retired_sources_included(self):
        self.saved.retired_items.append(self.row_fixture('retired'))
        package = self.package()
        self.assertEqual(package['rows'], 2)
        self.assertEqual(package['sources'], 2)
        self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.worklist._check_baseline(self.saved.retired_items[0])

    def test_historical_no_source_claim_stays_absent_without_inventing_baseline(self):
        absent = NS(**dict(self.row.__dict__))
        absent.item_id, absent.source_file, absent.source_hash = 'historical-local', '', ''
        absent.source_action = None
        with patch.object(self.dep, '_historical_rows', return_value=[('history:exact', absent)]):
            package = self.package()
            manifest = json.loads(Path(package['manifest_path']).read_text())
            self.assertEqual(manifest['rows'][1]['path'], '')
            self.assertEqual(manifest['rows'][1]['source_content_sha256'], '')
            self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertIsNone(absent.source_action)
        self.assertEqual(absent.source_file, '')

    def test_historical_partial_source_claim_is_not_treated_as_absent(self):
        partial = NS(**dict(self.row.__dict__))
        partial.item_id, partial.source_action = 'partial-history', None
        with patch.object(self.dep, '_historical_rows', return_value=[('history:exact', partial)]):
            with self.assertRaisesRegex(ValueError, 'loaded read-only Source is missing'):
                self.package()
        self.assertFalse((self.root / 'package').exists())

    def test_new_action_row_keeps_its_explicit_no_source_identity(self):
        row = NS(**dict(self.row.__dict__))
        row.item_id, row.new_animation, row.source_action = 'new-local', True, None
        row.source_file, row.source_hash = '', ''
        record = dict(schema='randomrealm.animation-new-action/1',
            newActionId=uuid.uuid4().hex, workItemId=uuid.uuid4().hex,
            workspaceId=uuid.uuid4().hex, projectId=uuid.uuid4().hex,
            modelFile=str(self.root / 'model.fbx'), workspaceManifest=str(self.root / 'workspace.json'),
            modelSha256='a' * 64, editScope='All')
        record.update({field: 'b' * 32 for field in ('targetGuid', 'targetHash', 'baseControllerGuid',
            'avatarGuid', 'avatarHash', 'workspaceTemplateLinkGuid')})
        row.link_identity = json.dumps(record)
        self.saved.items.append(row)
        package = self.package()
        self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertIsNone(row.source_action)
        self.assertEqual(row.source_file, '')
        self.worklist._check_baseline(row)

    def test_missing_or_changed_legacy_packet_refuses_package_before_creating_directory(self):
        packet = self.root / 'legacy-packet.json'
        self.rig[self.target.SESSION] = json.dumps(dict(packet_path=str(packet), packet_sha256='a' * 64))
        with self.assertRaises(FileNotFoundError):
            self.package()
        self.assertFalse((self.root / 'package').exists())
        packet.write_bytes(b'different revision')
        with self.assertRaisesRegex(ValueError, 'saved recovery packet changed'):
            self.package()
        self.assertFalse((self.root / 'package').exists())

    def test_distinct_libraries_with_identical_bytes_deduplicate_copy(self):
        second = self.row_fixture('second')
        Path(second.source_file).write_bytes(Path(self.row.source_file).read_bytes())
        second.source_hash = self.row.source_hash
        self.saved.retired_items.append(second)
        package = self.package()
        self.assertEqual(package['sources'], 1)
        first_ID, second_ID = self.row.source_action, second.source_action
        result = self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertEqual(result['sources'], 2)
        self.assertIs(self.row.source_action, first_ID)
        self.assertIs(second.source_action, second_ID)

    def test_shared_library_in_another_scene_refuses_without_changes(self):
        package = self.package()
        foreign = self.row_fixture('foreign')
        foreign.source_action.library = self.row.source_action.library
        self.bpy.data.scenes.append(NS(character_designer_animation_worklist=NS(items=[foreign], retired_items=[])))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Another scene shares'):
            self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertEqual(self.snapshot(), before)

    def test_missing_ID_is_never_reloaded_or_made_local(self):
        package = self.package()
        self.row.source_action.is_missing = True
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'loaded read-only Source is missing'):
            self.dep.rebind_dependencies(self.context, package['manifest_path'])
        self.assertEqual(self.snapshot(), before)

    def test_manifest_row_mismatch_and_traversal_refuse(self):
        package = self.package()
        path = Path(package['manifest_path'])
        original = json.loads(path.read_text())
        changed = json.loads(path.read_text())
        changed['rows'][0]['item_id'] = 'some other work'
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'Package rows changed'):
            self.dep.rebind_dependencies(self.context, path)
        original['rows'][0]['path'] = '../walk.blend'
        path.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.dep.rebind_dependencies(self.context, path)
        self.assertNotIn(self.dep.LOCATIONS, self.rig)

    def test_special_rigging_refuses_before_creating_directory(self):
        self.row.custom_action['character_designer_rigging_capture_v1'] = 'captured'
        with self.assertRaisesRegex(ValueError, 'captured Rigging'):
            self.package()
        self.assertFalse((self.root / 'package').exists())

    def test_existing_output_directory_is_never_reused(self):
        directory = self.root / 'package'
        directory.mkdir()
        sentinel = directory / 'kept.txt'
        sentinel.write_text('keep')
        with self.assertRaises(FileExistsError):
            self.package()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_packet_pin_is_immutable_and_reuses_only_identical_cache(self):
        packet_path = self.root / 'exchange.json'
        packet_path.write_bytes(b'exact admitted packet')
        packet = dict(_path=str(packet_path), _sha256=self.dep._hash(packet_path))
        cache = self.root / 'cache'
        copied = Path(self.dep.pin_recovery_packet(packet, cache))
        self.assertEqual(copied.read_bytes(), packet_path.read_bytes())
        self.assertEqual(self.dep.pin_recovery_packet(packet, cache), str(copied))
        copied.write_bytes(b'foreign bytes')
        with self.assertRaisesRegex(ValueError, 'existing dependency cache differs'):
            self.dep.pin_recovery_packet(packet, cache)
        self.assertEqual(copied.read_bytes(), b'foreign bytes')

    def test_current_rig_history_paths_and_session_raw_remain_byte_exact(self):
        historical = self.row_fixture('historical')
        revision = ModuleType(self.name + '.animation_worklist_revision')
        revision.CHECKPOINT, revision.TOKEN = 'checkpoint', 'token'
        revision.RECORD, revision.REFS, revision.SCHEMA = 'record', 'references', 'current-schema'
        revision._digest = lambda value: hashlib.sha256(self.dep._json(value).encode()).hexdigest()
        revision.validate = Mock()
        self.rig[self.target.SESSION] = json.dumps(dict(packet_path=str(self.root / 'packet.json'), packet_sha256='a' * 64))
        self.rig[self.target.REFS] = {'old': self.rig}
        (self.root / 'packet.json').write_bytes(b'packet')
        raw = json.loads(self.rig[self.target.SESSION])
        raw['packet_sha256'] = self.dep._hash(self.root / 'packet.json')
        self.rig[self.target.SESSION] = json.dumps(raw)
        history_raw = self.rig[self.target.SESSION]
        decoded = dict(schema=revision.SCHEMA, rig=self.rig, scene=self.scene, view_layer='Layer',
                       history_raw=history_raw, history_refs={'old': self.rig}, revision_id='rev',
                       old_rows=[dict(historical.__dict__)])
        # Decoding native ID refs is already covered by revision's own tests;
        # this boundary lets the dependency test witness checkpoint inclusion.
        revision._decode = Mock(return_value=decoded)
        payload = {'exact-native-reference-payload': True}
        checkpoint = Owner(library=None, use_fake_user=True)
        checkpoint[revision.RECORD] = json.dumps(dict(payload=payload, sha256=revision._digest(payload)))
        checkpoint[revision.REFS] = {'kept': self.row.source_action}
        self.row.current_rig_revision = 'current-token'
        self.row.custom_action[revision.TOKEN] = 'current-token'
        self.row.custom_action[revision.CHECKPOINT] = checkpoint
        with patch.dict(sys.modules, {revision.__name__: revision}):
            setattr(sys.modules[self.name], 'animation_worklist_revision', revision)
            package = self.package()
            self.assertEqual(package['rows'], 2)
            self.assertEqual(package['sources'], 2)
            before = (self.row.source_file, historical.source_file, dict(checkpoint), dict(self.rig))
            self.dep.rebind_dependencies(self.context, package['manifest_path'])
            self.assertEqual(self.row.source_file, before[0])
            self.assertEqual(historical.source_file, before[1])
            self.assertEqual(dict(checkpoint), before[2])
            self.assertEqual(self.rig[self.target.SESSION], history_raw)
            self.assertIs(self.rig[self.target.REFS]['old'], self.rig)
            self.assertGreaterEqual(revision.validate.call_count, 2)
            self.assertNotEqual(self.dep.source_path(historical), historical.source_file)


if __name__ == '__main__':
    unittest.main()
