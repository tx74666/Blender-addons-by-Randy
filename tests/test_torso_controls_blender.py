"""Native torso integration: neutral build, shared bend, FK, recovery and guards."""
import math
import os
import sys
import tempfile

import bpy
from mathutils import Euler, Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'addons'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))
from character_designer import torso_controls as torso, limb_ik, limb_ik_fk, foot_controls
import test_limb_ik_blender as base


def fixture(method='DIRECT_PREROLL', count=3, posed=False, feet=False):
    base.ensure_registered()
    base.reset_scene()
    rig = base.make_humanoid(roll_offset=0.27)
    bpy.ops.object.mode_set(mode='EDIT')
    bones = rig.data.edit_bones
    first = bones['Chest']
    first.name = 'spine'
    chain = ['spine'] + (['SpineMid'] if count == 4 else []) + ['Chest', 'UpperChest']
    first.tail.z = 1.15 + 0.4 / count
    parent = first
    for i, name in enumerate(chain[1:], 1):
        parent = base.add_bone(bones, name, parent.tail.copy(), (0, 0, 1.15 + .4 * (i + 1) / count), parent)
    for side in ('L', 'R'):
        bones['shoulder.' + side].parent = parent
        if feet:
            foot = bones['foot.' + side]
            base.add_bone(bones, 'toe.' + side, foot.tail.copy(), foot.tail + Vector((0, -.13, 0)), foot)
    bpy.ops.object.mode_set(mode='POSE')
    _, settings = base.analyze(rig)
    settings.build_method, settings.selected_limb = method, 'LEFT_LEG'
    if method == 'DIRECT_PREROLL':
        assert bpy.ops.character_designer.limb_ik_direct_preroll_check() == {'FINISHED'}
    assert bpy.ops.character_designer.limb_ik_build_all() == {'FINISHED'}, settings.last_message
    if feet:
        foot_controls.build(bpy.context, rig, ('LEG', 'L'), toe_name='toe.L')
    if posed:
        rig.location, rig.rotation_euler, rig.scale = (.2, -.1, .3), (.1, -.08, .07), (.85,) * 3
        for name in ['Hips', *chain]:
            pb = rig.pose.bones[name]
            pb.rotation_mode = 'XYZ'
            pb.rotation_euler = (.07, -.035, .05)
        inventory = limb_ik._validate_inventory(rig)
        for data in inventory['rigs'].values():
            rig.pose.bones[data['target'].name].location += Vector((.015, -.015, .01))
        if feet:
            rec = foot_controls.get_record(rig, ('LEG', 'L'))
            rig.pose.bones[rec['roll']].rotation_euler.x = .35
    limb_ik_fk._update(bpy.context, rig)
    return rig, chain


def poses(rig, names=None):
    limb_ik_fk._update(bpy.context, rig)
    return {pb.name: pb.matrix.copy() for pb in rig.pose.bones if names is None or pb.name in names}


def test_neutral_build_and_remove():
    for method in ('DIRECT_PREROLL', 'ROLL_DECOUPLED'):
        for count in (3, 4):
            for posed in (False, True):
                rig, chain = fixture(method, count, posed, feet=True)
                before, digest = poses(rig), limb_ik._armature_digest(rig)
                rest = {b.name: torso._state(b) for b in rig.data.bones}
                rig.data.use_mirror_x = True
                rec = torso.build(bpy.context, rig, chain=chain, hips_name='Hips')
                torso._verify_pose(rig, before)
                assert torso.build(bpy.context, rig) == rec
                assert limb_ik._armature_digest(rig) == digest
                for name, state in rest.items():
                    assert torso._same_rest(rig.data.bones[name], state)
                for name in [rec['bend'], *rec['controls'].values()]:
                    assert max(abs(rig.pose.bones[name].matrix_basis[i][j] - Matrix.Identity(4)[i][j]) for i in range(4) for j in range(4)) < 2e-6
                rig.pose.bones[rec['bend']].rotation_euler = (.22, .11, -.08)
                rig.pose.bones[rec['controls'][chain[1]]].rotation_euler = (-.07, .13, .05)
                now = poses(rig, before)
                assert (now[chain[-1]].translation - before[chain[-1]].translation).length > .005
                desired = poses(rig, before)
                torso.remove(bpy.context, rig)
                torso._verify_pose(rig, desired)
                assert torso.get_record(rig) is None
                assert set(before) == set(rig.pose.bones.keys())
                assert rig.data.use_mirror_x
                assert limb_ik._armature_digest(rig) == digest
                limb_ik._validate_inventory(rig)
                print('TORSO_ROUNDTRIP', method, count, posed, flush=True)


def test_bend_and_independent_fk():
    rig, chain = fixture()
    before = poses(rig)
    rec = torso.build(bpy.context, rig)
    bend = rig.pose.bones[rec['bend']]
    bend.rotation_euler.x = .6
    update = lambda: limb_ik_fk._update(bpy.context, rig)
    update()
    for i, name in enumerate(chain):
        delta = before[name].to_quaternion().rotation_difference(rig.pose.bones[name].matrix.to_quaternion())
        assert abs(delta.angle - .2 * (i + 1)) < 1e-4, (i, delta.angle)
    ctrl = rig.pose.bones[rec['controls'][chain[1]]]
    ctrl.rotation_euler.x = .12
    update()
    for i, name in enumerate(chain):
        delta = before[name].to_quaternion().rotation_difference(rig.pose.bones[name].matrix.to_quaternion())
        assert abs(delta.angle - (.2 * (i + 1) + (.12 if i else 0))) < 1e-4
    bend.rotation_euler = (0, 0, 0)
    ctrl.rotation_euler = (0, 0, 0)
    update()
    torso._verify_pose(rig, before)


def test_rollback_and_dependency_guards():
    rig, chain = fixture(posed=True)
    before, names = poses(rig), set(rig.data.bones.keys())
    objects, meshes = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())
    add = torso._add_widget
    def fail(*args, **kwargs):
        add(*args, **kwargs)
        raise RuntimeError('Injected torso widget failure')
    torso._add_widget = fail
    try:
        try:
            torso.build(bpy.context, rig)
        except RuntimeError as exc:
            assert 'Injected' in str(exc)
        else:
            raise AssertionError('Build did not roll back')
    finally:
        torso._add_widget = add
    assert set(bpy.data.objects.keys()) == objects and set(bpy.data.meshes.keys()) == meshes
    assert set(rig.data.bones.keys()) == names and torso.get_record(rig) is None
    torso._verify_pose(rig, before)
    rec = torso.build(bpy.context, rig)
    expected = rig.data[torso.RECORD_KEY]
    verify = torso._verify_pose
    def fail_match(*_args):
        raise limb_ik.LimbIKError('Injected native matching failure')
    torso._verify_pose = fail_match
    try:
        assert 'Injected' in str(_remove_error(rig))
    finally:
        torso._verify_pose = verify
    assert rig.data[torso.RECORD_KEY] == expected
    torso.validate(rig)
    torso._verify_pose(rig, before)
    def refused():
        try:
            torso.remove(bpy.context, rig)
        except limb_ik.LimbIKError:
            pass
        else:
            raise AssertionError('Artist dependency was removed')
        assert rig.data[torso.RECORD_KEY] == expected
    obj = bpy.data.objects.new('Artist attachment', None)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent, obj.parent_type, obj.parent_bone = rig, 'BONE', rec['bend']
    refused()
    obj.parent = None
    con = obj.constraints.new('COPY_TRANSFORMS')
    con.target, con.subtarget = rig, rec['bend']
    refused()
    obj.constraints.remove(con)
    ctrl = rig.pose.bones[rec['bend']]
    ctrl.keyframe_insert('rotation_euler', frame=1)
    refused()
    assert 'animation' in str(_remove_error(rig))

    rig, chain = fixture()
    rig.pose.bones[chain[0]].keyframe_insert('rotation_quaternion', frame=1)
    before_names = set(rig.data.bones.keys())
    try:
        torso.build(bpy.context, rig)
    except limb_ik.LimbIKError as exc:
        assert 'animation' in str(exc)
    else:
        raise AssertionError('Native authored animation was overridden')
    assert set(rig.data.bones.keys()) == before_names


def _remove_error(rig):
    try:
        torso.remove(bpy.context, rig)
    except limb_ik.LimbIKError as exc:
        return exc
    raise AssertionError('Expected safe refusal')


def test_save_reopen_and_tampering():
    rig, chain = fixture()
    rec = torso.build(bpy.context, rig)
    rig.pose.bones[rec['bend']].rotation_euler.y = .18
    rig.pose.bones[rec['controls'][chain[-1]]].rotation_euler.x = -.09
    before = poses(rig)
    name = rig.name
    with tempfile.TemporaryDirectory(prefix='cd-torso-') as directory:
        path = os.path.join(directory, 'torso.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig = bpy.data.objects[name]
        poses(rig)
        assert torso.validate(rig) == rec
        torso._verify_pose(rig, before)
        con = rig.pose.bones[chain[0]].constraints['CD Torso FK']
        con.influence = .5
        assert 'edited' in str(_remove_error(rig))
        con.influence = 1.0
        torso.remove(bpy.context, rig)


if __name__ == '__main__':
    for test in (test_neutral_build_and_remove, test_bend_and_independent_fk,
                 test_rollback_and_dependency_guards, test_save_reopen_and_tampering):
        test()
        print('PASS', test.__name__, flush=True)
    print('TORSO_CONTROLS_PASSED 4', flush=True)
