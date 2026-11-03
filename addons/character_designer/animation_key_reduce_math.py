"""Deterministic reduction of grouped, piecewise LINEAR vector keys.

Only original indices escape this module. Authored values, including raw
quaternion components, are never changed, normalized or sign-adjusted.

The input times must be the union knot grid of the grouped channels. Between
adjacent original knots the difference from a retained LINEAR reconstruction
is affine. Its Euclidean norm is convex, so its continuous maximum occurs at
an original knot. A final outward-rounded reconstruction check bounds those
knots; no sampling between them is needed for this LINEAR-only contract.
"""

from collections.abc import Sequence
from dataclasses import dataclass
import math
from numbers import Real

MAX_KEYS = 200_001
MAX_COMPONENTS = 16
MAX_WORK = 1_000_000


@dataclass(frozen=True, slots=True)
class ReductionResult:
    indices: tuple[int, ...]
    max_error: float
    work: int
    budget_exhausted: bool
    reason: str


class _NumericGuard(Exception):
    pass


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(label + ' must be a finite real number.')
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(label + ' must be a finite real number.') from exc
    if not math.isfinite(result):
        raise ValueError(label + ' must be a finite real number.')
    return result


def _sequence(value, label):
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(label + ' must be a sequence.')
    return value


def _validate(times, values, tolerance, max_work):
    times, values = _sequence(times, 'times'), _sequence(values, 'values')
    if len(times) != len(values) or len(times) > MAX_KEYS:
        raise ValueError('times and values need equal lengths within MAX_KEYS.')
    tolerance = _finite(tolerance, 'tolerance')
    if tolerance < 0:
        raise ValueError('tolerance must be nonnegative.')
    if type(max_work) is not int or not 0 <= max_work <= MAX_WORK:
        raise ValueError('max_work must be an integer between zero and MAX_WORK.')
    clock = tuple(_finite(value, 'time') for value in times)
    if any(right <= left for left, right in zip(clock, clock[1:])):
        raise ValueError('times must be strictly increasing after float conversion.')
    rows = []
    dimension = None
    for row in values:
        row = _sequence(row, 'Each value')
        if not 1 <= len(row) <= MAX_COMPONENTS or dimension is not None and len(row) != dimension:
            raise ValueError('Values need one consistent dimension within MAX_COMPONENTS.')
        dimension = len(row)
        rows.append(tuple(_finite(value, 'component') for value in row))
    return clock, tuple(rows), tolerance


def _checked(value):
    if not math.isfinite(value):
        raise _NumericGuard
    return value


def _alpha(times, first, last, index):
    span = _checked(times[last] - times[first])
    fraction = _checked((times[index] - times[first]) / span)
    if not 0 <= fraction <= 1:
        raise _NumericGuard
    return fraction


def _distance(times, rows, first, last, index):
    fraction = _alpha(times, first, last, index)
    # This convex form avoids an unnecessary overflow in right-left.
    differences = [rows[index][axis] - ((1. - fraction) * left + fraction * right)
                   for axis, (left, right) in enumerate(zip(rows[first], rows[last]))]
    return _checked(math.hypot(*differences))


def _subtract_bounds(left, right):
    if left == right:
        return 0., 0.
    value = _checked(left - right)
    return (_checked(math.nextafter(value, -math.inf)),
            _checked(math.nextafter(value, math.inf)))


def _alpha_bounds(times, first, last, index):
    numerator = _subtract_bounds(times[index], times[first])
    denominator = _subtract_bounds(times[last], times[first])
    if denominator[0] <= 0:
        raise _NumericGuard
    lower = math.nextafter(numerator[0] / denominator[1], -math.inf)
    upper = math.nextafter(numerator[1] / denominator[0], math.inf)
    # An interior knot's exact fraction is also known to lie in [0, 1].
    return max(0., _checked(lower)), min(1., _checked(upper))


def _reconstruction_bound(times, rows, first, last, index):
    fraction = _alpha_bounds(times, first, last, index)
    errors = []
    for actual, left, right in zip(rows[index], rows[first], rows[last]):
        if left == right:
            if actual == left:
                errors.append(0.)
            else:
                errors.append(_checked(math.nextafter(abs(_checked(actual - left)), math.inf)))
            continue
        difference = _subtract_bounds(right, left)
        products = [_checked(delta * alpha) for delta in difference for alpha in fraction]
        lower = _checked(math.nextafter(min(products), -math.inf))
        upper = _checked(math.nextafter(max(products), math.inf))
        lower = _checked(math.nextafter(_checked(left + lower), -math.inf))
        upper = _checked(math.nextafter(_checked(left + upper), math.inf))
        error = max(abs(_checked(actual - lower)), abs(_checked(actual - upper)))
        errors.append(_checked(math.nextafter(error, math.inf)))
    error = _checked(math.hypot(*errors))
    return _checked(math.nextafter(error, math.inf)) if error else 0.


def reduce_linear(times, values, tolerance, *, max_work=MAX_WORK):
    """Return retained original indices and a continuous Euclidean error bound.

    ``times`` and vector ``values`` are equal-length sequences of finite real
    numbers; times are strictly increasing and vectors share one dimension.
    The caller must supply the complete union knot grid and LINEAR semantics.
    Rotation-aware tolerances and unsupported interpolation policy belong to
    the caller; this module treats quaternion components as ordinary raw data.

    Positive tolerance uses iterative RDP, splitting at the earliest maximum
    error on a tie. First/last keys and original key times are retained. Zero
    tolerance conservatively disables reduction. ``max_error`` describes the
    returned reconstruction, not a discarded candidate; all-key fallbacks have
    exact zero error. Work counts knot/segment distance evaluations, including
    the final bound check, and cannot exceed ``max_work``. Invalid inputs raise
    ValueError. Numeric uncertainty or exhausted work retains every input key.
    """
    times, rows, tolerance = _validate(times, values, tolerance, max_work)
    count = len(times)
    original = tuple(range(count))
    work = 0

    def unchanged(reason, exhausted=False):
        return ReductionResult(original, 0., work, exhausted, reason)

    if count <= 2:
        return unchanged('unchanged')
    if tolerance == 0:
        return unchanged('zero_tolerance')

    retained = {0, count - 1}
    pending = [(0, count - 1)]
    try:
        while pending:
            first, last = pending.pop()
            worst, split = -1., None
            for index in range(first + 1, last):
                if work >= max_work:
                    return unchanged('work_limit', True)
                work += 1
                error = _distance(times, rows, first, last, index)
                if error > worst:
                    worst, split = error, index
            if split is not None and worst > tolerance:
                retained.add(split)
                pending.extend(((split, last), (first, split)))
        indices = tuple(sorted(retained))
        if len(indices) == count:
            return unchanged('unchanged')

        maximum = 0.
        for first, last in zip(indices, indices[1:]):
            for index in range(first + 1, last):
                if work >= max_work:
                    return unchanged('work_limit', True)
                work += 1
                error = _reconstruction_bound(times, rows, first, last, index)
                if error > tolerance:
                    return unchanged('reconstruction_guard')
                maximum = max(maximum, error)
        return ReductionResult(indices, maximum, work, False, 'reduced')
    except (ArithmeticError, _NumericGuard):
        return unchanged('numeric_guard')
