"""Explicit surface-based weight mirroring for unequal base-Mesh topology.

Strict group copying remains in weight_symmetry. This operation instead samples
the complete skin vector in a bone-defined region, preserving each destination
vertex's original total. It never interpolates geometry or uses the posed mesh.
"""

from dataclasses import dataclass
import hashlib
import math

import bpy
from bpy.props import FloatProperty
from bpy.types import Operator
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import weight_symmetry as ws


@dataclass(frozen=True)
class SurfacePlan:
    mesh_obj: object
    armature_obj: object
    source_names: tuple
    target_names: tuple
    source_side: int
    group_snapshot: tuple
    group_after: tuple
    active_group_index: int
    affected_indices: tuple
    sampled_count: int
    max_sample_distance: float
    distance_limit: float
    fingerprint: str


def _fingerprint(mesh_obj, armature_obj):
    geometry = (
        tuple(tuple(v.co) for v in mesh_obj.data.vertices),
        tuple(tuple(e.vertices) for e in mesh_obj.data.edges),
        tuple(tuple(p.vertices) for p in mesh_obj.data.polygons),
        tuple(tuple(row) for row in mesh_obj.matrix_world),
        tuple(tuple(row) for row in armature_obj.matrix_world),
        tuple((b.name, b.use_deform, tuple(tuple(r) for r in b.matrix_local))
              for b in armature_obj.data.bones),
    )
    return hashlib.sha256(repr(geometry).encode()).hexdigest()


def _barycentric(point, a, b, c):
    u, v, q = b - a, c - a, point - a
    uu, uv, vv = u.dot(u), u.dot(v), v.dot(v)
    qu, qv = q.dot(u), q.dot(v)
    denom = uu * vv - uv * uv
    if denom <= 1e-20:
        raise ws.WeightSymmetryError("Cannot sample a degenerate source triangle.")
    wb, wc = (vv * qu - uv * qv) / denom, (uu * qv - uv * qu) / denom
    weights = tuple(max(0.0, min(1.0, w)) for w in (1.0 - wb - wc, wb, wc))
    total = sum(weights)
    return tuple(w / total for w in weights)


def build_surface_plan(context, *, max_distance=0.0):
    mesh_obj, armature_obj, names = ws._resolve_selected_sources(context)
    return build_surface_plan_for_sources(
        context, mesh_obj, armature_obj, names, max_distance=max_distance,
    )


def build_surface_plan_for_sources(context, mesh_obj, armature_obj, source_names,
                                   *, max_distance=0.0):
    ws._preflight_mesh(context, mesh_obj)
    names = tuple(source_names)
    if not names or len(set(names)) != len(names):
        raise ws.WeightSymmetryError("Select distinct Deform bones from one side.")
    if len({ws._strict_side(name) for name in names}) != 1:
        raise ws.WeightSymmetryError("Select Deform bones from only one side (.L or .R).")
    targets = tuple(ws._strict_opposite_name(name) for name in names)
    tolerance = ws._automatic_tolerance(mesh_obj)
    sides = {ws._bone_source_side(mesh_obj, armature_obj, a, b, tolerance)
             for a, b in zip(names, targets)}
    if len(sides) != 1:
        raise ws.WeightSymmetryError("Selected bones do not share one Mesh-local X side.")
    side = sides.pop()
    source, destination, center = ws._classify_mesh_halves(mesh_obj, side, tolerance)
    source_set, center_set = set(source), set(center)
    snapshot = ws._capture_vertex_groups(mesh_obj)
    states = ws._state_map(snapshot)
    for name in names:
        if name not in states:
            raise ws.WeightSymmetryError(f'Source Vertex Group "{name}" is missing.')
    deform = {b.name for b in armature_obj.data.bones if b.use_deform}
    maps = {name: ws._weight_map(states.get(name)) for name in deform}
    for name, weights in maps.items():
        if any(not math.isfinite(w) or w < 0 or w > 1 for w in weights.values()):
            raise ws.WeightSymmetryError(f'Invalid Deform weights in "{name}".')
    support = {i for name in names for i, w in maps[name].items()
               if w > ws.WEIGHT_EPSILON and i in source_set}
    if not support:
        raise ws.WeightSymmetryError("Selected bones have no positive source-side weights.")
    sampling_maps = {name: dict(weights) for name, weights in maps.items()}
    for name in sorted(deform):
        if not ws.SIDE_NAME_PATTERN.fullmatch(name):
            continue
        opposite = ws._strict_opposite_name(name)
        if opposite not in deform:
            continue  # Missing counterparts are rejected if actually sampled.
        try:
            bone_side = ws._bone_source_side(mesh_obj, armature_obj, name, opposite, tolerance)
        except ws.WeightSymmetryError:
            continue  # An unrelated ambiguous side group is never auto-cleaned.
        same, _, middle = ws._classify_mesh_halves(mesh_obj, bone_side, tolerance)
        for index in ws._wrong_side_weight_islands(mesh_obj, maps[name], same, middle):
            sampling_maps[name].pop(index, None)

    # The automatic bound is anatomical rather than whole-character tolerance.
    # A larger bound is an explicit operator option, never a hidden retry.
    transform = mesh_obj.matrix_world.inverted_safe() @ armature_obj.matrix_world
    lengths = [((transform @ armature_obj.data.bones[n].tail_local)
                - (transform @ armature_obj.data.bones[n].head_local)).length
               for n in names]
    limit = float(max_distance)
    if not math.isfinite(limit) or limit < 0:
        raise ws.WeightSymmetryError("Surface distance must be finite and non-negative.")
    limit = limit or max(tolerance * 4, min(lengths) * 0.02)

    mesh = mesh_obj.data
    mesh.calc_loop_triangles()
    coordinates = [v.co.copy() for v in mesh.vertices]
    allowed = source_set | center_set
    triangles = [tuple(t.vertices) for t in mesh.loop_triangles
                 if all(i in allowed for i in t.vertices)
                 and (coordinates[t.vertices[1]] - coordinates[t.vertices[0]]).cross(
                     coordinates[t.vertices[2]] - coordinates[t.vertices[0]]).length > 1e-12]
    if not triangles:
        raise ws.WeightSymmetryError("The source half has no usable surface triangles.")
    tree = BVHTree.FromPolygons(coordinates, triangles, all_triangles=True)
    before_maps = {s.name: dict(s.weights) for s in snapshot}
    after_maps = {name: dict(weights) for name, weights in before_maps.items()}
    target_support = {i for name in targets for i, w in maps[name].items()
                      if w > ws.WEIGHT_EPSILON}
    failures, sampled, largest = [], [], 0.0
    for index in destination:
        reflected = coordinates[index].copy()
        reflected.x *= -1
        point, normal, triangle_index, distance = tree.find_nearest(reflected)
        if point is None:
            if index in target_support:
                failures.append(index)
            continue
        if index not in target_support and distance > limit:
            continue
        triangle = triangles[triangle_index]
        bary = _barycentric(point, *(coordinates[i] for i in triangle))
        selected_weight = sum(maps[name].get(i, 0.0) * f
                              for name in names for i, f in zip(triangle, bary))
        if selected_weight <= ws.WEIGHT_EPSILON and index not in target_support:
            continue
        if distance > limit:
            failures.append(index)
            continue
        reflected_normal = mesh.vertices[index].normal.copy()
        reflected_normal.x *= -1
        if reflected_normal.length > 1e-8 and reflected_normal.dot(normal) < 0.25:
            failures.append(index)
            continue
        # Nearby disconnected surfaces (e.g. adjacent fingers) must not be
        # resolved by BVH order. Adjacent triangles sharing vertices are fine.
        ambiguous = False
        ambiguity_window = max(tolerance, limit * 0.1)
        for _, _, other_index, other_distance in tree.find_nearest_range(
                reflected, distance + ambiguity_window):
            other = triangles[other_index]
            if (abs(other_distance - distance) <= ambiguity_window
                    and not set(other).intersection(triangle)):
                ambiguous = True
                break
        if ambiguous:
            failures.append(index)
            continue
        vector = {}
        for name in sorted(deform):
            weight = sum(sampling_maps[name].get(i, 0.0) * f for i, f in zip(triangle, bary))
            if weight <= ws.WEIGHT_EPSILON:
                continue
            opposite = ws._strict_opposite_name(name) if ws.SIDE_NAME_PATTERN.fullmatch(name) else name
            if opposite not in deform:
                raise ws.WeightSymmetryError(
                    f'Sampled bone "{name}" has no matching Deform bone "{opposite}".',
                    mesh_obj=mesh_obj, vertex_indices=(index,),
                )
            vector[opposite] = vector.get(opposite, 0.0) + weight
        total = sum(vector.values())
        budget = sum(maps[name].get(index, 0.0) for name in deform)
        if total <= ws.WEIGHT_EPSILON or budget <= ws.WEIGHT_EPSILON:
            raise ws.WeightSymmetryError(
                "Surface mirror needs existing nonzero skin weights on both sides; "
                "bind unweighted vertices first.", mesh_obj=mesh_obj, vertex_indices=(index,),
            )
        for name in deform:
            value = vector.get(name, 0.0) * budget / total
            if value > 1.0 + ws.WEIGHT_TOLERANCE:
                raise ws.WeightSymmetryError("Destination skin total would require a weight above 1.")
            if value > ws.WEIGHT_EPSILON:
                after_maps.setdefault(name, {})[index] = min(value, 1.0)
            elif index in after_maps.get(name, {}):
                after_maps[name].pop(index)
        sampled.append(index)
        largest = max(largest, distance)
    if failures:
        raise ws.WeightSymmetryError(
            f"Surface match is too far or ambiguous at {len(failures)} vertices "
            f"(first {failures[0]}, distance limit {limit:.6g} local units). "
            "No weights changed; locate them and inspect before increasing distance.",
            mesh_obj=mesh_obj, vertex_indices=tuple(failures),
        )
    if not sampled:
        raise ws.WeightSymmetryError("No opposite-side vertices fall in the selected bone region.")
    affected = set()
    # Keep byte-identical entries for semantically equal samples and all locked
    # groups that need no change. Repeated application is a true no-op.
    for name, after in after_maps.items():
        before = before_maps.get(name, {})
        for i in set(before) | set(after):
            if not ws._maps_semantically_differ(before, after, i):
                if i in before:
                    after[i] = before[i]
                else:
                    after.pop(i, None)
            else:
                affected.add(i)
                if name in states and states[name].lock_weight:
                    raise ws.WeightSymmetryError(
                        f'Surface mirror would change locked group "{name}"; no weights changed.',
                        mesh_obj=mesh_obj, vertex_indices=(i,),
                    )
    ordered = list(states) + sorted(name for name in after_maps if name not in states and after_maps[name])
    after = tuple(ws.VertexGroupState(
        name, i, states[name].lock_weight if name in states else False,
        tuple(sorted(after_maps[name].items())),
    ) for i, name in enumerate(ordered))
    ws._validate_deform_budget_batch(snapshot, armature_obj, {s.name: s.weights for s in after})
    return SurfacePlan(mesh_obj, armature_obj, names, targets, side, snapshot, after,
                       mesh_obj.vertex_groups.active_index, tuple(sorted(affected)),
                       len(sampled), largest, limit, _fingerprint(mesh_obj, armature_obj))


def _verify_surface_state(mesh_obj, plan):
    actual = ws._capture_vertex_groups(mesh_obj)
    if len(actual) != len(plan.group_after):
        raise ws.WeightSymmetryError("Surface mirror group count verification failed.")
    for a, b in zip(actual, plan.group_after):
        if (a.name, a.index, a.lock_weight) != (b.name, b.index, b.lock_weight):
            raise ws.WeightSymmetryError("Surface mirror group definition verification failed.")
        am, bm = dict(a.weights), dict(b.weights)
        if set(am) != set(bm) or any(ws._maps_semantically_differ(am, bm, i) for i in am):
            raise ws.WeightSymmetryError(f'Surface mirror verification failed: "{a.name}".')
    if _fingerprint(mesh_obj, plan.armature_obj) != plan.fingerprint:
        raise ws.WeightSymmetryError("Mesh or rest skeleton changed during weight mirroring.")


def apply_surface_plan(plan):
    obj = plan.mesh_obj
    if ws._capture_vertex_groups(obj) != plan.group_snapshot:
        raise ws.WeightSymmetryError("Weights changed after preview; run Surface Mirror again.")
    if _fingerprint(obj, plan.armature_obj) != plan.fingerprint:
        raise ws.WeightSymmetryError("Geometry or rest skeleton changed after preview; run it again.")
    if not plan.affected_indices:
        return plan
    before = ws._state_map(plan.group_snapshot)
    try:
        for state in plan.group_after:
            old = ws._weight_map(before.get(state.name))
            new = dict(state.weights)
            if old == new:
                continue
            group = obj.vertex_groups.get(state.name)
            if group is None:
                group = obj.vertex_groups.new(name=state.name)
                if group.name != state.name:
                    raise ws.WeightSymmetryError("Could not create the exact bone group name.")
            ws._remove_indices(group, sorted(set(old) - set(new)))
            for index, value in new.items():
                if index not in old or old[index] != value:
                    group.add((index,), value, "REPLACE")
        obj.vertex_groups.active_index = plan.active_group_index
        obj.data.update()
        _verify_surface_state(obj, plan)
    except Exception as exc:
        try:
            ws._restore_vertex_groups(obj, plan.group_snapshot, plan.active_group_index)
        except Exception as rollback:
            raise ws.WeightSymmetryRollbackError(
                f"Surface mirror failed ({exc}); rollback also failed ({rollback})."
            ) from rollback
        raise ws.WeightSymmetryError(f"Surface mirror was rolled back: {exc}") from exc
    return plan


class CHARACTERDESIGNER_OT_surface_weight_mirror(Operator):
    bl_idname = "character_designer.surface_weight_mirror"
    bl_label = "Surface Mirror"
    bl_description = (
        "For different topology: mirror all skin weights within the selected bone region "
        "by surface interpolation, including joint blends; keep source, center and geometry unchanged"
    )
    bl_options = {"REGISTER", "UNDO"}

    max_distance: FloatProperty(
        name="Max Surface Distance", default=0.0, min=0.0, precision=6,
        description="Mesh-local units; zero uses 2% of the shortest selected bone length",
    )

    @classmethod
    def poll(cls, context):
        return ws.CHARACTERDESIGNER_OT_copy_weight_to_opposite.poll(context)

    def execute(self, context):
        try:
            plan = build_surface_plan(context, max_distance=self.max_distance)
            apply_surface_plan(plan)
        except ws.WeightSymmetryError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Surface mirror: {len(plan.affected_indices)} vertices updated; "
                    "source and geometry unchanged.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_locate_weight_symmetry(Operator):
    bl_idname = "character_designer.locate_weight_symmetry"
    bl_label = "Locate Unmatched Vertices"
    bl_description = "Select the actual vertices that prevent exact weight mirroring"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return ws.CHARACTERDESIGNER_OT_copy_weight_to_opposite.poll(context)

    def execute(self, context):
        try:
            ws.build_weight_symmetry_batch_plan(context)
        except ws.WeightSymmetryError as exc:
            obj, indices = exc.mesh_obj, exc.vertex_indices
            if obj is None or not indices:
                self.report({"WARNING"}, str(exc))
                return {"CANCELLED"}
            if context.object and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for selected in context.selected_objects:
                selected.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            selected = set(indices)
            for v in obj.data.vertices:
                v.select = v.index in selected
            for e in obj.data.edges:
                e.select = all(i in selected for i in e.vertices)
            for p in obj.data.polygons:
                p.select = all(i in selected for i in p.vertices)
            context.tool_settings.mesh_select_mode = (True, False, False)
            bpy.ops.object.mode_set(mode="EDIT")
            self.report({"INFO"}, f"Selected {len(indices)} unmatched vertices; weights unchanged.")
            return {"FINISHED"}
        self.report({"INFO"}, "No unmatched vertices in the selected weight region.")
        return {"FINISHED"}


WEIGHT_SURFACE_CLASSES = (
    CHARACTERDESIGNER_OT_surface_weight_mirror,
    CHARACTERDESIGNER_OT_locate_weight_symmetry,
)
