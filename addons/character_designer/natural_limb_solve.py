"""Neutral control inputs and the evaluated Original Bones bind check."""

import math

from . import limb_ik_fk


def _unlocked(bone, field, values, locks):
    channel = getattr(bone, field)
    for axis, value in enumerate(values):
        if not locks[axis] and channel[axis] != value:
            channel[axis] = value


def _zero(bone):
    _unlocked(bone, 'location', (0., 0., 0.), bone.lock_location)
    _unlocked(bone, 'scale', (1., 1., 1.), bone.lock_scale)
    if bone.rotation_mode in {'QUATERNION', 'AXIS_ANGLE'}:
        locked_w = bone.lock_rotations_4d and bone.lock_rotation_w
        locks = (locked_w, *bone.lock_rotation)
        if bone.rotation_mode == 'QUATERNION':
            _unlocked(bone, 'rotation_quaternion', (1., 0., 0., 0.), locks)
        else:
            # The axis is immaterial at zero angle. Keep the artist's axis.
            if not locked_w:
                bone.rotation_axis_angle[0] = 0.
    else:
        _unlocked(bone, 'rotation_euler', (0., 0., 0.), bone.lock_rotation)


def reset_limbs(context, rig, inventory):
    """Use the public zero inputs, including the original FK side of a blended limb."""
    names = set()
    for entry in inventory['rigs'].values():
        names.update(limb_ik_fk.pose_names(entry))
        names.update((entry['target'].name, entry['pole'].name))
        if entry.get('heel'):
            names.add(entry['heel'].name)
        foot = entry.get('foot_controls')
        if foot:
            names.update((foot['roll'], foot['toe_control']))
    for name in names:
        _zero(rig.pose.bones[name])
    limb_ik_fk._update(context, rig)


def verify_bind(rig, inventory):
    """Reject a failed or singular solve before the unkeyed pose is committed.

    Compare head, tail and all three axes in armature space. A bone's full
    3x3 matters even when its endpoints happen to match.
    """
    # Blender's evaluated bone matrices store IEEE binary32 components.
    unit_roundoff = 2.0 ** -23
    # A foot may sit near the armature origin while its two-bone IK has
    # travelled down from the hip. Use that chain's actual coordinate scale
    # for positional roundoff throughout the chain.
    for entry in inventory['rigs'].values():
        names = limb_ik_fk.pose_names(entry)
        extent = max(max(rig.data.bones[n].length, rig.data.bones[n].head_local.length,
                         rig.data.bones[n].tail_local.length) for n in names)
        for name in names:
            pb = rig.pose.bones[name]
            rest = pb.bone.matrix_local
            head = (pb.head - rest.translation).length
            tail = (pb.tail - pb.bone.tail_local).length
            axes = pb.matrix.to_3x3() - rest.to_3x3()
            drift = max(axes.col[i].length for i in range(3))
            if (not all(map(math.isfinite, (head, tail, drift))) or
                    max(head, tail) > 8 * unit_roundoff * extent or drift > 8 * unit_roundoff):
                raise ValueError(f'{name} could not reach its original bind transform.')
