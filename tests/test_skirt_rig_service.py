"""Blender background integration checks for native skirt rig ownership and posing."""
import importlib
import math
import os
import sys
import types

import bpy
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
package = types.ModuleType("character_designer")
package.__path__ = [os.path.join(ROOT, "addons", "character_designer")]
sys.modules["character_designer"] = package
service = importlib.import_module("character_designer.skirt_rig")


def fixture():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    vertices, faces = [], []
    columns, levels = 32, 10
    for ring in range(levels):
        t = ring / (levels - 1)
        for column in range(columns):
            angle = math.tau * column / columns
            vertices.append(((0.35 + 0.35 * t) * math.cos(angle),
                             (0.28 + 0.25 * t) * math.sin(angle), 1.1 - t * 0.7))
    for ring in range(levels - 1):
        for column in range(columns):
            a = ring * columns + column
            b = ring * columns + (column + 1) % columns
            faces.append((a, a + columns, b + columns, b))
    mesh = bpy.data.meshes.new("Dress mesh")
    mesh.from_pydata(vertices, [], faces)
    source = bpy.data.objects.new("Dress", mesh)
    bpy.context.scene.collection.objects.link(source)
    source.matrix_world = Matrix.Translation((0.2, -0.3, 0.1)) @ Matrix.Diagonal((0.73, 0.73, 0.73, 1.0))
    bpy.context.view_layer.objects.active = source
    source.select_set(True)
    keep = source.vertex_groups.new(name="Artist pin")
    keep.add([3, 25], 0.42, "REPLACE")
    source.modifiers.new("Surface smooth", "SUBSURF").levels = 0
    return source


def evaluated_vertices(source):
    graph = bpy.context.evaluated_depsgraph_get()
    return [point.co.copy() for point in source.evaluated_get(graph).data.vertices]


source = fixture()
before = evaluated_vertices(source)
matrix = source.matrix_world.copy()
record = service.build_skirt(bpy.context, source)
rig = source[service.RIG_KEY]
after = evaluated_vertices(source)
error = max((a - b).length for a, b in zip(before, after))
print("REST_ERROR", error)
# Blender's native Spline IK introduces submillimetre float rounding at a
# near-axis chain even for a straight curve (under 0.04% of skirt height).
assert error < 5e-4, error
assert max(abs(source.matrix_world[i][j] - matrix[i][j]) for i in range(4) for j in range(4)) < 1e-6
assert len(record["chains"]) == 8
assert len([b for b in rig.data.bones if b.use_deform]) == 33
assert source.modifiers[0].type == "ARMATURE"
for vertex in source.data.vertices:
    total = sum(group.weight for group in vertex.groups if source.vertex_groups[group.group].name in record["groups"])
    assert abs(total - 1.0) < 1e-6
count = len(bpy.data.objects)
assert service.build_skirt(bpy.context, source)["owner"] == record["owner"]
assert len(bpy.data.objects) == count
source.data.vertices[8].co.x += 0.02
try:
    service.build_skirt(bpy.context, source)
    raise AssertionError("Changed source geometry must require refitting")
except service.SkirtRigError as error:
    assert "mesh changed" in str(error)
source.data.vertices[8].co.x -= 0.02
rig.pose.bones[record["controls"]["chains"][0]["hem"]].location.x = 0.15
bpy.context.view_layer.update()
after_pose = evaluated_vertices(source)
displacement = max((a - b).length for a, b in zip(after, after_pose))
print("POSE_DISPLACEMENT", displacement)
assert displacement > 0.03
assert max((after_pose[index] - after[index]).length for index in record["fit"]["rings"][0]) < 1e-5
for chain in record["chains"]:
    for manual, deform in zip(chain["manual"], chain["def"]):
        matrix_error = max(abs(rig.pose.bones[manual].matrix[i][j] - rig.pose.bones[deform].matrix[i][j])
                           for i in range(4) for j in range(4))
        assert matrix_error < 1e-3
source.vertex_groups.new(name="Artist later").add([8], 0.77, "REPLACE")
rig.name = "Artist renamed rig"
assert service.read_record(source)["rig"] == rig.name
source.modifiers[0].name = "Artist renamed generated skin"
service.remove_skirt(bpy.context, source)
assert not service.read_record(source)
assert len(bpy.data.objects) == 1
assert source.modifiers[0].name == "Surface smooth"
assert abs(source.vertex_groups["Artist pin"].weight(3) - 0.42) < 1e-6
assert abs(source.vertex_groups["Artist later"].weight(8) - 0.77) < 1e-6
assert max(abs(source.matrix_world[i][j] - matrix[i][j]) for i in range(4) for j in range(4)) < 1e-6

# An exception after bones exist must restore source ownership, weights and context.
source = fixture()
original_hook = service._hook
def fail(*args, **kwargs):
    raise RuntimeError("Injected Hook failure")
service._hook = fail
try:
    service.build_skirt(bpy.context, source)
    raise AssertionError("Failure injection did not raise")
except service.SkirtRigError:
    pass
finally:
    service._hook = original_hook
assert len(bpy.data.objects) == 1
assert len(source.vertex_groups) == 1
assert source.parent is None
assert bpy.context.view_layer.objects.active is source
assert source.mode == "OBJECT"
assert service.RECORD_KEY not in source and service.RIG_KEY not in source

# Rollback after source skin/parent changes, not just early Hook creation.
source = fixture()
original_write = service.write_record
service.write_record = fail
try:
    service.build_skirt(bpy.context, source)
    raise AssertionError("Late failure injection did not raise")
except service.SkirtRigError:
    pass
finally:
    service.write_record = original_write
assert len(bpy.data.objects) == 1 and len(source.vertex_groups) == 1
assert source.parent is None and len(source.modifiers) == 1
assert source.modifiers[0].name == "Surface smooth"
assert service.RECORD_KEY not in source and service.RIG_KEY not in source

source = fixture()
source.modifiers.new("Unapplied mirrored skirt", "MIRROR")
try:
    service.build_skirt(bpy.context, source)
    raise AssertionError("Unapplied topology modifier must be rejected")
except service.SkirtRigError as error:
    assert "Unapplied mirrored skirt" in str(error)
assert len(bpy.data.objects) == 1 and len(source.vertex_groups) == 1

# Bone parenting must preserve the initial world frame and follow the pelvis
# exactly once, including with a transformed character armature.
source = fixture()
matrix = source.matrix_world.copy()
data = bpy.data.armatures.new("Character")
character = bpy.data.objects.new("Character", data)
bpy.context.scene.collection.objects.link(character)
service._activate(bpy.context, character, "EDIT")
bone = data.edit_bones.new("Hips")
bone.head, bone.tail = (0, 0, 1), (0, 0, 1.2)
bpy.ops.object.mode_set(mode="OBJECT")
character.location = (0.2, 0.3, -0.1)
bpy.context.view_layer.update()
record = service.build_skirt(bpy.context, source, armature=character)
rig = source[service.RIG_KEY]
assert max(abs(source.matrix_world[i][j] - matrix[i][j]) for i in range(4) for j in range(4)) < 1e-6
start_world = source.matrix_world.translation.copy()
character.pose.bones["Hips"].location.x = 0.4
bpy.context.view_layer.update()
assert abs((source.matrix_world.translation - start_world).x - 0.4) < 1e-5
rig["physics_influence"] = 1.0
bpy.context.view_layer.update()
before_physics = evaluated_vertices(source)
physics = rig.pose.bones[record["chains"][0]["phys"][0]]
physics.rotation_mode = "XYZ"
physics.rotation_euler.x = 0.2
bpy.context.view_layer.update()
physics_displacement = max((a - b).length for a, b in zip(before_physics, evaluated_vertices(source)))
print("PHYSICS_DELTA_DISPLACEMENT", physics_displacement)
assert physics_displacement > 0.02
graph = bpy.context.evaluated_depsgraph_get()
evaluated_rig = rig.evaluated_get(graph)
chain = record["chains"][0]["def"]
gaps = [(evaluated_rig.pose.bones[a].tail - evaluated_rig.pose.bones[b].head).length for a, b in zip(chain, chain[1:])]
print("PHYSICS_CHAIN_GAPS", gaps)
assert max(gaps) < 1e-5
orientation_errors = [evaluated_rig.pose.bones[a].matrix.to_quaternion().rotation_difference(
    evaluated_rig.pose.bones[b].matrix.to_quaternion()).angle
    for a, b in zip(record["chains"][0]["phys"], record["chains"][0]["def"])]
print("PHYSICS_ORIENTATION_ERRORS", orientation_errors)
assert max(orientation_errors) < 1e-3
rig.pose.bones[record["controls"]["waist"]].keyframe_insert("location", frame=1)
try:
    service.remove_skirt(bpy.context, source)
    raise AssertionError("Animated controls require explicit confirmation")
except service.SkirtRigError as error:
    assert "animation" in str(error)
service.remove_skirt(bpy.context, source, allow_animation=True)
assert len(bpy.data.objects) == 2
print("SKIRT_RIG_SERVICE_OK")
