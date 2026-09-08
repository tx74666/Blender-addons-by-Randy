"""Native Mirror planning for complete, independently poseable hair rigs.

The source stays a half mesh. Mirror creates the opposite vertex groups before
Armature evaluates them; center-seam strands share one centered bone chain.
"""

import copy

import bmesh
import bpy
from mathutils import Vector


LAYOUT = "BEFORE_ARMATURE_X"


def _error(message):
    from .hair_bones_rig import HairBonesRigError
    return HairBonesRigError(message)


def _geometry(obj):
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        coordinates = tuple(vertex.co.copy() for vertex in bm.verts)
        edges = tuple(tuple(vertex.index for vertex in edge.verts) for edge in bm.edges)
        boundary = {vertex.index for edge in bm.edges if edge.is_boundary for vertex in edge.verts}
    else:
        coordinates = tuple(vertex.co.copy() for vertex in obj.data.vertices)
        edges = tuple(tuple(edge.vertices) for edge in obj.data.edges)
        uses = {tuple(sorted(edge)): 0 for edge in edges}
        for face in obj.data.polygons:
            for edge in face.edge_keys:
                uses[tuple(sorted(edge))] += 1
        boundary = {vertex for edge, count in uses.items() if count == 1 for vertex in edge}
    adjacency = {index: set() for index in range(len(coordinates))}
    for first, second in edges:
        adjacency[first].add(second)
        adjacency[second].add(first)
    return coordinates, adjacency, boundary


def _tolerance(modifier, coordinates):
    scale = max((point.length for point in coordinates), default=1.0)
    return max(float(modifier.merge_threshold) if modifier.use_mirror_merge else 0.0,
               scale * 1.0e-7, 1.0e-7)


def _classification(member, coordinates, adjacency, boundary, tolerance):
    """Recognize a longitudinal mirror seam, not merely a root on the plane."""
    regular = [tuple(layer) for layer in member["layers"] if len(layer) > 1]
    checks = []
    for layer in regular:
        domain = set(layer)
        endpoints = [index for index in layer if len(adjacency[index] & domain) == 1]
        seam = [index for index in endpoints if index in boundary and abs(coordinates[index].x) <= tolerance]
        checks.append(len(endpoints) == 2 and bool(seam))
    # A joined scalp root can have no mesh boundary. The remaining full rows
    # must have one continuous seam; a side strand touching it only at the root
    # must retain its own left/right pair.
    inner = checks[1:] if len(checks) > 2 else checks
    centered = bool(inner) and all(inner)
    values = [coordinates[index].x for index in member["vertices"]]
    positive = any(value > tolerance for value in values)
    negative = any(value < -tolerance for value in values)
    if positive and negative:
        raise _error("A selected strand crosses both sides of its Mirror plane; use a clean half-mesh source.")
    if centered:
        return "C"
    if not positive and not negative:
        raise _error("A selected strand lies entirely on the Mirror plane; its width is ambiguous.")
    return "L" if positive else "R"


def preflight(obj, plans=None):
    """Validate the supported native layout before creating any result data."""
    mirrors = tuple(modifier for modifier in obj.modifiers if modifier.type == "MIRROR")
    if not mirrors:
        return None
    if len(mirrors) != 1:
        raise _error("Full hair controls support one X-axis Mirror modifier; simplify the source Mirror stack first.")
    modifier = mirrors[0]
    if tuple(modifier.use_axis) != (True, False, False) or any(modifier.use_bisect_axis):
        raise _error("Full hair controls need a single X-axis Mirror without Bisect.")
    if not modifier.show_viewport or not modifier.show_render:
        raise _error("Enable the hair Mirror in both viewport and render before generating full controls.")
    reference = modifier.mirror_object
    # Older Hair Bones versions supplied an owned, animated reference. The
    # variant copy removes it; other artists' custom planes remain unsupported.
    if reference is not None and not (
            reference.get("character_designer_hair_bones_owner") == "hair_bones_v1"
            and reference.get("character_designer_hair_bones_source") is obj
            and obj.get("character_designer_hair_bones_mirror_plane") is reference):
        raise _error("Full hair controls need the Mesh-local Mirror plane; custom Mirror Objects are not supported.")
    preceding = tuple(obj.modifiers)[:tuple(obj.modifiers).index(modifier)]
    if any(item.type != "ARMATURE" for item in preceding):
        raise _error("Place Mirror before the other hair geometry modifiers before generating full controls.")
    if not modifier.use_mirror_vertex_groups:
        names = set(obj.vertex_groups.keys())
        if any(bpy.utils.flip_name(name) != name and bpy.utils.flip_name(name) in names for name in names):
            raise _error("Existing left/right vertex groups have Mirror flipping disabled; resolve their Mirror behavior before generating full hair controls.")
    if plans is not None:
        coordinates, adjacency, boundary = _geometry(obj)
        tolerance = _tolerance(modifier, coordinates)
        for plan in plans:
            if "members" in plan:
                raise _error("Each strand needs its own bone chain; shared-chain plans are no longer supported.")
            side = _classification(plan, coordinates, adjacency, boundary, tolerance)
            if side == "C" and not modifier.use_mirror_merge:
                raise _error("Enable Mirror Merge so the central hair strand is joined before deformation.")
    return modifier


def preview_full_chain_count(obj, plans):
    """Number of visible controllable chains, including the generated half."""
    plans = tuple(plans)
    modifier = preflight(obj, plans)
    if modifier is None:
        return len(plans)
    coordinates, adjacency, boundary = _geometry(obj)
    tolerance = _tolerance(modifier, coordinates)
    return sum(1 if _classification(plan, coordinates, adjacency, boundary, tolerance) == "C" else 2 for plan in plans)


def _distances(points):
    result = [0.0]
    for first, second in zip(points, points[1:]):
        result.append(result[-1] + (second - first).length)
    return tuple(result)


def _centered_points(member, coordinates, tolerance):
    points = []
    for layer in member["layers"]:
        values = [coordinates[index] for index in layer]
        seam = [point for point in values if abs(point.x) <= tolerance]
        center = (2.0 * sum(values, Vector()) - sum(seam, Vector())) / (2 * len(values) - len(seam))
        center.x = 0.0
        points.append(center)
    return tuple(points)


def prepare_plans(obj, plans):
    """Expand copied/validated plans; mirrored plans receive no base weights."""
    plans = tuple(plans)
    modifier = preflight(obj, plans)
    if modifier is None:
        return plans
    coordinates, adjacency, boundary = _geometry(obj)
    tolerance = _tolerance(modifier, coordinates)
    result = []
    for source in plans:
        plan = copy.deepcopy(source)
        side = _classification(plan, coordinates, adjacency, boundary, tolerance)
        centered = side == "C"
        plan["mirror_side"] = side
        if centered:
            plan["centers"] = _centered_points(plan, coordinates, tolerance)
            plan["distances"] = _distances(plan["centers"])
            if any(b - a <= 1.0e-8 for a, b in zip(plan["distances"], plan["distances"][1:])):
                raise _error("The centered hair guide contains coincident sections; refine the source band.")
            result.append(plan)
        else:
            result.append(plan)
            reflected = copy.deepcopy(plan)
            reflected["signature"] = plan["signature"] + ":mirror"
            reflected["mirror_of"] = plan["signature"]
            reflected["mirror_side"] = "R" if side == "L" else "L"
            reflected["centers"] = tuple(Vector((-point.x, point.y, point.z)) for point in plan["centers"])
            # Reflection preserves the native arclength parameterization.
            result.append(reflected)
    return tuple(result)
