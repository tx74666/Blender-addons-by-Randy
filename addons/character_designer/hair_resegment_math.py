"""Pure arc-length sampling and current-weight migration for Hair resegmentation.

The caller supplies the current owned chain and painted weights. These helpers
never read a saved pre-bind snapshot, normalize a vertex, or infer ownership.
Changing the number of influences cannot promise identical deformed geometry;
the old binding/animation must remain available for comparison and recovery.
"""
from bisect import bisect_right
import math
from collections.abc import Mapping


class HairResegmentMathError(ValueError):
    pass


def _number(value, label):
    if isinstance(value, (bool, str, bytes)):
        raise HairResegmentMathError(label + ' must be a finite number.')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise HairResegmentMathError(label + ' must be a finite number.') from error
    if not math.isfinite(result):
        raise HairResegmentMathError(label + ' must be a finite number.')
    return result


def _sequence(values, label):
    if isinstance(values, (str, bytes, Mapping)):
        raise HairResegmentMathError(label + ' must be a sequence.')
    try:
        return tuple(values)
    except TypeError as error:
        raise HairResegmentMathError(label + ' must be a sequence.') from error


def _positive_lengths(values, label):
    values = tuple(_number(value, label) for value in _sequence(values, label))
    if not values or any(value <= 0. for value in values):
        raise HairResegmentMathError(label + ' must contain strictly positive lengths.')
    try:
        total = math.fsum(values)
    except OverflowError as error:
        raise HairResegmentMathError(label + ' total overflows.') from error
    if not math.isfinite(total) or total <= 0.:
        raise HairResegmentMathError(label + ' total must be finite and positive.')
    prefix = 0.
    for length in values:
        current = prefix + length
        if not math.isfinite(current) or current <= prefix:
            raise HairResegmentMathError(label + ' intervals are not representably increasing.')
        prefix = current
    return values, total


def _points(values):
    points = []
    for value in _sequence(values, 'Polyline points'):
        point = tuple(_number(axis, 'Point coordinate') for axis in _sequence(value, 'Point'))
        if len(point) != 3:
            raise HairResegmentMathError('Each polyline point must have three coordinates.')
        points.append(point)
    if len(points) < 2:
        raise HairResegmentMathError('A polyline needs at least two points.')
    return tuple(points)


def cumulative_lengths(points):
    """Return increasing physical distances, beginning at zero, for 3D points."""
    points = _points(points)
    lengths, _total = _positive_lengths(
        (math.dist(first, second) for first, second in zip(points, points[1:])),
        'Polyline segment lengths')
    distances = [0.]
    for length in lengths:
        distance = distances[-1] + length
        if not math.isfinite(distance) or distance <= distances[-1]:
            raise HairResegmentMathError('Polyline distances are not representably increasing.')
        distances.append(distance)
    return tuple(distances)


def sample_polyline(points, positions, *, distances=None):
    """Sample normalized positions 0..1 against an optional fixed Rest parameter.

    ``distances`` may be the *Rest* cumulative lengths while ``points`` are posed
    heads/tails. This preserves the original parameterization during animation,
    instead of redistributing it according to the current stretched pose.
    """
    points = _points(points)
    if distances is None:
        distances = cumulative_lengths(points)
    else:
        distances = tuple(_number(value, 'Polyline distance')
                          for value in _sequence(distances, 'Polyline distances'))
        if (len(distances) != len(points) or distances[0] != 0.
                or any(b <= a for a, b in zip(distances, distances[1:]))):
            raise HairResegmentMathError('Rest distances must begin at zero and strictly increase for every point.')
    positions = tuple(_number(value, 'Sample position')
                      for value in _sequence(positions, 'Sample positions'))
    if any(value < 0. or value > 1. for value in positions):
        raise HairResegmentMathError('Normalized sample positions must be between zero and one.')
    samples = []
    for position in positions:
        if position == 0.:
            samples.append(points[0])
            continue
        if position == 1.:
            samples.append(points[-1])
            continue
        distance = position * distances[-1]
        index = min(len(points) - 2, max(0, bisect_right(distances, distance) - 1))
        factor = (distance - distances[index]) / (distances[index + 1] - distances[index])
        point = tuple((1. - factor) * a + factor * b
                      for a, b in zip(points[index], points[index + 1]))
        if not all(math.isfinite(axis) for axis in point):
            raise HairResegmentMathError('Interpolated polyline coordinates overflow.')
        samples.append(point)
    return tuple(samples)


def project_parameter(point, restpolyline):
    """Find a vertex's normalized arc parameter on one authenticated Rest chain.

    Only the supplied chain is searched; this is not a mesh/strand ownership
    search. Equally close sections with different arc parameters are ambiguous
    and refused, including self intersections, overlaps and folded-back edges.
    Adjacent edges meeting at the same parameter are safe. The distance tie
    tolerance is 1e-7 of chain length (at least 1e-12 coordinate units), and the
    parameter tie tolerance is 1e-7; this accommodates ordinary float32 inputs.
    """
    point = tuple(_number(axis, 'Vertex coordinate') for axis in _sequence(point, 'Vertex point'))
    if len(point) != 3:
        raise HairResegmentMathError('A vertex point must have three coordinates.')
    points = _points(restpolyline)
    distances = cumulative_lengths(points)
    candidates = []
    try:
        for index, (first, second) in enumerate(zip(points, points[1:])):
            length = distances[index + 1] - distances[index]
            direction = tuple((b - a) / length for a, b in zip(first, second))
            offset = tuple(value - origin for value, origin in zip(point, first))
            along = math.fsum(value * axis for value, axis in zip(offset, direction))
            if not math.isfinite(along):
                raise HairResegmentMathError('Rest projection overflows its finite coordinate range.')
            factor = max(0., min(1., along / length))
            closest = tuple((1. - factor) * a + factor * b for a, b in zip(first, second))
            distance = math.dist(point, closest)
            parameter = (distances[index] + factor * length) / distances[-1]
            if not math.isfinite(distance) or not 0. <= parameter <= 1.:
                raise HairResegmentMathError('Rest projection produced a non-finite distance or parameter.')
            candidates.append((distance, parameter))
    except OverflowError as error:
        raise HairResegmentMathError('Rest projection overflows its finite coordinate range.') from error
    nearest = min(candidates)
    distance_tolerance = max(1e-12, distances[-1] * 1e-7, nearest[0] * 1e-10)
    tied = [parameter for distance, parameter in candidates
            if distance - nearest[0] <= distance_tolerance]
    if max(tied) - min(tied) > 1e-7:
        raise HairResegmentMathError(
            'Ambiguous Rest projection: equally close sections represent different positions on this strand.')
    return nearest[1]


def _centers(lengths, total):
    centers, start = [], 0.
    for length in lengths:
        center = (start + length * .5) / total
        if not 0. < center < 1. or centers and center <= centers[-1]:
            raise HairResegmentMathError('Bone centers are not representably increasing.')
        centers.append(center)
        start += length
    return tuple(centers)


def influence_map(old_lengths, new_lengths):
    """Return sparse old-center→new-center interpolation rows, with edge clamp.

    Each row contains one or two ``(new_index, fraction)`` pairs whose fractions
    sum to one. Lengths are physical Rest intervals, not guessed vertex indices.
    """
    old_lengths, old_total = _positive_lengths(old_lengths, 'Old Rest lengths')
    new_lengths, new_total = _positive_lengths(new_lengths, 'New Rest lengths')
    old_centers, new_centers = _centers(old_lengths, old_total), _centers(new_lengths, new_total)
    rows = []
    for center in old_centers:
        if center <= new_centers[0]:
            rows.append(((0, 1.),))
        elif center >= new_centers[-1]:
            rows.append(((len(new_centers) - 1, 1.),))
        else:
            upper = bisect_right(new_centers, center)
            lower = upper - 1
            fraction = (center - new_centers[lower]) / (new_centers[upper] - new_centers[lower])
            rows.append(((lower, 1. - fraction), (upper, fraction)))
    return tuple(rows)


def remap_weights(weights, old_lengths, new_lengths):
    """Migrate one vertex's current strand weights, preserving its total.

    The supplied weights may sum to more or less than one; they are never
    normalized. Floating point correction goes only to the largest new bucket.
    """
    old_lengths, _ = _positive_lengths(old_lengths, 'Old Rest lengths')
    new_lengths, _ = _positive_lengths(new_lengths, 'New Rest lengths')
    weights = tuple(_number(value, 'Current painted weight')
                    for value in _sequence(weights, 'Current painted weights'))
    if len(weights) != len(old_lengths) or any(value < 0. for value in weights):
        raise HairResegmentMathError('Current painted weights must be nonnegative and match the old bone count.')
    rows = influence_map(old_lengths, new_lengths)
    try:
        total = math.fsum(weights)
        buckets = [[] for _ in new_lengths]
        for weight, row in zip(weights, rows):
            for index, fraction in row:
                buckets[index].append(weight * fraction)
        result = [math.fsum(bucket) for bucket in buckets]
    except OverflowError as error:
        raise HairResegmentMathError('The current strand weight total overflows.') from error
    if not math.isfinite(total):
        raise HairResegmentMathError('The current strand weight total must be finite.')
    largest = max(range(len(result)), key=result.__getitem__)
    result[largest] = total - math.fsum(value for i, value in enumerate(result) if i != largest)
    if any(not math.isfinite(value) or value < 0. for value in result):
        raise HairResegmentMathError('Weight migration produced an invalid influence.')
    return tuple(result)


def remap_vertex_influence(total, t, new_lengths):
    """Spatially redistribute one vertex's current strand total to new centers.

    Returns one dense tuple with at most two nonzero influences. Using each
    vertex's authenticated Rest parameter gives added bones a spatial influence
    even when the previous strand had only one painted bone. No other group's
    weight participates in this calculation. The result is not normalized to one.
    """
    total = _number(total, 'Current strand weight total')
    t = _number(t, 'Vertex Rest parameter')
    if total < 0. or not 0. <= t <= 1.:
        raise HairResegmentMathError('Strand total must be nonnegative and Rest parameter must be between zero and one.')
    lengths, length_total = _positive_lengths(new_lengths, 'New Rest lengths')
    centers = _centers(lengths, length_total)
    result = [0.] * len(lengths)
    if t <= centers[0]:
        result[0] = total
    elif t >= centers[-1]:
        result[-1] = total
    else:
        upper = bisect_right(centers, t)
        lower = upper - 1
        fraction = (t - centers[lower]) / (centers[upper] - centers[lower])
        result[lower] = total * (1. - fraction)
        result[upper] = total - result[lower]
    return tuple(result)


def _names(values, label, *, allow_empty=False):
    names = _sequence(values, label)
    if ((not allow_empty and not names) or any(type(name) is not str or not name for name in names)
            or len(set(names)) != len(names)):
        raise HairResegmentMathError(label + ' must contain unique nonempty strings.')
    return names


def remap_vertex_groups(current_groups, old_names, new_names, old_lengths, new_lengths, *, locked_names=()):
    """Copy one vertex's groups, replacing only this authenticated strand's data.

    Ownership and mesh identity are deliberately caller responsibilities. Foreign
    groups are carried through verbatim; affected locked groups or occupied new
    names are refused before any output is produced. This function mutates none
    of its inputs and returns only nonzero new influences.
    """
    if not isinstance(current_groups, Mapping):
        raise HairResegmentMathError('Current vertex groups must be a mapping.')
    old_names, new_names = _names(old_names, 'Old bone names'), _names(new_names, 'New bone names')
    locked_names = _names(locked_names, 'Locked group names', allow_empty=True)
    old_lengths, _ = _positive_lengths(old_lengths, 'Old Rest lengths')
    new_lengths, _ = _positive_lengths(new_lengths, 'New Rest lengths')
    if len(old_names) != len(old_lengths) or len(new_names) != len(new_lengths):
        raise HairResegmentMathError('Bone names and Rest lengths have different counts.')
    weights = tuple(current_groups.get(name, 0.) for name in old_names)
    remapped = remap_weights(weights, old_lengths, new_lengths)
    if old_names == new_names and old_lengths == new_lengths:
        return dict(current_groups)
    affected = set(old_names) | set(new_names)
    if affected.intersection(locked_names):
        raise HairResegmentMathError('Unlock the affected Hair groups before changing their influences.')
    if (set(new_names) - set(old_names)).intersection(current_groups):
        raise HairResegmentMathError('A new Hair group name is already occupied by unrelated vertex data.')
    result = {name: weight for name, weight in current_groups.items() if name not in set(old_names)}
    result.update((name, weight) for name, weight in zip(new_names, remapped) if weight > 0.)
    return result
