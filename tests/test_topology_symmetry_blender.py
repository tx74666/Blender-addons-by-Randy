"""Blender regression tests for selection-driven Topology Mirror.

Run in a disposable Blender session.  The fixture has a source patch with one
interior vertex and an opposite patch with two interior vertices, so a
successful replacement proves that topology, Shape Keys, UVs and weights are
all transferred without touching the selected source patch.
"""

import math
import sys
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
import character_designer
from character_designer import topology_symmetry as topology
from character_designer import weight_symmetry as ws


def reset_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def add_group(obj, name, weights, locked=False):
    group = obj.vertex_groups.new(name=name)
    for index, value in weights.items():
        group.add((index,), value, "REPLACE")
    group.lock_weight = locked
    return group


def make_fixture():
    reset_scene()
    armature = bpy.data.armatures.new("TopologyRig.Data")
    rig = bpy.data.objects.new("TopologyRig", armature)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for name, x in (("hand.L", 2.0), ("hand.R", -2.0)):
        bone = armature.edit_bones.new(name)
        bone.head = (x, 0, 0)
        bone.tail = (x, 1, 0)
    spine = armature.edit_bones.new("spine")
    spine.head = (0, 0, 0)
    spine.tail = (0, 1, 0)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Each patch has a four-vertex wrist boundary in the YZ plane.  The left
    # source is a four-triangle fan; the right target is deliberately a
    # different six-triangle layout with two interior vertices.
    vertices = [
        (2.0, -1.0, -1.0), (2.0, 1.0, -1.0),
        (2.0, 1.0, 1.0), (2.0, -1.0, 1.0), (3.5, 0.0, 0.0),
        (-2.05, -1.0, -1.0), (-2.05, 1.0, -1.0),
        (-2.05, 1.0, 1.0), (-2.05, -1.0, 1.0),
        (-2.8, -0.1, 0.2), (-3.5, 0.2, -0.15),
        (0.0, 3.0, 0.0), (0.0, 3.5, 0.0), (0.0, 3.0, 0.5),
    ]
    faces = [
        (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4),
        (5, 6, 9), (6, 7, 9), (7, 10, 9),
        (7, 8, 10), (8, 5, 10), (5, 9, 10),
        (11, 12, 13),
    ]
    mesh = bpy.data.meshes.new("TopologyMesh.Data")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    obj = bpy.data.objects.new("TopologyMesh", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = rig
    obj.shape_key_add(name="Basis")
    artist = obj.shape_key_add(name="ArtistExpression")
    artist.value = 0.4
    artist.data[4].co.y += 0.25
    artist.data[10].co.z -= 0.2
    uv = mesh.uv_layers.new(name="ArtistUV")
    for loop in mesh.loops:
        uv.data[loop.index].uv = (loop.vertex_index * 0.1, loop.index * 0.01)
    add_group(obj, "hand.L", {0: 0.8, 1: 0.8, 2: 0.8, 3: 0.8, 4: 1.0})
    add_group(obj, "hand.R", {5: 0.2, 6: 0.2, 7: 0.2, 8: 0.2, 9: 0.3, 10: 0.4})
    add_group(obj, "spine", {index: 0.2 for index in range(len(vertices))})
    add_group(obj, "ArtistMask", {index: 0.1 + index * 0.01 for index in range(len(vertices))})
    obj.vertex_groups.active_index = obj.vertex_groups["hand.R"].index
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(face.index < 4)
    for vertex in bm.verts:
        vertex.select_set(vertex.index < 5)
    bmesh.update_edit_mesh(mesh)
    return obj, rig


def group_map(obj, name):
    group = obj.vertex_groups[name]
    return {
        vertex.index: next(
            (membership.weight for membership in vertex.groups
             if membership.group == group.index),
            None,
        )
        for vertex in obj.data.vertices
        if any(membership.group == group.index for membership in vertex.groups)
    }


def shape_map(obj, name):
    key = obj.data.shape_keys.key_blocks[name]
    return tuple(tuple(point.co) for point in key.data)


def assert_close(actual, expected):
    if not math.isclose(float(actual), float(expected), abs_tol=1.0e-6):
        raise AssertionError((actual, expected))


def test_different_topology_replaced_with_mirrored_source():
    obj, rig = make_fixture()
    source_before = tuple(tuple(obj.data.vertices[index].co) for index in range(5))
    source_shape_before = shape_map(obj, "ArtistExpression")[:5]
    source_weights_before = group_map(obj, "hand.L")
    artist_value = obj.data.shape_keys.key_blocks["ArtistExpression"].value
    uv_names = tuple(layer.name for layer in obj.data.uv_layers)
    plan = topology.build_topology_mirror_plan(bpy.context)
    assert plan.target_name == "hand.R"
    assert plan.source_name == "hand.L"
    assert len(plan.target_boundary) == 4
    assert len(plan.source_faces) == 4
    assert len(plan.target_faces) == 6
    bpy.ops.object.mode_set(mode="OBJECT")
    result = topology.apply_topology_mirror_plan(plan)
    assert result.removed_faces == 6
    assert result.added_faces == 4
    assert result.removed_vertices == 2
    assert result.added_vertices == 1
    assert set(result.selected_source_vertices) == {0, 1, 2, 3, 4}
    assert set(result.selected_target_vertices) == {5, 6, 7, 8, 12}
    assert set(result.selected_vertices) == {0, 1, 2, 3, 4, 5, 6, 7, 8, 12}
    assert not ({9, 10, 11} & set(result.selected_vertices))
    assert len(obj.data.vertices) == 13
    assert len(obj.data.polygons) == 9
    assert tuple(tuple(obj.data.vertices[index].co) for index in range(5)) == source_before
    assert shape_map(obj, "ArtistExpression")[:5] == source_shape_before
    assert obj.data.shape_keys.key_blocks["ArtistExpression"].value == artist_value
    assert tuple(layer.name for layer in obj.data.uv_layers) == uv_names
    # The new target interior is the mirrored source center vertex.
    new_point = obj.data.vertices[-1].co
    assert (new_point - Vector((-3.5, 0.0, 0.0))).length < 1.0e-6
    new_shape_point = obj.data.shape_keys.key_blocks["ArtistExpression"].data[-1].co
    assert (new_shape_point - Vector((-3.5, 0.25, 0.0))).length < 1.0e-6
    for target_index, source_index in zip((5, 6, 7, 8), (0, 1, 2, 3)):
        assert (obj.data.vertices[target_index].co -
                Vector((-source_before[source_index][0],
                        source_before[source_index][1],
                        source_before[source_index][2]))).length < 1.0e-6
    for index, weight in source_weights_before.items():
        assert_close(group_map(obj, "hand.L")[index], weight)
    target_weights = group_map(obj, "hand.R")
    assert_close(target_weights[5], source_weights_before[0])
    assert_close(target_weights[12], source_weights_before[4])
    assert_close(group_map(obj, "ArtistMask")[12], 0.14)
    topology._select_result_region(obj, result.selected_vertices)
    bm = bmesh.from_edit_mesh(obj.data)
    selected_after = {vertex.index for vertex in bm.verts if vertex.select}
    assert selected_after == set(result.selected_vertices)
    selected_faces_after = {face.index for face in bm.faces if face.select}
    assert selected_faces_after == {0, 1, 2, 3, 5, 6, 7, 8}
    bpy.ops.object.mode_set(mode="OBJECT")


def test_boundary_mismatch_refuses_without_writes():
    obj, _rig = make_fixture()
    # Move one source wrist vertex away from its reflected target counterpart.
    obj.data.vertices[0].co.x = 2.25
    before = (
        tuple(tuple(vertex.co) for vertex in obj.data.vertices),
        ws._capture_vertex_groups(obj),
    )
    try:
        topology.build_topology_mirror_plan(bpy.context)
    except topology.TopologySymmetryError as error:
        assert "matches" in str(error).lower() or "ambiguous" in str(error).lower()
    else:
        raise AssertionError("Boundary mismatch unexpectedly passed")
    assert tuple(tuple(vertex.co) for vertex in obj.data.vertices) == before[0]
    assert ws._capture_vertex_groups(obj) == before[1]


def test_locked_target_refuses_before_mesh_swap():
    obj, _rig = make_fixture()
    obj.vertex_groups["hand.R"].lock_weight = True
    plan = topology.build_topology_mirror_plan(bpy.context)
    bpy.ops.object.mode_set(mode="OBJECT")
    before_geometry = tuple(tuple(vertex.co) for vertex in obj.data.vertices)
    before_groups = ws._capture_vertex_groups(obj)
    try:
        topology.apply_topology_mirror_plan(plan)
    except topology.TopologySymmetryError as error:
        assert "locked" in str(error).lower()
    else:
        raise AssertionError("Locked target unexpectedly changed")
    assert tuple(tuple(vertex.co) for vertex in obj.data.vertices) == before_geometry
    assert ws._capture_vertex_groups(obj) == before_groups


def test_selected_source_overrides_active_group_side():
    obj, _rig = make_fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(4 <= face.index < 10)
    for vertex in bm.verts:
        vertex.select_set(5 <= vertex.index < 11)
    bmesh.update_edit_mesh(obj.data)
    plan = topology.build_topology_mirror_plan(bpy.context)
    assert plan.source_name == "hand.R"
    assert plan.target_name == "hand.L"
    assert plan.source_faces == (4, 5, 6, 7, 8, 9)
    assert len(plan.target_faces) == 4
    bpy.ops.object.mode_set(mode="OBJECT")
    result = topology.apply_topology_mirror_plan(plan)
    assert result.source_name == "hand.R"
    assert result.target_name == "hand.L"
    assert len(obj.data.polygons) == 13


def test_repair_selection_fills_hole_and_welds_opposite_boundary():
    obj, _rig = make_fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    target_faces = [face for face in bm.faces if 4 <= face.index < 10]
    bmesh.ops.delete(bm, geom=target_faces, context="FACES_ONLY")
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)
    plan = topology.build_topology_repair_plan(bpy.context, merge_distance=0.1)
    assert plan.source_name == "hand.L"
    assert plan.target_name == "hand.R"
    assert sum(target is not None for _source, target in plan.vertex_pairs) == 4
    assert sum(target is None for _source, target in plan.vertex_pairs) == 1
    bpy.ops.object.mode_set(mode="OBJECT")
    result = topology.apply_topology_repair_plan(plan)
    assert result.source_name == "hand.L"
    assert result.target_name == "hand.R"
    assert result.added_faces == 4
    assert result.welded_vertices == 4
    assert result.added_vertices == 1
    assert result.removed_faces == 0
    assert len(obj.data.polygons) == 9
    assert len(obj.data.vertices) == 15
    # The four existing target boundary vertices are welded to the reflected
    # source positions; the missing center is newly created.
    for target_index, source_index in zip((5, 6, 7, 8), (0, 1, 2, 3)):
        expected = Vector(obj.data.vertices[source_index].co)
        expected.x = -expected.x
        assert (obj.data.vertices[target_index].co - expected).length < 1.0e-6
    assert (obj.data.vertices[-1].co - Vector((-3.5, 0.0, 0.0))).length < 1.0e-6
    target_weights = group_map(obj, "hand.R")
    assert_close(target_weights[5], 0.8)
    assert_close(target_weights[14], 1.0)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bpy.ops.object.mode_set(mode="OBJECT")


def test_centerline_virtual_boundary_accepts_source_touching_center():
    obj, _rig = make_fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for index in (0, 1):
        bm.verts[index].co.x = 0.0
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    descriptor = topology.build_boundary_descriptor(bpy.context)
    assert descriptor.source_side == 1
    assert descriptor.boundary_type == "CENTERLINE_VIRTUAL"
    assert set(descriptor.center_vertices) == {0, 1}
    assert set(descriptor.selected_vertices) == {0, 1, 2, 3, 4}
    assert len(descriptor.physical_boundary) == 4
    bpy.ops.object.mode_set(mode="OBJECT")


def test_registration_and_operator_poll():
    reset_scene()
    character_designer.register()
    try:
        cls = topology.CHARACTERDESIGNER_OT_topology_mirror
        assert "UNDO" in cls.bl_options
        assert bpy.ops.character_designer.topology_mirror.get_rna_type()
        assert bpy.ops.character_designer.topology_mirror_repair.get_rna_type()
        assert bpy.ops.character_designer.analyze_topology_boundary.get_rna_type()
        assert not cls.poll(bpy.context)
    finally:
        character_designer.unregister()


TESTS = (
    test_different_topology_replaced_with_mirrored_source,
    test_boundary_mismatch_refuses_without_writes,
    test_locked_target_refuses_before_mesh_swap,
    test_selected_source_overrides_active_group_side,
    test_repair_selection_fills_hole_and_welds_opposite_boundary,
    test_centerline_virtual_boundary_accepts_source_touching_center,
    test_registration_and_operator_poll,
)


if __name__ == "__main__":
    failures = []
    for test in TESTS:
        try:
            test()
        except Exception as error:
            failures.append((test.__name__, error))
            traceback.print_exc()
            print("FAIL", test.__name__, repr(error))
        else:
            print("PASS", test.__name__)
    if failures:
        raise SystemExit(1)
    print(f"PASS Topology Mirror {len(TESTS)} tests")
