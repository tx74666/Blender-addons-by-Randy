"""Verify approved release-source projections without Blender or deployment writes."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from release_projection import SourceProjectionError, load_source, resolve_source


class ReleaseProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "repository"
        self.root.mkdir()
        self.source = self.workspace / "approved_package"
        self.source.mkdir()
        (self.source / "__init__.py").write_text(
            'bl_info = {"name": "Fixture", "version": (0, 77, 0)}\n',
            encoding="utf-8",
        )
        (self.source / "nested").mkdir()
        (self.source / "nested" / "feature.py").write_text(
            "ENABLED = True\n", encoding="utf-8"
        )
        (self.source / "README.md").write_text(
            "Approved fixture package.\n", encoding="utf-8"
        )
        self.manifest = self.workspace / "projection.json"
        self.payload = {
            "schema_version": 1,
            "module": "character_designer",
            "version": "0.77.0",
            "source_root": str(self.source.resolve()),
            "files": self.hashes(self.source),
            "source_provenance": {
                "approval": "Explicit test-fixture projection approval",
                "canonical_root": str(self.root.resolve()),
            },
        }
        self.write_manifest()

    @staticmethod
    def hashes(folder):
        return {
            path.relative_to(folder).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in folder.rglob("*")
            if path.is_file()
        }

    def write_manifest(self, payload=None):
        self.manifest.write_text(
            json.dumps(self.payload if payload is None else payload),
            encoding="utf-8",
        )

    def resolve(self, module="character_designer"):
        return resolve_source(module, self.manifest, root=self.root)

    def assert_rejected(self):
        with self.assertRaises(SourceProjectionError):
            self.resolve()

    def test_default_keeps_canonical_source_and_needs_no_manifest(self):
        expected = self.root / "addons" / "character_designer"
        self.assertFalse(expected.exists())
        self.assertEqual(
            resolve_source("character_designer", root=self.root),
            expected.resolve(),
        )

    def test_valid_projection_resolves_to_exact_package_directory(self):
        self.assertEqual(self.resolve(), self.source.resolve())

    def test_load_source_freezes_verified_bytes_and_projection_identity(self):
        original = (self.source / "nested" / "feature.py").read_bytes()
        manifest_hash = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        loaded = load_source("character_designer", self.manifest, root=self.root)
        self.assertEqual(loaded.root, self.source.resolve())
        self.assertEqual(loaded.version, "0.77.0")
        self.assertEqual(loaded.projection_sha256, manifest_hash)
        self.assertEqual(loaded.source_provenance, self.payload["source_provenance"])
        self.assertEqual(
            set(loaded.files), {Path(relative) for relative in self.payload["files"]}
        )
        (self.source / "nested" / "feature.py").write_bytes(b"Changed after verification\n")
        self.assertEqual(loaded.files[Path("nested/feature.py")], original)
        self.assertTrue(all(isinstance(content, bytes) for content in loaded.files.values()))

    def test_load_default_source_has_no_projection_identity(self):
        canonical = self.root / "addons" / "character_designer"
        canonical.mkdir(parents=True)
        init = (self.source / "__init__.py").read_bytes()
        (canonical / "__init__.py").write_bytes(init)
        loaded = load_source("character_designer", root=self.root)
        self.assertEqual(loaded.root, canonical.resolve())
        self.assertEqual(loaded.files, {Path("__init__.py"): init})
        self.assertEqual(loaded.version, "0.77.0")
        self.assertIsNone(loaded.projection_sha256)
        self.assertIsNone(loaded.source_provenance)

    def test_changed_file_rejects_manifest_hash(self):
        (self.source / "nested" / "feature.py").write_text(
            "ENABLED = False\n", encoding="utf-8"
        )
        self.assert_rejected()

    def test_hash_must_be_lowercase_sha256(self):
        original = self.payload["files"]["__init__.py"]
        for invalid in (original.upper(), "a" * 63, "g" * 64, 123, None):
            with self.subTest(hash=invalid):
                payload = copy.deepcopy(self.payload)
                payload["files"]["__init__.py"] = invalid
                self.write_manifest(payload)
                self.assert_rejected()

    def test_manifest_version_must_match_bl_info(self):
        self.payload["version"] = "0.77.1"
        self.write_manifest()
        self.assert_rejected()

    def test_changed_bl_info_rejects_even_when_content_hash_is_updated(self):
        (self.source / "__init__.py").write_text(
            'bl_info = {"version": (0, 77, 1)}\n', encoding="utf-8"
        )
        self.payload["files"] = self.hashes(self.source)
        self.write_manifest()
        self.assert_rejected()

    def test_manifest_must_include_every_shipped_file(self):
        del self.payload["files"]["README.md"]
        self.write_manifest()
        self.assert_rejected()

    def test_extra_shipped_file_is_rejected(self):
        (self.source / "unapproved.py").write_text(
            "EXTRA = True\n", encoding="utf-8"
        )
        self.assert_rejected()

    def test_missing_manifest_file_is_rejected(self):
        (self.source / "README.md").unlink()
        self.assert_rejected()

    def test_nonshipped_cache_and_hidden_files_do_not_change_complete_set(self):
        for relative in (
            "__pycache__/feature.cpython-312.pyc",
            "nested/feature.pyc",
            "nested/feature.pyo",
            ".approval-cache",
            ".hidden/unshipped.py",
        ):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"Not a shipped file")
        self.assertEqual(self.resolve(), self.source.resolve())

    def test_manifest_cannot_list_nonshipped_files(self):
        for relative in (".hidden.py", "__pycache__/cache.py", "nested/cache.pyc"):
            with self.subTest(path=relative):
                payload = copy.deepcopy(self.payload)
                payload["files"][relative] = "a" * 64
                self.write_manifest(payload)
                self.assert_rejected()

    def test_manifest_paths_must_be_safe_normalized_posix_relatives(self):
        for invalid in (
            "../feature.py",
            "nested/../../feature.py",
            "nested\\feature.py",
            "/feature.py",
            "C:/feature.py",
            "./__init__.py",
            "nested//feature.py",
            "nested/../__init__.py",
        ):
            with self.subTest(path=invalid):
                payload = copy.deepcopy(self.payload)
                digest = payload["files"].pop("nested/feature.py")
                payload["files"][invalid] = digest
                self.write_manifest(payload)
                self.assert_rejected()

    def test_manifest_module_must_match_selected_module(self):
        self.payload["module"] = "random_realm_builder_exporter"
        self.write_manifest()
        self.assert_rejected()

    def test_unknown_module_is_rejected(self):
        with self.assertRaises(SourceProjectionError):
            self.resolve(module="unknown_addon")

    def test_projection_cannot_be_canonical_source_itself(self):
        canonical = self.root / "addons" / "character_designer"
        canonical.mkdir(parents=True)
        (canonical / "__init__.py").write_bytes(
            (self.source / "__init__.py").read_bytes()
        )
        self.payload["source_root"] = str(canonical.resolve())
        self.payload["files"] = self.hashes(canonical)
        self.write_manifest()
        self.assert_rejected()

    def test_source_root_must_be_absolute_existing_directory(self):
        for invalid in (
            "approved_package",
            str(self.workspace / "missing_package"),
            str(self.source / "README.md"),
        ):
            with self.subTest(source_root=invalid):
                payload = copy.deepcopy(self.payload)
                payload["source_root"] = invalid
                self.write_manifest(payload)
                self.assert_rejected()

    def test_schema_and_provenance_are_required(self):
        for field, invalid in (
            ("schema_version", 2),
            ("schema_version", "1"),
            ("source_provenance", {}),
            ("source_provenance", []),
            ("source_provenance", None),
        ):
            with self.subTest(field=field, value=invalid):
                payload = copy.deepcopy(self.payload)
                payload[field] = invalid
                self.write_manifest(payload)
                self.assert_rejected()

    def test_symlink_cannot_replace_a_manifest_file(self):
        original = self.source / "nested" / "feature.py"
        external = self.workspace / "external_feature.py"
        external.write_bytes(original.read_bytes())
        original.unlink()
        try:
            original.symlink_to(external)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        self.assert_rejected()


if __name__ == "__main__":
    unittest.main()
