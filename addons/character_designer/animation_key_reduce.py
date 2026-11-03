"""Explicit, retained Custom reductions with native sampled pose verification.

No drawing-time scans, callbacks or background playback service. Only complete,
synchronized LINEAR transform groups are reduced; every other curve stays exact.
The component bound is continuous. Native world-space pose bounds are checked at
authored times and quarter intervals, and are explicitly sampled bounds.
"""
import bisect
import json
import math

RECORD = 'character_designer_key_reduction_v1'
SCHEMA = 'character-designer.custom-key-reduction/1'
MAX_KEYS = 500_000
MAX_TIMES = 4097
MAX_POSE_SAMPLES = 2_000_000
_PAIRS = {}
_PRESERVED_POSES = {}


def _require(value, message):
    if not value:
        raise ValueError(message)


def _backend():
    from . import control_pose_offset as offset, animation_physics as physics
    return offset, offset._modules(), physics


def _tolerances(position_error, rotation_error, scale_error):
    values = position_error, rotation_error, scale_error
    _require(all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values),
             'Choose finite non-negative simplification tolerances.')
    _require(position_error <= 1 and rotation_error <= 10 and scale_error <= .1,
             'Use conservative tolerances: position at most 1 unit, rotation 10 degrees, scale 10%.')
    return dict(position=float(position_error), rotation=float(rotation_error), scale=float(scale_error))


def _physics_idle(context, rig, physics):
    value = physics.status(context)
    if value.get('rig') is rig:
        _require(value.get('valid'), 'Resolve the Physics error before checking animation keys.')
    _require(not value.get('hair', {}).get('running') and
             not any(row.get('running') or row.get('pending_recovery') for row in value.get('dresses', ())),
             'Pause Hair and Dress physics before simplifying animation keys.')
    _require(not value.get('hair', {}).get('pending_recovery'),
             'Complete Hair recovery before simplifying animation keys.')


def _depth(rig):
    result = 1
    for bone in rig.data.bones:
        current, seen = bone, set()
        while current is not None:
            _require(current.name not in seen and len(seen) <= 4096, 'The bone hierarchy is invalid.')
            seen.add(current.name)
            current = current.parent
        result = max(result, len(seen))
    return result


def _quaternion_floor(values):
    """Minimum norm of the raw four-component piecewise-linear source."""
    result = min(math.hypot(*row) for row in values)
    for first, last in zip(values, values[1:]):
        delta = tuple(b-a for a, b in zip(first, last))
        square = sum(v*v for v in delta)
        factor = max(0., min(1., -sum(a*d for a, d in zip(first, delta))/square)) if square else 0.
        result = min(result, math.hypot(*(a+factor*d for a, d in zip(first, delta))))
    # Cancellation error scales with the endpoints, including opposite-sign
    # huge raw components whose chord really passes through zero.
    margin = max(math.hypot(*row) for row in values) * 1e-12 + 1e-14
    return max(0., result-margin)


def _group_tolerance(prop, values, error, depth):
    # The depth margin is conservative preparation, not a native world-error
    # guarantee. The actual full-rig sample gate below remains authoritative.
    if prop == 'location':
        return error['position'] / (2*depth)
    if prop == 'scale':
        minimum = min(component for row in values for component in row)
        return error['scale'] * minimum / (2*depth) if minimum > 0 else 0.
    angle = math.radians(error['rotation']) / (2*depth)
    if prop == 'rotation_euler':
        return angle / math.sqrt(3.)
    floor = _quaternion_floor(values)
    return floor * math.sin(angle/2) if floor > 1e-8 else 0.


def _retained(times, values, tolerance, markers):
    from .animation_key_reduce_math import reduce_linear
    # Keep both sides of the loop seam and every marker's enclosing segment.
    anchors = {0, 1, len(times)-2, len(times)-1}
    for frame in markers:
        index = bisect.bisect_left(times, frame)
        anchors.update((max(0, index-1), min(len(times)-1, index)))
    ordered, indices, error, work = sorted(anchors), set(anchors), 0., 0
    for first, last in zip(ordered, ordered[1:]):
        if work >= 1_000_000:
            return tuple(range(len(times))), 0.
        result = reduce_linear(times[first:last+1], values[first:last+1], tolerance,
                               max_work=1_000_000-work)
        if result.budget_exhausted:
            return tuple(range(len(times))), 0.
        work += result.work
        indices.update(first+i for i in result.indices)
        error = max(error, result.max_error)
    return tuple(sorted(indices)), error


def _keyed_values(rig, bag):
    result = []
    for curve in bag.fcurves:
        if curve.mute:
            continue
        value = rig.path_resolve(curve.data_path)
        actual = value[curve.array_index] if hasattr(value, '__len__') else value
        result.append((curve.data_path, curve.array_index, float(actual)))
    return tuple(result)


def _pending_keyed(rig, bag, frame):
    return any(actual != float(curve.evaluate(frame)) for curve, (_path, _index, actual) in
               zip((curve for curve in bag.fcurves if not curve.mute), _keyed_values(rig, bag)))


def prepare(context, *, position_error=.0005, rotation_error=.1, scale_error=.001):
    """Read-only admission, source fingerprints and deterministic reduction plan."""
    error = _tolerances(position_error, rotation_error, scale_error)
    offset, api, physics = _backend()
    item = api.worklist.current(context)
    _require(item is not None, 'Select an editable Custom animation first.')
    plan = offset.prepare_variant(context, item.rig, label='Simplified')
    rig = plan['rig']
    _require(len(rig.pose.bones) <= 4096, 'This rig exceeds the bounded simplification scope.')
    _require('character_designer_rigging_session_v1' not in rig,
             'Finish the Rigging input session before simplifying Custom.')
    _physics_idle(context, rig, physics)
    api.revision._validate_paths(rig, plan['original'], plan['slot'], api.revision._modules(), plan['capture'])
    bag = offset._bag(plan['original'], plan['slot'])
    curves = tuple(bag.fcurves)
    count = sum(len(curve.keyframe_points) for curve in curves)
    _require(count <= MAX_KEYS, 'This animation exceeds 500,000 keys; simplify a shorter clip.')
    groups, all_times, skipped = {}, set(), 0
    for curve in curves:
        for point in curve.keyframe_points:
            _require(math.isfinite(point.co.x) and math.isfinite(point.co.y), 'A source key is not finite.')
            all_times.add(float(point.co.x))
        parsed = offset._curve_name(curve)
        if parsed is not None:
            name, prop, index = parsed
            _require((name, prop, index) not in groups, 'Duplicate transform curves cannot be simplified.')
            groups[name, prop, index] = curve
    _require(all_times and len(all_times) <= (MAX_TIMES+3)//4,
             'This clip has too many sample times for one bounded pose check; use a shorter clip.')
    ordered = sorted(all_times)
    _require(ordered[-1] > ordered[0], 'Choose an animation with a non-empty time range.')
    frames = set(ordered)
    for first, last in zip(ordered, ordered[1:]):
        frames.update(first+(last-first)*fraction for fraction in (.25, .5, .75))
    _require(len(frames) <= MAX_TIMES and len(frames)*len(rig.pose.bones)*2 <= MAX_POSE_SAMPLES,
             'This full-rig pose check exceeds the sample budget; use a shorter clip.')
    depth, candidates = _depth(rig), {}
    markers = [float(marker.frame) for marker in plan['original'].pose_markers]
    for name, prop in sorted({(name, prop) for name, prop, _index in groups}):
        width = offset._TRANSFORMS.get(prop, 4)
        channels = [groups.get((name, prop, i)) for i in range(width)]
        pb = rig.pose.bones.get(name)
        mode = getattr(pb, 'rotation_mode', None)
        supported = (pb is not None and prop != 'rotation_axis_angle' and
                     (not prop.startswith('rotation_') or prop == ('rotation_quaternion' if mode == 'QUATERNION'
                       else 'rotation_euler' if mode in {'XYZ','XZY','YXZ','YZX','ZXY','ZYX'} else '')))
        if not supported or any(curve is None or curve.mute or curve.lock or curve.modifiers or
                curve.sampled_points or len(curve.keyframe_points) < 5 or
                any(point.interpolation != 'LINEAR' for point in curve.keyframe_points) for curve in channels):
            skipped += 1
            continue
        times = tuple(float(point.co.x) for point in channels[0].keyframe_points)
        if any(tuple(float(p.co.x) for p in curve.keyframe_points) != times for curve in channels[1:]):
            skipped += 1
            continue
        values = tuple(tuple(float(curve.keyframe_points[i].co.y) for curve in channels) for i in range(len(times)))
        tolerance = _group_tolerance(prop, values, error, depth)
        indices, bound = _retained(times, values, tolerance, markers)
        if len(indices) < len(times):
            # Constant raw groups remain exactly constant in every mode. For
            # approximate changes, keep unsupported IK/driven groups intact.
            if not all(row == values[0] for row in values):
                try:
                    offset._native_fk(api, rig, {name}, curves)
                except (ValueError, KeyError, TypeError):
                    skipped += 1
                    continue
            candidates[name, prop] = dict(indices=indices, count=len(times), bound=bound, tolerance=tolerance)
    _require(candidates, 'No supported LINEAR keys can be reduced at these tolerances; the current Custom was kept.')
    affected = {(name, prop, index) for name, prop in candidates for index in range(offset._TRANSFORMS[prop])}
    plan.update(key_reduce_api=1, errors=error, candidates=candidates, frames=sorted(frames),
                before_keys=count, skipped_groups=skipped,
                pending_keyed=_pending_keyed(rig, bag, plan['frame']),
                unaffected=offset._unaffected(api, plan['original'], plan['slot'], affected))
    return plan


def _write(plan, action):
    offset = _backend()[0]
    bag = offset._bag(action, plan['slot'])
    for curve in bag.fcurves:
        parsed = offset._curve_name(curve)
        group = plan['candidates'].get(parsed[:2]) if parsed else None
        if group is None:
            continue
        keep = set(group['indices'])
        for index in range(len(curve.keyframe_points)-1, -1, -1):
            if index not in keep:
                curve.keyframe_points.remove(curve.keyframe_points[index], fast=True)
        curve.update()


def _native_pose(context, rig):
    result = []
    world = rig.matrix_world
    for pb in rig.pose.bones:
        matrix = world @ pb.matrix
        determinant = matrix.determinant()
        _require(all(math.isfinite(v) for row in matrix for v in row) and math.isfinite(determinant) and determinant > 1e-12,
                 'A sampled bone transform is singular, reflected or non-finite.')
        record = world @ pb.head, world @ pb.tail, matrix.to_quaternion(), matrix.to_scale()
        _require(all(math.isfinite(value) for field in record for value in field)
                 and math.hypot(*record[2]) > 1e-12 and math.hypot(*record[3]) > 1e-12,
                 'A sampled pose decomposition is singular or non-finite.')
        result.append(record)
    return result


def _verify(context, plan, action):
    """Explicit native checks; always restore fresh then-current raw inputs."""
    api, rig = plan['api'], plan['rig']
    errors = dict(position=0., rotation=0., scale=0.)
    try:
        context.scene.tool_settings.use_keyframe_insert_auto = False
        ad = rig.animation_data
        for frame in plan['frames']:
            poses = []
            for source in (plan['original'], action):
                ad.action = source
                ad.action_slot = api.worklist._slot(source, plan['slot'])
                api.ua._set_frame(context.scene, frame)
                context.view_layer.update()
                poses.append(_native_pose(context, rig))
            for first, last in zip(*poses):
                position = max(math.hypot(*(a-b for a, b in zip(first[0], last[0]))),
                               math.hypot(*(a-b for a, b in zip(first[1], last[1]))))
                delta = first[2].normalized().conjugated() @ last[2].normalized()
                angle = math.degrees(2*math.atan2(math.hypot(delta.x, delta.y, delta.z), abs(delta.w)))
                scale = math.hypot(*(a-b for a, b in zip(first[3], last[3]))) / math.hypot(*first[3])
                _require(all(math.isfinite(value) for value in (position, angle, scale)),
                         'A native pose error metric is non-finite; no result was published.')
                errors['position'] = max(errors['position'], position)
                errors['rotation'] = max(errors['rotation'], angle)
                errors['scale'] = max(errors['scale'], scale)
        # No numerical epsilon silently expands the user's quality allowance.
        _require(all(errors[key] <= plan['errors'][key] for key in errors),
                 'The full-rig pose check exceeded the tolerance; the original Custom was kept.')
        return errors
    finally:
        api.revision._restore_current(context, rig, plan['capture'], api.revision._modules())


def _fault(phase):
    """Native validation injection seam, inert in normal operation."""
    return None


def _restore_with_action(context, plan, action):
    """Restore fresh raw overrides while retaining the newly published binding."""
    api = plan['api']
    entries = []
    for entry in plan['capture']['targets']:
        if entry['target'] is plan['rig']:
            entry = dict(entry, action=action, action_fake_user=bool(action.use_fake_user),
                         snapshot=dict(entry['snapshot'], had_animation_data=True, had_action=True,
                                       slot=plan['slot']))
        entries.append(entry)
    captured = dict(plan['capture'], targets=entries)
    api.revision._restore_current(context, plan['rig'], captured, api.revision._modules())


def apply(context, plan):
    _require(isinstance(plan, dict) and plan.get('key_reduce_api') == 1 and plan.get('context') is context,
             'Prepare this current Custom before simplifying.')
    offset, api, physics = _backend()
    offset._variant_owner(plan, final=True)
    _physics_idle(context, plan['rig'], physics)
    action = None
    try:
        action = offset.new_variant_action(plan)
        _write(plan, action)
        _fault('after_real_curve_write')
        affected = {(name, prop, index) for name, prop in plan['candidates'] for index in range(offset._TRANSFORMS[prop])}
        _require(offset._unaffected(api, action, plan['slot'], affected) == plan['unaffected'],
                 'An untouched curve changed; the old Custom was kept.')
        bag = offset._bag(action, plan['slot'])
        after = sum(len(curve.keyframe_points) for curve in bag.fcurves)
        _require(after < plan['before_keys'], 'The native Action did not remove any keys.')
        measured = _verify(context, plan, action)
        offset._variant_owner(plan, final=True)
        after_hashes = {slot.handle: offset._action_hash(api, action, slot.handle)
                        for slot in action.slots if slot.target_id_type == 'OBJECT'}
        published = offset.publish_variant(plan, action, activate=True, preserve_context=True)
        _fault('after_publication')
        data = dict(schema=SCHEMA, before_item=plan['item_id'], after_item=published['item_id'],
                    slot=plan['slot'], source_slot=plan['source_slot'], source_hash=plan['source_hash'],
                    before_hashes=plan['hashes'], after_hashes=after_hashes,
                    before_keys=plan['before_keys'], after_keys=after, errors=plan['errors'],
                    measured_errors=measured, native_sample_count=len(plan['frames']),
                    verification='world heads/tails, rotation and scale at authored knots and quarter intervals',
                    continuous_world_error_proven=False, skipped_groups=plan['skipped_groups'])
        action[RECORD] = json.dumps(data, sort_keys=True, allow_nan=False)
        if plan['revision_value'] is not None:
            api.revision.validate(context, published['item_id'])
        api.target._validate_capture(context, plan['capture'])
        _restore_with_action(context, plan, action)
        _PAIRS[plan['rig'].as_pointer()] = published['item_id']
        _PRESERVED_POSES[plan['rig'].as_pointer()] = dict(item_id=published['item_id'], frame=plan['frame'],
            pending=plan['pending_keyed'], values=_keyed_values(plan['rig'], offset._bag(action, plan['slot'])))
        plan['saved'].status, plan['saved'].has_error = f"Simplified keys: {plan['before_keys']} → {after}. Before remains available.", False
        return dict(published, before_keys=plan['before_keys'], after_keys=after, measured_errors=measured)
    except Exception as error:
        if plan.get('staged_action') is not None:
            try:
                owned_pointer = plan['staged_action'].as_pointer()
                offset.rollback_variant(plan)
                _require(not any(value.as_pointer() == owned_pointer for value in api.bpy.data.actions),
                         'The staged Action gained another user and was retained for recovery.')
            except Exception as restore_error:
                raise RuntimeError('Simplification failed; owned recovery was kept: '+str(restore_error)) from error
        raise


def _read_record(action):
    raw = action.get(RECORD) if action is not None else None
    if type(raw) is not str or len(raw) > 65536:
        return None
    try:
        data = json.loads(raw)
        if (data.get('schema') != SCHEMA or not all(type(data.get(k)) is str and len(data[k]) == 32
                for k in ('before_item', 'after_item')) or
                not all(type(data.get(k)) is int and 0 <= data[k] <= MAX_KEYS for k in ('before_keys', 'after_keys'))):
            return None
        return data
    except (ValueError, TypeError, AttributeError, RecursionError):
        return None


def _pair(context):
    # Kept deliberately independent of prepare: no fingerprint/curve/pose scan
    # while drawing. Full ownership/content checks happen on compare execution.
    from . import animation_worklist as worklist
    saved, current = worklist.state(context), worklist.current(context)
    if current is None or current.side != 'CUSTOM' or current.rig is None:
        return None
    rig, matches = current.rig, []
    if len(saved.items) > 4096:
        return None
    for row in saved.items:
        if row.rig is not rig:
            continue
        data = _read_record(row.custom_action)
        if data and data['after_item'] == row.item_id and current.item_id in {data['before_item'], data['after_item']}:
            parent = next((item for item in saved.items if item.item_id == data['before_item']), None)
            if (parent is not None and parent.rig is rig and parent.source_action is row.source_action
                    and parent.source_slot == row.source_slot and parent.custom_slot == row.custom_slot == data.get('slot')):
                matches.append((parent, row, data))
    preferred = [pair for pair in matches if pair[1].item_id == _PAIRS.get(rig.as_pointer())]
    matches = preferred or matches
    if len(matches) != 1:
        return None
    before, after, data = matches[0]
    ad = rig.animation_data
    if ad is None or ad.action is not current.custom_action or ad.action_slot is None or ad.action_slot.handle != current.custom_slot:
        return None
    return before, after, data


def status(context):
    try:
        pair = _pair(context)
        if pair is None:
            return None
        before, after, data = pair
        from . import animation_worklist as worklist
        return dict(before_keys=data['before_keys'], after_keys=data['after_keys'],
                    active_before=worklist.current(context).item_id == before.item_id,
                    before_item=before.item_id, after_item=after.item_id)
    except (ValueError, RuntimeError, ReferenceError, AttributeError, KeyError, TypeError):
        return None


def compare(context, *, original):
    _require(type(original) is bool, 'Choose Before or After.')
    pair = _pair(context)
    _require(pair is not None, 'Select an unchanged simplified Custom pair first.')
    before, after, data = pair
    offset, api, physics = _backend()
    current = api.worklist.current(context)
    plan = offset.prepare_variant(context, current.rig, label='Comparison')
    _physics_idle(context, current.rig, physics)
    # Viewing another Action must not discard manual, not-yet-keyed changes to
    # a keyed input. Keep the current pose and let normal Custom editing finish.
    bag = offset._bag(current.custom_action, current.custom_slot)
    preserved = _PRESERVED_POSES.get(current.rig.as_pointer(), {})
    known_preserved = (preserved.get('item_id') == current.item_id and preserved.get('frame') == plan['frame']
                       and preserved.get('pending') is False and preserved.get('values') == _keyed_values(current.rig, bag))
    _require(known_preserved or not _pending_keyed(current.rig, bag, plan['frame']),
             'Key the pending pose edits before comparing Before/After; the current pose was kept.')
    for row, field in ((before, 'before_hashes'), (after, 'after_hashes')):
        if getattr(row, 'current_rig_revision', ''):
            api.revision.validate(context, row.item_id)
        else:
            api.worklist._check_baseline(row)
        expected = data[field]
        _require(set(map(int, expected)) == {slot.handle for slot in row.custom_action.slots if slot.target_id_type == 'OBJECT'}
                 and all(offset._action_hash(api, row.custom_action, int(handle)) == digest for handle, digest in expected.items()),
                 'This Before/After Action was edited; its contents were kept. Select the desired Custom directly.')
    _require(offset._action_hash(api, before.source_action, before.source_slot) == data['source_hash']
             and before.source_action is after.source_action and before.source_slot == after.source_slot == data['source_slot'],
             'The comparison Source identity or content changed.')
    target = before if original else after
    try:
        ad = target.rig.animation_data
        ad.action = target.custom_action
        ad.action_slot = api.worklist._slot(target.custom_action, target.custom_slot)
        plan['saved'].active_index = next(i for i, row in enumerate(plan['saved'].items) if row.item_id == target.item_id)
        api.ua._set_frame(context.scene, plan['frame'])
        context.view_layer.update()
        _fault('after_comparison_binding')
        api.target._validate_capture(context, plan['capture'])
        _PAIRS[target.rig.as_pointer()] = after.item_id
        _PRESERVED_POSES.pop(target.rig.as_pointer(), None)
        plan['saved'].status, plan['saved'].has_error = ('Before simplification' if original else 'After simplification'), False
        return target.custom_action
    except Exception:
        api.revision._restore_current(context, target.rig, plan['capture'], api.revision._modules())
        plan['saved'].active_index, plan['saved'].status, plan['saved'].has_error = plan['previous']
        raise
