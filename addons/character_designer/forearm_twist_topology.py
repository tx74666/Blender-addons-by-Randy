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


class ForearmTopologyError(ValueError):
    """A requested control loop cannot be captured without guessing topology."""


def _mesh_graph(mesh_obj):
    if mesh_obj is None or mesh_obj.type != "MESH":
        raise ForearmTopologyError("Choose the forearm mesh before capturing a loop.")
    mesh = mesh_obj.data
    edges = [tuple(edge.vertices) for edge in mesh.edges]
    lookup = {tuple(sorted(edge)): index for index, edge in enumerate(edges)}
    faces = [[] for _ in edges]
    for polygon in mesh.polygons:
        for pair in polygon.edge_keys:
            faces[lookup[tuple(sorted(pair))]].append(polygon.index)
    return mesh, edges, lookup, faces


def _cycle_from_edges(edges, *, preferred=None):
    """Validate one unbranched cycle, retaining an already ordered input."""
    adjacent = {}
    for first, second in edges:
        adjacent.setdefault(first, set()).add(second)
        adjacent.setdefault(second, set()).add(first)
    if len(adjacent) < 4:
        raise ForearmTopologyError("Select one closed loop with at least four vertices.")
    if any(len(neighbors) > 2 for neighbors in adjacent.values()):
        raise ForearmTopologyError("The selected loop branches; select only one closed edge loop.")
    if any(len(neighbors) != 2 for neighbors in adjacent.values()):
        raise ForearmTopologyError("The selected loop is open; select a complete closed edge loop.")
    start = min(adjacent)
    ordered = [start]
    previous, current = start, min(adjacent[start])
    while current != start:
        if current in ordered:
            raise ForearmTopologyError("The selected loop intersects itself.")
        ordered.append(current)
        previous, current = current, next(index for index in adjacent[current] if index != previous)
    if len(ordered) != len(adjacent):
        raise ForearmTopologyError("The selection contains disconnected loops; capture one loop at a time.")
    if preferred is not None and len(preferred) == len(ordered) and all(
        preferred[(index + 1) % len(preferred)] in adjacent[vertex]
        for index, vertex in enumerate(preferred)
    ):
        return list(preferred)
    return ordered


def _indices(values, count, label):
    indices = list(values)
    if any(type(index) is not int or not 0 <= index < count for index in indices):
        raise ForearmTopologyError(f"The selected {label} IDs no longer match the base mesh.")
    if len(set(indices)) != len(indices):
        raise ForearmTopologyError(f"The selected {label} IDs contain duplicates.")
    return indices


def _measure_loop(mesh_obj, armature, lower_name, vertices):
    if armature is None or armature.type != "ARMATURE":
        raise ForearmTopologyError("Choose the forearm armature before capturing a loop.")
    lower = armature.data.bones.get(lower_name)
    if lower is None or lower.length <= _EPSILON:
        raise ForearmTopologyError("The forearm bone is missing or has no usable length.")
    try:
        mesh_to_armature = armature.matrix_world.inverted() @ mesh_obj.matrix_world
    except ValueError as exc:
        raise ForearmTopologyError("The armature transform cannot be inverted.") from exc
    axis = (lower.tail_local - lower.head_local).normalized()
    mesh = mesh_obj.data
    coords = mesh.shape_keys.reference_key.data if mesh.shape_keys else mesh.vertices
    positions, radials = [], []
    for index in vertices:
        offset = mesh_to_armature @ coords[index].co - lower.head_local
        projection = offset.dot(axis)
        positions.append(projection / lower.length)
        radial = offset - axis * projection
        if radial.length <= _EPSILON:
            raise ForearmTopologyError("The selected loop crosses the forearm axis.")
        radials.append(radial)
    if max(positions) - min(positions) > _MAX_AXIAL_SPREAD:
        raise ForearmTopologyError("The selected loop runs along the forearm; choose a cross-section loop.")
    winding = sum(math.atan2(axis.dot(before.cross(radials[(index + 1) % len(radials)])),
                             before.dot(radials[(index + 1) % len(radials)]))
                  for index, before in enumerate(radials))
    if not math.isclose(abs(winding), math.tau, abs_tol=0.05):
        raise ForearmTopologyError("The selected loop does not wrap once around the forearm.")
    return {"vertices": list(vertices), "position": sum(positions) / len(positions)}


def capture_loop(mesh_obj, armature, lower_name, vertex_indices=None, *, edge_indices=None):
    """Capture one closed base-mesh loop without changing selection or topology.

    Vertex input may be unordered. An already ordered cycle retains its start
    vertex and winding exactly. Edge input is ordered deterministically by IDs.
    Manual capture deliberately does not depend on deform weights or clamp the
    artist's endpoint position to the bone; automatic expansion is span-limited.
    """
    mesh, edges, lookup, faces = _mesh_graph(mesh_obj)
    if vertex_indices is not None and edge_indices is not None:
        raise ForearmTopologyError("Supply vertices or edges for one loop, not both.")
    preferred = None
    if edge_indices is not None:
        chosen = _indices(edge_indices, len(edges), "edge")
    elif vertex_indices is not None:
        preferred = _indices(vertex_indices, len(mesh.vertices), "vertex")
        selected = set(preferred)
        ordered_pairs = [tuple(sorted((vertex, preferred[(index + 1) % len(preferred)])))
                         for index, vertex in enumerate(preferred)]
        # Captured vertex order identifies the intended edges even where a
        # triangle or n-gon has another edge joining two members of the loop.
        if len(preferred) >= 4 and all(pair in lookup for pair in ordered_pairs):
            chosen = [lookup[pair] for pair in ordered_pairs]
        else:
            chosen = [index for index, edge in enumerate(edges) if set(edge) <= selected]
        endpoints = {vertex for index in chosen for vertex in edges[index]}
        if endpoints != selected:
            raise ForearmTopologyError("The selected loop is open or contains isolated vertices.")
    else:
        raise ForearmTopologyError("Select a closed edge loop before capturing it.")
    ordered = _cycle_from_edges([edges[index] for index in chosen], preferred=preferred)
    if any(not faces[index] or len(faces[index]) > 2 for index in chosen):
        raise ForearmTopologyError("The selected loop is not part of one manifold mesh surface.")
    return _measure_loop(mesh_obj, armature, lower_name, ordered)


def selected_loop(mesh_obj, armature, lower_name):
    """Read the current selection, including Edit Mode, without flushing edits."""
    mesh, edges, lookup, _faces = _mesh_graph(mesh_obj)
    if mesh_obj.mode == "EDIT":
        import bmesh
        editable = bmesh.from_edit_mesh(mesh)
        editable_edges = [tuple(sorted(vertex.index for vertex in edge.verts)) for edge in editable.edges]
        if (len(editable.verts) != len(mesh.vertices) or len(editable.edges) != len(edges)
                or set(editable_edges) != set(lookup)):
            raise ForearmTopologyError("Finish topology edits before capturing stable loop vertex IDs.")
        edge_indices = [lookup[pair] for edge, pair in zip(editable.edges, editable_edges) if edge.select and not edge.hide]
        vertices = [vertex.index for vertex in editable.verts if vertex.select and not vertex.hide]
    else:
        edge_indices = [edge.index for edge in mesh.edges if edge.select and not edge.hide]
        vertices = [vertex.index for vertex in mesh.vertices if vertex.select and not vertex.hide]
    if edge_indices:
        if set(vertices) - {vertex for index in edge_indices for vertex in edges[index]}:
            raise ForearmTopologyError("The selection contains isolated vertices outside the closed edge loop.")
        return capture_loop(mesh_obj, armature, lower_name, edge_indices=edge_indices)
    return capture_loop(mesh_obj, armature, lower_name, vertex_indices=vertices)


def adjacent_rings(mesh_obj, armature, lower_name, seed_vertices):
    """Return complete neighboring loops across the seed's adjacent quad strips.

    Incomplete strips, poles, and non-quad interruptions are reported explicitly;
    no geometric nearest-neighbor connection is manufactured across a gap.
    """
    seed = capture_loop(mesh_obj, armature, lower_name, seed_vertices)
    mesh, edges, lookup, faces = _mesh_graph(mesh_obj)
    vertices = seed["vertices"]
    opposite = set()
    seed_set = set(vertices)
    for index, first in enumerate(vertices):
        pair = tuple(sorted((first, vertices[(index + 1) % len(vertices)])))
        for face_index in faces[lookup[pair]]:
            polygon = mesh.polygons[face_index]
            if len(polygon.vertices) != 4:
                raise ForearmTopologyError("Adjacent expansion reached a non-quad face; add the next closed loop manually.")
            far = tuple(vertex for vertex in polygon.vertices if vertex not in pair)
            if len(far) != 2 or set(far) & seed_set:
                raise ForearmTopologyError("Adjacent expansion reached a branched quad strip.")
            opposite.add(lookup[tuple(sorted(far))])
    pending = set(opposite)
    rings = []
    while pending:
        component = {min(pending)}
        changed = True
        while changed:
            reached = {vertex for index in component for vertex in edges[index]}
            addition = {index for index in pending - component if reached.intersection(edges[index])}
            changed = bool(addition)
            component.update(addition)
        pending.difference_update(component)
        rings.append(capture_loop(mesh_obj, armature, lower_name, edge_indices=sorted(component)))
    if len(rings) > 2:
        raise ForearmTopologyError("Adjacent expansion reached more than two neighboring loops.")
    return sorted(rings, key=lambda ring: (ring["position"], min(ring["vertices"])))


def expand_rings(mesh_obj, armature, lower_name, seed_vertices, *, hand_name=None, diagnostics=None):
    """Grow a selected loop along actual quad strips within the forearm span.

    The seed is authoritative and does not require weights. Expansion stops at
    a topology interruption; a caller can then append missing loops manually.
    ``hand_name`` is reserved for callers sharing the automatic detector API.
    An optional diagnostics list receives distinct stop reasons for the UI;
    omitting it preserves the existing return shape and read-only behavior.
    """
    if diagnostics is not None and not isinstance(diagnostics, list):
        raise ForearmTopologyError("Expansion diagnostics must be supplied as a list.")
    seed = capture_loop(mesh_obj, armature, lower_name, seed_vertices)
    if not -_RING_END_PADDING <= seed["position"] <= 1.0 + _RING_END_PADDING:
        raise ForearmTopologyError("Choose a seed loop between the forearm's elbow and wrist.")
    found = {frozenset(seed["vertices"]): seed}
    queue = [seed]
    while queue:
        current = queue.pop(0)
        try:
            neighbors = adjacent_rings(mesh_obj, armature, lower_name, current["vertices"])
        except ForearmTopologyError as exc:
            if diagnostics is not None and str(exc) not in diagnostics:
                diagnostics.append(str(exc))
            continue
        for ring in neighbors:
            signature = frozenset(ring["vertices"])
            if signature in found or not -_RING_END_PADDING <= ring["position"] <= 1.0 + _RING_END_PADDING:
                continue
            if any(set(ring["vertices"]) & set(captured["vertices"]) for captured in found.values()):
                reason = "Adjacent expansion overlaps a captured loop; add the next distinct closed loop manually."
                if diagnostics is not None and reason not in diagnostics:
                    diagnostics.append(reason)
                continue
            found[signature] = ring
            queue.append(ring)
    return sorted(found.values(), key=lambda ring: (ring["position"], min(ring["vertices"])))


def append_ring(mesh_obj, armature, lower_name, rings, vertex_indices=None, *, edge_indices=None):
    """Insert a missing manual loop, retaining captured ordering and metadata."""
    added = capture_loop(mesh_obj, armature, lower_name, vertex_indices, edge_indices=edge_indices)
    copied = []
    previous = -math.inf
    captured_vertices = set()
    for existing in rings:
        checked = capture_loop(mesh_obj, armature, lower_name, existing["vertices"])
        if checked["vertices"] != list(existing["vertices"]):
            raise ForearmTopologyError("Captured loop vertex ordering is invalid; capture the range again.")
        if captured_vertices.intersection(checked["vertices"]):
            raise ForearmTopologyError("Captured loops overlap; capture the range again.")
        captured_vertices.update(checked["vertices"])
        position = float(existing["position"])
        if not math.isfinite(position) or position < previous:
            raise ForearmTopologyError("Captured loop ordering is invalid; capture the range again.")
        previous = position
        overlap = set(added["vertices"]) & set(checked["vertices"])
        if overlap:
            if overlap == set(added["vertices"]) == set(checked["vertices"]):
                raise ForearmTopologyError("The selected loop is already captured.")
            raise ForearmTopologyError("The selected loop overlaps a captured loop.")
        if abs(position - added["position"]) <= 1.0e-6:
            raise ForearmTopologyError("The selected loop shares a captured loop position; choose a distinct cross-section.")
        copied.append({**existing, "vertices": list(existing["vertices"])})
    insertion = next((index for index, ring in enumerate(copied) if ring["position"] > added["position"]), len(copied))
    copied.insert(insertion, added)
    return copied
