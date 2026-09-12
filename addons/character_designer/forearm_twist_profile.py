"""Pure helpers for captured loop profiles and explicitly bounded correction.

Ring positions are physical rest projections along the original forearm, not
uniform index fractions. These functions return values or copies and never
modify a captured profile, a current-loop selection, or a Blender datablock.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from numbers import Real


class ForearmProfileError(ValueError):
    """A saved profile or requested range is not a finite, ordered capture."""


def _number(value, label, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ForearmProfileError(f"{label} must be a finite number.")
    try:
        result = float(value)
    except (OverflowError, ValueError, TypeError) as exc:
        raise ForearmProfileError(f"{label} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ForearmProfileError(f"{label} must be a finite number.")
    if (minimum is not None and result < minimum) or (maximum is not None and result > maximum):
        raise ForearmProfileError(f"{label} is outside its supported range.")
    return result


def _index(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ForearmProfileError(f"{label} must be a whole loop index.")
    return value


def profile_knots(rings):
    """Validate and return immutable (rest position, share) knots in saved order."""
    if not isinstance(rings, Sequence) or isinstance(rings, (str, bytes)) or len(rings) < 2:
        raise ForearmProfileError("A profile needs at least two captured loops.")
    knots = []
    for ring in rings:
        if not isinstance(ring, Mapping) or "position" not in ring or "ratio" not in ring:
            raise ForearmProfileError("Each captured loop needs a position and ratio.")
        position = _number(ring["position"], "Loop position")
        ratio = _number(ring["ratio"], "Loop ratio", minimum=0.0, maximum=1.0)
        if knots and position <= knots[-1][0]:
            raise ForearmProfileError("Captured loop positions must be strictly increasing.")
        knots.append((position, ratio))
    return tuple(knots)


def validate_range(rings, start, end):
    """Return physical boundary positions for two distinct inclusive ring indices."""
    knots = profile_knots(rings)
    start, end = _index(start, "Range start"), _index(end, "Range end")
    if not 0 <= start < end < len(knots):
        raise ForearmProfileError("Choose distinct start and end loops in captured order.")
    if not math.isfinite(knots[end][0] - knots[start][0]):
        raise ForearmProfileError("The selected loop span is not finite.")
    return knots[start][0], knots[end][0]


def record_range(record):
    """Read an explicit persisted range; callers decide when legacy data migrates."""
    if not isinstance(record, Mapping) or not all(key in record for key in ("rings", "range_start", "range_end")):
        raise ForearmProfileError("This profile has no explicit saved loop range.")
    start, end = record["range_start"], record["range_end"]
    validate_range(record["rings"], start, end)
    return start, end


def with_range(record, start, end):
    """Copy a record with explicit bounds, preserving loop data and current selection."""
    if not isinstance(record, Mapping) or "rings" not in record:
        raise ForearmProfileError("The captured loop profile is missing.")
    validate_range(record["rings"], start, end)
    return {**record, "range_start": start, "range_end": end}


def ease_ratio(position, k=0.4):
    """Blend linear and smoothstep defaults on original forearm rest t in [0, 1]."""
    t = min(1.0, max(0.0, _number(position, "Rest position")))
    k = _number(k, "Default softness", minimum=0.0, maximum=1.0)
    smooth = t * t * (3.0 - 2.0 * t)
    return (1.0 - k) * t + k * smooth


def interpolate_ratio(rings, position):
    """Interpolate neighboring saved knots linearly, without smoothing overshoot.

    Outside the captured knot span, retain its nearest endpoint ratio. This is
    profile sampling only: range_influence independently gates correction.
    """
    knots = profile_knots(rings)
    position = _number(position, "Sample position")
    if position <= knots[0][0]:
        return knots[0][1]
    if position >= knots[-1][0]:
        return knots[-1][1]
    right = bisect.bisect_right([knot[0] for knot in knots], position)
    left_position, left_ratio = knots[right - 1]
    right_position, right_ratio = knots[right]
    amount = (position - left_position) / (right_position - left_position)
    return left_ratio + amount * (right_ratio - left_ratio)


def profile_angle(rings, position, angle):
    """Multiply the interpolated share by the signed angle, in radians, once."""
    angle = _number(angle, "Twist angle")
    return interpolate_ratio(rings, position) * angle


def range_influence(position, rings, start, end, transition=0.1):
    """Gate correction to the open interval between selected boundary positions.

    Each smooth fade occupies ``transition * selected_span`` entirely inside
    the range. A fraction of 0 means a hard interior mask; the exact boundary
    vertices still receive zero correction. Fractions up to 0.5 are accepted.
    """
    first, last = validate_range(rings, start, end)
    position = _number(position, "Sample position")
    transition = _number(transition, "Boundary transition", minimum=0.0, maximum=0.5)
    if position <= first or position >= last:
        return 0.0
    if transition == 0.0:
        return 1.0
    span = last - first
    amount = min(1.0, ((position - first) / span) / transition, ((last - position) / span) / transition)
    return amount * amount * (3.0 - 2.0 * amount)


def apply_default(rings, start, end, k=0.4):
    """Explicitly replace only selected interior shares, preserving authored anchors.

    Normalize each physical rest position within the selected interval, apply
    easing, then blend the existing boundary ratios. This keeps physical
    spacing, handles increasing or decreasing authored profiles, and never
    creates shares beyond either selected anchor. Changing k alone has no
    effect until this function is explicitly called and its result saved.
    """
    first, last = validate_range(rings, start, end)
    k = _number(k, "Default softness", minimum=0.0, maximum=1.0)
    knots = profile_knots(rings)
    copied = [{**ring, **({"vertices": list(ring["vertices"])} if "vertices" in ring else {})} for ring in rings]
    first_ratio, last_ratio = knots[start][1], knots[end][1]
    for index in range(start + 1, end):
        amount = ease_ratio((knots[index][0] - first) / (last - first), k)
        copied[index]["ratio"] = first_ratio + amount * (last_ratio - first_ratio)
    return copied


def batch_indices(current, count, stride, end):
    """Select up to count distinct rings from current, with an inclusive end limit.

    Oversized counts are clamped to the valid selection size. The return value
    is an absolute tuple of indices; it never accumulates previous edits.
    """
    current = _index(current, "Current loop")
    count = _index(count, "Batch count")
    stride = _index(stride, "Batch interval")
    end = _index(end, "Range end")
    if current < 0 or end < current:
        raise ForearmProfileError("Current loop must lie within the batch range.")
    if count < 1 or stride < 1:
        raise ForearmProfileError("Batch count and interval must be at least one.")
    available = (end - current) // stride + 1
    return tuple(current + index * stride for index in range(min(count, available)))
