"""Five pair indicators, one-side layout warning and real keyboard Undo/Redo."""
import json
import sys
import traceback
from pathlib import Path
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_bank_blender import fixture, select, capture_all, bank, definition, layout, character_designer
from character_designer import finger_definition_ui as ui, finger_layout_ui as rings_ui

ARGS = sys.argv[sys.argv.index('--')+1:]
STATE = {}


def view():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def finish(error=None):
    result = {'status': 'FAIL' if error else 'PASS', 'error': str(error) if error else None,
              'version': character_designer.bl_info['version']}
    Path(ARGS[1]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('FINGER_BANK_GUI', result, flush=True)
    if error: traceback.print_exc()
    bpy.ops.wm.quit_blender()


def guarded(fn):
    def run():
        try: return fn()
        except Exception as exc: return finish(exc)
    return run


def key(key_type, ctrl=False, shift=False):
    window, area, region = view()
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type=key_type, value=value, ctrl=ctrl, shift=shift,
                              x=region.x+region.width//2, y=region.y+region.height//2)


@guarded
def setup():
    character_designer.register()
    bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
    bpy.context.preferences.filepaths.temporary_directory = str(Path(ARGS[1]).parent)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.window_manager.character_designer.ui_page = 'RIG'
    bpy.context.window_manager.character_designer.rig_section = 'BODY'
    window, area, region = view()
    area.spaces.active.show_region_ui = True
    area.spaces.active.region_3d.view_distance = 3.3
    area.spaces.active.region_3d.view_location = Vector((3, .3, 0))
    area.spaces.active.region_3d.view_rotation = Vector((0, -1, 4)).to_track_quat('Z', 'Y')
    area.spaces.active.overlay.show_floor = False
    STATE['before'] = layout.fingerprint(bpy.context.edit_object)
    bpy.ops.ed.undo_push(message='Finger bank baseline')
    item = bpy.context.window_manager.keyconfigs.active.keymaps['3D View'].keymap_items.new('character_designer.finger_layout', 'F8', 'PRESS')
    item.properties.action = 'APPLY'
    item = bpy.context.window_manager.keyconfigs.active.keymaps['3D View'].keymap_items.new('character_designer.finger_setup', 'F7', 'PRESS')
    item.properties.action = 'CAPTURE'
    bpy.context.window_manager.keyconfigs.update()
    ui.show()
    with bpy.context.temp_override(window=window, area=area, region=region): rings_ui.show_preview(bpy.context)
    bpy.app.timers.register(panel, first_interval=.7)


@guarded
def panel():
    _, area, _ = view()
    next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Character Designer'
    bpy.app.timers.register(capture, first_interval=.8)


@guarded
def capture():
    assert ui.cached_frame(bpy.context)[0]
    b = bank.active_object(bpy.context).character_designer_finger_bank
    assert not json.loads(b.survey)['warnings']
    STATE['objects'] = set(bpy.data.objects.keys())
    STATE['input'] = json.loads(b.slots['INDEX.L'].guide.record)['input']['ids']
    STATE['middle'] = json.loads(b.slots['MIDDLE.L'].guide.record)['input']['ids']
    STATE['recaptures'] = 0
    select(bpy.context.edit_object, STATE['input'])
    key('F7')
    bpy.app.timers.register(recaptured, first_interval=.8)


@guarded
def recaptured():
    s = definition.state(bpy.context)
    assert s.confirmed and not s.pending and json.loads(s.record)['internal']
    assert ui._pending() is None and len(ui._handles) == 2
    assert set(bpy.data.objects.keys()) == STATE['objects']
    assert layout.fingerprint(bpy.context.edit_object) == STATE['before']
    STATE['recaptures'] += 1
    if STATE['recaptures'] < 3:
        key('F7')
        bpy.app.timers.register(recaptured, first_interval=.8)
        return
    bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_suffix('.png')))
    STATE['previous'] = s.record
    select(bpy.context.edit_object, STATE['input']+STATE['middle'])
    key('F7')
    bpy.app.timers.register(failed_capture, first_interval=.8)


@guarded
def failed_capture():
    b = bank.active_object(bpy.context).character_designer_finger_bank
    assert b.status.startswith('Update failed')
    assert definition.state(bpy.context).record == STATE['previous']
    assert len(ui._handles) == 2 and ui._pending() is None
    bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_name('failed-update.png')))
    bpy.ops.character_designer.finger_setup(action='TOGGLE')
    assert not ui._visible
    bpy.ops.character_designer.finger_setup(action='TOGGLE')
    assert ui._visible
    bpy.ops.character_designer.finger_setup(action='SELECT', digit='MIDDLE')
    assert definition.frame(bpy.context)['internal']
    bpy.ops.character_designer.finger_setup(action='SELECT', digit='INDEX')
    select(bpy.context.edit_object, STATE['input'])
    key('F7')
    bpy.app.timers.register(prepare, first_interval=.8)


@guarded
def prepare():
    assert not bank.active_object(bpy.context).character_designer_finger_bank.status
    layout.capture_definition(bpy.context)
    window, area, region = view()
    with bpy.context.temp_override(window=window, area=area, region=region): rings_ui.show_preview(bpy.context)
    key('F8')
    bpy.app.timers.register(applied, first_interval=1.5)


@guarded
def applied():
    obj = bank.active_object(bpy.context)
    STATE['after'] = layout.fingerprint(obj)
    assert STATE['after'] != STATE['before']
    assert set(json.loads(obj.character_designer_finger_bank.survey)['warnings']) == {'INDEX'}
    bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_name('warning.png')))
    key('Z', ctrl=True)
    bpy.app.timers.register(undone, first_interval=.8)


@guarded
def undone():
    assert layout.fingerprint(bpy.context.edit_object) == STATE['before']
    bank.recheck(bpy.context)
    assert not json.loads(bank.active_object(bpy.context).character_designer_finger_bank.survey)['warnings']
    key('Z', ctrl=True, shift=True)
    bpy.app.timers.register(redone, first_interval=.8)


@guarded
def redone():
    assert layout.fingerprint(bpy.context.edit_object) == STATE['after']
    bank.recheck(bpy.context)
    assert set(json.loads(bank.active_object(bpy.context).character_designer_finger_bank.survey)['warnings']) == {'INDEX'}
    bank.select(bpy.context, 'MIDDLE', 'L')
    assert definition.frame(bpy.context)['length'] > 1
    return finish()


if ARGS[0] == '--build':
    character_designer.register()
    obj, selected = fixture()
    capture_all(obj, selected)
    bank.select(bpy.context, 'INDEX', 'L')
    layout.capture_definition(bpy.context)
    bpy.ops.wm.save_as_mainfile(filepath=ARGS[1], check_existing=False)
else: bpy.app.timers.register(setup, first_interval=1)
