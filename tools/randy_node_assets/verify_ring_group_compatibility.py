"""Validate the unchanged Ring Group with current Arc and editing sources.

The published Ring Group records the generator hash from its original build.
An exact archived generator must match that embedded hash and its six factory
functions and seven contract constants must match current source by AST. This
wrapper preserves the asset's original metadata and binary. It reuses the
existing numerical/native persistence verifier without changing its source.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = "addons/random_realm_builder_exporter/rr_ring_nodes.py"
ARCHIVE_GENERATOR = "random_realm_builder_exporter/rr_ring_nodes.py"
EDITING_PROOF = "node_library/validation/ring_group_editing_20261002.json"
WRAPPER = "tools/randy_node_assets/verify_ring_group_compatibility.py"
FACTORY_FUNCTIONS = ("_socket", "_mask_ids", "_graph_signature", "_new_mask", "_build_union", "new_group")
CONTRACT_CONSTANTS = ("_KIND", "_MASKS", "_SHADER", "_MASK_OUTPUT", "_SHADER_OUTPUT", "_SIGNATURE", "_ORDER")


def digest(contents, *, text=False):
    if text:
        contents = contents.replace(b"\r\n", b"\n")
    return hashlib.sha256(contents).hexdigest()


def factory_parts(contents):
    """Capture precisely the factory definitions used in the earlier proof."""
    tree = ast.parse(contents.decode("utf-8-sig"))
    functions, constants = {}, {}
    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in FACTORY_FUNCTIONS:
            if item.name in functions:
                raise ValueError("Duplicate factory function: " + item.name)
            functions[item.name] = ast.dump(item, include_attributes=False)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id in CONTRACT_CONSTANTS:
                    if target.id in constants:
                        raise ValueError("Duplicate factory constant: " + target.id)
                    constants[target.id] = ast.dump(item, include_attributes=False)
    if set(functions) != set(FACTORY_FUNCTIONS) or set(constants) != set(CONTRACT_CONSTANTS):
        raise ValueError("Generator is missing a required factory function or contract constant")
    return {"functions": functions, "constants": constants}


def original_factory_equivalence(embedded_hash, *, root=ROOT):
    """Find exact birth source, then prove the currently used factory unchanged."""
    root = Path(root).resolve()
    current = (root / GENERATOR).read_bytes()
    current_parts = factory_parts(current)
    proof_path = root / EDITING_PROOF
    recorded_proof = json.loads(proof_path.read_text(encoding="utf-8-sig"))
    if (recorded_proof.get("passed") is not True
            or recorded_proof.get("factory_functions_ast_equivalent") != list(FACTORY_FUNCTIONS)
            or recorded_proof.get("factory_contract_constants_equivalent") != list(CONTRACT_CONSTANTS)):
        raise ValueError("The previously recorded factory comparison has a different scope")
    archives = sorted((root / "dist").glob("rr_helper-*.zip"))
    recorded_archive = root / recorded_proof["baseline_archive"]
    if recorded_archive in archives:
        archives.remove(recorded_archive)
        archives.insert(0, recorded_archive)
    for archive in archives:
        archive_bytes = archive.read_bytes()
        try:
            with zipfile.ZipFile(archive) as package:
                names = [name for name in package.namelist() if name == ARCHIVE_GENERATOR]
                if len(names) != 1:
                    continue
                original = package.read(names[0])
        except zipfile.BadZipFile:
            continue
        if digest(original, text=True) != embedded_hash:
            continue
        original_parts = factory_parts(original)
        if original_parts != current_parts:
            changed = [name for category in ("functions", "constants")
                       for name in original_parts[category]
                       if original_parts[category][name] != current_parts[category][name]]
            raise ValueError("Current Ring Group factory differs from its exact original source: " + ", ".join(changed))
        if archive.read_bytes() != archive_bytes or (root / GENERATOR).read_bytes() != current:
            raise ValueError("Archive or current source changed during factory comparison")
        return {
            "passed": True,
            "original_embedded_generator_sha256": embedded_hash,
            "current_generator_sha256": digest(current, text=True),
            "matching_archive": archive.relative_to(root).as_posix(),
            "matching_archive_sha256": digest(archive_bytes),
            "archive_generator_path": ARCHIVE_GENERATOR,
            "archived_generator_sha256": digest(original, text=True),
            "factory_functions_ast_equivalent": list(FACTORY_FUNCTIONS),
            "factory_contract_constants_equivalent": list(CONTRACT_CONSTANTS),
            "factory_ast_sha256": digest(json.dumps(current_parts, sort_keys=True).encode("utf-8")),
            "previous_editing_proof": EDITING_PROOF,
            "previous_editing_proof_sha256": digest(proof_path.read_bytes(), text=True),
            "metadata_properties_modified": False,
            "native_asset_rebuilt": False,
        }
    raise ValueError("No repository release archive contains the exact generator hash embedded in Ring Group: " + str(embedded_hash))


def compatibility_metadata(group, helper, baseline):
    """Retain every original structural check while proving the birth hash."""
    assert group.name == baseline.NAME and group.bl_idname == "ShaderNodeTree"
    assert group.library is None and group.color_tag == "SHADER"
    assert group.asset_data and group.asset_data.author == "Randy"
    assert group.asset_data.catalog_id == baseline.CATALOG_ID
    assert group.asset_data.description == baseline.DESCRIPTION
    assert group["randy_asset_version"] == baseline.VERSION
    assert group["randy_asset_generator"] == baseline.GENERATOR
    original_hash = group["randy_asset_generator_sha256"]
    birth_proof = original_factory_equivalence(original_hash)
    assert birth_proof["current_generator_sha256"] == baseline.hashes()[baseline.GENERATOR]
    masks = helper._validate_group(group)
    assert len(masks) == 2
    items = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    assert [item.name for item in items if item.in_out == "INPUT"] == ["Shader", "Mask 1", "Mask 2"]
    assert [item.name for item in items if item.in_out == "OUTPUT"] == ["Mask", "Shader"]
    assert {node.bl_idname for node in group.nodes} == {"NodeGroupInput", "NodeGroupOutput", "ShaderNodeMath"}
    assert all(node.operation == "MAXIMUM" and node.use_clamp
               for node in group.nodes if node.bl_idname == "ShaderNodeMath")
    expected = helper.new_group(mask_count=2)
    try:
        assert helper._graph_signature(group) == helper._graph_signature(expected)
    finally:
        baseline.bpy.data.node_groups.remove(expected)
    assert group["randy_asset_generator_sha256"] == original_hash
    return {
        "name": baseline.NAME, "version": baseline.VERSION, "catalog_id": baseline.CATALOG_ID,
        "color_tag": "SHADER", "inputs": ["Shader", "Mask 1", "Mask 2"],
        "outputs": ["Mask", "Shader"], "nodes": len(group.nodes),
        "mask_operation": "clamped Maximum", "shader_count": 1,
        "original_embedded_generator_sha256": original_hash,
        "birth_factory_equivalence": birth_proof,
    }


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--report", required=True)
    args, _remaining = parser.parse_known_args(sys.argv[sys.argv.index("--") + 1:])
    report_path = Path(args.report).resolve()
    if report_path.exists():
        raise FileExistsError("Use a fresh compatibility report destination: " + str(report_path))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import verify_ring_group as baseline
    # Actual current hashes remain in the numerical report. No temporary hash
    # rewrite or asset metadata mutation makes the original verifier pass.
    baseline.DEPENDENCIES = (*baseline.DEPENDENCIES, WRAPPER,
                             "tools/randy_node_assets/ring_boundary.py", EDITING_PROOF)
    baseline.metadata = lambda group, helper: compatibility_metadata(group, helper, baseline)
    try:
        baseline.main()
    finally:
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
            proof = report.get("asset_metadata", {}).get("birth_factory_equivalence")
            if proof is not None:
                report["birth_factory_equivalence"] = proof
                archive = ROOT / proof["matching_archive"]
                archive_intact = digest(archive.read_bytes()) == proof["matching_archive_sha256"]
                report["tests"].append({"name": "exact_archived_birth_generator_factory_ast_matches_current",
                                        "passed": proof["passed"] is True})
                report["tests"].append({"name": "birth_source_archive_preserved_during_fresh_native_validation",
                                        "passed": archive_intact})
                report["compatibility_verifier"] = WRAPPER
                report["asset_metadata_rewritten"] = False
                report["asset_binary_rebuilt"] = False
                if not archive_intact:
                    report.update(passed=False, state="failed", error="Birth source archive changed during validation")
                report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print("RING_GROUP_COMPATIBILITY " + json.dumps({"passed": report["passed"],
                      "tests": len(report["tests"]), "shader_samples": report.get("shader_sample_count"),
                      "original_generator": proof["original_embedded_generator_sha256"],
                      "archive": proof["matching_archive"], "report": str(report_path)}))
                if not archive_intact:
                    raise RuntimeError("Birth source archive changed during validation")


if __name__ == "__main__":
    main()
