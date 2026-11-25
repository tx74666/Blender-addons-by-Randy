"""A reversible, selected-control reduction on an isolated Action and its original slot."""
import json
import math
import re

import bpy

from . import animation_preview_curves as curves
from . import natural_bind_mode
from .animation_export_inputs import _curves
from .animation_local_motion_pins import assign_character_action


SESSION = 'character_designer_key_preview_session'
TEMP = 'character_designer_key_preview_temp'
_LEAF = re.compile(r'(?:\["((?:[^"\\]|\\.)*)"\]|(?:^|\.)(\w+))$')


def _rig(context):
    rig = context.active_object
    if (not rig or rig.type != 'ARMATURE' or rig.mode != 'POSE'
            or rig.library or rig.override_library or rig.data.library or rig.data.override_library):
        raise ValueError('Select the local character controls in Pose Mode.')
    return rig


def _binding(rig):
    ad = rig.animation_data
    return (ad.action, ad.action_slot_handle) if ad and ad.action else (None, None)


def _view(rig, record):
    action, handle = _binding(rig)
    if handle == record['slot']:
        if action == record.get('source'):
            return 'ORIGINAL'
        if action == record.get('preview'):
            return 'REDUCED'
    return 'OTHER'


def status(context):
    rig = context.active_object
    record = rig.get(SESSION) if rig and rig.type == 'ARMATURE' else None
    if record is None:
        return None
    data = json.loads(record['settings'])
    return dict(view=_view(rig, record), **{k: data[k] for k in (
        'bone_names', 'position_mm', 'rotation_degrees', 'before', 'after')})


def _ready(context, rig):
    from . import animation_controller_edit, animation_original_inputs_ui, animation_worklist
    if natural_bind_mode.KEY in rig:
        raise ValueError('Key or finish the current Natural Bind pose before switching Actions.')
    if animation_original_inputs_ui.original_inputs_live(context):
        raise ValueError('Finish editing Original Rigging Inputs before previewing keys.')
    if animation_controller_edit.pending(context.scene):
        raise ValueError('Finish preparing controller motions before previewing keys.')
    animation_worklist._idle(context)
    ad = rig.animation_data
    if (ad is None or ad.use_tweak_mode or ad.action_influence != 1.
            or ad.action_blend_type != 'REPLACE'
            or (ad.use_nla and any(not track.mute and track.strips for track in ad.nla_tracks))):
        raise ValueError('Use a directly playing Action for key reduction.')


def _writable_source(rig, action, handle):
    if (not action or action.library or action.override_library or not action.is_editable
            or TEMP in action or action.asset_data):
        raise ValueError('Choose an editable local animation Action.')
    for scene in bpy.data.scenes:
        worklist = getattr(scene, 'character_designer_animation_worklist', None)
        if worklist:
            for rows in (worklist.items, worklist.retired_items):
                if any(row.source_action == action and row.source_slot == handle for row in rows):
                    raise ValueError('Open the editable motion instead of its imported source.')
        for record in getattr(scene, 'character_designer_controller_motions', ()):
            if record.source_action == action and record.source_slot == handle:
                raise ValueError('Open the prepared controller Action instead of the mapping source.')
            if record.target != rig and (
                    (record.controller_action == action and record.controller_slot == handle)
                    or (record.alternate_action == action and record.alternate_slot == handle)):
                raise ValueError('This controller Action is shared with another character.')
    # Scene motion records may legitimately hold this Action. A different
    # animating datablock, Action constraint, or NLA strip can consume it too.
    owners = bpy.data.user_map(subset={action}).get(action, ())
    for owner in owners:
        ad = getattr(owner, 'animation_data', None)
        if ad and ad.action == action and ad.action_slot_handle == handle and owner != rig:
            raise ValueError('Another object uses this Action slot. Make a single-user copy first.')
        if ad and any(strip.action == action and strip.action_slot_handle == handle
                      for track in ad.nla_tracks for strip in track.strips if strip.type == 'CLIP'):
            raise ValueError('This Action slot is also used by an NLA strip.')
        if owner != rig and not isinstance(owner, bpy.types.Scene) and not (
                ad and ad.action == action and ad.action_slot_handle != handle):
            raise ValueError('Another datablock references this Action. Make a single-user copy first.')


def _record(context):
    rig = _rig(context)
    record = rig.get(SESSION)
    if (record is None or record.get('scene') != context.scene or not record.get('source')
            or not record.get('preview') or record['preview'].get(TEMP) != rig):
        raise ValueError('The preview binding changed. Discard the preview from its original character and scene.')
    for action in (record['source'], record['preview']):
        if action.library or action.override_library or not _has_slot(action, record['slot']):
            raise ValueError('The preview Action or slot changed. Discard this comparison.')
    return rig, record


def _has_slot(action, handle):
    return action is not None and any(slot.handle == handle for slot in action.slots)


def _held(context, rig):
    """Keep native unkeyed inputs when an Action assignment reevaluates the pose."""
    action, slot = _binding(rig)
    frame = context.scene.frame_current_final
    driven = {(c.data_path, c.array_index) for c in rig.animation_data.drivers if not c.mute}
    overrides = []
    for curve in _curves(action, slot):
        path, axis = curve.data_path, curve.array_index
        if curve.mute or (curve.group and curve.group.mute) or (path, axis) in driven:
            continue
        try:
            prop = rig.path_resolve(path)
        except ValueError:
            continue  # Unresolved paths do not evaluate on this character.
        scalar = isinstance(prop, (bool, float, int))
        if not scalar and not (hasattr(prop, '__len__') and 0 <= axis < len(prop)):
            continue
        value = prop if scalar else prop[axis]
        if not isinstance(value, (bool, int, float)) or math.isclose(
                value, curve.evaluate(frame), abs_tol=1.e-7, rel_tol=1.e-7):
            continue
        leaf = _LEAF.search(path) if scalar else None
        if scalar and not leaf:
            raise ValueError('Key the edited value before switching: ' + path)
        overrides.append((path, axis, value, leaf))
    return overrides


def _restore(rig, held):
    for path, axis, value, leaf in held:
        prop = rig.path_resolve(path)
        if leaf is None:
            if prop[axis] != value:
                prop[axis] = value
        elif prop != value:
            prefix = path[:leaf.start()]
            owner = rig.path_resolve(prefix) if prefix else rig
            if leaf[1] is not None:
                owner[json.loads('"' + leaf[1] + '"')] = value
            else:
                setattr(owner, leaf[2], value)


def _refresh(context, rig, held):
    context.view_layer.update()
    if held:
        _restore(rig, held)
        context.view_layer.update()


def _bind(context, rig, target, slot):
    previous, old_slot = _binding(rig)
    if (previous, old_slot) == (target, slot):
        return
    held = _held(context, rig) if previous else []
    try:
        assign_character_action(rig, target, slot)
        _refresh(context, rig, held)
    except Exception:
        if previous:
            assign_character_action(rig, previous, old_slot)
        else:
            rig.animation_data.action = None
        _refresh(context, rig, held)
        raise


def _signature(action):
    # Empty slots on copied layered Actions have no channelbag.
    if len(action.layers) != 1 or len(action.layers[0].strips) != 1:
        raise ValueError('The preview Action layers changed. Discard this comparison.')
    layer = action.layers[0]
    strip = layer.strips[0]
    if strip.type != 'KEYFRAME':
        raise ValueError('The preview Action strip changed. Discard this comparison.')
    return dict(curves=[[bag.slot_handle, curves.signature(action, bag.slot_handle)]
                        for bag in strip.channelbags],
                slots=[[slot.handle, slot.identifier] for slot in action.slots],
                markers=[[p.name, p.frame] for p in action.pose_markers],
                range=[action.use_frame_range, action.frame_start, action.frame_end, action.use_cyclic])


def _release(action, rig):
    if action.get(TEMP) != rig:
        return
    del action[TEMP]
    if not action.users:
        bpy.data.actions.remove(action, do_unlink=False)


def _make(context, rig, source, handle, names, position_mm, rotation_degrees):
    if (not math.isfinite(position_mm) or not math.isfinite(rotation_degrees)
            or min(position_mm, rotation_degrees) < 0):
        raise ValueError('Reduction tolerances must be finite and nonnegative.')
    units = context.scene.unit_settings.scale_length * max(map(abs, rig.matrix_world.to_scale()))
    if not math.isfinite(units) or units <= 0.:
        raise ValueError('The character needs a usable scene scale.')
    action = source.copy()
    try:
        action.name = '.Reduced Preview'
        if action.asset_data:
            action.asset_clear()
        action.use_fake_user = False
        # A comparison take has no catalog, export, pose-asset or recovery
        # identity of its own. Curves, slots, layers and groups stay native.
        for key in list(action.keys()):
            if key.startswith('character_designer_'):
                del action[key]
        action[TEMP] = rig
        result = curves.simplify(action, handle, names, position_mm * .001 / units,
                                 math.radians(rotation_degrees))
        scope = curves.selected_paths(source, handle, names)
        if not scope:
            raise ValueError('The selected controls have no animated transforms in this Action slot.')
        settings = dict(bone_names=list(names), position_mm=position_mm, rotation_degrees=rotation_degrees,
                        paths=list(result['paths']), before=result['before'], after=result['after'],
                        scope=scope, source_hash=curves.signature(source, handle, scope),
                        original_view_hash=_signature(source), preview_hash=_signature(action))
        return action, json.dumps(settings, ensure_ascii=False)
    except Exception:
        if not action.users:
            bpy.data.actions.remove(action, do_unlink=False)
        raise


def start(context, position_mm=2., rotation_degrees=1.):
    rig = _rig(context)
    if SESSION in rig:
        raise ValueError('Finish the open reduction preview first.')
    _ready(context, rig)
    source, handle = _binding(rig)
    _writable_source(rig, source, handle)
    names = sorted(bone.name for bone in (context.selected_pose_bones or ()))
    if not names:
        raise ValueError('Select the controls whose keys you want to simplify.')
    action, settings = _make(context, rig, source, handle, names, position_mm, rotation_degrees)
    try:
        rig[SESSION] = dict(source=source, preview=action, scene=context.scene, slot=handle, settings=settings)
        _bind(context, rig, action, handle)
    except Exception:
        if SESSION in rig:
            del rig[SESSION]
        _release(action, rig)
        raise
    return status(context)


def switch(context, view):
    rig, record = _record(context)
    _ready(context, rig)
    if _view(rig, record) == 'OTHER' or view not in {'ORIGINAL', 'REDUCED'}:
        raise ValueError('The active Action or slot changed. Discard this preview to start again.')
    if view == 'REDUCED' and _view(rig, record) != view:
        if _signature(record['source']) != json.loads(record['settings'])['original_view_hash']:
            raise ValueError('The original motion was edited. Update or restart the reduction preview.')
    _bind(context, rig, record['source' if view == 'ORIGINAL' else 'preview'], record['slot'])
    return status(context)


def _checked(context):
    rig, record = _record(context)
    _ready(context, rig)
    if _view(rig, record) == 'OTHER':
        raise ValueError('The active Action or slot changed. Discard this preview to start again.')
    source, preview, slot = record['source'], record['preview'], record['slot']
    data = json.loads(record['settings'])
    scope = curves.selected_paths(source, slot, data['bone_names'])
    if list(scope) != data['scope'] or curves.signature(source, slot, scope) != data['source_hash']:
        raise ValueError('The original controls were edited. Discard the preview and start from those edits.')
    if _signature(preview) != data['preview_hash']:
        raise ValueError('The temporary Action was edited. Keep a copy of those edits or discard the preview.')
    return rig, record, data


def regenerate(context, position_mm, rotation_degrees):
    rig, record, data = _checked(context)
    source, previous, slot = record['source'], record['preview'], record['slot']
    action, settings = _make(context, rig, source, slot, data['bone_names'], position_mm, rotation_degrees)
    try:
        _bind(context, rig, action, slot)
    except Exception:
        _release(action, rig)
        raise
    record['preview'], record['settings'] = action, settings
    _release(previous, rig)
    return status(context)


def discard(context):
    rig = _rig(context)
    record = rig.get(SESSION)
    if record is None or record.get('scene') != context.scene:
        raise ValueError('Return to the character and scene that own this preview.')
    source, preview, slot = record.get('source'), record.get('preview'), record['slot']
    active, _ = _binding(rig)
    restore_source = _has_slot(source, slot) and not (source.library or source.override_library)
    if restore_source and ((preview and active == preview) or (not preview and not active)):
        _ready(context, rig)
        _bind(context, rig, source, slot)
    if preview and preview.get(TEMP) == rig and not restore_source:
        # The user removed the baseline natively. Keep the only surviving
        # preview as an ordinary Action, including when it is not assigned.
        preview.use_fake_user = True
        preview.name = 'Recovered Key Preview'
    del rig[SESSION]
    if preview:
        _release(preview, rig)


def apply(context):
    rig, record, data = _checked(context)
    source, preview, slot = record['source'], record['preview'], record['slot']
    _writable_source(rig, source, slot)
    previous, _ = _binding(rig)
    held = _held(context, rig)
    _bind(context, rig, source, slot)
    try:
        if data['paths']:
            curves.copy_keys(preview, source, slot, data['paths'],
                             after_write=lambda: _refresh(context, rig, held))
    except Exception:
        # copy_keys has restored the original point columns. Reevaluate the
        # prior view with the pre-transaction overrides, not the failed pose.
        assign_character_action(rig, previous, slot)
        _refresh(context, rig, held)
        raise
    del rig[SESSION]
    _release(preview, rig)
