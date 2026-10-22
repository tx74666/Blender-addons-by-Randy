"""Restore saved workspaces from the retired weight-editing shortcut.

No new workspace, operator or panel is installed. Authored weights are untouched.
"""
import json

import bpy
from . import bone_display, bone_display_sync, selected_bone_weights as paint

SESSION = 'character_designer_weight_workspace_v1'
_TIMER = globals().get('_TIMER')
_REGISTERED = False


def active(context):
    return context.scene.get(SESSION)


def _state(context, rig, mesh):
    value = paint._capture_context_state(context, mesh, rig)
    refs = {'rig': rig, 'mesh': mesh, 'active': value.pop('active_object'),
            'selected': {str(i): obj for i, obj in enumerate(value.pop('selected_objects'))}}
    value.update(display=bone_display._snapshot(rig), in_front=rig.show_in_front,
                 view_layer=context.view_layer.name,
                 flags={key: [obj.hide_get(), obj.hide_viewport, obj.hide_select]
                        for key, obj in (('rig', rig), ('mesh', mesh))})
    refs['state'] = json.dumps(value)
    return refs


def _restore(context, saved):
    rig, mesh = saved.get('rig'), saved.get('mesh')
    if rig is None or mesh is None:
        raise ValueError('The legacy workspace rig or mesh was deleted; undo that deletion first.')
    state = json.loads(saved['state'])
    if context.view_layer.name != state['view_layer']:
        raise ValueError('Return to the original view layer to recover the legacy workspace.')
    if (context.view_layer.objects.get(rig.name) != rig
            or context.view_layer.objects.get(mesh.name) != mesh):
        raise ValueError('Restore the rig and mesh to the original view layer first.')
    state['selected_objects'] = tuple(obj for obj in saved.get('selected', {}).values() if obj)
    state['active_object'] = saved.get('active')
    bone_display._restore(rig, state['display'])
    rig.show_in_front = state['in_front']
    paint._restore_context_state(context, mesh, rig, state)
    for key in ('rig', 'mesh'):
        obj = saved[key]
        hidden, viewport, select = state['flags'][key]
        obj.hide_set(hidden)
        obj.hide_viewport, obj.hide_select = viewport, select
    bone_display_sync.completed(rig)


def leave(context):
    """Recover an old session transactionally, retaining it on failure."""
    saved = active(context)
    if not saved:
        raise ValueError('There is no saved legacy weight workspace.')
    rig, mesh = saved.get('rig'), saved.get('mesh')
    if rig is None or mesh is None:
        raise ValueError('The legacy workspace rig or mesh was deleted; undo that deletion first.')
    current = _state(context, rig, mesh)
    previous_busy = bone_display_sync._BUSY
    bone_display_sync._BUSY = True
    try:
        _restore(context, saved)
        context.scene.pop(SESSION, None)
    except Exception as original:
        try:
            _restore(context, current)
        except Exception as recovery:
            raise RuntimeError(f'Legacy workspace recovery failed: {original}. '
                               f'Rollback also failed: {recovery}') from original
        raise
    finally:
        bone_display_sync._BUSY = previous_busy
        bone_display_sync.completed(rig)


def _recover_once():
    """Use only this window and each session's owning scene and view layer."""
    global _TIMER
    _TIMER = None
    context = bpy.context
    window = getattr(context, 'window', None)
    for scene in tuple(bpy.data.scenes):
        saved = scene.get(SESSION)
        if not saved:
            continue
        try:
            if window is None:
                raise ValueError('Open a window and reload the add-on to recover this workspace.')
            layer = scene.view_layers.get(json.loads(saved['state'])['view_layer'])
            if layer is None:
                raise ValueError('The original view layer was removed.')
            previous_scene, previous_layer = window.scene, window.view_layer
            try:
                window.scene = scene
                window.view_layer = layer
                leave(context)
            finally:
                window.scene = previous_scene
                window.view_layer = previous_layer
        except Exception as exc:
            # Do not discard recovery references or retry in a permanent timer.
            print(f'Character Designer: legacy weight workspace retained in {scene.name!r}: {exc}')
    return None


def _schedule_recovery():
    global _TIMER
    if not _REGISTERED:
        return
    if _TIMER is not None and bpy.app.timers.is_registered(_TIMER):
        bpy.app.timers.unregister(_TIMER)
    _TIMER = _recover_once
    bpy.app.timers.register(_TIMER, first_interval=0.1)


@bpy.app.handlers.persistent
def _load_post(_unused):
    _schedule_recovery()


def _remove_callbacks():
    global _TIMER, _REGISTERED
    _REGISTERED = False
    if _TIMER is not None and bpy.app.timers.is_registered(_TIMER):
        bpy.app.timers.unregister(_TIMER)
    _TIMER = None
    for handler in tuple(bpy.app.handlers.load_post):
        if (getattr(handler, '__module__', '') == __name__
                and getattr(handler, '__name__', '') == '_load_post'):
            bpy.app.handlers.load_post.remove(handler)


def register():
    global _REGISTERED
    _remove_callbacks()
    _REGISTERED = True
    bpy.app.handlers.load_post.append(_load_post)
    # Blender restricts scene access during registration; recover only afterwards.
    _schedule_recovery()


def unregister():
    _remove_callbacks()
    # Failed initialization may have a restricted context and no accessible data.
    if getattr(bpy.context, 'scene', None) is not None:
        _recover_once()
