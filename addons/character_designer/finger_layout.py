"""Surface-following two-joint layout; immutable source, staged mesh updates.

An arbitrary connected root surface defines length. Only regular circumferential
quad bands are edited; suitable nearby rings can slide to virtual targets. Each
update starts from the captured mesh. Bones/group definitions are untouched.
"""
import bisect
import json

import bmesh
import bpy
from mathutils import Vector

from . import finger_joint, finger_flex, finger_range, finger_ring_slide
from .mesh_mirror import _fingerprint, _value, INTERNAL_ATTRIBUTES

EPS = 1e-6
SOURCE_TAG = 'character_designer_finger_layout_source'
RESULT_TAG = 'character_designer_finger_layout_result'


def fingerprint(obj):
    """Read all attribute buffers even while the real mesh is in Edit Mode."""
    if obj.mode != 'EDIT': return _fingerprint(obj)
    obj.update_from_editmode()
    snapshot = obj.copy()
    mesh = obj.data.copy()
    try:
        snapshot.data = mesh
        return _fingerprint(snapshot)
    finally:
        bpy.data.objects.remove(snapshot)
        bpy.data.meshes.remove(mesh)


def state(context):
    return context.scene.character_designer_finger_layout


def _edit(context):
    obj = context.edit_object
    if context.mode != 'EDIT_MESH' or obj is None:
        raise ValueError('Enter Mesh Edit Mode and select a root surface on one finger.')
    if len(context.objects_in_mode_unique_data) != 1:
        raise ValueError('Edit one mesh at a time for finger layout.')
    obj.update_from_editmode()
    _preflight(obj)
    return obj


def _preflight(obj):
    mesh = obj.data
    if mesh.users != 1 or not obj.is_editable or not mesh.is_editable:
        raise ValueError('Finger layout requires an editable, single-user mesh.')
    if mesh.animation_data or len(mesh.skin_vertices):
        raise ValueError('Animated mesh data or Skin data cannot be safely updated by this tool.')
    if mesh.shape_keys:
        if obj.active_shape_key_index != 0:
            raise ValueError('Select the Basis shape key before changing finger topology.')
        if mesh.shape_keys.animation_data or not mesh.shape_keys.use_relative:
            raise ValueError('Animated/driven or absolute Shape Keys are not yet supported; no data was changed.')
        if any(getattr(k, 'lock_shape', False) for k in mesh.shape_keys.key_blocks):
            raise ValueError('Unlock the Shape Keys explicitly before changing finger topology.')
    for modifier in obj.modifiers:
        if modifier.type == 'MULTIRES' and modifier.total_levels or modifier.type in {
            'HOOK', 'MESH_DEFORM', 'SURFACE_DEFORM', 'CLOTH', 'SOFT_BODY', 'PARTICLE_SYSTEM'}:
            raise ValueError(f'{modifier.name}: index-based or baked data cannot be safely updated.')
    if any(child.parent == obj and child.parent_type in {'VERTEX', 'VERTEX_3'} for child in bpy.data.objects):
        raise ValueError('A child is parented to mesh vertices; its indices cannot be remapped safely.')
    supported = {'FLOAT', 'INT', 'BOOLEAN', 'FLOAT_VECTOR', 'FLOAT_COLOR', 'BYTE_COLOR', 'FLOAT2'}
    for a in mesh.attributes:
        if a.name in INTERNAL_ATTRIBUTES or a.name == 'custom_normal': continue
        if a.domain not in {'POINT', 'EDGE', 'FACE', 'CORNER'} or a.data_type not in supported:
            raise ValueError(f'Cannot safely preserve attribute "{a.name}" ({a.data_type}).')
        if a.data_type == 'FLOAT2' and (a.domain != 'CORNER' or a.name not in mesh.uv_layers):
            raise ValueError(f'Only UV maps are supported as FLOAT2 attributes: "{a.name}".')


def _ordered_strip(faces):
    finger_flex._strip_geometry(faces)
    chosen = set(faces)
    neighbors = {f: [(e, other) for e in f.edges for other in e.link_faces
                     if other in chosen and other != f] for f in faces}
    current = min((f for f in faces if len(neighbors[f]) == 1), key=lambda f: f.index)
    shared = neighbors[current][0][0]
    entry = next(e for e in current.edges if not set(e.verts) & set(shared.verts))
    result, previous = [], None
    while current is not None:
        result.append((current, entry))
        choices = [(e, f) for e, f in neighbors[current] if f != previous]
        previous = current
        if choices: entry, current = choices[0]
        else: current = None
    return result


def _quad_band(seed, entry):
    """Walk around the finger across longitudinal edges, not along it."""
    todo, seen, mapping, cross_edges = [(seed, entry)], {}, {}, set()
    while todo:
        face, cross = todo.pop()
        if face in seen:
            if seen[face] != cross:
                raise ValueError('The strip twists or branches; choose a regular quad finger section.')
            continue
        if face.hide or len(face.verts) != 4:
            raise ValueError('The ring meets hidden or non-quad faces. Keep the palm and fingertip cap outside the strip.')
        seen[face] = cross
        cross_edges.add(cross)
        loops = list(face.loops)
        k = next(i for i, loop in enumerate(loops) if loop.edge == cross)
        u, v, w, x = (loops[(k+i) % 4].vert for i in range(4))
        for root, tip in ((u, x), (v, w)):
            if root in mapping and mapping[root] != tip:
                raise ValueError('The quad band does not have a one-to-one ring correspondence.')
            mapping[root] = tip
            connector = next(e for e in face.edges if root in e.verts and tip in e.verts)
            if len(connector.link_faces) != 2:
                raise ValueError('The finger cross-section is open or non-manifold; a complete ring is required.')
            neighbor = next(f for f in connector.link_faces if f != face)
            next_cross = [e for e in neighbor.edges if root in e.verts and e != connector]
            if len(next_cross) != 1:
                raise ValueError('The finger band branches.')
            todo.append((neighbor, next_cross[0]))
    ordered = finger_joint._ordered_loop(cross_edges)
    if len(seen) != len(ordered) or len(set(mapping.values())) != len(ordered):
        raise ValueError('The finger band does not close cleanly.')
    if set(mapping) & set(mapping.values()):
        raise ValueError('The finger band folds back into itself.')
    return ordered, mapping, set(seen)


def _capture_rings(context, obj):
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
        seq.index_update()
    faces = [f for f in bm.faces if f.select and not f.hide]
    # Reuse the previously captured top strip if the artist selected bones/loops
    # in between; never fall back from a non-empty but invalid face selection.
    if not faces:
        finger_flex.guide_frame(context)
        guide = finger_flex.state(context)
        if guide.mesh != obj: raise ValueError('The top-surface guide belongs to another mesh.')
        faces = [bm.faces[f['index']] for f in json.loads(guide.guide)['faces']]
    if len(faces) < 2:
        raise ValueError('Select a single top row of at least two quads from the root boundary toward the tip.')
    strip = _ordered_strip(faces)
    rings, used = [], set()
    for face, entry in strip:
        root, mapping, band = _quad_band(face, entry)
        if band & used:
            raise ValueError('The top strip visits the same cross-section twice.')
        used.update(band)
        if rings:
            if set(root) != set(rings[-1]):
                raise ValueError('The strip reaches webbing or changes ring size. Shorten it to the regular finger body; leave the palm outside.')
            root = rings[-1]
        else: rings.append(root)
        rings.append([mapping[v] for v in root])
    if len({v for row in rings for v in row}) != sum(map(len, rings)):
        raise ValueError('The finger region wraps around or intersects itself.')
    for row in rings[1:-1]:
        if any(len(v.link_edges) != 4 or set(v.link_faces)-used for v in row):
            raise ValueError('An interior ring has branches or attachments. Keep them outside the captured strip.')
    try:
        _, forward, _, _, _ = finger_flex.guide_frame(context)
        if finger_flex.state(context).mesh == obj:
            direction = sum((b.co-a.co for a, b in zip(rings[0], rings[-1])), Vector())
            if (obj.matrix_world.to_3x3() @ direction).dot(forward) < 0: rings.reverse()
    except (ValueError, ReferenceError): pass
    return [[v.index for v in row] for row in rings]


def capture(context, *, legacy=False):
    obj = _edit(context)
    if legacy:  # Regression/migration path; the UI always captures schema 2.
        rings = _capture_rings(context, obj)
        centers = [sum((obj.matrix_world @ obj.data.vertices[i].co for i in row), Vector())/len(row) for row in rings]
        lengths = [(b-a).length for a, b in zip(centers, centers[1:])]
        total = sum(lengths)
        if total < 1e-8 or min(lengths) < total*1e-5:
            raise ValueError('The finger rings are collapsed or too close together.')
        distances = [0.]
        for length in lengths: distances.append(distances[-1]+length/total)
        data = {'rings': rings, 'positions': distances, 'schema': 1}
    else:
        bm = bmesh.from_edit_mesh(obj.data)
        for seq in (bm.verts, bm.edges, bm.faces):
            seq.ensure_lookup_table()
            seq.index_update()
        data = finger_range.discover(bm, [f for f in bm.faces if f.select and not f.hide], _quad_band, obj.matrix_world)
    return _store_capture(context, obj, data)


def _store_capture(context, obj, data):
    settings = state(context)
    # Validate the entire proposed capture before replacing a previous recipe.
    source = obj.data.copy()
    try:
        if data['schema'] >= 2: data['protected_rows'] = sorted(finger_ring_slide.protected_rows(source, data['rings']))
        proposed = _build_layout(settings, data, source, obj, reverse=False)
    except Exception:
        bpy.data.meshes.remove(source)
        raise
    source.name = '.CD Finger Layout Source'
    source[SOURCE_TAG] = True
    old_source = settings.source
    settings.mesh, settings.source = obj, source
    settings.record = json.dumps(data)
    settings.signature = fingerprint(obj)
    settings.reverse = False
    settings.applied = False
    if old_source and old_source.users == 0 and old_source.get(SOURCE_TAG):
        bpy.data.meshes.remove(old_source)
    return proposed


def capture_definition(context):
    from . import finger_definition as definition
    data = definition.frame(context, require_basis=True, require_confirmed=True, purpose='TOPOLOGY')
    obj = _edit(context)
    s, d = state(context), definition.state(context)
    if s.mesh == obj and s.record and json.loads(s.record).get('definition_id') == d.revision:
        if fingerprint(obj) != s.signature: raise ValueError('The prepared mesh changed. Capture a fresh definition.')
        return build_layout(context)
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
        seq.index_update()
    from . import finger_bank
    candidate = finger_bank.current_candidate(context)
    selected = definition.seed_faces(context, bm)
    record = ({'schema': 2, 'rings': candidate['rings'], 'selection': [f.index for f in selected]}
              if candidate else finger_range.discover(bm, selected, _quad_band, obj.matrix_world))
    rows = record['rings']
    centers = [sum((obj.matrix_world @ bm.verts[i].co for i in row), Vector())/len(row) for row in rows]
    positions = [definition.project_distance(p, data['path'])/data['length'] for p in centers]
    if positions[-1] < positions[0]: rows.reverse(); positions.reverse()
    if any(b-a < 1e-5 for a, b in zip(positions, positions[1:])):
        raise ValueError('The reference folds across the editable body. Use clearer Start / End markers.')
    keep = [(r, t) for r, t in zip(rows, positions) if -EPS <= t <= 1+EPS]
    if len(keep) < 4:
        raise ValueError('Definition is valid, but fewer than three complete quad bands lie inside it. No geometry was changed.')
    record.update(rings=[r for r, _ in keep], positions=[t for _, t in keep],
                  root=list(obj.matrix_world.inverted() @ data['root']),
                  tip=list(obj.matrix_world.inverted() @ data['tip']), length=data['length'],
                  definition_id=d.revision)
    return _store_capture(context, obj, record)


def _source(context):
    settings = state(context)
    if not settings.mesh or not settings.source or not settings.record:
        raise ValueError('Capture a finger root surface first.')
    if settings.mesh != context.edit_object:
        raise ValueError('Return to the captured mesh in Edit Mode, or capture a new finger.')
    record = json.loads(settings.record)
    if record.get('definition_id'):
        from . import finger_definition as definition
        guide = definition.state(context)
        if not guide.record or guide.revision != record['definition_id']:
            raise ValueError('The finger definition changed. Prepare Rings again before updating.')
        definition.frame(context, require_basis=True, require_confirmed=True)
    return settings, record


def _sample(source, rows, ts, t, *, snap=False):
    for i, value in enumerate(ts):
        if abs(t-value) < EPS:
            return [source.vertices[v].co.copy() for v in rows[i]], i, 0.
    i = max(0, min(bisect.bisect_right(ts, t)-1, len(ts)-2))
    f = (t-ts[i])/(ts[i+1]-ts[i])
    # Reuse a practically coincident original ring rather than making a sliver.
    if snap and min(f, 1-f) < .002:
        row = i if f < .5 else i+1
        return [source.vertices[v].co.copy() for v in rows[row]], row, 0.
    return [source.vertices[a].co.lerp(source.vertices[b].co, f) for a, b in zip(rows[i], rows[i+1])], i, f


def build_layout(context):
    settings, record = _source(context)
    return _build_layout(settings, record, settings.source, settings.mesh, settings.reverse)


def _build_layout(settings, record, source, obj, reverse):
    j1, j2 = settings.joint_one, settings.joint_two
    w1, w2 = (settings.width_one, settings.width_two) if settings.three_rings else (0., 0.)
    if not (j1-w1 > .001 and j2+w2 < .999 and j1+w1+.005 < j2-w2):
        raise ValueError('Keep Joint 1 before Joint 2 and leave room for the support rings inside both boundaries.')
    targets = [(j1, 'JOINT_1'), (j2, 'JOINT_2')]
    if settings.three_rings:
        targets += [(j1-w1, 'SUPPORT'), (j1+w1, 'SUPPORT'), (j2-w2, 'SUPPORT'), (j2+w2, 'SUPPORT')]
    a, b = j1+w1, j2-w2
    targets += [(a+(b-a)*i/(settings.between_rings+1), 'BETWEEN') for i in range(1, settings.between_rings+1)]
    rows, ts, rings = record['rings'], record['positions'], []
    for t, kind in sorted(targets):
        source_t = 1-t if reverse else t
        points, band, fraction = _sample(source, rows, ts, source_t, snap=record.get('schema', 1) < 2)
        rings.append({'t': ts[band] if fraction == 0 else source_t, 'kind': kind, 'points': points, 'band': band, 'fraction': fraction,
                      'blocked': source_t <= ts[0]+EPS or source_t >= ts[-1]-EPS})
    start, end = (rows[-1], rows[0]) if reverse else (rows[0], rows[-1])
    plan = {'obj': obj, 'rows': rows, 'positions': ts, 'rings': rings,
            'root': [source.vertices[i].co.copy() for i in start],
            'tip': [source.vertices[i].co.copy() for i in end]}
    if record.get('schema', 1) >= 2:
        plan['root_point'] = Vector(record['tip'] if reverse else record['root'])
        plan['tip_point'] = Vector(record['root'] if reverse else record['tip'])
        plan['length'] = record['length']
        plan['defined'] = bool(record.get('definition_id'))
        if settings.slide_nearby: finger_ring_slide.assign(plan, source, record.get('protected_rows'))
    return plan


def _split_rings(bm, plan):
    bm.verts.ensure_lookup_table()
    rows = plan['rows']
    for band in range(len(rows)-1):
        first_ids, last_ids, previous = rows[band], rows[band+1], 0.
        previous_columns = [0.]*len(first_ids)
        targets = sorted((r for r in plan['rings'] if r['band'] == band and r['fraction']), key=lambda r: r['fraction'])
        for ring in targets:
            values = ring.get('fractions', [ring['fraction']]*len(first_ids))
            fractions = [(f-p)/(1-p) for f, p in zip(values, previous_columns)]
            if any(f <= 0 or f >= 1 for f in fractions): raise ValueError('Support rings cross or collapse along a surface rail.')
            bm.verts.ensure_lookup_table()
            first = [bm.verts[i] for i in first_ids]
            last = [bm.verts[i] for i in last_ids]
            edges = [bm.edges.get((a, b)) for a, b in zip(first, last)]
            if any(e is None for e in edges): raise ValueError('The captured ring correspondence changed.')
            before_count = len(bm.verts)
            bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=False,
                                     edge_percents={e: f if e.verts[0] == a else 1-f for e, a, f in zip(edges, first, fractions)})
            # CustomData allocation (notably shape layers) can invalidate all
            # Python BMVert wrappers. Reacquire using the preserved indices.
            bm.verts.ensure_lookup_table()
            bm.verts.index_update()
            first = [bm.verts[i] for i in first_ids]
            last = [bm.verts[i] for i in last_ids]
            fresh = {bm.verts[i] for i in range(before_count, len(bm.verts))}
            new_row = []
            for a, b in zip(first, last):
                candidates = [e.other_vert(a) for e in a.link_edges if e.other_vert(a) in fresh]
                if len(candidates) != 1:
                    raise ValueError('Subdivision did not create a complete ring; the original mesh was not changed.')
                new_row.append(candidates[0])
            if len(fresh) != len(first): raise ValueError('Unexpected subdivision outside the captured ring.')
            if any((v.co-p).length > 1e-5 for v, p in zip(new_row, ring['points'])):
                raise ValueError('Generated ring differs from preview; the original mesh was not changed.')
            first_ids, previous = [v.index for v in new_row], ring['fraction']
            previous_columns = values
            ring['vertex_ids'] = first_ids
            if 'original_ring' in ring: ring['original_ring']['vertex_ids'] = first_ids
    # Select the two anatomical centers, not all incidental existing edges.
    for seq in (bm.faces, bm.edges, bm.verts):
        for item in seq: item.select_set(False)
    for ring in plan['rings']:
        if not ring['kind'].startswith('JOINT'): continue
        for edge in bm.edges:
            if all(any((v.co-p).length < 1e-6 for p in ring['points']) for v in edge.verts):
                edge.select_set(True)
    bm.select_flush_mode()
    bm.normal_update()


def _preserve_external_normals(source, mesh, touched):
    """Don't re-quantize imported normals on the rest of the character."""
    faces = {tuple(sorted(p.vertices)): p for p in mesh.polygons}
    source_data = source.attributes['custom_normal'].data
    target_data = mesh.attributes['custom_normal'].data
    for face in source.polygons:
        target = faces.get(tuple(sorted(face.vertices)))
        if target is None: continue
        loops = {mesh.loops[i].vertex_index: i for i in target.loop_indices}
        for i in face.loop_indices:
            vi = source.loops[i].vertex_index
            if vi not in touched:
                prop, value = _value(source_data[i])
                setattr(target_data[loops[vi]], prop, value)
    mesh.update()


def _verify(source, mesh, touched, provenance=None):
    provenance = provenance or {}
    # Original indices and all points outside the explicitly moved rings stay.
    for old, new in zip(source.vertices, mesh.vertices):
        if old.index in provenance: continue
        if (old.co-new.co).length > 1e-6 or [(g.group, g.weight) for g in old.groups] != [(g.group, g.weight) for g in new.groups]:
            raise ValueError('Original coordinates or weights changed; update was discarded.')
    if source.shape_keys:
        if not mesh.shape_keys or len(mesh.shape_keys.key_blocks) != len(source.shape_keys.key_blocks):
            raise ValueError('Shape Key list changed; update was discarded.')
        for old, new in zip(source.shape_keys.key_blocks, mesh.shape_keys.key_blocks):
            if old.name != new.name or len(new.data) != len(mesh.vertices) or any((a.co-b.co).length > 1e-6 for i, (a, b) in enumerate(zip(old.data, new.data)) if i not in provenance):
                raise ValueError('Original Shape Key data changed; update was discarded.')
            for vi, (a, b, f) in provenance.items():
                if (new.data[vi].co-old.data[a].co.lerp(old.data[b].co, f)).length > 1e-6:
                    raise ValueError('Moved-ring Shape Key interpolation failed; update was discarded.')
    for vi, (a, b, f) in provenance.items():
        if (mesh.vertices[vi].co-source.vertices[a].co.lerp(source.vertices[b].co, f)).length > 1e-6:
            raise ValueError('A moved ring differs from its preview.')
        ga = {g.group: g.weight for g in source.vertices[a].groups}
        gb = {g.group: g.weight for g in source.vertices[b].groups}
        got = {g.group: g.weight for g in mesh.vertices[vi].groups}
        expected = {k: ga.get(k, 0)*(1-f)+gb.get(k, 0)*f for k in ga.keys() | gb.keys()}
        if any(abs(got.get(k, 0)-expected.get(k, 0)) > 1e-6 for k in got.keys() | expected.keys()):
            raise ValueError('Moved-ring weight interpolation failed; update was discarded.')
    for old in source.attributes:
        if old.name in INTERNAL_ATTRIBUTES or old.name == 'custom_normal' or old.name.startswith(('.select', '.hide')): continue
        new = mesh.attributes.get(old.name)
        if new is None or (new.domain, new.data_type) != (old.domain, old.data_type):
            raise ValueError(f'Attribute "{old.name}" cannot be preserved; update was discarded.')
        if old.domain == 'POINT' and any(_value(a) != _value(b) for i, (a, b) in enumerate(zip(old.data, new.data)) if i not in provenance):
            raise ValueError(f'Original attribute "{old.name}" changed; update was discarded.')
    edge_lookup = {tuple(sorted(e.vertices)): e for e in mesh.edges}
    face_lookup = {tuple(sorted(p.vertices)): p for p in mesh.polygons}
    unchanged = {'EDGE': [], 'FACE': [], 'CORNER': []}
    for edge in source.edges:
        new_edge = edge_lookup.get(tuple(sorted(edge.vertices)))
        if new_edge is None: continue  # A subdivided connector.
        unchanged['EDGE'].append((edge.index, new_edge.index))
        if any(getattr(edge, prop, None) != getattr(new_edge, prop, None)
               for prop in ('use_seam', 'use_edge_sharp', 'use_freestyle_mark')):
            raise ValueError('An original edge flag changed; update was discarded.')
    for face in source.polygons:
        new_face = face_lookup.get(tuple(sorted(face.vertices)))
        if new_face is None: continue  # A subdivided quad.
        unchanged['FACE'].append((face.index, new_face.index))
        if any(getattr(face, prop, None) != getattr(new_face, prop, None)
               for prop in ('material_index', 'use_smooth', 'use_freestyle_mark')):
            raise ValueError('An unaffected face changed; update was discarded.')
        loops = {mesh.loops[i].vertex_index: i for i in new_face.loop_indices}
        unchanged['CORNER'].extend((i, loops[source.loops[i].vertex_index]) for i in face.loop_indices
                                   if source.loops[i].vertex_index not in provenance)
    for a in source.attributes:
        if a.name in INTERNAL_ATTRIBUTES or a.name == 'custom_normal' or a.name.startswith(('.select', '.hide')): continue
        if a.domain in unchanged:
            target = mesh.attributes[a.name]
            if any(_value(a.data[i]) != _value(target.data[j]) for i, j in unchanged[a.domain]):
                raise ValueError(f'Unchanged-region attribute "{a.name}" was modified; update was discarded.')
    if source.has_custom_normals:
        if not mesh.has_custom_normals: raise ValueError('Custom normals were lost; update was discarded.')
        for i, j in unchanged['CORNER']:
            # Only the rebuilt finger changes loop normal spaces. Re-encoding
            # packed normals there has a small angular quantization tolerance.
            tolerance = .005 if source.loops[i].vertex_index in touched else 1e-6
            if (source.corner_normals[i].vector-mesh.corner_normals[j].vector).length > tolerance:
                raise ValueError('Original custom normals could not be preserved safely; update was discarded.')


def stage(source, plans):
    """Shared data-preserving generator; build several disjoint fingers atomically."""
    touched = set()
    for plan in plans:
        if any(r['blocked'] for r in plan['rings']):
            raise ValueError('A joint/support target lies in the protected root or fingertip.')
        ids = {i for row in plan['rows'] for i in row}
        if touched & ids: raise ValueError('Two finger plans overlap; no geometry was changed.')
        touched.update(ids)
    new, bm = source.copy(), bmesh.new()
    try:
        bm.from_mesh(source)
        normal_layer = None
        if source.has_custom_normals:
            normal_layer = bm.loops.layers.float_vector.new('_cd_finger_normal_transfer')
            # Match source corners by face + vertex, not by mutable loop index.
            for face, poly in zip(bm.faces, source.polygons):
                normals = {source.loops[i].vertex_index: source.corner_normals[i].vector.copy()
                           for i in poly.loop_indices}
                for loop in face.loops: loop[normal_layer] = normals[loop.vert.index]
            normal_name = normal_layer.name
        provenance, corners = {}, {}
        for plan in plans:
            if plan.get('sliding'):
                bm.verts.ensure_lookup_table()
                for row, t in plan['moves'].items():
                    points = _sample(source, plan['rows'], plan['positions'], t)[0]
                    for vi, point in zip(plan['rows'][row], points): bm.verts[vi].co = point
                _split_rings(bm, finger_ring_slide.split_plan(plan, source))
                changed, corner_map = finger_ring_slide.transfer(bm, source, plan)
                provenance.update(changed)
                corners.update(corner_map)
                for ring in plan['rings']:
                    if not ring['kind'].startswith('JOINT'): continue
                    ids = ring['vertex_ids']
                    for i, vi in enumerate(ids): bm.edges.get((bm.verts[vi], bm.verts[ids[(i+1) % len(ids)]])).select_set(True)
                bm.select_flush_mode()
            else:
                _split_rings(bm, plan)
                for ring in plan['rings']:
                    if not ring['fraction']: ring['vertex_ids'] = plan['rows'][ring['band']]
                    else:
                        for column, vi in enumerate(ring['vertex_ids']):
                            band = ring['band']
                            f = ring.get('fractions', [ring['fraction']]*len(ring['vertex_ids']))[column]
                            provenance[vi] = (plan['rows'][band][column], plan['rows'][band+1][column], f)
        bm.to_mesh(new)
        new.update()
        if provenance: finger_ring_slide.transfer_point_attributes(source, new, provenance)
        if corners: finger_ring_slide.transfer_corner_attributes(source, new, corners, normal_name if normal_layer is not None else None)
        if normal_layer is not None:
            attribute = new.attributes[normal_name]
            normals = [tuple(d.vector.normalized()) for d in attribute.data]
            new.attributes.remove(attribute)
            new.normals_split_custom_set(normals)
            _preserve_external_normals(source, new, touched)
        if SOURCE_TAG in new: del new[SOURCE_TAG]
        new[RESULT_TAG] = True
        _verify(source, new, touched, provenance)
        return new
    except Exception:
        bpy.data.meshes.remove(new)
        raise
    finally: bm.free()


def apply_layout(context, after_commit=None):
    obj = _edit(context)
    settings, _ = _source(context)
    if fingerprint(obj) != settings.signature:
        raise ValueError('The mesh, Shape Keys or weights changed after capture. Capture the strip again; no edits were overwritten.')
    plan = build_layout(context)
    source, old = settings.source, obj.data
    new = stage(source, [plan])
    old_signature, old_applied = settings.signature, settings.applied
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
        obj.data = new
        obj.active_shape_key_index = 0
        bpy.ops.object.mode_set(mode='EDIT')
        if after_commit: after_commit()
        obj.update_from_editmode()
        settings.signature = fingerprint(obj)
        settings.applied = True
        new.name = old.name
        from . import finger_bank
        finger_bank.after_layout(context)
        if old.users == 0 and old.get(RESULT_TAG): bpy.data.meshes.remove(old)
        return len(new.vertices)-len(source.vertices)
    except Exception:
        if obj.data != old:
            if obj.mode == 'EDIT': bpy.ops.object.mode_set(mode='OBJECT')
            obj.data = old
        if obj.mode != 'EDIT': bpy.ops.object.mode_set(mode='EDIT')
        settings.signature, settings.applied = old_signature, old_applied
        raise
    finally:
        if new.users == 0: bpy.data.meshes.remove(new)


def clear(context):
    settings = state(context)
    source = settings.source
    settings.mesh = None
    settings.source = None
    settings.record = settings.signature = ''
    settings.applied = False
    if source and source.users == 0 and source.get(SOURCE_TAG): bpy.data.meshes.remove(source)
