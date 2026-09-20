"""Render actual shared-context/subcategory placement in an isolated GUI fixture."""
import json
import sys
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
import character_designer as cd
from character_designer import character_setup, bone_display

OUTPUT = Path(sys.argv[sys.argv.index('--')+1])
STATE = {}


def later(fn, delay=.5):
    def guarded():
        try: fn()
        except Exception as exc:
            traceback.print_exc(); finish(str(exc))
    bpy.app.timers.register(guarded, first_interval=delay)


def view():
    win = bpy.context.window_manager.windows[0]
    return win, next(a for a in win.screen.areas if a.type == 'VIEW_3D')


def finish(error=None):
    OUTPUT.write_text(json.dumps({'status': 'FAIL' if error else 'PASS', 'error': error,
                                 'version': cd.bl_info['version']}, indent=2), encoding='utf-8')
    print('RIG_HIERARCHY_GUI', 'FAIL' if error else 'PASS', error, flush=True)
    bpy.ops.wm.quit_blender()


def setup():
    cd.register()
    bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
    bpy.context.preferences.filepaths.temporary_directory = str(OUTPUT.parent)
    _, area = view()
    area.spaces.active.show_region_ui = True
    bpy.ops.character_designer.set_rig_section(section='BODY')
    STATE['objects'] = set(bpy.data.objects.keys())
    later(ready)


def ready():
    _, area = view()
    try: next(r for r in area.regions if r.type == 'UI').active_panel_category = 'Character Designer'
    except AttributeError:
        STATE['tries'] = STATE.get('tries', 0)+1
        if STATE['tries'] > 10: raise
        area.tag_redraw(); later(ready); return
    STATE['remaining'] = ['BODY', 'HAIR', 'SKIRT']
    later(capture)


def capture():
    section = STATE['remaining'].pop(0)
    assert bpy.context.window_manager.character_designer.rig_section == section
    assert cd.CHARACTERDESIGNER_PT_rig_sections.poll(bpy.context)
    assert character_setup.CHARACTERDESIGNER_PT_character_setup.poll(bpy.context)
    assert bone_display.CHARACTERDESIGNER_PT_bone_display.poll(bpy.context)
    bpy.ops.screen.screenshot(filepath=str(OUTPUT.with_name(section+'.png')))
    if STATE['remaining']:
        bpy.ops.character_designer.set_rig_section(section=STATE['remaining'][0])
        view()[1].tag_redraw()
        later(capture)
    else:
        bpy.ops.character_designer.set_rig_section(section='BODY')
        view()[1].tag_redraw()
        later(collapse_display)


def click_header(y):
    win, _ = view()
    for value in ('PRESS', 'RELEASE'):
        win.event_simulate(type='LEFTMOUSE', value=value, x=739, y=win.height-y)


def collapse_display():
    click_header(217)
    later(collapsed)


def collapsed():
    bpy.ops.screen.screenshot(filepath=str(OUTPUT.with_name('COLLAPSED.png')))
    assert cd.CHARACTERDESIGNER_PT_rig_sections.poll(bpy.context)
    click_header(192)
    later(expanded)


def expanded():
    bpy.ops.screen.screenshot(filepath=str(OUTPUT.with_name('EXPANDED.png')))
    assert STATE['objects'] == set(bpy.data.objects.keys())
    finish()


later(setup, 1.)
