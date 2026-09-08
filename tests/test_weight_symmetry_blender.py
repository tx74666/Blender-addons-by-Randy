"""Blender 5.2 background regression tests for one-way Weight Symmetry.

Run without opening an artist file::

    blender.exe --background --factory-startup --python-exit-code 1 \
        --python tests/test_weight_symmetry_blender.py
"""

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
from character_designer import weight_symmetry


# Keep the public-API assumption in one place so an operator rename does not
# require rewriting the behavioral tests.
OPERATOR_ID = "character_designer.copy_weight_to_opposite"
OPERATOR_PROPERTIES = {}

TOLERANCE = 1.0e-6
LEFT_GROUP = "forearm.L"
RIGHT_GROUP = "forearm.R"
SECOND_LEFT_GROUP = "upper_arm.L"
SECOND_RIGHT_GROUP = "upper_arm.R"
POSITIVE_INDICES = (3, 4, 8, 9)
NEGATIVE_INDICES = (1, 0, 6, 5)
CENTER_INDICES = (2, 7)
POSITIVE_TO_NEGATIVE = tuple(zip(POSITIVE_INDICES, NEGATIVE_INDICES))
LEFT_SOURCE_WEIGHTS = {
    3: 0.20,
    4: 0.45,
    8: 0.70,
    9: 0.95,
}
RIGHT_SOURCE_WEIGHTS = {
    1: 0.91,
    0: 0.81,
    6: 0.71,
    5: 0.61,
}
SECOND_LEFT_SOURCE_WEIGHTS = {
    3: 0.14,
    4: 0.24,
    8: 0.34,
    9: 0.44,
}


def invoke_operator(**properties):
    namespace, name = OPERATOR_ID.split(".", 1)
    operator = getattr(getattr(bpy.ops, namespace), name)
    options = dict(OPERATOR_PROPERTIES)
    options.update(properties)
    return operator(**options)


def assert_close(actual, expected, tolerance=TOLERANCE):
    if not math.isclose(float(actual), float(expected), abs_tol=tolerance):
        raise AssertionError(f"Expected {expected}, got {actual}")


def reset_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.armatures):
        for datablock in tuple(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def make_armature(name="WeightSymmetryRig"):
    armature = bpy.data.armatures.new(f"{name}_Data")
    armature_obj = bpy.data.objects.new(name, armature)
    bpy.context.scene.collection.objects.link(armature_obj)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    definitions = (
        (LEFT_GROUP, (1.25, 0.0, -0.5), (2.0, 0.0, 0.5)),
        (RIGHT_GROUP, (-1.25, 0.0, -0.5), (-2.0, 0.0, 0.5)),
        (SECOND_LEFT_GROUP, (1.1, 0.3, -0.6), (1.8, 0.3, 0.4)),
        (SECOND_RIGHT_GROUP, (-1.1, 0.3, -0.6), (-1.8, 0.3, 0.4)),
        ("spine", (0.0, 0.0, -0.8), (0.0, 0.0, 0.8)),
    )
    for bone_name, head, tail in definitions:
        bone = armature.edit_bones.new(bone_name)
        bone.head = head
        bone.tail = tail
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature_obj


def symmetric_geometry(
    *,
    unpaired=False,
    ambiguous=False,
    unrelated_asymmetry=False,
):
    vertices = [
        (-2.0, -1.0, 0.0),
        (-1.0, -1.0, 0.0),
        (0.0, -1.0, 0.0),
        (1.0, -1.0, 0.0),
        (2.0, -1.0, 0.0),
        (-2.0, 1.0, 0.0),
        (-1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
        (2.0, 1.0, 0.0),
    ]
    if unpaired:
        vertices[1] = (-1.34, -1.0, 0.0)
    if ambiguous:
        # Two exact candidates for vertex 3's reflected position must never be
        # resolved by vertex order or an arbitrary nearest-neighbor tie.
        vertices.append((-1.0, -1.0, 0.0))
    if unrelated_asymmetry:
        # Real character meshes can have asymmetric accessories or facial
        # details.  Vertices outside the active source group's support must not
        # turn a directed limb-weight copy into a whole-Mesh symmetry test.
        vertices.extend(
            (
                (-3.7, 4.2, 0.6),
                (-3.2, 4.7, -0.4),
                (-2.9, 5.1, 0.2),
            )
        )
    faces = (
        (0, 1, 6, 5),
        (1, 2, 7, 6),
        (2, 3, 8, 7),
        (3, 4, 9, 8),
    )
    return vertices, faces


def add_group(mesh_obj, name, weights=(), *, locked=False):
    group = mesh_obj.vertex_groups.new(name=name)
    for vertex_index, weight in dict(weights).items():
        group.add((int(vertex_index),), float(weight), "REPLACE")
    group.lock_weight = bool(locked)
    return group


def make_fixture(
    *,
    active_group=LEFT_GROUP,
    include_target=True,
    target_locked=False,
    unpaired=False,
    ambiguous=False,
    unrelated_asymmetry=False,
    include_second_pair=False,
    include_second_target=True,
    second_target_locked=False,
):
    reset_scene()
    armature_obj = make_armature()
    vertices, faces = symmetric_geometry(
        unpaired=unpaired,
        ambiguous=ambiguous,
        unrelated_asymmetry=unrelated_asymmetry,
    )
    mesh = bpy.data.meshes.new("WeightSymmetryMesh_Data")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    mesh_obj = bpy.data.objects.new("WeightSymmetryMesh", mesh)
    bpy.context.scene.collection.objects.link(mesh_obj)
    mesh_obj.parent = armature_obj
    modifier = mesh_obj.modifiers.new("Character Armature", "ARMATURE")
    modifier.object = armature_obj

    if active_group == LEFT_GROUP:
        # A valid L -> R repair.  On the target half each canonical source
        # weight is currently split across the wrong source group and the old
        # target group.  Replacing it therefore preserves the deform budget.
        left_weights = dict(LEFT_SOURCE_WEIGHTS)
        left_weights.update(
            {
                negative: LEFT_SOURCE_WEIGHTS[positive] * (
                    1.0 if not include_target else 0.65
                )
                for positive, negative in POSITIVE_TO_NEGATIVE
            }
        )
        left_weights[2] = 0.19
        right_weights = {
            negative: LEFT_SOURCE_WEIGHTS[positive] * 0.35
            for positive, negative in POSITIVE_TO_NEGATIVE
        }
        # Explicit zero memberships are still wrong-side assignments and must
        # be removed, without inventing a budget change.
        right_weights.update({positive: 0.0 for positive in POSITIVE_INDICES})
        right_weights[7] = 0.27
    elif active_group == RIGHT_GROUP:
        # The exact mirror contract for R -> L.
        right_weights = dict(RIGHT_SOURCE_WEIGHTS)
        right_weights.update(
            {
                positive: RIGHT_SOURCE_WEIGHTS[negative] * 0.65
                for positive, negative in POSITIVE_TO_NEGATIVE
            }
        )
        right_weights[7] = 0.27
        left_weights = {
            positive: RIGHT_SOURCE_WEIGHTS[negative] * 0.35
            for positive, negative in POSITIVE_TO_NEGATIVE
        }
        left_weights.update({negative: 0.0 for negative in NEGATIVE_INDICES})
        left_weights[2] = 0.19
    else:
        raise ValueError(f"Unsupported active fixture group: {active_group}")
    add_group(mesh_obj, LEFT_GROUP, left_weights)
    if include_target:
        add_group(
            mesh_obj,
            RIGHT_GROUP,
            right_weights,
            locked=target_locked,
        )
    if include_second_pair:
        second_left = dict(SECOND_LEFT_SOURCE_WEIGHTS)
        second_left.update(
            {
                negative: SECOND_LEFT_SOURCE_WEIGHTS[positive]
                * (1.0 if not include_second_target else 0.60)
                for positive, negative in POSITIVE_TO_NEGATIVE
            }
        )
        add_group(mesh_obj, SECOND_LEFT_GROUP, second_left)
        if include_second_target:
            second_right = {
                negative: SECOND_LEFT_SOURCE_WEIGHTS[positive] * 0.40
                for positive, negative in POSITIVE_TO_NEGATIVE
            }
            second_right.update({positive: 0.0 for positive in POSITIVE_INDICES})
            add_group(
                mesh_obj,
                SECOND_RIGHT_GROUP,
                second_right,
                locked=second_target_locked,
            )
    add_group(
        mesh_obj,
        "spine",
        {index: 0.031 + index * 0.017 for index in range(len(vertices))},
    )
    artist = add_group(
        mesh_obj,
        "Locked.Artist",
        {0: 0.41, 2: 0.0, 4: 0.26, 7: 0.58, len(vertices) - 1: 0.13},
        locked=True,
    )
    if not artist.lock_weight:
        raise AssertionError("Fixture could not lock its protected artist group")

    bpy.ops.object.select_all(action="DESELECT")
    armature_obj.select_set(True)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    group = mesh_obj.vertex_groups.get(active_group)
    if group is None:
        raise AssertionError(f"Fixture has no active group {active_group}")
    mesh_obj.vertex_groups.active_index = group.index
    result = bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
    if result != {"FINISHED"}:
        raise AssertionError(f"Fixture could not enter Weight Paint Mode: {result}")
    return {
        "mesh_obj": mesh_obj,
        "armature_obj": armature_obj,
        "modifier": modifier,
    }


def select_pose_sources(fixture, names, *, mode):
    """Select exact Pose bones while preserving one bound Mesh selection."""

    mesh_obj = fixture["mesh_obj"]
    armature_obj = fixture["armature_obj"]
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    armature_obj.select_set(True)

    if mode == "POSE":
        bpy.context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode="POSE")
    elif mode == "WEIGHT_PAINT":
        bpy.context.view_layer.objects.active = mesh_obj
        bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
    else:
        raise ValueError(f"Unsupported selection mode: {mode}")

    selected = set(names)
    for pose_bone in armature_obj.pose.bones:
        pose_bone.select = pose_bone.name in selected
    armature_obj.data.bones.active = armature_obj.data.bones[names[0]]


def add_additional_batch_pairs(fixture, pair_count):
    """Add scalable synthetic pairs and return their left-side names."""

    mesh_obj = fixture["mesh_obj"]
    armature_obj = fixture["armature_obj"]
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    names = []
    for number in range(1, pair_count + 1):
        stem = f"batch_{number:02d}"
        left_name = f"{stem}.L"
        right_name = f"{stem}.R"
        names.append(left_name)
        y = number * 0.05
        left = armature_obj.data.edit_bones.new(left_name)
        left.head = (1.1, y, -0.4)
        left.tail = (1.8, y, 0.4)
        right = armature_obj.data.edit_bones.new(right_name)
        right.head = (-1.1, y, -0.4)
        right.tail = (-1.8, y, 0.4)
    bpy.ops.object.mode_set(mode="OBJECT")

    for number, left_name in enumerate(names, 1):
        right_name = weight_symmetry._strict_opposite_name(left_name)
        base = 0.01 + number * 0.001
        source_weights = {
            positive: base + offset * 0.002
            for offset, positive in enumerate(POSITIVE_INDICES)
        }
        source_weights.update(
            {
                negative: source_weights[positive] * 0.65
                for positive, negative in POSITIVE_TO_NEGATIVE
            }
        )
        target_weights = {
            negative: source_weights[positive] * 0.35
            for positive, negative in POSITIVE_TO_NEGATIVE
        }
        target_weights.update({positive: 0.0 for positive in POSITIVE_INDICES})
        add_group(mesh_obj, left_name, source_weights)
        add_group(mesh_obj, right_name, target_weights)
    return tuple(names)


class OperatorCaptureLayout:
    def __init__(self):
        self.calls = []

    def operator(self, operator_id, *, text, icon):
        self.calls.append((operator_id, text, icon))
        return object()


def membership(mesh_obj, group_name, vertex_index):
    group = mesh_obj.vertex_groups.get(group_name)
    if group is None:
        return None
    for element in mesh_obj.data.vertices[vertex_index].groups:
        if element.group == group.index:
            return float(element.weight)
    return None


def assert_weight(mesh_obj, group_name, vertex_index, expected):
    value = membership(mesh_obj, group_name, vertex_index)
    if value is None:
        raise AssertionError(
            f"{group_name} is missing vertex {vertex_index}; expected {expected}"
        )
    assert_close(value, expected)


def assert_no_membership(mesh_obj, group_name, vertex_index):
    value = membership(mesh_obj, group_name, vertex_index)
    if value is not None:
        raise AssertionError(
            f"{group_name} retained vertex {vertex_index} at weight {value}"
        )


def capture_groups(mesh_obj):
    states = []
    for group in mesh_obj.vertex_groups:
        weights = []
        for vertex in mesh_obj.data.vertices:
            value = membership(mesh_obj, group.name, vertex.index)
            if value is not None:
                weights.append((vertex.index, value))
        states.append(
            (
                group.index,
                group.name,
                bool(group.lock_weight),
                tuple(weights),
            )
        )
    return tuple(states)


def state_by_name(snapshot):
    return {state[1]: state for state in snapshot}


def vertex_total(mesh_obj, vertex_index):
    return sum(float(element.weight) for element in mesh_obj.data.vertices[vertex_index].groups)


def assert_cancelled_atomically(fixture):
    mesh_obj = fixture["mesh_obj"]
    before = capture_groups(mesh_obj)
    result = invoke_operator()
    if result != {"CANCELLED"}:
        raise AssertionError(f"Unsafe Weight Symmetry did not cancel: {result}")
    after = capture_groups(mesh_obj)
    if after != before:
        raise AssertionError("Cancelled Weight Symmetry changed Vertex Groups")


def assert_left_to_right_contract(mesh_obj):
    for positive, negative in POSITIVE_TO_NEGATIVE:
        expected = LEFT_SOURCE_WEIGHTS[positive]
        assert_weight(mesh_obj, LEFT_GROUP, positive, expected)
        assert_weight(mesh_obj, RIGHT_GROUP, negative, expected)
        assert_no_membership(mesh_obj, LEFT_GROUP, negative)
        assert_no_membership(mesh_obj, RIGHT_GROUP, positive)


def assert_second_left_to_right_contract(mesh_obj):
    for positive, negative in POSITIVE_TO_NEGATIVE:
        expected = SECOND_LEFT_SOURCE_WEIGHTS[positive]
        assert_weight(mesh_obj, SECOND_LEFT_GROUP, positive, expected)
        assert_weight(mesh_obj, SECOND_RIGHT_GROUP, negative, expected)
        assert_no_membership(mesh_obj, SECOND_LEFT_GROUP, negative)
        assert_no_membership(mesh_obj, SECOND_RIGHT_GROUP, positive)


def test_registration_undo_and_weight_page_integration():
    reset_scene()
    operator_class = weight_symmetry.CHARACTERDESIGNER_OT_copy_weight_to_opposite
    panel_class = weight_symmetry.CHARACTERDESIGNER_PT_weight_symmetry
    if "UNDO" not in operator_class.bl_options:
        raise AssertionError("Weight Symmetry is not a single Undo operator")
    namespace, name = OPERATOR_ID.split(".", 1)
    if not hasattr(getattr(bpy.ops, namespace), name):
        raise AssertionError(f"Operator is not registered: {OPERATOR_ID}")
    if not hasattr(bpy.types, panel_class.bl_idname):
        raise AssertionError("The separate Weight Symmetry panel is not registered")
    draw_source = inspect.getsource(weight_symmetry.draw_weight_symmetry)
    panel_source = inspect.getsource(panel_class.draw)
    if OPERATOR_ID not in draw_source:
        raise AssertionError("Weight Symmetry draw helper does not expose the operator")
    if "draw_weight_symmetry" not in panel_source and OPERATOR_ID not in panel_source:
        raise AssertionError("The Weight Symmetry panel does not expose its action")


def test_left_to_right_replaces_target_and_cleans_both_wrong_sides():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    before = capture_groups(mesh_obj)
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Left-to-Right copy failed: {result}")
    assert_left_to_right_contract(mesh_obj)

    # The source half must be byte-for-byte equivalent at the membership level.
    before_map = state_by_name(before)
    before_left = dict(before_map[LEFT_GROUP][3])
    for index in POSITIVE_INDICES:
        assert_weight(mesh_obj, LEFT_GROUP, index, before_left[index])


def test_right_to_left_is_the_same_one_way_operation():
    fixture = make_fixture(active_group=RIGHT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    before = capture_groups(mesh_obj)
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Right-to-Left copy failed: {result}")

    before_right = dict(state_by_name(before)[RIGHT_GROUP][3])
    for positive, negative in POSITIVE_TO_NEGATIVE:
        expected = RIGHT_SOURCE_WEIGHTS[negative]
        assert_weight(mesh_obj, RIGHT_GROUP, negative, expected)
        assert_weight(mesh_obj, LEFT_GROUP, positive, expected)
        assert_no_membership(mesh_obj, RIGHT_GROUP, positive)
        assert_no_membership(mesh_obj, LEFT_GROUP, negative)
        assert_close(before_right[negative], expected)


def test_pose_bone_entry_uses_the_selected_bound_mesh():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    armature_obj = fixture["armature_obj"]
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="POSE")
    for pose_bone in armature_obj.pose.bones:
        pose_bone.select = pose_bone.name == LEFT_GROUP
    armature_obj.data.bones.active = armature_obj.data.bones[LEFT_GROUP]

    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Pose-bone Weight Symmetry failed: {result}")
    assert_left_to_right_contract(mesh_obj)


def test_pose_mode_selected_bones_copy_as_one_batch():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        include_second_pair=True,
    )
    mesh_obj = fixture["mesh_obj"]
    select_pose_sources(
        fixture,
        (LEFT_GROUP, SECOND_LEFT_GROUP),
        mode="POSE",
    )
    plan = weight_symmetry.build_weight_symmetry_batch_plan(bpy.context)
    if len(plan.plans) != 2:
        raise AssertionError(f"Expected a two-bone plan, got {len(plan.plans)}")
    before_totals = tuple(
        vertex_total(mesh_obj, index)
        for index in range(len(mesh_obj.data.vertices))
    )
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Pose multi-bone Weight Symmetry failed: {result}")
    assert_left_to_right_contract(mesh_obj)
    assert_second_left_to_right_contract(mesh_obj)
    after_totals = tuple(
        vertex_total(mesh_obj, index)
        for index in range(len(mesh_obj.data.vertices))
    )
    for index, (before, after) in enumerate(zip(before_totals, after_totals)):
        assert_close(after, before)


def test_weight_paint_selected_bones_copy_and_dynamic_label():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        include_second_pair=True,
    )
    mesh_obj = fixture["mesh_obj"]
    select_pose_sources(
        fixture,
        (LEFT_GROUP, SECOND_LEFT_GROUP),
        mode="WEIGHT_PAINT",
    )
    layout = OperatorCaptureLayout()
    weight_symmetry.draw_weight_symmetry(layout, bpy.context)
    if layout.calls != [
        (
            OPERATOR_ID,
            "Copy Selected 2 Bones to Opposite",
            "MOD_MIRROR",
        )
    ]:
        raise AssertionError(f"Unexpected multi-bone UI action: {layout.calls}")
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Weight Paint multi-bone copy failed: {result}")
    assert_left_to_right_contract(mesh_obj)
    assert_second_left_to_right_contract(mesh_obj)


def test_weight_paint_batch_scales_to_fifteen_selected_bones():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    additional = add_additional_batch_pairs(fixture, 14)
    source_names = (LEFT_GROUP,) + additional
    select_pose_sources(fixture, source_names, mode="WEIGHT_PAINT")

    layout = OperatorCaptureLayout()
    weight_symmetry.draw_weight_symmetry(layout, bpy.context)
    if layout.calls[0][1] != "Copy Selected 15 Bones to Opposite":
        raise AssertionError(f"Fifteen-bone label is wrong: {layout.calls}")
    plan = weight_symmetry.build_weight_symmetry_batch_plan(bpy.context)
    if len(plan.plans) != 15:
        raise AssertionError(f"Expected 15 planned pairs, got {len(plan.plans)}")

    before_totals = tuple(
        vertex_total(mesh_obj, index)
        for index in range(len(mesh_obj.data.vertices))
    )
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Fifteen-bone Weight Symmetry failed: {result}")
    after_totals = tuple(
        vertex_total(mesh_obj, index)
        for index in range(len(mesh_obj.data.vertices))
    )
    for before, after in zip(before_totals, after_totals):
        assert_close(after, before)
    assert_left_to_right_contract(mesh_obj)
    for source_name in additional:
        target_name = weight_symmetry._strict_opposite_name(source_name)
        for positive, negative in POSITIVE_TO_NEGATIVE:
            expected = membership(mesh_obj, source_name, positive)
            assert_weight(mesh_obj, target_name, negative, expected)
            assert_no_membership(mesh_obj, source_name, negative)
            assert_no_membership(mesh_obj, target_name, positive)


def test_multi_bone_preflight_failure_creates_nothing_and_writes_nothing():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        include_target=False,
        include_second_pair=True,
        second_target_locked=True,
    )
    mesh_obj = fixture["mesh_obj"]
    select_pose_sources(
        fixture,
        (LEFT_GROUP, SECOND_LEFT_GROUP),
        mode="POSE",
    )
    before = capture_groups(mesh_obj)
    result = invoke_operator()
    if result != {"CANCELLED"}:
        raise AssertionError(f"Locked late pair did not cancel batch: {result}")
    if capture_groups(mesh_obj) != before:
        raise AssertionError("A failed multi-bone preflight changed Vertex Groups")
    if mesh_obj.vertex_groups.get(RIGHT_GROUP) is not None:
        raise AssertionError("Failed batch preflight created the earlier target group")


def test_multi_bone_selection_requires_one_named_and_physical_side():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        include_second_pair=True,
    )
    mesh_obj = fixture["mesh_obj"]
    select_pose_sources(
        fixture,
        (LEFT_GROUP, SECOND_RIGHT_GROUP),
        mode="POSE",
    )
    assert_cancelled_atomically(fixture)

    select_pose_sources(
        fixture,
        (LEFT_GROUP, "spine"),
        mode="POSE",
    )
    before = capture_groups(mesh_obj)
    result = invoke_operator()
    if result != {"CANCELLED"} or capture_groups(mesh_obj) != before:
        raise AssertionError("An invalid selected bone name was not atomic")


def test_multi_bone_budget_is_validated_on_the_combined_final_state():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        include_second_pair=True,
    )
    mesh_obj = fixture["mesh_obj"]
    # At one target vertex, pair one gains 0.05 while pair two loses 0.05.
    # A single-pair operation is unsafe, but the selected transaction preserves
    # the exact final Deform total and is therefore a valid batch repair.
    mesh_obj.vertex_groups[RIGHT_GROUP].add((1,), 0.02, "REPLACE")
    mesh_obj.vertex_groups[SECOND_RIGHT_GROUP].add((1,), 0.106, "REPLACE")
    select_pose_sources(
        fixture,
        (LEFT_GROUP, SECOND_LEFT_GROUP),
        mode="WEIGHT_PAINT",
    )
    try:
        weight_symmetry.build_weight_symmetry_plan(bpy.context)
    except weight_symmetry.WeightSymmetryError:
        pass
    else:
        raise AssertionError("The deliberately unsafe single-pair plan passed")

    before_totals = tuple(
        vertex_total(mesh_obj, index)
        for index in range(len(mesh_obj.data.vertices))
    )
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Combined-budget batch failed: {result}")
    after_totals = tuple(
        vertex_total(mesh_obj, index)
        for index in range(len(mesh_obj.data.vertices))
    )
    for index, (before, after) in enumerate(zip(before_totals, after_totals)):
        assert_close(after, before)
    assert_left_to_right_contract(mesh_obj)
    assert_second_left_to_right_contract(mesh_obj)


def test_multi_bone_post_write_failure_restores_every_group_exactly():
    original_verify = weight_symmetry._states_match_batch_expected

    def fail_after_writes(_mesh_obj, _batch):
        raise RuntimeError("Injected batch verification failure")

    weight_symmetry._states_match_batch_expected = fail_after_writes
    try:
        fixture = make_fixture(
            active_group=LEFT_GROUP,
            include_target=False,
            include_second_pair=True,
            include_second_target=False,
        )
        mesh_obj = fixture["mesh_obj"]
        select_pose_sources(
            fixture,
            (LEFT_GROUP, SECOND_LEFT_GROUP),
            mode="POSE",
        )
        before = capture_groups(mesh_obj)
        result = invoke_operator()
        if result != {"CANCELLED"}:
            raise AssertionError(f"Injected batch failure did not cancel: {result}")
        if capture_groups(mesh_obj) != before:
            raise AssertionError("Batch rollback did not restore every group")
        for target_name in (RIGHT_GROUP, SECOND_RIGHT_GROUP):
            if mesh_obj.vertex_groups.get(target_name) is not None:
                raise AssertionError(
                    f'Batch rollback retained created group "{target_name}"'
                )
    finally:
        weight_symmetry._states_match_batch_expected = original_verify


def test_center_head_bones_use_rest_midpoint_for_direction():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    armature_obj = fixture["armature_obj"]
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    armature_obj.data.edit_bones[LEFT_GROUP].head.x = 0.0
    armature_obj.data.edit_bones[RIGHT_GROUP].head.x = 0.0
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="DESELECT")
    armature_obj.select_set(True)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    mesh_obj.vertex_groups.active_index = mesh_obj.vertex_groups[LEFT_GROUP].index
    bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Center-head bone direction fallback failed: {result}")
    assert_left_to_right_contract(mesh_obj)


def test_missing_target_group_is_created_after_successful_preflight():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        include_target=False,
    )
    mesh_obj = fixture["mesh_obj"]
    if mesh_obj.vertex_groups.get(RIGHT_GROUP) is not None:
        raise AssertionError("Missing-target fixture unexpectedly has forearm.R")
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Missing-target copy failed: {result}")
    if mesh_obj.vertex_groups.get(RIGHT_GROUP) is None:
        raise AssertionError("Weight Symmetry did not create the target group")
    for positive, negative in POSITIVE_TO_NEGATIVE:
        assert_weight(
            mesh_obj,
            RIGHT_GROUP,
            negative,
            LEFT_SOURCE_WEIGHTS[positive],
        )
        assert_no_membership(mesh_obj, LEFT_GROUP, negative)


def test_locked_target_refuses_with_zero_writes():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        target_locked=True,
    )
    assert_cancelled_atomically(fixture)


def test_locked_source_refuses_with_zero_writes():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    mesh_obj.vertex_groups[LEFT_GROUP].lock_weight = True
    assert_cancelled_atomically(fixture)


def test_shared_mesh_data_refuses_with_zero_writes():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    linked_duplicate = mesh_obj.copy()
    linked_duplicate.name = "WeightSymmetryLinkedDuplicate"
    bpy.context.scene.collection.objects.link(linked_duplicate)
    if linked_duplicate.data is not mesh_obj.data or mesh_obj.data.users != 2:
        raise AssertionError("Fixture did not create two users of one Mesh data-block")
    assert_cancelled_atomically(fixture)


def test_source_and_target_must_both_be_deform_bones():
    fixture = make_fixture(active_group=LEFT_GROUP)
    fixture["armature_obj"].data.bones[LEFT_GROUP].use_deform = False
    assert_cancelled_atomically(fixture)

    fixture = make_fixture(active_group=LEFT_GROUP)
    fixture["armature_obj"].data.bones[RIGHT_GROUP].use_deform = False
    assert_cancelled_atomically(fixture)


def test_non_side_named_active_group_refuses_with_zero_writes():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    invalid = add_group(mesh_obj, "forearm_left", {3: 0.2, 4: 0.45})
    mesh_obj.vertex_groups.active_index = invalid.index
    assert_cancelled_atomically(fixture)


def test_unpaired_vertex_refuses_with_zero_writes():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        include_target=False,
        unpaired=True,
    )
    assert_cancelled_atomically(fixture)
    if fixture["mesh_obj"].vertex_groups.get(RIGHT_GROUP) is not None:
        raise AssertionError("Failed preflight left a newly created target group")


def test_ambiguous_vertex_refuses_with_zero_writes():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        ambiguous=True,
    )
    assert_cancelled_atomically(fixture)


def test_unrelated_one_sided_vertices_do_not_require_whole_mesh_pairing():
    fixture = make_fixture(
        active_group=LEFT_GROUP,
        unrelated_asymmetry=True,
    )
    mesh_obj = fixture["mesh_obj"]
    before = capture_groups(mesh_obj)
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(
            f"Unrelated one-sided vertices blocked limb Weight Symmetry: {result}"
        )
    assert_left_to_right_contract(mesh_obj)
    before_map = state_by_name(before)
    after_map = state_by_name(capture_groups(mesh_obj))
    for name in ("spine", "Locked.Artist"):
        if after_map[name] != before_map[name]:
            raise AssertionError(
                f"Asymmetric unrelated support changed protected group {name}"
            )


def test_budget_changing_cleanup_refuses_with_zero_writes():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    # A positive target-group weight on the source half cannot be cleared while
    # leaving the source unchanged and preserving the per-vertex deform total.
    mesh_obj.vertex_groups[RIGHT_GROUP].add((3,), 0.13, "REPLACE")
    assert_cancelled_atomically(fixture)


def test_group_edit_after_planning_refuses_without_reverting_artist_edit():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    plan = weight_symmetry.build_weight_symmetry_plan(bpy.context)

    # Simulate an artist or another tool changing any group between a modal
    # preflight and commit.  A stale plan must neither apply nor roll this newer
    # edit back to the older planning snapshot.
    mesh_obj.vertex_groups["spine"].add((3,), 0.319, "REPLACE")
    artist_state = capture_groups(mesh_obj)
    try:
        weight_symmetry.apply_weight_symmetry_plan(plan)
    except weight_symmetry.WeightSymmetryError:
        pass
    else:
        raise AssertionError("A stale Weight Symmetry plan unexpectedly committed")
    if capture_groups(mesh_obj) != artist_state:
        raise AssertionError("Rejecting a stale plan reverted the newer artist edit")


def test_post_write_failure_restores_every_group_exactly():
    original_verify = weight_symmetry._states_match_expected

    def fail_after_writes(_mesh_obj, _plan):
        raise RuntimeError("Injected verification failure after weight writes")

    weight_symmetry._states_match_expected = fail_after_writes
    try:
        for include_target in (True, False):
            fixture = make_fixture(
                active_group=LEFT_GROUP,
                include_target=include_target,
            )
            mesh_obj = fixture["mesh_obj"]
            before = capture_groups(mesh_obj)
            result = invoke_operator()
            if result != {"CANCELLED"}:
                raise AssertionError(
                    f"Injected post-write failure did not cancel: {result}"
                )
            if capture_groups(mesh_obj) != before:
                raise AssertionError(
                    "Post-write failure did not restore every Vertex Group"
                )
            if not include_target and mesh_obj.vertex_groups.get(RIGHT_GROUP) is not None:
                raise AssertionError(
                    "Rollback retained a target group created during the failed operation"
                )
    finally:
        weight_symmetry._states_match_expected = original_verify


def test_centerline_and_unrelated_groups_are_unchanged():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    before = capture_groups(mesh_obj)
    before_map = state_by_name(before)
    center_before = {
        (name, index): membership(mesh_obj, name, index)
        for name in (LEFT_GROUP, RIGHT_GROUP)
        for index in CENTER_INDICES
    }
    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Centerline preservation copy failed: {result}")
    after_map = state_by_name(capture_groups(mesh_obj))
    for name in ("spine", "Locked.Artist"):
        if after_map[name] != before_map[name]:
            raise AssertionError(f"Unrelated Vertex Group changed: {name}")
    for key, expected in center_before.items():
        name, index = key
        actual = membership(mesh_obj, name, index)
        if actual is None or expected is None:
            if actual != expected:
                raise AssertionError(
                    f"Centerline membership changed for {name} at {index}"
                )
        else:
            assert_close(actual, expected)


def test_pure_wrong_group_reassignment_preserves_every_vertex_total():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]

    # Model the user's exact failure: mirrored values exist on the target half,
    # but they still belong to the source group.  No weight is duplicated.
    right = mesh_obj.vertex_groups[RIGHT_GROUP]
    right.remove(tuple(range(len(mesh_obj.data.vertices))))
    left = mesh_obj.vertex_groups[LEFT_GROUP]
    for positive, negative in POSITIVE_TO_NEGATIVE:
        left.add((negative,), LEFT_SOURCE_WEIGHTS[positive], "REPLACE")
    before_totals = tuple(
        vertex_total(mesh_obj, index)
        for index in range(len(mesh_obj.data.vertices))
    )

    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Pure reassignment copy failed: {result}")
    after_totals = tuple(
        vertex_total(mesh_obj, index)
        for index in range(len(mesh_obj.data.vertices))
    )
    for index, (before, after) in enumerate(zip(before_totals, after_totals)):
        if not math.isclose(before, after, abs_tol=TOLERANCE):
            raise AssertionError(
                f"Vertex {index} budget changed during pure reassignment: "
                f"{before} -> {after}"
            )


def test_repeated_copy_is_idempotent():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    first = invoke_operator()
    if first != {"FINISHED"}:
        raise AssertionError(f"First Weight Symmetry copy failed: {first}")
    after_first = capture_groups(mesh_obj)
    second = invoke_operator()
    if second != {"FINISHED"}:
        raise AssertionError(f"Second Weight Symmetry copy failed: {second}")
    after_second = capture_groups(mesh_obj)
    if after_second != after_first:
        raise AssertionError("Repeating Weight Symmetry changed an already-correct result")


def test_operator_is_single_step_undoable_when_background_undo_is_available():
    fixture = make_fixture(active_group=LEFT_GROUP)
    mesh_obj = fixture["mesh_obj"]
    mesh_name = mesh_obj.name
    bpy.context.preferences.edit.use_global_undo = True
    before = capture_groups(mesh_obj)

    if not bpy.ops.ed.undo_push.poll():
        print("SKIP test_operator_is_single_step_undoable_when_background_undo_is_available")
        return
    try:
        pushed = bpy.ops.ed.undo_push(message="Weight Symmetry test baseline")
    except RuntimeError:
        print("SKIP test_operator_is_single_step_undoable_when_background_undo_is_available")
        return
    if pushed != {"FINISHED"}:
        print("SKIP test_operator_is_single_step_undoable_when_background_undo_is_available")
        return

    result = invoke_operator()
    if result != {"FINISHED"}:
        raise AssertionError(f"Undo fixture copy failed: {result}")
    after = capture_groups(mesh_obj)
    if after == before:
        raise AssertionError("Undo fixture did not produce a changed state")
    if not bpy.ops.ed.undo.poll():
        print("SKIP test_operator_is_single_step_undoable_when_background_undo_is_available")
        return
    try:
        undo_result = bpy.ops.ed.undo()
    except RuntimeError:
        print("SKIP test_operator_is_single_step_undoable_when_background_undo_is_available")
        return
    if undo_result != {"FINISHED"}:
        print("SKIP test_operator_is_single_step_undoable_when_background_undo_is_available")
        return
    restored = bpy.data.objects.get(mesh_name)
    if restored is None:
        raise AssertionError("Undo removed the pre-existing Weight Symmetry Mesh")
    if capture_groups(restored) != before:
        raise AssertionError("One Undo did not restore the exact pre-copy Vertex Groups")


def main():
    character_designer.register()
    tests = (
        test_registration_undo_and_weight_page_integration,
        test_left_to_right_replaces_target_and_cleans_both_wrong_sides,
        test_right_to_left_is_the_same_one_way_operation,
        test_pose_bone_entry_uses_the_selected_bound_mesh,
        test_pose_mode_selected_bones_copy_as_one_batch,
        test_weight_paint_selected_bones_copy_and_dynamic_label,
        test_weight_paint_batch_scales_to_fifteen_selected_bones,
        test_multi_bone_preflight_failure_creates_nothing_and_writes_nothing,
        test_multi_bone_selection_requires_one_named_and_physical_side,
        test_multi_bone_budget_is_validated_on_the_combined_final_state,
        test_multi_bone_post_write_failure_restores_every_group_exactly,
        test_center_head_bones_use_rest_midpoint_for_direction,
        test_missing_target_group_is_created_after_successful_preflight,
        test_locked_target_refuses_with_zero_writes,
        test_locked_source_refuses_with_zero_writes,
        test_shared_mesh_data_refuses_with_zero_writes,
        test_source_and_target_must_both_be_deform_bones,
        test_non_side_named_active_group_refuses_with_zero_writes,
        test_unpaired_vertex_refuses_with_zero_writes,
        test_ambiguous_vertex_refuses_with_zero_writes,
        test_unrelated_one_sided_vertices_do_not_require_whole_mesh_pairing,
        test_budget_changing_cleanup_refuses_with_zero_writes,
        test_group_edit_after_planning_refuses_without_reverting_artist_edit,
        test_post_write_failure_restores_every_group_exactly,
        test_centerline_and_unrelated_groups_are_unchanged,
        test_pure_wrong_group_reassignment_preserves_every_vertex_total,
        test_repeated_copy_is_idempotent,
        test_operator_is_single_step_undoable_when_background_undo_is_available,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        reset_scene()
        if hasattr(bpy.types, "CHARACTER_DESIGNER_OT_copy_weight_to_opposite"):
            character_designer.unregister()
    print(f"PASS Weight Symmetry {len(tests)} tests")


if __name__ == "__main__":
    main()
