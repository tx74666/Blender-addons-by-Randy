"""Disposable bilateral preview and real keyboard Undo/Redo acceptance."""
import json
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_flex_blender import character_designer, fixture, select_chain, snapshot, flex
from character_designer import finger_definition as definition, finger_definition_ui as definition_ui

ARGS = sys.argv[sys.argv.index('--')+1:]
STATE = {}


def finish(error=None):
    result = {'status': 'FAIL' if error else 'PASS', 'error': str(error) if error else None,
              'version': character_designer.bl_info['version']}
    Path(ARGS[1]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('FINGER_SYMMETRY_GUI', result, flush=True)
    if error: traceback.print_exc()
    bpy.ops.wm.quit_blender()


def guarded(function):
    def run():
        try: return function()
        except Exception as exc: return finish(exc)
    return run


def view():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


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
    area.spaces.active.region_3d.view_distance = 6.5
    area.spaces.active.region_3d.view_location = Vector((.5, 1.2, 0))
    area.spaces.active.region_3d.view_rotation = Vector((1, -3, 7)).to_track_quat('Z', 'Y')
    area.spaces.active.overlay.show_floor = False
    area.spaces.active.overlay.show_axis_x = False
    area.spaces.active.overlay.show_axis_y = False
    STATE['before'] = snapshot(bpy.data.objects['Rig'])
    bpy.ops.ed.undo_push(message='Bilateral fixture baseline')
    item = bpy.context.window_manager.keyconfigs.active.keymaps['3D View'].keymap_items.new('character_designer.finger_flex', 'F8', 'PRESS')
    item.properties.action = 'APPLY'
    bpy.context.window_manager.keyconfigs.update()
    flex.show_preview()
    definition_ui.show()
    bpy.app.timers.register(panel, first_interval=.6)


@guarded
def panel():
    _, area, _ = view()
    next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Character Designer'
    area.tag_redraw()
    bpy.app.timers.register(capture, first_interval=.6)


@guarded
def capture():
    assert len(flex.plan(bpy.context)[1]) == 6
    assert snapshot(bpy.data.objects['Rig']) == STATE['before']
    bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_suffix('.png')))
    key('F8')
    bpy.app.timers.register(applied, first_interval=.7)


@guarded
def applied():
    rig = bpy.data.objects['Rig']
    STATE['after'] = snapshot(rig)
    for name, value in STATE['before'].items():
        assert STATE['after'][name][:2] == value[:2]
        if name.startswith('f_index'): assert STATE['after'][name][2] != value[2]
        else: assert STATE['after'][name] == value
    assert rig.data.use_mirror_x
    key('Z', ctrl=True)
    bpy.app.timers.register(undone, first_interval=.7)


@guarded
def undone():
    assert snapshot(bpy.data.objects['Rig']) == STATE['before'], 'Undo failed to restore both sides'
    key('Z', ctrl=True, shift=True)
    bpy.app.timers.register(redone, first_interval=.7)


@guarded
def redone():
    rig = bpy.data.objects['Rig']
    assert snapshot(rig) == STATE['after'], 'Redo failed to restore both sides'
    assert rig.data.use_mirror_x
    return finish()


if ARGS[0] == '--build':
    character_designer.register()
    obj, rig = fixture()
    obj.rotation_euler.y = .5
    bpy.context.view_layer.update()
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    definition.capture(bpy.context)
    definition.confirm(bpy.context)
    select_chain(rig)
    rig.data.use_mirror_x = True
    bpy.ops.wm.save_as_mainfile(filepath=ARGS[1], check_existing=False)
elif ARGS[0] == '--run':
    bpy.app.timers.register(setup, first_interval=1)
