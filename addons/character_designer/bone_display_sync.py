"""Compatibility boundary: native Bone Collection visibility stays native.

Display presets and control ownership are explicit panel operations. Collection
eyes and solo stars never invoke those operations in a deferred callback.
"""
import bpy


# Retain upgrade references when Blender reloads this module in place. A fresh
# import can also discover legacy state through its registered handlers.
_OWNER = globals().get('_OWNER', object())
_TIMER = globals().get('_TIMER')
_REGISTERED = False
_BUSY = False
_LEGACY_HANDLERS = frozenset({'_reset', '_load_post', '_undo_redo_post'})
_LEGACY_FUNCTIONS = ('_candidates', '_flags', '_refresh', '_notice', '_identity',
                     '_view_signature', '_apply', '_tick', '_reset', '_load_post',
                     '_undo_redo_post')


def completed(_main):
    """Compatibility hook for explicit display and weight-workspace actions."""


def last_error(_main):
    """Native visibility does not run a service that can report an error."""
    return ''


def sync_pending():
    """Compatibility hook; native eye and star changes need no synchronization."""


def unregister():
    """Remove callbacks from older versions without changing artist visibility."""
    global _REGISTERED, _TIMER
    _REGISTERED = False
    states = {id(globals()): globals()}
    for name in ('load_post', 'undo_post', 'redo_post'):
        handlers = getattr(bpy.app.handlers, name)
        for handler in tuple(handlers):
            if (getattr(handler, '__module__', '') == __name__
                    and getattr(handler, '__name__', '') in _LEGACY_HANDLERS):
                state = getattr(handler, '__globals__', None)
                if state is not None:
                    states[id(state)] = state
                handlers.remove(handler)

    owners, timers = {}, {}
    for state in states.values():
        owner = state.get('_OWNER')
        if owner is not None:
            owners[id(owner)] = owner
        for timer in (state.get('_TIMER'), state.get('_tick')):
            if callable(timer):
                timers[id(timer)] = timer
        state['_REGISTERED'] = False
        for name in ('_ENTRIES', '_PENDING', '_ERRORS', '_JOURNAL'):
            value = state.get(name)
            if isinstance(value, (dict, list)):
                value.clear()
        state['_TIMER'] = None
        for name in _LEGACY_FUNCTIONS:
            state.pop(name, None)
    for owner in owners.values():
        bpy.msgbus.clear_by_owner(owner)
    for timer in timers.values():
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    _TIMER = None


def register():
    """Clean old hooks; install no timer, message bus or history callbacks."""
    global _REGISTERED
    unregister()
    _REGISTERED = True

