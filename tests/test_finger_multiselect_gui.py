"""Isolated GUI: modifier events, actual overlays, no per-redraw mesh scans."""
import json
import sys
import time
import traceback
from pathlib import Path
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import bound_fixture, bank, definition, fingerprint, character_designer, C
from character_designer import finger_definition_ui as preview, finger_bank_ui as ui

ARGS = sys.argv[sys.argv.index('--')+1:]
STATE = {}


class TEST_PT_finger_selection(bpy.types.Panel):
    bl_label='Finger · Basic Setup'
    bl_idname='TEST_PT_finger_selection'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='Finger Test'
    def draw(self, context):
        ui.draw_header(self.layout, context)
        preview.cached_frame(context)  # Same validation consumer as Basic Setup.


def view():
    win = C.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type=='VIEW_3D')
    return win, area, next(r for r in area.regions if r.type=='WINDOW')


def finish(error=None):
    if error: bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_name('failed-click.png')))
    result = {'status':'FAIL' if error else 'PASS', 'error':str(error) if error else None,
              'perf':STATE.get('perf'), 'version':character_designer.bl_info['version']}
    Path(ARGS[1]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('FINGER_MULTISELECT_GUI', result, flush=True)
    bpy.ops.wm.quit_blender()


def later(fn, seconds=.8):
    attempts=[0]
    def wrapped():
        try:
            if preview._visible and preview._display_cache is None:
                attempts[0]+=1
                if attempts[0]>20: raise AssertionError('Visible overlay never rebuilt: '+fn.__name__)
                view()[1].tag_redraw()
                return .1
            fn()
        except Exception as exc: traceback.print_exc(); finish(exc)
    bpy.app.timers.register(wrapped, first_interval=seconds)


def key(number, shift=False, ctrl=False):
    win, area, region = view()
    for value in ('PRESS','RELEASE'):
        win.event_simulate(type='F'+str(number), value=value, shift=shift, ctrl=ctrl,
                          x=region.x+region.width//2, y=region.y+region.height//2)


def setup():
    original=ui.CHARACTERDESIGNER_OT_finger_setup.invoke
    def logged_invoke(self,context,event):
        print('SELECT_EVENT', self.action, self.digit, event.type, event.shift, event.ctrl, flush=True)
        start=time.perf_counter()
        result=original(self,context,event)
        if STATE.get('warm_guard'):
            STATE['event_start']=start
            STATE['perf'].setdefault('warm_operator_ms',[]).append((time.perf_counter()-start)*1000)
        return result
    ui.CHARACTERDESIGNER_OT_finger_setup.invoke=logged_invoke
    draw=preview._draw_lines
    def measured_draw():
        draw()
        start=STATE.pop('event_start',None)
        if start is not None:
            STATE['perf'].setdefault('warm_event_to_draw_ms',[]).append((time.perf_counter()-start)*1000)
    preview._draw_lines=measured_draw
    character_designer.register(); bpy.utils.register_class(TEST_PT_finger_selection)
    C.preferences.filepaths.use_auto_save_temporary_files=False
    C.preferences.filepaths.temporary_directory=str(Path(ARGS[1]).parent)
    C.preferences.edit.use_global_undo=True
    win, area, region=view()
    area.spaces.active.show_region_ui=True
    area.spaces.active.region_3d.view_distance=2.4
    area.spaces.active.region_3d.view_location=Vector((2.9,.4,0))
    area.spaces.active.region_3d.view_rotation=Vector((0,-1,4)).to_track_quat('Z','Y')
    area.spaces.active.overlay.show_floor=False
    if ARGS[0]=='--real-run':
        obj=C.edit_object
        bank.select(C,'INDEX')
        points=[obj.matrix_world @ Vector(p)
                for slot in obj.character_designer_finger_bank.slots if slot.name.endswith('.L')
                for p in json.loads(slot.guide.record)['internal']['path']]
        center=sum(points,Vector())/len(points)
        area.spaces.active.region_3d.view_location=center
        area.spaces.active.region_3d.view_distance=max((p-center).length for p in points)*4
    for number, digit in enumerate(bank.detect.DIGITS,1):
        item=C.window_manager.keyconfigs.active.keymaps['3D View'].keymap_items.new('character_designer.finger_setup','F'+str(number),'PRESS',any=True)
        item.properties.action='SELECT'; item.properties.digit=digit
    C.window_manager.keyconfigs.update()
    STATE['mesh']=fingerprint(C.edit_object)
    STATE['objects']=set(bpy.data.objects.keys())
    if ARGS[0]=='--real-run':
        # Dismiss Blender's blocked-script notice, never permit embedded scripts.
        for value in ('PRESS','RELEASE'): win.event_simulate(type='ESC',value=value,x=700,y=500)
        later(begin_keys,.4)
    else: begin_keys()


def begin_keys():
    key(1); later(first)


def first():
    win, area, region=view()
    next(r for r in area.regions if r.type=='UI').active_panel_category='Finger Test'
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==('THUMB',)
    assert len(preview._display_cache['frames'])==2
    STATE['next']=2
    key(2,shift=True); later(add_next)


def add_next():
    assert len(bank.selected_digits(C.edit_object.character_designer_finger_bank))==STATE['next']
    assert len(preview._display_cache['frames'])==STATE['next']*2
    if STATE['next']<5:
        STATE['next']+=1; key(STATE['next'],shift=True); later(add_next)
    else:
        bpy.ops.screen.screenshot(filepath=str(Path(ARGS[1]).with_suffix('.png')))
        key(3,shift=True); later(removed)


def removed():
    assert len(preview._display_cache['frames'])==8
    key(1); later(plain_removed)


def plain_removed():
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==('INDEX','RING','PINKY')
    assert len(preview._display_cache['frames'])==6
    key(1); later(single)


def single():
    assert len(preview._display_cache['frames'])==2
    key(5,ctrl=True,shift=True); later(ranged)


def ranged():
    b=C.edit_object.character_designer_finger_bank
    assert bank.selected_digits(b)==bank.detect.DIGITS and len(preview._display_cache['frames'])==10
    # Once warmed, redraws must only reuse references and GPU batches.
    old=definition._snapshot
    def forbidden(*args, **kwargs): raise AssertionError('Idle display revalidated mesh')
    definition._snapshot=forbidden
    try:
        batches=preview._batches
        win, area, region=view()
        with C.temp_override(window=win,area=area,region=region):
            start=time.perf_counter(); bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=30)
        assert batches is preview._batches and batches is not None
        STATE['perf']={'ms_per_redraw':(time.perf_counter()-start)*1000/30,'axes':10,'reused_batches':True}
    finally: definition._snapshot=old
    bpy.ops.character_designer.finger_setup(action='TOGGLE')
    assert not preview._visible and preview._display_cache is None
    bpy.ops.character_designer.finger_setup(action='TOGGLE')
    later(shown)


def shown():
    assert len(preview._display_cache['frames'])==10
    assert fingerprint(C.edit_object)==STATE['mesh']
    assert set(bpy.data.objects.keys())==STATE['objects']
    # Native Undo/Redo may restore an older selected set; all display caches
    # must be hidden, then rebuild from whichever persisted selection won.
    bpy.ops.ed.undo_push(message='Multi-selection baseline')
    win,area,region=view()
    with C.temp_override(window=win,area=area,region=region): bpy.ops.transform.translate(value=(.001,0,0))
    bpy.ops.ed.undo_push(message='Disposable mesh edit')
    for value in ('PRESS','RELEASE'): win.event_simulate(type='Z',value=value,ctrl=True,x=region.x+region.width//2,y=region.y+region.height//2)
    later(undone)


def undone():
    assert not preview._visible and preview._display_cache is None
    preview.show(); later(restored)


def restored():
    assert len(preview._display_cache['frames'])==10
    assert fingerprint(C.edit_object)==STATE['mesh']
    # fingerprint() flushes Edit Mode and legitimately emits a geometry update.
    # Let that event finish and revalidate before guarding selection-only events.
    later(warm_buttons)


def warm_buttons():
    assert len(preview._display_cache['frames'])==10 and preview._display_cache['complete']
    # These real mouse events must reuse the validated frames, not just happen
    # to run fast on a tiny fixture. Unrelated screen redraws may not rescan it.
    STATE['originals']=(definition.frame,definition._snapshot,bank.dirty)
    def forbidden(*args,**kwargs):raise AssertionError('Warm GUI click revalidated geometry')
    definition.frame=definition._snapshot=bank.dirty=forbidden
    STATE['warm_guard']=True
    mouse(0); later(mouse_removed)


def mouse(index, shift=False, ctrl=False):
    # Button locations verified in this test window's screenshot (1400x1000).
    win, _, _=view()
    x,y=905+index*45,win.height-124
    # Native UI buttons consult held modifier state, unlike a keymap invoke.
    # Release the simulated Undo modifier, then press the requested keys.
    for kind in ('LEFT_CTRL','LEFT_SHIFT'):
        win.event_simulate(type=kind,value='RELEASE',x=x,y=y,ctrl=False,shift=False)
    if ctrl: win.event_simulate(type='LEFT_CTRL',value='PRESS',x=x,y=y,ctrl=True)
    if shift: win.event_simulate(type='LEFT_SHIFT',value='PRESS',x=x,y=y,ctrl=ctrl,shift=True)
    win.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y,shift=shift,ctrl=ctrl)
    def press():
        for value in ('PRESS','RELEASE'):
            win.event_simulate(type='LEFTMOUSE',value=value,x=x,y=y,shift=shift,ctrl=ctrl)
    bpy.app.timers.register(press,first_interval=.15)


def mouse_removed():
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==('INDEX','MIDDLE','RING','PINKY')
    assert len(preview._display_cache['frames'])==8
    mouse(0); later(mouse_single)


def mouse_single():
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==('THUMB',), 'Actual button single click'
    mouse(2,shift=True); later(mouse_toggle)


def mouse_toggle():
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==('THUMB','MIDDLE'), ('Actual button Shift click',bank.selected_digits(C.edit_object.character_designer_finger_bank))
    mouse(4,shift=True,ctrl=True); later(mouse_range)


def mouse_range():
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==('THUMB','MIDDLE','RING','PINKY'), 'Actual button range click'
    mouse(0,shift=True,ctrl=True); later(mouse_all)


def mouse_all():
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==bank.detect.DIGITS
    assert len(preview._display_cache['frames'])==10
    STATE['remove']=4
    mouse(4); later(mouse_remove_each)


def mouse_remove_each():
    remaining=STATE['remove']
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==bank.detect.DIGITS[:remaining]
    assert len(preview._display_cache['frames'])==remaining*2
    if remaining:
        STATE['remove']-=1; mouse(remaining-1); later(mouse_remove_each)
    else:
        assert preview._drawable_frames()==()
        mouse(0); later(mouse_restored)


def mouse_restored():
    assert bank.selected_digits(C.edit_object.character_designer_finger_bank)==('THUMB',)
    assert len(preview._display_cache['frames'])==2
    definition.frame,definition._snapshot,bank.dirty=STATE['originals']
    STATE['warm_guard']=False
    assert fingerprint(C.edit_object)==STATE['mesh']
    assert set(bpy.data.objects.keys())==STATE['objects']
    finish()


if ARGS[0]=='--build':
    character_designer.register(); bound_fixture(); bpy.ops.wm.save_as_mainfile(filepath=ARGS[1],check_existing=False)
else: later(setup,1)
