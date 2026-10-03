"""Build a native Arc Mask by angularly cropping the existing Ring Mask.

Run in a fresh factory background process. The radial implementation is the
unchanged Ring Mask asset, referenced by one nested node group. Only UV angle
gating is new. No add-on, external files or Python execution is needed at render
time. This generator never opens a user scene or overwrites an existing build.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ring_boundary import arc_data


ROOT = Path(__file__).resolve().parents[2]
NAME = "Arc Mask"
VERSION = "0.2.0"
CATALOG = "Textures"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
RING_ASSET = ROOT / "node_library/dependencies/Randy_Ring_Mask.blend"
RING_NAMES = ("Inner Radius", "Ring Width", "Edge Softness")
INPUT_NAMES = (*RING_NAMES, "Start Angle", "Sweep Angle")
DEFAULTS = (0.6, 0.08, 0.0, 0.0, 180.0)
DESCRIPTION = (
    "A counterclockwise arc of the existing normalized UV Ring Mask. "
    "Angles use degrees: 0 starts to the UV right, 90 points up. "
    "Sweep 0 is empty; 180 is a semicircle; 360 is a full ring. "
    "Start wraps across 0/360. Edge Softness retains Ring Mask's radial fade; "
    "angular ends are hard. Ring Data carries the boundaries and angles to "
    "Extend Mask without repeating the controls. No add-on is required."
)


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def node(group, kind, name, location, width=190):
    result = group.nodes.new(kind)
    result.name = name
    result.label = name
    result.location = location
    result.width = width
    result.select = False
    return result


def math_node(group, operation, values, name, location):
    result = node(group, "ShaderNodeMath", name, location)
    result.operation = operation
    for socket, value in zip(result.inputs, values):
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            group.links.new(value, socket)
    return result.outputs[0]


def socket(group, name, direction, default=None, minimum=0.0, maximum=1.0, description=""):
    result = group.interface.new_socket(name, in_out=direction, socket_type="NodeSocketFloat")
    result.min_value = minimum
    result.max_value = maximum
    result.description = description
    if default is not None:
        result.default_value = default
    return result


def append_ring(path):
    """Append the validated dependency; do not change its source file."""
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        if "Ring Mask" not in source.node_groups:
            raise ValueError("The selected Ring Mask asset does not contain Ring Mask.")
        target.node_groups = ["Ring Mask"]
    group = target.node_groups[0]
    inputs = [item.name for item in group.interface.items_tree
              if item.item_type == "SOCKET" and item.in_out == "INPUT"]
    if group.bl_idname != "ShaderNodeTree" or inputs != list(RING_NAMES):
        raise ValueError("Unexpected Ring Mask interface; do not silently replace its computation.")
    if group.get("randy_asset_version") != "0.1.1":
        raise ValueError("This Arc Mask build requires the validated Ring Mask 0.1.1 asset.")
    if group.get("randy_uv_contract") != "r=length((UV.xy-(0.5,0.5))*2); mask is clipped to r<=1":
        raise ValueError("Unexpected Ring Mask coordinates.")
    # Keep the native dependency, but do not advertise it as a second Ring Mask
    # asset in this additional asset file. The original asset file is unchanged.
    group.asset_clear()
    group.use_fake_user = False
    # Internal radial implementation must not appear as another Add > Group item.
    group.name = ".Arc Mask Radial"
    return group


def build(ring):
    group = bpy.data.node_groups.new(NAME, "ShaderNodeTree")
    group.use_fake_user = True
    group.default_group_node_width = 270
    group.color_tag = "TEXTURE"
    group.description = DESCRIPTION
    socket(group, "Mask", "OUTPUT", description="0 outside the arc, 1 inside; radial soft edges allow intermediate values.")
    socket(group, RING_NAMES[0], "INPUT", DEFAULTS[0], description="The existing Ring Mask inner radius: 0 at UV center, 1 at the circular rim.")
    socket(group, RING_NAMES[1], "INPUT", DEFAULTS[1], description="The existing Ring Mask width extending outward. Zero hides the arc.")
    socket(group, RING_NAMES[2], "INPUT", DEFAULTS[2], description="The existing Ring Mask radial softness. Angular ends remain hard.")
    socket(group, "Start Angle", "INPUT", DEFAULTS[3], -360.0, 360.0,
           "Degrees counterclockwise from UV right: 0=right, 90=up, 180=left, 270=down. Any linked angle wraps.")
    socket(group, "Sweep Angle", "INPUT", DEFAULTS[4], 0.0, 360.0,
           "Counterclockwise span in degrees, clamped to 0-360. 0=empty; 180=semicircle; 360=full ring.")
    # Append after all original sockets so existing Mask and control identifiers
    # stay stable. Ring Data carries geometry to Extend without modifying Mask.
    data_output = group.interface.new_socket("Ring Data", in_out="OUTPUT", socket_type="NodeSocketBundle")
    data_output.description = "Boundary data for Extend Mask; connect Mask to material mixing as before."
    group["randy_asset_version"] = VERSION
    group["randy_asset_source"] = "tools/randy_node_assets/build_arc_mask.py"
    group["randy_uv_contract"] = ring["randy_uv_contract"]
    group["randy_arc_contract"] = "Ring Mask * gate((degrees(atan2(UV.y-.5,UV.x-.5))-start)%360 <= clamp(sweep,0,360)); sweep<=0 empty; sweep>=360 full"
    group["randy_ring_dependency"] = "Ring Mask 0.1.1; native shared nested group; source radial graph unchanged"

    controls = node(group, "NodeGroupInput", "Arc controls", (-1500, -150))
    output = node(group, "NodeGroupOutput", "Arc Mask", (1540, 300))
    radial = node(group, "ShaderNodeGroup", "Existing Ring Mask", (-1240, -150), 270)
    radial.node_tree = ring
    for name in RING_NAMES:
        group.links.new(controls.outputs[name], radial.inputs[name])

    uv = node(group, "ShaderNodeTexCoord", "Active render UV", (-1500, 750))
    centered = node(group, "ShaderNodeVectorMath", "Same UV center as Ring Mask", (-1240, 750))
    centered.operation = "SUBTRACT"
    centered.inputs[1].default_value = (0.5, 0.5, 0.0)
    group.links.new(uv.outputs["UV"], centered.inputs[0])
    xy = node(group, "ShaderNodeSeparateXYZ", "UV XY only", (-980, 750))
    group.links.new(centered.outputs[0], xy.inputs[0])
    angle = math_node(group, "ARCTAN2", (xy.outputs["Y"], xy.outputs["X"]), "Angle from UV right", (-740, 750))
    degrees = math_node(group, "DEGREES", (angle,), "Angle in degrees", (-500, 750))
    delta = math_node(group, "SUBTRACT", (degrees, controls.outputs["Start Angle"]), "Angle relative to start", (-260, 750))
    wrapped = math_node(group, "FLOORED_MODULO", (delta, 360.0), "Wrapped angle 0 to 360", (-20, 750))
    positive = math_node(group, "MAXIMUM", (controls.outputs["Sweep Angle"], 0.0), "Nonnegative sweep", (-740, 50))
    sweep = math_node(group, "MINIMUM", (positive, 360.0), "Sweep capped to full circle", (-500, 50))
    outside = math_node(group, "GREATER_THAN", (wrapped, sweep), "Outside angular span", (240, 750))
    inside = math_node(group, "SUBTRACT", (1.0, outside), "Inclusive angular span", (500, 750))
    enabled = math_node(group, "GREATER_THAN", (sweep, 0.0), "Zero sweep is invisible", (-20, 50))
    gate = math_node(group, "MULTIPLY", (inside, enabled), "Arc angular gate", (760, 600))
    mask = math_node(group, "MULTIPLY", (radial.outputs["Mask"], gate), "Radial Mask times Arc gate", (1260, 300))
    group.links.new(mask, output.inputs["Mask"])
    group.links.new(arc_data(group, controls.outputs, uv.outputs["UV"]), output.inputs["Ring Data"])
    group["randy_boundary_contract"] = "ring-boundary-v1: Position, Bounds and Arc vectors in a native Bundle"

    group.asset_mark()
    group.asset_data.author = "Randy"
    group.asset_data.catalog_id = CATALOG_ID
    group.asset_data.description = DESCRIPTION
    for tag in ("Randy", "Textures", "Shader", "Mask", "Arc", "Semi Ring", "Ring Mask", "Normalized UV"):
        group.asset_data.tags.new(tag)
    preview = group.preview_ensure()
    preview.image_size = (128, 128)
    pixels = []
    for y in range(128):
        for x in range(128):
            dx, dy = (x + .5) / 64 - 1, (y + .5) / 64 - 1
            radius = math.hypot(dx, dy)
            angle = math.degrees(math.atan2(dy, dx)) % 360
            value = .92 if .6 <= radius <= .68 and 0 <= angle <= 180 else .055
            pixels.extend((value, value, value, 1.0))
    preview.image_pixels_float = pixels
    return group


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ring-asset", default=str(RING_ASSET))
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Build only in an isolated background Blender process.")
    output = Path(args.output).resolve()
    ring_path = Path(args.ring_asset).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing build: {output}")
    ring_hash = hashlib.sha256(ring_path.read_bytes()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    group = build(append_ring(ring_path))
    group["randy_ring_asset_sha256"] = ring_hash
    bpy.data.libraries.write(str(output), {group}, fake_user=True, compress=True)
    assert hashlib.sha256(ring_path.read_bytes()).hexdigest() == ring_hash
    print("ARC_MASK_BUILD " + json.dumps({"name": group.name, "version": VERSION,
          "catalog": CATALOG, "catalog_id": CATALOG_ID, "file": str(output),
          "nodes": len(group.nodes), "ring_asset_sha256": ring_hash}))


if __name__ == "__main__":
    main()
