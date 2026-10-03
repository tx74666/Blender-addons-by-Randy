"""Render and verify the saved Arc Mask asset in an isolated factory session.

The independent oracle multiplies the original Ring Mask scalar definition by
a degree-based counterclockwise interval. Constant UV tiles test real native
shader output, including zero/full sweep, seams, clamps and radial softness.
Neither the asset nor the original Ring Mask source file is changed.
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
NAME = "Arc Mask"
RING_ASSET = ROOT / "node_library/dependencies/Randy_Ring_Mask.blend"
INPUT_NAMES = ("Inner Radius", "Ring Width", "Edge Softness", "Start Angle", "Sweep Angle")
DEFAULTS = (0.6, 0.08, 0.0, 0.0, 180.0)
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
GRID_SIZE = 8
PIXELS_PER_TILE = 16
TOLERANCE = .0002


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_ring_verifier():
    path = ROOT / "tools/randy_node_assets/verify_ring_mask.py"
    spec = importlib.util.spec_from_file_location("arc_radial_oracle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RADIAL = load_ring_verifier()


def oracle(uv, values):
    inner, width, soft, start, sweep = values
    sweep = min(360.0, max(0.0, sweep))
    if sweep == 0:
        return 0.0
    radial = RADIAL.oracle(uv, (inner, width, soft))
    if sweep == 360:
        return radial
    angle = math.degrees(math.atan2(uv[1] - .5, uv[0] - .5))
    return radial if (angle - start) % 360.0 <= sweep else 0.0


def cases():
    result = []

    def add(name, angle=0.0, radius=.65, values=DEFAULTS, *, uv=None, linked=False, second=None):
        radians = math.radians(angle)
        coordinates = uv if uv is not None else (.5 + radius * math.cos(radians) / 2,
                                               .5 + radius * math.sin(radians) / 2)
        case = {"name": name, "uv": coordinates, "values": values,
                "linked_parameters": linked, "expected": oracle(coordinates, values)}
        if second is not None:
            case["second_values"] = second
            case["expected_rgb"] = [case["expected"], oracle(coordinates, second), 0.0]
        result.append(case)

    for angle, name in ((0, "right_start_inclusive"), (45, "upper_right"),
                        (90, "top"), (135, "upper_left"), (180, "left_end_inclusive"),
                        (225, "lower_left_empty"), (270, "bottom_empty"),
                        (315, "lower_right_empty")):
        add("default_" + name, angle)
    for angle in (0, 90, 180, 270):
        add("zero_sweep_empty_" + str(angle), angle, values=(.6, .08, 0, 0, 0))
        add("full_sweep_white_" + str(angle), angle, values=(.6, .08, 0, 247, 360))
    for angle, name in ((330, "before_seam"), (0, "at_seam"), (30, "after_seam"),
                        (90, "beyond_end"), (270, "before_start")):
        add("wrapped_arc_" + name, angle, values=(.6, .08, 0, 300, 120))
    add("negative_start_wraps", 315, values=(.6, .08, 0, -90, 180))
    add("negative_start_exterior", 180, values=(.6, .08, 0, -90, 180))
    add("linked_start_multi_turn_wraps", 135, values=(.6, .08, 0, 1170, 90), linked=True)
    add("linked_start_multi_turn_exterior", 315, values=(.6, .08, 0, 1170, 90), linked=True)
    add("negative_sweep_clamped_empty", 90, values=(.6, .08, 0, 0, -120), linked=True)
    add("large_sweep_clamped_full", 270, values=(.6, .08, 0, 0, 720), linked=True)
    add("zero_width_empty", 90, values=(.6, 0, 0, 0, 180))
    add("inside_radial_hole", 90, radius=.5)
    add("beyond_radial_outer", 90, radius=.8)
    add("outside_normalized_disk", 45, radius=1.01, values=(.75, .5, 0, 0, 360))
    add("disk_center_has_no_radial_hole", radius=0, values=(0, .5, .125, 0, 180))
    add("soft_radial_half", 90, radius=.3125, values=(.25, .5, .125, 0, 180))
    add("soft_radial_outer_half", 90, radius=.6875, values=(.25, .5, .125, 0, 180))
    add("soft_radial_same_point_outside_arc", 270, radius=.3125, values=(.25, .5, .125, 0, 180))
    add("soft_radial_full_matches_ring", 270, radius=.6875, values=(.25, .5, .125, 40, 360))
    add("independent_arc_a_red", 90, second=(.6, .08, 0, 180, 180))
    add("independent_arc_b_green", 270, second=(.6, .08, 0, 180, 180))
    assert len(result) <= GRID_SIZE ** 2
    return result


def value(socket):
    raw = getattr(socket, "default_value", None)
    if raw is None or isinstance(raw, (str, bool, int, float)):
        return raw
    return list(raw)


def radial_graph(group):
    """Capture every computation field used by the existing radial asset."""
    result = {"nodes": [], "links": [], "interface": []}
    for item in group.interface.items_tree:
        if item.item_type == "SOCKET":
            result["interface"].append([item.name, item.in_out, item.socket_type,
                                        getattr(item, "default_value", None), item.min_value, item.max_value])
    for node in group.nodes:
        record = {"name": node.name, "type": node.bl_idname, "label": node.label,
                  "location": list(node.location), "width": node.width,
                  "inputs": [(socket.name, value(socket)) for socket in node.inputs],
                  "outputs": [(socket.name, value(socket)) for socket in node.outputs]}
        for attribute in ("operation", "data_type", "interpolation_type", "clamp", "use_clamp"):
            if hasattr(node, attribute):
                record[attribute] = getattr(node, attribute)
        result["nodes"].append(record)
    for link in group.links:
        result["links"].append([link.from_node.name, list(link.from_node.outputs).index(link.from_socket),
                                link.to_node.name, list(link.to_node.inputs).index(link.to_socket)])
    result["nodes"].sort(key=lambda item: item["name"])
    result["links"].sort()
    return result


def validate(group, ring_hash):
    assert group.bl_idname == "ShaderNodeTree"
    assert group.asset_data is not None and group.asset_data.author == "Randy"
    assert group.asset_data.catalog_id == CATALOG_ID
    assert group.color_tag == "TEXTURE"
    assert group.get("randy_asset_version") == "0.2.0"
    assert group.get("randy_ring_asset_sha256") == ring_hash
    sockets = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    inputs = [item for item in sockets if item.in_out == "INPUT"]
    outputs = [item for item in sockets if item.in_out == "OUTPUT"]
    assert [item.name for item in inputs] == list(INPUT_NAMES)
    assert [(item.name, item.socket_type) for item in outputs] == [
        ("Mask", "NodeSocketFloat"), ("Ring Data", "NodeSocketBundle")]
    assert all(item.socket_type == "NodeSocketFloat" for item in inputs)
    assert outputs[0].identifier == "Socket_0"
    assert all(abs(item.default_value - expected) < 1e-6 for item, expected in zip(inputs, DEFAULTS))
    assert inputs[3].min_value == -360 and inputs[3].max_value == 360
    assert inputs[4].min_value == 0 and inputs[4].max_value == 360
    nested = [node for node in group.nodes if node.bl_idname == "ShaderNodeGroup"]
    assert len(nested) == 1, "Arc must reference the one original radial dependency."
    assert nested[0].node_tree is not None
    assert nested[0].node_tree.asset_data is None, "Do not add a duplicate Ring Mask asset menu entry."
    assert nested[0].node_tree.name.startswith(".Arc Mask Radial"), "Hide the internal radial group from the Add > Group menu."
    assert not any(node.bl_idname in {"ShaderNodeTexImage", "ShaderNodeScript", "ShaderNodeAttribute", "ShaderNodeNewGeometry"}
                   for node in group.nodes)
    assert not any(item.socket_type in {"NodeSocketShader", "NodeSocketGeometry", "NodeSocketMaterial", "NodeSocketVector"}
                   for item in sockets)
    coordinates = [node for node in group.nodes if node.bl_idname == "ShaderNodeTexCoord"]
    assert len(coordinates) == 1
    assert all(link.from_socket.name == "UV" for link in group.links if link.from_node == coordinates[0])
    return nested[0].node_tree


def instance(tree, group, values, linked):
    result = tree.nodes.new("ShaderNodeGroup")
    result.node_tree = group
    for name, number in zip(INPUT_NAMES, values):
        if linked:
            external = tree.nodes.new("ShaderNodeValue")
            external.outputs[0].default_value = number
            tree.links.new(external.outputs[0], result.inputs[name])
        else:
            result.inputs[name].default_value = number
    return result


def mask_graph(group):
    """Capture only the original Mask dependencies, excluding appended data."""
    output = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput' and n.is_active_output)
    pending = [l.from_node for l in output.inputs['Mask'].links]
    required = set()
    while pending:
        n = pending.pop()
        if n.name in required:
            continue
        required.add(n.name)
        pending.extend(l.from_node for s in n.inputs for l in s.links)
    nodes = []
    for n in group.nodes:
        if n.name not in required:
            continue
        nodes.append((n.name, n.bl_idname, bool(n.mute),
                      getattr(n, 'operation', None), getattr(n, 'data_type', None),
                      getattr(n, 'interpolation_type', None), getattr(n, 'clamp', None),
                      getattr(n, 'use_clamp', None), getattr(n, 'from_instancer', None),
                      [(s.name, value(s)) for s in n.inputs]))
    links = [(l.from_node.name, l.from_socket.identifier,
              l.to_node.name, l.to_socket.identifier) for l in group.links
             if l.from_node.name in required and l.to_node.name in required]
    mask_source = [(l.from_node.name, l.from_socket.identifier) for l in output.inputs['Mask'].links]
    return sorted(nodes), sorted(links), mask_source


def interface_record(group, direction):
    items = []
    for s in group.interface.items_tree:
        if s.item_type == 'SOCKET' and s.in_out == direction:
            item = {'name': s.name, 'type': s.socket_type}
            if s.socket_type == 'NodeSocketFloat':
                item['default'] = s.default_value
            items.append(item)
    return items


def add_tile(group, case, index):
    x, y = index % GRID_SIZE, index // GRID_SIZE
    mesh = bpy.data.meshes.new("UV " + case["name"])
    mesh.from_pydata([(x - .46, y - .46, 0), (x + .46, y - .46, 0),
                     (x + .46, y + .46, 0), (x - .46, y + .46, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for item in uv.data:
        item.uv = case["uv"]
    obj = bpy.data.objects.new(case["name"], mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location.z = -index * .001
    mat = bpy.data.materials.new("Arc " + case["name"])
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    first = instance(tree, group, case["values"], case["linked_parameters"])
    mask = first.outputs["Mask"]
    if "second_values" in case:
        second = instance(tree, group, case["second_values"], False)
        assert first.node_tree == second.node_tree
        combine = tree.nodes.new("ShaderNodeCombineColor")
        combine.mode = "RGB"
        tree.links.new(mask, combine.inputs["Red"])
        tree.links.new(second.outputs["Mask"], combine.inputs["Green"])
        combine.inputs["Blue"].default_value = 0
        mask = combine.outputs[0]
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(mask, emission.inputs["Color"])
    tree.links.new(emission.outputs[0], output.inputs["Surface"])
    mesh.materials.append(mat)


def preview(group, report_path):
    scene = bpy.data.scenes.new("Arc Mask three examples")
    RADIAL.setup_render(scene)
    scene.camera.location = (0, 0, 10)
    scene.camera.data.ortho_scale = 3.3
    scene.render.resolution_x = 600
    scene.render.resolution_y = 200
    scene.cycles.samples = 4
    examples = [
        ("Full ring", [(0.6, .08, 0, 0, 360)]),
        ("Semicircle", [(0.6, .08, 0, 0, 180)]),
        ("Three shared-shader arcs", [(.32, .07, 0, 300, 120), (.54, .07, 0, 20, 220), (.76, .07, 0, 100, 220)]),
    ]
    for index, (name, parameters) in enumerate(examples):
        x = (index - 1) * 1.1
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(x - .5, -.5, 0), (x + .5, -.5, 0), (x + .5, .5, 0), (x - .5, .5, 0)], [], [(0, 1, 2, 3)])
        uv = mesh.uv_layers.new(name="UVMap")
        for loop, coordinate in zip(uv.data, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop.uv = coordinate
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()
        combined = None
        for values in parameters:
            arc = instance(tree, group, values, False)
            if combined is None:
                combined = arc.outputs["Mask"]
            else:
                union = tree.nodes.new("ShaderNodeMath")
                union.operation = "MAXIMUM"
                tree.links.new(combined, union.inputs[0])
                tree.links.new(arc.outputs["Mask"], union.inputs[1])
                combined = union.outputs[0]
        emission = tree.nodes.new("ShaderNodeEmission")
        emission.inputs["Strength"].default_value = 1
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(combined, emission.inputs["Color"])
        tree.links.new(emission.outputs[0], output.inputs["Surface"])
        mesh.materials.append(mat)
    image = report_path.with_name("Arc_Mask_examples.png")
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = str(image)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    return {"image": str(image), "left_to_right": [{"name": name, "instances": params} for name, params in examples]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--ring-asset", default=str(RING_ASSET))
    parser.add_argument("--report", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Verify only in an isolated background Blender process.")
    asset = Path(args.asset).resolve()
    ring_path = Path(args.ring_asset).resolve()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    asset_hash = hashlib.sha256(asset.read_bytes()).hexdigest()
    ring_hash = hashlib.sha256(ring_path.read_bytes()).hexdigest()
    report = {"passed": False, "state": "verification_started", "asset": str(asset),
              "asset_sha256": asset_hash, "ring_asset_sha256": ring_hash,
              "blender": bpy.app.version_string, "ui_search_tested": False,
              "render_engine": "Cycles CPU", "tolerance": TOLERANCE, "tests": [],
              "source_dependencies_sha256": {path: source_hash(ROOT / path) for path in (
                  "tools/randy_node_assets/build_arc_mask.py", "tools/randy_node_assets/verify_arc_mask.py",
                  "tools/randy_node_assets/verify_ring_mask.py", "tools/randy_node_assets/ring_boundary.py")}}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with bpy.data.libraries.load(str(asset), link=False) as (source, target):
            assert NAME in source.node_groups
            target.node_groups = [NAME]
        group = target.node_groups[0]
        nested_ring = validate(group, ring_hash)
        report["asset_metadata"] = {"name": group.name, "version": "0.2.0", "catalog_id": CATALOG_ID,
                                    "color_tag": group.color_tag,
                                    "inputs": interface_record(group, 'INPUT'),
                                    "outputs": interface_record(group, 'OUTPUT'),
                                    "nodes": len(group.nodes), "dependency": nested_ring.name}
        report["tests"].append({"name": "native_asset_interface_and_single_unadvertised_ring_dependency", "passed": True})
        baseline = ROOT / 'node_library/validation/fixtures/Randy_Arc_Mask_0_1_1.blend'
        baseline_hash = hashlib.sha256(baseline.read_bytes()).hexdigest()
        with bpy.data.libraries.load(str(baseline), link=False) as (source, target):
            target.node_groups = ['Arc Mask']
        prior = target.node_groups[0]
        assert prior.get('randy_asset_version') == '0.1.1'
        assert mask_graph(prior) == mask_graph(group)
        prior_ids = [(s.name, s.identifier, s.in_out) for s in prior.interface.items_tree if s.item_type == 'SOCKET']
        current_ids = [(s.name, s.identifier, s.in_out) for s in group.interface.items_tree if s.item_type == 'SOCKET' and s.name != 'Ring Data']
        assert prior_ids == current_ids
        report['baseline_asset_sha256'] = baseline_hash
        report['compatibility_baseline'] = {
            'path': baseline.relative_to(ROOT).as_posix(), 'sha256': baseline_hash}
        report['mask_channel_compatible_with_arc_0_1_1'] = True
        report['tests'].append({'name': 'old_mask_graph_and_original_socket_identifiers_unchanged', 'passed': True})
        with bpy.data.libraries.load(str(ring_path), link=False) as (source, target):
            target.node_groups = ["Ring Mask"]
        original = target.node_groups[0]
        assert radial_graph(nested_ring) == radial_graph(original), "The original radial graph must be exactly unchanged."
        report["tests"].append({"name": "nested_radial_graph_equals_original_ring_mask", "passed": True})
        all_cases = cases()
        for index, case in enumerate(all_cases):
            add_tile(group, case, index)
        instances = [node for mat in bpy.data.materials if mat.node_tree for node in mat.node_tree.nodes
                     if node.bl_idname == "ShaderNodeGroup" and node.node_tree == group]
        assert len(instances) >= len(all_cases)
        assert all(node.node_tree == group for node in instances)
        assert all(node.node_tree.nodes["Existing Ring Mask"].node_tree == nested_ring for node in instances)
        report["tests"].append({"name": "independent_parameters_and_one_shared_native_dependency", "passed": True})
        scene = bpy.context.scene
        RADIAL.setup_render(scene)
        fixture = report_path.with_name("Randy_Arc_Mask_validation.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(fixture), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(fixture), load_ui=False, use_scripts=False)
        group = bpy.data.node_groups[NAME]
        validate(group, ring_hash)
        report["tests"].append({"name": "native_save_reopen_without_addon", "passed": True})
        scene = bpy.context.scene
        exr = report_path.with_name("Arc_Mask_numeric_atlas.exr")
        scene.render.filepath = str(exr)
        bpy.ops.render.render(write_still=True)
        rendered = bpy.data.images.load(str(exr), check_existing=False)
        width, height = rendered.size
        pixels = list(rendered.pixels)
        assert width == height == GRID_SIZE * PIXELS_PER_TILE
        failures = []
        max_error = 0.0
        for index, case in enumerate(all_cases):
            x = index % GRID_SIZE * PIXELS_PER_TILE + PIXELS_PER_TILE // 2
            y = index // GRID_SIZE * PIXELS_PER_TILE + PIXELS_PER_TILE // 2
            offset = (y * width + x) * 4
            rgb = pixels[offset:offset + 3]
            expected = case.get("expected_rgb", [case["expected"]] * 3)
            error = max(abs(actual - required) for actual, required in zip(rgb, expected))
            passed = all(math.isfinite(channel) and -TOLERANCE <= channel <= 1 + TOLERANCE for channel in rgb) and error <= TOLERANCE
            max_error = max(max_error, error)
            result = {**case, "rgb": rgb, "max_error": error, "passed": passed, "pixel": [x, y]}
            report["tests"].append(result)
            if not passed:
                failures.append(result)
        report.update({"shader_sample_count": len(all_cases), "maximum_absolute_error": max_error,
                       "render_exr": str(exr), "fixture": str(fixture)})
        assert not failures, json.dumps(failures)
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == asset_hash
        assert hashlib.sha256(ring_path.read_bytes()).hexdigest() == ring_hash
        assert hashlib.sha256(baseline.read_bytes()).hexdigest() == baseline_hash
        report["tests"].append({"name": "both_source_assets_unchanged", "passed": True})
        report["examples"] = preview(group, report_path)
        report.update({"passed": True, "state": "complete"})
    except Exception as exc:
        report.update({"state": "failed", "error": str(exc), "traceback": traceback.format_exc()})
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("ARC_MASK_VERIFICATION " + json.dumps({"passed": report["passed"],
              "tests": len(report["tests"]), "report": str(report_path)}))


if __name__ == "__main__":
    main()
