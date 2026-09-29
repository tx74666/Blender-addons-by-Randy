"""Verify the saved Ring Mask asset by rendering real shader output on a UV atlas.

Run in a separate factory-startup Blender process, never in the user's live file:
  blender --background --factory-startup --python verify_ring_mask.py -- \
      --asset PATH --report PATH

Each tile has constant UVs, making the sampled shader result independent of pixel
filtering. The independent oracle uses normalized two-dimensional UV radius.
Outputs (fixture, EXR, PNG, JSON) stay beside the requested verification report.
"""

import argparse
import hashlib
import json
import math
import sys
import traceback
from pathlib import Path

import bpy


ASSET_NAME = "Ring Mask"
INPUT_NAMES = (
    "Inner Radius",
    "Ring Width",
    "Edge Softness",
)
DEFAULTS = (0.6, 0.08, 0.0)
TOLERANCE = 0.0002
GRID_SIZE = 8
PIXELS_PER_TILE = 16


def oracle(uv, values):
    """Independent scalar definition; no production node-building helpers."""
    inner, width, softness = values
    inner = min(1.0, max(0.0, inner))
    width = max(0.0, width)
    radius = math.hypot((uv[0] - 0.5) * 2, (uv[1] - 0.5) * 2)
    if width <= 0 or radius > 1:
        return 0.0
    outer = inner + width
    distance = outer - radius
    if inner > 0:
        distance = min(distance, radius - inner)
    softness = min(max(0.0, softness), width / 2)
    if softness == 0:
        return float(distance >= 0)
    t = min(1.0, max(0.0, distance / max(softness, 1e-8)))
    return t * t * (3.0 - 2.0 * t)


def cases():
    result = []

    def add(name, radius=None, values=None, *, uv=None, linked=False):
        uv = tuple(uv if uv is not None else (0.5 + radius / 2, 0.5))
        params = tuple(values) if values is not None else DEFAULTS
        result.append({
            "name": name,
            "uv": uv,
            "values": params,
            "use_defaults": values is None,
            "linked_parameters": linked,
            "expected": oracle(uv, params),
        })

    add("default_center_empty", 0)
    add("default_ring_middle_white", 0.65)
    add("default_inside_hole", 0.5)
    add("default_beyond_ring", 0.8)
    add("default_off_axis_same_radius", uv=(0.6875, 0.75))
    add("default_cardinal_same_radius", 0.625)
    add("default_left", uv=(0.175, 0.5))
    add("default_top", uv=(0.5, 0.825))
    add("default_bottom", uv=(0.5, 0.175))
    hard = (0.25, 0.5, 0)
    for name, r in (("inner_inclusive", 0.25), ("outer_inclusive", 0.75),
                    ("just_inside_hole", 0.249), ("just_beyond_outer", 0.751),
                    ("hard_middle", 0.5)):
        add(name, r, hard)
    for name, uv in (("rim_right", (1, 0.5)), ("rim_left", (0, 0.5)),
                     ("rim_top", (0.5, 1)), ("rim_bottom", (0.5, 0)),
                     ("outside_disk_corner", (1, 1)),
                     ("outside_disk_inside_uv_square", (0.95, 0.95)),
                     ("outside_uv_right", (1.001, 0.5)),
                     ("outside_uv_left", (-0.001, 0.5))):
        add(name, values=(0.75, 0.5, 0), uv=uv)
    add("overshoot_no_artificial_fade_at_disk_rim", 1, (0.75, 0.5, 0.125))
    add("zero_width_at_inner", 0.5, (0.5, 0, 0))
    add("zero_width_at_center_soft", 0, (0, 0, 0.1))
    add("negative_linked_width", 0.5, (0.5, -0.25, 0), linked=True)
    add("negative_linked_inner_clamps_to_zero", 0, (-0.5, 0.5, 0), linked=True)
    add("negative_linked_inner_clamps_not_absolute", 0.25, (-0.5, 0.5, 0), linked=True)
    add("negative_linked_softness_is_hard", 0.25, (0.25, 0.5, -0.5), linked=True)
    add("high_linked_inner_clamps_to_one_rim", 1, (2, 0.5, 0), linked=True)
    add("high_linked_inner_empty_interior", 0.999, (2, 0.5, 0), linked=True)
    add("high_linked_inner_clipped_exterior", 1.001, (2, 0.5, 0), linked=True)
    add("zero_inner_hard_center_filled", 0, (0, 0.5, 0))
    soft = (0.25, 0.5, 0.125)
    for name, r in (("soft_inner_edge_zero", 0.25),
                    ("soft_inner_quarter", 0.28125),
                    ("soft_inner_half", 0.3125),
                    ("soft_inner_plateau", 0.375),
                    ("soft_outer_plateau", 0.625),
                    ("soft_outer_half", 0.6875),
                    ("soft_outer_quarter", 0.71875),
                    ("soft_outer_edge_zero", 0.75),
                    ("soft_hole_empty", 0.2),
                    ("soft_exterior_empty", 0.8)):
        add(name, r, soft)
    narrow = (0.5, 0.0625, 0.125)
    add("narrow_ring_clamps_softness_peak_white", 0.53125, narrow)
    add("narrow_ring_inner_half_fade", 0.515625, narrow)
    add("narrow_ring_outer_half_fade", 0.546875, narrow)
    add("narrow_ring_inner_edge_zero", 0.5, narrow)
    add("narrow_ring_outer_edge_zero", 0.5625, narrow)
    add("zero_inner_soft_center_has_no_pinhole", 0, (0, 0.5, 0.125))
    add("zero_inner_soft_inner_region_filled", 0.25, (0, 0.5, 0.125))
    add("zero_inner_soft_outer_half", 0.4375, (0, 0.5, 0.125))
    add("zero_inner_soft_outer_edge_zero", 0.5, (0, 0.5, 0.125))
    add("large_linked_width_and_softness_center", 0, (0, 8, 4), linked=True)
    add("large_linked_width_and_softness_rim", 1, (0, 8, 4), linked=True)
    add("large_linked_width_still_clips_disk", 1.01, (0, 8, 4), linked=True)
    add("instance_a_white", 0.375, (0.25, 0.25, 0))
    add("instance_b_same_uv_black", 0.375, (0.625, 0.25, 0))
    add("same_material_instances_red_ring", 0.375, (0.25, 0.25, 0))
    result[-1].update({"independent_instances": True, "expected_rgb": [1, 0, 0]})
    add("same_material_instances_green_ring", 0.75, (0.25, 0.25, 0))
    result[-1].update({"independent_instances": True, "expected_rgb": [0, 1, 0]})
    assert len(result) <= GRID_SIZE ** 2
    return result


def validate_interface(group):
    assert group.bl_idname == "ShaderNodeTree", group.bl_idname
    assert group.asset_data is not None, "Group must be marked as an asset"
    assert group.asset_data.author == "Randy"
    assert group.asset_data.catalog_id == "cc7f1f5b-84b2-4f62-93e3-d05ed982a2c2"
    sockets = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    inputs = [item for item in sockets if item.in_out == "INPUT"]
    outputs = [item for item in sockets if item.in_out == "OUTPUT"]
    assert [item.name for item in inputs] == list(INPUT_NAMES)
    assert len(outputs) == 1 and outputs[0].name == "Mask"
    assert all(item.socket_type == "NodeSocketFloat" for item in sockets)
    assert all(abs(item.default_value - expected) < 1e-6 for item, expected in zip(inputs, DEFAULTS))
    assert inputs[0].min_value == 0 and inputs[0].max_value == 1
    assert all(item.min_value == 0 for item in inputs)
    assert outputs[0].min_value == 0 and outputs[0].max_value == 1
    assert not group.library
    assert getattr(group, "default_group_node_width", None) == 300, "Controls require a readable initial group width"
    assert not any(node.type == "GROUP" for node in group.nodes), "Asset should need no third-party node group"
    assert not any(node.bl_idname in {"ShaderNodeTexImage", "ShaderNodeScript", "ShaderNodeAttribute", "ShaderNodeNewGeometry"} for node in group.nodes)
    coordinates = [node for node in group.nodes if node.bl_idname == "ShaderNodeTexCoord"]
    assert coordinates, "Internal UV coordinates make the asset usable immediately after adding it"
    for node in coordinates:
        links = [link for link in group.links if link.from_node == node]
        assert links and all(link.from_socket.name == "UV" for link in links)
    return {
        "name": group.name,
        "catalog_id": group.asset_data.catalog_id,
        "inputs": [item.name for item in inputs],
        "outputs": [item.name for item in outputs],
        "nodes": len(group.nodes),
        "default_group_node_width": getattr(group, "default_group_node_width", None),
        "tags": [tag.name for tag in group.asset_data.tags],
    }


def add_tile(group, case, index):
    x, y = index % GRID_SIZE, index // GRID_SIZE
    mesh = bpy.data.meshes.new("UV " + case["name"])
    mesh.from_pydata([(x - 0.46, y - 0.46, 0), (x + 0.46, y - 0.46, 0),
                     (x + 0.46, y + 0.46, 0), (x - 0.46, y + 0.46, 0)], [], [(0, 1, 2, 3)])
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop in uv_layer.data:
        loop.uv = case["uv"]
    obj = bpy.data.objects.new(case["name"], mesh)
    bpy.context.collection.objects.link(obj)
    # Deliberately vary object depth without changing its apparent geometry:
    # UV output should still depend solely on the assigned UV values.
    obj.location = (0, 0, -index * 0.001)
    material = bpy.data.materials.new("Mask " + case["name"])
    material.use_nodes = True
    material.node_tree.nodes.clear()
    tree = material.node_tree
    instance = tree.nodes.new("ShaderNodeGroup")
    instance.node_tree = group
    if not case["use_defaults"]:
        for name, value in zip(INPUT_NAMES, case["values"]):
            if case["linked_parameters"]:
                external = tree.nodes.new("ShaderNodeValue")
                external.outputs[0].default_value = value
                tree.links.new(external.outputs[0], instance.inputs[name])
            else:
                instance.inputs[name].default_value = value
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    if case.get("independent_instances"):
        second = tree.nodes.new("ShaderNodeGroup")
        second.node_tree = group
        for name, value in zip(INPUT_NAMES, (0.625, 0.25, 0)):
            second.inputs[name].default_value = value
        combine = tree.nodes.new("ShaderNodeCombineColor")
        combine.mode = "RGB"
        tree.links.new(instance.outputs["Mask"], combine.inputs["Red"])
        tree.links.new(second.outputs["Mask"], combine.inputs["Green"])
        combine.inputs["Blue"].default_value = 0
        tree.links.new(combine.outputs[0], emission.inputs["Color"])
    else:
        tree.links.new(instance.outputs["Mask"], emission.inputs["Color"])
    tree.links.new(emission.outputs[0], output.inputs["Surface"])
    obj.data.materials.append(material)
    return material


def add_independent_instance_check(group):
    """Exercise two differently configured instances in the same material."""
    material = bpy.data.materials.new("Independent instances in same material")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    first = nodes.new("ShaderNodeGroup")
    second = nodes.new("ShaderNodeGroup")
    first.node_tree = group
    second.node_tree = group
    first.inputs[INPUT_NAMES[0]].default_value = 0.25
    second.inputs[INPUT_NAMES[0]].default_value = 0.75
    first.inputs[INPUT_NAMES[1]].default_value = 0.125
    assert second.inputs[INPUT_NAMES[1]].default_value == group.interface.items_tree[INPUT_NAMES[1]].default_value
    assert second.inputs[INPUT_NAMES[0]].default_value == 0.75
    assert first.node_tree == second.node_tree


def setup_render(scene):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = 0
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 2
    scene.render.resolution_x = GRID_SIZE * PIXELS_PER_TILE
    scene.render.resolution_y = GRID_SIZE * PIXELS_PER_TILE
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "32"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    world = bpy.data.worlds.new("Black verification background")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0, 0, 0, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0
    scene.world = world
    camera_data = bpy.data.cameras.new("UV atlas camera")
    camera = bpy.data.objects.new("UV atlas camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = ((GRID_SIZE - 1) / 2, (GRID_SIZE - 1) / 2, 10)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = GRID_SIZE
    scene.camera = camera


def render_preview(group, report_path):
    """Three real UV planes: thin, soft, then independently combined rings."""
    scene = bpy.data.scenes.new("Ring Mask three examples")
    setup_render(scene)
    scene.camera.location = (0, 0, 10)
    scene.camera.data.ortho_scale = 3.3
    scene.render.resolution_x = 600
    scene.render.resolution_y = 200
    scene.cycles.samples = 4
    examples = [
        ("Default thin ring", [(0.6, 0.08, 0)]),
        ("Wide ring with soft edges", [(0.4, 0.35, 0.07)]),
        ("Two independent concentric rings", [(0.36, 0.09, 0), (0.68, 0.09, 0)]),
    ]
    for index, (name, parameters) in enumerate(examples):
        x = (index - 1) * 1.1
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(x - 0.5, -0.5, 0), (x + 0.5, -0.5, 0),
                         (x + 0.5, 0.5, 0), (x - 0.5, 0.5, 0)], [], [(0, 1, 2, 3)])
        uv = mesh.uv_layers.new(name="UVMap")
        for loop, co in zip(uv.data, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            loop.uv = co
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()
        masks = []
        for params in parameters:
            instance = tree.nodes.new("ShaderNodeGroup")
            instance.node_tree = group
            for socket_name, value in zip(INPUT_NAMES, params):
                instance.inputs[socket_name].default_value = value
            masks.append(instance.outputs["Mask"])
        source = masks[0]
        if len(masks) > 1:
            combine = tree.nodes.new("ShaderNodeMath")
            combine.operation = "MAXIMUM"
            tree.links.new(masks[0], combine.inputs[0])
            tree.links.new(masks[1], combine.inputs[1])
            source = combine.outputs[0]
        emission = tree.nodes.new("ShaderNodeEmission")
        emission.inputs["Strength"].default_value = 1
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(source, emission.inputs["Color"])
        tree.links.new(emission.outputs[0], output.inputs["Surface"])
        mesh.materials.append(mat)
    preview_path = report_path.with_name("Ring_Mask_examples.png")
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = str(preview_path)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    # Save this preview as another self-contained verification fixture only.
    fixture = report_path.with_name("Randy_Ring_Mask_examples.blend")
    bpy.context.window.scene = scene
    bpy.ops.wm.save_as_mainfile(filepath=str(fixture), check_existing=False)
    return {"image": str(preview_path), "fixture": str(fixture),
            "left_to_right": [{"name": name, "instances": params} for name, params in examples]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    asset_path = Path(args.asset).resolve()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    asset_hash = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    report = {"passed": False, "state": "verification_started", "asset": str(asset_path),
              "asset_sha256": asset_hash, "blender": bpy.app.version_string,
              "ui_search_tested": False, "render_engine": "Cycles CPU",
              "tolerance": TOLERANCE, "tests": []}
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with bpy.data.libraries.load(str(asset_path), link=False) as (source, target):
            assert ASSET_NAME in source.node_groups, source.node_groups
            target.node_groups = [ASSET_NAME]
        group = bpy.data.node_groups[ASSET_NAME]
        report["asset_metadata"] = validate_interface(group)
        report["tests"].append({"name": "append_native_shader_asset_and_validate_interface", "passed": True})
        all_cases = cases()
        for index, case in enumerate(all_cases):
            add_tile(group, case, index)
        add_independent_instance_check(group)
        report["tests"].append({"name": "multiple_independent_instances_in_one_material", "passed": True})
        scene = bpy.context.scene
        setup_render(scene)
        fixture = report_path.with_name("Randy_Ring_Mask_validation.blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(fixture), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(fixture), load_ui=False, use_scripts=False)
        validate_interface(bpy.data.node_groups[ASSET_NAME])
        report["tests"].append({"name": "appended_asset_save_reopen", "passed": True, "fixture": str(fixture)})
        scene = bpy.context.scene
        exr = report_path.with_name("Ring_Mask_numeric_atlas.exr")
        scene.render.filepath = str(exr)
        bpy.ops.render.render(write_still=True)
        rendered = bpy.data.images.load(str(exr), check_existing=False)
        width, height = rendered.size
        pixels = list(rendered.pixels)
        assert width == height == GRID_SIZE * PIXELS_PER_TILE
        assert len(pixels) == width * height * 4
        max_error = 0
        failures = []
        for index, case in enumerate(all_cases):
            x = (index % GRID_SIZE) * PIXELS_PER_TILE + PIXELS_PER_TILE // 2
            y = (index // GRID_SIZE) * PIXELS_PER_TILE + PIXELS_PER_TILE // 2
            offset = (y * width + x) * 4
            rgb = pixels[offset:offset + 3]
            expected_rgb = case.get("expected_rgb", [case["expected"]] * 3)
            error = max(abs(channel - expected) for channel, expected in zip(rgb, expected_rgb))
            passed = all(math.isfinite(channel) and -TOLERANCE <= channel <= 1 + TOLERANCE for channel in rgb) and error <= TOLERANCE
            max_error = max(max_error, error)
            result = {**case, "rgb": rgb, "max_error": error, "passed": passed, "pixel": [x, y]}
            report["tests"].append(result)
            if not passed:
                failures.append(result)
        preview = report_path.with_name("Ring_Mask_numeric_atlas.png")
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_depth = "8"
        rendered.save_render(filepath=str(preview), scene=scene)
        report.update({"shader_sample_count": len(all_cases), "maximum_absolute_error": max_error,
                       "render_exr": str(exr), "render_preview": str(preview)})
        assert not failures, json.dumps(failures, ensure_ascii=False)
        assert hashlib.sha256(asset_path.read_bytes()).hexdigest() == asset_hash, "Verification changed source asset"
        report["tests"].append({"name": "source_asset_unchanged", "passed": True})
        report["examples"] = render_preview(bpy.data.node_groups[ASSET_NAME], report_path)
        report["passed"] = True
        report["state"] = "complete"
    except Exception as exc:
        report["state"] = "failed"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("RING_MASK_VERIFICATION " + json.dumps({"passed": report["passed"], "tests": len(report["tests"]), "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
