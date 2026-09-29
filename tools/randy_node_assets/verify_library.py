#!/usr/bin/env python3
"""Check the published node library without importing Blender or changing files.

Run with standard Python before committing a node-library update. Blender's
numeric/render tests remain the responsibility of each bundle's verify script;
this check ties their saved results to the exact assets and source in Git.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import uuid


class LibraryCheck:
    def __init__(self, repo_root: Path):
        self.root = repo_root.resolve()
        self.errors: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def path(self, value: object, label: str) -> Path | None:
        if not isinstance(value, str) or not value or "\\" in value:
            self.error(f"{label}: expected a repository-relative path using '/'.")
            return None
        relative = PurePosixPath(value)
        if relative.is_absolute() or ":" in value or ".." in relative.parts:
            self.error(f"{label}: path must stay inside the repository: {value!r}.")
            return None
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            self.error(f"{label}: resolved path leaves the repository: {value!r}.")
            return None
        if not path.is_file():
            self.error(f"{label}: missing file: {value}.")
            return None
        return path

    def json_file(self, path: Path | None, label: str) -> dict | None:
        if path is None:
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self.error(f"{label}: cannot read JSON: {exc}.")
            return None
        if not isinstance(value, dict):
            self.error(f"{label}: expected a JSON object.")
            return None
        return value

    def hash_file(
        self, path: Path | None, expected: object, label: str, *, text_lf: bool = False
    ) -> None:
        if not isinstance(expected, str) or len(expected) != 64 or any(
            character not in "0123456789abcdef" for character in expected
        ):
            self.error(f"{label}: expected a lowercase SHA-256 digest.")
            return
        if path is None:
            return
        try:
            if text_lf:
                data = path.read_bytes()
                data.decode("utf-8")  # The manifest explicitly declares UTF-8 text.
                actual = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
            else:
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
        except (OSError, UnicodeError) as exc:
            self.error(f"{label}: cannot hash file: {exc}.")
            return
        if actual != expected:
            self.error(
                f"{label}: SHA-256 mismatch for {path.relative_to(self.root)} "
                f"(expected {expected}, found {actual}). "
                "Rebuild and verify the bundle, then update its manifest and report together."
            )

    def unique(self, entries: object, key: str, label: str) -> dict[str, dict]:
        result: dict[str, dict] = {}
        if not isinstance(entries, list) or not entries:
            self.error(f"{label}: expected a non-empty list.")
            return result
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                self.error(f"{label}[{index}]: expected an object.")
                continue
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                self.error(f"{label}[{index}]: missing or empty {key}.")
                continue
            if value in result:
                self.error(f"{label}: duplicate {key}: {value!r}.")
            else:
                result[value] = entry
        return result

    def catalogs(self, path: Path | None) -> dict[str, str]:
        result: dict[str, str] = {}
        if path is None:
            return result
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            self.error(f"catalog_file: cannot read catalog: {exc}.")
            return result
        versions = 0
        paths: set[str] = set()
        for number, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("VERSION"):
                versions += 1
                if line != "VERSION 1":
                    self.error(f"catalog line {number}: expected VERSION 1.")
                continue
            fields = line.split(":", 2)
            if len(fields) != 3 or not fields[1] or not fields[2]:
                self.error(f"catalog line {number}: expected UUID:path:simple-name.")
                continue
            identifier, catalog_path, _simple_name = fields
            try:
                if str(uuid.UUID(identifier)) != identifier:
                    raise ValueError("UUID must use lowercase canonical formatting")
            except ValueError:
                self.error(f"catalog line {number}: invalid UUID {identifier!r}.")
                continue
            if identifier in result:
                self.error(f"catalog line {number}: duplicate UUID {identifier}.")
            if catalog_path in paths:
                self.error(f"catalog line {number}: duplicate path {catalog_path!r}.")
            result[identifier] = catalog_path
            paths.add(catalog_path)
        if versions != 1:
            self.error("catalog_file: expected exactly one VERSION 1 declaration.")
        return result

    def source(self, bundle: dict, label: str) -> None:
        sources = bundle.get("source")
        hashes = bundle.get("source_sha256")
        if not isinstance(sources, dict) or set(sources) != {"build", "verify", "deploy"}:
            self.error(f"{label}.source: expected build, verify and deploy paths.")
            return
        if not isinstance(hashes, dict):
            self.error(f"{label}.source_sha256: expected a path-to-SHA-256 object.")
            return
        source_paths = list(sources.values())
        if not all(isinstance(value, str) for value in source_paths):
            self.error(f"{label}.source: all source paths must be strings.")
            return
        if set(hashes) != set(source_paths):
            self.error(f"{label}.source_sha256: hashes must cover exactly the declared sources.")
        for role, value in sources.items():
            path = self.path(value, f"{label}.source.{role}")
            self.hash_file(path, hashes.get(value), f"{label}.source.{role}", text_lf=True)
            if path is not None:
                if path.suffix != ".py":
                    self.error(f"{label}.source.{role}: expected a Python source file.")
                try:
                    ast.parse(path.read_text(encoding="utf-8-sig"), filename=value)
                except (OSError, UnicodeError, SyntaxError) as exc:
                    self.error(f"{label}.source.{role}: Python syntax check failed: {exc}.")

    def report(self, bundle: dict, assets: list[dict], label: str) -> None:
        path = self.path(bundle.get("verification"), f"{label}.verification")
        report = self.json_file(path, f"{label}.verification")
        if report is None:
            return
        if report.get("passed") is not True:
            self.error(f"{label}.verification: the saved verification did not pass.")
        if report.get("asset_sha256") != bundle.get("sha256"):
            self.error(f"{label}.verification: asset_sha256 does not match the bundle.")
        tests = self.unique(report.get("tests"), "name", f"{label}.verification.tests")
        for name, test in tests.items():
            if test.get("passed") is not True:
                self.error(f"{label}.verification: test {name!r} did not pass.")
        if "asset_metadata" in report:
            metadata = [report["asset_metadata"]]
        else:
            metadata = report.get("assets")
        recorded = self.unique(metadata, "name", f"{label}.verification.assets")
        expected_names = {asset["name"] for asset in assets}
        if set(recorded) != expected_names:
            self.error(
                f"{label}.verification: asset names differ from the manifest "
                f"(expected {sorted(expected_names)}, found {sorted(recorded)})."
            )
        for asset in assets:
            metadata = recorded.get(asset["name"])
            if metadata and metadata.get("catalog_id") != asset.get("catalog_id"):
                self.error(
                    f"{label}.verification: catalog_id differs for {asset['name']!r}."
                )
        if "baseline_verification" in report:
            baseline_label = f"{label}.verification.baseline"
            baseline_path = self.path(report["baseline_verification"], baseline_label)
            if report.get("baseline_verification_hash_mode") != "utf8-lf":
                self.error(f"{baseline_label}: expected baseline_verification_hash_mode='utf8-lf'.")
            self.hash_file(
                baseline_path, report.get("baseline_verification_sha256"),
                baseline_label, text_lf=True,
            )
            baseline = self.json_file(baseline_path, baseline_label)
            if baseline is not None:
                if baseline.get("passed") is not True:
                    self.error(f"{baseline_label}: the referenced baseline did not pass.")
                if baseline.get("asset_sha256") != report.get("baseline_sha256"):
                    self.error(f"{baseline_label}: baseline asset hash does not match its reference.")
                tests = self.unique(baseline.get("tests"), "name", f"{baseline_label}.tests")
                if len(tests) != report.get("baseline_test_count"):
                    self.error(f"{baseline_label}: baseline test count does not match its reference.")
                for name, test in tests.items():
                    if test.get("passed") is not True:
                        self.error(f"{baseline_label}: test {name!r} did not pass.")

    def run(self) -> tuple[int, int]:
        manifest = self.json_file(
            self.path("node_library/manifest.json", "manifest"), "manifest"
        )
        if manifest is None:
            return 0, 0
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            self.error("manifest.schema_version: expected 1.")
        if manifest.get("source_hash_mode") != "utf8-lf":
            self.error("manifest.source_hash_mode: expected 'utf8-lf' for portable source hashes.")
        for key in ("library_version", "blender_version"):
            if not isinstance(manifest.get(key), str) or not manifest[key].strip():
                self.error(f"manifest.{key}: expected a non-empty version string.")
        catalogs = self.catalogs(self.path(manifest.get("catalog_file"), "catalog_file"))
        bundles = self.unique(manifest.get("bundles"), "id", "bundles")
        assets = self.unique(manifest.get("assets"), "id", "assets")
        self.unique(manifest.get("assets"), "name", "assets")
        valid_assets = []
        for identifier, asset in assets.items():
            label = f"assets[{identifier}]"
            if not isinstance(asset.get("name"), str) or not asset["name"].strip():
                continue  # Already reported by the name uniqueness check.
            valid_assets.append(asset)
            if not isinstance(asset.get("bundle_id"), str) or asset["bundle_id"] not in bundles:
                self.error(f"{label}: unknown bundle_id {asset.get('bundle_id')!r}.")
            if asset.get("node_tree_type") not in {"ShaderNodeTree", "GeometryNodeTree"}:
                self.error(f"{label}: expected ShaderNodeTree or GeometryNodeTree.")
            for key in ("display_name", "version", "description"):
                if not isinstance(asset.get(key), str) or not asset[key].strip():
                    self.error(f"{label}.{key}: expected a non-empty string.")
            catalog_id = asset.get("catalog_id")
            if not isinstance(catalog_id, str) or catalog_id not in catalogs:
                self.error(f"{label}: catalog_id {catalog_id!r} is absent from the catalog file.")
            elif catalogs[catalog_id] != asset.get("catalog_path"):
                self.error(f"{label}: catalog_path does not match its catalog UUID.")
        declared_paths: set[Path] = set()
        for identifier, bundle in bundles.items():
            label = f"bundles[{identifier}]"
            path = self.path(bundle.get("path"), f"{label}.path")
            if path is not None:
                if not path.is_relative_to((self.root / "node_library" / "assets").resolve()):
                    self.error(f"{label}.path: bundle must be inside node_library/assets/.")
                if path.suffix.lower() != ".blend":
                    self.error(f"{label}.path: expected a .blend file.")
                if path in declared_paths:
                    self.error(f"{label}.path: duplicate bundle file {bundle['path']}.")
                declared_paths.add(path)
            self.hash_file(path, bundle.get("sha256"), label)
            self.source(bundle, label)
            bundle_assets = [a for a in valid_assets if a.get("bundle_id") == identifier]
            if not bundle_assets:
                self.error(f"{label}: no assets refer to this bundle.")
            self.report(bundle, bundle_assets, label)
        actual_paths = {
            path.resolve()
            for path in (self.root / "node_library" / "assets").rglob("*")
            if path.is_file() and path.suffix.lower() == ".blend"
        }
        for path in sorted(actual_paths - declared_paths):
            self.error(f"Unlisted .blend file in node_library/assets: {path}.")
        return len(assets), len(bundles)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2],
        help="Repository root; defaults to the repository containing this script.",
    )
    args = parser.parse_args()
    check = LibraryCheck(args.repo_root)
    assets, bundles = check.run()
    if check.errors:
        print(f"Node library verification FAILED ({len(check.errors)} issue(s)):", file=sys.stderr)
        for error in check.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        f"Node library verification passed: {assets} assets, {bundles} bundles; "
        "catalogs, file/source hashes, Python syntax and saved test reports agree."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
