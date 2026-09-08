import inspect
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDONS_ROOT = PROJECT_ROOT / "addons"
if str(ADDONS_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDONS_ROOT))

import character_designer
from character_designer import delta_symmetry


TOLERANCE = 1.0e-5


def assert_vector_close(actual, expected, tolerance=TOLERANCE):
    actual = Vector(actual)
    expected = Vector(expected)
    if (actual - expected).length > tolerance:
        raise AssertionError(f"Expected {tuple(expected)}, got {tuple(actual)}")


def reset_scene():
    delta_symmetry.stop_delta_symmetry_runtime(clear_capture=True)
    bpy.context.scene.tool_settings.use_mesh_automerge = False
    bpy.context.scene.tool_settings.use_proportional_edit = False
    bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def make_open_grid(name="DeltaGrid", rows=4, columns=5):
    middle = columns // 2
    vertices = []
    for row in range(rows):
        for column in range(columns):
            x = (column - middle) * 0.71
            y = row * 0.19 + column * 0.035
            z = row * 0.53
            if column > middle:
                x += 0.13 + row * 0.021
                y += 0.047
                z += column * 0.026
            vertices.append((x, y, z))
    faces = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            first = row * columns + column
            faces.append(
                (
                    first,
                    first + 1,
                    first + 1 + columns,
                    first + columns,
                )
            )
    center_edges = {
        tuple(sorted((row * columns + middle, (row + 1) * columns + middle)))
        for row in range(rows - 1)
    }
    return make_edit_object(name, vertices, faces, center_edges)


def make_open_center_band_strip(name="DeltaBandStrip", rows=4, columns=6):
    """Build an asymmetric curved quad strip with no vertex centerline."""

    if columns < 4 or columns % 2:
        raise ValueError("Center-band test strips need an even column count of at least four")
    first_center = columns // 2 - 1
    second_center = columns // 2
    vertices = []
    for row in range(rows):
        for column in range(columns):
            angle = (column - (columns - 1) / 2.0) * 0.22
            radius = 1.2 + row * 0.018
            x = math.sin(angle) * radius
            y = math.cos(angle) * radius + row * 0.013 * column
            z = row * 0.46 + column * 0.009
            if column >= second_center:
                x += 0.08 + row * 0.017
                y += 0.031
                z += column * 0.012
            vertices.append((x, y, z))
    faces = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            first = row * columns + column
            faces.append(
                (
                    first,
                    first + 1,
                    first + 1 + columns,
                    first + columns,
                )
            )
    boundary_edges = {
        tuple(sorted((row * columns + column, (row + 1) * columns + column)))
        for row in range(rows - 1)
        for column in (first_center, second_center)
    }
    return make_edit_object(name, vertices, faces, boundary_edges)


def make_closed_tube(name="DeltaTube", rows=4, columns=8):
    vertices = []
    for row in range(rows):
        for column in range(columns):
            angle = math.tau * column / columns
            radius = 1.0 + row * 0.017 + (0.035 if column > columns // 2 else 0.0)
            vertices.append(
                (
                    math.sin(angle) * radius,
                    math.cos(angle) * (1.0 + 0.011 * column),
                    row * 0.47 + 0.014 * column,
                )
            )
    faces = []
    for row in range(rows - 1):
        for column in range(columns):
            following = (column + 1) % columns
            faces.append(
                (
                    row * columns + column,
                    row * columns + following,
                    (row + 1) * columns + following,
                    (row + 1) * columns + column,
                )
            )
    center_edges = {
        tuple(sorted((row * columns, (row + 1) * columns)))
        for row in range(rows - 1)
    }
    return make_edit_object(name, vertices, faces, center_edges)


def make_closed_center_band_tube(name="DeltaBandTube", rows=4, columns=8):
    """Build a closed quad shaft with the two edges around one front band selected."""

    if columns < 4 or columns % 2:
        raise ValueError("Center-band tubes need an even circumference of at least four")
    vertices = []
    for row in range(rows):
        for column in range(columns):
            angle = math.tau * column / columns
            radius = 1.0 + row * 0.019
            x = math.sin(angle) * radius
            y = math.cos(angle) * radius + column * 0.006
            z = row * 0.48 + column * 0.011
            if 1 <= column <= columns // 2:
                x += 0.057 + row * 0.009
                y += 0.023
                z += column * 0.008
            vertices.append((x, y, z))
    faces = []
    for row in range(rows - 1):
        for column in range(columns):
            following = (column + 1) % columns
            faces.append(
                (
                    row * columns + column,
                    row * columns + following,
                    (row + 1) * columns + following,
                    (row + 1) * columns + column,
                )
            )
    boundary_edges = {
        tuple(sorted((row * columns + column, (row + 1) * columns + column)))
        for row in range(rows - 1)
        for column in (0, 1)
    }
    return make_edit_object(name, vertices, faces, boundary_edges)


def make_edit_object(name, vertices, faces, selected_edge_keys):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bm = bmesh.from_edit_mesh(mesh)
    prepare_bmesh(bm)
    for edge in bm.edges:
        edge_key = tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        if edge_key in selected_edge_keys:
            edge.select = True
    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    bm = bmesh.from_edit_mesh(mesh)
    prepare_bmesh(bm)
    return obj, bm


def prepare_bmesh(bm):
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()


def build_capture(obj, bm, coordinate_space="LOCAL", axis="X"):
    return delta_symmetry.build_pairing_from_selection(
        obj,
        bm,
        coordinate_space,
        axis,
    )


def snapshot_pairs(bm, pairs):
    return {
        index: bm.verts[index].co.copy()
        for pair in pairs
        for index in pair
    }


def deselect_all(bm):
    for vertex in bm.verts:
        vertex.select = False
    bm.select_history.clear()


def test_open_grid_pairing_is_topological_and_selection_safe():
    reset_scene()
    obj, bm = make_open_grid(rows=4, columns=5)
    selected_before = {edge.index for edge in bm.edges if edge.select}
    capture = build_capture(obj, bm)
    expected = {
        (0, 4),
        (1, 3),
        (5, 9),
        (6, 8),
        (10, 14),
        (11, 13),
        (15, 19),
        (16, 18),
    }
    if set(capture["pairs"]) != expected:
        raise AssertionError(f"Unexpected grid pairs: {capture['pairs']}")
    if capture["center"] != (2, 7, 12, 17):
        raise AssertionError("The selected centerline vertices were not preserved")
    if {edge.index for edge in bm.edges if edge.select} != selected_before:
        raise AssertionError("Building the pair map changed the Edit Mode selection")


def test_open_even_strip_auto_detects_two_center_band_boundaries():
    reset_scene()
    rows = 5
    columns = 6
    obj, bm = make_open_center_band_strip(rows=rows, columns=columns)
    selected_before = {edge.index for edge in bm.edges if edge.select}
    capture = build_capture(obj, bm)
    expected = {
        (row * columns + column, row * columns + columns - 1 - column)
        for row in range(rows)
        for column in range(columns // 2)
    }
    if capture["seed_mode"] != "CENTER_BAND":
        raise AssertionError("The two selected chains were not auto-detected as a center band")
    if set(capture["pairs"]) != expected:
        raise AssertionError(f"Unexpected center-band pairs: {capture['pairs']}")
    expected_seed = {
        row * columns + column
        for row in range(rows)
        for column in (columns // 2 - 1, columns // 2)
    }
    if set(capture["center"]) != expected_seed:
        raise AssertionError("The center-band boundary vertices were not retained as the seed")
    expected_first_pairs = {
        (row * columns + columns // 2 - 1, row * columns + columns // 2)
        for row in range(rows)
    }
    if not expected_first_pairs.issubset(set(capture["pairs"])):
        raise AssertionError("The first paired rows were not the two center-band boundaries")
    if len(capture["center_band_faces"]) != rows - 1:
        raise AssertionError("The center-band face row was not captured exactly")
    if {edge.index for edge in bm.edges if edge.select} != selected_before:
        raise AssertionError("Center-band detection changed the Edit Mode selection")


def test_set_symmetry_operator_auto_detects_center_band():
    reset_scene()
    rows = 4
    columns = 6
    _obj, _bm = make_open_center_band_strip(rows=rows, columns=columns)
    result = bpy.ops.character_designer.delta_build_pairs()
    settings = bpy.context.window_manager.character_designer_delta
    if result != {"FINISHED"}:
        raise AssertionError("Set Symmetry did not accept two center-band boundaries")
    if settings.seed_mode != "CENTER_BAND":
        raise AssertionError("Set Symmetry did not publish its auto-detected seed mode")
    if settings.pair_count != rows * (columns // 2):
        raise AssertionError(f"Unexpected operator pair count: {settings.pair_count}")
    if delta_symmetry._CAPTURE["seed_mode"] != "CENTER_BAND":
        raise AssertionError("The operator capture lost the detected center-band mode")


def test_closed_even_tube_center_band_meets_at_proven_opposite_face():
    reset_scene()
    rows = 4
    columns = 8
    obj, bm = make_closed_center_band_tube(rows=rows, columns=columns)
    capture = build_capture(obj, bm)
    expected = {
        frozenset((row * columns + first, row * columns + second))
        for row in range(rows)
        for first, second in ((0, 1), (7, 2), (6, 3), (5, 4))
    }
    actual = {frozenset(pair) for pair in capture["pairs"]}
    if capture["seed_mode"] != "CENTER_BAND":
        raise AssertionError("Closed-tube boundaries were not detected as a center band")
    if actual != expected:
        raise AssertionError(f"Closed center-band tube paired incorrectly: {capture['pairs']}")
    if len(capture["pairs"]) != rows * columns // 2:
        raise AssertionError("Closed center-band tube did not pair its full circumference")
    if any(first == second for first, second in capture["pairs"]):
        raise AssertionError("A face-centered opposite seam created false center vertices")


def test_center_band_rejects_mismatched_and_nonadjacent_chains():
    reset_scene()
    rows = 4
    columns = 6
    obj, bm = make_open_center_band_strip(rows=rows, columns=columns)
    # Remove one selected edge from the second chain: the chains no longer match.
    target = tuple(sorted(((rows - 2) * columns + 3, (rows - 1) * columns + 3)))
    for edge in bm.edges:
        key = tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        if key == target:
            edge.select = False
            break
    try:
        build_capture(obj, bm)
    except delta_symmetry.DeltaSymmetryError as exc:
        if "same number of edges" not in str(exc):
            raise AssertionError(f"Unexpected mismatched-chain error: {exc}") from exc
    else:
        raise AssertionError("Mismatched center-band chains were accepted")

    reset_scene()
    vertices = []
    for row in range(rows):
        for column in range(columns):
            vertices.append((column * 0.4, row * 0.08, row * 0.5))
    faces = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            first = row * columns + column
            faces.append((first, first + 1, first + 1 + columns, first + columns))
    nonadjacent = {
        tuple(sorted((row * columns + column, (row + 1) * columns + column)))
        for row in range(rows - 1)
        for column in (1, 4)
    }
    obj, bm = make_edit_object("NonAdjacentBand", vertices, faces, nonadjacent)
    try:
        build_capture(obj, bm)
    except delta_symmetry.DeltaSymmetryError as exc:
        if "exactly one face" not in str(exc):
            raise AssertionError(f"Unexpected nonadjacent-chain error: {exc}") from exc
    else:
        raise AssertionError("Nonadjacent chains were guessed to be a center band")


def test_closed_tube_meets_safely_at_the_opposite_center():
    reset_scene()
    obj, bm = make_closed_tube(rows=4, columns=8)
    capture = build_capture(obj, bm)
    if len(capture["pairs"]) != 12:
        raise AssertionError(f"Expected 12 tube pairs, got {len(capture['pairs'])}")
    expected_first_row = {(7, 1), (6, 2), (5, 3)}
    if not expected_first_row.issubset(set(capture["pairs"])):
        raise AssertionError("The closed tube paired the two directions incorrectly")
    if any(4 in pair for pair in capture["pairs"]):
        raise AssertionError("The opposite self-symmetry column should not become a two-vertex pair")


def test_side_labels_follow_topology_not_each_pair_position():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    # Push one vertex from the topological left side past its partner. Pair-wise
    # coordinate sorting would now mix the two sides, while side-wide labeling
    # must keep the whole left component together.
    bm.verts[0].co.x = 5.0
    capture = build_capture(obj, bm)
    expected_left = {0, 1, 5, 6, 10, 11}
    if set(capture["negative"]) != expected_left:
        raise AssertionError(f"Side labels mixed topology components: {capture['negative']}")


def test_local_delta_preserves_asymmetric_baseline_and_is_bidirectional():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    capture = build_capture(obj, bm)
    pairs = capture["pairs"]
    previous = snapshot_pairs(bm, pairs)
    negative, positive = pairs[0]
    negative_before = bm.verts[negative].co.copy()
    positive_before = bm.verts[positive].co.copy()

    writes, conflicts = delta_symmetry.apply_delta_updates(obj, bm, pairs, previous)
    if writes or conflicts:
        raise AssertionError("Capturing a baseline moved an already asymmetric mesh")
    assert_vector_close(bm.verts[negative].co, negative_before)
    assert_vector_close(bm.verts[positive].co, positive_before)

    first_delta = Vector((0.23, -0.17, 0.31))
    deselect_all(bm)
    bm.verts[negative].select = True
    bm.verts[negative].co += first_delta
    writes, conflicts = delta_symmetry.apply_delta_updates(obj, bm, pairs, previous)
    if (writes, conflicts) != (1, 0):
        raise AssertionError(f"Unexpected first update result {(writes, conflicts)}")
    assert_vector_close(
        bm.verts[positive].co,
        positive_before + Vector((-first_delta.x, first_delta.y, first_delta.z)),
    )

    second_delta = Vector((-0.11, 0.09, -0.07))
    negative_after_first = bm.verts[negative].co.copy()
    deselect_all(bm)
    bm.verts[positive].select = True
    bm.verts[positive].co += second_delta
    writes, conflicts = delta_symmetry.apply_delta_updates(obj, bm, pairs, previous)
    if (writes, conflicts) != (1, 0):
        raise AssertionError(f"Unexpected reverse update result {(writes, conflicts)}")
    assert_vector_close(
        bm.verts[negative].co,
        negative_after_first + Vector((-second_delta.x, second_delta.y, second_delta.z)),
    )


def test_world_axis_reflection_handles_object_rotation_and_scale():
    reset_scene()
    obj, _bm = make_open_grid(rows=3, columns=5)
    obj.matrix_world = (
        Matrix.Translation((3.0, -1.7, 0.8))
        @ Matrix.Rotation(math.radians(37.0), 4, "Z")
        @ Matrix.Diagonal((1.7, 0.65, 1.25, 1.0))
    )
    local_delta = Vector((0.31, -0.22, 0.17))
    reflected_local = delta_symmetry.reflect_delta(local_delta, obj, "WORLD", "Y")
    linear = obj.matrix_world.to_3x3()
    expected_world = linear @ local_delta
    expected_world.y *= -1.0
    assert_vector_close(linear @ reflected_local, expected_world)


def test_conflicting_two_side_edit_is_not_guessed_or_amplified():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    capture = build_capture(obj, bm)
    pairs = capture["pairs"]
    previous = snapshot_pairs(bm, pairs)
    negative, positive = pairs[0]
    deselect_all(bm)
    bm.verts[negative].select = True
    bm.verts[positive].select = True
    negative_change = Vector((0.12, 0.03, -0.02))
    positive_change = Vector((0.08, -0.04, 0.06))
    bm.verts[negative].co += negative_change
    bm.verts[positive].co += positive_change
    negative_after_user = bm.verts[negative].co.copy()
    positive_after_user = bm.verts[positive].co.copy()
    writes, conflicts = delta_symmetry.apply_delta_updates(obj, bm, pairs, previous)
    if (writes, conflicts) != (0, 1):
        raise AssertionError(f"Expected one safe conflict, got {(writes, conflicts)}")
    assert_vector_close(bm.verts[negative].co, negative_after_user)
    assert_vector_close(bm.verts[positive].co, positive_after_user)
    if delta_symmetry.apply_delta_updates(obj, bm, pairs, previous) != (0, 0):
        raise AssertionError("A conflict fed back into the next update")


def test_conflict_rejects_the_entire_tick_atomically():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    capture = build_capture(obj, bm)
    pairs = capture["pairs"]
    previous = snapshot_pairs(bm, pairs)
    first_negative, first_positive = pairs[0]
    second_negative, second_positive = pairs[1]
    first_positive_before = bm.verts[first_positive].co.copy()
    deselect_all(bm)
    for index in (first_negative, second_negative, second_positive):
        bm.verts[index].select = True
    bm.verts[first_negative].co += Vector((0.2, 0.0, 0.0))
    bm.verts[second_negative].co += Vector((0.1, 0.0, 0.0))
    bm.verts[second_positive].co += Vector((0.07, 0.0, 0.0))
    writes, conflicts = delta_symmetry.apply_delta_updates(obj, bm, pairs, previous)
    if writes != 0 or conflicts != 1:
        raise AssertionError(f"Expected one atomic conflict, got {(writes, conflicts)}")
    assert_vector_close(bm.verts[first_positive].co, first_positive_before)


def test_topology_change_invalidates_before_coordinate_writes():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    capture = build_capture(obj, bm)
    delta_symmetry._CAPTURE = capture
    bm.verts.new((9.0, 9.0, 9.0))
    prepare_bmesh(bm)
    try:
        delta_symmetry._validate_capture(obj, bm)
    except delta_symmetry.DeltaSymmetryError as exc:
        if "Topology changed" not in str(exc):
            raise
    else:
        raise AssertionError("A vertex-count topology change did not invalidate the pair map")


def test_same_count_topology_signature_mismatch_blocks_writes():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    settings.live_enabled = True
    negative, positive = delta_symmetry._CAPTURE["pairs"][0]
    positive_before = bm.verts[positive].co.copy()
    delta_symmetry._CAPTURE["topology_signature"] = "same-count-rewire"
    deselect_all(bm)
    bm.verts[negative].select = True
    bm.verts[negative].co += Vector((0.15, 0.0, 0.0))
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    assert_vector_close(bm.verts[positive].co, positive_before)
    if settings.live_enabled:
        raise AssertionError("A same-count signature mismatch did not stop Live Delta")


def test_timer_never_performs_a_late_fallback_write():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    settings.live_enabled = True
    negative, positive = delta_symmetry._CAPTURE["pairs"][0]
    positive_before = bm.verts[positive].co.copy()
    movement = Vector((0.12, 0.04, -0.03))
    deselect_all(bm)
    bm.verts[negative].select = True
    bm.verts[negative].co += movement
    delta_symmetry._delta_runtime_tick()
    assert_vector_close(bm.verts[positive].co, positive_before)
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    assert_vector_close(
        bm.verts[positive].co,
        positive_before + Vector((-movement.x, movement.y, movement.z)),
    )


def test_reenable_uses_a_fresh_baseline():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    settings.live_enabled = True
    negative, positive = delta_symmetry._CAPTURE["pairs"][0]
    settings.live_enabled = False
    positive_before = bm.verts[positive].co.copy()
    bm.verts[negative].co += Vector((0.3, 0.0, 0.0))
    settings.live_enabled = True
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    assert_vector_close(bm.verts[positive].co, positive_before)
    movement = Vector((0.08, -0.02, 0.05))
    deselect_all(bm)
    bm.verts[negative].select = True
    bm.verts[negative].co += movement
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    assert_vector_close(
        bm.verts[positive].co,
        positive_before + Vector((-movement.x, movement.y, movement.z)),
    )


def test_switching_active_shape_key_resamples_without_writes():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    negative, positive = delta_symmetry._CAPTURE["pairs"][0]

    bpy.ops.object.mode_set(mode="OBJECT")
    basis = obj.shape_key_add(name="Basis")
    first = obj.shape_key_add(name="First")
    second = obj.shape_key_add(name="Second")
    expected_first = {
        index: first.data[index].co.copy()
        for index in (negative, positive)
    }
    second.data[negative].co = basis.data[negative].co + Vector((0.21, -0.07, 0.04))
    second.data[positive].co = basis.data[positive].co + Vector((-0.03, 0.11, -0.08))
    expected_second = {
        index: second.data[index].co.copy()
        for index in (negative, positive)
    }
    obj.show_only_shape_key = True
    obj.active_shape_key_index = 1
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    prepare_bmesh(bm)
    deselect_all(bm)
    bm.verts[negative].select = True
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    settings.live_enabled = True
    obj.active_shape_key_index = 2
    bpy.context.view_layer.update()
    bm = bmesh.from_edit_mesh(obj.data)
    prepare_bmesh(bm)
    delta_symmetry._delta_runtime_tick(allow_writes=True)

    for index, expected in expected_second.items():
        assert_vector_close(bm.verts[index].co, expected)
    for index, previous in delta_symmetry._RUNTIME["previous"].items():
        assert_vector_close(previous, bm.verts[index].co)

    movement = Vector((0.08, 0.02, -0.05))
    bm.verts[negative].co += movement
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    assert_vector_close(bm.verts[negative].co, expected_second[negative] + movement)
    assert_vector_close(
        bm.verts[positive].co,
        expected_second[positive] + Vector((-movement.x, movement.y, movement.z)),
    )
    settings.live_enabled = False
    bpy.ops.object.mode_set(mode="OBJECT")
    for index, expected in expected_first.items():
        assert_vector_close(first.data[index].co, expected)
    assert_vector_close(second.data[negative].co, expected_second[negative] + movement)
    assert_vector_close(
        second.data[positive].co,
        expected_second[positive] + Vector((-movement.x, movement.y, movement.z)),
    )


def test_native_mirror_axes_are_temporarily_disabled_and_restored():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    obj.data.use_mirror_x = True
    obj.data.use_mirror_z = True
    settings.live_enabled = True
    if any((obj.data.use_mirror_x, obj.data.use_mirror_y, obj.data.use_mirror_z)):
        raise AssertionError("Mirror Movement did not temporarily disable native Mirror axes")
    obj.data.name = "DeltaGrid_Mesh_RenamedWhileActive"
    negative, positive = delta_symmetry._CAPTURE["pairs"][0]
    positive_before = bm.verts[positive].co.copy()
    obj.data.use_mirror_y = True
    movement = Vector((0.1, 0.03, -0.02))
    deselect_all(bm)
    bm.verts[negative].select = True
    bm.verts[negative].co += movement
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    assert_vector_close(
        bm.verts[positive].co,
        positive_before + Vector((-movement.x, movement.y, movement.z)),
    )
    if obj.data.use_mirror_y:
        raise AssertionError("A native Mirror axis enabled during runtime was not suppressed")
    settings.live_enabled = False
    restored = (obj.data.use_mirror_x, obj.data.use_mirror_y, obj.data.use_mirror_z)
    if restored != (True, False, True):
        raise AssertionError(f"Native Mirror axes were not restored exactly: {restored}")


def test_invalid_movement_conditions_turn_the_toggle_off_without_red_status():
    reset_scene()
    _obj, _bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    bpy.context.scene.tool_settings.use_mesh_automerge = True
    settings.live_enabled = True
    if settings.live_enabled:
        raise AssertionError("Mirror Movement stayed enabled beside Auto Merge")
    if settings.last_level == "ERROR":
        raise AssertionError("An unavailable runtime condition left a red error state")
    bpy.context.scene.tool_settings.use_mesh_automerge = False

    obj = bpy.context.edit_object
    obj.data.use_mirror_y = True
    settings.live_enabled = True
    bpy.context.scene.tool_settings.use_mesh_automerge = True
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    if settings.live_enabled:
        raise AssertionError("A runtime option change did not turn Mirror Movement off")
    if not obj.data.use_mirror_y:
        raise AssertionError("Automatic shutdown did not restore the native Mirror state")
    if settings.last_level == "ERROR":
        raise AssertionError("Runtime shutdown left a red error state")
    bpy.context.scene.tool_settings.use_mesh_automerge = False


def test_save_pre_restores_native_mirror_before_the_file_is_written():
    reset_scene()
    obj, _bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    obj.data.use_mirror_x = True
    settings.live_enabled = True
    if obj.data.use_mirror_x:
        raise AssertionError("Native Mirror was not suspended before the save test")
    delta_symmetry._delta_save_pre("")
    if settings.live_enabled:
        raise AssertionError("Saving did not turn Mirror Movement off")
    if not obj.data.use_mirror_x:
        raise AssertionError("save_pre did not restore the native Mirror state")
    delta_symmetry._delta_stop_after_save()
    if delta_symmetry._RUNTIME is not None:
        raise AssertionError("The deferred save cleanup left a movement runtime")


def test_auto_select_opposite_is_live_bidirectional_and_preserves_active():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    negative, positive = delta_symmetry._CAPTURE["pairs"][0]
    center = delta_symmetry._CAPTURE["center"][0]

    deselect_all(bm)
    bm.verts[negative].select = True
    bm.select_history.add(bm.verts[negative])
    obj.data.use_mirror_x = True
    settings.auto_select_opposite = True
    if not bm.verts[positive].select:
        raise AssertionError("Enabling Auto Select Opposite did not select the partner")
    if bm.select_history.active is not bm.verts[negative]:
        raise AssertionError("Auto selection replaced the artist's active vertex")
    if not obj.data.use_mirror_x:
        raise AssertionError("Auto Select Opposite changed Blender's native Mirror setting")

    # Shift-deselecting the active/source vertex clears the linked pair.
    bm.verts[negative].select = False
    delta_symmetry._auto_select_runtime_tick()
    if bm.verts[negative].select or bm.verts[positive].select:
        raise AssertionError("Cancelling the active vertex did not cancel the pair")

    bm.verts[negative].select = True
    delta_symmetry._auto_select_runtime_tick()
    if not bm.verts[negative].select or not bm.verts[positive].select:
        raise AssertionError("Selecting the original driver did not rebuild the pair")

    # A normal click first deselects the old pair, then selects only the clicked
    # active vertex. The clicked vertex must drive, not the partner that Blender
    # just deselected as a side effect of the click.
    deselect_all(bm)
    bm.verts[positive].select = True
    bm.select_history.add(bm.verts[positive])
    delta_symmetry._auto_select_runtime_tick()
    if not bm.verts[negative].select or not bm.verts[positive].select:
        raise AssertionError("A normal single click collapsed the paired selection")

    bm.verts[negative].select = False
    delta_symmetry._auto_select_runtime_tick()
    if not bm.verts[negative].select or not bm.verts[positive].select:
        raise AssertionError("Clicking the already-active vertex did not retain its pair")

    bm.verts[positive].select = False
    delta_symmetry._auto_select_runtime_tick()
    if bm.verts[negative].select or bm.verts[positive].select:
        raise AssertionError("Deselecting one paired vertex did not deselect its partner")

    bm.select_history.clear()
    bm.verts[positive].select = True
    bm.select_history.add(bm.verts[positive])
    delta_symmetry._auto_select_runtime_tick()
    if not bm.verts[negative].select or not bm.verts[positive].select:
        raise AssertionError("Reverse-side selection did not synchronize")
    if bm.select_history.active is not bm.verts[positive]:
        raise AssertionError("Reverse auto selection replaced the active vertex")

    bm.verts[center].select = True
    delta_symmetry._auto_select_runtime_tick()
    if not bm.verts[center].select:
        raise AssertionError("Auto selection modified an unpaired center vertex")
    settings.auto_select_opposite = False


def test_auto_select_records_the_driver_for_multi_pair_movement():
    reset_scene()
    obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    first_pair, second_pair = delta_symmetry._CAPTURE["pairs"][:2]
    deselect_all(bm)
    for negative, _positive in (first_pair, second_pair):
        bm.verts[negative].select = True
    settings.auto_select_opposite = True
    settings.live_enabled = True
    before = {
        index: bm.verts[index].co.copy()
        for pair in (first_pair, second_pair)
        for index in pair
    }
    # Native transforms may publish one selected partner before the source in
    # a depsgraph update. A recorded source must remain authoritative even for
    # that partial update, or the mirrored side can incorrectly drive backward.
    first_negative, first_positive = first_pair
    bm.verts[first_positive].co += Vector((0.04, 0.0, 0.0))
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    assert_vector_close(bm.verts[first_negative].co, before[first_negative])
    assert_vector_close(bm.verts[first_positive].co, before[first_positive])

    movement = Vector((0.12, -0.03, 0.07))
    for pair in (first_pair, second_pair):
        for index in pair:
            bm.verts[index].co += movement
    delta_symmetry._delta_runtime_tick(allow_writes=True)
    for negative, positive in (first_pair, second_pair):
        assert_vector_close(bm.verts[negative].co, before[negative] + movement)
        assert_vector_close(
            bm.verts[positive].co,
            before[positive] + Vector((-movement.x, movement.y, movement.z)),
        )


def test_ambiguous_two_sided_selection_keeps_auto_select_but_turns_movement_off():
    reset_scene()
    _obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    deselect_all(bm)
    for pair in delta_symmetry._CAPTURE["pairs"]:
        for index in pair:
            bm.verts[index].select = True
    settings.auto_select_opposite = True
    if not settings.auto_select_opposite:
        raise AssertionError("A symmetric selection unnecessarily disabled Auto Select Opposite")
    settings.live_enabled = True
    if settings.live_enabled:
        raise AssertionError("Mirror Movement guessed a driver for an ambiguous two-sided selection")
    if settings.last_level == "ERROR":
        raise AssertionError("Ambiguous selection left a red error state")

    settings.auto_select_opposite = False
    settings.live_enabled = True
    settings.auto_select_opposite = True
    if settings.live_enabled:
        raise AssertionError("Enabling Auto Select did not stop an already ambiguous movement runtime")
    if not settings.auto_select_opposite:
        raise AssertionError("Stopping movement also stopped the safe auto-selection runtime")


def test_auto_select_turns_off_silently_outside_vertex_mode():
    reset_scene()
    _obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    settings.auto_select_opposite = True
    selected_before = {vertex.index for vertex in bm.verts if vertex.select}
    bpy.context.scene.tool_settings.mesh_select_mode = (False, True, False)
    delta_symmetry._auto_select_runtime_tick()
    if settings.auto_select_opposite:
        raise AssertionError("Auto Select Opposite stayed enabled outside Vertex Select mode")
    if settings.last_level == "ERROR":
        raise AssertionError("Selection-mode fallback left a red error state")
    if {vertex.index for vertex in bm.verts if vertex.select} != selected_before:
        raise AssertionError("Turning Auto Select off changed the current selection")


def test_auto_select_resamples_after_history_without_replaying_selection():
    reset_scene()
    _obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    negative, positive = delta_symmetry._CAPTURE["pairs"][0]
    settings.auto_select_opposite = True
    bm.verts[negative].select = True
    bm.verts[positive].select = False
    delta_symmetry._auto_select_history_post(None)
    delta_symmetry._auto_select_runtime_tick()
    if not bm.verts[negative].select or bm.verts[positive].select:
        raise AssertionError("History resampling replayed an old mirrored selection")


def test_failed_rebuild_retains_last_proven_pair_map():
    reset_scene()
    _obj, bm = make_open_grid(rows=3, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    bpy.ops.character_designer.delta_build_pairs()
    old_capture = delta_symmetry._CAPTURE
    old_pair_count = settings.pair_count
    for edge in bm.edges:
        edge.select = False
    try:
        result = bpy.ops.character_designer.delta_build_pairs()
    except RuntimeError as exc:
        if "Select one center edge chain or loop" not in str(exc):
            raise
    else:
        if result != {"CANCELLED"}:
            raise AssertionError("An empty centerline selection unexpectedly rebuilt pairs")
    if delta_symmetry._CAPTURE is not old_capture or not settings.captured:
        raise AssertionError("A failed Rebuild discarded the last proven pair map")
    if settings.pair_count != old_pair_count:
        raise AssertionError("A failed Rebuild changed the published pair count")


def test_operator_runtime_and_selection_lifecycle():
    reset_scene()
    obj, bm = make_open_grid(rows=4, columns=5)
    settings = bpy.context.window_manager.character_designer_delta
    result = bpy.ops.character_designer.delta_build_pairs()
    if result != {"FINISHED"} or not settings.captured or settings.pair_count != 8:
        raise AssertionError("The one-click pair builder did not publish its state")

    settings.live_enabled = True
    if not bpy.app.timers.is_registered(delta_symmetry._delta_runtime_tick):
        raise AssertionError("Live Delta did not register its timer")
    if delta_symmetry._delta_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        raise AssertionError("Live Delta did not register its geometry update handler")

    settings.live_enabled = False
    if bpy.app.timers.is_registered(delta_symmetry._delta_runtime_tick):
        raise AssertionError("Disabling Live Delta left its timer registered")
    if delta_symmetry._delta_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        raise AssertionError("Disabling Live Delta left its update handler registered")
    if not settings.captured:
        raise AssertionError("Turning Mirror Movement off unexpectedly discarded the pair map")

    bpy.ops.character_designer.delta_select_group(group="NEGATIVE")
    selected_negative = {vertex.index for vertex in bm.verts if vertex.select}
    if selected_negative != set(delta_symmetry._CAPTURE["negative"]):
        raise AssertionError("Negative-side selection did not match the captured pairs")
    settings.auto_select_opposite = True
    if not bpy.app.timers.is_registered(delta_symmetry._auto_select_runtime_tick):
        raise AssertionError("Auto Select Opposite did not register its timer")
    selected_pairs = {vertex.index for vertex in bm.verts if vertex.select}
    expected_pairs = set(delta_symmetry._CAPTURE["negative"]) | set(delta_symmetry._CAPTURE["positive"])
    if selected_pairs != expected_pairs:
        raise AssertionError("Auto Select Opposite did not add the paired side")
    bpy.ops.character_designer.delta_select_group(group="POSITIVE")
    selected_after_side_button = {vertex.index for vertex in bm.verts if vertex.select}
    if selected_after_side_button != expected_pairs:
        raise AssertionError("A side shortcut fought the active auto-selection toggle")
    bpy.ops.character_designer.delta_select_group(group="CENTER")
    selected_center = {vertex.index for vertex in bm.verts if vertex.select}
    if selected_center != set(delta_symmetry._CAPTURE["center"]):
        raise AssertionError("The Center shortcut was changed by auto selection")

    bpy.ops.character_designer.delta_clear()
    if settings.captured or delta_symmetry._CAPTURE is not None:
        raise AssertionError("Clear left a hidden Delta Symmetry capture")
    if bpy.app.timers.is_registered(delta_symmetry._auto_select_runtime_tick):
        raise AssertionError("Clear left the auto-selection timer running")


def test_delta_panel_exposes_only_the_two_clear_runtime_toggles():
    settings = bpy.context.window_manager.character_designer_delta
    if settings.bl_rna.properties["live_enabled"].name != "Mirror Movement":
        raise AssertionError("The old Live Delta label returned")
    if settings.bl_rna.properties["auto_select_opposite"].name != "Auto Select Opposite":
        raise AssertionError("Auto Select Opposite is not exposed as a toggle")
    source = inspect.getsource(delta_symmetry.CHARACTERDESIGNER_PT_delta_symmetry.draw)
    if "Use Selected Centerline" in source:
        raise AssertionError("The centerline-only setup label returned")
    if "Set Symmetry" not in source:
        raise AssertionError("The automatic Set Symmetry action is not exposed")
    if "character_designer.delta_select_opposite" in source:
        raise AssertionError("The old manual Select Opposite button returned")
    if "row.alert" in source or "_draw_status" in source:
        raise AssertionError("The Delta panel can draw the removed red runtime status")


def test_unregister_cleans_runtime_without_leaving_edit_mode():
    reset_scene()
    obj, _bm = make_open_grid(rows=3, columns=5)
    bpy.ops.character_designer.delta_build_pairs()
    bpy.context.window_manager.character_designer_delta.auto_select_opposite = True
    bpy.context.window_manager.character_designer_delta.live_enabled = True
    character_designer.unregister()
    if obj.mode != "EDIT" or bpy.context.edit_object is not obj:
        raise AssertionError("Unregistering Delta Symmetry changed the artist's Edit Mode state")
    if bpy.app.timers.is_registered(delta_symmetry._delta_runtime_tick):
        raise AssertionError("Unregister left the Delta Symmetry timer running")
    if delta_symmetry._delta_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        raise AssertionError("Unregister left the Delta Symmetry handler running")
    if bpy.app.timers.is_registered(delta_symmetry._auto_select_runtime_tick):
        raise AssertionError("Unregister left the Auto Select Opposite timer running")
    character_designer.register()


def main():
    character_designer.register()
    tests = (
        test_open_grid_pairing_is_topological_and_selection_safe,
        test_open_even_strip_auto_detects_two_center_band_boundaries,
        test_set_symmetry_operator_auto_detects_center_band,
        test_closed_even_tube_center_band_meets_at_proven_opposite_face,
        test_center_band_rejects_mismatched_and_nonadjacent_chains,
        test_closed_tube_meets_safely_at_the_opposite_center,
        test_side_labels_follow_topology_not_each_pair_position,
        test_local_delta_preserves_asymmetric_baseline_and_is_bidirectional,
        test_world_axis_reflection_handles_object_rotation_and_scale,
        test_conflicting_two_side_edit_is_not_guessed_or_amplified,
        test_conflict_rejects_the_entire_tick_atomically,
        test_topology_change_invalidates_before_coordinate_writes,
        test_same_count_topology_signature_mismatch_blocks_writes,
        test_timer_never_performs_a_late_fallback_write,
        test_reenable_uses_a_fresh_baseline,
        test_switching_active_shape_key_resamples_without_writes,
        test_native_mirror_axes_are_temporarily_disabled_and_restored,
        test_invalid_movement_conditions_turn_the_toggle_off_without_red_status,
        test_save_pre_restores_native_mirror_before_the_file_is_written,
        test_auto_select_opposite_is_live_bidirectional_and_preserves_active,
        test_auto_select_records_the_driver_for_multi_pair_movement,
        test_ambiguous_two_sided_selection_keeps_auto_select_but_turns_movement_off,
        test_auto_select_turns_off_silently_outside_vertex_mode,
        test_auto_select_resamples_after_history_without_replaying_selection,
        test_failed_rebuild_retains_last_proven_pair_map,
        test_operator_runtime_and_selection_lifecycle,
        test_delta_panel_exposes_only_the_two_clear_runtime_toggles,
        test_unregister_cleans_runtime_without_leaving_edit_mode,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        if hasattr(bpy.types.WindowManager, "character_designer"):
            character_designer.unregister()
    print(f"PASS Delta Symmetry {len(tests)} tests")


if __name__ == "__main__":
    main()
