"""Removal guards and rollback on disposable Blender 5.2 hair bindings."""

import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import hair_bones_binding as binding
from character_designer import hair_bones_rig as rig
from test_hair_bones_binding_blender import scene_fixture, bones_snapshot, must_stop
from test_hair_bones_mirror_controls_blender import fixture as mirror_fixture
from test_hair_bones_variants_blender import source_state, database_counts


def pose_state(armature):
    return tuple((pose.name, pose.rotation_mode,
                  tuple(tuple(row) for row in pose.matrix_basis), dict(pose.items()))
                 for pose in armature.pose.bones)


def action_state(armature):
    action = armature.animation_data.action if armature.animation_data else None
    curves = tuple((curve.data_path, curve.array_index,
                    tuple((tuple(point.co), point.interpolation) for point in curve.keyframe_points))
                   for curve in binding._animation_paths(armature))
    return action, curves


def full_state(source, armature):
    return {"source": source_state(source), "bones": bones_snapshot(armature),
            "pose": pose_state(armature), "counts": database_counts(),
            "action": action_state(armature), "mode": bpy.context.mode,
            "active": bpy.context.active_object,
            "active_bone": armature.data.bones.active.name if armature.data.bones.active else None,
            "bone_properties": tuple((bone.name, dict(bone.items())) for bone in armature.data.bones)}


def assert_near(actual, expected, path="state"):
    if isinstance(expected, float):
        assert math.isclose(actual, expected, abs_tol=2e-6, rel_tol=2e-6), (path, actual, expected)
    elif isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key in expected:
            assert_near(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, (tuple, list)):
        assert len(actual) == len(expected), path
        for index, (first, second) in enumerate(zip(actual, expected)):
            assert_near(first, second, f"{path}[{index}]")
    else:
        assert actual == expected, (path, actual, expected)


def test_replaced_mirror_refuses_before_restoration_or_bone_deletion():
    source, plans, armature = mirror_fixture()
    binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
    mirror = next(modifier for modifier in source.modifiers if modifier.type == "MIRROR")
    name = mirror.name
    source.modifiers.remove(mirror)
    replacement = source.modifiers.new(name, "SUBSURF")
    assert replacement.name == name
    before = full_state(source, armature)
    with patch.object(binding, "_restore_touched_weights", side_effect=AssertionError("Weights restored before validating Mirror")) as restore:
        must_stop(lambda: binding.remove_hair_binding(bpy.context, source), ("mirror",))
        restore.assert_not_called()
    assert full_state(source, armature) == before
    assert binding.is_bound(source)


def test_invalid_saved_weights_and_parent_matrix_refuse_without_deletion():
    source, plans, armature = scene_fixture()
    binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
    original = source[binding.BINDING_KEY]

    def bad_weights(data):
        data["groups"][0]["weights"].append([len(source.data.vertices) + 5, 0.5])

    def bad_matrix(data):
        data["parent"]["inverse"] = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    for corrupt in (bad_weights, bad_matrix):
        data = json.loads(original)
        corrupt(data)
        source[binding.BINDING_KEY] = json.dumps(data)
        before = full_state(source, armature)
        with patch.object(binding, "_restore_touched_weights", side_effect=AssertionError("Invalid restoration payload reached mutation")) as restore:
            must_stop(lambda: binding.remove_hair_binding(bpy.context, source), ("incomplete", "stopped"))
            restore.assert_not_called()
        assert full_state(source, armature) == before
        assert binding.is_bound(source)
    source[binding.BINDING_KEY] = original
    binding.remove_hair_binding(bpy.context, source)
    assert not binding.is_bound(source)


def test_partial_weight_restore_failure_rolls_back_and_can_retry():
    source, plans, armature = scene_fixture()
    initial, initial_bones = source_state(source), bones_snapshot(armature)
    result = binding.bind_hair(bpy.context, source, plans, bone_count=4, armature=armature)
    pose = armature.pose.bones[result["chains"][0]["bones"][1]]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler = (0.17, -0.12, 0.06)
    pose["artist_setting"] = 0.42
    pose.bone["artist_bone_note"] = "Keep after failed removal"
    bpy.context.view_layer.update()
    before = full_state(source, armature)
    original_restore = binding._restore_touched_weights

    def restore_then_fail(obj, groups, affected):
        original_restore(obj, groups, affected)
        assert source_state(obj)["weights"] != before["source"]["weights"], "Failure must follow a real weight mutation"
        raise RuntimeError("Injected failure after restoring weights")

    with patch.object(binding, "_restore_touched_weights", side_effect=restore_then_fail) as restore:
        message = must_stop(lambda: binding.remove_hair_binding(bpy.context, source), ("injected",))
        assert "Rollback needs attention" not in message, message
        restore.assert_called_once()
    assert_near(full_state(source, armature), before)
    assert binding.is_bound(source)
    removed = binding.remove_hair_binding(bpy.context, source)
    assert removed["removed_bones"] == 8
    assert not binding.is_bound(source)
    assert source_state(source) == initial and bones_snapshot(armature) == initial_bones


def test_body_only_action_survives_hair_removal():
    source, plans, armature = scene_fixture()
    head, neck = armature.pose.bones["Head"], armature.pose.bones["Neck"]
    for pose in (head, neck):
        pose.rotation_mode = "XYZ"
        pose.rotation_euler = (0, 0, 0)
        pose.keyframe_insert("rotation_euler", frame=1)
        pose.rotation_euler.x = 0.25
        pose.keyframe_insert("rotation_euler", frame=12)
        pose.rotation_euler = (0, 0, 0)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    before = action_state(armature)
    assert before[0] is not None and before[1]
    binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
    binding.remove_hair_binding(bpy.context, source)
    assert not binding.is_bound(source)
    assert action_state(armature) == before
    assert armature.animation_data.action is before[0]
    bpy.context.scene.frame_set(12)
    assert abs(head.rotation_euler.x - 0.25) < 1e-6


def test_hair_bone_action_refuses_without_deleting_action():
    source, plans, armature = scene_fixture()
    result = binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
    pose = armature.pose.bones[result["chains"][0]["bones"][0]]
    pose.rotation_mode = "XYZ"
    pose.keyframe_insert("rotation_euler", frame=1)
    pose.rotation_euler.z = 0.3
    pose.keyframe_insert("rotation_euler", frame=12)
    bpy.context.view_layer.update()
    before = full_state(source, armature)
    action = armature.animation_data.action
    assert action is not None
    must_stop(lambda: binding.remove_hair_binding(bpy.context, source), ("animation",))
    assert full_state(source, armature) == before
    assert armature.animation_data.action is action and action.name in bpy.data.actions
    assert binding.is_bound(source)


def main():
    tests = (test_replaced_mirror_refuses_before_restoration_or_bone_deletion,
             test_invalid_saved_weights_and_parent_matrix_refuse_without_deletion,
             test_partial_weight_restore_failure_rolls_back_and_can_retry,
             test_body_only_action_survives_hair_removal,
             test_hair_bone_action_refuses_without_deleting_action)
    for test in tests:
        test()
        print("HAIR_BINDING_GUARD_TEST=" + json.dumps({"test": test.__name__, "status": "passed"}))
    print("HAIR_BINDING_GUARDS_OK")


if __name__ == "__main__":
    main()
