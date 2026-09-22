"""Frozen saved-reference frames shared by bank UI and overlays.

This cache is for viewing only. Modeling/rigging consumers still call frame()
directly and perform the complete validation before writes.
"""
from types import SimpleNamespace

from . import finger_bank as bank, finger_definition as definition

_domain = None
_entries = {}
_dependencies = set()
_serial = 0


def clear():
    global _domain, _serial
    _domain = None
    _entries.clear()
    _dependencies.clear()
    _serial += 1


def _id(value):
    if value is None: return 0
    pointer = value.as_pointer()
    _dependencies.add(pointer)
    return pointer


def _matrix(value):
    return tuple(x for row in value for x in row)


def _source(obj):
    if obj is None: return ()
    data = obj.data
    keys = getattr(data, 'shape_keys', None)
    return (_id(obj), _id(data), _id(keys), _matrix(obj.matrix_world))


def token(context, obj):
    """Cheap RNA-only domain check; safe in panel and GPU draw callbacks."""
    global _domain
    source = _source(obj)
    mirrors = tuple((_id(m.mirror_object), _matrix(m.mirror_object.matrix_world))
                    for m in obj.modifiers if m.type == 'MIRROR' and m.use_axis[0] and m.mirror_object)
    key = (context.scene.as_pointer(), source, mirrors,
           obj.character_designer_finger_bank.survey)
    if key != _domain:
        clear()
        _domain = key
        # clear() also removed the dependencies collected while building key.
        _source(obj)
        for m in obj.modifiers:
            if m.type == 'MIRROR' and m.mirror_object: _id(m.mirror_object)
    return _serial


def key(guide, error=''):
    """Include reference content, not just an optional caller-managed revision."""
    return (_source(guide.source), guide.record, guide.revision,
            guide.use_basis, guide.confirmed, guide.flip_bend,
            _source(guide.bend_source), guide.bend_record)


def lookup(guide, error=''):
    entry = _entries.get(guide.as_pointer())
    return entry[1] if entry and entry[0] == key(guide, error) else None


def evaluate(context, obj, guide, error='', *, reader=None):
    """Read saved coordinates only, including cold display after reload/edit."""
    global _serial
    token(context, obj)
    found = lookup(guide, error)
    if found is not None: return found
    try:
        scoped = SimpleNamespace(scene=context.scene, finger_definition=guide, finger_bank_object=obj)
        result = definition.saved_frame(scoped), ''
    except (ValueError, RuntimeError, KeyError, IndexError, ReferenceError, AttributeError) as exc:
        result = None, str(exc)
    _entries[guide.as_pointer()] = (key(guide, error), result)
    _serial += 1
    return result


def evaluate_many(context, obj, names):
    """Restore missing display records without snapshots or live validation."""
    result = {}
    for name in names:
        slot = obj.character_designer_finger_bank.slots.get(name)
        if slot and slot.guide.record:
            result[name] = evaluate(context, obj, slot.guide)
    return result


def affected(depsgraph):
    """Mesh edits do not invalidate explicitly saved display references."""
    return False
