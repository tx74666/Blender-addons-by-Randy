"""Safe, directed Vertex Group weight symmetry for paired deform bones.

The tool copies either the active side-named Deform group or a same-side Pose
bone selection to the corresponding opposite groups.  It does not run
Blender's bidirectional Weight Mirror operator, normalize other groups, or
guess through asymmetric geometry.  A multi-bone operation is planned and
validated as one transaction before the first Vertex Group is changed.
"""

from dataclasses import dataclass, replace
import math
import re
import traceback

import bpy
from bpy.props import FloatProperty
from bpy.types import Operator, Panel
from mathutils import Vector
from mathutils.kdtree import KDTree

from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_WEIGHT, active_ui_page


SUPPORTED_CONTEXT_MODES = frozenset({"OBJECT", "POSE", "PAINT_WEIGHT"})
SIDE_NAME_PATTERN = re.compile(r"^(?P<stem>.+)\.(?P<side>[LR])$")
AUTO_TOLERANCE_SCALE = 1.0e-5
AUTO_TOLERANCE_FLOOR = 1.0e-7
WEIGHT_TOLERANCE = 1.0e-6
WEIGHT_EPSILON = 1.0e-8
REMOVE_CHUNK_SIZE = 32768


class WeightSymmetryError(ValueError):
    """A safe, artist-facing preflight or directed-copy failure."""


class WeightSymmetryRollbackError(RuntimeError):
    """The operation failed and its exact Vertex Group state could not return."""


@dataclass(frozen=True)
class VertexGroupState:
    """One immutable Vertex Group definition and its base-Mesh memberships."""

    name: str
    index: int
    lock_weight: bool
    weights: tuple


@dataclass(frozen=True)
class WeightSymmetryPlan:
    """A fully validated, mutation-free Active Group -> Opposite plan."""

    mesh_obj: object
    armature_obj: object
    source_name: str
    target_name: str
    source_side: int
    tolerance: float
    source_indices: tuple
    target_indices: tuple
    center_indices: tuple
    pairs: tuple
    source_after: tuple
    target_after: tuple
    group_snapshot: tuple
    active_group_index: int
    target_existed: bool
    paired_count: int
    changed_count: int
    cleared_count: int
    affected_count: int


@dataclass(frozen=True)
class WeightSymmetryResult:
    """Small stable result returned by the public copy helper."""

    source_name: str
    target_name: str
    paired: int
    changed: int
    cleared: int
    affected: int


@dataclass(frozen=True)
class WeightSymmetryBatchPlan:
    """One immutable, fully preflighted multi-bone transaction."""

    mesh_obj: object
    armature_obj: object
    plans: tuple
    group_snapshot: tuple
    active_group_index: int
    source_side: int
    tolerance: float
    affected_count: int


@dataclass(frozen=True)
class WeightSymmetryBatchResult:
    """Compact result for one active-group or multi-bone transaction."""

    source_names: tuple
    target_names: tuple
    paired: int
    changed: int
    cleared: int
    affected: int


def _strict_opposite_name(name):
    match = SIDE_NAME_PATTERN.fullmatch(name or "")
    if match is None:
        raise WeightSymmetryError(
            f'Active group or Pose bone "{name or "<none>"}" must end exactly '
            'in ".L" or ".R".'
        )
    opposite = "R" if match.group("side") == "L" else "L"
    return f'{match.group("stem")}.{opposite}'


def _strict_side(name):
    """Return the literal L/R suffix after enforcing the public name contract."""

    match = SIDE_NAME_PATTERN.fullmatch(name or "")
    if match is None:
        _strict_opposite_name(name)
    return match.group("side")


def _active_vertex_group(mesh_obj):
    groups = mesh_obj.vertex_groups
    if not groups:
        return None
    index = int(groups.active_index)
    if 0 <= index < len(groups):
        return groups[index]
    return None


def _armature_modifiers(mesh_obj):
    return tuple(
        modifier
        for modifier in mesh_obj.modifiers
        if modifier.type == "ARMATURE"
        and modifier.object is not None
        and modifier.object.type == "ARMATURE"
    )


def _mesh_is_bound_to(mesh_obj, armature_obj):
    return any(
        modifier.object is armature_obj
        for modifier in _armature_modifiers(mesh_obj)
    )


def _selected_bound_mesh(context, armature_obj, source_name):
    selected = tuple(
        obj
        for obj in (context.selected_objects or ())
        if obj.type == "MESH"
        and _mesh_is_bound_to(obj, armature_obj)
        and obj.vertex_groups.get(source_name) is not None
    )
    if len(selected) == 1:
        return selected[0]
    if len(selected) > 1:
        raise WeightSymmetryError(
            "More than one selected bound Mesh owns the active bone group; "
            "select exactly one Mesh with the Armature."
        )

    candidates = tuple(
        obj
        for obj in context.view_layer.objects
        if obj.type == "MESH"
        and _mesh_is_bound_to(obj, armature_obj)
        and obj.vertex_groups.get(source_name) is not None
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise WeightSymmetryError(
            f'No bound Mesh has source Vertex Group "{source_name}".'
        )
    raise WeightSymmetryError(
        "More than one visible bound Mesh owns the active bone group; select "
        "exactly one Mesh before copying weights."
    )


def _armature_for_mesh(mesh_obj, source_name, target_name, preferred=None):
    modifiers = _armature_modifiers(mesh_obj)
    if preferred is not None:
        modifiers = tuple(
            modifier for modifier in modifiers if modifier.object is preferred
        )
    candidates = tuple(
        dict.fromkeys(
            modifier.object
            for modifier in modifiers
            if modifier.object.data.bones.get(source_name) is not None
            and modifier.object.data.bones.get(target_name) is not None
        )
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise WeightSymmetryError(
            f'The Mesh has no Armature containing both "{source_name}" and '
            f'"{target_name}".'
        )
    raise WeightSymmetryError(
        "The Mesh has multiple Armatures containing this bone pair; keep one "
        "unambiguous Armature modifier."
    )


def _resolve_active_source(context):
    active = context.view_layer.objects.active
    pose_bone = getattr(context, "active_pose_bone", None)

    if active is not None and active.type == "MESH":
        mesh_obj = active
        active_group = _active_vertex_group(mesh_obj)
        if active_group is not None:
            source_name = active_group.name
        elif pose_bone is not None:
            source_name = pose_bone.name
        else:
            raise WeightSymmetryError(
                "Make a side-named Vertex Group active, or select one Pose bone."
            )
        target_name = _strict_opposite_name(source_name)
        preferred = getattr(pose_bone, "id_data", None) if pose_bone else None
        armature_obj = _armature_for_mesh(
            mesh_obj,
            source_name,
            target_name,
            preferred=preferred,
        )
        return mesh_obj, armature_obj, source_name, target_name

    if active is not None and active.type == "ARMATURE" and pose_bone is not None:
        armature_obj = active
        if getattr(pose_bone, "id_data", None) is not armature_obj:
            raise WeightSymmetryError("The active Pose bone belongs to another Armature.")
        source_name = pose_bone.name
        target_name = _strict_opposite_name(source_name)
        mesh_obj = _selected_bound_mesh(context, armature_obj, source_name)
        return mesh_obj, armature_obj, source_name, target_name

    raise WeightSymmetryError(
        "Make one bound Mesh active, or enter Pose Mode with one active side bone."
    )


def _selected_pose_bones(context, *, armature_obj=None):
    """Return selected Pose bones deterministically in Armature bone order.

    ``context.selected_pose_bones`` is not populated consistently by every
    Weight Paint workspace.  Reading the selected Bone flags from the bound
    Armature is the stable fallback and does not mutate the scene.
    """

    selected = tuple(getattr(context, "selected_pose_bones", None) or ())
    candidates = []
    seen = set()
    for pose_bone in selected:
        owner = getattr(pose_bone, "id_data", None)
        if armature_obj is not None and owner is not armature_obj:
            continue
        key = (id(owner), pose_bone.name)
        if key not in seen:
            seen.add(key)
            candidates.append(pose_bone)

    if armature_obj is not None:
        for pose_bone in armature_obj.pose.bones:
            if not pose_bone.select:
                continue
            key = (id(armature_obj), pose_bone.name)
            if key not in seen:
                seen.add(key)
                candidates.append(pose_bone)

    if armature_obj is not None:
        order = {bone.name: index for index, bone in enumerate(armature_obj.data.bones)}
        candidates.sort(key=lambda bone: order.get(bone.name, len(order)))
    return tuple(candidates)


def _selected_bound_mesh_for_sources(context, armature_obj, source_names):
    """Resolve one bound Mesh for an entire selected bone set."""

    selected_bound = tuple(
        obj
        for obj in (context.selected_objects or ())
        if obj.type == "MESH" and _mesh_is_bound_to(obj, armature_obj)
    )
    if len(selected_bound) == 1:
        return selected_bound[0]
    if len(selected_bound) > 1:
        raise WeightSymmetryError(
            "More than one selected Mesh is bound to the active Armature; "
            "select exactly one target Mesh."
        )

    candidates = tuple(
        obj
        for obj in context.view_layer.objects
        if obj.type == "MESH"
        and _mesh_is_bound_to(obj, armature_obj)
        and all(obj.vertex_groups.get(name) is not None for name in source_names)
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        joined = ", ".join(source_names)
        raise WeightSymmetryError(
            f"No single bound Mesh owns every selected source group: {joined}."
        )
    raise WeightSymmetryError(
        "More than one visible bound Mesh owns all selected source groups; "
        "select exactly one target Mesh."
    )


def _resolve_selected_sources(context):
    """Resolve active-group fallback or an intentional Pose-bone selection."""

    active = context.view_layer.objects.active
    if active is not None and active.type == "ARMATURE":
        armature_obj = active
        selected = _selected_pose_bones(context, armature_obj=armature_obj)
        if not selected:
            active_pose_bone = getattr(context, "active_pose_bone", None)
            if active_pose_bone is not None:
                selected = (active_pose_bone,)
        if not selected:
            raise WeightSymmetryError("Select at least one side-named Pose bone.")
        source_names = tuple(bone.name for bone in selected)
        mesh_obj = _selected_bound_mesh_for_sources(
            context,
            armature_obj,
            source_names,
        )
        return mesh_obj, armature_obj, source_names

    if active is not None and active.type == "MESH" and context.mode == "PAINT_WEIGHT":
        selected_by_armature = []
        for modifier in _armature_modifiers(active):
            armature_obj = modifier.object
            selected = _selected_pose_bones(context, armature_obj=armature_obj)
            if len(selected) >= 2:
                selected_by_armature.append((armature_obj, selected))
        if len(selected_by_armature) > 1:
            raise WeightSymmetryError(
                "Selected Pose bones belong to multiple bound Armatures."
            )
        if selected_by_armature:
            armature_obj, selected = selected_by_armature[0]
            return active, armature_obj, tuple(bone.name for bone in selected)

    mesh_obj, armature_obj, source_name, _target_name = _resolve_active_source(context)
    return mesh_obj, armature_obj, (source_name,)


def _object_is_editable(context, obj):
    return (
        obj is not None
        and context.view_layer.objects.get(obj.name) is obj
        and not obj.hide_get(view_layer=context.view_layer)
        and not obj.hide_viewport
    )


def _capture_vertex_groups(mesh_obj):
    states = [
        VertexGroupState(
            name=group.name,
            index=group.index,
            lock_weight=bool(group.lock_weight),
            weights=(),
        )
        for group in mesh_obj.vertex_groups
    ]
    weights = [[] for _state in states]
    for vertex in mesh_obj.data.vertices:
        for membership in vertex.groups:
            if 0 <= membership.group < len(states):
                weights[membership.group].append(
                    (vertex.index, float(membership.weight))
                )
    return tuple(
        VertexGroupState(
            name=state.name,
            index=state.index,
            lock_weight=state.lock_weight,
            weights=tuple(weights[state.index]),
        )
        for state in states
    )


def _state_map(states):
    return {state.name: state for state in states}


def _weight_map(state):
    return dict(state.weights) if state is not None else {}


def _remove_indices(group, indices):
    indices = tuple(indices)
    for start in range(0, len(indices), REMOVE_CHUNK_SIZE):
        group.remove(indices[start : start + REMOVE_CHUNK_SIZE])


def _clear_group(group, vertex_count):
    was_locked = bool(group.lock_weight)
    group.lock_weight = False
    try:
        for start in range(0, vertex_count, REMOVE_CHUNK_SIZE):
            group.remove(
                tuple(range(start, min(start + REMOVE_CHUNK_SIZE, vertex_count)))
            )
    finally:
        group.lock_weight = was_locked


def _restore_vertex_groups(mesh_obj, states, active_group_index):
    expected_names = tuple(state.name for state in states)
    expected_set = set(expected_names)

    for group in reversed(tuple(mesh_obj.vertex_groups)):
        if group.name not in expected_set:
            mesh_obj.vertex_groups.remove(group)

    if tuple(group.name for group in mesh_obj.vertex_groups) != expected_names:
        for group in reversed(tuple(mesh_obj.vertex_groups)):
            mesh_obj.vertex_groups.remove(group)
        for state in states:
            mesh_obj.vertex_groups.new(name=state.name)

    vertex_count = len(mesh_obj.data.vertices)
    for state in states:
        group = mesh_obj.vertex_groups.get(state.name)
        if group is None or group.index != state.index:
            raise RuntimeError("Vertex Group order changed unexpectedly.")
        group.lock_weight = False
        _clear_group(group, vertex_count)
        for vertex_index, weight in state.weights:
            group.add((vertex_index,), weight, "REPLACE")
        group.lock_weight = state.lock_weight

    if mesh_obj.vertex_groups:
        mesh_obj.vertex_groups.active_index = min(
            max(0, active_group_index),
            len(mesh_obj.vertex_groups) - 1,
        )
    mesh_obj.data.update()
    if _capture_vertex_groups(mesh_obj) != states:
        raise RuntimeError("Vertex Group rollback could not be verified exactly.")


def _automatic_tolerance(mesh_obj):
    coordinates = tuple(vertex.co for vertex in mesh_obj.data.vertices)
    if not coordinates:
        raise WeightSymmetryError("The Mesh has no vertices to pair.")
    minimum = Vector(
        (
            min(co.x for co in coordinates),
            min(co.y for co in coordinates),
            min(co.z for co in coordinates),
        )
    )
    maximum = Vector(
        (
            max(co.x for co in coordinates),
            max(co.y for co in coordinates),
            max(co.z for co in coordinates),
        )
    )
    return max(AUTO_TOLERANCE_FLOOR, (maximum - minimum).length * AUTO_TOLERANCE_SCALE)


def _bone_source_side(mesh_obj, armature_obj, source_name, target_name, tolerance):
    source_bone = armature_obj.data.bones.get(source_name)
    target_bone = armature_obj.data.bones.get(target_name)
    if source_bone is None or target_bone is None:
        raise WeightSymmetryError("The source and opposite Pose bones must both exist.")
    if not source_bone.use_deform or not target_bone.use_deform:
        raise WeightSymmetryError("Both source and opposite bones must be Deform bones.")

    mesh_from_armature = (
        mesh_obj.matrix_world.inverted_safe() @ armature_obj.matrix_world
    )
    source_head_x = (mesh_from_armature @ source_bone.head_local).x
    target_head_x = (mesh_from_armature @ target_bone.head_local).x

    # Bone heads are the authoritative direction whenever they leave the
    # center line.  Side bones such as clavicles may legitimately share a
    # center head, so only that otherwise-ambiguous case falls back to the
    # bone's rest midpoint.  Each bone is resolved independently.
    source_used_midpoint = abs(source_head_x) <= tolerance
    target_used_midpoint = abs(target_head_x) <= tolerance
    source_x = (
        (
            mesh_from_armature
            @ ((source_bone.head_local + source_bone.tail_local) * 0.5)
        ).x
        if source_used_midpoint
        else source_head_x
    )
    target_x = (
        (
            mesh_from_armature
            @ ((target_bone.head_local + target_bone.tail_local) * 0.5)
        ).x
        if target_used_midpoint
        else target_head_x
    )
    if abs(source_x) <= tolerance:
        location = "rest midpoint" if source_used_midpoint else "head"
        raise WeightSymmetryError(
            f'Bone "{source_name}" has its {location} on the Mesh center line; '
            "its source half is ambiguous."
        )
    if abs(target_x) <= tolerance:
        location = "rest midpoint" if target_used_midpoint else "head"
        raise WeightSymmetryError(
            f'Bone "{target_name}" has its {location} on the Mesh center line; '
            "the opposite half is ambiguous."
        )
    if source_x * target_x >= 0.0:
        raise WeightSymmetryError(
            f'Bones "{source_name}" and "{target_name}" do not resolve to '
            "opposite Mesh-local X halves from their heads/rest midpoints."
        )
    return 1 if source_x > 0.0 else -1


def _classify_mesh_halves(mesh_obj, source_side, tolerance):
    source_indices = []
    target_indices = []
    center_indices = []
    for vertex in mesh_obj.data.vertices:
        signed_x = vertex.co.x * source_side
        if signed_x > tolerance:
            source_indices.append(vertex.index)
        elif signed_x < -tolerance:
            target_indices.append(vertex.index)
        else:
            center_indices.append(vertex.index)

    if not source_indices or not target_indices:
        raise WeightSymmetryError(
            "The base Mesh does not contain editable vertices on both local-X "
            "halves. Apply a half-Mesh Mirror before copying base weights."
        )
    return tuple(source_indices), tuple(target_indices), tuple(center_indices)


def _spatial_pairs(mesh_obj, source_indices, target_indices, tolerance):
    """Pair only the source group's weighted support, not the entire character.

    A character Mesh may contain intentionally asymmetric face, hair, or outfit
    vertices in the same datablock.  Those unrelated vertices must not block a
    directed forearm copy.  Every supplied source-support vertex still requires
    exactly one reflected target, and targets remain one-to-one.
    """

    if not source_indices:
        raise WeightSymmetryError(
            "The active source group has no weighted vertices on its bone-defined "
            "Mesh half."
        )

    tree = KDTree(len(target_indices))
    vertices = mesh_obj.data.vertices
    for vertex_index in target_indices:
        tree.insert(vertices[vertex_index].co, vertex_index)
    tree.balance()

    pairs = []
    used_targets = set()
    unmatched = []
    ambiguous = []
    for source_index in source_indices:
        reflected = vertices[source_index].co.copy()
        reflected.x = -reflected.x
        hits = sorted(
            tree.find_range(reflected, tolerance),
            key=lambda item: (item[2], item[1]),
        )
        if not hits:
            unmatched.append(source_index)
            continue
        if len(hits) != 1:
            ambiguous.append(source_index)
            continue
        target_index = hits[0][1]
        if target_index in used_targets:
            ambiguous.append(source_index)
            continue
        used_targets.add(target_index)
        pairs.append((source_index, target_index))

    if ambiguous:
        raise WeightSymmetryError(
            "Spatial symmetry is ambiguous near source vertex "
            f"{ambiguous[0]} ({len(ambiguous)} ambiguous). No weights were changed."
        )
    if unmatched:
        raise WeightSymmetryError(
            f"Spatial symmetry could not pair source vertex {unmatched[0]}; "
            f"{len(unmatched)} weighted source vert"
            f"{'ices are' if len(unmatched) != 1 else 'ex is'} unmatched. "
            "No weights were changed."
        )
    return tuple(sorted(pairs))


def _maps_semantically_differ(first, second, vertex_index):
    return abs(
        float(first.get(vertex_index, 0.0))
        - float(second.get(vertex_index, 0.0))
    ) > WEIGHT_TOLERANCE


def _validate_deform_budget(
    snapshot,
    armature_obj,
    source_name,
    target_name,
    source_after,
    target_after,
):
    return _validate_deform_budget_batch(
        snapshot,
        armature_obj,
        {
            source_name: tuple(source_after),
            target_name: tuple(target_after),
        },
    )


def _validate_deform_budget_batch(snapshot, armature_obj, group_changes):
    """Validate the final per-vertex Deform total for the whole transaction.

    A multi-bone repair can legitimately move a vertex's existing budget among
    several paired groups.  Checking the combined final state prevents both
    false failures from pair-by-pair validation and hidden normalization.
    """

    states = _state_map(snapshot)
    after_maps = {name: dict(weights) for name, weights in group_changes.items()}
    affected = set()
    for name, after in after_maps.items():
        before = _weight_map(states.get(name))
        affected.update(
            vertex_index
            for vertex_index in set(before) | set(after)
            if _maps_semantically_differ(before, after, vertex_index)
        )

    deform_names = tuple(
        bone.name for bone in armature_obj.data.bones if bone.use_deform
    )
    before_maps = {
        name: _weight_map(states.get(name)) for name in deform_names
    }
    failures = []
    for vertex_index in sorted(affected):
        before_total = sum(
            weights.get(vertex_index, 0.0) for weights in before_maps.values()
        )
        if not math.isfinite(before_total):
            raise WeightSymmetryError(
                f"Vertex {vertex_index} has a non-finite Deform weight total."
            )
        after_total = sum(
            after_maps.get(name, before_maps[name]).get(vertex_index, 0.0)
            for name in deform_names
        )
        if not math.isfinite(after_total):
            raise WeightSymmetryError(
                f"Vertex {vertex_index} would have a non-finite Deform weight total."
            )
        if abs(after_total - before_total) > WEIGHT_TOLERANCE:
            failures.append((vertex_index, before_total, after_total))

    if failures:
        vertex_index, before_total, after_total = failures[0]
        raise WeightSymmetryError(
            "Copying this group would change the Deform weight total at "
            f"{len(failures)} vert{'ices' if len(failures) != 1 else 'ex'} "
            f"(first: {vertex_index}, {before_total:.6g} -> {after_total:.6g}). "
            "No weights were changed; repair or normalize that region separately."
        )
    return tuple(sorted(affected))


def _preflight_mesh(context, mesh_obj):
    if context.mode not in SUPPORTED_CONTEXT_MODES:
        raise WeightSymmetryError(
            "Use Object Mode, Pose Mode, or Weight Paint Mode."
        )
    if not _object_is_editable(context, mesh_obj):
        raise WeightSymmetryError("The bound Mesh must be visible in this View Layer.")
    if mesh_obj.library is not None and mesh_obj.override_library is None:
        raise WeightSymmetryError("The linked Mesh object is read-only.")
    if mesh_obj.data.library is not None and mesh_obj.data.override_library is None:
        raise WeightSymmetryError("The linked Mesh data is read-only.")
    if mesh_obj.data.users != 1:
        raise WeightSymmetryError(
            f'The Mesh data "{mesh_obj.data.name}" is shared by '
            f"{mesh_obj.data.users} objects. Make it Single User first."
        )
    if mesh_obj.mode == "EDIT" or mesh_obj.data.is_editmode:
        raise WeightSymmetryError("Leave Mesh Edit Mode before copying weights.")
    if not mesh_obj.data.vertices:
        raise WeightSymmetryError("The Mesh has no vertices to pair.")


def _validated_tolerance(mesh_obj, match_tolerance):
    tolerance = float(match_tolerance)
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise WeightSymmetryError("Match Tolerance must be a finite positive value.")
    return tolerance if tolerance else _automatic_tolerance(mesh_obj)


def _build_plan_for_names(
    mesh_obj,
    armature_obj,
    source_name,
    target_name,
    snapshot,
    active_group_index,
    tolerance,
):
    """Build one pair from an already captured batch snapshot, without writes."""

    source_group = mesh_obj.vertex_groups.get(source_name)
    if source_group is None:
        raise WeightSymmetryError(
            f'Source Vertex Group "{source_name}" does not exist on the Mesh.'
        )
    target_group = mesh_obj.vertex_groups.get(target_name)
    if source_group.lock_weight:
        raise WeightSymmetryError(
            f'Unlock source Vertex Group "{source_name}" before copying weights.'
        )
    if target_group is not None and target_group.lock_weight:
        raise WeightSymmetryError(
            f'Unlock target Vertex Group "{target_name}" before overwriting it.'
        )

    source_side = _bone_source_side(
        mesh_obj,
        armature_obj,
        source_name,
        target_name,
        tolerance,
    )
    source_indices, target_indices, center_indices = _classify_mesh_halves(
        mesh_obj,
        source_side,
        tolerance,
    )

    states = _state_map(snapshot)
    source_before = _weight_map(states[source_name])
    target_before = _weight_map(states.get(target_name))
    source_set = set(source_indices)
    target_set = set(target_indices)
    center_set = set(center_indices)

    weighted_source_indices = tuple(
        sorted(
            vertex_index
            for vertex_index, weight in source_before.items()
            if vertex_index in source_set and weight > WEIGHT_EPSILON
        )
    )
    if not weighted_source_indices:
        side_label = "+X" if source_side > 0 else "-X"
        raise WeightSymmetryError(
            f'Source group "{source_name}" has no usable weights on its '
            f"bone-defined {side_label} half."
        )
    for vertex_index, weight in source_before.items():
        if not math.isfinite(weight) or weight < 0.0 or weight > 1.0:
            raise WeightSymmetryError(
                f'Source group "{source_name}" has invalid weight {weight!r} '
                f"at vertex {vertex_index}."
            )

    pairs = _spatial_pairs(
        mesh_obj,
        weighted_source_indices,
        target_indices,
        tolerance,
    )

    source_after = {
        vertex_index: weight
        for vertex_index, weight in source_before.items()
        if vertex_index not in target_set
    }
    target_after = {
        vertex_index: weight
        for vertex_index, weight in target_before.items()
        if vertex_index in center_set
    }
    for source_index, target_index in pairs:
        if source_index in source_before:
            target_after[target_index] = source_before[source_index]

    changed_count = sum(
        _maps_semantically_differ(target_before, target_after, vertex_index)
        for vertex_index in target_set
    )
    cleared_count = sum(
        weight > WEIGHT_EPSILON
        for vertex_index, weight in source_before.items()
        if vertex_index in target_set
    )
    cleared_count += sum(
        weight > WEIGHT_EPSILON
        for vertex_index, weight in target_before.items()
        if vertex_index in source_set
    )
    paired_count = len(pairs)
    return WeightSymmetryPlan(
        mesh_obj=mesh_obj,
        armature_obj=armature_obj,
        source_name=source_name,
        target_name=target_name,
        source_side=source_side,
        tolerance=tolerance,
        source_indices=source_indices,
        target_indices=target_indices,
        center_indices=center_indices,
        pairs=pairs,
        source_after=tuple(sorted(source_after.items())),
        target_after=tuple(sorted(target_after.items())),
        group_snapshot=snapshot,
        active_group_index=active_group_index,
        target_existed=target_group is not None,
        paired_count=paired_count,
        changed_count=changed_count,
        cleared_count=cleared_count,
        affected_count=0,
    )


def _group_changes_for_plans(plans):
    changes = {}
    for plan in plans:
        for name, weights in (
            (plan.source_name, plan.source_after),
            (plan.target_name, plan.target_after),
        ):
            if name in changes:
                raise WeightSymmetryError(
                    f'Vertex Group "{name}" would be written by more than one '
                    "selected pair. Select bones from only one side."
                )
            changes[name] = weights
    return changes


def _build_batch_for_sources(
    context,
    mesh_obj,
    armature_obj,
    source_names,
    *,
    match_tolerance=0.0,
):
    """Build and fully validate one transaction for explicit source names."""

    _preflight_mesh(context, mesh_obj)
    source_names = tuple(source_names)
    if not source_names:
        raise WeightSymmetryError("Select at least one side-named Deform bone.")
    if len(set(source_names)) != len(source_names):
        raise WeightSymmetryError("The selected source bone list contains duplicates.")

    suffixes = {_strict_side(name) for name in source_names}
    if len(suffixes) != 1:
        raise WeightSymmetryError(
            "Select Deform bones from only one named side (.L or .R)."
        )
    target_names = tuple(_strict_opposite_name(name) for name in source_names)
    source_set = set(source_names)
    target_set = set(target_names)
    if len(target_set) != len(target_names) or source_set & target_set:
        raise WeightSymmetryError(
            "Selected source and opposite groups overlap; select bones from only "
            "one side."
        )

    tolerance = _validated_tolerance(mesh_obj, match_tolerance)
    snapshot = _capture_vertex_groups(mesh_obj)
    active_group_index = int(mesh_obj.vertex_groups.active_index)
    plans = tuple(
        _build_plan_for_names(
            mesh_obj,
            armature_obj,
            source_name,
            target_name,
            snapshot,
            active_group_index,
            tolerance,
        )
        for source_name, target_name in zip(source_names, target_names)
    )
    physical_sides = {plan.source_side for plan in plans}
    if len(physical_sides) != 1:
        raise WeightSymmetryError(
            "Selected bones do not resolve to one common Mesh-local X side."
        )

    group_changes = _group_changes_for_plans(plans)
    affected = _validate_deform_budget_batch(
        snapshot,
        armature_obj,
        group_changes,
    )
    return WeightSymmetryBatchPlan(
        mesh_obj=mesh_obj,
        armature_obj=armature_obj,
        plans=plans,
        group_snapshot=snapshot,
        active_group_index=active_group_index,
        source_side=plans[0].source_side,
        tolerance=tolerance,
        affected_count=len(affected),
    )


def build_weight_symmetry_plan(context, *, match_tolerance=0.0):
    """Return a read-only, fully validated Active Group -> Opposite plan."""

    mesh_obj, armature_obj, source_name, _target_name = _resolve_active_source(context)
    batch = _build_batch_for_sources(
        context,
        mesh_obj,
        armature_obj,
        (source_name,),
        match_tolerance=match_tolerance,
    )
    plan = batch.plans[0]
    return replace(plan, affected_count=batch.affected_count)


def build_weight_symmetry_batch_plan(context, *, match_tolerance=0.0):
    """Return one fully validated selected-bones transaction.

    Pose Mode uses every selected Pose bone.  Weight Paint uses a selection of
    two or more Pose bones; otherwise it preserves the active Vertex Group
    workflow used by the original single-group tool.
    """

    if context.mode not in SUPPORTED_CONTEXT_MODES:
        raise WeightSymmetryError(
            "Use Object Mode, Pose Mode, or Weight Paint Mode."
        )
    mesh_obj, armature_obj, source_names = _resolve_selected_sources(context)
    return _build_batch_for_sources(
        context,
        mesh_obj,
        armature_obj,
        source_names,
        match_tolerance=match_tolerance,
    )


def _states_match_expected(mesh_obj, plan):
    after = _capture_vertex_groups(mesh_obj)
    before_map = _state_map(plan.group_snapshot)
    after_map = _state_map(after)
    expected_names = tuple(state.name for state in plan.group_snapshot)
    if not plan.target_existed:
        expected_names += (plan.target_name,)
    if tuple(state.name for state in after) != expected_names:
        raise WeightSymmetryError("The operation changed Vertex Group definitions.")

    for name, before in before_map.items():
        current = after_map.get(name)
        if current is None or current.index != before.index:
            raise WeightSymmetryError("The operation changed Vertex Group order.")
        if current.lock_weight != before.lock_weight:
            raise WeightSymmetryError(
                f'The operation changed the lock state of "{name}".'
            )
        if name not in {plan.source_name, plan.target_name} and current != before:
            raise WeightSymmetryError(
                f'The operation changed protected Vertex Group "{name}".'
            )

    target_state = after_map.get(plan.target_name)
    if target_state is None:
        raise WeightSymmetryError(
            f'Target Vertex Group "{plan.target_name}" was not created.'
        )
    if plan.target_existed:
        before_target = before_map[plan.target_name]
        if target_state.index != before_target.index:
            raise WeightSymmetryError("The target Vertex Group index changed.")
        if target_state.lock_weight != before_target.lock_weight:
            raise WeightSymmetryError("The target Vertex Group lock state changed.")
    elif target_state.index != len(plan.group_snapshot):
        raise WeightSymmetryError("The new target Vertex Group was not appended safely.")

    source_actual = _weight_map(after_map[plan.source_name])
    target_actual = _weight_map(target_state)
    source_expected = dict(plan.source_after)
    target_expected = dict(plan.target_after)
    if source_actual.keys() != source_expected.keys() or any(
        abs(source_actual[index] - expected) > WEIGHT_TOLERANCE
        for index, expected in source_expected.items()
    ):
        raise WeightSymmetryError("Source-side cleanup verification failed.")
    if target_actual.keys() != target_expected.keys() or any(
        abs(target_actual[index] - expected) > WEIGHT_TOLERANCE
        for index, expected in target_expected.items()
    ):
        raise WeightSymmetryError("Opposite-group copy verification failed.")


def _states_match_batch_expected(mesh_obj, batch):
    """Verify every group definition and membership after a batch commit."""

    after = _capture_vertex_groups(mesh_obj)
    before_map = _state_map(batch.group_snapshot)
    after_map = _state_map(after)
    new_targets = tuple(
        plan.target_name for plan in batch.plans if not plan.target_existed
    )
    expected_names = tuple(state.name for state in batch.group_snapshot) + new_targets
    if tuple(state.name for state in after) != expected_names:
        raise WeightSymmetryError("The operation changed Vertex Group definitions.")

    changes = _group_changes_for_plans(batch.plans)
    for name, before in before_map.items():
        current = after_map.get(name)
        if current is None or current.index != before.index:
            raise WeightSymmetryError("The operation changed Vertex Group order.")
        if current.lock_weight != before.lock_weight:
            raise WeightSymmetryError(
                f'The operation changed the lock state of "{name}".'
            )
        if name not in changes and current != before:
            raise WeightSymmetryError(
                f'The operation changed protected Vertex Group "{name}".'
            )

    first_new_index = len(batch.group_snapshot)
    for offset, target_name in enumerate(new_targets):
        target = after_map.get(target_name)
        if target is None or target.index != first_new_index + offset:
            raise WeightSymmetryError(
                f'New target Vertex Group "{target_name}" was not appended safely.'
            )
        if target.lock_weight:
            raise WeightSymmetryError(
                f'New target Vertex Group "{target_name}" became locked.'
            )

    for name, expected_items in changes.items():
        current = after_map.get(name)
        if current is None:
            raise WeightSymmetryError(
                f'Expected Vertex Group "{name}" is missing after the operation.'
            )
        actual = _weight_map(current)
        expected = dict(expected_items)
        if actual.keys() != expected.keys() or any(
            abs(actual[index] - weight) > WEIGHT_TOLERANCE
            for index, weight in expected.items()
        ):
            raise WeightSymmetryError(
                f'Weight-copy verification failed for Vertex Group "{name}".'
            )


def _commit_weight_symmetry_batch(batch, verify):
    """Commit preflighted group plans as one rollback-protected mutation."""

    mesh_obj = batch.mesh_obj
    if len(mesh_obj.data.vertices) == 0:
        raise WeightSymmetryError("The planned Mesh is no longer available.")
    if _capture_vertex_groups(mesh_obj) != batch.group_snapshot:
        raise WeightSymmetryError(
            "Vertex Groups changed after planning; run Copy to Opposite again."
        )

    try:
        # Check the complete destination set before creating or removing any
        # membership.  This keeps a late locked pair from causing partial work.
        for plan in batch.plans:
            source_group = mesh_obj.vertex_groups.get(plan.source_name)
            if source_group is None or source_group.lock_weight:
                raise WeightSymmetryError(
                    f'Source Vertex Group "{plan.source_name}" changed after planning.'
                )
            target_group = mesh_obj.vertex_groups.get(plan.target_name)
            if target_group is not None and target_group.lock_weight:
                raise WeightSymmetryError(
                    f'Target Vertex Group "{plan.target_name}" changed after planning.'
                )

        for plan in batch.plans:
            if mesh_obj.vertex_groups.get(plan.target_name) is None:
                created = mesh_obj.vertex_groups.new(name=plan.target_name)
                if created.name != plan.target_name:
                    raise WeightSymmetryError(
                        f'Could not create exact target group "{plan.target_name}".'
                    )

        snapshot_map = _state_map(batch.group_snapshot)
        for plan in batch.plans:
            source_group = mesh_obj.vertex_groups[plan.source_name]
            target_group = mesh_obj.vertex_groups[plan.target_name]
            source_before = _weight_map(snapshot_map[plan.source_name])
            target_before = _weight_map(snapshot_map.get(plan.target_name))
            target_set = set(plan.target_indices)
            source_set = set(plan.source_indices)
            _remove_indices(
                source_group,
                tuple(index for index in source_before if index in target_set),
            )
            _remove_indices(
                target_group,
                tuple(
                    index
                    for index in target_before
                    if index in source_set or index in target_set
                ),
            )
            for vertex_index, weight in plan.target_after:
                if vertex_index in target_set:
                    target_group.add((vertex_index,), weight, "REPLACE")

        if mesh_obj.vertex_groups:
            mesh_obj.vertex_groups.active_index = min(
                max(0, batch.active_group_index),
                len(mesh_obj.vertex_groups) - 1,
            )
        mesh_obj.data.update()
        verify(mesh_obj)
    except Exception as exc:
        try:
            _restore_vertex_groups(
                mesh_obj,
                batch.group_snapshot,
                batch.active_group_index,
            )
        except Exception as rollback_exc:
            raise WeightSymmetryRollbackError(
                "Weight symmetry failed and exact rollback also failed; inspect "
                f"the Mesh before continuing. Original error: {exc}; rollback: "
                f"{rollback_exc}"
            ) from rollback_exc
        if isinstance(exc, WeightSymmetryError):
            raise
        raise WeightSymmetryError(f"Weight symmetry was restored: {exc}") from exc


def apply_weight_symmetry_plan(plan):
    """Apply one validated plan atomically and return its compact result."""
    batch = WeightSymmetryBatchPlan(
        mesh_obj=plan.mesh_obj,
        armature_obj=plan.armature_obj,
        plans=(plan,),
        group_snapshot=plan.group_snapshot,
        active_group_index=plan.active_group_index,
        source_side=plan.source_side,
        tolerance=plan.tolerance,
        affected_count=plan.affected_count,
    )
    _commit_weight_symmetry_batch(
        batch,
        lambda mesh_obj: _states_match_expected(mesh_obj, plan),
    )

    return WeightSymmetryResult(
        source_name=plan.source_name,
        target_name=plan.target_name,
        paired=plan.paired_count,
        changed=plan.changed_count,
        cleared=plan.cleared_count,
        affected=plan.affected_count,
    )


def apply_weight_symmetry_batch_plan(batch):
    """Apply all selected pairs atomically and return one transaction result."""

    _commit_weight_symmetry_batch(
        batch,
        lambda mesh_obj: _states_match_batch_expected(mesh_obj, batch),
    )
    return WeightSymmetryBatchResult(
        source_names=tuple(plan.source_name for plan in batch.plans),
        target_names=tuple(plan.target_name for plan in batch.plans),
        paired=sum(plan.paired_count for plan in batch.plans),
        changed=sum(plan.changed_count for plan in batch.plans),
        cleared=sum(plan.cleared_count for plan in batch.plans),
        affected=batch.affected_count,
    )


def copy_active_group_to_opposite(context, *, match_tolerance=0.0):
    """Plan and atomically copy the active side group to its opposite group."""

    plan = build_weight_symmetry_plan(
        context,
        match_tolerance=match_tolerance,
    )
    return apply_weight_symmetry_plan(plan)


def copy_selected_groups_to_opposite(context, *, match_tolerance=0.0):
    """Copy an intentional Pose selection, with active-group fallback."""

    batch = build_weight_symmetry_batch_plan(
        context,
        match_tolerance=match_tolerance,
    )
    if len(batch.plans) == 1:
        raw_plan = batch.plans[0]
        plan = replace(raw_plan, affected_count=batch.affected_count)
        result = apply_weight_symmetry_plan(plan)
        return WeightSymmetryBatchResult(
            source_names=(result.source_name,),
            target_names=(result.target_name,),
            paired=result.paired,
            changed=result.changed,
            cleared=result.cleared,
            affected=result.affected,
        )
    return apply_weight_symmetry_batch_plan(batch)


class CHARACTERDESIGNER_OT_copy_weight_to_opposite(Operator):
    bl_idname = "character_designer.copy_weight_to_opposite"
    bl_label = "Copy Weight to Opposite"
    bl_description = (
        "Copy selected same-side Pose bones, or the active .L/.R Deform group, "
        "to opposite groups as one transaction; wrong-side assignments are "
        "cleaned only when per-vertex Deform totals remain unchanged"
    )
    bl_options = {"REGISTER", "UNDO"}

    match_tolerance: FloatProperty(
        name="Match Tolerance",
        description=(
            "Maximum local-space distance for an exact reflected vertex pair; "
            "zero chooses a Mesh-scale tolerance"
        ),
        default=0.0,
        min=0.0,
        soft_max=0.01,
        precision=6,
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        if context.view_layer is None or context.scene is None:
            return False
        if context.mode not in SUPPORTED_CONTEXT_MODES:
            cls.poll_message_set("Use Object Mode, Pose Mode, or Weight Paint Mode.")
            return False
        active = context.view_layer.objects.active
        if active is None or active.type not in {"MESH", "ARMATURE"}:
            cls.poll_message_set("Make one bound Mesh or Armature active.")
            return False
        return True

    def execute(self, context):
        try:
            result = copy_selected_groups_to_opposite(
                context,
                match_tolerance=self.match_tolerance,
            )
        except WeightSymmetryError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        except WeightSymmetryRollbackError as exc:
            traceback.print_exc()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Weight symmetry failed safely: {exc}")
            return {"CANCELLED"}

        if len(result.source_names) == 1:
            direction = f"{result.source_names[0]} -> {result.target_names[0]}"
        else:
            direction = f"{len(result.source_names)} bones -> opposite"
        self.report(
            {"INFO"},
            f"{direction}: paired {result.paired}, changed {result.changed}, "
            f"cleared {result.cleared}; other groups unchanged.",
        )
        return {"FINISHED"}


def _selected_pose_count_for_ui(context):
    """Return an intentional multi-bone count without running heavy preflight."""

    if context.mode not in {"POSE", "PAINT_WEIGHT"} or context.view_layer is None:
        return 0
    active = context.view_layer.objects.active
    if active is not None and active.type == "ARMATURE":
        return len(_selected_pose_bones(context, armature_obj=active))
    if active is not None and active.type == "MESH":
        counts = [
            len(_selected_pose_bones(context, armature_obj=modifier.object))
            for modifier in _armature_modifiers(active)
        ]
        return max(counts, default=0)
    return 0


def _active_direction_label(context):
    active = context.view_layer.objects.active if context.view_layer else None
    name = ""
    if active is not None and active.type == "MESH":
        group = _active_vertex_group(active)
        name = group.name if group is not None else ""
    if not name:
        pose_bone = getattr(context, "active_pose_bone", None)
        name = pose_bone.name if pose_bone is not None else ""
    try:
        return name, _strict_opposite_name(name)
    except WeightSymmetryError:
        return "", ""


def draw_weight_symmetry(layout, context):
    """Draw the intentionally minimal Weight-page action."""

    selected_count = _selected_pose_count_for_ui(context)
    if selected_count >= 2:
        text = f"Copy Selected {selected_count} Bones to Opposite"
    else:
        source_name, target_name = _active_direction_label(context)
        text = (
            f"Copy {source_name} -> {target_name}"
            if source_name
            else "Copy Active Group to Opposite"
        )
    layout.operator(
        "character_designer.copy_weight_to_opposite",
        text=text,
        icon="MOD_MIRROR",
    )


class CHARACTERDESIGNER_PT_weight_symmetry(Panel):
    bl_label = "Weight Symmetry"
    bl_idname = "CHARACTERDESIGNER_PT_weight_symmetry"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_WEIGHT

    def draw(self, context):
        draw_weight_symmetry(self.layout, context)


WEIGHT_SYMMETRY_CLASSES = (
    CHARACTERDESIGNER_OT_copy_weight_to_opposite,
    CHARACTERDESIGNER_PT_weight_symmetry,
)
