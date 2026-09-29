"""Deploy a verified Ring Mask asset and append its Textures catalog if missing.

Run with ordinary Python, not Blender. Backups and reports stay outside the
asset library so Blender cannot discover duplicate assets. --check writes nothing.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

# Keep even first-run --check free of Python bytecode cache writes.
sys.dont_write_bytecode = True
from deploy_assets import file_identity, read_optional, replace_checked, rollback_owned, sha256, stage_bytes


ASSET_NAME = "Ring Mask"
FILENAME = "Randy_Ring_Mask.blend"
CATALOG = "Textures"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_catalog(contents, *, require_present=False):
    paths, ids = {}, {}
    lines = [line.strip() for line in contents.decode("utf-8-sig").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    require(bool(lines) and lines[0] == "VERSION 1", "Unsupported asset catalog format")
    for line in lines[1:]:
        uuid, path, _label = line.split(":", 2)
        require(path not in paths and uuid not in ids, "Duplicate asset catalog path or UUID")
        paths[path] = uuid
        ids[uuid] = path
    require(CATALOG not in paths or paths[CATALOG] == CATALOG_ID,
            f"Existing catalog has a different UUID for {CATALOG}")
    require(CATALOG_ID not in ids or ids[CATALOG_ID] == CATALOG,
            f"Catalog UUID is already assigned to another path: {CATALOG_ID}")
    if CATALOG in paths:
        return contents
    require(not require_present, f"DEPLOYMENT_MISMATCH: missing {CATALOG_ID}:{CATALOG}")
    newline = b"\r\n" if b"\r\n" in contents else b"\n"
    separator = b"" if contents.endswith((b"\r", b"\n")) else newline
    return contents + separator + f"{CATALOG_ID}:{CATALOG}:{CATALOG}".encode("utf-8") + newline


def snapshot(library):
    return {path.relative_to(library).as_posix(): sha256(path)
            for path in sorted(library.rglob("*")) if path.is_file()}


def deploy(args):
    asset = Path(args.asset).resolve()
    verification_path = Path(args.verification).resolve()
    library = Path(args.library).resolve()
    backup_root = Path(args.backups).resolve()
    require(library.is_dir(), f"Asset library does not exist: {library}")
    require(not backup_root.is_relative_to(library), "Backups must stay outside the asset library")
    target = library / FILENAME
    catalog = library / "blender_assets.cats.txt"
    require(not target.is_symlink() and not catalog.is_symlink(), "Refusing symlink asset or catalog destinations")
    require(asset != target, "Verified build must be separate from the deployed asset")
    verification_bytes = verification_path.read_bytes()
    verification = json.loads(verification_bytes.decode("utf-8-sig"))
    require(verification.get("passed") is True, "Asset verification did not pass")
    tests = verification.get("tests")
    require(isinstance(tests, list) and tests and all(t.get("passed") is True for t in tests),
            "All verification tests must pass")
    metadata = verification.get("asset_metadata", {})
    require(metadata.get("name") == ASSET_NAME and metadata.get("catalog_id") == CATALOG_ID,
            "Verification must identify the expected Ring Mask asset and catalog")
    source_bytes = asset.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    require(source_hash == verification.get("asset_sha256"), "Verified source asset has changed")
    original_catalog = catalog.read_bytes()
    catalog_identity = file_identity(catalog)
    updated_catalog = validate_catalog(original_catalog, require_present=args.check)
    catalog_changed = updated_catalog != original_catalog
    original_target = read_optional(target)
    target_identity = file_identity(target) if original_target is not None else None
    base_report = {
        "passed": True,
        "mode": "check" if args.check else "deploy",
        "source_asset": str(asset),
        "deployed_file": str(target),
        "asset_sha256": source_hash,
        "verification_file": str(verification_path),
        "verification_sha256": hashlib.sha256(verification_bytes).hexdigest(),
        "verification_test_count": len(tests),
        "catalog_file": str(catalog),
        "catalog_sha256": hashlib.sha256(updated_catalog).hexdigest(),
        "catalog": CATALOG,
        "catalog_id": CATALOG_ID,
        "catalog_rewritten": catalog_changed,
        "ui_search_tested": False,
        "deployment_script_sha256": sha256(Path(__file__)),
        "atomic_helpers_sha256": sha256(Path(__file__).with_name("deploy_assets.py")),
    }
    if args.check:
        require(original_target == source_bytes, "DEPLOYMENT_MISMATCH: asset differs or is missing")
        return base_report

    require(args.report, "--report is required for deployment")
    report_path = Path(args.report).resolve()
    require(not report_path.is_relative_to(library), "Deployment report must stay outside the library")
    require(report_path not in (asset, verification_path), "Report must not overwrite build or verification")
    original_report = read_optional(report_path)
    before = snapshot(library)
    require(before.get(catalog.name) == hashlib.sha256(original_catalog).hexdigest(), "Catalog changed before backup")
    require(read_optional(target) == original_target, "Target changed before backup")
    backup = backup_root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup.mkdir(parents=True, exist_ok=False)
    (backup / catalog.name).write_bytes(original_catalog)
    require((backup / catalog.name).read_bytes() == original_catalog, "Catalog backup verification failed")
    if original_target is not None:
        (backup / target.name).write_bytes(original_target)
        require((backup / target.name).read_bytes() == original_target, "Asset backup verification failed")
    (backup / "before_sha256.json").write_text(json.dumps(before, indent=2), encoding="utf-8")

    temporary = None
    catalog_stage = None
    report_stage = None
    committed = []
    try:
        temporary = stage_bytes(target, source_bytes)
        if catalog_changed:
            catalog_stage = stage_bytes(catalog, updated_catalog)
        require(catalog.read_bytes() == original_catalog and file_identity(catalog) == catalog_identity,
                "Catalog changed during deployment; refusing stale deployment")
        require(asset.read_bytes() == source_bytes and verification_path.read_bytes() == verification_bytes,
                "Source or verification changed during deployment")
        deployed_identity = replace_checked(temporary, target, original_target, target_identity)
        committed.append((target, original_target, source_bytes, deployed_identity))
        if catalog_changed:
            catalog_identity = replace_checked(catalog_stage, catalog, original_catalog, catalog_identity)
            committed.append((catalog, original_catalog, updated_catalog, catalog_identity))
        require(target.read_bytes() == source_bytes, "Deployed asset verification failed")
        require(catalog.read_bytes() == updated_catalog and file_identity(catalog) == catalog_identity,
                "Catalog changed during deployment")
        after = snapshot(library)
        changed_files = {FILENAME, catalog.name} if catalog_changed else {FILENAME}
        before_unrelated = {name: digest for name, digest in before.items() if name not in changed_files}
        after_unrelated = {name: digest for name, digest in after.items() if name not in changed_files}
        require(before_unrelated == after_unrelated, "Unrelated library content changed during deployment")
        report = dict(base_report, backup_directory=str(backup), before_sha256=before,
                      after_sha256=after, existing_files_unchanged={name: True for name in before_unrelated})
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_stage = stage_bytes(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        replace_checked(report_stage, report_path, original_report)
        return report
    except BaseException as error:
        for destination, original, deployed, identity in reversed(committed):
            try:
                if not rollback_owned(destination, original, deployed, identity):
                    error.add_note(f"Rollback skipped externally changed {destination.name}; backup: {backup}")
            except Exception as rollback_error:
                error.add_note(f"Rollback failed for {destination.name}: {rollback_error}; backup: {backup}")
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if catalog_stage is not None:
            catalog_stage.unlink(missing_ok=True)
        if report_stage is not None:
            report_stage.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--verification", required=True)
    parser.add_argument("--library", required=True)
    parser.add_argument("--backups", required=True)
    parser.add_argument("--report")
    parser.add_argument("--check", action="store_true", help="Validate installed asset without writing any files")
    args = parser.parse_args()
    if not args.check and not args.report:
        parser.error("--report is required for deployment")
    print(json.dumps(deploy(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
