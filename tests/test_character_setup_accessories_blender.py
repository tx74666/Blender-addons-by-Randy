"""Saved character references used by real Hair/Skirt UI operators.

Disposable fixtures only. Run in factory-startup background Blender.
"""

from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))

import character_designer as cd
from character_designer import character_setup as setup
from character_designer import hair_bones as hair
from character_designer import hair_bones_rig as hair_rig
from character_designer import skirt, skirt_rig
from test_hair_bones_rig_blender import activate, make_armature, reset
from test_hair_bones_ui_blender import fixture as hair_fixture, call, panel_calls
from test_skirt_ui_blender import make_skirt


def empty_selection():
    obj = bpy.data.objects.new("Unrelated Selection", None)
    bpy.context.scene.collection.objects.link(obj)
    activate(obj)
    return obj


def clear_profile():
    profile = setup.settings(bpy.context)
    profile.rig = None
    profile.body = None
    profile.assets.clear()
    profile.active_asset = -1


def test_saved_hair_source_and_main_rig_after_reopen():
    source, _, main_rig = hair_fixture(count=2)
    clear_profile()
    main_rig.name = "Saved Main Rig"
    other_rig = make_armature("Another Character")
    setup.settings(bpy.context).rig = main_rig
    activate(source, "EDIT")
    call("select_hair_strands")
    source.name = "Renamed Saved Hair"
    assert setup.role_source(bpy.context, "HAIR") is source
    source_name, main_name, other_name = source.name, main_rig.name, other_rig.name
    assert setup.role_source(bpy.context, "HAIR") is source
    empty_selection()
    hair._settings(bpy.context).source = None
    assert hair._source(bpy.context) is source
    assert ("label", "Main Rig: Saved Main Rig", True) in panel_calls()

    with tempfile.TemporaryDirectory(prefix="cd_saved_hair_") as temporary:
        path = str(Path(temporary) / "character.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        source = bpy.data.objects[source_name]
        main_rig = bpy.data.objects[main_name]
        other_rig = bpy.data.objects[other_name]
        other_bones = len(other_rig.data.bones)
        settings = hair._settings(bpy.context)
        settings.source = None
        settings.target_armature = None
        assert setup.settings(bpy.context).rig is main_rig
        assert hair._source(bpy.context) is source
        assert bpy.context.active_object.type == "EMPTY"
        cd.unregister()
        cd.register()
        assert setup.settings(bpy.context).rig is main_rig
        assert setup.role_source(bpy.context, "HAIR") is source
        call("hair_bind_to_character")
        assert source[hair_rig.RIG_KEY] is main_rig
        assert len(other_rig.data.bones) == other_bones
        call("hair_remove_binding")
    print("PASS saved hair references survive reopen and bind with another object selected")


def test_explicit_hair_override_and_active_source_still_win():
    source, _, main_rig = hair_fixture(count=1)
    clear_profile()
    other_rig = make_armature("Explicit Override")
    setup.settings(bpy.context).rig = main_rig
    settings = hair._settings(bpy.context)
    settings.target_armature = other_rig
    # A stale remembered mesh must not override the selected source.
    old_mesh = bpy.data.meshes.new("Remembered Hair Data")
    old_source = bpy.data.objects.new("Remembered Hair", old_mesh)
    bpy.context.scene.collection.objects.link(old_source)
    setup.remember_asset(bpy.context, old_source, "HAIR")
    settings.source = old_source
    activate(source, "EDIT")
    assert hair._source(bpy.context) is source
    call("select_hair_strands")
    empty_selection()
    settings.source = None
    assert hair._source(bpy.context) is source
    before = len(main_rig.data.bones)
    call("hair_bind_to_character")
    assert source[hair_rig.RIG_KEY] is other_rig
    assert len(main_rig.data.bones) == before
    call("hair_remove_binding")
    print("PASS explicit hair rig and active mesh retain precedence")


def test_skirt_uses_main_rig_and_remembers_source():
    reset()
    clear_profile()
    main_rig = make_armature("Saved Skirt Character")
    activate(main_rig, "EDIT")
    bone = main_rig.data.edit_bones.new("Hips")
    bone.head, bone.tail = (0, 0, 1.4), (0, 0, 1.6)
    bpy.ops.object.mode_set(mode="OBJECT")
    setup.settings(bpy.context).rig = main_rig
    other_rig = make_armature("Unrelated Character")
    source = make_skirt("Remembered Skirt")
    activate(source)
    settings = skirt._settings(bpy.context)
    settings.source = None
    settings.armature = None
    settings.parent_bone = ""
    settings.physics = False
    assert bpy.context.selected_objects == [source]
    assert bpy.ops.character_designer.create_skirt_setup() == {"FINISHED"}
    generated = source[skirt_rig.RIG_KEY]
    assert generated.parent is main_rig
    assert generated.parent is not other_rig
    empty_selection()
    settings.source = None
    assert skirt._source(bpy.context) is source
    assert setup.role_source(bpy.context, "SKIRT") is source
    assert bpy.ops.character_designer.remove_skirt_setup() == {"FINISHED"}
    assert source.parent is None
    print("PASS skirt uses saved main rig without rig selection and recalls source")


if __name__ == "__main__":
    cd.register()
    try:
        test_saved_hair_source_and_main_rig_after_reopen()
        test_explicit_hair_override_and_active_source_still_win()
        test_skirt_uses_main_rig_and_remembers_source()
        print("CHARACTER_SETUP_ACCESSORIES_OK")
    finally:
        cd.unregister()
