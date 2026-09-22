"""One unnumbered Mark action: automatic order, replacement and atomic reads."""
import sys
from pathlib import Path

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_loop_marks_blender import (
    fixture, rows, edit_mesh, select_loop, selection, marks, C, bank,
    fingerprint, rig_state, slot_state, assert_rig_equal, assert_artist_data,
    refused, rolls, chain_names, expected_axis_targets, assert_chain,
    character_designer,
)


def select_many(obj, choices, *, open_last=False):
    """choices are (finger key, sleeve row); selection order has no meaning."""
    bm = edit_mesh(obj)
    C.tool_settings.mesh_select_mode = (False, True, False)
    for sequence in (bm.faces, bm.edges, bm.verts):
        for item in sequence: item.select_set(False)
    for position, (key, row) in enumerate(choices):
        ids = rows(obj, key)[row]
        pairs = list(zip(ids, ids[1:]+ids[:1]))
        if open_last and position == len(choices)-1: pairs.pop()
        for a, b in pairs:
            edge = bm.edges.get((bm.verts[a], bm.verts[b]))
            assert edge is not None
            edge.select_set(True)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def saved_rows(obj, key='INDEX.L'):
    stored = marks.records(obj, key)
    sleeve = rows(obj, key)
    return [next(i for i, row in enumerate(sleeve) if set(row) == set(stored[name]['ids']))
            for name in ('1', '2') if name in stored]


def test_two_selected_loops_sort_automatically_and_preserve_artist_data():
    obj, rig = fixture()
    mesh, bones, setup = fingerprint(obj), rig_state(rig), slot_state(obj)
    select_many(obj, [('INDEX.L', 5), ('INDEX.L', 1)])
    selected = selection(obj)
    result = marks.mark_selected(C)
    assert list(result) == ['1', '2'] and saved_rows(obj) == [1, 5]
    assert result['1']['fraction'] < result['2']['fraction']
    assert selection(obj) == selected
    assert fingerprint(obj) == mesh and slot_state(obj) == setup
    assert_rig_equal(bones, rig_state(rig))
    assert C.mode == 'EDIT_MESH' and C.edit_object == obj
    assert_artist_data(obj)


def test_singles_accept_distal_first_and_remarking_is_idempotent():
    obj, _ = fixture()
    select_loop(obj, 'INDEX.L', 5)
    result = marks.mark_selected(C)
    assert list(result) == ['1'] and saved_rows(obj) == [5]
    select_loop(obj, 'INDEX.L', 1)
    marks.mark_selected(C)
    assert saved_rows(obj) == [1, 5]
    saved = obj[marks.PROPERTY]
    for row in (1, 5, 1):
        select_loop(obj, 'INDEX.L', row)
        marks.mark_selected(C)
        assert obj[marks.PROPERTY] == saved


def test_single_new_loop_replaces_nearest_existing_mark():
    obj, _ = fixture()
    select_many(obj, [('INDEX.L', 1), ('INDEX.L', 5)])
    marks.mark_selected(C)
    select_loop(obj, 'INDEX.L', 2)
    marks.mark_selected(C)
    assert saved_rows(obj) == [2, 5]
    select_loop(obj, 'INDEX.L', 4)
    marks.mark_selected(C)
    assert saved_rows(obj) == [2, 4]


def test_tied_replacement_is_atomic_and_selecting_both_resolves_it():
    obj, rig = fixture()
    select_many(obj, [('INDEX.L', 1), ('INDEX.L', 5)])
    marks.mark_selected(C)
    select_loop(obj, 'INDEX.L', 3)
    saved, mesh, bones, selected = obj[marks.PROPERTY], fingerprint(obj), rig_state(rig), selection(obj)
    message = refused(lambda: marks.mark_selected(C))
    assert 'equally close' in message and 'select both' in message
    assert obj[marks.PROPERTY] == saved and fingerprint(obj) == mesh
    assert selection(obj) == selected
    assert_rig_equal(bones, rig_state(rig))
    select_many(obj, [('INDEX.L', 4), ('INDEX.L', 2)])
    marks.mark_selected(C)
    assert saved_rows(obj) == [2, 4]


def test_all_selected_components_validate_before_any_marker_write():
    obj, rig = fixture()
    select_many(obj, [('INDEX.L', 1), ('INDEX.L', 5)])
    marks.mark_selected(C)
    saved, mesh, bones = obj[marks.PROPERTY], fingerprint(obj), rig_state(rig)
    invalid = (
        ([('INDEX.L', 2), ('MIDDLE.L', 4)], False),
        ([('INDEX.L', 2), ('INDEX.R', 4)], False),
        ([('INDEX.L', 2), ('INDEX.L', 4)], True),
        ([('INDEX.L', 1), ('INDEX.L', 3), ('INDEX.L', 5)], False),
        ([('INDEX.L', 2), ('INDEX.L', 0)], False),
    )
    for choices, open_last in invalid:
        select_many(obj, choices, open_last=open_last)
        selected = selection(obj)
        refused(lambda: marks.mark_selected(C))
        assert obj[marks.PROPERTY] == saved
        assert fingerprint(obj) == mesh and selection(obj) == selected
        assert_rig_equal(bones, rig_state(rig))


def test_legacy_single_slot_is_sorted_and_other_finger_marks_are_retained():
    obj, _ = fixture()
    # Compatibility API may have left only internal slot 2 populated.
    select_loop(obj, 'INDEX.L', 5)
    marks.mark(C, 2)
    select_loop(obj, 'INDEX.L', 1)
    marks.mark_selected(C)
    assert saved_rows(obj) == [1, 5]
    index = marks.records(obj, 'INDEX.L')
    bank.select(C, 'MIDDLE', 'L')
    select_many(obj, [('MIDDLE.L', 4), ('MIDDLE.L', 2)])
    marks.mark_selected(C)
    middle = marks.records(obj, 'MIDDLE.L')
    assert marks.records(obj, 'INDEX.L') == index
    bank.select(C, 'INDEX', 'L')
    select_loop(obj, 'INDEX.L', 2)
    marks.mark_selected(C)
    assert saved_rows(obj) == [2, 5]
    assert marks.records(obj, 'MIDDLE.L') == middle


def test_auto_sorted_marks_align_only_active_chain_without_numbered_input():
    obj, rig = fixture()
    rig.data.use_mirror_x = True
    select_loop(obj, 'INDEX.L', 5)
    marks.mark_selected(C)
    select_loop(obj, 'INDEX.L', 1)
    marks.mark_selected(C)
    before, old_rolls, mesh = rig_state(rig), rolls(rig), fingerprint(obj)
    stored, selected = obj[marks.PROPERTY], selection(obj)
    left = chain_names('INDEX', 'L')
    wanted = expected_axis_targets(before, left)
    result = marks.align(C)
    assert result['chains'] == 1 and result['keys'] == ['INDEX.L']
    assert_chain(rig, before, old_rolls, left, wanted)
    assert_rig_equal(before, rig_state(rig), names=[name for name in before['bones'] if name not in left])
    assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == stored
    assert selection(obj) == selected and rig.data.use_mirror_x


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FINGER_AUTO_LOOP_MARKS_PASS', flush=True)
