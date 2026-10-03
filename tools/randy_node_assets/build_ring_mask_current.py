"""Build the current multifunction Ring Mask from the verified Arc 0.2.0 graph.

Only the public name, description, asset metadata and full-ring default change.
The complete native Mask and Ring Data implementation is retained, including
socket identifiers and the hidden historical radial dependency. This is not
the old three-input radial Ring Mask builder.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
NAME = "Ring Mask"
VERSION = "0.2.1"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
BASELINE = ROOT / "node_library/validation/fixtures/Randy_Arc_Mask_0_2_0.blend"
DESCRIPTION = (
    "A normalized UV ring or counterclockwise arc. Sweep Angle 360 makes a "
    "full ring; 180 makes a semicircle; 0 hides it. Start Angle rotates the "
    "arc in degrees. Edge Softness fades its radial boundaries. Ring Data "
    "carries the original boundaries and angles to Extend Mask. Native "
    "Texture node group; no add-on is required."
)


def default_source():
    return BASELINE if BASELINE.is_file() else ROOT / "node_library/assets/Randy_Arc_Mask.blend"


def append_current(source):
    with bpy.data.libraries.load(str(source), link=False) as (available, loaded):
        if "Arc Mask" not in available.node_groups:
            raise ValueError("The source must contain the validated Arc Mask 0.2.0.")
        loaded.node_groups = ["Arc Mask"]
    group = loaded.node_groups[0]
    if group.get("randy_asset_version") != "0.2.0":
        raise ValueError("Refusing to build from a historical radial or older Arc Mask.")
    items = [s for s in group.interface.items_tree if s.item_type == "SOCKET"]
    if [(s.name, s.socket_type) for s in items if s.in_out == "OUTPUT"] != [
            ("Mask", "NodeSocketFloat"), ("Ring Data", "NodeSocketBundle")]:
        raise ValueError("The source is missing the multifunction Ring Data interface.")
    return group


def build(source):
    group = append_current(source)
    group.name = NAME
    group.description = DESCRIPTION
    group.color_tag = "TEXTURE"
    group.use_fake_user = True
    sweep = next(s for s in group.interface.items_tree
                 if s.item_type == "SOCKET" and s.in_out == "INPUT" and s.name == "Sweep Angle")
    sweep.default_value = 360.0
    group["randy_asset_version"] = VERSION
    group["randy_asset_source"] = "tools/randy_node_assets/build_ring_mask_current.py"
    group["randy_arc_020_baseline_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    group.asset_data.description = DESCRIPTION
    group.asset_data.author = "Randy"
    group.asset_data.catalog_id = CATALOG_ID
    if "Full Ring" not in [tag.name for tag in group.asset_data.tags]:
        group.asset_data.tags.new("Full Ring")
    # An asset thumbnail follows the new full-ring default, not the old
    # semicircle preview. It is metadata and does not affect shader execution.
    preview = group.preview_ensure()
    preview.image_size = (128, 128)
    pixels = []
    for y in range(128):
        for x in range(128):
            r = (((x + .5) / 64 - 1) ** 2 + ((y + .5) / 64 - 1) ** 2) ** .5
            value = .92 if .6 <= r <= .68 else .055
            pixels.extend((value, value, value, 1.0))
    preview.image_pixels_float = pixels
    return group


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=default_source())
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Build only in an isolated factory background process.")
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        raise FileExistsError("Refusing to replace an existing build: " + str(output))
    original = source.read_bytes()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    group = build(source)
    bpy.data.libraries.write(str(output), {group}, fake_user=True, compress=True)
    assert source.read_bytes() == original, "Source Arc asset changed."
    print("CURRENT_RING_MASK_BUILD " + json.dumps({
        "name": group.name, "version": VERSION, "file": str(output),
        "source_arc_sha256": hashlib.sha256(original).hexdigest(),
        "default_sweep": 360.0,
    }))


if __name__ == "__main__":
    main()
