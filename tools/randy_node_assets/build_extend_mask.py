"""Build native Extend Mask for radial bands and complete contour outlines.

Run only in an isolated factory background Blender process. The saved asset
uses native Bundle, Menu Switch and Shader nodes and evaluates without an
add-on. This builder never opens a working scene or overwrites a prior build.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ring_boundary import CONTRACT, Graph, angular_fields, pack, radial_band, sector_distance, unpack


ROOT = Path(__file__).resolve().parents[2]
NAME = "Extend Mask"
VERSION = "0.1.0"
CATALOG = "Textures"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
MODES = ("Inner", "Outer", "Both", "Outline")
DESCRIPTION = (
    "Create new equal-width bands from Arc Mask Ring Data. Inner, Outer and "
    "Both keep the source angles; Outline grows along all contours, including "
    "arc ends. Gap starts at the source boundary. Original source is excluded. "
    "Inner Data and Outer Data can feed another Extend for radial modes only; "
    "Outline does not output reusable radial data. No add-on is required."
)


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def scalar(group, name, direction, default=None, description=""):
    result = group.interface.new_socket(name, in_out=direction, socket_type="NodeSocketFloat")
    result.min_value = 0.0
    result.max_value = 1.0
    result.description = description
    if default is not None:
        result.default_value = default
    return result


def bundle(group, name, direction, description):
    result = group.interface.new_socket(name, in_out=direction, socket_type="NodeSocketBundle")
    result.description = description
    return result


def build():
    group = bpy.data.node_groups.new(NAME, "ShaderNodeTree")
    group.use_fake_user = True
    group.default_group_node_width = 290
    group.color_tag = "TEXTURE"
    group.description = DESCRIPTION
    scalar(group, "Mask", "OUTPUT", description="Only the newly extended bands; use as a material mix factor.")
    bundle(group, "Inner Data", "OUTPUT", "Reusable inner band for another Extend; inactive in Outer and Outline modes.")
    bundle(group, "Outer Data", "OUTPUT", "Reusable outer band for another Extend; inactive in Inner and Outline modes.")
    bundle(group, "Source", "INPUT", "Connect Arc Mask Ring Data, or an Extend Inner Data / Outer Data output. Missing source is empty.")
    mode_socket = group.interface.new_socket("Mode", in_out="INPUT", socket_type="NodeSocketMenu")
    mode_socket.description = "Inner, Outer or Both preserve the arc's angles. Outline grows around every contour, including arc ends."
    scalar(group, "Width", "INPUT", .01, "Equal width of each newly created band; zero disables the result.")
    scalar(group, "Gap", "INPUT", 0.0, "Empty distance from the source boundary before the new band starts.")
    scalar(group, "Softness", "INPUT", 0.0, "Inward edge fade, capped at half the new band's visible width.")
    graph = Graph(group, prefix="Extend", origin=(-2200, 1100))
    controls = graph.node("NodeGroupInput", "Extend controls", 290)
    output = graph.node("NodeGroupOutput", "Extended bands", 290)
    output.is_active_output = True
    source = unpack(group, controls.outputs["Source"])
    position = source["Position"]
    inner, outer, _ = graph.separate(source["Bounds"], "Source bounds")
    start, sweep, source_active = graph.separate(source["Arc"], "Source arc")
    inner = graph.clamp(inner, 0.0, 1.0, "Source inner")
    outer = graph.clamp(outer, inner, 1.0, "Source outer")
    start = graph.math("FLOORED_MODULO", start, 360.0, name="Source wrapped start")
    sweep = graph.clamp(sweep, 0.0, 360.0, "Source sweep")
    source_active = graph.math("GREATER_THAN", source_active, .5, name="Source active flag")
    source_width = graph.math("SUBTRACT", outer, inner, name="Source visible width")
    valid_width = graph.math("GREATER_THAN", source_width, 0.0, name="Source width positive")
    valid_sweep = graph.math("GREATER_THAN", sweep, 0.0, name="Source sweep positive")
    source_active = graph.math("MULTIPLY", source_active, valid_width, name="Source band valid")
    source_active = graph.math("MULTIPLY", source_active, valid_sweep, name="Source sector valid")
    width = graph.math("MAXIMUM", controls.outputs["Width"], 0.0, name="Nonnegative extension width")
    gap = graph.math("MAXIMUM", controls.outputs["Gap"], 0.0, name="Nonnegative boundary gap")
    width_on = graph.math("GREATER_THAN", width, 0.0, name="Extension enabled")
    enabled = graph.math("MULTIPLY", source_active, width_on, name="Valid nonempty extension")
    radius = graph.vector("LENGTH", position, name="Normalized radius")
    outside_disk = graph.math("GREATER_THAN", radius, 1.0, name="Outside normalized disk")
    in_disk = graph.math("SUBTRACT", 1.0, outside_disk, name="Inside normalized disk")
    angular_inside, has_ends, start_unit, end_unit = angular_fields(graph, position, start, sweep)

    mode = graph.node("GeometryNodeMenuSwitch", "Extension mode")
    mode.data_type = "FLOAT"
    mode.enum_definition.enum_items.clear()
    for item in MODES:
        mode.enum_definition.enum_items.new(item)
    for index, item in enumerate(MODES):
        mode.inputs[item].default_value = float(index)
    mode.inputs["Menu"].default_value = "Both"
    graph.value(mode.inputs["Menu"], controls.outputs["Mode"])
    mode_socket.default_value = "Both"
    mode_value = mode.outputs[0]
    mode_flags = []
    for index, name in enumerate(MODES):
        delta = graph.math("SUBTRACT", mode_value, float(index), name=name + " mode delta")
        absolute = graph.math("ABSOLUTE", delta, name=name + " mode distance")
        mode_flags.append(graph.math("LESS_THAN", absolute, .5, name=name + " mode selected"))
    inner_mode = graph.math("MAXIMUM", mode_flags[0], mode_flags[2], name="Inner band selected")
    outer_mode = graph.math("MAXIMUM", mode_flags[1], mode_flags[2], name="Outer band selected")

    inner_outer = graph.math("SUBTRACT", inner, gap, name="Inner band outer before clip")
    inner_inner = graph.math("SUBTRACT", inner_outer, width, name="Inner band inner before clip")
    inner_inner = graph.clamp(inner_inner, 0.0, 1.0, "Inner band inner")
    inner_outer = graph.clamp(inner_outer, 0.0, 1.0, "Inner band outer")
    outer_inner = graph.math("ADD", outer, gap, name="Outer band inner before clip")
    outer_outer = graph.math("ADD", outer_inner, width, name="Outer band outer before clip")
    outer_inner = graph.clamp(outer_inner, 0.0, 1.0, "Outer band inner")
    outer_outer = graph.clamp(outer_outer, 0.0, 1.0, "Outer band outer")
    inner_width = graph.math("SUBTRACT", inner_outer, inner_inner, name="Inner band width")
    outer_width = graph.math("SUBTRACT", outer_outer, outer_inner, name="Outer band width")
    inner_on = graph.math("GREATER_THAN", inner_width, 0.0, name="Inner band exists")
    outer_on = graph.math("GREATER_THAN", outer_width, 0.0, name="Outer band exists")
    inner_active = graph.math("MULTIPLY", enabled, inner_on, name="Inner band source valid")
    inner_active = graph.math("MULTIPLY", inner_active, inner_mode, name="Inner data active")
    outer_active = graph.math("MULTIPLY", enabled, outer_on, name="Outer band source valid")
    outer_active = graph.math("MULTIPLY", outer_active, outer_mode, name="Outer data active")
    inner_mask, inner_soft = radial_band(graph, radius, inner_inner, inner_outer, angular_inside, inner_active, controls.outputs["Softness"], "Inner band")
    outer_mask, outer_soft = radial_band(graph, radius, outer_inner, outer_outer, angular_inside, outer_active, controls.outputs["Softness"], "Outer band")
    radial_mask = graph.math("MAXIMUM", inner_mask, outer_mask, name="Selected radial bands union")

    signed_distance = sector_distance(graph, position, radius, inner, outer, angular_inside, has_ends, start_unit, end_unit)
    near = graph.math("SUBTRACT", signed_distance, gap, name="Outline distance from gap")
    far_limit = graph.math("ADD", gap, width, name="Outline far boundary")
    far = graph.math("SUBTRACT", far_limit, signed_distance, name="Outline distance to far boundary")
    nearest = graph.math("MINIMUM", near, far, name="Outline nearest band edge")
    outline, _ = graph.smooth_profile(nearest, width, controls.outputs["Softness"], "Outline")
    outline_on = graph.math("MULTIPLY", enabled, mode_flags[3], name="Outline selected and valid")
    outline = graph.math("MULTIPLY", outline, outline_on, name="Enabled contour outline")
    combined = graph.math("MAXIMUM", radial_mask, outline, name="Extended band result")
    combined = graph.math("MULTIPLY", combined, in_disk, name="Mask inside normalized disk")
    graph.value(output.inputs["Mask"], combined)

    inner_bounds = graph.combine(inner_inner, inner_outer, inner_soft, "Inner data bounds")
    outer_bounds = graph.combine(outer_inner, outer_outer, outer_soft, "Outer data bounds")
    inner_arc = graph.combine(start, sweep, inner_active, "Inner data arc")
    outer_arc = graph.combine(start, sweep, outer_active, "Outer data arc")
    graph.value(output.inputs["Inner Data"], pack(group, position, inner_bounds, inner_arc, name="Inner Data bundle"))
    graph.value(output.inputs["Outer Data"], pack(group, position, outer_bounds, outer_arc, name="Outer Data bundle"))
    output.location = (0, -7800)
    group["randy_asset_version"] = VERSION
    group["randy_asset_source"] = "tools/randy_node_assets/build_extend_mask.py"
    group["randy_boundary_contract"] = CONTRACT
    group["randy_extend_contract"] = "Inner/Outer/Both: exact clipped radial bands; Outline: gap<=exact_signed_sector_distance<=gap+width; source excluded; radial data only"
    group["randy_uv_contract"] = "Source Position is (UV.xy-(0.5,0.5))*2; result clipped to length(Position)<=1"
    group.asset_mark()
    group.asset_data.author = "Randy"
    group.asset_data.catalog_id = CATALOG_ID
    group.asset_data.description = DESCRIPTION
    for tag in ("Randy", "Textures", "Shader", "Mask", "Extend", "Arc", "Ring", "Outline", "Boundary Data", "Normalized UV"):
        group.asset_data.tags.new(tag)
    preview = group.preview_ensure()
    preview.image_size = (128, 128)
    pixels = []
    for y in range(128):
        for x in range(128):
            radius = math.hypot((x + .5) / 64 - 1, (y + .5) / 64 - 1)
            band = .45 <= radius <= .50 or .60 <= radius <= .65
            value = .92 if band else .055
            pixels.extend((value, value, value, 1.0))
    preview.image_pixels_float = pixels
    return group


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Build only in an isolated background Blender process.")
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing build: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    group = build()
    bpy.data.libraries.write(str(output), {group}, fake_user=True, compress=True)
    print("EXTEND_MASK_BUILD " + json.dumps({
        "name": NAME, "version": VERSION, "catalog": CATALOG,
        "catalog_id": CATALOG_ID, "file": str(output), "nodes": len(group.nodes),
        "boundary_helper_sha256": source_hash(Path(__file__).with_name("ring_boundary.py")),
    }))


if __name__ == "__main__":
    main()
