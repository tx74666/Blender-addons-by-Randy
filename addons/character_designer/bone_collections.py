"""Reversible animation visibility; bone groups never change the rig's mode."""

import json
import bpy
from bpy.types import Operator

PROFILE_KEY = "character_designer_simple_bone_collections"
GROUP_KEY = "character_designer_simple_bone_group"
BODY_NAMES = ("Original", "Controls", "Animation")
BACKUP_KEY = "character_designer_bone_collections_backup_v1"
BACKUP_REFS_KEY = "character_designer_bone_collections_backup_refs"
AUTO_KEY = "character_designer_auto_bone_collections"
_FRAME_CACHE = {}


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
    if snapshot["profile"] is None:
        data.pop(PROFILE_KEY, None)
    else:
        data[PROFILE_KEY] = snapshot["profile"]
    for key, value in ((AUTO_KEY, snapshot.get("automatic")),
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
               if c.get(GROUP_KEY) in BODY_NAMES
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


def _animation_names(armature, inventory, native):
    from . import limb_ik
    ik_rigs = [rig for rig in inventory["rigs"].values()
               if float(armature.pose.bones[rig["target"].name].get("ik_fk", 1.0)) > 0.0]
    replaced = {name for rig in ik_rigs for name in rig["chain"]}
    ik_ids = {rig["rig_id"] for rig in ik_rigs}
    animator = {b.name for b in inventory["bones"]
                if b.get(limb_ik.ROLE_KEY) == "MASTER"
                or (b.get(limb_ik.ROLE_KEY) in limb_ik.CONTROL_VISUAL_ROLES
                    and b.get(limb_ik.RIG_ID_KEY) in ik_ids)}
    guides = {b.name for b in inventory["bones"]
              if b.get(limb_ik.ROLE_KEY) == "POLE_LINE" and not b.hide
              and b.get(limb_ik.RIG_ID_KEY) in ik_ids}
    return (native - replaced) | animator | guides


def simplify_body_collections(armature, *, compact=True, visibility=None, original_layout=None):
    """Use current tagged IK chains for per-limb native fallback, never name guesses."""
    from . import hair_bones_rig as hair, limb_ik

    if (armature.type != "ARMATURE" or armature.mode == "EDIT"
            or armature.library or armature.data.library or not armature.is_editable
            or armature.data.users != 1):
        raise ValueError("Choose a local, single-user Armature outside Edit Mode.")
    data = armature.data
    inventory = limb_ik._validate_inventory(armature)
    generated = {b.name for b in inventory["bones"]}
    owned = [c for c in data.collections_all
             if c.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
             and c.get(limb_ik.ROLE_KEY) == "CONTROL_COLLECTION"]
    if generated:
        # Collection ownership is also the remove/rebuild contract, including helpers.
        if len(owned) != 1 or {b.name for b in owned[0].bones} != generated:
            raise ValueError("The generated Controls collection needs a valid complete rig.")
    elif owned:
        raise ValueError("An orphaned generated Controls collection needs repair first.")
    hair_names = {b.name for b in data.bones if b.get(hair.OWNER_KEY) == hair.OWNER_VALUE}
    hair_groups = [c for c in data.collections_all if c.get(hair.OWNER_KEY) == hair.OWNER_VALUE]
    if len(hair_groups) > 1:
        raise ValueError("Multiple owned Hair collections need repair first.")
    if not compact:
        for name in BODY_NAMES:
            existing = data.collections_all.get(name)
            if (existing is not None and existing not in owned
                    and existing.get(GROUP_KEY) != name):
                raise ValueError(f"Bone Collection '{name}' belongs to an artist-created group; keep it or organize again explicitly.")
        existing_hair = data.collections_all.get("Hair")
        if hair_names and existing_hair is not None and existing_hair not in hair_groups:
            raise ValueError("Bone Collection 'Hair' belongs to another group.")
    native = {b.name for b in data.bones} - generated - hair_names
    desired = {"Original": native, "Controls": generated,
               "Animation": _animation_names(armature, inventory, native)}
    before = snapshot_layout(armature)
    try:
        controls = owned[0] if owned else None
        hair_group = hair_groups[0] if hair_groups else None
        # Explicit organization folds old subdivisions. Later lifecycle updates
        # leave any new artist-created groups alone.
        keep = {c.as_pointer() for c in (controls, hair_group) if c is not None}
        for c in reversed(tuple(data.collections_all)):
            if c.as_pointer() in keep:
                c.parent = None
            elif compact or c.get(GROUP_KEY) == "Controls":
                data.collections.remove(c)
        if controls is not None:
            controls.name = "Controls"
        for name in BODY_NAMES:
            collection = controls if name == "Controls" and controls else data.collections_all.get(name)
            if collection is None:
                collection = data.collections.new(name)
            collection[GROUP_KEY] = name
            _assign_exact(collection, data, desired[name])
            if compact:
                collection.is_visible = name == "Animation"
                collection.is_solo = False
            elif visibility and name in visibility:
                collection.is_visible, collection.is_solo = visibility[name]
        if hair_names:
            if hair_group is None:
                hair_group = data.collections.new("Hair")
                hair_group[hair.OWNER_KEY] = hair.OWNER_VALUE
            hair_group.name = "Hair"
            _assign_exact(hair_group, data, hair_names)
        # Keep the rig's own hide/select flags, including visible non-selectable
        # Pole connector guides. Collection organization never changes the rig.
        order = [data.collections_all[name] for name in BODY_NAMES]
        if hair_group is not None:
            order.append(hair_group)
        for index, collection in enumerate(order):
            data.collections.move(list(data.collections).index(collection), index)
        if compact:
            data.collections.active = data.collections_all["Animation"]
        data[PROFILE_KEY] = 1
        data[AUTO_KEY] = 1
        _save_backup(armature, original_layout or before)
        return {name: len(names) for name, names in desired.items()}
    except Exception:
        restore_layout(armature, before)
        raise


def capture_managed_layout(armature):
    return snapshot_layout(armature)


def finish_rig_edit(armature, previous, *, failed=False):
    """Call after the rig operator has completed its own commit or recovery."""
    if previous is None:
        return
    if failed:
        restore_layout(armature, previous)
    elif previous.get("automatic") == 0:
        return
    elif not previous.get("profile"):
        from . import limb_ik
        if limb_ik._validate_inventory(armature)["bones"]:
            simplify_body_collections(armature, original_layout=previous)
    else:
        visibility = {c["name"]: (c["visible"], c["solo"]) for c in previous["collections"]}
        simplify_body_collections(armature, compact=False, visibility=visibility)


@bpy.app.handlers.persistent
def _frame_visibility(scene, _depsgraph=None):
    """Follow keyed modes without changing the artist's visible/solo switches."""
    from . import hair_bones_rig as hair, limb_ik
    for armature in scene.objects:
        if (armature.type != "ARMATURE" or armature.mode == "EDIT"
                or armature.library or armature.data.library or armature.data.users != 1
                or not armature.is_editable or not armature.data.get(PROFILE_KEY)
                or armature.data.get(AUTO_KEY) == 0 or not has_layout_backup(armature)):
            continue
        collection = armature.data.collections_all.get("Animation")
        if collection is None or collection.get(GROUP_KEY) != "Animation":
            continue
        modes = tuple((pb.name, pb.get("ik_fk", 1.0) > 0.0
                       if isinstance(pb.get("ik_fk", 1.0), (int, float)) else None)
                      for pb in armature.pose.bones
                      if pb.bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                      and pb.bone.get(limb_ik.ROLE_KEY) in {"HAND_IK", "FOOT_IK"})
        membership = tuple(sorted(collection.bones.keys()))
        key = armature.as_pointer()
        state = (modes, membership)
        if _FRAME_CACHE.get(key) == state:
            continue
        # Cache unsuccessful cases too: no repeated errors or retries per frame.
        _FRAME_CACHE[key] = state
        try:
            backup = _load_backup(armature)
            saved = next(record for record in backup["managed"] if record["name"] == "Animation")
            current = _collection_record(collection)
            if any(current[field] != saved[field] for field in ("parent", "properties", "bones")):
                # A user-repurposed collection is theirs; a frame change never overwrites it.
                continue
            inventory = limb_ik._validate_inventory(armature)
            generated = {bone.name for bone in inventory["bones"]}
            native = {bone.name for bone in armature.data.bones
                      if bone.name not in generated and bone.get(hair.OWNER_KEY) != hair.OWNER_VALUE}
            desired = _animation_names(armature, inventory, native)
            if desired != set(membership):
                _assign_exact(collection, armature.data, desired)
                # Update only our Animation record, not artist edits to other collections.
                saved.update(_collection_record(collection))
                _write_backup(armature, backup)
            _FRAME_CACHE[key] = (modes, tuple(sorted(desired)))
        except Exception:
            # The explicit rig tools report validation errors. Playback stays uninterrupted.
            continue


def register_handlers():
    unregister_handlers()
    bpy.app.handlers.frame_change_post.append(_frame_visibility)


def unregister_handlers():
    for handler in tuple(bpy.app.handlers.frame_change_post):
        if (getattr(handler, "__module__", "") == __name__
                and getattr(handler, "__name__", "") == "_frame_visibility"):
            bpy.app.handlers.frame_change_post.remove(handler)
    _FRAME_CACHE.clear()


class CHARACTERDESIGNER_OT_simplify_bone_collections(Operator):
    bl_idname = "character_designer.simplify_bone_collections"
    bl_label = "Simplify Bone Collections"
    bl_description = "Original, Controls, and Animation with native fallback; Hair and Skirt stay separate"
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
        self.report({"INFO"}, "Bone collections simplified; Animation uses controls where available and original bones elsewhere.")
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
