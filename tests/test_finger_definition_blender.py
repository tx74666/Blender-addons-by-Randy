"""Unified read-only references and their guarded paired-roll consumers."""
import json
import sys
import tempfile
from pathlib import Path

import bpy
import bmesh
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import single_finger_fixture as fixture, fingerprint as snap
from test_finger_flex_blender import fixture as flex_fixture, activate, select_chain, refused
import character_designer
from character_designer import finger_definition as definition, finger_flex as flex

C = bpy.context


def reset():
    definition.clear(C)
    C.tool_settings.mesh_select_mode = (False, False, True)


def select_faces(obj, indices):
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
        for item in seq: item.select_set(False)
    for i in indices: bm.faces[i].select_set(True)
    bmesh.update_edit_mesh(obj.data)


def test_nonbasis_readonly_and_basis_reference():
    reset()
    obj = fixture(rooted=True)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 1
    bpy.ops.object.mode_set(mode='EDIT')
    before = snap(obj)
    active = obj.active_shape_key_index
    frame = definition.capture(C)
    assert not frame['basis'] and frame['key'] == 'Artist'
    assert frame['length'] > 3.3, frame
    assert frame['direction'].z > .9 and frame['direction_label'] == 'Detected tip'
    assert snap(obj) == before and obj.active_shape_key_index == active
    definition.confirm(C)
    old = frame['path']
    base = definition.basis_reference(C)
    assert base['basis'] and any((a-b).length > .01 for a, b in zip(old, base['path']))
    assert snap(obj) == before and obj.active_shape_key_index == active
    definition.confirm(C)
    assert snap(obj) == before


def test_surface_without_loops_is_still_a_definition():
    reset()
    obj, rig = flex_fixture()
    before = snap(obj)
    frame = definition.capture(C)
    assert abs(frame['length']-1) < 1e-6 and frame['bend'] is not None
    definition.confirm(C)
    assert snap(obj) == before
    assert definition.frame(C)['length'] == frame['length']


def test_edge_path_and_separate_markers():
    reset()
    obj = fixture(rooted=True)
    select_faces(obj, [])
    C.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for i in range(6): bm.edges.get((bm.verts[i*8], bm.verts[(i+1)*8])).select_set(True)
    before = snap(obj)
    f = definition.capture(C)
    assert f['label'] == 'Edge path' and len(f['path']) == 7 and f['bend'] is None
    assert snap(obj) == before
    first, last = f['root'].copy(), f['tip'].copy()
    definition.swap(C)
    assert (definition.frame(C)['root']-last).length < 1e-6
    assert not definition.state(C).confirmed
    C.tool_settings.mesh_select_mode = (False, False, True)
    select_faces(obj, [0])
    definition.mark(C)
    select_faces(obj, [40])
    definition.mark(C, end=True)
    f = definition.frame(C)
    assert abs(f['length']-2.5) < .01 and f['bend'] is None
    assert f['direction'].z > .99
    select_faces(obj, [0, 40])
    f = definition.capture(C)
    assert f['label'] == 'Two markers' and abs(f['length']-2.5) < .01


def test_stale_markers_and_source_guards():
    reset()
    obj = fixture()
    select_faces(obj, [0])
    definition.mark(C)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.x += .1
    select_faces(obj, [40])
    refused(lambda: definition.mark(C, end=True), 'changed')
    definition.capture(C)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.objects.remove(obj, do_unlink=True)
    refused(lambda: definition.frame(C), 'Capture')


def test_bone_reference_top_surface_and_paired_roll():
    reset()
    obj, rig = flex_fixture()
    select_chain(rig)
    f = definition.capture(C)
    assert f['label'] == 'Rest bone chain' and abs(f['length']-3) < 1e-6
    assert f['bend'] is None
    definition.confirm(C)
    refused(lambda: flex.apply(C), 'undefined')
    activate(obj, 'EDIT')
    definition.set_top(C)
    definition.confirm(C)
    select_chain(rig)
    assert flex.apply(C) == 6
    assert definition.frame(C)['bend'].z < -.99
    assert flex.apply(C) == 6
    activate(rig, 'POSE')
    rig.pose.bones['f_index.01.L'].rotation_mode = 'XYZ'
    rig.pose.bones['f_index.01.L'].rotation_euler.x = .5
    C.view_layer.update()
    rest = definition.capture(C)
    assert (rest['tip']-Vector((.6, 3, 0))).length < 1e-6


def test_mesh_definition_paired_roll_and_confirm_guard():
    reset()
    obj, rig = flex_fixture()
    definition.capture(C)
    select_chain(rig)
    refused(lambda: flex.apply(C), 'Confirm')
    definition.confirm(C)
    assert flex.apply(C) == 6
    definition.state(C).flip_bend = True
    refused(lambda: flex.apply(C), 'Confirm')
    definition.confirm(C)
    assert flex.apply(C) == 6
    assert definition.frame(C)['bend'].z > .99


def test_world_transform_and_explicit_swap():
    reset()
    obj = fixture(rooted=True)
    obj.matrix_world = Matrix.Translation((2, 3, 4)) @ Matrix.Diagonal((1.2, .8, 1.4, 1))
    C.view_layer.update()
    f = definition.capture(C)
    assert f['length'] > 4.6
    definition.confirm(C)
    definition.swap(C)


def test_persistence():
    reset()
    obj = fixture(rooted=True)
    definition.capture(C)
    definition.confirm(C)
    before = snap(obj)
    path = [tuple(p) for p in definition.frame(C)['path']]
    with tempfile.TemporaryDirectory(prefix='finger-definition-') as directory:
        filename = str(Path(directory)/'reference.blend')
        bpy.ops.wm.save_as_mainfile(filepath=filename)
        bpy.ops.wm.open_mainfile(filepath=filename)
        assert snap(C.edit_object) == before
        assert [tuple(p) for p in definition.frame(C)['path']] == path
        assert definition.state(C).confirmed


def test_transaction_and_explicit_top():
    reset()
    obj = fixture(rooted=True)
    definition.capture(C)
    select_faces(obj, [16])
    definition.set_top(C)
    definition.confirm(C)
    expected = definition.frame(C)['bend'].copy()
    previous = definition.state(C).record
    obj.scale.z = 0
    C.view_layer.update()
    refused(lambda: definition.capture(C), 'zero scale')
    assert definition.state(C).record == previous and definition.state(C).confirmed
    obj.scale.z = 1
    C.view_layer.update()
    assert definition.frame(C)['bend'].dot(expected) > .999
    definition.swap(C)
    assert definition.frame(C)['bend'].dot(expected) > .999


def test_large_key_rotation_keeps_endpoint_identities():
    reset()
    obj = fixture(rooted=True)
    select_faces(obj, [8*i for i in range(6)])
    bpy.ops.object.mode_set(mode='OBJECT')
    for point in obj.data.shape_keys.key_blocks['Artist'].data:
        point.co.z = -point.co.z
    obj.active_shape_key_index = 1
    bpy.ops.object.mode_set(mode='EDIT')
    current = definition.capture(C)
    basis = definition.basis_reference(C)
    assert current['direction'].z < -.9 and basis['direction'].z > .9


if __name__ == '__main__':
    character_designer.register()
    tests = [value for name, value in list(globals().items()) if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('FINGER_DEFINITION_PASSED', len(tests), flush=True)
