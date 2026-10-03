"""Publish verified multifunction Ring Mask with exact Arc baseline evidence.

This ordinary-Python tool replaces the Arc manifest identity with ring_mask,
preserves the Arc 0.2.0 binary/report outside the visible asset directory, and
uses guarded atomic writes with backups and ownership-aware rollback. It
does not remove visible Arc files or change an active Blender scene.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path

from deploy_assets import file_identity, read_optional, replace_checked, rollback_owned, stage_bytes
from finalize_mix_shaders import digest


ROOT = Path(__file__).resolve().parents[2]
LIBRARY_VERSION = "0.3.1"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
INPUTS = [(n, "NodeSocketFloat") for n in
          ("Inner Radius", "Ring Width", "Edge Softness", "Start Angle", "Sweep Angle")]
OUTPUTS = [("Mask", "NodeSocketFloat"), ("Ring Data", "NodeSocketBundle")]
ROLES = {role: "tools/randy_node_assets/" + filename for role, filename in (
    ("build", "build_ring_mask_current.py"), ("verify", "verify_ring_mask_current.py"),
    ("deploy", "deploy_ring_mask_current.py"))}
BASELINE = "node_library/validation/fixtures/Randy_Arc_Mask_0_2_0.blend"
BASELINE_REPORT = "node_library/validation/arc_mask_0_2_0_baseline.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _write_checked(path, contents, expected, expected_identity):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = stage_bytes(path, contents)
    try:
        return replace_checked(staged, path, expected, expected_identity)
    finally:
        staged.unlink(missing_ok=True)


def finalize(asset, verification, backups):
    asset, verification, backups = (p.resolve() for p in (asset, verification, backups))
    candidate, original_report = asset.read_bytes(), verification.read_bytes()
    report = json.loads(original_report.decode("utf-8-sig"))
    require(report.get("passed") is True and report.get("state") == "complete", "Verification did not complete and pass")
    tests = report.get("tests")
    require(isinstance(tests, list) and tests and all(t.get("passed") is True for t in tests), "All verification checks must pass")
    require(report.get("asset_sha256") == digest(candidate), "Verified asset bytes changed")
    require(report.get("recursive_graph_matches_arc_0_2_0") is True, "Missing exact native graph equality proof")
    require(type(report.get("shader_sample_count")) is int and report["shader_sample_count"] > 0, "Missing fresh native shader samples")
    metadata = report.get("asset_metadata", {})
    require(metadata.get("name") == "Ring Mask" and metadata.get("version") == "0.2.1", "Wrong asset name/version")
    require(metadata.get("catalog_id") == CATALOG_ID and metadata.get("color_tag") == "TEXTURE", "Wrong native Texture catalog/tag")
    for direction, expected in (("inputs", INPUTS), ("outputs", OUTPUTS)):
        items = metadata.get(direction)
        require(isinstance(items, list) and all(isinstance(i, dict) for i in items), "Interface must contain explicit socket records")
        require([(i.get("name"), i.get("type")) for i in items] == expected, "Wrong multifunction interface: " + direction)
    require(metadata["inputs"][-1].get("default") == 360.0, "Current Ring Mask must default to a full ring")
    dependencies = report.get("source_dependencies_sha256")
    require(isinstance(dependencies, dict) and {ROLES["build"], ROLES["verify"],
            "tools/randy_node_assets/ring_boundary.py"} <= set(dependencies), "Missing exact build/verifier/boundary provenance")
    for relative, expected in dependencies.items():
        path = (ROOT / relative).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), "Invalid source dependency: " + relative)
        require(digest(path.read_bytes(), text=True) == expected, "Source changed after verification: " + relative)
    for relative in ROLES.values():
        require((ROOT / relative).is_file(), "Missing source: " + relative)
    source = Path(report.get("source_arc_asset", "")).resolve()
    source_bytes = source.read_bytes()
    require(digest(source_bytes) == report.get("source_arc_sha256"), "Verified Arc baseline changed")
    require(report.get("compatibility_baseline") == {"path": BASELINE, "sha256": digest(source_bytes)}, "Wrong compatibility baseline identity")
    arc_report_path = ROOT / BASELINE_REPORT
    arc_report = read_optional(arc_report_path)
    if arc_report is None:
        arc_report_path = ROOT / "node_library/validation/arc_mask.json"
        arc_report = arc_report_path.read_bytes()
    prior = json.loads(arc_report.decode("utf-8-sig"))
    require(prior.get("passed") is True and prior.get("state") == "complete" and
            prior.get("asset_metadata", {}).get("version") == "0.2.0" and
            prior.get("asset_sha256") == digest(source_bytes), "Baseline must match the verified Arc 0.2.0 asset/report")
    for path, expected in ((ROOT / BASELINE, source_bytes), (ROOT / BASELINE_REPORT, arc_report)):
        require(read_optional(path) in (None, expected), "A different baseline already exists: " + str(path))
    preview_value = report.get("render_preview") or report.get("examples", {}).get("image")
    require(isinstance(preview_value, str) and preview_value, "Missing fresh render preview")
    preview = Path(preview_value).read_bytes()
    require(preview.startswith(b"\x89PNG\r\n\x1a\n"), "Preview must be a PNG")
    manifest_path = ROOT / "node_library/manifest.json"
    manifest_before = manifest_path.read_bytes()
    manifest = json.loads(manifest_before)
    require(tuple(map(int, manifest["library_version"].split("."))) <= (0, 3, 1), "Refusing to downgrade a newer library")
    bundle = {"id": "ring_mask", "path": "node_library/assets/Randy_Ring_Mask.blend", "sha256": digest(candidate),
              "source": ROLES, "source_sha256": {p: digest((ROOT / p).read_bytes(), text=True) for p in ROLES.values()},
              "source_dependencies_sha256": dependencies, "verification": "node_library/validation/ring_mask_current.json"}
    entry = {"id": "ring_mask", "name": "Ring Mask", "display_name": "Ring Mask", "version": "0.2.1",
             "node_tree_type": "ShaderNodeTree", "bundle_id": "ring_mask", "catalog_path": "Textures",
             "catalog_id": CATALOG_ID, "inputs": metadata["inputs"], "outputs": metadata["outputs"],
             "description": "A full ring by default, adjustable counterclockwise arcs, radial softness and reusable Ring Data for Extend Mask.",
             "runtime_note": "Native Shader Node Group. Sweep Angle 360 is a full ring; no runtime add-on is required."}
    for key, replacement in (("bundles", bundle), ("assets", entry)):
        require(sum(i["id"] == "arc_mask" for i in manifest[key]) <= 1 and
                sum(i["id"] == "ring_mask" for i in manifest[key]) <= 1, "Duplicate manifest identity")
        kept, inserted = [], False
        for old in manifest[key]:
            if old["id"] in ("arc_mask", "ring_mask"):
                if not inserted:
                    kept.append(replacement)
                    inserted = True
            else:
                kept.append(old)
        if not inserted:
            kept.append(replacement)
        manifest[key] = kept
    manifest["library_version"] = LIBRARY_VERSION
    report.update(published_asset=bundle["path"], published_preview="node_library/previews/ring_mask_current.png",
                  previous_verification=BASELINE_REPORT,
                  previous_verification_sha256=digest(arc_report, text=True), previous_verification_hash_mode="utf8-lf")
    writes = {
        ROOT / BASELINE: source_bytes,
        ROOT / BASELINE_REPORT: arc_report,
        ROOT / bundle["path"]: candidate,
        ROOT / report["published_preview"]: preview,
        ROOT / bundle["verification"]: (json.dumps(report, indent=2) + "\n").encode("utf-8"),
        manifest_path: (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }
    require(not backups.is_relative_to((ROOT / "node_library/assets").resolve()), "Backups must stay outside visible assets")
    require(not any(p.is_symlink() for p in writes), "Refusing symlink publication destinations")
    snapshots = {p: read_optional(p) for p in writes}
    snapshots[manifest_path] = manifest_before
    # Derive and write baseline provenance from the exact original bytes, not
    # from a later editor save picked up while assembling the write set.
    if snapshots[ROOT / BASELINE] is not None:
        snapshots[ROOT / BASELINE] = source_bytes
    if snapshots[ROOT / BASELINE_REPORT] is not None:
        snapshots[ROOT / BASELINE_REPORT] = arc_report
    identities = {p: file_identity(p) if old is not None else None for p, old in snapshots.items()}
    backup = backups / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_ring_mask_current")
    for path, old in snapshots.items():
        if old is not None and old != writes[path]:
            saved = backup / path.relative_to(ROOT)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(old)
            require(saved.read_bytes() == old, "Publication backup verification failed")
    for path, old in snapshots.items():
        require(read_optional(path) == old and (old is None or file_identity(path) == identities[path]),
                "Destination changed before publication: " + str(path))
    require(asset.read_bytes() == candidate and verification.read_bytes() == original_report and source.read_bytes() == source_bytes
            and arc_report_path.read_bytes() == arc_report,
            "Source or report changed during publication preparation")
    committed = []
    try:
        for path, contents in writes.items():
            if contents == snapshots[path]:
                continue
            identity = _write_checked(path, contents, snapshots[path], identities[path])
            committed.append((path, snapshots[path], contents, identity))
    except BaseException as error:
        for path, old, contents, identity in reversed(committed):
            try:
                if not rollback_owned(path, old, contents, identity):
                    error.add_note("Rollback skipped externally changed publication {}; backup: {}".format(path, backup))
            except Exception as rollback_error:
                error.add_note("Rollback failed for publication {}: {}; backup: {}".format(path, rollback_error, backup))
        raise
    return {"passed": True, "asset": bundle["path"], "version": "0.2.1", "library_version": LIBRARY_VERSION,
            "tests": len(tests), "samples": report["shader_sample_count"], "backup": str(backup),
            "old_arc_visible_file_removed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("asset", "verification", "backups"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(finalize(args.asset, args.verification, args.backups), indent=2))


if __name__ == "__main__":
    main()
