"""Read-only discovery and tapered cage fitting for regular open skirt meshes.

The input is the complete, unmodified mesh: a single quad annulus with equal
vertex counts around its two open boundaries.  Coordinates in the returned
JSON-compatible plan are in source-object space.  World Z identifies the waist;
the caller must convert positions through ``obj.matrix_world`` when making a rig.
"""

import hashlib
import math
from collections import defaultdict, deque

import bmesh
from mathutils import Vector


class SkirtTopologyError(ValueError):
    """The source cannot safely be interpreted as one regular skirt."""


def _point(vector):
    return [float(value) for value in vector]


def _mean(points):
    return Vector(tuple(math.fsum(point[axis] for point in points) / len(points)
                        for axis in range(3)))


def _edge(a, b):
    return (a, b) if a < b else (b, a)


def _snapshot(obj):
    if obj is None or obj.type != "MESH":
        raise SkirtTopologyError("Select one skirt mesh.")
    if obj.mode == "EDIT":
        # Index updates only affect this owned copy; selection and edit history
        # of the artist's mesh must remain untouched.
        bm = bmesh.from_edit_mesh(obj.data).copy()
        try:
            bm.verts.index_update()
            vertices = [vertex.co.copy() for vertex in bm.verts]
            edges = [_edge(*(v.index for v in edge.verts)) for edge in bm.edges]
            faces = [tuple(v.index for v in face.verts) for face in bm.faces]
        finally:
            bm.free()
    else:
        vertices = [vertex.co.copy() for vertex in obj.data.vertices]
        edges = [_edge(*edge.vertices) for edge in obj.data.edges]
        faces = [tuple(face.vertices) for face in obj.data.polygons]
    return vertices, edges, faces


def _components(adjacency):
    remaining = set(adjacency)
    components = []
    while remaining:
        seed = min(remaining)
        reached = {seed}
        queue = deque([seed])
        while queue:
            for neighbor in adjacency[queue.popleft()] - reached:
                reached.add(neighbor)
                queue.append(neighbor)
        components.append(reached)
        remaining.difference_update(reached)
    return components


def _cycle(component, adjacency):
    start = min(component)
    ordered = [start]
    previous, current = start, min(adjacency[start])
    while current != start:
        if current in ordered:
            raise SkirtTopologyError("A skirt boundary is not one closed loop.")
        ordered.append(current)
        following = adjacency[current] - {previous}
        if len(following) != 1:
            raise SkirtTopologyError("A skirt boundary branches or has a slit.")
        previous, current = current, next(iter(following))
    if len(ordered) != len(component):
        raise SkirtTopologyError("A skirt boundary contains disconnected sections.")
    return ordered


def _xy_area(ring, world):
    return 0.5 * sum(world[a].x * world[b].y - world[b].x * world[a].y
                     for a, b in zip(ring, ring[1:] + ring[:1]))


def _fit_ring(points):
    """First harmonic gives an ellipse while preserving topological columns."""
    center = _mean(points)
    count = len(points)
    cosine = Vector()
    sine = Vector()
    for column, point in enumerate(points):
        angle = math.tau * column / count
        offset = point - center
        cosine += offset * math.cos(angle) * (2.0 / count)
        sine += offset * math.sin(angle) * (2.0 / count)
    # A level cage is preferable to propagating little hem undulations into
    # controls and bone rolls.  The source itself is never moved.
    cosine.z = sine.z = 0.0
    return center, cosine, sine


def _smoothstep(value):
    return value * value * (3.0 - 2.0 * value)


def _longitudinal_weights(t, segment_count):
    centers = [0.0] + [(index + 0.5) / segment_count for index in range(segment_count)]
    if t >= centers[-1]:
        return [(segment_count - 1, 1.0)]
    for index in range(len(centers) - 1):
        low, high = centers[index:index + 2]
        if t <= high:
            fraction = _smoothstep(max(0.0, min(1.0, (t - low) / (high - low))))
            return [(index - 1, 1.0 - fraction), (index, fraction)]
    raise AssertionError("Unreachable skirt weight interval")


def sample_fit(plan, t, phase):
    """Sample the fitted cage in source-local space (phase is in radians)."""
    root, hem = plan["fit_waist"], plan["fit_hem"]
    center = Vector(root["center"]).lerp(Vector(hem["center"]), t)
    cosine = Vector(root["cosine"]).lerp(Vector(hem["cosine"]), t)
    sine = Vector(root["sine"]).lerp(Vector(hem["sine"]), t)
    return _point(center + cosine * math.cos(phase) + sine * math.sin(phase))


def analyze_skirt(obj, chain_count=8, segment_count=4):
    """Return a deterministic rig plan without modifying any Blender data.

    ``rings`` contains corresponding vertex columns, ordered waist to hem;
    ``chains`` contains ``segment_count + 1`` cage points per chain.
    ``vertex_weights[vertex_index]`` holds ``[chain, segment, weight]`` entries.
    Segment ``-1`` is the shared waist anchor (its chain index is always zero).
    At most four nonzero influences occur per vertex and each list sums to one.
    The first ring is rigidly anchored.  ``signature`` describes topology only.
    """
    if (isinstance(chain_count, bool) or not isinstance(chain_count, int)
            or not 3 <= chain_count <= 64):
        raise SkirtTopologyError("Use between 3 and 64 skirt chains.")
    if (isinstance(segment_count, bool) or not isinstance(segment_count, int)
            or not 1 <= segment_count <= 32):
        raise SkirtTopologyError("Use between 1 and 32 bones per skirt chain.")
    vertices, edges, faces = _snapshot(obj)
    if not vertices or not faces or any(len(face) != 4 for face in faces):
        raise SkirtTopologyError("The skirt must be one open tube made entirely of quads.")
    if any(not math.isfinite(value) for point in vertices for value in point):
        raise SkirtTopologyError("The skirt contains non-finite vertex coordinates.")
    matrix = obj.matrix_world.copy()
    if any(not math.isfinite(value) for row in matrix for value in row):
        raise SkirtTopologyError("The skirt object has an invalid transform.")
    lengths = [matrix.to_3x3().col[index].length for index in range(3)]
    determinant = abs(matrix.to_3x3().determinant())
    if min(lengths) <= 1.0e-12 or determinant / math.prod(lengths) <= 1.0e-8:
        raise SkirtTopologyError("The skirt transform is singular; restore a nonzero object scale.")
    inverse = matrix.inverted()
    world = [matrix @ point for point in vertices]
    adjacency = {index: set() for index in range(len(vertices))}
    face_edges = defaultdict(list)
    for face_index, face in enumerate(faces):
        if len(set(face)) != 4:
            raise SkirtTopologyError("The skirt contains a degenerate quad.")
        for a, b in zip(face, face[1:] + face[:1]):
            face_edges[_edge(a, b)].append(face_index)
    if len(edges) != len(set(edges)) or set(edges) != set(face_edges):
        raise SkirtTopologyError("Remove loose or duplicate edges from the skirt.")
    if any(len(incident) not in {1, 2} for incident in face_edges.values()):
        raise SkirtTopologyError("The skirt contains nonmanifold edges.")
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    if len(_components(adjacency)) != 1:
        raise SkirtTopologyError("The skirt must be a single connected mesh.")
    boundary = defaultdict(set)
    for (a, b), incident in face_edges.items():
        if len(incident) == 1:
            boundary[a].add(b)
            boundary[b].add(a)
    if not boundary or any(len(neighbors) != 2 for neighbors in boundary.values()):
        raise SkirtTopologyError("The skirt needs two closed openings: waist and hem.")
    openings = _components(boundary)
    if len(openings) != 2 or len(openings[0]) != len(openings[1]):
        raise SkirtTopologyError("Use equal-count waist and hem loops without slits or holes.")
    columns = len(openings[0])
    if columns < max(4, chain_count):
        raise SkirtTopologyError("The skirt needs at least as many perimeter vertices as chains.")
    openings.sort(key=lambda loop: (-_mean([world[index] for index in loop]).z, min(loop)))
    waist = _cycle(openings[0], boundary)
    waist_center = _mean([world[index] for index in waist])
    hem_center = _mean([world[index] for index in openings[1]])
    extent = max((point - waist_center).length for point in world)
    tolerance = max(extent * 1.0e-6, 1.0e-10)
    height = waist_center.z - hem_center.z
    if height <= tolerance * 10:
        raise SkirtTopologyError("Waist and hem are ambiguous; orient the skirt upright along world Z.")
    if _xy_area(waist, world) < 0:
        waist.reverse()
    seam = min(range(columns), key=lambda index: (
        abs(math.atan2((world[waist[index]] - waist_center).y,
                       (world[waist[index]] - waist_center).x)), waist[index]))
    waist = waist[seam:] + waist[:seam]
    rings = [waist]
    visited = set(waist)
    previous = set()
    while set(rings[-1]) != openings[1]:
        current = set(rings[-1])
        following = []
        for index in rings[-1]:
            if len(adjacency[index] & current) != 2:
                raise SkirtTopologyError("Every skirt row must form one regular closed ring.")
            outgoing = adjacency[index] - current - previous
            if len(outgoing) != 1:
                raise SkirtTopologyError("Skirt rows must connect through one quad rail per vertex.")
            following.append(next(iter(outgoing)))
        if len(set(following)) != columns or visited.intersection(following):
            raise SkirtTopologyError("Skirt rows merge, branch, or wrap back onto themselves.")
        rings.append(following)
        visited.update(following)
        previous = current
    if visited != set(adjacency):
        raise SkirtTopologyError("The skirt has geometry outside the regular waist-to-hem rings.")
    # Comparing the actual quad bands rejects diagonal rail connections, hidden
    # cross-links, caps, and invalid final-ring valence in one complete check.
    expected_faces = {frozenset((upper[i], upper[(i + 1) % columns],
                                  lower[(i + 1) % columns], lower[i]))
                      for upper, lower in zip(rings, rings[1:]) for i in range(columns)}
    if len(expected_faces) != len(faces) or expected_faces != {frozenset(face) for face in faces}:
        raise SkirtTopologyError("The skirt does not have consistent quad bands between its rings.")
    centers = [_mean([world[index] for index in ring]) for ring in rings]
    # Rolled hems may turn upward a little (the Elaina reference does).  Keep
    # their topological order and use rail distance for weight progression.
    # A large reversal is ambiguous and should be repaired/oriented by the user.
    lowest = centers[0].z
    rolled_hem = False
    for center in centers[1:]:
        if center.z - lowest > height * 0.1:
            raise SkirtTopologyError("Skirt rings must descend overall; a large upward fold is ambiguous.")
        rolled_hem |= center.z - lowest > tolerance
        lowest = min(lowest, center.z)
    if any(_xy_area(ring, world) <= tolerance * tolerance for ring in rings):
        raise SkirtTopologyError("A skirt ring collapses or reverses around world Z.")
    distances = [0.0]
    for upper, lower in zip(rings, rings[1:]):
        distance = math.fsum((world[a] - world[b]).length for a, b in zip(upper, lower)) / columns
        if distance <= tolerance:
            raise SkirtTopologyError("The skirt has a collapsed band between neighboring rings.")
        distances.append(distances[-1] + distance)
    ring_t = [distance / distances[-1] for distance in distances]
    ring_t[0], ring_t[-1] = 0.0, 1.0
    root_fit = _fit_ring([world[index] for index in rings[0]])
    hem_fit = _fit_ring([world[index] for index in rings[-1]])
    for _, cosine, sine in (root_fit, hem_fit):
        if cosine.x * sine.y - cosine.y * sine.x <= tolerance * tolerance:
            raise SkirtTopologyError("The skirt openings cannot be fitted to a stable ellipse.")
    # The determinant of linearly interpolated ellipse axes is quadratic.  Its
    # interior minimum catches heavily twisted columns that would pinch the
    # generated frustum to a line despite valid endpoint rings.
    def cross_xy(first, second):
        return first.x * second.y - first.y * second.x

    delta_cosine = hem_fit[1] - root_fit[1]
    delta_sine = hem_fit[2] - root_fit[2]
    quadratic = cross_xy(delta_cosine, delta_sine)
    linear = cross_xy(delta_cosine, root_fit[2]) + cross_xy(root_fit[1], delta_sine)
    constant = cross_xy(root_fit[1], root_fit[2])
    if quadratic > 0:
        critical = -linear / (2 * quadratic)
        if 0 < critical < 1 and quadratic * critical * critical + linear * critical + constant <= tolerance * tolerance:
            raise SkirtTopologyError("The skirt columns twist too far for a stable tapered cage.")

    def cage_point(t, phase):
        center = root_fit[0].lerp(hem_fit[0], t)
        cosine = root_fit[1].lerp(hem_fit[1], t)
        sine = root_fit[2].lerp(hem_fit[2], t)
        return _point(inverse @ (center + cosine * math.cos(phase) + sine * math.sin(phase)))

    chains = [[cage_point(segment / segment_count, math.tau * chain / chain_count)
               for segment in range(segment_count + 1)] for chain in range(chain_count)]
    fitted_rings = [[cage_point(t, math.tau * column / columns)
                     for column in range(columns)] for t in ring_t]
    weights = [None] * len(vertices)
    for ring, t in zip(rings, ring_t):
        along = _longitudinal_weights(t, segment_count)
        for column, index in enumerate(ring):
            phase = column * chain_count / columns
            chain = int(math.floor(phase))
            fraction = _smoothstep(phase - chain)
            around = [(chain % chain_count, 1.0 - fraction), ((chain + 1) % chain_count, fraction)]
            combined = defaultdict(float)
            for chain_index, angular in around:
                for segment, longitudinal in along:
                    key = (0, -1) if segment == -1 else (chain_index, segment)
                    combined[key] += angular * longitudinal
            active = sorted((key, value) for key, value in combined.items() if value > 1.0e-12)
            total = sum(value for _, value in active)
            weights[index] = [[key[0], key[1], value / total] for key, value in active]
    signature_data = (len(vertices), tuple(sorted(edges)), tuple(sorted(tuple(sorted(face)) for face in faces)))
    signature = hashlib.sha256(repr(signature_data).encode("ascii")).hexdigest()
    waist_radius = math.sqrt(abs(root_fit[1].x * root_fit[2].y - root_fit[1].y * root_fit[2].x))
    hem_radius = math.sqrt(abs(hem_fit[1].x * hem_fit[2].y - hem_fit[1].y * hem_fit[2].x))
    warnings = []
    if rolled_hem:
        warnings.append("A small rolled hem was detected; weights follow its surface distance.")
    if hem_radius <= waist_radius:
        warnings.append("The hem is no wider than the waist; the fitted cage follows this source silhouette.")
    return {
        "rings": rings,
        "vertices": [_point(point) for point in vertices],
        "waist_center": _point(inverse @ waist_center),
        "hem_center": _point(inverse @ hem_center),
        "ring_centers": [_point(inverse @ point) for point in centers],
        "signature": signature,
        "chains": chains,
        "vertex_weights": weights,
        "fitted_rings": fitted_rings,
        "ring_t": ring_t,
        "ring_count": len(rings),
        "columns_per_ring": columns,
        "chain_count": chain_count,
        "segment_count": segment_count,
        "height_world": float(height),
        "waist_radius_world": float(waist_radius),
        "hem_radius_world": float(hem_radius),
        "fit_waist": {"center": _point(inverse @ root_fit[0]),
                      "cosine": _point(inverse.to_3x3() @ root_fit[1]),
                      "sine": _point(inverse.to_3x3() @ root_fit[2])},
        "fit_hem": {"center": _point(inverse @ hem_fit[0]),
                    "cosine": _point(inverse.to_3x3() @ hem_fit[1]),
                    "sine": _point(inverse.to_3x3() @ hem_fit[2])},
        "matrix_world": [[float(value) for value in row] for row in matrix],
        "warnings": warnings,
    }
