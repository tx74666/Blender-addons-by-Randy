"""Previewed, transactional cleanup of bone-weight islands and empty vertices.

Planning uses immutable base-Mesh snapshots.  Applying a plan changes only the
listed Vertex Group memberships; it never rebuilds groups, runs a weight solver,
changes the Rest pose, or normalizes existing paint.
"""

from dataclasses import dataclass
import hashlib
from itertools import islice
import json
import math
import struct
import textwrap
import traceback

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList

from . import weight_cleanup_core as core
from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_WEIGHT, active_ui_page


# Even a tiny positive Deform membership is existing authored paint, not a gap.
EPSILON = 0.0
WRITE_MODES = frozenset({"OBJECT", "POSE", "PAINT_WEIGHT"})


class WeightCleanupError(ValueError):
    """A preflight failure that leaves the artist's data unchanged."""


def format_cleanup_errors(errors, *, review_keep=False, max_chars=600,
                          max_examples=2, weights_unchanged=True):
    """Bound diagnostic output without joining every per-vertex failure."""
    limit = max(1, min(600, int(max_chars)))
    example_limit = max(1, min(2, int(max_examples)))
    if isinstance(errors, str):
        count, examples = 1, (errors,)
    else:
        try:
            count = len(errors)
        except TypeError:
            count = 0
            examples = []
            for error in errors:
                count += 1
                if len(examples) < example_limit:
                    examples.append(error)
        else:
            examples = tuple(islice(iter(errors), example_limit))
    prefix = f"{count} error{'s' if count != 1 else ''}."
    suffix = "No weights changed." if weights_unchanged else "Inspect the Mesh before continuing."
    if review_keep:
        suffix += " Review Keep choices."
    budget = max(0, limit - len(prefix) - len(suffix) - 2)
    parts = []
    if examples and budget:
        per_example = max(1, (budget - max(0, len(examples) - 1) * 2) // len(examples))
        for example in examples:
            raw = str(example)
            detail = " ".join(raw[:per_example + 32].split())
            if len(raw) > per_example or len(detail) > per_example:
                detail = detail[:max(0, per_example - 1)].rstrip() + "…"
            parts.append(detail)
    message = " ".join(part for part in (prefix, "; ".join(parts), suffix) if part)
    if len(message) > limit:
        message = message[:max(0, limit - 1)].rstrip() + "…"
    return message


def cleanup_error_lines(errors, *, line_width=82, **kwargs):
    """Use the same bounded diagnostic in short native-UI labels."""
    message = format_cleanup_errors(errors, **kwargs)
    return tuple(textwrap.wrap(message, width=max(1, int(line_width)),
                               break_long_words=True, break_on_hyphens=False))


@dataclass(frozen=True)
class PreparedCleanup:
    operation: str
    scope: str
    fill_holes: bool
    target_name: str
    fingerprint: str
    plan: object
    reference_names: tuple
    skipped_references: tuple
    warnings: tuple
    selected_bone_names: tuple


@dataclass(frozen=True)
class PreparedInspection:
    target_name: str
    context_token: str
    fingerprint: str
    scan: object
    reference_names: tuple
    skipped_references: tuple
    unweighted_count: int
    warnings: tuple


def _target_token(target, armature):
    return ":".join(format(value.as_pointer(), "x")
                    for value in (target, target.data, armature, armature.data))


def _is_local_editable(value):
    return (
        value is not None
        and getattr(value, "library", None) is None
        and getattr(value, "override_library", None) is None
        and bool(getattr(value, "is_editable", True))
    )


def _armature_modifier(mesh):
    modifiers = tuple(m for m in mesh.modifiers if m.type == "ARMATURE")
    if len(modifiers) != 1:
        raise WeightCleanupError("The Mesh must have exactly one Armature modifier.")
    modifier = modifiers[0]
    armature = modifier.object
    if armature is None or armature.type != "ARMATURE":
        raise WeightCleanupError("The Armature modifier needs an existing Rig.")
    if not modifier.show_viewport:
        raise WeightCleanupError("Enable the Armature modifier in the viewport before cleanup.")
    if modifier.vertex_group:
        raise WeightCleanupError("Remove the Armature modifier's group mask before cleanup.")
    if modifier.use_multi_modifier:
        raise WeightCleanupError("Multi Modifier weighting needs a separate check before cleanup.")
    if not modifier.use_vertex_groups or modifier.use_bone_envelopes:
        raise WeightCleanupError("Use Vertex Groups with Bone Envelopes disabled.")
    return modifier, armature


def _validate_mesh(mesh):
    if not _is_local_editable(mesh) or not _is_local_editable(mesh.data):
        raise WeightCleanupError("Use a local, editable Mesh without a library override.")
    if mesh.data.users != 1:
        raise WeightCleanupError("Make the Mesh data single-user before cleaning weights.")
    parented = mesh
    while parented is not None:
        if parented.parent_type == "BONE":
            raise WeightCleanupError("Bone-parented Meshes need a separate Rest-space check before cleanup.")
        parented = parented.parent
    return _armature_modifier(mesh)


def _resolve_target(context, *, allow_edit=False):
    modes = WRITE_MODES | ({"EDIT_MESH"} if allow_edit else set())
    if context.mode not in modes:
        raise WeightCleanupError("Use Object, Pose, or Weight Paint Mode to clean weights.")
    selected = tuple(obj for obj in context.selected_objects if obj.type == "MESH")
    if len(selected) != 1:
        raise WeightCleanupError("Select exactly one bound target Mesh.")
    mesh = selected[0]
    if context.view_layer.objects.get(mesh.name) is not mesh:
        raise WeightCleanupError("The target Mesh is outside the current View Layer.")
    active = context.view_layer.objects.active
    if active is None or (active is not mesh and active.type != "ARMATURE"):
        raise WeightCleanupError("Make the target Mesh or its Rig active.")
    _modifier, armature = _validate_mesh(mesh)
    if active.type == "ARMATURE" and active is not armature:
        raise WeightCleanupError("The active Rig does not control the target Mesh.")
    if not _is_local_editable(armature) or not _is_local_editable(armature.data):
        raise WeightCleanupError("Use a local, editable Rig without a library override.")
    if armature.mode == "EDIT":
        raise WeightCleanupError("Finish editing the Rig before cleaning weights.")
    if not any(bone.use_deform for bone in armature.data.bones):
        raise WeightCleanupError("The Rig has no Deform bones.")
    return mesh, armature


def _selected_bones(context, mesh, armature):
    if context.mode in {"POSE", "PAINT_WEIGHT"}:
        names = tuple(
            bone.name for bone in armature.data.bones
            if bone.use_deform and bool(getattr(bone, "select", False))
        )
        # In Blender 5.2 selection may be exposed on PoseBone instead of Bone.
        if not names and armature.pose is not None:
            names = tuple(
                pose.name for pose in armature.pose.bones
                if pose.bone.use_deform and bool(getattr(pose, "select", False))
            )
    else:
        group = mesh.vertex_groups.active
        bone = armature.data.bones.get(group.name) if group else None
        names = (group.name,) if bone is not None and bone.use_deform else ()
    if not names:
        raise WeightCleanupError("Select Deform bones, or make a Deform Vertex Group active.")
    for name in names:
        group = mesh.vertex_groups.get(name)
        if group is not None and group.lock_weight:
            raise WeightCleanupError(f"Unlock the selected group '{name}' before cleaning it.")
    return names


def _memberships(mesh):
    names = {group.index: group.name for group in mesh.vertex_groups}
    result = []
    for vertex in mesh.data.vertices:
        values = {}
        for membership in vertex.groups:
            if membership.group not in names:
                raise WeightCleanupError("A Vertex Group membership has an invalid group index.")
            value = float(membership.weight)
            if not math.isfinite(value) or value < 0.0:
                raise WeightCleanupError("The Mesh contains invalid weights; no changes were made.")
            values[names[membership.group]] = value
        result.append(values)
    return tuple(result)


def _snapshot_mesh(mesh):
    mesh.data.calc_loop_triangles()
    matrix = mesh.matrix_world
    shape_keys = mesh.data.shape_keys
    basis = shape_keys.key_blocks[0].data if shape_keys and shape_keys.key_blocks else None
    if basis is not None and len(basis) != len(mesh.data.vertices):
        raise WeightCleanupError("The Basis Shape Key does not match the source Mesh.")
    return core.MeshSnapshot(
        name=mesh.name,
        world_positions=tuple(tuple(matrix @ (basis[vertex.index].co if basis is not None else vertex.co))
                              for vertex in mesh.data.vertices),
        edges=tuple(tuple(edge.vertices) for edge in mesh.data.edges),
        weights=_memberships(mesh),
        triangles=tuple(tuple(triangle.vertices) for triangle in mesh.data.loop_triangles),
    )


def _reference_meshes(context, target, armature, scope):
    references = [target]
    skipped = []
    if scope == "CHARACTER":
        for obj in sorted(context.scene.objects, key=lambda item: item.name):
            if obj is target or obj.type != "MESH":
                continue
            if not any(m.type == "ARMATURE" and m.object is armature for m in obj.modifiers):
                continue
            try:
                _modifier, rig = _validate_mesh(obj)
                if rig is not armature or obj.mode == "EDIT":
                    raise WeightCleanupError("Reference Mesh is unavailable.")
                _memberships(obj)
            except WeightCleanupError:
                skipped.append(obj.name)
                continue
            references.append(obj)
    return tuple(references), tuple(skipped)


def _fingerprint(context, target, armature, references, snapshots, selected_names, skipped):
    digest = hashlib.sha256()

    def add(value):
        encoded = repr(value).encode("utf-8")
        digest.update(struct.pack("<Q", len(encoded)))
        digest.update(encoded)

    add((context.mode, target.name, target.data.name, armature.name, armature.data.name,
         target.vertex_groups.active_index))
    add(_target_token(target, armature))
    add(tuple(sorted(obj.name for obj in context.selected_objects)))
    add(context.view_layer.objects.active.name)
    add((selected_names, skipped))
    add(tuple(tuple(row) for row in armature.matrix_world))
    for bone in armature.data.bones:
        add((bone.name, bone.use_deform, bool(getattr(bone, "select", False)),
             tuple(bone.head_local), tuple(bone.tail_local),
             tuple(tuple(row) for row in bone.matrix_local)))
    if armature.pose is not None:
        for bone in armature.pose.bones:
            add((bone.name, bool(getattr(bone, "select", False)),
                 tuple(tuple(row) for row in bone.matrix_basis)))
    for obj, snapshot in zip(references, snapshots):
        add((obj.name, obj.data.name, obj.data.users))
        add(tuple((group.name, group.index, group.lock_weight) for group in obj.vertex_groups))
        modifier, rig = _armature_modifier(obj)
        add((modifier.name, rig.name, modifier.use_vertex_groups, modifier.use_bone_envelopes,
             modifier.show_viewport, modifier.show_render, modifier.vertex_group,
             modifier.use_multi_modifier))
        add(tuple(tuple(row) for row in obj.matrix_world))
        for point, weights in zip(snapshot.world_positions, snapshot.weights):
            add((point, tuple(sorted(weights.items()))))
        for edge in snapshot.edges:
            add(edge)
        for triangle in snapshot.triangles:
            add(triangle)
        for vertex in obj.data.vertices:
            add(tuple(vertex.co))
        add(tuple((vertex.select, vertex.hide) for vertex in obj.data.vertices))
    return digest.hexdigest()


def _capture(context, operation, scope):
    target, armature = _resolve_target(context)
    selected = _selected_bones(context, target, armature) if operation == "ISLANDS" else ()
    references, skipped = _reference_meshes(context, target, armature, scope)
    snapshots = tuple(_snapshot_mesh(obj) for obj in references)
    fingerprint = _fingerprint(context, target, armature, references, snapshots, selected, skipped)
    deform = frozenset(bone.name for bone in armature.data.bones if bone.use_deform)
    locked = {
        obj.name: frozenset(group.name for group in obj.vertex_groups if group.lock_weight)
        for obj in references
    }
    return target, armature, selected, references, snapshots, fingerprint, deform, locked, skipped


def build_cleanup_plan(context, operation, *, scope="CHARACTER", fill_holes=True):
    """Build a read-only preview; no Blender data or selection is changed."""
    if operation not in {"FILL", "ISLANDS"} or scope not in {"CURRENT", "CHARACTER"}:
        raise WeightCleanupError("Unknown weight cleanup operation.")
    if operation == "FILL":
        scope = "CURRENT"
    target, armature, selected, references, snapshots, fingerprint, deform, locked, skipped = (
        _capture(context, operation, scope)
    )
    if operation == "FILL":
        plan = core.plan_fill(snapshots[0], deform, locked_names=locked[target.name], epsilon=EPSILON)
    else:
        segments = {
            name: (tuple(armature.matrix_world @ armature.data.bones[name].head_local),
                   tuple(armature.matrix_world @ armature.data.bones[name].tail_local))
            for name in selected
        }
        plan = core.plan_islands(
            snapshots, (target.name,), segments, deform, locked_names=locked,
            scope=scope, fill_holes=fill_holes, epsilon=EPSILON,
        )
    warnings = []
    if any(modifier.type == "MIRROR" and (modifier.show_viewport or modifier.show_render)
           for modifier in target.modifiers):
        warnings.append("Source vertices only; generated Mirror vertices are not cleaned.")
    modifier, _rig = _armature_modifier(target)
    if not modifier.show_render:
        warnings.append("The Armature modifier is disabled for rendering.")
    return PreparedCleanup(
        operation, scope, bool(fill_holes), target.name, fingerprint, plan,
        tuple(obj.name for obj in references), skipped, tuple(warnings), selected,
    )


def build_inspection(context, *, scope="CHARACTER"):
    """Inspect every used Deform bone without selecting bones or changing paint."""
    if scope not in {"CURRENT", "CHARACTER"}:
        raise WeightCleanupError("Unknown weight inspection scope.")
    target, armature, _selected, references, snapshots, fingerprint, deform, _locked, skipped = (
        _capture(context, "INSPECT", scope)
    )
    segments = {
        bone.name: (tuple(armature.matrix_world @ bone.head_local),
                    tuple(armature.matrix_world @ bone.tail_local))
        for bone in armature.data.bones if bone.use_deform
    }
    scan = core.inspect_islands(snapshots, (target.name,), deform, segments,
                                scope=scope, epsilon=EPSILON)
    unweighted = sum(
        sum(value for name, value in weights.items() if name in deform and value > 0.0) <= EPSILON
        for weights in snapshots[0].weights
    )
    return PreparedInspection(
        target.name, _target_token(target, armature), fingerprint, scan,
        tuple(obj.name for obj in references), skipped, unweighted,
        ("Source vertices only; generated Mirror vertices are not inspected.",)
        if any(mod.type == "MIRROR" and (mod.show_viewport or mod.show_render)
               for mod in target.modifiers) else (),
    )


def build_allowed_plan(context, allowed_names, *, selected_vertices_only=False):
    """Plan only explicit bone exclusions on the current target Mesh."""
    target, _rig, _selected, _refs, snapshots, fingerprint, deform, locked, skipped = (
        _capture(context, "ALLOWED", "CURRENT")
    )
    targets = tuple(vertex.index for vertex in target.data.vertices if vertex.select) \
        if selected_vertices_only else None
    plan = core.plan_allowed_bones(
        snapshots[0], deform, allowed_names, locked_names=locked[target.name], targets=targets,
    )
    warnings = (
        ("Source vertices only; generated Mirror vertices are not cleaned.",)
        if any(mod.type == "MIRROR" and (mod.show_viewport or mod.show_render)
               for mod in target.modifiers) else ()
    )
    return PreparedCleanup(
        "ALLOWED", "CURRENT", True, target.name, fingerprint, plan,
        (target.name,), skipped, warnings, (),
    )


def locate_weight_group(context, target_name, bone_name, *, context_token):
    """Locate the row's current suspect vertices without changing any weights."""
    target, armature = _resolve_target(context)
    if target.name != target_name or _target_token(target, armature) != context_token:
        raise WeightCleanupError("The target Mesh or Rig changed. Inspect again.")
    bone = armature.data.bones.get(bone_name)
    group = target.vertex_groups.get(bone_name)
    if bone is None or not bone.use_deform or group is None:
        raise WeightCleanupError("The inspected Deform bone or Vertex Group no longer exists.")
    inspection = build_inspection(context)
    if not inspection.scan.ok:
        raise WeightCleanupError(format_cleanup_errors(inspection.scan.errors))
    record = next((item for item in inspection.scan.records
                   if item.bone_name == bone_name and item.mesh_name == target.name), None)
    if record is None or not record.suspect_vertex_indices:
        raise WeightCleanupError("This bone has no currently locatable suspect vertices.")
    previous_group = target.vertex_groups.active_index
    previous_active = armature.data.bones.active
    previous_selected = tuple(
        (pose.name, bool(getattr(pose, "select", getattr(pose.bone, "select", False))))
        for pose in armature.pose.bones
    ) if armature.pose is not None else ()

    def select_pose(pose, value):
        if hasattr(pose, "select"):
            pose.select = value
        else:
            pose.bone.select = value

    try:
        target.vertex_groups.active_index = group.index
        if armature.pose is not None:
            for pose in armature.pose.bones:
                select_pose(pose, pose.name == bone_name)
        armature.data.bones.active = bone
        _select_vertices(context, target, record.suspect_vertex_indices)
    except Exception as exc:
        target.vertex_groups.active_index = previous_group
        if armature.pose is not None:
            for name, selected in previous_selected:
                select_pose(armature.pose.bones[name], selected)
        armature.data.bones.active = previous_active
        raise WeightCleanupError("Could not select the inspected bone: " + str(exc)) from exc
    return target, armature


def _select_vertices(context, target, indices):
    """Make a read-only diagnostic's vertex selection visible to the artist."""
    selected = set(indices)
    hidden = 0
    count = 0
    if context.mode == "EDIT_MESH":
        import bmesh
        bm = bmesh.from_edit_mesh(target.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        for face in bm.faces:
            face.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        for vertex in bm.verts:
            wanted = vertex.index in selected
            vertex.select_set(wanted and not vertex.hide)
            count += wanted and not vertex.hide
            hidden += wanted and vertex.hide
        context.tool_settings.mesh_select_mode = (True, False, False)
        bm.select_mode = {"VERT"}
        bm.select_flush_mode()
        bmesh.update_edit_mesh(target.data, loop_triangles=False, destructive=False)
        return count, hidden
    for face in target.data.polygons:
        face.select = False
    for edge in target.data.edges:
        edge.select = False
    for vertex in target.data.vertices:
        wanted = vertex.index in selected
        vertex.select = wanted and not vertex.hide
        count += wanted and not vertex.hide
        hidden += wanted and vertex.hide
    target.data.update()
    if context.mode == "PAINT_WEIGHT":
        target.data.use_paint_mask = False
        target.data.use_paint_mask_vertex = True
    else:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        context.view_layer.objects.active = target
        context.tool_settings.mesh_select_mode = (True, False, False)
        bpy.ops.object.mode_set(mode="EDIT")
    return count, hidden


def _vertex_memberships(mesh, vertex_index):
    names = {group.index: group.name for group in mesh.vertex_groups}
    return tuple(sorted((names[item.group], float(item.weight))
                        for item in mesh.data.vertices[vertex_index].groups))


def _write_vertex_memberships(mesh, vertex_index, old_weights, new_weights):
    old, new = dict(old_weights), dict(new_weights)
    for name in old.keys() - new.keys():
        mesh.vertex_groups[name].remove([vertex_index])
    for name, weight in new.items():
        if name not in old or old[name] != weight:
            mesh.vertex_groups[name].add([vertex_index], weight, "REPLACE")


def _restore_vertex_memberships(mesh, vertex_index, expected):
    current = dict(_vertex_memberships(mesh, vertex_index))
    old = dict(expected)
    for name in current.keys() - old.keys():
        mesh.vertex_groups[name].remove([vertex_index])
    for name, weight in old.items():
        if name not in current or current[name] != weight:
            mesh.vertex_groups[name].add([vertex_index], weight, "REPLACE")


def _native_weights(values):
    return tuple(sorted((name, struct.unpack("<f", struct.pack("<f", value))[0])
                        for name, value in values))


def apply_cleanup_plan(context, prepared):
    """Revalidate a preview and copy back only its planned memberships."""
    target, _armature, _selected, _refs, _snapshots, fingerprint, _deform, _locked, _skipped = (
        _capture(context, prepared.operation, prepared.scope)
    )
    if target.name != prepared.target_name or fingerprint != prepared.fingerprint:
        raise WeightCleanupError("The Mesh, Rig, selection, or weights changed. Preview again.")
    plan = prepared.plan
    if not plan.ok:
        raise WeightCleanupError(format_cleanup_errors(
            plan.errors, review_keep=prepared.operation == "ALLOWED",
        ))
    changes = tuple(plan.changes)
    seen = set()
    for change in changes:
        key = (change.mesh_name, change.vertex_index)
        if (change.mesh_name != target.name or key in seen
                or change.vertex_index < 0 or change.vertex_index >= len(target.data.vertices)):
            raise WeightCleanupError("The cleanup plan contains an invalid target.")
        seen.add(key)
        if _vertex_memberships(target, change.vertex_index) != tuple(change.old_weights):
            raise WeightCleanupError("The weights changed. Preview again.")
        old, new = dict(change.old_weights), dict(change.new_weights)
        for name in old.keys() | new.keys():
            group = target.vertex_groups.get(name)
            if group is None:
                raise WeightCleanupError("The cleanup plan requires a missing Vertex Group.")
            if group.lock_weight and old.get(name) != new.get(name):
                raise WeightCleanupError(f"The cleanup plan would change locked group '{name}'.")
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0
               for value in new.values()):
            raise WeightCleanupError("The cleanup plan contains invalid weights.")
    try:
        for change in changes:
            _write_vertex_memberships(target, change.vertex_index, change.old_weights, change.new_weights)
        for change in changes:
            if _vertex_memberships(target, change.vertex_index) != _native_weights(change.new_weights):
                raise WeightCleanupError("Blender did not retain the planned weights.")
        target.data.update()
    except Exception as failure:
        rollback_errors = []
        for change in changes:
            try:
                _restore_vertex_memberships(target, change.vertex_index, change.old_weights)
                if _vertex_memberships(target, change.vertex_index) != tuple(change.old_weights):
                    raise RuntimeError("Restored memberships do not match the checkpoint.")
            except Exception as rollback_failure:
                rollback_errors.append(str(rollback_failure))
        target.data.update()
        if rollback_errors:
            raise RuntimeError("Weight rollback failed. " + format_cleanup_errors(
                rollback_errors, weights_unchanged=False, max_chars=570,
            )) from failure
        raise WeightCleanupError(format_cleanup_errors((str(failure),))) from failure
    return len(changes)


class _CleanupOperator:
    operation = "FILL"

    def _prepare(self, context):
        return build_cleanup_plan(
            context, self.operation, scope=getattr(self, "scope", "CURRENT"),
            fill_holes=getattr(self, "fill_holes", True),
        )

    @classmethod
    def poll(cls, context):
        return context.mode in WRITE_MODES and context.view_layer is not None

    def invoke(self, context, _event):
        try:
            self._prepared_cleanup = self._prepare(context)
            if not self._prepared_cleanup.plan.ok:
                raise WeightCleanupError(format_cleanup_errors(self._prepared_cleanup.plan.errors))
        except (WeightCleanupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, _context):
        layout = self.layout
        prepared = getattr(self, "_prepared_cleanup", None)
        if prepared is None:
            return
        stats = prepared.plan.stats
        layout.label(text=f"Target Mesh: {prepared.target_name}")
        if self.operation == "ISLANDS":
            names = prepared.selected_bone_names
            bone_label = ", ".join(names[:3])
            if len(names) > 3:
                bone_label += f" (+{len(names) - 3})"
            layout.label(text=f"Selected bones: {bone_label}")
            reference = "Character" if prepared.scope == "CHARACTER" else "Current Mesh"
            layout.label(text=f"Reference: {reference} ({len(prepared.reference_names)} Meshes)")
            layout.label(text=f"Remove {stats.get('removed_memberships', 0)} stray influences")
            layout.label(text=f"Keep {stats.get('retained_islands', 0)} main weight islands")
            retained = tuple(island for island in prepared.plan.islands if island.retained)
            for island in retained[:4]:
                layout.label(text=f"Keep {island.bone_name}: {island.mesh_name}")
            if len(retained) > 4:
                layout.label(text=f"Plus {len(retained) - 4} other main islands")
        layout.label(text=f"Fill {stats.get('filled_vertices', 0)} unweighted vertices")
        layout.label(text=f"Change {stats.get('changed_vertices', len(prepared.plan.changes))} vertices")
        if prepared.skipped_references:
            layout.label(text=f"Excluded {len(prepared.skipped_references)} unsafe reference Meshes", icon="INFO")
        for warning in (prepared.warnings + tuple(getattr(prepared.plan, "warnings", ())))[:3]:
            layout.label(text=str(warning), icon="INFO")

    def execute(self, context):
        try:
            prepared = getattr(self, "_prepared_cleanup", None) or self._prepare(context)
            changed = apply_cleanup_plan(context, prepared)
        except (WeightCleanupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        stats = prepared.plan.stats
        message = f"Changed {changed} vertices; filled {stats.get('filled_vertices', 0)}."
        if prepared.warnings:
            message += " Source Mesh only."
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_clean_weight_islands(_CleanupOperator, Operator):
    bl_idname = "character_designer.clean_weight_islands"
    bl_label = "Clean Weight Islands"
    bl_description = "Keep the selected bones' nearby main weight islands and remove their stray influences"
    bl_options = {"REGISTER", "UNDO"}
    operation = "ISLANDS"

    scope: EnumProperty(
        name="Reference", items=(("CHARACTER", "Character", "Compare safe Meshes bound to this Rig"),
                                 ("CURRENT", "Current Mesh", "Compare islands only on the target Mesh")),
        default="CHARACTER", options={"HIDDEN"},
    )
    fill_holes: BoolProperty(name="Fill New Gaps", default=True, options={"HIDDEN"})


class CHARACTERDESIGNER_OT_fill_unweighted(_CleanupOperator, Operator):
    bl_idname = "character_designer.fill_unweighted"
    bl_label = "Fill Unweighted"
    bl_description = "Fill only vertices with no valid Deform weight, using connected neighbours on this Mesh"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, _event):
        return self.execute(context)


class CHARACTERDESIGNER_PG_weight_keep_row(PropertyGroup):
    name: StringProperty()
    bone_name: StringProperty()
    keep: BoolProperty(name="Keep", default=True)
    vertex_count: IntProperty(default=0, min=0)
    weight_sum: FloatProperty(default=0.0, min=0.0)
    locked: BoolProperty(default=False)


def _keep_columns(layout):
    first = layout.split(factor=0.12)
    keep = first.column()
    second = first.split(factor=0.55)
    bone = second.column()
    third = second.split(factor=0.50)
    return keep, bone, third.column(), third.column()


class CHARACTERDESIGNER_UL_weight_keep(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data,
                  _active_propname, _index=0, _flt_flag=0):
        keep, bone, count, weight = _keep_columns(layout)
        keep.enabled = not item.locked
        keep.prop(item, "keep", text="")
        bone.label(text=item.bone_name, icon="LOCKED" if item.locked else "BONE_DATA")
        count.label(text=str(item.vertex_count))
        weight.label(text=f"{item.weight_sum:.4g}")


class CHARACTERDESIGNER_OT_clear_bone_influences(Operator):
    bl_idname = "character_designer.clear_bone_influences"
    bl_label = "Clear Bone Influences"
    bl_description = "Choose bones to keep; clear unchecked influences and fill only newly unweighted vertices"
    bl_options = {"REGISTER", "UNDO"}

    rows: CollectionProperty(type=CHARACTERDESIGNER_PG_weight_keep_row, options={"HIDDEN", "SKIP_SAVE"})
    active_index: IntProperty(default=-1, min=-1, options={"HIDDEN", "SKIP_SAVE"})
    selected_vertices_only: BoolProperty(
        name="Selected Vertices Only", default=False,
        description="Change only the target Mesh's currently selected source vertices",
    )
    exclude_bones: StringProperty(
        default="", options={"HIDDEN", "SKIP_SAVE"},
        description="Explicit unchecked bone names as a JSON array for scripted calls",
    )

    @classmethod
    def poll(cls, context):
        return context.mode in WRITE_MODES and context.view_layer is not None

    def _initialize_rows(self, context):
        target, _rig, _selected, _refs, snapshots, fingerprint, deform, _locked, _skipped = (
            _capture(context, "ALLOWED", "CURRENT")
        )
        counts = {}
        totals = {}
        for weights in snapshots[0].weights:
            for name, value in weights.items():
                if name in deform and value > 0.0:
                    counts[name] = counts.get(name, 0) + 1
                    totals[name] = totals.get(name, 0.0) + value
        try:
            excluded = json.loads(self.exclude_bones) if self.exclude_bones else []
        except (TypeError, ValueError) as exc:
            raise WeightCleanupError("Unchecked bones must be an explicit JSON list of names.") from exc
        if (not isinstance(excluded, list) or any(not isinstance(name, str) for name in excluded)
                or len(set(excluded)) != len(excluded) or set(excluded) - counts.keys()):
            raise WeightCleanupError("Unchecked bones must name existing positive Deform influences on this Mesh.")
        self.rows.clear()
        for name in sorted(counts):
            item = self.rows.add()
            item.name = item.bone_name = name
            item.keep = name not in excluded
            item.vertex_count = counts[name]
            item.weight_sum = totals[name]
            item.locked = target.vertex_groups[name].lock_weight
        self.active_index = -1
        self._target_name = target.name
        self._initial_fingerprint = fingerprint
        self._initial_deform_names = deform
        self._initial_row_names = frozenset(counts)
        self._initialized = True
        self._preview_cache = None

    def _prepare(self, context):
        if not getattr(self, "_initialized", False):
            self._initialize_rows(context)
        row_names = tuple(item.bone_name for item in self.rows)
        if len(row_names) != len(set(row_names)) or frozenset(row_names) != self._initial_row_names:
            raise WeightCleanupError("The Keep list changed unexpectedly. Open it again.")
        excluded = frozenset(item.bone_name for item in self.rows if not item.keep)
        allowed = self._initial_deform_names - excluded
        prepared = build_allowed_plan(
            context, allowed, selected_vertices_only=self.selected_vertices_only,
        )
        if prepared.fingerprint != self._initial_fingerprint:
            raise WeightCleanupError("The Mesh, Rig, selection, or weights changed. Open the Keep list again.")
        return prepared

    def invoke(self, context, _event):
        try:
            self._initialize_rows(context)
        except (WeightCleanupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=600, confirm_text="Clear")

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Target Mesh: {getattr(self, '_target_name', '')}")
        unchecked = tuple(item.bone_name for item in self.rows if not item.keep)
        layout.label(text=f"Keep {len(self.rows) - len(unchecked)} bones / Clear {len(unchecked)} bones")
        layout.label(text="Checked = KEEP · Unchecked = CLEAR")
        if unchecked:
            names = ", ".join(unchecked[:3])
            if len(unchecked) > 3:
                names += f" (+{len(unchecked) - 3})"
            for line in textwrap.wrap(f"Clear: {names}", width=82,
                                      break_long_words=True, break_on_hyphens=False):
                layout.label(text=line)
        layout.prop(self, "selected_vertices_only")
        keep, bone, count, weight = _keep_columns(layout.row())
        keep.label(text="Keep")
        bone.label(text="Bone")
        count.label(text="Vertices")
        weight.label(text="Total Weight")
        layout.template_list("CHARACTERDESIGNER_UL_weight_keep", "", self, "rows",
                             self, "active_index", rows=min(10, max(3, len(self.rows))))
        settings_key = (self.selected_vertices_only, tuple((item.bone_name, item.keep) for item in self.rows))
        try:
            cached = getattr(self, "_preview_cache", None)
            if cached is None or cached[0] != settings_key:
                prepared = self._prepare(context)
                self._preview_cache = (settings_key, prepared)
            else:
                prepared = cached[1]
            if prepared.plan.ok:
                stats = prepared.plan.stats
                layout.label(text=f"Clear {stats.get('removed_memberships', 0)} influences; "
                                  f"fill {stats.get('filled_vertices', 0)} new gaps")
                layout.label(text=f"Change {stats.get('changed_vertices', 0)} vertices")
            else:
                for message in cleanup_error_lines(prepared.plan.errors, review_keep=True):
                    layout.label(text=str(message), icon="ERROR")
            for warning in prepared.warnings:
                layout.label(text=warning, icon="INFO")
        except (WeightCleanupError, ValueError) as exc:
            for message in cleanup_error_lines((str(exc),), review_keep=True):
                layout.label(text=message, icon="ERROR")
        layout.label(text="New gaps use connected neighbours on this Mesh.", icon="INFO")
        if any(item.locked for item in self.rows):
            layout.label(text="Locked groups stay checked.", icon="LOCKED")

    def execute(self, context):
        try:
            prepared = self._prepare(context)
            changed = apply_cleanup_plan(context, prepared)
        except (WeightCleanupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.exclude_bones = json.dumps([item.bone_name for item in self.rows if not item.keep])
        stats = prepared.plan.stats
        self.report({"INFO"}, f"Changed {changed} vertices; cleared {stats.get('removed_memberships', 0)} "
                    f"influences and filled {stats.get('filled_vertices', 0)} new gaps.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_find_unweighted(Operator):
    bl_idname = "character_designer.find_unweighted"
    bl_label = "Find Unweighted"
    bl_description = "Show vertices with no valid bone weights in Edit Mode or Weight Paint vertex selection"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode in WRITE_MODES | {"EDIT_MESH"} and context.view_layer is not None

    def execute(self, context):
        try:
            target, armature = _resolve_target(context, allow_edit=True)
            deform = {bone.name for bone in armature.data.bones if bone.use_deform}
            index_names = {group.index: group.name for group in target.vertex_groups}
            if context.mode == "EDIT_MESH":
                import bmesh
                bm = bmesh.from_edit_mesh(target.data)
                bm.verts.ensure_lookup_table()
                bm.verts.index_update()
                layer = bm.verts.layers.deform.active
                indices = []
                for vertex in bm.verts:
                    values = vertex[layer] if layer is not None else {}
                    empty = sum(float(value) for index, value in values.items()
                                if index_names.get(index) in deform and value > 0.0) <= EPSILON
                    if empty:
                        indices.append(vertex.index)
            else:
                memberships = _memberships(target)
                indices = tuple(vertex.index for vertex, values in zip(target.data.vertices, memberships)
                                if sum(value for name, value in values.items()
                                       if name in deform and value > 0.0) <= EPSILON)
            if not indices:
                self.report({"INFO"}, "No unweighted vertices; selection and weights unchanged.")
                return {"FINISHED"}
            count, hidden = _select_vertices(context, target, indices)
            message = f"Selected {count} unweighted vertices; weights unchanged."
            if hidden:
                message += f" {hidden} hidden vertices were left hidden."
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (WeightCleanupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


class CHARACTERDESIGNER_PG_weight_inspection_row(PropertyGroup):
    name: StringProperty()
    bone_name: StringProperty()
    target_name: StringProperty()
    context_token: StringProperty()
    suspect_vertices: IntProperty(default=0, min=0)
    distant_vertices: IntProperty(default=0, min=0)
    stray_vertices: IntProperty(default=0, min=0)
    target_vertices: IntProperty(default=0, min=0)
    main_mesh: StringProperty()
    distance_label: StringProperty()
    reason: StringProperty()
    status: StringProperty()


def _inspection_columns(layout):
    first = layout.split(factor=0.28)
    bone = first.column()
    second = first.split(factor=0.12)
    distant = second.column()
    third = second.split(factor=0.14)
    stray = third.column()
    fourth = third.split(factor=0.50)
    return bone, distant, stray, fourth.column(), fourth.column()


class CHARACTERDESIGNER_UL_weight_inspection(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data,
                  _active_propname, _index=0, _flt_flag=0):
        bone, distant, stray, distance, main = _inspection_columns(layout)
        if item.suspect_vertices:
            action = bone.operator(
                "character_designer.locate_weight_island", text=item.bone_name,
                icon="INFO", emboss=False,
            )
            action.target_name = item.target_name
            action.bone_name = item.bone_name
            action.context_token = item.context_token
        else:
            bone.label(text=item.bone_name, icon="CHECKMARK" if item.status == "OK" else "INFO")
        distant.label(text=str(item.distant_vertices))
        stray.label(text=str(item.stray_vertices))
        distance.label(text=item.distance_label)
        main.label(text=item.main_mesh or "—")


class CHARACTERDESIGNER_OT_inspect_weight_islands(Operator):
    bl_idname = "character_designer.inspect_weight_islands"
    bl_label = "Inspect Bone Influences"
    bl_description = "List used Deform bones and suspect weight islands without changing weights or bone selection"
    bl_options = {"REGISTER"}

    rows: CollectionProperty(type=CHARACTERDESIGNER_PG_weight_inspection_row, options={"HIDDEN", "SKIP_SAVE"})
    active_index: IntProperty(default=-1, min=-1, options={"HIDDEN", "SKIP_SAVE"})
    target_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    unweighted_count: IntProperty(default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
    suspect_count: IntProperty(default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
    reference_count: IntProperty(default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
    skipped_count: IntProperty(default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
    source_notice: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.mode in WRITE_MODES and context.view_layer is not None

    def _inspect(self, context):
        prepared = build_inspection(context)
        if not prepared.scan.ok:
            raise WeightCleanupError(format_cleanup_errors(prepared.scan.errors))
        self.rows.clear()
        self.target_name = prepared.target_name
        self.unweighted_count = prepared.unweighted_count
        self.suspect_count = len(prepared.scan.suspects)
        self.reference_count = len(prepared.reference_names)
        self.skipped_count = len(prepared.skipped_references)
        self.source_notice = " ".join(prepared.warnings)
        self.active_index = -1
        for record in sorted(prepared.scan.records,
                             key=lambda value: (value.status == "OK", value.bone_name)):
            item = self.rows.add()
            item.name = item.bone_name = record.bone_name
            item.target_name = prepared.target_name
            item.context_token = prepared.context_token
            item.suspect_vertices = record.suspect_vertex_count
            item.distant_vertices = record.distant_vertex_count
            item.stray_vertices = record.stray_vertex_count
            item.target_vertices = record.target_vertex_count
            item.main_mesh = record.main_mesh_name or ""
            item.status = record.status
            item.reason = record.reason
            if record.distance is None:
                item.distance_label = "—"
            else:
                item.distance_label = f"{record.distance:.3g} BU"
                ratio = getattr(record, "distance_ratio", None)
                if ratio is not None:
                    item.distance_label += f" ({ratio:.2g}×)"
        return prepared

    def invoke(self, context, _event):
        try:
            self._inspect(context)
        except (WeightCleanupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_popup(self, width=700)

    def execute(self, context):
        try:
            self._inspect(context)
        except (WeightCleanupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{self.target_name}: {self.suspect_count} suspect bones; "
                    f"{self.unweighted_count} unweighted vertices. Weights unchanged.")
        return {"FINISHED"}

    def draw(self, _context):
        layout = self.layout
        layout.label(text=f"Inspect: {self.target_name}", icon="VIEWZOOM")
        layout.label(text=f"{self.suspect_count} suspect bones · {self.reference_count} reference Meshes")
        bone, distant, stray, distance, main = _inspection_columns(layout.row())
        bone.label(text="Bone")
        distant.label(text="Far")
        stray.label(text="Stray")
        distance.label(text="Distance / bone length")
        main.label(text="Main Mesh")
        layout.template_list("CHARACTERDESIGNER_UL_weight_inspection", "", self, "rows",
                             self, "active_index", rows=min(10, max(3, len(self.rows))))
        if 0 <= self.active_index < len(self.rows):
            item = self.rows[self.active_index]
            layout.label(text=f"{item.bone_name}: {item.reason}", icon="INFO")
        layout.label(text="Click a suspect bone name to locate its vertices.")
        layout.label(text="Distance alone is a warning; cleanup needs your selection.", icon="INFO")
        layout.label(text=f"Unweighted: {self.unweighted_count} · use Fill Unweighted separately")
        if self.source_notice:
            layout.label(text=self.source_notice, icon="INFO")
        if self.skipped_count:
            layout.label(text=f"Excluded {self.skipped_count} unsafe reference Meshes", icon="INFO")


class CHARACTERDESIGNER_OT_locate_weight_island(Operator):
    bl_idname = "character_designer.locate_weight_island"
    bl_label = "Locate Bone Weights"
    bl_description = "Locate this bone's suspect vertices and activate its group; weights remain unchanged"
    bl_options = {"REGISTER", "UNDO"}

    target_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    bone_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    context_token: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.mode in WRITE_MODES and context.view_layer is not None

    def execute(self, context):
        try:
            locate_weight_group(context, self.target_name, self.bone_name,
                                context_token=self.context_token)
        except (WeightCleanupError, ValueError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Selected {self.bone_name}; weights unchanged.")
        return {"FINISHED"}


class CHARACTERDESIGNER_PT_weight_cleanup(Panel):
    bl_label = "Weight Cleanup"
    bl_idname = "CHARACTERDESIGNER_PT_weight_cleanup"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_WEIGHT

    def draw(self, context):
        layout = self.layout
        layout.operator("character_designer.fill_unweighted", icon="GROUP_VERTEX")
        layout.operator("character_designer.clear_bone_influences", icon="BRUSH_DATA")
        if context.mode == "EDIT_MESH":
            layout.label(text="Use Object or Weight Paint Mode to change weights.", icon="INFO")


CLASSES = (
    CHARACTERDESIGNER_PG_weight_inspection_row,
    CHARACTERDESIGNER_PG_weight_keep_row,
    CHARACTERDESIGNER_UL_weight_inspection,
    CHARACTERDESIGNER_UL_weight_keep,
    CHARACTERDESIGNER_OT_clean_weight_islands,
    CHARACTERDESIGNER_OT_fill_unweighted,
    CHARACTERDESIGNER_OT_clear_bone_influences,
    CHARACTERDESIGNER_OT_find_unweighted,
    CHARACTERDESIGNER_OT_inspect_weight_islands,
    CHARACTERDESIGNER_OT_locate_weight_island,
    CHARACTERDESIGNER_PT_weight_cleanup,
)
