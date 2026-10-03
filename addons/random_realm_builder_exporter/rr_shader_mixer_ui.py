"""Edit-time + / - controls for native Mix Shaders and Ring Group nodes.

The saved node remains ShaderNodeGroup. This module only draws a small overlay
and handles clicks in those rectangles; it has no shader or save-time callbacks.
"""

import math

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

if __package__:
    from . import rr_shader_mixer as mixer
    from . import rr_ring_nodes as ring
else:
    import rr_shader_mixer as mixer
    import rr_ring_nodes as ring


_DRAW_HANDLE = None
_KEYMAP_ITEMS = []
_SHADER = None
_LAST_DRAW_ERROR = None


def button_layout_rects(top_left, header_bottom_right, *, ui_scale=1.0, region_size=None):
    """Pure pixel layout; returns ADD/REMOVE rectangles, or {} when too small.

    Input points describe the already projected node header. Using its projected
    height makes controls follow zoom; ui_scale only sets legibility limits.
    Rectangles use (left, bottom, right, top), matching mouse region coordinates.
    """
    x, top = top_left
    right, bottom = header_bottom_right
    numbers = (x, top, right, bottom, ui_scale)
    if not all(math.isfinite(value) for value in numbers) or ui_scale <= 0:
        return {}
    height, width = top - bottom, right - x
    size = min(height * 0.72, 20.0 * ui_scale)
    if size < 8.0 * ui_scale or width < max(110.0 * ui_scale, height * 5.5):
        return {}
    margin = max(1.0, height * 0.14)
    gap = max(2.0, height * 0.12)
    button_bottom = bottom + (height - size) * 0.5
    # Native disclosure is at the far left; the datablock selector and sockets
    # are below this header. Reserve the remaining header for its normal title.
    remove_right = right - margin
    add_right = remove_right - size - gap
    result = {
        "ADD": (add_right - size, button_bottom, add_right, button_bottom + size),
        "REMOVE": (remove_right - size, button_bottom, remove_right, button_bottom + size),
    }
    if region_size is not None:
        region_width, region_height = region_size
        if not all(math.isfinite(value) and value > 0 for value in region_size):
            return {}
        if any(left < 0 or low < 0 or high_x > region_width or high_y > region_height
               for left, low, high_x, high_y in result.values()):
            return {}
    return result


def _active_node(context):
    if (getattr(getattr(context, "area", None), "type", "") != "NODE_EDITOR"
            or getattr(getattr(context, "region", None), "type", "") != "WINDOW"):
        return None
    tree = mixer._editor_tree(context)
    node = tree.nodes.active if tree is not None else None
    if (node is None or not (mixer.is_mixer(node) or ring.is_ring_group(node)) or not node.select
            or node.hide
            or node.is_property_readonly("node_tree")):
        return None
    return node


def _slot_count(node):
    if ring.is_ring_group(node):
        return len(ring._mask_ids(node.node_tree))
    return len(mixer._pairs(node.node_tree))


def node_button_rects(context, node=None):
    """Project only the active selected supported node; never scan the scene."""
    try:
        active = _active_node(context)
        if active is None or (node is not None and node != active):
            return {}
        node = active
        # Same DPI contract used by Blender's bundled Node Wrangler: locations
        # are unscaled node coordinates; dimensions are absolute View2D bounds.
        dpi_factor = context.preferences.system.dpi / 72.0
        location = node.location_absolute
        if node.dimensions.x <= 0 or node.dimensions.y <= 0:
            return {}
        view = context.region.view2d
        left, top = location.x * dpi_factor, location.y * dpi_factor
        top_left = view.view_to_region(left, top, clip=False)
        bottom_right = view.view_to_region(left + node.dimensions.x, top - 20.0 * dpi_factor,
                                          clip=False)
        return button_layout_rects(top_left, bottom_right, ui_scale=dpi_factor,
                                   region_size=(context.region.width, context.region.height))
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return {}


def button_at_point(rectangles, point):
    """Use half-open boundaries so gaps and neighbouring node UI pass through."""
    x, y = point
    return next((action for action, (left, bottom, right, top) in rectangles.items()
                 if left <= x < right and bottom <= y < top), None)


def _fill_rect(shader, rect, color):
    left, bottom, right, top = rect
    positions = ((left, bottom), (right, bottom), (right, top), (left, top))
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": positions},
                     indices=((0, 1, 2), (0, 2, 3))).draw(shader)


def _draw_button(shader, rect, action, enabled):
    left, bottom, right, top = rect
    width, height = right - left, top - bottom
    _fill_rect(shader, rect, (0.42, 0.42, 0.42, 0.95))
    border = max(0.6, height * 0.055)
    _fill_rect(shader, (left + border, bottom + border, right - border, top - border),
               (0.20, 0.20, 0.20, 0.98) if enabled else (0.17, 0.17, 0.17, 0.98))
    center_x, center_y = (left + right) * 0.5, (bottom + top) * 0.5
    half_length = min(width, height) * 0.23
    half_stroke = max(0.7, height * 0.045)
    color = (0.94, 0.94, 0.94, 1.0) if enabled else (0.42, 0.42, 0.42, 1.0)
    _fill_rect(shader, (center_x - half_length, center_y - half_stroke,
                        center_x + half_length, center_y + half_stroke), color)
    if action == "ADD":
        _fill_rect(shader, (center_x - half_stroke, center_y - half_length,
                            center_x + half_stroke, center_y + half_length), color)


def draw_overlay():
    global _SHADER, _LAST_DRAW_ERROR
    context = bpy.context
    rectangles = node_button_rects(context)
    if not rectangles:
        return
    blend = gpu.state.blend_get()
    try:
        if _SHADER is None:
            _SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        _SHADER.bind()
        node = _active_node(context)
        can_remove = node is not None and _slot_count(node) > 1
        for action, rectangle in rectangles.items():
            _draw_button(_SHADER, rectangle, action, action == "ADD" or can_remove)
        _LAST_DRAW_ERROR = None
    except Exception as exc:
        # A stale node during Undo or a GPU context change must not break the
        # rest of Blender's UI or repeatedly flood its console.
        message = str(exc)
        _SHADER = None
        if message != _LAST_DRAW_ERROR:
            print("RR Helper: node slot button drawing failed:", message)
            _LAST_DRAW_ERROR = message
    finally:
        try:
            gpu.shader.unbind()
        finally:
            gpu.state.blend_set(blend)


class RR_OT_click_shader_mixer_buttons(bpy.types.Operator):
    bl_idname = "rr_builder.click_shader_mixer_buttons"
    bl_label = "Node Slot Controls"
    bl_description = "Add or remove one Mask input on Ring Group, or one Mask / Shader pair on Mix Shaders"
    # This outer event operator owns the Undo step for its nested EXEC call.
    bl_options = {"INTERNAL", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_node(context) is not None

    def invoke(self, context, event):
        node = _active_node(context)
        rectangles = node_button_rects(context, node)
        action = button_at_point(rectangles, (event.mouse_region_x, event.mouse_region_y))
        if action is None:
            return {"PASS_THROUGH"}
        if action == "REMOVE" and _slot_count(node) <= 1:
            return {"CANCELLED"}
        try:
            if ring.is_ring_group(node):
                if action == "ADD":
                    result = bpy.ops.rr_builder.add_mask_slot("EXEC_DEFAULT", node_name=node.name)
                else:
                    result = bpy.ops.rr_builder.remove_mask_slot("EXEC_DEFAULT", node_name=node.name)
            elif action == "ADD":
                result = bpy.ops.rr_builder.add_shader_slot("EXEC_DEFAULT", node_name=node.name)
            else:
                result = bpy.ops.rr_builder.remove_shader_slot("EXEC_DEFAULT", node_name=node.name)
        except (RuntimeError, ReferenceError) as exc:
            # bpy.ops raises for the nested operator's ERROR report (for
            # example, an animated or customized group that cannot be rebuilt).
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        context.area.tag_redraw()
        return result


CLASSES = (RR_OT_click_shader_mixer_buttons,)


def register():
    global _DRAW_HANDLE
    try:
        for cls in CLASSES:
            if not getattr(cls, "is_registered", False):
                bpy.utils.register_class(cls)
        if _DRAW_HANDLE is None:
            _DRAW_HANDLE = bpy.types.SpaceNodeEditor.draw_handler_add(draw_overlay, (), "WINDOW", "POST_PIXEL")
        if not _KEYMAP_ITEMS:
            manager = bpy.context.window_manager
            config = manager.keyconfigs.addon if manager is not None else None
            if config is not None:
                keymap = config.keymaps.new(name="Node Editor", space_type="NODE_EDITOR", region_type="WINDOW")
                item = keymap.keymap_items.new(RR_OT_click_shader_mixer_buttons.bl_idname,
                                               "LEFTMOUSE", "PRESS", any=True, head=True)
                _KEYMAP_ITEMS.append((keymap, item))
    except Exception:
        unregister()
        raise


def unregister():
    global _DRAW_HANDLE, _SHADER, _LAST_DRAW_ERROR
    for keymap, item in reversed(_KEYMAP_ITEMS):
        try:
            keymap.keymap_items.remove(item)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    _KEYMAP_ITEMS.clear()
    if _DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceNodeEditor.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        except (ReferenceError, RuntimeError, ValueError):
            pass
        _DRAW_HANDLE = None
    _SHADER = None
    _LAST_DRAW_ERROR = None
    for cls in reversed(CLASSES):
        if hasattr(cls, "bl_rna") and cls.is_registered:
            bpy.utils.unregister_class(cls)
