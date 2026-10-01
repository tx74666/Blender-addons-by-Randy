"""Compact material-layer controls for RR Helper's Texture page."""

import bpy

try:
    from . import rr_ring_stack
except ImportError:
    import rr_ring_stack


def current_material(context):
    """Respect pinned shader editors, including their nested group views.

    A stack always manages the material's Surface. With no Shader Editor open,
    the active object's material is the same target shown by Material Properties.
    Conflicting editors deliberately require the user to choose one.
    """
    def from_space(space):
        if getattr(space, "tree_type", "") != "ShaderNodeTree":
            return None
        if getattr(space, "shader_type", "OBJECT") != "OBJECT":
            return None
        material = getattr(space, "id", None)
        return material if isinstance(material, bpy.types.Material) else None

    if getattr(getattr(context, "area", None), "type", None) == "NODE_EDITOR":
        return from_space(getattr(context, "space_data", None))
    screen = getattr(getattr(context, "window", None), "screen", None)
    editors = [area.spaces.active for area in getattr(screen, "areas", ())
               if area.type == "NODE_EDITOR" and area.spaces.active.tree_type == "ShaderNodeTree"]
    if editors:
        materials = {from_space(space) for space in editors}
        materials.discard(None)
        return next(iter(materials)) if len(materials) == 1 else None
    return getattr(getattr(context, "active_object", None), "active_material", None)


class RR_UL_ring_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        label = row.row(align=True)
        label.active = item.enabled
        label.prop(item, "name", text="", emboss=False,
                   icon="CURVE_BEZCIRCLE" if item.mode == "ARC" else "MESH_CIRCLE")

    def draw_filter(self, context, layout):
        # Layer order is compositing order. No alphabetic sorting or second search.
        pass


class RR_OT_ring_stack_action(bpy.types.Operator):
    bl_idname = "rr_builder.ring_stack_action"
    bl_label = "Ring Stack"
    bl_options = {"REGISTER", "UNDO"}

    action: bpy.props.EnumProperty(items=(
        ("ADD", "Add Ring", "Add a new top layer without changing the Base Shader"),
        ("DUPLICATE", "Duplicate Ring", "Copy this layer above the original"),
        ("DELETE", "Delete Ring", "Remove this layer; the last layer restores the Base Shader"),
        ("UP", "Move Up", "Give this layer higher priority"),
        ("DOWN", "Move Down", "Give this layer lower priority"),
        ("REBUILD", "Update Stack", "Retry applying layer settings after resolving the reported issue"),
    ))

    @classmethod
    def description(cls, context, properties):
        descriptions = {
            "ADD": "Add a ring above the existing layers. The original Base Shader stays connected",
            "DUPLICATE": "Duplicate the selected ring with independent radius, width and angle settings",
            "DELETE": "Delete the selected ring. Deleting the last ring restores the original Base Shader",
            "UP": "Move this ring above the previous layer so it covers that layer",
            "DOWN": "Move this ring below the next layer",
            "REBUILD": "Apply the current layer settings after resolving the reported issue",
        }
        return descriptions.get(properties.action, "Manage Ring Stack layers")

    @classmethod
    def poll(cls, context):
        material = current_material(context)
        return material is not None and not rr_ring_stack.validate_target(material)

    def execute(self, context):
        material = current_material(context)
        try:
            if material is None:
                raise ValueError("Choose one material to edit.")
            stack = rr_ring_stack.get_stack(material)
            index = stack.active_index
            if self.action == "ADD":
                rr_ring_stack.add_ring(material)
            elif self.action == "DUPLICATE":
                rr_ring_stack.duplicate_ring(material, index)
            elif self.action == "DELETE":
                rr_ring_stack.delete_ring(material, index)
            elif self.action in {"UP", "DOWN"}:
                rr_ring_stack.move_ring(material, index, -1 if self.action == "UP" else 1)
            else:
                rr_ring_stack.rebuild_stack(material)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if context.area is not None:
            context.area.tag_redraw()
        return {"FINISHED"}


def draw_ring_stack(layout, context):
    material = current_material(context)
    if material is None:
        layout.label(text="Choose a material to edit.", icon="INFO")
        return
    layout.label(text=material.name, icon="MATERIAL")
    error = rr_ring_stack.validate_target(material)
    if error:
        for index, line in enumerate(_message_lines(error)):
            layout.label(text=line, icon="INFO" if index == 0 else "NONE")
        return
    stack = rr_ring_stack.get_stack(material)
    if stack.ring_mask is None and rr_ring_stack.find_ring_mask(material) is None:
        layout.label(text="Add the existing Ring Mask to this file.", icon="INFO")
        layout.prop(stack, "ring_mask", text="Ring Mask")
        return

    if stack.layers:
        layout.template_list("RR_UL_ring_layers", "", stack, "layers", stack, "active_index",
                             rows=min(6, max(2, len(stack.layers))), sort_lock=True)
    row = layout.row(align=True)
    row.operator("rr_builder.ring_stack_action", text="Add Ring", icon="ADD").action = "ADD"
    selected = 0 <= stack.active_index < len(stack.layers)
    actions = row.row(align=True)
    actions.enabled = selected
    actions.operator("rr_builder.ring_stack_action", text="", icon="DUPLICATE").action = "DUPLICATE"
    actions.operator("rr_builder.ring_stack_action", text="", icon="TRASH").action = "DELETE"
    up = actions.row(align=True)
    up.enabled = stack.active_index > 0
    up.operator("rr_builder.ring_stack_action", text="", icon="TRIA_UP").action = "UP"
    down = actions.row(align=True)
    down.enabled = stack.active_index < len(stack.layers) - 1
    down.operator("rr_builder.ring_stack_action", text="", icon="TRIA_DOWN").action = "DOWN"

    if selected:
        layer = stack.layers[stack.active_index]
        details = layout.column(align=True)
        details.use_property_split = True
        details.use_property_decorate = False
        details.prop(layer, "mode", text="Shape", expand=True)
        details.prop(layer, "radius", text="Radius")
        details.prop(layer, "width", text="Width")
        details.prop(layer, "softness", text="Softness")
        if layer.mode == "ARC":
            details.prop(layer, "start_angle", text="Start Angle")
            details.prop(layer, "sweep_angle", text="Sweep Angle")
        details.prop(layer, "source_material", text="Material")
        if layer.source_material is None:
            layout.label(text="Choose a material for this layer.", icon="INFO")

    if stack.error:
        layout.label(text="Changes not applied.", icon="ERROR")
        for line in _message_lines(stack.error):
            layout.label(text=line)
        layout.operator("rr_builder.ring_stack_action", text="Update Stack", icon="FILE_REFRESH").action = "REBUILD"


def _message_lines(message):
    import textwrap
    return textwrap.wrap(message, width=43) or [message]


CLASSES = (RR_UL_ring_layers, RR_OT_ring_stack_action)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        if hasattr(cls, "bl_rna") and cls.is_registered:
            bpy.utils.unregister_class(cls)
