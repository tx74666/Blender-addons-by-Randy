"""Synthetic integration checks for non-destructive hair strand discovery.

Run with Blender factory preferences; no addon registration is needed::

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/test_hair_bones_topology_blender.py

This script creates disposable meshes in memory and never opens or saves a blend.
"""

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
from character_designer import CenterlineError
from character_designer import hair_bones_topology as topology


EPSILON = 2.0e-6


class Fixture:
    def __init__(self):
        self.vertices = []
        self.faces = []

    def vertex(self, point):
        self.vertices.append(tuple(point))
        return len(self.vertices) - 1

    def tube(self, sides=5, rows=5, origin=(0, 0, 0), drift=0.0, root=None):
        """Quad tube with one projected, triangle-fan point tip."""
        origin = Vector(origin)
        layers = [] if root is None else [tuple(root)]
        first = 0 if root is None else 1
        for row in range(first, rows):
            center = origin + Vector((drift * row, 0.02 * row * row, row * 0.55))
            radius = 0.20 - row * 0.018
            layer = tuple(
                self.vertex(center + Vector((radius * math.cos(i * math.tau / sides),
                                             radius * math.sin(i * math.tau / sides), 0)))
                for i in range(sides)
            )
            layers.append(layer)
        for previous, current in zip(layers, layers[1:]):
            for i in range(sides):
                j = (i + 1) % sides
                self.faces.append((previous[i], previous[j], current[j], current[i]))
        tip_center = origin + Vector((drift * rows, 0.02 * rows * rows, rows * 0.55))
        tip = self.vertex(tip_center)
        for i in range(sides):
            self.faces.append((layers[-1][i], layers[-1][(i + 1) % sides], tip))
        layers.append((tip,))
        return tuple(layers)

    def card(self, rows=6, width=3):
        layers = []
        for row in range(rows):
            layers.append(tuple(self.vertex(((i - (width - 1) / 2) * 0.16,
                                             0.025 * row * row, row * 0.6))
                                for i in range(width)))
        for previous, current in zip(layers, layers[1:]):
            for i in range(width - 1):
                self.faces.append((previous[i], previous[i + 1], current[i + 1], current[i]))
        return tuple(layers)

    def object(self, name):
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        mesh = bpy.data.meshes.new(name + "Mesh")
        mesh.from_pydata(self.vertices, [], self.faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        # Existing artist data must survive discovery and strand selection.
        obj.shape_key_add(name="Basis")
        artist = obj.shape_key_add(name="ArtistKey")
        artist.data[0].co.x += 0.012
        obj.vertex_groups.new(name="ArtistWeights").add([0], 0.75, "REPLACE")
        modifier = obj.modifiers.new("ArtistSubdivision", "SUBSURF")
        modifier.show_viewport = False
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.tool_settings.mesh_select_mode = (True, False, False)
        select(obj, ())
        return obj


def bm_for(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    return bm


def select(obj, indices, active=None):
    bm = bm_for(obj)
    selected = set(indices)
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(vertex.index in selected and not vertex.hide)
    bm.select_flush_mode()
    bm.select_history.clear()
    if active is not None:
        bm.select_history.add(bm.verts[active])
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def flattened(layers):
    return frozenset(index for layer in layers for index in layer)


def geometry_snapshot(obj):
    bm = bm_for(obj)
    keys = obj.data.shape_keys
    return {
        "objects": tuple(sorted((item.name, item.as_pointer(), item.type) for item in bpy.data.objects)),
        "meshes": tuple(sorted((item.name, item.as_pointer()) for item in bpy.data.meshes)),
        "vertices": tuple((tuple(v.co), v.hide) for v in bm.verts),
        "edges": tuple((tuple(v.index for v in edge.verts), edge.hide) for edge in bm.edges),
        "faces": tuple((tuple(v.index for v in face.verts), face.hide) for face in bm.faces),
        "matrix": tuple(tuple(row) for row in obj.matrix_world),
        "groups": tuple(group.name for group in obj.vertex_groups),
        "weights": tuple(tuple((group.group, group.weight) for group in vertex.groups)
                         for vertex in obj.data.vertices),
        "keys": tuple((key.name, key.value, tuple(tuple(point.co) for point in key.data))
                      for key in keys.key_blocks),
        "modifiers": tuple((m.name, m.type, m.show_viewport, m.show_render) for m in obj.modifiers),
        "mode": obj.mode,
    }


def selection_snapshot(obj):
    bm = bm_for(obj)
    return (tuple(v.select for v in bm.verts), tuple(e.select for e in bm.edges),
            tuple(f.select for f in bm.faces),
            tuple((type(item).__name__, item.index) for item in bm.select_history))


def discover(obj, **kwargs):
    geometry = geometry_snapshot(obj)
    selection = selection_snapshot(obj)
    result_obj, plans = topology.discover_strands(bpy.context, **kwargs)
    assert result_obj is obj
    assert isinstance(plans, tuple)
    assert geometry_snapshot(obj) == geometry, "Discovery changed artist geometry or objects"
    assert selection_snapshot(obj) == selection, "Discovery changed the selection"
    validate_plans(obj, plans)
    return plans


def validate_plans(obj, plans):
    bm = bm_for(obj)
    signatures = []
    for plan in plans:
        assert isinstance(plan, dict)
        layers, centers = plan["layers"], plan["centers"]
        assert isinstance(layers, tuple) and len(layers) >= 2
        assert isinstance(centers, tuple) and len(centers) == len(layers)
        assert isinstance(plan["vertices"], tuple)
        assert len(plan["vertices"]) == len(set(plan["vertices"]))
        assert set(plan["vertices"]) == flattened(layers)
        assert sum(len(layer) for layer in layers) == len(plan["vertices"])
        assert isinstance(plan["signature"], str) and plan["signature"]
        assert isinstance(plan["direction_confirmable"], bool)
        assert isinstance(plan["root_tip_rule"], str) and plan["root_tip_rule"]
        for layer, center in zip(layers, centers):
            assert isinstance(layer, tuple) and layer
            assert all(not bm.verts[index].hide for index in layer)
            expected = sum((bm.verts[index].co for index in layer), Vector()) / len(layer)
            assert (Vector(center) - expected).length < EPSILON
        signatures.append(plan["signature"])
    assert len(signatures) == len(set(signatures)), "Duplicate strand plans"


def find_vertices(plans, vertices):
    matching = [plan for plan in plans if set(plan["vertices"]) == set(vertices)]
    assert len(matching) == 1, (len(matching), sorted(vertices), plans)
    return matching[0]


def test_projected_tubes():
    for sides in (3, 5):
        fixture = Fixture()
        layers = fixture.tube(sides=sides)
        obj = fixture.object(f"TopologyTube{sides}")
        plans = discover(obj)
        assert len(plans) == 1
        plan = find_vertices(plans, flattened(layers))
        assert plan["layers"] == layers, "A projected point must remain the tip"
        assert plan["direction_confirmable"]


def test_open_card_direction_and_signature():
    fixture = Fixture()
    layers = fixture.card()
    obj = fixture.object("TopologyOpenCard")
    first = discover(obj)
    assert len(first) == 1
    original = find_vertices(first, flattened(layers))
    assert all(len(layer) == 3 for layer in original["layers"])
    assert original["direction_confirmable"]
    # Reversing the object's world height reverses the directional evidence,
    # while the strand's vertex partition and identity remain the same.
    obj.matrix_world = Matrix.Rotation(math.pi, 4, "X")
    second = discover(obj)
    reversed_plan = find_vertices(second, flattened(layers))
    assert reversed_plan["layers"] == tuple(reversed(original["layers"]))
    assert reversed_plan["signature"] == original["signature"]


def test_disjoint_seeds_hidden_and_coordinate_cache():
    fixture = Fixture()
    first = fixture.tube(sides=3, origin=(-1.5, 0, 0))
    second = fixture.tube(sides=5, origin=(1.5, 0, 0))
    obj = fixture.object("TopologyDisjoint")
    assert len(discover(obj)) == 2
    select(obj, first[-1], active=first[-1][0])
    seeded = discover(obj)
    assert len(seeded) == 1
    find_vertices(seeded, flattened(first))
    # Restricting to the single selected tip must never expand beyond it.
    try:
        restricted = discover(obj, selected_only=True)
    except CenterlineError:
        restricted = ()
    assert not restricted
    select(obj, flattened(second))
    restricted = discover(obj, selected_only=True)
    assert len(restricted) == 1
    find_vertices(restricted, flattened(second))

    select(obj, ())
    bm = bm_for(obj)
    for index in flattened(second):
        bm.verts[index].hide_set(True)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    visible = discover(obj)
    assert len(visible) == 1
    find_vertices(visible, flattened(first))
    bm = bm_for(obj)
    for vertex in bm.verts:
        vertex.hide_set(False)
    for edge in bm.edges:
        edge.hide_set(False)
    for face in bm.faces:
        face.hide_set(False)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    select(obj, ())

    before = geometry_snapshot(obj)
    result_obj, selected = topology.select_strands(bpy.context)
    assert result_obj is obj and len(selected) == 2
    validate_plans(obj, selected)
    assert geometry_snapshot(obj) == before, "Select Strands changed source data"
    assert {v.index for v in bm_for(obj).verts if v.select} == flattened(first + second)
    result_obj, cached = topology.selected_strands(bpy.context)
    assert result_obj is obj and cached == selected

    translation = Vector((0.125, -0.2, 0.3))
    old_centers = {plan["signature"]: tuple(plan["centers"]) for plan in selected}
    for vertex in bm_for(obj).verts:
        vertex.co += translation
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    result_obj, moved = topology.selected_strands(bpy.context)
    assert result_obj is obj and len(moved) == 2
    validate_plans(obj, moved)
    for plan in moved:
        previous = old_centers[plan["signature"]]
        for center, old in zip(plan["centers"], previous):
            assert (Vector(center) - Vector(old) - translation).length < EPSILON, \
                "Selection cache returned stale centers after coordinates changed"


def test_welded_branch_and_exact_selection_cache():
    fixture = Fixture()
    sides = 5
    junction = tuple(fixture.vertex((0.2 * math.cos(i * math.tau / sides),
                                     0.2 * math.sin(i * math.tau / sides), 0))
                     for i in range(sides))
    first = fixture.tube(sides=sides, root=junction, drift=-0.32)
    second = fixture.tube(sides=sides, root=junction, drift=0.32)
    previous = junction
    trunk = []
    for row in (1, 2):
        current = tuple(fixture.vertex((0.2 * math.cos(i * math.tau / sides),
                                       0.2 * math.sin(i * math.tau / sides), -row * 0.55))
                        for i in range(sides))
        trunk.extend(current)
        for i in range(sides):
            j = (i + 1) % sides
            fixture.faces.append((previous[i], current[i], current[j], previous[j]))
        previous = current
    obj = fixture.object("TopologyWeldedBranch")
    plans = discover(obj)
    assert len(plans) >= 2
    for branch, other in ((first, second), (second, first)):
        tip = branch[-1][0]
        matches = [plan for plan in plans if tip in plan["vertices"]]
        assert len(matches) == 1, "A branch tip must identify exactly one strand"
        plan = matches[0]
        assert len(plan["layers"]) >= 3
        assert set(plan["vertices"]) <= flattened(branch), "A strand crossed the welded fork"
        assert not (set(plan["vertices"]) & (flattened(other) - set(junction)))
        assert not (set(plan["vertices"]) & set(trunk))

    before = geometry_snapshot(obj)
    result_obj, selected = topology.select_strands(bpy.context)
    assert result_obj is obj and selected == plans
    assert geometry_snapshot(obj) == before
    selection = selection_snapshot(obj)
    result_obj, cached = topology.selected_strands(bpy.context)
    assert result_obj is obj and cached == plans, \
        "The expanded connected selection lost its separate strand plans"
    assert selection_snapshot(obj) == selection
    assert geometry_snapshot(obj) == before


def main():
    assert bpy.app.background
    completed = []
    for test in (test_projected_tubes, test_open_card_direction_and_signature,
                 test_disjoint_seeds_hidden_and_coordinate_cache,
                 test_welded_branch_and_exact_selection_cache):
        test()
        completed.append(test.__name__)
        print("HAIR_TOPOLOGY_TEST=" + json.dumps({"test": test.__name__, "status": "passed"}))
    print("HAIR_TOPOLOGY_RESULT=" + json.dumps({"status": "passed", "tests": completed}))


if __name__ == "__main__":
    main()
