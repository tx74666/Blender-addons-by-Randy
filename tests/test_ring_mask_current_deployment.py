"""Current Ring deployment rejects its historical fixture and stays scoped."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/randy_node_assets"))
import deploy_ring_mask_current as deploy
import deploy_ring_mask as guard


class CurrentRingDeploymentTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        library = root / "library"
        library.mkdir()
        originals = {"Randy_Arc_Mask.blend": b"existing Arc", "personal.blend": b"unrelated asset",
                     "blender_assets.cats.txt": ("VERSION 1\n" + guard.CATALOG_ID + ":Textures:Textures\n").encode()}
        for filename, contents in originals.items():
            (library / filename).write_bytes(contents)
        asset = root / "candidate.blend"
        asset.write_bytes(b"verified current native Ring")
        report = root / "verification.json"
        report.write_text(json.dumps({"passed": True, "asset_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                                     "tests": [{"name": "isolated fixture", "passed": True}],
                                     "asset_metadata": {"name": "Ring Mask", "version": "0.2.1", "catalog_id": guard.CATALOG_ID,
                                                        "outputs": [{"name": "Mask", "type": "NodeSocketFloat"},
                                                                    {"name": "Ring Data", "type": "NodeSocketBundle"}]}}))
        args = SimpleNamespace(asset=str(asset), verification=str(report), library=str(library),
                               backups=str(root / "backups"), report=str(root / "deployment.json"), check=False)
        return root, library, originals, args

    def test_scoped_install_and_final_read_only_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root, library, originals, args = self.fixture(folder)
            self.assertTrue(deploy.deploy(args)["passed"])
            for filename, data in originals.items():
                self.assertEqual((library / filename).read_bytes(), data)
            self.assertEqual((library / "Randy_Ring_Mask.blend").read_bytes(), Path(args.asset).read_bytes())
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            args.check = True
            self.assertTrue(deploy.deploy(args)["passed"])
            self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_historical_three_input_ring_cannot_be_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            root, library, originals, args = self.fixture(folder)
            evidence = json.loads(Path(args.verification).read_text())
            evidence["asset_metadata"]["version"] = "0.1.1"
            evidence["asset_metadata"]["outputs"] = [{"name": "Mask", "type": "NodeSocketFloat"}]
            Path(args.verification).write_text(json.dumps(evidence))
            with self.assertRaisesRegex(ValueError, "current Ring Mask"):
                deploy.deploy(args)
            self.assertEqual({p.name: p.read_bytes() for p in library.iterdir()}, originals)
            self.assertFalse(Path(args.backups).exists())


if __name__ == "__main__":
    unittest.main()
