"""Explicit Dress motion actions and a metadata-only compact drawing helper."""
import importlib
import json

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator


def _skirt():
    return importlib.import_module(__package__ + ".skirt")


def _rig():
    return importlib.import_module(__package__ + ".skirt_rig")


def _profiles():
    return importlib.import_module(__package__ + ".skirt_motion_profiles")


def _tuning():
    return importlib.import_module(__package__ + ".skirt_motion_tuning")


def _physics():
    return importlib.import_module(__package__ + ".skirt_physics")


def _source_record(context, *, source=None, physics=False):
    if not _skirt()._idle(context):
        raise ValueError("Wait for the current Dress bake to finish.")
    source = _skirt()._source(context) if source is None else source
    record = _rig().read_record(source) if source is not None else None
    if not record:
        raise ValueError("Create the Dress setup first.")
    _rig()._require_controls_for_setup(source)
    if physics and not record.get("physics"):
        raise ValueError("Add Physics + Colliders for automatic Dress motion first.")
    return source, record


def _ready(context, *, physics=False):
    try:
        if not _skirt()._idle(context):
            return False
        source = _skirt()._source(context)
        if source is None:
            return False
        # Operator polling also happens while drawing buttons. Use saved
        # metadata here; actions run the full native setup checks themselves.
        record = json.loads(source.get(_rig().RECORD_KEY, "null"))
        return bool(isinstance(record, dict) and record.get("chains")
                    and source.get(_rig().RIG_KEY) is not None and _metadata_controls(source)
                    and (not physics or record.get("physics")))
    except (ValueError, TypeError, RuntimeError, ReferenceError):
        return False


def _report(operator, context, message, *, error=False):
    _skirt()._report(operator, context, message, error=error)


def _targets(source, record, all_dresses):
    if not all_dresses:
        return (source,)
    rig = source.get(_rig().RIG_KEY)
    if not record.get("shared") or rig is None:
        raise ValueError("Batch tuning needs Dress setups on the same Main Rig.")
    result = [source]
    for candidate in _rig().shared_sources(rig):
        if candidate == source:
            continue
        other = _rig().read_record(candidate)
        if other and other.get("shared") and other.get("physics"):
            result.append(candidate)
    return tuple(result)


class CHARACTERDESIGNER_OT_dress_motion_mode(Operator):
    bl_idname = "character_designer.dress_motion_mode"
    bl_label = "Set Dress Motion"
    bl_description = "Choose automatic motion or manual controls while retaining the saved simulation"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(items=(
        ("AUTOMATIC", "Automatic", "Use the Dress simulation"),
        ("MANUAL", "Manual", "Use the Dress controls"),
    ), default="AUTOMATIC")

    @classmethod
    def poll(cls, context):
        return _ready(context)

    def execute(self, context):
        try:
            source, _record = _source_record(context)
            _tuning().apply(context, (source,), mode=self.mode)
        except (ValueError, RuntimeError, ReferenceError) as error:
            _report(self, context, str(error), error=True)
            return {"CANCELLED"}
        _report(self, context, f"Dress motion: {self.mode.title()}.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_dress_motion_tuning(Operator):
    bl_idname = "character_designer.dress_motion_tuning"
    bl_label = "Tune Dress Motion"
    bl_description = "Read current Dress settings and apply only your changes to the selected Dress setups"
    bl_options = {"REGISTER", "UNDO"}

    all_dresses: BoolProperty(name="All physics Dresses on this Main Rig", default=False,
                              options={"SKIP_SAVE"})
    show_advanced: BoolProperty(name="Advanced", default=False, options={"SKIP_SAVE"})
    bend: FloatProperty(name="Bend", default=0.8, min=0.0, max=10.0)
    damping: FloatProperty(name="Damping", default=5.0, min=0.0, max=50.0)
    recovery: FloatProperty(name="Shape Recovery", default=0.0, min=0.0, max=1.0,
                            description="A soft goal toward the waist-following Dress shape")
    collision_margin: FloatProperty(name="Body Clearance", default=0.008, min=0.0001, max=0.05,
                                    precision=4, description="Fraction of the fitted Dress height")
    quality: IntProperty(name="Simulation Quality", default=8, min=1, max=32)
    mass: FloatProperty(name="Mass", default=0.15, min=0.001, max=5.0)
    stretch: FloatProperty(name="Stretch Resistance", default=25.0, min=0.0, max=100.0)
    shear: FloatProperty(name="Shear Resistance", default=10.0, min=0.0, max=100.0)
    bend_damping: FloatProperty(name="Bend Damping", default=1.0, min=0.0, max=50.0)
    air_damping: FloatProperty(name="Air Damping", default=3.0, min=0.0, max=10.0)
    gravity: FloatProperty(name="Gravity", default=1.0, min=0.0, max=2.0,
                           description="Multiplier of scene gravity")
    waist_depth: FloatProperty(name="Fixed Waist Depth", default=0.0, min=0.0, max=0.2,
                               description="Fraction of the Dress height held at the waist")
    transition: FloatProperty(name="Waist Transition", default=0.35, min=0.0, max=1.0)
    pin_stiffness: FloatProperty(name="Goal Stiffness", default=1.0, min=0.0, max=50.0)
    collision_quality: IntProperty(name="Collision Quality", default=4, min=1, max=16)
    self_collision: BoolProperty(name="Self Collision", default=False)
    self_margin: FloatProperty(name="Self Clearance", default=0.008, min=0.0001, max=5.0,
                                precision=4, description="Fraction of the fitted Dress height")
    self_friction: FloatProperty(name="Self Friction", default=5.0, min=0.0, max=80.0)

    @classmethod
    def poll(cls, context):
        return _ready(context, physics=True)

    def invoke(self, context, _event):
        try:
            source, record = _source_record(context, physics=True)
            profile = _tuning().effective(source)
            self._source_ref = source
            self._identity = dict(profile["identity"])
            self._is_shared = bool(record.get("shared"))
            self.all_dresses = False
            for key, value in profile["settings"].items():
                setattr(self, key, value)
            # Read back RNA floats once, so opening/confirming a dialog cannot
            # turn float storage rounding into an unintended material edit.
            self._loaded_values = {key: getattr(self, key) for key in profile["settings"]}
            return context.window_manager.invoke_props_dialog(self, width=420)
        except (ValueError, RuntimeError, ReferenceError) as error:
            _report(self, context, str(error), error=True)
            return {"CANCELLED"}

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        for key in ("bend", "damping", "recovery", "collision_margin", "quality"):
            layout.prop(self, key)
        if getattr(self, "_is_shared", False):
            layout.prop(self, "all_dresses")
        layout.prop(self, "show_advanced", toggle=True)
        if self.show_advanced:
            for key in ("mass", "stretch", "shear", "bend_damping", "air_damping", "gravity",
                        "waist_depth", "transition", "pin_stiffness", "collision_quality",
                        "self_collision"):
                layout.prop(self, key)
            column = layout.column()
            column.enabled = self.self_collision
            column.prop(self, "self_margin")
            column.prop(self, "self_friction")

    def execute(self, context):
        try:
            source, record = _source_record(context, source=getattr(self, "_source_ref", None), physics=True)
            identity = getattr(self, "_identity", None)
            if identity is not None and any(record.get(key) != value for key, value in identity.items()):
                raise ValueError("The Dress setup changed. Reopen its tuning dialog.")
            loaded = getattr(self, "_loaded_values", None)
            if loaded is None:
                changes = {key: getattr(self, key) for key in _profiles().DEFAULTS
                           if self.properties.is_property_set(key)}
            else:
                changes = {key: getattr(self, key) for key, value in loaded.items()
                           if getattr(self, key) != value}
            targets = _targets(source, record, self.all_dresses)
            _tuning().apply(context, targets, changes)
        except (ValueError, RuntimeError, ReferenceError) as error:
            _report(self, context, str(error), error=True)
            return {"CANCELLED"}
        count = len(targets)
        _report(self, context, "Dress motion settings saved." if count == 1 else
                f"Motion settings saved for {count} Dresses.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_dress_motion_reset(Operator):
    bl_idname = "character_designer.dress_motion_reset"
    bl_label = "Reset Dress Motion"
    bl_description = "Clear the Dress simulation and return to the simulation start frame"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _ready(context, physics=True)

    def execute(self, context):
        try:
            source, _record = _source_record(context, physics=True)
            _physics().reset_simulation(context, source)
        except (ValueError, RuntimeError, ReferenceError) as error:
            _report(self, context, str(error), error=True)
            return {"CANCELLED"}
        _report(self, context, "Dress motion reset to the start frame.")
        return {"FINISHED"}


def _metadata_controls(source):
    rig = source.get(_rig().RIG_KEY)
    for candidate in (source, rig):
        if candidate is None:
            continue
        if _rig()._ORIGINAL_SESSION_KEY in candidate:
            return False
        owner = candidate.get(_rig().ORIGINAL_DISPLAY_OWNER_KEY)
        if owner is not None and hasattr(owner, "get") and _rig()._ORIGINAL_SESSION_KEY in owner:
            return False
    return True


def _metadata_mode(source, record, profile):
    rig = source.get(_rig().RIG_KEY)
    holder = rig
    if rig is not None and record.get("shared"):
        holder = rig.pose.bones.get(record.get("controls", {}).get("waist", ""))
    influence = holder.get("physics_influence") if holder is not None else None
    if isinstance(influence, (int, float)):
        return "AUTOMATIC" if record.get("physics") and influence > 0 else "MANUAL"
    return profile["mode"] if profile is not None else "MANUAL"


def draw_motion(layout, context, source, record):
    """Draw only passed setup and saved motion metadata; never validate a mesh."""
    if source is None or not record:
        return
    box = layout.box()
    box.label(text="Dress Motion")
    try:
        profile = _profiles().read(source, record)
    except (ValueError, RuntimeError, ReferenceError):
        box.label(text="Saved motion settings need review.", icon="ERROR")
        return
    has_physics = bool(record.get("physics"))
    capability = profile["capability"] if profile else ("BOTH" if has_physics else "MANUAL")
    mode = _metadata_mode(source, record, profile)
    idle = _skirt()._idle(context)
    controls = _metadata_controls(source)
    column = box.column(align=True)
    column.enabled = idle and controls
    row = column.row(align=True)
    automatic = row.row(align=True)
    automatic.enabled = has_physics and capability != "MANUAL"
    automatic.operator("character_designer.dress_motion_mode", text="Automatic",
                       depress=mode == "AUTOMATIC").mode = "AUTOMATIC"
    manual = row.row(align=True)
    manual.enabled = capability != "PHYSICS"
    manual.operator("character_designer.dress_motion_mode", text="Manual",
                    depress=mode == "MANUAL").mode = "MANUAL"
    if not has_physics:
        box.label(text="Manual controls ready. Add Physics + Colliders for automatic motion.", icon="INFO")
    else:
        baked = bool(record["physics"].get("baked_range"))
        row = column.row(align=True)
        tune = row.row(align=True)
        tune.enabled = not baked
        tune.operator("character_designer.dress_motion_tuning", text="Tuning", icon="PREFERENCES")
        row.operator("character_designer.dress_motion_reset", text="Reset", icon="LOOP_BACK")
        row.operator("character_designer.skirt_bake_physics", text="Bake", icon="REC")
        if baked:
            box.label(text="Baked motion. Reset before changing tuning.", icon="INFO")
    if not idle:
        box.label(text="Baking Dress motion. Esc cancels.", icon="INFO")
    elif not controls:
        box.label(text="Switch to Controls to adjust Dress motion.", icon="INFO")


DRESS_MOTION_CLASSES = (
    CHARACTERDESIGNER_OT_dress_motion_mode,
    CHARACTERDESIGNER_OT_dress_motion_tuning,
    CHARACTERDESIGNER_OT_dress_motion_reset,
)
