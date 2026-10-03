"""Extend Mask deployment is scoped, guarded, and supports read-only checks."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/randy_node_assets"))
import deploy_extend_mask as deploy
import deploy_ring_mask as guarded


class ExtendMaskDeploymentTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        library = root / "library"
        library.mkdir()
        originals = {
            "Randy_Arc_Mask.blend": b"existing Arc",
            "Randy_Mix_Shaders.blend": b"existing Mixer",
            "personal.blend": b"unrelated geometry",
            "blender_assets.cats.txt": ("VERSION 1\n" + guarded.CATALOG_ID + ":Textures:Textures\n").encode(),
        }
        for filename, data in originals.items():
            (library / filename).write_bytes(data)
        asset = root / "build.blend"
        asset.write_bytes(b"passed Extend Mask candidate")
        evidence = root / "validation.json"
        evidence.write_text(json.dumps({
            "passed": True, "asset_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
            "tests": [{"name": "isolated fixture", "passed": True}],
            "asset_metadata": {"name": "Extend Mask", "catalog_id": guarded.CATALOG_ID},
        }), encoding="utf-8")
        args = SimpleNamespace(asset=str(asset), verification=str(evidence), library=str(library),
                               backups=str(root / "backups"), report=str(root / "deploy.json"), check=False)
        return root, library, originals, args

    def test_only_extend_file_changes_and_check_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root, library, originals, args = self.fixture(folder)
            result = deploy.deploy(args)
            self.assertTrue(result["passed"])
            self.assertFalse(result["catalog_rewritten"])
            for filename, data in originals.items():
                self.assertEqual((library / filename).read_bytes(), data)
            self.assertEqual((library / "Randy_Extend_Mask.blend").read_bytes(), Path(args.asset).read_bytes())
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            args.check = True
            self.assertTrue(deploy.deploy(args)["passed"])
            after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual((guarded.ASSET_NAME, guarded.FILENAME), ("Ring Mask", "Randy_Ring_Mask.blend"))

    def test_wrong_metadata_is_rejected_without_backup_or_library_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root, library, originals, args = self.fixture(folder)
            evidence = json.loads(Path(args.verification).read_text())
            evidence["asset_metadata"]["name"] = "Arc Mask"
            Path(args.verification).write_text(json.dumps(evidence))
            with self.assertRaises(ValueError):
                deploy.deploy(args)
            self.assertEqual({path.name: path.read_bytes() for path in library.iterdir()}, originals)
            self.assertFalse(Path(args.backups).exists())

    def test_check_missing_asset_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root, library, originals, args = self.fixture(folder)
            args.check = True
            with self.assertRaisesRegex(ValueError, "DEPLOYMENT_MISMATCH"):
                deploy.deploy(args)
            self.assertEqual({path.name: path.read_bytes() for path in library.iterdir()}, originals)
            self.assertFalse(Path(args.backups).exists())
            self.assertFalse(Path(args.report).exists())

    def test_catalog_conflict_cannot_touch_existing_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            root, library, originals, args = self.fixture(folder)
            catalog = library / "blender_assets.cats.txt"
            catalog.write_text("VERSION 1\n00000000-0000-0000-0000-000000000001:Textures:Textures\n")
            before = {path.name: path.read_bytes() for path in library.iterdir()}
            with self.assertRaisesRegex(ValueError, "different UUID"):
                deploy.deploy(args)
            self.assertEqual({path.name: path.read_bytes() for path in library.iterdir()}, before)
            self.assertFalse(Path(args.backups).exists())


if __name__ == "__main__":
    unittest.main()
