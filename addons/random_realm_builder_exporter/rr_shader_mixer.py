"""Native shader groups with expandable mask/shader inputs.

This is an add-on runtime helper, not another Ring Mask library asset. Masks and
material shaders remain normal nodes outside the group. Only the generated Mix
Shader chain is owned here; the surrounding material graph is never rewired.
"""

import json

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty


_KIND = "rr_shader_mixer_version"
_PAIRS = "rr_shader_mixer_pairs"
_BASE = "rr_shader_mixer_base"
_OUTPUT = "rr_shader_mixer_output"
_SIGNATURE = "rr_shader_mixer_signature"
_SIGNATURE_SCHEMA = "rr_shader_mixer_signature_schema"
_INTERFACE_ORDER = "rr_shader_mixer_interface_order"
_OWNER_TREES = {}
_SYNCING = False
_MENUS = set()


def _socket(sockets, identifier):
    return next((item for item in sockets if item.identifier == identifier), None)


def _pairs(group):
    try:
        pairs = json.loads(group.get(_PAIRS, "[]"))
        if not isinstance(pairs, list) or not pairs:
            return []
        size = 3 if group.get(_KIND) == 1 else 2
        if any(not isinstance(pair, list) or len(pair) != size
               or any(not isinstance(item, str) for item in pair) for pair in pairs):
            return []
        return pairs
    except (TypeError, ValueError, ReferenceError):
        return []


def is_mixer(node):
    try:
        return (node is not None and node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None
                and node.node_tree.bl_idname == "ShaderNodeTree"
                and node.node_tree.get(_KIND) in {1, 2})
    except ReferenceError:
        return False


def _legacy_mixer(node):
    return is_mixer(node) and node.node_tree.get(_KIND) == 1


def _socket_defaults(sockets):
    values = []
    for socket in sockets:
        if hasattr(socket, "default_value"):
            value = socket.default_value
            if hasattr(value, "__len__"):
                value = tuple(value)
            values.append((socket.identifier, value))
    return values


def _graph_signature(group):
    """Sign shader semantics, so arranging or relabelling nodes stays harmless."""
    nodes = list(group.nodes)
    indices = {node.as_pointer(): index for index, node in enumerate(nodes)}
    return json.dumps((
        [(node.bl_idname,
                node.operation if node.bl_idname == "ShaderNodeMath" else "",
                bool(node.use_clamp) if node.bl_idname == "ShaderNodeMath" else False,
                bool(node.mute),
                bool(node.is_active_output) if node.bl_idname == "NodeGroupOutput" else False,
                _socket_defaults(node.inputs), _socket_defaults(node.outputs))
               for node in nodes],
        sorted((indices[link.from_node.as_pointer()], link.from_socket.identifier,
                indices[link.to_node.as_pointer()], link.to_socket.identifier) for link in group.links),
    ), separators=(",", ":"))


def _matches_signature(group):
    if group.get(_SIGNATURE_SCHEMA) == 2:
        return _graph_signature(group) == group.get(_SIGNATURE)
    # Saved v1 groups used visual fields and names in their signature. Ignore
    # those visual fields when loading an old group, but retain its name-based
    # topology contract. New and rebuilt groups use semantic signatures above.
    if group.get(_KIND) != 1:
        return False
    try:
        stored_nodes, stored_links = json.loads(group.get(_SIGNATURE, "null"))
        stored = sorted(tuple(item[:6]) for item in stored_nodes)
        current = sorted((node.name, node.bl_idname,
                          node.operation if node.bl_idname == "ShaderNodeMath" else "",
                          bool(node.use_clamp) if node.bl_idname == "ShaderNodeMath" else False,
                          bool(node.mute),
                          bool(node.is_active_output) if node.bl_idname == "NodeGroupOutput" else False)
                         for node in group.nodes)
        links = sorted((link.from_node.name, link.from_socket.identifier,
                        link.to_node.name, link.to_socket.identifier) for link in group.links)
        return current == stored and links == sorted(tuple(item) for item in stored_links)
    except (TypeError, ValueError):
        return False


def _editable_tree(tree):
    if (tree is None or tree.bl_idname != "ShaderNodeTree" or tree.library is not None
            or not tree.is_editable):
        raise ValueError("Choose an editable Shader node tree.")


def _validate_group(group):
    # A library-linked template is read-only, but can still be validated and
    # copied. Only the staged local copy and editable material are changed.
    if group is None or group.bl_idname != "ShaderNodeTree":
        raise ValueError("Choose a Shader node tree.")
    if group.get(_KIND) not in {1, 2}:
        raise ValueError("Select a Mix Shaders node added by RR Helper.")
    pairs = _pairs(group)
    if not pairs:
        raise ValueError("The Mix Shaders interface was changed.")
    expected = {group.get(_BASE): ("INPUT", "NodeSocketShader"),
                group.get(_OUTPUT): ("OUTPUT", "NodeSocketShader")}
    for pair in pairs:
        mask, shader = pair[:2]
        expected.update({mask: ("INPUT", "NodeSocketFloat"),
                         shader: ("INPUT", "NodeSocketShader")})
        if len(pair) == 3:
            expected[pair[2]] = ("INPUT", "NodeSocketFloat")
    actual = {item.identifier: (item.in_out, item.socket_type)
              for item in group.interface.items_tree if item.item_type == "SOCKET"}
    size = 3 if group.get(_KIND) == 1 else 2
    if actual != expected or len(expected) != 2 + size * len(pairs):
        raise ValueError("The Mix Shaders interface was changed.")
    order = [item.identifier for item in group.interface.items_tree if item.item_type == "SOCKET"]
    if json.dumps(order) != group.get(_INTERFACE_ORDER):
        raise ValueError("The Mix Shaders interface order was changed.")
    if not _matches_signature(group):
        raise ValueError("The Mix Shaders internals were edited. Keep that group, or add a new Mix Shaders node.")
    animation = group.animation_data
    if animation and (animation.action or animation.nla_tracks or animation.drivers):
        raise ValueError("An animated Mix Shaders group cannot be expanded safely.")
    return pairs


def _new_pair(group, number):
    mask = group.interface.new_socket(name="Mask {}".format(number), in_out="INPUT",
                                      socket_type="NodeSocketFloat")
    mask.default_value = 0.0
    mask.min_value = 0.0
    mask.max_value = 1.0
    mask.description = "Mask for Shader {}; earlier slots cover later slots".format(number)
    shader = group.interface.new_socket(name="Shader {}".format(number), in_out="INPUT",
                                        socket_type="NodeSocketShader")
    shader.description = "Connect a shader and its Mask {}; Mask 0 leaves lower shaders unchanged".format(number)
    if group.get(_KIND) == 2:
        return [mask.identifier, shader.identifier]
    gate = group.interface.new_socket(name="_Connected {}".format(number), in_out="INPUT",
                                      socket_type="NodeSocketFloat")
    gate.default_value = 0.0
    gate.min_value = 0.0
    gate.max_value = 1.0
    gate.hide_value = True
    gate.description = "Managed connection state: an empty Shader input passes the Base through"
    return [mask.identifier, shader.identifier, gate.identifier]


def _build_chain(group):
    """Only called on a new/staged owned group, never a user's material tree."""
    group.nodes.clear()
    input_node = group.nodes.new("NodeGroupInput")
    input_node.name = "Inputs"
    input_node.location = (-620, 60)
    output_node = group.nodes.new("NodeGroupOutput")
    output_node.name = "Output"
    output_node.is_active_output = True
    previous = _socket(input_node.outputs, group[_BASE])
    pairs = _pairs(group)
    for position, index in enumerate(reversed(range(len(pairs)))):
        pair = pairs[index]
        mask, shader = pair[:2]
        factor = group.nodes.new("ShaderNodeMath")
        factor.name = ("Mask Gate {}" if len(pair) == 3 else "Clamp Mask {}").format(index + 1)
        factor.operation = "MULTIPLY"
        factor.use_clamp = True
        factor.location = (-380 + position * 240, -240)
        group.links.new(_socket(input_node.outputs, mask), factor.inputs[0])
        if len(pair) == 3:
            group.links.new(_socket(input_node.outputs, pair[2]), factor.inputs[1])
        else:
            factor.inputs[1].default_value = 1.0
        mix = group.nodes.new("ShaderNodeMixShader")
        mix.name = "Mix {}".format(index + 1)
        mix.location = (-300 + position * 240, 60)
        group.links.new(factor.outputs[0], mix.inputs[0])
        group.links.new(previous, mix.inputs[1])
        group.links.new(_socket(input_node.outputs, shader), mix.inputs[2])
        previous = mix.outputs[0]
    output_node.location = (-60 + len(pairs) * 240, 60)
    group.links.new(previous, _socket(output_node.inputs, group[_OUTPUT]))
    group[_SIGNATURE] = _graph_signature(group)
    group[_SIGNATURE_SCHEMA] = 2
    group[_INTERFACE_ORDER] = json.dumps([item.identifier for item in group.interface.items_tree
                                        if item.item_type == "SOCKET"])


def _new_group():
    group = bpy.data.node_groups.new("Mix Shaders", "ShaderNodeTree")
    try:
        group.color_tag = "SHADER"
        group.description = "Mix Mask / Shader pairs over a Base. Earlier slots have priority. Unused masks stay at 0. Add Shader Slot expands this node."
        group[_KIND] = 2
        base = group.interface.new_socket(name="Base Shader", in_out="INPUT",
                                          socket_type="NodeSocketShader")
        output = group.interface.new_socket(name="Shader", in_out="OUTPUT",
                                            socket_type="NodeSocketShader")
        group[_BASE] = base.identifier
        group[_OUTPUT] = output.identifier
        group[_PAIRS] = json.dumps([_new_pair(group, 1), _new_pair(group, 2)])
        _build_chain(group)
        return group
    except Exception:
        bpy.data.node_groups.remove(group)
        raise


def _remember_owner(tree):
    key = tree.as_pointer()
    if any(_legacy_mixer(node) for node in tree.nodes):
        _OWNER_TREES[key] = tree
    else:
        _OWNER_TREES.pop(key, None)


def sync_tree(tree):
    """Update only per-instance hidden gates; linked black shaders stay enabled."""
    changes = 0
    for node in tree.nodes:
        if not _legacy_mixer(node):
            continue
        for _mask, shader_id, gate_id in _pairs(node.node_tree):
            shader = _socket(node.inputs, shader_id)
            gate = _socket(node.inputs, gate_id)
            if shader is None or gate is None or gate.type != "VALUE" or shader.type != "SHADER":
                continue
            # hide=True keeps implementation sockets out of the normal node UI.
            # It does not affect the saved native shader computation.
            if not gate.hide:
                gate.hide = True
            if not gate.hide_value:
                gate.hide_value = True
            enabled = 1.0 if any(link.is_valid for link in shader.links) else 0.0
            if not gate.is_linked and gate.default_value != enabled:
                gate.default_value = enabled
                changes += 1
    return changes


def sync_mixers(trees=None):
    global _SYNCING
    if _SYNCING:
        return 0
    _SYNCING = True
    changes = 0
    try:
        selected = list(_OWNER_TREES.values()) if trees is None else trees
        for tree in selected:
            try:
                if tree.is_editable:
                    changes += sync_tree(tree)
            except ReferenceError:
                # Undo can remove the old owner datablock before undo_post.
                for key, cached in list(_OWNER_TREES.items()):
                    if cached is tree:
                        _OWNER_TREES.pop(key, None)
        return changes
    finally:
        _SYNCING = False


def add_mix_shaders(tree, *, location=None):
    """Insert a disconnected native group into the current editable shader tree."""
    _editable_tree(tree)
    group = _new_group()
    node = None
    try:
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        node.name = "Mix Shaders"
        node.label = "Mix Shaders"
        node.width = 220
        node.location = location if location is not None else tree.view_center
        sync_mixers([tree])
        _remember_owner(tree)
        return node
    except Exception:
        if node is not None:
            tree.nodes.remove(node)
        if group.users == 0:
            bpy.data.node_groups.remove(group)
        raise


def _external_state(node):
    values = {}
    for socket in node.inputs:
        if hasattr(socket, "default_value"):
            value = socket.default_value
            values[socket.identifier] = tuple(value) if hasattr(value, "__len__") else value
    links = []
    for link in node.id_data.links:
        if link.from_node == node or link.to_node == node:
            links.append((link.from_node, link.from_socket.identifier,
                          link.to_node, link.to_socket.identifier))
    return values, links


def _restore_external_state(node, state):
    values, links = state
    for identifier, value in values.items():
        socket = _socket(node.inputs, identifier)
        if socket is not None and hasattr(socket, "default_value"):
            socket.default_value = value
    existing = {(link.from_node.as_pointer(), link.from_socket.identifier,
                 link.to_node.as_pointer(), link.to_socket.identifier) for link in node.id_data.links}
    for from_node, from_id, to_node, to_id in links:
        key = (from_node.as_pointer(), from_id, to_node.as_pointer(), to_id)
        if key not in existing:
            node.id_data.links.new(_socket(from_node.outputs, from_id), _socket(to_node.inputs, to_id))


def _discardable_helper(group):
    """Only remove an unreferenced local helper, never a reusable template."""
    return (group.users == 0 and group.library is None and group.override_library is None
            and group.is_editable and group.asset_data is None and not group.use_fake_user)


def add_shader_slot(node):
    """Stage the expanded graph, then swap only this node's group reference.

    Interface identifiers survive NodeTree.copy(), so Blender preserves existing
    input values and external links. Shared duplicated nodes keep their old group;
    an unused old helper is removed only after the new reference is established.
    """
    if not is_mixer(node):
        raise ValueError("Select a Mix Shaders node added by RR Helper.")
    owner = node.id_data
    _editable_tree(owner)
    old_group = node.node_tree
    pairs = _validate_group(old_group)
    if old_group.get(_KIND) == 1 and any(_socket(node.inputs, pair[2]).is_linked for pair in pairs):
        raise ValueError("The managed connection inputs were rewired. Keep that node, or add a new Mix Shaders node.")
    state = _external_state(node)
    candidate = old_group.copy()
    try:
        _editable_tree(candidate)
        # Library assets are reusable templates. Expanded per-node helpers must
        # not become new assets or survive solely through an inherited fake user.
        candidate.asset_clear()
        candidate.use_fake_user = False
        candidate[_PAIRS] = json.dumps(pairs + [_new_pair(candidate, len(pairs) + 1)])
        _build_chain(candidate)
        _validate_group(candidate)
        node.node_tree = candidate
        _restore_external_state(node, state)
        sync_mixers([owner])
        _remember_owner(owner)
    except Exception:
        if node.node_tree == candidate:
            node.node_tree = old_group
            _restore_external_state(node, state)
        if candidate.users == 0:
            bpy.data.node_groups.remove(candidate)
        raise
    if _discardable_helper(old_group):
        name = old_group.name
        bpy.data.node_groups.remove(old_group)
        candidate.name = name
    return len(pairs) + 1


def remove_shader_slot(node):
    """Remove the last pair from this instance; keep its upstream shader nodes."""
    if not is_mixer(node):
        raise ValueError("Select a Mix Shaders node added by RR Helper.")
    owner = node.id_data
    _editable_tree(owner)
    old_group = node.node_tree
    pairs = _validate_group(old_group)
    if len(pairs) <= 1:
        raise ValueError("Keep at least one Mask / Shader pair and the Base Shader.")
    animation = owner.animation_data
    if animation and (animation.action or animation.nla_tracks or animation.drivers):
        raise ValueError("An animated shader tree cannot remove shader inputs safely.")
    if old_group.get(_KIND) == 1 and any(_socket(node.inputs, pair[2]).is_linked for pair in pairs):
        raise ValueError("The managed connection inputs were rewired. Keep that node, or add a new Mix Shaders node.")
    state = _external_state(node)
    removed_ids = set(pairs[-1])
    retained_state = (
        {identifier: value for identifier, value in state[0].items() if identifier not in removed_ids},
        [link for link in state[1] if not (link[2] == node and link[3] in removed_ids)],
    )
    candidate = old_group.copy()
    try:
        _editable_tree(candidate)
        candidate.asset_clear()
        candidate.use_fake_user = False
        for item in tuple(candidate.interface.items_tree):
            if item.item_type == "SOCKET" and item.identifier in removed_ids:
                candidate.interface.remove(item)
        candidate[_PAIRS] = json.dumps(pairs[:-1])
        _build_chain(candidate)
        _validate_group(candidate)
        node.node_tree = candidate
        _restore_external_state(node, retained_state)
        sync_mixers([owner])
        _remember_owner(owner)
    except Exception:
        if node.node_tree == candidate:
            node.node_tree = old_group
            _restore_external_state(node, state)
        if candidate.users == 0:
            bpy.data.node_groups.remove(candidate)
        raise
    if _discardable_helper(old_group):
        name = old_group.name
        bpy.data.node_groups.remove(old_group)
        candidate.name = name
    return len(pairs) - 1


def _native_mix_links(node, replacement, links):
    inputs = {
        node.inputs[0].identifier: replacement.inputs["Mask 1"].identifier,
        node.inputs[1].identifier: replacement.inputs["Base Shader"].identifier,
        node.inputs[2].identifier: replacement.inputs["Shader 1"].identifier,
    }
    output = replacement.outputs["Shader"].identifier
    return [(replacement if source == node else source,
             output if source == node else source_id,
             replacement if target == node else target,
             inputs[target_id] if target == node else target_id)
            for source, source_id, target, target_id in links]


def _connect_native_mix_links(owner, links):
    """Small staging boundary: partial link creation is rolled back by caller."""
    for source, source_id, target, target_id in links:
        owner.links.new(_socket(source.outputs, source_id), _socket(target.inputs, target_id))


def expand_native_mix_shader(node):
    """Replace an ordinary Mix Shader with two native Mask / Shader slots.

    Its first Shader becomes Base, Factor becomes Mask 1, and its second Shader
    becomes Shader 1. The second pair starts unused at Mask 0. Stage and verify
    a separate node before deleting the old one; a failed stage restores all of
    the ordinary Mix Shader's original external wires.
    """
    if node is None or node.bl_idname != "ShaderNodeMixShader":
        raise ValueError("Select an ordinary Mix Shader node.")
    owner = node.id_data
    _editable_tree(owner)
    animation = owner.animation_data
    if animation and (animation.action or animation.nla_tracks or animation.drivers):
        raise ValueError("An animated shader tree cannot convert Mix Shader safely. Add a new Mix Shaders node instead.")
    state = _external_state(node)
    name = node.name
    replacement = None
    group = None
    try:
        replacement = add_mix_shaders(owner, location=node.location)
        group = replacement.node_tree
        replacement.parent = node.parent
        replacement.location = node.location
        replacement.label = node.label
        replacement.width = max(220, node.width)
        replacement.hide = node.hide
        replacement.mute = node.mute
        replacement.use_custom_color = node.use_custom_color
        replacement.color = node.color
        replacement.inputs["Mask 1"].default_value = node.inputs[0].default_value
        mapped = _native_mix_links(node, replacement, state[1])
        _connect_native_mix_links(owner, mapped)
        actual = {(link.from_node.as_pointer(), link.from_socket.identifier,
                   link.to_node.as_pointer(), link.to_socket.identifier) for link in owner.links}
        if any((source.as_pointer(), source_id, target.as_pointer(), target_id) not in actual
               for source, source_id, target, target_id in mapped):
            raise RuntimeError("Mix Shader conversion did not preserve its connections.")
    except Exception:
        if replacement is not None:
            owner.nodes.remove(replacement)
        _restore_external_state(node, state)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
        raise
    # This is the commit point. Only this ordinary node is removed; its upstream
    # shaders, downstream nodes and every unrelated material connection remain.
    owner.nodes.remove(node)
    replacement.name = name
    for item in owner.nodes:
        item.select = item == replacement
    owner.nodes.active = replacement
    return replacement


def _candidate_trees():
    # Full discovery happens only at registration, load and undo/redo boundaries.
    for datablocks in (bpy.data.materials, bpy.data.worlds, bpy.data.lights):
        for datablock in datablocks:
            tree = getattr(datablock, "node_tree", None)
            if tree is not None:
                yield tree
    yield from (tree for tree in bpy.data.node_groups if tree.bl_idname == "ShaderNodeTree")


def rebuild_index():
    _OWNER_TREES.clear()
    for tree in _candidate_trees():
        if any(_legacy_mixer(node) for node in tree.nodes):
            _remember_owner(tree)
    sync_mixers()


@persistent
def _on_graph_update(_scene, depsgraph):
    if _SYNCING:
        return
    dirty = {}
    for update in depsgraph.updates:
        item = update.id
        item = getattr(item, "original", None) or item
        tree = item if isinstance(item, bpy.types.ShaderNodeTree) else getattr(item, "node_tree", None)
        if tree is None or tree.bl_idname != "ShaderNodeTree":
            continue
        key = tree.as_pointer()
        if key in _OWNER_TREES or any(_legacy_mixer(node) for node in tree.nodes):
            _remember_owner(tree)
            dirty[key] = tree
    if dirty:
        sync_mixers(list(dirty.values()))


@persistent
def _on_reload(_unused):
    rebuild_index()


@persistent
def _before_save_or_render(_unused):
    sync_mixers()


def _editor_tree(context):
    space = getattr(context, "space_data", None)
    if (getattr(space, "type", "") != "NODE_EDITOR"
            or getattr(space, "tree_type", "") != "ShaderNodeTree"):
        return None
    tree = getattr(space, "edit_tree", None)
    return tree if tree is not None and tree.is_editable and tree.library is None else None


class RR_OT_add_mix_shaders(bpy.types.Operator):
    bl_idname = "rr_builder.add_mix_shaders"
    bl_label = "Add Mix Shaders"
    bl_description = "Add a native shader group with two Mask / Shader pairs over a Base; keep unused masks at 0"
    bl_options = {"REGISTER", "UNDO"}

    use_transform: BoolProperty(default=True, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _editor_tree(context) is not None

    def execute(self, context):
        tree = _editor_tree(context)
        try:
            node = add_mix_shaders(tree, location=context.space_data.cursor_location)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        for item in tree.nodes:
            item.select = item == node
        tree.nodes.active = node
        return {"FINISHED"}

    def invoke(self, context, event):
        space = context.space_data
        if context.region.type == "WINDOW":
            space.cursor_location_from_region(event.mouse_region_x, event.mouse_region_y)
        else:
            space.cursor_location = space.edit_tree.view_center
        result = self.execute(context)
        if self.use_transform and "FINISHED" in result:
            bpy.ops.node.translate_attach_remove_on_cancel("INVOKE_DEFAULT")
        return result


class RR_OT_add_shader_slot(bpy.types.Operator):
    bl_idname = "rr_builder.add_shader_slot"
    bl_label = "Add Shader Slot"
    bl_description = "Add a Mask / Shader pair, or expand an ordinary Mix Shader while keeping its connections; earlier slots cover later slots"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        # Explicit node_name may target a different node than nodes.active.
        return _editor_tree(context) is not None

    def execute(self, context):
        try:
            tree = _editor_tree(context)
            node = tree.nodes.get(self.node_name) if self.node_name else tree.nodes.active
            if node is None:
                raise ValueError("The chosen Mix Shaders node is no longer available.")
            if node.bl_idname == "ShaderNodeMixShader":
                expand_native_mix_shader(node)
            else:
                add_shader_slot(node)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class RR_OT_remove_shader_slot(bpy.types.Operator):
    bl_idname = "rr_builder.remove_shader_slot"
    bl_label = "Remove Shader Slot"
    bl_description = "Remove the last Mask / Shader pair and its input links; upstream shader nodes stay. Undo restores the pair"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        # An inline button can target a named node rather than the active node.
        return _editor_tree(context) is not None

    def execute(self, context):
        try:
            tree = _editor_tree(context)
            node = tree.nodes.get(self.node_name) if self.node_name else tree.nodes.active
            if node is None:
                raise ValueError("The chosen Mix Shaders node is no longer available.")
            remove_shader_slot(node)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def draw_context_menu(self, context):
    tree = _editor_tree(context)
    node = tree.nodes.active if tree is not None else None
    if node is not None and (is_mixer(node) or node.bl_idname == "ShaderNodeMixShader"):
        self.layout.separator()
        self.layout.operator(RR_OT_add_shader_slot.bl_idname, icon="ADD")
        if is_mixer(node) and len(_pairs(node.node_tree)) > 1:
            self.layout.operator(RR_OT_remove_shader_slot.bl_idname, icon="REMOVE")


CLASSES = (RR_OT_add_mix_shaders, RR_OT_add_shader_slot, RR_OT_remove_shader_slot)
_HANDLERS = (("depsgraph_update_post", _on_graph_update),
             ("load_post", _on_reload), ("undo_post", _on_reload), ("redo_post", _on_reload),
             ("save_pre", _before_save_or_render), ("render_pre", _before_save_or_render))


def register():
    # addon_utils enables under RestrictedBlend: scene discovery belongs in the
    # add-on's existing deferred startup hook, not class registration.
    try:
        for cls in CLASSES:
            if not getattr(cls, "is_registered", False):
                bpy.utils.register_class(cls)
        for name, callback in (("NODE_MT_context_menu", draw_context_menu),):
            if name not in _MENUS:
                getattr(bpy.types, name).append(callback)
                _MENUS.add(name)
        for name, callback in _HANDLERS:
            handlers = getattr(bpy.app.handlers, name)
            if callback not in handlers:
                handlers.append(callback)
    except Exception:
        unregister()
        raise


def unregister():
    for name, callback in (("NODE_MT_context_menu", draw_context_menu),):
        if name in _MENUS:
            getattr(bpy.types, name).remove(callback)
            _MENUS.discard(name)
    for name, callback in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
    _OWNER_TREES.clear()
    for cls in reversed(CLASSES):
        if hasattr(cls, "bl_rna") and cls.is_registered:
            bpy.utils.unregister_class(cls)
