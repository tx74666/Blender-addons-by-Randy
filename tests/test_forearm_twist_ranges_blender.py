"""Evaluated-mesh and recovery tests for explicit forearm correction ranges.

Uses generated disposable fixtures only; never reads or writes production X.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "addons"), os.path.join(ROOT, "tests")]
from character_designer import forearm_twist as runtime
from character_designer import forearm_twist_edit as editing
from character_designer import forearm_twist_math as geometry
from character_designer import forearm_twist_topology as topology
from character_designer.forearm_twist_symmetry import mirror_ring_pairs
import test_forearm_twist_blender as fixtures


def keys(mesh):
    return fixtures.key_snapshot(mesh, names=tuple(mesh.data.shape_keys.key_blocks.keys()))


def ratios(record):
    return [ring["ratio"] for ring in record["rings"]]


def anchors(record):
    return [frozenset(record["rings"][record[name]]["vertices"])
            for name in ("range_start", "range_end", "current_ring")]


def uncorrected_skin(fixture):
    """Independent existing-key + linear blend skin reference, without correction."""
    mesh, arm = fixture["mesh"], fixture["armature"]
    transforms = fixtures.deformation_matrices(arm)
    to_arm = arm.matrix_world.inverted() @ mesh.matrix_world
    from_arm = to_arm.inverted()
    return [from_arm @ (geometry.blended_matrix(transforms, fixtures.normalized_weights(mesh, arm, index))
                        @ (to_arm @ point))
            for index, point in enumerate(fixtures.uncorrected_points(mesh))]


def test_bounded_evaluated_deformation_and_idempotence():
    for method in fixtures.BUILD_METHODS:
        f = fixtures.make_fixture(method, transformed_objects=True)
        mesh, arm = f["mesh"], f["armature"]
        structure = fixtures.structure_snapshot(arm, mesh)
        originals = fixtures.key_snapshot(mesh)
        runtime.start_test(bpy.context, mesh)
        runtime.set_range(bpy.context, 1, 6)
        runtime.set_ratio(bpy.context, 3, .77)
        record = fixtures.record_for(mesh)
        actual, baseline = fixtures.evaluated_points(mesh), uncorrected_skin(f)
        outside = set(f["outside"] + f["guard_rings"][0] + f["guard_rings"][1])
        for index in (0, 1, 6, 7):
            outside.update(record["rings"][index]["vertices"])
        fixtures.assert_points_close([actual[i] for i in sorted(outside)],
                                     [baseline[i] for i in sorted(outside)],
                                     method + " outside and exact selected boundaries", tolerance=4e-6)
        interior = record["rings"][3]["vertices"]
        assert max((actual[i] - baseline[i]).length for i in interior) > .002
        buffer = keys(mesh)
        for _ in range(5):
            runtime.update_runtime(bpy.context.scene, bpy.context.evaluated_depsgraph_get())
            bpy.context.view_layer.update()
        assert keys(mesh) == buffer, "Repeated runtime updates accumulated a correction"
        assert fixtures.structure_snapshot(arm, mesh) == structure
        assert fixtures.key_snapshot(mesh) == originals
        runtime.finish_test(bpy.context, confirm=False)
        print("PASS bounded evaluated deformation and idempotence", method, flush=True)


def test_explicit_profile_edits_and_refusal_atomicity():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    mesh = f["mesh"]
    runtime.start_test(bpy.context, mesh)
    runtime.set_range(bpy.context, 1, 6)
    runtime.set_ratio(bpy.context, 1, .15)
    runtime.set_ratio(bpy.context, 6, .83)
    runtime.set_ratio(bpy.context, 3, .92)
    before = fixtures.record_for(mesh)
    runtime.apply_batch(bpy.context, 2, 2, 2, .27)
    after = fixtures.record_for(mesh)
    assert {i for i, (a, b) in enumerate(zip(ratios(before), ratios(after))) if a != b} == {2, 4}
    assert after["rings"][2]["ratio"] == .27 and after["rings"][4]["ratio"] == .27
    runtime.apply_batch(bpy.context, 2, 2, 2, .27)
    assert fixtures.record_for(mesh) == after, "Batch must write absolute shares, not accumulate"
    raw, buffer = mesh[runtime.RECORD_KEY], keys(mesh)
    for bad in (lambda: runtime.set_range(bpy.context, 5, 2),
                lambda: runtime.set_range(bpy.context, 2, 2),
                lambda: runtime.set_ratio(bpy.context, 0, .2),
                lambda: runtime.apply_batch(bpy.context, 0, 2, 1, .7),
                lambda: runtime.apply_batch(bpy.context, 3, 2, 0, .7)):
        fixtures.assert_refused(bad, "Invalid explicit range edit")
        assert mesh[runtime.RECORD_KEY] == raw and keys(mesh) == buffer
    runtime.apply_default_profile(bpy.context, .4)
    default = fixtures.record_for(mesh)
    assert default["curve_strength"] == .4
    assert all(default["rings"][i] == after["rings"][i] for i in (0, 1, 6, 7))
    assert all(.15 <= default["rings"][i]["ratio"] <= .83 for i in range(2, 6))
    runtime.set_ratio(bpy.context, 3, .98)
    sharp = fixtures.record_for(mesh)
    runtime.smooth_profile(bpy.context)
    smooth = fixtures.record_for(mesh)
    assert smooth["rings"][3]["ratio"] < sharp["rings"][3]["ratio"]
    assert all(smooth["rings"][i] == sharp["rings"][i] for i in (0, 1, 6, 7))
    runtime.finish_test(bpy.context, confirm=False)
    print("PASS explicit profile, boundary anchors, absolute batch and atomic refusal", flush=True)


def test_cancel_reentry_and_save_reopen_keep_authored_capture():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    mesh, arm = f["mesh"], f["armature"]
    fixtures.pose_target(f, 22., bend=.04)
    runtime.start_test(bpy.context, mesh)
    runtime.set_range(bpy.context, 1, 6)
    runtime.set_ratio(bpy.context, 2, .19)
    runtime.set_ratio(bpy.context, 5, .79)
    editing.set_current(bpy.context, 5)
    runtime.finish_test(bpy.context, confirm=True)
    confirmed = fixtures.record_for(mesh)
    raw, buffer, pose = mesh[runtime.RECORD_KEY], keys(mesh), fixtures.pose_snapshot(arm)
    runtime.start_test(bpy.context, mesh)
    assert fixtures.record_for(mesh) == confirmed, "Reentry must keep saved rings, shares, bounds and current loop"
    runtime.set_range(bpy.context, 2, 5)
    runtime.set_ratio(bpy.context, 3, .93)
    runtime.finish_test(bpy.context, confirm=False)
    assert mesh[runtime.RECORD_KEY] == raw, "Cancel lost the preceding record"
    assert keys(mesh) == buffer, "Cancel lost prior corrective coordinates/value/mute"
    fixtures.assert_pose_snapshot(arm, pose, "Range preview Cancel")
    names = mesh.name, arm.name
    with tempfile.TemporaryDirectory(prefix="cd_forearm_ranges_") as folder:
        path = os.path.join(folder, "confirmed_ranges.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
        mesh, arm = bpy.data.objects[names[0]], bpy.data.objects[names[1]]
        runtime.update_runtime(bpy.context.scene)
        assert fixtures.record_for(mesh) == confirmed
        assert keys(mesh) == buffer
        fixtures.assert_pose_snapshot(arm, pose, "Range save/reopen")
    print("PASS range Cancel, reentry and save/reopen", flush=True)


def test_manual_capture_addition_preserves_indices_and_legacy_opt_in():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    mesh, arm = f["mesh"], f["armature"]
    captured = [topology.capture_loop(mesh, arm, f["lower_name"], ring)
                for index, ring in enumerate(f["rings"]) if index != 3]
    runtime.start_test(bpy.context, mesh, rings_override=captured)
    runtime.set_range(bpy.context, 1, 5)
    runtime.set_ratio(bpy.context, 2, .37)
    editing.set_current(bpy.context, 4)
    runtime.finish_test(bpy.context, confirm=True)
    before = fixtures.record_for(mesh)
    old_by_ids = {frozenset(r["vertices"]): r for r in before["rings"]}
    saved_anchors = anchors(before)
    added = topology.capture_loop(mesh, arm, f["lower_name"], list(reversed(f["rings"][3])))
    runtime.manual_add_loop(bpy.context, mesh, "L", added)
    complete = fixtures.record_for(mesh)
    assert len(complete["rings"]) == len(f["rings"])
    assert anchors(complete) == saved_anchors, "Inserting a loop moved saved boundary/current identities"
    assert all(r == old_by_ids[frozenset(r["vertices"])] for r in complete["rings"]
               if frozenset(r["vertices"]) in old_by_ids)
    assert next(r for r in complete["rings"] if frozenset(r["vertices"]) == frozenset(added["vertices"]))["vertices"] == added["vertices"]
    runtime.start_test(bpy.context, mesh)
    assert fixtures.record_for(mesh) == complete, "Reentry rediscovered or replaced manual loops"
    runtime.finish_test(bpy.context, confirm=True)
    # An old file evaluates using its saved behavior until the user explicitly
    # enters the new bounded editor; Cancel must then recover that old format.
    legacy = runtime._records(mesh)
    for key in ("range_start", "range_end", "current_ring", "curve_strength", "transition"):
        legacy["L"].pop(key, None)
    runtime._write_records(mesh, legacy)
    legacy_raw = mesh[runtime.RECORD_KEY]
    runtime.update_runtime(bpy.context.scene)
    assert mesh[runtime.RECORD_KEY] == legacy_raw, "Runtime silently opted legacy data into bounded behavior"
    runtime.start_test(bpy.context, mesh)
    migrated = fixtures.record_for(mesh)
    assert "range_start" in migrated and migrated["rings"] == legacy["L"]["rings"]
    runtime.finish_test(bpy.context, confirm=False)
    assert mesh[runtime.RECORD_KEY] == legacy_raw
    print("PASS manual persistent loops and reversible explicit legacy opt-in", flush=True)


def test_overlay_matches_deformed_world_space_and_boundary_layering():
    f = fixtures.make_fixture("ROLL_DECOUPLED", transformed_objects=True)
    mesh = f["mesh"]
    runtime.start_test(bpy.context, mesh)
    runtime.set_range(bpy.context, 2, 5)
    runtime.set_ratio(bpy.context, 3, .81)
    editing.set_current(bpy.context, 2)
    record = fixtures.record_for(mesh)
    layers = runtime.overlay_geometry(bpy.context)
    assert {(l["index"], l["kind"]) for l in layers} == {(2, "start"), (5, "end"), (2, "current")}
    start, current = next(l for l in layers if l["kind"] == "start"), next(l for l in layers if l["kind"] == "current")
    assert start["width"] > current["width"] and start["color"] != current["color"]
    evaluated = fixtures.evaluated_points(mesh)
    for layer in runtime.overlay_geometry(bpy.context, all_rings=True):
        ids = record["rings"][layer["index"]]["vertices"]
        expected = [mesh.matrix_world @ evaluated[i] for i in ids + ids[:1]]
        fixtures.assert_points_close(layer["points"], expected, "Deformed world overlay", tolerance=5e-6)
        assert (Vector(layer["points"][0]) - Vector(layer["points"][-1])).length < 1e-8
    runtime.finish_test(bpy.context, confirm=False)
    assert runtime.overlay_geometry(bpy.context) == []
    print("PASS deformed overlay and coincident boundary/current visibility", flush=True)


def bilateral_fixture():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    obj = f["mesh"]
    old = obj.data
    original = [v.co.copy() for v in old.vertices]
    n = len(original)
    # Reverse the entire other side's vertex numbering; no index-offset mapping.
    reflected_id = lambda index: n + n - 1 - index
    points = original + [Vector((-p.x, p.y, p.z)) for p in reversed(original)]
    faces = [tuple(p.vertices) for p in old.polygons]
    faces += [tuple(reflected_id(i) for i in reversed(p.vertices)) for p in old.polygons]
    keydata = [(k.name, k.value, k.mute, [v.co.copy() for v in k.data]) for k in old.shape_keys.key_blocks]
    weights = [[(obj.vertex_groups[g.group].name, g.weight) for g in v.groups] for v in old.vertices]
    mesh = bpy.data.meshes.new("BilateralForearmRangeMesh")
    mesh.from_pydata(points, [], faces)
    mesh.update()
    obj.data = mesh
    for name in ("forearm.L", "hand.L", "Hips", "forearm.R", "hand.R"):
        if name not in obj.vertex_groups:
            obj.vertex_groups.new(name=name)
    for index, entries in enumerate(weights):
        for name, weight in entries:
            obj.vertex_groups[name].add([index], weight, "REPLACE")
            other = name[:-2] + ".R" if name.endswith(".L") else name
            obj.vertex_groups[other].add([reflected_id(index)], weight, "REPLACE")
    for name, value, mute, coordinates in keydata:
        key = obj.shape_key_add(name=name)
        for index, co in enumerate(coordinates):
            key.data[index].co = co
            key.data[reflected_id(index)].co = (-co.x, co.y, co.z)
        key.value, key.mute = value, mute
    return f


def test_mirror_ranges_uses_geometric_pairs_and_failure_rolls_back():
    f = bilateral_fixture()
    mesh, arm = f["mesh"], f["armature"]
    runtime.start_test(bpy.context, mesh, symmetry=False)
    runtime.set_range(bpy.context, 1, 6)
    runtime.set_ratio(bpy.context, 2, .29)
    editing.set_current(bpy.context, 4)
    runtime.finish_test(bpy.context, confirm=True)
    before_json, before_keys = mesh[runtime.RECORD_KEY], keys(mesh)
    before_pose, before_structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
    with patch.object(runtime, "_calculate_object", side_effect=RuntimeError("injected second-side evaluation failure")):
        try:
            runtime.mirror_calibration(bpy.context, mesh, "L")
        except RuntimeError as exc:
            assert "injected" in str(exc)
        else:
            raise AssertionError("Expected injected mirror transaction failure")
    assert mesh[runtime.RECORD_KEY] == before_json and keys(mesh) == before_keys
    fixtures.assert_pose_snapshot(arm, before_pose, "Mirror rollback")
    runtime.mirror_calibration(bpy.context, mesh, "L")
    records = runtime._records(mesh)
    pairs = dict(mirror_ring_pairs(mesh, arm, records["L"], records["R"]))
    assert all(records["R"][name] == pairs[records["L"][name]]
               for name in ("range_start", "range_end", "current_ring"))
    assert all(records["L"]["rings"][s]["ratio"] == records["R"]["rings"][t]["ratio"]
               for s, t in pairs.items())
    assert records["L"]["vertices"] != records["R"]["vertices"]
    assert records["L"]["chain"] != records["R"]["chain"]
    assert fixtures.structure_snapshot(arm, mesh) == before_structure
    fixtures.assert_pose_snapshot(arm, before_pose, "Mirror preserves both hand poses")
    print("PASS geometric mirror range correspondence and complete failure rollback", flush=True)


def test_paired_remove_refusal_preserves_a_renamed_other_side():
    f = bilateral_fixture()
    mesh, arm = f["mesh"], f["armature"]
    runtime.start_test(bpy.context, mesh, symmetry=True)
    runtime.finish_test(bpy.context, confirm=True)
    records = runtime._records(mesh)
    left = runtime._managed_key(mesh, "L", records["L"])
    right = runtime._managed_key(mesh, "R", records["R"])
    original_keys = fixtures.key_snapshot(mesh)
    original_pose = fixtures.pose_snapshot(arm)
    original_structure = fixtures.structure_snapshot(arm, mesh)
    busy = runtime._BUSY
    runtime._BUSY = True
    try:
        left.name = "Artist Friendly Forearm L"
        dependent = mesh.shape_key_add(name="Artist derivative of right correction")
        dependent.relative_key = right
        before_json, before_keys = mesh[runtime.RECORD_KEY], keys(mesh)
        fixtures.assert_refused(lambda: runtime.remove_paired_calibration(bpy.context, mesh),
                                "A right-side artist dependency must refuse both removals")
        assert mesh[runtime.RECORD_KEY] == before_json
        assert keys(mesh) == before_keys, "Failed removal renamed or edited the left-side key during preflight"
        assert left.name == "Artist Friendly Forearm L"
        fixtures.assert_pose_snapshot(arm, original_pose, "Paired removal refusal")
        # Resolving that dependency permits removal, including the cached renamed
        # left key, without disturbing artist keys, weights or native rig data.
        mesh.shape_key_remove(dependent)
        runtime.remove_paired_calibration(bpy.context, mesh)
        assert runtime.RECORD_KEY not in mesh
        assert keys(mesh) == original_keys
        assert fixtures.structure_snapshot(arm, mesh) == original_structure
        fixtures.assert_pose_snapshot(arm, original_pose, "Successful paired removal after refusal")
    finally:
        runtime._BUSY = busy
    print("PASS paired Remove preflight preserves renamed opposite key on refusal", flush=True)


def test_paired_remove_name_collision_refuses_before_either_deletion():
    f = bilateral_fixture()
    mesh, arm = f["mesh"], f["armature"]
    runtime.start_test(bpy.context, mesh, symmetry=True)
    runtime.finish_test(bpy.context, confirm=True)
    records = runtime._records(mesh)
    left = runtime._managed_key(mesh, "L", records["L"])
    right = runtime._managed_key(mesh, "R", records["R"])
    original_keys = fixtures.key_snapshot(mesh)
    original_pose = fixtures.pose_snapshot(arm)
    original_structure = fixtures.structure_snapshot(arm, mesh)
    busy = runtime._BUSY
    runtime._BUSY = True
    try:
        # Cached identity still identifies R's owned output after a user rename.
        # A different artist key now occupies the name the remover would repair.
        left.name = "Artist Friendly Forearm L"
        right.name = "Artist Friendly Forearm R"
        occupant = mesh.shape_key_add(name=records["R"]["key"])
        occupant.value = .23
        occupant.data[0].co.x += .004
        before_json, before_keys = mesh[runtime.RECORD_KEY], keys(mesh)
        fixtures.assert_refused(lambda: runtime.remove_paired_calibration(bpy.context, mesh),
                                "Right name collision must refuse before removing left")
        assert mesh[runtime.RECORD_KEY] == before_json, "Name collision deleted the first side's record before refusal"
        assert keys(mesh) == before_keys, "Name collision deleted or renamed a key before refusal"
        assert left.name == "Artist Friendly Forearm L" and right.name == "Artist Friendly Forearm R"
        assert fixtures.structure_snapshot(arm, mesh) == original_structure
        fixtures.assert_pose_snapshot(arm, original_pose, "Paired removal collision refusal")
        # Give the artist key a free name, then allow both owned outputs to be
        # removed. The artist's colliding key must survive with its data intact.
        occupant.name = "Artist preserved forearm expression"
        expected = dict(original_keys)
        expected.update(fixtures.key_snapshot(mesh, names=(occupant.name,)))
        runtime.remove_paired_calibration(bpy.context, mesh)
        assert runtime.RECORD_KEY not in mesh and keys(mesh) == expected
        assert fixtures.structure_snapshot(arm, mesh) == original_structure
        fixtures.assert_pose_snapshot(arm, original_pose, "Paired removal after name collision resolved")
    finally:
        runtime._BUSY = busy
    print("PASS paired Remove name collision refuses before either deletion", flush=True)


if __name__ == "__main__":
    test_bounded_evaluated_deformation_and_idempotence()
    test_explicit_profile_edits_and_refusal_atomicity()
    test_cancel_reentry_and_save_reopen_keep_authored_capture()
    test_manual_capture_addition_preserves_indices_and_legacy_opt_in()
    test_overlay_matches_deformed_world_space_and_boundary_layering()
    test_mirror_ranges_uses_geometric_pairs_and_failure_rolls_back()
    test_paired_remove_refusal_preserves_a_renamed_other_side()
    test_paired_remove_name_collision_refuses_before_either_deletion()
    print("FOREARM_TWIST_RANGES_TESTS_PASS", flush=True)
