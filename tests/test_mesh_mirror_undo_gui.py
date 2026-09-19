"""Disposable Blender GUI test: actual key-event Undo/Redo and overlay screenshot.

Build: blender -b --factory-startup --python THIS -- --build <temp fixture.blend>
Run: blender --factory-startup --enable-event-simulate <fixture> --python THIS -- --run <report.json>
No user preferences or existing scene files are saved.
"""
import json
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
import character_designer
from character_designer import mesh_mirror as mirror
from character_designer import mesh_mirror_ui as ui
from test_mesh_mirror_blender import make, tetra, select

ARGS = sys.argv[sys.argv.index('--')+1:]
STATE = {}


def context_3d():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return window, area, region


def snapshot():
    obj = bpy.data.objects['UnboundHair']
    bpy.ops.object.mode_set(mode='OBJECT')
    try:
        return mirror._fingerprint(obj)
    finally:
        bpy.ops.object.mode_set(mode='EDIT')


def emit(key, ctrl=False, shift=False):
    window, area, region = context_3d()
    for value in ('PRESS', 'RELEASE'):
        window.event_simulate(type=key, value=value, ctrl=ctrl, shift=shift,
                              x=region.x + region.width//2, y=region.y + region.height//2)


def finish(error=None):
    result = {'status': 'FAIL' if error else 'PASS', 'error': str(error) if error else None,
              'screenshot': str(Path(ARGS[1]).with_suffix('.png'))}
    Path(ARGS[1]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('MESH_MIRROR_GUI', result, flush=True)
    if error:
        traceback.print_exc()
    bpy.ops.wm.quit_blender()
    return None


def guarded(function):
    def run():
        try:
            return function()
        except Exception as error:
            return finish(error)
    return run


@guarded
def setup():
    bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
    bpy.context.preferences.filepaths.temporary_directory = str(Path(ARGS[1]).parent)
    bpy.context.preferences.edit.use_global_undo = True
    from character_designer import weight_symmetry
    weight_symmetry.CHARACTERDESIGNER_PT_weight_symmetry.bl_options = set()
    character_designer.register()
    bpy.context.window_manager.character_designer.ui_page = 'WEIGHT'
    window, area, region = context_3d()
    area.spaces.active.show_region_ui = True
    area.spaces.active.region_3d.view_distance = 12
    area.spaces.active.region_3d.view_location = Vector((0, 0, 0))
    area.spaces.active.region_3d.view_rotation = Vector((6, -10, 6)).to_track_quat('Z', 'Y')
    STATE['before'] = snapshot()
    bpy.ops.ed.undo_push(message='Mirror fixture baseline')
    keymap = bpy.context.window_manager.keyconfigs.active.keymaps['3D View']
    keymap.keymap_items.new('character_designer.mirror_selected_region', 'F8', 'PRESS')
    bpy.context.window_manager.keyconfigs.update()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.character_designer.mesh_mirror_preview()
    bpy.app.timers.register(choose_panel, first_interval=.8)


@guarded
def choose_panel():
    window, area, region = context_3d()
    next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Character Designer'
    area.tag_redraw()
    bpy.app.timers.register(capture_and_mirror, first_interval=.5)


@guarded
def capture_and_mirror():
    assert ui._valid_preview() is not None
    bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_suffix('.png')))
    emit('F8')
    bpy.app.timers.register(check_mirror, first_interval=.8)


@guarded
def check_mirror():
    obj = bpy.data.objects['UnboundHair']
    obj.update_from_editmode()
    assert len(obj.data.vertices) == 8 and len(obj.data.polygons) == 8
    assert 'hair.R' in obj.vertex_groups
    STATE['after'] = snapshot()
    assert STATE['before'] != STATE['after']
    emit('Z', ctrl=True)
    bpy.app.timers.register(check_undo, first_interval=.8)


@guarded
def check_undo():
    assert snapshot() == STATE['before'], 'One Ctrl+Z did not restore exact geometry/UV/shape keys/groups'
    emit('Z', ctrl=True, shift=True)
    bpy.app.timers.register(check_redo, first_interval=.8)


@guarded
def check_redo():
    assert snapshot() == STATE['after'], 'One Ctrl+Shift+Z did not restore exact mirrored state'
    return finish()


if ARGS[0] == '--build':
    obj = make(*tetra())
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Artist')
    key.data[3].co.z += .2
    group = obj.vertex_groups.new(name='hair.L')
    group.add((0, 1, 2, 3), .8, 'REPLACE')
    obj.data.uv_layers.new(name='ArtistUV')
    select(obj, range(4))
    bpy.ops.wm.save_as_mainfile(filepath=ARGS[1], check_existing=False)
elif ARGS[0] == '--run':
    bpy.app.timers.register(setup, first_interval=1)
else:
    raise ValueError(ARGS)
