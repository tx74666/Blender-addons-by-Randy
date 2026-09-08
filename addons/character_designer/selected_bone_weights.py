"""Transactional Automatic Weights for only the selected deform bones."""

import math
import traceback

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator, Panel

from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_WEIGHT, active_ui_page


SUPPORTED_CONTEXT_MODES = frozenset({"POSE", "PAINT_WEIGHT", "EDIT_ARMATURE"})
NORMALIZE_EPSILON = 1.0e-8
NORMALIZE_TOLERANCE = 1.0e-5


class SelectedBoneWeightsError(ValueError):
    """A safe, artist-facing preflight or weighting failure."""


def _object_is_available(context, obj):
    return (
        obj is not None
        and context.view_layer.objects.get(obj.name) is obj
        and not obj.hide_get(view_layer=context.view_layer)
        and not obj.hide_viewport
    )


def _armature_modifiers(mesh_obj):
    return tuple(
        modifier
        for modifier in mesh_obj.modifiers
        if modifier.type == "ARMATURE"
        and modifier.object is not None
        and modifier.object.type == "ARMATURE"
    )


def _selected_pose_bone_names(armature_obj):
    names = []
    for pose_bone in armature_obj.pose.bones:
        if not bool(getattr(pose_bone, "select", False)):
            continue
        bone = pose_bone.bone
        if bone.hide or not bone.use_deform:
            continue
        names.append(pose_bone.name)
    return tuple(names)


def _selected_edit_bone_names(armature_obj):
    names = []
    for edit_bone in armature_obj.data.edit_bones:
        if edit_bone.select and edit_bone.use_deform:
            names.append(edit_bone.name)
    return tuple(names)


def _selected_deform_bone_names(context, armature_obj):
    if (
        context.mode == "EDIT_ARMATURE"
        and context.edit_object is armature_obj
    ):
        return _selected_edit_bone_names(armature_obj)
    return _selected_pose_bone_names(armature_obj)


def _current_armature_bones(armature_obj):
    """Return the authoritative bone collection for the current Armature mode."""

    if armature_obj.mode == "EDIT":
        return armature_obj.data.edit_bones
    return armature_obj.data.bones


def _bone_center_local(armature_obj, bone):
    if armature_obj.mode == "EDIT":
        return (bone.head + bone.tail) * 0.5
    return (bone.head_local + bone.tail_local) * 0.5


def _mesh_x_half_source_for_mirror(mesh_obj, mirror):
    """Return the occupied X side in the Mirror plane's coordinate space."""
    if mirror.mirror_object is None:
        into_mirror_space = None
    else:
        into_mirror_space = (
            mirror.mirror_object.matrix_world.inverted_safe()
            @ mesh_obj.matrix_world
        )
    x_values = tuple(
        vertex.co.x
        if into_mirror_space is None
        else (into_mirror_space @ vertex.co).x
        for vertex in mesh_obj.data.vertices
    )
    if not x_values:
        return None
    minimum = min(x_values)
    maximum = max(x_values)
    tolerance = max(1.0e-6, (maximum - minimum) * 1.0e-5)
    if minimum >= -tolerance and maximum > tolerance:
        source_side = 1.0
    elif maximum <= tolerance and minimum < -tolerance:
        source_side = -1.0
    else:
        return None
    return source_side, tolerance


def _active_x_mirror_modifiers(mesh_obj):
    return tuple(
        modifier
        for modifier in mesh_obj.modifiers
        if modifier.type == "MIRROR"
        and modifier.use_axis[0]
        and (modifier.show_viewport or modifier.show_render)
    )


def _mirrored_half_mesh_source(mesh_obj, armature_modifier):
    """Validate and return one supported, true half-Mesh Mirror source.

    Automatic Weights can produce independently driven opposite-side groups
    only when one ordinary local-X Mirror flips group names before Armature
    deformation.  Ambiguous configurations must fail closed instead of
    reporting success while leaving the generated side on the source bone.
    """

    mirrors = _active_x_mirror_modifiers(mesh_obj)
    if not mirrors:
        return None
    half_sources = tuple(
        (mirror, source)
        for mirror in mirrors
        if (source := _mesh_x_half_source_for_mirror(mesh_obj, mirror)) is not None
    )
    if not half_sources:
        return None
    if len(mirrors) != 1:
        raise SelectedBoneWeightsError(
            "A one-sided Mesh must use exactly one active X Mirror for "
            "selected-bone Automatic Weights."
        )

    mirror, source = half_sources[0]
    if tuple(bool(value) for value in mirror.use_axis) != (True, False, False):
        raise SelectedBoneWeightsError(
            "The one-sided Mesh Mirror must use only the X axis."
        )
    if mirror.mirror_object is not None:
        raise SelectedBoneWeightsError(
            "A one-sided Mesh with a custom Mirror Object is not supported; "
            "use an object-local X Mirror or make the result real first."
        )
    if not mirror.use_mirror_vertex_groups:
        raise SelectedBoneWeightsError(
            "Enable Mirror Vertex Groups on the one-sided Mesh before "
            "recalculating selected-bone weights."
        )

    modifiers = tuple(mesh_obj.modifiers)
    if modifiers.index(mirror) > modifiers.index(armature_modifier):
        raise SelectedBoneWeightsError(
            "Place the Mirror modifier before the Armature modifier so the "
            "opposite bone can deform the generated side."
        )
    return source


def _validate_mirrored_half_mesh_side(
    mesh_obj,
    armature_obj,
    armature_modifier,
    selected_names,
):
    """Refuse bones that exist only on a Mirror-generated X side.

    A generated half has no base vertices that can own the requested weights.
    Its source-side counterpart must be weighted instead.
    """

    source = _mirrored_half_mesh_source(mesh_obj, armature_modifier)
    if source is None:
        return
    source_side, tolerance = source

    mesh_from_armature = mesh_obj.matrix_world.inverted_safe() @ armature_obj.matrix_world
    generated_side = []
    bones = _current_armature_bones(armature_obj)
    for name in selected_names:
        bone = bones.get(name)
        if bone is None:
            continue
        center = mesh_from_armature @ _bone_center_local(armature_obj, bone)
        if center.x * source_side < -tolerance:
            generated_side.append(name)
    if not generated_side:
        return

    label = ", ".join(generated_side[:3])
    if len(generated_side) > 3:
        label += f" (+{len(generated_side) - 3})"
    side_label = "+X" if source_side > 0.0 else "-X"
    raise SelectedBoneWeightsError(
        f"This is a mirrored {side_label} half Mesh. Select source-side or center "
        f"bones instead; these bones exist only on the generated side: {label}."
    )


def _missing_mirror_pair_group_names(
    mesh_obj,
    armature_obj,
    armature_modifier,
    selected_names,
):
    """Find empty counterpart groups required by a true half-Mesh Mirror.

    Blender's Mirror modifier can flip ``.L``/``.R`` memberships only when the
    counterpart Vertex Group definition exists.  The generated side still owns
    no base vertices, so creating an empty definition preserves every
    unselected bone's weights while making the evaluated mirrored weights usable.
    """

    if _mirrored_half_mesh_source(mesh_obj, armature_modifier) is None:
        return ()

    selected = set(selected_names)
    missing = []
    bones = _current_armature_bones(armature_obj)
    for name in selected_names:
        paired_name = bpy.utils.flip_name(name)
        if paired_name == name or paired_name in selected:
            continue
        paired_bone = bones.get(paired_name)
        if paired_bone is None or not paired_bone.use_deform:
            continue
        if mesh_obj.vertex_groups.get(paired_name) is None:
            missing.append(paired_name)
    return tuple(dict.fromkeys(missing))


def _full_auto_solver_bone_names(
    mesh_obj,
    armature_obj,
    armature_modifier,
):
    """Return Deform bones that own real vertices in the current Mesh domain.

    A regular full Mesh uses every Deform bone.  A true one-sided Mirror source
    excludes bones that live only on the generated side; otherwise Bone Heat can
    write right-side ownership onto left-side base vertices before the Mirror
    modifier has a chance to flip group names.
    """

    bones = _current_armature_bones(armature_obj)
    deform_names = tuple(bone.name for bone in bones if bone.use_deform)
    source = _mirrored_half_mesh_source(mesh_obj, armature_modifier)
    if source is None:
        return deform_names

    source_side, tolerance = source
    mesh_from_armature = mesh_obj.matrix_world.inverted_safe() @ armature_obj.matrix_world
    result = []
    for name in deform_names:
        bone = bones[name]
        center = mesh_from_armature @ _bone_center_local(armature_obj, bone)
        if center.x * source_side >= -tolerance:
            result.append(name)
    return tuple(result)


def _resolve_armature(context):
    active = context.view_layer.objects.active
    selected = tuple(context.selected_objects or ())
    selected_armatures = tuple(obj for obj in selected if obj.type == "ARMATURE")

    if active is not None and active.type == "ARMATURE":
        return active

    if active is not None and active.type == "MESH":
        modifiers = _armature_modifiers(active)
        targets = tuple(dict.fromkeys(modifier.object for modifier in modifiers))
        if len(targets) == 1:
            if selected_armatures and targets[0] not in selected_armatures:
                raise SelectedBoneWeightsError(
                    "The selected Armature does not match the Mesh's Armature modifier."
                )
            return targets[0]
        if len(targets) > 1:
            raise SelectedBoneWeightsError(
                "The active Mesh has multiple Armature targets; choose an unambiguous rig."
            )

    if len(selected_armatures) == 1:
        return selected_armatures[0]
    if not selected_armatures:
        raise SelectedBoneWeightsError("Select the bound Armature and its deform bones.")
    raise SelectedBoneWeightsError("Select exactly one Armature.")


def _mesh_uses_armature(mesh_obj, armature_obj):
    modifiers = _armature_modifiers(mesh_obj)
    return len(modifiers) == 1 and modifiers[0].object is armature_obj


def _resolve_mesh(context, armature_obj):
    active = context.view_layer.objects.active
    if active is not None and active.type == "MESH":
        candidates = (active,)
    else:
        selected = tuple(
            obj
            for obj in (context.selected_objects or ())
            if obj.type == "MESH"
        )
        if selected:
            candidates = selected
        else:
            candidates = tuple(
                obj
                for obj in context.view_layer.objects
                if obj.type == "MESH" and _mesh_uses_armature(obj, armature_obj)
            )

    if len(candidates) != 1:
        if not candidates:
            raise SelectedBoneWeightsError(
                "Select one Mesh with an existing Armature modifier for this rig."
            )
        raise SelectedBoneWeightsError(
            "More than one bound Mesh is available; select exactly one Mesh."
        )

    mesh_obj = candidates[0]
    modifiers = _armature_modifiers(mesh_obj)
    if len(modifiers) != 1 or modifiers[0].object is not armature_obj:
        raise SelectedBoneWeightsError(
            "The Mesh must have exactly one Armature modifier targeting the selected rig."
        )
    return mesh_obj, modifiers[0]


def _preflight(context):
    if context.mode not in SUPPORTED_CONTEXT_MODES:
        raise SelectedBoneWeightsError(
            "Use Pose Mode, Weight Paint Mode, or Armature Edit Mode."
        )

    armature_obj = _resolve_armature(context)
    mesh_obj, armature_modifier = _resolve_mesh(context, armature_obj)

    if not _object_is_available(context, armature_obj):
        raise SelectedBoneWeightsError("The Armature must be visible in the current View Layer.")
    if not _object_is_available(context, mesh_obj):
        raise SelectedBoneWeightsError("The Mesh must be visible in the current View Layer.")
    if mesh_obj.library is not None and mesh_obj.override_library is None:
        raise SelectedBoneWeightsError("The linked Mesh is read-only.")
    if mesh_obj.data.library is not None and mesh_obj.data.override_library is None:
        raise SelectedBoneWeightsError("The linked Mesh data is read-only.")
    if mesh_obj.data.users != 1:
        raise SelectedBoneWeightsError(
            f'The Mesh data "{mesh_obj.data.name}" is shared by '
            f"{mesh_obj.data.users} objects. Make it Single User before "
            "recalculating weights."
        )
    if not mesh_obj.data.vertices:
        raise SelectedBoneWeightsError("The Mesh has no vertices to weight.")

    selected_names = _selected_deform_bone_names(context, armature_obj)
    if not selected_names:
        raise SelectedBoneWeightsError("Select at least one visible Deform bone.")
    _validate_mirrored_half_mesh_side(
        mesh_obj,
        armature_obj,
        armature_modifier,
        selected_names,
    )

    locked = tuple(
        name
        for name in selected_names
        if mesh_obj.vertex_groups.get(name) is not None
        and mesh_obj.vertex_groups[name].lock_weight
    )
    if locked:
        label = ", ".join(locked[:3])
        if len(locked) > 3:
            label += f" (+{len(locked) - 3})"
        raise SelectedBoneWeightsError(
            f"Unlock the selected bone Vertex Group first: {label}."
        )

    return mesh_obj, armature_obj, armature_modifier, selected_names


def _capture_vertex_groups(mesh_obj):
    states = [
        {
            "name": group.name,
            "index": group.index,
            "lock_weight": bool(group.lock_weight),
            "weights": [],
        }
        for group in mesh_obj.vertex_groups
    ]
    for vertex in mesh_obj.data.vertices:
        for membership in vertex.groups:
            if membership.group < len(states):
                states[membership.group]["weights"].append(
                    (vertex.index, float(membership.weight))
                )
    for state in states:
        state["weights"] = tuple(state["weights"])
    return tuple(states)


def _group_state_map(states):
    return {state["name"]: state for state in states}


def _state_weight_map(state):
    if state is None:
        return {}
    return dict(state["weights"])


def _selected_influence_vertices(before_states, solver_states, selected_names):
    """Return base-Mesh vertices owned by selected groups before or after Bone Heat.

    Including the pre-existing influence is intentional: rerunning the operator can
    repair an earlier, unnormalized selected-bone result even when Bone Heat itself
    deterministically produces the same values a second time. Empty Mirror
    counterpart definitions contribute no vertices.
    """

    before_by_name = _group_state_map(before_states)
    solver_by_name = _group_state_map(solver_states)
    affected = set()
    for name in selected_names:
        for state in (before_by_name.get(name), solver_by_name.get(name)):
            affected.update(
                vertex_index
                for vertex_index, weight in _state_weight_map(state).items()
                if weight > NORMALIZE_EPSILON
            )
    return tuple(sorted(affected))


def _deform_group_names(mesh_obj, structure_snapshot):
    return tuple(
        name
        for name, use_deform in structure_snapshot["deform"]
        if use_deform
    )


def _normalization_conflict(vertex_index, detail):
    raise SelectedBoneWeightsError(
        "Affected Deform weights cannot be normalized at vertex "
        f"{vertex_index}: {detail}. The entire operation was rolled back."
    )


def _validated_deform_weight(vertex_index, name, value, source_label):
    value = float(value)
    if (
        not math.isfinite(value)
        or value < -NORMALIZE_TOLERANCE
        or value > 1.0 + NORMALIZE_TOLERANCE
    ):
        _normalization_conflict(
            vertex_index,
            f'{source_label} Deform group "{name}" has invalid weight {value:.6g}',
        )
    return min(1.0, max(0.0, value))


def _build_normalization_plan(
    mesh_obj,
    before_states,
    solver_states,
    deform_names,
    affected_vertices,
    selected_names,
):
    """Plan a selected-priority slice of a full-rig Automatic Weight solve.

    The selected bones keep their full-rig Bone Heat values whenever the locked
    budget permits.  Other unlocked Deform bones use the *full solver's* local
    proportions to fill the remainder.  This preserves a weight-1 target core,
    supplies a spatially meaningful smooth edge, and prevents the global leakage
    produced when one bone is solved without competition.
    """

    before_by_name = _group_state_map(before_states)
    solver_by_name = _group_state_map(solver_states)
    before_weights = {
        name: _state_weight_map(before_by_name.get(name)) for name in deform_names
    }
    solver_weights = {
        name: _state_weight_map(solver_by_name.get(name)) for name in deform_names
    }
    locked_names = {
        name
        for name in deform_names
        if before_by_name.get(name) is not None
        and before_by_name[name]["lock_weight"]
    }
    selected = set(selected_names)
    plan = {}

    for vertex_index in affected_vertices:
        locked_total = sum(
            _validated_deform_weight(
                vertex_index,
                name,
                before_weights[name].get(vertex_index, 0.0),
                "Locked source",
            )
            for name in locked_names
        )
        if locked_total > 1.0 + NORMALIZE_TOLERANCE:
            _normalization_conflict(
                vertex_index,
                f"locked Deform groups already total {locked_total:.6g}",
            )
        available = max(0.0, 1.0 - locked_total)

        target_entries = []
        support_entries = []
        for name in deform_names:
            if name in locked_names:
                continue
            weight = _validated_deform_weight(
                vertex_index,
                name,
                solver_weights[name].get(vertex_index, 0.0),
                "Full Automatic Weight",
            )
            if weight <= 0.0:
                continue
            if name in selected:
                target_entries.append((name, weight))
            else:
                support_entries.append((name, weight))

        target_total = sum(weight for _name, weight in target_entries)
        support_total = sum(weight for _name, weight in support_entries)
        if target_total > NORMALIZE_EPSILON and available <= NORMALIZE_EPSILON:
            _normalization_conflict(
                vertex_index,
                "locked Deform groups leave no budget for the selected result",
            )

        if target_total > NORMALIZE_EPSILON:
            if support_total <= NORMALIZE_EPSILON:
                target_budget = available
            else:
                target_budget = min(target_total, available)
        else:
            target_budget = 0.0
        support_budget = max(0.0, available - target_budget)

        if (
            support_budget > NORMALIZE_EPSILON
            and support_total <= NORMALIZE_EPSILON
        ):
            _normalization_conflict(
                vertex_index,
                "the full Automatic Weight solve has no Deform support to fill "
                "the remaining budget",
            )

        target_scale = (
            target_budget / target_total
            if target_total > NORMALIZE_EPSILON
            else 0.0
        )
        support_scale = (
            support_budget / support_total
            if support_total > NORMALIZE_EPSILON
            else 0.0
        )
        targets = {
            name: weight * target_scale for name, weight in target_entries
        }
        targets.update(
            (name, weight * support_scale) for name, weight in support_entries
        )

        names_to_touch = set(selected_names)
        names_to_touch.update(targets)
        names_to_touch.update(
            name
            for name in deform_names
            if name not in locked_names
            and vertex_index in before_weights[name]
        )
        for name in names_to_touch:
            if name not in locked_names:
                plan.setdefault(name, {})[vertex_index] = targets.get(name, 0.0)

    return plan


def _apply_normalization_plan(mesh_obj, plan):
    for name, targets in plan.items():
        group = mesh_obj.vertex_groups.get(name)
        if group is None:
            if not any(weight > NORMALIZE_EPSILON for weight in targets.values()):
                continue
            group = mesh_obj.vertex_groups.new(name=name)
        if group.lock_weight:
            raise SelectedBoneWeightsError(
                "A Deform Vertex Group changed while normalization was being applied."
            )
        for vertex_index, target_weight in targets.items():
            if target_weight > NORMALIZE_EPSILON:
                group.add((vertex_index,), target_weight, "REPLACE")
            else:
                group.remove((vertex_index,))


def _verify_normalized_result(
    mesh_obj,
    before_states,
    deform_names,
    affected_vertices,
    selected_names,
    plan,
    mirror_pair_names=(),
):
    final_states = _capture_vertex_groups(mesh_obj)
    before_by_name = _group_state_map(before_states)
    final_by_name = _group_state_map(final_states)
    required_plan_names = {
        name
        for name, targets in plan.items()
        if name in before_by_name
        or any(weight > NORMALIZE_EPSILON for weight in targets.values())
    }
    allowed_names = set(before_by_name) | required_plan_names | set(mirror_pair_names)
    if set(final_by_name) != allowed_names:
        raise SelectedBoneWeightsError(
            "Normalization changed protected Vertex Group definitions."
        )

    deform = set(deform_names)
    affected = set(affected_vertices)
    mirror_pairs = set(mirror_pair_names)
    for name, before in before_by_name.items():
        after = final_by_name.get(name)
        if after is None or after["index"] != before["index"]:
            raise SelectedBoneWeightsError(
                "Normalization changed protected Vertex Group definitions."
            )
        if after["lock_weight"] != before["lock_weight"]:
            raise SelectedBoneWeightsError(
                f'Normalization changed the lock state of Vertex Group "{name}".'
            )
        if name in mirror_pairs:
            if after["weights"]:
                raise SelectedBoneWeightsError(
                    f'Normalization filled mirror-pair Vertex Group "{name}".'
                )
            continue
        if name not in deform or before["lock_weight"]:
            if after != before:
                raise SelectedBoneWeightsError(
                    f'Normalization changed protected Vertex Group "{name}".'
                )
            continue

        before_outside = tuple(
            item for item in before["weights"] if item[0] not in affected
        )
        after_outside = tuple(
            item for item in after["weights"] if item[0] not in affected
        )
        if after_outside != before_outside:
            raise SelectedBoneWeightsError(
                f'Normalization changed "{name}" outside the affected vertices.'
            )

    for name, targets in plan.items():
        after = final_by_name.get(name)
        if after is None:
            if any(weight > NORMALIZE_EPSILON for weight in targets.values()):
                raise SelectedBoneWeightsError(
                    f'Normalization did not create required Deform group "{name}".'
                )
            continue
        actual = _state_weight_map(after)
        for vertex_index, expected in targets.items():
            value = actual.get(vertex_index, 0.0)
            if abs(value - expected) > NORMALIZE_TOLERANCE:
                raise SelectedBoneWeightsError(
                    "Normalization verification failed for Deform group "
                    f'"{name}" at vertex {vertex_index}.'
                )

    final_weights = {
        name: _state_weight_map(final_by_name.get(name)) for name in deform_names
    }
    for vertex_index in affected_vertices:
        total = sum(
            weights.get(vertex_index, 0.0) for weights in final_weights.values()
        )
        if not math.isfinite(total) or abs(total - 1.0) > NORMALIZE_TOLERANCE:
            raise SelectedBoneWeightsError(
                "Normalization verification failed at vertex "
                f"{vertex_index} (Deform total {total:.6g})."
            )

    for name in selected_names:
        state = final_by_name.get(name)
        if state is None or not any(
            weight > NORMALIZE_EPSILON for _index, weight in state["weights"]
        ):
            raise SelectedBoneWeightsError(
                f'Normalization left no usable result for "{name}".'
            )
    for name in mirror_pair_names:
        state = final_by_name.get(name)
        if state is None or state["weights"]:
            raise SelectedBoneWeightsError(
                f'Normalization changed mirror-pair Vertex Group "{name}".'
            )
    return final_states


def _clear_group(group, vertex_count):
    was_locked = bool(group.lock_weight)
    group.lock_weight = False
    try:
        chunk_size = 32768
        for start in range(0, vertex_count, chunk_size):
            group.remove(list(range(start, min(start + chunk_size, vertex_count))))
    finally:
        group.lock_weight = was_locked


def _prepare_full_auto_solver_groups(mesh_obj, solver_names, vertex_count):
    """Create and clear a disposable group domain for the full Bone Heat solve."""

    for name in solver_names:
        group = mesh_obj.vertex_groups.get(name)
        if group is None:
            group = mesh_obj.vertex_groups.new(name=name)
        _clear_group(group, vertex_count)


def _ensure_empty_mirror_pair_groups(mesh_obj, names, vertex_count):
    """Keep every generated-side Deform group empty on a half-Mesh source."""

    for name in names:
        group = mesh_obj.vertex_groups.get(name)
        if group is None:
            mesh_obj.vertex_groups.new(name=name)
            continue
        if group.lock_weight:
            if any(
                membership.group == group.index
                for vertex in mesh_obj.data.vertices
                for membership in vertex.groups
            ):
                raise SelectedBoneWeightsError(
                    f'Unlock generated-side Mirror Vertex Group "{name}" so its '
                    "base-Mesh weights can be cleared."
                )
            continue
        _clear_group(group, vertex_count)


def _verify_full_auto_selected_result(solver_states, selected_names):
    solver_by_name = _group_state_map(solver_states)
    for name in selected_names:
        state = solver_by_name.get(name)
        if state is None or not any(
            weight > NORMALIZE_EPSILON for _index, weight in state["weights"]
        ):
            raise SelectedBoneWeightsError(
                f'Full-rig Automatic Weights produced no usable result for "{name}".'
            )


def _restore_vertex_groups(mesh_obj, states):
    expected_names = tuple(state["name"] for state in states)
    expected_set = set(expected_names)

    for group in reversed(tuple(mesh_obj.vertex_groups)):
        if group.name not in expected_set:
            mesh_obj.vertex_groups.remove(group)

    current_names = tuple(group.name for group in mesh_obj.vertex_groups)
    if current_names != expected_names:
        for group in reversed(tuple(mesh_obj.vertex_groups)):
            mesh_obj.vertex_groups.remove(group)
        for state in states:
            mesh_obj.vertex_groups.new(name=state["name"])

    vertex_count = len(mesh_obj.data.vertices)
    for state in states:
        group = mesh_obj.vertex_groups.get(state["name"])
        if group is None or group.index != state["index"]:
            raise RuntimeError("Vertex Group order changed unexpectedly.")
        group.lock_weight = False
        _clear_group(group, vertex_count)
        for vertex_index, weight in state["weights"]:
            group.add((vertex_index,), weight, "REPLACE")
        group.lock_weight = state["lock_weight"]

    restored = _capture_vertex_groups(mesh_obj)
    if restored != states:
        raise RuntimeError("Vertex Group rollback could not be verified exactly.")


def _verify_protected_groups(
    mesh_obj,
    before_states,
    selected_names,
    allowed_empty_names=(),
):
    selected = set(selected_names)
    allowed_empty = set(allowed_empty_names)
    after_states = _capture_vertex_groups(mesh_obj)
    before_by_name = _group_state_map(before_states)
    after_by_name = _group_state_map(after_states)

    unexpected = (
        set(after_by_name)
        - set(before_by_name)
        - selected
        - allowed_empty
    )
    missing = set(before_by_name) - set(after_by_name)
    if unexpected or missing:
        raise SelectedBoneWeightsError(
            "Automatic Weights changed protected Vertex Group definitions."
        )

    for name, before in before_by_name.items():
        if name in selected:
            continue
        after = after_by_name.get(name)
        if after != before:
            raise SelectedBoneWeightsError(
                f'Automatic Weights changed protected Vertex Group "{name}".'
            )

    for name in allowed_empty_names:
        after = after_by_name.get(name)
        if after is None or after["weights"]:
            raise SelectedBoneWeightsError(
                f'Automatic Weights changed mirror-pair Vertex Group "{name}".'
            )

    for name in selected_names:
        after = after_by_name.get(name)
        if after is None or not any(weight > 0.0 for _index, weight in after["weights"]):
            raise SelectedBoneWeightsError(
                f'Automatic Weights produced no usable result for "{name}".'
            )
    return after_states


def _capture_context_state(context, mesh_obj, armature_obj):
    edit_selection = ()
    edit_active = ""
    if context.mode == "EDIT_ARMATURE" and context.edit_object is armature_obj:
        edit_selection = tuple(
            (
                bone.name,
                bool(bone.select),
                bool(bone.select_head),
                bool(bone.select_tail),
            )
            for bone in armature_obj.data.edit_bones
        )
        active = armature_obj.data.edit_bones.active
        edit_active = active.name if active is not None else ""

    active_bone = armature_obj.data.bones.active
    return {
        "mode": context.mode,
        "active_object": context.view_layer.objects.active,
        "selected_objects": tuple(
            obj for obj in context.view_layer.objects if obj.select_get()
        ),
        "pose_selection": tuple(
            bone.name
            for bone in armature_obj.pose.bones
            if bool(getattr(bone, "select", False))
        ),
        "active_bone": active_bone.name if active_bone is not None else "",
        "edit_selection": edit_selection,
        "edit_active": edit_active,
        "active_group_index": mesh_obj.vertex_groups.active_index,
    }


def _force_object_mode(context):
    active = context.view_layer.objects.active
    if active is not None and active.mode != "OBJECT":
        result = bpy.ops.object.mode_set(mode="OBJECT")
        if "FINISHED" not in result:
            raise RuntimeError("Blender could not leave the current mode safely.")


def _deselect_all_objects(context):
    for obj in context.view_layer.objects:
        if obj.select_get():
            obj.select_set(False)


def _set_pose_selection(armature_obj, names, active_name=""):
    selected = set(names)
    for pose_bone in armature_obj.pose.bones:
        pose_bone.select = pose_bone.name in selected
    active = armature_obj.data.bones.get(active_name) if active_name else None
    if active is None and names:
        active = armature_obj.data.bones.get(names[0])
    armature_obj.data.bones.active = active


def _enter_pose_mode(context, armature_obj, selected_names, active_name=""):
    _force_object_mode(context)
    _deselect_all_objects(context)
    armature_obj.select_set(True)
    context.view_layer.objects.active = armature_obj
    result = bpy.ops.object.mode_set(mode="POSE")
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not enter Pose Mode.")
    _set_pose_selection(armature_obj, selected_names, active_name)


def _enter_weight_paint(context, mesh_obj, armature_obj, selected_names):
    _enter_pose_mode(context, armature_obj, selected_names, selected_names[0])
    mesh_obj.select_set(True)
    context.view_layer.objects.active = mesh_obj
    result = bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not enter Weight Paint Mode.")
    actual = tuple(
        bone.name
        for bone in (context.selected_pose_bones or ())
        if bone.bone.use_deform
    )
    if set(actual) != set(selected_names):
        raise RuntimeError("Blender did not preserve the intended Pose Bone selection.")


def _restore_context_state(context, mesh_obj, armature_obj, state):
    _force_object_mode(context)
    _deselect_all_objects(context)

    for obj in state["selected_objects"]:
        if _object_is_available(context, obj):
            obj.select_set(True)
    active = state["active_object"]
    if _object_is_available(context, active):
        context.view_layer.objects.active = active

    original_mode = state["mode"]
    if original_mode == "POSE":
        if not armature_obj.select_get():
            armature_obj.select_set(True)
        context.view_layer.objects.active = armature_obj
        result = bpy.ops.object.mode_set(mode="POSE")
        if "FINISHED" not in result:
            raise RuntimeError("Blender could not restore Pose Mode.")
        _set_pose_selection(
            armature_obj,
            state["pose_selection"],
            state["active_bone"],
        )
    elif original_mode == "PAINT_WEIGHT":
        _enter_pose_mode(
            context,
            armature_obj,
            state["pose_selection"],
            state["active_bone"],
        )
        mesh_obj.select_set(True)
        context.view_layer.objects.active = mesh_obj
        result = bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
        if "FINISHED" not in result:
            raise RuntimeError("Blender could not restore Weight Paint Mode.")
        for obj in state["selected_objects"]:
            if obj not in {mesh_obj, armature_obj} and _object_is_available(context, obj):
                obj.select_set(True)
    elif original_mode == "EDIT_ARMATURE":
        if not armature_obj.select_get():
            armature_obj.select_set(True)
        context.view_layer.objects.active = armature_obj
        result = bpy.ops.object.mode_set(mode="EDIT")
        if "FINISHED" not in result:
            raise RuntimeError("Blender could not restore Armature Edit Mode.")
        selected = {
            name: (body, head, tail)
            for name, body, head, tail in state["edit_selection"]
        }
        for edit_bone in armature_obj.data.edit_bones:
            edit_bone.select = False
            edit_bone.select_head = False
            edit_bone.select_tail = False
        for edit_bone in armature_obj.data.edit_bones:
            body, head, tail = selected.get(edit_bone.name, (False, False, False))
            edit_bone.select = body
            edit_bone.select_head = head
            edit_bone.select_tail = tail
        armature_obj.data.edit_bones.active = armature_obj.data.edit_bones.get(
            state["edit_active"]
        )
        restored = tuple(
            (
                bone.name,
                bool(bone.select),
                bool(bone.select_head),
                bool(bone.select_tail),
            )
            for bone in armature_obj.data.edit_bones
        )
        if restored != state["edit_selection"]:
            raise RuntimeError("Blender could not restore the exact Edit Bone selection.")
    else:
        _set_pose_selection(
            armature_obj,
            state["pose_selection"],
            state["active_bone"],
        )
    mesh_obj.vertex_groups.active_index = state["active_group_index"]


def _capture_structure(mesh_obj, armature_obj):
    bones = (
        armature_obj.data.edit_bones
        if armature_obj.mode == "EDIT"
        else armature_obj.data.bones
    )
    return {
        "parent": mesh_obj.parent,
        "parent_type": mesh_obj.parent_type,
        "parent_bone": mesh_obj.parent_bone,
        "matrix_parent_inverse": tuple(
            value for row in mesh_obj.matrix_parent_inverse for value in row
        ),
        "modifiers": tuple(
            (
                modifier.as_pointer(),
                modifier.name,
                modifier.type,
                getattr(modifier, "object", None),
            )
            for modifier in mesh_obj.modifiers
        ),
        "deform": tuple(
            (bone.name, bool(bone.use_deform)) for bone in bones
        ),
        "mesh_data": mesh_obj.data,
    }


def _verify_structure(mesh_obj, armature_obj, before):
    after = _capture_structure(mesh_obj, armature_obj)
    if after != before:
        raise SelectedBoneWeightsError(
            "Automatic Weights changed parenting, modifiers, Mesh data, or Deform flags."
        )


def _restore_deform_flags(armature_obj, before):
    if before is None:
        return
    expected = dict(before["deform"])
    bones = (
        armature_obj.data.edit_bones
        if armature_obj.mode == "EDIT"
        else armature_obj.data.bones
    )
    for bone in bones:
        if bone.name in expected:
            bone.use_deform = expected[bone.name]
    restored = tuple((bone.name, bool(bone.use_deform)) for bone in bones)
    if restored != before["deform"]:
        raise RuntimeError("Blender could not restore every bone Deform flag.")


def _run_native_auto_weights():
    return bpy.ops.paint.weight_from_bones(type="AUTOMATIC")


class CHARACTERDESIGNER_OT_auto_weight_selected_bones(Operator):
    bl_idname = "character_designer.auto_weight_selected_bones"
    bl_label = "Auto Weight Selected Bones"
    bl_description = (
        "Recalculate Automatic Weights for selected Deform bones; optional Full "
        "Auto Blend solves the current rig together and normalizes the affected "
        "region from the full solver's local proportions"
    )
    bl_options = {"REGISTER", "UNDO"}

    normalize_affected_deform_weights: BoolProperty(
        name="Full Auto Blend (Normalized)",
        description=(
            "Solve all current-rig Deform bones together, give selected solver "
            "weights first claim after locked weights, and normalize their old/new "
            "influence region from the full solver's local proportions; "
            "non-Deform and locked weights remain unchanged, and outside weights "
            "stay exact except invalid generated-side half-Mesh weights are "
            "cleared; disable for strict selected-only weighting without column "
            "normalization"
        ),
        default=False,
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        if context.view_layer is None or context.scene is None:
            return False
        if context.mode not in SUPPORTED_CONTEXT_MODES:
            cls.poll_message_set(
                "Use Pose Mode, Weight Paint Mode, or Armature Edit Mode."
            )
            return False
        return True

    def execute(self, context):
        mesh_obj = None
        armature_obj = None
        group_snapshot = None
        context_snapshot = None
        structure_snapshot = None
        temporary_flags = None
        context_restore_error = None
        finalization_errors = []
        rollback_error = None
        failure_message = None
        selected_names = ()
        solver_names = ()
        deform_names = ()
        mirror_pair_names = ()
        created_mirror_pair_names = ()
        normalized_vertex_count = 0
        completed = False

        try:
            (
                mesh_obj,
                armature_obj,
                armature_modifier,
                selected_names,
            ) = _preflight(context)
            group_snapshot = _capture_vertex_groups(mesh_obj)
            context_snapshot = _capture_context_state(
                context,
                mesh_obj,
                armature_obj,
            )
            structure_snapshot = _capture_structure(mesh_obj, armature_obj)
            if self.normalize_affected_deform_weights:
                solver_names = _full_auto_solver_bone_names(
                    mesh_obj,
                    armature_obj,
                    armature_modifier,
                )
                deform_names = _deform_group_names(
                    mesh_obj,
                    structure_snapshot,
                )
                solver_name_set = set(solver_names)
                mirror_pair_names = tuple(
                    name for name in deform_names if name not in solver_name_set
                )
            else:
                mirror_pair_names = _missing_mirror_pair_group_names(
                    mesh_obj,
                    armature_obj,
                    armature_modifier,
                    selected_names,
                )
            created_mirror_pair_names = tuple(
                name
                for name in mirror_pair_names
                if mesh_obj.vertex_groups.get(name) is None
            )
            temporary_flags = (
                bool(mesh_obj.data.use_mirror_x),
                bool(mesh_obj.data.use_paint_mask),
                bool(mesh_obj.data.use_paint_mask_vertex),
            )

            mesh_obj.data.use_mirror_x = False
            mesh_obj.data.use_paint_mask = False
            mesh_obj.data.use_paint_mask_vertex = False
            vertex_count = len(mesh_obj.data.vertices)
            if self.normalize_affected_deform_weights:
                if not set(selected_names).issubset(solver_names):
                    raise SelectedBoneWeightsError(
                        "The selected bones do not belong to the editable Mirror "
                        "source side."
                    )
                _enter_weight_paint(
                    context,
                    mesh_obj,
                    armature_obj,
                    solver_names,
                )
                _prepare_full_auto_solver_groups(
                    mesh_obj,
                    solver_names,
                    vertex_count,
                )
                _ensure_empty_mirror_pair_groups(
                    mesh_obj,
                    mirror_pair_names,
                    vertex_count,
                )
                if not bpy.ops.paint.weight_from_bones.poll():
                    raise SelectedBoneWeightsError(
                        "Blender Automatic Weights is unavailable for this Mesh "
                        "and Armature."
                    )
                result = _run_native_auto_weights()
                if "FINISHED" not in result:
                    raise SelectedBoneWeightsError(
                        "Blender Automatic Weights did not finish."
                    )
                solver_states = _capture_vertex_groups(mesh_obj)
                _verify_full_auto_selected_result(solver_states, selected_names)
                affected_vertices = _selected_influence_vertices(
                    group_snapshot,
                    solver_states,
                    selected_names,
                )
                if not affected_vertices:
                    raise SelectedBoneWeightsError(
                        "The selected bones have no affected vertices to normalize."
                    )
                plan = _build_normalization_plan(
                    mesh_obj,
                    group_snapshot,
                    solver_states,
                    deform_names,
                    affected_vertices,
                    selected_names,
                )
                _restore_vertex_groups(mesh_obj, group_snapshot)
                _ensure_empty_mirror_pair_groups(
                    mesh_obj,
                    mirror_pair_names,
                    vertex_count,
                )
                _apply_normalization_plan(mesh_obj, plan)
                _verify_normalized_result(
                    mesh_obj,
                    group_snapshot,
                    deform_names,
                    affected_vertices,
                    selected_names,
                    plan,
                    mirror_pair_names,
                )
                normalized_vertex_count = len(affected_vertices)
            else:
                _enter_weight_paint(
                    context,
                    mesh_obj,
                    armature_obj,
                    selected_names,
                )
                for name in mirror_pair_names:
                    mesh_obj.vertex_groups.new(name=name)
                for name in selected_names:
                    group = mesh_obj.vertex_groups.get(name)
                    if group is None:
                        group = mesh_obj.vertex_groups.new(name=name)
                    _clear_group(group, vertex_count)
                if not bpy.ops.paint.weight_from_bones.poll():
                    raise SelectedBoneWeightsError(
                        "Blender Automatic Weights is unavailable for this Mesh "
                        "and Armature."
                    )
                result = _run_native_auto_weights()
                if "FINISHED" not in result:
                    raise SelectedBoneWeightsError(
                        "Blender Automatic Weights did not finish."
                    )
                _verify_protected_groups(
                    mesh_obj,
                    group_snapshot,
                    selected_names,
                    mirror_pair_names,
                )
            _verify_structure(mesh_obj, armature_obj, structure_snapshot)
            completed = True
        except SelectedBoneWeightsError as exc:
            failure_message = str(exc)
        except Exception as exc:
            traceback.print_exc()
            failure_message = (
                f"Selected-bone Automatic Weights was rolled back: {exc}"
            )
        finally:
            if (
                not completed
                and mesh_obj is not None
                and group_snapshot is not None
            ):
                try:
                    _restore_vertex_groups(mesh_obj, group_snapshot)
                except Exception as exc:
                    traceback.print_exc()
                    rollback_error = exc
            if armature_obj is not None:
                try:
                    _restore_deform_flags(armature_obj, structure_snapshot)
                except Exception as exc:
                    traceback.print_exc()
                    finalization_errors.append(f"Deform flags: {exc}")
            if mesh_obj is not None and temporary_flags is not None:
                try:
                    (
                        mesh_obj.data.use_mirror_x,
                        mesh_obj.data.use_paint_mask,
                        mesh_obj.data.use_paint_mask_vertex,
                    ) = temporary_flags
                except Exception as exc:
                    traceback.print_exc()
                    finalization_errors.append(f"Mesh flags: {exc}")
            if (
                mesh_obj is not None
                and armature_obj is not None
                and context_snapshot is not None
            ):
                try:
                    _restore_context_state(
                        context,
                        mesh_obj,
                        armature_obj,
                        context_snapshot,
                    )
                except Exception:
                    traceback.print_exc()
                    context_restore_error = RuntimeError(
                        "Blender could not restore the original mode and selection."
                    )

        if (
            completed
            and (context_restore_error is not None or finalization_errors)
            and mesh_obj is not None
            and group_snapshot is not None
        ):
            try:
                _restore_vertex_groups(mesh_obj, group_snapshot)
                completed = False
            except Exception as exc:
                traceback.print_exc()
                rollback_error = exc

        if rollback_error is not None:
            self.report(
                {"ERROR"},
                "Weight rollback failed; inspect the Mesh before continuing.",
            )
            return {"CANCELLED"}
        if finalization_errors:
            self.report(
                {"ERROR"},
                "Could not restore all temporary state: "
                + "; ".join(finalization_errors),
            )
            return {"CANCELLED"}
        if context_restore_error is not None:
            self.report({"WARNING"}, str(context_restore_error))
            return {"CANCELLED"}
        if failure_message is not None:
            self.report({"WARNING"}, failure_message)
            return {"CANCELLED"}

        if self.normalize_affected_deform_weights:
            message = (
                f"Recalculated {len(selected_names)} selected bone"
                f"{'s' if len(selected_names) != 1 else ''} from a full-rig "
                "Automatic Weight solve; applied a normalized local blend to "
                f"{normalized_vertex_count} affected vert"
                f"{'ices' if normalized_vertex_count != 1 else 'ex'}. Selected "
                "solver weights were prioritized within the unlocked budget; "
                "non-Deform groups, locked weights, and outside vertices were "
                "preserved except invalid generated-side half-Mesh base weights."
            )
        else:
            protected_count = sum(
                state["name"] not in set(selected_names) for state in group_snapshot
            )
            message = (
                f"Recalculated {len(selected_names)} selected bone"
                f"{'s' if len(selected_names) != 1 else ''}; "
                f"verified {protected_count} other Vertex Groups unchanged."
            )
        if created_mirror_pair_names:
            message += (
                f" Added {len(created_mirror_pair_names)} empty generated-side "
                f"Mirror group{'s' if len(created_mirror_pair_names) != 1 else ''}."
            )
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_PT_weight_tools(Panel):
    bl_label = "Weight Tools"
    bl_idname = "CHARACTERDESIGNER_PT_weight_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_WEIGHT

    def draw(self, context):
        layout = self.layout
        layout.label(text="Select one bound Mesh and Pose bones.", icon="INFO")
        settings = getattr(context.window_manager, "character_designer", None)
        normalize = bool(
            getattr(settings, "normalize_affected_deform_weights", False)
        )
        if settings is not None:
            layout.prop(
                settings,
                "normalize_affected_deform_weights",
                text="Full Auto Blend (Normalized)",
            )
        action = layout.operator(
            "character_designer.auto_weight_selected_bones",
            text="Auto Weight Selected Bones",
            icon="MOD_ARMATURE",
        )
        action.normalize_affected_deform_weights = normalize


SELECTED_BONE_WEIGHT_CLASSES = (
    CHARACTERDESIGNER_OT_auto_weight_selected_bones,
    CHARACTERDESIGNER_PT_weight_tools,
)
