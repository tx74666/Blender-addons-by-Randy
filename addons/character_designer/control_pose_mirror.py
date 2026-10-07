"""Mirror saved native poses without transferring asymmetric Rest roll.

Managed poses reflect their local deformation increments. Legacy poses retain
Blender's disposable Action flip. The character and source asset are read-only;
the normal pose service retains matching, undo and auto-key.
"""

import math

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

_MATRIX_TOLERANCE = 2e-6
_HEAD_TOLERANCE = 2e-5


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
    if rig.data.bones[name].use_connect:
        if max(abs(value) for value in location) > _MATRIX_TOLERANCE:
            raise ValueError(f'{name}: connected bones cannot mirror nonzero location channels.')
        location = Vector((0., 0., 0.))
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
    head = source.matrix_local.translation.copy()
    head.x = -head.x
    if (head - target.matrix_local.translation).length > _HEAD_TOLERANCE:
        raise ValueError(f'{source.name}: opposite Rest joint positions differ; this Pose cannot be mirrored exactly.')


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
    """Reflect each saved local skin increment, including center bones.

    For standard inheritance, skin_b = skin_parent @ (Rest_b @ basis_b @
    Rest_b.inverted()). Reflect that increment and express it in the target
    Rest: basis_R = Rest_R^-1 @ F @ Rest_L @ basis_L @ Rest_L^-1 @ F @ Rest_R.
    Identity stays identity even when the opposite Rest rolls differ. No current
    source-parent deformation is copied into a partial limb or hand Pose.
    """
    reflection = Matrix.Diagonal((-1., 1., 1., 1.))
    for name in transformed:
        source, target = rig.data.bones[name], rig.data.bones[targets[name]]
        _validate_managed_pair(source, target)
        basis = _complete_basis(rig, name, values[name])
        source_rest, target_rest = source.matrix_local.copy(), target.matrix_local.copy()
        mirrored = (target_rest.inverted() @ reflection @ source_rest @ basis
                    @ source_rest.inverted() @ reflection @ target_rest)
        result[target.name].update(_exact_transform_fields(target, mirrored))
    return result


def mirrored_channels(rig, values, *, metadata=None):
    """Return the opposite-side pose, leaving all unrelated bones untouched.

    Managed complete transforms reflect local deformation about corresponding
    Rest joints. Legacy channels retain Blender's rest-aware Action flip and
    keyed location/scale components. Both return complete authored rotations so
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
    if not transformed:
        return result
    if metadata is not None:
        return _managed_channels(rig, values, targets, transformed, result)

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
