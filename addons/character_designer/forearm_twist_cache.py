"""Exact, bulk-read mesh fingerprints for read-only forearm validation caches.

Do not use depsgraph dirtiness as a geometry proof: direct RNA edits and our own
corrective-key writes do not provide a reliable distinction there. These byte
snapshots retain same-count topology and Basis edits without Python mesh walks.
"""
from array import array
from contextlib import contextmanager
import math


_TOPOLOGY = {}
_MIRROR = {}
_SUPPORT = {}
_LIMIT = 32
_READ_SCOPE = None


@contextmanager
def read_scope():
    """Reuse one topology snapshot during a single read-only preparation pass."""
    global _READ_SCOPE
    previous = _READ_SCOPE
    _READ_SCOPE = {}
    try:
        yield
    finally:
        _READ_SCOPE = previous


def clear():
    _TOPOLOGY.clear()
    _MIRROR.clear()
    _SUPPORT.clear()


def _remember(cache, key, value):
    if key not in cache and len(cache) >= _LIMIT:
        cache.pop(next(iter(cache)))
    cache[key] = value


def _identity(data):
    # Keep the declared Blender 4.0 compatibility. Pointer reuse is harmless:
    # an entry is accepted only when its complete content fingerprint matches.
    return getattr(data, 'session_uid', None) or data.as_pointer()


def _bytes(items, name, width, kind):
    values = array(kind, [0]) * (len(items) * width)
    items.foreach_get(name, values)
    return values.tobytes()


def basis_bytes(obj):
    """Fresh Basis bytes, shared only inside one read-only runtime call.

    The native Key lifetime and the actual reference block identify the source.
    Neither the mirror proof nor the corrective-key commit writes this Basis.
    Outside the scope every call reads native data again, including direct edits.
    """
    mesh = obj.data
    keys = mesh.shape_keys
    basis = keys.reference_key if keys is not None else None
    coordinates = basis.data if basis is not None else mesh.vertices
    key = ('basis', _identity(mesh), _identity(keys) if keys is not None else None,
           basis.as_pointer() if basis is not None else None, len(coordinates))
    if _READ_SCOPE is not None and key in _READ_SCOPE:
        return _READ_SCOPE[key]
    result = _bytes(coordinates, 'co', 3, 'f')
    if _READ_SCOPE is not None:
        _READ_SCOPE[key] = result
    return result


def basis_coordinates(obj):
    """Return a fresh writable array; callers cannot alter the scope's proof."""
    result = array('f')
    result.frombytes(basis_bytes(obj))
    return result


def _mirror_chain_token(armature, source, target):
    """Snapshot every native bone field read by mirror_ring_pairs.

    Unrelated bone endpoints do not enter the symmetry calculation. Malformed
    chains go through the original calculator so its validation order remains.
    Full rig validation remains the caller's independent, fresh responsibility.
    """
    result = []
    bones = armature.data.bones
    for record in (source, target):
        if not isinstance(record, dict):
            return None
        chain = record.get('chain')
        if (not isinstance(chain, (list, tuple)) or len(chain) != 3
                or any(not isinstance(name, str) for name in chain)
                or len(set(chain)) != 3):
            return None
        for name in chain:
            bone = bones.get(name)
            if bone is None:
                return None
            head, tail = tuple(bone.head_local), tuple(bone.tail_local)
            if not all(math.isfinite(value) for value in head + tail):
                return None
            result.append((name, bool(bone.use_deform),
                           array('f', head).tobytes(), array('f', tail).tobytes()))
    return tuple(result)


def topology_token(mesh):
    key = _identity(mesh)
    if _READ_SCOPE is not None and key in _READ_SCOPE:
        return _READ_SCOPE[key]
    token = (len(mesh.vertices), _bytes(mesh.edges, 'vertices', 2, 'i'),
             _bytes(mesh.loops, 'vertex_index', 1, 'i'),
             _bytes(mesh.polygons, 'loop_start', 1, 'i'),
             _bytes(mesh.polygons, 'loop_total', 1, 'i'))
    if _READ_SCOPE is not None:
        _READ_SCOPE[key] = token
    return token


def topology_digest(mesh, calculate):
    token = topology_token(mesh)
    key = _identity(mesh)
    cached = _TOPOLOGY.get(key)
    if cached is not None and cached[0] == token:
        return cached[1]
    result = calculate(mesh)
    _remember(_TOPOLOGY, key, (token, result))
    return result


def support_topology_digest(mesh, vertices, calculate):
    """Cache a scoped proof only while the complete topology token is exact."""
    token = topology_token(mesh)
    key = _identity(mesh)
    cached = _SUPPORT.get(key)
    if cached is None or cached[0] != token:
        cached = (token, {})
        _remember(_SUPPORT, key, cached)
    indices = tuple(vertices)
    if any(type(index) is not int for index in indices):
        return calculate(mesh, indices)
    if indices not in cached[1]:
        _remember(cached[1], indices, calculate(mesh, indices))
    return cached[1][indices]


def mirror_pairs(obj, armature, source, target, calculate):
    if obj is None or obj.type != 'MESH' or armature is None or armature.type != 'ARMATURE':
        return calculate(obj, armature, source, target)
    mesh = obj.data
    chain = _mirror_chain_token(armature, source, target)
    if chain is None:
        return calculate(obj, armature, source, target)
    # Correspondence reads only Basis, connectivity, object frames, chain Rest
    # endpoints/deform flags and saved records. Pose and weights are not inputs.
    signature = (topology_token(mesh), basis_bytes(obj),
                 tuple(v for row in obj.matrix_world for v in row),
                 tuple(v for row in armature.matrix_world for v in row),
                 chain, repr(source), repr(target))
    key = tuple(_identity(data) for data in (obj, mesh, armature, armature.data))
    cached = _MIRROR.get(key)
    if cached is not None and cached[0] == signature:
        if cached[2] is not None:
            error_type, arguments = cached[2]
            raise error_type(*arguments)
        return list(cached[1])
    try:
        result = calculate(obj, armature, source, target)
    except ValueError as exc:
        # A deterministic failed geometry proof is also unchanged when every
        # exact input is unchanged. Retain only the type/arguments, never an
        # exception instance or traceback that could keep Blender data alive.
        # Real edits change the full signature and immediately retry the proof.
        _remember(_MIRROR, key, (signature, None, (type(exc), tuple(exc.args))))
        raise
    _remember(_MIRROR, key, (signature, tuple(result), None))
    return result
