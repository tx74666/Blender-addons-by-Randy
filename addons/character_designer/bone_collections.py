"""Reversible animation visibility; bone groups never change the rig's mode."""

import json
import bpy
from bpy.types import Operator

PROFILE_KEY = "character_designer_simple_bone_collections"
GROUP_KEY = "character_designer_simple_bone_group"
BODY_NAMES = ("Body", "Original")
INTERNAL_NAME = "_Internal"
OTHER_NAME = "_Other"
MANAGED_GROUPS = {"Body", "Original", "Controls", "Animation", INTERNAL_NAME, OTHER_NAME, "Hair"}
VIEW_KEY = "character_designer_bone_display_view_v1"
BACKUP_KEY = "character_designer_bone_collections_backup_v1"
BACKUP_REFS_KEY = "character_designer_bone_collections_backup_refs"
AUTO_KEY = "character_designer_auto_bone_collections"
NATIVE_ONLY_KEY = "character_designer_native_after_body_removal"
_FRAME_CACHE = {}
_MIGRATION_TIMER = globals().get("_MIGRATION_TIMER")
_MIGRATION_REGISTERED = False


def body_collection(armature):
    """Resolve the daily Body group while accepting saved legacy Animation."""
    if armature is None or armature.type != "ARMATURE":
        return None
    for name in ("Body", "Animation"):
        collection = armature.data.collections_all.get(name)
        if collection is not None and collection.get(GROUP_KEY) in {"Body", "Animation"}:
            return collection
    return None


def public_collections(armature):
    """Existing daily body groups only; independent dresses use their own rig."""
    if armature is None or armature.type != "ARMATURE":
        return ()
    return tuple(c for c in (body_collection(armature), armature.data.collections_all.get("Hair"),
                             armature.data.collections_all.get("Original")) if c is not None)


def _structural_edit_guard(armature):
    if VIEW_KEY in armature.data:
        raise ValueError("Restore the temporary bone display view before changing Bone Collections or rebuilding controls.")


def _native_body_names(armature, generated, hair_names):
    """Exclude identifiable machinery without guessing that ordinary bones are controls."""
    return {b.name for b in armature.data.bones
            if b.name not in generated and b.name not in hair_names
            and not b.get("character_designer_owner")
            and not (not b.use_deform and b.name.rsplit(":", 1)[-1].upper().startswith(("MCH-", "MCH_", "ORG-", "ORG_")))}


def _collection_record(collection):
    return {"name": collection.name,
            "parent": collection.parent.name if collection.parent else None,
            "visible": collection.is_visible, "solo": collection.is_solo,
            "expanded": collection.is_expanded,
            "properties": {key: _copy_property(collection[key]) for key in collection.keys()},
            "bones": sorted(bone.name for bone in collection.bones)}


def _copy_property(value):
    if hasattr(value, "to_dict"):
        return {key: _copy_property(item) for key, item in value.items()}
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


def snapshot_layout(armature):
    data = armature.data
    return {
        "collections": [_collection_record(c) for c in data.collections_all],
        "active": data.collections.active.name if data.collections.active else None,
        "profile": data.get(PROFILE_KEY),
        "hidden": {b.name: (b.hide, b.hide_select) for b in data.bones},
        "automatic": data.get(AUTO_KEY),
        "native_only": data.get(NATIVE_ONLY_KEY),
        "pose_hidden": {pb.name: pb.hide for pb in (armature.pose.bones if armature.pose else ()) if hasattr(pb, "hide")},
        "backup": data.get(BACKUP_KEY),
        "backup_refs": _copy_property(data.get(BACKUP_REFS_KEY, {})),
    }


def restore_layout(armature, snapshot):
    data = armature.data
    for collection in reversed(tuple(data.collections_all)):
        data.collections.remove(collection)
    for saved in snapshot["collections"]:
        parent = data.collections_all.get(saved["parent"]) if saved["parent"] else None
        collection = data.collections.new(saved["name"], parent=parent)
        for key, value in saved["properties"].items():
            collection[key] = value
        collection.is_visible = saved["visible"]
        collection.is_solo = saved["solo"]
        collection.is_expanded = saved["expanded"]
        for name in saved["bones"]:
            if name in data.bones:
                collection.assign(data.bones[name])
    data.collections.active = data.collections_all.get(snapshot["active"] or "")
    for name, flags in snapshot["hidden"].items():
        if name in data.bones:
            data.bones[name].hide, data.bones[name].hide_select = flags
    for name, hidden in snapshot.get("pose_hidden", {}).items():
        pb = armature.pose.bones.get(name) if armature.pose else None
        if pb is not None and hasattr(pb, "hide"):
            pb.hide = hidden
    if snapshot["profile"] is None:
        data.pop(PROFILE_KEY, None)
    else:
        data[PROFILE_KEY] = snapshot["profile"]
    for key, value in ((AUTO_KEY, snapshot.get("automatic")),
                       (NATIVE_ONLY_KEY, snapshot.get("native_only")),
                       (BACKUP_KEY, snapshot.get("backup")),
                       (BACKUP_REFS_KEY, snapshot.get("backup_refs"))):
        if value is None or value == {}:
            data.pop(key, None)
        else:
            data[key] = value


def _encode(value, references):
    """Keep custom-property ID pointers native, so Blender remaps them on reload."""
    if isinstance(value, bpy.types.ID):
        key = str(len(references))
        references[key] = value
        return {"id": key}
    if isinstance(value, dict):
        return {"group": {key: _encode(item, references) for key, item in value.items()}}
    if isinstance(value, (list, tuple)):
        return {"array": [_encode(item, references) for item in value]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("A Bone Collection custom property cannot be safely backed up.")


def _decode(value, references):
    if not isinstance(value, dict):
        return value
    if "id" in value:
        result = references.get(value["id"])
        if result is None:
            raise ValueError("An object referenced by the original bone collections was removed.")
        return result
    if "group" in value:
        return {key: _decode(item, references) for key, item in value["group"].items()}
    return [_decode(item, references) for item in value["array"]]


def has_layout_backup(armature):
    return armature is not None and armature.type == "ARMATURE" and BACKUP_KEY in armature.data


def _load_backup(armature):
    if not has_layout_backup(armature):
        raise ValueError("No previous Bone Collections layout was saved for this rig.")
    try:
        return _decode(json.loads(armature.data[BACKUP_KEY]),
                       armature.data.get(BACKUP_REFS_KEY, {}))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("The saved Bone Collections layout is invalid.") from exc


def _save_backup(armature, original):
    from . import hair_bones_rig as hair
    if has_layout_backup(armature):
        original = _load_backup(armature)["original"]
    # No recursive backup history: always retain the first artist layout.
    original = {key: value for key, value in original.items()
                if key not in {"backup", "backup_refs"}}
    managed = [_collection_record(c) for c in armature.data.collections_all
               if c.get(GROUP_KEY) in MANAGED_GROUPS
               or (c.name == "Hair" and c.get(hair.OWNER_KEY) == hair.OWNER_VALUE)]
    _write_backup(armature, {"original": original, "managed": managed})


def _write_backup(armature, backup):
    references = {}
    encoded = _encode(backup, references)
    armature.data[BACKUP_KEY] = json.dumps(encoded, separators=(",", ":"))
    if references:
        armature.data[BACKUP_REFS_KEY] = references
    else:
        armature.data.pop(BACKUP_REFS_KEY, None)


def restore_bone_collections(armature):
    """Restore the first layout while retaining later collections and live controls."""
    from . import hair_bones_rig as hair, limb_ik
    _structural_edit_guard(armature)
    if (armature.mode == "EDIT" or armature.library or armature.data.library
            or not armature.is_editable or armature.data.users != 1):
        raise ValueError("Choose a local, single-user Armature outside Edit Mode.")
    backup = _load_backup(armature)
    data = armature.data
    original = backup["original"]
    managed = {record["name"]: record for record in backup["managed"]}

    def signature(record):
        return {key: record[key] for key in ("name", "parent", "properties", "bones")}

    for name, saved in managed.items():
        current = data.collections_all.get(name)
        if current is None or signature(_collection_record(current)) != signature(saved):
            raise ValueError(f"Bone Collection '{name}' was edited; keep those edits or undo them before restoring the layout.")
    preserved = [c for c in data.collections_all if c.name not in managed]
    original_names = {record["name"] for record in original["collections"]}
    for collection in preserved:
        if collection.name in original_names:
            raise ValueError(f"New Bone Collection '{collection.name}' conflicts with the saved layout; rename it before restoring.")
        if collection.parent and collection.parent.name in managed:
            raise ValueError(f"New Bone Collection '{collection.name}' is parented inside the compact layout; move it outside before restoring.")

    def owner(record):
        props = record["properties"]
        if (props.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                and props.get(limb_ik.ROLE_KEY) == "CONTROL_COLLECTION"):
            return "controls"
        if props.get(hair.OWNER_KEY) == hair.OWNER_VALUE:
            return "hair"
        return None

    retained = {owner(record): data.collections_all[name] for name, record in managed.items()
                if owner(record)}
    used = set()
    before = snapshot_layout(armature)
    try:
        for collection in reversed(tuple(data.collections_all)):
            if collection.name in managed and collection not in retained.values():
                data.collections.remove(collection)
        # Temporary names prevent Blender from silently suffixing original names.
        for kind, collection in retained.items():
            collection.parent = None
            collection.name = f"__CD_Restore_{kind}"
        for saved in original["collections"]:
            kind = owner(saved)
            collection = retained.get(kind) if kind else None
            parent = data.collections_all.get(saved["parent"]) if saved["parent"] else None
            if collection is None:
                collection = data.collections.new(saved["name"], parent=parent)
                for key, value in saved["properties"].items():
                    collection[key] = value
                for name in saved["bones"]:
                    if name in data.bones:
                        collection.assign(data.bones[name])
            else:
                used.add(kind)
                collection.name = saved["name"]
                collection.parent = parent
                # Current rig ownership and assignments must survive later rebuilds.
                collection.pop(GROUP_KEY, None)
            collection.is_visible = saved["visible"]
            collection.is_solo = saved["solo"]
            collection.is_expanded = saved["expanded"]
        for kind, collection in retained.items():
            if kind not in used:
                collection.name = limb_ik.CONTROL_COLLECTION_NAME if kind == "controls" else "Hair"
                collection.pop(GROUP_KEY, None)
                collection.is_visible = True
                # A pre-build solo layout must not make the new animator controls inaccessible.
                collection.is_solo = any(c.is_solo for c in data.collections_all)
        # Reproduce the original root order; newly created artist collections follow it.
        roots = [data.collections_all[r["name"]] for r in original["collections"] if not r["parent"]]
        for index, collection in enumerate(roots):
            data.collections.move(list(data.collections).index(collection), index)
        data.collections.active = data.collections_all.get(original["active"] or "")
        if original["profile"] is None:
            data.pop(PROFILE_KEY, None)
        else:
            data[PROFILE_KEY] = original["profile"]
        data[AUTO_KEY] = 0
        data.pop(NATIVE_ONLY_KEY, None)
        data.pop(BACKUP_KEY, None)
        data.pop(BACKUP_REFS_KEY, None)
        return {"restored": len(original["collections"]), "preserved": len(preserved)}
    except Exception:
        restore_layout(armature, before)
        raise


def _assign_exact(collection, data, names):
    for bone in tuple(collection.bones):
        if bone.name not in names:
            collection.unassign(bone)
    for name in names:
        collection.assign(data.bones[name])


def _animation_names(armature, inventory, native, foot=None, torso=None, eyes=None, spine=None):
    from . import eye_controls, foot_controls, limb_ik, torso_controls, spine_ik_fk, root_control
    foot = foot if foot is not None else foot_controls.collection_members(armature)
    torso = torso if torso is not None else torso_controls.collection_members(armature)
    eyes = eyes if eyes is not None else eye_controls.collection_members(armature)
    spine = spine if spine is not None else spine_ik_fk.collection_members(armature)
    ik_rigs = [rig for rig in inventory["rigs"].values()
               if float(armature.pose.bones[rig["target"].name].get("ik_fk", 1.0)) > 0.0]
    # A partial blend needs both inputs visible; only full IK replaces FK.
    replaced = {name for rig in ik_rigs
                if float(armature.pose.bones[rig["target"].name].get("ik_fk", 1.0)) >= 1.0 - 1.0e-6
                for name in rig["chain"]}
    ik_ids = {rig["rig_id"] for rig in ik_rigs}
    animator = {b.name for b in inventory["bones"]
                if b.get(limb_ik.ROLE_KEY) == "MASTER"
                or (b.get(limb_ik.ROLE_KEY) in limb_ik.CONTROL_VISUAL_ROLES
                    and b.get(limb_ik.RIG_ID_KEY) in ik_ids)}
    guides = {b.name for b in inventory["bones"]
              if b.get(limb_ik.ROLE_KEY) == "POLE_LINE" and not b.hide
              and b.get(limb_ik.RIG_ID_KEY) in ik_ids}
    foot_ik = {name for target, names in foot["ik"].items()
               if float(armature.pose.bones[target].get("ik_fk", 1.0)) > 0.0
               for name in names}
    # Toe Bend remains useful in either mode; the reverse-foot roll is an IK input.
    # A legacy Stable heel stays in Controls but yields its animation display to Roll.
    return (((native - replaced - foot["replaced"] - torso["replaced"] - eyes["replaced"]) | animator | guides
             | foot["always"] | foot_ik | torso["always"] | eyes["always"] | spine["always"]
             | root_control.collection_members(armature)["always"])
            - foot.get("hidden_base", set()) - spine["hidden_fk"])


def show_original_after_removal(armature):
    """Keep one native-body view after the last generated Body control is gone.

    Idempotent repair for older saved scenes as well as the normal RGC path.
    Only owned, redundant collections are removed. No bone transforms, shape
    assignments, constraints or skin data are changed.
    """
    from . import body_setup, hair_bones_rig as hair, limb_ik
    if (armature is None or armature.type != "ARMATURE" or armature.mode == "EDIT"
            or armature.library or armature.data.library or not armature.is_editable
            or armature.data.users != 1):
        raise ValueError("Choose a local, single-user Armature outside Edit Mode.")
    _structural_edit_guard(armature)
    if body_setup.has_generated(armature):
        return {"changed": False, "removed_collections": [], "reason": "Generated Body controls still exist."}
    data = armature.data
    original = data.collections_all.get("Original")
    if original is not None and original.get(GROUP_KEY) != "Original":
        raise ValueError("Bone Collection 'Original' belongs to an artist-created group; keep or rename that group before repairing the native view.")
    hair_names = {bone.name for bone in data.bones if bone.get(hair.OWNER_KEY) == hair.OWNER_VALUE}
    hair_group = data.collections_all.get("Hair")
    if hair_group is not None:
        stack = [hair_group]
        while stack:
            current = stack.pop()
            hair_names.update(current.bones.keys())
            stack.extend(current.children)
    native = _native_body_names(armature, set(), hair_names)
    roles = {"Body", "Animation", "Controls", INTERNAL_NAME}
    redundant = {collection for collection in data.collections_all
                 if ((collection.get(GROUP_KEY) in roles)
                     or (collection.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                         and collection.get(limb_ik.ROLE_KEY) == "CONTROL_COLLECTION"))
                 and set(collection.bones.keys()) <= native}
    private_roots = {collection for collection in data.collections_all
                     if collection.get(GROUP_KEY) == OTHER_NAME and not collection.children}
    # A user-created child makes that parent a useful container. Retain both
    # instead of deleting or silently reparenting the artist's own hierarchy.
    while True:
        kept = {collection for collection in redundant
                if any(child not in redundant and child not in private_roots for child in collection.children)}
        if not kept:
            break
        redundant -= kept
    before = snapshot_layout(armature)
    try:
        removed = []
        for collection in private_roots:
            if collection.parent in redundant:
                collection.parent = None
        for collection in reversed(tuple(data.collections_all)):
            if collection in redundant:
                removed.append(collection.name)
                data.collections.remove(collection)
        if original is None:
            original = data.collections.new("Original")
        original[GROUP_KEY] = "Original"
        original.parent = None
        _assign_exact(original, data, native)
        original.is_visible = True
        # Preserve artist solo switches, while ensuring Original is visible
        # even when a preserved collection was soloed before RGC.
        original.is_solo = any(collection.is_solo for collection in data.collections_all)
        data.collections.active = original
        for name in native:
            data.bones[name].hide = False
            pb = armature.pose.bones.get(name)
            if pb is not None and hasattr(pb, "hide"):
                pb.hide = False
        data[PROFILE_KEY] = 2
        data[AUTO_KEY] = 1
        data[NATIVE_ONLY_KEY] = 1
        _save_backup(armature, before)
        _FRAME_CACHE.pop(armature.as_pointer(), None)
        return {"changed": snapshot_layout(armature) != before,
                "removed_collections": removed, "original_bones": len(native)}
    except Exception:
        restore_layout(armature, before)
        raise


def migrate_removed_body_layouts(scene=None, *, objects=None):
    """One-shot register/load repair of old owned layouts, never a frame policy.

    The marker prevents later refreshes from overriding the user's subsequent
    visibility choices. Ordinary artist collections and live setups are ignored.
    """
    from . import body_setup
    result = {"repaired": [], "skipped": []}
    scene = scene or bpy.context.scene
    objects = scene.objects if objects is None else objects
    for rig in tuple(objects):
        if (rig.name not in scene.objects or rig.type != "ARMATURE" or rig.mode == "EDIT" or rig.library or rig.data.library
                or not rig.is_editable or rig.data.users != 1 or rig.data.get(NATIVE_ONLY_KEY)
                or VIEW_KEY in rig.data):
            continue
        original = rig.data.collections_all.get("Original")
        body = body_collection(rig)
        if (body is None or original is None or original.get(GROUP_KEY) != "Original"
                or body_setup.has_generated(rig)):
            continue
        try:
            repaired = show_original_after_removal(rig)
            result["repaired"].append({"rig": rig.name, **repaired})
        except (ValueError, RuntimeError) as error:
            result["skipped"].append({"rig": rig.name, "reason": str(error)})
    return result


def simplify_body_collections(armature, *, compact=True, visibility=None, original_layout=None):
    """Expose Body/Hair/Original; keep owned implementation bones nested and hidden."""
    from . import eye_controls, foot_controls, hair_bones_rig as hair, limb_ik, torso_controls, spine_ik_fk, root_control

    if (armature.type != "ARMATURE" or armature.mode == "EDIT"
            or armature.library or armature.data.library or not armature.is_editable
            or armature.data.users != 1):
        raise ValueError("Choose a local, single-user Armature outside Edit Mode.")
    _structural_edit_guard(armature)
    if armature.data.get(NATIVE_ONLY_KEY):
        from . import body_setup
        if not body_setup.has_generated(armature):
            return show_original_after_removal(armature)
    data = armature.data
    inventory = limb_ik._validate_inventory(armature)
    foot = foot_controls.collection_members(armature)
    torso = torso_controls.collection_members(armature)
    eyes = eye_controls.collection_members(armature)
    spine = spine_ik_fk.collection_members(armature)
    generated = ({b.name for b in inventory["bones"]} | foot["generated"] | torso["generated"] | eyes["generated"]
                 | spine["generated"] | root_control.collection_members(armature)["generated"])
    owned = [c for c in data.collections_all
             if c.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
             and c.get(limb_ik.ROLE_KEY) == "CONTROL_COLLECTION"]
    if generated:
        if len(owned) != 1 or {b.name for b in owned[0].bones} != generated:
            raise ValueError("The generated Controls collection needs a valid complete rig.")
    elif owned:
        raise ValueError("An orphaned generated Controls collection needs repair first.")
    hair_names = {b.name for b in data.bones if b.get(hair.OWNER_KEY) == hair.OWNER_VALUE}
    hair_groups = [c for c in data.collections_all if c.get(hair.OWNER_KEY) == hair.OWNER_VALUE]
    if len(hair_groups) > 1:
        raise ValueError("Multiple owned Hair collections need repair first.")
    # An explicitly named native Hair group is also a reliable authored boundary.
    named_hair = data.collections_all.get("Hair")
    hair_group = hair_groups[0] if hair_groups else named_hair
    if hair_group is not None:
        stack = [hair_group]
        while stack:
            current = stack.pop()
            hair_names.update(b.name for b in current.bones)
            stack.extend(current.children)
    controls = owned[0] if owned else None
    body = body_collection(armature)
    other = data.collections_all.get(OTHER_NAME)
    if other is not None and other.get(GROUP_KEY) != OTHER_NAME:
        other = None
    original = data.collections_all.get("Original")
    if original is not None and original.get(GROUP_KEY) != "Original":
        original = None
    if not compact:
        for name, expected in (("Body", body), (INTERNAL_NAME, controls), (OTHER_NAME, other), ("Original", original)):
            existing = data.collections_all.get(name)
            if existing is not None and existing != expected:
                raise ValueError(f"Bone Collection '{name}' belongs to an artist-created group; organize again explicitly to fold it.")
        if hair_groups and named_hair is not None and named_hair != hair_group:
            raise ValueError("Bone Collection 'Hair' belongs to another group.")
    native = _native_body_names(armature, generated, hair_names)
    remaining = {b.name for b in data.bones} - generated - hair_names - native
    desired = {"Body": _animation_names(armature, inventory, native, foot, torso, eyes, spine),
               INTERNAL_NAME: generated, OTHER_NAME: remaining, "Original": native}
    before = snapshot_layout(armature)
    try:
        # Explicit organization folds old subdivisions. Routine rig lifecycle
        # updates retain later artist groups and the generated ownership object.
        keep = {c.as_pointer() for c in (controls, hair_group, body, original, other) if c is not None}
        for c in reversed(tuple(data.collections_all)):
            if c.as_pointer() in keep:
                c.parent = None
            elif compact or c.get(GROUP_KEY) in {"Controls", "Animation", INTERNAL_NAME, OTHER_NAME}:
                data.collections.remove(c)
        if body is None:
            body = data.collections.new("Body")
        body.name, body.parent = "Body", None
        if original is None:
            original = data.collections.new("Original")
        original.name, original.parent = "Original", None
        groups = {"Body": body, "Original": original}
        if controls is not None:
            controls.name, controls.parent = INTERNAL_NAME, body
            groups[INTERNAL_NAME] = controls
        # An unassigned bone is visible in Blender. Keep identifiable foreign
        # machinery in a private group without claiming our rig ownership.
        if remaining:
            if other is None:
                other = data.collections.new(OTHER_NAME)
            other.name, other.parent = OTHER_NAME, body
            groups[OTHER_NAME] = other
        elif other is not None:
            data.collections.remove(other)
        for name, collection in groups.items():
            collection[GROUP_KEY] = name
            _assign_exact(collection, data, desired[name])
            if compact:
                collection.is_visible = name == "Body"
                collection.is_solo = False
                collection.is_expanded = False
            else:
                previous_name = "Animation" if name == "Body" else "Controls" if name == INTERNAL_NAME else name
                flags = (visibility or {}).get(name, (visibility or {}).get(previous_name))
                if flags is not None:
                    collection.is_visible, collection.is_solo = flags
                elif name in {INTERNAL_NAME, OTHER_NAME}:
                    collection.is_visible, collection.is_solo = False, False
        if hair_names or hair_group is not None:
            if hair_group is None:
                hair_group = data.collections.new("Hair")
                hair_group[hair.OWNER_KEY] = hair.OWNER_VALUE
            hair_group.name, hair_group.parent = "Hair", None
            hair_group[GROUP_KEY] = "Hair"
            _assign_exact(hair_group, data, hair_names)
            if compact:
                hair_group.is_visible, hair_group.is_solo = True, False
        front = [body] + ([hair_group] if hair_group is not None else [])
        for index, collection in enumerate(front):
            data.collections.move(list(data.collections).index(collection), index)
        # Original stays last, including when later artist groups are retained.
        data.collections.move(list(data.collections).index(original), len(data.collections)-1)
        if compact:
            data.collections.active = body
        data[PROFILE_KEY] = 2
        data[AUTO_KEY] = 1
        _save_backup(armature, original_layout or before)
        _FRAME_CACHE.pop(armature.as_pointer(), None)
        return {name: len(names) for name, names in desired.items()}
    except Exception:
        restore_layout(armature, before)
        raise


def capture_managed_layout(armature):
    _structural_edit_guard(armature)
    return snapshot_layout(armature)


def finish_rig_edit(armature, previous, *, failed=False):
    """Call after the rig operator has completed its own commit or recovery."""
    if previous is None:
        return
    if failed:
        restore_layout(armature, previous)
    elif previous.get("automatic") == 0:
        return
    elif previous.get("profile"):
        from . import body_setup
        if not body_setup.has_generated(armature):
            show_original_after_removal(armature)
            return
        visibility = {c["name"]: (c["visible"], c["solo"]) for c in previous["collections"]}
        if previous.get("native_only"):
            visibility.update({"Body": (True, False), "Original": (False, False),
                               INTERNAL_NAME: (False, False)})
            # A preserved artist solo group must not hide newly generated Body.
            if any(c["solo"] for c in previous["collections"] if c["name"] != "Original"):
                visibility["Body"] = (True, True)
        simplify_body_collections(armature, compact=False, visibility=visibility)
        armature.data.pop(NATIVE_ONLY_KEY, None)
    elif not previous.get("profile"):
        from . import limb_ik
        if limb_ik._validate_inventory(armature)["bones"]:
            simplify_body_collections(armature, original_layout=previous)


@bpy.app.handlers.persistent
def _frame_visibility(scene, _depsgraph=None):
    """Follow keyed modes without changing the artist's visible/solo switches."""
    from . import eye_controls, foot_controls, hair_bones_rig as hair, limb_ik, torso_controls, spine_ik_fk, root_control
    for armature in scene.objects:
        if (armature.type != "ARMATURE" or armature.mode == "EDIT"
                or armature.library or armature.data.library or armature.data.users != 1
                or not armature.is_editable or not armature.data.get(PROFILE_KEY)
                or armature.data.get(AUTO_KEY) == 0 or not has_layout_backup(armature)
                or VIEW_KEY in armature.data):
            continue
        collection = body_collection(armature)
        if collection is None:
            continue
        modes = tuple((pb.name, ("IK" if pb.get("ik_fk", 1.0) >= 1.0 - 1.0e-6
                                else "FK" if pb.get("ik_fk", 1.0) <= 1.0e-6 else "BLEND")
                       if isinstance(pb.get("ik_fk", 1.0), (int, float)) else None)
                      for pb in armature.pose.bones
                      if (pb.bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                          and pb.bone.get(limb_ik.ROLE_KEY) in {"HAND_IK", "FOOT_IK"})
                      or (pb.bone.get(limb_ik.OWNER_KEY) == spine_ik_fk.OWNER_VALUE and "ik_fk" in pb))
        membership = tuple(sorted(collection.bones.keys()))
        key = armature.as_pointer()
        state = (modes, membership)
        if _FRAME_CACHE.get(key) == state:
            continue
        # Cache unsuccessful cases too: no repeated errors or retries per frame.
        _FRAME_CACHE[key] = state
        try:
            backup = _load_backup(armature)
            saved = next(record for record in backup["managed"] if record["name"] == collection.name)
            current = _collection_record(collection)
            if any(current[field] != saved[field] for field in ("parent", "properties", "bones")):
                # A user-repurposed collection is theirs; a frame change never overwrites it.
                continue
            inventory = limb_ik._validate_inventory(armature)
            foot = foot_controls.collection_members(armature)
            torso = torso_controls.collection_members(armature)
            eyes = eye_controls.collection_members(armature)
            spine = spine_ik_fk.collection_members(armature)
            generated = ({bone.name for bone in inventory["bones"]} | foot["generated"] | torso["generated"]
                         | eyes["generated"] | spine["generated"] | root_control.collection_members(armature)["generated"])
            hair_names = {b.name for b in armature.data.bones if b.get(hair.OWNER_KEY) == hair.OWNER_VALUE}
            hair_group = armature.data.collections_all.get("Hair")
            if hair_group is not None:
                stack = [hair_group]
                while stack:
                    current = stack.pop()
                    hair_names.update(b.name for b in current.bones)
                    stack.extend(current.children)
            native = _native_body_names(armature, generated, hair_names)
            desired = _animation_names(armature, inventory, native, foot, torso, eyes, spine)
            if desired != set(membership):
                _assign_exact(collection, armature.data, desired)
                # Update only our Body record, not artist edits to other collections.
                saved.update(_collection_record(collection))
                _write_backup(armature, backup)
            _FRAME_CACHE[key] = (modes, tuple(sorted(desired)))
        except Exception:
            # The explicit rig tools report validation errors. Playback stays uninterrupted.
            continue


def _native_migration_once():
    global _MIGRATION_TIMER
    _MIGRATION_TIMER = None
    if _MIGRATION_REGISTERED:
        try:
            from . import character_setup
            scene = bpy.context.scene
            rig = character_setup.preferred_rig(bpy.context)
            if rig is None or rig.name not in scene.objects:
                rig = bpy.context.view_layer.objects.active
            # Automatic refresh is scoped to the current character. Backup rigs
            # elsewhere in this scene or file are not migration targets.
            result = migrate_removed_body_layouts(scene, objects=(rig,) if rig else ())
            for entry in result["skipped"]:
                print(f'Character Designer: native display repair skipped {entry["rig"]}: {entry["reason"]}')
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as error:
            print(f'Character Designer: native display repair deferred: {error}')
    return None


def _schedule_native_migration():
    global _MIGRATION_TIMER
    if not _MIGRATION_REGISTERED:
        return
    if _MIGRATION_TIMER is not None and bpy.app.timers.is_registered(_MIGRATION_TIMER):
        bpy.app.timers.unregister(_MIGRATION_TIMER)
    _MIGRATION_TIMER = _native_migration_once
    bpy.app.timers.register(_MIGRATION_TIMER, first_interval=0.1)


@bpy.app.handlers.persistent
def _load_native_migration(_unused):
    _schedule_native_migration()


def register_handlers():
    global _MIGRATION_REGISTERED
    unregister_handlers()
    _MIGRATION_REGISTERED = True
    bpy.app.handlers.frame_change_post.append(_frame_visibility)
    bpy.app.handlers.load_post.append(_load_native_migration)
    # Blender restricts data access while an add-on is being registered. Run
    # once after registration, never in playback or undo/redo callbacks.
    _schedule_native_migration()


def unregister_handlers():
    global _MIGRATION_REGISTERED, _MIGRATION_TIMER
    _MIGRATION_REGISTERED = False
    if _MIGRATION_TIMER is not None and bpy.app.timers.is_registered(_MIGRATION_TIMER):
        bpy.app.timers.unregister(_MIGRATION_TIMER)
    _MIGRATION_TIMER = None
    for handler in tuple(bpy.app.handlers.frame_change_post):
        if (getattr(handler, "__module__", "") == __name__
                and getattr(handler, "__name__", "") == "_frame_visibility"):
            bpy.app.handlers.frame_change_post.remove(handler)
    for handler in tuple(bpy.app.handlers.load_post):
        if (getattr(handler, "__module__", "") == __name__
                and getattr(handler, "__name__", "") == "_load_native_migration"):
            bpy.app.handlers.load_post.remove(handler)
    _FRAME_CACHE.clear()


class CHARACTERDESIGNER_OT_simplify_bone_collections(Operator):
    bl_idname = "character_designer.simplify_bone_collections"
    bl_label = "Simplify Bone Collections"
    bl_description = "Organize Body and Hair with Original last; Dress stays on its own rig and internal controls stay hidden"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == "ARMATURE" and obj.mode != "EDIT"

    def execute(self, context):
        from . import skirt_rig
        armature = context.object
        affected = [armature]
        if not armature.get(skirt_rig.OWNER_KEY):
            # Only attached skirts of this character, never other scene rigs.
            affected += [obj for obj in context.scene.objects
                         if obj.type == "ARMATURE" and obj != armature
                         and obj.get(skirt_rig.OWNER_KEY)
                         and (obj.parent == armature or any(
                             getattr(c, "target", None) == armature for c in obj.constraints))]
        snapshots = [(obj, snapshot_layout(obj)) for obj in affected]
        try:
            for obj in affected:
                if obj.get(skirt_rig.OWNER_KEY):
                    skirt_rig.migrate_skirt_bone_collections(obj)
                else:
                    simplify_body_collections(obj)
        except Exception as exc:
            for obj, saved in snapshots:
                restore_layout(obj, saved)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Bone collections organized: Body uses available controls, Hair stays separate, and Original is last.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_restore_bone_collections(Operator):
    bl_idname = "character_designer.restore_bone_collections"
    bl_label = "Restore Bone Collections"
    bl_description = "Restore the layout before automatic organization; retain the generated rig and later artist collections"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return has_layout_backup(obj) and obj.mode != "EDIT"

    def execute(self, context):
        try:
            result = restore_bone_collections(context.object)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Restored {result['restored']} Bone Collections; generated controls remain available.")
        return {"FINISHED"}


BONE_COLLECTION_CLASSES = (CHARACTERDESIGNER_OT_simplify_bone_collections,
                           CHARACTERDESIGNER_OT_restore_bone_collections)
