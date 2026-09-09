"""Exercise the public switching operator and its collection/lifecycle integration."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from character_designer import bone_collections, limb_ik, limb_ik_fk
import test_limb_ik_blender as base


base.ensure_registered()
for method in ("ROLL_DECOUPLED", "DIRECT_PREROLL"):
    base.reset_scene()
    rig = base.make_humanoid()
    _result, settings = base.analyze(rig)
    settings.build_method = method
    settings.selected_limb = "LEFT_LEG"
    assert bpy.ops.character_designer.limb_ik_build_selected() == {"FINISHED"}
    assert bone_collections.has_layout_backup(rig)
    inventory = limb_ik._validate_inventory(rig)
    side = inventory["rigs"][("LEG", "L")]
    before = limb_ik_fk._matrices(rig, side["chain"])
    assert bpy.ops.character_designer.limb_ik_fk_switch(mode="FK") == {"FINISHED"}
    limb_ik_fk._verify(rig, before)
    animation = rig.data.collections["Animation"]
    assert set(side["chain"]) <= {bone.name for bone in animation.bones}
    assert base.cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) == {"CANCELLED"}
    assert base.cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) == {"CANCELLED"}
    limb_ik_fk._verify(rig, before)
    assert bpy.ops.character_designer.limb_ik_fk_switch(mode="IK") == {"FINISHED"}
    animation = rig.data.collections["Animation"]
    assert not (set(side["chain"]) & {bone.name for bone in animation.bones})
    limb_ik_fk._verify(rig, before)
    assert bpy.ops.character_designer.limb_ik_rebuild() == {"FINISHED"}
    assert bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") == {"FINISHED"}
    assert not rig.animation_data or not rig.animation_data.drivers
    print("PASS IK_FK_UI", method)
print("IK_FK_UI_PASSED")
