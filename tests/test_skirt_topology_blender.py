"""Non-destructive skirt discovery, fit and weight checks under Blender.

Run with --background --factory-startup --disable-autoexec --python-exit-code 1.
If X.blend is present, its Dress is appended into this disposable session for
read-only analysis.  No source blend is saved or edited.
"""

import hashlib
import json
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
from character_designer import skirt_topology as topology


def frustum(name="TestSkirt", rows=10, sides=32, permute=False, pleats=0.0):
    vertices, faces = [], []
    for row in range(rows):
        t = row / (rows - 1)
        radius = 0.5 + 0.5 * t
        for column in range(sides):
            angle = math.tau * column / sides
            fold = 1 + pleats * math.cos(8 * angle) * t
            vertices.append((0.1 * t + radius * math.cos(angle) * fold,
                             0.04 * t + 0.8 * radius * math.sin(angle) * fold, 2 - t))
    for row in range(rows - 1):
        for column in range(sides):
            following = (column + 1) % sides
            faces.append((row * sides + column, (row + 1) * sides + column,
                          (row + 1) * sides + following, row * sides + following))
    if permute:
        order = list(range(len(vertices)))
        random.Random(21).shuffle(order)
        inverse = {old: new for new, old in enumerate(order)}
        vertices = [vertices[old] for old in order]
        faces = [tuple(inverse[index] for index in face) for face in reversed(faces)]
    return mesh_object(name, vertices, faces)


def mesh_object(name, vertices, faces, edges=()):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, edges, faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def state(obj):
    return (tuple(tuple(vertex.co) for vertex in obj.data.vertices),
            tuple(tuple(edge.vertices) for edge in obj.data.edges),
            tuple(tuple(face.vertices) for face in obj.data.polygons),
            tuple((group.name, group.index) for group in obj.vertex_groups),
            tuple((modifier.name, modifier.type) for modifier in obj.modifiers),
            tuple(tuple(row) for row in obj.matrix_world), obj.mode, obj.select_get(),
            len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.armatures),
            len(bpy.data.curves), bpy.context.view_layer.objects.active,
            None if obj.data.shape_keys is None else
            tuple((key.name, tuple(tuple(point.co) for point in key.data))
                  for key in obj.data.shape_keys.key_blocks))


def checked_plan(obj, chains=8, segments=4):
    before = state(obj)
    result = topology.analyze_skirt(obj, chains, segments)
    assert state(obj) == before, "Analysis mutated artist data or context"
    assert result == topology.analyze_skirt(obj, chains, segments), "Plan is nondeterministic"
    json.dumps(result, allow_nan=False)
    assert sorted(index for ring in result["rings"] for index in ring) == list(range(len(obj.data.vertices)))
    assert len(result["chains"]) == chains
    assert all(len(chain) == segments + 1 for chain in result["chains"])
    assert abs(result["ring_t"][0]) < 1e-5
    assert abs(result["ring_t"][-1] - 1) < 1e-5
    for index in result["rings"][0]:
        assert result["vertex_weights"][index] == [[0, -1, 1.0]], "Waist not rigidly anchored"
    for influences in result["vertex_weights"]:
        assert 1 <= len(influences) <= 4
        assert abs(sum(weight for _, _, weight in influences) - 1) < 1e-12
        assert all(0 < weight <= 1 for _, _, weight in influences)
        assert all(0 <= chain < chains and -1 <= segment < segments for chain, segment, _ in influences)
        angular = sorted({chain for chain, segment, _ in influences if segment >= 0})
        assert len(angular) <= 2
        if len(angular) == 2:
            assert (angular[1] - angular[0]) in {1, chains - 1}, "Non-neighboring chain crossover"
        longitudinal = sorted({segment for _, segment, _ in influences})
        assert len(longitudinal) <= 2
        if len(longitudinal) == 2:
            assert longitudinal[1] - longitudinal[0] == 1
    for chain_index, chain in enumerate(result["chains"]):
        for segment, point in enumerate(chain):
            expected = topology.sample_fit(result, segment / segments, math.tau * chain_index / chains)
            assert (Vector(point) - Vector(expected)).length < 1e-5
    return result


def expect_error(obj, expected=None, **kwargs):
    before = state(obj)
    try:
        topology.analyze_skirt(obj, **kwargs)
    except topology.SkirtTopologyError as error:
        if expected:
            assert expected in str(error), str(error)
    else:
        raise AssertionError("Malformed skirt unexpectedly accepted: " + obj.name)
    assert before == state(obj)


def test_exact_ellipse_and_weight_continuity():
    obj = frustum(rows=17, sides=64)
    obj.shape_key_add(name="Basis")
    artist = obj.shape_key_add(name="ArtistShape")
    artist.data[3].co.x += 0.015
    obj.vertex_groups.new(name="ArtistGroup").add([5], 0.37, "REPLACE")
    obj.modifiers.new(name="ArtistSubdivision", type="SUBSURF")
    plan = checked_plan(obj)
    assert plan["ring_count"] == 17 and plan["columns_per_ring"] == 64
    assert plan["hem_radius_world"] > plan["waist_radius_world"]
    for ring, fitted in zip(plan["rings"], plan["fitted_rings"]):
        for index, point in zip(ring, fitted):
            assert (obj.data.vertices[index].co - Vector(point)).length < 2e-6
    # The circumference seam blends only the final and first chain.
    seam = plan["vertex_weights"][plan["rings"][-1][-1]]
    assert {chain for chain, _, _ in seam} == {0, 7}
    assert all(segment == 3 for _, segment, _ in seam)
    # First-harmonic approximation removes alternating pleats in the cage only.
    pleated = frustum("Pleated", rows=10, sides=64, pleats=0.15)
    pleated_plan = checked_plan(pleated)
    assert max((Vector(point) - pleated.data.vertices[index].co).length
               for index, point in zip(pleated_plan["rings"][-1], pleated_plan["fitted_rings"][-1])) > 0.1


def test_permuted_vertices_and_nonuniform_transform():
    obj = frustum("Transformed", permute=True)
    obj.matrix_world = (Matrix.Translation((3.1, -1.2, 0.7)) @ Matrix.Rotation(0.47, 4, "Z")
                        @ Matrix.Diagonal((0.72996455, 1.3, 1.8, 1.0)))
    bpy.context.view_layer.update()
    plan = checked_plan(obj, chains=7, segments=5)
    assert abs(plan["height_world"] - 1.8) < 2e-6
    for chain in plan["chains"]:
        levels = [(obj.matrix_world @ Vector(point)).z for point in chain]
        assert all(a > b for a, b in zip(levels, levels[1:]))
    # Raw vertex indexing and polygon creation order cannot change fitted rows.
    for ring, fitted in zip(plan["rings"], plan["fitted_rings"]):
        for index, point in zip(ring, fitted):
            assert (obj.data.vertices[index].co - Vector(point)).length < 4e-6
    signature = plan["signature"]
    obj.location.x += 0.1
    bpy.context.view_layer.update()
    assert topology.analyze_skirt(obj)["signature"] == signature
    # Reflection and unapplied rotation are supported through world-space fit.
    obj.scale.x *= -1
    bpy.context.view_layer.update()
    checked_plan(obj)


def test_edit_mesh_read_only_and_whole_mesh():
    obj = frustum("EditSkirt")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="DESELECT")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts[5].select_set(True)
        bm.verts[7].co.x += 0.004
        bm.select_history.add(bm.verts[5])
        snapshot = (tuple(tuple(vertex.co) for vertex in bm.verts),
                    tuple(vertex.select for vertex in bm.verts),
                    tuple(element.index for element in bm.select_history))
        plan = checked_plan(obj)
        assert (tuple(tuple(vertex.co) for vertex in bm.verts),
                tuple(vertex.select for vertex in bm.verts),
                tuple(element.index for element in bm.select_history)) == snapshot
        assert len(plan["vertex_weights"]) == len(bm.verts)
        assert plan["vertices"][7] == list(bm.verts[7].co)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def test_malformed_topology_and_transform():
    regular = frustum("MalformedBase", rows=5, sides=16)
    vertices = [tuple(vertex.co) for vertex in regular.data.vertices]
    faces = [tuple(face.vertices) for face in regular.data.polygons]
    expect_error(mesh_object("MissingQuad", vertices, faces[:-1]))
    expect_error(mesh_object("Slit", vertices, [face for index, face in enumerate(faces) if index % 16 != 0]))
    expect_error(mesh_object("DuplicateFace", vertices, faces + faces[:1]), "nonmanifold")
    expect_error(mesh_object("Triangle", vertices, faces[:-1] + [faces[-1][:3]]), "quads")
    expect_error(mesh_object("LoosePoint", vertices + [(0, 0, 4)], faces), "connected")
    expect_error(mesh_object("LooseEdge", vertices, faces, [(0, 2)]), "loose")
    disconnected_vertices = vertices + [(x + 4, y, z) for x, y, z in vertices]
    disconnected_faces = faces + [tuple(index + len(vertices) for index in face) for face in faces]
    expect_error(mesh_object("TwoSkirts", disconnected_vertices, disconnected_faces), "connected")
    expect_error(regular, chain_count=17)
    expect_error(regular, chain_count=2)
    expect_error(regular, segment_count=0)
    regular.scale.z = 0
    bpy.context.view_layer.update()
    expect_error(regular, "singular")
    regular.scale.z = 1
    regular.rotation_euler.y = math.pi / 2
    bpy.context.view_layer.update()
    expect_error(regular)
    # A folded middle row is topologically regular, but ambiguous for a cage.
    folded = frustum("Folded", rows=5)
    for vertex in folded.data.vertices[64:96]:
        vertex.co.z = 1.9
    expect_error(folded, "descend")
    twisted = frustum("Twisted", rows=9)
    for vertex in twisted.data.vertices:
        t = 2 - vertex.co.z
        center = Vector((0.1 * t, 0.04 * t, vertex.co.z))
        vertex.co = center + Matrix.Rotation(math.pi * t, 3, "Z") @ (vertex.co - center)
    expect_error(twisted, "twist")


def test_real_x_and_elaina_if_present():
    paths = [(ROOT / "X.blend", "Dress", 10, 80),
             (ROOT.parent / "Elaina" / "Ex1.blend", "Elaina_Skirt.002", 15, 128)]
    for path, name, rows, columns in paths:
        if not path.exists():
            print("SKIP real skirt file absent:", path)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with bpy.data.libraries.load(str(path), link=False) as (available, loaded):
            assert name in available.objects
            loaded.objects = [name]
        obj = loaded.objects[0]
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        result = checked_plan(obj)
        assert (result["ring_count"], result["columns_per_ring"]) == (rows, columns)
        assert result["hem_radius_world"] > result["waist_radius_world"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        print("REAL_SKIRT_TOPOLOGY=" + json.dumps({"source": str(path), "object": name,
            "rows": rows, "columns": columns, "signature": result["signature"],
            "height_world": result["height_world"], "warnings": result["warnings"]}))


def main():
    tests = (test_exact_ellipse_and_weight_continuity, test_permuted_vertices_and_nonuniform_transform,
             test_edit_mesh_read_only_and_whole_mesh, test_malformed_topology_and_transform,
             test_real_x_and_elaina_if_present)
    for test in tests:
        test()
        print("PASS", test.__name__, flush=True)
    print("SKIRT_TOPOLOGY_PASS", len(tests), flush=True)


if __name__ == "__main__":
    main()
