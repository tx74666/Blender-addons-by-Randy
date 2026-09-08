"""Geometry for the bounded forearm twist prototype; no scene mutations.

All points, deformation matrices, the rest axis and the wrist pivot must use
the same coordinate system.  A deformation matrix is ``pose @ rest.inverted()``
(conjugated into mesh space when necessary), not a pose-bone matrix alone.

The output is an absolute pre-Armature point.  Callers subtract their uncorrected
pre-Armature point to obtain a relative Shape Key delta.  Always start from that
uncorrected point, never from the previous corrected result.
"""

from __future__ import annotations

import math

from mathutils import Matrix, Quaternion, Vector


class TwistMathError(ValueError):
    """The current input cannot be safely represented by this prototype."""


def _finite(values):
    return all(math.isfinite(float(value)) for value in values)


def _point(value, label="Point"):
    result = Vector(value)
    if len(result) != 3 or not _finite(result):
        raise TwistMathError(f"{label} must be a finite three-dimensional vector")
    return result


def _axis(value):
    result = _point(value, "Forearm axis")
    if result.length_squared < 1.0e-12:
        raise TwistMathError("Forearm axis has zero length")
    return result.normalized()


def _rotation(matrix):
    """Extract rotation only from positive, uniformly scaled rigid transforms."""
    if len(matrix) != 4 or len(matrix[0]) != 4:
        raise TwistMathError("Bone deformation must be a 4 by 4 matrix")
    if not _finite(value for row in matrix for value in row):
        raise TwistMathError("Bone deformation contains non-finite values")
    if max(abs(matrix[3][index]) for index in range(3)) > 1.0e-6 or abs(matrix[3][3] - 1.0) > 1.0e-6:
        raise TwistMathError("Bone deformation must be affine")
    basis = matrix.to_3x3()
    columns = [basis.col[index].copy() for index in range(3)]
    lengths = [column.length for column in columns]
    scale = sum(lengths) / 3.0
    if scale < 1.0e-7 or basis.determinant() <= 0.0:
        raise TwistMathError("Zero or reflected bone scale is unsupported")
    if max(abs(length - scale) for length in lengths) > scale * 1.0e-4:
        raise TwistMathError("Nonuniform bone scale is unsupported")
    if any(abs(columns[a].dot(columns[b])) > scale * scale * 1.0e-4
           for a, b in ((0, 1), (0, 2), (1, 2))):
        raise TwistMathError("Sheared bone deformation is unsupported")
    return basis.to_quaternion().normalized()


def twist_angle(lower_matrix, hand_matrix, axis):
    """Signed relative axial twist in radians, on the principal [-pi, pi] branch.

    This uses deformation rotations, so differing hand/forearm rest orientations
    do not create a spurious twist.  For q = swing * twist, the twist is the
    quaternion projection onto the *rest* forearm axis.  At a 180-degree swing
    perpendicular to that axis this decomposition is undefined and is rejected.
    """
    axis = _axis(axis)
    relative = _rotation(lower_matrix).conjugated() @ _rotation(hand_matrix)
    relative.normalize()
    if relative.w < 0.0:
        relative.negate()
    projected = Vector((relative.x, relative.y, relative.z)).dot(axis)
    if relative.w * relative.w + projected * projected < 1.0e-12:
        raise TwistMathError("Axial twist is undefined at this 180-degree wrist swing")
    angle = math.remainder(2.0 * math.atan2(projected, relative.w), math.tau)
    return 0.0 if abs(angle) < 1.0e-8 else angle


def _weights(transforms, weights):
    result = {}
    for name, weight in weights.items():
        weight = float(weight)
        if not math.isfinite(weight) or weight < 0.0:
            raise TwistMathError("Bone weights must be finite and nonnegative")
        if weight == 0.0:
            continue
        if name not in transforms:
            raise TwistMathError(f"Missing deformation matrix for bone '{name}'")
        result[name] = weight
    total = sum(result.values())
    if not result:
        return result
    if abs(total - 1.0) > 1.0e-4:
        raise TwistMathError("Bone weights must be normalized across all deform influences")
    return {name: weight / total for name, weight in result.items()}


def blended_matrix(transforms, weights):
    """Actual linear skinning matrix for normalized deform-bone weights."""
    weights = _weights(transforms, weights)
    if not weights:
        return Matrix.Identity(4)
    result = Matrix(tuple(tuple(sum(weight * transforms[name][row][column]
                                    for name, weight in weights.items())
                              for column in range(4)) for row in range(4)))
    if not _finite(value for row in result for value in row):
        raise TwistMathError("Blended deformation contains non-finite values")
    return result


def rotate_about_axis(point, axis, pivot, angle):
    """Rotate along a circular arc, preserving distance to the rest axis."""
    point = _point(point)
    pivot = _point(pivot, "Wrist pivot")
    if not math.isfinite(angle):
        raise TwistMathError("Twist angle must be finite")
    return pivot + Quaternion(_axis(axis), angle) @ (point - pivot)


def desired_vertex(point, transforms, weights, lower_name, hand_name,
                   axis, pivot, ratio, *, angle=None):
    """Return the desired post-Armature position before inverse skinning.

    Replace only the lower/hand contribution.  Lower receives ``ratio * angle``
    in rest space.  Hand receives ``(ratio - 1) * angle`` before its existing
    transform, removing its existing axial twist and adding the requested twist
    exactly once.  Its swing, translation and the other bones' contributions
    remain in the skinning expression.

    For pure axial rotation with lower/hand weights summing to one, both terms
    land on the identical circular-arc point, regardless of their weight split.
    Swing still uses the original linear blend; this is not general dual-
    quaternion skinning or an arbitrary-pose volume preservation claim.
    """
    point = _point(point)
    axis = _axis(axis)
    pivot = _point(pivot, "Wrist pivot")
    ratio = float(ratio)
    if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
        raise TwistMathError("Ring twist ratio must be between zero and one")
    weights = _weights(transforms, weights)
    actual = blended_matrix(transforms, weights) @ point
    lower_weight = weights.get(lower_name, 0.0)
    hand_weight = weights.get(hand_name, 0.0)
    if lower_weight + hand_weight == 0.0:
        return actual
    if lower_name not in transforms or hand_name not in transforms:
        raise TwistMathError("Both forearm and hand deformation matrices are required")
    if angle is None:
        angle = twist_angle(transforms[lower_name], transforms[hand_name], axis)
    if not math.isfinite(angle):
        raise TwistMathError("Twist angle must be finite")
    if abs(angle) < 1.0e-8:
        return actual
    desired = actual.copy()
    for name, weight, fraction in ((lower_name, lower_weight, ratio),
                                    (hand_name, hand_weight, ratio - 1.0)):
        if weight == 0.0 or fraction == 0.0:
            continue
        rotated = rotate_about_axis(point, axis, pivot, fraction * angle)
        desired += weight * (transforms[name] @ rotated - transforms[name] @ point)
    return desired


def corrected_vertex(point, transforms, weights, lower_name, hand_name,
                     axis, pivot, ratio, *, angle=None, max_condition=1.0e5):
    """Return an absolute pre-Armature point producing ``desired_vertex``.

    Inverse LBS cannot repair a singular transform (the familiar 50/50 blend at
    180 degrees is one).  Reject it before generating an unbounded correction.
    The runtime should report/disable this pose instead of accumulating deltas.
    """
    point = _point(point)
    desired = desired_vertex(point, transforms, weights, lower_name, hand_name,
                             axis, pivot, ratio, angle=angle)
    actual = blended_matrix(transforms, weights)
    delta = desired - actual @ point
    if delta.length_squared < 1.0e-18:
        return point.copy()
    linear = actual.to_3x3()
    norm = math.sqrt(sum(value * value for row in linear for value in row))
    if norm < 1.0e-9 or abs(linear.determinant()) < 1.0e-9 * norm ** 3:
        raise TwistMathError("The current linear skinning blend is singular")
    try:
        inverse = linear.inverted()
    except ValueError as exc:
        raise TwistMathError("The current linear skinning blend cannot be inverted") from exc
    inverse_norm = math.sqrt(sum(value * value for row in inverse for value in row))
    if norm * inverse_norm > max_condition:
        raise TwistMathError("The current linear skinning blend is too close to singular")
    corrected = point + inverse @ delta
    if not _finite(corrected):
        raise TwistMathError("Inverse skinning produced a non-finite correction")
    return corrected


def profile_ratio(position, knots):
    """Interpolate saved (axial position, absolute twist ratio) calibration knots.

    Endpoints are clamped and each segment uses smoothstep, avoiding abrupt
    changes between neighboring loops.  The ratios are absolute fractions of
    the hand angle, not a set of fractions that must sum to one.
    """
    position = float(position)
    if not math.isfinite(position):
        raise TwistMathError("Axial position must be finite")
    knots = sorted((float(at), float(ratio)) for at, ratio in knots)
    if not knots:
        raise TwistMathError("A twist profile needs at least one calibration knot")
    if any(not _finite((at, ratio)) or not 0.0 <= ratio <= 1.0 for at, ratio in knots):
        raise TwistMathError("Profile knots must be finite with ratios between zero and one")
    if any(right[0] - left[0] <= 1.0e-8 for left, right in zip(knots, knots[1:])):
        raise TwistMathError("Profile knot positions must be distinct")
    if position <= knots[0][0]:
        return knots[0][1]
    if position >= knots[-1][0]:
        return knots[-1][1]
    for left, right in zip(knots, knots[1:]):
        if position <= right[0]:
            factor = (position - left[0]) / (right[0] - left[0])
            factor = factor * factor * (3.0 - 2.0 * factor)
            return left[1] + (right[1] - left[1]) * factor
    raise AssertionError("Unreachable profile interval")
