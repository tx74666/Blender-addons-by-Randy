"""Surface-defined flexion: real positive rotations, guards, rollback and persistence."""
import math
import json
import os
import sys
import tempfile
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'addons'))
import character_designer
from character_designer import finger_flex as flex, finger_bones


def object_mode():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def activate(obj, mode):
    object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode=mode)


def fixture(transform=None):
    object_mode()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    mesh = bpy.data.meshes.new('Surface')
    mesh.from_pydata([(-.1, 0, 0), (.1, 0, 0), (.1, 1, 0), (-.1, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new('Surface', mesh)
    bpy.context.collection.objects.link(obj)
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Keep')
    key.data[0].co.z = .07
    obj.active_shape_key_index = 0
    obj.vertex_groups.new(name='Keep').add([0, 1], .6, 'REPLACE')
    arm = bpy.data.armatures.new('Rig')
    rig = bpy.data.objects.new('Rig', arm)
    bpy.context.collection.objects.link(rig)
    if transform is not None:
        obj.matrix_world = transform
        rig.matrix_world = transform
    activate(rig, 'EDIT')
    prev = None
    for i in range(3):
        bone = arm.edit_bones.new(f'f_index.0{i+1}.L')
        bone.head, bone.tail = (0, i, 0), (0, i+1, 0)
        bone.parent = prev
        bone.roll = .4 + i * .3
        prev = bone
    body = arm.edit_bones.new('hand.L')
    body.head, body.tail = (0, -1, 0), (0, 0, 0)
    arm.edit_bones['f_index.01.L'].parent = body
    activate(obj, 'EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    flex.capture(bpy.context)
    # A face alone is an unoriented line; the user confirms this arrow.
    if flex.guide_frame(bpy.context)[1].dot((obj.matrix_world.to_3x3() @ Vector((0,1,0))).normalized()) < 0:
        flex.state(bpy.context).flip_forward = True
    return obj, rig


def select_chain(rig):
    activate(rig, 'EDIT')
    for b in rig.data.edit_bones:
        b.select = b.name.startswith('f_index')
    rig.data.edit_bones.active = rig.data.edit_bones['f_index.01.L']


def snapshot(rig):
    return {b.name: (tuple(b.head), tuple(b.tail), b.roll, b.parent.name if b.parent else None)
            for b in rig.data.edit_bones}


def refused(fn, fragment):
    try:
        fn()
    except ValueError as exc:
        assert fragment in str(exc), str(exc)
    else:
        raise AssertionError('Expected refusal: ' + fragment)


def test_rotation_preview_and_preservation():
    rotation = Matrix.Rotation(.7, 4, 'Y') @ Matrix.Rotation(.3, 4, 'Z')
    obj, rig = fixture(Matrix.Translation((2, 3, 1)) @ rotation @ Matrix.Scale(1.7, 4))
    select_chain(rig)
    before = snapshot(rig)
    assert all(flex.preview_lines(bpy.context))
    assert snapshot(rig) == before
    assert flex.apply(bpy.context) == 3
    after = snapshot(rig)
    assert after['hand.L'] == before['hand.L']
    assert all(after[n][:2] == before[n][:2] and after[n][3] == before[n][3] for n in after)
    object_mode()
    normal = flex.guide_frame(bpy.context)[2]
    for name in [n for n in after if n.startswith('f_index')]:
        pb = rig.pose.bones[name]
        old = (rig.matrix_world @ pb.tail).copy()
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler.x = math.radians(5)
        bpy.context.view_layer.update()
        movement = rig.matrix_world @ pb.tail - old
        assert movement.normalized().dot(normal) > .99, (name, movement, normal)
        pb.rotation_euler.x = 0
        bpy.context.view_layer.update()
    assert abs(obj.data.shape_keys.key_blocks['Keep'].data[0].co.z - .07) < 1e-6
    assert abs(obj.vertex_groups['Keep'].weight(0) - .6) < 1e-6


def test_flip_and_invalid_selection():
    obj, rig = fixture()
    select_chain(rig)
    flex.state(bpy.context).flip_bend = True
    assert flex.apply(bpy.context) == 3
    for b in rig.data.edit_bones:
        if b.select:
            delta = Quaternion(b.x_axis, .1) @ (b.tail - b.head) - (b.tail - b.head)
            assert delta.z > 0
    before = snapshot(rig)
    flex.state(bpy.context).flip_forward = not flex.state(bpy.context).flip_forward
    refused(lambda: flex.apply(bpy.context), 'blue arrow')
    assert snapshot(rig) == before
    flex.state(bpy.context).flip_forward = not flex.state(bpy.context).flip_forward
    rig.data.edit_bones['hand.L'].select = True
    refused(lambda: flex.apply(bpy.context), 'only one finger')
    assert snapshot(rig) == before


def test_edge_ambiguity_and_stale_guide():
    obj, rig = fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.active = bm.faces[0]
    for f in bm.faces: f.select_set(False)
    for e in bm.edges: e.select_set(False)
    edge = max(bm.edges, key=lambda e: e.calc_length())
    edge.select_set(True)
    flex.capture(bpy.context)
    assert flex.guide_frame(bpy.context)[2].z < -.99
    # Face edits invalidate the saved definition before any roll change.
    bm.verts[0].co.z += .01
    refused(lambda: flex.guide_frame(bpy.context), 'changed')
    obj, rig = fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts: v.co.x *= 5
    refused(lambda: flex.capture(bpy.context), 'no clear long direction')


def test_rollback_and_pose_guard():
    obj, rig = fixture()
    select_chain(rig)
    before = snapshot(rig)
    calls = []
    def update():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('Injected update failure')
    context = SimpleNamespace(object=rig, scene=bpy.context.scene, view_layer=SimpleNamespace(update=update))
    try:
        flex.apply(context)
    except RuntimeError as exc:
        assert str(exc) == 'Injected update failure'
    else:
        raise AssertionError('failure not injected')
    assert snapshot(rig) == before
    object_mode()
    rig.pose.bones['f_index.02.L'].rotation_mode = 'XYZ'
    rig.pose.bones['f_index.02.L'].rotation_euler.x = .3
    bpy.context.view_layer.update()
    select_chain(rig)
    refused(lambda: flex.apply(bpy.context), 'neutral pose')
    assert snapshot(rig) == before


def test_shared_edge_and_scaled_normal():
    obj, rig = fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.active = None
    for f in bm.faces: f.select_set(False)
    for e in bm.edges: e.select_set(False)
    edge = max(bm.edges, key=lambda e: e.calc_length())
    extra = bm.verts.new(edge.verts[0].co + Vector((0, 0, .2)))
    second = bm.faces.new((edge.verts[1], edge.verts[0], extra))
    edge.select_set(True)
    refused(lambda: flex.capture(bpy.context), 'two sides')
    bm.faces.active = second
    flex.capture(bpy.context)
    before = flex.guide_frame(bpy.context)
    obj.scale = (-2, 3, .7)
    obj.rotation_euler = (.3, .2, .6)
    bpy.context.view_layer.update()
    now = flex.guide_frame(bpy.context)
    expected = (obj.matrix_world.to_3x3().inverted().transposed() @ Vector(second.normal)).normalized()
    assert now[2].dot(expected) < -.9999
    assert abs(now[1].dot(now[2])) < 1e-5


def test_object_edit_axis_equivalence():
    obj, rig = fixture()
    select_chain(rig)
    expected = {b.name: (finger_bones._axis(b, 'X'), finger_bones._head(b), finger_bones._tail(b)) for b in rig.data.edit_bones}
    object_mode()
    for b in rig.data.bones:
        actual = (finger_bones._axis(b, 'X'), finger_bones._head(b), finger_bones._tail(b))
        assert all((a-e).length < 1e-5 for a,e in zip(actual, expected[b.name]))


def make_top_grid(columns=1, rows=8):
    _, rig = fixture()
    object_mode()
    mesh = bpy.data.meshes.new('Top Strip')
    verts = [(x * .2 - .1, y * 3 / rows, .15) for y in range(rows + 1) for x in range(columns + 1)]
    faces = []
    for y in range(rows):
        for x in range(columns):
            i = y * (columns + 1) + x
            faces.append((i, i+1, i+columns+2, i+columns+1))
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new('Top Strip', mesh)
    bpy.context.collection.objects.link(obj)
    activate(obj, 'EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    return obj, rig


def test_eight_face_top_strip_and_center_hinges():
    obj, rig = make_top_grid()
    before_mesh = [tuple(v.co) for v in bmesh.from_edit_mesh(obj.data).verts]
    assert bpy.ops.character_designer.finger_flex(action='CAPTURE') == {'FINISHED'}
    origin, forward, bend, axis, _ = flex.guide_frame(bpy.context)
    assert bend.z < -.9999  # Top faces point up; flexion must go inward/down.
    assert abs(forward.y) > .9999
    if forward.y < 0: flex.state(bpy.context).flip_forward = True
    assert len(json.loads(flex.state(bpy.context).guide)['faces']) == 8
    select_chain(rig)
    before_bones = snapshot(rig)
    lines = flex.preview_lines(bpy.context)
    assert all(lines)
    # The first purple hinge is centered on bone Head, not on the surface.
    midpoint = (Vector(lines[3][0]) + Vector(lines[3][1])) * .5
    assert (midpoint - rig.data.edit_bones['f_index.01.L'].head).length < 1e-6
    assert abs(midpoint.z) < 1e-6 and abs(origin.z - .15) < 1e-6
    assert snapshot(rig) == before_bones
    assert bpy.ops.character_designer.finger_flex(action='APPLY') == {'FINISHED'}
    for b in rig.data.edit_bones:
        if b.select:
            assert b.x_axis.dot(Vector((-1, 0, 0))) > .9999
            assert (Quaternion(b.x_axis, .1) @ (b.tail-b.head) - (b.tail-b.head)).z < 0
    activate(obj, 'EDIT')
    assert [tuple(v.co) for v in bmesh.from_edit_mesh(obj.data).verts] == before_mesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[-1].co.z += .01
    refused(lambda: flex.guide_frame(bpy.context), 'changed')


def test_invalid_strips_and_legacy_direction():
    obj, rig = make_top_grid(columns=2, rows=2)
    refused(lambda: flex.capture(bpy.context), 'without branches or closed loops')
    obj, rig = make_top_grid()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.faces[3].select_set(False)
    refused(lambda: flex.capture(bpy.context), 'continuous top strip')
    obj, rig = fixture()
    settings = flex.state(bpy.context)
    data = json.loads(settings.guide)
    data['schema'] = 1
    data['face'] = data.pop('faces')[0]['index']
    data.pop('normal_sign')
    settings.guide = json.dumps(data)
    assert flex.guide_frame(bpy.context)[2].z > .99  # Never silently invert saved v1 guides.


def test_saved_guide():
    obj, rig = make_top_grid()
    flex.capture(bpy.context)
    flex.state(bpy.context).flip_bend = True
    object_mode()
    expected = flex.guide_frame(bpy.context)
    with tempfile.TemporaryDirectory(prefix='finger-flex-') as folder:
        path = os.path.join(folder, 'guide.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        actual = flex.guide_frame(bpy.context)
        assert all((a-b).length < 1e-6 for a,b in zip(actual[:4], expected[:4]))
        assert flex.state(bpy.context).flip_bend


character_designer.register()
try:
    tests = [test_rotation_preview_and_preservation, test_flip_and_invalid_selection,
             test_edge_ambiguity_and_stale_guide, test_rollback_and_pose_guard,
             test_shared_edge_and_scaled_normal, test_object_edit_axis_equivalence,
             test_eight_face_top_strip_and_center_hinges, test_invalid_strips_and_legacy_direction,
             test_saved_guide]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('FINGER_FLEX_PASSED', len(tests), flush=True)
finally:
    character_designer.unregister()
