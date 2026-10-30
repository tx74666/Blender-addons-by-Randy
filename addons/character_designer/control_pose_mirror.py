"""Mirror saved Poses with Blender's native Action flip semantics.

Managed poses use complete saved inputs in one disposable Action. Legacy poses
retain their partial-channel reconstruction. The character and source asset are
read-only; the normal pose service retains matching, undo and auto-key.
"""

import math

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

_MATRIX_TOLERANCE = 2e-6


def _rotation_property(mode):
    return ('rotation_quaternion' if mode == 'QUATERNION' else
            'rotation_axis_angle' if mode == 'AXIS_ANGLE' else 'rotation_euler')


def _rotation_values(rotation, mode):
    if mode == 'QUATERNION':
        return tuple(rotation)
    if mode == 'AXIS_ANGLE':
        axis, angle = rotation.to_axis_angle()
        return (angle, *axis)
    return tuple(rotation.to_euler(mode))


def _quaternion(entries, mode):
    values = [entries[index] for index in sorted(entries)]
    if mode == 'QUATERNION':
        return Quaternion(values).normalized()
    if mode == 'AXIS_ANGLE':
        return Quaternion(Vector(values[1:]), values[0]).normalized()
    return Euler(values, mode).to_quaternion()


def _complete_basis(rig, name, fields):
    """Read a complete saved transform without using the current source pose."""
    from . import control_pose_assets as poses

    rotations = [prop for prop in fields if prop.startswith('rotation_')]
    if len(rotations) != 1 or rotations[0] not in poses._TRANSFORMS:
        raise ValueError(f'{name}: mirrored saved Poses need one complete rotation representation.')
    rotation_prop = rotations[0]
    for prop in ('location', rotation_prop, 'scale'):
        if prop not in fields or set(fields[prop]) != set(range(poses._TRANSFORMS[prop])):
            raise ValueError(f'{name}: mirrored saved Poses need complete location, rotation and scale channels.')
    try:
        entries = {prop: [float(fields[prop][index]) for index in range(poses._TRANSFORMS[prop])]
                   for prop in ('location', rotation_prop, 'scale')}
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name}: this saved Pose has invalid transform values.') from exc
    if not all(math.isfinite(value) for values in entries.values() for value in values):
        raise ValueError(f'{name}: this saved Pose has non-finite transforms.')
    location, scale = Vector(entries['location']), Vector(entries['scale'])
    if not all(math.isfinite(value) for values in (location, scale) for value in values):
        raise ValueError(f'{name}: this saved Pose exceeds native transform precision.')
    if min(abs(value) for value in scale) < 1e-8:
        raise ValueError(f'{name}: this saved Pose has a singular scale; it cannot be mirrored exactly.')
    # Keep complete raw location channels, including connected bones. Native
    # Action flip transforms those channels; Pose evaluation separately ignores
    # their displacement. Clamping here breaks native flip round trips.
    if rotation_prop == 'rotation_quaternion':
        rotation = Quaternion(entries[rotation_prop])
    elif rotation_prop == 'rotation_axis_angle':
        axis, angle = Vector(entries[rotation_prop][1:]), entries[rotation_prop][0]
        if axis.length < 1e-8 and abs(angle) > 1e-8:
            raise ValueError(f'{name}: this saved Pose has a zero rotation axis.')
        rotation = Quaternion(axis, angle)
    else:
        mode = rig.pose.bones[name].rotation_mode
        mode = mode if mode not in {'QUATERNION', 'AXIS_ANGLE'} else 'XYZ'
        rotation = Euler(entries[rotation_prop], mode).to_quaternion()
    if not math.isfinite(rotation.magnitude) or rotation.magnitude < 1e-8:
        raise ValueError(f'{name}: this saved Pose has an invalid rotation quaternion.')
    return Matrix.LocRotScale(location, rotation.normalized(), scale)


def _validate_managed_pair(source, target):
    """Limit deformation reflection to the proven standard inheritance chain."""
    for bone in (source, target):
        if (not bone.use_inherit_rotation or bone.inherit_scale != 'FULL'
                or not bone.use_local_location):
            raise ValueError(f'{bone.name}: this saved Pose cannot mirror custom transform inheritance.')
        if not all(math.isfinite(value) for row in bone.matrix_local for value in row):
            raise ValueError(f'{bone.name}: this bone has a non-finite Rest transform.')
    parent = bpy.utils.flip_name(source.parent.name) if source.parent else None
    target_parent = target.parent.name if target.parent else None
    if parent != target_parent or source.use_connect != target.use_connect:
        raise ValueError(f'{source.name}: opposite bones need corresponding parents and connection settings.')


def _mirrored_metadata(rig, metadata, names, native):
    """Derive an operation view without rewriting the source asset or its slot."""
    from copy import deepcopy
    from . import control_pose_assets as poses

    result = deepcopy(metadata)
    result['names'] = sorted(names)
    seeds = poses._validate_stable_ik_seed_metadata(metadata)
    if seeds:
        seed_values, targets = {}, {}
        for sources in seeds.values():
            for name, saved in sources.items():
                target = bpy.utils.flip_name(name)
                if name not in native or target not in native:
                    raise ValueError('This saved Pose has no opposite native IK seed bone.')
                loc, rot, scale = Matrix(saved['matrix_basis']).decompose()
                seed_values[name] = {'location': dict(enumerate(loc)),
                    'rotation_quaternion': dict(enumerate(rot)), 'scale': dict(enumerate(scale))}
                targets[name] = target
        seed_result = _managed_channels(rig, seed_values, targets, list(seed_values),
                                        {name: {} for name in targets.values()})
        flipped = {}
        for key, sources in seeds.items():
            kind, side = key.split('/')
            target_key = kind + '/' + ('R' if side == 'L' else 'L')
            entries = {}
            for name, saved in sources.items():
                target_name = targets[name]
                fields = seed_result[target_name]
                basis = Matrix.LocRotScale(Vector([fields['location'][i] for i in range(3)]),
                    Quaternion([fields['rotation_quaternion'][i] for i in range(4)]),
                    Vector([fields['scale'][i] for i in range(3)]))
                entries[target_name] = dict(rotation_mode=saved['rotation_mode'],
                    matrix_basis=[list(row) for row in basis])
            flipped[target_key] = entries
        result['stable_ik_seed'] = flipped
        poses._validate_stable_ik_seed_metadata(result)
    return result


def _exact_transform_fields(bone, basis):
    """Reject shear and connected-bone offsets before returning native channels."""
    from . import control_pose_assets as poses

    if not all(math.isfinite(value) for row in basis for value in row):
        raise ValueError(f'{bone.name}: mirroring produced a non-finite transform.')
    location, rotation, scale = basis.decompose()
    if not all(math.isfinite(value) for values in (location, rotation, scale) for value in values):
        raise ValueError(f'{bone.name}: mirrored deformation cannot be decomposed into finite Pose channels.')
    if min(abs(value) for value in scale) < 1e-8:
        raise ValueError(f'{bone.name}: mirroring produced a singular scale.')
    if bone.use_connect:
        if max(abs(value) for value in location) > _MATRIX_TOLERANCE:
            raise ValueError(f'{bone.name}: mirrored deformation needs a location offset that a connected bone cannot use.')
        location = Vector((0., 0., 0.))
    rotation.normalize()
    rebuilt = Matrix.LocRotScale(location, rotation, scale)
    tolerance = _MATRIX_TOLERANCE * max(1., max(abs(value) for row in basis for value in row))
    if poses._difference(basis, rebuilt) > tolerance:
        raise ValueError(f'{bone.name}: mirrored deformation has shear; it cannot be restored exactly as Pose channels.')
    return {'location': dict(enumerate(location)),
            'rotation_quaternion': dict(enumerate(rotation)),
            'scale': dict(enumerate(scale))}


def _managed_channels(rig, values, targets, transformed, result):
    """Use native Action flip on complete authored inputs, without binding it.

    The one-slot scratch Action uses each source bone's current representation
    so native flip reads the authored rotation even when live mode is Euler.
    No current parent deformation or partial-channel reconstruction is added.
    """
    from . import control_pose_assets as poses
    fields_by_name = {}
    for name, fields in values.items():
        _validate_managed_pair(rig.data.bones[name], rig.data.bones[targets[name]])
        output = {prop: dict(entries) for prop, entries in fields.items() if prop in poses._BBONE}
        if name in transformed:
            loc, rot, scale = _complete_basis(rig, name, fields).decompose()
            mode = rig.pose.bones[name].rotation_mode
            output.update(location=dict(enumerate(loc)), scale=dict(enumerate(scale)))
            output[_rotation_property(mode)] = dict(enumerate(_rotation_values(rot, mode)))
        fields_by_name[name] = output
    action = bpy.data.actions.new('Character Designer temporary native mirrored Pose')
    try:
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        bag = action.layers.new(name='Pose').strips.new(type='KEYFRAME').channelbag(slot, ensure=True)
        for name, fields in fields_by_name.items():
            for prop, entries in fields.items():
                for index, value in entries.items():
                    bag.fcurves.new(data_path=rig.pose.bones[name].path_from_id(prop), index=index).keyframe_points.insert(1., float(value))
        action.flip_with_pose(rig)
        flipped = poses.channels(action, rig)
        for name, fields in fields_by_name.items():
            target = targets[name]
            for prop, entries in fields.items():
                if prop.startswith('rotation_'):
                    mode = rig.pose.bones[name].rotation_mode
                    rot = _quaternion(flipped[target][prop], mode)
                    result[target]['rotation_quaternion'] = dict(enumerate(rot))
                else:
                    result[target][prop] = {index: flipped[target][prop][index] for index in entries}
            if any(not math.isfinite(value) for entries in result[target].values() for value in entries.values()):
                raise ValueError('Native mirroring produced non-finite Pose channels.')
        return result
    finally:
        bpy.data.actions.remove(action)


def mirrored_channels(rig, values, *, metadata=None):
    """Return the opposite-side pose, leaving all unrelated bones untouched.

    Managed complete transforms and legacy channels use native rest-aware Action
    flip, including asymmetric joints and Rest roll. Both return authored rotations so
    different destination Euler orders are handled correctly.
    """
    from . import control_pose_assets as poses

    if metadata is not None:
        poses._compatible(rig, values, metadata)
    else:
        desired = poses.desired_pose(rig, values)
    native = poses.native_rest(rig)
    targets = {name: bpy.utils.flip_name(name) for name in values}
    missing = sorted(set(targets.values()) - set(native))
    if missing:
        raise ValueError('This Pose has no opposite native bone: ' + ', '.join(missing[:4]))
    if len(set(targets.values())) != len(targets):
        raise ValueError('This Pose has ambiguous opposite-side bone names.')

    result = {target: {prop: dict(entries) for prop, entries in values[name].items()
                       if prop in poses._BBONE}
              for name, target in targets.items()}
    transformed = [name for name, fields in values.items()
                   if any(prop in poses._TRANSFORMS for prop in fields)]
    if metadata is not None:
        if transformed:
            _managed_channels(rig, values, targets, transformed, result)
        return poses._AssetChannels(result, _mirrored_metadata(rig, metadata, result, native))
    if not transformed:
        return result

    action = bpy.data.actions.new('Character Designer temporary mirrored pose')
    try:
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        layer = action.layers.new(name='Pose')
        strip = layer.strips.new(type='KEYFRAME')
        bag = strip.channelbag(slot, ensure=True)
        for name in transformed:
            pb, bone = rig.pose.bones[name], rig.data.bones[name]
            parent = bone.parent
            parent_matrix = (desired.get(parent.name, rig.pose.bones[parent.name].matrix)
                             if parent else Matrix.Identity(4))
            basis = poses._convert(bone, desired[name], parent_matrix, invert=True)
            location, rotation, scale = basis.decompose()
            fields = {'location': location, 'scale': scale,
                      _rotation_property(pb.rotation_mode): _rotation_values(rotation, pb.rotation_mode)}
            for prop, entries in fields.items():
                for index, value in enumerate(entries):
                    curve = bag.fcurves.new(data_path=pb.path_from_id(prop), index=index)
                    curve.keyframe_points.insert(1., float(value))

        # This API changes only Action data, without a clipboard, mode switch,
        # selection change, temporary character, or dependency-graph evaluation.
        action.flip_with_pose(rig)
        flipped = poses.channels(action, rig)
        for name in transformed:
            target, fields = targets[name], values[name]
            for prop in ('location', 'scale'):
                if prop in fields:
                    result[target][prop] = {index: flipped[target][prop][index]
                                            for index in fields[prop]}
            if any(prop.startswith('rotation_') for prop in fields):
                mode = rig.pose.bones[name].rotation_mode
                rotation = _quaternion(flipped[target][_rotation_property(mode)], mode)
                result[target]['rotation_quaternion'] = dict(enumerate(rotation))
        return result
    finally:
        bpy.data.actions.remove(action)
