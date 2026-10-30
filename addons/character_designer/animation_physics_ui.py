"""Animation-page physics controls; drawing reads saved metadata only."""

import unicodedata

from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator


WIGGLE_DOWNLOAD_URL = "https://github.com/shteeve3d/blender-wiggle-2/blob/02c4f0f1175cff5a6798229e0a5383dfe00f1221/wiggle_2.py"


def _service():
    from . import animation_physics
    return animation_physics


def _panel(layout, identifier, *, default_closed=False):
    """Native region-local folds; older Blender layouts remain usable."""
    panel = getattr(layout, "panel", None)
    if callable(panel):
        return panel(identifier, default_closed=default_closed)
    box = layout.box()
    return box.row(align=True), box


def section(layout, identifier, label, *, icon="NONE", default_closed=False):
    header, body = _panel(layout, identifier, default_closed=default_closed)
    header.label(text=label, icon=icon)
    return body


def _line_width(context):
    region = getattr(context, "region", None)
    width = getattr(region, "width", 320)
    system = getattr(getattr(context, "preferences", None), "system", None)
    scale = max(.5, float(getattr(system, "ui_scale", 1.) or 1.))
    return max(14, min(100, int((width / scale - 56) / 7.5)))


def _cells(text):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
               for char in text)


def _wrapped(text, width):
    """Wrap every character, including long paths and full-width text."""
    result = []
    for paragraph in str(text).expandtabs(4).splitlines() or [""]:
        line = ""
        for char in paragraph:
            if line and _cells(line + char) > width:
                split = line.rfind(" ")
                if split > 0:
                    result.append(line[:split].rstrip())
                    line = line[split + 1:] + char
                else:
                    result.append(line.rstrip())
                    line = char
            else:
                line += char
        result.append(line.rstrip())
    return result


def wrapped_label(layout, context, text, *, icon="INFO"):
    for index, line in enumerate(_wrapped(text, _line_width(context))):
        layout.label(text=line, icon=icon if index == 0 else "NONE")


def _message(layout, context, text, identifier, *, icon="INFO", details=()):
    """Show actionable failures directly, without a second diagnostic fold."""
    if not text and not details:
        return
    if str(text).startswith("Hair preview guard stopped: "):
        text = "Hair physics stopped: " + str(text)[len("Hair preview guard stopped: "):]
    if text:
        wrapped_label(layout, context, text, icon=icon)
    for detail in details:
        if detail and detail != text:
            wrapped_label(layout, context, detail, icon="NONE")


def _target_token(rig):
    return str(rig.as_pointer()) if rig is not None else ""


def _capture_target(operator, state):
    rig = state.get("rig")
    operator.target_name = rig.name if rig is not None else ""
    operator.target_pointer = _target_token(rig)
    return operator


def _checked_target(operator, context):
    state = _service().status(context)
    if not state.get("valid", False):
        raise ValueError(state.get("message") or "Physics settings need review before changing them.")
    rig = state.get("rig")
    if (rig is None or rig.name != operator.target_name
            or _target_token(rig) != operator.target_pointer):
        raise ValueError("The current character changed. Use its Physics controls again.")
    return state


def _run(operator, context, action):
    try:
        _checked_target(operator, context)
        action()
    except (ValueError, RuntimeError, ReferenceError, TypeError, KeyError) as error:
        operator.report({"ERROR"}, str(error))
        area = getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()
        return {"CANCELLED"}
    area = getattr(context, "area", None)
    if area is not None:
        area.tag_redraw()
    return {"FINISHED"}


class CHARACTERDESIGNER_OT_animation_physics_set(Operator):
    bl_idname = "character_designer.animation_physics_set"
    bl_label = "Set Animation Physics"
    bl_description = "Allow or pause this character's physics; checked permission alone does not start a preview"
    bl_options = {"REGISTER", "UNDO"}

    component: EnumProperty(items=(
        ("ALL", "All Physics", "This character's master physics switch"),
        ("SKIRT", "Skirt", "This character's skirt physics"),
        ("HAIR", "Hair", "This character's hair physics"),
    ), default="ALL")
    enabled: BoolProperty(default=True)
    target_name: StringProperty(options={"HIDDEN"})
    target_pointer: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        def apply():
            if self.enabled:
                component = "SKIRT" if self.component == "ALL" else self.component
                if _service().dependency_status(component).get("state") == "UNCHECKED":
                    _service().dependency_status(component, refresh=True)
            if self.component == "ALL":
                _service().set_enabled(context, self.enabled)
            else:
                _service().set_component(context, self.component, self.enabled)
        return _run(self, context, apply)


class CHARACTERDESIGNER_OT_animation_physics_skirt_mode(Operator):
    bl_idname = "character_designer.animation_physics_skirt_mode"
    bl_label = "Set Skirt Motion Mode"
    bl_description = "Change the existing skirt motion mode without creating another setup"
    bl_options = {"REGISTER", "UNDO"}

    source_name: StringProperty(options={"HIDDEN"})
    source_owner: StringProperty(options={"HIDDEN"})
    mode: EnumProperty(items=(
        ("AUTOMATIC", "Automatic", "Use this skirt's automatic motion"),
        ("MANUAL", "Manual", "Use this skirt's manual motion mode"),
    ), default="MANUAL")
    target_name: StringProperty(options={"HIDDEN"})
    target_pointer: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        def apply():
            if self.mode == "AUTOMATIC" and _service().dependency_status("SKIRT").get("state") == "UNCHECKED":
                _service().dependency_status("SKIRT", refresh=True)
            return _service().set_skirt_mode(
                context, self.source_name, self.mode, expected_owner=self.source_owner)
        return _run(self, context, apply)


class CHARACTERDESIGNER_OT_animation_physics_start_hair(Operator):
    bl_idname = "character_designer.animation_physics_start_hair"
    bl_label = "Start Hair Preview"
    bl_description = "Start or resume this character's Blender Hair preview using Wiggle 2 and its existing strand settings"
    bl_options = {"REGISTER", "UNDO"}

    target_name: StringProperty(options={"HIDDEN"})
    target_pointer: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        return _run(self, context, lambda: _service().start_hair_preview(context))


class CHARACTERDESIGNER_OT_animation_physics_dependency(Operator):
    bl_idname = "character_designer.animation_physics_dependency"
    bl_label = "Check Physics Extension"
    bl_description = "Check or enable the installed supported Wiggle extension without saving preferences"
    bl_options = {"REGISTER"}

    component: EnumProperty(items=(("SKIRT", "Skirt", "Skirt motion extension"),
                                  ("HAIR", "Hair", "Hair preview extension")))
    action: EnumProperty(items=(("CHECK", "Check", "Read the actual extension compatibility"),
                               ("ENABLE", "Enable", "Enable the installed supported extension")),
                         default="CHECK")
    target_name: StringProperty(options={"HIDDEN"})
    target_pointer: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        def apply():
            if self.action == "ENABLE":
                _service().enable_dependency(context, self.component)
            else:
                _service().dependency_status(self.component, refresh=True)
        return _run(self, context, apply)


class CHARACTERDESIGNER_OT_animation_physics_stop_hair(Operator):
    bl_idname = "character_designer.animation_physics_stop_hair"
    bl_label = "Stop Hair Preview"
    bl_description = "End only this character's Blender Hair preview and restore its owned settings; playback continues"
    bl_options = {"REGISTER", "UNDO"}

    target_name: StringProperty(options={"HIDDEN"})
    target_pointer: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        return _run(self, context, lambda: _service().stop_hair_preview(context))


class CHARACTERDESIGNER_OT_animation_physics_controls(Operator):
    bl_idname = "character_designer.animation_physics_controls"
    bl_label = "Return to Controls"
    bl_description = "Leave this character's Original bone display using its existing guarded Controls action"
    bl_options = {"REGISTER", "UNDO"}

    target_name: StringProperty(options={"HIDDEN"})
    target_pointer: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        return _run(self, context, lambda: _service().set_control_mode(
            context, not bool(_service().status(context).get("control_mode", False))))


def _checkbox(layout, state, *, component, label, checked, enabled):
    row = layout.row(align=True)
    row.alignment = "LEFT"
    row.enabled = enabled
    operator = row.operator(
        CHARACTERDESIGNER_OT_animation_physics_set.bl_idname, text=label,
        icon="CHECKBOX_HLT" if checked else "CHECKBOX_DEHLT", emboss=False)
    operator.component = component
    operator.enabled = not checked
    _capture_target(operator, state)


def _draw_dependency(layout, context, state, component, dependency, identifier):
    if not dependency:
        return
    kind = dependency.get("state", "UNCHECKED")
    if kind in {"READY", "UNCHECKED"}:
        return
    if kind == "MISSING":
        row = layout.row(align=True)
        operator = row.operator("wm.url_open", text="Get Wiggle 2", icon="URL")
        operator.url = WIGGLE_DOWNLOAD_URL
        operator = row.operator(CHARACTERDESIGNER_OT_animation_physics_dependency.bl_idname,
                                text="Check Again", icon="FILE_REFRESH")
        operator.component, operator.action = component, "CHECK"
        _capture_target(operator, state)
        return
    else:
        action = "ENABLE" if dependency.get("enable_available") else "CHECK"
        row = layout.row(align=True)
        row.enabled = context.mode in {"OBJECT", "POSE"}
        operator = row.operator(CHARACTERDESIGNER_OT_animation_physics_dependency.bl_idname,
                                text="Enable Wiggle" if action == "ENABLE" else "Check Wiggle",
                                icon="CHECKMARK" if action == "ENABLE" else "FILE_REFRESH")
        operator.component, operator.action = component, action
        _capture_target(operator, state)
    _message(layout, context, dependency.get("message", ""), identifier + "_dependency_details",
             details=dependency.get("details", ()))


def _can_start(entry):
    dependency = entry.get("dependency") or {}
    return bool(entry.get("available", False)) and dependency.get("state") in {None, "READY", "UNCHECKED"}


def _draw_skirts(layout, context, state, editable):
    dresses = state.get("dresses", ())
    enabled = bool(state.get("enabled", True))
    checked = bool(state.get("skirt_enabled", True))
    queued = [dress for dress in dresses
              if dress.get("mode", "AUTOMATIC") == "AUTOMATIC"
              and dress.get("mode_pending") and dress.get("context_blocked")
              and dress.get("requires_controls") and not dress.get("last_error")
              and not dress.get("pending_recovery")
              and (dress.get("dependency") or {}).get("state") in {None, "READY", "UNCHECKED"}]
    if enabled:
        checked = checked and bool(dresses) and all(
            dress.get("mode", "AUTOMATIC") == "AUTOMATIC"
            and not dress.get("last_error") and not dress.get("pending_recovery")
            and ((dress.get("applied_mode", "AUTOMATIC") == "AUTOMATIC"
                  and dress.get("active", False) and dress.get("running", False)
                  and not dress.get("paused")) or dress in queued)
            for dress in dresses)
    header = layout.row(align=True)
    _checkbox(header, state, component="SKIRT", label="Skirt",
              checked=checked,
              enabled=editable and enabled and bool(dresses))
    if len(dresses) == 1:
        dress = dresses[0]
        row = header.row(align=True)
        row.alignment = "RIGHT"
        row.enabled = editable and dress.get("can_change_mode", True)
        manual = dress.get("mode", "AUTOMATIC") == "MANUAL"
        operator = row.operator(CHARACTERDESIGNER_OT_animation_physics_skirt_mode.bl_idname,
                                text="Control Mode", depress=manual)
        operator.source_name, operator.source_owner = dress["source"], dress.get("owner", "")
        operator.mode = "AUTOMATIC" if manual else "MANUAL"
        _capture_target(operator, state)
    if not dresses:
        wrapped_label(layout, context, "No skirt setup on this character.")
        return
    for dress in dresses:
        identifier = "character_designer_animation_physics_skirt_" + dress.get("owner", dress["source"])
        column = layout
        if len(dresses) > 1:
            row = column.row(align=True)
            row.label(text=dress.get("name", dress["source"]))
            choice = row.row(align=True)
            choice.alignment = "RIGHT"
            choice.enabled = editable and dress.get("can_change_mode", True)
            manual = dress.get("mode", "AUTOMATIC") == "MANUAL"
            operator = choice.operator(CHARACTERDESIGNER_OT_animation_physics_skirt_mode.bl_idname,
                                       text="Control Mode", depress=manual)
            operator.source_name, operator.source_owner = dress["source"], dress.get("owner", "")
            operator.mode = "AUTOMATIC" if manual else "MANUAL"
            _capture_target(operator, state)
        block_reason = dress.get("block_reason") or dress.get("context_message")
        dependency_message = (dress.get("dependency") or {}).get("message")
        routine = {dependency_message, block_reason, "Dress physics is paused.",
                   "Original editing paused automatic Dress motion.",
                   "Automatic Dress motion paused; the current shape is kept.",
                   "Scene editing paused automatic Dress motion; the current shape is kept.",
                   "Manual keeps the current skirt shape.",
                   "Manual keeps the current shape; pose the rings and handles.",
                   "Automatic uses Wiggle 2. Manual keeps the current skirt shape.",
                   "Automatic is stopped. Choose Automatic to start.",
                   "Automatic is paused. Choose Automatic to resume."}
        if dress.get("pending_recovery"):
            column.label(text="Skirt physics needs review.", icon="ERROR")
        if dress.get("message") and dress["message"] not in routine:
            _message(column, context, dress["message"], identifier + "_motion_details")
        if (dress.get("last_error") and dress["last_error"] != dependency_message
                and (dress["last_error"] != dress.get("message") or dress.get("message") in routine)):
            _message(column, context, dress["last_error"], identifier + "_last_error", icon="ERROR")


def _draw_hair(layout, context, state, editable):
    hair = state.get("hair", {})
    enabled = bool(state.get("enabled", True))
    checked = bool(state.get("hair_enabled", True))
    if enabled:
        checked = (checked and bool(hair.get("active")) and not hair.get("paused")
                   and not hair.get("cleanup_pending") and not hair.get("pending_recovery"))
    _checkbox(layout, state, component="HAIR", label="Hair",
              checked=checked,
              enabled=editable and enabled and bool(hair.get("sources")))
    identifier = "character_designer_animation_physics_hair"
    if not hair.get("sources"):
        wrapped_label(layout, context, "No initialized Hair motion on this character.")
    dependency_message = (hair.get("dependency") or {}).get("message")
    routine = {dependency_message, "Stopped", "Save, undo, redo, load or render ended Hair preview.",
               "Character Designer unregistered.",
               "History/load ended the transient Hair lease; use explicit Physics controls to verify it again.",
               "Saved Hair preview flags need explicit recovery."}
    if hair.get("message") and hair["message"] not in routine:
        _message(layout, context, hair["message"], identifier + "_motion_details")
    if hair.get("last_error") and hair["last_error"] not in {hair.get("message"), dependency_message}:
        _message(layout, context, hair["last_error"], identifier + "_last_error", icon="ERROR")
    seen_errors = {hair.get("last_error"), hair.get("message"), dependency_message}
    for source, error in (hair.get("source_errors") or {}).items():
        if error and error not in seen_errors:
            _message(layout, context, source + ": " + error, identifier + "_source_error_" + source, icon="ERROR")
            seen_errors.add(error)
    if (hair.get("sources") and (hair.get("cleanup_pending") or hair.get("pending_recovery"))
            and (not enabled or bool(hair.get("last_error")) or any((hair.get("source_errors") or {}).values())
                 or hair.get("message") not in routine | {"", None})):
        row = layout.row(align=True)
        row.alignment = "LEFT"
        row.enabled = editable and (bool(hair.get("active")) or len(hair["sources"]) == 1)
        operator = row.operator(CHARACTERDESIGNER_OT_animation_physics_stop_hair.bl_idname,
                                text="Recover Hair", icon="FILE_REFRESH")
        _capture_target(operator, state)


def draw(layout, context):
    """Draw permission, mode and dependency controls in independent folds."""
    header, box = _panel(layout, "character_designer_animation_physics")
    header.label(text="Physics", icon="PHYSICS")
    try:
        state = _service().status(context)
    except (ValueError, RuntimeError, ReferenceError, TypeError, KeyError) as error:
        if box is not None:
            _message(box, context, str(error), "character_designer_animation_physics_error", icon="ERROR")
        return
    rig = state.get("rig")
    if not state.get("valid", False):
        if box is not None:
            _message(box, context, state.get("message") or "Physics settings need review.",
                     "character_designer_animation_physics_invalid", icon="ERROR")
        return
    enabled = bool(state.get("enabled", True))
    editable = rig is not None and context.mode in {"OBJECT", "POSE"}
    row = header.row(align=True)
    row.alignment = "RIGHT"
    _checkbox(row, state, component="ALL", label="", checked=enabled,
              enabled=editable)
    if box is None:
        return
    if rig is None:
        wrapped_label(box, context, state.get("message") or "Choose the character's Main Rig first.")
        return
    if state.get("message") and (enabled or state["message"] !=
            "Physics permission is off. Saved Dress modes are retained."):
        _message(box, context, state["message"], "character_designer_animation_physics_status")
    dependencies = [("SKIRT", dress.get("dependency")) for dress in state.get("dresses", ())
                    if dress.get("backend") == "WIGGLE" and dress.get("dependency")]
    if not dependencies and state.get("hair", {}).get("dependency"):
        dependencies = [("HAIR", state["hair"]["dependency"])]
    if not dependencies and state.get("dependencies", {}).get("SKIRT"):
        dependencies = [("SKIRT", state["dependencies"]["SKIRT"])]
    if dependencies:
        component, dependency = dependencies[0]
        _draw_dependency(box, context, state, component, dependency,
                         "character_designer_animation_physics_shared")
    _draw_skirts(box, context, state, editable)
    _draw_hair(box, context, state, editable)


PHYSICS_CLASSES = (
    CHARACTERDESIGNER_OT_animation_physics_set,
    CHARACTERDESIGNER_OT_animation_physics_skirt_mode,
    CHARACTERDESIGNER_OT_animation_physics_start_hair,
    CHARACTERDESIGNER_OT_animation_physics_dependency,
    CHARACTERDESIGNER_OT_animation_physics_controls,
    CHARACTERDESIGNER_OT_animation_physics_stop_hair,
)
