"""Explicit existing-loop marks and transactional rest-joint alignment.

Disposable fixture tests only. No topology generation or retired joint workflow
is imported, and this script never opens or saves an artist's blend file.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import (
    bound_fixture, character_designer, C, bank, definition, targets, fingerprint,
    rig_state, assert_rig_equal, assert_artist_data, close,
)
from character_designer import finger_loop_marks as marks, finger_chain as chain


def fixture():
    obj, rig, _ = bound_fixture()
    rig.data.use_mirror_x = False
    bank.select(C, 'INDEX', 'L')
    return obj, rig


def edit_mesh(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    for sequence in (bm.verts, bm.edges, bm.faces):
        sequence.ensure_lookup_table()
        sequence.index_update()
    return bm


def rows(obj, key):
    return json.loads(obj.character_designer_finger_bank.slots[key].guide.record)['body']['rings']


def select_loop(obj, key, row, *, omit_last=False):
    indices = rows(obj, key)[row]
    bm = edit_mesh(obj)
    C.tool_settings.mesh_select_mode = (False, True, False)
    for sequence in (bm.faces, bm.edges, bm.verts):
        for item in sequence:
            item.select_set(False)
    pairs = list(zip(indices, indices[1:]+indices[:1]))
    if omit_last:
        pairs.pop()
    for a, b in pairs:
        edge = bm.edges.get((bm.verts[a], bm.verts[b]))
        assert edge is not None
        edge.select_set(True)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return indices


def selection(obj):
    bm = edit_mesh(obj)
    return tuple(tuple(item.select for item in sequence) for sequence in (bm.verts, bm.edges, bm.faces))


def mark_pair(obj, key='INDEX.L', *, first=1, second=5):
    digit, side = key.split('.')
    bank.select(C, digit, side)
    for number, row in ((1, first), (2, second)):
        select_loop(obj, key, row)
        marks.mark(C, number)
    return marks.records(obj, key)


def slot_state(obj):
    return {slot.name: (slot.guide.record, slot.guide.bend_record, slot.guide.revision,
                        slot.guide.confirmed, slot.bones, slot.error)
            for slot in obj.character_designer_finger_bank.slots}


def rolls(rig):
    with targets.edit_rig(C, rig):
        return {bone.name: bone.roll for bone in rig.data.edit_bones}


def refused(fn, message=None):
    try:
        fn()
    except ValueError as exc:
        if message is not None:
            assert str(exc) == message, str(exc)
        return str(exc)
    raise AssertionError('Invalid loop-mark operation was accepted')


def chain_names(digit, side):
    return [f'f_{digit.lower()}.{i:02d}.{side}' for i in (1, 2, 3)]


def expected_axis_targets(before, names, fractions=(1/6, 5/6)):
    root = Vector(before['bones'][names[0]]['head'])
    tip = Vector(before['bones'][names[-1]]['tail'])
    return [root.lerp(tip, value) for value in fractions]


def assert_chain(rig, before, old_rolls, names, junctions):
    after = rig_state(rig)
    close(after['bones'][names[0]]['head'], before['bones'][names[0]]['head'])
    close(after['bones'][names[-1]]['tail'], before['bones'][names[-1]]['tail'])
    for a, b, target in zip(names, names[1:], junctions):
        close(after['bones'][a]['tail'], target)
        close(after['bones'][b]['head'], target)
    for name in names:
        for field in ('parent', 'connected', 'deform'):
            assert after['bones'][name][field] == before['bones'][name][field]
    current_rolls = rolls(rig)
    for name, value in old_rolls.items():
        assert abs(current_rolls[name]-value) < 1e-6, name


def test_marking_two_existing_loops_changes_only_marker_metadata():
    obj, rig = fixture()
    # Add ordinary artist layers to make the read-only contract broader than
    # positions alone, while preserving the captured reference geometry.
    bpy.ops.object.mode_set(mode='OBJECT')
    uv = obj.data.uv_layers.new(name='Artist UV')
    for loop, value in zip(obj.data.loops, uv.data):
        value.uv = (loop.vertex_index*.001, loop.index*.002)
    attribute = obj.data.attributes.new('Artist Float', 'FLOAT', 'POINT')
    for i, value in enumerate(attribute.data):
        value.value = i*.003
    bpy.ops.object.mode_set(mode='EDIT')
    before, bones, bindings = fingerprint(obj), rig_state(rig), slot_state(obj)
    for number, row in ((1, 1), (2, 5)):
        chosen = select_loop(obj, 'INDEX.L', row)
        selected = selection(obj)
        result = marks.mark(C, number)
        assert set(result['ids']) == set(chosen)
        assert len(result['coordinates']) == 8
        assert selection(obj) == selected
        assert fingerprint(obj) == before
        assert C.mode == 'EDIT_MESH' and C.edit_object == obj
    saved = marks.records(obj, 'INDEX.L')
    assert set(saved) == {'1', '2'} and marks.records(obj, 'INDEX.R') == {}
    assert saved['1']['fraction'] < saved['2']['fraction']
    with patch.object(definition, '_snapshot', side_effect=AssertionError('Drawing marker points copied a mesh')), \
         patch.object(bmesh, 'from_edit_mesh', side_effect=AssertionError('Drawing marker points read edit geometry')):
        display = marks.points(obj, 'INDEX.L')
        assert set(display) == {1, 2}
        assert display[1] == tuple(map(tuple, saved['1']['coordinates']))
        assert display[2] == tuple(map(tuple, saved['2']['coordinates']))
    assert slot_state(obj) == bindings
    assert_rig_equal(bones, rig_state(rig))
    assert_artist_data(obj)


def test_four_straight_fingers_keep_fixed_axis_endpoints_roll_and_other_side():
    obj, rig = fixture()
    # Put each whole existing chain slightly off the surface centroid axis.
    # Copying loop centroids would wrongly bend it back toward the mesh center.
    with targets.edit_rig(C, rig):
        for digit in ('INDEX', 'MIDDLE', 'RING', 'PINKY'):
            for i, name in enumerate(chain_names(digit, 'L')):
                bone = rig.data.edit_bones[name]
                bone.head.z += .012
                bone.tail.z += .012
                bone.roll = (.2, -.35, .6)[i]
                if i:
                    bone.use_connect = True
    for digit in ('INDEX', 'MIDDLE', 'RING', 'PINKY'):
        saved = mark_pair(obj, digit+'.L')
        before, old_rolls = rig_state(rig), rolls(rig)
        mesh, marked = fingerprint(obj), obj[marks.PROPERTY]
        names = chain_names(digit, 'L')
        wanted = expected_axis_targets(before, names)
        assert abs(wanted[0].z-Vector(saved['1']['center']).z) > .01
        result = marks.align(C)
        assert result['chains'] == 1 and result['keys'] == [digit+'.L']
        assert result['changed'] > 0
        assert_chain(rig, before, old_rolls, names, wanted)
        assert_rig_equal(before, rig_state(rig), names=[name for name in before['bones'] if name not in names])
        assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == marked
        assert C.mode == 'EDIT_MESH' and C.edit_object == obj


def test_x_mirror_does_not_expand_active_side_alignment():
    obj, rig = fixture()
    rig.data.use_mirror_x = True
    mark_pair(obj)
    before, old_rolls = rig_state(rig), rolls(rig)
    mesh, marked, selected = fingerprint(obj), obj[marks.PROPERTY], selection(obj)
    left = chain_names('INDEX', 'L')
    wanted = expected_axis_targets(before, left)
    bindings = slot_state(obj)
    result = marks.align(C)
    assert result['chains'] == 1 and result['keys'] == ['INDEX.L']
    assert_chain(rig, before, old_rolls, left, wanted)
    assert_rig_equal(before, rig_state(rig), names=[name for name in before['bones'] if name not in left])
    after_bindings = slot_state(obj)
    assert all(after_bindings[key] == value for key, value in bindings.items() if key != 'INDEX.L')
    assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == marked
    assert marks.records(obj, 'INDEX.R') == {}
    assert selection(obj) == selected and rig.data.use_mirror_x


def test_wrong_finger_partial_boundary_and_reversed_marks_are_rejected():
    obj, rig = fixture()
    before, bones = fingerprint(obj), rig_state(rig)
    for key, row, partial in (('MIDDLE.L', 2, False), ('INDEX.R', 2, False),
                              ('INDEX.L', 2, True), ('INDEX.L', 0, False)):
        select_loop(obj, key, row, omit_last=partial)
        refused(lambda: marks.mark(C, 1))
        assert marks.records(obj, 'INDEX.L') == {}
    mark_pair(obj, first=5, second=1)
    stored = obj[marks.PROPERTY]
    refused(lambda: marks.align(C))
    assert obj[marks.PROPERTY] == stored
    assert fingerprint(obj) == before
    assert_rig_equal(bones, rig_state(rig))


def test_damaged_opposite_geometry_and_missing_reference_do_not_gate_active_side():
    obj, rig = fixture()
    mark_pair(obj)
    rig.data.use_mirror_x = True
    bm = edit_mesh(obj)
    for vi in rows(obj, 'INDEX.R')[3]:
        bm.verts[vi].co.z += .008
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    state = obj.character_designer_finger_bank
    mate = state.slots['INDEX.R']
    mate.guide.record = ''
    mate.error = 'Opposite finger is missing and asymmetric'
    report = json.loads(state.survey)
    report['warnings']['INDEX'] = 'Old pair warning must not gate this side'
    state.survey = json.dumps(report)
    state.slots['INDEX.L'].error = report['warnings']['INDEX']
    before, bones, stored = fingerprint(obj), rig_state(rig), obj[marks.PROPERTY]
    bindings = slot_state(obj)
    actual_reference, actual_resolve = marks._reference, targets.resolve
    def active_reference(owner, key, reader):
        assert key == 'INDEX.L', 'Read opposite reference'
        return actual_reference(owner, key, reader)
    def active_resolve(owner, armature, key, candidates, *args, **kwargs):
        assert key == 'INDEX.L', 'Resolved opposite bones'
        return actual_resolve(owner, armature, key, candidates, *args, **kwargs)
    with patch.object(marks, '_reference', side_effect=active_reference), \
         patch.object(targets, 'resolve', side_effect=active_resolve), \
         patch.object(targets, 'pair', side_effect=AssertionError('Compared the other side')), \
         patch.object(targets, 'reflection', side_effect=AssertionError('Reflected onto the other side')), \
         patch.object(bank, '_mirrored_ring_surface', side_effect=AssertionError('Validated opposite mesh')):
        result = marks.align(C)
    assert result['keys'] == ['INDEX.L'] and result['chains'] == 1
    assert fingerprint(obj) == before and obj[marks.PROPERTY] == stored and rig.data.use_mirror_x
    assert_rig_equal(bones, rig_state(rig), names=[name for name in bones['bones'] if name not in chain_names('INDEX', 'L')])
    after_bindings = slot_state(obj)
    assert all(after_bindings[key] == value for key, value in bindings.items() if key != 'INDEX.L')


def test_unmarked_side_never_borrows_opposite_marks_even_with_x_mirror():
    obj, rig = fixture()
    rig.data.use_mirror_x = True
    mark_pair(obj)
    bank.select(C, 'INDEX', 'R')
    mesh, bones, stored, bindings = fingerprint(obj), rig_state(rig), obj[marks.PROPERTY], slot_state(obj)
    assert marks.effective_key(obj, 'INDEX.R', True) == 'INDEX.R'
    assert marks.effective_key(obj, 'INDEX.R', False) == 'INDEX.R'
    refused(lambda: marks.align(C), 'Mark two joint loops first.')
    assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == stored and slot_state(obj) == bindings
    assert_rig_equal(bones, rig_state(rig))


def test_active_right_marks_work_with_no_left_reference_and_leave_left_untouched():
    obj, rig = fixture()
    rig.data.use_mirror_x = True
    mark_pair(obj, 'INDEX.R')
    left = obj.character_designer_finger_bank.slots['INDEX.L']
    left.guide.record = ''
    left.error = 'No left reference'
    before, old_rolls = rig_state(rig), rolls(rig)
    mesh, bindings, stored = fingerprint(obj), slot_state(obj), obj[marks.PROPERTY]
    names = chain_names('INDEX', 'R')
    result = marks.align(C)
    assert result['keys'] == ['INDEX.R'] and result['chains'] == 1
    assert_chain(rig, before, old_rolls, names, expected_axis_targets(before, names))
    assert_rig_equal(before, rig_state(rig), names=[name for name in before['bones'] if name not in names])
    assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == stored and rig.data.use_mirror_x
    after_bindings = slot_state(obj)
    assert all(after_bindings[key] == value for key, value in bindings.items() if key != 'INDEX.R')


def test_current_unmarked_surface_edit_does_not_require_old_capture_equivalence():
    obj, rig = fixture()
    rig.data.use_mirror_x = True
    mark_pair(obj)
    state = obj.character_designer_finger_bank
    state.slots['INDEX.L'].error = bank.PAIR_SYNC_WARNING
    bm = edit_mesh(obj)
    bm.verts[rows(obj, 'INDEX.L')[3][0]].co.z += .025
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    before, bones, stored, bindings = fingerprint(obj), rig_state(rig), obj[marks.PROPERTY], slot_state(obj)
    with patch.object(definition, '_validate_mesh', side_effect=AssertionError('Read old mesh proof')), \
         patch.object(bank, 'remap_record', side_effect=AssertionError('Remapped old capture')):
        result = marks.align(C)
    assert result['keys'] == ['INDEX.L'] and result['chains'] == 1
    assert fingerprint(obj) == before and obj[marks.PROPERTY] == stored
    after_bindings = slot_state(obj)
    assert all(after_bindings[key] == value for key, value in bindings.items() if key != 'INDEX.L')
    assert state.slots['INDEX.L'].error == bank.PAIR_SYNC_WARNING and rig.data.use_mirror_x
    assert_rig_equal(bones, rig_state(rig), names=[name for name in bones['bones']
                                                if name not in chain_names('INDEX', 'L')])


def test_changed_marked_loop_requires_remark_without_losing_saved_marks():
    obj, rig = fixture()
    mark_pair(obj)
    bm = edit_mesh(obj)
    bm.verts[rows(obj, 'INDEX.L')[1][0]].co.z += .015
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    before, bones, stored, bindings = fingerprint(obj), rig_state(rig), obj[marks.PROPERTY], slot_state(obj)
    refused(lambda: marks.align(C), 'A marked loop changed; mark that loop again.')
    assert fingerprint(obj) == before and obj[marks.PROPERTY] == stored and slot_state(obj) == bindings
    assert_rig_equal(bones, rig_state(rig))
    select_loop(obj, 'INDEX.L', 1)
    marks.mark_selected(C)
    result = marks.align(C)
    assert result['keys'] == ['INDEX.L'] and fingerprint(obj) == before


def test_failure_after_bone_and_binding_writes_rolls_back_everything():
    obj, rig = fixture()
    rig.data.use_mirror_x = True
    mark_pair(obj)
    before, old_rolls = rig_state(rig), rolls(rig)
    mesh, bindings, marked = fingerprint(obj), slot_state(obj), obj[marks.PROPERTY]
    selected, mesh_id = selection(obj), obj.data.as_pointer()
    original = chain._commit_plan
    def fail_after_commit(plan):
        original(plan)
        raise ValueError('Injected binding commit failure')
    with patch.object(chain, '_commit_plan', side_effect=fail_after_commit):
        refused(lambda: marks.align(C), 'Injected binding commit failure')
    assert_rig_equal(before, rig_state(rig))
    restored_rolls = rolls(rig)
    assert all(abs(restored_rolls[name]-value) < 1e-6 for name, value in old_rolls.items())
    assert fingerprint(obj) == mesh and obj.data.as_pointer() == mesh_id
    assert slot_state(obj) == bindings and obj[marks.PROPERTY] == marked
    assert selection(obj) == selected and C.mode == 'EDIT_MESH' and C.edit_object == obj


def test_unchanged_marked_loop_survives_vertex_index_renumbering():
    obj, rig = fixture()
    mark_pair(obj)
    marked = obj[marks.PROPERTY]
    old = marks.records(obj, 'INDEX.L')
    bm = edit_mesh(obj)
    bm.verts.sort(key=lambda vertex: -vertex.index)
    bm.verts.index_update()
    bm.verts.ensure_lookup_table()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    obj.update_from_editmode()
    assert any((obj.data.vertices[i].co-Vector(point)).length > .01
               for i, point in zip(old['1']['ids'], old['1']['coordinates']))
    before, mesh = rig_state(rig), fingerprint(obj)
    wanted = expected_axis_targets(before, chain_names('INDEX', 'L'))
    result = marks.align(C)
    assert result['keys'] == ['INDEX.L']
    after = rig_state(rig)
    for i, target in enumerate(wanted, 1):
        close(after['bones'][f'f_index.{i:02d}.L']['tail'], target)
        close(after['bones'][f'f_index.{i+1:02d}.L']['head'], target)
    assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == marked
    assert_artist_data(obj)


def test_thumb_junctions_follow_original_curved_chain_without_straightening():
    obj, rig = fixture()
    body = json.loads(obj.character_designer_finger_bank.slots['THUMB.L'].guide.record)['body']
    root, tip = Vector(body['root']), Vector(body['tip'])
    old_nodes = [root, root.lerp(tip, .3)+Vector((0, 0, .022)),
                 root.lerp(tip, .65)+Vector((0, 0, .028)), tip]
    names = chain_names('THUMB', 'L')
    with targets.edit_rig(C, rig):
        for name in list(rig.data.edit_bones.keys()):
            if name.startswith('f_thumb.') and name.endswith('.L'):
                rig.data.edit_bones.remove(rig.data.edit_bones[name])
        parent = None
        for i, name in enumerate(names):
            bone = rig.data.edit_bones.new(name)
            bone.head, bone.tail = old_nodes[i], old_nodes[i+1]
            bone.parent, bone.use_connect, bone.roll = parent, bool(parent), (.2, -.3, .6)[i]
            parent = bone
    mark_pair(obj, 'THUMB.L')
    before, old_rolls, mesh = rig_state(rig), rolls(rig), fingerprint(obj)
    # Planes at 1/6 and 5/6 cross the first/last pieces of the original curve.
    wanted = [old_nodes[0].lerp(old_nodes[1], (1/6)/.3),
              old_nodes[2].lerp(old_nodes[3], ((5/6)-.65)/(1.-.65))]
    result = marks.align(C)
    assert result['keys'] == ['THUMB.L'] and result['chains'] == 1
    assert all(point.z > .01 for point in wanted)
    assert_chain(rig, before, old_rolls, names, wanted)
    assert_rig_equal(before, rig_state(rig), names=[name for name in before['bones'] if name not in names])
    assert fingerprint(obj) == mesh
    assert_artist_data(obj)


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FINGER_LOOP_MARKS_PASS', flush=True)
