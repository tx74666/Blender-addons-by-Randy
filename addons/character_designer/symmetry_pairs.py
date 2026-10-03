"""Read-only, conservative vertex correspondence from mesh topology.

Fixed near-plane seam edges establish unique reflected face cycles, grown
through manifold topology.  Conflicting walks stop locally.  Disconnected
parts can also qualify through collision-free neighborhood refinement, like
the unique-valence classes used by Blender's Topology Mirror.  Both paths
verify reciprocal edge and cyclic face correspondence before exposing a
pair.  Coordinates qualify the seam and choose X sides; they never select
between counterpart vertices or provide a nearest fallback.

No Blender data, selection, cache, or operator is read or changed here.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
import math


MAX_REFINEMENT_ITERATIONS = 256


class SymmetryPairError(ValueError):
    """Malformed input prevents a trustworthy topology correspondence."""


@dataclass(frozen=True)
class VertexPairMap:
    """One-to-one X pairs; every other vertex is explicitly unmatched.

    ``pairs`` are ordered (negative X, positive X).  ``centerline`` contains
    topologically unique self-pairs within the requested center tolerance.
    ``ambiguous`` is a subset of ``unmatched`` whose topology class had more
    than two members.  Counts are vertex counts unless a field says pairs.
    """

    pairs: tuple
    centerline: tuple
    unmatched: tuple
    ambiguous: tuple
    iterations: int
    converged: bool
    diagnostics: tuple = ()

    @property
    def matched_vertices(self):
        return len(self.pairs) * 2 + len(self.centerline)


def _intern(signatures):
    """Return equality-preserving integer colors without hash collisions.

    Python dictionaries check full tuple equality after hashing.  Vertex
    index only determines the integer spelling of a color, never membership.
    """

    unique = {}
    colors = []
    for signature in signatures:
        colors.append(unique.setdefault(signature, len(unique)))
    return colors, len(unique)


def _ring_key(values):
    """Canonical cycle, allowing reversed winding under reflection."""

    values = tuple(values)
    if not values:
        return ()
    minimum = min(values)
    candidates = []
    for sequence in (values, tuple(reversed(values))):
        for index, value in enumerate(sequence):
            if value == minimum:
                candidates.append(sequence[index:] + sequence[:index])
    return min(candidates)


def _around_vertex_key(face, vertex, token):
    """Face ring anchored at a vertex, respecting cyclic incidence."""

    offset = face.index(vertex)
    ring = face[offset + 1:] + face[:offset]
    values = tuple(token(index) for index in ring)
    return min(values, tuple(reversed(values)))


def _input_topology(coordinates, edges, faces):
    positions = []
    for index, coordinate in enumerate(coordinates):
        point = tuple(float(value) for value in coordinate)
        if len(point) != 3 or not all(math.isfinite(value) for value in point):
            raise SymmetryPairError(f"Vertex {index} has no finite 3D coordinate.")
        positions.append(point)
    count = len(positions)
    neighbors = [set() for _ in range(count)]
    edge_set = set()
    for edge in edges:
        values = tuple(edge)
        if len(values) != 2:
            raise SymmetryPairError("An edge has no two vertex indices.")
        a, b = values
        if (not isinstance(a, int) or not isinstance(b, int)
                or not 0 <= a < count or not 0 <= b < count or a == b):
            raise SymmetryPairError("An edge has invalid vertex indices.")
        key = (min(a, b), max(a, b))
        if key in edge_set:
            raise SymmetryPairError("Duplicate edges prevent topology certification.")
        edge_set.add(key)
        neighbors[a].add(b)
        neighbors[b].add(a)
    polygons = []
    incident = [[] for _ in range(count)]
    face_keys = set()
    for values in faces:
        face = tuple(values)
        if (len(face) < 3 or len(set(face)) != len(face)
                or any(not isinstance(index, int) or not 0 <= index < count for index in face)):
            raise SymmetryPairError("A face has invalid or repeated vertex indices.")
        for a, b in zip(face, face[1:] + face[:1]):
            if (min(a, b), max(a, b)) not in edge_set:
                raise SymmetryPairError("A face boundary is missing an edge.")
        key = _ring_key(face)
        if key in face_keys:
            raise SymmetryPairError("Duplicate faces prevent topology certification.")
        face_keys.add(key)
        face_index = len(polygons)
        polygons.append(face)
        for index in face:
            incident[index].append(face_index)
    return positions, neighbors, edge_set, polygons, incident, face_keys


def _refine_colors(neighbors, polygons, incident):
    initial = [
        (len(links), tuple(sorted(len(polygons[face]) for face in incident[index])))
        for index, links in enumerate(neighbors)
    ]
    colors, color_count = _intern(initial)
    for iteration in range(1, MAX_REFINEMENT_ITERATIONS + 1):
        # Full signatures distinguish connectivity without any float/geometry
        # value.  Keeping the previous color makes refinement monotonic.
        signatures = (
            (colors[index], tuple(sorted(colors[other] for other in links)))
            for index, links in enumerate(neighbors)
        )
        refined, refined_count = _intern(signatures)
        colors = refined
        if refined_count == color_count:
            return colors, iteration, True
        color_count = refined_count
    return colors, MAX_REFINEMENT_ITERATIONS, False


def _certify_mapping(mapping, colors, neighbors, polygons, incident, face_keys):
    """Reject incompatible candidates; preserve unresolved class information.

    Known counterparts must preserve every known edge.  Unresolved neighbors
    must have exactly the same color multiplicities.  Incident polygons must
    have compatible cyclic order anchored at the corresponding vertex.  An
    unresolved vertex is a color token, never arbitrarily assigned an index.
    Removing a candidate changes known tokens, so certification repeats until
    all surviving candidates pass against the same reciprocal mapping.
    """

    rejected = set()
    while mapping:
        invalid = set()
        for source, target in mapping.items():
            if mapping.get(target) != source:
                invalid.update((source, target))
                continue
            known_source = {mapping[index] for index in neighbors[source] if index in mapping}
            known_target = {index for index in neighbors[target] if index in mapping}
            if known_source != known_target:
                invalid.update((source, target))
                continue
            unknown_source = Counter(colors[index] for index in neighbors[source] if index not in mapping)
            unknown_target = Counter(colors[index] for index in neighbors[target] if index not in mapping)
            if unknown_source != unknown_target:
                invalid.update((source, target))
                continue
            if incident[source]:
                source_token = lambda index: (1, mapping[index]) if index in mapping else (0, colors[index])
                target_token = lambda index: (1, index) if index in mapping else (0, colors[index])
                source_faces = Counter(
                    _around_vertex_key(polygons[face], source, source_token)
                    for face in incident[source]
                )
                target_faces = Counter(
                    _around_vertex_key(polygons[face], target, target_token)
                    for face in incident[target]
                )
                if source_faces != target_faces:
                    invalid.update((source, target))
        # Fully resolved faces have an exact cyclic counterpart; the anchored
        # checks above also constrain faces that touch unresolved vertices.
        for face in polygons:
            if all(index in mapping for index in face):
                if _ring_key(tuple(mapping[index] for index in face)) not in face_keys:
                    invalid.update(face)
                    invalid.update(mapping[index] for index in face)
        if not invalid:
            break
        rejected.update(invalid)
        for index in invalid:
            mapping.pop(index, None)
    return mapping, rejected


def _face_outer_path(face, first, second):
    """The unique long route between the endpoints of a polygon edge."""

    if first not in face or second not in face:
        return None
    start = face.index(first)
    for direction in (1, -1):
        path = [first]
        position = start
        for _ in range(len(face)):
            position = (position + direction) % len(face)
            path.append(face[position])
            if face[position] == second:
                break
        if len(path) == len(face):
            return tuple(path)
    return None


def _seam_face_mapping(positions, neighbors, polygons, incident, face_keys, tolerance):
    """Grow unique face-cycle correspondence from fixed center seam edges.

    Geometry only certifies that a seed edge is on the plane and its two
    incident faces occupy opposite sides.  The endpoints then fix one unique
    reflected cycle.  Every subsequent counterpart follows manifold face
    incidence and already established edge endpoints, without a distance
    search.  Topology damage stops a local branch instead of recoloring the
    whole connected surface.
    """

    edge_faces = defaultdict(list)
    for face_index, face in enumerate(polygons):
        for first, second in zip(face, face[1:] + face[:1]):
            edge_faces[(min(first, second), max(first, second))].append(face_index)
    near_plane = {index for index, point in enumerate(positions) if abs(point[0]) <= tolerance}
    seeds = []
    for edge, linked in edge_faces.items():
        if len(linked) != 2 or not set(edge).issubset(near_plane):
            continue
        first_face, second_face = linked
        if len(polygons[first_face]) != len(polygons[second_face]):
            continue
        sides = []
        for face_index in linked:
            outside = [positions[index][0] for index in polygons[face_index] if index not in near_plane]
            if outside and all(value < -tolerance for value in outside):
                sides.append(-1)
            elif outside and all(value > tolerance for value in outside):
                sides.append(1)
            else:
                sides.append(0)
        if sides[0] * sides[1] == -1:
            if sides[0] > 0:
                first_face, second_face = second_face, first_face
            seeds.append((first_face, second_face, edge))
    if not seeds:
        return {}, set(), 0, 0
    mapping = {}
    face_mapping = {}
    blocked = set()

    def block(indices):
        affected = set(indices)
        affected.update(mapping[index] for index in tuple(affected) if index in mapping)
        blocked.update(affected)
        for index in affected:
            mapping.pop(index, None)

    def propose_face_pair(first_face, second_face, first_edge, second_edge, endpoints):
        face_a, face_b = polygons[first_face], polygons[second_face]
        a, b = first_edge
        reflected_a, reflected_b = endpoints
        if set((reflected_a, reflected_b)) != set(second_edge):
            return
        path_a = _face_outer_path(face_a, a, b)
        path_b = _face_outer_path(face_b, reflected_a, reflected_b)
        if path_a is None or path_b is None or len(path_a) != len(path_b):
            return
        proposed = {}
        for source, target in zip(path_a, path_b):
            if source in blocked or target in blocked:
                return
            if source == target:
                if source not in near_plane:
                    return
            elif not ((positions[source][0] < -tolerance and positions[target][0] > tolerance)
                      or (positions[target][0] < -tolerance and positions[source][0] > tolerance)):
                # A cycle that folds to the same X side is not an X reflection.
                return
            if proposed.get(source, target) != target or proposed.get(target, source) != source:
                return None
            proposed[source], proposed[target] = target, source
        return first_face, second_face, proposed

    pending = []
    for first_face, second_face, edge in seeds:
        proposal = propose_face_pair(first_face, second_face, edge, edge, edge)
        if proposal is not None:
            pending.append(proposal)
    visited = set()
    while pending:
        # Compare an entire graph-distance wave before accepting anything.
        # First vertex index, face storage order, and traversal direction must
        # never decide which conflicting correspondence is retained.
        candidates = {}
        vertex_claims = defaultdict(set)
        face_claims = defaultdict(set)
        for first_face, second_face, proposed in pending:
            signature = (min(first_face, second_face), max(first_face, second_face), tuple(sorted(proposed.items())))
            if signature in visited or any(index in blocked for index in proposed):
                continue
            visited.add(signature)
            candidates[signature] = (first_face, second_face, proposed)
            face_claims[first_face].add(second_face)
            face_claims[second_face].add(first_face)
            for index, target in proposed.items():
                vertex_claims[index].add(target)
        for index in vertex_claims:
            if index in mapping:
                vertex_claims[index].add(mapping[index])
        for index in face_claims:
            if index in face_mapping:
                face_claims[index].add(face_mapping[index])
        conflicting_vertices = {index for index, claims in vertex_claims.items() if len(claims) > 1}
        conflicting_faces = {index for index, claims in face_claims.items() if len(claims) > 1}
        involved = set(conflicting_vertices)
        for index in conflicting_vertices:
            involved.update(vertex_claims[index])
        for first_face, second_face, proposed in candidates.values():
            if (first_face in conflicting_faces or second_face in conflicting_faces
                    or conflicting_vertices.intersection(proposed)):
                involved.update(proposed)
                involved.update(proposed.values())
        for face_index in conflicting_faces:
            involved.update(polygons[face_index])
            for other in face_claims[face_index]:
                involved.update(polygons[other])
        block(involved)
        accepted = []
        for first_face, second_face, proposed in candidates.values():
            if any(index in blocked for index in proposed):
                continue
            if face_mapping.get(first_face) == second_face and face_mapping.get(second_face) == first_face:
                continue
            mapping.update(proposed)
            face_mapping[first_face], face_mapping[second_face] = second_face, first_face
            accepted.append((first_face, second_face))
        pending = []
        for first_face, second_face in accepted:
            face_a, face_b = polygons[first_face], polygons[second_face]
            if any(index not in mapping for index in face_a + face_b):
                continue
            for a, b in zip(face_a, face_a[1:] + face_a[:1]):
                first_edge = (min(a, b), max(a, b))
                reflected_a, reflected_b = mapping[first_edge[0]], mapping[first_edge[1]]
                second_edge = (min(reflected_a, reflected_b), max(reflected_a, reflected_b))
                linked_a, linked_b = edge_faces[first_edge], edge_faces.get(second_edge, ())
                if (len(linked_a) > 2 or len(linked_b) > 2
                        or first_face not in linked_a or second_face not in linked_b):
                    continue
                next_a = [index for index in linked_a if index != first_face]
                next_b = [index for index in linked_b if index != second_face]
                if len(next_a) != 1 or len(next_b) != 1:
                    # A missing or mismatched boundary remains a local endpoint.
                    continue
                if next_a[0] == second_face and next_b[0] == first_face:
                    continue
                proposal = propose_face_pair(next_a[0], next_b[0], first_edge, second_edge, (reflected_a, reflected_b))
                if proposal is not None:
                    pending.append(proposal)
    # Local structure guards expose damaged boundary vertices as unmatched.
    # Unresolved neighbor identities are not recursively compared here: a
    # single damaged patch must not invalidate the opposite entire surface.
    invalid = set()
    for source, target in mapping.items():
        if (len(neighbors[source]) != len(neighbors[target])
                or sorted(len(polygons[face]) for face in incident[source])
                != sorted(len(polygons[face]) for face in incident[target])):
            invalid.update((source, target))
            continue
        for other in neighbors[source]:
            if other in mapping and mapping[other] not in neighbors[target]:
                invalid.update((source, target, other, mapping[other]))
    for face in polygons:
        if all(index in mapping for index in face):
            if _ring_key(tuple(mapping[index] for index in face)) not in face_keys:
                invalid.update(face)
                invalid.update(mapping[index] for index in face)
    block(invalid)
    return mapping, blocked, len(seeds), len(face_mapping)


def build_vertex_pairs(coordinates, edges, faces=(), *, centerline_tolerance=1.0e-6):
    """Build unique topology-based mirror candidates without scene mutation.

    Unknown/ambiguous vertices stay unmatched even at perfectly mirrored
    coordinates.  Isolated vertices are never qualified.  A refinement limit
    fails closed for that path; certified seam correspondences remain usable.
    """

    tolerance = float(centerline_tolerance)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise SymmetryPairError("Centerline tolerance must be finite and non-negative.")
    positions, neighbors, edge_set, polygons, incident, face_keys = _input_topology(
        coordinates, edges, faces
    )
    count = len(positions)
    if count == 0:
        return VertexPairMap((), (), (), (), 0, True)
    colors, iterations, converged = _refine_colors(neighbors, polygons, incident)
    classes = defaultdict(list)
    for index, color in enumerate(colors):
        classes[color].append(index)
    ambiguous_candidates = {index for group in classes.values() if len(group) > 2 for index in group}
    mapping = {}
    for group in classes.values() if converged else ():
        if len(group) == 1:
            index = group[0]
            if neighbors[index]:
                # A unique self-vertex remains an internal topology witness
                # even if off plane; only near-plane vertices are exposed.
                mapping[index] = index
        elif len(group) == 2:
            a, b = group
            if neighbors[a] and neighbors[b]:
                mapping[a], mapping[b] = b, a
    mapping, rejected = _certify_mapping(
        mapping, colors, neighbors, polygons, incident, face_keys
    )
    surface_mapping, surface_rejected, seam_count, face_count = _seam_face_mapping(
        positions, neighbors, polygons, incident, face_keys, tolerance
    )
    # Only qualified global candidates participate in the union.  In
    # particular an off-plane singleton from an asymmetric graph is not a
    # competing self-pair against a certified seam-grown counterpart.
    for source, target in tuple(mapping.items()):
        first_x, second_x = positions[source][0], positions[target][0]
        if not ((source == target and abs(first_x) <= tolerance)
                or (first_x < -tolerance and second_x > tolerance)
                or (second_x < -tolerance and first_x > tolerance)):
            mapping.pop(source, None)
    conflicts = set()
    for source, target in surface_mapping.items():
        if source in mapping and mapping[source] != target:
            conflicts.update((source, target, mapping[source]))
        if target in mapping and mapping[target] != source:
            conflicts.update((source, target, mapping[target]))
    mapping.update(surface_mapping)
    rejected.update(surface_rejected)
    rejected.update(conflicts)
    for index in tuple(rejected):
        if index in mapping:
            rejected.add(mapping[index])
    for index in rejected:
        mapping.pop(index, None)
    pairs = []
    centerline = []
    qualified = set()
    wrong_side = 0
    for source in sorted(mapping):
        target = mapping[source]
        if source > target:
            continue
        source_x, target_x = positions[source][0], positions[target][0]
        if source == target:
            if abs(source_x) <= tolerance:
                centerline.append(source)
                qualified.add(source)
            else:
                wrong_side += 1
        elif source_x < -tolerance and target_x > tolerance:
            pairs.append((source, target))
            qualified.update((source, target))
        elif target_x < -tolerance and source_x > tolerance:
            pairs.append((target, source))
            qualified.update((source, target))
        else:
            wrong_side += 2
    diagnostics = []
    if seam_count:
        diagnostics.append(f"Centerline topology walk used {seam_count} seam edges and {face_count} face correspondences.")
    if not converged:
        diagnostics.append("Global topology refinement reached its safety limit; only certified seam-grown pairs were accepted.")
    ambiguous = tuple(sorted(ambiguous_candidates.difference(qualified)))
    if ambiguous:
        diagnostics.append(f"{len(ambiguous)} vertices have non-unique topology and remain unmatched.")
    if rejected:
        diagnostics.append(f"{len(rejected)} vertices failed edge/face correspondence certification.")
    if wrong_side:
        diagnostics.append(f"{wrong_side} topology candidates do not form opposite X sides or a near-plane centerline.")
    isolated = sum(not links for links in neighbors)
    if isolated:
        diagnostics.append(f"{isolated} isolated vertices remain unmatched.")
    return VertexPairMap(
        tuple(sorted(pairs)), tuple(centerline),
        tuple(index for index in range(count) if index not in qualified),
        ambiguous, iterations, converged, tuple(diagnostics),
    )
