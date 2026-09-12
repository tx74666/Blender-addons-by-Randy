"""Global legacy-foot repair is atomic and its button disappears when complete."""
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'addons'), str(ROOT/'tests')]
import character_designer
from character_designer import body_controls_ui as ui, foot_controls as feet, limb_ik
from test_limb_ik_fk_blender import build
from test_foot_auto_align_blender import legacy_build
from test_body_setup_ui_blender import Layout, buttons


def main():
    character_designer.register()
    rig, _, _ = build('ROLL_DECOUPLED', 'LEFT_LEG', toes=True)
    settings = limb_ik._settings(bpy.context)
    settings.selected_limb = 'RIGHT_LEG'
    assert bpy.ops.character_designer.limb_ik_build_selected() == {'FINISHED'}
    for side in 'LR':
        legacy_build(rig, ('LEG',side))
    settings.selected_limb = 'LEFT_ARM'  # Repair is independent of the dropdown.
    before = {p.name:p.matrix.copy() for p in rig.pose.bones}
    raw = rig.data[feet.RECORD_KEY]
    layout = Layout()
    ui.draw_foot_auto_align_upgrade(layout,bpy.context)
    assert [r[1] for r in buttons(layout.records)] == ['character_designer.upgrade_foot_auto_align']
    update = feet.update_auto_follow
    def fail_second(context,armature,key):
        if key[1]=='R': raise RuntimeError('Injected second foot failure')
        return update(context,armature,key)
    feet.update_auto_follow = fail_second
    try:
        assert bpy.ops.character_designer.upgrade_foot_auto_align('EXEC_DEFAULT') == {'CANCELLED'}
    finally:
        feet.update_auto_follow = update
    assert rig.data[feet.RECORD_KEY] == raw
    assert set(rig.data.bones.keys()) == set(before)
    assert max(abs(rig.pose.bones[n].matrix[i][j]-m[i][j])
               for n,m in before.items() for i in range(4) for j in range(4)) < 1e-5
    assert bpy.ops.character_designer.upgrade_foot_auto_align('EXEC_DEFAULT') == {'FINISHED'}
    assert all(feet.get_record(rig,('LEG',s))['auto_follow']==1 for s in 'LR')
    layout = Layout()
    ui.draw_foot_auto_align_upgrade(layout,bpy.context)
    assert not buttons(layout.records)
    print('FOOT_AUTO_ALIGN_UI_PASSED',flush=True)


if __name__=='__main__': main()
