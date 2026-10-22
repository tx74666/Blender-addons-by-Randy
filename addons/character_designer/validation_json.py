"""Exact JSON data reuse within audited read-only validation calls.

Readers must still obtain current RNA text and run all schema/native proofs.
Each scope owns its mutable decoded values. A bounded process-local LRU stores
only exact raw strings and private marshal bytes made here from finite JSON;
each new scope reconstructs a fresh tree. No binary is accepted from callers
or persisted. Ordinary readers/writers always use fresh json.loads results.
"""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import json
import marshal
import math
import sys
from threading import RLock

_ACTIVE = ContextVar('character_designer_validation_json', default=None)
_MISSING = object()
_MIN_RAW_LENGTH = 4096
_MAX_PACKED_BYTES = 2 * 1024 * 1024
_MAX_PACKED_ENTRIES = 16
_PACKED = OrderedDict()
_PACKED_LOCK = RLock()
_PACKED_BYTES = (sys.getsizeof(_PACKED) + sys.getsizeof(_PACKED_LOCK)
                 + sys.getsizeof(None) + sys.getsizeof(0))
_PACKED_POLICY = None


@contextmanager
def read_scope():
    """Own fresh mutable results; restore an outer scope even after failure."""
    cache = {}
    token = _ACTIVE.set(cache)
    try:
        yield
    finally:
        cache.clear()
        _ACTIVE.reset(token)


def _packable(value):
    # Preserve json.loads' shared non-finite constants (notably NaN equality in
    # lists/dicts), and never marshal arbitrary custom-decoder Python objects.
    pending = [value]
    while pending:
        item = pending.pop()
        kind = type(item)
        if kind is float:
            if not math.isfinite(item):
                return False
        elif kind is list:
            pending.extend(item)
        elif kind is dict:
            if any(type(key) is not str for key in item):
                return False
            pending.extend(item.values())
        elif kind not in (type(None), str, int, bool):
            return False
    return True


def _packed_size():
    """Account the actual OrderedDict allocation and its owned strings/bytes."""
    policy_bytes = (sys.getsizeof(_PACKED_POLICY) + sum(sys.getsizeof(value) for value in _PACKED_POLICY)
                    if _PACKED_POLICY is not None else sys.getsizeof(None))
    return (sys.getsizeof(_PACKED) + sys.getsizeof(_PACKED_LOCK) + policy_bytes + sys.getsizeof(0)
            + sum(sys.getsizeof(raw) + sys.getsizeof(payload) for raw, payload in _PACKED.items()))


def _decoder_policy():
    # Changing Python's parser limits must not turn an originally failing JSON
    # read into a success through an old binary value. Decoder identity also
    # prevents bypassing a newly installed diagnostic/custom decoder.
    return (sys.get_int_max_str_digits(), sys.getrecursionlimit(), id(json.loads))


def _get_packed(raw):
    global _PACKED_BYTES, _PACKED_POLICY
    policy = _decoder_policy()
    with _PACKED_LOCK:
        if policy != _PACKED_POLICY:
            _PACKED.clear()
            _PACKED_POLICY = policy
            _PACKED_BYTES = _packed_size()
        payload = _PACKED.get(raw, _MISSING)
        if payload is not _MISSING:
            _PACKED.move_to_end(raw)
        return payload, policy


def _remember(raw, payload, policy):
    global _PACKED_BYTES
    with _PACKED_LOCK:
        if policy != _PACKED_POLICY or policy != _decoder_policy():
            return  # A concurrent decoder-policy change cannot publish data.
        _PACKED[raw] = payload
        _PACKED.move_to_end(raw)
        _PACKED_BYTES = _packed_size()
        while _PACKED and (len(_PACKED) > _MAX_PACKED_ENTRIES
                           or _PACKED_BYTES > _MAX_PACKED_BYTES):
            _PACKED.popitem(last=False)
            _PACKED_BYTES = _packed_size()


def decode(raw):
    """Read current raw first; only active scopes can use private binary data."""
    global _PACKED_BYTES
    cache = _ACTIVE.get()
    if cache is None or type(raw) is not str:
        return json.loads(raw)
    value = cache.get(raw, _MISSING)
    if value is not _MISSING:
        return value
    eligible = len(raw) >= _MIN_RAW_LENGTH
    payload = _MISSING
    if eligible:
        payload, policy = _get_packed(raw)
    if payload is not _MISSING:
        try:
            value = marshal.loads(payload)
        except (ValueError, TypeError, OverflowError, RecursionError):
            # Only our own bytes enter marshal; a changed runtime limit or an
            # unsupported representation still uses the original JSON path.
            with _PACKED_LOCK:
                _PACKED.pop(raw, None)
                _PACKED_BYTES = _packed_size()
            payload = _MISSING
            eligible = False
    if payload is _MISSING:
        value = json.loads(raw)
        if eligible and _packable(value):
            try:
                payload = marshal.dumps(value)
            except (ValueError, TypeError, OverflowError, RecursionError):
                pass  # Unusual values retain the original parsed JSON result.
            else:
                _remember(raw, payload, policy)
    cache[raw] = value
    return value
