"""Compare native edits on a temporary Action against the original slot."""
import json
import math
import re

import bpy

from . import animation_preview_curves as curves
from . import natural_bind_mode
from .animation_cleanup import DEFAULTS, hide_unused_scale
from .animation_loop_timing import LOOP, is_loop
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
    if (action, handle) == (record.get('source'), record['slot']):
        return 'ORIGINAL'
    if (action, handle) == (record.get('preview'), record.get('preview_slot', record['slot'])):
        return 'REDUCED'
    return 'OTHER'


def status(context):
    rig = context.active_object
    record = rig.get(SESSION) if rig and rig.type == 'ARMATURE' else None
    if record is None:
        return None
    if record.get('kind') == 'FBX':
        return dict(view=_view(rig, record), kind='FBX', filename=record['filename'])
    data = json.loads(record['settings'])
    edit_only = data.get('edit_only', False)
    original, reduced = record.get('source'), record.get('preview')
    counts = {}
    if original is not None and reduced is not None:
        # Native edits may change channels outside the initial bone selection.
        bone_paths = None if edit_only else {
            f'pose.bones["{bpy.utils.escape_identifier(name)}"]' for name in data['bone_names']
        }
        try:
            counts = curves.key_counts(original, reduced, record['slot'], bone_paths)
        except (ValueError, RuntimeError, ReferenceError):
            pass  # A changed or missing channelbag makes comparison unavailable.
    return dict(view=_view(rig, record), edit_only=edit_only, skipped=data.get('skipped', {}), **counts, **{
        key: data[key] for key in ('bone_names', 'position_mm', 'rotation_degrees')})


def changed_cycle_ranges(context):
    """Check the key bounds Blender uses for Repeat Motion before Apply."""
    rig = context.active_object
    record = rig.get(SESSION) if rig and rig.type == 'ARMATURE' else None
    if not record or not record.get('source') or not record.get('preview'):
        return 0
    try:
        original = curves._indexed(record['source'], record['slot'])
        edited = curves._indexed(record['preview'], record['slot'])
    except (ValueError, RuntimeError, ReferenceError):
        return 0  # Apply already validates the slot and binding.
    count = 0
    for key, after in edited.items():
        before = original.get(key)
        if (before is None or len(before.keyframe_points) < 2 or len(after.keyframe_points) < 2
                or curves._repeat_mode(before) != 'REPEAT' or curves._repeat_mode(after) != 'REPEAT'):
            continue
        if any(not math.isclose(before.keyframe_points[i].co.x, after.keyframe_points[i].co.x,
                                rel_tol=0., abs_tol=.0001) for i in (0, -1)):
            count += 1
    return count


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
    for action, handle in ((record['source'], record['slot']),
                           (record['preview'], record.get('preview_slot', record['slot']))):
        if action.library or action.override_library or not _has_slot(action, handle):
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


def _initial_inputs(rig):
    # Blender leaves the last animated value on an input when the next Action
    # has no curve for it. Keep the real starting values, including non-unit scales.
    result = {}
    for owner in (rig, *rig.pose.bones):
        prefix = '' if owner is rig else owner.path_from_id()
        for prop in ('location', 'rotation_quaternion', 'rotation_euler', 'rotation_axis_angle', 'scale'):
            result[owner.path_from_id(prop)] = list(getattr(owner, prop))
        for key in owner.keys():
            value = owner[key]
            if isinstance(value, (float, int)):
                values = [value]
            elif getattr(value, 'typecode', '') in ('f', 'd', 'i'):
                values = list(value)
            else:
                continue
            result[prefix + '["' + bpy.utils.escape_identifier(key) + '"]'] = values
    return json.dumps(result, separators=(',', ':'))


def _channels(action, slot):
    return {(fc.data_path, fc.array_index) for fc in _curves(action, slot)
            if not fc.mute and not (fc.group and fc.group.mute) and not fc.is_empty}


def _unkeyed_inputs(rig, record, side, keys, *, capture=False):
    if not keys:
        return []
    driven = {(fc.data_path, fc.array_index) for fc in rig.animation_data.drivers if not fc.mute}
    name = side + '_unkeyed'
    saved = json.loads(record.get(name, '{}'))
    initial = {} if capture else json.loads(record.get('initial_inputs', '{}'))
    held = []
    for path, axis in keys - driven:
        try:
            prop = rig.path_resolve(path)
            scalar = isinstance(prop, (int, float))
            value = prop if scalar else prop[axis]
        except (ValueError, TypeError, IndexError, KeyError):
            continue
        if not isinstance(value, (int, float)):
            continue
        if capture:
            saved.setdefault(path, {})[str(axis)] = value
            continue
        fallback = initial.get(path, ())
        expected = saved.get(path, {}).get(str(axis), fallback[axis] if 0 <= axis < len(fallback) else None)
        leaf = _LEAF.search(path) if scalar else None
        if expected is not None and (not scalar or leaf):
            held.append((path, axis, expected, leaf))
    if capture:
        record[name] = json.dumps(saved)
    return held


def _bind(context, rig, target, slot):
    previous, old_slot = _binding(rig)
    if (previous, old_slot) == (target, slot):
        return
    held = _held(context, rig) if previous else []
    target_values = []
    record = rig.get(SESSION)
    if previous and record is not None and previous in (record.get('source'), record.get('preview')):
        old_keys, new_keys = _channels(previous, old_slot), _channels(target, slot)
        old_side = 'original' if previous == record['source'] else 'reduced'
        new_side = 'reduced' if target == record.get('preview') else 'original'
        _unkeyed_inputs(rig, record, old_side, new_keys - old_keys, capture=True)
        target_values = _unkeyed_inputs(rig, record, new_side, old_keys - new_keys)
    try:
        assign_character_action(rig, target, slot)
        _refresh(context, rig, held + target_values)
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
    result = dict(curves=[[bag.slot_handle, curves.signature(action, bag.slot_handle)]
                        for bag in strip.channelbags],
                slots=[[slot.handle, slot.identifier] for slot in action.slots],
                markers=[[p.name, p.frame] for p in action.pose_markers],
                range=[action.use_frame_range, action.frame_start, action.frame_end, action.use_cyclic])
    if DEFAULTS in action:
        result['clean_defaults'] = action[DEFAULTS]
    return result


def _release(action, rig):
    if action.get(TEMP) != rig:
        return
    del action[TEMP]
    if not action.users:
        bpy.data.actions.remove(action, do_unlink=False)


def _loop(context, rig, action, handle):
    if action.use_cyclic or is_loop(action):
        return True
    if LOOP in action:
        return False
    # Older prepared and alternate takes only recorded loop intent on the imported clip.
    return any(record.target == rig and (action, handle) in (
                   (record.controller_action, record.controller_slot), (record.alternate_action, record.alternate_slot))
               and is_loop(record.source_action)
               for record in getattr(context.scene, 'character_designer_controller_motions', ()))


def _make(context, rig, source, handle, names, position_mm, rotation_degrees):
    edit_only = position_mm is None and rotation_degrees is None
    if not edit_only:
        position_mm = 2. if position_mm is None else position_mm
        rotation_degrees = 1. if rotation_degrees is None else rotation_degrees
        if (not math.isfinite(position_mm) or not math.isfinite(rotation_degrees)
                or min(position_mm, rotation_degrees) < 0):
            raise ValueError('Reduction tolerances must be finite and nonnegative.')
        units = context.scene.unit_settings.scale_length * max(map(abs, rig.matrix_world.to_scale()))
        if not math.isfinite(units) or units <= 0.:
            raise ValueError('The character needs a usable scene scale.')
    action = source.copy()
    try:
        action.name = '.Edited Preview' if edit_only else '.Reduced Preview'
        if action.asset_data:
            action.asset_clear()
        action.use_fake_user = False
        # A comparison take has no catalog, export, pose-asset or recovery
        # identity of its own. Curves, slots, layers and groups stay native.
        for key in list(action.keys()):
            if key.startswith('character_designer_') and key != DEFAULTS:
                del action[key]
        action[TEMP] = rig
        loop = _loop(context, rig, source, handle)
        result = dict(paths=[], skipped={})
        if not edit_only:
            result = curves.simplify(action, handle, names, position_mm * .001 / units,
                                     math.radians(rotation_degrees), loop=loop)
            hide_unused_scale(rig, action, handle, bone_names=names)
        scope = curves.selected_paths(source, handle, names)
        if not edit_only and not scope:
            raise ValueError('The selected controls have no animated transforms in this Action slot.')
        settings = dict(bone_names=list(names), edit_only=edit_only,
                        position_mm=position_mm, rotation_degrees=rotation_degrees,
                        paths=list(result['paths']),
                        skipped=result['skipped'], loop=loop,
                        scope=scope, source_hash=curves.signature(source, handle, scope),
                        original_view_hash=_signature(source), preview_hash=_signature(action))
        return action, json.dumps(settings, ensure_ascii=False)
    except Exception:
        if not action.users:
            bpy.data.actions.remove(action, do_unlink=False)
        raise


def start(context, position_mm=None, rotation_degrees=None):
    rig = _rig(context)
    if SESSION in rig:
        raise ValueError('Finish the open reduction preview first.')
    _ready(context, rig)
    source, handle = _binding(rig)
    _writable_source(rig, source, handle)
    names = sorted(bone.name for bone in (context.selected_pose_bones or ()))
    if not names and (position_mm is not None or rotation_degrees is not None):
        raise ValueError('Select the controls whose keys you want to simplify.')
    action, settings = _make(context, rig, source, handle, names, position_mm, rotation_degrees)
    try:
        rig[SESSION] = dict(source=source, preview=action, scene=context.scene, slot=handle,
                            settings=settings, initial_inputs=_initial_inputs(rig))
        _bind(context, rig, action, handle)
    except Exception:
        if SESSION in rig:
            del rig[SESSION]
        _release(action, rig)
        raise
    return status(context)


def _version_motion(context, rig, action, slot):
    rows = [row for row in context.scene.character_designer_controller_motions
            if row.target == rig and (action, slot) in (
                (row.controller_action, row.controller_slot), (row.alternate_action, row.alternate_slot))]
    if rows and (len(rows) != 1 or rows[0].controller_action != action or rows[0].controller_slot != slot):
        raise ValueError('Open the main working animation for this clip before comparing versions.')
    return rows[0] if rows else None


def start_version(context, filepath, skip_reference=True):
    from pathlib import Path
    from . import animation_playback_range as playback, animation_version_fbx, unity_animation
    from .animation_export_queue import _idle as export_idle

    rig = _rig(context)
    if SESSION in rig:
        raise ValueError('Finish the open comparison first.')
    export_idle(context, rig)
    _ready(context, rig)
    original, handle = _binding(rig)
    _writable_source(rig, original, handle)
    motion = _version_motion(context, rig, original, handle)
    signature = _signature(original)
    loop = _loop(context, rig, original, handle)
    packet = animation_version_fbx.read(context, rig, filepath, skip_reference)
    action, slot = animation_version_fbx.bake(context, rig, packet, float(original.frame_range[0]), loop)
    try:
        action.name = '.Incoming Version'
        action[TEMP] = rig
        scene = context.scene
        rig[SESSION] = dict(source=original, preview=action, scene=scene, slot=handle,
                            preview_slot=slot, kind='FBX', filename=Path(filepath).name,
                            settings=json.dumps(dict(original_view_hash=signature, loop=loop)),
                            clip_key=motion.clip_key if motion else '',
                            initial_inputs=_initial_inputs(rig),
                            range=[scene.frame_start, scene.frame_end, scene.frame_preview_start,
                                   scene.frame_preview_end, scene.use_preview_range])
        unity_animation._set_playing(context, False)
        _bind(context, rig, action, slot)
        playback.set_ranges(scene, min(original.frame_range[0], action.frame_range[0]),
                            max(original.frame_range[1], action.frame_range[1]), False)
    except Exception:
        if SESSION in rig:
            discard(context)
        else:
            _release(action, rig)
        raise


def switch(context, view):
    rig, record = _record(context)
    _ready(context, rig)
    if _view(rig, record) == 'OTHER' or view not in {'ORIGINAL', 'REDUCED'}:
        raise ValueError('The active Action or slot changed. Discard this preview to start again.')
    if view == 'REDUCED' and _view(rig, record) != view:
        if _signature(record['source']) != json.loads(record['settings'])['original_view_hash']:
            raise ValueError('The original motion was edited. Restart the comparison from those edits.')
    preview = view == 'REDUCED'
    handle = record.get('preview_slot', record['slot']) if preview else record['slot']
    _bind(context, rig, record['preview' if preview else 'source'], handle)
    return status(context)


def _checked(context):
    rig, record = _record(context)
    _ready(context, rig)
    if _view(rig, record) == 'OTHER':
        raise ValueError('The active Action or slot changed. Discard this preview to start again.')
    if record.get('kind') == 'FBX':
        raise ValueError('Finish comparing versions before editing keys.')
    source, preview, slot = record['source'], record['preview'], record['slot']
    data = json.loads(record['settings'])
    if 'loop' in data and data['loop'] != _loop(context, rig, source, slot):
        raise ValueError('The loop setting changed. Discard this preview and start again.')
    scope = curves.selected_paths(source, slot, data['bone_names'])
    if list(scope) != data['scope'] or curves.signature(source, slot, scope) != data['source_hash']:
        raise ValueError('The original controls were edited. Discard the preview and start from those edits.')
    if source.get(DEFAULTS) != data['original_view_hash'].get('clean_defaults'):
        raise ValueError('The original clip defaults changed. Discard this preview and start again.')
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
    if 'reduced_unkeyed' in record:
        del record['reduced_unkeyed']
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
    if record.get('kind') == 'FBX':
        scene = context.scene
        first, last, preview_first, preview_last, use_preview = record['range']
        scene.frame_end, scene.frame_start, scene.frame_end = max(scene.frame_end, last), first, last
        scene.frame_preview_end, scene.frame_preview_start, scene.frame_preview_end = (
            max(scene.frame_preview_end, preview_last), preview_first, preview_last)
        scene.use_preview_range = bool(use_preview)
    del rig[SESSION]
    if preview:
        _release(preview, rig)


def _apply_version(context, rig, record):
    from . import animation_action_browser as browser, animation_playback_range as playback
    from .animation_loop_timing import set_loop

    _ready(context, rig)
    if _view(rig, record) != 'REDUCED':
        raise ValueError('Switch to Incoming before applying the version.')
    source, incoming, slot = record['source'], record['preview'], record['slot']
    _writable_source(rig, source, slot)
    data = json.loads(record['settings'])
    if _signature(source) != data['original_view_hash']:
        raise ValueError('The original motion changed during the comparison.')
    motion = _version_motion(context, rig, source, slot)
    if (motion.clip_key if motion else '') != record['clip_key']:
        raise ValueError('The working motion changed during comparison.')
    name, handle = source.name, record['preview_slot']
    label, clip_id = source.get(browser.LABEL_KEY) or name, source.get(browser.CLIP_KEY)
    classify = bool(clip_id and source.get(browser.ROLE_KEY) == 'WORK' and len(source.slots) == 1)
    if classify:
        browser.classify_action(incoming, rig, role='WORK', label=label, clip_id=clip_id)
        browser.classify_action(source, rig, role='ARCHIVED', label=label, clip_id=clip_id)
    set_loop(incoming, data['loop'])
    incoming['character_designer_controller_representation'] = 'FK'
    source.use_fake_user = incoming.use_fake_user = True
    if len(source.slots) == 1:
        source.name = name + ' · Previous'
    incoming.name = name
    if motion:
        motion.controller_action, motion.controller_slot = incoming, handle
    for scene in bpy.data.scenes:
        for pin in scene.character_designer_local_motion_pins:
            if (pin.rig, pin.action, pin.slot_handle) == (rig, source, slot):
                pin.action, pin.slot_handle = incoming, handle
        for route in scene.character_designer_animation_export_routes:
            if (route.rig, route.action, route.slot) == (rig, source, slot):
                route.action, route.slot = incoming, handle
    playback.set_ranges(context.scene, *incoming.frame_range, data['loop'])
    del incoming[TEMP]
    del rig[SESSION]


def apply(context):
    rig, record = _record(context)
    if record.get('kind') == 'FBX':
        return _apply_version(context, rig, record)
    rig, record, data = _checked(context)
    source, preview, slot = record['source'], record['preview'], record['slot']
    _writable_source(rig, source, slot)
    current = _signature(preview)
    initial = data['preview_hash']
    edited = current != initial
    if edited:
        # Older saved previews already contain these whole-slot signatures.
        # Only the current character slot is ours to publish.
        def other_data(state):
            return dict(state, curves=[entry for entry in state['curves'] if entry[0] != slot])
        if other_data(current) != other_data(initial):
            raise ValueError('Apply Reduction copies curves in the current Action slot. Other slots or clip settings were also edited.')
        if curves.signature(source, slot) != dict(data['original_view_hash']['curves']).get(slot):
            raise ValueError('The original Action slot was edited during the preview. Your Reduced edits are still available.')
    previous, _ = _binding(rig)
    held = _held(context, rig)
    missing = _channels(source, slot) - _channels(preview, slot) if edited else set()
    if previous == preview:
        _unkeyed_inputs(rig, record, 'reduced', missing, capture=True)
    committed_pose = held + _unkeyed_inputs(rig, record, 'reduced', missing)
    _bind(context, rig, source, slot)
    try:
        if edited:
            curves.copy_edited_slot(preview, source, slot, after_write=lambda: _refresh(context, rig, committed_pose))
        elif data['paths']:
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
