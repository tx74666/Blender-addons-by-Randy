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
from character_designer import finger_detect as detect
sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import hands_fixture as fixture, select, refused, capture_all, bank, definition, fingerprint

C = bpy.context


def test_five_pairs_without_binding():
    obj, chosen = fixture()
    before = fingerprint(obj)
    capture_all(obj, chosen)
    assert fingerprint(obj) == before
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
    before = fingerprint(obj)
    assert bank.capture(C) == 'INDEX.L'
    assert fingerprint(obj) == before and obj.active_shape_key_index == 1
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
    before = fingerprint(obj)
    bank.recheck(C)
    assert fingerprint(obj) == before
    assert set(json.loads(b.survey)['warnings']) == {'RING'}
    assert b.slots['INDEX.L'].guide.confirmed
    vertex.co = original
    bank.recheck(C)
    assert not json.loads(b.survey)['warnings']
    assert not b.slots['RING.R'].error


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
