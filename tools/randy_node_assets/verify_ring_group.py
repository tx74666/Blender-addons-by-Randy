"""Verify native Ring Group expansion and real Ring / Arc shader composition.

The saved asset is appended from disk. Optional expansion API calls are made
without registering RR Helper. After save/reopen, native shader output is
rendered with no RR Helper handler or operator registration. Three independent
mask generators share one external shader and combine by Maximum, not addition
or repeated mixing of the same shader.
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
from build_ring_group import CATALOG_ID, DESCRIPTION, GENERATOR, NAME, ROOT, VERSION, runtime
import verify_ring_mask as radial
import verify_arc_mask as arc


DEPENDENCIES = (GENERATOR, "tools/randy_node_assets/build_ring_group.py",
                "tools/randy_node_assets/verify_ring_group.py", "tools/randy_node_assets/verify_ring_mask.py",
                "tools/randy_node_assets/verify_arc_mask.py", "tools/randy_node_assets/build_arc_mask.py")
TOLERANCE = .002
GRID = 5
TILE_PIXELS = 16


def hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for path in DEPENDENCIES}


def append_group(path, name):
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        assert name in source.node_groups, source.node_groups
        target.node_groups = [name]
    return target.node_groups[0]


def disabled_helper(helper):
    assert bpy.context.preferences.addons.get("random_realm_builder_exporter") is None
    assert not helper._MENU_REGISTERED
    assert all(not getattr(cls, "is_registered", False) for cls in helper.CLASSES)
    callbacks = []
    for name in dir(bpy.app.handlers):
        functions = getattr(bpy.app.handlers, name)
        if isinstance(functions, list):
            callbacks.extend((name, getattr(function, "__name__", repr(function))) for function in functions
                             if getattr(function, "__module__", "").startswith("random_realm_builder_exporter"))
    assert not callbacks, callbacks
    return {"addon_enabled": False, "operators_registered": False, "rr_helper_handlers": callbacks}


def metadata(group, helper):
    assert group.name == NAME and group.bl_idname == "ShaderNodeTree"
    assert group.library is None and group.color_tag == "SHADER"
    assert group.asset_data and group.asset_data.author == "Randy"
    assert group.asset_data.catalog_id == CATALOG_ID
    assert group.asset_data.description == DESCRIPTION
    assert group["randy_asset_version"] == VERSION
    assert group["randy_asset_generator"] == GENERATOR
    assert group["randy_asset_generator_sha256"] == hashes()[GENERATOR]
    masks = helper._validate_group(group)
    assert len(masks) == 2
    items = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    assert [item.name for item in items if item.in_out == "INPUT"] == ["Shader", "Mask 1", "Mask 2"]
    assert [item.name for item in items if item.in_out == "OUTPUT"] == ["Mask", "Shader"]
    assert {node.bl_idname for node in group.nodes} == {"NodeGroupInput", "NodeGroupOutput", "ShaderNodeMath"}
    assert all(node.operation == "MAXIMUM" and node.use_clamp
               for node in group.nodes if node.bl_idname == "ShaderNodeMath")
    expected = helper.new_group(mask_count=2)
    try:
        assert helper._graph_signature(group) == helper._graph_signature(expected)
    finally:
        bpy.data.node_groups.remove(expected)
    return {"name": NAME, "version": VERSION, "catalog_id": CATALOG_ID,
            "color_tag": "SHADER", "inputs": ["Shader", "Mask 1", "Mask 2"],
            "outputs": ["Mask", "Shader"], "nodes": len(group.nodes),
            "mask_operation": "clamped Maximum", "shader_count": 1}


def links(tree):
    return sorted((link.from_node.name, link.from_socket.identifier,
                   link.to_node.name, link.to_socket.identifier) for link in tree.links)


def shader(tree, name, color):
    node = tree.nodes.new("ShaderNodeEmission")
    node.name = name
    node.inputs["Color"].default_value = (*color, 1)
    node.inputs["Strength"].default_value = 1
    return node.outputs[0]


def expansion(group, helper):
    material = bpy.data.materials.new("24 masks and independent asset instances")
    material.use_nodes = True
    material.use_fake_user = True
    tree = material.node_tree
    tree.nodes.clear()
    first = tree.nodes.new("ShaderNodeGroup")
    second = tree.nodes.new("ShaderNodeGroup")
    first.node_tree = second.node_tree = group
    overlay = shader(tree, "One shared shader", (1, 0, 0))
    tree.links.new(overlay, first.inputs["Shader"])
    external = tree.nodes.new("ShaderNodeValue")
    external.outputs[0].default_value = .37
    tree.links.new(external.outputs[0], first.inputs["Mask 1"])
    first.inputs["Mask 2"].default_value = .21
    second.inputs["Mask 1"].default_value = .81
    mix = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(first.outputs["Mask"], mix.inputs[0])
    tree.links.new(shader(tree, "Original base", (0, 0, 1)), mix.inputs[1])
    tree.links.new(first.outputs["Shader"], mix.inputs[2])
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(mix.outputs[0], output.inputs["Surface"])
    before = links(tree)
    identifiers = [item.identifier for item in first.inputs]
    second_pointer = second.node_tree.as_pointer()
    for count in range(3, 25):
        assert helper.add_mask_slot(first) == count
        assert len(helper._mask_ids(first.node_tree)) == count
        assert len(helper._mask_ids(second.node_tree)) == 2
        assert second.node_tree.as_pointer() == second_pointer
        assert links(tree) == before
        assert [item.identifier for item in first.inputs][:len(identifiers)] == identifiers
        assert abs(first.inputs["Mask 2"].default_value - .21) < 1e-6
        assert abs(second.inputs["Mask 1"].default_value - .81) < 1e-6
    assert first.node_tree.asset_data is None and not first.node_tree.use_fake_user
    assert second.node_tree == group and group.asset_data is not None
    disabled_helper(helper)
    return material.name, before


def cases():
    result = []
    rings = ((.2, .15, 0), (.45, .15, 0))
    arc_values = (.7, .1, 0, 20, 180)

    def add(name, radius=.5, angle=0, *, ring_values=rings, arc_parameters=arc_values,
            overrides=None, slot_count=3, extra_mask=0):
        radians = math.radians(angle)
        uv = (.5 + radius * math.cos(radians) / 2, .5 + radius * math.sin(radians) / 2)
        masks = [radial.oracle(uv, values) for values in ring_values]
        masks.append(arc.oracle(uv, arc_parameters))
        if overrides is not None:
            masks = list(overrides)
        if slot_count > 3:
            masks.extend([0] * (slot_count - 4) + [extra_mask])
        coverage = min(1.0, max(0.0, max(masks)))
        result.append({"name": name, "uv": uv, "rings": ring_values, "arc": arc_parameters,
                       "overrides": overrides, "slot_count": slot_count, "extra_mask": extra_mask,
                       "masks": masks, "coverage": coverage, "expected_rgb": [coverage, 0, 1 - coverage]})

    add("first_ring_only", .25)
    add("second_ring_only", .5)
    add("arc_only", .75, 90)
    add("arc_cut_exposes_base", .75, 270)
    add("outside_all_masks", .95, 90)
    add("radial_hole_base", 0)
    soft = (.25, .5, .125)
    add("three_half_soft_masks_union_is_half_not_sum_or_repeated_mix", .3125, 90,
        ring_values=(soft, soft), arc_parameters=(*soft, 0, 180))
    add("three_quarter_edge_masks_union_is_015625", .28125, 90,
        ring_values=(soft, soft), arc_parameters=(*soft, 0, 180))
    add("soft_arc_outside_angle_keeps_ring_half", .3125, 270,
        ring_values=(soft, (.7, .1, 0)), arc_parameters=(*soft, 0, 180))
    add("arc_wrap_before_seam", .75, 330, arc_parameters=(.7, .1, 0, 300, 120))
    add("arc_wrap_after_seam", .75, 30, arc_parameters=(.7, .1, 0, 300, 120))
    add("zero_arc_keeps_base", .75, 90, arc_parameters=(.7, .1, 0, 0, 0))
    add("full_arc_covers_bottom", .75, 270, arc_parameters=(.7, .1, 0, 40, 360))
    add("zero_width_all_masks_keeps_base", .5, 90,
        ring_values=((.2, 0, 0), (.45, 0, 0)), arc_parameters=(.7, 0, 0, 0, 180))
    add("high_external_mask_clamped_to_one", overrides=(-.8, 1.4, .4))
    add("negative_external_masks_clamped_to_zero", overrides=(-.8, -.4, -.2))
    add("24_mask_slots_include_last_input", .95, slot_count=24, extra_mask=.8)
    assert len(result) <= GRID * GRID
    return result


def tile(scene, group, ring_mask, arc_mask, helper, case, index):
    material = bpy.data.materials.new("Atlas " + case["name"])
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    node = tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    for _ in range(2, case["slot_count"]):
        helper.add_mask_slot(node)
    overlay = shader(tree, "One overlay shader for every Ring and Arc", (1, 0, 0))
    tree.links.new(overlay, node.inputs["Shader"])
    for number, values in enumerate(case["rings"], 1):
        mask = tree.nodes.new("ShaderNodeGroup")
        mask.name = "Independent Ring Mask {}".format(number)
        mask.node_tree = ring_mask
        for name, value in zip(radial.INPUT_NAMES, values):
            mask.inputs[name].default_value = value
        tree.links.new(mask.outputs["Mask"], node.inputs["Mask {}".format(number)])
    arc_instance = tree.nodes.new("ShaderNodeGroup")
    arc_instance.name = "Independent Arc Mask 3"
    arc_instance.node_tree = arc_mask
    for name, value in zip(arc.INPUT_NAMES, case["arc"]):
        arc_instance.inputs[name].default_value = value
    tree.links.new(arc_instance.outputs["Mask"], node.inputs["Mask 3"])
    if case["overrides"] is not None:
        for number, value in enumerate(case["overrides"], 1):
            external = tree.nodes.new("ShaderNodeValue")
            external.outputs[0].default_value = value
            tree.links.new(external.outputs[0], node.inputs["Mask {}".format(number)])
    if case["slot_count"] > 3:
        node.inputs["Mask {}".format(case["slot_count"])].default_value = case["extra_mask"]
    base = shader(tree, "Original blue Base Shader", (0, 0, 1))
    mix = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(node.outputs["Mask"], mix.inputs[0])
    tree.links.new(base, mix.inputs[1])
    tree.links.new(node.outputs["Shader"], mix.inputs[2])
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(mix.outputs[0], output.inputs["Surface"])
    # Exactly one overlay shader exists, outside Ring Group and its mask nodes.
    assert len([item for item in tree.nodes if item.bl_idname == "ShaderNodeEmission"]) == 2
    assert not any(item.bl_idname in {"ShaderNodeEmission", "ShaderNodeMixShader", "ShaderNodeBsdfPrincipled"}
                   for item in node.node_tree.nodes)
    x, y = index % GRID, index // GRID
    mesh = bpy.data.meshes.new(case["name"])
    mesh.from_pydata([(x - .46, y - .46, 0), (x + .46, y - .46, 0),
                     (x + .46, y + .46, 0), (x - .46, y + .46, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in uv.data:
        loop.uv = case["uv"]
    mesh.materials.append(material)
    obj = bpy.data.objects.new(case["name"], mesh)
    scene.collection.objects.link(obj)
    obj.location.z = -index * .001


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--ring-mask", default=str(ROOT / "node_library/dependencies/Randy_Ring_Mask.blend"))
    parser.add_argument("--arc-mask", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not bpy.app.background:
        raise RuntimeError("Verify only in an isolated background Blender process.")
    asset, ring_path, arc_path, report_path = map(lambda value: Path(value).resolve(),
                                               (args.asset, args.ring_mask, args.arc_mask, args.report))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    file_hashes = {label: hashlib.sha256(path.read_bytes()).hexdigest()
                   for label, path in (("asset", asset), ("ring_mask", ring_path), ("arc_mask", arc_path))}
    report = {"passed": False, "state": "started", "asset": str(asset),
              "asset_sha256": file_hashes["asset"], "ring_mask_sha256": file_hashes["ring_mask"],
              "arc_mask_sha256": file_hashes["arc_mask"], "source_dependencies_sha256": hashes(),
              "blender": bpy.app.version_string, "ui_search_tested": False,
              "render_engine": "Cycles CPU", "tests": []}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        helper = runtime()
        report["helper_state"] = disabled_helper(helper)
        group = append_group(asset, NAME)
        report["asset_metadata"] = metadata(group, helper)
        report["tests"].append({"name": "appended_native_asset_matches_canonical_union_shader_passthrough", "passed": True})
        material_name, expected_links = expansion(group, helper)
        report["tests"].append({"name": "expand_one_instance_to_24_preserving_old_shader_values_identifiers_links", "passed": True})
        ring_mask = append_group(ring_path, "Ring Mask")
        radial.validate_interface(ring_mask)
        arc_mask = append_group(arc_path, "Arc Mask")
        arc.validate(arc_mask, file_hashes["ring_mask"])
        scene = bpy.context.scene
        all_cases = cases()
        for index, case in enumerate(all_cases):
            tile(scene, group, ring_mask, arc_mask, helper, case, index)
        report["tests"].append({"name": "three_actual_shared_ring_arc_masks_use_one_external_overlay_shader", "passed": True})
        radial.setup_render(scene)
        scene.camera.location = ((GRID - 1) / 2, (GRID - 1) / 2, 10)
        scene.camera.data.ortho_scale = GRID
        scene.render.resolution_x = scene.render.resolution_y = GRID * TILE_PIXELS
        fixture = report_path.with_name("Randy_Ring_Group_validation.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(fixture), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(fixture), load_ui=False, use_scripts=False)
        metadata(bpy.data.node_groups[NAME], helper)
        assert links(bpy.data.materials[material_name].node_tree) == expected_links
        assert max(len(helper._mask_ids(item)) for item in bpy.data.node_groups if item.get("rr_ring_group_version") == 1) == 24
        report["tests"].append({"name": "native_saved_expanded_groups_reopen_with_existing_links_intact", "passed": True,
                                "fixture": str(fixture)})
        report["helper_state_before_render"] = disabled_helper(helper)
        scene = bpy.context.scene
        exr = report_path.with_name("Ring_Group_numeric_atlas.exr")
        scene.render.filepath = str(exr)
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(str(exr), check_existing=False)
        width, height = image.size
        assert width == height == GRID * TILE_PIXELS
        pixels = list(image.pixels)
        maximum = 0.0
        for index, case in enumerate(all_cases):
            x = index % GRID * TILE_PIXELS + TILE_PIXELS // 2
            y = index // GRID * TILE_PIXELS + TILE_PIXELS // 2
            rgb = pixels[(y * width + x) * 4:(y * width + x) * 4 + 3]
            error = max(abs(actual - expected) for actual, expected in zip(rgb, case["expected_rgb"]))
            passed = all(math.isfinite(channel) for channel in rgb) and error <= TOLERANCE
            report["tests"].append({**case, "rgb": rgb, "max_error": error, "passed": passed, "pixel": [x, y]})
            maximum = max(maximum, error)
        png = report_path.with_name("Ring_Group_numeric_atlas.png")
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_depth = "8"
        image.save_render(filepath=str(png), scene=scene)
        report.update({"shader_sample_count": len(all_cases), "maximum_absolute_error": maximum,
                       "render_exr": str(exr), "render_preview": str(png), "tolerance": TOLERANCE})
        assert all(test["passed"] for test in report["tests"]), "Native mask union shader sample mismatch."
        disabled_helper(helper)
        for label, path in (("asset", asset), ("ring_mask", ring_path), ("arc_mask", arc_path)):
            assert hashlib.sha256(path.read_bytes()).hexdigest() == file_hashes[label]
        assert hashes() == report["source_dependencies_sha256"], "Sources changed during verification."
        report["tests"].append({"name": "all_source_assets_and_dependencies_unchanged", "passed": True})
        report.update({"passed": True, "state": "complete"})
    except Exception as exc:
        report.update({"state": "failed", "error": str(exc), "traceback": traceback.format_exc()})
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("RING_GROUP_VERIFICATION " + json.dumps({"passed": report["passed"],
              "tests": len(report["tests"]), "report": str(report_path)}))


if __name__ == "__main__":
    main()
