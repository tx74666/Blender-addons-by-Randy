"""Measure a raw FBX import against the actual Unity weapon mesh geometry.

This module never chooses an arbitrary orientation from a symmetric point cloud.
It admits one proved uniform scale/orthogonal coordinate transform, or fails.
"""
import hashlib
import itertools
import math
import struct


class WeaponGeometryError(ValueError):
    pass


def _points(values, label):
    import numpy as np
    array = np.asarray(values, dtype=np.float64)
    if (array.ndim != 2 or array.shape[1] != 3 or len(array) < 4
            or len(array) > 250000 or not np.isfinite(array).all()
            or np.max(np.abs(array)) > 1e9):
        raise WeaponGeometryError(label + ' must contain finite 3D vertices.')
    center = (array.min(axis=0) + array.max(axis=0)) * 0.5
    extent = float(np.max(array.max(axis=0) - array.min(axis=0)))
    if extent <= 1e-10:
        raise WeaponGeometryError(label + ' has no measurable extent.')
    # UV/normal splits create duplicate vertices in either importer. Deduplicate
    # in relative coordinates; never use duplicate count as a unit-scale proof.
    quantized = np.rint((array - center) / extent * 1e6).astype(np.int64)
    _, indices = np.unique(quantized, axis=0, return_index=True)
    unique = array[np.sort(indices)]
    if len(unique) < 4:
        raise WeaponGeometryError(label + ' has too few distinct vertices.')
    return unique


def geometry_sha256(values):
    """Selected actual positions, float32 little endian; no FBX binary proxy."""
    h = hashlib.sha256()
    for point in values:
        if len(point) != 3 or any(not math.isfinite(float(v)) for v in point):
            raise WeaponGeometryError('Invalid weapon geometry.')
        h.update(struct.pack('<3f', *(float(v) for v in point)))
    return h.hexdigest()


def _nearest_error(source, target):
    import numpy as np
    try:
        from mathutils.kdtree import KDTree
    except ImportError:
        # Small pure fixtures use the bounded fallback; native Blender uses its
        # KDTree and does not allocate an all-vertices squared-distance matrix.
        maximum = 0.0
        for start in range(0, len(source), 32):
            part = source[start:start + 32]
            nearest = np.full(len(part), np.inf)
            for other in range(0, len(target), 4096):
                chunk = target[other:other + 4096]
                distances = np.sum((part[:, None, :] - chunk[None, :, :]) ** 2, axis=2)
                nearest = np.minimum(nearest, distances.min(axis=1))
            maximum = max(maximum, float(np.sqrt(nearest).max()))
        return maximum
    tree = KDTree(len(target))
    for index, point in enumerate(target):
        tree.insert(tuple(point), index)
    tree.balance()
    return max(float(tree.find(tuple(point))[2]) for point in source)


def compare_surface_points(actual, expected, *, tolerance_metres=2e-5):
    """Compare placed surfaces directly; never fit away a bad mount or scale."""
    import numpy as np
    if not math.isfinite(tolerance_metres) or not 0 < tolerance_metres <= 2e-4:
        raise WeaponGeometryError('Invalid placed weapon comparison policy.')
    a, b = _points(actual, 'Placed FBX'), _points(expected, 'Placed Unity renderer')
    if len(a) != len(b):
        raise WeaponGeometryError('Placed weapon has different distinct geometry.')
    error = max(_nearest_error(a, b), _nearest_error(b, a))
    bounds_error = max(float(np.max(np.abs(a.min(axis=0) - b.min(axis=0)))),
                       float(np.max(np.abs(a.max(axis=0) - b.max(axis=0)))))
    if error > tolerance_metres or bounds_error > tolerance_metres:
        raise WeaponGeometryError('Placed weapon differs from the actual Unity mount/scale.')
    return {'placed_surface_error_metres': error, 'placed_bounds_error_metres': bounds_error,
            'placed_distinct_vertices': len(a), 'placed_surface_proved': True}


def fit_import_geometry(imported, unity_metres, *, tolerance_metres=2e-5,
                        determinant_sign=-1):
    """Return one measured imported-coordinate -> Unity-metres affine matrix.

    Actual renderer geometry is required. Nonuniform distortion, incomplete
    meshes and an ambiguous forward/grip orientation reject before attachment.
    The caller still authenticates the source file/context hashes and mount.
    """
    import numpy as np
    if (not math.isfinite(tolerance_metres) or not 0 < tolerance_metres <= 2e-4
            or determinant_sign not in (-1, 1)):
        raise WeaponGeometryError('Invalid weapon geometry comparison policy.')
    a, b = _points(imported, 'FBX import'), _points(unity_metres, 'Unity renderer')
    if len(a) != len(b):
        raise WeaponGeometryError('Raw import and Unity renderer have different distinct geometry.')
    ac, bc = a.mean(axis=0), b.mean(axis=0)
    x, y = a - ac, b - bc
    ae, av = np.linalg.eigh(x.T @ x / len(x))
    be, bv = np.linalg.eigh(y.T @ y / len(y))
    if ae[0] <= 1e-12 * ae[-1] or be[0] <= 1e-12 * be[-1]:
        raise WeaponGeometryError('Weapon geometry is too planar to prove its coordinate frame.')
    accepted = []
    for permutation in itertools.permutations(range(3)):
        ratios = np.sqrt(be / ae[list(permutation)])
        scale = float(ratios.mean())
        if not math.isfinite(scale) or scale <= 1e-10 or np.max(np.abs(ratios / scale - 1)) > 2e-4:
            continue
        axes = av[:, list(permutation)]
        for signs in itertools.product((-1, 1), repeat=3):
            rotation = bv @ np.diag(signs) @ axes.T
            if float(np.linalg.det(rotation)) * determinant_sign < 0.999999:
                continue
            translation = bc - scale * rotation @ ac
            transformed = a @ rotation.T * scale + translation
            error = max(_nearest_error(transformed, b), _nearest_error(b, transformed))
            if error <= tolerance_metres:
                matrix = np.eye(4)
                matrix[:3, :3] = rotation * scale
                matrix[:3, 3] = translation
                if not any(np.max(np.abs(rotation - previous['rotation'])) <= 1e-6
                           and abs(scale / previous['scale'] - 1) <= 1e-6
                           and np.max(np.abs(translation - previous['translation'])) <= tolerance_metres * .01
                           for previous in accepted):
                    accepted.append({'matrix': matrix, 'rotation': rotation, 'translation': translation,
                                     'error': error, 'scale': scale})
    if len(accepted) != 1:
        reason = 'ambiguous' if accepted else 'unmatched'
        raise WeaponGeometryError('Weapon coordinate proof is ' + reason + '; inspect the captured source geometry before retrying.')
    result = accepted[0]
    return {'matrix': result['matrix'].tolist(), 'uniform_scale': result['scale'],
            'maximum_geometry_error_metres': result['error'],
            'distinct_vertices': len(a), 'unity_geometry_sha256': geometry_sha256(unity_metres),
            'import_geometry_sha256': geometry_sha256(imported), 'coordinate_frame_proved': True}
