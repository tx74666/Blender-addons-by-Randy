"""Transactional Unity UV export contract for RandomRealm Builder assets.

Blender shader Mapping nodes are not a portable FBX material feature.  Builder
assets therefore evaluate their final material coordinates once at export time
and temporarily write those values into UV0.  No helper UV layer is created,
and every source datablock change is registered for restoration before it is
applied.
"""

from mathutils import Matrix, Vector


CONTRACT_VERSION = 3
MAPPING_POLICY = "bake-material-coordinates-to-uv0"


class UVExportContractError(RuntimeError):
    pass


def _datablock_key(value):
    if value is None:
        return 0
    try:
        return int(value.as_pointer())
    except (AttributeError, TypeError, ValueError):
        return id(value)


def _mapping_input_vector(mapping_node):
    if mapping_node is None or mapping_node.bl_idname != "ShaderNodeMapping":
        return None

    vector_input = mapping_node.inputs.get("Vector")
    if vector_input is None or not vector_input.links:
        return None

    link = vector_input.links[0]
    return link.from_node, link.from_socket


def _trace_image_texture_vector_source(image_node):
    if image_node is None or image_node.bl_idname != "ShaderNodeTexImage":
        return "UV", "", [], False

    vector_input = image_node.inputs.get("Vector")
    if vector_input is None or not vector_input.links:
        # Blender Image Texture nodes use the mesh's active render UV when the
        # Vector input is unconnected. Treating this as Generated silently
        # replaces authored UVs with bounding-box coordinates.
        return "UV", "", [], False

    mappings = []
    link = vector_input.links[0]
    from_node = link.from_node
    from_socket = link.from_socket
    for _ in range(8):
        if from_node is None:
            break

        if from_node.bl_idname == "ShaderNodeMapping":
            mappings.append(from_node)
            next_link = _mapping_input_vector(from_node)
            if next_link is None:
                # An unconnected Mapping input evaluates its constant Vector
                # socket; that cannot be represented by our per-loop UV bake.
                return "UV", "", list(reversed(mappings)), True
            from_node, from_socket = next_link
            continue

        if from_node.bl_idname == "ShaderNodeUVMap":
            return "UV", getattr(from_node, "uv_map", "") or "", list(reversed(mappings)), False

        if from_node.bl_idname == "ShaderNodeTexCoord":
            socket_name = getattr(from_socket, "name", "") or "UV"
            if socket_name in {"UV", "Generated"}:
                return socket_name, "", list(reversed(mappings)), False
            if socket_name == "Object":
                # Mesh-local Object coordinates are representable. A custom
                # Object/Empty applies another transform and must be rejected
                # until that transform is evaluated explicitly.
                has_custom_object = getattr(from_node, "object", None) is not None
                return socket_name, "", list(reversed(mappings)), has_custom_object
            return "UV", "", list(reversed(mappings)), True

        return "UV", "", list(reversed(mappings)), True

    return "UV", "", list(reversed(mappings)), True


def _mapping_node_values(mapping_node):
    location = mapping_node.inputs.get("Location")
    rotation = mapping_node.inputs.get("Rotation")
    scale = mapping_node.inputs.get("Scale")
    return (
        Vector(location.default_value if location is not None else (0.0, 0.0, 0.0)),
        Vector(rotation.default_value if rotation is not None else (0.0, 0.0, 0.0)),
        Vector(scale.default_value if scale is not None else (1.0, 1.0, 1.0)),
    )


def _mapping_node_is_identity(mapping_node):
    if mapping_node is None or mapping_node.bl_idname != "ShaderNodeMapping":
        return True

    location, rotation, scale = _mapping_node_values(mapping_node)
    epsilon = 0.00001
    return (
        all(abs(value) <= epsilon for value in location)
        and all(abs(value) <= epsilon for value in rotation)
        and all(abs(value - 1.0) <= epsilon for value in scale)
    )


def _apply_mapping_node(mapping_node, vector):
    if mapping_node is None or mapping_node.bl_idname != "ShaderNodeMapping":
        return vector

    location, rotation, scale = _mapping_node_values(mapping_node)
    mapped = Vector((
        vector.x * scale.x,
        vector.y * scale.y,
        vector.z * scale.z,
    ))
    mapped = Matrix.Rotation(rotation.x, 4, "X") @ mapped
    mapped = Matrix.Rotation(rotation.y, 4, "Y") @ mapped
    mapped = Matrix.Rotation(rotation.z, 4, "Z") @ mapped
    if getattr(mapping_node, "vector_type", "POINT") != "VECTOR":
        mapped += location
    return mapped


def _mesh_local_bounds(mesh):
    if mesh is None or len(mesh.vertices) == 0:
        return Vector((0.0, 0.0, 0.0)), Vector((1.0, 1.0, 1.0))

    minimum = Vector((
        min(vertex.co.x for vertex in mesh.vertices),
        min(vertex.co.y for vertex in mesh.vertices),
        min(vertex.co.z for vertex in mesh.vertices),
    ))
    maximum = Vector((
        max(vertex.co.x for vertex in mesh.vertices),
        max(vertex.co.y for vertex in mesh.vertices),
        max(vertex.co.z for vertex in mesh.vertices),
    ))
    return minimum, maximum - minimum


def _generated_coordinate(mesh, vertex_index, minimum, size):
    coordinate = mesh.vertices[vertex_index].co
    return Vector((
        0.0 if abs(size.x) <= 0.00001 else (coordinate.x - minimum.x) / size.x,
        0.0 if abs(size.y) <= 0.00001 else (coordinate.y - minimum.y) / size.y,
        0.0 if abs(size.z) <= 0.00001 else (coordinate.z - minimum.z) / size.z,
    ))


def _render_uv_layer(uv_layers):
    for layer in uv_layers:
        if getattr(layer, "active_render", False):
            return layer
    return uv_layers.active or (uv_layers[0] if len(uv_layers) else None)


def _material_recipe(material, mesh, image_node_resolver, render_layer):
    image_node = image_node_resolver(material)
    source_type, uv_map_name, mappings, unsupported = _trace_image_texture_vector_source(image_node)
    source_layer = None
    if source_type == "UV" and mesh.uv_layers:
        source_layer = mesh.uv_layers.get(uv_map_name) if uv_map_name else render_layer
        if source_layer is None:
            source_layer = render_layer or mesh.uv_layers[0]

    unsupported = unsupported or any(
        getattr(mapping, "vector_type", "POINT") not in {"POINT", "VECTOR"}
        for mapping in mappings
    )
    first_layer = mesh.uv_layers[0] if len(mesh.uv_layers) else None
    return {
        "source_type": source_type,
        "source_layer": source_layer,
        "source_layer_name": source_layer.name if source_layer is not None else "",
        "mappings": mappings,
        "unsupported": unsupported,
        "needs_bake": (
            unsupported
            or source_type != "UV"
            or any(not _mapping_node_is_identity(mapping) for mapping in mappings)
            or (source_layer is not None and source_layer != first_layer)
        ),
    }


def _evaluate_loop_uv(mesh, loop_index, vertex_index, recipe, fallback_layer, minimum, size):
    source_type = recipe.get("source_type", "UV") if recipe else "UV"
    source_layer = recipe.get("source_layer") if recipe else None

    if source_type == "Generated":
        vector = _generated_coordinate(mesh, vertex_index, minimum, size)
    elif source_type == "Object":
        coordinate = mesh.vertices[vertex_index].co
        vector = Vector((coordinate.x, coordinate.y, coordinate.z))
    else:
        layer = source_layer or fallback_layer
        if layer is None or loop_index >= len(layer.data):
            vector = Vector((0.0, 0.0, 0.0))
        else:
            uv = layer.data[loop_index].uv
            vector = Vector((uv.x, uv.y, 0.0))

    for mapping in recipe.get("mappings", ()) if recipe else ():
        vector = _apply_mapping_node(mapping, vector)
    return vector.to_2d()


def _material_signature(obj):
    return tuple(
        _datablock_key(slot.material)
        for slot in getattr(obj, "material_slots", ())
    )


def _restore_mesh_uv0(mesh, first_name, values, active_name, render_name):
    uv_layers = mesh.uv_layers
    first = uv_layers.get(first_name)
    if first is not None:
        for index, value in enumerate(values):
            if index < len(first.data):
                first.data[index].uv = value

    active = uv_layers.get(active_name) if active_name else None
    if active is not None:
        uv_layers.active = active

    render = uv_layers.get(render_name) if render_name else None
    if render is not None:
        render.active_render = True


def _restore_mapping(mapping, location_value, rotation_value, scale_value):
    location = mapping.inputs.get("Location")
    rotation = mapping.inputs.get("Rotation")
    scale = mapping.inputs.get("Scale")
    if location is not None:
        location.default_value = location_value
    if rotation is not None:
        rotation.default_value = rotation_value
    if scale is not None:
        scale.default_value = scale_value


def restore_actions_best_effort(actions, context="Unity UV export"):
    errors = []
    for restore in reversed(list(actions or ())):
        try:
            restore()
        except Exception as exception:
            errors.append(exception)
            print(f"[RR Helper] {context} cleanup failed: {exception}")
    return errors


def prepare_unity_uvs_for_export(root, mesh_objects, image_node_resolver):
    """Bake final material coordinates into UV0 and return reversible cleanup actions."""

    root_name = getattr(root, "name", "<asset>")
    restore_actions = []
    warnings = []
    mapping_states = {}
    mesh_groups = {}

    for obj in mesh_objects:
        mesh = getattr(obj, "data", None)
        if mesh is None or not hasattr(mesh, "uv_layers"):
            continue
        mesh_groups.setdefault(_datablock_key(mesh), []).append(obj)

    try:
        for objects in mesh_groups.values():
            obj = objects[0]
            mesh = obj.data
            signatures = {_material_signature(candidate) for candidate in objects}
            if len(signatures) > 1:
                object_names = ", ".join(candidate.name for candidate in objects)
                raise UVExportContractError(
                    f"{root_name}: shared mesh '{mesh.name}' has conflicting object material overrides "
                    f"({object_names}); make the mesh single-user before Builder export."
                )

            uv_layers = mesh.uv_layers
            if len(uv_layers) == 0:
                warnings.append(f"{root_name}: mesh '{obj.name}' has no UV Map.")
                continue

            first_layer = uv_layers[0]
            render_layer = _render_uv_layer(uv_layers)
            recipes = {}
            unsupported_materials = []
            for slot in obj.material_slots:
                material = slot.material
                if material is None:
                    continue
                material_key = _datablock_key(material)
                if material_key in recipes:
                    continue
                recipe = _material_recipe(material, mesh, image_node_resolver, render_layer)
                recipes[material_key] = recipe
                if recipe["unsupported"]:
                    unsupported_materials.append(material.name)
                for mapping in recipe["mappings"]:
                    mapping_states.setdefault(_datablock_key(mapping), mapping)

            if unsupported_materials:
                names = ", ".join(sorted(set(unsupported_materials)))
                raise UVExportContractError(
                    f"{root_name}: mesh '{obj.name}' has unsupported texture-coordinate graphs "
                    f"in material(s) {names}. Use an Image Texture with its default UV input, "
                    "or connect Texture Coordinate UV / UV Map through supported Mapping nodes."
                )

            needs_bake = (
                render_layer is not None
                and render_layer != first_layer
            ) or any(recipe["needs_bake"] for recipe in recipes.values())
            if not needs_bake:
                continue

            minimum, size = _mesh_local_bounds(mesh)
            baked_values = [Vector((0.0, 0.0)) for _ in mesh.loops]
            for polygon in mesh.polygons:
                material = None
                if polygon.material_index < len(obj.material_slots):
                    material = obj.material_slots[polygon.material_index].material
                recipe = recipes.get(_datablock_key(material)) if material is not None else None
                for loop_index in polygon.loop_indices:
                    baked_values[loop_index] = _evaluate_loop_uv(
                        mesh,
                        loop_index,
                        mesh.loops[loop_index].vertex_index,
                        recipe,
                        render_layer,
                        minimum,
                        size,
                    )

            original_values = [loop.uv.copy() for loop in first_layer.data]
            active_name = uv_layers.active.name if uv_layers.active is not None else ""
            render_name = render_layer.name if render_layer is not None else ""
            restore_actions.append(
                lambda mesh=mesh,
                first_name=first_layer.name,
                values=original_values,
                active_name=active_name,
                render_name=render_name: _restore_mesh_uv0(
                    mesh,
                    first_name,
                    values,
                    active_name,
                    render_name,
                )
            )

            for index, value in enumerate(baked_values):
                first_layer.data[index].uv = value
            uv_layers.active = first_layer
            first_layer.active_render = True

            detail_parts = []
            for material_key, recipe in recipes.items():
                if not recipe["needs_bake"]:
                    continue
                source = recipe["source_type"]
                if recipe["source_layer_name"]:
                    source += f"({recipe['source_layer_name']})"
                if recipe["mappings"]:
                    source += "+Mapping"
                material_name = next(
                    (
                        slot.material.name
                        for slot in obj.material_slots
                        if slot.material is not None and _datablock_key(slot.material) == material_key
                    ),
                    str(material_key),
                )
                detail_parts.append(f"{material_name}:{source}")

            print(
                "[RR Helper]",
                f"{root_name}: mesh '{obj.name}' baked final material coordinates directly into "
                f"UV0 '{first_layer.name}' for Unity FBX export"
                + (f" ({', '.join(detail_parts)})." if detail_parts else "."),
            )
        for mapping in mapping_states.values():
            if _mapping_node_is_identity(mapping):
                continue

            location_value, rotation_value, scale_value = _mapping_node_values(mapping)
            restore_actions.append(
                lambda mapping=mapping,
                location_value=location_value.copy(),
                rotation_value=rotation_value.copy(),
                scale_value=scale_value.copy(): _restore_mapping(
                    mapping,
                    location_value,
                    rotation_value,
                    scale_value,
                )
            )

            location = mapping.inputs.get("Location")
            rotation = mapping.inputs.get("Rotation")
            scale = mapping.inputs.get("Scale")
            if location is not None:
                location.default_value = (0.0, 0.0, 0.0)
            if rotation is not None:
                rotation.default_value = (0.0, 0.0, 0.0)
            if scale is not None:
                scale.default_value = (1.0, 1.0, 1.0)

        return restore_actions, warnings
    except Exception:
        restore_actions_best_effort(restore_actions, f"{root_name} UV prepare")
        raise
