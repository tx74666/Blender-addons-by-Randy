"""Native eye controls: gaze, neutral coordinates, recovery, persistence and guards."""
import os
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'addons'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))
from character_designer import eye_controls as eyes, limb_ik, limb_ik_fk
import test_limb_ik_blender as base


def update(rig):
    limb_ik_fk._update(bpy.context, rig)


def poses(rig, names=None):
    update(rig)
    return {pb.name: pb.matrix.copy() for pb in rig.pose.bones if names is None or pb.name in names}


def fixture(posed=False, divergent=True):
    base.ensure_registered()
    base.reset_scene()
    rig = base.make_humanoid(roll_offset=0.27)
    bpy.ops.object.mode_set(mode='EDIT')
    eb = rig.data.edit_bones
    head = base.add_bone(eb, 'Head', (0, .047589943, 1.760459900), (0, .049121048, 1.912583828), eb['Chest'])
    for side, sign in (('L', 1), ('R', -1)):
        eye = base.add_bone(eb, 'eye.' + side,
                            (sign * .021800358, .026740108, 1.849194288),
                            (sign * (.048999600 if divergent else .021800358), -.021604294, 1.849720716), head)
        base.add_bone(eb, 'pupil.' + side, eye.tail, eye.tail + Vector((0, -.012, 0)), eye)
    bpy.ops.object.mode_set(mode='POSE')
    _, settings = base.analyze(rig)
    settings.build_method = 'DIRECT_PREROLL'
    assert bpy.ops.character_designer.limb_ik_direct_preroll_check() == {'FINISHED'}
    assert bpy.ops.character_designer.limb_ik_build_all() == {'FINISHED'}, settings.last_message
    # A disposable weighted mesh catches accidental writes to original eye groups.
    mesh = bpy.data.meshes.new('Eye Weight Fixture')
    mesh.from_pydata([(x, -.02, 1.85) for x in (.018, .025, -.018, -.025)], [], [])
    obj = bpy.data.objects.new('Eye Weight Fixture', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new('Armature', 'ARMATURE').object = rig
    obj.vertex_groups.new(name='eye.L').add([0, 1], 1.0, 'REPLACE')
    obj.vertex_groups.new(name='eye.R').add([2, 3], 1.0, 'REPLACE')
    if posed:
        rig.location, rig.rotation_euler, rig.scale = (.2, -.1, .3), (.1, -.08, .07), (.782315731,) * 3
        for name in ('Hips', 'Chest', 'Head'):
            pb = rig.pose.bones[name]
            pb.rotation_mode = 'XYZ'
            pb.rotation_euler = (.07, -.035, .12)
        for name, rotation in (('eye.L', (.17, .09, -.13)), ('eye.R', (-.08, .11, .19))):
            rig.pose.bones[name].rotation_mode = 'XYZ'
            rig.pose.bones[name].rotation_euler = rotation
    update(rig)
    return rig


def weights():
    mesh = bpy.data.objects['Eye Weight Fixture']
    return (tuple(group.name for group in mesh.vertex_groups),
            tuple((vertex.index, tuple((group.group, group.weight) for group in vertex.groups))
                  for vertex in mesh.data.vertices))


def expect_refusal(call, phrase=None):
    try:
        call()
    except limb_ik.LimbIKError as exc:
        if phrase:
            assert phrase.casefold() in str(exc).casefold(), str(exc)
        return str(exc)
    raise AssertionError('Expected a safe refusal')


def test_rest_and_posed_roundtrip():
    for posed in (False, True):
        for divergent in (False, True):
            rig = fixture(posed, divergent)
            before, digest, weight_before = poses(rig), limb_ik._armature_digest(rig), weights()
            rest = {bone.name: eyes._state(bone) for bone in rig.data.bones}
            rig.data.use_mirror_x = True
            active = rig.data.bones['eye.L']
            rig.data.bones.active = active
            rig.pose.bones['eye.L'].select = True
            record = eyes.build(bpy.context, rig)
            assert eyes.build(bpy.context, rig) == record
            assert eyes.resolve_eyes(bpy.context, rig) == ('Head', 'eye.L', 'eye.R')
            assert bpy.context.mode == 'POSE' and rig.data.bones.active.name == 'eye.L'
            assert rig.data.use_mirror_x and rig.pose.bones['eye.L'].select
            eyes._verify_pose(rig, before)
            assert weights() == weight_before
            assert limb_ik._armature_digest(rig) == digest
            for name, state in rest.items():
                assert eyes._same_rest(rig.data.bones[name], state)
            master = rig.pose.bones[record['master']]
            assert master.location.length < 1e-6
            assert master.bone.parent.name == 'Head'
            assert len(record['bones']) == 3
            for side in ('L', 'R'):
                target = rig.pose.bones[record['targets'][side]]
                assert target.bone.parent.name == record['master']
                assert Vector(target.rotation_euler).length < 2e-6 and (target.scale - Vector((1, 1, 1))).length < 2e-6
                assert not target.bone.use_deform and target.custom_shape
                if not posed:
                    assert target.location.length < 2e-6
                else:
                    assert target.location.length > .005
            master.location = (.045, 0, .018)
            rig.pose.bones[record['targets']['R']].location.z -= .02
            desired = poses(rig, before)
            assert before['eye.L'].to_quaternion().rotation_difference(desired['eye.L'].to_quaternion()).angle > .05
            result = eyes.remove(bpy.context, rig)
            assert result == {'bones_removed': 3, 'pose_preserved': True}
            eyes._verify_pose(rig, desired)
            assert eyes.get_record(rig) is None and set(rig.pose.bones.keys()) == set(before)
            assert limb_ik._armature_digest(rig) == digest and weights() == weight_before
            assert rig.data.use_mirror_x and bpy.context.mode == 'POSE'
            limb_ik._validate_inventory(rig)
            print('EYE_ROUNDTRIP', posed, divergent, flush=True)


def test_shared_individual_neutral_and_head_follow():
    rig = fixture(posed=True)
    record = eyes.build(bpy.context, rig)
    before = poses(rig)
    master = rig.pose.bones[record['master']]
    master.location.x += .04
    shared = poses(rig)
    for side in ('L', 'R'):
        assert before['eye.' + side].to_quaternion().rotation_difference(shared['eye.' + side].to_quaternion()).angle > .03
    rig.pose.bones[record['targets']['L']].location.z += .025
    individual = poses(rig)
    assert shared['eye.L'].to_quaternion().rotation_difference(individual['eye.L'].to_quaternion()).angle > .03
    assert shared['eye.R'].to_quaternion().rotation_difference(individual['eye.R'].to_quaternion()).angle < 1e-5
    for name in record['bones'].values():
        rig.pose.bones[name].location = (0, 0, 0)
    update(rig)
    head = rig.pose.bones['Head']
    rest_to_pose = head.matrix @ head.bone.matrix_local.inverted()
    for source in record['sources']:
        bone = rig.data.bones[source]
        expected = (rest_to_pose.to_3x3() @ (bone.tail_local - bone.head_local)).normalized()
        current = rig.pose.bones[source].matrix.to_3x3().col[1].normalized()
        assert current.dot(expected) > .999999, (source, current, expected)
    relative = {name: head.matrix.inverted() @ rig.pose.bones[name].matrix for name in record['bones'].values()}
    head.rotation_euler = (.35, -.22, -.18)
    update(rig)
    for name, expected in relative.items():
        current = head.matrix.inverted() @ rig.pose.bones[name].matrix
        assert max(abs(current[i][j] - expected[i][j]) for i in range(4) for j in range(4)) < 2e-5
    for source, side in zip(record['sources'], ('L', 'R')):
        pb = rig.pose.bones[source]
        direction = (rig.pose.bones[record['targets'][side]].head - pb.head).normalized()
        assert pb.matrix.to_3x3().col[1].normalized().dot(direction) > .999999


def test_build_rollback_and_remove_match_rollback():
    rig = fixture(posed=True)
    before = poses(rig)
    bones, objects, meshes, collections = (set(rig.data.bones.keys()), set(bpy.data.objects.keys()),
                                           set(bpy.data.meshes.keys()), set(bpy.data.collections.keys()))
    add_widget = eyes._add_widget
    def fail(*args, **kwargs):
        add_widget(*args, **kwargs)
        raise RuntimeError('Injected eye widget failure')
    eyes._add_widget = fail
    try:
        try:
            eyes.build(bpy.context, rig)
        except RuntimeError as exc:
            assert 'Injected' in str(exc)
        else:
            raise AssertionError('Build did not roll back')
    finally:
        eyes._add_widget = add_widget
    assert set(rig.data.bones.keys()) == bones
    assert set(bpy.data.objects.keys()) == objects and set(bpy.data.meshes.keys()) == meshes
    assert set(bpy.data.collections.keys()) == collections and eyes.get_record(rig) is None
    eyes._verify_pose(rig, before)
    record = eyes.build(bpy.context, rig)
    rig.pose.bones[record['master']].location.z += .03
    desired = poses(rig)
    saved = rig.data[eyes.RECORD_KEY]
    verify = eyes._verify_pose
    def fail_match(*_args):
        raise limb_ik.LimbIKError('Injected eye native match failure')
    eyes._verify_pose = fail_match
    try:
        expect_refusal(lambda: eyes.remove(bpy.context, rig), 'Injected')
    finally:
        eyes._verify_pose = verify
    assert rig.data[eyes.RECORD_KEY] == saved
    eyes.validate(rig)
    eyes._verify_pose(rig, desired)
    # Late failures after bone deletion must restore the same widgets and controls.
    from character_designer import bone_collections
    ctrl = rig.pose.bones[record['master']]
    ctrl['artist_note'] = {'value': [1, 2, 3]}
    ctrl.custom_shape_scale_xyz = (1.2, 1.2, 1.2)
    ctrl.color.custom.show_colored_constraints = True
    widget_ids = {role: bpy.data.objects[entry['object']].as_pointer()
                  for role, entry in record['widgets'].items()}
    finish = bone_collections.finish_rig_edit
    def fail_finish(*_args, **_kwargs):
        raise RuntimeError('Injected eye final layout failure')
    bone_collections.finish_rig_edit = fail_finish
    try:
        try:
            eyes.remove(bpy.context, rig)
        except RuntimeError as exc:
            assert 'Injected' in str(exc)
        else:
            raise AssertionError('Late removal failure did not roll back')
    finally:
        bone_collections.finish_rig_edit = finish
    assert rig.data[eyes.RECORD_KEY] == saved
    eyes.validate(rig)
    eyes._verify_pose(rig, desired)
    ctrl = rig.pose.bones[record['master']]
    assert list(ctrl['artist_note']['value']) == [1, 2, 3]
    assert abs(ctrl.custom_shape_scale_xyz.x - 1.2) < 1e-6
    assert ctrl.color.custom.show_colored_constraints
    assert widget_ids == {role: bpy.data.objects[entry['object']].as_pointer()
                          for role, entry in record['widgets'].items()}
    eyes.remove(bpy.context, rig)


def test_dependencies_and_tampering():
    rig = fixture()
    record = eyes.build(bpy.context, rig)
    saved = rig.data[eyes.RECORD_KEY]
    def refused(phrase=None):
        expect_refusal(lambda: eyes.remove(bpy.context, rig), phrase)
        assert rig.data[eyes.RECORD_KEY] == saved
        assert all(name in rig.data.bones for name in record['bones'].values())
    obj = bpy.data.objects.new('Artist attachment', None)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent, obj.parent_type, obj.parent_bone = rig, 'BONE', record['master']
    refused('object follows')
    obj.parent = None
    con = obj.constraints.new('COPY_TRANSFORMS')
    con.target, con.subtarget = rig, record['targets']['L']
    refused('object constraint')
    obj.constraints.remove(con)
    curve = obj.driver_add('location', 0)
    variable = curve.driver.variables.new()
    variable.type = 'TRANSFORMS'
    variable.targets[0].id = rig
    variable.targets[0].bone_target = record['master']
    refused('driver')
    obj.driver_remove('location', 0)
    aim = rig.pose.bones['eye.L'].constraints['CD Eye Aim']
    aim.influence = .5
    refused('edited')
    aim.influence = 1
    extra = rig.pose.bones[record['master']].constraints.new('LIMIT_LOCATION')
    refused('additional constraints')
    rig.pose.bones[record['master']].constraints.remove(extra)
    original = rig.data.bones[record['targets']['L']][eyes.ROLE_KEY]
    rig.data.bones[record['targets']['L']][eyes.ROLE_KEY] = 'ARTIST'
    refused('structurally')
    rig.data.bones[record['targets']['L']][eyes.ROLE_KEY] = original
    bpy.ops.object.mode_set(mode='EDIT')
    rig.data.edit_bones['pupil.L'].parent = rig.data.edit_bones[record['master']]
    bpy.ops.object.mode_set(mode='POSE')
    refused('another bone')
    bpy.ops.object.mode_set(mode='EDIT')
    rig.data.edit_bones['pupil.L'].parent = rig.data.edit_bones['eye.L']
    bpy.ops.object.mode_set(mode='POSE')
    bpy.data.collections[record['widget_collection']].objects.link(obj)
    refused('artist data')
    bpy.data.collections[record['widget_collection']].objects.unlink(obj)
    widget = rig.pose.bones[record['master']].custom_shape
    rig.pose.bones['pupil.L'].custom_shape = widget
    refused('shared')
    rig.pose.bones['pupil.L'].custom_shape = None
    duplicate = rig.copy()
    bpy.context.scene.collection.objects.link(duplicate)
    refused('single-user')
    bpy.data.objects.remove(duplicate, do_unlink=True)
    rig.pose.bones[record['targets']['R']].keyframe_insert('location', frame=1)
    refused('animation')


def test_native_constraints_animation_and_resolution_guards():
    rig = fixture()
    before = set(rig.data.bones.keys())
    con = rig.pose.bones['eye.L'].constraints.new('LIMIT_ROTATION')
    expect_refusal(lambda: eyes.build(bpy.context, rig), 'constraints')
    rig.pose.bones['eye.L'].constraints.remove(con)
    expect_refusal(lambda: eyes.build(bpy.context, rig, left_name='eye.R'), 'distinct')
    bpy.ops.object.mode_set(mode='EDIT')
    rig.data.edit_bones['eye.R'].parent = rig.data.edit_bones['Chest']
    bpy.ops.object.mode_set(mode='POSE')
    expect_refusal(lambda: eyes.build(bpy.context, rig), 'Head')
    bpy.ops.object.mode_set(mode='EDIT')
    rig.data.edit_bones['eye.R'].parent = rig.data.edit_bones['Head']
    bpy.ops.object.mode_set(mode='POSE')
    rig.pose.bones['eye.L'].keyframe_insert('rotation_quaternion', frame=1)
    expect_refusal(lambda: eyes.build(bpy.context, rig), 'animation')
    assert set(rig.data.bones.keys()) == before


def test_save_reopen_and_source_rest_guard():
    rig = fixture(posed=True)
    record = eyes.build(bpy.context, rig)
    rig.pose.bones[record['master']].location.z += .03
    before, rig_name = poses(rig), rig.name
    with tempfile.TemporaryDirectory(prefix='cd-eye-') as directory:
        path = os.path.join(directory, 'eye-controls.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig = bpy.data.objects[rig_name]
        update(rig)
        assert eyes.validate(rig) == record
        eyes._verify_pose(rig, before)
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['eye.L'].tail.x += .002
        bpy.ops.object.mode_set(mode='POSE')
        expect_refusal(lambda: eyes.remove(bpy.context, rig), 'original structure')


if __name__ == '__main__':
    tests = (test_rest_and_posed_roundtrip, test_shared_individual_neutral_and_head_follow,
             test_build_rollback_and_remove_match_rollback, test_dependencies_and_tampering,
             test_native_constraints_animation_and_resolution_guards, test_save_reopen_and_source_rest_guard)
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('EYE_CONTROLS_PASSED', len(tests), flush=True)
