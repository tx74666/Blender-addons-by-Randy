"""Event-invalidated, bounded read-only frames shared by bank UI and overlays.

This cache is for viewing only. Modeling/rigging consumers still call frame()
directly and perform the complete validation before writes.
"""
from types import SimpleNamespace

from . import finger_bank as bank, finger_definition as definition

_domain = None
_entries = {}
_dependencies = set()
_health_checked = False
_serial = 0


def clear():
    global _domain, _health_checked, _serial
    _domain, _health_checked = None, False
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
    return (_id(obj), _id(data), _id(keys), obj.mode,
            obj.active_shape_key_index if obj.type == 'MESH' else 0,
            _matrix(obj.matrix_world))


def token(context, obj):
    """Cheap RNA-only domain check; safe in panel and GPU draw callbacks."""
    global _domain
    from . import finger_layout
    layout = finger_layout.state(context)
    source = _source(obj)
    mirrors = tuple((_id(m.mirror_object), _matrix(m.mirror_object.matrix_world))
                    for m in obj.modifiers if m.type == 'MIRROR' and m.use_axis[0] and m.mirror_object)
    key = (context.scene.as_pointer(), source, mirrors,
           obj.character_designer_finger_bank.survey,
           _id(layout.mesh), _id(layout.source), layout.applied, layout.record, layout.signature)
    if key != _domain:
        clear()
        _domain = key
        # clear() also removed the dependencies collected while building key.
        _source(obj)
        _id(layout.mesh); _id(layout.source)
        for m in obj.modifiers:
            if m.type == 'MIRROR' and m.mirror_object: _id(m.mirror_object)
    return _serial


def key(guide, error=''):
    """Include reference content, not just an optional caller-managed revision."""
    return (_source(guide.source), guide.record, guide.revision,
            guide.use_basis, guide.confirmed, guide.flip_bend,
            _source(guide.bend_source), guide.bend_record, error)


def lookup(guide, error=''):
    entry = _entries.get(guide.as_pointer())
    return entry[1] if entry and entry[0] == key(guide, error) else None


def evaluate(context, obj, guide, error=''):
    """Call outside drawing. Cache successes AND failures until inputs change."""
    global _health_checked, _serial
    token(context, obj)
    found = lookup(guide, error)
    if found is not None: return found
    try:
        scoped = SimpleNamespace(scene=context.scene, finger_definition=guide, finger_bank_object=obj)
        if not _health_checked:
            obj.character_designer_finger_bank.needs_recheck = 'Geometry changed: Recheck symmetry.' if bank.dirty(scoped) else ''
            _health_checked = True
        if error: raise ValueError(error)
        result = definition.frame(scoped), ''
    except (ValueError, RuntimeError, KeyError, IndexError, ReferenceError, AttributeError) as exc:
        result = None, str(exc)
    _entries[guide.as_pointer()] = (key(guide, error), result)
    _serial += 1
    return result


def affected(depsgraph):
    """Hidden cached pairs must also be invalidated after genuine geometry edits."""
    try:
        return any((u.is_updated_geometry or u.is_updated_transform) and
                   u.id.original.as_pointer() in _dependencies for u in depsgraph.updates)
    except ReferenceError:
        return True


def snapshot():
    """Retain validated immutable frames across a pending parameter notification."""
    return _domain, dict(_entries), _health_checked


def restore_verified(context, obj, saved):
    """Caller must first prove the complete source data is unchanged.

    Domain/individual records are checked again. Entries depending on a separate
    bend mesh are deliberately excluded: the caller did not validate that mesh.
    """
    global _health_checked, _serial
    if not saved: return
    token(context, obj)
    domain, entries, health = saved
    if domain != _domain: return
    for slot in obj.character_designer_finger_bank.slots:
        guide = slot.guide
        if guide.source != obj or guide.bend_source not in (None, obj): continue
        entry = entries.get(guide.as_pointer())
        if entry and entry[0] == key(guide, slot.error): _entries[guide.as_pointer()] = entry
    _health_checked = health
    _serial += 1
