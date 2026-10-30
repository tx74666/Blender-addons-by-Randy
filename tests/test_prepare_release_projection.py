"""Exercise projection preparation with tiny packages; no Editor or deployment."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from prepare_release_projection import prepare_projection
from release_projection import SourceProjectionError, load_source


class PrepareProjectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace / "repo"
        self.canonical = self.root / "addons" / "character_designer"
        self.canonical.mkdir(parents=True)
        self.base = self.workspace / "approved"
        self.base.mkdir()
        for name, content in {
            "__init__.py": b'bl_info = {"version": (0, 77, 1)}\n',
            "keep.py": b"APPROVED = True\n", "feature.py": b"FEATURE = 1\n",
        }.items():
            (self.base / name).write_bytes(content)
            (self.canonical / name).write_bytes(content)
        (self.canonical / "keep.py").write_bytes(b"UNAPPROVED_CANDIDATE = True\n")
        (self.canonical / "feature.py").write_bytes(b"FEATURE = 2\n")
        (self.canonical / "new.py").write_bytes(b"NEW = True\n")
        self.manifest = self.workspace / "baseline.json"
        self.payload = {
            "schema_version": 1, "module": "character_designer", "version": "0.77.1",
            "source_root": str(self.base), "source_provenance": {"fixture": True},
            "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.base.iterdir()},
        }
        self.write_manifest()
        self.output = self.workspace / "candidate"

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.payload), encoding="utf-8")

    def prepare(self, **kwargs):
        return prepare_projection("character_designer", self.manifest, self.output,
                                  root=self.root, **kwargs)

    def test_preview_does_not_write_and_unlisted_candidates_stay_excluded(self):
        report = self.prepare(changed_files=["feature.py"], new_files=["new.py"])
        self.assertFalse(self.output.exists())
        self.assertFalse(report["applied"])
        self.assertEqual(report["changed_files"], ["feature.py"])
        self.assertEqual(report["new_files"], ["new.py"])
        self.assertEqual(report["unchanged_count"], 2)

    def test_applied_complete_projection_is_builder_compatible_and_preserves_baseline(self):
        original = {p.name: p.read_bytes() for p in self.base.iterdir()}
        report = self.prepare(changed_files=["feature.py"], new_files=["new.py"], apply=True)
        result = load_source("character_designer", self.output / "projection.json", root=self.root)
        self.assertEqual(result.files[Path("keep.py")], original["keep.py"])
        self.assertEqual(result.files[Path("feature.py")], b"FEATURE = 2\n")
        self.assertEqual(result.files[Path("new.py")], b"NEW = True\n")
        self.assertEqual(result.projection_sha256, report["projection_sha256"])
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.base.iterdir()})
        self.assertTrue((self.output / "prepare_receipt.json").is_file())

    def test_contaminated_missing_or_extra_baseline_is_refused(self):
        for mode in ("changed", "missing", "extra"):
            with self.subTest(mode=mode):
                path = self.base / "feature.py"
                original = path.read_bytes()
                if mode == "changed":
                    path.write_bytes(b"WRONG = True\n")
                elif mode == "missing":
                    path.unlink()
                else:
                    (self.base / "extra.py").write_bytes(b"EXTRA = True\n")
                with self.assertRaises(SourceProjectionError):
                    self.prepare()
                path.write_bytes(original)
                (self.base / "extra.py").unlink(missing_ok=True)
                self.assertFalse(self.output.exists())

    def test_output_cannot_exist_or_be_inside_sources(self):
        self.output.mkdir()
        with self.assertRaises(SourceProjectionError):
            self.prepare(apply=True)
        for parent in (self.base, self.canonical):
            with self.subTest(parent=parent):
                with self.assertRaises(SourceProjectionError):
                    prepare_projection("character_designer", self.manifest, parent / "new-stage", root=self.root)

    def test_wrong_pin_and_version_are_refused(self):
        for kwargs in ({"baseline_sha256": "0" * 64}, {"expected_version": "0.77.9"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SourceProjectionError):
                self.prepare(**kwargs)

    def test_safe_version_is_taken_from_explicit_init_overlay(self):
        (self.canonical / "__init__.py").write_bytes(b'bl_info = {"version": (0, 77, 2)}\n')
        self.assertEqual(self.prepare()["version"], "0.77.1")
        self.assertEqual(self.prepare(changed_files=["__init__.py"], expected_version="0.77.2")["version"], "0.77.2")

    def test_unsafe_duplicate_and_case_alias_overlay_names_are_refused(self):
        for name in ("../escape.py", "nested\\escape.py", "C:/escape.py", "a/../b.py",
                     "CON.py", "tail.py "):
            with self.subTest(name=name), self.assertRaises(SourceProjectionError):
                self.prepare(new_files=[name])
        for names in (["new.py", "NEW.py"], ["new.py", "new.py"], ["A/new.py", "a/other.py"],
                      ["new.py", "new.py/child.py"]):
            with self.subTest(names=names), self.assertRaises(SourceProjectionError):
                self.prepare(new_files=names)

    def test_wrong_change_category_or_missing_overlay_is_refused(self):
        for kwargs in ({"changed_files": ["new.py"]}, {"new_files": ["feature.py"]},
                       {"new_files": ["missing.py"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SourceProjectionError):
                self.prepare(**kwargs)

    def test_case_collisions_in_baseline_manifest_are_refused(self):
        self.payload["files"]["KEEP.py"] = self.payload["files"]["keep.py"]
        self.write_manifest()
        with self.assertRaisesRegex(SourceProjectionError, "collid"):
            self.prepare()

    def test_invalid_python_overlay_is_refused_without_writes(self):
        (self.canonical / "feature.py").write_bytes(b"def broken(\n")
        with self.assertRaises(SyntaxError):
            self.prepare(changed_files=["feature.py"], apply=True)
        self.assertFalse(self.output.exists())

    def test_invalid_selected_version_is_refused_before_creating_output(self):
        (self.canonical / "__init__.py").write_bytes(b'bl_info = {"version": (0, 77)}\n')
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaises(SourceProjectionError):
                self.prepare(changed_files=["__init__.py"], apply=apply)
            self.assertFalse(self.output.exists())

    def test_nonobject_manifest_is_refused(self):
        self.manifest.write_text("[]", encoding="utf-8")
        with self.assertRaises(SourceProjectionError):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
