"""Choose editable output-key indices without changing validation samples.

This module only proposes subsets of existing samples. Every original sample
and interval probe still needs the caller's native playback checks. Selecting
an endpoint representative never changes the source clip range or clock.
"""
import math
import struct

MAX_TIMES = 20001
MAX_POINTS = 2_000_000
MIN_KEY_GAP = .01


def _require(value, message):
    if not value:
        raise ValueError(message)


def _finite(value):
    _require(type(value) in (int, float), 'Controller samples must be finite numeric data.')
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError('Controller samples must be finite numeric data.') from error
    _require(math.isfinite(result), 'Controller samples must be finite numeric data.')
    return result


def _times(values):
    _require(hasattr(values, '__len__') and 2 <= len(values) <= MAX_TIMES,
             'Choose a bounded clip with at least two validation times.')
    result = tuple(_finite(value) for value in values)
    _require(all(first < last for first, last in zip(result, result[1:])),
             'Controller validation times must be strictly increasing.')
    return result


def _native_time(value):
    try:
        result = struct.unpack('f', struct.pack('f', value))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError('Controller time exceeds finite native storage.') from error
    _require(math.isfinite(result), 'Controller time exceeds finite native storage.')
    return result


def legal_indices(frame_times):
    """Return at least two original indices, with native gaps strictly >.01.

    Existing exact integer samples own their inclusive .01 edit zones. A
    missing native integer in such a zone refuses instead of inventing a solve
    or changing a sample's value. Existing requested times use the writer's
    normal float32 storage, even when the request itself is not float32 exact.
    First/last native keys must stay inside and within .01 of the original
    bounds; CONSTANT extension still needs verification at all original times.
    """
    times = _times(frame_times)
    native = tuple(_native_time(value) for value in times)
    lookup = {}
    for index, value in enumerate(native):
        if times[0] <= value <= times[-1]:
            lookup.setdefault(value, []).append(index)
    mandatory = set()
    for value in native:
        integer = float(round(value))
        if abs(value - integer) <= MIN_KEY_GAP:
            _require(integer in lookup,
                     'An integer edit zone has no exact existing integer sample.')
            # Prefer an exact requested integer when present; otherwise retain
            # the existing sample already stored there by the native writer.
            index = min(lookup[integer], key=lambda i: (times[i] != integer, abs(times[i]-integer), i))
            mandatory.add(index)
    anchors = tuple(sorted(mandatory))
    anchor = 0
    selected = []
    for index, value in enumerate(native):
        while anchor < len(anchors) and anchors[anchor] < index:
            anchor += 1
        if index in mandatory:
            _require(not selected or value - native[selected[-1]] > MIN_KEY_GAP,
                     'Required integer edit keys are too close in native storage.')
            selected.append(index)
            continue
        if not times[0] <= value <= times[-1]:
            continue
        if selected and value - native[selected[-1]] <= MIN_KEY_GAP:
            continue
        if anchor < len(anchors) and native[anchors[anchor]] - value <= MIN_KEY_GAP:
            continue
        selected.append(index)
    _require(len(selected) >= 2, 'Editable output needs at least two legal key times.')
    _require(0. <= native[selected[0]] - times[0] <= MIN_KEY_GAP
             and 0. <= times[-1] - native[selected[-1]] <= MIN_KEY_GAP,
             'Legal keys cannot cover the original clip bounds with a bounded extension.')
    _require(all(native[last] - native[first] > MIN_KEY_GAP
                 for first, last in zip(selected, selected[1:])),
             'Editable output contains nearby native keys.')
    return tuple(selected)


def compress_legal(channels, frame_times, curve_module, *, auto_reduce=True):
    """Map the existing reducer's legal-sample indices back to original V.

    ``indices`` and ``fallback_indices`` always refer to the original arrays.
    The fallback is the complete legal subset, never the original unsafe key
    roster. Both proposals require native verification over the unchanged V.
    All newly written channels use this subset, including incomplete transform
    groups and scalar channels; existing source FCurves are outside this API.
    """
    _require(type(auto_reduce) is bool, 'Automatic controller key reduction must be on or off.')
    times = _times(frame_times)
    _require(isinstance(channels, dict) and channels
             and len(channels) * len(times) <= MAX_POINTS,
             'Choose a bounded nonempty set of controller channels.')
    legal = legal_indices(times)
    native_times = tuple(_native_time(times[index]) for index in legal)
    selected = {}
    for key, values in channels.items():
        _require(isinstance(key, tuple) and len(key) == 2 and type(key[0]) is str
                 and type(key[1]) is int and key[1] >= 0
                 and hasattr(values, '__len__') and len(values) == len(times),
                 'A controller channel has an invalid path, index or sample count.')
        finite = tuple(_finite(value) for value in values)
        selected[key] = tuple(finite[index] for index in legal)
    compressed = (curve_module.compress(selected, native_times) if auto_reduce else
        dict(indices={key: tuple(range(len(legal))) for key in channels},
             component_error_bound=0., work=0, fallback_groups=0, tolerances=None))
    _require(isinstance(compressed, dict) and isinstance(compressed.get('indices'), dict)
             and set(compressed['indices']) == set(channels),
             'The controller reducer returned an incomplete channel map.')
    mapped = {}
    for key, indices in compressed['indices'].items():
        _require(isinstance(indices, (tuple, list)) and 2 <= len(indices) <= len(legal)
                 and all(type(index) is int and 0 <= index < len(legal) for index in indices)
                 and all(first < last for first, last in zip(indices, indices[1:]))
                 and indices[0] == 0 and indices[-1] == len(legal) - 1,
                 'The controller reducer returned invalid or uncovered key indices.')
        mapped[key] = tuple(legal[index] for index in indices)
    return dict(compressed, indices=mapped, legal_indices=legal,
        fallback_indices={key: legal for key in channels},
        before_points=len(channels) * len(times), legal_points=len(channels) * len(legal),
        after_points=sum(len(indices) for indices in mapped.values()),
        validation_sample_count=len(times), legal_sample_count=len(legal),
        legal_native_times=native_times, legal_key_time_range=[native_times[0], native_times[-1]],
        auto_reduce_requested=auto_reduce, compression_attempted=auto_reduce,
        component_error_scope='legal samples only; complete original V needs native verification',
        native_verification_required=True)
