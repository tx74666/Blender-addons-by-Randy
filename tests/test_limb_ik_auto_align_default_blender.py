"""New IK defaults to Auto; native saved state and explicit Manual survive reload/rebuild."""

from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import limb_ik
from test_limb_ik_blender import analyze, ensure_registered, ensure_unregistered, make_humanoid, reset_scene


def check_state(armature, enabled, count):
    inventory = limb_ik._validate_inventory(armature)
    assert len(inventory["rigs"]) == count
    for rig in inventory["rigs"].values():
        assert rig["auto_align"] is enabled
        assert rig["target"][limb_ik.AUTO_ALIGN_KEY] is enabled
        end_rotation = next(c for _owner, c, record in rig["entries"] if record["role"] == "END_ROTATION")
        assert end_rotation.mute is enabled
        assert rig["auto_offset_rotation"].mute is (not enabled)
        if enabled:
            assert armature.pose.bones[rig["target"].name].custom_shape_transform == armature.pose.bones[rig["chain"][2]]
    settings = bpy.context.window_manager.character_designer_limb_ik
    assert limb_ik._global_auto_align_ui_state(bpy.context, settings) == (True, enabled)


def main():
    ensure_registered()
    try:
        with tempfile.TemporaryDirectory(prefix="cd-auto-align-") as temp:
            for method in ("ROLL_DECOUPLED", "DIRECT_PREROLL"):
                reset_scene()
                armature = make_humanoid()
                _result, settings = analyze(armature)
                settings.build_method = method
                settings.selected_limb = "LEFT_ARM"
                assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
                check_state(armature, True, 1)
                assert bpy.ops.character_designer.limb_ik_build_all() == {"FINISHED"}
                check_state(armature, True, 4)
                name = armature.name
                for enabled in (True, False):
                    if not enabled:
                        assert bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") == {"FINISHED"}
                    path = Path(temp) / f"{method}-{enabled}.blend"
                    assert bpy.ops.wm.save_as_mainfile(filepath=str(path)) == {"FINISHED"}
                    assert bpy.ops.wm.open_mainfile(filepath=str(path)) == {"FINISHED"}
                    armature = bpy.data.objects[name]
                    check_state(armature, enabled, 4)
                    _result, settings = analyze(armature)
                    settings.build_method = method
                    assert bpy.ops.character_designer.limb_ik_rebuild() == {"FINISHED"}
                    check_state(armature, enabled, 4)
                print(f"PASS {method}: default Auto, incremental Build All, save/reopen, Rebuild, explicit Manual")
    finally:
        reset_scene()
        ensure_unregistered()
    print("AUTO_ALIGN_DEFAULT_AND_PERSISTENCE_PASSED")


if __name__ == "__main__":
    main()
