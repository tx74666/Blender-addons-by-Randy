"""Find genuine circumferential quad loops in a deform forearm's rest mesh.

Detection is read-only: vertex ids always refer to the unmodified base mesh.
The winding check excludes short surface loops and the deform weights exclude
the other arm, clothing, and nearby torso geometry. Poles and open paths are
not guessed into rings.
"""

from __future__ import annotations

import math

_EPSILON = 1.0e-8
_END_PADDING = 0.08
_RING_END_PADDING = 0.025
_MAX_AXIAL_SPREAD = 0.20
_MAX_EDGE_AXIAL_COSINE = 0.60


def _closed_loop(start_edge, start_vertex, edges, vertex_edges, edge_faces):
    """Follow the opposite edge at regular four-edge quad vertices."""
    path = [start_vertex]
    used = set()
    edge_index = start_edge
    vertex_index = start_vertex
    while edge_index not in used:
        if len(edge_faces[edge_index]) != 2:
            return None
        used.add(edge_index)
        first, second = edges[edge_index]
        vertex_index = second if vertex_index == first else first
        closing = vertex_index == start_vertex
        if (vertex_index in path and not closing) or len(vertex_edges[vertex_index]) != 4:
            return None
        next_edges = [
            candidate
            for candidate in vertex_edges[vertex_index]
            if candidate != edge_index
            and not edge_faces[candidate].intersection(edge_faces[edge_index])
        ]
        if len(next_edges) != 1:
            return None
        if closing:
            return path if len(path) >= 4 and next_edges[0] == start_edge else None
        path.append(vertex_index)
        edge_index = next_edges[0]
    return None


def detect_rings(mesh_obj, armature, lower_name, hand_name):
    """Return ``{vertices: [ids], position: t}`` from elbow towards wrist.

    ``position`` is the mean rest projection along the lower bone. A small
    endpoint tolerance retains genuine wrist/elbow loops that straddle the
    bone endpoints; it is deliberately not clamped to [0, 1]. Missing bones,
    absent deform groups, or unsuitable topology return an empty list.
    """
    if (
        mesh_obj is None
        or mesh_obj.type != "MESH"
        or armature is None
        or armature.type != "ARMATURE"
    ):
        return []
    lower = armature.data.bones.get(lower_name)
    hand = armature.data.bones.get(hand_name)
    if lower is None or hand is None or not lower.use_deform or not hand.use_deform:
        return []
    axis = lower.tail_local - lower.head_local
    length = axis.length
    if length <= _EPSILON:
        return []
    axis /= length
    try:
        mesh_to_armature = armature.matrix_world.inverted() @ mesh_obj.matrix_world
    except ValueError:
        return []

    deform_names = {bone.name for bone in armature.data.bones if bone.use_deform}
    deform_indices = {
        group.index for group in mesh_obj.vertex_groups if group.name in deform_names
    }
    pair_indices = {
        group.index
        for group in mesh_obj.vertex_groups
        if group.name in {lower_name, hand_name}
    }
    if not pair_indices:
        return []

    mesh = mesh_obj.data
    basis = mesh.shape_keys.reference_key if mesh.shape_keys is not None else None
    eligible = {}
    for vertex in mesh.vertices:
        total = sum(group.weight for group in vertex.groups if group.group in deform_indices)
        pair = sum(group.weight for group in vertex.groups if group.group in pair_indices)
        support = pair / total if total > _EPSILON else 0.0
        if support < 0.15:
            continue
        source = basis.data[vertex.index].co if basis is not None else vertex.co
        offset = mesh_to_armature @ source - lower.head_local
        projection = offset.dot(axis)
        position = projection / length
        radial = offset - axis * projection
        if (
            -_END_PADDING <= position <= 1.0 + _END_PADDING
            and _EPSILON < radial.length <= 0.40 * length
        ):
            eligible[vertex.index] = (position, radial, support, offset)
    if len(eligible) < 4:
        return []

    edges = [tuple(edge.vertices) for edge in mesh.edges]
    vertex_edges = [[] for _ in mesh.vertices]
    edge_lookup = {}
    for index, (first, second) in enumerate(edges):
        vertex_edges[first].append(index)
        vertex_edges[second].append(index)
        edge_lookup[tuple(sorted((first, second)))] = index
    edge_faces = [set() for _ in edges]
    non_quad_edges = set()
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            index = edge_lookup.get(tuple(sorted(key)))
            if index is not None:
                edge_faces[index].add(polygon.index)
                if len(polygon.vertices) != 4:
                    non_quad_edges.add(index)

    seen = set()
    rings = []
    for edge_index, (first, second) in enumerate(edges):
        if first not in eligible or second not in eligible:
            continue
        delta = eligible[second][3] - eligible[first][3]
        if abs(delta.dot(axis)) > delta.length * _MAX_EDGE_AXIAL_COSINE:
            continue
        if len(vertex_edges[first]) != 4:
            continue
        loop = _closed_loop(edge_index, first, edges, vertex_edges, edge_faces)
        if loop is None or any(index not in eligible for index in loop):
            continue
        signature = tuple(sorted(loop))
        if signature in seen:
            continue
        seen.add(signature)
        ring_edges = [
            edge_lookup[tuple(sorted((loop[index], loop[(index + 1) % len(loop)])))]
            for index in range(len(loop))
        ]
        if any(index in non_quad_edges for index in ring_edges):
            continue
        positions = [eligible[index][0] for index in loop]
        if max(positions) - min(positions) > _MAX_AXIAL_SPREAD:
            continue
        position = sum(positions) / len(positions)
        if not -_RING_END_PADDING <= position <= 1.0 + _RING_END_PADDING:
            continue
        if sum(eligible[index][2] for index in loop) / len(loop) < 0.45:
            continue
        winding = 0.0
        for index, vertex_index in enumerate(loop):
            before = eligible[vertex_index][1]
            after = eligible[loop[(index + 1) % len(loop)]][1]
            winding += math.atan2(axis.dot(before.cross(after)), before.dot(after))
        if not math.isclose(abs(winding), math.tau, abs_tol=0.05):
            continue
        rings.append({"vertices": loop, "position": position})
    rings.sort(key=lambda ring: (ring["position"], min(ring["vertices"])))
    return rings
