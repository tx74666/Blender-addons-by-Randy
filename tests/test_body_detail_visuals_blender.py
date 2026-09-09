"""Native Breast/Hips display fitting, persistence and transactional recovery."""
import json
import math
import os
import sys
import tempfile

import bpy
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons')]
import character_designer
from character_designer import body_detail_visuals as service, character_setup, control_colors, limb_ik
from character_designer.torso_controls import _state, _same_rest


def fixture(*, roll=0., artist=False, posed=True):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    data = bpy.data.armatures.new('Body Detail Rig')
    rig = bpy.data.objects.new('Body Detail Rig', data)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    def bone(name, start, end, parent=None, angle=0):
        item = data.edit_bones.new(name)
        item.head, item.tail, item.parent, item.roll = start, end, parent, angle
        return item
    hips = bone('Hips', (0, .05, 1.2), (0, .05, 1.31), angle=roll*.3)
    chest = bone('UpperChest', (0, .05, 1.55), (0, .05, 1.70), hips, roll*.7)
    for side, sign in (('L', 1), ('R', -1)):
        bone('breast.'+side, (sign*.085, -.015, 1.56), (sign*.085, -.12, 1.53), chest, roll*sign)
    bpy.ops.object.mode_set(mode='POSE')
    rig.location, rig.rotation_euler, rig.scale = (.2, -.15, .3), (.3, -.2, .15), (.85, .85, .85)
    points, weights = [], {}
    for side, sign in (('L', 1), ('R', -1)):
        indices = []
        for level in range(7):
            z = 1.475+level*.021
            for i in range(32):
                angle = i*math.tau/32
                indices.append(len(points))
                points.append(Vector((sign*.085+.065*math.cos(angle), -.065+.065*math.sin(angle), z)))
        weights['breast.'+side] = indices
    indices = []
    for level in range(5):
        for i in range(48):
            angle = i*math.tau/48
            indices.append(len(points))
            points.append(Vector((.165*math.cos(angle), .05+.115*math.sin(angle), 1.20+level*.013)))
    weights['Hips'] = indices
    # Loose trailing geometry with misleading breast weights is outside the fitting window.
    points += [Vector((.01, -.5, .3)), Vector((-.01, -.5, .3))]
    weights['breast.L'].append(len(points)-2)
    weights['breast.R'].append(len(points)-1)
    def mesh(name, points, *, bound=True):
        data = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.location, obj.rotation_euler, obj.scale = (-.1, .2, -.2), (-.2, .1, .05), (.8, 1.1, 1.05)
        bpy.context.view_layer.update()
        transform = obj.matrix_world.inverted() @ rig.matrix_world
        data.from_pydata([transform @ p for p in points], [], [])
        if bound:
            obj.modifiers.new('Rig', 'ARMATURE').object = rig
        return obj
    body = mesh('Body reference', points)
    for name, indices in weights.items():
        body.vertex_groups.new(name=name).add(indices, 1., 'REPLACE')
    clothing = mesh('Mapped clothing', [p+Vector((0, -.018, 0)) for p in points if p.z > 1.4])
    # Large skirt hem must not enlarge the waist ring; only the local belt slice is used.
    skirt = mesh('Mapped skirt', [Vector((radius*math.cos(i*math.tau/48), .05+radius*.7*math.sin(i*math.tau/48), z))
                for z, radius in ((1.23, .182), (.7, .6)) for i in range(48)], bound=False)
    hair = mesh('Mapped hair', [Vector((sign*.085+x, -.30, z)) for sign in (-1, 1)
                               for x in (-.06, 0, .06) for z in (1.5, 1.53, 1.56)])
    setup = character_setup.settings(bpy.context)
    setup.rig, setup.body, setup.hips_bone = rig, body, 'Hips'
    setup.assets.clear()
    for obj, role in ((clothing, 'CLOTHING'), (skirt, 'SKIRT'), (hair, 'HAIR')):
        item = setup.assets.add()
        item.object, item.role = obj, role
    if posed:
        for name, rotation in (('Hips', (.05, .03, -.04)), ('UpperChest', (.12, -.08, .03)),
                               ('breast.L', (-.09, .11, .06)), ('breast.R', (.04, -.03, -.06))):
            rig.pose.bones[name].rotation_mode = 'XYZ'
            rig.pose.bones[name].rotation_euler = rotation
    limit = rig.pose.bones['breast.L'].constraints.new('LIMIT_ROTATION')
    limit.owner_space = 'LOCAL'
    limit.use_limit_x, limit.min_x, limit.max_x = True, -.5, .5
    if artist:
        mesh = bpy.data.meshes.new('Original artist mesh')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0)], [(0, 1)], [])
        shape = bpy.data.objects.new('Original artist shape', mesh)
        pb = rig.pose.bones['breast.L']
        pb.custom_shape, pb.custom_shape_transform = shape, rig.pose.bones['UpperChest']
        pb.custom_shape_scale_xyz, pb.custom_shape_translation = (.7, 1.1, .9), (.01, .02, -.01)
        pb.custom_shape_rotation_euler, pb.color.palette = (.2, -.1, .15), 'THEME05'
        pb.custom_shape_wire_width = 3.0
    service._update(bpy.context, rig)
    return rig, body


def snapshot(rig, body):
    return {'rest': {b.name: _state(b) for b in rig.data.bones},
            'pose': {p.name: p.matrix.copy() for p in rig.pose.bones},
            'basis': {p.name: p.matrix_basis.copy() for p in rig.pose.bones},
            'vertices': tuple(tuple(v.co) for v in body.data.vertices),
            'weights': tuple(tuple((g.group, g.weight) for g in v.groups) for v in body.data.vertices),
            'constraints': {p.name: tuple((c.name, c.type, c.influence, c.owner_space, c.mute) for c in p.constraints)
                            for p in rig.pose.bones}}


def preserved(rig, body, before):
    assert set(rig.data.bones.keys()) == set(before['rest'])
    for name, rest in before['rest'].items():
        assert _same_rest(rig.data.bones[name], rest)
        assert rig.data.bones[name].use_deform == rest['deform']
    service.visuals._verify_pose(rig, before['pose'])
    assert all(max(abs(p.matrix_basis[i][j]-before['basis'][p.name][i][j]) for i in range(4) for j in range(4)) < 1e-7
               for p in rig.pose.bones)
    after = snapshot(rig, body)
    assert all(after[key] == before[key] for key in ('vertices', 'weights', 'constraints'))


def refusal(call, phrase):
    try:
        call()
    except (ValueError, RuntimeError) as exc:
        assert phrase.casefold() in str(exc).casefold(), str(exc)
        return
    raise AssertionError('Expected refusal: '+phrase)


def test_fit_roll_pose_and_editable_removal():
    reference = None
    for roll in (0., 1.05):
        rig, body = fixture(roll=roll, artist=True)
        before = snapshot(rig, body)
        names = ('Hips', 'breast.L', 'breast.R')
        old_display = {name: limb_ik._pose_shape_runtime_state(rig.pose.bones[name]) for name in names}
        old_color = {name: control_colors.capture_bone(rig.pose.bones[name]) for name in names}
        record = service.build(bpy.context, rig)
        assert service.build(bpy.context, rig) == record
        assert set(record['bindings']) == set(names) and limb_ik.ARMATURE_ID_KEY not in rig.data
        preserved(rig, body, before)
        points = {name: [rig.data.bones[name].matrix_local @ v.co for v in rig.pose.bones[name].custom_shape.data.vertices]
                  for name in names}
        if reference is None:
            reference = points
        else:
            assert max((a-b).length for name in names for a, b in zip(reference[name], points[name])) < 1e-6
        for name in names:
            shape = rig.pose.bones[name].custom_shape
            assert len(shape.data.vertices) == len(shape.data.edges) == 64 and len(shape.data.polygons) == 0
            assert rig.pose.bones[name].custom_shape_transform is None
        assert min(p.x for p in points['breast.L']) > max(p.x for p in points['breast.R'])+.015
        for name in ('breast.L', 'breast.R'):
            # In this fixture, -Y is anterior: middle is outside, top/bottom
            # wrap toward the body, rather than curving away like a scoop.
            assert points[name][0].y < points[name][16].y-.005
            assert points[name][32].y < points[name][48].y-.005
        assert all(1.44 < p.z < 1.63 and -.20 < p.y < -.148 for name in ('breast.L', 'breast.R') for p in points[name])
        assert max(abs(p.x) for p in points['Hips']) < .22
        assert all(1.21 < p.z < 1.24 for p in points['Hips'])
        assert record['fit']['roles']['BREAST_L']['method'] == 'WEIGHTS'
        assert record['fit']['roles']['HIPS']['method'] == 'SLICE'
        shape = rig.pose.bones['breast.L'].custom_shape
        shape.data.vertices[0].co.x += .02
        rig.pose.bones['breast.L'].custom_shape_translation = (.02, -.03, .04)
        rig.pose.bones['breast.L'].color.palette = 'THEME09'
        service.validate(rig)
        objects = [entry['object'] for entry in record['bindings'].values()]
        assert service.remove(bpy.context, rig) == {'removed': 3}
        assert service.remove(bpy.context, rig) == {'removed': 0}
        for name in names:
            assert limb_ik._pose_shape_runtime_state(rig.pose.bones[name]) == old_display[name]
            assert control_colors.capture_bone(rig.pose.bones[name]) == old_color[name]
        assert not set(objects) & set(bpy.data.objects.keys())
        preserved(rig, body, before)


def test_artist_reference_save_reopen_and_pose_animation():
    rig, body = fixture(artist=True)
    pb = rig.pose.bones['breast.L']
    pb.keyframe_insert('rotation_euler', frame=1)
    original = pb.custom_shape
    record = service.build(bpy.context, rig)
    assert original.users >= 1 and not original.use_fake_user
    original.name = 'Renamed artist shape'
    names = rig.name, body.name
    with tempfile.TemporaryDirectory(prefix='cd-body-detail-') as directory:
        path = os.path.join(directory, 'body.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        rig, body = (bpy.data.objects[name] for name in names)
        assert service.validate(rig) == record
        before = snapshot(rig, body)
        service.remove(bpy.context, rig)
        assert rig.pose.bones['breast.L'].custom_shape.name == 'Renamed artist shape'
        assert rig.pose.bones['breast.L'].custom_shape_transform.name == 'UpperChest'
        assert rig.animation_data.action is not None
        preserved(rig, body, before)


def test_mapping_fallback_and_animation_guards():
    rig, body = fixture()
    rig.data.bones['breast.L'].name = 'Chest_L'
    refusal(lambda: service.build(bpy.context, rig), 'native left breast')
    record = service.build(bpy.context, rig, left_name='Chest_L')
    assert record['names']['BREAST_L'] == 'Chest_L'
    service.remove(bpy.context, rig)
    rig.data.bones['Chest_L'].name = 'breast.L'
    rig.data.bones['UpperChest'].name = 'breast_L'
    refusal(lambda: service.build(bpy.context, rig), 'native left breast')
    record = service.build(bpy.context, rig, left_name='breast.L')
    service.remove(bpy.context, rig)
    rig.data.bones['breast_L'].name = 'UpperChest'
    state = character_setup.settings(bpy.context)
    state.body = None
    state.assets.clear()
    record = service.build(bpy.context, rig)
    assert all(item['method'] == 'BONE' for item in record['fit']['roles'].values())
    service.remove(bpy.context, rig)
    rig.pose.bones['Hips'].keyframe_insert('custom_shape_scale_xyz', frame=1)
    refusal(lambda: service.build(bpy.context, rig), 'animation')
    rig.animation_data_clear()
    record = service.build(bpy.context, rig)
    rig.pose.bones['breast.R'].keyframe_insert('custom_shape_translation', frame=1)
    refusal(lambda: service.remove(bpy.context, rig), 'animation')
    assert service.validate(rig) == record


def test_dependencies_and_transaction_rollback():
    rig, body = fixture(artist=True)
    before = snapshot(rig, body)
    original = rig.pose.bones['breast.L'].custom_shape
    objects, meshes = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())
    create = service._create_widget
    def fail_create(*args):
        create(*args)
        if args[-1]['role'] == 'BREAST_L':
            raise RuntimeError('Injected widget failure')
    service._create_widget = fail_create
    try:
        refusal(lambda: service.build(bpy.context, rig), 'Injected')
    finally:
        service._create_widget = create
    assert rig.pose.bones['breast.L'].custom_shape == original
    assert set(bpy.data.objects.keys()) == objects and set(bpy.data.meshes.keys()) == meshes
    assert service.get_record(rig) is None and service.ID_KEY not in rig.data
    preserved(rig, body, before)
    geometry = service._geometry
    def fail_geometry(*args):
        raise RuntimeError('Injected mesh allocation failure')
    service._geometry = fail_geometry
    try:
        refusal(lambda: service.build(bpy.context, rig), 'allocation failure')
    finally:
        service._geometry = geometry
    assert set(bpy.data.objects.keys()) == objects and set(bpy.data.meshes.keys()) == meshes
    assert service.get_record(rig) is None and service.REFERENCE_KEY not in rig.data
    record = service.build(bpy.context, rig)
    generated_shape = rig.pose.bones['breast.L'].custom_shape
    rig.pose.bones['breast.L'].custom_shape = original
    refusal(lambda: service.remove(bpy.context, rig), 'assignment')
    assert rig.pose.bones['breast.L'].custom_shape == original
    rig.pose.bones['breast.L'].custom_shape = generated_shape
    rig.pose.bones['UpperChest'].custom_shape = rig.pose.bones['Hips'].custom_shape
    refusal(lambda: service.remove(bpy.context, rig), 'shared')
    rig.pose.bones['UpperChest'].custom_shape = None
    rig.pose.bones['breast.L'].custom_shape_translation.x = .1
    generated = limb_ik._pose_shape_runtime_state(rig.pose.bones['breast.L'])
    verify = service.visuals._verify_pose
    def fail_verify(*args):
        raise RuntimeError('Injected verification failure')
    service.visuals._verify_pose = fail_verify
    try:
        refusal(lambda: service.remove(bpy.context, rig), 'Injected')
    finally:
        service.visuals._verify_pose = verify
    assert limb_ik._pose_shape_runtime_state(rig.pose.bones['breast.L']) == generated
    assert service.validate(rig) == record
    service.remove(bpy.context, rig)
    preserved(rig, body, before)


def legacy(rig):
    record = service.get_record(rig)
    for name, entry in record['bindings'].items():
        if entry['role'] == 'HIPS':
            continue
        record['fit']['roles'][entry['role']].pop('profile', None)
        points, _ = service._geometry(entry['role'], record['fit'])
        local = rig.data.bones[name].matrix_local.inverted() @ Matrix(record['fit']['frame'])
        mesh = rig.pose.bones[name].custom_shape.data
        for vertex, point in zip(mesh.vertices, points):
            vertex.co = local @ Vector(point)
        mesh.update()
    rig.data[service.RECORD_KEY] = json.dumps(record)
    return record


def test_legacy_curvature_migration_and_guards():
    rig, body = fixture(artist=True, roll=.7)
    service.build(bpy.context, rig)
    old_record = legacy(rig)
    names = tuple(old_record['bindings'])
    def displays():
        return {name: limb_ik._pose_shape_runtime_state(rig.pose.bones[name]) for name in names}
    def colors():
        return {name: control_colors.capture_bone(rig.pose.bones[name]) for name in names}
    def points():
        return {name: tuple(tuple(v.co) for v in rig.pose.bones[name].custom_shape.data.vertices) for name in names}
    before, old_display, old_color, old_points = snapshot(rig, body), displays(), colors(), points()
    mesh_ids = {name: rig.pose.bones[name].custom_shape.data for name in names}
    # Both legacy shapes must be untouched. The first cannot be migrated before
    # an edit on the second is discovered.
    rig.pose.bones['breast.R'].custom_shape.data.vertices[0].co.x += .01
    refusal(lambda: service.update_breast_curvature(bpy.context, rig), 'artist edits')
    assert points()['breast.L'] == old_points['breast.L'] and service.get_record(rig) == old_record
    rig.pose.bones['breast.R'].custom_shape.data.vertices[0].co = old_points['breast.R'][0]
    rig.pose.bones['UpperChest'].custom_shape = rig.pose.bones['breast.R'].custom_shape
    refusal(lambda: service.update_breast_curvature(bpy.context, rig), 'shared')
    assert points() == old_points and service.get_record(rig) == old_record
    rig.pose.bones['UpperChest'].custom_shape = None
    verify = service.visuals._verify_pose
    def fail_verify(*args):
        raise RuntimeError('Injected curvature verification failure')
    service.visuals._verify_pose = fail_verify
    try:
        refusal(lambda: service.update_breast_curvature(bpy.context, rig), 'Injected curvature')
    finally:
        service.visuals._verify_pose = verify
    assert points() == old_points and service.get_record(rig) == old_record
    record = service.build(bpy.context, rig)  # Explicit build also upgrades old profiles.
    assert displays() == old_display and colors() == old_color
    assert points()['Hips'] == old_points['Hips']
    assert all(rig.pose.bones[name].custom_shape.data == mesh_ids[name] for name in names)
    normalized = json.loads(json.dumps(record))
    for role in ('BREAST_L', 'BREAST_R'):
        assert normalized['fit']['roles'][role].pop('profile') == 'WRAP'
    assert normalized == old_record
    inverse = Matrix(record['fit']['frame']).inverted()
    for name in ('breast.L', 'breast.R'):
        local = inverse @ rig.data.bones[name].matrix_local
        old = [local @ Vector(p) for p in old_points[name]]
        new = [local @ v.co for v in rig.pose.bones[name].custom_shape.data.vertices]
        assert new[0].y < new[16].y-.005 and old[0].y > old[16].y+.005
        assert max(abs(a.x-b.x) for a,b in zip(old,new)) < 1e-6
        assert max(abs(a.z-b.z) for a,b in zip(old,new)) < 1e-6
        assert abs(min(p.y for p in old)-min(p.y for p in new)) < 1e-6
        assert abs(max(p.y for p in old)-max(p.y for p in new)) < 1e-6
    preserved(rig, body, before)
    rig.pose.bones['breast.R'].custom_shape.data.vertices[0].co.x += .01
    edited = points()
    assert service.update_breast_curvature(bpy.context, rig) == record
    assert points() == edited
    service.remove(bpy.context, rig)
    assert rig.pose.bones['breast.L'].custom_shape.name == 'Original artist shape'
    preserved(rig, body, before)


if __name__ == '__main__':
    character_designer.register()
    tests = (test_fit_roll_pose_and_editable_removal, test_artist_reference_save_reopen_and_pose_animation,
             test_mapping_fallback_and_animation_guards, test_dependencies_and_transaction_rollback,
             test_legacy_curvature_migration_and_guards)
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('BODY_DETAIL_VISUALS_PASSED', len(tests), flush=True)
