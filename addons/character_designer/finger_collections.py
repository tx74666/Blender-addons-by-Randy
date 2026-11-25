"""Animator-facing finger collection and native left/right selection sets."""

NAME = "Fingers"
SIDE_KEY = "character_designer_finger_selection_side"


def members(rig, inventory, eligible):
    from . import control_pose_capture
    return {name: side for name, side in control_pose_capture._finger_names(rig, inventory).items()
            if name in eligible and side in {"L", "R"}}


def owned(body):
    from .bone_collections import GROUP_KEY
    groups = [c for c in body.children if c.get(GROUP_KEY) == NAME] if body else []
    if len(groups) > 1:
        raise ValueError("More than one Fingers collection belongs to Body.")
    return groups[0] if groups else None


def body_members(body, *, visible_only=False):
    names = set(body.bones.keys())
    group = owned(body)
    if group is not None and (not visible_only or group.is_visible_effectively):
        names.update(group.bones.keys())
    return names


def selection_snapshot(rig):
    return [(item.name, item.get(SIDE_KEY), list(item.bone_ids.keys()))
            for item in getattr(rig, "selection_sets", ()) if item.get(SIDE_KEY) in {"L", "R"}]


def _set_bones(item, names):
    item.bone_ids.clear()
    for name in names:
        item.bone_ids.add().name = name


def restore_selection(rig, saved):
    if selection_snapshot(rig) == saved:
        return
    desired = {name: (side, names) for name, side, names in saved}
    sets = rig.selection_sets
    for i in reversed(range(len(sets))):
        item = sets[i]
        if item.get(SIDE_KEY) in {"L", "R"} and item.name not in desired:
            sets.remove(i)
    for name, (side, names) in desired.items():
        item = sets.get(name)
        if item is None:
            item = sets.add()
            item.name = name
        item[SIDE_KEY] = side
        if list(item.bone_ids.keys()) != names:
            _set_bones(item, names)


def sync_selection(rig, fingers):
    sets = rig.selection_sets
    for side in ("L", "R"):
        names = sorted(name for name, owner in fingers.items() if owner == side)
        if not names:
            continue
        label = f"Fingers.{side}"
        if any((item.name == label or item.get(SIDE_KEY) == side)
               and set(item.bone_ids.keys()) == set(names) for item in sets):
            continue
        # Keep any user edits to a previously generated set. Reuse the exact
        # alternate on subsequent calls, including after saving and reopening.
        index = 1
        available = label
        while available in sets:
            available = f"{label} ({index})"
            index += 1
        item = sets.add()
        item.name = available
        item[SIDE_KEY] = side
        _set_bones(item, names)


def write(rig, body, group, fingers):
    from .bone_collections import GROUP_KEY
    if not fingers and group is None:
        return None
    if group is None:
        group = rig.data.collections.new(NAME, parent=body)
        group[GROUP_KEY] = NAME
    elif group.parent != body:
        group.parent = body
    existing = set(group.bones.keys())
    for name in existing - fingers.keys():
        group.unassign(rig.data.bones[name])
    for name in fingers.keys() - existing:
        group.assign(rig.data.bones[name])
    sync_selection(rig, fingers)
    return group
