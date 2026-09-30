"""Reuse a material's Surface as native nodes or a reusable shader group.

This module deliberately uses the data API: importing never changes editor
selection, source nodes, material slots, or existing links in the target.
"""

import copy

import bpy


_OWNED_SETTINGS = {"color_ramp", "mapping", "texture_mapping", "color_mapping", "image_user"}
_NODE_SKIP = {
    "rna_type", "type", "inputs", "outputs", "internal_links", "parent",
    "name", "location", "location_absolute", "select", "dimensions",
}
_SHAPING_PROPERTIES = (
    "node_tree", "data_type", "mode", "operation", "blend_type", "factor_mode",
    "noise_dimensions", "voronoi_dimensions", "feature", "distribution",
)


def material_shader_group_name(material):
    return "Material " + material.name


def generated_shader_group(material):
    if material is None:
        return None
    group = bpy.data.node_groups.get(material_shader_group_name(material))
    return group if group is not None and group.bl_idname == "ShaderNodeTree" else None


def _set_shader_color_tag(group):
    # Use Blender's native node-group category, not a custom node/header color.
    # Older Blender versions may not expose color_tag; linked groups stay shared.
    if (hasattr(group, "color_tag") and group.is_editable and group.library is None
            and not group.is_property_readonly("color_tag") and group.color_tag != "SHADER"):
        group.color_tag = "SHADER"


def validate_reusable_shader_group(group):
    """Validate a saved shader implementation without requiring its source graph."""
    if group is None or group.bl_idname != "ShaderNodeTree":
        return "No reusable Shader Group found."
    sockets = [item for item in group.interface.items_tree
               if item.item_type == "SOCKET" and item.in_out == "OUTPUT"
               and item.name == "Shader" and item.socket_type == "NodeSocketShader"]
    if len(sockets) != 1:
        return "The existing group needs one output named Shader with the Shader socket type."
    outputs = [node for node in group.nodes
               if node.bl_idname == "NodeGroupOutput" and node.is_active_output]
    if len(outputs) != 1:
        return "The existing group has no active Shader output. Refresh it before use."
    socket = next((item for item in outputs[0].inputs
                   if item.identifier == sockets[0].identifier), None)
    if socket is None or not any(link.is_valid for link in socket.links):
        return "The existing group's Shader output is not linked. Refresh it before use."
    return ""


def _active_material_output(tree):
    active = [node for node in tree.nodes
              if node.bl_idname == "ShaderNodeOutputMaterial" and node.is_active_output]
    if len(active) == 1:
        return active[0]
    # If multiple active flags are present, prefer this render target. Never
    # replace an explicit active output with an inactive renderer-specific one.
    engine = getattr(getattr(getattr(bpy.context, "scene", None), "render", None), "engine", "")
    target = "CYCLES" if engine == "CYCLES" else "EEVEE" if "EEVEE" in engine else "ALL"
    preferred = [node for node in active if node.target == target]
    if len(preferred) == 1:
        return preferred[0]
    fallback = [node for node in active if node.target == "ALL"]
    return fallback[0] if len(fallback) == 1 else None


def surface_dependency_nodes(material):
    """Return the ordered dependency nodes and the socket feeding Surface.

    Ancestor frames are retained for layout, but unrelated nodes inside those
    frames are not dependencies and are never copied.
    """
    if material is None:
        raise ValueError("Select a source material.")
    if not material.use_nodes or material.node_tree is None:
        raise ValueError("No Surface shader found: material does not use nodes.")
    tree = material.node_tree
    output = _active_material_output(tree)
    if output is None:
        raise ValueError("No active Material Output found.")
    surface = output.inputs.get("Surface")
    if surface is None or not surface.is_linked:
        raise ValueError("No Surface shader found.")
    links = list(surface.links)
    if len(links) != 1 or not links[0].is_valid:
        raise ValueError("The Surface shader link is invalid.")
    found, visiting = set(), set()

    def visit(node):
        if node in visiting:
            raise ValueError("The Surface graph contains a cycle.")
        if node in found:
            return
        if node.bl_idname in {"ShaderNodeOutputMaterial", "NodeGroupInput", "NodeGroupOutput"}:
            raise ValueError("The Surface graph contains an unsupported output dependency.")
        visiting.add(node)
        for socket in node.inputs:
            for link in socket.links:
                if not link.is_valid:
                    raise ValueError("The Surface graph contains an invalid link.")
                visit(link.from_node)
        visiting.remove(node)
        found.add(node)

    visit(links[0].from_node)
    for node in tuple(found):
        parent = node.parent
        while parent is not None:
            found.add(parent)
            parent = parent.parent
    return [node for node in tree.nodes if node in found], links[0].from_socket


def validate_material_surface(material):
    """Return a concise validation error, or an empty string for a usable graph."""
    try:
        surface_dependency_nodes(material)
    except (ValueError, ReferenceError) as exc:
        return str(exc)
    return ""


def _is_simple_surface(nodes, surface_socket):
    # Frames and reroutes organize the graph; they do not add shader work.
    functional = [node for node in nodes if node.bl_idname not in {"NodeFrame", "NodeReroute"}]

    def is_texture(node):
        return node.bl_idname.startswith("ShaderNodeTex") and node.bl_idname != "ShaderNodeTexCoord"

    def is_shader(node):
        return (node.bl_idname.startswith("ShaderNode")
                and node.bl_idname not in {"ShaderNodeGroup", "ShaderNodeScript"}
                and any(socket.type == "SHADER" for socket in node.outputs))

    if len(functional) == 1:
        return is_texture(functional[0]) or is_shader(functional[0])
    return (len(functional) == 2 and surface_socket.type == "SHADER"
            and sum(is_texture(node) for node in functional) == 1
            and sum(is_shader(node) for node in functional) == 1)


def is_simple_material_surface(material):
    """A native shader/texture, optionally a texture feeding one shader."""
    try:
        return _is_simple_surface(*surface_dependency_nodes(material))
    except (ValueError, ReferenceError):
        return False


def _editor_material(space):
    if getattr(space, "type", None) != "NODE_EDITOR" or getattr(space, "tree_type", "") != "ShaderNodeTree":
        return None
    if getattr(space, "shader_type", "OBJECT") != "OBJECT":
        return None
    material = getattr(space, "id", None)
    if not isinstance(material, bpy.types.Material) or material.node_tree is None:
        return None
    if getattr(space, "edit_tree", None) != material.node_tree:
        return None
    return material


def get_current_shader_material(context):
    """Respect a pinned editor and refuse ambiguous or nested editor targets."""
    area = getattr(context, "area", None)
    if area is not None and area.type == "NODE_EDITOR":
        return _editor_material(getattr(context, "space_data", None))
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None)
    if screen is None:
        return None
    materials = set()
    for candidate in screen.areas:
        if candidate.type != "NODE_EDITOR":
            continue
        space = candidate.spaces.active
        if getattr(space, "tree_type", "") != "ShaderNodeTree":
            continue
        material = _editor_material(space)
        # A nested material editor is not permission to insert at its root, and
        # must not be silently ignored in favor of a different editor's target.
        if material is None and isinstance(getattr(space, "id", None), bpy.types.Material):
            return None
        if material is not None:
            materials.add(material)
    return next(iter(materials)) if len(materials) == 1 else None


def _copy_custom_properties(source, target):
    try:
        keys = source.keys()
    except TypeError:
        # Built-in sockets expose bpy_struct.keys() but do not support ID props.
        return
    for key in keys:
        value = source[key]
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        elif hasattr(value, "to_list"):
            value = value.to_list()
        elif not isinstance(value, bpy.types.ID):
            value = copy.deepcopy(value)
        target[key] = value
        try:
            target.id_properties_ui(key).update(**source.id_properties_ui(key).as_dict())
        except (TypeError, KeyError):
            # Non-scalar custom properties have no editable UI metadata.
            pass


def _set_property(source, target, prop):
    value = getattr(source, prop.identifier)
    if getattr(prop, "is_array", False):
        value = tuple(value)
    try:
        setattr(target, prop.identifier, value)
    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError("Cannot preserve {}.{}: {}".format(
            source.bl_rna.identifier, prop.identifier, exc)) from exc


def _copy_color_ramp(source, target):
    _copy_rna_settings(source, target, skip={"elements"})
    while len(target.elements) > 1:
        target.elements.remove(target.elements[-1])
    for index, element in enumerate(source.elements):
        result = target.elements[0] if index == 0 else target.elements.new(element.position)
        result.position = element.position
        result.color = element.color


def _copy_curve_mapping(source, target):
    target.initialize()
    _copy_rna_settings(source, target, skip={"curves"})
    if len(source.curves) != len(target.curves):
        raise ValueError("Cannot preserve the curve mapping's channel count.")
    for source_curve, target_curve in zip(source.curves, target.curves):
        while len(target_curve.points) > 2:
            target_curve.points.remove(target_curve.points[-2])
        points = list(source_curve.points)
        if len(points) < 2:
            raise ValueError("Cannot preserve a curve mapping with fewer than two points.")
        # Set the endpoints before inserting middle points; Blender sorts points
        # by X during update, so keep explicit references while assigning them.
        destinations = [target_curve.points[0], target_curve.points[-1]]
        for index, point in enumerate(points):
            destination = destinations[0] if index == 0 else destinations[1] if index == len(points) - 1 else target_curve.points.new(*point.location)
            _copy_rna_settings(point, destination)
    target.update()


def _copy_rna_settings(source, target, skip=()):
    """Copy writable values and shared IDs; deep-copy known node-owned settings."""
    skip = set(skip) | {"rna_type", "id_data"}
    properties = list(source.bl_rna.properties)
    properties.sort(key=lambda prop: (
        _SHAPING_PROPERTIES.index(prop.identifier)
        if prop.identifier in _SHAPING_PROPERTIES else len(_SHAPING_PROPERTIES)))
    for prop in properties:
        name = prop.identifier
        if name in skip or name.startswith("bl_"):
            continue
        if prop.type == "POINTER":
            value = getattr(source, name)
            if name in _OWNED_SETTINGS and value is not None:
                destination = getattr(target, name)
                if isinstance(value, bpy.types.ColorRamp):
                    _copy_color_ramp(value, destination)
                elif isinstance(value, bpy.types.CurveMapping):
                    _copy_curve_mapping(value, destination)
                else:
                    _copy_rna_settings(value, destination)
            elif not prop.is_readonly and (value is None or isinstance(value, bpy.types.ID)):
                _set_property(source, target, prop)
            elif value is not None and not prop.is_readonly:
                raise ValueError("Cannot preserve node setting '{}'.".format(name))
        elif prop.type == "COLLECTION":
            collection = getattr(source, name)
            if name == "panel_states":
                destinations = getattr(target, name)
                for index, panel in enumerate(collection):
                    if index < len(destinations):
                        _copy_rna_settings(panel, destinations[index])
            elif len(collection):
                raise ValueError("Cannot preserve node collection '{}'.".format(name))
        elif not prop.is_readonly:
            _set_property(source, target, prop)


def _matching_socket(source_socket, source_sockets, target_sockets):
    index = next(index for index, socket in enumerate(source_sockets) if socket == source_socket)
    # Identifiers distinguish sockets with identical display names (Math, Mix).
    matches = [socket for socket in target_sockets if socket.identifier == source_socket.identifier]
    if len(matches) == 1:
        return matches[0]
    if index < len(target_sockets):
        return target_sockets[index]
    raise ValueError("Cannot preserve socket '{}' on '{}'.".format(source_socket.name, source_socket.node.name))


def _copy_socket_values(source_node, target_node):
    for source_sockets, target_sockets in ((source_node.inputs, target_node.inputs), (source_node.outputs, target_node.outputs)):
        for source_socket in source_sockets:
            destination = _matching_socket(source_socket, source_sockets, target_sockets)
            # These are instance values. Socket type/identifier/link collections
            # belong to Blender and must not be recreated by display name.
            for name in ("default_value", "hide", "hide_value"):
                prop = source_socket.bl_rna.properties.get(name)
                if prop is not None and not prop.is_readonly and hasattr(destination, name):
                    _set_property(source_socket, destination, prop)
            _copy_custom_properties(source_socket, destination)


def _copy_node_graph(source_nodes, source_links, destination):
    """Copy only these nodes/links. Nested groups and image IDs stay shared."""
    nodes = list(source_nodes)
    node_map = {}
    for source in nodes:
        if source.bl_idname == "ShaderNodeScript":
            raise ValueError("OSL Script nodes cannot be imported safely by this tool.")
        result = destination.nodes.new(source.bl_idname)
        node_map[source] = result
        _copy_rna_settings(source, result, skip=_NODE_SKIP)
        result.name = source.name
        _copy_custom_properties(source, result)
    for source, result in node_map.items():
        if source.parent in node_map:
            result.parent = node_map[source.parent]
        result.location = source.location
        result.select = source.select
    for link in source_links:
        if link.from_node not in node_map or link.to_node not in node_map:
            continue
        output = _matching_socket(link.from_socket, link.from_node.outputs, node_map[link.from_node].outputs)
        input_socket = _matching_socket(link.to_socket, link.to_node.inputs, node_map[link.to_node].inputs)
        result = destination.links.new(output, input_socket)
        if hasattr(link, "is_muted") and not result.is_property_readonly("is_muted"):
            result.is_muted = link.is_muted
    # Reroute socket types become known only after links have been restored.
    for source, result in node_map.items():
        _copy_socket_values(source, result)
    if nodes:
        source_active = nodes[0].id_data.nodes.active
        if source_active in node_map:
            destination.nodes.active = node_map[source_active]
    return node_map


def _has_animation(tree):
    animation = tree.animation_data
    return animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks))


def _depends_on_group(tree, sought, visited=None):
    if tree == sought:
        return True
    visited = set() if visited is None else visited
    if tree in visited:
        return False
    visited.add(tree)
    return any(node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None
               and _depends_on_group(node.node_tree, sought, visited) for node in tree.nodes)


def _copy_surface_graph(source, destination):
    source_nodes, surface_socket = surface_dependency_nodes(source)
    if _has_animation(source.node_tree):
        raise ValueError("Animated material nodes are not supported; the source was left unchanged.")
    node_map = _copy_node_graph(source_nodes, source.node_tree.links, destination)
    output = destination.nodes.new("NodeGroupOutput")
    output.name = "Shader Output"
    output.is_active_output = True
    source_output = surface_socket.node
    source_node = node_map[source_output]
    output.location = (max((node.location.x + node.width for node in destination.nodes if node != output), default=0) + 100,
                       source_node.location.y)
    socket = _matching_socket(surface_socket, source_output.outputs, source_node.outputs)
    destination.links.new(socket, output.inputs["Shader"])
    return destination


def _ensure_refreshable(group):
    if group.library is not None or not group.is_editable:
        raise ValueError("The existing Shader Group is read-only.")
    if _has_animation(group):
        raise ValueError("The existing Shader Group is animated and cannot be refreshed safely.")
    sockets = [item for item in group.interface.items_tree if item.item_type == "SOCKET"]
    if len(sockets) != 1 or sockets[0].in_out != "OUTPUT" or sockets[0].name != "Shader" or sockets[0].socket_type != "NodeSocketShader":
        raise ValueError("The existing group must have one Shader output and no inputs before refreshing.")


def _new_staging_group():
    group = bpy.data.node_groups.new(".RR Material Shader staging", "ShaderNodeTree")
    group.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    _set_shader_color_tag(group)
    return group


def _build_shader_group(source, existing=None):
    nodes, _socket = surface_dependency_nodes(source)
    expected_name = material_shader_group_name(source)
    collision = bpy.data.node_groups.get(expected_name)
    if existing is None and collision is not None:
        raise ValueError("A different node group already uses '{}'.".format(expected_name))
    if existing is not None:
        _ensure_refreshable(existing)
        if any(node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None
               and _depends_on_group(node.node_tree, existing) for node in nodes):
            raise ValueError("Refresh would create a recursive Shader Group dependency.")
    stage = _new_staging_group()
    backup = None
    restore_check = None
    try:
        _copy_surface_graph(source, stage)
        if existing is None:
            stage.name = expected_name
            if stage.name != expected_name:
                raise ValueError("The generated group name is too long or already in use. Shorten the material name.")
            stage.use_fake_user = True
            result, stage = stage, None
            return result
        # Blender's ID copy captures the entire old graph for rollback. Its
        # interface is never assigned back: preserve the existing socket IDs.
        backup = existing.copy()
        # A user may have edited the generated group since its last import.
        # Prove that its graph can be restored before removing any old nodes.
        restore_check = _new_staging_group()
        _copy_node_graph(backup.nodes, backup.links, restore_check)
        try:
            existing.nodes.clear()
            _copy_node_graph(stage.nodes, stage.links, existing)
            _set_shader_color_tag(existing)
        except Exception:
            existing.nodes.clear()
            _copy_node_graph(backup.nodes, backup.links, existing)
            raise
        existing.update_tag()
        return existing
    finally:
        if stage is not None:
            bpy.data.node_groups.remove(stage)
        if backup is not None:
            bpy.data.node_groups.remove(backup)
        if restore_check is not None:
            bpy.data.node_groups.remove(restore_check)


def refresh_material_shader_group(source, group=None):
    if source is None:
        raise ValueError("Select a source material.")
    group = generated_shader_group(source) if group is None else group
    if group is None:
        raise ValueError("No existing Shader Group to refresh.")
    return _build_shader_group(source, existing=group)


def _insertion_location(tree, location):
    if location is not None:
        return location
    nodes = list(tree.nodes)
    if not nodes:
        return (0, 0)
    # Keep existing layout untouched and leave a visible gap to its right.
    return (max(getattr(node, "location_absolute", node.location).x + node.width for node in nodes) + 100,
            max(getattr(node, "location_absolute", node.location).y for node in nodes))


def _insert_surface_nodes(source, target, location):
    """Insert an independent native-node copy, rolling back partial copies."""
    if source == target:
        raise ValueError("Source is the target; choose another material to import these nodes into.")
    if _has_animation(source.node_tree):
        raise ValueError("Animated material nodes are not supported; the source was left unchanged.")
    source_nodes, surface_socket = surface_dependency_nodes(source)
    tree = target.node_tree
    previous_nodes = set(tree.nodes)
    previous_active = tree.nodes.active
    try:
        position = _insertion_location(tree, location)
        node_map = _copy_node_graph(source_nodes, source.node_tree.links, tree)
        roots = [node for node in source_nodes if node.parent not in node_map]
        left = min(node.location.x for node in roots)
        top = max(node.location.y for node in roots)
        for original, node in node_map.items():
            if original in roots:
                node.location = (original.location.x + position[0] - left,
                                 original.location.y + position[1] - top)
            node.select = False
        inserted = node_map[surface_socket.node]
    except Exception:
        for node in list(tree.nodes):
            if node not in previous_nodes:
                tree.nodes.remove(node)
        raise
    finally:
        tree.nodes.active = previous_active
    return None, inserted, "DIRECT"


def import_material_shader(source, target=None, *, refresh=False, location=None):
    """Insert simple Surface nodes directly; generate/reuse groups for complex ones.

    Return ``(group_or_None, inserted_node_or_None, status)``. DIRECT means native
    nodes were copied. Passing no target or refresh=True explicitly requests the
    group workflow. Simple self-import is rejected to leave the source untouched.
    """
    if source is None:
        raise ValueError("Select a source material.")
    direct = target is not None and not refresh and is_simple_material_surface(source)
    group = generated_shader_group(source)
    # Reuse deliberately depends on the saved implementation. The source may
    # currently be under construction, disconnected, or have nodes disabled.
    error = (validate_material_surface(source) if direct or group is None or refresh
             else validate_reusable_shader_group(group))
    if error:
        raise ValueError(error)
    if target is not None and target != source:
        if not isinstance(target, bpy.types.Material) or not target.use_nodes or target.node_tree is None:
            raise ValueError("Open a material in the Shader Editor first.")
        if target.library is not None or not target.is_editable or not target.node_tree.is_editable:
            raise ValueError("The target material is read-only.")
    if direct:
        return _insert_surface_nodes(source, target, location)
    created = group is None
    if created:
        group = _build_shader_group(source)
        status = "CREATED"
    elif refresh:
        group = refresh_material_shader_group(source, group)
        status = "REFRESHED"
    else:
        status = "REUSED"
    if target is None or target == source:
        _set_shader_color_tag(group)
        return group, None, status
    tree = target.node_tree
    node = None
    try:
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        node.location = _insertion_location(tree, location)
        node.label = ""
        node.use_custom_color = False
        node.select = False
        _set_shader_color_tag(group)
    except Exception:
        if node is not None:
            tree.nodes.remove(node)
        if created:
            bpy.data.node_groups.remove(group)
        raise
    return group, node, status


__all__ = [
    "material_shader_group_name", "generated_shader_group", "surface_dependency_nodes",
    "validate_material_surface", "validate_reusable_shader_group",
    "is_simple_material_surface",
    "get_current_shader_material", "import_material_shader",
    "refresh_material_shader_group",
]
