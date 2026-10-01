"""Native shader groups with expandable mask/shader inputs.

This is an add-on runtime helper, not another Ring Mask library asset. Masks and
material shaders remain normal nodes outside the group. Only the generated Mix
Shader chain is owned here; the surrounding material graph is never rewired.
"""

import json

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty


_KIND = "rr_shader_mixer_version"
_PAIRS = "rr_shader_mixer_pairs"
_BASE = "rr_shader_mixer_base"
_OUTPUT = "rr_shader_mixer_output"
_SIGNATURE = "rr_shader_mixer_signature"
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
        if any(not isinstance(pair, list) or len(pair) != 3
               or any(not isinstance(item, str) for item in pair) for pair in pairs):
            return []
        return pairs
    except (TypeError, ValueError, ReferenceError):
        return []


def is_mixer(node):
    try:
        return (node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None
                and node.node_tree.bl_idname == "ShaderNodeTree"
                and node.node_tree.get(_KIND) == 1)
    except ReferenceError:
        return False


def _graph_signature(group):
    return json.dumps((
        sorted((node.name, node.bl_idname,
                node.operation if node.bl_idname == "ShaderNodeMath" else "",
                bool(node.use_clamp) if node.bl_idname == "ShaderNodeMath" else False,
                bool(node.mute),
                bool(node.is_active_output) if node.bl_idname == "NodeGroupOutput" else False,
                tuple(node.location), node.label, node.width, bool(node.hide),
                bool(node.use_custom_color), tuple(node.color))
               for node in group.nodes),
        sorted((link.from_node.name, link.from_socket.identifier,
                link.to_node.name, link.to_socket.identifier) for link in group.links),
    ), separators=(",", ":"))


def _editable_tree(tree):
    if (tree is None or tree.bl_idname != "ShaderNodeTree" or tree.library is not None
            or not tree.is_editable):
        raise ValueError("Choose an editable Shader node tree.")


def _validate_group(group):
    _editable_tree(group)
    if group.get(_KIND) != 1:
        raise ValueError("Select a Mix Shaders node added by RR Helper.")
    pairs = _pairs(group)
    if not pairs:
        raise ValueError("The Mix Shaders interface was changed.")
    expected = {group.get(_BASE): ("INPUT", "NodeSocketShader"),
                group.get(_OUTPUT): ("OUTPUT", "NodeSocketShader")}
    for mask, shader, gate in pairs:
        expected.update({mask: ("INPUT", "NodeSocketFloat"),
                         shader: ("INPUT", "NodeSocketShader"),
                         gate: ("INPUT", "NodeSocketFloat")})
    actual = {item.identifier: (item.in_out, item.socket_type)
              for item in group.interface.items_tree if item.item_type == "SOCKET"}
    if actual != expected or len(expected) != 2 + 3 * len(pairs):
        raise ValueError("The Mix Shaders interface was changed.")
    order = [item.identifier for item in group.interface.items_tree if item.item_type == "SOCKET"]
    if json.dumps(order) != group.get(_INTERFACE_ORDER):
        raise ValueError("The Mix Shaders interface order was changed.")
    if _graph_signature(group) != group.get(_SIGNATURE):
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
    shader.description = "Shader to mix over the Base using Mask {}".format(number)
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
        mask, shader, gate = pairs[index]
        factor = group.nodes.new("ShaderNodeMath")
        factor.name = "Mask Gate {}".format(index + 1)
        factor.operation = "MULTIPLY"
        factor.use_clamp = True
        factor.location = (-380 + position * 240, -240)
        group.links.new(_socket(input_node.outputs, mask), factor.inputs[0])
        group.links.new(_socket(input_node.outputs, gate), factor.inputs[1])
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
    group[_INTERFACE_ORDER] = json.dumps([item.identifier for item in group.interface.items_tree
                                        if item.item_type == "SOCKET"])


def _new_group():
    group = bpy.data.node_groups.new("Mix Shaders", "ShaderNodeTree")
    try:
        group.color_tag = "SHADER"
        group.description = "Mix masked shaders over a Base. Earlier slots have priority. Add Shader Slot expands this node."
        group[_KIND] = 1
        base = group.interface.new_socket(name="Base Shader", in_out="INPUT",
                                          socket_type="NodeSocketShader")
        output = group.interface.new_socket(name="Shader", in_out="OUTPUT",
                                            socket_type="NodeSocketShader")
        group[_BASE] = base.identifier
        group[_OUTPUT] = output.identifier
        group[_PAIRS] = json.dumps([_new_pair(group, 1)])
        _build_chain(group)
        return group
    except Exception:
        bpy.data.node_groups.remove(group)
        raise


def _remember_owner(tree):
    _OWNER_TREES[tree.as_pointer()] = tree


def sync_tree(tree):
    """Update only per-instance hidden gates; linked black shaders stay enabled."""
    changes = 0
    for node in tree.nodes:
        if not is_mixer(node):
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
    if any(_socket(node.inputs, gate).is_linked for _mask, _shader, gate in pairs):
        raise ValueError("The managed connection inputs were rewired. Keep that node, or add a new Mix Shaders node.")
    state = _external_state(node)
    candidate = old_group.copy()
    try:
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
    if old_group.users == 0:
        name = old_group.name
        bpy.data.node_groups.remove(old_group)
        candidate.name = name
    return len(pairs) + 1


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
        if any(is_mixer(node) for node in tree.nodes):
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
        if key in _OWNER_TREES or any(is_mixer(node) for node in tree.nodes):
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
    bl_description = "Add a shader group with a Base and expandable Mask / Shader slots"
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
    bl_description = "Add a Mask / Shader input pair to the selected Mix Shaders node; earlier slots cover later slots"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        tree = _editor_tree(context)
        return tree is not None and tree.nodes.active is not None and is_mixer(tree.nodes.active)

    def execute(self, context):
        try:
            add_shader_slot(_editor_tree(context).nodes.active)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def draw_context_menu(self, context):
    if RR_OT_add_shader_slot.poll(context):
        self.layout.separator()
        self.layout.operator(RR_OT_add_shader_slot.bl_idname, icon="ADD")


CLASSES = (RR_OT_add_mix_shaders, RR_OT_add_shader_slot)
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
