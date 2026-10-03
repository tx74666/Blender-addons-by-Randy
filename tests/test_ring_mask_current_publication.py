"""Current Ring publication retains native provenance and concurrent edits."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/randy_node_assets"))
import finalize_ring_mask_current as pub


class CurrentRingPublicationTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        sources = [*pub.ROLES.values(), "tools/randy_node_assets/ring_boundary.py"]
        for relative in sources:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# exact publication fixture\r\n")
        source = root / "node_library/assets/Randy_Arc_Mask.blend"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"Arc 020 native fixture")
        arc_report = root / "node_library/validation/arc_mask.json"
        arc_report.parent.mkdir(parents=True)
        arc_report.write_text(json.dumps({"passed": True, "state": "complete",
                                         "asset_sha256": pub.digest(source.read_bytes()),
                                         "asset_metadata": {"version": "0.2.0"}}))
        radial_report = arc_report.with_name("ring_mask.json")
        radial_report.write_bytes(b"historical radial verification must survive")
        manifest = root / "node_library/manifest.json"
        unrelated = {"id": "personal", "unchanged": "personal asset metadata"}
        manifest.write_text(json.dumps({"library_version": "0.3.0", "unrelated": "keep",
                                        "assets": [{"id": "arc_mask"}, unrelated],
                                        "bundles": [{"id": "arc_mask"}, unrelated]}))
        candidate = root / "build/Randy_Ring_Mask.blend"
        candidate.parent.mkdir()
        candidate.write_bytes(b"fresh current Ring fixture")
        preview = root / "build/preview.png"
        preview.write_bytes(b"\x89PNG\r\n\x1a\nrender fixture")
        inputs = [{"name": name, "type": kind, "default": value} for (name, kind), value in
                  zip(pub.INPUTS, (.6, .08, 0, 0, 360))]
        report = root / "build/report.json"
        report.write_text(json.dumps({
            "passed": True, "state": "complete", "asset_sha256": pub.digest(candidate.read_bytes()),
            "recursive_graph_matches_arc_0_2_0": True, "shader_sample_count": 46,
            "source_arc_asset": str(source), "source_arc_sha256": pub.digest(source.read_bytes()),
            "compatibility_baseline": {"path": pub.BASELINE, "sha256": pub.digest(source.read_bytes())},
            "tests": [{"name": "fixture numerical evidence", "passed": True}],
            "asset_metadata": {"name": "Ring Mask", "version": "0.2.1", "catalog_id": pub.CATALOG_ID,
                               "color_tag": "TEXTURE", "inputs": inputs,
                               "outputs": [{"name": n, "type": t} for n, t in pub.OUTPUTS]},
            "source_dependencies_sha256": {p: pub.digest((root / p).read_bytes(), text=True) for p in
                                            (sources[0], sources[1], sources[-1])},
            "render_preview": str(preview),
        }))
        originals = {manifest: manifest.read_bytes(), source: source.read_bytes(),
                     arc_report: arc_report.read_bytes(), radial_report: radial_report.read_bytes()}
        return root, candidate, report, manifest, originals

    def publish(self, root, candidate, report):
        with patch.object(pub, "ROOT", root):
            return pub.finalize(candidate, report, root / "backups")

    def test_success_preserves_arc_evidence_and_original_radial_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, originals = self.fixture(folder)
            result = self.publish(root, candidate, report)
            self.assertTrue(result["passed"])
            inventory = json.loads(manifest.read_text())
            self.assertEqual(inventory["library_version"], "0.3.1")
            self.assertEqual(inventory["unrelated"], "keep")
            self.assertEqual([i["id"] for i in inventory["assets"]], ["ring_mask", "personal"])
            self.assertEqual(inventory["assets"][0]["inputs"][-1]["default"], 360)
            self.assertEqual(inventory["bundles"][0]["verification"], "node_library/validation/ring_mask_current.json")
            self.assertEqual((root / pub.BASELINE).read_bytes(), originals[root / "node_library/assets/Randy_Arc_Mask.blend"])
            self.assertEqual((root / pub.BASELINE_REPORT).read_bytes(), originals[root / "node_library/validation/arc_mask.json"])
            for path, old in originals.items():
                if path != manifest:
                    self.assertEqual(path.read_bytes(), old)

    def test_late_manifest_failure_rolls_back_owned_files_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, originals = self.fixture(folder)
            writer = pub._write_checked

            def fail_manifest(path, *args):
                if path == manifest:
                    raise OSError("injected manifest failure")
                return writer(path, *args)

            with patch.object(pub, "_write_checked", side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, "injected manifest failure"):
                    self.publish(root, candidate, report)
            for path, old in originals.items():
                self.assertEqual(path.read_bytes(), old)
            for relative in (pub.BASELINE, pub.BASELINE_REPORT, "node_library/assets/Randy_Ring_Mask.blend",
                             "node_library/validation/ring_mask_current.json", "node_library/previews/ring_mask_current.png"):
                self.assertFalse((root / relative).exists())

    def test_external_edit_after_write_survives_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, originals = self.fixture(folder)
            target = root / "node_library/assets/Randy_Ring_Mask.blend"
            writer = pub._write_checked

            def external_edit(path, *args):
                if path == manifest:
                    target.write_bytes(b"externally saved Ring")
                    manifest.write_bytes(b"externally saved manifest")
                    raise OSError("injected concurrent change")
                return writer(path, *args)

            with patch.object(pub, "_write_checked", side_effect=external_edit):
                with self.assertRaises(OSError) as error:
                    self.publish(root, candidate, report)
            self.assertEqual(target.read_bytes(), b"externally saved Ring")
            self.assertEqual(manifest.read_bytes(), b"externally saved manifest")
            self.assertIn("Rollback skipped externally changed publication", " ".join(error.exception.__notes__))

    def test_stale_baseline_source_or_missing_graph_proof_is_rejected(self):
        for mutation in ("source", "proof", "default", "source_dependency"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root, candidate, report, manifest, originals = self.fixture(folder)
                evidence = json.loads(report.read_text())
                if mutation == "source":
                    (root / "node_library/assets/Randy_Arc_Mask.blend").write_bytes(b"changed Arc")
                elif mutation == "proof":
                    evidence["recursive_graph_matches_arc_0_2_0"] = False
                elif mutation == "default":
                    evidence["asset_metadata"]["inputs"][-1]["default"] = 180
                else:
                    (root / pub.ROLES["build"]).write_bytes(b"source changed after verification")
                report.write_text(json.dumps(evidence))
                with self.assertRaises(ValueError):
                    self.publish(root, candidate, report)
                self.assertEqual(manifest.read_bytes(), originals[manifest])
                self.assertFalse((root / "node_library/assets/Randy_Ring_Mask.blend").exists())

    def test_report_changed_during_preparation_cannot_rewrite_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root, candidate, report, manifest, originals = self.fixture(folder)
            original_read = pub.read_optional
            arc_report = root / "node_library/validation/arc_mask.json"

            def external_edit(path):
                if path == root / "node_library/assets/Randy_Ring_Mask.blend":
                    arc_report.write_bytes(b"changed report after provenance read")
                return original_read(path)

            with patch.object(pub, "read_optional", side_effect=external_edit):
                with patch.object(pub, "_write_checked") as writer:
                    with self.assertRaisesRegex(ValueError, "Source or report changed"):
                        self.publish(root, candidate, report)
                    writer.assert_not_called()
            self.assertEqual(arc_report.read_bytes(), b"changed report after provenance read")
            self.assertEqual(manifest.read_bytes(), originals[manifest])


if __name__ == "__main__":
    unittest.main()
