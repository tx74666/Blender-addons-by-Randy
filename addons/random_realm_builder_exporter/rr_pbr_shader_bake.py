"""Read and bake a bounded opaque shader closure without editing its source.

Mix Shader weights and native node-group instance inputs stay in Cycles. A
temporary material and independent nested groups replace only shader leaves
with channel emissions. Unsupported closures fail before any datablock copy.
"""

from contextlib import contextmanager
import math

import bpy

from . import rr_ring_nodes


_ROLES = {"BaseColor", "Roughness", "Metallic", "Emission", "Normal", "RingBase", "RingMask"}
_SHADERS = {"ShaderNodeBsdfPrincipled", "ShaderNodeEmission"}
_BASIC_PRINCIPLED_DEFAULTS = (
    (("Coat Weight", "Clearcoat"), 0.0),
    (("Subsurface Weight", "Subsurface"), 0.0),
    (("Sheen Weight", "Sheen"), 0.0),
    (("Anisotropic IOR Level", "Anisotropic"), 0.0),
    (("Weight",), 1.0),
    (("Specular IOR Level", "Specular"), 0.5),
    (("IOR",), 1.5),
)


def _key(value):
    return int(value.as_pointer())


def _socket(sockets, identifier):
    matches = [item for item in sockets if item.identifier == identifier]
    if len(matches) != 1:
        raise ValueError("Cannot resolve a unique shader-group socket: " + str(identifier))
    return matches[0]


def _input(node, *names):
    return next((node.inputs.get(name) for name in names if node.inputs.get(name) is not None), None)


def _active_output(tree, node_type):
    outputs = [node for node in tree.nodes if node.bl_idname == node_type
               and getattr(node, "is_active_output", False)]
    if node_type == "ShaderNodeOutputMaterial":
        cycles = [node for node in outputs if getattr(node, "target", "ALL") == "CYCLES"]
        all_targets = [node for node in outputs if getattr(node, "target", "ALL") == "ALL"]
        outputs = cycles or all_targets
    if len(outputs) != 1:
        raise ValueError("Expected one active " + node_type + ".")
    return outputs[0]


class _Context:
    def __init__(self, tree, parent=None, instance=None):
        self.tree, self.parent, self.instance = tree, parent, instance
        self.sources = {}
        for node in tree.nodes:
            for socket in node.inputs:
                links = list(socket.links)
                if len(links) > 1 or any(not link.is_valid for link in links):
                    raise ValueError("Invalid or ambiguous node link at " + node.name + ".")
                self.sources[_key(socket)] = links[0].from_socket if links else None
        self.children = {}
        self.converted = {}

    def source(self, socket):
        return self.sources.get(_key(socket)) if socket is not None else None

    def child(self, instance):
        child = self.children.get(_key(instance))
        if child is None:
            group = instance.node_tree
            if group is None or group.bl_idname != "ShaderNodeTree":
                raise ValueError("A shader group is missing its implementation.")
            ancestor = self
            while ancestor is not None:
                if ancestor.tree == group:
                    raise ValueError("Recursive shader groups cannot be baked.")
                ancestor = ancestor.parent
            child = _Context(group, self, instance)
            self.children[_key(instance)] = child
        return child


def _group_source(socket, context):
    child = context.child(socket.node)
    output = _active_output(child.tree, "NodeGroupOutput")
    incoming = _socket(output.inputs, socket.identifier)
    return child.source(incoming), child, incoming


def _parent_source(socket, context):
    if context.parent is None or context.instance is None:
        raise ValueError("A material cannot use an unbound Group Input.")
    incoming = _socket(context.instance.inputs, socket.identifier)
    return context.parent.source(incoming), context.parent, incoming


def _constant_input(socket, context, label):
    if socket is None:
        return None
    source = context.source(socket)
    return (_constant_output(source, context, label, set()) if source is not None
            else getattr(socket, "default_value", None))


def _constant_output(socket, context, label, visiting):
    token = (id(context), _key(socket))
    if token in visiting:
        raise ValueError(label + " contains a cycle.")
    visiting.add(token)
    try:
        node = socket.node
        if node.mute:
            raise ValueError(label + " uses a muted node; expose a constant value before baking.")
        if node.bl_idname in {"ShaderNodeValue", "ShaderNodeRGB"}:
            return socket.default_value
        if node.bl_idname == "NodeReroute":
            source = context.source(node.inputs[0])
            if source is not None:
                return _constant_output(source, context, label, visiting)
        elif node.bl_idname == "ShaderNodeGroup":
            source, child, incoming = _group_source(socket, context)
            if source is not None:
                return _constant_output(source, child, label, visiting)
            return getattr(incoming, "default_value", None)
        elif node.bl_idname == "NodeGroupInput":
            source, parent, incoming = _parent_source(socket, context)
            if source is not None:
                return _constant_output(source, parent, label, visiting)
            return getattr(incoming, "default_value", None)
        raise ValueError(label + " must resolve to a constant; linked procedural values need manual baking.")
    finally:
        visiting.remove(token)


def _finite_scalar(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(label + " must be a finite scalar.") from None
    if not math.isfinite(result):
        raise ValueError(label + " must be a finite scalar.")
    return result


def _finite_color(value, label):
    try:
        result = tuple(float(item) for item in value[:3])
    except (TypeError, ValueError, OverflowError):
        raise ValueError(label + " must be a finite RGB color.") from None
    if len(result) != 3 or any(not math.isfinite(item) or item < 0 for item in result):
        raise ValueError(label + " must be a finite nonnegative RGB color.")
    return result


def _emission_radiance(node, context):
    if node.bl_idname == "ShaderNodeEmission":
        color, strength = node.inputs.get("Color"), node.inputs.get("Strength")
    else:
        color = _input(node, "Emission Color", "Emission")
        strength = node.inputs.get("Emission Strength")
    if strength is None:
        return (0.0, 0.0, 0.0)
    strength_value = _finite_scalar(_constant_input(strength, context, "Emission Strength"), "Emission Strength")
    if strength_value < 0:
        raise ValueError("Emission Strength must be nonnegative.")
    if strength_value == 0:
        return (0.0, 0.0, 0.0)
    color_value = _finite_color(_constant_input(color, context, "Emission Color"), "Emission Color")
    result = tuple(item * strength_value for item in color_value)
    if any(not math.isfinite(item) for item in result):
        raise ValueError("Emission radiance exceeds the finite numeric range.")
    return result


def _ring_shader(socket):
    node = socket.node
    if not rr_ring_nodes.is_ring_group(node):
        return False
    group = node.node_tree
    return socket.identifier == group.get("rr_ring_group_shader_output")


def _data_depends_on(socket, context, target, target_context, visiting):
    if socket is None:
        return False
    if context is target_context and socket == target:
        return True
    token = (id(context), _key(socket))
    if token in visiting:
        raise ValueError("The Ring mask factor contains a cycle.")
    visiting.add(token)
    try:
        node = socket.node
        if node.bl_idname == "ShaderNodeGroup":
            source, child, _incoming = _group_source(socket, context)
            return _data_depends_on(source, child, target, target_context, visiting)
        if node.bl_idname == "NodeGroupInput":
            source, parent, _incoming = _parent_source(socket, context)
            return _data_depends_on(source, parent, target, target_context, visiting)
        return any(_data_depends_on(context.source(incoming), context, target, target_context, visiting)
                   for incoming in node.inputs if incoming.type != "SHADER")
    finally:
        visiting.remove(token)


def _ring_leaf_values(node, context):
    if node.bl_idname == "ShaderNodeEmission":
        color_socket, strength_socket = node.inputs.get("Color"), node.inputs.get("Strength")
        color = _finite_color(_constant_input(color_socket, context, "Ring Color"), "Ring Color")
        strength = _finite_scalar(_constant_input(strength_socket, context, "Ring Emission Strength"),
                                  "Ring Emission Strength")
        return color, strength if any(color) else 0.0
    color = _finite_color(_constant_input(node.inputs.get("Base Color"), context, "Ring Color"), "Ring Color")
    emission = _emission_radiance(node, context)
    strength = 0.0
    if any(emission):
        emission_color = _finite_color(_constant_input(_input(node, "Emission Color", "Emission"),
                                                      context, "Ring Emission Color"), "Ring Emission Color")
        if any(abs(a - b) > 1e-6 for a, b in zip(color, emission_color)):
            raise ValueError("Ring face and emission colors differ; choose one uniform Ring color before automatic baking.")
        strength = _finite_scalar(_constant_input(node.inputs.get("Emission Strength"), context,
                                                  "Ring Emission Strength"), "Ring Emission Strength")
    return color, strength


def _validate_basic_principled(node):
    # The final Unity material is one opaque PBR layer. These additional lobes
    # and Fresnel controls cannot be encoded in our fixed map/constant contract.
    for names, expected in _BASIC_PRINCIPLED_DEFAULTS:
        incoming = _input(node, *names)
        if incoming is None:
            continue
        if incoming.is_linked:
            raise ValueError(node.name + ": linked " + incoming.name +
                             " cannot be represented by the basic opaque PBR bake; use manual baking.")
        actual = _finite_scalar(incoming.default_value, node.name + ": " + incoming.name)
        if abs(actual - expected) > 1e-6:
            raise ValueError(node.name + ": " + incoming.name + " is " + str(actual) +
                             "; the basic opaque PBR bake requires " + str(expected) +
                             ". Keep this material and use manual baking.")


def _check_tree(tree, visiting, seen):
    """Copies include native data groups, so validate those dependencies too."""
    token = _key(tree)
    if token in visiting:
        raise ValueError("Recursive node groups cannot be copied for baking.")
    if token in seen:
        return
    animation = tree.animation_data
    if animation and (animation.action or animation.drivers or animation.nla_tracks):
        raise ValueError("Animated node trees need an explicit frame-specific manual bake.")
    visiting.add(token)
    for node in tree.nodes:
        if node.bl_idname in {"ShaderNodeScript", "ShaderNodeShaderToRGB"}:
            raise ValueError(node.bl_idname + " is unsupported by the Cycles PBR bake.")
        if node.bl_idname == "ShaderNodeGroup":
            if node.node_tree is None:
                raise ValueError("A node group is missing its implementation.")
            _check_tree(node.node_tree, visiting, seen)
    visiting.remove(token)
    seen.add(token)


def _inspect_shader(socket, context, state, visiting, mixes=(), in_ring=False):
    if socket is None:
        return  # An empty Mix input contributes a zero channel closure.
    token = (id(context), _key(socket))
    if token in visiting:
        raise ValueError("The Surface shader closure contains a cycle.")
    visiting.add(token)
    try:
        node = socket.node
        if node.mute:
            raise ValueError("Muted shader nodes need manual baking: " + node.name)
        kind = node.bl_idname
        if kind in _SHADERS:
            if kind == "ShaderNodeBsdfPrincipled":
                _validate_basic_principled(node)
                alpha = _finite_scalar(_constant_input(node.inputs.get("Alpha"), context, "Alpha"), "Alpha")
                transmission = _input(node, "Transmission Weight", "Transmission")
                amount = (_finite_scalar(_constant_input(transmission, context, "Transmission"), "Transmission")
                          if transmission is not None else 0.0)
                if abs(alpha - 1.0) > 1e-6 or abs(amount) > 1e-6:
                    raise ValueError("Glass and transparency need a separate material workflow.")
            radiance = _emission_radiance(node, context)
            if in_ring:
                state["ring_values"].append(_ring_leaf_values(node, context))
            else:
                state["emission_strength"] = max(state["emission_strength"], *radiance)
            state["shader_leaf_count"] += 1
        elif kind == "ShaderNodeMixShader":
            for incoming in node.inputs[1:3]:
                _inspect_shader(context.source(incoming), context, state, visiting,
                                mixes + ((context.source(node.inputs[0]), context),), in_ring)
        elif kind == "NodeReroute":
            _inspect_shader(context.source(node.inputs[0]), context, state, visiting, mixes, in_ring)
        elif kind == "ShaderNodeGroup":
            if _ring_shader(socket):
                rr_ring_nodes._validate_group(node.node_tree)
                mask = _socket(node.outputs, node.node_tree.get("rr_ring_group_mask_output"))
                if not any(_data_depends_on(factor, owner, mask, context, set()) for factor, owner in mixes):
                    raise ValueError("Ring Group Shader is not mixed by its own Mask; connect its Mask and Shader as one layer.")
                in_ring = True
                state["ring_group_count"] += 1
            source, child, _incoming = _group_source(socket, context)
            _inspect_shader(source, child, state, visiting, mixes, in_ring)
        elif kind == "NodeGroupInput":
            source, parent, _incoming = _parent_source(socket, context)
            _inspect_shader(source, parent, state, visiting, mixes, in_ring)
        else:
            raise ValueError("Unsupported Surface shader " + kind + "; use manual baking.")
    finally:
        visiting.remove(token)


def analyze_material(material):
    """Return serializable bake metadata; allocate or mutate no Blender data.

    Emission PNG values are linear radiance divided by emission_strength. That
    common nonnegative scale retains constant HDR values and explicitly zero
    emission without clipping; Unity restores it as a separately editable gain.
    """
    if not isinstance(material, bpy.types.Material) or material.node_tree is None or not material.use_nodes:
        raise ValueError("Choose a node material before PBR baking.")
    if not material.is_editable:
        raise ValueError(material.name + ": automatic Bake requires an editable source material.")
    tree = material.node_tree
    _check_tree(tree, set(), set())
    output = _active_output(tree, "ShaderNodeOutputMaterial")
    for name in ("Volume", "Displacement"):
        socket = output.inputs.get(name)
        if socket is not None and socket.is_linked:
            raise ValueError(name + " cannot be represented by this opaque PBR bake.")
    context = _Context(tree)
    source = context.source(output.inputs.get("Surface"))
    if source is None:
        raise ValueError("The active material output has no Surface shader.")
    state = {"contract_version": 1, "emission_strength": 0.0,
             "emission_color": [1.0, 1.0, 1.0, 1.0], "shader_leaf_count": 0,
             "ring_group_count": 0, "ring_values": []}
    _inspect_shader(source, context, state, set())
    state["has_emission"] = state["emission_strength"] > 0
    values = state.pop("ring_values")
    state["has_ring"] = bool(state["ring_group_count"])
    if state["has_ring"]:
        if not values:
            raise ValueError("Ring Group needs a connected constant-color shader before baking.")
        color, strength = values[0]
        if any(any(abs(a - b) > 1e-6 for a, b in zip(color, other_color))
               or abs(strength - other_strength) > 1e-6 for other_color, other_strength in values[1:]):
            raise ValueError("Multiple Ring branches have different colors or emission strengths; choose uniform Ring controls before baking.")
        state["ring_color"] = [*color, 1.0]
        state["ring_emission_strength"] = strength
    return state


def _copy_groups(tree, copies):
    # Copy per instance, rather than per shared group ID. Group Input values and
    # the channels connected at each instance can differ in the same material.
    for node in list(tree.nodes):
        if node.bl_idname != "ShaderNodeGroup":
            continue
        copied = node.node_tree.copy()
        copies.append(copied)
        copied.name = "RR Bake " + node.node_tree.name
        copied.use_fake_user = False
        if copied.asset_data is not None:
            copied.asset_clear()
        node.node_tree = copied
        _copy_groups(copied, copies)


def _replace_link(tree, incoming, source):
    for link in list(incoming.links):
        tree.links.remove(link)
    if source is not None:
        tree.links.new(source, incoming)


def _emit(context, color, source=None):
    node = context.tree.nodes.new("ShaderNodeEmission")
    node.name = "RR Bake Channel"
    node.inputs["Strength"].default_value = 1.0
    node.inputs["Color"].default_value = (*color[:3], 1.0)
    if source is not None:
        context.tree.links.new(source, node.inputs["Color"])
    return node.outputs["Emission"]


def _leaf_channel(socket, context, role, scale):
    node = socket.node
    if role == "RingMask":
        return _emit(context, (0, 0, 0))
    if role == "Emission":
        color = _emission_radiance(node, context)
        return _emit(context, tuple(item / scale for item in color) if scale > 0 else (0, 0, 0))
    if node.bl_idname == "ShaderNodeEmission":
        # A pure light closure contributes no base reflectance or metalness.
        return _emit(context, (0, 0, 0))
    name = {"BaseColor": "Base Color", "RingBase": "Base Color",
            "Roughness": "Roughness", "Metallic": "Metallic"}[role]
    incoming = node.inputs.get(name)
    source = context.source(incoming)
    if source is not None:
        return _emit(context, (0, 0, 0), source)
    value = getattr(incoming, "default_value", 0.0)
    color = ((float(value),) * 3 if isinstance(value, (int, float)) else tuple(value[:3]))
    if any(not math.isfinite(item) for item in color):
        raise ValueError(name + " contains a non-finite value.")
    return _emit(context, color)


def _convert_shader(socket, context, role, scale):
    if socket is None:
        return _emit(context, (0, 0, 0))
    token = _key(socket)
    if token in context.converted:
        return context.converted[token]
    node, kind = socket.node, socket.node.bl_idname
    if _ring_shader(socket) and role in {"RingMask", "RingBase", "Emission"}:
        result = _emit(context, (1, 1, 1) if role == "RingMask" else (0, 0, 0))
    elif kind in _SHADERS:
        result = _leaf_channel(socket, context, role, scale)
    elif kind == "ShaderNodeMixShader":
        mix = context.tree.nodes.new("ShaderNodeMixShader")
        mix.name = "RR Bake Channel Mix"
        factor_source = context.source(node.inputs[0])
        mix.inputs[0].default_value = node.inputs[0].default_value
        if factor_source is not None:
            context.tree.links.new(factor_source, mix.inputs[0])
        for index in (1, 2):
            channel = _convert_shader(context.source(node.inputs[index]), context, role, scale)
            context.tree.links.new(channel, mix.inputs[index])
        result = mix.outputs[0]
    elif kind == "NodeReroute":
        result = _convert_shader(context.source(node.inputs[0]), context, role, scale)
    elif kind == "ShaderNodeGroup":
        source, child, incoming = _group_source(socket, context)
        channel = _convert_shader(source, child, role, scale)
        _replace_link(child.tree, incoming, channel)
        result = socket
    elif kind == "NodeGroupInput":
        source, parent, incoming = _parent_source(socket, context)
        channel = _convert_shader(source, parent, role, scale)
        _replace_link(parent.tree, incoming, channel)
        result = socket
    else:
        raise ValueError("Unsupported shader reached after preflight: " + kind)
    context.converted[token] = result
    return result


@contextmanager
def cloned_materials(materials, role):
    """Yield original -> temporary material; dispose only this operation's IDs.

    The caller assigns clones only for native baking and restores its own object
    slots before leaving the context. Normal keeps the original shader closure.
    """
    key = role.get("key") if isinstance(role, dict) else str(role)
    if key not in _ROLES:
        raise ValueError("Unsupported PBR bake role: " + str(key))
    sources = list(dict.fromkeys(materials))
    plans = {material: analyze_material(material) for material in sources}
    cloned, groups = {}, []
    try:
        for source in sources:
            clone = source.copy()
            cloned[source] = clone
            clone.name = "RR Bake " + source.name
            clone.use_fake_user = False
            if clone.asset_data is not None:
                clone.asset_clear()
            _copy_groups(clone.node_tree, groups)
            if key != "Normal":
                context = _Context(clone.node_tree)
                output = _active_output(context.tree, "ShaderNodeOutputMaterial")
                surface = output.inputs["Surface"]
                result = _convert_shader(context.source(surface), context, key, plans[source]["emission_strength"])
                _replace_link(context.tree, surface, result)
        yield cloned
    finally:
        for clone in reversed(list(cloned.values())):
            bpy.data.materials.remove(clone, do_unlink=True)
        for group in groups:
            bpy.data.node_groups.remove(group, do_unlink=True)
