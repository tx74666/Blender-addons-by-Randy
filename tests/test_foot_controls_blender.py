"""Reverse-foot pivots, toe isolation, ownership, and atomic build validation."""
import math
import os
import sys
import tempfile

import bpy
from mathutils import Euler, Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'addons'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))
from character_designer import foot_controls as feet, limb_ik, limb_ik_fk
import test_limb_ik_blender as base
from test_limb_ik_fk_blender import build as fixture, update


def check_pivots(method, selected):
    rig, key, data = fixture(method, selected, toes=True)
    side = key[1]
    native_foot, native_toe = f'foot.{side}', f'toe.{side}'
    old_pose = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
    rest = {b.name: (b.head_local.copy(), b.tail_local.copy(), b.matrix_local.copy()) for b in rig.data.bones}
    target_basis = rig.pose.bones[data['target'].name].matrix_basis.copy()
    record = feet.build(bpy.context, rig, key, toe_name=native_toe)
    assert feet.build(bpy.context, rig, key) == record  # Idempotent.
    feet.validate(rig, limb_ik._validate_inventory(rig))
    for name, matrix in old_pose.items():
        assert (rig.pose.bones[name].head - matrix.translation).length < 3e-4
        assert limb_ik._rotation_error(rig.pose.bones[name].matrix, matrix) < 3e-3
    assert rig.pose.bones[record['target']].matrix_basis == target_basis
    for name, values in rest.items():
        assert rig.data.bones[name].head_local == values[0]
        assert rig.data.bones[name].tail_local == values[1]
        assert limb_ik._rotation_error(rig.data.bones[name].matrix_local, values[2]) < 1e-5
    roll = rig.pose.bones[record['roll']]
    toe = rig.pose.bones[record['toe_control']]
    ankle = rig.pose.bones[native_foot]
    native = rig.pose.bones[native_toe]
    before = {'ankle': ankle.head.copy(), 'ball': native.head.copy(), 'tip': native.tail.copy()}
    roll.rotation_euler.x = 0.30
    update(rig)
    assert ankle.head.z > before['ankle'].z + 0.005, (method, side, ankle.head, before)
    assert (native.head - before['ball']).length < 1e-4
    assert (native.tail - before['tip']).length < 1e-4
    roll.rotation_euler.x = 1.0
    update(rig)
    assert (native.tail - before['tip']).length < 2e-4
    assert native.head.z > before['ball'].z + 0.002
    roll.rotation_euler.x = -0.25
    update(rig)
    assert native.tail.z > before['tip'].z + 0.005
    roll.rotation_euler.x = 0.0
    update(rig)
    ankle_pose = ankle.matrix.copy()
    toe.rotation_euler.x = 0.25
    update(rig)
    assert (ankle.head - ankle_pose.translation).length < 1e-5
    assert limb_ik._rotation_error(ankle.matrix, ankle_pose) < 1e-5
    assert (native.head - before['ball']).length < 1e-4
    assert (native.tail - before['tip']).length > 0.01
    desired = {name: rig.pose.bones[name].matrix.copy() for name in (*record['chain'], native_toe)}
    feet.remove(bpy.context, rig, key)
    limb_ik_fk._verify(rig, desired)
    assert feet.get_record(rig, key) is None
    assert not feet.owned_bone_names(rig)
    assert not any(o.get(feet.OWNER_KEY) == feet.OWNER_VALUE for o in bpy.data.objects)
    assert set(rest) == set(rig.data.bones.keys())
    limb_ik._validate_inventory(rig)


def test_pivots_both_sides_and_schemas():
    for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
        for selected in ('LEFT_LEG', 'RIGHT_LEG'):
            check_pivots(method, selected)
            print('FOOT_PIVOT', method, selected, flush=True)


def test_add_in_arbitrary_pose():
    for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
        rig, key, data = fixture(method, 'LEFT_LEG', toes=True)
        target = rig.pose.bones[data['target'].name]
        target.location += Vector((0.03, -0.04, 0.02))
        target.rotation_euler = (0.12, -0.06, 0.09)
        update(rig)
        rig.data.use_mirror_x = True
        desired = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
        saved_basis = target.matrix_basis.copy()
        feet.build(bpy.context, rig, key, toe_name='toe.L')
        limb_ik_fk._verify(rig, desired)
        assert target.matrix_basis == saved_basis
        assert rig.data.use_mirror_x


def test_build_rollback_and_conflicts():
    rig, key, data = fixture('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
    objects_before = set(bpy.data.objects.keys())
    meshes_before = set(bpy.data.meshes.keys())
    bones_before = set(rig.data.bones.keys())
    pose_before = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
    original = feet._add_widget
    calls = 0
    def fail_second_widget(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('Injected widget construction failure')
        return original(*args, **kwargs)
    feet._add_widget = fail_second_widget
    try:
        try:
            feet.build(bpy.context, rig, key, toe_name='toe.L')
        except RuntimeError as exc:
            assert 'Injected' in str(exc)
        else:
            raise AssertionError('Injected build failure was ignored')
    finally:
        feet._add_widget = original
    assert set(bpy.data.objects.keys()) == objects_before
    assert set(bpy.data.meshes.keys()) == meshes_before
    assert set(rig.data.bones.keys()) == bones_before
    assert not feet.records(rig)
    limb_ik_fk._verify(rig, pose_before)
    limb_ik._validate_inventory(rig)
    toe = rig.pose.bones['toe.L']
    toe.rotation_mode = 'XYZ'
    toe.keyframe_insert(data_path='rotation_euler', frame=1)
    try:
        feet.build(bpy.context, rig, key, toe_name='toe.L')
    except limb_ik.LimbIKError as exc:
        assert 'animation' in str(exc)
    else:
        raise AssertionError('An authored toe Action was replaced')
    assert set(rig.data.bones.keys()) == bones_before


def test_ownership_and_animated_remove():
    rig, key, _data = fixture('ROLL_DECOUPLED', 'LEFT_LEG', toes=True)
    record = feet.build(bpy.context, rig, key, toe_name='toe.L')
    roll = rig.pose.bones[record['roll']]
    roll.keyframe_insert(data_path='rotation_euler', frame=1)
    names = set(rig.data.bones.keys())
    try:
        feet.remove(bpy.context, rig, key)
    except limb_ik.LimbIKError as exc:
        assert 'animation' in str(exc)
    else:
        raise AssertionError('An animated Foot Roll was removed')
    assert set(rig.data.bones.keys()) == names
    curve = next(c for c in rig.animation_data.drivers if c.data_path == record['drivers'][0]['path'])
    previous = curve.driver.expression
    curve.driver.expression = '0'
    try:
        feet.validate(rig)
    except limb_ik.LimbIKError:
        pass
    else:
        raise AssertionError('Edited owned driver was accepted')
    curve.driver.expression = previous
    feet.validate(rig)


def test_removal_preserves_new_artist_dependencies():
    rig, key, _data = fixture('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
    record = feet.build(bpy.context, rig, key, toe_name='toe.L')
    baseline_record = rig.data[feet.RECORD_KEY]
    baseline_bones = set(rig.data.bones.keys())
    def refused():
        try:
            feet.remove(bpy.context, rig, key)
        except limb_ik.LimbIKError:
            pass
        else:
            raise AssertionError('A foreign dependency was removed silently')
        assert rig.data[feet.RECORD_KEY] == baseline_record
        assert set(rig.data.bones.keys()) == baseline_bones
    artist = bpy.data.objects.new('Artist attachment', None)
    bpy.context.scene.collection.objects.link(artist)
    artist.parent = rig
    artist.parent_type = 'BONE'
    artist.parent_bone = record['roll']
    refused()
    artist.parent = None
    con = artist.constraints.new('ARMATURE')
    target = con.targets.new()
    target.target, target.subtarget = rig, record['roll']
    refused()
    artist.constraints.remove(con)
    artist['follow'] = 0.0
    curve = artist.driver_add('["follow"]')
    variable = curve.driver.variables.new()
    variable.name, variable.type = 'roll', 'SINGLE_PROP'
    variable.targets[0].id = rig
    variable.targets[0].data_path = rig.pose.bones[record['roll']].path_from_id('rotation_euler') + '[0]'
    curve.driver.expression = 'roll'
    refused()
    artist.driver_remove('["follow"]')
    hip = rig.pose.bones['Hips']
    shape_before = hip.custom_shape
    hip.custom_shape = rig.pose.bones[record['roll']].custom_shape
    refused()
    hip.custom_shape = shape_before
    hip.custom_shape_transform = rig.pose.bones[record['roll']]
    refused()
    hip.custom_shape_transform = None
    shape = rig.pose.bones[record['roll']].custom_shape
    duplicate = bpy.data.objects.new('Artist copy of widget mesh', shape.data)
    bpy.context.scene.collection.objects.link(duplicate)
    refused()
    bpy.data.objects.remove(duplicate, do_unlink=True)
    roll = rig.pose.bones[record['roll']]
    con = roll.constraints.new('COPY_LOCATION')
    con.target, con.subtarget = rig, 'Hips'
    refused()
    roll.constraints.remove(con)
    feet.remove(bpy.context, rig, key)
    assert artist in bpy.data.objects.values()


def _display_vertices(rig, roll):
    source = roll.custom_shape_transform or roll
    offset = Matrix.LocRotScale(roll.custom_shape_translation,
                               roll.custom_shape_rotation_euler.to_quaternion(), roll.custom_shape_scale_xyz)
    transform = rig.matrix_world @ source.matrix @ offset
    return [transform @ vertex.co for vertex in roll.custom_shape.data.vertices]


def _paired_shoes(rig, *, bound=True):
    vertices, sides = [], {}
    for side in ('L', 'R'):
        foot, toe = rig.data.bones[f'foot.{side}'], rig.data.bones[f'toe.{side}']
        forward = (toe.tail_local - foot.head_local)
        forward.z = 0
        forward.normalize()
        lateral = forward.cross(Vector((0, 0, 1)))
        sides[side] = list(range(len(vertices), len(vertices) + 8))
        # The right shoe has a much longer heel; fitting the left must not use it.
        for x in (-0.065, 0.065):
            for y in ((-0.15 if side == 'L' else -0.40), 0.48):
                for z in (-0.11, 0.10):
                    vertices.append(foot.head_local + lateral * x + forward * y + Vector((0, 0, z)))
    mesh = bpy.data.meshes.new('Paired shoe reference')
    mesh.from_pydata(vertices, [], [])
    shoe = bpy.data.objects.new('Paired Shoes', mesh)
    bpy.context.scene.collection.objects.link(shoe)
    shoe.matrix_world = rig.matrix_world
    if bound:
        for side, indices in sides.items():
            shoe.vertex_groups.new(name=f'foot.{side}').add(indices, 1.0, 'REPLACE')
        modifier = shoe.modifiers.new('Original shoe binding', 'ARMATURE')
        modifier.object = rig
    return shoe


def _rig_visual_invariants(rig):
    return {'rest': {bone.name: tuple(tuple(row) for row in bone.matrix_local) for bone in rig.data.bones},
            'basis': {bone.name: bone.matrix_basis.copy() for bone in rig.pose.bones},
            'pose': {bone.name: bone.matrix.copy() for bone in rig.pose.bones if bone.bone.use_deform},
            'drivers': [(curve.data_path, curve.array_index, curve.driver.expression)
                        for curve in rig.animation_data.drivers]}


def _assert_visual_only(rig, before):
    after = _rig_visual_invariants(rig)
    assert after['rest'] == before['rest']
    assert after['drivers'] == before['drivers']
    for name, matrix in before['basis'].items():
        assert max(abs(after['basis'][name][i][j] - matrix[i][j]) for i in range(4) for j in range(4)) < 1e-6
    limb_ik_fk._verify(rig, before['pose'])


def test_roll_display_follows_solved_foot_and_fits_paired_shoes():
    for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
        for side in ('L', 'R'):
            rig, key, _data = fixture(method, 'LEFT_LEG' if side == 'L' else 'RIGHT_LEG', toes=True)
            shoe = _paired_shoes(rig)
            record = feet.build(bpy.context, rig, key, shoe=shoe)
            roll = rig.pose.bones[record['roll']]
            assert roll.custom_shape_transform.name == f'foot.{side}'
            assert not feet.has_roll_visual_backup(rig, key)
            assert record['roll_visual_fit']['source'] == 'SHOE'
            expected_heel = -0.15 if side == 'L' else -0.40
            assert abs(record['roll_visual_fit']['minimum'][1] - expected_heel) < 2e-5
            rotation, _ball, _tip = feet._foot_display_frame(rig, f'foot.{side}', f'toe.{side}')
            local_shape = [(rig.matrix_world @ rig.pose.bones[f'foot.{side}'].matrix).inverted() @ point
                           for point in _display_vertices(rig, roll)]
            ground_shape = [rotation.transposed() @ point for point in local_shape]
            assert max(point.y for point in ground_shape) < expected_heel - 0.02
            assert min(point.z for point in ground_shape) > -0.11 + 0.02
            target = rig.pose.bones[record['target']]
            target.location += Vector((0.02, 0.02, 0.08))
            target.rotation_euler = (0.4, -0.25, 0.3)
            roll.rotation_euler.x = 0.6
            roll.keyframe_insert(data_path='rotation_euler', frame=1)
            update(rig)
            posed_foot = rig.matrix_world @ rig.pose.bones[f'foot.{side}'].matrix
            for original, actual in zip(local_shape, _display_vertices(rig, roll)):
                assert (posed_foot @ original - actual).length < 2e-5
            before = _rig_visual_invariants(rig)
            visual_before = feet._roll_visual_state(roll)
            shoe_before = ([tuple(v.co) for v in shoe.data.vertices],
                           [[(g.group, g.weight) for g in v.groups] for v in shoe.data.vertices])
            fitted = feet.fit_roll_visual(bpy.context, rig, key, shoe)
            _assert_visual_only(rig, before)
            assert abs(fitted['roll_visual_fit']['minimum'][1] - expected_heel) < 2e-5
            assert feet.has_roll_visual_backup(rig, key)
            first_backup = fitted['roll_visual_backup']
            feet.fit_roll_visual(bpy.context, rig, key)  # Refit never replaces the original recovery point.
            assert feet.get_record(rig, key)['roll_visual_backup'] == first_backup
            feet.restore_roll_visual(bpy.context, rig, key)
            assert feet._roll_visual_state(roll) == visual_before
            assert not feet.has_roll_visual_backup(rig, key)
            _assert_visual_only(rig, before)
            assert shoe_before == ([tuple(v.co) for v in shoe.data.vertices],
                                  [[(g.group, g.weight) for g in v.groups] for v in shoe.data.vertices])


def test_roll_display_legacy_restore_persists_and_unbound_reference():
    rig, key, _data = fixture('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
    record = feet.build(bpy.context, rig, key)
    roll = rig.pose.bones[record['roll']]
    roll.custom_shape_transform = None  # Simulate an existing 0.46 layout and artist offsets.
    roll.custom_shape_translation = (0.012, -0.034, 0.056)
    roll.custom_shape_rotation_euler = (0.2, -0.4, 0.6)
    roll.custom_shape_scale_xyz = (0.11, 0.12, 0.13)
    roll.use_custom_shape_bone_size = True
    legacy = feet._roll_visual_state(roll)
    shoe = _paired_shoes(rig, bound=False)
    rig.pose.bones[record['target']].rotation_euler = (0.4, -0.3, 0.2)
    update(rig)
    result = feet.fit_roll_visual(bpy.context, rig, key, shoe)
    assert abs(result['roll_visual_fit']['minimum'][1] + 0.15) < 2e-5
    names = rig.name, record['roll']
    with tempfile.TemporaryDirectory(prefix='cd_foot_visual_') as directory:
        path = os.path.join(directory, 'visual_recovery.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        rig = bpy.data.objects[names[0]]
        roll = rig.pose.bones[names[1]]
        assert feet.has_roll_visual_backup(rig, key)
        feet.restore_roll_visual(bpy.context, rig, key)
        assert feet._roll_visual_state(roll) == legacy
        assert not feet.has_roll_visual_backup(rig, key)


def test_roll_display_errors_are_atomic():
    rig, key, _data = fixture('ROLL_DECOUPLED', 'LEFT_LEG', toes=True)
    invalid = bpy.data.objects.new('Not a shoe', None)
    bpy.context.scene.collection.objects.link(invalid)
    bones = set(rig.data.bones.keys())
    try:
        feet.build(bpy.context, rig, key, shoe=invalid)
    except limb_ik.LimbIKError:
        pass
    else:
        raise AssertionError('An invalid fit reference was accepted during build')
    assert set(rig.data.bones.keys()) == bones and not feet.records(rig)
    record = feet.build(bpy.context, rig, key)
    roll = rig.pose.bones[record['roll']]
    visual, raw = feet._roll_visual_state(roll), rig.data[feet.RECORD_KEY]
    before = _rig_visual_invariants(rig)
    try:
        feet.fit_roll_visual(bpy.context, rig, key, invalid)
    except limb_ik.LimbIKError:
        pass
    else:
        raise AssertionError('An invalid fit reference was accepted')
    assert rig.data[feet.RECORD_KEY] == raw and feet._roll_visual_state(roll) == visual
    _assert_visual_only(rig, before)
    roll.keyframe_insert(data_path='custom_shape_translation', frame=1)
    try:
        feet.fit_roll_visual(bpy.context, rig, key)
    except limb_ik.LimbIKError as exc:
        assert 'display' in str(exc)
    else:
        raise AssertionError('Animated display offsets were overwritten')
    assert rig.data[feet.RECORD_KEY] == raw and feet._roll_visual_state(roll) == visual


def main():
    base.ensure_registered()
    try:
        for test in (test_pivots_both_sides_and_schemas, test_add_in_arbitrary_pose,
                     test_build_rollback_and_conflicts, test_ownership_and_animated_remove,
                     test_removal_preserves_new_artist_dependencies,
                     test_roll_display_follows_solved_foot_and_fits_paired_shoes,
                     test_roll_display_legacy_restore_persists_and_unbound_reference,
                     test_roll_display_errors_are_atomic):
            test()
            print('PASS', test.__name__, flush=True)
    finally:
        base.reset_scene()
        base.ensure_unregistered()
    print('FOOT_CONTROLS_PASSED 8')


if __name__ == '__main__':
    main()
