"""Disposable Character Designer forearm twist lifecycle regression tests.

Run with Blender --background --factory-startup --python-exit-code 1 --python
tests/test_forearm_twist_blender.py.  Only generated fixtures and temporary
blend files are used; this suite never opens or saves production X.blend.
"""

from __future__ import annotations

import math
import json
import os
import sys
import tempfile

import bpy
from mathutils import Matrix, Quaternion, Vector


TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
for path in (TESTS, os.path.join(ROOT, "addons")):
    if path not in sys.path:
        sys.path.insert(0, path)

import character_designer
from character_designer import forearm_twist_math as geometry
from character_designer import forearm_twist as runtime
import test_limb_ik_blender as base


BUILD_METHODS = ("DIRECT_PREROLL", "ROLL_DECOUPLED")
RING_COUNT = 8
RING_SIZE = 12
TOLERANCE = 1.2e-4


def ensure_registered():
    if not hasattr(bpy.types.WindowManager, "character_designer_limb_ik"):
        character_designer.register()


def activate_mesh(mesh):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bpy.context.view_layer.update()


def matrix_tuple(matrix):
    return tuple(float(value) for row in matrix for value in row)


def pose_snapshot(armature):
    return {bone.name: (bone.rotation_mode, tuple(bone.location),
                        tuple(bone.rotation_euler), tuple(bone.rotation_quaternion),
                        tuple(bone.rotation_axis_angle), tuple(bone.scale),
                        matrix_tuple(bone.matrix_basis))
            for bone in armature.pose.bones}


def assert_pose_snapshot(armature, expected, label):
    actual = pose_snapshot(armature)
    assert set(actual) == set(expected), f"{label}: pose bone inventory changed"
    for name, before in expected.items():
        after = actual[name]
        assert before[0] == after[0], f"{label}: {name} rotation mode changed"
        for old, new in zip(before[1:], after[1:]):
            assert max(abs(a - b) for a, b in zip(old, new)) < 3.0e-6, f"{label}: {name} pose changed"


def structure_snapshot(armature, mesh):
    return {
        "bones": tuple((bone.name, bone.use_deform,
                         bone.parent.name if bone.parent else None,
                         matrix_tuple(bone.matrix_local))
                        for bone in armature.data.bones),
        "constraints": tuple((bone.name, tuple((item.name, item.type) for item in bone.constraints))
                             for bone in armature.pose.bones),
        "modifiers": tuple((modifier.name, modifier.type) for modifier in mesh.modifiers),
        "weights": tuple(tuple((entry.group, entry.weight) for entry in vertex.groups)
                         for vertex in mesh.data.vertices),
        "groups": tuple(group.name for group in mesh.vertex_groups),
        "topology": tuple(tuple(polygon.vertices) for polygon in mesh.data.polygons),
    }


def key_snapshot(mesh, names=("Basis", "ExistingFace", "ExistingForearm")):
    if mesh.data.shape_keys is None:
        return {}
    return {key.name: (key.value, key.mute, key.relative_key.name,
                       tuple(tuple(point.co) for point in key.data))
            for key in mesh.data.shape_keys.key_blocks if key.name in names}


def evaluated_points(mesh):
    bpy.context.view_layer.update()
    evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    data = evaluated.to_mesh()
    try:
        return [vertex.co.copy() for vertex in data.vertices]
    finally:
        evaluated.to_mesh_clear()


def assert_points_close(actual, expected, label, tolerance=TOLERANCE):
    assert len(actual) == len(expected), f"{label}: vertex count mismatch"
    for index, (left, right) in enumerate(zip(actual, expected)):
        error = (Vector(left) - Vector(right)).length
        assert error < tolerance, f"{label}: vertex {index}, error={error:.7g}"


def uncorrected_points(mesh):
    """Known fixture's existing shape keys, independent of the runtime key."""
    keys = mesh.data.shape_keys.key_blocks
    evaluated_keys = mesh.data.shape_keys.evaluated_get(bpy.context.evaluated_depsgraph_get()).key_blocks
    result = [point.co.copy() for point in keys["Basis"].data]
    for name in ("ExistingFace", "ExistingForearm"):
        key = keys.get(name)
        if key is None or key.mute:
            continue
        for index, point in enumerate(key.data):
            result[index] += evaluated_keys[name].value * (point.co - key.relative_key.data[index].co)
    return result


def deformation_matrices(armature):
    bpy.context.view_layer.update()
    evaluated = armature.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {bone.name: evaluated.pose.bones[bone.name].matrix @ bone.matrix_local.inverted()
            for bone in armature.data.bones if bone.use_deform}


def record_for(mesh, side="L"):
    return json.loads(mesh[runtime.RECORD_KEY])[side]


def expected_runtime_points(fixture):
    mesh, armature = fixture["mesh"], fixture["armature"]
    record = record_for(mesh, fixture["side"])
    matrices = deformation_matrices(armature)
    to_arm = armature.matrix_world.inverted() @ mesh.matrix_world
    from_arm = to_arm.inverted()
    source = uncorrected_points(mesh)
    active = dict(zip(record["vertices"], record["positions"]))
    knots = [(0.0, 0.0)] + [(ring["position"], ring["ratio"]) for ring in record["rings"]
                           if 1.0e-5 < ring["position"] < 1.0 - 1.0e-5] + [(1.0, 1.0)]
    expected = []
    for index, coordinate in enumerate(source):
        weights = normalized_weights(mesh, armature, index)
        point = to_arm @ coordinate
        if index in active:
            position = geometry.desired_vertex(point, matrices, weights, fixture["lower_name"],
                                               fixture["hand_name"], fixture["axis"], fixture["pivot"],
                                               geometry.profile_ratio(active[index], knots))
        else:
            position = geometry.blended_matrix(matrices, weights) @ point
        expected.append(from_arm @ position)
    return expected


def assert_runtime_geometry(fixture, label):
    expected = expected_runtime_points(fixture)
    actual = evaluated_points(fixture["mesh"])
    assert fixture["mesh"].name not in runtime._ERRORS, f"{label}: {runtime._ERRORS}"
    key = fixture["mesh"].data.shape_keys.key_blocks[record_for(fixture["mesh"], fixture["side"])["key"]]
    assert not key.mute and abs(key.value - 1.0) < 1.0e-8, f"{label}: corrective key is inactive"
    assert_points_close(actual, expected, label)


def pose_target(fixture, degrees, *, bend=0.0, side_wave=0.0, elbow_offset=0.0):
    target = fixture["target"]
    target.rotation_euler = (bend, math.radians(degrees), side_wave)
    target.location = (0.0, elbow_offset, 0.0)
    bpy.context.view_layer.update()


def assert_refused(function, label):
    try:
        function()
    except (runtime.ForearmTwistError, ValueError):
        return
    raise AssertionError(f"{label}: unsafe input was accepted")


def normalized_weights(mesh, armature, index):
    weights = {mesh.vertex_groups[entry.group].name: entry.weight
               for entry in mesh.data.vertices[index].groups
               if mesh.vertex_groups[entry.group].name in armature.data.bones
               and armature.data.bones[mesh.vertex_groups[entry.group].name].use_deform}
    total = sum(weights.values())
    return {name: value / total for name, value in weights.items()} if total else {}


def make_fixture(method, *, side="L", forearm_key=True, transformed_objects=False, build_ik=True):
    ensure_registered()
    base.reset_scene()
    armature = base.make_humanoid(name="ForearmTwistFixtureRig", include_right=True, roll_offset=0.61)
    result, settings = base.analyze(armature)
    assert result == {"FINISHED"}, settings.last_message
    settings.build_method = method
    if build_ik:
        assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}, settings.last_message
        bpy.context.view_layer.update()
        rig = base.limb_ik._validate_inventory(armature)["rigs"][("ARM", side)]
    else:
        rig = {"chain": [f"upper_arm.{side}", f"forearm.{side}", f"hand.{side}"],
               "target": armature.data.bones[f"hand.{side}"]}
    lower_name, hand_name = rig["chain"][1:]
    lower = armature.data.bones[lower_name]
    axis = (lower.tail_local - lower.head_local).normalized()
    wrist = lower.tail_local.copy()
    radial_a = lower.matrix_local.to_3x3() @ Vector((1, 0, 0))
    radial_b = lower.matrix_local.to_3x3() @ Vector((0, 0, 1))
    points, faces, rings, positions, all_rings = [], [], [], [], []
    all_positions = [-0.12] + [ring / (RING_COUNT - 1) for ring in range(RING_COUNT)] + [1.12]
    for ring, position in enumerate(all_positions):
        center = lower.head_local.lerp(lower.tail_local, position)
        radius = 0.036 - 0.008 * position
        ring_indices = []
        for spoke in range(RING_SIZE):
            angle = spoke * math.tau / RING_SIZE
            ring_indices.append(len(points))
            points.append(center + radius * (math.cos(angle) * radial_a + math.sin(angle) * radial_b))
        all_rings.append(ring_indices)
        if 0.0 <= position <= 1.0:
            rings.append(ring_indices)
            positions.append(position)
    for ring in range(len(all_rings) - 1):
        for spoke in range(RING_SIZE):
            next_spoke = (spoke + 1) % RING_SIZE
            faces.append((all_rings[ring][spoke], all_rings[ring + 1][spoke],
                          all_rings[ring + 1][next_spoke], all_rings[ring][next_spoke]))
    outside = list(range(len(points), len(points) + 3))
    points.extend((Vector((0.0, 0.0, 1.95)), Vector((0.06, 0.0, 1.95)), Vector((0.03, 0.0, 2.02))))
    faces.append(tuple(outside))
    mesh_data = bpy.data.meshes.new("ForearmTwistFixtureMesh")
    mesh_data.from_pydata(points, [], faces)
    mesh_data.update()
    mesh = bpy.data.objects.new("ForearmTwistFixtureMesh", mesh_data)
    bpy.context.collection.objects.link(mesh)
    for name in (lower_name, hand_name, "Hips"):
        mesh.vertex_groups.new(name=name)
    for position, indices in zip(all_positions, all_rings):
        hand_weight = max(0.0, min(1.0, position)) ** 3
        mesh.vertex_groups[lower_name].add(indices, 1.0 - hand_weight, "REPLACE")
        mesh.vertex_groups[hand_name].add(indices, hand_weight, "REPLACE")
    mesh.vertex_groups["Hips"].add(outside, 1.0, "REPLACE")
    mesh.shape_key_add(name="Basis")
    face = mesh.shape_key_add(name="ExistingFace")
    for index in outside:
        face.data[index].co.z += 0.031
    face.value = 0.42
    if forearm_key:
        shaped = mesh.shape_key_add(name="ExistingForearm")
        for ring, indices in enumerate(rings):
            for index in indices:
                shaped.data[index].co += radial_a * (0.005 * math.sin(positions[ring] * math.pi))
        shaped.value = 0.27
    armature_modifier = mesh.modifiers.new("Existing Armature", "ARMATURE")
    armature_modifier.object = armature
    armature_modifier.use_deform_preserve_volume = False
    subdivision = mesh.modifiers.new("Existing Surface", "SUBSURF")
    subdivision.levels = 0
    subdivision.render_levels = 0
    if transformed_objects:
        shared = Matrix.Translation((0.45, -0.24, 0.12)) @ Matrix.Rotation(0.37, 4, "Z") @ Matrix.Scale(1.3, 4)
        armature.matrix_world = shared
        mesh.matrix_world = Matrix.Translation((-0.2, 0.08, -0.31)) @ Matrix.Rotation(-0.27, 4, "X") @ Matrix.Scale(0.91, 4)
        mesh.data.transform(mesh.matrix_world.inverted() @ shared, shape_keys=True)
    activate_mesh(mesh)
    return {"armature": armature, "mesh": mesh, "rig": rig, "side": side,
            "target": armature.pose.bones[rig["target"].name],
            "lower_name": lower_name, "hand_name": hand_name,
            "axis": axis, "pivot": wrist, "rings": rings, "positions": positions,
            "outside": outside, "guard_rings": (all_rings[0], all_rings[-1]), "method": method}


def check_fixture_generation():
    for method in BUILD_METHODS:
        fixture = make_fixture(method)
        assert len(fixture["rings"]) == RING_COUNT
        assert len(evaluated_points(fixture["mesh"])) == (RING_COUNT + 2) * RING_SIZE + 3
        from character_designer.forearm_twist_topology import detect_rings
        detected = detect_rings(fixture["mesh"], fixture["armature"], fixture["lower_name"], fixture["hand_name"])
        assert len(detected) == RING_COUNT, f"Expected eight detected fixture loops, got {len(detected)}"
        assert len(fixture["mesh"].modifiers) == 2
        before = structure_snapshot(fixture["armature"], fixture["mesh"])
        assert before["bones"] and before["constraints"]
        assert len(key_snapshot(fixture["mesh"])) == 3
        print(f"PASS fixture {method}")


def test_start_cancel_and_confirm():
    for method in BUILD_METHODS:
        fixture = make_fixture(method)
        mesh, armature = fixture["mesh"], fixture["armature"]
        pose_target(fixture, 12.0, bend=0.09, side_wave=-0.06)
        before_pose = pose_snapshot(armature)
        before_structure = structure_snapshot(armature, mesh)
        before_keys = key_snapshot(mesh)
        before_surface = evaluated_points(mesh)
        before_lock = bpy.context.scene.render.use_lock_interface
        before_index = mesh.active_shape_key_index
        record = runtime.start_test(bpy.context, mesh, side="L")
        assert len(record["rings"]) == RING_COUNT
        assert len(mesh.data.shape_keys.key_blocks) == 4
        assert structure_snapshot(armature, mesh) == before_structure
        assert key_snapshot(mesh) == before_keys
        matrices = deformation_matrices(armature)
        actual_angle = geometry.twist_angle(matrices[fixture["lower_name"]], matrices[fixture["hand_name"]], fixture["axis"])
        assert abs(actual_angle - math.pi / 2) < 1.0e-4, f"{method}: test preview is not 90 degrees"
        assert_runtime_geometry(fixture, f"{method} initial 90-degree preview")
        runtime.set_ratio(bpy.context, 3, 0.31)
        assert abs(record_for(mesh)["rings"][3]["ratio"] - 0.31) < 1.0e-8
        assert_runtime_geometry(fixture, f"{method} edited loop")
        runtime.finish_test(bpy.context, confirm=False)
        assert runtime.RECORD_KEY not in mesh
        assert len(mesh.data.shape_keys.key_blocks) == 3
        assert_pose_snapshot(armature, before_pose, f"{method} cancel")
        assert structure_snapshot(armature, mesh) == before_structure
        assert key_snapshot(mesh) == before_keys
        assert mesh.active_shape_key_index == before_index
        assert bpy.context.scene.render.use_lock_interface == before_lock
        assert_points_close(evaluated_points(mesh), before_surface, f"{method} cancel surface")
        runtime.start_test(bpy.context, mesh)
        runtime.set_ratio(bpy.context, 3, 0.33)
        runtime.finish_test(bpy.context, confirm=True)
        assert_pose_snapshot(armature, before_pose, f"{method} confirm restores pose")
        assert abs(record_for(mesh)["rings"][3]["ratio"] - 0.33) < 1.0e-8
        assert_runtime_geometry(fixture, f"{method} confirmed original pose")
        before_json = mesh[runtime.RECORD_KEY]
        runtime.start_test(bpy.context, mesh)
        runtime.set_ratio(bpy.context, 3, 0.72)
        runtime.finish_test(bpy.context, confirm=False)
        assert mesh[runtime.RECORD_KEY] == before_json
        assert_pose_snapshot(armature, before_pose, f"{method} recalibration cancel")
        assert_runtime_geometry(fixture, f"{method} recalibration cancel geometry")
        for degrees in (-90, -45, 0, 45, 90):
            for bend in (0.0, 0.3):
                pose_target(fixture, degrees, bend=bend, side_wave=0.11, elbow_offset=0.025)
                assert_runtime_geometry(fixture, f"{method} motion {degrees}/{bend}")
        assert structure_snapshot(armature, mesh) == before_structure
        assert key_snapshot(mesh) == before_keys
        runtime.remove_calibration(bpy.context, mesh, "L")
        assert runtime.RECORD_KEY not in mesh
        assert len(mesh.data.shape_keys.key_blocks) == 3
        assert bpy.context.scene.render.use_lock_interface == before_lock
        print(f"PASS lifecycle {method}")


def test_unsupported_stack_is_transactional():
    fixture = make_fixture(BUILD_METHODS[0])
    mesh, armature = fixture["mesh"], fixture["armature"]
    before_pose = pose_snapshot(armature)
    before_keys = key_snapshot(mesh)
    mirror = mesh.modifiers.new("Unsupported preceding Mirror", "MIRROR")
    mesh.modifiers.move(len(mesh.modifiers) - 1, 0)
    before_structure = structure_snapshot(armature, mesh)
    assert_refused(lambda: runtime.start_test(bpy.context, mesh), "Preceding Mirror")
    assert_pose_snapshot(armature, before_pose, "Preceding Mirror refusal")
    assert structure_snapshot(armature, mesh) == before_structure
    assert key_snapshot(mesh) == before_keys
    assert runtime.RECORD_KEY not in mesh and runtime._SESSION is None
    mesh.modifiers.remove(mirror)
    mesh.modifiers[0].use_deform_preserve_volume = True
    assert_refused(lambda: runtime.start_test(bpy.context, mesh), "Preserve Volume")
    assert runtime.RECORD_KEY not in mesh and runtime._SESSION is None
    print("PASS unsupported stack transactional refusal")


def test_current_frame_animation_and_reload():
    fixture = make_fixture(BUILD_METHODS[0], side="R", transformed_objects=True)
    mesh, armature, target = fixture["mesh"], fixture["armature"], fixture["target"]
    runtime.start_test(bpy.context, mesh, side="R")
    runtime.set_ratio(bpy.context, 4, 0.58)
    runtime.finish_test(bpy.context, confirm=True)
    before_structure = structure_snapshot(armature, mesh)
    source_coordinates = {name: snapshot[3] for name, snapshot in key_snapshot(mesh).items()}
    for frame, degrees, value in ((1, -75.0, 0.0), (7, 0.0, 0.75), (13, 75.0, 0.2)):
        target.rotation_euler = (0.18, math.radians(degrees), -0.09)
        target.keyframe_insert(data_path="rotation_euler", frame=frame)
        key = mesh.data.shape_keys.key_blocks["ExistingForearm"]
        key.value = value
        key.keyframe_insert(data_path="value", frame=frame)
    surfaces = {}
    for frame in (1, 7, 13, 4, 10, 1, 13, 4):
        bpy.context.scene.frame_set(frame)
        assert_runtime_geometry(fixture, f"Animated same-frame {frame}")
        surface = evaluated_points(mesh)
        if frame in surfaces:
            assert_points_close(surface, surfaces[frame], f"No accumulated correction frame {frame}")
        surfaces[frame] = surface
    assert structure_snapshot(armature, mesh) == before_structure
    assert {name: snapshot[3] for name, snapshot in key_snapshot(mesh).items()} == source_coordinates
    assert_refused(lambda: runtime.start_test(bpy.context, mesh, side="R"), "Animated target calibration")
    assert runtime._SESSION is None
    mesh_name, armature_name, target_name = mesh.name, armature.name, target.name
    before_json = mesh[runtime.RECORD_KEY]
    with tempfile.TemporaryDirectory(prefix="character_designer_twist_") as temporary:
        path = os.path.join(temporary, "fixture.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=path)
        fixture["mesh"] = mesh = bpy.data.objects[mesh_name]
        fixture["armature"] = armature = bpy.data.objects[armature_name]
        fixture["target"] = armature.pose.bones[target_name]
        assert mesh[runtime.RECORD_KEY] == before_json
        for frame in (13, 1, 7, 4):
            bpy.context.scene.frame_set(frame)
            assert_runtime_geometry(fixture, f"Reloaded current-frame {frame}")
            assert_points_close(evaluated_points(mesh), surfaces[frame], f"Reloaded saved surface {frame}")
        assert structure_snapshot(armature, mesh) == before_structure
        runtime.remove_calibration(bpy.context, mesh, "R")
        assert runtime.RECORD_KEY not in mesh
    print("PASS right-arm unequal object transforms, current-frame animation, and save/reload")


def test_auto_align_preview_and_error_recovery():
    for method in BUILD_METHODS:
        fixture = make_fixture(method, forearm_key=False)
        mesh, armature = fixture["mesh"], fixture["armature"]
        bpy.context.view_layer.objects.active = armature
        armature.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")
        assert bpy.ops.character_designer.limb_ik_auto_align_target(action="ENABLE") == {"FINISHED"}
        activate_mesh(mesh)
        pose_target(fixture, -15.0, bend=0.14, side_wave=0.12, elbow_offset=0.02)
        before_pose = pose_snapshot(armature)
        before_structure = structure_snapshot(armature, mesh)
        runtime.start_test(bpy.context, mesh)
        assert_runtime_geometry(fixture, f"{method} Auto Align 90-degree calibration")
        bpy.context.window_manager.character_designer_forearm_twist.test_angle = -math.pi / 2
        assert_runtime_geometry(fixture, f"{method} Auto Align negative preview")
        matrices = deformation_matrices(armature)
        angle = geometry.twist_angle(matrices[fixture["lower_name"]], matrices[fixture["hand_name"]], fixture["axis"])
        assert abs(angle + math.pi / 2) < 1.0e-4
        runtime.finish_test(bpy.context, confirm=True)
        assert_pose_snapshot(armature, before_pose, f"{method} Auto Align confirm")
        pose_target(fixture, 160.0)
        runtime.update_runtime(bpy.context.scene, bpy.context.evaluated_depsgraph_get())
        assert mesh.name in runtime._ERRORS and "120" in runtime._ERRORS[mesh.name]
        key = mesh.data.shape_keys.key_blocks[record_for(mesh)["key"]]
        assert key.mute, "Out-of-range pose retained a stale correction"
        pose_target(fixture, 35.0, bend=0.12)
        assert_runtime_geometry(fixture, f"{method} recovered after unsupported twist")
        assert structure_snapshot(armature, mesh) == before_structure
        runtime.remove_calibration(bpy.context, mesh, "L")
        print(f"PASS Auto Align and angle recovery {method}")


def test_initial_basis_lifecycle():
    fixture = make_fixture(BUILD_METHODS[0])
    mesh, armature = fixture["mesh"], fixture["armature"]
    mesh.shape_key_clear()
    assert mesh.data.shape_keys is None
    before_pose = pose_snapshot(armature)
    before_structure = structure_snapshot(armature, mesh)
    before_points = evaluated_points(mesh)
    runtime.start_test(bpy.context, mesh)
    assert len(mesh.data.shape_keys.key_blocks) == 2
    runtime.finish_test(bpy.context, confirm=False)
    assert mesh.data.shape_keys is None
    assert runtime.RECORD_KEY not in mesh
    assert_pose_snapshot(armature, before_pose, "First-created Basis cancel")
    assert structure_snapshot(armature, mesh) == before_structure
    assert_points_close(evaluated_points(mesh), before_points, "First-created Basis cancel surface")
    runtime.start_test(bpy.context, mesh)
    runtime.finish_test(bpy.context, confirm=True)
    runtime.remove_calibration(bpy.context, mesh, "L")
    assert mesh.data.shape_keys is None
    assert structure_snapshot(armature, mesh) == before_structure
    print("PASS first-created Basis cleanup on cancel and remove")


def test_noncoplanar_loop_has_one_saved_share():
    fixture = make_fixture(BUILD_METHODS[0], forearm_key=False)
    mesh = fixture["mesh"]
    lower = fixture["armature"].data.bones[fixture["lower_name"]]
    basis = mesh.data.shape_keys.reference_key
    # Real character loops wander axially; the root loop may straddle the
    # elbow origin. Shift every existing key equally, preserving its deltas.
    for loop_index, spread in ((0, 0.012), (3, 0.035)):
        for spoke, vertex_index in enumerate(fixture["rings"][loop_index]):
            shift = fixture["axis"] * (lower.length * spread * math.cos(spoke * math.tau / RING_SIZE))
            for key in mesh.data.shape_keys.key_blocks:
                key.data[vertex_index].co += shift
            mesh.data.vertices[vertex_index].co = basis.data[vertex_index].co
    mesh.data.update()
    record = runtime.start_test(bpy.context, mesh)
    assert len(record["rings"]) == RING_COUNT
    positions = dict(zip(record["vertices"], record["positions"]))
    for ring in record["rings"]:
        assert all(positions[index] == ring["position"] for index in ring["vertices"]), \
            "Members of a captured loop did not retain one shared axial coordinate"
    root = record["rings"][0]
    raw_root = [(basis.data[index].co - lower.head_local).dot(fixture["axis"]) / lower.length
                for index in root["vertices"]]
    assert min(raw_root) < 0.0 < max(raw_root), "Root fixture must span both sides of elbow origin"
    edited = record["rings"][3]
    raw_interior = [(basis.data[index].co - lower.head_local).dot(fixture["axis"]) / lower.length
                    for index in edited["vertices"]]
    assert max(raw_interior) - min(raw_interior) > 0.05
    runtime.set_ratio(bpy.context, 3, 0.4)
    record = record_for(mesh)
    knots = [(0.0, 0.0)] + [(ring["position"], ring["ratio"]) for ring in record["rings"]
                           if 1.0e-6 < ring["position"] < 1.0 - 1.0e-6] + [(1.0, 1.0)]
    for index in edited["vertices"]:
        assert abs(geometry.profile_ratio(positions[index], knots) - 0.4) < 1.0e-8
    assert_runtime_geometry(fixture, "Noncoplanar loop exact saved share")
    runtime.finish_test(bpy.context, confirm=False)
    print("PASS noncoplanar loop and elbow-origin straddle use one saved share")


def test_managed_key_rename_recovery():
    fixture = make_fixture(BUILD_METHODS[0])
    mesh = fixture["mesh"]
    runtime.start_test(bpy.context, mesh)
    runtime.finish_test(bpy.context, True)
    pose_target(fixture, 65.0)
    record = record_for(mesh)
    owned = mesh.data.shape_keys.key_blocks[record["key"]]
    pointer = owned.as_pointer()
    owned.name = "Artist renamed managed output"
    runtime.update_runtime(bpy.context.scene)
    assert owned.name == record["key"] and owned.as_pointer() == pointer
    assert_runtime_geometry(fixture, "Active managed-key rename repair")
    # A new user key taking the required name must never be adopted/deleted.
    owned.name = "Renamed before collision"
    foreign = mesh.shape_key_add(name=record["key"], from_mix=False)
    foreign_pointer = foreign.as_pointer()
    runtime.update_runtime(bpy.context.scene)
    assert owned.mute and mesh.name in runtime._ERRORS
    assert_refused(lambda: runtime.remove_calibration(bpy.context, mesh, "L"), "Foreign key name collision")
    assert foreign.as_pointer() == foreign_pointer
    foreign.name = "Unrelated user key"
    runtime.update_runtime(bpy.context.scene)
    assert_runtime_geometry(fixture, "Recovered managed-key collision")
    # Cached ownership survives normal pose/profile invalidation and is repaired
    # before the serialized file is used by a fresh runtime reference cache.
    owned.name = "Renamed before save"
    runtime.update_runtime(bpy.context.scene)
    names = (mesh.name, fixture["armature"].name, fixture["target"].name)
    with tempfile.TemporaryDirectory(prefix="character_designer_twist_rename_") as temporary:
        path = os.path.join(temporary, "fixture.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=path)
        fixture["mesh"] = mesh = bpy.data.objects[names[0]]
        fixture["armature"] = bpy.data.objects[names[1]]
        fixture["target"] = fixture["armature"].pose.bones[names[2]]
        assert_runtime_geometry(fixture, "Reloaded managed name")
        owned = mesh.data.shape_keys.key_blocks[record["key"]]
        owned.name = "Renamed immediately before remove"
        runtime.remove_calibration(bpy.context, mesh, "L")
        assert runtime.RECORD_KEY not in mesh
        assert all(key.name not in {record["key"], "Renamed immediately before remove"}
                   for key in mesh.data.shape_keys.key_blocks)
        assert "Unrelated user key" in mesh.data.shape_keys.key_blocks
    # Without a surviving RNA reference (as after loading a file whose key was
    # renamed while the add-on was absent), removal must refuse ambiguous data.
    fixture = make_fixture(BUILD_METHODS[0])
    mesh = fixture["mesh"]
    runtime.start_test(bpy.context, mesh)
    runtime.finish_test(bpy.context, True)
    record = record_for(mesh)
    owned = mesh.data.shape_keys.key_blocks[record["key"]]
    owned.name = "Untracked renamed output"
    runtime._KEY_REFERENCES.clear()
    runtime.update_runtime(bpy.context.scene)
    assert "restore that exact name" in runtime._ERRORS[mesh.name]
    before_json = mesh[runtime.RECORD_KEY]
    assert_refused(lambda: runtime.remove_calibration(bpy.context, mesh, "L"), "Untracked missing managed name")
    assert mesh[runtime.RECORD_KEY] == before_json
    owned.name = record["key"]
    runtime.update_runtime(bpy.context.scene)
    runtime.remove_calibration(bpy.context, mesh, "L")
    assert runtime.RECORD_KEY not in mesh
    print("PASS managed-key rename repair, collision ownership, reload, and ambiguous removal refusal")


def test_existing_fk_without_generated_ik():
    for mode, side in (("QUATERNION", "L"), ("AXIS_ANGLE", "R"), ("ZYX", "L")):
        fixture = make_fixture(BUILD_METHODS[0], side=side, build_ik=False)
        mesh, armature, hand = fixture["mesh"], fixture["armature"], fixture["target"]
        assert not base.limb_ik._validate_inventory(armature)["rigs"]
        hand.rotation_euler = (0.11, 0.19, -0.04)
        hand.rotation_quaternion = Quaternion(Vector((0.2, 0.8, -0.3)).normalized(), 0.25)
        hand.rotation_axis_angle = (0.21, 0.0, 1.0, 0.0)
        hand.rotation_mode = mode
        lower = armature.pose.bones[fixture["lower_name"]]
        lower.rotation_mode = "XYZ"
        lower.rotation_euler = (0.18, -0.06, 0.04)
        armature.update_tag(refresh={"OBJECT"})
        bpy.context.view_layer.update()
        before_pose = pose_snapshot(armature)
        before_structure = structure_snapshot(armature, mesh)
        before_keys = key_snapshot(mesh)
        for confirm in (False, True):
            runtime.start_test(bpy.context, mesh, side=side)
            assert hand.rotation_mode == "XYZ"
            matrices = deformation_matrices(armature)
            angle = geometry.twist_angle(matrices[fixture["lower_name"]], matrices[fixture["hand_name"]], fixture["axis"])
            assert abs(angle - math.pi / 2) < 1.0e-4, f"FK {mode}: wrong preview axis"
            assert_runtime_geometry(fixture, f"Existing FK {mode} preview")
            runtime.set_ratio(bpy.context, 3, 0.37)
            runtime.finish_test(bpy.context, confirm)
            assert_pose_snapshot(armature, before_pose, f"Existing FK {mode} finish {confirm}")
            assert structure_snapshot(armature, mesh) == before_structure
            assert key_snapshot(mesh) == before_keys
            assert not base.limb_ik._validate_inventory(armature)["rigs"]
        assert_runtime_geometry(fixture, f"Existing FK {mode} saved correction")
        runtime.remove_calibration(bpy.context, mesh, side)
        constraint = hand.constraints.new("LIMIT_ROTATION")
        assert_refused(lambda: runtime.start_test(bpy.context, mesh, side), "Constrained FK hand")
        hand.constraints.remove(constraint)
        assert runtime.RECORD_KEY not in mesh
        assert structure_snapshot(armature, mesh) == before_structure
        print(f"PASS existing FK hand {mode}/{side}: no generated rig, complete pose restoration")


if __name__ == "__main__":
    check_fixture_generation()
    test_start_cancel_and_confirm()
    test_unsupported_stack_is_transactional()
    test_current_frame_animation_and_reload()
    test_auto_align_preview_and_error_recovery()
    test_initial_basis_lifecycle()
    test_noncoplanar_loop_has_one_saved_share()
    test_managed_key_rename_recovery()
    test_existing_fk_without_generated_ik()
