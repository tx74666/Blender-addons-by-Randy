"""Blender 5.2 regression tests for topology-aware Weight Flow."""

import inspect
import math
import sys
from pathlib import Path

import bpy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDONS_ROOT = PROJECT_ROOT / "addons"
if str(ADDONS_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDONS_ROOT))

import character_designer
from character_designer import selected_bone_weights, weight_flow
from character_designer.ui_constants import SIDEBAR_CATEGORY


TOLERANCE = 1.0e-6
PARTICIPANTS = ("Flow.A", "Flow.B")


def assert_close(actual, expected, tolerance=TOLERANCE):
    if not math.isclose(float(actual), float(expected), abs_tol=tolerance):
        raise AssertionError(f"Expected {expected}, got {actual}")


def reset_scene():
    weight_flow.stop_weight_flow_runtime(restore=True)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.armatures):
        for datablock in tuple(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def make_armature(name="WeightFlowRig"):
    armature = bpy.data.armatures.new(f"{name}_Data")
    armature_obj = bpy.data.objects.new(name, armature)
    bpy.context.scene.collection.objects.link(armature_obj)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    definitions = (
        ("Flow.A", (-0.8, 0.0, -0.5), (-0.8, 0.0, 0.5), True),
        ("Flow.B", (0.8, 0.0, -0.5), (0.8, 0.0, 0.5), True),
        ("Flow.C", (0.0, 0.8, -0.5), (0.0, 0.8, 0.5), True),
        ("Helper", (0.0, -0.8, -0.5), (0.0, -0.8, 0.5), False),
    )
    for bone_name, head, tail, _deform in definitions:
        bone = armature.edit_bones.new(bone_name)
        bone.head = head
        bone.tail = tail
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone_name, _head, _tail, deform in definitions:
        armature.bones[bone_name].use_deform = deform
    return armature_obj


def grid_geometry(rows, columns, *, x_values=None):
    if x_values is None:
        x_values = tuple(float(column) for column in range(columns))
    if len(x_values) != columns:
        raise ValueError("Grid x_values must match the column count")
    vertices = [
        (float(x_values[column]), float(row), 0.0)
        for row in range(rows)
        for column in range(columns)
    ]
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
    return vertices, faces


def tube_geometry(rows=3, columns=4):
    vertices = []
    for row in range(rows):
        for column in range(columns):
            angle = math.tau * column / columns
            vertices.append(
                (
                    math.cos(angle),
                    math.sin(angle),
                    float(row - rows // 2),
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
    return vertices, faces


def make_fixture(
    vertices,
    faces,
    selected,
    selected_weights,
    *,
    with_stack=False,
):
    reset_scene()
    armature_obj = make_armature()
    mesh = bpy.data.meshes.new("WeightFlowMesh_Data")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    mesh_obj = bpy.data.objects.new("WeightFlowMesh", mesh)
    bpy.context.scene.collection.objects.link(mesh_obj)

    if with_stack:
        subdivision = mesh_obj.modifiers.new("Artist Subdivision", "SUBSURF")
        subdivision.levels = 1
        subdivision.show_viewport = False
    armature_modifier = mesh_obj.modifiers.new("Existing Armature", "ARMATURE")
    armature_modifier.object = armature_obj
    mesh_obj.parent = armature_obj

    groups = {
        name: mesh_obj.vertex_groups.new(name=name)
        for name in ("Flow.A", "Flow.B", "Protected", "Locked.Artist")
    }
    selected = tuple(int(index) for index in selected)
    selected_set = set(selected)
    for vertex in mesh.vertices:
        index = vertex.index
        if index in selected_weights:
            first, second = selected_weights[index]
        else:
            first = 0.16 + 0.01 * (index % 5)
            second = 0.44 - 0.01 * (index % 5)
        groups["Flow.A"].add((index,), float(first), "REPLACE")
        groups["Flow.B"].add((index,), float(second), "REPLACE")
        groups["Protected"].add(
            (index,),
            0.2 if index in selected_set else 0.071 + 0.003 * (index % 7),
            "REPLACE",
        )
        vertex.select = index in selected_set
        vertex.hide = False

    # Keep both a positive protected membership and an explicit zero membership.
    unselected = tuple(index for index in range(len(vertices)) if index not in selected_set)
    if unselected:
        groups["Locked.Artist"].add((unselected[0],), 0.319, "REPLACE")
    groups["Locked.Artist"].add((selected[0],), 0.0, "REPLACE")
    groups["Locked.Artist"].lock_weight = True

    mesh.use_paint_mask_vertex = True
    mesh_obj.vertex_groups.active_index = groups["Flow.A"].index
    return {
        "mesh_obj": mesh_obj,
        "armature_obj": armature_obj,
        "armature_modifier": armature_modifier,
        "selected": selected,
    }


def prepare_weight_paint(fixture, selected_bones=PARTICIPANTS, active_group="Flow.A"):
    mesh_obj = fixture["mesh_obj"]
    armature_obj = fixture["armature_obj"]
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="POSE")
    selected = set(selected_bones)
    for pose_bone in armature_obj.pose.bones:
        pose_bone.select = pose_bone.name in selected
    active_bone = next(iter(selected_bones), "")
    armature_obj.data.bones.active = armature_obj.data.bones.get(active_bone)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    result = bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
    if result != {"FINISHED"}:
        raise AssertionError(f"Fixture could not enter Weight Paint Mode: {result}")
    group = mesh_obj.vertex_groups.get(active_group)
    if group is not None:
        mesh_obj.vertex_groups.active_index = group.index


def make_open_chain_fixture(*, with_stack=False):
    x_values = (-1.0, 0.0, 1.0, 3.0, 6.0, 10.0, 11.0)
    vertices, faces = grid_geometry(3, 7, x_values=x_values)
    selected = tuple(7 + column for column in range(1, 6))
    first_values = (0.8, 0.1, 0.7, 0.3, 0.0)
    weights = {
        index: (first, 0.8 - first)
        for index, first in zip(selected, first_values)
    }
    fixture = make_fixture(
        vertices,
        faces,
        selected,
        weights,
        with_stack=with_stack,
    )
    fixture["chain_parameters"] = (0.0, 0.1, 0.3, 0.6, 1.0)
    prepare_weight_paint(fixture)
    return fixture


def make_closed_loop_fixture():
    rows = 3
    columns = 4
    vertices, faces = tube_geometry(rows=rows, columns=columns)
    selected = tuple(columns + column for column in range(columns))
    first_values = (0.8, 0.4, 0.0, 0.4)
    weights = {
        index: (first, 0.8 - first)
        for index, first in zip(selected, first_values)
    }
    fixture = make_fixture(vertices, faces, selected, weights)
    prepare_weight_paint(fixture)
    return fixture


def make_region_fixture():
    rows = 5
    columns = 7
    vertices, faces = grid_geometry(rows, columns)
    selected = tuple(
        row * columns + column
        for row in range(1, 4)
        for column in range(1, 6)
    )
    first_by_position = {}
    boundary_values = (0.8, 0.6, 0.4, 0.2, 0.0)
    for row in range(1, 4):
        for offset, column in enumerate(range(1, 6)):
            if row in {1, 3} or column in {1, 5}:
                first = boundary_values[offset]
            else:
                first = {2: 0.0, 3: 0.8, 4: 0.0}[column]
            first_by_position[row * columns + column] = (first, 0.8 - first)
    fixture = make_fixture(vertices, faces, selected, first_by_position)
    fixture.update(
        columns=columns,
        boundary=frozenset(
            row * columns + column
            for row in range(1, 4)
            for column in range(1, 6)
            if row in {1, 3} or column in {1, 5}
        ),
        interior=(2 * columns + 2, 2 * columns + 3, 2 * columns + 4),
    )
    prepare_weight_paint(fixture)
    return fixture


def state_map(snapshot):
    return {state["name"]: state for state in snapshot}


def weights_for_state(state):
    return {index: weight for index, weight in state["weights"]}


def group_weight(mesh_obj, group_name, vertex_index):
    group = mesh_obj.vertex_groups[group_name]
    try:
        return float(group.weight(vertex_index))
    except RuntimeError:
        return 0.0


def assert_flow_safety(mesh_obj, before, selected, participants=PARTICIPANTS):
    after = selected_bone_weights._capture_vertex_groups(mesh_obj)
    before_by_name = state_map(before)
    after_by_name = state_map(after)
    if tuple(before_by_name) != tuple(after_by_name):
        raise AssertionError("Weight Flow changed Vertex Group definitions or order")

    participant_set = set(participants)
    for name, before_state in before_by_name.items():
        after_state = after_by_name[name]
        if name not in participant_set:
            if after_state != before_state:
                raise AssertionError(f"Protected Vertex Group changed: {name}")
            continue
        if (
            before_state["index"] != after_state["index"]
            or before_state["lock_weight"] != after_state["lock_weight"]
        ):
            raise AssertionError(f"Participating Vertex Group definition changed: {name}")
        before_weights = weights_for_state(before_state)
        after_weights = weights_for_state(after_state)
        for vertex in mesh_obj.data.vertices:
            if vertex.index in selected:
                continue
            before_marker = (
                vertex.index in before_weights,
                before_weights.get(vertex.index, 0.0),
            )
            after_marker = (
                vertex.index in after_weights,
                after_weights.get(vertex.index, 0.0),
            )
            if after_marker != before_marker:
                raise AssertionError(
                    f"Unselected membership changed in {name} at {vertex.index}"
                )

    first_before = weights_for_state(before_by_name[participants[0]])
    second_before = weights_for_state(before_by_name[participants[1]])
    first_after = weights_for_state(after_by_name[participants[0]])
    second_after = weights_for_state(after_by_name[participants[1]])
    for index in selected:
        expected_budget = first_before.get(index, 0.0) + second_before.get(index, 0.0)
        actual_budget = first_after.get(index, 0.0) + second_after.get(index, 0.0)
        assert_close(actual_budget, expected_budget, tolerance=2.0e-6)
        for value in (first_after.get(index, 0.0), second_after.get(index, 0.0)):
            if not math.isfinite(value) or value < -TOLERANCE or value > 1.0 + TOLERANCE:
                raise AssertionError(f"Invalid flowed weight at vertex {index}: {value}")


def assert_cancelled_without_group_changes(fixture, **operator_properties):
    mesh_obj = fixture["mesh_obj"]
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    context_before = weight_flow._capture_context_signature(
        bpy.context,
        mesh_obj,
        fixture["armature_obj"],
    )
    result = bpy.ops.character_designer.weight_flow(**operator_properties)
    if result != {"CANCELLED"}:
        raise AssertionError(f"Unsafe Weight Flow preflight did not cancel: {result}")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Cancelled Weight Flow changed Vertex Groups")
    context_after = weight_flow._capture_context_signature(
        bpy.context,
        mesh_obj,
        fixture["armature_obj"],
    )
    if context_after != context_before:
        raise AssertionError("Cancelled Weight Flow changed the artist context")


def test_registration_and_separate_ui_panel():
    reset_scene()
    if not hasattr(bpy.types, "CHARACTER_DESIGNER_OT_weight_flow"):
        raise AssertionError("The Weight Flow operator is not registered")
    if not hasattr(bpy.types, "CHARACTERDESIGNER_PT_weight_flow"):
        raise AssertionError("The separate Weight Flow panel is not registered")
    if not hasattr(bpy.types, "CHARACTERDESIGNER_PT_weight_tools"):
        raise AssertionError("The existing Weight Tools panel disappeared")
    if weight_flow.CHARACTERDESIGNER_PT_weight_flow.bl_category != SIDEBAR_CATEGORY:
        raise AssertionError("Weight Flow is not in the Character Designer sidebar")
    if weight_flow.CHARACTERDESIGNER_PT_weight_flow.bl_label != "Weight Flow":
        raise AssertionError("The new UI region has the wrong label")
    panel_source = inspect.getsource(
        weight_flow.CHARACTERDESIGNER_PT_weight_flow.draw
    )
    if "character_designer.weight_flow" not in panel_source:
        raise AssertionError("The Weight Flow panel does not expose its operator")
    if "UNDO" not in weight_flow.CHARACTERDESIGNER_OT_weight_flow.bl_options:
        raise AssertionError("Weight Flow is not a single Undo operator")

    properties = bpy.ops.character_designer.weight_flow.get_rna_type().properties
    profile_items = tuple(item.identifier for item in properties["profile"].enum_items)
    if profile_items != ("LINEAR", "SMOOTH", "SHARP"):
        raise AssertionError(f"Unexpected Weight Flow Profiles: {profile_items}")
    if properties["strength"].hard_min != 0.0 or properties["strength"].hard_max != 1.0:
        raise AssertionError("Strength is not bounded to 0–1")
    if properties["iterations"].hard_min != 1 or properties["iterations"].hard_max != 128:
        raise AssertionError("Iterations has an unexpected range")
    if bpy.ops.character_designer.weight_flow.poll():
        raise AssertionError("Weight Flow polls true outside Weight Paint Mode")

    fixture = make_open_chain_fixture()
    if not bpy.ops.character_designer.weight_flow.poll():
        raise AssertionError("Weight Flow does not poll in its valid context")
    # Avoid leaving the fixture implicit: it also proves registration survives scene reset.
    if fixture["mesh_obj"] is not bpy.context.active_object:
        raise AssertionError("Fixture lost its active Weight Paint Mesh")


def test_open_chain_uses_arc_length_and_fixed_endpoints():
    fixture = make_open_chain_fixture(with_stack=True)
    mesh_obj = fixture["mesh_obj"]
    armature_obj = fixture["armature_obj"]
    selected = fixture["selected"]
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    context_before = weight_flow._capture_context_signature(
        bpy.context,
        mesh_obj,
        armature_obj,
    )
    structure_before = selected_bone_weights._capture_structure(mesh_obj, armature_obj)
    bpy.context.scene.tool_settings.use_auto_normalize = True

    result = bpy.ops.character_designer.weight_flow(
        profile="LINEAR",
        strength=1.0,
        iterations=7,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Open-chain Weight Flow failed: {result}")

    expected_first = tuple(
        0.8 * (1.0 - parameter)
        for parameter in fixture["chain_parameters"]
    )
    for index, first in zip(selected, expected_first):
        assert_close(group_weight(mesh_obj, "Flow.A", index), first)
        assert_close(group_weight(mesh_obj, "Flow.B", index), 0.8 - first)
    assert_flow_safety(mesh_obj, before, set(selected))
    if selected_bone_weights._capture_structure(mesh_obj, armature_obj) != structure_before:
        raise AssertionError("Open-chain Weight Flow changed object or rig structure")
    if weight_flow._capture_context_signature(
        bpy.context,
        mesh_obj,
        armature_obj,
    ) != context_before:
        raise AssertionError("Open-chain Weight Flow changed the artist context")
    if not bpy.context.scene.tool_settings.use_auto_normalize:
        raise AssertionError("Weight Flow changed Auto Normalize")


def test_closed_loop_converges_to_budget_weighted_mean():
    fixture = make_closed_loop_fixture()
    mesh_obj = fixture["mesh_obj"]
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.weight_flow(
        profile="SHARP",
        strength=1.0,
        iterations=128,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Closed-loop Weight Flow failed: {result}")
    for index in fixture["selected"]:
        assert_close(group_weight(mesh_obj, "Flow.A", index), 0.4)
        assert_close(group_weight(mesh_obj, "Flow.B", index), 0.4)
    assert_flow_safety(mesh_obj, before, set(fixture["selected"]))


def test_region_keeps_relative_boundary_and_relaxes_only_interior():
    fixture = make_region_fixture()
    mesh_obj = fixture["mesh_obj"]
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    before_map = state_map(before)
    first_before = weights_for_state(before_map["Flow.A"])
    second_before = weights_for_state(before_map["Flow.B"])

    result = bpy.ops.character_designer.weight_flow(
        profile="LINEAR",
        strength=1.0,
        iterations=1,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Surface-region Weight Flow failed: {result}")

    for index in fixture["boundary"]:
        assert_close(group_weight(mesh_obj, "Flow.A", index), first_before[index])
        assert_close(group_weight(mesh_obj, "Flow.B", index), second_before[index])
    expected_interior_first = (0.7, 0.2, 0.3)
    for index, expected in zip(fixture["interior"], expected_interior_first):
        assert_close(group_weight(mesh_obj, "Flow.A", index), expected)
        assert_close(group_weight(mesh_obj, "Flow.B", index), 0.8 - expected)
    assert_flow_safety(mesh_obj, before, set(fixture["selected"]))


def test_invalid_branch_selection_is_refused_atomically():
    fixture = make_open_chain_fixture()
    mesh_obj = fixture["mesh_obj"]
    # The extra vertex is connected vertically to the middle of the chain,
    # producing a selected degree-three branch without selecting a surface face.
    branch_index = 3
    mesh_obj.data.vertices[branch_index].select = True
    assert_cancelled_without_group_changes(
        fixture,
        profile="LINEAR",
        strength=1.0,
    )


def test_locked_participant_is_refused_atomically():
    fixture = make_open_chain_fixture()
    fixture["mesh_obj"].vertex_groups["Flow.B"].lock_weight = True
    assert_cancelled_without_group_changes(fixture)


def test_preflight_requires_mask_two_bones_active_group_and_budget():
    fixture = make_open_chain_fixture()
    fixture["mesh_obj"].data.use_paint_mask_vertex = False
    assert_cancelled_without_group_changes(fixture)

    fixture = make_open_chain_fixture()
    fixture["armature_obj"].pose.bones["Flow.B"].select = False
    assert_cancelled_without_group_changes(fixture)

    fixture = make_open_chain_fixture()
    protected = fixture["mesh_obj"].vertex_groups["Protected"]
    fixture["mesh_obj"].vertex_groups.active_index = protected.index
    assert_cancelled_without_group_changes(fixture)

    fixture = make_open_chain_fixture()
    empty_index = fixture["selected"][2]
    fixture["mesh_obj"].vertex_groups["Flow.A"].remove((empty_index,))
    fixture["mesh_obj"].vertex_groups["Flow.B"].remove((empty_index,))
    assert_cancelled_without_group_changes(fixture)


def test_shared_mesh_data_is_refused_without_changes():
    fixture = make_open_chain_fixture()
    mesh_obj = fixture["mesh_obj"]
    linked_duplicate = mesh_obj.copy()
    linked_duplicate.name = "WeightFlowLinkedDuplicate"
    bpy.context.scene.collection.objects.link(linked_duplicate)
    if linked_duplicate.data is not mesh_obj.data or mesh_obj.data.users != 2:
        raise AssertionError("Fixture did not create shared Mesh data")
    assert_cancelled_without_group_changes(fixture)


def test_partial_write_failure_rolls_back_groups_and_context():
    fixture = make_open_chain_fixture(with_stack=True)
    mesh_obj = fixture["mesh_obj"]
    armature_obj = fixture["armature_obj"]
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    context_before = weight_flow._capture_context_signature(
        bpy.context,
        mesh_obj,
        armature_obj,
    )
    structure_before = selected_bone_weights._capture_structure(mesh_obj, armature_obj)
    original = weight_flow._write_group_weight
    calls = {"count": 0}

    def fail_after_partial_write(group, vertex_index, value):
        calls["count"] += 1
        original(group, vertex_index, value)
        if calls["count"] == 3:
            raise RuntimeError("Injected Weight Flow partial-write failure")

    weight_flow._write_group_weight = fail_after_partial_write
    try:
        try:
            result = bpy.ops.character_designer.weight_flow(
                profile="SMOOTH",
                strength=0.73,
                iterations=9,
            )
        except RuntimeError as exc:
            # Blender raises an RNA RuntimeError when an operator reports ERROR,
            # even though Weight Flow has already completed its rollback.
            if "Weight Flow was restored" not in str(exc):
                raise
            result = {"CANCELLED"}
    finally:
        weight_flow._write_group_weight = original
    if result != {"CANCELLED"}:
        raise AssertionError(f"Injected Weight Flow failure did not cancel: {result}")
    if calls["count"] != 3:
        raise AssertionError("Fault injection did not occur after a partial write")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Partial-write rollback did not restore exact Vertex Groups")
    if selected_bone_weights._capture_structure(mesh_obj, armature_obj) != structure_before:
        raise AssertionError("Partial-write rollback changed object or rig structure")
    if weight_flow._capture_context_signature(
        bpy.context,
        mesh_obj,
        armature_obj,
    ) != context_before:
        raise AssertionError("Partial-write rollback changed the artist context")
    if weight_flow._ACTIVE_PREVIEW is not None:
        raise AssertionError("Partial-write rollback left an active Preview")


def main():
    character_designer.register()
    tests = (
        test_registration_and_separate_ui_panel,
        test_open_chain_uses_arc_length_and_fixed_endpoints,
        test_closed_loop_converges_to_budget_weighted_mean,
        test_region_keeps_relative_boundary_and_relaxes_only_interior,
        test_invalid_branch_selection_is_refused_atomically,
        test_locked_participant_is_refused_atomically,
        test_preflight_requires_mask_two_bones_active_group_and_budget,
        test_shared_mesh_data_is_refused_without_changes,
        test_partial_write_failure_rolls_back_groups_and_context,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        weight_flow.stop_weight_flow_runtime(restore=True)
        if hasattr(bpy.types, "CHARACTERDESIGNER_PT_weight_flow"):
            character_designer.unregister()
    print(f"PASS Weight Flow {len(tests)} tests")


if __name__ == "__main__":
    main()
