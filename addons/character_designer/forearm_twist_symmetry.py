"""Read-only, strict correspondence for opposite forearm calibration loops.

Only loop indices are returned. The caller keeps each side's own deformation
weights, captured support, rest frames, key, and live pose.
"""

from __future__ import annotations

import math

from mathutils import Vector
from mathutils.kdtree import KDTree


def _reflect(point):
    return Vector((-point.x, point.y, point.z))


def _validated_rings(record, vertex_count, edge_lookup):
    rings = record.get("rings")
    if not isinstance(rings, (list, tuple)) or len(rings) < 3:
        raise ValueError("Symmetry needs at least three captured loops on each arm.")
    result = []
    used = set()
    for ring in rings:
        if not isinstance(ring, dict):
            raise ValueError("Captured loop data is invalid; capture the arm again.")
        vertices = ring.get("vertices")
        if not isinstance(vertices, (list, tuple)) or len(vertices) < 4:
            raise ValueError("Each captured loop must be a closed vertex cycle.")
        if any(type(index) is not int or index < 0 or index >= vertex_count for index in vertices):
            raise ValueError("Captured vertex indices are stale; capture the arm again.")
        if len(set(vertices)) != len(vertices) or used.intersection(vertices):
            raise ValueError("Captured loops overlap or repeat vertices.")
        try:
            position = float(ring["position"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Captured loop positions are invalid.") from None
        if not math.isfinite(position):
            raise ValueError("Captured loop positions must be finite.")
        for offset, index in enumerate(vertices):
            edge = tuple(sorted((index, vertices[(offset + 1) % len(vertices)])))
            if edge not in edge_lookup:
                raise ValueError("Captured loop connectivity changed; capture the arm again.")
        used.update(vertices)
        result.append((tuple(vertices), position))
    return result


def mirror_ring_pairs(obj, armature, source_record, target_record):
    """Return ``[(source_loop_index, target_loop_index), ...]`` without writes.

    Reflection is across Mesh Basis local X=0. Every source ring vertex must
    have one reciprocal match in the full Basis, and its reflected cycle must
    be exactly one target ring. Local topology and anatomical rest endpoints
    must agree; ring order and vertex index offsets are never assumed.
    """
    if obj is None or obj.type != "MESH" or armature is None or armature.type != "ARMATURE":
        raise ValueError("Select a bound character Mesh to mirror its calibration.")
    if not isinstance(source_record, dict) or not isinstance(target_record, dict):
        raise ValueError("Capture both forearm regions before matching symmetry.")
    mesh = obj.data
    basis = mesh.shape_keys.reference_key if mesh.shape_keys is not None else None
    coordinates = [point.co.copy() for point in (basis.data if basis is not None else mesh.vertices)]
    if not coordinates or any(not all(math.isfinite(value) for value in point) for point in coordinates):
        raise ValueError("The Mesh Basis contains empty or invalid coordinates.")
    extent = Vector(tuple(max(point[axis] for point in coordinates) - min(point[axis] for point in coordinates)
                          for axis in range(3)))
    tolerance = max(1.0e-7, extent.length * 1.0e-6)
    try:
        to_mesh = obj.matrix_world.inverted() @ armature.matrix_world
        to_armature = to_mesh.inverted()
    except ValueError:
        raise ValueError("Mesh and Armature transforms must be invertible for symmetry.") from None
    if not all(math.isfinite(value) for matrix in (to_mesh, to_armature) for row in matrix for value in row):
        raise ValueError("Mesh and Armature transforms must be finite for symmetry.")
    source_chain = source_record.get("chain")
    target_chain = target_record.get("chain")
    if any(not isinstance(chain, (list, tuple)) or len(chain) != 3 for chain in (source_chain, target_chain)):
        raise ValueError("Symmetry needs matching upper-arm, forearm and hand chains.")
    if any(any(not isinstance(name, str) for name in chain) or len(set(chain)) != 3
           for chain in (source_chain, target_chain)):
        raise ValueError("Each captured arm needs three distinct bone names.")
    for source_name, target_name in zip(source_chain, target_chain):
        if not isinstance(source_name, str) or not isinstance(target_name, str):
            raise ValueError("Captured bone names are invalid.")
        source_bone = armature.data.bones.get(source_name)
        target_bone = armature.data.bones.get(target_name)
        if source_bone is None or target_bone is None or source_name == target_name:
            raise ValueError("The opposite arm needs its own matching deform bone chain.")
        if not source_bone.use_deform or not target_bone.use_deform:
            raise ValueError("Both captured arm chains must use deform bones.")
        for attribute in ("head_local", "tail_local"):
            source_point = to_mesh @ getattr(source_bone, attribute)
            target_point = to_mesh @ getattr(target_bone, attribute)
            if (_reflect(source_point) - target_point).length > tolerance * 4.0:
                raise ValueError("Arm Rest endpoints are not mirrored across Mesh local X=0.")
    lower_bones = [armature.data.bones[chain[1]] for chain in (source_chain, target_chain)]
    centers = [to_mesh @ ((bone.head_local + bone.tail_local) * 0.5) for bone in lower_bones]
    if centers[0].x * centers[1].x >= -(tolerance * tolerance):
        raise ValueError("Captured forearms must lie on opposite sides of Mesh local X=0.")
    lengths = [(to_mesh.to_3x3() @ (bone.tail_local - bone.head_local)).length for bone in lower_bones]
    if min(lengths) <= tolerance * 10.0:
        raise ValueError("Forearm Rest length is too small to establish symmetry.")
    position_tolerance = max(1.0e-5, tolerance * 4.0 / min(lengths))

    edge_lookup = {tuple(sorted(edge.vertices)): edge.index for edge in mesh.edges}
    edge_faces = [[] for _ in mesh.edges]
    valences = [0 for _ in mesh.vertices]
    for first, second in edge_lookup:
        valences[first] += 1
        valences[second] += 1
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            edge_index = edge_lookup.get(tuple(sorted(key)))
            if edge_index is not None:
                edge_faces[edge_index].append(len(polygon.vertices))
    source_rings = _validated_rings(source_record, len(coordinates), edge_lookup)
    target_rings = _validated_rings(target_record, len(coordinates), edge_lookup)
    if len(source_rings) != len(target_rings):
        raise ValueError("The two forearms do not contain the same number of matching loops.")
    for rings, bone in zip((source_rings, target_rings), lower_bones):
        axis = bone.tail_local - bone.head_local
        length = axis.length
        if length <= 1.0e-8:
            raise ValueError("Forearm Rest length is zero.")
        axis /= length
        for vertices, recorded_position in rings:
            measured = sum((to_armature @ coordinates[index] - bone.head_local).dot(axis) / length
                           for index in vertices) / len(vertices)
            if abs(measured - recorded_position) > position_tolerance:
                raise ValueError("Captured loop positions are stale; capture the arm again.")

    tree = KDTree(len(coordinates))
    for index, point in enumerate(coordinates):
        tree.insert(point, index)
    tree.balance()
    matches = {}
    used_targets = set()
    for vertices, _position in source_rings:
        for source_index in vertices:
            candidates = tree.find_range(_reflect(coordinates[source_index]), tolerance)
            if len(candidates) != 1:
                raise ValueError("Mirrored Basis vertices are missing or ambiguous; symmetry was not applied.")
            target_index = candidates[0][1]
            reverse = tree.find_range(_reflect(coordinates[target_index]), tolerance)
            if len(reverse) != 1 or reverse[0][1] != source_index or target_index in used_targets:
                raise ValueError("Mirrored Basis vertices do not form a unique reciprocal mapping.")
            matches[source_index] = target_index
            used_targets.add(target_index)
            if valences[source_index] != valences[target_index]:
                raise ValueError("The mirrored forearm loops have different local topology.")

    targets_by_vertices = {frozenset(vertices): index for index, (vertices, _position) in enumerate(target_rings)}
    pairs = []
    paired_targets = set()
    for source_index, (vertices, source_position) in enumerate(source_rings):
        mapped = tuple(matches[index] for index in vertices)
        target_index = targets_by_vertices.get(frozenset(mapped))
        if target_index is None or target_index in paired_targets:
            raise ValueError("A mirrored source loop does not match one complete opposite loop.")
        if abs(source_position - target_rings[target_index][1]) > position_tolerance:
            raise ValueError("Matching loops occupy different positions along the forearms.")
        for offset, index in enumerate(vertices):
            following = vertices[(offset + 1) % len(vertices)]
            original_edge = tuple(sorted((index, following)))
            mirrored_edge = tuple(sorted((matches[index], matches[following])))
            if mirrored_edge not in edge_lookup:
                raise ValueError("The mirrored loop edge cycle is different on the opposite arm.")
            if sorted(edge_faces[edge_lookup[original_edge]]) != sorted(edge_faces[edge_lookup[mirrored_edge]]):
                raise ValueError("The mirrored loop surface topology differs between arms.")
        paired_targets.add(target_index)
        pairs.append((source_index, target_index))
    return pairs
