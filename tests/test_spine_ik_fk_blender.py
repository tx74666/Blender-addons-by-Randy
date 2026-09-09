"""Native spine IK/FK: true endpoint solving, matching, recovery and strict guards."""
import os
import sys
import tempfile
import math

import bpy
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import spine_ik_fk as spine, torso_controls as torso, eye_controls, limb_ik, limb_ik_fk, bone_collections
import test_torso_controls_blender as torso_tests
import test_limb_ik_blender as base


def update(rig):
    spine._update(bpy.context, rig)


def native(rig):
    update(rig)
    return spine._matrices(rig)


def fixture(count=3, posed=False, eyes=True, connected=True):
    rig, chain = torso_tests.fixture(count=count, posed=posed, feet=True)
    bpy.ops.object.mode_set(mode='EDIT')
    bones = rig.data.edit_bones
    for name in chain[1:]:
        bones[name].use_connect = connected
    head = base.add_bone(bones, 'Head', (0, 0, 1.57), (0, 0, 1.78), bones[chain[-1]])
    for side, sign in (('L', 1), ('R', -1)):
        base.add_bone(bones, 'eye.' + side, (sign*.025, -.01, 1.71), (sign*.04, -.06, 1.71), head)
    bpy.ops.object.mode_set(mode='POSE')
    record = torso.build(bpy.context, rig, chain=chain, hips_name='Hips')
    if posed:
        rig.pose.bones[record['bend']].rotation_euler = (.12, -.05, .035)
        for i, name in enumerate(record['controls'].values()):
            rig.pose.bones[name].rotation_euler = ((.12, -.20, .15, -.04)[i], .025, -.035)
    if eyes:
        eye_controls.build(bpy.context, rig)
    mesh = bpy.data.meshes.new('Spine Weight Fixture')
    mesh.from_pydata([(0, -.05, 1.22), (.05, -.05, 1.35), (-.05, -.05, 1.47)], [], [])
    obj = bpy.data.objects.new('Spine Weight Fixture', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new('Armature', 'ARMATURE').object = rig
    for i, name in enumerate(chain):
        obj.vertex_groups.new(name=name).add([min(i,2)], .7, 'REPLACE')
    update(rig)
    return rig, chain, record


def weights():
    obj = bpy.data.objects['Spine Weight Fixture']
    return (tuple(group.name for group in obj.vertex_groups),
            tuple(tuple((group.group, group.weight) for group in vertex.groups) for vertex in obj.data.vertices))


def refusal(call, phrase=None):
    try:
        call()
    except limb_ik.LimbIKError as exc:
        if phrase:
            assert phrase.casefold() in str(exc).casefold(), str(exc)
        return str(exc)
    raise AssertionError('Expected a safe refusal')


def test_default_fk_and_full_roundtrips():
    for count in (3,4):
        for posed in (False,True):
            rig, chain, original = fixture(count, posed)
            if posed:
                limb_ik_fk.switch_limb(bpy.context, rig, ('ARM','R'), 'FK', keyframe=False)
            before, before_weights = native(rig), weights()
            digest = limb_ik._armature_digest(rig)
            rest = {bone.name: torso._state(bone) for bone in rig.data.bones}
            original_record, eye_record = rig.data[torso.RECORD_KEY], rig.data[eye_controls.RECORD_KEY]
            rig.data.use_mirror_x = True
            rec = spine.build(bpy.context, rig)
            assert spine.build(bpy.context, rig) == rec
            assert spine.mode_for_rig(rig) == 'FK'
            spine._verify_pose(rig, before)
            assert weights() == before_weights and limb_ik._armature_digest(rig) == digest
            assert bpy.context.mode == 'POSE' and rig.data.use_mirror_x
            assert not spine.switch(bpy.context, rig, 'FK')['changed']
            assert len(rec['bones']) == count+2
            endpoint = rig.pose.bones[rec['bones'][f'IK_{count-2}']].constraints['CD Spine Endpoint']
            assert endpoint.type == 'IK' and endpoint.chain_count == count-1 and not endpoint.use_stretch
            assert endpoint.pole_target is None
            spine.switch(bpy.context, rig, 'IK')
            spine._verify_pose(rig, before)
            assert spine.mode_for_rig(rig) == 'IK'
            animation = rig.data.collections_all['Animation']
            assert rec['chest'] in animation.bones and rec['shape'] in animation.bones
            assert not (set(original['controls'].values()) | {original['bend']}) & set(animation.bones.keys())
            target = rig.pose.bones[rec['chest']]
            target.location.x += .015
            target.location.y -= .02
            target.rotation_euler.z += .07
            rig.pose.bones[rec['shape']].rotation_euler = (.06,.05,-.025)
            current = native(rig)
            assert before[chain[0]].to_quaternion().rotation_difference(current[chain[0]].to_quaternion()).angle > .01
            spine.switch(bpy.context, rig, 'FK')
            spine._verify_pose(rig, current)
            assert spine.mode_for_rig(rig) == 'FK'
            assert rec['chest'] not in rig.data.collections_all['Animation'].bones
            assert set(original['controls'].values()) <= set(rig.data.collections_all['Animation'].bones.keys())
            rig.pose.bones[original['controls'][chain[1]]].rotation_euler.x += .035
            current = native(rig)
            spine.switch(bpy.context, rig, 'IK')
            spine._verify_pose(rig, current)
            result = spine.remove(bpy.context, rig)
            assert result == {'bones_removed': count+2, 'pose_preserved': True, 'mode': 'FK'}
            spine._verify_pose(rig, current)
            assert spine.get_record(rig) is None and set(rig.data.bones.keys()) == set(rest)
            for name, state in rest.items():
                assert torso._same_rest(rig.data.bones[name], state)
            assert rig.data[torso.RECORD_KEY] == original_record and rig.data[eye_controls.RECORD_KEY] == eye_record
            assert weights() == before_weights and limb_ik._armature_digest(rig) == digest
            limb_ik._validate_inventory(rig)
            print('SPINE_ROUNDTRIP', count, posed, flush=True)


def test_true_solver_shape_reach_and_excursion_return():
    for count in (3,4):
        rig, chain, original = fixture(count)
        rec = spine.build(bpy.context, rig)
        spine.switch(bpy.context, rig, 'IK')
        before = native(rig)
        target, shape = rig.pose.bones[rec['chest']], rig.pose.bones[rec['shape']]
        target_before, shape_before = target.matrix_basis.copy(), shape.matrix_basis.copy()
        target.location.y -= .035
        shape.rotation_euler.x = .07
        update(rig)
        assert (rig.pose.bones[chain[-1]].head-target.head).length < 2e-4
        assert (rig.pose.bones[chain[1]].head-before[chain[1]].translation).length > .01
        target.matrix_basis, shape.matrix_basis = target_before, shape_before
        update(rig)
        spine._verify_pose(rig,before)
        target.location.y += 1.0
        update(rig)
        assert (rig.pose.bones[chain[-1]].head-target.head).length > .5
        for name in chain:
            pb = rig.pose.bones[name]
            assert abs((pb.tail-pb.head).length-pb.bone.length) < 2e-5
        unreachable_pose = native(rig)
        spine.switch(bpy.context,rig,'FK')
        spine._verify_pose(rig,unreachable_pose)


def test_blended_pose_recovery_and_refusal():
    for count in (3, 4):
        for endpoint in ('FK', 'IK'):
            rig, chain, original = fixture(count, posed=True)
            rec = spine.build(bpy.context, rig)
            spine.switch(bpy.context, rig, 'IK')
            chest = rig.pose.bones[rec['chest']]
            chest.location.x += .035
            chest.location.y -= .025
            rig.pose.bones[rec['shape']].rotation_euler = (.08, -.04, .035)
            chest[spine.PROPERTY] = .5
            desired = native(rig)
            members = spine.collection_members(rig)
            assert not members['hidden_fk'] and members['visible'] == {rec['chest'], rec['shape']}
            result = spine.switch(bpy.context, rig, endpoint)
            assert result['mode'] == endpoint
            spine._verify_pose(rig, desired)
    rig, chain, original = fixture(posed=True, connected=False)
    rec = spine.build(bpy.context, rig)
    chest = rig.pose.bones[rec['chest']]
    rig.pose.bones[original['controls'][chain[1]]].location.x += .04
    chest[spine.PROPERTY] = .5
    desired, before = native(rig), spine._pose_snapshot(rig)
    refusal(lambda: spine.switch(bpy.context, rig, 'IK'), 'disconnected')
    assert chest[spine.PROPERTY] == .5
    spine._verify_pose(rig, desired)
    assert all(max(abs(rig.pose.bones[name].matrix_basis[i][j] - basis[i][j]) for i in range(4) for j in range(4)) < 1e-6
               for name, (_mode, basis) in before.items())
    desired[chain[1]][2][1] = math.nan
    refusal(lambda: spine._verify_pose(rig, desired), 'non-finite')


def test_matched_endpoints_under_transformed_root():
    from character_designer import root_control
    rig, chain, original = fixture(posed=True)
    rec = spine.build(bpy.context, rig)
    root = root_control.build(bpy.context, rig)
    master = rig.pose.bones[root['master']]
    master.location = (.12, -.08, .04)
    master.rotation_euler = (.11, -.09, .2)
    master[root_control.SCALE_PROPERTY] = 1.15
    update(rig)
    inventory = limb_ik._validate_inventory(rig)
    for key, data in inventory['rigs'].items():
        for endpoint in ('FK', 'IK'):
            target = rig.pose.bones[data['target'].name]
            target.location.x += .007
            target[limb_ik_fk.PROPERTY] = .5
            desired = native(rig)
            limb_ik_fk.switch_limb(bpy.context, rig, key, endpoint, keyframe=False)
            spine._verify_pose(rig, desired)
    for endpoint in ('FK', 'IK'):
        chest = rig.pose.bones[rec['chest']]
        chest.location.x += .015
        rig.pose.bones[rec['shape']].rotation_euler.x += .045
        chest[spine.PROPERTY] = .5
        desired = native(rig)
        spine.switch(bpy.context, rig, endpoint)
        spine._verify_pose(rig, desired)
    assert master.location == Vector((.12, -.08, .04))
    assert master[root_control.SCALE_PROPERTY] == 1.15
    root_control.validate(rig)


def test_impossible_match_and_switch_rollback():
    rig, chain, original = fixture(posed=True, connected=False)
    rec = spine.build(bpy.context,rig)
    # An artist-unlocked FK translation makes a disconnected chain IK cannot reproduce.
    rig.pose.bones[original['controls'][chain[1]]].location.x += .08
    desired, bases = native(rig), spine._pose_snapshot(rig)
    refusal(lambda:spine.switch(bpy.context,rig,'IK'),'without a jump')
    assert spine.mode_for_rig(rig)=='FK'
    spine._verify_pose(rig,desired)
    for name, (_mode,basis) in bases.items():
        assert max(abs(rig.pose.bones[name].matrix_basis[i][j]-basis[i][j]) for i in range(4) for j in range(4)) < 2e-6
    rig.pose.bones[original['controls'][chain[1]]].location.x -= .08
    desired = native(rig)
    verify = spine._verify_pose
    def fail(*_args):raise limb_ik.LimbIKError('Injected matching failure')
    spine._verify_pose=fail
    try:
        refusal(lambda:spine.switch(bpy.context,rig,'IK'),'Injected')
    finally:
        spine._verify_pose=verify
    spine._verify_pose(rig,desired)
    assert spine.mode_for_rig(rig)=='FK'
    spine.validate(rig)


def test_build_and_late_removal_rollback():
    rig, chain, original = fixture(posed=True)
    before=native(rig)
    objects,meshes,bones=set(bpy.data.objects.keys()),set(bpy.data.meshes.keys()),set(rig.data.bones.keys())
    drivers={curve.data_path for curve in rig.animation_data.drivers}
    add_widget=spine._add_widget
    def fail(*args,**kwargs):
        add_widget(*args,**kwargs)
        raise RuntimeError('Injected widget failure')
    spine._add_widget=fail
    try:
        try:spine.build(bpy.context,rig)
        except RuntimeError as exc:assert 'Injected' in str(exc)
        else:raise AssertionError('Build failed to roll back')
    finally:spine._add_widget=add_widget
    assert set(bpy.data.objects.keys())==objects and set(bpy.data.meshes.keys())==meshes
    assert set(rig.data.bones.keys())==bones and spine.get_record(rig) is None
    assert {curve.data_path for curve in rig.animation_data.drivers}==drivers
    spine._verify_pose(rig,before)
    rec=spine.build(bpy.context,rig)
    spine.switch(bpy.context,rig,'IK')
    rig.pose.bones[rec['chest']].location.x+=.015
    desired=native(rig)
    saved=rig.data[spine.RECORD_KEY]
    switch_metadata=rig.pose.bones[rec['chest']].id_properties_ui(spine.PROPERTY).as_dict()
    old_widgets={role:bpy.data.objects[entry['object']].as_pointer() for role,entry in rec['widgets'].items()}
    finish=bone_collections.finish_rig_edit
    def fail_finish(*_args,**_kwargs):raise RuntimeError('Injected layout failure')
    bone_collections.finish_rig_edit=fail_finish
    try:
        try:spine.remove(bpy.context,rig)
        except RuntimeError as exc:assert 'Injected' in str(exc)
        else:raise AssertionError('Removal failed to roll back')
    finally:bone_collections.finish_rig_edit=finish
    assert rig.data[spine.RECORD_KEY]==saved and spine.mode_for_rig(rig)=='IK'
    assert rig.pose.bones[rec['chest']].id_properties_ui(spine.PROPERTY).as_dict()==switch_metadata
    assert old_widgets=={role:bpy.data.objects[entry['object']].as_pointer() for role,entry in rec['widgets'].items()}
    spine.validate(rig)
    spine._verify_pose(rig,desired)
    spine.remove(bpy.context,rig)
    spine._verify_pose(rig,desired)


def test_animation_dependency_and_tamper_guards():
    rig,chain,original=fixture()
    rec=spine.build(bpy.context,rig)
    saved=rig.data[spine.RECORD_KEY]
    bpy.context.scene.tool_settings.use_keyframe_insert_auto=True
    try:
        refusal(lambda:spine.switch(bpy.context,rig,'IK'),'Auto Key')
        refusal(lambda:spine.reset(bpy.context,rig),'Auto Key')
    finally:bpy.context.scene.tool_settings.use_keyframe_insert_auto=False
    refusal(lambda:spine.switch(bpy.context,rig,'IK',keyframe=True),'key animation')
    obj=bpy.data.objects.new('Artist attachment',None)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent,obj.parent_type,obj.parent_bone=rig,'BONE',rec['chest']
    refusal(lambda:spine.remove(bpy.context,rig),'object follows')
    obj.parent=None
    con=obj.constraints.new('COPY_TRANSFORMS');con.target,con.subtarget=rig,rec['shape']
    refusal(lambda:spine.remove(bpy.context,rig),'object constraint')
    obj.constraints.remove(con)
    curve=spine._find_driver(rig,rec['drivers'][0]['path'])
    curve.driver.expression='0.5'
    refusal(lambda:spine.switch(bpy.context,rig,'IK'),'driver')
    curve.driver.expression=spine.PROPERTY
    rig.pose.bones[rec['bones']['IK_0']].ik_stretch=.1
    refusal(lambda:spine.remove(bpy.context,rig),'stretch')
    rig.pose.bones[rec['bones']['IK_0']].ik_stretch=0
    endpoint=rig.pose.bones[rec['bones'][f'IK_{len(chain)-2}']].constraints['CD Spine Endpoint']
    endpoint.use_location=False
    refusal(lambda:spine.switch(bpy.context,rig,'IK'),'edited')
    endpoint.use_location=True
    rig.pose.bones[rec['bones']['IK_0']].ik_stiffness_x=.9
    refusal(lambda:spine.remove(bpy.context,rig),'settings')
    rig.pose.bones[rec['bones']['IK_0']].ik_stiffness_x=0
    source=rig.pose.bones[chain[0]]
    source.constraints.move(1,0)
    refusal(lambda:spine.switch(bpy.context,rig,'IK'),'order')
    source.constraints.move(0,1)
    rig.pose.bones[original['controls'][chain[0]]].keyframe_insert('rotation_euler',frame=1)
    refusal(lambda:spine.switch(bpy.context,rig,'IK'),'animation')
    refusal(lambda:spine.remove(bpy.context,rig),'animation')
    refusal(lambda:spine.reset(bpy.context,rig),'animation')
    assert rig.data[spine.RECORD_KEY]==saved


def test_explicit_reset_to_native_neutral_and_rollback():
    for count in (3,4):
        for mode in ('FK','IK','BLEND'):
            rig,chain,original=fixture(count=count,posed=True)
            rec=spine.build(bpy.context,rig)
            if mode!='FK':
                spine.switch(bpy.context,rig,'IK')
                rig.pose.bones[rec['chest']].location.x+=.015
                rig.pose.bones[rec['shape']].rotation_euler.x=.07
                if mode=='BLEND':rig.pose.bones[rec['chest']][spine.PROPERTY]=.4
            eye=eye_controls.get_record(rig)
            rig.pose.bones[eye['master']].location.x+=.012
            update(rig)
            expected=spine._neutral_matrices(rig,rec)
            old_hips=rig.pose.bones[rec['hips']].matrix.copy()
            before=spine._pose_snapshot(rig)
            old_weights=weights()
            rest={bone.name:torso._state(bone) for bone in rig.data.bones}
            result=spine.reset(bpy.context,rig)
            assert result['native_neutral'] and result['mode']==mode
            spine._verify_pose(rig,expected)
            spine._verify_pose(rig,{rec['hips']:old_hips})
            affected=set(rec['torso_bones'])|set(rec['bones'].values())
            for name,(_rotation_mode,basis) in before.items():
                wanted=Matrix.Identity(4) if name in affected else basis
                assert max(abs(rig.pose.bones[name].matrix_basis[i][j]-wanted[i][j]) for i in range(4) for j in range(4))<2e-6
            assert weights()==old_weights
            for name,state in rest.items():assert torso._same_rest(rig.data.bones[name],state)
            print('SPINE_RESET',count,mode,result['error'],flush=True)
    # Failure after reset must recover both old hidden offsets and visible inputs.
    rig,chain,original=fixture(posed=True)
    rec=spine.build(bpy.context,rig)
    spine.switch(bpy.context,rig,'IK')
    desired=native(rig)
    before=spine._pose_snapshot(rig)
    verify=spine._verify_pose
    def fail(*_args):raise limb_ik.LimbIKError('Injected reset verification failure')
    spine._verify_pose=fail
    try:refusal(lambda:spine.reset(bpy.context,rig),'Injected')
    finally:spine._verify_pose=verify
    assert spine.mode_for_rig(rig)=='IK'
    spine._verify_pose(rig,desired)
    for name,(_mode,basis) in before.items():
        assert max(abs(rig.pose.bones[name].matrix_basis[i][j]-basis[i][j]) for i in range(4) for j in range(4))<2e-6


def test_save_reopen_and_contiguity_guard():
    rig,chain,original=fixture(count=4,posed=True)
    rec=spine.build(bpy.context,rig)
    spine.switch(bpy.context,rig,'IK')
    rig.pose.bones[rec['shape']].rotation_euler=(.07,-.03,.025)
    rig.pose.bones[rec['chest']].location.x+=.02
    desired=native(rig)
    name=rig.name
    with tempfile.TemporaryDirectory(prefix='cd-spine-ik-') as directory:
        path=os.path.join(directory,'spine.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig=bpy.data.objects[name]
        update(rig)
        assert spine.validate(rig)==rec and spine.mode_for_rig(rig)=='IK'
        spine._verify_pose(rig,desired)
        spine.switch(bpy.context,rig,'FK')
        spine._verify_pose(rig,desired)
        spine.remove(bpy.context,rig)
    rig,chain=torso_tests.fixture()
    bpy.ops.object.mode_set(mode='EDIT')
    rig.data.edit_bones[chain[1]].use_connect=False
    rig.data.edit_bones[chain[1]].head.z+=.01
    bpy.ops.object.mode_set(mode='POSE')
    torso.build(bpy.context,rig,chain=chain,hips_name='Hips')
    refusal(lambda:spine.build(bpy.context,rig),'consecutive')


if __name__=='__main__':
    tests=(test_default_fk_and_full_roundtrips,test_true_solver_shape_reach_and_excursion_return,
           test_blended_pose_recovery_and_refusal,
           test_matched_endpoints_under_transformed_root,
           test_impossible_match_and_switch_rollback,test_build_and_late_removal_rollback,
           test_animation_dependency_and_tamper_guards,test_explicit_reset_to_native_neutral_and_rollback,
           test_save_reopen_and_contiguity_guard)
    for test in tests:
        test()
        print('PASS',test.__name__,flush=True)
    print('SPINE_IK_FK_PASSED',len(tests),flush=True)
