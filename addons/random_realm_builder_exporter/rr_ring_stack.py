"""Material-owned Ring layers, with transactional shader graph maintenance.

The library Ring Mask is always referenced, never rebuilt.  A single managed
wrapper is spliced into Surface; the user's original graph stays outside it.
"""

import math
import uuid
from contextlib import contextmanager

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, IntProperty, PointerProperty, StringProperty)

try:
    from . import rr_material_shader as shader
except ImportError:  # Support Blender's direct add-on module reload path.
    import rr_material_shader as shader


_BUSY = set()
_OWNER = "rr_ring_stack_id"
_ROLE = "rr_ring_role"
_UID = "rr_ring_uid"
_LAYER_FIELDS = ("uid", "name", "radius", "width", "softness", "enabled",
                 "source_material", "mode", "start_angle", "sweep_angle")


@contextmanager
def _editing(material):
    key = material.as_pointer()
    previous = key in _BUSY
    _BUSY.add(key)
    try:
        yield
    finally:
        if not previous:
            _BUSY.discard(key)


def _changed(self, _context):
    material = self.id_data
    if not isinstance(material, bpy.types.Material) or material.as_pointer() in _BUSY:
        return
    try:
        rebuild_stack(material)
    except Exception as exc:
        material.rr_ring_stack.error = str(exc)


def _shader_tree_poll(_self, tree):
    return tree.bl_idname == "ShaderNodeTree"


class RR_RingLayer(bpy.types.PropertyGroup):
    uid: StringProperty(options={"HIDDEN"})
    name: StringProperty(name="Name", default="Ring", update=_changed)
    radius: FloatProperty(name="Radius", description="Inner radius in the Ring Mask's normalized UV space",
                          default=0.25, min=0.0, soft_max=1.0, update=_changed)
    width: FloatProperty(name="Width", default=0.05, min=0.0, soft_max=1.0, update=_changed)
    softness: FloatProperty(name="Softness", default=0.01, min=0.0, soft_max=0.2, update=_changed)
    enabled: BoolProperty(name="Enabled", default=True, update=_changed)
    source_material: PointerProperty(name="Material", type=bpy.types.Material, update=_changed)
    mode: EnumProperty(name="Shape", items=(("RING", "Ring", "A complete ring"),
                                            ("ARC", "Arc", "A counterclockwise portion of a ring")),
                       default="RING", update=_changed)
    start_angle: FloatProperty(name="Start Angle", description="Degrees counterclockwise from UV right (+U)",
                               default=0.0, min=-360.0, max=360.0, update=_changed)
    sweep_angle: FloatProperty(name="Sweep", description="Counterclockwise arc length in degrees; 360 is a full ring",
                               default=90.0, min=0.0, max=360.0, update=_changed)


class RR_RingStack(bpy.types.PropertyGroup):
    layers: CollectionProperty(type=RR_RingLayer)
    active_index: IntProperty(default=0, min=0)
    ring_mask: PointerProperty(name="Ring Mask", type=bpy.types.ShaderNodeTree,
                              poll=_shader_tree_poll, update=_changed)
    generated_group: PointerProperty(type=bpy.types.ShaderNodeTree)
    stack_id: StringProperty(options={"HIDDEN"})
    output_name: StringProperty(options={"HIDDEN"})
    base_node_name: StringProperty(options={"HIDDEN"})
    base_socket_identifier: StringProperty(options={"HIDDEN"})
    base_socket_index: IntProperty(default=-1, options={"HIDDEN"})
    error: StringProperty(options={"HIDDEN"})


def get_stack(material):
    return material.rr_ring_stack if material is not None else None


def _mask_error(group):
    if group is None or group.bl_idname != "ShaderNodeTree":
        return "Load the existing Ring Mask shader group into this file first."
    sockets = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    for name in ("Inner Radius", "Ring Width", "Edge Softness"):
        matches = [item for item in sockets if item.in_out == "INPUT" and item.name == name
                   and item.socket_type == "NodeSocketFloat"]
        if len(matches) != 1:
            return "Ring Mask needs a Float input named {}.".format(name)
    outputs = [item for item in sockets if item.in_out == "OUTPUT" and item.name == "Mask"
               and item.socket_type == "NodeSocketFloat"]
    if len(outputs) != 1:
        return "Ring Mask needs one Float output named Mask."
    active = [node for node in group.nodes if node.bl_idname == "NodeGroupOutput" and node.is_active_output]
    if len(active) != 1 or not active[0].inputs.get("Mask") or not active[0].inputs["Mask"].is_linked:
        return "Ring Mask has no linked Mask output."
    return ""


def find_ring_mask(material=None):
    stack = get_stack(material)
    if stack is not None and stack.ring_mask is not None and not _mask_error(stack.ring_mask):
        return stack.ring_mask
    exact = bpy.data.node_groups.get("Ring Mask")
    if exact is not None and not _mask_error(exact):
        return exact
    return next((group for group in bpy.data.node_groups
                 if group.name.startswith("Ring Mask.") and not _mask_error(group)), None)


def _wrapper(material, stack):
    nodes = [node for node in material.node_tree.nodes if node.get(_OWNER) == stack.stack_id
             and stack.stack_id]
    if len(nodes) != 1 or nodes[0].bl_idname != "ShaderNodeGroup":
        raise ValueError("The Ring Stack node was changed or removed. Undo that edit before updating its layers.")
    return nodes[0]


def _base_socket(material, stack):
    if stack.base_socket_index < 0:
        return None
    node = material.node_tree.nodes.get(stack.base_node_name)
    if node is None:
        raise ValueError("The original Base Shader node was changed or removed.")
    sockets = [socket for socket in node.outputs if socket.identifier == stack.base_socket_identifier]
    if len(sockets) != 1:
        raise ValueError("The original Base Shader socket was changed or removed.")
    return sockets[0]


def _same_link(socket, expected):
    links = list(socket.links)
    return not links if expected is None else (
        len(links) == 1 and links[0].is_valid and links[0].from_socket == expected)


def _managed_state(material):
    stack = get_stack(material)
    tree = material.node_tree
    output = shader._active_material_output(tree)
    if output is None:
        raise ValueError("No active Material Output found.")
    surface = output.inputs.get("Surface")
    if surface is None:
        raise ValueError("The active Material Output has no Surface input.")
    if stack.generated_group is None:
        if stack.stack_id:
            raise ValueError("The managed Ring Stack group was removed. Undo that edit before updating its layers.")
        links = list(surface.links)
        if len(links) > 1 or (links and not links[0].is_valid):
            raise ValueError("The current Surface link is invalid.")
        base = links[0].from_socket if links else None
        if base is not None and base.type != "SHADER":
            raise ValueError("The Base Surface must be a Shader output.")
        return output, surface, base, None
    group = stack.generated_group
    if group.get(_OWNER) != stack.stack_id:
        raise ValueError("This is not the managed Ring Stack group.")
    if not group.is_editable or group.library is not None:
        raise ValueError("The Ring Stack group is read-only.")
    if shader._has_animation(group):
        raise ValueError("The Ring Stack group is animated and cannot be rebuilt safely.")
    node = _wrapper(material, stack)
    if node.node_tree != group or output.name != stack.output_name:
        raise ValueError("The Ring Stack output was changed. Undo the manual rewiring before updating its layers.")
    base_input, shader_output = node.inputs.get("Base Shader"), node.outputs.get("Shader")
    if (base_input is None or shader_output is None or base_input.type != "SHADER"
            or shader_output.type != "SHADER"):
        raise ValueError("The Ring Stack interface was changed.")
    sockets = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    if len(sockets) != 2:
        raise ValueError("The Ring Stack interface was changed.")
    base = _base_socket(material, stack)
    if not _same_link(base_input, base):
        raise ValueError("The Ring Stack Base Shader was rewired. Undo that edit before updating its layers.")
    outgoing = list(shader_output.links)
    if not _same_link(surface, shader_output) or len(outgoing) != 1 or outgoing[0].to_socket != surface:
        raise ValueError("The Ring Stack output was rewired. Undo that edit before updating its layers.")
    return output, surface, base, node


def validate_target(material):
    try:
        if material is None or not isinstance(material, bpy.types.Material):
            return "Open a material in the Shader Editor first."
        if not material.use_nodes or material.node_tree is None:
            return "The target material needs a shader node tree."
        if material.library is not None or not material.is_editable or not material.node_tree.is_editable:
            return "The target material is read-only."
        if shader._has_animation(material.node_tree):
            return "Animated material nodes cannot be updated safely by Ring Stack."
        _managed_state(material)
    except (ValueError, ReferenceError) as exc:
        return str(exc)
    return ""


def _check_target(material):
    error = validate_target(material)
    if error:
        raise ValueError(error)


def _source_reaches(source, target, seen=None):
    if source == target:
        return True
    seen = set() if seen is None else seen
    if source in seen:
        return False
    seen.add(source)
    stack = get_stack(source)
    return stack is not None and any(layer.enabled and layer.source_material is not None
                                     and _source_reaches(layer.source_material, target, seen)
                                     for layer in stack.layers)


def _check_dependency(tree, target, existing):
    if shader._depends_on_group(tree, target.node_tree) or (
            existing is not None and shader._depends_on_group(tree, existing)):
        raise ValueError("This material would create a recursive Ring Stack dependency.")


def _source_group(source, target, existing, created):
    if _source_reaches(source, target):
        raise ValueError("A Ring cannot use its own material or a material that depends on it.")
    # Only the selected Surface dependency graph matters, not unused nodes.
    nodes, surface = shader.surface_dependency_nodes(source)
    if surface.type != "SHADER":
        raise ValueError("The Ring material's Surface must have a Shader output.")
    for node in nodes:
        if node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None:
            _check_dependency(node.node_tree, target, existing)
    reusable = shader.generated_shader_group(source)
    if reusable is not None:
        _check_dependency(reusable, target, existing)
    group, _node, status = shader.import_material_shader(source, target=None)
    if status == "CREATED":
        created.append(group)
    _check_dependency(group, target, existing)
    return group


def _tag(node, layer, role):
    node[_UID] = layer.uid
    node[_ROLE] = role
    node.select = False
    return node


def _math(tree, layer, role, operation, x, y):
    node = _tag(tree.nodes.new("ShaderNodeMath"), layer, role)
    node.operation = operation
    node.location = (x, y)
    return node


def _arc_factor(tree, layer, mask, x, y):
    if layer.mode == "RING" or layer.sweep_angle >= 360.0:
        return mask
    if layer.sweep_angle <= 0.0:
        zero = _tag(tree.nodes.new("ShaderNodeValue"), layer, "arc_zero")
        zero.outputs[0].default_value = 0.0
        zero.location = (x, y)
        return zero.outputs[0]
    uv = _tag(tree.nodes.new("ShaderNodeTexCoord"), layer, "arc_uv")
    uv.location = (x - 1000, y - 160)
    separate = _tag(tree.nodes.new("ShaderNodeSeparateXYZ"), layer, "arc_xy")
    separate.location = (x - 820, y - 160)
    tree.links.new(uv.outputs["UV"], separate.inputs[0])
    u = _math(tree, layer, "arc_u", "SUBTRACT", x - 640, y - 120)
    v = _math(tree, layer, "arc_v", "SUBTRACT", x - 640, y - 280)
    u.inputs[1].default_value = v.inputs[1].default_value = 0.5
    tree.links.new(separate.outputs["X"], u.inputs[0])
    tree.links.new(separate.outputs["Y"], v.inputs[0])
    angle = _math(tree, layer, "arc_angle", "ARCTAN2", x - 460, y - 160)
    tree.links.new(v.outputs[0], angle.inputs[0])
    tree.links.new(u.outputs[0], angle.inputs[1])
    start = _math(tree, layer, "arc_start", "SUBTRACT", x - 280, y - 160)
    start.inputs[1].default_value = math.radians(layer.start_angle)
    tree.links.new(angle.outputs[0], start.inputs[0])
    wrap = _math(tree, layer, "arc_wrap", "FLOORED_MODULO", x - 100, y - 160)
    wrap.inputs[1].default_value = math.tau
    tree.links.new(start.outputs[0], wrap.inputs[0])
    window = _math(tree, layer, "arc_window", "LESS_THAN", x + 80, y - 160)
    window.inputs[1].default_value = math.radians(layer.sweep_angle)
    tree.links.new(wrap.outputs[0], window.inputs[0])
    factor = _math(tree, layer, "arc_factor", "MULTIPLY", x + 260, y)
    tree.links.new(mask, factor.inputs[0])
    tree.links.new(window.outputs[0], factor.inputs[1])
    return factor.outputs[0]


def _stage_group(material, mask_group, created):
    stack = get_stack(material)
    tree = bpy.data.node_groups.new(".RR Ring Stack staging", "ShaderNodeTree")
    tree.interface.new_socket(name="Base Shader", in_out="INPUT", socket_type="NodeSocketShader")
    tree.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    shader._set_shader_color_tag(tree)
    try:
        incoming = tree.nodes.new("NodeGroupInput")
        incoming.location = (-200, 0)
        incoming.select = False
        previous = incoming.outputs["Base Shader"]
        for index, layer in enumerate(reversed(stack.layers)):
            if not layer.enabled or layer.source_material is None:
                continue
            if layer.source_material == material:
                raise ValueError("A Ring cannot use its own material.")
            if not all(math.isfinite(value) for value in (
                    layer.radius, layer.width, layer.softness, layer.start_angle, layer.sweep_angle)):
                raise ValueError("Ring values must be finite numbers.")
            source = _source_group(layer.source_material, material, stack.generated_group, created)
            x, y = index * 640, -index * 560
            mask = _tag(tree.nodes.new("ShaderNodeGroup"), layer, "mask")
            mask.node_tree = mask_group
            mask.label = layer.name
            mask.location = (x, y - 60)
            mask.inputs["Inner Radius"].default_value = layer.radius
            mask.inputs["Ring Width"].default_value = layer.width
            mask.inputs["Edge Softness"].default_value = layer.softness
            implementation = _tag(tree.nodes.new("ShaderNodeGroup"), layer, "shader")
            implementation.node_tree = source
            implementation.label = layer.source_material.name
            implementation.location = (x + 200, y - 300)
            factor = _arc_factor(tree, layer, mask.outputs["Mask"], x, y)
            mix = _tag(tree.nodes.new("ShaderNodeMixShader"), layer, "mix")
            mix.label = layer.name
            mix.location = (x + 440, y)
            tree.links.new(factor, mix.inputs[0])
            tree.links.new(previous, mix.inputs[1])
            tree.links.new(implementation.outputs["Shader"], mix.inputs[2])
            previous = mix.outputs[0]
        outgoing = tree.nodes.new("NodeGroupOutput")
        outgoing.is_active_output = True
        outgoing.select = False
        outgoing.location = (max((node.location.x for node in tree.nodes), default=0) + 240, 0)
        tree.links.new(previous, outgoing.inputs["Shader"])
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise


def _relink(tree, node, base, surface):
    base_input = node.inputs["Base Shader"]
    if not _same_link(base_input, base):
        for link in list(base_input.links):
            tree.links.remove(link)
        if base is not None:
            tree.links.new(base, base_input)
    if not _same_link(surface, node.outputs["Shader"]):
        tree.links.new(node.outputs["Shader"], surface)


def _remove_unused(group):
    if group is not None and group.users == 0 and group.get(_OWNER):
        bpy.data.node_groups.remove(group)


def _private_group(material, group, wrapper):
    # A copied Material carries the same saved pointer and wrapper. Check real
    # owners as well as ID.users, which also includes custom/fake-user handles.
    if group.users > 2 or group.use_fake_user:
        return False
    for owner in bpy.data.materials:
        if owner != material and get_stack(owner).generated_group == group:
            return False
    return True


def _commit_group(material, stage):
    stack = get_stack(material)
    output, surface, base, node = _managed_state(material)
    existing = stack.generated_group
    if existing is not None and _private_group(material, existing, node):
        # Both the wrapper and the material property own this ID. Preserve it
        # when private, including socket identifiers and outside references.
        backup = existing.copy()
        restore_check = stage.copy()
        try:
            restore_check.nodes.clear()
            shader._copy_node_graph(backup.nodes, backup.links, restore_check)
            try:
                existing.nodes.clear()
                shader._copy_node_graph(stage.nodes, stage.links, existing)
            except Exception:
                existing.nodes.clear()
                shader._copy_node_graph(backup.nodes, backup.links, existing)
                raise
            shader._set_shader_color_tag(existing)
            existing.update_tag()
        finally:
            bpy.data.node_groups.remove(restore_check)
            bpy.data.node_groups.remove(backup)
        return existing
    identifier = uuid.uuid4().hex
    stage[_OWNER] = identifier
    stage.name = "Ring Stack " + material.name
    previous_id = stack.stack_id
    created_node = node is None
    if created_node:
        node = material.node_tree.nodes.new("ShaderNodeGroup")
        node.name = "Ring Stack"
        node.label = "Ring Stack"
        node.select = False
        node.location = (output.location.x - 240, output.location.y + 220)
    try:
        node.node_tree = stage
        node[_OWNER] = identifier
        _relink(material.node_tree, node, base, surface)
        stack.generated_group = stage
        stack.stack_id = identifier
        stack.output_name = output.name
        stack.base_node_name = base.node.name if base is not None else ""
        stack.base_socket_identifier = base.identifier if base is not None else ""
        stack.base_socket_index = (list(base.node.outputs).index(base) if base is not None else -1)
    except Exception:
        if created_node:
            material.node_tree.nodes.remove(node)
            if base is not None:
                material.node_tree.links.new(base, surface)
        else:
            node.node_tree = existing
            node[_OWNER] = previous_id
            _relink(material.node_tree, node, base, surface)
        stack.generated_group = existing
        stack.stack_id = previous_id
        raise
    _remove_unused(existing)
    return stage


def rebuild_stack(material):
    """Build off to the side, then commit. Failures leave the visible graph intact."""
    _check_target(material)
    stack = get_stack(material)
    with _editing(material):
        if not len(stack.layers):
            return remove_stack(material)
        mask = stack.ring_mask if stack.ring_mask is not None else find_ring_mask(material)
        error = _mask_error(mask)
        if error:
            raise ValueError(error)
        _check_dependency(mask, material, stack.generated_group)
        stage, created = None, []
        try:
            stage = _stage_group(material, mask, created)
            result = _commit_group(material, stage)
            if result == stage:
                stage = None
            stack.ring_mask = mask
            stack.error = ""
            material.node_tree.update_tag()
            return stack
        except Exception as exc:
            stack.error = str(exc)
            if stage is not None:
                bpy.data.node_groups.remove(stage)
                stage = None
            for group in created:
                # These temporary imports only have the importer's fake user.
                if group.users == int(group.use_fake_user):
                    bpy.data.node_groups.remove(group)
            raise
        finally:
            if stage is not None:
                bpy.data.node_groups.remove(stage)


def _snapshot(stack):
    return [dict((name, getattr(layer, name)) for name in _LAYER_FIELDS) for layer in stack.layers]


def _restore_layers(stack, values):
    stack.layers.clear()
    for data in values:
        layer = stack.layers.add()
        for name, value in data.items():
            setattr(layer, name, value)


def _mutate(material, edit):
    _check_target(material)
    stack = get_stack(material)
    values, index = _snapshot(stack), stack.active_index
    with _editing(material):
        try:
            result = edit(stack)
            rebuild_stack(material)
            return result
        except Exception as exc:
            _restore_layers(stack, values)
            stack.active_index = index
            stack.error = str(exc)
            raise


def add_ring(material):
    """Insert a new top layer; an unset material passes Base through unchanged."""
    def edit(stack):
        if find_ring_mask(material) is None:
            raise ValueError("Load the existing Ring Mask shader group into this file first.")
        layer = stack.layers.add()
        layer.uid = uuid.uuid4().hex
        layer.name = "Ring {}".format(len(stack.layers))
        stack.layers.move(len(stack.layers) - 1, 0)
        stack.active_index = 0
        return stack.layers[0]
    return _mutate(material, edit)


def _index(stack, index):
    if index < 0 or index >= len(stack.layers):
        raise ValueError("Select a Ring layer first.")


def duplicate_ring(material, index):
    def edit(stack):
        _index(stack, index)
        values = {name: getattr(stack.layers[index], name) for name in _LAYER_FIELDS}
        layer = stack.layers.add()
        for name, value in values.items():
            setattr(layer, name, value)
        layer.uid = uuid.uuid4().hex
        layer.name = values["name"] + " Copy"
        stack.layers.move(len(stack.layers) - 1, index)
        stack.active_index = index
        return stack.layers[index]
    return _mutate(material, edit)


def delete_ring(material, index):
    def edit(stack):
        _index(stack, index)
        stack.layers.remove(index)
        stack.active_index = min(index, max(0, len(stack.layers) - 1))
        return stack
    return _mutate(material, edit)


def move_ring(material, index, direction):
    """Move one row: -1/UP toward the top; +1/DOWN toward the bottom."""
    delta = {"UP": -1, "DOWN": 1}.get(direction, direction)
    if delta not in (-1, 1):
        raise ValueError("Ring direction must be Up or Down.")
    def edit(stack):
        _index(stack, index)
        destination = max(0, min(len(stack.layers) - 1, index + delta))
        stack.layers.move(index, destination)
        stack.active_index = destination
        return stack
    return _mutate(material, edit)


def remove_stack(material):
    """Restore the original Surface connection and remove only managed data."""
    _check_target(material)
    stack = get_stack(material)
    with _editing(material):
        _output, surface, base, node = _managed_state(material)
        group = stack.generated_group
        if node is not None:
            # Establish the original connection before removing its wrapper.
            if base is not None:
                material.node_tree.links.new(base, surface)
            material.node_tree.nodes.remove(node)
        stack.generated_group = None
        stack.stack_id = ""
        stack.output_name = ""
        stack.base_node_name = ""
        stack.base_socket_identifier = ""
        stack.base_socket_index = -1
        stack.layers.clear()
        stack.active_index = 0
        stack.error = ""
        _remove_unused(group)
        material.node_tree.update_tag()
        return stack


_CLASSES = (RR_RingLayer, RR_RingStack)


def register():
    for cls in _CLASSES:
        if not getattr(cls, "is_registered", False):
            bpy.utils.register_class(cls)
    if not hasattr(bpy.types.Material, "rr_ring_stack"):
        bpy.types.Material.rr_ring_stack = PointerProperty(type=RR_RingStack)


def unregister():
    if hasattr(bpy.types.Material, "rr_ring_stack"):
        del bpy.types.Material.rr_ring_stack
    for cls in reversed(_CLASSES):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
    _BUSY.clear()


__all__ = ["get_stack", "find_ring_mask", "validate_target", "add_ring", "duplicate_ring",
           "delete_ring", "move_ring", "rebuild_stack", "remove_stack", "register", "unregister"]
