"""Stable Unity FBX publication and stale destination checks."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_stable_fbx.py'
SPEC = importlib.util.spec_from_file_location('_animation_stable_fbx_test', SOURCE)
stable = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stable
SPEC.loader.exec_module(stable)


class StableFBXTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        assets = self.root / 'Assets'
        assets.mkdir()
        (self.root / 'ProjectSettings').mkdir()
        (self.root / 'ProjectSettings' / 'ProjectVersion.txt').touch()
        self.guid = 'a' * 32
        self.asset = assets / 'Walk.fbx'
        self.asset.write_bytes(b'old skeletal data')
        self.meta = self.asset.with_suffix('.fbx.meta')
        self.metadata = f'fileFormatVersion: 2\r\nguid: {self.guid}\r\nModelImporter:\r\n  importAnimation: 1\r\n'.encode()
        self.meta.write_bytes(self.metadata)
        self.staged = self.root / 'Candidate.fbx'
        self.bytes = b'new skeletal data'
        self.staged.write_bytes(self.bytes)
        self.digest = hashlib.sha256(self.bytes).hexdigest()

    def test_updates_only_the_existing_fbx_and_preserves_the_importer(self):
        destination = stable.pin(self.asset, self.guid)
        stable.publish(destination, self.staged, self.digest)
        self.assertEqual(self.asset.read_bytes(), self.bytes)
        self.assertEqual(self.meta.read_bytes(), self.metadata)
        self.assertEqual(set(self.asset.parent.iterdir()), {self.asset, self.meta})

    def test_refuses_concurrent_fbx_or_importer_edits(self):
        for path in (self.asset, self.meta):
            with self.subTest(path=path):
                original = path.read_bytes()
                destination = stable.pin(self.asset, self.guid)
                path.write_bytes(original + b'\n# Changed while exporting.\n')
                changed = path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'changed during export'):
                    stable.publish(destination, self.staged, self.digest)
                self.assertEqual(path.read_bytes(), changed)
                path.write_bytes(original)

    def test_bad_worker_digest_cannot_touch_existing_file(self):
        destination = stable.pin(self.asset, self.guid)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            stable.publish(destination, self.staged, '0' * 64)
        self.assertEqual(self.asset.read_bytes(), b'old skeletal data')
        self.assertEqual(self.meta.read_bytes(), self.metadata)

    def test_rejects_outside_project_paths_and_managed_character_models(self):
        with self.assertRaisesRegex(ValueError, 'Assets folder'):
            stable.asset(self.staged)
        self.asset.with_suffix('.cdesigner.json').touch()
        with self.assertRaisesRegex(ValueError, 'character model'):
            stable.asset(self.asset)


if __name__ == '__main__':
    unittest.main()
