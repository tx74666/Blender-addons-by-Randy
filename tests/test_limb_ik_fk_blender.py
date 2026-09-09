"""Native Blender integration for limb IK/FK matching and animation."""
import os
import sys
import math
import tempfile

import bpy
from mathutils import Matrix, Euler, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addons"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from character_designer import limb_ik, limb_ik_fk
import test_limb_ik_blender as base


def build(method="ROLL_DECOUPLED", selected="LEFT_LEG"):
    base.reset_scene()
    armature = base.make_humanoid(roll_offset=0.37)
    result, settings = base.analyze(armature)
    assert result == {"FINISHED"}, settings.last_message
    settings.build_method = method
    settings.selected_limb = selected
    if method == "DIRECT_PREROLL":
        assert bpy.ops.character_designer.limb_ik_direct_preroll_check() == {"FINISHED"}, settings.last_message
    assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}, settings.last_message
    inventory = limb_ik._validate_inventory(armature)
    key = limb_ik.SELECTED_LIMBS[selected]
    return armature, key, inventory["rigs"][key]


def update(armature):
    limb_ik_fk._update(bpy.context, armature)


def snapshot(armature, rig):
    update(armature)
    return limb_ik_fk._matrices(armature, rig["chain"])


def test_roundtrip_auto():
    for method in ("ROLL_DECOUPLED", "DIRECT_PREROLL"):
        for selected in ("LEFT_ARM", "LEFT_LEG", "RIGHT_ARM", "RIGHT_LEG"):
            armature, key, rig = build(method, selected)
            target = armature.pose.bones[rig["target"].name]
            target.location += Vector((0.03, -0.045, 0.045))
            target.rotation_euler = (0.13, -0.07, 0.05)
            if rig["heel"]:
                armature.pose.bones[rig["heel"].name].rotation_euler.x = 0.15
            update(armature)
            before = snapshot(armature, rig)
            result = limb_ik_fk.switch_limb(bpy.context, armature, key, "FK")
            assert result["changed"]
            limb_ik_fk._verify(armature, before)
            result = limb_ik_fk.switch_limb(bpy.context, armature, key, "IK")
            assert result["changed"]
            limb_ik_fk._verify(armature, before)
            limb_ik._validate_inventory(armature)
            print("ROUNDTRIP", method, selected, result["errors"], flush=True)


def test_manual_with_parent_and_object_transforms():
    for method in ("ROLL_DECOUPLED", "DIRECT_PREROLL"):
        for selected in ("LEFT_ARM", "LEFT_LEG", "RIGHT_ARM", "RIGHT_LEG"):
            armature, key, rig = build(method, selected)
            assert bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") == {"FINISHED"}
            rig = limb_ik._validate_inventory(armature)["rigs"][key]
            armature.location = (0.2, -0.3, 0.1)
            armature.rotation_euler = (0.15, -0.07, 0.1)
            armature.scale = (0.83, 0.83, 0.83)
            if method == "ROLL_DECOUPLED":
                master = armature.pose.bones[limb_ik.MASTER_NAME]
                master.rotation_mode = "XYZ"
                master.location = (0.08, -0.04, 0.03)
                master.rotation_euler = (0.06, -0.04, 0.11)
            target = armature.pose.bones[rig["target"].name]
            target.location += Vector((0.025, -0.02, 0.025))
            target.rotation_euler = (0.21, -0.13, 0.18)
            if rig["heel"]:
                armature.pose.bones[rig["heel"].name].rotation_euler.x = 0.12
            before = snapshot(armature, rig)
            limb_ik_fk.switch_limb(bpy.context, armature, key, "FK")
            limb_ik_fk._verify(armature, before)
            limb_ik_fk.switch_limb(bpy.context, armature, key, "IK")
            limb_ik_fk._verify(armature, before)
            assert not limb_ik._validate_inventory(armature)["rigs"][key]["auto_align"]


def test_authored_fk_pose():
    for method in ("ROLL_DECOUPLED", "DIRECT_PREROLL"):
        armature, key, rig = build(method, "LEFT_LEG")
        limb_ik_fk.switch_limb(bpy.context, armature, key, "FK")
        for name, rotation in zip(rig["chain"], ((0.1, 0.07, -0.08), (-0.3, 0.0, 0.0), (0.07, 0.03, 0.1))):
            pb = armature.pose.bones[name]
            pb.rotation_mode = "XYZ"
            pb.rotation_euler.rotate(Euler(rotation))
            update(armature)
        before = snapshot(armature, rig)
        result = limb_ik_fk.switch_limb(bpy.context, armature, key, "IK")
        limb_ik_fk._verify(armature, before)
        print("AUTHORED", method, result["errors"], flush=True)


def test_failed_match_restores_everything():
    armature, key, rig = build()
    limb_ik_fk.switch_limb(bpy.context, armature, key, "FK")
    armature.pose.bones[rig["chain"][0]].scale.y = 1.6
    update(armature)
    before = {pb.name: pb.matrix_basis.copy() for pb in armature.pose.bones}
    try:
        limb_ik_fk.switch_limb(bpy.context, armature, key, "IK")
    except limb_ik.LimbIKError:
        pass
    else:
        raise AssertionError("A stretched FK limb unexpectedly matched the fixed length IK solver")
    assert limb_ik_fk.mode_for_rig(armature, rig) == "FK"
    for pb in armature.pose.bones:
        assert max(abs(pb.matrix_basis[row][col] - before[pb.name][row][col]) for row in range(4) for col in range(4)) < 1e-6
    limb_ik._validate_inventory(armature)


def test_native_keyframes_reopen_and_driver_ownership():
    armature, key, rig = build()
    bpy.context.scene.frame_set(10)
    desired = snapshot(armature, rig)
    limb_ik_fk.switch_limb(bpy.context, armature, key, "FK", keyframe=True)
    bpy.context.scene.frame_set(20)
    limb_ik_fk.switch_limb(bpy.context, armature, key, "IK", keyframe=True)
    paths = limb_ik_fk.owned_driver_paths(armature)
    assert len(paths) == 4
    for frame, expected in ((9, "IK"), (10, "FK"), (15, "FK"), (20, "IK")):
        bpy.context.scene.frame_set(frame)
        update(armature)
        assert limb_ik_fk.mode_for_rig(armature, rig) == expected
        limb_ik_fk._verify(armature, desired)
    filepath = os.path.join(tempfile.gettempdir(), "character-designer-ik-fk-test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=filepath)
    bpy.ops.wm.open_mainfile(filepath=filepath)
    armature = bpy.data.objects["Humanoid"]
    inventory = limb_ik._validate_inventory(armature)
    rig = inventory["rigs"][key]
    assert limb_ik_fk.mode_for_rig(armature, rig) == "IK"
    assert limb_ik_fk.owned_driver_paths(armature) == paths
    curve = next(curve for curve in armature.animation_data.drivers if curve.data_path in paths)
    curve.driver.expression = "0.2"
    try:
        limb_ik._validate_inventory(armature)
    except limb_ik.LimbIKError:
        pass
    else:
        raise AssertionError("An edited owned driver was accepted")


def test_animation_keeps_previous_ik_roll():
    armature, key, rig = build()
    bpy.context.scene.frame_set(1)
    original = snapshot(armature, rig)
    bpy.context.scene.frame_set(10)
    limb_ik_fk.switch_limb(bpy.context, armature, key, "FK", keyframe=True)
    bpy.context.scene.frame_set(20)
    upper = armature.pose.bones[rig["chain"][0]]
    upper.rotation_mode = "XYZ"
    upper.rotation_euler.rotate(Euler((0.1, 0.2, -0.07)))
    update(armature)
    authored = snapshot(armature, rig)
    limb_ik_fk.switch_limb(bpy.context, armature, key, "IK", keyframe=True)
    limb_ik_fk._verify(armature, authored)
    bpy.context.scene.frame_set(1)
    limb_ik_fk._verify(armature, original)
    bpy.context.scene.frame_set(20)
    limb_ik_fk._verify(armature, authored)
    bpy.context.scene.frame_set(25)
    previous_ik = snapshot(armature, rig)
    bpy.context.scene.frame_set(30)
    limb_ik_fk.switch_limb(bpy.context, armature, key, "FK", keyframe=True)
    bpy.context.scene.frame_set(40)
    upper.rotation_euler.rotate(Euler((0.03, -0.3, 0.02)))
    update(armature)
    limb_ik_fk.switch_limb(bpy.context, armature, key, "IK", keyframe=True)
    bpy.context.scene.frame_set(25)
    limb_ik_fk._verify(armature, previous_ik)


def test_keyed_switch_preserves_moving_prior_curve():
    for interpolation in ("BEZIER", "LINEAR", "CONSTANT"):
        armature, key, rig = build()
        bpy.context.scene.frame_set(1)
        limb_ik_fk.switch_limb(bpy.context, armature, key, "FK", keyframe=True)
        target = armature.pose.bones[rig["target"].name]
        target.location.x = 0.02
        target.keyframe_insert(data_path="location", index=0, frame=1)
        target.location.x = 0.6
        target.keyframe_insert(data_path="location", index=0, frame=20)
        path = target.path_from_id("location")
        def curve():
            return next(curve for curve in limb_ik._fcurves_for_action(armature.animation_data.action)
                        if curve.data_path == path and curve.array_index == 0)
        for point in curve().keyframe_points:
            point.interpolation = interpolation
        curve().update()
        samples = (1.0, 2.0, 5.0, 7.25, 8.0, 9.0)
        before = {frame: curve().evaluate(frame) for frame in samples}
        bpy.context.scene.frame_set(10)
        limb_ik_fk.switch_limb(bpy.context, armature, key, "IK", keyframe=True)
        for frame in samples:
            assert abs(curve().evaluate(frame) - before[frame]) < 1.0e-6, (
                interpolation, frame, before[frame], curve().evaluate(frame))
    armature, key, rig = build()
    target = armature.pose.bones[rig["target"].name]
    target[limb_ik_fk.PROPERTY] = 1.0
    target.keyframe_insert(data_path='["ik_fk"]', frame=1)
    target[limb_ik_fk.PROPERTY] = 0.2
    target.keyframe_insert(data_path='["ik_fk"]', frame=20)
    path = limb_ik_fk.property_path(target)
    def mode_curve():
        return next(curve for curve in limb_ik._fcurves_for_action(armature.animation_data.action)
                    if curve.data_path == path)
    samples = (1.0, 2.0, 5.0, 7.25, 8.0, 9.0)
    before = {frame: mode_curve().evaluate(frame) for frame in samples}
    bpy.context.scene.frame_set(10)
    limb_ik_fk.switch_limb(bpy.context, armature, key, "IK", keyframe=True)
    for frame in samples:
        assert abs(mode_curve().evaluate(frame) - before[frame]) < 1.0e-6


def main():
    base.ensure_registered()
    tests = (test_roundtrip_auto, test_manual_with_parent_and_object_transforms, test_authored_fk_pose,
             test_failed_match_restores_everything, test_animation_keeps_previous_ik_roll,
             test_keyed_switch_preserves_moving_prior_curve,
             test_native_keyframes_reopen_and_driver_ownership)
    for test in tests:
        test()
        print("PASS", test.__name__, flush=True)
    print("LIMB_IK_FK_TESTS_PASS", len(tests), flush=True)


if __name__ == "__main__":
    main()
