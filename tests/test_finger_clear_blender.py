"""Clearing Basic Setup affects one side, retaining mate data and diagnostics."""
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import (
    hands_fixture, capture_all, select, fingerprint, character_designer, C, bank, definition,
)
from test_finger_topology_adapt_blender import body, edit_mesh, update, split_ring
from character_designer import finger_bank_ui as ui, finger_definition_ui as guides
from character_designer import finger_bone_tools as bones
from character_designer import finger_loop_marks as marks


class Layout:
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls
        self.alert = False

    def row(self, **_): return Layout(self.calls)
    def column(self, **_): return Layout(self.calls)
    def box(self): return Layout(self.calls)
    def label(self, **kwargs): self.calls.append(('label', kwargs))
    def operator(self, name, **kwargs):
        op = SimpleNamespace()
        self.calls.append((name, dict(kwargs, op=op, alert=self.alert)))
        return op


def fixture():
    bones.hide()
    guides.hide(invalidate=True)
    obj, chosen = hands_fixture()
    capture_all(obj, chosen)
    bank.select(C, 'MIDDLE', 'L')
    C.scene.character_designer_finger_definition.overlays_enabled = True
    return obj, chosen


def metadata(slot):
    return tuple(getattr(slot.guide, field) for field in bank.FIELDS)+(slot.error, slot.bones)


def assert_empty(obj, digit='MIDDLE', side='L'):
    slot = obj.character_designer_finger_bank.slots[f'{digit}.{side}']
    assert not slot.error and not slot.bones, slot.name
    guide = slot.guide
    assert not any(getattr(guide, field) for field in
                   ('source', 'record', 'revision', 'bend_source', 'bend_record',
                    'pending_source', 'pending', 'confirmed', 'status')), slot.name
    assert guide.use_basis and not guide.flip_bend


def seed_marks(obj):
    stored = {key: {'1': {'coordinates': [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                          'sentinel': key}}
              for key in ('MIDDLE.L', 'MIDDLE.R', 'RING.L', 'RING.R')}
    obj[marks.PROPERTY] = json.dumps(stored)
    return stored


def indicators():
    layout = Layout()
    ui.draw_header(layout, C)
    return {item['op'].digit: item for name, item in layout.calls
            if name == 'character_designer.finger_setup' and item['op'].action == 'SELECT'}


def test_real_x_clears_only_current_side_and_preserves_mate_marks_and_status():
    obj, _ = fixture()
    state = obj.character_designer_finger_bank
    state.visible_digits = {'MIDDLE', 'RING'}
    guides.show()
    assert len(guides.display_frames(C)) == 2
    for side in ('L', 'R'):
        slot = state.slots[f'MIDDLE.{side}']
        slot.error = 'Old Middle reference error'
        slot.bones = '{"old": "binding"}'
        slot.guide.status = 'Old Middle status'
    state.slots['INDEX.L'].error = 'Keep the Index reference error'
    state.slots['INDEX.L'].bones = '{"keep": "index binding"}'
    report = json.loads(state.survey)
    report['warnings']['MIDDLE'] = 'Old Middle survey warning'
    state.survey = json.dumps(report)
    state.status, state.bone_status = 'Old capture failure', 'Old bone failure'
    saved_marks = seed_marks(obj)
    others = {slot.name: metadata(slot) for slot in state.slots if slot.name != 'MIDDLE.L'}
    survey = state.survey
    layout = Layout()
    guides.draw_controls(layout, C)
    name, button = next((name, item) for name, item in layout.calls
                        if item.get('icon') == 'X' and item.get('op').action == 'CLEAR')
    assert name == 'character_designer.finger_setup', name
    before = fingerprint(obj)
    # The clear action itself must not validate, census or edit the mesh.
    with patch.object(definition, '_snapshot', side_effect=AssertionError('Clear scanned the mesh')):
        assert getattr(bpy.ops.character_designer, name.rsplit('.', 1)[1])(action='CLEAR') == {'FINISHED'}
    assert_empty(obj)
    assert not state.status and not state.bone_status
    assert {slot.name: metadata(slot) for slot in state.slots if slot.name != 'MIDDLE.L'} == others
    assert state.survey == survey
    assert json.loads(obj[marks.PROPERTY]) == {key: value for key, value in saved_marks.items() if key != 'MIDDLE.L'}
    assert guides._display_cache is None and guides._cache is None and not bones._saved_previews
    assert guides._visible, 'Clearing Middle must not hide Ring'
    frames = guides.display_frames(C)
    assert [frame['bank_key'] for frame in frames] == ['RING.L']
    buttons = indicators()
    assert buttons['MIDDLE']['icon'] == 'RADIOBUT_OFF' and not buttons['MIDDLE']['alert']
    assert buttons['INDEX']['icon'] == 'ERROR' and buttons['INDEX']['alert']
    assert fingerprint(obj) == before


def test_legacy_and_scoped_clear_preserve_the_correct_opposite_side():
    obj, _ = fixture()
    state = obj.character_designer_finger_bank
    state.visible_digits = {'MIDDLE', 'RING'}
    guides.show()
    for side in ('L', 'R'):
        state.slots[f'MIDDLE.{side}'].bones = 'old binding'
        state.slots[f'MIDDLE.{side}'].error = 'old error'
    mate = metadata(state.slots['MIDDLE.R'])
    saved_marks = seed_marks(obj)
    before = fingerprint(obj)
    assert bpy.ops.character_designer.finger_definition(action='CLEAR') == {'FINISHED'}
    assert_empty(obj)
    assert metadata(state.slots['MIDDLE.R']) == mate
    assert guides._visible
    # A scoped right-side clear must not accidentally clear the visible left
    # side or its active status while another finger is being edited.
    ring = state.slots['RING.R']
    index = metadata(state.slots['INDEX.L'])
    left_ring = metadata(state.slots['RING.L'])
    state.status, state.bone_status = 'Keep active-side status', 'Keep active bone status'
    definition.clear(bank.scoped(C, ring.guide))
    assert_empty(obj, 'RING', 'R')
    assert metadata(state.slots['RING.L']) == left_ring
    assert metadata(state.slots['MIDDLE.R']) == mate
    assert state.status == 'Keep active-side status' and state.bone_status == 'Keep active bone status'
    assert json.loads(obj[marks.PROPERTY]) == {key: value for key, value in saved_marks.items()
                                            if key not in {'MIDDLE.L', 'RING.R'}}
    assert metadata(state.slots['INDEX.L']) == index
    assert state.active == 'MIDDLE.L' and fingerprint(obj) == before


def test_clear_then_recheck_and_geometry_change_never_recreate_empty_slot_errors():
    obj, _ = fixture()
    state = obj.character_designer_finger_bank
    # Both missing-tip errors are real; only Middle is being forgotten.
    bm = edit_mesh(obj)
    for key in ('MIDDLE.L', 'INDEX.L'):
        tip = set(body(obj, key)['rings'][-1])
        cap = next(f for f in bm.faces if {v.index for v in f.verts} == tip)
        bm.faces.remove(cap)
    update(obj, bm)
    result = bank.adapt_topology(C)
    assert {'MIDDLE.L', 'INDEX.L'} <= set(result['failed']), result
    before = fingerprint(obj)
    mate = metadata(state.slots['MIDDLE.R'])
    bpy.ops.character_designer.finger_setup(action='CLEAR')
    for _ in range(2):
        result = bank.recheck(C)
        assert not any(key.startswith('MIDDLE.') for key in result['failed']), result
        assert not result['failed'], result
        assert state.slots['INDEX.L'].error
        assert_empty(obj)
        assert metadata(state.slots['MIDDLE.R']) == mate
        buttons = indicators()
        assert buttons['MIDDLE']['icon'] == 'RADIOBUT_OFF'
        assert buttons['INDEX']['icon'] == 'ERROR'
    assert fingerprint(obj) == before
    # A later artist edit causes a fresh census, not a resurrection of cleared
    # Middle. Ring can still adapt normally and keep its captured setup.
    split_ring(obj, 'RING.L')
    edited = fingerprint(obj)
    result = bank.adapt_topology(C)
    assert 'RING.L' in result['adapted'] and 'INDEX.L' in result['failed'], result
    assert_empty(obj)
    assert indicators()['MIDDLE']['icon'] == 'RADIOBUT_OFF'
    assert fingerprint(obj) == edited


def test_cleared_side_recaptures_independently_and_retains_pair_census_warning():
    obj, chosen = fixture()
    state = obj.character_designer_finger_bank
    bm = edit_mesh(obj)
    # Asymmetry remains explicit census metadata; it cannot prevent source-only
    # capture or replace the opposite saved guide after clearing this side.
    vertex = bm.verts[body(obj, 'MIDDLE.L')['rings'][3][0]]
    vertex.co.z += .015
    update(obj, bm)
    bank.recheck(C)
    assert 'MIDDLE' in json.loads(state.survey)['warnings']
    mate = metadata(state.slots['MIDDLE.R'])
    survey = state.survey
    bpy.ops.character_designer.finger_setup(action='CLEAR')
    assert state.survey == survey and metadata(state.slots['MIDDLE.R']) == mate
    assert indicators()['MIDDLE']['icon'] == 'RADIOBUT_OFF'
    before = fingerprint(obj)
    select(obj, chosen['MIDDLE.L'])
    bank.capture(C)
    assert state.slots['MIDDLE.L'].guide.record
    assert metadata(state.slots['MIDDLE.R']) == mate
    assert 'MIDDLE' in json.loads(state.survey)['warnings']
    assert indicators()['MIDDLE']['icon'] == 'CHECKMARK' and not indicators()['MIDDLE']['alert']
    assert fingerprint(obj) == before


def test_x_ignores_opposite_only_data_but_can_clear_current_stale_binding():
    obj, _ = fixture()
    state = obj.character_designer_finger_bank
    definition._clear_state(state.slots['MIDDLE.L'].guide)
    assert state.slots['MIDDLE.R'].guide.record
    layout = Layout()
    guides.draw_controls(layout, C)
    assert not any(item.get('icon') == 'X' and item.get('op').action == 'CLEAR'
                   for _, item in layout.calls)
    mate = metadata(state.slots['MIDDLE.R'])
    bpy.ops.character_designer.finger_setup(action='CLEAR')
    assert metadata(state.slots['MIDDLE.R']) == mate
    state.slots['MIDDLE.L'].bones = 'obsolete binding'
    layout = Layout()
    guides.draw_controls(layout, C)
    assert any(item.get('icon') == 'X' and item.get('op').action == 'CLEAR'
               for _, item in layout.calls)
    bpy.ops.character_designer.finger_setup(action='CLEAR')
    assert_empty(obj)
    assert metadata(state.slots['MIDDLE.R']) == mate


if __name__ == '__main__':
    try:
        character_designer.register()
        tests = [fn for name, fn in list(globals().items()) if name.startswith('test_')]
        for test in tests:
            test()
            print('PASS', test.__name__, flush=True)
        print('FINGER_CLEAR_TESTS_PASSED', len(tests), flush=True)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
