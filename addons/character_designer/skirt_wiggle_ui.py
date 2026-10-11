"""Daily Dress motion choices and owned-bone display tools."""

import bpy
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator

from . import skirt_rig

ADVANCED_PROPERTY = "character_designer_dress_wiggle_advanced"


def _workflow():
    from . import skirt
    return skirt


def _motion():
    from . import skirt_wiggle
    return skirt_wiggle


def _display_owner(source):
    rig = source[skirt_rig.RIG_KEY]
    owner = rig.get(skirt_rig.ORIGINAL_DISPLAY_OWNER_KEY)
    if owner is not None:
        return owner
    attachment = skirt_rig.attachment_status(source)
    return attachment["character"] if attachment["attached"] else rig


def _original(source):
    from . import body_original_mode
    return body_original_mode.active(_display_owner(source))


def _original_supported(source):
    from . import bone_collections, body_original_mode
    main = _display_owner(source)
    group = main.data.collections_all.get("Original")
    return (body_original_mode.active(main)
            or group is not None and group.get(bone_collections.GROUP_KEY) == "Original")


def _ready(context):
    workflow = _workflow()
    return (context.mode in {"OBJECT", "POSE"} and workflow._idle(context)
            and workflow._has_setup(context))


def _show_layer(context, source, layer):
    """Change only this Dress's owned visibility, never its solver state."""
    from . import bone_display
    record = skirt_rig.read_record(source)
    if record is None:
        raise ValueError("Create the Dress controls first.")
    rig = source[skirt_rig.RIG_KEY]
    bone_display._editable(rig)
    controls, deform, mechanism = skirt_rig._bone_collection_layout(record)
    owned = controls | deform | mechanism
    if layer == "CONTROLS":
        visible = controls
    elif layer == "ORIGINAL":
        visible = {name for name in deform | {record["controls"]["waist"]}
                   if rig.data.bones[name].use_deform}
    elif layer in {"MCH", "PHYS"}:
        role = "manual" if layer == "MCH" else "phys"
        visible = {name for chain in record["chains"] for name in chain[role]}
    else:
        raise ValueError("Choose Controls, Original, MCH or PHYS.")
    if any(name not in rig.data.bones for name in owned):
        raise ValueError("The Dress bones changed; undo that edit first.")
    saved = bone_display._snapshot(rig)
    try:
        bone_display._reveal_object(rig)
        # A solo collection elsewhere in this armature otherwise overrides the
        # requested layer. Its flag is included in the reversible snapshot.
        for collection in rig.data.collections_all:
            collection.is_solo = False
        for name in owned:
            bone = rig.data.bones[name]
            bone_display._set_hidden(rig, bone, name not in visible)
            if name in visible:
                bone.hide_select = False
                for collection in bone.collections:
                    while collection is not None:
                        collection.is_visible = True
                        collection = collection.parent
        if layer == "CONTROLS":
            rig.data.show_bone_custom_shapes = True
        bone_display._completed(_display_owner(source))
    except Exception:
        bone_display._restore(rig, saved)
        raise


def _bones_visible(rig, names):
    solo = any(collection.is_solo for collection in rig.data.collections_all)

    def collection_visible(collection):
        if not collection.is_visible_effectively:
            return False
        if not solo:
            return True
        while collection is not None:
            if collection.is_solo:
                return True
            collection = collection.parent
        return False

    return bool(names) and all(
        not getattr(rig.pose.bones[name], "hide", rig.data.bones[name].hide)
        and not rig.data.bones[name].hide
        and (not rig.data.bones[name].collections
             or any(collection_visible(group) for group in rig.data.bones[name].collections))
        for name in names)


def _layer_visible(source, record, layer):
    rig = source[skirt_rig.RIG_KEY]
    role = "manual" if layer == "MCH" else "phys"
    names = [name for chain in record["chains"] for name in chain[role]]
    return _bones_visible(rig, names)


def _controls_visible(source, record):
    rig = source[skirt_rig.RIG_KEY]
    controls, deform, mechanism = skirt_rig._bone_collection_layout(record)
    return (_bones_visible(rig, controls)
            and all(rig.data.bones[name].hide
                    and getattr(rig.pose.bones[name], "hide", rig.data.bones[name].hide)
                    for name in (deform | mechanism) - controls))


class CHARACTERDESIGNER_OT_dress_wiggle_mode(Operator):
    bl_idname = "character_designer.dress_wiggle_mode"
    bl_label = "Dress Motion"
    bl_description = "Choose automatic Wiggle Bones motion or keep the current pose for manual shaping"
    bl_options = {"REGISTER", "UNDO"}
    mode: EnumProperty(items=(("AUTOMATIC", "Automatic", "Start Wiggle Bones on the Dress physics layer"),
                              ("MANUAL", "Manual", "Pause automatic motion and keep the current Dress pose")))

    @classmethod
    def poll(cls, context):
        try:
            return _ready(context) and not _original(_workflow()._source(context))
        except (ValueError, RuntimeError, KeyError, ReferenceError):
            return False

    def execute(self, context):
        workflow = _workflow()
        source = workflow._source(context)
        try:
            message = _motion().set_mode(context, source, self.mode)
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as error:
            workflow._report(self, context, str(error), error=True)
            return {"CANCELLED"}
        # A successful pause remains successful even if its optional display
        # handoff needs recovery. It never changes the artist's selection.
        try:
            _show_layer(context, source, "CONTROLS")
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as error:
            self.report({"WARNING"}, "Motion changed; Dress display needs attention: " + str(error))
        workflow._report(self, context, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_dress_wiggle_display(Operator):
    bl_idname = "character_designer.dress_wiggle_display"
    bl_label = "Dress Display"
    bl_description = "Show the selected Dress layer while keeping its pose and automatic motion state"
    bl_options = {"REGISTER", "UNDO"}
    layer: EnumProperty(items=(("CONTROLS", "Controls", "Show waist, hem and manual wire handles"),
                               ("ORIGINAL", "Original", "Edit weighted Dress bones while keeping the current pose"),
                               ("MCH", "MCH", "Inspect only the Dress manual mechanism bones"),
                               ("PHYS", "PHYS", "Inspect only the Dress physics bones")))

    @classmethod
    def poll(cls, context):
        try:
            return _ready(context)
        except (ValueError, RuntimeError, KeyError, ReferenceError):
            return False

    def execute(self, context):
        workflow = _workflow()
        source = workflow._source(context)
        try:
            from . import body_original_mode, bone_display
            main = _display_owner(source)
            if self.layer == "CONTROLS":
                if body_original_mode.active(main):
                    body_original_mode.leave(context, main)
                elif bone_display.view_mode(main):
                    bone_display.restore_view(main)
            elif self.layer == "ORIGINAL":
                if not _original_supported(source):
                    raise ValueError("Original needs an attached Main Rig with organized Bone Collections.")
                _motion().suspend(context, source=source,
                                  reason="Original editing paused automatic Dress motion.")
                if not body_original_mode.active(main):
                    body_original_mode.enter(context, main)
                else:
                    body_original_mode.ensure_dress_editable(context, main)
            elif body_original_mode.active(main):
                raise ValueError("Choose Controls before inspecting MCH or PHYS.")
            _show_layer(context, source, self.layer)
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as error:
            workflow._report(self, context, str(error), error=True)
            return {"CANCELLED"}
        workflow._report(self, context, "Dress " + self.layer.title() + " shown.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_dress_wiggle_details(Operator):
    bl_idname = "character_designer.dress_wiggle_details"
    bl_label = "Advanced Inspection"
    bl_description = "Expand or collapse Dress mechanism and physics bone inspection"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        manager = context.window_manager
        setattr(manager, ADVANCED_PROPERTY, not getattr(manager, ADVANCED_PROPERTY, False))
        if context.area:
            context.area.tag_redraw()
        return {"FINISHED"}


def draw_wiggle_motion(layout, context, source, record):
    """Draw released Dress choices without importing pending Cloth services."""
    if (record.get("physics") or {}).get("backend", "LEGACY_CAGE") != "LEGACY_CAGE":
        return False
    try:
        state = _motion().status(context, source)
        original = _original(source)
        original_supported = _original_supported(source)
        controls_visible = _controls_visible(source, record)
    except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as error:
        layout.label(text=str(error), icon="ERROR")
        return True
    row = layout.row(align=True)
    row.enabled = _ready(context) and not original
    automatic = row.row(align=True)
    automatic.enabled = state["available"]
    automatic.operator("character_designer.dress_wiggle_mode", text="Automatic · Wiggle Bones",
                       depress=state["mode"] == "AUTOMATIC").mode = "AUTOMATIC"
    row.operator("character_designer.dress_wiggle_mode", text="Manual",
                 depress=state["mode"] == "MANUAL").mode = "MANUAL"
    if state.get("message"):
        layout.label(text=state["message"], icon="INFO")
    row = layout.row(align=True)
    row.enabled = _ready(context)
    row.operator("character_designer.dress_wiggle_display", text="Controls",
                 depress=not original and controls_visible).layer = "CONTROLS"
    original_row = row.row(align=True)
    original_row.enabled = original_supported
    original_row.operator("character_designer.dress_wiggle_display", text="Original",
                          depress=original).layer = "ORIGINAL"
    if not original_supported:
        layout.label(text="Original needs organized Main Rig bones.", icon="INFO")
    expanded = getattr(context.window_manager, ADVANCED_PROPERTY, False)
    layout.operator("character_designer.dress_wiggle_details", text="Advanced Inspection",
                    icon="TRIA_DOWN" if expanded else "TRIA_RIGHT", emboss=False)
    if expanded:
        row = layout.row(align=True)
        row.enabled = _ready(context) and not original
        for layer in ("MCH", "PHYS"):
            row.operator("character_designer.dress_wiggle_display", text=layer,
                         depress=_layer_visible(source, record, layer)).layer = layer
        layout.label(text="Visibility keeps automatic motion running.", icon="INFO")
    return True


SKIRT_WIGGLE_UI_CLASSES = (CHARACTERDESIGNER_OT_dress_wiggle_mode,
                          CHARACTERDESIGNER_OT_dress_wiggle_display,
                          CHARACTERDESIGNER_OT_dress_wiggle_details)


def register():
    for item in SKIRT_WIGGLE_UI_CLASSES:
        if not item.is_registered:
            bpy.utils.register_class(item)
    if not hasattr(bpy.types.WindowManager, ADVANCED_PROPERTY):
        setattr(bpy.types.WindowManager, ADVANCED_PROPERTY,
                BoolProperty(name="Advanced Inspection", default=False, options={"SKIP_SAVE"}))


def unregister():
    if hasattr(bpy.types.WindowManager, ADVANCED_PROPERTY):
        delattr(bpy.types.WindowManager, ADVANCED_PROPERTY)
    for item in reversed(SKIRT_WIGGLE_UI_CLASSES):
        if item.is_registered:
            bpy.utils.unregister_class(item)
