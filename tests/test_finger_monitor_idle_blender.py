"""Failed joint monitoring must settle without rescanning an idle Edit mesh."""
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import bmesh

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_preview_eye_blender import fixture, expect, C, ui, work, layout, bank, character_designer
from character_designer import finger_preview_proof as proof


def drain():
    """Deliver actual dependency updates; never manufacture another refresh."""
    quiet = 0
    rebuilds = 0
    for _ in range(12):
        C.view_layer.update()
        if ui._refresh_request is not None:
            quiet = 0
            rebuilds += 1
            ui._refresh()
        else:
            quiet += 1
            if quiet == 3:
                return rebuilds
    raise AssertionError(f'Idle monitoring did not settle after {rebuilds} queued rebuilds')


def assert_idle(signatures, fingerprints):
    before = signatures.call_count, fingerprints.call_count
    for _ in range(8):
        C.view_layer.update()
        assert ui._refresh_request is None, 'Idle dependency update queued joint monitoring'
        ui._refresh()  # An already registered, empty timer must also be inert.
    assert (signatures.call_count, fingerprints.call_count) == before, 'Idle mesh was scanned'


def unavailable_case(kind):
    obj, _ = fixture()
    state = obj.character_designer_finger_workflow
    assert state.source and state.pairs['INDEX'].recipe
    if kind == 'missing_pair':
        bank.select(C, 'MIDDLE')
        assert state.pairs.get('MIDDLE') is None
    elif kind == 'missing_source':
        state.source = None
    elif kind == 'blank_recipe':
        state.pairs['INDEX'].recipe = ''
    else:
        raise AssertionError(kind)
    assert ui._preview is None
    state.preview_enabled = True
    # Queue once, as opening the eye or receiving an external update would.
    ui.refresh(C)
    with patch.object(proof, 'signature', wraps=proof.signature) as signatures, patch.object(
            layout, 'fingerprint', wraps=layout.fingerprint) as fingerprints:
        assert drain() >= 1
        # The unprepared panel has no ring-eye button yet; monitoring intent
        # still persists independently of whether a drawable preview exists.
        assert not ui.joint_preview_visible(C) and ui.joint_preview_enabled(C)
        assert 'prepare' in state.status.lower(), state.status
        assert signatures.call_count == fingerprints.call_count == 0, (
            kind, signatures.call_count, fingerprints.call_count)
        assert_idle(signatures, fingerprints)
        # A new external notification remains cheap and does not start a loop.
        obj.update_tag(refresh={'DATA'})
        assert drain() >= 1
        assert not ui.joint_preview_visible(C) and ui.joint_preview_enabled(C)
        assert signatures.call_count == fingerprints.call_count == 0
        assert_idle(signatures, fingerprints)
    ui.hide()


def test_unprepared_active_finger_skips_mesh_proof_and_settles():
    unavailable_case('missing_pair')


def test_missing_source_skips_mesh_proof_and_settles():
    unavailable_case('missing_source')


def test_blank_recipe_skips_mesh_proof_and_settles():
    unavailable_case('blank_recipe')


def test_stale_prepared_source_settles_and_actual_restoration_revalidates():
    obj, _ = fixture()
    ui.show(C)
    drain()
    state = obj.character_designer_finger_workflow
    before = ui._preview
    labels = before['labels']
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    original = bm.verts[0].co.copy()
    with patch.object(proof, 'signature', wraps=proof.signature) as signatures, patch.object(
            layout, 'fingerprint', wraps=layout.fingerprint) as fingerprints:
        bm.verts[0].co.z += .02
        bmesh.update_edit_mesh(obj.data)
        assert drain() >= 1
        assert signatures.call_count >= 1 and fingerprints.call_count >= 1
        expect(True)
        assert ui._preview is before and ui._preview['labels'] == labels
        assert ui._preview.get('stale') and state.status.startswith('Monitoring:')
        assert 'changed' in state.status.lower(), state.status
        assert_idle(signatures, fingerprints)
        # The retained comparison overlay must never authorize overwriting edits.
        edited = bm.verts[0].co.copy()
        try:
            work.apply(C)
        except ValueError:
            pass
        else:
            raise AssertionError('Stale preview authorized replacing the edited mesh')
        assert bm.verts[0].co == edited
        drain()
        assert_idle(signatures, fingerprints)
        # Restore the real mesh and rely on its notification, without refresh().
        scans = signatures.call_count
        bm.verts[0].co = original
        bmesh.update_edit_mesh(obj.data)
        assert drain() >= 1
        assert signatures.call_count > scans, 'Source restoration was never revalidated'
        expect(True)
        assert not ui._preview.get('stale')
        assert not state.status.startswith('Monitoring:')
        assert_idle(signatures, fingerprints)
    ui.hide()


def test_hide_cancels_pending_monitor_and_stays_closed_after_updates():
    obj, _ = fixture()
    ui.show(C)
    drain()
    work.settings(C)[2].joints[0].position += .001
    assert ui._refresh_request is not None
    ui.hide()
    assert ui._refresh_request is None
    assert not bpy.app.timers.is_registered(ui._refresh)
    with patch.object(proof, 'signature', wraps=proof.signature) as signatures, patch.object(
            layout, 'fingerprint', wraps=layout.fingerprint) as fingerprints:
        obj.update_tag(refresh={'DATA'})
        assert drain() == 0
        expect(False)
        assert signatures.call_count == fingerprints.call_count == 0
        assert_idle(signatures, fingerprints)


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FINGER_MONITOR_IDLE_PASS', flush=True)
