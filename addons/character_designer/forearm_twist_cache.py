"""Exact, bulk-read mesh fingerprints for read-only forearm validation caches.

Do not use depsgraph dirtiness as a geometry proof: direct RNA edits and our own
corrective-key writes do not provide a reliable distinction there. These byte
snapshots retain same-count topology and Basis edits without Python mesh walks.
"""
from array import array
from contextlib import contextmanager


_TOPOLOGY = {}
_MIRROR = {}
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


def mirror_pairs(obj, armature, source, target, calculate):
    if obj is None or obj.type != 'MESH' or armature is None or armature.type != 'ARMATURE':
        return calculate(obj, armature, source, target)
    mesh = obj.data
    keys = mesh.shape_keys
    coordinates = keys.reference_key.data if keys is not None else mesh.vertices
    # Correspondence reads only Basis, connectivity, object frames, chain Rest
    # endpoints/deform flags and saved records. Pose and weights are not inputs.
    signature = (topology_token(mesh), _bytes(coordinates, 'co', 3, 'f'),
                 tuple(v for row in obj.matrix_world for v in row),
                 tuple(v for row in armature.matrix_world for v in row),
                 tuple((bone.name, bone.use_deform, tuple(bone.head_local), tuple(bone.tail_local))
                       for bone in armature.data.bones), repr(source), repr(target))
    key = tuple(_identity(data) for data in (obj, mesh, armature, armature.data))
    cached = _MIRROR.get(key)
    if cached is not None and cached[0] == signature:
        return list(cached[1])
    result = calculate(obj, armature, source, target)
    _remember(_MIRROR, key, (signature, tuple(result)))
    return result
