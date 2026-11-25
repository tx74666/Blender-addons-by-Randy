"""Batched native/world pose comparisons for imported daily FK playback."""
import numpy as np


def _components(matrices):
    # Keep Blender's own TRS decomposition and signed scale convention.
    values = [matrix.decompose() for matrix in matrices]
    locations, rotations, scales = zip(*values)
    return tuple(np.asarray(field, dtype=np.float64) for field in (locations, rotations, scales))


def _rotation_stretch(rotations, matrices):
    w, x, y, z = rotations.T
    rotation = np.stack((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
                         2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
                         2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)), axis=1)
    return rotation.reshape(-1, 3, 3).transpose(0, 2, 1) @ matrices


def _world_components(matrices):
    rows = np.asarray(matrices, dtype=np.float64)
    axes = rows[:, :3, :3]
    column_volume = np.prod(np.linalg.norm(axes, axis=1), axis=1)
    locations, rotations, scales = _components(matrices)
    lengths = np.linalg.norm(rotations, axis=1, keepdims=True)
    if (not np.isfinite(rows).all() or not np.isfinite(column_volume).all()
            or np.any(column_volume <= 0) or np.any(abs(np.linalg.det(axes)) <= column_volume * 1.e-12)
            or np.any(lengths <= 1.e-12) or np.any(scales == 0)):
        raise ValueError('World playback has a singular or non-finite body matrix.')
    rotations = rotations / lengths
    return rows, locations, rotations, scales, _rotation_stretch(rotations, axes)


def _maximum(delta):
    value = float(np.max(delta))
    if not np.isfinite(value):
        raise ValueError('Controller playback has non-finite pose error.')
    return value


def compare(actual_world, actual, expected_world, expected, meters_per_unit, *, native):
    """Compare corresponding armature-local pose matrices on the same rig.

    Array math uses doubles. Native quaternion differences retain Blender's
    API behavior. World shortest angles use normalized quaternion chords and
    remain stable near zero or when quaternion signs differ.
    """
    if not actual or len(actual) != len(expected):
        raise ValueError('FK playback needs matching nonempty bone samples.')
    native_error = None
    if native:
        native_a, native_b = [actual_world, *actual], [expected_world, *expected]
        first = [m.decompose() for m in native_a]
        second = [m.decompose() for m in native_b]
        native_error = dict(matrix=_maximum(abs(np.asarray(native_a, dtype=np.float64) -
                                                np.asarray(native_b, dtype=np.float64))),
                            position=max((a[0] - b[0]).length for a, b in zip(first, second)),
                            rotation=max(a[1].rotation_difference(b[1]).angle for a, b in zip(first, second)),
                            scale=_maximum(abs(np.asarray([a[2] for a in first]) -
                                               np.asarray([b[2] for b in second]))))
    ma, la, qa, sa, stretch_a = _world_components([actual_world, *(actual_world @ m for m in actual)])
    mb, lb, qb, sb, stretch_b = _world_components([expected_world, *(expected_world @ m for m in expected)])
    signs = np.where(np.sum(qa * qb, axis=1, keepdims=True) < 0., -1., 1.)
    near = np.linalg.norm(qa - signs * qb, axis=1)
    far = np.linalg.norm(qa + signs * qb, axis=1)
    return native_error, dict(world_matrix=_maximum(abs(ma-mb)),
                              position_meters=_maximum(np.linalg.norm(la-lb, axis=1)) * meters_per_unit,
                              rotation_radians=_maximum(4. * np.arctan2(near, far)),
                              scale_relative=_maximum(abs((sa-sb)/sb)),
                              shape_relative=_maximum(abs((stretch_a-stretch_b)/sb[:, None, :])))
