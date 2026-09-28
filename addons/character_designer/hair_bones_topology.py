"""Strict, read-only strand discovery for Hair Mesh -> Bones.

The mesh is inspected before modifiers. A seed restricts which strand is
returned; it does not make a connected scalp sheet into one strand. Point tips
grow only through regular face bands. Untapered strands use complete quad
cross-sections, with competing transverse partitions rejected by their aspect
ratio. This module creates no bones, curves, groups, or modifiers.
"""

import hashlib
import math
from collections import defaultdict

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
    # Grow the same BFS layers incrementally. A tip welded to a scalp must not
    # traverse that whole scalp and every other strand before rejecting its
    # first irregular layer.
    layers = [(seed,)]
    seen = {seed}
    regular_kind = None
    while True:
        following = {neighbor for index in layers[-1] for neighbor in adjacency[index]
                     if neighbor in allowed and neighbor not in seen}
        if not following:
            break
        layer = tuple(sorted(following))
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
        seen.update(following)
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


def terminal_patches(obj, bm, plans, *, allowed=None, adjacency=None):
    """Return small closed end caps without weakening regular strand layers.

    A patch owns no centerline section. It is accepted only as a complete
    manifold disk closing one strand's distal ring, and stays separate from
    persistent core signatures. Binding recomputes this geometric evidence.
    Ambiguous, hidden, open, shared, or oversized residual regions are omitted.
    """
    if allowed is None or adjacency is None:
        visible, _edges, graph = _visible_graph(bm)
        allowed = visible if allowed is None else set(allowed) & visible
        adjacency = {index: graph[index] & allowed for index in allowed}
    else:
        allowed = set(allowed)
    plans = tuple(plans)
    owners = defaultdict(set)
    by_signature = {}
    for plan in plans:
        by_signature[plan["signature"]] = plan
        for index in plan["vertices"]:
            owners[index].add(plan["signature"])
    covered = set(owners)
    pending = allowed - covered
    result = {}
    while pending:
        component = {pending.pop()}
        stack = list(component)
        boundary = set()
        while stack:
            for neighbor in adjacency[stack.pop()]:
                if neighbor in pending:
                    pending.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
                elif neighbor in covered:
                    boundary.add(neighbor)
        identities = {signature for index in boundary for signature in owners[index]}
        if len(identities) != 1:
            continue
        signature = next(iter(identities))
        plan = by_signature[signature]
        ring = set(plan["layers"][-1])
        if (not boundary or not boundary.issubset(ring) or not ring.issubset(allowed)
                or _kind(ring, adjacency) != "CLOSED" or len(plan["layers"]) < 2):
            continue
        domain = component | ring
        faces = {face for index in component for face in bm.verts[index].link_faces}
        # Do not let visibility/selection clipping turn part of the scalp or a
        # second strand into an apparently isolated terminal disk.
        if (not faces or any(face.hide or len(face.verts) < 3
                             or any(vertex.index not in domain or vertex.hide for vertex in face.verts)
                             for face in faces)
                or any(edge.hide or any(vertex.index not in domain or vertex.hide for vertex in edge.verts)
                       for index in component for edge in bm.verts[index].link_edges)):
            continue
        face_edges = defaultdict(set)
        links = {index: defaultdict(set) for index in domain}
        link_degrees = {index: defaultdict(int) for index in domain}
        face_vertices = set()
        for face in faces:
            indices = tuple(vertex.index for vertex in face.verts)
            face_vertices.update(indices)
            for position, index in enumerate(indices):
                previous, following = indices[position - 1], indices[(position + 1) % len(indices)]
                links[index][previous].add(following)
                links[index][following].add(previous)
                link_degrees[index][previous] += 1
                link_degrees[index][following] += 1
            for edge in face.edges:
                face_edges[edge].add(face)
        ring_keys = {tuple(sorted((index, neighbor))) for index in ring
                     for neighbor in adjacency[index] & ring if index < neighbor}
        boundary_keys = {_cd()._bm_edge_key(edge) for edge, linked in face_edges.items() if len(linked) == 1}
        if (face_vertices != domain or boundary_keys != ring_keys
                or any(len(linked) not in {1, 2} for linked in face_edges.values())
                or len(domain) - len(face_edges) + len(faces) != 1
                or any(edge not in face_edges for index in component for edge in bm.verts[index].link_edges)):
            continue
        # The end ring must have the regular strand on its other side, not a
        # third surface, a wire, or another cap sharing the same rim.
        core = set(plan["vertices"])
        if any(len(edge.link_faces) != 2
               or any(any(vertex.index not in core for vertex in face.verts)
                      for face in edge.link_faces if face not in faces)
               for edge, linked in face_edges.items() if len(linked) == 1):
            continue
        # Edge counts and Euler characteristic alone allow pinched vertices.
        # Interior vertex links must be cycles; boundary links must be paths.
        valid_links = True
        for index, link in links.items():
            degrees = tuple(link_degrees[index].values())
            # An inserted valence-two vertex has two parallel arcs in its
            # interior link. Keep multiplicity instead of rejecting that valid
            # two-edge cycle as a one-edge path.
            if (not degrees or any(degree not in {1, 2} for degree in degrees)
                    or sum(degree == 1 for degree in degrees) != (2 if index in ring else 0)):
                valid_links = False
                break
            reached = {next(iter(link))}
            link_stack = list(reached)
            while link_stack:
                for neighbor in link[link_stack.pop()] - reached:
                    reached.add(neighbor)
                    link_stack.append(neighbor)
            if reached != set(link):
                valid_links = False
                break
        if not valid_links:
            continue
        seen_faces = {next(iter(faces))}
        stack = list(seen_faces)
        while stack:
            for edge in stack.pop().edges:
                for face in face_edges[edge] - seen_faces:
                    seen_faces.add(face)
                    stack.append(face)
        if seen_faces != faces:
            continue
        points = {index: obj.matrix_world @ bm.verts[index].co for index in domain}
        previous_points = [obj.matrix_world @ bm.verts[index].co for index in plan["layers"][-2]]
        if any(not all(math.isfinite(value) for value in point)
               for point in (*points.values(), *previous_points)):
            continue
        center = sum((points[index] for index in ring), Vector()) / len(ring)
        previous = sum(previous_points, Vector()) / len(previous_points)
        tangent = center - previous
        span = tangent.length
        diameter = max((points[first] - points[second]).length for first in ring for second in ring)
        if span <= 1.0e-12 or diameter <= 1.0e-12:
            continue
        tangent /= span
        epsilon = max(span, diameter) * 1.0e-6
        offsets = tuple(points[index] - center for index in component)
        if any(offset.dot(tangent) < -epsilon or offset.dot(tangent) > 1.5 * span + epsilon
               or (offset - tangent * offset.dot(tangent)).length > diameter + epsilon
               for offset in offsets):
            continue
        result[signature] = tuple(sorted(component))
    return result


def _discover(obj, bm, allowed, edge_keys, adjacency, seeds):
    cd = _cd()
    # The band helper also serves one-off manual guides. Automatic discovery
    # shares its lookup instead of rebuilding all E entries for each of E rails.
    edge_by_key = {cd._bm_edge_key(edge): edge for edge in bm.edges}
    candidates = {}
    point_covered_edges = set()
    validated = {}

    def strict_plan(layers):
        key = min(layers, tuple(reversed(layers)))
        if key not in validated:
            vertices = {index for layer in layers for index in layer}
            # Supply exactly the same induced edges as the full graph scan,
            # but visit only this candidate's incident edges.
            local_edges = tuple(sorted((index, neighbor) for index in vertices
                                       for neighbor in adjacency[index] & vertices
                                       if index < neighbor))
            validated[key] = _strict_plan(obj, bm, layers, local_edges)
        return validated[key]

    for seed in sorted(allowed):
        if _kind(adjacency[seed], adjacency) not in {"OPEN", "CLOSED"}:
            continue
        layers = _point_layers(seed, allowed, adjacency)
        # Only the maximal regular prefix is proposed. Its complete face bands
        # must still pass the existing strict validator.
        plan = strict_plan(layers)
        if plan and plan["aspect_ratio"] >= _MIN_ASPECT and plan["root_tip_rule"] == "COLLAPSED_TIP":
            candidates[plan["signature"]] = plan
            covered = set(plan["vertices"])
            point_covered_edges.update((index, neighbor) for index in covered
                                       for neighbor in adjacency[index] & covered
                                       if index < neighbor)

    examined_bands = set()
    examined_rails = set()
    for edge in edge_keys:
        if edge in point_covered_edges or edge in examined_rails:
            continue
        try:
            first, second, band_edges = cd._quad_band_from_rail_edge(
                bm, edge, edge_by_key=edge_by_key)
        except cd.CenterlineError:
            continue
        first_set, second_set = set(first), set(second)
        # Every longitudinal edge in this band gives the same two slices.
        # Transverse edges must remain eligible for competing partitions.
        examined_rails.update(key for key in band_edges
                              if (key[0] in first_set and key[1] in second_set)
                              or (key[1] in first_set and key[0] in second_set))
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
        plan = strict_plan(layers)
        if plan is not None:
            # A strictly valid maximal partition is identical from any of its
            # interior bands. Skip those rails before repeating band expansion.
            for previous, current in zip(layers, layers[1:]):
                current_set = set(current)
                examined_rails.update(tuple(sorted((index, neighbor)))
                                      for index in previous
                                      for neighbor in adjacency[index] & current_set)
        if plan and plan["aspect_ratio"] >= _MIN_ASPECT:
            candidates[plan["signature"]] = plan

    ordered = sorted(candidates.values(), key=lambda plan: (
        plan["root_tip_rule"] == "COLLAPSED_TIP", plan["aspect_ratio"], len(plan["vertices"])), reverse=True)
    accepted = []
    occupied, occupied_inner = set(), set()
    for plan in ordered:
        # Resolve partitions before applying seeds: a clicked transverse edge
        # must not make a previously rejected crosswise candidate win.
        vertices, roots = set(plan["vertices"]), set(plan["layers"][0])
        if (vertices - roots) & occupied or roots & occupied_inner:
            continue
        accepted.append(plan)
        occupied.update(vertices)
        occupied_inner.update(vertices - roots)
    if seeds:
        patches = terminal_patches(obj, bm, accepted, allowed=allowed, adjacency=adjacency)
        accepted = [plan for plan in accepted
                    if seeds & (set(plan["vertices"]) | set(patches.get(plan["signature"], ())))]
    return tuple(sorted(accepted, key=lambda plan: (min(plan["vertices"]), plan["signature"])))


def discover_strands(context, *, selected_only=False, respect_selection=True):
    """Discover regular strands in the active Edit Mesh without editing it.

    The default scans visible geometry, then retains strands touching any
    selected visible vertex. With no selected vertices it scans the whole mesh.
    respect_selection=False searches all visible strands regardless of the
    existing selection, useful for the whole-hair capture button.
    selected_only=True instead forbids expansion beyond the visible selection.
    Empty/ambiguous input returns no plans; no partial chain is silently built.
    """
    obj, bm = _edit(context)
    allowed, edges, adjacency = _visible_graph(bm, selected_only)
    if not allowed:
        return obj, ()
    seeds = {v.index for v in bm.verts if v.select and not v.hide} if respect_selection else set()
    return obj, _discover(obj, bm, allowed, edges, adjacency, seeds)


def select_strands(context, *, respect_selection=True):
    """Replace selection with discovered strands and remember their partition."""
    obj, plans = discover_strands(context, respect_selection=respect_selection)
    if not plans:
        raise HairTopologyError("No unambiguous regular hair strand was found. Select a tip or a longitudinal guide path.")
    _, bm = _edit(context)
    vertices = {index for plan in plans for index in plan["vertices"]}
    patches = terminal_patches(obj, bm, plans)
    vertices.update(index for patch in patches.values() for index in patch)
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
