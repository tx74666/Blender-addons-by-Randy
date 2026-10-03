"""Original native Ring Group factory evidence is exact and narrowly scoped."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/randy_node_assets"))
import verify_ring_group_compatibility as verifier


class RingGroupFactoryCompatibilityTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        original = ("\n".join(name + " = " + repr(name + " contract") for name in verifier.CONTRACT_CONSTANTS)
                    + "\n\n" + "\n\n".join("def " + name + "():\n    return 'factory'"
                                             for name in verifier.FACTORY_FUNCTIONS) + "\n").encode()
        current = original + b"\n# An edit-time helper does not change the saved factory.\ndef editing_helper():\n    return 'new UI'\n"
        source = root / verifier.GENERATOR
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(current)
        archive = root / "dist/rr_helper-0.2.44.zip"
        archive.parent.mkdir()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr(verifier.ARCHIVE_GENERATOR, original)
        receipt = root / verifier.EDITING_PROOF
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"passed": True, "baseline_archive": "dist/rr_helper-0.2.44.zip",
                                       "factory_functions_ast_equivalent": list(verifier.FACTORY_FUNCTIONS),
                                       "factory_contract_constants_equivalent": list(verifier.CONTRACT_CONSTANTS)}), encoding="utf-8")
        return root, original, current, source, archive, receipt

    def test_exact_archived_birth_source_matches_current_factory_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root, original, current, source, archive, receipt = self.fixture(folder)
            original_hash = verifier.digest(original, text=True)
            proof = verifier.original_factory_equivalence(original_hash, root=root)
            self.assertTrue(proof["passed"])
            self.assertEqual(proof["original_embedded_generator_sha256"], original_hash)
            self.assertEqual(proof["archived_generator_sha256"], original_hash)
            self.assertEqual(proof["current_generator_sha256"], verifier.digest(current, text=True))
            self.assertNotEqual(proof["current_generator_sha256"], proof["archived_generator_sha256"])
            self.assertEqual(proof["matching_archive_sha256"], verifier.digest(archive.read_bytes()))
            self.assertFalse(proof["metadata_properties_modified"])
            self.assertFalse(proof["native_asset_rebuilt"])
            self.assertEqual(source.read_bytes(), current)

    def test_change_to_factory_calculation_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root, original, current, source, archive, receipt = self.fixture(folder)
            source.write_bytes(current.replace(b"return 'factory'", b"return 'different calculation'", 1))
            with self.assertRaisesRegex(ValueError, "factory differs"):
                verifier.original_factory_equivalence(verifier.digest(original, text=True), root=root)

    def test_change_to_contract_constant_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root, original, current, source, archive, receipt = self.fixture(folder)
            source.write_bytes(current.replace(b"_KIND contract", b"different kind", 1))
            with self.assertRaisesRegex(ValueError, "factory differs.*_KIND"):
                verifier.original_factory_equivalence(verifier.digest(original, text=True), root=root)

    def test_unknown_embedded_generator_hash_has_no_archive_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root, original, current, source, archive, receipt = self.fixture(folder)
            with self.assertRaisesRegex(ValueError, "No repository release archive"):
                verifier.original_factory_equivalence("f" * 64, root=root)

    def test_prior_receipt_cannot_expand_or_reduce_the_comparison_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root, original, current, source, archive, receipt = self.fixture(folder)
            evidence = json.loads(receipt.read_text())
            evidence["factory_functions_ast_equivalent"].remove("_build_union")
            receipt.write_text(json.dumps(evidence))
            with self.assertRaisesRegex(ValueError, "different scope"):
                verifier.original_factory_equivalence(verifier.digest(original, text=True), root=root)

    def test_missing_factory_definition_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing a required factory"):
            verifier.factory_parts(b"def new_group():\n    return 'incomplete'\n")


if __name__ == "__main__":
    unittest.main()
