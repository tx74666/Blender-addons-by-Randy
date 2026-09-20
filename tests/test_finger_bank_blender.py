"""Unbound paired hands: geometric identity, local persistence and warning recovery."""
import json
import math
import sys
import tempfile
from pathlib import Path
import bpy
import bmesh
from mathutils import Vector, Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'addons'))
import character_designer
from character_designer import finger_bank as bank, finger_detect as detect, finger_definition as definition, finger_layout as layout

C = bpy.context


def fixture():
    if C.object and C.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    for obj in list(bpy.data.objects): bpy.data.objects.remove(obj, do_unlink=True)
    vertices, faces, chosen = [], [], {}
    specs = [('THUMB', (-.6, -.4, 0), (-.55, .83, 0), .75),
             ('INDEX', (-.3, 0, 0), (-.07, 1, 0), 1.05),
             ('MIDDLE', (-.1, .025, 0), (0, 1, 0), 1.15),
             ('RING', (.1, .005, 0), (.03, 1, 0), 1.07),
             ('PINKY', (.3, -.045, 0), (.15, 1, 0), .82)]
    for side, sign in (('L', 1), ('R', -1)):
        for digit, root, tangent, length in specs:
            root, t = Vector(root)+Vector((3, 0, 0)), Vector(tangent).normalized()
            across = Vector((t.y, -t.x, 0))
            first, face_first = len(vertices), len(faces)
            for i in range(7):
                for j in range(8):
                    p = root+t*(length*i/6)+across*(.065*math.cos(j*math.tau/8))+Vector((0, 0, .065*math.sin(j*math.tau/8)))
                    vertices.append((sign*p.x, p.y, p.z))
            for i in range(6):
                for j in range(8):
                    a, b = first+i*8+j, first+i*8+(j+1)%8
                    f = (a, a+8, b+8, b)
                    faces.append(f if sign > 0 else tuple(reversed(f)))
            f = tuple(reversed(range(first+48, first+56)))
            faces.append(f if sign > 0 else tuple(reversed(f)))
            for j in range(8):
                p = root-t*.5+across*(.5*math.cos(j*math.tau/8))+Vector((0, 0, .5*math.sin(j*math.tau/8)))
                vertices.append((sign*p.x, p.y, p.z))
            for j in range(8):
                a, b = first+j, first+(j+1)%8
                f = (a, b, first+56+(j+1)%8, first+56+j)
                faces.append(f if sign > 0 else tuple(reversed(f)))
            chosen[f'{digit}.{side}'] = [face_first+i*8+2 for i in range(6)]
    mesh = bpy.data.meshes.new('Hands')
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new('UnboundHands', mesh)
    C.collection.objects.link(obj)
    C.view_layer.objects.active = obj
    obj.select_set(True)
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Artist')
    for p in key.data: p.co.z += .01
    obj.vertex_groups.new(name='ArtistWeight').add(list(range(len(vertices))), .4, 'REPLACE')
    bpy.ops.object.mode_set(mode='EDIT')
    C.tool_settings.mesh_select_mode = (False, False, True)
    return obj, chosen


def select(obj, ids):
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
        for item in seq: item.select_set(False)
    for i in ids: bm.faces[i].select_set(True)
    bmesh.update_edit_mesh(obj.data)


def refused(fn):
    try: fn()
    except ValueError: return
    raise AssertionError('Ambiguous capture accepted')


def capture_all(obj, chosen):
    for digit in ('PINKY', 'INDEX', 'THUMB', 'RING', 'MIDDLE'):
        select(obj, chosen[f'{digit}.L'])
        assert bank.capture(C) == f'{digit}.L'
        definition.confirm(C)
        bank.sync(C)


def test_five_pairs_without_binding():
    obj, chosen = fixture()
    before = layout.fingerprint(obj)
    capture_all(obj, chosen)
    assert layout.fingerprint(obj) == before
    b = obj.character_designer_finger_bank
    assert len(b.slots) == 10
    assert all(s.guide.record and s.guide.confirmed and not s.error for s in b.slots)
    assert not json.loads(b.survey)['warnings']
    assert all(len(c['rings']) == 7 for c in json.loads(b.survey)['candidates'].values())
    saved = {s.name: s.guide.record for s in b.slots}
    select(obj, chosen['INDEX.L'])
    bank.capture(C)
    assert all(s.guide.record == saved[s.name] for s in b.slots if not s.name.startswith('INDEX'))


def test_reject_cross_finger_and_pending_isolation():
    obj, chosen = fixture()
    capture_all(obj, chosen)
    b = obj.character_designer_finger_bank
    saved = {s.name: s.guide.record for s in b.slots}
    select(obj, chosen['INDEX.L']+chosen['MIDDLE.L'])
    refused(lambda: bank.capture(C))
    assert {s.name: s.guide.record for s in b.slots} == saved
    select(obj, chosen['INDEX.L'][:1])
    bank.capture(C, 'START')
    select(obj, chosen['MIDDLE.L'][-1:])
    refused(lambda: bank.capture(C, 'END'))
    assert b.slots['INDEX.L'].guide.pending
    select(obj, chosen['INDEX.L'][-1:])
    bank.capture(C, 'END')
    assert not b.slots['INDEX.L'].guide.pending
    assert b.slots['MIDDLE.L'].guide.record == saved['MIDDLE.L']


def test_loop_capture_and_nonbasis():
    obj, chosen = fixture()
    select(obj, chosen['INDEX.L'])
    bank.capture(C)
    c = json.loads(obj.character_designer_finger_bank.survey)['candidates']['INDEX.L']
    select(obj, [])
    C.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    ring = c['rings'][3]
    for i, v in enumerate(ring): bm.edges.get((bm.verts[v], bm.verts[ring[(i+1)%len(ring)]])).select_set(True)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 1
    bpy.ops.object.mode_set(mode='EDIT')
    before = layout.fingerprint(obj)
    assert bank.capture(C) == 'INDEX.L'
    assert layout.fingerprint(obj) == before and obj.active_shape_key_index == 1
    assert definition.frame(C)['label'] == 'Internal straight axis'
    assert definition.frame(C)['length'] > 1
    # A loop is only the marker: changing its supporting rest geometry must
    # invalidate the full-body axis, even while a different key is active.
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    tip_vertex = bm.verts[c['rings'][-1][0]]
    basis = bm.verts.layers.shape.get('Basis')
    original = tip_vertex[basis].copy()
    tip_vertex[basis] = original+Vector((0, .02, 0))
    refused(lambda: definition.frame(C))
    tip_vertex[basis] = original
    assert definition.frame(C)['length'] > 1
    # A local connection change cannot be hidden by unchanged coordinates.
    bm.faces.ensure_lookup_table()
    face = bm.faces[c['faces'][0]]
    corners = list(face.verts)
    bm.faces.remove(face)
    bm.faces.new(corners[:3])
    bm.faces.new((corners[0], corners[2], corners[3]))
    bmesh.update_edit_mesh(obj.data)
    refused(lambda: definition.frame(C))


def test_asymmetry_warning_and_recovery():
    obj, chosen = fixture()
    capture_all(obj, chosen)
    b = obj.character_designer_finger_bank
    region = json.loads(b.survey)['candidates']['RING.R']
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    vertex = bm.verts[region['rings'][3][0]]
    original = vertex.co.copy()
    vertex.co.z += .008
    assert bank.dirty(C)
    before = layout.fingerprint(obj)
    bank.recheck(C)
    assert layout.fingerprint(obj) == before
    assert set(json.loads(b.survey)['warnings']) == {'RING'}
    assert b.slots['INDEX.L'].guide.confirmed
    vertex.co = original
    bank.recheck(C)
    assert not json.loads(b.survey)['warnings']
    assert not b.slots['RING.R'].error


def test_layout_one_finger_preserves_other_four():
    obj, chosen = fixture()
    capture_all(obj, chosen)
    b = obj.character_designer_finger_bank
    bank.select(C, 'INDEX', 'L')
    definition.confirm(C)
    layout.capture_definition(C)
    layout.apply_layout(C)
    assert 'INDEX' in json.loads(b.survey)['warnings']
    for digit in detect.DIGITS:
        bank.select(C, digit, 'L')
        assert definition.frame(C)['length'] > .7
        assert not b.slots[f'{digit}.L'].error, b.slots[f'{digit}.L'].error
    bank.select(C, 'MIDDLE', 'L')
    layout.capture_definition(C)
    layout.apply_layout(C)
    bank.select(C, 'INDEX', 'L')
    assert definition.frame(C)['length'] > 1


def test_save_reopen_and_character_scope():
    obj, chosen = fixture()
    capture_all(obj, chosen)
    with tempfile.TemporaryDirectory(prefix='finger-bank-') as temp:
        path = str(Path(temp)/'hands.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        obj = C.edit_object
        assert len(obj.character_designer_finger_bank.slots) == 10
        assert all(s.guide.confirmed for s in obj.character_designer_finger_bank.slots)
        assert definition.frame(C)['length'] > 1
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.mesh.primitive_cube_add()
    assert bank.active_object(C) is None


def test_missing_opposite_tip_recovered_without_recapture():
    obj, chosen = fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    cap = bm.faces[chosen['INDEX.R'][0]-2+48]
    vertices = list(cap.verts)
    bm.faces.remove(cap)
    bmesh.update_edit_mesh(obj.data)
    select(obj, chosen['INDEX.L'])
    assert bank.capture(C) == 'INDEX.L'
    b = obj.character_designer_finger_bank
    assert 'INDEX' in json.loads(b.survey)['warnings']
    assert b.slots['INDEX.L'].guide.record and not b.slots['INDEX.R'].guide.record
    bm.faces.new(vertices)
    bmesh.update_edit_mesh(obj.data)
    bank.recheck(C)
    assert not json.loads(b.survey)['warnings']
    assert b.slots['INDEX.R'].guide.record and not b.slots['INDEX.R'].error


def test_ambiguous_order_and_view_independent_identity():
    generic = [{'root': [i, 0, 0], 'tip': [i, 1, 0]} for i in range(5)]
    refused(lambda: detect.order_hand(generic))
    obj, chosen = fixture()
    obj.matrix_world = Matrix.Translation((7, 2, -3)) @ Matrix.Rotation(1.1, 4, 'Z')
    select(obj, chosen['THUMB.R'])
    assert bank.capture(C) == 'THUMB.R'
    b = obj.character_designer_finger_bank
    assert not json.loads(b.survey)['warnings']
    select(obj, chosen['MIDDLE.L'][:1])
    bank.capture(C, 'START')
    assert not b.slots['MIDDLE.L'].guide.record, 'Pending Start inherited another finger definition'
    assert b.slots['MIDDLE.L'].guide.pending


if __name__ == '__main__':
    character_designer.register()
    tests = [v for k, v in list(globals().items()) if k.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('FINGER_BANK_PASSED', len(tests), flush=True)
