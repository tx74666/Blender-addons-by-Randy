"""Pure, conservative plans for local deform-weight cleanup.

This module deliberately has no Blender dependency and never edits its inputs.
An adapter must recheck every complete ``old_weights`` membership before applying
a plan, and apply the whole plan transactionally.  Reference meshes may help
choose a bone's main island; they are never spatial donors for another mesh.
"""

from dataclasses import dataclass, field, replace
import heapq
import math
from numbers import Integral, Real
from types import MappingProxyType
from typing import Mapping, Sequence


DEFAULT_EPSILON = 0.0
DEFAULT_DISTANCE_TOLERANCE = 1.0e-6
# Leaves room for the fourth-order products used by geometric predicates.
_MAX_COORDINATE = 1.0e70


@dataclass(frozen=True)
class MeshSnapshot:
    """World-space geometry and exact group memberships, indexed by vertex.

    Input sequences are copied and validated by each planner, including unused
    reference vertices and non-deform memberships.  Zero memberships are valid.
    ``triangles`` are optional; they improve surface distance and area ranking.
    """

    name: str
    world_positions: Sequence
    edges: Sequence
    weights: Sequence
    triangles: Sequence = ()


@dataclass(frozen=True)
class VertexChange:
    mesh_name: str
    vertex_index: int
    old_weights: tuple
    new_weights: tuple

    def to_dict(self):
        return {
            "mesh_name": self.mesh_name,
            "vertex_index": self.vertex_index,
            "old_weights": dict(self.old_weights),
            "new_weights": dict(self.new_weights),
        }


@dataclass(frozen=True)
class IslandInfo:
    bone_name: str
    mesh_name: str
    vertex_indices: tuple
    distance: float
    surface_area: float
    weight_mass: float
    retained: bool

    def to_dict(self):
        return {
            "bone_name": self.bone_name,
            "mesh_name": self.mesh_name,
            "vertex_indices": list(self.vertex_indices),
            "distance": self.distance,
            "surface_area": self.surface_area,
            "weight_mass": self.weight_mass,
            "retained": self.retained,
        }


@dataclass(frozen=True)
class CleanupPlan:
    ok: bool
    mode: str
    changes: tuple = ()
    errors: tuple = ()
    warnings: tuple = ()
    stats: Mapping = field(default_factory=lambda: MappingProxyType({}))
    islands: tuple = ()

    @property
    def changed(self):
        return bool(self.changes)

    def to_dict(self):
        return {
            "ok": self.ok,
            "mode": self.mode,
            "changes": [change.to_dict() for change in self.changes],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
            "islands": [island.to_dict() for island in self.islands],
        }


@dataclass(frozen=True)
class InspectionRecord:
    """A diagnostic row, never authority to remove a merely distant region."""

    bone_name: str
    mesh_name: str
    status: str
    suspect_vertex_indices: tuple
    target_vertex_count: int
    main_mesh_name: object
    distance: object
    main_distance: object
    threshold: object
    reason: str
    reference_usage: tuple
    removable: bool = False
    distance_ratio: object = None
    stray_vertex_indices: tuple = ()
    distant_vertex_indices: tuple = ()

    @property
    def suspect_vertex_count(self):
        return len(self.suspect_vertex_indices)

    @property
    def affected_count(self):
        return self.suspect_vertex_count

    @property
    def stray_vertex_count(self):
        return len(self.stray_vertex_indices)

    @property
    def distant_vertex_count(self):
        return len(self.distant_vertex_indices)

    @property
    def min_distance(self):
        return self.distance

    @property
    def reason_code(self):
        return self.status

    def to_dict(self):
        return {
            "bone_name": self.bone_name,
            "mesh_name": self.mesh_name,
            "status": self.status,
            "suspect_vertex_indices": list(self.suspect_vertex_indices),
            "suspect_vertex_count": self.suspect_vertex_count,
            "target_vertex_count": self.target_vertex_count,
            "stray_vertex_indices": list(self.stray_vertex_indices),
            "stray_vertex_count": self.stray_vertex_count,
            "distant_vertex_indices": list(self.distant_vertex_indices),
            "distant_vertex_count": self.distant_vertex_count,
            "main_mesh_name": self.main_mesh_name,
            "distance": self.distance,
            "main_distance": self.main_distance,
            "threshold": self.threshold,
            "distance_ratio": self.distance_ratio,
            "removable": self.removable,
            "reason": self.reason,
            "reference_usage": dict(self.reference_usage),
        }


@dataclass(frozen=True)
class InspectionResult:
    ok: bool
    records: tuple = ()
    errors: tuple = ()
    warnings: tuple = ()
    stats: Mapping = field(default_factory=lambda: MappingProxyType({}))
    # This diagnostic API cannot return a weight-edit plan.
    changes: tuple = field(default=(), init=False)

    @property
    def suspects(self):
        return tuple(record for record in self.records if record.status != "OK")

    @property
    def changed(self):
        return False

    def to_dict(self):
        return {
            "ok": self.ok,
            "records": [record.to_dict() for record in self.records],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
            "changes": [],
        }


class _Invalid(ValueError):
    pass


@dataclass(frozen=True)
class _Mesh:
    name: str
    positions: tuple
    edges: tuple
    weights: tuple
    triangles: tuple
    adjacency: tuple
    vertex_areas: tuple
    vertex_triangles: tuple


def _number(value, label, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _Invalid(f"{label} must be a finite number.")
    value = float(value)
    if not math.isfinite(value):
        raise _Invalid(f"{label} must be finite.")
    if minimum is not None and value < minimum:
        raise _Invalid(f"{label} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise _Invalid(f"{label} must be at most {maximum}.")
    return value


def _name(value, label):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _Invalid(f"{label} must be a nonempty name without NUL characters.")
    return value


def _sequence(value, label):
    if isinstance(value, (str, bytes, Mapping)):
        raise _Invalid(f"{label} must be a sequence.")
    try:
        return tuple(value)
    except TypeError as exc:
        raise _Invalid(f"{label} must be a sequence.") from exc


def _point(value, label):
    values = _sequence(value, label)
    if len(values) != 3:
        raise _Invalid(f"{label} must have three coordinates.")
    return tuple(
        _number(item, label, minimum=-_MAX_COORDINATE, maximum=_MAX_COORDINATE)
        for item in values
    )


def _index(value, count, label):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _Invalid(f"{label} must be an integer vertex index.")
    value = int(value)
    if value < 0 or value >= count:
        raise _Invalid(f"{label} is outside the mesh vertex range.")
    return value


def _names(value, label):
    values = _sequence(value, label)
    return frozenset(_name(item, label) for item in values)


def _per_mesh_names(value, meshes, label, *, allow_missing=False):
    if isinstance(value, Mapping):
        known = {mesh.name for mesh in meshes}
        for key in value:
            _name(key, label)
            if key not in known:
                raise _Invalid(f"{label} refers to unknown mesh {key!r}.")
        result = {}
        for mesh in meshes:
            if mesh.name not in value and not allow_missing:
                raise _Invalid(f"{label} is missing mesh {mesh.name!r}.")
            result[mesh.name] = _names(value.get(mesh.name, ()), label)
        return result
    names = _names(value, label)
    return {mesh.name: names for mesh in meshes}


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return math.fsum((a[0] * b[0], a[1] * b[1], a[2] * b[2]))


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _along(a, delta, factor):
    return tuple(a[axis] + delta[axis] * factor for axis in range(3))


def _triangle_area(a, b, c):
    return math.hypot(*_cross(_sub(b, a), _sub(c, a))) * 0.5


def _mesh(snapshot):
    if not isinstance(snapshot, MeshSnapshot):
        raise _Invalid("Each mesh must be a MeshSnapshot.")
    name = _name(snapshot.name, "Mesh name")
    positions = tuple(
        _point(position, f"{name}: vertex {index}")
        for index, position in enumerate(_sequence(snapshot.world_positions, "Positions"))
    )
    count = len(positions)
    weight_inputs = _sequence(snapshot.weights, f"{name}: weights")
    if len(weight_inputs) != count:
        raise _Invalid(f"{name}: every vertex must have one weight mapping.")
    weights = []
    for index, memberships in enumerate(weight_inputs):
        if not isinstance(memberships, Mapping):
            raise _Invalid(f"{name}: vertex {index} weights must be a mapping.")
        validated = {}
        for group, weight in memberships.items():
            group = _name(group, f"{name}: group name")
            validated[group] = _number(
                weight, f"{name}: vertex {index}, group {group}", minimum=0.0, maximum=1.0
            )
        weights.append(MappingProxyType(validated))
    edges = set()
    for edge in _sequence(snapshot.edges, f"{name}: edges"):
        edge = _sequence(edge, f"{name}: edge")
        if len(edge) != 2:
            raise _Invalid(f"{name}: an edge must have two vertex indices.")
        a, b = (_index(item, count, f"{name}: edge") for item in edge)
        if a != b:
            edges.add((min(a, b), max(a, b)))
    triangles = set()
    for triangle in _sequence(snapshot.triangles, f"{name}: triangles"):
        triangle = _sequence(triangle, f"{name}: triangle")
        if len(triangle) != 3:
            raise _Invalid(f"{name}: a triangle must have three vertex indices.")
        triangle = tuple(_index(item, count, f"{name}: triangle") for item in triangle)
        if len(set(triangle)) != 3:
            raise _Invalid(f"{name}: a triangle must use three distinct vertex indices.")
        triangles.add(tuple(sorted(triangle)))
    adjacency = [[] for _ in positions]
    for a, b in sorted(edges):
        distance = math.dist(positions[a], positions[b])
        if not math.isfinite(distance):
            raise _Invalid(f"{name}: an edge length is not finite.")
        adjacency[a].append((b, distance))
        adjacency[b].append((a, distance))
    areas = [0.0] * count
    ordered_triangles = tuple(sorted(triangles))
    vertex_triangles = [[] for _ in positions]
    for triangle_index, triangle in enumerate(ordered_triangles):
        contribution = _triangle_area(*(positions[index] for index in triangle)) / 3.0
        for index in triangle:
            areas[index] += contribution
            vertex_triangles[index].append(triangle_index)
    if any(not math.isfinite(area) for area in areas):
        raise _Invalid(f"{name}: surface area is not finite.")
    return _Mesh(
        name, positions, tuple(sorted(edges)), tuple(weights), ordered_triangles,
        tuple(tuple(neighbors) for neighbors in adjacency), tuple(areas),
        tuple(tuple(indices) for indices in vertex_triangles),
    )


def _targets(value, mesh):
    if value is None:
        return frozenset(range(len(mesh.positions)))
    return frozenset(
        _index(item, len(mesh.positions), f"{mesh.name}: target")
        for item in _sequence(value, "Target vertices")
    )


def _total(weights, deform):
    return math.fsum(weight for name, weight in weights.items() if name in deform)


def _nearest_sources(mesh, seeds):
    """Multi-source graph distance; exact equal distances prefer source index."""
    best = [(math.inf, len(mesh.positions)) for _ in mesh.positions]
    queue = []
    for index in seeds:
        best[index] = (0.0, index)
        heapq.heappush(queue, (0.0, index, index))
    while queue:
        distance, source, index = heapq.heappop(queue)
        if best[index] != (distance, source):
            continue
        for neighbor, length in mesh.adjacency[index]:
            candidate = (distance + length, source)
            if candidate < best[neighbor]:
                best[neighbor] = candidate
                heapq.heappush(queue, (candidate[0], source, neighbor))
    return tuple(source if math.isfinite(distance) else None for distance, source in best)


def _fill_weights(mesh, deform, locked, targets, epsilon, forbidden_by_vertex=None):
    """Return new mappings only; all seed memberships precede this fill pass."""
    holes = tuple(index for index in sorted(targets) if _total(mesh.weights[index], deform) <= epsilon)
    if not holes:
        return {}, ()
    forbidden_by_vertex = forbidden_by_vertex or {}
    seeds = tuple(
        index for index, weights in enumerate(mesh.weights)
        if _total(weights, deform) > epsilon
    )
    by_forbidden = {}
    for index in holes:
        forbidden = frozenset(forbidden_by_vertex.get(index, ()))
        by_forbidden.setdefault(forbidden, []).append(index)
    updates = {}
    errors = []
    for forbidden, indices in sorted(by_forbidden.items(), key=lambda item: tuple(sorted(item[0]))):
        allowed = deform - locked - forbidden
        # A cleanup must not put the removed bone straight back.  Its donors
        # therefore need a transferable, non-forbidden contribution.  The
        # independent Fill tool uses the nearest original valid donor exactly.
        eligible = seeds
        if forbidden:
            eligible = tuple(
                index for index in seeds
                if math.fsum(weight for name, weight in mesh.weights[index].items() if name in allowed) > epsilon
            )
        nearest = _nearest_sources(mesh, eligible)
        for index in indices:
            source = nearest[index]
            if source is None:
                detail = "another eligible bone" if forbidden else "a weighted vertex"
                errors.append(
                    f"{mesh.name}: vertex {index} has no connected donor with {detail}; "
                    "disconnected parts are never used as donors."
                )
                continue
            donor = mesh.weights[source]
            transferable = {name: weight for name, weight in donor.items() if name in allowed and weight > 0.0}
            donor_total = math.fsum(transferable.values())
            locked_total = math.fsum(
                weight for name, weight in mesh.weights[index].items()
                if name in deform and name in locked
            )
            budget = 1.0 - locked_total
            if donor_total <= epsilon or budget <= epsilon:
                errors.append(
                    f"{mesh.name}: vertex {index} cannot inherit from nearest donor {source} "
                    "without changing locked groups."
                )
                continue
            new = dict(mesh.weights[index])
            # Keep membership presence, including exact zeros.  Only unlocked
            # deform values on this genuinely unweighted target are replaced.
            for name in tuple(new):
                if name in deform and name not in locked:
                    new[name] = 0.0
            for name, weight in transferable.items():
                new[name] = (weight / donor_total) * budget
            if _total(new, deform) <= epsilon:
                errors.append(f"{mesh.name}: vertex {index} could not receive an effective deform weight.")
            else:
                updates[index] = new
    return updates, tuple(errors)


def _changes(mesh, updated):
    result = []
    for index in sorted(updated):
        old = tuple(sorted(mesh.weights[index].items()))
        new = tuple(sorted(updated[index].items()))
        if old != new:
            result.append(VertexChange(mesh.name, index, old, new))
    return tuple(result)


def _stats(**values):
    result = {
        "meshes": 0,
        "target_vertices": 0,
        "changed_vertices": 0,
        "filled_vertices": 0,
        "removed_memberships": 0,
        "island_count": 0,
        "retained_islands": 0,
        "removed_islands": 0,
    }
    result.update(values)
    return MappingProxyType(result)


def _failure(mode, errors, *, stats=None, islands=()):
    if isinstance(errors, str):
        errors = (errors,)
    return CleanupPlan(False, mode, errors=tuple(errors), stats=stats or _stats(), islands=tuple(islands))


def plan_fill(mesh, deform_names, locked_names=(), targets=None, *, epsilon=DEFAULT_EPSILON):
    """Fill only zero-effective-weight targets from local surface graph seeds.

    Existing valid vertices are immutable donors.  The nearest original valid
    donor is selected by world edge length, with source vertex index breaking
    exact distance ties.  No seed, or a locked-only nearest donor, rejects the
    entire plan.  Non-deform and locked memberships are preserved exactly.
    """
    try:
        epsilon = _number(epsilon, "Weight epsilon", minimum=0.0, maximum=1.0)
        mesh = _mesh(mesh)
        deform = _per_mesh_names(deform_names, (mesh,), "Deform groups")[mesh.name]
        locked = _per_mesh_names(locked_names, (mesh,), "Locked groups", allow_missing=True)[mesh.name]
        targets = _targets(targets, mesh)
        stats = _stats(meshes=1, target_vertices=len(targets))
        updates, errors = _fill_weights(mesh, deform, locked, targets, epsilon)
        if errors:
            return _failure("FILL", errors, stats=stats)
        changes = _changes(mesh, updates)
        return CleanupPlan(
            True, "FILL", changes=changes,
            stats=_stats(meshes=1, target_vertices=len(targets), changed_vertices=len(changes), filled_vertices=len(changes)),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure("FILL", str(exc))


def plan_allowed_bones(mesh, deform_names, allowed_names, locked_names=(), targets=None):
    """Plan the user's explicit allowed-bone restriction on target vertices.

    Memberships of excluded deform bones are removed only at the requested
    vertices, including explicit zero memberships.  This does not remove vertex
    groups.  Non-deform memberships and allowed, surviving authored weights are
    preserved exactly; a locked membership that would be removed refuses the
    whole plan rather than silently skipping it.

    Only vertices made truly unweighted by this restriction are filled.  Their
    donors use the same mesh's edge graph and original positive contributions
    from allowed, unlocked deform bones, with nearest distance then source index
    choosing the donor.  Excluded influences are never transferred back.  A
    disconnected or otherwise donorless new hole refuses the entire operation.
    Pre-existing holes and tiny positive surviving contributions are untouched.
    """
    mode = "ALLOWED_BONES"
    try:
        mesh = _mesh(mesh)
        deform = _per_mesh_names(deform_names, (mesh,), "Deform groups")[mesh.name]
        allowed = _per_mesh_names(allowed_names, (mesh,), "Allowed bone groups")[mesh.name]
        locked = _per_mesh_names(locked_names, (mesh,), "Locked groups", allow_missing=True)[mesh.name]
        if allowed - deform:
            raise _Invalid("Allowed bone groups must belong to the mesh's deform-bone set.")
        targets = _targets(targets, mesh)
        excluded = deform - allowed
        updated = {}
        new_weights = list(mesh.weights)
        holes = set()
        locked_hits = {}
        removed_count = 0
        for index in sorted(targets):
            old = mesh.weights[index]
            removing = excluded.intersection(old)
            if not removing:
                continue
            for name in removing.intersection(locked):
                locked_hits[name] = locked_hits.get(name, 0) + 1
            new = {name: weight for name, weight in old.items() if name not in removing}
            updated[index] = new
            new_weights[index] = MappingProxyType(new)
            removed_count += len(removing)
            if _total(old, deform) > 0.0 and _total(new, deform) == 0.0:
                holes.add(index)
        base_stats = _stats(
            meshes=1, target_vertices=len(targets), removed_memberships=removed_count,
            allowed_bones=len(allowed), excluded_bones=len(excluded),
        )
        if locked_hits:
            return _failure(mode, tuple(
                f"{mesh.name}: excluded bone group {name!r} is locked on {count} target vertices."
                for name, count in sorted(locked_hits.items())
            ), stats=base_stats)
        filled_count = 0
        if holes:
            # Allowed donor values have not changed in this snapshot.  All
            # holes are filled together, so generated values never seed another
            # target, and previously excluded memberships stay absent.
            restricted_mesh = replace(mesh, weights=tuple(new_weights))
            fills, errors = _fill_weights(
                restricted_mesh, deform, locked, holes, 0.0,
                {index: excluded for index in holes},
            )
            if errors:
                return _failure(mode, errors, stats=base_stats)
            updated.update(fills)
            filled_count = len(fills)
        changes = _changes(mesh, updated)
        stats = dict(base_stats)
        stats.update(changed_vertices=len(changes), filled_vertices=filled_count)
        return CleanupPlan(True, mode, changes=changes, stats=_stats(**stats))
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure(mode, str(exc))


def _point_segment_sq(point, a, b):
    delta = _sub(b, a)
    length_sq = _dot(delta, delta)
    if length_sq == 0.0:
        return _dot(_sub(point, a), _sub(point, a))
    factor = max(0.0, min(1.0, _dot(_sub(point, a), delta) / length_sq))
    residual = _sub(point, _along(a, delta, factor))
    return _dot(residual, residual)


def _segment_segment_sq(a, b, c, d):
    first, second, offset = _sub(b, a), _sub(d, c), _sub(a, c)
    aa, ee = _dot(first, first), _dot(second, second)
    if aa == 0.0:
        return _point_segment_sq(a, c, d)
    if ee == 0.0:
        return _point_segment_sq(c, a, b)
    bb, cc, ff = _dot(first, second), _dot(first, offset), _dot(second, offset)
    denominator = aa * ee - bb * bb
    factor_first = max(0.0, min(1.0, (bb * ff - cc * ee) / denominator)) if denominator > 0.0 else 0.0
    factor_second = (bb * factor_first + ff) / ee
    if factor_second < 0.0:
        factor_second = 0.0
        factor_first = max(0.0, min(1.0, -cc / aa))
    elif factor_second > 1.0:
        factor_second = 1.0
        factor_first = max(0.0, min(1.0, (bb - cc) / aa))
    residual = _sub(_along(a, first, factor_first), _along(c, second, factor_second))
    return max(0.0, _dot(residual, residual))


def _point_triangle_sq(point, a, b, c):
    """Closest point on a closed triangle, including degenerate triangles."""
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(point, a)
    normal = _cross(ab, ac)
    if _dot(normal, normal) == 0.0:
        return min(_point_segment_sq(point, a, b), _point_segment_sq(point, b, c), _point_segment_sq(point, c, a))
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return _dot(ap, ap)
    bp = _sub(point, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return _dot(bp, bp)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        residual = _sub(point, _along(a, ab, d1 / (d1 - d3)))
        return _dot(residual, residual)
    cp = _sub(point, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return _dot(cp, cp)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        residual = _sub(point, _along(a, ac, d2 / (d2 - d6)))
        return _dot(residual, residual)
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and d4 - d3 >= 0.0 and d5 - d6 >= 0.0:
        factor = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        residual = _sub(point, _along(b, _sub(c, b), factor))
        return _dot(residual, residual)
    denominator = va + vb + vc
    if denominator <= 0.0:
        return min(_point_segment_sq(point, a, b), _point_segment_sq(point, b, c), _point_segment_sq(point, c, a))
    v, w = vb / denominator, vc / denominator
    closest = tuple(a[axis] + ab[axis] * v + ac[axis] * w for axis in range(3))
    residual = _sub(point, closest)
    return max(0.0, _dot(residual, residual))


def _segment_triangle_sq(start, end, a, b, c):
    # Endpoint/edge distances cover all disjoint cases.  A plane crossing in the
    # triangle interior is the remaining zero-distance case.
    result = min(
        _point_triangle_sq(start, a, b, c), _point_triangle_sq(end, a, b, c),
        _segment_segment_sq(start, end, a, b),
        _segment_segment_sq(start, end, b, c),
        _segment_segment_sq(start, end, c, a),
    )
    direction = _sub(end, start)
    normal = _cross(_sub(b, a), _sub(c, a))
    denominator = _dot(normal, direction)
    if denominator != 0.0:
        factor = _dot(normal, _sub(a, start)) / denominator
        if 0.0 <= factor <= 1.0:
            crossing = _along(start, direction, factor)
            # A scale-relative distance predicate avoids classifying a nearby
            # point outside the triangle as an intersection.
            distance_sq = _point_triangle_sq(crossing, a, b, c)
            scale_sq = max(_dot(_sub(b, a), _sub(b, a)), _dot(_sub(c, a), _sub(c, a)))
            if distance_sq <= scale_sq * 1.0e-28:
                return 0.0
    return result


def _component_info(mesh, bone, component, segment):
    members = frozenset(component)
    start, end = segment
    distance_sq = min(_point_segment_sq(mesh.positions[index], start, end) for index in members)
    if distance_sq > 0.0:
        for a in members:
            for b, _length in mesh.adjacency[a]:
                if a < b and b in members:
                    distance_sq = min(distance_sq, _segment_segment_sq(start, end, mesh.positions[a], mesh.positions[b]))
    if distance_sq > 0.0:
        triangle_indices = {
            triangle_index for index in members for triangle_index in mesh.vertex_triangles[index]
        }
        for triangle_index in triangle_indices:
            triangle = mesh.triangles[triangle_index]
            if all(index in members for index in triangle):
                distance_sq = min(distance_sq, _segment_triangle_sq(start, end, *(mesh.positions[index] for index in triangle)))
                if distance_sq == 0.0:
                    break
    if not math.isfinite(distance_sq):
        raise _Invalid(f"{mesh.name}: bone distance is not finite.")
    return IslandInfo(
        bone, mesh.name, tuple(sorted(members)), math.sqrt(max(0.0, distance_sq)),
        math.fsum(mesh.vertex_areas[index] for index in members),
        math.fsum(mesh.weights[index][bone] for index in members), False,
    )


def _positive_weight_indices(mesh, deform):
    """Index actual bone uses once for a validated, immutable mesh snapshot."""
    result = {}
    if not deform:
        return result
    for index, weights in enumerate(mesh.weights):
        for bone, weight in weights.items():
            if bone in deform and weight > 0.0:
                result.setdefault(bone, []).append(index)
    return {bone: tuple(indices) for bone, indices in result.items()}


def _bone_islands(mesh, bone, segment, *, positive_indices=None):
    # A tiny positive contribution is still a membership island.  The epsilon
    # used to decide whether a vertex is effectively unbound is separate.
    remaining = (
        {index for index, weights in enumerate(mesh.weights) if weights.get(bone, 0.0) > 0.0}
        if positive_indices is None else set(positive_indices)
    )
    result = []
    for first in sorted(remaining):
        if first not in remaining:
            continue
        remaining.remove(first)
        stack = [first]
        component = []
        while stack:
            index = stack.pop()
            component.append(index)
            for neighbor, _length in mesh.adjacency[index]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        result.append(_component_info(mesh, bone, component, segment))
    return result


def _main_island(islands, tolerance, bone):
    nearest = min(island.distance for island in islands)
    candidates = [island for island in islands if island.distance <= nearest + tolerance]
    for attribute in ("surface_area", "weight_mass"):
        if len(candidates) == 1:
            break
        largest = max(getattr(island, attribute) for island in candidates)
        candidates = [
            island for island in candidates
            if math.isclose(getattr(island, attribute), largest, rel_tol=1.0e-9, abs_tol=1.0e-15)
        ]
    if len(candidates) != 1:
        names = ", ".join(f"{island.mesh_name}[{island.vertex_indices[0]}]" for island in candidates[:6])
        raise _Invalid(f"Bone {bone!r} has equally plausible main islands ({names}); narrow the reference scope.")
    return candidates[0]


def _distance_ratio(distance, bone_length):
    if bone_length == 0.0:
        return None
    ratio = distance / bone_length
    return ratio if math.isfinite(ratio) else None


def plan_islands(
    meshes, target_mesh_names, selected_bone_segments, deform_names, locked_names=(), *,
    scope="CHARACTER", fill_holes=True, target_vertices=None,
    epsilon=DEFAULT_EPSILON, distance_tolerance=DEFAULT_DISTANCE_TOLERANCE,
):
    """Keep each selected bone's nearest main island and clear targeted strays.

    ``CHARACTER`` chooses one main island per bone across the reference meshes;
    ``CURRENT`` chooses independently inside each target mesh.  Positive bone
    memberships connect only through real mesh edges.  Bone-segment distance
    ranks islands first, followed by supported surface area and weight mass;
    an unresolved tie refuses the complete operation.

    Only requested mesh/vertex targets can change.  Other groups are untouched
    unless a formerly valid vertex becomes unbound: by default it then inherits
    from a connected surviving donor, filtering out every bone removed at that
    vertex.  Pre-existing holes are not filled by this cleanup operation.
    """
    try:
        epsilon = _number(epsilon, "Weight epsilon", minimum=0.0, maximum=1.0)
        tolerance = _number(distance_tolerance, "Distance tolerance", minimum=0.0)
        if scope not in ("CHARACTER", "CURRENT"):
            raise _Invalid("Island scope must be CHARACTER or CURRENT.")
        if not isinstance(fill_holes, bool):
            raise _Invalid("fill_holes must be a boolean.")
        meshes = tuple(_mesh(item) for item in _sequence(meshes, "Meshes"))
        by_name = {mesh.name: mesh for mesh in meshes}
        if len(by_name) != len(meshes):
            raise _Invalid("Mesh names must be unique within the reference set.")
        target_names = _names(target_mesh_names, "Target meshes")
        if target_names - by_name.keys():
            raise _Invalid("Target meshes must belong to the reference set.")
        deform = _per_mesh_names(deform_names, meshes, "Deform groups")
        locked = _per_mesh_names(locked_names, meshes, "Locked groups", allow_missing=True)
        if target_vertices is not None and not isinstance(target_vertices, Mapping):
            raise _Invalid("Target vertices must be a mesh-name mapping.")
        if target_vertices is not None and set(target_vertices) - target_names:
            raise _Invalid("Target vertices refer to a mesh outside the target set.")
        targets = {
            name: _targets(None if target_vertices is None else target_vertices.get(name, ()), by_name[name])
            for name in target_names
        }
        if not isinstance(selected_bone_segments, Mapping):
            raise _Invalid("Selected bones must map names to world-space head/tail segments.")
        segments = {}
        for bone, segment in selected_bone_segments.items():
            bone = _name(bone, "Selected bone")
            segment = _sequence(segment, f"{bone}: bone segment")
            if len(segment) != 2:
                raise _Invalid(f"{bone}: bone segment must have a head and tail.")
            segments[bone] = tuple(_point(item, f"{bone}: bone segment") for item in segment)
            if not any(bone in names for names in deform.values()):
                raise _Invalid(f"Selected bone {bone!r} is not a deform bone in the reference set.")
        base_stats = _stats(meshes=len(meshes), target_vertices=sum(map(len, targets.values())))
        if not segments or not any(targets.values()):
            return CleanupPlan(True, "ISLANDS", stats=base_stats)
        all_islands = []
        for bone, segment in sorted(segments.items()):
            groups = []
            if scope == "CHARACTER":
                groups.append([
                    island for mesh in meshes if bone in deform[mesh.name]
                    for island in _bone_islands(mesh, bone, segment)
                ])
            else:
                for name in sorted(target_names):
                    if bone in deform[name]:
                        groups.append(_bone_islands(by_name[name], bone, segment))
            for islands in groups:
                if islands:
                    main = _main_island(islands, tolerance, bone)
                    all_islands.extend(replace(island, retained=island is main) for island in islands)
        removed = {name: {} for name in target_names}
        errors = []
        removed_count = 0
        removed_islands = 0
        for island in all_islands:
            if island.retained or island.mesh_name not in target_names:
                continue
            indices = targets[island.mesh_name].intersection(island.vertex_indices)
            if not indices:
                continue
            if island.bone_name in locked[island.mesh_name]:
                errors.append(f"{island.mesh_name}: selected bone group {island.bone_name!r} is locked.")
                continue
            removed_islands += 1
            for index in indices:
                removed[island.mesh_name].setdefault(index, set()).add(island.bone_name)
                removed_count += 1
        island_stats = dict(base_stats)
        island_stats.update(
            island_count=len(all_islands),
            retained_islands=sum(island.retained for island in all_islands),
            removed_islands=removed_islands, removed_memberships=removed_count,
        )
        stats = _stats(**island_stats)
        if errors:
            return _failure("ISLANDS", errors, stats=stats, islands=all_islands)
        changes = []
        filled_count = 0
        for name in sorted(target_names):
            mesh = by_name[name]
            updated = {}
            new_weights = list(mesh.weights)
            holes = set()
            for index, bones in removed[name].items():
                new = {group: weight for group, weight in mesh.weights[index].items() if group not in bones}
                updated[index] = new
                new_weights[index] = MappingProxyType(new)
                if _total(mesh.weights[index], deform[name]) > epsilon and _total(new, deform[name]) <= epsilon:
                    holes.add(index)
            if fill_holes and holes:
                cleaned_mesh = replace(mesh, weights=tuple(new_weights))
                fills, fill_errors = _fill_weights(
                    cleaned_mesh, deform[name], locked[name], holes, epsilon, removed[name]
                )
                errors.extend(fill_errors)
                updated.update(fills)
                filled_count += len(fills)
            changes.extend(_changes(mesh, updated))
        final_stats = dict(stats)
        final_stats.update(changed_vertices=len(changes), filled_vertices=filled_count)
        stats = _stats(**final_stats)
        if errors:
            return _failure("ISLANDS", errors, stats=stats, islands=all_islands)
        return CleanupPlan(True, "ISLANDS", changes=tuple(changes), stats=stats, islands=tuple(all_islands))
    except (TypeError, ValueError, OverflowError) as exc:
        return _failure("ISLANDS", str(exc))


def inspect_islands(
    meshes, target_mesh_names, deform_names, bone_segments, *, scope="CHARACTER",
    epsilon=DEFAULT_EPSILON, distance_tolerance=DEFAULT_DISTANCE_TOLERANCE,
    remote_factor=2.0,
):
    """Inspect all actual positive deform-bone uses on the target meshes.

    This operation neither needs selected bones nor fills or removes weights.
    Every target bone/mesh gets a row; ``result.suspects`` excludes ``OK`` rows.
    A known non-main region is ``STRAY``.  Every positive deform membership is
    also checked individually: a vertex farther from its bone's Rest segment
    than ``max(remote_factor * bone_length, distance_tolerance, 1e-12)`` is only
    a ``DISTANT`` warning, even when connected to the retained main island.
    Distance is in world mesh units.  Only definite ``STRAY`` rows have
    ``removable=True``; any eventual cleanup still requires a fresh edit plan.

    An ambiguous main island or a missing/invalid bone segment becomes a
    diagnostic row and does not prevent checking the other target bones.
    Invalid mesh/reference data rejects the scan because its comparisons would
    not be trustworthy.  ``epsilon`` is validated for parity with edit plans;
    actual use counts include every strictly positive contribution.
    """
    try:
        _number(epsilon, "Weight epsilon", minimum=0.0, maximum=1.0)
        tolerance = _number(distance_tolerance, "Distance tolerance", minimum=0.0)
        remote_factor = _number(remote_factor, "Distant-region factor", minimum=0.0)
        if remote_factor == 0.0:
            raise _Invalid("Distant-region factor must be greater than zero.")
        if scope not in ("CHARACTER", "CURRENT"):
            raise _Invalid("Island scope must be CHARACTER or CURRENT.")
        meshes = tuple(_mesh(item) for item in _sequence(meshes, "Meshes"))
        by_name = {mesh.name: mesh for mesh in meshes}
        if len(by_name) != len(meshes):
            raise _Invalid("Mesh names must be unique within the reference set.")
        target_names = _names(target_mesh_names, "Target meshes")
        if target_names - by_name.keys():
            raise _Invalid("Target meshes must belong to the reference set.")
        deform = _per_mesh_names(deform_names, meshes, "Deform groups")
        if not isinstance(bone_segments, Mapping):
            raise _Invalid("Bone segments must map bone names to world-space head/tail segments.")
        positive = {
            name: _positive_weight_indices(by_name[name], deform[name])
            for name in sorted(target_names)
        }
        target_uses = {}
        for name in sorted(target_names):
            for bone, indices in positive[name].items():
                target_uses.setdefault(bone, {})[name] = indices
        # A few target bones can cost less to scan than to index every
        # reference membership. Keep the original scan path for small targets.
        use_index = len(target_uses) > 4
        if use_index:
            for mesh in meshes:
                if mesh.name not in positive:
                    positive[mesh.name] = _positive_weight_indices(
                        mesh, deform[mesh.name].intersection(target_uses),
                    )
        records = []
        warnings = []
        island_count = 0
        for bone, uses in sorted(target_uses.items()):
            usage = tuple(
                (mesh.name, count)
                for mesh in sorted(meshes, key=lambda item: item.name)
                if bone in deform[mesh.name]
                for count in (
                    len(positive[mesh.name].get(bone, ())) if use_index else
                    sum(weights.get(bone, 0.0) > 0.0 for weights in mesh.weights),
                )
                if count
            )
            try:
                if bone not in bone_segments:
                    raise _Invalid(f"Bone {bone!r} has no world-space head/tail segment.")
                raw_segment = _sequence(bone_segments[bone], f"{bone}: bone segment")
                if len(raw_segment) != 2:
                    raise _Invalid(f"{bone}: bone segment must have a head and tail.")
                segment = tuple(_point(point, f"{bone}: bone segment") for point in raw_segment)
                bone_length = math.dist(*segment)
                threshold = max(remote_factor * bone_length, tolerance, 1.0e-12)
                if not math.isfinite(threshold):
                    raise _Invalid(f"{bone}: the distant-region threshold is not finite.")
                reference = {}
                for mesh in meshes:
                    if bone in deform[mesh.name] and (scope == "CHARACTER" or mesh.name in uses):
                        reference[mesh.name] = (
                            _bone_islands(mesh, bone, segment,
                                          positive_indices=positive[mesh.name].get(bone, ()))
                            if use_index else _bone_islands(mesh, bone, segment)
                        )
                island_count += sum(map(len, reference.values()))
            except (TypeError, ValueError, OverflowError) as exc:
                for name, indices in sorted(uses.items()):
                    records.append(InspectionRecord(
                        bone, name, "UNRESOLVED", (), len(indices), None,
                        None, None, None, str(exc), usage,
                    ))
                continue
            for name, indices in sorted(uses.items()):
                candidates = (
                    [island for islands in reference.values() for island in islands]
                    if scope == "CHARACTER" else reference[name]
                )
                target_islands = reference[name]
                # Per-island minimum distance is insufficient here: a tiny
                # positive bridge can connect a near region to wrongly weighted
                # vertices far away.  Diagnose each actual membership instead.
                point_distances = {
                    index: math.sqrt(max(0.0, _point_segment_sq(by_name[name].positions[index], *segment)))
                    for index in indices
                }
                distant_indices = tuple(index for index in indices if point_distances[index] > threshold)
                try:
                    main = _main_island(candidates, tolerance, bone)
                except (TypeError, ValueError, OverflowError) as exc:
                    distance = min(point_distances[index] for index in (distant_indices or indices))
                    records.append(InspectionRecord(
                        bone, name, "AMBIGUOUS", distant_indices, len(indices), None,
                        distance, None, threshold, str(exc), usage,
                        distance_ratio=_distance_ratio(distance, bone_length),
                        distant_vertex_indices=distant_indices,
                    ))
                    continue
                strays = [island for island in target_islands if island is not main]
                stray_indices = tuple(sorted(index for island in strays for index in island.vertex_indices))
                if strays:
                    status = "STRAY"
                    reason = (
                        f"A separate weighted region is outside the nearest main island on {main.mesh_name!r}. "
                        "Only that non-main region is eligible for a fresh cleanup preview; "
                        "distant vertices on the retained main island are diagnostic only."
                    )
                elif distant_indices:
                    status = "DISTANT"
                    reason = (
                        "Some vertices are far from this bone's Rest segment, even if connected to its main region. "
                        "Distance alone never authorizes deleting its weights."
                    )
                else:
                    status = "OK"
                    reason = "The target uses the nearest main weighted region."
                suspect_indices = tuple(sorted(set(stray_indices).union(distant_indices)))
                distance = min(point_distances[index] for index in (suspect_indices or indices))
                if bone_length == 0.0:
                    warnings.append(
                        f"{bone}: zero-length bone; any distant-region warning uses only the absolute distance floor."
                    )
                records.append(InspectionRecord(
                    bone, name, status, suspect_indices, len(indices), main.mesh_name,
                    distance, main.distance, threshold, reason, usage,
                    removable=status == "STRAY",
                    distance_ratio=_distance_ratio(distance, bone_length),
                    stray_vertex_indices=stray_indices,
                    distant_vertex_indices=distant_indices,
                ))
        stats = MappingProxyType({
            "meshes": len(meshes),
            "target_meshes": len(target_names),
            "checked_bones": len(target_uses),
            "checked_memberships": sum(len(indices) for uses in target_uses.values() for indices in uses.values()),
            "record_count": len(records),
            "suspect_records": sum(record.status != "OK" for record in records),
            "suspect_memberships": sum(record.suspect_vertex_count for record in records),
            "stray_memberships": sum(record.stray_vertex_count for record in records),
            "distant_memberships": sum(record.distant_vertex_count for record in records),
            "stray_records": sum(record.status == "STRAY" for record in records),
            "distant_records": sum(record.status == "DISTANT" for record in records),
            "ambiguous_records": sum(record.status == "AMBIGUOUS" for record in records),
            "unresolved_records": sum(record.status == "UNRESOLVED" for record in records),
            "island_count": island_count,
        })
        return InspectionResult(True, tuple(records), warnings=tuple(dict.fromkeys(warnings)), stats=stats)
    except (TypeError, ValueError, OverflowError) as exc:
        return InspectionResult(False, errors=(str(exc),), stats=MappingProxyType({}))
