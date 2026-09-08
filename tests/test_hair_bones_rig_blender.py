"""Disposable native hair bone integration tests for Blender 5.2.

Run with --background --factory-startup --disable-autoexec --python-exit-code 1
--python tests/test_hair_bones_rig_blender.py. Production files are never opened
or saved. The final check opens a temporary blend in a second, addon-free process.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
from character_designer import hair_bones_rig as service


EPSILON = 8.0e-5


def activate(obj, mode="OBJECT", vertices=None):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for item in bpy.context.selected_objects:
        item.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if mode != "OBJECT":
        bpy.ops.object.mode_set(mode=mode)
    if mode == "EDIT" and vertices is not None:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for face in bm.faces:
            face.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        chosen = set(vertices)
        for vertex in bm.verts:
            vertex.select_set(vertex.index in chosen)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    bpy.context.view_layer.update()


def reset():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def make_armature(name="Character"):
    data = bpy.data.armatures.new(name + "Data")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    activate(obj, "EDIT")
    specs = (
        ("Neck", (0, 0, 1.4), (0, 0, 1.8), None),
        ("spine.006", (0, 0, 1.8), (0, 0, 2.2), "Neck"),
        ("eye.L", (0.08, -0.1, 2.05), (0.08, -0.2, 2.05), "spine.006"),
        ("eye.R", (-0.08, -0.1, 2.05), (-0.08, -0.2, 2.05), "spine.006"),
        ("Arm", (0.3, 0, 1.5), (0.8, 0, 1.3), "Neck"),
    )
    for bone_name, head, tail, parent in specs:
        bone = data.edit_bones.new(bone_name)
        bone.head, bone.tail = head, tail
        bone.use_deform = True
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def make_hair(name="Hair", strands=2):
    """Two strands connected through a root bridge, excluded from bone plans."""
    vertices, faces, plans = [], [], []
    for strand in range(strands):
        layers = []
        for row in range(7):
            center = Vector((0.28 + strand * 0.32 + 0.018 * row * row,
                             0.015 * row * row, 2.0 - 0.19 * row))
            layer = tuple(range(len(vertices), len(vertices) + 3))
            for i in range(3):
                angle = i * math.tau / 3
                vertices.append(tuple(center + Vector((0.055 * math.cos(angle),
                                                        0.055 * math.sin(angle), 0))))
            layers.append(layer)
        for upper, lower in zip(layers, layers[1:]):
            for i in range(3):
                j = (i + 1) % 3
                faces.append((upper[i], upper[j], lower[j], lower[i]))
        selected_layers = tuple(layers[1:])
        centers = tuple(tuple(sum((Vector(vertices[i]) for i in layer), Vector()) / len(layer))
                        for layer in selected_layers)
        plans.append({"layers": selected_layers, "centers": centers,
                      "vertices": tuple(i for layer in selected_layers for i in layer),
                      "signature": f"fixture-strand-{strand}",
                      "direction_confirmable": True, "root_tip_rule": "fixture-root"})
        if strand:
            previous_root = (strand - 1) * 21
            root = strand * 21
            faces.append((previous_root, previous_root + 1, root + 1, root))
    data = bpy.data.meshes.new(name + "Mesh")
    data.from_pydata(vertices, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj, tuple(plans)


def weights(obj, indices=None):
    names = {group.index: group.name for group in obj.vertex_groups}
    selected = range(len(obj.data.vertices)) if indices is None else indices
    return {i: {names[entry.group]: round(entry.weight, 7)
                for entry in obj.data.vertices[i].groups}
            for i in selected}


def evaluated_points(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    data = evaluated.to_mesh()
    try:
        return tuple(obj.matrix_world @ vertex.co for vertex in data.vertices)
    finally:
        evaluated.to_mesh_clear()


def assert_points(actual, expected, label):
    assert len(actual) == len(expected), (label, len(actual), len(expected))
    error = max(((a - b).length for a, b in zip(actual, expected)), default=0.0)
    assert error < EPSILON, (label, error)


def plain_snapshot(obj):
    # Flush Mesh Edit coordinates and selection without changing the context.
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    return {
        "objects": tuple(sorted((item.name, item.type, item.as_pointer()) for item in bpy.data.objects)),
        "rig_state": tuple((item.name, item.show_in_front,
                             item.data.bones.active.name if item.data.bones.active else None,
                             tuple((bone.name, bone.select, bone.rotation_mode,
                                    tuple(tuple(row) for row in bone.matrix_basis))
                                   for bone in item.pose.bones))
                            for item in bpy.data.objects if item.type == "ARMATURE"),
        "armatures": tuple((item.name, tuple((bone.name, bone.parent.name if bone.parent else None,
                                               tuple(bone.head_local), tuple(bone.tail_local))
                                              for bone in item.bones),
                             tuple(collection.name for collection in item.collections))
                           for item in bpy.data.armatures),
        "geometry": tuple(tuple(vertex.co) for vertex in obj.data.vertices),
        "selection": tuple(vertex.select for vertex in obj.data.vertices),
        "faces": tuple(tuple(face.vertices) for face in obj.data.polygons),
        "groups": tuple((group.name, group.lock_weight) for group in obj.vertex_groups),
        "weights": weights(obj),
        "modifiers": tuple((m.name, m.type, m.object.name if m.type == "ARMATURE" and m.object else None,
                             m.mirror_object.name if m.type == "MIRROR" and m.mirror_object else None)
                           for m in obj.modifiers),
        "properties": repr(obj.get(service.RECORD_KEY)),
        "rig_reference": repr(obj.get(service.RIG_KEY)),
        "parent": obj.parent.name if obj.parent else None,
        "matrix": tuple(tuple(row) for row in obj.matrix_world),
        "mode": obj.mode,
        "active": bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        "selected_objects": tuple(sorted(item.name for item in bpy.context.selected_objects)),
    }


def build(obj, plans, **kwargs):
    activate(obj, "EDIT", (i for plan in plans for i in plan["vertices"]))
    result = service.build_hair_bones(bpy.context, obj, plans, **kwargs)
    assert result["armature"].type == "ARMATURE"
    assert result["armature"].mode == "POSE", "Generation must leave usable FK controls selected"
    assert len(result["chains"]) == len(plans)
    return result


def assert_chains(obj, plans, result, count=4):
    arm = result["armature"]
    for plan, chain in zip(plans, result["chains"]):
        assert chain["signature"] == plan["signature"]
        assert len(chain["bones"]) == count
        bones = [arm.data.bones[name] for name in chain["bones"]]
        assert bones[0].parent.name == result["parent_bone"]
        assert all(bone.use_deform for bone in bones)
        assert all(b.parent == a and b.use_connect for a, b in zip(bones, bones[1:]))
        assert_points((arm.matrix_world @ bones[0].head_local,
                       arm.matrix_world @ bones[-1].tail_local),
                      (obj.matrix_world @ Vector(plan["centers"][0]),
                       obj.matrix_world @ Vector(plan["centers"][-1])), "world chain endpoints")
        assert all(arm.pose.bones[name].select for name in chain["bones"])


def test_existing_rig_weights_deformation_and_repeat():
    reset()
    arm = make_armature()
    obj, plans = make_hair()
    selected = set(i for plan in plans for i in plan["vertices"])
    outside = set(range(len(obj.data.vertices))) - selected
    head = obj.vertex_groups.new(name="spine.006")
    head.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    old_arm = obj.vertex_groups.new(name="Arm")
    old_arm.add([0], 1.0, "REPLACE")
    old_arm.lock_weight = True
    head.remove([0])
    artist = obj.vertex_groups.new(name="ArtistMask")
    artist.add(list(range(len(obj.data.vertices))), 0.37, "REPLACE")
    artist.lock_weight = True
    modifier = obj.modifiers.new("ExistingDeformation", "ARMATURE")
    modifier.object = arm
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="ArtistShape")
    key.data[0].co.x += 0.012
    arm.pose.bones["Arm"].rotation_mode = "XYZ"
    arm.pose.bones["Arm"].rotation_euler.z = 0.42
    before_points = evaluated_points(obj)
    before_outside = weights(obj, outside)
    before_keys = tuple(tuple(vertex.co) for vertex in key.data)
    before_arm_matrix = arm.pose.bones["Arm"].matrix_basis.copy()
    result = build(obj, plans)
    assert result["armature"] is arm and result["parent_bone"] == "spine.006"
    assert result["created"] == 2 and result["reused"] == 0
    assert not result["rig_created"] and not result["modifier_created"]
    assert_chains(obj, plans, result)
    assert_points(evaluated_points(obj), before_points, "binding must not jump")
    assert weights(obj, outside) == before_outside, "Outside weights changed"
    assert tuple(tuple(vertex.co) for vertex in key.data) == before_keys
    assert arm.pose.bones["Arm"].matrix_basis == before_arm_matrix
    generated = set(name for chain in result["chains"] for name in chain["bones"])
    selected_weights = weights(obj, selected)
    for index, values in selected_weights.items():
        assert abs(values["ArtistMask"] - 0.37) < 1e-6
        assert abs(sum(weight for name, weight in values.items()
                       if name in generated or name == result["parent_bone"]) - 1.0) < 2e-6
        assert values.get("Arm", 0.0) == 0.0
    # Every cross section receives identical weights; neighboring sections are smooth.
    for plan in plans:
        for layer in plan["layers"]:
            reference = selected_weights[layer[0]]
            assert all(selected_weights[index] == reference for index in layer)
    chain = result["chains"][0]
    moving = arm.pose.bones[chain["bones"][1]]
    moving.rotation_mode = "XYZ"
    moving.rotation_euler.x = 0.6
    deformed = evaluated_points(obj)
    tip = plans[0]["layers"][-1][0]
    assert (deformed[tip] - before_points[tip]).length > 0.05, "FK control did not bend strand"
    assert_points(tuple(deformed[i] for i in outside), tuple(before_points[i] for i in outside), "local FK influence")
    assert_points(tuple(deformed[i] for i in plans[1]["vertices"]),
                  tuple(before_points[i] for i in plans[1]["vertices"]), "other strand isolation")
    counts = (len(arm.data.bones), len(obj.vertex_groups), len(obj.modifiers), len(bpy.data.objects))
    repeated = build(obj, plans)
    assert repeated["created"] == 0 and repeated["reused"] == 2
    assert counts == (len(arm.data.bones), len(obj.vertex_groups), len(obj.modifiers), len(bpy.data.objects))
    assert_points(evaluated_points(obj), deformed, "repeat must preserve animated pose")
    head_pose = arm.pose.bones["spine.006"]
    before_head = head_pose.matrix.copy()
    head_pose.rotation_mode = "XYZ"
    head_pose.rotation_euler.z = 0.31
    bpy.context.view_layer.update()
    delta = arm.matrix_world @ head_pose.matrix @ before_head.inverted() @ arm.matrix_world.inverted()
    after_head = evaluated_points(obj)
    assert_points(tuple(after_head[i] for i in selected), tuple(delta @ deformed[i] for i in selected), "head parent following")
    return obj, arm, plans, result


def test_transformed_mesh_and_modifier_order():
    reset()
    arm = make_armature()
    arm.matrix_world = Matrix.Translation((-0.4, 0.3, 0.2)) @ Matrix.Rotation(-0.2, 4, "Z")
    obj, plans = make_hair(strands=1)
    obj.matrix_world = (Matrix.Translation((0.5, -0.2, 0.3)) @
                        Euler((0.2, -0.17, 0.31)).to_matrix().to_4x4() @
                        Matrix.Diagonal((1.2, 0.8, 1.4, 1.0)))
    mirror = obj.modifiers.new("ArtistMirror", "MIRROR")
    mirror.use_mirror_merge = False
    subdivision = obj.modifiers.new("ArtistSubdivision", "SUBSURF")
    subdivision.show_viewport = False
    before = evaluated_points(obj)
    result = build(obj, plans)
    assert result["armature"] is arm and result["modifier_created"]
    assert [m.type for m in obj.modifiers] == ["ARMATURE", "MIRROR", "SUBSURF"]
    assert_chains(obj, plans, result)
    assert_points(evaluated_points(obj), before, "transformed bind")
    first = arm.pose.bones[result["chains"][0]["bones"][0]]
    first.rotation_mode = "XYZ"
    first.rotation_euler.x = 0.3
    points = evaluated_points(obj)
    local = [obj.matrix_world.inverted() @ point for point in points]
    count = len(obj.data.vertices)
    assert len(local) == count * 2
    assert_points(tuple(Vector((-p.x, p.y, p.z)) for p in local[:count]), tuple(local[count:]), "mirror shares authored chain")
    head = arm.pose.bones[result["parent_bone"]]
    before_head = head.matrix.copy()
    head.location.x += 0.23
    head.rotation_mode = "XYZ"
    head.rotation_euler.y += 0.37
    bpy.context.view_layer.update()
    delta = arm.matrix_world @ head.matrix @ before_head.inverted() @ arm.matrix_world.inverted()
    moved_head = evaluated_points(obj)
    assert_points(moved_head, tuple(delta @ point for point in points), "both mirrored halves follow moving head")
    rig_delta = Matrix.Translation((0.2, -0.4, 0.15)) @ Matrix.Rotation(0.2, 4, "Z")
    arm.matrix_world = rig_delta @ arm.matrix_world
    assert_points(evaluated_points(obj), tuple(rig_delta @ point for point in moved_head), "both mirrored halves follow rig object")
    return obj, arm, plans, result


def test_new_rig_anchor_and_append():
    reset()
    obj, plans = make_hair()
    before = evaluated_points(obj)
    result = build(obj, plans[:1])
    arm = result["armature"]
    assert result["rig_created"] and result["modifier_created"]
    assert len([item for item in bpy.data.objects if item.type == "ARMATURE"]) == 1
    assert_chains(obj, plans[:1], result)
    assert_points(evaluated_points(obj), before, "new rig binding")
    second = build(obj, plans[1:])
    assert second["armature"] is arm and not second["rig_created"] and not second["modifier_created"]
    assert second["created"] == 1
    anchor = arm.pose.bones[result["parent_bone"]]
    anchor.location.x = 0.21
    bpy.context.view_layer.update()
    expected_delta = arm.matrix_world.to_3x3() @ anchor.bone.matrix_local.to_3x3() @ Vector((0.21, 0, 0))
    assert_points(evaluated_points(obj), tuple(point + expected_delta for point in before), "new anchor follows all mesh")


def expect_atomic_failure(obj, plans, label, **kwargs):
    activate(obj, "EDIT", plans[0]["vertices"])
    before = plain_snapshot(obj)
    try:
        service.build_hair_bones(bpy.context, obj, plans, **kwargs)
    except service.HairBonesRigError:
        pass
    else:
        raise AssertionError(label + " unexpectedly succeeded")
    assert plain_snapshot(obj) == before, label + " left partial changes or changed context"


def test_preflight_failures_are_atomic():
    reset()
    obj, plans = make_hair()
    invalid = dict(plans[1])
    invalid["layers"] = plans[1]["layers"][:-1] + ((len(obj.data.vertices) + 100,),)
    invalid["vertices"] = tuple(i for layer in invalid["layers"] for i in layer)
    expect_atomic_failure(obj, (plans[0], invalid), "batch invalid index")
    overlap = dict(plans[0], signature="overlap")
    expect_atomic_failure(obj, (plans[0], overlap), "overlapping ownership")
    copy = bpy.data.objects.new("SharedDataCopy", obj.data)
    bpy.context.scene.collection.objects.link(copy)
    expect_atomic_failure(obj, plans, "shared mesh")
    bpy.data.objects.remove(copy, do_unlink=True)
    arm = make_armature()
    group = obj.vertex_groups.new(name="spine.006")
    group.add(list(plans[0]["vertices"]), 1.0, "REPLACE")
    group.lock_weight = True
    expect_atomic_failure(obj, plans, "locked affected deform group", armature=arm)
    group.lock_weight = False
    arm.pose.bones["spine.006"].rotation_mode = "XYZ"
    arm.pose.bones["spine.006"].rotation_euler.z = 0.2
    bpy.context.view_layer.update()
    expect_atomic_failure(obj, plans, "posed head bind", armature=arm)


def test_mid_write_rollback():
    for existing_rig in (False, True):
        reset()
        if existing_rig:
            make_armature()
        obj, plans = make_hair()
        artist = obj.vertex_groups.new(name="ArtistMask")
        artist.add([0, 3, 9], 0.43, "REPLACE")
        original = service._write_weights

        def injected_write(mesh, planned_weights):
            original(mesh, planned_weights)
            assert any(group.name != "ArtistMask" for group in mesh.vertex_groups)
            assert any(item.type == "ARMATURE" for item in bpy.data.objects)
            raise RuntimeError("Injected failure after actual bone and weight creation")

        service._write_weights = injected_write
        try:
            expect_atomic_failure(obj, plans, "write rollback existing=" + str(existing_rig))
        finally:
            service._write_weights = original

    # A commit-time exception must also remove a newly created modifier and,
    # for mirrored hair, restore the artist's mirror plane and remove helpers.
    reset()
    make_armature()
    obj, plans = make_hair()
    obj.modifiers.new("ArtistMirror", "MIRROR")
    original = service._validate_owned
    calls = 0

    def injected_commit(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            assert any(modifier.type == "ARMATURE" for modifier in obj.modifiers)
            assert obj.get(service.RECORD_KEY)
            raise RuntimeError("Injected failure after metadata and modifier creation")
        return result

    service._validate_owned = injected_commit
    try:
        expect_atomic_failure(obj, plans, "commit rollback with artist Mirror")
    finally:
        service._validate_owned = original


def test_ambiguous_rig_and_first_binding_pose():
    reset()
    first_rig = make_armature("FirstCharacter")
    second_rig = make_armature("SecondCharacter")
    obj, plans = make_hair()
    expect_atomic_failure(obj, plans, "two possible character heads")
    bpy.data.objects.remove(second_rig, do_unlink=True)
    activate(obj)
    outside = obj.vertex_groups.new(name="Arm")
    outside.add([0], 1.0, "REPLACE")
    old_pose = first_rig.pose.bones["Arm"]
    old_pose.rotation_mode = "XYZ"
    old_pose.rotation_euler.z = 0.5
    bpy.context.view_layer.update()
    # Adding the first Armature would newly activate this old, unbound group.
    # Refuse the jump instead of altering its outside weight or resetting a pose.
    expect_atomic_failure(obj, plans, "first binding activates posed outside weights")


def test_small_count_and_short_strand():
    for requested, layer_count, expected in ((1, 6, 1), (12, 3, 2)):
        reset()
        obj, plans = make_hair(strands=1)
        plan = dict(plans[0])
        plan["layers"] = plan["layers"][:layer_count]
        plan["centers"] = plan["centers"][:layer_count]
        plan["vertices"] = tuple(i for layer in plan["layers"] for i in layer)
        result = build(obj, (plan,), bone_count=requested)
        assert_chains(obj, (plan,), result, count=expected)


def test_shared_root_append_and_changed_count():
    reset()
    arm = make_armature()
    obj, original = make_hair()
    first, second = original
    shared_layers = (first["layers"][0],) + second["layers"][1:]
    second = dict(second, layers=shared_layers,
                  centers=(first["centers"][0],) + second["centers"][1:],
                  vertices=tuple(i for layer in shared_layers for i in layer))
    # Weld the two original root sections, just as joined Hair3 strands share a cap.
    mapping = dict(zip(original[1]["layers"][0], first["layers"][0]))
    faces = [tuple(mapping.get(index, index) for index in face.vertices) for face in obj.data.polygons]
    coords = [tuple(vertex.co) for vertex in obj.data.vertices]
    obj.data.clear_geometry()
    obj.data.from_pydata(coords, [], faces)
    before = evaluated_points(obj)
    result = build(obj, (first,))
    moving = arm.pose.bones[result["chains"][0]["bones"][0]]
    moving.rotation_mode = "XYZ"
    moving.rotation_euler.x = 0.4
    first_bent = evaluated_points(obj)
    appended = build(obj, (second,))
    assert appended["armature"] is arm and appended["created"] == 1
    assert_points(evaluated_points(obj), first_bent, "shared-root append preserves earlier FK pose")
    root_points = first["layers"][0]
    assert all(values == {"spine.006": 1.0} for values in weights(obj, root_points).values())
    other = arm.pose.bones[appended["chains"][0]["bones"][0]]
    other.rotation_mode = "XYZ"
    other.rotation_euler.x = -0.35
    both_bent = evaluated_points(obj)
    assert_points(tuple(both_bent[i] for i in root_points), tuple(before[i] for i in root_points), "shared root stays anchored")
    assert (both_bent[second["layers"][-1][0]] - first_bent[second["layers"][-1][0]]).length > 0.05
    assert_points(tuple(both_bent[i] for i in first["vertices"]),
                  tuple(first_bent[i] for i in first["vertices"]), "shared-root chains remain independent")
    expect_atomic_failure(obj, (first,), "animated count change", bone_count=5)


def assert_native_reopen(obj, arm, plans, result):
    points = evaluated_points(obj)
    mirrored = any(modifier.type == "MIRROR" for modifier in obj.modifiers)
    payload = {"mesh": obj.name, "armature": arm.name,
               "bone": result["chains"][0]["bones"][2],
               "head": result["parent_bone"],
               "head_vertices": list(range(len(points))) if mirrored else
                                [i for plan in plans for i in plan["vertices"]],
               "tip": plans[0]["layers"][-1][0],
               "points": [tuple(point) for point in points]}
    # A separate Blender process has no imported Character Designer module,
    # no runtime handlers, and factory addon preferences.
    verify_source = '''
import bpy, json, sys
from mathutils import Vector
payload = json.loads(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8").read())
assert not any(name == "character_designer" or name.startswith("character_designer.") for name in sys.modules)
obj = bpy.data.objects[payload["mesh"]]
arm = bpy.data.objects[payload["armature"]]
def points():
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()
before = points()
error = max((a - Vector(b)).length for a, b in zip(before, payload["points"]))
assert error < 8e-5, ("reopened deformation", error)
bone = arm.pose.bones[payload["bone"]]
bone.rotation_mode = "XYZ"
bone.rotation_euler.z += 0.4
after = points()
assert (after[payload["tip"]] - before[payload["tip"]]).length > 0.02
head = arm.pose.bones[payload["head"]]
head_before = head.matrix.copy()
head.location.x += 0.11
head.rotation_mode = "XYZ"
head.rotation_euler.y -= 0.2
bpy.context.view_layer.update()
delta = arm.matrix_world @ head.matrix @ head_before.inverted() @ arm.matrix_world.inverted()
following = points()
error = max((following[i] - delta @ after[i]).length for i in payload["head_vertices"])
assert error < 8e-5, ("native reopened head follow", error)
print("HAIR_BONES_NATIVE_REOPEN_OK")
'''
    with tempfile.TemporaryDirectory(prefix="character_designer_hair_rig_") as directory:
        directory = Path(directory)
        blend = directory / "native_hair.blend"
        payload_file = directory / "expected.json"
        script = directory / "verify_native.py"
        payload_file.write_text(json.dumps(payload), encoding="utf-8")
        script.write_text(verify_source, encoding="utf-8")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
        command = [bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                   str(blend), "--python-exit-code", "1", "--python", str(script), "--", str(payload_file)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0 and "HAIR_BONES_NATIVE_REOPEN_OK" in completed.stdout, (
            completed.returncode, completed.stdout, completed.stderr)
        print("HAIR_BONES_NATIVE_REOPEN_OK")


def test_native_reopen_without_addon():
    assert_native_reopen(*test_existing_rig_weights_deformation_and_repeat())
    assert_native_reopen(*test_transformed_mesh_and_modifier_order())


def main():
    tests = (test_existing_rig_weights_deformation_and_repeat,
             test_transformed_mesh_and_modifier_order,
             test_new_rig_anchor_and_append,
             test_preflight_failures_are_atomic,
             test_mid_write_rollback,
             test_ambiguous_rig_and_first_binding_pose,
             test_small_count_and_short_strand,
             test_shared_root_append_and_changed_count,
             test_native_reopen_without_addon)
    for test in tests:
        test()
        print("PASS", test.__name__)
    print("HAIR_BONES_RIG_TESTS_OK", len(tests))


if __name__ == "__main__":
    main()
