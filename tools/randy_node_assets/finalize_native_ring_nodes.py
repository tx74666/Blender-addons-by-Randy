"""Publish passed native ring assets with backups and exact source/report hashes.

Run with ordinary Python after isolated Blender validation. Never deploy into a
live asset library here; deployment uses the guarded deployment scripts.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from deploy_assets import file_identity, read_optional, replace_checked, rollback_owned, stage_bytes
from finalize_mix_shaders import digest

ROOT = Path(__file__).resolve().parents[2]
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
SPECS = {
    "mix_shaders": ("Mix Shaders", "Randy_Mix_Shaders.blend", "0.2.0", "deploy_mix_shaders.py"),
    "arc_mask": ("Arc Mask", "Randy_Arc_Mask.blend", "0.1.1", "deploy_ring_nodes.py"),
    "ring_group": ("Ring Group", "Randy_Ring_Group.blend", "0.1.0", "deploy_ring_nodes.py")
}

def interface(kind):
    f = lambda name, default=0.0: {"name": name, "type": "NodeSocketFloat", "default": default}
    s = lambda name: {"name": name, "type": "NodeSocketShader"}
    if kind == "mix_shaders":
        return [s("Base Shader"), f("Mask 1"), s("Shader 1"), f("Mask 2"), s("Shader 2")], [s("Shader")]
    if kind == "ring_group":
        return [s("Shader"), f("Mask 1"), f("Mask 2")], [{"name": "Mask", "type": "NodeSocketFloat"}, s("Shader")]
    return [f("Inner Radius", .6), f("Ring Width", .08), f("Edge Softness"), f("Start Angle"), f("Sweep Angle", 180)], [{"name": "Mask", "type": "NodeSocketFloat"}]


def _write_checked(path, contents, expected, expected_identity):
    """Replace a publication file and return the identity of our staged file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = stage_bytes(path, contents)
    try:
        return replace_checked(temporary, path, expected, expected_identity)
    finally:
        temporary.unlink(missing_ok=True)


def finalize(kind, asset, verification, backups):
    name, filename, version, deploy = SPECS[kind]
    source = asset.read_bytes()
    report = json.loads(verification.read_text(encoding="utf-8-sig"))
    assert report.get("passed") is True and report.get("state") == "complete"
    assert report.get("tests") and all(t.get("passed") is True for t in report["tests"])
    assert report["asset_sha256"] == digest(source)
    assert report["asset_metadata"]["name"] == name and report["asset_metadata"]["catalog_id"] == CATALOG_ID
    dependencies = report.get("source_dependencies_sha256")
    assert dependencies
    for relative, expected in dependencies.items():
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT) and path.is_file()
        assert digest(path.read_bytes(), text=True) == expected, "Source changed: " + relative
    ring_hash = report.get("ring_mask_sha256", report.get("ring_asset_sha256"))
    if ring_hash:
        assert digest((ROOT / "node_library/dependencies/Randy_Ring_Mask.blend").read_bytes()) == ring_hash
    if kind == "ring_group":
        assert digest((ROOT / "node_library/assets/Randy_Arc_Mask.blend").read_bytes()) == report["arc_mask_sha256"]
    preview_path = Path(report.get("render_preview") or report["examples"]["image"])
    preview = preview_path.read_bytes()
    assert preview.startswith(b"\x89PNG\r\n\x1a\n")
    manifest_path = ROOT / "node_library/manifest.json"
    before = manifest_path.read_bytes()
    manifest = json.loads(before)
    roles = {"build": "tools/randy_node_assets/build_" + kind + ".py",
             "verify": "tools/randy_node_assets/verify_" + kind + ".py",
             "deploy": "tools/randy_node_assets/" + deploy}
    bundle = {"id": kind, "path": "node_library/assets/" + filename,
              "sha256": digest(source), "source": roles,
              "source_sha256": {path: digest((ROOT / path).read_bytes(), text=True) for path in roles.values()},
              "source_dependencies_sha256": dependencies,
              "verification": "node_library/validation/" + kind + ".json"}
    inputs, outputs = interface(kind)
    descriptions = {
        "mix_shaders": "Native expandable Mask / Shader mixing over a Base. Earlier slots cover later slots; no runtime add-on required.",
        "arc_mask": "Counterclockwise angular crop of the existing normalized UV Ring Mask, in degrees; 180 semicircle, 360 full ring.",
        "ring_group": "Union multiple Ring and Arc masks using Maximum and share one Shader, without runtime add-on dependencies."}
    entry = {"id": kind, "name": name, "display_name": name, "version": version,
             "node_tree_type": "ShaderNodeTree", "bundle_id": kind,
             "catalog_path": "Textures", "catalog_id": CATALOG_ID,
             "description": descriptions[kind], "inputs": inputs, "outputs": outputs,
             "runtime_note": "Computation and editing ordinary connections need no add-on. RR Helper provides optional edit-time shortcuts; native chaining has no fixed count."}
    for key, value in (("bundles", bundle), ("assets", entry)):
        matches = [i for i, old in enumerate(manifest[key]) if old["id"] == kind]
        assert len(matches) <= 1
        if matches:
            manifest[key][matches[0]] = value
        else:
            manifest[key].append(value)
    manifest["library_version"] = "0.2.1"
    report["published_asset"] = bundle["path"]
    report["published_preview"] = "node_library/previews/" + kind + ".png"
    writes = {
        ROOT / bundle["path"]: source,
        ROOT / report["published_preview"]: preview,
        ROOT / bundle["verification"]: (json.dumps(report, indent=2) + "\n").encode("utf-8"),
        manifest_path: (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}
    assert not backups.resolve().is_relative_to((ROOT / "node_library/assets").resolve())
    if any(path.is_symlink() for path in writes):
        raise RuntimeError("Refusing symlink publication destinations.")
    snapshots = {path: read_optional(path) for path in writes}
    # The new manifest was derived from 'before'. Do not adopt a concurrent
    # manifest save as an expected destination and then overwrite its edits.
    snapshots[manifest_path] = before
    identities = {path: file_identity(path) if contents is not None else None
                  for path, contents in snapshots.items()}
    backup = backups / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + kind)
    for path, contents in snapshots.items():
        if contents is not None and contents != writes[path]:
            saved = backup / path.relative_to(ROOT)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(contents)
            assert saved.read_bytes() == contents
    # Check all destinations before the first mutation. Each subsequent
    # replacement checks its own bytes and identity immediately before replace.
    for path, expected in snapshots.items():
        if read_optional(path) != expected or (expected is not None and file_identity(path) != identities[path]):
            raise RuntimeError("Destination changed before publication: {}".format(path))
    committed = []
    try:
        # Assets/evidence first, manifest last. A late failure restores only
        # still-owned replacements; a concurrent save or new file is preserved.
        for path, contents in writes.items():
            if contents != snapshots[path]:
                identity = _write_checked(path, contents, snapshots[path], identities[path])
                committed.append((path, snapshots[path], contents, identity))
    except BaseException as error:
        for path, original, contents, identity in reversed(committed):
            try:
                if not rollback_owned(path, original, contents, identity):
                    error.add_note("Rollback skipped externally changed publication {}; backup: {}".format(path, backup))
            except Exception as rollback_error:
                error.add_note("Rollback failed for publication {}: {}; backup: {}".format(path, rollback_error, backup))
        raise
    return {"passed": True, "asset": bundle["path"], "version": version,
            "tests": len(report["tests"]), "samples": report["shader_sample_count"], "backup": str(backup)}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kind", choices=tuple(SPECS), required=True)
    for name in ("asset", "verification", "backups"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(finalize(args.kind, args.asset.resolve(), args.verification.resolve(), args.backups.resolve()), indent=2))

if __name__ == "__main__":
    main()
