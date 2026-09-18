"""Behavioral Blender tests for explicit, local surface weight mirroring.

Run with Blender --background --factory-startup --python-exit-code 1 --python
tests/test_weight_surface_blender.py. All fixtures are synthetic; no artist
file is opened. Source and target deliberately have different topology.
"""

import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
from character_designer import weight_surface as surface
from character_designer import weight_symmetry as ws


SOURCE = (0, 1, 2, 3)
TARGET = (4, 5, 6, 7, 8)
CENTER = (9, 10)
UNRELATED = tuple(range(11, 19))
EPSILON = 1e-6


def assert_close(actual, expected):
    assert math.isclose(actual, expected, abs_tol=EPSILON), (actual, expected)


def reset_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def capture_groups(obj):
    return ws._capture_vertex_groups(obj)


def group_maps(obj):
    return {s.name: dict(s.weights) for s in capture_groups(obj)}


def capture_protected(obj, rig):
    return (
        tuple(tuple(v.co) for v in obj.data.vertices),
        tuple(tuple(e.vertices) for e in obj.data.edges),
        tuple(tuple(p.vertices) for p in obj.data.polygons),
        tuple((key.name, key.value, tuple(tuple(p.co) for p in key.data))
              for key in obj.data.shape_keys.key_blocks),
        tuple((b.name, b.parent.name if b.parent else None, b.use_deform,
               tuple(tuple(row) for row in b.matrix_local))
              for b in rig.data.bones),
        tuple(tuple(row) for row in obj.matrix_world),
        tuple(tuple(row) for row in rig.matrix_world),
        tuple((m.name, m.type, m.object.name if m.type == "ARMATURE" else None)
              for m in obj.modifiers),
    )


def add_group(obj, name, weights, locked=False):
    group = obj.vertex_groups.new(name=name)
    for index, value in weights.items():
        group.add((index,), value, "REPLACE")
    group.lock_weight = locked
    return group


def make_fixture(*, target_offset=0.0, duplicate_surface=False,
                 missing_mapped_bone=False, reverse=False):
    reset_scene()
    data = bpy.data.armatures.new("SurfaceRig.Data")
    rig = bpy.data.objects.new("SurfaceRig", data)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for stem in ("forearm", "upper_arm", "hand"):
        for side, sign in (("L", 1), ("R", -1)):
            bone = data.edit_bones.new(f"{stem}.{side}")
            bone.head = (sign * 1.5, 0, 0)
            bone.tail = (sign * 1.5, 1, 0)
    bone = data.edit_bones.new("spine")
    bone.head, bone.tail = (0, 0, -1), (0, 0, 1)
    bone = data.edit_bones.new("CTRL_artist")
    bone.head, bone.tail = (0, -1, 0), (0, -1, 1)
    bone.use_deform = False
    if missing_mapped_bone:
        bone = data.edit_bones.new("orphan.L")
        bone.head, bone.tail = (1, 0, 0), (1, 1, 0)
    bpy.ops.object.mode_set(mode="OBJECT")

    vertices = [
        (1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0),
        (-1, 0, 0), (-2, 0, 0), (-2, 1, 0), (-1, 1, 0),
        (-1.5, .5, target_offset), (0, 0, 0), (0, 1, 0),
        (3, 0, 0), (4, 0, 0), (4, 1, 0), (3, 1, 0),
        (-3, 0, 0), (-4, 0, 0), (-4, 1, 0), (-3, 1, 0),
    ]
    # Reverse mirrored winding so both sheets retain the same outward +Z
    # normal, as the two sides of a correctly oriented closed body would.
    faces = [(0, 1, 2, 3), (8, 5, 4), (8, 6, 5),
             (8, 7, 6), (8, 4, 7), (11, 12, 13, 14), (18, 17, 16, 15)]
    if duplicate_surface:
        vertices.extend(vertices[:4])
        faces.append((19, 20, 21, 22))
    if reverse:
        vertices = [(-x, y, z) for x, y, z in vertices]
    mesh = bpy.data.meshes.new("SurfaceMesh.Data")
    mesh.from_pydata(vertices, [(9, 10)], faces)
    mesh.update()
    obj = bpy.data.objects.new("SurfaceMesh", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new("Skin", "ARMATURE")
    modifier.object = rig
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Artist.Expression")
    key.data[8].co.z += .125
    key.value = .35

    source_side, target_side = ("R", "L") if reverse else ("L", "R")
    source_indices = SOURCE + (tuple(range(19, 23)) if duplicate_surface else ())
    forearm = {i: .2 + .6 * vertices[i][1] for i in source_indices}
    upper = {i: .7 - .6 * vertices[i][1] for i in source_indices}
    spine = {i: .1 for i in source_indices}
    forearm.update({i: .25 for i in TARGET})  # Deliberate wrong-side influence.
    forearm.update({9: .17, 10: .23})
    add_group(obj, f"forearm.{source_side}", forearm)
    add_group(obj, f"upper_arm.{source_side}", upper)
    add_group(obj, f"forearm.{target_side}", {i: .10 for i in TARGET} | {9: .29, 10: .19})
    add_group(obj, f"upper_arm.{target_side}", {i: .20 for i in TARGET})
    spine.update({i: .25 for i in TARGET})
    spine.update({9: .33, 10: .35})
    add_group(obj, "spine", spine)
    add_group(obj, f"hand.{source_side}", {i: .73 for i in range(11, 15)})
    add_group(obj, f"hand.{target_side}", {i: .41 for i in range(15, 19)})
    add_group(obj, "ArtistMask", {i: .15 + .02 * i for i in range(len(vertices))}, locked=True)
    add_group(obj, "CTRL_artist", {i: .36 for i in range(len(vertices))}, locked=True)
    if missing_mapped_bone:
        add_group(obj, "orphan.L", {i: .025 for i in SOURCE})
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = obj
    obj.vertex_groups.active_index = obj.vertex_groups[f"forearm.{source_side}"].index
    return obj, rig, f"forearm.{source_side}", f"forearm.{target_side}"


def plan_for(obj, rig, source, **kwargs):
    return surface.build_surface_plan_for_sources(bpy.context, obj, rig, (source,), **kwargs)


def expect_error(operation, *, contains=None):
    try:
        operation()
    except ws.WeightSymmetryError as exc:
        if contains:
            assert contains.lower() in str(exc).lower(), str(exc)
        return exc
    raise AssertionError("Expected transactional preflight/apply failure")


def test_nonmatching_topology_interpolates_full_vector_with_original_budget():
    obj, rig, source, target = make_fixture()
    protected = capture_protected(obj, rig)
    before = group_maps(obj)
    plan = surface.build_surface_plan(bpy.context)
    assert plan.source_names == (source,)
    assert plan.target_names == (target,)
    assert set(plan.affected_indices) == set(TARGET)
    assert plan.sampled_count == len(TARGET)
    assert_close(plan.max_sample_distance, 0)
    assert_close(plan.distance_limit, .02)
    assert capture_groups(obj) == plan.group_snapshot  # Preview is read-only.
    surface.apply_surface_plan(plan)
    after = group_maps(obj)
    deform = {b.name for b in rig.data.bones if b.use_deform}
    for index in TARGET:
        budget = sum(before.get(name, {}).get(index, 0) for name in deform)
        y = obj.data.vertices[index].co.y
        assert_close(after[target][index], (.2 + .6 * y) * budget)
        assert_close(after["upper_arm.R"][index], (.7 - .6 * y) * budget)
        assert_close(after["spine"][index], .1 * budget)
        assert index not in after[source]
        assert_close(sum(after.get(n, {}).get(index, 0) for n in deform), budget)
    assert_close(after[target][8], .4)  # Extra target vertex has no source vertex pair.
    assert capture_protected(obj, rig) == protected


def test_source_center_other_region_and_non_deform_are_exactly_unchanged():
    obj, rig, source, _ = make_fixture()
    before = group_maps(obj)
    surface.apply_surface_plan(plan_for(obj, rig, source))
    after = group_maps(obj)
    outside = SOURCE + CENTER + UNRELATED
    for name, weights in before.items():
        for index in outside:
            assert (index in weights, weights.get(index)) == (index in after[name], after[name].get(index))
    for name in ("ArtistMask", "CTRL_artist"):
        assert before[name] == after[name]
        assert obj.vertex_groups[name].lock_weight


def test_right_to_left_has_the_same_sampling_and_budget_contract():
    obj, rig, source, target = make_fixture(reverse=True)
    before = group_maps(obj)
    plan = plan_for(obj, rig, source)
    assert plan.source_side == -1
    surface.apply_surface_plan(plan)
    after = group_maps(obj)
    assert_close(after[target][8], .4)
    assert_close(after["upper_arm.L"][8], .32)
    assert_close(after["spine"][8], .08)
    assert before["ArtistMask"] == after["ArtistMask"]


def test_two_selected_bones_share_one_atomic_region_and_do_not_double_apply():
    obj, rig, source, _ = make_fixture()
    plan = surface.build_surface_plan_for_sources(
        bpy.context, obj, rig, (source, "upper_arm.L"))
    assert plan.sampled_count == len(TARGET)
    surface.apply_surface_plan(plan)
    assert_close(group_maps(obj)["forearm.R"][8], .4)


def test_oversized_surface_gap_refuses_without_writes_and_explicit_bound_can_allow_it():
    obj, rig, source, _ = make_fixture(target_offset=.10)
    before = capture_groups(obj)
    exc = expect_error(lambda: plan_for(obj, rig, source), contains="too far")
    assert 8 in exc.vertex_indices
    assert capture_groups(obj) == before
    plan = plan_for(obj, rig, source, max_distance=.11)
    assert_close(plan.max_sample_distance, .10)
    surface.apply_surface_plan(plan)
    assert_close(group_maps(obj)["forearm.R"][8], .4)


def test_disconnected_equidistant_surfaces_are_not_arbitrarily_resolved():
    obj, rig, source, _ = make_fixture(duplicate_surface=True)
    before = capture_groups(obj)
    exc = expect_error(lambda: plan_for(obj, rig, source), contains="ambiguous")
    assert exc.vertex_indices
    assert capture_groups(obj) == before


def test_disconnected_unequal_but_nearby_surfaces_are_still_ambiguous():
    obj, rig, source, _ = make_fixture(duplicate_surface=True)
    # One sheet is an exact match; the independent sheet is 0.001 away.
    # This exceeds numerical pairing tolerance but is inside 10% of the
    # automatic 0.02 surface distance. Picking the exact one is still unsafe.
    for index in range(19, 23):
        obj.data.vertices[index].co.z = .001
        for key in obj.data.shape_keys.key_blocks:
            key.data[index].co.z = .001
    obj.data.update()
    assert .001 > ws._automatic_tolerance(obj)
    before, protected = capture_groups(obj), capture_protected(obj, rig)
    exc = expect_error(lambda: plan_for(obj, rig, source), contains="ambiguous")
    assert set(exc.vertex_indices) == set(TARGET)
    assert capture_groups(obj) == before
    assert capture_protected(obj, rig) == protected


def test_far_unrelated_target_is_not_blocked_by_nearest_selected_source_face():
    obj, rig, source, target = make_fixture()
    # Move the unrelated target patch directly above the selected source's
    # reflected footprint. Its closest source triangles now carry forearm.L,
    # but it remains far away and has only hand.R weights, so must be skipped.
    for index, co in zip(range(15, 19),
                         ((-1, 0, 2), (-2, 0, 2), (-2, 1, 2), (-1, 1, 2))):
        obj.data.vertices[index].co = co
        for key in obj.data.shape_keys.key_blocks:
            key.data[index].co = co
    obj.data.update()
    before, protected = group_maps(obj), capture_protected(obj, rig)
    assert not set(range(15, 19)).intersection(before[target])
    plan = plan_for(obj, rig, source)
    assert set(plan.affected_indices) == set(TARGET)
    assert plan.sampled_count == len(TARGET)
    surface.apply_surface_plan(plan)
    after = group_maps(obj)
    for name, weights in before.items():
        for index in range(15, 19):
            assert (index in weights, weights.get(index)) == (index in after[name], after[name].get(index))
    assert capture_protected(obj, rig) == protected
    assert_close(after[target][8], .4)


def test_sampled_deform_bone_requires_real_opposite_bone():
    obj, rig, source, _ = make_fixture(missing_mapped_bone=True)
    before = capture_groups(obj)
    expect_error(lambda: plan_for(obj, rig, source), contains="orphan.R")
    assert capture_groups(obj) == before


def test_zero_weight_source_and_destination_fail_without_writes():
    obj, rig, source, _ = make_fixture()
    obj.vertex_groups[source].remove(list(SOURCE))
    before = capture_groups(obj)
    expect_error(lambda: plan_for(obj, rig, source), contains="no positive")
    assert capture_groups(obj) == before
    obj, rig, source, _ = make_fixture()
    for bone in rig.data.bones:
        group = obj.vertex_groups.get(bone.name)
        if group and bone.use_deform:
            group.remove([8])
    before = capture_groups(obj)
    expect_error(lambda: plan_for(obj, rig, source), contains="nonzero")
    assert capture_groups(obj) == before


def test_late_locked_group_refuses_every_planned_change():
    obj, rig, source, _ = make_fixture()
    obj.vertex_groups["spine"].lock_weight = True
    before = capture_groups(obj)
    protected = capture_protected(obj, rig)
    expect_error(lambda: plan_for(obj, rig, source), contains="locked")
    assert capture_groups(obj) == before
    assert capture_protected(obj, rig) == protected


def test_plan_rejects_stale_mesh_without_reverting_new_geometry():
    obj, rig, source, _ = make_fixture()
    plan = plan_for(obj, rig, source)
    obj.data.vertices[8].co.z += .003
    protected = capture_protected(obj, rig)
    expect_error(lambda: surface.apply_surface_plan(plan), contains="Geometry")
    assert capture_groups(obj) == plan.group_snapshot
    assert capture_protected(obj, rig) == protected


def test_plan_rejects_stale_groups_without_reverting_artist_edit():
    obj, rig, source, _ = make_fixture()
    plan = plan_for(obj, rig, source)
    obj.vertex_groups["forearm.R"].add([8], .234, "REPLACE")
    before = capture_groups(obj)
    expect_error(lambda: surface.apply_surface_plan(plan), contains="Weights changed")
    assert capture_groups(obj) == before


def test_post_write_failure_rolls_back_all_groups_and_active_index():
    obj, rig, source, _ = make_fixture()
    plan = plan_for(obj, rig, source)
    protected = capture_protected(obj, rig)
    original = surface._verify_surface_state
    entered = []
    def fail_after_writes(mesh_obj, actual_plan):
        entered.append(True)
        assert capture_groups(mesh_obj) != actual_plan.group_snapshot
        raise RuntimeError("injected post-write validation failure")
    surface._verify_surface_state = fail_after_writes
    try:
        expect_error(lambda: surface.apply_surface_plan(plan), contains="rolled back")
    finally:
        surface._verify_surface_state = original
    assert entered
    assert capture_groups(obj) == plan.group_snapshot
    assert obj.vertex_groups.active_index == plan.active_group_index
    assert capture_protected(obj, rig) == protected


def test_new_mapped_group_creation_rolls_back_if_final_verification_fails():
    obj, rig, source, _ = make_fixture()
    obj.vertex_groups.remove(obj.vertex_groups["upper_arm.R"])
    plan = plan_for(obj, rig, source)
    original = surface._verify_surface_state
    def fail(mesh_obj, _plan):
        assert mesh_obj.vertex_groups.get("upper_arm.R") is not None
        raise RuntimeError("reject created mapped group")
    surface._verify_surface_state = fail
    try:
        expect_error(lambda: surface.apply_surface_plan(plan), contains="rolled back")
    finally:
        surface._verify_surface_state = original
    assert capture_groups(obj) == plan.group_snapshot
    assert obj.vertex_groups.get("upper_arm.R") is None
    # A new plan may then legitimately create the missing target vertex group.
    surface.apply_surface_plan(plan_for(obj, rig, source))
    assert obj.vertex_groups.get("upper_arm.R") is not None


def test_real_armature_skin_uses_the_new_joint_blend():
    obj, rig, source, _ = make_fixture()
    surface.apply_surface_plan(plan_for(obj, rig, source))
    rig.pose.bones["forearm.R"].location.z = -.1
    rig.pose.bones["upper_arm.R"].location.z = .25
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    # At the additional target vertex: forearm 0.4, upper arm 0.32,
    # spine 0.08. Blender's skin normalization uses their unchanged total 0.8.
    # The pre-existing expression Shape Key adds 0.125 * 0.35 independently.
    expected_z = .125 * .35 + (-.1 * .4 + .25 * .32) / .8
    assert_close(evaluated.data.vertices[8].co.z, expected_z)
    assert_close(evaluated.data.vertices[8].co.x, -1.5)
    assert_close(evaluated.data.vertices[8].co.y, .5)


def test_stale_rest_skeleton_refuses_without_reverting_rig_edit():
    obj, rig, source, _ = make_fixture()
    plan = plan_for(obj, rig, source)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    rig.data.edit_bones["upper_arm.R"].tail.z += .01
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = obj
    protected = capture_protected(obj, rig)
    expect_error(lambda: surface.apply_surface_plan(plan), contains="rest skeleton")
    assert capture_groups(obj) == plan.group_snapshot
    assert capture_protected(obj, rig) == protected


def test_repeated_operation_is_exactly_idempotent():
    obj, rig, source, _ = make_fixture()
    surface.apply_surface_plan(plan_for(obj, rig, source))
    after = capture_groups(obj)
    again = plan_for(obj, rig, source)
    assert not again.affected_indices
    surface.apply_surface_plan(again)
    assert capture_groups(obj) == after


def test_invalid_mixed_sources_and_distance_refuse_without_writes():
    obj, rig, source, _ = make_fixture()
    before = capture_groups(obj)
    for names in ((), (source, source), (source, "upper_arm.R")):
        expect_error(lambda: surface.build_surface_plan_for_sources(bpy.context, obj, rig, names))
    for distance in (-1, math.nan, math.inf):
        expect_error(lambda: plan_for(obj, rig, source, max_distance=distance))
    assert capture_groups(obj) == before


def test_saved_result_reopens_unchanged_and_can_be_processed_again():
    obj, rig, source, _ = make_fixture()
    surface.apply_surface_plan(plan_for(obj, rig, source))
    groups, protected = capture_groups(obj), capture_protected(obj, rig)
    obj_name, rig_name = obj.name, rig.name
    with tempfile.TemporaryDirectory(prefix="cd_surface_weights_") as directory:
        path = str(Path(directory) / "surface_result.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=path)
        obj, rig = bpy.data.objects[obj_name], bpy.data.objects[rig_name]
        assert capture_groups(obj) == groups
        assert capture_protected(obj, rig) == protected
        bpy.context.view_layer.objects.active = obj
        plan = plan_for(obj, rig, source)
        assert not plan.affected_indices
        surface.apply_surface_plan(plan)
        assert capture_groups(obj) == groups


def test_operator_undo_and_redo_when_background_undo_is_available():
    obj, rig, source, _ = make_fixture()
    obj_name = obj.name
    before = capture_groups(obj)
    bpy.context.preferences.edit.use_global_undo = True
    if not bpy.ops.ed.undo_push.poll():
        print("SKIP surface operator Undo/Redo: background context has no undo_push")
        return False
    bpy.ops.ed.undo_push(message="Surface test baseline")
    result = bpy.ops.character_designer.surface_weight_mirror()
    assert result == {"FINISHED"}, result
    after = capture_groups(obj)
    assert after != before
    if not bpy.ops.ed.undo.poll():
        print("SKIP surface operator Undo/Redo: background context has no undo")
        return False
    bpy.ops.ed.undo()
    assert capture_groups(bpy.data.objects[obj_name]) == before
    assert bpy.ops.ed.redo.poll(), "Undo succeeded, but Redo is unavailable"
    bpy.ops.ed.redo()
    assert capture_groups(bpy.data.objects[obj_name]) == after


def main():
    for cls in surface.WEIGHT_SURFACE_CLASSES:
        bpy.utils.register_class(cls)
    tests = tuple(value for name, value in sorted(globals().items())
                  if name.startswith("test_") and callable(value))
    passed, skipped = 0, 0
    for test in tests:
        if test() is False:
            skipped += 1
        else:
            passed += 1
            print(f"PASS {test.__name__}")
    print(f"Surface weight mirror: {passed} passed, {skipped} skipped, 0 failed")


if __name__ == "__main__":
    main()
