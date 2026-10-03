"""Publication rollback preserves concurrent edits and exact prior assets."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/randy_node_assets"))
import finalize_native_ring_nodes as finalizer


class NativeRingFinalizationTests(unittest.TestCase):
    def fixture(self, directory, *, existing=True):
        root = Path(directory)
        for relative in ("tools/randy_node_assets/build_arc_mask.py", "tools/randy_node_assets/verify_arc_mask.py",
                         "tools/randy_node_assets/deploy_ring_nodes.py"):
            source = root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"# exact source fixture\r\n")
        ring = root / "node_library/dependencies/Randy_Ring_Mask.blend"
        ring.parent.mkdir(parents=True, exist_ok=True)
        ring.write_bytes(b"original unchanged radial asset")
        manifest = root / "node_library/manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "library_version": "0.1.2",
                                       "source_hash_mode": "utf8-lf", "bundles": [], "assets": []}), encoding="utf-8")
        candidate = root / "build/Randy_Arc_Mask.blend"
        candidate.parent.mkdir()
        candidate.write_bytes(b"validated new native arc asset")
        preview = root / "build/arc-examples.png"
        preview.write_bytes(b"\x89PNG\r\n\x1a\nnew preview fixture")
        report = root / "build/arc-validation.json"
        dependency = "tools/randy_node_assets/build_arc_mask.py"
        report.write_text(json.dumps({
            "passed": True, "state": "complete", "asset_sha256": finalizer.digest(candidate.read_bytes()),
            "asset_metadata": {"name": "Arc Mask", "catalog_id": finalizer.CATALOG_ID},
            "source_dependencies_sha256": {dependency: finalizer.digest((root / dependency).read_bytes(), text=True)},
            "ring_asset_sha256": finalizer.digest(ring.read_bytes()),
            "examples": {"image": str(preview)}, "shader_sample_count": 1,
            "tests": [{"name": "passed mocked shader evidence", "passed": True}],
        }), encoding="utf-8")
        destinations = (root / "node_library/assets/Randy_Arc_Mask.blend",
                        root / "node_library/previews/arc_mask.png",
                        root / "node_library/validation/arc_mask.json")
        originals = {}
        for index, destination in enumerate(destinations):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if existing:
                destination.write_bytes(("old publication " + str(index)).encode())
                originals[destination] = destination.read_bytes()
            else:
                originals[destination] = None
        originals[manifest] = manifest.read_bytes()
        return root, candidate, report, manifest, destinations, originals

    def publish(self, root, candidate, report):
        with patch.object(finalizer, "ROOT", root):
            return finalizer.finalize("arc_mask", candidate, report, root / "backups")

    def test_successful_publication_records_exact_hashes_and_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root, candidate, report, manifest, destinations, originals = self.fixture(directory)
            result = self.publish(root, candidate, report)
            self.assertTrue(result["passed"])
            self.assertEqual(destinations[0].read_bytes(), candidate.read_bytes())
            saved = json.loads(destinations[2].read_text())
            inventory = json.loads(manifest.read_text())
            self.assertEqual(saved["asset_sha256"], inventory["bundles"][0]["sha256"])
            self.assertEqual(inventory["assets"][0]["version"], "0.1.1")
            self.assertEqual(inventory["library_version"], "0.2.1")
            backup = Path(result["backup"])
            for path, contents in originals.items():
                self.assertEqual((backup / path.relative_to(root)).read_bytes(), contents)

    def test_manifest_failure_restores_each_existing_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root, candidate, report, manifest, destinations, originals = self.fixture(directory)
            original_write = finalizer._write_checked

            def fail_manifest(path, *args):
                if path == manifest:
                    raise OSError("injected manifest failure")
                return original_write(path, *args)

            with patch.object(finalizer, "_write_checked", side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, "injected manifest failure"):
                    self.publish(root, candidate, report)
            for path, contents in originals.items():
                self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(candidate.read_bytes(), b"validated new native arc asset")

    def test_manifest_failure_removes_only_new_owned_publication_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root, candidate, report, manifest, destinations, originals = self.fixture(directory, existing=False)
            original_write = finalizer._write_checked

            def fail_manifest(path, *args):
                if path == manifest:
                    raise OSError("injected manifest failure")
                return original_write(path, *args)

            with patch.object(finalizer, "_write_checked", side_effect=fail_manifest):
                with self.assertRaises(OSError):
                    self.publish(root, candidate, report)
            self.assertTrue(all(not path.exists() for path in destinations))
            self.assertEqual(manifest.read_bytes(), originals[manifest])
            self.assertEqual((root / "node_library/dependencies/Randy_Ring_Mask.blend").read_bytes(), b"original unchanged radial asset")

    def test_concurrent_asset_and_report_edits_survive_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root, candidate, report, manifest, destinations, originals = self.fixture(directory)
            original_write = finalizer._write_checked

            def fail_after_external_edits(path, *args):
                if path == manifest:
                    destinations[0].write_bytes(b"concurrent asset editor")
                    destinations[2].write_bytes(b"concurrent report editor")
                    manifest.write_bytes(b"concurrent manifest editor")
                    raise OSError("injected manifest failure")
                return original_write(path, *args)

            with patch.object(finalizer, "_write_checked", side_effect=fail_after_external_edits):
                with self.assertRaises(OSError) as caught:
                    self.publish(root, candidate, report)
            self.assertEqual(destinations[0].read_bytes(), b"concurrent asset editor")
            self.assertEqual(destinations[2].read_bytes(), b"concurrent report editor")
            self.assertEqual(manifest.read_bytes(), b"concurrent manifest editor")
            self.assertEqual(destinations[1].read_bytes(), originals[destinations[1]])
            self.assertEqual(len(caught.exception.__notes__), 2)

    def test_new_file_replaced_with_same_bytes_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root, candidate, report, manifest, destinations, originals = self.fixture(directory, existing=False)
            original_write = finalizer._write_checked

            def replace_asset_then_fail(path, *args):
                if path == manifest:
                    replacement = destinations[0].with_suffix(".external")
                    replacement.write_bytes(destinations[0].read_bytes())
                    os.replace(replacement, destinations[0])
                    raise OSError("injected manifest failure")
                return original_write(path, *args)

            with patch.object(finalizer, "_write_checked", side_effect=replace_asset_then_fail):
                with self.assertRaises(OSError) as caught:
                    self.publish(root, candidate, report)
            self.assertEqual(destinations[0].read_bytes(), candidate.read_bytes())
            self.assertFalse(destinations[1].exists())
            self.assertFalse(destinations[2].exists())
            self.assertEqual(manifest.read_bytes(), originals[manifest])
            self.assertIn("Rollback skipped externally changed publication", caught.exception.__notes__[0])

    def test_manifest_edit_after_read_is_rejected_before_any_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root, candidate, report, manifest, destinations, originals = self.fixture(directory)
            original_interface = finalizer.interface

            def edit_manifest(kind):
                manifest.write_bytes(b"concurrent manifest editor")
                return original_interface(kind)

            with patch.object(finalizer, "interface", side_effect=edit_manifest):
                with patch.object(finalizer, "_write_checked") as writes:
                    with self.assertRaisesRegex(RuntimeError, "Destination changed before publication"):
                        self.publish(root, candidate, report)
                    writes.assert_not_called()
            self.assertEqual(manifest.read_bytes(), b"concurrent manifest editor")
            for path in destinations:
                self.assertEqual(path.read_bytes(), originals[path])


if __name__ == "__main__":
    unittest.main()
