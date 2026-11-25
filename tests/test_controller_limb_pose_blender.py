"""Native current-frame controller edits, including a temporary IK pose written as FK."""
from pathlib import Path
import sys

import bpy
from mathutils import Vector

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'addons'), str(root / 'tests')]
import character_designer as cd
from character_designer import animation_controller_worker as worker, control_pose_offset, limb_ik_fk as match
from character_designer import limb_ik_fk_pose_key as keys, limb_ik as limb
from test_limb_ik_fk_blender import build, snapshot


def main():
    cd.register()
    rig, key, entry = build('DIRECT_PREROLL', 'RIGHT_LEG')
    scene = bpy.context.scene
    other = rig.pose.bones['thigh.L']
    scene.character_designer_setup.rig = rig
    scene.character_designer_animation_worklist.workspace_identity = 'controller-keys'
    target = rig.pose.bones[entry['target'].name]
    match.switch_limb(bpy.context, rig, key, 'FK', keyframe=False)
    target.keyframe_insert(data_path='["ik_fk"]', frame=1)
    action, slot = rig.animation_data.action, rig.animation_data.action_slot_handle
    rec = scene.character_designer_controller_motions.add()
    rec.workspace_identity = 'controller-keys'
    rec.state, rec.target = 'READY', rig
    rec.controller_action, rec.controller_slot = action, slot
    rig.data.bones.active = target.bone
    for bone in rig.pose.bones:
        bone.select = bone == target
    untouched = other.matrix_basis.copy()

    def mode(frame):
        scene.frame_set(frame)
        return match.mode_for_rig(rig, entry)

    assert mode(10) == 'FK'
    keys.edit(bpy.context, rig, key, 'FK', record_key=True)
    scene.frame_set(20)
    keys.edit(bpy.context, rig, key, 'IK', record_key=False)
    target.location += Vector((.025, -.035, .04))
    desired = snapshot(rig, entry)
    assert bpy.ops.character_designer.controller_limb_pose(operation='KEY') == {'FINISHED'}
    assert mode(19) == 'FK' and mode(20) == 'IK'
    match._verify(rig, desired)
    scene.frame_set(30)
    before = snapshot(rig, entry)
    keys.edit(bpy.context, rig, key, 'FK', record_key=True)
    assert mode(29) == 'IK' and mode(30) == 'FK'
    match._verify(rig, before)

    # Adjust with an unkeyed IK control, commit to FK with ordinary interpolation.
    scene.frame_set(40)
    scene.tool_settings.use_keyframe_insert_auto = True
    end = rig.pose.bones[entry['chain'][-1]]
    rig.data.bones.active = end.bone
    for bone in rig.pose.bones:
        bone.select = bone == end
    assert bpy.ops.character_designer.controller_limb_pose(operation='POSE_IK') == {'FINISHED'}
    target.location += Vector((.02, .03, -.015))
    wanted = snapshot(rig, entry)
    # Native transform Auto Key can write the IK target. A custom keying set
    # can key its mode as well. Neither belongs to this frame's FK pose.
    target.keyframe_insert(data_path='location', frame=40)
    target.keyframe_insert(data_path='["ik_fk"]', frame=40)
    assert bpy.ops.character_designer.controller_limb_pose(operation='KEY_FK') == {'FINISHED'}
    scene.tool_settings.use_keyframe_insert_auto = False
    assert mode(39) == 'FK' and mode(40) == 'FK'
    match._verify(rig, wanted)
    assert other.matrix_basis == untouched
    assert rig.animation_data.action == rec.controller_action == action
    assert rec.controller_slot == rig.animation_data.action_slot_handle == slot
    bag = control_pose_offset._bag(action, slot)
    mode_curve, = [c for c in bag.fcurves if c.data_path == match.property_path(target)]
    assert any(p.co.x == 10 and p.co.y == 0 and p.interpolation == 'CONSTANT' for p in mode_curve.keyframe_points)
    assert not any(other.path_from_id() in c.data_path for c in bag.fcurves)
    assert not any(p.co.x == 39 for c in bag.fcurves for p in c.keyframe_points)
    assert not any(p.co.x == 40 for c in bag.fcurves if c.data_path in (match.property_path(target), target.path_from_id('location'))
                   for p in c.keyframe_points)
    scene.frame_set(41)
    original = worker._action_hash(action,slot)
    committed = snapshot(rig,entry)
    actual = match._restore_bookend
    def fail_after_cut(*args):
        actual(*args)
        raise RuntimeError('injected key failure')
    try:
        match._restore_bookend = fail_after_cut
        try:
            keys.edit(bpy.context, rig, key, 'IK', record_key=True)
        except RuntimeError as exc:
            assert str(exc) == 'injected key failure'
        else:
            raise AssertionError('failed key transaction was accepted')
    finally:
        match._restore_bookend = actual
    assert action == rig.animation_data.action == rec.controller_action
    assert worker._action_hash(action,slot) == original
    match._verify(rig,committed)
    print('CONTROLLER_LIMB_POSE_PASS', flush=True)


if __name__ == '__main__':
    main()
