"""Read-only finger definition shared by topology and rest-axis consumers.

Only scene metadata is captured. Surface paths are references, not inferred
bone centers. Current-key and Basis samples are stored separately and never
silently substituted for one another.
"""
import hashlib
import json
import math
import uuid

import bmesh
import bpy
from mathutils import Vector

EPS = 1e-7


class FrameReader:
    """Own read-only snapshots for one synchronous batch, never across events.

    Geometry consumers must not write to a borrowed BMesh. Exit frees every
    snapshot, including when one reference fails validation.
    """
    def __init__(self):
        self._meshes, self._topologies = {}, {}

    def __enter__(self): return self

    def __exit__(self, *_exc):
        for bm in self._meshes.values(): bm.free()
        self._meshes.clear()
        self._topologies.clear()

    def snapshot(self, obj, key='Basis', mesh=None):
        identity = (obj.as_pointer(), key, mesh.as_pointer() if mesh else 0)
        if identity not in self._meshes:
            self._meshes[identity] = _snapshot(obj, key, mesh)
        return self._meshes[identity]

    def topology(self, bm):
        identity = id(bm)
        if identity not in self._topologies: self._topologies[identity] = _topology(bm)
        return self._topologies[identity]


def state(context):
    from . import finger_bank
    active = finger_bank.active_state(context)
    if active is not None: return active
    return context.scene.character_designer_finger_definition


def _index(bm):
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
        seq.index_update()


def _key_name(obj):
    return obj.active_shape_key.name if obj.data.shape_keys else 'Basis'


def _snapshot(obj, key='Basis', mesh=None):
    """Caller owns this BMesh. No mode/key/selection changes on the real mesh."""
    data = mesh or obj.data
    if mesh is None and obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(data).copy()
        _index(bm)
        if data.shape_keys and key != _key_name(obj):
            layer = bm.verts.layers.shape.get(key)
            if layer is None:
                bm.free()
                raise ValueError(f'Shape Key "{key}" is unavailable. Capture the definition again.')
            for v in bm.verts: v.co = v[layer]
    else:
        bm = bmesh.new()
        bm.from_mesh(data)
        _index(bm)
        if data.shape_keys:
            block = data.shape_keys.key_blocks.get(key)
            if block is None:
                bm.free()
                raise ValueError(f'Shape Key "{key}" is unavailable. Capture the definition again.')
            for v, point in zip(bm.verts, block.data): v.co = point.co
    bm.normal_update()
    return bm


def _basis_name(obj):
    return obj.data.shape_keys.key_blocks[0].name if obj.data.shape_keys else 'Basis'


def _topology(bm):
    value = ([tuple(v.index for v in e.verts) for e in bm.edges],
             [tuple(v.index for v in f.verts) for f in bm.faces])
    return hashlib.sha256(repr(value).encode()).hexdigest()


def _input_vertices(bm, spec):
    if spec['kind'] == 'FACES': return {v for i in spec['ids'] for v in bm.faces[i].verts}
    if spec['kind'] == 'EDGES': return {v for i in spec['ids'] for v in bm.edges[i].verts}
    return {bm.verts[i] for i in spec['ids']}


def _selection(context, bm):
    mode = context.tool_settings.mesh_select_mode
    faces = [f.index for f in bm.faces if f.select and not f.hide]
    edges = [e.index for e in bm.edges if e.select and not e.hide]
    verts = [v.index for v in bm.verts if v.select and not v.hide]
    if faces and (mode[2] or not mode[1]): return {'kind': 'FACES', 'ids': faces}
    if edges: return {'kind': 'EDGES', 'ids': edges}
    if verts: return {'kind': 'VERTS', 'ids': verts}
    raise ValueError('Select a lengthwise surface/edge path, or mark Start and End separately.')


def _components(bm, spec):
    remaining = _input_vertices(bm, spec)
    edges = {bm.edges[i] for i in spec['ids']} if spec['kind'] == 'EDGES' else set(bm.edges)
    groups = []
    while remaining:
        todo, group = [min(remaining, key=lambda v: v.index)], set()
        while todo:
            v = todo.pop()
            if v not in remaining: continue
            remaining.remove(v)
            group.add(v)
            todo.extend(e.other_vert(v) for e in v.link_edges if e in edges and e.other_vert(v) in remaining)
        groups.append(group)
    return groups


def _mean(vertices):
    vertices = list(vertices)
    return sum((v.co for v in vertices), Vector())/len(vertices)


def _face_path(faces):
    chosen = set(faces)
    adjacent = {f: [(e, g) for e in f.edges for g in e.link_faces if g in chosen and g != f] for f in faces}
    ends = [f for f in faces if len(adjacent[f]) == 1]
    if len(ends) != 2 or any(len(x) > 2 for x in adjacent.values()): return None
    current, previous = min(ends, key=lambda f: f.index), None
    incoming = adjacent[current][0][0]
    def end_point(face, edge):
        other = [e for e in face.edges if not set(e.verts) & set(edge.verts)]
        if not other: return None
        return _mean(max(other, key=lambda e: (_mean(e.verts)-_mean(edge.verts)).length).verts)
    first = end_point(current, incoming)
    if first is None: return None
    points, visited = [first], set()
    while current is not None:
        if current in visited: return None
        visited.add(current)
        choices = [(e, f) for e, f in adjacent[current] if f != previous]
        if not choices:
            last = end_point(current, incoming)
            if last is None: return None
            points.append(last)
            break
        edge, other = choices[0]
        points.append(_mean(edge.verts))
        previous, current, incoming = current, other, edge
    return points if len(visited) == len(faces) else None


def _principal_span(vertices):
    # Blender bundles numpy. It is only used for a tiny 3x3 covariance solve.
    import numpy as np
    points = np.array([tuple(v.co) for v in vertices], dtype=float)
    center = points.mean(axis=0)
    values, vectors = np.linalg.eigh((points-center).T @ (points-center))
    if values[-1] < 1e-14 or values[-1] < values[-2]*1.4:
        raise ValueError('This patch has no clear length direction. Mark it as Start, then mark End.')
    tangent = Vector(vectors[:, -1])
    midpoint = Vector(center)
    distances = [(v.co-midpoint).dot(tangent) for v in vertices]
    return [midpoint+tangent*min(distances), midpoint+tangent*max(distances)]


def _path(bm, spec):
    parts = _components(bm, spec)
    if len(parts) == 2:
        return [_mean(p) for p in parts], 'Two markers'
    if len(parts) != 1: raise ValueError('Use one connected path, or two endpoint patches.')
    if spec['kind'] == 'EDGES':
        edges = {bm.edges[i] for i in spec['ids']}
        vertices = parts[0]
        adjacent = {v: [e.other_vert(v) for e in v.link_edges if e in edges] for v in vertices}
        ends = [v for v in vertices if len(adjacent[v]) == 1]
        if len(ends) != 2 or any(len(x) > 2 for x in adjacent.values()):
            raise ValueError('Use one open edge path. A cross-section can instead be marked as Start or End.')
        current, previous, points = min(ends, key=lambda v: v.index), None, []
        while current is not None:
            points.append(current.co.copy())
            choices = [v for v in adjacent[current] if v != previous]
            previous, current = current, choices[0] if choices else None
        return points, 'Edge path'
    if spec['kind'] == 'FACES':
        points = _face_path([bm.faces[i] for i in spec['ids']])
        if points is not None: return points, 'Surface path'
    return _principal_span(parts[0]), 'Surface span'


def _normal(bm, spec):
    if spec['kind'] != 'FACES': return None
    faces = [bm.faces[i] for i in spec['ids']]
    normal = sum((f.normal*f.calc_area() for f in faces), Vector())
    if normal.length < EPS or any(f.normal.dot(normal.normalized()) < .25 for f in faces): return None
    return list(normal.normalized())


def _sample(bm, spec, path=True):
    vertices = sorted(_input_vertices(bm, spec), key=lambda v: v.index)
    points, label = _path(bm, spec) if path else ([_mean(vertices)], 'Marker')
    return {'path': [list(p) for p in points], 'label': label, 'normal': _normal(bm, spec),
            'coordinates': [[v.index, list(v.co)] for v in vertices]}


def _mesh_capture(context, path=True):
    obj = context.edit_object
    if context.mode != 'EDIT_MESH' or obj is None or len(context.objects_in_mode_unique_data) != 1:
        raise ValueError('Enter Edit Mode on one mesh to capture its selected surface or edges.')
    key, basis = _key_name(obj), _basis_name(obj)
    bm, base = _snapshot(obj, key), _snapshot(obj, basis)
    try:
        spec = _selection(context, bm)
        current, rest = _sample(bm, spec, path), _sample(base, spec, path)
        # Face/edge traversal is already ordered by topology, even if a key
        # turns the finger through 180 degrees. Only PCA spans need sign repair;
        # correlate the same vertex identities instead of world directions.
        if current['label'] == rest['label'] == 'Surface span':
            c0, c1 = map(Vector, current['path'])
            r0, r1 = map(Vector, rest['path'])
            covariance = sum((Vector(c)-(c0+c1)*.5).dot(c1-c0)*(Vector(r)-(r0+r1)*.5).dot(r1-r0)
                             for (_, c), (_, r) in zip(current['coordinates'], rest['coordinates']))
            if covariance < 0: rest['path'].reverse()
        direction = 'Review arrow'
        if path:
            from . import finger_range
            # A strongly displaced Shape Key may no longer look like a capped
            # sleeve. Corresponding Basis topology can still orient its path.
            for probe, sample in ((base, rest), (bm, current)):
                try:
                    faces = [probe.faces[i] for i in spec['ids']] if spec['kind'] == 'FACES' else list({f for v in _input_vertices(probe, spec) for f in v.link_faces})
                    discovered = finger_range.discover(probe, faces, finger_range.quad_band, obj.matrix_world)
                    forward = Vector(discovered['tip'])-Vector(discovered['root'])
                    if (Vector(sample['path'][-1])-Vector(sample['path'][0])).dot(forward) < 0:
                        current['path'].reverse()
                        rest['path'].reverse()
                    direction = 'Detected tip'
                    break
                except ValueError:
                    pass  # Definition does not depend on a capped regular sleeve.
        return obj, {'kind': 'MESH', 'input': spec, 'topology': _topology(bm), 'key': key,
                     'basis_key': basis, 'current': current, 'basis': rest, 'direction': direction}
    finally:
        bm.free()
        base.free()


def _commit(context, obj, record):
    if len(record['basis']['path']) > 1:
        if sum((Vector(b)-Vector(a)).length for a, b in zip(record['basis']['path'], record['basis']['path'][1:])) < EPS:
            raise ValueError('Start and End must be different points.')
    s = state(context)
    fields = ('source', 'record', 'use_basis', 'revision', 'bend_source', 'bend_record',
              'flip_bend', 'pending_source', 'pending', 'confirmed')
    previous = {name: getattr(s, name) for name in fields}
    try:
        s.source, s.record = obj, json.dumps(record)
        s.use_basis = record['key'] == record['basis_key']
        s.revision = uuid.uuid4().hex
        s.confirmed = False
        s.bend_source, s.bend_record = None, ''
        s.flip_bend = False
        s.pending_source, s.pending = None, ''
        return frame(context)
    except Exception:
        for name, value in previous.items(): setattr(s, name, value)
        raise


def capture(context):
    obj = context.object
    if obj and obj.type == 'ARMATURE' and obj.mode in {'EDIT', 'POSE'}:
        from .finger_bones import _bone_collection, _head, _tail, _parent_depth
        selected = sorted((b for b in _bone_collection(obj) if b.select), key=_parent_depth) if obj.mode == 'EDIT' else sorted(
            (b.bone for b in (context.selected_pose_bones or []) if b.id_data == obj), key=_parent_depth)
        if not selected or any(b.parent != a for a, b in zip(selected, selected[1:])):
            raise ValueError('Select one continuous bone chain on one side for the reference.')
        points = [list(_head(selected[0]))]
        for bone in selected:
            if (Vector(points[-1])-_head(bone)).length > EPS: points.append(list(_head(bone)))
            points.append(list(_tail(bone)))
        sample = {'path': points, 'label': 'Rest bone chain', 'normal': None}
        record = {'kind': 'BONES', 'key': 'Basis', 'basis_key': 'Basis', 'basis': sample,
                  'current': sample, 'direction': 'Bone Head to Tail',
                  'bones': [{'name': b.name, 'head': list(_head(b)), 'tail': list(_tail(b)),
                             'parent': b.parent.name if b.parent else ''} for b in selected]}
        return _commit(context, obj, record)
    obj, record = _mesh_capture(context)
    return _commit(context, obj, record)


def mark(context, end=False):
    obj, record = _mesh_capture(context, path=False)
    s = state(context)
    if not end:
        s.pending_source, s.pending = obj, json.dumps(record)
        return
    if not s.pending or s.pending_source != obj:
        raise ValueError('Mark Start on this mesh first.')
    start = json.loads(s.pending)
    if start['key'] != record['key'] or start['topology'] != record['topology']:
        raise ValueError('Use the same mesh topology and Shape Key for Start and End.')
    _validate_mesh(obj, start, s.use_basis if start['key'] == start['basis_key'] else False)
    record['start_input'] = start['input']
    for variant in ('basis', 'current'):
        record[variant]['path'] = start[variant]['path']+record[variant]['path']
        record[variant]['coordinates'] = start[variant]['coordinates']+record[variant]['coordinates']
        record[variant]['normal'] = None  # End cross-faces do not identify a top side.
        record[variant]['label'] = 'Start / End markers'
    record['direction'] = 'Explicit Start to End'
    return _commit(context, obj, record)


def _validate_mesh(obj, record, basis, mesh=None, *, reader=None):
    if reader is None:
        with FrameReader() as reader:
            return _validate_mesh(obj, record, basis, mesh, reader=reader)
    variant = 'basis' if basis else 'current'
    key = record['basis_key'] if basis else record['key']
    bm = reader.snapshot(obj, key, mesh)
    topology = reader.topology(bm)
    if mesh is None and record.get('local_evidence') and topology != record['topology']:
        from . import finger_bank
        return finger_bank.validate_local(obj, record, basis, bm, reader=reader)
    if topology != record['topology'] or any(i >= len(bm.verts) or (bm.verts[i].co-Vector(co)).length > 1e-6
                                             for i, co in record[variant]['coordinates']):
        raise ValueError('The reference geometry changed. Capture the finger definition again.')
    return record


def frame(context, *, require_basis=False, require_bend=False, require_confirmed=False, purpose='INTERNAL', reader=None):
    if reader is None:
        with FrameReader() as reader:
            return frame(context, require_basis=require_basis, require_bend=require_bend,
                         require_confirmed=require_confirmed, purpose=purpose, reader=reader)
    s = state(context)
    if not s.source or not s.record: raise ValueError('Capture a finger definition first.')
    if require_confirmed and not s.confirmed: raise ValueError('Review the markers and Confirm Definition first.')
    record = json.loads(s.record)
    if require_basis and not s.use_basis:
        raise ValueError('This is a current-key preview. Use Basis Reference and confirm before changing bone axes.')
    if record['kind'] == 'MESH':
        record = _validate_mesh(s.source, record, s.use_basis, reader=reader)
    else:
        from .finger_bones import _bone_collection, _head, _tail
        collection = _bone_collection(s.source)
        for item in record['bones']:
            bone = collection.get(item['name'])
            if bone is None or (_head(bone)-Vector(item['head'])).length > 1e-6 or (_tail(bone)-Vector(item['tail'])).length > 1e-6 or (bone.parent.name if bone.parent else '') != item['parent']:
                raise ValueError('The reference bone chain changed. Capture its definition again.')
    sample = record['basis' if s.use_basis else 'current']
    if record.get('body', {}).get('root_extension'):
        # The real connected shell is a live proof domain, not a huge global
        # reference fingerprint. Unrelated valid topology edits may rebind;
        # newly opened/intersecting root geometry must never keep a fake proof.
        from . import finger_bank, finger_internal
        base = reader.snapshot(s.source, record['basis_key'])
        resolved = finger_bank.remap_record(record, base) if reader.topology(base) != record['topology'] else record
        finger_internal.verify(base, resolved['body'], resolved['internal'])
    if record.get('internal') and purpose == 'INTERNAL' and not require_basis and _key_name(s.source) != record['basis_key']:
        # A single rest definition, never one configuration per Shape Key. Do
        # not display it as interior if the currently edited deformation moved
        # the finger away from that line.
        from . import finger_bank, finger_internal
        base = reader.snapshot(s.source, record['basis_key'])
        current = reader.snapshot(s.source, _key_name(s.source))
        try:
            display_record = finger_bank.remap_record(record, base) if reader.topology(base) != record['topology'] else record
            finger_internal.verify(current, display_record['body'], display_record['internal'])
        except ValueError as exc:
            raise ValueError('The visible finger deformation does not contain this rest axis safely; the saved reference and Shape Keys are unchanged.') from exc
    world = s.source.matrix_world
    if abs(world.to_3x3().determinant()) < EPS: raise ValueError('The reference object has zero scale.')
    internal = record.get('internal')
    path = [world @ Vector(p) for p in (internal['path'] if internal and purpose == 'INTERNAL' else sample['path'])]
    length = sum((b-a).length for a, b in zip(path, path[1:]))
    if length < EPS or (path[-1]-path[0]).length < EPS: raise ValueError('The reference span is collapsed.')
    tangent = (path[-1]-path[0]).normalized()
    remaining, midpoint = length*.5, path[0]
    for a, b in zip(path, path[1:]):
        segment = (b-a).length
        if segment > EPS and remaining <= segment:
            midpoint = a.lerp(b, remaining/segment)
            break
        remaining -= segment
    normal, bend_error = sample.get('normal'), ''
    normal_world = world.to_3x3().inverted().transposed() @ Vector(normal) if normal else None
    if s.bend_record:
        try:
            if not s.bend_source: raise ValueError('The top-reference mesh is missing.')
            top = json.loads(s.bend_record)
            _validate_mesh(s.bend_source, top, s.use_basis, reader=reader)
            normal = top['basis' if s.use_basis else 'current']['normal']
            normal_world = s.bend_source.matrix_world.to_3x3().inverted().transposed() @ Vector(normal)
        except ValueError as exc:
            normal_world, bend_error = None, str(exc)
    bend = None
    if normal_world is not None:
        projected = normal_world-tangent*normal_world.dot(tangent)
        if projected.length > EPS: bend = projected.normalized()*(1 if s.flip_bend else -1)
    if require_bend and bend is None:
        raise ValueError(bend_error or ('Bone Roll needs a reliable bend side. Capture a longitudinal top-surface strip; the internal axis itself is already usable.' if internal else 'Bend direction is undefined. Select a top surface and use Set Top Surface.'))
    return {'path': path, 'root': path[0], 'tip': path[-1], 'midpoint': midpoint, 'direction': tangent, 'bend': bend,
            'length': length, 'basis': s.use_basis, 'key': record['basis_key'] if s.use_basis else record['key'],
            'label': sample['label'], 'direction_label': record['direction'], 'bend_error': bend_error,
            'internal': bool(internal), 'purpose': purpose}


def _saved_vector(value):
    """Validate only serialized display coordinates, never scene geometry."""
    try:
        result = Vector(value)
        if len(result) != 3 or not all(math.isfinite(v) for v in result): raise ValueError
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('The saved reference has invalid coordinates; capture it again.') from exc


def saved_frame(context):
    """Reconstruct frozen display guides from saved metadata and transforms.

    This is deliberately not a proof that the current geometry or bones still
    match the reference. Existing internal guides always use their saved Basis
    path until explicit recapture/recheck. All geometry and bone writes must
    continue to use frame() and their current-mesh validation instead.
    """
    s = state(context)
    if not s.source or not s.record: raise ValueError('Capture a finger definition first.')
    try:
        record = json.loads(s.record)
        if not isinstance(record, dict): raise ValueError
        internal = record.get('internal')
        if internal is not None and not isinstance(internal, dict): raise ValueError
        basis = bool(internal) or bool(s.use_basis)
        sample = record['basis' if basis else 'current']
        if not isinstance(sample, dict): raise ValueError
        saved_path = internal['path'] if internal else sample['path']
        path = [_saved_vector(point) for point in saved_path]
        if len(path) < 2: raise ValueError
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('The saved reference is incomplete; capture it again.') from exc
    world = s.source.matrix_world
    linear = world.to_3x3()
    if (not all(math.isfinite(v) for row in world for v in row) or
            abs(linear.determinant()) < EPS):
        raise ValueError('The reference object has an invalid transform.')
    path = [world @ point for point in path]
    length = sum((b-a).length for a, b in zip(path, path[1:]))
    if length < EPS or (path[-1]-path[0]).length < EPS:
        raise ValueError('The saved reference span is collapsed.')
    tangent = (path[-1]-path[0]).normalized()
    remaining, midpoint = length*.5, path[0]
    for a, b in zip(path, path[1:]):
        segment = (b-a).length
        if segment > EPS and remaining <= segment:
            midpoint = a.lerp(b, remaining/segment)
            break
        remaining -= segment

    # Separate top evidence retains precedence over a base sample. If that
    # saved evidence is unavailable, omit its bend instead of guessing from
    # live faces or reporting a geometry-staleness error during drawing.
    normal, normal_matrix, bend_error = sample.get('normal'), linear, ''
    if s.bend_record:
        try:
            if not s.bend_source: raise ValueError
            top = json.loads(s.bend_record)
            normal = top['basis' if basis else 'current']['normal']
            normal_matrix = s.bend_source.matrix_world.to_3x3()
        except (ValueError, TypeError, KeyError):
            normal, bend_error = None, 'Saved bend reference is unavailable.'
    bend = None
    if normal is not None:
        try:
            normal = _saved_vector(normal)
            if normal.length < EPS or abs(normal_matrix.determinant()) < EPS: raise ValueError
            normal_world = normal_matrix.inverted().transposed() @ normal
            if not all(math.isfinite(v) for v in normal_world): raise ValueError
            projected = normal_world-tangent*normal_world.dot(tangent)
            if projected.length <= max(EPS, normal_world.length*1e-4): raise ValueError
            bend = projected.normalized()*(1 if s.flip_bend else -1)
        except ValueError:
            bend_error = 'Saved bend direction is unavailable.'
    return {'path': path, 'root': path[0], 'tip': path[-1], 'midpoint': midpoint,
            'direction': tangent, 'bend': bend, 'length': length, 'basis': basis,
            'key': record.get('basis_key' if basis else 'key', 'Basis'),
            'label': sample.get('label', 'Saved reference'),
            'direction_label': record.get('direction', 'Saved direction'),
            'bend_error': bend_error, 'internal': bool(internal), 'purpose': 'INTERNAL',
            'display_only': True}


def confirm(context):
    frame(context)
    state(context).confirmed = True


def swap(context):
    s = state(context)
    frame(context)
    record = json.loads(s.record)
    for variant in ('basis', 'current'): record[variant]['path'].reverse()
    record['direction'] = 'User reversed'
    s.record, s.revision, s.confirmed = json.dumps(record), uuid.uuid4().hex, False


def basis_reference(context):
    s = state(context)
    if not s.record or not s.source: raise ValueError('Capture a finger definition first.')
    record = json.loads(s.record)
    if record['kind'] == 'MESH': _validate_mesh(s.source, record, True)
    s.use_basis, s.confirmed = True, False
    s.revision = uuid.uuid4().hex
    return frame(context)


def set_top(context):
    s = state(context)
    if not s.record: raise ValueError('Define the finger span first.')
    obj, record = _mesh_capture(context, path=False)
    if record['basis' if s.use_basis else 'current']['normal'] is None:
        raise ValueError('Select one top face or a consistently oriented top patch, not an edge or end cap.')
    candidate = Vector(record['basis' if s.use_basis else 'current']['normal'])
    if abs(obj.matrix_world.to_3x3().determinant()) < EPS: raise ValueError('The top-reference object has zero scale.')
    candidate = obj.matrix_world.to_3x3().inverted().transposed() @ candidate
    tangent = frame(context)['direction']
    if (candidate-tangent*candidate.dot(tangent)).length < candidate.length*1e-4:
        raise ValueError('This is an end-facing surface. Select a top surface along the finger.')
    s.bend_source, s.bend_record, s.flip_bend, s.confirmed = obj, json.dumps(record), False, False


def seed_faces(context, bm):
    s = state(context)
    record = json.loads(s.record)
    if record['kind'] != 'MESH' or s.source != context.edit_object:
        faces = [f for f in bm.faces if f.select and not f.hide]
        if not faces: raise ValueError('Select nearby finger faces on the target mesh for the topology check.')
        return faces
    spec = record.get('start_input', record['input'])
    vertices = _input_vertices(bm, spec)
    faces = [bm.faces[i] for i in spec['ids']] if spec['kind'] == 'FACES' else list({f for v in vertices for f in v.link_faces if not f.hide})
    # Two disconnected endpoint patches are a definition, not a sleeve selection.
    chosen, todo, connected = set(faces), [faces[0]] if faces else [], set()
    while todo:
        f = todo.pop()
        if f in connected: continue
        connected.add(f)
        todo.extend(g for e in f.edges for g in e.link_faces if g in chosen and g not in connected)
    return list(connected)


def project_distance(point, path):
    best, offset = None, 0.
    for i, (a, b) in enumerate(zip(path, path[1:])):
        delta = b-a
        if delta.length < EPS: continue
        t = (point-a).dot(delta)/delta.length_squared
        if i != 0: t = max(0., t)
        if i != len(path)-2: t = min(1., t)
        candidate = ((point-a-delta*t).length_squared, offset+t*delta.length)
        if best is None or candidate[0] < best[0]: best = candidate
        offset += delta.length
    if best is None: raise ValueError('The definition has no usable length.')
    return best[1]


def _clear_state(s):
    s.source = s.bend_source = s.pending_source = None
    s.record = s.bend_record = s.pending = s.revision = s.status = ''
    s.use_basis, s.flip_bend = True, False
    s.confirmed = False


def clear(context):
    s = state(context)
    from . import finger_bank
    obj = finger_bank.active_object(context)
    if obj:
        slot = next((slot for slot in obj.character_designer_finger_bank.slots
                     if slot.guide.as_pointer() == s.as_pointer()), None)
        if slot:
            digit, side = slot.name.split('.')
            finger_bank.clear(context, digit, side=side)
            return
    _clear_state(s)
    from . import finger_definition_ui, finger_bone_tools
    finger_definition_ui.redraw()
    finger_bone_tools.invalidate(context)


def pending_frame(context):
    s = state(context)
    if not s.pending_source or not s.pending: return None
    record = json.loads(s.pending)
    _validate_mesh(s.pending_source, record, False)
    world = s.pending_source.matrix_world
    return {'point': world @ Vector(record['current']['path'][0]),
            'size': max(s.pending_source.dimensions.length*.015, .001)}


def saved_pending_frame(context):
    """Frozen legacy Start marker; completing End retains its own validation."""
    s = state(context)
    if not s.pending_source or not s.pending: return None
    sample = json.loads(s.pending)['current']
    point = _saved_vector(sample['path'][0])
    radius = max(((_saved_vector(co)-point).length for _, co in sample.get('coordinates', ())), default=0.)
    world = s.pending_source.matrix_world
    return {'point': world @ point, 'size': max(radius*.08, .001)}
