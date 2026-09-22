"""Capture/Recheck operate on one side; saved pair diagnostics stay compact."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import (
    hands_fixture, bound_fixture, select, capture_all, character_designer, C,
    bank, definition, fingerprint, rig_state,
)
from character_designer import (
    finger_definition_ui as guides, finger_preview_cache as cache, finger_bone_tools,
    finger_bank_ui as bank_ui,
)

DIGIT = 'MIDDLE'
KEYS = tuple(DIGIT+'.'+side for side in ('L', 'R'))


def fixture():
    guides.hide(invalidate=True)
    C.scene.character_designer_finger_definition.overlays_enabled = True
    obj, chosen = hands_fixture()
    capture_all(obj, chosen)
    C.view_layer.update()
    guides.redraw()
    return obj, chosen, obj.character_designer_finger_bank


def guide_values(guide):
    return {name: getattr(guide, name) for name in bank.FIELDS}


def other_references(state):
    return {slot.name: (guide_values(slot.guide), slot.bones, slot.error)
            for slot in state.slots if not slot.name.startswith(DIGIT+'.')}


def shift_middle_row(obj, keys, amount=.004):
    """A genuine local shape edit, deliberately not an equivalent loop cut."""
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    report = json.loads(obj.character_designer_finger_bank.survey)
    for key in keys:
        row = report['candidates'][key]['rings'][3]
        for index in row: bm.verts[index].co.z += amount
    bm.normal_update()
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    C.view_layer.update()
    guides.redraw()


def public_capture(obj, faces):
    select(obj, faces)
    assert bpy.ops.character_designer.finger_setup(action='CAPTURE') == {'FINISHED'}
    assert C.mode == 'EDIT_MESH' and C.edit_object == obj


def assert_pair_healthy(obj, state):
    report = json.loads(state.survey)
    assert DIGIT not in report['warnings'], report['warnings']
    for key in KEYS:
        slot = state.slots[key]
        assert not slot.error, (key, slot.error)
        assert slot.guide.confirmed and not slot.guide.pending
        frame = definition.frame(bank.scoped(C, slot.guide), require_basis=True,
                                 require_bend=True, require_confirmed=True)
        assert frame['length'] > .5


def warm_pair_and_check_master_cache():
    C.view_layer.update()
    guides.show(invalidate=True)
    frames = {}
    for side in ('L', 'R'):
        bank.select(C, DIGIT, side)
        displayed = guides.display_frames(C)
        assert len(displayed) == 1
        frame, error = guides.cached_frame(C)
        assert frame is displayed[0] and not error
        frames[side] = frame
    with patch.object(definition, 'frame', side_effect=AssertionError('Warm recapture frame was revalidated')), \
         patch.object(definition, '_snapshot', side_effect=AssertionError('Warm recapture copied a mesh')), \
         patch.object(bank, 'dirty', side_effect=AssertionError('Warm recapture rescanned topology')):
        for _ in range(5):
            for side in ('L', 'R'):
                bank.select(C, DIGIT, side)
                assert guides.display_frames(C)[0] is frames[side]
                assert bpy.ops.character_designer.finger_definition(action='OVERLAYS') == {'FINISHED'}
                assert guides.display_frames(C) == ()
                assert bpy.ops.character_designer.finger_definition(action='OVERLAYS') == {'FINISHED'}
                assert guides.display_frames(C)[0] is frames[side]


def test_first_capture_and_recheck_never_create_an_opposite_reference():
    guides.hide(invalidate=True)
    obj, chosen = hands_fixture()
    state = obj.character_designer_finger_bank
    before = fingerprint(obj)
    with patch.object(bank, '_mirror', side_effect=AssertionError('Implicit opposite capture')):
        public_capture(obj, chosen['MIDDLE.L'])
        assert state.active == 'MIDDLE.L' and bank.display_side(state) == 'L'
        assert state.slots['MIDDLE.L'].guide.confirmed
        assert {slot.name for slot in state.slots if slot.guide.record} == {'MIDDLE.L'}
        source_revision = state.slots['MIDDLE.L'].guide.revision
        others = {slot.name: (guide_values(slot.guide), slot.bones, slot.error)
                  for slot in state.slots if slot.name != 'MIDDLE.L'}
        definition.confirm(C)
        bank.sync(C)
        checked = bank.recheck(C)
        assert checked['unchanged'] == ['MIDDLE.L'] and not checked['failed'], checked
        assert state.slots['MIDDLE.L'].guide.revision == source_revision
        assert {slot.name: (guide_values(slot.guide), slot.bones, slot.error)
                for slot in state.slots if slot.name != 'MIDDLE.L'} == others
        bank.select(C, DIGIT, 'R')
        empty = bank.recheck(C)
        assert not empty['unchanged'] and not empty['failed'] and not empty['adapted'], empty
        assert not state.slots['MIDDLE.R'].guide.record and not state.slots['MIDDLE.R'].error
    assert fingerprint(obj) == before


def test_symmetric_edit_rechecks_and_recaptures_only_the_active_side():
    obj, chosen, state = fixture()
    public_capture(obj, chosen['MIDDLE.L'][1:5])
    old = {key: json.loads(state.slots[key].guide.record) for key in KEYS}
    revisions = {key: state.slots[key].guide.revision for key in KEYS}
    shift_middle_row(obj, KEYS)
    mate = state.slots['MIDDLE.R']
    mate_before = guide_values(mate.guide), mate.bones, mate.error
    result = bank.recheck(C)
    assert set(result['failed']) == {'MIDDLE.L'}, result
    assert DIGIT not in result['survey']['warnings'], 'Both sides received the same mirrored shape edit'
    assert state.slots['MIDDLE.L'].error
    assert (guide_values(mate.guide), mate.bones, mate.error) == mate_before

    # Explicit validation failures never prevent frozen saved display.
    guides.show(invalidate=True)
    for side in ('L', 'R'):
        bank.select(C, DIGIT, side)
        frame, error = guides.cached_frame(C)
        assert frame and not error
        assert not cache.lookup(state.slots[DIGIT+'.'+side].guide,
                                state.slots[DIGIT+'.'+side].error)[1]
    others, artist_data = other_references(state), fingerprint(obj)
    public_capture(obj, chosen['MIDDLE.L'])
    assert (guide_values(mate.guide), mate.bones, mate.error) == mate_before
    assert state.slots['MIDDLE.L'].guide.confirmed and not state.slots['MIDDLE.L'].error
    new = {key: json.loads(state.slots[key].guide.record) for key in KEYS}
    assert new['MIDDLE.L']['surface']['origin']['input']['ids'] == chosen['MIDDLE.L']
    assert state.slots['MIDDLE.L'].guide.revision != revisions['MIDDLE.L']
    assert new['MIDDLE.L']['surface']['path'] != old['MIDDLE.L']['surface']['path']
    assert len(new['MIDDLE.L']['surface']['path']) > len(old['MIDDLE.L']['surface']['path'])
    assert state.slots['MIDDLE.R'].guide.revision == revisions['MIDDLE.R']
    assert other_references(state) == others
    assert fingerprint(obj) == artist_data
    repeated = bank.recheck(C)
    assert not repeated['failed'] and repeated['unchanged'] == ['MIDDLE.L'], repeated
    assert (guide_values(mate.guide), mate.bones, mate.error) == mate_before
    public_capture(obj, chosen['MIDDLE.R'])
    assert_pair_healthy(obj, state)
    warm_pair_and_check_master_cache()
    assert fingerprint(obj) == artist_data


def test_asymmetric_source_capture_preserves_mate_until_explicit_mate_capture():
    obj, chosen, state = fixture()
    shift_middle_row(obj, ('MIDDLE.L',))
    result = bank.recheck(C)
    assert set(result['failed']) == {'MIDDLE.L'}, result
    assert DIGIT in result['survey']['warnings']
    mate = state.slots['MIDDLE.R']
    old_mate_record, old_mate_revision = mate.guide.record, mate.guide.revision
    mate.bones = '{"sentinel":"keep independent binding"}'
    mate.error = 'An earlier explicit right-side error'
    saved_mate = guide_values(mate.guide), mate.bones, mate.error
    artist_data, others = fingerprint(obj), other_references(state)
    with patch.object(bank, '_mirror', side_effect=AssertionError('Capture generated an opposite reference')):
        public_capture(obj, chosen['MIDDLE.L'])
    source = state.slots['MIDDLE.L']
    assert not source.error and source.guide.confirmed
    assert definition.frame(bank.scoped(C, source.guide), require_basis=True,
                            require_bend=True, require_confirmed=True)['length'] > .5
    assert DIGIT in json.loads(state.survey)['warnings']
    assert (guide_values(mate.guide), mate.bones, mate.error) == saved_mate
    assert mate.guide.record == old_mate_record and mate.guide.revision == old_mate_revision
    assert other_references(state) == others
    assert fingerprint(obj) == artist_data, 'Capture must never silently mirror artist geometry'

    # Manual mesh repair clears the next census warning, but cannot authorize
    # Capture L to replace a retained R definition or its bone binding.
    shift_middle_row(obj, ('MIDDLE.R',))
    repaired_data = fingerprint(obj)
    public_capture(obj, chosen['MIDDLE.L'])
    assert DIGIT not in json.loads(state.survey)['warnings']
    assert (guide_values(mate.guide), mate.bones, mate.error) == saved_mate
    public_capture(obj, chosen['MIDDLE.R'])
    assert_pair_healthy(obj, state)
    assert mate.guide.record != old_mate_record and mate.guide.revision != old_mate_revision
    warm_pair_and_check_master_cache()
    assert fingerprint(obj) == repaired_data


def test_independent_opposite_bend_is_never_compared_or_replaced_by_capture():
    obj, chosen, state = fixture()
    # Opposite longitudinal faces are four corners away on this octagonal
    # fixture. Capture an intentional independent bend definition on R.
    bottom_right = [index+4 for index in chosen['MIDDLE.R']]
    public_capture(obj, bottom_right)
    mate = state.slots['MIDDLE.R']
    independent = json.loads(mate.guide.record)
    assert independent['capture_source'] == 'MIDDLE.R'
    assert mate.guide.confirmed
    # Re-select L before recapture to exercise the flip update callback on the
    # active source; it must not pre-emptively rewrite the independently saved R.
    bank.select(C, DIGIT, 'L')
    mate.bones = '{"sentinel":"independent bone binding"}'
    saved, saved_bones, saved_error = guide_values(mate.guide), mate.bones, mate.error
    artist_data, others = fingerprint(obj), other_references(state)
    public_capture(obj, chosen['MIDDLE.L'])
    source = state.slots['MIDDLE.L']
    assert not source.error and source.guide.confirmed
    assert mate.error == saved_error
    assert DIGIT not in json.loads(state.survey)['warnings'], 'This is a saved bend conflict, not asymmetric geometry'
    assert guide_values(mate.guide) == saved, 'A failed mate replacement changed retained reference metadata'
    assert mate.bones == saved_bones
    assert other_references(state) == others
    assert fingerprint(obj) == artist_data


def test_reference_write_is_nested_and_restores_callbacks_after_exception():
    obj, _, state = fixture()
    bank.select(C, DIGIT, 'L')
    source, mate = (state.slots[key].guide for key in KEYS)
    saved_mate, artist_data = guide_values(mate), fingerprint(obj)
    assert source.confirmed and mate.confirmed
    with patch.object(guides, 'redraw') as redraw, \
         patch.object(finger_bone_tools, 'invalidate') as invalidate:
        with guides.reference_write():
            with guides.reference_write():
                source.flip_bend = True
                assert source.confirmed and guide_values(mate) == saved_mate
            source.flip_bend = False
            assert source.confirmed and guide_values(mate) == saved_mate
        redraw.assert_not_called()
        invalidate.assert_not_called()

        try:
            with guides.reference_write():
                with guides.reference_write():
                    source.flip_bend = True
                    raise RuntimeError('Simulated reference transaction failure')
        except RuntimeError as exc:
            assert str(exc) == 'Simulated reference transaction failure'
        else:
            raise AssertionError('The reference guard swallowed a transaction failure')
        assert source.confirmed and guide_values(mate) == saved_mate
        redraw.assert_not_called()
        invalidate.assert_not_called()

        # Both guard levels must unwind on failure; a later user edit must
        # invalidate this side's confirmation, retaining the opposite settings.
        source.flip_bend = False
        assert not source.confirmed and guide_values(mate) == saved_mate
        redraw.assert_called()
        invalidate.assert_called()
    assert fingerprint(obj) == artist_data


def test_user_reverse_bend_only_changes_the_active_side():
    obj, _, state = fixture()
    artist_data = fingerprint(obj)
    for side in ('L', 'R'):
        bank.select(C, DIGIT, side)
        source = state.slots[DIGIT+'.'+side].guide
        mate = state.slots[DIGIT+'.'+('R' if side == 'L' else 'L')].guide
        source.confirmed = mate.confirmed = True
        saved_mate = guide_values(mate)
        flipped = not source.flip_bend
        with patch.object(guides, 'redraw') as redraw, \
             patch.object(finger_bone_tools, 'invalidate') as invalidate:
            source.flip_bend = flipped
            assert source.flip_bend == flipped and not source.confirmed
            assert guide_values(mate) == saved_mate
            redraw.assert_called()
            invalidate.assert_called()
    assert fingerprint(obj) == artist_data


def test_pair_mismatch_is_one_info_badge_without_popup_or_opposite_errors():
    obj, chosen, state = fixture()
    shift_middle_row(obj, ('MIDDLE.L',))
    public_capture(obj, chosen['MIDDLE.L'])
    warning = json.loads(state.survey)['warnings'][DIGIT]
    assert warning == bank.PAIR_SYNC_WARNING
    assert 'rebind' not in warning.casefold()
    assert not state.slots['MIDDLE.R'].error

    class Layout:
        def __init__(self, labels=None, calls=None):
            self.labels = [] if labels is None else labels
            self.calls = [] if calls is None else calls
            self.alert = False
        def row(self, **_): return Layout(self.labels, self.calls)
        def column(self, **_): return Layout(self.labels, self.calls)
        def label(self, *, text, **kwargs):
            self.labels.append(text)
            self.calls.append(('label', text, self.alert, kwargs))
        def operator(self, *_args, **kwargs):
            self.calls.append(('operator', kwargs.get('text'), self.alert, kwargs))
            return SimpleNamespace()

    def panel_text():
        panel = Layout()
        with patch.object(definition, '_snapshot', side_effect=AssertionError('Panel scanned the mesh')), \
             patch.object(bank, 'dirty', side_effect=AssertionError('Panel checked geometry')), \
             patch.object(bank.detect, 'census', side_effect=AssertionError('Panel repeated detection')):
            bank_ui.draw_header(panel, C)
        return panel.labels, ' '.join(panel.labels), panel.calls

    labels, text, calls = panel_text()
    assert labels.count('L/R differ') == 1 and warning not in text, text
    assert 'L reference:' not in text and 'R reference:' not in text, text
    assert 'rebind this finger only' not in text.casefold()
    assert not any(alert for _, _, alert, _ in calls), calls
    assert any(kind == 'label' and label == 'L/R differ' and details.get('icon') == 'INFO'
               for kind, label, _, details in calls), calls
    assert not any(details.get('icon') == 'ERROR' for _, _, _, details in calls), calls
    assert any(kind == 'operator' and label == 'M' and details.get('icon') == 'CHECKMARK'
               for kind, label, _, details in calls), calls
    reports = []
    operator = SimpleNamespace(action='CAPTURE', report=lambda levels, text: reports.append((levels, text)))
    assert bank_ui.CHARACTERDESIGNER_OT_finger_setup.execute(operator, C) == {'FINISHED'}
    assert reports == [], reports

    # A loaded .53 survey shows the short warning immediately, even before an
    # explicit Capture/Recheck upgrades its diagnostic schema.
    current_survey = state.survey
    old = json.loads(current_survey)
    legacy = 'Left/right finger surfaces differ. Match the mesh shape on both sides before paired bone actions.'
    old['pair_warning_version'] = 1
    old['warnings'][DIGIT] = legacy
    state.survey, state.slots['MIDDLE.R'].error = json.dumps(old), legacy
    _, text, calls = panel_text()
    assert text.count('L/R differ') == 1 and legacy not in text and warning not in text, text
    assert not any(alert for _, _, alert, _ in calls), calls
    state.survey, state.slots['MIDDLE.R'].error = current_survey, warning

    # An opposite-side reference problem does not color the working side red
    # and appears only when the user explicitly switches to that side.
    state.slots['MIDDLE.R'].error = 'Saved bend direction needs review.'
    labels, text, calls = panel_text()
    assert text.count('L/R differ') == 1, text
    assert 'R reference:' not in text, text
    assert 'L reference:' not in text, text
    assert not any(alert for _, _, alert, _ in calls), calls
    bank.select(C, DIGIT, 'R')
    labels, text, calls = panel_text()
    assert text.count('R reference: Saved bend direction needs review.') == 1, text
    assert any(kind == 'operator' and text == 'M' and alert
               for kind, text, alert, _ in calls), calls
    assert any(kind == 'label' and text.startswith('R reference:') and alert
               for kind, text, alert, _ in calls), calls


def test_recapture_upgrades_saved_diagnostic_without_touching_mate_or_bones():
    guides.hide(invalidate=True)
    obj, rig, chosen = bound_fixture()
    state = obj.character_designer_finger_bank
    bank.select(C, DIGIT, 'L')
    shift_middle_row(obj, ('MIDDLE.L',))
    bank.recheck(C)
    previous = json.loads(state.survey)
    legacy_warning = 'Left/right finger surfaces differ. Match the mesh shape on both sides before paired bone actions.'
    previous['pair_warning_version'] = 1
    previous['warnings'][DIGIT] = legacy_warning
    state.survey = json.dumps(previous)
    mate = state.slots['MIDDLE.R']
    mate.error = legacy_warning
    saved_mate, saved_bones = guide_values(mate.guide), mate.bones
    artist_data, bone_data = fingerprint(obj), rig_state(rig)

    with patch.object(bank.detect, 'census', side_effect=AssertionError('Warning migration repeated detection')), \
         patch.object(bank, '_warnings', wraps=bank._warnings) as warnings:
        public_capture(obj, chosen['MIDDLE.L'])
        assert warnings.call_count == 1
    upgraded = json.loads(state.survey)
    warning = upgraded['warnings'][DIGIT]
    assert upgraded['pair_warning_version'] == bank.PAIR_WARNING_VERSION
    assert upgraded['stamp'] == previous['stamp']
    assert upgraded['candidates'] == previous['candidates']
    assert warning == bank.PAIR_SYNC_WARNING and 'rebind' not in warning.casefold()
    assert mate.error == legacy_warning
    assert guide_values(mate.guide) == saved_mate and mate.bones == saved_bones
    assert state.slots['MIDDLE.L'].guide.confirmed and not state.slots['MIDDLE.L'].error

    assert rig_state(rig) == bone_data
    assert fingerprint(obj) == artist_data
    assert C.mode == 'EDIT_MESH' and C.edit_object == obj


def test_source_capture_failure_retains_every_reference_and_reports_only_source():
    obj, chosen, state = fixture()
    before = {slot.name: (guide_values(slot.guide), slot.bones, slot.error) for slot in state.slots}
    survey, artist_data = state.survey, fingerprint(obj)
    original = bank._internal_record
    def fail_after_validation(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError('Injected source capture failure')
    reports = []
    operator = SimpleNamespace(action='CAPTURE', report=lambda levels, text: reports.append((levels, text)))
    select(obj, chosen['MIDDLE.L'])
    with patch.object(bank, '_internal_record', side_effect=fail_after_validation):
        assert bank_ui.CHARACTERDESIGNER_OT_finger_setup.execute(operator, C) == {'CANCELLED'}
    assert len(reports) == 1 and reports[0][0] == {'WARNING'}
    assert 'Injected source capture failure' in reports[0][1]
    assert {slot.name: (guide_values(slot.guide), slot.bones, slot.error) for slot in state.slots} == before
    assert state.survey == survey and fingerprint(obj) == artist_data


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FINGER_RECAPTURE_PASS', flush=True)
