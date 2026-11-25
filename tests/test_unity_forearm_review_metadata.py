"""Forearm review metadata closure; synthetic reports/temp projects only.

Reuse fixture helpers without subclassing their test classes or loading their
test methods into this suite. No native proof/capability is produced here.
"""
import copy
import hashlib
import json
import unittest

import test_unity_reviewed_forearm_enable as fixture

subject = fixture.subject
write, digest, inventory = fixture.write, fixture.digest, fixture.inventory


class ForearmReviewMetadataTests(unittest.TestCase):
    setUp = fixture.ReviewedForearmEnableTests.setUp
    sidecar = fixture.ReviewedForearmEnableTests.sidecar
    bundle = fixture.ReviewedForearmEnableTests.bundle
    write_capability = fixture.ReviewedForearmEnableTests.write_capability
    capture = fixture.ReviewedForearmEnableTests.capture
    publish = fixture.ReviewedForearmEnableTests.publish
    completed = fixture.ReviewedForearmEnableTests.completed
    review = fixture.ReviewedForearmEnableTests.review
    enable_review = fixture.ReviewedForearmEnableTests.enable_review
    pin_enable_authority = fixture.ReviewedForearmEnableTests.pin_enable_authority

    def refresh_source_and_forearm_claims(self, rest, enable):
        """Refresh every synthetic hash, so a stale envelope cannot reject first."""
        self.write_capability(rest)
        packet = json.loads(enable.read_bytes())
        packet['source_snapshot_sha256'] = digest(self.source_path)
        write(enable, packet)
        self.pin_enable_authority(enable)
        proof = json.loads(self.report_path.read_bytes())
        cap = json.loads(self.cap_path.read_bytes())
        self.assertEqual(proof['source_snapshot_sha256'], digest(self.source_path))
        self.assertEqual(cap['source_snapshot_sha256'], digest(self.source_path))
        self.assertEqual(proof['reviewed_forearm_enable_source_snapshot_sha256'], digest(self.source_path))
        self.assertEqual(proof['reviewed_forearm_enable_manifest_sha256'], digest(enable))
        self.assertEqual(cap['hair_structure_verification_report_sha256'], digest(self.report_path))

    def refuse_read_only(self, message, callback):
        before = inventory(self.project)
        with self.assertRaisesRegex(ValueError, message):
            callback()
        self.assertEqual(inventory(self.project), before)
        self.assertFalse(self.completed())

    def test_valid_reference_and_definition2_metadata_admits_read_only(self):
        rest, enable = self.enable_review()
        before = inventory(self.project)
        plan = self.capture(rest)
        self.assertEqual(plan.forearm_enable_review_pin.sha256, digest(enable))
        source = json.loads(self.source_path.read_bytes())
        self.assertEqual(source['definition']['schema'], 'cdesigner.rig-source-definition/2')
        forearm = source['definition']['objects']['Cosha']['mesh']['forearm_definition']
        self.assertEqual([side['enabled'] for side in forearm['sides']], [False, False])
        self.assertEqual([forearm['authored_calibration_records'][side]['enabled'] for side in ('L', 'R')], [False, False])
        self.assertEqual(inventory(self.project), before)
        self.assertFalse(self.completed())

    def test_fresh_reference_manifest_hash_cannot_authorize_extra_metadata(self):
        rest, enable = self.enable_review()
        packet = json.loads(enable.read_bytes())
        reference = self.root / 'sealed/currentRigTrue/Character.cdesigner.json'
        record = json.loads(reference.read_bytes())
        record['unreviewed_extra_metadata'] = {'changed': True}
        write(reference, record)
        packet['reference_manifest_sha256'] = digest(reference)
        write(enable, packet)
        self.pin_enable_authority(enable)
        self.refuse_read_only('derived complete manifest changes beyond its reviewed Forearm checksum',
                              lambda: self.capture(rest))

    def test_false_payload_cannot_hide_true_authored_source_flag_with_fresh_claims(self):
        rest, enable = self.enable_review()
        original = copy.deepcopy(self.definition)
        for side in ('L', 'R'):
            with self.subTest(side=side):
                self.definition = copy.deepcopy(original)
                self.definition['objects']['Cosha']['mesh']['forearm_definition']['authored_calibration_records'][side]['enabled'] = True
                self.refresh_source_and_forearm_claims(rest, enable)
                payload = json.loads((self.stage / 'Character.forearm.json').read_bytes())
                self.assertEqual([row['enabled'] for row in payload['meshes'][0]['sides']], [False, False])
                self.assertEqual([row['enabled'] for row in self.definition['objects']['Cosha']['mesh']['forearm_definition']['sides']], [False, False])
                self.refuse_read_only('source snapshot does not prove the exact current disabled Forearm flags',
                                      lambda: self.capture(rest))

    def test_definition1_refuses_normal_capture_with_fully_fresh_envelope(self):
        self.definition['schema'] = 'cdesigner.rig-source-definition/1'
        self.write_capability()
        self.refuse_read_only('native Rig source snapshot is incomplete', self.capture)

    def test_exact46_inventory_digest_and_missing_forearm_math_refusal(self):
        expected = '6088dd93aa70b82af7ae7559d6269404b8e4713e3c57ad000486d6f4304c1a31'
        math_path = 'Assets/CharacterDesigner/Runtime/ForearmCorrectionMath.cs'
        self.assertEqual(len(subject.PROOF_SOURCE_PATHS), 46)
        self.assertIn(math_path, subject.PROOF_SOURCE_PATHS)
        self.assertEqual(subject.SOURCE_INVENTORY_SHA256, expected)
        self.assertEqual(hashlib.sha256(('\n'.join(sorted(subject.PROOF_SOURCE_PATHS)) + '\n').encode()).hexdigest(), expected)
        rest, enable = self.enable_review()
        for claimed_digest in ('retained46', 'rehashed45'):
            with self.subTest(claimed_digest=claimed_digest):
                self.refresh_source_and_forearm_claims(rest, enable)
                proof, cap = json.loads(self.report_path.read_bytes()), json.loads(self.cap_path.read_bytes())
                sources = dict(proof['hair_structure_verified_sources'])
                del sources[math_path]
                new_digest = expected if claimed_digest == 'retained46' else hashlib.sha256(('\n'.join(sorted(sources)) + '\n').encode()).hexdigest()
                for record in (proof, cap):
                    record['hair_structure_verified_sources'] = sources
                    record['hair_structure_source_inventory_sha256'] = new_digest
                write(self.report_path, proof)
                cap['hair_structure_verification_report_sha256'] = digest(self.report_path)
                write(self.cap_path, cap)
                self.refuse_read_only('exact receiver source inventory', lambda: self.capture(rest))


if __name__ == '__main__':
    unittest.main()
