"""Synchronize explicit managed collection eye toggles with reversible views."""
import bpy
from bpy.app.handlers import persistent
from . import bone_collections as groups

_OWNER = globals().get('_OWNER', object())
_TIMER = globals().get('_TIMER')
_REGISTERED = False
_BUSY = False
_ENTRIES = {}
_PENDING = {}
_ERRORS = {}
_JOURNAL = []
INTERVAL = 0.2


def _candidates():
    scene = getattr(bpy.context, 'scene', None)
    if scene is None:
        return {}
    result = {}
    for rig in scene.objects:
        if (rig.type != 'ARMATURE' or rig.library or rig.data.library
                or not rig.is_editable or rig.data.users != 1):
            continue
        body = groups.body_collection(rig)
        original = rig.data.collections_all.get('Original')
        if body is not None and original is not None and original.get(groups.GROUP_KEY) == 'Original':
            result[rig.as_pointer()] = (rig, body, original)
    return result


def _flags(entry):
    return (bool(entry['body'].is_visible), bool(entry['original'].is_visible))


def _refresh(*, reset=False):
    candidates = _candidates()
    signature = {key: (body.as_pointer(), original.as_pointer())
                 for key, (_rig, body, original) in candidates.items()}
    old = {key: entry['signature'] for key, entry in _ENTRIES.items()}
    if not reset and signature == old:
        return
    bpy.msgbus.clear_by_owner(_OWNER)
    previous = dict(_ENTRIES)
    _ENTRIES.clear()
    for key, (rig, body, original) in candidates.items():
        entry = dict(rig=rig, body=body, original=original, signature=signature[key])
        saved = previous.get(key)
        entry['flags'] = (saved['flags'] if not reset and saved and saved['signature'] == signature[key]
                          else _flags(entry))
        _ENTRIES[key] = entry
        for role, collection in (('BODY', body), ('ORIGINAL', original)):
            bpy.msgbus.subscribe_rna(key=collection.path_resolve('is_visible', False),
                                    owner=_OWNER, args=(key, role), notify=_notice)
    for key in tuple(_PENDING):
        if reset or key not in _ENTRIES:
            _PENDING.pop(key, None)


def _notice(key, role):
    if _BUSY or not _REGISTERED:
        return
    entry = _ENTRIES.get(key)
    if entry is None:
        return
    try:
        index = 0 if role == 'BODY' else 1
        if _flags(entry)[index] != entry['flags'][index]:
            _PENDING[key] = role
    except ReferenceError:
        pass


def completed(main):
    """Ignore eye changes made by a completed panel/service operation."""
    if not _REGISTERED or _BUSY:
        return
    _refresh()
    entry = _ENTRIES.get(main.as_pointer())
    if entry is not None:
        entry['flags'] = _flags(entry)
        _PENDING.pop(main.as_pointer(), None)
        _ERRORS.pop(main.as_pointer(), None)


def last_error(main):
    return _ERRORS.get(main.as_pointer(), '') if main else ''


def _identity(rig):
    # Session IDs survive undo reallocations and renames; a file load clears
    # the journal instead of trying to identify a character in another file.
    return (rig.session_uid, rig.data.session_uid)


def _view_signature(main):
    from . import bone_display as display
    return [(_identity(rig), display._snapshot(rig), rig.data.get(display.VIEW_KEY),
             sorted((key, _identity(value)) for key, value in rig.data.get(display.REFS_KEY, {}).items()),
             [(c.name, c.parent.name if c.parent else None, sorted(c.bones.keys()))
              for c in rig.data.collections_all])
            for rig in display._affected(bpy.context, main)]


def _apply(entry, role, current):
    from . import bone_display as display
    rig = entry['rig']
    old = entry['flags']
    checkpoint = None
    try:
        checkpoint = display._checkpoint(display._affected(bpy.context, rig))
        mode = display.view_mode(rig)
        if role == 'ORIGINAL' and current[1]:
            # The clicked eye has already changed. Back up the display from
            # before that click, so switching it off restores the real baseline.
            entry['body'].is_visible, entry['original'].is_visible = old
            display.show_native(bpy.context, rig, 'ORIGINAL')
        elif role == 'ORIGINAL' and mode == 'ORIGINAL':
            display.restore_view(rig)
            entry['original'].is_visible = False
        elif role == 'BODY' and current[0]:
            display.show_controls(bpy.context, rig, 'BODY')
        # An OFF toggle outside isolation simply hides that collection.
        _ERRORS.pop(rig.as_pointer(), None)
    except Exception as exc:
        if checkpoint is not None:
            display._rollback(checkpoint)
        entry['body'].is_visible, entry['original'].is_visible = old
        message = 'Bone display could not switch: ' + str(exc)
        if _ERRORS.get(rig.as_pointer()) != message:
            print('Character Designer: ' + message)
        _ERRORS[rig.as_pointer()] = message


def sync_pending():
    """Process eye changes once; also callable by background integration tests."""
    global _BUSY
    if not _REGISTERED or _BUSY:
        return
    _refresh()
    _BUSY = True
    try:
        for key, entry in tuple(_ENTRIES.items()):
            current, old = _flags(entry), entry['flags']
            role = _PENDING.pop(key, None)
            if current == old:
                continue
            if entry['rig'].mode == 'EDIT' or getattr(bpy.context, 'mode', '').startswith('EDIT'):
                entry['flags'] = current
                continue
            changed = [name for i, name in enumerate(('BODY', 'ORIGINAL')) if current[i] != old[i]]
            if role not in changed:
                # Normally one UI eye changes per notification. A bulk RNA
                # update without notifications has no event order to preserve.
                if 'ORIGINAL' in changed and current[1]:
                    role = 'ORIGINAL'
                elif 'BODY' in changed and current[0]:
                    role = 'BODY'
                else:
                    role = changed[-1]
            try:
                raw = _view_signature(entry['rig'])
            except (ValueError, KeyError, TypeError, ReferenceError):
                raw = None  # The service reports invalid attachment/recovery data.
            _apply(entry, role, current)
            if raw is not None and key not in _ERRORS and raw != _view_signature(entry['rig']):
                # Blender records the native eye-click undo step before the
                # deferred callback. Retain only known successful click states
                # so Undo/Redo can complete that exact event without adding an
                # extra undo step or treating loaded visibility as user input.
                _JOURNAL.append((_identity(entry['rig']), raw, role, old))
                limit = max(64, bpy.context.preferences.edit.undo_steps * 2)
                del _JOURNAL[:-limit]
            entry['flags'] = _flags(entry)
    finally:
        _BUSY = False


def _tick():
    if not _REGISTERED:
        return None
    try:
        sync_pending()
    except (AttributeError, ReferenceError, RuntimeError):
        # Loading and add-on registration may temporarily restrict datablocks.
        pass
    return INTERVAL


@persistent
def _reset(_unused):
    _ERRORS.clear()
    _PENDING.clear()
    _ENTRIES.clear()
    if _REGISTERED:
        try:
            _refresh(reset=True)
        except (AttributeError, ReferenceError, RuntimeError):
            pass


@persistent
def _load_post(_unused):
    _JOURNAL.clear()
    _reset(None)


@persistent
def _undo_redo_post(_unused):
    global _BUSY
    _reset(None)
    if not _REGISTERED or _BUSY:
        return
    _BUSY = True
    try:
        for entry in _ENTRIES.values():
            if entry['rig'].mode == 'EDIT':
                continue
            try:
                identity, raw = _identity(entry['rig']), _view_signature(entry['rig'])
            except (ValueError, KeyError, TypeError, ReferenceError):
                continue
            for event_identity, event_raw, role, old in reversed(_JOURNAL):
                if event_identity == identity and event_raw == raw:
                    entry['flags'] = old
                    _apply(entry, role, _flags(entry))
                    entry['flags'] = _flags(entry)
                    break
    finally:
        _BUSY = False


def unregister():
    global _REGISTERED, _TIMER
    _REGISTERED = False
    bpy.msgbus.clear_by_owner(_OWNER)
    if _TIMER is not None and bpy.app.timers.is_registered(_TIMER):
        bpy.app.timers.unregister(_TIMER)
    _TIMER = None
    for name in ('load_post', 'undo_post', 'redo_post'):
        handlers = getattr(bpy.app.handlers, name)
        for handler in tuple(handlers):
            if (getattr(handler, '__module__', '') == __name__
                    and getattr(handler, '__name__', '') in {'_reset', '_load_post', '_undo_redo_post'}):
                handlers.remove(handler)
    _ENTRIES.clear()
    _PENDING.clear()
    _ERRORS.clear()
    _JOURNAL.clear()


def register():
    global _REGISTERED, _TIMER
    unregister()
    _REGISTERED = True
    for name, handler in (('load_post', _load_post), ('undo_post', _undo_redo_post), ('redo_post', _undo_redo_post)):
        getattr(bpy.app.handlers, name).append(handler)
    _reset(None)
    _TIMER = _tick
    bpy.app.timers.register(_TIMER, first_interval=INTERVAL, persistent=True)
