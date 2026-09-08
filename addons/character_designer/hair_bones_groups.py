"""Persistent strand captures and legacy group metadata for Hair Bones.

Only source-object metadata and explicit selection/guide actions are changed.
The partition survives reloads and coordinate edits; connectivity changes must
be recaptured deliberately. Bone generation consumes freshly computed plans.
"""

import hashlib
import json
import math
import uuid
from contextlib import contextmanager

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import hair_bones_topology as topology


GROUPS_KEY = "character_designer_hair_groups"
GUIDESOURCE_KEY = "character_designer_hair_group_source"
GUIDEGROUP_KEY = "character_designer_hair_group_id"
GUIDE_ID_KEY = "character_designer_hair_group_guide"


class HairGroupsError(ValueError):
    pass


def source_from_context(context):
    obj = context.active_object
    if obj is not None and obj.type == "MESH":
        return obj
    if obj is not None and obj.type == "CURVE":
        source = obj.get(GUIDESOURCE_KEY)
        if isinstance(source, bpy.types.Object) and source.type == "MESH":
            return source
    raise HairGroupsError("Select the source hair mesh or one of its group guides.")


@contextmanager
def _mesh(obj):
    if obj is None or obj.type != "MESH":
        raise HairGroupsError("Select a hair mesh.")
    owned = obj.mode != "EDIT"
    bm = bmesh.new() if owned else bmesh.from_edit_mesh(obj.data)
    if owned:
        bm.from_mesh(obj.data)
    try:
        for elements in (bm.verts, bm.edges, bm.faces):
            elements.ensure_lookup_table()
            elements.index_update()
        yield bm
    finally:
        if owned:
            bm.free()


def _topology(bm):
    value = (len(bm.verts),
             sorted(tuple(sorted(v.index for v in edge.verts)) for edge in bm.edges),
             sorted(tuple(v.index for v in face.verts) for face in bm.faces))
    return hashlib.sha256(repr(value).encode("ascii")).hexdigest()


def _read(obj, bm=None):
    raw = obj.get(GROUPS_KEY)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        assert data["version"] == 1
        assert isinstance(data["topology"], str)
        strands = data["strands"]
        groups = data["groups"]
        assert isinstance(strands, list) and isinstance(groups, list)
        signatures = [strand["signature"] for strand in strands]
        assert len(signatures) == len(set(signatures))
        assert all(isinstance(sig, str) and sig for sig in signatures)
        ids = [group["id"] for group in groups]
        assert len(ids) == len(set(ids))
        assert all(isinstance(value, str) and value for value in ids)
        flattened = [member for group in groups for member in group["members"]]
        assert sorted(flattened) == sorted(signatures)
        assert all(isinstance(group["name"], str) and group["members"] for group in groups)
        for strand in strands:
            layers = strand["layers"]
            assert len(layers) >= 2 and all(layer for layer in layers)
            indices = [i for layer in layers for i in layer]
            assert all(type(i) is int and i >= 0 for i in indices)
            assert len(indices) == len(set(indices))
            assert topology._signature(layers) == strand["signature"]
            assert set(indices) == set(strand["vertices"])
    except (AssertionError, TypeError, ValueError, KeyError) as exc:
        raise HairGroupsError("The saved hair groups are incomplete. Undo the last group change or restore the source mesh.") from exc
    if bm is None:
        with _mesh(obj) as mesh:
            current = _topology(mesh)
    else:
        current = _topology(bm)
    if data["topology"] != current:
        raise HairGroupsError("Hair mesh topology changed. Restore it or clear the saved groups and capture strands again.")
    return data


def _write(obj, data):
    if obj.library is not None:
        raise HairGroupsError("Make the source hair object local before changing its groups.")
    try:
        obj[GROUPS_KEY] = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    except (RuntimeError, TypeError) as exc:
        raise HairGroupsError("The source hair object cannot store group settings.") from exc


def _guides(obj, group):
    guide_id = group.get("guide_id")
    if not guide_id:
        return ()
    return tuple(item for item in bpy.data.objects
                 if item.type == "CURVE" and item.get(GUIDESOURCE_KEY) == obj
                 and item.get(GUIDEGROUP_KEY) == group["id"] and item.get(GUIDE_ID_KEY) == guide_id)


def _public_group(obj, group, *, require_unique_guide=True):
    guides = _guides(obj, group)
    if len(guides) > 1 and require_unique_guide:
        raise HairGroupsError("This hair group has duplicate guides. Remove the duplicate guide before generating bones.")
    return {"id": group["id"], "name": group["name"],
            "members": tuple(group["members"]), "guide": guides[0] if len(guides) == 1 else None}


def read_groups(obj):
    """Return list[{id, name, members: tuple[str], guide: Object | None}]."""
    data = _read(obj)
    return [] if data is None else [_public_group(obj, group) for group in data["groups"]]


def captured_strand_count(obj):
    """Count captured strands without requiring obsolete group guides."""
    data = _read(obj)
    return len(data["strands"]) if data else 0


def clear_groups(obj):
    """Explicitly clear only source partition settings; retain guides and rigs."""
    if obj.library is not None:
        raise HairGroupsError("Make the source hair object local before clearing its groups.")
    if GROUPS_KEY in obj:
        del obj[GROUPS_KEY]


def _native_plan(obj, bm, record, edges=None):
    layers = tuple(tuple(layer) for layer in record["layers"])
    if len(layers) < 2 or not all(layers):
        raise HairGroupsError("A hair strand needs at least two nonempty cross-sections.")
    if any(type(index) is not int or not 0 <= index < len(bm.verts)
           for layer in layers for index in layer):
        raise HairGroupsError("The saved hair strand indices are invalid. Clear its groups and capture strands again.")
    if edges is None:
        edges = tuple(topology._cd()._bm_edge_key(edge) for edge in bm.edges)
    checked = topology._strict_plan(obj, bm, layers, edges, explicit_direction=True)
    if checked is None or checked["signature"] != record["signature"]:
        raise HairGroupsError("A captured strand no longer has valid cross-sections. Restore its shape or capture it again.")
    # A captured root remains the root even after rotating or reshaping the mesh.
    centers = tuple(sum((bm.verts[i].co for i in layer), Vector()) / len(layer) for layer in layers)
    _distances(centers, "A captured hair strand")
    plan = dict(checked)
    plan.update(signature=record["signature"], layers=layers,
                vertices=tuple(sorted(i for layer in layers for i in layer)),
                centers=tuple(tuple(point) for point in centers),
                direction_confirmable=bool(record.get("direction_confirmable", False)),
                root_tip_rule=record.get("root_tip_rule", "CAPTURED_ROOT"))
    return plan


def _validate_overlap(plans):
    owners = {}
    for plan in plans:
        roots = set(plan["layers"][0])
        for index in plan["vertices"]:
            if index in owners and not (index in roots and owners[index]):
                raise HairGroupsError("Captured strands overlap outside their anchored roots. Adjust the strand selection before grouping.")
            owners[index] = index in roots


def capture_plans(obj, plans):
    """Add newly identified strands as singleton groups, preserving all groups."""
    plans = tuple(plans)
    if not plans:
        raise HairGroupsError("Select Hair Strands before capturing groups.")
    with _mesh(obj) as bm:
        data = _read(obj, bm) or {"version": 1, "topology": _topology(bm), "strands": [], "groups": []}
        edges = tuple(topology._cd()._bm_edge_key(edge) for edge in bm.edges)
        known = {item["signature"]: item for item in data["strands"]}
        for plan in plans:
            try:
                record = {"signature": plan["signature"],
                          "layers": [list(layer) for layer in plan["layers"]],
                          "vertices": list(plan["vertices"]),
                          "direction_confirmable": bool(plan.get("direction_confirmable", False)),
                          "root_tip_rule": plan.get("root_tip_rule", "CAPTURED_ROOT")}
                assert topology._signature(record["layers"]) == record["signature"]
                assert set(record["vertices"]) == {i for layer in record["layers"] for i in layer}
                _native_plan(obj, bm, record, edges)
            except (KeyError, TypeError, IndexError, AssertionError) as exc:
                raise HairGroupsError("The selected strand data is incomplete. Select Hair Strands again.") from exc
            if record["signature"] in known:
                continue
            known[record["signature"]] = record
            data["strands"].append(record)
            data["groups"].append({"id": uuid.uuid4().hex, "name": f"Strand {len(data['strands']):02d}",
                                   "members": [record["signature"]]})
        _validate_overlap(data["strands"])
        # Old group guides never determine independent strand generation.
        # Preserve them, including missing/duplicate legacy references.
        _write(obj, data)
    return [_public_group(obj, group, require_unique_guide=False) for group in data["groups"]]


def selected_members(context, obj):
    """Return captured strands touched by selected, unhidden exclusive vertices.

    A shared root alone selects no strand, preventing a scalp/root click from
    accidentally merging every strand that meets at that junction.
    """
    if context.active_object != obj or obj.mode != "EDIT":
        raise HairGroupsError("Enter Mesh Edit Mode on the source hair to choose group members.")
    with _mesh(obj) as bm:
        data = _read(obj, bm)
        if data is None:
            raise HairGroupsError("Select Hair Strands first to capture their boundaries.")
        selected = {v.index for v in bm.verts if v.select and not v.hide}
        owners = {}
        for strand in data["strands"]:
            for index in strand["vertices"]:
                owners.setdefault(index, []).append(strand["signature"])
        touched = {owners[index][0] for index in selected if len(owners.get(index, ())) == 1}
        return tuple(strand["signature"] for strand in data["strands"] if strand["signature"] in touched)


def group_selected(context, name="Hair Group"):
    obj = source_from_context(context)
    members = selected_members(context, obj)
    if len(members) < 2:
        raise HairGroupsError("Select a tip or inner vertices on at least two captured strands to group them.")
    data = _read(obj)
    chosen = set(members)
    for group in data["groups"]:
        if set(group["members"]) == chosen:
            return group["id"]
    for group in data["groups"]:
        group["members"] = [member for member in group["members"] if member not in chosen]
    data["groups"] = [group for group in data["groups"] if group["members"]]
    group_id = uuid.uuid4().hex
    data["groups"].append({"id": group_id, "name": str(name).strip() or "Hair Group", "members": list(members)})
    _write(obj, data)
    return group_id


def split_selected(context):
    obj = source_from_context(context)
    members = selected_members(context, obj)
    if not members:
        raise HairGroupsError("Select a tip or inner vertices on the strands to split out.")
    data = _read(obj)
    chosen = set(members)
    result = []
    new_members = []
    for group in data["groups"]:
        selected = chosen.intersection(group["members"])
        if len(group["members"]) == 1:
            if selected:
                result.append(group["id"])
            continue
        new_members.extend(member for member in group["members"] if member in selected)
        group["members"] = [member for member in group["members"] if member not in selected]
    data["groups"] = [group for group in data["groups"] if group["members"]]
    order = {record["signature"]: index + 1 for index, record in enumerate(data["strands"])}
    for member in new_members:
        group_id = uuid.uuid4().hex
        data["groups"].append({"id": group_id, "name": f"Strand {order[member]:02d}", "members": [member]})
        result.append(group_id)
    _write(obj, data)
    return tuple(result)


def _find_group(data, group_id):
    if data is not None:
        for group in data["groups"]:
            if group["id"] == group_id:
                return group
    raise HairGroupsError("This hair group no longer exists. Choose a current group.")


def select_group(context, group_id):
    obj = source_from_context(context)
    if context.active_object != obj or obj.mode != "EDIT":
        raise HairGroupsError("Enter Mesh Edit Mode on the source hair to select a group.")
    with _mesh(obj) as bm:
        data = _read(obj, bm)
        group = _find_group(data, group_id)
        chosen = set(group["members"])
        vertices = {i for strand in data["strands"] if strand["signature"] in chosen for i in strand["vertices"]}
        if any(bm.verts[i].hide for i in vertices):
            raise HairGroupsError("Unhide this group's source vertices before selecting it.")
        for face in bm.faces:
            face.select = not face.hide and all(v.index in vertices for v in face.verts)
        for edge in bm.edges:
            edge.select = not edge.hide and all(v.index in vertices for v in edge.verts)
        for vertex in bm.verts:
            vertex.select = vertex.index in vertices
        bm.select_history.clear()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return _public_group(obj, group)


def _distances(points, label):
    points = tuple(Vector(point) for point in points)
    if len(points) < 2 or any(not math.isfinite(v) for point in points for v in point):
        raise HairGroupsError(f"{label} needs at least two finite points.")
    distances = [0.0]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1] + (b - a).length)
    if distances[-1] <= 1.0e-8:
        raise HairGroupsError(f"{label} has no usable length. Move its tip away from its root.")
    return points, tuple(distances)


def _sample(points, count):
    points, distances = _distances(points, "The hair guide")
    result = []
    segment = 0
    for index in range(count):
        target = distances[-1] * index / (count - 1)
        while segment < len(points) - 2 and distances[segment + 1] < target:
            segment += 1
        extent = distances[segment + 1] - distances[segment]
        factor = (target - distances[segment]) / extent if extent > 1.0e-12 else 0.0
        result.append(points[segment].lerp(points[segment + 1], factor))
    result[0], result[-1] = points[0].copy(), points[-1].copy()
    return tuple(result)


def _average(members, count):
    sampled = tuple(_sample(member["centers"], count) for member in members)
    result = tuple(sum((points[index] for points in sampled), Vector()) / len(sampled) for index in range(count))
    _distances(result, "The averaged group guide")
    return result


def _editable_collections(context):
    """Collections reached through an unhidden, unexcluded, selectable path."""
    visible = set()

    def visit(layer, blocked=False):
        collection = layer.collection
        blocked = (blocked or layer.exclude or layer.hide_viewport
                   or collection.hide_viewport or collection.hide_select)
        if not blocked:
            visible.add(collection)
        for child in layer.children:
            visit(child, blocked)

    visit(context.view_layer.layer_collection)
    return visible


def guide_in_editable_collection(context, guide):
    """Check guide collection access without changing visibility or selection."""
    visible = _editable_collections(context)
    return any(collection in visible for collection in guide.users_collection)


def _new_guide_collection(context, obj):
    visible = _editable_collections(context)
    for collection in obj.users_collection:
        if collection in visible:
            return collection
    if context.scene.collection in visible:
        return context.scene.collection
    raise HairGroupsError("Reveal and unlock a collection in this View Layer before creating a hair guide.")


def build_plans(context, *, mode="PER_STRAND", selected_only=False, source=None):
    """Rebuild independent strands, preserving old grouping metadata as data."""
    if mode != "PER_STRAND":
        raise HairGroupsError("Grouped shared-chain generation is retired; generate independent strands instead.")
    obj = source if source is not None else source_from_context(context)
    # Consume pending source transforms before validating world-space direction.
    context.view_layer.update()
    with _mesh(obj) as bm:
        data = _read(obj, bm)
        if data is None:
            if obj.mode == "EDIT" and context.active_object == obj:
                try:
                    source, plans = topology.selected_strands(context)
                except topology.HairTopologyError as exc:
                    raise HairGroupsError(str(exc)) from exc
                return source, plans
            raise HairGroupsError("Select Hair Strands first to save their boundaries.")
        edges = tuple(topology._cd()._bm_edge_key(edge) for edge in bm.edges)
        members = {record["signature"]: _native_plan(obj, bm, record, edges) for record in data["strands"]}
        _validate_overlap(members.values())
        selected = set(selected_members(context, obj)) if selected_only else set(members)
        if not selected:
            raise HairGroupsError("Select at least one captured strand.")
        return obj, tuple(plan for signature, plan in members.items() if signature in selected)


def create_group_guide(context, group_id, point_count=5):
    """Create one editable source-local Poly guide without changing selection/mode."""
    obj = source_from_context(context)
    if type(point_count) is not int or not 2 <= point_count <= 64:
        raise HairGroupsError("Use between 2 and 64 points for a group guide.")
    context.view_layer.update()
    with _mesh(obj) as bm:
        data = _read(obj, bm)
        group = _find_group(data, group_id)
        existing = _guides(obj, group)
        if len(existing) > 1:
            raise HairGroupsError("Remove the duplicate group guide first.")
        if existing:
            return existing[0]
        chosen = set(group["members"])
        members = tuple(_native_plan(obj, bm, record) for record in data["strands"] if record["signature"] in chosen)
        points = _average(members, point_count)
        collection = _new_guide_collection(context, obj)
    curve = guide = None
    try:
        curve = bpy.data.curves.new(f"Hair Guide {group['name']}", "CURVE")
        curve.dimensions = "3D"
        spline = curve.splines.new("POLY")
        spline.points.add(point_count - 1)
        for point, coordinate in zip(spline.points, points):
            point.co = (*coordinate, 1.0)
        guide = bpy.data.objects.new(f"Hair Guide {group['name']}", curve)
        collection.objects.link(guide)
        guide.parent = obj
        guide.matrix_parent_inverse = Matrix.Identity(4)
        guide.matrix_basis = Matrix.Identity(4)
        guide.show_in_front = True
        guide.hide_render = True
        guide.display_type = "WIRE"
        guide_id = uuid.uuid4().hex
        guide[GUIDESOURCE_KEY] = obj
        guide[GUIDEGROUP_KEY] = group_id
        guide[GUIDE_ID_KEY] = guide_id
        group["guide_id"] = guide_id
        # Return a guide whose parent transform is already current, including
        # callers that immediately generate without leaving Mesh Edit Mode.
        context.view_layer.update()
        _write(obj, data)
    except Exception:
        if guide is not None:
            bpy.data.objects.remove(guide, do_unlink=True)
        if curve is not None and curve.users == 0:
            bpy.data.curves.remove(curve)
        raise
    return guide
