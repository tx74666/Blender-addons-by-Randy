"""Native skirt cloth, collider, cache and animation-copy integration checks.

Run in a disposable factory Blender session with --disable-autoexec.
Never opens or saves an artist blend.
"""

import json
import math
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import skirt_physics as physics
from character_designer import skirt_rig
from test_skirt_topology_blender import frustum


def activate(obj):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def character():
    data = bpy.data.armatures.new("Physics Test Character")
    obj = bpy.data.objects.new("Physics Test Character", data)
    bpy.context.scene.collection.objects.link(obj)
    activate(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    hips = data.edit_bones.new("Hips")
    hips.head, hips.tail = (0, 0, 2), (0, 0, 2.25)
    for side, x in (("L", 0.2), ("R", -0.2)):
        bone = data.edit_bones.new("thigh." + side)
        bone.head, bone.tail = (x, 0, 1.9), (x, 0, 1.1)
        bone.parent = hips
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def evaluated_vertices(obj):
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def evaluated_bones(rig, names):
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {name: evaluated.matrix_world @ evaluated.pose.bones[name].matrix for name in names}


def maximum_distance(first, second):
    assert len(first) == len(second)
    return max((a - b).length for a, b in zip(first, second))


def matrix_error(first, second):
    return max(abs(first[row][column] - second[row][column]) for row in range(4) for column in range(4))


def action_state(obj):
    animation = obj.animation_data
    if not animation:
        return None
    action = animation.action
    curves = []
    if action:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        curves.append((curve.data_path, curve.array_index,
                                       tuple((tuple(key.co), key.interpolation) for key in curve.keyframe_points)))
    return (action, tuple(curves), tuple(driver.data_path for driver in animation.drivers))


def closed_convex_mesh(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        assert all(edge.is_manifold and len(edge.link_faces) == 2 for edge in bm.edges), obj.name + " is open"
        assert all(vertex.link_faces for vertex in bm.verts)
        assert bm.calc_volume(signed=True) > 0, obj.name + " winding is inverted"
        # Every point must lie on or behind each face plane of a convex collider.
        for face in bm.faces:
            origin = face.verts[0].co
            assert max(face.normal.dot(vertex.co - origin) for vertex in bm.verts) < 2e-5, obj.name + " is concave"
    finally:
        bm.free()


def data_names():
    return {label: frozenset(item.name for item in values) for label, values in (
        ("objects", bpy.data.objects), ("meshes", bpy.data.meshes),
        ("armatures", bpy.data.armatures), ("collections", bpy.data.collections),
        ("actions", bpy.data.actions))}


def verify_saved_copy_without_addon(result, references):
    """Reopen a saved bake in a separate factory Blender and remove the live rig."""
    with tempfile.TemporaryDirectory(prefix="cd-skirt-bake-test-") as temporary:
        folder = Path(temporary)
        blend_path = folder / "baked-copy.blend"
        payload_path = folder / "expected.json"
        script_path = folder / "verify_copy.py"
        payload_path.write_text(json.dumps({"rig": result["rig"], "mesh": result["mesh"],
                                           "frames": references}), encoding="utf-8")
        script_path.write_text('''import bpy, json, math, sys
from mathutils import Vector
with open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8") as stream:
    payload = json.load(stream)
assert "character_designer" not in sys.modules, "Addon loaded in standalone verification"
rig = bpy.data.objects[payload["rig"]]
mesh = bpy.data.objects[payload["mesh"]]
for obj in list(bpy.data.objects):
    if obj != rig and obj != mesh:
        bpy.data.objects.remove(obj, do_unlink=True)
assert rig.parent is None and not rig.constraints and not rig.animation_data.drivers
assert all(not bone.constraints for bone in rig.pose.bones)
for frame, reference in reversed(list(payload["frames"].items())):
    bpy.context.scene.frame_set(int(frame))
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = rig.evaluated_get(graph)
    for name, matrix in reference["bones"].items():
        actual = evaluated.matrix_world @ evaluated.pose.bones[name].matrix
        assert max(abs(actual[r][c] - matrix[r][c]) for r in range(4) for c in range(4)) < 2e-4
    evaluated = mesh.evaluated_get(graph)
    data = evaluated.to_mesh()
    try:
        actual = [evaluated.matrix_world @ vertex.co for vertex in data.vertices]
        assert max((point - Vector(expected)).length for point, expected in zip(actual, reference["vertices"])) < 3e-4
    finally:
        evaluated.to_mesh_clear()
print("STANDALONE_BAKED_COPY_REOPEN_PASS", flush=True)
''', encoding="utf-8")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
        command = [bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec", "--threads", "2",
                   str(blend_path), "--python-exit-code", "1", "--python", str(script_path), "--", str(payload_path)]
        child = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert child.returncode == 0, child.stdout + child.stderr
        assert "STANDALONE_BAKED_COPY_REOPEN_PASS" in child.stdout
        print("PASS saved animation copy reopens without addon, character or live skirt rig", flush=True)


def main():
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 15
    scene.frame_set(1)
    target = character()
    source = frustum("Physics Skirt", rows=10, sides=32)
    source_before = evaluated_vertices(source)
    activate(source)
    record = skirt_rig.build_skirt(bpy.context, source, chain_count=4, segment_count=3,
                                   armature=target, parent_bone="Hips")
    rig = bpy.data.objects[record["rig"]]
    rig_rest = evaluated_vertices(source)
    rig_rest_error = maximum_distance(rig_rest, source_before)
    assert rig_rest_error < 1e-3, ("Manual rig changed the resting skirt", rig_rest_error)
    record = physics.add_physics(bpy.context, source)
    bpy.context.view_layer.update()
    proxy = bpy.data.objects[record["physics"]["proxy"]]
    cloth = next(modifier for modifier in proxy.modifiers if modifier.type == "CLOTH")
    colliders = [bpy.data.objects[name] for name in record["physics"]["colliders"]]
    assert len(colliders) == 3, "Expected closed pelvis and two thigh proxies"
    for collider in colliders:
        closed_convex_mesh(collider)
        assert collider.parent == target
        assert any(modifier.type == "COLLISION" for modifier in collider.modifiers)
    assert rig.parent == target and rig.parent_type == "BONE" and rig.parent_bone == "Hips"
    assert source.parent == rig and proxy.parent == rig
    uses = Counter(tuple(sorted((a, b))) for face in proxy.data.polygons
                   for a, b in zip(face.vertices, list(face.vertices[1:]) + [face.vertices[0]]))
    assert sum(count == 1 for count in uses.values()) == 2 * record["physics"]["columns"]
    assert all(count in {1, 2} for count in uses.values())
    assert len(proxy.data.vertices) == record["physics"]["rows"] * record["physics"]["columns"]
    physics_rest_error = maximum_distance(evaluated_vertices(source), rig_rest)
    assert physics_rest_error < 1e-4, ("Building physics caused a rest pop", rig_rest_error, physics_rest_error)
    print("PASS closed convex colliders, connected cage, attachment and rest", flush=True)

    waist = rig.pose.bones[record["controls"]["waist"]]
    hips = target.pose.bones["Hips"]
    for frame, translation, rotation in ((1, 0.0, 0.0), (8, 0.13, 0.14), (15, -0.08, -0.12)):
        hips.location.x = translation
        hips.keyframe_insert("location", frame=frame)
        hips.rotation_mode = "XYZ"
        hips.rotation_euler.y = rotation
        hips.keyframe_insert("rotation_euler", frame=frame)
        waist.rotation_euler.z = rotation * 0.5
        waist.keyframe_insert("rotation_euler", frame=frame)
    scene.frame_set(4)
    active_before, mode_before = bpy.context.view_layer.objects.active, bpy.context.object.mode
    rig_action, target_action, source_action = action_state(rig), action_state(target), action_state(source)
    source_basis = source.matrix_basis.copy()
    def_names = [record["controls"]["waist"]] + [name for chain in record["chains"] for name in chain["def"]]
    phys_names = [name for chain in record["chains"] for name in chain["phys"]]
    sequential, cached_bones = {}, {}
    cloth_deflection = 0.0
    physics_difference = 0.0
    iterator = physics.bake_steps(bpy.context, source, 1, 15, "SIMULATION")
    for progress, total, label in iterator:
        assert progress == scene.frame_current and total == 15
        sequential[progress] = evaluated_vertices(source)
        cached_bones[progress] = evaluated_bones(rig, def_names)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        rigid = evaluated.matrix_world @ evaluated.pose.bones[waist.name].matrix @ rig.data.bones[waist.name].matrix_local.inverted()
        proxy_local = rig.matrix_world.inverted() @ proxy.matrix_world
        rigid_proxy = [rigid @ proxy_local @ vertex.co for vertex in proxy.data.vertices]
        cloth_deflection = max(cloth_deflection, maximum_distance(evaluated_vertices(proxy), rigid_proxy))
        for chain in record["chains"]:
            physics_difference = max(physics_difference, max(
                matrix_error(evaluated.pose.bones[manual].matrix, evaluated.pose.bones[physical].matrix)
                for manual, physical in zip(chain["manual"], chain["phys"])))
        for name in phys_names:
            pose = evaluated.pose.bones[name]
            assert math.isfinite(pose.length)
            assert abs(pose.length - rig.data.bones[name].length) < 3e-5, "Physics changed a bone length"
        assert all(math.isfinite(value) for point in sequential[progress] for value in point)
        assert max(point.length for point in sequential[progress]) < 10, "Simulation exploded"
    assert cloth.point_cache.is_baked and cloth.point_cache.frame_step == 1
    assert scene.frame_current == 4 and bpy.context.view_layer.objects.active == active_before
    assert bpy.context.object.mode == mode_before
    assert maximum_distance(sequential[1], sequential[15]) > 0.01, "No animated result"
    assert cloth_deflection > 0.005, ("Cloth did not simulate away from its rigid attachment", cloth_deflection)
    assert physics_difference > 0.005, ("Physics bones did not respond to cloth", physics_difference)
    for frame in (15, 3, 11, 1, 8, 5, 14):
        scene.frame_set(frame)
        assert maximum_distance(evaluated_vertices(source), sequential[frame]) < 3e-5, "Random seek differs from sequential cache"
    print("PASS sequential simulation bake, finite bone motion and random-seek cache", flush=True)

    scene.frame_set(4)
    result = physics.bake_animation(bpy.context, source, 1, 15)
    assert result["frames"] == 15 and result["kind"] == "ANIMATION"
    assert scene.frame_current == 4 and bpy.context.view_layer.objects.active == active_before
    assert action_state(rig) == rig_action and action_state(target) == target_action and action_state(source) == source_action
    assert matrix_error(source.matrix_basis, source_basis) < 1e-7
    output = bpy.data.objects[result["rig"]]
    output_mesh = bpy.data.objects[result["mesh"]]
    output_collection = bpy.data.collections[result["collection"]]
    assert output.parent is None and not output.constraints
    assert not output.animation_data.drivers
    assert all(not bone.constraints for bone in output.pose.bones)
    assert {bone.name for bone in output.pose.bones} == set(def_names)
    output_collection.hide_viewport = False
    output_collection.hide_render = False
    bpy.context.view_layer.update()
    references = {}
    for frame in (1, 8, 15, 3, 12, 6):
        scene.frame_set(frame)
        live, baked = evaluated_bones(rig, def_names), evaluated_bones(output, def_names)
        error = max(matrix_error(live[name], baked[name]) for name in def_names)
        assert error < 2e-4, ("Baked pose differs", frame, error)
        error = maximum_distance(evaluated_vertices(source), evaluated_vertices(output_mesh))
        assert error < 3e-4, ("Baked mesh differs", frame, error)
        references[frame] = {"bones": {name: [list(row) for row in matrix] for name, matrix in live.items()},
                             "vertices": [list(point) for point in evaluated_vertices(source)]}
    print("PASS independent animation copy matches evaluated live bones and mesh", flush=True)
    verify_saved_copy_without_addon(result, references)

    scene.frame_set(4)
    before = data_names()
    iterator = physics.bake_steps(bpy.context, source, 1, 15, "ANIMATION")
    next(iterator)
    next(iterator)
    iterator.close()
    assert data_names() == before, "Canceled export leaked data"
    assert scene.frame_current == 4 and bpy.context.view_layer.objects.active == active_before
    assert bpy.context.object.mode == mode_before
    assert action_state(rig) == rig_action and action_state(target) == target_action
    print("PASS canceled animation-copy cleanup and context restoration", flush=True)
    print("SKIRT_PHYSICS_PASS=" + json.dumps({"frames": 15, "colliders": len(colliders),
        "cage_vertices": len(proxy.data.vertices), "deform_bones": len(def_names)}), flush=True)


if __name__ == "__main__":
    main()
