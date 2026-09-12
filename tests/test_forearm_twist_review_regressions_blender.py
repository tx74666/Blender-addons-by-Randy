"""Focused review fixes: picker guards, legacy support, manual sampling, UI RNA."""
import copy
import os
import sys
from types import SimpleNamespace

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "addons"), os.path.join(ROOT, "tests")]
from character_designer import forearm_twist as runtime, forearm_twist_edit as editor
from character_designer import forearm_twist_profile as profile, forearm_twist_topology as topology
import test_forearm_twist_blender as fixtures


def event(kind, **values):
    return SimpleNamespace(type=kind, value="PRESS", ctrl=False, shift=False, **values)


def test_picker_does_not_bypass_destructive_or_frame_guards():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    mesh, arm = f["mesh"], f["armature"]
    pose, keys = fixtures.pose_snapshot(arm), fixtures.key_snapshot(mesh)
    for kind in ("G", "R", "S", "X", "DEL", "TAB", "F3", "FRAME"):
        runtime.start_test(bpy.context, mesh)
        runtime._SESSION["picking"] = True
        timer = bpy.context.window_manager.event_timer_add(.2, window=bpy.context.window)
        owner = SimpleNamespace(_timer=timer, report=lambda *_args: None)
        if kind == "FRAME":
            bpy.context.scene.frame_current += 1
        result = runtime.CHARACTERDESIGNER_OT_forearm_twist_start.modal(owner, bpy.context,
                                                                     event("TIMER" if kind == "FRAME" else kind))
        assert result == {"CANCELLED"}, (kind, result)
        assert runtime._SESSION is None and runtime.PREVIEW_KEY not in mesh
        assert fixtures.key_snapshot(mesh) == keys
        fixtures.assert_pose_snapshot(arm, pose, "Picker guard " + kind)


def test_escape_cancels_only_picker_and_ctrl_z_uses_session_history():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    mesh = f["mesh"]
    runtime.start_test(bpy.context, mesh)
    before = fixtures.record_for(mesh)
    runtime.set_ratio(bpy.context, 3, .83)
    runtime._SESSION["picking"] = True
    timer = bpy.context.window_manager.event_timer_add(.2, window=bpy.context.window)
    owner = SimpleNamespace(_timer=timer, report=lambda *_args: None)
    try:
        assert runtime.CHARACTERDESIGNER_OT_forearm_twist_start.modal(owner, bpy.context, event("ESC")) == {"PASS_THROUGH"}
        assert editor.CHARACTERDESIGNER_OT_forearm_loop_pick.modal(owner, bpy.context, event("ESC")) == {"CANCELLED"}
        assert runtime._SESSION is not None and not runtime._SESSION.get("picking")
        runtime._SESSION["picking"] = True
        undo = SimpleNamespace(type="Z", value="PRESS", ctrl=True, shift=False)
        assert runtime.CHARACTERDESIGNER_OT_forearm_twist_start.modal(owner, bpy.context, undo) == {"RUNNING_MODAL"}
        assert runtime._SESSION is not None and not runtime._SESSION.get("picking")
        assert fixtures.record_for(mesh) == before
    finally:
        runtime.finish_test(bpy.context, confirm=False)
        bpy.context.window_manager.event_timer_remove(timer)


def test_rna_noop_commits_preserve_one_step_operator_undo_and_redo():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    mesh = f["mesh"]
    runtime.start_test(bpy.context, mesh)
    settings = bpy.context.window_manager.character_designer_forearm_twist
    try:
        settings.ring_index = 2
        before = runtime._records(mesh)
        assert bpy.ops.character_designer.forearm_loop_edit(action="START") == {"FINISHED"}
        changed = runtime._records(mesh)
        assert changed["L"]["range_start"] == 1
        assert len(runtime._SESSION["history"]) == 1
        # These are real RNA update callbacks, including Blender's behavior
        # of committing an unchanged field value when UI focus moves.
        settings.range_start = settings.range_start
        settings.range_end = settings.range_end
        assert bpy.ops.character_designer.forearm_loop_edit(action="START") == {"FINISHED"}
        assert len(runtime._SESSION["history"]) == 1
        assert bpy.ops.character_designer.forearm_loop_edit(action="UNDO") == {"FINISHED"}
        assert runtime._records(mesh) == before
        assert settings.range_start == 1
        redo = copy.deepcopy(runtime._SESSION["redo"])
        settings.range_start = settings.range_start
        settings.range_end = settings.range_end
        assert runtime._SESSION["redo"] == redo
        assert not runtime._SESSION["history"]
        assert bpy.ops.character_designer.forearm_loop_edit(action="REDO") == {"FINISHED"}
        assert runtime._records(mesh) == changed and settings.range_start == 2
        # Sessions opened before the fix may already contain duplicates.
        runtime._SESSION["history"].extend([copy.deepcopy(changed), copy.deepcopy(changed)])
        assert bpy.ops.character_designer.forearm_loop_edit(action="UNDO") == {"FINISHED"}
        assert runtime._records(mesh) == before
        runtime._SESSION["redo"].extend([copy.deepcopy(before), copy.deepcopy(before)])
        assert bpy.ops.character_designer.forearm_loop_edit(action="REDO") == {"FINISHED"}
        assert runtime._records(mesh) == changed
        # A stack containing only duplicates is emptied without adding a fake
        # state to its counterpart, and never changes the current profile.
        runtime._SESSION["history"] = [copy.deepcopy(changed)]
        destination = copy.deepcopy(runtime._SESSION["redo"])
        assert bpy.ops.character_designer.forearm_loop_edit(action="UNDO") == {"FINISHED"}
        assert runtime._records(mesh) == changed
        assert not runtime._SESSION["history"] and runtime._SESSION["redo"] == destination
    finally:
        runtime.finish_test(bpy.context, confirm=False)


def test_legacy_rest_roll_migration_keeps_old_palm_support():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    mesh, arm = f["mesh"], f["armature"]
    runtime.start_test(bpy.context, mesh)
    runtime.finish_test(bpy.context, confirm=True)
    record = fixtures.record_for(mesh)
    for name in ("range_start", "range_end", "current_ring", "curve_strength", "transition"):
        record.pop(name, None)
    palm = f["guard_rings"][1]
    mesh.vertex_groups[f["lower_name"]].add(palm, .25, "REPLACE")
    mesh.vertex_groups[f["hand_name"]].add(palm, .75, "REPLACE")
    record["vertices"] += palm
    record["positions"] += [1.12] * len(palm)
    record["paired"] = False
    old = copy.deepcopy(record)
    runtime._write_records(mesh, {"L": record})
    busy = runtime._BUSY
    runtime._BUSY = True
    try:
        fixtures.activate_mesh(arm)
        bpy.ops.object.mode_set(mode="EDIT")
        arm.data.edit_bones[f["lower_name"]].roll += .17
        bpy.ops.object.mode_set(mode="OBJECT")
        fixtures.activate_mesh(mesh)
        _arm, rig = runtime._resolve_rig(mesh, "L")
        fresh = runtime._current_record(mesh, arm, rig, "L", copy.deepcopy(record), {"L": record})
    finally:
        runtime._BUSY = busy
    assert fresh["rest"] != old["rest"]
    assert fresh["vertices"] == old["vertices"] and fresh["positions"] == old["positions"]
    assert fresh["rings"] == old["rings"]
    assert not any(name in fresh for name in ("range_start", "range_end", "current_ring"))
    assert set(palm) <= set(fresh["vertices"])


def test_manual_added_loop_samples_existing_smooth_profile():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    mesh, arm = f["mesh"], f["armature"]
    fixtures.pose_target(f, 22.)
    captured = [topology.capture_loop(mesh, arm, f["lower_name"], ids)
                for index, ids in enumerate(f["rings"]) if index not in (3, 4)]
    runtime.start_test(bpy.context, mesh, rings_override=captured)
    runtime.set_ratio(bpy.context, 2, .1)
    runtime.set_ratio(bpy.context, 3, .9)
    runtime.finish_test(bpy.context, confirm=True)
    before = fixtures.record_for(mesh)
    points = fixtures.evaluated_points(mesh)
    added = topology.capture_loop(mesh, arm, f["lower_name"], f["rings"][3])
    expected = runtime.profile_ratio(added["position"], profile.profile_knots(before["rings"]))
    assert abs(expected - profile.interpolate_ratio(before["rings"], added["position"])) > .01
    editor.manual_add_loop(bpy.context, mesh, "L", added)
    after = fixtures.record_for(mesh)
    inserted = next(ring for ring in after["rings"] if set(ring["vertices"]) == set(added["vertices"]))
    assert abs(inserted["ratio"] - expected) < 1e-8
    previous = {frozenset(ring["vertices"]): ring for ring in before["rings"]}
    assert all(ring == previous[frozenset(ring["vertices"])] for ring in after["rings"]
               if frozenset(ring["vertices"]) in previous)
    actual = fixtures.evaluated_points(mesh)
    fixtures.assert_points_close([points[index] for index in added["vertices"]],
                                 [actual[index] for index in added["vertices"]],
                                 "Manual addition preserves the inserted loop's sampled deformation", tolerance=4e-6)


class OperatorValues:
    def __init__(self, identifier):
        namespace, name = identifier.split(".")
        object.__setattr__(self, "rna", getattr(getattr(bpy.ops, namespace), name).get_rna_type())
    def __setattr__(self, key, value):
        prop = self.rna.properties[key]
        if prop.type == "ENUM":
            assert value in prop.enum_items.keys(), (key, value)


class RNALayout:
    enabled = True
    def _check(self, name, kwargs):
        parameters = bpy.types.UILayout.bl_rna.functions[name].parameters
        for key, value in kwargs.items():
            prop = parameters[key]
            if prop.type == "ENUM" and prop.enum_items:
                assert value in prop.enum_items.keys(), (name, key, value)
    def row(self, **kwargs):
        self._check("row", kwargs)
        return RNALayout()
    def column(self, **kwargs):
        self._check("column", kwargs)
        return RNALayout()
    def box(self, **kwargs):
        self._check("box", kwargs)
        return RNALayout()
    def label(self, **kwargs):
        self._check("label", kwargs)
    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties.keys(), name
        self._check("prop", kwargs)
    def operator(self, identifier, **kwargs):
        self._check("operator", kwargs)
        return OperatorValues(identifier)


def test_saved_current_validation_and_twenty_draw_rna_variants():
    f = fixtures.make_fixture("ROLL_DECOUPLED", build_ik=False)
    runtime.start_test(bpy.context, f["mesh"])
    runtime.set_range(bpy.context, 2, 5)
    settings = bpy.context.window_manager.character_designer_forearm_twist
    try:
        record = fixtures.record_for(f["mesh"])
        for invalid in (-1, len(record["rings"]), 2.0, True, None):
            damaged = {**record, "current_ring": invalid}
            try:
                editor.bounded_record(damaged)
            except ValueError as exc:
                assert "current loop" in str(exc)
            else:
                raise AssertionError("Malformed saved current loop was accepted")
        for locked in (False, True):
            runtime._SESSION["pose_locked"] = locked
            for expanded in (False, True):
                settings.show_batch = expanded
                for current in (0, 2, 3, 5, 7):
                    editor.set_current(bpy.context, current)
                    editor.draw_session(RNALayout(), bpy.context, fixtures.record_for(f["mesh"]))
    finally:
        runtime._SESSION["pose_locked"] = False
        runtime.finish_test(bpy.context, confirm=False)


if __name__ == "__main__":
    tests = [value for name, value in globals().copy().items() if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__, flush=True)
    print("FOREARM_REVIEW_REGRESSIONS_PASS", len(tests), flush=True)
