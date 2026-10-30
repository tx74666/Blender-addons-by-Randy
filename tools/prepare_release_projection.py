"""Preview or prepare a complete candidate projection from an approved baseline.

Only explicitly selected canonical files are overlaid. This tool does not build,
deploy, approve a release, or contact Blender. Existing release_projection guards
remain the authority for complete baseline and generated-package verification.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path

try:
    from release_projection import (
        ROOT, SourceProjectionError, _VERSION, _reparse, _safe_relative, _version_bytes,
        load_source,
    )
except ModuleNotFoundError as exc:
    if exc.name != "release_projection":
        raise
    from tools.release_projection import (
        ROOT, SourceProjectionError, _VERSION, _reparse, _safe_relative, _version_bytes,
        load_source,
    )


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _unique_names(names):
    """Refuse Windows aliases and inconsistent directory casing on any host."""
    entries = {}
    files = set()
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    for name in names:
        relative = _safe_relative(name)
        if name.casefold() in files:
            raise SourceProjectionError(f"Duplicate or case-colliding file: {name}")
        files.add(name.casefold())
        for index, part in enumerate(relative.parts):
            if (part.endswith((".", " ")) or any(c in part for c in '<>"|?*')
                    or part.split(".")[0].upper() in reserved):
                raise SourceProjectionError(f"Unsafe Windows file name: {name}")
            prefix = "/".join(relative.parts[:index + 1])
            previous = entries.setdefault(prefix.casefold(), prefix)
            if previous != prefix:
                raise SourceProjectionError(f"Case-colliding paths: {previous}, {prefix}")
    for name in files:
        parts = name.split("/")
        if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
            raise SourceProjectionError(f"A file is also a parent directory: {name}")


def _no_links(path):
    for entry in (path, *path.parents):
        if entry.exists() and _reparse(entry):
            raise SourceProjectionError(f"Symlink/reparse path is not allowed: {entry}")


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceProjectionError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def prepare_projection(module, baseline, output_dir, *, changed_files=(),
                       new_files=(), baseline_sha256=None, expected_version=None,
                       apply=False, root=ROOT):
    """Return a preview; --apply additionally writes one new frozen directory."""
    baseline = Path(baseline).absolute()
    raw = baseline.read_bytes()
    if baseline_sha256 is not None and _sha(raw) != baseline_sha256:
        raise SourceProjectionError("Baseline manifest SHA256 differs from the requested pin.")
    manifest = json.loads(raw, object_pairs_hook=_json_pairs)
    if not isinstance(manifest, dict):
        raise SourceProjectionError("Baseline manifest must be a JSON object.")
    _unique_names(manifest.get("files", {}))
    base = load_source(module, baseline, root=root)
    if base.projection_sha256 != _sha(raw):
        raise SourceProjectionError("Baseline manifest changed while it was read.")

    changed_files, new_files = tuple(changed_files), tuple(new_files)
    selected = changed_files + new_files
    _unique_names(selected)
    base_names = {path.as_posix() for path in base.files}
    if set(changed_files) - base_names:
        raise SourceProjectionError("--changed-file must already exist in the baseline.")
    if set(new_files) & base_names:
        raise SourceProjectionError("--new-file must not already exist in the baseline.")
    _unique_names(sorted(base_names) + list(new_files))

    canonical = (Path(root) / "addons" / module).absolute()
    _no_links(canonical)
    if not canonical.is_dir():
        raise SourceProjectionError(f"Canonical module directory is missing: {canonical}")
    output = Path(output_dir).absolute()
    _no_links(output)
    if output.exists() or output.is_symlink():
        raise SourceProjectionError(f"Output must be a new directory: {output}")
    if not output.parent.is_dir():
        raise SourceProjectionError("Output parent must already exist.")
    output = output.resolve()
    if output.is_relative_to(base.root) or output.is_relative_to(canonical.resolve()):
        raise SourceProjectionError("Output must be outside baseline and canonical packages.")

    payload = dict(base.files)
    for name in selected:
        relative = _safe_relative(name)
        source = canonical / relative
        _no_links(source)
        if not source.is_file() or not source.resolve().is_relative_to(canonical.resolve()):
            raise SourceProjectionError(f"Canonical overlay file is missing or unsafe: {name}")
        payload[relative] = source.read_bytes()
    for relative, content in payload.items():
        if relative.suffix == ".py":
            ast.parse(content, filename=relative.as_posix())
    version = _version_bytes(payload[Path("__init__.py")], module)
    if not _VERSION.fullmatch(version):
        raise SourceProjectionError("Selected package version must have three numeric parts.")
    if expected_version is not None and version != expected_version:
        raise SourceProjectionError(f"Selected package version is {version}, expected {expected_version}.")
    actual_changes = sorted(path.as_posix() for path in base.files
                            if payload[path] != base.files[path])
    source_root = output / module
    projection = {
        "schema_version": 1, "module": module, "version": version,
        "source_root": str(source_root),
        "source_provenance": {
            "base_projection": str(baseline), "base_sha256": base.projection_sha256,
            "canonical_source": str(canonical),
            "canonical_increment": sorted(selected),
            "changed_files": actual_changes, "new_files": sorted(new_files),
            "approval_status": "candidate_requires_validation_and_approval",
            "scope": "Complete verified baseline with explicit canonical overlays only.",
        },
        "files": {path.as_posix(): _sha(content) for path, content in sorted(payload.items())},
    }
    receipt = {
        "schema": "release-projection-preparation/1", "applied": bool(apply),
        "module": module, "version": version, "baseline_sha256": base.projection_sha256,
        "projection": str(output / "projection.json"), "file_count": len(payload),
        "changed_files": actual_changes, "new_files": sorted(new_files),
        "unchanged_count": len(base.files) - len(actual_changes),
        "selected_unchanged": sorted(set(changed_files) - set(actual_changes)),
        "approval_status": "candidate_requires_validation_and_approval",
        "build_deploy_or_artist_operations": False,
    }
    if apply:
        # Exclusive creation is the only mutation; never overwrite or clean up an
        # existing tree. A failed write leaves its candidate for inspection.
        output.mkdir()
        source_root.mkdir()
        for relative, content in payload.items():
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(content)
        projection_bytes = (json.dumps(projection, indent=2) + "\n").encode("utf-8")
        with (output / "projection.json").open("xb") as stream:
            stream.write(projection_bytes)
        verified = load_source(module, output / "projection.json", root=root)
        if verified.files != payload:
            raise SourceProjectionError("Generated projection did not verify exactly.")
        receipt["projection_sha256"] = _sha(projection_bytes)
        with (output / "prepare_receipt.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", required=True,
                        choices=("character_designer", "random_realm_builder_exporter"))
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--baseline-sha256")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--new-file", action="append", default=[])
    parser.add_argument("--expected-version")
    parser.add_argument("--apply", action="store_true", help="Write a new candidate; default is read-only preview")
    args = parser.parse_args(argv)
    try:
        receipt = prepare_projection(
            args.module, args.baseline, args.output_dir, changed_files=args.changed_file,
            new_files=args.new_file, baseline_sha256=args.baseline_sha256,
            expected_version=args.expected_version, apply=args.apply,
        )
    except (OSError, ValueError, TypeError, KeyError, SyntaxError) as exc:
        parser.exit(1, f"Projection preparation refused: {exc}\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
