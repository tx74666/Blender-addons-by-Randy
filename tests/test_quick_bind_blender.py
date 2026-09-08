"""Native transfer / heat weighting, preservation, and transactional failure tests."""

import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import quick_bind as service
from character_designer.selected_bone_weights import _capture_vertex_groups


def reset():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def mesh(name, coords, faces):
    data = bpy.data.meshes.new(name)
    data.from_pydata(coords, [], faces)
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def rig(spec=(('A', (0, 0, 0), (0, 0, 1)), ('B', (2, 0, 0), (2, 0, 1)),
              ('Unused', (4, 0, 0), (4, 0, 1)))):
    obj = bpy.data.objects.new('Rig', bpy.data.armatures.new('Rig'))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for name, head, tail in spec:
        bone = obj.data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
    control = obj.data.edit_bones.new('CTRL')
    control.head, control.tail = (0, 0, 0), (0, 1, 0)
    control.use_deform = False
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


def assign(obj, name, assignments, lock=False):
    group = obj.vertex_groups.new(name=name)
    for index, weight in assignments:
        group.add((index,), weight, 'REPLACE')
    group.lock_weight = lock
    return group


def weight(obj, name, vertex):
    try:
        return obj.vertex_groups[name].weight(vertex)
    except RuntimeError:
        return 0.0


def fixture():
    reset()
    armature = rig()
    body = mesh('Body', [(0, 0, 0), (2, 0, 0), (0, 2, 0)], [(0, 1, 2)])
    # Deliberately non-normalized source groups (sum 1.25) must be normalized.
    assign(body, 'A', [(0, 1), (1, .5), (2, .5)])
    assign(body, 'B', [(0, .25), (1, .75), (2, .75)])
    assign(body, 'CTRL', [(i, 1) for i in range(3)])
    assign(body, 'Modeling', [(i, 1) for i in range(3)])
    body.modifiers.new('Armature', 'ARMATURE').object = armature
    target = mesh('Clothes', [(.5, .5, .1), (1, .5, .1), (.5, 1, .1)], [(0, 1, 2)])
    assign(target, 'A', [(i, 1) for i in range(3)])
    assign(target, 'Unused', [(i, .8) for i in range(3)])
    assign(target, 'SculptMask', [(0, .123), (2, .456)], lock=True)
    return target, body, armature


def mesh_state(obj):
    return (obj.data.as_pointer(), tuple(tuple(v.co) for v in obj.data.vertices),
            tuple(tuple(p.vertices) for p in obj.data.polygons),
            tuple((layer.name, tuple(tuple(item.uv) for item in layer.data)) for layer in obj.data.uv_layers),
            tuple((key.name, key.value, tuple(tuple(v.co) for v in key.data))
                  for key in obj.data.shape_keys.key_blocks) if obj.data.shape_keys else (),
            tuple(tuple(row) for row in obj.matrix_world), obj.parent)


def test_native_interpolation_preserves_and_repeats():
    target, body, armature = fixture()
    target.data.uv_layers.new(name='UVMap')
    for i, loop in enumerate(target.data.uv_layers.active.data):
        loop.uv = (i * .2, .7)
    target.shape_key_add(name='Basis')
    key = target.shape_key_add(name='Artist')
    key.data[0].co.z += .25
    key.value = .6
    body.shape_key_add(name='Basis')
    body_key = body.shape_key_add(name='PosedSurface')
    for vertex in body_key.data:
        vertex.co.x += 10
    body_key.value = 1
    mirror = target.modifiers.new('Mirror artist settings', 'MIRROR')
    mirror.use_axis = (False, True, False)
    smooth = target.modifiers.new('Subdivision', 'SUBSURF')
    original_state = mesh_state(target)
    source_state = mesh_state(body), _capture_vertex_groups(body)
    artist_group = _capture_vertex_groups(target)[2]
    armature.pose.bones['A'].location = (7, 3, 1)
    bpy.context.view_layer.update()
    objects_before = set(bpy.data.objects.keys())
    datablocks_before = (len(bpy.data.meshes), len(bpy.data.armatures), len(bpy.data.shape_keys))
    for _ in range(2):
        result = service.bind_weights(bpy.context, target, armature, body=body)
        assert result['vertex_count'] == 3
        for vertex, a in enumerate((.6, .5, .5)):
            assert abs(weight(target, 'A', vertex) - a) < 1e-5
            assert abs(weight(target, 'B', vertex) - (1-a)) < 1e-5
            assert weight(target, 'Unused', vertex) == 0
        assert target.vertex_groups.get('CTRL') is None
        assert target.vertex_groups.get('Modeling') is None
        assert next(g for g in _capture_vertex_groups(target) if g['name'] == 'SculptMask') == artist_group
        assert mesh_state(target) == original_state
        assert (mesh_state(body), _capture_vertex_groups(body)) == source_state
        assert tuple(m.type for m in target.modifiers) == ('MIRROR', 'ARMATURE', 'SUBSURF')
        assert target.modifiers[1].object == armature
        assert set(bpy.data.objects.keys()) == objects_before
        assert (len(bpy.data.meshes), len(bpy.data.armatures), len(bpy.data.shape_keys)) == datablocks_before
    assert tuple(armature.pose.bones['A'].location) == (7, 3, 1)


def test_source_mirror_and_world_space():
    reset()
    armature = rig((('Bone.L', (1, 0, 0), (1, 0, 1)), ('Bone.R', (-1, 0, 0), (-1, 0, 1))))
    body = mesh('Half Body', [(1, 0, 0), (2, 0, 0), (1, 2, 0)], [(0, 1, 2)])
    assign(body, 'Bone.L', [(i, 1) for i in range(3)])
    body.modifiers.new('Mirror', 'MIRROR')
    body.matrix_world = Matrix.Translation((0, 0, 3))
    target = mesh('Both Sides', [(1.2, .2, 3), (-1.2, .2, 3), (1.1, .1, 3)], [(0, 1, 2)])
    service.bind_weights(bpy.context, target, armature, body=body)
    assert abs(weight(target, 'Bone.L', 0)-1) < 1e-6
    assert abs(weight(target, 'Bone.R', 1)-1) < 1e-6


def expect_failure(call, substring):
    try:
        call()
    except service.QuickBindError as exc:
        assert substring in str(exc), str(exc)
    else:
        raise AssertionError('Expected QuickBindError')


def test_locked_and_conflicting_binding_refused():
    target, body, armature = fixture()
    target.vertex_groups['A'].lock_weight = True
    before = _capture_vertex_groups(target)
    expect_failure(lambda: service.bind_weights(bpy.context, target, armature, body=body), 'Unlock')
    assert _capture_vertex_groups(target) == before
    target.vertex_groups['A'].lock_weight = False
    target.modifiers.new('Unassigned Armature', 'ARMATURE')
    expect_failure(lambda: service.bind_weights(bpy.context, target, armature, body=body), 'conflicting')
    modifier = target.modifiers[0]
    modifier.object = armature
    modifier.show_viewport = False
    expect_failure(lambda: service.bind_weights(bpy.context, target, armature, body=body), 'enable the Armature')
    assert not modifier.show_viewport
    modifier.show_viewport = True
    modifier.vertex_group = 'SculptMask'
    expect_failure(lambda: service.bind_weights(bpy.context, target, armature, body=body), 'Vertex Group mask')
    assert modifier.vertex_group == 'SculptMask'
    modifier.vertex_group = ''
    mirror = target.modifiers.new('Mirror', 'MIRROR')
    mirror.use_mirror_vertex_groups = False
    before = _capture_vertex_groups(target)
    expect_failure(lambda: service.bind_weights(bpy.context, target, armature, body=body), 'enable Vertex Groups')
    assert not mirror.use_mirror_vertex_groups
    assert _capture_vertex_groups(target) == before
    mirror.use_mirror_vertex_groups = True
    source_mirror = body.modifiers.new('Mirror', 'MIRROR')
    source_mirror.use_mirror_vertex_groups = False
    expect_failure(lambda: service.bind_weights(bpy.context, target, armature, body=body), 'Body: enable Vertex Groups')


def test_solver_and_commit_failure_rollback():
    target, body, armature = fixture()
    existing = target.modifiers.new('Original', 'ARMATURE')
    existing.object = armature
    existing.use_vertex_groups = False
    existing.use_bone_envelopes = True
    before = _capture_vertex_groups(target), mesh_state(target)
    objects = set(bpy.data.objects.keys())
    original_run = service._run_auto
    def fail_auto(*args):
        raise RuntimeError('injected heat failure')
    service._run_auto = fail_auto
    try:
        expect_failure(lambda: service.bind_weights(bpy.context, target, armature, mode='AUTO'), 'injected heat failure')
    finally:
        service._run_auto = original_run
    original_write = service._write_weights
    def fail_write(obj, weights):
        obj.vertex_groups['A'].add((0,), .125, 'REPLACE')
        obj.vertex_groups.new(name='B').add((0,), 1, 'REPLACE')
        raise RuntimeError('injected write failure')
    service._write_weights = fail_write
    try:
        expect_failure(lambda: service.bind_weights(bpy.context, target, armature, body=body), 'injected write failure')
    finally:
        service._write_weights = original_write
    assert (_capture_vertex_groups(target), mesh_state(target)) == before
    assert len(target.modifiers) == 1
    assert not existing.use_vertex_groups and existing.use_bone_envelopes
    assert set(bpy.data.objects.keys()) == objects
    assert not service.has_binding_backup(target)


def test_native_auto_weights_and_deform():
    reset()
    armature = rig((('Foot', (0, 0, -.5), (0, 0, .5)),))
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=1)
    target = bpy.context.object
    assign(target, 'ArtistMask', [(0, .75)])
    before = mesh_state(target)
    result = service.bind_weights(bpy.context, target, armature, mode='AUTO')
    assert result['group_count'] == 1
    assert all(abs(weight(target, 'Foot', i)-1) < 1e-6 for i in range(len(target.data.vertices)))
    assert weight(target, 'ArtistMask', 0) == .75
    assert mesh_state(target) == before
    armature.pose.bones['Foot'].location.x = .25
    bpy.context.view_layer.update()
    evaluated = target.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert abs(evaluated.data.vertices[0].co.x - target.data.vertices[0].co.x - .25) < 1e-5


def test_native_auto_mirror_retains_base_mesh():
    reset()
    armature = rig((('Foot.L', (1, 0, -.3), (1, 0, .3)),
                    ('Foot.R', (-1, 0, -.3), (-1, 0, .3))))
    target = mesh('Half Shoe', [(.5, -.5, -.5), (1.5, -.5, -.5), (1.5, .5, -.5), (.5, .5, -.5),
                                (.5, -.5, .5), (1.5, -.5, .5), (1.5, .5, .5), (.5, .5, .5)],
                  [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    mirror = target.modifiers.new('Live Mirror', 'MIRROR')
    before = mesh_state(target)
    service.bind_weights(bpy.context, target, armature, mode='AUTO')
    assert mesh_state(target) == before
    assert len(target.data.vertices) == 8
    assert all(weight(target, 'Foot.L', i) > .99 for i in range(8))
    assert target.vertex_groups.get('Foot.R') is not None
    assert target.modifiers[0] == mirror
    armature.pose.bones['Foot.L'].location.x = .25
    bpy.context.view_layer.update()
    evaluated = target.evaluated_get(bpy.context.evaluated_depsgraph_get())
    positive = [v.co.x for v in evaluated.data.vertices if v.co.x > 0]
    negative = [v.co.x for v in evaluated.data.vertices if v.co.x < 0]
    assert abs(min(positive) - .75) < 1e-4
    assert abs(max(negative) + .5) < 1e-4


def group_values(obj):
    return {state['name']: (state['lock_weight'], state['weights'])
            for state in _capture_vertex_groups(obj)}


def test_persistent_first_binding_restore():
    for had_modifier in (False, True):
        target, body, armature = fixture()
        original = group_values(target)
        if had_modifier:
            modifier = target.modifiers.new('Original Armature', 'ARMATURE')
            modifier.object = armature
            modifier.use_vertex_groups = False
            modifier.use_bone_envelopes = True
        service.bind_weights(bpy.context, target, armature, body=body)
        baseline = target[service.BACKUP_KEY]
        target.vertex_groups['A'].add((0,), .1, 'REPLACE')
        service.bind_weights(bpy.context, target, armature, body=body)
        assert target[service.BACKUP_KEY] == baseline, 'Repeated bind must keep the first baseline'
        target.modifiers[0].name = 'Artist renamed Armature'
        with tempfile.TemporaryDirectory(prefix='character-designer-quick-bind-') as directory:
            path = str(Path(directory) / 'binding.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            target, armature = bpy.data.objects['Clothes'], bpy.data.objects['Rig']
            assert service.has_binding_backup(target)
            assert target[service.BACKUP_KEY] == baseline
            # Later artist work outside tracked groups and rig growth must survive.
            target.vertex_groups['SculptMask'].add((1,), .9, 'REPLACE')
            assign(target, 'Later Artist Group', [(2, .37)], lock=True)
            bpy.context.view_layer.objects.active = armature
            bpy.ops.object.mode_set(mode='EDIT')
            bone = armature.data.edit_bones.new('Later Hair Bone')
            bone.head, bone.tail = (0, 0, 3), (0, 0, 4)
            bpy.ops.object.mode_set(mode='OBJECT')
            assign(target, 'Later Hair Bone', [(0, .3)])
            target.data.vertices[0].co.z += .05
            target.modifiers[0].use_deform_preserve_volume = True
            target.modifiers[0].show_render = False
            target.modifiers.new('Later Subdivision', 'SUBSURF')
            geometric = mesh_state(target)
            later = group_values(target)
            result = service.restore_binding(bpy.context, target)
            assert result['removed_modifier'] == (not had_modifier)
            values = group_values(target)
            assert values['A'] == original['A'] and values['Unused'] == original['Unused']
            assert 'B' not in values
            for name in ('SculptMask', 'Later Artist Group', 'Later Hair Bone'):
                assert values[name] == later[name]
            assert mesh_state(target) == geometric
            assert not service.has_binding_backup(target)
            assert target.modifiers[-1].name == 'Later Subdivision'
            if had_modifier:
                assert target.modifiers[0].name == 'Artist renamed Armature'
                assert not target.modifiers[0].use_vertex_groups
                assert target.modifiers[0].use_bone_envelopes
                assert target.modifiers[0].use_deform_preserve_volume
                assert not target.modifiers[0].show_render
            else:
                assert len(target.modifiers) == 1


def test_restore_refuses_changed_topology_and_replaced_modifier():
    target, body, armature = fixture()
    expect_failure(lambda: service.restore_binding(bpy.context, target), '0.43.0')
    service.bind_weights(bpy.context, target, armature, body=body)
    baseline = target[service.BACKUP_KEY]
    target.data.polygons[0].flip()
    before = group_values(target)
    expect_failure(lambda: service.restore_binding(bpy.context, target), 'topology')
    assert group_values(target) == before and target[service.BACKUP_KEY] == baseline
    target.data.polygons[0].flip()
    old = target.modifiers[0]
    name = old.name
    target.modifiers.remove(old)
    replacement = target.modifiers.new(name, 'ARMATURE')
    replacement.object = rig()
    expect_failure(lambda: service.restore_binding(bpy.context, target), 'replaced')
    assert target.modifiers[0] == replacement and group_values(target) == before
    # Same-slot/same-rig replacement is explicitly treated as binding continuity.
    replacement.object = armature
    service.restore_binding(bpy.context, target)
    assert not service.has_binding_backup(target) and not target.modifiers
    service.bind_weights(bpy.context, target, armature, body=body)
    target.modifiers.remove(target.modifiers[0])
    service.restore_binding(bpy.context, target)
    assert not service.has_binding_backup(target) and not target.modifiers


for test in (test_native_interpolation_preserves_and_repeats, test_source_mirror_and_world_space,
             test_locked_and_conflicting_binding_refused, test_solver_and_commit_failure_rollback,
             test_native_auto_weights_and_deform, test_native_auto_mirror_retains_base_mesh,
             test_persistent_first_binding_restore, test_restore_refuses_changed_topology_and_replaced_modifier):
    test()
    print('PASS', test.__name__)
print('QUICK_BIND_OK')
