"""Compact, opt-in bone groups; the rig graph and ownership remain unchanged."""

import bpy
from bpy.types import Operator

PROFILE_KEY = "character_designer_simple_bone_collections"
GROUP_KEY = "character_designer_simple_bone_group"
BODY_NAMES = ("Original", "Controls", "Animation")


def _copy_property(value):
    if hasattr(value, "to_dict"):
        return {key: _copy_property(item) for key, item in value.items()}
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


def snapshot_layout(armature):
    data = armature.data
    return {
        "collections": [{"name": c.name, "parent": c.parent.name if c.parent else None,
                         "visible": c.is_visible, "solo": c.is_solo,
                         "expanded": c.is_expanded,
                         "properties": {key: _copy_property(c[key]) for key in c.keys()},
                         "bones": [bone.name for bone in c.bones]}
                        for c in data.collections_all],
        "active": data.collections.active.name if data.collections.active else None,
        "profile": data.get(PROFILE_KEY),
        "hidden": {b.name: (b.hide, b.hide_select) for b in data.bones},
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


def _assign_exact(collection, data, names):
    for bone in tuple(collection.bones):
        if bone.name not in names:
            collection.unassign(bone)
    for name in names:
        collection.assign(data.bones[name])


def simplify_body_collections(armature, *, compact=True, visibility=None):
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
    replaced = {name for rig in inventory["rigs"].values() for name in rig["chain"]}
    animator = {b.name for b in inventory["bones"] if b.get(limb_ik.ROLE_KEY) in limb_ik.CONTROL_VISUAL_ROLES}
    guides = {b.name for b in inventory["bones"]
              if b.get(limb_ik.ROLE_KEY) == "POLE_LINE" and not b.hide}
    desired = {"Original": native, "Controls": generated,
               "Animation": (native - replaced) | animator | guides}
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
        return {name: len(names) for name, names in desired.items()}
    except Exception:
        restore_layout(armature, before)
        raise


def capture_managed_layout(armature):
    return snapshot_layout(armature) if armature.data.get(PROFILE_KEY) else None


def finish_rig_edit(armature, previous, *, failed=False):
    """Call after the rig operator has completed its own commit or recovery."""
    if previous is None:
        return
    if failed:
        restore_layout(armature, previous)
    else:
        visibility = {c["name"]: (c["visible"], c["solo"]) for c in previous["collections"]}
        simplify_body_collections(armature, compact=False, visibility=visibility)


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


BONE_COLLECTION_CLASSES = (CHARACTERDESIGNER_OT_simplify_bone_collections,)
