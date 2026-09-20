"""Native slider drags: moving ring batches, no blank drawn frames, no writes.

Run in an isolated --enable-event-simulate Blender window with the saved bound
fixture from test_finger_multiselect_gui.py --build. Never uses a live user file.
"""
import json
import sys
import time
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import character_designer, C, work, ui, layout
from character_designer import finger_definition_ui as guides, finger_definition as definition

OUTPUT = Path(sys.argv[sys.argv.index('--')+1])
STATE = {'frames': 0, 'blank_frames': 0, 'positions': [], 'refresh_ms': []}


class TEST_PT_joint_drag(bpy.types.Panel):
    bl_label = 'Joint Topology & Weights'
    bl_idname = 'TEST_PT_joint_drag'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Drag Test'

    def draw(self, context):
        _, _, pair = work.settings(context)
        for i, joint in enumerate(pair.joints):
            self.layout.prop(joint, 'position', text=f'Joint {i+1}', slider=True)
        row = self.layout.row(align=True)
        row.prop(pair, 'auto_weight')
        ui.draw_joint_visibility(row, context)
        # Exercise the simultaneously visible production Basic Setup consumer,
        # not only a minimal slider panel which misses cascading invalidations.
        guides.draw_controls(self.layout, context)
        if context.object.character_designer_finger_workflow.status:
            self.layout.label(text='Adjust marker / spacing (preview remains visible)')


def later(fn, delay=.15):
    def guarded():
        try: fn()
        except Exception as exc:
            traceback.print_exc(); finish(str(exc))
    bpy.app.timers.register(guarded, first_interval=delay)


def view():
    window = C.window_manager.windows[0]
    return window, next(a for a in window.screen.areas if a.type == 'VIEW_3D')


def observe():
    if not STATE.get('watch'): return
    STATE['frames'] += 1
    if not ui.joint_preview_visible(C): STATE['blank_frames'] += 1
    else:
        groups = ui._preview['groups']
        STATE['positions'].append([list(groups[0][0][0]), list(groups[-1][0][0])])


def finish(error=None):
    STATE['watch'] = False
    if STATE.get('handle'): bpy.types.SpaceView3D.draw_handler_remove(STATE.pop('handle'), 'WINDOW')
    STATE.update(status='FAIL' if error is not None else 'PASS', error=error)
    for key in ('mesh', 'objects', 'original_show', 'baseline'): STATE.pop(key, None)
    OUTPUT.write_text(json.dumps(STATE, indent=2), encoding='utf-8')
    print('FINGER_JOINT_DRAG_GUI', STATE['status'], error, flush=True)
    bpy.ops.wm.quit_blender()


def setup():
    character_designer.register(); bpy.utils.register_class(TEST_PT_joint_drag)
    C.preferences.filepaths.use_auto_save_temporary_files = False
    C.preferences.filepaths.temporary_directory = str(OUTPUT.parent)
    _, area = view()
    area.spaces.active.show_region_ui = True
    region = area.spaces.active.region_3d
    region.view_distance = 2.4
    region.view_location = Vector((2.9, .4, 0))
    region.view_rotation = Vector((0, -1, 4)).to_track_quat('Z', 'Y')
    STATE['mesh'] = layout.fingerprint(C.edit_object)
    STATE['objects'] = set(bpy.data.objects.keys())
    assert bpy.ops.character_designer.finger_workflow(action='PREPARE') == {'FINISHED'}
    STATE['baseline'] = [j.position for j in work.settings(C)[2].joints]
    guides.show(invalidate=False)
    guides.display_frames(C); guides.cached_frame(C)
    STATE['reference_rechecks'] = 0
    original_frame = definition.frame
    def measured_frame(*args, **kwargs):
        if STATE.get('watch'): STATE['reference_rechecks'] += 1
        return original_frame(*args, **kwargs)
    definition.frame = measured_frame
    original_show = ui.show
    def measured(*args, **kwargs):
        start = time.perf_counter()
        try: return original_show(*args, **kwargs)
        finally: STATE['refresh_ms'].append((time.perf_counter()-start)*1000)
    ui.show = measured
    STATE['handle'] = bpy.types.SpaceView3D.draw_handler_add(observe, (), 'WINDOW', 'POST_PIXEL')
    later(ready, .8)


def ready():
    _, area = view()
    try: next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Drag Test'
    except AttributeError:
        STATE['tries'] = STATE.get('tries', 0)+1
        if STATE['tries'] > 10: raise
        area.tag_redraw(); later(ready); return
    STATE['joint'] = 0
    later(start_drag, .5)


def start_drag():
    win, area = view()
    bpy.ops.screen.screenshot(filepath=str(OUTPUT.with_name('before-drag.png')))
    STATE['watch'] = True
    STATE['step'] = 0
    STATE['before'] = work.settings(C)[2].joints[STATE['joint']].position
    # The factory panel's row height is fixed, but the sidebar moves with window
    # width. Target its actual region instead of assuming a 1100px-wide window.
    sidebar = next(r for r in area.regions if r.type == 'UI')
    x, y = sidebar.x+int(sidebar.width*.5), win.height-123-STATE['joint']*25
    STATE['xy'] = [x, y]
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    later(press)


def press():
    win, _ = view(); x, y = STATE['xy']
    win.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    later(move)


def move():
    win, _ = view(); x, y = STATE['xy']
    STATE['step'] += 1
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x+STATE['step']*5, y=y)
    if STATE['step'] < 12: later(move, .08)
    else: later(release)


def release():
    win, _ = view(); x, y = STATE['xy']
    win.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x+STATE['step']*5, y=y)
    later(check_drag, .4)


def check_drag():
    _, state, pair = work.settings(C)
    assert abs(pair.joints[STATE['joint']].position-STATE['before']) > .01, 'Native drag missed slider'
    assert ui.joint_preview_visible(C), state.status
    if STATE['joint'] == 0:
        STATE['joint'] = 1; later(start_drag); return
    bpy.ops.screen.screenshot(filepath=str(OUTPUT.with_suffix('.png')))
    # Exercise invalid overlap then recovery without pressing the eye/Prepare.
    pair.joints[0].position = pair.joints[1].position = .50
    later(overlap)


def overlap():
    _, state, pair = work.settings(C)
    assert ui.joint_preview_visible(C) and 'overlap' in state.status, state.status
    for j, t in zip(pair.joints, STATE['baseline']): j.position = t
    later(recovered)


def recovered():
    assert ui.joint_preview_visible(C)
    assert not work.state(C)[1].status
    assert STATE['frames'] >= 10 and STATE['blank_frames'] == 0, STATE
    assert len({json.dumps(p) for p in STATE['positions']}) >= 8, 'Rings did not track drag steps'
    assert layout.fingerprint(C.edit_object) == STATE['mesh']
    assert set(bpy.data.objects.keys()) == STATE['objects']
    finish()


later(setup, 1.)
