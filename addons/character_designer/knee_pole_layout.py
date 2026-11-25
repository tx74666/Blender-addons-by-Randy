"""Place an animator's knee pole from the evaluated hip, knee and ankle."""

import math

from mathutils import Quaternion, Vector


def condition_shin(hip, shin, ankle):
    """Cap the native pre-IK bend on the independent IK edit, keeping its bend plane and roll."""
    upper = shin.translation - hip.translation
    lower = ankle.translation - shin.translation
    if min(upper.length, lower.length) < 1.e-8:
        raise ValueError('The knee solver needs two leg segments with measurable length.')
    normal = upper.cross(lower)
    angle = math.atan2(normal.length, upper.dot(lower))
    limit = math.radians(65.)
    if angle <= limit:
        return shin.copy(), False
    if normal.length <= upper.length * lower.length * 1.e-6:
        # At a complete fold either plane contains the collinear segments.
        # Prefer the bone-local hinge, projected into the plane normal to the thigh.
        axis = upper.normalized()
        axes = (shin.to_3x3().col[i] for i in (0, 2))
        normal = max((a - axis*a.dot(axis) for a in axes), key=lambda a: a.length)
    result = Quaternion(normal.normalized(), limit-angle).to_matrix().to_4x4() @ shin
    result.translation = shin.translation
    return result, True


def position(armature, limb, desired, *, previous=None):
    """Return the armature-space handle position. Previous is the last matched handle."""
    return bend_position(armature, limb, desired, previous=previous, fallback=(0., -1., 0.),
                         label='The knee pole needs a leg')


def bend_position(armature, limb, desired, *, previous, fallback, label):
    """Choose a stable armature-space point in the evaluated limb's bend plane."""
    upper_name, lower_name, end_name = limb['chain']
    start, joint, end = (desired[name].translation for name in (upper_name, lower_name, end_name))
    upper = joint - start
    span = upper.length + (end - joint).length
    if not math.isfinite(span) or span < 1.e-8:
        raise ValueError(label + ' with measurable length.')
    rest_pole = armature.data.bones[limb['pole'].name].head_local
    rest_joint = armature.data.bones[lower_name].head_local
    distance = (rest_pole - rest_joint).length
    if distance < span * .01:
        distance = .75 * span
    axis = end - start
    if axis.length < span * 1.e-5:
        # At a full fold the middle joint still has a direction from the start.
        return joint + upper.normalized() * distance
    axis.normalize()
    radial = upper - axis * upper.dot(axis)
    bend = radial.length / span
    if bend >= .01:
        return joint + radial.normalized() * distance

    rest_upper = armature.data.bones[upper_name].matrix_local
    rotation = desired[upper_name].to_quaternion() @ rest_upper.to_quaternion().inverted()
    reference = rotation @ (rest_pole - rest_joint)
    candidates = []
    if previous is not None:
        candidates.append(previous - joint)
    candidates.extend((reference, rotation @ Vector(limb.get('pole_direction') or fallback)))
    for vector in candidates:
        projected = vector - axis * vector.dot(axis)
        if projected.length > 1.e-7 * max(1., vector.length):
            hint = projected.normalized()
            break
    else:
        hint = axis.orthogonal().normalized()
    # A straight chain has no unique bending plane. Ease out of the last
    # reliable plane until joint displacement supplies a stable normal.
    if bend > .002:
        direction = radial.normalized()
        angle = math.atan2(axis.dot(hint.cross(direction)), hint.dot(direction))
        weight = (bend - .002) / .008
        hint = Quaternion(axis, angle * weight * weight * (3. - 2. * weight)) @ hint
    return joint + hint * distance
