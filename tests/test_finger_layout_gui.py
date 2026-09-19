"""Disposable GUI preview + actual key-event Undo/Redo, never the user's scene."""
import json
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
from test_finger_layout_blender import fixture, character_designer, layout, snap
from character_designer import finger_layout_ui as ui
from character_designer import finger_definition as definition, finger_definition_ui as definition_ui

ARGS = sys.argv[sys.argv.index('--')+1:]
STATE = {}


def context_3d():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def finish(error=None):
    result = {'status': 'FAIL' if error else 'PASS', 'error': str(error) if error else None}
    Path(ARGS[1]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('FINGER_LAYOUT_GUI', result, flush=True)
    if error: traceback.print_exc()
    bpy.ops.wm.quit_blender()


def guarded(function):
    def run():
        try: return function()
        except Exception as exc: return finish(exc)
    return run


def emit(key, ctrl=False, shift=False):
    window, area, region = context_3d()
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type=key, value=value, ctrl=ctrl, shift=shift,
                              x=region.x+region.width//2, y=region.y+region.height//2)


@guarded
def setup():
    character_designer.register()
    bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
    bpy.context.preferences.filepaths.temporary_directory = str(Path(ARGS[1]).parent)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.window_manager.character_designer.ui_page = 'RIG'
    bpy.context.window_manager.character_designer.rig_section = 'BODY'
    window, area, region = context_3d()
    area.spaces.active.show_region_ui = True
    area.spaces.active.region_3d.view_distance = 5.5
    area.spaces.active.region_3d.view_location = Vector((.4, 0, 1.5))
    area.spaces.active.region_3d.view_rotation = Vector((5, -9, 3)).to_track_quat('Z', 'Y')
    area.spaces.active.overlay.show_floor = False
    area.spaces.active.overlay.show_axis_x = False
    area.spaces.active.overlay.show_axis_y = False
    STATE['before'] = snap(bpy.context.edit_object)
    bpy.ops.ed.undo_push(message='Finger fixture baseline')
    km = bpy.context.window_manager.keyconfigs.active.keymaps['3D View']
    item = km.keymap_items.new('character_designer.finger_layout', 'F8', 'PRESS')
    item.properties.action = 'APPLY'
    bpy.context.window_manager.keyconfigs.update()
    with bpy.context.temp_override(window=window, area=area, region=region):
        ui.show_preview(bpy.context)
        definition_ui.show()
    bpy.app.timers.register(panel, first_interval=.7)


@guarded
def panel():
    window, area, region = context_3d()
    next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Character Designer'
    area.tag_redraw()
    bpy.app.timers.register(capture, first_interval=.7)


@guarded
def capture():
    assert ui._preview and not ui._preview['error']
    bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_suffix('.png')))
    emit('F8')
    bpy.app.timers.register(applied, first_interval=.8)


@guarded
def applied():
    obj = bpy.data.objects['FingerLayoutFixture']
    STATE['after'] = snap(obj)
    assert STATE['after'] != STATE['before']
    assert layout.state(bpy.context).applied
    assert definition.frame(bpy.context)['basis']
    emit('Z', ctrl=True)
    bpy.app.timers.register(undone, first_interval=.8)


@guarded
def undone():
    assert snap(bpy.data.objects['FingerLayoutFixture']) == STATE['before'], 'Undo did not restore original mesh/data'
    assert not layout.state(bpy.context).applied
    emit('Z', ctrl=True, shift=True)
    bpy.app.timers.register(redone, first_interval=.8)


@guarded
def redone():
    assert snap(bpy.data.objects['FingerLayoutFixture']) == STATE['after'], 'Redo did not restore mesh/data'
    assert layout.state(bpy.context).applied
    # Updating after Undo/Redo must still start from the saved source.
    obj = bpy.data.objects['FingerLayoutFixture']
    layout.apply_layout(bpy.context)
    assert snap(obj) == STATE['after']
    return finish()


if ARGS[0] == '--build':
    character_designer.register()
    obj = fixture(rooted=True)
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    definition.capture(bpy.context)
    definition.confirm(bpy.context)
    layout.capture_definition(bpy.context)
    state = layout.state(bpy.context)
    state.joint_one, state.joint_two = .31, .69
    state.between_rings = 2
    bpy.ops.wm.save_as_mainfile(filepath=ARGS[1], check_existing=False)
elif ARGS[0] == '--run':
    bpy.app.timers.register(setup, first_interval=1)
