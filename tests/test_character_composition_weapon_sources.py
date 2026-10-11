"""Actual temporary-file provenance checks; no Blender or Unity process."""
import copy
import hashlib
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_weapon_source_fixture'
namespace = types.ModuleType(PACKAGE)
namespace.__path__ = [str(ROOT / 'addons' / 'character_designer')]
sys.modules[PACKAGE] = namespace
weapon = importlib.import_module(PACKAGE + '.animation_composition_weapon')


class WeaponSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'Unity'
        rows = []
        for index, asset in enumerate(('Assets/Weapons/Blaster.fbx',
                                       'Assets/Weapons/Blaster.prefab',
                                       'Assets/Weapons/Nested.prefab')):
            path = self.project / asset
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('authored-source-' + asset).encode())
            guid = str(index + 1) * 32
            meta = Path(str(path) + '.meta')
            meta.write_text('fileFormatVersion: 2\nguid: ' + guid + '\n', encoding='utf-8')
            rows.append({'assetPath': asset, 'file': str(path), 'guid': guid,
                         'fileSha256': weapon._sha(path), 'metaSha256': weapon._sha(meta)})
        model, prefab = rows[:2]
        self.document = {
            'modelFile': model['file'], 'modelAssetPath': model['assetPath'],
            'modelGuid': model['guid'], 'modelSha256': model['fileSha256'],
            'modelMetaSha256': model['metaSha256'], 'prefabAssetPath': prefab['assetPath'],
            'prefabGuid': prefab['guid'], 'prefabFileSha256': prefab['fileSha256'],
            'prefabMetaSha256': prefab['metaSha256'],
            'sourceDependencies': sorted(rows, key=lambda row: row['assetPath'].encode('utf-16-be')),
            'excludedDependencies': []}

    def test_actual_dependency_inventory(self):
        self.assertEqual(weapon._source_dependencies(self.document), (3, 0))

    def test_nested_file_changed_with_roots_unchanged(self):
        path = self.project / 'Assets/Weapons/Nested.prefab'
        path.write_bytes(b'changed nested transform')
        with self.assertRaisesRegex(ValueError, 'dependency or importer changed'):
            weapon._source_dependencies(self.document)

    def test_importer_metadata_changed(self):
        path = self.project / 'Assets/Weapons/Nested.prefab.meta'
        path.write_text(path.read_text() + 'newImporterSetting: 1\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'dependency or importer changed'):
            weapon._source_dependencies(self.document)

    def test_guid_mismatch_rejects_even_after_rehash(self):
        row = self.document['sourceDependencies'][-1]
        meta = Path(row['file'] + '.meta')
        meta.write_text('guid: ' + '4' * 32 + '\n', encoding='utf-8')
        row['metaSha256'] = weapon._sha(meta)
        with self.assertRaisesRegex(ValueError, 'GUID changed'):
            weapon._source_dependencies(self.document)

    def test_root_omission_or_duplicate_rejects(self):
        for field in ('modelAssetPath', 'prefabAssetPath'):
            document = copy.deepcopy(self.document)
            document['sourceDependencies'] = [row for row in document['sourceDependencies']
                                               if row['assetPath'] != document[field]]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Both actual'):
                weapon._source_dependencies(document)
        self.document['sourceDependencies'].append(self.document['sourceDependencies'][0])
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            weapon._source_dependencies(self.document)

    def test_wrong_project_physical_path_rejects_identical_bytes(self):
        row = self.document['sourceDependencies'][-1]
        clone = Path(self.temp.name) / 'Other' / row['assetPath']
        clone.parent.mkdir(parents=True)
        clone.write_bytes(Path(row['file']).read_bytes())
        Path(str(clone) + '.meta').write_bytes(Path(row['file'] + '.meta').read_bytes())
        row['file'] = str(clone)
        with self.assertRaisesRegex(ValueError, 'another Unity project'):
            weapon._source_dependencies(self.document)

    def test_source_file_cannot_be_excluded_as_virtual(self):
        self.document['excludedDependencies'] = [{'assetPath': 'Assets/Weapons/Other.prefab',
            'guid': '9' * 32, 'reason': 'package-no-physical-file'}]
        with self.assertRaisesRegex(ValueError, 'silently excluded'):
            weapon._source_dependencies(self.document)

    def test_builtin_exclusion_is_explicit(self):
        self.document['excludedDependencies'] = [{'assetPath': 'Resources/unity_builtin_extra',
            'guid': '', 'reason': 'unity-builtin-no-physical-file'}]
        self.assertEqual(weapon._source_dependencies(self.document), (3, 1))


if __name__ == '__main__':
    unittest.main()
