"""Native boundary-data publication preserves exact evidence and concurrent edits."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/randy_node_assets"))
import finalize_extend_mask as finalizer


class ExtendMaskPublicationTests(unittest.TestCase):
    def fixture(self, folder, *, kind="extend_mask", existing=True):
        root = Path(folder)
        spec = finalizer.SPECS[kind]
        sources = ["tools/randy_node_assets/build_" + kind + ".py",
                   "tools/randy_node_assets/verify_" + kind + ".py",
                   "tools/randy_node_assets/" + spec["deploy"],
                   "tools/randy_node_assets/ring_boundary.py"]
        for relative in sources:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# isolated exact source fixture\r\n")
        manifest = root / "node_library/manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"library_version": "0.2.1", "bundles": [], "assets": [],
                                        "unrelated_metadata": "must survive"}), encoding="utf-8")
        candidate = root / "build" / spec["filename"]
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"fresh numerically verified asset")
        preview = root / "build/preview.png"
        preview.write_bytes(b"\x89PNG\r\n\x1a\nverified sample preview")
        interface = {direction: [{"name": name, "type": socket_type} for name, socket_type in spec[direction]]
                     for direction in ("inputs", "outputs")}
        if kind == "extend_mask":
            interface["inputs"][1].update(default="Inner", choices=["Inner", "Outer", "Both", "Outline"])
            interface["inputs"][2].update(default=.01)
        report = root / "build/report.json"
        report.write_text(json.dumps({
            "passed": True, "state": "complete", "asset_sha256": finalizer.digest(candidate.read_bytes()),
            "asset_metadata": {"name": spec["name"], "version": spec["version"],
                               "catalog_id": finalizer.CATALOG_ID, "color_tag": "TEXTURE", **interface},
            "source_dependencies_sha256": {relative: finalizer.digest((root / relative).read_bytes(), text=True)
                                            for relative in (sources[0], sources[1], sources[3])},
            "render_preview": str(preview), "shader_sample_count": 3,
            "tests": [{"name": "actual numerical sample fixture", "passed": True}],
        }), encoding="utf-8")
        targets = [root / "node_library/assets" / spec["filename"],
                   root / "node_library/previews" / (kind + ".png"),
                   root / "node_library/validation" / (kind + ".json")]
        originals = {manifest: manifest.read_bytes()}
        for index, path in enumerate(targets):
            path.parent.mkdir(parents=True, exist_ok=True)
            if existing:
                if index == 2:
                    data = json.dumps({"asset_metadata": {"version": "0.1.1" if kind == "arc_mask" else "0.0.0"},
                                       "historical": "do not rewrite as new numerical evidence"}).encode()
                else:
                    data = ("old publication " + str(index)).encode()
                path.write_bytes(data)
                originals[path] = data
            else:
                originals[path] = None
        return root, candidate, report, manifest, targets, originals

    def publish(self, root, candidate, report, *, kind="extend_mask"):
        with patch.object(finalizer, "ROOT", root):
            return finalizer.finalize(kind, candidate, report, root / "backups")

    def assert_restored(self, originals):
        for path, data in originals.items():
            if data is None:
                self.assertFalse(path.exists())
            else:
                self.assertEqual(path.read_bytes(), data)

    def test_success_preserves_menu_metadata_and_exact_source_report_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, targets, originals = self.fixture(folder)
            result = self.publish(root, candidate, report)
            self.assertTrue(result["passed"])
            inventory = json.loads(manifest.read_text())
            evidence = json.loads(targets[2].read_text())
            self.assertEqual(inventory["library_version"], "0.3.0")
            self.assertEqual(inventory["unrelated_metadata"], "must survive")
            self.assertEqual(inventory["bundles"][0]["sha256"], evidence["asset_sha256"])
            self.assertEqual(inventory["assets"][0]["inputs"][1]["default"], "Inner")
            self.assertEqual(inventory["assets"][0]["inputs"][1]["choices"], ["Inner", "Outer", "Both", "Outline"])
            self.assertEqual(targets[0].read_bytes(), candidate.read_bytes())
            backup = Path(result["backup"])
            for path, data in originals.items():
                self.assertEqual((backup / path.relative_to(root)).read_bytes(), data)

    def test_arc_publication_keeps_original_mask_first_and_archives_exact_011_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, targets, originals = self.fixture(folder, kind="arc_mask")
            result = self.publish(root, candidate, report, kind="arc_mask")
            self.assertEqual(result["version"], "0.2.0")
            entry = json.loads(manifest.read_text())["assets"][0]
            self.assertEqual([item["name"] for item in entry["outputs"]], ["Mask", "Ring Data"])
            self.assertEqual((root / "node_library/validation/arc_mask_0_1_1_baseline.json").read_bytes(), originals[targets[2]])
            evidence = json.loads(targets[2].read_text())
            self.assertEqual(evidence["previous_verification_sha256"], finalizer.digest(originals[targets[2]], text=True))
            self.assertEqual(evidence["previous_verification_hash_mode"], "utf8-lf")

    def test_manifest_failure_restores_all_prior_publication_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, targets, originals = self.fixture(folder)
            writer = finalizer._write_checked

            def fail_manifest(path, *args):
                if path == manifest:
                    raise OSError("injected manifest failure")
                return writer(path, *args)

            with patch.object(finalizer, "_write_checked", side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, "injected manifest failure"):
                    self.publish(root, candidate, report)
            self.assert_restored(originals)

    def test_manifest_failure_removes_only_new_owned_publication_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, targets, originals = self.fixture(folder, existing=False)
            writer = finalizer._write_checked

            def fail_manifest(path, *args):
                if path == manifest:
                    raise OSError("injected manifest failure")
                return writer(path, *args)

            with patch.object(finalizer, "_write_checked", side_effect=fail_manifest):
                with self.assertRaises(OSError):
                    self.publish(root, candidate, report)
            self.assert_restored(originals)

    def test_concurrent_asset_and_manifest_edits_survive_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, targets, originals = self.fixture(folder)
            writer = finalizer._write_checked

            def concurrent_save(path, *args):
                if path == manifest:
                    targets[0].write_bytes(b"concurrent asset editor")
                    manifest.write_bytes(b"concurrent manifest editor")
                    raise OSError("injected late failure")
                return writer(path, *args)

            with patch.object(finalizer, "_write_checked", side_effect=concurrent_save):
                with self.assertRaises(OSError) as caught:
                    self.publish(root, candidate, report)
            self.assertEqual(targets[0].read_bytes(), b"concurrent asset editor")
            self.assertEqual(manifest.read_bytes(), b"concurrent manifest editor")
            self.assertEqual(targets[1].read_bytes(), originals[targets[1]])
            self.assertEqual(targets[2].read_bytes(), originals[targets[2]])
            self.assertIn("Rollback skipped externally changed publication", caught.exception.__notes__[0])

    def test_externally_replaced_new_file_with_same_bytes_survives_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, targets, originals = self.fixture(folder, existing=False)
            writer = finalizer._write_checked

            def concurrent_replace(path, *args):
                if path == manifest:
                    external = targets[0].with_suffix(".external")
                    external.write_bytes(targets[0].read_bytes())
                    os.replace(external, targets[0])
                    raise OSError("injected late failure")
                return writer(path, *args)

            with patch.object(finalizer, "_write_checked", side_effect=concurrent_replace):
                with self.assertRaises(OSError):
                    self.publish(root, candidate, report)
            self.assertEqual(targets[0].read_bytes(), candidate.read_bytes())
            self.assertFalse(targets[1].exists())
            self.assertFalse(targets[2].exists())
            self.assertEqual(manifest.read_bytes(), originals[manifest])

    def test_stale_manifest_is_rejected_before_first_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, targets, originals = self.fixture(folder)
            original_read = finalizer.read_optional

            def edit_during_snapshot(path):
                if path == targets[0]:
                    manifest.write_bytes(b"external manifest change")
                return original_read(path)

            with patch.object(finalizer, "read_optional", side_effect=edit_during_snapshot):
                with patch.object(finalizer, "_write_checked") as writer:
                    with self.assertRaisesRegex(ValueError, "Destination changed before publication"):
                        self.publish(root, candidate, report)
                    writer.assert_not_called()
            self.assertEqual(manifest.read_bytes(), b"external manifest change")
            for path in targets:
                self.assertEqual(path.read_bytes(), originals[path])

    def test_unverified_interface_or_changed_source_cannot_publish(self):
        for mutation in ("interface", "source", "samples"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root, candidate, report, manifest, targets, originals = self.fixture(folder)
                evidence = json.loads(report.read_text())
                if mutation == "interface":
                    evidence["asset_metadata"]["outputs"][1]["type"] = "NodeSocketFloat"
                elif mutation == "source":
                    (root / "tools/randy_node_assets/build_extend_mask.py").write_bytes(b"changed after validation")
                else:
                    evidence["shader_sample_count"] = 0
                report.write_text(json.dumps(evidence))
                with self.assertRaises(ValueError):
                    self.publish(root, candidate, report)
                self.assert_restored(originals)
                self.assertFalse((root / "backups").exists())

    def test_arc_report_changed_after_provenance_read_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, targets, originals = self.fixture(folder, kind="arc_mask")
            original_read = finalizer.read_optional

            def edit_during_snapshot(path):
                if path == targets[0]:
                    targets[2].write_bytes(b"concurrent Arc verification edit")
                return original_read(path)

            with patch.object(finalizer, "read_optional", side_effect=edit_during_snapshot):
                with patch.object(finalizer, "_write_checked") as writer:
                    with self.assertRaisesRegex(ValueError, "Destination changed before publication"):
                        self.publish(root, candidate, report, kind="arc_mask")
                    writer.assert_not_called()
            self.assertEqual(targets[2].read_bytes(), b"concurrent Arc verification edit")
            self.assertEqual(manifest.read_bytes(), originals[manifest])
            self.assertFalse((root / "node_library/validation/arc_mask_0_1_1_baseline.json").exists())


if __name__ == "__main__":
    unittest.main()
