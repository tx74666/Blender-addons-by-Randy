"""Publish verified native Extend/Arc boundary-data assets with exact evidence.

Run with ordinary Python after isolated Blender numerical validation. This
publisher does not start Blender or deploy into an active asset library.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path

from deploy_assets import file_identity, read_optional, replace_checked, rollback_owned, stage_bytes
from finalize_mix_shaders import digest


ROOT = Path(__file__).resolve().parents[2]
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
LIBRARY_VERSION = "0.3.0"
SPECS = {
    "extend_mask": {
        "name": "Extend Mask", "filename": "Randy_Extend_Mask.blend", "version": "0.1.0",
        "deploy": "deploy_extend_mask.py",
        "inputs": [("Source", "NodeSocketBundle"), ("Mode", "NodeSocketMenu"),
                   ("Width", "NodeSocketFloat"), ("Gap", "NodeSocketFloat"),
                   ("Softness", "NodeSocketFloat")],
        "outputs": [("Mask", "NodeSocketFloat"), ("Inner Data", "NodeSocketBundle"),
                    ("Outer Data", "NodeSocketBundle")],
        "description": "Inner/Outer/Both create radial border bands from Arc Mask data while preserving its angular span; Outline extends the complete contour including arc ends. Radial outputs can chain into further bands; Outline exposes only its final Mask.",
    },
    "arc_mask": {
        "name": "Arc Mask", "filename": "Randy_Arc_Mask.blend", "version": "0.2.0",
        "deploy": "deploy_ring_nodes.py",
        "inputs": [("Inner Radius", "NodeSocketFloat"), ("Ring Width", "NodeSocketFloat"),
                   ("Edge Softness", "NodeSocketFloat"), ("Start Angle", "NodeSocketFloat"),
                   ("Sweep Angle", "NodeSocketFloat")],
        "outputs": [("Mask", "NodeSocketFloat"), ("Ring Data", "NodeSocketBundle")],
        "description": "Normalized UV rings and arcs with an unchanged Mask output and reusable radial/angular boundary data. Sweep Angle 360 gives a full ring.",
    },
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_interface(metadata, spec):
    result = {}
    for direction in ("inputs", "outputs"):
        items = metadata.get(direction)
        require(isinstance(items, list) and all(isinstance(item, dict) for item in items),
                "Verification must record explicit interface objects: " + direction)
        require([(item.get("name"), item.get("type")) for item in items] == spec[direction],
                "Verified interface differs from the published " + direction)
        # Preserve verified native menu defaults and choices rather than inventing
        # a numeric enum index or attempting to infer it from the socket name.
        result[direction] = items
    return result


def _write_checked(path, contents, expected, expected_identity):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = stage_bytes(path, contents)
    try:
        return replace_checked(staged, path, expected, expected_identity)
    finally:
        staged.unlink(missing_ok=True)


def finalize(kind, asset, verification, backups):
    spec = SPECS[kind]
    asset, verification, backups = asset.resolve(), verification.resolve(), backups.resolve()
    candidate, raw_report = asset.read_bytes(), verification.read_bytes()
    report = json.loads(raw_report.decode("utf-8-sig"))
    require(report.get("passed") is True and report.get("state") == "complete", "Verification did not complete and pass")
    tests = report.get("tests")
    require(isinstance(tests, list) and tests and all(test.get("passed") is True for test in tests),
            "All recorded verification checks must pass")
    require(report.get("asset_sha256") == digest(candidate), "Verified asset bytes changed")
    require(type(report.get("shader_sample_count")) is int and report["shader_sample_count"] > 0,
            "Behavior/interface publication requires fresh numerical shader samples")
    metadata = report.get("asset_metadata", {})
    require(metadata.get("name") == spec["name"] and metadata.get("version") == spec["version"],
            "Wrong asset name/version in verification")
    require(metadata.get("catalog_id") == CATALOG_ID and metadata.get("color_tag") == "TEXTURE",
            "Expected the Textures catalog and native Texture color tag")
    interface = checked_interface(metadata, spec)
    dependencies = report.get("source_dependencies_sha256")
    require(isinstance(dependencies, dict) and dependencies, "Missing exact verification source dependencies")
    roles = {
        "build": "tools/randy_node_assets/build_" + kind + ".py",
        "verify": "tools/randy_node_assets/verify_" + kind + ".py",
        "deploy": "tools/randy_node_assets/" + spec["deploy"],
    }
    require(set(roles.values()) - {roles["deploy"]} <= set(dependencies),
            "Verification must bind the build and numerical verifier sources")
    require("tools/randy_node_assets/ring_boundary.py" in dependencies,
            "Verification must bind the shared boundary bundle and distance implementation")
    for relative, expected in dependencies.items():
        path = (ROOT / relative).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), "Dependency must be an existing repository file: " + relative)
        require(digest(path.read_bytes(), text=True) == expected, "Source changed after verification: " + relative)
    for relative in roles.values():
        require((ROOT / relative).is_file(), "Missing publication source: " + relative)
    radial_hash = report.get("ring_mask_sha256", report.get("ring_asset_sha256"))
    if radial_hash is not None:
        require(digest((ROOT / "node_library/dependencies/Randy_Ring_Mask.blend").read_bytes()) == radial_hash,
                "Historical radial dependency changed")
    if kind == "extend_mask":
        arc_hash = report.get("arc_mask_sha256", report.get("arc_asset_sha256"))
        if arc_hash is not None:
            require(digest((ROOT / "node_library/assets/Randy_Arc_Mask.blend").read_bytes()) == arc_hash,
                    "Publish the exact validated Arc Mask dependency before Extend Mask")
    preview_value = report.get("render_preview") or report.get("examples", {}).get("image")
    require(isinstance(preview_value, str) and preview_value, "Missing validated preview file")
    preview = Path(preview_value).read_bytes()
    require(preview.startswith(b"\x89PNG\r\n\x1a\n"), "Preview is not a PNG file")
    manifest_path = ROOT / "node_library/manifest.json"
    manifest_before = manifest_path.read_bytes()
    manifest = json.loads(manifest_before)
    existing_version = tuple(int(part) for part in manifest["library_version"].split("."))
    require(existing_version <= (0, 3, 0), "Refusing to downgrade a newer library manifest")
    bundle = {
        "id": kind, "path": "node_library/assets/" + spec["filename"], "sha256": digest(candidate),
        "source": roles,
        "source_sha256": {relative: digest((ROOT / relative).read_bytes(), text=True) for relative in roles.values()},
        "source_dependencies_sha256": dependencies, "verification": "node_library/validation/" + kind + ".json",
    }
    entry = {
        "id": kind, "name": spec["name"], "display_name": spec["name"], "version": spec["version"],
        "node_tree_type": "ShaderNodeTree", "bundle_id": kind,
        "catalog_path": "Textures", "catalog_id": CATALOG_ID, "description": spec["description"],
        **interface,
        "runtime_note": "Native Shader Node Group computation and saved connections require no add-on. Boundary data is radial/angular data, not arbitrary black/white mask dilation.",
    }
    for key, updated in (("bundles", bundle), ("assets", entry)):
        matches = [index for index, old in enumerate(manifest[key]) if old["id"] == kind]
        require(len(matches) <= 1, "Duplicate manifest identity: " + kind)
        if matches:
            manifest[key][matches[0]] = updated
        else:
            manifest[key].append(updated)
    manifest["library_version"] = LIBRARY_VERSION
    report["published_asset"] = bundle["path"]
    report["published_preview"] = "node_library/previews/" + kind + ".png"
    writes = {
        ROOT / bundle["path"]: candidate,
        ROOT / report["published_preview"]: preview,
        ROOT / bundle["verification"]: (json.dumps(report, indent=2) + "\n").encode("utf-8"),
    }
    previous_report = None
    if kind == "arc_mask":
        previous_report = read_optional(ROOT / bundle["verification"])
        if previous_report is not None:
            previous = json.loads(previous_report)
            previous_version = previous.get("asset_metadata", {}).get("version")
            if previous_version == "0.1.1":
                archive = ROOT / "node_library/validation/arc_mask_0_1_1_baseline.json"
                archived = read_optional(archive)
                require(archived in (None, previous_report), "Different Arc 0.1.1 baseline evidence already exists")
                writes[archive] = previous_report
                report["previous_verification"] = archive.relative_to(ROOT).as_posix()
                report["previous_verification_sha256"] = digest(previous_report, text=True)
                report["previous_verification_hash_mode"] = "utf8-lf"
                report["previous_asset_sha256"] = previous.get("asset_sha256")
                writes[ROOT / bundle["verification"]] = (json.dumps(report, indent=2) + "\n").encode("utf-8")
    writes[manifest_path] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    require(not backups.is_relative_to((ROOT / "node_library/assets").resolve()),
            "Publication backups must stay outside the visible asset library")
    require(not any(path.is_symlink() for path in writes), "Refusing symlink publication destinations")
    snapshots = {path: read_optional(path) for path in writes}
    # The manifest is based on these exact original bytes, never on a later save.
    snapshots[manifest_path] = manifest_before
    if previous_report is not None:
        # Do not back up a later report edit and then overwrite it after deriving
        # archive provenance from different original bytes.
        snapshots[ROOT / bundle["verification"]] = previous_report
    identities = {path: file_identity(path) if prior is not None else None for path, prior in snapshots.items()}
    backup = backups / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + kind)
    for path, prior in snapshots.items():
        if prior is not None and prior != writes[path]:
            saved = backup / path.relative_to(ROOT)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(prior)
            require(saved.read_bytes() == prior, "Publication backup verification failed")
    for path, prior in snapshots.items():
        require(read_optional(path) == prior and (prior is None or file_identity(path) == identities[path]),
                "Destination changed before publication: " + str(path))
    require(asset.read_bytes() == candidate and verification.read_bytes() == raw_report,
            "Candidate or verification changed during publication preparation")
    committed = []
    try:
        for path, contents in writes.items():
            if contents == snapshots[path]:
                continue
            identity = _write_checked(path, contents, snapshots[path], identities[path])
            committed.append((path, snapshots[path], contents, identity))
    except BaseException as error:
        for path, prior, contents, identity in reversed(committed):
            try:
                if not rollback_owned(path, prior, contents, identity):
                    error.add_note("Rollback skipped externally changed publication {}; backup: {}".format(path, backup))
            except Exception as rollback_error:
                error.add_note("Rollback failed for publication {}: {}; backup: {}".format(path, rollback_error, backup))
        raise
    return {"passed": True, "asset": bundle["path"], "version": spec["version"],
            "tests": len(tests), "samples": report["shader_sample_count"], "backup": str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=tuple(SPECS), default="extend_mask")
    for name in ("asset", "verification", "backups"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.kind, args.asset, args.verification, args.backups), indent=2))


if __name__ == "__main__":
    main()
