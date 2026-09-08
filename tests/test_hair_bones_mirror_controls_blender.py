"""Full, independently deforming Mirror hair controls; disposable Blender scenes.

The fixture has one ordinary side strand and a central half tube whose two
longitudinal boundaries meet the X mirror plane.  It must create three real
deform chains, with one continuous central tube, without applying the Mirror.
Run in Blender with --background --factory-startup --disable-autoexec
--python-exit-code 1 --python tests/test_hair_bones_mirror_controls_blender.py.
"""

import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

import bpy
from mathutils import Euler, Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import hair_bones_rig as rig
from character_designer import hair_bones_variants as variants
from test_hair_bones_rig_blender import (
    activate, assert_points, evaluated_points, make_armature, plain_snapshot,
    reset, weights,
)
from test_hair_bones_variants_blender import database_counts, grouped, rig_pose, source_state


def fixture(*, transformed=False):
    reset()
    original = make_armature()
    activate(original, "EDIT")
    original.data.edit_bones["spine.006"].tail.y += 0.004
    original.data.edit_bones["spine.006"].roll = 0.04
    activate(original)
    vertices, faces, plans = [], [], []
    for central in (False, True):
        layers, centers = [], []
        for row in range(7):
            z = 2.0 - row * 0.2
            profile = ((0.0, -0.12, z), (0.16, 0.0, z), (0.0, 0.12, z)) if central else tuple(
                (0.65 + 0.075 * math.cos(corner * math.tau / 4),
                 0.075 * math.sin(corner * math.tau / 4), z) for corner in range(4))
            layer = tuple(range(len(vertices), len(vertices) + len(profile)))
            vertices.extend(profile)
            layers.append(layer)
            centers.append(tuple(sum((Vector(point) for point in profile), Vector()) / len(profile)))
        for upper, lower in zip(layers, layers[1:]):
            for corner in range(2 if central else 4):
                following = (corner + 1) % len(upper)
                faces.append((upper[corner], upper[following], lower[following], lower[corner]))
        plans.append({"signature": "central-half" if central else "side-half",
                      "layers": tuple(layers), "centers": tuple(centers),
                      "vertices": tuple(index for layer in layers for index in layer),
                      "direction_confirmable": True, "root_tip_rule": "EXPLICIT"})
    data = bpy.data.meshes.new("MirrorHairFixtureMesh")
    data.from_pydata(vertices, [], faces)
    data.update()
    source = bpy.data.objects.new("MirrorHairFixture", data)
    bpy.context.scene.collection.objects.link(source)
    mirror = source.modifiers.new("Artist Mirror", "MIRROR")
    mirror.use_axis = (True, False, False)
    mirror.use_clip = True
    mirror.use_mirror_merge = True
    mirror.merge_threshold = 0.001
    source["artist_note"] = "Keep source and prior versions intact"
    if transformed:
        original.matrix_world = Matrix.LocRotScale(Vector((1.2, -0.8, 0.4)),
                                                   Euler((0.13, -0.21, 0.34)).to_quaternion(),
                                                   Vector((1.2, 1.2, 1.2)))
        source.matrix_world = Matrix.LocRotScale(Vector((0.4, -0.7, 0.2)),
                                                 Euler((-0.11, 0.19, -0.27)).to_quaternion(),
                                                 Vector((1.1, 0.8, 1.4)))
    activate(source)
    return source, tuple(plans), original


def evaluated_topology(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    data = evaluated.to_mesh()
    try:
        adjacency = {vertex.index: set() for vertex in data.vertices}
        for edge in data.edges:
            a, b = edge.vertices
            adjacency[a].add(b)
            adjacency[b].add(a)
        remaining, components = set(adjacency), []
        while remaining:
            pending, component = [remaining.pop()], set()
            while pending:
                current = pending.pop()
                component.add(current)
                new = adjacency[current] & remaining
                remaining.difference_update(new)
                pending.extend(new)
            components.append(frozenset(component))
        return (len(data.vertices), len(data.polygons), frozenset(components))
    finally:
        evaluated.to_mesh_clear()


def classify(result, baseline):
    """Classify by geometry, independently of the implementation's metadata."""
    mesh, arm = result["mesh"], result["armature"]
    inverse = mesh.matrix_world.inverted()
    indices = {side: [] for side in ("L", "R", "C")}
    for index, point in enumerate(baseline):
        local = inverse @ point
        indices["L" if local.x > 0.4 else "R" if local.x < -0.4 else "C"].append(index)
    chains = {}
    assert len(result["chains"]) == 3, "One side strand and its mirror, plus ONE central chain"
    for chain in result["chains"]:
        assert len(chain["bones"]) == 3
        root = arm.data.bones[chain["bones"][0]]
        x = (inverse @ arm.matrix_world @ root.head_local).x
        side = "L" if x > 0.4 else "R" if x < -0.4 else "C"
        assert side not in chains, ("Duplicate or one-sided controls", side)
        assert all(name.endswith("." + side) for name in chain["bones"]), chain
        assert all(arm.data.bones[name].use_deform for name in chain["bones"])
        if side == "C":
            for name in chain["bones"]:
                bone = arm.data.bones[name]
                assert abs((inverse @ arm.matrix_world @ bone.head_local).x) < 2e-6
                assert abs((inverse @ arm.matrix_world @ bone.tail_local).x) < 2e-6
        chains[side] = chain
    assert set(chains) == set(indices) and all(indices.values())
    return chains, indices


def build_fixture(*, transformed=False):
    source, plans, original = fixture(transformed=transformed)
    before = source_state(source)
    baseline = evaluated_points(source)
    result = variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=3)
    assert source_state(source) == before
    assert_points(evaluated_points(result["mesh"]), baseline, "Mirror binding has no rest jump")
    modifiers = tuple(result["mesh"].modifiers)
    mirror = next(item for item in modifiers if item.type == "MIRROR")
    armature_modifier = next(item for item in modifiers if item.type == "ARMATURE")
    assert modifiers.index(mirror) < modifiers.index(armature_modifier), "Both mesh halves must exist before skinning"
    assert mirror.mirror_object is None, "Object-local Mirror does not need a moving reference object"
    assert tuple(mirror.use_axis) == (True, False, False) and mirror.use_mirror_vertex_groups
    assert len(result["mesh"].data.vertices) == len(source.data.vertices), "Do not apply Mirror to source or copy"
    record = rig._read_records(result["mesh"])
    assert record["version"] == 3 and record["mirror_layout"] == "BEFORE_ARMATURE_X"
    assert len(record["chains"]) == 3
    chains, indices = classify(result, baseline)
    source_weights = weights(result["mesh"])
    # Mirror maps source-side groups to real opposite-side groups.  Merely
    # duplicating visible bones or retaining Armature-before-Mirror fails this.
    for name in chains["R"]["bones"]:
        assert result["mesh"].vertex_groups.get(name) is not None
        assert all(values.get(name, 0.0) == 0.0 for values in source_weights.values())
    assert evaluated_topology(result["mesh"])[:2] == (84, 72)
    assert len(evaluated_topology(result["mesh"])[2]) == 3
    return source, plans, original, result, chains, indices, baseline


def test_both_sides_are_independent_deform_controls():
    source, plans, original, result, chains, indices, baseline = build_fixture()
    topology = evaluated_topology(result["mesh"])
    for side, opposite in (("L", "R"), ("R", "L")):
        pose = result["armature"].pose.bones[chains[side]["bones"][0]]
        pose.rotation_mode = "XYZ"
        pose.rotation_euler.x = 0.35
        pose.location.y = 0.08
        actual = evaluated_points(result["mesh"])
        assert max((actual[i] - baseline[i]).length for i in indices[side]) > 0.2
        for unchanged in (opposite, "C"):
            assert_points(tuple(actual[i] for i in indices[unchanged]),
                          tuple(baseline[i] for i in indices[unchanged]),
                          f"{side} control must leave {unchanged} hair unchanged")
        assert evaluated_topology(result["mesh"]) == topology
        pose.matrix_basis = Matrix.Identity(4)
        assert_points(evaluated_points(result["mesh"]), baseline, "Returning FK to rest")


def test_central_chain_moves_the_whole_welded_strand():
    source, plans, original, result, chains, indices, baseline = build_fixture()
    topology = evaluated_topology(result["mesh"])
    arm = result["armature"]
    first = arm.pose.bones[chains["C"]["bones"][0]]
    first.rotation_mode = "XYZ"
    first.rotation_euler = (0.25, 0.18, -0.12)
    first.location.x = 0.21
    actual = evaluated_points(result["mesh"])
    assert evaluated_topology(result["mesh"]) == topology, "Moving central hair cannot reopen its mirror seam"
    inverse = result["mesh"].matrix_world.inverted()
    tip_indices = tuple(i for i in indices["C"] if abs((inverse @ baseline[i]).z - 0.8) < 1e-5)
    assert len(tip_indices) == 4
    last = arm.pose.bones[chains["C"]["bones"][-1]]
    skin = arm.matrix_world @ last.matrix @ last.bone.matrix_local.inverted() @ arm.matrix_world.inverted()
    assert_points(tuple(actual[i] for i in tip_indices), tuple(skin @ baseline[i] for i in tip_indices),
                  "Both halves and shared seam follow the same central control")
    assert max((actual[i] - baseline[i]).length for i in tip_indices) > 0.2
    for side in ("L", "R"):
        assert_points(tuple(actual[i] for i in indices[side]), tuple(baseline[i] for i in indices[side]),
                      "Central chain must not move either separate side strand")


def test_grouped_central_and_side_share_one_centered_chain():
    source, plans, original = fixture()
    before_source, baseline = source_state(source), evaluated_points(source)
    result = variants.build_variant(bpy.context, source, grouped(plans), mode="GROUPED", bone_count=3)
    mesh, arm = result["mesh"], result["armature"]
    assert source_state(source) == before_source
    assert_points(evaluated_points(mesh), baseline, "A mixed central/side group binds without moving either half")
    assert len(result["chains"]) == 1 and len(arm.data.bones) == 4, "One shared chain plus head attachment"
    names = result["chains"][0]["bones"]
    assert len(names) == 3 and all(name.endswith(".C") for name in names)
    inverse = mesh.matrix_world.inverted()
    for name in names:
        bone = arm.data.bones[name]
        assert abs((inverse @ arm.matrix_world @ bone.head_local).x) < 2e-6
        assert abs((inverse @ arm.matrix_world @ bone.tail_local).x) < 2e-6
    record = rig._read_records(mesh)
    assert record["version"] == 3 and len(record["chains"]) == 1
    assert len(record["chains"][0]["members"]) == 2
    topology = evaluated_topology(mesh)
    assert topology[:2] == (84, 72) and len(topology[2]) == 3
    tip_indices = tuple(index for index, point in enumerate(baseline)
                        if abs((inverse @ point).z - 0.8) < 1e-5)
    assert len(tip_indices) == 12, "Both side tubes and the complete central tip must be included"
    source_weights = weights(mesh)
    for plan in plans:
        for index in plan["layers"][-1]:
            assert source_weights[index] == {names[-1]: 1.0}
    first = arm.pose.bones[names[0]]
    first.rotation_mode = "XYZ"
    first.rotation_euler = (0.24, -0.18, 0.16)
    first.location.x = 0.19
    actual = evaluated_points(mesh)
    last = arm.pose.bones[names[-1]]
    delta = arm.matrix_world @ last.matrix @ last.bone.matrix_local.inverted() @ arm.matrix_world.inverted()
    assert_points(tuple(actual[i] for i in tip_indices), tuple(delta @ baseline[i] for i in tip_indices),
                  "Full-weight tips across the entire mirrored group share exactly one control transform")
    assert min((actual[i] - baseline[i]).length for i in tip_indices) > 0.1
    assert evaluated_topology(mesh) == topology, "Grouped central seam remains welded during asymmetric movement"
    root_indices = tuple(index for index, point in enumerate(baseline)
                         if abs((inverse @ point).z - 2.0) < 1e-5)
    assert_points(tuple(actual[i] for i in root_indices), tuple(baseline[i] for i in root_indices),
                  "All grouped roots remain attached to the head")


def test_nonunit_mesh_and_character_transform_follow_without_jump():
    source, plans, original, result, chains, indices, baseline = build_fixture(transformed=True)
    pose = result["armature"].pose.bones[chains["R"]["bones"][0]]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler.x = -0.23
    before_head = evaluated_points(result["mesh"])
    head = original.pose.bones["spine.006"]
    head.rotation_mode = "XYZ"
    head.rotation_euler = (0.17, -0.21, 0.32)
    head.location = (0.08, -0.04, 0.03)
    bpy.context.view_layer.update()
    delta = original.matrix_world @ head.matrix @ head.bone.matrix_local.inverted() @ original.matrix_world.inverted()
    assert_points(evaluated_points(result["mesh"]), tuple(delta @ point for point in before_head),
                  "All native Mirror controls follow posed character head")
    old_world = original.matrix_world.copy()
    before_object = evaluated_points(result["mesh"])
    original.matrix_world = Matrix.LocRotScale(Vector((-0.3, 0.7, 1.1)),
                                              Euler((-0.2, 0.1, 0.4)).to_quaternion(),
                                              Vector((0.9, 0.9, 0.9)))
    object_delta = original.matrix_world @ old_world.inverted()
    assert_points(evaluated_points(result["mesh"]), tuple(object_delta @ point for point in before_object),
                  "Moving and scaling the character also moves the complete mirrored hair")
    return source, original, result, chains, indices


def test_regeneration_preserves_source_and_hand_edited_prior_version():
    source, plans, original, a, chains, indices, baseline = build_fixture()
    before_source = source_state(source)
    tip = plans[0]["layers"][-1][0]
    a["mesh"].vertex_groups[chains["L"]["bones"][-1]].add([tip], 0.91, "REPLACE")
    pose = a["armature"].pose.bones[chains["R"]["bones"][0]]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler.x = -0.28
    pose.keyframe_insert("rotation_euler", frame=1)
    before_weights, before_pose = weights(a["mesh"]), rig_pose(a["armature"])
    before_action = a["armature"].animation_data.action
    before_points = evaluated_points(a["mesh"])
    b = variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=3)
    assert len(variants.variants_for(source)) == 2
    assert a["mesh"].data is not b["mesh"].data and a["armature"].data is not b["armature"].data
    assert source_state(source) == before_source
    assert weights(a["mesh"]) == before_weights and rig_pose(a["armature"]) == before_pose
    assert a["armature"].animation_data.action is before_action
    assert_points(evaluated_points(b["mesh"]), baseline, "New generation starts from original source")
    variants.show_variant(bpy.context, a)
    assert_points(evaluated_points(a["mesh"]), before_points, "Old artist edits survive new generation")


def test_unsupported_multi_axis_is_atomic():
    source, plans, original = fixture()
    source.modifiers[0].use_axis = (True, True, False)
    activate(source, "EDIT", vertices=plans[0]["vertices"])
    before, counts = plain_snapshot(source), database_counts()
    try:
        variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=3)
        raise AssertionError("Multi-axis Mirror must stop before creating a partial rig")
    except (variants.HairVariantError, rig.HairBonesRigError) as exc:
        assert "mirror" in str(exc).lower(), str(exc)
    assert plain_snapshot(source) == before and database_counts() == counts
    assert tuple(source.modifiers[0].use_axis) == (True, True, False)
    assert not variants.variants_for(source)


def test_disabled_group_flip_with_artist_pair_is_atomic():
    source, plans, original = fixture()
    mirror = source.modifiers[0]
    mirror.use_mirror_vertex_groups = False
    source.vertex_groups.new(name="ArtistMask.L").add([0, 1], 0.72, "REPLACE")
    source.vertex_groups.new(name="ArtistMask.R").add([1, 2], 0.36, "REPLACE")
    activate(source, "EDIT", vertices=plans[1]["vertices"])
    before, counts = plain_snapshot(source), database_counts()
    try:
        variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=3)
        raise AssertionError("Enabling Mirror group flipping must not change existing artist masks")
    except (variants.HairVariantError, rig.HairBonesRigError) as exc:
        assert "flipping disabled" in str(exc).lower(), str(exc)
    assert plain_snapshot(source) == before and database_counts() == counts
    assert not mirror.use_mirror_vertex_groups and not variants.variants_for(source)


def test_native_reopen_and_edit_without_addon():
    source, original, result, chains, indices = test_nonunit_mesh_and_character_transform_follow_without_jump()
    pose = result["armature"].pose.bones[chains["C"]["bones"][0]]
    pose.location.x = 0.11
    points = evaluated_points(result["mesh"])
    payload = {"mesh": result["mesh"].name, "rig": result["armature"].name,
               "left_control": chains["L"]["bones"][0], "left": indices["L"], "right": indices["R"],
               "points": [list(point) for point in points], "topology": evaluated_topology(result["mesh"])[:2]}
    with tempfile.TemporaryDirectory(prefix="hair-mirror-controls-") as directory:
        directory = Path(directory)
        blend, expected, check = directory / "mirror-controls.blend", directory / "expected.json", directory / "check.py"
        expected.write_text(json.dumps(payload), encoding="utf-8")
        check.write_text('''import bpy, json, sys
from mathutils import Vector
from pathlib import Path
assert "character_designer" not in sys.modules
record = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
obj, arm = bpy.data.objects[record["mesh"]], bpy.data.objects[record["rig"]]
def points():
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        assert (len(mesh.vertices), len(mesh.polygons)) == tuple(record["topology"])
        return tuple(obj.matrix_world @ vertex.co for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()
before = points()
assert len(before) == len(record["points"])
error = max((point - Vector(expected)).length for point, expected in zip(before, record["points"]))
assert error < 8e-5, error
pose = arm.pose.bones[record["left_control"]]
pose.rotation_mode = "XYZ"
pose.rotation_euler.x += 0.3
after = points()
assert max((after[i] - before[i]).length for i in record["left"]) > 0.15
assert max((after[i] - before[i]).length for i in record["right"]) < 8e-5
assert "character_designer" not in sys.modules
print("HAIR_MIRROR_CONTROLS_NATIVE_REOPEN_OK")
''', encoding="utf-8")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
        process = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                                  str(blend), "--python-exit-code", "1", "--python", str(check), "--", str(expected)],
                                 capture_output=True, text=True, timeout=120)
        assert process.returncode == 0 and "HAIR_MIRROR_CONTROLS_NATIVE_REOPEN_OK" in process.stdout, (process.stdout, process.stderr)
    print("HAIR_MIRROR_CONTROLS_NATIVE_REOPEN_OK")


if __name__ == "__main__":
    for test in (test_both_sides_are_independent_deform_controls,
                 test_central_chain_moves_the_whole_welded_strand,
                 test_grouped_central_and_side_share_one_centered_chain,
                 test_nonunit_mesh_and_character_transform_follow_without_jump,
                 test_regeneration_preserves_source_and_hand_edited_prior_version,
                 test_unsupported_multi_axis_is_atomic,
                 test_disabled_group_flip_with_artist_pair_is_atomic,
                 test_native_reopen_and_edit_without_addon):
        test()
        print("PASS", test.__name__)
    print("HAIR_BONES_MIRROR_CONTROLS_TESTS_OK")
