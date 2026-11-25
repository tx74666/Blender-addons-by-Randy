"""Match an edited native pose to the already calibrated, evaluated limb solver."""


import numpy as np
from mathutils import Euler, Matrix, Quaternion, Vector

from . import limb_ik_fk


def _basis(entry):
    values, mode = entry['channels'], entry['mode']
    if mode == 'QUATERNION':
        rotation = Quaternion(values['rotation_quaternion'])
    elif mode == 'AXIS_ANGLE':
        angle, *axis = values['rotation_axis_angle']
        rotation = Quaternion(Vector(axis), angle) if any(axis) else Quaternion()
    else:
        rotation = Euler(values['rotation_euler'], mode)
    return Matrix.LocRotScale(Vector(values['location']), rotation, Vector(values['scale']))


def _rotation_field(bone):
    return {'QUATERNION': 'rotation_quaternion', 'AXIS_ANGLE': 'rotation_axis_angle'}.get(
        bone.rotation_mode, 'rotation_euler')


def _parameters(bone, *, rotate=True, translate=False):
    fields = []
    if translate:
        fields.extend(('location', i) for i in range(3) if not bone.lock_location[i])
    if rotate:
        field = _rotation_field(bone)
        if field == 'rotation_euler':
            fields.extend((field, i) for i in range(3) if not bone.lock_rotation[i])
        elif not any(bone.lock_rotation) and not (bone.lock_rotations_4d and bone.lock_rotation_w):
            fields.extend((field, i) for i in range(4))
    return [(bone, field, axis) for field, axis in fields]


def _residual(bones, wanted, length):
    rows = []
    for bone, goal in zip(bones, wanted):
        actual = bone.matrix
        rows.extend((goal.translation - actual.translation) / length)
        delta = actual.to_quaternion().rotation_difference(goal.to_quaternion())
        if delta.w < 0:
            delta.negate()
        rows.extend((2 * delta.x, 2 * delta.y, 2 * delta.z))
    return np.asarray(rows)


def _assign(inputs, values):
    for (bone, field, axis), value in zip(inputs, values):
        getattr(bone, field)[axis] = value


def _fit(context, bones, goals, inputs, length):
    """Match editable native/control channels and retain only steps that improve the pose."""
    if not inputs:
        return
    for _ in range(6):
        residual = _residual(bones, goals, length)
        if np.max(np.abs(residual)) < 8.e-5:
            return
        values = np.array([getattr(bone, field)[axis] for bone, field, axis in inputs])
        jacobian = np.empty((len(residual), len(inputs)))
        for j, (bone, field, axis) in enumerate(inputs):
            step = 0.001 * (length if field == 'location' else 1.0)
            channel = getattr(bone, field)
            channel[axis] = values[j] + step
            context.view_layer.update()
            plus = _residual(bones, goals, length)
            channel[axis] = values[j] - step
            context.view_layer.update()
            minus = _residual(bones, goals, length)
            channel[axis] = values[j]
            context.view_layer.update()
            jacobian[:, j] = (minus - plus) / (2 * step)
        # Damping keeps a straight IK chain from taking a large step along a nearly free axis.
        system = np.vstack((jacobian, .02 * np.eye(len(inputs))))
        delta = np.linalg.lstsq(system, np.concatenate((residual, np.zeros(len(inputs)))), rcond=1.e-7)[0]
        delta *= min(1.0, .35 / max(1.e-12, np.max(np.abs(delta))))
        for factor in (1.0, .5, .25, .1):
            _assign(inputs, values + factor * delta)
            context.view_layer.update()
            if np.linalg.norm(_residual(bones, goals, length)) < np.linalg.norm(residual):
                break
        else:
            _assign(inputs, values)
            context.view_layer.update()
            return


def _matches(bones, goals):
    from . import control_pose_assets
    return all(control_pose_assets._difference(bone.matrix, goal) < 2.e-4
               for bone, goal in zip(bones, goals))


def match(context, rig, saved, desired, edited, inventory):
    from . import body_original_mode as original
    # A rotation relative to the baked Original pose is a local edit, never
    # an instruction to replace an unedited controller or change its IK blend.
    switched = []
    for name in desired:
        if name not in edited:
            continue
        previous = saved['entered_channels'][name]
        if edited[name] != previous:
            bone = rig.pose.bones[name]
            old = _basis(previous)
            if old.inverted(None) is None:
                raise ValueError(f'{name} has zero scale; keep the Original edit.')
            bone.matrix_basis = bone.matrix_basis @ old.inverted() @ _basis(edited[name])
    original._update(context, rig)
    for (kind, side), entry in inventory['rigs'].items():
        names = limb_ik_fk.pose_names(entry)
        bones = [rig.pose.bones[n] for n in names]
        goals = [desired[n] for n in names]
        if _matches(bones, goals):
            continue
        target = rig.pose.bones[entry['target'].name]
        pole = rig.pose.bones[entry['pole'].name]
        length = max(sum(bone.bone.length for bone in bones[:2]), .001)
        foot = entry.get('foot_controls')
        toe_inputs = _parameters(rig.pose.bones[foot['toe_control']]) if foot else []
        inputs = _parameters(target, translate=True) + _parameters(pole, rotate=False, translate=True)
        for bone in bones:
            inputs.extend(_parameters(bone))
        inputs.extend(toe_inputs)
        drivers = {(curve.data_path, curve.array_index) for curve in rig.animation_data.drivers} if rig.animation_data else set()
        inputs = [item for item in inputs if (item[0].path_from_id(item[1]), item[2]) not in drivers]
        initial = [getattr(bone, field)[axis] for bone, field, axis in inputs]
        if foot and _matches(bones[:-1], goals[:-1]):
            _fit(context, bones[-1:], goals[-1:], [item for item in toe_inputs if item in inputs], length)
        if not _matches(bones, goals):
            _fit(context, bones, goals, inputs, length)
        if _matches(bones, goals):
            continue
        # Discard the failed fit before trying FK. Keep every unedited limb in its chosen mode.
        if target[limb_ik_fk.PROPERTY] == 0 or not any(
                edited[name] != saved['entered_channels'][name] for name in names):
            continue
        _assign(inputs, initial)
        target[limb_ik_fk.PROPERTY] = 0.0
        original._update(context, rig)
        _fit(context, bones, goals, inputs, length)
        if not _matches(bones, goals):
            raise ValueError(f"Controls could not match the pose of '{bones[1].name}'. Your Original Bones edit is still active.")
        switched.append(f"{'Left' if side == 'L' else 'Right'} {kind.lower()}")
    return switched
