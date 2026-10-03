"""Render and verify native Extend Mask in a fresh background Blender process.

The scalar oracle below independently measures the boundary of an annular
sector. It never imports the production builder or boundary constructors.
Constant-UV tiles isolate shader output from spatial filtering. All fixtures,
EXR atlases, the fresh PNG preview and the JSON report stay beside --report;
the supplied assets and the user's working scene are never modified.
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
NAME = "Extend Mask"
VERSION = "0.1.0"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
MODES = ("Inner", "Outer", "Both", "Outline")
CONTROLS = ("Width", "Gap", "Softness")
DEFAULTS = (.01, 0.0, 0.0)
ARC_CONTROLS = ("Inner Radius", "Ring Width", "Edge Softness", "Start Angle", "Sweep Angle")
GRID_SIZE = 8
PIXELS_PER_TILE = 16
TOLERANCE = .0003
SOURCE_PATHS = (
    "tools/randy_node_assets/build_extend_mask.py",
    "tools/randy_node_assets/verify_extend_mask.py",
    "tools/randy_node_assets/build_arc_mask.py",
    "tools/randy_node_assets/ring_boundary.py",
    "tools/randy_node_assets/verify_ring_mask.py",
)

spec = importlib.util.spec_from_file_location("extend_radial_render_fixture", ROOT / SOURCE_PATHS[-1])
RADIAL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RADIAL)


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def clamp(value, low, high):
    return min(high, max(low, value))


def point(radius, angle=0.0):
    radians = math.radians(angle)
    return radius * math.cos(radians), radius * math.sin(radians)


def source_descriptor(values):
    """Hard visible boundaries; the source's fade does not move its contour."""
    inner, width, _soft, start, sweep = values
    inner = clamp(inner, 0.0, 1.0)
    outer = min(1.0, inner + max(0.0, width))
    sweep = clamp(sweep, 0.0, 360.0)
    return (inner, outer, start % 360.0, sweep, outer > inner and sweep > 0)


def angular_inside(p, start, sweep):
    return (math.degrees(math.atan2(p[1], p[0])) - start) % 360.0 <= sweep


def sector_distance(p, descriptor):
    """Exact Euclidean signed distance to finite arc and radial boundaries."""
    inner, outer, start, sweep, active = descriptor
    if not active:
        return None
    radius = math.hypot(*p)
    inside_angle = angular_inside(p, start, sweep)
    start_unit, end_unit = point(1.0, start), point(1.0, start + sweep)

    def distance(q):
        return math.hypot(p[0] - q[0], p[1] - q[1])

    def circle_arc(boundary):
        if inside_angle:
            return abs(radius - boundary)
        return min(distance((boundary * u[0], boundary * u[1]))
                   for u in (start_unit, end_unit))

    candidates = [circle_arc(outer)]
    if inner > 0:
        candidates.append(circle_arc(inner))
    if sweep < 360.0:
        for u in (start_unit, end_unit):
            projection = clamp(p[0] * u[0] + p[1] * u[1], inner, outer)
            candidates.append(distance((projection * u[0], projection * u[1])))
    result = min(candidates)
    return -result if inside_angle and inner <= radius <= outer else result


def smooth_profile(distance, width, softness):
    softness = min(max(0.0, softness), width / 2.0)
    if softness <= 0:
        return float(distance >= 0)
    t = clamp(distance / max(softness, 1e-8), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def radial_profile(p, descriptor, softness):
    inner, outer, start, sweep, active = descriptor
    radius = math.hypot(*p)
    if not active or radius > 1 or not angular_inside(p, start, sweep):
        return 0.0
    distance = outer - radius
    if inner > 0:
        distance = min(distance, radius - inner)
    return smooth_profile(distance, outer - inner, softness)


def extend_oracle(p, source, mode, values):
    """Return new mask plus the two independently reusable radial bands."""
    inner, outer, start, sweep, active = source
    width, gap, softness = values
    width, gap = max(0.0, width), max(0.0, gap)
    enabled = active and width > 0
    ilow, ihigh = clamp(inner - gap - width, 0, 1), clamp(inner - gap, 0, 1)
    olow, ohigh = clamp(outer + gap, 0, 1), clamp(outer + gap + width, 0, 1)
    inner_data = (ilow, ihigh, start, sweep,
                  enabled and ihigh > ilow and mode in ("Inner", "Both"))
    outer_data = (olow, ohigh, start, sweep,
                  enabled and ohigh > olow and mode in ("Outer", "Both"))
    if not enabled or math.hypot(*p) > 1:
        return 0.0, inner_data, outer_data
    if mode == "Outline":
        distance = sector_distance(p, source)
        nearest = min(distance - gap, gap + width - distance)
        return smooth_profile(nearest, width, softness), inner_data, outer_data
    mask = max(radial_profile(p, inner_data, softness), radial_profile(p, outer_data, softness))
    return mask, inner_data, outer_data


def oracle(case):
    if case.get("source") is None:
        descriptor = (0, 0, 0, 0, False)
    else:
        descriptor = source_descriptor(case["source"])
    result, inner, outer = extend_oracle(case["point"], descriptor, case["mode"], case["values"])
    for chain in case.get("chain", []):
        descriptor = inner if chain["branch"] == "Inner Data" else outer
        result, inner, outer = extend_oracle(case["point"], descriptor, chain["mode"], chain["values"])
    if case.get("both_branches"):
        # Both output descriptors are evaluated separately and only the
        # resulting masks are merged. No approximate SDF union is reused.
        result = max(extend_oracle(case["point"], data, "Both", (.01, .005, 0))[0]
                     for data in (inner, outer))
    return result


def cases():
    result = []
    full = (.4, .1, 0, 0, 360)
    standard = (.04, .02, 0)

    def add(name, radius=None, angle=0, *, p=None, source=full, mode="Both", values=standard,
            linked=False, defaults=False, chain=None, branches=False):
        p = tuple(p if p is not None else point(radius, angle))
        case = {"name": name, "point": p, "uv": (.5 + p[0] / 2, .5 + p[1] / 2),
                "source": source, "mode": mode, "values": values,
                "linked_parameters": linked, "use_defaults": defaults}
        if chain:
            case["chain"] = chain
        if branches:
            case["both_branches"] = True
        case["expected"] = oracle(case)
        result.append(case)

    add("default_both_inner", .395, values=DEFAULTS, defaults=True)
    add("default_both_outer", .505, values=DEFAULTS, defaults=True)
    add("original_source_excluded", .45)
    add("inner_band_white", .36, mode="Inner")
    add("inner_does_not_add_outer", .54, mode="Inner")
    add("outer_band_white", .54, mode="Outer")
    add("outer_does_not_add_inner", .36, mode="Outer")
    add("both_original_hole_empty", .2)
    add("both_beyond_new_outer_empty", .58)
    add("inner_gap_empty", .39)
    add("outer_gap_empty", .51)
    add("zero_extension_width", .36, values=(0, .02, 0))
    add("negative_linked_extension_width", .36, values=(-.1, .02, 0), linked=True)
    add("negative_linked_gap_clamps_zero", .38, values=(.04, -.1, 0), linked=True)
    add("negative_linked_softness_is_hard", .36, values=(.04, .02, -.1), linked=True)
    add("unconnected_source_black", .36, source=None)
    add("zero_source_width_black", .36, source=(.4, 0, 0, 0, 360))
    add("zero_source_sweep_black", .36, source=(.4, .1, 0, 0, 0))
    add("fully_cropped_source_black", .95, source=(1, .1, 0, 0, 360))
    add("disk_has_no_inner_extension", .02, source=(0, .5, 0, 0, 360), mode="Inner")
    add("disk_outer_extension_white", .54, source=(0, .5, 0, 0, 360), mode="Outer")
    add("clipped_inner_disk_center_no_soft_pinhole", 0, source=(.02, .04, 0, 0, 360),
        mode="Inner", values=(.04, 0, .01))
    add("outer_clipped_to_disk_white", .995, source=(.94, .04, 0, 0, 360),
        mode="Outer", values=(.08, 0, .005))
    add("softness_uses_clipped_band_width", .985, source=(.94, .04, 0, 0, 360),
        mode="Outer", values=(.08, 0, .1))
    add("result_outside_disk_black", 1.01, source=(.94, .04, 0, 0, 360), values=(.08, 0, 0))
    add("large_gap_collapsed_inner_no_phantom_center", 0, source=(.2, .1, 0, 0, 360),
        mode="Inner", values=(.1, .3, 0))
    add("large_gap_both_surviving_outer_white", .65, source=(.2, .1, 0, 0, 360), values=(.1, .3, 0))
    add("large_gap_outline_no_phantom_center", 0, source=(.2, .1, 0, 0, 360),
        mode="Outline", values=(.1, .3, 0))
    add("inner_soft_half_fade", .35, mode="Inner", values=(.04, .02, .02))
    add("outer_soft_quarter_fade", .525, mode="Outer", values=(.04, .02, .02))
    add("large_softness_caps_to_half_width", .36, mode="Inner", values=(.04, .02, .2))
    add("center_disk_soft_plateau", .005, source=(.02, .04, 0, 0, 360), mode="Inner", values=(.04, 0, .01))

    arc = (.4, .1, 0, 30, 120)
    add("arc_outer_inherits_angle_white", .54, 60, source=arc, mode="Outer")
    add("arc_outer_outside_angle_black", .54, 200, source=arc, mode="Outer")
    add("arc_inner_inherits_angle_white", .36, 60, source=arc, mode="Inner")
    add("arc_inner_does_not_wrap_ends", .36, 20, source=arc, mode="Inner")
    add("wrapped_arc_inside_white", .54, 20, source=(.4, .1, 0, 300, 100))
    add("wrapped_arc_outside_black", .54, 80, source=(.4, .1, 0, 300, 100))
    add("linked_multi_turn_start_wraps", .54, 60, source=(.4, .1, 0, 1110, 120), linked=True)
    add("linked_oversized_sweep_full_circle", .54, 220, source=(.4, .1, 0, 30, 720), linked=True)
    add("linked_negative_sweep_empty", .54, 60, source=(.4, .1, 0, 30, -100), linked=True)
    add("radial_softness_does_not_extend_arc_ends", .54, 20, source=arc, values=(.04, .02, .02))

    quarter = (.4, .1, 0, 0, 90)
    add("outline_outer_circular_band", .54, 45, source=quarter, mode="Outline")
    add("outline_inner_circular_band", .36, 45, source=quarter, mode="Outline")
    add("outline_start_cut_extends_outward", p=(.45, -.04), source=quarter, mode="Outline")
    add("outline_beyond_start_band_empty", p=(.45, -.07), source=quarter, mode="Outline")
    add("radial_both_leaves_start_cut_empty", p=(.45, -.04), source=quarter)
    add("outline_round_outer_corner", p=(.525, -.025), source=quarter, mode="Outline")
    add("outline_beyond_round_corner_empty", p=(.555, -.055), source=quarter, mode="Outline")
    add("outline_original_sector_interior_empty", p=(.45, .05), source=quarter, mode="Outline")
    add("outline_end_cut_extends_outward", p=(-.04, .45), source=quarter, mode="Outline")
    add("outline_reflex_arc_cut", p=(.45, -.04), source=(.4, .1, 0, 0, 270), mode="Outline")
    add("outline_full_circle_has_no_fake_cut", .54, 45, mode="Outline")
    add("outline_soft_cut_half_fade", p=(.45, -.025), source=quarter, mode="Outline", values=(.04, .02, .01))
    second = lambda branch: [{"branch": branch, "mode": "Both", "values": (.01, .005, 0)}]
    add("outline_inner_data_cannot_feed_radial_chain", .33, mode="Outline", chain=second("Inner Data"))
    add("chain_inner_branch_new_inner", .33, chain=second("Inner Data"))
    add("chain_inner_branch_new_outer", .39, chain=second("Inner Data"))
    add("chain_outer_branch_new_inner", .51, chain=second("Outer Data"))
    add("chain_outer_branch_new_outer", .57, chain=second("Outer Data"))
    add("both_branch_chain_inner_component", .33, branches=True)
    add("both_branch_chain_outer_component", .57, branches=True)
    add("unselected_inner_data_inactive", .33, mode="Outer", chain=second("Inner Data"))
    add("unselected_outer_data_inactive", .57, mode="Inner", chain=second("Outer Data"))
    add("collapsed_inner_data_remains_empty_on_chain", 0, source=(.2, .1, 0, 0, 360),
        mode="Inner", values=(.1, .3, 0), chain=second("Inner Data"))
    # Last angular-chain case replaces a redundant plateau sample to fit 8x8.
    result.pop(31)
    add("arc_chain_retains_angular_limits", .57, 200, source=arc, chain=second("Outer Data"))
    assert len(result) == GRID_SIZE ** 2, len(result)
    return result


def mode_items(group):
    switches = [node for node in group.nodes if node.bl_idname == "GeometryNodeMenuSwitch"]
    assert len(switches) == 1, "One compact native mode menu is required."
    items = switches[0].enum_definition.enum_items
    assert [item.name for item in items] == list(MODES)
    # Blender 5.2 exposes the socket's value as a menu-item NAME. NodeEnumItem
    # does not expose an identifier through this RNA API.
    return tuple(item.name for item in items)


def interface_record(group, direction):
    choices = mode_items(group)
    records = []
    for socket in group.interface.items_tree:
        if socket.item_type != "SOCKET" or socket.in_out != direction:
            continue
        record = {"name": socket.name, "type": socket.socket_type}
        if socket.socket_type == "NodeSocketFloat":
            record["default"] = socket.default_value
        elif socket.socket_type == "NodeSocketMenu":
            default = socket.default_value
            assert default in choices, default
            record.update({"default": default, "choices": list(choices)})
        records.append(record)
    return records


def validate(group):
    assert group.bl_idname == "ShaderNodeTree" and group.library is None
    assert group.asset_data is not None and group.asset_data.author == "Randy"
    assert group.asset_data.catalog_id == CATALOG_ID
    assert group.get("randy_asset_version") == VERSION
    assert group.color_tag == "TEXTURE"
    assert group.get("randy_boundary_contract") == "ring-boundary-v1: Position, Bounds and Arc vectors in a native Bundle"
    sockets = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    inputs = [(item.name, item.socket_type) for item in sockets if item.in_out == "INPUT"]
    outputs = [(item.name, item.socket_type) for item in sockets if item.in_out == "OUTPUT"]
    assert inputs == [("Source", "NodeSocketBundle"), ("Mode", "NodeSocketMenu"),
                      ("Width", "NodeSocketFloat"), ("Gap", "NodeSocketFloat"), ("Softness", "NodeSocketFloat")]
    assert outputs == [("Mask", "NodeSocketFloat"), ("Inner Data", "NodeSocketBundle"), ("Outer Data", "NodeSocketBundle")]
    for name, expected in zip(CONTROLS, DEFAULTS):
        assert abs(group.interface.items_tree[name].default_value - expected) < 1e-7
    mode_items(group)
    assert group.interface.items_tree["Mode"].default_value == "Both", "Default menu must select Both."
    forbidden = {"ShaderNodeScript", "ShaderNodeTexImage", "ShaderNodeAttribute", "ShaderNodeNewGeometry"}
    assert not any(node.bl_idname in forbidden for node in group.nodes)
    assert not any(node.bl_idname == "ShaderNodeGroup" for node in group.nodes), "Extend asset must have no external nested dependency."
    assert any(node.bl_idname == "NodeSeparateBundle" for node in group.nodes)
    assert len([node for node in group.nodes if node.bl_idname == "NodeCombineBundle"]) == 2
    return {"name": group.name, "version": VERSION, "catalog_id": CATALOG_ID,
            "color_tag": group.color_tag, "inputs": interface_record(group, "INPUT"),
            "outputs": interface_record(group, "OUTPUT"), "nodes": len(group.nodes)}


def set_values(tree, instance, names, values, linked=False):
    for name, number in zip(names, values):
        if linked:
            value = tree.nodes.new("ShaderNodeValue")
            value.outputs[0].default_value = number
            tree.links.new(value.outputs[0], instance.inputs[name])
        else:
            instance.inputs[name].default_value = number


def extend_instance(tree, group, source, mode, values, linked=False, defaults=False):
    instance = tree.nodes.new("ShaderNodeGroup")
    instance.node_tree = group
    if source is not None:
        tree.links.new(source, instance.inputs["Source"])
    if defaults:
        assert instance.inputs["Mode"].default_value == "Both"
    else:
        instance.inputs["Mode"].default_value = mode
        set_values(tree, instance, CONTROLS, values, linked)
    return instance


def material_for_case(extend, arc, case):
    material = bpy.data.materials.new("Extend " + case["name"])
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    source = None
    if case.get("source") is not None:
        source_node = tree.nodes.new("ShaderNodeGroup")
        source_node.node_tree = arc
        set_values(tree, source_node, ARC_CONTROLS, case["source"], case["linked_parameters"])
        source = source_node.outputs["Ring Data"]
    instance = extend_instance(tree, extend, source, case["mode"], case["values"],
                               case["linked_parameters"], case["use_defaults"])
    for chain in case.get("chain", []):
        instance = extend_instance(tree, extend, instance.outputs[chain["branch"]], chain["mode"], chain["values"])
    mask = instance.outputs["Mask"]
    if case.get("both_branches"):
        masks = [extend_instance(tree, extend, instance.outputs[name], "Both", (.01, .005, 0)).outputs["Mask"]
                 for name in ("Inner Data", "Outer Data")]
        union = tree.nodes.new("ShaderNodeMath")
        union.operation = "MAXIMUM"
        for target, source in zip(union.inputs, masks):
            tree.links.new(source, target)
        mask = union.outputs[0]
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(mask, emission.inputs["Color"])
    tree.links.new(emission.outputs[0], output.inputs["Surface"])
    return material


def add_tile(extend, arc, case, index):
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
    mesh.materials.append(material_for_case(extend, arc, case))


def check_atlas(scene, all_cases, report_path, engine, label):
    scene.render.engine = engine
    if engine == "CYCLES":
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
    elif hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = 8
    exr = report_path.with_name("Extend_Mask_" + label + "_numeric_atlas.exr")
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    scene.render.filepath = str(exr)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    image = bpy.data.images.load(str(exr), check_existing=False)
    width, height = image.size
    assert width == height == GRID_SIZE * PIXELS_PER_TILE
    pixels = list(image.pixels)
    results, failures = [], []
    for index, case in enumerate(all_cases):
        x = index % GRID_SIZE * PIXELS_PER_TILE + PIXELS_PER_TILE // 2
        y = index // GRID_SIZE * PIXELS_PER_TILE + PIXELS_PER_TILE // 2
        offset = (y * width + x) * 4
        rgb = pixels[offset:offset + 3]
        error = max(abs(channel - case["expected"]) for channel in rgb)
        passed = error <= TOLERANCE and all(math.isfinite(c) and -TOLERANCE <= c <= 1 + TOLERANCE for c in rgb)
        record = {**case, "name": label + ": " + case["name"], "rgb": rgb,
                  "max_error": error, "pixel": [x, y], "passed": passed}
        results.append(record)
        if not passed:
            failures.append(record)
    bpy.data.images.remove(image)
    return {"engine": engine, "render_exr": str(exr), "sample_count": len(results),
            "maximum_absolute_error": max(item["max_error"] for item in results),
            "passed": not failures, "results": results, "failures": failures}


def preview(extend, arc, report_path):
    scene = bpy.data.scenes.new("Extend Mask four examples")
    RADIAL.setup_render(scene)
    scene.render.threads = 1
    scene.camera.location = (0, 0, 10)
    scene.camera.data.ortho_scale = 4.4
    scene.render.resolution_x = 640
    scene.render.resolution_y = 160
    scene.cycles.samples = 4
    examples = [
        {"name": "Full ring inner and outer bands", "source": (.48, .08, 0, 0, 360),
         "mode": "Both", "values": (.04, .02, .008)},
        {"name": "Arc radial bands retain its angles", "source": (.48, .08, 0, 20, 250),
         "mode": "Both", "values": (.04, .02, .008)},
        {"name": "Arc complete contour outline", "source": (.48, .08, 0, 20, 250),
         "mode": "Outline", "values": (.04, .02, .008)},
        {"name": "Both radial branches reused", "source": (.48, .08, 0, 0, 360),
         "mode": "Both", "values": (.04, .02, 0), "both_branches": True},
    ]
    for index, example in enumerate(examples):
        x = (index - 1.5) * 1.1
        mesh = bpy.data.meshes.new(example["name"])
        mesh.from_pydata([(x - .5, -.5, 0), (x + .5, -.5, 0),
                         (x + .5, .5, 0), (x - .5, .5, 0)], [], [(0, 1, 2, 3)])
        uv = mesh.uv_layers.new(name="UVMap")
        for loop, coordinate in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
            loop.uv = coordinate
        obj = bpy.data.objects.new(example["name"], mesh)
        scene.collection.objects.link(obj)
        case = {**example, "linked_parameters": False, "use_defaults": False}
        mesh.materials.append(material_for_case(extend, arc, case))
    image = report_path.with_name("Extend_Mask_examples.png")
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = str(image)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    return {"image": str(image), "left_to_right": examples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--arc-asset", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--eevee", action="store_true", help="Also run the same numeric atlas using native EEVEE.")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Verification requires a fresh isolated background Blender process.")
    asset, arc_asset, report_path = (Path(path).resolve() for path in (args.asset, args.arc_asset, args.report))
    if report_path.exists():
        raise FileExistsError("Refusing to overwrite previous verification evidence: " + str(report_path))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    asset_hash = hashlib.sha256(asset.read_bytes()).hexdigest()
    arc_hash = hashlib.sha256(arc_asset.read_bytes()).hexdigest()
    report = {"passed": False, "state": "verification_started", "asset": str(asset),
              "asset_sha256": asset_hash, "arc_mask_sha256": arc_hash,
              "blender": bpy.app.version_string, "render_engine": "Cycles CPU", "tolerance": TOLERANCE,
              "ui_search_tested": False, "tests": [],
              "source_dependencies_sha256": {path: source_hash(ROOT / path) for path in SOURCE_PATHS}}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with bpy.data.libraries.load(str(asset), link=False) as (source, target):
            assert NAME in source.node_groups
            target.node_groups = [NAME]
        group = target.node_groups[0]
        report["asset_metadata"] = validate(group)
        with bpy.data.libraries.load(str(arc_asset), link=False) as (source, target):
            assert "Arc Mask" in source.node_groups
            target.node_groups = ["Arc Mask"]
        arc = target.node_groups[0]
        assert arc.get("randy_asset_version") == "0.2.0"
        assert arc.interface.items_tree["Ring Data"].socket_type == "NodeSocketBundle"
        assert arc.color_tag == "TEXTURE"
        report["tests"].append({"name": "native_bundle_menu_interface_and_texture_metadata", "passed": True})
        all_cases = cases()
        for index, case in enumerate(all_cases):
            add_tile(group, arc, case, index)
        scene = bpy.context.scene
        RADIAL.setup_render(scene)
        scene.render.threads = 1
        fixture = report_path.with_name("Randy_Extend_Mask_validation.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(fixture), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(fixture), load_ui=False, use_scripts=False)
        group = bpy.data.node_groups[NAME]
        arc = bpy.data.node_groups["Arc Mask"]
        validate(group)
        assert arc.interface.items_tree["Ring Data"].socket_type == "NodeSocketBundle"
        assert not any(tree.bl_idname != "ShaderNodeTree" for tree in (group, arc))
        for material in bpy.data.materials:
            if not material.node_tree:
                continue
            for instance in material.node_tree.nodes:
                if instance.bl_idname == "ShaderNodeGroup" and instance.node_tree == group:
                    assert instance.inputs["Source"].type == "BUNDLE"
                    assert instance.inputs["Mode"].type == "MENU"
        report["tests"].append({"name": "native_save_reopen_preserves_menu_and_bundle_links", "passed": True})
        scene = bpy.context.scene
        atlas = check_atlas(scene, all_cases, report_path, "CYCLES", "cycles")
        report["tests"].extend(atlas.pop("results"))
        report["cycles"] = atlas
        assert atlas["passed"], json.dumps(atlas["failures"])
        report.update({"shader_sample_count": len(all_cases),
                       "maximum_absolute_error": atlas["maximum_absolute_error"],
                       "render_exr": atlas["render_exr"], "fixture": str(fixture)})
        if args.eevee:
            # Registered engine choices need not appear in static RNA enums.
            # BLENDER_EEVEE is the installed Blender 5.2 engine identifier.
            eevee = check_atlas(scene, all_cases, report_path, "BLENDER_EEVEE", "eevee")
            report["tests"].extend(eevee.pop("results"))
            report["eevee"] = eevee
            assert eevee["passed"], json.dumps(eevee["failures"])
            report["shader_sample_count"] += len(all_cases)
        report["examples"] = preview(group, arc, report_path)
        report["render_preview"] = report["examples"]["image"]
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == asset_hash
        assert hashlib.sha256(arc_asset.read_bytes()).hexdigest() == arc_hash
        report["tests"].append({"name": "both_input_assets_unchanged", "passed": True})
        report.update({"passed": True, "state": "complete"})
    except Exception as exc:
        report.update({"state": "failed", "error": str(exc), "traceback": traceback.format_exc()})
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("EXTEND_MASK_VERIFICATION " + json.dumps({"passed": report["passed"],
              "tests": len(report["tests"]), "report": str(report_path)}))


if __name__ == "__main__":
    main()
