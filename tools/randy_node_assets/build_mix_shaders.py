"""Build the native Mix Shaders asset using RR Helper's canonical generator.

Run only in an isolated factory Blender process. This never opens a working
scene or replaces an existing build. The asset shares Ring Mask's Textures
catalog; RR Helper supplies expansion and automatic empty-Shader detection.
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
GENERATOR = "addons/random_realm_builder_exporter/rr_shader_mixer.py"
NAME = "Mix Shaders"
CATALOG = "Textures"
CATALOG_ID = "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
VERSION = "0.1.0"
DESCRIPTION = (
    "Mix masked shaders over a Base Shader. Earlier slots cover later slots. "
    "Use RR Helper's Add Shader Slot to expand; keep RR Helper enabled for "
    "automatic empty-Shader passthrough. Saved graphs render natively."
)


def source_hash():
    return hashlib.sha256((ROOT / GENERATOR).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def runtime():
    addons = ROOT / "addons"
    if str(addons) not in sys.path:
        sys.path.insert(0, str(addons))
    module = importlib.import_module("random_realm_builder_exporter.rr_shader_mixer")
    assert Path(module.__file__).resolve() == (ROOT / GENERATOR).resolve(), module.__file__
    return module


def build():
    helper = runtime()
    group = helper._new_group()
    group.name = NAME
    group.default_group_node_width = 220
    group.use_fake_user = True
    group.description = DESCRIPTION
    group["randy_asset_version"] = VERSION
    group["randy_asset_source"] = "tools/randy_node_assets/build_mix_shaders.py"
    group["randy_asset_generator"] = GENERATOR
    group["randy_asset_generator_sha256"] = source_hash()
    group.asset_mark()
    group.asset_data.author = "Randy"
    group.asset_data.description = DESCRIPTION
    group.asset_data.catalog_id = CATALOG_ID
    for tag in ("Randy", "Textures", "Shader", "Mix Shaders", "Mask", "Ring Mask"):
        group.asset_data.tags.new(tag)
    # An explanatory mathematical thumbnail, not numerical render evidence.
    preview = group.preview_ensure()
    preview.image_size = (128, 128)
    pixels = []
    for y in range(128):
        for x in range(128):
            radius = math.hypot((x + .5)/64 - 1, (y + .5)/64 - 1)
            color = (.08, .15, .23)
            for inner, outer, ring_color in ((.26, .36, (.15, .7, .85)),
                                              (.48, .58, (.95, .65, .12)),
                                              (.70, .80, (.65, .25, .8))):
                if inner <= radius <= outer:
                    color = ring_color
            pixels.extend((*color, 1))
    preview.image_pixels_float = pixels
    helper._validate_group(group)
    return group


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Build only in an isolated background Blender process.")
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError("Refusing to replace existing build: {}".format(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    group = build()
    bpy.data.libraries.write(str(output), {group}, fake_user=True, compress=True)
    print("MIX_SHADERS_BUILD " + json.dumps({"name": group.name, "type": group.bl_idname,
          "catalog": CATALOG, "catalog_id": CATALOG_ID, "file": str(output),
          "nodes": len(group.nodes), "generator_sha256": source_hash()}))


if __name__ == "__main__":
    main()
