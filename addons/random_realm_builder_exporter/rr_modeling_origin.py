import bmesh
import bpy
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
    objects = (
        selected_edit_objects_for_modeling_origin(context)
        if mode == "SELECTION"
        else selected_mesh_objects_for_modeling_origin(context)
    )
    if not objects:
        if mode == "SELECTION":
            raise RuntimeError("Select at least one mesh or curve object in Edit Mode.")
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
