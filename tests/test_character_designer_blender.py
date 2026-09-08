import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Matrix, Vector


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDONS_ROOT = PROJECT_ROOT / "addons"
if str(ADDONS_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDONS_ROOT))

import character_designer


TOLERANCE = 1.0e-5
TRIANGLE_LOOP_RADII = (0.25, 0.21, 0.16, 0.10)
TRIANGLE_LOOP_Y_SCALES = (0.52, 0.50, 0.47, 0.43)


def assert_vector_close(actual, expected, tolerance=TOLERANCE):
    actual = Vector(actual)
    expected = Vector(expected)
    if (actual - expected).length > tolerance:
        raise AssertionError(f"Expected {tuple(expected)}, got {tuple(actual)}")


def assert_matrix_close(actual, expected, tolerance=TOLERANCE):
    for row in range(4):
        for column in range(4):
            if abs(actual[row][column] - expected[row][column]) > tolerance:
                raise AssertionError(f"Matrix mismatch at {row}, {column}: {actual} != {expected}")


def assert_triangle_loop_metadata(sections, layers, first_ring=0):
    regular_layers = layers[:-1]
    radii = TRIANGLE_LOOP_RADII[first_ring : first_ring + len(regular_layers)]
    y_scales = TRIANGLE_LOOP_Y_SCALES[first_ring : first_ring + len(regular_layers)]
    if len(sections) != len(regular_layers) + 1:
        raise AssertionError("Triangle-loop metadata lost a cross-section")

    for section, layer, radius, y_scale in zip(
        sections[:-1],
        regular_layers,
        radii,
        y_scales,
    ):
        expected_width = radius * math.sqrt(2.25 + 0.75 * y_scale**2)
        expected_pair = list(layer[:2])
        expected_vector = (
            -1.5 * radius,
            0.5 * math.sqrt(3.0) * radius * y_scale,
            0.0,
        )
        if section["kind"] != "CLOSED":
            raise AssertionError("A selected triangle loop was not preserved as CLOSED")
        if abs(section["max_width_local"] - expected_width) > TOLERANCE:
            raise AssertionError(
                f"Triangle loop {tuple(layer)} lost its exact maximum width: "
                f"{section['max_width_local']} != {expected_width}"
            )
        if section["diameter_pair"] != expected_pair:
            raise AssertionError(
                f"Triangle loop {tuple(layer)} lost its deterministic maximum-width pair"
            )
        assert_vector_close(section["diameter_vector_local"], expected_vector)

        profile_span = section["max_profile_span_local"]
        profile_vector = section["profile_span_vector_local"]
        if (
            profile_span <= 0.0
            or section["profile_span_pair"] != expected_pair
            or profile_vector is None
            or abs(Vector(profile_vector).length - profile_span) > TOLERANCE
        ):
            raise AssertionError(
                f"Triangle loop {tuple(layer)} lost its tangent-plane scaling span"
            )

        mesh_normal = Vector(section["mesh_normal_local"])
        frame_normal = Vector(section["normal_local"])
        if (
            section["orientation_source"] != "MESH_NORMAL"
            or abs(mesh_normal.length - 1.0) > TOLERANCE
            or mesh_normal.z < 0.999
            or frame_normal.dot(mesh_normal) <= 0.0
        ):
            raise AssertionError(
                f"Triangle loop {tuple(layer)} did not preserve the quad-band normal direction"
            )

    tip = sections[-1]
    if (
        tip["kind"] != "POINT"
        or tip["max_width_local"] != 0.0
        or tip["diameter_pair"] is not None
        or tip["diameter_vector_local"] is not None
        or tip["max_profile_span_local"] != 0.0
        or tip["profile_span_pair"] is not None
        or tip["profile_span_vector_local"] is not None
        or tip["orientation_source"] != "TRANSPORTED_TIP"
    ):
        raise AssertionError("The point tip did not preserve zero size and transported orientation")


def reset_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for curve in list(bpy.data.curves):
        if curve.users == 0:
            bpy.data.curves.remove(curve)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def make_mesh_object(name, vertices, faces, collections=None):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    for collection in collections or (bpy.context.scene.collection,):
        collection.objects.link(obj)
    return obj


def make_open_grid(
    name,
    row_centers,
    column_offsets=(-0.8, 0.0, 1.1),
    collections=None,
    reverse_faces=False,
):
    vertices = []
    for center in row_centers:
        for offset in column_offsets:
            vertices.append((center[0] + offset, center[1], center[2]))

    column_count = len(column_offsets)
    faces = []
    for row in range(len(row_centers) - 1):
        current = row * column_count
        following = (row + 1) * column_count
        for column in range(column_count - 1):
            face = (
                current + column,
                current + column + 1,
                following + column + 1,
                following + column,
            )
            faces.append(tuple(reversed(face)) if reverse_faces else face)
    obj = make_mesh_object(name, vertices, faces, collections=collections)
    expected = [
        Vector(center) + Vector((sum(column_offsets) / column_count, 0.0, 0.0))
        for center in row_centers
    ]
    root = tuple(range(column_count))
    return obj, root, expected


def make_closed_point_tip(name):
    ring_centers = (
        Vector((0.20, -0.10, 0.00)),
        Vector((0.25, -0.08, 0.35)),
        Vector((0.32, -0.02, 0.68)),
    )
    widths = (0.22, 0.17, 0.10)
    thicknesses = (0.10, 0.08, 0.045)
    vertices = []
    for center, width, thickness in zip(ring_centers, widths, thicknesses):
        for x_value, y_value in (
            (-width * 0.5, -thickness * 0.5),
            (width * 0.5, -thickness * 0.5),
            (width * 0.5, thickness * 0.5),
            (-width * 0.5, thickness * 0.5),
        ):
            vertices.append((center.x + x_value, center.y + y_value, center.z))

    tip = Vector((0.38, 0.01, 0.94))
    tip_index = len(vertices)
    vertices.append(tuple(tip))
    faces = [tuple(reversed(range(4)))]
    for ring in range(len(ring_centers) - 1):
        current = ring * 4
        following = (ring + 1) * 4
        for side in range(4):
            next_side = (side + 1) % 4
            faces.append(
                (
                    current + side,
                    current + next_side,
                    following + next_side,
                    following + side,
                )
            )
    last = (len(ring_centers) - 1) * 4
    for side in range(4):
        next_side = (side + 1) % 4
        faces.append((last + side, last + next_side, tip_index))
    return make_mesh_object(name, vertices, faces), tuple(range(4)), (*ring_centers, tip)


def make_three_by_four_closed_tube(name):
    """Build three regular four-vertex loops with one polygon cap at each end."""

    ring_centers = (
        Vector((0.10, -0.05, 0.00)),
        Vector((0.18, -0.01, 0.40)),
        Vector((0.31, 0.04, 0.82)),
    )
    dimensions = ((0.24, 0.12), (0.20, 0.10), (0.14, 0.07))
    vertices = []
    for center, (width, thickness) in zip(ring_centers, dimensions):
        vertices.extend(
            (
                (center.x - width * 0.5, center.y - thickness * 0.5, center.z),
                (center.x + width * 0.5, center.y - thickness * 0.5, center.z),
                (center.x + width * 0.5, center.y + thickness * 0.5, center.z),
                (center.x - width * 0.5, center.y + thickness * 0.5, center.z),
            )
        )

    faces = [tuple(reversed(range(4)))]
    for ring in range(len(ring_centers) - 1):
        current = ring * 4
        following = (ring + 1) * 4
        for side in range(4):
            next_side = (side + 1) % 4
            faces.append(
                (
                    current + side,
                    current + next_side,
                    following + next_side,
                    following + side,
                )
            )
    last = (len(ring_centers) - 1) * 4
    end_cap_index = len(faces)
    faces.append(tuple(last + side for side in range(4)))
    return make_mesh_object(name, vertices, faces), (0, end_cap_index), ring_centers


def make_four_triangle_loops_and_point(name):
    """Match the artist's 13V/24E/12F full-selection hair topology exactly."""

    ring_centers = (
        Vector((0.02, -0.03, 0.00)),
        Vector((0.08, -0.01, 0.32)),
        Vector((0.17, 0.02, 0.63)),
        Vector((0.29, 0.06, 0.91)),
    )
    vertices = []
    for center, radius, y_scale in zip(
        ring_centers,
        TRIANGLE_LOOP_RADII,
        TRIANGLE_LOOP_Y_SCALES,
    ):
        for side in range(3):
            angle = 2.0 * math.pi * side / 3.0
            vertices.append(
                (
                    center.x + math.cos(angle) * radius,
                    center.y + math.sin(angle) * radius * y_scale,
                    center.z,
                )
            )

    tip = Vector((0.43, 0.10, 1.18))
    tip_index = len(vertices)
    vertices.append(tuple(tip))

    faces = []
    for ring in range(len(ring_centers) - 1):
        current = ring * 3
        following = (ring + 1) * 3
        for side in range(3):
            next_side = (side + 1) % 3
            faces.append(
                (
                    current + side,
                    current + next_side,
                    following + next_side,
                    following + side,
                )
            )
    final_ring = (len(ring_centers) - 1) * 3
    for side in range(3):
        next_side = (side + 1) % 3
        faces.append((final_ring + side, final_ring + next_side, tip_index))

    obj = make_mesh_object(name, vertices, faces)
    expected_centers = (*ring_centers, tip)
    return obj, tip_index, expected_centers


def make_radial_fan_tube(
    name,
    side_count=12,
    hub_offset=(0.037, -0.011),
):
    root_center = Vector((0.20, -0.10, 0.00))
    next_center = Vector((0.34, -0.02, 0.48))
    vertices = []
    for center, width, thickness in (
        (root_center, 0.30, 0.12),
        (next_center, 0.24, 0.09),
    ):
        for side in range(side_count):
            angle = 2.0 * math.pi * side / side_count
            vertices.append(
                (
                    center.x + math.cos(angle) * width,
                    center.y + math.sin(angle) * thickness,
                    center.z,
                )
            )

    hub_index = len(vertices)
    vertices.append(
        (
            root_center.x + hub_offset[0],
            root_center.y + hub_offset[1],
            root_center.z,
        )
    )
    faces = []
    for side in range(side_count):
        following = (side + 1) % side_count
        faces.append((hub_index, following, side))
    for side in range(side_count):
        following = (side + 1) % side_count
        faces.append((side, following, side_count + following, side_count + side))

    obj = make_mesh_object(name, vertices, faces)
    cap_faces = tuple(range(side_count))
    cap_and_band_faces = tuple(range(side_count * 2))
    return obj, cap_faces, cap_and_band_faces, hub_index, (root_center, next_center)


def make_triangulated_square_cap_tube(name):
    root_center = Vector((0.12, -0.06, 0.00))
    next_center = Vector((0.27, 0.01, 0.46))
    vertices = []
    for center, width, thickness in (
        (root_center, 0.40, 0.20),
        (next_center, 0.30, 0.16),
    ):
        vertices.extend(
            (
                (center.x - width * 0.5, center.y - thickness * 0.5, center.z),
                (center.x + width * 0.5, center.y - thickness * 0.5, center.z),
                (center.x + width * 0.5, center.y + thickness * 0.5, center.z),
                (center.x - width * 0.5, center.y + thickness * 0.5, center.z),
            )
        )

    # The root cap faces -Z and deliberately contains the internal 0-2 chord.
    faces = [(0, 2, 1), (0, 3, 2)]
    for side in range(4):
        following = (side + 1) % 4
        faces.append((side, following, 4 + following, 4 + side))
    obj = make_mesh_object(name, vertices, faces)
    return obj, (0, 1), tuple(range(len(faces))), (0, 2), (root_center, next_center)


def make_nonradial_triangulated_tube(name, side_count=12):
    if side_count != 12:
        raise ValueError("The non-radial regression topology is defined for 12 sides")

    root_center = Vector((0.20, -0.10, 0.00))
    next_center = Vector((0.34, -0.02, 0.48))
    vertices = []
    for center, width, thickness in (
        (root_center, 0.30, 0.12),
        (next_center, 0.24, 0.09),
    ):
        for side in range(side_count):
            angle = 2.0 * math.pi * side / side_count
            vertices.append(
                (
                    center.x + math.cos(angle) * width,
                    center.y + math.sin(angle) * thickness,
                    center.z,
                )
            )

    interior_index = len(vertices)
    vertices.append((root_center.x + 0.015, root_center.y - 0.006, root_center.z))

    # The interior vertex connects only to vertices 0, 4, and 8.  The remaining
    # nine triangles use local boundary diagonals, so this is a valid 12-face
    # disk but deliberately not a shared-center radial fan.
    faces = [
        (interior_index, 0, 4),
        (interior_index, 4, 8),
        (interior_index, 8, 0),
    ]
    for sector in ((0, 1, 2, 3, 4), (4, 5, 6, 7, 8), (8, 9, 10, 11, 0)):
        for index in range(1, len(sector) - 1):
            faces.append((sector[0], sector[index], sector[index + 1]))

    for side in range(side_count):
        following = (side + 1) % side_count
        faces.append((side, following, side_count + following, side_count + side))

    obj = make_mesh_object(name, vertices, faces)
    cap_faces = tuple(range(side_count))
    cap_and_band_faces = tuple(range(side_count * 2))
    return obj, cap_faces, cap_and_band_faces, interior_index, (root_center, next_center)


def make_triangulated_open_card(name):
    vertices = [
        (float(column), 0.0, float(row))
        for row in range(3)
        for column in range(3)
    ]
    faces = []
    for row in range(2):
        for column in range(2):
            lower_left = row * 3 + column
            lower_right = lower_left + 1
            upper_left = lower_left + 3
            upper_right = upper_left + 1
            faces.extend(
                (
                    (lower_left, lower_right, upper_right),
                    (lower_left, upper_right, upper_left),
                )
            )
    return make_mesh_object(name, vertices, faces), tuple(range(len(faces)))


def make_mixed_polygon_cap_tube(name, side_count=6):
    if side_count != 6:
        raise ValueError("The mixed-polygon regression topology is defined for 6 sides")

    root_center = Vector((-0.16, 0.08, 0.00))
    next_center = Vector((-0.09, 0.13, 0.42))
    vertices = []
    for center, width, thickness in (
        (root_center, 0.31, 0.14),
        (next_center, 0.25, 0.11),
    ):
        for side in range(side_count):
            angle = 2.0 * math.pi * side / side_count
            vertices.append(
                (
                    center.x + math.cos(angle) * width,
                    center.y + math.sin(angle) * thickness,
                    center.z,
                )
            )

    interior_index = len(vertices)
    vertices.append(tuple(root_center))
    faces = [
        (interior_index, 0, 1),
        (interior_index, 1, 2, 3),
        (interior_index, 3, 4, 5, 0),
    ]
    for side in range(side_count):
        following = (side + 1) % side_count
        faces.append((side, following, side_count + following, side_count + side))

    obj = make_mesh_object(name, vertices, faces)
    cap_faces = (0, 1, 2)
    cap_and_band_faces = tuple(range(len(faces)))
    return obj, cap_faces, cap_and_band_faces, interior_index, (root_center, next_center)


def make_cap_with_collapsed_first_collar(name, side_count=5):
    root_center = Vector((0.11, -0.04, 0.00))
    vertices = []
    for side in range(side_count):
        angle = 2.0 * math.pi * side / side_count
        vertices.append(
            (
                root_center.x + math.cos(angle) * 0.23,
                root_center.y + math.sin(angle) * 0.10,
                root_center.z,
            )
        )
    tip = Vector((0.19, 0.02, 0.52))
    tip_index = len(vertices)
    vertices.append(tuple(tip))

    faces = [tuple(reversed(range(side_count)))]
    for side in range(side_count):
        following = (side + 1) % side_count
        faces.append((side, following, tip_index))
    obj = make_mesh_object(name, vertices, faces)
    return obj, (0,), tuple(range(len(faces))), (root_center, tip)


def select_vertices(obj, indices):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(vertex.index in indices)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def select_faces(obj, indices):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(face.index in indices)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def select_edges(obj, edge_keys):
    edge_keys = {tuple(sorted(edge_key)) for edge_key in edge_keys}
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(obj.data)
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        key = tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        edge.select_set(key in edge_keys)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def capture_then_build(obj, root_indices, selected_indices=None):
    selected_indices = selected_indices or tuple(range(len(obj.data.vertices)))
    select_vertices(obj, root_indices)
    result = bpy.ops.character_designer.capture_root_slice()
    if result != {"FINISHED"}:
        raise AssertionError(f"Root capture failed for {obj.name}: {result}")
    select_vertices(obj, selected_indices)
    before_selection = {
        vertex.index
        for vertex in bmesh.from_edit_mesh(obj.data).verts
        if vertex.select
    }
    result = bpy.ops.character_designer.build_centerline()
    if result != {"FINISHED"}:
        raise AssertionError(f"Centerline build failed for {obj.name}: {result}")
    after_selection = {
        vertex.index
        for vertex in bmesh.from_edit_mesh(obj.data).verts
        if vertex.select
    }
    if before_selection != after_selection or bpy.context.mode != "EDIT_MESH":
        raise AssertionError("Build changed the source Edit Mode selection")
    return bpy.context.window_manager.character_designer.output_object


def read_cross_section_metadata(curve_obj):
    if curve_obj.get("character_designer_metadata_version") != character_designer.METADATA_VERSION:
        raise AssertionError("Generated Curve has no recognized metadata version")
    metadata = character_designer._curve_cross_section_metadata(curve_obj)
    if metadata is None:
        raise AssertionError("Generated Curve metadata failed the production schema validator")
    if metadata.get("version") != character_designer.METADATA_VERSION:
        raise AssertionError("Metadata payload version does not match its object marker")
    if metadata.get("space") != "SOURCE_OBJECT_LOCAL":
        raise AssertionError("Cross-section metadata must use source-object local space")
    sections = metadata.get("sections")
    if not isinstance(sections, list) or metadata.get("point_count") != len(sections):
        raise AssertionError("Metadata point count does not match its section array")
    spline_point_count = len(curve_obj.data.splines[0].points)
    if len(sections) != spline_point_count:
        raise AssertionError("Metadata must contain exactly one section per Curve point")
    if len(metadata.get("matrix_world_at_build", ())) != 16:
        raise AssertionError("Metadata did not preserve the source matrix snapshot")
    return metadata


def assert_plain_poly_curve(curve_obj, expected_centers):
    if curve_obj is None or curve_obj.type != "CURVE":
        raise AssertionError("No curve output")
    data = curve_obj.data
    if (
        data.bevel_mode != "ROUND"
        or data.fill_mode != "HALF"
        or data.bevel_depth <= 0.0
        or data.bevel_resolution != character_designer.HAIR_PROFILE_BEVEL_RESOLUTION
        or data.extrude != 0.0
        or data.taper_object is not None
    ):
        raise AssertionError("Output does not use the automatic Round/Half hair profile")
    if len(data.splines) != 1 or data.splines[0].type != "POLY":
        raise AssertionError("Output must contain one Poly spline")
    spline = data.splines[0]
    if spline.use_cyclic_u:
        raise AssertionError("Centerline must be open")
    if len(spline.points) != len(expected_centers):
        raise AssertionError(f"Expected {len(expected_centers)} points, got {len(spline.points)}")
    metadata = read_cross_section_metadata(curve_obj)
    expected_depth, expected_radii, expected_tilts = (
        character_designer._curve_profile_parameters(metadata)
    )
    if abs(data.bevel_depth - expected_depth) > TOLERANCE:
        raise AssertionError("Output bevel Depth does not match the source width")
    for point, expected, radius, tilt in zip(
        spline.points,
        expected_centers,
        expected_radii,
        expected_tilts,
    ):
        assert_vector_close(point.co.xyz, expected)
        if abs(point.radius - radius) > TOLERANCE or abs(point.tilt - tilt) > TOLERANCE:
            raise AssertionError("Curve Radius or Tilt does not match the source profile")


def test_live_shape_open_patch():
    reset_scene()
    centers = (
        (0.0, 0.0, 0.0),
        (0.15, 0.05, 0.42),
        (0.30, 0.12, 0.91),
    )
    obj, root, expected = make_open_grid("OpenPatch_4Faces_9Verts", centers)
    if len(obj.data.vertices) != 9 or len(obj.data.edges) != 12 or len(obj.data.polygons) != 4:
        raise AssertionError("The live-shape regression fixture must be 4F/9V/12E")
    output = capture_then_build(obj, root)
    assert_plain_poly_curve(output, expected)
    assert_matrix_close(output.matrix_world, obj.matrix_world)


def test_open_metadata_width_and_winding_direction():
    def build(reverse_faces):
        reset_scene()
        centers = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.5), (0.0, 0.0, 1.0))
        obj, root, expected = make_open_grid(
            "OpenMetadataReversed" if reverse_faces else "OpenMetadata",
            centers,
            reverse_faces=reverse_faces,
        )
        output = capture_then_build(obj, root)
        assert_plain_poly_curve(output, expected)
        return read_cross_section_metadata(output)

    forward = build(False)
    forward_sections = forward["sections"]
    for layer_index, section in enumerate(forward_sections):
        if abs(section["max_width_local"] - 1.9) > TOLERANCE:
            raise AssertionError("Open-row maximum width was not stored exactly")
        if section["diameter_pair"] != [layer_index * 3, layer_index * 3 + 2]:
            raise AssertionError("Open-row diameter pair is not deterministic")
        if abs(section["max_profile_span_local"] - 1.9) > TOLERANCE:
            raise AssertionError("Open-row profile span was not stored exactly")
        if section["profile_span_pair"] != [layer_index * 3, layer_index * 3 + 2]:
            raise AssertionError("Open-row profile-span pair is not deterministic")
        assert_vector_close(section["diameter_vector_local"], (1.9, 0.0, 0.0))
        assert_vector_close(section["profile_span_vector_local"], (1.9, 0.0, 0.0))
        if section["width_axis_source"] != "PROFILE_SPAN":
            raise AssertionError("Open-row width orientation did not use its profile span")
        if section["orientation_source"] != "MESH_NORMAL":
            raise AssertionError("A coherent open strip should use its band-face normal")
        assert_vector_close(section["mesh_normal_local"], (0.0, -1.0, 0.0))
        assert_vector_close(section["normal_local"], (0.0, -1.0, 0.0))
        tangent = Vector(section["tangent_local"])
        width_axis = Vector(section["width_axis_local"])
        normal = Vector(section["normal_local"])
        if (
            abs(tangent.length - 1.0) > TOLERANCE
            or abs(width_axis.length - 1.0) > TOLERANCE
            or abs(normal.length - 1.0) > TOLERANCE
            or abs(tangent.dot(width_axis)) > TOLERANCE
            or abs(tangent.dot(normal)) > TOLERANCE
            or abs(width_axis.dot(normal)) > TOLERANCE
        ):
            raise AssertionError("Stored open-strip orientation is not an orthonormal frame")

    reversed_metadata = build(True)
    reversed_sections = reversed_metadata["sections"]
    for forward_section, reversed_section in zip(forward_sections, reversed_sections):
        if Vector(forward_section["mesh_normal_local"]).dot(
            Vector(reversed_section["mesh_normal_local"])
        ) > -0.999:
            raise AssertionError("Reversing source winding did not reverse the stored mesh normal")
        if Vector(forward_section["normal_local"]).dot(
            Vector(reversed_section["normal_local"])
        ) > -0.999:
            raise AssertionError("The profile frame did not preserve source normal direction")


def test_skew_sections_preserve_raw_chord_and_profile_span():
    reset_scene()
    vertices = (
        (-1.0, 0.0, -0.5),
        (1.0, 0.0, 0.5),
        (-1.0, 0.0, 0.5),
        (1.0, 0.0, 1.5),
        (-1.0, 0.0, 1.5),
        (1.0, 0.0, 2.5),
    )
    faces = ((0, 1, 3, 2), (2, 3, 5, 4))
    obj = make_mesh_object("SkewProfileSpan", vertices, faces)
    output = capture_then_build(obj, (0, 1))
    assert_plain_poly_curve(
        output,
        (Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 1.0)), Vector((0.0, 0.0, 2.0))),
    )
    metadata = read_cross_section_metadata(output)
    for layer_index, section in enumerate(metadata["sections"]):
        expected_pair = [layer_index * 2, layer_index * 2 + 1]
        if abs(section["max_width_local"] - math.sqrt(5.0)) > TOLERANCE:
            raise AssertionError("The skew section's lossless raw chord was not preserved")
        if section["diameter_pair"] != expected_pair:
            raise AssertionError("The skew section's raw chord pair is not deterministic")
        assert_vector_close(section["diameter_vector_local"], (2.0, 0.0, 1.0))
        if abs(section["max_profile_span_local"] - 2.0) > TOLERANCE:
            raise AssertionError("The skew section's tangent-plane profile span is incorrect")
        if section["profile_span_pair"] != expected_pair:
            raise AssertionError("The skew section's profile-span pair is not deterministic")
        assert_vector_close(section["profile_span_vector_local"], (2.0, 0.0, 0.0))
        if section["width_axis_source"] != "PROFILE_SPAN":
            raise AssertionError("Width orientation must be driven by the projected profile span")
        if abs(Vector(section["width_axis_local"]).dot(Vector((1.0, 0.0, 0.0)))) < 0.999:
            raise AssertionError("The skew section's width axis retained longitudinal slant")

    summary = character_designer._metadata_summary(metadata)
    if any(
        abs(summary[key] - 2.0) > TOLERANCE
        for key in ("root_profile_span", "maximum_profile_span", "tip_profile_span")
    ):
        raise AssertionError("The UI summary did not use tangent-plane profile span")


def test_flipped_single_side_quad_is_rejected_atomically():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "FlippedSingleQuad",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.5), (0.0, 0.0, 1.0)),
    )
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.faces[1].normal_flip()
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    select_vertices(obj, root)
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("Root capture failed for the flipped-quad fixture")
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    settings = bpy.context.window_manager.character_designer
    settings.output_object = None
    selected_before = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    object_names = set(bpy.data.objects.keys())
    curve_names = set(bpy.data.curves.keys())
    try:
        result = bpy.ops.character_designer.build_centerline()
    except RuntimeError as exc:
        error = str(exc)
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("A side band with one flipped quad should be rejected")
        error = settings.last_message
    if "inconsistent winding" not in error:
        raise AssertionError(f"Flipped-quad rejection was unclear: {error}")
    selected_after = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    if (
        object_names != set(bpy.data.objects.keys())
        or curve_names != set(bpy.data.curves.keys())
        or settings.output_object is not None
    ):
        raise AssertionError("A winding failure left output residue")
    if bpy.context.mode != "EDIT_MESH" or selected_before != selected_after:
        raise AssertionError("A winding failure changed the source Edit Mode selection")


def test_closed_tube_and_point_tip():
    reset_scene()
    obj, root, expected = make_closed_point_tip("ClosedTubePointTip")
    select_faces(obj, (0,))
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("A selected root cap face was not captured")
    settings = bpy.context.window_manager.character_designer
    if settings.root_kind != "CLOSED" or settings.root_vertex_count != len(root):
        raise AssertionError("The selected root cap was not classified as its boundary loop")
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("Closed tube build failed")
    output = settings.output_object
    assert_plain_poly_curve(output, expected)
    summary = character_designer._metadata_summary(read_cross_section_metadata(output))
    if summary["minimum_profile_span"] <= 0.0:
        raise AssertionError("The visible profile-span range must exclude the point tip")
    metadata = read_cross_section_metadata(output)
    sections = metadata["sections"]
    expected_widths = (
        math.sqrt(0.22**2 + 0.10**2),
        math.sqrt(0.17**2 + 0.08**2),
        math.sqrt(0.10**2 + 0.045**2),
        0.0,
    )
    expected_pairs = ([0, 2], [4, 6], [8, 10], None)
    for section, expected_width, expected_pair in zip(
        sections,
        expected_widths,
        expected_pairs,
    ):
        if abs(section["max_width_local"] - expected_width) > TOLERANCE:
            raise AssertionError("Closed-loop maximum width was not preserved")
        if section["diameter_pair"] != expected_pair:
            raise AssertionError("Equal closed-loop diameters did not use a deterministic pair")

    tip_section = sections[-1]
    if (
        tip_section["kind"] != "POINT"
        or tip_section["max_width_local"] != 0.0
        or tip_section["diameter_pair"] is not None
        or tip_section["diameter_vector_local"] is not None
        or tip_section["max_profile_span_local"] != 0.0
        or tip_section["profile_span_pair"] is not None
        or tip_section["profile_span_vector_local"] is not None
        or tip_section["orientation_source"] != "TRANSPORTED_TIP"
        or tip_section["width_axis_source"] != "TRANSPORTED"
    ):
        raise AssertionError("Point-tip metadata did not use zero width and transported orientation")
    tip_tangent = Vector(tip_section["tangent_local"])
    tip_axis = Vector(tip_section["width_axis_local"])
    tip_normal = Vector(tip_section["normal_local"])
    if (
        abs(tip_axis.length - 1.0) > TOLERANCE
        or abs(tip_normal.length - 1.0) > TOLERANCE
        or abs(tip_tangent.dot(tip_axis)) > TOLERANCE
        or abs(tip_tangent.dot(tip_normal)) > TOLERANCE
    ):
        raise AssertionError("Transported point-tip orientation is invalid")

    # Rebuilding the unchanged source must resolve all equal diagonal ties and
    # orientation signs identically.
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("Determinism rebuild failed")
    repeated = read_cross_section_metadata(settings.output_object)
    if [section["diameter_pair"] for section in repeated["sections"]] != [
        section["diameter_pair"] for section in sections
    ]:
        raise AssertionError("Diameter tie resolution changed between identical builds")
    for first, second in zip(sections, repeated["sections"]):
        if Vector(first["width_axis_local"]).dot(Vector(second["width_axis_local"])) < 0.999:
            raise AssertionError("Orientation sign changed between identical builds")


def test_point_tip_can_be_captured_as_reverse_root():
    reset_scene()
    obj, _forward_root, expected = make_closed_point_tip("PointTipReverseRoot")
    tip_index = len(obj.data.vertices) - 1
    select_vertices(obj, (tip_index,))

    try:
        result = bpy.ops.character_designer.capture_root_slice()
    except RuntimeError as exc:
        raise AssertionError("A single endpoint vertex should be capturable as a POINT root") from exc
    if result != {"FINISHED"}:
        raise AssertionError(f"POINT-root capture failed: {result}")

    settings = bpy.context.window_manager.character_designer
    if (
        settings.root_kind != "POINT"
        or settings.root_vertex_count != 1
        or tuple(character_designer._indices_from_settings(settings)) != (tip_index,)
        or tuple(character_designer._ignored_indices_from_settings(settings))
    ):
        raise AssertionError("The single endpoint was not stored as one lossless POINT root")

    all_indices = tuple(range(len(obj.data.vertices)))
    select_vertices(obj, all_indices)
    selected_before = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("Reverse point-to-rings centerline build failed")
    selected_after = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    if bpy.context.mode != "EDIT_MESH" or selected_before != selected_after:
        raise AssertionError("Reverse POINT-root build changed the live Edit Mode selection")

    output = settings.output_object
    reverse_expected = tuple(reversed(expected))
    assert_plain_poly_curve(output, reverse_expected)
    layers = json.loads(output["character_designer_layer_vertices"])
    if len(layers) != 4 or layers[0] != [tip_index]:
        raise AssertionError("Reverse layering swallowed the captured endpoint")

    metadata = read_cross_section_metadata(output)
    first_section = metadata["sections"][0]
    if (
        metadata["point_count"] != 4
        or first_section["kind"] != "POINT"
        or first_section["max_width_local"] != 0.0
        or first_section["diameter_pair"] is not None
        or first_section["max_profile_span_local"] != 0.0
        or first_section["profile_span_pair"] is not None
    ):
        raise AssertionError("The POINT-root section did not preserve a readable zero-span endpoint")


def test_point_tip_select_more_builds_four_reverse_points():
    reset_scene()
    obj, _forward_root, expected = make_closed_point_tip("PointTipSelectMore")
    tip_index = len(obj.data.vertices) - 1
    select_vertices(obj, (tip_index,))

    try:
        result = bpy.ops.character_designer.capture_root_slice()
    except RuntimeError as exc:
        raise AssertionError("Select More workflow requires direct POINT-root capture") from exc
    if result != {"FINISHED"}:
        raise AssertionError(f"POINT-root capture failed before Select More: {result}")

    selected_counts = []
    for step in range(4):
        bm = bmesh.from_edit_mesh(obj.data)
        selected_counts.append(sum(vertex.select for vertex in bm.verts))
        if step < 3:
            bpy.ops.mesh.select_more()
    if selected_counts != [1, 5, 9, 13]:
        raise AssertionError(
            f"POINT-root Select More layers should grow 1->5->9->13, got {selected_counts}"
        )

    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("Select More did not build the reverse point-to-rings centerline")
    output = bpy.context.window_manager.character_designer.output_object
    assert_plain_poly_curve(output, tuple(reversed(expected)))

    layers = json.loads(output["character_designer_layer_vertices"])
    expected_layers = [[tip_index], [8, 9, 10, 11], [4, 5, 6, 7], [0, 1, 2, 3]]
    if layers != expected_layers:
        raise AssertionError(f"Select More produced the wrong reverse layers: {layers}")
    if len(read_cross_section_metadata(output)["sections"]) != 4:
        raise AssertionError("Select More must produce exactly four Curve sections")


def test_full_selection_toggle_parses_four_triangle_loops_without_cap_capture():
    reset_scene()
    obj, tip_index, expected_centers = make_four_triangle_loops_and_point(
        "FullSelectionTriangleLoops"
    )
    select_faces(obj, tuple(range(len(obj.data.polygons))))
    bm = bmesh.from_edit_mesh(obj.data)
    counts = (
        sum(vertex.select for vertex in bm.verts),
        sum(edge.select for edge in bm.edges),
        sum(face.select for face in bm.faces),
    )
    if counts != (13, 24, 12):
        raise AssertionError(f"The screenshot regression fixture must be 13V/24E/12F, got {counts}")

    settings = bpy.context.window_manager.character_designer
    original_cap_parser = character_designer._capture_selected_face_boundary
    cap_parser_calls = []

    def forbidden_cap_parser(*_args, **_kwargs):
        cap_parser_calls.append(True)
        raise AssertionError("Full-selection inference must not interpret selected faces as a cap")

    try:
        character_designer._capture_selected_face_boundary = forbidden_cap_parser
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()

        expected_layers = (
            (0, 1, 2),
            (3, 4, 5),
            (6, 7, 8),
            (9, 10, 11),
            (tip_index,),
        )
        if cap_parser_calls:
            raise AssertionError("The full-selection toggle called the legacy cap parser")
        if (
            not settings.live_preview_enabled
            or not settings.preview_active
            or not settings.preview_valid
            or not settings.preview_confirmable
            or settings.preview_layer_count != 5
        ):
            raise AssertionError(
                f"The 13V/24E/12F selection did not produce a valid five-point preview: "
                f"{settings.preview_message}"
            )
        if character_designer._LIVE_PREVIEW_LAYERS != expected_layers:
            raise AssertionError(
                "Full-selection inference must order four triangle loops followed by the point tip, "
                f"got {character_designer._LIVE_PREVIEW_LAYERS}"
            )
        if len(character_designer._LIVE_PREVIEW_WORLD_POINTS) != len(expected_centers):
            raise AssertionError("Full-selection preview did not publish five world-space centers")
        for actual, expected in zip(
            character_designer._LIVE_PREVIEW_WORLD_POINTS,
            expected_centers,
        ):
            assert_vector_close(actual, obj.matrix_world @ expected)

        metadata = json.loads(character_designer._LIVE_PREVIEW_METADATA_JSON)
        sections = metadata.get("sections", ())
        assert_triangle_loop_metadata(sections, expected_layers)
    finally:
        character_designer._capture_selected_face_boundary = original_cap_parser
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_full_selection_toggle_pauses_in_object_mode_and_recovers():
    reset_scene()
    obj, tip_index, _expected_centers = make_four_triangle_loops_and_point(
        "FullSelectionModePause"
    )
    select_faces(obj, tuple(range(len(obj.data.polygons))))
    settings = bpy.context.window_manager.character_designer

    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        if not settings.preview_valid or settings.preview_layer_count != 5:
            raise AssertionError("Mode-pause fixture did not start with a valid five-layer preview")

        bpy.ops.object.mode_set(mode="OBJECT")
        character_designer._live_preview_tick()
        if not settings.live_preview_enabled:
            raise AssertionError("Leaving Edit Mode must pause, not switch off, the Live Preview toggle")
        if settings.preview_valid or settings.preview_confirmable:
            raise AssertionError("Object Mode left a stale preview confirmable")
        if character_designer._LIVE_PREVIEW_WORLD_POINTS or character_designer._LIVE_PREVIEW_LAYERS:
            raise AssertionError("Object Mode pause left stale preview geometry visible")

        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        character_designer._live_preview_tick()
        expected_sizes = [3, 3, 3, 3, 1]
        if (
            not settings.live_preview_enabled
            or not settings.preview_valid
            or not settings.preview_confirmable
            or [len(layer) for layer in character_designer._LIVE_PREVIEW_LAYERS]
            != expected_sizes
            or character_designer._LIVE_PREVIEW_LAYERS[-1] != (tip_index,)
        ):
            raise AssertionError(
                f"Returning to Edit Mode did not recover the full-selection preview: "
                f"{settings.preview_message}"
            )
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_full_selection_confirm_reparses_latest_selection():
    reset_scene()
    obj, tip_index, expected_centers = make_four_triangle_loops_and_point(
        "FullSelectionFreshConfirm"
    )
    select_faces(obj, tuple(range(len(obj.data.polygons))))
    settings = bpy.context.window_manager.character_designer

    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        stale_layers = character_designer._LIVE_PREVIEW_LAYERS
        if not settings.preview_confirmable or [len(layer) for layer in stale_layers] != [3, 3, 3, 3, 1]:
            raise AssertionError("Fresh-confirm fixture did not publish its initial five layers")

        latest_indices = set(range(3, tip_index + 1))
        bpy.context.tool_settings.mesh_select_mode = (True, False, False)
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for face in bm.faces:
            face.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        for vertex in bm.verts:
            vertex.select_set(vertex.index in latest_indices)
        bm.select_history.clear()
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        if character_designer._LIVE_PREVIEW_LAYERS != stale_layers:
            raise AssertionError("The test did not leave a deliberately stale five-layer preview cache")

        result = bpy.ops.character_designer.confirm_centerline()
        if result != {"FINISHED"}:
            raise AssertionError(f"Direct full-selection Confirm failed: {result}")
        output = settings.output_object
        latest_centers = expected_centers[1:]
        assert_plain_poly_curve(output, latest_centers)
        layers = json.loads(output["character_designer_layer_vertices"])
        expected_layers = [[3, 4, 5], [6, 7, 8], [9, 10, 11], [tip_index]]
        if layers != expected_layers:
            raise AssertionError(
                "Confirm committed the stale preview instead of reparsing the latest selection, "
                f"got {layers}"
            )
        metadata = read_cross_section_metadata(output)
        if metadata["point_count"] != 4:
            raise AssertionError("Fresh Confirm metadata did not match the reparsed four-point Curve")
        assert_triangle_loop_metadata(metadata["sections"], expected_layers, first_ring=1)
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_auto_radial_fan_cap_only_and_band_ignore_hub():
    reset_scene()
    obj, cap_faces, cap_and_band_faces, hub_index, expected_centers = make_radial_fan_tube(
        "AutoRadialFan"
    )
    select_faces(obj, cap_faces)
    bm = bmesh.from_edit_mesh(obj.data)
    cap_counts = (
        sum(vertex.select for vertex in bm.verts),
        sum(edge.select for edge in bm.edges),
        sum(face.select for face in bm.faces),
    )
    if cap_counts != (13, 24, 12):
        raise AssertionError(f"AUTO radial-cap fixture must begin at 13V/24E/12F, got {cap_counts}")

    settings = bpy.context.window_manager.character_designer
    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        expected_root_layer = (tuple(range(12)),)
        if (
            not settings.preview_valid
            or settings.preview_confirmable
            or settings.preview_layer_count != 1
            or character_designer._LIVE_PREVIEW_LAYERS != expected_root_layer
            or hub_index in character_designer._LIVE_PREVIEW_LAYERS[0]
        ):
            raise AssertionError(
                "A complete triangle-fan cap must preview only its boundary center, "
                f"got {character_designer._LIVE_PREVIEW_LAYERS}: {settings.preview_message}"
            )
        if bpy.ops.character_designer.confirm_centerline.poll():
            raise AssertionError("A cap-only AUTO preview must not enable Confirm")
        assert_vector_close(
            character_designer._LIVE_PREVIEW_WORLD_POINTS[0],
            obj.matrix_world @ expected_centers[0],
        )

        bm.faces.ensure_lookup_table()
        for face in bm.faces:
            face.select_set(face.index in cap_and_band_faces)
        bm.select_history.clear()
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        grown_counts = (
            sum(vertex.select for vertex in bm.verts),
            sum(edge.select for edge in bm.edges),
            sum(face.select for face in bm.faces),
        )
        if grown_counts != (25, 48, 24):
            raise AssertionError(
                f"AUTO radial cap+band fixture must be 25V/48E/24F, got {grown_counts}"
            )

        character_designer._live_preview_tick()
        expected_layers = (tuple(range(12)), tuple(range(12, 24)))
        flattened_layers = {
            index
            for layer in character_designer._LIVE_PREVIEW_LAYERS
            for index in layer
        }
        if (
            not settings.preview_valid
            or not settings.preview_confirmable
            or settings.preview_layer_count != 2
            or character_designer._LIVE_PREVIEW_LAYERS != expected_layers
            or hub_index in flattened_layers
        ):
            raise AssertionError(
                "AUTO cap+band inference did not discard the coplanar hub and orient "
                "root-to-outer, "
                f"got {character_designer._LIVE_PREVIEW_LAYERS}: {settings.preview_message}"
            )
        for actual, expected in zip(
            character_designer._LIVE_PREVIEW_WORLD_POINTS,
            expected_centers,
        ):
            assert_vector_close(actual, obj.matrix_world @ expected)
        if not bpy.ops.character_designer.confirm_centerline.poll():
            raise AssertionError("A complete radial cap+band selection must enable Confirm")
        if bpy.ops.character_designer.confirm_centerline() != {"FINISHED"}:
            raise AssertionError("AUTO radial cap+band Confirm failed")
        assert_plain_poly_curve(settings.output_object, expected_centers)
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_auto_triangulated_square_cap_ignores_internal_chord():
    reset_scene()
    obj, cap_faces, all_faces, chord_key, expected_centers = make_triangulated_square_cap_tube(
        "AutoTriangulatedSquareCap"
    )
    select_faces(obj, cap_faces)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    selected_edge_keys = {
        tuple(sorted(vertex.index for vertex in edge.verts))
        for edge in bm.edges
        if edge.select
    }
    cap_counts = (
        sum(vertex.select for vertex in bm.verts),
        sum(edge.select for edge in bm.edges),
        sum(face.select for face in bm.faces),
    )
    if cap_counts != (4, 5, 2) or chord_key not in selected_edge_keys:
        raise AssertionError(
            f"Triangulated square cap must select 4V/5E/2F including chord {chord_key}, "
            f"got {cap_counts} and {selected_edge_keys}"
        )

    expected_root_layer = (0, 1, 2, 3)
    settings = bpy.context.window_manager.character_designer
    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        if (
            not settings.preview_valid
            or settings.preview_confirmable
            or settings.preview_layer_count != 1
            or character_designer._LIVE_PREVIEW_LAYERS != (expected_root_layer,)
            or settings.root_kind != "CLOSED"
        ):
            raise AssertionError(
                "Cap-only AUTO inference did not reduce the two triangles to one semantic "
                f"boundary loop: {settings.preview_message}"
            )
        if bpy.ops.character_designer.confirm_centerline.poll():
            raise AssertionError("A triangulated cap-only preview must not enable Confirm")
        assert_vector_close(
            character_designer._LIVE_PREVIEW_WORLD_POINTS[0],
            obj.matrix_world @ expected_centers[0],
        )

        bm.faces.ensure_lookup_table()
        for face in bm.faces:
            face.select_set(face.index in all_faces)
        bm.select_history.clear()
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        full_counts = (
            sum(vertex.select for vertex in bm.verts),
            sum(edge.select for edge in bm.edges),
            sum(face.select for face in bm.faces),
        )
        if full_counts != (8, 13, 6):
            raise AssertionError(f"Triangulated square tube must be 8V/13E/6F, got {full_counts}")

        character_designer._live_preview_tick()
        expected_layers = (expected_root_layer, (4, 5, 6, 7))
        if (
            not settings.preview_valid
            or not settings.preview_confirmable
            or settings.preview_layer_count != 2
            or character_designer._LIVE_PREVIEW_LAYERS != expected_layers
        ):
            raise AssertionError(
                "Full AUTO inference treated the selected cap diagonal as a profile edge, "
                f"got {character_designer._LIVE_PREVIEW_LAYERS}: {settings.preview_message}"
            )
        for actual, expected in zip(
            character_designer._LIVE_PREVIEW_WORLD_POINTS,
            expected_centers,
        ):
            assert_vector_close(actual, obj.matrix_world @ expected)
        if not bpy.ops.character_designer.confirm_centerline.poll():
            raise AssertionError("A complete triangulated square cap+band must enable Confirm")
        if bpy.ops.character_designer.confirm_centerline() != {"FINISHED"}:
            raise AssertionError("Triangulated square cap AUTO Confirm failed")
        assert_plain_poly_curve(settings.output_object, expected_centers)
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_auto_nonradial_cap_metadata_matches_captured_cap():
    reset_scene()
    legacy_obj, cap_faces, all_faces, interior_index, expected_centers = (
        make_nonradial_triangulated_tube("LegacyNonRadialMetadata")
    )
    select_faces(legacy_obj, cap_faces)
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("Legacy non-radial cap capture failed")
    select_faces(legacy_obj, all_faces)
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("Legacy non-radial cap build failed")
    legacy_settings = bpy.context.window_manager.character_designer
    legacy_output = legacy_settings.output_object
    assert_plain_poly_curve(legacy_output, expected_centers)
    legacy_metadata = read_cross_section_metadata(legacy_output)
    legacy_layers = json.loads(legacy_output["character_designer_layer_vertices"])

    reset_scene()
    auto_obj, _cap_faces, auto_all_faces, auto_interior_index, auto_centers = (
        make_nonradial_triangulated_tube("AutoNonRadialMetadata")
    )
    if auto_interior_index != interior_index or auto_centers != expected_centers:
        raise AssertionError("Legacy and AUTO non-radial fixtures are not identical")
    select_faces(auto_obj, auto_all_faces)
    settings = bpy.context.window_manager.character_designer
    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        expected_layers = (tuple(range(12)), tuple(range(12, 24)))
        flattened_layers = {
            index
            for layer in character_designer._LIVE_PREVIEW_LAYERS
            for index in layer
        }
        if (
            not settings.preview_valid
            or not settings.preview_confirmable
            or settings.preview_layer_count != 2
            or character_designer._LIVE_PREVIEW_LAYERS != expected_layers
            or auto_interior_index in flattened_layers
        ):
            raise AssertionError(
                "AUTO did not reduce the non-radial triangulated cap to its two semantic loops, "
                f"got {character_designer._LIVE_PREVIEW_LAYERS}: {settings.preview_message}"
            )

        preview_metadata = json.loads(character_designer._LIVE_PREVIEW_METADATA_JSON)
        if preview_metadata != legacy_metadata:
            differing_keys = sorted(
                key
                for key in set(preview_metadata) | set(legacy_metadata)
                if preview_metadata.get(key) != legacy_metadata.get(key)
            )
            raise AssertionError(
                "AUTO preview metadata differs from legacy cap capture metadata at "
                f"{differing_keys}"
            )
        if not bpy.ops.character_designer.confirm_centerline.poll():
            raise AssertionError("AUTO non-radial semantic cap must enable Confirm")
        if bpy.ops.character_designer.confirm_centerline() != {"FINISHED"}:
            raise AssertionError("AUTO non-radial semantic cap Confirm failed")
        auto_output = settings.output_object
        assert_plain_poly_curve(auto_output, auto_centers)
        auto_layers = json.loads(auto_output["character_designer_layer_vertices"])
        auto_metadata = read_cross_section_metadata(auto_output)
        if auto_layers != legacy_layers or auto_metadata != legacy_metadata:
            raise AssertionError(
                "Confirmed AUTO output is not metadata-equivalent to legacy cap capture"
            )
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_auto_exact_centered_radial_hub_is_ignored():
    reset_scene()
    obj, _cap_faces, all_faces, hub_index, expected_centers = make_radial_fan_tube(
        "AutoCenteredRadialHub",
        hub_offset=(0.0, 0.0),
    )
    assert_vector_close(obj.data.vertices[hub_index].co, expected_centers[0])
    select_faces(obj, all_faces)
    settings = bpy.context.window_manager.character_designer
    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        expected_layers = (tuple(range(12)), tuple(range(12, 24)))
        flattened_layers = {
            index
            for layer in character_designer._LIVE_PREVIEW_LAYERS
            for index in layer
        }
        if (
            not settings.preview_valid
            or not settings.preview_confirmable
            or settings.preview_layer_count != 2
            or character_designer._LIVE_PREVIEW_LAYERS != expected_layers
            or hub_index in flattened_layers
        ):
            raise AssertionError(
                "An exactly centered radial hub became a zero-length centerline layer, "
                f"got {character_designer._LIVE_PREVIEW_LAYERS}: {settings.preview_message}"
            )
        for actual, expected in zip(
            character_designer._LIVE_PREVIEW_WORLD_POINTS,
            expected_centers,
        ):
            assert_vector_close(actual, obj.matrix_world @ expected)
        if not bpy.ops.character_designer.confirm_centerline.poll():
            raise AssertionError("A centered radial semantic cap+band must enable Confirm")
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_auto_single_quad_active_endpoint_edge_builds_two_points():
    reset_scene()
    vertices = (
        (-0.80, -0.10, 0.00),
        (0.80, -0.10, 0.00),
        (-0.55, 0.02, 0.50),
        (0.85, 0.02, 0.50),
    )
    obj = make_mesh_object("AutoSingleQuad", vertices, ((0, 1, 3, 2),))
    select_faces(obj, (0,))
    bm = bmesh.from_edit_mesh(obj.data)
    active_edge = next(
        edge
        for edge in bm.edges
        if tuple(sorted(vertex.index for vertex in edge.verts)) == (0, 1)
    )
    bm.select_history.clear()
    bm.select_history.add(active_edge)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    counts = (
        sum(vertex.select for vertex in bm.verts),
        sum(edge.select for edge in bm.edges),
        sum(face.select for face in bm.faces),
    )
    if counts != (4, 4, 1):
        raise AssertionError(f"Single-quad AUTO fixture must be 4V/4E/1F, got {counts}")

    expected_layers = ((0, 1), (2, 3))
    expected_centers = (
        (Vector(vertices[0]) + Vector(vertices[1])) * 0.5,
        (Vector(vertices[2]) + Vector(vertices[3])) * 0.5,
    )
    settings = bpy.context.window_manager.character_designer
    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        if (
            not settings.preview_valid
            or not settings.preview_confirmable
            or settings.preview_layer_count != 2
            or character_designer._LIVE_PREVIEW_LAYERS != expected_layers
            or settings.root_kind != "OPEN"
        ):
            raise AssertionError(
                "The active endpoint edge did not disambiguate the full-selected single quad, "
                f"got {character_designer._LIVE_PREVIEW_LAYERS}: {settings.preview_message}"
            )
        for actual, expected in zip(
            character_designer._LIVE_PREVIEW_WORLD_POINTS,
            expected_centers,
        ):
            assert_vector_close(actual, obj.matrix_world @ expected)
        if not bpy.ops.character_designer.confirm_centerline.poll():
            raise AssertionError("An active endpoint edge on a single quad must enable Confirm")
        if bpy.ops.character_designer.confirm_centerline() != {"FINISHED"}:
            raise AssertionError("Single-quad AUTO Confirm failed")
        assert_plain_poly_curve(settings.output_object, expected_centers)
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_auto_fully_capped_three_by_four_tube_uses_active_cap():
    reset_scene()
    obj, cap_face_indices, expected_centers = make_three_by_four_closed_tube(
        "AutoFullyCapped3x4"
    )
    select_faces(obj, tuple(range(len(obj.data.polygons))))
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.select_history.clear()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    counts = (
        sum(vertex.select for vertex in bm.verts),
        sum(edge.select for edge in bm.edges),
        sum(face.select for face in bm.faces),
    )
    if counts != (12, 20, 10):
        raise AssertionError(f"Fully capped 3x4 tube must be 12V/20E/10F, got {counts}")

    settings = bpy.context.window_manager.character_designer
    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        if (
            not settings.preview_valid
            or settings.preview_layer_count != 3
            or settings.preview_confirmable
            or bpy.ops.character_designer.confirm_centerline.poll()
        ):
            raise AssertionError(
                "A fully capped tube without an active end should preview all centers but remain "
                f"direction-ambiguous: {settings.preview_message}"
            )

        start_cap_index, _end_cap_index = cap_face_indices
        revision_before_hint = settings.preview_revision
        bm.select_history.add(bm.faces[start_cap_index])
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        character_designer._live_preview_tick()

        expected_layers = tuple(
            tuple(range(layer_start, layer_start + 4))
            for layer_start in range(0, 12, 4)
        )
        if (
            not settings.preview_valid
            or not settings.preview_confirmable
            or settings.preview_layer_count != 3
            or settings.preview_revision <= revision_before_hint
            or character_designer._LIVE_PREVIEW_LAYERS != expected_layers
        ):
            raise AssertionError(
                "The active start-cap face did not resolve the fully capped tube direction, "
                f"got {character_designer._LIVE_PREVIEW_LAYERS}: {settings.preview_message}"
            )
        for actual, expected in zip(
            character_designer._LIVE_PREVIEW_WORLD_POINTS,
            expected_centers,
        ):
            assert_vector_close(actual, obj.matrix_world @ expected)
        if not bpy.ops.character_designer.confirm_centerline.poll():
            raise AssertionError("An active endpoint cap must enable Confirm for the 3x4 tube")
        if bpy.ops.character_designer.confirm_centerline() != {"FINISHED"}:
            raise AssertionError("Fully capped 3x4 AUTO Confirm failed")
        assert_plain_poly_curve(settings.output_object, expected_centers)
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_auto_sparse_identity_audit_refreshes_equal_count_selection():
    reset_scene()
    obj, _tip_index, expected_centers = make_four_triangle_loops_and_point(
        "AutoSparseSelectionAudit"
    )
    first_indices = tuple(range(9))
    second_indices = set(range(3, 12))
    select_vertices(obj, first_indices)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.select_history.clear()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    settings = bpy.context.window_manager.character_designer

    try:
        settings.live_preview_enabled = True
        character_designer._live_preview_tick()
        first_layers = (
            (0, 1, 2),
            (3, 4, 5),
            (6, 7, 8),
        )
        if (
            not settings.preview_valid
            or settings.preview_layer_count != 3
            or character_designer._LIVE_PREVIEW_LAYERS != first_layers
        ):
            raise AssertionError(
                "Sparse-audit fixture did not publish its first three loop centers"
            )

        counts_before = (
            obj.data.total_vert_sel,
            obj.data.total_edge_sel,
            obj.data.total_face_sel,
        )
        fast_signature_before = character_designer._auto_preview_input_signature(bpy.context)
        identity_before = character_designer._selection_identity_digest(obj)
        input_signature_before = character_designer._LIVE_PREVIEW_INPUT_SIGNATURE
        selection_digest_before = character_designer._LIVE_PREVIEW_SELECTION_DIGEST
        if (
            input_signature_before != fast_signature_before
            or selection_digest_before != identity_before
        ):
            raise AssertionError("AUTO preview did not seed its sparse selection audit state")
        depsgraph_revision_before = character_designer._LIVE_PREVIEW_DEPSGRAPH_REVISION
        preview_revision_before = settings.preview_revision
        stale_layers = character_designer._LIVE_PREVIEW_LAYERS
        stale_points = character_designer._LIVE_PREVIEW_WORLD_POINTS
        character_designer._LIVE_PREVIEW_SELECTION_AUDIT_AT = float("inf")

        for face in bm.faces:
            face.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        for vertex in bm.verts:
            vertex.select_set(vertex.index in second_indices)
        bm.select_history.clear()
        bm.select_flush_mode()
        # Deliberately omit update_edit_mesh: this models a missed selection
        # notification, which is exactly what the sparse identity audit covers.

        counts_after = (
            obj.data.total_vert_sel,
            obj.data.total_edge_sel,
            obj.data.total_face_sel,
        )
        fast_signature_after = character_designer._auto_preview_input_signature(bpy.context)
        identity_after = character_designer._selection_identity_digest(obj)
        if (
            counts_after != counts_before
            or fast_signature_after != fast_signature_before
            or character_designer._LIVE_PREVIEW_DEPSGRAPH_REVISION
            != depsgraph_revision_before
        ):
            raise AssertionError(
                "The replacement selection did not preserve the AUTO O(1) fast signature: "
                f"{counts_before} -> {counts_after}"
            )
        if identity_after == identity_before:
            raise AssertionError(
                "The sparse identity digest ignored equal-count vertex replacement"
            )

        if character_designer._live_preview_tick() != character_designer.LIVE_PREVIEW_INTERVAL:
            raise AssertionError("An unchanged fast tick unexpectedly stopped AUTO preview")
        if (
            settings.preview_revision != preview_revision_before
            or character_designer._LIVE_PREVIEW_INPUT_SIGNATURE != input_signature_before
            or character_designer._LIVE_PREVIEW_SELECTION_DIGEST != selection_digest_before
            or character_designer._LIVE_PREVIEW_LAYERS != stale_layers
            or character_designer._LIVE_PREVIEW_WORLD_POINTS != stale_points
        ):
            raise AssertionError(
                "The not-yet-due sparse audit refreshed the deliberately stale cache"
            )

        character_designer._LIVE_PREVIEW_SELECTION_AUDIT_AT = 0.0
        character_designer._live_preview_tick()
        expected_layers = (
            (3, 4, 5),
            (6, 7, 8),
            (9, 10, 11),
        )
        if (
            not settings.preview_valid
            or settings.preview_layer_count != 3
            or settings.preview_revision <= preview_revision_before
            or character_designer._LIVE_PREVIEW_SELECTION_DIGEST != identity_after
            or character_designer._LIVE_PREVIEW_LAYERS != expected_layers
            or character_designer._LIVE_PREVIEW_SELECTION_AUDIT_AT <= 0.0
        ):
            raise AssertionError(
                "The due sparse identity audit did not refresh an equal-count selection replacement"
            )
        for actual, expected in zip(
            character_designer._LIVE_PREVIEW_WORLD_POINTS,
            expected_centers[1:4],
        ):
            assert_vector_close(actual, obj.matrix_world @ expected)
    finally:
        if settings.live_preview_enabled:
            settings.live_preview_enabled = False


def test_live_preview_root_only_and_four_world_layers():
    reset_scene()
    obj, _forward_root, expected = make_closed_point_tip("LivePreviewRootOnly")
    tip_index = len(obj.data.vertices) - 1
    select_vertices(obj, (tip_index,))

    if bpy.ops.character_designer.preview_selected_root() != {"FINISHED"}:
        raise AssertionError("POINT-root live preview did not start")
    settings = bpy.context.window_manager.character_designer
    if not settings.preview_active or not settings.preview_valid:
        raise AssertionError("Root-only preview is not active and valid")
    if settings.preview_confirmable or settings.preview_layer_count != 1:
        raise AssertionError("Root-only preview must contain one non-confirmable layer")
    if bpy.ops.character_designer.confirm_centerline.poll():
        raise AssertionError("Confirm must remain disabled while only the root is selected")
    if character_designer._LIVE_PREVIEW_LAYERS != ((tip_index,),):
        raise AssertionError("Root-only preview did not preserve the endpoint layer")
    if len(character_designer._LIVE_PREVIEW_WORLD_POINTS) != 1:
        raise AssertionError("Root-only preview did not publish one world-space point")
    assert_vector_close(
        character_designer._LIVE_PREVIEW_WORLD_POINTS[0],
        obj.matrix_world @ expected[-1],
    )

    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    if not character_designer._update_live_preview(bpy.context, force=True):
        raise AssertionError("Forced preview update did not publish the grown selection")
    if (
        not settings.preview_active
        or not settings.preview_valid
        or not settings.preview_confirmable
        or settings.preview_layer_count != 4
        or not settings.preview_orientation_valid
    ):
        raise AssertionError("Grown POINT-root preview is not a valid four-layer result")
    if not bpy.ops.character_designer.confirm_centerline.poll():
        raise AssertionError("Confirm should be enabled for a valid four-layer preview")

    reverse_expected = tuple(reversed(expected))
    if len(character_designer._LIVE_PREVIEW_WORLD_POINTS) != len(reverse_expected):
        raise AssertionError("Live preview did not publish four world-space centers")
    for actual, local_center in zip(
        character_designer._LIVE_PREVIEW_WORLD_POINTS,
        reverse_expected,
    ):
        assert_vector_close(actual, obj.matrix_world @ local_center)
    character_designer._stop_live_preview(settings, clear_capture=True)


def test_live_preview_tracks_geometry_and_object_matrix():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "LivePreviewDependencies",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.5), (0.0, 0.0, 1.0)),
    )
    select_vertices(obj, root)
    if bpy.ops.character_designer.preview_selected_root() != {"FINISHED"}:
        raise AssertionError("Dependency-tracking preview did not start")
    settings = bpy.context.window_manager.character_designer
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    character_designer._update_live_preview(bpy.context, force=True)

    revision_before_move = settings.preview_revision
    points_before_move = character_designer._LIVE_PREVIEW_WORLD_POINTS
    selected_before = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.x += 0.4
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    if not character_designer._update_live_preview(bpy.context, force=False):
        raise AssertionError("A coordinate edit with unchanged selection did not refresh preview")
    points_after_move = character_designer._LIVE_PREVIEW_WORLD_POINTS
    if settings.preview_revision <= revision_before_move or points_after_move == points_before_move:
        raise AssertionError("Coordinate edit did not advance preview revision/world cache")
    selected_after = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    if selected_before != selected_after:
        raise AssertionError("Dependency tracking changed the source selection")

    revision_before_matrix = settings.preview_revision
    translation = Vector((2.0, -3.0, 1.25))
    obj.matrix_world = Matrix.Translation(translation) @ obj.matrix_world
    if not character_designer._update_live_preview(bpy.context, force=False):
        raise AssertionError("An object-matrix edit with unchanged selection did not refresh preview")
    points_after_matrix = character_designer._LIVE_PREVIEW_WORLD_POINTS
    if settings.preview_revision <= revision_before_matrix:
        raise AssertionError("Object-matrix edit did not advance preview revision")
    for before, after in zip(points_after_move, points_after_matrix):
        assert_vector_close(Vector(after) - Vector(before), translation)

    stable_revision = settings.preview_revision
    if character_designer._update_live_preview(bpy.context, force=False):
        raise AssertionError("Unchanged preview inputs should not publish another revision")
    if settings.preview_revision != stable_revision:
        raise AssertionError("Unchanged preview inputs advanced the revision")
    character_designer._stop_live_preview(settings, clear_capture=True)


def test_live_preview_confirm_recomputes_latest_geometry():
    reset_scene()
    obj, root, expected = make_open_grid(
        "LivePreviewFreshConfirm",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.5), (0.0, 0.0, 1.0)),
    )
    select_vertices(obj, root)
    if bpy.ops.character_designer.preview_selected_root() != {"FINISHED"}:
        raise AssertionError("Fresh-confirm preview did not start")
    settings = bpy.context.window_manager.character_designer
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    if character_designer._live_preview_tick() != character_designer.LIVE_PREVIEW_INTERVAL:
        raise AssertionError("Live-preview tick did not remain scheduled after publishing")
    if not settings.preview_confirmable or settings.preview_layer_count != 3:
        raise AssertionError("Live-preview tick did not publish the grown three-layer selection")
    stale_preview_points = character_designer._LIVE_PREVIEW_WORLD_POINTS
    stale_revision = settings.preview_revision

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for vertex_index in (6, 7, 8):
        bm.verts[vertex_index].co.x += 0.3
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    if character_designer._LIVE_PREVIEW_WORLD_POINTS != stale_preview_points:
        raise AssertionError("The test did not leave a deliberately stale preview cache")
    if settings.preview_revision != stale_revision:
        raise AssertionError("Geometry changed the preview before an explicit update")

    if bpy.ops.character_designer.confirm_centerline() != {"FINISHED"}:
        raise AssertionError("Confirm failed to recompute the latest Edit BMesh")
    expected_latest = list(expected)
    expected_latest[-1] = expected_latest[-1] + Vector((0.3, 0.0, 0.0))
    output = settings.output_object
    assert_plain_poly_curve(output, expected_latest)
    confirmed_world = output.matrix_world @ Vector(output.data.splines[0].points[-1].co.xyz)
    if (Vector(stale_preview_points[-1]) - confirmed_world).length <= TOLERANCE:
        raise AssertionError("Confirm appears to have committed the stale preview cache")
    if (
        settings.preview_active
        or settings.preview_valid
        or settings.preview_confirmable
        or settings.preview_layer_count != 0
        or character_designer._LIVE_PREVIEW_WORLD_POINTS
        or character_designer._LIVE_PREVIEW_LAYERS
        or character_designer._LIVE_PREVIEW_DRAW_HANDLE is not None
        or bpy.app.timers.is_registered(character_designer._live_preview_tick)
    ):
        raise AssertionError("Confirm did not fully clean the live-preview session")


def test_live_preview_invalid_selection_clears_and_recovers():
    reset_scene()
    obj, root, expected = make_open_grid(
        "LivePreviewInvalidRecovery",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.5), (0.0, 0.0, 1.0)),
    )
    select_vertices(obj, root)
    if bpy.ops.character_designer.preview_selected_root() != {"FINISHED"}:
        raise AssertionError("Invalid-recovery preview did not start")
    settings = bpy.context.window_manager.character_designer
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    character_designer._update_live_preview(bpy.context, force=True)
    if not settings.preview_valid or len(character_designer._LIVE_PREVIEW_WORLD_POINTS) != 3:
        raise AssertionError("Valid preview fixture did not publish its initial three points")

    revision_before_invalid = settings.preview_revision
    select_vertices(obj, (*root, 6, 7, 8))
    if not character_designer._update_live_preview(bpy.context, force=False):
        raise AssertionError("Invalid selection did not refresh preview state")
    if (
        settings.preview_valid
        or settings.preview_confirmable
        or settings.preview_layer_count != 0
        or character_designer._LIVE_PREVIEW_WORLD_POINTS
        or character_designer._LIVE_PREVIEW_LAYERS
        or settings.preview_revision <= revision_before_invalid
    ):
        raise AssertionError("Invalid selection left stale valid preview geometry")
    if "disconnected" not in settings.preview_message.lower():
        raise AssertionError(f"Invalid preview error was unclear: {settings.preview_message}")

    revision_before_recovery = settings.preview_revision
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    if not character_designer._update_live_preview(bpy.context, force=False):
        raise AssertionError("A repaired selection did not recover live preview")
    if (
        not settings.preview_valid
        or not settings.preview_confirmable
        or settings.preview_layer_count != 3
        or settings.preview_revision <= revision_before_recovery
    ):
        raise AssertionError("Live preview did not recover after repairing the selection")
    for actual, local_center in zip(character_designer._LIVE_PREVIEW_WORLD_POINTS, expected):
        assert_vector_close(actual, obj.matrix_world @ local_center)
    character_designer._stop_live_preview(settings, clear_capture=True)


def test_live_preview_topology_latch_and_fake_depsgraph_recovery():
    reset_scene()
    vertices = (
        (-1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (-1.0, 0.0, 0.5),
        (0.0, 0.0, 0.5),
        (1.0, 0.0, 0.5),
        (10.0, 0.0, 0.0),
        (11.0, 0.0, 0.0),
        (10.0, 1.0, 0.0),
        (11.0, 1.0, 0.0),
    )
    faces = ((0, 1, 4, 3), (1, 2, 5, 4))
    original_remote_edges = ((6, 7), (8, 9))
    rewired_remote_edges = ((6, 8), (7, 9))
    mesh = bpy.data.meshes.new("PreviewTopologyLatch_Mesh")
    mesh.from_pydata(vertices, original_remote_edges, faces)
    mesh.update()
    obj = bpy.data.objects.new("PreviewTopologyLatch", mesh)
    bpy.context.scene.collection.objects.link(obj)

    def replace_remote_edges(edge_keys):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        remote_indices = {6, 7, 8, 9}
        remote_vertices = {index: bm.verts[index] for index in remote_indices}
        remote_edges = [
            edge
            for edge in bm.edges
            if all(vertex.index in remote_indices for vertex in edge.verts)
        ]
        if len(remote_edges) != 2:
            raise AssertionError(f"Expected two remote loose edges, got {len(remote_edges)}")
        for edge in remote_edges:
            bm.edges.remove(edge)
        for first, second in edge_keys:
            bm.edges.new((remote_vertices[first], remote_vertices[second]))
        bm.edges.index_update()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)

    def notify_fake_mesh_geometry_update():
        revision_before = character_designer._LIVE_PREVIEW_DEPSGRAPH_REVISION
        fake_updates = (
            SimpleNamespace(
                id=SimpleNamespace(original=obj),
                is_updated_geometry=False,
            ),
            SimpleNamespace(
                id=SimpleNamespace(original=obj.data),
                is_updated_geometry=True,
            ),
        )
        character_designer._live_preview_depsgraph_update(
            None,
            SimpleNamespace(updates=fake_updates),
        )
        if character_designer._LIVE_PREVIEW_DEPSGRAPH_REVISION <= revision_before:
            raise AssertionError("The tracked fake Mesh update did not advance its revision")
        if (
            not character_designer._LIVE_PREVIEW_TOPOLOGY_DIRTY
            or character_designer._LIVE_PREVIEW_TOPOLOGY_AUDIT_AT <= 0.0
        ):
            raise AssertionError("The tracked fake Mesh update did not schedule an audit")

    select_vertices(obj, (0, 1, 2))
    if bpy.ops.character_designer.preview_selected_root() != {"FINISHED"}:
        raise AssertionError("Topology-latch preview did not start")
    settings = bpy.context.window_manager.character_designer
    select_vertices(obj, tuple(range(6)))
    character_designer._update_live_preview(bpy.context, force=True)
    if not settings.preview_valid or not settings.preview_confirmable:
        raise AssertionError("Topology-latch fixture did not publish a valid preview")
    captured_counts = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons))

    replace_remote_edges(rewired_remote_edges)
    if (len(mesh.vertices), len(mesh.edges), len(mesh.polygons)) != captured_counts:
        raise AssertionError("The remote rewire did not preserve topology counts")
    notify_fake_mesh_geometry_update()
    character_designer._LIVE_PREVIEW_TOPOLOGY_AUDIT_AT = 0.0
    if character_designer._live_preview_tick() != character_designer.LIVE_PREVIEW_INTERVAL:
        raise AssertionError("Topology audit unexpectedly stopped the live-preview timer")
    if (
        settings.preview_valid
        or settings.preview_confirmable
        or character_designer._LIVE_PREVIEW_WORLD_POINTS
        or character_designer._LIVE_PREVIEW_LAYERS
        or character_designer._LIVE_PREVIEW_TOPOLOGY_VERIFIED
    ):
        raise AssertionError("An equal-count remote rewire did not invalidate preview atomically")
    if "topology changed" not in settings.preview_message.lower():
        raise AssertionError(f"Topology mismatch error was unclear: {settings.preview_message}")

    invalid_revision = settings.preview_revision
    invalid_signature = character_designer._LIVE_PREVIEW_INPUT_SIGNATURE
    character_designer._live_preview_tick()
    if (
        settings.preview_valid
        or settings.preview_confirmable
        or settings.preview_revision != invalid_revision
        or character_designer._LIVE_PREVIEW_INPUT_SIGNATURE != invalid_signature
        or character_designer._LIVE_PREVIEW_WORLD_POINTS
    ):
        raise AssertionError("An unchanged fast tick revived a known-invalid topology")

    replace_remote_edges(original_remote_edges)
    restored_bm = bmesh.from_edit_mesh(obj.data)
    if character_designer._topology_signature(restored_bm) != settings.topology_signature:
        raise AssertionError("Canonical digest rejected restored topology after edge reordering")
    if character_designer._LIVE_PREVIEW_TRACKED_SOURCE_KEY is None:
        raise AssertionError("Invalid preview stopped tracking its captured source")
    notify_fake_mesh_geometry_update()
    character_designer._LIVE_PREVIEW_TOPOLOGY_AUDIT_AT = 0.0
    character_designer._live_preview_tick()
    if (
        not settings.preview_valid
        or not settings.preview_confirmable
        or settings.preview_layer_count != 2
        or not character_designer._LIVE_PREVIEW_TOPOLOGY_VERIFIED
        or len(character_designer._LIVE_PREVIEW_WORLD_POINTS) != 2
    ):
        raise AssertionError("A fake tracked update did not recover restored topology")
    character_designer._stop_live_preview(settings, clear_capture=True)


def test_topology_signature_is_canonical_but_winding_sensitive():
    reset_scene()
    vertices = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (2.0, 0.0, 0.0),
        (3.0, 0.0, 0.0),
        (3.0, 1.0, 0.0),
        (2.0, 1.0, 0.0),
    )
    canonical = make_mesh_object(
        "TopologyCanonical",
        vertices,
        ((0, 1, 2, 3), (4, 5, 6, 7)),
    )
    reordered = make_mesh_object(
        "TopologyReordered",
        vertices,
        ((6, 7, 4, 5), (2, 3, 0, 1)),
    )
    reversed_winding = make_mesh_object(
        "TopologyReversed",
        vertices,
        ((0, 3, 2, 1), (4, 5, 6, 7)),
    )

    signatures = []
    raw_orders = []
    for obj in (canonical, reordered, reversed_winding):
        select_vertices(obj, ())
        bm = bmesh.from_edit_mesh(obj.data)
        raw_orders.append(
            (
                tuple(
                    (edge.verts[0].index, edge.verts[1].index)
                    for edge in bm.edges
                ),
                tuple(
                    tuple(vertex.index for vertex in face.verts)
                    for face in bm.faces
                ),
            )
        )
        signatures.append(character_designer._topology_signature(bm))
    if raw_orders[0] == raw_orders[1]:
        raise AssertionError("Canonical-order fixture did not change raw BMesh order")
    if signatures[0] != signatures[1]:
        raise AssertionError("Digest changed with face order or same-winding loop rotation")
    if signatures[0] == signatures[2]:
        raise AssertionError("Digest ignored a reversed polygon winding")


def test_live_preview_lifecycle_cleanup_is_idempotent():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "LivePreviewLifecycle",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.5)),
    )
    select_vertices(obj, root)
    if bpy.ops.character_designer.preview_selected_root() != {"FINISHED"}:
        raise AssertionError("Lifecycle preview did not start")
    settings = bpy.context.window_manager.character_designer
    if (
        not bpy.app.timers.is_registered(character_designer._live_preview_tick)
        or character_designer._LIVE_PREVIEW_DRAW_HANDLE is None
        or character_designer._live_preview_load_pre not in bpy.app.handlers.load_pre
    ):
        raise AssertionError("Preview runtime was not fully registered")

    if bpy.ops.character_designer.cancel_preview() != {"FINISHED"}:
        raise AssertionError("Cancel Preview failed")
    if (
        settings.preview_active
        or character_designer._LIVE_PREVIEW_DRAW_HANDLE is not None
        or bpy.app.timers.is_registered(character_designer._live_preview_tick)
        or character_designer._live_preview_load_pre in bpy.app.handlers.load_pre
        or character_designer._LIVE_PREVIEW_WORLD_POINTS
    ):
        raise AssertionError("Cancel Preview left live runtime state")
    character_designer._stop_live_preview(settings, clear_capture=True)
    character_designer._stop_live_preview(settings, clear_capture=True)

    select_vertices(obj, root)
    if bpy.ops.character_designer.preview_selected_root() != {"FINISHED"}:
        raise AssertionError("Preview could not restart after repeated stop")
    selected_before = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    character_designer.unregister()
    if (
        hasattr(bpy.types.WindowManager, "character_designer")
        or character_designer._LIVE_PREVIEW_DRAW_HANDLE is not None
        or bpy.app.timers.is_registered(character_designer._live_preview_tick)
        or character_designer._live_preview_load_pre in bpy.app.handlers.load_pre
    ):
        raise AssertionError("Unregister left live-preview runtime state")
    character_designer.register()
    if not hasattr(bpy.types.WindowManager, "character_designer"):
        raise AssertionError("Add-on did not register again after preview cleanup")
    if (
        character_designer._LIVE_PREVIEW_DRAW_HANDLE is not None
        or bpy.app.timers.is_registered(character_designer._live_preview_tick)
        or character_designer._live_preview_load_pre in bpy.app.handlers.load_pre
    ):
        raise AssertionError("Re-register resurrected stale preview runtime")
    selected_after = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    if bpy.context.mode != "EDIT_MESH" or selected_before != selected_after:
        raise AssertionError("Preview lifecycle cleanup changed Edit Mode or selection")


def test_triangulated_fan_cap_uses_only_its_boundary():
    reset_scene()
    obj, cap_faces, grown_faces, hub_index, expected = make_radial_fan_tube(
        "TriangulatedFanCap"
    )
    select_faces(obj, cap_faces)
    bm = bmesh.from_edit_mesh(obj.data)
    counts = (
        sum(vertex.select for vertex in bm.verts),
        sum(edge.select for edge in bm.edges),
        sum(face.select for face in bm.faces),
    )
    if counts != (13, 24, 12):
        raise AssertionError(f"Fan-cap regression fixture must be 13V/24E/12F, got {counts}")
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("A selected radial triangle-fan cap was not captured")

    settings = bpy.context.window_manager.character_designer
    root_indices = set(character_designer._indices_from_settings(settings))
    ignored_indices = set(character_designer._ignored_indices_from_settings(settings))
    if len(root_indices) != 12 or ignored_indices != {hub_index}:
        raise AssertionError("Fan capture did not separate its 12-vert boundary from the hub")
    try:
        character_designer._extract_layers(bpy.context, settings)
    except character_designer.CenterlineError as exc:
        if "Only the root is selected" not in str(exc):
            raise
    else:
        raise AssertionError("The selected fan hub was incorrectly treated as a second layer")

    select_faces(obj, grown_faces)
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("The grown fan-cap tube did not build")
    output = settings.output_object
    assert_plain_poly_curve(output, expected)


def test_nonradial_triangulated_cap_uses_its_single_boundary():
    reset_scene()
    obj, cap_faces, grown_faces, interior_index, expected = make_nonradial_triangulated_tube(
        "NonRadialTriangulatedCap"
    )
    select_faces(obj, cap_faces)
    bm = bmesh.from_edit_mesh(obj.data)
    counts = (
        sum(vertex.select for vertex in bm.verts),
        sum(edge.select for edge in bm.edges),
        sum(face.select for face in bm.faces),
    )
    if counts != (13, 24, 12):
        raise AssertionError(f"Non-radial cap fixture must be 13V/24E/12F, got {counts}")
    if all(interior_index in {vertex.index for vertex in face.verts} for face in bm.faces if face.select):
        raise AssertionError("Non-radial cap fixture accidentally became a shared-center fan")
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("A non-radial triangulated cap was not captured")

    settings = bpy.context.window_manager.character_designer
    root_indices = set(character_designer._indices_from_settings(settings))
    ignored_indices = set(character_designer._ignored_indices_from_settings(settings))
    if len(root_indices) != 12 or ignored_indices != {interior_index}:
        raise AssertionError("Non-radial capture did not isolate its boundary and interior")
    if settings.root_capture_mode != "REGION_CAP":
        raise AssertionError("A generic triangulated cap was recorded with the wrong capture mode")

    select_faces(obj, grown_faces)
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("The non-radial triangulated-cap tube did not build")
    assert_plain_poly_curve(settings.output_object, expected)


def test_whole_triangulated_open_card_is_not_a_cap():
    reset_scene()
    obj, all_faces = make_triangulated_open_card("WholeTriangulatedCard")
    select_faces(obj, all_faces)
    settings = bpy.context.window_manager.character_designer
    settings.root_object = None
    try:
        result = bpy.ops.character_designer.capture_root_slice()
    except RuntimeError as exc:
        if "regular first collar" not in str(exc):
            raise
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("A whole triangulated open card must not be captured as a cap")
    if settings.root_object is not None or settings.last_level != "ERROR":
        raise AssertionError("Rejected triangulated card polluted root state or hid its error")


def test_mixed_polygon_cap_uses_regular_first_collar():
    reset_scene()
    obj, cap_faces, grown_faces, interior_index, expected = make_mixed_polygon_cap_tube(
        "MixedPolygonCap"
    )
    select_faces(obj, cap_faces)
    bm = bmesh.from_edit_mesh(obj.data)
    selected_sizes = {len(face.verts) for face in bm.faces if face.select}
    if selected_sizes != {3, 4, 5}:
        raise AssertionError(f"Mixed cap fixture must contain triangle/quad/ngon, got {selected_sizes}")
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("A mixed-polygon cap with a regular collar was not captured")

    settings = bpy.context.window_manager.character_designer
    root_indices = set(character_designer._indices_from_settings(settings))
    ignored_indices = set(character_designer._ignored_indices_from_settings(settings))
    if len(root_indices) != 6 or ignored_indices != {interior_index}:
        raise AssertionError("Mixed-polygon capture did not isolate its boundary and interior")

    select_faces(obj, grown_faces)
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("The mixed-polygon cap tube did not build")
    assert_plain_poly_curve(settings.output_object, expected)


def test_single_side_quad_is_not_a_cap():
    reset_scene()
    obj, _root, _expected = make_closed_point_tip("SingleSideQuad")
    select_faces(obj, (1,))
    settings = bpy.context.window_manager.character_designer
    settings.root_object = None
    try:
        result = bpy.ops.character_designer.capture_root_slice()
    except RuntimeError as exc:
        if "regular first collar" not in str(exc):
            raise
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("One arbitrary tube side quad must not be captured as a cap")
    if settings.root_object is not None or settings.last_level != "ERROR":
        raise AssertionError("Rejected side quad polluted root state or hid its error")


def test_cap_may_have_uniform_collapsed_first_collar():
    reset_scene()
    obj, cap_faces, grown_faces, expected = make_cap_with_collapsed_first_collar(
        "CollapsedFirstCollar"
    )
    select_faces(obj, cap_faces)
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("A cap with a uniform triangle-collapse collar was not captured")
    settings = bpy.context.window_manager.character_designer
    select_faces(obj, grown_faces)
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("A cap with a uniform point-tip collar did not build")
    assert_plain_poly_curve(settings.output_object, expected)


def test_large_world_transform_keeps_local_precision():
    reset_scene()
    centers = (
        (0.000, 0.00, 0.00),
        (0.011, 0.02, 0.30),
        (0.024, 0.05, 0.67),
    )
    obj, root, expected = make_open_grid(
        "LargeWorld",
        centers,
        column_offsets=(-0.03, 0.0, 0.03),
    )
    transform = (
        Matrix.Translation((1_000_000.0, -720_000.0, 250_000.0))
        @ Matrix.Rotation(math.radians(31.0), 4, "Z")
        @ Matrix.Diagonal((1.7, 0.6, 2.3, 1.0))
    )
    obj.matrix_world = transform
    output = capture_then_build(obj, root)
    assert_plain_poly_curve(output, expected)
    assert_matrix_close(output.matrix_world, transform, tolerance=2.0e-3)
    local_points = [Vector(point.co.xyz) for point in output.data.splines[0].points]
    if abs((local_points[1] - local_points[0]).x - 0.011) > 1.0e-6:
        raise AssertionError("Large world coordinates destroyed local sub-centimeter precision")
    metadata = read_cross_section_metadata(output)
    for section in metadata["sections"]:
        if abs(section["max_width_local"] - 0.06) > TOLERANCE:
            raise AssertionError("Object transform contaminated source-local width metadata")
    stored_matrix = metadata["matrix_world_at_build"]
    for row in range(4):
        for column in range(4):
            actual = stored_matrix[row * 4 + column]
            if abs(actual - transform[row][column]) > 2.0e-3:
                raise AssertionError("Metadata source matrix snapshot is inaccurate")


def test_visible_collection_is_used():
    reset_scene()
    foreign_scene = bpy.data.scenes.new("ForeignScene")
    current_collection = bpy.data.collections.new("VisibleHair")
    bpy.context.scene.collection.children.link(current_collection)
    centers = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4), (0.0, 0.0, 0.8))
    obj, root, expected = make_open_grid(
        "MultiSceneHair",
        centers,
        collections=(foreign_scene.collection, current_collection),
    )
    output = capture_then_build(obj, root)
    assert_plain_poly_curve(output, expected)
    if current_collection not in output.users_collection:
        raise AssertionError("Output was not linked into the current visible collection")
    bpy.data.scenes.remove(foreign_scene)


def test_disconnected_selection_is_rejected_without_output():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "Disconnected",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4)),
    )
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.new((9.0, 9.0, 9.0))
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    select_vertices(obj, root)
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("Root capture failed")
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    object_names = set(bpy.data.objects.keys())
    curve_names = set(bpy.data.curves.keys())
    try:
        result = bpy.ops.character_designer.build_centerline()
    except RuntimeError as exc:
        if "disconnected from the root" not in str(exc):
            raise
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("Disconnected selection should be rejected")
    if object_names != set(bpy.data.objects.keys()) or curve_names != set(bpy.data.curves.keys()):
        raise AssertionError("A rejected build left output residue")


def test_nonfinite_layer_vertex_is_rejected_atomically():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "NonFiniteLayerVertex",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4), (0.0, 0.0, 0.8)),
    )
    select_vertices(obj, root)
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("Root capture failed for the non-finite geometry fixture")
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[4].co.x = float("nan")

    settings = bpy.context.window_manager.character_designer
    settings.output_object = None
    selected_before = {vertex.index for vertex in bm.verts if vertex.select}
    object_names = set(bpy.data.objects.keys())
    curve_names = set(bpy.data.curves.keys())
    try:
        result = bpy.ops.character_designer.build_centerline()
    except RuntimeError as exc:
        error = str(exc)
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("A non-finite layer vertex should be rejected")
        error = settings.last_message
    if "Layer 2 vertex 4 has non-finite coordinates" not in error:
        raise AssertionError(f"Non-finite geometry rejection was unclear: {error}")

    selected_after = {
        vertex.index for vertex in bmesh.from_edit_mesh(obj.data).verts if vertex.select
    }
    if (
        object_names != set(bpy.data.objects.keys())
        or curve_names != set(bpy.data.curves.keys())
        or settings.output_object is not None
    ):
        raise AssertionError("A non-finite geometry failure left output residue")
    if bpy.context.mode != "EDIT_MESH" or selected_before != selected_after:
        raise AssertionError("A non-finite geometry failure changed the source selection")


def test_branched_root_capture_is_rejected():
    reset_scene()
    obj, _root, _expected = make_open_grid(
        "BranchedRoot",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4), (0.0, 0.0, 0.8)),
    )
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    settings = bpy.context.window_manager.character_designer
    settings.root_object = None
    try:
        result = bpy.ops.character_designer.capture_root_slice()
    except RuntimeError as exc:
        if "regular first collar" not in str(exc):
            raise
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("A 2D grid must not be misidentified as one root slice")
    if settings.root_object is not None or settings.last_level != "ERROR":
        raise AssertionError("Failed capture polluted root state or hid its error")


def test_ambiguous_cross_layer_edges_are_rejected():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "AmbiguousBand",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4)),
    )
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.new((bm.verts[0], bm.verts[4]))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    select_vertices(obj, root)
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("Root capture failed")
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    try:
        result = bpy.ops.character_designer.build_centerline()
    except RuntimeError as exc:
        if "regular quad band" not in str(exc):
            raise
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("An ambiguous many-to-many band should be rejected")


def test_unselected_root_chord_is_ignored():
    reset_scene()
    obj, _root, expected = make_open_grid(
        "ExplicitRootEdges",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4), (0.0, 0.0, 0.8)),
    )
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.new((bm.verts[0], bm.verts[2]))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    select_edges(obj, ((0, 1), (1, 2)))
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("Explicit root edges were polluted by an unselected chord")
    select_vertices(obj, tuple(range(len(obj.data.vertices))))
    if bpy.ops.character_designer.build_centerline() != {"FINISHED"}:
        raise AssertionError("Stored root edges were not preserved after selection growth")
    output = bpy.context.window_manager.character_designer.output_object
    assert_plain_poly_curve(output, expected)


def test_missing_face_band_is_rejected():
    reset_scene()
    vertices = (
        (-1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (-1.0, 0.0, 0.5),
        (0.0, 0.0, 0.5),
        (1.0, 0.0, 0.5),
    )
    edges = ((0, 1), (1, 2), (3, 4), (4, 5), (0, 3), (1, 4), (2, 5))
    mesh = bpy.data.meshes.new("WireLadder_Mesh")
    mesh.from_pydata(vertices, edges, [])
    obj = bpy.data.objects.new("WireLadder", mesh)
    bpy.context.scene.collection.objects.link(obj)
    select_edges(obj, ((0, 1), (1, 2)))
    if bpy.ops.character_designer.capture_root_slice() != {"FINISHED"}:
        raise AssertionError("Wire root capture failed unexpectedly")
    select_vertices(obj, tuple(range(len(vertices))))
    try:
        result = bpy.ops.character_designer.build_centerline()
    except RuntimeError as exc:
        if "complete face band" not in str(exc):
            raise
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("A wire ladder without quad faces should be rejected")


def test_creation_transaction_removes_partial_data():
    reset_scene()
    obj, _root, expected = make_open_grid(
        "CreateFailure",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4)),
    )
    object_names = set(bpy.data.objects.keys())
    curve_names = set(bpy.data.curves.keys())
    original_collection_picker = character_designer._visible_source_collection
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    layers = ((0, 1, 2), (3, 4, 5))
    metadata = character_designer._build_cross_section_metadata(
        obj,
        bm,
        layers,
        expected,
        "OPEN",
    )
    bm.free()

    class BrokenObjects:
        def link(self, _obj):
            raise RuntimeError("injected link failure")

    class BrokenCollection:
        objects = BrokenObjects()

    character_designer._visible_source_collection = lambda _context, _obj: BrokenCollection()
    try:
        try:
            character_designer._create_centerline_object(
                bpy.context,
                obj,
                layers,
                expected,
                metadata,
            )
        except RuntimeError as exc:
            if "injected link failure" not in str(exc):
                raise
        else:
            raise AssertionError("Injected creation failure did not fail")
    finally:
        character_designer._visible_source_collection = original_collection_picker
    if object_names != set(bpy.data.objects.keys()) or curve_names != set(bpy.data.curves.keys()):
        raise AssertionError("Failed creation left an Object or Curve data-block")

    # Metadata is written after the object has been linked. A serialization
    # failure must still roll back both the Object and Curve data-block.
    invalid_metadata = {
        "point_count": len(expected),
        "invalid_number": float("nan"),
    }
    try:
        character_designer._create_centerline_object(
            bpy.context,
            obj,
            ((0, 1, 2), (3, 4, 5)),
            expected,
            invalid_metadata,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid metadata unexpectedly created a Curve")
    if object_names != set(bpy.data.objects.keys()) or curve_names != set(bpy.data.curves.keys()):
        raise AssertionError("Metadata serialization failure left output residue")


def test_corrupt_cross_section_metadata_is_ignored_safely():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "CorruptMetadata",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4), (0.0, 0.0, 0.8)),
    )
    output = capture_then_build(obj, root)
    valid_text = output["character_designer_cross_sections"]

    def clone_valid():
        return json.loads(valid_text)

    corrupt_payloads = []
    corrupt_payloads.append(("top-level value", []))

    payload = clone_valid()
    payload["sections"][0] = 7
    corrupt_payloads.append(("non-dict section", payload))

    payload = clone_valid()
    payload["space"] = "WORLD"
    corrupt_payloads.append(("wrong space", payload))

    payload = clone_valid()
    payload["version"] = character_designer.METADATA_VERSION + 1
    corrupt_payloads.append(("wrong payload version", payload))

    payload = clone_valid()
    payload["point_count"] += 1
    corrupt_payloads.append(("wrong payload count", payload))

    payload = clone_valid()
    payload["matrix_world_at_build"][0] = float("nan")
    corrupt_payloads.append(("NaN matrix", payload))

    payload = clone_valid()
    payload["matrix_world_at_build"][0] = 10**400
    corrupt_payloads.append(("overflowing integer matrix", payload))

    payload = clone_valid()
    payload["sections"][0]["max_profile_span_local"] = float("inf")
    corrupt_payloads.append(("infinite scalar", payload))

    payload = clone_valid()
    payload["sections"][0]["width_axis_local"][1] = float("-inf")
    corrupt_payloads.append(("infinite vector", payload))

    payload = clone_valid()
    payload["sections"][0]["profile_span_pair"] = [0]
    corrupt_payloads.append(("malformed pair", payload))

    for label, payload in corrupt_payloads:
        output["character_designer_cross_sections"] = json.dumps(payload)
        parsed = character_designer._curve_cross_section_metadata(output)
        if parsed is not None:
            raise AssertionError(f"The {label} metadata payload was accepted")
        if character_designer._metadata_summary(parsed) is not None:
            raise AssertionError(f"The {label} payload leaked into the UI summary")

    payload = clone_valid()
    payload["sections"][0]["width_axis_local"] = [1.0e308, 0.0, 0.0]
    output["character_designer_cross_sections"] = json.dumps(payload)
    parsed = character_designer._curve_cross_section_metadata(output)
    if parsed is None or character_designer._metadata_summary(parsed) is None:
        raise AssertionError("A finite extreme vector overflowed metadata validation")

    output["character_designer_cross_sections"] = valid_text
    output["character_designer_metadata_version"] = character_designer.METADATA_VERSION + 1
    if character_designer._curve_cross_section_metadata(output) is not None:
        raise AssertionError("A mismatched object metadata marker was accepted")

    output["character_designer_metadata_version"] = character_designer.METADATA_VERSION
    output.data.splines[0].points.add(1)
    if character_designer._curve_cross_section_metadata(output) is not None:
        raise AssertionError("Metadata with the wrong current Poly point count was accepted")


def test_register_cycle_preserves_edit_state():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "ReloadState",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4)),
    )
    select_vertices(obj, root)
    selected_before = {
        vertex.index
        for vertex in bmesh.from_edit_mesh(obj.data).verts
        if vertex.select
    }
    character_designer.unregister()
    character_designer.register()
    character_designer.register()
    selected_after = {
        vertex.index
        for vertex in bmesh.from_edit_mesh(obj.data).verts
        if vertex.select
    }
    if bpy.context.mode != "EDIT_MESH" or bpy.context.edit_object is not obj:
        raise AssertionError("Register cycle changed Edit Mode or active object")
    if selected_before != selected_after:
        raise AssertionError("Register cycle changed the mesh selection")
    character_designer._validate_source_files()


def test_register_rejects_stale_rna_and_wrong_pointer_type():
    original_classes = character_designer.CLASSES
    current_build_class = character_designer.CHARACTERDESIGNER_OT_build_centerline

    class StaleBuildCenterline(bpy.types.Operator):
        bl_idname = current_build_class.bl_idname
        bl_label = "Stale Build Centerline"

    character_designer.CLASSES = tuple(
        StaleBuildCenterline if cls is current_build_class else cls
        for cls in original_classes
    )
    try:
        try:
            character_designer.register()
        except RuntimeError as exc:
            if "stale RNA class CHARACTER_DESIGNER_OT_build_centerline" not in str(exc):
                raise AssertionError(f"Unexpected stale-class diagnostic: {exc}") from exc
        else:
            raise AssertionError("Idempotent register accepted a stale RNA class")
    finally:
        character_designer.CLASSES = original_classes

    # The current class table remains a valid idempotent registration after a
    # rejected stale-class fast path.
    character_designer.register()

    property_name = "character_designer_spline_ik"
    delattr(bpy.types.WindowManager, property_name)
    setattr(
        bpy.types.WindowManager,
        property_name,
        bpy.props.PointerProperty(type=character_designer.CharacterDesignerDeltaState),
    )
    try:
        try:
            character_designer.register()
        except RuntimeError as exc:
            if f"PointerProperty {property_name} targets" not in str(exc):
                raise AssertionError(f"Unexpected PointerProperty diagnostic: {exc}") from exc
        else:
            raise AssertionError("Idempotent register accepted an incorrect PointerProperty type")
    finally:
        delattr(bpy.types.WindowManager, property_name)
        setattr(
            bpy.types.WindowManager,
            property_name,
            bpy.props.PointerProperty(
                type=character_designer.CharacterDesignerSplineIKState
            ),
        )

    character_designer.register()


def test_workspace_owner_filter_guard_preserves_filter():
    workspace = bpy.data.workspaces[0]
    original_filter_state = workspace.use_filter_by_owner
    original_owner_ids = [owner_id.name for owner_id in workspace.owner_ids]
    try:
        workspace.use_filter_by_owner = True
        workspace.owner_ids.clear()
        workspace.owner_ids.new("keep_me")

        character_designer._ensure_workspace_owner_ids(workspace, "CYCLES")
        character_designer._ensure_workspace_owner_ids(workspace, "CYCLES")

        names = [owner_id.name for owner_id in workspace.owner_ids]
        expected = {
            "keep_me",
            character_designer.ADDON_MODULE_NAME,
            "cycles",
        }
        if not workspace.use_filter_by_owner:
            raise AssertionError("Workspace filtering was disabled")
        if set(names) != expected:
            raise AssertionError(f"Unexpected workspace owners: {names}")
        if any(names.count(name) != 1 for name in expected):
            raise AssertionError("Workspace owner guard is not idempotent")
    finally:
        workspace.owner_ids.clear()
        for owner_id in original_owner_ids:
            workspace.owner_ids.new(owner_id)
        workspace.use_filter_by_owner = original_filter_state


def test_hidden_output_cannot_destroy_edit_selection():
    reset_scene()
    obj, root, _expected = make_open_grid(
        "HiddenOutputSource",
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.4)),
    )
    output = capture_then_build(obj, root)
    hidden_collection = bpy.data.collections.new("ExcludedOutput")
    bpy.context.scene.collection.children.link(hidden_collection)
    hidden_collection.objects.link(output)
    for collection in tuple(output.users_collection):
        if collection is not hidden_collection:
            collection.objects.unlink(output)
    layer_collection = bpy.context.view_layer.layer_collection.children[hidden_collection.name]
    hidden_collection.hide_select = True

    if bpy.ops.character_designer.select_output.poll():
        raise AssertionError("Select Output should be disabled for a selection-locked collection")
    hidden_collection.hide_select = False
    layer_collection.exclude = True

    selected_before = {
        vertex.index
        for vertex in bmesh.from_edit_mesh(obj.data).verts
        if vertex.select
    }
    if bpy.ops.character_designer.select_output.poll():
        raise AssertionError("Select Output should be disabled for an excluded curve")
    selected_after = {
        vertex.index
        for vertex in bmesh.from_edit_mesh(obj.data).verts
        if vertex.select
    }
    if bpy.context.mode != "EDIT_MESH" or selected_before != selected_after:
        raise AssertionError("A hidden output check changed the source edit state")


def test_refresh_ui_only_appears_for_actionable_state():
    old_pending = character_designer.ADDON_REFRESH_PENDING
    old_changed = character_designer.ADDON_REFRESH_LAST_STATE
    old_error = character_designer.ADDON_REFRESH_LAST_ERROR
    try:
        character_designer.ADDON_REFRESH_PENDING = False
        character_designer.ADDON_REFRESH_LAST_STATE = False
        character_designer.ADDON_REFRESH_LAST_ERROR = ""
        if character_designer._refresh_ui_visible():
            raise AssertionError("Current source should hide Refresh Add-on")
        if bpy.ops.character_designer.refresh_addon.poll():
            raise AssertionError("Current source should not expose an actionable refresh operator")

        character_designer.ADDON_REFRESH_LAST_STATE = True
        if not character_designer._refresh_ui_visible():
            raise AssertionError("A source difference should reveal Refresh Add-on")
        if not bpy.ops.character_designer.refresh_addon.poll():
            raise AssertionError("A source difference should enable Refresh Add-on")
        character_designer.ADDON_REFRESH_LAST_STATE = False
        character_designer.ADDON_REFRESH_PENDING = True
        if not character_designer._refresh_ui_visible():
            raise AssertionError("A pending refresh should remain visible")
        if bpy.ops.character_designer.refresh_addon.poll():
            raise AssertionError("A pending refresh should disable repeated clicks")
        character_designer.ADDON_REFRESH_PENDING = False
        character_designer.ADDON_REFRESH_LAST_ERROR = "stale test error"
        if not character_designer._refresh_ui_visible():
            raise AssertionError("A refresh error should keep its retry entry visible")
        if not bpy.ops.character_designer.refresh_addon.poll():
            raise AssertionError("A refresh error should allow a current-state probe or retry")

        result = bpy.ops.character_designer.refresh_addon()
        if result != {"CANCELLED"}:
            raise AssertionError("An already-current add-on should not schedule a reload")
        if character_designer.ADDON_REFRESH_LAST_ERROR:
            raise AssertionError("An already-current source should clear a stale refresh error")
        if character_designer._refresh_ui_visible():
            raise AssertionError("An already-current probe should hide Refresh Add-on")
    finally:
        character_designer.ADDON_REFRESH_PENDING = old_pending
        character_designer.ADDON_REFRESH_LAST_STATE = old_changed
        character_designer.ADDON_REFRESH_LAST_ERROR = old_error


def test_refresh_schedules_without_saving_or_touching_runtime_early():
    old_pending = character_designer.ADDON_REFRESH_PENDING
    old_changed = character_designer.ADDON_REFRESH_LAST_STATE
    old_error = character_designer.ADDON_REFRESH_LAST_ERROR
    old_source_changed = character_designer._source_changed
    old_validate = character_designer._validate_source_files
    old_stop_preview = character_designer._stop_live_preview
    old_stop_delta = character_designer.stop_delta_symmetry_runtime
    calls = {"validate": 0, "preview": 0, "delta": 0, "save": 0}

    def validate():
        calls["validate"] += 1

    def stop_preview(*_args, **_kwargs):
        calls["preview"] += 1

    def stop_delta(*_args, **_kwargs):
        calls["delta"] += 1

    def save_probe(*_args):
        calls["save"] += 1

    try:
        if bpy.app.timers.is_registered(character_designer._reload_addon_deferred):
            raise AssertionError("Refresh reload timer was unexpectedly registered before test")
        bpy.app.handlers.save_pre.append(save_probe)
        character_designer.ADDON_REFRESH_PENDING = False
        character_designer.ADDON_REFRESH_LAST_STATE = True
        character_designer.ADDON_REFRESH_LAST_ERROR = ""
        character_designer._source_changed = lambda: True
        character_designer._validate_source_files = validate
        character_designer._stop_live_preview = stop_preview
        character_designer.stop_delta_symmetry_runtime = stop_delta

        result = bpy.ops.character_designer.refresh_addon()
        if result != {"FINISHED"}:
            raise AssertionError("A source change did not schedule refresh immediately")
        if calls != {"validate": 1, "preview": 0, "delta": 0, "save": 0}:
            raise AssertionError(f"Refresh scheduling performed unexpected work: {calls}")
        if not character_designer.ADDON_REFRESH_PENDING:
            raise AssertionError("Refresh did not enter pending state")
        if not bpy.app.timers.is_registered(character_designer._reload_addon_deferred):
            raise AssertionError("Refresh did not schedule its deferred reload")
    finally:
        if save_probe in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.remove(save_probe)
        if bpy.app.timers.is_registered(character_designer._reload_addon_deferred):
            bpy.app.timers.unregister(character_designer._reload_addon_deferred)
        character_designer._source_changed = old_source_changed
        character_designer._validate_source_files = old_validate
        character_designer._stop_live_preview = old_stop_preview
        character_designer.stop_delta_symmetry_runtime = old_stop_delta
        character_designer.ADDON_REFRESH_PENDING = old_pending
        character_designer.ADDON_REFRESH_LAST_STATE = old_changed
        character_designer.ADDON_REFRESH_LAST_ERROR = old_error


def test_refresh_deferred_reloads_modules_without_saving_scene():
    import addon_utils

    old_module_name = character_designer.ADDON_MODULE_NAME
    old_pending = character_designer.ADDON_REFRESH_PENDING
    old_error = character_designer.ADDON_REFRESH_LAST_ERROR
    old_disable = addon_utils.disable
    old_enable = addon_utils.enable
    old_stop_preview = character_designer._stop_live_preview
    old_stop_delta = character_designer.stop_delta_symmetry_runtime
    fixture_name = "character_designer_refresh_success_fixture"
    fixture_child_name = f"{fixture_name}.child"
    calls = {"disable": 0, "enable": 0, "preview": 0, "delta": 0, "save": 0}
    old_main = SimpleNamespace(__addon_persistent__=False)
    old_child = SimpleNamespace()
    new_main = SimpleNamespace()
    marker = None

    def disable(name, **_kwargs):
        if name != fixture_name:
            raise AssertionError(f"Refresh disabled the wrong module: {name}")
        calls["disable"] += 1

    def enable(name, **_kwargs):
        if name != fixture_name:
            raise AssertionError(f"Refresh enabled the wrong module: {name}")
        calls["enable"] += 1
        sys.modules[name] = new_main
        return new_main

    def stop_preview(*_args, **_kwargs):
        calls["preview"] += 1

    def stop_delta(*_args, **_kwargs):
        calls["delta"] += 1

    def save_probe(*_args):
        calls["save"] += 1

    try:
        marker = bpy.data.objects.new("RefreshUnsavedMarker", None)
        bpy.context.scene.collection.objects.link(marker)
        marker["unsaved_value"] = 17
        sys.modules[fixture_name] = old_main
        sys.modules[fixture_child_name] = old_child
        bpy.app.handlers.save_pre.append(save_probe)
        character_designer.ADDON_MODULE_NAME = fixture_name
        character_designer.ADDON_REFRESH_PENDING = True
        character_designer.ADDON_REFRESH_LAST_ERROR = ""
        addon_utils.disable = disable
        addon_utils.enable = enable
        character_designer._stop_live_preview = stop_preview
        character_designer.stop_delta_symmetry_runtime = stop_delta

        character_designer._reload_addon_deferred()

        if calls != {"disable": 1, "enable": 1, "preview": 1, "delta": 1, "save": 0}:
            raise AssertionError(f"Deferred refresh performed unexpected work: {calls}")
        if sys.modules.get(fixture_name) is not new_main:
            raise AssertionError("Deferred refresh did not install the newly enabled module")
        if fixture_child_name in sys.modules:
            raise AssertionError("Deferred refresh left a stale child module cached")
        if marker.name not in bpy.data.objects or marker.get("unsaved_value") != 17:
            raise AssertionError("Deferred refresh lost unsaved scene data")
    finally:
        if save_probe in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.remove(save_probe)
        sys.modules.pop(fixture_name, None)
        sys.modules.pop(fixture_child_name, None)
        addon_utils.disable = old_disable
        addon_utils.enable = old_enable
        character_designer._stop_live_preview = old_stop_preview
        character_designer.stop_delta_symmetry_runtime = old_stop_delta
        character_designer.ADDON_MODULE_NAME = old_module_name
        character_designer.ADDON_REFRESH_PENDING = old_pending
        character_designer.ADDON_REFRESH_LAST_ERROR = old_error
        if marker is not None and marker.name in bpy.data.objects:
            bpy.data.objects.remove(marker, do_unlink=True)


def test_refresh_failure_restores_old_modules_and_registration():
    import addon_utils

    old_module_name = character_designer.ADDON_MODULE_NAME
    old_pending = character_designer.ADDON_REFRESH_PENDING
    old_error = character_designer.ADDON_REFRESH_LAST_ERROR
    old_disable = addon_utils.disable
    old_enable = addon_utils.enable
    old_stop_preview = character_designer._stop_live_preview
    old_stop_delta = character_designer.stop_delta_symmetry_runtime
    fixture_name = "character_designer_refresh_failure_fixture"
    fixture_child_name = f"{fixture_name}.child"
    calls = {"register": 0, "save": 0}

    def register_old():
        calls["register"] += 1

    old_main = SimpleNamespace(
        __addon_persistent__=False,
        register=register_old,
        ADDON_REFRESH_PENDING=True,
        ADDON_REFRESH_LAST_ERROR="",
    )
    old_child = SimpleNamespace()

    def disable(_name, **_kwargs):
        return None

    def enable(_name, **_kwargs):
        raise RuntimeError("Injected refresh enable failure")

    def save_probe(*_args):
        calls["save"] += 1

    try:
        sys.modules[fixture_name] = old_main
        sys.modules[fixture_child_name] = old_child
        bpy.app.handlers.save_pre.append(save_probe)
        character_designer.ADDON_MODULE_NAME = fixture_name
        character_designer.ADDON_REFRESH_PENDING = True
        character_designer.ADDON_REFRESH_LAST_ERROR = ""
        addon_utils.disable = disable
        addon_utils.enable = enable
        character_designer._stop_live_preview = lambda *_args, **_kwargs: None
        character_designer.stop_delta_symmetry_runtime = lambda *_args, **_kwargs: None

        character_designer._reload_addon_deferred()

        if sys.modules.get(fixture_name) is not old_main:
            raise AssertionError("Failed refresh did not restore the old main module")
        if sys.modules.get(fixture_child_name) is not old_child:
            raise AssertionError("Failed refresh did not restore old child modules")
        if calls != {"register": 1, "save": 0}:
            raise AssertionError(f"Failed refresh restored unexpected state: {calls}")
        if old_main.ADDON_REFRESH_PENDING:
            raise AssertionError("Failed refresh left the restored add-on pending")
        if "Injected refresh enable failure" not in old_main.ADDON_REFRESH_LAST_ERROR:
            raise AssertionError("Failed refresh did not expose its error on the restored module")
        if not old_main.__addon_enabled__:
            raise AssertionError("Failed refresh did not restore the enabled marker")
    finally:
        if save_probe in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.remove(save_probe)
        sys.modules.pop(fixture_name, None)
        sys.modules.pop(fixture_child_name, None)
        addon_utils.disable = old_disable
        addon_utils.enable = old_enable
        character_designer._stop_live_preview = old_stop_preview
        character_designer.stop_delta_symmetry_runtime = old_stop_delta
        character_designer.ADDON_MODULE_NAME = old_module_name
        character_designer.ADDON_REFRESH_PENDING = old_pending
        character_designer.ADDON_REFRESH_LAST_ERROR = old_error


def main():
    character_designer.register()
    tests = (
        test_live_shape_open_patch,
        test_open_metadata_width_and_winding_direction,
        test_skew_sections_preserve_raw_chord_and_profile_span,
        test_flipped_single_side_quad_is_rejected_atomically,
        test_closed_tube_and_point_tip,
        test_point_tip_can_be_captured_as_reverse_root,
        test_point_tip_select_more_builds_four_reverse_points,
        test_full_selection_toggle_parses_four_triangle_loops_without_cap_capture,
        test_full_selection_toggle_pauses_in_object_mode_and_recovers,
        test_full_selection_confirm_reparses_latest_selection,
        test_auto_radial_fan_cap_only_and_band_ignore_hub,
        test_auto_triangulated_square_cap_ignores_internal_chord,
        test_auto_nonradial_cap_metadata_matches_captured_cap,
        test_auto_exact_centered_radial_hub_is_ignored,
        test_auto_single_quad_active_endpoint_edge_builds_two_points,
        test_auto_fully_capped_three_by_four_tube_uses_active_cap,
        test_auto_sparse_identity_audit_refreshes_equal_count_selection,
        test_live_preview_root_only_and_four_world_layers,
        test_live_preview_tracks_geometry_and_object_matrix,
        test_live_preview_confirm_recomputes_latest_geometry,
        test_live_preview_invalid_selection_clears_and_recovers,
        test_live_preview_topology_latch_and_fake_depsgraph_recovery,
        test_topology_signature_is_canonical_but_winding_sensitive,
        test_live_preview_lifecycle_cleanup_is_idempotent,
        test_triangulated_fan_cap_uses_only_its_boundary,
        test_nonradial_triangulated_cap_uses_its_single_boundary,
        test_whole_triangulated_open_card_is_not_a_cap,
        test_mixed_polygon_cap_uses_regular_first_collar,
        test_single_side_quad_is_not_a_cap,
        test_cap_may_have_uniform_collapsed_first_collar,
        test_large_world_transform_keeps_local_precision,
        test_visible_collection_is_used,
        test_disconnected_selection_is_rejected_without_output,
        test_nonfinite_layer_vertex_is_rejected_atomically,
        test_branched_root_capture_is_rejected,
        test_ambiguous_cross_layer_edges_are_rejected,
        test_unselected_root_chord_is_ignored,
        test_missing_face_band_is_rejected,
        test_creation_transaction_removes_partial_data,
        test_corrupt_cross_section_metadata_is_ignored_safely,
        test_register_cycle_preserves_edit_state,
        test_register_rejects_stale_rna_and_wrong_pointer_type,
        test_workspace_owner_filter_guard_preserves_filter,
        test_hidden_output_cannot_destroy_edit_selection,
        test_refresh_ui_only_appears_for_actionable_state,
        test_refresh_schedules_without_saving_or_touching_runtime_early,
        test_refresh_deferred_reloads_modules_without_saving_scene,
        test_refresh_failure_restores_old_modules_and_registration,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        if hasattr(bpy.types.WindowManager, "character_designer"):
            character_designer.unregister()
    print(f"PASS Character Designer {len(tests)} tests")


if __name__ == "__main__":
    main()
