"""Saved guides remain frozen while editing; explicit actions still validate."""
import json
import sys
from contextlib import contextmanager, ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import bound_fixture, character_designer, C, bank, definition, fingerprint
from character_designer import finger_definition_ui as ui, finger_preview_cache as cache
from character_designer import finger_bone_tools as bones


def fixture():
    bones.hide()
    bones.invalidate()
    ui.hide(invalidate=True)
    obj, rig, selected = bound_fixture()
    C.scene.character_designer_finger_definition.overlays_enabled = True
    C.view_layer.update()
    ui.redraw()
    return obj, rig, selected


def select(digit, mode='SINGLE'):
    result = bpy.ops.character_designer.finger_setup(action='SELECT', digit=digit, selection_mode=mode)
    assert result == {'FINISHED'}, result


def all_frames():
    select('THUMB'); select('PINKY', 'RANGE')
    frames = ui.display_frames(C)
    assert len(frames) == 5 and not ui._display_cache['errors']
    return frames


def saved_metadata(obj):
    """Reference/error/binding bytes must not be rewritten by visual reads."""
    state = obj.character_designer_finger_bank
    return (state.survey, state.needs_recheck, tuple(
        (slot.name, slot.error, slot.bones, slot.guide.record, slot.guide.revision,
         slot.guide.confirmed, slot.guide.use_basis, slot.guide.flip_bend,
         slot.guide.bend_record,
         slot.guide.source.name if slot.guide.source else None,
         slot.guide.bend_source.name if slot.guide.bend_source else None)
        for slot in state.slots))


@contextmanager
def no_reads():
    """Check call counts too: a handler must not swallow a forbidden read."""
    with ExitStack() as stack:
        forbidden = [stack.enter_context(patch.object(module, name, side_effect=AssertionError(
            'Saved display invoked live geometry validation: '+name)))
            for module, name in ((definition, '_snapshot'), (definition, '_topology'),
                                 (definition, 'frame'), (bank, 'dirty'), (bank, 'adapt_topology'))]
        yield
        assert all(call.call_count == 0 for call in forbidden), [call.call_count for call in forbidden]


def event(data, *, geometry=True):
    graph = SimpleNamespace(updates=[SimpleNamespace(
        id=data, is_updated_geometry=geometry, is_updated_transform=not geometry)])
    ui._geometry_changed(C.scene, graph)
    bones._dirty(C.scene, graph)


def edited_vertex(obj):
    """Keep the BMesh wrapper alive for every returned element reference."""
    record = json.loads(obj.character_designer_finger_bank.slots['INDEX.L'].guide.record)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return bm, bm.verts[record['basis']['coordinates'][0][0]]


def test_cold_panel_overlay_and_warm_clicks_never_read_geometry():
    obj, _, _ = fixture()
    before, saved = fingerprint(obj), saved_metadata(obj)
    with no_reads():
        select('INDEX')
        first = ui.cached_frame(C)[0]
        frames = ui.display_frames(C)
        assert first is frames[0] and len(frames) == 1 and first['display_only']
        all_frames()
        for _ in range(5):
            for digit in bank.detect.DIGITS:
                select(digit)
                assert len(ui.display_frames(C)) == 1 and ui.cached_frame(C)[0]
                C.view_layer.update()
                assert len(ui.display_frames(C)) == 1
                select(digit, 'CLICK')
                assert ui.display_frames(C) == ()
                select(digit, 'CLICK')
                assert len(ui.display_frames(C)) == 1
        all_frames()
        bpy.ops.character_designer.finger_setup(action='TOGGLE')
        assert cache._entries and ui.display_frames(C) == ()
        bpy.ops.character_designer.finger_setup(action='TOGGLE')
        assert len(ui.display_frames(C)) == 5
        ui.redraw()
        assert not cache._entries
        assert len(ui.display_frames(C)) == 5  # A cold rebuild is also scan-free.
    assert fingerprint(obj) == before and saved_metadata(obj) == saved


def test_25_real_edit_updates_keep_frozen_visible_hidden_and_cold_guides():
    obj, rig, _ = fixture()
    originals = {frame['bank_key']: frame for frame in all_frames()}
    saved, before = saved_metadata(obj), fingerprint(obj)
    bm, vertex = edited_vertex(obj)
    initial = vertex.co.copy()
    assert ui._geometry_changed not in bpy.app.handlers.depsgraph_update_post
    assert bones._dirty not in bpy.app.handlers.depsgraph_update_post
    with no_reads():
        for step in range(25):
            bm, vertex = edited_vertex(obj)
            vertex.co.z += .0008
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            C.view_layer.update()
            event(obj.data)
            event(rig)
            current = {frame['bank_key']: frame for frame in ui.display_frames(C)}
            assert all(current[name] is originals[name] for name in originals), step
            assert not ui._display_cache['errors'] and saved_metadata(obj) == saved
            if step % 5 == 0:
                bpy.ops.character_designer.finger_setup(action='TOGGLE')
                assert ui.display_frames(C) == ()
                event(obj.data.shape_keys)
                bpy.ops.character_designer.finger_setup(action='TOGGLE')
                assert len(ui.display_frames(C)) == 5
        bm, vertex = edited_vertex(obj)
        assert (vertex.co-initial).length > .019
        bpy.ops.character_designer.finger_setup(action='SIDE')
        assert {f['bank_key'] for f in ui.display_frames(C)} == {d+'.R' for d in bank.detect.DIGITS}
        bpy.ops.character_designer.finger_setup(action='SIDE')
        ui.redraw()
        assert not cache._entries
        current = {frame['bank_key']: frame for frame in ui.display_frames(C)}
        for name, original in originals.items():
            assert current[name]['path'] == original['path'] and not current[name]['bend_error']
        assert not ui._display_cache['errors'] and saved_metadata(obj) == saved
    edited = fingerprint(obj)
    assert edited != before
    guide = obj.character_designer_finger_bank.slots['INDEX.L'].guide
    try: definition.frame(bank.scoped(C, guide), require_basis=True)
    except ValueError: pass
    else: raise AssertionError('Explicit write validation accepted an edited captured vertex')
    assert fingerprint(obj) == edited and saved_metadata(obj) == saved


def test_saved_warning_does_not_hide_guides_and_only_changed_record_rebuilds():
    obj, _, _ = fixture()
    originals = {f['bank_key']: f for f in all_frames()}
    slot = obj.character_designer_finger_bank.slots['INDEX.L']
    slot.error = 'Test: counterpart is stale'
    obj.character_designer_finger_bank.needs_recheck = 'Retained explicit-action warning'
    saved = saved_metadata(obj)
    with no_reads():
        current = {f['bank_key']: f for f in ui.display_frames(C)}
        assert len(current) == 5 and not ui._display_cache['errors']
        assert all(current[name] is originals[name] for name in originals)
        assert saved_metadata(obj) == saved
        ui.redraw()
        assert len(ui.display_frames(C)) == 5 and not ui._display_cache['errors']
        assert saved_metadata(obj) == saved
        originals = {f['bank_key']: f for f in ui.display_frames(C)}
        slot.guide.revision += '-changed'
        current = {f['bank_key']: f for f in ui.display_frames(C)}
        assert current['INDEX.L'] is not originals['INDEX.L']
        assert current['MIDDLE.L'] is originals['MIDDLE.L']
        record = json.loads(slot.guide.record)
        record['diagnostic_metadata'] = 'changed without a revision bump'
        slot.guide.record = json.dumps(record)
        changed = {f['bank_key']: f for f in ui.display_frames(C)}
        assert changed['INDEX.L'] is not current['INDEX.L']
        assert changed['PINKY.L'] is current['PINKY.L']
    assert slot.error == 'Test: counterpart is stale'


def test_transform_shape_key_and_undo_redo_lifecycle_remain_scan_free():
    obj, _, _ = fixture()
    saved = saved_metadata(obj)
    with no_reads():
        select('INDEX'); original = ui.display_frames(C)[0]
        offset = Matrix.Translation((.1, .2, .3))
        obj.matrix_world = offset @ obj.matrix_world
        moved = ui.display_frames(C)[0]
        assert moved is not original
        assert (moved['root']-offset @ original['root']).length < 1e-6
        bpy.ops.object.mode_set(mode='OBJECT')
        obj.active_shape_key_index = 1
        obj.data.shape_keys.key_blocks['Artist'].value = .7
        obj.data.shape_keys.update_tag()
        C.view_layer.update()
        event(obj.data.shape_keys)
        current = ui.display_frames(C)[0]
        assert current['path'] == moved['path'] and current['key'] == 'Basis'
        # Exercise installed undo/redo lifecycle callbacks with pending work.
        # Native undo restores data; these hooks only cancel stale RNA and
        # rebuild the restored saved metadata, with no mesh reads.
        for before, after in ((bpy.app.handlers.undo_pre, bpy.app.handlers.undo_post),
                              (bpy.app.handlers.redo_pre, bpy.app.handlers.redo_post)):
            assert ui._invalidate in before and ui._resume in after
            ui._request = C.scene, None, definition.state(C), obj
            ui._display_request = C.scene, obj
            ui._invalidate()
            bones._reset()
            assert not cache._entries and not ui._visible
            assert ui._request is None and ui._display_request is None and bones._request is None
            ui._resume()
            rebuilt = ui.display_frames(C)
            assert len(rebuilt) == 1 and rebuilt[0]['path'] == moved['path']
            assert not ui._display_cache['errors']
        assert saved_metadata(obj) == saved
    assert obj.active_shape_key_index == 1
    assert abs(obj.data.shape_keys.key_blocks['Artist'].value-.7) < 1e-6


def test_pending_selection_clear_does_not_resurrect_saved_guide():
    obj, _, _ = fixture()
    all_frames()
    select('INDEX')
    ui._display_request = C.scene, obj
    ui._request = C.scene, None, definition.state(C), obj
    with no_reads():
        select('INDEX', 'CLICK')
        ui._refresh()
        ui._refresh_display()
        assert ui.display_frames(C) == ()
        assert ui._display_cache['frames'] == ()


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'): test(); print('PASS', name, flush=True)
    print('FINGER_PREVIEW_CACHE_PASS', flush=True)
