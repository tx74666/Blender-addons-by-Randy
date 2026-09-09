"""Head/Neck display fitting, artist-state restoration and native-pivot invariance."""
import json
import math
import os
import sys
import tempfile

import bpy
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
import character_designer
from character_designer import head_neck_visuals as service, character_setup, control_colors, limb_ik
from character_designer.torso_controls import _state, _same_rest


def fixture(*, roll=0., artist=False, posed=True):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    data = bpy.data.armatures.new('Head Visual Rig')
    rig = bpy.data.objects.new('Head Visual Rig', data)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    def bone(name, start, end, parent=None, angle=0):
        item = data.edit_bones.new(name)
        item.head, item.tail, item.parent, item.roll = start, end, parent, angle
        return item
    hips = bone('Hips', (0, 0, .9), (0, 0, 1.2))
    neck = bone('Neck', (0, 0, 1.35), (0, 0, 1.5), hips, -roll*.7)
    head = bone('Head', (0, 0, 1.5), (0, 0, 1.75), neck, roll)
    for side, sign in (('L', 1), ('R', -1)):
        bone('eye.'+side, (sign*.035, -.07, 1.62), (sign*.035, -.12, 1.62), head)
    bpy.ops.object.mode_set(mode='POSE')
    rig.location, rig.rotation_euler, rig.scale = (.2, -.15, .3), (.3, -.2, .15), (.85, .85, .85)
    body_mesh = bpy.data.meshes.new('Body reference mesh')
    body = bpy.data.objects.new('Body reference', body_mesh)
    bpy.context.scene.collection.objects.link(body)
    body.location, body.rotation_euler, body.scale = (-.1, .2, -.2), (-.2, .1, .05), (.8, 1.1, 1.05)
    bpy.context.view_layer.update()
    points = []
    for level in range(11):
        height = level*.025
        width = .075*(.68+.32*math.sin(math.pi*level/10))
        for i in range(24):
            angle = i*math.tau/24
            points.append(Vector((width*math.cos(angle), -.015+.095*math.sin(angle), 1.49+height)))
    head_count = len(points)
    for level in range(5):
        for i in range(24):
            angle = i*math.tau/24
            points.append(Vector((.04*math.cos(angle), .035*math.sin(angle), 1.37+level*.017)))
    neck_end = len(points)
    points.extend((Vector((.01, .02, .25)), Vector((.02, .02, .35))))  # Long trailing hair on the same mesh.
    transform = body.matrix_world.inverted() @ rig.matrix_world
    body_mesh.from_pydata([transform @ point for point in points], [], [])
    body.modifiers.new('Rig', 'ARMATURE').object = rig
    body.vertex_groups.new(name='Head').add(list(range(head_count))+list(range(neck_end, len(points))), 1., 'REPLACE')
    body.vertex_groups.new(name='Neck').add(list(range(head_count, neck_end)), 1., 'REPLACE')
    setup = character_setup.settings(bpy.context)
    setup.rig, setup.body, setup.head_bone = rig, body, 'Head'
    if posed:
        for name, rotation in (('Hips', (.05, .03, -.04)), ('Neck', (.12, -.08, .03)), ('Head', (-.09, .11, .06))):
            rig.pose.bones[name].rotation_mode = 'XYZ'
            rig.pose.bones[name].rotation_euler = rotation
    if artist:
        mesh = bpy.data.meshes.new('Original artist mesh')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0)], [(0, 1)], [])
        shape = bpy.data.objects.new('Original artist shape', mesh)  # Deliberately unlinked, saved by our ID reference.
        pb = rig.pose.bones['Head']
        pb.custom_shape, pb.custom_shape_transform = shape, rig.pose.bones['eye.L']
        pb.custom_shape_scale_xyz, pb.custom_shape_translation = (.7, 1.1, .9), (.01, .02, -.01)
        pb.custom_shape_rotation_euler, pb.color.palette = (.2, -.1, .15), 'THEME05'
        pb.custom_shape_wire_width = 3.0
    service._update(bpy.context, rig)
    return rig, body


def snapshot(rig, body):
    return {'rest': {b.name: _state(b) for b in rig.data.bones},
            'pose': {p.name: p.matrix.copy() for p in rig.pose.bones},
            'basis': {p.name: p.matrix_basis.copy() for p in rig.pose.bones},
            'weights': tuple(tuple((g.group, g.weight) for g in v.groups) for v in body.data.vertices),
            'constraints': {p.name: tuple(p.constraints.keys()) for p in rig.pose.bones}}


def preserved(rig, body, before):
    assert set(rig.data.bones.keys()) == set(before['rest'])
    for name, rest in before['rest'].items():
        assert _same_rest(rig.data.bones[name], rest)
    service.visuals._verify_pose(rig, before['pose'])
    assert all(max(abs(p.matrix_basis[i][j]-before['basis'][p.name][i][j]) for i in range(4) for j in range(4)) < 1e-7
               for p in rig.pose.bones)
    assert snapshot(rig, body)['weights'] == before['weights']
    assert {p.name: tuple(p.constraints.keys()) for p in rig.pose.bones} == before['constraints']


def refusal(call, phrase):
    try:
        call()
    except (ValueError, RuntimeError) as exc:
        assert phrase.casefold() in str(exc).casefold(), str(exc)
        return
    raise AssertionError('Expected refusal: '+phrase)


def test_fit_roll_pose_and_editable_removal():
    unrolled = None
    for roll in (0., 1.05):
        rig, body = fixture(roll=roll, artist=True)
        before = snapshot(rig, body)
        old_display = {name: limb_ik._pose_shape_runtime_state(rig.pose.bones[name]) for name in ('Head', 'Neck')}
        old_color = {name: control_colors.capture_bone(rig.pose.bones[name]) for name in ('Head', 'Neck')}
        record = service.build(bpy.context, rig)
        assert service.build(bpy.context, rig) == record
        assert record['fit']['method'] == 'HEAD_WEIGHTS' and record['fit']['point_count'] == 264
        assert record['fit']['bounds'][0][2] > -.04  # Trailing hair is excluded.
        assert set(record['bindings']) == {'Head', 'Neck'} and limb_ik.ARMATURE_ID_KEY not in rig.data
        preserved(rig, body, before)
        shape = rig.pose.bones['Head'].custom_shape
        rest_vertices = [rig.data.bones['Head'].matrix_local @ v.co for v in shape.data.vertices]
        if unrolled is None:
            unrolled = rest_vertices
        else:
            assert max((a-b).length for a, b in zip(unrolled, rest_vertices)) < 1e-6
        assert len(shape.data.polygons) == 0
        # The middle of the face remains open after rounding the side profile.
        frame = Matrix(record['fit']['head_frame']).inverted() @ rig.data.bones['Head'].matrix_local
        points = [frame @ vertex.co for vertex in shape.data.vertices]
        low, high = record['fit']['bounds']
        for edge in shape.data.edges:
            midpoint = (points[edge.vertices[0]] + points[edge.vertices[1]]) * .5
            assert not (abs(midpoint.y-low[1]) < 1e-5 and abs(midpoint.x) < (high[0]-low[0])*.25
                        and low[2]+(high[2]-low[2])*.2 < midpoint.z < low[2]+(high[2]-low[2])*.8)
        assert len(rig.pose.bones['Neck'].custom_shape.data.vertices) == 27
        shape.data.vertices[0].co.x += .02
        rig.pose.bones['Head'].custom_shape_translation = (.02, -.03, .04)
        rig.pose.bones['Head'].custom_shape_scale_xyz = (.8, .9, 1.2)
        rig.pose.bones['Head'].color.palette = 'THEME09'
        service.validate(rig)
        objects = [entry['object'] for entry in record['bindings'].values()]
        assert service.remove(bpy.context, rig) == {'removed': 2}
        assert service.remove(bpy.context, rig) == {'removed': 0}
        for name in ('Head', 'Neck'):
            assert limb_ik._pose_shape_runtime_state(rig.pose.bones[name]) == old_display[name]
            assert control_colors.capture_bone(rig.pose.bones[name]) == old_color[name]
        assert not set(objects) & set(bpy.data.objects.keys())
        preserved(rig, body, before)


def test_artist_reference_save_reopen_and_pose_animation():
    rig, body = fixture(artist=True)
    pb = rig.pose.bones['Head']
    pb.keyframe_insert('rotation_euler', frame=1)
    original = pb.custom_shape
    record = service.build(bpy.context, rig)
    assert original.users >= 1 and not original.use_fake_user
    original.name = 'Renamed artist shape'
    names = rig.name, body.name
    with tempfile.TemporaryDirectory(prefix='cd-head-neck-') as directory:
        path = os.path.join(directory, 'head.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig, body = (bpy.data.objects[name] for name in names)
        assert service.validate(rig) == record
        before = snapshot(rig, body)
        service.remove(bpy.context, rig)
        assert rig.pose.bones['Head'].custom_shape.name == 'Renamed artist shape'
        assert rig.pose.bones['Head'].custom_shape_transform.name == 'eye.L'
        assert rig.animation_data.action is not None
        preserved(rig, body, before)


def test_mapping_bounds_fallback_and_animation_guards():
    rig, body = fixture()
    rig.data.bones['Head'].name = 'Skull'
    character_setup.settings(bpy.context).head_bone = 'Skull'
    character_setup.settings(bpy.context).body = None
    record = service.build(bpy.context, rig)
    assert record['head'] == 'Skull' and record['fit']['method'] == 'BONE'
    service.remove(bpy.context, rig)
    record = service.build(bpy.context, rig, bounds=((- .09, -.13, 1.49), (.09, .08, 1.76)))
    assert record['fit']['method'] == 'BOUNDS'
    service.remove(bpy.context, rig)
    rig.pose.bones['Skull'].keyframe_insert('custom_shape_scale_xyz', frame=1)
    refusal(lambda: service.build(bpy.context, rig), 'animation')
    rig.animation_data_clear()
    record = service.build(bpy.context, rig)
    rig.pose.bones['Skull'].keyframe_insert('custom_shape_translation', frame=1)
    refusal(lambda: service.remove(bpy.context, rig), 'animation')
    assert service.validate(rig) == record


def test_dependencies_and_transaction_rollback():
    rig, body = fixture(artist=True)
    before = snapshot(rig, body)
    original = rig.pose.bones['Head'].custom_shape
    objects, meshes = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())
    create = service._create_widget
    def fail_create(*args):
        create(*args)
        raise RuntimeError('Injected widget failure')
    service._create_widget = fail_create
    try:
        refusal(lambda: service.build(bpy.context, rig), 'Injected')
    finally:
        service._create_widget = create
    assert rig.pose.bones['Head'].custom_shape == original
    assert set(bpy.data.objects.keys()) == objects and set(bpy.data.meshes.keys()) == meshes
    assert service.get_record(rig) is None and service.ID_KEY not in rig.data
    preserved(rig, body, before)
    record = service.build(bpy.context, rig)
    rig.pose.bones['Hips'].custom_shape = rig.pose.bones['Head'].custom_shape
    refusal(lambda: service.remove(bpy.context, rig), 'shared')
    rig.pose.bones['Hips'].custom_shape = None
    rig.pose.bones['eye.L'].name = 'Missing original anchor'
    refusal(lambda: service.remove(bpy.context, rig), 'anchor')
    rig.pose.bones['Missing original anchor'].name = 'eye.L'
    rig.pose.bones['Head'].custom_shape_translation.x = .1
    generated = limb_ik._pose_shape_runtime_state(rig.pose.bones['Head'])
    verify = service.visuals._verify_pose
    def fail_verify(*args): raise RuntimeError('Injected verification failure')
    service.visuals._verify_pose = fail_verify
    try:
        refusal(lambda: service.remove(bpy.context, rig), 'Injected')
    finally:
        service.visuals._verify_pose = verify
    assert limb_ik._pose_shape_runtime_state(rig.pose.bones['Head']) == generated
    assert service.validate(rig) == record
    service.remove(bpy.context, rig)
    preserved(rig, body, before)


if __name__ == '__main__':
    character_designer.register()
    tests = (test_fit_roll_pose_and_editable_removal, test_artist_reference_save_reopen_and_pose_animation,
             test_mapping_bounds_fallback_and_animation_guards, test_dependencies_and_transaction_rollback)
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('HEAD_NECK_VISUALS_PASSED', len(tests), flush=True)
