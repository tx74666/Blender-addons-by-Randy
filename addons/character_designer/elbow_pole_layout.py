"""Keep the displayed elbow pole on the native arm's evaluated bend."""

import math

from .knee_pole_layout import bend_position


MARKER = 'character_designer_elbow_pole_layout'
VERSION = 1
ALIGNMENT_TOLERANCE_DEGREES = 5.


def supports(inventory, limb):
    """A Direct arm uses its native IK constraint to compensate source-bone roll."""
    from . import limb_ik
    return (inventory['schema'] in {limb_ik.LEGACY_DIRECT_PREROLL_SCHEMA, limb_ik.DIRECT_PREROLL_SCHEMA}
            and limb['pole'].get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
            and limb['pole'].get(limb_ik.KIND_KEY) == 'ARM')


def position(armature, limb, desired, *, previous=None):
    """Use the shoulder, elbow and wrist; the wrist's axial twist stays independent."""
    return bend_position(armature, limb, desired, previous=previous, fallback=(0., 1., 0.),
                         label='The elbow pole needs an arm')


def alignment_error(armature, limb):
    """Visible guide error in degrees, or None for an indeterminate near-straight elbow."""
    from . import limb_ik
    shoulder, elbow, wrist = (armature.pose.bones[name].head for name in limb['chain'])
    upper, lower = elbow - shoulder, wrist - elbow
    span = upper.length + lower.length
    axis = wrist - shoulder
    if span < 1.e-8:
        return None
    if axis.length < span * 1.e-5:
        direction = upper
    else:
        axis.normalize()
        direction = upper - axis * upper.dot(axis)
    if direction.length < .01 * span:
        return None
    matrix = armature.matrix_world
    pole = armature.pose.bones[limb['pole'].name]
    anchor = limb_ik._custom_shape_anchor_world(armature, pole)
    return math.degrees((anchor - matrix @ elbow).angle(matrix.to_3x3() @ direction, math.pi))
