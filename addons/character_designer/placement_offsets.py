"""Keep an unkeyed child-Mesh placement across constant Object-Action reloads.

Only an explicitly admitted constant location can be reparameterized as its
unchanged keyed value plus an unkeyed delta_location. No Action, animation
binding, geometry, bone, or evaluated current placement is intentionally edited.
The placement caller owns whole-character/root movement and its rollback.
"""
import math

import bpy
from mathutils import Vector

from . import body_original_owner, body_setup_transaction as transaction, foot_controls, limb_ik


MATRIX_TOLERANCE = 1e-6
_CHANNELS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle',
    'scale', 'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')
_TRANSFORMS = frozenset(_CHANNELS) | {'rotation_mode', 'matrix_basis', 'matrix_local', 'matrix_world',
    'matrix_parent_inverse', 'parent', 'parent_type', 'parent_bone', 'parent_vertices'}


def _pointer(value):
    try:
        return value.as_pointer() if value is not None else None
    except ReferenceError:
        return None


def _matrix(value):
    return tuple(tuple(row) for row in value)


def _close(actual, expected):
    return all(math.isfinite(a) and abs(a-b) <= MATRIX_TOLERANCE
               for row_a, row_b in zip(actual, expected) for a,b in zip(row_a, row_b))


def _transform_path(path):
    return any(path == field or path.startswith(field + '[') for field in _TRANSFORMS)


def _read_path(path):
    return any(path == field or path.startswith(field + '[') for field in ('location', 'delta_location'))


def _action_state(action):
    # Only this Mesh's admitted active Action is read, never the character's
    # large retained bone Actions. The fingerprint is the existing animation
    # semantic proof; extra native curve/Action RNA covers modifiers and flags.
    return {'fingerprint': body_original_owner._animation_fingerprint(action),
        'rna': transaction._rna(action), 'properties': transaction._properties(action),
        'slots': [transaction._rna(slot) for slot in getattr(action, 'slots', ())],
        'layers': [(transaction._rna(layer), [(transaction._rna(strip),
            [(transaction._rna(bag), bag.slot_handle) for bag in getattr(strip, 'channelbags', ())])
            for strip in layer.strips]) for layer in getattr(action, 'layers', ())],
        'curves': [(transaction._rna(curve), transaction._properties(curve),
            [transaction._rna(modifier) for modifier in curve.modifiers],
            [transaction._rna(point) for point in curve.sampled_points],
            [transaction._rna(point) for point in curve.keyframe_points])
            for curve in limb_ik._fcurves_for_action(action)]}


def _protected(obj):
    animation = obj.animation_data
    return {'channels': {field: tuple(getattr(obj, field)) for field in _CHANNELS
                         if field not in {'location', 'delta_location'}},
        'rotation_mode': obj.rotation_mode, 'parent': _pointer(obj.parent),
        'parent_type': obj.parent_type, 'parent_bone': obj.parent_bone,
        'parent_vertices': tuple(obj.parent_vertices), 'inverse': _matrix(obj.matrix_parent_inverse),
        'properties': transaction._properties(obj), 'data_properties': transaction._properties(obj.data),
        'animation': transaction._rna(animation) if animation is not None else None,
        'slot': getattr(getattr(animation, 'action_slot', None), 'handle', None),
        'nla': [(transaction._rna(track), [transaction._rna(strip) for strip in track.strips])
                for track in animation.nla_tracks] if animation is not None else [],
        'constraints': [(constraint.as_pointer(), transaction._rna(constraint)) for constraint in obj.constraints],
        'locks': (tuple(obj.lock_location), tuple(obj.lock_rotation), tuple(obj.lock_scale))}


def _active_curves(obj, action, *, strict):
    animation = obj.animation_data
    if not getattr(action, 'is_action_layered', False):
        return tuple(limb_ik._fcurves_for_action(action))
    slots = tuple(action.slots)
    slot = getattr(animation, 'action_slot', None)
    if strict and (len(slots) != 1 or slot is None or slot.handle != slots[0].handle
                   or slot.target_id_type != 'OBJECT'):
        raise ValueError(f'{obj.name} needs one unambiguous Object Action slot before floor placement.')
    if slot is None:
        return tuple(limb_ik._fcurves_for_action(action))
    layers = tuple(action.layers)
    strips = tuple(strip for layer in layers for strip in layer.strips)
    bags = tuple(bag for strip in strips for bag in getattr(strip, 'channelbags', ()))
    if strict and (len(layers) != 1 or len(strips) != 1 or len(bags) != 1
                   or bags[0].slot_handle != slot.handle):
        raise ValueError(f'{obj.name} needs one direct Object Action layer before floor placement.')
    return tuple(curve for bag in bags if bag.slot_handle == slot.handle for curve in bag.fcurves)


def _admit_animation(obj, action, curves, time):
    animation = obj.animation_data
    if (animation.use_tweak_mode or animation.action_blend_type != 'REPLACE'
            or animation.action_influence != 1. or animation.action_extrapolation != 'HOLD'
            or any(track.strips for track in animation.nla_tracks)):
        raise ValueError(f'{obj.name} has blended or NLA Object animation; preserve its placement first.')
    if obj.constraints or any(_transform_path(curve.data_path) for curve in animation.drivers):
        raise ValueError(f'{obj.name} has constrained or driven Object transforms; preserve its placement first.')
    _active_curves(obj, action, strict=True)
    constants = {}
    for curve in curves:
        path, index = curve.data_path, curve.array_index
        if not _transform_path(path):
            continue
        if (curve.mute or not curve.is_valid or (curve.group is not None and curve.group.mute)
                or path == 'delta_location' or path not in _CHANNELS
                or not 0 <= index < len(getattr(obj, path))):
            raise ValueError(f'{obj.name} has ambiguous Object transform animation; preserve its placement first.')
        expected = float(curve.evaluate(time))
        if not math.isfinite(expected):
            raise ValueError(f'{obj.name} has a non-finite Object Action value.')
        if path == 'location':
            if (index in constants or len(curve.keyframe_points) != 1 or curve.sampled_points
                    or curve.modifiers or curve.extrapolation != 'CONSTANT'):
                raise ValueError(f'{obj.name} needs constant single-key location curves before floor placement.')
            constant = float(curve.keyframe_points[0].co.y)
            if not math.isfinite(constant) or expected != constant:
                raise ValueError(f'{obj.name} has a nonconstant Object location curve.')
            constants[index] = constant
        elif abs(float(getattr(obj, path)[index]) - expected) > 1e-6:
            raise ValueError(f'{obj.name} has another unkeyed animated transform; preserve that adjustment first.')
    return constants


def _refuse_channel_readers(objects):
    selected = set(objects)
    for owner in foot_controls._driver_owners():
        animation = getattr(owner, 'animation_data', None)
        for curve in animation.drivers if animation is not None else ():
            for variable in curve.driver.variables:
                if variable.type != 'SINGLE_PROP':
                    continue
                for target in variable.targets:
                    if target.id in selected and _read_path(target.data_path):
                        raise ValueError(f'{target.id.name} location/delta_location is read by a property driver; preserve that dependency first.')


def prepare(context, objects, roots):
    """Plan the two-field storage correction without changing current inputs."""
    roots = set(roots)
    time = context.scene.frame_current + context.scene.frame_subframe
    state = {'entries': [], 'expected_channels': {},
             'time': (context.scene.frame_current, context.scene.frame_subframe)}
    for obj in sorted(objects, key=lambda value: value.name):
        if obj.type != 'MESH' or obj in roots:
            continue
        animation = obj.animation_data
        action = animation.action if animation is not None else None
        if animation and any(curve.data_path == 'location'
                for track in animation.nla_tracks for strip in track.strips if strip.action
                for curve in limb_ik._fcurves_for_action(strip.action)):
            raise ValueError(f'{obj.name} has NLA location animation; preserve its placement first.')
        if action is None:
            continue
        curves = _active_curves(obj, action, strict=False)
        for curve in curves:
            path, index = curve.data_path, curve.array_index
            if (path in _CHANNELS and path != 'location' and 0 <= index < len(getattr(obj, path))
                    and not curve.mute and abs(float(getattr(obj, path)[index]) - curve.evaluate(time)) > 1e-6):
                raise ValueError(f'{obj.name} has another unkeyed animated transform; preserve that adjustment first.')
        locations = [curve for curve in curves if curve.data_path == 'location']
        if not locations or all(0 <= curve.array_index < 3
                and float(obj.location[curve.array_index]) == float(curve.evaluate(time)) for curve in locations):
            continue
        if obj.library or obj.override_library or not obj.is_editable:
            raise ValueError(f'{obj.name} is not a local editable Mesh for placement preservation.')
        constants = _admit_animation(obj, action, curves, time)
        original = {'location': tuple(obj.location), 'delta_location': tuple(obj.delta_location)}
        location, delta = list(obj.location), list(obj.delta_location)
        for axis, constant in constants.items():
            delta[axis] += location[axis] - constant
            location[axis] = constant
        planned = {'location': tuple(Vector(location)), 'delta_location': tuple(Vector(delta))}
        if not all(math.isfinite(value) for values in planned.values() for value in values):
            raise ValueError(f'{obj.name} cannot store a finite persistent placement offset.')
        entry = {'object': obj, 'object_pointer': _pointer(obj), 'data_pointer': _pointer(obj.data),
            'original': original, 'planned': planned, 'basis': _matrix(obj.matrix_basis),
            'world': _matrix(obj.matrix_world), 'action': action, 'action_pointer': _pointer(action),
            'action_state': _action_state(action), 'protected': _protected(obj)}
        state['entries'].append(entry)
        state['expected_channels'][obj] = dict(planned)
    if state['entries']:
        _refuse_channel_readers(entry['object'] for entry in state['entries'])
    return state


def _verify(context, state, *, restored):
    if (context.scene.frame_current, context.scene.frame_subframe) != state['time']:
        raise ValueError('Time changed while preserving persistent character placement.')
    for entry in state['entries']:
        obj = entry['object']
        if (_pointer(obj) != entry['object_pointer'] or _pointer(obj.data) != entry['data_pointer']
                or _pointer(obj.animation_data.action) != entry['action_pointer']):
            raise ValueError('A Mesh or its Object Action was replaced during placement.')
        channels = entry['original'] if restored else entry['planned']
        if any(tuple(getattr(obj, field)) != value for field,value in channels.items()):
            raise ValueError(f'{obj.name} did not retain its expected placement storage channels.')
        if _protected(obj) != entry['protected'] or _action_state(entry['action']) != entry['action_state']:
            raise ValueError(f'{obj.name} changed protected Object inputs or its original Action.')
        if not _close(obj.matrix_basis, entry['basis']):
            raise ValueError(f'{obj.name} changed its local transform while preserving placement storage.')


def verify(context, state):
    """Check storage and original Action after placement; world may have moved."""
    context.view_layer.update()
    _verify(context, state, restored=False)
    return {'count': len(state['entries']), 'objects': {entry['object'].name: {
        'location_before': list(entry['original']['location']),
        'delta_location_before': list(entry['original']['delta_location']),
        'location_after': list(entry['planned']['location']),
        'delta_location_after': list(entry['planned']['delta_location']),
        'action': entry['action'].name, 'action_unchanged': True,
        'local_basis_unchanged_within': MATRIX_TOLERANCE}
        for entry in state['entries']}, 'changed_fields_only': ['location', 'delta_location']}


def restore(context, state):
    """Restore the exact original two fields without touching animation."""
    for entry in state['entries']:
        obj = entry['object']
        if _pointer(obj) != entry['object_pointer']:
            raise RuntimeError('The original placement Mesh disappeared during recovery.')
        for field in ('delta_location', 'location'):
            if tuple(getattr(obj, field)) != entry['original'][field]:
                setattr(obj, field, entry['original'][field])
    context.view_layer.update()
    _verify(context, state, restored=True)


def apply(context, state):
    """Commit before root movement, requiring the current world to stay fixed."""
    _verify(context, state, restored=True)
    try:
        for entry in state['entries']:
            for field in ('delta_location', 'location'):
                if tuple(getattr(entry['object'], field)) != entry['planned'][field]:
                    setattr(entry['object'], field, entry['planned'][field])
        proof = verify(context, state)
        graph = context.evaluated_depsgraph_get()
        for entry in state['entries']:
            if not _close(entry['object'].evaluated_get(graph).matrix_world, entry['world']):
                raise ValueError(f"{entry['object'].name} moved while preserving its persistent placement storage.")
        return proof
    except Exception as original:
        try:
            restore(context, state)
        except Exception as recovery:
            raise RuntimeError(f'Placement storage correction failed: {original}. Recovery failed: {recovery}.') from original
        raise
