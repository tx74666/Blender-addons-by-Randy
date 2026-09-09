"""Reversible FK rings and conservative, independent IK control size fits."""
import json
import math
import os
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import limb_fk_visuals as visuals, limb_ik, limb_ik_fk, torso_controls
import test_torso_controls_blender as fixtures


def fixture(method='DIRECT_PREROLL'):
    rig, _chain = fixtures.fixture(method=method, posed=True, feet=True)
    if visuals.get_record(rig):
        visuals.remove(bpy.context, rig)
    return rig, limb_ik._validate_inventory(rig)


def poses(rig):
    visuals._update(bpy.context, rig)
    return {pb.name: pb.matrix.copy() for pb in rig.pose.bones}


def displays(rig):
    return {pb.name: limb_ik._pose_shape_json_state(pb) for pb in rig.pose.bones}


def refuse(call, phrase):
    try:
        call()
    except limb_ik.LimbIKError as exc:
        assert phrase.lower() in str(exc).lower(), str(exc)
        return
    raise AssertionError('Expected safe refusal')


def test_rings_all_sides_native_joint_anchor_and_roundtrip():
    for method in ('DIRECT_PREROLL', 'ROLL_DECOUPLED'):
        rig, inventory = fixture(method)
        before, before_displays = poses(rig), displays(rig)
        rest = {bone.name: torso_controls._state(bone) for bone in rig.data.bones}
        digest = limb_ik._armature_digest(rig)
        original_objects = set(bpy.data.objects.keys())
        original_meshes = set(bpy.data.meshes.keys())
        record = visuals.build(bpy.context, rig)
        assert len(record['bindings']) == 8
        assert visuals.apply(bpy.context, rig) == record
        assert set(rig.data.bones.keys()) == set(rest)
        visuals._verify_pose(rig, before)
        for name, entry in record['bindings'].items():
            pb = rig.pose.bones[name]
            obj = pb.custom_shape
            assert obj.get(visuals.OWNER_KEY) == visuals.OWNER_VALUE
            assert pb.bone.get(visuals.OWNER_KEY) != visuals.OWNER_VALUE
            assert len(obj.data.vertices) == 32 and len(obj.data.edges) == 32 and not obj.data.polygons
            assert all(abs(vertex.co.y) < 1e-8 for vertex in obj.data.vertices)
            assert abs(max(vertex.co.x for vertex in obj.data.vertices) - 1.0) < 1e-6
            assert abs(max(vertex.co.z for vertex in obj.data.vertices) - .819) < 1e-6
            assert pb.custom_shape_transform is None
            assert tuple(pb.custom_shape_translation) == (0, 0, 0)
            assert tuple(pb.custom_shape_rotation_euler) == (0, 0, 0)
            assert abs(pb.custom_shape_scale_xyz.x - pb.bone.length * visuals.RADIUS_FACTORS[entry['kind']]) < 1e-6
            assert before_displays[name]['custom_shape'] == ''
        for key, data in inventory['rigs'].items():
            target = rig.pose.bones[data['target'].name]
            target.location += Vector((.005, -.005, .005))
            target[limb_ik_fk.PROPERTY] = .5
        halfway = poses(rig)
        for name in record['bindings']:
            pb = rig.pose.bones[name]
            centroid = sum((vertex.co for vertex in pb.custom_shape.data.vertices), Vector()) / 32
            world_anchor = rig.matrix_world @ pb.matrix @ centroid
            assert (world_anchor - rig.matrix_world @ pb.head).length < 2e-6
        assert visuals.remove(bpy.context, rig) == {'removed': 8}
        visuals._verify_pose(rig, halfway)
        assert displays(rig) == before_displays
        assert set(bpy.data.objects.keys()) == original_objects and set(bpy.data.meshes.keys()) == original_meshes
        assert limb_ik._armature_digest(rig) == digest
        assert all(torso_controls._same_rest(rig.data.bones[name], state) for name, state in rest.items())
        limb_ik._validate_inventory(rig)


def test_artist_shapes_anchors_and_saved_empty_display_are_preserved():
    rig, inventory = fixture()
    sources = [name for data in inventory['rigs'].values() for name in data['chain'][:2]]
    artist = bpy.data.objects.new('Artist Custom Shape', bpy.data.meshes.new('Artist Custom Shape'))
    bpy.context.scene.collection.objects.link(artist)
    rig.pose.bones[sources[0]].custom_shape = artist
    rig.pose.bones[sources[1]].custom_shape_transform = rig.pose.bones['Hips']
    modified = rig.pose.bones[sources[2]]
    modified.custom_shape_scale_xyz = (.71, .92, 1.13)
    modified.custom_shape_translation = (.01, .02, .03)
    modified.custom_shape_rotation_euler = (.15, -.1, .07)
    modified.custom_shape_wire_width = 3.5
    before = displays(rig)
    record = visuals.build(bpy.context, rig)
    assert len(record['bindings']) == 6 and len(record['skipped']) == 2
    assert rig.pose.bones[sources[0]].custom_shape == artist
    assert rig.pose.bones[sources[1]].custom_shape_transform.name == 'Hips'
    visuals.remove(bpy.context, rig)
    assert displays(rig) == before


def test_ownership_sharing_and_animated_display_guards():
    rig, inventory = fixture()
    source = inventory['rigs'][('ARM', 'L')]['chain'][0]
    pb = rig.pose.bones[source]
    pb.keyframe_insert(data_path='custom_shape_scale_xyz', frame=1)
    record = visuals.build(bpy.context, rig)
    assert source in record['skipped'] and source not in record['bindings']
    name = next(iter(record['bindings']))
    obj = rig.pose.bones[name].custom_shape
    sharer = bpy.data.objects.new('Artist Mesh Copy', obj.data)
    bpy.context.scene.collection.objects.link(sharer)
    saved = rig.data[visuals.RECORD_KEY]
    refuse(lambda: visuals.remove(bpy.context, rig), 'shared')
    assert rig.data[visuals.RECORD_KEY] == saved
    bpy.data.objects.remove(sharer, do_unlink=True)
    rig.pose.bones[name].custom_shape_scale_xyz.x *= 1.1
    refuse(lambda: visuals.remove(bpy.context, rig), 'edited')
    rig.pose.bones[name].custom_shape_scale_xyz = record['bindings'][name]['generated']['scale']
    visuals.remove(bpy.context, rig)
    assert visuals.get_record(rig) is None


def test_failed_build_and_remove_restore_displays_and_resources():
    rig, inventory = fixture()
    original_displays = displays(rig)
    objects, meshes = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())
    create = visuals._create_widget
    def fail_create(*args, **kwargs):
        create(*args, **kwargs)
        raise RuntimeError('Injected widget failure')
    visuals._create_widget = fail_create
    try:
        try: visuals.build(bpy.context, rig)
        except RuntimeError: pass
        else: raise AssertionError('Expected build failure')
    finally:
        visuals._create_widget = create
    assert visuals.get_record(rig) is None
    assert displays(rig) == original_displays
    assert set(bpy.data.objects.keys()) == objects and set(bpy.data.meshes.keys()) == meshes
    record = visuals.build(bpy.context, rig)
    generated_displays = displays(rig)
    saved = rig.data[visuals.RECORD_KEY]
    verify = visuals._verify_pose
    def fail_verify(*args): raise RuntimeError('Injected late display failure')
    visuals._verify_pose = fail_verify
    try:
        try: visuals.remove(bpy.context, rig)
        except RuntimeError: pass
        else: raise AssertionError('Expected removal failure')
    finally:
        visuals._verify_pose = verify
    assert rig.data[visuals.RECORD_KEY] == saved and displays(rig) == generated_displays
    visuals.validate(rig, inventory)
    visuals.remove(bpy.context, rig)


def test_default_size_fit_skips_artist_edits_and_restores_only_sizes():
    rig, inventory = fixture()
    arm_l = rig.pose.bones[inventory['rigs'][('ARM', 'L')]['target'].name]
    leg_l = rig.pose.bones[inventory['rigs'][('LEG', 'L')]['pole'].name]
    arm_l.custom_shape_scale_xyz *= .93
    leg_l.custom_shape_translation.x += .012
    leg_l.custom_shape_rotation_euler.z += .08
    original = displays(rig)
    default_raw = {pb.name: pb.bone.get(limb_ik.CONTROL_VISUAL_DEFAULT_KEY) for pb in rig.pose.bones}
    before = poses(rig)
    result = visuals.fit_ik_sizes(bpy.context, rig)
    assert result['fitted'] == 3 and arm_l.name in result['skipped']
    assert visuals.get_record(rig) is None and visuals.has_ik_size_backup(rig)
    backup = visuals._size_record(rig)
    for name, entry in backup['bindings'].items():
        pb = rig.pose.bones[name]
        current = limb_ik._pose_shape_json_state(pb)
        assert all(abs(a-b*entry['factor']) < 1e-6 for a,b in zip(current['scale'], original[name]['scale']))
        assert all(current[key] == original[name][key] for key in current if key != 'scale')
    assert visuals.fit_ik_sizes(bpy.context, rig)['fitted'] == 0
    visuals._verify_pose(rig, before)
    assert default_raw == {pb.name: pb.bone.get(limb_ik.CONTROL_VISUAL_DEFAULT_KEY) for pb in rig.pose.bones}
    limb_ik._validate_inventory(rig)
    leg_l.custom_shape_scale_xyz.x *= 1.1
    refuse(lambda: visuals.restore_ik_sizes(bpy.context, rig), 'edited')
    leg_l.custom_shape_scale_xyz = backup['bindings'][leg_l.name]['fitted']
    assert visuals.restore_ik_sizes(bpy.context, rig) == {'restored': 3}
    assert not visuals.has_ik_size_backup(rig) and displays(rig) == original


def test_size_fit_rollback_and_save_reopen():
    rig, inventory = fixture()
    before = displays(rig)
    verify = visuals._verify_pose
    def fail(*args): raise RuntimeError('Injected size-fit failure')
    visuals._verify_pose = fail
    try:
        try: visuals.fit_ik_sizes(bpy.context, rig)
        except RuntimeError: pass
        else: raise AssertionError('Expected fit failure')
    finally:
        visuals._verify_pose = verify
    assert displays(rig) == before and not visuals.has_ik_size_backup(rig)
    record = visuals.build(bpy.context, rig)
    assert visuals.fit_ik_sizes(bpy.context, rig)['fitted'] == 4
    saved = displays(rig)
    desired = poses(rig)
    with tempfile.TemporaryDirectory(prefix='cd-fk-rings-') as directory:
        path = os.path.join(directory, 'rings.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig = bpy.data.objects['Humanoid']
        assert visuals.validate(rig) == record and displays(rig) == saved
        visuals._verify_pose(rig, desired)
        visuals.restore_ik_sizes(bpy.context, rig)
        visuals.remove(bpy.context, rig)
        assert displays(rig) == before


if __name__ == '__main__':
    tests = (test_rings_all_sides_native_joint_anchor_and_roundtrip,
             test_artist_shapes_anchors_and_saved_empty_display_are_preserved,
             test_ownership_sharing_and_animated_display_guards,
             test_failed_build_and_remove_restore_displays_and_resources,
             test_default_size_fit_skips_artist_edits_and_restores_only_sizes,
             test_size_fit_rollback_and_save_reopen)
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('LIMB_FK_VISUALS_PASSED', len(tests), flush=True)
