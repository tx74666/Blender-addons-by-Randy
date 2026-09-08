"""A Character Designer release must not downgrade an independently updated RR Helper."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeployScopeTests(unittest.TestCase):
    def test_scope_and_newer_install_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            helper = destination / "random_realm_builder_exporter"
            helper.mkdir()
            sentinel = b'bl_info = {"version": (99, 0, 0)}\n'
            (helper / "__init__.py").write_bytes(sentinel)
            command = [sys.executable, str(ROOT / "tools/deploy_local.py"),
                       "--addons-dir", str(destination)]
            scoped = subprocess.run(command + ["--module", "character_designer"],
                                    capture_output=True, text=True)
            self.assertEqual(scoped.returncode, 0, scoped.stderr)
            self.assertNotIn('"module": "random_realm_builder_exporter"', scoped.stdout)
            self.assertEqual((helper / "__init__.py").read_bytes(), sentinel)
            check = subprocess.run(command + ["--module", "character_designer", "--check"],
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            all_modules = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(all_modules.returncode, 0)
            self.assertIn("is newer than the canonical", all_modules.stderr)
            self.assertEqual((helper / "__init__.py").read_bytes(), sentinel)


if __name__ == "__main__":
    unittest.main()
