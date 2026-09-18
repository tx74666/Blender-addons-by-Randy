"""Real registered Unity animation operators, native undo, playback and draw RNA."""

import json
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
import character_designer
from character_designer import animation as ui, unity_animation as ua, forearm_twist as ft
from character_designer.ui_constants import UI_PAGE_ANIMATION
import test_unity_animation_blender as fixtures


class Layout:
    """Exercise real draw code against registered RNA; collect visible controls."""
    def __init__(self):
        self.buttons, self.fields, self.labels = [], [], []
    def box(self): return self
    def row(self, **kwargs): return self
    def column(self, **kwargs): return self
    def separator(self): pass
    def prop(self, data, name, **kwargs):
        assert name in data.bl_rna.properties, name
        self.fields.append(name)
    def label(self, **kwargs): self.labels.append(kwargs.get("text", ""))
    def operator(self, name, **kwargs):
        group, identifier = name.split(".")
        getattr(getattr(bpy.ops, group), identifier).get_rna_type()
        self.buttons.append(name)
        return SimpleNamespace()


def draw():
    layout = Layout()
    ui.CHARACTERDESIGNER_PT_animation.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout


def target():
    return bpy.data.objects["CoshaRig"]


def prepare(folder):
    fixtures.reset()
    rig = fixtures.make_rig()
    rig.pose.bones["Hips"].location = (.02, .01, -.03)
    rig.pose.bones["Hips"].keyframe_insert("location", frame=1)
    original = rig.animation_data.action
    original.name = "UI Original Action"
    alternate = original.slots.new(id_type="OBJECT", name="Unused Slot")
    assert alternate.handle != rig.animation_data.action_slot.handle
    nla = original.copy()
    nla.name = "UI Original NLA"
    track = rig.animation_data.nla_tracks.new()
    track.strips.new("Keep this strip", 1, nla)
    rig.animation_data.action_blend_type, rig.animation_data.action_influence = "ADD", .3
    ua._set_frame(bpy.context.scene, bpy.context.scene.frame_current_final)
    bpy.context.view_layer.update()
    package, _, data = fixtures.package(rig, folder, "Cosha_RealClip.cdanim.json")
    settings = bpy.context.window_manager.character_designer_animation
    settings.target, settings.unity_directory, settings.start_frame = rig, str(folder), 9
    bpy.context.window_manager.character_designer.ui_page = UI_PAGE_ANIMATION
    return ua._snapshot(bpy.context, rig), original.name, rig.animation_data.action_slot.handle


def assert_restored(snapshot, action_name, slot):
    rig = target()
    assert ua.active_preview(rig) is None
    assert rig.animation_data.action.name == action_name
    assert rig.animation_data.action_slot.handle == slot
    actual = ua._snapshot(bpy.context, rig)
    assert actual == snapshot, {k: (snapshot[k], actual[k]) for k in snapshot if actual[k] != snapshot[k]}


def import_latest():
    assert bpy.ops.character_designer.animation_unity_import.poll()
    assert bpy.ops.character_designer.animation_unity_import(use_latest=True) == {"FINISHED"}
    rig = target()
    assert ua.active_preview(rig) is rig.animation_data.action
    assert not rig.animation_data.use_nla
    assert bpy.context.scene.tool_settings.use_keyframe_insert_auto
    assert not bpy.ops.character_designer.animation_unity_import.poll()
    return rig.animation_data.action.name


def assert_forearm_warning_draw_is_read_only():
    errors = dict(ft._ERRORS)
    objects, mesh_data = [], []
    try:
        for name in ("Bound Warning Fixture", "Unrelated Warning Fixture"):
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata([(0, 0, 0)], [], [])
            obj = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            objects.append(obj)
            mesh_data.append(mesh)
        bound, unrelated = objects
        bound.modifiers.new("Existing Armature Binding", "ARMATURE").object = target()
        # Draw must only report the existing diagnostic, not try to repair or
        # regenerate calibration records, weights, geometry or the preview.
        record = json.dumps({"fixture": "confirmed calibration must remain unchanged"})
        bound[ft.RECORD_KEY] = record
        bound.vertex_groups.new(name="hand.L").add([0], .75, "REPLACE")
        ft._ERRORS[bound.name] = "Calibration topology changed."
        ft._ERRORS[unrelated.name] = "Unrelated calibration error."
        expected_errors = dict(ft._ERRORS)
        preview = ua.active_preview(target())
        drawn = draw()
        assert f"{bound.name}: Forearm Correction" in drawn.labels
        assert "Calibration topology changed." in drawn.labels
        assert f"{unrelated.name}: Forearm Correction" not in drawn.labels
        assert "Unrelated calibration error." not in drawn.labels
        assert bound[ft.RECORD_KEY] == record
        assert tuple(bound.data.vertices[0].co) == (0, 0, 0)
        assert bound.vertex_groups["hand.L"].weight(0) == .75
        assert ft._ERRORS == expected_errors
        assert ua.active_preview(target()) is preview
    finally:
        ft._ERRORS.clear()
        ft._ERRORS.update(errors)
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in mesh_data:
            bpy.data.meshes.remove(mesh)


def test_operators_draw_restore_and_cancel(folder):
    snapshot, original, slot = prepare(folder)
    assert ui.CHARACTERDESIGNER_PT_animation.poll(bpy.context)
    first = draw()
    assert "character_designer.animation_unity_import" in first.buttons
    assert "character_designer.animation_generate" not in first.buttons
    imported = import_latest()
    preview = draw()
    assert {"frame_current", "frame_subframe"} <= set(preview.fields)
    assert "character_designer.animation_play_pause" in preview.buttons
    assert "character_designer.animation_restore" in preview.buttons
    assert "character_designer.animation_unity_cancel" in preview.buttons
    assert_forearm_warning_draw_is_read_only()
    bpy.context.window_manager.character_designer_animation.show_local_motion = True
    expanded = draw()
    assert expanded.buttons.count("character_designer.animation_restore") == 1
    bpy.context.window_manager.character_designer_animation.show_local_motion = False
    ua._set_frame(bpy.context.scene, 14.25)
    assert abs(ua.preview_time_seconds(bpy.context, target()) - 5.25 / (24 / 1.001)) < 1e-6
    assert bpy.ops.character_designer.animation_restore() == {"FINISHED"}
    assert_restored(snapshot, original, slot)
    assert imported in bpy.data.actions
    second = import_latest()
    assert imported != second
    ua._set_frame(bpy.context.scene, 23.75)
    assert bpy.ops.character_designer.animation_unity_cancel() == {"FINISHED"}
    assert_restored(snapshot, original, slot)
    assert second in bpy.data.actions
    bpy.context.window_manager.character_designer_animation.show_local_motion = True
    legacy = draw()
    assert "character_designer.animation_generate" in legacy.buttons
    assert "character_designer.animation_import" in legacy.buttons
    bpy.context.window_manager.character_designer_animation.show_local_motion = False


def test_native_undo_redo(folder):
    snapshot, original, slot = prepare(folder)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Before Unity animation test")
    imported = import_latest()
    # Python-driven operator batches require an explicit terminal undo boundary.
    # The entrypoint itself still has REGISTER/UNDO and executes its real code.
    bpy.ops.ed.undo_push(message="Unity animation test applied")
    assert bpy.ops.ed.undo.poll(), "Native undo requires an interactive Blender event loop"
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert_restored(snapshot, original, slot)
    assert bpy.ops.ed.redo() == {"FINISHED"}
    assert ua.active_preview(target()) is target().animation_data.action
    assert target().animation_data.action.name == imported
    assert bpy.ops.character_designer.animation_unity_cancel() == {"FINISHED"}
    assert_restored(snapshot, original, slot)


def test_play_pause_and_cancel_playing(folder):
    snapshot, original, slot = prepare(folder)
    import_latest()
    assert bpy.context.screen is not None
    assert bpy.ops.character_designer.animation_play_pause.poll()
    assert bpy.ops.character_designer.animation_play_pause() == {"FINISHED"}
    assert bpy.context.screen.is_animation_playing
    assert bpy.ops.character_designer.animation_play_pause() == {"FINISHED"}
    assert not bpy.context.screen.is_animation_playing
    assert bpy.ops.character_designer.animation_play_pause() == {"FINISHED"}
    assert bpy.context.screen.is_animation_playing
    assert bpy.ops.character_designer.animation_unity_cancel() == {"FINISHED"}
    assert not bpy.context.screen.is_animation_playing
    assert_restored(snapshot, original, slot)
    # Starting a preview while the original Action was playing pauses the test;
    # restoring resumes that previous playback state.
    assert bpy.ops.character_designer.animation_play_pause() == {"FINISHED"}
    playing_snapshot = ua._snapshot(bpy.context, target())
    import_latest()
    assert not bpy.context.screen.is_animation_playing
    assert bpy.ops.character_designer.animation_restore() == {"FINISHED"}
    assert_restored(playing_snapshot, original, slot)
    assert bpy.context.screen.is_animation_playing
    bpy.ops.screen.animation_cancel(restore_frame=False)


def test_real_walk_data_recovery(folder, blend_path, motion_path):
    blend_path, motion_path = Path(blend_path), Path(motion_path)
    before_file = hashlib.sha256(blend_path.read_bytes()).hexdigest()
    before_package = hashlib.sha256(motion_path.read_bytes()).hexdigest()
    bpy.ops.wm.open_mainfile(filepath=str(blend_path), use_scripts=False, load_ui=False)
    rig = target()
    original_data, original_name = rig.data, rig.data.name
    original_rest = fixtures.rest_hash(rig)
    connects = {b.name: b.use_connect for b in rig.data.bones}
    snapshot = ua._snapshot(bpy.context, rig)
    before_objects = set(bpy.data.objects.keys())
    data = ua.load_package(motion_path)
    mapping = ua._mapping(rig, data, bpy.context.scene.unit_settings.scale_length)
    assert set(mapping.indices) == set(rig.data.bones.keys())
    assert set(b['name'] for b in data['bones']) - set(mapping.indices) == {"CoshaRig"}
    settings = bpy.context.window_manager.character_designer_animation
    settings.target, settings.start_frame = rig, 1
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Real Walk before import")
    assert bpy.ops.character_designer.animation_unity_import(use_latest=False, filepath=str(motion_path)) == {"FINISHED"}
    preview = ua.active_preview(rig)
    assert rig.data is not original_data and preview[ua.ORIGINAL_DATA_KEY] is original_data
    preview_data_name = rig.data.name
    assert {b.name: b.use_connect for b in original_data.bones} == connects
    assert fixtures.rest_hash(rig) == original_rest
    assert set(bpy.data.objects.keys()) == before_objects
    for index in (0, 28, 43, 58):
        ua._set_frame(bpy.context.scene, 1 + data['frames'][index]['time'] * snapshot['fps'] / snapshot['fps_base'])
        bpy.context.view_layer.update()
        expected = ua.expected_world_matrices(rig, data, index, mapping=mapping)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        assert max(fixtures.max_matrix_error(rig.matrix_world @ evaluated.pose.bones[name].matrix, mat)
                   for name, mat in expected.items()) < 2e-5
    bpy.ops.ed.undo_push(message="Real Walk imported and scrubbed")
    assert bpy.ops.ed.undo() == {"FINISHED"}
    rig = target()
    assert rig.data.name == original_name and ua.active_preview(rig) is None
    assert {b.name: b.use_connect for b in rig.data.bones} == connects
    assert ua._snapshot(bpy.context, rig) == snapshot
    assert bpy.ops.ed.redo() == {"FINISHED"}
    rig = target()
    assert ua.active_preview(rig) and rig.data.name == preview_data_name
    saved = Path(folder) / "real_walk_preview_recovery.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved), use_scripts=False, load_ui=False)
    rig = target()
    bpy.context.window_manager.character_designer_animation.target = rig
    assert ua.active_preview(rig) and rig.data.name == preview_data_name
    assert bpy.ops.character_designer.animation_unity_cancel() == {"FINISHED"}
    assert rig.data is bpy.data.armatures[original_name]
    assert bpy.data.armatures.get(preview_data_name) is None
    assert {b.name: b.use_connect for b in rig.data.bones} == connects
    assert fixtures.rest_hash(rig) == original_rest
    assert ua._snapshot(bpy.context, rig) == snapshot
    assert set(bpy.data.objects.keys()) == before_objects
    assert hashlib.sha256(blend_path.read_bytes()).hexdigest() == before_file
    assert hashlib.sha256(motion_path.read_bytes()).hexdigest() == before_package
    print("PASS real Walk", len(mapping.indices), "matched bones", len(data['frames']),
          "samples; data-copy native Undo/Redo + save/reopen/cancel; originals unchanged")


def main():
    character_designer.register()
    character_designer.register()
    character_designer._validate_registration_integrity()
    assert bpy.app.handlers.load_pre.count(ui._animation_load_pre) == 1
    try:
        with tempfile.TemporaryDirectory(prefix="cdesigner-unity-animation-ui-") as folder:
            if "--real-blend" in sys.argv and "--motion" in sys.argv:
                test_real_walk_data_recovery(folder, sys.argv[sys.argv.index("--real-blend") + 1],
                                            sys.argv[sys.argv.index("--motion") + 1])
            else:
                for test in (test_operators_draw_restore_and_cancel, test_native_undo_redo,
                             test_play_pause_and_cancel_playing):
                    test(folder)
                    print("PASS", test.__name__)
        character_designer._validate_registration_integrity()
    finally:
        if bpy.context.screen and bpy.context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        character_designer.unregister()
    assert ui._animation_load_pre not in bpy.app.handlers.load_pre
    print("UNITY_ANIMATION_UI_TESTS_PASSED")


if __name__ == "__main__":
    main()
    if "--quit-after-test" in sys.argv:
        bpy.ops.wm.quit_blender()
