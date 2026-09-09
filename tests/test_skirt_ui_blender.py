"""Clothing operators, selected-source ownership, rollback, and bake cancellation.

Run in factory-startup background Blender; no production scene is opened.
"""

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import character_designer
from character_designer import character_setup, skirt, skirt_rig


def activate(obj):
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for candidate in bpy.context.selected_objects:
        candidate.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def make_skirt(name):
    sides, rows = 16, 6
    vertices = []
    for ring in range(rows):
        fraction = ring / (rows - 1)
        radius = 0.4 + 0.4 * fraction
        for column in range(sides):
            angle = math.tau * column / sides
            vertices.append((radius * math.cos(angle), radius * math.sin(angle),
                             1.5 - fraction))
    faces = [(row * sides + col, row * sides + (col + 1) % sides,
              (row + 1) * sides + (col + 1) % sides, (row + 1) * sides + col)
             for row in range(rows - 1) for col in range(sides)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def cancelled(callback):
    try:
        result = callback()
    except RuntimeError:
        # Blender promotes ERROR reports to RuntimeError for bpy.ops calls.
        return
    assert result == {"CANCELLED"}, result


def test_modal_cleanup():
    closed = []

    def steps():
        try:
            yield (1, 3, "Frame 1 / 3")
            yield (2, 3, "Frame 2 / 3")
        finally:
            closed.append(True)

    operator = skirt._SkirtBakeOperator()
    operator.report = lambda *_args: None
    operator._wm = bpy.context.window_manager
    operator._steps = steps()
    operator._start, operator._end = 1, 3
    operator._wm.progress_begin(0, 1)
    skirt._ACTIVE_BAKES[operator._wm.as_pointer()] = operator
    assert not skirt._idle(bpy.context)
    assert operator._advance(bpy.context) is None
    assert skirt._settings(bpy.context).last_message == "Frame 1 / 3"
    assert operator.modal(bpy.context, SimpleNamespace(type="ESC")) == {"CANCELLED"}
    assert closed == [True] and skirt._idle(bpy.context)
    assert operator._steps is None and operator._wm is None


def test_baked_preview(source, record):
    collection = bpy.data.collections.new("UI Test Baked Copy")
    bpy.context.scene.collection.children.link(collection)
    mesh = bpy.data.objects.new("UI Test Baked Mesh", source.data.copy())
    rig = bpy.data.objects.new("UI Test Baked Rig", bpy.data.armatures.new("UI Test Baked Bones"))
    collection.objects.link(mesh)
    collection.objects.link(rig)
    collection.hide_viewport = True
    collection.hide_render = True
    wire = bpy.data.objects[record["cage"][0]]
    wire.hide_set(True)
    before = {obj.name: obj.hide_get() for obj in bpy.context.view_layer.objects}
    skirt._remember_bake(source, {"rig": rig.name, "mesh": mesh.name, "collection": collection.name})
    assert bpy.ops.character_designer.skirt_preview_bake() == {"FINISHED"}
    assert source.hide_get() and not collection.hide_viewport
    assert collection.hide_render, "Viewport inspection changed render visibility"
    assert skirt._source(bpy.context) == source, "Baked selection lost its editable source"
    assert bpy.context.active_object == mesh
    assert bpy.ops.character_designer.skirt_select_controls() == {"FINISHED"}
    assert skirt.PREVIEW_KEY not in source
    assert collection.hide_viewport and collection.hide_render
    assert all(obj.hide_get() == before[obj.name] for obj in bpy.context.view_layer.objects)


def test_ui_workflow():
    settings = bpy.context.window_manager.character_designer_skirt
    assert settings.chain_count == 8 and settings.segment_count == 4
    assert not settings.physics and settings.use_scene_range
    settings.physics = False
    source = make_skirt("Skirt UI Source")
    activate(source)
    assert bpy.ops.character_designer.create_skirt_setup() == {"FINISHED"}
    record = skirt_rig.read_record(source)
    assert record["chain_count"] == 8 and record["segment_count"] == 4
    assert bpy.context.mode == "POSE" and skirt._source(bpy.context) == source
    assert bpy.ops.character_designer.skirt_toggle_helpers(kind="WIRE") == {"FINISHED"}
    assert not skirt._helpers_visible(record, "WIRE", bpy.context)
    assert bpy.ops.character_designer.skirt_toggle_helpers(kind="WIRE") == {"FINISHED"}
    assert skirt._helpers_visible(record, "WIRE", bpy.context)

    test_baked_preview(source, record)

    object_count = len(bpy.data.objects)
    assert bpy.ops.character_designer.create_skirt_setup() == {"FINISHED"}
    assert len(bpy.data.objects) == object_count, "Repeated primary action duplicated the rig"

    original_physics = skirt._physics

    def fail_physics(*_args):
        raise ValueError("Synthetic physics failure")

    skirt._physics = lambda: SimpleNamespace(add_physics=fail_physics)
    try:
        settings.physics = True
        cancelled(lambda: bpy.ops.character_designer.create_skirt_setup())
        assert skirt_rig.read_record(source)["owner"] == record["owner"]
        assert len(bpy.data.objects) == object_count, "Physics failure removed the existing setup"
        fresh = make_skirt("Another Skirt")
        activate(fresh)
        assert skirt._source(bpy.context) == fresh, "Stale UI pointer overrode the selected mesh"
        before = len(bpy.data.objects)
        cancelled(lambda: bpy.ops.character_designer.create_skirt_setup())
        assert skirt_rig.read_record(fresh) is None and len(bpy.data.objects) == before
        assert fresh.parent is None and not fresh.modifiers and not fresh.vertex_groups
    finally:
        skirt._physics = original_physics
        settings.physics = False

    activate(source)
    assert bpy.ops.character_designer.skirt_select_controls() == {"FINISHED"}
    assert skirt._source(bpy.context) == source
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 3, 9
    assert skirt._bake_range(bpy.context) == (3, 9)
    settings.use_scene_range = False
    settings.bake_start, settings.bake_end = 9, 3
    try:
        skirt._bake_range(bpy.context)
    except ValueError:
        pass
    else:
        raise AssertionError("Reversed bake range was accepted")
    settings.use_scene_range = True
    test_modal_cleanup()
    assert bpy.ops.character_designer.remove_skirt_setup() == {"FINISHED"}
    assert skirt_rig.read_record(source) is None and source.parent is None
    assert not source.modifiers and not source.vertex_groups


def test_physics_bake_operators():
    settings = bpy.context.window_manager.character_designer_skirt
    settings.physics = True
    settings.chain_count, settings.segment_count = 4, 2
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 3
    bpy.context.scene.frame_set(2)
    source = make_skirt("UI Physics Source")
    activate(source)
    assert bpy.ops.character_designer.create_skirt_setup() == {"FINISHED"}
    record = skirt_rig.read_record(source)
    assert record["physics"]["colliders"]
    assert bpy.ops.character_designer.skirt_bake_physics() == {"FINISHED"}
    assert skirt_rig.read_record(source)["physics"]["baked_range"] == [1, 3]
    assert bpy.context.scene.frame_current == 2 and bpy.context.mode == "POSE"
    assert bpy.ops.character_designer.skirt_clear_cache() == {"FINISHED"}
    assert skirt_rig.read_record(source)["physics"]["baked_range"] is None
    assert bpy.ops.character_designer.skirt_bake_animation() == {"FINISHED"}
    baked = skirt._last_bake(source)
    assert baked and bpy.data.objects[baked["rig"]].animation_data.action
    assert bpy.ops.character_designer.skirt_preview_bake() == {"FINISHED"}
    assert skirt._source(bpy.context) == source
    assert bpy.ops.character_designer.skirt_select_controls() == {"FINISHED"}
    live_rig = source[skirt_rig.RIG_KEY]
    live_rig.keyframe_insert(data_path="location", frame=2)
    cancelled(lambda: bpy.ops.character_designer.remove_skirt_setup())
    assert skirt_rig.read_record(source)["owner"] == record["owner"]

    confirmation = {}

    def confirm(_operator, _event, **kwargs):
        confirmation.update(kwargs)
        return {"RUNNING_MODAL"}

    operator = SimpleNamespace(authorize_animated_removal=False)
    context = SimpleNamespace(window_manager=SimpleNamespace(invoke_confirm=confirm),
                              view_layer=bpy.context.view_layer,
                              selected_objects=bpy.context.selected_objects)
    result = skirt.CHARACTERDESIGNER_OT_remove_skirt_setup.invoke(operator, context, None)
    assert result == {"RUNNING_MODAL"} and operator.authorize_animated_removal
    assert "animation" in confirmation["message"] and "Baked copies remain" in confirmation["message"]
    assert bpy.ops.character_designer.remove_skirt_setup(authorize_animated_removal=True) == {"FINISHED"}
    assert all(bpy.data.objects.get(baked[key]) is not None for key in ("rig", "mesh")), \
        "Removing the live setup removed its independent baked animation"
    assert bpy.ops.character_designer.skirt_preview_bake() == {"FINISHED"}
    assert bpy.ops.character_designer.skirt_preview_bake(restore=True) == {"FINISHED"}
    assert bpy.context.active_object == source and not source.hide_get()


def test_shared_attachment_operator_workflow():
    def make_character(name, bone_name, x):
        data = bpy.data.armatures.new(name + " Bones")
        armature = bpy.data.objects.new(name, data)
        bpy.context.collection.objects.link(armature)
        armature.location.x = x
        activate(armature)
        bpy.ops.object.mode_set(mode="EDIT")
        bone = data.edit_bones.new(bone_name)
        bone.head, bone.tail = (0, 0, 1), (0, 0, 1.5)
        bpy.ops.object.mode_set(mode="OBJECT")
        return armature

    settings = skirt._settings(bpy.context)
    profile = character_setup.settings(bpy.context)
    settings.physics = False
    settings.armature = None
    settings.parent_bone = ""
    settings.show_attachment = False
    original = make_character("Shared Attachment Character", "Artist Waist", 0)
    replacement = make_character("Replacement Attachment Character", "Travel Base", 2)
    assert not character_setup.bone_candidates(original, "HIPS")
    assert not character_setup.bone_candidates(replacement, "HIPS")
    profile.rig = original
    profile.hips_bone = "Artist Waist"
    assert character_setup.bone_mapping_status(bpy.context, "HIPS")["status"] == "CONFIRMED"
    source = make_skirt("Shared Attachment Skirt")
    activate(source)
    assert bpy.ops.character_designer.create_skirt_setup() == {"FINISHED"}
    generated = source[skirt_rig.RIG_KEY]
    status = skirt_rig.attachment_status(source)
    assert status["attached"] and status["character"] is original
    assert status["parent_bone"] == "Artist Waist" and not status["physics"]
    world_before = generated.matrix_world.copy()
    objects_before = set(bpy.data.objects)

    # Changing shared data describes the next target; existing artist work stays attached.
    profile.rig = replacement
    profile.hips_bone = "Travel Base"
    bpy.context.view_layer.update()
    assert skirt._desired_attachment(bpy.context, source) == (replacement, "Travel Base")
    assert bpy.ops.character_designer.create_skirt_setup() == {"FINISHED"}
    status = skirt_rig.attachment_status(source)
    assert status["character"] is original and status["parent_bone"] == "Artist Waist"
    assert set(bpy.data.objects) == objects_before and source[skirt_rig.RIG_KEY] is generated

    labels = []
    layout = SimpleNamespace(
        label=lambda **kwargs: labels.append(kwargs.get("text")),
        prop=lambda *_args, **_kwargs: None,
        operator=lambda *_args, **_kwargs: SimpleNamespace(),
        separator=lambda: None,
    )
    layout.row = lambda **_kwargs: layout
    skirt.CHARACTERDESIGNER_PT_skirt_setup.draw(SimpleNamespace(layout=layout), bpy.context)
    assert f"Following: {original.name} / Artist Waist" in labels
    assert f"New target: {replacement.name} / Travel Base" in labels
    assert "Connected" not in labels, "The UI confused the desired target with the actual attachment"

    assert bpy.ops.character_designer.skirt_update_attachment() == {"FINISHED"}
    status = skirt_rig.attachment_status(source)
    assert status["attached"] and status["character"] is replacement
    assert status["parent_bone"] == "Travel Base" and status["has_backup"]
    assert bpy.ops.character_designer.skirt_restore_attachment.poll()
    assert max(abs(generated.matrix_world[i][j] - world_before[i][j])
               for i in range(4) for j in range(4)) < 1.0e-5
    assert set(bpy.data.objects) == objects_before

    assert bpy.ops.character_designer.skirt_restore_attachment() == {"FINISHED"}
    status = skirt_rig.attachment_status(source)
    assert status["attached"] and status["character"] is original
    assert status["parent_bone"] == "Artist Waist" and not status["has_backup"]
    assert not bpy.ops.character_designer.skirt_restore_attachment.poll()
    assert skirt._desired_attachment(bpy.context, source) == (replacement, "Travel Base")
    assert source[skirt_rig.RIG_KEY] is generated and set(bpy.data.objects) == objects_before
    assert max(abs(generated.matrix_world[i][j] - world_before[i][j])
               for i in range(4) for j in range(4)) < 1.0e-5
    assert bpy.ops.character_designer.remove_skirt_setup() == {"FINISHED"}
    profile.rig = None
    print("PASS shared custom Hips / explicit update / actual UI state / restore")


def main():
    character_designer.register()
    own_registration = not hasattr(bpy.types.WindowManager, "character_designer_skirt")
    if own_registration:
        for cls in skirt.SKIRT_CLASSES:
            bpy.utils.register_class(cls)
        bpy.types.WindowManager.character_designer_skirt = bpy.props.PointerProperty(
            type=skirt.CharacterDesignerSkirtState,
        )
    try:
        test_ui_workflow()
        test_physics_bake_operators()
        test_shared_attachment_operator_workflow()
        print("SKIRT_UI_PASS=create/repeat, selected source, wire controls, baked preview visibility, rollback, frame range, cancellation, removal, shared attachment update/restore", flush=True)
    finally:
        skirt.stop_skirt_runtime()
        if own_registration:
            del bpy.types.WindowManager.character_designer_skirt
            for cls in reversed(skirt.SKIRT_CLASSES):
                bpy.utils.unregister_class(cls)
        character_designer.unregister()


if __name__ == "__main__":
    main()
