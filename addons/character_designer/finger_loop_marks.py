"""Explicit existing-loop marks and safe rest-joint alignment.

There are no handlers, timers or geometry writes here. Stored coordinates are
display data; only Mark and Align inspect the current mesh. Alignment always
verifies the live loops and local surface before starting the bone transaction.
Capture supplies direction/range hints, never a historical topology proof.
The four non-thumb chains use their fixed root-to-tip internal line. Thumb
joints follow the existing curved bone path instead. Loop planes determine
longitudinal positions; off-center loop centroids never bend a straight finger.
Marks and joint alignment affect the active side only. Mirroring the other
hand is a separate, explicit Mirror Selected Region operation.
"""
import json
import math

from mathutils import Vector
from mathutils.kdtree import KDTree

from . import finger_bank as bank, finger_chain as chain
from . import finger_definition as definition
from . import finger_internal as internal, finger_range as ranges
from . import finger_symmetry as symmetry, finger_targets as targets

PROPERTY = 'character_designer_finger_loop_marks'
VERSION = 1
EPS = 1e-6


def _saved(obj):
    try:
        value = json.loads(obj.get(PROPERTY, '{}'))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def records(obj, key):
    """Read small persistent marker records; never inspect mesh data."""
    value = _saved(obj).get(key, {})
    return value if isinstance(value, dict) else {}


def effective_key(obj, key, mirror):
    """Compatibility accessor: marks always belong to the requested side."""
    return key


def points(obj, key, *, saved=None):
    """Return open ordered polylines in mesh-local space for cached drawing."""
    result = {}
    marked = records(obj, key) if saved is None else saved.get(key, {}) if isinstance(saved, dict) else {}
    if not isinstance(marked, dict): return result
    for number, record in marked.items():
        if number not in {'1', '2'} or not isinstance(record, dict): continue
        coordinates = record.get('coordinates', ())
        try:
            path = tuple(tuple(float(v) for v in point) for point in coordinates)
            if len(path) < 4 or any(len(p) != 3 or not all(map(math.isfinite, p)) for p in path): continue
            result[int(number)] = path
        except (TypeError, ValueError):
            continue
    return result


def _active(context):
    obj = bank.active_object(context)
    if obj is None: raise ValueError('Capture this finger first.')
    if obj.library or obj.override_library or not obj.is_editable:
        raise ValueError('Use a local editable character mesh.')
    return obj, obj.character_designer_finger_bank.active


def _reference(obj, key, reader):
    """Read only saved spatial hints; old topology IDs and errors are irrelevant."""
    slot = obj.character_designer_finger_bank.slots.get(key)
    if not slot or not slot.guide.record:
        raise ValueError('Capture this finger first.')
    try:
        record = json.loads(slot.guide.record)
        body = record['body']
        root, tip = Vector(body['root']), Vector(body['tip'])
        radius = float(body['radius'])
        if (len(root) != 3 or len(tip) != 3 or
                not all(math.isfinite(v) for p in (root, tip) for v in p) or
                not math.isfinite(radius) or radius <= EPS or (tip-root).length <= EPS):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('Capture this finger direction and range first.') from None
    # Do not carry historical face/ring IDs into any current geometry checks.
    return {'body': {'root': list(root), 'tip': list(tip),
                     'radius': radius, 'length': (tip-root).length}}


def _ring(obj, bm, reference, vertices):
    """Check this live cycle and a spatial hint, not captured vertex membership."""
    if any(v.hide for v in vertices):
        raise ValueError('Unhide the complete marked loop first.')
    edges = [bm.edges.get((a, b)) for a, b in zip(vertices, vertices[1:]+vertices[:1])]
    if any(edge is None or edge.hide or len(edge.link_faces) != 2 for edge in edges):
        raise ValueError('Select one closed surface loop, without holes or branches.')
    center, normal = _section(vertices)
    body = reference['body']
    root, tip = Vector(body['root']), Vector(body['tip'])
    axis = tip-root
    fraction = (center-root).dot(axis)/axis.length_squared
    if not 1e-5 < fraction < 1-1e-5:
        raise ValueError('Choose an internal joint loop; root and tip stay fixed.')
    radial = (center-root-axis*fraction).length
    if radial > body['radius']*1.5:
        raise ValueError('Select one complete loop on the active finger and side.')
    # A longitudinal face boundary is also a closed edge cycle. Its section
    # plane must actually cross the saved direction, with a coherent local area.
    if abs(normal.dot(axis.normalized())) < .5:
        raise ValueError('Choose a transverse loop across the active finger.')
    radius = max((v.co-center).length for v in vertices)
    if radius <= EPS or max(abs((v.co-center).dot(normal)) for v in vertices) > radius*.35:
        raise ValueError('The selected loop does not form a stable cross section.')
    if radius > max(body['radius']*2.5, body['length']*.3):
        raise ValueError('Select one complete loop on the active finger and side.')
    _simple_section(vertices, center, normal)


def _simple_section(vertices, center, normal):
    """Reject crossing/overlapping projected edges without scanning neighbors."""
    u = (vertices[0].co-center)
    u -= normal*u.dot(normal)
    if u.length <= EPS: raise ValueError('The loop section is collapsed.')
    u.normalize()
    v = normal.cross(u)
    points = [((item.co-center).dot(u), (item.co-center).dot(v)) for item in vertices]
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def on(a, b, c):
        return (min(a[0], b[0])-EPS <= c[0] <= max(a[0], b[0])+EPS and
                min(a[1], b[1])-EPS <= c[1] <= max(a[1], b[1])+EPS)
    for i, a in enumerate(points):
        b = points[(i+1) % len(points)]
        if math.dist(a, b) <= EPS: raise ValueError('The loop section is collapsed.')
        for j in range(i+1, len(points)):
            if j == i+1 or i == 0 and j == len(points)-1: continue
            c, d = points[j], points[(j+1) % len(points)]
            ac, ad, ca, cb = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
            if (ac*ad < 0 and ca*cb < 0 or
                    abs(ac) <= EPS*EPS and on(a, b, c) or abs(ad) <= EPS*EPS and on(a, b, d) or
                    abs(ca) <= EPS*EPS and on(c, d, a) or abs(cb) <= EPS*EPS and on(c, d, b)):
                raise ValueError('The selected loop section crosses itself.')


def _section(vertices):
    """Area centroid and normal of the marked cross section."""
    points = [v.co.copy() for v in vertices]
    if len(points) < 4 or any(not all(math.isfinite(value) for value in point) for point in points):
        raise ValueError('The loop section has invalid coordinates.')
    anchor = sum(points, Vector())/len(points)
    normal = sum(((a-anchor).cross(b-anchor) for a, b in
                  zip(points, points[1:]+points[:1])), Vector())
    if normal.length <= EPS*EPS: raise ValueError('The loop section is collapsed.')
    normal.normalize()
    terms = [((a-anchor).cross(b-anchor).dot(normal), (anchor+a+b)/3)
             for a, b in zip(points, points[1:]+points[:1])]
    area = sum(weight for weight, _ in terms)
    if abs(area) <= EPS*EPS: raise ValueError('The loop section is collapsed.')
    return sum((point*weight for weight, point in terms), Vector())/area, normal


def _center(vertices):
    return _section(vertices)[0]


def _marker(obj, key, vertices, reference):
    center, normal = _section(vertices)
    root, tip = map(Vector, (reference['body']['root'], reference['body']['tip']))
    axis = tip-root
    fraction = (center-root).dot(axis)/axis.length_squared
    if not 0 < fraction < 1:
        raise ValueError('Choose an internal joint loop; root and tip stay fixed.')
    return dict(version=VERSION, key=key, basis_key=definition._basis_name(obj),
                ids=[v.index for v in vertices], coordinates=[list(v.co) for v in vertices],
                center=list(center), normal=list(normal), fraction=fraction)


def _plane_hit(path, marker):
    """Unique path/section intersection, plus its distance from the root."""
    center, normal = Vector(marker['center']), Vector(marker['normal'])
    lengths = [(b-a).length for a, b in zip(path, path[1:])]
    total = sum(lengths)
    tolerance = max(EPS, total*1e-6)
    if total <= tolerance or normal.length <= EPS:
        raise ValueError('The bone path or marked cross section is collapsed.')
    normal.normalize()
    hits, distance = [], 0.
    for a, b, length in zip(path, path[1:], lengths):
        if length <= tolerance:
            raise ValueError('The bone path contains a collapsed segment.')
        start, end = (a-center).dot(normal), (b-center).dot(normal)
        denominator = start-end
        if abs(denominator) <= tolerance:
            if abs(start) <= tolerance and abs(end) <= tolerance:
                raise ValueError('A marked loop follows a bone segment; choose a transverse loop.')
        else:
            fraction = start/denominator
            slack = tolerance/length
            if -slack <= fraction <= 1+slack:
                fraction = max(0., min(1., fraction))
                hit = a.lerp(b, fraction)
                position = distance+length*fraction
                if not hits or (hit-hits[-1][0]).length > tolerance:
                    hits.append((hit, position/total))
        distance += length
    if len(hits) != 1:
        raise ValueError('The marked loop must cross the internal bone path once.')
    point, fraction = hits[0]
    if not tolerance/total < fraction < 1-tolerance/total:
        raise ValueError('The marked joints must stay between the fixed root and tip.')
    return point, fraction


def _joint_nodes(key, nodes, markers):
    """Place joints on the internal line, or preserve Thumb's curved path."""
    thumb = key.split('.')[0] == 'THUMB'
    path = nodes if thumb else [nodes[0], nodes[-1]]
    hits = [_plane_hit(path, marker) for marker in markers]
    if hits[1][1]-hits[0][1] <= 1e-5:
        raise ValueError('Select both joint loops and mark them again.')
    proposed = [nodes[0].copy(), hits[0][0], hits[1][0], nodes[-1].copy()]
    if thumb:
        scale = sum((b-a).length for a, b in zip(nodes, nodes[1:]))
        tolerance = scale*scale*1e-7
        for before, after in zip(chain._turns(nodes), chain._turns(proposed)):
            if before.length > tolerance and (after.length <= tolerance or before.dot(after) <= 0):
                raise ValueError('These marks would flatten or reverse the thumb bend; choose other loops.')
    return proposed


def mark(context, number):
    """Replace one explicit mark without modifying mesh, selection or bones."""
    if number not in (1, 2): raise ValueError('Choose joint mark 1 or 2.')
    obj, key = _active(context)
    if (context.mode != 'EDIT_MESH' or context.edit_object != obj or
            len(context.objects_in_mode_unique_data) != 1):
        raise ValueError('Select one complete edge loop in Mesh Edit Mode.')
    if definition._key_name(obj) != definition._basis_name(obj):
        raise ValueError('Select Basis before marking rest-joint loops.')
    with definition.FrameReader() as reader:
        bm = reader.snapshot(obj, definition._basis_name(obj))
        selected = [edge for edge in bm.edges if edge.select and not edge.hide]
        vertices = ranges.ordered_loop(selected)
        reference = _reference(obj, key, reader)
        _ring(obj, bm, reference, vertices)
        result = _marker(obj, key, vertices, reference)
    saved = _saved(obj)
    previous = saved.get(key, {})
    marks = dict(previous) if isinstance(previous, dict) else {}
    marks[str(number)] = result
    saved[key] = marks
    obj[PROPERTY] = json.dumps(saved, separators=(',', ':'))
    return result


def _selected_loops(edges):
    """Split only the selected edges, then require complete disjoint cycles."""
    remaining = set(edges)
    if not remaining: raise ValueError('Select one or two complete joint loops.')
    linked = {}
    for edge in edges:
        for vertex in edge.verts: linked.setdefault(vertex, set()).add(edge)
    loops = []
    while remaining:
        pending, component = [min(remaining, key=lambda edge: edge.index)], set()
        while pending:
            edge = pending.pop()
            if edge not in remaining: continue
            remaining.remove(edge)
            component.add(edge)
            for vertex in edge.verts: pending.extend(linked[vertex] & remaining)
        loops.append(ranges.ordered_loop(component))
        if len(loops) > 2: raise ValueError('Select at most two joint loops on this finger.')
    return loops


def _same_loop(first, second):
    """Compare small saved coordinates, independent of index or cycle order."""
    a, b = [list(map(Vector, record.get('coordinates', ()))) for record in (first, second)]
    if len(a) != len(b) or not a: return False
    hits = [[i for i, q in enumerate(b) if (p-q).length <= EPS] for p in a]
    return all(len(values) == 1 for values in hits) and len({values[0] for values in hits}) == len(a)


def _merge_marks(previous, incoming):
    """One click stores a root-to-tip pair without requiring named joint slots."""
    if len(incoming) == 2:
        result = list(incoming)
    else:
        result = []
        for name in ('1', '2'):
            if name not in previous: continue
            record = previous[name]
            if (not isinstance(record, dict) or not isinstance(record.get('fraction'), (float, int))
                    or not math.isfinite(record['fraction']) or not 0 < record['fraction'] < 1):
                raise ValueError('Select both joint loops and mark them again.')
            try:
                duplicate = any(_same_loop(record, other) for other in result)
            except (ValueError, TypeError):
                raise ValueError('Select both joint loops and mark them again.') from None
            if not duplicate: result.append(record)
        new = incoming[0]
        try: matches = [i for i, record in enumerate(result) if _same_loop(record, new)]
        except (ValueError, TypeError):
            raise ValueError('Select both joint loops and mark them again.') from None
        if matches:
            result[matches[0]] = new
        elif len(result) < 2:
            result.append(new)
        else:
            distances = [abs(record['fraction']-new['fraction']) for record in result]
            if abs(distances[0]-distances[1]) <= 1e-5:
                raise ValueError('This loop is equally close to both marks; select both intended loops.')
            result[0 if distances[0] < distances[1] else 1] = new
    result.sort(key=lambda record: record['fraction'])
    if len(result) == 2 and result[1]['fraction']-result[0]['fraction'] <= 1e-5:
        raise ValueError('Select two distinct joint loops with space between them.')
    return {str(i+1): record for i, record in enumerate(result)}


def mark_selected(context):
    """Mark one or two existing loops; assign internal order automatically.

    A new single loop joins one prior mark or replaces the nearest of a pair.
    Selecting both intended loops always replaces the pair unambiguously. All
    selected components must pass before any saved marker metadata is changed.
    """
    obj, key = _active(context)
    if (context.mode != 'EDIT_MESH' or context.edit_object != obj or
            len(context.objects_in_mode_unique_data) != 1):
        raise ValueError('Select one or two complete edge loops in Mesh Edit Mode.')
    if definition._key_name(obj) != definition._basis_name(obj):
        raise ValueError('Select Basis before marking rest-joint loops.')
    with definition.FrameReader() as reader:
        bm = reader.snapshot(obj, definition._basis_name(obj))
        loops = _selected_loops([edge for edge in bm.edges if edge.select and not edge.hide])
        reference = _reference(obj, key, reader)
        incoming = []
        for vertices in loops:
            _ring(obj, bm, reference, vertices)
            incoming.append(_marker(obj, key, vertices, reference))
        saved = _saved(obj)
        previous = saved.get(key, {})
        result = _merge_marks(previous if isinstance(previous, dict) else {}, incoming)
    saved[key] = result
    obj[PROPERTY] = json.dumps(saved, separators=(',', ':'))
    return result


def clear(context, number=None):
    """Forget the active side's marks only; no mesh or bone change."""
    obj, key = _active(context)
    if number is not None and number not in (1, 2): raise ValueError('Choose joint mark 1 or 2.')
    saved = _saved(obj)
    if number is None: saved.pop(key, None)
    elif isinstance(saved.get(key), dict):
        saved[key].pop(str(number), None)
        if not saved[key]: saved.pop(key)
    if saved: obj[PROPERTY] = json.dumps(saved, separators=(',', ':'))
    elif PROPERTY in obj: del obj[PROPERTY]


def clear_key(obj, key):
    """Forget exactly the side whose owning Basic Setup was cleared."""
    saved = _saved(obj)
    saved.pop(key, None)
    if saved: obj[PROPERTY] = json.dumps(saved, separators=(',', ':'))
    elif PROPERTY in obj: del obj[PROPERTY]


def clear_digit(obj, digit):
    """Explicit legacy pair clear; current Basic Setup uses clear_key."""
    saved = _saved(obj)
    for side in ('L', 'R'): saved.pop(digit+'.'+side, None)
    if saved: obj[PROPERTY] = json.dumps(saved, separators=(',', ':'))
    elif PROPERTY in obj: del obj[PROPERTY]


def _recover(bm, record, number):
    message = 'A marked loop changed; mark that loop again.'
    try:
        ids, coordinates = record['ids'], list(map(Vector, record['coordinates']))
        if len(ids) != len(coordinates) or len(ids) < 4: raise ValueError(message)
        if not all(isinstance(i, int) and i >= 0 for i in ids): raise ValueError(message)
        exact = all(i < len(bm.verts) and (bm.verts[i].co-p).length <= EPS
                    for i, p in zip(ids, coordinates))
        if not exact:
            # Index changes elsewhere are harmless. Coordinate recovery is
            # explicit and rejects ambiguous coincident vertices.
            tree = KDTree(len(bm.verts))
            for v in bm.verts: tree.insert(v.co, v.index)
            tree.balance()
            hits = [tree.find_range(p, EPS) for p in coordinates]
            if any(len(values) != 1 for values in hits): raise ValueError(message)
            ids = [values[0][1] for values in hits]
        if len(set(ids)) != len(ids): raise ValueError(message)
        vertices = [bm.verts[i] for i in ids]
        edges = [bm.edges.get((a, b)) for a, b in zip(vertices, vertices[1:]+vertices[:1])]
        if any(edge is None or edge.hide for edge in edges): raise ValueError(message)
        ranges.ordered_loop(edges)
        return vertices
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(message) from exc


def _fresh_reference(bm, loops, hint):
    """Build a bounded current finger volume from the marked ring itself.

    No census, saved face membership or old surface-equivalence test is used.
    Quad traversal stops at the palm transition; only a small closed distal cap
    is considered. An uncertain local surface leaves both marks and bones alone.
    """
    message = 'Cannot verify this finger locally; check its closed tip and surface before Align.'
    first = loops[0]
    entry = bm.edges.get((first[0], first[1]))
    if entry is None: raise ValueError(message)
    axis = Vector(hint['body']['tip'])-Vector(hint['body']['root'])
    candidates = {}
    for face in entry.link_faces:
        if face.hide or len(face.verts) != 4: continue
        try:
            rows, used = ranges._sleeve(bm, face, entry, ranges.quad_band)
        except ValueError:
            continue
        cap_a = ranges._end_cap(list(reversed(rows)), used)
        cap_b = ranges._end_cap(rows, used)
        if (cap_a is None) == (cap_b is None): continue
        if cap_a is not None: rows.reverse()
        cap, cap_vertices, direction = cap_a if cap_a is not None else cap_b
        # Expansion into the palm is a traversal stop, not evidence belonging
        # to the captured body. Trim it using the current adjacent sections.
        while len(rows) > 4:
            a, b = ranges._center(rows[0]), ranges._center(rows[1])
            if max((v.co-a).length for v in rows[0]) <= max((v.co-b).length for v in rows[1])*1.8:
                break
            rows.pop(0)
        row_sets = [set(row) for row in rows]
        if any(set(loop) not in row_sets[1:-1] for loop in loops): continue
        included = set().union(*row_sets)
        used = {f for f in used if set(f.verts) <= included}
        if any(len(v.link_edges) != 4 or set(v.link_faces)-used for row in rows[1:-1] for v in row):
            continue
        centers = [ranges._center(row) for row in rows]
        root = centers[0]
        tip = centers[-1]+direction*max((v.co-centers[-1]).dot(direction) for v in cap_vertices)
        if (tip-root).dot(axis) <= 0: continue
        length = sum((b-a).length for a, b in zip(centers, centers[1:]))+(tip-centers[-1]).length
        radius = max((v.co-center).length for row, center in zip(rows, centers) for v in row)
        if radius <= EPS or length <= radius*2: continue
        faces = used | cap
        body = {'rings': [[v.index for v in row] for row in rows],
                'faces': sorted(f.index for f in faces),
                'vertices': sorted({v.index for f in faces for v in f.verts}),
                'root': list(root), 'tip': list(tip), 'length': length, 'radius': radius}
        candidates[frozenset(body['faces'])] = body
    if len(candidates) != 1: raise ValueError(message)
    body = next(iter(candidates.values()))
    return {'body': body, 'basis': {'path': [body['root'], body['tip']]}}


def _covered_span(a, b, original_segments, tolerance):
    """True only when current bone segments already cover this exact line span.

    Redistributing joints along an existing line does not introduce a new bone
    path. Merge collinear coverage intervals so crossing an old joint is safe;
    a bent corner, gap, or lateral displacement cannot pass as unchanged.
    """
    direction = b-a
    length = direction.length
    if length <= tolerance or not original_segments: return False
    direction /= length
    intervals = []
    for c, d in original_segments:
        start, end = (c-a).dot(direction), (d-a).dot(direction)
        if ((c-a-direction*start).length > tolerance or
                (d-a-direction*end).length > tolerance):
            continue
        start, end = max(0., min(start, end)), min(length, max(start, end))
        if end-start > tolerance: intervals.append((start, end))
    covered = 0.
    for start, end in sorted(intervals):
        if start > covered+tolerance: return False
        covered = max(covered, end)
        if covered >= length-tolerance: return True
    return False


def _regular_root_band(bm, body):
    """Extend one current annular quad band; never search a palm component."""
    row = [bm.verts[index] for index in body['rings'][0]]
    used, included = set(body['faces']), set(body['vertices'])
    mapping, added = {}, set()
    if len(row) > 64:
        raise ValueError('The proximal boundary exceeds the local proof budget.')
    for a, b in zip(row, row[1:]+row[:1]):
        edge = bm.edges.get((a, b))
        faces = [] if edge is None else [face for face in edge.link_faces if face.index not in used]
        if edge is None or len(edge.link_faces) != 2 or len(faces) != 1:
            raise ValueError('The proximal boundary has no unique adjacent band.')
        face = faces[0]
        if face.hide or len(face.verts) != 4 or face in added:
            raise ValueError('The proximal boundary is not a complete visible quad band.')
        loop = next(loop for loop in face.loops if loop.edge == edge)
        u, v = loop.vert, loop.link_loop_next.vert
        w, x = loop.link_loop_next.link_loop_next.vert, loop.link_loop_prev.vert
        for inside, outside in ((u, x), (v, w)):
            if inside in mapping and mapping[inside] != outside:
                raise ValueError('The proximal boundary branches at the palm.')
            mapping[inside] = outside
        added.add(face)
    outer = [mapping[vertex] for vertex in row]
    if (len(set(outer)) != len(row) or any(vertex.hide or vertex.index in included for vertex in outer) or
            any(edge.hide or len(edge.link_faces) > 2 for face in added for edge in face.edges)):
        raise ValueError('The proximal band folds, branches, or meets hidden geometry.')
    for a, b in zip(outer, outer[1:]+outer[:1]):
        if bm.edges.get((a, b)) is None:
            raise ValueError('The proximal band does not have one closed outer boundary.')
    center, normal = _section(outer)
    _simple_section(outer, center, normal)
    old_center = Vector(body['root'])
    axis = (Vector(body['tip'])-old_center).normalized()
    progress = (old_center-center).dot(axis)
    radius = max((vertex.co-center).length for vertex in outer)
    if (progress <= max(EPS, body['length']*1e-5) or abs(normal.dot(axis)) < .5 or
            max(abs((vertex.co-center).dot(normal)) for vertex in outer) > radius*.35):
        raise ValueError('The proximal band does not define a reliable rootward section.')
    faces = sorted(used | {face.index for face in added})
    return dict(body, rings=[[vertex.index for vertex in outer]]+body['rings'], faces=faces,
                vertices=sorted({vertex.index for index in faces for vertex in bm.faces[index].verts}),
                root=list(center), length=body['length']+progress,
                radius=max(body['radius'], radius))


def _rootward(bm, body, point, margin):
    row = [bm.verts[index] for index in body['rings'][0]]
    center, normal = _section(row)
    if normal.dot(Vector(body['tip'])-center) < 0: normal.negate()
    return (point-center).dot(normal) < -margin


def _root_volume(bm, body, point, margin, volume):
    """Prove a changed proximal path with local bands or the live closed shell.

    A normal finger root exposes one-to-one quad bands.  At a real palm
    transition the first band can split into two branches, even though the
    current connected mesh is a closed shell and can prove the planned path
    safely.  In that explicit Align operation only, use that live shell as a
    bounded fallback; this is never built by a draw callback or a monitor.
    """
    failure = None
    if not _rootward(bm, body, point, margin): return volume
    for _ in range(3):
        try:
            body = _regular_root_band(bm, body)
            volume = internal.Volume(bm, body)
        except ValueError as exc:
            failure = exc
            break
        if ((volume.inside(point) and volume.distance(point) >= margin) or
                not _rootward(bm, body, point, margin)):
            return volume
    # The fallback consumes only the current connected shell and is reached
    # after the local proof fails.  Internal.Volume still verifies the whole
    # changed segment, including the fixed endpoint, before any bone write.
    try:
        actual = internal.Volume(bm, dict(body, root_extension=True))
        distance = actual.distance(point)
        if not actual.inside(point) or distance is None or distance < margin:
            raise ValueError('The fixed root is outside the current closed shell.')
        return actual
    except ValueError as exc:
        if failure is not None:
            exc.add_note(f'Local proximal proof failed: {failure}')
        raise ValueError('Cannot verify the fixed root locally; check its position and proximal surface.') from exc


def _certify(obj, rig, reference, nodes, bm, *, original_nodes=None,
             original_segments=None, bone_names=()):
    """Check new joints and changed paths; retain exactly covered rest spans.

    The sleeve's root cap is an artificial proof boundary. It must not reject
    unchanged existing spans in the palm while merely redistributing joints.
    A changed proximal path still needs actual current local surface proof.
    """
    body = reference['body']
    volume = internal.Volume(bm, body)
    transform = obj.matrix_world.inverted() @ rig.matrix_world
    points = [transform @ Vector(p) for p in nodes]
    if original_segments is None:
        original = [] if original_nodes is None else list(zip(original_nodes, original_nodes[1:]))
    else:
        original = original_segments
    original = [(transform @ Vector(a), transform @ Vector(b)) for a, b in original]
    margin = max(body['length']*1e-6, min(body['radius']*.02, body['length']*.001))
    # Rest coordinates are stored as Blender floats.  A straight chain can
    # accumulate a few ulps at each joint; allow that representation noise,
    # while remaining far below a visible mesh or bone edit.
    tolerance = max(1e-7, body['length']*1e-5)
    def name(index): return bone_names[index] if index < len(bone_names) else f'Bone {index+1}'
    for index, p in enumerate(points[1:-1]):
        if not volume.inside(p) or volume.distance(p) < margin:
            raise ValueError(f'{name(index)} / {name(index+1)}: marked joint is outside the current finger interior.')
    for i, (a, b) in enumerate(zip(points, points[1:])):
        length = (b-a).length
        if length < max(EPS, body['length']*1e-5):
            raise ValueError(f'{name(i)}: the marked loops would collapse this bone.')
        if _covered_span(a, b, original, tolerance): continue
        current = volume
        if i == 0:
            try: current = _root_volume(bm, body, a, margin, volume)
            except ValueError as exc: raise ValueError(f'{name(i)}: {exc}') from exc
        start, end = a.copy(), b.copy()
        for which, endpoint in ((0, a), (1, b)):
            boundary = i == 0 and which == 0 or i == len(points)-2 and which == 1
            distance = current.distance(endpoint)
            if boundary and distance is not None and distance <= margin*1.1:
                inset = endpoint.lerp(b if which == 0 else a, min(.01, margin*2/length))
                if not current.inside(inset):
                    raise ValueError(f'{name(i)}: fixed endpoint points outside the current finger surface.')
                if which == 0: start = inset
                else: end = inset
        if current.certify((start, end), margin*.5) is None:
            raise ValueError(f'{name(i)}: planned segment leaves the verified finger interior.')


def align(context):
    """Move only active-side joints; Armature X Mirror does not broaden scope."""
    obj, key = _active(context)
    owner, rig = targets.owner(context)
    if owner != obj: raise ValueError('Use the captured Body mesh for this Main Rig.')
    symmetry.guard_rig(rig)
    if definition._key_name(obj) != definition._basis_name(obj):
        raise ValueError('Select Basis before aligning rest joints.')
    marked = records(obj, key)
    if not all(isinstance(marked.get(str(i)), dict) for i in (1, 2)):
        raise ValueError('Mark two joint loops first.')
    plan = dict(obj=obj, rig=rig, keys=[], chains=[])
    with definition.FrameReader() as reader:
        bm = reader.snapshot(obj, definition._basis_name(obj))
        reference = _reference(obj, key, reader)
        current, loops = [], []
        for number in (1, 2):
            old = marked[str(number)]
            if old.get('basis_key') != definition._basis_name(obj) or old.get('key') != key:
                raise ValueError('A marked loop reference changed; mark that loop again.')
            vertices = _recover(bm, old, number)
            _ring(obj, bm, reference, vertices)
            current.append(_marker(obj, key, vertices, reference))
            loops.append(vertices)
        if current[1]['fraction']-current[0]['fraction'] <= 1e-5:
            raise ValueError('Select both joint loops and mark them again.')
        reference = _fresh_reference(bm, loops, reference)
        candidates = targets.index(rig)
        source = targets.resolve(obj, rig, key, candidates, live_mark_reference=reference)
        if len(source) != 3:
            raise ValueError('Two joint marks need an existing three-bone finger chain.')
        original_nodes = chain._nodes(source)
        to_mesh = obj.matrix_world.inverted() @ rig.matrix_world
        mesh_nodes = [to_mesh @ point for point in original_nodes]
        proposed = _joint_nodes(key, mesh_nodes, current)
        transform = rig.matrix_world.inverted() @ obj.matrix_world
        nodes = [transform @ point for point in proposed]
        nodes[0], nodes[-1] = original_nodes[0], original_nodes[-1]
        _certify(obj, rig, reference, nodes, bm, original_nodes=original_nodes,
                 original_segments=[(chain._head(bone), chain._tail(bone)) for bone in source],
                 bone_names=[bone.name for bone in source])
        plan['keys'].append(key)
        plan['chains'].append(dict(key=key, names=[b.name for b in source], nodes=nodes,
                                   expected=chain._rest(source)))
    return chain._run(context, plan)
