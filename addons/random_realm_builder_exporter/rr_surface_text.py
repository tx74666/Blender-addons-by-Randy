"""Create editable text aligned to one or two selected mesh faces.

The operator deliberately keeps the text as a Font object.  Shrinkwrap and
Solidify remain native, editable modifiers so the user can change the text or
adjust the result in Blender's normal modifier panels.
"""

import math

import bmesh
import bpy
from bpy_extras import view3d_utils
from mathutils import Matrix, Vector
from mathutils.geometry import tessellate_polygon


SURFACE_TEXT_NAME = "Surface Text"
SURFACE_TEXT_BODY = "Text"
SURFACE_TEXT_CLEARANCE_METERS = 0.002
SURFACE_TEXT_THICKNESS_METERS = 0.1
SURFACE_TEXT_MAX_FACES = 2
SURFACE_TEXT_MIN_NORMAL_DOT = 0.2
SURFACE_TEXT_EPSILON = 1.0e-8


def _scene_scale_length(scene):
    unit_settings = getattr(scene, "unit_settings", None)
    scale_length = float(getattr(unit_settings, "scale_length", 1.0) or 1.0)
    return scale_length if abs(scale_length) > SURFACE_TEXT_EPSILON else 1.0


def _meters_to_scene_units(scene, meters):
    """Convert a real-world metre value to the scene's Blender units."""

    return float(meters) / _scene_scale_length(scene)


def _face_world_geometry(obj, face):
    """Return an area-weighted world centroid and normal for one BMFace.

    The values are copied out of edit-mode BMesh immediately.  No BMesh face
    reference escapes this function or survives a mode change.
    """

    matrix_world = obj.matrix_world.copy()
    normal_matrix = matrix_world.to_3x3().inverted().transposed()
    local_points = [vert.co.copy() for vert in face.verts]
    if len(local_points) < 3:
        raise ValueError("A selected face has fewer than three vertices.")

    try:
        triangles = tessellate_polygon([local_points])
    except Exception:
        triangles = []

    if not triangles:
        root = local_points[0]
        triangles = [
            (root, local_points[index], local_points[index + 1])
            for index in range(1, len(local_points) - 1)
        ]
    else:
        # Blender 5.x returns vertex indices here; older builds may return
        # the Vector values themselves.  Normalize both forms before the
        # world-space calculation below.
        triangles = [
            tuple(local_points[point] if isinstance(point, int) else point for point in triangle)
            for triangle in triangles
        ]

    total_area = 0.0
    centroid_sum = Vector((0.0, 0.0, 0.0))
    for triangle in triangles:
        first, second, third = (matrix_world @ point for point in triangle)
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
        "area": total_area,
        "center": centroid_sum / total_area,
        "normal": world_normal,
        "points": [matrix_world @ point for point in local_points],
    }


def _selected_surface_info(obj, selected_faces):
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
    if len(records) == 2 and records[0]["normal"].dot(records[1]["normal"]) < SURFACE_TEXT_MIN_NORMAL_DOT:
        raise ValueError("The selected face normals conflict too strongly; choose nearby faces.")

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
                    "area": area,
                    "center": center,
                    "normal": normal,
                    "points": points,
                }
            )
        candidate_surface = _surface_from_records(records)
        candidate_surface["candidate_label"] = label
        candidate_surface["mirror_transform"] = transform.copy()
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
        raise RuntimeError("Select one or two faces before adding Surface Text.")
    if len(selected_faces) > SURFACE_TEXT_MAX_FACES:
        raise RuntimeError("Select no more than two faces for Surface Text.")

    # This copies all required values before any mode switch.  No BMesh face
    # reference is stored in the request or used after this function returns.
    surface = _selected_surface_info(target, selected_faces)
    scene = context.scene
    clearance = _meters_to_scene_units(scene, SURFACE_TEXT_CLEARANCE_METERS)
    thickness = _meters_to_scene_units(scene, SURFACE_TEXT_THICKNESS_METERS)
    project_limit = _surface_project_limit(context, surface, clearance, thickness)
    mirror_candidates = _mirror_candidates(target, surface)
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
        _select_created_text(context, text_obj)
    except Exception as exc:
        _remove_created_text(text_obj, text_data)
        _restore_state(context, state)
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}

    operator.report({"INFO"}, "Created editable Surface Text with Shrinkwrap and Solidify.")
    return {"FINISHED"}


class RR_OT_add_surface_text(bpy.types.Operator):
    bl_idname = "rr_builder.add_surface_text"
    bl_label = "Add Surface Text"
    bl_description = "Create editable centered text on one or two selected mesh faces with Shrinkwrap and Solidify"
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

    def _cancel_pick(self, context, message=None):
        self._remove_preview()
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
            text_obj["rr_surface_text_preview"] = False
            _select_created_text(context, text_obj)
        except Exception as exc:
            return self._cancel_pick(context, str(exc))

        self._preview_obj = None
        self._preview_data = None
        self._clear_header(context)
        self.report({"INFO"}, f"Created Surface Text on {candidate['candidate_label']} side.")
        return {"FINISHED"}

    def invoke(self, context, event):
        self._state = _capture_state(context)
        self._preview_obj = None
        self._preview_data = None
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


