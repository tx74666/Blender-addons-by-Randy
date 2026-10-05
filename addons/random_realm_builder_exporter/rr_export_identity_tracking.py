"""Persistent export ownership outside the Objects copied by Blender."""

from contextlib import contextmanager
import hashlib

import bpy

REGISTRY_PROP = "rr_export_identity_owners"
CARRIER_MARKER = "rr_export_identity_registry"
STABLE_PROP = "rr_export_stable_id"
IDENTITY_PROPS = (STABLE_PROP, "rr_export_last_id", "rr_export_previous_ids",
                  "rr_export_asset_id_override", "rr_export_follow_object_name",
                  "rr_reference_marked", "rr_reference_stable_id")


def _key(stable_id):
    return hashlib.sha256(stable_id.casefold().encode("utf-8")).hexdigest()[:32]


def _live_owner(obj, stable_id):
    try:
        return (isinstance(obj, bpy.types.Object)
                and bpy.data.objects.get(obj.name) is obj and bool(obj.users_scene)
                and str(obj.get(STABLE_PROP, "") or "").strip().casefold() == stable_id.casefold())
    except ReferenceError:
        return False


def _editable_carrier(carrier):
    return carrier.is_editable and carrier.library is None


def _carrier(scene, create=False):
    registry = scene.get(REGISTRY_PROP)
    carrier = registry.get("carrier") if hasattr(registry, "get") else None
    if isinstance(carrier, bpy.types.Object) and carrier.get(CARRIER_MARKER):
        if create and not _editable_carrier(carrier):
            raise RuntimeError("A linked registry retains this export identity; resolve it before relinking.")
        return carrier
    if not create:
        return None
    carrier = bpy.data.objects.new("RR Export Identity Registry", None)
    carrier[CARRIER_MARKER] = True
    carrier.hide_render = True
    carrier.hide_viewport = True
    carrier.hide_select = True
    # A data-only carrier has no collection link and no evaluated scene presence.
    # Its muted constraint targets are native weak ID references: deletion clears
    # them without retaining the authored Object as an orphan datablock.
    if REGISTRY_PROP not in scene:
        scene[REGISTRY_PROP] = {}
    scene[REGISTRY_PROP]["carrier"] = carrier
    return carrier


def _row_owner(scene, row):
    carrier = _carrier(scene)
    constraint = carrier.constraints.get(str(row.get("constraint", ""))) if carrier else None
    return constraint.target if constraint and constraint.type == "COPY_LOCATION" else None


def ownership(stable_id):
    """Return (record exists, live Object owner); deleted owners stay tombstones."""
    stable_id = str(stable_id or "").strip()
    if not stable_id:
        return False, None
    known, owners = False, set()
    for scene in bpy.data.scenes:
        registry = scene.get(REGISTRY_PROP)
        if not hasattr(registry, "get"):
            continue
        row = registry.get(_key(stable_id))
        if not hasattr(row, "get") or str(row.get("stableId", "")).casefold() != stable_id.casefold():
            continue
        known = True
        owner = _row_owner(scene, row)
        if _live_owner(owner, stable_id):
            owners.add(owner)
    if len(owners) > 1:
        raise RuntimeError("Export identity ownership conflicts across scenes. Use Diagnose / Relink.")
    return known, next(iter(owners), None)


def remember(root, *, replace=False):
    """Remember an actual Object pointer; names and session UID are not owners."""
    stable_id = str(root.get(STABLE_PROP, "") or "").strip()
    if not stable_id:
        return False
    if not replace:
        known, owner = ownership(stable_id)
        if owner is root or known:
            return False
    scenes = [scene for scene in root.users_scene if scene.is_editable and scene.library is None]
    if not scenes:
        raise RuntimeError("Keep the export object in an editable local scene before assigning its identity.")
    scene = bpy.context.scene if bpy.context.scene in scenes else scenes[0]
    existing_carrier = _carrier(scene)
    if existing_carrier is not None and not _editable_carrier(existing_carrier):
        raise RuntimeError("A linked registry retains this export identity; resolve it before relinking.")
    # Explicit repair/relink can replace invalid or previously selected owners.
    # Remove the same row from other editable scenes so a duplicated Scene does
    # not leave two competing pointers after an explicit ownership choice.
    if replace:
        replacements = []
        for peer_scene in bpy.data.scenes:
            registry = peer_scene.get(REGISTRY_PROP)
            if hasattr(registry, "get") and _key(stable_id) in registry:
                if not peer_scene.is_editable or peer_scene.library is not None:
                    raise RuntimeError("A linked scene retains this export identity; resolve it before relinking.")
                carrier = _carrier(peer_scene)
                if carrier is not None and not _editable_carrier(carrier):
                    raise RuntimeError("A linked registry retains this export identity; resolve it before relinking.")
                row = registry[_key(stable_id)]
                constraint = carrier.constraints.get(str(row.get("constraint", ""))) if carrier else None
                pointer = constraint.as_pointer() if constraint is not None else None
                replacements.append((registry, carrier, constraint, pointer))
        removed_constraints = set()
        for registry, carrier, constraint, pointer in replacements:
            if pointer is not None and pointer not in removed_constraints:
                carrier.constraints.remove(constraint)
                removed_constraints.add(pointer)
            del registry[_key(stable_id)]
    carrier = _carrier(scene, create=True)
    constraint = carrier.constraints.new("COPY_LOCATION")
    constraint.name = f"Owner {len(carrier.constraints):02d}"
    constraint.mute = True
    constraint.influence = 0.0
    constraint.target = root
    scene[REGISTRY_PROP][_key(stable_id)] = {"stableId": stable_id, "constraint": constraint.name}
    return True


def bootstrap(objects):
    """Register unambiguous older identities. Shared legacy IDs need a choice."""
    by_id = {}
    for obj in objects:
        identity = str(obj.get(STABLE_PROP, "") or "").strip()
        if identity:
            by_id.setdefault(identity.casefold(), []).append(obj)
    for peers in by_id.values():
        if len(peers) == 1:
            root = peers[0]
            if root.is_editable and root.library is None and root.users_scene:
                remember(root)


def _plain(value):
    if isinstance(value, bpy.types.ID):
        return value
    if hasattr(value, "items"):
        # Removed ID references become None; omission preserves the tombstone.
        return {key: _plain(item) for key, item in value.items() if item is not None}
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


@contextmanager
def transaction(objects):
    """Restore object metadata and scene ownership together on any failure."""
    objects = list(dict.fromkeys(objects))
    snapshots = [(obj, {key: _plain(obj[key]) for key in IDENTITY_PROPS if key in obj}) for obj in objects
                 if obj.is_editable and obj.library is None]
    scenes = [(scene, _plain(scene[REGISTRY_PROP]) if REGISTRY_PROP in scene else None)
              for scene in bpy.data.scenes if scene.is_editable and scene.library is None]
    carriers = {obj: [(item.name, item.type, item.target, item.mute, item.influence)
                      for item in obj.constraints]
                for obj in bpy.data.objects if obj.get(CARRIER_MARKER) and _editable_carrier(obj)}
    try:
        yield
    except Exception:
        for obj, values in snapshots:
            for key in IDENTITY_PROPS:
                if key in obj and key not in values:
                    del obj[key]
            for key, value in values.items():
                obj[key] = value
        for scene, values in scenes:
            if values is None:
                if REGISTRY_PROP in scene:
                    del scene[REGISTRY_PROP]
            else:
                scene[REGISTRY_PROP] = values
        for carrier, constraints in carriers.items():
            for item in list(carrier.constraints):
                carrier.constraints.remove(item)
            for name, kind, target, muted, influence in constraints:
                item = carrier.constraints.new(kind)
                item.name, item.target, item.mute, item.influence = name, target, muted, influence
        for obj in list(bpy.data.objects):
            if obj.get(CARRIER_MARKER) and _editable_carrier(obj) and obj not in carriers:
                bpy.data.objects.remove(obj, do_unlink=True)
        raise
