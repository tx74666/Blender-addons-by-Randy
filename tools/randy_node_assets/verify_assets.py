"""Independent geometry and persistence checks for the Randy starter assets.

Run in factory-startup Blender with -- --asset PATH --report PATH.
All outputs are confined to the report directory; no user preferences are saved.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy


RING = "Randy Ring 圆环"
PATTERN = "Randy Circular Pattern 环形阵列"
SOURCE_VERTICES = [(x, y, z) for x in (-0.2, 0.2) for y in (-0.1, 0.1) for z in (-0.05, 0.05)]
SOURCE_FACES = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]


def create_object(asset, name, values=None, realize=True):
    source = bpy.data.meshes.new(name + " source")
    if asset.name == PATTERN:
        source.from_pydata(SOURCE_VERTICES, [], SOURCE_FACES)
    obj = bpy.data.objects.new(name, source)
    bpy.context.collection.objects.link(obj)
    wrapper = bpy.data.node_groups.new(name + " validation wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    wrapper.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    wrapper.is_modifier = True
    inp = wrapper.nodes.new("NodeGroupInput")
    out = wrapper.nodes.new("NodeGroupOutput")
    group = wrapper.nodes.new("GeometryNodeGroup")
    group.node_tree = asset
    if asset.name == PATTERN:
        wrapper.links.new(inp.outputs["Geometry"], group.inputs["Geometry 几何"])
    for key, value in (values or {}).items():
        group.inputs[key].default_value = value
    if realize:
        node = wrapper.nodes.new("GeometryNodeRealizeInstances")
        wrapper.links.new(group.outputs["Geometry 几何"], node.inputs["Geometry"])
        wrapper.links.new(node.outputs["Geometry"], out.inputs["Geometry"])
    else:
        wrapper.links.new(group.outputs["Geometry 几何"], out.inputs["Geometry"])
    mod = obj.modifiers.new("Randy asset verification", "NODES")
    mod.node_group = wrapper
    bpy.context.view_layer.update()
    return obj


def evaluated(obj):
    graph = bpy.context.evaluated_depsgraph_get()
    value = obj.evaluated_get(graph)
    mesh = value.to_mesh()
    try:
        vertices = [tuple(v.co) for v in mesh.vertices]
        assert all(math.isfinite(x) for point in vertices for x in point)
        return {
            "vertices": vertices,
            "vertex_count": len(vertices),
            "face_count": len(mesh.polygons),
            "bounds": [[min(v[i] for v in vertices), max(v[i] for v in vertices)] for i in range(3)] if vertices else None,
            "all_smooth": bool(mesh.polygons) and all(p.use_smooth for p in mesh.polygons),
            "materials": [m.name for m in mesh.materials if m],
        }
    finally:
        value.to_mesh_clear()


def direct_modifier_object(asset, name, values=None):
    mesh = bpy.data.meshes.new(name + " source")
    if asset.name == PATTERN:
        mesh.from_pydata(SOURCE_VERTICES, [], SOURCE_FACES)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    modifier = obj.modifiers.new("Direct asset modifier", "NODES")
    modifier.node_group = asset
    sockets = {item.name: item.identifier for item in asset.interface.items_tree if item.item_type == "SOCKET" and item.in_out == "INPUT"}
    for name, value in (values or {}).items():
        # Blender 5.2 uses typed modifier properties; 5.1 and earlier used ID properties.
        if hasattr(modifier, "properties"):
            getattr(modifier.properties.inputs, sockets[name]).value = value
        else:
            modifier[sockets[name]] = value
    obj.update_tag()
    bpy.context.view_layer.update()
    return obj


def pattern_expected(count, radius, angle_offset=0, outward=True, rotation_offset=0, scale=(1, 1, 1)):
    result = []
    for index in range(count):
        angle = index * math.tau / count + angle_offset
        rotation = (angle if outward else 0) + rotation_offset
        for source in SOURCE_VERTICES:
            x, y, z = [source[i] * scale[i] for i in range(3)]
            result.append((radius * math.cos(angle) + x * math.cos(rotation) - y * math.sin(rotation), radius * math.sin(angle) + x * math.sin(rotation) + y * math.cos(rotation), z))
    return result


def assert_points_match(actual, expected):
    assert len(actual) == len(expected), (len(actual), len(expected))
    # Hash buckets with a small tolerance instead of relying on Blender vertex ordering.
    rounded = lambda point: tuple(round(x, 4) for x in point)
    assert sorted(map(rounded, actual)) == sorted(map(rounded, expected)), "Evaluated copy positions or rotations differ"


def check_bounds(actual, expected):
    for axis, target in zip(actual, expected):
        assert all(abs(a - b) < 0.0001 for a, b in zip(axis, target)), (actual, expected)


def brief(data):
    return {key: value for key, value in data.items() if key != "vertices"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    asset_path = Path(args.asset).resolve()
    report_path = Path(args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"passed": False, "state": "verification_started"}), encoding="utf-8")
    report = {"blender": bpy.app.version_string, "blender_hash": bpy.app.build_hash.decode(), "asset_file": str(asset_path), "asset_sha256": hashlib.sha256(asset_path.read_bytes()).hexdigest(), "ui_search_tested": False, "tests": []}
    bpy.ops.wm.open_mainfile(filepath=str(asset_path), load_ui=False, use_scripts=False)
    assert not bpy.data.objects, "Asset bundle must not contain user scene objects"
    assets = [g for g in bpy.data.node_groups if g.asset_data]
    assert sorted(g.name for g in assets) == sorted((RING, PATTERN))
    report["assets"] = []
    for group in assets:
        assert group.is_modifier and not group.is_tool
        assert group.asset_data.author == "Randy"
        assert len(group.name.encode("utf-8")) <= 63
        assert not group.library and all(not n.node_tree for n in group.nodes if n.type == "GROUP"), "Must use only native nodes"
        report["assets"].append({"name": group.name, "catalog_id": group.asset_data.catalog_id, "is_modifier": group.is_modifier, "is_tool": group.is_tool, "name_utf8_bytes": len(group.name.encode("utf-8")), "tags": [t.name for t in group.asset_data.tags], "sockets": [{"name": s.name, "direction": s.in_out, "type": s.socket_type} for s in group.interface.items_tree if s.item_type == "SOCKET"]})
    report["tests"].append({"name": "saved_bundle_reopened", "passed": True})
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(str(asset_path), link=False) as (source, target):
        assert RING in source.node_groups and PATTERN in source.node_groups
        target.node_groups = [RING, PATTERN]
    ring, pattern = bpy.data.node_groups[RING], bpy.data.node_groups[PATTERN]
    ring_default = create_object(ring, "Ring default")
    data = evaluated(ring_default)
    assert data["vertex_count"] == 64 * 16 and data["face_count"] == 64 * 16
    assert data["all_smooth"]
    check_bounds(data["bounds"], [[-1.1, 1.1], [-1.1, 1.1], [-0.1, 0.1]])
    report["tests"].append({"name": "ring_default", "passed": True, **brief(data)})
    material = bpy.data.materials.new("Verification material")
    ring_alternate = create_object(ring, "Ring alternate", {"Radius 半径": 2.0, "Tube Radius 管半径": 0.2, "Segments 环分段": 16, "Sides 截面分段": 8, "Smooth 平滑": False, "Material 材质": material})
    data = evaluated(ring_alternate)
    assert data["vertex_count"] == 128 and data["face_count"] == 128 and not data["all_smooth"]
    assert data["materials"] == [material.name]
    check_bounds(data["bounds"], [[-2.2, 2.2], [-2.2, 2.2], [-0.2, 0.2]])
    report["tests"].append({"name": "ring_parameters_material_smoothing", "passed": True, **brief(data)})
    pattern_default = create_object(pattern, "Pattern default")
    data = evaluated(pattern_default)
    assert_points_match(data["vertices"], pattern_expected(12, 2))
    assert data["face_count"] == 12 * 6
    report["tests"].append({"name": "pattern_default_realized_by_downstream_node", "passed": True, **brief(data)})
    pattern_alt = create_object(pattern, "Pattern alternate", {"Count 数量": 5, "Radius 半径": 3.0, "Angle Offset 起始角": math.pi / 6, "Outward 朝外": False, "Rotation Offset 自转角": math.pi / 2, "Scale 缩放": (0.5, 2.0, 1.5), "Realize 实体化": True}, realize=False)
    data = evaluated(pattern_alt)
    assert_points_match(data["vertices"], pattern_expected(5, 3, math.pi / 6, False, math.pi / 2, (0.5, 2, 1.5)))
    assert data["face_count"] == 30
    report["tests"].append({"name": "pattern_count_radius_angle_orientation_scale_realize", "passed": True, **brief(data)})
    single = create_object(pattern, "Single copy", {"Count 数量": 1, "Radius 半径": 0.0, "Realize 实体化": True}, realize=False)
    data = evaluated(single)
    assert_points_match(data["vertices"], SOURCE_VERTICES)
    report["tests"].append({"name": "pattern_single_copy_zero_radius", "passed": True, **brief(data)})
    lightweight = create_object(pattern, "Lightweight instances", realize=False)
    graph = bpy.context.evaluated_depsgraph_get()
    instances = [item for item in graph.object_instances if item.is_instance and item.parent and item.parent.original == lightweight]
    assert len(instances) == 12, f"Expected 12 light instances, got {len(instances)}"
    assert evaluated(lightweight)["vertex_count"] == 0, "Unrealized default should not allocate a full mesh"
    report["tests"].append({"name": "pattern_default_keeps_12_instances", "passed": True, "instance_count": len(instances)})
    direct_ring = direct_modifier_object(ring, "Direct ring modifier")
    assert evaluated(direct_ring)["vertex_count"] == 1024
    direct_pattern = direct_modifier_object(pattern, "Direct pattern modifier", {"Count 数量": 4, "Realize 实体化": True})
    assert_points_match(evaluated(direct_pattern)["vertices"], pattern_expected(4, 2))
    report["tests"].append({"name": "direct_modifiers_and_socket_values", "passed": True, "ring_vertices": 1024, "pattern_vertices": 32})
    fixture = report_path.with_name("Randy_validation_roundtrip.blend")
    bpy.ops.wm.save_as_mainfile(filepath=str(fixture), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(fixture), load_ui=False, use_scripts=False)
    assert evaluated(bpy.data.objects["Ring default"])["vertex_count"] == 1024
    data = evaluated(bpy.data.objects["Pattern alternate"])
    assert_points_match(data["vertices"], pattern_expected(5, 3, math.pi / 6, False, math.pi / 2, (0.5, 2, 1.5)))
    report["tests"].append({"name": "blank_file_append_save_reopen_evaluate", "passed": True, "fixture": str(fixture)})
    report["passed"] = True
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RANDY_VERIFICATION " + json.dumps({"passed": True, "test_count": len(report["tests"]), "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
