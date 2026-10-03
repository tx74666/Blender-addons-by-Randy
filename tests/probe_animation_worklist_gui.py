"""Observe actual worklist gestures in a separate, factory-startup GUI process.

blender --factory-startup --python tests/probe_animation_worklist_gui.py --
    --blend ABSOLUTE_ISOLATED_EXAMPLE.blend --status ABSOLUTE_PRIVATE_STATUS.json

Opens only the supplied QA file. Does not save a blend, Sync, launch a worker,
or operate Unity. The window remains open for the operator's real mouse test.
The status file records state changes and a bounded drag event trace; the
production drag callbacks are delegated without altering their results.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
STATE = {'phase': 'starting', 'dragcount': 0, 'dragEvents': [], 'errors': []}
PREVIOUS = None
STATUS = None
SCENE_NAME = None


def write_status(record):
    global PREVIOUS
    encoded = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False)
    if encoded != PREVIOUS:
        temporary = STATUS.with_suffix(STATUS.suffix + '.tmp')
        temporary.write_text(encoded, encoding='utf-8')
        temporary.replace(STATUS)
        PREVIOUS = encoded


def error(message):
    if message not in STATE['errors']:
        STATE['errors'].append(message)
        del STATE['errors'][:-10]


def trace_drag(ui):
    """Instrument this cold QA registration only; never modify source files."""
    cls = ui.CHARACTERDESIGNER_OT_worklist_drag
    original_invoke, original_modal = cls.invoke, cls.modal

    def record(operator, stage, event, result):
        entry = {'stage': stage, 'type': event.type, 'value': event.value,
                 'mouseY': event.mouse_y, 'itemID': operator.item_id,
                 'mousePressY': getattr(event, 'mouse_prev_press_y', None),
                 'delta': getattr(operator, '_delta', None),
                 'destination': getattr(operator, '_destination', None),
                 'rowHeight': getattr(operator, '_row_height', None),
                 'result': sorted(result)}
        # Keep mouse movement only when its destination changes.
        if (stage == 'modal' and event.type == 'MOUSEMOVE' and STATE['dragEvents']
                and STATE['dragEvents'][-1]['stage'] == 'modal'
                and STATE['dragEvents'][-1]['type'] == 'MOUSEMOVE'
                and STATE['dragEvents'][-1]['destination'] == entry['destination']):
            return
        STATE['dragEvents'].append(entry)
        del STATE['dragEvents'][:-32]

    def invoke(self, context, event):
        STATE['dragcount'] += 1
        try:
            result = original_invoke(self, context, event)
            record(self, 'invoke', event, result)
            return result
        except Exception:
            error(traceback.format_exc())
            raise

    def modal(self, context, event):
        try:
            result = original_modal(self, context, event)
            if event.type in {'MOUSEMOVE', 'LEFTMOUSE', 'RIGHTMOUSE', 'ESC'}:
                record(self, 'modal', event, result)
            return result
        except Exception:
            error(traceback.format_exc())
            raise

    cls.invoke, cls.modal = invoke, modal
    handle = ui.CHARACTERDESIGNER_OT_worklist_drag_handle
    original_handle_invoke = handle.invoke

    def invoke_handle(self, context, event):
        STATE['dragcount'] += 1
        try:
            result = original_handle_invoke(self, context, event)
            record(self, 'handle_invoke', event, result)
            return result
        except Exception:
            error(traceback.format_exc())
            raise

    handle.invoke = invoke_handle


def configure_view(window, rig):
    areas = [area for area in window.screen.areas if area.type == 'VIEW_3D']
    if not areas:
        raise RuntimeError('Factory QA window has no 3D Viewport.')
    points = [rig.matrix_world @ point for bone in rig.data.bones
              for point in (bone.head_local, bone.tail_local)]
    if not points:
        raise RuntimeError('The saved worklist rig has no bones to frame.')
    minimum = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    maximum = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    center, diameter = (minimum + maximum) * .5, max((maximum - minimum).length, .1)
    for area in areas:
        space = area.spaces.active
        space.show_region_ui = True
        space.shading.type = 'SOLID'
        space.region_3d.view_location = center
        space.region_3d.view_distance = diameter * 1.6
        space.region_3d.view_rotation = Vector((2, -8, 1.5)).to_track_quat('Z', 'Y')
        space.region_3d.view_perspective = 'ORTHO'
        area.tag_redraw()
    timeline = next((area for area in window.screen.areas
                     if area.type == 'DOPESHEET_EDITOR'), None)
    if timeline is not None:
        timeline.spaces.active.mode = 'ACTION'
        timeline.tag_redraw()


def snapshot(ui):
    scene = bpy.data.scenes.get(SCENE_NAME)
    if scene is None:
        raise RuntimeError('The QA worklist scene was removed.')
    saved = scene.character_designer_animation_worklist
    rig = saved.rig
    ad = rig.animation_data if rig else None
    action = ad.action if ad else None
    slot = ad.action_slot if ad else None
    selected = saved.items[saved.active_index] if 0 <= saved.active_index < len(saved.items) else None
    matches = [(item.item_id, side) for item in saved.items
               for side, candidate in (('SOURCE', item.source_action), ('CUSTOM', item.custom_action))
               if action is not None and candidate == action]
    active_id, side = matches[0] if len(matches) == 1 else (None, None)
    if saved.has_error:
        error(saved.status)
    return {**STATE, 'scene': scene.name,
            'worklistOrder': [{'id': item.item_id, 'name': item.name} for item in saved.items],
            'selectedID': selected.item_id if selected else None,
            'activeID': active_id, 'activeSide': side,
            'activeAction': action.name if action else None,
            'activeSlot': slot.handle if slot else None,
            'activeDrags': [{'id': operator.item_id, 'destination': operator._destination}
                            for operator in tuple(ui._DRAGS)],
            'worklistStatus': saved.status}


def main():
    global STATUS, SCENE_NAME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blend', required=True)
    parser.add_argument('--status', required=True)
    arguments = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    blend, STATUS = Path(arguments.blend), Path(arguments.status)
    if not blend.is_absolute() or blend.suffix.lower() != '.blend' or not blend.is_file():
        raise ValueError('--blend must name an existing absolute isolated QA .blend file.')
    if not STATUS.is_absolute() or STATUS.suffix.lower() != '.json':
        raise ValueError('--status must name an absolute private JSON output file.')
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATE['blend'] = str(blend.resolve())
    digest = hashlib.sha256()
    with blend.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    STATE['inputSha256'] = digest.hexdigest()
    write_status(STATE)
    if bpy.app.background or bpy.context.window is None:
        raise RuntimeError('This probe requires a separate factory-startup GUI Blender process.')
    if 'character_designer' in sys.modules or hasattr(bpy.types.Scene, 'character_designer_animation_worklist'):
        raise RuntimeError('Start a fresh --factory-startup process; this probe does not hot-reload an add-on.')
    sys.path.insert(0, str(ROOT / 'addons'))
    import character_designer
    from character_designer import animation_worklist_ui as ui
    from character_designer.ui_constants import SIDEBAR_CATEGORY, UI_PAGE_ANIMATION
    if Path(character_designer.__file__).resolve() != ROOT / 'addons/character_designer/__init__.py':
        raise RuntimeError('The probe did not load the canonical add-on source.')
    trace_drag(ui)
    character_designer.register()
    bpy.context.preferences.view.show_splash = False
    bpy.context.preferences.filepaths.use_auto_save_temporary_files = False
    bpy.ops.wm.open_mainfile(filepath=str(blend), use_scripts=False, load_ui=False)
    STATE['phase'] = 'waiting_for_window'
    write_status(STATE)
    attempts = [0, 0]
    owned_window = [None]

    def setup_gui():
        global SCENE_NAME
        try:
            attempts[0] += 1
            windows = [window for wm in bpy.data.window_managers for window in wm.windows]
            if not windows or windows[0].screen is None:
                if attempts[0] >= 40:
                    raise RuntimeError('The isolated GUI window did not become available.')
                return .25
            window = windows[0]
            candidates = [scene for scene in bpy.data.scenes
                          if scene.character_designer_animation_worklist.workspace_path
                          and len(scene.character_designer_animation_worklist.items) >= 2
                          and scene.character_designer_animation_worklist.rig is not None]
            if len(candidates) != 1:
                raise RuntimeError('Expected one saved worklist scene containing at least two entries.')
            scene = candidates[0]
            SCENE_NAME = scene.name
            window.scene = scene
            with bpy.context.temp_override(window=window):
                saved = scene.character_designer_animation_worklist
                rig = saved.rig
                if rig.type != 'ARMATURE' or scene.objects.get(rig.name) != rig:
                    raise RuntimeError('The saved worklist rig is not an armature in its editing scene.')
                for obj in bpy.context.selected_objects:
                    obj.select_set(False)
                rig.hide_set(False)
                rig.select_set(True)
                window.view_layer.objects.active = rig
                bpy.context.window_manager.character_designer.ui_page = UI_PAGE_ANIMATION
                saved.show_catalog = False
                configure_view(window, rig)
            owned_window[0] = window
            STATE['phase'] = 'waiting_for_sidebar'
            bpy.app.timers.register(observe, first_interval=.25)
        except Exception:
            error(traceback.format_exc())
            STATE['phase'] = 'error'
            write_status(STATE)
        return None

    def observe():
        try:
            window = owned_window[0]
            if STATE['phase'] == 'waiting_for_sidebar':
                attempts[1] += 1
                pending = False
                for area in window.screen.areas:
                    if area.type != 'VIEW_3D':
                        continue
                    region = next((region for region in area.regions if region.type == 'UI'), None)
                    if region is not None:
                        try:
                            region.active_panel_category = SIDEBAR_CATEGORY
                        except (TypeError, ValueError):
                            pending = True  # Categories are populated after the first native draw.
                    area.tag_redraw()
                if not pending:
                    STATE['phase'] = 'ready'
                elif attempts[1] >= 20:
                    STATE['phase'] = 'ready_select_sidebar_manually'
            write_status(snapshot(ui))
            return .2
        except Exception:
            error(traceback.format_exc())
            STATE['phase'] = 'error'
            write_status(STATE)
            return None

    bpy.app.timers.register(setup_gui, first_interval=.25)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        error(traceback.format_exc())
        STATE['phase'] = 'error'
        if STATUS is not None:
            write_status(STATE)
        raise
