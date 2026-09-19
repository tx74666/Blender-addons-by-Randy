"""A selected surface defines the root, not a prescribed palm junction pattern.

Discovery is read-only. Only a unique tube with a small closed distal cap and a
continuing proximal surface is accepted; the irregular root is never rebuilt.
"""
from collections import deque

from mathutils import Vector


def _center(row):
    return sum((v.co for v in row), Vector()) / len(row)


def _extend(bm, rows, used, quad_band):
    for _ in range(128):
        row = rows[-1]
        edge = bm.edges.get((row[0], row[1]))
        choices = [f for f in edge.link_faces if f not in used]
        if len(choices) != 1 or any(len(v.link_edges) != 4 for v in row): break
        try: root, mapping, band = quad_band(choices[0], edge)
        except ValueError: break
        if set(root) != set(row) or band & used: break
        next_row = [mapping[v] for v in row]
        if set(next_row) & {v for r in rows for v in r}: break
        a, b, c = _center(rows[-2]), _center(row), _center(next_row)
        # A longitudinal path must not turn back into the palm/cap.
        if (b-a).normalized().dot((c-b).normalized()) < .3: break
        radius = max((v.co-b).length for v in row)
        if max((v.co-c).length for v in next_row) > radius*1.8: break
        rows.append(next_row)
        used.update(band)


def _sleeve(bm, face, edge, quad_band):
    root, mapping, used = quad_band(face, edge)
    rows = [root, [mapping[v] for v in root]]
    _extend(bm, rows, used, quad_band)
    rows.reverse()
    _extend(bm, rows, used, quad_band)
    if len(rows) < 4: raise ValueError('Too few regular cross-sections.')
    return rows, used


def _end_cap(rows, used):
    """Small closed component beyond one end; open/hidden ends are not tips."""
    row, previous = rows[-1], rows[-2]
    border = set(row)
    todo = [f for v in row for f in v.link_faces if f not in used]
    seen = set()
    while todo:
        face = todo.pop()
        if face in seen: continue
        seen.add(face)
        if face.hide or len(seen) > max(32, len(row)*4): return None
        for edge in face.edges:
            if len(edge.link_faces) != 2: return None
            todo.extend(f for f in edge.link_faces if f not in used and f not in seen)
    if not seen: return None
    center = _center(row)
    direction = (center-_center(previous)).normalized()
    radius = max((v.co-center).length for v in row)
    verts = {v for f in seen for v in f.verts}
    # Other points of the tube would mean that the two ends reconnect outside.
    if (verts-border) & {v for r in rows[:-1] for v in r}: return None
    for v in verts:
        delta = v.co-center
        along = delta.dot(direction)
        if along < -radius*.6 or along > radius*3: return None
        if (delta-direction*along).length > radius*1.8: return None
    return seen, verts, direction


def discover(bm, selected, quad_band, world):
    if not selected:
        raise ValueError('Select a connected root surface, optionally continuing toward the fingertip.')
    chosen = set(selected)
    visited, todo = set(), [selected[0]]
    while todo:
        face = todo.pop()
        if face in visited: continue
        visited.add(face)
        todo.extend(f for e in face.edges for f in e.link_faces if f in chosen and f not in visited)
    if visited != chosen:
        raise ValueError('Select one connected surface on one finger, not separate patches.')

    # Search only a few face steps beyond the supplied root. Do not scan the
    # whole character and choose a globally nearest, possibly unrelated finger.
    distance = {f: 0 for f in selected}
    queue = deque(selected)
    while queue:
        face = queue.popleft()
        if distance[face] == 3: continue
        for edge in face.edges:
            for other in edge.link_faces:
                if not other.hide and other not in distance:
                    distance[other] = distance[face]+1
                    queue.append(other)
    candidates, signatures, attempted = [], set(), set()
    for face in sorted(distance, key=lambda f: (distance[f], f.index)):
        if len(face.verts) != 4: continue
        for edge in list(face.edges)[:2]:
            key = (face.index, tuple(sorted(v.index for v in edge.verts)))
            if key in attempted: continue
            try: rows, used = _sleeve(bm, face, edge, quad_band)
            except ValueError: continue
            signature = frozenset(f.index for f in used)
            if signature in signatures: continue
            signatures.add(signature)
            # Mark both orientations of all cross edges of this discovered tube.
            row_edges = {bm.edges.get((r[i], r[(i+1) % len(r)])) for r in rows for i in range(len(r))}
            attempted.update((f.index, tuple(sorted(v.index for v in e.verts)))
                             for f in used for e in f.edges if e in row_edges)
            cap_a, cap_b = _end_cap(list(reversed(rows)), used), _end_cap(rows, used)
            if (cap_a is None) == (cap_b is None): continue
            if cap_a is not None: rows.reverse()
            cap_faces, cap_verts, direction = cap_a if cap_a is not None else cap_b
            # Root selection must meet this finger (or be within the local root
            # neighborhood). More than one credible tube is deliberately refused.
            near = min(distance.get(f, 99) for f in used)
            center = _center(rows[0])
            root_direction = (_center(rows[1])-center).normalized()
            selected_verts = {v for f in selected for v in f.verts}
            projected = [(v.co-center).dot(root_direction) for v in selected_verts]
            extent = (_center(rows[-1])-center).length
            radius = max((v.co-center).length for v in rows[0])
            if min(projected) < -max(extent*.8, radius*3): continue
            # Reject selections extending into an adjacent finger or wide palm.
            if any(((v.co-center)-root_direction*(v.co-center).dot(root_direction)).length
                   > radius*2.5 for v in selected_verts): continue
            candidates.append((near, -len(chosen & used), rows, used, cap_faces, cap_verts, direction))
    if not candidates:
        raise ValueError('No unique capped finger found. Select the root and a few faces along that finger; open or ambiguous ends are not guessed.')
    candidates.sort(key=lambda c: c[:2])
    best = candidates[0]
    if len(candidates) > 1 and candidates[1][0] <= best[0]+1:
        raise ValueError('The surface reaches more than one possible finger. Narrow the selection to one root and its finger body.')
    _, _, rows, used, cap_faces, cap_verts, direction = best
    # Every interior ring must remain an ordinary sleeve, even when the selected
    # definition surface contains triangles, poles, or a palm fan.
    if any(len(v.link_edges) != 4 or set(v.link_faces)-used for r in rows[1:-1] for v in r):
        raise ValueError('The editable finger body has branches; its root can be marked but not rebuilt safely.')
    centers = [_center(row) for row in rows]
    forward = (centers[1]-centers[0]).normalized()
    selected_verts = {v for f in selected for v in f.verts}
    root_distance = min((v.co-centers[0]).dot(forward) for v in selected_verts)
    # If the selection starts partway along the body, it defines a shorter
    # range; do not silently expand the root back into unselected anatomy.
    root = centers[0]+forward*root_distance
    tip = centers[-1]+direction*max((v.co-centers[-1]).dot(direction) for v in cap_verts)
    root_offset = (world.to_3x3() @ (centers[0]-root)).length
    if root_distance > 0: root_offset *= -1
    distances = [root_offset]
    for a, b in zip(centers, centers[1:]):
        distances.append(distances[-1]+(world.to_3x3() @ (b-a)).length)
    total = distances[-1]+(world.to_3x3() @ (tip-centers[-1])).length
    if total <= 1e-8 or min(b-a for a, b in zip(distances, distances[1:])) < total*1e-5:
        raise ValueError('The finger length or cross-section spacing is collapsed.')
    # Fixed real rings bracket the editable interval; the root itself need not
    # have a real edge loop. Never drag vertices from before the chosen start.
    first = next((i for i, d in enumerate(distances) if d >= -total*1e-5), len(rows))
    rows, distances = rows[first:], distances[first:]
    if len(rows) < 4: raise ValueError('Select an earlier root: fewer than three safe finger bands remain.')
    return {'schema': 2, 'rings': [[v.index for v in row] for row in rows],
            'positions': [d/total for d in distances], 'root': list(root), 'tip': list(tip),
            'length': total, 'selection': sorted(f.index for f in selected)}
