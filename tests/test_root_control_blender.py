"""Global root: complete-character motion, matching, recovery, and ownership guards."""
import os
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import root_control as root, limb_ik, limb_ik_fk, spine_ik_fk as spine
from character_designer import torso_controls, foot_controls, eye_controls, bone_collections
import test_spine_ik_fk_blender as spine_tests
import test_limb_ik_fk_blender as limb_tests


def update(rig):
    limb_ik_fk._update(bpy.context, rig)


def poses(rig):
    update(rig)
    return {pb.name: pb.matrix.copy() for pb in rig.pose.bones}


def fixture():
    rig, chain, torso = spine_tests.fixture(posed=True)
    foot_controls.build(bpy.context, rig, ('LEG', 'R'), toe_name='toe.R')
    record = spine.build(bpy.context, rig)
    spine.switch(bpy.context, rig, 'IK')
    rig.pose.bones[record['chest']].location.y -= .01
    rig.pose.bones[record['shape']].rotation_euler.z += .025
    eyes = eye_controls.get_record(rig)
    rig.pose.bones[eyes['master']].location.x += .01
    update(rig)
    return rig


def test_whole_body_follow_roundtrip_and_removal():
    rig = fixture()
    before = poses(rig)
    rest = {bone.name: torso_controls._state(bone) for bone in rig.data.bones}
    digest = limb_ik._armature_digest(rig)
    records = {key: rig.data[key] for key in (spine.RECORD_KEY, torso_controls.RECORD_KEY,
                                             foot_controls.RECORD_KEY, eye_controls.RECORD_KEY)}
    rig.data.use_mirror_x = True
    rec = root.build(bpy.context, rig)
    assert root.build(bpy.context, rig) == rec
    root._verify_pose(rig, before)
    assert bpy.context.mode == 'POSE' and rig.data.use_mirror_x
    assert len(rec['controls']) == 8 and rec['sources'] == ['Hips']
    assert len(rig.pose.bones[rec['master']].custom_shape.data.vertices) == 24
    assert rec['master'] in rig.data.collections_all['Animation'].bones
    assert limb_ik._armature_digest(rig) == digest
    for name, state in rest.items():
        expected = dict(state, parent=rec['master']) if name in rec['controls'] else state
        assert torso_controls._same_rest(rig.data.bones[name], expected), name
    control = rig.pose.bones[rec['master']]
    for loc, rot, size in (((.15, -.07, .06), (0, 0, 0), 1.0),
                           ((.15, -.07, .06), (.12, -.15, .2), 1.0),
                           ((.15, -.07, .06), (.12, -.15, .2), 1.17)):
        control.location, control.rotation_euler = loc, rot
        control[root.SCALE_PROPERTY] = size
        update(rig)
        expected = {name: control.matrix @ matrix for name, matrix in before.items()}
        root._verify_pose(rig, expected)
        limb_ik._validate_inventory(rig)
    inventory = limb_ik._validate_inventory(rig)
    # The positive lifecycle uses a well-conditioned bend. The cumulative
    # near-straight case below also checks the strict removal/refusal contract.
    for key, data in inventory['rigs'].items():
        if key[0] == 'ARM':
            rig.pose.bones[data['target'].name].location.y -= .035
    update(rig)
    native = {pb.name: pb.matrix.copy() for pb in rig.pose.bones if not pb.bone.get(limb_ik.OWNER_KEY)}
    for key in inventory['rigs']:
        for mode in ('FK', 'IK'):
            print('ROOT_LIMB_SWITCH', key, mode, flush=True)
            limb_ik_fk.switch_limb(bpy.context, rig, key, mode, keyframe=False)
            limb_ik_fk._verify(rig, native)
    desired = poses(rig)
    root.remove(bpy.context, rig)
    assert root.get_record(rig) is None and set(rig.data.bones.keys()) == set(rest)
    root._verify_pose(rig, {name: matrix for name, matrix in desired.items()
                           if name != rec['master'] and name not in rec['controls']})
    for name, state in rest.items():
        assert torso_controls._same_rest(rig.data.bones[name], state), name
    assert records == {key: rig.data[key] for key in records}
    assert limb_ik._armature_digest(rig) == digest
    limb_ik._validate_inventory(rig)


def test_near_straight_cumulative_removal_preserves_or_rolls_back():
    rig = fixture()
    rest = {bone.name: torso_controls._state(bone) for bone in rig.data.bones}
    digest = limb_ik._armature_digest(rig)
    rec = root.build(bpy.context, rig)
    control = rig.pose.bones[rec['master']]
    for loc, rot, size in (((.15, -.07, .06), (0, 0, 0), 1.0),
                           ((.15, -.07, .06), (.12, -.15, .2), 1.0),
                           ((.15, -.07, .06), (.12, -.15, .2), 1.17)):
        control.location, control.rotation_euler = loc, rot
        control[root.SCALE_PROPERTY] = size
        update(rig)
    inventory = limb_ik._validate_inventory(rig)
    native = {pb.name: pb.matrix.copy() for pb in rig.pose.bones if not pb.bone.get(limb_ik.OWNER_KEY)}
    for key in inventory['rigs']:
        for mode in ('FK', 'IK'):
            limb_ik_fk.switch_limb(bpy.context, rig, key, mode, keyframe=False)
            limb_ik_fk._verify(rig, native)
    desired = poses(rig)
    bases = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in rig.pose.bones}
    root_rest = {bone.name: torso_controls._state(bone) for bone in rig.data.bones}
    saved = rig.data[root.RECORD_KEY]
    extension_records = {key: rig.data[key] for key in (spine.RECORD_KEY, torso_controls.RECORD_KEY,
                                                       foot_controls.RECORD_KEY, eye_controls.RECORD_KEY)}
    modes = {data['target'].name: rig.pose.bones[data['target'].name][limb_ik_fk.PROPERTY]
             for data in inventory['rigs'].values()}
    layout = bone_collections.capture_managed_layout(rig)
    widget = root._widget_snapshot(rig, rec)
    objects, meshes, collections = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys()), set(bpy.data.collections.keys())
    try:
        root.remove(bpy.context, rig)
    except limb_ik.LimbIKError as exc:
        assert 'could not preserve' in str(exc), str(exc)
        assert rig.data[root.RECORD_KEY] == saved
        assert root.validate(rig) == rec
        assert set(rig.data.bones.keys()) == set(root_rest)
        for name, state in root_rest.items():
            assert torso_controls._same_rest(rig.data.bones[name], state), name
        for name, (mode, basis) in bases.items():
            pb = rig.pose.bones[name]
            assert pb.rotation_mode == mode
            assert max(abs(pb.matrix_basis[i][j] - basis[i][j])
                       for i in range(4) for j in range(4)) < 1e-6, name
        assert rig.pose.bones[rec['master']][root.SCALE_PROPERTY] == 1.17
        assert modes == {name: rig.pose.bones[name][limb_ik_fk.PROPERTY] for name in modes}
        assert bone_collections.capture_managed_layout(rig) == layout
        assert root._widget_snapshot(rig, rec) == widget
        assert (set(bpy.data.objects.keys()), set(bpy.data.meshes.keys()), set(bpy.data.collections.keys())) == (objects, meshes, collections)
        root._verify_pose(rig, desired)
        print('ROOT_NEAR_STRAIGHT_REMOVAL_SAFE_REFUSAL', str(exc), flush=True)
    else:
        assert root.get_record(rig) is None and set(rig.data.bones.keys()) == set(rest)
        root._verify_pose(rig, {name: matrix for name, matrix in desired.items()
                               if name != rec['master'] and name not in rec['controls']})
        for name, state in rest.items():
            assert torso_controls._same_rest(rig.data.bones[name], state), name
        print('ROOT_NEAR_STRAIGHT_REMOVAL_PRESERVED', flush=True)
    assert extension_records == {key: rig.data[key] for key in extension_records}
    assert limb_ik._armature_digest(rig) == digest
    limb_ik._validate_inventory(rig)


def test_build_and_remove_rollback():
    rig = fixture()
    before = poses(rig)
    names = set(rig.data.bones.keys())
    objects, meshes = set(bpy.data.objects), set(bpy.data.meshes)
    add = root._add_widget
    def fail_widget(*args, **kwargs):
        add(*args, **kwargs)
        raise RuntimeError('Injected root widget failure')
    root._add_widget = fail_widget
    try:
        try:
            root.build(bpy.context, rig)
        except RuntimeError as exc:
            assert 'Injected' in str(exc)
        else:
            raise AssertionError('Root build fault was ignored')
    finally:
        root._add_widget = add
    assert set(rig.data.bones.keys()) == names and not root.get_record(rig)
    assert set(bpy.data.objects) == objects and set(bpy.data.meshes) == meshes
    root._verify_pose(rig, before)
    rec = root.build(bpy.context, rig)
    rig.pose.bones[rec['master']].rotation_euler = (.08, -.11, .05)
    rig.pose.bones[rec['master']][root.SCALE_PROPERTY] = 1.11
    before = poses(rig)
    saved = rig.data[root.RECORD_KEY]
    finish = bone_collections.finish_rig_edit
    def fail_finish(*_args, **_kwargs):
        raise RuntimeError('Injected root final layout failure')
    bone_collections.finish_rig_edit = fail_finish
    try:
        try:
            root.remove(bpy.context, rig)
        except RuntimeError as exc:
            assert 'Injected' in str(exc)
        else:
            raise AssertionError('Root removal fault was ignored')
    finally:
        bone_collections.finish_rig_edit = finish
    assert rig.data[root.RECORD_KEY] == saved
    root.validate(rig)
    root._verify_pose(rig, before)
    limb_ik._validate_inventory(rig)


def test_dependencies_uniform_scale_and_reopen():
    rig = fixture()
    rec = root.build(bpy.context, rig)
    control = rig.pose.bones[rec['master']]
    artist = bpy.data.objects.new('Root artist dependency', None)
    bpy.context.scene.collection.objects.link(artist)
    artist.parent, artist.parent_type, artist.parent_bone = rig, 'BONE', rec['master']
    spine_tests.refusal(lambda: root.remove(bpy.context, rig), 'object follows')
    artist.parent = None
    control[root.SCALE_PROPERTY] = 1.25
    update(rig)
    assert max(abs(value - 1.25) for value in control.scale) < 1e-6
    assert tuple(control.lock_scale) == (True, True, True)
    control.keyframe_insert('location', frame=1)
    spine_tests.refusal(lambda: root.remove(bpy.context, rig), 'animation')
    before = poses(rig)
    name = rig.name
    with tempfile.TemporaryDirectory(prefix='cd-root-') as directory:
        path = os.path.join(directory, 'root.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig = bpy.data.objects[name]
        update(rig)
        assert root.validate(rig) == rec
        root._verify_pose(rig, before)
        limb_ik._validate_inventory(rig)
    con = rig.pose.bones['Hips'].constraints['CD Root Follow']
    con.influence = .5
    spine_tests.refusal(lambda: root.validate(rig), 'constraint')


def test_existing_enhanced_master_reused():
    limb_tests.base.ensure_registered()
    rig, _key, _limb = limb_tests.build('ROLL_DECOUPLED')
    before = poses(rig)
    names = set(rig.data.bones.keys())
    result = root.build(bpy.context, rig)
    assert result == {'master': limb_ik.MASTER_NAME, 'reused': True}
    assert root.get_record(rig) is None and set(rig.data.bones.keys()) == names
    assert root.control_name(rig) == limb_ik.MASTER_NAME
    root._verify_pose(rig, before)


def test_manual_and_fk_removal():
    limb_tests.base.ensure_registered()
    for selected in ('LEFT_ARM', 'LEFT_LEG'):
        for mode in ('IK', 'FK'):
            rig, key, _data = limb_tests.build('DIRECT_PREROLL', selected)
            assert bpy.ops.character_designer.limb_ik_auto_align_target(action='DISABLE') == {'FINISHED'}
            if mode == 'FK':
                limb_ik_fk.switch_limb(bpy.context, rig, key, mode, keyframe=False)
            rec = root.build(bpy.context, rig)
            control = rig.pose.bones[rec['master']]
            control.location = (.1, -.05, .03)
            control.rotation_euler = (.11, .08, -.12)
            control[root.SCALE_PROPERTY] = 1.08
            current = poses(rig)
            expected = {name: matrix for name, matrix in current.items()
                        if name != rec['master'] and name not in rec['controls']}
            root.remove(bpy.context, rig)
            root._verify_pose(rig, expected)
            inventory = limb_ik._validate_inventory(rig)
            assert limb_ik_fk.mode_for_rig(rig, inventory['rigs'][key]) == mode
            assert not inventory['rigs'][key]['auto_align']


if __name__ == '__main__':
    for test in (test_whole_body_follow_roundtrip_and_removal,
                 test_near_straight_cumulative_removal_preserves_or_rolls_back, test_build_and_remove_rollback,
                 test_dependencies_uniform_scale_and_reopen, test_existing_enhanced_master_reused,
                 test_manual_and_fk_removal):
        test()
        print('PASS', test.__name__, flush=True)
    print('ROOT_CONTROL_TESTS_PASS 6', flush=True)
