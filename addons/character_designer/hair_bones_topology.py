"""Strict, read-only strand discovery for Hair Mesh -> Bones.

The mesh is inspected before modifiers. A seed restricts which strand is
returned; it does not make a connected scalp sheet into one strand. Point tips
grow only through regular face bands. Untapered strands use complete quad
cross-sections, with competing transverse partitions rejected by their aspect
ratio. This module creates no bones, curves, groups, or modifiers.
"""

import hashlib
import math
from collections import defaultdict, deque

import bmesh
from mathutils import Vector


class HairTopologyError(ValueError):
    pass


_SELECTION_CACHE = {}
_MIN_ASPECT = 1.75


def _cd():
    # __init__ imports this module when registering the UI.
    import character_designer
    return character_designer


def _edit(context):
    try:
        obj, bm = _cd()._edit_bmesh(context)
    except _cd().CenterlineError as exc:
        raise HairTopologyError(str(exc)) from exc
    return obj, bm


def _visible_graph(bm, selected_only=False):
    vertices = {v.index for v in bm.verts if not v.hide and (not selected_only or v.select)}
    edges = tuple(sorted(_cd()._bm_edge_key(edge) for edge in bm.edges
                         if not edge.hide and all(v.index in vertices for v in edge.verts)))
    return vertices, edges, _cd()._adjacency_from_edge_keys(vertices, edges)


def _signature(layers):
    canonical = tuple(tuple(sorted(layer)) for layer in layers)
    canonical = min(canonical, tuple(reversed(canonical)))
    return hashlib.sha256(repr(canonical).encode("ascii")).hexdigest()


def _fingerprint(obj, bm):
    # Coordinate and matrix changes affect centers, aspect, and root direction.
    return hashlib.sha256(repr((obj.data.as_pointer(),
        tuple((tuple(v.co), v.hide) for v in bm.verts),
        tuple((_cd()._bm_edge_key(e), e.hide) for e in bm.edges),
        tuple((tuple(v.index for v in f.verts), f.hide) for f in bm.faces),
        tuple(value for row in obj.matrix_world for value in row))).encode()).hexdigest()


def _selection(bm):
    return (tuple(v.index for v in bm.verts if v.select),
            tuple(e.index for e in bm.edges if e.select),
            tuple(f.index for f in bm.faces if f.select))


def _plain_copy(plans):
    # Plans contain only immutable tuples/scalars; callers may alter the dict.
    return tuple(dict(plan) for plan in plans)


def _kind(layer, adjacency):
    try:
        return _cd()._classify_slice(layer, adjacency, label="A hair cross-section")
    except _cd().CenterlineError:
        return None


def _regular_pair(first, second, adjacency):
    first, second = set(first), set(second)
    links = [(a, b) for a in first for b in adjacency[a] & second]
    first_degree, second_degree = defaultdict(int), defaultdict(int)
    for a, b in links:
        first_degree[a] += 1
        second_degree[b] += 1
    if len(first) == 1:
        return len(second) > 1 and len(links) == len(second) and all(second_degree[b] == 1 for b in second)
    if len(second) == 1:
        return len(links) == len(first) and all(first_degree[a] == 1 for a in first)
    return (len(first) == len(second) == len(links)
            and all(first_degree[a] == 1 for a in first)
            and all(second_degree[b] == 1 for b in second))


def _point_layers(seed, allowed, adjacency):
    distances = {seed: 0}
    queue = deque((seed,))
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in distances:
                distances[neighbor] = distances[current] + 1
                queue.append(neighbor)
    grouped = defaultdict(list)
    for index, distance in distances.items():
        grouped[distance].append(index)
    layers = [(seed,)]
    regular_kind = None
    for distance in range(1, max(grouped, default=0) + 1):
        layer = tuple(sorted(grouped[distance]))
        kind = _kind(layer, adjacency)
        if kind not in {"OPEN", "CLOSED", "POINT"}:
            break
        if kind != "POINT":
            if regular_kind is not None and kind != regular_kind:
                break
            regular_kind = kind
        if not _regular_pair(layers[-1], layer, adjacency):
            break
        layers.append(layer)
        if kind == "POINT":
            break
    return tuple(layers)


def _extend_layers(layers, adjacency, regular_kind):
    layers = list(layers)
    seen = {index for layer in layers for index in layer}
    while len(layers[-1]) > 1:
        previous, current = set(layers[-2]), set(layers[-1])
        outside = {index: adjacency[index] - current - previous for index in current}
        # Every section vertex has one outgoing rail, or all terminate together.
        if any(len(neighbors) != 1 for neighbors in outside.values()):
            break
        following = set().union(*outside.values())
        if following & seen:
            break
        kind = _kind(following, adjacency)
        if kind not in {regular_kind, "POINT"} or not _regular_pair(current, following, adjacency):
            break
        layers.append(tuple(sorted(following)))
        seen.update(following)
        if kind == "POINT":
            break
    return tuple(layers)


def _world_diameter(obj, bm, layer):
    points = [obj.matrix_world @ bm.verts[index].co for index in layer]
    return max(((a - b).length for pos, a in enumerate(points) for b in points[pos + 1:]), default=0.0)


def _strict_plan(obj, bm, layers, edge_keys, *, explicit_direction=False):
    cd = _cd()
    layers = tuple(tuple(sorted(layer)) for layer in layers)
    if len(layers) < 2:
        return None
    vertices = {index for layer in layers for index in layer}
    selected_edges = tuple(edge for edge in edge_keys if edge[0] in vertices and edge[1] in vertices)
    root = set(layers[0])
    root_edges = tuple(edge for edge in selected_edges if edge[0] in root and edge[1] in root)
    try:
        _, _, parsed, centers = cd._extract_layers_from_seed(obj, bm, vertices, layers[0], root_edges,
            selected_edge_keys=selected_edges, allow_coincident_endpoint_points=True)
        if parsed != layers:
            return None
        regular_kind = cd._regular_kind_from_layers(bm, parsed)
        first_role = cd._collapsed_point_role(bm, tuple(reversed(parsed)), tuple(reversed(centers)), regular_kind)
        last_role = cd._collapsed_point_role(bm, parsed, centers, regular_kind)
    except cd.CenterlineError:
        return None

    # A triangulated cap hub is not a centerline segment or a hair tip.
    if first_role == "CAP":
        parsed, centers = parsed[1:], centers[1:]
        first_role = "NONE"
    if last_role == "CAP":
        parsed, centers = parsed[:-1], centers[:-1]
        last_role = "NONE"
    if len(parsed) < 2:
        return None
    world_centers = tuple(obj.matrix_world @ center for center in centers)
    length = sum((b - a).length for a, b in zip(world_centers, world_centers[1:]))
    diameters = tuple(_world_diameter(obj, bm, layer) for layer in parsed if len(layer) > 1)
    width = sum(diameters) / len(diameters) if diameters else 0.0
    if not math.isfinite(length) or width <= 1.0e-12 or length <= 1.0e-12:
        return None
    aspect = length / width
    # Two projected tips describe a connection between strands, not an
    # identifiable single root-to-tip strand. Do not route one chain through it.
    if first_role == last_role == "TIP":
        return None
    reverse = False
    direction = True
    if (first_role == "TIP") != (last_role == "TIP"):
        reverse = first_role == "TIP"
        rule = "COLLAPSED_TIP"
    elif explicit_direction:
        rule = "ACTIVE_ROOT"
    else:
        height = world_centers[-1].z - world_centers[0].z
        first_width = _world_diameter(obj, bm, parsed[0])
        last_width = _world_diameter(obj, bm, parsed[-1])
        # Relative height, not a fixed model/scalp Z threshold. Width resolves
        # near-horizontal strands; equally broad level ends remain ambiguous.
        if abs(height) > length * 0.03:
            reverse = height > 0.0
            rule = "HIGHER_WORLD_Z_ROOT"
        elif abs(last_width - first_width) > max(first_width, last_width) * 0.15:
            reverse = last_width > first_width
            rule = "WIDER_ENDPOINT_ROOT"
        else:
            reverse = parsed[-1] < parsed[0]
            direction = False
            rule = "AMBIGUOUS_ENDPOINTS"
    if reverse:
        parsed, centers = tuple(reversed(parsed)), tuple(reversed(centers))
    return {"layers": tuple(parsed), "centers": tuple(tuple(center) for center in centers),
            "vertices": tuple(sorted(index for layer in parsed for index in layer)),
            "signature": _signature(parsed), "direction_confirmable": direction,
            "root_tip_rule": rule, "profile_kind": regular_kind,
            "aspect_ratio": aspect, "length_world": length}


def _shared_root_only(first, second):
    overlap = set(first["vertices"]) & set(second["vertices"])
    return bool(overlap and overlap.issubset(first["layers"][0])
                and overlap.issubset(second["layers"][0]))


def _discover(obj, bm, allowed, edge_keys, adjacency, seeds):
    candidates = {}
    point_covered = []
    for seed in sorted(allowed):
        if _kind(adjacency[seed], adjacency) not in {"OPEN", "CLOSED"}:
            continue
        layers = _point_layers(seed, allowed, adjacency)
        # Only the maximal regular prefix is proposed. Its complete face bands
        # must still pass the existing strict validator.
        plan = _strict_plan(obj, bm, layers, edge_keys)
        if plan and plan["aspect_ratio"] >= _MIN_ASPECT and plan["root_tip_rule"] == "COLLAPSED_TIP":
            candidates[plan["signature"]] = plan
            point_covered.append(set(plan["vertices"]))

    examined_bands = set()
    for edge in edge_keys:
        if any(edge[0] in covered and edge[1] in covered for covered in point_covered):
            continue
        try:
            first, second, _band_edges = _cd()._quad_band_from_rail_edge(bm, edge)
        except _cd().CenterlineError:
            continue
        if not set(first + second).issubset(allowed):
            continue
        pair = min((first, second), (second, first))
        if pair in examined_bands:
            continue
        examined_bands.add(pair)
        kind = _kind(first, adjacency)
        if kind not in {"OPEN", "CLOSED"} or _kind(second, adjacency) != kind:
            continue
        forward = _extend_layers((first, second), adjacency, kind)
        backward = _extend_layers((second, first), adjacency, kind)
        layers = tuple(reversed(backward[1:])) + forward[1:]
        plan = _strict_plan(obj, bm, layers, edge_keys)
        if plan and plan["aspect_ratio"] >= _MIN_ASPECT:
            candidates[plan["signature"]] = plan

    ordered = sorted(candidates.values(), key=lambda plan: (
        plan["root_tip_rule"] == "COLLAPSED_TIP", plan["aspect_ratio"], len(plan["vertices"])), reverse=True)
    accepted = []
    for plan in ordered:
        # Resolve partitions before applying seeds: a clicked transverse edge
        # must not make a previously rejected crosswise candidate win.
        if any(set(plan["vertices"]) & set(other["vertices"]) and not _shared_root_only(plan, other)
               for other in accepted):
            continue
        accepted.append(plan)
    if seeds:
        accepted = [plan for plan in accepted if seeds & set(plan["vertices"])]
    return tuple(sorted(accepted, key=lambda plan: (min(plan["vertices"]), plan["signature"])))


def discover_strands(context, *, selected_only=False):
    """Discover regular strands in the active Edit Mesh without editing it.

    The default scans visible geometry, then retains strands touching any
    selected visible vertex. With no selected vertices it scans the whole mesh.
    selected_only=True instead forbids expansion beyond the visible selection.
    Empty/ambiguous input returns no plans; no partial chain is silently built.
    """
    obj, bm = _edit(context)
    allowed, edges, adjacency = _visible_graph(bm, selected_only)
    if not allowed:
        return obj, ()
    seeds = {v.index for v in bm.verts if v.select and not v.hide}
    return obj, _discover(obj, bm, allowed, edges, adjacency, seeds)


def select_strands(context):
    """Replace selection with discovered strands and remember their partition."""
    obj, plans = discover_strands(context)
    if not plans:
        raise HairTopologyError("No unambiguous regular hair strand was found. Select a tip or a longitudinal guide path.")
    _, bm = _edit(context)
    vertices = {index for plan in plans for index in plan["vertices"]}
    for face in bm.faces:
        face.select = False
    for edge in bm.edges:
        edge.select = False
    for vertex in bm.verts:
        vertex.select = vertex.index in vertices and not vertex.hide
    for edge in bm.edges:
        edge.select = not edge.hide and all(vertex.index in vertices for vertex in edge.verts)
    for face in bm.faces:
        face.select = not face.hide and all(vertex.index in vertices for vertex in face.verts)
    # Set vertices last: deselecting an adjacent unselected face/edge may flush
    # its boundary vertices down in some existing Edit Mesh selection states.
    for vertex in bm.verts:
        vertex.select = vertex.index in vertices and not vertex.hide
    bm.select_history.clear()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    _SELECTION_CACHE[obj.as_pointer()] = (_fingerprint(obj, bm), _selection(bm), _plain_copy(plans))
    return obj, _plain_copy(plans)


def selected_strands(context):
    """Validate selected full bands/rails, retaining a cached welded partition."""
    obj, bm = _edit(context)
    cached = _SELECTION_CACHE.get(obj.as_pointer())
    if cached and cached[0] == _fingerprint(obj, bm) and cached[1] == _selection(bm):
        return obj, _plain_copy(cached[2])
    _SELECTION_CACHE.pop(obj.as_pointer(), None)
    recorded = _revalidate_owned_selection(obj, bm)
    if recorded is not None:
        _SELECTION_CACHE[obj.as_pointer()] = (_fingerprint(obj, bm), _selection(bm), _plain_copy(recorded))
        return obj, recorded
    try:
        _, _, records = _cd()._infer_selected_recovery_components(context)
    except _cd().CenterlineError as exc:
        raise HairTopologyError(str(exc)) from exc
    plans = []
    for record in records:
        plan = _strict_plan(obj, bm, record["layers"], record["selected_edge_keys"],
                            explicit_direction=record["direction_confirmable"])
        if plan is None:
            raise HairTopologyError("A selected hair strand no longer forms complete regular cross-sections.")
        plans.append(plan)
    return obj, tuple(plans)


def _revalidate_owned_selection(obj, bm):
    # Edit/Object/Pose transitions normalize edge and face selection flags, and
    # an addon reload intentionally loses this module's transient cache. The
    # builder's ownership record is a partition hint, never cached geometry.
    from . import hair_bones_rig
    try:
        record = hair_bones_rig._read_records(obj)
    except hair_bones_rig.HairBonesRigError as exc:
        raise HairTopologyError(str(exc)) from exc
    if record is None:
        return None
    selected = {vertex.index for vertex in bm.verts if vertex.select and not vertex.hide}
    if not selected:
        return None
    try:
        # Shared-chain records retain their individual surface strips. A group
        # guide is not a regular mesh strip and must never be parsed as one.
        members = [member for chain in record["chains"] if not chain.get("mirror_of")
                   for member in (chain.get("members") or (chain,))]
        chains = [chain for chain in members if set(chain["vertices"]).issubset(selected)]
        if not chains or set().union(*(set(chain["vertices"]) for chain in chains)) != selected:
            return None
        if record["topology"] != hair_bones_rig._mesh_snapshot(obj)["topology"]:
            raise HairTopologyError("The recorded hair topology changed. Restore it before selecting its generated chains.")
        edges = tuple(_cd()._bm_edge_key(edge) for edge in bm.edges)
        plans = []
        for chain in chains:
            plan = _strict_plan(obj, bm, chain["layers"], edges, explicit_direction=True)
            if (plan is None or plan["signature"] != chain["signature"]
                    or set(plan["vertices"]) != set(chain["vertices"])):
                raise HairTopologyError("A generated hair strand no longer matches complete regular cross-sections.")
            plan["root_tip_rule"] = chain.get("root_tip_rule", plan["root_tip_rule"])
            plans.append(plan)
        return tuple(plans)
    except (TypeError, KeyError, IndexError, ValueError) as exc:
        if isinstance(exc, HairTopologyError):
            raise
        raise HairTopologyError("The generated hair strand record is incomplete or no longer valid.") from exc


def clear_cache():
    _SELECTION_CACHE.clear()
