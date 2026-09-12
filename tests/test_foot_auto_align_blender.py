"""Foot Auto follows the shin; Manual plants; upgrades and handoffs preserve pose."""
import os
import sys
import tempfile

import bpy
from mathutils import Euler, Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import foot_controls as feet, limb_ik, limb_ik_fk
import test_limb_ik_blender as base
from test_limb_ik_fk_blender import build, update


def pose(rig, record):
    return {name: rig.pose.bones[name].matrix.copy() for name in (*record['chain'], record['toe'])}


def inventory(rig, key):
    return limb_ik._validate_inventory(rig)['rigs'][key]


def legacy_build(rig, key):
    upgrade = feet.update_auto_follow
    feet.update_auto_follow = lambda context, armature, selected, **kw: feet.get_record(armature, selected)
    try:
        return feet.build(bpy.context, rig, key)
    finally:
        feet.update_auto_follow = upgrade


def check_motion(rig, key, record):
    target = rig.pose.bones[record['target']]
    foot, shin, toe = (rig.pose.bones[name] for name in (record['chain'][2], record['chain'][1], record['toe']))
    local = shin.matrix.inverted() @ foot.matrix
    toe_local = foot.matrix.inverted() @ toe.matrix
    before = foot.matrix.copy()
    target.location += Vector((.04, -.12, .13))
    update(rig)
    assert limb_ik._rotation_error(foot.matrix, before) > .04
    assert limb_ik._rotation_error(shin.matrix.inverted() @ foot.matrix, local) < 8e-4
    assert limb_ik._rotation_error(foot.matrix.inverted() @ toe.matrix, toe_local) < 8e-4
    # Roll and bank still modify the inherited foot, and toe stays attached.
    roll = rig.pose.bones[record['roll']]
    roll.rotation_euler = (-.5, .12, 0)
    update(rig)
    assert limb_ik._rotation_error(shin.matrix.inverted() @ foot.matrix, local) > .2
    assert (toe.head - foot.tail).length < 3e-4
    wanted = pose(rig, record)
    settings = bpy.context.window_manager.character_designer_limb_ik
    for enabled in (False, True, False):
        limb_ik._set_auto_align_selected_target(bpy.context, rig, settings, enabled)
        limb_ik_fk._verify(rig, wanted)
    before = foot.matrix.copy()
    target.location += Vector((-.02, .045, .04))
    update(rig)
    assert limb_ik._rotation_error(foot.matrix, before) < 8e-4
    limb_ik._set_auto_align_selected_target(bpy.context, rig, settings, True)
    wanted = pose(rig, record)
    limb_ik_fk.switch_limb(bpy.context, rig, key, 'FK')
    limb_ik_fk._verify(rig, wanted)
    limb_ik_fk.switch_limb(bpy.context, rig, key, 'IK')
    limb_ik_fk._verify(rig, wanted)
    return wanted


def test_fresh_follow_and_handoffs():
    for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
        for selected in ('LEFT_LEG', 'RIGHT_LEG'):
            rig, key, _data = build(method, selected, toes=True)
            record = feet.build(bpy.context, rig, key)
            assert record['auto_follow'] == 1
            wanted = check_motion(rig, key, record)
            feet.remove(bpy.context, rig, key)
            limb_ik_fk._verify(rig, wanted)
            assert not feet.records(rig)
            print('FOLLOW_HANDOFF', method, selected, flush=True)


def test_legacy_upgrade_and_reload():
    for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
        rig, key, _data = build(method, 'RIGHT_LEG', toes=True)
        record = legacy_build(rig, key)
        target = rig.pose.bones[record['target']]
        target.location += Vector((-.03, -.07, .055))
        target.rotation_euler = (.11, -.06, .07)
        rig.pose.bones[record['roll']].rotation_euler = (-.23, .08, 0)
        rig.pose.bones[record['toe_control']].rotation_euler = (.12, .04, -.03)
        rig.rotation_euler = (.17, -.11, .2)
        if master := rig.pose.bones.get(limb_ik.MASTER_NAME):
            master.rotation_euler = (.13, .05, -.17)
        update(rig)
        wanted = pose(rig, record)
        bases = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
        record = feet.update_auto_follow(bpy.context, rig, key)
        limb_ik_fk._verify(rig, wanted)
        for name, basis in bases.items():
            assert rig.pose.bones[name].matrix_basis == basis, name
        assert feet.update_auto_follow(bpy.context, rig, key) == record
        check_motion(rig, key, record)
        with tempfile.TemporaryDirectory(prefix='cd-foot-auto-') as temp:
            filename = os.path.join(temp, 'foot-auto.blend')
            name = rig.name
            bpy.ops.wm.save_as_mainfile(filepath=filename)
            bpy.ops.wm.open_mainfile(filepath=filename)
            rig = bpy.data.objects[name]
            assert inventory(rig, key)['foot_controls']['auto_follow'] == 1
            update(rig)
            feet.validate(rig)
        print('UPGRADE_RELOAD', method, flush=True)


def test_upgrade_refusals_and_rollback():
    rig, key, _data = build('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
    record = legacy_build(rig, key)
    raw, names, wanted = rig.data[feet.RECORD_KEY], set(rig.data.bones.keys()), pose(rig, record)
    target = rig.pose.bones[record['target']]
    target.keyframe_insert(data_path='location', frame=1)
    try:
        feet.update_auto_follow(bpy.context, rig, key)
    except limb_ik.LimbIKError as exc:
        assert 'animation' in str(exc)
    else:
        raise AssertionError('Authored animation migrated silently')
    rig.animation_data.action = None
    for name in (record['toe_control'], record['toe'], inventory(rig, key)['pole'].name, 'Hips'):
        rig.pose.bones[name].keyframe_insert(data_path='rotation_euler', frame=1)
        try:
            feet.update_auto_follow(bpy.context, rig, key)
        except limb_ik.LimbIKError as exc:
            assert 'animation' in str(exc), (name, str(exc))
        else:
            raise AssertionError('Affected toe/leg/ancestor animation migrated silently: ' + name)
        rig.animation_data.action = None
    original = feet._verify_pose
    feet._verify_pose = lambda *args: (_ for _ in ()).throw(RuntimeError('Injected Auto upgrade failure'))
    try:
        try:
            feet.update_auto_follow(bpy.context, rig, key)
        except RuntimeError as exc:
            assert 'Injected' in str(exc)
        else:
            raise AssertionError('Injected failure ignored')
    finally:
        feet._verify_pose = original
    assert rig.data[feet.RECORD_KEY] == raw
    assert set(rig.data.bones.keys()) == names
    limb_ik_fk._verify(rig, wanted)
    limb_ik._validate_inventory(rig)


def test_authored_auto_animation_is_stateless():
    rig, key, _data = build('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
    record = feet.build(bpy.context, rig, key)
    target = rig.pose.bones[record['target']]
    foot = rig.pose.bones[record['chain'][2]]
    shin = rig.pose.bones[record['chain'][1]]
    local = shin.matrix.inverted() @ foot.matrix
    original = foot.matrix.copy()
    for frame, position in ((1, (0, 0, 0)), (12, (.04, -.12, .13))):
        target.location = position
        target.keyframe_insert(data_path='location', frame=frame)
    frames = {}
    for frame in (12, 1, 7, 12, 7, 1):
        bpy.context.scene.frame_set(frame)
        update(rig)
        assert limb_ik._rotation_error(shin.matrix.inverted() @ foot.matrix, local) < 8e-4
        if frame in frames:
            limb_ik_fk._verify(rig, frames[frame])
        frames[frame] = pose(rig, record)
    assert limb_ik._rotation_error(frames[12][foot.name], original) > .04
    # New rigs may be animated normally; changing their static mode then
    # refuses to rewrite those authored animator channels.
    settings = bpy.context.window_manager.character_designer_limb_ik
    try:
        limb_ik._set_auto_align_selected_target(bpy.context, rig, settings, False)
    except limb_ik.LimbIKError as exc:
        assert 'animation' in str(exc)
    else:
        raise AssertionError('Animated target was rematched by a static mode switch')
    assert inventory(rig, key)['auto_align']


def main():
    base.ensure_registered()
    try:
        for test in (test_fresh_follow_and_handoffs, test_legacy_upgrade_and_reload,
                     test_upgrade_refusals_and_rollback, test_authored_auto_animation_is_stateless):
            test()
            print('PASS', test.__name__, flush=True)
    finally:
        base.reset_scene()
        base.ensure_unregistered()
    print('FOOT_AUTO_ALIGN_PASSED 4')


if __name__ == '__main__':
    main()
