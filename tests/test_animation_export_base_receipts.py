"""Retained sparse-export inputs participate in collection publication identity.

Reuse the existing collection fixture and its regression cases unchanged.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import test_animation_worklist_collection as fixtures


class ExportBaseReceiptTests(fixtures.CollectionTests):
    def test_recapture_cannot_be_skipped_after_artist_restores_old_pose(self):
        # Curves and the current rig proof are equal; only the saved defaults
        # differ. This used to look Unchanged after restoring the current pose.
        first = self.collection._with_export_base(deepcopy(self.proof),
            {'character_designer_animation_export_base_v1': '{"pose":"first"}'})
        second = self.collection._with_export_base(deepcopy(self.proof),
            {'character_designer_animation_export_base_v1': '{"pose":"second"}'})
        self.assertEqual(first['fingerprint'], second['fingerprint'])
        self.assertTrue(self.collection._valid_proof(first))
        self.assertFalse(self.collection._same_proof(first, second))

    def test_same_saved_defaults_keep_publication_comparison(self):
        action = {'character_designer_animation_export_base_v1': '{"pose":"saved"}'}
        first = self.collection._with_export_base(deepcopy(self.proof), action)
        second = self.collection._with_export_base(deepcopy(self.proof), action)
        self.assertTrue(self.collection._same_proof(first, second))

    def test_removing_saved_defaults_invalidates_unchanged(self):
        with_base = self.collection._with_export_base(deepcopy(self.proof),
            {'character_designer_animation_export_base_v1': '{"pose":"saved"}'})
        without_base = self.collection._with_export_base(deepcopy(self.proof), {})
        self.assertFalse(self.collection._same_proof(with_base, without_base))
        self.assertFalse(self.collection._same_proof(with_base, self.proof))

    def test_bad_metadata_and_bad_receipt_are_not_unchanged(self):
        with self.assertRaises(ValueError):
            self.collection._with_export_base(self.proof,
                {'character_designer_animation_export_base_v1': 17})
        tampered = {**self.proof, 'export_base_sha256': 'invalid'}
        self.assertFalse(self.collection._valid_proof(tampered))
        self.assertFalse(self.collection._same_proof(tampered, tampered))

    def test_export_implementation_binds_both_new_files(self):
        implementation = self.real_export_implementation()
        for name in ('animation_export_inputs.py', 'animation_export_inputs_worker.py'):
            expected = hashlib.sha256((fixtures.SOURCE / name).read_bytes()).hexdigest()
            self.assertEqual(implementation[name], expected)
        original = Path.is_file
        with patch.object(Path, 'is_file', lambda path:
                          False if path.name == 'animation_export_inputs_worker.py' else original(path)):
            with self.assertRaisesRegex(ValueError, 'implementation pair is incomplete'):
                self.real_export_implementation()


if __name__ == '__main__':
    unittest.main()
