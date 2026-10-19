"""Exercise actual full-export admissions and publication with owned file fixtures.

No Blender, Unity, native worker, or user preferences are opened. Only the native
scene boundary and subprocess are replaced; the core guard, target resolution,
baseline helper, begin_export, poll_export and _publish are production code.
"""
import importlib
import json
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class Object:
    def __init__(self, name, kind):
        self.name, self.type = name, kind


class FullUpdateGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='unity-full-guard-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'UnityProject'
        self.folder = self.project / 'Assets' / 'Character'
        self.folder.mkdir(parents=True)
        (self.project / 'ProjectSettings').mkdir()
        self.backups = self.root / 'owned-backups'
        self.asset_id = 'a' * 32
        self.rig, self.body = Object('CharacterRig', 'ARMATURE'), Object('Character', 'MESH')
        self.context = SimpleNamespace(mode='OBJECT',
            scene=SimpleNamespace(objects={self.rig.name: self.rig, self.body.name: self.body},
                                  unit_settings=SimpleNamespace(scale_length=1.0)),
            setup=SimpleNamespace(rig=self.rig, body=self.body, assets=[]))
        self.config = SimpleNamespace(directory=str(self.folder), asset_id=self.asset_id,
                                      extras=[], simple_materials=[], unity_material_matches=[])
        name = '_unity_full_guard_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        bpy.path = SimpleNamespace(abspath=lambda value: value)
        bpy.app = SimpleNamespace(binary_path='owned-fixture-blender')
        self.snapshot = mock.Mock(side_effect=lambda filepath, *_args, **_kwargs:
                                  Path(filepath).write_bytes(b'owned snapshot fixture'))
        bpy.data = SimpleNamespace(libraries=SimpleNamespace(write=self.snapshot))
        self.user_resource = mock.Mock(return_value=str(self.backups))
        bpy.utils = SimpleNamespace(user_resource=self.user_resource)
        setup = ModuleType(name + '.character_setup')
        setup.settings = lambda context: context.setup
        self.preview = mock.Mock(return_value={'active': False})
        modules = {name: package, 'bpy': bpy, setup.__name__: setup}
        for suffix, fields in {
            'dress_export_guard': {'reject_model_sources': mock.Mock()},
            'hair_wiggle_adapter': {'status': self.preview, 'stop_preview': mock.Mock()},
            'skirt_wiggle': {'suspend': mock.Mock()},
            'unity_forearm': {'capture': mock.Mock()},
            'unity_material_match_ui': {'capture': mock.Mock(return_value=[])},
        }.items():
            module = ModuleType(name + '.' + suffix)
            module.__dict__.update(fields)
            modules[module.__name__] = module
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.subject = importlib.import_module(name + '.unity_export')
        self.helper = importlib.import_module(name + '.unity_update_candidate')
        for function, value in {
            'collect_character': {'objects': [self.rig, self.body], 'warnings': []},
            '_capture_dress_surfaces': [], '_capture_hair_motion': None,
            '_owned_keys': {}, '_image_buffers': {},
            '_model_snapshot_datablocks': {self.rig, self.body},
        }.items():
            replacement = mock.patch.object(self.subject, function, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)
        subprocess_patch = mock.patch.object(self.subject.subprocess, 'Popen', side_effect=self.worker)
        self.popen = subprocess_patch.start()
        self.addCleanup(subprocess_patch.stop)
        self.addCleanup(self.cleanup_job)

    @property
    def model(self):
        return self.folder / 'Character.fbx'

    @property
    def manifest(self):
        return self.model.with_suffix('.cdesigner.json')

    @property
    def ledger(self):
        return self.project / '.codex' / 'character-update-state' / (self.asset_id + '.json')

    def worker(self, command, **_kwargs):
        specification = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
        stage = Path(specification['stage'])
        (stage / specification['filename']).write_bytes(b'new full FBX fixture\n' * 5)
        (stage / 'result.json').write_text(json.dumps({'ok': True,
            'files': [specification['filename']], 'warnings': [], 'dress_surfaces': []}), encoding='utf-8')
        return SimpleNamespace(returncode=0, poll=lambda: 0, args=command)

    def cleanup_job(self):
        if self.subject._ACTIVE_JOB is not None:
            self.subject.cancel_export(self.subject._ACTIVE_JOB)

    def write_ledger(self, kind='malformed'):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'directory':
            self.ledger.mkdir()
        else:
            self.ledger.write_bytes(b'' if kind == 'empty' else b'{malformed accepted state')

    def write_formal(self):
        self.model.write_bytes(b'accepted full FBX fixture\n' * 5)
        self.manifest.write_text(json.dumps({'schema': self.subject.SCHEMA, 'ok': True,
            'asset_id': self.asset_id, 'filename': self.model.name,
            'files': {self.model.name: self.subject._hash(self.model)}}), encoding='utf-8')
        Path(str(self.model) + '.meta').write_text('preserve-source-guid', encoding='utf-8')

    def formal_bytes(self):
        return {path.name: path.read_bytes() for path in self.folder.iterdir() if path.is_file()}

    def assert_no_admission_effect(self):
        self.preview.assert_not_called()
        self.snapshot.assert_not_called()
        self.popen.assert_not_called()
        self.user_resource.assert_not_called()
        self.assertIsNone(self.subject._ACTIVE_JOB)
        self.assertEqual(vars(self.rig), {'name': self.rig.name, 'type': 'ARMATURE'})
        self.assertEqual(vars(self.body), {'name': self.body.name, 'type': 'MESH'})

    def test_direct_begin_blocks_ledger_before_preview_snapshot_and_process(self):
        self.write_formal()
        self.write_ledger()
        before = self.formal_bytes()
        with self.assertRaisesRegex(ValueError, 'accepted partial updates'):
            self.subject.begin_export(self.context, self.rig, self.config)
        self.assert_no_admission_effect()
        self.assertEqual(self.formal_bytes(), before)

    def test_empty_and_directory_ledgers_are_not_bypassable(self):
        for kind in ('empty', 'directory'):
            with self.subTest(kind=kind):
                self.write_ledger(kind)
                with self.assertRaisesRegex(ValueError, 'accepted partial updates'):
                    self.subject.begin_export(self.context, self.rig, self.config)
                self.assert_no_admission_effect()
                if self.ledger.is_dir():
                    self.ledger.rmdir()
                else:
                    self.ledger.unlink()

    def test_current_character_ledger_blocks_a_renamed_body_target(self):
        self.write_ledger()
        self.body.name = 'Renamed Body'
        self.context.scene.objects[self.body.name] = self.body
        self.assertFalse((self.folder / 'Renamed Body.fbx').exists())
        with self.assertRaisesRegex(ValueError, 'accepted partial updates'):
            self.subject.begin_export(self.context, self.rig, self.config)
        self.assert_no_admission_effect()

    def test_cleared_identity_cannot_replace_existing_fbx_or_manifest(self):
        self.config.asset_id = ''
        for existing in (self.model, self.manifest):
            with self.subTest(existing=existing.name):
                existing.write_bytes(b'existing formal input')
                before = self.formal_bytes()
                with self.assertRaisesRegex(ValueError, 'saved character identity'):
                    self.subject.begin_export(self.context, self.rig, self.config)
                self.assert_no_admission_effect()
                self.assertEqual(self.config.asset_id, '')
                self.assertEqual(self.formal_bytes(), before)
                existing.unlink()

    def test_first_full_export_keeps_blank_identity_uuid_flow(self):
        self.config.asset_id = ''
        job = self.subject.begin_export(self.context, self.rig, self.config)
        self.assertRegex(job['asset_id'], r'^[0-9a-f]{32}$')
        self.assertEqual(self.config.asset_id, job['asset_id'])
        self.assertIs(self.subject._ACTIVE_JOB, job)
        self.popen.assert_called_once()

    def test_legacy_outside_unity_keeps_existing_identity_and_full_flow(self):
        folder = self.root / 'Legacy Export'
        self.config.directory, self.config.asset_id = str(folder), 'legacy-profile'
        job = self.subject.begin_export(self.context, self.rig, self.config)
        self.assertEqual(job['asset_id'], 'legacy-profile')
        self.assertEqual(job['directory'], folder)
        self.popen.assert_called_once()

    def test_assets_named_folder_without_projectsettings_is_legacy(self):
        (self.project / 'ProjectSettings').rmdir()
        self.config.asset_id = 'legacy-profile'
        self.subject._full_update_ledger_guard(self.model, self.config.asset_id)
        job = self.subject.begin_export(self.context, self.rig, self.config)
        self.assertEqual(job['asset_id'], 'legacy-profile')

    def test_nested_assets_name_does_not_hide_the_actual_unity_ledger(self):
        self.folder = self.project / 'Assets' / 'Clothes' / 'Assets' / 'Character'
        self.folder.mkdir(parents=True)
        self.config.directory = str(self.folder)
        self.write_ledger()
        with self.assertRaisesRegex(ValueError, 'accepted partial updates'):
            self.subject.begin_export(self.context, self.rig, self.config)
        self.assert_no_admission_effect()

    def test_ledger_created_during_worker_blocks_publish_using_captured_identity(self):
        self.write_formal()
        before = self.formal_bytes()
        job = self.subject.begin_export(self.context, self.rig, self.config)
        self.write_ledger()
        self.config.asset_id = 'b' * 32
        with self.assertRaisesRegex(ValueError, 'accepted partial updates'):
            self.subject.poll_export(job)
        self.assertEqual(self.formal_bytes(), before)
        self.user_resource.assert_not_called()
        self.assertIsNone(self.subject._ACTIVE_JOB)
        self.assertFalse(job['root'].exists())

    def test_ledger_created_during_backups_blocks_first_formal_overwrite(self):
        self.write_formal()
        before = self.formal_bytes()
        job = self.subject.begin_export(self.context, self.rig, self.config)
        copy = self.subject.shutil.copy2

        def create_state_during_backup(source, destination, *args, **kwargs):
            result = copy(source, destination, *args, **kwargs)
            if Path(destination).is_relative_to(self.backups) and not self.ledger.exists():
                self.write_ledger()
            return result

        with mock.patch.object(self.subject.shutil, 'copy2', side_effect=create_state_during_backup):
            with self.assertRaisesRegex(ValueError, 'accepted partial updates'):
                self.subject.poll_export(job)
        self.assertEqual(self.formal_bytes(), before)
        self.assertTrue(self.ledger.exists())
        self.assertIsNone(self.subject._ACTIVE_JOB)

    def test_ledger_created_during_first_scratch_copy_blocks_replacement(self):
        self.write_formal()
        before = self.formal_bytes()
        job = self.subject.begin_export(self.context, self.rig, self.config)
        copy = self.subject.shutil.copy2

        def create_state_during_scratch(source, destination, *args, **kwargs):
            result = copy(source, destination, *args, **kwargs)
            if Path(destination).name.startswith('.cdesigner-'):
                self.write_ledger()
            return result

        with mock.patch.object(self.subject.shutil, 'copy2', side_effect=create_state_during_scratch):
            with self.assertRaisesRegex(ValueError, 'accepted partial updates'):
                self.subject.poll_export(job)
        self.assertEqual(self.formal_bytes(), before)
        self.assertTrue(self.ledger.exists())
        self.assertIsNone(self.subject._ACTIVE_JOB)

    def test_ledger_created_during_later_scratch_copy_rolls_back_prior_replacement(self):
        self.write_formal()
        before = self.formal_bytes()
        job = self.subject.begin_export(self.context, self.rig, self.config)
        copy = self.subject.shutil.copy2
        replaced_model = []

        def create_state_during_manifest_scratch(source, destination, *args, **kwargs):
            result = copy(source, destination, *args, **kwargs)
            if (Path(destination).name.startswith('.cdesigner-')
                    and Path(source).name == self.manifest.name):
                replaced_model.append(self.model.read_bytes())
                self.write_ledger()
            return result

        with mock.patch.object(self.subject.shutil, 'copy2', side_effect=create_state_during_manifest_scratch):
            with self.assertRaisesRegex(ValueError, 'accepted partial updates'):
                self.subject.poll_export(job)
        self.assertEqual(len(replaced_model), 1)
        self.assertNotEqual(replaced_model[0], before[self.model.name])
        self.assertEqual(self.formal_bytes(), before)
        self.assertTrue(self.ledger.exists())
        self.assertIsNone(self.subject._ACTIVE_JOB)

    def test_ordinary_full_publish_remains_available_without_ledger(self):
        self.write_formal()
        old = self.model.read_bytes()
        meta = Path(str(self.model) + '.meta').read_bytes()
        job = self.subject.begin_export(self.context, self.rig, self.config)
        result = self.subject.poll_export(job)
        self.assertEqual(result['filepath'], str(self.model))
        self.assertNotEqual(self.model.read_bytes(), old)
        self.assertEqual(Path(str(self.model) + '.meta').read_bytes(), meta)
        self.assertEqual(json.loads(self.manifest.read_text())['asset_id'], self.asset_id)
        self.assertIsNone(self.subject._ACTIVE_JOB)


if __name__ == '__main__':
    unittest.main(verbosity=2)
