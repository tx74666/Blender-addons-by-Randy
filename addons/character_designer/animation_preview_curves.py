"""Selected-control reduction of baked linear keys, preserving original key times."""

from array import array
from bisect import bisect_left
import hashlib
import json
import math

from .animation_key_reduce_math import reduce_linear
from .animation_worklist_fingerprint import _rna_settings, _MODIFIER_UI
from .control_pose_offset import _bag, _curve_name


# RNA column order matters on write: handle type and position changes can move handles.
_FIELDS = (
    ('co', 'f', 2), ('interpolation', 'i', 1), ('easing', 'i', 1),
    ('type', 'i', 1), ('back', 'f', 1), ('amplitude', 'f', 1), ('period', 'f', 1),
    ('handle_left_type', 'i', 1), ('handle_right_type', 'i', 1),
    ('select_control_point', 'b', 1), ('select_left_handle', 'b', 1),
    ('select_right_handle', 'b', 1), ('handle_left', 'f', 2), ('handle_right', 'f', 2),
)


def _column(points, name, code, width):
    values = array(code, [0]) * (len(points) * width)
    if values:
        points.foreach_get(name, values)
    return values


def _points(curve):
    return {name: _column(curve.keyframe_points, name, code, width)
            for name, code, width in _FIELDS}


def _replace(curve, columns, *, active_index=None):
    points = curve.keyframe_points
    if active_index is None and hasattr(curve, 'active_keyframe_index'):
        old_index = curve.active_keyframe_index
        active_index = -1
        if 0 <= old_index < len(points):
            time = points[old_index].co[0]
            times = columns['co'][::2]
            i = bisect_left(times, time)
            if i < len(times) and times[i] == time:
                active_index = i
    points.clear()
    points.add(len(columns['co']) // 2)
    for name, values in columns.items():
        points.foreach_set(name, values)
    if active_index is not None and active_index != curve.active_keyframe_index:
        curve.active_keyframe_index = active_index
    # Data is already sorted. update() recalculates auto handles, including
    # those we just restored from an exact reference. foreach_set tags the ID.


def _indexed(action, handle, paths=None):
    requested = None if paths is None else set(paths)
    curves = {}
    for curve in _bag(action, handle).fcurves:
        if requested is not None and curve.data_path not in requested:
            continue
        key = (curve.data_path, curve.array_index)
        if key in curves:
            raise ValueError('The selected slot contains duplicate animation channels.')
        curves[key] = curve
    return curves


def selected_paths(action, slot_handle, bone_names):
    names = set(bone_names)
    result = set()
    for curve in _bag(action, slot_handle).fcurves:
        info = _curve_name(curve)
        if info and info[0] in names:
            result.add(curve.data_path)
    return sorted(result)


def signature(action, slot_handle, paths=None):
    """Track relevant motion and edit policy while allowing normal viewport selection."""
    digest = hashlib.sha256()
    schema = {}
    for key, curve in sorted(_indexed(action, slot_handle, paths).items()):
        group = curve.group
        settings = (key, curve.extrapolation, curve.auto_smoothing, curve.mute, curve.lock,
                    (group.name, group.mute, group.lock) if group else None,
                    len(curve.keyframe_points), len(curve.sampled_points),
                    [_rna_settings(mod, exclude=_MODIFIER_UI, schema_cache=schema)
                     for mod in curve.modifiers])
        digest.update(json.dumps(settings, ensure_ascii=True, sort_keys=True).encode())
        for name, code, width in _FIELDS:
            if not name.startswith('select_'):
                digest.update(_column(curve.keyframe_points, name, code, width))
        digest.update(_column(curve.sampled_points, 'co', 'f', 2))
    return digest.hexdigest()


def _editable(curve):
    return (not curve.modifiers and not curve.sampled_points and not curve.lock
            and not curve.mute and not (curve.group and (curve.group.lock or curve.group.mute)))


def copy_keys(source_action, destination_action, slot_handle, paths, *, after_write=None):
    """Replace only existing point collections and roll back even a partial curve write."""
    paths = set(paths)
    source = _indexed(source_action, slot_handle, paths)
    destination = _indexed(destination_action, slot_handle, paths)
    if (source.keys() != destination.keys() or paths != {path for path, _ in source}
            or not all(_editable(c) for c in (*source.values(), *destination.values()))):
        raise ValueError('The selected curves changed or are no longer editable.')
    edits = [(destination[key], _points(source[key]), _points(destination[key]),
              getattr(destination[key], 'active_keyframe_index', None)) for key in source]
    attempted = []
    try:
        for curve, values, backup, active_index in edits:
            attempted.append((curve, backup, active_index))
            _replace(curve, values)
        destination_action.update_tag()
        if after_write is not None:
            after_write()
    except Exception:
        for curve, backup, active_index in reversed(attempted):
            _replace(curve, backup, active_index=active_index)
        destination_action.update_tag()
        raise


def _switch_guards(curves):
    guards = set()
    for curve in curves:
        if not curve.data_path.endswith('["ik_fk"]'):
            continue
        values = _column(curve.keyframe_points, 'co', 'f', 2)
        for i in range(2, len(values), 2):
            if values[i+1] != values[i-1]:
                guards.update((values[i-2], values[i]))
    return guards


def _quaternion_limit(rows, radians):
    # Along each original linear segment, locate the closest approach to zero.
    # The normalized quaternion direction changes by at most asin(error/radius).
    # Physical rotation is twice that angle. Antipodal interpolation through
    # zero has no stable orientation and the reducer then keeps the group.
    minimum = min(math.hypot(*row) for row in rows)
    for left, right in zip(rows, rows[1:]):
        delta = [b - a for a, b in zip(left, right)]
        length_sq = sum(v * v for v in delta)
        if length_sq:
            t = max(0., min(1., -sum(a * v for a, v in zip(left, delta)) / length_sq))
            minimum = min(minimum, math.hypot(*(a + t * v for a, v in zip(left, delta))))
    return minimum * math.sin(min(math.pi, radians) / 2.) if minimum > 1.e-6 else 0.


def _retained(curves, prop, guard_times, position_tolerance, rotation_tolerance):
    if not all(_editable(curve) and len(curve.keyframe_points) > 2 for curve in curves):
        return None
    coords = [_column(curve.keyframe_points, 'co', 'f', 2) for curve in curves]
    times = coords[0][::2]
    if any(axis[::2] != times for axis in coords[1:]) or any(b <= a for a, b in zip(times, times[1:])):
        return None
    guards = {0, len(times)-1}
    for t in guard_times:
        i = bisect_left(times, t)
        if i < len(times) and t == times[i]:
            guards.add(i)
        elif 0 < i < len(times):
            guards.update((i-1, i))
    for curve in curves:
        if curve.extrapolation == 'LINEAR':
            guards.update((1, len(times)-2))
        for i, point in enumerate(curve.keyframe_points):
            if point.interpolation != 'LINEAR':
                return None
            if point.type not in {'KEYFRAME', 'GENERATED'}:
                guards.add(i)
    rows = list(zip(*(axis[1::2] for axis in coords)))
    if prop == 'rotation_quaternion':
        if len(curves) != 4 or [c.array_index for c in curves] != [0, 1, 2, 3]:
            return None
        tolerance = _quaternion_limit(rows, rotation_tolerance)
    elif prop == 'rotation_euler':
        tolerance = rotation_tolerance / math.sqrt(len(curves))
    elif prop == 'location':
        tolerance = position_tolerance
    elif prop == 'scale' and all(row == rows[0] for row in rows):
        return sorted(guards)
    else:
        return None
    # Keep both boundaries of exact holds, including planted-foot segments.
    for i in range(1, len(rows)-1):
        if (rows[i] == rows[i-1]) != (rows[i] == rows[i+1]):
            guards.add(i)
    keep = set(guards)
    ordered = sorted(guards)
    for first, last in zip(ordered, ordered[1:]):
        result = reduce_linear(times[first:last+1], rows[first:last+1], tolerance)
        keep.update(first + i for i in result.indices)
    return sorted(keep)


def simplify(copy_action, slot_handle, bone_names, position_tolerance, rotation_tolerance):
    """Simplify an isolated Action copy. Tolerances are local units and radians."""
    if not all(math.isfinite(v) and v >= 0 for v in (position_tolerance, rotation_tolerance)):
        raise ValueError('Reduction tolerances must be finite and nonnegative.')
    curves = _indexed(copy_action, slot_handle)
    paths = selected_paths(copy_action, slot_handle, bone_names)
    guard_times = _switch_guards(curves.values())
    guard_times.update(marker.frame for marker in copy_action.pose_markers)
    before = after = 0
    changed = []
    groups = {path: [] for path in paths}
    for (path, _axis), curve in sorted(curves.items()):
        if path in groups:
            groups[path].append(curve)
    for path, group in groups.items():
        count = sum(len(curve.keyframe_points) for curve in group)
        before += count
        keep = _retained(group, _curve_name(group[0])[1], guard_times, position_tolerance, rotation_tolerance)
        if keep is None or len(keep) == len(group[0].keyframe_points):
            after += count
            continue
        for curve in group:
            values = _points(curve)
            for name, code, width in _FIELDS:
                values[name] = array(code, (values[name][i*width+j] for i in keep for j in range(width)))
            _replace(curve, values)
        after += len(group) * len(keep)
        changed.append(path)
    if changed:
        copy_action.update_tag()
    return dict(paths=changed, before=before, after=after)
