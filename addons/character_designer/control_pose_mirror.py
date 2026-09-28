"""Mirror native Pose channels with Blender's own rest-aware Action flip.

Only an unattached, disposable Action is edited here. The character and source
asset are read-only; the normal pose service retains matching, undo and auto-key.
"""

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector


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


def mirrored_channels(rig, values):
    """Return the opposite-side pose, leaving all unrelated bones untouched.

    Blender's flip uses both bones' Rest matrices and its standard side-name
    mapping, including center-bone handling. Complete intermediate transforms
    avoid reading dormant native transform channels beneath generated controls.
    Only the asset's location/scale components and B-Bone properties escape the
    scratch Action; an authored rotation is returned as a complete quaternion so
    different destination Euler orders are handled correctly.
    """
    from . import control_pose_assets as poses

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
