"""Ring and Arc asset deployment preserves the existing personal library."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/randy_node_assets"))
import deploy_ring_nodes as deploy
import deploy_ring_mask as original

class RingNodeDeploymentTests(unittest.TestCase):
    def test_two_assets_install_sequentially_without_touching_existing_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library"
            library.mkdir()
            existing = {"Randy_Ring_Mask.blend": b"original Ring", "Randy_Mix_Shaders.blend": b"original Mixer", "personal.blend": b"private geometry", "blender_assets.cats.txt": ("VERSION 1\n" + original.CATALOG_ID + ":Textures:Textures\n").encode()}
            for name, data in existing.items():
                (library / name).write_bytes(data)
            for kind, (name, filename) in deploy.ASSETS.items():
                asset = root / filename
                asset.write_bytes(kind.encode())
                evidence = root / (kind + ".json")
                evidence.write_text(json.dumps({"passed": True, "asset_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(), "tests": [{"name": "fixture", "passed": True}], "asset_metadata": {"name": name, "catalog_id": original.CATALOG_ID}}))
                args = SimpleNamespace(kind=kind, asset=str(asset), verification=str(evidence), library=str(library), backups=str(root / "backups"), report=str(root / (kind + "-deployment.json")), check=False)
                self.assertTrue(deploy.deploy(args)["passed"])
                for old, data in existing.items():
                    self.assertEqual((library / old).read_bytes(), data)
                self.assertEqual((library / filename).read_bytes(), kind.encode())
                args.check = True
                before = {p.name: p.read_bytes() for p in library.iterdir()}
                self.assertTrue(deploy.deploy(args)["passed"])
                self.assertEqual({p.name: p.read_bytes() for p in library.iterdir()}, before)
                existing[filename] = kind.encode()
            self.assertEqual(original.ASSET_NAME, "Ring Mask")
            self.assertEqual(original.FILENAME, "Randy_Ring_Mask.blend")

    def test_wrong_asset_metadata_is_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library"
            library.mkdir()
            (library / "blender_assets.cats.txt").write_text("VERSION 1\n" + original.CATALOG_ID + ":Textures:Textures\n")
            source = root / "build.blend"
            source.write_bytes(b"fixture")
            evidence = root / "report.json"
            evidence.write_text(json.dumps({"passed": True, "asset_sha256": hashlib.sha256(b"fixture").hexdigest(), "tests": [{"name": "fixture", "passed": True}], "asset_metadata": {"name": "Ring Mask", "catalog_id": original.CATALOG_ID}}))
            args = SimpleNamespace(kind="arc_mask", asset=str(source), verification=str(evidence), library=str(library), backups=str(root / "backups"), report=str(root / "deploy.json"), check=False)
            before = {p.name: p.read_bytes() for p in library.iterdir()}
            with self.assertRaises(ValueError):
                deploy.deploy(args)
            self.assertEqual({p.name: p.read_bytes() for p in library.iterdir()}, before)
            self.assertFalse((root / "backups").exists())

if __name__ == "__main__":
    unittest.main()
