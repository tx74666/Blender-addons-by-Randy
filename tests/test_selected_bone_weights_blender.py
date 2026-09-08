"""Blender 5.2 regression tests for selected-bone Automatic Weights."""

import sys
from pathlib import Path

import bpy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDONS_ROOT = PROJECT_ROOT / "addons"
if str(ADDONS_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDONS_ROOT))

import character_designer
from character_designer import selected_bone_weights


def reset_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.armatures):
        for datablock in tuple(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)
    settings = getattr(bpy.context.window_manager, "character_designer", None)
    if settings is not None:
        settings.normalize_affected_deform_weights = False


def make_armature(name="SelectedWeightRig"):
    armature = bpy.data.armatures.new(f"{name}_Data")
    armature_obj = bpy.data.objects.new(name, armature)
    bpy.context.scene.collection.objects.link(armature_obj)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    definitions = (
        ("Bone.L", (-1.2, 0.0, -0.8), (-1.2, 0.0, 0.8), True),
        ("Spine", (0.0, 0.0, -0.8), (0.0, 0.0, 0.8), True),
        ("Bone.R", (1.2, 0.0, -0.8), (1.2, 0.0, 0.8), True),
        ("Helper", (0.0, 1.5, -0.5), (0.0, 1.5, 0.5), False),
    )
    for bone_name, head, tail, _deform in definitions:
        bone = armature.edit_bones.new(bone_name)
        bone.head = head
        bone.tail = tail
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone_name, _head, _tail, deform in definitions:
        armature.bones[bone_name].use_deform = deform
    return armature_obj


def _append_box(vertices, faces, center_x):
    first = len(vertices)
    for x, y, z in (
        (-0.42, -0.35, -0.65),
        (0.42, -0.35, -0.65),
        (0.42, 0.35, -0.65),
        (-0.42, 0.35, -0.65),
        (-0.42, -0.35, 0.65),
        (0.42, -0.35, 0.65),
        (0.42, 0.35, 0.65),
        (-0.42, 0.35, 0.65),
    ):
        vertices.append((x + center_x, y, z))
    faces.extend(
        tuple(first + index for index in face)
        for face in (
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (4, 0, 3, 7),
        )
    )


def make_mesh(armature_obj, with_stack=False):
    vertices = []
    faces = []
    for center_x in (-1.2, 0.0, 1.2):
        _append_box(vertices, faces, center_x)
    mesh = bpy.data.meshes.new("SelectedWeightMesh_Data")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    mesh_obj = bpy.data.objects.new("SelectedWeightMesh", mesh)
    bpy.context.scene.collection.objects.link(mesh_obj)

    if with_stack:
        mirror = mesh_obj.modifiers.new("Artist Mirror", "MIRROR")
        mirror.show_viewport = False
        subdivision = mesh_obj.modifiers.new("Artist Subdivision", "SUBSURF")
        subdivision.levels = 1
        subdivision.show_viewport = False
    armature_modifier = mesh_obj.modifiers.new("Existing Armature", "ARMATURE")
    armature_modifier.object = armature_obj

    groups = {
        "Bone.L": (tuple(range(0, 8)), 0.081),
        "Spine": (tuple(range(8, 16)), 0.237),
        "Bone.R": (tuple(range(16, 24)), 0.731),
        "Helper": ((1, 8, 17), 0.319),
        "Artist": ((0, 5, 7, 12, 21), 0.456),
    }
    for group_name, (indices, weight) in groups.items():
        group = mesh_obj.vertex_groups.new(name=group_name)
        group.add(indices, weight, "REPLACE")
    mesh_obj.vertex_groups["Artist"].add((3,), 0.0, "REPLACE")
    mesh_obj.vertex_groups.active_index = mesh_obj.vertex_groups["Artist"].index
    return mesh_obj, armature_modifier


def make_fixture(with_stack=False):
    reset_scene()
    armature_obj = make_armature()
    mesh_obj, armature_modifier = make_mesh(armature_obj, with_stack=with_stack)
    return mesh_obj, armature_obj, armature_modifier


def prepare_pose_context(mesh_obj, armature_obj, selected_names):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="POSE")
    for pose_bone in armature_obj.pose.bones:
        pose_bone.select = pose_bone.name in set(selected_names)
    armature_obj.data.bones.active = armature_obj.data.bones.get(selected_names[0])


def prepare_weight_paint_context(mesh_obj, armature_obj, selected_names):
    prepare_pose_context(mesh_obj, armature_obj, selected_names)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    result = bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
    if result != {"FINISHED"}:
        raise AssertionError("Fixture could not enter Weight Paint Mode")


def prepare_edit_armature_context(mesh_obj, armature_obj, selected_names):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    selected = set(selected_names)
    for edit_bone in armature_obj.data.edit_bones:
        edit_bone.select = edit_bone.name in selected
        edit_bone.select_head = edit_bone.select
        edit_bone.select_tail = edit_bone.select
    armature_obj.data.edit_bones.active = armature_obj.data.edit_bones.get(
        selected_names[0]
    )


def protected_states(states, selected_names):
    selected = set(selected_names)
    return tuple(state for state in states if state["name"] not in selected)


def modifier_signature(mesh_obj):
    return tuple(
        (
            modifier.as_pointer(),
            modifier.name,
            modifier.type,
            getattr(modifier, "object", None),
            modifier.show_viewport,
            modifier.show_render,
        )
        for modifier in mesh_obj.modifiers
    )


def _effective_group_weight(mesh_obj, group_name, vertex_index):
    group = mesh_obj.vertex_groups.get(group_name)
    if group is None:
        return 0.0
    try:
        return group.weight(vertex_index)
    except RuntimeError:
        return 0.0


def _deform_total(mesh_obj, armature_obj, vertex_index):
    return sum(
        _effective_group_weight(mesh_obj, bone.name, vertex_index)
        for bone in armature_obj.data.bones
        if bone.use_deform
    )


def _replace_group(mesh_obj, name, weights):
    existing = mesh_obj.vertex_groups.get(name)
    if existing is not None:
        mesh_obj.vertex_groups.remove(existing)
    group = mesh_obj.vertex_groups.new(name=name)
    for vertex_index, weight in weights.items():
        group.add((vertex_index,), weight, "REPLACE")
    return group


def test_pose_entry_is_transactional_and_mirror_safe():
    mesh_obj, armature_obj, _modifier = make_fixture(with_stack=True)
    mesh_obj.data.use_mirror_x = True
    mesh_obj.data.use_paint_mask = True
    bpy.context.scene.tool_settings.use_auto_normalize = True
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))

    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    before_target = selected_bone_weights._group_state_map(before)["Bone.L"]
    before_modifiers = modifier_signature(mesh_obj)
    before_deform = tuple(
        (bone.name, bone.use_deform) for bone in armature_obj.data.bones
    )
    active_group = mesh_obj.vertex_groups.active_index

    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"FINISHED"}:
        raise AssertionError(f"Selected-bone auto weight failed: {result}")

    after = selected_bone_weights._capture_vertex_groups(mesh_obj)
    after_map = selected_bone_weights._group_state_map(after)
    if protected_states(after, ("Bone.L",)) != protected_states(before, ("Bone.L",)):
        raise AssertionError("A protected group changed")
    if after_map["Bone.L"] == before_target:
        raise AssertionError("The selected bone group did not change")
    if modifier_signature(mesh_obj) != before_modifiers or mesh_obj.parent is not None:
        raise AssertionError("Parenting or modifier identity changed")
    if tuple((bone.name, bone.use_deform) for bone in armature_obj.data.bones) != before_deform:
        raise AssertionError("A Deform flag changed")
    if not mesh_obj.data.use_mirror_x or not mesh_obj.data.use_paint_mask:
        raise AssertionError("Temporary Mesh flags were not restored")
    if mesh_obj.vertex_groups.active_index != active_group:
        raise AssertionError("The active Vertex Group was not restored")
    if bpy.context.mode != "POSE" or bpy.context.active_object is not armature_obj:
        raise AssertionError("Pose Mode or active object was not restored")
    selected = tuple(pb.name for pb in armature_obj.pose.bones if pb.select)
    if selected != ("Bone.L",):
        raise AssertionError(f"Pose Bone selection changed: {selected}")


def test_multiple_selected_bones_change_without_normalizing_others():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.parent = armature_obj
    parent_inverse = mesh_obj.matrix_parent_inverse.copy()
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L", "Spine"))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    before_map = selected_bone_weights._group_state_map(before)

    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"FINISHED"}:
        raise AssertionError(f"Multi-bone Automatic Weights failed: {result}")
    after = selected_bone_weights._capture_vertex_groups(mesh_obj)
    after_map = selected_bone_weights._group_state_map(after)
    if protected_states(after, ("Bone.L", "Spine")) != protected_states(
        before,
        ("Bone.L", "Spine"),
    ):
        raise AssertionError("Multi-bone weighting changed a protected group")
    for name in ("Bone.L", "Spine"):
        if after_map[name] == before_map[name]:
            raise AssertionError(f"Selected group {name} did not change")
    if mesh_obj.parent is not armature_obj or mesh_obj.matrix_parent_inverse != parent_inverse:
        raise AssertionError("Existing parenting changed")


def test_full_auto_blend_preserves_selected_target_and_fills_remainder():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _replace_group(mesh_obj, "Bone.L", {0: 0.8})
    mesh_obj.vertex_groups["Spine"].add((0,), 0.6, "REPLACE")
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    before_map = selected_bone_weights._group_state_map(before)
    original = selected_bone_weights._run_native_auto_weights

    def deterministic_full_rig_result():
        mesh_obj.vertex_groups["Bone.L"].add((0,), 0.8, "REPLACE")
        mesh_obj.vertex_groups["Spine"].add((0,), 0.15, "REPLACE")
        mesh_obj.vertex_groups["Bone.R"].add((0,), 0.05, "REPLACE")
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = deterministic_full_rig_result
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"FINISHED"}:
        raise AssertionError(f"Full Auto Blend failed: {result}")
    if abs(_deform_total(mesh_obj, armature_obj, 0) - 1.0) > 1.0e-5:
        raise AssertionError("The full-auto blend is not normalized")
    if abs(_effective_group_weight(mesh_obj, "Bone.L", 0) - 0.8) > 1.0e-5:
        raise AssertionError("The selected Automatic Weight target was diluted")
    if abs(_effective_group_weight(mesh_obj, "Spine", 0) - 0.15) > 1.0e-5:
        raise AssertionError("Full-auto support did not fill the target remainder")
    if abs(_effective_group_weight(mesh_obj, "Bone.R", 0) - 0.05) > 1.0e-5:
        raise AssertionError("Full-auto support proportions changed")
    if abs(_deform_total(mesh_obj, armature_obj, 8) - 0.237) > 1.0e-7:
        raise AssertionError("A vertex outside the selected influence was normalized")
    after_map = selected_bone_weights._group_state_map(
        selected_bone_weights._capture_vertex_groups(mesh_obj)
    )
    for name in ("Helper", "Artist"):
        if after_map[name] != before_map[name]:
            raise AssertionError(f"Non-Deform group changed: {name}")


def test_full_weight_core_is_not_averaged_with_old_support():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _replace_group(mesh_obj, "Bone.L", {0: 1.0})
    mesh_obj.vertex_groups["Spine"].add((0,), 1.0, "REPLACE")
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    original = selected_bone_weights._run_native_auto_weights

    def full_weight_core():
        mesh_obj.vertex_groups["Bone.L"].add((0,), 1.0, "REPLACE")
        mesh_obj.vertex_groups["Spine"].add((0,), 1.0, "REPLACE")
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = full_weight_core
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"FINISHED"}:
        raise AssertionError(f"Full-weight core blend failed: {result}")
    if abs(_effective_group_weight(mesh_obj, "Bone.L", 0) - 1.0) > 1.0e-5:
        raise AssertionError("A selected full-weight core was reduced below 1")
    if _effective_group_weight(mesh_obj, "Spine", 0) > 1.0e-8:
        raise AssertionError("Old support survived inside a full-weight target core")


def test_full_auto_edge_preserves_target_and_support_ratio():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _replace_group(mesh_obj, "Bone.L", {})
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    original = selected_bone_weights._run_native_auto_weights

    def full_auto_edge():
        mesh_obj.vertex_groups["Bone.L"].add((0,), 0.75, "REPLACE")
        mesh_obj.vertex_groups["Spine"].add((0,), 0.375, "REPLACE")
        mesh_obj.vertex_groups["Bone.R"].add((0,), 0.125, "REPLACE")
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = full_auto_edge
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"FINISHED"}:
        raise AssertionError(f"Full-auto edge blend failed: {result}")
    expected = {"Bone.L": 0.75, "Spine": 0.1875, "Bone.R": 0.0625}
    for name, weight in expected.items():
        if abs(_effective_group_weight(mesh_obj, name, 0) - weight) > 1.0e-5:
            raise AssertionError(f"Unexpected full-auto edge weight for {name}")
    if abs(_deform_total(mesh_obj, armature_obj, 0) - 1.0) > 1.0e-5:
        raise AssertionError("The full-auto edge is not normalized")


def test_full_auto_blend_cleans_old_target_footprint():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _replace_group(mesh_obj, "Bone.L", {0: 0.8})
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    original = selected_bone_weights._run_native_auto_weights

    def relocated_full_auto_result():
        mesh_obj.vertex_groups["Spine"].add((0,), 1.0, "REPLACE")
        mesh_obj.vertex_groups["Bone.L"].add((16,), 0.9, "REPLACE")
        mesh_obj.vertex_groups["Bone.R"].add((16,), 0.1, "REPLACE")
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = relocated_full_auto_result
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"FINISHED"}:
        raise AssertionError(f"Old-footprint cleanup failed: {result}")
    if _effective_group_weight(mesh_obj, "Bone.L", 0) > 1.0e-8:
        raise AssertionError("The obsolete selected-bone footprint survived")
    if abs(_effective_group_weight(mesh_obj, "Spine", 0) - 1.0) > 1.0e-5:
        raise AssertionError("Full-auto support did not repair the old footprint")
    if abs(_effective_group_weight(mesh_obj, "Bone.L", 16) - 0.9) > 1.0e-5:
        raise AssertionError("The relocated selected target was not applied")


def test_opt_in_preserves_locked_deform_budget():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _replace_group(mesh_obj, "Bone.L", {})
    mesh_obj.vertex_groups["Spine"].add((0,), 0.25, "REPLACE")
    mesh_obj.vertex_groups["Spine"].lock_weight = True
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    original = selected_bone_weights._run_native_auto_weights

    def add_selected_result():
        mesh_obj.vertex_groups["Bone.L"].add((0,), 0.8, "REPLACE")
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = add_selected_result
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"FINISHED"}:
        raise AssertionError(f"Locked-budget normalization failed: {result}")
    if _effective_group_weight(mesh_obj, "Spine", 0) != 0.25:
        raise AssertionError("The locked Deform weight changed")
    if abs(_effective_group_weight(mesh_obj, "Bone.L", 0) - 0.75) > 1.0e-5:
        raise AssertionError("The selected weight did not fill the unlocked budget")
    if abs(_deform_total(mesh_obj, armature_obj, 0) - 1.0) > 1.0e-5:
        raise AssertionError("The locked-budget result is not normalized")


def test_impossible_locked_budget_rolls_back_everything():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _replace_group(mesh_obj, "Bone.L", {})
    mesh_obj.vertex_groups["Spine"].add((0,), 0.7, "REPLACE")
    mesh_obj.vertex_groups["Bone.R"].add((0,), 0.6, "REPLACE")
    mesh_obj.vertex_groups["Spine"].lock_weight = True
    mesh_obj.vertex_groups["Bone.R"].lock_weight = True
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    original = selected_bone_weights._run_native_auto_weights

    def add_selected_result():
        mesh_obj.vertex_groups["Bone.L"].add((0,), 0.2, "REPLACE")
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = add_selected_result
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"CANCELLED"}:
        raise AssertionError("An impossible locked budget was not rejected")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Locked-budget failure did not roll back exactly")


def test_zero_deform_budget_rolls_back_everything():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _replace_group(mesh_obj, "Bone.L", {0: 0.2})
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    original = selected_bone_weights._run_native_auto_weights

    def move_selected_result():
        mesh_obj.vertex_groups["Bone.L"].add((1,), 0.8, "REPLACE")
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = move_selected_result
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"CANCELLED"}:
        raise AssertionError("A zero Deform budget was not rejected")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Zero-budget failure did not roll back exactly")


def test_partial_normalization_write_failure_rolls_back_everything():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _replace_group(mesh_obj, "Bone.L", {0: 0.8})
    mesh_obj.vertex_groups["Spine"].add((0,), 0.6, "REPLACE")
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    original_native = selected_bone_weights._run_native_auto_weights
    original_apply = selected_bone_weights._apply_normalization_plan

    def deterministic_same_result():
        mesh_obj.vertex_groups["Bone.L"].add((0,), 0.8, "REPLACE")
        return {"FINISHED"}

    def fail_after_normalization_write(target_mesh, plan):
        original_apply(target_mesh, plan)
        raise RuntimeError("Injected failure after normalization writes")

    selected_bone_weights._run_native_auto_weights = deterministic_same_result
    selected_bone_weights._apply_normalization_plan = fail_after_normalization_write
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original_native
        selected_bone_weights._apply_normalization_plan = original_apply
    if result != {"CANCELLED"}:
        raise AssertionError("A partial normalization write failure was not rejected")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Partial normalization writes were not rolled back exactly")
    if bpy.context.mode != "POSE" or bpy.context.active_object is not armature_obj:
        raise AssertionError("Context was not restored after normalization rollback")


def test_weight_paint_entry_and_missing_target_group():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Spine"])
    prepare_weight_paint_context(mesh_obj, armature_obj, ("Spine",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)

    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"FINISHED"}:
        raise AssertionError(f"Missing target group was not created: {result}")
    after = selected_bone_weights._capture_vertex_groups(mesh_obj)
    if protected_states(after, ("Spine",)) != protected_states(before, ("Spine",)):
        raise AssertionError("A protected group changed while creating the target group")
    spine = selected_bone_weights._group_state_map(after).get("Spine")
    if spine is None or not any(weight > 0.0 for _index, weight in spine["weights"]):
        raise AssertionError("The missing selected group has no generated weights")
    if bpy.context.mode != "PAINT_WEIGHT" or bpy.context.active_object is not mesh_obj:
        raise AssertionError("Weight Paint Mode was not restored")


def test_edit_armature_entry_restores_edit_selection():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_edit_armature_context(mesh_obj, armature_obj, ("Spine",))
    spine = armature_obj.data.edit_bones["Spine"]
    spine.select = True
    spine.select_head = False
    spine.select_tail = True
    before_selection = tuple(
        (
            bone.name,
            bool(bone.select),
            bool(bone.select_head),
            bool(bone.select_tail),
        )
        for bone in armature_obj.data.edit_bones
    )
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"FINISHED"}:
        raise AssertionError(f"Edit Armature entry failed: {result}")
    after = selected_bone_weights._capture_vertex_groups(mesh_obj)
    if protected_states(after, ("Spine",)) != protected_states(before, ("Spine",)):
        raise AssertionError("Edit Armature entry changed a protected group")
    if bpy.context.mode != "EDIT_ARMATURE" or bpy.context.active_object is not armature_obj:
        raise AssertionError("Edit Armature Mode was not restored")
    after_selection = tuple(
        (
            bone.name,
            bool(bone.select),
            bool(bone.select_head),
            bool(bone.select_tail),
        )
        for bone in armature_obj.data.edit_bones
    )
    if after_selection != before_selection:
        raise AssertionError(
            f"Edit Bone selection changed: {before_selection} -> {after_selection}"
        )


def test_edit_mode_unsynced_deform_state_is_preserved():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_edit_armature_context(mesh_obj, armature_obj, ("Spine",))
    armature_obj.data.edit_bones["Bone.R"].use_deform = False
    before_deform = tuple(
        (bone.name, bool(bone.use_deform))
        for bone in armature_obj.data.edit_bones
    )
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"FINISHED"}:
        raise AssertionError(f"Edit Deform preservation run failed: {result}")
    after_deform = tuple(
        (bone.name, bool(bone.use_deform))
        for bone in armature_obj.data.edit_bones
    )
    if after_deform != before_deform:
        raise AssertionError(
            f"An unsynced EditBone Deform value changed: {before_deform} -> {after_deform}"
        )


def test_full_auto_uses_unsynced_edit_mode_deform_roster():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_edit_armature_context(mesh_obj, armature_obj, ("Spine",))
    armature_obj.data.edit_bones["Bone.R"].use_deform = False
    before_deform = tuple(
        (bone.name, bool(bone.use_deform))
        for bone in armature_obj.data.edit_bones
    )
    result = bpy.ops.character_designer.auto_weight_selected_bones(
        normalize_affected_deform_weights=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Full Auto rejected the EditBone Deform roster: {result}")
    after_deform = tuple(
        (bone.name, bool(bone.use_deform))
        for bone in armature_obj.data.edit_bones
    )
    if after_deform != before_deform:
        raise AssertionError(
            f"Full Auto changed an unsynced EditBone Deform value: "
            f"{before_deform} -> {after_deform}"
        )


def test_mesh_edit_mode_is_not_allowed():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode="EDIT")
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    if bpy.ops.character_designer.auto_weight_selected_bones.poll():
        raise AssertionError("The operator is available from Mesh Edit Mode")
    if bpy.context.mode != "EDIT_MESH":
        raise AssertionError("The unsupported-mode poll changed the mode")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("The unsupported-mode poll changed weights")


def test_explicit_invalid_mesh_is_refused_without_fallback():
    mesh_obj, armature_obj, _modifier = make_fixture()
    invalid_data = bpy.data.meshes.new("ExplicitInvalidMesh_Data")
    invalid_data.from_pydata(((0.0, 0.0, 0.0),), (), ())
    invalid_obj = bpy.data.objects.new("ExplicitInvalidMesh", invalid_data)
    bpy.context.scene.collection.objects.link(invalid_obj)
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    mesh_obj.select_set(False)
    invalid_obj.select_set(True)
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("An explicitly selected invalid Mesh was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("The unselected valid Mesh was modified by fallback")


def test_locked_selected_group_is_refused_atomically():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups["Bone.L"].lock_weight = True
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("A locked selected group was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Locked-group preflight changed weights")


def test_only_nondeform_selection_is_refused():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_pose_context(mesh_obj, armature_obj, ("Helper",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("A non-Deform-only selection was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Non-Deform preflight changed weights")


def test_protected_corruption_triggers_full_rollback():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    before_deform = tuple(
        (bone.name, bone.use_deform) for bone in armature_obj.data.bones
    )
    original = selected_bone_weights._run_native_auto_weights

    def corrupt_protected_state():
        mesh_obj.vertex_groups["Artist"].add((0,), 0.999, "REPLACE")
        armature_obj.data.bones["Bone.R"].use_deform = False
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = corrupt_protected_state
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones()
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"CANCELLED"}:
        raise AssertionError("Protected corruption was not rejected")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("The complete Vertex Group state was not rolled back")
    if tuple((bone.name, bone.use_deform) for bone in armature_obj.data.bones) != before_deform:
        raise AssertionError("Deform flags were not rolled back")
    if bpy.context.mode != "POSE" or bpy.context.active_object is not armature_obj:
        raise AssertionError("Context was not restored after rollback")


def test_deleted_and_renamed_groups_are_rebuilt_on_rollback():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    original = selected_bone_weights._run_native_auto_weights

    def corrupt_group_definitions():
        mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Artist"])
        mesh_obj.vertex_groups["Helper"].name = "Helper_Renamed"
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = corrupt_group_definitions
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones()
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"CANCELLED"}:
        raise AssertionError("Corrupted group definitions were not rejected")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Deleted or renamed groups were not rebuilt exactly")


def test_context_restore_failure_rolls_back_weights():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    original = selected_bone_weights._restore_context_state

    def fail_context_restore(*_args, **_kwargs):
        raise RuntimeError("Injected context restore failure")

    selected_bone_weights._restore_context_state = fail_context_restore
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones()
    finally:
        selected_bone_weights._restore_context_state = original
    if result != {"CANCELLED"}:
        raise AssertionError("A context restore failure was not reported")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Weights remained changed after context restore failure")


def test_native_cancel_rolls_back_selected_groups():
    mesh_obj, armature_obj, _modifier = make_fixture()
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L", "Spine"))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    original = selected_bone_weights._run_native_auto_weights
    selected_bone_weights._run_native_auto_weights = lambda: {"CANCELLED"}
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones()
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"CANCELLED"}:
        raise AssertionError("Native cancellation was not propagated")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Selected groups were not restored after cancellation")


def test_multiple_armatures_are_refused_without_guessing():
    mesh_obj, armature_obj, _modifier = make_fixture()
    other_armature = make_armature("OtherRig")
    second = mesh_obj.modifiers.new("Second Armature", "ARMATURE")
    second.object = other_armature
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    signature = modifier_signature(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("An ambiguous Armature stack was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Ambiguous preflight changed weights")
    if modifier_signature(mesh_obj) != signature:
        raise AssertionError("Ambiguous preflight changed modifiers")


def _configure_positive_half_mesh_mirror(mesh_obj):
    mirror = mesh_obj.modifiers.new("Half Mesh Mirror", "MIRROR")
    mirror.use_axis[0] = True
    mirror.use_mirror_vertex_groups = True
    mesh_obj.modifiers.move(mesh_obj.modifiers.find(mirror.name), 0)
    for vertex in mesh_obj.data.vertices:
        vertex.co.x = abs(vertex.co.x)
    mesh_obj.data.update()
    return mirror


def _evaluated_group_side_counts(mesh_obj, group_name):
    group = mesh_obj.vertex_groups.get(group_name)
    if group is None:
        return 0, 0
    evaluated = mesh_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        positive = 0
        negative = 0
        for vertex in mesh.vertices:
            weighted = any(
                membership.group == group.index and membership.weight > 1.0e-6
                for membership in vertex.groups
            )
            if not weighted:
                continue
            if vertex.co.x > 1.0e-5:
                positive += 1
            elif vertex.co.x < -1.0e-5:
                negative += 1
        return positive, negative
    finally:
        evaluated.to_mesh_clear()


def test_source_side_half_mesh_creates_empty_mirror_pair():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Bone.L"])
    _configure_positive_half_mesh_mirror(mesh_obj)
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.R",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    before_map = selected_bone_weights._group_state_map(before)

    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"FINISHED"}:
        raise AssertionError(f"Half-Mesh source weighting failed: {result}")

    after = selected_bone_weights._capture_vertex_groups(mesh_obj)
    after_map = selected_bone_weights._group_state_map(after)
    for name, state in before_map.items():
        if name != "Bone.R" and after_map.get(name) != state:
            raise AssertionError(f"Protected group changed: {name}")
    pair = after_map.get("Bone.L")
    if pair is None or pair["weights"]:
        raise AssertionError("The missing Mirror counterpart is not an empty group")
    selected = after_map.get("Bone.R")
    if selected is None or not any(weight > 0.0 for _index, weight in selected["weights"]):
        raise AssertionError("The selected source group has no usable weights")
    positive, negative = _evaluated_group_side_counts(mesh_obj, "Bone.L")
    if positive != 0 or negative == 0:
        raise AssertionError(
            f"The empty counterpart did not receive evaluated mirrored weights: "
            f"+X={positive}, -X={negative}"
        )


def test_source_side_half_mesh_normalizes_without_filling_pair():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Bone.L"])
    _configure_positive_half_mesh_mirror(mesh_obj)
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.R",))

    result = bpy.ops.character_designer.auto_weight_selected_bones(
        normalize_affected_deform_weights=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Normalized half-Mesh weighting failed: {result}")

    states = selected_bone_weights._group_state_map(
        selected_bone_weights._capture_vertex_groups(mesh_obj)
    )
    pair = states.get("Bone.L")
    if pair is None or pair["weights"]:
        raise AssertionError("Normalization filled the empty Mirror counterpart")
    selected = states["Bone.R"]
    affected = tuple(
        vertex_index
        for vertex_index, weight in selected["weights"]
        if weight > 1.0e-8
    )
    if not affected:
        raise AssertionError("The half-Mesh source group has no affected vertices")
    for vertex_index in affected:
        if abs(_deform_total(mesh_obj, armature_obj, vertex_index) - 1.0) > 1.0e-5:
            raise AssertionError(
                f"Half-Mesh vertex {vertex_index} is not normalized"
            )


def test_full_auto_blend_clears_existing_generated_side_pair():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _configure_positive_half_mesh_mirror(mesh_obj)
    if not selected_bone_weights._state_weight_map(
        selected_bone_weights._group_state_map(
            selected_bone_weights._capture_vertex_groups(mesh_obj)
        )["Bone.L"]
    ):
        raise AssertionError("Fixture needs a non-empty generated-side pair")
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.R",))

    result = bpy.ops.character_designer.auto_weight_selected_bones(
        normalize_affected_deform_weights=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Existing pair cleanup failed: {result}")
    pair = selected_bone_weights._group_state_map(
        selected_bone_weights._capture_vertex_groups(mesh_obj)
    )["Bone.L"]
    if pair["weights"]:
        raise AssertionError("Generated-side base weights were not cleared")


def test_full_auto_clears_all_generated_side_support_on_half_mesh():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _configure_positive_half_mesh_mirror(mesh_obj)
    _replace_group(mesh_obj, "Spine", {})
    _replace_group(mesh_obj, "Bone.L", {0: 0.081})
    prepare_pose_context(mesh_obj, armature_obj, ("Spine",))
    original = selected_bone_weights._run_native_auto_weights

    def full_source_domain_result():
        mesh_obj.vertex_groups["Spine"].add((0,), 0.5, "REPLACE")
        mesh_obj.vertex_groups["Bone.R"].add((0,), 0.5, "REPLACE")
        return {"FINISHED"}

    selected_bone_weights._run_native_auto_weights = full_source_domain_result
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones(
            normalize_affected_deform_weights=True,
        )
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"FINISHED"}:
        raise AssertionError(f"Generated-side support cleanup failed: {result}")
    states = selected_bone_weights._group_state_map(
        selected_bone_weights._capture_vertex_groups(mesh_obj)
    )
    if states["Bone.L"]["weights"]:
        raise AssertionError(
            "An unselected generated-side group retained base-Mesh weights"
        )
    if abs(_effective_group_weight(mesh_obj, "Spine", 0) - 0.5) > 1.0e-5:
        raise AssertionError("Selected center-bone solver weight changed")
    if abs(_effective_group_weight(mesh_obj, "Bone.R", 0) - 0.5) > 1.0e-5:
        raise AssertionError("Source-side full-solver support changed")
    if abs(_deform_total(mesh_obj, armature_obj, 0) - 1.0) > 1.0e-5:
        raise AssertionError("Half-Mesh source-domain result is not normalized")


def test_half_mesh_pair_creation_rolls_back_on_native_cancel():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Bone.L"])
    _configure_positive_half_mesh_mirror(mesh_obj)
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.R",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    original = selected_bone_weights._run_native_auto_weights
    selected_bone_weights._run_native_auto_weights = lambda: {"CANCELLED"}
    try:
        result = bpy.ops.character_designer.auto_weight_selected_bones()
    finally:
        selected_bone_weights._run_native_auto_weights = original
    if result != {"CANCELLED"}:
        raise AssertionError("Half-Mesh cancellation was not propagated")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Half-Mesh pair creation was not rolled back exactly")


def test_generated_side_bone_on_half_mesh_is_refused():
    mesh_obj, armature_obj, _modifier = make_fixture()
    _configure_positive_half_mesh_mirror(mesh_obj)
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("A generated-side bone on a half Mesh was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Half-Mesh side preflight changed weights")


def test_shared_mesh_data_is_refused_without_changes():
    mesh_obj, armature_obj, _modifier = make_fixture()
    linked_duplicate = mesh_obj.copy()
    linked_duplicate.name = "LinkedWeightDuplicate"
    bpy.context.scene.collection.objects.link(linked_duplicate)
    if linked_duplicate.data is not mesh_obj.data or mesh_obj.data.users != 2:
        raise AssertionError("Fixture did not create two users of one Mesh data-block")
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.L",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("Shared Mesh data was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Shared-data preflight changed weights")


def test_custom_mirror_object_is_refused_without_changes():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Bone.L"])
    mirror = mesh_obj.modifiers.new("Custom Half Mesh Mirror", "MIRROR")
    mirror.use_axis[0] = True
    mirror.use_mirror_vertex_groups = True
    mesh_obj.modifiers.move(mesh_obj.modifiers.find(mirror.name), 0)
    mirror_object = bpy.data.objects.new("CustomMirrorPlane", None)
    bpy.context.scene.collection.objects.link(mirror_object)
    mirror_object.location.x = -3.0
    mirror.mirror_object = mirror_object
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.R",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("A custom Mirror Object was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Custom-Mirror preflight changed weights")


def test_multiple_x_mirrors_are_refused_without_changes():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Bone.L"])
    _configure_positive_half_mesh_mirror(mesh_obj)
    second = mesh_obj.modifiers.new("Second Half Mesh Mirror", "MIRROR")
    second.use_axis[0] = True
    second.use_mirror_vertex_groups = True
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.R",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("Multiple active X Mirrors were not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Multiple-Mirror preflight changed weights")


def test_mirror_after_armature_is_refused_without_changes():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Bone.L"])
    mirror = mesh_obj.modifiers.new("Late Half Mesh Mirror", "MIRROR")
    mirror.use_axis[0] = True
    mirror.use_mirror_vertex_groups = True
    for vertex in mesh_obj.data.vertices:
        vertex.co.x = abs(vertex.co.x)
    mesh_obj.data.update()
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.R",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("A Mirror after Armature was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Late-Mirror preflight changed weights")


def test_half_mesh_without_group_mirroring_is_refused_without_changes():
    mesh_obj, armature_obj, _modifier = make_fixture()
    mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups["Bone.L"])
    mirror = _configure_positive_half_mesh_mirror(mesh_obj)
    mirror.use_mirror_vertex_groups = False
    prepare_pose_context(mesh_obj, armature_obj, ("Bone.R",))
    before = selected_bone_weights._capture_vertex_groups(mesh_obj)
    result = bpy.ops.character_designer.auto_weight_selected_bones()
    if result != {"CANCELLED"}:
        raise AssertionError("A half Mesh without group mirroring was not refused")
    if selected_bone_weights._capture_vertex_groups(mesh_obj) != before:
        raise AssertionError("Disabled group-mirroring preflight changed weights")


def main():
    character_designer.register()
    tests = (
        test_pose_entry_is_transactional_and_mirror_safe,
        test_multiple_selected_bones_change_without_normalizing_others,
        test_full_auto_blend_preserves_selected_target_and_fills_remainder,
        test_full_weight_core_is_not_averaged_with_old_support,
        test_full_auto_edge_preserves_target_and_support_ratio,
        test_full_auto_blend_cleans_old_target_footprint,
        test_opt_in_preserves_locked_deform_budget,
        test_impossible_locked_budget_rolls_back_everything,
        test_zero_deform_budget_rolls_back_everything,
        test_partial_normalization_write_failure_rolls_back_everything,
        test_weight_paint_entry_and_missing_target_group,
        test_edit_armature_entry_restores_edit_selection,
        test_edit_mode_unsynced_deform_state_is_preserved,
        test_full_auto_uses_unsynced_edit_mode_deform_roster,
        test_mesh_edit_mode_is_not_allowed,
        test_explicit_invalid_mesh_is_refused_without_fallback,
        test_locked_selected_group_is_refused_atomically,
        test_only_nondeform_selection_is_refused,
        test_protected_corruption_triggers_full_rollback,
        test_deleted_and_renamed_groups_are_rebuilt_on_rollback,
        test_context_restore_failure_rolls_back_weights,
        test_native_cancel_rolls_back_selected_groups,
        test_multiple_armatures_are_refused_without_guessing,
        test_source_side_half_mesh_creates_empty_mirror_pair,
        test_source_side_half_mesh_normalizes_without_filling_pair,
        test_full_auto_blend_clears_existing_generated_side_pair,
        test_full_auto_clears_all_generated_side_support_on_half_mesh,
        test_half_mesh_pair_creation_rolls_back_on_native_cancel,
        test_generated_side_bone_on_half_mesh_is_refused,
        test_shared_mesh_data_is_refused_without_changes,
        test_custom_mirror_object_is_refused_without_changes,
        test_multiple_x_mirrors_are_refused_without_changes,
        test_mirror_after_armature_is_refused_without_changes,
        test_half_mesh_without_group_mirroring_is_refused_without_changes,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        if hasattr(bpy.types, "CHARACTERDESIGNER_PT_weight_tools"):
            character_designer.unregister()
    print(f"PASS Selected Bone Weights {len(tests)} tests")


if __name__ == "__main__":
    main()
