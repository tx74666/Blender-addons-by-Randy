"""Keep an unkeyed Original Bones reset local to its current frame and Action."""

import json

import bpy
from bpy.app.handlers import persistent

from . import control_pose_assets, natural_bind_graph as graph, natural_bind_keys as keys


KEY = 'character_designer_unkeyed_limb_bind_v1'
_sessions = {}
_keyed = {}
_known_actions = {}
_applied = {}
_route_inputs = {}
_pose_inputs = {}
_preview_inputs = {}
_operators = {}
_loaded_inputs = set()
_expiry_deferrals = {}
_bus_owner = object()
_busy = False
_fields = control_pose_assets._FIELDS


class ExpiryCommitError(RuntimeError):
    """The new binding committed; expiry needs recovery, not stale-graph rollback."""

    committed = True


class _ExpiryDeferral:
    """Own one synchronous, target-local provisional binding transition."""

    def __init__(self, rig):
        self._rig = rig
        self._uid = None
        self._entered = False
        self._closed = False
        self._committed = False

    def __enter__(self):
        if self._entered or self._closed:
            raise RuntimeError('A bind expiry deferral cannot be reused.')
        uid = self._rig.session_uid
        stack = _expiry_deferrals.setdefault(uid, [])
        if stack and stack[-1]._rig != self._rig:
            raise RuntimeError('The bind expiry deferral belongs to a different rig.')
        self._uid = uid
        self._entered = True
        stack.append(self)
        return self

    def commit(self):
        """Mark success after provisional writes; the outermost exit expires."""
        if not self._entered or self._closed:
            raise RuntimeError('The bind expiry deferral is not active.')
        if _expiry_deferrals.get(self._uid, [None])[-1] is not self:
            raise RuntimeError('Bind expiry deferrals must commit in nesting order.')
        self._committed = True

    def __exit__(self, exc_type, exc_value, traceback):
        stack = _expiry_deferrals.get(self._uid)
        if not self._entered or self._closed or not stack or stack[-1] is not self:
            raise RuntimeError('Bind expiry deferrals must close in nesting order.')
        stack.pop()
        self._closed = True
        if stack:
            return False  # Only the outer owner decides whether to commit.
        del _expiry_deferrals[self._uid]
        if exc_type is None and self._committed:
            try:
                if _busy:
                    raise RuntimeError('Another bind expiry is still running.')
                _expire(_only_uid=self._uid)
            except Exception as exc:
                raise ExpiryCommitError(
                    'The controller Action was committed, but its bind transition '
                    'needs recovery: ' + str(exc)) from exc
        return False


def defer_expiry(rig):
    """Defer this rig's expiry while a caller activates or rolls back a binding.

    Restore the original binding/frame inside the scope on failure. Call
    ``commit()`` last on success. A successful outer exit then performs ordinary
    target-only expiry; subsequent native refresh is beyond the rollback boundary.
    This guard never snapshots, restores or manufactures native graph ownership.
    """
    return _ExpiryDeferral(rig)


def _values(p, field):
    value = getattr(p, field)
    return (value,) if _fields[field] == 1 else tuple(value)


def _assign(p, field, axis, value):
    if _fields[field] == 1:
        setattr(p, field, value)
    else:
        getattr(p, field)[axis] = value


def _release_inputs(uid, rig):
    for (name, field, axis), (old, applied) in _pose_inputs.pop(uid, {}).items():
        p = rig.pose.bones.get(name)
        if p is not None and _values(p, field)[axis] == applied:
            _assign(p, field, axis, old)


def _editing_original(rig):
    from . import body_original_mode
    return body_original_mode.SESSION in rig


def _holding_current_inputs(uid):
    return any(getattr(scope, 'hold_current_inputs', False)
               for scope in _expiry_deferrals.get(uid, ()))


def keep_restored_inputs(scene, rig, before_restore, restored):
    """Recognize only offsets undone while preserving this frame's displayed pose."""
    uid = rig.session_uid
    route = _route_inputs.get(uid, ())
    if (_keyed.get(uid) != (scene, rig) or uid in _sessions or not _applied.get(uid)
            or route[:2] != (_frame(scene), _binding(rig))):
        return
    receipts = _pose_inputs.get(uid, {})
    for key, (base, applied) in receipts.items():
        name, field, axis = key
        if name not in restored or field not in restored[name] or applied == base:
            continue
        # A manual edit may equal the native pre-offset value. The next R
        # event should keep that edit, just as it keeps any other manual value.
        if (before_restore[name][field][axis] == applied and restored[name][field][axis] == base
                and _values(rig.pose.bones[name], field)[axis] == base):
            receipts[key] = (base, base)


def _apply_inputs(scene, depsgraph=None):
    """Follow native Action evaluation without writing extra keys to an existing take."""
    if _busy:
        return
    for uid, (owner, rig) in tuple(_sessions.items()):
        if owner != scene or KEY not in rig or _holding_current_inputs(uid) or _editing_original(rig):
            continue
        record = rig[KEY]
        if not _match(scene, rig, record):
            continue
        for name, field, axis, old, new in _preview_inputs[uid]:
            p = rig.pose.bones.get(name)
            if p and _values(p, field)[axis] == old:
                _assign(p, field, axis, new)
    for uid, (owner, rig) in tuple(_keyed.items()):
        if owner != scene or uid in _sessions or not _applied.get(uid) or _holding_current_inputs(uid) or _editing_original(rig):
            continue
        route = _route_inputs.get(uid)
        if not route or route[:2] != (_frame(scene), _binding(rig)):
            continue
        applied = _pose_inputs.setdefault(uid, {})
        for key, (weight, original, adjusted) in route[2].items():
            name, field, axis = key
            p = rig.pose.bones.get(name)
            if p is None:
                continue
            current = _values(p, field)[axis]
            prior = applied.get(key)
            if prior and (current == prior[1] or current != prior[0]):
                continue  # Includes edits the artist made on the displayed pose.
            if prior is None and weight == 1. and current == adjusted:
                # A fresh Action or a reopened key can already contain the
                # applied local on an Original Bone without a native F-Curve.
                applied[key] = (original, current)
                continue
            new = current + adjusted - original
            if current != new:
                _assign(p, field, axis, new)
                applied[key] = (current, _values(p, field)[axis])


def _binding(rig):
    ad = rig.animation_data
    action = ad.action if ad else None
    return (action.session_uid if action else 0, ad.action_slot_handle if action else 0)


def _frame(scene):
    return (scene.frame_current, scene.frame_subframe)


def _action(rig):
    ad = rig.animation_data
    return ad.action if ad else None


def _animated_nla(rig):
    ad = rig.animation_data
    return bool(ad and ad.use_nla and any(not t.mute and t.strips for t in ad.nla_tracks))


def capture(context, rig):
    """Take the input witness before the first shortcut in a three-channel clear."""
    rec = rig.get(graph.KEY)
    return {
        'scene': context.scene.session_uid, 'rig': rig.session_uid,
        'frame': _frame(context.scene), 'binding': _binding(rig),
        'prior_graph': rec['token'] if rec else '', 'prior_enabled': bool(rec and rec['enabled']),
        'prior_offsets': json.dumps(graph.snapshot_offsets(rig), separators=(',', ':'))
            if rec and rec['enabled'] and KEY not in rig and keys.KEY not in rig else '',
        'inputs': {p.name: {f: _values(p, f) for f in _fields} for p in rig.pose.bones},
    }


def _same_input(scene, rig, record):
    return (record['scene'] == scene.session_uid and tuple(record['frame']) == _frame(scene)
            and tuple(record['binding']) == _binding(rig))


def _recent_operators():
    wm = bpy.context.window_manager
    return tuple(wm.operators) if wm else ()


def note_rotation_keys(context, rig, names):
    """Retain the current solved bind when Auto Key explicitly keyed wrist rotations."""
    record = rig.get(KEY)
    if record is None or not record.get('action') or not _match(context.scene, rig, record):
        return
    paths = set()
    for name in names:
        p = rig.pose.bones[name]
        field = ('rotation_quaternion' if p.rotation_mode == 'QUATERNION' else
                 'rotation_axis_angle' if p.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        paths.add(p.path_from_id(field))
    if keys._keys(record['action'], record['slot'], sum(record['frame']), paths):
        _capture_native_edits(rig, record)
        record['rotation_keys'] = json.dumps(sorted(paths | set(json.loads(record.get('rotation_keys', '[]')))))


def _observe_key_operator(rig, record):
    if not record.get('action') or _action(rig) != record.get('action') or bpy.context.object != rig:
        return
    current = _recent_operators()
    previous = _operators.get(rig.session_uid, set())
    if record.get('body_rotation'):
        cleared = {o.bl_idname for o in current if o.as_pointer() not in previous}
        fields = [field for op, field in (('POSE_OT_loc_clear', 'location'), ('POSE_OT_scale_clear', 'scale'))
                  if op in cleared]
        if fields:
            hold_control_channels(bpy.context, rig, bpy.context.selected_pose_bones or (), fields)
    if any(o.as_pointer() not in previous and o.bl_idname in
           {'ANIM_OT_keyframe_insert', 'ANIM_OT_keyframe_insert_by_name'} for o in current):
        record['explicit_key'] = True
        if 'rotation_keys' in record:
            del record['rotation_keys']
        selection = bpy.context.selected_pose_bones
        if selection is not None:
            record['key_selection'] = json.dumps([p.name for p in selection])
        _capture_native_edits(rig, record)
    _operators[rig.session_uid] = {o.as_pointer() for o in current}


def hold_control_channels(context, rig, controls, fields):
    """Remember native unkeyed control edits across same-frame Action reevaluation."""
    action = _action(rig)
    record = rig.get(KEY)
    if not action or record is None or not _match(context.scene, rig, record):
        return
    record['body_rotation'] = True
    paths = {p.path_from_id(field): (p, field) for p in controls for field in fields}
    rows = {tuple(row[:3]): row for row in _preview_inputs[rig.session_uid]}
    frame = sum(record['frame'])
    for curve in keys._curves(action, record['slot']):
        item = paths.get(curve.data_path)
        if item is None or curve.mute or curve.group and curve.group.mute:
            continue
        bone, field = item
        index = curve.array_index
        source = curve.evaluate(frame)
        value = _values(bone, field)[index]
        key = (bone.name, field, index)
        if source != value:
            rows[key] = [*key, source, value]
        else:
            rows.pop(key, None)
    values = list(rows.values())
    _preview_inputs[rig.session_uid] = values
    record['changes'] = json.dumps(values, separators=(',', ':'))


def _match(scene, rig, record):
    action = _action(rig)
    if scene != record['scene'] or action != record.get('action'):
        return False
    if action:
        return (record['slot'] == rig.animation_data.action_slot_handle
                and tuple(record['frame']) == _frame(scene))
    return not _animated_nla(rig)


def _changes(rig, inputs, previous):
    changes = {(name, field, axis): (old, new) for name, field, axis, old, new in previous}
    for name, fields in inputs.items():
        p = rig.pose.bones[name]
        for field, before in fields.items():
            for axis, (old, new) in enumerate(zip(before, _values(p, field))):
                if old == new:
                    continue
                key = (name, field, axis)
                if key in changes and changes[key][1] == old:
                    old = changes[key][0]
                if old == new:
                    changes.pop(key, None)
                else:
                    changes[key] = (old, new)
    return [[*key, *values] for key, values in changes.items()]


def _capture_native_edits(rig, record):
    # An artist may pose an Original Bone between the reset and native I,
    # including an axis on which the reset itself had no work to do.
    if 'native_before' in record:
        old_rows = _preview_inputs[rig.session_uid]
        rows = _changes(rig, json.loads(record['native_before']), old_rows)
        if rows != old_rows:
            _preview_inputs[rig.session_uid] = rows
            record['changes'] = json.dumps(rows, separators=(',', ':'))


def begin_preview(context, rig, previous):
    """Attach the already-verified graph to the current, unkeyed pose."""
    if previous['rig'] != rig.session_uid or not _same_input(context.scene, rig, previous):
        raise ValueError('The rig, frame or active Action changed during the clear.')
    rec = graph.validate(rig)
    if not rec['enabled']:
        raise ValueError('The bind-relative solver must be active before starting the preview.')
    if previous['prior_graph'] and previous['prior_graph'] != rec['token']:
        raise ValueError('The bind-relative solver changed during the clear.')
    prior = rig.get(KEY)
    if prior and (prior['token'] != rec['token'] or not _match(context.scene, rig, prior)):
        raise ValueError('A different unkeyed bind preview is still active.')
    rows = _changes(rig, previous['inputs'], json.loads(prior['changes']) if prior else ())
    record = {field: previous[field] for field in ('prior_graph', 'prior_enabled')}
    record['prior_offsets'] = previous.get('prior_offsets', '')
    if prior:
        record.update(prior_graph=prior['prior_graph'], prior_enabled=prior['prior_enabled'])
        record['prior_offsets'] = prior.get('prior_offsets', '')
    record.update(token=rec['token'], changes=json.dumps(rows, separators=(',', ':')),
                  scene=context.scene, frame=list(map(float, previous['frame'])), slot=previous['binding'][1])
    record['native_before'] = (prior['native_before'] if prior and 'native_before' in prior else
        json.dumps({name: {f: previous['inputs'][name][f] for f in control_pose_assets._TRANSFORMS}
                    for name in json.loads(rec['native'])}, separators=(',', ':')))
    if _action(rig):
        record['action'] = _action(rig)
    selection = [p.name for p in context.selected_pose_bones or ()]
    record['key_witness'] = prior['key_witness'] if prior else keys.capture(
        rig, _action(rig), record['slot'], sum(record['frame']), selection, rows)
    if prior:
        witness = json.loads(record['key_witness'])
        if 'selection' not in witness:
            witness['selection'] = selection
            record['key_witness'] = json.dumps(witness, separators=(',', ':'))
    old_record = prior.to_dict() if prior else None
    old_session = _sessions.get(rig.session_uid)
    try:
        register()
        rig[KEY] = record
        _sessions[rig.session_uid] = (context.scene, rig)
        _preview_inputs[rig.session_uid] = rows
        _operators.setdefault(rig.session_uid, {o.as_pointer() for o in _recent_operators()})
        if not _action(rig):
            _known_actions.setdefault(rig.session_uid, {a.session_uid for a in bpy.data.actions})
        _watch()
    except BaseException:
        if old_record:
            rig[KEY] = old_record
        elif KEY in rig:
            del rig[KEY]
        if old_session:
            _sessions[rig.session_uid] = old_session
            _preview_inputs[rig.session_uid] = json.loads(old_record['changes'])
        else:
            _sessions.pop(rig.session_uid, None)
            _preview_inputs.pop(rig.session_uid, None)
            _known_actions.pop(rig.session_uid, None)
            _operators.pop(rig.session_uid, None)
        if not _sessions and not _keyed:
            _unwatch()
        raise


def _end(scene, rig, record, *, allow_keying):
    rec = graph.validate(rig)
    if record['token'] != rec['token']:
        raise ValueError('The unkeyed bind preview no longer owns the limb graph.')
    action, slot = record.get('action'), record['slot']
    fresh = False
    if not action and _action(rig):
        action = _action(rig)
        fresh = action.session_uid not in _known_actions.get(rig.session_uid, ())
        if not fresh:
            action = None
        else:
            slot = rig.animation_data.action_slot_handle
    graph.freeze_preview(rig, rec)
    if allow_keying and record.get('action') == _action(rig):
        _capture_native_edits(rig, record)
    recorded = allow_keying and keys.remember(rig, scene, record, action, slot, fresh)
    with bpy.context.temp_override(scene=scene):
        if keys.KEY in rig:
            _keyed[rig.session_uid] = (scene, rig)
            _applied.pop(rig.session_uid, None)
            _route_inputs.pop(rig.session_uid, None)
        elif record['prior_graph']:
            if record.get('prior_offsets'):
                graph.restore_offsets(bpy.context, rig, json.loads(record['prior_offsets']))
            else:
                graph.restore_prior_solver(rec, 'DIRECT')
            graph.set_enabled(bpy.context, rig, record['prior_enabled'], evaluate=False)
        else:
            graph.remove(bpy.context, rig, evaluate=False)
    if not (recorded and fresh):
        for name, field, axis, old, new in json.loads(record['changes']):
            p = rig.pose.bones.get(name)
            if p and _values(p, field)[axis] == new:
                _assign(p, field, axis, old)
    del rig[KEY]


def _expire(*args, all_sessions=False, _only_uid=None):
    global _busy
    if _busy:
        return
    _busy = True
    try:
        for uid, (scene, rig) in tuple(_sessions.items()):
            if uid in _expiry_deferrals or (_only_uid is not None and uid != _only_uid) or (_alive(rig) and _editing_original(rig)):
                continue
            try:
                if KEY in rig:
                    record = rig[KEY]
                    _observe_key_operator(rig, record)
                    if not all_sessions and _match(scene, rig, record):
                        continue
                    _end(scene, rig, record, allow_keying=not all_sessions)
            except ReferenceError:
                pass  # Blender may have removed an object from another window.
            finally:
                if all_sessions or not _alive(rig):
                    _sessions.pop(uid, None)
                    _preview_inputs.pop(uid, None)
                    _known_actions.pop(uid, None)
                    _operators.pop(uid, None)
        for uid, (scene, rig) in tuple(_keyed.items()):
            if uid in _expiry_deferrals or (_only_uid is not None and uid != _only_uid) or (_alive(rig) and _editing_original(rig)):
                continue
            try:
                if keys.KEY not in rig:
                    _release_inputs(uid, rig)
                    _keyed.pop(uid, None)
                    _applied.pop(uid, None)
                    _route_inputs.pop(uid, None)
                    continue
                if uid in _sessions:
                    continue
                route_input = (_frame(scene), _binding(rig))
                if _route_inputs.get(uid, ())[:2] == route_input:
                    continue
                _release_inputs(uid, rig)
                target = keys.desired(rig, scene)
                if uid not in _applied or _applied[uid] != target:
                    with bpy.context.temp_override(scene=scene):
                        keys.route(bpy.context, rig, target)
                    _applied[uid] = target
                _route_inputs[uid] = (*route_input, keys.input_values(rig, target))
            except ReferenceError:
                _keyed.pop(uid, None)
                _applied.pop(uid, None)
                _route_inputs.pop(uid, None)
                _pose_inputs.pop(uid, None)
        if not _sessions and not _keyed:
            _unwatch()
    finally:
        _busy = False


def _alive(rig):
    try:
        return KEY in rig
    except ReferenceError:
        return False


def _save(*args):
    # Blender evaluates the old Action again when reading a .blend. Remember
    # the actual unkeyed axes, including the user's edits following the clear.
    for scene, rig in tuple(_sessions.values()):
        record = rig[KEY]
        _observe_key_operator(rig, record)
        if not _match(scene, rig, record):
            continue
        _capture_native_edits(rig, record)
        rows = json.loads(record['changes'])
        values = [_values(rig.pose.bones[name], field)[axis] for name, field, axis, _, _ in rows]
        record['saved_values'] = json.dumps(values, separators=(',', ':'))


def _unwatch():
    bpy.msgbus.clear_by_owner(_bus_owner)
    for hooks, callback in _temporary():
        if callback in hooks:
            hooks.remove(callback)


def _temporary():
    handlers = bpy.app.handlers
    return ((handlers.frame_change_pre, _expire), (handlers.depsgraph_update_pre, _expire),
            (handlers.frame_change_post, _apply_inputs), (handlers.depsgraph_update_post, _apply_inputs),
            (handlers.save_pre, _save))


def _watch():
    if _expire in bpy.app.handlers.frame_change_pre:
        return
    for hooks, callback in _temporary():
        hooks.append(callback)
    for key in ('action', 'action_slot_handle'):
        bpy.msgbus.subscribe_rna(key=(bpy.types.AnimData, key), owner=_bus_owner, args=(), notify=_expire)


@persistent
def _invalidate(*args):
    _sessions.clear()
    _keyed.clear()
    _known_actions.clear()
    _applied.clear()
    _route_inputs.clear()
    _pose_inputs.clear()
    _preview_inputs.clear()
    _operators.clear()
    _loaded_inputs.clear()
    if bpy.app.timers.is_registered(_after_read):
        bpy.app.timers.unregister(_after_read)
    _unwatch()


@persistent
def _recover(*args):
    # Undo and redo replace RNA wrappers. Only an actual restored marker resumes.
    _invalidate()
    for rig in bpy.data.objects:
        for key, sessions in ((KEY, _sessions), (keys.KEY, _keyed)):
            if key not in rig:
                continue
            record = rig[key]
            scene = record.get('scene')
            if scene and scene in tuple(rig.users_scene) and graph.validate(rig)['token'] == record['token']:
                sessions[rig.session_uid] = (scene, rig)
                if key == KEY:
                    _preview_inputs[rig.session_uid] = json.loads(record['changes'])
                    _operators[rig.session_uid] = {o.as_pointer() for o in _recent_operators()}
                if key == KEY and not record.get('action'):
                    _known_actions[rig.session_uid] = {a.session_uid for a in bpy.data.actions}
    if _sessions or _keyed:
        _watch()
        _expire()


@persistent
def _load(*args):
    _recover()
    _loaded_inputs.update(uid for uid, (_, rig) in _sessions.items() if 'saved_values' in rig[KEY])
    _loaded_inputs.update(_keyed)
    if _loaded_inputs and not bpy.app.timers.is_registered(_after_read):
        bpy.app.timers.register(_after_read, first_interval=0.0)


def _after_read():
    # Opening a .blend evaluates the saved Action after load_post. Apply the
    # current pose after this initial evaluation too, even before the first scrub.
    keyed_scenes = set()
    for uid in tuple(_loaded_inputs):
        session = _sessions.get(uid)
        _loaded_inputs.remove(uid)
        if not session:
            keyed = _keyed.get(uid)
            if keyed:
                keyed_scenes.add(keyed[0])
                _route_inputs.pop(uid, None)
            continue
        scene, rig = session
        record = rig[KEY]
        if 'saved_values' not in record or not _match(scene, rig, record):
            continue
        rows = json.loads(record['changes'])
        values = json.loads(record['saved_values'])
        if len(rows) != len(values):
            raise ValueError('The saved neutral-pose inputs are incomplete.')
        for (name, field, axis, _, _), value in zip(rows, values):
            p = rig.pose.bones[name]
            if _values(p, field)[axis] != value:
                _assign(p, field, axis, value)
    for scene in keyed_scenes:
        _expire()
        _apply_inputs(scene)
    return None


def _lifecycle():
    handlers = bpy.app.handlers
    return ((handlers.undo_pre, _invalidate), (handlers.undo_post, _recover),
            (handlers.redo_pre, _invalidate), (handlers.redo_post, _recover),
            (handlers.load_pre, _invalidate), (handlers.load_post, _load))


def register():
    for hooks, callback in _lifecycle():
        if callback not in hooks:
            hooks.append(callback)


def refresh_ready():
    # An open/load or an Action-binding transaction must finish with its own
    # callbacks before the code that owns them can be replaced.
    return not (_busy or _expiry_deferrals or bpy.app.timers.is_registered(_after_read))


def _refresh_maps():
    return dict(sessions=_sessions, keyed=_keyed, known_actions=_known_actions,
                applied=_applied, routes=_route_inputs, inputs=_pose_inputs,
                previews=_preview_inputs, operators=_operators)


def detach_for_refresh():
    """Detach this runtime's watchers while keeping its current native pose."""
    state = {name: mapping.copy() for name, mapping in _refresh_maps().items()}
    _invalidate()
    return state


def resume_after_refresh(state):
    # Blender IDs outlive this synchronous module swap. Receipts distinguish
    # applied offsets from later hand edits when the same Action evaluates again.
    maps = _refresh_maps()
    if state.keys() != maps.keys() or any(maps.values()):
        raise RuntimeError('Could not transfer the active limb pose to the refreshed add-on.')
    try:
        for name, mapping in maps.items():
            mapping.update(state[name])
        if _sessions or _keyed:
            _watch()
    except Exception:
        _invalidate()
        raise


def unregister():
    _expire(all_sessions=True)
    for uid, (_, rig) in tuple(_keyed.items()):
        _release_inputs(uid, rig)
    _invalidate()
    for hooks, callback in _lifecycle():
        if callback in hooks:
            hooks.remove(callback)
