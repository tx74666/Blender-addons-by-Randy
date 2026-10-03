"""Publish a passed, graph-equivalent Arc Mask metadata build with backups.

This path does not relabel historical samples as new renders. The historical
Arc report is archived and hash-linked, and the source-path-only retirement
evidence is superseded only for files now bound by fresh metadata validation.
"""

import argparse
import json
from pathlib import Path

from deploy_assets import file_identity, read_optional, replace_checked, rollback_owned, stage_bytes
from finalize_mix_shaders import digest


ROOT = Path(__file__).resolve().parents[2]


def publish(asset, verification, backups):
    raw_report = verification.read_bytes()
    report = json.loads(raw_report)
    candidate = asset.read_bytes()
    assert report.get("passed") is True and report.get("state") == "complete"
    assert report.get("render_repeated") is False
    assert report.get("tests") and all(test.get("passed") is True for test in report["tests"])
    assert report.get("asset_sha256") == digest(candidate)
    assert report.get("asset_metadata", {}).get("name") == "Arc Mask"
    assert report["asset_metadata"].get("version") == "0.1.1"
    assert report["asset_metadata"].get("catalog_id") == "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
    assert report["asset_metadata"].get("color_tag") == "TEXTURE"
    dependencies = report["source_dependencies_sha256"]
    assert set(dependencies) == {"tools/randy_node_assets/build_arc_mask.py", "tools/randy_node_assets/verify_arc_mask.py",
                                 "tools/randy_node_assets/verify_ring_mask.py", "tools/randy_node_assets/verify_arc_mask_metadata.py"}
    for relative, expected in dependencies.items():
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT) and path.is_file()
        assert digest(path.read_bytes(), text=True) == expected
    assert digest((ROOT / "node_library/dependencies/Randy_Ring_Mask.blend").read_bytes()) == report["ring_asset_sha256"]
    manifest_path = ROOT / "node_library/manifest.json"
    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    bundle = next(item for item in manifest["bundles"] if item["id"] == "arc_mask")
    entry = next(item for item in manifest["assets"] if item["id"] == "arc_mask")
    published = ROOT / bundle["path"]
    old_asset = published.read_bytes()
    old_report_path = ROOT / bundle["verification"]
    old_report = old_report_path.read_bytes()
    assert digest(old_asset) == report["baseline_sha256"] == bundle["sha256"]
    assert digest(old_report, text=True) == report["baseline_verification_sha256"]
    archive = "node_library/validation/arc_mask_0_1_0_baseline.json"
    report["baseline_verification"] = archive
    report["published_asset"] = bundle["path"]
    report["published_preview"] = "node_library/previews/arc_mask.png"
    report["preview_note"] = "Historical shader preview; calculation is unchanged, no new render."
    bundle["sha256"] = digest(candidate)
    bundle["source_sha256"] = {relative: digest((ROOT / relative).read_bytes(), text=True) for relative in bundle["source"].values()}
    bundle["source_dependencies_sha256"] = dependencies
    entry["version"] = "0.1.1"
    entry["description"] = "Normalized UV Ring and Arc Mask. Sweep Angle 360 gives a full ring; hidden shared radial implementation; native Texture color tag."

    retirement_path = ROOT / manifest["retirement_verification"]
    retirement_before = retirement_path.read_bytes()
    retirement = json.loads(retirement_before)
    superseded = {"tools/randy_node_assets/build_arc_mask.py", "tools/randy_node_assets/verify_arc_mask.py",
                  "tools/randy_node_assets/finalize_native_ring_nodes.py", "tests/test_native_ring_finalization.py"}
    archived_changes = [change for change in retirement["source_path_only_changes"] if change["path"] in superseded]
    retirement["source_path_only_changes"] = [change for change in retirement["source_path_only_changes"] if change["path"] not in superseded]
    retirement["source_path_migrations_superseded_by_metadata_update"] = archived_changes
    retirement["superseding_arc_metadata_verification"] = bundle["verification"]
    retirement["unchanged_native_assets"].pop(bundle["path"], None)
    retirement["evidence_note"] = "The radial fixture is byte-identical. Mixer/Ring Group default fixture relocation is checked by AST equality. Arc build/verifier source and native metadata are now bound by the fresh recursive graph-equivalence report; historical samples were not rerendered."

    writes = {published: candidate,
              ROOT / archive: old_report,
              old_report_path: (json.dumps(report, indent=2) + "\n").encode("utf-8"),
              retirement_path: (json.dumps(retirement, indent=2) + "\n").encode("utf-8")}
    # The current Ring Group graph has no nested Arc. Its earlier shader samples
    # remain valid against the graph-identical Arc dependency. Runtime generator
    # hash changes require their own validation and are deliberately untouched.
    ring_bundle = next((item for item in manifest["bundles"] if item["id"] == "ring_group"), None)
    if ring_bundle is not None:
        ring_report_path = ROOT / ring_bundle["verification"]
        ring_before = ring_report_path.read_bytes()
        ring_report = json.loads(ring_before)
        ring_report["arc_mask_sha256_before_metadata_update"] = ring_report["arc_mask_sha256"]
        ring_report["arc_mask_sha256"] = bundle["sha256"]
        ring_report["arc_metadata_equivalence_verification"] = bundle["verification"]
        for key in ("source_sha256", "source_dependencies_sha256"):
            for relative in dependencies:
                if relative in ring_bundle.get(key, {}):
                    ring_bundle[key][relative] = dependencies[relative]
        for relative in dependencies:
            if relative in ring_report.get("source_dependencies_sha256", {}):
                ring_report["source_dependencies_sha256"][relative] = dependencies[relative]
        writes[ring_report_path] = (json.dumps(ring_report, indent=2) + "\n").encode("utf-8")
    writes[manifest_path] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert not backups.resolve().is_relative_to((ROOT / "node_library/assets").resolve())
    snapshots = {path: read_optional(path) for path in writes}
    snapshots[manifest_path] = original_manifest
    snapshots[published] = old_asset
    snapshots[old_report_path] = old_report
    snapshots[retirement_path] = retirement_before
    if ring_bundle is not None:
        snapshots[ring_report_path] = ring_before
    if ROOT / archive in snapshots and snapshots[ROOT / archive] is not None:
        assert snapshots[ROOT / archive] == old_report, "Refusing to replace different historical baseline evidence."
    if any(path.is_symlink() for path in writes):
        raise RuntimeError("Refusing symlink publication destinations.")
    identities = {path: file_identity(path) if contents is not None else None for path, contents in snapshots.items()}
    for path, contents in snapshots.items():
        if contents is not None and contents != writes[path]:
            backup = backups / path.relative_to(ROOT)
            if backup.exists():
                raise FileExistsError("Use a fresh publication backup folder.")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(contents)
    for path, contents in snapshots.items():
        if read_optional(path) != contents or (contents is not None and file_identity(path) != identities[path]):
            raise RuntimeError("Publication changed before write: " + str(path))
    committed = []
    try:
        for path, contents in writes.items():
            if contents == snapshots[path]:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            staged = stage_bytes(path, contents)
            try:
                identity = replace_checked(staged, path, snapshots[path], identities[path])
            finally:
                staged.unlink(missing_ok=True)
            committed.append((path, snapshots[path], contents, identity))
    except BaseException:
        for path, prior, contents, identity in reversed(committed):
            rollback_owned(path, prior, contents, identity)
        raise
    return {"passed": True, "version": "0.1.1", "metadata_checks": len(report["tests"]),
            "baseline_shader_samples": report["baseline_shader_sample_count"], "render_repeated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("asset", "verification", "backups"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.asset.resolve(), args.verification.resolve(), args.backups.resolve()), indent=2))


if __name__ == "__main__":
    main()
