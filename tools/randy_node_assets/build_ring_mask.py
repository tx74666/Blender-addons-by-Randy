"""Build a native Shader Node Group asset for a normalized circular UV mask.

The UV disk has center (0.5, 0.5) and outer radius 1 after multiplying XY by 2.
This script only writes a new named output; it never opens a user scene.
"""

import argparse
import json
import math
from pathlib import Path
import sys

import bpy

NAME = "Ring Mask"
CATALOG = "Textures"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
VERSION = "0.1.1"
INNER = "Inner Radius"
WIDTH = "Ring Width"
SOFTNESS = "Edge Softness"


def node(group, kind, name, xy, width=190):
    n = group.nodes.new(kind)
    n.name = name
    n.label = name
    n.location = xy
    n.width = width
    n.select = False
    return n


def link(group, source, target):
    group.links.new(source, target)


def math_node(group, operation, a, b, name, xy):
    n = node(group, "ShaderNodeMath", name, xy)
    n.operation = operation
    for i, value in enumerate((a, b)):
        if value is None:
            continue
        if isinstance(value, (float, int)):
            n.inputs[i].default_value = value
        else:
            link(group, value, n.inputs[i])
    return n.outputs[0]


def scalar(group, name, *, direction="INPUT", value=None, description=""):
    s = group.interface.new_socket(name, in_out=direction, socket_type="NodeSocketFloat")
    s.min_value = 0.0
    s.max_value = 1.0
    s.description = description
    if value is not None:
        s.default_value = value
    return s


def build():
    assert len(NAME.encode("utf-8")) <= 63
    g = bpy.data.node_groups.new(NAME, "ShaderNodeTree")
    g.use_fake_user = True
    g.default_group_node_width = 300
    description = (
        "Normalized UV ring mask. UV center=(0.5,0.5); the circular rim has Radius=1. "
        "Inner Radius + Ring Width defines the outer edge. Width 0 hides the ring; "
        "softness fades inward and is capped at half the width. Outside Radius 1 is black. "
        "Outputs only a 0-1 mask."
    )
    g.description = description
    scalar(g, "Mask", direction="OUTPUT", description="0 outside, 1 inside; soft edges allow intermediate values.")
    scalar(g, INNER, value=0.6, description="Normalized start radius, clamped to 0-1. 0=center, 1=circular UV rim.")
    scalar(g, WIDTH, value=0.08, description="Width measured outward from Inner Radius. Zero hides the ring; negative values are clamped to zero.")
    scalar(g, SOFTNESS, value=0.0, description="Inward smooth transition distance per edge; capped at half the width. Zero gives inclusive hard edges.")
    g["randy_asset_version"] = VERSION
    g["randy_asset_source"] = "tools/randy_node_assets/build_ring_mask.py"
    g["randy_uv_contract"] = "r=length((UV.xy-(0.5,0.5))*2); mask is clipped to r<=1"
    g["randy_softness_contract"] = "smoothstep(0,min(max(softness,0),max(width,0)/2),nearest_edge_distance); inward; no inner fade at inner=0"

    inp = node(g, "NodeGroupInput", "Ring controls", (-1700, -100))
    out = node(g, "NodeGroupOutput", "Mask only", (2030, 360))
    uv = node(g, "ShaderNodeTexCoord", "Active render UV", (-1700, 750))
    center = node(g, "ShaderNodeVectorMath", "UV minus center (0.5, 0.5)", (-1450, 750))
    center.operation = "SUBTRACT"
    center.inputs[1].default_value = (0.5, 0.5, 0.0)
    link(g, uv.outputs["UV"], center.inputs[0])
    normalize = node(g, "ShaderNodeVectorMath", "Normalize XY and ignore Z", (-1200, 750))
    normalize.operation = "MULTIPLY"
    normalize.inputs[1].default_value = (2.0, 2.0, 0.0)
    link(g, center.outputs[0], normalize.inputs[0])
    radius = node(g, "ShaderNodeVectorMath", "Normalized UV Radius", (-950, 750))
    radius.operation = "LENGTH"
    link(g, normalize.outputs[0], radius.inputs[0])
    r = radius.outputs["Value"]

    inner_min = math_node(g, "MAXIMUM", inp.outputs[INNER], 0.0, "Inner minimum", (-1450, 140))
    inner = math_node(g, "MINIMUM", inner_min, 1.0, "Inner maximum", (-1200, 140))
    width = math_node(g, "MAXIMUM", inp.outputs[WIDTH], 0.0, "Nonnegative width", (-1450, -130))
    outer = math_node(g, "ADD", inner, width, "Outer = inner + width", (-950, -50))
    half_width = math_node(g, "MULTIPLY", width, 0.5, "Half width", (-1200, -350))
    soft_positive = math_node(g, "MAXIMUM", inp.outputs[SOFTNESS], 0.0, "Nonnegative softness", (-1450, -600))
    soft = math_node(g, "MINIMUM", soft_positive, half_width, "Softness capped to half width", (-950, -440))

    din = math_node(g, "SUBTRACT", r, inner, "Distance from inner edge", (-690, 720))
    dout = math_node(g, "SUBTRACT", outer, r, "Distance from outer edge", (-690, 440))
    has_inner = math_node(g, "GREATER_THAN", inner, 0.0, "Has inner hole", (-690, 130))
    no_inner = math_node(g, "SUBTRACT", 1.0, has_inner, "Bypass center fade for disk", (-440, 20))
    din_used = math_node(g, "MULTIPLY", din, has_inner, "Use inner boundary if present", (-440, 720))
    bypass = math_node(g, "MULTIPLY", outer, no_inner, "Disk bypass distance", (-200, 30))
    effective_inner = math_node(g, "ADD", din_used, bypass, "Effective inner distance", (40, 720))
    nearest = math_node(g, "MINIMUM", effective_inner, dout, "Nearest edge distance", (290, 620))
    outside = math_node(g, "LESS_THAN", nearest, 0.0, "Outside ring", (540, 850))
    hard = math_node(g, "SUBTRACT", 1.0, outside, "Inclusive hard ring", (780, 850))

    # Map Range implements clamped smoothstep. A safe denominator avoids an
    # undefined zero-width mapping on the unused soft branch.
    safe_soft = math_node(g, "MAXIMUM", soft, 1e-8, "Safe soft width", (40, -180))
    smooth = node(g, "ShaderNodeMapRange", "Smooth inward transition", (540, 400))
    smooth.data_type = "FLOAT"
    smooth.interpolation_type = "SMOOTHSTEP"
    smooth.clamp = True
    smooth.inputs["From Min"].default_value = 0.0
    smooth.inputs["To Min"].default_value = 0.0
    smooth.inputs["To Max"].default_value = 1.0
    link(g, nearest, smooth.inputs["Value"])
    link(g, safe_soft, smooth.inputs["From Max"])
    soft_on = math_node(g, "GREATER_THAN", soft, 0.0, "Soft edge enabled", (290, -190))
    soft_off = math_node(g, "SUBTRACT", 1.0, soft_on, "Hard edge enabled", (540, -160))
    hard_part = math_node(g, "MULTIPLY", hard, soft_off, "Hard branch", (1030, 850))
    soft_part = math_node(g, "MULTIPLY", smooth.outputs["Result"], soft_on, "Soft branch", (1030, 460))
    ring = math_node(g, "ADD", hard_part, soft_part, "Selected edge profile", (1280, 680))
    width_active = math_node(g, "GREATER_THAN", width, 0.0, "Zero width is invisible", (540, -410))
    outside_disk = math_node(g, "GREATER_THAN", r, 1.0, "Outside normalized UV disk", (40, 1150))
    in_disk = math_node(g, "SUBTRACT", 1.0, outside_disk, "Keep Radius <= 1", (1280, 1120))
    active = math_node(g, "MULTIPLY", width_active, in_disk, "Active ring within disk", (1520, 960))
    mask = math_node(g, "MULTIPLY", ring, active, "Bounded Mask", (1780, 650))
    link(g, mask, out.inputs["Mask"])

    g.asset_mark()
    g.asset_data.author = "Randy"
    g.asset_data.description = description
    g.asset_data.catalog_id = CATALOG_ID
    for tag in ("Randy", "Shader", "Ring Mask", "Normalized UV", "Mask", "Textures", "Concentric Rings"):
        g.asset_data.tags.new(tag)
    # A mathematical thumbnail is embedded in the asset; it is not a render test.
    preview = g.preview_ensure()
    preview.image_size = (128, 128)
    pixels = []
    for y in range(128):
        for x in range(128):
            rr = math.hypot((x+0.5)/64-1, (y+0.5)/64-1)
            value = 0.92 if 0.6 <= rr <= 0.68 else 0.055
            pixels.extend((value,value,value,1.0))
    preview.image_pixels_float = pixels
    return g


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    args = p.parse_args(sys.argv[sys.argv.index("--")+1:])
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing build: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    group = build()
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    print(json.dumps({'name':group.name,'type':group.bl_idname,'catalog':CATALOG,'file':str(output),'nodes':len(group.nodes)},ensure_ascii=True))


if __name__ == "__main__":
    main()
