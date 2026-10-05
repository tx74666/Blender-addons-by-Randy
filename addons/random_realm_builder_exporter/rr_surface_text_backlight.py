"""Opt-in native rear emission for editable Surface Text Fonts.

The copied shell of Blender's Simple Solidify is the rear shell when Flip
Normals is disabled. An appended material and a clamped material offset target
that shell only; the original faces and rims retain their authored materials.
"""

import math

import bpy


BACKLIGHT_PREFIX = "rr_surface_backlight_"
BACKLIGHT_DEFAULT_GAP_METERS = 0.05
BACKLIGHT_DEFAULT_STRENGTH = 5.0
BACKLIGHT_DEFAULT_HALO_WIDTH_METERS = 0.08
BACKLIGHT_DEFAULT_COLOR = (1.0, 1.0, 1.0)
_SOLIDIFY_RESTORE_FIELDS = (
    "thickness", "offset", "use_flip_normals", "material_offset",
    "material_offset_rim", "solidify_mode", "use_rim", "use_rim_only",
    "vertex_group", "thickness_clamp", "use_even_offset",
)


def _surface_module():
    try:
        from . import rr_surface_text
    except ImportError:
        import rr_surface_text
    return rr_surface_text


def _positive(value, name, allow_zero=False):
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or (not allow_zero and number == 0.0):
        raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'} and finite.")
    return number


def read_backlight_settings(source):
    """Return the v1 regeneration/Unity contract; old Fonts remain disabled."""
    color = tuple(float(component) for component in source.get(
        BACKLIGHT_PREFIX + "color", BACKLIGHT_DEFAULT_COLOR))
    if len(color) != 3 or any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in color):
        raise ValueError("Backlight color must contain three RGB values between zero and one.")
    settings = {
        "version": 1,
        "enabled": bool(source.get(BACKLIGHT_PREFIX + "enabled", False)),
        "backGapMeters": _positive(source.get(BACKLIGHT_PREFIX + "back_gap_meters", BACKLIGHT_DEFAULT_GAP_METERS), "Back Gap", True),
        "color": list(color),
        "strength": _positive(source.get(BACKLIGHT_PREFIX + "strength", BACKLIGHT_DEFAULT_STRENGTH), "Backlight Strength", True),
        "haloWidthMeters": _positive(source.get(BACKLIGHT_PREFIX + "halo_width_meters", BACKLIGHT_DEFAULT_HALO_WIDTH_METERS), "Unity Halo Width", True),
    }
    if settings["enabled"]:
        material = source.get(BACKLIGHT_PREFIX + "material_ref")
        indices = [index for index, slot in enumerate(source.material_slots) if slot.material is material]
        if indices:
            settings["backMaterialIndex"] = indices[-1]
    return settings


def _bound_modifiers(source):
    surface = _surface_module()
    if source is None or source.type != "FONT" or source.get("rr_surface_text_role") != "surface_text":
        raise RuntimeError("Select a bound editable Surface Text Font before configuring backlight.")
    if not getattr(source, "is_editable", True):
        raise RuntimeError("Make this Surface Text Font local and editable first.")
    shrinkwrap, solidify = surface._surface_text_modifier_pair(source)
    if shrinkwrap is None or solidify is None or shrinkwrap.target is None:
        raise RuntimeError("Surface Text backlight requires its bound Shrinkwrap and Solidify modifiers.")
    sample = surface._surface_text_sampling_surface(source)
    if sample is None or sample.type != "MESH" or sample.get("rr_surface_role") != "sampling_surface":
        raise RuntimeError("Bind this Surface Text Font to its selected wall faces before configuring backlight.")
    return shrinkwrap, solidify


def _depth_scale(source):
    scale = source.matrix_world.to_3x3().col[2].length
    if not math.isfinite(scale) or scale <= 1.0e-8:
        raise RuntimeError("Surface Text has a degenerate depth transform.")
    return scale


def _validate_flat_font(source, shrinkwrap, solidify):
    if abs(source.data.extrude) > 1.0e-8 or abs(source.data.bevel_depth) > 1.0e-8:
        raise RuntimeError("Use Solidify for Thickness; backlight requires Font Extrude and Bevel Depth to be zero.")
    if source.data.follow_curve is not None:
        raise RuntimeError("Backlight currently requires Surface Text projected directly onto its bound wall.")
    if (shrinkwrap.wrap_method != "PROJECT" or not shrinkwrap.use_project_z
            or shrinkwrap.use_project_x or shrinkwrap.use_project_y):
        raise RuntimeError("Backlight requires the Surface Text Shrinkwrap projection along its local Z axis.")
    modifiers = list(source.modifiers)
    if modifiers.index(shrinkwrap) >= modifiers.index(solidify):
        raise RuntimeError("Surface Text Shrinkwrap must precede Solidify.")
    if any(modifier.show_viewport or modifier.show_render for modifier in modifiers[modifiers.index(solidify) + 1:]
           if modifier.type not in {"TRIANGULATE", "WEIGHTED_NORMAL", "NORMAL_EDIT"}):
        raise RuntimeError("Apply or disable geometry modifiers after Surface Text Solidify before configuring backlight.")


def validate_backlight_settings(source, scene=None):
    """Reject stale native edits instead of exporting a misleading rear gap."""
    settings = read_backlight_settings(source)
    if not settings["enabled"]:
        return settings
    shrinkwrap, solidify = _bound_modifiers(source)
    _validate_flat_font(source, shrinkwrap, solidify)
    material = source.get(BACKLIGHT_PREFIX + "material_ref")
    index = settings.get("backMaterialIndex", -1)
    if (material is None or material.get(BACKLIGHT_PREFIX + "role") != "rear_emission"
            or index < 1 or index != len(source.material_slots) - 1
            or solidify.material_offset != index or solidify.material_offset_rim != 0
            or solidify.use_flip_normals or solidify.thickness >= 0.0
            or abs(solidify.offset + 1.0) > 1.0e-6
            or solidify.solidify_mode != "EXTRUDE" or not solidify.use_rim or solidify.use_rim_only
            or solidify.vertex_group or solidify.thickness_clamp != 0.0):
        raise RuntimeError("Surface Text backlight shell settings changed; run Configure Backlight again.")
    if any(character.material_index >= index for character in source.data.body_format):
        raise RuntimeError("Use a front material for Font characters; the final material slot is reserved for rear emission.")
    scene = scene or bpy.context.scene
    scene_scale = _surface_module()._scene_scale_length(scene)
    expected = settings["backGapMeters"] / (scene_scale * _depth_scale(source))
    if abs(shrinkwrap.offset - expected) > max(1.0e-6, abs(expected) * 1.0e-5):
        raise RuntimeError("Surface Text backlight depth scale or rear gap changed; run Configure Backlight again.")
    return settings


def _new_material(name, role, color=None, strength=0.0):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material[BACKLIGHT_PREFIX + "role"] = role
    principled = material.node_tree.nodes.get("Principled BSDF")
    if color is not None:
        principled.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        principled.inputs["Roughness"].default_value = 1.0
        principled.inputs["Emission Color"].default_value = (*color, 1.0)
        principled.inputs["Emission Strength"].default_value = strength
        material.diffuse_color = (*color, 1.0)
    return material


def _remove_unused_material(material):
    if material is not None and material.users == 0 and not material.use_fake_user:
        bpy.data.materials.remove(material)


def configure_backlight(context, source, *, enabled=True,
                        back_gap_meters=BACKLIGHT_DEFAULT_GAP_METERS,
                        color=BACKLIGHT_DEFAULT_COLOR, strength=BACKLIGHT_DEFAULT_STRENGTH,
                        halo_width_meters=BACKLIGHT_DEFAULT_HALO_WIDTH_METERS,
                        thickness_meters=None):
    """Apply explicit opt-in settings without changing body or shared materials.

    Enabling normalizes native Solidify to an outward shell whose rear is the
    Shrinkwrap plane. Disabling restores the pre-opt-in placement/modifier
    parameters while retaining text edits made since enabling.
    """
    shrinkwrap, solidify = _bound_modifiers(source)
    gap = _positive(back_gap_meters, "Back Gap", True)
    width = _positive(halo_width_meters, "Unity Halo Width", True)
    power = _positive(strength, "Backlight Strength", True)
    rgb = tuple(float(component) for component in color)
    if len(rgb) != 3 or any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in rgb):
        raise ValueError("Backlight color must contain three RGB values between zero and one.")
    scene_scale = _surface_module()._scene_scale_length(context.scene)
    depth_scale = _depth_scale(source)
    thickness = _positive(thickness_meters if thickness_meters is not None
                          else abs(solidify.thickness) * scene_scale * depth_scale, "Thickness", not enabled)
    if enabled:
        _validate_flat_font(source, shrinkwrap, solidify)

    original_data = source.data
    original_materials = list(original_data.materials)
    original_slots = [(slot.link, slot.material) for slot in source.material_slots]
    original_character_materials = [character.material_index for character in original_data.body_format]
    original_active = source.active_material_index
    original_properties = {key: _surface_module()._copy_binding_property(value) for key, value in source.items()}
    original_solidify = {field: getattr(solidify, field) for field in _SOLIDIFY_RESTORE_FIELDS}
    original_shrinkwrap = {"offset": float(shrinkwrap.offset), "wrap_mode": shrinkwrap.wrap_mode}
    copied_data = None
    new_materials = []
    retired_materials = []
    try:
        rear = source.get(BACKLIGHT_PREFIX + "material_ref")
        if rear is not None and (not isinstance(rear, bpy.types.Material)
                                 or rear.get(BACKLIGHT_PREFIX + "role") != "rear_emission"):
            raise RuntimeError("Surface Text rear material metadata is invalid; restore the saved backlight material first.")
        rear_indices = [index for index, slot in enumerate(source.material_slots) if slot.material is rear] if rear else []
        previous_rear_index = rear_indices[-1] if rear_indices else None
        if rear_indices and enabled and rear_indices[-1] != len(source.material_slots) - 1:
            raise RuntimeError("Disable backlight before adding or rearranging front material slots, then configure it again.")
        if enabled or rear_indices:
            if original_data.users > 1 or not getattr(original_data, "is_editable", True):
                copied_data = original_data.copy()
                source.data = copied_data
        if enabled:
            if not source.get(BACKLIGHT_PREFIX + "enabled", False):
                source[BACKLIGHT_PREFIX + "restore"] = {
                    "solidify": original_solidify, "shrinkwrap": original_shrinkwrap}
            if rear_indices:
                source.data.materials.pop(index=rear_indices[-1])
                retired_materials.append(rear)
            if not source.material_slots:
                front = _new_material("Surface Text Front", "default_front")
                new_materials.append(front)
                source.data.materials.append(front)
                source[BACKLIGHT_PREFIX + "default_front_ref"] = front
            rear = _new_material("Surface Text Rear Light", "rear_emission", rgb, power)
            new_materials.append(rear)
            source.data.materials.append(rear)
            index = len(source.material_slots) - 1
            solidify.solidify_mode = "EXTRUDE"
            solidify.thickness = -thickness / (scene_scale * depth_scale)
            solidify.offset = -1.0
            solidify.use_flip_normals = False
            solidify.use_rim = True
            solidify.use_rim_only = False
            solidify.vertex_group = ""
            solidify.thickness_clamp = 0.0
            solidify.use_even_offset = True
            solidify.material_offset = index
            solidify.material_offset_rim = 0
            shrinkwrap.wrap_mode = "ON_SURFACE"
            shrinkwrap.offset = gap / (scene_scale * depth_scale)
            source[BACKLIGHT_PREFIX + "material_ref"] = rear
        else:
            restore = source.get(BACKLIGHT_PREFIX + "restore")
            if restore:
                for field, value in restore.get("solidify", {}).items():
                    setattr(solidify, field, value)
                for field, value in restore.get("shrinkwrap", {}).items():
                    setattr(shrinkwrap, field, value)
                del source[BACKLIGHT_PREFIX + "restore"]
            if rear_indices:
                source.data.materials.pop(index=rear_indices[-1])
                retired_materials.append(rear)
            if BACKLIGHT_PREFIX + "material_ref" in source:
                del source[BACKLIGHT_PREFIX + "material_ref"]
            front = source.get(BACKLIGHT_PREFIX + "default_front_ref")
            if front is not None and len(source.material_slots) == 1 and source.material_slots[0].material is front:
                source.data.materials.pop(index=0)
                retired_materials.append(front)
            if BACKLIGHT_PREFIX + "default_front_ref" in source:
                del source[BACKLIGHT_PREFIX + "default_front_ref"]
        source[BACKLIGHT_PREFIX + "enabled"] = bool(enabled)
        source[BACKLIGHT_PREFIX + "back_gap_meters"] = gap
        source[BACKLIGHT_PREFIX + "color"] = rgb
        source[BACKLIGHT_PREFIX + "strength"] = power
        source[BACKLIGHT_PREFIX + "halo_width_meters"] = width
        # Assigning a private Curve must preserve object-linked front finishes
        # as well as its data-linked slots. Never write the materials themselves.
        for index, (link, material) in enumerate(original_slots):
            if index == previous_rear_index:
                continue
            destination = index - 1 if previous_rear_index is not None and index > previous_rear_index else index
            if destination < len(source.material_slots):
                source.material_slots[destination].link = link
                source.material_slots[destination].material = material
        # Curve material removal remaps character styles as well as slots.
        # Replacing the rear slot must not silently change authored finishes.
        for character, index in zip(source.data.body_format, original_character_materials):
            if not enabled and previous_rear_index is not None and index >= previous_rear_index:
                index = max(0, index - 1)
            character.material_index = index
        source.active_material_index = min(original_active, max(0, len(source.material_slots) - 2 if enabled else len(source.material_slots) - 1))
        context.view_layer.update()
        if enabled:
            validate_backlight_settings(source, context.scene)
    except BaseException:
        try:
            source.data = original_data
            if copied_data is None:
                source.data.materials.clear()
                for material in original_materials:
                    source.data.materials.append(material)
            for index, (link, material) in enumerate(original_slots):
                source.material_slots[index].link = link
                source.material_slots[index].material = material
            for character, index in zip(source.data.body_format, original_character_materials):
                character.material_index = index
            source.active_material_index = original_active
            for field, value in original_solidify.items():
                setattr(solidify, field, value)
            for field, value in original_shrinkwrap.items():
                setattr(shrinkwrap, field, value)
            for key in list(source.keys()):
                if key not in original_properties:
                    del source[key]
            for key, value in original_properties.items():
                source[key] = value
        finally:
            if copied_data is not None and copied_data.users == 0:
                bpy.data.curves.remove(copied_data)
            for material in new_materials:
                _remove_unused_material(material)
        raise
    for material in retired_materials:
        _remove_unused_material(material)
    return read_backlight_settings(source)


class RR_OT_configure_surface_text_backlight(bpy.types.Operator):
    bl_idname = "rr_builder.configure_surface_text_backlight"
    bl_label = "Configure Backlight"
    bl_description = "Configure a rear emitting shell and a separate rear-to-wall gap for the selected Surface Text"
    bl_options = {"REGISTER", "UNDO"}

    enabled: bpy.props.BoolProperty(name="Backlight", default=True)
    back_gap_meters: bpy.props.FloatProperty(name="Back Gap (m)", description="Distance from the rear of the letters to the wall along the Surface Text projection", default=0.05, min=0.0, precision=4)
    thickness_meters: bpy.props.FloatProperty(name="Thickness (m)", description="Letter thickness, independent of the rear-to-wall gap", default=0.1, min=0.00001, precision=4)
    color: bpy.props.FloatVectorProperty(name="Color", subtype="COLOR", size=3, default=BACKLIGHT_DEFAULT_COLOR, min=0.0, max=1.0)
    strength: bpy.props.FloatProperty(name="Strength", default=5.0, min=0.0)
    halo_width_meters: bpy.props.FloatProperty(name="Unity Halo Width (m)", description="Width of the generated wall halo in Unity; Blender uses native Cycles emission", default=0.08, min=0.0, precision=4)

    @classmethod
    def poll(cls, context):
        source = context.object
        return (context.mode == "OBJECT" and source is not None and source.type == "FONT"
                and source.get("rr_surface_text_role") == "surface_text"
                and getattr(source, "is_editable", True))

    def invoke(self, context, _event):
        source = context.object
        settings = read_backlight_settings(source)
        self.enabled = settings["enabled"] if BACKLIGHT_PREFIX + "enabled" in source else True
        self.back_gap_meters = settings["backGapMeters"]
        self.color = settings["color"]
        self.strength = settings["strength"]
        self.halo_width_meters = settings["haloWidthMeters"]
        _shrinkwrap, solidify = _bound_modifiers(source)
        self.thickness_meters = abs(solidify.thickness) * _surface_module()._scene_scale_length(context.scene) * _depth_scale(source)
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, _context):
        self.layout.prop(self, "enabled")
        column = self.layout.column()
        column.enabled = self.enabled
        for name in ("back_gap_meters", "thickness_meters", "color", "strength", "halo_width_meters"):
            column.prop(self, name)
        column.label(text="Blender wall illumination uses native Cycles emission.")

    def execute(self, context):
        try:
            configure_backlight(context, context.object, enabled=self.enabled,
                                back_gap_meters=self.back_gap_meters, thickness_meters=self.thickness_meters,
                                color=self.color, strength=self.strength, halo_width_meters=self.halo_width_meters)
        except Exception as exception:
            self.report({"ERROR"}, str(exception))
            return {"CANCELLED"}
        self.report({"INFO"}, "Configured rear Surface Text emission." if self.enabled else "Disabled backlight and restored the previous placement.")
        return {"FINISHED"}
