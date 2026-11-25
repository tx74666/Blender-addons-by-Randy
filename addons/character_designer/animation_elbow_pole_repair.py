"""Align retained elbow poles while preserving the evaluated arm and authored curves."""

import bisect
import json
import math

import bpy

from . import animation_controller_isolation as isolation
from . import animation_controller_mapping as mapping
from . import animation_export_inputs as inputs
from . import controller_animation as controller
from . import controller_animation_sampling as sampling
from . import elbow_pole_layout, forearm_twist, limb_ik, limb_ik_fk as match
from .animation_knee_pole_repair import (
    _angle_seam_probes, _assign, _require, _set_frame, _write_keys,
)
from .controller_animation_curves import pole_angle_keys


PLAYBACK_LIMITS = (.00075, .15, .0005, 1.)
FIT_LIMITS = (.000025, .006, .00005)


def _editable_curve(curve):
    return (curve is not None and curve.keyframe_points and not curve.mute and not curve.lock
            and not curve.modifiers and not curve.sampled_points
            and not (curve.group and (curve.group.lock or curve.group.mute)))


def _arm_channels(rig, action, output, slot):
    from .control_pose_offset import _bag

    curves = inputs._curves(action, slot)
    indexed = {(curve.data_path, curve.array_index): curve for curve in curves}
    _require(len(indexed) == len(curves), 'The controller Action has ambiguous duplicate channels.')
    inventory = limb_ik._validate_inventory(rig, limb_pose_only=True)
    bag = _bag(output, slot)
    result = []
    static_angles = []
    for side in ('L', 'R'):
        data = inventory['rigs'].get(('ARM', side))
        _require(data and elbow_pole_layout.supports(inventory, data),
                 'Elbow alignment needs both installed direct arm controls.')
        target = rig.pose.bones[data['target'].name]
        mode = indexed.get((match.property_path(target), 0))
        if mode is not None:
            _require(mode.keyframe_points and not mode.mute and not mode.modifiers
                     and not mode.sampled_points and not (mode.group and mode.group.mute)
                     and all(p.co.y == 1. and p.interpolation in {'LINEAR', 'CONSTANT'}
                             for p in mode.keyframe_points),
                     'Choose a motion with both arms in IK throughout.')
        pole = rig.pose.bones[data['pole'].name]
        constraint = match._fixed_pole_constraint(data)
        paths = [(pole.path_from_id('location'), axis) for axis in range(3)]
        paths.append((constraint.path_from_id('pole_angle'), 0))
        original = tuple(indexed.get(path) for path in paths)
        _require(all(_editable_curve(curve) for curve in original[:3])
                 and (original[-1] is None or _editable_curve(original[-1])),
                 'Elbow alignment needs editable pole location and angle curves.')
        if original[-1] is None:
            static_angles.append((constraint, float(constraint.pole_angle)))
        changed = []
        for path, index in paths:
            curve = bag.fcurves.find(path, index=index)
            changed.append(curve if curve is not None else bag.fcurves.new(path, index=index))
        result.append((data, pole, constraint, tuple(changed)))
    return result, curves, static_angles


def _capture(rig, arms):
    pose = [match._matrices(rig, match.pose_names(data)) for data, *_ in arms]
    seeds = [(tuple(pole.location), float(constraint.pole_angle))
             for _, pole, constraint, _ in arms]
    return pose, seeds


def _pose_error(rig, wanted, units):
    position, rotation, scale = match._pose_errors(rig, wanted)
    return position * units, math.degrees(rotation), scale


def _within(errors, limits):
    return all(error <= limit for error, limit in zip(errors, limits))


def _phase_error(rig, data, wanted):
    upper, lower, end = data['chain']
    origin, joint, tip = (wanted[name].translation for name in (upper, lower, end))
    axis = tip - origin
    length = axis.length
    if length < 1.e-8:
        return None
    axis.normalize()

    def planar(vector):
        return vector - axis * vector.dot(axis)

    current = planar(rig.pose.bones[lower].head - origin)
    target = planar(joint - origin)
    if min(current.length, target.length) < .01 * length:
        # A nearly straight arm gives a poorly conditioned joint radius. Its
        # upper-bone frame still tells us which way the native IK rotated.
        actual_axes = rig.pose.bones[upper].matrix.to_3x3().col
        target_axes = wanted[upper].to_3x3().col
        current, target = max(((planar(actual_axes[i]), planar(target_axes[i])) for i in (0, 2)),
                              key=lambda pair: min(pair[0].length, pair[1].length))
    if min(current.length, target.length) < 1.e-8:
        return None
    return math.atan2(axis.dot(current.cross(target)), current.dot(target))


def _fit_phases(private, arms, desired, units):
    """Measure both native responses in each dependency graph update."""
    rig, context = private.rig, private.context
    history = {}
    for _ in range(6):
        changed = False
        for index, ((data, _, constraint, _), wanted) in enumerate(zip(arms, desired)):
            if _within(_pose_error(rig, wanted, units), FIT_LIMITS):
                continue
            error = _phase_error(rig, data, wanted)
            if error is None or abs(error) < 1.e-7:
                continue
            angle = float(constraint.pole_angle)
            step = .0125
            if index in history:
                previous_angle, previous_error = history[index]
                delta = math.remainder(error - previous_error, math.tau)
                if abs(delta) < 1.e-7:
                    continue
                step = -error * math.remainder(angle - previous_angle, math.tau) / delta
                step = max(-1., min(1., step))
            history[index] = angle, error
            constraint.pole_angle = math.remainder(angle + step, math.tau)
            changed = True
        if not changed:
            break
        match._update(context, rig)


def _solve(private, arms, frame, reference, previous, units):
    rig, scene, context = private.rig, private.scene, private.context
    desired, seeds = reference
    previous_values, previous_handles = previous if previous else (None, None)
    for index, ((_, pole, constraint, _), (location, angle)) in enumerate(zip(arms, seeds)):
        pole.location = location
        constraint.pole_angle = previous_values[index][3] if previous_values else angle
    _set_frame(scene, frame)
    handles = []
    for index, (data, pole, _, _) in enumerate(arms):
        position = elbow_pole_layout.position(rig, data, desired[index],
                                              previous=previous_handles[index] if previous_handles else None)
        matrix = pole.matrix.copy()
        matrix.translation = position
        pole.location = rig.convert_space(pose_bone=pole, matrix=matrix,
                                          from_space='POSE', to_space='LOCAL').translation
        handles.append(position)
    match._update(context, rig)
    _fit_phases(private, arms, desired, units)
    values = []
    for index, (data, pole, constraint, _) in enumerate(arms):
        wanted = desired[index]

        def acceptable(target):
            return _within(_pose_error(rig, target, units), FIT_LIMITS)

        errors = _pose_error(rig, wanted, units)
        if not _within(errors, PLAYBACK_LIMITS):
            match._match_fixed_pole_angle(context, rig, data, wanted, local_search=True, accept_pose=acceptable)
        errors = _pose_error(rig, wanted, units)
        if not _within(errors, PLAYBACK_LIMITS):
            match._match_fixed_pole_angle(context, rig, data, wanted, precise=True, accept_pose=acceptable)
            errors = _pose_error(rig, wanted, units)
        _require(_within(errors, PLAYBACK_LIMITS),
                 f'Elbow alignment could not preserve {data["chain"][1]} at frame {frame:g} '
                 f'(meters, degrees, scale = {errors}).')
        values.append((*pole.location, float(constraint.pole_angle)))
    return values, handles


def _write(arms, solved):
    times = sorted(solved)
    for arm_index, (_, _, _, curves) in enumerate(arms):
        for channel, curve in enumerate(curves):
            pairs = [(t, solved[t][0][arm_index][channel]) for t in times]
            keys = pole_angle_keys(pairs) if channel == 3 else [(t, value, 'LINEAR') for t, value in pairs]
            _write_keys(curve, keys)


def _errors(rig, arms, reference, units):
    measured = []
    desired, _ = reference
    for (data, *_), wanted in zip(arms, desired):
        guide = elbow_pole_layout.alignment_error(rig, data)
        measured.append((*_pose_error(rig, wanted, units), guide if guide is not None else 0.))
    return tuple(map(max, zip(*measured)))


def _next_key(frame, grid, errors):
    index = bisect.bisect_left(grid, frame)
    _require(0 < index < len(grid), 'Elbow alignment could not retain the original Action endpoints.')
    left, right = grid[index - 1:index + 1]
    midpoint = sampling._native_time((left + right) * .5)
    gap = sampling.MIN_KEY_GAP
    _require(min(midpoint - sampling._native_time(left), sampling._native_time(right) - midpoint) > gap
             and (midpoint == round(midpoint) or abs(midpoint - round(midpoint)) > gap),
             f'Elbow alignment needs keys closer than Blender can edit independently at frame {frame:g} '
             f'(meters, degrees, scale, guide degrees = {errors}).')
    return midpoint


def _rebuild(private, action, slot, output, progress):
    rig, scene = private.rig, private.scene
    arms, curves, static_angles = _arm_channels(rig, action, output, slot)
    times = controller._times(action, curves, None, None, 1.)
    units = max(rig.matrix_world.to_scale()) * scene.unit_settings.scale_length
    _require(units > 0 and math.isfinite(units), 'The character has no usable scene scale.')
    reference, solved = {}, {}
    base = [times[0], *(frame for frame in times[1:-1] if frame.is_integer()), times[-1]]
    pending = {base[index] for index in sampling.legal_indices(base)}
    output_curves = [curve for _, _, _, changes in arms for curve in changes]

    def read_missing(probes):
        missing = sorted(set(probes).difference(reference))
        if not missing:
            return
        _assign(rig, action, slot)
        # Removing an Action-owned angle track leaves its last value in RNA.
        # Reapply the original native angle whenever the source had no key.
        for constraint, angle in static_angles:
            constraint.pole_angle = angle
        for frame in missing:
            _set_frame(scene, frame)
            _require(all(constraint.enabled and match.mode_for_rig(rig, data) == 'IK'
                         for data, _, constraint, _ in arms),
                     'Choose a motion with both arms in IK throughout.')
            reference[frame] = _capture(rig, arms)
        _assign(rig, output, slot)

    for round_index in range(1, 9):
        grid = sorted(solved.keys() | pending)
        probes = set(times) | set(grid) | {(a+b)*.5 for a, b in zip(grid, grid[1:])}
        _require(len(probes) <= mapping.MAX_SAMPLES, 'The motion exceeds the elbow alignment sample budget.')
        read_missing(probes)
        for curve in output_curves:
            curve.mute = True
        for index, frame in enumerate(sorted(pending), 1):
            if progress:
                progress('Aligning elbow poles', index, len(pending), round_index)
            grid_index = bisect.bisect_left(grid, frame)
            previous = solved[grid[grid_index-1]] if grid_index else None
            solved[frame] = _solve(private, arms, frame, reference[frame], previous, units)
        _write(arms, solved)
        probes.update(_angle_seam_probes(arms, grid))
        read_missing(probes)
        worst = (0.,) * len(PLAYBACK_LIMITS)
        pending = set()
        for index, frame in enumerate(sorted(probes), 1):
            if progress:
                progress('Checking aligned elbow motion', index, len(probes), round_index)
            _set_frame(scene, frame)
            errors = _errors(rig, arms, reference[frame], units)
            worst = tuple(map(max, worst, errors))
            if not _within(errors, PLAYBACK_LIMITS):
                _require(frame not in solved, f'Playback of repaired elbows failed at keyed frame {frame:g}: {errors}.')
                pending.add(_next_key(frame, grid, errors))
        if not pending:
            return dict(sampled_key_times=len(solved), playback_samples=len(probes), rounds=round_index,
                        max_position_m=worst[0], max_rotation_deg=worst[1], max_scale=worst[2],
                        max_guide_degrees=worst[3])
    raise mapping.MappingNeeded('Elbow motion needs more subdivision to preserve this Action.')


def repair_action(context, rig, action, slot, source_hash, progress=None):
    """Return an independent copy whose only edited curves are the two elbow poles."""
    _require(bpy.app.background, 'Align elbow poles in an isolated preparation worker.')
    marker, version = elbow_pole_layout.MARKER, elbow_pole_layout.VERSION
    _require(action is not None and action.get(marker) != version and not action.asset_data,
             'Choose a controller Action that needs elbow pole alignment.')
    try:
        origin = json.loads(action.get(mapping.RECORD, '{}'))
    except (TypeError, ValueError) as error:
        raise mapping.MappingNeeded('The Action has incomplete target mapping data.') from error
    _require(isinstance(origin, dict) and origin.get('target_rest_sha256') == mapping._rest_fingerprint(rig),
             'The Action has no matching target Rest pose.')
    output = None
    try:
        with forearm_twist.defer_runtime(context, flush_on_exit=False), isolation.isolate_target(context, rig) as private:
            output = action.copy()
            output.use_fake_user = False
            output.name = action.name + ' · Aligned Elbows'
            result = _rebuild(private, action, slot, output, progress)
            diagnostics = dict(origin, native_output_verified=True, source_action_sha256=source_hash,
                               source_action=action.name_full, source_slot=slot,
                               elbow_pole_alignment=result, elbow_pole_layout=version)
            output[marker] = version
            output[mapping.RECORD] = json.dumps(diagnostics, ensure_ascii=False, allow_nan=False, sort_keys=True)
        return mapping.MappingResult(output, slot, source_hash, diagnostics)
    except BaseException:
        if output is not None and output.users == 0:
            bpy.data.actions.remove(output)
        raise
