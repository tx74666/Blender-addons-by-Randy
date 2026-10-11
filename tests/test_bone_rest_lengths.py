"""Pure metadata contracts; no Blender import, mocks, or process startup."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/bone_rest_lengths.py'
spec = importlib.util.spec_from_file_location('_bone_rest_lengths_contract', SOURCE)
lengths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lengths)

FBX_BYTES = b'Exact frozen model bytes for manifest identity checks'
DIGEST = hashlib.sha256(FBX_BYTES).hexdigest()
RIG = 'CoshaRig'


def payload():
    def bone(name, parent, head, length, terminal, restore):
        return {'name': name, 'parent': parent, 'head': head,
                'axes': [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                'length': length, 'terminal': terminal, 'restore': restore}

    return {'schema': lengths.SCHEMA, 'space': lengths.SPACE,
            'fbx_sha256': DIGEST, 'rig_name': RIG,
            'bones': [bone('Root', None, [0.0, 0.0, 0.0], 0.5, False, False),
                      bone('hand.L', 'Root', [0.3, 0.0, 1.0], 0.06, False, False),
                      bone('thumb.03.L', 'hand.L', [0.35, 0.02, 1.0], 0.038, True, True),
                      bone('eye.L', 'Root', [0.03, 0.0, 1.5], 0.055, True, False)]}


class ValidateTests(unittest.TestCase):
    def assert_rejected(self, value, digest=DIGEST, rig=RIG):
        with self.assertRaises(lengths.BoneLengthError):
            lengths._validate(value, digest, rig)

    def test_complete_payload_returns_exact_named_records(self):
        value = payload()
        records = lengths._validate(value, DIGEST, RIG)
        self.assertEqual(set(records), {'Root', 'hand.L', 'thumb.03.L', 'eye.L'})
        self.assertIs(records['thumb.03.L'], value['bones'][2])
        self.assertTrue(records['thumb.03.L']['restore'])
        self.assertFalse(records['eye.L']['restore'])

    def test_proper_rotated_axes_and_uppercase_caller_digest_are_valid(self):
        value = payload()
        value['bones'][2]['axes'] = [[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
        self.assertEqual(len(lengths._validate(value, DIGEST.upper(), RIG)), 4)

    def test_schema_space_hash_and_rig_are_exact(self):
        cases = [('schema', 'cdesigner.bone-rest-lengths/2'), ('space', 'bone-local'),
                 ('fbx_sha256', '0' * 64), ('fbx_sha256', DIGEST.upper()),
                 ('fbx_sha256', 'g' * 64), ('fbx_sha256', DIGEST[:-1]),
                 ('rig_name', 'CoshaRig.001'), ('rig_name', 'cosharig')]
        for field, wrong in cases:
            with self.subTest(field=field, wrong=wrong):
                value = payload()
                value[field] = wrong
                self.assert_rejected(value)

    def test_different_requested_hash_or_rig_is_rejected(self):
        self.assert_rejected(payload(), digest='f' * 64)
        self.assert_rejected(payload(), rig='OtherRig')

    def test_missing_or_invalid_skeleton_is_rejected(self):
        for bones in (None, [], {}, [None], ['thumb.03.L']):
            with self.subTest(bones=bones):
                value = payload()
                value['bones'] = bones
                self.assert_rejected(value)

    def test_duplicate_names_are_rejected(self):
        value = payload()
        value['bones'].append(copy.deepcopy(value['bones'][2]))
        self.assert_rejected(value)

    def test_invalid_names_or_parents_are_rejected(self):
        for field, wrong in (('name', ''), ('name', 17), ('parent', ''),
                             ('parent', 17), ('parent', 'Missing'), ('parent', 'thumb.03.L')):
            with self.subTest(field=field, wrong=wrong):
                value = payload()
                value['bones'][2][field] = wrong
                self.assert_rejected(value)

    def test_head_and_axes_require_three_finite_numbers(self):
        for wrong in (None, [0, 0], (0, 0, 0), [0, 0, True],
                      [0, 0, float('nan')], [0, 0, float('inf')], [0, 0, float('-inf')]):
            for field in ('head', 'axis'):
                with self.subTest(field=field, wrong=wrong):
                    value = payload()
                    if field == 'head':
                        value['bones'][2]['head'] = wrong
                    else:
                        value['bones'][2]['axes'][0] = wrong
                    self.assert_rejected(value)

    def test_non_positive_non_finite_or_boolean_lengths_are_rejected(self):
        for wrong in (0, -0.038, float('nan'), float('inf'), float('-inf'), True, '0.038'):
            with self.subTest(wrong=wrong):
                value = payload()
                value['bones'][2]['length'] = wrong
                self.assert_rejected(value)

    def test_invalid_axis_inventory_and_non_orthonormal_frames_are_rejected(self):
        cases = [None, [], [[1, 0, 0], [0, 1, 0]],
                 [[2, 0, 0], [0, 1, 0], [0, 0, 1]],
                 [[1, 0, 0], [1, 0, 0], [0, 0, 1]]]
        for wrong in cases:
            with self.subTest(wrong=wrong):
                value = payload()
                value['bones'][2]['axes'] = wrong
                self.assert_rejected(value)

    def test_reflected_orthonormal_axes_are_rejected(self):
        value = payload()
        value['bones'][2]['axes'][2] = [0, 0, -1]
        self.assert_rejected(value)

    def test_terminal_inventory_matches_actual_children(self):
        for index, wrong in ((0, True), (1, True), (2, False), (3, False)):
            with self.subTest(index=index, terminal=wrong):
                value = payload()
                value['bones'][index]['terminal'] = wrong
                value['bones'][index]['restore'] = False
                self.assert_rejected(value)

    def test_restore_requires_a_terminal_and_boolean_flags(self):
        for index, field, wrong in ((1, 'restore', True), (2, 'restore', 1),
                                    (2, 'terminal', 1), (2, 'terminal', None)):
            with self.subTest(index=index, field=field, wrong=wrong):
                value = payload()
                value['bones'][index][field] = wrong
                self.assert_rejected(value)


class LoadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bone-rest-lengths-contract-')
        self.addCleanup(temporary.cleanup)
        self.model = Path(temporary.name) / 'Cosha.fbx'
        self.model.write_bytes(FBX_BYTES)
        self.manifest = self.model.with_suffix('.cdesigner.json')

    def report(self):
        return {'schema': 'cdesigner.unity-character.v1', 'filename': self.model.name,
                'files': {self.model.name: DIGEST}, 'bone_rest_lengths': payload()}

    def write_report(self, value):
        self.manifest.write_text(json.dumps(value), encoding='utf-8')

    def load(self, digest=DIGEST, rig=RIG):
        return lengths.load(str(self.model), digest, rig)

    def assert_rejected(self):
        with self.assertRaises(lengths.BoneLengthError):
            self.load()

    def test_legacy_missing_manifest_returns_none(self):
        self.assertFalse(self.manifest.exists())
        self.assertIsNone(self.load())

    def test_legacy_missing_block_returns_none_without_new_identity_requirements(self):
        for value in ({}, {'schema': 'older-export', 'filename': 'Old.fbx', 'files': []}):
            with self.subTest(value=value):
                self.write_report(value)
                self.assertIsNone(self.load())

    def test_complete_hash_bound_manifest_loads_payload(self):
        value = self.report()
        self.write_report(value)
        self.assertEqual(self.load(), value['bone_rest_lengths'])
        self.assertEqual(self.load(digest=DIGEST.upper()), value['bone_rest_lengths'])

    def test_manifest_schema_filename_and_files_gates_are_exact(self):
        cases = [('schema', 'cdesigner.unity-character.v2'),
                 ('filename', 'Other.fbx'), ('filename', 'cosha.fbx'),
                 ('filename', str(self.model)), ('files', None), ('files', []),
                 ('files', {}), ('files', {'Other.fbx': DIGEST}),
                 ('files', {self.model.name: '0' * 64}),
                 ('files', {self.model.name: DIGEST.upper()})]
        for field, wrong in cases:
            with self.subTest(field=field, wrong=wrong):
                value = self.report()
                value[field] = wrong
                self.write_report(value)
                self.assert_rejected()

    def test_block_model_hash_and_rig_must_match_manifest_request(self):
        for field, wrong in (('fbx_sha256', '0' * 64), ('rig_name', 'CoshaRig.001')):
            with self.subTest(field=field):
                value = self.report()
                value['bone_rest_lengths'][field] = wrong
                self.write_report(value)
                self.assert_rejected()

    def test_malformed_present_block_is_not_treated_as_legacy(self):
        for block in (None, {}, []):
            with self.subTest(block=block):
                value = self.report()
                value['bone_rest_lengths'] = block
                self.write_report(value)
                self.assert_rejected()

    def test_malformed_json_and_non_object_manifest_are_rejected(self):
        self.manifest.write_text('{malformed JSON', encoding='utf-8')
        self.assert_rejected()
        for value in (None, [], 'not a report'):
            with self.subTest(value=value):
                self.write_report(value)
                self.assert_rejected()


if __name__ == '__main__':
    unittest.main(verbosity=2)
