"""Whole-ring artist edits preserve spatial definitions and isolate real damage.

Run in a disposable factory-startup Blender; never saves the user's scene.
"""
import copy
import json
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import (
    bound_fixture, hands_fixture as fixture, capture_all, select, character_designer, C,
    bank, definition, fingerprint, rig_state, assert_rig_equal, assert_artist_data,
)


def spatial_state(obj):
    result = {}
    for slot in obj.character_designer_finger_bank.slots:
        guide = slot.guide
        records = {}
        for field in ('record', 'bend_record'):
            value = json.loads(getattr(guide, field) or '{}')
            records[field] = {
                name: copy.deepcopy(value[name]) for name in ('internal', 'surface', 'direction') if name in value
            }
            for variant in ('basis', 'current'):
                if variant in value:
                    records[field][variant] = {name: copy.deepcopy(v) for name, v in value[variant].items()
                                               if name != 'coordinates'}
        result[slot.name] = (guide.revision, guide.confirmed, guide.flip_bend, guide.use_basis, records)
    return result


def edit_mesh(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    for sequence in (bm.verts, bm.edges, bm.faces):
        sequence.ensure_lookup_table()
        sequence.index_update()
    return bm


def update(obj, bm):
    for sequence in (bm.verts, bm.edges, bm.faces):
        sequence.index_update()
        sequence.ensure_lookup_table()
    bm.normal_update()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)


def body(obj, key='INDEX.L'):
    return json.loads(obj.character_designer_finger_bank.survey)['candidates'][key]


def split_ring(obj, key='INDEX.L', band=2):
    rows = body(obj, key)['rings']
    bm = edit_mesh(obj)
    before = len(bm.verts)
    expected = [bm.verts[a].co.lerp(bm.verts[b].co, .5) for a, b in zip(rows[band], rows[band+1])]
    edges = [bm.edges.get((bm.verts[a], bm.verts[b])) for a, b in zip(rows[band], rows[band+1])]
    assert all(edges), 'Fixture rows must have corresponding longitudinal edges'
    bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=True)
    update(obj, bm)
    obj.update_from_editmode()
    bm = edit_mesh(obj)
    assert len(bm.verts) == before+len(rows[0]), 'Exactly one complete cross-section must be added'
    new = []
    for point in expected:
        hits = [v for v in bm.verts if (v.co-point).length <= 1e-6]
        assert len(hits) == 1, 'Each new rail midpoint must have one unambiguous vertex'
        new.append(hits[0])
    assert len({v.index for v in new}) == len(rows[0])
    for a, b in zip(new, new[1:]+new[:1]):
        edge = bm.edges.get((a, b))
        assert edge and len(edge.link_faces) == 2, 'The new row must be a closed manifold loop'
        assert len(a.link_edges) == 4 and all(len(f.verts) == 4 for f in edge.link_faces), \
            'The subdivision must preserve a regular quad sleeve'
    return [v.index for v in new]


def dissolve_ring(obj, row):
    bm = edit_mesh(obj)
    before = len(bm.verts)
    selected = {bm.verts[i] for i in row}
    edges = [e for e in bm.edges if all(v in selected for v in e.verts)]
    assert len(edges) == len(row), 'Only a closed circumference is dissolved'
    bmesh.ops.dissolve_edges(bm, edges=edges, use_verts=True, use_face_split=False)
    update(obj, bm)
    obj.update_from_editmode()
    assert len(obj.data.vertices) == before-len(row), 'Exactly the selected complete ring must be removed'


def assert_frames(obj, excluding=()):
    for slot in obj.character_designer_finger_bank.slots:
        if slot.name in excluding: continue
        frame = definition.frame(bank.scoped(C, slot.guide), require_basis=True,
                                 require_bend=True, require_confirmed=True)
        assert frame['length'] > .5, slot.name


def test_one_sided_complete_split_preserves_all_definitions_and_artist_data():
    obj, rig, _ = bound_fixture()
    accepted, bones = spatial_state(obj), rig_state(rig)
    split_ring(obj)
    edited = fingerprint(obj)
    # Read-only consumers can resolve a proven remap before metadata catches up.
    assert_frames(obj)
    metadata = obj.character_designer_finger_bank
    metadata.status = 'Update failed; previous result retained: obsolete global topology failure'
    result = bank.adapt_topology(C)
    assert metadata.status == '', 'Recovered references must not keep a stale global failure banner'
    assert result['adapted'] == ['INDEX.L'], result
    assert len(result['unchanged']) == 9 and not result['failed'], result
    assert not result['survey']['warnings'], result['survey']['warnings']
    assert len(body(obj, 'INDEX.L')['rings']) == 8
    assert len(body(obj, 'INDEX.R')['rings']) == 7
    assert spatial_state(obj) == accepted
    assert fingerprint(obj) == edited
    assert_rig_equal(bones, rig_state(rig))
    assert_artist_data(obj)
    assert_frames(obj)
    metadata.status = 'Review the finger definitions before binding.'
    repeated = bank.adapt_topology(C)
    assert metadata.status == 'Review the finger definitions before binding.', 'Unrelated status must be retained'
    assert not repeated['adapted'] and len(repeated['unchanged']) == 10 and not repeated['failed']
    assert fingerprint(obj) == edited and spatial_state(obj) == accepted


def test_dissolve_redundant_ring_remaps_other_fingers_after_index_changes():
    obj, rig, _ = bound_fixture()
    accepted, bones = spatial_state(obj), rig_state(rig)
    dissolve_ring(obj, body(obj)['rings'][3])
    edited = fingerprint(obj)
    result = bank.adapt_topology(C)
    assert result['adapted'] == ['INDEX.L'] and not result['failed'], result
    assert len(result['unchanged']) == 9 and not result['survey']['warnings'], result
    assert len(body(obj)['rings']) == 6
    assert spatial_state(obj) == accepted and fingerprint(obj) == edited
    assert_rig_equal(bones, rig_state(rig))
    assert_artist_data(obj)
    assert_frames(obj)


def test_captured_bend_face_keeps_accepted_normal_when_that_band_is_split():
    obj, rig, chosen = bound_fixture()
    select(obj, [chosen['INDEX.L'][2]])
    definition.set_top(C)
    definition.confirm(C)
    bank.sync(C)
    accepted, bones = spatial_state(obj), rig_state(rig)
    split_ring(obj)
    edited = fingerprint(obj)
    result = bank.adapt_topology(C)
    assert not result['failed'] and not result['survey']['warnings'], result
    assert result['adapted'] == ['INDEX.L'], result
    assert spatial_state(obj) == accepted and fingerprint(obj) == edited
    assert_rig_equal(bones, rig_state(rig))
    assert_frames(obj)
    # Reconfirming after a valid one-sided edit must not reintroduce a strict
    # equal-topology error through the mirror writer.
    definition.confirm(C)
    bank.sync(C)
    assert all(not slot.error for slot in obj.character_designer_finger_bank.slots)
    assert not json.loads(obj.character_designer_finger_bank.survey)['warnings']
    assert fingerprint(obj) == edited
    assert_rig_equal(bones, rig_state(rig))
    assert_frames(obj)


def test_deformed_split_rejects_only_changed_reference_and_keeps_asymmetry_guard():
    obj, rig, _ = bound_fixture()
    accepted, bones = spatial_state(obj), rig_state(rig)
    original = obj.character_designer_finger_bank.slots['INDEX.L'].guide.record
    added = split_ring(obj)
    bm = edit_mesh(obj)
    bm.verts[added[0]].co.z += .015
    update(obj, bm)
    edited = fingerprint(obj)
    metadata = obj.character_designer_finger_bank
    metadata.status = 'Update failed; previous result retained: obsolete global topology failure'
    result = bank.adapt_topology(C)
    assert set(result['failed']) == {'INDEX.L'}, result
    assert metadata.status == '' and metadata.slots['INDEX.L'].error, 'Current local errors must remain local'
    assert len(result['unchanged']) == 9 and not result['adapted'], result
    assert set(result['survey']['warnings']) == {'INDEX'}, result['survey']['warnings']
    assert obj.character_designer_finger_bank.slots['INDEX.L'].guide.record == original
    assert spatial_state(obj) == accepted and fingerprint(obj) == edited
    assert_rig_equal(bones, rig_state(rig))
    assert_frames(obj, excluding=('INDEX.L',))


def test_missing_tip_invalidates_only_affected_finger():
    obj, rig, _ = bound_fixture()
    accepted, bones = spatial_state(obj), rig_state(rig)
    bm = edit_mesh(obj)
    tip = set(body(obj)['rings'][-1])
    cap = next(f for f in bm.faces if {v.index for v in f.verts} == tip)
    bm.faces.remove(cap)
    update(obj, bm)
    edited = fingerprint(obj)
    result = bank.adapt_topology(C)
    assert set(result['failed']) == {'INDEX.L'}, result
    assert set(result['survey']['warnings']) == {'INDEX'}, result['survey']['warnings']
    assert len(result['unchanged']) == 9, result
    assert spatial_state(obj) == accepted and fingerprint(obj) == edited
    assert_rig_equal(bones, rig_state(rig))
    assert_frames(obj, excluding=('INDEX.L',))


def test_dissolving_shape_defining_ring_is_not_an_equivalent_surface():
    obj, chosen = fixture()
    # A real bulge is part of the accepted source on both sides before capture.
    bm = edit_mesh(obj)
    for sign in (1, -1):
        # The fixture assigns 64 vertices to each finger, five fingers per side.
        first = (1 if sign == 1 else 6)*64
        row = [bm.verts[first+3*8+j] for j in range(8)]
        center = sum((v.co for v in row), Vector())/len(row)
        for vertex in row:
            delta = (vertex.co-center)*.14
            vertex.co += delta
            for name in bm.verts.layers.shape.keys():
                layer = bm.verts.layers.shape.get(name)
                vertex[layer] += delta
    update(obj, bm)
    capture_all(obj, chosen)
    bank.select(C, 'INDEX', 'L')
    assert not json.loads(obj.character_designer_finger_bank.survey)['warnings']
    accepted = spatial_state(obj)
    dissolve_ring(obj, body(obj)['rings'][3])
    edited = fingerprint(obj)
    result = bank.adapt_topology(C)
    assert set(result['failed']) == {'INDEX.L'}, result
    assert set(result['survey']['warnings']) == {'INDEX'}, result['survey']['warnings']
    assert len(result['unchanged']) == 9, result
    assert spatial_state(obj) == accepted and fingerprint(obj) == edited
    assert_frames(obj, excluding=('INDEX.L',))


if __name__ == '__main__':
    character_designer.register()
    tests = [fn for name, fn in list(globals().items()) if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('FINGER_TOPOLOGY_ADAPT_PASSED', len(tests), flush=True)
