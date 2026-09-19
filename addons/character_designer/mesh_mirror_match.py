"""Topology-independent evidence for ONE whole region, never a union of strands.

Area-uniform surface samples avoid bias from dense/damaged tessellation. Distances
are bidirectional; longitudinal cross-sections test distributed agreement and
smoothly varying offsets. Scores rank evidence, not statistical probabilities.
"""
from dataclasses import dataclass
import math
from statistics import median

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import tessellate_polygon


@dataclass(frozen=True)
class Evidence:
    distance: float
    score: float
    coverage: tuple
    profile_error: float
    plausible: bool
    confident: bool


def _quantile(values, fraction):
    ordered = sorted(values)
    return ordered[min(int((len(ordered) - 1) * fraction), len(ordered) - 1)] if ordered else float('inf')


def _samples(points, polygons, count=384):
    triangles, areas = [], []
    for polygon in polygons:
        polygon_points = [points[i] for i in polygon]
        for triangle in tessellate_polygon([polygon_points]):
            a, b, c = (tuple(polygon_points[i] for i in triangle)
                       if isinstance(triangle[0], int) else triangle)
            area = (b-a).cross(c-a).length * .5
            if area > 1e-14:
                triangles.append((a, b, c))
                areas.append(area)
    total = sum(areas)
    if not total:
        return list(points), 0.0
    output, ti, accumulated = [], 0, areas[0]
    for index in range(count):
        position = total * (index + .5) / count
        while position > accumulated and ti < len(areas) - 1:
            ti += 1
            accumulated += areas[ti]
        # Deterministic low-discrepancy barycentric samples, not vertex IDs.
        u = math.sqrt(((index + .5) * .6180339887498949) % 1)
        v = ((index + .5) * .4142135623730951) % 1
        a, b, c = triangles[ti]
        output.append((1-u)*a + u*(1-v)*b + u*v*c)
    return output, total


def _principal_axis(points):
    center = sum(points, Vector()) / len(points)
    centered = [p-center for p in points]
    covariance = [[sum(p[i]*p[j] for p in centered) for j in range(3)] for i in range(3)]
    axis = Vector(tuple(1 if i == max(range(3), key=lambda a: covariance[a][a]) else 0 for i in range(3)))
    for _ in range(24):
        product = Vector(tuple(sum(covariance[i][j]*axis[j] for j in range(3)) for i in range(3)))
        if product.length < 1e-20:
            break
        axis = product.normalized()
    return axis


def _profile(samples, axis, start, length, count=10):
    bins = [[] for _ in range(count)]
    for point in samples:
        fraction = (point.dot(axis)-start) / length
        if -.025 <= fraction <= 1.025:
            bins[min(count-1, max(0, int(fraction*count)))].append(point)
    return {i: sum(points, Vector())/len(points) for i, points in enumerate(bins) if len(points) >= 3}


class RegionMatcher:
    def __init__(self, points, polygons, tolerance):
        self.points, self.polygons = points, polygons
        self.tree = BVHTree.FromPolygons(points, polygons)
        self.samples, self.area = _samples(points, polygons)
        self.axis = _principal_axis(self.samples)
        self.start = min(p.dot(self.axis) for p in points)
        self.length = max(p.dot(self.axis) for p in points) - self.start
        self.span = max((Vector(tuple(max(p[i] for p in points) for i in range(3)))
                         - Vector(tuple(min(p[i] for p in points) for i in range(3)))).length, 1e-7)
        self.length = max(self.length, self.span*.01)
        # Length-scaled thresholds allow a smoothly deformed counterpart while
        # still requiring agreement over substantial, distributed surface area.
        # Seam tolerance controls welding only; increasing it must not authorize
        # deletion of a more distant strand.
        self.profile = _profile(self.samples, self.axis, self.start, self.length)
        radii = []
        for point in self.samples:
            index = min(9, max(0, int((point.dot(self.axis)-self.start)/self.length*10)))
            if index in self.profile:
                delta = point-self.profile[index]
                radii.append((delta-self.axis*delta.dot(self.axis)).length)
        width = median(radii)*2 if radii else self.span
        # Long, thin hair must not gain a huge matching radius simply because
        # it is long. Measure cross-section width as well as length.
        self.near = min(self.span*.06, max(self.span*.015, width*.65))

    def compare(self, points, polygons):
        samples, area = _samples(points, polygons)
        if not polygons or not area:
            # A loose remnant can be selected explicitly, but never auto-deleted
            # based on too little evidence. It still prevents an unsafe append.
            distances = [self.tree.find_nearest(p)[3] for p in points]
            close = sum(d is not None and d <= self.near*2 for d in distances) / max(1, len(points))
            return Evidence(max((d for d in distances if d is not None), default=float('inf')),
                            float('inf'), (0.0, close), float('inf'), close >= .5, False)
        tree = BVHTree.FromPolygons(points, polygons)
        forward = [tree.find_nearest(p)[3] for p in self.samples]
        reverse = [self.tree.find_nearest(p)[3] for p in samples]
        if any(d is None for d in forward + reverse):
            return Evidence(float('inf'), float('inf'), (0, 0), float('inf'), True, False)
        coverage = (sum(d <= self.near for d in forward)/len(forward),
                    sum(d <= self.near for d in reverse)/len(reverse))
        loose_coverage = (sum(d <= self.near*2 for d in forward)/len(forward),
                          sum(d <= self.near*2 for d in reverse)/len(reverse))
        profile = _profile(samples, self.axis, self.start, self.length)
        deltas = {}
        for index in self.profile.keys() & profile.keys():
            delta = profile[index] - self.profile[index]
            deltas[index] = delta - self.axis*delta.dot(self.axis)
        profile_error = _quantile([d.length for d in deltas.values()], .8)
        bends = [(deltas[i-1]-2*deltas[i]+deltas[i+1]).length for i in deltas if i-1 in deltas and i+1 in deltas]
        continuity = median(bends) if bends else profile_error
        populated = len(deltas) / max(1, len(self.profile))
        projections = [p.dot(self.axis) for p in points]
        overlap = max(0, min(max(projections), self.start+self.length) - max(min(projections), self.start)) / self.length
        extent_ratio = (max(projections)-min(projections)) / self.length
        area_ratio = area / max(self.area, 1e-20)
        plausible = (loose_coverage[0] >= .20 and loose_coverage[1] >= .85
                     and populated >= .30 and overlap >= .25 and .20 <= extent_ratio <= 2.0
                     and profile_error <= self.span*.16 and .08 <= area_ratio <= 3.0)
        confident = (plausible and coverage[0] >= .48 and coverage[1] >= .85
                     and populated >= .5 and overlap >= .48 and .48 <= extent_ratio <= 1.35
                     and _quantile(reverse, .9) <= self.near*1.25
                     and profile_error <= self.span*.08 and continuity <= self.span*.04
                     and .22 <= area_ratio <= 1.8)
        if min(coverage) >= .98 and max(_quantile(forward, .95), _quantile(reverse, .95)) <= self.near*.1:
            # Near-identical surfaces should not fail because a triangulation
            # produces slightly different section-sampling centroids.
            plausible = confident = True
        # Trim the forward tail: a deleted tip/hole should not dominate the
        # whole score. Coverage and extent independently limit missing evidence.
        score = ((_quantile(forward, .65) + _quantile(reverse, .8)) / self.near
                 + profile_error/self.near*.5 + continuity/self.near*.25
                 + (2-sum(coverage))*.8)
        return Evidence(max(forward+reverse), score, coverage, profile_error, plausible, confident)


def choose_unique(candidates):
    """A clear winner must be strong AND separated from all plausible rivals."""
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda c: c.score)
    if any(not math.isfinite(candidate.score) for candidate in ranked):
        return None
    best = ranked[0]
    if not best.automatic:
        return None
    if len(ranked) > 1 and ranked[1].score - best.score < max(.65, best.score*.75):
        return None
    return best
