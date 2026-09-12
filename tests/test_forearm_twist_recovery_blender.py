"""Stale forearm calibration can be disabled, inspected and restored safely."""
import copy
import os
import sys
from types import SimpleNamespace

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "addons"), os.path.join(ROOT, "tests")]
from character_designer import forearm_twist as runtime
import test_forearm_twist_blender as fixtures


class Layout:
    def __init__(self, buttons=None, labels=None):
        self.buttons = [] if buttons is None else buttons
        self.labels = [] if labels is None else labels

    def row(self, **_kwargs):
        return Layout(self.buttons, self.labels)

    box = row

    def prop(self, *_args, **_kwargs):
        pass

    def label(self, **kwargs):
        self.labels.append(kwargs.get("text", ""))

    def operator(self, identifier, **kwargs):
        self.buttons.append((identifier, kwargs))
        return SimpleNamespace()


def panel_state():
    layout = Layout()
    runtime.CHARACTERDESIGNER_PT_forearm_twist.draw(SimpleNamespace(layout=layout), bpy.context)
    assert "Both Arms" not in layout.labels
    return next((kwargs["text"] for identifier, kwargs in layout.buttons
                 if identifier == "character_designer.forearm_twist_toggle"), None)


def calibrated_fixture(side="L"):
    fixture = fixtures.make_fixture(fixtures.BUILD_METHODS[0], side=side, build_ik=False)
    runtime.start_test(bpy.context, fixture["mesh"], side)
    runtime.set_ratio(bpy.context, 3, .34)
    runtime.finish_test(bpy.context, confirm=True)
    fixtures.pose_target(fixture, 45.)
    runtime.update_runtime(bpy.context.scene)
    fixtures.assert_runtime_geometry(fixture, "Calibrated recovery fixture")
    return fixture


def test_stale_calibration_disable_and_reenable_guard():
    for side in ("L", "R"):
        for stale_field in ("topology", "chain", "armature"):
            fixture = calibrated_fixture(side)
            mesh, armature = fixture["mesh"], fixture["armature"]
            valid = runtime._records(mesh)
            stale = copy.deepcopy(valid)
            if stale_field == "chain":
                stale[side]["chain"][1] = "previous_forearm_name." + side
            else:
                stale[side][stale_field] = "previous_" + stale_field
            runtime._write_records(mesh, stale)
            runtime.update_runtime(bpy.context.scene)
            assert "Mesh topology or rig chain changed" in runtime._ERRORS[mesh.name]
            assert panel_state() == "Paused"
            before_pose = fixtures.pose_snapshot(armature)
            before_structure = fixtures.structure_snapshot(armature, mesh)
            before_keys = fixtures.key_snapshot(mesh)
            key = mesh.data.shape_keys.key_blocks[stale[side]["key"]]
            coordinates = tuple(tuple(point.co) for point in key.data)
            value = key.value
            # Simulate a stale output still visible before the graph can mute it.
            key.mute = False
            assert bpy.ops.character_designer.forearm_twist_toggle() == {"FINISHED"}
            disabled = copy.deepcopy(stale)
            disabled[side]["enabled"] = False
            assert runtime._records(mesh) == disabled
            assert key.mute and key.value == value
            assert tuple(tuple(point.co) for point in key.data) == coordinates
            runtime.update_runtime(bpy.context.scene)
            assert key.mute and panel_state() == "Disabled"
            assert not bpy.context.scene.render.use_lock_interface
            assert runtime.controller_status(armature, fixture["target"].name) == (
                False, "Forearm Twist disabled: original skinning")
            fixtures.assert_pose_snapshot(armature, before_pose, "Disable stale calibration")
            assert fixtures.structure_snapshot(armature, mesh) == before_structure
            assert fixtures.key_snapshot(mesh) == before_keys
            disabled_json = mesh[runtime.RECORD_KEY]
            fixtures.assert_refused(lambda: runtime.toggle_paired_calibration(bpy.context, mesh),
                                    "Enabling stale calibration")
            assert mesh[runtime.RECORD_KEY] == disabled_json and key.mute
            # Restoring the matching rig/mesh signature makes enabling reversible.
            valid[side]["enabled"] = False
            runtime._write_records(mesh, valid)
            runtime.toggle_paired_calibration(bpy.context, mesh)
            fixtures.assert_runtime_geometry(fixture, "Reenabled repaired calibration")
            assert panel_state() == "Enabled"
            runtime.remove_paired_calibration(bpy.context, mesh)
            assert runtime.RECORD_KEY not in mesh and panel_state() is None
            print("PASS stale disable, enable guard and recovery", side, stale_field, flush=True)


def test_disable_resolves_both_owned_keys_before_mutation():
    fixture = calibrated_fixture()
    mesh = fixture["mesh"]
    records = runtime._records(mesh)
    # A paired saved record whose second managed key was lost must not disable
    # only the first side or adopt an unrelated renamed key.
    records["R"] = copy.deepcopy(records["L"])
    records["R"]["key"] = runtime.KEY_PREFIX + "R"
    runtime._write_records(mesh, records)
    key = mesh.data.shape_keys.key_blocks[records["L"]["key"]]
    before_json, before_mute = mesh[runtime.RECORD_KEY], key.mute
    fixtures.assert_refused(lambda: runtime.toggle_paired_calibration(bpy.context, mesh),
                            "Missing second owned corrective")
    assert mesh[runtime.RECORD_KEY] == before_json and key.mute == before_mute
    print("PASS paired ownership refusal is atomic", flush=True)


def test_disable_undo_redo_preserves_stale_profile():
    fixture = calibrated_fixture()
    mesh, armature = fixture["mesh"], fixture["armature"]
    records = runtime._records(mesh)
    records["L"]["topology"] = "previous_topology"
    runtime._write_records(mesh, records)
    runtime.update_runtime(bpy.context.scene)
    name, armature_name = mesh.name, armature.name
    before_pose = fixtures.pose_snapshot(armature)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Before stale calibration disable")
    assert bpy.ops.character_designer.forearm_twist_toggle() == {"FINISHED"}
    bpy.ops.ed.undo_push(message="After stale calibration disable")
    assert bpy.ops.ed.undo() == {"FINISHED"}
    mesh = bpy.data.objects[name]
    assert runtime._records(mesh) == records
    runtime.update_runtime(bpy.context.scene)
    assert panel_state() == "Paused"
    assert bpy.ops.ed.redo() == {"FINISHED"}
    mesh = bpy.data.objects[name]
    records["L"]["enabled"] = False
    assert runtime._records(mesh) == records
    assert mesh.data.shape_keys.key_blocks[records["L"]["key"]].mute
    assert panel_state() == "Disabled"
    fixtures.assert_pose_snapshot(bpy.data.objects[armature_name], before_pose, "Disable Undo/Redo")
    print("PASS stale calibration disable Undo/Redo", flush=True)


if __name__ == "__main__":
    test_stale_calibration_disable_and_reenable_guard()
    test_disable_resolves_both_owned_keys_before_mutation()
    test_disable_undo_redo_preserves_stale_profile()
    print("FOREARM_TWIST_RECOVERY_TESTS_PASS", flush=True)
