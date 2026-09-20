"""Display selection must never turn into a bulk modeling selection."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import bound_fixture, work, ui as workflow_ui, targets, bank, definition, layout, character_designer, C
from character_designer import finger_bank_ui as ui, finger_definition_ui as preview


def click(digit, shift=False, ctrl=False):
    # Exercise the registered operator's real invoke dispatch with event flags.
    op = SimpleNamespace(action='SELECT', digit=digit, selection_mode='SINGLE',
                         report=lambda *args: None)
    op.execute = lambda context: ui.CHARACTERDESIGNER_OT_finger_setup.execute(op, context)
    return ui.CHARACTERDESIGNER_OT_finger_setup.invoke(op, C, SimpleNamespace(shift=shift, ctrl=ctrl))


def fixture():
    preview.hide(); workflow_ui.hide()
    return bound_fixture()


def test_shift_toggle_range_and_active():
    obj, rig, _ = fixture()
    b = obj.character_designer_finger_bank
    before, records = layout.fingerprint(obj), {s.name: s.guide.record for s in b.slots}
    assert click('THUMB') == {'FINISHED'}
    for d in ('INDEX','MIDDLE','RING','PINKY'): click(d, shift=True)
    assert bank.selected_digits(b) == bank.detect.DIGITS and b.active == 'PINKY.L'
    click('MIDDLE', shift=True)
    assert bank.selected_digits(b) == ('THUMB','INDEX','RING','PINKY')
    click('PINKY', shift=True)
    assert b.active == 'RING.L'
    click('INDEX')
    assert bank.selected_digits(b) == ('THUMB','RING')
    click('INDEX')
    assert bank.selected_digits(b) == ('INDEX',)
    click('PINKY', shift=True, ctrl=True)
    assert bank.selected_digits(b) == ('INDEX','MIDDLE','RING','PINKY') and b.selection_anchor == 'INDEX'
    click('THUMB', shift=True, ctrl=True)
    assert bank.selected_digits(b) == bank.detect.DIGITS
    click('PINKY'); click('THUMB', shift=True, ctrl=True)
    assert bank.selected_digits(b) == bank.detect.DIGITS
    for digit in bank.detect.DIGITS: click(digit)
    assert bank.selected_digits(b) == ()
    assert preview.display_frames(C) == ()
    click('PINKY', shift=True)
    assert bank.selected_digits(b) == ('PINKY',)
    b.selection_initialized = False; b.selection_anchor = ''
    click('THUMB', ctrl=True, shift=True); click('PINKY', ctrl=True, shift=True)
    assert bank.selected_digits(b) == bank.detect.DIGITS
    assert layout.fingerprint(obj) == before
    assert records == {s.name: s.guide.record for s in b.slots}


def test_plain_click_can_hide_last_pair_and_recapture_refocuses():
    obj, rig, chosen = fixture(); b = obj.character_designer_finger_bank
    before = layout.fingerprint(obj)
    records = {s.name: s.guide.record for s in b.slots}
    assert bank.selected_digits(b) == ('INDEX',)
    assert len(preview.display_frames(C)) == 2
    click('INDEX')
    assert bank.selected_digits(b) == () and preview._drawable_frames() == ()
    # Hidden is not deleted: preserve the independent editing identity/data.
    assert b.active == 'INDEX.L'
    assert records == {s.name: s.guide.record for s in b.slots}
    assert layout.fingerprint(obj) == before
    bpy.ops.character_designer.finger_setup(action='TOGGLE')
    bpy.ops.character_designer.finger_setup(action='TOGGLE')
    assert preview._drawable_frames() == ()
    click('INDEX')
    assert len(preview.display_frames(C)) == 2
    # Explicit programmatic focus and Capture must not toggle a visible pair off.
    bank.select(C, 'INDEX'); bank.select(C, 'INDEX')
    assert bank.selected_digits(b) == ('INDEX',)
    from test_finger_bank_blender import select
    select(obj, chosen['INDEX.L'])
    assert bpy.ops.character_designer.finger_setup(action='CAPTURE') == {'FINISHED'}
    assert bank.selected_digits(b) == ('INDEX',)
    assert len(preview.display_frames(C)) == 2
    assert layout.fingerprint(obj) == before


def test_multiple_axes_bend_missing_slot_and_cache():
    obj, rig, _ = fixture(); b = obj.character_designer_finger_bank
    click('THUMB'); click('PINKY', shift=True, ctrl=True)
    frames = preview.display_frames(C)
    assert {d['bank_key'] for d in frames} == {s.name for s in b.slots} and len(frames) == 10
    workflow_ui.show_bend(C)
    assert len(workflow_ui._preview['labels']) == 10
    old = definition._snapshot
    def forbidden(*args, **kwargs): raise AssertionError('Display cache scanned the mesh')
    definition._snapshot = forbidden
    try:
        for _ in range(1000): assert preview.display_frames(C) is frames
    finally: definition._snapshot = old
    b.slots['MIDDLE.R'].error = 'test stale topology'
    assert len(preview.display_frames(C)) == 9
    assert 'MIDDLE.R' in preview._display_cache['errors']
    b.slots['INDEX.L'].guide.record = ''
    assert len(preview.display_frames(C)) == 8
    preview.hide()
    assert preview._display_cache is None and preview._display_request is None and preview._drawable_frames() == ()


def test_edit_scope_and_save_reopen():
    obj, rig, _ = fixture(); b = obj.character_designer_finger_bank
    click('THUMB'); click('PINKY', shift=True, ctrl=True)
    pair = work.prepare(C)
    assert pair.name == 'PINKY' and len(obj.character_designer_finger_workflow.pairs) == 1
    work.apply(C)
    assert set(json.loads(obj.character_designer_finger_workflow.results)) == {'PINKY.L','PINKY.R'}
    assert bank.selected_digits(b) == bank.detect.DIGITS
    with tempfile.TemporaryDirectory(prefix='cd-multiselect-') as folder:
        path = str(Path(folder)/'selection.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path); bpy.ops.wm.open_mainfile(filepath=path)
        b = bank.active_object(C).character_designer_finger_bank
        assert bank.selected_digits(b) == bank.detect.DIGITS and b.active == 'PINKY.L'
        assert not preview._visible and preview._display_cache is None


def test_button_highlights_are_separate_from_readiness():
    obj, rig, _ = fixture(); b = obj.character_designer_finger_bank
    click('THUMB'); click('PINKY', ctrl=True, shift=True)
    calls = []
    class FakeLayout:
        def row(self, **kwargs): return self
        def column(self, **kwargs): return self
        def label(self, **kwargs): pass
        def operator(self, name, **kwargs):
            calls.append(kwargs); return SimpleNamespace()
    ui.draw_header(FakeLayout(), C)
    assert sum(c.get('depress', False) for c in calls) == 5
    b.slots['RING.L'].guide.record = ''
    calls.clear(); ui.draw_header(FakeLayout(), C)
    assert sum(c.get('depress', False) for c in calls) == 5
    assert next(c for c in calls if c.get('text') == 'R')['icon'] != 'CHECKMARK'


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'): test(); print('PASS', name, flush=True)
    print('FINGER_MULTISELECT_PASS', flush=True)
