"""Disjoint owned bone leases for the one verified external Wiggle 2 solver.

No enable, global rebuild, timeline operation, handler or timer lives here.
Adapters prove their source/rig graph and own lifecycle; this module owns only
their native flags, list membership and first/last Scene/Armature enable leases.
"""
from contextlib import contextmanager
import array
import sys

import bpy


_LEASES = {}
_SCENES = {}
_RIGS = {}
_FLAGS = ('wiggle_enable', 'wiggle_head', 'wiggle_tail', 'wiggle_mute',
          'wiggle_head_mute', 'wiggle_tail_mute')
_SCENE_DEFAULTS = {'iterations': 2, 'loop': True, 'preroll': 0,
                   'bake_overwrite': False, 'bake_nla': False}
# Observed saved-X inactive leaves; names also occur in verified1.1.2
# properties.py (LF SHA b24a6d00...). This is no claim about their provenance.
# These raw int zeros are inert under2.2.4 and are never rewritten by us.
_INACTIVE_COMPAT = {'Scene': frozenset({'syncing'}),
                    'Object': frozenset({'enable'}),
                    'PoseBone': frozenset({'enable'})}


class WiggleRuntimeError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise WiggleRuntimeError(message)


def _key(owner):
    return owner.as_pointer()


def _raw(owner, field):
    return {'exists': owner.is_property_set(field),
            'value': getattr(owner, field) if owner.is_property_set(field) else None}


def _restore(owner, field, saved):
    if _raw(owner, field) == saved:
        return
    if saved['exists']:
        setattr(owner, field, saved['value'])
    else:
        owner.property_unset(field)


def _clone(value):
    if isinstance(value, bpy.types.ID):
        return value
    if hasattr(value, 'keys'):
        return {key: _clone(value[key]) for key in value.keys()}
    if hasattr(value, 'typecode'):
        return array.array(value.typecode, value)
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    return tuple(value)


def _group(owner):
    if not owner.is_property_set('wiggle'):
        return {'exists': False, 'values': {}}
    group = owner.wiggle
    rna = getattr(group, 'bl_rna', None)
    if rna is not None:
        allowed = {prop.identifier for prop in rna.properties} - {'rna_type'}
        kind = getattr(getattr(owner, 'bl_rna', None), 'identifier', None)
        compatible = _INACTIVE_COMPAT.get(kind, ())
        for key in set(group.keys()) - allowed:
            value = group[key]
            _require(key in compatible and type(value) is int and value == 0,
                     'Custom Wiggle runtime properties need review before exact restoration.')
    return {'exists': True, 'values': {key: _clone(group[key]) for key in group.keys() if key != 'list'}}


def _compatibility(owner, saved):
    kind = getattr(getattr(owner, 'bl_rna', None), 'identifier', None)
    return {key: (type(saved['values'][key]), saved['values'][key])
            for key in _INACTIVE_COMPAT.get(kind, ()) if key in saved['values']}


def _check_group(owner, saved):
    current = _group(owner)
    _require(_compatibility(owner, current) == _compatibility(owner, saved),
             'Inactive compatibility metadata changed during this physics lease; external changes were retained.')
    return current


def _restore_group(owner, saved):
    _check_group(owner, saved)
    if not saved['exists']:
        if owner.is_property_set('wiggle'):
            owner.property_unset('wiggle')
        return
    group = owner.wiggle
    compatible = _compatibility(owner, saved)
    for key in list(group.keys()):
        if key != 'list' and key not in compatible:
            del group[key]
    for key, value in saved['values'].items():
        if key not in compatible:
            group[key] = _clone(value)


def _restore_scene_group(scene, saved):
    # The native solver owns dt/lastframe; other Scene settings may be edited
    # independently. Never overwrite a render/reset or author quality setting.
    _check_group(scene, saved)
    if not scene.is_property_set('wiggle'):
        return
    group = scene.wiggle
    for key in ('dt', 'lastframe'):
        if key in saved['values']:
            group[key] = saved['values'][key]
        elif key in group.keys():
            del group[key]
    if not saved['exists'] and not (set(group.keys()) - {'list'}):
        scene.property_unset('wiggle')


@contextmanager
def _writes(backend):
    original = backend.update_prop
    backend.update_prop = lambda _owner, _context, _field: None
    try:
        yield
    finally:
        backend.update_prop = original


def _prove_backend(backend):
    from . import skirt_wiggle
    _require(skirt_wiggle._backend() is backend,
             'The shared Wiggle 2 backend changed; preserve the current scene.')


def _schema(backend):
    # PropertyRNA addresses are safe opaque tokens even if official unregister
    # later frees their PG targets. Never inspect fixed_type here.
    classes = tuple(getattr(backend, name, None)
                    for name in ('WiggleScene', 'WiggleObject', 'WiggleBone'))
    _require(all(cls is not None and getattr(cls, 'is_registered', False) for cls in classes),
             'Wiggle 2 is disabled or its RNA classes are not registered.')
    properties = tuple((owner.__name__, prop.identifier, prop.type, prop.as_pointer())
        for owner in (bpy.types.Scene, bpy.types.Object, bpy.types.PoseBone)
        for prop in owner.bl_rna.properties if prop.identifier.startswith('wiggle'))
    _require(bpy.app.handlers.frame_change_pre.count(backend.wiggle_pre) == 1
             and bpy.app.handlers.frame_change_post.count(backend.wiggle_post) == 1,
             'The shared Wiggle 2 callbacks changed.')
    return classes, properties, backend.wiggle_pre, backend.wiggle_post, backend.reset_bone, backend.update_prop


def _check_backend(lease):
    backend = lease['backend']
    _require(sys.modules.get(backend.__name__) is backend and _schema(backend) == lease['schema'],
             'The shared Wiggle 2 registration changed; simulation is paused.')


def _members(scene):
    if not scene.is_property_set('wiggle'):
        return set()
    members = [(row.name, item.name) for row in scene.wiggle.list for item in row.list]
    _require(len(members) == len(set(members)), 'Duplicate native Wiggle list entries need review.')
    return set(members)


def _list_add(scene, rig_name, names):
    row = scene.wiggle.list.get(rig_name)
    if row is None:
        row = scene.wiggle.list.add()
        row.name = rig_name
    for name in names:
        if row.list.get(name) is None:
            row.list.add().name = name


def _list_remove(scene, rig_name, names):
    if not scene.is_property_set('wiggle'):
        return
    row = scene.wiggle.list.get(rig_name)
    if row is None:
        return
    for index in reversed(range(len(row.list))):
        if row.list[index].name in names:
            row.list.remove(index)
    if not row.list:
        for index, item in enumerate(scene.wiggle.list):
            if item == row:
                scene.wiggle.list.remove(index)
                break


def owned_bones(scene):
    return {(lease['rig'], name) for lease in _LEASES.values()
            if lease['scene'] == scene and lease['active'] for name in lease['names']}


def owns(rig, name):
    return any(lease['active'] and lease['rig'] == rig and name in lease['names']
               for lease in _LEASES.values())


def has_component(component, scene=None):
    return any(lease['component'] == component and lease['active']
               and (scene is None or lease['scene'] == scene) for lease in _LEASES.values())


def _native_owned(scene):
    owned = owned_bones(scene)
    actual = {(rig, bone.name) for rig in scene.objects if rig.type == 'ARMATURE'
              for bone in rig.pose.bones if bone.wiggle_head or bone.wiggle_tail}
    _require(actual == owned, 'An unowned Wiggle simulation was enabled; preserve it before continuing.')
    running = {(lease['rig'].name, name) for lease in _LEASES.values()
               if lease['scene'] == scene and lease['active'] and not lease['paused']
               for name in lease['names']}
    _require(_members(scene) == running,
             'The native Wiggle list changed outside these owned simulations.')


def _running(*, scene=None, rig=None):
    return any(lease['active'] and not lease['paused']
               and (scene is None or lease['scene'] == scene)
               and (rig is None or lease['rig'] == rig) for lease in _LEASES.values())


def _ancestor_overlap(rig, names):
    peer = {name for lease in _LEASES.values() if lease['rig'] == rig for name in lease['names']}
    for name in names | peer:
        parent = rig.pose.bones[name].parent
        seen = set()
        while parent is not None:
            _require(parent.name not in seen, 'The rig parent graph contains a cycle.')
            seen.add(parent.name)
            _require(not ((name in names and parent.name in peer) or (name in peer and parent.name in names)),
                     'Hair and skirt simulation chains cannot drive each other through an ancestor.')
            parent = parent.parent


def acquire(context, component, source, rig, names, backend):
    _require(component in {'HAIR', 'SKIRT'}, 'Choose an owned Hair or Skirt simulation.')
    _require(context.scene == bpy.context.scene and context.mode in {'OBJECT', 'POSE'},
             'Start shared physics in the active Scene in Object or Pose Mode.')
    _prove_backend(backend)
    schema = _schema(backend)
    names = tuple(names)
    key = component, _key(source)
    _require(names and len(names) == len(set(names)) and key not in _LEASES,
             'The requested physics owner is empty, duplicated, or already connected.')
    _require(context.scene.objects.get(rig.name) == rig
             and bpy.data.objects.get(source.name) == source,
             'The physics source or rig was replaced.')
    _native_owned(context.scene)
    for lease in tuple(_LEASES.values()):
        if lease['scene'] == context.scene:
            validate(lease)
    _require(not rig.wiggle_freeze and not rig.wiggle_mute,
             'The rig is frozen or muted outside these physics controls.')
    _require(all(rig.pose.bones.get(name) is not None for name in names), 'Owned physics bones are missing.')
    _require(not any(owns(rig, name) for name in names), 'The physics bone sets overlap.')
    _ancestor_overlap(rig, set(names))
    scene = context.scene
    pg_original = _group(scene)
    rig_pg_original = _group(rig)
    group = scene.wiggle if pg_original['exists'] else None
    _require(group is None or not any(getattr(group, field) for field in ('is_rendering', 'is_preroll', 'reset')),
             'Finish the current Wiggle render, reset or preroll operation first.')
    scene_key, rig_key = _key(scene), _key(rig)
    fields = tuple(prop.identifier for prop in bpy.types.PoseBone.bl_rna.properties
                   if prop.identifier.startswith('wiggle_'))
    bones = {name: rig.pose.bones[name] for name in names}
    lease = dict(key=key, component=component, source=source, rig=rig, scene=scene,
        names=names, bones=bones, rig_name=rig.name, backend=backend, schema=schema,
        native_original={name: {field: _raw(bone, field) for field in fields} for name, bone in bones.items()},
        group_original={name: _group(bone) for name, bone in bones.items()},
        active=False, paused=False, expected_flags={}, fields=fields)
    if scene_key not in _SCENES:
        _SCENES[scene_key] = dict(scene=scene, enabled=_raw(scene, 'wiggle_enable'), group=pg_original,
            controls={field: getattr(group, field) if group else default
                      for field, default in _SCENE_DEFAULTS.items()})
    if rig_key not in _RIGS:
        _RIGS[rig_key] = dict(rig=rig, enabled=_raw(rig, 'wiggle_enable'), group=rig_pg_original)
    lease['scene_original'] = dict(_SCENES[scene_key]['enabled'])
    lease['rig_original'] = dict(_RIGS[rig_key]['enabled'])
    _LEASES[key] = lease
    return lease


def validate(lease, *, teardown=False):
    _require(_LEASES.get(lease['key']) is lease, 'The physics lease is no longer current.')
    _check_backend(lease)
    scene, rig, source = lease['scene'], lease['rig'], lease['source']
    _require(bpy.data.objects.get(source.name) == source
             and scene.objects.get(lease['rig_name']) == rig and rig.name == lease['rig_name']
             and all(rig.pose.bones.get(name) == bone for name, bone in lease['bones'].items()),
             'Physics source, rig, or owned bones were replaced.')
    _check_group(scene, _SCENES[_key(scene)]['group'])
    _check_group(rig, _RIGS[_key(rig)]['group'])
    for name, bone in lease['bones'].items():
        _check_group(bone, lease['group_original'][name])
    if not teardown:
        _require(not rig.wiggle_freeze and not rig.wiggle_mute,
                 'The rig was frozen or muted outside these physics controls.')
        shared = _SCENES[_key(scene)]
        if scene.is_property_set('wiggle'):
            _require(all(getattr(scene.wiggle, field) == value for field, value in shared['controls'].items()),
                     'Shared Wiggle quality or loop settings changed outside these physics controls.')
    if lease['active']:
        if not teardown:
            _require(bool(scene.wiggle_enable) == _running(scene=scene)
                     and bool(rig.wiggle_enable) == _running(rig=rig),
                     'The shared physics enable switch changed.')
        _require(all(getattr(lease['bones'][name], field) == value
                     for name, flags in lease['expected_flags'].items() for field, value in flags.items()),
                 'Owned physics flags changed outside these controls.')
    if not teardown:
        _native_owned(scene)


def _live(lease):
    return dict(raw={name: {field: _raw(bone, field) for field in lease['fields']}
                     for name, bone in lease['bones'].items()},
        groups={name: _group(bone) for name, bone in lease['bones'].items()},
        scene=_raw(lease['scene'], 'wiggle_enable'), rig=_raw(lease['rig'], 'wiggle_enable'),
        scene_group=_group(lease['scene']), active=lease['active'], paused=lease['paused'],
        flags={name: dict(values) for name, values in lease['expected_flags'].items()},
        layout=[(row.name, tuple(item.name for item in row.list))
                for row in lease['scene'].wiggle.list] if lease['scene'].is_property_set('wiggle') else [])


def _restore_members(lease, before):
    scene, rig_name, names = lease['scene'], lease['rig_name'], set(lease['names'])
    if not before['active'] or before.get('paused', False):
        _list_remove(scene, rig_name, names)
        return
    _list_add(scene, rig_name, lease['names'])
    rows = scene.wiggle.list
    row = rows.get(rig_name)
    target_row, prior = next((index, items) for index, (name, items) in enumerate(before['layout']) if name == rig_name)
    for target_index, name in enumerate(prior):
        if name in names:
            current = next(index for index, item in enumerate(row.list) if item.name == name)
            if current != target_index:
                row.list.move(current, target_index)
    current_row = next(index for index, item in enumerate(rows) if item == row)
    if current_row != target_row:
        rows.move(current_row, target_row)


def _rollback(lease, before):
    scene, rig = lease['scene'], lease['rig']
    with _writes(lease['backend']):
        for name, values in before['raw'].items():
            for field, saved in values.items():
                _restore(lease['bones'][name], field, saved)
            _restore_group(lease['bones'][name], before['groups'][name])
        _restore(scene, 'wiggle_enable', before['scene'])
        _restore(rig, 'wiggle_enable', before['rig'])
    _restore_members(lease, before)
    _restore_scene_group(scene, before['scene_group'])
    lease.update(active=before['active'], paused=before['paused'], expected_flags=before['flags'])


def configure(lease, settings_by_name):
    validate(lease)
    _require(not lease['active'] and set(settings_by_name) == set(lease['names']),
             'Configure this acquired owner once with exactly its owned bones.')
    _require(all(set(values) <= set(lease['fields']) for values in settings_by_name.values()),
             'Unsupported native physics settings were requested.')
    before = _live(lease)
    first = not _running(scene=lease['scene'])
    try:
        with _writes(lease['backend']):
            for name, values in settings_by_name.items():
                flags = dict(wiggle_enable=True, wiggle_head=False, wiggle_tail=True,
                             wiggle_mute=False, wiggle_head_mute=False, wiggle_tail_mute=False)
                for field, value in {**values, **flags}.items():
                    setattr(lease['bones'][name], field, value)
                lease['expected_flags'][name] = flags
            lease['rig'].wiggle_enable = True
            lease['scene'].wiggle_enable = True
        _list_add(lease['scene'], lease['rig_name'], lease['names'])
        for bone in lease['bones'].values():
            lease['backend'].reset_bone(bone)
        if first:
            lease['scene'].wiggle.lastframe = lease['scene'].frame_current
        lease['active'] = True
        validate(lease)
    except Exception:
        _rollback(lease, before)
        raise


def reseed(lease, *, skip_frame=False):
    validate(lease)
    for bone in lease['bones'].values():
        lease['backend'].reset_bone(bone)
    if skip_frame:
        lease['scene'].wiggle.lastframe = lease['scene'].frame_current


def set_paused(lease, paused):
    _require(type(paused) is bool, 'Choose whether this physics owner is paused.')
    validate(lease)
    _require(lease['active'], 'Start this physics owner before pausing it.')
    if lease['paused'] == paused:
        return
    before = _live(lease)
    try:
        if not paused:
            reseed(lease)
        with _writes(lease['backend']):
            for name, bone in lease['bones'].items():
                bone.wiggle_mute = paused
                lease['expected_flags'][name]['wiggle_mute'] = paused
        lease['paused'] = paused
        # Muting alone is not inert in Wiggle 2.2.4: wiggle_pre still resets
        # every muted list entry, writes runtime RNA and updates the view layer.
        # Keep the owned endpoints/rollback data, but detach a paused owner
        # from the solver list. A running peer retains its own list and clock.
        if paused:
            _list_remove(lease['scene'], lease['rig_name'], lease['names'])
        else:
            _list_add(lease['scene'], lease['rig_name'], lease['names'])
            if not before['scene']['value']:
                lease['scene'].wiggle.lastframe = lease['scene'].frame_current
        with _writes(lease['backend']):
            lease['rig'].wiggle_enable = _running(rig=lease['rig'])
            lease['scene'].wiggle_enable = _running(scene=lease['scene'])
        validate(lease)
    except Exception:
        _rollback(lease, before)
        raise


def release(lease, *, restore_fields=None):
    if _LEASES.get(lease['key']) is not lease:
        return
    validate(lease, teardown=True)
    fields = lease['fields'] if restore_fields is None else tuple(restore_fields)
    _require(set(fields) <= set(lease['fields']), 'Restore only this owner\'s native fields.')
    before = _live(lease)
    scene, rig = lease['scene'], lease['rig']
    last_scene = not any(other is not lease and other['scene'] == scene for other in _LEASES.values())
    last_rig = not any(other is not lease and other['rig'] == rig for other in _LEASES.values())
    try:
        with _writes(lease['backend']):
            for name, bone in lease['bones'].items():
                for field in fields:
                    _restore(bone, field, lease['native_original'][name][field])
                _restore_group(bone, lease['group_original'][name])
            foreign_rig = any((bone.wiggle_head or bone.wiggle_tail) and not owns(rig, bone.name)
                              for bone in rig.pose.bones)
            foreign_scene = any((bone.wiggle_head or bone.wiggle_tail) and not owns(obj, bone.name)
                for obj in scene.objects if obj.type == 'ARMATURE' for bone in obj.pose.bones)
            lease['active'] = False
            if last_rig and not foreign_rig:
                _restore(rig, 'wiggle_enable', _RIGS[_key(rig)]['enabled'])
            elif not foreign_rig:
                rig.wiggle_enable = _running(rig=rig)
            if last_scene and not foreign_scene:
                _restore(scene, 'wiggle_enable', _SCENES[_key(scene)]['enabled'])
            elif not foreign_scene:
                scene.wiggle_enable = _running(scene=scene)
        _list_remove(scene, lease['rig_name'], lease['names'])
        if last_scene and not foreign_scene and not _members(scene):
            _restore_scene_group(scene, _SCENES[_key(scene)]['group'])
        _LEASES.pop(lease['key'])
        if last_scene:
            _SCENES.pop(_key(scene))
        if last_rig:
            _RIGS.pop(_key(rig))
    except Exception:
        _rollback(lease, before)
        raise


def forget(component=None):
    """Drop stale references after native history replacement; never read RNA."""
    for key, lease in tuple(_LEASES.items()):
        if component is None or lease['component'] == component:
            _LEASES.pop(key)
    scene_ids = {id(lease['scene']) for lease in _LEASES.values()}
    rig_ids = {id(lease['rig']) for lease in _LEASES.values()}
    for key, shared in tuple(_SCENES.items()):
        if id(shared['scene']) not in scene_ids:
            _SCENES.pop(key)
    for key, shared in tuple(_RIGS.items()):
        if id(shared['rig']) not in rig_ids:
            _RIGS.pop(key)
