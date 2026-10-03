"""Verify the saved Mix Shaders asset, including three external Ring Masks.

Run in a serial factory background Blender process. The asset is appended from
disk; RR Helper's canonical expansion code is used, while Ring Mask's existing
independent scalar oracle supplies expected masks. One tiny CPU atlas tests the
actual shader result. Fixtures and images stay beside the verification report.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_mix_shaders import CATALOG_ID, DESCRIPTION, GENERATOR, NAME, ROOT, VERSION, runtime
import verify_ring_mask as radial


DEPENDENCIES = (GENERATOR, "tools/randy_node_assets/build_mix_shaders.py",
                "tools/randy_node_assets/verify_mix_shaders.py", "tools/randy_node_assets/verify_ring_mask.py",
                "tools/randy_node_assets/deploy_ring_mask.py", "tools/randy_node_assets/deploy_assets.py")
TOLERANCE = .002
GRID = 3
TILE_PIXELS = 12


def hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for path in DEPENDENCIES}


def append_group(path, name):
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        assert name in source.node_groups, source.node_groups
        target.node_groups = [name]
    return target.node_groups[0]


def metadata(group, helper):
    assert group.name == NAME and group.bl_idname == "ShaderNodeTree"
    assert group.library is None and group.color_tag == "SHADER"
    assert group.asset_data and group.asset_data.author == "Randy"
    assert group.asset_data.catalog_id == CATALOG_ID
    assert group.asset_data.description == DESCRIPTION
    assert group["randy_asset_version"] == VERSION
    assert group["randy_asset_generator"] == GENERATOR
    assert group["randy_asset_generator_sha256"] == hashes()[GENERATOR]
    assert group.default_group_node_width == 220
    helper._validate_group(group)
    assert group.get(helper._KIND) == 2
    assert len(helper._pairs(group)) == 2
    items = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    assert [item.name for item in items if item.in_out == "INPUT"] == [
        "Base Shader", "Mask 1", "Shader 1", "Mask 2", "Shader 2"]
    assert not any(item.name.startswith("_Connected") for item in items)
    assert [item.name for item in items if item.in_out == "OUTPUT"] == ["Shader"]
    assert {node.bl_idname for node in group.nodes} == {
        "NodeGroupInput", "NodeGroupOutput", "ShaderNodeMath", "ShaderNodeMixShader"}
    expected = helper._new_group()
    try:
        assert helper._graph_signature(group) == helper._graph_signature(expected)
    finally:
        bpy.data.node_groups.remove(expected)
    return {"name": group.name, "catalog_id": group.asset_data.catalog_id,
            "color_tag": group.color_tag, "version": VERSION,
            "inputs": [item.name for item in items if item.in_out == "INPUT"],
            "outputs": [item.name for item in items if item.in_out == "OUTPUT"],
            "hidden_runtime_inputs": [], "nodes": len(group.nodes),
            "default_group_node_width": group.default_group_node_width,
            "tags": [tag.name for tag in group.asset_data.tags]}


def shader(tree, name, color):
    node = tree.nodes.new("ShaderNodeEmission")
    node.name = name
    node.inputs["Color"].default_value = (*color, 1)
    node.inputs["Strength"].default_value = 1
    return node.outputs[0]


def links(tree):
    return sorted((item.from_node.name, item.from_socket.identifier,
                   item.to_node.name, item.to_socket.identifier) for item in tree.links)


def independent_expansion(group, helper):
    material = bpy.data.materials.new("Independent Mix Shaders Asset Instances")
    material.use_nodes = True
    material.use_fake_user = True
    tree = material.node_tree
    tree.nodes.clear()
    nodes = [tree.nodes.new("ShaderNodeGroup") for _ in range(2)]
    for node in nodes:
        node.node_tree = group
    base = shader(tree, "Original Base", (0, 0, 1))
    overlay = shader(tree, "Original Overlay", (1, 0, 0))
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(base, nodes[0].inputs["Base Shader"])
    tree.links.new(overlay, nodes[0].inputs["Shader 1"])
    tree.links.new(nodes[0].outputs[0], output.inputs["Surface"])
    nodes[0].inputs["Mask 1"].default_value = .37
    nodes[1].inputs["Mask 1"].default_value = .81
    helper.sync_tree(tree)
    before = links(tree)
    identifiers = [item.identifier for item in nodes[0].inputs]
    helper.add_shader_slot(nodes[0])
    assert nodes[0].node_tree != group and nodes[1].node_tree == group
    assert len(helper._pairs(nodes[0].node_tree)) == 3
    assert len(helper._pairs(nodes[1].node_tree)) == 2
    assert links(tree) == before
    assert [item.identifier for item in nodes[0].inputs][:len(identifiers)] == identifiers
    assert abs(nodes[0].inputs["Mask 1"].default_value - .37) < 1e-6
    assert abs(nodes[1].inputs["Mask 1"].default_value - .81) < 1e-6
    assert nodes[0].node_tree.asset_data is None and not nodes[0].node_tree.use_fake_user
    assert group.asset_data is not None and group.use_fake_user
    for count in range(4, 26):
        assert helper.add_shader_slot(nodes[0]) == count
    assert len(helper._pairs(nodes[0].node_tree)) == 25
    assert len(nodes[0].inputs) == 51
    assert nodes[1].node_tree == group and len(helper._pairs(group)) == 2
    assert links(tree) == before
    assert [item.identifier for item in nodes[0].inputs][:len(identifiers)] == identifiers
    assert abs(nodes[0].inputs["Mask 1"].default_value - .37) < 1e-6
    assert not any(item.name.startswith("_Connected") for node in nodes for item in node.inputs)
    return material.name, before, 25


def cases():
    rings = ((.4, .4, 0), (.25, .6, 0), (.1, .8, 0))
    colors = ((0, 1, 0), (1, 0, 0), (1, 1, 0))
    result = []
    for name, radius, overrides in (
        ("top_ring_covers_both_lower_rings", .5, {}),
        ("second_ring_over_third", .3, {}),
        ("third_ring_only", .15, {}),
        ("base_outside_all_rings", .95, {}),
        ("unlinked_top_shader_with_white_mask_is_native_black", .5,
         {"colors": (None, colors[1], colors[2])}),
        ("connected_black_top_shader_covers", .5, {"colors": ((0, 0, 0), colors[1], colors[2])}),
        ("soft_top_half_blends_second", .425, {"rings": ((.4, .4, .05), rings[1], rings[2])}),
        ("zero_width_top_keeps_second", .5, {"rings": ((.4, 0, 0), rings[1], rings[2])}),
        ("all_unused_masks_zero_keep_base", .5,
         {"rings": ((.4, 0, 0), (.25, 0, 0), (.1, 0, 0)), "colors": (None, None, None)}),
    ):
        params = overrides.get("rings", rings)
        shaders = overrides.get("colors", colors)
        uv = (.5 + radius/2, .5)
        expected = (0, 0, 1)
        mask_values = [radial.oracle(uv, values) for values in params]
        for mask, color in reversed(list(zip(mask_values, shaders))):
            # A native unlinked shader socket evaluates to black. Bypass an
            # unused pair with Mask 0, rather than a hidden Python-driven gate.
            color = color if color is not None else (0, 0, 0)
            expected = tuple(lower*(1-mask) + top*mask for lower, top in zip(expected, color))
        result.append({"name": name, "radius": radius, "uv": uv, "rings": params,
                       "colors": shaders, "masks": mask_values, "expected_rgb": expected})
    return result


def tile(scene, group, ring_mask, helper, case, index):
    material = bpy.data.materials.new("Atlas " + case["name"])
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    node = tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    node.name = "Mixed Rings"
    helper.add_shader_slot(node)
    tree.links.new(shader(tree, "Blue Base", (0, 0, 1)), node.inputs["Base Shader"])
    temporary = shader(tree, "Temporary Shader", (1, 0, 1))
    for index_slot, (values, color) in enumerate(zip(case["rings"], case["colors"]), 1):
        mask = tree.nodes.new("ShaderNodeGroup")
        mask.node_tree = ring_mask
        mask.name = "Ring Mask {}".format(index_slot)
        for name, value in zip(radial.INPUT_NAMES, values):
            mask.inputs[name].default_value = value
        tree.links.new(mask.outputs["Mask"], node.inputs["Mask {}".format(index_slot)])
        # Save deliberately different shader links. After reopen, the native
        # graph is edited with all RR Helper callbacks unregistered.
        tree.links.new(temporary, node.inputs["Shader {}".format(index_slot)])
        if color is not None:
            shader(tree, "Shader {}".format(index_slot), color)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(node.outputs[0], output.inputs["Surface"])
    x, y = index % GRID, index // GRID
    mesh = bpy.data.meshes.new(case["name"])
    mesh.from_pydata([(x-.46, y-.46, 0), (x+.46, y-.46, 0),
                      (x+.46, y+.46, 0), (x-.46, y+.46, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in uv.data:
        loop.uv = case["uv"]
    mesh.materials.append(material)
    obj = bpy.data.objects.new(case["name"], mesh)
    scene.collection.objects.link(obj)


def edit_native_connections(all_cases, helper):
    assert not any(callback in getattr(bpy.app.handlers, name) for name, callback in helper._HANDLERS)
    for case in all_cases:
        tree = bpy.data.materials["Atlas " + case["name"]].node_tree
        node = tree.nodes["Mixed Rings"]
        for number, color in enumerate(case["colors"], 1):
            target = node.inputs["Shader {}".format(number)]
            for link in list(target.links):
                tree.links.remove(link)
            if color is not None:
                tree.links.new(tree.nodes["Shader {}".format(number)].outputs[0], target)
        assert helper.sync_tree(tree) == 0
        assert not any(item.name.startswith("_Connected") for item in node.inputs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--ring-mask", default=str(ROOT / "node_library/dependencies/Randy_Ring_Mask.blend"))
    parser.add_argument("--report", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Verify only in an isolated background Blender process.")
    asset, ring_path, report_path = map(lambda value: Path(value).resolve(),
                                       (args.asset, args.ring_mask, args.report))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "state": "started", "asset": str(asset),
              "asset_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
              "ring_mask_sha256": hashlib.sha256(ring_path.read_bytes()).hexdigest(),
              "source_dependencies_sha256": hashes(), "blender": bpy.app.version_string,
              "ui_search_tested": False, "render_engine": "Cycles CPU", "tests": []}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        helper = runtime()
        helper.register()
        group = append_group(asset, NAME)
        report["asset_metadata"] = metadata(group, helper)
        report["tests"].append({"name": "append_asset_and_validate_canonical_graph_catalog_interface", "passed": True})
        material_name, expected_links, expanded_slots = independent_expansion(group, helper)
        report["tests"].append({"name": "independent_instances_expand_only_one_keep_old_identifiers_links_values", "passed": True})
        report["tests"].append({"name": "dynamic_expansion_to_twenty_five_pairs_without_hidden_gates",
                                "passed": True, "pairs": expanded_slots})
        ring_mask = append_group(ring_path, "Ring Mask")
        radial.validate_interface(ring_mask)
        scene = bpy.context.scene
        all_cases = cases()
        for index, case in enumerate(all_cases):
            tile(scene, group, ring_mask, helper, case, index)
        radial.setup_render(scene)
        scene.camera.location = ((GRID-1)/2, (GRID-1)/2, 10)
        scene.camera.data.ortho_scale = GRID
        scene.render.resolution_x = scene.render.resolution_y = GRID * TILE_PIXELS
        fixture = report_path.with_name("Randy_Mix_Shaders_validation.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(fixture), check_existing=False)
        helper.unregister()
        bpy.ops.wm.open_mainfile(filepath=str(fixture), load_ui=False, use_scripts=False)
        metadata(bpy.data.node_groups[NAME], helper)
        assert links(bpy.data.materials[material_name].node_tree) == expected_links
        edit_native_connections(all_cases, helper)
        report["tests"].append({"name": "saved_native_groups_reopen_and_connections_edit_with_helper_unregistered",
                                "passed": True,
                                "fixture": str(fixture)})
        exr = report_path.with_name("Mix_Shaders_numeric_atlas.exr")
        scene = bpy.context.scene
        scene.render.filepath = str(exr)
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(str(exr), check_existing=False)
        width, height = image.size
        assert width == height == GRID * TILE_PIXELS
        pixels = list(image.pixels)
        max_error = 0
        for index, case in enumerate(all_cases):
            x = (index % GRID) * TILE_PIXELS + TILE_PIXELS//2
            y = (index // GRID) * TILE_PIXELS + TILE_PIXELS//2
            rgb = pixels[(y * width + x)*4:(y * width + x)*4+3]
            error = max(abs(actual-wanted) for actual, wanted in zip(rgb, case["expected_rgb"]))
            passed = all(math.isfinite(value) for value in rgb) and error <= TOLERANCE
            report["tests"].append(dict(case, rgb=rgb, max_error=error, passed=passed, pixel=[x, y]))
            max_error = max(max_error, error)
        preview = report_path.with_name("Mix_Shaders_numeric_atlas.png")
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_depth = "8"
        image.save_render(filepath=str(preview), scene=scene)
        report.update(shader_sample_count=len(all_cases), maximum_absolute_error=max_error,
                      render_exr=str(exr), render_preview=str(preview), tolerance=TOLERANCE)
        assert all(test["passed"] for test in report["tests"]), "Shader sample mismatch"
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == report["asset_sha256"]
        assert hashlib.sha256(ring_path.read_bytes()).hexdigest() == report["ring_mask_sha256"]
        assert hashes() == report["source_dependencies_sha256"], "Source changed during verification"
        report["tests"].append({"name": "source_asset_ring_mask_and_generator_dependencies_unchanged", "passed": True})
        report.update(passed=True, state="complete")
    except Exception as exc:
        report.update(state="failed", error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("MIX_SHADERS_VERIFICATION " + json.dumps({"passed": report["passed"],
              "tests": len(report["tests"]), "report": str(report_path)}))


if __name__ == "__main__":
    main()
