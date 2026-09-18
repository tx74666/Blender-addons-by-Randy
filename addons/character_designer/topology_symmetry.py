"""Transactional replacement of the opposite side with a selected mirror.

Weight Symmetry and Surface Mirror deliberately leave topology alone.  This
module is the explicit, selection-driven topology operation: the selected
face patch is always the source/baseline, its one boundary loop is matched to
the opposite side, and only the opposite patch is replaced.  A boundary-loop
failure never falls back to a nearest arbitrary face.
"""

from dataclasses import dataclass
import hashlib
import math
from statistics import median
import traceback

import bmesh
import bpy
from bpy.props import FloatProperty
from bpy.types import Operator
from mathutils import Vector
from mathutils.kdtree import KDTree

from . import weight_symmetry as ws


TOPOLOGY_TOLERANCE_SCALE = 8.0
TOPOLOGY_TOLERANCE_FRACTION = 0.10
SIDE_EPSILON = 1.0e-8
SKIP_ATTRIBUTES = frozenset({
    "position",
    "material_index",
    "sharp_face",
    "sharp_edge",
    "crease_edge",
    "bevel_weight_edge",
    "bevel_weight_vertex",
})


class TopologySymmetryError(ws.WeightSymmetryError):
    """A safe, artist-facing topology preflight or replacement failure."""


@dataclass(frozen=True)
class TopologyMirrorPlan:
    mesh_obj: object
    armature_obj: object
    target_name: str
    source_name: str
    target_side: int
    source_side: int
    boundary_tolerance: float
    selected_vertices: tuple
    target_faces: tuple
    target_boundary: tuple
    source_boundary: tuple
    source_faces: tuple
    boundary_pairs: tuple
    group_snapshot: tuple
    active_group_index: int
    geometry_fingerprint: str


@dataclass(frozen=True)
class TopologyMirrorResult:
    source_name: str
    target_name: str
    removed_faces: int
    added_faces: int
    removed_vertices: int
    added_vertices: int
    selected_vertices: tuple
    selected_source_vertices: tuple
    selected_target_vertices: tuple


@dataclass(frozen=True)
class TopologyRepairPlan:
    """Append a selected source patch onto the opposite side and weld it safely."""

    mesh_obj: object
    armature_obj: object
    target_name: str
    source_name: str
    target_side: int
    source_side: int
    merge_distance: float
    selected_vertices: tuple
    source_faces: tuple
    source_boundary: tuple
    vertex_pairs: tuple
    group_snapshot: tuple
    active_group_index: int
    geometry_fingerprint: str


@dataclass(frozen=True)
class TopologyRepairResult:
    source_name: str
    target_name: str
    added_faces: int
    welded_vertices: int
    added_vertices: int
    removed_faces: int
    selected_vertices: tuple


def _mirror_point(point):
    result = Vector(point)
    result.x = -result.x
    return result


def _geometry_fingerprint(mesh_obj):
    mesh = mesh_obj.data
    shapes = mesh.shape_keys
    payload = (
        tuple(tuple(vertex.co) for vertex in mesh.vertices),
        tuple(tuple(edge.vertices) for edge in mesh.edges),
        tuple((tuple(poly.vertices), poly.material_index, poly.use_smooth)
              for poly in mesh.polygons),
        tuple((layer.name, tuple(tuple(loop.uv) for loop in layer.data))
              for layer in mesh.uv_layers),
        None if shapes is None else (
            tuple((key.name, key.value, key.mute, key.slider_min,
                   key.slider_max, key.vertex_group,
                   key.relative_key.name if key.relative_key else None,
                   tuple(tuple(point.co) for point in key.data))
                  for key in shapes.key_blocks),
            shapes.use_relative,
            shapes.eval_time,
        ),
        tuple(tuple(row) for row in mesh_obj.matrix_world),
    )
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def _active_pair(context):
    if context.mode != "EDIT_MESH":
        raise TopologySymmetryError(
            "Enter Mesh Edit Mode and select the source region to mirror."
        )
    mesh_obj = context.view_layer.objects.active
    if mesh_obj is None or mesh_obj.type != "MESH":
        raise TopologySymmetryError("Make the Mesh active in Edit Mode.")
    if mesh_obj.data.users != 1:
        raise TopologySymmetryError(
            "Topology Mirror needs a single-user Mesh; make the Mesh Single User first."
        )
    active_group = ws._active_vertex_group(mesh_obj)
    if active_group is None:
        raise TopologySymmetryError(
            "Set the active Vertex Group to either side of the bone pair (.L or .R)."
        )
    active_name = active_group.name
    opposite_name = ws._strict_opposite_name(active_name)
    armature_obj = ws._armature_for_mesh(mesh_obj, active_name, opposite_name)
    tolerance = ws._automatic_tolerance(mesh_obj)
    active_side = ws._bone_source_side(
        mesh_obj, armature_obj, active_name, opposite_name, tolerance
    )
    if active_side not in {-1, 1}:
        raise TopologySymmetryError("Could not resolve the active bone side in Mesh-local X.")
    return mesh_obj, armature_obj, active_name, opposite_name, active_side, tolerance


def _selected_patch(bm, expected_side, tolerance):
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()

    explicitly_selected = {vertex.index for vertex in bm.verts if vertex.select}
    selected_faces = {
        face.index
        for face in bm.faces
        if face.select or all(vertex.select for vertex in face.verts)
    }
    if not selected_faces:
        raise TopologySymmetryError(
            "Select the complete source face region, including its wrist boundary loop."
        )
    selected_vertices = {
        vertex.index
        for face in bm.faces
        if face.index in selected_faces
        for vertex in face.verts
    }
    if explicitly_selected - selected_vertices:
        raise TopologySymmetryError(
            "Some selected vertices do not belong to complete selected faces; "
            "select the whole face region or deselect loose vertices."
        )
    if not selected_vertices:
        raise TopologySymmetryError("The selected source region is empty.")
    if expected_side in {-1, 1} and any(
            vertex.co.x * expected_side <= tolerance
            for vertex in bm.verts if vertex.index in selected_vertices):
        raise TopologySymmetryError(
            "The selected source region must stay on one side of the Mesh center line."
        )

    # More than one selected face component is unsafe: the user may have
    # selected a hand and a nearby accessory at the same time.
    remaining = set(selected_faces)
    components = []
    while remaining:
        start = remaining.pop()
        component = {start}
        pending = [start]
        while pending:
            current = bm.faces[pending.pop()]
            for edge in current.edges:
                for neighbor in edge.link_faces:
                    if neighbor.index in remaining:
                        remaining.remove(neighbor.index)
                        component.add(neighbor.index)
                        pending.append(neighbor.index)
        components.append(component)
    if len(components) != 1:
        raise TopologySymmetryError(
            f"Select one connected source face region; found {len(components)}."
        )

    boundary_edges = []
    selected_face_set = components[0]
    for edge in bm.edges:
        selected_count = sum(face.index in selected_face_set for face in edge.link_faces)
        if selected_count == 1:
            boundary_edges.append(edge)
    if not boundary_edges:
        raise TopologySymmetryError(
            "The selected region has no boundary loop; select the hand below its wrist ring."
        )
    boundary_neighbors = {}
    for edge in boundary_edges:
        first, second = (vertex.index for vertex in edge.verts)
        boundary_neighbors.setdefault(first, set()).add(second)
        boundary_neighbors.setdefault(second, set()).add(first)
    if any(len(neighbors) != 2 for neighbors in boundary_neighbors.values()):
        raise TopologySymmetryError(
            "The selected region boundary branches; select exactly one continuous wrist loop."
        )
    if len(boundary_neighbors) < 3:
        raise TopologySymmetryError("The selected boundary is too small to be a Loop.")

    start = min(boundary_neighbors)
    ordered = [start]
    previous = None
    current = start
    while True:
        candidates = sorted(boundary_neighbors[current] - ({previous} if previous is not None else set()))
        if not candidates:
            raise TopologySymmetryError("The selected boundary Loop is not closed.")
        following = candidates[0]
        if following == start:
            break
        if following in ordered:
            raise TopologySymmetryError("The selected boundary Loop self-intersects.")
        ordered.append(following)
        previous, current = current, following
    if len(ordered) != len(boundary_neighbors):
        raise TopologySymmetryError("The selected boundary contains multiple Loops.")
    return tuple(sorted(selected_vertices)), tuple(sorted(selected_face_set)), tuple(ordered)


def _match_boundary(mesh_obj, target_boundary, source_side, tolerance, boundary_tolerance):
    coordinates = [vertex.co.copy() for vertex in mesh_obj.data.vertices]
    candidates = [
        index for index, point in enumerate(coordinates)
        if point.x * source_side > tolerance
    ]
    if len(candidates) < len(target_boundary):
        raise TopologySymmetryError(
            "The opposite side does not contain enough vertices for the wrist Loop."
        )
    tree = KDTree(len(candidates))
    for local_index, vertex_index in enumerate(candidates):
        tree.insert(coordinates[vertex_index], local_index)
    tree.balance()
    used = set()
    pairs = []
    for target_index in target_boundary:
        reflected = _mirror_point(coordinates[target_index])
        hits = sorted(tree.find_range(reflected, boundary_tolerance),
                      key=lambda item: (item[2], candidates[item[1]]))
        if not hits:
            nearest, nearest_local, distance = tree.find(reflected)
            raise TopologySymmetryError(
                f"No opposite wrist-loop vertex matches target {target_index}; "
                f"nearest distance is {distance:.6g}, tolerance is {boundary_tolerance:.6g}.",
                mesh_obj=mesh_obj,
                vertex_indices=(target_index,),
            )
        best_distance = hits[0][2]
        close = [item for item in hits if abs(item[2] - best_distance) <= boundary_tolerance * 0.1]
        if len(close) != 1:
            raise TopologySymmetryError(
                f"Opposite wrist-loop match for target {target_index} is ambiguous.",
                mesh_obj=mesh_obj,
                vertex_indices=(target_index,),
            )
        local_index = close[0][1]
        if local_index in used:
            raise TopologySymmetryError(
                "Two target Loop vertices map to the same opposite vertex; "
                "the interfaces are not one-to-one."
            )
        used.add(local_index)
        pairs.append((target_index, candidates[local_index]))
    return tuple(pairs)


def _source_faces_for_boundary(bm, boundary_pairs, target_boundary,
                               target_points, source_side, tolerance):
    source_by_target = dict(boundary_pairs)
    source_boundary = tuple(source_by_target[index] for index in target_boundary)
    source_edges = []
    for first, second in zip(source_boundary, source_boundary[1:] + source_boundary[:1]):
        edge = bm.edges.get((bm.verts[first], bm.verts[second]))
        if edge is None:
            raise TopologySymmetryError(
                "The opposite wrist vertices exist, but their edges do not form the same Loop."
            )
        source_edges.append(edge)
    blocked = {edge.index for edge in source_edges}

    remaining = {face.index for face in bm.faces}
    components = []
    while remaining:
        start = remaining.pop()
        component = {start}
        pending = [start]
        while pending:
            current = bm.faces[pending.pop()]
            for edge in current.edges:
                if edge.index in blocked:
                    continue
                for neighbor in edge.link_faces:
                    if neighbor.index in remaining:
                        remaining.remove(neighbor.index)
                        component.add(neighbor.index)
                        pending.append(neighbor.index)
        components.append(component)

    adjacent = {
        face.index
        for edge in source_edges
        for face in edge.link_faces
    }
    candidates = [component for component in components if component & adjacent]
    if not candidates:
        raise TopologySymmetryError("Could not find a source surface beside the opposite Loop.")

    coordinates = [vertex.co.copy() for vertex in bm.verts]
    mirrored_target = [_mirror_point(point) for point in target_points]
    target_center = sum(mirrored_target, Vector()) / len(mirrored_target)
    scored = []
    for component in candidates:
        vertex_indices = {
            vertex.index
            for face_index in component
            for vertex in bm.faces[face_index].verts
        }
        if any(coordinates[index].x * source_side <= -tolerance
               for index in vertex_indices):
            continue
        source_points = [coordinates[index] for index in vertex_indices]
        mirrored_source = [_mirror_point(point) for point in source_points]
        mean_target_distance = sum(
            min((point - candidate).length for candidate in mirrored_source)
            for point in mirrored_target
        ) / len(mirrored_target)
        center = sum(mirrored_source, Vector()) / len(mirrored_source)
        center_distance = (center - target_center).length
        score = mean_target_distance + center_distance * 0.25
        scored.append((score, component, vertex_indices))
    if not scored:
        raise TopologySymmetryError(
            "The opposite surface crosses the center line; source patch is unsafe."
        )
    scored.sort(key=lambda item: item[0])
    if len(scored) > 1 and scored[1][0] - scored[0][0] <= max(tolerance * 8, 1.0e-7):
        raise TopologySymmetryError(
            "The opposite Loop has two equally plausible surface regions; "
            "select a cleaner wrist boundary."
        )
    source_faces = tuple(sorted(scored[0][1]))
    source_vertex_set = scored[0][2]
    source_boundary_set = set(source_boundary)
    source_interior = source_vertex_set - source_boundary_set
    if not source_interior:
        raise TopologySymmetryError("The opposite Loop encloses no replaceable source surface.")
    # Return the source face region and the mapped source boundary in target
    # Loop order.  The caller stores the interior implicitly from the faces.
    return source_boundary, source_faces


def build_topology_mirror_plan(context, *, boundary_tolerance=0.0):
    (
        mesh_obj,
        armature_obj,
        active_name,
        opposite_name,
        active_side,
        tolerance,
    ) = _active_pair(context)
    bm = bmesh.from_edit_mesh(mesh_obj.data)
    # Synchronize completed artist edits from the Edit BMesh before taking a
    # fingerprint.  Re-fetch the BMesh because update_edit_mesh may rebuild
    # its lookup tables while retaining the user's selection.
    bmesh.update_edit_mesh(mesh_obj.data, loop_triangles=False, destructive=False)
    bm = bmesh.from_edit_mesh(mesh_obj.data)
    selected_vertices, selected_faces, selected_boundary = _selected_patch(
        bm, None, tolerance
    )
    coordinates = [vertex.co.copy() for vertex in bm.verts]
    selected_x = [coordinates[index].x for index in selected_vertices]
    selected_on_active = all(
        value * active_side > tolerance for value in selected_x
    )
    selected_on_opposite = all(
        value * -active_side > tolerance for value in selected_x
    )
    if not selected_on_active and not selected_on_opposite:
        mixed_indices = tuple(
            index for index in selected_vertices
            if abs(coordinates[index].x) <= tolerance
            or coordinates[index].x * active_side <= tolerance
            and coordinates[index].x * -active_side <= tolerance
        )
        if not mixed_indices:
            mixed_indices = tuple(
                index for index in selected_vertices
                if coordinates[index].x * active_side <= tolerance
            )
        raise TopologySymmetryError(
            "The selected source region crosses the Mesh center line or contains both sides. "
            "Select only one side; the opposite side will be replaced automatically. "
            f'The first conflicting vertex is '
            f'{mixed_indices[0] if mixed_indices else selected_vertices[0]}.',
            mesh_obj=mesh_obj,
            vertex_indices=mixed_indices or (selected_vertices[0],),
        )
    if selected_on_active:
        source_name = active_name
        target_name = opposite_name
        source_side = active_side
        target_side = -active_side
    else:
        source_name = opposite_name
        target_name = active_name
        source_side = -active_side
        target_side = active_side
    edge_lengths = [
        (coordinates[first] - coordinates[second]).length
        for first, second in zip(
            selected_boundary,
            selected_boundary[1:] + selected_boundary[:1],
        )
    ]
    auto_boundary_tolerance = max(
        tolerance * TOPOLOGY_TOLERANCE_SCALE,
        median(edge_lengths) * TOPOLOGY_TOLERANCE_FRACTION,
    )
    try:
        boundary_tolerance = float(boundary_tolerance)
    except (TypeError, ValueError):
        raise TopologySymmetryError("Boundary tolerance must be finite and non-negative.")
    if not math.isfinite(boundary_tolerance) or boundary_tolerance < 0:
        raise TopologySymmetryError("Boundary tolerance must be finite and non-negative.")
    boundary_tolerance = boundary_tolerance or auto_boundary_tolerance
    source_boundary = selected_boundary
    source_faces = selected_faces
    source_to_target = _match_boundary(
        mesh_obj,
        selected_boundary,
        target_side,
        tolerance,
        boundary_tolerance,
    )
    target_boundary, target_faces = _source_faces_for_boundary(
        bm,
        source_to_target,
        selected_boundary,
        [coordinates[index] for index in selected_boundary],
        target_side,
        tolerance,
    )
    boundary_pairs = tuple(
        (target_index, source_index)
        for source_index, target_index in source_to_target
    )
    group_snapshot = ws._capture_vertex_groups(mesh_obj)
    active_group_index = mesh_obj.vertex_groups.active_index
    # Blender may normalize the edit BMesh when leaving Edit Mode.  Capture
    # the stale-check fingerprint in the same Object Mode representation that
    # the commit will use, then return the artist to Edit Mode unchanged.
    bpy.ops.object.mode_set(mode="OBJECT")
    try:
        geometry_fingerprint = _geometry_fingerprint(mesh_obj)
    finally:
        bpy.ops.object.mode_set(mode="EDIT")
    return TopologyMirrorPlan(
        mesh_obj=mesh_obj,
        armature_obj=armature_obj,
        target_name=target_name,
        source_name=source_name,
        target_side=target_side,
        source_side=source_side,
        boundary_tolerance=boundary_tolerance,
        selected_vertices=selected_vertices,
        target_faces=target_faces,
        target_boundary=target_boundary,
        source_boundary=tuple(source_boundary),
        source_faces=tuple(source_faces),
        boundary_pairs=tuple(boundary_pairs),
        group_snapshot=group_snapshot,
        active_group_index=active_group_index,
        geometry_fingerprint=geometry_fingerprint,
    )


def build_topology_repair_plan(context, *, merge_distance=0.0):
    """Build a source-selected patch repair without requiring equal target topology.

    This deliberately does not guess an arbitrary destination face region.  It
    mirrors the selected source faces, maps nearby opposite-side vertices, and
    appends only faces that are not already present.  Existing target faces
    made entirely from mapped vertices are replaced, which makes a hole or a
    locally deleted vertex repairable while keeping unrelated surrounding
    topology intact.
    """

    (
        mesh_obj,
        armature_obj,
        active_name,
        opposite_name,
        active_side,
        tolerance,
    ) = _active_pair(context)
    bm = bmesh.from_edit_mesh(mesh_obj.data)
    bmesh.update_edit_mesh(mesh_obj.data, loop_triangles=False, destructive=False)
    bm = bmesh.from_edit_mesh(mesh_obj.data)
    selected_vertices, selected_faces, selected_boundary = _selected_patch(
        bm, None, tolerance
    )
    coordinates = [vertex.co.copy() for vertex in bm.verts]
    selected_x = [coordinates[index].x for index in selected_vertices]
    selected_on_active = all(value * active_side > tolerance for value in selected_x)
    selected_on_opposite = all(value * -active_side > tolerance for value in selected_x)
    if not selected_on_active and not selected_on_opposite:
        raise TopologySymmetryError(
            "The selected source region crosses the Mesh center line or contains both sides. "
            "Select only the intact source side."
        )
    if selected_on_active:
        source_name, target_name = active_name, opposite_name
        source_side, target_side = active_side, -active_side
    else:
        source_name, target_name = opposite_name, active_name
        source_side, target_side = -active_side, active_side

    edge_lengths = [
        (coordinates[first] - coordinates[second]).length
        for first, second in zip(
            selected_boundary,
            selected_boundary[1:] + selected_boundary[:1],
        )
    ]
    auto_distance = max(
        tolerance * TOPOLOGY_TOLERANCE_SCALE,
        median(edge_lengths) * 0.03,
    )
    try:
        merge_distance = float(merge_distance)
    except (TypeError, ValueError):
        raise TopologySymmetryError("Merge distance must be finite and non-negative.")
    if not math.isfinite(merge_distance) or merge_distance < 0:
        raise TopologySymmetryError("Merge distance must be finite and non-negative.")
    merge_distance = merge_distance or auto_distance
    if merge_distance <= SIDE_EPSILON:
        raise TopologySymmetryError("Merge distance is too small for this mesh.")

    candidates = [
        index
        for index, point in enumerate(coordinates)
        if point.x * target_side > tolerance and index not in selected_vertices
    ]
    tree = KDTree(len(candidates))
    for local_index, vertex_index in enumerate(candidates):
        tree.insert(coordinates[vertex_index], local_index)
    tree.balance()
    used_targets = set()
    vertex_pairs = []
    for source_index in sorted(selected_vertices):
        reflected = _mirror_point(coordinates[source_index])
        if not candidates:
            vertex_pairs.append((source_index, None))
            continue
        hits = sorted(
            tree.find_range(reflected, merge_distance),
            key=lambda item: (item[2], candidates[item[1]]),
        )
        if not hits:
            vertex_pairs.append((source_index, None))
            continue
        best_distance = hits[0][2]
        close = [
            item
            for item in hits
            if abs(item[2] - best_distance) <= merge_distance * 0.1
        ]
        if len(close) != 1:
            raise TopologySymmetryError(
                f"Opposite vertex match for source {source_index} is ambiguous.",
                mesh_obj=mesh_obj,
                vertex_indices=(source_index,),
            )
        target_index = candidates[close[0][1]]
        if target_index in used_targets:
            raise TopologySymmetryError(
                "Two selected source vertices would merge into one opposite vertex.",
                mesh_obj=mesh_obj,
                vertex_indices=(source_index, target_index),
            )
        used_targets.add(target_index)
        vertex_pairs.append((source_index, target_index))

    boundary_welded = sum(
        target_index is not None
        for source_index, target_index in vertex_pairs
        if source_index in set(selected_boundary)
    )
    minimum_boundary_matches = max(3, len(selected_boundary) - 1)
    if boundary_welded < minimum_boundary_matches:
        raise TopologySymmetryError(
            f"Only {boundary_welded}/{len(selected_boundary)} boundary vertices can be welded. "
            "Select a larger intact boundary or increase Merge Distance carefully."
        )

    group_snapshot = ws._capture_vertex_groups(mesh_obj)
    active_group_index = mesh_obj.vertex_groups.active_index
    bpy.ops.object.mode_set(mode="OBJECT")
    try:
        geometry_fingerprint = _geometry_fingerprint(mesh_obj)
    finally:
        bpy.ops.object.mode_set(mode="EDIT")
    return TopologyRepairPlan(
        mesh_obj=mesh_obj,
        armature_obj=armature_obj,
        target_name=target_name,
        source_name=source_name,
        target_side=target_side,
        source_side=source_side,
        merge_distance=merge_distance,
        selected_vertices=tuple(selected_vertices),
        source_faces=tuple(selected_faces),
        source_boundary=tuple(selected_boundary),
        vertex_pairs=tuple(vertex_pairs),
        group_snapshot=group_snapshot,
        active_group_index=active_group_index,
        geometry_fingerprint=geometry_fingerprint,
    )


def _copy_rna_value(source, target):
    for property_name in ("value", "vector", "color", "uv", "quaternion", "byte_color"):
        if not hasattr(source, property_name) or not hasattr(target, property_name):
            continue
        try:
            setattr(target, property_name, getattr(source, property_name))
            return
        except (AttributeError, TypeError, ValueError):
            continue


def _copy_attributes(old_mesh, new_mesh, vertex_origins, face_origins,
                     loop_origins, edge_origins):
    domain_origins = {
        "POINT": vertex_origins,
        "FACE": face_origins,
        "CORNER": loop_origins,
        "EDGE": edge_origins,
    }
    for attribute in old_mesh.attributes:
        if attribute.name in SKIP_ATTRIBUTES:
            continue
        origins = domain_origins.get(attribute.domain)
        if origins is None:
            continue
        if new_mesh.attributes.get(attribute.name) is not None:
            continue
        try:
            created = new_mesh.attributes.new(
                name=attribute.name,
                type=attribute.data_type,
                domain=attribute.domain,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise TopologySymmetryError(
                f'Cannot preserve Mesh attribute "{attribute.name}" during topology replacement.'
            ) from error
        for destination_index, origin in enumerate(origins):
            source_index = origin[1]
            _copy_rna_value(attribute.data[source_index], created.data[destination_index])


def _shape_key_snapshot(mesh):
    shapes = mesh.shape_keys
    if shapes is None:
        return ()
    return tuple(
        {
            "name": key.name,
            "value": key.value,
            "mute": key.mute,
            "slider_min": key.slider_min,
            "slider_max": key.slider_max,
            "vertex_group": key.vertex_group,
            "relative_key": key.relative_key.name if key.relative_key else None,
            "interpolation": getattr(key, "interpolation", None),
            "frame": getattr(key, "frame", None),
            "coords": tuple(tuple(point.co) for point in key.data),
        }
        for key in shapes.key_blocks
    ), shapes.use_relative, shapes.eval_time


def _populate_shape_keys(obj, old_mesh, new_mesh, vertex_origins):
    snapshot = _shape_key_snapshot(old_mesh)
    if not snapshot:
        return
    key_snapshots, use_relative, eval_time = snapshot
    for key_snapshot in key_snapshots:
        key = obj.shape_key_add(name=key_snapshot["name"])
        for destination_index, (kind, source_index) in enumerate(vertex_origins):
            point = Vector(key_snapshot["coords"][source_index])
            if kind == "source":
                point.x = -point.x
            key.data[destination_index].co = point
        for property_name in ("value", "mute", "slider_min", "slider_max", "vertex_group", "interpolation", "frame"):
            value = key_snapshot.get(property_name)
            if value is not None and hasattr(key, property_name):
                try:
                    setattr(key, property_name, value)
                except (AttributeError, TypeError, ValueError):
                    pass
    shapes = new_mesh.shape_keys
    shapes.use_relative = use_relative
    shapes.eval_time = eval_time
    for key_snapshot in key_snapshots:
        key = shapes.key_blocks.get(key_snapshot["name"])
        relative_name = key_snapshot["relative_key"]
        if key is not None and relative_name:
            key.relative_key = shapes.key_blocks.get(relative_name)


def _mirror_group_name(name, names):
    try:
        opposite = ws._strict_opposite_name(name)
    except ws.WeightSymmetryError:
        return name
    if opposite not in names:
        raise TopologySymmetryError(
            f'Source Vertex Group "{name}" has no opposite group "{opposite}".'
        )
    return opposite


def _expected_group_maps(old_states, vertex_origins):
    old_maps = {state.name: dict(state.weights) for state in old_states}
    names = set(old_maps)
    expected = {name: {} for name in old_maps}
    source_group_maps = {}
    for name, weights in old_maps.items():
        source_group_maps[name] = weights
    for output_index, (kind, source_index) in enumerate(vertex_origins):
        if kind == "old":
            for name, weights in old_maps.items():
                if source_index in weights:
                    expected[name][output_index] = weights[source_index]
            continue
        for name, weights in source_group_maps.items():
            if source_index not in weights:
                continue
            destination_name = _mirror_group_name(name, names)
            expected[destination_name][output_index] = weights[source_index]
    return expected


def _apply_group_maps(obj, states, expected):
    for state in states:
        group = obj.vertex_groups.get(state.name)
        if group is None:
            raise TopologySymmetryError(f'Vertex Group "{state.name}" disappeared during replacement.')
        group.lock_weight = False
        ws._clear_group(group, len(obj.data.vertices))
        for index, weight in sorted(expected[state.name].items()):
            group.add((index,), weight, "REPLACE")
        group.lock_weight = state.lock_weight
    obj.data.update()


def _build_replacement_mesh(obj, plan):
    old_mesh = obj.data
    old_vertices = [vertex.co.copy() for vertex in old_mesh.vertices]
    old_polygons = tuple(old_mesh.polygons)
    old_edges = tuple(old_mesh.edges)
    target_face_set = set(plan.target_faces)
    target_boundary_set = set(plan.target_boundary)
    source_boundary_set = set(plan.source_boundary)
    target_interior = {
        vertex_index
        for face_index in plan.target_faces
        for vertex_index in old_polygons[face_index].vertices
    } - target_boundary_set
    source_vertices = {
        vertex_index
        for face_index in plan.source_faces
        for vertex_index in old_polygons[face_index].vertices
    }
    source_interior = source_vertices - source_boundary_set
    if not source_interior:
        raise TopologySymmetryError("The source region has no interior vertices to replace.")

    old_to_new = {}
    vertex_origins = []
    coordinates = []
    for old_index, point in enumerate(old_vertices):
        if old_index in target_interior:
            continue
        old_to_new[old_index] = len(coordinates)
        coordinates.append(point)
        vertex_origins.append(("old", old_index))

    source_to_output = {}
    for target_index, source_index in plan.boundary_pairs:
        output_index = old_to_new[target_index]
        source_to_output[source_index] = output_index
        # Move the shared interface onto the reflected source Loop.  The
        # retained outside faces use these same vertices, so this closes the
        # seam without creating a second, nearly-coincident boundary.
        coordinates[output_index] = _mirror_point(old_vertices[source_index])
        vertex_origins[output_index] = ("source", source_index)
    for source_index in sorted(source_interior):
        source_to_output[source_index] = len(coordinates)
        coordinates.append(_mirror_point(old_vertices[source_index]))
        vertex_origins.append(("source", source_index))

    faces = []
    face_origins = []
    loop_origins = []
    for polygon_index, polygon in enumerate(old_polygons):
        if polygon_index in target_face_set:
            continue
        vertices = tuple(old_to_new[index] for index in polygon.vertices)
        faces.append(vertices)
        face_origins.append(("old", polygon_index))
        loop_origins.extend(("old", loop_index) for loop_index in polygon.loop_indices)

    for polygon_index in plan.source_faces:
        polygon = old_polygons[polygon_index]
        source_indices = tuple(polygon.vertices)
        vertices = tuple(reversed(tuple(source_to_output[index] for index in source_indices)))
        faces.append(vertices)
        face_origins.append(("source", polygon_index))
        loop_origins.extend(("source", loop_index) for loop_index in reversed(tuple(polygon.loop_indices)))

    old_edge_lookup = {
        tuple(sorted(edge.vertices)): edge.index
        for edge in old_edges
    }
    edge_keys = set()
    edges = []
    edge_origins = []
    for face, face_origin in zip(faces, face_origins):
        origin_kind, polygon_index = face_origin
        source_indices = tuple(old_polygons[polygon_index].vertices)
        face_origin_indices = (
            source_indices
            if origin_kind == "old"
            else tuple(reversed(source_indices))
        )
        for loop_position, (first, second) in enumerate(
                zip(face, face[1:] + face[:1])):
            key = tuple(sorted((first, second)))
            if key in edge_keys:
                continue
            edge_keys.add(key)
            edges.append((first, second))
            original_pair = tuple(sorted((
                face_origin_indices[loop_position],
                face_origin_indices[(loop_position + 1) % len(face_origin_indices)],
            )))
            edge_origins.append((origin_kind, old_edge_lookup.get(original_pair, 0)))

    new_mesh = bpy.data.meshes.new(old_mesh.name + ".TopologyMirror")
    new_mesh.from_pydata(coordinates, edges, faces)
    new_mesh.update()
    for material in old_mesh.materials:
        new_mesh.materials.append(material)
    for new_polygon, origin in zip(new_mesh.polygons, face_origins):
        source_polygon = old_polygons[origin[1]]
        new_polygon.material_index = source_polygon.material_index
        new_polygon.use_smooth = source_polygon.use_smooth
    for old_layer in old_mesh.uv_layers:
        new_layer = new_mesh.uv_layers.new(name=old_layer.name)
        for loop_index, origin in enumerate(loop_origins):
            new_layer.data[loop_index].uv = old_layer.data[origin[1]].uv
        if old_layer.active_render:
            new_layer.active_render = True
    new_mesh.uv_layers.active_index = min(
        old_mesh.uv_layers.active_index,
        max(0, len(new_mesh.uv_layers) - 1),
    ) if new_mesh.uv_layers else 0
    for key, value in old_mesh.items():
        if key != "_RNA_UI":
            new_mesh[key] = value
    _copy_attributes(
        old_mesh,
        new_mesh,
        vertex_origins,
        face_origins,
        loop_origins,
        edge_origins,
    )
    selected_source_vertices = {
        old_to_new[index] for index in source_vertices
    }
    selected_target_vertices = {
        old_to_new[index] for index in target_boundary_set
    }
    selected_target_vertices.update(
        source_to_output[index] for index in source_interior
    )
    return (
        new_mesh,
        vertex_origins,
        tuple(sorted(selected_source_vertices | selected_target_vertices)),
        tuple(sorted(selected_source_vertices)),
        tuple(sorted(selected_target_vertices)),
    )


def _build_repair_mesh(obj, plan):
    """Mirror selected faces into a locally missing target patch.

    Existing opposite vertices inside the selected patch are reused when they
    are within the explicit merge distance.  Existing faces made entirely from
    those reused vertices are removed, then the reflected source faces are
    added.  The surrounding mesh and every unrelated face remain untouched.
    """

    old_mesh = obj.data
    old_vertices = [vertex.co.copy() for vertex in old_mesh.vertices]
    old_polygons = tuple(old_mesh.polygons)
    old_edges = tuple(old_mesh.edges)
    vertex_pairs = dict(plan.vertex_pairs)
    target_vertices = {
        target_index
        for target_index in vertex_pairs.values()
        if target_index is not None
    }
    if len(target_vertices) < 3:
        raise TopologySymmetryError(
            "Repair needs at least three opposite vertices to weld the copied patch."
        )

    remove_face_set = {
        polygon.index
        for polygon in old_polygons
        if polygon.vertices
        and set(polygon.vertices).issubset(target_vertices)
    }

    coordinates = list(old_vertices)
    vertex_origins = [("old", index) for index in range(len(old_vertices))]
    old_to_new = {index: index for index in range(len(old_vertices))}
    target_to_source = {
        target_index: source_index
        for source_index, target_index in plan.vertex_pairs
        if target_index is not None
    }
    for target_index, source_index in target_to_source.items():
        coordinates[target_index] = _mirror_point(old_vertices[source_index])
        vertex_origins[target_index] = ("source", source_index)

    source_to_output = {}
    for source_index, target_index in plan.vertex_pairs:
        if target_index is None:
            source_to_output[source_index] = len(coordinates)
            coordinates.append(_mirror_point(old_vertices[source_index]))
            vertex_origins.append(("source", source_index))
        else:
            source_to_output[source_index] = old_to_new[target_index]

    faces = []
    face_origins = []
    loop_origins = []
    for polygon_index, polygon in enumerate(old_polygons):
        if polygon_index in remove_face_set:
            continue
        vertices = tuple(old_to_new[index] for index in polygon.vertices)
        faces.append(vertices)
        face_origins.append(("old", polygon_index))
        loop_origins.extend(("old", loop_index) for loop_index in polygon.loop_indices)

    existing_face_keys = {frozenset(face) for face in faces}
    added_face_keys = set()
    added_faces = 0
    for polygon_index in plan.source_faces:
        polygon = old_polygons[polygon_index]
        vertices = tuple(
            reversed(tuple(source_to_output[index] for index in polygon.vertices))
        )
        if len(set(vertices)) != len(vertices):
            raise TopologySymmetryError(
                f"Mirrored source face {polygon_index} collapses after welding; "
                "increase the repair boundary or reduce Merge Distance."
            )
        key = frozenset(vertices)
        if key in existing_face_keys or key in added_face_keys:
            continue
        faces.append(vertices)
        face_origins.append(("source", polygon_index))
        loop_origins.extend(
            ("source", loop_index) for loop_index in reversed(tuple(polygon.loop_indices))
        )
        added_face_keys.add(key)
        added_faces += 1
    if not added_faces:
        raise TopologySymmetryError(
            "The mirrored patch would add no new faces; select the intact source faces around the damaged area."
        )

    old_edge_lookup = {
        tuple(sorted(edge.vertices)): edge.index
        for edge in old_edges
    }
    edge_keys = set()
    edges = []
    edge_origins = []
    for face, face_origin in zip(faces, face_origins):
        origin_kind, polygon_index = face_origin
        source_indices = tuple(old_polygons[polygon_index].vertices)
        face_origin_indices = (
            source_indices
            if origin_kind == "old"
            else tuple(reversed(source_indices))
        )
        for loop_position, (first, second) in enumerate(
                zip(face, face[1:] + face[:1])):
            key = tuple(sorted((first, second)))
            if key in edge_keys:
                continue
            edge_keys.add(key)
            edges.append((first, second))
            original_pair = tuple(sorted((
                face_origin_indices[loop_position],
                face_origin_indices[(loop_position + 1) % len(face_origin_indices)],
            )))
            edge_origins.append((origin_kind, old_edge_lookup.get(original_pair, 0)))

    new_mesh = bpy.data.meshes.new(old_mesh.name + ".TopologyRepair")
    new_mesh.from_pydata(coordinates, edges, faces)
    new_mesh.update()
    for material in old_mesh.materials:
        new_mesh.materials.append(material)
    for new_polygon, origin in zip(new_mesh.polygons, face_origins):
        source_polygon = old_polygons[origin[1]]
        new_polygon.material_index = source_polygon.material_index
        new_polygon.use_smooth = source_polygon.use_smooth
    for old_layer in old_mesh.uv_layers:
        new_layer = new_mesh.uv_layers.new(name=old_layer.name)
        for loop_index, origin in enumerate(loop_origins):
            new_layer.data[loop_index].uv = old_layer.data[origin[1]].uv
        if old_layer.active_render:
            new_layer.active_render = True
    new_mesh.uv_layers.active_index = min(
        old_mesh.uv_layers.active_index,
        max(0, len(new_mesh.uv_layers) - 1),
    ) if new_mesh.uv_layers else 0
    for key, value in old_mesh.items():
        if key != "_RNA_UI":
            new_mesh[key] = value
    _copy_attributes(
        old_mesh,
        new_mesh,
        vertex_origins,
        face_origins,
        loop_origins,
        edge_origins,
    )
    selected_source_vertices = set(
        old_to_new[index] for index in plan.selected_vertices
    )
    selected_target_vertices = set(
        source_to_output[index] for index in plan.selected_vertices
    )
    return (
        new_mesh,
        vertex_origins,
        tuple(sorted(selected_source_vertices | selected_target_vertices)),
        added_faces,
        len(target_vertices),
        len(coordinates) - len(old_vertices),
        len(remove_face_set),
    )


def _verify_shape_keys(obj, old_snapshot, vertex_origins):
    new_snapshot = _shape_key_snapshot(obj.data)
    if not old_snapshot:
        return
    keys, _use_relative, _eval_time = new_snapshot
    if len(keys) != len(old_snapshot[0]):
        raise TopologySymmetryError("Shape Key count changed during topology replacement.")
    for key_snapshot, key in zip(old_snapshot[0], keys):
        if key_snapshot["name"] != key["name"]:
            raise TopologySymmetryError("Shape Key order changed during topology replacement.")
        for output_index, (kind, source_index) in enumerate(vertex_origins):
            expected = Vector(key_snapshot["coords"][source_index])
            if kind == "source":
                expected.x = -expected.x
            if (Vector(key["coords"][output_index]) - expected).length > ws.WEIGHT_TOLERANCE:
                raise TopologySymmetryError(
                    f'Shape Key "{key["name"]}" was not mirrored consistently.'
                )


def apply_topology_mirror_plan(plan):
    obj = plan.mesh_obj
    if obj.data.users != 1:
        raise TopologySymmetryError("The Mesh became shared after planning; run the operation again.")
    if _geometry_fingerprint(obj) != plan.geometry_fingerprint:
        raise TopologySymmetryError(
            "Mesh geometry or Shape Keys changed after planning; select the region again."
        )
    if ws._capture_vertex_groups(obj) != plan.group_snapshot:
        raise TopologySymmetryError(
            "Vertex Groups changed after planning; run Topology Mirror again."
        )

    old_mesh = obj.data
    old_groups = plan.group_snapshot
    old_shape_keys = _shape_key_snapshot(old_mesh)
    active_index = plan.active_group_index
    new_mesh = None
    try:
        (
            new_mesh,
            vertex_origins,
            selected_output,
            selected_source_vertices,
            selected_target_vertices,
        ) = _build_replacement_mesh(obj, plan)
        expected_groups = _expected_group_maps(old_groups, vertex_origins)
        for state in old_groups:
            if state.lock_weight and dict(state.weights) != expected_groups[state.name]:
                raise TopologySymmetryError(
                    f'Topology replacement would change locked Vertex Group "{state.name}".'
                )
        obj.data = new_mesh
        for group in tuple(obj.vertex_groups):
            obj.vertex_groups.remove(group)
        for state in old_groups:
            group = obj.vertex_groups.new(name=state.name)
            group.lock_weight = state.lock_weight
        _populate_shape_keys(obj, old_mesh, new_mesh, vertex_origins)
        _apply_group_maps(obj, old_groups, expected_groups)
        obj.vertex_groups.active_index = min(
            max(0, active_index), len(obj.vertex_groups) - 1
        )
        _verify_shape_keys(obj, old_shape_keys, vertex_origins)
        if ws._capture_vertex_groups(obj) != tuple(
                ws.VertexGroupState(state.name, state.index, state.lock_weight,
                                    tuple(sorted(expected_groups[state.name].items())))
                for state in old_groups
        ):
            raise TopologySymmetryError("Topology replacement weight verification failed.")
        obj.data.update()
    except Exception as error:
        try:
            if obj.data is new_mesh:
                obj.data = old_mesh
            ws._restore_vertex_groups(obj, old_groups, active_index)
        except Exception as rollback_error:
            raise ws.WeightSymmetryRollbackError(
                f"Topology Mirror failed ({error}); exact rollback also failed ({rollback_error})."
            ) from rollback_error
        if isinstance(error, (TopologySymmetryError, ws.WeightSymmetryError)):
            raise
        raise TopologySymmetryError(f"Topology Mirror was rolled back: {error}") from error

    return TopologyMirrorResult(
        source_name=plan.source_name,
        target_name=plan.target_name,
        removed_faces=len(plan.target_faces),
        added_faces=len(plan.source_faces),
        removed_vertices=len({
            index
            for face_index in plan.target_faces
            for index in old_mesh.polygons[face_index].vertices
        } - set(plan.target_boundary)),
        added_vertices=len({
            index
            for face_index in plan.source_faces
            for index in old_mesh.polygons[face_index].vertices
        } - set(plan.source_boundary)),
        selected_vertices=tuple(selected_output),
        selected_source_vertices=tuple(selected_source_vertices),
        selected_target_vertices=tuple(selected_target_vertices),
    )


def apply_topology_repair_plan(plan):
    """Apply a selected-patch repair transactionally and preserve all data."""

    obj = plan.mesh_obj
    if obj.data.users != 1:
        raise TopologySymmetryError("The Mesh became shared after planning; run Repair again.")
    if _geometry_fingerprint(obj) != plan.geometry_fingerprint:
        raise TopologySymmetryError(
            "Mesh geometry or Shape Keys changed after planning; select the repair region again."
        )
    if ws._capture_vertex_groups(obj) != plan.group_snapshot:
        raise TopologySymmetryError(
            "Vertex Groups changed after planning; run Repair again."
        )

    old_mesh = obj.data
    old_groups = plan.group_snapshot
    old_shape_keys = _shape_key_snapshot(old_mesh)
    active_index = plan.active_group_index
    new_mesh = None
    try:
        (
            new_mesh,
            vertex_origins,
            selected_output,
            added_faces,
            welded_vertices,
            added_vertices,
            removed_faces,
        ) = _build_repair_mesh(obj, plan)
        expected_groups = _expected_group_maps(old_groups, vertex_origins)
        for state in old_groups:
            if state.lock_weight and dict(state.weights) != expected_groups[state.name]:
                raise TopologySymmetryError(
                    f'Repair would change locked Vertex Group "{state.name}".'
                )
        obj.data = new_mesh
        for group in tuple(obj.vertex_groups):
            obj.vertex_groups.remove(group)
        for state in old_groups:
            group = obj.vertex_groups.new(name=state.name)
            group.lock_weight = state.lock_weight
        _populate_shape_keys(obj, old_mesh, new_mesh, vertex_origins)
        _apply_group_maps(obj, old_groups, expected_groups)
        obj.vertex_groups.active_index = min(
            max(0, active_index), len(obj.vertex_groups) - 1
        )
        _verify_shape_keys(obj, old_shape_keys, vertex_origins)
        if ws._capture_vertex_groups(obj) != tuple(
                ws.VertexGroupState(
                    state.name,
                    state.index,
                    state.lock_weight,
                    tuple(sorted(expected_groups[state.name].items())),
                )
                for state in old_groups
        ):
            raise TopologySymmetryError("Topology repair weight verification failed.")
        obj.data.update()
    except Exception as error:
        try:
            if obj.data is new_mesh:
                obj.data = old_mesh
            ws._restore_vertex_groups(obj, old_groups, active_index)
        except Exception as rollback_error:
            raise ws.WeightSymmetryRollbackError(
                f"Topology repair failed ({error}); exact rollback also failed ({rollback_error})."
            ) from rollback_error
        if isinstance(error, (TopologySymmetryError, ws.WeightSymmetryError)):
            raise
        raise TopologySymmetryError(f"Topology repair was rolled back: {error}") from error

    return TopologyRepairResult(
        source_name=plan.source_name,
        target_name=plan.target_name,
        added_faces=added_faces,
        welded_vertices=welded_vertices,
        added_vertices=added_vertices,
        removed_faces=removed_faces,
        selected_vertices=tuple(selected_output),
    )


def _select_result_region(obj, selected_vertices):
    selected = set(selected_vertices)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(vertex.index in selected)
    for edge in bm.edges:
        edge.select_set(all(vertex.index in selected for vertex in edge.verts))
    for face in bm.faces:
        face.select_set(all(vertex.index in selected for vertex in face.verts))
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


class CHARACTERDESIGNER_OT_topology_mirror(Operator):
    bl_idname = "character_designer.topology_mirror"
    bl_label = "Topology Mirror"
    bl_description = (
        "Use the selected patch as the source and replace only its opposite-side "
        "patch; preserve weights, UVs, materials, Shape Keys and the boundary interface"
    )
    bl_options = {"REGISTER", "UNDO"}

    boundary_tolerance: FloatProperty(
        name="Boundary Tolerance",
        description=(
            "Maximum distance for matching the selected wrist Loop to its opposite; "
            "zero uses a boundary-edge-based value"
        ),
        default=0.0,
        min=0.0,
        soft_max=0.01,
        precision=6,
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        return (
            context.view_layer is not None
            and context.view_layer.objects.active is not None
            and context.view_layer.objects.active.type == "MESH"
            and context.mode == "EDIT_MESH"
        )

    def execute(self, context):
        try:
            plan = build_topology_mirror_plan(
                context,
                boundary_tolerance=self.boundary_tolerance,
            )
            bpy.ops.object.mode_set(mode="OBJECT")
            result = apply_topology_mirror_plan(plan)
            _select_result_region(plan.mesh_obj, result.selected_vertices)
        except TopologySymmetryError as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        except ws.WeightSymmetryRollbackError as error:
            traceback.print_exc()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception as error:
            traceback.print_exc()
            self.report({"ERROR"}, f"Topology Mirror failed safely: {error}")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"{result.source_name} -> {result.target_name}: replaced "
            f"{result.removed_faces} faces with {result.added_faces}; "
            f"weights and Shape Keys mirrored in one Undo.",
        )
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_topology_mirror_repair(Operator):
    bl_idname = "character_designer.topology_mirror_repair"
    bl_label = "Topology Mirror · Repair Selection"
    bl_description = (
        "Mirror the selected source faces into a locally missing opposite-side patch, "
        "weld nearby opposite vertices, and preserve weights, UVs and Shape Keys"
    )
    bl_options = {"REGISTER", "UNDO"}

    merge_distance: FloatProperty(
        name="Merge Distance",
        description=(
            "Distance used to weld mirrored source vertices to existing opposite vertices; "
            "zero uses a conservative boundary-based value"
        ),
        default=0.0,
        min=0.0,
        soft_max=0.01,
        precision=6,
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        return (
            context.view_layer is not None
            and context.view_layer.objects.active is not None
            and context.view_layer.objects.active.type == "MESH"
            and context.mode == "EDIT_MESH"
        )

    def execute(self, context):
        try:
            plan = build_topology_repair_plan(
                context,
                merge_distance=self.merge_distance,
            )
            bpy.ops.object.mode_set(mode="OBJECT")
            result = apply_topology_repair_plan(plan)
            _select_result_region(plan.mesh_obj, result.selected_vertices)
        except TopologySymmetryError as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        except ws.WeightSymmetryRollbackError as error:
            traceback.print_exc()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception as error:
            traceback.print_exc()
            self.report({"ERROR"}, f"Topology repair failed safely: {error}")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"{result.source_name} -> {result.target_name}: repaired "
            f"{result.added_faces} face(s), welded {result.welded_vertices} vertex/vertices, "
            f"added {result.added_vertices}; removed {result.removed_faces} old target face(s).",
        )
        return {"FINISHED"}


TOPOLOGY_SYMMETRY_CLASSES = (
    CHARACTERDESIGNER_OT_topology_mirror,
    CHARACTERDESIGNER_OT_topology_mirror_repair,
)
