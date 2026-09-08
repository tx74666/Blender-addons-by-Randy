"""Topology-paired delta symmetry for asymmetric Edit Mode meshes."""

import hashlib
import time
import traceback
from collections import deque

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector

from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_MODELING, active_ui_page


DELTA_TIMER_INTERVAL = 0.04
DELTA_TOPOLOGY_AUDIT_INTERVAL = 0.5
DELTA_EPSILON = 1.0e-7
AUTO_SELECT_TIMER_INTERVAL = 0.05

_CAPTURE = None
_RUNTIME = None
_RUNTIME_GUARD = False
_TOGGLE_GUARD = False
_AUTO_SELECT = None
_AUTO_SELECT_GUARD = False
_AUTO_SELECT_TOGGLE_GUARD = False


class DeltaSymmetryError(ValueError):
    """A pairing or runtime problem that can be shown directly to the artist."""


def _delta_settings(context):
    window_manager = getattr(context, "window_manager", None)
    return getattr(window_manager, "character_designer_delta", None)


def _set_status(settings, level, message):
    if settings is None:
        return
    settings.last_level = level
    settings.last_message = message


def _set_live_enabled(settings, enabled):
    global _TOGGLE_GUARD
    if settings is None:
        return
    _TOGGLE_GUARD = True
    try:
        settings.live_enabled = bool(enabled)
    finally:
        _TOGGLE_GUARD = False


def _set_auto_select_enabled(settings, enabled):
    global _AUTO_SELECT_TOGGLE_GUARD
    if settings is None:
        return
    _AUTO_SELECT_TOGGLE_GUARD = True
    try:
        settings.auto_select_opposite = bool(enabled)
    finally:
        _AUTO_SELECT_TOGGLE_GUARD = False


def _edit_bmesh(context):
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise DeltaSymmetryError("Enter Mesh Edit Mode on one object.")
    if context.object is not obj:
        raise DeltaSymmetryError("The edited mesh must be the active object.")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    return obj, bm


def _topology_signature(bm):
    def canonical_face_cycle(face):
        indices = tuple(vertex.index for vertex in face.verts)
        return min(indices[offset:] + indices[:offset] for offset in range(len(indices)))

    digest = hashlib.sha256()
    digest.update(f"v{len(bm.verts)}e{len(bm.edges)}f{len(bm.faces)}|".encode("ascii"))
    for first, second in sorted(
        tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        for edge in bm.edges
    ):
        digest.update(f"{first},{second};".encode("ascii"))
    digest.update(b"|")
    for face_key in sorted(
        canonical_face_cycle(face)
        for face in bm.faces
    ):
        digest.update((",".join(str(index) for index in face_key) + ";").encode("ascii"))
    return digest.hexdigest()


def _edge_components(edges):
    """Return selected edge components without changing Edit Mode selection."""

    remaining = {edge.index: edge for edge in edges}
    vertex_edges = {}
    for edge in edges:
        for vertex in edge.verts:
            vertex_edges.setdefault(vertex.index, []).append(edge)

    components = []
    while remaining:
        first = next(iter(remaining.values()))
        component = []
        stack = [first]
        while stack:
            edge = stack.pop()
            if edge.index not in remaining:
                continue
            remaining.pop(edge.index)
            component.append(edge)
            for vertex in edge.verts:
                stack.extend(
                    linked
                    for linked in vertex_edges[vertex.index]
                    if linked.index in remaining
                )
        components.append(component)
    return components


def _validate_simple_edge_component(edges, label):
    """Validate and describe one non-branching open chain or closed loop."""

    vertex_edges = {}
    for edge in edges:
        for vertex in edge.verts:
            vertex_edges.setdefault(vertex.index, []).append(edge)

    if any(len(edges) > 2 for edges in vertex_edges.values()):
        raise DeltaSymmetryError(f"The selected {label} branches; select a simple chain or loop.")

    endpoint_count = sum(len(edges) == 1 for edges in vertex_edges.values())
    if endpoint_count not in {0, 2}:
        raise DeltaSymmetryError(f"The selected {label} must be an open chain or closed loop.")
    return {
        "edges": tuple(edges),
        "edge_ids": frozenset(edge.index for edge in edges),
        "vertex_ids": frozenset(vertex_edges),
        "closed": endpoint_count == 0,
    }


def _selected_centerline_edges(bm):
    selected = [edge for edge in bm.edges if edge.select]
    if not selected:
        raise DeltaSymmetryError(
            "Select one center edge chain or loop, or two center-band boundary chains."
        )
    components = _edge_components(selected)
    if len(components) != 1:
        raise DeltaSymmetryError("Select only one connected center edge chain or loop.")
    _validate_simple_edge_component(components[0], "centerline")
    if any(len(edge.link_faces) != 2 for edge in selected):
        raise DeltaSymmetryError("Every centerline edge needs exactly two faces, one on each side.")
    return selected


def _selected_symmetry_seed(bm):
    """Auto-detect a single centerline or the two boundaries of a center band."""

    selected = [edge for edge in bm.edges if edge.select]
    if not selected:
        raise DeltaSymmetryError(
            "Select one center edge chain or loop, or two center-band boundary chains."
        )
    components = _edge_components(selected)
    if len(components) == 1:
        return {
            "mode": "CENTERLINE",
            "edges": tuple(_selected_centerline_edges(bm)),
        }
    if len(components) != 2:
        raise DeltaSymmetryError(
            "Select either one centerline or exactly two center-band boundary chains."
        )

    first = _validate_simple_edge_component(components[0], "center-band boundary")
    second = _validate_simple_edge_component(components[1], "center-band boundary")
    if first["closed"] != second["closed"]:
        raise DeltaSymmetryError(
            "The two center-band boundaries must both be open chains or both be closed loops."
        )
    if len(first["edges"]) != len(second["edges"]):
        raise DeltaSymmetryError("The two center-band boundaries need the same number of edges.")
    if any(len(edge.link_faces) != 2 for edge in selected):
        raise DeltaSymmetryError(
            "Every center-band boundary edge needs one band face and one outer face."
        )
    return {
        "mode": "CENTER_BAND",
        "first": first,
        "second": second,
        "edges": tuple(selected),
    }


def _assign_involution(mapping, first, second, label):
    existing_first = mapping.get(first)
    existing_second = mapping.get(second)
    if existing_first is not None and existing_first != second:
        raise DeltaSymmetryError(f"{label} pairing is ambiguous at element {first}.")
    if existing_second is not None and existing_second != first:
        raise DeltaSymmetryError(f"{label} pairing is ambiguous at element {second}.")
    mapping[first] = second
    mapping[second] = first


def _outer_face_path(face, start_index, end_index, excluded_edge_index):
    vertices = [vertex.index for vertex in face.verts]
    count = len(vertices)
    if start_index not in vertices or end_index not in vertices:
        raise DeltaSymmetryError("Paired faces lost their shared edge endpoints.")

    start = vertices.index(start_index)
    paths = []
    for direction in (1, -1):
        path = [start_index]
        position = start
        for _step in range(count):
            position = (position + direction) % count
            path.append(vertices[position])
            if vertices[position] == end_index:
                break
        paths.append(path)

    for path in paths:
        if len(path) > 2:
            return path

    excluded = next((edge for edge in face.edges if edge.index == excluded_edge_index), None)
    if excluded is None:
        raise DeltaSymmetryError("A paired face no longer contains its propagation edge.")
    raise DeltaSymmetryError("A two-vertex face cannot define symmetry pairing.")


def _mapped_edge(face, vertex_map, edge):
    first = vertex_map.get(edge.verts[0].index)
    second = vertex_map.get(edge.verts[1].index)
    if first is None or second is None:
        raise DeltaSymmetryError("Face propagation reached an unmapped edge.")
    for candidate in face.edges:
        key = {candidate.verts[0].index, candidate.verts[1].index}
        if key == {first, second}:
            return candidate
    raise DeltaSymmetryError("The two sides do not have matching face topology.")


def _other_linked_face(edge, current_face):
    others = [face for face in edge.link_faces if face is not current_face]
    if len(others) > 1:
        raise DeltaSymmetryError("Non-manifold geometry cannot be paired safely.")
    return others[0] if others else None


def _establish_face_mapping(face_a, face_b, edge_a, edge_b, vertex_map):
    first_a = edge_a.verts[0].index
    second_a = edge_a.verts[1].index
    first_b = vertex_map.get(first_a)
    second_b = vertex_map.get(second_a)
    if first_b is None or second_b is None:
        raise DeltaSymmetryError("Propagation edge endpoints are not paired.")
    if {first_b, second_b} != {edge_b.verts[0].index, edge_b.verts[1].index}:
        raise DeltaSymmetryError("Propagation edges do not correspond.")

    path_a = _outer_face_path(face_a, first_a, second_a, edge_a.index)
    path_b = _outer_face_path(face_b, first_b, second_b, edge_b.index)
    if len(path_a) != len(path_b):
        raise DeltaSymmetryError("Corresponding faces have different vertex counts.")
    for vertex_a, vertex_b in zip(path_a, path_b):
        _assign_involution(vertex_map, vertex_a, vertex_b, "Vertex")


def _band_face_endpoint_pairs(face, edge_a, edge_b):
    """Pair the endpoints of two opposite boundary edges through one band face."""

    vertices_a = {vertex.index for vertex in edge_a.verts}
    vertices_b = {vertex.index for vertex in edge_b.verts}
    if vertices_a & vertices_b:
        raise DeltaSymmetryError(
            "The two center-band boundaries must not share vertices."
        )

    blocked = {edge_a.index, edge_b.index}
    adjacency = {vertex.index: set() for vertex in face.verts}
    for edge in face.edges:
        if edge.index in blocked:
            continue
        first, second = (vertex.index for vertex in edge.verts)
        adjacency[first].add(second)
        adjacency[second].add(first)

    remaining = set(adjacency)
    components = []
    while remaining:
        first = next(iter(remaining))
        component = set()
        stack = [first]
        while stack:
            vertex_index = stack.pop()
            if vertex_index not in remaining:
                continue
            remaining.remove(vertex_index)
            component.add(vertex_index)
            stack.extend(adjacency[vertex_index] & remaining)
        components.append(component)

    if len(components) != 2:
        raise DeltaSymmetryError(
            "Each center-band face must connect one boundary edge to the other."
        )
    pairs = []
    for component in components:
        endpoint_a = component & vertices_a
        endpoint_b = component & vertices_b
        if len(endpoint_a) != 1 or len(endpoint_b) != 1:
            raise DeltaSymmetryError(
                "A center-band face does not connect the two boundary chains cleanly."
            )
        pairs.append((next(iter(endpoint_a)), next(iter(endpoint_b))))
    return tuple(pairs)


def _axis_component(obj, coordinate, coordinate_space, axis_index):
    if coordinate_space == "WORLD":
        coordinate = obj.matrix_world @ coordinate
    return coordinate[axis_index]


def _orient_topological_sides(
    obj,
    bm,
    unordered_pairs,
    topology_side_a,
    topology_side_b,
    coordinate_space,
    axis,
):
    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]
    paired_indices = {index for pair in unordered_pairs for index in pair}
    side_a = set(topology_side_a) & paired_indices
    side_b = set(topology_side_b) & paired_indices
    if side_a & side_b or side_a | side_b != paired_indices:
        raise DeltaSymmetryError("The two topological sides could not be labeled consistently.")
    if not side_a or not side_b:
        raise DeltaSymmetryError("Both topological sides need at least one paired vertex.")

    mean_a = sum(
        _axis_component(obj, bm.verts[index].co, coordinate_space, axis_index)
        for index in side_a
    ) / len(side_a)
    mean_b = sum(
        _axis_component(obj, bm.verts[index].co, coordinate_space, axis_index)
        for index in side_b
    ) / len(side_b)
    negative_side, positive_side = (side_a, side_b) if mean_a <= mean_b else (side_b, side_a)

    oriented = []
    for first, second in unordered_pairs:
        if first in negative_side and second in positive_side:
            oriented.append((first, second))
        elif second in negative_side and first in positive_side:
            oriented.append((second, first))
        else:
            raise DeltaSymmetryError("A vertex pair crosses the topological side labels incorrectly.")
    return tuple(oriented), tuple(sorted(negative_side)), tuple(sorted(positive_side))


def build_pairing_from_centerline(obj, bm, center_edges, coordinate_space="LOCAL", axis="X"):
    """Return a proven vertex involution grown from the two faces beside a seam."""

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]
    center_edge_ids = {edge.index for edge in center_edges}
    center_vertex_ids = {
        vertex.index
        for edge in center_edges
        for vertex in edge.verts
    }
    vertex_map = {}
    for vertex_index in center_vertex_ids:
        _assign_involution(vertex_map, vertex_index, vertex_index, "Centerline")

    seed_edge = center_edges[0]
    active = bm.select_history.active
    if isinstance(active, bmesh.types.BMEdge) and active.index in center_edge_ids:
        seed_edge = active
    face_a, face_b = seed_edge.link_faces
    seam_mean = sum((bm.verts[index].co for index in center_vertex_ids), Vector())
    seam_mean /= len(center_vertex_ids)

    def face_side_value(face):
        outside = [vertex.co for vertex in face.verts if vertex.index not in center_vertex_ids]
        if not outside:
            outside = [vertex.co for vertex in face.verts]
        mean = sum(outside, Vector()) / len(outside)
        return _axis_component(obj, mean, coordinate_space, axis_index) - _axis_component(
            obj,
            seam_mean,
            coordinate_space,
            axis_index,
        )

    if face_side_value(face_a) > face_side_value(face_b):
        face_a, face_b = face_b, face_a

    face_map = {}
    face_side = {}
    queue = deque()

    def add_face_pair(first_face, second_face, first_edge, second_edge, side_a, side_b):
        first_index = first_face.index
        second_index = second_face.index
        existing_first = face_map.get(first_index)
        existing_second = face_map.get(second_index)
        if existing_first is not None or existing_second is not None:
            if existing_first != second_index or existing_second != first_index:
                raise DeltaSymmetryError("Face topology produces conflicting symmetry pairs.")
            return
        _assign_involution(face_map, first_index, second_index, "Face")
        if first_index == second_index:
            face_side[first_index] = 0
        else:
            face_side[first_index] = side_a
            face_side[second_index] = side_b
        _establish_face_mapping(first_face, second_face, first_edge, second_edge, vertex_map)
        queue.append((first_face, second_face))

    add_face_pair(face_a, face_b, seed_edge, seed_edge, -1, 1)

    while queue:
        current_a, current_b = queue.popleft()
        for edge_a in current_a.edges:
            edge_b = _mapped_edge(current_b, vertex_map, edge_a)
            if edge_a.index in center_edge_ids or edge_b.index in center_edge_ids:
                continue
            next_a = _other_linked_face(edge_a, current_a)
            next_b = _other_linked_face(edge_b, current_b)
            if next_a is None and next_b is None:
                continue
            if next_a is None or next_b is None:
                raise DeltaSymmetryError("The two sides reach different mesh boundaries.")
            if next_a is current_b and next_b is current_a:
                continue
            add_face_pair(
                next_a,
                next_b,
                edge_a,
                edge_b,
                face_side.get(current_a.index, -1),
                face_side.get(current_b.index, 1),
            )

    for edge in center_edges:
        linked = edge.link_faces
        if face_map.get(linked[0].index) != linked[1].index:
            raise DeltaSymmetryError("The selected centerline does not divide matching topology.")

    unordered_pairs = tuple(sorted(
        (first, second)
        for first, second in vertex_map.items()
        if first < second
    ))
    if not unordered_pairs:
        raise DeltaSymmetryError("No vertex pairs were found beside the selected centerline.")

    paired_face_vertices = {
        vertex.index
        for face_index in face_map
        for vertex in bm.faces[face_index].verts
    }
    unpaired = sorted(paired_face_vertices - set(vertex_map))
    if unpaired:
        raise DeltaSymmetryError("Some vertices in the paired surface could not be mapped safely.")

    topology_side_a = {
        vertex.index
        for face_index, side in face_side.items()
        if side == -1
        for vertex in bm.faces[face_index].verts
        if vertex.index not in center_vertex_ids
    }
    topology_side_b = {
        vertex.index
        for face_index, side in face_side.items()
        if side == 1
        for vertex in bm.faces[face_index].verts
        if vertex.index not in center_vertex_ids
    }
    pairs, negative, positive = _orient_topological_sides(
        obj,
        bm,
        unordered_pairs,
        topology_side_a,
        topology_side_b,
        coordinate_space,
        axis,
    )

    return {
        "object": obj,
        "object_name": obj.name,
        "mesh": obj.data,
        "mesh_name": obj.data.name,
        "pairs": pairs,
        "unordered_pairs": unordered_pairs,
        "topology_side_a": tuple(sorted(topology_side_a)),
        "topology_side_b": tuple(sorted(topology_side_b)),
        "negative": negative,
        "positive": positive,
        "orientation_space": coordinate_space,
        "orientation_axis": axis,
        "center": tuple(sorted(center_vertex_ids)),
        "center_edges": tuple(sorted(center_edge_ids)),
        "seed_mode": "CENTERLINE",
        "center_band_faces": (),
        "topology_signature": _topology_signature(bm),
        "vertex_count": len(bm.verts),
        "edge_count": len(bm.edges),
        "face_count": len(bm.faces),
    }


def build_pairing_from_center_band(
    obj,
    bm,
    first_boundary,
    second_boundary,
    coordinate_space="LOCAL",
    axis="X",
):
    """Grow a symmetry involution from two adjacent center-band boundaries."""

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()

    first_edge_ids = set(first_boundary["edge_ids"])
    second_edge_ids = set(second_boundary["edge_ids"])
    boundary_edge_ids = first_edge_ids | second_edge_ids
    boundary_vertex_ids = set(first_boundary["vertex_ids"]) | set(
        second_boundary["vertex_ids"]
    )
    vertex_map = {}
    band_rows = []
    used_second_edges = set()
    used_band_faces = set()

    for edge_a in first_boundary["edges"]:
        matches = []
        for face in edge_a.link_faces:
            edges_a = [edge for edge in face.edges if edge.index in first_edge_ids]
            edges_b = [edge for edge in face.edges if edge.index in second_edge_ids]
            if edges_b:
                if len(edges_a) != 1 or len(edges_b) != 1 or edges_a[0] is not edge_a:
                    raise DeltaSymmetryError(
                        "Each center-band face must contain exactly one edge from each boundary."
                    )
                matches.append((face, edges_b[0]))
        if len(matches) != 1:
            raise DeltaSymmetryError(
                "Each boundary edge must meet exactly one face in the selected center band."
            )
        band_face, edge_b = matches[0]
        if edge_b.index in used_second_edges or band_face.index in used_band_faces:
            raise DeltaSymmetryError(
                "The center-band faces do not pair the two boundary chains one-to-one."
            )
        used_second_edges.add(edge_b.index)
        used_band_faces.add(band_face.index)
        for first_vertex, second_vertex in _band_face_endpoint_pairs(
            band_face,
            edge_a,
            edge_b,
        ):
            _assign_involution(vertex_map, first_vertex, second_vertex, "Band boundary")
        band_rows.append((edge_a, edge_b, band_face))

    if used_second_edges != second_edge_ids:
        raise DeltaSymmetryError(
            "Some edges on the second center-band boundary have no matching band face."
        )
    if set(vertex_map) != boundary_vertex_ids:
        raise DeltaSymmetryError(
            "The center-band faces do not pair every boundary vertex exactly once."
        )

    face_map = {}
    face_side = {}
    queue = deque()
    for _edge_a, _edge_b, band_face in band_rows:
        _assign_involution(face_map, band_face.index, band_face.index, "Band face")
        face_side[band_face.index] = 0

    def add_face_pair(first_face, second_face, first_edge, second_edge, side_a, side_b):
        first_index = first_face.index
        second_index = second_face.index
        existing_first = face_map.get(first_index)
        existing_second = face_map.get(second_index)
        if existing_first is not None or existing_second is not None:
            if existing_first != second_index or existing_second != first_index:
                raise DeltaSymmetryError("Face topology produces conflicting symmetry pairs.")
            return
        _assign_involution(face_map, first_index, second_index, "Face")
        if first_index == second_index:
            face_side[first_index] = 0
        else:
            face_side[first_index] = side_a
            face_side[second_index] = side_b
        _establish_face_mapping(first_face, second_face, first_edge, second_edge, vertex_map)
        queue.append((first_face, second_face))

    for edge_a, edge_b, band_face in band_rows:
        outer_a = [face for face in edge_a.link_faces if face is not band_face]
        outer_b = [face for face in edge_b.link_faces if face is not band_face]
        if len(outer_a) != 1 or len(outer_b) != 1:
            raise DeltaSymmetryError(
                "Every center-band boundary edge needs exactly one outer face."
            )
        add_face_pair(outer_a[0], outer_b[0], edge_a, edge_b, -1, 1)

    while queue:
        current_a, current_b = queue.popleft()
        for edge_a in current_a.edges:
            edge_b = _mapped_edge(current_b, vertex_map, edge_a)
            if edge_a.index in boundary_edge_ids or edge_b.index in boundary_edge_ids:
                continue
            next_a = _other_linked_face(edge_a, current_a)
            next_b = _other_linked_face(edge_b, current_b)
            if next_a is None and next_b is None:
                continue
            if next_a is None or next_b is None:
                raise DeltaSymmetryError("The two sides reach different mesh boundaries.")
            if next_a is current_b and next_b is current_a:
                continue
            add_face_pair(
                next_a,
                next_b,
                edge_a,
                edge_b,
                face_side.get(current_a.index, -1),
                face_side.get(current_b.index, 1),
            )

    for edge_a, edge_b, band_face in band_rows:
        outer_a = next(face for face in edge_a.link_faces if face is not band_face)
        outer_b = next(face for face in edge_b.link_faces if face is not band_face)
        if face_map.get(outer_a.index) != outer_b.index:
            raise DeltaSymmetryError(
                "The selected center band does not divide matching topology."
            )

    unordered_pairs = tuple(
        sorted(
            (first, second)
            for first, second in vertex_map.items()
            if first < second
        )
    )
    if not unordered_pairs:
        raise DeltaSymmetryError("No vertex pairs were found from the selected center band.")

    paired_face_vertices = {
        vertex.index
        for face_index in face_map
        for vertex in bm.faces[face_index].verts
    }
    unpaired = sorted(paired_face_vertices - set(vertex_map))
    if unpaired:
        raise DeltaSymmetryError("Some vertices in the paired surface could not be mapped safely.")

    topology_side_a = {
        vertex.index
        for face_index, side in face_side.items()
        if side == -1
        for vertex in bm.faces[face_index].verts
    }
    topology_side_b = {
        vertex.index
        for face_index, side in face_side.items()
        if side == 1
        for vertex in bm.faces[face_index].verts
    }
    pairs, negative, positive = _orient_topological_sides(
        obj,
        bm,
        unordered_pairs,
        topology_side_a,
        topology_side_b,
        coordinate_space,
        axis,
    )

    return {
        "object": obj,
        "object_name": obj.name,
        "mesh": obj.data,
        "mesh_name": obj.data.name,
        "pairs": pairs,
        "unordered_pairs": unordered_pairs,
        "topology_side_a": tuple(sorted(topology_side_a)),
        "topology_side_b": tuple(sorted(topology_side_b)),
        "negative": negative,
        "positive": positive,
        "orientation_space": coordinate_space,
        "orientation_axis": axis,
        "center": tuple(sorted(boundary_vertex_ids)),
        "center_edges": tuple(sorted(boundary_edge_ids)),
        "seed_mode": "CENTER_BAND",
        "center_band_faces": tuple(sorted(used_band_faces)),
        "topology_signature": _topology_signature(bm),
        "vertex_count": len(bm.verts),
        "edge_count": len(bm.edges),
        "face_count": len(bm.faces),
    }


def build_pairing_from_selection(obj, bm, coordinate_space="LOCAL", axis="X"):
    """Auto-detect the selected seed topology and build its symmetry pairing."""

    seed = _selected_symmetry_seed(bm)
    if seed["mode"] == "CENTERLINE":
        return build_pairing_from_centerline(
            obj,
            bm,
            seed["edges"],
            coordinate_space,
            axis,
        )
    return build_pairing_from_center_band(
        obj,
        bm,
        seed["first"],
        seed["second"],
        coordinate_space,
        axis,
    )


def _refresh_capture_orientation(obj, bm, coordinate_space, axis):
    if _CAPTURE is None:
        return
    previous_auto_drivers = {}
    if _AUTO_SELECT is not None:
        previous_auto_drivers = {
            frozenset(pair): driver
            for pair, driver in _AUTO_SELECT.get("preferred_drivers", {}).items()
        }
    pairs, negative, positive = _orient_topological_sides(
        obj,
        bm,
        _CAPTURE["unordered_pairs"],
        _CAPTURE["topology_side_a"],
        _CAPTURE["topology_side_b"],
        coordinate_space,
        axis,
    )
    _CAPTURE["pairs"] = pairs
    _CAPTURE["negative"] = negative
    _CAPTURE["positive"] = positive
    _CAPTURE["orientation_space"] = coordinate_space
    _CAPTURE["orientation_axis"] = axis
    if _RUNTIME is not None:
        _RUNTIME["resample"] = True
    if _AUTO_SELECT is not None:
        _AUTO_SELECT["preferred_drivers"] = {
            pair: previous_auto_drivers[frozenset(pair)]
            for pair in pairs
            if frozenset(pair) in previous_auto_drivers
        }
        _AUTO_SELECT["selection_previous"] = _auto_select_vertex_snapshot(bm)
        _AUTO_SELECT["active_previous"] = _active_vertex_index(bm)


def reflect_delta(delta, obj, coordinate_space="LOCAL", axis="X"):
    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]
    delta = Vector(delta)
    if coordinate_space == "LOCAL":
        result = delta.copy()
        result[axis_index] *= -1.0
        return result

    linear = obj.matrix_world.to_3x3()
    world_delta = linear @ delta
    world_delta[axis_index] *= -1.0
    try:
        return linear.inverted() @ world_delta
    except ValueError as exc:
        raise DeltaSymmetryError("The object transform is singular; World symmetry is unavailable.") from exc


def _active_vertex_indices(bm):
    active = bm.select_history.active
    if isinstance(active, bmesh.types.BMVert):
        return {active.index}
    if isinstance(active, bmesh.types.BMEdge):
        return {vertex.index for vertex in active.verts}
    if isinstance(active, bmesh.types.BMFace):
        return {vertex.index for vertex in active.verts}
    return set()


def apply_delta_updates(
    obj,
    bm,
    pairs,
    previous,
    coordinate_space="LOCAL",
    axis="X",
    preferred_drivers=None,
):
    """Mirror incremental movement without replacing either side's baseline position."""

    active = _active_vertex_indices(bm)
    selected = {
        index
        for pair in pairs
        for index in pair
        if bm.verts[index].select
    }
    planned_writes = []
    conflicts = 0

    for negative, positive in pairs:
        current_negative = bm.verts[negative].co.copy()
        current_positive = bm.verts[positive].co.copy()
        previous_negative = previous[negative]
        previous_positive = previous[positive]
        delta_negative = current_negative - previous_negative
        delta_positive = current_positive - previous_positive
        changed_negative = delta_negative.length > DELTA_EPSILON
        changed_positive = delta_positive.length > DELTA_EPSILON
        if not changed_negative and not changed_positive:
            continue

        reflected_negative = reflect_delta(delta_negative, obj, coordinate_space, axis)
        preferred_driver = (
            preferred_drivers.get((negative, positive))
            if preferred_drivers
            else None
        )
        if (
            changed_negative
            and changed_positive
            and (delta_positive - reflected_negative).length <= DELTA_EPSILON * 8.0
        ):
            continue
        if preferred_driver == negative:
            driver = "NEGATIVE"
        elif preferred_driver == positive:
            driver = "POSITIVE"
        elif changed_negative and changed_positive:
            active_negative = negative in active
            active_positive = positive in active
            selected_negative = negative in selected
            selected_positive = positive in selected
            if active_negative and not active_positive:
                driver = "NEGATIVE"
            elif active_positive and not active_negative:
                driver = "POSITIVE"
            elif selected_negative and not selected_positive:
                driver = "NEGATIVE"
            elif selected_positive and not selected_negative:
                driver = "POSITIVE"
            else:
                conflicts += 1
                continue
        elif changed_negative:
            driver = "NEGATIVE"
        else:
            driver = "POSITIVE"

        if driver == "NEGATIVE":
            planned_writes.append((positive, previous_positive + reflected_negative))
        else:
            reflected_positive = reflect_delta(delta_positive, obj, coordinate_space, axis)
            planned_writes.append((negative, previous_negative + reflected_positive))

    # A conflicting pair invalidates this entire update. This keeps one timer
    # tick atomic instead of leaving earlier pairs mirrored and later pairs
    # untouched. The artist's current coordinates become the new baseline so
    # the rejected movement cannot feed back on the next tick.
    if conflicts:
        for negative, positive in pairs:
            previous[negative] = bm.verts[negative].co.copy()
            previous[positive] = bm.verts[positive].co.copy()
        return 0, conflicts

    for vertex_index, coordinate in planned_writes:
        bm.verts[vertex_index].co = coordinate
    if planned_writes:
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    for negative, positive in pairs:
        previous[negative] = bm.verts[negative].co.copy()
        previous[positive] = bm.verts[positive].co.copy()
    return len(planned_writes), 0


def _paired_coordinates_changed(bm, pairs, previous):
    for negative, positive in pairs:
        if (bm.verts[negative].co - previous[negative]).length > DELTA_EPSILON:
            return True
        if (bm.verts[positive].co - previous[positive]).length > DELTA_EPSILON:
            return True
    return False


def _capture_snapshot(obj, bm):
    return {
        index: bm.verts[index].co.copy()
        for pair in _CAPTURE["pairs"]
        for index in pair
    }


def _active_shape_key_identity(obj):
    """Return a stable runtime identity for the Shape Key being edited."""

    try:
        shape_key = obj.active_shape_key
        shape_keys = getattr(obj.data, "shape_keys", None)
        if shape_key is None or shape_keys is None:
            return None
        return shape_keys.as_pointer(), shape_key.as_pointer()
    except (AttributeError, ReferenceError, RuntimeError):
        return None


def _validate_capture(obj, bm):
    if _CAPTURE is None or _CAPTURE.get("object") is not obj or _CAPTURE.get("mesh") is not obj.data:
        raise DeltaSymmetryError("Build pairs for this mesh first.")
    counts = (len(bm.verts), len(bm.edges), len(bm.faces))
    expected = (_CAPTURE["vertex_count"], _CAPTURE["edge_count"], _CAPTURE["face_count"])
    if counts != expected:
        raise DeltaSymmetryError("Topology changed - rebuild the Delta Symmetry pairs.")


def _resolve_record_object(record):
    if record is None:
        return None
    obj = record.get("object")
    try:
        if obj is not None:
            current = bpy.data.objects.get(obj.name)
            if (
                current is not None
                and current.as_pointer() == obj.as_pointer()
                and current.type == "MESH"
            ):
                mesh = current.data
                expected_mesh_name = record.get("mesh_name", "")
                stored_mesh = record.get("mesh")
                if expected_mesh_name and mesh.name != expected_mesh_name:
                    try:
                        if stored_mesh is None or mesh.as_pointer() != stored_mesh.as_pointer():
                            return None
                    except (ReferenceError, RuntimeError):
                        return None
                record["object"] = current
                record["mesh"] = mesh
                record["object_name"] = current.name
                record["mesh_name"] = mesh.name
                return current
    except (ReferenceError, RuntimeError):
        pass

    try:
        obj = bpy.data.objects.get(record.get("object_name", ""))
        if obj is None or obj.type != "MESH":
            return None
        mesh = obj.data
        expected_mesh_name = record.get("mesh_name", "")
        if expected_mesh_name and mesh.name != expected_mesh_name:
            stored_mesh = record.get("mesh")
            try:
                if stored_mesh is None or mesh.as_pointer() != stored_mesh.as_pointer():
                    return None
            except (ReferenceError, RuntimeError):
                return None
    except (ReferenceError, RuntimeError):
        return None
    record["object"] = obj
    record["mesh"] = mesh
    record["object_name"] = obj.name
    record["mesh_name"] = mesh.name
    return obj


def _resolve_capture_object():
    return _resolve_record_object(_CAPTURE)


def _resolve_runtime_object():
    obj = _resolve_record_object(_RUNTIME)
    if obj is None:
        return None
    if _CAPTURE is not None:
        _CAPTURE["object"] = obj
        _CAPTURE["mesh"] = obj.data
        _CAPTURE["object_name"] = obj.name
        _CAPTURE["mesh_name"] = obj.data.name
    return obj


def _auto_select_vertex_snapshot(bm):
    if _CAPTURE is None:
        return set()
    return {
        index
        for pair in _CAPTURE["pairs"]
        for index in pair
        if bm.verts[index].select
    }


def _active_vertex_index(bm):
    active = bm.select_history.active
    return active.index if isinstance(active, bmesh.types.BMVert) else None


def _validate_auto_select_options(context):
    objects_in_mode = [
        edited
        for edited in getattr(context, "objects_in_mode_unique_data", ())
        if edited.type == "MESH"
    ]
    if len(objects_in_mode) > 1:
        raise DeltaSymmetryError("Auto Select Opposite works on one edited mesh at a time.")
    if tuple(context.scene.tool_settings.mesh_select_mode) != (True, False, False):
        raise DeltaSymmetryError("Auto Select Opposite is available in Vertex Select mode.")


def _sync_auto_selection(obj, bm, *, initial=False):
    """Mirror paired vertex selection without replacing the user's active element."""

    if _AUTO_SELECT is None or _CAPTURE is None:
        return False, False
    current = _auto_select_vertex_snapshot(bm)
    previous = _AUTO_SELECT.get("selection_previous", set())
    preferred = _AUTO_SELECT.setdefault("preferred_drivers", {})
    active_index = _active_vertex_index(bm)
    planned = {}
    ambiguous = False

    for negative, positive in _CAPTURE["pairs"]:
        pair = (negative, positive)
        negative_selected = negative in current
        positive_selected = positive in current
        previous_negative = negative in previous
        previous_positive = positive in previous

        if negative_selected == positive_selected:
            if not negative_selected:
                preferred.pop(pair, None)
            elif preferred.get(pair) in pair:
                pass
            elif active_index in pair:
                preferred[pair] = active_index
            elif initial or (
                negative_selected != previous_negative
                and positive_selected != previous_positive
            ):
                preferred.pop(pair, None)
                ambiguous = True
            continue

        if active_index in pair and active_index in current:
            driver = active_index
        elif initial:
            driver = negative if negative_selected else positive
        else:
            negative_changed = negative_selected != previous_negative
            positive_changed = positive_selected != previous_positive
            if negative_changed and not positive_changed:
                driver = negative
            elif positive_changed and not negative_changed:
                driver = positive
            elif active_index in pair:
                driver = active_index
            else:
                driver = negative if negative_selected else positive

        desired = driver in current
        partner = positive if driver == negative else negative
        planned[partner] = desired
        if desired:
            preferred[pair] = driver
        else:
            preferred.pop(pair, None)

    if planned:
        for index, selected in planned.items():
            bm.verts[index].select = selected
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    _AUTO_SELECT["selection_previous"] = _auto_select_vertex_snapshot(bm)
    _AUTO_SELECT["active_previous"] = _active_vertex_index(bm)
    return bool(planned), ambiguous


def _movement_selection_is_ambiguous(bm):
    if _AUTO_SELECT is None or _CAPTURE is None:
        return False
    selected = _auto_select_vertex_snapshot(bm)
    active_index = _active_vertex_index(bm)
    preferred = _AUTO_SELECT.get("preferred_drivers", {})
    for pair in _CAPTURE["pairs"]:
        if pair[0] not in selected or pair[1] not in selected:
            continue
        if preferred.get(pair) in pair or active_index in pair:
            continue
        return True
    return False


def _register_auto_select_runtime():
    if _auto_select_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_auto_select_load_pre)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _auto_select_history_post not in handlers:
            handlers.append(_auto_select_history_post)
    if not bpy.app.timers.is_registered(_auto_select_runtime_tick):
        bpy.app.timers.register(
            _auto_select_runtime_tick,
            first_interval=AUTO_SELECT_TIMER_INTERVAL,
        )


def _unregister_auto_select_runtime(*, remove_load_handler=True):
    if remove_load_handler and _auto_select_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_auto_select_load_pre)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _auto_select_history_post in handlers:
            handlers.remove(_auto_select_history_post)
    try:
        if bpy.app.timers.is_registered(_auto_select_runtime_tick):
            bpy.app.timers.unregister(_auto_select_runtime_tick)
    except Exception:
        pass


def _start_auto_select(context, settings):
    global _AUTO_SELECT
    obj, bm = _edit_bmesh(context)
    _validate_capture(obj, bm)
    _validate_auto_select_options(context)
    if _topology_signature(bm) != _CAPTURE["topology_signature"]:
        raise DeltaSymmetryError("Topology changed - rebuild the Delta Symmetry pairs.")
    _AUTO_SELECT = {
        "object": obj,
        "object_name": obj.name,
        "mesh": obj.data,
        "mesh_name": obj.data.name,
        "selection_previous": _auto_select_vertex_snapshot(bm),
        "active_previous": _active_vertex_index(bm),
        "preferred_drivers": {},
        "resample": False,
    }
    _changed, ambiguous = _sync_auto_selection(obj, bm, initial=True)
    _register_auto_select_runtime()
    if ambiguous and settings.live_enabled:
        _stop_mirror_movement_neutral(settings)
    _set_status(settings, "NONE", "")


def stop_auto_select_runtime(*, remove_load_handler=True):
    global _AUTO_SELECT
    settings = _delta_settings(bpy.context)
    if settings is not None:
        _set_auto_select_enabled(settings, False)
    _unregister_auto_select_runtime(remove_load_handler=remove_load_handler)
    _AUTO_SELECT = None


def _stop_auto_select_neutral(settings, message=""):
    stop_auto_select_runtime()
    _set_status(settings, "INFO" if message else "NONE", message)


def _auto_select_runtime_tick():
    global _AUTO_SELECT_GUARD
    settings = _delta_settings(bpy.context)
    if settings is None or not settings.auto_select_opposite:
        return None
    if _AUTO_SELECT_GUARD:
        return AUTO_SELECT_TIMER_INTERVAL
    if _AUTO_SELECT is None or _CAPTURE is None:
        _stop_auto_select_neutral(settings)
        return None

    try:
        obj = _resolve_record_object(_AUTO_SELECT)
        if obj is None or obj.mode != "EDIT" or bpy.context.edit_object is not obj:
            raise DeltaSymmetryError("")
        _validate_auto_select_options(bpy.context)
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        _validate_capture(obj, bm)
        current = _auto_select_vertex_snapshot(bm)
        current_active = _active_vertex_index(bm)
        if _AUTO_SELECT.get("resample"):
            if _topology_signature(bm) != _CAPTURE["topology_signature"]:
                raise DeltaSymmetryError("Topology changed - rebuild the pairs.")
            _AUTO_SELECT["selection_previous"] = current
            _AUTO_SELECT["active_previous"] = current_active
            preferred = {
                pair: driver
                for pair, driver in _AUTO_SELECT.get("preferred_drivers", {}).items()
                if pair[0] in current and pair[1] in current and driver in pair
            }
            if current_active is not None:
                for pair in _CAPTURE["pairs"]:
                    if (
                        current_active in pair
                        and pair[0] in current
                        and pair[1] in current
                    ):
                        preferred[pair] = current_active
                        break
            _AUTO_SELECT["preferred_drivers"] = preferred
            _AUTO_SELECT["resample"] = False
            if settings.live_enabled and _movement_selection_is_ambiguous(bm):
                _stop_mirror_movement_neutral(settings)
            return AUTO_SELECT_TIMER_INTERVAL
        if (
            current == _AUTO_SELECT.get("selection_previous", set())
            and current_active == _AUTO_SELECT.get("active_previous")
        ):
            return AUTO_SELECT_TIMER_INTERVAL
        if _topology_signature(bm) != _CAPTURE["topology_signature"]:
            raise DeltaSymmetryError("Topology changed - rebuild the pairs.")
        _AUTO_SELECT_GUARD = True
        _changed, ambiguous = _sync_auto_selection(obj, bm)
        if ambiguous and settings.live_enabled:
            _stop_mirror_movement_neutral(settings)
    except DeltaSymmetryError as exc:
        _stop_auto_select_neutral(settings, str(exc))
        return None
    except Exception:
        traceback.print_exc()
        _stop_auto_select_neutral(settings)
        return None
    finally:
        _AUTO_SELECT_GUARD = False
    return AUTO_SELECT_TIMER_INTERVAL


def _auto_select_load_pre(_filepath):
    stop_delta_symmetry_runtime(clear_capture=True, remove_load_handler=False)


def _auto_select_history_post(_scene):
    if _AUTO_SELECT is not None:
        _AUTO_SELECT["resample"] = True


def _auto_select_toggle_updated(settings, context):
    if _AUTO_SELECT_TOGGLE_GUARD:
        return
    if settings.auto_select_opposite:
        try:
            _start_auto_select(context, settings)
        except (DeltaSymmetryError, ReferenceError, RuntimeError) as exc:
            _stop_auto_select_neutral(settings, str(exc))
        except Exception:
            traceback.print_exc()
            _stop_auto_select_neutral(settings)
    else:
        stop_auto_select_runtime()


def _preferred_movement_drivers():
    if _AUTO_SELECT is None:
        return None
    return _AUTO_SELECT.get("preferred_drivers")


def _validate_runtime_options(context, obj):
    objects_in_mode = [
        edited
        for edited in getattr(context, "objects_in_mode_unique_data", ())
        if edited.type == "MESH"
    ]
    if len(objects_in_mode) > 1:
        raise DeltaSymmetryError("Mirror Movement works on one edited mesh at a time.")
    tool_settings = context.scene.tool_settings
    if getattr(tool_settings, "use_mesh_automerge", False):
        raise DeltaSymmetryError("Mirror Movement turned off while Auto Merge is active.")
    proportional_edit = getattr(tool_settings, "use_proportional_edit", False)
    if proportional_edit not in {False, "DISABLED"}:
        raise DeltaSymmetryError("Mirror Movement turned off while Proportional Editing is active.")


def _native_mirror_axes(mesh):
    return (mesh.use_mirror_x, mesh.use_mirror_y, mesh.use_mirror_z)


def _disable_native_mirror_axes(mesh):
    if mesh.use_mirror_x:
        mesh.use_mirror_x = False
    if mesh.use_mirror_y:
        mesh.use_mirror_y = False
    if mesh.use_mirror_z:
        mesh.use_mirror_z = False


def _restore_native_mirror_axes(runtime):
    if runtime is None or "native_mirror_axes" not in runtime:
        return
    try:
        mesh = runtime.get("mesh")
        if mesh is None:
            return
        current = bpy.data.meshes.get(mesh.name)
        if current is None or current.as_pointer() != mesh.as_pointer():
            return
        values = runtime["native_mirror_axes"]
        if mesh.use_mirror_x != values[0]:
            mesh.use_mirror_x = values[0]
        if mesh.use_mirror_y != values[1]:
            mesh.use_mirror_y = values[1]
        if mesh.use_mirror_z != values[2]:
            mesh.use_mirror_z = values[2]
    except (ReferenceError, RuntimeError):
        pass


def _register_runtime_handlers():
    if _delta_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_delta_load_pre)
    if _delta_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_delta_depsgraph_update)
    for handlers in (bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _delta_history_pre not in handlers:
            handlers.append(_delta_history_pre)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _delta_history_post not in handlers:
            handlers.append(_delta_history_post)
    if _delta_save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_delta_save_pre)


def _unregister_runtime_handlers(*, remove_load_handler=True):
    if remove_load_handler and _delta_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_delta_load_pre)
    if _delta_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_delta_depsgraph_update)
    for handlers in (bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _delta_history_pre in handlers:
            handlers.remove(_delta_history_pre)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _delta_history_post in handlers:
            handlers.remove(_delta_history_post)
    if _delta_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_delta_save_pre)


def _start_runtime(context, settings):
    global _RUNTIME
    obj, bm = _edit_bmesh(context)
    _validate_capture(obj, bm)
    _validate_runtime_options(context, obj)
    if _topology_signature(bm) != _CAPTURE["topology_signature"]:
        raise DeltaSymmetryError("Topology changed - rebuild the Delta Symmetry pairs.")
    _refresh_capture_orientation(obj, bm, settings.coordinate_space, settings.axis)
    if _AUTO_SELECT is not None and settings.auto_select_opposite:
        _changed, ambiguous = _sync_auto_selection(obj, bm, initial=True)
        if ambiguous or _movement_selection_is_ambiguous(bm):
            raise DeltaSymmetryError("")
    _RUNTIME = {
        "object": obj,
        "object_name": obj.name,
        "mesh": obj.data,
        "mesh_name": obj.data.name,
        "previous": _capture_snapshot(obj, bm),
        "active_shape_key": _active_shape_key_identity(obj),
        "paused": False,
        "resample": False,
        "history_active": False,
        "native_mirror_axes": _native_mirror_axes(obj.data),
        "next_topology_audit": time.monotonic() + DELTA_TOPOLOGY_AUDIT_INTERVAL,
    }
    try:
        _disable_native_mirror_axes(obj.data)
        _register_runtime_handlers()
        if not bpy.app.timers.is_registered(_delta_runtime_tick):
            bpy.app.timers.register(_delta_runtime_tick, first_interval=DELTA_TIMER_INTERVAL)
    except Exception:
        _restore_native_mirror_axes(_RUNTIME)
        _RUNTIME = None
        raise
    settings.paused = False
    _set_status(settings, "NONE", "")


def stop_delta_symmetry_runtime(clear_capture=False, *, remove_load_handler=True):
    global _CAPTURE, _RUNTIME
    settings = _delta_settings(bpy.context)
    if settings is not None:
        _set_live_enabled(settings, False)
        settings.paused = False
    runtime = _RUNTIME
    try:
        if bpy.app.timers.is_registered(_delta_runtime_tick):
            bpy.app.timers.unregister(_delta_runtime_tick)
        if bpy.app.timers.is_registered(_delta_stop_after_save):
            bpy.app.timers.unregister(_delta_stop_after_save)
    except Exception:
        pass
    _unregister_runtime_handlers(remove_load_handler=remove_load_handler)
    _RUNTIME = None
    _restore_native_mirror_axes(runtime)
    if clear_capture:
        stop_auto_select_runtime(remove_load_handler=remove_load_handler)
        _CAPTURE = None
        if settings is not None:
            settings.source_object = None
            settings.captured = False
            settings.center_count = 0
            settings.pair_count = 0
            settings.seed_mode = ""


def _stop_mirror_movement_neutral(settings, message=""):
    _set_live_enabled(settings, False)
    stop_delta_symmetry_runtime(clear_capture=False)
    _set_status(settings, "INFO" if message else "NONE", message)


def _delta_runtime_tick(allow_writes=False):
    global _RUNTIME_GUARD
    settings = _delta_settings(bpy.context)
    if settings is None or not settings.live_enabled:
        return None
    if _RUNTIME_GUARD:
        return DELTA_TIMER_INTERVAL
    if _RUNTIME is None or _CAPTURE is None:
        _stop_mirror_movement_neutral(settings)
        return None
    if _RUNTIME.get("history_active"):
        return DELTA_TIMER_INTERVAL

    try:
        obj = _resolve_runtime_object()
        if obj is None:
            raise DeltaSymmetryError("")
        if obj.mode != "EDIT" or bpy.context.edit_object is not obj:
            _stop_mirror_movement_neutral(settings)
            return None
        _disable_native_mirror_axes(obj.data)
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        _validate_capture(obj, bm)
        _validate_runtime_options(bpy.context, obj)
        now = time.monotonic()
        active_shape_key = _active_shape_key_identity(obj)
        if active_shape_key != _RUNTIME.get("active_shape_key"):
            # Switching the edited Shape Key replaces the Edit BMesh coordinates
            # wholesale. That is a context change, not an artist transform, so it
            # must establish a fresh baseline before movement deltas are observed.
            _RUNTIME["active_shape_key"] = active_shape_key
            _RUNTIME["previous"] = _capture_snapshot(obj, bm)
            _RUNTIME["paused"] = False
            _RUNTIME["resample"] = False
            _RUNTIME["next_topology_audit"] = now + DELTA_TOPOLOGY_AUDIT_INTERVAL
            settings.paused = False
            _set_status(settings, "NONE", "")
            return DELTA_TIMER_INTERVAL
        coordinates_changed = _paired_coordinates_changed(
            bm,
            _CAPTURE["pairs"],
            _RUNTIME["previous"],
        )
        if (
            _RUNTIME["resample"]
            or coordinates_changed
            or now >= _RUNTIME["next_topology_audit"]
        ):
            if _topology_signature(bm) != _CAPTURE["topology_signature"]:
                raise DeltaSymmetryError("Topology changed - rebuild the Delta Symmetry pairs.")
            _RUNTIME["next_topology_audit"] = now + DELTA_TOPOLOGY_AUDIT_INTERVAL
        if (
            _CAPTURE.get("orientation_space") != settings.coordinate_space
            or _CAPTURE.get("orientation_axis") != settings.axis
        ):
            _refresh_capture_orientation(obj, bm, settings.coordinate_space, settings.axis)
        if _RUNTIME["paused"] or _RUNTIME["resample"]:
            _RUNTIME["previous"] = _capture_snapshot(obj, bm)
            _RUNTIME["paused"] = False
            _RUNTIME["resample"] = False
            settings.paused = False
            _set_status(settings, "NONE", "")
            return DELTA_TIMER_INTERVAL
        if not coordinates_changed or not allow_writes:
            return DELTA_TIMER_INTERVAL

        _RUNTIME_GUARD = True
        writes, conflicts = apply_delta_updates(
            obj,
            bm,
            _CAPTURE["pairs"],
            _RUNTIME["previous"],
            settings.coordinate_space,
            settings.axis,
            _preferred_movement_drivers(),
        )
        if conflicts:
            _set_status(
                settings,
                "INFO",
                "Mirror Movement skipped an ambiguous two-sided edit.",
            )
        elif writes:
            _set_status(settings, "NONE", "")
    except DeltaSymmetryError as exc:
        _stop_mirror_movement_neutral(settings, str(exc))
        return None
    except Exception as exc:
        traceback.print_exc()
        _stop_mirror_movement_neutral(settings)
        return None
    finally:
        _RUNTIME_GUARD = False
    return DELTA_TIMER_INTERVAL


def _delta_history_post(_scene):
    if _RUNTIME is not None:
        _RUNTIME["history_active"] = False
        _RUNTIME["resample"] = True
        _RUNTIME["next_topology_audit"] = 0.0


def _delta_history_pre(_scene):
    if _RUNTIME is not None:
        _RUNTIME["history_active"] = True


def _delta_depsgraph_update(_scene, depsgraph):
    if _RUNTIME is None or _RUNTIME_GUARD or _RUNTIME.get("history_active"):
        return
    try:
        obj = _resolve_runtime_object()
        mesh = _RUNTIME.get("mesh")
    except (ReferenceError, RuntimeError):
        return
    if obj is None or mesh is None:
        return
    relevant = False
    for update in depsgraph.updates:
        updated_id = update.id
        original = getattr(updated_id, "original", updated_id)
        if any(candidate == obj or candidate == mesh for candidate in (updated_id, original)):
            relevant = True
            break
    if relevant:
        _delta_runtime_tick(allow_writes=True)


def _delta_load_pre(_filepath):
    # Do not remove this callback while Blender is iterating load_pre; doing so
    # can skip a following add-on's callback. Non-persistent load handlers are
    # cleared by Blender after the file load.
    stop_delta_symmetry_runtime(clear_capture=True, remove_load_handler=False)


def _delta_save_pre(_filepath):
    settings = _delta_settings(bpy.context)
    _set_live_enabled(settings, False)
    _restore_native_mirror_axes(_RUNTIME)
    if not bpy.app.timers.is_registered(_delta_stop_after_save):
        bpy.app.timers.register(_delta_stop_after_save, first_interval=0.0)


def _delta_stop_after_save():
    settings = _delta_settings(bpy.context)
    stop_delta_symmetry_runtime(clear_capture=False)
    _set_status(settings, "NONE", "")
    return None


def _live_toggle_updated(settings, context):
    if _TOGGLE_GUARD:
        return
    if settings.live_enabled:
        try:
            _start_runtime(context, settings)
        except DeltaSymmetryError as exc:
            _stop_mirror_movement_neutral(settings, str(exc))
        except Exception:
            traceback.print_exc()
            _stop_mirror_movement_neutral(settings)
    else:
        stop_delta_symmetry_runtime(clear_capture=False)
        _set_status(settings, "NONE", "")


def _orientation_updated(settings, context):
    if _CAPTURE is None:
        return
    if settings.live_enabled:
        stop_delta_symmetry_runtime(clear_capture=False)
    try:
        obj = _resolve_capture_object()
        if obj is None or context.edit_object is not obj or obj.mode != "EDIT":
            _set_status(settings, "INFO", "Axis changed; return to this mesh's Edit Mode to refresh side labels.")
            return
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        _validate_capture(obj, bm)
        if _topology_signature(bm) != _CAPTURE["topology_signature"]:
            raise DeltaSymmetryError("Topology changed - rebuild the Delta Symmetry pairs.")
        _refresh_capture_orientation(obj, bm, settings.coordinate_space, settings.axis)
        _set_status(settings, "SUCCESS", f"Side labels updated for {settings.coordinate_space.title()} {settings.axis}.")
    except (DeltaSymmetryError, ReferenceError, RuntimeError) as exc:
        _set_status(settings, "INFO", str(exc))


class CharacterDesignerDeltaState(PropertyGroup):
    source_object: PointerProperty(type=bpy.types.Object, options={"SKIP_SAVE"})
    captured: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    live_enabled: BoolProperty(
        name="Mirror Movement",
        description="Mirror Edit Mode movement deltas while preserving each side's existing position",
        default=False,
        update=_live_toggle_updated,
        options={"SKIP_SAVE"},
    )
    auto_select_opposite: BoolProperty(
        name="Auto Select Opposite",
        description="Keep every selected paired vertex selected on the opposite side too",
        default=False,
        update=_auto_select_toggle_updated,
        options={"SKIP_SAVE"},
    )
    coordinate_space: EnumProperty(
        name="Space",
        items=(
            ("LOCAL", "Local", "Reflect deltas in the object's local axes"),
            ("WORLD", "World", "Reflect deltas in world axes"),
        ),
        default="LOCAL",
        update=_orientation_updated,
        options={"SKIP_SAVE"},
    )
    axis: EnumProperty(
        name="Axis",
        items=(("X", "X", "X axis"), ("Y", "Y", "Y axis"), ("Z", "Z", "Z axis")),
        default="X",
        update=_orientation_updated,
        options={"SKIP_SAVE"},
    )
    center_count: IntProperty(default=0, min=0, options={"SKIP_SAVE"})
    pair_count: IntProperty(default=0, min=0, options={"SKIP_SAVE"})
    seed_mode: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    paused: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    last_level: StringProperty(default="NONE", options={"HIDDEN", "SKIP_SAVE"})
    last_message: StringProperty(options={"HIDDEN", "SKIP_SAVE"})


class CHARACTERDESIGNER_OT_delta_build_pairs(Operator):
    bl_idname = "character_designer.delta_build_pairs"
    bl_label = "Set Symmetry"
    bl_description = (
        "Build vertex pairs from one selected centerline or two selected center-band boundaries"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        global _CAPTURE, _RUNTIME
        settings = _delta_settings(context)
        # A failed Rebuild must not discard the last proven pair map.
        stop_auto_select_runtime()
        stop_delta_symmetry_runtime(clear_capture=False)
        try:
            obj, bm = _edit_bmesh(context)
            capture = build_pairing_from_selection(
                obj,
                bm,
                settings.coordinate_space,
                settings.axis,
            )
        except DeltaSymmetryError as exc:
            _set_status(settings, "ERROR", str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _CAPTURE = capture
        _CAPTURE["pair_count"] = len(capture["pairs"])
        _RUNTIME = None
        settings.source_object = obj
        settings.captured = True
        settings.center_count = len(capture["center"])
        settings.pair_count = len(capture["pairs"])
        settings.seed_mode = capture["seed_mode"]
        _set_status(settings, "NONE", "")
        if capture["seed_mode"] == "CENTER_BAND":
            message = f"Built {settings.pair_count} pairs from the selected center band."
        else:
            message = (
                f"Built {settings.pair_count} pairs from "
                f"{settings.center_count} center vertices."
            )
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_delta_clear(Operator):
    bl_idname = "character_designer.delta_clear"
    bl_label = "Clear Delta Symmetry"
    bl_description = "Clear the current session's centerline and pair map"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        settings = _delta_settings(context)
        stop_delta_symmetry_runtime(clear_capture=True)
        _set_status(settings, "INFO", "Delta Symmetry pair map cleared.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_delta_select_group(Operator):
    bl_idname = "character_designer.delta_select_group"
    bl_label = "Select Delta Symmetry Group"
    bl_description = "Select the captured negative side, centerline, or positive side"
    bl_options = {"INTERNAL"}

    group: EnumProperty(
        items=(("NEGATIVE", "Negative", "Negative side"), ("CENTER", "Center", "Centerline"), ("POSITIVE", "Positive", "Positive side")),
    )

    @classmethod
    def poll(cls, context):
        try:
            return _CAPTURE is not None and context.edit_object is _resolve_capture_object()
        except (ReferenceError, RuntimeError):
            return False

    def execute(self, context):
        settings = _delta_settings(context)
        try:
            obj, bm = _edit_bmesh(context)
            if obj is not _resolve_capture_object():
                raise DeltaSymmetryError("Build pairs for this mesh first.")
            _validate_capture(obj, bm)
            if _topology_signature(bm) != _CAPTURE["topology_signature"]:
                raise DeltaSymmetryError("Topology changed - rebuild the Delta Symmetry pairs.")
        except (DeltaSymmetryError, ReferenceError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        indices = {
            "NEGATIVE": _CAPTURE["negative"],
            "CENTER": _CAPTURE["center"],
            "POSITIVE": _CAPTURE["positive"],
        }[self.group]
        for vertex in bm.verts:
            vertex.select = False
        for index in indices:
            bm.verts[index].select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        if settings is not None and settings.auto_select_opposite and _AUTO_SELECT is not None:
            _sync_auto_selection(obj, bm, initial=True)
        return {"FINISHED"}


class CHARACTERDESIGNER_PT_delta_symmetry(Panel):
    bl_label = "Delta Symmetry"
    bl_idname = "CHARACTERDESIGNER_PT_delta_symmetry"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_MODELING

    def draw(self, context):
        layout = self.layout
        settings = _delta_settings(context)
        if settings is None:
            layout.label(text="Add-on state unavailable", icon="ERROR")
            return

        axis_row = layout.row(align=True)
        axis_row.enabled = not settings.live_enabled
        axis_row.prop(settings, "coordinate_space", text="")
        axis_row.prop(settings, "axis", expand=True)

        if not settings.captured:
            layout.operator(
                "character_designer.delta_build_pairs",
                text="Set Symmetry",
                icon="MOD_MIRROR",
            )
            layout.label(text="Select one centerline or two band boundaries.", icon="INFO")
        else:
            auto_select = layout.row()
            auto_select.prop(
                settings,
                "auto_select_opposite",
                text="Auto Select Opposite",
                icon="RESTRICT_SELECT_OFF",
                toggle=True,
            )
            movement = layout.row()
            movement.prop(
                settings,
                "live_enabled",
                text="Mirror Movement",
                icon="MOD_MIRROR",
                toggle=True,
            )
            seed_label = "Band" if settings.seed_mode == "CENTER_BAND" else "Center"
            layout.label(
                text=f"{seed_label} {settings.center_count} / Pairs {settings.pair_count}",
                icon="LINKED",
            )
            select_row = layout.row(align=True)
            select_row.operator(
                "character_designer.delta_select_group",
                text="- Side",
            ).group = "NEGATIVE"
            select_row.operator(
                "character_designer.delta_select_group",
                text="Band" if settings.seed_mode == "CENTER_BAND" else "Center",
            ).group = "CENTER"
            select_row.operator(
                "character_designer.delta_select_group",
                text="+ Side",
            ).group = "POSITIVE"
            action_row = layout.row(align=True)
            action_row.operator(
                "character_designer.delta_build_pairs",
                text="Rebuild",
                icon="FILE_REFRESH",
            )
            action_row.operator(
                "character_designer.delta_clear",
                text="",
                icon="X",
            )


DELTA_SYMMETRY_CLASSES = (
    CharacterDesignerDeltaState,
    CHARACTERDESIGNER_OT_delta_build_pairs,
    CHARACTERDESIGNER_OT_delta_clear,
    CHARACTERDESIGNER_OT_delta_select_group,
    CHARACTERDESIGNER_PT_delta_symmetry,
)
