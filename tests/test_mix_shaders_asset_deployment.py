"""The second Textures asset must preserve Ring Mask and unrelated library files."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/randy_node_assets"))
import deploy_mix_shaders as deploy
import deploy_ring_mask as existing


class MixShadersDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.library = folder / "library"
        self.library.mkdir()
        (self.library / "blender_assets.cats.txt").write_text(
            "VERSION 1\n{}:Textures:Textures\n".format(existing.CATALOG_ID), encoding="utf-8")
        (self.library / "Randy_Ring_Mask.blend").write_bytes(b"old Ring Mask must survive")
        (self.library / "personal.blend").write_bytes(b"unrelated personal asset")
        self.asset = folder / "build.blend"
        self.asset.write_bytes(b"verified Mix Shaders fixture bytes")
        self.verification = folder / "verification.json"
        self.verification.write_text(json.dumps({"passed": True,
            "tests": [{"name": "isolated fixture", "passed": True}],
            "asset_sha256": hashlib.sha256(self.asset.read_bytes()).hexdigest(),
            "asset_metadata": {"name": "Mix Shaders", "catalog_id": existing.CATALOG_ID}}), encoding="utf-8")
        self.args = SimpleNamespace(asset=str(self.asset), verification=str(self.verification),
                                    library=str(self.library), backups=str(folder / "backups"),
                                    report=str(folder / "deploy.json"), check=False)

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.library.iterdir() if path.is_file()}

    def test_deploys_only_mix_asset_preserves_catalog_ring_and_personal_content(self):
        before = self.snapshot()
        result = deploy.deploy(self.args)
        self.assertTrue(result["passed"])
        for name, data in before.items():
            self.assertEqual((self.library / name).read_bytes(), data)
        self.assertEqual((self.library / "Randy_Mix_Shaders.blend").read_bytes(), self.asset.read_bytes())
        self.assertEqual(existing.ASSET_NAME, "Ring Mask")
        self.assertEqual(existing.FILENAME, "Randy_Ring_Mask.blend")
        self.assertFalse(result["catalog_rewritten"])
        before_check = self.snapshot()
        self.args.check = True
        self.assertTrue(deploy.deploy(self.args)["passed"])
        self.assertEqual(self.snapshot(), before_check)

    def test_catalog_collision_is_rejected_without_any_library_changes(self):
        (self.library / "blender_assets.cats.txt").write_text(
            "VERSION 1\n00000000-0000-0000-0000-000000000001:Textures:Textures\n", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "different UUID"):
            deploy.deploy(self.args)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(Path(self.args.backups).exists())

    def test_check_missing_asset_is_read_only(self):
        before = self.snapshot()
        self.args.check = True
        with self.assertRaisesRegex(ValueError, "DEPLOYMENT_MISMATCH"):
            deploy.deploy(self.args)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(Path(self.args.backups).exists())
        self.assertFalse(Path(self.args.report).exists())


if __name__ == "__main__":
    unittest.main()
