"""Native mask unions sharing one shader, with optional edit-time helpers.

Ring Group does not generate a shader for each ring. It unions any number of
ordinary scalar masks and passes its single Shader input through unchanged.
Saved nodes evaluate without this add-on; no update, save or render handler is
registered here. Ring and Arc helpers reuse the user's existing mask assets.
"""

import json
import math
from pathlib import Path

import bpy
from bpy.props import StringProperty


_KIND = "rr_ring_group_version"
_MASKS = "rr_ring_group_masks"
_SHADER = "rr_ring_group_shader"
_MASK_OUTPUT = "rr_ring_group_mask_output"
_SHADER_OUTPUT = "rr_ring_group_shader_output"
_SIGNATURE = "rr_ring_group_signature"
_ORDER = "rr_ring_group_interface_order"
_NOTICE = "rr_ring_group_notice"
_MENU_REGISTERED = False
_ASSETS = {
    # One current Ring Mask supports complete rings and adjustable arcs.
    "RING": ("Ring Mask", "Randy_Ring_Mask.blend", "tools/randy_node_assets/build_ring_mask_current.py"),
    "ARC": ("Ring Mask", "Randy_Ring_Mask.blend", "tools/randy_node_assets/build_ring_mask_current.py"),
}
_RADIAL_INPUTS = ("Inner Radius", "Ring Width", "Edge Softness")


def _socket(sockets, identifier):
    return next((item for item in sockets if item.identifier == identifier), None)


def _mask_ids(group):
    try:
        value = json.loads(group.get(_MASKS, "[]"))
        if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            return value
    except (TypeError, ValueError, ReferenceError):
        pass
    return []


def is_ring_group(node):
    try:
        return (node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None
                and node.node_tree.bl_idname == "ShaderNodeTree"
                and node.node_tree.get(_KIND) == 1)
    except (AttributeError, ReferenceError):
        return False


def _shader_tree(tree, *, editable=True):
    if tree is None or tree.bl_idname != "ShaderNodeTree":
        raise ValueError("Choose a Shader node tree.")
    if editable and (tree.library is not None or not tree.is_editable):
        raise ValueError("Choose an editable Shader node tree.")


def _graph_signature(group):
    # Presentation does not change the union. Moving, hiding or labelling an
    # internal node must not prevent the edit-time helper from expanding it.
    return json.dumps((
        sorted((node.name, node.bl_idname, bool(node.mute),
                node.operation if node.bl_idname == "ShaderNodeMath" else "",
                bool(node.use_clamp) if node.bl_idname == "ShaderNodeMath" else False,
                tuple(socket.default_value for socket in node.inputs)
                if node.bl_idname == "ShaderNodeMath" else (),
                bool(node.is_active_output) if node.bl_idname == "NodeGroupOutput" else False)
               for node in group.nodes),
        sorted((link.from_node.name, link.from_socket.identifier,
                link.to_node.name, link.to_socket.identifier) for link in group.links),
    ), separators=(",", ":"))


def _validate_group(group):
    # A linked asset may be read and copied; its source is never edited.
    _shader_tree(group, editable=False)
    masks = _mask_ids(group)
    if group.get(_KIND) != 1 or not masks or len(set(masks)) != len(masks):
        raise ValueError("Select a Ring Group node.")
    expected = {group.get(_SHADER): ("INPUT", "NodeSocketShader"),
                group.get(_MASK_OUTPUT): ("OUTPUT", "NodeSocketFloat"),
                group.get(_SHADER_OUTPUT): ("OUTPUT", "NodeSocketShader")}
    expected.update({identifier: ("INPUT", "NodeSocketFloat") for identifier in masks})
    sockets = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    actual = {item.identifier: (item.in_out, item.socket_type) for item in sockets}
    if actual != expected or len(expected) != len(masks) + 3:
        raise ValueError("The Ring Group interface was changed. Keep it and add a fresh Ring Group.")
    if json.dumps([item.identifier for item in sockets]) != group.get(_ORDER):
        raise ValueError("The Ring Group interface order was changed.")
    if _graph_signature(group) != group.get(_SIGNATURE):
        raise ValueError("The Ring Group calculation was edited. Keep it and add a fresh Ring Group.")
    animation = group.animation_data
    if animation and (animation.action or animation.nla_tracks or animation.drivers):
        raise ValueError("An animated Ring Group cannot be rebuilt safely.")
    return masks


def _new_mask(group, number):
    mask = group.interface.new_socket(name="Mask {}".format(number), in_out="INPUT",
                                      socket_type="NodeSocketFloat")
    mask.default_value = 0.0
    mask.min_value = 0.0
    mask.max_value = 1.0
    mask.description = "Ring or Arc coverage; masks in this group share one Shader"
    return mask.identifier


def _build_union(group):
    """Build only a new or staged helper group, never the surrounding material."""
    group.nodes.clear()
    inputs = group.nodes.new("NodeGroupInput")
    inputs.name = "Inputs"
    inputs.location = (-620, 100)
    output = group.nodes.new("NodeGroupOutput")
    output.name = "Output"
    output.is_active_output = True
    previous = None
    for index, identifier in enumerate(_mask_ids(group)):
        union = group.nodes.new("ShaderNodeMath")
        union.name = "Union {}".format(index + 1)
        union.operation = "MAXIMUM"
        union.use_clamp = True
        union.inputs[1].default_value = 0.0
        union.location = (-360 + index * 220, -100)
        group.links.new(_socket(inputs.outputs, identifier), union.inputs[0])
        if previous is not None:
            group.links.new(previous, union.inputs[1])
        previous = union.outputs[0]
    output.location = (-100 + len(_mask_ids(group)) * 220, 100)
    group.links.new(previous, _socket(output.inputs, group[_MASK_OUTPUT]))
    group.links.new(_socket(inputs.outputs, group[_SHADER]),
                    _socket(output.inputs, group[_SHADER_OUTPUT]))
    group[_SIGNATURE] = _graph_signature(group)
    group[_ORDER] = json.dumps([item.identifier for item in group.interface.items_tree
                               if item.item_type == "SOCKET"])


def new_group(mask_count=2):
    """Create a native Ring Group with one shader and an expandable mask union."""
    if not isinstance(mask_count, int) or isinstance(mask_count, bool) or mask_count < 1:
        raise ValueError("A Ring Group needs at least one Mask input.")
    group = bpy.data.node_groups.new("Ring Group", "ShaderNodeTree")
    try:
        group.color_tag = "SHADER"
        group.default_group_node_width = 260
        group.description = "Combine Ring and Arc masks using Maximum, sharing one Shader. No add-on is needed to evaluate this group."
        group[_KIND] = 1
        shader = group.interface.new_socket(name="Shader", in_out="INPUT", socket_type="NodeSocketShader")
        shader.description = "The single material shader shared by all masks"
        mask_out = group.interface.new_socket(name="Mask", in_out="OUTPUT", socket_type="NodeSocketFloat")
        mask_out.description = "Union of all masks, clamped to 0-1"
        shader_out = group.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
        shader_out.description = "The same Shader input, to pair with this group's Mask in Mix Shaders"
        group[_SHADER] = shader.identifier
        group[_MASK_OUTPUT] = mask_out.identifier
        group[_SHADER_OUTPUT] = shader_out.identifier
        group[_MASKS] = json.dumps([_new_mask(group, index + 1) for index in range(mask_count)])
        _build_union(group)
        return group
    except Exception:
        bpy.data.node_groups.remove(group)
        raise


def _external_state(node):
    values = {socket.identifier: socket.default_value for socket in node.inputs
              if hasattr(socket, "default_value")}
    links = [(link.from_node, link.from_socket.identifier, link.to_node, link.to_socket.identifier)
             for link in node.id_data.links if link.from_node == node or link.to_node == node]
    return values, links


def _restore_external_state(node, state):
    values, links = state
    for identifier, value in values.items():
        socket = _socket(node.inputs, identifier)
        if socket is not None and hasattr(socket, "default_value"):
            socket.default_value = value
    existing = {(link.from_node.as_pointer(), link.from_socket.identifier,
                 link.to_node.as_pointer(), link.to_socket.identifier) for link in node.id_data.links}
    for source, source_id, target, target_id in links:
        key = (source.as_pointer(), source_id, target.as_pointer(), target_id)
        if key not in existing:
            node.id_data.links.new(_socket(source.outputs, source_id), _socket(target.inputs, target_id))


def _expanded_group(group, masks):
    candidate = group.copy()
    try:
        candidate.asset_clear()
        candidate.use_fake_user = False
        candidate[_MASKS] = json.dumps(masks + [_new_mask(candidate, len(masks) + 1)])
        _build_union(candidate)
        _validate_group(candidate)
        return candidate
    except Exception:
        if candidate.users == 0:
            bpy.data.node_groups.remove(candidate)
        raise


def _finish_swap(old_group, candidate):
    # Only the superseded local helper belongs to this edit. Linked sources,
    # library overrides and reusable asset templates must remain untouched even
    # when this was their final current-file node reference.
    if (old_group.library is None and old_group.override_library is None
            and old_group.is_editable and old_group.asset_data is None
            and not old_group.use_fake_user and old_group.users == 0):
        old_name = old_group.name
        bpy.data.node_groups.remove(old_group)
        candidate.name = old_name


def add_mask_slot(node):
    """Append one Mask input to this instance, preserving shader and links."""
    if not is_ring_group(node):
        raise ValueError("Select a Ring Group node.")
    _shader_tree(node.id_data)
    old_group = node.node_tree
    masks = _validate_group(old_group)
    state = _external_state(node)
    candidate = _expanded_group(old_group, masks)
    try:
        node.node_tree = candidate
        _restore_external_state(node, state)
    except Exception:
        if node.node_tree == candidate:
            node.node_tree = old_group
            _restore_external_state(node, state)
        if candidate.users == 0:
            bpy.data.node_groups.remove(candidate)
        raise
    _finish_swap(old_group, candidate)
    return len(masks) + 1


def remove_mask_slot(node):
    """Remove the last Mask input from this instance, keeping upstream nodes."""
    if not is_ring_group(node):
        raise ValueError("Select a Ring Group node.")
    owner = node.id_data
    _shader_tree(owner)
    old_group = node.node_tree
    masks = _validate_group(old_group)
    if len(masks) <= 1:
        raise ValueError("Keep at least one Mask input and the shared Shader.")
    animation = owner.animation_data
    if animation and (animation.action or animation.nla_tracks or animation.drivers):
        # Socket animation paths are index-based. Do not shift or remove them
        # implicitly, even when another node owns the apparent animation.
        raise ValueError("An animated shader tree cannot remove Mask inputs safely.")
    state = _external_state(node)
    removed_id = masks[-1]
    retained_state = (
        {identifier: value for identifier, value in state[0].items() if identifier != removed_id},
        [link for link in state[1] if not (link[2] == node and link[3] == removed_id)],
    )
    candidate = old_group.copy()
    try:
        _shader_tree(candidate)
        candidate.asset_clear()
        candidate.use_fake_user = False
        for item in tuple(candidate.interface.items_tree):
            if item.item_type == "SOCKET" and item.identifier == removed_id:
                candidate.interface.remove(item)
        candidate[_MASKS] = json.dumps(masks[:-1])
        _build_union(candidate)
        _validate_group(candidate)
        node.node_tree = candidate
        _restore_external_state(node, retained_state)
    except Exception:
        if node.node_tree == candidate:
            node.node_tree = old_group
            _restore_external_state(node, state)
        if candidate.users == 0:
            bpy.data.node_groups.remove(candidate)
        raise
    _finish_swap(old_group, candidate)
    return len(masks) - 1


def _compatible_mask(group, kind):
    name, _filename, source = _ASSETS[kind]
    if group is None or group.bl_idname != "ShaderNodeTree":
        return False
    # Explicit legacy Arc arguments remain usable by the conservative radial
    # migration helper. New Add Ring / Add Arc lookups use _current_mask below.
    if (group.name not in {name, "Arc Mask"}
            and group.get("randy_asset_source") not in {
                source, "tools/randy_node_assets/build_arc_mask.py"}):
        return False
    sockets = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    inputs = {item.name: item.socket_type for item in sockets if item.in_out == "INPUT"}
    outputs = {item.name: item.socket_type for item in sockets if item.in_out == "OUTPUT"}
    if any(inputs.get(item) != "NodeSocketFloat" for item in _RADIAL_INPUTS):
        return False
    if outputs.get("Mask") != "NodeSocketFloat":
        return False
    if any(inputs.get(item) != "NodeSocketFloat" for item in ("Start Angle", "Sweep Angle")):
        return False
    return any(node.bl_idname == "NodeGroupOutput" and node.is_active_output
               and node.inputs.get("Mask") is not None and node.inputs["Mask"].is_linked
               for node in group.nodes)


def _current_mask(group, kind):
    """Require the current reusable boundary asset when adding new nodes."""
    if not _compatible_mask(group, kind):
        return False
    if (group.get("randy_asset_source") != _ASSETS[kind][2]
            or group.get("randy_asset_version") != "0.2.1"):
        return False
    return any(item.item_type == "SOCKET" and item.in_out == "OUTPUT"
               and item.name == "Ring Data" and item.socket_type == "NodeSocketBundle"
               for item in group.interface.items_tree)


def _asset_paths(kind):
    """Only known asset filenames under explicitly configured library roots."""
    _name, filename, _source = _ASSETS[kind]
    seen = set()
    for library in bpy.context.preferences.filepaths.asset_libraries:
        root = Path(bpy.path.abspath(library.path)).resolve()
        for relative in (filename, "assets/" + filename):
            candidate = (root / relative).resolve()
            if candidate not in seen and candidate.is_relative_to(root) and candidate.is_file():
                seen.add(candidate)
                yield candidate


def find_mask_group(kind):
    """Reuse current Ring Mask 0.2.1, or append its configured native asset.

    Older three-control Ring Mask and Arc Mask copies remain untouched. They
    must not hide the current boundary-capable asset just by sharing its name.
    """
    if kind not in _ASSETS:
        raise ValueError("Choose Ring or Arc.")
    name, _filename, _source = _ASSETS[kind]
    exact = bpy.data.node_groups.get(name)
    if _current_mask(exact, kind):
        return exact
    for group in bpy.data.node_groups:
        if _current_mask(group, kind):
            return group
    for path in _asset_paths(kind):
        with bpy.data.libraries.load(str(path), link=False) as (available, requested):
            if name not in available.node_groups:
                continue
            requested.node_groups = [name]
        group = requested.node_groups[0]
        if _current_mask(group, kind):
            return group
        if group is not None:
            group.use_fake_user = False
            if group.users == 0:
                bpy.data.node_groups.remove(group)
    raise ValueError("{} is not available. Add its existing asset to this file or configure its asset library.".format(name))


def _depends_on(group, target, seen=None):
    if group == target:
        return True
    seen = set() if seen is None else seen
    pointer = group.as_pointer()
    if pointer in seen:
        return False
    seen.add(pointer)
    return any(_depends_on(node.node_tree, target, seen) for node in group.nodes
               if node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None)


def _link_mask_node(owner, ring_group, identifier, mask_node):
    owner.links.new(mask_node.outputs["Mask"], _socket(ring_group.inputs, identifier))


def _constant_scalar(socket):
    if socket is None or socket.type != "VALUE" or socket.is_linked:
        return None
    try:
        value = float(socket.default_value)
        return value if math.isfinite(value) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _place_new_radius(ring_group, mask_node):
    """Space only the new mask when constant radial controls are known."""
    inner_value = _constant_scalar(mask_node.inputs.get("Inner Radius"))
    width_value = _constant_scalar(mask_node.inputs.get("Ring Width"))
    if inner_value is None or width_value is None:
        mask_node[_NOTICE] = "New ring controls are not finite constant values. Set its Radius and Width manually."
        return
    width = max(width_value, 0.0)
    if width == 0.0:
        mask_node[_NOTICE] = "Ring Width is zero. Increase it to make this ring visible."
        return
    occupied = []
    for identifier in _mask_ids(ring_group.node_tree):
        socket = _socket(ring_group.inputs, identifier)
        for link in socket.links:
            if not link.is_valid:
                continue
            source = link.from_node
            inner, source_width = source.inputs.get("Inner Radius"), source.inputs.get("Ring Width")
            start_value, source_width_value = _constant_scalar(inner), _constant_scalar(source_width)
            animation = source.id_data.animation_data
            animated = animation is not None and (animation.action or animation.nla_tracks or animation.drivers)
            if start_value is None or source_width_value is None or animated:
                mask_node[_NOTICE] = "Some mask controls are linked, animated or not finite scalar values. Set this new ring's radius manually."
                return
            start = min(max(start_value, 0.0), 1.0)
            end = min(start + max(source_width_value, 0.0), 1.0)
            if end > start:
                occupied.append((start, end))
    if not occupied:
        return
    gap, tolerance = .02, 1e-7
    candidate = max(end for _start, end in occupied) + gap
    if candidate + width > 1.0 + tolerance:
        candidate = 0.0
        for start, end in sorted(occupied):
            if candidate + width <= start - gap + tolerance:
                break
            candidate = max(candidate, end + gap)
        if candidate + width > 1.0 + tolerance:
            mask_node[_NOTICE] = "No free radius interval fits this ring's Width. Adjust its Radius or Width; existing rings are unchanged."
            return
    mask_node.inputs["Inner Radius"].default_value = candidate


def _add_mask_node(node, kind):
    if not is_ring_group(node):
        raise ValueError("Select a Ring Group node.")
    owner = node.id_data
    _shader_tree(owner)
    old_group = node.node_tree
    masks = _validate_group(old_group)
    mask_group = find_mask_group(kind)
    if _depends_on(mask_group, owner):
        raise ValueError("That mask would create a recursive node-group dependency.")
    # A zero-valued but animated input is not an unused slot. Appending one
    # avoids changing any action or driver on the owner shader tree.
    animated_owner = owner.animation_data is not None and (
        owner.animation_data.action or owner.animation_data.nla_tracks or owner.animation_data.drivers)
    free = next((identifier for identifier in masks
                 if not animated_owner and not _socket(node.inputs, identifier).is_linked
                 and _socket(node.inputs, identifier).default_value == 0.0), None)
    state = _external_state(node)
    candidate = _expanded_group(old_group, masks) if free is None else None
    mask_node = None
    try:
        if candidate is not None:
            node.node_tree = candidate
            _restore_external_state(node, state)
            free = _mask_ids(candidate)[-1]
        mask_node = owner.nodes.new("ShaderNodeGroup")
        mask_node.node_tree = mask_group
        mask_node.name = _ASSETS[kind][0]
        if kind == "RING":
            mask_node.inputs["Start Angle"].default_value = 0.0
            mask_node.inputs["Sweep Angle"].default_value = 360.0
        else:
            # The unified asset now defaults to a complete ring. Add Arc keeps
            # the shortcut's deliberate semicircle behavior on each new node.
            mask_node.inputs["Start Angle"].default_value = 0.0
            mask_node.inputs["Sweep Angle"].default_value = 180.0
        mask_node.width = mask_group.default_group_node_width
        number = _mask_ids(node.node_tree).index(free)
        mask_node.location = (node.location.x - 380, node.location.y - number * 180)
        _place_new_radius(node, mask_node)
        _link_mask_node(owner, node, free, mask_node)
    except Exception:
        if mask_node is not None:
            owner.nodes.remove(mask_node)
        if candidate is not None:
            node.node_tree = old_group
            _restore_external_state(node, state)
            if candidate.users == 0:
                bpy.data.node_groups.remove(candidate)
        raise
    if candidate is not None:
        _finish_swap(old_group, candidate)
    # Keep this group active so right-click Add Ring / Add Arc can be repeated.
    # The new mask remains selected and visible for parameter editing.
    node.select = True
    mask_node.select = True
    owner.nodes.active = node
    return mask_node


def add_ring(node):
    return _add_mask_node(node, "RING")


def add_arc(node):
    return _add_mask_node(node, "ARC")


def _radial_graph_signature(group):
    """Compare calculations while allowing different presentation and tags."""
    return (
        tuple((n.name, n.bl_idname, bool(n.mute), getattr(n, "operation", None),
               getattr(n, "data_type", None), getattr(n, "interpolation_type", None),
               getattr(n, "clamp", None), getattr(n, "use_clamp", None),
               getattr(n, "from_instancer", None), getattr(n, "is_active_output", None),
               tuple((s.name, tuple(s.default_value) if hasattr(s.default_value, "__len__")
                      and not isinstance(s.default_value, str) else s.default_value)
                     for s in n.inputs if hasattr(s, "default_value"))) for n in group.nodes),
        tuple(sorted((l.from_node.name, l.from_socket.identifier,
                      l.to_node.name, l.to_socket.identifier) for l in group.links)),
        tuple((s.name, s.in_out, s.socket_type, s.identifier) for s in group.interface.items_tree
              if s.item_type == "SOCKET"),
    )


def replace_legacy_ring_masks(tree, arc_group=None):
    """Explicit migration to full ring/arc nodes; never a render handler.

    Only unchanged radial graphs matching Arc's embedded dependency qualify.
    Customized or animated sources are preserved rather than silently replaced.
    """
    _shader_tree(tree)
    animation = tree.animation_data
    if animation and (animation.action or animation.nla_tracks or animation.drivers):
        raise ValueError("Keep animated Ring Mask nodes; migration requires an unanimated owner tree.")
    arc_group = find_mask_group("ARC") if arc_group is None else arc_group
    if not _compatible_mask(arc_group, "ARC") or _depends_on(arc_group, tree):
        raise ValueError("Choose a compatible Arc Mask without recursive dependencies.")
    radial = next((n.node_tree for n in arc_group.nodes if n.bl_idname == "ShaderNodeGroup"
                   and n.node_tree is not None
                   and n.node_tree.get("randy_asset_source") == "tools/randy_node_assets/build_ring_mask.py"), None)
    if radial is None:
        raise ValueError("Arc Mask has no validated legacy radial dependency.")
    expected = _radial_graph_signature(radial)
    candidates = [n for n in tree.nodes if n.bl_idname == "ShaderNodeGroup" and n.node_tree
                  and n.node_tree.get("randy_asset_source") == "tools/randy_node_assets/build_ring_mask.py"]
    for n in candidates:
        a = n.node_tree.animation_data
        if _radial_graph_signature(n.node_tree) != expected or (a and (a.action or a.nla_tracks or a.drivers)):
            raise ValueError("Keep customized or animated Ring Mask: " + n.name)
    plans = []
    for n in candidates:
        plans.append((n, n.node_tree, n.name, n.label,
                      {s.name: s.default_value for s in n.inputs},
                      [(l.from_node, l.from_socket.name, l.from_socket.identifier,
                        l.to_node, l.to_socket.name, l.to_socket.identifier)
                       for l in tree.links if l.from_node == n or l.to_node == n]))

    def restore_links(plan):
        n, _group, _name, _label, values, cables = plan
        for name, value in values.items():
            n.inputs[name].default_value = value
        # Only migrated endpoints change group identifiers. Other nodes can
        # have repeated socket names (Math's two Value inputs are common), so
        # retain their exact identifiers instead of reconnecting to the first.
        for l in list(tree.links):
            if l.from_node == n or l.to_node == n:
                tree.links.remove(l)
        for source, output_name, output_id, target, input_name, input_id in cables:
            output = source.outputs.get(output_name) if source in candidates else _socket(source.outputs, output_id)
            input_socket = target.inputs.get(input_name) if target in candidates else _socket(target.inputs, input_id)
            if output is None or input_socket is None:
                raise ValueError("A connected socket is no longer available during Ring Mask migration.")
            tree.links.new(output, input_socket)

    applied = []
    try:
        for plan in plans:
            n, _group, _name, label, _values, _links = plan
            applied.append(plan)
            n.node_tree = arc_group
            restore_links(plan)
            n.inputs["Start Angle"].default_value = 0.0
            n.inputs["Sweep Angle"].default_value = 360.0
            n.name = "Ring Mask" if _current_mask(arc_group, "RING") else "Arc Mask"
            if label == "Ring Mask":
                n.label = ""
    except Exception:
        for plan in reversed(applied):
            n, group, name, label, _values, _links = plan
            n.node_tree = group
            n.name, n.label = name, label
            restore_links(plan)
        raise
    return len(plans)


def _editor_tree(context):
    space = getattr(context, "space_data", None)
    if getattr(space, "type", "") != "NODE_EDITOR" or getattr(space, "tree_type", "") != "ShaderNodeTree":
        return None
    tree = getattr(space, "edit_tree", None)
    if tree is None or not tree.is_editable or tree.library is not None:
        return None
    return tree


def _active_group(context):
    tree = _editor_tree(context)
    if tree is None:
        return None
    node = tree.nodes.active
    return node if node is not None and is_ring_group(node) else None


def _operator_group(context, node_name):
    tree = _editor_tree(context)
    node = tree.nodes.get(node_name) if tree is not None and node_name else _active_group(context)
    if node is None or not is_ring_group(node):
        raise ValueError("The chosen Ring Group node is no longer available.")
    return node


class RR_OT_add_mask_slot(bpy.types.Operator):
    bl_idname = "rr_builder.add_mask_slot"
    bl_label = "Add Mask Slot"
    bl_description = "Add another Mask input to this Ring Group, keeping its one shared Shader"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        # Header controls explicitly name their node rather than depending on
        # whichever node Blender has made active at the next event boundary.
        return _editor_tree(context) is not None

    def execute(self, context):
        try:
            add_mask_slot(_operator_group(context, getattr(self, "node_name", "")))
        except (ValueError, RuntimeError, ReferenceError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class RR_OT_remove_mask_slot(bpy.types.Operator):
    bl_idname = "rr_builder.remove_mask_slot"
    bl_label = "Remove Mask Slot"
    bl_description = "Remove the last Mask input and its link; its upstream mask node stays. Undo restores the input"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _editor_tree(context) is not None

    def execute(self, context):
        try:
            remove_mask_slot(_operator_group(context, getattr(self, "node_name", "")))
        except (ValueError, RuntimeError, ReferenceError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class RR_OT_add_ring_mask(bpy.types.Operator):
    bl_idname = "rr_builder.add_ring_mask"
    bl_label = "Add Ring"
    bl_description = "Add Ring Mask with a full 360-degree sweep and connect it to this Ring Group"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_group(context) is not None

    def execute(self, context):
        try:
            added = add_ring(_active_group(context))
            if added.get(_NOTICE):
                self.report({"WARNING"}, added[_NOTICE])
        except (ValueError, RuntimeError, ReferenceError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class RR_OT_add_arc_mask(bpy.types.Operator):
    bl_idname = "rr_builder.add_arc_mask"
    bl_label = "Add Arc"
    bl_description = "Add Ring Mask with a 180-degree arc and connect it to this Ring Group"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_group(context) is not None

    def execute(self, context):
        try:
            added = add_arc(_active_group(context))
            if added.get(_NOTICE):
                self.report({"WARNING"}, added[_NOTICE])
        except (ValueError, RuntimeError, ReferenceError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def draw_context_menu(self, context):
    node = _active_group(context)
    if node is not None:
        self.layout.separator()
        self.layout.operator(RR_OT_add_ring_mask.bl_idname, icon="ADD")
        self.layout.operator(RR_OT_add_arc_mask.bl_idname, icon="ADD")
        self.layout.operator(RR_OT_add_mask_slot.bl_idname, icon="ADD")
        if len(_mask_ids(node.node_tree)) > 1:
            self.layout.operator(RR_OT_remove_mask_slot.bl_idname, icon="REMOVE")


CLASSES = (RR_OT_add_mask_slot, RR_OT_remove_mask_slot, RR_OT_add_ring_mask, RR_OT_add_arc_mask)


def register():
    global _MENU_REGISTERED
    try:
        for cls in CLASSES:
            if not getattr(cls, "is_registered", False):
                bpy.utils.register_class(cls)
        if not _MENU_REGISTERED:
            bpy.types.NODE_MT_context_menu.append(draw_context_menu)
            _MENU_REGISTERED = True
    except Exception:
        unregister()
        raise


def unregister():
    global _MENU_REGISTERED
    if _MENU_REGISTERED:
        bpy.types.NODE_MT_context_menu.remove(draw_context_menu)
        _MENU_REGISTERED = False
    for cls in reversed(CLASSES):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
