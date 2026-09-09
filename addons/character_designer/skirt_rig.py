"""Native, removable skirt controls fitted to an unbound quad skirt.

Each skirt owns a separate armature, hooked wire curves, three control rings,
and matched manual / physics / deform chains.  All evaluation is Blender-native;
no frame handler, embedded script, or dependency on a particular character name.
"""

import hashlib
import json
import math
import uuid

import bmesh
import bpy
from mathutils import Matrix, Vector

from .skirt_topology import analyze_skirt, sample_fit


RECORD_KEY = "character_designer_skirt_v1"
OWNER_KEY = "character_designer_skirt_owner"
RIG_KEY = "character_designer_skirt_armature"
SOURCE_KEY = "character_designer_skirt_source"
PARENT_KEY = "character_designer_skirt_original_parent"
ATTACHMENT_BACKUP_KEY = "character_designer_skirt_attachment_before_update_v1"
ATTACHMENT_PARENT_KEY = "character_designer_skirt_attachment_previous_parent"
BONE_COLLECTION_NAME = "Skirt"


class SkirtRigError(ValueError):
    """An artist-facing validation or rolled-back construction error."""


def _matrix_values(matrix):
    return [list(row) for row in matrix]


def _activate(context, obj, mode="OBJECT"):
    context.view_layer.update()
    if context.view_layer.objects.get(obj.name) is not obj:
        raise SkirtRigError("The skirt or its rig is excluded from the current view layer.")
    current = context.view_layer.objects.active
    if current is not None and current.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for candidate in context.view_layer.objects:
        if candidate.select_get():
            candidate.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if mode != "OBJECT":
        bpy.ops.object.mode_set(mode=mode)


def _context_state(context):
    active = context.view_layer.objects.active
    return (active, active.mode if active else "OBJECT",
            [obj for obj in context.view_layer.objects if obj.select_get()])


def _restore_context(context, state):
    active, mode, selected = state
    current = context.view_layer.objects.active
    if current and current.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for candidate in context.view_layer.objects:
        candidate.select_set(candidate in selected)
    context.view_layer.objects.active = active
    if active and mode != "OBJECT":
        bpy.ops.object.mode_set(mode=mode)


def write_record(obj, record):
    """Persist service metadata; physics extends the same ownership record."""
    obj[RECORD_KEY] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def read_record(obj):
    if obj is None or RECORD_KEY not in obj:
        return None
    try:
        record = json.loads(obj[RECORD_KEY])
        if record["version"] != 1 or not record["owner"] or not record["chains"]:
            raise ValueError()
        rig = obj.get(RIG_KEY)
        if rig is None or rig.type != "ARMATURE" or rig.get(OWNER_KEY) != record["owner"]:
            raise SkirtRigError("This skirt's generated rig is missing. Undo its removal before continuing.")
        record["rig"] = rig.name
        record["source"] = obj.name
        for chain in record["chains"]:
            if any(name not in rig.data.bones for layer in ("manual", "def", "phys") for name in chain[layer]):
                raise SkirtRigError("Generated skirt bones were renamed or removed. Undo that change first.")
        return record
    except (TypeError, ValueError, KeyError) as error:
        if isinstance(error, SkirtRigError):
            raise
        raise SkirtRigError("The skirt setup record is unreadable. Undo the previous change.") from None


def _bone_collection_layout(record):
    controls = record["controls"]
    control_names = {controls[level] for level in ("waist", "mid", "hem")}
    control_names.update(name for entry in controls["chains"] for name in entry.values())
    deform_names = {name for chain in record["chains"] for name in chain["def"]}
    mechanism_names = {name for chain in record["chains"] for layer in ("manual", "phys") for name in chain[layer]}
    return control_names, deform_names, mechanism_names


def migrate_skirt_bone_collections(armature):
    """Collapse a verified generated skirt's legacy display groups only.

    Rig objects, bone transforms, constraints, weights and unrelated collections
    are untouched. Non-owned armatures are ignored; edited ownership is rejected
    before any collection or visibility changes.
    """
    if armature is None or armature.type != "ARMATURE" or not armature.get(OWNER_KEY):
        return False
    source = armature.get(SOURCE_KEY)
    if source is None or source.type != "MESH" or source.get(RIG_KEY) is not armature:
        raise SkirtRigError("The skirt rig's source ownership is incomplete.")
    record = read_record(source)
    if (record is None or source.get(OWNER_KEY) != record["owner"]
            or armature.data.get(OWNER_KEY) != record["owner"] or armature.data.users != 1):
        raise SkirtRigError("The skirt rig's bone ownership is incomplete or shared.")
    if armature.mode == "EDIT":
        raise SkirtRigError("Leave Edit Mode before organizing skirt bone collections.")
    try:
        controls, deform, mechanism = _bone_collection_layout(record)
    except (KeyError, TypeError):
        raise SkirtRigError("The skirt control record is incomplete.") from None
    owned_names = controls | deform | mechanism
    if owned_names != set(armature.data.bones.keys()):
        raise SkirtRigError("The skirt rig's bones were edited; restore its generated layout before organizing it.")
    collections = armature.data.collections
    grouped = [item for item in armature.data.collections_all if item.get(OWNER_KEY) == record["owner"]]
    if len(grouped) > 1:
        raise SkirtRigError("The skirt bone collection ownership is ambiguous.")
    target = grouped[0] if grouped else None
    if target is not None:
        if set(target.bones.keys()) != owned_names or target.children:
            raise SkirtRigError("The skirt bone collection was edited; restore it before organizing it.")
        return False
    legacy = []
    for title, names in (("Skirt Controls", controls), ("Skirt Deform", deform),
                         ("Skirt Mechanism", mechanism)):
        item = collections.get(title)
        if (item is None or item.get(OWNER_KEY) or item.children or item.parent
                or set(item.bones.keys()) != names):
            raise SkirtRigError("The legacy skirt bone collections were edited; restore them before organizing them.")
        legacy.append(item)
    target = collections.new(BONE_COLLECTION_NAME)
    target[OWNER_KEY] = record["owner"]
    target.is_visible = legacy[0].is_visible
    for name in owned_names:
        bone = armature.data.bones[name]
        target.assign(bone)
        if name not in controls:
            bone.hide = True
    for item in legacy:
        collections.remove(item)
    return True


def find_source(context):
    """Resolve a selected source, control rig, wire, or physics helper."""
    active = context.view_layer.objects.active
    if active and active.type == "MESH" and not active.get(SOURCE_KEY):
        return active
    if active:
        source = active.get(SOURCE_KEY)
        if source and source.type == "MESH":
            return source
        if RECORD_KEY in active:
            return active
    candidates = [obj for obj in context.selected_objects if obj.type == "MESH" and not obj.get(SOURCE_KEY)]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _anchor_bone(armature, requested=""):
    if requested:
        if requested not in armature.data.bones:
            raise SkirtRigError(f"The character rig has no bone named '{requested}'.")
        return requested
    names = {"hips", "pelvis", "hip", "def-hips", "def-pelvis", "mixamorig:hips", "mixamorig1:hips"}
    matching = [bone.name for bone in armature.data.bones if bone.name.casefold() in names]
    if len(matching) == 1:
        return matching[0]
    raise SkirtRigError("Choose one Attachment Bone, usually the character's central Hips bone.")


def _find_character(context, obj, armature, parent_bone):
    if armature is None and obj.parent and obj.parent.type == "ARMATURE":
        armature = obj.parent
    if armature is None:
        selected = [candidate for candidate in context.selected_objects
                    if candidate.type == "ARMATURE" and not candidate.get(OWNER_KEY)]
        if len(selected) == 1:
            armature = selected[0]
    if armature is None:
        candidates = []
        for candidate in context.view_layer.objects:
            if candidate.type != "ARMATURE" or candidate.get(OWNER_KEY):
                continue
            try:
                candidates.append((candidate, _anchor_bone(candidate, parent_bone)))
            except SkirtRigError:
                continue
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise SkirtRigError("Several character rigs have a Hips attachment candidate. Choose the Main Rig.")
        if parent_bone:
            raise SkirtRigError("Choose the Main Rig before specifying its Attachment Bone.")
        return None, ""
    if armature.type != "ARMATURE" or armature.get(OWNER_KEY):
        raise SkirtRigError("Choose the character armature, not a generated skirt rig.")
    if context.view_layer.objects.get(armature.name) is not armature:
        raise SkirtRigError("The selected character rig must be linked into the current view layer.")
    return armature, _anchor_bone(armature, parent_bone)


def has_attachment_backup(source):
    return source is not None and ATTACHMENT_BACKUP_KEY in source


def attachment_status(source):
    """Report the actual live parent; legacy record names are only metadata."""
    record = read_record(source)
    if not record:
        return None
    rig = source[RIG_KEY]
    parent = rig.parent
    character = parent if parent and parent.type == "ARMATURE" else None
    attached = bool(character and rig.parent_type == "BONE" and rig.parent_bone in character.data.bones)
    return {"rig": rig, "character": character, "parent": parent,
            "parent_type": rig.parent_type, "parent_bone": rig.parent_bone,
            "attached": attached, "has_backup": has_attachment_backup(source),
            "physics": bool(record.get("physics"))}


def _attachment_state(rig):
    return {"parent": rig.parent.name if rig.parent else "", "parent_type": rig.parent_type,
            "parent_bone": rig.parent_bone,
            "matrix_parent_inverse": _matrix_values(rig.matrix_parent_inverse)}


def _valid_matrix(matrix):
    return (all(math.isfinite(value) for row in matrix for value in row)
            and abs(matrix.determinant()) > 1.0e-12)


def _attachment_preflight(context, source, record, parent, parent_type, parent_bone):
    rig = source[RIG_KEY]
    if (source.library or source.override_library or rig.library or rig.override_library
            or rig.data.library or rig.data.users != 1
            or rig.get(SOURCE_KEY) is not source or rig.data.get(OWNER_KEY) != record["owner"]):
        raise SkirtRigError("The skirt attachment must have a local, unshared generated rig and valid ownership.")
    if source.mode == "EDIT" or rig.mode == "EDIT":
        raise SkirtRigError("Leave Edit Mode before changing the skirt attachment.")
    if any(context.view_layer.objects.get(obj.name) is not obj for obj in (source, rig)):
        raise SkirtRigError("Include the skirt and its rig in the current view layer before changing attachment.")
    if rig.constraints:
        raise SkirtRigError("The skirt rig has object constraints. Resolve them before changing its attachment.")
    if not _valid_matrix(rig.matrix_world) or not _valid_matrix(rig.matrix_basis):
        raise SkirtRigError("The skirt rig has an invalid or zero-scale transform. Restore a nonzero scale first.")
    if parent is None:
        return
    if (parent.library or parent.override_library or (parent.type == "ARMATURE"
            and (parent.data.library or parent.data.users != 1))):
        raise SkirtRigError("Choose a local Main Rig with unshared armature data for the attachment.")
    if context.view_layer.objects.get(parent.name) is not parent:
        raise SkirtRigError("The attachment target must be in the current view layer.")
    if parent_type not in {"OBJECT", "BONE"}:
        raise SkirtRigError("Only object and bone attachments can be restored.")
    if parent_type == "BONE" and (parent.type != "ARMATURE" or parent_bone not in parent.data.bones):
        raise SkirtRigError(f"The attachment target has no bone named '{parent_bone}'. Choose a valid Attachment Bone.")
    if parent.mode == "EDIT":
        raise SkirtRigError("Leave the Main Rig's Edit Mode before changing attachment.")
    if parent_type == "BONE" and not _valid_matrix(parent.pose.bones[parent_bone].matrix):
        raise SkirtRigError("The Attachment Bone has a zero-scale or invalid pose.")
    owned = {candidate for candidate in bpy.data.objects if candidate.get(OWNER_KEY) == record["owner"]}
    current, visited = parent, set()
    while current:
        if current in owned or current is source or current in visited:
            raise SkirtRigError("This attachment would create a parenting cycle. Choose an independent Main Rig.")
        visited.add(current)
        constraints = list(current.constraints)
        if current.type == "ARMATURE":
            constraints.extend(constraint for bone in current.pose.bones for constraint in bone.constraints)
        if any(getattr(constraint, "target", None) in owned for constraint in constraints):
            raise SkirtRigError("The target rig depends on this skirt's controls. Resolve that dependency before attaching.")
        if current.animation_data:
            for driver in current.animation_data.drivers:
                if any(target.id in owned for variable in driver.driver.variables for target in variable.targets):
                    raise SkirtRigError("The target rig has a driver depending on this skirt. Resolve it before attaching.")
        current = current.parent
    if not _valid_matrix(parent.matrix_world):
        raise SkirtRigError("The attachment target has an invalid or zero-scale transform.")


def _set_attachment(rig, parent, state):
    rig.parent = parent
    rig.parent_type = state["parent_type"] if parent else "OBJECT"
    rig.parent_bone = state["parent_bone"] if parent else ""
    rig.matrix_parent_inverse = Matrix(state["matrix_parent_inverse"])


def _write_attachment_record(source, record):
    rig = source[RIG_KEY]
    record["character"] = rig.parent.name if rig.parent and rig.parent.type == "ARMATURE" else ""
    record["parent_bone"] = rig.parent_bone if rig.parent_type == "BONE" else ""
    write_record(source, record)


def _require_no_physics(record):
    if record.get("physics"):
        raise SkirtRigError("This skirt has Physics + Colliders linked to its current attachment. "
                            "Keep this setup; use a separate skirt setup without physics to change attachment. "
                            "Clearing the cache alone does not retarget colliders.")


def update_attachment(context, source, armature, parent_bone=""):
    """Explicitly reconnect without changing any pose/object animation channels.

    Only the rig's parent and parent inverse change. The first successful update
    retains a blend-persistent original attachment until Restore is used.
    """
    record = read_record(source)
    if not record:
        raise SkirtRigError("Create the skirt setup before updating its attachment.")
    if armature is None or armature.type != "ARMATURE" or armature.get(OWNER_KEY):
        raise SkirtRigError("Choose the Main Rig and one Attachment Bone, usually Hips.")
    bone = _anchor_bone(armature, parent_bone)
    rig = source[RIG_KEY]
    context.view_layer.update()
    _attachment_preflight(context, source, record, armature, "BONE", bone)
    if rig.parent is armature and rig.parent_type == "BONE" and rig.parent_bone == bone:
        return record
    _require_no_physics(record)
    previous, previous_parent = _attachment_state(rig), rig.parent
    old_record = source[RECORD_KEY]
    old_backup = source.get(ATTACHMENT_BACKUP_KEY)
    old_backup_parent = source.get(ATTACHMENT_PARENT_KEY)
    world, basis = rig.matrix_world.copy(), rig.matrix_basis.copy()
    try:
        rig.parent, rig.parent_type, rig.parent_bone = armature, "BONE", bone
        rig.matrix_parent_inverse = Matrix.Identity(4)
        context.view_layer.update()
        # Ask Blender for the effective parent frame, including bone-tail and
        # relative-parent conventions. No transform channels are decomposed.
        parent_frame = rig.matrix_world @ basis.inverted()
        if not _valid_matrix(parent_frame):
            raise SkirtRigError("The Attachment Bone has a zero-scale or invalid pose.")
        rig.matrix_parent_inverse = parent_frame.inverted() @ world @ basis.inverted()
        context.view_layer.update()
        if max(abs(rig.matrix_world[i][j] - world[i][j]) for i in range(4) for j in range(4)) > 1.0e-5:
            raise SkirtRigError("The skirt could not keep its current placement; attachment was restored.")
        if old_backup is None:
            source[ATTACHMENT_BACKUP_KEY] = json.dumps(previous, separators=(",", ":"))
            if previous_parent:
                source[ATTACHMENT_PARENT_KEY] = previous_parent
        _write_attachment_record(source, record)
        return record
    except Exception as error:
        _set_attachment(rig, previous_parent, previous)
        source[RECORD_KEY] = old_record
        for key, value in ((ATTACHMENT_BACKUP_KEY, old_backup), (ATTACHMENT_PARENT_KEY, old_backup_parent)):
            if value is None:
                if key in source:
                    del source[key]
            else:
                source[key] = value
        context.view_layer.update()
        if isinstance(error, SkirtRigError):
            raise
        raise SkirtRigError(f"Attachment update was rolled back: {error}") from error


def restore_attachment(context, source):
    """Restore the first saved parent and inverse, retaining authored channels.

    This restores the original animation space. If that original parent moved
    meanwhile, the skirt follows its current pose rather than keeping an offset.
    """
    record = read_record(source)
    if not record or not has_attachment_backup(source):
        raise SkirtRigError("This skirt has no previous attachment to restore.")
    try:
        original = json.loads(source[ATTACHMENT_BACKUP_KEY])
        parent = source.get(ATTACHMENT_PARENT_KEY)
        if original["parent"] and parent is None:
            raise SkirtRigError("The previous attachment object is missing. Restore that object before restoring attachment.")
        if not _valid_matrix(Matrix(original["matrix_parent_inverse"])):
            raise ValueError("invalid parent inverse")
        _attachment_preflight(context, source, record, parent, original["parent_type"], original["parent_bone"])
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, SkirtRigError):
            raise
        raise SkirtRigError("The saved skirt attachment is unreadable. Undo its modification before restoring.") from error
    _require_no_physics(record)
    rig = source[RIG_KEY]
    previous, previous_parent = _attachment_state(rig), rig.parent
    old_record = source[RECORD_KEY]
    try:
        _set_attachment(rig, parent, original)
        context.view_layer.update()
        _write_attachment_record(source, record)
    except Exception as error:
        _set_attachment(rig, previous_parent, previous)
        source[RECORD_KEY] = old_record
        context.view_layer.update()
        raise SkirtRigError(f"Attachment restore was rolled back: {error}") from error
    for key in (ATTACHMENT_BACKUP_KEY, ATTACHMENT_PARENT_KEY):
        if key in source:
            del source[key]
    return record


def _owned(obj, source, owner):
    obj[OWNER_KEY] = owner
    obj[SOURCE_KEY] = source
    if getattr(obj, "data", None) is not None:
        obj.data[OWNER_KEY] = owner
    return obj


def _collection(parent, name, owner):
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    collection[OWNER_KEY] = owner
    return collection


def _new_curve(name, points, collection, rig, source, owner, cyclic=False, smooth=True):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.resolution_u = 12
    spline = data.splines.new("NURBS" if smooth else "POLY")
    spline.points.add(len(points) - 1)
    for point, coordinate in zip(spline.points, points):
        point.co = (*coordinate, 1.0)
    if smooth:
        spline.order_u = min(3, len(points))
        spline.use_endpoint_u = not cyclic
    spline.use_cyclic_u = cyclic
    obj = _owned(bpy.data.objects.new(name, data), source, owner)
    collection.objects.link(obj)
    obj.parent = rig
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.matrix_basis = Matrix.Identity(4)
    obj.hide_render = True
    obj.hide_select = True
    obj.show_in_front = True
    obj.show_wire = True
    obj.color = (0.14, 0.65, 0.92, 1.0)
    return obj


def _hook(curve, rig, bone_name, indices):
    modifier = curve.modifiers.new("Control " + bone_name, "HOOK")
    modifier.object = rig
    modifier.subtarget = bone_name
    modifier.vertex_indices_set(indices)
    modifier.strength = 1.0
    modifier.falloff_type = "NONE"
    modifier.matrix_inverse = (rig.matrix_world @ rig.pose.bones[bone_name].matrix).inverted() @ curve.matrix_world
    return modifier


def _shape(name, points, edges, collection, rig, bone_name, source, owner):
    # Custom shape vertices are in the bone's unscaled local frame.
    matrix = rig.data.bones[bone_name].matrix_local.inverted()
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([matrix @ Vector(point) for point in points], edges, [])
    mesh.update()
    obj = _owned(bpy.data.objects.new(name, mesh), source, owner)
    collection.objects.link(obj)
    obj.hide_render = True
    obj.hide_set(True)
    pose = rig.pose.bones[bone_name]
    pose.custom_shape = obj
    pose.use_custom_shape_bone_size = False
    pose.custom_shape_wire_width = 2.0
    return obj


def _restore_parent(source, record, original_parent=None):
    state = record["original"]
    original_parent = original_parent or source.get(PARENT_KEY)
    source.parent = original_parent
    source.parent_type = state["parent_type"] if original_parent else "OBJECT"
    source.parent_bone = state["parent_bone"] if original_parent else ""
    source.matrix_parent_inverse = Matrix(state["matrix_parent_inverse"])
    source.matrix_basis = Matrix(state["matrix_basis"])
    if state["parent"] and original_parent is None:
        source.matrix_world = Matrix(state["matrix_world"])


def _purge_owned(source, owner):
    for obj in list(bpy.data.objects):
        if obj is not source and obj.get(OWNER_KEY) == owner:
            bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        if collection.get(OWNER_KEY) == owner:
            if not collection.objects and not collection.children:
                bpy.data.collections.remove(collection)
    # Children may precede their parents in the datablock collection.
    for collection in list(bpy.data.collections):
        if collection.get(OWNER_KEY) == owner and not collection.objects and not collection.children:
            bpy.data.collections.remove(collection)
    for domain in (bpy.data.curves, bpy.data.meshes, bpy.data.armatures):
        for datablock in list(domain):
            if datablock.get(OWNER_KEY) == owner and datablock.users == 0:
                domain.remove(datablock)


def _clear_source_properties(source):
    for key in (RECORD_KEY, RIG_KEY, OWNER_KEY, PARENT_KEY, ATTACHMENT_BACKUP_KEY, ATTACHMENT_PARENT_KEY):
        if key in source:
            del source[key]


def _validate_source(obj):
    if obj is None or obj.type != "MESH":
        raise SkirtRigError("Select the skirt mesh before building its controls.")
    if any(key in obj for key in (RIG_KEY, OWNER_KEY, PARENT_KEY)):
        raise SkirtRigError("The skirt has incomplete setup ownership. Undo the previous change before rebuilding.")
    if obj.library or obj.override_library or obj.data.library or obj.data.users > 1:
        raise SkirtRigError("Make the skirt and its mesh local and single-user before setup.")
    if obj.constraints:
        raise SkirtRigError("The skirt has object constraints. Apply or remove them before setup to avoid double motion.")
    if obj.animation_data and (obj.animation_data.action or obj.animation_data.nla_tracks or obj.animation_data.drivers):
        raise SkirtRigError("The skirt object is animated. Transfer that motion to the character before setup.")
    allowed = {"SUBSURF", "SOLIDIFY", "BEVEL", "SMOOTH", "CORRECTIVE_SMOOTH", "WEIGHTED_NORMAL", "TRIANGULATE"}
    unsupported = [modifier.name for modifier in obj.modifiers if modifier.type not in allowed]
    if unsupported:
        raise SkirtRigError("Apply the skirt's existing deformation or topology modifiers, or use an unbound copy: "
                            + ", ".join(unsupported))
    if obj.parent and obj.parent_type not in {"OBJECT", "BONE"}:
        raise SkirtRigError("Remove vertex parenting from the skirt before setup.")
    if any(not math.isfinite(value) for row in obj.matrix_world for value in row) or abs(obj.matrix_world.determinant()) < 1.0e-12:
        raise SkirtRigError("The skirt transform is singular or invalid. Restore a nonzero scale.")


def _check_existing_geometry(obj, record):
    """Compare undeformed source geometry without assuming its posed world axis."""
    if obj.mode == "EDIT":
        mesh = bmesh.from_edit_mesh(obj.data).copy()
        try:
            mesh.verts.index_update()
            vertices = [tuple(vertex.co) for vertex in mesh.verts]
            edges = [tuple(sorted(vertex.index for vertex in edge.verts)) for edge in mesh.edges]
            faces = [tuple(vertex.index for vertex in face.verts) for face in mesh.faces]
        finally:
            mesh.free()
    else:
        vertices = [tuple(vertex.co) for vertex in obj.data.vertices]
        edges = [tuple(sorted(edge.vertices)) for edge in obj.data.edges]
        faces = [tuple(face.vertices) for face in obj.data.polygons]
    payload = (len(vertices), tuple(sorted(edges)), tuple(sorted(tuple(sorted(face)) for face in faces)))
    signature = hashlib.sha256(repr(payload).encode("ascii")).hexdigest()
    original = record["fit"]["vertices"]
    if (signature != record["fit"]["signature"] or len(original) != len(vertices)
            or any(abs(a - b) > 1.0e-6 for current, saved in zip(vertices, original) for a, b in zip(current, saved))):
        raise SkirtRigError("The skirt mesh changed after setup. Remove its setup and rebuild to refit the wire and weights.")
    rig = obj[RIG_KEY]
    if not any(modifier.type == "ARMATURE" and modifier.object is rig for modifier in obj.modifiers):
        raise SkirtRigError("The skirt's generated Armature modifier is missing. Undo its removal first.")
    if any(name not in obj.vertex_groups for name in record["groups"]):
        raise SkirtRigError("Generated skirt weight groups were renamed or removed. Undo that change first.")
    for chain in record["chains"]:
        curve = bpy.data.objects.get(chain["curve"])
        if curve is None or curve.get(OWNER_KEY) != record["owner"]:
            raise SkirtRigError("A generated skirt wire was renamed or removed. Undo that change first.")


def build_skirt(context, obj, chain_count=8, segment_count=4, armature=None, parent_bone=""):
    existing = read_record(obj)
    if existing:
        if existing["chain_count"] != chain_count or existing["segment_count"] != segment_count:
            raise SkirtRigError("Remove the existing setup before changing its chain or segment count.")
        _check_existing_geometry(obj, existing)
        select_controls(context, obj)
        return existing
    _validate_source(obj)
    plan = analyze_skirt(obj, chain_count=chain_count, segment_count=segment_count)
    character, anchor = _find_character(context, obj, armature, parent_bone)
    saved_context = _context_state(context)
    original_parent = obj.parent
    owner = uuid.uuid4().hex
    prefix = "SK_" + obj.name[:16] + "_" + owner[:6]
    record = {
        "version": 1, "owner": owner, "source": obj.name, "rig": "",
        "chain_count": chain_count, "segment_count": segment_count,
        "fit": {key: value for key, value in plan.items() if key != "vertex_weights"},
        "character": character.name if character else "", "parent_bone": anchor,
        "controls": {"waist": prefix + "_Waist", "mid": prefix + "_Mid", "hem": prefix + "_Hem", "chains": []},
        "chains": [], "cage": [], "owned_objects": [], "owned_collections": [], "groups": [], "modifier": "",
        "original": {"parent": original_parent.name if original_parent else "",
                     "parent_type": obj.parent_type, "parent_bone": obj.parent_bone,
                     "matrix_parent_inverse": _matrix_values(obj.matrix_parent_inverse),
                     "matrix_basis": _matrix_values(obj.matrix_basis),
                     "matrix_world": _matrix_values(obj.matrix_world),
                     "active_group": obj.vertex_groups.active_index},
        "warnings": plan.get("warnings", []),
    }
    try:
        _activate(context, obj)
        world = obj.matrix_world.copy()
        host_collection = obj.users_collection[0] if obj.users_collection else context.scene.collection
        collection = _collection(host_collection, "Skirt | " + obj.name, owner)
        helpers = _collection(collection, "Skirt wire and shapes | " + owner[:6], owner)
        record["owned_collections"] = [collection.name, helpers.name]
        data = bpy.data.armatures.new(prefix + "_Rig")
        rig = _owned(bpy.data.objects.new(prefix + "_Rig", data), obj, owner)
        collection.objects.link(rig)
        rig.matrix_world = world
        rig.show_in_front = True
        data.display_type = "OCTAHEDRAL"
        record["rig"] = rig.name
        obj[RIG_KEY] = rig
        obj[OWNER_KEY] = owner
        if original_parent:
            obj[PARENT_KEY] = original_parent
        rig["physics_influence"] = 0.0
        rig.id_properties_ui("physics_influence").update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0,
                                                        description="Add simulated bone motion to the manual skirt controls")
        rig["skirt_guide"] = "Pose the waist, mid and hem rings; small diamonds shape each wire rib."
        controls = record["controls"]
        centers = [Vector(plan["waist_center"]),
                   (Vector(plan["waist_center"]) + Vector(plan["hem_center"])) * 0.5,
                   Vector(plan["hem_center"])]
        local_up = world.inverted().to_3x3() @ Vector((0.0, 0.0, plan["height_world"] * 0.07))
        control_names = []
        mechanism_names = []
        deform_names = []
        _activate(context, rig, "EDIT")

        def add_bone(name, head, tail, parent=None, connected=False, deform=False):
            bone = data.edit_bones.new(name)
            bone.head, bone.tail = head, tail
            bone.use_deform = deform
            if parent:
                bone.parent = data.edit_bones[parent]
                bone.use_connect = connected
            return bone

        for level, center in zip(("waist", "mid", "hem"), centers):
            name = controls[level]
            add_bone(name, center, center + local_up, controls["waist"] if level != "waist" else None,
                     deform=level == "waist")
            control_names.append(name)
        for chain_index, nodes in enumerate(plan["chains"]):
            angle = math.tau * chain_index / chain_count
            entry = {"mid": f"{prefix}_Mid_{chain_index + 1:02}", "hem": f"{prefix}_Hem_{chain_index + 1:02}"}
            controls["chains"].append(entry)
            for level, t in (("mid", 0.5), ("hem", 1.0)):
                point = Vector(sample_fit(plan, t, angle))
                add_bone(entry[level], point, point + local_up * 0.7, controls[level])
                control_names.append(entry[level])
            chain = {"manual": [], "phys": [], "def": [], "curve": ""}
            for layer, marker in (("manual", "MCH"), ("phys", "PHYS"), ("def", "DEF")):
                for segment in range(segment_count):
                    name = f"{prefix}_{marker}_{chain_index + 1:02}_{segment + 1:02}"
                    parent = chain[layer][-1] if segment else controls["waist"]
                    add_bone(name, Vector(nodes[segment]), Vector(nodes[segment + 1]), parent,
                             connected=segment > 0, deform=layer == "def")
                    chain[layer].append(name)
                    (deform_names if layer == "def" else mechanism_names).append(name)
            record["chains"].append(chain)
        bpy.ops.object.mode_set(mode="OBJECT")
        bone_collection = data.collections.new(BONE_COLLECTION_NAME)
        bone_collection[OWNER_KEY] = owner
        for names, hidden in ((control_names, False), (deform_names, True), (mechanism_names, True)):
            for name in names:
                bone = data.bones[name]
                bone_collection.assign(bone)
                bone.hide = hidden
        for name in control_names:
            pose = rig.pose.bones[name]
            pose.rotation_mode = "XYZ"
            pose.bone.color.palette = "THEME04" if name in (controls["waist"], controls["mid"], controls["hem"]) else "THEME03"
        context.view_layer.update()
        for index, chain in enumerate(record["chains"]):
            angle = math.tau * index / chain_count
            curve = _new_curve(f"{prefix}_Wire_{index + 1:02}",
                               [sample_fit(plan, t, angle) for t in (0.0, 0.5, 1.0)],
                               helpers, rig, obj, owner)
            context.view_layer.update()
            for point, name in enumerate((controls["waist"], controls["chains"][index]["mid"], controls["chains"][index]["hem"])):
                _hook(curve, rig, name, [point])
            chain["curve"] = curve.name
            record["cage"].append(curve.name)
            spline = rig.pose.bones[chain["manual"][-1]].constraints.new("SPLINE_IK")
            spline.name = "Skirt manual wire"
            spline.target = curve
            spline.chain_count = segment_count
            spline.use_even_divisions = True
            spline.use_curve_radius = False
            spline.y_scale_mode = "FIT_CURVE"
            spline.xz_scale_mode = "NONE"
            for manual, physics, deform in zip(chain["manual"], chain["phys"], chain["def"]):
                pose = rig.pose.bones[deform]
                copy = pose.constraints.new("COPY_TRANSFORMS")
                copy.name = "Skirt manual pose"
                copy.target, copy.subtarget = rig, manual
                # Copy each manual segment relative to its parent.  Pose-space
                # copying would cancel the previous segment's physics rotation
                # on every child, making simulated bends fail to propagate.
                copy.owner_space = copy.target_space = "LOCAL"
                rotation = pose.constraints.new("COPY_ROTATION")
                rotation.name = "Skirt physics delta"
                rotation.target, rotation.subtarget = rig, physics
                rotation.owner_space = rotation.target_space = "LOCAL"
                rotation.mix_mode = "BEFORE"
                driver = rotation.driver_add("influence").driver
                driver.type = "AVERAGE"
                variable = driver.variables.new()
                variable.name = "physics"
                variable.type = "SINGLE_PROP"
                variable.targets[0].id = rig
                variable.targets[0].data_path = '["physics_influence"]'
        for level, t in (("waist", 0.0), ("mid", 0.5), ("hem", 1.0)):
            ring = _new_curve(f"{prefix}_Wire_{level}",
                              [sample_fit(plan, t, math.tau * index / chain_count) for index in range(chain_count)],
                              helpers, rig, obj, owner, cyclic=True, smooth=False)
            context.view_layer.update()
            for index in range(chain_count):
                bone = controls["waist"] if level == "waist" else controls["chains"][index][level]
                _hook(ring, rig, bone, [index])
            record["cage"].append(ring.name)
            points = [sample_fit(plan, t, math.tau * index / 64) for index in range(64)]
            # The master ring is slightly outside its wire so it remains easy to pick.
            center = centers[("waist", "mid", "hem").index(level)]
            points = [center + (Vector(point) - center) * 1.065 for point in points]
            _shape(f"{prefix}_Shape_{level}", points,
                   [(index, (index + 1) % 64) for index in range(64)],
                   helpers, rig, controls[level], obj, owner)
        radius_world = plan["height_world"] * 0.028
        inverse = world.inverted().to_3x3()
        axes = [inverse @ Vector(vector) * radius_world for vector in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
        for entry in controls["chains"]:
            for name in entry.values():
                center = data.bones[name].head_local
                points = [center + sign * axis for axis in axes for sign in (-1, 1)]
                edges = [(a, b) for a in range(6) for b in range(a + 1, 6) if a // 2 != b // 2]
                _shape(name + "_Shape", points, edges, helpers, rig, name, obj, owner)
        groups = {}
        for chain_index, chain in enumerate(record["chains"]):
            for segment, name in enumerate(chain["def"]):
                group = obj.vertex_groups.new(name=name)
                groups[(chain_index, segment)] = group
                record["groups"].append(group.name)
        waist_group = obj.vertex_groups.new(name=controls["waist"])
        record["groups"].append(waist_group.name)
        for vertex_index, weights in enumerate(plan["vertex_weights"]):
            for chain_index, segment, weight in weights:
                group = waist_group if segment == -1 else groups[(chain_index, segment)]
                group.add([vertex_index], weight, "REPLACE")
        modifier = obj.modifiers.new(prefix + "_Skin", "ARMATURE")
        modifier.object = rig
        modifier.use_deform_preserve_volume = True
        record["modifier"] = modifier.name
        obj.modifiers.move(len(obj.modifiers) - 1, 0)
        if character:
            rig.parent = character
            rig.parent_type = "BONE"
            rig.parent_bone = anchor
            context.view_layer.update()
            rig.matrix_world = world
        elif original_parent:
            rig.parent = original_parent
            rig.parent_type = record["original"]["parent_type"]
            rig.parent_bone = record["original"]["parent_bone"]
            context.view_layer.update()
            rig.matrix_world = world
        obj.parent = rig
        obj.parent_type = "OBJECT"
        obj.parent_bone = ""
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = Matrix.Identity(4)
        context.view_layer.update()
        record["owned_objects"] = [candidate.name for candidate in bpy.data.objects
                                   if candidate is not obj and candidate.get(OWNER_KEY) == owner]
        write_record(obj, record)
        select_controls(context, obj)
        return record
    except Exception as error:
        current = context.view_layer.objects.active
        if current is not None and current.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for modifier in list(obj.modifiers):
            if modifier.name == record["modifier"]:
                obj.modifiers.remove(modifier)
        for name in record["groups"]:
            if name in obj.vertex_groups:
                obj.vertex_groups.remove(obj.vertex_groups[name])
        _restore_parent(obj, record, original_parent)
        _purge_owned(obj, owner)
        _clear_source_properties(obj)
        _restore_context(context, saved_context)
        if isinstance(error, ValueError):
            raise
        raise SkirtRigError(f"Skirt setup was rolled back: {error}") from error


def select_controls(context, obj, level="ALL"):
    record = read_record(obj)
    if not record:
        raise SkirtRigError("Build the skirt controls first.")
    rig = obj[RIG_KEY]
    _activate(context, rig, "POSE")
    wanted = record["controls"]["waist"]
    if level.lower() in {"waist", "mid", "hem"}:
        wanted = record["controls"][level.lower()]
    for bone in rig.pose.bones:
        bone.select = bone.name == wanted
    rig.data.bones.active = rig.data.bones[wanted]
    for collection in rig.data.bones[wanted].collections:
        collection.is_visible = True
    rig.data.bones[wanted].hide = False
    return rig


def remove_skirt(context, obj, allow_animation=False):
    record = read_record(obj)
    if not record:
        raise SkirtRigError("This mesh has no generated skirt setup.")
    rig = obj[RIG_KEY]
    owned = [candidate for candidate in bpy.data.objects if candidate is not obj and candidate.get(OWNER_KEY) == record["owner"]]
    if not allow_animation and any(candidate.animation_data and
                                   (candidate.animation_data.action or candidate.animation_data.nla_tracks)
                                   for candidate in owned):
        raise SkirtRigError("This skirt has animation. Keep a saved copy and explicitly confirm removal of its animated setup.")
    expected = obj.modifiers.get(record["modifier"])
    if expected is None:
        matching = [modifier for modifier in obj.modifiers if modifier.type == "ARMATURE" and modifier.object is rig]
        if len(matching) == 1:
            expected = matching[0]
    if expected and (expected.type != "ARMATURE" or expected.object is not rig):
        raise SkirtRigError("The generated skin modifier was replaced. Restore it before removing the setup.")
    # No user-owned child is deleted with its generated parent.
    for child in list(bpy.data.objects):
        if child is obj or child.parent not in owned or child in owned:
            continue
        world = child.matrix_world.copy()
        child.parent = None
        child.matrix_world = world
    _activate(context, obj)
    if expected:
        obj.modifiers.remove(expected)
    for name in record["groups"]:
        group = obj.vertex_groups.get(name)
        if group:
            obj.vertex_groups.remove(group)
    _restore_parent(obj, record)
    _purge_owned(obj, record["owner"])
    _clear_source_properties(obj)
    obj.vertex_groups.active_index = min(record["original"]["active_group"], len(obj.vertex_groups) - 1)
    context.view_layer.update()
    return record
