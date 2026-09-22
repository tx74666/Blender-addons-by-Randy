"""Current-key preview is read-only and does not consume mesh Undo/Redo."""
import json
import sys
import traceback
from pathlib import Path
import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import single_finger_fixture as fixture, character_designer, fingerprint as snap
from character_designer import finger_definition as definition, finger_definition_ui as ui

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
    print('FINGER_DEFINITION_GUI', result, flush=True)
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
    _, area, _ = view()
    area.spaces.active.show_region_ui = True
    area.spaces.active.region_3d.view_distance = 5.4
    area.spaces.active.region_3d.view_location = Vector((.35, 0, 1.45))
    area.spaces.active.region_3d.view_rotation = Vector((6, 4, 2)).to_track_quat('Z', 'Y')
    area.spaces.active.overlay.show_floor = False
    area.spaces.active.overlay.show_axis_x = False
    area.spaces.active.overlay.show_axis_y = False
    STATE['before'] = snap(bpy.context.edit_object)
    bpy.ops.ed.undo_push(message='Definition baseline')
    item = bpy.context.window_manager.keyconfigs.active.keymaps['3D View'].keymap_items.new('character_designer.finger_definition', 'F8', 'PRESS')
    item.properties.action = 'CAPTURE'
    bpy.utils.register_class(TEST_OT_move)
    bpy.context.window_manager.keyconfigs.active.keymaps['3D View'].keymap_items.new('test.definition_move', 'F7', 'PRESS')
    bpy.context.window_manager.keyconfigs.update()
    bpy.app.timers.register(panel, first_interval=.5)


@guarded
def panel():
    _, area, _ = view()
    next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Character Designer'
    key('F7')
    bpy.app.timers.register(moved, first_interval=.5)


class TEST_OT_move(bpy.types.Operator):
    bl_idname = 'test.definition_move'
    bl_label = 'Test Outside Vertex Edit'
    bl_options = {'UNDO'}

    def execute(self, context):
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        bm.verts.ensure_lookup_table()
        bm.verts[-1].co.x += .125
        bmesh.update_edit_mesh(context.edit_object.data)
        return {'FINISHED'}


@guarded
def moved():
    STATE['edited'] = snap(bpy.context.edit_object)
    assert STATE['edited'] != STATE['before']
    key('F8')
    bpy.app.timers.register(captured, first_interval=.8)


@guarded
def captured():
    obj = bpy.context.edit_object
    assert obj.active_shape_key_index == 1 and snap(obj) == STATE['edited']
    assert definition.state(bpy.context).record
    assert ui._visible and ui.cached_frame(bpy.context)[0]
    STATE['record'] = definition.state(bpy.context).record
    bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_suffix('.png')))
    key('Z', ctrl=True)
    bpy.app.timers.register(undone, first_interval=.7)


@guarded
def undone():
    assert snap(bpy.context.edit_object) == STATE['before'], 'Capture consumed the mesh undo step'
    key('Z', ctrl=True, shift=True)
    bpy.app.timers.register(redone, first_interval=.7)


@guarded
def redone():
    assert snap(bpy.context.edit_object) == STATE['edited']
    assert not definition.frame(bpy.context)['basis']
    return finish()


if ARGS[0] == '--build':
    character_designer.register()
    obj = fixture(rooted=True)
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 1
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.wm.save_as_mainfile(filepath=ARGS[1], check_existing=False)
else:
    bpy.app.timers.register(setup, first_interval=1)
