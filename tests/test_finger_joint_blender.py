"""Blender regression tests for the conservative finger-joint ring prototype."""

import math
import sys
from pathlib import Path

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import character_designer
from character_designer import finger_joint
from character_designer.ui_constants import UI_PAGE_MISC


EPSILON = 1.0e-6


def reset_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in list(bpy.data.collections):
        if collection.name == finger_joint.MARKER_COLLECTION_NAME and collection.users == 0:
            bpy.data.collections.remove(collection)


def make_fixture(with_shape_key=True):
    reset_scene()
    sides = 8
    rings = 4
    vertices = []
    for ring in range(rings):
        z = float(ring)
        for side in range(sides):
            angle = side * math.tau / sides
            vertices.append((0.25 * math.cos(angle), 0.20 * math.sin(angle), z))
    faces = []
    for ring in range(rings - 1):
        for side in range(sides):
            current = ring * sides + side
            next_side = ring * sides + (side + 1) % sides
            upper = (ring + 1) * sides
            faces.append((current, next_side, upper + (side + 1) % sides, upper + side))
    mesh = bpy.data.meshes.new("FingerJointFixtureMesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("FingerJointFixture", mesh)
    bpy.context.scene.collection.objects.link(obj)
    if with_shape_key:
        obj.shape_key_add(name="Basis")
        smile = obj.shape_key_add(name="Smile")
        smile.data[0].co.y += 0.05
        smile.data[9].co.x += 0.03
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.window_manager.character_designer.ui_page = UI_PAGE_MISC
    bpy.ops.object.mode_set(mode="EDIT")
    return obj


def select_ring(obj, ring_index):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    ring_vertices = {
        vertex for vertex in bm.verts
        if abs(vertex.co.z - float(ring_index)) <= EPSILON
    }
    for vertex in bm.verts:
        vertex.select_set(False)
    for edge in bm.edges:
        edge.select_set(
            edge.verts[0] in ring_vertices and edge.verts[1] in ring_vertices
        )
    for vertex in ring_vertices:
        vertex.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def z_ring_counts(obj):
    bpy.ops.object.mode_set(mode="OBJECT")
    counts = {}
    for vertex in obj.data.vertices:
        key = round(float(vertex.co.z), 5)
        counts[key] = counts.get(key, 0) + 1
    bpy.ops.object.mode_set(mode="EDIT")
    return counts


def test_create_three_rings_preserves_shape_keys_and_marks_joint():
    obj = make_fixture(with_shape_key=True)
    select_ring(obj, 1)
    settings = bpy.context.window_manager.character_designer_finger_joint
    settings.side_a_ratio = 0.25
    settings.side_b_ratio = 0.50
    before_shape_key_count = len(obj.data.shape_keys.key_blocks)
    before_smile = tuple(point.co.copy() for point in obj.data.shape_keys.key_blocks[1].data)
    result = bpy.ops.character_designer.finger_joint(action="CREATE")
    if result != {"FINISHED"}:
        raise AssertionError(f"Three-ring creation failed: {result}")
    if len(obj.data.shape_keys.key_blocks) != before_shape_key_count:
        raise AssertionError("Finger joint creation changed the Shape Key list")
    counts = z_ring_counts(obj)
    expected = {0.0: 8, 0.75: 8, 1.0: 8, 1.5: 8, 2.0: 8, 3.0: 8}
    if counts != expected:
        raise AssertionError(f"Unexpected generated ring positions: {counts}")
    markers = [
        marker for marker in bpy.data.objects
        if marker.get(finger_joint.OWNER_KEY) == finger_joint.OWNER_VALUE
    ]
    if len(markers) != 1:
        raise AssertionError(f"Expected one joint marker, got {len(markers)}")
    marker = markers[0]
    if marker.get("mesh_object") != obj.name or marker.get("status") != "three_rings_created":
        raise AssertionError("Joint marker metadata is incomplete")
    smile = obj.data.shape_keys.key_blocks.get("Smile")
    if smile is None or len(smile.data) != len(obj.data.vertices):
        raise AssertionError("Shape Key data was not extended with the new topology")
    for index, expected in enumerate(before_smile):
        if (smile.data[index].co - expected).length > EPSILON:
            raise AssertionError(f"Shape Key coordinate changed at original vertex {index}")


def test_check_is_read_only():
    obj = make_fixture(with_shape_key=False)
    select_ring(obj, 1)
    before_vertices = len(obj.data.vertices)
    result = bpy.ops.character_designer.finger_joint(action="CHECK")
    if result != {"FINISHED"}:
        raise AssertionError(f"Loop check failed on the regular fixture: {result}")
    if len(obj.data.vertices) != before_vertices:
        raise AssertionError("Loop check changed topology")
    if any(marker.get(finger_joint.OWNER_KEY) == finger_joint.OWNER_VALUE for marker in bpy.data.objects):
        raise AssertionError("Loop check created a marker")


def test_marked_center_loop_drives_creation_after_selection_changes():
    obj = make_fixture(with_shape_key=False)
    select_ring(obj, 1)
    settings = bpy.context.window_manager.character_designer_finger_joint
    settings.side_a_ratio = 0.35
    settings.side_b_ratio = 0.35
    result = bpy.ops.character_designer.finger_joint(action="MARK")
    if result != {"FINISHED"} or not settings.marked_signature:
        raise AssertionError("Mark Center Loop did not lock the selected loop")
    select_ring(obj, 0)
    result = bpy.ops.character_designer.finger_joint(action="CHECK")
    if result != {"FINISHED"}:
        raise AssertionError(f"Marked center loop was not used by Check: {result}")
    result = bpy.ops.character_designer.finger_joint(action="CREATE")
    if result != {"FINISHED"}:
        raise AssertionError(f"Marked center loop was not used by Create: {result}")
    counts = z_ring_counts(obj)
    if counts.get(1.0) != 8 or counts.get(0.65) != 8 or counts.get(1.65) != 8:
        raise AssertionError(f"Creation did not use the locked ring: {counts}")
    if settings.marked_signature:
        raise AssertionError("Successful creation did not clear the temporary loop mark")


def test_same_seed_refuses_duplicate_generation():
    obj = make_fixture(with_shape_key=False)
    select_ring(obj, 1)
    result = bpy.ops.character_designer.finger_joint(action="CREATE")
    if result != {"FINISHED"}:
        raise AssertionError(f"Initial three-ring creation failed: {result}")
    original_count = len(obj.data.vertices)
    select_ring(obj, 1)
    result = bpy.ops.character_designer.finger_joint(action="CREATE")
    if result != {"CANCELLED"}:
        raise AssertionError("Repeated generation did not refuse the marked loop")
    if len(obj.data.vertices) != original_count:
        raise AssertionError("Repeated generation changed topology")


def main():
    character_designer.register()
    tests = (
        test_create_three_rings_preserves_shape_keys_and_marks_joint,
        test_check_is_read_only,
        test_marked_center_loop_drives_creation_after_selection_changes,
        test_same_seed_refuses_duplicate_generation,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        reset_scene()
        character_designer.unregister()
    print(f"PASS Finger Joint Tools {len(tests)} tests")


if __name__ == "__main__":
    main()
