"""Conservative straight internal axes; no scene data or surface offsets.

Search is bounded to the detected longitudinal direction and selected span.
The regular sleeve uses a virtual root cap; explicit proximal selections use
the actual closed connected shell instead. Both certify the whole segment.
"""
import math
import statistics

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import tessellate_polygon


def _component(bm, candidate):
    todo, found = [bm.faces[candidate['faces'][0]]], set()
    while todo:
        face = todo.pop()
        if face in found: continue
        found.add(face)
        todo.extend(f for edge in face.edges for f in edge.link_faces if f not in found)
    return found


def prepare_body(bm, candidate, range_path):
    """Keep the regular sleeve for identification; extend only its proof domain.

    An explicit root selection can precede the first regular ring. In that case
    the real, closed connected surface supplies containment, never a synthesized
    extrusion of that ring. The requested span still bounds every tested segment.
    """
    body = dict(candidate)
    centers, main, _, _, root_width, _, _ = geometry(bm, candidate)
    root_z = centers[0].dot(main)
    start_z = Vector(range_path[0]).dot(main)
    extra = root_z-start_z
    if extra <= root_width*.05: return body
    if extra > min(candidate['length']*.6, root_width*2):
        raise ValueError('The selected start extends beyond the local finger-root transition. Select a start nearer the knuckle; the saved range was not shortened.')
    body['root_extension'] = True
    # The regular body and actual selected root faces are reference evidence.
    # The connected shell is rebuilt/verified live, not frozen into all five
    # records (otherwise editing one finger would invalidate all the others).
    return body


def support_faces(body):
    return sorted(set(body['faces']))


class Volume:
    def __init__(self, bm, candidate):
        extended = candidate.get('root_extension', False)
        ids = {f.index for f in _component(bm, candidate)} if extended else set(candidate['faces'])
        local_support = set(support_faces(candidate))
        directed = {}
        for i in ids:
            face = bm.faces[i]
            if face.hide and i in local_support: raise ValueError('Internal guide: a supporting finger face is hidden.')
            for loop in face.loops:
                a, b = loop.vert.index, loop.link_loop_next.vert.index
                directed.setdefault(tuple(sorted((a, b))), []).append((a, b))
        boundary = {e for e, directions in directed.items() if len(directions) == 1}
        root = candidate['rings'][0][:]
        expected = set() if extended else {tuple(sorted((v, root[(i+1) % len(root)]))) for i, v in enumerate(root)}
        if boundary != expected:
            raise ValueError('Internal guide: the connected root surface is open; its interior cannot be verified.' if extended else 'Internal guide: the finger body has a hole or an extra boundary.')
        for directions in directed.values():
            if len(directions) != 1 and (len(directions) != 2 or directions[0] != directions[1][::-1]):
                raise ValueError('Internal guide: inconsistent or non-manifold finger faces.')
        if not extended and directed[tuple(sorted(root[:2]))][0] == tuple(root[:2]): root.reverse()
        triangles = [tuple(loop.vert.co.copy() for loop in tri)
                     for tri in bm.calc_loop_triangles() if tri[0].face.index in ids]
        if not extended:
            root_points = [bm.verts[i].co.copy() for i in root]
            triangles += [tuple(root_points[p].copy() if isinstance(p, int) else p.copy() for p in tri)
                          for tri in tessellate_polygon([root_points])]
        scale = candidate['length']
        if not triangles or any((b-a).cross(c-a).length < scale*scale*1e-12 for a, b, c in triangles):
            raise ValueError('Internal guide: a supporting triangle is collapsed.')
        self.triangles = triangles
        vertices = [p for tri in triangles for p in tri]
        self.tree = BVHTree.FromPolygons(vertices, [tuple(range(i, i+3)) for i in range(0, len(vertices), 3)], all_triangles=True)

    def distance(self, point):
        return self.tree.find_nearest(point)[3]

    def inside(self, point):
        total = 0.
        for triangle in self.triangles:
            a, b, c = (p-point for p in triangle)
            denominator = a.length*b.length*c.length+a.dot(b)*c.length+b.dot(c)*a.length+c.dot(a)*b.length
            total += 2*math.atan2(a.dot(b.cross(c)), denominator)
        # A single closed, consistently oriented volume has winding +/-1.
        return abs(abs(total)-4*math.pi) < .02

    def certify(self, path, margin):
        a, b = map(Vector, path)
        if not self.inside((a+b)*.5): return None
        pending, lower = [(a, b, 0)], float('inf')
        while pending:
            a, b, depth = pending.pop()
            mid, half = (a+b)*.5, (b-a).length*.5
            distance = self.distance(mid)
            if distance is None or distance < margin: return None
            # Every point in this interval lies in an interior ball with at
            # least this clearance. The connected certified balls cover [A,B].
            bound = distance-half
            if bound >= margin:
                lower = min(lower, bound)
                continue
            if depth >= 16: return None
            pending.extend(((a, mid, depth+1), (mid, b, depth+1)))
        return lower


def geometry(bm, candidate):
    centers = [sum((bm.verts[i].co for i in row), Vector())/len(row) for row in candidate['rings']]
    main = centers[-1]-centers[0]
    if main.length < candidate['length']*.65:
        raise ValueError('Internal guide: this finger bends too far for a full-range straight axis.')
    main.normalize()
    radial = bm.verts[candidate['rings'][0][0]].co-centers[0]
    u = radial-main*radial.dot(main)
    if u.length < candidate['radius']*.01: raise ValueError('Internal guide: the root cross-section is collapsed.')
    u.normalize()
    v = main.cross(u).normalized()
    widths = []
    for row in candidate['rings']:
        spans = []
        for i in range(12):
            direction = u*math.cos(i*math.pi/12)+v*math.sin(i*math.pi/12)
            values = [bm.verts[j].co.dot(direction) for j in row]
            spans.append(max(values)-min(values))
        widths.append(min(spans))
    # Exclude the last, possibly collapsed tip ring. Use stable body sections.
    root_width = statistics.median(widths[:min(3, len(widths)-1)])
    tip_width = statistics.median(widths[max(0, len(widths)-4):-1])
    thickness = min(root_width, tip_width)
    if thickness < candidate['length']*1e-5: raise ValueError('Internal guide: no stable finger thickness could be measured.')
    return centers, main, u, v, root_width, tip_width, thickness


def surface_centering(bm, candidate, spec):
    """A narrow longitudinal selection defines lateral position, not depth.

    Use equal-weight regular cross-sections, not cap/root faces or face area:
    large knuckle polygons and terminal wrap must not tilt the top reference.
    A transverse loop / broad selection supplies no longitudinal center plane.
    """
    if spec['kind'] not in {'FACES', 'EDGES'}: return None
    seq = bm.faces if spec['kind'] == 'FACES' else bm.edges
    selected = {v.index for i in spec['ids'] for v in seq[i].verts}
    centers, main, _, _, _, _, thickness = geometry(bm, candidate)
    points, sides = [], []
    for row, center in zip(candidate['rings'][:-1], centers[:-1]):
        hits = [i for i in row if i in selected]
        if not hits: continue
        if len(hits) == 2:
            a, b = hits
            if (row.index(a)-row.index(b)) % len(row) not in (1, len(row)-1): return None
            across = bm.verts[b].co-bm.verts[a].co
        elif len(hits) == 1:
            j = row.index(hits[0])
            across = bm.verts[row[(j+1) % len(row)]].co-bm.verts[row[j-1]].co
        else: return None
        point = sum((bm.verts[i].co for i in hits), Vector())/len(hits)
        across -= main*across.dot(main)
        if across.length < thickness*1e-4: return None
        across.normalize()
        if across.dot(main.cross(point-center)) < 0: across.negate()
        points.append(point)
        sides.append(across)
    if len(points) < 3: return None
    side = sum(sides, Vector())
    if side.length < len(sides)*.9: return None
    side.normalize()
    if any(s.dot(side) < .8 for s in sides): return None
    normal = side.cross(main).normalized()
    center = sum(points, Vector())/len(points)
    ts = [(p-center).dot(main) for p in points]
    if max(ts)-min(ts) < thickness: return None
    slope = sum((t*(p-center).dot(side) for p, t in zip(points, ts)))/sum(t*t for t in ts)
    direction = main+side*slope
    if direction.normalized().dot(main) < .97: return None
    return {'path': [list(center+direction*min(ts)), list(center+direction*max(ts))],
            'normal': list(normal)}


def solve(bm, candidate, range_path, centering=None):
    centers, main, u, v, root_width, tip_width, thickness = geometry(bm, candidate)
    points = list(map(Vector, range_path))
    lo, hi = points[0].dot(main), points[-1].dot(main)
    span = hi-lo
    if span <= thickness*.5: raise ValueError('Internal guide: the selected longitudinal range is too short.')
    volume = Volume(bm, candidate)
    center = sum(centers, Vector())/len(centers)
    ts = [(p-center).dot(main) for p in centers]
    variance = sum(t*t for t in ts)
    fitted = sum(((p-center)*t for p, t in zip(centers, ts)), Vector())/variance
    fitted.normalize()
    if fitted.dot(main) < .97: fitted = main
    directions = [main, fitted]
    directions += [(fitted+u*a+v*b).normalized() for a, b in ((-.05, 0), (.05, 0), (0, -.05), (0, .05))]
    alignment = None
    if centering:
        p, q = map(Vector, centering['path'])
        normal = Vector(centering['normal'])
        side = main.cross(normal).normalized()
        normal = side.cross(main).normalized()
        delta = q-p
        if abs(delta.dot(main)) < thickness*.5:
            raise ValueError('The saved surface centerline no longer follows this finger; recapture it.')
        slope = delta.dot(side)/delta.dot(main)
        target = p+main*(center-p).dot(main)+side*(slope*(center-p).dot(main))
        anchor = center+side*(target-center).dot(side)
        alignment = (side, normal, slope, anchor)
    margin = thickness*.04
    # Preserve longitudinal coverage first. Never shrink past these small
    # automatic endpoint margins, and never accept less than 85% of the span.
    for retreat in (.10, .125, .15):
        start, end = lo+root_width*retreat, hi-tip_width*retreat
        if end-start < span*.85: continue
        options = []
        candidates = [(direction, center+(u*x+v*y)*thickness)
                      for direction in directions
                      for x in (-.30, -.15, 0., .15, .30)
                      for y in (-.30, -.15, 0., .15, .30)]
        if alignment:
            side, normal, slope, anchor = alignment
            depth_slope = fitted.dot(normal)/fitted.dot(main)
            # Exact surface-center alignment in the lateral direction; solve
            # depth independently. Small lateral fallbacks are still ranked
            # by deviation and may only pass the same full-volume certificate.
            candidates += [((main+side*slope+normal*d).normalized(), anchor+(normal*z+side*x)*thickness)
                           for d in (0., depth_slope, depth_slope-.05, depth_slope+.05)
                           for z in (-.45, -.30, -.15, 0., .15, .30, .45)
                           for x in (0., -.05, .05)]
        for direction, anchor in candidates:
            dot = direction.dot(main)
            if dot < .97: continue
            a = anchor+direction*((start-anchor.dot(main))/dot)
            b = anchor+direction*((end-anchor.dot(main))/dot)
            clear = min(volume.distance(a.lerp(b, i/16)) for i in range(17))
            if clear <= margin: continue
            stable = sum((p-(a+direction*(p-a).dot(direction))).length_squared for p in centers)
            deviation = 0.
            if alignment:
                side, _, slope, target = alignment
                deviation = max(abs((p-target).dot(side)-slope*(p-target).dot(main)) for p in (a, b))
            # Ignore float noise at an exactly aligned plane. Safety is still
            # certified before accepting any ranked candidate.
            rank = (round(deviation/thickness, 4), -round(clear/thickness, 4),
                    round(stable/(len(centers)*thickness*thickness), 4))
            options.append((rank, a, b))
        options.sort(key=lambda item: item[0])
        for _, a, b in options:
            certified = volume.certify((a, b), margin)
            if certified is not None:
                return {'path': [list(a), list(b)], 'margin': margin,
                        'clearance_lower_bound': certified, 'thickness': thickness,
                        'start_retreat': root_width*retreat, 'tip_retreat': tip_width*retreat,
                        'coverage': (end-start)/span,
                        'method': 'surface_centered_straight_v2' if alignment else 'certified_straight_v1'}
    raise ValueError('No straight axis safely covers the selected range with surface clearance. Try a shorter explicit range, or check the finger for a bend or narrow section.')


def verify(bm, candidate, internal):
    thickness = geometry(bm, candidate)[-1]
    certified = Volume(bm, candidate).certify(internal['path'], thickness*.04) if len(internal['path']) == 2 else None
    if certified is None:
        raise ValueError('The opposite internal straight axis does not have safe clearance in its actual finger body.')
    return {'margin': thickness*.04, 'thickness': thickness, 'clearance_lower_bound': certified}
