"""Joint/Start-End consumer ordering and pre-invalidation proof preservation."""
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import bmesh

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_preview_eye_blender import fixture, C, ui, work, layout, character_designer
from character_designer import finger_definition_ui as guides, finger_definition as definition
from character_designer import finger_preview_cache as cache, finger_preview_proof as proof, finger_bank as bank


def prepared(*, all_digits=False):
    obj, rig = fixture()
    if all_digits: obj.character_designer_finger_bank.visible_digits = set(bank.detect.DIGITS)
    ui.show(C); guides.show(invalidate=False)
    frames = guides.display_frames(C); guides.cached_frame(C)
    return obj, rig, work.settings(C)[2], frames


def test_repeated_real_notifications_do_not_overwrite_pending_snapshot():
    obj, rig, pair, before = prepared(all_digits=True)
    unchanged = layout.fingerprint(obj)
    with patch.object(definition, 'frame', side_effect=AssertionError('Warm reference recomputed')):
        for i in range(8):
            pair.joints[0].position = .4+i*.001
            request = ui._refresh_request
            saved = request['reference_cache']
            assert len(saved[1]) == 10
            C.view_layer.update()
            ui.refresh(C); ui.refresh(C)
            assert ui._refresh_request is request and request['reference_cache'] is saved
            ui._refresh()
            current = guides.display_frames(C)
            assert len(current) == 10 and all(a is b for a, b in zip(before, current))
            assert guides.cached_frame(C)[0] is not None and ui.joint_preview_visible(C)
    assert layout.fingerprint(obj) == unchanged


def test_guides_first_and_joint_first_share_one_proof():
    obj, _, pair, before = prepared()
    for order in ('joint', 'panel', 'display'):
        pair.joints[0].position += .001
        C.view_layer.update()
        with patch.object(proof, 'signature', wraps=proof.signature) as signatures, patch.object(
                definition, 'frame', side_effect=AssertionError('Reference recomputed before shared proof')):
            if order == 'joint': ui._refresh()
            elif order == 'panel':
                guides._request = C.scene, None, definition.state(C), obj
                guides._refresh()
            else:
                guides._display_request = C.scene, obj
                guides._refresh_display()
            ui._refresh()
            assert len(guides.display_frames(C)) == 2
            assert guides.cached_frame(C)[0] is before[0]
            assert signatures.call_count == 1, (order, signatures.call_count)
            assert ui._refresh_request is None


def test_notification_without_slider_snapshots_before_cache_clear():
    obj, _, _, before = prepared()
    obj.update_tag(refresh={'DATA'})
    C.view_layer.update()
    assert ui._refresh_request is not None
    assert len(ui._refresh_request['reference_cache'][1]) == 2
    with patch.object(definition, 'frame', side_effect=AssertionError('Unchanged reference recomputed')):
        ui._refresh()
        assert guides.display_frames(C)[0] is before[0]


def test_real_source_edit_cannot_reuse_snapshot():
    obj, _, pair, before = prepared()
    pair.joints[0].position += .001
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.active
    bm.verts[0][deform][obj.vertex_groups['ArtistWeight'].index] = .123
    bmesh.update_edit_mesh(obj.data); C.view_layer.update()
    with patch.object(cache, 'restore_verified', wraps=cache.restore_verified) as restored, patch.object(
            definition, 'frame', wraps=definition.frame) as checked:
        guides.cached_frame(C); guides.display_frames(C)
        assert restored.call_count == 0
        assert checked.call_count >= 2
        assert guides.cached_frame(C)[0] is not before[0]
    assert ui.joint_preview_enabled(C) and ui._preview.get('stale')
    try: work.apply(C)
    except ValueError: pass
    else: raise AssertionError('Read cache authorized overwriting a real edit')


def test_changed_guide_key_and_pending_selection_never_restore_wrong_record():
    obj, _, pair, before = prepared(all_digits=True)
    pair.joints[0].position += .001
    slot = obj.character_designer_finger_bank.slots['PINKY.R']
    slot.error = 'Counterpart changed'
    C.view_layer.update(); ui._refresh()
    frames = guides.display_frames(C)
    assert len(frames) == 9 and all(f['bank_key'] != 'PINKY.R' for f in frames)
    pair.joints[0].position += .001
    bank.select(C, 'MIDDLE')
    ui.refresh(C)
    assert ui._refresh_request['active'] == 'MIDDLE.L'
    ui._refresh()
    assert ui._preview['active'] != 'MIDDLE.L' or ui._preview.get('stale')  # Not prepared, never masquerade Index.
    assert not ui.joint_preview_visible(C)
    work.prepare(C); ui.show(C)
    assert ui._preview['active'] == 'MIDDLE.L'


def test_hide_and_undo_cancel_handoff_and_idle_does_not_loop():
    obj, _, pair, _ = prepared()
    pair.joints[0].position += .001
    ui.hide()
    C.view_layer.update(); guides.display_frames(C)
    assert ui._refresh_request is None and not ui.joint_preview_enabled(C)
    ui.show(C); guides.display_frames(C)
    pair.joints[0].position += .001
    ui._invalidate(); guides._invalidate()
    assert ui._refresh_request is None and not cache._entries
    ui._resume(); ui._refresh(); guides.show(invalidate=False)
    guides.display_frames(C); guides.cached_frame(C)
    with patch.object(ui, 'show', side_effect=AssertionError('Idle rebuilt joints')), patch.object(
            definition, 'frame', side_effect=AssertionError('Idle rechecked reference')):
        for _ in range(8):
            C.view_layer.update(); ui._refresh(); guides.display_frames(C); guides.cached_frame(C)
    assert ui.joint_preview_enabled(C)


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'): test(); print('PASS', name, flush=True)
    print('FINGER_COMBINED_PREVIEW_PASS', flush=True)
