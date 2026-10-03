"""Verify the multifunction Ring Mask against its exact Arc 0.2.0 baseline.

Fresh native shader samples cover full-ring defaults, adjustable arcs, seams,
soft edges and Ring Data -> Extend Mask. A recursive graph equality check
establishes that only the public metadata and Sweep default changed. All work
uses a factory background session and leaves source assets unchanged.
"""

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[2]
NAME = "Ring Mask"
VERSION = "0.2.1"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
INPUT_NAMES = ("Inner Radius", "Ring Width", "Edge Softness", "Start Angle", "Sweep Angle")
DEFAULTS = (.6, .08, 0.0, 0.0, 360.0)
BASELINE = ROOT / "node_library/validation/fixtures/Randy_Arc_Mask_0_2_0.blend"
SOURCE_PATHS = (
    "tools/randy_node_assets/build_ring_mask_current.py",
    "tools/randy_node_assets/verify_ring_mask_current.py",
    "tools/randy_node_assets/verify_arc_mask.py",
    "tools/randy_node_assets/verify_extend_mask.py",
    "tools/randy_node_assets/verify_ring_mask.py",
    "tools/randy_node_assets/ring_boundary.py",
)


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARC = load("current_ring_arc_oracle", SOURCE_PATHS[2])
EXTEND = load("current_ring_extend_oracle", SOURCE_PATHS[3])


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def plain(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return list(value)


def graph(group, top=True):
    """Recursive native graph record; normalize only the renamed default.

    Datablock names are intentionally omitted because appending two equal
    native dependencies gives them Blender's ordinary .001 suffixes.
    """
    interface = []
    for item in group.interface.items_tree:
        if item.item_type != "SOCKET":
            continue
        default = getattr(item, "default_value", None)
        if top and item.in_out == "INPUT" and item.name == "Sweep Angle":
            default = "public-full-ring-default"
        interface.append((item.identifier, item.name, item.in_out, item.socket_type,
                          plain(default), getattr(item, "min_value", None),
                          getattr(item, "max_value", None), getattr(item, "hide_value", None)))
    nodes = []
    for node in group.nodes:
        record = {"name": node.name, "type": node.bl_idname, "label": node.label,
                  "location": list(node.location), "width": node.width, "mute": node.mute,
                  "inputs": [(s.identifier, s.name, plain(getattr(s, "default_value", None))) for s in node.inputs],
                  "outputs": [(s.identifier, s.name,
                               "public-full-ring-default" if top and node.bl_idname == "NodeGroupInput" and s.name == "Sweep Angle"
                               else plain(getattr(s, "default_value", None))) for s in node.outputs]}
        for attr in ("operation", "data_type", "interpolation_type", "clamp", "use_clamp",
                     "from_instancer", "is_active_output", "define_signature"):
            if hasattr(node, attr):
                record[attr] = getattr(node, attr)
        if hasattr(node, "bundle_items"):
            record["bundle_items"] = [{attr: getattr(item, attr) for attr in
                                      ("name", "identifier", "socket_type") if hasattr(item, attr)}
                                     for item in node.bundle_items]
        if node.bl_idname == "ShaderNodeGroup":
            assert node.node_tree is not None
            record["nested"] = graph(node.node_tree, top=False)
        nodes.append(record)
    return {"interface": interface, "nodes": sorted(nodes, key=lambda record: record["name"]),
            "links": sorted((l.from_node.name, l.from_socket.identifier,
                              l.to_node.name, l.to_socket.identifier) for l in group.links)}


def append(path, name):
    with bpy.data.libraries.load(str(path), link=False) as (available, loaded):
        assert name in available.node_groups, name
        loaded.node_groups = [name]
    return loaded.node_groups[0]


def validate(group):
    assert group.name == NAME and group.bl_idname == "ShaderNodeTree" and group.library is None
    assert group.get("randy_asset_version") == VERSION
    assert group.get("randy_asset_source") == SOURCE_PATHS[0]
    assert group.asset_data and group.asset_data.author == "Randy"
    assert group.asset_data.catalog_id == CATALOG_ID and group.color_tag == "TEXTURE"
    items = [s for s in group.interface.items_tree if s.item_type == "SOCKET"]
    inputs = [s for s in items if s.in_out == "INPUT"]
    outputs = [s for s in items if s.in_out == "OUTPUT"]
    assert [(s.name, s.socket_type) for s in inputs] == [(n, "NodeSocketFloat") for n in INPUT_NAMES]
    assert [(s.name, s.socket_type) for s in outputs] == [("Mask", "NodeSocketFloat"), ("Ring Data", "NodeSocketBundle")]
    assert all(abs(s.default_value - value) < 1e-6 for s, value in zip(inputs, DEFAULTS))
    assert outputs[0].identifier == "Socket_0"
    nested = [n.node_tree for n in group.nodes if n.bl_idname == "ShaderNodeGroup"]
    assert len(nested) == 1 and nested[0].asset_data is None
    assert nested[0].name.startswith(".Arc Mask Radial")
    assert group.get("randy_boundary_contract") == "ring-boundary-v1: Position, Bounds and Arc vectors in a native Bundle"
    return {"name": NAME, "version": VERSION, "catalog_id": CATALOG_ID, "color_tag": group.color_tag,
            "inputs": ARC.interface_record(group, "INPUT"), "outputs": ARC.interface_record(group, "OUTPUT"),
            "nodes": len(group.nodes), "dependency": nested[0].name}


def compare_atlas(scene, cases, report_path, engine, label):
    scene.render.engine = engine
    if engine == "CYCLES":
        scene.cycles.samples = 1
    elif hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = 8
    exr = report_path.with_name("Ring_Mask_current_" + label + "_atlas.exr")
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    scene.render.filepath = str(exr)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(exr), check_existing=False)
    width, height = image.size
    assert width == height == ARC.GRID_SIZE * ARC.PIXELS_PER_TILE
    pixels, results = list(image.pixels), []
    for i, case in enumerate(cases):
        x = i % ARC.GRID_SIZE * ARC.PIXELS_PER_TILE + ARC.PIXELS_PER_TILE // 2
        y = i // ARC.GRID_SIZE * ARC.PIXELS_PER_TILE + ARC.PIXELS_PER_TILE // 2
        rgb = pixels[(y * width + x) * 4:(y * width + x) * 4 + 3]
        expected = case.get("expected_rgb", [case["expected"]] * 3)
        error = max(abs(a - b) for a, b in zip(rgb, expected))
        passed = error <= EXTEND.TOLERANCE and all(math.isfinite(v) and -EXTEND.TOLERANCE <= v <= 1 + EXTEND.TOLERANCE for v in rgb)
        results.append({**case, "name": label + ": " + case["name"], "rgb": rgb,
                        "pixel": [x, y], "max_error": error, "passed": passed})
    assert all(r["passed"] for r in results), json.dumps([r for r in results if not r["passed"]])
    return results, str(exr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=BASELINE if BASELINE.is_file() else ROOT / "node_library/assets/Randy_Arc_Mask.blend")
    parser.add_argument("--extend-asset", type=Path, default=ROOT / "node_library/assets/Randy_Extend_Mask.blend")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--eevee", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Verification requires an isolated factory background process.")
    asset, source, extend_path, report_path = (p.resolve() for p in
                                            (args.asset, args.source, args.extend_asset, args.report))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = {p: p.read_bytes() for p in (asset, source, extend_path)}
    report = {"passed": False, "state": "verification_started", "asset": str(asset),
              "asset_sha256": hashlib.sha256(original_bytes[asset]).hexdigest(),
              "source_arc_asset": str(source), "source_arc_sha256": hashlib.sha256(original_bytes[source]).hexdigest(),
              "extend_asset_sha256": hashlib.sha256(original_bytes[extend_path]).hexdigest(),
              "blender": bpy.app.version_string, "ui_search_tested": False, "tests": [],
              "source_dependencies_sha256": {p: source_hash(ROOT / p) for p in SOURCE_PATHS}}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        group = append(asset, NAME)
        report["asset_metadata"] = validate(group)
        prior = append(source, "Arc Mask")
        assert prior.get("randy_asset_version") == "0.2.0"
        assert group.get("randy_arc_020_baseline_sha256") == report["source_arc_sha256"]
        assert graph(group) == graph(prior), "Native Mask/Ring Data graph changed beyond name and Sweep default."
        prior_ids = [(s.identifier, s.name, s.in_out) for s in prior.interface.items_tree if s.item_type == "SOCKET"]
        current_ids = [(s.identifier, s.name, s.in_out) for s in group.interface.items_tree if s.item_type == "SOCKET"]
        assert prior_ids == current_ids
        report["compatibility_baseline"] = {"path": BASELINE.relative_to(ROOT).as_posix(),
                                           "sha256": report["source_arc_sha256"]}
        report["recursive_graph_matches_arc_0_2_0"] = True
        report["tests"].append({"name": "recursive_native_graph_and_all_socket_identifiers_equal_arc_020", "passed": True})
        report["tests"].append({"name": "current_texture_asset_and_full_ring_default_interface", "passed": True})
        cases = ARC.cases()
        for index, case in enumerate(cases):
            ARC.add_tile(group, case, index)
        for angle in (0, 90, 180, 270):
            p = EXTEND.point(.65, angle)
            case = {"name": "new_instance_default_full_ring_" + str(angle),
                    "uv": (.5 + p[0] / 2, .5 + p[1] / 2), "values": DEFAULTS,
                    "linked_parameters": False, "expected": 1.0}
            # ARC.add_tile assigns explicit controls, so make a material
            # with an untouched new group instance for this default test.
            ARC.add_tile(group, case, len(cases))
            tree = bpy.data.materials["Arc " + case["name"]].node_tree
            old = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeGroup")
            emission = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeEmission")
            tree.nodes.remove(old)
            fresh = tree.nodes.new("ShaderNodeGroup")
            fresh.node_tree = group
            assert all(abs(fresh.inputs[n].default_value - v) < 1e-6 for n, v in zip(INPUT_NAMES, DEFAULTS))
            tree.links.new(fresh.outputs["Mask"], emission.inputs["Color"])
            cases.append(case)
        extend = append(extend_path, "Extend Mask")
        EXTEND.validate(extend)
        for angle, radius, mode in ((270, .71, "Outer"), (270, .57, "Inner"),
                                    (330, .71, "Outer"), (90, .71, "Outer")):
            source_values = DEFAULTS if angle == 270 else (.6, .08, 0, 300, 120)
            p = EXTEND.point(radius, angle)
            values = (.04, .01, 0.0)
            expected = EXTEND.extend_oracle(p, EXTEND.source_descriptor(source_values), mode, values)[0]
            case = {"name": "ring_data_extend_" + mode.lower() + "_" + str(angle),
                    "uv": (.5 + p[0] / 2, .5 + p[1] / 2), "source": source_values,
                    "mode": mode, "values": values, "linked_parameters": False,
                    "use_defaults": False, "expected": expected}
            EXTEND.add_tile(extend, group, case, len(cases))
            cases.append(case)
        assert len(cases) <= ARC.GRID_SIZE ** 2
        scene = bpy.context.scene
        ARC.RADIAL.setup_render(scene)
        fixture = report_path.with_name("Randy_Ring_Mask_current_validation.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(fixture), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(fixture), load_ui=False, use_scripts=False)
        group = bpy.data.node_groups[NAME]
        validate(group)
        report["tests"].append({"name": "native_save_reopen_without_addon", "passed": True})
        scene = bpy.context.scene
        samples, atlas = compare_atlas(scene, cases, report_path, "CYCLES", "Cycles")
        report["tests"].extend(samples)
        report["render_exr"] = atlas
        report["shader_sample_count"] = len(samples)
        report["cycles_shader_sample_count"] = len(samples)
        if args.eevee:
            samples, atlas = compare_atlas(scene, cases, report_path, "BLENDER_EEVEE", "EEVEE")
            report["tests"].extend(samples)
            report["eevee_render_exr"] = atlas
            report["eevee_shader_sample_count"] = len(samples)
            report["shader_sample_count"] += len(samples)
        report["examples"] = ARC.preview(group, report_path)
        report["fixture"] = str(fixture)
        assert all(path.read_bytes() == contents for path, contents in original_bytes.items())
        report["tests"].append({"name": "all_source_assets_unchanged", "passed": True})
        report.update(passed=True, state="complete")
    except Exception as exc:
        report.update(state="failed", error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("CURRENT_RING_MASK_VERIFICATION " + json.dumps({"passed": report["passed"],
              "tests": len(report["tests"]), "report": str(report_path)}))


if __name__ == "__main__":
    main()
