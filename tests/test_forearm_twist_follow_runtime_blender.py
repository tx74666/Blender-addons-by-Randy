"""Disposable native-FK wrist correction and persistence integration checks.

Run only in an isolated Blender --background --factory-startup process. Existing
small humanoid/sleeve fixtures supply all scene data. Expected mesh points use
an independent articulated circular-arc/LBS expression, never corrected_vertex
or desired_vertex. Temporary save/reload checks never open an artist file.
"""

import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "addons"), str(ROOT / "tests")]
from character_designer import forearm_twist as runtime, limb_ik_fk, unity_forearm
import test_forearm_twist_blender as fixtures
from test_forearm_twist_ranges_blender import bilateral_fixture

def write_rotation(bone, rotation, mode="QUATERNION"):
    bone.rotation_mode = mode
    if mode == "QUATERNION":
        bone.rotation_quaternion = rotation
    elif mode == "AXIS_ANGLE":
        axis, angle = rotation.to_axis_angle()
        bone.rotation_axis_angle = (angle, *axis)
    else:
        bone.rotation_euler = rotation.to_euler(mode)


def native_pose(fixture, alpha, beta, *, bend=0.0, wrist_bend=0.0,
                whole=None, mode="QUATERNION"):
    """Author known deformation-space swing/twist in the native input channels."""
    arm = fixture["armature"]
    upper_name = fixture["rig"]["chain"][0]
    lower = arm.pose.bones[fixture["lower_name"]]
    hand = arm.pose.bones[fixture["hand_name"]]
    upper = arm.pose.bones[upper_name]
    axis = fixture["axis"]
    perpendicular = lower.bone.matrix_local.to_3x3() @ Vector((1.0, 0.0, 0.0))
    lower_rest = lower.bone.matrix_local.to_quaternion().normalized()
    hand_rest = hand.bone.matrix_local.to_quaternion().normalized()
    upper_rest = upper.bone.matrix_local.to_quaternion().normalized()
    lower_delta = Quaternion(perpendicular, bend) @ Quaternion(axis, math.radians(alpha))
    hand_delta = Quaternion(perpendicular, wrist_bend) @ Quaternion(axis, math.radians(beta))
    whole = Quaternion() if whole is None else whole
    write_rotation(upper, upper_rest.conjugated() @ whole @ upper_rest, mode)
    write_rotation(lower, lower_rest.conjugated() @ lower_delta @ lower_rest, mode)
    write_rotation(hand, hand_rest.conjugated() @ hand_delta @ hand_rest, mode)
    authored = fixtures.pose_snapshot(arm)
    bpy.context.view_layer.update()
    fixtures.assert_pose_snapshot(arm, authored, "Evaluation preserves authored native inputs")


def smooth(value):
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def sample_record(record, position):
    """Independent interpolation and capture-mask interpretation."""
    first, last = record.get("range_start", 0), record.get("range_end", len(record["rings"]) - 1)
    continuous = record.get("distribution") == "WRIST_CONTINUOUS"
    rings = record["rings"][first:last + 1] if continuous else record["rings"]
    knots = [(ring["position"], ring["ratio"]) for ring in rings]
    if "range_start" not in record:
        knots = [(0.0, 0.0)] + [(p, r) for p, r in knots if 1e-6 < p < 1.0 - 1e-6] + [(1.0, 1.0)]
    ratio = knots[0][1] if position <= knots[0][0] else knots[-1][1]
    for left, right in zip(knots, knots[1:]):
        if left[0] < position < right[0]:
            ratio = left[1] + (right[1] - left[1]) * smooth((position - left[0]) / (right[0] - left[0]))
            break
        if position == right[0]:
            ratio = right[1]
            break
    start = record["rings"][first]["position"]
    end = record["rings"][last]["position"]
    influence = 1.0
    if continuous:
        influence = 0.0 if position < start else 1.0
    elif "range_start" in record:
        if position <= start or position >= end:
            influence = 0.0
        elif record.get("transition", 0.1):
            width = record.get("transition", 0.1) * (end - start)
            influence = smooth(min((position - start) / width, (end - position) / width))
    return ratio, influence


def weighted_point(point, matrices, weights):
    if not weights:
        return point.copy()
    result = Vector((0.0, 0.0, 0.0))
    for name, weight in weights.items():
        result += weight * (matrices[name] @ point)
    return result


def expected_points(fixture, angles):
    """Independent physical expression; angles are authored degrees per side."""
    mesh, arm = fixture["mesh"], fixture["armature"]
    matrices = fixtures.deformation_matrices(arm)
    to_arm = arm.matrix_world.inverted() @ mesh.matrix_world
    from_arm = to_arm.inverted()
    source = fixtures.uncorrected_points(mesh)
    expected = []
    records = runtime._records(mesh)
    by_vertex = {index: (side, record, position)
                 for side, record in records.items()
                 for index, position in zip(record["vertices"], record["positions"])}
    for index, coordinate in enumerate(source):
        point = to_arm @ coordinate
        weights = fixtures.normalized_weights(mesh, arm, index)
        actual = weighted_point(point, matrices, weights)
        if index not in by_vertex:
            expected.append(from_arm @ actual)
            continue
        side, record, position = by_vertex[index]
        if not record.get("enabled", True):
            expected.append(from_arm @ actual)
            continue
        _alpha, beta = map(math.radians, angles.get(side, (0.0, 0.0)))
        # Common forearm alpha stays in each actual bone matrix. Only the
        # relative wrist beta is distributed, giving alpha + ratio * beta
        # for a pure axial lower/hand pair independently of their weight split.
        ratio, influence = sample_record(record, position)
        lower, hand = record["chain"][1:]
        axis = (arm.data.bones[lower].tail_local - arm.data.bones[lower].head_local).normalized()
        pivot = arm.data.bones[hand].head_local
        desired = Vector((0.0, 0.0, 0.0))
        for name, weight in weights.items():
            correction = ratio * beta if name == lower else (ratio - 1.0) * beta if name == hand else 0.0
            rotated = pivot + Quaternion(axis, correction) @ (point - pivot)
            desired += weight * (matrices[name] @ rotated)
        expected.append(from_arm @ actual.lerp(desired, influence))
    return expected


def artist_state(fixture):
    arm, mesh = fixture["armature"], fixture["mesh"]
    return {"structure": fixtures.structure_snapshot(arm, mesh),
            "keys": fixtures.key_snapshot(mesh),
            "vertices": tuple(tuple(vertex.co) for vertex in mesh.data.vertices),
            "object_matrices": (fixtures.matrix_tuple(arm.matrix_world), fixtures.matrix_tuple(mesh.matrix_world)),
            "drivers": tuple((curve.data_path, curve.array_index, curve.mute, curve.driver.expression)
                             for curve in getattr(arm.animation_data, "drivers", ())),
            "actions": tuple((action.name, tuple((curve.data_path, curve.array_index,
                           tuple((tuple(key.co), key.interpolation) for key in curve.keyframe_points))
                          for curve in runtime.limb_ik._fcurves_for_action(action)))
                         for action in runtime.limb_ik._actions_for_id(arm))}


def all_keys(mesh):
    return fixtures.key_snapshot(mesh, tuple(mesh.data.shape_keys.key_blocks.keys()))


def side_fixture(fixture, side):
    result = dict(fixture)
    arm = fixture["armature"]
    lower, hand = "forearm." + side, "hand." + side
    bone = arm.data.bones[lower]
    result.update(side=side, lower_name=lower, hand_name=hand,
                  rig={"chain": ["upper_arm." + side, lower, hand]},
                  target=arm.pose.bones[hand],
                  axis=(bone.tail_local - bone.head_local).normalized(), pivot=bone.tail_local.copy())
    return result


class FollowRuntimeTests(unittest.TestCase):
    def tearDown(self):
        if runtime._SESSION is not None:
            runtime.finish_test(bpy.context, confirm=False)

    def make(self, **kwargs):
        return fixtures.make_fixture("DIRECT_PREROLL", build_ik=False, **kwargs)

    def calibrate(self, fixture, **kwargs):
        runtime.start_test(bpy.context, fixture["mesh"], side=fixture["side"],
                           continuous=True, **kwargs)
        runtime.finish_test(bpy.context, confirm=True)

    def check(self, fixture, angles, label):
        arm, mesh = fixture["armature"], fixture["mesh"]
        before, pose = artist_state(fixture), fixtures.pose_snapshot(arm)
        runtime.update_runtime(bpy.context.scene)
        self.assertNotIn(mesh.name, runtime._ERRORS)
        fixtures.assert_points_close(fixtures.evaluated_points(mesh), expected_points(fixture, angles),
                                     label, tolerance=2e-5)
        self.assertEqual(artist_state(fixture), before)
        fixtures.assert_pose_snapshot(arm, pose, label + " does not write native pose")

    def test_native_fk_common_roll_left_right_and_rotation_representations(self):
        for side in ("L", "R"):
            for mode in ("XYZ", "QUATERNION", "AXIS_ANGLE"):
                with self.subTest(side=side, mode=mode):
                    f = self.make(side=side, forearm_key=False)
                    native_pose(f, 76.0, 0.0, mode=mode)
                    protected, pose = artist_state(f), fixtures.pose_snapshot(f["armature"])
                    self.calibrate(f)
                    record = runtime._records(f["mesh"])[side]
                    self.assertNotIn("twist_mode", record)
                    self.assertEqual(record["version"], 1)
                    self.assertEqual(artist_state(f), protected)
                    fixtures.assert_pose_snapshot(f["armature"], pose, "Follow capture preserves native input")
                    self.check(f, {side: (76.0, 0.0)}, "Inherited common roll " + side + " " + mode)

    def test_wrist_input_matches_legacy_and_default_records_stay_legacy(self):
        f = self.make()
        native_pose(f, 0.0, -71.0)
        runtime.start_test(bpy.context, f["mesh"], continuous=True)
        runtime.finish_test(bpy.context, confirm=True)
        self.assertNotIn("twist_mode", runtime._records(f["mesh"])["L"])
        self.check(f, {"L": (0.0, -71.0)}, "Default legacy wrist route")
        legacy = fixtures.evaluated_points(f["mesh"])
        native_pose(f, 61.0, 0.0)
        self.check(f, {"L": (61.0, 0.0)}, "Legacy common forearm roll keeps original skinning")
        keys = f["mesh"].data.shape_keys
        managed = keys.key_blocks[runtime._records(f["mesh"])["L"]["key"]]
        fixtures.assert_points_close([point.co for point in managed.data],
                                     [point.co for point in keys.reference_key.data],
                                     "Legacy common roll creates no correction", tolerance=2e-6)
        native_pose(f, 0.0, -71.0)
        self.calibrate(f)
        fixtures.assert_points_close(fixtures.evaluated_points(f["mesh"]), legacy,
                                     "Reentry with alpha zero keeps saved wrist behavior", tolerance=2e-6)
        self.check(f, {"L": (0.0, -71.0)}, "Native FK wrist-only input")

    def test_combined_bends_and_rig_object_transform_preserve_artist_channels(self):
        f = self.make(transformed_objects=True)
        self.calibrate(f)
        whole = Quaternion(Vector((0.2, 0.3, 0.7)).normalized(), 0.61)
        native_pose(f, 35.0, -18.0, bend=0.26, wrist_bend=-0.31, whole=whole)
        self.check(f, {"L": (35.0, -18.0)}, "Articulated follow with different mesh/rig transforms")
        native_pose(f, 0.0, 0.0, bend=0.26, wrist_bend=-0.31, whole=whole)
        self.check(f, {"L": (0.0, 0.0)}, "Non-axial bend retains original skinning")

    def test_selected_boundary_keeps_common_roll_and_does_not_accumulate(self):
        f = self.make(forearm_key=False)
        self.calibrate(f, start_vertices=f["rings"][2])
        native_pose(f, 81.0, 0.0)
        self.check(f, {"L": (81.0, 0.0)}, "Selected sleeve preserves common forearm roll")
        record = runtime._records(f["mesh"])["L"]
        first = record["range_start"]
        self.assertGreater(first, 0)
        matrices = fixtures.deformation_matrices(f["armature"])
        raw = fixtures.uncorrected_points(f["mesh"])
        actual = fixtures.evaluated_points(f["mesh"])
        untouched = record["rings"][first]["vertices"] + record["rings"][first - 1]["vertices"] + f["outside"]
        for index in untouched:
            reference = weighted_point(raw[index], matrices,
                                       fixtures.normalized_weights(f["mesh"], f["armature"], index))
            self.assertLess((actual[index] - reference).length, 2e-6)
        buffers, protected = all_keys(f["mesh"]), artist_state(f)
        for _ in range(5):
            runtime.update_runtime(bpy.context.scene)
            bpy.context.view_layer.update()
        self.assertEqual(all_keys(f["mesh"]), buffers)
        self.assertEqual(artist_state(f), protected)

    def test_confirm_cancel_reentry_and_temporary_save_reload_keep_capture(self):
        f = self.make()
        native_pose(f, 25.0, 10.0)
        mesh, arm = f["mesh"], f["armature"]
        protected, pose = artist_state(f), fixtures.pose_snapshot(arm)
        runtime.start_test(bpy.context, mesh, continuous=True)
        runtime.finish_test(bpy.context, confirm=False)
        self.assertNotIn(runtime.RECORD_KEY, mesh)
        self.assertEqual(artist_state(f), protected)
        fixtures.assert_pose_snapshot(arm, pose, "Fresh follow Cancel")
        self.calibrate(f, initial_angle=math.radians(90.0))
        self.assertEqual(artist_state(f), protected)
        fixtures.assert_pose_snapshot(arm, pose, "Confirm restores all authored rotation modes/channels")
        raw = mesh[runtime.RECORD_KEY]
        runtime.start_test(bpy.context, mesh)
        runtime.set_ratio(bpy.context, 3, 0.27)
        runtime.finish_test(bpy.context, confirm=False)
        self.assertEqual(mesh[runtime.RECORD_KEY], raw)
        self.check(f, {"L": (25.0, 10.0)}, "Cancel preserves previously confirmed loop capture")
        names = mesh.name, arm.name
        with tempfile.TemporaryDirectory(prefix="cd_forearm_follow_") as folder:
            path = os.path.join(folder, "follow_fixture.blend")
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {"FINISHED"})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False), {"FINISHED"})
            f.update(mesh=bpy.data.objects[names[0]], armature=bpy.data.objects[names[1]])
            f["target"] = f["armature"].pose.bones[f["hand_name"]]
            runtime.update_runtime(bpy.context.scene)
            self.assertEqual(f["mesh"][runtime.RECORD_KEY], raw)
            fixtures.assert_pose_snapshot(f["armature"], pose, "Saved follow native pose")
            self.assertEqual(artist_state(f), protected)
            self.check(f, {"L": (25.0, 10.0)}, "Persistent native-FK capture after reload")

    def test_wrist_angle_bound_preserves_forearm_roll_and_recovers(self):
        f = self.make()
        self.calibrate(f)
        mesh, arm = f["mesh"], f["armature"]
        for alpha, beta in ((170.0, 0.0), (0.0, -120.0), (170.0, 120.0), (-170.0, 60.0)):
            native_pose(f, alpha, beta)
            self.check(f, {"L": (alpha, beta)}, "Wrist 120-degree boundary and unrestricted common roll")
        for alpha, beta in ((25.0, 121.0), (170.0, -121.0), (76.0, 160.0), (-150.0, -160.0)):
            with self.subTest(alpha=alpha, beta=beta):
                native_pose(f, alpha, beta)
                protected, pose = artist_state(f), fixtures.pose_snapshot(arm)
                runtime.update_runtime(bpy.context.scene)
                self.assertIn("120", runtime._ERRORS.get(mesh.name, ""))
                record = runtime._records(mesh)["L"]
                self.assertTrue(mesh.data.shape_keys.key_blocks[record["key"]].mute)
                self.assertEqual(artist_state(f), protected)
                fixtures.assert_pose_snapshot(arm, pose, "Out-of-range fallback preserves pose")
                native_pose(f, 35.0, -10.0)
                self.check(f, {"L": (35.0, -10.0)}, "Valid follow pose restores managed output")

    def test_mirror_keeps_native_targets_and_side_inputs_independent(self):
        f = bilateral_fixture()
        protected, pose = artist_state(f), fixtures.pose_snapshot(f["armature"])
        self.calibrate(f, symmetry=True)
        records = runtime._records(f["mesh"])
        self.assertEqual(set(records), {"L", "R"})
        self.assertTrue(all("twist_mode" not in record for record in records.values()))
        self.assertEqual({side: record["target"] for side, record in records.items()},
                         {"L": "hand.L", "R": "hand.R"})
        self.assertEqual(artist_state(f), protected)
        fixtures.assert_pose_snapshot(f["armature"], pose, "Paired follow capture preserves both arms")
        right = side_fixture(f, "R")
        native_pose(f, 41.0, 12.0)
        native_pose(right, -38.0, 17.0)
        self.check(f, {"L": (41.0, 12.0), "R": (-38.0, 17.0)}, "Independent left/right follow inputs")

    def test_installed_controls_fk_uses_native_hand_and_preserves_animation(self):
        f = fixtures.make_fixture("DIRECT_PREROLL", build_ik=True)
        arm, mesh = f["armature"], f["mesh"]
        generated_name = f["target"].name
        limb_ik_fk.switch_limb(bpy.context, arm, ("ARM", "L"), "FK", keyframe=False)
        fixtures.activate_mesh(mesh)
        _arm, route = runtime._resolve_rig(mesh, "L")
        self.assertEqual(route["target"].name, f["hand_name"])
        self.assertTrue(route.get("native_source") and route.get("fk_source"))
        self.assertFalse(route.get("original_source"))
        f["target"] = arm.pose.bones[f["hand_name"]]
        native_pose(f, 28.0, 14.0)
        protected, pose = artist_state(f), fixtures.pose_snapshot(arm)
        runtime.start_test(bpy.context, mesh, continuous=True)
        self.assertEqual(runtime._SESSION["target"], f["hand_name"])
        self.assertFalse(runtime._SESSION["pose_locked"], "Owned influence drivers are not native rotation drivers")
        runtime.finish_test(bpy.context, confirm=False)
        self.assertEqual(artist_state(f), protected)
        fixtures.assert_pose_snapshot(arm, pose, "Installed FK preview Cancel")
        self.calibrate(f)
        self.check(f, {"L": (28.0, 14.0)}, "Installed FK follow capture")
        generated_pose = fixtures.pose_snapshot(arm)[generated_name]
        for frame, alpha, beta in ((1, 28.0, 14.0), (10, -34.0, 47.0)):
            bpy.context.scene.frame_set(frame)
            native_pose(f, alpha, beta)
            for name in (f["lower_name"], f["hand_name"]):
                arm.pose.bones[name].keyframe_insert(data_path="rotation_quaternion", frame=frame)
        bpy.context.scene.frame_set(1)
        before_animation = artist_state(f)
        before_preview_pose = fixtures.pose_snapshot(arm)
        runtime.start_test(bpy.context, mesh, initial_angle=math.radians(90.0))
        self.assertTrue(runtime._SESSION["pose_locked"], "Actual hand transform keyframes protect native preview inputs")
        fixtures.assert_pose_snapshot(arm, before_preview_pose, "Keyed native hand preview is read-only")
        runtime.finish_test(bpy.context, confirm=False)
        self.assertEqual(artist_state(f), before_animation)
        for frame, alpha, beta in ((1, 28.0, 14.0), (10, -34.0, 47.0)):
            bpy.context.scene.frame_set(frame)
            self.check(f, {"L": (alpha, beta)}, "Keyed installed FK frame " + str(frame))
        self.assertEqual(fixtures.pose_snapshot(arm)[generated_name], generated_pose)

    def test_unity_existing_protocol_capture_is_available_and_read_only(self):
        f = self.make()
        self.calibrate(f)
        native_pose(f, 32.0, 21.0)
        self.check(f, {"L": (32.0, 21.0)}, "Unity native-FK export input")
        mesh = f["mesh"]
        before = (artist_state(f), fixtures.pose_snapshot(f["armature"]), mesh[runtime.RECORD_KEY], all_keys(mesh))
        payload = unity_forearm.capture(mesh)
        self.assertTrue(payload["sides"][0]["enabled"])
        self.assertEqual(payload["sides"][0]["lowerBone"], f["lower_name"])
        self.assertEqual(payload["sides"][0]["handBone"], f["hand_name"])
        self.assertEqual(payload["objectName"], mesh.name)
        self.assertEqual((artist_state(f), fixtures.pose_snapshot(f["armature"]), mesh[runtime.RECORD_KEY], all_keys(mesh)), before)
        records = runtime._records(mesh)
        records["L"]["enabled"] = False
        runtime._write_records(mesh, records)
        runtime.update_runtime(bpy.context.scene)
        disabled_before = (artist_state(f), fixtures.pose_snapshot(f["armature"]), mesh[runtime.RECORD_KEY], all_keys(mesh))
        payload = unity_forearm.capture(mesh)
        self.assertFalse(payload["sides"][0]["enabled"])
        self.assertEqual((artist_state(f), fixtures.pose_snapshot(f["armature"]), mesh[runtime.RECORD_KEY], all_keys(mesh)), disabled_before)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FollowRuntimeTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
