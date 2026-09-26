"""A scoped RR release must not package another in-progress add-on."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rr_build_scope", ROOT / "tools" / "build_releases.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class BuildScopeTests(unittest.TestCase):
    def test_scoped_release_ignores_unready_other_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "addons" / "random_realm_builder_exporter"
            source.mkdir(parents=True)
            content = b'bl_info = {"version": (0, 2, 13)}\n'
            (source / "__init__.py").write_bytes(content)
            # No second add-on exists: packaging it would fail.
            with mock.patch.object(builder, "ROOT", root):
                builder.main(["--module", "random_realm_builder_exporter"])
                builder.main(["--module", "random_realm_builder_exporter"])
            archives = list((root / "dist").glob("*.zip"))
            self.assertEqual(len(archives), 1)
            with zipfile.ZipFile(archives[0]) as archive:
                self.assertEqual(archive.read("random_realm_builder_exporter/__init__.py"), content)
            self.assertIn(archives[0].name, (root / "dist" / "SHA256SUMS.txt").read_text())


if __name__ == "__main__":
    unittest.main()
