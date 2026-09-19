"""Conservative nearest-ring reuse and immutable-surface data transfer."""
import bisect

from .mesh_mirror import INTERNAL_ATTRIBUTES, _value


def protected_rows(source, rows):
    faces = {frozenset(p.vertices): p for p in source.polygons}
    edges = {frozenset(e.vertices): e for e in source.edges}
    protected = set()
    n = len(rows[0])
    for i in range(1, len(rows)-1):
        for j in range(n):
            k = (j+1) % n
            edge = edges[frozenset((rows[i][j], rows[i][k]))]
            if edge.use_seam or edge.use_edge_sharp or getattr(edge, 'use_freestyle_mark', False):
                protected.add(i)
                break
            a = faces[frozenset((rows[i-1][j], rows[i-1][k], rows[i][j], rows[i][k]))]
            b = faces[frozenset((rows[i][j], rows[i][k], rows[i+1][j], rows[i+1][k]))]
            if (a.material_index, a.use_smooth) != (b.material_index, b.use_smooth): protected.add(i)
            for attr in source.attributes:
                if attr.name in INTERNAL_ATTRIBUTES or attr.name.startswith(('.select', '.hide')) or attr.name == 'custom_normal': continue
                if attr.domain == 'FACE' and _value(attr.data[a.index]) != _value(attr.data[b.index]): protected.add(i)
                # A longitudinal UV seam is fine; a discontinuity ACROSS the
                # moving ring pins it so the tool cannot carry the seam away.
                if attr.domain == 'CORNER':
                    for vi in (rows[i][j], rows[i][k]):
                        ia = next(li for li in a.loop_indices if source.loops[li].vertex_index == vi)
                        ib = next(li for li in b.loop_indices if source.loops[li].vertex_index == vi)
                        if _value(attr.data[ia]) != _value(attr.data[ib]): protected.add(i)
            for attr in source.attributes:
                if attr.domain == 'EDGE' and attr.name not in INTERNAL_ATTRIBUTES and not attr.name.startswith(('.select', '.hide')):
                    _, value = _value(attr.data[edge.index])
                    if value != 0 and value is not False: protected.add(i)
    return protected


def assign(plan, source, protected=None):
    ts = plan['positions']
    protected = protected_rows(source, plan['rows']) if protected is None else set(protected)
    taken, moves = set(), {}
    # Preserve exact matches, then prioritize the two anatomical centers.
    for ring in plan['rings']:
        if ring['fraction'] == 0:
            ring['reuse'] = ring['band']
            taken.add(ring['band'])
    for ring in sorted(plan['rings'], key=lambda r: (not r['kind'].startswith('JOINT'), r['t'])):
        if ring.get('blocked') or 'reuse' in ring: continue
        options = [i for i in range(1, len(ts)-1) if i not in taken and i not in protected
                   and abs(ts[i]-ring['t']) <= .49*min(ts[i]-ts[i-1], ts[i+1]-ts[i])]
        if not options: continue
        i = min(options, key=lambda k: abs(ts[k]-ring['t']))
        moves[i] = ring['t']
        ring['reuse'] = i
        taken.add(i)
    plan['moves'] = moves
    plan['protected_rows'] = protected
    plan['sliding'] = True


def split_plan(plan, source):
    """Split AFTER sliding: flank rings must not end up across their center."""
    from .finger_layout import _sample
    ts, rows = plan['positions'], plan['rows']
    new_ts = [plan['moves'].get(i, t) for i, t in enumerate(ts)]
    points = [_sample(source, rows, ts, t)[0] for t in new_ts]
    rings = []
    for ring in plan['rings']:
        if 'reuse' in ring:
            ring['vertex_ids'] = rows[ring['reuse']]
            continue
        i = max(0, min(bisect.bisect_right(new_ts, ring['t'])-1, len(ts)-2))
        f = (ring['t']-new_ts[i])/(new_ts[i+1]-new_ts[i])
        rings.append(dict(ring, band=i, fraction=f, original_ring=ring,
                          points=[a.lerp(b, f) for a, b in zip(points[i], points[i+1])]))
    return dict(plan, rings=rings, positions=new_ts)


def _provenance(rows, ts, t, column):
    i = max(0, min(bisect.bisect_right(ts, t)-1, len(ts)-2))
    f = max(0., min(1., (t-ts[i])/(ts[i+1]-ts[i])))
    return rows[i][column], rows[i+1][column], f


def transfer(bm, source, plan):
    """Resample moved/new points and each side's corner data from the source."""
    rows, ts = plan['rows'], plan['positions']
    slots = {vi: (plan['moves'].get(i, t), j) for i, (row, t) in enumerate(zip(rows, ts)) for j, vi in enumerate(row)}
    changed = {vi for i in plan['moves'] for vi in rows[i]}
    for ring in plan['rings']:
        for j, vi in enumerate(ring['vertex_ids']):
            slots[vi] = ring['t'], j
            if 'reuse' not in ring: changed.add(vi)
    bm.verts.ensure_lookup_table()
    provenance = {}
    deform = bm.verts.layers.deform.active
    for vi in changed:
        t, column = slots[vi]
        a, b, f = _provenance(rows, ts, t, column)
        va, vb = source.vertices[a], source.vertices[b]
        bm.verts[vi].co = va.co.lerp(vb.co, f)
        if source.shape_keys:
            for key in source.shape_keys.key_blocks:
                layer = bm.verts.layers.shape.get(key.name)
                if layer is None: raise ValueError('A Shape Key layer is missing; no geometry was changed.')
                bm.verts[vi][layer] = key.data[a].co.lerp(key.data[b].co, f)
        if deform:
            ga = {g.group: g.weight for g in va.groups}
            gb = {g.group: g.weight for g in vb.groups}
            values = bm.verts[vi][deform]
            values.clear()
            for group in ga.keys() | gb.keys():
                values[group] = ga.get(group, 0)*(1-f)+gb.get(group, 0)*f
        provenance[vi] = (a, b, f)
    faces = {frozenset(f.vertices): f for f in source.polygons}
    corners = {}
    n = len(rows[0])
    for face in bm.faces:
        if not any(v.index in changed for v in face.verts): continue
        if any(v.index not in slots for v in face.verts):
            raise ValueError('A moving ring would modify the protected root or fingertip.')
        columns = {slots[v.index][1] for v in face.verts}
        j = next((k for k in columns if (k+1) % n in columns), None)
        if len(columns) != 2 or j is None: raise ValueError('Invalid circumferential correspondence.')
        k = (j+1) % n
        mid = sum(slots[v.index][0] for v in face.verts)/len(face.verts)
        for loop in face.loops:
            t = slots[loop.vert.index][0]
            # At an original cross-section, preserve this face's side of any
            # corner discontinuity rather than averaging across the seam.
            value = t+1e-8 if mid > t else t-1e-8
            band = max(0, min(bisect.bisect_right(ts, value)-1, len(ts)-2))
            src = faces[frozenset((rows[band][j], rows[band][k], rows[band+1][j], rows[band+1][k]))]
            column = slots[loop.vert.index][1]
            a = next(li for li in src.loop_indices if source.loops[li].vertex_index == rows[band][column])
            b = next(li for li in src.loop_indices if source.loops[li].vertex_index == rows[band+1][column])
            f = max(0., min(1., (t-ts[band])/(ts[band+1]-ts[band])))
            corners[(frozenset(v.index for v in face.verts), loop.vert.index)] = (a, b, f)
    bm.normal_update()
    return provenance, corners


def interpolate_value(value_a, value_b, f, floating):
    if not floating: return value_a if f < .5 else value_b
    if isinstance(value_a, (tuple, list)):
        return tuple(a+(b-a)*f for a, b in zip(value_a, value_b))
    return value_a+(value_b-value_a)*f


def transfer_point_attributes(source, mesh, provenance):
    # Explicit discrete-value rule; do not depend on BMesh's int interpolation.
    for attr in source.attributes:
        if attr.domain != 'POINT' or attr.name in INTERNAL_ATTRIBUTES or attr.name.startswith(('.select', '.hide')): continue
        dst = mesh.attributes[attr.name]
        for vi, (a, b, f) in provenance.items():
            prop, va = _value(attr.data[a])
            _, vb = _value(attr.data[b])
            setattr(dst.data[vi], prop, interpolate_value(va, vb, f, attr.data_type not in {'INT', 'BOOLEAN'}))


def transfer_corner_attributes(source, mesh, corners, normal_name=None):
    mapping = {}
    for face in mesh.polygons:
        key = frozenset(face.vertices)
        for li in face.loop_indices:
            values = corners.get((key, mesh.loops[li].vertex_index))
            if values is not None: mapping[li] = values
    for attr in source.attributes:
        if attr.domain != 'CORNER' or attr.name in INTERNAL_ATTRIBUTES or attr.name == 'custom_normal' or attr.name.startswith(('.select', '.hide')): continue
        dst = mesh.attributes[attr.name]
        for li, (a, b, f) in mapping.items():
            prop, va = _value(attr.data[a])
            _, vb = _value(attr.data[b])
            setattr(dst.data[li], prop, interpolate_value(va, vb, f, attr.data_type not in {'INT', 'BOOLEAN'}))
    if normal_name:
        dst = mesh.attributes[normal_name]
        for li, (a, b, f) in mapping.items():
            dst.data[li].vector = source.corner_normals[a].vector.lerp(source.corner_normals[b].vector, f).normalized()
