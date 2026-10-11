"""Pure admission/durable-evidence tests; this suite never starts Blender."""
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import uuid

PATH = Path(__file__).resolve().parents[1] / 'tools' / 'composition_sync_acceptance.py'
SPEC = importlib.util.spec_from_file_location('composition_sync_acceptance', PATH)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def config(directory):
    return {'schema': sync.SCHEMA, 'allowPreparedLinkPublication': True,
        'evidenceDirectory': str(Path(directory).resolve()), 'items': [
            {'kind': 'SOURCE', 'workItemId': str(index) * 32, 'newActionId': '',
             'manifestPath': str((Path(directory) / ('link' + str(index) + '.json')).resolve()),
             'manifestSha256': 'a' * 64} for index in (1, 2)]}


def numeric(row):
    row = sync.validate_one(row)
    return {'schema': sync.NUMERIC_SCHEMA, 'completed': True, 'authorization': row,
        'provenance': {'originalPreparedWorkId': '5' * 32, 'actualWorkItemId': row['workItemId'],
            'newActionId': row['newActionId'], 'templateLinkId': '6' * 32,
            'manifestPath': row['manifestPath'], 'manifestSha256': row['manifestSha256'],
            'modelSha256': 'b' * 64, 'packetSha256': 'c' * 64,
            'sourceAction': 'Actual Custom B', 'sourceActionSlot': 3,
            'publicManifestWorkItemId': str(uuid.UUID(row['workItemId'])),
            'publicManifestNewActionId': str(uuid.UUID(row['newActionId'])) if row['newActionId'] else '',
            'sourcePreparedWorkItemIdExact': str(uuid.UUID('5' * 32))},
        'spec': {}, 'composition_export': {}, 'effective_values': {}}


class SyncFixtureTests(unittest.TestCase):
    def test_single_actual_source_or_registered_new_proof_accepts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = config(directory)['items'][0]
            new = {**source, 'kind': 'NEW', 'workItemId': '3' * 32, 'newActionId': '4' * 32}
            for row in (source, new):
                self.assertEqual(sync.validate_independent_expected(row, numeric(row)), sync.validate_one(row))

    def test_single_proof_rejects_other_identity_manifest_or_plain_missing_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            row = config(directory)['items'][0]
            for field, replacement in (('actualWorkItemId', '9' * 32),
                    ('manifestSha256', 'd' * 64), ('sourceActionSlot', '3'),
                    ('newActionId', '4' * 32), ('templateLinkId', '0' * 32),
                    ('publicManifestWorkItemId', '9' * 32), ('sourcePreparedWorkItemIdExact', '9' * 32)):
                proof = numeric(row)
                proof['provenance'][field] = replacement
                with self.assertRaises(AssertionError):
                    sync.validate_independent_expected(row, proof)
            proof = numeric(row)
            del proof['provenance']['originalPreparedWorkId']
            with self.assertRaises(AssertionError):
                sync.validate_independent_expected(row, proof)

    def test_single_proof_requires_completed_plain_finite_numeric_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            row = config(directory)['items'][0]
            proof = numeric(row)
            proof['completed'] = False
            with self.assertRaises(AssertionError):
                sync.validate_independent_expected(row, proof)
            proof = numeric(row)
            proof['effective_values'] = {'invalid': float('nan')}
            with self.assertRaises(ValueError):
                sync.validate_independent_expected(row, proof)
            source = {**row, 'newActionId': '4' * 32}
            with self.assertRaises(AssertionError):
                sync.validate_one(source)
    def test_explicit_two_real_prepared_targets_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            value = config(directory)
            self.assertIs(sync.validate_config(value), value)

    def test_publication_opt_in_required(self):
        with tempfile.TemporaryDirectory() as directory:
            value = config(directory)
            value['allowPreparedLinkPublication'] = False
            with self.assertRaises(AssertionError):
                sync.validate_config(value)

    def test_duplicate_or_unpinned_publication_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for field, replacement in (('workItemId', '1' * 32), ('manifestSha256', 'wrong'),
                                        ('manifestPath', 'relative.json')):
                value = config(directory)
                value['items'][1][field] = replacement
                with self.assertRaises(AssertionError):
                    sync.validate_config(value)

    def test_new_requires_existing_native_identity_in_explicit_third_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            value = config(directory)
            row = {**value['items'][0], 'kind': 'NEW', 'workItemId': '3' * 32, 'newActionId': '4' * 32}
            value['items'].append(row)
            sync.validate_config(value)
            row['newActionId'] = ''
            with self.assertRaises((AssertionError, ValueError)):
                sync.validate_config(value)

    def test_archive_keeps_snapshot_logs_and_result_after_normal_temp_cleanup(self):
        with tempfile.TemporaryDirectory() as destination:
            with tempfile.TemporaryDirectory() as source:
                root = Path(source)
                (root / 'stage').mkdir()
                (root / 'animation.blend').write_bytes(b'isolated-snapshot')
                (root / 'worker.log').write_text('real-worker-log', encoding='utf-8')
                (root / 'stage/result.json').write_text('{"ok":true}', encoding='utf-8')
                archive = Path(destination) / 'attempt'
                hashes = sync.archive_worker({'root': root, 'log': io.StringIO('flushed')}, archive)
            self.assertEqual(set(hashes), {'animation.blend', 'worker.log', 'stage/result.json'})
            self.assertEqual(hashes['animation.blend'], sync.file_hash(archive / 'animation.blend'))

    def test_worker_evidence_never_overwrites_existing_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AssertionError):
                sync.archive_worker({'root': Path(directory)}, Path(directory))


if __name__ == '__main__':
    unittest.main()
