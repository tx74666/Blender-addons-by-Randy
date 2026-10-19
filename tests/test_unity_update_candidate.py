"""Pure provider-contract and immutable publication tests; no Blender/Unity."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_update_candidate.py'
SPEC = importlib.util.spec_from_file_location('unity_update_candidate_subject', MODULE_PATH)
SUBJECT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SUBJECT
SPEC.loader.exec_module(SUBJECT)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'UnityProject'
        self.formal = self.project / 'Assets/Character'
        self.formal.mkdir(parents=True)
        (self.project / 'ProjectSettings').mkdir()
        self.model = self.formal / 'Cosha.fbx'
        self.model.write_bytes(b'accepted complete FBX fixture\n' * 6)
        self.texture = self.formal / 'skin.png'
        self.texture.write_bytes(b'accepted texture fixture')
        (self.formal / 'Cosha.fbx.meta').write_text('preserve source GUID', encoding='utf-8')
        self.manifest = self.formal / 'Cosha.cdesigner.json'
        self.asset, self.rig, self.update = 'a' * 32, 'b' * 32, 'c' * 32
        self.parts = [
            {'part_id': '1' * 32, 'renderer_path': 'Rig/Body', 'object_name': 'Body', 'material_slots': ['Skin']},
            {'part_id': '2' * 32, 'renderer_path': 'Rig/Dress', 'object_name': 'Dress', 'material_slots': ['Cloth']},
        ]
        self.write_manifest()
        self.stage = self.root / 'worker'
        self.stage.mkdir()
        (self.stage / 'context.fbx').write_bytes(b'new complete FBX fixture\n' * 8)
        (self.stage / 'textures').mkdir()
        (self.stage / 'textures/skin.png').write_bytes(b'new context texture')
        self.files = ['context.fbx', 'textures/skin.png']

    def write_manifest(self, **changes):
        record = {'schema': SUBJECT.BASE_SCHEMA, 'ok': True, 'asset_id': self.asset,
                  'filename': self.model.name,
                  'files': {self.model.name: digest(self.model), self.texture.name: digest(self.texture)}}
        record.update(changes)
        self.manifest.write_text(json.dumps(record), encoding='utf-8')

    def capture(self, **changes):
        options = dict(asset_id=self.asset, rig_id=self.rig, parts=self.parts)
        options.update(changes)
        return SUBJECT.capture_base(self.model, self.manifest, **options)

    def publish(self, base, **changes):
        options = dict(stage=self.stage, model_file='context.fbx', files=self.files,
                       parts=self.parts, mesh_ids=[self.parts[1]['part_id']],
                       update_rig=False, update_id=self.update)
        options.update(changes)
        return SUBJECT.publish_candidate(base, **options)

    def inventory(self, folder):
        return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob('*') if path.is_file()}

    def state_path(self):
        return self.project / '.codex/character-update-state' / (self.asset + '.json')

    def write_state(self, **changes):
        record = {'schema': SUBJECT.RECEIVER_SCHEMA, 'asset_id': self.asset, 'rig_id': self.rig,
                  'target_guid': 'd' * 32, 'model_asset': 'Assets/Character/Cosha.fbx',
                  'model_sha256': digest(self.model), 'manifest_sha256': digest(self.manifest),
                  'receiver_revision': 'e' * 32,
                  'parts': [{key: part[key] for key in ('part_id', 'renderer_path', 'material_slots')}
                            for part in self.parts]}
        record.update(changes)
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding='utf-8')
        return record

    def write_capabilities(self, **changes):
        record = {'schema': SUBJECT.RECEIVER_SCHEMA, 'candidate_schema': SUBJECT.SCHEMA,
                  'project_root': str(self.project),
                  'inbox': str(self.project / '.codex/character-update-inbox'),
                  'accepted_state_root': str(self.project / '.codex/character-update-state'),
                  'supported': ['mesh_same_skeleton'], 'requires_review': True,
                  'supports_rig': False, 'supports_forearm_mesh': False, 'supports_full_reconcile': False,
                  'native_verified_utc': '2026-10-19T04:00:00Z',
                  'verification_report': str(self.project / '.codex/task-staging/character-partial-update-20261019/verification-runs/native.json')}
        record.update(changes)
        path = self.project / '.codex/character-update-receiver.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding='utf-8')
        return path, record

    def test_missing_receiver_announcement_is_read_only_and_not_capability(self):
        before = self.inventory(self.project)
        self.assertIsNone(SUBJECT.receiver_capabilities(self.model))
        self.assertEqual(self.inventory(self.project), before)
        self.assertFalse((self.project / '.codex').exists())

    def test_receiver_announcement_exact_paths_and_boolean_flags(self):
        _path, expected = self.write_capabilities()
        self.assertEqual(SUBJECT.receiver_capabilities(self.model), expected)
        before = self.inventory(self.project)
        self.assertEqual(SUBJECT.receiver_capabilities(self.model, unity_project=self.project), expected)
        self.assertEqual(self.inventory(self.project), before)

    def test_malformed_and_incompatible_capabilities_are_not_missing(self):
        path, _record = self.write_capabilities()
        for text in ('', '{broken', 'null', '{"schema":"a","schema":"b"}'):
            path.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(SUBJECT.CandidateError):
                SUBJECT.receiver_capabilities(self.model)
        for changes in ({'schema': 'other'}, {'candidate_schema': 'other'},
                        {'supported': []}, {'supported': ['mesh_same_skeleton', 'unchecked_auto_apply']},
                        {'requires_review': False}, {'supports_rig': 'false'},
                        {'supports_forearm_mesh': 0}, {'supports_full_reconcile': None}):
            self.write_capabilities(**changes)
            with self.subTest(changes=changes), self.assertRaises(SUBJECT.CandidateError):
                SUBJECT.receiver_capabilities(self.model)

    def test_capability_paths_cannot_redirect_to_another_project_or_arbitrary_folders(self):
        for key, value in (('project_root', str(self.root)),
                           ('inbox', str(self.project / 'Assets/inbox')),
                           ('accepted_state_root', str(self.project / 'Temp/state')),
                           ('verification_report', str(self.root / 'unrelated-proof.json')),
                           ('inbox', 'relative/inbox')):
            self.write_capabilities(**{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(SUBJECT.CandidateError):
                SUBJECT.receiver_capabilities(self.model)

    def test_capability_unknown_metadata_is_not_interpreted_or_returned(self):
        self.write_capabilities(unchecked_action={'command': 'do something'})
        result = SUBJECT.receiver_capabilities(self.model)
        self.assertNotIn('unchecked_action', result)
        self.assertIs(result['requires_review'], True)

    def test_full_update_without_ledger_is_read_only(self):
        before = self.inventory(self.project)
        self.assertIsNone(SUBJECT.require_full_update_allowed(self.model, asset_id=self.asset))
        self.assertEqual(self.inventory(self.project), before)
        self.assertFalse((self.project / '.codex').exists())

    def test_any_existing_ledger_blocks_full_replacement_until_reconciled(self):
        self.write_state()
        with self.assertRaises(SUBJECT.CandidateError):
            SUBJECT.require_full_update_allowed(self.model, asset_id=self.asset)
        for contents in ('', '{broken', '{"schema":"wrong"}'):
            self.state_path().write_text(contents, encoding='utf-8')
            with self.subTest(contents=contents), self.assertRaises(SUBJECT.CandidateError):
                SUBJECT.require_full_update_allowed(self.model, asset_id=self.asset)
        self.state_path().unlink()
        self.state_path().mkdir()
        with self.assertRaises(SUBJECT.CandidateError):
            SUBJECT.require_full_update_allowed(self.model, asset_id=self.asset)

    def test_claimed_reconcile_flag_cannot_bypass_existing_accepted_state(self):
        self.write_capabilities(supports_full_reconcile=True)
        self.write_state()
        with self.assertRaises(SUBJECT.CandidateError):
            SUBJECT.require_full_update_allowed(self.model, asset_id=self.asset)
        self.assertTrue(self.state_path().is_file())

    def test_strict_helpers_reject_external_or_another_unity_project_path(self):
        outside = self.root / 'outside.fbx'
        outside.write_bytes(b'outside model fixture' * 3)
        for call in (lambda: SUBJECT.receiver_capabilities(outside),
                     lambda: SUBJECT.require_full_update_allowed(outside, asset_id=self.asset),
                     lambda: SUBJECT.receiver_capabilities(outside, unity_project=self.project),
                     lambda: SUBJECT.require_full_update_allowed(self.model, asset_id='not-a-stable-id')):
            with self.assertRaises(SUBJECT.CandidateError):
                call()

    def test_windows_long_project_paths_publish_with_conventional_manifest_paths(self):
        long_project = self.root / ('long-parent-' + 'a' * 70) / ('deep-parent-' + 'b' * 70) / 'UnityProject'
        shutil.copytree(self.project, long_project)
        self.project = long_project
        self.formal = self.project / 'Assets/Character'
        self.model, self.texture = self.formal / 'Cosha.fbx', self.formal / 'skin.png'
        self.manifest = self.formal / 'Cosha.cdesigner.json'
        self.write_capabilities()
        self.assertIsNone(SUBJECT.require_full_update_allowed(self.model, asset_id=self.asset))
        self.assertIs(SUBJECT.receiver_capabilities(self.model)['requires_review'], True)
        path = self.publish(self.capture())
        self.assertGreater(len(str(path)), 260)
        record = json.loads(SUBJECT._io(path).read_text(encoding='utf-8'))
        self.assertFalse(record['base']['model_file'].startswith('\\\\?\\'))
        self.assertEqual(record['base']['model_file'], str(self.model))
        self.assertTrue(all(SUBJECT._io(path.parent / name).is_file() for name in self.files))
        if os.name == 'nt':
            self.assertTrue(str(SUBJECT._io(path)).startswith('\\\\?\\'))
            self.assertEqual(SUBJECT._absolute(SUBJECT._io(path), 'Test path'), path)

    def test_nested_asset_folder_name_cannot_hide_real_outer_unity_project_or_ledger(self):
        nested = self.project / 'Assets/Clothes/Assets/Nested.fbx'
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b'accepted nested-name FBX fixture\n' * 5)
        manifest = nested.with_suffix('.cdesigner.json')
        manifest.write_text(json.dumps({'schema': SUBJECT.BASE_SCHEMA, 'ok': True,
            'asset_id': self.asset, 'filename': nested.name,
            'files': {nested.name: digest(nested)}}), encoding='utf-8')
        base = SUBJECT.capture_base(nested, manifest, asset_id=self.asset, rig_id=self.rig, parts=self.parts)
        self.assertEqual(base.unity_project, self.project)
        self.write_capabilities()
        self.assertEqual(SUBJECT.receiver_capabilities(nested)['project_root'], str(self.project))
        SUBJECT.require_full_update_allowed(nested, asset_id=self.asset)
        self.state_path().parent.mkdir(parents=True)
        self.state_path().write_text('{incomplete accepted state', encoding='utf-8')
        with self.assertRaises(SUBJECT.CandidateError):
            SUBJECT.require_full_update_allowed(nested, asset_id=self.asset)

    def test_complete_candidate_matches_receiver_contract_and_preserves_formal_bundle(self):
        before, worker_before = self.inventory(self.formal), self.inventory(self.stage)
        base = self.capture()
        self.assertEqual(base.unity_project, self.project)
        self.assertEqual(base.receiver_revision, '')
        path = self.publish(base)
        self.assertEqual(path, self.project / '.codex/character-update-inbox' / self.asset / self.update / 'candidate.json')
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(record['schema'], SUBJECT.SCHEMA)
        self.assertIs(record['ok'], True)
        self.assertEqual(record['selection'], {'mesh_ids': ['2' * 32], 'update_rig': False, 'delete_ids': []})
        self.assertEqual(record['base'], {'model_file': str(self.model), 'model_sha256': digest(self.model),
                                         'manifest_sha256': digest(self.manifest), 'receiver_revision': ''})
        self.assertEqual(record['parts'], self.parts)
        for relative, expected in record['files'].items():
            self.assertEqual(digest(path.parent / relative), expected)
        self.assertEqual(self.inventory(self.formal), before)
        self.assertEqual(self.inventory(self.stage), worker_before)

    def test_existing_receiver_identity_mapping_and_revision_are_pinned(self):
        self.write_state()
        base = self.capture()
        self.assertEqual(base.receiver_revision, 'e' * 32)
        record = json.loads(self.publish(base).read_text(encoding='utf-8'))
        self.assertEqual(record['base']['receiver_revision'], 'e' * 32)
        self.write_state(receiver_revision='f' * 32)
        with self.assertRaises(SUBJECT.CandidateError):
            SUBJECT.revalidate_base(base)

    def test_state_appearance_and_disappearance_are_not_treated_as_empty_revision(self):
        base = self.capture()
        self.write_state()
        with self.assertRaises(SUBJECT.CandidateError):
            self.publish(base)
        base = self.capture()
        self.state_path().unlink()
        with self.assertRaises(SUBJECT.CandidateError):
            SUBJECT.revalidate_base(base)

    def test_missing_changed_or_unowned_full_baseline_is_refused(self):
        for changes in ({'ok': False}, {'schema': 'other'}, {'asset_id': 'f' * 32},
                        {'filename': 'Renamed.fbx'}, {'files': {}},
                        {'files': {'skin.png': digest(self.texture)}}):
            with self.subTest(changes=changes):
                self.write_manifest(**changes)
                with self.assertRaises(SUBJECT.CandidateError):
                    self.capture()
        self.write_manifest()
        self.texture.unlink()
        with self.assertRaises(SUBJECT.CandidateError):
            self.capture()

    def test_stale_baseline_and_corrupt_receiver_state_refuse_publication(self):
        base = self.capture()
        self.model.write_bytes(b'artist accepted file changed')
        with self.assertRaises(SUBJECT.CandidateError):
            self.publish(base)
        self.model.write_bytes(b'accepted complete FBX fixture\n' * 6)
        self.write_state()
        self.state_path().write_text('{broken', encoding='utf-8')
        with self.assertRaises(SUBJECT.CandidateError):
            self.capture()

    def test_registered_part_identity_path_and_material_order_cannot_be_reinferred(self):
        self.write_state()
        for field, value in (('part_id', '3' * 32), ('renderer_path', 'Rig/Renamed'),
                             ('material_slots', ['New Cloth'])):
            parts = json.loads(json.dumps(self.parts))
            parts[1][field] = value
            with self.subTest(field=field), self.assertRaises(SUBJECT.CandidateError):
                self.capture(parts=parts)

    def test_missing_duplicate_and_unsafe_identity_context_is_refused(self):
        invalid = []
        for field, value in (('part_id', ''), ('part_id', 'stable-name'),
                             ('renderer_path', 'Rig//Body'), ('renderer_path', '../Body'),
                             ('material_slots', ['']), ('object_name', 'Bad\nName')):
            parts = json.loads(json.dumps(self.parts))
            parts[0][field] = value
            invalid.append(parts)
        duplicate_id = json.loads(json.dumps(self.parts))
        duplicate_id[1]['part_id'] = duplicate_id[0]['part_id']
        invalid.append(duplicate_id)
        duplicate_path = json.loads(json.dumps(self.parts))
        duplicate_path[1]['renderer_path'] = duplicate_path[0]['renderer_path']
        invalid.append(duplicate_path)
        for parts in invalid:
            with self.subTest(parts=parts), self.assertRaises(SUBJECT.CandidateError):
                self.capture(parts=parts)

    def test_selection_is_explicit_bounded_unique_and_cannot_authorize_deletion(self):
        base = self.capture()
        for changes in ({'mesh_ids': None}, {'mesh_ids': ['3' * 32]},
                        {'mesh_ids': ['1' * 32, '1' * 32]}, {'update_rig': 1},
                        {'update_id': 'renamed-character'}):
            with self.subTest(changes=changes), self.assertRaises(SUBJECT.CandidateError):
                self.publish(base, **changes)
        path = self.publish(base, mesh_ids=[], update_rig=False)
        self.assertEqual(json.loads(path.read_text())['selection'],
                         {'mesh_ids': [], 'update_rig': False, 'delete_ids': []})

    def test_candidate_file_alias_traversal_metadata_and_reserved_names_are_refused(self):
        base = self.capture()
        for relative in ('../escape.fbx', 'folder\\escape.fbx', 'C:stream', '/absolute.fbx',
                         'folder//file.fbx', 'CON', 'bad.meta', 'candidate.json'):
            with self.subTest(relative=relative), self.assertRaises(SUBJECT.CandidateError):
                self.publish(base, files=['context.fbx', relative])
        with self.assertRaises(SUBJECT.CandidateError):
            self.publish(base, files=['context.fbx', 'CONTEXT.FBX'])
        self.assertFalse((self.root / 'escape.fbx').exists())

    def test_hard_linked_and_symbolic_inputs_are_refused(self):
        alias = self.root / 'alias.fbx'
        os.link(self.model, alias)
        with self.assertRaises(SUBJECT.CandidateError):
            self.capture()
        alias.unlink()
        link = self.root / 'linked-stage'
        try:
            os.symlink(self.stage, link, target_is_directory=True)
        except OSError:
            return  # Native hard-link rejection above still ran on Windows.
        with self.assertRaises(SUBJECT.CandidateError):
            self.publish(self.capture(), stage=link)

    def test_duplicate_json_keys_and_aliases_in_baseline_are_refused(self):
        self.manifest.write_text('{"schema":"a","schema":"b"}', encoding='utf-8')
        with self.assertRaises(SUBJECT.CandidateError):
            self.capture()
        self.write_manifest(files={'Cosha.fbx': digest(self.model), 'COSHA.FBX': digest(self.model)})
        with self.assertRaises(SUBJECT.CandidateError):
            self.capture()

    def test_existing_update_and_racing_update_directory_are_never_overwritten(self):
        base = self.capture()
        path = self.publish(base)
        before = self.inventory(path.parent)
        with self.assertRaises(SUBJECT.CandidateError):
            self.publish(base)
        self.assertEqual(self.inventory(path.parent), before)
        real_rename = SUBJECT._rename_noreplace
        other_id = 'd' * 32
        def collision(source, target):
            if source.is_dir():
                target.mkdir()
            return real_rename(source, target)
        with mock.patch.object(SUBJECT, '_rename_noreplace', side_effect=collision), self.assertRaises(FileExistsError):
            self.publish(base, update_id=other_id)
        self.assertTrue((path.parent.parent / other_id).is_dir())
        self.assertFalse((path.parent.parent / other_id / 'candidate.json').exists())

    def test_manifest_is_the_last_discoverable_file(self):
        base = self.capture()
        real_rename = SUBJECT._rename_noreplace
        stages = []
        def inspect(source, target):
            if source.is_dir():
                self.assertFalse((source / 'candidate.json').exists())
                stages.append('hidden complete stage')
            else:
                self.assertEqual(target.name, 'candidate.json')
                self.assertFalse(target.exists())
                self.assertTrue(all((target.parent / name).is_file() for name in self.files))
                stages.append('manifest last')
            return real_rename(source, target)
        with mock.patch.object(SUBJECT, '_rename_noreplace', side_effect=inspect):
            self.publish(base)
        self.assertEqual(stages, ['hidden complete stage', 'manifest last'])

    def test_copy_failure_cleans_only_own_pending_files_and_preserves_source_bundle(self):
        base = self.capture()
        before = self.inventory(self.formal)
        real_copy = SUBJECT._copy_file
        calls = []
        def fail_second(pin, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError('injected copy failure')
            return real_copy(pin, target)
        with mock.patch.object(SUBJECT, '_copy_file', side_effect=fail_second), self.assertRaises(OSError):
            self.publish(base)
        inbox = self.project / '.codex/character-update-inbox' / self.asset
        self.assertEqual(list(inbox.iterdir()), [])
        self.assertEqual(self.inventory(self.formal), before)

    def test_ledger_change_after_directory_claim_leaves_no_completed_candidate(self):
        base = self.capture()
        real_rename = SUBJECT._rename_noreplace
        def change_after_claim(source, target):
            result = real_rename(source, target)
            if target.is_dir():
                self.write_state()
            return result
        with mock.patch.object(SUBJECT, '_rename_noreplace', side_effect=change_after_claim), self.assertRaises(SUBJECT.CandidateError):
            self.publish(base)
        inbox = self.project / '.codex/character-update-inbox' / self.asset
        self.assertFalse((inbox / self.update).exists())


if __name__ == '__main__':
    unittest.main()
