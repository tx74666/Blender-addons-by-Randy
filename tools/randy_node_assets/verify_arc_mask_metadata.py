"""Build Arc Mask's metadata update and prove its graph equals the rendered baseline.

Run in one isolated factory Blender session. There is no numerical rendering:
the saved baseline render report remains explicit, hashed evidence. Only the
asset version and internal radial datablock name change. User scenes are never
opened, modified, saved or used for validation.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_arc_mask import ROOT, NAME, VERSION, RING_ASSET, append_ring, build, source_hash
from verify_arc_mask import radial_graph, validate


def digest(data, *, text=False):
    return hashlib.sha256(data.replace(b"\r\n", b"\n") if text else data).hexdigest()


def graph(group):
    """Exact node/socket/link snapshot, including every shared nested graph."""
    result = radial_graph(group)
    result["node_tree_type"] = group.bl_idname
    result["group_color_tag"] = group.color_tag
    result["node_flags"] = sorted((node.name, bool(node.mute)) for node in group.nodes)
    result["outputs_active"] = sorted((node.name, bool(node.is_active_output)) for node in group.nodes
                                      if node.bl_idname == "NodeGroupOutput")
    result["contracts"] = {key: group.get(key) for key in ("randy_uv_contract", "randy_softness_contract", "randy_arc_contract")
                           if key in group}
    result["nested_graphs"] = {node.name: graph(node.node_tree) for node in group.nodes
                               if node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None}
    return result


def append(path, name):
    with bpy.data.libraries.load(str(path), link=False) as (available, requested):
        assert name in available.node_groups
        requested.node_groups = [name]
    return requested.node_groups[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / "node_library/assets/Randy_Arc_Mask.blend")
    parser.add_argument("--baseline-verification", type=Path, default=ROOT / "node_library/validation/arc_mask.json")
    parser.add_argument("--ring-asset", type=Path, default=RING_ASSET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Use an isolated background Blender session.")
    baseline, evidence, ring_path, output, report_path = (path.resolve() for path in
        (args.baseline, args.baseline_verification, args.ring_asset, args.output, args.report))
    if output.exists() or report_path.exists():
        raise FileExistsError("Use fresh metadata validation output paths.")
    originals = {path: path.read_bytes() for path in (baseline, evidence, ring_path)}
    prior = json.loads(originals[evidence])
    assert prior["passed"] is True and prior["asset_sha256"] == digest(originals[baseline])
    assert prior["tests"] and all(test["passed"] is True for test in prior["tests"])
    assert prior["shader_sample_count"] > 0
    bpy.ops.wm.read_factory_settings(use_empty=True)
    old = append(baseline, NAME)
    assert old.get("randy_asset_version") == "0.1.0"
    expected = graph(old)
    old.name = ".Metadata baseline Arc Mask"
    candidate = build(append_ring(ring_path))
    ring_hash = digest(originals[ring_path])
    candidate["randy_ring_asset_sha256"] = ring_hash
    validate(candidate, ring_hash)
    assert graph(candidate) == expected, "Arc or nested radial calculation changed."
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.data.libraries.write(str(output), {candidate}, fake_user=True, compress=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    saved = append(output, NAME)
    validate(saved, ring_hash)
    assert graph(saved) == expected, "Save/reopen changed the native graph."
    assert len([group for group in bpy.data.node_groups if group.asset_data is not None]) == 1
    assert saved.nodes["Existing Ring Mask"].node_tree.name.startswith(".")
    assert saved.color_tag == "TEXTURE"
    for path, original in originals.items():
        assert path.read_bytes() == original, "Baseline or dependency changed: " + str(path)
    paths = ("tools/randy_node_assets/build_arc_mask.py", "tools/randy_node_assets/verify_arc_mask.py",
             "tools/randy_node_assets/verify_ring_mask.py", "tools/randy_node_assets/verify_arc_mask_metadata.py")
    report = {"passed": True, "state": "complete", "asset": str(output), "asset_sha256": digest(output.read_bytes()),
              "asset_metadata": {"name": NAME, "version": VERSION, "catalog_id": saved.asset_data.catalog_id,
                                 "inputs": [item.name for item in saved.interface.items_tree if item.item_type == "SOCKET" and item.in_out == "INPUT"],
                                 "outputs": ["Mask"], "color_tag": saved.color_tag, "dependency": saved.nodes["Existing Ring Mask"].node_tree.name},
              "ring_asset_sha256": ring_hash, "blender": bpy.app.version_string,
              "render_repeated": False, "ui_search_tested": False, "baseline_sha256": digest(originals[baseline]),
              "baseline_verification_sha256": digest(originals[evidence], text=True),
              "baseline_test_count": len(prior["tests"]), "baseline_shader_sample_count": prior["shader_sample_count"],
              "baseline_verification_hash_mode": "utf8-lf", "baseline_verification_source": str(evidence),
              "functional_graph_sha256": digest(json.dumps(expected, sort_keys=True).encode("utf-8")),
              "source_dependencies_sha256": {relative: source_hash(ROOT / relative) for relative in paths},
              "tests": [{"name": name, "passed": True} for name in (
                  "functional_arc_and_nested_radial_graph_identical_to_render_verified_baseline",
                  "single_texture_asset_and_hidden_native_radial_dependency",
                  "save_reopen_preserves_graph_version_and_metadata",
                  "baseline_asset_evidence_and_radial_fixture_unchanged")],
              "evidence_note": "Four metadata/persistence checks on the exact new asset. Its complete recursive functional graph equals the previously rendered baseline; numerical rendering was not repeated."}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("ARC_MASK_METADATA " + json.dumps({"passed": True, "version": VERSION, "asset": str(output),
          "checks": len(report["tests"]), "baseline_shader_samples": prior["shader_sample_count"], "render_repeated": False}))


if __name__ == "__main__":
    main()
