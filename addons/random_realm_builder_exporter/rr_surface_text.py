"""Create editable text aligned to one connected selected surface region.

The operator deliberately keeps the text as a Font object.  Shrinkwrap and
Solidify remain native, editable modifiers so the user can change the text or
adjust the result in Blender's normal modifier panels.
"""

import hashlib
import math
import re

import bmesh
import bpy
from bpy_extras import view3d_utils
from mathutils import Matrix, Vector
from mathutils.geometry import tessellate_polygon


SURFACE_TEXT_NAME = "Surface Text"
SURFACE_TEXT_BODY = "Text"
SURFACE_TEXT_CLEARANCE_METERS = 0.002
SURFACE_TEXT_THICKNESS_METERS = 0.1
SURFACE_TEXT_MAX_FACES = 10000
SURFACE_TEXT_MIN_NORMAL_DOT = 0.35
SURFACE_TEXT_MIN_ADJACENT_NORMAL_DOT = 0.5
SURFACE_TEXT_EPSILON = 1.0e-8
SURFACE_TEXT_GEOMETRY_TOLERANCE = 1.0e-7
SURFACE_SAMPLE_PREFIX = "RR_SurfaceSample"
SURFACE_SAMPLE_EXPORT_PREFIX = "RR_SurfaceSample_Export"
SURFACE_TEXT_ROLE = "surface_text"
SURFACE_TEXT_EXPORT_ROLE = "solid_geometry"
SURFACE_TEXT_EXPORT_PREFIX = "RR_SurfaceText_Geometry"


def _scene_scale_length(scene):
    unit_settings = getattr(scene, "unit_settings", None)
    scale_length = float(getattr(unit_settings, "scale_length", 1.0) or 1.0)
    return scale_length if abs(scale_length) > SURFACE_TEXT_EPSILON else 1.0


def _meters_to_scene_units(scene, meters):
    """Convert a real-world metre value to the scene's Blender units."""

    return float(meters) / _scene_scale_length(scene)


def _face_triangles(points):
    try:
        triangles = tessellate_polygon([points])
    except Exception:
        triangles = []
    if not triangles:
        triangles = [(0, index, index + 1) for index in range(1, len(points) - 1)]
    else:
        triangles = [
            tuple(index if isinstance(index, int) else points.index(index) for index in triangle)
            for triangle in triangles
        ]
    return triangles


def _orientation_2d(first, second, third):
    return (second.x - first.x) * (third.y - first.y) - (second.y - first.y) * (third.x - first.x)


def _on_segment_2d(first, second, point, tolerance):
    return (
        min(first.x, second.x) - tolerance <= point.x <= max(first.x, second.x) + tolerance
        and min(first.y, second.y) - tolerance <= point.y <= max(first.y, second.y) + tolerance
        and abs(_orientation_2d(first, second, point)) <= tolerance
    )


def _segments_intersect_2d(first, second, third, fourth, tolerance):
    orientations = (
        _orientation_2d(first, second, third),
        _orientation_2d(first, second, fourth),
        _orientation_2d(third, fourth, first),
        _orientation_2d(third, fourth, second),
    )
    if (
        ((orientations[0] > tolerance and orientations[1] < -tolerance)
         or (orientations[0] < -tolerance and orientations[1] > tolerance))
        and ((orientations[2] > tolerance and orientations[3] < -tolerance)
             or (orientations[2] < -tolerance and orientations[3] > tolerance))
    ):
        return True
    return any(
        abs(value) <= tolerance and _on_segment_2d(start, end, point, tolerance)
        for value, start, end, point in (
            (orientations[0], first, second, third),
            (orientations[1], first, second, fourth),
            (orientations[2], third, fourth, first),
            (orientations[3], third, fourth, second),
        )
    )


def _validate_face_shape(local_points, face_normal):
    if len(local_points) < 3:
        raise ValueError("A selected face has fewer than three vertices.")

    extent = max((point - local_points[0]).length for point in local_points)
    tolerance = max(extent * SURFACE_TEXT_GEOMETRY_TOLERANCE, SURFACE_TEXT_EPSILON)
    for first_index, first in enumerate(local_points):
        for second in local_points[first_index + 1:]:
            if (first - second).length <= tolerance:
                raise ValueError("The selected surface contains a degenerate face with duplicate vertices.")

    normal = face_normal.copy()
    if normal.length <= SURFACE_TEXT_EPSILON:
        raise ValueError("The selected surface contains a face with an unstable normal.")
    normal.normalize()
    right, up, _ = _text_axes(normal)
    projected = [Vector((point.dot(right), point.dot(up))) for point in local_points]
    for first_index in range(len(projected)):
        second_index = (first_index + 1) % len(projected)
        for third_index in range(first_index + 1, len(projected)):
            fourth_index = (third_index + 1) % len(projected)
            if first_index in (third_index, fourth_index) or second_index in (third_index, fourth_index):
                continue
            if _segments_intersect_2d(
                projected[first_index],
                projected[second_index],
                projected[third_index],
                projected[fourth_index],
                tolerance,
            ):
                raise ValueError("The selected surface contains a self-intersecting face.")

    plane_tolerance = max(extent * 0.05, tolerance)
    origin = local_points[0]
    if any(abs((point - origin).dot(normal)) > plane_tolerance for point in local_points[1:]):
        raise ValueError("The selected surface contains a severely non-planar face.")

    triangles = _face_triangles(local_points)
    if not triangles:
        raise ValueError("The selected surface contains a face that cannot be triangulated.")
    if any(
        (local_points[second] - local_points[first]).cross(local_points[third] - local_points[first]).length
        <= tolerance
        for first, second, third in triangles
    ):
        raise ValueError("The selected surface contains a degenerate triangle.")


def _face_world_geometry(obj, face):
    """Return an area-weighted world centroid and normal for one BMFace.

    The values are copied out of edit-mode BMesh immediately.  No BMesh face
    reference escapes this function or survives a mode change.
    """

    matrix_world = obj.matrix_world.copy()
    normal_matrix = matrix_world.to_3x3().inverted().transposed()
    local_points = [vert.co.copy() for vert in face.verts]
    _validate_face_shape(local_points, face.normal)
    triangles = _face_triangles(local_points)

    total_area = 0.0
    centroid_sum = Vector((0.0, 0.0, 0.0))
    for triangle in triangles:
        first, second, third = (
            matrix_world @ local_points[index] for index in triangle
        )
        area = (second - first).cross(third - first).length * 0.5
        if area <= SURFACE_TEXT_EPSILON:
            continue
        total_area += area
        centroid_sum += ((first + second + third) / 3.0) * area

    if total_area <= SURFACE_TEXT_EPSILON:
        raise ValueError("A selected face has zero world-space area.")

    world_normal = normal_matrix @ face.normal
    if world_normal.length <= SURFACE_TEXT_EPSILON:
        raise ValueError("A selected face has an unstable normal.")
    world_normal.normalize()

    return {
        "source_index": int(face.index),
        "area": total_area,
        "center": centroid_sum / total_area,
        "normal": world_normal,
        "points": [matrix_world @ point for point in local_points],
    }


def _selected_surface_info(obj, selected_faces):
    selected_faces = sorted(selected_faces, key=lambda face: face.index)
    if len(selected_faces) > SURFACE_TEXT_MAX_FACES:
        raise ValueError(
            f"Select a contiguous surface region with no more than {SURFACE_TEXT_MAX_FACES} faces."
        )

    selected_set = set(selected_faces)
    unvisited = set(selected_faces)
    components = []
    while unvisited:
        root = unvisited.pop()
        component = [root]
        pending = [root]
        while pending:
            face = pending.pop()
            for edge in face.edges:
                for linked_face in edge.link_faces:
                    if linked_face in unvisited:
                        unvisited.remove(linked_face)
                        pending.append(linked_face)
                        component.append(linked_face)
        components.append(component)
    if len(components) != 1:
        raise ValueError(
            "Selected faces must form one connected surface region; disconnected islands are not supported."
        )

    records = [_face_world_geometry(obj, face) for face in selected_faces]
    total_area = sum(record["area"] for record in records)
    if total_area <= SURFACE_TEXT_EPSILON:
        raise ValueError("The selected faces have no usable world-space area.")

    center = Vector((0.0, 0.0, 0.0))
    normal_sum = Vector((0.0, 0.0, 0.0))
    for record in records:
        weight = record["area"] / total_area
        center += record["center"] * weight
        normal_sum += record["normal"] * record["area"]

    if normal_sum.length <= SURFACE_TEXT_EPSILON:
        raise ValueError("The selected-face average normal is unstable.")
    average_normal_strength = normal_sum.length / total_area
    if average_normal_strength < SURFACE_TEXT_MIN_NORMAL_DOT:
        raise ValueError("The selected face normals conflict too strongly; choose nearby faces.")

    normal = normal_sum.normalized()
    face_normal_dots = [record["normal"].dot(normal) for record in records]
    if min(face_normal_dots) < SURFACE_TEXT_MIN_NORMAL_DOT:
        raise ValueError("The selected face normals conflict too strongly; choose nearby faces.")

    face_by_index = {face.index: face for face in selected_faces}
    record_by_index = {record["source_index"]: record for record in records}
    adjacent_dots = []
    for face in selected_faces:
        for edge in face.edges:
            for linked_face in edge.link_faces:
                if linked_face in selected_set and face.index < linked_face.index:
                    adjacent_dots.append(
                        record_by_index[face.index]["normal"].dot(record_by_index[linked_face.index]["normal"])
                    )
    if adjacent_dots and min(adjacent_dots) < SURFACE_TEXT_MIN_ADJACENT_NORMAL_DOT:
        fold_angle = math.degrees(math.acos(max(-1.0, min(1.0, min(adjacent_dots)))))
        raise ValueError(
            f"The selected surface has a fold of about {fold_angle:.1f} degrees; select a gently curved region."
        )

    points = [point for record in records for point in record["points"]]
    depths = [(point - center).dot(normal) for point in points]
    min_depth = min(depths)
    max_depth = max(depths)
    extent = max((point - center).length for point in points)

    return {
        "records": records,
        "center": center,
        "normal": normal,
        "min_depth": min_depth,
        "max_depth": max_depth,
        "depth_span": max_depth - min_depth,
        "extent": extent,
        "source_face_count": len(records),
        "source_face_indices": [record["source_index"] for record in records],
        "connected_components": len(components),
        "normal_min_dot": min(face_normal_dots),
        "adjacent_min_dot": min(adjacent_dots) if adjacent_dots else 1.0,
        "max_adjacent_fold_degrees": (
            math.degrees(math.acos(max(-1.0, min(1.0, min(adjacent_dots)))))
            if adjacent_dots
            else 0.0
        ),
    }


def _text_axes(normal):
    """Build Right, Up, Normal as the text object's local X/Y/Z axes."""

    reference_up = Vector((0.0, 0.0, 1.0))
    up = reference_up - normal * reference_up.dot(normal)
    if up.length_squared <= SURFACE_TEXT_EPSILON:
        reference_up = Vector((0.0, 1.0, 0.0))
        up = reference_up - normal * reference_up.dot(normal)
    if up.length_squared <= SURFACE_TEXT_EPSILON:
        reference_up = Vector((1.0, 0.0, 0.0))
        up = reference_up - normal * reference_up.dot(normal)
    if up.length_squared <= SURFACE_TEXT_EPSILON:
        raise ValueError("Could not construct a stable text up direction.")

    up.normalize()
    right = up.cross(normal)
    if right.length_squared <= SURFACE_TEXT_EPSILON:
        raise ValueError("Could not construct a stable text right direction.")
    right.normalize()
    up = normal.cross(right).normalized()
    return right, up, normal


def _capture_state(context):
    return {
        "mode": context.mode,
        "active": context.view_layer.objects.active,
        "selected": list(context.selected_objects),
    }


def _restore_state(context, state):
    if state is None:
        return

    try:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass

    for obj in context.view_layer.objects:
        try:
            obj.select_set(False)
        except Exception:
            pass

    for obj in state["selected"]:
        if obj is not None and obj.name in bpy.data.objects:
            try:
                obj.select_set(True)
            except Exception:
                pass

    active = state["active"]
    if active is not None and active.name in bpy.data.objects:
        context.view_layer.objects.active = active

    if state["mode"] == "EDIT_MESH" and active is not None and active.type == "MESH":
        try:
            bpy.ops.object.mode_set(mode="EDIT")
        except Exception:
            pass


def _remove_created_text(text_obj, text_data):
    if text_obj is not None and text_obj.name in bpy.data.objects:
        bpy.data.objects.remove(text_obj, do_unlink=True)
    if text_data is not None and text_data.users == 0:
        bpy.data.curves.remove(text_data)


def _remove_surface_mesh(surface_obj, surface_mesh):
    if surface_obj is not None and surface_obj.name in bpy.data.objects:
        bpy.data.objects.remove(surface_obj, do_unlink=True)
    if surface_mesh is not None and surface_mesh.users == 0:
        bpy.data.meshes.remove(surface_mesh)


def _target_ray_hits(target, starts, direction, project_limit, depsgraph):
    """Return True/False when target ray casting is available and definitive."""

    target_eval = target.evaluated_get(depsgraph)
    try:
        inverse = target_eval.matrix_world.inverted()
        inverse_3x3 = inverse.to_3x3()
        forward_3x3 = target_eval.matrix_world.to_3x3()
    except Exception:
        return None

    for start in starts:
        local_origin = inverse @ start
        local_direction = inverse_3x3 @ direction
        if local_direction.length <= SURFACE_TEXT_EPSILON:
            continue
        local_direction.normalize()
        world_step = (forward_3x3 @ local_direction).length
        if world_step <= SURFACE_TEXT_EPSILON:
            continue
        local_distance = project_limit / world_step
        try:
            hit = target_eval.ray_cast(
                local_origin,
                local_direction,
                distance=local_distance,
            )[0]
        except Exception:
            return None
        if hit:
            return True

    return False


def _create_text_object(context, target, surface, clearance, project_limit):
    text_data = bpy.data.curves.new(SURFACE_TEXT_NAME, type="FONT")
    text_data.body = SURFACE_TEXT_BODY
    text_data.align_x = "CENTER"
    text_data.align_y = "CENTER"
    text_data.extrude = 0.0
    text_data.bevel_depth = 0.0

    text_obj = bpy.data.objects.new(SURFACE_TEXT_NAME, text_data)
    collection = next(iter(target.users_collection), None)
    if collection is None:
        collection = getattr(context, "collection", None) or context.scene.collection
    collection.objects.link(text_obj)

    text_obj.rotation_mode = "QUATERNION"
    text_obj.scale = (1.0, 1.0, 1.0)
    _apply_text_pose(text_obj, surface, clearance)

    shrinkwrap = text_obj.modifiers.new("Shrinkwrap", "SHRINKWRAP")
    shrinkwrap.wrap_method = "PROJECT"
    shrinkwrap.use_project_x = False
    shrinkwrap.use_project_y = False
    shrinkwrap.use_project_z = True
    shrinkwrap.use_negative_direction = True
    shrinkwrap.use_positive_direction = False
    shrinkwrap.wrap_mode = "ON_SURFACE"
    shrinkwrap.offset = clearance
    shrinkwrap.project_limit = project_limit
    shrinkwrap.target = target

    solidify = text_obj.modifiers.new("Solidify", "SOLIDIFY")
    solidify.thickness = -_meters_to_scene_units(context.scene, SURFACE_TEXT_THICKNESS_METERS)
    solidify.offset = -1.0
    solidify.use_rim = True

    return text_obj, text_data


def _apply_text_pose(text_obj, surface, clearance):
    right, up, normal = _text_axes(surface["normal"])
    location = surface["center"] + normal * (surface["max_depth"] + clearance)
    rotation = Matrix((right, up, normal)).transposed()
    text_obj.rotation_quaternion = rotation.to_quaternion()
    text_obj.location = location


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_") or "Object"


def _matrix_values(matrix):
    return [float(matrix[row][column]) for row in range(4) for column in range(4)]


def _vector_values(vector):
    return [float(component) for component in vector]


def _surface_identity(target, surface):
    source_indices = tuple(int(index) for index in surface.get("source_face_indices", ()))
    side = str(surface.get("candidate_label", "Original"))
    digest = hashlib.sha1(
        repr((target.name_full, source_indices, side)).encode("utf-8")
    ).hexdigest()[:10]
    return f"{_safe_name(target.name)}_{_safe_name(side)}_{digest}"


def surface_sampling_export_name(surface_obj):
    """Return a short FBX-safe alias for an authored sampling mesh.

    Blender's FBX importer rewrites object names longer than 63 characters,
    which breaks manifest pairing even though the source name is stable.  A
    compact alias keeps the source object untouched and gives Unity a name
    that survives the round trip.  The stored surface identity is preferred;
    the object full name is a deterministic fallback for older files.
    """

    if surface_obj is None:
        return ""
    identity = str(surface_obj.get("rr_surface_identity", "") or "")
    if not identity:
        identity = hashlib.sha1(
            str(getattr(surface_obj, "name_full", surface_obj.name)).encode("utf-8")
        ).hexdigest()[:12]
    identity = _safe_name(identity)
    if len(identity) > 24:
        identity = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"{SURFACE_SAMPLE_EXPORT_PREFIX}_{identity}"


def create_surface_text_sampling_export_aliases(root):
    """Create transient short-name sampling meshes for FBX export.

    Authored sampling meshes remain hidden and source-linked.  The aliases are
    exported beside them only for the duration of the FBX bake, then removed
    by the caller.  Unity pairs them through the manifest field
    ``samplingSurfaceExportObjectName``.
    """

    if root is None:
        return []

    aliases = []
    seen_sources = set()
    collection = next(iter(root.users_collection), None) or bpy.context.scene.collection
    for source in find_surface_text_objects_for_export(root):
        surface_name = str(source.get("rr_surface_text_surface_object", "") or "")
        surface_obj = bpy.data.objects.get(surface_name)
        if surface_obj is None or surface_obj.type != "MESH":
            continue
        if surface_obj.get("rr_surface_role") != "sampling_surface":
            continue
        if surface_obj in seen_sources:
            continue
        seen_sources.add(surface_obj)

        alias_name = surface_sampling_export_name(surface_obj)
        existing_alias = bpy.data.objects.get(alias_name) if alias_name else None
        if existing_alias is surface_obj:
            continue
        if existing_alias is not None:
            raise RuntimeError(
                f"Surface Text sampling export alias '{alias_name}' is already used by "
                f"'{existing_alias.name_full}'."
            )

        mesh = surface_obj.data.copy()
        alias = bpy.data.objects.new(alias_name, mesh)
        collection.objects.link(alias)
        alias.parent = surface_obj.parent
        alias.matrix_world = surface_obj.matrix_world.copy()
        alias.hide_render = False
        alias.hide_viewport = False
        alias.hide_select = False
        alias.display_type = "TEXTURED"
        alias["rr_surface_role"] = "sampling_surface_export_alias"
        alias["rr_surface_source_object"] = surface_obj.name
        alias["rr_surface_identity"] = str(surface_obj.get("rr_surface_identity", ""))
        aliases.append(alias)
    return aliases


def _create_surface_mesh(context, target, surface):
    """Create the persistent, source-linked mesh used as a Unity sampling surface."""

    identity = _surface_identity(target, surface)
    object_name = f"{SURFACE_SAMPLE_PREFIX}_{identity}"
    mesh_name = f"{SURFACE_SAMPLE_PREFIX}Mesh_{identity}"
    try:
        target_inverse = target.matrix_world.inverted()
    except Exception as exc:
        raise RuntimeError("The target transform cannot be inverted for a sampling surface.") from exc

    vertices = []
    faces = []
    vertex_lookup = {}

    def add_vertex(world_point):
        local_point = target_inverse @ world_point
        key = tuple(round(float(component), 8) for component in local_point)
        index = vertex_lookup.get(key)
        if index is None:
            index = len(vertices)
            vertex_lookup[key] = index
            vertices.append(tuple(float(component) for component in local_point))
        return index

    for record in surface["records"]:
        face_indices = [add_vertex(point) for point in record["points"]]
        if len(set(face_indices)) < 3:
            raise RuntimeError("The sampling surface contains a degenerate face.")
        faces.append(face_indices)

    if not vertices or not faces:
        raise RuntimeError("The selected region produced no sampling surface geometry.")

    mesh = bpy.data.meshes.new(mesh_name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update(calc_edges=True)
    if len(mesh.polygons) != len(faces):
        bpy.data.meshes.remove(mesh)
        raise RuntimeError("Blender could not create the selected sampling surface faces.")

    surface_obj = bpy.data.objects.new(object_name, mesh)
    collection = next(iter(target.users_collection), None)
    if collection is None:
        collection = getattr(context, "collection", None) or context.scene.collection
    collection.objects.link(surface_obj)
    surface_obj.parent = target
    surface_obj.matrix_parent_inverse = Matrix.Identity(4)
    surface_obj.matrix_basis = Matrix.Identity(4)
    surface_obj.hide_render = True
    surface_obj.display_type = "WIRE"

    right, up, normal = _text_axes(surface["normal"])
    source_matrix = target.matrix_world.copy()
    source_face_indices = [int(index) for index in surface["source_face_indices"]]
    surface_obj["rr_surface_role"] = "sampling_surface"
    surface_obj["rr_surface_identity"] = identity
    surface_obj["rr_surface_source_object"] = target.name
    surface_obj["rr_surface_source_object_full_name"] = target.name_full
    surface_obj["rr_surface_source_face_count"] = int(surface["source_face_count"])
    surface_obj["rr_surface_source_face_indices"] = source_face_indices
    surface_obj["rr_surface_connected_components"] = int(surface["connected_components"])
    surface_obj["rr_surface_mirror_side"] = str(surface.get("candidate_label", "Original"))
    surface_obj["rr_surface_mirror_axis"] = int(surface.get("mirror_axis", -1))
    surface_obj["rr_surface_mirror_object"] = str(surface.get("mirror_object_name", ""))
    surface_obj["rr_surface_center_world"] = _vector_values(surface["center"])
    surface_obj["rr_surface_normal_world"] = _vector_values(normal)
    surface_obj["rr_surface_right_world"] = _vector_values(right)
    surface_obj["rr_surface_up_world"] = _vector_values(up)
    surface_obj["rr_surface_source_matrix_world"] = _matrix_values(source_matrix)
    surface_obj["rr_surface_scene_scale_length"] = _scene_scale_length(context.scene)
    surface_obj["rr_surface_vertex_count"] = len(vertices)
    surface_obj["rr_surface_face_count"] = len(faces)
    surface_obj["rr_surface_normal_min_dot"] = float(surface.get("normal_min_dot", 1.0))
    surface_obj["rr_surface_max_adjacent_fold_degrees"] = float(
        surface.get("max_adjacent_fold_degrees", 0.0)
    )

    mesh["rr_surface_role"] = "sampling_surface_mesh"
    mesh["rr_surface_identity"] = identity
    mesh["rr_surface_source_object"] = target.name
    mesh["rr_surface_source_face_count"] = int(surface["source_face_count"])
    mesh["rr_surface_mirror_side"] = str(surface.get("candidate_label", "Original"))
    mesh["rr_surface_face_indices"] = source_face_indices
    return surface_obj, mesh


def _annotate_text_object(context, text_obj, target, surface, surface_obj):
    right, up, normal = _text_axes(surface["normal"])
    text_obj["rr_surface_text_role"] = "surface_text"
    text_obj["rr_surface_text_source_target"] = target.name
    text_obj["rr_surface_text_source_target_full_name"] = target.name_full
    text_obj["rr_surface_text_surface_object"] = surface_obj.name
    text_obj["rr_surface_text_surface_mesh"] = surface_obj.data.name
    text_obj["rr_surface_text_source_face_count"] = int(surface["source_face_count"])
    text_obj["rr_surface_text_source_face_indices"] = [
        int(index) for index in surface["source_face_indices"]
    ]
    text_obj["rr_surface_text_connected_components"] = int(surface["connected_components"])
    text_obj["rr_surface_text_mirror_side"] = str(surface.get("candidate_label", "Original"))
    text_obj["rr_surface_text_mirror_axis"] = int(surface.get("mirror_axis", -1))
    text_obj["rr_surface_text_mirror_object"] = str(surface.get("mirror_object_name", ""))
    text_obj["rr_surface_text_center_world"] = _vector_values(surface["center"])
    text_obj["rr_surface_text_normal_world"] = _vector_values(normal)
    text_obj["rr_surface_text_right_world"] = _vector_values(right)
    text_obj["rr_surface_text_up_world"] = _vector_values(up)
    text_obj["rr_surface_text_source_matrix_world"] = _matrix_values(target.matrix_world)
    text_obj["rr_surface_text_normal_min_dot"] = float(surface.get("normal_min_dot", 1.0))
    text_obj["rr_surface_text_max_adjacent_fold_degrees"] = float(
        surface.get("max_adjacent_fold_degrees", 0.0)
    )
    text_obj["rr_surface_text_thickness_meters"] = float(SURFACE_TEXT_THICKNESS_METERS)
    text_obj["rr_surface_text_preview"] = False


def _surface_text_modifier_pair(source):
    shrinkwrap = next(
        (modifier for modifier in source.modifiers if modifier.type == "SHRINKWRAP"),
        None,
    )
    solidify = next(
        (modifier for modifier in source.modifiers if modifier.type == "SOLIDIFY"),
        None,
    )
    return shrinkwrap, solidify


def _migrate_legacy_surface_text_source(source, allowed_target_names=None):
    """Tag the pre-manifest Surface Text Font used by older Builder6 files.

    The first Surface Text implementation stored only ``rr_surface_text_preview``
    and the Shrinkwrap/Solidify modifiers.  Keep those authored files exportable
    without saving them or guessing for ordinary decorative Font objects.
    """

    if source.type != "FONT":
        return False
    if source.get("rr_surface_text_role") == SURFACE_TEXT_ROLE:
        return True
    if "rr_surface_text_preview" not in source:
        return False
    shrinkwrap, solidify = _surface_text_modifier_pair(source)
    target = getattr(shrinkwrap, "target", None) if shrinkwrap is not None else None
    if target is None or solidify is None:
        return False
    if allowed_target_names is not None and not (
        target.name in allowed_target_names or target.name_full in allowed_target_names
    ):
        return False
    try:
        thickness = abs(float(solidify.thickness))
    except (TypeError, ValueError):
        return False
    if thickness <= SURFACE_TEXT_EPSILON:
        return False

    scene = getattr(bpy.context, "scene", None)
    scene_scale = _scene_scale_length(scene) if scene is not None else 1.0
    source["rr_surface_text_role"] = SURFACE_TEXT_ROLE
    source["rr_surface_text_source_target"] = target.name
    source["rr_surface_text_source_target_full_name"] = target.name_full
    source["rr_surface_text_surface_object"] = target.name
    source["rr_surface_text_surface_mesh"] = getattr(
        getattr(target, "data", None), "name", ""
    )
    source["rr_surface_text_thickness_meters"] = thickness * scene_scale
    source["rr_surface_text_legacy_migrated"] = True
    return True


def find_surface_text_objects_for_export(root):
    """Find authored Surface Text Font objects associated with an export root."""

    if root is None:
        return []

    export_targets = [root]
    export_targets.extend(list(root.children_recursive))
    target_names = {getattr(target, "name", "") for target in export_targets}
    target_full_names = {getattr(target, "name_full", "") for target in export_targets}
    matches = []
    allowed_target_names = target_names | target_full_names
    for candidate in bpy.data.objects:
        if candidate.type != "FONT":
            continue
        if candidate.get("rr_surface_text_role") != SURFACE_TEXT_ROLE and not _migrate_legacy_surface_text_source(
            candidate,
            allowed_target_names,
        ):
            continue
        source_name = str(candidate.get("rr_surface_text_source_target", ""))
        source_full_name = str(candidate.get("rr_surface_text_source_target_full_name", ""))
        if source_name in target_names or source_full_name in target_full_names:
            matches.append(candidate)
    return matches


def build_surface_text_manifest(root):
    """Describe authored Surface Text sources without baking editable geometry into JSON.

    The manifest is the handoff needed by Unity's editor regeneration path: the
    source .blend is already recorded by the parent exporter, while this list
    identifies the editable Font and its stable target/sampling objects.  The
    actual O/B/A outline and holes remain Blender-generated geometry.
    """

    descriptors = []
    scene = getattr(bpy.context, "scene", None)
    scene_scale = _scene_scale_length(scene) if scene is not None else 1.0
    for source in find_surface_text_objects_for_export(root):
        solidify = next(
            (modifier for modifier in source.modifiers if modifier.type == "SOLIDIFY"),
            None,
        )
        authored_thickness = source.get("rr_surface_text_thickness_meters")
        try:
            thickness_meters = abs(float(authored_thickness))
        except (TypeError, ValueError):
            thickness_meters = 0.0
        if thickness_meters <= SURFACE_TEXT_EPSILON and solidify is not None:
            thickness_meters = abs(float(solidify.thickness)) * scene_scale

        sampling_surface_name = str(
            source.get("rr_surface_text_surface_object", "")
        )
        sampling_surface = bpy.data.objects.get(sampling_surface_name)
        sampling_export_name = ""
        if (
            sampling_surface is not None
            and sampling_surface.type == "MESH"
            and sampling_surface.get("rr_surface_role") == "sampling_surface"
        ):
            sampling_export_name = surface_sampling_export_name(sampling_surface)

        descriptors.append(
            {
                "fontObjectName": source.name,
                "fontObjectFullName": source.name_full,
                "targetObjectName": str(source.get("rr_surface_text_source_target", "")),
                "targetObjectFullName": str(
                    source.get("rr_surface_text_source_target_full_name", "")
                ),
                "samplingSurfaceObjectName": sampling_surface_name,
                "samplingSurfaceExportObjectName": sampling_export_name,
                "samplingSurfaceMeshName": str(
                    source.get("rr_surface_text_surface_mesh", "")
                ),
                "exportObjectName": f"{SURFACE_TEXT_EXPORT_PREFIX}_{_safe_name(source.name)}",
                "thicknessMeters": round(thickness_meters, 6),
            }
        )
    return descriptors


def create_surface_text_export_meshes(root, depsgraph=None):
    """Evaluate editable Surface Text into transient, exportable solid meshes.

    Blender's FBX exporter intentionally writes MESH objects only in this
    project. Surface Text remains a native Font with Shrinkwrap/Solidify while
    editing, so export creates a temporary evaluated mesh and leaves the source
    Font and its modifiers untouched.
    """

    if root is None:
        return []
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()

    export_objects = []
    export_targets = [root]
    export_targets.extend(list(root.children_recursive))
    target_by_name = {getattr(target, "name", ""): target for target in export_targets}
    target_by_full_name = {
        getattr(target, "name_full", ""): target for target in export_targets
    }
    collection = next(iter(root.users_collection), None) or bpy.context.scene.collection

    for source in find_surface_text_objects_for_export(root):
        evaluated = source.evaluated_get(depsgraph)
        mesh = None
        try:
            mesh = bpy.data.meshes.new_from_object(
                evaluated,
                depsgraph=depsgraph,
                preserve_all_data_layers=True,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not evaluate Surface Text '{source.name}' for FBX export."
            ) from exc

        if mesh is None or len(mesh.vertices) == 0 or len(mesh.polygons) == 0:
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            raise RuntimeError(
                f"Surface Text '{source.name}' produced no exportable solid geometry."
            )
        if any(
            not math.isfinite(float(component))
            for vertex in mesh.vertices
            for component in vertex.co
        ):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            raise RuntimeError(
                f"Surface Text '{source.name}' produced non-finite export coordinates."
            )

        solidify = next(
            (modifier for modifier in source.modifiers if modifier.type == "SOLIDIFY"),
            None,
        )
        if solidify is None or abs(float(solidify.thickness)) <= SURFACE_TEXT_EPSILON:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            raise RuntimeError(
                f"Surface Text '{source.name}' has no positive Solidify thickness."
            )

        local_z_span = max(vertex.co.z for vertex in mesh.vertices) - min(
            vertex.co.z for vertex in mesh.vertices
        )
        if local_z_span < abs(float(solidify.thickness)) * 0.5:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            raise RuntimeError(
                f"Surface Text '{source.name}' evaluated without its requested thickness."
            )

        export_name = f"{SURFACE_TEXT_EXPORT_PREFIX}_{_safe_name(source.name)}"
        export_obj = bpy.data.objects.new(export_name, mesh)
        collection.objects.link(export_obj)
        source_target = target_by_full_name.get(
            str(source.get("rr_surface_text_source_target_full_name", ""))
        ) or target_by_name.get(str(source.get("rr_surface_text_source_target", "")))
        parent = source_target or root
        export_obj.parent = parent
        export_obj.matrix_world = source.matrix_world.copy()
        export_obj.hide_render = False
        export_obj.hide_viewport = False
        export_obj.display_type = "TEXTURED"

        for slot in source.material_slots:
            if slot.material is not None and slot.material.name not in mesh.materials:
                mesh.materials.append(slot.material)
        for key, value in source.items():
            if not str(key).startswith("rr_surface_text_"):
                continue
            try:
                export_obj[key] = value
            except (TypeError, ValueError):
                export_obj[key] = str(value)
        export_obj["rr_surface_text_export_role"] = SURFACE_TEXT_EXPORT_ROLE
        export_obj["rr_surface_text_source_object"] = source.name
        export_objects.append(export_obj)

    return export_objects


def _evaluated_world_points(context, text_obj):
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = text_obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        points = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        if not points:
            raise RuntimeError("Blender generated no text surface for Shrinkwrap/Solidify.")
        if not all(all(math.isfinite(component) for component in point) for point in points):
            raise RuntimeError("The generated surface text contains invalid coordinates.")
        return points, evaluated.matrix_world.inverted()
    finally:
        evaluated.to_mesh_clear()


def _validate_evaluated_text(context, text_obj, expected_thickness, surface_center, surface_normal):
    """Confirm the evaluated result is finite, thick, and extends outward."""

    context.view_layer.update()
    full_points, inverse_world = _evaluated_world_points(context, text_obj)

    # Read the Shrinkwrap-only surface as a reference.  This verifies the
    # actual Solidify displacement instead of trusting the sign of Thickness
    # and Offset in isolation.
    solidify = next((modifier for modifier in text_obj.modifiers if modifier.type == "SOLIDIFY"), None)
    if solidify is None:
        raise RuntimeError("Solidify modifier is missing from the generated text.")
    show_viewport = solidify.show_viewport
    try:
        solidify.show_viewport = False
        context.view_layer.update()
        surface_points, _ = _evaluated_world_points(context, text_obj)
    finally:
        solidify.show_viewport = show_viewport
        context.view_layer.update()

    # The local-Z span catches a degenerate or missing Solidify result even
    # when the target happens to be nearly horizontal in world space.
    local_points = [inverse_world @ point for point in full_points]
    local_z_span = max(point.z for point in local_points) - min(point.z for point in local_points)
    if local_z_span < expected_thickness * 0.5:
        raise RuntimeError("Solidify did not generate the requested thickness.")

    surface_depths = [(point - surface_center).dot(surface_normal) for point in surface_points]
    full_depths = [(point - surface_center).dot(surface_normal) for point in full_points]
    outward_extension = max(full_depths) - max(surface_depths)
    inward_extension = min(full_depths) - min(surface_depths)
    tolerance = max(expected_thickness * 0.05, SURFACE_TEXT_EPSILON)
    if outward_extension < expected_thickness * 0.5 or inward_extension < -tolerance:
        raise RuntimeError("Solidify thickness was not generated outward from the selected surface.")


def _select_created_text(context, text_obj):
    for obj in context.view_layer.objects:
        obj.select_set(False)
    text_obj.select_set(True)
    context.view_layer.objects.active = text_obj


def _surface_project_limit(context, surface, clearance, thickness):
    return max(
        _meters_to_scene_units(context.scene, 0.25),
        surface["depth_span"]
        + max(surface["extent"] * 2.0, thickness, clearance * 8.0)
        + thickness
        + clearance * 4.0,
    )


def _surface_ray_starts(surface, clearance):
    ray_start = surface["center"] + surface["normal"] * (surface["max_depth"] + clearance)
    ray_starts = [ray_start]
    for record in surface["records"]:
        record_depth = (record["center"] - surface["center"]).dot(surface["normal"])
        ray_starts.append(
            record["center"]
            + surface["normal"] * (surface["max_depth"] - record_depth + clearance)
        )
    return ray_starts


def _polygon_area_and_center(points):
    if len(points) < 3:
        raise ValueError("A mirrored face has fewer than three vertices.")
    try:
        triangles = tessellate_polygon([points])
    except Exception:
        triangles = []
    if not triangles:
        triangles = [(0, index, index + 1) for index in range(1, len(points) - 1)]
    else:
        triangles = [
            tuple(index if isinstance(index, int) else points.index(index) for index in triangle)
            for triangle in triangles
        ]

    total_area = 0.0
    centroid_sum = Vector((0.0, 0.0, 0.0))
    for first_index, second_index, third_index in triangles:
        first, second, third = points[first_index], points[second_index], points[third_index]
        area = (second - first).cross(third - first).length * 0.5
        if area <= SURFACE_TEXT_EPSILON:
            continue
        total_area += area
        centroid_sum += ((first + second + third) / 3.0) * area
    if total_area <= SURFACE_TEXT_EPSILON:
        raise ValueError("A mirrored face has zero world-space area.")
    return total_area, centroid_sum / total_area


def _surface_from_records(records):
    total_area = sum(record["area"] for record in records)
    if total_area <= SURFACE_TEXT_EPSILON:
        raise ValueError("The mirrored selected faces have no usable area.")

    center = Vector((0.0, 0.0, 0.0))
    normal_sum = Vector((0.0, 0.0, 0.0))
    for record in records:
        center += record["center"] * record["area"]
        normal_sum += record["normal"] * record["area"]
    if normal_sum.length <= SURFACE_TEXT_EPSILON:
        raise ValueError("The mirrored selected-face average normal is unstable.")
    average_normal_strength = normal_sum.length / total_area
    if average_normal_strength < SURFACE_TEXT_MIN_NORMAL_DOT:
        raise ValueError("The mirrored face normals conflict too strongly.")

    normal = normal_sum.normalized()
    center /= total_area
    points = [point for record in records for point in record["points"]]
    depths = [(point - center).dot(normal) for point in points]
    return {
        "records": records,
        "center": center,
        "normal": normal,
        "min_depth": min(depths),
        "max_depth": max(depths),
        "depth_span": max(depths) - min(depths),
        "extent": max((point - center).length for point in points),
        "source_face_count": len(records),
        "source_face_indices": [
            record["source_index"] for record in records if "source_index" in record
        ],
        "connected_components": 1,
        "normal_min_dot": min(record["normal"].dot(normal) for record in records),
        "adjacent_min_dot": 1.0,
        "max_adjacent_fold_degrees": 0.0,
    }


def _mirror_candidates(target, surface):
    """Build source-face and mirrored-face candidates without using eval indices.

    A Mirror modifier does not create separately selectable source BMesh faces.
    These candidates therefore come from the selected source face geometry and
    the modifier's actual reflection plane.  The viewport ray is still checked
    against evaluated geometry before a candidate can be confirmed.
    """

    mirrors = [
        (index, modifier)
        for index, modifier in enumerate(target.modifiers)
        if modifier.type == "MIRROR" and modifier.show_viewport
    ]
    if not mirrors:
        return None
    if len(mirrors) != 1:
        raise RuntimeError(
            "Mirror surface picking currently supports one visible Mirror modifier at a time."
        )

    mirror_index, mirror = mirrors[0]
    if any(
        modifier.show_viewport
        for modifier in target.modifiers[:mirror_index]
    ):
        raise RuntimeError(
            "A modifier before Mirror changes the source topology; mirror-side picking is not reliable for this stack."
        )
    if any(mirror.use_bisect_axis) or any(mirror.use_bisect_flip_axis):
        raise RuntimeError(
            "Mirror Bisect is not supported by surface picking yet; disable Bisect or apply that topology change first."
        )

    enabled_axes = [axis for axis, enabled in enumerate(mirror.use_axis) if enabled]
    if len(enabled_axes) != 1:
        raise RuntimeError(
            "Mirror surface picking currently supports one Mirror axis at a time."
        )

    axis = enabled_axes[0]
    reflection = Matrix.Identity(4)
    reflection[axis][axis] = -1.0
    if mirror.mirror_object is not None:
        mirror_space = mirror.mirror_object.matrix_world.copy()
    else:
        mirror_space = target.matrix_world.copy()
    try:
        reflection_world = mirror_space @ reflection @ mirror_space.inverted()
    except Exception as exc:
        raise RuntimeError("The Mirror plane could not be converted to world space.") from exc

    def make_candidate(label, transform):
        normal_matrix = transform.to_3x3().inverted().transposed()
        records = []
        for source_record in surface["records"]:
            points = [transform @ point for point in source_record["points"]]
            area, center = _polygon_area_and_center(points)
            normal = normal_matrix @ source_record["normal"]
            if normal.length <= SURFACE_TEXT_EPSILON:
                raise RuntimeError("The Mirror transform produced an unstable face normal.")
            normal.normalize()
            records.append(
                {
                    "source_index": source_record.get("source_index"),
                    "area": area,
                    "center": center,
                    "normal": normal,
                    "points": points,
                }
            )
        candidate_surface = _surface_from_records(records)
        candidate_surface["candidate_label"] = label
        candidate_surface["mirror_transform"] = transform.copy()
        candidate_surface["mirror_axis"] = axis
        candidate_surface["mirror_modifier_name"] = mirror.name
        candidate_surface["mirror_object_name"] = (
            mirror.mirror_object.name if mirror.mirror_object is not None else ""
        )
        candidate_surface["source_face_count"] = surface["source_face_count"]
        candidate_surface["source_face_indices"] = list(surface["source_face_indices"])
        candidate_surface["connected_components"] = surface["connected_components"]
        candidate_surface["normal_min_dot"] = surface["normal_min_dot"]
        candidate_surface["adjacent_min_dot"] = surface["adjacent_min_dot"]
        candidate_surface["max_adjacent_fold_degrees"] = surface["max_adjacent_fold_degrees"]
        return candidate_surface

    original = make_candidate("Original", Matrix.Identity(4))
    mirrored = make_candidate(f"Mirror {('XYZ'[axis])}", reflection_world)

    center_distance = (original["center"] - mirrored["center"]).length
    normal_alignment = abs(original["normal"].dot(mirrored["normal"]))
    overlap_tolerance = max(original["extent"] * 1.0e-5, 1.0e-6)
    if center_distance <= overlap_tolerance and normal_alignment > 1.0 - 1.0e-5:
        return None
    return [original, mirrored]


def _point_in_triangle(point, first, second, third, tolerance):
    edge_u = second - first
    edge_v = third - first
    point_vector = point - first
    dot_uu = edge_u.dot(edge_u)
    dot_uv = edge_u.dot(edge_v)
    dot_vv = edge_v.dot(edge_v)
    dot_up = edge_u.dot(point_vector)
    dot_vp = edge_v.dot(point_vector)
    denominator = dot_uu * dot_vv - dot_uv * dot_uv
    if abs(denominator) <= SURFACE_TEXT_EPSILON:
        return False
    barycentric_v = (dot_vv * dot_up - dot_uv * dot_vp) / denominator
    barycentric_w = (dot_uu * dot_vp - dot_uv * dot_up) / denominator
    return (
        barycentric_v >= -tolerance
        and barycentric_w >= -tolerance
        and barycentric_v + barycentric_w <= 1.0 + tolerance
    )


def _point_in_record(point, record, tolerance):
    points = record["points"]
    normal = record["normal"]
    plane_distance = (point - points[0]).dot(normal)
    projected = point - normal * plane_distance
    try:
        triangles = tessellate_polygon([points])
    except Exception:
        triangles = []
    if not triangles:
        triangles = [(0, index, index + 1) for index in range(1, len(points) - 1)]
    else:
        triangles = [
            tuple(index if isinstance(index, int) else points.index(index) for index in triangle)
            for triangle in triangles
        ]
    inside = any(
        _point_in_triangle(projected, points[first], points[second], points[third], tolerance)
        for first, second, third in triangles
    )
    return inside, abs(plane_distance)


def _viewport_target_hit(context, target, region, event):
    """Ray cast the actual evaluated target under the mouse in a VIEW_3D window."""

    if region is None or context.area is None or context.area.type != "VIEW_3D":
        return None
    rv3d = context.area.spaces.active.region_3d
    coordinate = (event.mouse_x - region.x, event.mouse_y - region.y)
    if not (0 <= coordinate[0] < region.width and 0 <= coordinate[1] < region.height):
        return None
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coordinate)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coordinate)
    if direction.length <= SURFACE_TEXT_EPSILON:
        return None
    direction.normalize()

    target_eval = target.evaluated_get(context.evaluated_depsgraph_get())
    try:
        inverse = target_eval.matrix_world.inverted()
        local_origin = inverse @ origin
        local_direction = inverse.to_3x3() @ direction
        if local_direction.length <= SURFACE_TEXT_EPSILON:
            return None
        local_direction.normalize()
        hit, local_location, local_normal, evaluated_index = target_eval.ray_cast(
            local_origin,
            local_direction,
            distance=1.0e7,
        )
    except Exception:
        return None
    if not hit:
        return None

    world_matrix = target_eval.matrix_world
    world_normal = world_matrix.to_3x3().inverted().transposed() @ local_normal
    if world_normal.length <= SURFACE_TEXT_EPSILON:
        return None
    world_normal.normalize()
    return world_matrix @ local_location, world_normal, evaluated_index


def _match_pick_to_candidate(hit_point, hit_normal, candidate, tolerance):
    matches = []
    for record in candidate["records"]:
        inside, plane_distance = _point_in_record(hit_point, record, tolerance)
        if not inside or plane_distance > tolerance:
            continue
        normal_dot = hit_normal.dot(record["normal"])
        if normal_dot < 0.25:
            continue
        matches.append((plane_distance, normal_dot))
    if not matches:
        return None
    plane_distance, normal_dot = min(matches, key=lambda item: item[0])
    return plane_distance + (1.0 - normal_dot) * tolerance


def _read_surface_request(context):
    if context.mode != "EDIT_MESH" or context.object is None or context.object.type != "MESH":
        raise RuntimeError("Active object must be a Mesh in Edit Mode.")

    edit_objects = list(getattr(context, "objects_in_mode", ()) or ())
    if len(edit_objects) > 1:
        raise RuntimeError("Multi-object Edit Mode is not supported; edit one Mesh at a time.")

    target = context.object
    bm = bmesh.from_edit_mesh(target.data)
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    selected_faces = [face for face in bm.faces if face.select]
    if not selected_faces:
        raise RuntimeError("Select a connected surface region before adding Surface Text.")
    if len(selected_faces) > SURFACE_TEXT_MAX_FACES:
        raise RuntimeError(
            f"Select no more than {SURFACE_TEXT_MAX_FACES} faces for one Surface Text region."
        )

    # This copies all required values before any mode switch.  No BMesh face
    # reference is stored in the request or used after this function returns.
    surface = _selected_surface_info(target, selected_faces)
    scene = context.scene
    clearance = _meters_to_scene_units(scene, SURFACE_TEXT_CLEARANCE_METERS)
    thickness = _meters_to_scene_units(scene, SURFACE_TEXT_THICKNESS_METERS)
    project_limit = _surface_project_limit(context, surface, clearance, thickness)
    mirror_candidates = _mirror_candidates(target, surface)
    if mirror_candidates is None:
        surface["candidate_label"] = "Original"
        surface["mirror_axis"] = -1
        surface["mirror_object_name"] = ""
    return {
        "target": target,
        "surface": surface,
        "clearance": clearance,
        "thickness": thickness,
        "project_limit": project_limit,
        "mirror_candidates": mirror_candidates,
    }


def _execute_surface_request(context, operator, state, request):
    text_obj = None
    text_data = None
    surface_obj = None
    surface_mesh = None
    try:
        target = request["target"]
        surface = request["surface"]
        bpy.ops.object.mode_set(mode="OBJECT")
        depsgraph = context.evaluated_depsgraph_get()
        ray_hit = _target_ray_hits(
            target,
            _surface_ray_starts(surface, request["clearance"]),
            -surface["normal"],
            request["project_limit"],
            depsgraph,
        )
        if ray_hit is False:
            raise RuntimeError(
                f"Shrinkwrap found no target surface within {request['project_limit']:.4g} scene units."
            )

        surface_obj, surface_mesh = _create_surface_mesh(context, target, surface)
        text_obj, text_data = _create_text_object(
            context,
            target,
            surface,
            request["clearance"],
            request["project_limit"],
        )
        _validate_evaluated_text(
            context,
            text_obj,
            request["thickness"],
            surface["center"],
            surface["normal"],
        )
        _annotate_text_object(context, text_obj, target, surface, surface_obj)
        _select_created_text(context, text_obj)
    except Exception as exc:
        _remove_created_text(text_obj, text_data)
        _remove_surface_mesh(surface_obj, surface_mesh)
        _restore_state(context, state)
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}

    operator.report({"INFO"}, "Created editable Surface Text with Shrinkwrap and Solidify.")
    return {"FINISHED"}


class RR_OT_add_surface_text(bpy.types.Operator):
    bl_idname = "rr_builder.add_surface_text"
    bl_label = "Add Surface Text"
    bl_description = "Create editable centered text on one connected selected surface region with a persistent sampling mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and context.object is not None and context.object.type == "MESH"

    def execute(self, context):
        state = _capture_state(context)
        try:
            request = _read_surface_request(context)
            if request["mirror_candidates"]:
                raise RuntimeError(
                    "This selected surface has a distinct Mirror side; invoke Add Surface Text from the 3D View and click the visible side."
                )
        except Exception as exc:
            _restore_state(context, state)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return _execute_surface_request(context, self, state, request)

    def _window_region(self):
        if self._view3d_area is None:
            return None
        return next(
            (region for region in self._view3d_area.regions if region.type == "WINDOW"),
            None,
        )

    def _set_header(self, context, message):
        try:
            if self._view3d_area is not None:
                self._view3d_area.header_text_set(message)
        except Exception:
            pass

    def _clear_header(self, context):
        try:
            if self._view3d_area is not None:
                self._view3d_area.header_text_set(None)
        except Exception:
            pass

    def _remove_preview(self):
        _remove_created_text(self._preview_obj, self._preview_data)
        self._preview_obj = None
        self._preview_data = None
        self._hover_candidate = None

    def _remove_modal_surface(self):
        _remove_surface_mesh(self._surface_obj, self._surface_mesh)
        self._surface_obj = None
        self._surface_mesh = None

    def _cancel_pick(self, context, message=None):
        self._remove_preview()
        self._remove_modal_surface()
        self._clear_header(context)
        _restore_state(context, self._state)
        if message:
            self.report({"INFO"}, message)
        return {"CANCELLED"}

    def _update_preview(self, context, candidate):
        self._hover_candidate = candidate
        if candidate is None:
            if self._preview_obj is not None:
                self._preview_obj.hide_viewport = True
            return

        request = self._request
        if self._preview_obj is None:
            self._preview_obj, self._preview_data = _create_text_object(
                context,
                request["target"],
                candidate,
                request["clearance"],
                _surface_project_limit(
                    context,
                    candidate,
                    request["clearance"],
                    request["thickness"],
                ),
            )
            self._preview_obj["rr_surface_text_preview"] = True
            self._preview_obj.hide_render = True
        else:
            self._preview_obj.hide_viewport = False
            _apply_text_pose(self._preview_obj, candidate, request["clearance"])
            shrinkwrap = next(
                (modifier for modifier in self._preview_obj.modifiers if modifier.type == "SHRINKWRAP"),
                None,
            )
            if shrinkwrap is not None:
                shrinkwrap.project_limit = _surface_project_limit(
                    context,
                    candidate,
                    request["clearance"],
                    request["thickness"],
                )
        context.view_layer.update()
        try:
            self._view3d_area.tag_redraw()
        except Exception:
            pass

    def _picked_candidate(self, context, event):
        region = self._window_region()
        hit = _viewport_target_hit(context, self._request["target"], region, event)
        if hit is None:
            return None
        hit_point, hit_normal, _evaluated_index = hit
        candidate_tolerance = max(
            self._request["surface"]["extent"] * 0.35,
            self._request["thickness"] * 2.0,
            self._request["clearance"] * 8.0,
            1.0e-5,
        )
        matches = []
        for candidate in self._request["mirror_candidates"]:
            score = _match_pick_to_candidate(
                hit_point,
                hit_normal,
                candidate,
                candidate_tolerance,
            )
            if score is not None:
                matches.append((score, candidate))
        if len(matches) != 1:
            return None
        return matches[0][1]

    def _finish_pick(self, context):
        candidate = self._hover_candidate
        if candidate is None:
            self.report({"INFO"}, "Point at the selected visible surface first; Esc cancels.")
            return {"RUNNING_MODAL"}

        request = self._request
        try:
            if self._preview_obj is None:
                self._update_preview(context, candidate)
            text_obj = self._preview_obj
            text_obj.hide_viewport = False
            if self._surface_obj is None:
                self._surface_obj, self._surface_mesh = _create_surface_mesh(
                    context,
                    request["target"],
                    candidate,
                )
            shrinkwrap = next(
                modifier for modifier in text_obj.modifiers if modifier.type == "SHRINKWRAP"
            )
            shrinkwrap.project_limit = _surface_project_limit(
                context,
                candidate,
                request["clearance"],
                request["thickness"],
            )
            ray_hit = _target_ray_hits(
                request["target"],
                _surface_ray_starts(candidate, request["clearance"]),
                -candidate["normal"],
                shrinkwrap.project_limit,
                context.evaluated_depsgraph_get(),
            )
            if ray_hit is False:
                raise RuntimeError("Shrinkwrap found no selected target surface within its finite projection limit.")
            _validate_evaluated_text(
                context,
                text_obj,
                request["thickness"],
                candidate["center"],
                candidate["normal"],
            )
            _annotate_text_object(
                context,
                text_obj,
                request["target"],
                candidate,
                self._surface_obj,
            )
            _select_created_text(context, text_obj)
        except Exception as exc:
            return self._cancel_pick(context, str(exc))

        self._preview_obj = None
        self._preview_data = None
        self._surface_obj = None
        self._surface_mesh = None
        self._clear_header(context)
        self.report({"INFO"}, f"Created Surface Text on {candidate['candidate_label']} side.")
        return {"FINISHED"}

    def invoke(self, context, event):
        self._state = _capture_state(context)
        self._preview_obj = None
        self._preview_data = None
        self._surface_obj = None
        self._surface_mesh = None
        self._hover_candidate = None
        self._view3d_area = context.area if context.area and context.area.type == "VIEW_3D" else None
        try:
            self._request = _read_surface_request(context)
            if not self._request["mirror_candidates"]:
                return _execute_surface_request(context, self, self._state, self._request)
            if self._view3d_area is None or self._window_region() is None:
                raise RuntimeError("Surface picking requires a visible 3D View area.")
            bpy.ops.object.mode_set(mode="OBJECT")
            self._set_header(
                context,
                "Surface Text: move into the 3D View, click the visible side; Esc cancels",
            )
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except Exception as exc:
            _restore_state(context, self._state)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            return self._cancel_pick(context, "Surface Text cancelled.")

        if event.type == "MOUSEMOVE":
            candidate = self._picked_candidate(context, event)
            if candidate is not self._hover_candidate:
                self._update_preview(context, candidate)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            return self._finish_pick(context)

        return {"RUNNING_MODAL"}
