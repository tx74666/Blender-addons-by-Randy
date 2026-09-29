"""Deploy a verified bundle and append catalogs, backing up outside the library."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile


CATALOGS = {
    "Randy/Primitives": "67c33df7-9a94-5d93-b9ca-21feef71eadb",
    "Randy/Patterns": "a1f8b514-e0ed-5fc8-9c14-12c5d9c0d57e",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_optional(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def file_identity(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def stage_bytes(destination, contents):
    """Finish and sync a complete file on the destination filesystem first."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".randy-deploy-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.read_bytes() != contents:
            raise OSError(f"Incomplete staged file: {temporary}")
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def replace_checked(temporary, destination, expected, expected_identity=None):
    # Compare immediately before replace, especially for Blender's shared catalog.
    # This is optimistic concurrency detection, not a lock across both files.
    if read_optional(destination) != expected:
        raise RuntimeError(f"Destination changed during deployment; refusing stale replacement: {destination}")
    if expected_identity is not None and file_identity(destination) != expected_identity:
        raise RuntimeError(f"Destination no longer belongs to this deployment: {destination}")
    identity = file_identity(temporary)
    os.replace(temporary, destination)
    return identity


def rollback_owned(destination, original, deployed, identity):
    """Restore only our exact still-current replacement; preserve external saves."""
    if read_optional(destination) != deployed or file_identity(destination) != identity:
        return False
    if original is None:
        # Do not delete a newly-created file if another writer replaced or changed it.
        if read_optional(destination) != deployed or file_identity(destination) != identity:
            return False
        destination.unlink()
        return True
    temporary = stage_bytes(destination, original)
    try:
        replace_checked(temporary, destination, deployed, expected_identity=identity)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def deploy(args):
    asset = Path(args.asset).resolve()
    library = Path(args.library).resolve()
    backup_root = Path(args.backups).resolve()
    if backup_root.is_relative_to(library):
        raise ValueError("Backups must stay outside the asset library to avoid duplicate assets")
    verification = json.loads(Path(args.verification).read_text(encoding="utf-8"))
    assert verification["passed"] and all(t["passed"] for t in verification["tests"])
    assert verification["asset_sha256"] == sha256(asset), "Verified file has changed"
    assert {a["catalog_id"] for a in verification["assets"]} == set(CATALOGS.values())
    assert library.is_dir()
    catalog = library / "blender_assets.cats.txt"
    original_catalog = catalog.read_bytes()
    text = original_catalog.decode("utf-8-sig")
    paths = {}
    ids = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#") or line.startswith("VERSION"):
            continue
        uuid, path, _label = line.split(":", 2)
        paths[path] = uuid
        ids[uuid] = path
    for path, uuid in CATALOGS.items():
        assert path not in paths or paths[path] == uuid, f"Existing catalog has a different UUID: {path}"
        assert uuid not in ids or ids[uuid] == path, f"Catalog UUID collision: {uuid}"
    target = library / "Randy_Toolkit.blend"
    original_target = read_optional(target)
    source_bytes = asset.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != verification["asset_sha256"]:
        raise RuntimeError("Verified source changed during deployment")
    if args.check:
        if original_target != source_bytes or any(paths.get(path) != uuid for path, uuid in CATALOGS.items()):
            raise RuntimeError("DEPLOYMENT_MISMATCH")
        return {
            "passed": True,
            "mode": "check",
            "deployed_file": str(target),
            "asset_sha256": sha256(target),
            "catalog_sha256": sha256(catalog),
            "verification_test_count": len(verification["tests"]),
            "source_sha256": {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")},
        }
    before = {p.name: sha256(p) for p in library.iterdir() if p.is_file()}
    if before[catalog.name] != hashlib.sha256(original_catalog).hexdigest() or read_optional(target) != original_target:
        raise RuntimeError("Destination changed before backup; retry from fresh state")
    backup = backup_root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup.mkdir(parents=True, exist_ok=False)
    (backup / catalog.name).write_bytes(original_catalog)
    assert sha256(backup / catalog.name) == before[catalog.name]
    if original_target is not None:
        (backup / target.name).write_bytes(original_target)
        assert (backup / target.name).read_bytes() == original_target
    (backup / "before_sha256.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    extra = "" if text.endswith("\n") else "\n"
    for path, uuid in CATALOGS.items():
        if path not in paths:
            extra += f"{uuid}:{path}:{path.replace('/', '-')}\n"
    updated_catalog = original_catalog + extra.encode("utf-8")
    staged = []
    committed = []
    try:
        asset_stage = stage_bytes(target, source_bytes)
        staged.append(asset_stage)
        catalog_stage = stage_bytes(catalog, updated_catalog)
        staged.append(catalog_stage)
        identity = replace_checked(asset_stage, target, original_target)
        committed.append((target, original_target, source_bytes, identity))
        identity = replace_checked(catalog_stage, catalog, original_catalog)
        committed.append((catalog, original_catalog, updated_catalog, identity))
        assert target.read_bytes() == source_bytes
        assert catalog.read_bytes() == updated_catalog
        preserved = {name: sha256(library / name) == digest for name, digest in before.items() if name not in (catalog.name, target.name)}
        assert all(preserved.values()), "Unrelated library content changed"
    except BaseException as error:
        for destination, original, deployed, identity in reversed(committed):
            try:
                if not rollback_owned(destination, original, deployed, identity):
                    error.add_note(f"Rollback skipped external change: {destination}; backup: {backup}")
            except Exception as rollback_error:
                error.add_note(f"Rollback could not restore {destination}: {rollback_error}; backup: {backup}")
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)
    report = {
        "passed": True,
        "deployed_file": str(target),
        "asset_sha256": sha256(target),
        "catalog_file": str(catalog),
        "catalog_sha256": sha256(catalog),
        "catalogs": CATALOGS,
        "backup_directory": str(backup),
        "existing_files_unchanged": preserved,
        "source_sha256": {p.name: sha256(p) for p in Path(__file__).parent.glob("*.py")},
        "ui_search_tested": False,
    }
    report_path = Path(args.report)
    temporary = stage_bytes(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    try:
        os.replace(temporary, report_path)
    finally:
        temporary.unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True)
    parser.add_argument("--verification", required=True)
    parser.add_argument("--library", required=True)
    parser.add_argument("--backups", required=True)
    parser.add_argument("--report")
    parser.add_argument("--check", action="store_true", help="Check deployed content and catalogs without any writes")
    args = parser.parse_args()
    if not args.check and not args.report:
        parser.error("--report is required for deployment")
    print(json.dumps(deploy(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
