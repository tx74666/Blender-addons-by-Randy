"""Publish one ordinary Standard package without damaging the previous export.

The two directory renames are not a cross-process atomic operation. Unity must
honor the sibling .publishing marker and verify the manifest/model contract.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile


def _prepare_package(staged_package, previous_package, asset_id):
    manifest_path = os.path.join(staged_package, "manifest.json")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict) or manifest.get("id") != asset_id:
        raise RuntimeError("Standard staged manifest has a different asset ID.")

    paths = [manifest.get("modelFile"), manifest.get("iconFile")]
    # The exporter currently emits three maps. Keep the established Unity
    # manifest aliases valid when an existing package is reused for icon-only.
    for material in manifest.get("materialMaps", []):
        paths.extend(material.get(key) for key in (
            "bakedBaseColor", "baseMap", "baseColor", "normal", "roughness",
            "occlusion", "ao", "metallicSmoothness",
        ))
    for relative in filter(None, paths):
        path = os.path.abspath(os.path.join(staged_package, relative))
        if (
            os.path.commonpath((staged_package, path)) != staged_package
            or not os.path.isfile(path)
            or os.path.getsize(path) == 0
        ):
            raise RuntimeError(f"Standard staged resource is missing or invalid: {relative}")

    model_file = manifest.get("modelFile")
    if model_file:
        contract = manifest.get("uvExport") or {}
        expected_hash = str(contract.get("modelSha256", "") or "").lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
            raise RuntimeError("Standard model has no valid SHA-256 contract; export Model again.")
        digest = hashlib.sha256()
        with open(os.path.join(staged_package, model_file), "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise RuntimeError("Standard staged model does not match its manifest SHA-256.")

    prepare_published_metadata(staged_package, previous_package, manifest)


def prepare_published_metadata(staged_package, previous_package, manifest=None):
    """Keep snapshot paths and surviving Unity GUIDs valid after directory publication."""
    staged_package = os.path.abspath(staged_package)
    previous_package = os.path.abspath(previous_package)
    manifest_path = os.path.join(staged_package, "manifest.json")
    if manifest is None:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    source_blend = manifest.get("sourceBlend", "")
    if source_blend:
        source_blend = os.path.abspath(source_blend)
        try:
            is_snapshot = os.path.commonpath((staged_package, source_blend)) == staged_package
        except ValueError:
            is_snapshot = False
        if is_snapshot:
            if not os.path.isfile(source_blend):
                raise RuntimeError("Staged Surface Text snapshot is missing.")
            manifest["sourceBlend"] = os.path.join(
                previous_package, os.path.relpath(source_blend, staged_package)
            )
            with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(manifest, handle, indent=2)
                handle.write("\n")

    # Texture regeneration may remove its staged directory. Restore the existing
    # GUID for every surviving path; the package folder's .meta stays in place.
    if os.path.isdir(previous_package):
        for directory, _subdirs, files in os.walk(previous_package):
            for name in files:
                if not name.endswith(".meta"):
                    continue
                source = os.path.join(directory, name)
                relative = os.path.relpath(source, previous_package)
                destination = os.path.join(staged_package, relative)
                if os.path.exists(destination[:-5]):
                    shutil.copy2(source, destination)


def export_package(output_root, asset_id, transaction_parent, export_callback):
    """Run the existing exporter in staging, then publish or restore the old package.

    export_callback receives an output root with the same <asset_id>/ layout.
    Failed recovery keeps the marker, backup and staging paths for inspection.
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+", asset_id or ""):
        raise ValueError("Standard package ID must contain only ASCII letters, digits or underscore.")
    output_root = os.path.abspath(output_root)
    previous_package = os.path.join(output_root, asset_id)
    marker = os.path.join(output_root, f".rr-{asset_id}.publishing")
    os.makedirs(output_root, exist_ok=True)
    os.makedirs(transaction_parent, exist_ok=True)
    transaction_root = tempfile.mkdtemp(prefix=f"standard_{asset_id}_", dir=transaction_parent)
    staging_root = os.path.join(transaction_root, "staged")
    staged_package = os.path.join(staging_root, asset_id)
    backup = os.path.join(transaction_root, "previous")
    moved_old = False
    owns_marker = False
    recovered = True
    try:
        # Keep a failed/crashed writer's marker; never guess that its lease expired.
        with open(marker, "x", encoding="utf-8") as handle:
            owns_marker = True
            json.dump({"assetId": asset_id, "transactionRoot": transaction_root}, handle)
        if os.path.islink(previous_package):
            raise RuntimeError("Standard package cannot be a symbolic link.")
        if os.path.isdir(previous_package):
            shutil.copytree(previous_package, staged_package, copy_function=shutil.copy2)
        else:
            os.makedirs(staged_package)
        result = export_callback(staging_root)
        _prepare_package(staged_package, previous_package, asset_id)
        if os.path.exists(previous_package):
            os.replace(previous_package, backup)
            moved_old = True
        try:
            os.replace(staged_package, previous_package)
        except Exception as publish_error:
            if moved_old:
                try:
                    os.replace(backup, previous_package)
                    moved_old = False
                except Exception as recovery_error:
                    recovered = False
                    raise RuntimeError(
                        f"Standard publication and rollback failed; recovery files retained at "
                        f"'{transaction_root}' and marker '{marker}': {recovery_error}"
                    ) from publish_error
            raise
        return result
    finally:
        if recovered:
            if owns_marker:
                # If marker removal fails, retain recovery files and report the
                # error; silently leaving a permanently blocked package is unsafe.
                os.remove(marker)
                published_manifest = os.path.join(previous_package, "manifest.json")
                if os.path.isfile(published_manifest):
                    # Also wake a reader after a failed long export: its bounded
                    # retry may have expired while this marker blocked the old,
                    # still-valid package that we preserved or restored.
                    os.utime(published_manifest, None)
            shutil.rmtree(transaction_root, ignore_errors=True)
