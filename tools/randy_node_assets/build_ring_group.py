"""Build a native Ring Group asset with one Shader and a union of masks.

RR Helper's readable canonical generator creates the graph, but no add-on is
registered or required to evaluate the saved asset. Optional edit-time helpers
can append Mask inputs; ordinary Blender nodes can extend the union manually.
"""

import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = "addons/random_realm_builder_exporter/rr_ring_nodes.py"
NAME = "Ring Group"
VERSION = "0.1.0"
CATALOG = "Textures"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
DESCRIPTION = (
    "Combine any Ring or Arc masks using Maximum, with one shared Shader. "
    "Connect the Mask and Shader outputs to a Mix Shader layer. "
    "Two initial Mask inputs; optional RR Helper adds Ring, Arc or Mask slots. "
    "The native saved graph evaluates without any add-on."
)


def source_hash():
    return hashlib.sha256((ROOT / GENERATOR).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def runtime():
    addons = ROOT / "addons"
    if str(addons) not in sys.path:
        sys.path.insert(0, str(addons))
    module = importlib.import_module("random_realm_builder_exporter.rr_ring_nodes")
    assert Path(module.__file__).resolve() == (ROOT / GENERATOR).resolve(), module.__file__
    return module


def build():
    helper = runtime()
    group = helper.new_group(mask_count=2)
    group.name = NAME
    group.use_fake_user = True
    group.description = DESCRIPTION
    group["randy_asset_version"] = VERSION
    group["randy_asset_source"] = "tools/randy_node_assets/build_ring_group.py"
    group["randy_asset_generator"] = GENERATOR
    group["randy_asset_generator_sha256"] = source_hash()
    group.asset_mark()
    group.asset_data.author = "Randy"
    group.asset_data.catalog_id = CATALOG_ID
    group.asset_data.description = DESCRIPTION
    for tag in ("Randy", "Textures", "Shader", "Ring Group", "Ring Mask", "Arc Mask", "Mask", "Maximum", "Union"):
        group.asset_data.tags.new(tag)
    preview = group.preview_ensure()
    preview.image_size = (128, 128)
    pixels = []
    for y in range(128):
        for x in range(128):
            radius = math.hypot((x + .5) / 64 - 1, (y + .5) / 64 - 1)
            ring = any(inner <= radius <= outer for inner, outer in ((.27, .35), (.49, .57), (.71, .79)))
            color = (.1, .65, .9) if ring else (.04, .07, .1)
            pixels.extend((*color, 1))
    preview.image_pixels_float = pixels
    helper._validate_group(group)
    assert not helper._MENU_REGISTERED
    assert all(not getattr(cls, "is_registered", False) for cls in helper.CLASSES)
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
    print("RING_GROUP_BUILD " + json.dumps({"name": NAME, "version": VERSION,
          "catalog": CATALOG, "catalog_id": CATALOG_ID, "file": str(output),
          "nodes": len(group.nodes), "generator_sha256": source_hash()}))


if __name__ == "__main__":
    main()
