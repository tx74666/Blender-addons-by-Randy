"""Read-only capped-sleeve census, geometric five-digit ordering and pairing."""
import math

from mathutils import Vector
from mathutils.kdtree import KDTree

from . import finger_range

DIGITS = ('THUMB', 'INDEX', 'MIDDLE', 'RING', 'PINKY')
LABELS = dict(zip(DIGITS, ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky')))


def census(bm, quad_band):
    """Find regular protrusions; stop at their palm transition, don't remesh it."""
    attempted, signatures, cap_tags, result = set(), set(), set(), []
    for face in bm.faces:
        if face.hide or len(face.verts) != 4: continue
        for edge in list(face.edges)[:2]:
            if (face.index, edge.index) in attempted: continue
            try: rows, used = finger_range._sleeve(bm, face, edge, quad_band)
            except ValueError: continue
            signature = frozenset(f.index for f in used)
            if signature in signatures: continue
            signatures.add(signature)
            row_edges = {bm.edges.get((r[i], r[(i+1) % len(r)])) for r in rows for i in range(len(r))}
            attempted.update((f.index, e.index) for f in used for e in f.edges if e in row_edges)
            first = finger_range._end_cap(list(reversed(rows)), used)
            last = finger_range._end_cap(rows, used)
            if (first is None) == (last is None): continue
            if first is not None: rows.reverse()
            cap, cap_verts, direction = first or last
            while len(rows) > 4:
                a, b = finger_range._center(rows[0]), finger_range._center(rows[1])
                if max((v.co-a).length for v in rows[0]) <= max((v.co-b).length for v in rows[1])*1.8: break
                rows.pop(0)
            included = {v for row in rows for v in row}
            used = {f for f in used if set(f.verts) <= included}
            if any(len(v.link_edges) != 4 or set(v.link_faces)-used for r in rows[1:-1] for v in r): continue
            cap_tag = frozenset(f.index for f in cap)
            if cap_tag in cap_tags: continue
            centers = [finger_range._center(r) for r in rows]
            length = sum((b-a).length for a, b in zip(centers, centers[1:]))
            radius = max((v.co-centers[0]).length for v in rows[0])
            if radius <= 1e-9 or length < radius*2: continue
            cap_tags.add(cap_tag)
            faces = used | cap
            result.append({'rings': [[v.index for v in r] for r in rows],
                           'faces': sorted(f.index for f in faces),
                           'vertices': sorted({v.index for f in faces for v in f.verts}),
                           'root': list(centers[0]),
                           'tip': list(centers[-1]+direction*max((v.co-centers[-1]).dot(direction) for v in cap_verts)),
                           'length': length, 'radius': radius})
    return result


def owner(candidates, vertices):
    chosen = set(vertices)
    hits = [i for i, c in enumerate(candidates) if chosen & set(c['vertices'])]
    if len(hits) != 1:
        raise ValueError('Selection must touch exactly one detected finger, not another finger or only the palm.')
    return hits[0]


def order_hand(candidates):
    """Four approximately aligned roots plus a distinct thumb; view-independent."""
    import numpy as np
    if len(candidates) != 5: raise ValueError('Five distinct finger bodies are needed to establish their order.')
    roots = np.array([c['root'] for c in candidates])
    dirs = [(Vector(c['tip'])-Vector(c['root'])).normalized() for c in candidates]
    options = []
    for thumb in range(5):
        ids = [i for i in range(5) if i != thumb]
        points = roots[ids]
        center = points.mean(axis=0)
        values, axes = np.linalg.eigh((points-center).T @ (points-center))
        if values[-1] < 1e-15: continue
        mean_dir = sum((dirs[i] for i in ids), Vector()).normalized()
        scatter = sum(1-max(-1., min(1., d.dot(mean_dir))) for i, d in enumerate(dirs) if i in ids)/4
        score = float((values[0]+values[1])/values[-1]) + scatter*2
        axis = Vector(axes[:, -1])
        toward_thumb = Vector(roots[thumb]-center).dot(axis)
        if abs(toward_thumb) < math.sqrt(values[-1])*.15: continue
        if toward_thumb < 0: axis.negate()
        ordered = sorted(ids, key=lambda i: -Vector(roots[i]-center).dot(axis))
        # A thumb that is parallel to the four fingers AND not proximally offset
        # isn't distinguishable from five identical tubes. Never name by index.
        distinct = 1-dirs[thumb].dot(mean_dir)
        proximal = -Vector(roots[thumb]-center).dot(mean_dir)/math.sqrt(values[-1])
        if distinct < .06 and proximal < .18: continue
        options.append((score, [thumb]+ordered))
    options.sort(key=lambda x: x[0])
    if not options or (len(options) > 1 and options[1][0]-options[0][0] < max(.025, options[0][0]*.5)):
        raise ValueError('Five-finger order is ambiguous. Spread the fingers/rest mesh enough to distinguish the thumb; no identities were overwritten.')
    return dict(zip(DIGITS, (candidates[i] for i in options[0][1])))


def reflect(point, to_plane):
    p = to_plane @ Vector(point)
    p.x = -p.x
    return to_plane.inverted() @ p


def detect(bm, quad_band, selected, to_plane):
    candidates = census(bm, quad_band)
    seed_index = owner(candidates, selected)
    seed = candidates[seed_index]
    sign = 1 if (to_plane @ Vector(seed['root'])).x > 0 else -1
    same_side = [c for c in candidates if (to_plane @ Vector(c['root'])).x*sign > 0]
    same_side.sort(key=lambda c: (Vector(c['root'])-Vector(seed['root'])).length)
    if len(same_side) < 5: raise ValueError('Cannot establish five finger identities on this hand. No saved definitions were replaced.')
    hand = same_side[:5]
    reach = max(c['length'] for c in hand)*2
    if any((Vector(c['root'])-Vector(seed['root'])).length > reach for c in hand):
        raise ValueError('Five nearby fingers were not found around this selection.')
    if len(same_side) > 5 and (Vector(same_side[5]['root'])-Vector(seed['root'])).length < (Vector(hand[-1]['root'])-Vector(seed['root'])).length*1.25:
        raise ValueError('More than five nearby protrusions could belong to this hand. Detection is ambiguous.')
    ordered = order_hand(hand)
    source_side, opposite = ('L', 'R') if sign > 0 else ('R', 'L')
    result = {f'{digit}.{source_side}': c for digit, c in ordered.items()}
    used = set()
    for digit, c in ordered.items():
        root, tip = reflect(c['root'], to_plane), reflect(c['tip'], to_plane)
        options = sorted(((Vector(t['root'])-root).length+(Vector(t['tip'])-tip).length, i, t)
                         for i, t in enumerate(candidates) if (to_plane @ Vector(t['root'])).x*sign < 0)
        if not options: continue
        score, i, mate = options[0]
        if score > c['length']*.55 or i in used: continue
        if len(options) > 1 and options[1][0] < score*1.8+c['radius']*.25: continue
        used.add(i)
        result[f'{digit}.{opposite}'] = mate
    return result


def vertex_map(bm, source, target, to_plane, *, mirror=True):
    if len(source['vertices']) != len(target['vertices']) or len(source['rings']) != len(target['rings']):
        raise ValueError('Opposite finger has different topology / ring count.')
    tree = KDTree(len(target['vertices']))
    for vi in target['vertices']: tree.insert(bm.verts[vi].co, vi)
    tree.balance()
    tolerance = max(source['length']*1e-4, 1e-7)
    mapping = {}
    for vi in source['vertices']:
        point = reflect(bm.verts[vi].co, to_plane) if mirror else bm.verts[vi].co
        _, match, distance = tree.find(point)
        if distance > tolerance: raise ValueError('Opposite finger geometry is not symmetric.')
        mapping[vi] = match
    if len(set(mapping.values())) != len(mapping): raise ValueError('Opposite vertex correspondence is ambiguous.')
    def cycle(ids):
        ids = tuple(ids)
        return min(ids[i:]+ids[:i] for i in range(len(ids)))
    target_faces = {cycle(v.index for v in bm.faces[i].verts) for i in target['faces']}
    mapped_faces = [[mapping[v.index] for v in bm.faces[i].verts] for i in source['faces']]
    if {cycle(reversed(f) if mirror else f) for f in mapped_faces} != target_faces:
        raise ValueError('Opposite face connections / orientation differ.')
    target_rings = [frozenset(r) for r in target['rings']]
    if [frozenset(mapping[i] for i in r) for r in source['rings']] != target_rings:
        raise ValueError('Opposite loops do not correspond.')
    return mapping
