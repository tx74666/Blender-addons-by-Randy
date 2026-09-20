"""Warm clicks must not scan geometry; changed inputs must never reuse a proof."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import bound_fixture, character_designer, C, bank, definition, layout
from character_designer import finger_definition_ui as ui, finger_preview_cache as cache


def fixture():
    ui.hide(invalidate=True)
    obj, rig, selected = bound_fixture()
    # Flush pending construction updates before measuring read-only selections.
    C.view_layer.update()
    ui.redraw()
    return obj, rig, selected


def select(digit, mode='SINGLE'):
    return bpy.ops.character_designer.finger_setup(action='SELECT', digit=digit, selection_mode=mode)


def all_frames():
    select('THUMB'); select('PINKY', 'RANGE')
    frames = ui.display_frames(C)
    assert len(frames) == 10
    return frames


def event(data, *, geometry=True):
    ui._geometry_changed(C.scene, SimpleNamespace(updates=[SimpleNamespace(
        id=data, is_updated_geometry=geometry, is_updated_transform=not geometry)]))


def test_shared_panel_overlay_and_no_scans_for_warm_clicks():
    obj, _, _ = fixture()
    before = layout.fingerprint(obj)
    old_frame, old_dirty, old_snapshot = definition.frame, bank.dirty, definition._snapshot
    counts = {'frame': 0, 'dirty': 0}
    def frame(*args, **kwargs):
        counts['frame'] += 1
        return old_frame(*args, **kwargs)
    def dirty(*args, **kwargs):
        counts['dirty'] += 1
        return old_dirty(*args, **kwargs)
    definition.frame, bank.dirty = frame, dirty
    try:
        select('INDEX')
        first = ui.cached_frame(C)[0]
        frames = ui.display_frames(C)
        assert first is frames[0] and len(frames) == 2
        assert counts == {'frame': 2, 'dirty': 1}, counts
        all_frames()
        assert counts == {'frame': 10, 'dirty': 1}, counts
        def forbidden(*args, **kwargs): raise AssertionError('Warm selection revalidated geometry')
        definition.frame = bank.dirty = definition._snapshot = forbidden
        for _ in range(5):
            for digit in bank.detect.DIGITS:
                select(digit)
                assert len(ui.display_frames(C)) == 2 and ui.cached_frame(C)[0]
                C.view_layer.update()
                assert len(ui.display_frames(C)) == 2
                select(digit, 'CLICK')
                assert ui.display_frames(C) == ()
                select(digit, 'CLICK')
                assert len(ui.display_frames(C)) == 2
        all_frames()
        bpy.ops.character_designer.finger_setup(action='TOGGLE')
        assert ui._display_cache is None and cache._entries
        bpy.ops.character_designer.finger_setup(action='TOGGLE')
        assert len(ui.display_frames(C)) == 10
    finally:
        definition.frame, bank.dirty, definition._snapshot = old_frame, old_dirty, old_snapshot
    assert layout.fingerprint(obj) == before


def test_hidden_geometry_change_and_unrelated_object():
    obj, rig, _ = fixture()
    all_frames()
    entries = dict(cache._entries)
    event(rig)
    assert cache._entries == entries  # This mesh preview does not depend on bones.
    ui.hide()
    bm = bmesh.from_edit_mesh(obj.data)
    record = json.loads(obj.character_designer_finger_bank.slots['INDEX.L'].guide.record)
    bm.verts.ensure_lookup_table()
    bm.verts[record['basis']['coordinates'][0][0]].co.z += .02
    bmesh.update_edit_mesh(obj.data)
    event(obj.data)
    assert not cache._entries and ui._display_cache is None
    select('INDEX')
    frames = ui.display_frames(C)
    assert {f['bank_key'] for f in frames} == {'INDEX.R'}
    assert ui.cached_frame(C)[0] is None and ui.cached_frame(C)[1]
    assert obj.character_designer_finger_bank.needs_recheck
    # Failed validation is cached, never resurrected as the old valid preview.
    old = definition._snapshot
    def forbidden(*args, **kwargs): raise AssertionError('Unchanged failure was recomputed')
    definition._snapshot = forbidden
    try:
        select('INDEX')
        assert len(ui.display_frames(C)) == 1 and ui.cached_frame(C)[0] is None
    finally: definition._snapshot = old


def test_record_warning_and_revision_revalidate_only_changed_slot():
    obj, _, _ = fixture()
    frames = all_frames()
    originals = {f['bank_key']: f for f in frames}
    slot = obj.character_designer_finger_bank.slots['INDEX.L']
    slot.error = 'Test: counterpart is stale'
    assert len(ui.display_frames(C)) == 9
    assert 'INDEX.L' in ui._display_cache['errors']
    assert ui.display_frames(C)[-1] is originals['PINKY.R']
    slot.error = ''
    slot.guide.revision += '-changed'
    current = {f['bank_key']: f for f in ui.display_frames(C)}
    assert len(current) == 10
    assert current['INDEX.L'] is not originals['INDEX.L']
    assert current['INDEX.R'] is originals['INDEX.R']
    assert current['PINKY.R'] is originals['PINKY.R']
    record = json.loads(slot.guide.record)
    record['diagnostic_metadata'] = 'changed without a revision bump'
    slot.guide.record = json.dumps(record)
    changed = {f['bank_key']: f for f in ui.display_frames(C)}
    assert changed['INDEX.L'] is not current['INDEX.L']
    assert changed['PINKY.R'] is current['PINKY.R']


def test_transform_shape_key_domain_and_undo_invalidation():
    obj, _, _ = fixture()
    select('INDEX'); original = ui.display_frames(C)[0]
    offset = Matrix.Translation((.1, .2, .3))
    obj.matrix_world = offset @ obj.matrix_world
    moved = ui.display_frames(C)[0]
    assert moved is not original
    assert (moved['root'] - offset @ original['root']).length < 1e-6
    # Mode/key changes are cheap key checks even before the depsgraph callback.
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 1
    current = ui.display_frames(C)[0]
    assert current is not moved and current['internal']
    ui._invalidate()
    assert not cache._entries and not ui._visible and ui._display_request is None
    ui.show(invalidate=False)
    assert len(ui.display_frames(C)) == 2
    # A Shape Key data notification invalidates both active and hidden slots.
    event(obj.data.shape_keys)
    assert not cache._entries


def test_pending_selection_clear_and_direct_write_validation():
    obj, _, _ = fixture()
    all_frames()
    select('INDEX')
    # A delayed refresh must respect the new empty display selection.
    ui._display_request = C.scene, obj
    ui._request = C.scene, None, definition.state(C), obj
    select('INDEX', 'CLICK')
    old = definition.frame
    def forbidden(*args, **kwargs): raise AssertionError('Deselected panel request was evaluated')
    definition.frame = forbidden
    try: ui._refresh()
    finally: definition.frame = old
    ui._refresh_display()
    assert ui._display_cache['frames'] == ()
    # Persistent view frames must not become a write-validation shortcut.
    record = json.loads(obj.character_designer_finger_bank.slots['INDEX.L'].guide.record)
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    bm.verts[record['basis']['coordinates'][0][0]].co.z += .02
    try: definition.frame(C, require_basis=True)
    except ValueError: pass
    else: raise AssertionError('Direct consumer accepted geometry from a cached display')


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'): test(); print('PASS', name, flush=True)
    print('FINGER_PREVIEW_CACHE_PASS', flush=True)
