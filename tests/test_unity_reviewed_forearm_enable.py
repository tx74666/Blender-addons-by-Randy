"""Exact two-flag Forearm permission tests; synthetic proof and tempfile assets only."""
import copy
import json
from pathlib import Path
import unittest

import test_unity_rig_update as fixture

subject, base = fixture.subject, fixture.base
write, digest, inventory = fixture.write, fixture.digest, fixture.inventory


class ReviewedForearmEnableTests(unittest.TestCase):
    setUp = fixture.RigUpdateTests.setUp
    sidecar = fixture.RigUpdateTests.sidecar
    bundle = fixture.RigUpdateTests.bundle
    write_capability = fixture.RigUpdateTests.write_capability
    capture = fixture.RigUpdateTests.capture
    publish = fixture.RigUpdateTests.publish
    completed = fixture.RigUpdateTests.completed
    review = fixture.RigUpdateTests.review

    def enable_review(self, candidate_edit=None):
        body = next(part for part in self.parts if part['object_name'] == 'Cosha')
        reference = {'schema': 'cdesigner.forearm/1', 'fbx': 'Character.fbx',
            'fbxSha256': digest(self.stage / 'Character.fbx'), 'meshes': [{'objectName': 'Cosha',
                'sources': [{'vertex': 0, 'ratio': .35, 'point': [1., 2., 3.], 'lowerWeight': .8}],
                'sides': [{'name': side, 'axis': {'x': 1., 'y': 0., 'z': 0.},
                    'pivot': {'x': .2, 'y': .3, 'z': .4}, 'enabled': True} for side in ('L', 'R')],
                'shapes': [{'name': 'Artist', 'deltas': [[.1, .2, .3]]}]}]}
        candidate = copy.deepcopy(reference)
        for side in candidate['meshes'][0]['sides']: side['enabled'] = False
        if candidate_edit: candidate_edit(candidate)
        old_path = self.root / 'sealed/currentRigTrue/Character.forearm.json'
        new_path = self.stage / 'Character.forearm.json'
        write(old_path, reference); write(new_path, candidate)
        self.new['files'][new_path.name] = digest(new_path)
        self.definition['objects']['Cosha']['mesh'] = {'forearm_definition': {
            'sides': [{'name': side, 'enabled': False} for side in ('L', 'R')],
            'authored_calibration_records': {side: {'enabled': False} for side in ('L', 'R')}}}
        rest_path = self.review()
        rest = json.loads(rest_path.read_bytes())
        reference_manifest = self.root / 'sealed/currentRigTrue/Character.cdesigner.json'
        reference_record = copy.deepcopy(self.new); reference_record['files'][new_path.name] = digest(old_path)
        write(reference_manifest, reference_record)
        packet = {'schema': subject.FOREARM_ENABLE_SCHEMA, 'policy': 'use_current_blender',
            'source_asset_id': self.asset, 'rig_id': self.rig, 'target_guid': self.target,
            'receiver_revision': self.revision, 'ledger_sha256': digest(self.ledger), 'ledger_file': str(self.ledger),
            'baseline_fbx_sha256': digest(self.model), 'baseline_manifest_sha256': digest(self.manifest),
            'candidate_fbx_sha256': digest(self.stage / 'Character.fbx'), 'candidate_manifest_sha256': digest(self.new_manifest),
            'baseline_manifest_file': rest['baseline_manifest_file'], 'candidate_manifest_file': rest['candidate_manifest_file'],
            'reference_manifest_file': str(reference_manifest), 'reference_manifest_sha256': digest(reference_manifest),
            'source_snapshot_sha256': digest(self.source_path),
            'reference_forearm_file': str(old_path), 'reference_forearm_sha256': digest(old_path),
            'candidate_forearm_file': str(new_path), 'candidate_forearm_sha256': digest(new_path),
            'rows': [{'part_id': body['part_id'], 'renderer_path': body['renderer_path'], 'object_name': body['object_name'],
                      'side': side, 'before': True, 'after': False} for side in ('L', 'R')]}
        path = self.root / 'review/Character.reviewed-forearm-enable.json'
        write(path, packet)
        self.pin_enable_authority(path)
        return rest_path, path

    def pin_enable_authority(self, path):
        packet = json.loads(path.read_bytes())
        proof, cap = json.loads(self.report_path.read_bytes()), json.loads(self.cap_path.read_bytes())
        proof.update({flag: True for flag in subject.FOREARM_ENABLE_PROOF_FLAGS})
        proof.update(reviewed_forearm_enable_file=str(path), reviewed_forearm_enable_manifest_sha256=digest(path),
            reviewed_forearm_enable_receiver_revision=self.revision, reviewed_forearm_enable_part_id=packet['rows'][0]['part_id'],
            reviewed_forearm_enable_sides=['L', 'R'], reviewed_forearm_enable_policy='use_current_blender',
            reviewed_forearm_enable_source_snapshot_sha256=digest(self.source_path))
        cap.update(supports_current_rig_reviewed_forearm_enable=True)
        cap.update({key: proof[key] for key in subject.FOREARM_ENABLE_FIELDS})
        write(self.report_path, proof); cap['hair_structure_verification_report_sha256'] = digest(self.report_path)
        write(self.cap_path, cap)

    def test_exact_two_flags_transport_keeps_calibration_and_formal_inputs(self):
        rest, review = self.enable_review()
        before = inventory(self.formal), self.ledger.read_bytes(), self.player.read_bytes(), self.profile.read_bytes()
        plan = self.capture(rest)
        self.assertEqual(plan.forearm_enable_review_pin.sha256, digest(review))
        self.assertTrue(subject.read_capabilities(self.model)['supports_hair_structure'])
        path = self.publish(plan); candidate = json.loads(path.read_bytes())
        self.assertEqual(candidate['rig_update']['reviewed_forearm_enable'],
            {'schema': subject.FOREARM_ENABLE_SCHEMA, 'file': review.name, 'sha256': digest(review)})
        self.assertEqual((path.parent / review.name).read_bytes(), review.read_bytes())
        self.assertNotIn(review.name, json.loads((path.parent / self.new_manifest.name).read_bytes())['files'])
        self.assertNotIn(review.name, candidate['rig_update']['required_sidecars'])
        self.assertEqual(before, (inventory(self.formal), self.ledger.read_bytes(), self.player.read_bytes(), self.profile.read_bytes()))

    def test_other_calibration_changes_refuse_even_with_fresh_synthetic_native_claim(self):
        for field in ('axis', 'pivot', 'source_weight', 'shape', 'extra_side'):
            with self.subTest(field=field):
                def edit(payload):
                    mesh = payload['meshes'][0]
                    if field in ('axis', 'pivot'): mesh['sides'][0][field]['x'] += .01
                    elif field == 'source_weight': mesh['sources'][0]['lowerWeight'] = .7
                    elif field == 'shape': mesh['shapes'][0]['deltas'][0][0] += .1
                    else: mesh['sides'].append({'name': 'extra', 'enabled': False})
                rest, _ = self.enable_review(edit)
                with self.assertRaisesRegex(ValueError, 'beyond the two'): self.capture(rest)
        self.assertFalse(self.completed())

    def test_wrong_rows_identity_policy_and_snapshot_pin_refuse(self):
        rest, path = self.enable_review(); original = json.loads(path.read_bytes())
        changes = ({'policy': 'keep_old'}, {'ledger_sha256': '0' * 64}, {'target_guid': '0' * 32},
                   {'source_snapshot_sha256': '0' * 64}, {'candidate_fbx_sha256': '0' * 64})
        for change in changes:
            with self.subTest(change=change):
                write(path, dict(original, **change)); self.pin_enable_authority(path)
                with self.assertRaises(ValueError): self.capture(rest)
        for field, value in (('before', False), ('after', True), ('side', 'X'), ('renderer_path', 'Other')):
            packet = copy.deepcopy(original); packet['rows'][0][field] = value
            write(path, packet); self.pin_enable_authority(path)
            with self.subTest(field=field), self.assertRaises(ValueError): self.capture(rest)
        self.assertFalse(self.completed())

    def test_every_forearm_native_flag_and_exact_source_pin_is_required(self):
        rest, path = self.enable_review()
        for flag in subject.FOREARM_ENABLE_PROOF_FLAGS:
            self.pin_enable_authority(path)
            proof = json.loads(self.report_path.read_bytes()); proof[flag] = False; write(self.report_path, proof)
            cap = json.loads(self.cap_path.read_bytes()); cap['hair_structure_verification_report_sha256'] = digest(self.report_path); write(self.cap_path, cap)
            with self.subTest(flag=flag), self.assertRaises(ValueError): self.capture(rest)
        self.pin_enable_authority(path); plan = self.capture(rest)
        reference = Path(json.loads(path.read_bytes())['reference_forearm_file'])
        reference.write_bytes(reference.read_bytes() + b' ')
        with self.assertRaises(ValueError): self.publish(plan)
        self.assertFalse(self.completed())

    def test_forearm_permission_cannot_stand_alone_or_redirect(self):
        rest, path = self.enable_review()
        other = self.root / 'other/Character.reviewed-forearm-enable.json'; write(other, path.read_bytes())
        with self.assertRaises(ValueError): self.capture(rest, forearm_enable_review_file=other)
        proof = json.loads(self.report_path.read_bytes()); cap = json.loads(self.cap_path.read_bytes())
        cap['supports_current_rig_reviewed_rest'] = False
        write(self.cap_path, cap)
        with self.assertRaisesRegex(ValueError, 'complete Hair'): self.capture()
        self.assertFalse(self.completed())


if __name__ == '__main__':
    unittest.main()
