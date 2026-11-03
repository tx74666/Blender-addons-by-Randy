"""Retained whole-clip local translations of explicitly selected IK targets.

The original Custom and Source are never written. This edits an existing
controller trajectory by translating its stored points/handles, without
resampling, solving again, adding rig parts, or installing callbacks.
"""

from array import array
import copy
import hashlib
import json
import math
import struct


RECORD = 'character_designer_controller_animation_move_v1'
SCHEMA = 'character-designer.controller-animation-move/1'
MAX_CURVES = 20000
MAX_POINTS = 500000
_TRANSFORMS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')
_INTERPOLATION = {'CONSTANT', 'LINEAR', 'BEZIER'}


def _require(value, message):
    if not value:
        raise ValueError(message)


def _backend():
    from . import control_pose_offset as offset, controller_animation as controller
    from . import animation_physics as physics
    return offset, controller, physics


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _f32(value):
    _require(math.isfinite(value), 'The controller move contains a non-finite value.')
    try:
        result = struct.unpack('=f', struct.pack('=f', value))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError('The controller move exceeds native key precision.') from error
    _require(math.isfinite(result), 'The controller move exceeds native key precision.')
    return result


def translate_key_payload(payload, delta):
    """Expected native-float vertical translation, retaining every other field."""
    _require(type(delta) in (int, float) and math.isfinite(delta), 'Choose a finite local move.')
    result = copy.deepcopy(payload)
    keys = result.get('keyframe_points')
    _require(isinstance(keys, list) and keys, 'A complete keyed location curve is required.')
    for point in keys:
        _require(all(name in point for name in ('co', 'handle_left', 'handle_right')),
                 'The location curve does not expose its complete handles.')
        for field in ('co', 'co_ui', 'handle_left', 'handle_right'):
            if field not in point:
                continue
            values = point[field]
            _require(isinstance(values, (list, tuple)) and len(values) == 2,
                     'A key coordinate has an unsupported shape.')
            point[field] = [values[0], _f32(float(values[1]) + delta)]
    return result


def pending_delta(current, baseline):
    _require(len(current) == len(baseline) == 3 and
             all(type(v) in (int, float) and math.isfinite(v) for v in (*current, *baseline)),
             'The selected target location must be finite.')
    delta = tuple(float(a)-float(b) for a, b in zip(current, baseline))
    floor = 1e-7 * max(1., *(abs(float(v)) for v in (*current, *baseline)))
    _require(max(map(abs, delta)) > floor,
             'Move the selected IK target before applying across the clip. '
             'If Auto Key already keyed the move, its earlier position cannot be inferred.')
    return delta


def _pointer(value):
    try:
        pointer = int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError, ValueError) as error:
        raise ValueError('Select actual IK hand or foot Pose targets.') from error
    _require(pointer != 0, 'A selected IK target is no longer available.')
    return pointer


def _selection(context, rig, targets=None):
    _require(context.mode == 'POSE' and getattr(context, 'object', None) is rig,
             'Use Pose Mode on this animation rig and select only IK hand or foot targets.')
    selected = tuple(getattr(context, 'selected_pose_bones', ()) or ())
    _require(0 < len(selected) <= 4 and len({_pointer(pb) for pb in selected}) == len(selected),
             'Select the IK hand or foot targets to move across the clip.')
    _require(all(pb.id_data is rig for pb in selected), 'The selected targets belong to another rig.')
    if targets is not None:
        explicit = tuple(targets)
        _require(len(explicit) == len(selected) and
                 {_pointer(pb) for pb in explicit} == {_pointer(pb) for pb in selected},
                 'Explicit targets must match the current Pose selection exactly.')
    return tuple(sorted(selected, key=lambda pb: pb.name))


def _idle_physics(context, physics):
    value = physics.status(context)
    _require(not value.get('hair', {}).get('running') and
             not value.get('hair', {}).get('pending_recovery') and
             not any(row.get('running') or row.get('pending_recovery')
                     for row in value.get('dresses', ())),
             'Pause physics and finish its recovery before moving a controller clip.')


def _curve_ready(curve, *, location=False, start=None, end=None):
    _require(not curve.modifiers and not curve.sampled_points and not curve.mute and not curve.lock
             and not (curve.group and (curve.group.lock or getattr(curve.group, 'mute', False)))
             and not getattr(curve, 'is_driver', False),
             'Unlock the target curves and remove their modifiers, samples or muting first.')
    points = curve.keyframe_points
    _require(0 < len(points) <= MAX_POINTS, 'The target needs nonempty bounded key curves.')
    previous = None
    for point in points:
        time = float(point.co[0])
        _require(math.isfinite(time) and (previous is None or time > previous)
                 and point.interpolation in _INTERPOLATION
                 and all(math.isfinite(float(value)) for field in ('co', 'handle_left', 'handle_right')
                         for value in getattr(point, field)),
                 'The target needs ordered finite Constant, Linear or Bezier keys.')
        previous = time
    if location:
        _require(float(points[0].co[0]) == start and float(points[-1].co[0]) == end,
                 'Each target location curve must cover the complete controller clip.')
    else:
        _require(all(float(point.co[1]) == 1. and
                     (point.interpolation != 'BEZIER' or
                      float(point.handle_left[1]) == float(point.handle_right[1]) == 1.)
                     for point in points),
                 'The selected limb must stay in IK for the complete clip.')


def _artist_settings(block):
    result = {}
    for prop in block.bl_rna.properties:
        name = prop.identifier
        if name in {'rna_type', 'is_valid', 'is_empty'} or prop.type not in {'BOOLEAN','INT','FLOAT','STRING','ENUM'}:
            continue
        value = getattr(block, name)
        result[name] = (list(value) if getattr(prop, 'is_array', False) else
                        sorted(value) if isinstance(value, set) else value)
    return result


def _curve_payload(api, curve):
    payload = api.fingerprint._curve_payload(curve)
    payload['artist_curve_settings'] = _artist_settings(curve)
    payload['artist_key_selection'] = [[bool(point.select_control_point),
        bool(point.select_left_handle), bool(point.select_right_handle)] for point in curve.keyframe_points]
    group = curve.group
    payload['artist_group_settings'] = _artist_settings(group) if group is not None else None
    if group is not None and getattr(group, 'colors', None) is not None:
        payload['artist_group_colors'] = _artist_settings(group.colors)
    return payload


def _states(api, action, affected=None):
    states, selected, total, count = {}, {}, 0, 0
    for li, layer in enumerate(action.layers):
        for si, strip in enumerate(layer.strips):
            _require(strip.type == 'KEYFRAME', 'Unsupported Action strip; the old Custom was kept.')
            for bag in strip.channelbags:
                for ci, curve in enumerate(bag.fcurves):
                    count += 1
                    total += len(curve.keyframe_points) + len(curve.sampled_points)
                    _require(count <= MAX_CURVES and total <= MAX_POINTS,
                             'This clip exceeds the complete curve-preservation bound.')
                    key = li, si, bag.slot_handle, ci
                    payload = _curve_payload(api, curve)
                    states[key] = _digest(payload)
                    channel = bag.slot_handle, curve.data_path, curve.array_index
                    if affected is not None and channel in affected:
                        selected[key] = (payload, affected[channel])
    return states, selected


def _raw_snapshot(variant, controller):
    result = []
    for entry in variant['capture']['targets']:
        owner = entry['target']
        raw = controller._raw(owner, tuple(pb.name for pb in owner.pose.bones))
        # Constraint graph/settings are not part of a target-location move.
        # Preserve input channels/properties; let existing drivers evaluate.
        raw['constraints'] = {}
        result.append((owner, raw))
    return result


def _raw_matches(snapshot, controller):
    for owner, expected in snapshot:
        current = controller._raw(owner, tuple(expected['pose']))
        current['constraints'] = {}
        if current != expected:
            return False
    return True


def prepare(context, rig=None, *, targets=None):
    offset, controller, physics = _backend()
    from . import animation_worklist as worklist
    item = worklist.current(context)
    _require(item is not None and item.side == 'CUSTOM', 'Select the converted Custom animation.')
    rig = item.rig if rig is None else rig
    selected = _selection(context, rig, targets)
    variant = offset.prepare_variant(context, rig, label='Move Across Clip')
    api, action = variant['api'], variant['original']
    record = controller._read_record(action)
    _require(record is not None, 'Make Controller Animation before applying a move across the clip.')
    start, end = float(record['frame_start']), float(record['frame_end'])
    _require(end > start and tuple(map(float, action.frame_range)) == (start, end)
             and start <= variant['frame'] <= end,
             'Use a frame inside the unchanged complete controller clip.')
    _idle_physics(context, physics)
    inventory = api.limb._validate_inventory(rig)
    supported = {entry['target'].name:(key, entry) for key, entry in inventory['rigs'].items()
                 if key[0] in {'ARM','LEG'}}
    bag = offset._bag(action, variant['slot'])
    channels = {}
    for curve in bag.fcurves:
        key = curve.data_path, curve.array_index
        _require(key not in channels, 'Duplicate controller channels need deliberate repair first.')
        channels[key] = curve
    affected, moves = {}, []
    for pb in selected:
        _require(pb.name in supported, 'Select only the actual IK hand or foot targets, without Pole or helper bones.')
        key, entry = supported[pb.name]
        expected_role = 'HAND_IK' if key[0] == 'ARM' else 'FOOT_IK'
        _require(pb.bone.get('character_designer_limb_ik_role') == expected_role
                 and pb.bone.get(api.match.VERSION_KEY) == api.match.VERSION
                 and float(pb.get(api.match.PROPERTY, -1)) == 1.
                 and not any(pb.lock_location) and not pb.bone.use_connect,
                 'Use an unlocked independent IK hand or foot target in IK mode.')
        limb_name = key[0] + '.' + key[1]
        _require(limb_name in record['limbs'], 'This target is outside the recorded controller clip.')
        path, mode_path = pb.path_from_id('location'), api.match.property_path(pb)
        _require(not any(curve.data_path in {path, mode_path} for curve in rig.animation_data.drivers),
                 'A target location or mode driver needs a separate authoring workflow.')
        mode = channels.get((mode_path, 0))
        _require(mode is not None, 'The target lost its complete-clip IK mode curve.')
        _curve_ready(mode)
        location = [channels.get((path, index)) for index in range(3)]
        _require(all(curve is not None for curve in location), 'The target needs all three location curves.')
        for curve in location:
            _curve_ready(curve, location=True, start=start, end=end)
        baseline = tuple(float(curve.evaluate(variant['frame'])) for curve in location)
        current = tuple(float(value) for value in pb.location)
        delta = pending_delta(current, baseline)
        moves.append(dict(name=pb.name, pointer=_pointer(pb), limb=limb_name,
                          current=current, baseline=baseline, delta=delta))
        affected.update({(variant['slot'], path, index):delta[index] for index in range(3)})
    states, payloads = _states(api, action, affected)
    _require(len(payloads) == 3*len(moves), 'The selected target channels are ambiguous.')
    expected = dict(states)
    for key, (payload, delta) in payloads.items():
        expected[key] = _digest(translate_key_payload(payload, delta))
    owned_poles = [(entry['target'], name, key, values['pole_angle'])
                   for entry in variant['capture']['targets']
                   for name, constraints in entry.get('constraints', {}).items()
                   for key, values in constraints.items() if 'pole_angle' in values]
    return dict(move_api=1, phase='prepared', context=context, variant=variant, moves=moves,
                affected=affected, original_states=states, expected_states=expected,
                controller_record=action.get(controller.RECORD), raw=_raw_snapshot(variant, controller),
                display_checkpoint=controller._capture_display_checkpoint(rig),
                original_properties=api.target._freeze(dict(action.items())),
                owned_poles=owned_poles, scene=context.scene, view_layer=context.view_layer,
                autokey=bool(context.scene.tool_settings.use_keyframe_insert_auto),
                frame_start=start, frame_end=end)


def _translate_curve(curve, delta):
    if delta == 0.:
        return
    count = len(curve.keyframe_points)
    for field in ('co', 'handle_left', 'handle_right'):
        values = array('f', [0.]) * (2*count)
        curve.keyframe_points.foreach_get(field, values)
        for index in range(1, len(values), 2):
            values[index] = _f32(float(values[index]) + delta)
        curve.keyframe_points.foreach_set(field, values)
    # Times are unchanged and already ordered. No update/clear/add or scalar
    # key setters: they can recalculate artist handles or flush pending poses.


def _fault(phase):
    return None


def apply(plan, *, activate=True):
    _require(isinstance(plan, dict) and plan.get('move_api') == 1 and plan.get('phase') == 'prepared',
             'Prepare the current selected target move before applying it.')
    _require(activate is True, 'Apply Move Across Clip creates and activates a retained Custom.')
    offset, controller, physics = _backend()
    variant, context = plan['variant'], plan['context']
    api, rig = variant['api'], variant['rig']
    _require(context.scene is plan['scene'] and context.view_layer == plan['view_layer'],
             'Return to this Move\'s scene and view layer before applying it.')
    offset._variant_owner(variant, final=True)
    chosen = _selection(context, rig)
    _require([_pointer(pb) for pb in chosen] == [move['pointer'] for move in plan['moves']]
             and _raw_matches(plan['raw'], controller)
             and all(owner.pose.bones[name].constraints[key].pole_angle == value
                     for owner, name, key, value in plan['owned_poles'])
             and variant['original'].get(controller.RECORD) == plan['controller_record'],
             'The selection, pending pose or controller record changed; prepare the move again.')
    _idle_physics(context, physics)
    _require(_states(api, variant['original'])[0] == plan['original_states'],
             'Artist curve settings or keys changed before applying the move.')
    action = None
    try:
        action = offset.new_variant_action(variant)
        bag = offset._bag(action, variant['slot'])
        for curve in bag.fcurves:
            key = variant['slot'], curve.data_path, curve.array_index
            if key in plan['affected']:
                _translate_curve(curve, plan['affected'][key])
        _fault('after_curve_translation')
        _require(_states(api, action)[0] == plan['expected_states'],
                 'A curve, handle or artist display setting changed beyond the requested translation.')
        action[RECORD] = json.dumps(dict(schema=SCHEMA, parent_action=variant['original'].name_full,
            frame=variant['frame'], frame_start=plan['frame_start'], frame_end=plan['frame_end'],
            coordinate_space='target-local location', moves=[{key:move[key] for key in ('name','limb','delta')}
                                                             for move in plan['moves']],
            operation='constant trajectory translation; original Source motion intentionally edited',
            sparse_correction_layer=False), ensure_ascii=False, allow_nan=False)
        result = offset.publish_variant(variant, action, activate=True, preserve_context=True)
        _fault('after_publication')
        context.view_layer.update()
        for owner, raw in plan['raw']:
            controller._restore_raw(owner, raw)
        for owner, name, key, value in plan['owned_poles']:
            owner.pose.bones[name].constraints[key].pole_angle = value
        context.view_layer.update()
        _fault('after_raw_restore')
        _require(_raw_matches(plan['raw'], controller)
                 and all(owner.pose.bones[name].constraints[key].pole_angle == value
                         for owner, name, key, value in plan['owned_poles'])
                 and bool(context.scene.tool_settings.use_keyframe_insert_auto) == plan['autokey'],
                 'The current moved pose or another pending input could not be retained.')
        _require(_states(api, action)[0] == plan['expected_states']
                 and _states(api, variant['original'])[0] == plan['original_states'],
                 'Publication changed a protected curve or its artist state.')
        plan['published_properties'] = api.target._freeze(dict(action.items()))
        # Observation only, never restoration authority. Reuse the existing
        # capture schema so later lock/display/timing/Auto Key edits cannot be
        # silently replaced by the operation's earlier rollback snapshot.
        plan['published_observation'] = api.target._capture(context, rig, include_owned_ik_poles=True)
        plan['published_users'] = action.users
        variant['saved'].status, variant['saved'].has_error = 'Move applied across the clip in a new Custom version.', False
        plan['phase'] = 'published'
        return dict(result, moves=[{key:move[key] for key in ('name','limb','delta')} for move in plan['moves']],
                    frame_start=plan['frame_start'], frame_end=plan['frame_end'],
                    coordinate_space='target-local location', sparse_correction_layer=False)
    except Exception as error:
        try:
            discard(plan)
        except Exception as recovery:
            plan['phase'] = 'recovery_required'
            raise RuntimeError('Controller move stopped; the original binding needs recovery: ' + str(recovery)) from error
        raise


def discard(plan):
    """Undo only this owned Move, with its same-operation display/cache proof."""
    _require(isinstance(plan, dict) and plan.get('move_api') == 1,
             'Only an owned controller Move can be discarded.')
    if plan.get('phase') == 'discarded':
        return
    _require(plan.get('phase') in {'prepared', 'published'},
             'This Move needs recovery review; its owned data was kept.')
    offset, controller, _physics = _backend()
    variant, context = plan['variant'], plan['context']
    api, rig = variant['api'], variant['rig']
    staged = variant.get('staged_action')
    _require(context.scene is plan['scene'] and context.view_layer == plan['view_layer'],
             'Return to this Move\'s scene and view layer before discarding it.')
    if plan['phase'] == 'published':
        item = api.worklist.current(context)
        ad = rig.animation_data
        _require(staged is not None and item is not None
                 and item.item_id == variant.get('owned_row_id') and item.rig is rig
                 and item.side == 'CUSTOM' and item.custom_action is staged
                 and item.custom_slot == variant['slot']
                 and item.source_action is variant['source'] and item.source_slot == variant['source_slot']
                 and ad is not None and ad.action is staged and ad.action_slot is not None
                 and ad.action_slot.handle == variant['slot']
                 and context.scene.frame_current + context.scene.frame_subframe == variant['frame'],
                 'Return to this Move\'s unchanged Custom, slot and frame before discarding it.')
        original = api.worklist._item(variant['saved'], variant['item_id'])
        identity = {key: value for key, value in api.revision._row(original).items()
                    if key in api.revision._IDENTITY_FIELDS}
        _require(identity == variant['row_identity']
                 and all(offset._action_hash(api, variant['original'], handle) == digest
                         for handle, digest in variant['hashes'].items())
                 and offset._action_hash(api, variant['source'], variant['source_slot']) == variant['source_hash']
                 and _states(api, variant['original'])[0] == plan['original_states']
                 and _states(api, staged)[0] == plan['expected_states']
                 and api.target._freeze(dict(variant['original'].items())) == plan['original_properties']
                 and api.target._freeze(dict(staged.items())) == plan['published_properties']
                 and staged.users == plan['published_users']
                 and bool(context.scene.tool_settings.use_keyframe_insert_auto) == plan['autokey']
                 and _raw_matches(plan['raw'], controller)
                 and all(owner.pose.bones[name].constraints[key].pole_angle == value
                         for owner, name, key, value in plan['owned_poles']),
                 'The Move, original animation or pending pose changed afterwards; those edits were kept.')
        _require(api.target._capture(context, rig, include_owned_ik_poles=True) == plan['published_observation'],
                 'Scene, control or display settings changed after this Move; those edits were kept.')
        api.target._validate_capture(context, variant['capture'])
        if getattr(item, 'current_rig_revision', ''):
            api.revision.validate(context, item.item_id)
        else:
            api.worklist._check_baseline(item)
    try:
        if staged is not None:
            controller._validate_display_checkpoint(rig, plan['display_checkpoint'])
        offset.rollback_variant(variant)
        if staged is not None:
            for owner, raw in plan['raw']:
                controller._restore_raw(owner, raw)
            context.view_layer.update()
            controller._restore_display_checkpoint(rig, plan['display_checkpoint'])
        plan['phase'] = 'discarded'
    except Exception:
        plan['phase'] = 'recovery_required'
        raise


def move_across_clip(context, rig=None, *, targets=None):
    return apply(prepare(context, rig, targets=targets))
