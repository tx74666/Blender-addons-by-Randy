"""Reversible hair binding on the original mesh and the character's own rig.

No mesh or Armature object is created. Persistent ownership and pre-bind data
make removal possible after saving and reopening, without guessing by names.
"""

from datetime import datetime
import json
import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import hair_bones_rig as rig
from . import hair_bones_topology as topology
from . import hair_bones_variants as variants
from .selected_bone_weights import _capture_vertex_groups, _restore_vertex_groups

BINDING_KEY = "character_designer_hair_binding_v1"
OLD_PARENT_KEY = "character_designer_hair_binding_old_parent"


def is_bound(source):
    return isinstance(source, bpy.types.Object) and BINDING_KEY in source


def _head(armature):
    name = rig._head_bone(armature)
    if name:
        return name
    names = [bone.name for bone in armature.data.bones
             if bone.name.rsplit(":", 1)[-1].casefold() == "head" and bone.use_deform]
    return names[0] if len(names) == 1 else ""


def _character(armature):
    return (isinstance(armature, bpy.types.Object) and armature.type == "ARMATURE"
            and not armature.get(variants.VERSION_KEY) and not armature.get(rig.OWNER_KEY))


def resolve_target(context, source, armature=None):
    """Prefer an existing binding; otherwise choose the nearest humanoid Head."""
    if source is None or source.type != "MESH":
        raise rig.HairBonesRigError("Choose the original hair mesh.")
    modifiers = [item for item in source.modifiers if item.type == "ARMATURE"]
    if len(modifiers) > 1:
        raise rig.HairBonesRigError("Hair binding supports one Armature modifier.")
    if modifiers and modifiers[0].object is None:
        raise rig.HairBonesRigError("The hair Armature modifier has no rig.")
    parent = source.parent if source.parent and source.parent.type == "ARMATURE" else None
    targets = [item for item in (armature, modifiers[0].object if modifiers else None,
                                parent, source.get(rig.RIG_KEY)) if item is not None]
    if len(set(targets)) > 1:
        raise rig.HairBonesRigError("The chosen, parent and bound hair rigs disagree.")
    target = targets[0] if targets else None
    if target:
        if not _character(target) or not _head(target):
            raise rig.HairBonesRigError("Choose the character Armature with a recognizable Head bone.")
        return target, _head(target)
    snapshot = rig._mesh_snapshot(source)
    if not snapshot["coordinates"]:
        raise rig.HairBonesRigError("The original hair mesh is empty.")
    from . import hair_bones_groups
    capture = hair_bones_groups._read(source)
    roots = set()
    if capture:
        for strand in capture.get("strands", []):
            roots.update(strand["layers"][0])
    coordinates = snapshot["coordinates"]
    points = [source.matrix_world @ Vector(coordinates[index])
              for index in roots if 0 <= index < len(coordinates)]
    if not points:
        points = [source.matrix_world @ Vector(point) for point in coordinates]
    center = sum(points, Vector()) / len(points)
    candidates = []
    for item in context.scene.objects:
        if not _character(item):
            continue
        head = _head(item)
        if not head or not item.data.bones[head].use_deform:
            continue
        names = {bone.name.rsplit(":", 1)[-1].casefold().removeprefix("def-")
                 for bone in item.data.bones}
        if not any(name in {"neck", "spine", "hips", "pelvis", "chest"} for name in names):
            continue
        bone = item.data.bones[head]
        position = item.matrix_world @ ((bone.head_local + bone.tail_local) * 0.5)
        distance = (position - center).length
        if math.isfinite(distance):
            candidates.append((distance, item.name, item, head))
    candidates.sort(key=lambda item: item[:2])
    if not candidates:
        raise rig.HairBonesRigError("No character Head was found; choose the target Armature.")
    if len(candidates) > 1 and math.isclose(candidates[0][0], candidates[1][0], rel_tol=1e-4, abs_tol=1e-6):
        raise rig.HairBonesRigError("Two character Heads are equally near; choose the target Armature.")
    return candidates[0][2:]


def _cap_vertices(source, plans):
    """Validate a complete partition and the residual region touching roots."""
    covered = {index for plan in plans for index in plan["vertices"]}
    roots = {index for plan in plans for index in plan["layers"][0]}
    bm = bmesh.from_edit_mesh(source.data).copy() if source.mode == "EDIT" else bmesh.new()
    try:
        if source.mode != "EDIT":
            bm.from_mesh(source.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.index_update()
        for domain in (bm.verts, bm.edges, bm.faces):
            for element in domain:
                element.hide = False
        allowed, edges, adjacency = topology._visible_graph(bm)
        discovered = topology._discover(source, bm, allowed, edges, adjacency, set())
        if any(set(plan["vertices"]) - covered for plan in discovered):
            raise rig.HairBonesRigError("Capture all hair strands before binding; uncaptured long strands are not a head cap.")
        remaining = set(range(len(bm.verts))) - covered
        pending = set(remaining)
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
            if not boundary or not boundary.issubset(roots):
                raise rig.HairBonesRigError("Uncaptured geometry does not form a cap joined only to strand roots; refine the capture first.")
        return tuple(sorted(remaining))
    finally:
        bm.free()


def _read(source):
    try:
        value = json.loads(source[BINDING_KEY])
        if value["version"] != 1 or not value["bones"] or len(set(value["bones"])) != len(value["bones"]):
            raise ValueError()
        return value
    except (KeyError, TypeError, ValueError):
        raise rig.HairBonesRigError("The reversible hair binding record is missing or damaged.") from None


def _parent_state(source):
    return {"had_parent": source.parent is not None, "type": source.parent_type,
            "bone": source.parent_bone, "inverse": [list(row) for row in source.matrix_parent_inverse],
            "basis": [list(row) for row in source.matrix_basis]}


def _restore_parent(source, state, parent):
    source.parent = parent
    source.parent_type = state["type"]
    source.parent_bone = state["bone"]
    source.matrix_parent_inverse = Matrix(state["inverse"])
    source.matrix_basis = Matrix(state["basis"])


def _restore_touched_weights(source, before, affected):
    previous = {item["name"]: item for item in before}
    vertices = tuple(range(len(source.data.vertices)))
    for name in affected:
        saved, group = previous.get(name), source.vertex_groups.get(name)
        if saved is None:
            if group:
                source.vertex_groups.remove(group)
            continue
        group = group or source.vertex_groups.new(name=name)
        group.lock_weight = False
        if vertices:
            group.remove(vertices)
        for index, weight in saved["weights"]:
            group.add((index,), weight, "REPLACE")
        group.lock_weight = saved["lock_weight"]


def _modifier_move(source, modifier, index):
    current = tuple(source.modifiers).index(modifier)
    source.modifiers.move(current, min(index, len(source.modifiers) - 1))


def bind_hair(context, source, plans, *, bone_count=4, armature=None):
    """Bind the original mesh; record only changes owned by this operation."""
    if variants.source_for(source) is not source:
        raise rig.HairBonesRigError("Return to the original hair mesh before binding.")
    if is_bound(source) or rig._read_records(source):
        raise rig.HairBonesRigError("Remove the existing hair binding before binding again.")
    target, head = resolve_target(context, source, armature)
    rig._validate_object(context, source)
    rig._validate_object(context, target, armature=True)
    if not source.visible_get() or not target.visible_get():
        raise rig.HairBonesRigError("Show the original hair and character Armature in this View Layer.")
    if not target.data.bones[head].use_deform:
        raise rig.HairBonesRigError("The character Head must be a deform bone.")
    if source.parent is not None and (source.parent is not target or source.parent_type != "OBJECT"):
        raise rig.HairBonesRigError("Hair must be unparented or Object-parented to its character Armature.")
    plans = tuple(plans)
    snapshot = rig._mesh_snapshot(source)
    rig._plan_copy(plans, snapshot, bone_count)
    cap = _cap_vertices(source, plans)
    state = rig._context_snapshot(context, source, target)
    parent, parent_data = source.parent, _parent_state(source)
    existing = next((item for item in source.modifiers if item.type == "ARMATURE"), None)
    modifier_before = {"name": existing.name, "index": tuple(source.modifiers).index(existing)} if existing else None
    mirrors = [(item, item.use_mirror_vertex_groups) for item in source.modifiers if item.type == "MIRROR"]
    collections_before = set(target.data.collections.keys())
    front_before = target.show_in_front
    old_groups = None
    result = None
    try:
        rig._mode(context, source, "OBJECT")
        old_groups = _capture_vertex_groups(source)
        deform = {bone.name for bone in target.data.bones if bone.use_deform}
        influencing = {group["name"] for group in old_groups if group["name"] in deform
                       and any(weight > rig.EPSILON for _, weight in group["weights"])}
        rig._check_rest(context, target, influencing | {head})
        if cap:
            weights = {head: {index: 1.0 for index in cap}}
            for group in old_groups:
                if group["name"] in deform and group["name"] != head:
                    if group["lock_weight"] and any(index in cap and weight > rig.EPSILON for index, weight in group["weights"]):
                        raise rig.HairBonesRigError(f"Unlock '{group['name']}' before assigning the head cap.")
                    if any(index in cap and weight > rig.EPSILON for index, weight in group["weights"]):
                        weights[group["name"]] = {index: 0.0 for index in cap}
            rig._write_weights(source, weights)
        result = rig.build_hair_bones(context, source, plans, bone_count=bone_count,
                                     armature=target, parent_bone=head, mirror_controls=True)
        current_groups = _capture_vertex_groups(source)
        previous = {item["name"]: item for item in old_groups}
        affected = [item["name"] for item in current_groups if item != previous.get(item["name"])]
        data = {"version": 1, "topology": snapshot["topology"], "head": head,
                "bones": [name for chain in result["chains"] for name in chain["bones"]],
                "groups": list(old_groups), "affected_groups": affected, "parent": parent_data,
                "modifier_before": modifier_before,
                "modifier": next(item.name for item in source.modifiers if item.type == "ARMATURE"),
                "mirrors": [{"name": item.name, "flip": flag} for item, flag in mirrors],
                "created_collections": sorted(set(target.data.collections.keys()) - collections_before),
                "front_before": front_before, "cap_vertices": cap}
        source[BINDING_KEY] = json.dumps(data, sort_keys=True, separators=(",", ":"))
        if parent:
            source[OLD_PARENT_KEY] = parent
        result["cap_vertices"] = cap
        return result
    except Exception:
        # The rig service is transactional. If failure follows its successful
        # return, remove only its newly made bones before restoring our cap edits.
        if result:
            rig._mode(context, target, "EDIT")
            for chain in result["chains"]:
                for name in reversed(chain["bones"]):
                    target.data.edit_bones.remove(target.data.edit_bones[name])
            rig._mode(context, source, "OBJECT")
            if not existing:
                for item in tuple(source.modifiers):
                    if item.type == "ARMATURE" and item.object is target:
                        source.modifiers.remove(item)
            for name in set(target.data.collections.keys()) - collections_before:
                target.data.collections.remove(target.data.collections[name])
            for key in (rig.RECORD_KEY, rig.RIG_KEY, BINDING_KEY, OLD_PARENT_KEY):
                if key in source:
                    del source[key]
        if old_groups is not None:
            rig._mode(context, source, "OBJECT")
            _restore_vertex_groups(source, old_groups)
        _restore_parent(source, parent_data, parent)
        for item, flag in mirrors:
            item.use_mirror_vertex_groups = flag
        if existing and modifier_before:
            _modifier_move(source, existing, modifier_before["index"])
        target.show_in_front = front_before
        rig._restore_context(context, source, state)
        raise


def _animation_paths(owner):
    animation = getattr(owner, "animation_data", None)
    if not animation:
        return ()
    curves = list(animation.drivers)
    actions = {animation.action} if animation.action else set()
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.type == "CLIP" and strip.action:
                actions.add(strip.action)
    for action in actions:
        if getattr(action, "is_action_legacy", False):
            curves.extend(action.fcurves)
        else:
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in getattr(strip, "channelbags", ()):
                        curves.extend(bag.fcurves)
    return tuple(curves)


def _check_bone_dependencies(source, armature, names):
    for bone in armature.data.bones:
        if bone.parent and bone.parent.name in names and bone.name not in names:
            raise rig.HairBonesRigError(f"Other bone '{bone.name}' depends on these hair bones; detach it before removal.")
    paths = tuple(armature.pose.bones[name].path_from_id() for name in names)
    if any(any(curve.data_path == path or curve.data_path.startswith(path + '.') or curve.data_path.startswith(path + '[')
               for path in paths) for curve in _animation_paths(armature)):
        raise rig.HairBonesRigError("These hair bones have animation; remove or transfer their animation before removing the binding.")
    if any(armature.pose.bones[name].constraints for name in names):
        raise rig.HairBonesRigError("These hair bones have custom constraints; remove or transfer them before removing the binding.")
    for user in bpy.data.user_map(subset={armature}).get(armature, ()):
        for curve in _animation_paths(user):
            if curve.driver is None:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id is armature and (target.bone_target in names or any(path in target.data_path for path in paths)):
                        raise rig.HairBonesRigError(f"A driver on '{user.name}' uses a hair bone; detach it first.")
    for obj in bpy.data.objects:
        if obj.parent is armature and obj.parent_type == "BONE" and obj.parent_bone in names:
            raise rig.HairBonesRigError(f"'{obj.name}' is parented to a hair bone; detach it first.")
        if obj is not source and obj.type == "MESH" and any(
                item.type == "ARMATURE" and item.object is armature for item in obj.modifiers):
            groups = {group.index for group in obj.vertex_groups if group.name in names}
            if any(value.group in groups and value.weight > rig.EPSILON for vertex in obj.data.vertices for value in vertex.groups):
                raise rig.HairBonesRigError(f"Other mesh '{obj.name}' uses these hair bones; detach it first.")
        owners = [obj, *tuple(obj.pose.bones)] if obj.type == "ARMATURE" else [obj]
        for owner in owners:
            if obj is armature and isinstance(owner, bpy.types.PoseBone) and owner.name in names:
                continue
            for constraint in owner.constraints:
                if getattr(constraint, "target", None) is armature and getattr(constraint, "subtarget", "") in names:
                    raise rig.HairBonesRigError(f"A constraint on '{obj.name}' uses a hair bone; detach it first.")
                if any(item.target is armature and item.subtarget in names for item in getattr(constraint, "targets", ())):
                    raise rig.HairBonesRigError(f"A constraint on '{obj.name}' uses a hair bone; detach it first.")
        for owner in (obj, obj.data, getattr(obj.data, "shape_keys", None)):
            for curve in _animation_paths(owner):
                if curve.driver is None:
                    continue
                for variable in curve.driver.variables:
                    for target in variable.targets:
                        if target.id is armature and (target.bone_target in names or any(path in target.data_path for path in paths)):
                            raise rig.HairBonesRigError(f"A driver on '{obj.name}' uses a hair bone; detach it first.")


def _validate_restore(source, data, armature):
    """Check the complete restoration payload before any destructive work."""
    try:
        parent = data["parent"]
        if parent["type"] != "OBJECT" or not isinstance(parent["had_parent"], bool):
            raise ValueError()
        for field in ("inverse", "basis"):
            matrix = parent[field]
            if len(matrix) != 4 or any(len(row) != 4 for row in matrix) or not rig._finite(v for row in matrix for v in row):
                raise ValueError()
        groups = data["groups"]
        if len({group["name"] for group in groups}) != len(groups):
            raise ValueError()
        for group in groups:
            if not isinstance(group["name"], str) or not isinstance(group["lock_weight"], bool):
                raise ValueError()
            if any(type(index) is not int or not 0 <= index < len(source.data.vertices)
                   or not math.isfinite(weight) or not 0 <= weight <= 1 for index, weight in group["weights"]):
                raise ValueError()
        deform = {bone.name for bone in armature.data.bones if bone.use_deform}
        if not set(data["affected_groups"]).issubset(deform):
            raise ValueError()
        for saved in data["mirrors"]:
            modifier = source.modifiers.get(saved["name"])
            if modifier is None or modifier.type != "MIRROR" or not isinstance(saved["flip"], bool):
                raise rig.HairBonesRigError("The recorded Mirror modifier changed; restore it before removing the binding.")
        previous = data["modifier_before"]
        if previous is not None and (previous["name"] != data["modifier"] or type(previous["index"]) is not int
                                     or not 0 <= previous["index"] < len(source.modifiers)):
            raise ValueError()
        if not all(isinstance(name, str) for name in data["created_collections"]):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, rig.HairBonesRigError):
            raise
        raise rig.HairBonesRigError("The saved pre-binding state is incomplete; removal was stopped.") from exc


def _owned_bone_snapshot(armature, names):
    result = []
    bone_fields = ("use_deform", "use_inherit_rotation", "use_local_location", "inherit_scale",
                   "use_relative_parent", "hide", "hide_select", "bbone_segments", "head_radius",
                   "tail_radius", "envelope_distance", "envelope_weight", "use_envelope_multiply")
    pose_fields = ("rotation_mode", "lock_location", "lock_rotation", "lock_rotation_w",
                   "lock_rotations_4d", "lock_scale", "custom_shape", "custom_shape_transform",
                   "use_custom_shape_bone_size", "custom_shape_scale_xyz", "custom_shape_translation",
                   "custom_shape_rotation_euler")
    for name in names:
        bone, pose = armature.data.bones[name], armature.pose.bones[name]
        def value(owner, field):
            item = getattr(owner, field)
            return tuple(item) if getattr(owner.bl_rna.properties[field], "is_array", False) else item
        result.append({"name": name, "rest": rig._bone_state(bone), "properties": dict(bone.items()),
                       "bone_fields": {field: value(bone, field) for field in bone_fields},
                       "pose_fields": {field: value(pose, field) for field in pose_fields},
                       "pose_properties": dict(pose.items()), "pose": pose.matrix_basis.copy(),
                       "collections": tuple(bone.collections), "color": bone.color.palette,
                       "color_custom": (tuple(bone.color.custom.normal), tuple(bone.color.custom.select), tuple(bone.color.custom.active))})
    return result


def _restore_owned_bones(context, armature, states):
    rig._mode(context, armature, "EDIT")
    for state in states:
        name, rest = state["name"], state["rest"]
        bone = armature.data.edit_bones.get(name) or armature.data.edit_bones.new(name)
        bone.head, bone.tail = rest["head"], rest["tail"]
        bone.align_roll(Matrix(rest["matrix"]).to_3x3().col[2])
    for state in states:
        bone, rest = armature.data.edit_bones[state["name"]], state["rest"]
        bone.parent = armature.data.edit_bones.get(rest["parent"])
        bone.use_connect = rest["connected"]
    rig._mode(context, armature, "OBJECT")
    for state in states:
        bone, pose = armature.data.bones[state["name"]], armature.pose.bones[state["name"]]
        for field, value in state["bone_fields"].items():
            setattr(bone, field, value)
        for field, value in state["pose_fields"].items():
            setattr(pose, field, value)
        for field, value in state["properties"].items():
            bone[field] = value
        for field, value in state["pose_properties"].items():
            pose[field] = value
        for collection in state["collections"]:
            collection.assign(bone)
        pose.matrix_basis = state["pose"]
        bone.color.palette = state["color"]
        bone.color.custom.normal, bone.color.custom.select, bone.color.custom.active = state["color_custom"]


def remove_hair_binding(context, source):
    """Remove owned hair bones and restore only the binding's previous state."""
    data = _read(source)
    armature = source.get(rig.RIG_KEY)
    rig._validate_object(context, source)
    rig._validate_object(context, armature, armature=True)
    record = rig._read_records(source)
    snapshot = rig._mesh_snapshot(source)
    if snapshot["topology"] != data["topology"]:
        raise rig.HairBonesRigError("Hair topology changed after binding; restore it before removing this binding.")
    rig._validate_owned(source, armature, record, snapshot)
    names = set(data["bones"])
    if names != {name for chain in record["chains"] for name in chain["bones"]}:
        raise rig.HairBonesRigError("The owned hair bone list changed; removal was stopped.")
    _validate_restore(source, data, armature)
    _check_bone_dependencies(source, armature, names)
    modifier = source.modifiers.get(data["modifier"])
    if modifier is None or modifier.type != "ARMATURE" or modifier.object is not armature:
        raise rig.HairBonesRigError("The recorded hair Armature modifier changed; removal was stopped.")
    parent = source.get(OLD_PARENT_KEY)
    if data["parent"]["had_parent"] and not isinstance(parent, bpy.types.Object):
        raise rig.HairBonesRigError("The pre-binding parent was removed; restore it before removing the binding.")
    state = rig._context_snapshot(context, source, armature)
    bone_states = _owned_bone_snapshot(armature, data["bones"])
    current_parent, parent_state = source.parent, _parent_state(source)
    current_mirrors = [(source.modifiers[saved["name"]], source.modifiers[saved["name"]].use_mirror_vertex_groups)
                       for saved in data["mirrors"]]
    modifier_index = tuple(source.modifiers).index(modifier)
    rig._mode(context, source, "OBJECT")
    current_groups = _capture_vertex_groups(source)
    mirror = armature.data.use_mirror_x
    try:
        # Restore all fallible weights/transforms before deleting any bone.
        _restore_touched_weights(source, data["groups"], data["affected_groups"])
        for saved in data["mirrors"]:
            source.modifiers[saved["name"]].use_mirror_vertex_groups = saved["flip"]
        _restore_parent(source, data["parent"], parent)
        if data["modifier_before"]:
            _modifier_move(source, modifier, data["modifier_before"]["index"])
        armature.data.use_mirror_x = False
        rig._mode(context, armature, "EDIT")
        for name in reversed(data["bones"]):
            armature.data.edit_bones.remove(armature.data.edit_bones[name])
        rig._mode(context, source, "OBJECT")
        context.view_layer.update()
    except Exception as exc:
        errors = []
        try:
            _restore_owned_bones(context, armature, bone_states)
            rig._mode(context, source, "OBJECT")
            _restore_vertex_groups(source, current_groups)
            _restore_parent(source, parent_state, current_parent)
            for item, flag in current_mirrors:
                item.use_mirror_vertex_groups = flag
            _modifier_move(source, modifier, modifier_index)
            rig._restore_context(context, source, state)
        except Exception as rollback:
            errors.append(str(rollback))
        suffix = " Rollback needs attention: " + "; ".join(errors) if errors else ""
        raise rig.HairBonesRigError(str(exc) + suffix) from exc
    finally:
        armature.data.use_mirror_x = mirror
    # Deletion of the recorded modifier/empty owned collection is now the
    # final commit; the character rig and unrelated groups remain in place.
    if data["modifier_before"] is None:
        source.modifiers.remove(modifier)
    for name in data["created_collections"]:
        collection = armature.data.collections.get(name)
        if collection and not collection.bones and not collection.children:
            armature.data.collections.remove(collection)
    armature.show_in_front = data["front_before"]
    for key in (BINDING_KEY, OLD_PARENT_KEY, rig.RECORD_KEY, rig.RIG_KEY):
        if key in source:
            del source[key]
    context.view_layer.update()
    return {"removed_bones": len(names)}


def _cleanup_inventory(source):
    collections = variants.variants_for(source)
    objects, meshes, armatures = set(), set(), set()
    for collection in collections:
        mesh, armature = collection.get(variants.MESH_KEY), collection.get(variants.ARMATURE_KEY)
        if not isinstance(mesh, bpy.types.Object) or mesh is source or mesh.type != "MESH":
            raise rig.HairBonesRigError("A generated copy has lost its mesh ownership; cleanup stopped.")
        if not isinstance(armature, bpy.types.Object) or armature.type != "ARMATURE":
            raise rig.HairBonesRigError("A generated copy has lost its private rig; cleanup stopped.")
        for obj in (mesh, armature):
            if obj.get(variants.SOURCE_KEY) is not source or obj.get(variants.VERSION_KEY) != 1:
                raise rig.HairBonesRigError("A copy's source ownership changed; cleanup stopped.")
            if obj.library or obj.data.library or obj.override_library or obj.data.users != 1:
                raise rig.HairBonesRigError("A generated copy is linked or shares its data; cleanup stopped.")
            if any(item not in collections for item in obj.users_collection):
                raise rig.HairBonesRigError(f"'{obj.name}' is used outside its generated versions; cleanup stopped.")
        record = rig._read_records(mesh)
        if not record or mesh.get(rig.RIG_KEY) is not armature:
            raise rig.HairBonesRigError("A generated copy's binding ownership changed; cleanup stopped.")
        rig._validate_owned(mesh, armature, record, rig._mesh_snapshot(mesh))
        expected = {record["parent"], *(name for chain in record["chains"] for name in chain["bones"])}
        if set(armature.data.bones.keys()) != expected:
            raise rig.HairBonesRigError("A generated rig contains other bones; cleanup stopped.")
        owned = {mesh, armature}
        reference = mesh.get(rig.MIRROR_KEY)
        if reference:
            if reference.type != "EMPTY" or reference.get(rig.OWNER_KEY) != rig.OWNER_VALUE or reference.get(rig.SOURCE_KEY) is not mesh:
                raise rig.HairBonesRigError("A generated Mirror reference changed; cleanup stopped.")
            owned.add(reference)
        if set(collection.objects) - owned or collection.children:
            raise rig.HairBonesRigError(f"'{collection.name}' contains other objects or collections; move them out before cleanup.")
        objects.update(owned)
        meshes.add(mesh)
        armatures.add(armature)
    for obj in bpy.data.objects:
        if obj in objects:
            continue
        if obj.parent in objects:
            raise rig.HairBonesRigError(f"'{obj.name}' is parented to a generated copy; cleanup stopped.")
        owners = [obj, *tuple(obj.pose.bones)] if obj.type == "ARMATURE" else [obj]
        for owner in owners:
            for constraint in owner.constraints:
                if getattr(constraint, "target", None) in objects or any(item.target in objects for item in getattr(constraint, "targets", ())):
                    raise rig.HairBonesRigError(f"'{obj.name}' depends on a generated copy; cleanup stopped.")
        for modifier in obj.modifiers:
            for prop in modifier.bl_rna.properties:
                if prop.type == "POINTER" and getattr(modifier, prop.identifier, None) in objects:
                    raise rig.HairBonesRigError(f"A modifier on '{obj.name}' uses a generated copy; cleanup stopped.")
        for owner in (obj, obj.data, getattr(obj.data, "shape_keys", None)):
            for curve in _animation_paths(owner):
                if curve.driver is not None and any(target.id in objects for variable in curve.driver.variables for target in variable.targets):
                    raise rig.HairBonesRigError(f"A driver on '{obj.name}' uses a generated copy; cleanup stopped.")
    # RNA modifier pointers do not cover Geometry Nodes socket ID properties,
    # collection instances, or drivers on materials, node trees and scenes.
    # Blender's ID user map includes those real references across datablocks.
    protected = set(collections) | objects | {obj.data for obj in meshes | armatures}
    internal = set(protected)
    internal.update(obj.data.shape_keys for obj in meshes if obj.data.shape_keys)

    def contains_protected_id(value):
        if isinstance(value, bpy.types.ID):
            return value in protected
        if hasattr(value, "items"):
            return any(contains_protected_id(item) for _, item in value.items())
        if isinstance(value, (tuple, list)):
            return any(contains_protected_id(item) for item in value)
        return False

    def extra_parent_reference(owner):
        if any(contains_protected_id(value) for _, value in owner.items()):
            return True
        trees = tuple(tree for tree in (getattr(owner, "node_tree", None),
                                       getattr(owner, "compositing_node_group", None)) if tree is not None)
        for animation_owner in (owner, *trees):
            for curve in _animation_paths(animation_owner):
                if curve.driver is not None and any(
                        target.id in protected for variable in curve.driver.variables for target in variable.targets):
                    return True
        for tree in trees:
            if any(contains_protected_id(value) for _, value in tree.items()):
                return True
            for node in tree.nodes:
                for prop in node.bl_rna.properties:
                    if prop.type == "POINTER" and contains_protected_id(getattr(node, prop.identifier, None)):
                        return True
                if any(contains_protected_id(getattr(socket, "default_value", None))
                       for socket in (*node.inputs, *node.outputs)):
                    return True
        if isinstance(owner, bpy.types.Scene) and (
                owner.camera in protected or any(marker.camera in protected for marker in owner.timeline_markers)):
            return True
        # A scene may use the same collection for physics as well as link it.
        physics = getattr(owner, "rigidbody_world", None)
        return physics is not None and (
            physics.collection in protected or physics.constraints in protected)

    for target, users in bpy.data.user_map(subset=protected).items():
        for owner in users:
            if owner in internal:
                continue
            parent = owner.collection if isinstance(owner, bpy.types.Scene) else owner
            structural_link = (target in collections and isinstance(parent, bpy.types.Collection)
                               and parent.children.get(target.name) is target)
            # Active/selected View Layer bases can also report Scene as an ID
            # user. They are ordinary scene membership, not an outside rig or
            # simulation dependency, provided no explicit scene reference exists.
            if isinstance(owner, bpy.types.Scene) and target in objects:
                structural_link = owner.objects.get(target.name) is target
                if not structural_link and target in armatures:
                    # Historical files may unlink a private rig while its
                    # visible owned mesh still uses it as parent/deformer.
                    # Blender reports that indirect scene dependency too.
                    structural_link = any(
                        owner.objects.get(mesh.name) is mesh and (
                            mesh.parent is target or any(modifier.type == "ARMATURE" and modifier.object is target
                                                         for modifier in mesh.modifiers))
                        for mesh in meshes)
            elif isinstance(owner, bpy.types.Scene) and target in collections:
                pending, visited = [owner.collection], set()
                while pending and not structural_link:
                    candidate = pending.pop()
                    if candidate in visited:
                        continue
                    visited.add(candidate)
                    structural_link = candidate is target
                    pending.extend(candidate.children)
            if structural_link and not extra_parent_reference(owner):
                continue
            raise rig.HairBonesRigError(
                f"'{owner.name}' uses generated hair data '{target.name}'; detach that dependency before cleanup.")
    return collections, objects, meshes, armatures


def _reveal_source(context, source):
    def walk(layer, path=()):
        path = (*path, layer)
        if source.name in layer.collection.objects:
            for item in path:
                item.exclude = False
                item.hide_viewport = False
                item.collection.hide_viewport = False
        for child in layer.children:
            walk(child, path)
    walk(context.view_layer.layer_collection)
    source.hide_viewport = source.hide_render = source.hide_select = False
    source.hide_set(False)
    context.view_layer.update()


def remove_generated_copies(context, source):
    """Delete verified historical copies, retaining and revealing the source."""
    if source is None or source.type != "MESH" or variants.source_for(source) is not source:
        raise rig.HairBonesRigError("Choose the original hair mesh for copy cleanup.")
    collections, objects, meshes, armatures = _cleanup_inventory(source)
    counts = {"removed_meshes": len(meshes), "removed_rigs": len(armatures)}
    if not objects:
        return counts
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    if not bpy.app.background:
        if not bpy.data.filepath:
            raise rig.HairBonesRigError("Save this Blender file once so cleanup can keep a recovery backup.")
        current = Path(bpy.data.filepath)
        backup = current.parent / ".character_designer_backups" / (current.stem + "-before-hair-copy-cleanup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".blend")
        backup.parent.mkdir(parents=True, exist_ok=True)
        if bpy.ops.wm.save_as_mainfile(filepath=str(backup), copy=True, check_existing=False) != {"FINISHED"}:
            raise rig.HairBonesRigError("The recovery backup failed; no copies were deleted.")
        counts["backup"] = str(backup)
    mesh_data = {obj.data for obj in meshes}
    armature_data = {obj.data for obj in armatures}
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in collections:
        bpy.data.collections.remove(collection)
    for data in mesh_data:
        if data.users == 0:
            bpy.data.meshes.remove(data)
    for data in armature_data:
        if data.users == 0:
            bpy.data.armatures.remove(data)
    _reveal_source(context, source)
    rig._mode(context, source, "OBJECT")
    return counts

