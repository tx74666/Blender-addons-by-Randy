"""Reduce newly solved LINEAR controller samples without altering their values.

This is a proposal for the solver's native comparison, never permission to
publish an approximation. The caller must verify the reconstructed motion on
its disposable rig, and retain the complete samples if that comparison fails.
Existing artist FCurves and their handles are outside this module's scope.
"""
import json
import math
import re

from .animation_key_reduce_math import reduce_linear

_TRANSFORM = re.compile(
    r'^(pose\.bones\[("(?:[^"\\]|\\.)*")\])\.'
    r'(location|rotation_euler|rotation_quaternion|rotation_axis_angle|scale)$')
_WIDTH = {'location': 3, 'rotation_euler': 3, 'rotation_quaternion': 4,
          'rotation_axis_angle': 4, 'scale': 3}
MAX_TIMES = 20001
MAX_POINTS = 2_000_000


def _finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + ' must be finite numeric data.')
    return float(value)


def compress(channels, frame_times, *, position_tolerance=1e-5,
             rotation_tolerance=1e-6, scalar_tolerance=1e-7):
    """Return original sample indices, synchronized within transform groups.

    Tolerances are raw local component-vector distances, not a world-pose
    guarantee. Raw quaternion signs/lengths remain exactly as supplied. A
    varying unknown scalar channel is retained in full. Constant switch
    properties reduce to their endpoints; a caller writes them as CONSTANT.
    Transform groups retain the first/last two samples to protect loop seams.
    """
    tolerances = dict(location=_finite(position_tolerance, 'Position tolerance'),
                      rotation=_finite(rotation_tolerance, 'Rotation tolerance'),
                      scalar=_finite(scalar_tolerance, 'Scalar tolerance'))
    if any(value < 0 for value in tolerances.values()):
        raise ValueError('Controller reduction tolerances must be nonnegative.')
    if not isinstance(channels, dict) or not channels or not 1 <= len(frame_times) <= MAX_TIMES:
        raise ValueError('Choose a bounded nonempty set of controller samples.')
    times = tuple(_finite(value, 'Sample time') for value in frame_times)
    if any(last <= first for first, last in zip(times, times[1:])):
        raise ValueError('Controller sample times must be strictly increasing.')
    count = len(times)
    if len(channels) * count > MAX_POINTS:
        raise ValueError('This controller clip exceeds the key reduction bound.')
    original = tuple(range(count))
    indices, groups, vectors = {}, {}, {}
    for key, samples in channels.items():
        if (not isinstance(key, tuple) or len(key) != 2 or type(key[0]) is not str
                or type(key[1]) is not int or key[1] < 0 or len(samples) != count):
            raise ValueError('A controller channel has an invalid path, index or sample count.')
        values = tuple(_finite(value, 'Controller sample') for value in samples)
        indices[key], vectors[key] = original, values
        match = _TRANSFORM.fullmatch(key[0])
        if match:
            # Parse the quoted identifier without guessing bone-name escapes.
            json.loads(match[2])
            groups.setdefault((match[1], match[3]), {})[key[1]] = key
        elif values and all(value == values[0] for value in values):
            indices[key] = (0,) if count == 1 else (0, count - 1)

    maximum, work, fallbacks = 0., 0, 0
    for (_prefix, prop), axes in groups.items():
        width = _WIDTH[prop]
        if set(axes) != set(range(width)) or prop == 'rotation_axis_angle':
            continue
        rows = tuple(tuple(vectors[axes[axis]][i] for axis in range(width)) for i in range(count))
        if all(row == rows[0] for row in rows):
            retained = (0,) if count == 1 else (0, count - 1)
        elif count <= 4:
            retained = original
        else:
            tolerance = tolerances['location' if prop == 'location' else
                                   'rotation' if prop.startswith('rotation_') else 'scalar']
            # Separate exact seam segments from the reducible interior.
            result = reduce_linear(times[1:-1], rows[1:-1], tolerance)
            work += result.work
            maximum = max(maximum, result.max_error)
            fallbacks += int(result.budget_exhausted or result.reason in
                             {'numeric_guard', 'reconstruction_guard'})
            retained = (0, *(index + 1 for index in result.indices), count - 1)
        for key in axes.values():
            indices[key] = retained
    return dict(indices=indices, before_points=len(channels) * count,
                after_points=sum(len(value) for value in indices.values()),
                component_error_bound=maximum, work=work, fallback_groups=fallbacks,
                tolerances=tolerances, native_verification_required=True)
