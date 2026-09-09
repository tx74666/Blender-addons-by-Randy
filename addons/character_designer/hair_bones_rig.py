"""Transactional FK bone chains for selected hair bands.

The service consumes copied source-local topology plans; it owns no UI and
never creates Curves, Hooks, drivers, or a persistent evaluation handler.
"""

import hashlib
import json
import math
import re
import uuid

import bmesh
import bpy
from mathutils import Matrix, Vector

from .selected_bone_weights import _capture_vertex_groups, _restore_vertex_groups


RECORD_KEY = "character_designer_hair_bones_v1"
RIG_KEY = "character_designer_hair_bones_armature"
MIRROR_KEY = "character_designer_hair_bones_mirror_plane"
OWNER_KEY = "character_designer_hair_bones_owner"
SOURCE_KEY = "character_designer_hair_bones_source"
SIGNATURE_KEY = "character_designer_hair_bones_signature"
OWNER_VALUE = "hair_bones_v1"
COLLECTION_NAME = "Hair"
EPSILON = 1.0e-8


class HairBonesRigError(ValueError):
    """A short, artist-facing refusal or rolled-back build failure."""


def _finite(values):
    return all(math.isfinite(float(value)) for value in values)


def _identity(matrix, tolerance=2.0e-5):
    return all(abs(matrix[i][j] - float(i == j)) <= tolerance for i in range(4) for j in range(4))


def _mode(context, obj, mode):
    current = context.view_layer.objects.active
    if current is not None and current.mode != "OBJECT":
        if bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
            raise HairBonesRigError("Blender could not leave the current editing mode.")
    for candidate in context.view_layer.objects:
        candidate.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if mode != "OBJECT" and bpy.ops.object.mode_set(mode=mode) != {"FINISHED"}:
        raise HairBonesRigError(f"Blender could not enter {mode.title()} Mode.")


def _mesh_snapshot(obj):
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        coordinates = tuple(tuple(v.co) for v in bm.verts)
        edges = tuple(tuple(v.index for v in edge.verts) for edge in bm.edges)
        faces = tuple(tuple(v.index for v in face.verts) for face in bm.faces)
        selection = {
            "vertices": tuple((v.select, v.hide) for v in bm.verts),
            "edges": tuple((e.select, e.hide) for e in bm.edges),
            "faces": tuple((f.select, f.hide) for f in bm.faces),
            "history": tuple(("V" if isinstance(e, bmesh.types.BMVert) else
                              "E" if isinstance(e, bmesh.types.BMEdge) else "F", e.index)
                             for e in bm.select_history),
            "active_face": bm.faces.active.index if bm.faces.active else -1,
            "select_mode": set(bm.select_mode),
        }
    else:
        coordinates = tuple(tuple(v.co) for v in obj.data.vertices)
        edges = tuple(tuple(e.vertices) for e in obj.data.edges)
        faces = tuple(tuple(f.vertices) for f in obj.data.polygons)
        selection = None
    payload = (len(coordinates), tuple(sorted(tuple(sorted(e)) for e in edges)), faces)
    digest = hashlib.sha256(repr(payload).encode("ascii")).hexdigest()
    return {"coordinates": coordinates, "topology": digest, "selection": selection}


def _context_snapshot(context, obj, armature):
    active = context.view_layer.objects.active
    armatures = {candidate for candidate in (active, armature) if candidate and candidate.type == "ARMATURE"}
    return {
        "mode": context.mode,
        "active": active,
        "selected": tuple(candidate for candidate in context.view_layer.objects if candidate.select_get()),
        "mesh_selection": _mesh_snapshot(obj)["selection"],
        "mesh_select_mode": tuple(context.tool_settings.mesh_select_mode),
        "active_group": obj.vertex_groups.active_index,
        "arms": {candidate: {
            "active": candidate.data.bones.active.name if candidate.data.bones.active else "",
            "selected": tuple(p.name for p in candidate.pose.bones if p.select),
            "in_front": candidate.show_in_front,
            "hidden": {bone.name: bool(bone.hide) for bone in candidate.data.bones},
            "collections": {collection.name: bool(collection.is_visible) for collection in candidate.data.collections},
        } for candidate in armatures},
    }


def _restore_context(context, obj, state):
    active = context.view_layer.objects.active
    if active is not None and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for candidate in context.view_layer.objects:
        candidate.select_set(candidate in state["selected"])
    context.view_layer.objects.active = state["active"]
    for armature, saved in state["arms"].items():
        armature.show_in_front = saved["in_front"]
        for bone in armature.pose.bones:
            bone.select = bone.name in saved["selected"]
            if bone.name in saved["hidden"]:
                bone.bone.hide = saved["hidden"][bone.name]
        for collection in armature.data.collections:
            if collection.name in saved["collections"]:
                collection.is_visible = saved["collections"][collection.name]
        armature.data.bones.active = armature.data.bones.get(saved["active"])
    obj.vertex_groups.active_index = state["active_group"]
    context.tool_settings.mesh_select_mode = state["mesh_select_mode"]
    if state["mode"] == "EDIT_MESH":
        if context.view_layer.objects.active is not obj:
            raise HairBonesRigError("The original hair Edit Mode context was lost.")
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(obj.data)
        for domain in (bm.verts, bm.edges, bm.faces):
            domain.ensure_lookup_table()
        saved = state["mesh_selection"]
        for domain, name in ((bm.verts, "vertices"), (bm.edges, "edges"), (bm.faces, "faces")):
            for element, (selected, hidden) in zip(domain, saved[name]):
                element.hide = hidden
                element.select = selected
        bm.select_mode = saved["select_mode"]
        bm.select_history.clear()
        domains = {"V": bm.verts, "E": bm.edges, "F": bm.faces}
        for kind, index in saved["history"]:
            bm.select_history.add(domains[kind][index])
        bm.faces.active = bm.faces[saved["active_face"]] if saved["active_face"] >= 0 else None
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    elif state["mode"] == "POSE":
        bpy.ops.object.mode_set(mode="POSE")


def _head_bone(armature):
    exact = [b.name for b in armature.data.bones if b.name.casefold() in {"head", "def-head"}]
    if len(exact) == 1:
        return exact[0]
    eyes = {}
    for bone in armature.data.bones:
        name = bone.name.casefold().removeprefix("def-")
        if name in {"eye.l", "eye_l", "l_eye", "left_eye"}:
            eyes["L"] = bone
        elif name in {"eye.r", "eye_r", "r_eye", "right_eye"}:
            eyes["R"] = bone
    if set(eyes) == {"L", "R"} and eyes["L"].parent and eyes["L"].parent == eyes["R"].parent:
        return eyes["L"].parent.name
    return ""


def _read_records(obj):
    value = obj.get(RECORD_KEY)
    if value is None:
        if RIG_KEY in obj:
            raise HairBonesRigError("Hair bone ownership is incomplete; undo the previous edit.")
        return None
    try:
        record = json.loads(value)
        if (record["version"] not in {1, 2, 3} or not isinstance(record["chains"], list) or not record["source_id"]
                or not isinstance(record["parent"], str) or not isinstance(record["topology"], str)):
            raise ValueError()
        for chain in record["chains"]:
            if (not isinstance(chain["signature"], str) or not isinstance(chain["bones"], list)
                    or not isinstance(chain["rest"], list) or not isinstance(chain["layers"], list)
                    or not isinstance(chain["vertices"], list)
                    or not isinstance(chain["count"], int) or not 1 <= chain["count"] <= 12
                    or not isinstance(chain["requested_count"], int)
                    or len(chain["bones"]) != len(set(chain["bones"]))):
                raise ValueError()
            members = chain.get("members")
            if members is not None:
                if record["version"] < 2 or not isinstance(members, list) or not members or chain["layers"]:
                    raise ValueError()
                if (not isinstance(chain.get("centers"), list) or len(chain["centers"]) < 2
                        or not isinstance(chain.get("group_id"), str)
                        or not isinstance(chain.get("group_name"), str)):
                    raise ValueError()
                identities = []
                for member in members:
                    if (not isinstance(member["signature"], str) or not isinstance(member["layers"], list)
                            or len(member["layers"]) < 2 or not all(member["layers"])
                            or not isinstance(member["vertices"], list)
                            or not isinstance(member["centers"], list)
                            or len(member["centers"]) != len(member["layers"])):
                        raise ValueError()
                    identities.append(member["signature"])
                if len(identities) != len(set(identities)):
                    raise ValueError()
            elif not chain["layers"]:
                raise ValueError()
        signatures = [chain["signature"] for chain in record["chains"]]
        if len(set(signatures)) != len(signatures):
            raise ValueError()
        if record.get("mirror_layout"):
            if record["version"] != 3 or record["mirror_layout"] != "BEFORE_ARMATURE_X":
                raise ValueError()
            for chain in record["chains"]:
                if chain.get("mirror_side") not in {"L", "R", "C"}:
                    raise ValueError()
                if chain.get("mirror_of") and chain["mirror_of"] not in signatures:
                    raise ValueError()
        return record
    except (TypeError, ValueError, KeyError):
        raise HairBonesRigError("Hair bone ownership is unreadable; undo the previous edit.") from None


def _resolve_rig(context, obj, armature, parent_bone, record):
    modifiers = tuple(m for m in obj.modifiers if m.type == "ARMATURE")
    if len(modifiers) > 1:
        raise HairBonesRigError("Hair Bones supports one Armature modifier per Mesh.")
    modifier = modifiers[0] if modifiers else None
    bound = modifier.object if modifier else None
    if modifier and (bound is None or bound.type != "ARMATURE"):
        raise HairBonesRigError("The existing Armature modifier has no valid rig.")
    if obj.parent_type == "BONE" and obj.parent is not None:
        raise HairBonesRigError("Bone-parented hair would deform twice; remove its bone parenting first.")
    parent_rig = obj.parent if obj.parent and obj.parent.type == "ARMATURE" else None
    candidates = [candidate for candidate in (armature, bound, parent_rig, obj.get(RIG_KEY)) if candidate is not None]
    if any(candidate.type != "ARMATURE" for candidate in candidates) or len(set(candidates)) > 1:
        raise HairBonesRigError("The selected, bound, and recorded hair rigs do not agree.")
    armature = candidates[0] if candidates else None
    if armature is None:
        heads = [(candidate, _head_bone(candidate)) for candidate in context.scene.objects
                 if candidate.type == "ARMATURE"]
        heads = [(candidate, head) for candidate, head in heads if head]
        if len(heads) > 1:
            raise HairBonesRigError("More than one head rig is available; choose the hair Armature.")
        if heads:
            armature, inferred = heads[0]
            parent_bone = parent_bone or inferred
    if record:
        if armature is None or obj.get(RIG_KEY) is not armature:
            raise HairBonesRigError("The recorded hair Armature is missing; undo its removal.")
        if parent_bone and parent_bone != record["parent"]:
            raise HairBonesRigError("The attachment changed; undo the old hair chain before rebuilding.")
        parent_bone = record["parent"]
    if armature is not None:
        parent_bone = parent_bone or _head_bone(armature)
        if not parent_bone or armature.data.bones.get(parent_bone) is None:
            raise HairBonesRigError("Choose an existing head or attachment bone on the hair rig.")
    elif parent_bone:
        raise HairBonesRigError("A parent bone needs an existing Armature.")
    return armature, parent_bone, modifier


def _mirror_preflight(obj, armature, parent_bone, record, modifier, *, full_mirror_build=False):
    mirrors = tuple(m for m in obj.modifiers if m.type == "MIRROR")
    reference = obj.get(MIRROR_KEY)
    if record and record.get("mirror_layout") == "BEFORE_ARMATURE_X":
        from . import hair_bones_mirror
        hair_bones_mirror.preflight(obj)
        if ([m.name for m in mirrors] != record.get("mirrors", []) or len(mirrors) != 1
                or reference is not None or mirrors[0].mirror_object is not None
                or not mirrors[0].use_mirror_vertex_groups or modifier is None
                or tuple(obj.modifiers).index(mirrors[0]) >= tuple(obj.modifiers).index(modifier)):
            raise HairBonesRigError("The generated full-hair Mirror binding changed; generate a new version.")
        if (not modifier.show_viewport or not modifier.show_render or not modifier.use_vertex_groups
                or modifier.use_bone_envelopes or modifier.vertex_group or modifier.use_multi_modifier):
            raise HairBonesRigError("The existing Armature modifier must use ordinary, unmasked vertex-group deformation.")
        return mirrors
    if record:
        if [m.name for m in mirrors] != record.get("mirrors", []):
            raise HairBonesRigError("The hair Mirror stack changed; undo that edit before adding bones.")
        if mirrors:
            if (reference is None or reference.type != "EMPTY" or reference.get(OWNER_KEY) != OWNER_VALUE
                    or reference.get(SOURCE_KEY) is not obj or any(m.mirror_object is not reference for m in mirrors)
                    or len(reference.constraints) != 1):
                raise HairBonesRigError("The generated Mirror reference changed; undo that edit first.")
            constraint = reference.constraints[0]
            if (constraint.type != "CHILD_OF" or constraint.target is not armature or constraint.subtarget != parent_bone
                    or constraint.mute or constraint.influence != 1.0 or reference.parent is not None
                    or constraint.owner_space != "WORLD" or constraint.target_space != "WORLD"):
                raise HairBonesRigError("The generated Mirror attachment changed; undo that edit first.")
        elif reference is not None:
            raise HairBonesRigError("Hair Mirror ownership is incomplete; undo the previous edit.")
    elif reference is not None or any(m.mirror_object is not None for m in mirrors):
        raise HairBonesRigError("Hair Bones needs a Mesh-local Mirror plane; custom Mirror Objects are not supported.")
    if modifier:
        if (not modifier.show_viewport or not modifier.show_render or not modifier.use_vertex_groups
                or modifier.use_bone_envelopes or modifier.vertex_group or modifier.use_multi_modifier):
            raise HairBonesRigError("The existing Armature modifier must use ordinary, unmasked vertex-group deformation.")
        if (not (full_mirror_build and record is None)
                and any(tuple(obj.modifiers).index(m) < tuple(obj.modifiers).index(modifier) for m in mirrors)):
            raise HairBonesRigError("The hair Armature must precede Mirror before adding shared strand controls.")
    return mirrors


def _create_mirror_reference(context, obj, armature, parent_bone, mirrors):
    reference = bpy.data.objects.new(f"Hair Mirror Plane {obj.name}", None)
    try:
        (obj.users_collection[0] if obj.users_collection else context.scene.collection).objects.link(reference)
        reference[OWNER_KEY] = OWNER_VALUE
        reference[SOURCE_KEY] = obj
        reference.matrix_world = obj.matrix_world.copy()
        reference.empty_display_size = 0.02
        reference.hide_render = True
        reference.hide_select = True
        constraint = reference.constraints.new("CHILD_OF")
        constraint.name = "Follow Hair Attachment"
        constraint.target = armature
        constraint.subtarget = parent_bone
        constraint.inverse_matrix = (armature.matrix_world @ armature.data.bones[parent_bone].matrix_local).inverted()
        for mirror in mirrors:
            mirror.mirror_object = reference
        reference.hide_set(True)
        return reference
    except Exception:
        for mirror in mirrors:
            if mirror.mirror_object is reference:
                mirror.mirror_object = None
        bpy.data.objects.remove(reference, do_unlink=True)
        raise


def _validate_object(context, obj, *, armature=False):
    expected = "ARMATURE" if armature else "MESH"
    if obj is None or obj.type != expected:
        raise HairBonesRigError(f"Choose one {expected.title()} object.")
    if context.view_layer.objects.get(obj.name) is not obj or obj.hide_get() or obj.hide_viewport or obj.hide_select:
        raise HairBonesRigError(f"Reveal and unlock '{obj.name}' in the current View Layer.")
    if obj.library or obj.data.library or obj.override_library or obj.data.override_library:
        raise HairBonesRigError("Linked or overridden hair and rigs are not supported.")
    if obj.data.users != 1:
        raise HairBonesRigError("Make the Mesh and Armature data single-user before generating hair bones.")
    if any(not c.mute and c.influence > EPSILON for c in obj.constraints):
        raise HairBonesRigError("Object constraints are not supported while binding hair bones.")
    if not _finite(v for row in obj.matrix_world for v in row) or abs(obj.matrix_world.determinant()) < EPSILON:
        raise HairBonesRigError("The object transform must be finite and have no zero scale.")
    if armature and obj.mode == "EDIT":
        raise HairBonesRigError("Finish Armature Edit Mode before binding hair.")


def _distances(centers):
    if len(centers) < 2 or any(len(point) != 3 or not _finite(point) for point in centers):
        raise HairBonesRigError("The hair centerline contains invalid coordinates.")
    distances = [0.0]
    for first, second in zip(centers, centers[1:]):
        span = (second - first).length
        if span <= EPSILON:
            raise HairBonesRigError("Two hair sections share a center; refine the selected band.")
        distances.append(distances[-1] + span)
    return tuple(distances)


def _native_plan_copy(plan, snapshot):
    if "members" in plan:
        raise HairBonesRigError("A hair group must contain individual strands, not nested groups.")
    layers = tuple(tuple(int(index) for index in layer) for layer in plan["layers"])
    centers = tuple(Vector(point) for point in plan["centers"])
    flat = [index for layer in layers for index in layer]
    vertices = set(flat)
    if (len(layers) < 2 or len(centers) != len(layers) or any(not layer for layer in layers)
            or len(flat) != len(vertices)
            or min(vertices) < 0 or max(vertices) >= len(snapshot["coordinates"])):
        raise HairBonesRigError("Hair bands overlap or contain invalid cross-sections.")
    if set(plan.get("vertices", vertices)) != vertices:
        raise HairBonesRigError("The hair band vertex domain does not match its sections.")
    if not plan.get("direction_confirmable", False):
        raise HairBonesRigError("Choose the root end of this hair band before generating bones.")
    signature = str(plan["signature"])
    if not signature:
        raise HairBonesRigError("Hair bands have invalid identities.")
    return {"signature": signature, "layers": layers, "centers": centers,
            "vertices": tuple(sorted(vertices)), "distances": _distances(centers),
            "root_tip_rule": str(plan.get("root_tip_rule", "EXPLICIT")),
            "direction_confirmable": True}


def _members(plan):
    """Return the actual mesh strips, never treating a group guide as a strip."""
    return plan.get("members") or (plan,)


def _root_vertices(plan):
    return {index for member in _members(plan) for index in member["layers"][0]}


def _plan_copy(plans, snapshot, bone_count):
    try:
        if isinstance(bone_count, bool) or int(bone_count) != bone_count or not 1 <= bone_count <= 12:
            raise HairBonesRigError("Bone Count must be between 1 and 12.")
        result = []
        for plan in plans:
            if "members" in plan:
                raise HairBonesRigError("Each strand needs its own bone chain; shared-chain plans are no longer supported.")
            copied = _native_plan_copy(plan, snapshot)
            copied["count"] = min(int(bone_count), len(copied["centers"]) - 1)
            if not copied["signature"] or any(item["signature"] == copied["signature"] for item in result):
                raise HairBonesRigError("Hair bands have duplicate or invalid identities.")
            for earlier in result:
                overlap = set(copied["vertices"]) & set(earlier["vertices"])
                if overlap and not (overlap.issubset(copied["layers"][0])
                                    and overlap.issubset(earlier["layers"][0])):
                    raise HairBonesRigError("Hair bands may share root vertices, but not their inner sections.")
            copied["requested_count"] = int(bone_count)
            result.append(copied)
        if not result:
            raise HairBonesRigError("Select at least one complete hair band.")
        return tuple(result)
    except HairBonesRigError:
        raise
    except (TypeError, KeyError, IndexError, OverflowError, ValueError) as exc:
        raise HairBonesRigError("The selected hair band plan is incomplete.") from exc


def _sample(points, distances, count):
    result, segment = [], 0
    for index in range(count + 1):
        distance = distances[-1] * index / count
        while segment < len(points) - 2 and distance > distances[segment + 1]:
            segment += 1
        factor = (distance - distances[segment]) / (distances[segment + 1] - distances[segment])
        result.append(points[segment].lerp(points[segment + 1], factor))
    if any((b - a).length <= EPSILON for a, b in zip(result, result[1:])):
        raise HairBonesRigError("The sampled hair chain folds onto itself; use fewer bones.")
    return tuple(result)


def _bone_state(bone):
    return {"head": list(bone.head_local), "tail": list(bone.tail_local),
            "matrix": [list(row) for row in bone.matrix_local],
            "parent": bone.parent.name if bone.parent else "", "connected": bool(bone.use_connect)}


def _bone_state_matches(actual, saved):
    if actual.get("parent") != saved.get("parent") or actual.get("connected") != saved.get("connected"):
        return False
    try:
        for name in ("head", "tail", "matrix"):
            left, right = actual[name], saved[name]
            if name == "matrix":
                left, right = [v for row in left for v in row], [v for row in right for v in row]
            if len(left) != len(right) or any(not math.isclose(a, b, rel_tol=1.0e-6, abs_tol=1.0e-6)
                                            for a, b in zip(left, right)):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _validate_owned(obj, armature, record, snapshot):
    if not record:
        return {}
    if record["topology"] != snapshot["topology"]:
        raise HairBonesRigError("Hair topology changed; undo the old hair chains before rebuilding.")
    owned = {}
    for chain in record["chains"]:
        if (not chain.get("bones") or len(chain["bones"]) != chain["count"]
                or len(chain.get("rest", ())) != chain["count"]):
            raise HairBonesRigError("A recorded hair chain is incomplete.")
        for name, state in zip(chain["bones"], chain["rest"]):
            bone = armature.data.bones.get(name)
            if (bone is None or bone.get(OWNER_KEY) != OWNER_VALUE or bone.get(SOURCE_KEY) is not obj
                    or bone.get(SIGNATURE_KEY) != chain["signature"] or not bone.use_deform
                    or not _bone_state_matches(_bone_state(bone), state) or obj.vertex_groups.get(name) is None):
                raise HairBonesRigError("A generated hair chain changed; undo that edit before rebuilding.")
        owned[chain["signature"]] = chain
    return owned


def _same_centers(first, second):
    try:
        return len(first) == len(second) and all(
            len(a) == len(b) == 3 and all(math.isclose(x, y, rel_tol=1.0e-6, abs_tol=1.0e-6)
                                        for x, y in zip(a, b)) for a, b in zip(first, second))
    except (TypeError, ValueError):
        return False


def _same_plan(old, plan):
    """An existing chain is selectable, but never silently rebound to a new guide."""
    if (old["count"] != plan["count"] or old["requested_count"] != plan["requested_count"]
            or set(old["vertices"]) != set(plan["vertices"])
            or bool(old.get("members")) != bool(plan.get("members"))):
        return False
    # Legacy version-one records have no guide coordinates. Their original
    # rest-state validation remains in force; newly built chains save them.
    if "centers" in old and not _same_centers(old["centers"], plan["centers"]):
        return False
    old_members = {member["signature"]: member for member in _members(old)}
    new_members = {member["signature"]: member for member in _members(plan)}
    if old_members.keys() != new_members.keys():
        return False
    for signature, before in old_members.items():
        after = new_members[signature]
        if (tuple(tuple(layer) for layer in before["layers"]) != after["layers"]
                or ("centers" in before and not _same_centers(before["centers"], after["centers"]))):
            return False
    return True


def _check_rest(context, armature, names):
    if armature is None:
        return
    evaluated = armature.evaluated_get(context.evaluated_depsgraph_get())
    for name in names:
        bone = armature.data.bones.get(name)
        pose = evaluated.pose.bones.get(name)
        if bone is None or pose is None or not _identity(pose.matrix @ bone.matrix_local.inverted()):
            raise HairBonesRigError(f"Return attachment/influencing bone '{name}' to Rest before binding hair.")


def _weight_plan(obj, armature, plans, parent_bone, first_binding, old_states):
    deform = {b.name for b in armature.data.bones if b.use_deform} if armature else set()
    by_name = {state["name"]: state for state in old_states}
    selected = {index for plan in plans for index in plan["vertices"]}
    touched, influencing, supported = {}, set(), set()
    for state in old_states:
        if state["name"] not in deform:
            continue
        for index, weight in state["weights"]:
            if not math.isfinite(weight) or weight < 0:
                raise HairBonesRigError("Existing hair deform weights are invalid.")
            if weight > EPSILON:
                supported.add(index)
                if first_binding:
                    influencing.add(state["name"])
                if index in selected:
                    influencing.add(state["name"])
                    if state["lock_weight"]:
                        raise HairBonesRigError(f"Unlock '{state['name']}' before replacing this band's weights.")
                    touched.setdefault(state["name"], {})[index] = 0.0
    for plan in plans:
        if plan.get("mirror_of"):
            # Native Mirror supplies these groups on the evaluated opposite
            # half. Writing them to the source half would bind both sides twice.
            continue
        count = plan["count"]
        for member in _members(plan):
            for layer, distance in zip(member["layers"], member["distances"]):
                position = distance / member["distances"][-1]
                root_blend = min(1.0, position * count * 2.0)
                if root_blend < 1.0:
                    # Every strand's root is anchored to the same attachment,
                    # even when its guide or length differs from its group.
                    touched.setdefault(parent_bone, {}).update({index: 1.0 - root_blend for index in layer})
                    if root_blend > EPSILON:
                        touched.setdefault(plan["names"][0], {}).update({index: root_blend for index in layer})
                    continue
                value = max(0.0, min(count - 1.0, position * count - 0.5))
                first = min(int(value), count - 1)
                factor = value - first
                for offset, weight in ((first, 1.0 - factor), (min(first + 1, count - 1), factor)):
                    if weight > EPSILON:
                        group = touched.setdefault(plan["names"][offset], {})
                        for index in layer:
                            group[index] = group.get(index, 0.0) + weight
    if first_binding:
        unassigned = set(range(len(obj.data.vertices))) - selected - supported
        if unassigned:
            if parent_bone in by_name and by_name[parent_bone]["lock_weight"]:
                raise HairBonesRigError(f"Unlock attachment group '{parent_bone}' before binding the hair Mesh.")
            touched.setdefault(parent_bone, {}).update({index: 1.0 for index in unassigned})
    for name in touched:
        if name in by_name and by_name[name]["lock_weight"]:
            raise HairBonesRigError(f"Unlock '{name}' before assigning this band's weights.")
    return touched, influencing


def _write_weights(obj, weights):
    for name, values in weights.items():
        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        if group.lock_weight:
            raise HairBonesRigError(f"Vertex Group '{name}' became locked during the build.")
        zeros = [index for index, value in values.items() if value <= EPSILON]
        if zeros:
            group.remove(zeros)
        for index, value in values.items():
            if value > EPSILON:
                group.add((index,), value, "REPLACE")


def _select_chains(context, armature, names):
    _mode(context, armature, "POSE")
    armature.show_in_front = True
    for pose in armature.pose.bones:
        pose.select = pose.name in names
    for name in names:
        bone = armature.data.bones[name]
        bone.hide = False
        for collection in bone.collections:
            collection.is_visible = True
    armature.data.bones.active = armature.data.bones[names[0]]


def build_hair_bones(context, obj, plans, *, bone_count=4, armature=None, parent_bone="", mirror_controls=False):
    """Build/select independent per-strand FK chains as one atomic batch.

    Return ``armature``, ``chains`` (signature/bones/created dictionaries),
    integer ``created``/``reused``, ``parent_bone`` and creation flags.
    The caller supplies an UNDO operator; this service never pushes extra undo.
    """
    _validate_object(context, obj)
    if context.mode not in {"OBJECT", "EDIT_MESH", "POSE"}:
        raise HairBonesRigError("Use Object Mode or select hair bands in Mesh Edit Mode.")
    if context.mode == "EDIT_MESH" and (context.edit_object is not obj or len(context.objects_in_mode) != 1):
        raise HairBonesRigError("Edit only the selected hair Mesh before generating bones.")
    if obj.data.shape_keys and obj.active_shape_key_index != 0:
        raise HairBonesRigError("Select the Basis Shape Key before capturing hair bones.")
    snapshot = _mesh_snapshot(obj)
    plans = _plan_copy(tuple(plans), snapshot, bone_count)
    record = _read_records(obj)
    if record and (mirror_controls or record.get("mirror_layout")):
        raise HairBonesRigError("Generate a new hair version to change full Mirror controls; existing versions are preserved.")
    armature, parent_bone, modifier = _resolve_rig(context, obj, armature, parent_bone, record)
    if armature:
        _validate_object(context, armature, armature=True)
    if obj.parent is not None and obj.parent is not armature:
        raise HairBonesRigError("Hair must be unparented or Object-parented to its bound Armature.")
    mirrors = _mirror_preflight(obj, armature, parent_bone, record, modifier,
                                full_mirror_build=mirror_controls)
    full_mirror = bool(mirror_controls and mirrors)
    if full_mirror:
        from . import hair_bones_mirror
        plans = hair_bones_mirror.prepare_plans(obj, plans)
    owned = _validate_owned(obj, armature, record, snapshot)
    new, selected_chains = [], []
    source_id = record["source_id"] if record else uuid.uuid4().hex
    reserved_names = set(armature.data.bones.keys()) if armature else set()
    for plan in plans:
        old = owned.get(plan["signature"])
        if old:
            if not _same_plan(old, plan):
                raise HairBonesRigError("This chain's count, guide, or member sections changed; generate a new comparison copy.")
            selected_chains.append({"signature": plan["signature"], "bones": tuple(old["bones"]), "created": False})
            continue
        for earlier in owned.values():
            overlap = set(plan["vertices"]) & set(earlier["vertices"])
            if overlap and not (overlap.issubset(_root_vertices(plan)) and overlap.issubset(_root_vertices(earlier))):
                raise HairBonesRigError("This band overlaps an existing hair chain; select its original complete band.")
        label = re.sub(r"[^\w .-]", "_", obj.name)[:28]
        chain_index = len(owned) + sum(not item.get("mirror_of") for item in new) + 1
        if plan.get("mirror_of"):
            original = next(item for item in new if item["signature"] == plan["mirror_of"])
            names = tuple(name[:-2] + "." + plan["mirror_side"] for name in original["names"])
        else:
            suffix = "." + plan["mirror_side"] if full_mirror else ""
            names = tuple(f"Hair {label} {chain_index:02d}.{index + 1:02d}{suffix}" for index in range(plan["count"]))
        if any(name in reserved_names or obj.vertex_groups.get(name) for name in names):
            raise HairBonesRigError("Generated hair bone names already exist; rename the conflicting data first.")
        reserved_names.update(names)
        plan["names"] = names
        plan["samples"] = _sample(plan["centers"], plan["distances"], plan["count"])
        new.append(plan)
        selected_chains.append({"signature": plan["signature"], "bones": names, "created": True})
    state = _context_snapshot(context, obj, armature)
    if not new:
        try:
            _select_chains(context, armature, tuple(name for chain in selected_chains for name in chain["bones"]))
            for collection in armature.data.collections_all:
                if collection.get(OWNER_KEY) == OWNER_VALUE:
                    collection.name = COLLECTION_NAME
        except Exception:
            _restore_context(context, obj, state)
            raise
        return {"armature": armature, "chains": tuple(selected_chains), "created": 0, "reused": len(plans),
                "parent_bone": parent_bone, "rig_created": False, "modifier_created": False}
    # Edit Mode group assignments are stored in BMesh; flush the mode only
    # after the complete geometric preflight, and restore it on any failure.
    old_json, old_rig, old_mirror = obj.get(RECORD_KEY), obj.get(RIG_KEY), obj.get(MIRROR_KEY)
    old_parent = (obj.parent, obj.parent_type, obj.parent_bone, obj.matrix_parent_inverse.copy(), obj.matrix_basis.copy())
    original_world = obj.matrix_world.copy()
    old_mirror_objects = [(m, m.mirror_object) for m in mirrors]
    old_mirror_groups = [(m, m.use_mirror_vertex_groups) for m in mirrors]
    old_modifier_order = tuple(obj.modifiers)
    created_bones, created_collection, created_modifier = [], None, None
    created_rig, created_data, old_groups, created_reference = None, None, None, None
    armature_in_front = armature.show_in_front if armature else False
    try:
        _mode(context, obj, "OBJECT")
        old_groups = _capture_vertex_groups(obj)
        if armature is None:
            created_data = bpy.data.armatures.new("Hair Rig")
            created_rig = bpy.data.objects.new("Hair Rig", created_data)
            (obj.users_collection[0] if obj.users_collection else context.scene.collection).objects.link(created_rig)
            armature = created_rig
            armature[OWNER_KEY] = OWNER_VALUE
            parent_bone = "Hair Anchor"
        elif not armature.data.bones[parent_bone].use_deform:
            # Never silently change an existing control's Deform flag.
            raise HairBonesRigError("The attachment bone must deform the unassigned hair vertices.")
        weights, influencing = _weight_plan(obj, armature, new, parent_bone, modifier is None, old_groups)
        _check_rest(context, armature if created_rig is None else None, influencing | ({parent_bone} if not created_rig else set()))
        to_arm = armature.matrix_world.inverted() @ obj.matrix_world
        for plan in new:
            plan["arm_samples"] = tuple(to_arm @ point for point in plan["samples"])
        mirror = armature.data.use_mirror_x
        try:
            armature.data.use_mirror_x = False
            _mode(context, armature, "EDIT")
            if created_rig:
                anchor = armature.data.edit_bones.new(parent_bone)
                created_bones.append(anchor.name)
                anchor.head = to_arm @ new[0]["centers"][0]
                anchor.tail = anchor.head + Vector((0, 0.05, 0))
                anchor.use_deform = True
                anchor.use_connect = False
            for plan in new:
                parent = armature.data.edit_bones[parent_bone]
                for index, name in enumerate(plan["names"]):
                    bone = armature.data.edit_bones.new(name)
                    created_bones.append(bone.name)
                    if bone.name != name:
                        raise HairBonesRigError("Blender renamed a generated bone; the batch was rolled back.")
                    bone.head, bone.tail = plan["arm_samples"][index:index + 2]
                    bone.parent = parent
                    bone.use_connect = index > 0
                    bone.use_deform = True
                    bone.roll = 0.0
                    parent = bone
            _mode(context, armature, "OBJECT")
        finally:
            armature.data.use_mirror_x = mirror
        collection = next((c for c in armature.data.collections_all if c.get(OWNER_KEY) == OWNER_VALUE), None)
        if collection is None:
            collection = armature.data.collections.new(COLLECTION_NAME)
            created_collection = collection
            collection[OWNER_KEY] = OWNER_VALUE
        for plan in new:
            for name in plan["names"]:
                bone = armature.data.bones[name]
                bone[OWNER_KEY] = OWNER_VALUE
                bone[SOURCE_KEY] = obj
                bone[SIGNATURE_KEY] = plan["signature"]
                collection.assign(bone)
        _write_weights(obj, weights)
        if full_mirror:
            for plan in new:
                for name in plan["names"]:
                    if obj.vertex_groups.get(name) is None:
                        obj.vertex_groups.new(name=name)
            mirrors[0].use_mirror_vertex_groups = True
        if modifier is None:
            created_modifier = obj.modifiers.new("Hair Armature", "ARMATURE")
            created_modifier.object = armature
            created_modifier.use_vertex_groups = True
            created_modifier.use_bone_envelopes = False
        if created_modifier is not None or full_mirror:
            # Blender may insert before a pinned last modifier rather than
            # append. Move the actual Armature by identity, including an
            # existing source binding when creating full Mirror controls.
            target_modifier = created_modifier if created_modifier is not None else modifier
            destination = tuple(obj.modifiers).index(mirrors[0]) + 1 if full_mirror else 0
            current_index = tuple(obj.modifiers).index(target_modifier)
            if current_index < destination:
                destination -= 1
            obj.modifiers.move(current_index, destination)
        if obj.parent is None:
            obj.parent = armature
            obj.parent_type = "OBJECT"
            obj.matrix_parent_inverse = armature.matrix_world.inverted() @ original_world @ old_parent[4].inverted()
        context.view_layer.update()
        if mirrors and old_mirror is None and not full_mirror:
            created_reference = _create_mirror_reference(context, obj, armature, parent_bone, mirrors)
            obj[MIRROR_KEY] = created_reference
        updated = dict(record) if record else {"version": 1, "source_id": source_id,
                                               "topology": snapshot["topology"], "chains": []}
        updated["chains"] = list(updated["chains"])
        updated["armature"] = armature.name
        updated["parent"] = parent_bone
        updated["mirrors"] = [m.name for m in mirrors]
        if full_mirror:
            updated["version"] = 3
            updated["mirror_layout"] = hair_bones_mirror.LAYOUT
        for plan in new:
            chain = {"signature": plan["signature"], "bones": list(plan["names"]),
                     "count": plan["count"], "requested_count": plan["requested_count"],
                     "vertices": list(plan["vertices"]), "layers": [list(layer) for layer in plan["layers"]],
                     "centers": [list(point) for point in plan["centers"]],
                     "rest": [_bone_state(armature.data.bones[name]) for name in plan["names"]],
                     "root_tip_rule": plan["root_tip_rule"]}
            if full_mirror:
                chain["mirror_side"] = plan["mirror_side"]
                if plan.get("mirror_of"):
                    chain["mirror_of"] = plan["mirror_of"]
            updated["chains"].append(chain)
        obj[RECORD_KEY] = json.dumps(updated, sort_keys=True, separators=(",", ":"))
        obj[RIG_KEY] = armature
        _validate_owned(obj, armature, updated, snapshot)
        _mirror_preflight(obj, armature, parent_bone, updated, created_modifier or modifier)
        _select_chains(context, armature, tuple(name for chain in selected_chains for name in chain["bones"]))
        context.view_layer.update()
        collection.name = COLLECTION_NAME
        from . import control_colors
        for plan in new:
            for name in plan['names']:
                control_colors.style(armature.pose.bones[name])
        return {"armature": armature, "chains": tuple(selected_chains), "created": len(new),
                "reused": len(plans) - len(new), "parent_bone": parent_bone,
                "rig_created": created_rig is not None, "modifier_created": created_modifier is not None}
    except Exception as exc:
        rollback_errors = []
        try:
            current = context.view_layer.objects.active
            if current is not None and current.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            if created_modifier is not None:
                obj.modifiers.remove(created_modifier)
            for mirror, original in old_mirror_objects:
                mirror.mirror_object = original
            for mirror, original in old_mirror_groups:
                mirror.use_mirror_vertex_groups = original
            for destination, original in enumerate(old_modifier_order):
                current_index = tuple(obj.modifiers).index(original)
                if current_index != destination:
                    obj.modifiers.move(current_index, destination)
            if created_reference is not None:
                bpy.data.objects.remove(created_reference, do_unlink=True)
            obj.parent, obj.parent_type, obj.parent_bone = old_parent[:3]
            obj.matrix_parent_inverse = old_parent[3]
            obj.matrix_basis = old_parent[4]
            if old_groups is not None:
                _restore_vertex_groups(obj, old_groups)
            if created_rig is None and armature is not None and created_bones:
                mirror = armature.data.use_mirror_x
                try:
                    armature.data.use_mirror_x = False
                    _mode(context, armature, "EDIT")
                    for name in reversed(created_bones):
                        bone = armature.data.edit_bones.get(name)
                        if bone is not None:
                            armature.data.edit_bones.remove(bone)
                    _mode(context, armature, "OBJECT")
                finally:
                    armature.data.use_mirror_x = mirror
            if created_collection is not None and created_rig is None:
                armature.data.collections.remove(created_collection)
            if created_rig is not None:
                bpy.data.objects.remove(created_rig, do_unlink=True)
            if created_data is not None and created_data.users == 0:
                bpy.data.armatures.remove(created_data)
            if armature is not None and created_rig is None:
                armature.show_in_front = armature_in_front
        except Exception as rollback_exc:
            rollback_errors.append(str(rollback_exc))
        for name, value in ((RECORD_KEY, old_json), (RIG_KEY, old_rig), (MIRROR_KEY, old_mirror)):
            if value is None:
                if name in obj:
                    del obj[name]
            else:
                obj[name] = value
        try:
            _restore_context(context, obj, state)
        except Exception as rollback_exc:
            rollback_errors.append(str(rollback_exc))
        suffix = " Rollback needs attention: " + "; ".join(rollback_errors) if rollback_errors else ""
        raise HairBonesRigError(str(exc) + suffix) from exc
