"""Protect current, original RNA author channels during private Hair sampling.

Only channels named by associated Action/NLA F-Curves are writable here. Driver
outputs and evaluated transforms are never restored. Unsupported author targets
refuse before the caller changes frames; this is not a scene checkpoint system.
"""

import ast
import math

import bpy
from mathutils import Color, Euler, Quaternion, Vector


_MATH_ARRAYS = (Color, Euler, Quaternion, Vector)


class ChannelProtectionError(RuntimeError):
    pass


def _tokens(path):
    """Parse an RNA path without eval, calls, slicing or Python expressions."""
    if not isinstance(path, str) or not path:
        raise ChannelProtectionError('An animation channel has no RNA path.')
    try:
        node = ast.parse('owner' + ('' if path.startswith('[') else '.') + path,
                         mode='eval').body
    except (SyntaxError, ValueError) as error:
        raise ChannelProtectionError('An animation RNA path cannot be parsed.') from error

    def walk(value):
        if isinstance(value, ast.Name) and value.id == 'owner':
            return []
        if isinstance(value, ast.Attribute) and not value.attr.startswith('_'):
            return walk(value.value) + [('attr', value.attr)]
        if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Constant):
            key = value.slice.value
            if type(key) in (str, int) and (type(key) is str or key >= 0):
                return walk(value.value) + [('item', key)]
        raise ChannelProtectionError('An animation RNA path uses an unsupported expression.')

    result = tuple(walk(node))
    if not result:
        raise ChannelProtectionError('An animation channel does not name a property.')
    return result


def _pointer(value):
    return value.as_pointer()


def _ids():
    mapping = bpy.data.user_map()
    result = set(mapping)
    for users in mapping.values():
        result.update(users)
    result.update(getattr(bpy.data, 'all_ids', ()))
    # Embedded node trees are not uniformly exposed by Main ID collections.
    pending = list(result)
    while pending:
        owner = pending.pop()
        for name in ('node_tree', 'shape_keys', 'collection'):
            child = getattr(owner, name, None)
            if isinstance(child, bpy.types.ID) and child not in result:
                result.add(child)
                pending.append(child)
    return result


def _local(owner):
    if (not isinstance(owner, bpy.types.ID) or owner.library
            or not owner.is_editable or getattr(owner, 'is_evaluated', False)):
        raise ChannelProtectionError('Animated author data must be local and editable.')


def _step(parent, token):
    kind, key = token
    if kind == 'attr':
        properties = getattr(getattr(parent, 'bl_rna', None), 'properties', None)
        prop = properties.get(key) if properties is not None else None
        if prop is None or getattr(prop, 'is_runtime', False):
            raise ChannelProtectionError('An animation path leaves native RNA data.')
        return getattr(parent, key)
    if isinstance(parent, bpy.types.bpy_struct):
        if type(key) is not str or key not in parent.keys():
            raise ChannelProtectionError('An animated custom property no longer exists.')
    elif not isinstance(parent, (bpy.types.bpy_prop_collection, bpy.types.bpy_prop_array)
                        + _MATH_ARRAYS):
        # IDProperty groups/arrays are native containers, not arbitrary Python
        # objects with executable __getitem__ implementations.
        from idprop.types import IDPropertyArray, IDPropertyGroup
        if not isinstance(parent, (IDPropertyArray, IDPropertyGroup)):
            raise ChannelProtectionError('An animation path uses an unsupported container.')
    return parent[key]


def _resolve(owner, tokens, index, *, writable):
    if type(index) is not int or index < 0:
        raise ChannelProtectionError('An animation component index is invalid.')
    parent = owner
    prefix = tokens[:-1]
    for token in prefix:
        parent = _step(parent, token)
    leaf = tokens[-1]
    explicit_axis = None
    if leaf[0] == 'item' and type(leaf[1]) is int:
        if index != 0 or len(tokens) < 2:
            raise ChannelProtectionError('An explicitly indexed animation channel is ambiguous.')
        explicit_axis = leaf[1]
        leaf = tokens[-2]
        prefix = tokens[:-2]
        parent = owner
        for token in prefix:
            parent = _step(parent, token)
    value = _step(parent, leaf)
    actual_id = getattr(parent, 'id_data', None)
    if not isinstance(actual_id, bpy.types.ID):
        raise ChannelProtectionError('An animated property has no original ID owner.')
    if writable:
        _local(owner)
        _local(actual_id)
    kind, key = leaf
    prop = None
    if kind == 'attr':
        prop = parent.bl_rna.properties.get(key)
        if writable and (prop.is_readonly or parent.is_property_readonly(key)
                         or not prop.is_animatable
                         or prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'ENUM'}):
            raise ChannelProtectionError('An animation channel is not a writable author property.')
        if sum(bool(dimension) for dimension in getattr(prop, 'array_dimensions', ())) > 1:
            raise ChannelProtectionError('Multidimensional animation targets are unsupported.')
    scalar = type(value) in (bool, int, float)
    if type(value) is str and prop is not None and prop.type == 'ENUM':
        scalar = True
    if scalar:
        if explicit_axis is not None or index != 0:
            raise ChannelProtectionError('A scalar animation channel has an invalid component.')
        axis = None
        saved = value
    else:
        from idprop.types import IDPropertyArray
        if not isinstance(value, (bpy.types.bpy_prop_array, IDPropertyArray) + _MATH_ARRAYS):
            raise ChannelProtectionError('An animation channel has an unsupported value type.')
        axis = explicit_axis if explicit_axis is not None else index
        if not 0 <= axis < len(value) or type(value[axis]) not in (bool, int, float):
            raise ChannelProtectionError('An animated array component is invalid.')
        saved = value[axis]
    if type(saved) is float and not math.isfinite(saved):
        raise ChannelProtectionError('An animation author value is not finite.')
    return dict(parent=parent, leaf=leaf, value=value, saved=saved, axis=axis,
                actual_id=actual_id, identity=(_pointer(parent), kind, key, axis),
                size=None if scalar else len(value))


def _curves(action, slot):
    if action is None:
        return ()
    if getattr(action, 'is_action_layered', False):
        bags = [bag for layer in action.layers for strip in layer.strips
                for bag in getattr(strip, 'channelbags', ())]
        if slot is None:
            if any(bag.fcurves for bag in bags):
                raise ChannelProtectionError('An animated Action has no unambiguous slot.')
            return ()
        return tuple(curve for bag in bags if bag.slot_handle == slot.handle
                     for curve in bag.fcurves)
    return tuple(getattr(action, 'fcurves', ()))


def _strips(strips):
    for strip in strips:
        yield strip
        yield from _strips(getattr(strip, 'strips', ()))


def _path_join(prefix, path):
    return prefix + ('' if path.startswith('[') else '.') + path


def _sources(owner):
    ad = getattr(owner, 'animation_data', None)
    if ad is None:
        return
    for curve in _curves(ad.action, ad.action_slot):
        yield curve.data_path, curve.array_index, dict(kind='ACTION', owner=owner,
            action=ad.action, slot=ad.action_slot, curve=curve)
    for track in ad.nla_tracks:
        for strip in _strips(track.strips):
            for curve in _curves(strip.action, strip.action_slot):
                yield curve.data_path, curve.array_index, dict(kind='NLA', owner=owner,
                    action=strip.action, slot=strip.action_slot, curve=curve)
            for curve in strip.fcurves:
                prefix = strip.path_from_id()
                if not prefix:
                    raise ChannelProtectionError('An NLA control channel has no original RNA path.')
                yield _path_join(prefix, curve.data_path), curve.array_index, dict(
                    kind='NLA_CONTROL', owner=owner, action=None, slot=None, curve=curve)


def capture():
    """Read all existing Action/NLA author values, refusing unsafe targets first."""
    owners = _ids()
    drivers = set()
    raw_drivers = set()
    for owner in owners:
        ad = getattr(owner, 'animation_data', None)
        for curve in ad.drivers if ad else ():
            raw_drivers.add((_pointer(owner), curve.data_path, curve.array_index))
            try:
                target = _resolve(owner, _tokens(curve.data_path), curve.array_index,
                                  writable=False)
                drivers.add(target['identity'])
            except Exception:
                # An unrelated broken/unsupported driver is never a write target.
                # Exact raw-path overlap remains protected even if it cannot be
                # resolved to a native leaf identity.
                pass
    records = {}
    for owner in owners:
        if (owner.library or not owner.is_editable
                or getattr(owner, 'is_evaluated', False)):
            continue
        for path, index, source in _sources(owner):
            try:
                tokens = _tokens(path)
                target = _resolve(owner, tokens, index, writable=True)
                identity = target['identity']
                if identity in drivers or (_pointer(owner), path, index) in raw_drivers:
                    raise ChannelProtectionError('An author animation channel is also a driver output.')
                if identity not in records:
                    records[identity] = dict(owner=owner, owner_pointer=_pointer(owner),
                        owner_uid=getattr(owner, 'session_uid', None), path=path, tokens=tokens,
                        index=index, identity=identity, axis=target['axis'],
                        actual_id_pointer=_pointer(target['actual_id']), size=target['size'],
                        value=target['saved'], sources=[])
                records[identity]['sources'].append(source)
            except Exception as error:
                raise ChannelProtectionError('Cannot protect animated author channel '
                    + repr(owner.name_full) + ' / ' + path + ': ' + str(error)) from error
    return dict(records=tuple(records.values()))


def _allowed(skip_deleted):
    if skip_deleted is False or skip_deleted is None:
        return frozenset()
    if skip_deleted is True:
        raise ChannelProtectionError('Skipping deleted authors requires explicit owned ID pointers.')
    values = frozenset(skip_deleted)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ChannelProtectionError('Deleted author exceptions must be owned ID pointers.')
    return values


def _live_target(record, alive, allowed):
    pointer = record['owner_pointer']
    if pointer not in alive:
        if pointer in allowed:
            return None
        raise ChannelProtectionError('An existing animated author ID disappeared.')
    owner = alive[pointer]
    if getattr(owner, 'session_uid', None) != record['owner_uid']:
        raise ChannelProtectionError('An animated author ID was replaced.')
    target = _resolve(owner, record['tokens'], record['index'], writable=True)
    if (target['identity'] != record['identity'] or target['size'] != record['size']
            or _pointer(target['actual_id']) != record['actual_id_pointer']
            or type(target['saved']) is not type(record['value'])):
        raise ChannelProtectionError('An animated author property changed identity or type.')
    return target


def _same(value, expected):
    if type(value) is not type(expected):
        return False
    return value == expected


def _collect(checkpoint, skip_deleted):
    allowed = _allowed(skip_deleted)
    alive = {_pointer(owner): owner for owner in _ids()}
    result, errors = [], []
    for record in checkpoint['records']:
        try:
            target = _live_target(record, alive, allowed)
            if target is not None:
                result.append((record, target))
        except Exception as error:
            errors.append(record['path'] + ': ' + str(error))
    return result, errors


def verify(checkpoint, skip_deleted=False):
    """Read and compare every captured author component; never write or evaluate."""
    targets, errors = _collect(checkpoint, skip_deleted)
    for record, target in targets:
        if not _same(target['saved'], record['value']):
            errors.append(record['path'] + ': current author value differs.')
    if errors:
        raise ChannelProtectionError('Hair author verification failed: ' + '; '.join(errors))
    return True


def restore(checkpoint, skip_deleted=False):
    """Restore only captured components, attempt all records and report every error.

    skip_deleted accepts only explicit ID pointers proved owned by the caller's
    deletion transaction. It never gives blanket permission to lose existing IDs.
    """
    targets, errors = _collect(checkpoint, skip_deleted)
    for record, target in targets:
        try:
            if _same(target['saved'], record['value']):
                continue
            parent, (kind, key), axis = target['parent'], target['leaf'], target['axis']
            if axis is not None:
                target['value'][axis] = record['value']
            elif kind == 'attr':
                setattr(parent, key, record['value'])
            else:
                parent[key] = record['value']
        except Exception as error:
            errors.append(record['path'] + ': ' + str(error))
    # Setters may update related channels, so validate the entire set after all
    # writes. Do not frame_set or update here: either may evaluate animation again.
    try:
        verify(checkpoint, skip_deleted)
    except Exception as error:
        errors.append(str(error))
    if errors:
        raise ChannelProtectionError('Hair author recovery needs attention: ' + '; '.join(errors))
    return True


def guard_cleanup(checkpoint, frame):
    """Conservatively prove author values survive native deletion Undo, read-only."""
    verify(checkpoint)
    if not math.isfinite(float(frame)):
        raise ChannelProtectionError('Cleanup requires a finite author frame.')
    errors = []
    for record in checkpoint['records']:
        try:
            direct = [source for source in record['sources'] if source['kind'] == 'ACTION']
            if not direct:
                raise ChannelProtectionError('An NLA author channel cannot be proved without evaluation.')
            expected = []
            for source in direct:
                owner = source['owner']
                ad = owner.animation_data
                if (not ad or ad.action != source['action'] or ad.action_slot != source['slot']
                        or ad.use_tweak_mode or ad.action_blend_type != 'REPLACE'
                        or ad.action_extrapolation != 'HOLD'
                        or not math.isclose(ad.action_influence, 1.0, rel_tol=0, abs_tol=1e-8)):
                    raise ChannelProtectionError('Complex Action blending prevents safe cleanup proof.')
                if ad.use_nla and any(not track.mute and any(not strip.mute
                        for strip in _strips(track.strips)) for track in ad.nla_tracks):
                    raise ChannelProtectionError('Live NLA prevents safe cleanup proof.')
                curve = source['curve']
                if curve.mute or (curve.group is not None and curve.group.mute):
                    raise ChannelProtectionError('Muted Action channels prevent safe cleanup proof.')
                evaluated = curve.evaluate(frame)
                value = record['value']
                if type(value) is bool:
                    if evaluated not in (0.0, 1.0):
                        raise ChannelProtectionError('A boolean channel has ambiguous interpolation.')
                    evaluated = bool(evaluated)
                elif type(value) is int:
                    if not math.isfinite(evaluated) or evaluated != int(evaluated):
                        raise ChannelProtectionError('An integer channel has ambiguous interpolation.')
                    evaluated = int(evaluated)
                elif type(value) is not float:
                    raise ChannelProtectionError('This author property has no safe numeric cleanup proof.')
                expected.append(math.isclose(evaluated, value, rel_tol=1e-6, abs_tol=1e-6)
                                if type(value) is float else _same(evaluated, value))
            if not all(expected):
                raise ChannelProtectionError('Key or resolve temporary animated author edits before cleanup.')
        except Exception as error:
            errors.append(record['path'] + ': ' + str(error))
    if errors:
        raise ChannelProtectionError('Keep Hair versions until author animation is resolved: '
                                     + '; '.join(errors))
    return True
