"""Material exchange boundary contracts; no bpy, Unity or production assets."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_material_match.py'
spec = importlib.util.spec_from_file_location('unity_material_match_contract', SOURCE)
exchange = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exchange)


class UnityMaterialMatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cd-material-match-contract-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.root = self.folder / 'UnityProject'
        self.foreign_root = self.folder / 'OtherUnityProject'
        for root in (self.root, self.foreign_root):
            (root / 'Assets').mkdir(parents=True)
            (root / 'ProjectSettings').mkdir()
        self.directory = self.root / 'Assets/Characters/Cosha'
        self.directory.mkdir(parents=True)
        self.asset_id = 'a' * 32
        self.material_guid = 'b' * 32
        self.source = 'Crystal_Blue'
        self.filename = 'Cosha.fbx'
        self.model = self.directory / self.filename
        self.model.write_bytes(b'Exported FBX fixture, first export')
        self.manifest_path = self.model.with_suffix('.cdesigner.json')
        self.manifest = {
            'schema': 'cdesigner.unity-character.v1', 'asset_id': self.asset_id,
            'filename': self.filename, 'materials': [self.source, 'Skin'],
            'future_metadata': {'artist_note': 'Preserve this export report'},
        }
        self.write_json(self.manifest_path, self.manifest)
        self.material = self.root / 'Assets/Materials/Crystal.mat'
        self.material.parent.mkdir()
        self.material.write_bytes(b'%YAML 1.1\nMaterial:\n  m_Name: Crystal\n')
        self.meta = self.material.with_name(self.material.name + '.meta')
        self.meta.write_text('fileFormatVersion: 2\nguid: ' + self.material_guid + '\n', encoding='utf-8')
        self.binding = {
            'project_root': self.root.as_posix(), 'export_asset_id': self.asset_id,
            'source_material': self.source, 'material_guid': self.material_guid,
            'material_local_id': 1657602633327794031,
            'material_path': 'Assets/Materials/Crystal.mat', 'material_name': 'Crystal',
            'artist_metadata': {'preserve': True},
        }

    @staticmethod
    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def protected_snapshot(self):
        """The exchange may write .codex, but it does not own Assets or reports."""
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in (self.root / 'Assets').rglob('*') if path.is_file()}

    def request(self):
        return exchange.create_request(self.directory, self.filename, self.asset_id, self.source)

    def result_path(self, request):
        return (self.root / '.codex/character-material-matches/requests' /
                (request['request_id'] + '.result.json'))

    def matched_result(self, request):
        return {**request, **self.binding, 'schema': 'cdesigner.material-match-result/1',
                'status': 'matched', 'fbx_sha256': hashlib.sha256(self.model.read_bytes()).hexdigest(),
                'manifest_sha256': hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()}

    def receipt(self):
        """Construct the documented receipt independently of the reader."""
        name = hashlib.sha256(self.source.encode('utf-8')).hexdigest() + '.json'
        path = (self.root / '.codex/character-material-matches/receipts' / self.asset_id / name)
        record = {**self.binding, 'schema': 'cdesigner.material-match-receipt/1',
                  'model_path': self.model.relative_to(self.root).as_posix(),
                  'status': 'matched', 'fbx_sha256': hashlib.sha256(self.model.read_bytes()).hexdigest(),
                  'manifest_sha256': hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()}
        self.write_json(path, record)
        return path, record

    def status(self, binding=None):
        return exchange.binding_status(self.directory, self.filename, self.asset_id,
                                       self.source, self.binding if binding is None else binding)

    def test_request_is_owned_by_export_project_and_preserves_production_files(self):
        before = self.protected_snapshot()
        request = self.request()
        self.assertEqual(request['schema'], 'cdesigner.material-match-request/1')
        self.assertEqual(request['project_root'], self.root.as_posix())
        self.assertEqual(request['model_path'], 'Assets/Characters/Cosha/Cosha.fbx')
        self.assertEqual(request['export_asset_id'], self.asset_id)
        self.assertEqual(request['source_material'], self.source)
        self.assertRegex(request['request_id'], r'^[0-9a-f]{32}$')
        request_path = self.result_path(request).with_name(request['request_id'] + '.json')
        self.assertEqual(json.loads(request_path.read_text(encoding='utf-8')), request)
        self.assertIsNone(exchange.read_result(request))
        self.assertEqual(self.protected_snapshot(), before)

    def test_wrong_project_folder_and_foreign_export_owner_are_rejected(self):
        for directory in (self.root, self.root / 'ProjectSettings', self.folder):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                exchange.create_request(directory, self.filename, self.asset_id, self.source)
        self.write_json(self.manifest_path, {**self.manifest, 'asset_id': 'c' * 32})
        before = self.protected_snapshot()
        with self.assertRaises(ValueError):
            self.request()
        self.assertEqual(self.protected_snapshot(), before)
        self.assertFalse((self.root / '.codex').exists())

    def test_manifest_filename_schema_and_reported_source_must_match(self):
        for change in ({'filename': 'Other.fbx'}, {'schema': 'foreign.export/1'},
                       {'materials': ['Skin']}):
            with self.subTest(change=change):
                self.write_json(self.manifest_path, {**self.manifest, **change})
                with self.assertRaises(ValueError):
                    self.request()
        self.assertFalse((self.root / '.codex').exists())

    def test_path_escape_missing_fbx_and_non_fbx_cannot_create_request(self):
        escaped = self.folder / 'Escaped.fbx'
        escaped.write_bytes(b'Outside Assets')
        escaped_report = escaped.with_suffix('.cdesigner.json')
        self.write_json(escaped_report, {**self.manifest, 'filename': escaped.name})
        for filename in ('../../../Escaped.fbx', escaped.as_posix(),
                         'Missing.fbx', 'Cosha.obj', 'child/Cosha.fbx'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                exchange.create_request(self.directory, filename, self.asset_id, self.source)
        self.model.unlink()
        with self.assertRaises(ValueError):
            self.request()
        self.assertFalse((self.root / '.codex').exists())
        self.assertEqual(escaped.read_bytes(), b'Outside Assets')

    def test_result_identity_spoof_is_rejected(self):
        request = self.request()
        original = self.matched_result(request)
        for field, value in (
                ('schema', 'cdesigner.material-match-result/2'), ('request_id', 'd' * 32),
                ('project_root', self.foreign_root.as_posix()),
                ('model_path', 'Assets/Characters/Other.fbx'), ('export_asset_id', 'e' * 32),
                ('source_material', 'Skin'), ('status', 'success')):
            with self.subTest(field=field):
                self.write_json(self.result_path(request), {**original, field: value})
                with self.assertRaises(ValueError):
                    exchange.read_result(request)

    def test_material_guid_must_match_existing_meta(self):
        request = self.request()
        record = self.matched_result(request)
        self.meta.write_text('fileFormatVersion: 2\nguid: ' + 'f' * 32 + '\n', encoding='utf-8')
        self.write_json(self.result_path(request), record)
        with self.assertRaises(ValueError):
            exchange.read_result(request)
        self.receipt()
        self.assertNotEqual(self.status()[0], 'Matched')

    def test_result_material_reference_cannot_escape_assets_or_coerce_identity(self):
        request = self.request()
        original = self.matched_result(request)
        for field, value in (
                ('material_path', 'Assets/../ProjectSettings/Escaped.mat'),
                ('material_path', self.material.as_posix()),
                ('material_path', 'Assets/Materials/Missing.mat'),
                ('material_path', 'Assets/Materials/Crystal.fbx'),
                ('material_guid', 'bad'), ('material_guid', None),
                ('material_local_id', True), ('material_local_id', 1.0),
                ('material_local_id', '1657602633327794031'),
                ('material_local_id', 0), ('material_local_id', -1)):
            with self.subTest(field=field, value=value):
                self.write_json(self.result_path(request), {**original, field: value})
                with self.assertRaises(ValueError):
                    exchange.read_result(request)

    def test_matched_result_requires_a_usable_material_display_name(self):
        request = self.request()
        original = self.matched_result(request)
        for value in (None, '', ' \t ', 23, []):
            with self.subTest(value=value):
                self.write_json(self.result_path(request), {**original, 'material_name': value})
                with self.assertRaises(ValueError):
                    exchange.read_result(request)
        missing = dict(original)
        missing.pop('material_name')
        self.write_json(self.result_path(request), missing)
        with self.assertRaises(ValueError):
            exchange.read_result(request)

    def test_cancel_wins_over_late_success_and_untrusted_late_json(self):
        request = self.request()
        record = self.matched_result(request)
        exchange.cancel_request(request)
        for late_record in (record, {**record, 'export_asset_id': 'f' * 32}):
            self.write_json(self.result_path(request), late_record)
            self.assertEqual(exchange.read_result(request)['status'], 'cancelled')
        self.result_path(request).write_text('{invalid late JSON', encoding='utf-8')
        self.assertEqual(exchange.read_result(request)['status'], 'cancelled')
        exchange.cancel_request(request)  # Repeated cancellation remains safe.

    def test_old_result_rejected_when_current_model_hash_changes(self):
        request = self.request()
        self.write_json(self.result_path(request), self.matched_result(request))
        self.model.write_bytes(b'New FBX export after Unity matched the first one')
        with self.assertRaises(ValueError):
            exchange.read_result(request)

    def test_unchanged_fbx_does_not_validate_a_result_for_a_previous_manifest_choice(self):
        request = self.request()
        self.write_json(self.result_path(request), self.matched_result(request))
        original_fbx = self.model.read_bytes()
        self.write_json(self.manifest_path, {**self.manifest, 'unity_material_matches': [
            {'source_material': self.source, 'material_guid': 'f' * 32,
             'material_local_id': 2100000, 'project_root': self.root.as_posix(),
             'export_asset_id': self.asset_id}]})
        self.assertEqual(self.model.read_bytes(), original_fbx)
        with self.assertRaises(ValueError):
            exchange.read_result(request)

    def test_result_without_current_manifest_hash_is_not_confirmed(self):
        request = self.request()
        original = self.matched_result(request)
        for value in (None, '', '0' * 64):
            with self.subTest(value=value):
                self.write_json(self.result_path(request), {**original, 'manifest_sha256': value})
                with self.assertRaises(ValueError):
                    exchange.read_result(request)
        missing = dict(original)
        missing.pop('manifest_sha256')
        self.write_json(self.result_path(request), missing)
        with self.assertRaises(ValueError):
            exchange.read_result(request)

    def test_exact_long_material_local_id_survives_json_result_and_recipe(self):
        request = self.request()
        result = self.matched_result(request)
        self.write_json(self.result_path(request), result)
        actual = exchange.read_result(request)
        self.assertIs(type(actual['material_local_id']), int)
        self.assertEqual(actual['material_local_id'], 1657602633327794031)
        recipe = exchange.export_bindings([self.binding], self.directory, self.asset_id,
                                          {self.source}, set())
        self.assertIs(type(recipe[0]['material_local_id']), int)
        self.assertEqual(recipe[0]['material_local_id'], 1657602633327794031)
        decoded = json.loads(json.dumps(recipe))
        self.assertEqual(decoded[0]['material_local_id'], 1657602633327794031)

    def test_saved_recipe_or_result_without_receipt_never_claims_matched(self):
        self.assertNotEqual(self.status()[0], 'Matched')
        request = self.request()
        self.write_json(self.result_path(request), self.matched_result(request))
        self.assertEqual(exchange.read_result(request)['status'], 'matched')
        self.assertNotEqual(self.status()[0], 'Matched')

    def test_only_receipt_for_current_model_and_identity_claims_matched(self):
        path, original = self.receipt()
        self.assertEqual(self.status(), ('Matched', 'Crystal'))
        for field, value in (
                ('schema', 'cdesigner.material-match-receipt/2'),
                ('project_root', self.foreign_root.as_posix()),
                ('model_path', 'Assets/Characters/Other.fbx'),
                ('export_asset_id', 'c' * 32), ('source_material', 'Skin'),
                ('material_guid', 'c' * 32), ('material_local_id', 2100000),
                ('status', 'failed'), ('fbx_sha256', '0' * 64)):
            with self.subTest(field=field):
                self.write_json(path, {**original, field: value})
                self.assertNotEqual(self.status()[0], 'Matched')
        self.write_json(path, original)
        self.assertNotEqual(self.status({**self.binding, 'project_root': self.foreign_root.as_posix()})[0],
                            'Matched')
        self.model.write_bytes(b'Current FBX is a subsequent export')
        self.assertEqual(self.status()[0], 'Awaiting Unity')

    def test_matched_receipt_does_not_override_foreign_or_unusable_saved_recipe(self):
        self.receipt()
        self.assertEqual(self.status()[0], 'Matched')
        self.assertNotIn('model_path', self.binding)
        for change in ({'project_root': self.foreign_root.as_posix()}, {'project_root': ''},
                       {'export_asset_id': 'f' * 32}, {'source_material': 'Skin'},
                       {'material_path': 'Assets/Materials/NoLongerHere.mat'},
                       {'material_guid': 'f' * 32}, {'material_local_id': 0}):
            with self.subTest(change=change):
                self.assertNotEqual(self.status({**self.binding, **change})[0], 'Matched')

    def test_unchanged_fbx_does_not_make_a_stale_or_unversioned_receipt_matched(self):
        path, record = self.receipt()
        self.assertEqual(self.status()[0], 'Matched')
        for value in (None, '', '0' * 64):
            with self.subTest(value=value):
                self.write_json(path, {**record, 'manifest_sha256': value})
                self.assertNotEqual(self.status()[0], 'Matched')
        missing = dict(record)
        missing.pop('manifest_sha256')
        self.write_json(path, missing)
        self.assertNotEqual(self.status()[0], 'Matched')
        self.write_json(path, record)
        original_fbx = self.model.read_bytes()
        self.write_json(self.manifest_path, {**self.manifest, 'simple_materials': [self.source]})
        self.assertEqual(self.model.read_bytes(), original_fbx)
        self.assertEqual(self.status()[0], 'Awaiting Unity')

    def test_repeated_status_reuses_metadata_but_changed_files_invalidate_it(self):
        receipt_path, receipt = self.receipt()
        read_text = Path.read_text
        reads = []

        def counted(path, *args, **kwargs):
            reads.append(path)
            return read_text(path, *args, **kwargs)

        with patch.object(Path, 'read_text', counted):
            for _ in range(8):
                self.assertEqual(self.status()[0], 'Matched')
            self.assertEqual(reads.count(self.manifest_path), 1)
            self.assertEqual(reads.count(receipt_path), 1)
            self.assertEqual(reads.count(self.meta), 1)

            previous = receipt_path.stat()
            self.write_json(receipt_path, {**receipt, 'status': 'failed', 'error': 'Unity remap removed'})
            os.utime(receipt_path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
            self.assertEqual(self.status()[0], 'Review Match')
            self.assertEqual(reads.count(receipt_path), 2)

            previous = receipt_path.stat()
            self.write_json(receipt_path, receipt)
            os.utime(receipt_path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
            self.assertEqual(self.status()[0], 'Matched')
            previous = self.meta.stat()
            self.meta.write_text('fileFormatVersion: 2\nguid: ' + 'f' * 32 + '\n', encoding='utf-8')
            os.utime(self.meta, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
            self.assertEqual(self.status()[0], 'Review Match')
            self.assertEqual(reads.count(self.meta), 2)

    def test_cached_export_manifest_changes_still_revoke_matched_status(self):
        self.receipt()
        self.assertEqual(self.status()[0], 'Matched')
        previous = self.manifest_path.stat()
        self.write_json(self.manifest_path, {**self.manifest, 'asset_id': 'f' * 32})
        os.utime(self.manifest_path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
        self.assertNotEqual(self.status()[0], 'Matched')

    def test_reexport_keeps_only_used_non_simplified_recipes_without_mutating_saved_records(self):
        unused = {**self.binding, 'source_material': 'Unused', 'project_root': self.foreign_root.as_posix(),
                  'material_guid': 'invalid unused binding'}
        simplified = {**self.binding, 'source_material': 'Skin', 'material_guid': 'invalid suspended binding'}
        entries = [self.binding, unused, simplified]
        original = copy.deepcopy(entries)
        before = self.protected_snapshot()
        actual = exchange.export_bindings(entries, self.directory, self.asset_id,
                                          {self.source, 'Skin'}, {'Skin'})
        self.assertEqual(actual, [{key: self.binding[key] for key in
                                 ('source_material', 'material_guid', 'material_local_id',
                                  'project_root', 'export_asset_id')}])
        self.assertEqual(entries, original)
        self.assertEqual(self.protected_snapshot(), before)
        self.assertFalse((self.root / '.codex').exists())

    def test_reexport_rejects_foreign_active_recipe_and_duplicate_pairing(self):
        before = self.protected_snapshot()
        for change in ({'project_root': self.foreign_root.as_posix()}, {'export_asset_id': 'e' * 32}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                exchange.export_bindings([{**self.binding, **change}], self.directory, self.asset_id,
                                         {self.source}, set())
        with self.assertRaises(ValueError):
            exchange.export_bindings([self.binding, copy.deepcopy(self.binding)], self.directory,
                                     self.asset_id, {self.source}, set())
        self.assertEqual(self.protected_snapshot(), before)

    def test_successful_match_receipt_cancel_and_reexport_do_not_modify_export_or_material_assets(self):
        before = self.protected_snapshot()
        request = self.request()
        self.write_json(self.result_path(request), self.matched_result(request))
        self.assertEqual(exchange.read_result(request)['status'], 'matched')
        self.receipt()
        self.assertEqual(self.status()[0], 'Matched')
        exchange.export_bindings([self.binding], self.directory, self.asset_id, {self.source}, set())
        exchange.cancel_request(request)
        self.assertEqual(exchange.read_result(request)['status'], 'cancelled')
        self.assertEqual(self.protected_snapshot(), before)


if __name__ == '__main__':
    unittest.main()
