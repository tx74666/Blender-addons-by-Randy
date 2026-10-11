"""Pure admission regressions; no bpy import, helper job or process launch."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / 'tools/run_character_composition_first_stage_continuation.py'
spec = importlib.util.spec_from_file_location('_composition_continuation_test', TOOL)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)

PARENT = '''def begin_isolated_export(context, inputs, destination, export_rig_name=None, *, launch_proof=None):
    snapshot = root / 'animation.blend'
    try:
        bpy.data.libraries.write(snapshot, {rig, action})
    finally:
        cleanup()

def isolate_export(context, session, item):
    return sample_effective(item)
'''
HELPERS = '''\ndef _snapshot_editor_state(rig):
    return rig.editor

def _prepare_snapshot_editor(rig, original):
    rig.property_unset('character_designer_unity_export')
'''


class SerializationAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.before, self.after = self.root / 'old', self.root / 'new'
        self.before.mkdir()
        self.after.mkdir()
        self.old_export = self.before / 'animation_composition_export.py'
        self.new_export = self.after / self.old_export.name
        self.old_export.write_text(PARENT, encoding='utf-8')
        self.new_export.write_text(PARENT.replace('        bpy.data.libraries.write',
            '        _prepare_snapshot_editor(rig, original)\n        bpy.data.libraries.write') + HELPERS, encoding='utf-8')
        self.old_manifest, self.new_manifest = self.root / 'old.json', self.root / 'new.json'
        self.owner, self.peer = self.root / 'owner.json', self.root / 'peer.json'
        self.refresh()

    def tearDown(self):
        self.tmp.cleanup()

    def row(self, path):
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def refresh(self, *, changed_functions=None, helpers=None):
        files = {'ordinary_' + str(index) + '.py': 'a' * 64 for index in range(166)}
        self.old_manifest.write_text(json.dumps({'files': {**files, self.old_export.name: self.row(self.old_export)['sha256']}}), encoding='utf-8')
        self.new_manifest.write_text(json.dumps({'files': {**files, self.new_export.name: self.row(self.new_export)['sha256']}}), encoding='utf-8')
        self.facts = {'old_projection': self.row(self.old_manifest), 'serialization_only': True,
            'changed_functions': {self.old_export.name: changed_functions or ['begin_isolated_export']},
            'added_functions': {self.old_export.name: helpers or ['_snapshot_editor_state', '_prepare_snapshot_editor']}}
        review = {'source_only': True, 'native_execution_performed': False, 'passed': True,
            'numeric_reuse_delta': self.facts, 'changed_runtime_sources': {self.old_export.name: self.row(self.new_export)}}
        for path in (self.owner, self.peer):
            path.write_text(json.dumps(review), encoding='utf-8')
        self.old = {'runtime': {'package': str(self.before), 'manifest': str(self.old_manifest),
                               'manifest_sha256': self.row(self.old_manifest)['sha256']}}
        self.fixture = {'addonPackage': str(self.after), 'runtimeManifest': str(self.new_manifest),
                        'runtimeManifestSha256': self.row(self.new_manifest)['sha256']}
        self.admission = {'owner_review': self.row(self.owner), 'peer_review': self.row(self.peer)}

    def check(self):
        return subject.validate_delta(self.old, self.fixture, self.admission)

    def test_exact_two_helpers_and_snapshot_call_are_admitted(self):
        self.assertEqual(self.check(), self.facts)

    def test_shared_numeric_function_change_is_rejected(self):
        self.new_export.write_text(self.new_export.read_text().replace('sample_effective(item)', 'sample_effective(other)'), encoding='utf-8')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'numeric functions changed'):
            self.check()

    def test_additional_begin_statement_is_rejected(self):
        self.new_export.write_text(self.new_export.read_text().replace("    snapshot = root", "    inputs['timing'] = changed\n    snapshot = root"), encoding='utf-8')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'beyond one serialization call'):
            self.check()

    def test_snapshot_call_after_write_is_rejected(self):
        self.new_export.write_text(self.new_export.read_text().replace(
            '        _prepare_snapshot_editor(rig, original)\n        bpy.data.libraries.write(snapshot, {rig, action})',
            '        bpy.data.libraries.write(snapshot, {rig, action})\n        _prepare_snapshot_editor(rig, original)'), encoding='utf-8')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'immediately precede'):
            self.check()

    def test_changed_owner_peer_facts_are_rejected(self):
        peer = json.loads(self.peer.read_text())
        peer['numeric_reuse_delta']['serialization_only'] = False
        self.peer.write_text(json.dumps(peer), encoding='utf-8')
        self.admission['peer_review'] = self.row(self.peer)
        with self.assertRaisesRegex(ValueError, 'same serialization-only'):
            self.check()

    def test_extra_helper_is_rejected(self):
        self.new_export.write_text(self.new_export.read_text() + '\ndef unauthorized():\n    return 1\n', encoding='utf-8')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'numeric functions changed'):
            self.check()


class NumericAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {'seekSeconds': [0., .137, .537], 'bWorkItemIds': ['1' * 32, '2' * 32]}
        values = {'schema': 'cdesigner.composition-effective-values/1', 'timesSeconds': [0., .01, .02, .03],
                  'channels': [['channel_' + str(index), [0., 0., 0., 0.]] for index in range(410)]}
        fingerprint = {'fingerprint_known': True, 'fingerprint': 'same'}
        cases = [{'reference': label, 'object_motion_case': obj, 'seek_seconds': seconds,
            'native_count': 217, 'b_work_item_id': self.fixture['bWorkItemIds'][index],
            'b_fingerprint': fingerprint, 'visible_c_sha256': str((label, obj, seconds)),
            'composition_export': {'effectiveChannelsSha256': subject.digest(values),
                'referenceActionBeforeSha256': label, 'referenceActionAfterSha256': label,
                'excludedReferenceEvaluation': True}}
            for index, label in enumerate(('a1', 'a2')) for obj in range(2) for seconds in self.fixture['seekSeconds']]
        self.old = {'schema': 'x.validation.character-composition-native/1', 'completed': False,
            'numeric_completed': True, 'worker_launch_performed': False,
            'fresh_workspace_cleanup': 'historical_native_state_and_rows_restored',
            'cases': cases, 'effective_values': values}
        self.old.update({key: True for key in ('same_b_across_two_a_seek_and_object_motion',
            'protected_prior_actions_unchanged', 'protected_assets_unchanged',
            'fresh_native_associations_verified', 'historical_recovery_association_unchanged')})
        for key in ('qa_fk_preflight', 'qa_fk_post_adoption'):
            self.old[key] = {'full_scope_verified': True, 'native_count': 217}

    def test_twelve_complete_numeric_cases_are_admitted(self):
        subject.validate_numeric(self.old, self.fixture)

    def test_boolean_value_is_rejected_even_with_matching_digest(self):
        self.old['effective_values']['channels'][0][1][0] = True
        for row in self.old['cases']:
            row['composition_export']['effectiveChannelsSha256'] = subject.digest(self.old['effective_values'])
        with self.assertRaisesRegex(ValueError, 'excluding booleans'):
            subject.validate_numeric(self.old, self.fixture)

    def test_string_value_is_rejected_even_with_matching_digest(self):
        self.old['effective_values']['channels'][0][1][0] = '0'
        for row in self.old['cases']:
            row['composition_export']['effectiveChannelsSha256'] = subject.digest(self.old['effective_values'])
        with self.assertRaisesRegex(ValueError, 'finite numbers'):
            subject.validate_numeric(self.old, self.fixture)

    def test_nonfinite_value_is_rejected(self):
        self.old['effective_values']['channels'][0][1][0] = float('inf')
        with self.assertRaisesRegex(ValueError, 'finite numbers'):
            subject.validate_numeric(self.old, self.fixture)

    def test_only_three_complete_samples_are_rejected(self):
        self.old['effective_values']['timesSeconds'].pop()
        for row in self.old['effective_values']['channels']:
            row[1].pop()
        with self.assertRaisesRegex(ValueError, 'Incomplete numeric'):
            subject.validate_numeric(self.old, self.fixture)


if __name__ == '__main__':
    unittest.main()
