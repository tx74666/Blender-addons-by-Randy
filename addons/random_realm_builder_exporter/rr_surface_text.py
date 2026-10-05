"""Create editable text aligned to one connected selected surface region.

The operator deliberately keeps the text as a Font object.  Shrinkwrap and
Solidify remain native, editable modifiers so the user can change the text or
adjust the result in Blender's normal modifier panels.
"""

import hashlib
import math
import re
import uuid
from array import array
from contextlib import contextmanager

import bmesh
import bpy
from bpy_extras import view3d_utils
from mathutils import Matrix, Vector
from mathutils.geometry import tessellate_polygon

try:
    from .rr_surface_text_backlight import (
        RR_OT_configure_surface_text_backlight,
        configure_backlight,
        read_backlight_settings,
        validate_backlight_settings,
    )
except ImportError:
    from rr_surface_text_backlight import (
        RR_OT_configure_surface_text_backlight,
        configure_backlight,
        read_backlight_settings,
        validate_backlight_settings,
    )


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
SURFACE_SAMPLE_NAME_MAX_BYTES = 63
SURFACE_TEXT_ROLE = "surface_text"
SURFACE_TEXT_EXPORT_ROLE = "solid_geometry"
SURFACE_TEXT_EXPORT_PREFIX = "RR_SurfaceText_Geometry"
SURFACE_TEXT_FRAME_PREFIX = "RR_STFrame"


def is_surface_sampling_helper(obj):
    return (obj is not None and obj.type == "MESH"
            and obj.get("rr_surface_role") == "sampling_surface")


def configure_surface_sample_display(obj):
    """Keep the authored sampling mesh available as an unshaded wire overlay.

    WIRE alone does not hide a surface from Rendered viewport ray tracing.
    Disable renderer visibility without disabling evaluated geometry, selection
    or the user's per-view-layer eye state. Export aliases are separate objects
    and keep their normal geometry visibility.
    """
    if not is_surface_sampling_helper(obj) or not getattr(obj, "is_editable", True):
        return False
    settings = {
        "display_type": "WIRE",
        "show_wire": True,
        "show_all_edges": True,
        "hide_render": True,
        "visible_camera": False,
        "visible_diffuse": False,
        "visible_glossy": False,
        "visible_transmission": False,
        "visible_shadow": False,
        "visible_volume_scatter": False,
        "visible_raycast": False,
        "hide_probe_volume": True,
        "hide_probe_sphere": True,
        "hide_probe_plane": True,
    }
    changed = False
    for name, value in settings.items():
        # Renderer properties differ between supported Blender versions.
        if hasattr(obj, name) and getattr(obj, name) != value:
            setattr(obj, name, value)
            changed = True
    return changed


def repair_surface_sample_display(objects=None):
    """Repair only owned, editable helpers; preserve names, data and bindings."""
    changes = {"objects": [], "skipped": []}
    for obj in list(bpy.data.objects if objects is None else objects):
        if not is_surface_sampling_helper(obj):
            continue
        if not getattr(obj, "is_editable", True):
            changes["skipped"].append(obj.name)
        elif configure_surface_sample_display(obj):
            changes["objects"].append(obj.name)
    return changes


def _surface_text_id(source):
    identity = str(source.get("rr_surface_text_id", "") or "")
    peers = sorted((obj for obj in bpy.data.objects if obj.type == "FONT" and
                    obj.get("rr_surface_text_id") == identity),
                   key=lambda obj: (getattr(obj, "session_uid", 0), obj.name_full)) if identity else []
    if not identity or (peers and peers[0] is not source):
        if not getattr(source, "is_editable", True):
            raise RuntimeError("Make the Surface Text local before assigning its persistent identity.")
        identity = uuid.uuid4().hex
        source["rr_surface_text_id"] = identity
    return identity


def surface_text_frame_names(source):
    identity = _surface_text_id(source)[:24]
    return [f"{SURFACE_TEXT_FRAME_PREFIX}_{identity}_{axis}" for axis in "OXYZ"]


def _source_topology_fingerprint(mesh):
    return hashlib.sha256(repr([(tuple(poly.vertices)) for poly in mesh.polygons]).encode()).hexdigest()


def _evaluated_geometry_signature(obj, attribute_name):
    """Copy a geometry/tag digest before freeing the evaluated temporary mesh.

    Smooth shading, normals and other attributes may differ. Vertex coordinates,
    edge/face/loop identity and the injected selected-face mapping must not.
    """
    expected_visibility = [(modifier.name, bool(modifier.show_viewport)) for modifier in obj.modifiers]
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    try:
        evaluated_visibility = {modifier.name: bool(modifier.show_viewport) for modifier in evaluated.modifiers}
        if any(evaluated_visibility.get(name) != visible for name, visible in expected_visibility):
            raise RuntimeError("Surface Text cannot verify driven modifier visibility during its geometry check.")
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        tagged = mesh.attributes.get(attribute_name)
        if (tagged is None or tagged.domain != "FACE" or tagged.data_type != "INT"
                or len(tagged.data) != len(mesh.polygons)):
            raise RuntimeError("A Geometry Nodes modifier lost the selected-face mapping; apply and rebind the surface.")
        digest = hashlib.sha256()
        digest.update(repr((len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops))).encode())
        for collection, field, kind, count in (
            (mesh.vertices, "co", "f", len(mesh.vertices) * 3),
            (mesh.edges, "vertices", "i", len(mesh.edges) * 2),
            (mesh.loops, "vertex_index", "i", len(mesh.loops)),
            (mesh.loops, "edge_index", "i", len(mesh.loops)),
            (mesh.polygons, "loop_start", "i", len(mesh.polygons)),
            (mesh.polygons, "loop_total", "i", len(mesh.polygons)),
            (tagged.data, "value", "i", len(tagged.data)),
        ):
            values = array(kind, [0]) * count
            collection.foreach_get(field, values)
            digest.update(values.tobytes())
        return digest.digest()
    finally:
        evaluated.to_mesh_clear()


def _node_group_has_only_shading_geometry(node_tree, visiting=None):
    """Reject hidden non-mesh output that a Mesh-only digest cannot observe.

    Follow every linked input to the active output, including both switch
    branches. Field calculations are unrestricted; geometry-producing actions
    must be known pass-through/shading operations. Names provide no exemption.
    """
    if node_tree is None:
        return False
    visiting = set() if visiting is None else visiting
    key = node_tree.as_pointer()
    if key in visiting:
        return False
    visiting.add(key)
    safe_geometry = {"NodeGroupInput", "NodeReroute", "GeometryNodeSetShadeSmooth",
                     "GeometryNodeStoreNamedAttribute", "GeometryNodeSwitch",
                     "GeometryNodeMenuSwitch", "GeometryNodeRemoveAttribute"}
    try:
        pending = [node for node in node_tree.nodes
                   if node.type == "GROUP_OUTPUT" and node.is_active_output]
        if not pending:
            return False
        visited = set()
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            geometry_output = any(socket.type == "GEOMETRY" and socket.is_linked
                                  for socket in node.outputs)
            if geometry_output:
                if node.type == "GROUP":
                    if not _node_group_has_only_shading_geometry(node.node_tree, visiting):
                        return False
                elif node.bl_idname not in safe_geometry:
                    return False
            for socket in node.inputs:
                pending.extend(link.from_node for link in socket.links)
        return True
    finally:
        visiting.remove(key)


def _verify_geometry_preserving_nodes(clone, attribute_name):
    """Prove each active Nodes stage individually on the private clone.

    Checking only the final stack could miss two geometry changes that cancel
    out. Disable later stages, compare this stage on/off, and restore all clone
    switches even on failure. The authored modifier and node tree stay intact.
    """
    modifiers = list(clone.modifiers)
    visibility = [modifier.show_viewport for modifier in modifiers]
    if not any(modifier.type == "NODES" and visible for modifier, visible in zip(modifiers, visibility)):
        return
    try:
        for index, modifier in enumerate(modifiers):
            if modifier.type != "NODES" or not visibility[index]:
                continue
            if not _node_group_has_only_shading_geometry(modifier.node_group):
                raise RuntimeError(
                    f"Surface Text cannot verify modifier '{modifier.name}' (NODES): its node group can produce or change geometry; apply it and rebind the selected region."
                )
            for position, item in enumerate(modifiers):
                item.show_viewport = visibility[position] if position < index else False
            before = _evaluated_geometry_signature(clone, attribute_name)
            modifier.show_viewport = True
            after = _evaluated_geometry_signature(clone, attribute_name)
            if before != after:
                raise RuntimeError(
                    f"Surface Text cannot verify modifier '{modifier.name}' (NODES): it changes geometry or the selected-face mapping; apply it and rebind the selected region."
                )
    finally:
        for modifier, visible in zip(modifiers, visibility):
            modifier.show_viewport = visible
        bpy.context.view_layer.update()


@contextmanager
def _evaluated_sampling_mesh(source, verify_export_modifiers=True):
    """Evaluate tagged faces privately; authoring can check viewport connectivity.

    Export always keeps its stricter modifier/render-parity contract. The Add
    operator also verifies the visible joined seam without treating a shading
    modifier after Mirror as an unsupported authoring operation.
    """
    sample = _surface_text_sampling_surface(source)
    target = _surface_text_target(source)
    if sample is None or target is None or target.type != "MESH":
        raise RuntimeError("Surface Text has no selected sampling region; bind the existing text to selected faces.")
    indices = list(sample.get("rr_surface_source_face_indices", ()))
    if sample.get("rr_surface_role") != "sampling_surface" or not indices:
        raise RuntimeError("Surface Text has no selected sampling region; bind the existing text to selected faces.")
    expected_topology = str(sample.get("rr_surface_source_topology", ""))
    if not expected_topology or expected_topology != _source_topology_fingerprint(target.data):
        raise RuntimeError("Surface Text source topology changed or predates region validation; rebind its selected faces.")
    if min(indices) < 0 or max(indices) >= len(target.data.polygons):
        raise RuntimeError("Surface Text selected face indices are no longer valid; rebind its selected faces.")
    allowed = {"SUBSURF", "TRIANGULATE", "SIMPLE_DEFORM", "SMOOTH", "CORRECTIVE_SMOOTH",
               "LAPLACIANSMOOTH", "LATTICE", "ARMATURE", "SHRINKWRAP", "DISPLACE", "CAST",
                "WARP", "WAVE", "WEIGHTED_NORMAL", "NORMAL_EDIT", "MIRROR", "NODES"}
    active = [modifier for modifier in target.modifiers
              if modifier.show_viewport or (verify_export_modifiers and modifier.show_render)]
    for modifier in active:
        if verify_export_modifiers and (modifier.type not in allowed or modifier.show_viewport != modifier.show_render):
            raise RuntimeError(f"Surface Text cannot verify modifier '{modifier.name}' ({modifier.type}); apply it and rebind the selected region.")
    mirrors = [modifier for modifier in active if modifier.type == "MIRROR"]
    mirror_plane = None
    joined_mirror = bool(sample.get("rr_surface_mirror_joined", False))
    if joined_mirror:
        # A saved joined region is valid only while Mirror still welds a real
        # source boundary edge. Recheck topology/settings rather than exporting
        # two visually close but disconnected halves after a later edit.
        bm = bmesh.new()
        try:
            bm.from_mesh(target.data)
            bm.faces.ensure_lookup_table()
            bm.normal_update()
            surface = _selected_surface_info(target, [bm.faces[index] for index in indices])
            candidates = _mirror_candidates(target, surface)
            if _joined_mirror_surface(target, surface, candidates) is None:
                raise RuntimeError("The joined Surface Text region is no longer connected by Mirror Merge; rebind its selected faces.")
        finally:
            bm.free()
    if mirrors:
        mirror = mirrors[0]
        axes = [axis for axis, enabled in enumerate(mirror.use_axis) if enabled]
        if len(mirrors) != 1 or len(axes) != 1 or any(mirror.use_bisect_axis) or any(mirror.use_bisect_flip_axis):
            raise RuntimeError("Surface Text needs a single non-bisect Mirror axis or an applied/rebound surface.")
        if verify_export_modifiers and any(modifier.type not in {"MIRROR", "TRIANGULATE", "WEIGHTED_NORMAL", "NORMAL_EDIT", "NODES"} for modifier in active):
            raise RuntimeError("Surface Text cannot prove the selected Mirror side with this modifier stack; apply and rebind it.")
        plane_inverse = (mirror.mirror_object.matrix_world if mirror.mirror_object else target.matrix_world).inverted()
        axis = axes[0]
        if not joined_mirror:
            values = [(plane_inverse @ target.matrix_world @ target.data.vertices[index].co)[axis]
                      for face_index in indices for index in target.data.polygons[face_index].vertices]
            nonzero = [value for value in values if abs(value) > 1.0e-6]
            if not nonzero or min(nonzero) * max(nonzero) < 0:
                raise RuntimeError("The selected Surface Text region crosses the Mirror plane; select one unambiguous side.")
            sign = 1 if nonzero[0] > 0 else -1
            if str(sample.get("rr_surface_mirror_side", "Original")) != "Original":
                sign = -sign
            mirror_plane = (plane_inverse, axis, sign)
    clone = None
    clone_mesh = None
    evaluated = None
    try:
        clone_mesh = target.data.copy()
        attribute_name = "rr_text_region_" + uuid.uuid4().hex[:12]
        attribute = clone_mesh.attributes.new(attribute_name, "INT", "FACE")
        selected = set(indices)
        for polygon in clone_mesh.polygons:
            attribute.data[polygon.index].value = 1 if polygon.index in selected else 0
        clone = target.copy()
        clone.data = clone_mesh
        bpy.context.scene.collection.objects.link(clone)
        clone.hide_viewport = False
        clone.hide_set(False)
        if verify_export_modifiers:
            _verify_geometry_preserving_nodes(clone, attribute_name)
        bpy.context.view_layer.update()
        evaluated = clone.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=bpy.context.evaluated_depsgraph_get())
        tagged = mesh.attributes.get(attribute_name)
        if tagged is None or tagged.domain != "FACE" or len(tagged.data) != len(mesh.polygons):
            raise RuntimeError("The evaluated modifiers lost the selected-face mapping; apply and rebind the surface.")
        if joined_mirror:
            # Use evaluated edge identity, not coordinate proximity. Shape Keys
            # can open a seam even when the saved Basis still passes Merge.
            selected_faces = {polygon.index for polygon in mesh.polygons
                              if tagged.data[polygon.index].value == 1}
            edge_faces = {}
            for index in selected_faces:
                polygon = mesh.polygons[index]
                for loop_index in polygon.loop_indices:
                    edge_faces.setdefault(mesh.loops[loop_index].edge_index, []).append(index)
            pending = [next(iter(selected_faces))] if selected_faces else []
            visited = set(pending)
            while pending:
                polygon = mesh.polygons[pending.pop()]
                for loop_index in polygon.loop_indices:
                    for linked in edge_faces[mesh.loops[loop_index].edge_index]:
                        if linked not in visited:
                            visited.add(linked)
                            pending.append(linked)
            if not selected_faces or len(visited) != len(selected_faces):
                raise RuntimeError("The evaluated joined Surface Text region is disconnected; restore Mirror Merge or rebind its selected faces.")
        inverse_sample = sample.matrix_world.inverted()
        vertices, faces = [], []
        for polygon in mesh.polygons:
            if tagged.data[polygon.index].value != 1:
                continue
            points_world = [evaluated.matrix_world @ mesh.vertices[index].co for index in polygon.vertices]
            if mirror_plane:
                plane_inverse, axis, sign = mirror_plane
                side = [(plane_inverse @ point)[axis] * sign for point in points_world]
                if max(side) <= 1.0e-6:
                    continue
                if min(side) < -1.0e-6:
                    raise RuntimeError("An evaluated Surface Text face crosses the chosen Mirror side.")
            first = len(vertices)
            vertices.extend(tuple(inverse_sample @ point) for point in points_world)
            faces.append(tuple(range(first, len(vertices))))
        if not faces or any(not math.isfinite(component) for point in vertices for component in point):
            raise RuntimeError("The evaluated selected Surface Text region is empty or non-finite.")
        yield vertices, faces
    finally:
        if evaluated is not None:
            evaluated.to_mesh_clear()
        if clone is not None:
            bpy.data.objects.remove(clone, do_unlink=True)
        if clone_mesh is not None and clone_mesh.users == 0:
            bpy.data.meshes.remove(clone_mesh)


def _editable_surface_descriptor(source, sample):
    if sample is None or sample.get("rr_surface_role") != "sampling_surface":
        return {"editableVersion": 0, "editableError": "Bind this existing Font to a selected surface region first."}
    if not sample.get("rr_surface_source_topology"):
        return {"editableVersion": 0, "editableError": "Rebind this older selected region to validate evaluated geometry."}
    with _evaluated_sampling_mesh(source) as (vertices, _faces):
        points = [Vector(point) for point in vertices]
    sample_inverse = sample.matrix_world.inverted()
    source_basis = sample_inverse.to_3x3() @ source.matrix_world.to_3x3()
    right, up, depth = (source_basis.col[index].normalized() for index in range(3))
    basis = Matrix((right, up, depth)).transposed()
    if abs(basis.determinant()) < SURFACE_TEXT_EPSILON:
        raise RuntimeError("The authored Surface Text frame is degenerate.")
    # Tangents and the depth axis are vectors; the projection normal is a
    # covector. They differ when the authored sampling transform is nonuniform.
    normal = source_basis.inverted().transposed().col[2].normalized()
    coordinates = [basis.inverted() @ point for point in points]
    low = Vector(tuple(min(point[axis] for point in coordinates) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in coordinates) for axis in range(3)))
    scene_scale = _scene_scale_length(bpy.context.scene)
    shrinkwrap, solidify = _surface_text_modifier_pair(source)
    thickness = float(solidify.thickness) if solidify else 0.0
    thickness_sign = 1.0 if thickness > 0.0 else -1.0 if thickness < 0.0 else 0.0
    # Solidify's two offsets are t * (offset +/- 1) / 2. Flip Normals
    # swaps shell winding, not these positions, so it does not change the split.
    solid_front_fraction = max(0.0, min(1.0,
        (1.0 + thickness_sign * float(solidify.offset)) * 0.5)) if solidify else 0.0
    surface_offset_meters = float(shrinkwrap.offset) * scene_scale if shrinkwrap else 0.0
    if "rr_surface_backlight_enabled" in source:
        # Opt-in backlight and its restored disabled state use a world-space
        # gap. Native Shrinkwrap offset remains in the Font's local units.
        surface_offset_meters *= source.matrix_world.to_3x3().col[2].length
    material = source.active_material
    descriptor = {
        "editableVersion": 1, "textId": _surface_text_id(source), "text": source.data.body,
        "fontName": source.data.font.name if source.data.font else "",
        "fontSizeMeters": float(source.data.size) * source.matrix_world.to_3x3().col[0].length * scene_scale,
        "characterSpacing": float(source.data.space_character), "lineSpacing": float(source.data.space_line),
        "alignment": str(source.data.align_x), "verticalAlignment": str(source.data.align_y),
        "materialName": material.name if material else "",
        "surfaceOffsetMeters": surface_offset_meters,
        "solidFrontFraction": solid_front_fraction,
        "regionCenter": _vector_values(basis @ ((low + high) * 0.5)),
        "regionSize": [float(high.x - low.x), float(high.y - low.y)],
        "readDirection": _vector_values(right), "upDirection": _vector_values(up),
        "surfaceNormal": _vector_values(normal),
        "textCenter": _vector_values(sample_inverse @ source.matrix_world.translation),
        "coordinateSpace": "sampling-local-blender", "sceneUnitMeters": scene_scale,
        "frameObjectNames": surface_text_frame_names(source),
    }
    if "rr_surface_backlight_enabled" in source:
        descriptor["backlight"] = validate_backlight_settings(source, bpy.context.scene)
    return descriptor


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
    adjacent_pairs = []
    for face in selected_faces:
        for edge in face.edges:
            for linked_face in edge.link_faces:
                if linked_face in selected_set and face.index < linked_face.index:
                    adjacent_pairs.append((int(face.index), int(linked_face.index)))
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
        "boundary_edges": [
            {"source_index": int(face.index),
             "points": [obj.matrix_world @ vert.co for vert in edge.verts]}
            for face in selected_faces for edge in face.edges
            if sum(linked in selected_set for linked in edge.link_faces) == 1
        ],
        "adjacent_pairs": adjacent_pairs,
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


def _utf8_name_prefix(value, maximum_bytes):
    return str(value).encode("utf-8")[:max(0, maximum_bytes)].decode("utf-8", "ignore")


def _surface_sample_display_name(prefix, target_name, side, sequence):
    """Keep the readable region/sequence suffix when a target name is long."""

    target_label = re.sub(r"[\x00-\x1f\x7f]+", " ", str(target_name)).strip() or "Object"
    side_label = re.sub(r"[\x00-\x1f\x7f]+", " ", str(side)).strip() or "Original"
    # Region labels include Original, Mirror X/Y/Z and Joined Mirror X/Y/Z. Bound unexpected
    # custom metadata too, while always leaving room for a target and number.
    side_budget = SURFACE_SAMPLE_NAME_MAX_BYTES - len(prefix.encode("utf-8")) - len(f"___{sequence:02d}".encode("utf-8")) - 1
    side_label = _utf8_name_prefix(side_label, side_budget)
    suffix = f"_{side_label}_{sequence:02d}"
    target_budget = SURFACE_SAMPLE_NAME_MAX_BYTES - len((prefix + "_" + suffix).encode("utf-8"))
    target_label = _utf8_name_prefix(target_label, target_budget) or "O"
    return f"{prefix}_{target_label}{suffix}"


def _surface_sample_display_names(target_name, side, sample=None, mesh=None):
    """Allocate a readable, matching sequence for the object and its mesh."""

    sequence = 1
    while True:
        object_name = _surface_sample_display_name(SURFACE_SAMPLE_PREFIX, target_name, side, sequence)
        mesh_name = _surface_sample_display_name(SURFACE_SAMPLE_PREFIX + "Mesh", target_name, side, sequence)
        object_owner = bpy.data.objects.get(object_name)
        mesh_owner = bpy.data.meshes.get(mesh_name)
        if (object_owner is None or object_owner is sample) and (mesh_owner is None or mesh_owner is mesh):
            return object_name, mesh_name
        sequence += 1


def _is_legacy_surface_sample_name(name, prefix, identity):
    """Recognize only this add-on's original generated name and ID suffix."""

    if not re.fullmatch(r".+_[0-9a-f]{10}", str(identity)):
        return False
    expected = f"{prefix}_{identity}"
    if name == expected:
        return True
    suffix_match = re.search(r"\.\d{3,}$", name)
    suffix = suffix_match.group(0) if suffix_match is not None else ""
    base = name[:-len(suffix)] if suffix else name
    if suffix and base == expected:
        return True
    # Blender's legacy ID limit includes its terminating null byte. The old
    # names are ASCII, so native creation and duplicate truncation is exact.
    maximum_bytes = 63
    if len(expected.encode("utf-8")) > maximum_bytes - len(suffix):
        return name == _utf8_name_prefix(expected, maximum_bytes - len(suffix)) + suffix
    return False


def migrate_surface_sample_display_names(objects=None):
    """Explicitly rename legacy managed display names without changing IDs.

    Preserve custom names and linked/read-only datablocks. Resolve legacy
    name-only Font references before renaming, then synchronize those strings
    and any verified transient alias ownership metadata. No load handler calls
    this migration; the caller decides when to update and save an authored file.
    """

    candidates = list(bpy.data.objects if objects is None else objects)
    all_objects = list(bpy.data.objects)
    changes = {"objects": [], "meshes": [], "skipped": []}
    for sample in sorted(candidates, key=lambda obj: obj.name_full):
        if sample.type != "MESH" or sample.get("rr_surface_role") != "sampling_surface":
            continue
        identity = str(sample.get("rr_surface_identity", "") or "")
        mesh = sample.data
        rename_object = _is_legacy_surface_sample_name(sample.name, SURFACE_SAMPLE_PREFIX, identity)
        rename_mesh = (mesh is not None and mesh.get("rr_surface_role") == "sampling_surface_mesh"
                       and mesh.get("rr_surface_identity") == identity
                       and _is_legacy_surface_sample_name(mesh.name, SURFACE_SAMPLE_PREFIX + "Mesh", identity))
        if not rename_object and not rename_mesh:
            continue
        if not getattr(sample, "is_editable", True) or (rename_mesh and not getattr(mesh, "is_editable", True)):
            changes["skipped"].append({"name": sample.name, "reason": "read-only datablock"})
            continue
        sources = [obj for obj in all_objects if obj.type == "FONT" and _surface_text_sampling_surface(obj) is sample]
        aliases = [obj for obj in all_objects if _is_stale_sampling_alias(obj, sample)]
        if any(not getattr(obj, "is_editable", True) for obj in sources + aliases):
            changes["skipped"].append({"name": sample.name, "reason": "read-only reference"})
            continue
        target = next((_surface_text_target(source) for source in sources if _surface_text_target(source) is not None), None)
        target = target or sample.parent or bpy.data.objects.get(str(sample.get("rr_surface_source_object", "")))
        if target is None:
            changes["skipped"].append({"name": sample.name, "reason": "source target unavailable"})
            continue
        side = str(sample.get("rr_surface_mirror_side", "Original"))
        object_name, mesh_name = _surface_sample_display_names(target.name, side, sample, mesh)
        for source in sources:
            source["rr_surface_text_surface_ref"] = sample
        if rename_object:
            previous_name = sample.name
            sample.name = object_name
            changes["objects"].append({"old": previous_name, "new": sample.name})
            for alias in aliases:
                alias["rr_surface_source_object"] = sample.name
        if rename_mesh and mesh.users == 1:
            previous_name = mesh.name
            mesh.name = mesh_name
            changes["meshes"].append({"old": previous_name, "new": mesh.name})
        elif rename_mesh:
            changes["skipped"].append({"name": mesh.name, "reason": "shared mesh"})
        for source in sources:
            _sync_surface_text_references(source, _surface_text_target(source) or target)
    return changes


def _legacy_sampling_export_identity(surface_obj):
    identity = str(surface_obj.get("rr_surface_identity", "") or "")
    if not identity:
        identity = hashlib.sha1(surface_obj.name_full.encode("utf-8")).hexdigest()[:12]
    identity = _safe_name(identity)
    if len(identity) > 24:
        identity = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return identity


def surface_sampling_export_name(surface_obj):
    """Return a short FBX-safe alias for an authored sampling mesh.

    Blender's FBX importer rewrites object names longer than 63 characters,
    which breaks manifest pairing even though the source name is stable.  A
    New sampling objects have their own persistent export ID. Older files keep
    their existing alias where possible; colliding region IDs are migrated
    together so manifest creation and FBX creation always agree.
    """

    if surface_obj is None:
        return ""
    def stored_or_legacy_id(obj):
        return str(obj.get("rr_surface_export_id", "") or _legacy_sampling_export_identity(obj))

    original_identity = stored_or_legacy_id(surface_obj)
    sampling_objects = [
        obj for obj in bpy.data.objects
        if obj.get("rr_surface_role") == "sampling_surface"
    ]
    peers = sorted(
        (obj for obj in sampling_objects if stored_or_legacy_id(obj) == original_identity),
        # Native duplicate/Append copies custom IDs. Prefer the older local
        # datablock when deciding which object retains a conflicting alias.
        key=lambda obj: (getattr(obj, "session_uid", 0), obj.name_full),
    )
    if surface_obj not in peers:
        peers.append(surface_obj)
    claimed = {stored_or_legacy_id(obj) for obj in sampling_objects if obj not in peers}
    identity = original_identity
    for peer in peers:
        candidate = original_identity
        salt = 0
        while candidate in claimed:
            candidate = hashlib.sha1(
                repr((original_identity, peer.name_full, salt)).encode("utf-8")
            ).hexdigest()[:16]
            salt += 1
        claimed.add(candidate)
        if getattr(peer, "is_editable", True):
            peer["rr_surface_export_id"] = candidate
        if peer is surface_obj:
            identity = candidate
    return f"{SURFACE_SAMPLE_EXPORT_PREFIX}_{identity}"


@contextmanager
def _transient_mesh_resources():
    """Own partial construction until a complete list can be handed to export."""

    objects, meshes = [], []
    try:
        yield objects, meshes
    except BaseException:
        for obj in reversed(objects):
            try:
                if bpy.data.objects.get(obj.name) is obj:
                    bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass
        for mesh in reversed(meshes):
            try:
                if mesh.users == 0 and bpy.data.meshes.get(mesh.name) is mesh:
                    bpy.data.meshes.remove(mesh)
            except ReferenceError:
                pass
        raise


def _is_stale_sampling_alias(alias, surface_obj):
    return (
        alias is not None
        and alias.type == "MESH"
        and alias.get("rr_surface_role") == "sampling_surface_export_alias"
        and alias.get("rr_surface_source_object") == surface_obj.name
        and alias.get("rr_surface_identity") == surface_obj.get("rr_surface_identity")
    )


def cleanup_stale_surface_text_sampling_aliases(root):
    """Reclaim old leaked aliases before the caller captures scene state."""

    for source in find_surface_text_objects_for_export(root):
        surface_obj = _surface_text_sampling_surface(source)
        if surface_obj is None or surface_obj.get("rr_surface_role") != "sampling_surface":
            continue
        alias = bpy.data.objects.get(surface_sampling_export_name(surface_obj))
        if _is_stale_sampling_alias(alias, surface_obj):
            _remove_surface_mesh(alias, alias.data)


def create_surface_text_sampling_export_aliases(root):
    """Create transient short-name sampling meshes for FBX export.

    Authored sampling meshes remain hidden and source-linked.  The aliases are
    exported beside them only for the duration of the FBX bake, then removed
    by the caller.  Unity pairs them through the manifest field
    ``samplingSurfaceExportObjectName``.
    """

    if root is None:
        return []

    with _transient_mesh_resources() as (aliases, meshes):
        aliases_by_source = {}
        collection = next(iter(root.users_collection), None) or bpy.context.scene.collection
        for source in find_surface_text_objects_for_export(root):
            surface_obj = _surface_text_sampling_surface(source)
            if surface_obj is None or surface_obj.type != "MESH":
                continue
            if surface_obj.get("rr_surface_role") != "sampling_surface":
                continue
            if surface_obj in aliases_by_source:
                continue

            alias_name = surface_sampling_export_name(surface_obj)
            existing_alias = bpy.data.objects.get(alias_name) if alias_name else None
            if existing_alias is surface_obj:
                raise RuntimeError(
                    f"The sampling helper '{surface_obj.name}' uses its reserved export name. "
                    "Rename the helper before exporting."
                )
            if _is_stale_sampling_alias(existing_alias, surface_obj):
                # Releases before transactional cleanup left these disposable
                # aliases in the scene. Reclaim only an exact source match.
                _remove_surface_mesh(existing_alias, existing_alias.data)
                existing_alias = None
            if existing_alias is not None:
                raise RuntimeError(
                    f"Surface Text sampling export alias '{alias_name}' is already used by "
                    f"'{existing_alias.name_full}'."
                )

            if surface_obj.get("rr_surface_source_topology"):
                with _evaluated_sampling_mesh(source) as (vertices, faces):
                    mesh = bpy.data.meshes.new(alias_name + "Mesh")
                    meshes.append(mesh)
                    mesh.from_pydata(vertices, [], faces)
                    mesh.update()
            else:
                mesh = surface_obj.data.copy()
                meshes.append(mesh)
            # A top-level, translation-only alias is representable by FBX TRS
            # even when the source inherits nonuniform scale or shear. Bake
            # only this disposable mesh; keep the persistent sampling object.
            alias_world = Matrix.Translation(surface_obj.matrix_world.translation)
            source_to_alias = alias_world.inverted() @ surface_obj.matrix_world
            determinant = source_to_alias.to_3x3().determinant()
            if not math.isfinite(determinant) or abs(determinant) <= 1.0e-12:
                raise RuntimeError("The Surface Text sampling transform is degenerate.")
            mesh.transform(source_to_alias)
            if determinant < 0.0:
                mesh.flip_normals()
            mesh.update()
            alias = bpy.data.objects.new(alias_name, mesh)
            aliases.append(alias)
            collection.objects.link(alias)
            alias.matrix_world = alias_world
            alias.hide_render = False
            alias.hide_viewport = False
            alias.hide_select = False
            alias.display_type = "TEXTURED"
            alias["rr_surface_role"] = "sampling_surface_export_alias"
            alias["rr_surface_source_object"] = surface_obj.name
            alias["rr_surface_identity"] = str(surface_obj.get("rr_surface_identity", ""))
            aliases_by_source[surface_obj] = alias
        return aliases


def create_surface_text_frame_export_aliases(root):
    with _transient_mesh_resources() as (objects, _meshes):
        collection = next(iter(root.users_collection), None) or bpy.context.scene.collection
        for source in find_surface_text_objects_for_export(root):
            sample = _surface_text_sampling_surface(source)
            if sample is None or not sample.get("rr_surface_source_topology"):
                continue
            alias = bpy.data.objects.get(surface_sampling_export_name(sample))
            if not _is_stale_sampling_alias(alias, sample):
                raise RuntimeError("Create the validated sampling alias before its Surface Text frame markers.")
            _create_frame_markers(source, sample, alias, collection, objects)
        return objects


def _create_frame_markers(source, sample, alias, collection, objects):
    source_to_alias = alias.matrix_world.inverted() @ sample.matrix_world
    for name, point in zip(surface_text_frame_names(source),
                           ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))):
        if bpy.data.objects.get(name) is not None:
            raise RuntimeError(f"Surface Text frame marker name is already used: '{name}'.")
        marker = bpy.data.objects.new(name, None)
        objects.append(marker)
        collection.objects.link(marker)
        marker.parent = alias
        marker.matrix_parent_inverse = Matrix.Identity(4)
        marker.location = source_to_alias @ Vector(point)
        marker["rr_surface_role"] = "sampling_frame_export_alias"


def _create_surface_mesh(context, target, surface):
    """Create the persistent, source-linked mesh used as a Unity sampling surface."""

    identity = _surface_identity(target, surface)
    object_name, mesh_name = _surface_sample_display_names(
        target.name, str(surface.get("candidate_label", "Original"))
    )
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

    right, up, normal = _text_axes(surface["normal"])
    source_matrix = target.matrix_world.copy()
    source_face_indices = [int(index) for index in surface["source_face_indices"]]
    surface_obj["rr_surface_role"] = "sampling_surface"
    configure_surface_sample_display(surface_obj)
    surface_obj["rr_surface_identity"] = identity
    surface_obj["rr_surface_export_id"] = uuid.uuid4().hex[:16]
    surface_obj["rr_surface_source_object"] = target.name
    surface_obj["rr_surface_source_object_full_name"] = target.name_full
    surface_obj["rr_surface_source_face_count"] = int(surface["source_face_count"])
    surface_obj["rr_surface_source_face_indices"] = source_face_indices
    surface_obj["rr_surface_source_topology"] = _source_topology_fingerprint(target.data)
    surface_obj["rr_surface_connected_components"] = int(surface["connected_components"])
    surface_obj["rr_surface_mirror_side"] = str(surface.get("candidate_label", "Original"))
    surface_obj["rr_surface_mirror_joined"] = bool(surface.get("mirror_joined", False))
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
    mesh["rr_surface_mirror_joined"] = bool(surface.get("mirror_joined", False))
    mesh["rr_surface_face_indices"] = source_face_indices
    return surface_obj, mesh


def _annotate_text_object(context, text_obj, target, surface, surface_obj):
    right, up, normal = _text_axes(surface["normal"])
    text_obj["rr_surface_text_role"] = "surface_text"
    text_obj["rr_surface_text_target_ref"] = target
    text_obj["rr_surface_text_surface_ref"] = surface_obj
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
    text_obj["rr_surface_text_mirror_joined"] = bool(surface.get("mirror_joined", False))
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


def _copy_binding_property(value):
    """Own rollback values; IDProperty arrays/groups are borrowed Blender views."""
    if isinstance(value, bpy.types.ID):
        return value
    if hasattr(value, "items"):
        return {key: _copy_binding_property(item) for key, item in value.items()}
    if hasattr(value, "to_list"):
        return [_copy_binding_property(item) for item in value.to_list()]
    if isinstance(value, (tuple, list)):
        return [_copy_binding_property(item) for item in value]
    return value


def bind_existing_surface_text(context, source, target, surface):
    """Explicitly rebind selected faces, preserving the authored Font and its pose/settings."""
    if source is None or source.type != "FONT":
        raise RuntimeError("Choose an existing editable Font object.")
    if not getattr(source, "is_editable", True):
        raise RuntimeError("Make the existing Font local and editable before binding.")
    shrinkwrap, solidify = _surface_text_modifier_pair(source)
    if shrinkwrap is None or solidify is None:
        raise RuntimeError("The existing Font needs Shrinkwrap and Solidify modifiers before binding.")
    previous_properties = {key: _copy_binding_property(value) for key, value in source.items()}
    previous_target = shrinkwrap.target
    sample = mesh = None
    try:
        sample, mesh = _create_surface_mesh(context, target, surface)
        shrinkwrap.target = target
        _annotate_text_object(context, source, target, surface, sample)
        source["rr_surface_text_thickness_meters"] = abs(float(solidify.thickness)) * _scene_scale_length(context.scene)
        with _evaluated_sampling_mesh(source):
            pass
        return sample
    except BaseException:
        try:
            shrinkwrap.target = previous_target
            for key in list(source.keys()):
                if key not in previous_properties:
                    del source[key]
            for key, value in previous_properties.items():
                source[key] = value
        finally:
            _remove_surface_mesh(sample, mesh)
        raise


def _surface_text_target(source):
    shrinkwrap, _solidify = _surface_text_modifier_pair(source)
    target = getattr(shrinkwrap, "target", None)
    if target is not None:
        return target
    target = source.get("rr_surface_text_target_ref")
    if isinstance(target, bpy.types.Object):
        return target
    return bpy.data.objects.get(str(source.get("rr_surface_text_source_target", "")))


def _surface_text_sampling_surface(source):
    surface = source.get("rr_surface_text_surface_ref")
    if isinstance(surface, bpy.types.Object):
        return surface
    surface = bpy.data.objects.get(str(source.get("rr_surface_text_surface_object", "")))
    if surface is None and source.get("rr_surface_text_legacy_migrated"):
        return _surface_text_target(source)
    return surface


def _sync_surface_text_references(source, target):
    """Keep legacy name consumers in sync with Blender's durable ID links."""

    if not getattr(source, "is_editable", True):
        return
    source["rr_surface_text_target_ref"] = target
    source["rr_surface_text_source_target"] = target.name
    source["rr_surface_text_source_target_full_name"] = target.name_full
    surface = _surface_text_sampling_surface(source)
    if surface is not None:
        source["rr_surface_text_surface_ref"] = surface
        source["rr_surface_text_surface_object"] = surface.name
        source["rr_surface_text_surface_mesh"] = getattr(surface.data, "name", "")


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
        target = _surface_text_target(candidate)
        if target in export_targets:
            _sync_surface_text_references(candidate, target)
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
        authored_thickness = (abs(float(solidify.thickness)) * scene_scale *
                              source.matrix_world.to_3x3().col[2].length) if solidify else source.get("rr_surface_text_thickness_meters")
        try:
            thickness_meters = abs(float(authored_thickness))
        except (TypeError, ValueError):
            thickness_meters = 0.0
        if thickness_meters <= SURFACE_TEXT_EPSILON and solidify is not None:
            thickness_meters = abs(float(solidify.thickness)) * scene_scale

        target = _surface_text_target(source)
        sampling_surface = _surface_text_sampling_surface(source)
        sampling_surface_name = sampling_surface.name if sampling_surface is not None else ""
        sampling_export_name = ""
        if (
            sampling_surface is not None
            and sampling_surface.type == "MESH"
            and sampling_surface.get("rr_surface_role") == "sampling_surface"
        ):
            sampling_export_name = surface_sampling_export_name(sampling_surface)

        descriptor = {
                "fontObjectName": source.name,
                "fontObjectFullName": source.name_full,
                "targetObjectName": target.name if target is not None else "",
                "targetObjectFullName": target.name_full if target is not None else "",
                "samplingSurfaceObjectName": sampling_surface_name,
                "samplingSurfaceExportObjectName": sampling_export_name,
                "samplingSurfaceMeshName": getattr(getattr(sampling_surface, "data", None), "name", ""),
                "exportObjectName": f"{SURFACE_TEXT_EXPORT_PREFIX}_{_safe_name(source.name)}",
                "thicknessMeters": round(thickness_meters, 6),
            }
        descriptor.update(_editable_surface_descriptor(source, sampling_surface))
        descriptors.append(descriptor)
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

    collection = next(iter(root.users_collection), None) or bpy.context.scene.collection

    with _transient_mesh_resources() as (export_objects, meshes):
        for source in find_surface_text_objects_for_export(root):
            evaluated = source.evaluated_get(depsgraph)
            mesh = None
            try:
                mesh = bpy.data.meshes.new_from_object(
                    evaluated,
                    depsgraph=depsgraph,
                    preserve_all_data_layers=True,
                )
                if mesh is not None:
                    meshes.append(mesh)
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
            export_objects.append(export_obj)
            collection.objects.link(export_obj)
            source_target = _surface_text_target(source)
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


def _joined_mirror_surface(target, surface, candidates):
    """Join only a boundary edge actually welded by the supported Mirror.

    Blender compares each source vertex with its reflected partner in target
    local coordinates, using a strict distance < merge_threshold, then moves
    both to their midpoint. Clipping and the edit cage do not establish a weld.
    """

    if not candidates or len(candidates) != 2:
        return None
    mirror = target.modifiers.get(candidates[1]["mirror_modifier_name"])
    if mirror is None or not mirror.use_mirror_merge:
        return None
    threshold = float(mirror.merge_threshold)
    if not math.isfinite(threshold) or threshold <= 0.0:
        return None
    reflection = candidates[1]["mirror_transform"]
    try:
        target_inverse = target.matrix_world.inverted()
        plane_inverse = (mirror.mirror_object.matrix_world if mirror.mirror_object
                         else target.matrix_world).inverted()
    except Exception as exc:
        raise RuntimeError("The joined Mirror surface has a non-invertible transform.") from exc
    axis = candidates[1]["mirror_axis"]

    def vertex_pair(point):
        reflected = reflection @ point
        local_distance = ((target_inverse @ point) - (target_inverse @ reflected)).length
        merged = local_distance < threshold
        if merged:
            midpoint = (point + reflected) * 0.5
            return midpoint, midpoint.copy(), True
        return point.copy(), reflected, False

    # The source must occupy one side. Faces entirely collapsed onto the plane
    # can become duplicates; do not claim their reflected copies are a region.
    sides = []
    for record in surface["records"]:
        nonmerged = [point for point in record["points"] if not vertex_pair(point)[2]]
        if not nonmerged:
            return None
        sides.extend((plane_inverse @ point)[axis] for point in nonmerged)
    if not sides or min(sides) * max(sides) <= 0.0:
        return None

    seam_faces = set()
    for edge in surface.get("boundary_edges", ()):
        first, second = [vertex_pair(point) for point in edge["points"]]
        if (first[2] and second[2]
                and ((target_inverse @ first[0]) - (target_inverse @ second[0])).length
                > SURFACE_TEXT_EPSILON):
            seam_faces.add(edge["source_index"])
    if not seam_faces:
        return None

    normal_matrix = reflection.to_3x3().inverted().transposed()

    def welded_record(source_record, points, expected_normal):
        # Reflection reverses winding. Rebuild the geometric normal after
        # midpoint welding, and keep the sample's winding facing outward.
        local_points = [target_inverse @ point for point in points]
        normal_sum = Vector((0.0, 0.0, 0.0))
        for first, second, third in _face_triangles(local_points):
            normal_sum += ((local_points[second] - local_points[first])
                           .cross(local_points[third] - local_points[first]))
        normal_sum = target_inverse.to_3x3().transposed() @ normal_sum
        if normal_sum.length <= SURFACE_TEXT_EPSILON:
            raise ValueError("Mirror Merge collapses a selected face; choose a non-degenerate region.")
        if normal_sum.dot(expected_normal) < 0.0:
            points = list(reversed(points))
            normal_sum.negate()
        normal = normal_sum.normalized()
        _validate_face_shape(points, normal)
        area, center = _polygon_area_and_center(points)
        return {"source_index": source_record["source_index"], "points": points,
                "area": area, "center": center, "normal": normal}

    originals, reflected_records = [], []
    for record in surface["records"]:
        pairs = [vertex_pair(point) for point in record["points"]]
        originals.append(welded_record(record, [pair[0] for pair in pairs], record["normal"]))
        reflected_records.append(welded_record(
            record, [pair[1] for pair in pairs], normal_matrix @ record["normal"]))
    try:
        joined = _surface_from_records(originals + reflected_records)
    except ValueError:
        # The two halves can each be a valid surface while their combined
        # normals cannot define one text frame. Keep the existing side picker.
        return None
    if joined["normal_min_dot"] < SURFACE_TEXT_MIN_NORMAL_DOT:
        return None
    seam_dots = [original["normal"].dot(reflected["normal"])
                 for original, reflected in zip(originals, reflected_records)
                 if original["source_index"] in seam_faces]
    adjacent_dots = list(seam_dots)
    for records in (originals, reflected_records):
        records_by_index = {record["source_index"]: record for record in records}
        adjacent_dots.extend(records_by_index[first]["normal"].dot(records_by_index[second]["normal"])
                             for first, second in surface.get("adjacent_pairs", ()))
    adjacent_min_dot = min(adjacent_dots)
    if adjacent_min_dot < SURFACE_TEXT_MIN_ADJACENT_NORMAL_DOT:
        return None
    joined.update({
        "candidate_label": f"Joined Mirror {'XYZ'[axis]}",
        "mirror_joined": True,
        "mirror_axis": axis,
        "mirror_modifier_name": mirror.name,
        "mirror_object_name": mirror.mirror_object.name if mirror.mirror_object else "",
        "source_face_count": surface["source_face_count"],
        "source_face_indices": list(surface["source_face_indices"]),
        "connected_components": 1,
        "adjacent_min_dot": adjacent_min_dot,
        "max_adjacent_fold_degrees": math.degrees(math.acos(max(-1.0, min(1.0, adjacent_min_dot)))),
    })
    return joined


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


def _read_surface_request(context, join_connected_mirror=True):
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
    if join_connected_mirror:
        joined = _joined_mirror_surface(target, surface, mirror_candidates)
        if joined is not None:
            surface = joined
            project_limit = _surface_project_limit(context, surface, clearance, thickness)
            mirror_candidates = None
    if mirror_candidates is None:
        surface.setdefault("candidate_label", "Original")
        surface.setdefault("mirror_axis", -1)
        surface.setdefault("mirror_object_name", "")
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
        if surface.get("mirror_joined", False):
            # Validate the actual viewport seam, including active Shape Keys.
            # Keep the exporter's stricter modifier contract at export time;
            # ordinary post-Mirror shading modifiers need not block authoring.
            with _evaluated_sampling_mesh(text_obj, verify_export_modifiers=False):
                pass
        _select_created_text(context, text_obj)
    except Exception as exc:
        _remove_created_text(text_obj, text_data)
        _remove_surface_mesh(surface_obj, surface_mesh)
        _restore_state(context, state)
        operator.report({"ERROR"}, str(exc))
        return {"CANCELLED"}

    operator.report({"INFO"}, "Created editable Surface Text with Shrinkwrap and Solidify.")
    return {"FINISHED"}


def _search_bind_fonts(_operator, _context, edit_text):
    """Offer editable Font objects only, retaining the name-based operator API."""
    query = edit_text.casefold()
    return sorted((obj.name for obj in bpy.data.objects
                   if obj.type == "FONT" and getattr(obj, "is_editable", True)
                   and query in obj.name.casefold()), key=str.casefold)


class RR_OT_bind_surface_text(bpy.types.Operator):
    bl_idname = "rr_builder.bind_surface_text"
    bl_label = "Bind Existing Surface Text"
    bl_description = "Bind an existing Font to selected faces without changing its text, pose or material"
    bl_options = {"REGISTER", "UNDO"}

    font_object_name: bpy.props.StringProperty(
        name="Existing Font", search=_search_bind_fonts, search_options={"SORT"})
    mirror_side: bpy.props.EnumProperty(name="Selected side", items=(
        ("ORIGINAL", "Original", "Use the original selected faces"),
        ("MIRRORED", "Mirrored", "Use the one supported Mirror side")), default="ORIGINAL")

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and context.object is not None and context.object.type == "MESH"

    def draw(self, context):
        self.layout.prop(self, "font_object_name", text="Existing Font", icon="FONT_DATA")
        self.layout.prop(self, "mirror_side")
        self.layout.label(text="Keeps text, font, material, transform and modifier settings.")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        state = _capture_state(context)
        try:
            request = _read_surface_request(context, join_connected_mirror=False)
            source = bpy.data.objects.get(self.font_object_name)
            candidates = request["mirror_candidates"]
            if self.mirror_side == "MIRRORED" and not candidates:
                raise RuntimeError("This surface has no supported mirrored candidate.")
            surface = candidates[1 if self.mirror_side == "MIRRORED" else 0] if candidates else request["surface"]
            bpy.ops.object.mode_set(mode="OBJECT")
            bind_existing_surface_text(context, source, request["target"], surface)
        except Exception as exception:
            self.report({"ERROR"}, str(exception))
            return {"CANCELLED"}
        finally:
            _restore_state(context, state)
        self.report({"INFO"}, "Bound selected faces; the existing text and authored pose were retained.")
        return {"FINISHED"}


class RR_OT_add_surface_text(bpy.types.Operator):
    bl_idname = "rr_builder.add_surface_text"
    bl_label = "Add Surface Text"
    bl_description = "Create editable centered text on connected selected faces, including their joined Mirror side, with a persistent sampling mesh"
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
