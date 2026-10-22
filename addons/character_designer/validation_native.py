"""Native read reuse only inside one audited, synchronous validation call.

The caller must not mutate or evaluate Blender data while this scope is active.
Only ownership tags and the strict constraint-discovery pass are reused; every
structure, Rest, mute, driver and widget check still runs at its original site.
Nothing survives scope exit, and ordinary callers always read current RNA.
"""
from contextlib import contextmanager
from contextvars import ContextVar


_ACTIVE = ContextVar('character_designer_validation_native', default=None)


@contextmanager
def read_scope():
    """Create a separate read snapshot, including for nested validation calls."""
    cache = {}
    token = _ACTIVE.set(cache)
    try:
        yield
    finally:
        # Release borrowed RNA references even if the validation is rejected.
        cache.clear()
        _ACTIVE.reset(token)


def iter_owned_bones(armature, owner_key, owner_value):
    """Preserve bone order, lazy ordinary reads and non-string comparisons."""
    cache = _ACTIVE.get()
    if cache is None:
        for bone in armature.data.bones:
            if bone.get(owner_key) == owner_value:
                yield bone
        return
    key = ('owners', id(armature), owner_key)
    entry = cache.get(key)
    if entry is None:
        # Do not hash arbitrary IDProperty owner values: malformed arrays and
        # groups retain the original equality-to-owner behavior and error order.
        entries = tuple((bone, bone.get(owner_key)) for bone in armature.data.bones)
        entry = (armature, entries)
        cache[key] = entry
    for bone, owner in entry[1]:
        if owner == owner_value:
            yield bone


def strict_constraint_records(armature, discover):
    """Reuse successful discovery, never a passed inventory or an exception.

    The discovery function validates registry schema, exact names/counts and
    constraint types. Its consumers remain read-only and independently inspect
    current native fields. A new list prevents a consumer changing cache order.
    """
    cache = _ACTIVE.get()
    if cache is None:
        return discover(armature)
    key = ('strict_constraints', id(armature), discover)
    entry = cache.get(key)
    if entry is None:
        records = discover(armature)
        entry = (armature, tuple(records))
        cache[key] = entry
    return list(entry[1])
