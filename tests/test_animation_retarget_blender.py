"""Run with Blender --background --factory-startup --python this_file.py.

Optionally append -- --real-blend PATH to run the same transfer against a
saved project opened read-only in this disposable Blender process.
"""

import math
import os
import sys
import tempfile

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addons"))
from character_designer import animation_retarget as retarget


def assert_close(a, b, tolerance=1.0e-5):
    error = (Vector(a) - Vector(b)).length
    assert error < tolerance, (tuple(a), tuple(b), error)


def assert_rotation(a, b):
    assert abs(a.normalized().dot(b.normalized())) > 1 - 1.0e-5, (tuple(a), tuple(b))


def reset():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    bpy.context.scene.frame_set(1)


def make_target(name="Target"):
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    definitions = [
        ("Hips", None, (0, 0, 1.0), (0, 0, 1.15)),
        ("spine", "Hips", (0, 0, 1.15), (0, 0, 1.3)),
        ("Chest", "spine", (0, 0, 1.3), (0, 0, 1.5)),
        ("UpperChest", "Chest", (0, 0, 1.5), (0, 0, 1.6)),
        ("Neck", "UpperChest", (0, 0, 1.6), (0, 0, 1.7)),
        ("Head", "Neck", (0, 0, 1.7), (0, 0, 1.9)),
    ]
    for side, sign in (("L", 1), ("R", -1)):
        definitions.extend([
            (f"shoulder.{side}", "UpperChest", (0, 0, 1.55), (.2 * sign, 0, 1.55)),
            (f"upper_arm.{side}", f"shoulder.{side}", (.2 * sign, 0, 1.55), (.5 * sign, .05, 1.4)),
            (f"forearm.{side}", f"upper_arm.{side}", (.5 * sign, .05, 1.4), (.8 * sign, 0, 1.25)),
            (f"hand.{side}", f"forearm.{side}", (.8 * sign, 0, 1.25), (.9 * sign, 0, 1.2)),
            (f"thigh.{side}", "Hips", (.1 * sign, 0, 1.0), (.1 * sign, -.05, .55)),
            (f"shin.{side}", f"thigh.{side}", (.1 * sign, -.05, .55), (.1 * sign, 0, .1)),
            (f"foot.{side}", f"shin.{side}", (.1 * sign, 0, .1), (.1 * sign, -.15, .05)),
            (f"toe.{side}", f"foot.{side}", (.1 * sign, -.15, .05), (.1 * sign, -.22, .05)),
        ])
    for name, parent, head, tail in definitions:
        b = data.edit_bones.new(name)
        b.head, b.tail = head, tail
        b.parent = data.edit_bones.get(parent) if parent else None
        b.use_deform = True
    for name, parent, offset in (("Hair Test", "Head", 1.8), ("Skirt Test", "Hips", .9), ("f_index.01.L", "hand.L", 1.2)):
        b = data.edit_bones.new(name)
        b.head, b.tail = (0, 0, offset), (0, 0, offset + .1)
        b.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.pose.bones["shoulder.L"].rotation_mode = "YXZ"
    return obj


def make_source(target, *, centimeters=False, roll=0.0):
    obj = bpy.data.objects.new("SOMA Source", target.data.copy())
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    reverse = {t: s for s, t in retarget.SOMA_BODY_MAP.items()}
    bones = obj.data.edit_bones
    for bone in list(bones):
        if bone.name not in reverse:
            bones.remove(bone)
    for bone in bones:
        bone.use_connect = False
    for bone in bones:
        bone.name = "TEMP_" + bone.name
    for bone in bones:
        old = bone.name[5:]
        bone.name = reverse[old]
        bone.roll += roll
        if centimeters:
            bone.head *= 100
            bone.tail *= 100
    wrapper = bones.new("Root")
    wrapper.head, wrapper.tail = (0, 0, 0), (0, 0, .1)
    bones["Hips"].parent = wrapper
    neck = bones.new("Neck1")
    neck.head = bones["Neck2"].head - Vector((0, 0, .04 * (100 if centimeters else 1)))
    neck.tail = bones["Neck2"].head
    neck.parent = bones["Chest"]
    bones["Neck2"].parent = neck
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.scale = (.01, .01, .01) if centimeters else (1, 1, 1)
    bpy.context.view_layer.update()
    return obj


def source_action(source, *, movement=(0, 0, 0), arm_angle=0.0, neck_angle=0.0):
    root = source.pose.bones["Hips"]
    arm = source.pose.bones["LeftArm"]
    neck = source.pose.bones["Neck1"]
    for frame, factor in ((1, 0), (3, 1)):
        root.location = source.data.bones["Hips"].matrix_local.to_quaternion().inverted() @ Vector(movement) * factor
        root.keyframe_insert("location", frame=frame)
        for bone, angle in ((arm, arm_angle), (neck, neck_angle)):
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = Quaternion((1, 0, 0), angle * factor)
            bone.keyframe_insert("rotation_quaternion", frame=frame)
    for curve in retarget._curves(source.animation_data.action):
        for point in curve.keyframe_points:
            point.interpolation = "LINEAR"
    bpy.context.scene.frame_set(1)


def pose_matrices(target):
    bpy.context.view_layer.update()
    evaluated = target.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {b.name: b.matrix.copy() for b in evaluated.pose.bones}


def test_preview_rest_and_no_accessory_keys():
    reset()
    target = make_target()
    source = make_source(target, roll=.63)
    source_action(source)
    bpy.context.scene.frame_set(8, subframe=.25)
    before = pose_matrices(target)
    actions_before = set(bpy.data.actions)
    result = retarget.retarget_action(bpy.context, source, target, start_frame=10)
    assert target.animation_data is None
    assert bpy.context.scene.frame_current_final == 8.25
    assert result.frame_start == 10 and result.frame_end == 12
    assert set(bpy.data.actions) == actions_before | {result.action}
    assert result.action.use_fake_user
    assert len(result.mapping) == 22
    curves = retarget._curves(result.action)
    assert all("Hair" not in c.data_path and "Skirt" not in c.data_path and "f_index" not in c.data_path for c in curves)
    assert any('shoulder.L' in c.data_path and 'rotation_euler' in c.data_path for c in curves)
    result.apply(bpy.context)
    bpy.context.scene.frame_set(10)
    after = pose_matrices(target)
    for name in result.mapping.values():
        assert_rotation(after[name].to_quaternion(), before[name].to_quaternion())
        assert_close(after[name].translation, before[name].translation)
    result.restore(bpy.context)
    assert target.animation_data.action is None


def test_root_world_scale_anchor_and_rotation_delta():
    reset()
    target = make_target()
    target.scale = (.7823157,) * 3
    target.location = (2, 3, .17562)
    target.rotation_euler.z = .27
    target.pose.bones["Hips"].location = (.15, 0, .1)
    source = make_source(target, centimeters=True, roll=.7)
    source_action(source, movement=(100, 0, 0), arm_angle=.6, neck_angle=.2)
    original = pose_matrices(target)
    initial_root = target.matrix_world @ original["Hips"].translation
    result = retarget.retarget_action(bpy.context, source, target, assign=True)
    assert abs(result.motion_scale - .7823157) < 1.0e-5
    bpy.context.scene.frame_set(3)
    actual = pose_matrices(target)
    assert_close(target.matrix_world @ actual["Hips"].translation, initial_root + Vector((.7823157, 0, 0)))
    evaluated_source = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for source_name, source_child, target_name, target_child in (
        ("LeftArm", "LeftForeArm", "upper_arm.L", "forearm.L"),
        ("Neck2", "Head", "Neck", "Head"),
    ):
        source_direction = source.matrix_world.to_3x3() @ (evaluated_source.pose.bones[source_child].matrix.translation - evaluated_source.pose.bones[source_name].matrix.translation)
        target_direction = target.matrix_world.to_3x3() @ (actual[target_child].translation - actual[target_name].translation)
        assert_close(target_direction.normalized(), source_direction.normalized())
    for source_name, target_name in (("Head", "Head"),):
        delta = (source.matrix_world.to_quaternion() @ evaluated_source.pose.bones[source_name].matrix.to_quaternion()) @ (source.matrix_world.to_quaternion() @ source.data.bones[source_name].matrix_local.to_quaternion()).inverted()
        expected_world = delta @ target.matrix_world.to_quaternion() @ target.data.bones[target_name].matrix_local.to_quaternion()
        assert_rotation(target.matrix_world.to_quaternion() @ actual[target_name].to_quaternion(), expected_world)
    result.restore(bpy.context)
    restored = pose_matrices(target)
    for name in original:
        assert_close(restored[name].translation, original[name].translation)
        assert_rotation(restored[name].to_quaternion(), original[name].to_quaternion())
    assert result.action.name in bpy.data.actions


def test_a_pose_target_t_pose_source_keeps_idle_arms_outside_body():
    """Regression: rest-delta alone lowered A-pose arms twice and crossed them."""
    reset()
    target = make_target()
    source = make_source(target, roll=.37)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.mode_set(mode="EDIT")
    for prefix, sign in (("Left", 1), ("Right", -1)):
        arm = source.data.edit_bones[prefix + "Arm"]
        forearm = source.data.edit_bones[prefix + "ForeArm"]
        hand = source.data.edit_bones[prefix + "Hand"]
        start = arm.head.copy()
        elbow = start + Vector((sign * arm.length, 0, 0))
        wrist = elbow + Vector((sign * forearm.length, 0, 0))
        arm.tail = elbow
        forearm.head, forearm.tail = elbow, wrist
        hand.head, hand.tail = wrist, wrist + Vector((sign * .12, 0, 0))
    bpy.ops.object.mode_set(mode="OBJECT")
    source_action(source)
    for prefix, sign in (("Left", 1), ("Right", -1)):
        bone = source.pose.bones[prefix + "Arm"]
        rest_q = bone.bone.matrix_local.to_quaternion()
        for frame in (1, 3):
            bone.rotation_quaternion = rest_q.inverted() @ Quaternion((0, 1, 0), math.radians(70) * sign) @ rest_q
            bone.keyframe_insert("rotation_quaternion", frame=frame)
    bpy.context.scene.frame_set(1)
    result = retarget.retarget_action(bpy.context, source, target, assign=True)
    evaluated_source = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
    actual = pose_matrices(target)
    assert result.calibration_angles["upper_arm.L"] > 20
    for prefix, side, sign in (("Left", "L", 1), ("Right", "R", -1)):
        for src_start, src_end, tgt_start, tgt_end in (
            ("Arm", "ForeArm", "upper_arm", "forearm"),
            ("ForeArm", "Hand", "forearm", "hand"),
        ):
            src_direction = evaluated_source.pose.bones[prefix + src_end].matrix.translation - evaluated_source.pose.bones[prefix + src_start].matrix.translation
            tgt_direction = actual[tgt_end + "." + side].translation - actual[tgt_start + "." + side].translation
            assert_close(tgt_direction.normalized(), src_direction.normalized())
        assert actual["forearm." + side].translation.x * sign > actual["upper_arm." + side].translation.x * sign
        assert actual["hand." + side].translation.x * sign > actual["upper_arm." + side].translation.x * sign
    result.restore(bpy.context)
    assert target.animation_data.action is None


def test_previous_action_slot_and_persistent_restore():
    reset()
    target = make_target()
    target.pose.bones["Hips"].location = (.2, .1, 0)
    target.pose.bones["Hips"].keyframe_insert("location", frame=1)
    original = target.animation_data.action
    original_slot = target.animation_data.action_slot.handle
    original_curves = [(c.data_path, [tuple(p.co) for p in c.keyframe_points]) for c in retarget._curves(original)]
    source = make_source(target)
    source_action(source, movement=(1, 0, 0))
    result = retarget.retarget_action(bpy.context, source, target, assign=True)
    assert result.previous_action is original
    assert result.action[retarget.PREVIOUS_ACTION_KEY] is original
    assert original.users >= 1, "Old Action must retain a real ID reference when unbound"
    assert not original.use_fake_user, "Previous Action persistence does not require modifying its flags"
    result.restore(bpy.context)
    assert target.animation_data.action is original
    assert target.animation_data.action_slot.handle == original_slot
    assert original_curves == [(c.data_path, [tuple(p.co) for p in c.keyframe_points]) for c in retarget._curves(original)]
    second = retarget.retarget_action(bpy.context, source, target, assign=True)
    assert result.action.name != second.action.name
    # Persistence check uses a temp blend only; no real project is saved.
    with tempfile.TemporaryDirectory(prefix="codex-animation-retarget-") as directory:
        path = os.path.join(directory, "actions.blend")
        expected_names = (target.name, original.name, second.action.name)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
        target = bpy.data.objects[expected_names[0]]
        generated = bpy.data.actions[expected_names[2]]
        restored = retarget.restore_previous_action(bpy.context, target, generated)
        assert restored.name == expected_names[1]
        assert target.animation_data.action is restored
        assert generated.name in bpy.data.actions


def test_same_frame_apply_and_restore_updates_evaluated_pose():
    reset()
    target = make_target()
    source = make_source(target)
    source_action(source, movement=(1, 0, 0))
    bpy.context.scene.frame_set(3)
    before = pose_matrices(target)["Hips"].translation
    result = retarget.retarget_action(bpy.context, source, target, assign=True)
    # No frame change after attach/restore: these must update the viewport now.
    assert_close(pose_matrices(target)["Hips"].translation, before + Vector((1, 0, 0)))
    result.restore(bpy.context)
    assert_close(pose_matrices(target)["Hips"].translation, before)


def assert_rejected(source, target, expected):
    actions = set(bpy.data.actions)
    action = target.animation_data.action if target.animation_data else None
    try:
        retarget.retarget_action(bpy.context, source, target, assign=True)
    except retarget.RetargetError as exc:
        assert expected.casefold() in str(exc).casefold(), str(exc)
    else:
        raise AssertionError("Unsafe body transfer was accepted")
    assert set(bpy.data.actions) == actions
    assert (target.animation_data.action if target.animation_data else None) is action


def test_constraints_drivers_nla_and_scale_guards():
    reset()
    target = make_target()
    source = make_source(target)
    source_action(source)
    constraint = target.pose.bones["forearm.L"].constraints.new("IK")
    constraint.mute = True
    assert_rejected(source, target, "constraints")
    assert constraint.mute
    target.pose.bones["forearm.L"].constraints.remove(constraint)
    driver = target.pose.bones["Hips"].driver_add("location", 0)
    driver.driver.expression = "0.0"
    assert_rejected(source, target, "drivers")
    target.pose.bones["Hips"].driver_remove("location", 0)
    target.scale = (1, 2, 1)
    bpy.context.view_layer.update()
    assert_rejected(source, target, "uniform")
    target.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    target.animation_data_create()
    track = target.animation_data.nla_tracks.new()
    track.strips.new("Existing", 1, source.animation_data.action)
    assert_rejected(source, target, "NLA")


def test_nonmapped_constraints_untouched_and_apply_race_guard():
    reset()
    target = make_target()
    source = make_source(target)
    source_action(source)
    constraint = target.pose.bones["Hair Test"].constraints.new("LIMIT_ROTATION")
    result = retarget.retarget_action(bpy.context, source, target)
    assert target.pose.bones["Hair Test"].constraints[0].as_pointer() == constraint.as_pointer()
    target.animation_data_create().action = bpy.data.actions.new("User's new action")
    try:
        result.apply(bpy.context)
    except retarget.RetargetError as exc:
        assert "changed after preview" in str(exc)
    else:
        raise AssertionError("Apply overwrote an Action selected after preview")


def test_failure_restores_frame_and_no_partial_action():
    reset()
    target = make_target()
    source = make_source(target)
    source_action(source)
    bpy.context.scene.frame_set(12, subframe=.5)
    old = retarget._write_curve
    actions = set(bpy.data.actions)
    def fail(*_args):
        raise RuntimeError("Injected channel write failure")
    retarget._write_curve = fail
    try:
        try:
            retarget.retarget_action(bpy.context, source, target)
        except RuntimeError as exc:
            assert "Injected" in str(exc)
        else:
            raise AssertionError("Fault injection did not fire")
    finally:
        retarget._write_curve = old
    assert bpy.context.scene.frame_current_final == 12.5
    assert set(bpy.data.actions) == actions
    assert target.animation_data is None


def test_actual_soma_bvh_import_axes_and_root_displacement():
    """Exercise Blender's real importer with an original synthetic BVH clip."""
    from bpy_extras.io_utils import axis_conversion
    from io_anim_bvh import import_bvh
    reset()
    target = make_target()
    source = make_source(target)
    target.scale = (.7823157,) * 3
    bpy.context.view_layer.update()
    lines, order = ["HIERARCHY"], []
    def converted(vector):
        return Vector((vector.x, vector.z, -vector.y)) * 100
    def emit(bone, depth=0):
        order.append(bone.name)
        offset = bone.head_local - bone.parent.head_local if bone.parent else bone.head_local
        offset = converted(offset)
        tab = "  " * depth
        lines.extend([f"{tab}{'JOINT' if bone.parent else 'ROOT'} {bone.name}", tab + "{",
                      tab + "  OFFSET " + " ".join(str(v) for v in offset)])
        lines.append(tab + ("  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation"
                            if bone.name in {"Root", "Hips"} else "  CHANNELS 3 Zrotation Yrotation Xrotation"))
        for child in bone.children:
            emit(child, depth + 1)
        if not bone.children:
            tail = converted(bone.tail_local - bone.head_local)
            lines.extend([tab + "  End Site", tab + "  {", tab + "    OFFSET " + " ".join(str(v) for v in tail), tab + "  }"])
        lines.append(tab + "}")
    emit(source.data.bones["Root"])
    lines.extend(["MOTION", "Frames: 3", "Frame Time: 0.03333333333333333"])
    for factor in (0, .5, 1):
        channels = []
        for name in order:
            if name in {"Root", "Hips"}:
                channels.extend([100 * factor if name == "Hips" else 0, 0, 0])
            channels.extend([0, 0, 0])
        lines.append(" ".join(str(v) for v in channels))
    bpy.data.objects.remove(source, do_unlink=True)
    bpy.context.scene.render.fps = 30
    bpy.context.scene.render.fps_base = 1.0
    with tempfile.TemporaryDirectory(prefix="codex-soma-bvh-") as directory:
        path = os.path.join(directory, "soma_fixture.bvh")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        outcome = import_bvh.load(bpy.context, filepath=path, global_scale=.01,
                                  global_matrix=axis_conversion(from_forward="-Z", from_up="Y").to_4x4(),
                                  use_fps_scale=True)
        assert outcome == {"FINISHED"}
    source = bpy.context.view_layer.objects.active
    initial = target.matrix_world @ pose_matrices(target)["Hips"].translation
    result = retarget.retarget_action(bpy.context, source, target, assign=True)
    bpy.context.scene.frame_set(3)
    actual = target.matrix_world @ pose_matrices(target)["Hips"].translation
    assert_close(actual, initial + Vector((.7823157, 0, 0)))
    result.restore(bpy.context)


def test_saved_x_read_only(path):
    size_before = os.stat(path).st_size
    mtime_before = os.stat(path).st_mtime_ns
    bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
    target = bpy.data.objects["CoshaRig"]
    names_before = tuple(target.data.bones.keys())
    pose_before = pose_matrices(target)
    hair_names = [n for n in names_before if n.startswith("Hair ")]
    assert len(hair_names) == 52
    source = make_source(target, centimeters=True, roll=.31)
    source_action(source, movement=(80, 0, 0), arm_angle=.15)
    result = retarget.retarget_action(bpy.context, source, target, assign=True)
    bpy.context.scene.frame_set(3)
    assert all(all(n not in c.data_path for n in hair_names) for c in retarget._curves(result.action))
    result.restore(bpy.context)
    after = pose_matrices(target)
    assert tuple(target.data.bones.keys()) == names_before
    for name in names_before:
        assert_close(after[name].translation, pose_before[name].translation, 3.0e-5)
        assert_rotation(after[name].to_quaternion(), pose_before[name].to_quaternion())
    assert os.stat(path).st_size == size_before
    assert os.stat(path).st_mtime_ns == mtime_before
    print(f"PASS saved X: 22 body joints, {len(hair_names)} hair bones untouched, scale {result.motion_scale:.7f}, original file unchanged")


def main():
    tests = (
        test_preview_rest_and_no_accessory_keys,
        test_root_world_scale_anchor_and_rotation_delta,
        test_a_pose_target_t_pose_source_keeps_idle_arms_outside_body,
        test_previous_action_slot_and_persistent_restore,
        test_same_frame_apply_and_restore_updates_evaluated_pose,
        test_constraints_drivers_nla_and_scale_guards,
        test_nonmapped_constraints_untouched_and_apply_race_guard,
        test_failure_restores_frame_and_no_partial_action,
        test_actual_soma_bvh_import_axes_and_root_displacement,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    if "--real-blend" in sys.argv:
        test_saved_x_read_only(sys.argv[sys.argv.index("--real-blend") + 1])
    print(f"PASS Animation Retarget {len(tests)} tests")


if __name__ == "__main__":
    main()
