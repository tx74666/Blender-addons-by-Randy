"""Cheap second-stage admission/catalog guards; no bpy/process/publication."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
import uuid

PATH = Path(__file__).resolve().parents[1] / 'tools' / 'run_character_composition_raise_stage.py'
SPEC = importlib.util.spec_from_file_location('composition_raise_stage', PATH)
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)


def fixture(directory):
    base = Path(directory).resolve()
    value = {key: 'a' * 64 for key in stage.FIELDS}
    value.update(schema=stage.SCHEMA, evidenceDirectory=str(base / 'new-evidence'),
        workItemId='1' * 32, sceneName='Composition QA · Fresh Workspace', rigName='CoshaRig',
        targetGuid='2' * 32, poseTolerance=2e-5, inputTolerance=1e-6, outputSeedFile=None)
    for key, name in (('inputBlend', 'artist_final_validation.blend'), ('addonPackage', 'character_designer'),
        ('runtimeManifest', 'runtime.json'), ('numericReport', 'composition_native_report.json'),
        ('seedBlend', 'first_stage_qa_seed.blend')):
        value[key] = str(base / name)
    for key in ('freshWorkspace', 'raiseAuthorFixture'):
        value[key] = {'path': str(base / (key + '.json')), 'sha256': 'a' * 64}
    value['tools'] = {name: {'path': str(base / (name + '.py')), 'sha256': 'b' * 64} for name in stage.TOOLS}
    return value


def catalog():
    def row(index, mode='HoldPose'):
        return {'workItemId': str(index) * 32, 'linkManifest': 'D:/link' + str(index) + '.json',
            'sourcePackage': 'D:/packet' + str(index) + '.json', 'sourcePackageSha256': 'a' * 64,
            'linkGuid': str(index) * 32, 'editScope': 'UpperBodyOverride', 'needsRebuild': False,
            'composition': {'timeMode': mode, 'editWorkItemId': str(index) * 32}}
    old = {'identity': ('actualworkspace', 'targetGUID', 'modelSHA'),
           'clips': [row(1), row(2), row(3, 'HoldLoop')]}
    fresh = copy.deepcopy(old)
    fresh['clips'].append(row(4, 'Raise'))
    return old, fresh


def workspace():
    route = lambda row: json.dumps((row['workItemId'], row.get('composition'),
        row.get('compositionOwnerLinkGuid'), row.get('clipGuid')), sort_keys=True)
    return NS(workspace_identity=lambda value: value['identity'],
        work_item_id=lambda row: uuid.UUID(row['workItemId']).hex,
        row_key=lambda row: 'work:' + row['workItemId'], route_identity=route)


class RaiseStageTests(unittest.TestCase):
    def check(self, old, fresh):
        return stage.validate_catalog_extension(old, fresh, workspace=workspace(),
            raise_work_id='4' * 32, protected_work_ids=['1' * 32, '2' * 32, '3' * 32])

    def test_exact_source_only_config_accepts_optional_fresh_test_save(self):
        with tempfile.TemporaryDirectory() as directory:
            value = fixture(directory)
            self.assertIs(stage.validate_config(value), value)
            value['outputSeedFile'] = str(Path(value['evidenceDirectory']) / 'new_raise.blend')
            stage.validate_config(value)

    def test_artist_ui_seed_alias_unknown_fields_and_external_save_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, change in (('inputBlend', str(Path(directory) / 'X.blend')),
                ('seedBlend', fixture(directory)['inputBlend']), ('poseTolerance', float('nan')),
                ('outputSeedFile', str(Path(directory) / 'outside.blend'))):
                value = fixture(directory)
                value[key] = change
                with self.assertRaises(AssertionError):
                    stage.validate_config(value)
            value = fixture(directory)
            value['allowRewriteOldPacket'] = True
            with self.assertRaises(AssertionError):
                stage.validate_config(value)

    def test_real_raise_extension_preserves_all_old_sealed_source_inputs(self):
        old, fresh = catalog()
        original = copy.deepcopy((old, fresh))
        proof = self.check(old, fresh)
        self.assertEqual(proof['admitted_prepared_additions'], ['4' * 32])
        self.assertEqual((old, fresh), original)

    def test_actual_raise_own_readonly_A_companion_is_admitted(self):
        old, fresh = catalog()
        fresh['clips'].append({'workItemId': '5' * 32, 'linkManifest': 'D:/reference.link.json',
            'sourcePackage': 'D:/reference.packet.json', 'composition': None,
            'compositionReferenceSource': True, 'compositionOwnerLinkGuid': '4' * 32, 'editScope': 'All'})
        self.assertEqual(self.check(old, fresh)['admitted_prepared_additions'], ['4' * 32, '5' * 32])
        fresh['clips'][-1]['compositionOwnerLinkGuid'] = '9' * 32
        with self.assertRaisesRegex(AssertionError, 'unrelated'):
            self.check(old, fresh)

    def test_changed_identity_sealed_scope_packet_or_link_route_rejects(self):
        for field, changed in (('sourcePackageSha256', 'f' * 64), ('sourcePackage', 'D:/different.json'),
            ('linkManifest', 'D:/different.link.json'), ('composition', {'timeMode': 'Raise'}), ('clipGuid', '9' * 32),
            ('editScope', 'All'), ('sourceOnly', True), ('compositionReferenceSource', True),
            ('compositionOwnerLinkGuid', '9' * 32), ('clipLocalId', 99), ('linkId', '9' * 32)):
            old, fresh = catalog()
            fresh['clips'][0][field] = changed
            with self.assertRaisesRegex(AssertionError, 'changed'):
                self.check(old, fresh)
        old, fresh = catalog()
        fresh['identity'] = ('differentworkspace', 'targetGUID', 'modelSHA')
        with self.assertRaisesRegex(AssertionError, 'identity'):
            self.check(old, fresh)

    def test_missing_duplicate_or_unrelated_prepared_sources_reject(self):
        old, fresh = catalog()
        del fresh['clips'][0]
        with self.assertRaises(AssertionError):
            self.check(old, fresh)
        old, fresh = catalog()
        fresh['clips'].append(copy.deepcopy(fresh['clips'][0]))
        with self.assertRaises(AssertionError):
            self.check(old, fresh)
        old, fresh = catalog()
        unrelated = copy.deepcopy(fresh['clips'][-1])
        unrelated.update(workItemId='6' * 32, linkGuid='6' * 32,
                         composition={'timeMode': 'HoldLoop', 'editWorkItemId': '6' * 32})
        fresh['clips'].append(unrelated)
        with self.assertRaisesRegex(AssertionError, 'unrelated'):
            self.check(old, fresh)


if __name__ == '__main__':
    unittest.main()
