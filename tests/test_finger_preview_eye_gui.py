"""Isolated native mouse clicks on the production joint-preview eye widget."""
import json
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import character_designer, C, bank, work, ui, layout
from character_designer import finger_definition_ui as guides

OUTPUT = Path(sys.argv[sys.argv.index('--')+1])
STATE = {}


class TEST_PT_preview_eye(bpy.types.Panel):
    bl_label = 'Joint Topology & Weights'
    bl_idname = 'TEST_PT_preview_eye'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Eye Test'
    def draw(self, context):
        row = self.layout.row(align=True)
        _, _, pair = work.settings(context)
        row.prop(pair, 'auto_weight')
        ui.draw_joint_visibility(row, context)


def later(fn, delay=.8):
    def wrapped():
        try: fn()
        except Exception as exc: traceback.print_exc(); finish(str(exc))
    bpy.app.timers.register(wrapped, first_interval=delay)


def view():
    window = C.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area


def finish(error=None):
    print('FINGER_EYE_GUI', 'FAIL' if error is not None else 'PASS', error, flush=True)
    OUTPUT.write_text(json.dumps({'status': 'FAIL' if error is not None else 'PASS', 'error': error,
                                  'version': character_designer.bl_info['version']}, indent=2), encoding='utf-8')
    bpy.ops.wm.quit_blender()


def screenshot(name):
    bpy.ops.screen.screenshot(filepath=str(OUTPUT.with_name(name+'.png')))


def click():
    win, _ = view()
    x, y = 1100, win.height-123
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    def press():
        for value in ('PRESS', 'RELEASE'): win.event_simulate(type='LEFTMOUSE', value=value, x=x, y=y)
    bpy.app.timers.register(press, first_interval=.15)


def setup():
    character_designer.register(); bpy.utils.register_class(TEST_PT_preview_eye)
    C.preferences.filepaths.use_auto_save_temporary_files = False
    C.preferences.filepaths.temporary_directory = str(OUTPUT.parent)
    _, area = view()
    area.spaces.active.show_region_ui = True
    area.spaces.active.region_3d.view_distance = 2.4
    area.spaces.active.region_3d.view_location = Vector((2.9, .4, 0))
    area.spaces.active.region_3d.view_rotation = Vector((0, -1, 4)).to_track_quat('Z', 'Y')
    STATE['mesh'] = layout.fingerprint(C.edit_object)
    STATE['objects'] = set(bpy.data.objects.keys())
    assert bpy.ops.character_designer.finger_workflow(action='PREPARE') == {'FINISHED'}
    guides.show(invalidate=False)
    later(prepared)


def prepared():
    _, area = view()
    try: next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Eye Test'
    except AttributeError:
        STATE['ready_attempts'] = STATE.get('ready_attempts', 0)+1
        if STATE['ready_attempts'] > 10: raise
        area.tag_redraw(); later(prepared, .2); return
    assert ui.joint_preview_visible(C), 'Prepare did not keep its preview visible'
    later(opened)


def opened():
    assert ui.joint_preview_visible(C) and guides._visible
    screenshot('eye-open')
    click(); later(closed)


def closed():
    assert not ui.joint_preview_visible(C) and ui._preview is None
    assert guides._visible, 'Joint eye hid Basic Setup'
    screenshot('eye-closed')
    # Preparing again must reopen a manually closed eye without an eye click.
    assert bpy.ops.character_designer.finger_workflow(action='PREPARE') == {'FINISHED'}
    later(reopened)


def reopened():
    assert ui.joint_preview_visible(C) and guides._visible
    _, _, pair = work.settings(C)
    pair.joints[0].root_weight = .79
    later(refreshed)


def refreshed():
    assert ui.joint_preview_visible(C), ('Parameter refresh hid the preview',
        C.edit_object.character_designer_finger_workflow.status)
    assert bpy.ops.character_designer.finger_workflow(action='BEND') == {'FINISHED'}
    later(bend)


def bend():
    assert ui._preview['kind'] == 'RINGS' and ui.joint_preview_visible(C)
    assert ui._bend_preview is not None and ui.joint_preview_enabled(C)
    screenshot('eye-open-with-bend')
    # Toggling the other visual layer must not close the ring eye.
    assert bpy.ops.character_designer.finger_workflow(action='BEND') == {'FINISHED'}
    later(from_bend)


def from_bend():
    assert ui.joint_preview_visible(C) and ui._preview['kind'] == 'RINGS'
    assert guides._visible
    assert layout.fingerprint(C.edit_object) == STATE['mesh']
    assert set(bpy.data.objects.keys()) == STATE['objects']
    finish()


later(setup, 1.)
