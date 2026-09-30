import bmesh
import bpy
import math
from array import array
from mathutils import Vector


def selected_mesh_objects_for_modeling_origin(context):
    if context is None:
        return []

    if getattr(context, "mode", "") == "EDIT_MESH":
        objects = list(getattr(context, "objects_in_mode", []) or [])
        if not objects and getattr(context, "edit_object", None) is not None:
            objects = [context.edit_object]
    else:
        objects = list(getattr(context, "selected_objects", []) or [])

    return [obj for obj in objects if obj is not None and obj.type == "MESH"]


def selected_edit_objects_for_modeling_origin(context):
    if context is None or getattr(context, "mode", "") not in {"EDIT_MESH", "EDIT_CURVE"}:
        return []

    objects = list(getattr(context, "objects_in_mode", []) or [])
    if not objects and getattr(context, "edit_object", None) is not None:
        objects = [context.edit_object]
    return [obj for obj in objects if obj is not None and obj.type in {"MESH", "CURVE"}]


def selected_empty_objects_for_modeling_origin(context):
    if context is None or getattr(context, "mode", "") != "OBJECT":
        return []
    objects = list(getattr(context, "selected_objects", []) or [])
    return objects if objects and all(obj.type == "EMPTY" for obj in objects) else []


def modeling_empty_origin_selection(context):
    """Resolve movers and a read-only active target, never selection-list order."""
    if context is None or getattr(context, "mode", "") != "OBJECT":
        return [], None
    objects = list(getattr(context, "selected_objects", []) or [])
    empties = [obj for obj in objects if obj.type == "EMPTY"]
    if not empties:
        return [], None
    if len(empties) == len(objects):
        return empties, None
    active = getattr(context, "active_object", None)
    if active is not None and active in objects and active.type != "EMPTY" and len(objects) == len(empties) + 1:
        return empties, active
    return [], None


def _origin_matrix_is_finite(matrix):
    return all(math.isfinite(value) for row in matrix for value in row)


def _origin_matrix_matches(actual, expected):
    return all(
        math.isclose(a, b, rel_tol=2.0e-5, abs_tol=2.0e-4)
        for actual_row, expected_row in zip(actual, expected)
        for a, b in zip(actual_row, expected_row)
    )


def _origin_parent_depth(obj):
    depth = 0
    parent = obj.parent
    while parent is not None:
        depth += 1
        parent = parent.parent
    return depth


def _origin_references_selected_object(owner, selected):
    for prop in owner.bl_rna.properties:
        if prop.type == "POINTER" and prop.identifier != "rna_type":
            value = getattr(owner, prop.identifier, None)
            if isinstance(value, bpy.types.Object) and value in selected:
                return True
    return False


def _origin_nodes_reference_selected_object(tree, selected, seen):
    if tree is None or tree in seen:
        return False
    seen.add(tree)
    sockets = list(tree.interface.items_tree)
    for node in tree.nodes:
        if _origin_references_selected_object(node, selected):
            return True
        sockets.extend(node.inputs)
        sockets.extend(node.outputs)
        if _origin_nodes_reference_selected_object(getattr(node, "node_tree", None), selected, seen):
            return True
    return any(
        isinstance(value := getattr(socket, "default_value", None), bpy.types.Object) and value in selected
        for socket in sockets
    )


def _origin_modifier_references_selected_object(modifier, selected):
    if _origin_references_selected_object(modifier, selected):
        return True
    if modifier.type != "NODES":
        return False
    try:
        values = modifier.values()
    except TypeError:
        # A node group with no modifier inputs can have no ID-property storage.
        values = ()
    if any(isinstance(value, bpy.types.Object) and value in selected for value in values):
        return True
    return _origin_nodes_reference_selected_object(modifier.node_group, selected, set())


_ORIGIN_STABLE_CONSTRAINT_TYPES = {
    "LIMIT_LOCATION", "LIMIT_ROTATION", "LIMIT_SCALE", "LIMIT_DISTANCE",
    "COPY_LOCATION", "COPY_ROTATION", "COPY_SCALE", "COPY_TRANSFORMS",
    "CHILD_OF", "TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK",
    "TRANSFORM", "MAINTAIN_VOLUME", "FLOOR", "STRETCH_TO",
}


def _origin_constraint_object_references(owner):
    for prop in owner.bl_rna.properties:
        if prop.identifier == "rna_type":
            continue
        if prop.type == "POINTER":
            value = getattr(owner, prop.identifier, None)
            if isinstance(value, bpy.types.Object):
                yield value
        elif prop.type == "COLLECTION":
            for item in getattr(owner, prop.identifier):
                yield from _origin_constraint_object_references(item)


def _origin_constraint_dependency_is_unsafe(obj, selected, inverse_owners, seen):
    if obj in selected:
        return True
    if obj in seen:
        return False
    seen.add(obj)
    # Drivers can read arbitrary data, including scripted access with no RNA
    # variable. Keep this conservative instead of guessing their dependencies.
    if obj.animation_data and obj.animation_data.drivers:
        return True
    if obj.parent is not None:
        if obj.parent_type != "OBJECT":
            return True
        # This edge is compensated exactly, unlike a constraint's target edge.
        if obj not in inverse_owners and _origin_constraint_dependency_is_unsafe(
            obj.parent, selected, inverse_owners, seen
        ):
            return True
    for constraint in obj.constraints:
        if constraint.type not in _ORIGIN_STABLE_CONSTRAINT_TYPES:
            return True
        for target in _origin_constraint_object_references(constraint):
            if _origin_constraint_dependency_is_unsafe(target, selected, inverse_owners, seen):
                return True
    return False


def _origin_evaluated_geometry(obj, depsgraph):
    if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT"}:
        return None
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    if mesh is None:
        return None
    try:
        coordinates = array("f", [0.0]) * (3 * len(mesh.vertices))
        edges = array("i", [0]) * (2 * len(mesh.edges))
        loops = array("i", [0]) * len(mesh.loops)
        faces = array("i", [0]) * len(mesh.polygons)
        mesh.vertices.foreach_get("co", coordinates)
        mesh.edges.foreach_get("vertices", edges)
        mesh.loops.foreach_get("vertex_index", loops)
        mesh.polygons.foreach_get("loop_total", faces)
        return coordinates, edges, loops, faces
    finally:
        evaluated.to_mesh_clear()


def _origin_geometry_matches(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    return (len(actual[0]) == len(expected[0]) and actual[1:] == expected[1:]
            and all(math.isclose(a, b, rel_tol=2.0e-5, abs_tol=2.0e-4)
                    for a, b in zip(actual[0], expected[0])))


def _apply_empty_origin(context, obj, target):
    old_world = obj.matrix_world.copy()
    child_inverses = {child: child.matrix_parent_inverse.copy() for child in obj.children}
    delta = target - old_world.translation
    if obj.parent is not None:
        parent_space = obj.parent.matrix_world @ obj.matrix_parent_inverse
        delta = parent_space.inverted().to_3x3() @ delta
    # Change location alone: decomposing matrix_world can lose shear beneath a
    # rotated, non-uniformly scaled parent or alter rotation/delta channels.
    obj.location += delta
    context.view_layer.update()
    compensation = obj.matrix_world.inverted() @ old_world
    for child, parent_inverse in child_inverses.items():
        # Keep each child's local transform and animation channels unchanged.
        child.matrix_parent_inverse = compensation @ parent_inverse
    context.view_layer.update()


def apply_empty_origins_to_cursor(context, objects):
    target = context.scene.cursor.location.copy()
    if not all(math.isfinite(value) for value in target):
        raise RuntimeError("The 3D Cursor position is not finite.")
    return apply_empty_origins_to_point(context, objects, target)


def apply_empty_origins_to_point(context, objects, target, *, protected_objects=()):
    target = Vector(target)
    if not all(math.isfinite(value) for value in target):
        raise RuntimeError("The target origin position is not finite.")
    context.view_layer.update()
    objects = sorted(objects, key=_origin_parent_depth)
    selected = set(objects)
    affected = set(objects)
    inverse_owners = set()
    for obj in objects:
        affected.update(object_descendants(obj))
        inverse_owners.update(obj.children)
    # A separately selected target may depend on the Empty through constraints,
    # drivers or modifiers. Include it in the same preflight/rollback contract.
    affected.update(protected_objects)

    # Child constraints can retain their inputs: compensation preserves the
    # product parent.matrix_world @ matrix_parent_inverse, and local channels.
    # Reject actual dependencies on a moving origin, not every constrained part.
    for obj in affected:
        if obj.parent is not None and obj.parent_type != "OBJECT":
            raise RuntimeError(f"{obj.name}: only ordinary object parenting is supported.")
        if obj.parent is not None and obj.parent.type == "CURVE" and obj.parent.data.use_path:
            raise RuntimeError(f"{obj.name}: curve path parenting is not supported.")
        animation = obj.animation_data
        if (obj in selected and obj.constraints) or (animation and animation.drivers):
            raise RuntimeError(f"{obj.name}: constraints or drivers prevent keeping parts in place.")
        if obj not in selected and _origin_constraint_dependency_is_unsafe(
            obj, selected, inverse_owners, set()
        ):
            raise RuntimeError(f"{obj.name}: a constraint or driver depends on a moving Empty, or uses an unsupported constraint.")
        for modifier in obj.modifiers:
            if _origin_modifier_references_selected_object(modifier, selected):
                raise RuntimeError(f"{obj.name}: modifier '{modifier.name}' uses this Empty; moving it could change the geometry.")
        if not all(_origin_matrix_is_finite(matrix) for matrix in (
            obj.matrix_world, obj.matrix_basis, obj.matrix_parent_inverse,
        )):
            raise RuntimeError(f"{obj.name}: invalid transform.")
        if obj in selected or obj in inverse_owners:
            if not obj.is_editable or obj.is_property_readonly("matrix_parent_inverse"):
                raise RuntimeError(f"{obj.name}: the object is read-only.")
        if obj not in selected:
            continue
        if obj.instance_type != "NONE" or obj.instance_collection is not None:
            raise RuntimeError(f"{obj.name}: collection instances cannot move independently of their contents.")
        if animation and (animation.action or animation.nla_tracks):
            raise RuntimeError(f"{obj.name}: an animated Empty cannot be repositioned independently.")
        if obj.is_property_readonly("location"):
            raise RuntimeError(f"{obj.name}: location is read-only.")
        try:
            obj.matrix_world.inverted()
            if obj.parent is not None:
                (obj.parent.matrix_world @ obj.matrix_parent_inverse).inverted()
        except ValueError as exc:
            raise RuntimeError(f"{obj.name}: zero scale or a singular transform prevents repositioning.") from exc

    original_world = {obj: obj.matrix_world.copy() for obj in affected}
    original_locations = {obj: obj.location.copy() for obj in objects}
    original_inverses = {obj: obj.matrix_parent_inverse.copy() for obj in inverse_owners}
    depsgraph = context.evaluated_depsgraph_get()
    original_geometry = {obj: _origin_evaluated_geometry(obj, depsgraph)
                         for obj in affected if obj not in selected}
    try:
        for obj in objects:
            _apply_empty_origin(context, obj, target)
        for obj, matrix in original_world.items():
            expected = matrix.copy()
            if obj in selected:
                expected.translation = target
            if not _origin_matrix_matches(obj.matrix_world, expected):
                raise RuntimeError(f"{obj.name}: unable to preserve the assembly transforms.")
        depsgraph = context.evaluated_depsgraph_get()
        for obj, geometry in original_geometry.items():
            if not _origin_geometry_matches(_origin_evaluated_geometry(obj, depsgraph), geometry):
                raise RuntimeError(f"{obj.name}: unable to preserve the evaluated geometry.")
    except Exception:
        for obj, location in original_locations.items():
            obj.location = location
        for obj, parent_inverse in original_inverses.items():
            obj.matrix_parent_inverse = parent_inverse
        context.view_layer.update()
        raise
    return len(objects)


def mesh_selection_origin_point(obj):
    if obj is None or obj.type != "MESH" or obj.data is None or obj.mode != "EDIT":
        return None

    try:
        edit_mesh = bmesh.from_edit_mesh(obj.data)
    except (ReferenceError, RuntimeError, ValueError):
        return None

    selected_vertices = [vertex for vertex in edit_mesh.verts if vertex.select and not vertex.hide]
    if selected_vertices:
        local_point = sum((vertex.co for vertex in selected_vertices), Vector()) / len(selected_vertices)
        return obj.matrix_world @ local_point

    selected_edges = [edge for edge in edit_mesh.edges if edge.select and not edge.hide]
    if selected_edges:
        local_point = sum(
            ((edge.verts[0].co + edge.verts[1].co) * 0.5 for edge in selected_edges),
            Vector(),
        ) / len(selected_edges)
        return obj.matrix_world @ local_point

    selected_faces = [face for face in edit_mesh.faces if face.select and not face.hide]
    if selected_faces:
        local_point = sum((face.calc_center_median() for face in selected_faces), Vector()) / len(selected_faces)
        return obj.matrix_world @ local_point
    return None


def selected_curve_coordinates(obj):
    if obj is None or obj.type != "CURVE" or obj.data is None or obj.mode != "EDIT":
        return []

    coordinates = []
    for spline in obj.data.splines:
        for point in spline.bezier_points:
            if getattr(point, "hide", False):
                continue
            if point.select_control_point or point.select_left_handle or point.select_right_handle:
                # Blender commonly selects both handles with the anchor. Always resolve
                # any selection on a Bezier triple to its control point and count it once.
                coordinates.append(point.co.copy())

        for point in spline.points:
            if point.select and not getattr(point, "hide", False):
                coordinates.append(Vector(point.co[:3]))
    return coordinates


def curve_selection_origin_point(obj):
    coordinates = selected_curve_coordinates(obj)
    if not coordinates:
        return None
    local_point = sum(coordinates, Vector()) / len(coordinates)
    return obj.matrix_world @ local_point


def modeling_selection_origin_point(obj):
    if obj is None:
        return None
    if obj.type == "MESH":
        return mesh_selection_origin_point(obj)
    if obj.type == "CURVE":
        return curve_selection_origin_point(obj)
    return None


def modeling_origin_selection_label(context):
    if context is None or getattr(context, "mode", "") not in {"EDIT_MESH", "EDIT_CURVE"}:
        return ""

    if getattr(context, "mode", "") == "EDIT_CURVE":
        count = sum(
            len(selected_curve_coordinates(obj))
            for obj in selected_edit_objects_for_modeling_origin(context)
            if obj.type == "CURVE"
        )
        if count:
            suffix = "" if count == 1 else "s"
            return f"{count} curve point{suffix} selected"
        return ""

    counts = {"vertex": 0, "edge": 0, "face": 0}
    for obj in selected_mesh_objects_for_modeling_origin(context):
        try:
            edit_mesh = bmesh.from_edit_mesh(obj.data)
        except (ReferenceError, RuntimeError, ValueError):
            continue
        counts["vertex"] += sum(vertex.select and not vertex.hide for vertex in edit_mesh.verts)
        counts["edge"] += sum(edge.select and not edge.hide for edge in edit_mesh.edges)
        counts["face"] += sum(face.select and not face.hide for face in edit_mesh.faces)

    for element_name in ("face", "edge", "vertex"):
        count = counts[element_name]
        if count:
            suffix = "" if count == 1 else "s"
            return f"{count} {element_name}{suffix} selected"
    return ""


def object_descendants(obj):
    descendants = []
    stack = list(getattr(obj, "children", []) or [])
    while stack:
        child = stack.pop(0)
        descendants.append(child)
        stack.extend(list(getattr(child, "children", []) or []))
    return descendants


def apply_origin_to_mesh_object(obj, world_point):
    if obj is None or obj.type != "MESH" or obj.data is None:
        return False

    target = Vector(world_point)
    old_world = obj.matrix_world.copy()
    new_world = old_world.copy()
    new_world.translation = target

    try:
        data_transform = new_world.inverted() @ old_world
    except Exception:
        return False

    child_world_matrices = {child: child.matrix_world.copy() for child in object_descendants(obj)}
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    obj.data.transform(data_transform)
    obj.data.update()
    obj.matrix_world = new_world
    for child, matrix in child_world_matrices.items():
        if child.name in bpy.data.objects:
            child.matrix_world = matrix
    return True


def apply_origin_to_curve_object(obj, world_point):
    if obj is None or obj.type != "CURVE" or obj.data is None:
        return False

    target = Vector(world_point)
    old_world = obj.matrix_world.copy()
    new_world = old_world.copy()
    new_world.translation = target

    try:
        data_transform = new_world.inverted() @ old_world
    except Exception:
        return False

    child_world_matrices = {child: child.matrix_world.copy() for child in object_descendants(obj)}
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    obj.data.transform(data_transform, shape_keys=True)
    obj.data.update_tag()
    obj.matrix_world = new_world
    for child, matrix in child_world_matrices.items():
        if child.name in bpy.data.objects:
            child.matrix_world = matrix
    return True


def apply_origin_to_modeling_object(obj, world_point):
    if obj is None:
        return False
    if obj.type == "MESH":
        return apply_origin_to_mesh_object(obj, world_point)
    if obj.type == "CURVE":
        return apply_origin_to_curve_object(obj, world_point)
    return False


def mesh_bottom_origin_point(obj):
    if obj is None or obj.type != "MESH" or obj.data is None:
        return None

    mesh = obj.data
    if not mesh.polygons or not mesh.vertices:
        return None

    try:
        normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
    except Exception:
        normal_matrix = obj.matrix_world.to_3x3()

    down = Vector((0.0, 0.0, -1.0))
    candidates = []
    for polygon in mesh.polygons:
        if not polygon.vertices:
            continue

        world_normal = normal_matrix @ polygon.normal
        if world_normal.length <= 1.0e-8:
            continue
        world_normal.normalize()

        down_score = world_normal.dot(down)
        if down_score < 0.5:
            continue

        world_points = [obj.matrix_world @ mesh.vertices[index].co for index in polygon.vertices]
        center = Vector((0.0, 0.0, 0.0))
        for point in world_points:
            center += point
        center /= len(world_points)

        min_z = min(point.z for point in world_points)
        candidates.append((min_z, center.z, -down_score, polygon.index, center))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return candidates[0][4]


def modeling_origin_point_for_object(obj, settings, mode=None):
    mode = mode or getattr(settings, "modeling_origin_mode", "SELECTION")
    if mode == "SELECTION":
        return modeling_selection_origin_point(obj)
    if mode == "BOTTOM":
        return mesh_bottom_origin_point(obj)
    return None


def apply_modeling_origin(context, settings, mode=None):
    mode = mode or getattr(settings, "modeling_origin_mode", "SELECTION")
    if mode == "SELECTION":
        empty_objects, target_object = modeling_empty_origin_selection(context)
        if empty_objects:
            if target_object is not None:
                context.view_layer.update()
                # Capture before moving the Empty: the target may be one of its parts.
                target = target_object.matrix_world.translation.copy()
                return apply_empty_origins_to_point(
                    context, empty_objects, target, protected_objects=(target_object,))
            return apply_empty_origins_to_cursor(context, empty_objects)
    objects = (
        selected_edit_objects_for_modeling_origin(context)
        if mode == "SELECTION"
        else selected_mesh_objects_for_modeling_origin(context)
    )
    if not objects:
        if mode == "SELECTION":
            raise RuntimeError("Select Empty objects, then one target object last; select only Empties to use the 3D Cursor. Mesh/curve elements require Edit Mode.")
        raise RuntimeError("Select at least one mesh object.")

    selection_points = {}
    if mode == "SELECTION":
        if getattr(context, "mode", "") not in {"EDIT_MESH", "EDIT_CURVE"}:
            raise RuntimeError("Enter Edit Mode and select a mesh element or curve point.")
        selection_points = {
            obj: point
            for obj in objects
            if (point := modeling_selection_origin_point(obj)) is not None
        }
        if not selection_points:
            raise RuntimeError("Select at least one mesh element or curve point.")
    elif mode != "BOTTOM":
        raise RuntimeError(f"Unsupported origin mode: {mode}")

    active = context.view_layer.objects.active
    original_mode = active.mode if active is not None else "OBJECT"
    restore_edit_mode = getattr(context, "mode", "") in {"EDIT_MESH", "EDIT_CURVE"}
    if restore_edit_mode and bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")

    try:
        applied = 0
        missing = []
        for obj in objects:
            if obj.name not in bpy.data.objects:
                continue

            world_point = selection_points.get(obj) if mode == "SELECTION" else mesh_bottom_origin_point(obj)
            if world_point is None:
                missing.append(obj.name)
                continue

            if apply_origin_to_modeling_object(obj, world_point):
                applied += 1
    finally:
        if restore_edit_mode and active is not None and active.name in bpy.data.objects:
            context.view_layer.objects.active = active
            active.select_set(True)
            if bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode=original_mode)

    if applied <= 0:
        if mode == "SELECTION":
            raise RuntimeError("No selected mesh elements or curve points found.")
        detail = f" ({', '.join(missing[:3])})" if missing else ""
        raise RuntimeError(f"No downward bottom face found{detail}.")
    return applied


__all__ = [name for name, value in globals().items() if callable(value) and not name.startswith("_")]
