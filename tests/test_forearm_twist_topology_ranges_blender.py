"""Read-only forearm loop capture, adjacent expansion, and manual completion."""
import math
import os
import sys

import bpy
from mathutils import Matrix

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addons"))
from character_designer import forearm_twist_topology as topology


def sleeve(*, break_strip=False):
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    data = bpy.data.armatures.new("LoopRig")
    arm = bpy.data.objects.new("LoopRig", data)
    bpy.context.collection.objects.link(arm)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    lower = data.edit_bones.new("Forearm")
    lower.head, lower.tail = (0, 0, 0), (0, 1, 0)
    hand = data.edit_bones.new("Hand")
    hand.head, hand.tail, hand.parent = (0, 1, 0), (0, 1.2, 0), lower
    bpy.ops.object.mode_set(mode="OBJECT")
    positions = [-.12] + [index / 8 for index in range(9)] + [1.12]
    points, rings, faces = [], [], []
    for position in positions:
        ids = []
        for spoke in range(12):
            angle = math.tau * spoke / 12
            ids.append(len(points))
            points.append((math.cos(angle) * .09, position, math.sin(angle) * .09))
        rings.append(ids)
    for index in range(len(rings) - 1):
        for spoke in range(12):
            other = (spoke + 1) % 12
            face = (rings[index][spoke], rings[index + 1][spoke], rings[index + 1][other], rings[index][other])
            if break_strip and index == 4 and spoke == 0:
                faces.extend(((face[0], face[1], face[2]), (face[0], face[2], face[3])))
            else:
                faces.append(face)
    mesh = bpy.data.meshes.new("LoopMesh")
    mesh.from_pydata(points, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("LoopMesh", mesh)
    bpy.context.collection.objects.link(obj)
    obj.vertex_groups.new(name="Forearm").add(list(range(len(points))), 1.0, "REPLACE")
    obj.vertex_groups.new(name="Hand")
    obj.shape_key_add(name="Basis")
    shaped = obj.shape_key_add(name="ArtistShape")
    for vertex in shaped.data:
        vertex.co.x += .03
    shaped.value = .65
    common = Matrix.Translation((.7, -.3, .2)) @ Matrix.Rotation(.41, 4, "Z") @ Matrix.Scale(1.3, 4)
    arm.matrix_world = common
    obj.matrix_world = Matrix.Translation((-.2, .6, -.4)) @ Matrix.Rotation(-.27, 4, "X") @ Matrix.Scale(.91, 4)
    obj.data.transform(obj.matrix_world.inverted() @ common, shape_keys=True)
    return obj, arm, rings


def snapshot(obj):
    mesh = obj.data
    return (tuple(tuple(vertex.co) for vertex in mesh.vertices),
            tuple(tuple(edge.vertices) for edge in mesh.edges),
            tuple(tuple(face.vertices) for face in mesh.polygons),
            tuple(tuple((group.group, group.weight) for group in vertex.groups) for vertex in mesh.vertices),
            tuple((key.name, key.value, tuple(tuple(vertex.co) for vertex in key.data)) for key in mesh.shape_keys.key_blocks),
            tuple(vertex.select for vertex in mesh.vertices), tuple(edge.select for edge in mesh.edges))


def refused(function, phrase):
    try:
        function()
    except topology.ForearmTopologyError as exc:
        assert phrase in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected explicit refusal containing {phrase!r}")


def test_capture_and_expand_preserve_base_ids():
    obj, arm, rings = sleeve()
    before = snapshot(obj)
    seed = rings[5][4:] + rings[5][:4]
    seed.reverse()
    captured = topology.capture_loop(obj, arm, "Forearm", seed)
    assert captured["vertices"] == seed
    assert abs(captured["position"] - .5) < 1e-6
    neighbors = topology.adjacent_rings(obj, arm, "Forearm", seed)
    assert [set(ring["vertices"]) for ring in neighbors] == [set(rings[4]), set(rings[6])]
    expanded = topology.expand_rings(obj, arm, "Forearm", seed)
    assert len(expanded) == 9
    assert [set(ring["vertices"]) for ring in expanded] == [set(ids) for ids in rings[1:-1]]
    assert expanded[4]["vertices"] == seed
    detected = topology.detect_rings(obj, arm, "Forearm", "Hand")
    assert [set(ring["vertices"]) for ring in detected] == [set(ids) for ids in rings[1:-1]]
    assert snapshot(obj) == before


def test_manual_append_retains_captured_order_and_metadata():
    obj, arm, rings = sleeve(break_strip=True)
    before = snapshot(obj)
    first = topology.capture_loop(obj, arm, "Forearm", list(reversed(rings[3])))
    last = topology.capture_loop(obj, arm, "Forearm", rings[6][2:] + rings[6][:2])
    first["ratio"], last["ratio"] = .2, .8
    captured = [first, last]
    copied = topology.append_ring(obj, arm, "Forearm", captured, rings[5])
    assert len(copied) == 3 and copied[0] == first and copied[2] == last
    assert copied[0] is not first and copied[0]["vertices"] is not first["vertices"]
    assert copied[1]["vertices"] == rings[5]
    assert captured == [first, last]
    refused(lambda: topology.append_ring(obj, arm, "Forearm", copied, rings[5]), "already captured")
    refused(lambda: topology.adjacent_rings(obj, arm, "Forearm", rings[4]), "non-quad")
    diagnostics = []
    expanded = topology.expand_rings(obj, arm, "Forearm", rings[2], diagnostics=diagnostics)
    assert all(ring["position"] < .5 for ring in expanded)
    assert len(diagnostics) == 1 and "non-quad" in diagnostics[0]
    # An explicit artist endpoint may extend beyond the automatically bounded span.
    assert topology.capture_loop(obj, arm, "Forearm", rings[-1])["position"] > 1.0
    assert snapshot(obj) == before


def test_invalid_manual_paths_are_refused():
    obj, arm, rings = sleeve()
    refused(lambda: topology.capture_loop(obj, arm, "Forearm", rings[4][:-1]), "open")
    refused(lambda: topology.capture_loop(obj, arm, "Forearm", rings[4] + rings[6]), "disconnected")
    refused(lambda: topology.capture_loop(obj, arm, "Forearm", rings[4] + rings[5][:2]), "branches")
    refused(lambda: topology.capture_loop(obj, arm, "Forearm", rings[4] + [rings[4][0]]), "duplicates")
    refused(lambda: topology.capture_loop(obj, arm, "Forearm", [-1, 0, 1, 2]), "IDs")
    refused(lambda: topology.expand_rings(obj, arm, "Forearm", rings[0]), "between")
    original = topology.capture_loop(obj, arm, "Forearm", rings[4])
    damaged = {**original, "vertices": rings[4][::2] + rings[4][1::2]}
    refused(lambda: topology.append_ring(obj, arm, "Forearm", [damaged], rings[6]), "ordering")


def test_selected_edges_in_object_and_edit_mode_are_read_only():
    obj, arm, rings = sleeve()
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    target = set(rings[5])
    for vertex in obj.data.vertices:
        vertex.select = vertex.index in target
    for edge in obj.data.edges:
        edge.select = set(edge.vertices) <= target
    for face in obj.data.polygons:
        face.select = False
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    before = snapshot(obj)
    result = topology.selected_loop(obj, arm, "Forearm")
    assert set(result["vertices"]) == target
    assert snapshot(obj) == before
    bpy.ops.object.mode_set(mode="EDIT")
    import bmesh
    editable = bmesh.from_edit_mesh(obj.data)
    selected_before = tuple(edge.select for edge in editable.edges)
    result = topology.selected_loop(obj, arm, "Forearm")
    assert set(result["vertices"]) == target
    assert tuple(edge.select for edge in editable.edges) == selected_before
    assert bpy.context.mode == "EDIT_MESH"
    bpy.ops.object.mode_set(mode="OBJECT")
    assert snapshot(obj) == before


if __name__ == "__main__":
    tests = [value for name, value in globals().copy().items() if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__, flush=True)
    print("FOREARM_TOPOLOGY_RANGES_TESTS_PASS", len(tests), flush=True)
