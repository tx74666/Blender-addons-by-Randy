"""Build two native Geometry Nodes assets in a separate, explicitly named file.

Run with Blender --background --factory-startup --python this_file -- --output PATH.
The build never opens an existing user blend file or changes user preferences.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy


ASSETS = {
    "ring": {"name": "Randy Ring 圆环", "catalog": "Randy/Primitives", "uuid": "67c33df7-9a94-5d93-b9ca-21feef71eadb"},
    "pattern": {"name": "Randy Circular Pattern 环形阵列", "catalog": "Randy/Patterns", "uuid": "a1f8b514-e0ed-5fc8-9c14-12c5d9c0d57e"},
}


def socket(group, name, kind, *, direction="INPUT", default=None, minimum=None, maximum=None, description="", subtype=None):
    item = group.interface.new_socket(name, in_out=direction, socket_type=kind)
    item.description = description
    if default is not None:
        item.default_value = default
    if minimum is not None:
        item.min_value = minimum
    if maximum is not None:
        item.max_value = maximum
    if subtype is not None:
        item.subtype = subtype
    if hasattr(item, "force_non_field") and kind != "NodeSocketGeometry":
        item.force_non_field = True
    return item


def node(group, kind, label, x, y):
    result = group.nodes.new(kind)
    result.label = label
    result.location = (x, y)
    result.width = 190
    result.select = False
    return result


def wire(group, source, target):
    group.links.new(source, target)


def math_node(group, operation, a, b, label, x, y):
    result = node(group, "ShaderNodeMath", label, x, y)
    result.operation = operation
    for index, value in enumerate((a, b)):
        if isinstance(value, (int, float)):
            result.inputs[index].default_value = value
        elif value is not None:
            wire(group, value, result.inputs[index])
    return result.outputs[0]


def bounded(group, source, minimum, maximum, label, x, y):
    low = math_node(group, "MAXIMUM", source, minimum, label + " min", x, y)
    return math_node(group, "MINIMUM", low, maximum, label + " max", x + 220, y)


def new_group(key, description, tags):
    spec = ASSETS[key]
    assert len(spec["name"].encode("utf-8")) <= 63, "Keep names portable to older Blender ID limits"
    group = bpy.data.node_groups.new(spec["name"], "GeometryNodeTree")
    assert group.name == spec["name"]
    group.description = description
    group.is_modifier = True
    group.is_tool = False
    group.use_fake_user = True
    group.asset_mark()
    group.asset_data.author = "Randy"
    group.asset_data.description = description
    group.asset_data.catalog_id = spec["uuid"]
    for tag in ("Randy", "Geometry Nodes", "Native", *tags):
        group.asset_data.tags.new(tag)
    group["randy_asset_version"] = "0.1.0"
    group["randy_asset_source"] = "tools/randy_node_assets/build_assets.py"
    socket(group, "Geometry 几何", "NodeSocketGeometry", direction="OUTPUT")
    return group


def build_ring():
    group = new_group("ring", "Parametric torus in the local XY plane. Radius is the centerline radius; tube radius is capped at 99% of it. 原点为中心的参数圆环：半径为中心线半径，管半径最多为其99%。", ("Ring", "Torus", "圆环", "圆圈", "基础形状"))
    socket(group, "Radius 半径", "NodeSocketFloat", default=1.0, minimum=0.0001, maximum=1000.0, subtype="DISTANCE", description="Centerline radius, in local units. 中心线半径，局部坐标单位。")
    socket(group, "Tube Radius 管半径", "NodeSocketFloat", default=0.1, minimum=0.000001, maximum=1000.0, subtype="DISTANCE", description="Tube thickness as a radius; capped at 99% of Radius. 管截面半径，自动限制到主半径的99%。")
    socket(group, "Segments 环分段", "NodeSocketInt", default=64, minimum=3, maximum=512, description="Segments around the ring, clamped to 3–512. 圆环分段数，限制为3–512。")
    socket(group, "Sides 截面分段", "NodeSocketInt", default=16, minimum=3, maximum=128, description="Tube profile sides, clamped to 3–128. 管截面分段数，限制为3–128。")
    socket(group, "Smooth 平滑", "NodeSocketBool", default=True, description="Shade all faces smooth. 启用面平滑着色。")
    socket(group, "Material 材质", "NodeSocketMaterial", description="Optional material for the generated ring. 圆环材质，可留空。")
    inp = node(group, "NodeGroupInput", "Ring controls / 圆环参数", -1000, 300)
    out = node(group, "NodeGroupOutput", "Ring / 圆环", 950, 300)
    radius = bounded(group, inp.outputs["Radius 半径"], 0.0001, 1000, "Radius / 半径", -760, 500)
    tube_positive = math_node(group, "MAXIMUM", inp.outputs["Tube Radius 管半径"], 0.000001, "Tube minimum / 管半径下限", -540, 220)
    tube_maximum = math_node(group, "MULTIPLY", radius, 0.99, "Avoid self intersection / 限制相交", -310, 450)
    tube = math_node(group, "MINIMUM", tube_positive, tube_maximum, "Tube radius / 管半径", -80, 300)
    segments = bounded(group, inp.outputs["Segments 环分段"], 3, 512, "Ring segments / 环分段", -760, -80)
    sides = bounded(group, inp.outputs["Sides 截面分段"], 3, 128, "Tube sides / 截面分段", -760, -380)
    circle = node(group, "GeometryNodeCurvePrimitiveCircle", "Centerline / 中心线", -80, 700)
    profile = node(group, "GeometryNodeCurvePrimitiveCircle", "Tube profile / 管截面", 170, 150)
    for curve, resolution, curve_radius in ((circle, segments, radius), (profile, sides, tube)):
        curve.mode = "RADIUS"
        wire(group, resolution, curve.inputs["Resolution"])
        wire(group, curve_radius, curve.inputs["Radius"])
    mesh = node(group, "GeometryNodeCurveToMesh", "Sweep profile / 扫掠成环", 180, 700)
    wire(group, circle.outputs["Curve"], mesh.inputs["Curve"])
    wire(group, profile.outputs["Curve"], mesh.inputs["Profile Curve"])
    smooth = node(group, "GeometryNodeSetShadeSmooth", "Smooth shading / 平滑着色", 440, 700)
    smooth.domain = "FACE"
    wire(group, mesh.outputs["Mesh"], smooth.inputs["Geometry"])
    wire(group, inp.outputs["Smooth 平滑"], smooth.inputs["Shade Smooth"])
    material = node(group, "GeometryNodeSetMaterial", "Material / 材质", 690, 500)
    wire(group, smooth.outputs["Geometry"], material.inputs["Geometry"])
    wire(group, inp.outputs["Material 材质"], material.inputs["Material"])
    wire(group, material.outputs["Geometry"], out.inputs["Geometry 几何"])
    return group


def build_pattern():
    group = new_group("pattern", "Repeat input geometry in the local XY plane around the origin. Outward aligns the source +X axis radially; input geometry uses its local origin as pivot. Instances stay lightweight unless Realize is enabled. 将输入几何围绕原点环形排列；朝外使源几何+X轴沿径向，局部原点为支点。默认保留实例。", ("Circular", "Array", "Pattern", "Radial", "环形阵列", "图案", "圆周排列"))
    socket(group, "Geometry 几何", "NodeSocketGeometry", description="Shape to repeat; center its local geometry around its origin. 要重复的形状，请以其局部原点作为排列支点。")
    socket(group, "Count 数量", "NodeSocketInt", default=12, minimum=1, maximum=512, description="Number of copies, clamped to 1–512. 重复数量，限制为1–512。")
    socket(group, "Radius 半径", "NodeSocketFloat", default=2.0, minimum=0.0, maximum=1000.0, subtype="DISTANCE", description="Circle radius in local units. 圆周半径，局部坐标单位。")
    socket(group, "Angle Offset 起始角", "NodeSocketFloat", default=0.0, minimum=-math.tau, maximum=math.tau, subtype="ANGLE", description="Rotate the copy positions around local Z. 围绕局部Z轴旋转排列位置。")
    socket(group, "Outward 朝外", "NodeSocketBool", default=True, description="Align source +X radially outward; off preserves a common orientation. 源几何+X轴朝外；关闭时所有副本保持同向。")
    socket(group, "Rotation Offset 自转角", "NodeSocketFloat", default=0.0, minimum=-math.tau, maximum=math.tau, subtype="ANGLE", description="Additional local Z rotation of each copy. 每个副本额外绕局部Z轴旋转。")
    socket(group, "Scale 缩放", "NodeSocketVector", default=(1.0, 1.0, 1.0), minimum=0.0, maximum=100.0, subtype="XYZ", description="Per-copy XYZ scale, clamped to 0–100. 每份副本的XYZ缩放，限制为0–100。")
    socket(group, "Realize 实体化", "NodeSocketBool", default=False, description="Enable only when downstream mesh operations require realized geometry; dense input multiplies memory use. 后续网格操作需要时开启；高密度源几何会增加内存占用。")
    inp = node(group, "NodeGroupInput", "Pattern controls / 阵列参数", -1450, 300)
    out = node(group, "NodeGroupOutput", "Circular pattern / 环形阵列", 1900, 350)
    count = bounded(group, inp.outputs["Count 数量"], 1, 512, "Count / 数量", -1200, 700)
    radius = bounded(group, inp.outputs["Radius 半径"], 0, 1000, "Radius / 半径", -1200, 350)
    line = node(group, "GeometryNodeMeshLine", "One point per copy / 每份一个点", -700, 1050)
    line.mode = "OFFSET"
    line.inputs["Offset"].default_value = (0, 0, 0)
    wire(group, count, line.inputs["Count"])
    index = node(group, "GeometryNodeInputIndex", "Copy index / 副本序号", -950, -50)
    step = math_node(group, "DIVIDE", math.tau, count, "Angle step / 角步长", -700, 650)
    angle = math_node(group, "MULTIPLY", index.outputs[0], step, "Index angle / 序号角度", -450, 650)
    angle = math_node(group, "ADD", angle, inp.outputs["Angle Offset 起始角"], "Position angle / 位置角度", -210, 650)
    cosine = math_node(group, "COSINE", angle, None, "Cosine / 余弦", 50, 850)
    sine = math_node(group, "SINE", angle, None, "Sine / 正弦", 50, 600)
    x = math_node(group, "MULTIPLY", cosine, radius, "X", 290, 850)
    y = math_node(group, "MULTIPLY", sine, radius, "Y", 290, 600)
    position = node(group, "ShaderNodeCombineXYZ", "Circle position / 圆周位置", 550, 900)
    wire(group, x, position.inputs["X"])
    wire(group, y, position.inputs["Y"])
    points = node(group, "GeometryNodeSetPosition", "Place copies / 放置副本", 820, 1050)
    wire(group, line.outputs["Mesh"], points.inputs["Geometry"])
    wire(group, position.outputs[0], points.inputs["Position"])
    rotation = math_node(group, "MULTIPLY", angle, inp.outputs["Outward 朝外"], "Outward toggle / 朝外开关", 50, 220)
    rotation = math_node(group, "ADD", rotation, inp.outputs["Rotation Offset 自转角"], "Copy rotation / 副本旋转", 290, 220)
    euler = node(group, "ShaderNodeCombineXYZ", "Z rotation / Z轴旋转", 550, 320)
    wire(group, rotation, euler.inputs["Z"])
    rotation_node = node(group, "FunctionNodeEulerToRotation", "Euler to rotation / 欧拉角转旋转", 820, 420)
    wire(group, euler.outputs[0], rotation_node.inputs[0])
    scale_min = node(group, "ShaderNodeVectorMath", "Scale minimum / 缩放下限", -450, -300)
    scale_min.operation = "MAXIMUM"
    scale_min.inputs[1].default_value = (0, 0, 0)
    wire(group, inp.outputs["Scale 缩放"], scale_min.inputs[0])
    scale_max = node(group, "ShaderNodeVectorMath", "Scale maximum / 缩放上限", -210, -300)
    scale_max.operation = "MINIMUM"
    scale_max.inputs[1].default_value = (100, 100, 100)
    wire(group, scale_min.outputs[0], scale_max.inputs[0])
    instances = node(group, "GeometryNodeInstanceOnPoints", "Repeat input / 重复源几何", 1080, 850)
    wire(group, points.outputs["Geometry"], instances.inputs["Points"])
    wire(group, inp.outputs["Geometry 几何"], instances.inputs["Instance"])
    wire(group, rotation_node.outputs[0], instances.inputs["Rotation"])
    wire(group, scale_max.outputs[0], instances.inputs["Scale"])
    realize = node(group, "GeometryNodeRealizeInstances", "Optional realization / 可选实体化", 1350, 400)
    wire(group, instances.outputs["Instances"], realize.inputs["Geometry"])
    switch = node(group, "GeometryNodeSwitch", "Keep instances by default / 默认保留实例", 1620, 650)
    switch.input_type = "GEOMETRY"
    wire(group, inp.outputs["Realize 实体化"], switch.inputs["Switch"])
    wire(group, instances.outputs["Instances"], switch.inputs["False"])
    wire(group, realize.outputs["Geometry"], switch.inputs["True"])
    wire(group, switch.outputs["Output"], out.inputs["Geometry 几何"])
    return group


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing asset build: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    groups = [build_ring(), build_pattern()]
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
    print("RANDY_BUILD " + json.dumps({"blender": bpy.app.version_string, "output": str(output), "assets": [g.name for g in groups]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
