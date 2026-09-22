"""Fresh loop marks survive artist edits without recapturing frozen Basic Setup.

Run only in a disposable Blender. These tests edit fixture meshes deliberately,
then prove Mark/Align leave that edited artist data and the opposite rig intact.
"""
import json
import sys
import traceback
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_loop_marks_blender import (
    fixture as base_fixture, rows, edit_mesh, selection, slot_state, rolls,
    chain_names, assert_chain, refused, marks, C, bank, definition, targets,
    fingerprint, rig_state, assert_rig_equal, character_designer,
)
from test_finger_topology_adapt_blender import update, split_ring
from character_designer import finger_detect as detect
from character_designer import finger_loop_marks_ui as mark_ui
from character_designer import finger_definition_ui as guides


def fixture():
    obj, rig = base_fixture()
    rig.data.use_mirror_x = True
    # Existing keys and weights come from bound_fixture. Add loop-domain artist
    # data too, so the read-only check includes UVs through local topology edits.
    bpy.ops.object.mode_set(mode='OBJECT')
    uv = obj.data.uv_layers.new(name='Artist UV')
    for loop, value in zip(obj.data.loops, uv.data):
        value.uv = (loop.vertex_index*.001, loop.index*.002)
    attribute = obj.data.attributes.new('Artist Float', 'FLOAT', 'POINT')
    for i, value in enumerate(attribute.data): value.value = i*.003
    bpy.ops.object.mode_set(mode='EDIT')
    return obj, rig


@contextmanager
def without_capture_validation():
    """Mark owns current loops; old capture proof must never gate its actions."""
    with ExitStack() as stack:
        for module, names in (
                (definition, ('_validate_mesh', 'frame')),
                (bank, ('validate_local', 'remap_record', 'recheck', 'adapt_topology', 'survey', 'dirty')),
                (detect, ('census',)),
                (targets, ('pair', 'reflection'))):
            for name in names:
                stack.enter_context(patch.object(module, name,
                    side_effect=AssertionError(f'Mark/Align called obsolete capture proof: {module.__name__}.{name}')))
        yield


def loop_points(obj, key, row):
    bm = edit_mesh(obj)
    return [tuple(bm.verts[i].co) for i in rows(obj, key)[row]]


def vertices_at(bm, points):
    """Locate current geometry after index changes; never use stale saved IDs."""
    vertices = []
    for point in map(Vector, points):
        found = [v for v in bm.verts if (v.co-point).length < 1e-6]
        assert len(found) == 1, 'Fixture loop must have unique current coordinates'
        vertices.append(found[0])
    return vertices


def axis(obj, key='INDEX.L'):
    body = json.loads(obj.character_designer_finger_bank.slots[key].guide.record)['body']
    return (Vector(body['tip'])-Vector(body['root'])).normalized()


def deform_loop(obj, points, along, *, slide=0., radial=.0):
    bm = edit_mesh(obj)
    vertices = vertices_at(bm, points)
    center = sum((v.co for v in vertices), Vector())/len(vertices)
    for i, vertex in enumerate(vertices):
        # Nonuniform radial changes invalidate old reference coordinates while
        # retaining a valid transverse planar ring and an internal bone axis.
        outward = vertex.co-center
        vertex.co += along*slide + outward*(radial if i % 2 else radial*.5)
    result = [tuple(v.co) for v in vertices]
    update(obj, bm)
    return result


def choose_loops(obj, loops, *, open_last=False):
    bm = edit_mesh(obj)
    C.tool_settings.mesh_select_mode = (False, True, False)
    for sequence in (bm.faces, bm.edges, bm.verts):
        for item in sequence: item.select_set(False)
    for i, points in enumerate(loops):
        vertices = vertices_at(bm, points)
        pairs = list(zip(vertices, vertices[1:]+vertices[:1]))
        if open_last and i == len(loops)-1: pairs.pop()
        for a, b in pairs:
            edge = bm.edges.get((a, b))
            assert edge is not None, 'Fixture ring must remain a current edge cycle'
            edge.select_set(True)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def dissolve_points(obj, points):
    bm = edit_mesh(obj)
    vertices = vertices_at(bm, points)
    edges = [bm.edges.get((a, b)) for a, b in zip(vertices, vertices[1:]+vertices[:1])]
    assert all(edges)
    count = len(bm.verts)
    bmesh.ops.dissolve_edges(bm, edges=edges, use_verts=True, use_face_split=False)
    update(obj, bm)
    bm = edit_mesh(obj)
    assert len(bm.verts) == count-len(points)


def assert_current_mark_and_alignment(obj, rig, loops, fractions):
    choose_loops(obj, loops)
    mesh, before, saved_setup = fingerprint(obj), rig_state(rig), slot_state(obj)
    old_rolls, selected = rolls(rig), selection(obj)
    state = obj.character_designer_finger_bank
    survey, key = state.survey, state.active
    names = chain_names(*key.split('.'))
    root, tip = Vector(before['bones'][names[0]]['head']), Vector(before['bones'][names[-1]]['tail'])
    expected = [root.lerp(tip, fraction) for fraction in fractions]
    with without_capture_validation():
        result = marks.mark_selected(C)
        assert list(result) == ['1', '2']
        assert result['1']['fraction'] < result['2']['fraction']
        assert fingerprint(obj) == mesh and selection(obj) == selected
        assert slot_state(obj) == saved_setup, 'Mark rewrote a frozen Capture reference'
        assert_rig_equal(before, rig_state(rig))
        stored = obj[marks.PROPERTY]
        aligned = marks.align(C)
    assert aligned['chains'] == 1 and aligned['keys'] == [key]
    assert_chain(rig, before, old_rolls, names, expected)
    assert_rig_equal(before, rig_state(rig), names=[name for name in before['bones'] if name not in names])
    assert fingerprint(obj) == mesh and selection(obj) == selected
    assert obj[marks.PROPERTY] == stored and state.survey == survey
    after_setup = slot_state(obj)
    for slot_key, previous in saved_setup.items():
        actual = after_setup[slot_key]
        # Align may update only this side's saved bone signature, never the
        # captured positions, reference revision, confirmation or cached error.
        assert actual[:4] == previous[:4] and actual[5:] == previous[5:]
        if slot_key != key: assert actual == previous
    assert C.mode == 'EDIT_MESH' and C.edit_object == obj and rig.data.use_mirror_x


def test_slide_and_reshape_selected_current_loops_without_recapture():
    obj, rig = fixture()
    first, second = loop_points(obj, 'INDEX.L', 2), loop_points(obj, 'INDEX.L', 4)
    along = axis(obj)
    body = json.loads(obj.character_designer_finger_bank.slots['INDEX.L'].guide.record)['body']
    length = (Vector(body['tip'])-Vector(body['root'])).length
    first = deform_loop(obj, first, along, slide=.035, radial=.10)
    second = deform_loop(obj, second, along, slide=-.025, radial=.07)
    # A legacy cached Capture failure is no authority over freshly selected
    # valid loops. Mark/Align must neither require Recheck nor clear that record.
    obj.character_designer_finger_bank.slots['INDEX.L'].error = (
        'This finger surface changed beyond complete loop subdivision/dissolve; rebind this finger only.')
    assert_current_mark_and_alignment(obj, rig, [second, first],
                                      (2/6+.035/length, 4/6-.025/length))


def test_insert_new_ring_and_dissolve_unmarked_ring_then_mark_current_topology():
    obj, rig = fixture()
    unmarked = loop_points(obj, 'INDEX.L', 4)
    distal = loop_points(obj, 'INDEX.L', 5)
    added = split_ring(obj, 'INDEX.L', band=2)
    bm = edit_mesh(obj)
    new = [tuple(bm.verts[i].co) for i in added]
    # Discard BMesh wrappers before later conversions/fingerprints; all saved
    # fixture selections below are coordinates, not invalidatable handles.
    del bm
    new = deform_loop(obj, new, axis(obj), radial=.12)
    dissolve_points(obj, unmarked)
    assert_current_mark_and_alignment(obj, rig, [distal, new], (2.5/6, 5/6))


def test_unmarked_source_edits_and_damaged_opposite_do_not_gate_current_marks():
    obj, rig = fixture()
    first, second = loop_points(obj, 'INDEX.L', 1), loop_points(obj, 'INDEX.L', 5)
    own_unmarked = loop_points(obj, 'INDEX.L', 3)
    unrelated = loop_points(obj, 'MIDDLE.L', 3)
    opposite = loop_points(obj, 'INDEX.R', 3)
    deform_loop(obj, own_unmarked, axis(obj), radial=.18)
    deform_loop(obj, unrelated, axis(obj, 'MIDDLE.L'), slide=.018, radial=.20)
    deform_loop(obj, opposite, axis(obj, 'INDEX.R'), slide=-.02, radial=.35)
    state = obj.character_designer_finger_bank
    state.slots['INDEX.R'].guide.record = ''
    state.slots['INDEX.R'].error = 'Opposite capture is unavailable'
    assert_current_mark_and_alignment(obj, rig, [second, first], (1/6, 5/6))


def test_invalid_current_selection_keeps_previous_marks_and_all_artist_data():
    obj, rig = fixture()
    first, second = loop_points(obj, 'INDEX.L', 1), loop_points(obj, 'INDEX.L', 5)
    choose_loops(obj, [first, second])
    marks.mark_selected(C)
    fresh = deform_loop(obj, loop_points(obj, 'INDEX.L', 2), axis(obj), slide=.025, radial=.1)
    choose_loops(obj, [fresh, second], open_last=True)
    stored, mesh, before, setup = obj[marks.PROPERTY], fingerprint(obj), rig_state(rig), slot_state(obj)
    selected = selection(obj)
    with without_capture_validation():
        refused(lambda: marks.mark_selected(C))
    assert obj[marks.PROPERTY] == stored and fingerprint(obj) == mesh and slot_state(obj) == setup
    assert selection(obj) == selected
    assert_rig_equal(before, rig_state(rig))


def test_missing_current_cap_refuses_alignment_after_valid_mark_without_mutation():
    obj, rig = fixture()
    first, second = loop_points(obj, 'INDEX.L', 2), loop_points(obj, 'INDEX.L', 4)
    first = deform_loop(obj, first, axis(obj), slide=.025, radial=.1)
    tip = set(rows(obj, 'INDEX.L')[-1])
    bm = edit_mesh(obj)
    cap = next(face for face in bm.faces if {v.index for v in face.verts} == tip)
    bm.faces.remove(cap)
    update(obj, bm)
    choose_loops(obj, [first, second])
    with without_capture_validation():
        marks.mark_selected(C)  # Mark itself only requires the selected cycles.
    stored, mesh, before, setup = obj[marks.PROPERTY], fingerprint(obj), rig_state(rig), slot_state(obj)
    with without_capture_validation():
        refused(lambda: marks.align(C))
    assert obj[marks.PROPERTY] == stored and fingerprint(obj) == mesh and slot_state(obj) == setup
    assert_rig_equal(before, rig_state(rig))


def test_twenty_five_geometry_updates_retain_saved_loop_display_without_rebuild():
    obj, _ = fixture()
    first, second = loop_points(obj, 'INDEX.L', 2), loop_points(obj, 'INDEX.L', 4)
    choose_loops(obj, [first, second])
    marks.mark_selected(C)
    C.scene.character_designer_finger_definition.overlays_enabled = True
    mark_ui.refresh(C)
    guides.show(invalidate=False)
    guides.display_frames(C)
    cached = mark_ui._cache[obj.as_pointer()]
    visible = mark_ui.visible(C)[0][1]
    stored, setup = obj[marks.PROPERTY], slot_state(obj)
    # Keep the native handle only for intentional fixture edits. Add-on paths
    # are forbidden from reading edit geometry or regenerating saved polylines.
    native_edit = bmesh.from_edit_mesh
    indices = tuple(rows(obj, 'INDEX.L')[2])
    step = axis(obj)*.0001
    with without_capture_validation(), \
         patch.object(definition, '_snapshot', side_effect=AssertionError('Passive mesh snapshot')), \
         patch.object(bmesh, 'from_edit_mesh', side_effect=AssertionError('Passive edit-geometry read')), \
         patch.object(marks, 'points', side_effect=AssertionError('Passive mark cache rebuild')):
        for _ in range(25):
            bm = native_edit(obj.data)
            bm.verts.ensure_lookup_table()
            for index in indices: bm.verts[index].co += step
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            C.view_layer.update()
            assert mark_ui.visible(C)[0][1] is visible
            guides.display_frames(C)
    assert mark_ui._cache[obj.as_pointer()] is cached
    assert obj[marks.PROPERTY] == stored and slot_state(obj) == setup


def main():
    character_designer.register()
    tests = [fn for name, fn in globals().items() if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('FINGER_MARK_EDITING_PASS', len(tests), flush=True)


if __name__ == '__main__':
    try: main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
