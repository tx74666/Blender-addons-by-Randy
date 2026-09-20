"""Real event-loop operators, native keyboard undo/redo and cached draw timing."""
import json
import sys
import time
import traceback
from pathlib import Path
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import bound_fixture, work, ui, targets, bank, layout, character_designer, C
from character_designer import finger_definition_ui as guide_ui, finger_definition as definition

ARGS = sys.argv[sys.argv.index('--')+1:]
STATE = {'perf': {}}


def view():
    window = C.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def guarded(fn):
    def run():
        try: return fn()
        except Exception as exc:
            traceback.print_exc(); finish(str(exc))
    return run


def later(fn, delay=.7): bpy.app.timers.register(guarded(fn), first_interval=delay)


def key(kind, ctrl=False, shift=False):
    win, area, region = view()
    for value in ('PRESS', 'RELEASE'):
        win.event_simulate(type=kind, value=value, ctrl=ctrl, shift=shift, x=region.x+region.width//2, y=region.y+region.height//2)


def rolls(): return {b.name: [list(r) for r in b.matrix_local] for b in bpy.data.objects['MainRig'].data.bones}


def metadata(obj):
    s = obj.character_designer_finger_workflow
    return {'results': s.results, 'signature': s.signature, 'applied': [p.applied for p in s.pairs],
            'guides': {p.name: p.guide.record for p in obj.character_designer_finger_bank.slots}}


def finish(error=None):
    STATE.update(status='FAIL' if error else 'PASS', error=error)
    Path(ARGS[1]).write_text(json.dumps(STATE, indent=2), encoding='utf-8')
    print('FINGER_WORKFLOW_GUI', STATE['status'], error, STATE['perf'], flush=True)
    bpy.ops.wm.quit_blender()


def setup():
    character_designer.register()
    C.preferences.filepaths.use_auto_save_temporary_files = False
    C.preferences.filepaths.temporary_directory = str(Path(ARGS[1]).parent)
    C.preferences.edit.use_global_undo = True
    C.window_manager.character_designer.ui_page = 'RIG'
    C.window_manager.character_designer.rig_section = 'BODY'
    win, area, region = view()
    area.spaces.active.show_region_ui = True
    area.spaces.active.region_3d.view_distance = 2.4
    area.spaces.active.region_3d.view_location = Vector((2.9, .4, 0))
    area.spaces.active.region_3d.view_rotation = Vector((0, -1, 4)).to_track_quat('Z', 'Y')
    area.spaces.active.overlay.show_floor = False
    for kind, action in (('F6', 'ALL'), ('F7', 'PREPARE'), ('F8', 'APPLY'), ('F9', 'WEIGHTS')):
        item = C.window_manager.keyconfigs.active.keymaps['3D View'].keymap_items.new('character_designer.finger_workflow', kind, 'PRESS')
        item.properties.action = action
    C.window_manager.keyconfigs.update()
    later(start)


def start():
    win, area, region = view()
    next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Character Designer'
    STATE['roll_before'] = rolls()
    STATE['mesh_before'] = layout.fingerprint(C.edit_object)
    bpy.ops.ed.undo_push(message='Workflow baseline')
    key('F6'); later(calibrated)


def calibrated():
    STATE['roll_after'] = rolls()
    assert STATE['roll_after'] != STATE['roll_before'], 'All did not change roll'
    assert layout.fingerprint(C.edit_object) == STATE['mesh_before']
    key('Z', ctrl=True); later(roll_undone)


def roll_undone():
    assert rolls() == STATE['roll_before'], 'Roll Undo mismatch'
    key('Z', ctrl=True, shift=True); later(roll_redone)


def roll_redone():
    assert rolls() == STATE['roll_after'], 'Roll Redo mismatch'
    key('F7'); later(prepared)


def prepared():
    obj = C.edit_object
    assert obj.character_designer_finger_workflow.source
    STATE['metadata_before'] = metadata(obj)
    key('F8'); later(applied, 1.2)


def applied():
    obj = C.edit_object
    STATE['mesh_after'] = layout.fingerprint(obj)
    STATE['metadata_after'] = metadata(obj)
    assert STATE['mesh_after'] != STATE['mesh_before']
    assert obj.character_designer_finger_workflow.pairs['INDEX'].applied
    key('Z', ctrl=True); later(mesh_undone)


def mesh_undone():
    obj = C.edit_object
    assert layout.fingerprint(obj) == STATE['mesh_before'], 'Combined topology/weight Undo mismatch'
    assert metadata(obj) == STATE['metadata_before'], 'Metadata Undo mismatch'
    key('Z', ctrl=True, shift=True); later(mesh_redone)


def mesh_redone():
    obj = C.edit_object
    assert layout.fingerprint(obj) == STATE['mesh_after'], 'Combined Redo mismatch'
    assert metadata(obj) == STATE['metadata_after'], 'Metadata Redo mismatch'
    obj.character_designer_finger_workflow.pairs['INDEX'].joints[0].root_weight = .7
    key('F9'); later(weighted)


def weighted():
    STATE['weights_after'] = layout.fingerprint(C.edit_object)
    assert STATE['weights_after'] != STATE['mesh_after']
    key('Z', ctrl=True); later(weights_undone)


def weights_undone():
    assert layout.fingerprint(C.edit_object) == STATE['mesh_after'], 'Weight Undo mismatch'
    key('Z', ctrl=True, shift=True); later(weights_redone)


def weights_redone():
    assert layout.fingerprint(C.edit_object) == STATE['weights_after'], 'Weight Redo mismatch'
    # Warm the reference and GPU caches before measuring idle redraw work.
    STATE['perf_index'] = 0
    STATE['scans'] = 0
    for module, attr in ((definition, '_snapshot'), (layout, 'fingerprint'), (targets, 'index'), (work, 'build_plan')):
        original = getattr(module, attr)
        def counted(*args, _original=original, **kwargs):
            STATE['scans'] += 1
            return _original(*args, **kwargs)
        setattr(module, attr, counted)
    perf_setup()


def perf_setup():
    _, area, _ = view()
    index = STATE['perf_index']
    area.spaces.active.show_region_ui = index != 0
    guide_ui.hide(); ui.hide()
    if index in (2, 3): ui.show_bend(C, all_fingers=index == 3)
    later(perf_run, 1.2)


def perf_run():
    win, area, region = view()
    STATE['scans'] = 0
    with C.temp_override(window=win, area=area, region=region):
        start = time.perf_counter()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=30)
        elapsed = time.perf_counter()-start
    index = STATE['perf_index']
    label = ('panel_closed', 'panel_open', 'pair_preview', 'ten_preview')[index]
    STATE['perf'][label] = {'ms_per_redraw': elapsed*1000/30, 'full_scans': STATE['scans'],
                             'preview_groups': len(ui._preview['groups']) if ui._preview else 0}
    assert STATE['scans'] == 0, (label, 'idle full scan')
    if index == 3:
        assert ui._preview and len(ui._preview['labels']) == 10, 'Ten-finger preview was lost'
        bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_suffix('.png')))
        ui.show(C)
        later(screenshot_rings)
    else:
        STATE['perf_index'] += 1; later(perf_setup)


def screenshot_rings():
    bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_name('joint-rings.png')))
    for name in list(STATE):
        if name not in ('perf',): del STATE[name]
    finish()


if ARGS[0] == '--build':
    character_designer.register(); bound_fixture()
    bpy.ops.wm.save_as_mainfile(filepath=ARGS[1], check_existing=False)
else: later(setup, 1)
