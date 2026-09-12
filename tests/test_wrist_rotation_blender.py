"""Wrist viewport direction and explicit, reversible legacy conversion."""
import math
import os
import sys

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "addons"), os.path.join(ROOT, "tests")]
from character_designer import limb_ik, root_control
import test_limb_ik_blender as base
import test_limb_ik_fk_blender as fixtures


def update(rig):
    rig.update_tag(refresh={"OBJECT"})
    bpy.context.view_layer.update()


def legacy(rig, key):
    inventory = limb_ik._validate_inventory(rig)
    data = inventory["rigs"][key]
    target_name = data["target"].name
    owner = rig.pose.bones[data["chain"][2]]
    old = data["auto_offset_rotation"]
    name, index, mute, influence = old.name, list(owner.constraints).index(old), old.mute, old.influence
    con = old
    con.target = rig
    con.subtarget = data["target"].name
    con.owner_space = con.target_space = "LOCAL"
    con.space_object = None
    con.space_subtarget = ""
    con.mix_mode = "AFTER"
    con.mute, con.influence = mute, influence
    registry = limb_ik._constraint_registry(owner, strict=True)
    registry[name].pop("rotation_space", None)
    limb_ik._write_constraint_registry(owner, registry)
    if limb_ik._is_direct_preroll_schema(inventory["schema"]):
        manual = next(c for _pb, c, record in data["entries"] if record["role"] == "END_ROTATION")
        manual.target_space, manual.owner_space = "LOCAL_OWNER_ORIENT", "LOCAL_WITH_PARENT"
    helper_name = limb_ik._wrist_helper_name(key[1])
    bpy.ops.object.mode_set(mode="EDIT")
    helper = rig.data.edit_bones.get(helper_name)
    if helper is not None:
        rig.data.edit_bones.remove(helper)
    bpy.ops.object.mode_set(mode="POSE")
    rig.pose.bones[target_name].use_transform_at_custom_shape = False
    update(rig)
    return limb_ik._validate_inventory(rig)["rigs"][key]


def frame(rig):
    return {pb.name: pb.matrix.copy() for pb in rig.pose.bones}


def equal(before, rig, excluded=()):
    update(rig)
    error = max((abs(matrix[i][j] - rig.pose.bones[name].matrix[i][j])
                 for name, matrix in before.items() if name not in excluded
                 for i in range(4) for j in range(4)), default=0)
    assert error < 2e-5, error


def select(rig, target):
    for collection in rig.data.collections_all:
        collection.is_visible = True
        collection.is_solo = False
    for pb in rig.pose.bones:
        pb.select = pb == target
    target.hide = target.bone.hide = target.bone.hide_select = False
    rig.data.bones.active = target.bone


def rotation_vector(before, after):
    q = (after.to_quaternion() @ before.to_quaternion().inverted()).normalized()
    if q.w < 0:
        q.negate()
    axis, angle = q.to_axis_angle()
    return axis * angle


def test_viewport_rotation_and_translation():
    for method in ("DIRECT_PREROLL", "ROLL_DECOUPLED"):
        for selected, manual in ((side, manual) for side in ("LEFT_ARM", "RIGHT_ARM") for manual in (False, True)):
            rig, key, data = fixtures.build(method, selected)
            if manual:
                assert bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") == {"FINISHED"}
                data = limb_ik._validate_inventory(rig)["rigs"][key]
            assert data["auto_rotation_space"] == "PARENT_DELTA"
            target = rig.pose.bones[data["target"].name]
            hand = rig.pose.bones[data["chain"][2]]
            assert target.use_transform_at_custom_shape
            assert not target.use_transform_around_custom_shape
            rig.location = (.6, -.3, .2)
            rig.rotation_euler = (.29, -.41, .13)
            rig.scale = (.8, .8, .8)
            if target.parent:
                target.parent.rotation_mode = "XYZ"
                target.parent.rotation_euler = (.26, .18, -.35)
            target.location += Vector((-.31, -.28, .21))
            target.rotation_euler = (.31, -.27, .42)
            target.custom_shape_scale_xyz = (-.21, .13, .34)
            target.custom_shape_rotation_euler = (.4, -.6, .2)
            target.custom_shape_translation = (.14, -.03, .08)
            select(rig, target)
            update(rig)
            basis = target.matrix_basis.copy()
            window = bpy.context.window
            area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
            region = next(r for r in area.regions if r.type == "WINDOW")
            for view in (area.spaces.active.region_3d.view_matrix.inverted().to_quaternion(),):
                for orientation, axis in (("LOCAL", "Y"), ("VIEW", "Z"), ("GLOBAL", "X"), ("GLOBAL", "Y"), ("GLOBAL", "Z")):
                    for sign in (-1, 1):
                        before = rig.matrix_world @ hand.matrix
                        if orientation == "LOCAL":
                            direction = before.to_3x3().col[1].normalized()
                        elif orientation == "VIEW":
                            direction = view @ Vector((0, 0, 1))
                        else:
                            direction = Vector(tuple(float(a == axis) for a in "XYZ"))
                        location_before = target.location.copy()
                        angle = sign * .19
                        with bpy.context.temp_override(window=window, area=area, region=region):
                            assert bpy.ops.transform.rotate(value=angle, orient_axis=axis, orient_type=orientation,
                                constraint_axis=tuple(a == axis for a in "XYZ")) == {"FINISHED"}
                        update(rig)
                        after = rig.matrix_world @ hand.matrix
                        assert (rotation_vector(before, after) - direction * angle).length < 8e-5, (method, selected, manual, orientation, axis, tuple(rotation_vector(before, after)), tuple(direction * angle))
                        assert (after.translation - before.translation).length < 2e-5
                        assert (target.location - location_before).length < 2e-5
                        target.matrix_basis = basis
                        update(rig)
            for axis in range(3):
                before = rig.matrix_world @ hand.matrix
                move = Vector(tuple(.015 if i == axis else 0 for i in range(3)))
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.transform.translate(value=move, orient_type="GLOBAL")
                update(rig)
                assert ((rig.matrix_world @ hand.matrix).translation - before.translation - move).length < 1e-4
                target.matrix_basis = basis
                update(rig)


def test_upgrade_posed_legacy_idempotence_and_remove():
    for method in ("DIRECT_PREROLL", "ROLL_DECOUPLED"):
        for selected in ("LEFT_ARM", "RIGHT_ARM"):
            rig, key, data = fixtures.build(method, selected)
            data = legacy(rig, key)
            target = rig.pose.bones[data["target"].name]
            target.location += Vector((-.21, -.23, .11))
            target.rotation_euler = (.41, -.29, .2)
            update(rig)
            before = frame(rig)
            shape = target.custom_shape
            rest = {b.name: b.matrix_local.copy() for b in rig.data.bones}
            target_name = target.name
            result = limb_ik.upgrade_wrist_rotation(bpy.context, rig, dry_run=True)
            assert not result["blockers"] and not result["changed"]
            assert limb_ik.upgrade_wrist_rotation(bpy.context, rig)["changed"]
            target = rig.pose.bones[target_name]
            assert target.use_transform_at_custom_shape
            assert not target.use_transform_around_custom_shape
            equal(before, rig, (target.name,))
            assert target.custom_shape is shape
            assert all(rig.data.bones[name].matrix_local == value for name, value in rest.items())
            assert not limb_ik.upgrade_wrist_rotation(bpy.context, rig)["changed"]
            assert limb_ik.sync_wrist_local_axes(rig) == []
            assert bpy.ops.character_designer.limb_ik_remove() == {"FINISHED"}


def test_manual_rotation_and_legacy_manual_upgrade():
    for method in ("DIRECT_PREROLL", "ROLL_DECOUPLED"):
        for selected in ("LEFT_ARM", "RIGHT_ARM"):
            rig, key, data = fixtures.build(method, selected)
            if method == "DIRECT_PREROLL":
                root_control.build(bpy.context, rig)
                data = limb_ik._validate_inventory(rig)["rigs"][key]
            parent = rig.pose.bones[data["target"].name].parent
            parent.rotation_mode = "XYZ"
            parent.rotation_euler = (.31, -.23, .42)
            rig.rotation_euler = (.14, -.09, .18)
            rig.scale = (.83, .83, .83)
            data = legacy(rig, key)
            assert bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") == {"FINISHED"}
            data = limb_ik._validate_inventory(rig)["rigs"][key]
            target = rig.pose.bones[data["target"].name]
            target.rotation_euler.x += .16
            update(rig)
            before = frame(rig)
            display = (target.custom_shape_transform or target).matrix @ limb_ik._custom_shape_state_matrix(limb_ik._control_visual_state(target))
            target_name = target.name
            assert limb_ik.upgrade_wrist_rotation(bpy.context, rig)["changed"]
            target = rig.pose.bones[target_name]
            assert target.use_transform_at_custom_shape
            assert not target.use_transform_around_custom_shape
            equal(before, rig, (target.name,))
            actual_display = (target.custom_shape_transform or target).matrix @ limb_ik._custom_shape_state_matrix(limb_ik._control_visual_state(target))
            assert max(abs(display[i][j] - actual_display[i][j]) for i in range(4) for j in range(4)) < 2e-5
            select(rig, target)
            hand = rig.pose.bones[data["chain"][2]]
            basis = target.matrix_basis.copy()
            w = bpy.context.window
            a = next(a for a in w.screen.areas if a.type == "VIEW_3D")
            region = next(r for r in a.regions if r.type == "WINDOW")
            for axis in "XYZ":
                for sign in (-1, 1):
                    before = rig.matrix_world @ hand.matrix
                    with bpy.context.temp_override(window=w, area=a, region=region):
                        bpy.ops.transform.rotate(value=sign * .19, orient_axis=axis, orient_type="GLOBAL",
                                                 constraint_axis=tuple(x == axis for x in "XYZ"))
                    update(rig)
                    expected = Vector(tuple(sign * .19 if x == axis else 0 for x in "XYZ"))
                    assert (rotation_vector(before, rig.matrix_world @ hand.matrix) - expected).length < 8e-5
                    target.matrix_basis = basis
                    update(rig)


def test_upgrade_keeps_zero_inputs_and_driver_paths():
    rig, key, data = fixtures.build("DIRECT_PREROLL", "LEFT_ARM")
    data = legacy(rig, key)
    target = rig.pose.bones[data["target"].name]
    before = frame(rig)
    channels = tuple(target.rotation_euler)
    drivers = tuple((c.data_path, c.driver.expression) for c in rig.animation_data.drivers)
    limb_ik.upgrade_wrist_rotation(bpy.context, rig)
    equal(before, rig)
    assert tuple(target.rotation_euler) == channels
    assert tuple((c.data_path, c.driver.expression) for c in rig.animation_data.drivers) == drivers
    assert all(c.is_valid for c in rig.animation_data.drivers)


def test_upgrade_authored_animation_and_driver_guards():
    rig, key, data = fixtures.build("ROLL_DECOUPLED", "LEFT_ARM")
    data = legacy(rig, key)
    target = rig.pose.bones[data["target"].name]
    target.keyframe_insert("rotation_euler", frame=1)
    before = frame(rig)
    assert limb_ik.upgrade_wrist_rotation(bpy.context, rig, dry_run=True)["blockers"]
    try:
        limb_ik.upgrade_wrist_rotation(bpy.context, rig)
    except limb_ik.LimbIKError:
        pass
    else:
        raise AssertionError("Authored rotation animation was accepted")
    equal(before, rig)
    rig.animation_data.action = None
    driven = target.driver_add("rotation_euler", 0)
    driven.driver.expression = "0.2"
    assert limb_ik.upgrade_wrist_rotation(bpy.context, rig, dry_run=True)["blockers"]


def test_upgrade_failure_transaction():
    rig, key, data = fixtures.build("ROLL_DECOUPLED", "LEFT_ARM")
    settings = bpy.context.window_manager.character_designer_limb_ik
    settings.selected_limb = "RIGHT_ARM"
    assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
    data = legacy(rig, key)
    legacy(rig, ("ARM", "R"))
    right_target = rig.pose.bones[limb_ik._validate_inventory(rig)["rigs"][("ARM", "R")]["target"].name]
    right_target.use_transform_at_custom_shape = True
    flags_before = {pb.name: pb.use_transform_at_custom_shape for pb in rig.pose.bones}
    target = rig.pose.bones[data["target"].name]
    target.rotation_euler = (.14, -.25, .31)
    update(rig)
    before = frame(rig)
    old_registry = rig.pose.bones[data["chain"][2]][limb_ik.CONSTRAINT_REGISTRY_KEY]
    original = limb_ik._configure_parent_delta_rotation
    calls = [0]
    def fail(*args):
        original(*args)
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("Injected migration failure")
    limb_ik._configure_parent_delta_rotation = fail
    try:
        try:
            limb_ik.upgrade_wrist_rotation(bpy.context, rig)
        except RuntimeError as exc:
            assert "Injected" in str(exc)
        else:
            raise AssertionError("Failure injection was not reached")
    finally:
        limb_ik._configure_parent_delta_rotation = original
    equal(before, rig)
    assert rig.pose.bones[data["chain"][2]][limb_ik.CONSTRAINT_REGISTRY_KEY] == old_registry
    assert limb_ik._validate_inventory(rig)["rigs"][key]["auto_rotation_space"] == "LOCAL"
    assert all(rig.data.bones.get(limb_ik._wrist_helper_name(side)) is None for side in ("L", "R"))
    assert {pb.name: pb.use_transform_at_custom_shape for pb in rig.pose.bones} == flags_before


def test_sync_local_axes_is_pose_safe_and_preserves_artist_settings():
    for method in ("DIRECT_PREROLL", "ROLL_DECOUPLED"):
        rig, key, _data = fixtures.build(method, "LEFT_ARM")
        settings = bpy.context.window_manager.character_designer_limb_ik
        for selected in ("RIGHT_ARM", "LEFT_LEG"):
            settings.selected_limb = selected
            assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
        inventory = limb_ik._validate_inventory(rig)
        targets = [rig.pose.bones[data["target"].name] for side, data in inventory["rigs"].items() if side[0] == "ARM"]
        leg = rig.pose.bones[inventory["rigs"][("LEG", "L")]["target"].name]
        assert not leg.use_transform_at_custom_shape
        for target in targets:
            target.use_transform_at_custom_shape = False
            target.location.y -= .18
            target.rotation_euler = (.21, -.19, .13)
            target.keyframe_insert("rotation_euler", frame=1)
        update(rig)
        before = frame(rig)
        bases = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
        rest = {bone.name: bone.matrix_local.copy() for bone in rig.data.bones}
        action = rig.animation_data.action
        curves = [(curve.data_path, curve.array_index, tuple(tuple(point.co) for point in curve.keyframe_points))
                  for curve in limb_ik._fcurves_for_action(action)]
        constraints = [(pb.name, con.as_pointer(), con.mute, con.influence)
                       for pb in rig.pose.bones for con in pb.constraints]
        displays = {pb.name: (pb.custom_shape, pb.custom_shape_transform, limb_ik._control_visual_state(pb))
                    for pb in rig.pose.bones}
        targets[-1].use_transform_around_custom_shape = True
        try:
            limb_ik.sync_wrist_local_axes(rig)
        except limb_ik.LimbIKError as exc:
            assert targets[-1].name in str(exc) and "Transform Around Custom Shape" in str(exc)
        else:
            raise AssertionError("An artist transform-around setting was silently replaced")
        assert all(not target.use_transform_at_custom_shape for target in targets)
        assert targets[-1].use_transform_around_custom_shape
        targets[-1].use_transform_around_custom_shape = False
        assert set(limb_ik.sync_wrist_local_axes(rig)) == {target.name for target in targets}
        assert limb_ik.sync_wrist_local_axes(rig) == []
        equal(before, rig)
        assert all(pb.matrix_basis == bases[pb.name] for pb in rig.pose.bones)
        assert all(bone.matrix_local == rest[bone.name] for bone in rig.data.bones)
        assert rig.animation_data.action is action
        assert curves == [(curve.data_path, curve.array_index, tuple(tuple(point.co) for point in curve.keyframe_points))
                          for curve in limb_ik._fcurves_for_action(action)]
        assert constraints == [(pb.name, con.as_pointer(), con.mute, con.influence)
                               for pb in rig.pose.bones for con in pb.constraints]
        assert displays == {pb.name: (pb.custom_shape, pb.custom_shape_transform, limb_ik._control_visual_state(pb))
                            for pb in rig.pose.bones}
        assert not leg.use_transform_at_custom_shape
        assert not rig.pose.bones["Hips"].use_transform_at_custom_shape


def test_local_axes_survive_mode_switches_and_rebuild():
    for method in ("DIRECT_PREROLL", "ROLL_DECOUPLED"):
        for selected in ("LEFT_ARM", "RIGHT_ARM"):
            rig, key, data = fixtures.build(method, selected)
            name = data["target"].name
            for action in ("DISABLE", "ENABLE", "DISABLE", "ENABLE"):
                assert bpy.ops.character_designer.limb_ik_auto_align_target(action=action) == {"FINISHED"}
                assert rig.pose.bones[name].use_transform_at_custom_shape
                assert not rig.pose.bones[name].use_transform_around_custom_shape
            fixtures.limb_ik_fk.switch_limb(bpy.context, rig, key, "FK")
            fixtures.limb_ik_fk.switch_limb(bpy.context, rig, key, "IK")
            assert rig.pose.bones[name].use_transform_at_custom_shape
            assert bpy.ops.character_designer.limb_ik_rebuild() == {"FINISHED"}
            assert rig.pose.bones[name].use_transform_at_custom_shape
            assert not rig.pose.bones[name].use_transform_around_custom_shape
            assert limb_ik.sync_wrist_local_axes(rig) == []


def test_legacy_sync_is_untouched_and_upgrade_respects_artist_setting():
    rig, key, _data = fixtures.build("ROLL_DECOUPLED", "LEFT_ARM")
    data = legacy(rig, key)
    target = rig.pose.bones[data["target"].name]
    assert limb_ik.sync_wrist_local_axes(rig) == []
    assert not target.use_transform_at_custom_shape
    target.use_transform_around_custom_shape = True
    blockers = limb_ik.upgrade_wrist_rotation(bpy.context, rig, dry_run=True)["blockers"]
    assert any("Transform Around Custom Shape" in reason for reason in blockers)
    assert target.use_transform_around_custom_shape
    assert not target.use_transform_at_custom_shape


def test_failed_rebuild_restores_custom_shape_transform_flags():
    for method in ("DIRECT_PREROLL", "ROLL_DECOUPLED"):
        rig, key, data = fixtures.build(method, "LEFT_ARM")
        name = data["target"].name
        target = rig.pose.bones[name]
        target.use_transform_at_custom_shape = False
        target.use_transform_around_custom_shape = True
        before = frame(rig)
        original = limb_ik._reapply_control_visual_overrides
        def fail(*args):
            original(*args)
            raise RuntimeError("Injected rebuild display failure")
        limb_ik._reapply_control_visual_overrides = fail
        try:
            assert base.cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) == {"CANCELLED"}
        finally:
            limb_ik._reapply_control_visual_overrides = original
        equal(before, rig)
        target = rig.pose.bones[name]
        assert not target.use_transform_at_custom_shape
        assert target.use_transform_around_custom_shape
        limb_ik._validate_inventory(rig)


if __name__ == "__main__":
    base.ensure_registered()
    tests = [value for name, value in globals().copy().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("PASS", test.__name__, flush=True)
    print("WRIST_ROTATION_TESTS_PASS", len(tests), flush=True)
