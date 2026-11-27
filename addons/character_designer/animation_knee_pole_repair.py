"""Realign both knee handles on a retained full-IK Action in a private worker."""

import bisect
import json
import math

import bpy
from mathutils import Quaternion

from . import animation_controller_isolation as isolation
from . import animation_controller_mapping as mapping
from . import animation_export_inputs as inputs
from . import controller_animation as controller
from . import controller_animation_curves as curve_tools
from . import controller_animation_sampling as sampling
from . import forearm_twist, knee_pole_layout, limb_ik, limb_ik_fk as match


MARKER = 'character_designer_knee_pole_layout'
VERSION = 1
PLAYBACK_LIMITS = (.00075, .15, .0005, 1.)


def _require(condition, message):
    if not condition:
        raise mapping.MappingNeeded(message)


def _assign(rig, action, slot):
    animation = rig.animation_data
    animation.action = action
    animation.action_slot = next(s for s in action.slots if s.handle == slot)
    from .animation_cleanup import restore_defaults
    restore_defaults(rig, action, slot)


def _set_frame(scene, frame):
    base = math.floor(frame)
    scene.frame_set(base, subframe=frame-base)


def _channels(rig, action, slot):
    curves = inputs._curves(action, slot)
    index = {(curve.data_path, curve.array_index): curve for curve in curves}
    _require(len(index) == len(curves), 'The controller Action has ambiguous duplicate channels.')
    inventory = limb_ik._validate_inventory(rig, limb_pose_only=True)
    result = []
    for side in ('L', 'R'):
        data = inventory['rigs'].get(('LEG', side))
        _require(data and match._leg_front_policy(inventory, data),
                 'Knee pole alignment needs both installed direct leg controls.')
        target = rig.pose.bones[data['target'].name]
        mode = index.get((target.path_from_id() + '["ik_fk"]', 0))
        _require(mode and len(mode.keyframe_points) and not mode.mute and not mode.modifiers
                 and not getattr(mode.group, 'mute', False)
                 and all(p.co.y == 1.0 and p.interpolation in {'CONSTANT','LINEAR'} for p in mode.keyframe_points),
                 'Choose a whole-clip IK Action with both legs keyed to IK throughout.')
        pole = rig.pose.bones[data['pole'].name]
        con = match._fixed_pole_constraint(data)
        keys = [(pole.path_from_id('location'), i) for i in range(3)] + [(con.path_from_id('pole_angle'), 0)]
        selected = tuple(index.get(key) for key in keys)
        _require(all(c and c.keyframe_points and not c.mute and not c.lock and not c.modifiers
                     and not c.sampled_points and not getattr(c.group, 'mute', False)
                     and not getattr(c.group, 'lock', False) for c in selected),
                 'The selected Action needs unlocked position and angle keys for each knee pole.')
        result.append((data, pole, con, selected))
    return result, curves


def _pose(rig, legs):
    return [match._matrices(rig, match.pose_names(data)) for data, *_ in legs]


def _shin_inputs(rig, legs, source, output, slot):
    from . import control_pose_offset
    bag = control_pose_offset._bag(output, slot)
    result = []
    original = {(c.data_path, c.array_index): c for c in inputs._curves(source, slot)}
    for data, *_ in legs:
        hip, shin, ankle = [rig.pose.bones[name] for name in data['chain']]
        _require(shin.rotation_mode == 'QUATERNION', 'Knee conditioning needs a quaternion shin input.')
        path = shin.path_from_id('rotation_quaternion')
        curves = [original.get((path, index)) for index in range(4)]
        _require(all(c is None or (not c.mute and not c.modifiers and not c.sampled_points
                                  and not getattr(c.group, 'mute', False)) for c in curves),
                 'The shin input has a muted or modified quaternion curve.')
        result.append(dict(hip=hip, shin=shin, ankle=ankle, path=path, curves=curves,
                           baseline=tuple(shin.rotation_quaternion),
                           output=[bag.fcurves.find(path,index=i) for i in range(4)], bag=bag))
    return result


def _source_shins(seeds, frame):
    for seed in seeds:
        seed['shin'].rotation_quaternion = [
            curve.evaluate(frame) if curve else default
            for curve, default in zip(seed['curves'],seed['baseline'])]


def _acceptable(rig, wanted, units):
    position, rotation, scale = match._pose_errors(rig, wanted)
    return position * units <= .000025 and rotation <= math.radians(.006) and scale <= .00005


def _bend_error(rig, data, desired):
    thigh, shin, foot = data['chain']
    hip, knee, ankle = (desired[name].translation for name in (thigh, shin, foot))
    axis = (ankle-hip).normalized()
    current = rig.pose.bones[shin].matrix.translation - hip
    current -= axis * current.dot(axis)
    target = knee - hip
    target -= axis * target.dot(axis)
    if min(target.length,current.length) <= (ankle-hip).length * .005:
        return None
    return math.atan2(axis.dot(current.cross(target)), current.dot(target))


def _fix(rig, context, legs, frame, scene, source_curves, wanted, previous, units, seeds):
    # An unkeyed constraint and pole remain editable across native dependency updates.
    # Seed each from its original animated value before seeking this private scene.
    for (_, pole, constraint, _), original in zip(legs, source_curves):
        pole.location = [curve.evaluate(frame) for curve in original[:3]]
        constraint.pole_angle = original[3].evaluate(frame)
        constraint.enabled = False
    _source_shins(seeds, frame)
    _set_frame(scene, frame)
    handles = []
    shins = []
    for i, (data, pole, constraint, _) in enumerate(legs):
        seed = seeds[i]
        hip, shin, ankle = (seed[n] for n in ('hip', 'shin', 'ankle'))
        matrix, changed = knee_pole_layout.condition_shin(hip.matrix, shin.matrix, ankle.matrix)
        if changed:
            _require(all(c is None or (not c.lock and not getattr(c.group,'lock',False)) for c in seed['curves']),
                     'Unlock the original shin quaternion before conditioning its private IK copy.')
            shin.matrix = matrix
        shins.append((tuple(shin.rotation_quaternion), changed))
        handle = knee_pole_layout.position(rig, data, wanted[i], previous=previous[1][i] if previous else None)
        matrix = pole.matrix.copy()
        matrix.translation = handle
        pole.matrix = matrix
        if previous:
            constraint.pole_angle = previous[0][i][3]
        handles.append(handle)
    for _, _, constraint, _ in legs:
        constraint.enabled = True
    match._update(context, rig)
    # Both legs are independent. Measure their native angular response in the
    # same dependency update, then use a bounded secant correction. Blender's
    # pole_angle is measured in the solver's own frame, which can have either
    # sign and a non-unit derivative with respect to the visible bend plane.
    last = {}
    for _ in range(6):
        changed = False
        for i, (data, _, constraint, _) in enumerate(legs):
            if _acceptable(rig, wanted[i], units):
                continue
            residual = _bend_error(rig, data, wanted[i])
            if residual is None:
                continue
            angle = float(constraint.pole_angle)
            step = .0125
            if i in last:
                old_angle, old_residual = last[i]
                angle_change = math.remainder(angle-old_angle, math.tau)
                error_change = math.remainder(residual-old_residual, math.tau)
                if abs(error_change) < 1.e-7:
                    continue
                step = max(-1., min(1., -residual * angle_change / error_change))
            last[i] = angle, residual
            constraint.pole_angle = math.remainder(angle + step, math.tau)
            changed = True
        if not changed:
            break
        match._update(context, rig)
    values = []
    for i, (data, pole, constraint, _) in enumerate(legs):
        desired = wanted[i]
        def accepted(bones):
            return _acceptable(rig, bones, units)
        if not _acceptable(rig, desired, units):
            match._match_fixed_pole_angle(context, rig, data, desired, local_search=True, accept_pose=accepted)
        def final_errors():
            position, rotation, scale = match._pose_errors(rig, desired)
            return (position * units, math.degrees(rotation), scale)
        errors = final_errors()
        if any(value > limit for value,limit in zip(errors, PLAYBACK_LIMITS)):
            match._match_fixed_pole_angle(context, rig, data, desired, precise=True, accept_pose=accepted)
            errors = final_errors()
        _require(all(value <= limit for value,limit in zip(errors, PLAYBACK_LIMITS)),
                 f'Knee alignment could not preserve {data["chain"][1]} at frame {frame:g} '
                 f'(meters, degrees, scale = {errors}).')
        values.append((*pole.location, float(constraint.pole_angle)))
    return values, handles, shins


def _write_keys(curve, keys):
    points = curve.keyframe_points
    points.clear()
    points.add(len(keys))
    points.foreach_set('co', [v for t, value, _ in keys for v in (t,value)])
    for point, (_, _, interpolation) in zip(points, keys):
        point.interpolation = interpolation
    curve.update()
    curve.mute = False


def _write(legs, samples, seeds):
    times = sorted(samples)
    for leg_index, (_, _, _, curves) in enumerate(legs):
        for channel, curve in enumerate(curves):
            pairs = [(t, samples[t][0][leg_index][channel]) for t in times]
            keys = (curve_tools.pole_angle_keys(pairs) if channel == 3 else
                    [(t, value, 'LINEAR') for t, value in pairs])
            _write_keys(curve, keys)
        seed = seeds[leg_index]
        if any(samples[t][2][leg_index][1] for t in times):
            values = []
            for frame in times:
                q = Quaternion(samples[frame][2][leg_index][0])
                if values:
                    q.make_compatible(values[-1])
                values.append(q)
            for channel in range(4):
                curve = seed['output'][channel]
                if curve is None:
                    curve = seed['bag'].fcurves.new(seed['path'],index=channel)
                    seed['output'][channel] = curve
                _write_keys(curve, [(t, q[channel], 'LINEAR') for t,q in zip(times,values)])
        else:
            for c in seed['output']:
                if c:
                    c.mute = False


def _errors(rig, legs, wanted, units):
    pos = rot = scale = arrow = 0.
    for i, (data, pole, _, _) in enumerate(legs):
        desired = wanted[i]
        p, r, s = match._pose_errors(rig, desired)
        pos, rot, scale = max(pos, p * units), max(rot, math.degrees(r)), max(scale, s)
        hip, knee, ankle = [desired[n].translation for n in data['chain']]
        axis = ankle - hip
        span = (knee-hip).length + (ankle-knee).length
        if axis.length > span * 1.e-5:
            axis.normalize()
            outward = knee - hip
            outward -= axis * outward.dot(axis)
            if outward.length >= .01 * span:
                visible = limb_ik._custom_shape_anchor_world(rig, pole)
                displayed = visible - (rig.matrix_world @ rig.pose.bones[data['chain'][1]].head)
                bend = rig.matrix_world.to_3x3() @ outward
                angle = math.degrees(math.atan2(displayed.cross(bend).length, displayed.dot(bend)))
                arrow = max(arrow, angle if displayed.length > 1e-8 else 180.)
    return pos, rot, scale, arrow


def _angle_seam_probes(legs, grid):
    probes = set()
    for _, _, _, curves in legs:
        keys = curves[-1].keyframe_points
        for a, b in zip(keys, list(keys)[1:]):
            if a.interpolation != 'CONSTANT' or abs(b.co.y-a.co.y) <= math.pi:
                continue
            start, end = a.co.x, b.co.x
            width = end-start
            probes.update(t for t in (start-width, start, start+.5*width, end-.01*width, end, end+width)
                          if grid[0] <= t <= grid[-1])
    return probes


def _rebuild(private, action, slot, output, progress):
    rig, scene, context = private.rig, private.scene, private.context
    source, all_curves = _channels(rig, action, slot)
    legs, _ = _channels(rig, output, slot)
    seeds = _shin_inputs(rig, legs, action, output, slot)
    original = [curves for *_, curves in source]
    times = controller._times(action, all_curves, None, None, 1.)
    units = max(rig.matrix_world.to_scale()) * scene.unit_settings.scale_length
    _require(units > 0 and math.isfinite(units), 'The character has no usable scene scale.')
    reference, solved = {}, {}
    verified = {}
    pending = {times[i] for i in sampling.legal_indices(times)}
    bounds = PLAYBACK_LIMITS
    for round_index in range(1, 9):
        before = sorted(solved)
        edited = set()
        for t in pending:
            i = bisect.bisect_left(before, t)
            if 0 < i < len(before):
                edited.add((before[i-1], before[i]))
        # Linear pole channels and the angle guards are local to each interval.
        # Retain verified, unchanged intervals when a different interval splits.
        for t in tuple(verified):
            i = bisect.bisect_left(before, t)
            if 0 < i < len(before) and (before[i-1], before[i]) in edited:
                del verified[t]
        grid = sorted(set(solved) | pending)
        midpoints = [(a+b)*.5 for a,b in zip(grid,grid[1:])]
        probes = sorted(set(times) | set(grid) | set(midpoints))
        _require(len(probes) <= mapping.MAX_SAMPLES, 'The motion exceeds the knee alignment sample budget.')
        _assign(rig, action, slot)
        missing = [t for t in probes if t not in reference]
        for index, frame in enumerate(missing, 1):
            if progress:
                progress('Reading original knee poses', index, len(missing), round_index)
            _source_shins(seeds, frame)
            _set_frame(scene, frame)
            reference[frame] = _pose(rig, legs)
        _assign(rig, output, slot)
        for _, _, _, curves in legs:
            for c in curves:
                c.mute = True
        for seed in seeds:
            for c in seed['output']:
                if c:
                    c.mute = True
        for index, frame in enumerate(sorted(pending), 1):
            if progress:
                progress('Aligning knee poles', index, len(pending), round_index)
            grid_index = bisect.bisect_left(grid, frame)
            previous = solved[grid[grid_index-1]] if grid_index else None
            solved[frame] = _fix(rig, context, legs, frame, scene, original, reference[frame], previous, units, seeds)
        _write(legs, solved, seeds)
        seam_times = _angle_seam_probes(legs, grid)
        missing_seams = sorted(seam_times.difference(reference))
        if missing_seams:
            _assign(rig, action, slot)
            for frame in missing_seams:
                _source_shins(seeds, frame)
                _set_frame(scene, frame)
                reference[frame] = _pose(rig, legs)
            _assign(rig, output, slot)
        probes = sorted(set(probes) | seam_times)
        worst = [0.] * len(bounds)
        pending = set()
        for index, frame in enumerate(probes, 1):
            errors = verified.get(frame)
            if errors is None:
                if progress:
                    progress('Checking aligned knee motion', index, len(probes), round_index)
                _set_frame(scene, frame)
                errors = verified[frame] = _errors(rig, legs, reference[frame], units)
            worst = list(map(max, worst, errors))
            if any(value > limit for value, limit in zip(errors, bounds)):
                _require(frame not in solved, f'Playback of the repaired knees failed at keyed frame {frame:g}: {errors}.')
                index = bisect.bisect_left(grid, frame)
                _require(0 < index < len(grid), 'Knee alignment could not retain the original Action endpoints.')
                midpoint = (grid[index-1] + grid[index]) * .5
                native = sampling._native_time(midpoint)
                _require(min(native - sampling._native_time(grid[index-1]), sampling._native_time(grid[index])-native)
                         > sampling.MIN_KEY_GAP and (native == round(native) or abs(native-round(native)) > sampling.MIN_KEY_GAP),
                         f'At frame {frame:g} the knee motion needs keys closer than Blender can edit independently '
                         f'(meters, degrees, scale, guide degrees = {errors}).')
                pending.add(midpoint)
        if not pending:
            if round_index > 1:
                worst = [0.] * len(bounds)
                for index, frame in enumerate(probes, 1):
                    if progress:
                        progress('Replaying completed knee motion', index, len(probes), round_index)
                    _set_frame(scene, frame)
                    errors = _errors(rig, legs, reference[frame], units)
                    _require(all(value <= limit for value, limit in zip(errors, bounds)),
                             f'The completed knee Action failed its native replay at frame {frame:g}.')
                    worst = list(map(max, worst, errors))
            conditioned = {seed['shin'].name: dict(path=seed['path'], new_channel_indices=
                           [j for j,c in enumerate(seed['curves']) if c is None],
                           sample_count=sum(int(v[2][i][1]) for v in solved.values()))
                           for i,seed in enumerate(seeds) if any(v[2][i][1] for v in solved.values())}
            return dict(conditioned_shins=conditioned, sampled_key_times=len(solved), playback_samples=len(probes), rounds=round_index,
                        max_position_m=worst[0], max_rotation_deg=worst[1], max_scale=worst[2],
                        max_guide_degrees=worst[3])
    raise mapping.MappingNeeded('Knee motion needs more subdivision to preserve this Action.')


def repair_action(context, rig, action, slot, source_hash, progress=None):
    """Return an unbound full-IK copy with aligned poles and conditioned shin inputs."""
    _require(bpy.app.background, 'Align knee poles in an isolated preparation worker.')
    _require(action is not None and action.get(MARKER) != VERSION and not action.asset_data,
             'Choose a controller Action that needs knee pole alignment.')
    try:
        origin = json.loads(action.get(mapping.RECORD, '{}'))
    except (TypeError, ValueError) as error:
        raise mapping.MappingNeeded('The Action has incomplete target mapping data.') from error
    rest = mapping._rest_fingerprint(rig)
    _require(isinstance(origin, dict) and origin.get('target_rest_sha256') == rest,
             'The Action has no matching target Rest pose.')
    output = None
    try:
        with forearm_twist.defer_runtime(context, flush_on_exit=False), isolation.isolate_target(context, rig) as private:
            output = action.copy()
            output.use_fake_user = False
            output.name = action.name + ' · Aligned Knees'
            result = _rebuild(private, action, slot, output, progress)
            diagnostics = dict(origin, native_output_verified=True, source_action_sha256=source_hash,
                source_action=action.name_full, source_slot=slot, original_source_action_sha256=origin.get('source_action_sha256'),
                knee_pole_alignment=result, knee_pole_layout=VERSION, control_representation='IK')
            output[MARKER] = VERSION
            output[mapping.RECORD] = json.dumps(diagnostics, ensure_ascii=False, allow_nan=False, sort_keys=True)
        from . import knee_pole_keying
        knee_pole_keying.store(output, slot, knee_pole_keying.capture(action, slot, rig, result['conditioned_shins']))
        return mapping.MappingResult(output, slot, source_hash, diagnostics)
    except BaseException:
        if output is not None and output.users == 0:
            bpy.data.actions.remove(output)
        raise
