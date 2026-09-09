"""Public foot-control actions, visual mode handoff, and base-rig lifecycle guard."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from character_designer import bone_collections, foot_controls, limb_ik, limb_ik_fk
import test_limb_ik_blender as base
from test_limb_ik_fk_blender import build, update


base.ensure_registered()
for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
    rig, key, data = build(method, 'LEFT_LEG', toes=True)
    before = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
    assert bpy.ops.character_designer.foot_controls(action='BUILD') == {'FINISHED'}
    record = foot_controls.get_record(rig, key)
    assert record and bone_collections.has_layout_backup(rig)
    limb_ik_fk._verify(rig, before)
    assert bpy.ops.character_designer.foot_controls(action='FIT_VISUAL') == {'FINISHED'}
    assert foot_controls.has_roll_visual_backup(rig, key)
    limb_ik_fk._verify(rig, before)
    assert bpy.ops.character_designer.foot_controls(action='RESTORE_VISUAL') == {'FINISHED'}
    assert not foot_controls.has_roll_visual_backup(rig, key)
    limb_ik_fk._verify(rig, before)
    settings = bpy.context.window_manager.character_designer_limb_ik
    settings.selected_limb = 'RIGHT_LEG'
    if method == 'DIRECT_PREROLL':
        assert bpy.ops.character_designer.limb_ik_direct_preroll_check() == {'FINISHED'}
    assert bpy.ops.character_designer.limb_ik_build_selected() == {'FINISHED'}
    settings.selected_limb = 'LEFT_LEG'
    assert bpy.ops.character_designer.foot_controls(action='SELECT_ROLL') == {'FINISHED'}
    assert rig.data.bones.active.name == record['roll']
    rig.pose.bones[record['roll']].rotation_euler.x = 0.31
    update(rig)
    before = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
    for action in ('DISABLE', 'ENABLE'):
        assert bpy.ops.character_designer.limb_ik_auto_align_target(action=action) == {'FINISHED'}
        limb_ik_fk._verify(rig, before)
        limb_ik._validate_inventory(rig)
    assert bpy.ops.character_designer.foot_controls(action='SELECT_TOE') == {'FINISHED'}
    assert rig.data.bones.active.name == record['toe_control']
    assert bpy.ops.character_designer.limb_ik_fk_switch(mode='FK') == {'FINISHED'}
    assert record['toe_control'] in {b.name for b in rig.data.collections['Animation'].bones}
    assert record['roll'] not in {b.name for b in rig.data.collections['Animation'].bones}
    assert bpy.ops.character_designer.limb_ik_fk_switch(mode='IK') == {'FINISHED'}
    assert base.cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) == {'CANCELLED'}
    assert base.cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove('EXEC_DEFAULT')) == {'CANCELLED'}
    desired = limb_ik_fk._matrices(rig, (*record['chain'], record['toe']))
    assert bpy.ops.character_designer.foot_controls(action='REMOVE') == {'FINISHED'}
    limb_ik_fk._verify(rig, desired)
    assert not foot_controls.records(rig)
    assert record['toe'] in {b.name for b in rig.data.collections['Animation'].bones}
    print('PASS FOOT_UI', method, flush=True)
print('FOOT_UI_PASSED', flush=True)
