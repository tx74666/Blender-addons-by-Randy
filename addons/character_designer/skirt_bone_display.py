"""Artist-facing Bone Collections for one generated Dress."""
from . import skirt_rig as skirt

ROLE = "character_designer_dress_display_role"
MODE = "character_designer_dress_display_mode"
TITLES = {"DEF": "Deform Bones", "CTRL": "Manual Controls", "MCH": "Mechanism Bones", "PHYS": "Physics Bones"}


def roles(record):
    controls, deform, _ = skirt._bone_collection_layout(record)
    waist = record["controls"]["waist"]
    return {"DEF": deform | {waist}, "CTRL": controls,
            "MCH": {n for chain in record["chains"] for n in chain["manual"]},
            "PHYS": {n for chain in record["chains"] for n in chain["phys"]}}


def inspect(rig, record, root):
    """Accept the intact legacy group or the complete, exclusive role partition."""
    members = roles(record)
    children = [c for c in root.children if ROLE in c]
    groups = {c[ROLE]: c for c in children}
    if children:
        if (len(children) != len(members) or groups.keys() != members.keys() or root.bones
                or any(g.get(skirt.OWNER_KEY) or set(g.bones.keys()) != members[k] for k, g in groups.items())):
            raise skirt.SkirtRigError("The Dress Bone Collection roles are incomplete. Restore their generated memberships.")
        targets = groups
    else:
        if set(root.bones.keys()) != set.union(*members.values()):
            raise skirt.SkirtRigError("The Dress Bone Collection membership changed. Restore its generated bones.")
        targets = dict.fromkeys(members, root)
    expected = {}
    for role, names in members.items():
        for name in names:
            expected.setdefault(name, set()).add(targets[role])
    owned = {root, *groups.values()}
    for name, required in expected.items():
        assigned = set(rig.data.bones[name].collections)
        # A standalone rig can have additional artist groups. Shared ownership
        # has always required exact membership on the character armature.
        actual = assigned if skirt.is_shared(record) else assigned & owned
        if actual != required:
            raise skirt.SkirtRigError("A generated Dress bone changed collection. Preserve that assignment.")
    return groups


def root_for(rig, record):
    found = [c for c in rig.data.collections_all if c.get(skirt.OWNER_KEY) == record["owner"]]
    if len(found) != 1:
        raise skirt.SkirtRigError("Restore the generated Dress Bone Collection before organizing it.")
    return found[0]


def ensure(rig, record, manual, undo=None):
    """Migrate once. Later calls retain the artist's eyes and individual hide choices."""
    from . import bone_display
    root = root_for(rig, record)
    groups = inspect(rig, record, root)
    if groups:
        return groups
    members = roles(record)
    data = rig.data
    active = data.collections.active
    previous = []
    created = []
    def restore():
        for bone, hidden, pose_hidden in previous:
            root.assign(bone)
            bone.hide = hidden
            if pose_hidden is not None:
                rig.pose.bones[bone.name].hide = pose_hidden
        for group in reversed(created):
            data.collections.remove(group)
        data.collections.active = active

    try:
        for role, names in members.items():
            group = data.collections.new(TITLES[role], parent=root)
            created.append(group)
            groups[role] = group
            group[ROLE] = role
            group.is_visible = role == "DEF" or (role == "CTRL" and manual)
            for name in sorted(names):
                bone = data.bones[name]
                pb = rig.pose.bones[name]
                group.assign(bone)
                if root in tuple(bone.collections):
                    previous.append((bone, bone.hide, getattr(pb, "hide", None)))
                    root.unassign(bone)
                    bone_display._set_hidden(rig, bone, False)
        groups["CTRL"][MODE] = bool(manual)
        inspect(rig, record, root)
    except Exception:
        restore()
        raise
    finally:
        if data.collections.active != active:
            data.collections.active = active
    if undo is not None:
        undo.append(restore)
    return groups


def sync(context, source, *, explicit=False, undo=None):
    """Reconcile a saved mode choice without starting physics or overriding inspection."""
    from . import animation_physics, bone_display
    rig = source[skirt.RIG_KEY]
    if (context.mode not in {"OBJECT", "POSE"} or rig.mode == "EDIT" or rig.library or rig.override_library
            or rig.data.library or rig.data.override_library or source.library or source.override_library
            or not source.is_editable or bone_display.view_mode(rig)):
        return
    try:
        skirt._require_controls_for_setup(source, rig.parent)
    except skirt.SkirtRigError:
        return
    bone_display._controls_editable(context, rig)
    record = skirt.read_record(source)
    if record is None or source.get(skirt.OWNER_KEY) != record["owner"]:
        raise skirt.SkirtRigError("Restore the Dress source ownership before organizing its display.")
    manual = animation_physics._skirt_choice(source, record)["mode"] == "MANUAL"
    if not skirt.is_shared(record):
        if rig.get(skirt.OWNER_KEY) != record["owner"] or rig.data.get(skirt.OWNER_KEY) != record["owner"]:
            raise skirt.SkirtRigError("Restore the generated Dress armature ownership before organizing it.")
        if not any(g.get(skirt.OWNER_KEY) == record["owner"] for g in rig.data.collections_all):
            from . import bone_collections
            legacy = bone_collections.snapshot_layout(rig)
            try:
                skirt.migrate_skirt_bone_collections(rig)
            except Exception:
                if bone_collections.snapshot_layout(rig) != legacy:
                    bone_collections.restore_layout(rig, legacy)
                raise
            if undo is not None:
                undo.append(lambda: bone_collections.restore_layout(rig, legacy))
    groups = ensure(rig, record, manual, undo)
    controls = groups["CTRL"]
    if explicit or controls.get(MODE) != manual:
        if undo is not None:
            old = (controls.is_visible, controls.parent.is_visible, controls[MODE], rig.data.show_bone_custom_shapes)
            flags = [(b, b.hide, getattr(rig.pose.bones[b.name], "hide", None), b.hide_select) for b in controls.bones]
            def restore():
                controls.is_visible, controls.parent.is_visible, controls[MODE], rig.data.show_bone_custom_shapes = old
                for bone, hidden, pose_hidden, locked in flags:
                    bone.hide, bone.hide_select = hidden, locked
                    if pose_hidden is not None:
                        rig.pose.bones[bone.name].hide = pose_hidden
            undo.append(restore)
        controls.is_visible = manual
        controls[MODE] = manual
        if manual:
            controls.parent.is_visible = True
            rig.data.show_bone_custom_shapes = True
            for bone in controls.bones:
                bone_display._set_hidden(rig, bone, False)
                bone.hide_select = False


def current_character(context, main):
    from . import bone_display
    if main is None or main.type != "ARMATURE" or context.mode not in {"OBJECT", "POSE"}:
        return
    for rig, record in bone_display.dress_rigs(context, main):
        source = (rig.get(skirt.SHARED_SOURCES_KEY, {}).get(record["owner"])
                  if skirt.is_shared(record) else rig.get(skirt.SOURCE_KEY))
        if source is not None:
            sync(context, source)


def inspect_layer(rig, record, layer):
    """Set a requested exclusive Dress view using collection eyes."""
    groups = ensure(rig, record, layer == "CONTROLS")
    shown = {"CONTROLS": {"CTRL"}, "ORIGINAL": {"DEF"}, "MCH": {"MCH"}, "PHYS": {"PHYS"}}[layer]
    from . import bone_display
    for role, group in groups.items():
        group.is_visible = role in shown
        for bone in group.bones:
            # The waist serves both as a weighted root and a ring handle.
            bone_display._set_hidden(rig, bone, False)
            if group.is_visible:
                bone.hide_select = False
    groups["CTRL"].parent.is_visible = True


def remove_groups(rig, root):
    for child in tuple(root.children):
        if child.get(ROLE) in TITLES:
            rig.data.collections.remove(child)
    rig.data.collections.remove(root)
