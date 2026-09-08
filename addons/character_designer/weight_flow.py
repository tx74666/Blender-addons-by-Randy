"""Topology-aware, transactional weight flow between two selected bones."""

import math
import traceback
from collections import deque

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator, Panel

from .selected_bone_weights import (
    _armature_modifiers,
    _capture_structure,
    _capture_vertex_groups,
    _group_state_map,
    _restore_vertex_groups,
    _selected_pose_bone_names,
    _validate_mirrored_half_mesh_side,
    _verify_structure,
)
from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_WEIGHT, active_ui_page


WEIGHT_EPSILON = 1.0e-8
WEIGHT_TOLERANCE = 2.0e-6
MIN_COMPONENT_VERTICES = 3

FLOW_PROFILE_LINEAR = "LINEAR"
FLOW_PROFILE_SMOOTH = "SMOOTH"
FLOW_PROFILE_SHARP = "SHARP"
FLOW_PROFILES = frozenset(
    {
        FLOW_PROFILE_LINEAR,
        FLOW_PROFILE_SMOOTH,
        FLOW_PROFILE_SHARP,
    }
)

_ACTIVE_PREVIEW = None


class WeightFlowError(ValueError):
    """A selection or data problem that can be shown directly to the artist."""


def _profile_value(value, profile):
    value = min(1.0, max(0.0, float(value)))
    if profile == FLOW_PROFILE_LINEAR:
        return value
    if profile == FLOW_PROFILE_SMOOTH:
        return value * value * (3.0 - 2.0 * value)
    if profile == FLOW_PROFILE_SHARP:
        if value <= 0.0 or value >= 1.0:
            return value
        left = value * value * value
        right = (1.0 - value) ** 3
        return left / (left + right)
    raise WeightFlowError(f"Unsupported Weight Flow Profile: {profile}.")


def _mesh_adjacency(mesh):
    adjacency = {vertex.index: {} for vertex in mesh.vertices}
    boundary_vertices = set()
    edge_face_counts = [0] * len(mesh.edges)
    edge_index_by_key = {
        tuple(sorted(edge.vertices)): edge.index for edge in mesh.edges
    }

    for polygon in mesh.polygons:
        for edge_key in polygon.edge_keys:
            edge_index = edge_index_by_key.get(tuple(sorted(edge_key)))
            if edge_index is not None:
                edge_face_counts[edge_index] += 1

    for edge in mesh.edges:
        first, second = edge.vertices
        length = (mesh.vertices[first].co - mesh.vertices[second].co).length
        length = max(float(length), WEIGHT_EPSILON)
        adjacency[first][second] = length
        adjacency[second][first] = length
        if edge_face_counts[edge.index] < 2:
            boundary_vertices.add(first)
            boundary_vertices.add(second)
    return adjacency, frozenset(boundary_vertices)


def _connected_components(selected, adjacency):
    remaining = set(selected)
    components = []
    while remaining:
        seed = min(remaining)
        queue = deque((seed,))
        remaining.remove(seed)
        component = set()
        while queue:
            vertex_index = queue.popleft()
            component.add(vertex_index)
            for neighbor in adjacency[vertex_index]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        components.append(frozenset(component))
    return tuple(components)


def _ordered_open_chain(component, adjacency, endpoints):
    start = min(endpoints)
    ordered = [start]
    previous = None
    current = start
    while True:
        candidates = tuple(
            neighbor
            for neighbor in adjacency[current]
            if neighbor in component and neighbor != previous
        )
        if not candidates:
            break
        if len(candidates) != 1:
            raise WeightFlowError("The selected open chain branches unexpectedly.")
        next_vertex = candidates[0]
        ordered.append(next_vertex)
        previous, current = current, next_vertex
    if len(ordered) != len(component) or ordered[-1] not in endpoints:
        raise WeightFlowError("The selected open chain could not be ordered safely.")
    return tuple(ordered)


def _analyze_selection(mesh, selected):
    selected = frozenset(int(index) for index in selected)
    if not selected:
        raise WeightFlowError(
            "Enable Vertex Selection Mask and select a vertex chain, loop, or region."
        )

    adjacency, mesh_boundary_vertices = _mesh_adjacency(mesh)
    components = _connected_components(selected, adjacency)
    selected_faces = tuple(
        polygon
        for polygon in mesh.polygons
        if polygon.vertices and all(index in selected for index in polygon.vertices)
    )
    specifications = []

    for component in components:
        if len(component) < MIN_COMPONENT_VERTICES:
            raise WeightFlowError(
                "Every Weight Flow component needs at least three connected vertices."
            )
        component_faces = tuple(
            polygon
            for polygon in selected_faces
            if all(index in component for index in polygon.vertices)
        )
        selected_neighbors = {
            index: tuple(
                neighbor for neighbor in adjacency[index] if neighbor in component
            )
            for index in component
        }

        if component_faces:
            face_vertices = {
                index for polygon in component_faces for index in polygon.vertices
            }
            if face_vertices != set(component):
                raise WeightFlowError(
                    "A surface selection cannot include loose branches or isolated edges."
                )

            selected_face_edge_counts = {}
            for polygon in component_faces:
                for edge_key in polygon.edge_keys:
                    key = tuple(sorted(edge_key))
                    selected_face_edge_counts[key] = (
                        selected_face_edge_counts.get(key, 0) + 1
                    )
            if any(count > 2 for count in selected_face_edge_counts.values()):
                raise WeightFlowError(
                    "A non-manifold selected surface cannot be relaxed safely."
                )
            boundary = {
                index
                for edge_key, count in selected_face_edge_counts.items()
                if count == 1
                for index in edge_key
            }
            boundary.update(component.intersection(mesh_boundary_vertices))
            if not boundary:
                raise WeightFlowError(
                    "A closed surface has no fixed boundary for Weight Flow."
                )
            interior = set(component) - boundary
            if not interior:
                raise WeightFlowError(
                    "Select a surface region with at least one interior vertex row."
                )
            specifications.append(
                {
                    "kind": "REGION",
                    "vertices": component,
                    "boundary": frozenset(boundary),
                    "interior": frozenset(interior),
                    "neighbors": selected_neighbors,
                }
            )
            continue

        degrees = {index: len(neighbors) for index, neighbors in selected_neighbors.items()}
        endpoints = tuple(index for index, degree in degrees.items() if degree == 1)
        if len(endpoints) == 2 and all(degree in {1, 2} for degree in degrees.values()):
            specifications.append(
                {
                    "kind": "OPEN_CHAIN",
                    "vertices": component,
                    "order": _ordered_open_chain(
                        component,
                        adjacency,
                        frozenset(endpoints),
                    ),
                }
            )
            continue
        if all(degree == 2 for degree in degrees.values()):
            specifications.append(
                {
                    "kind": "CLOSED_LOOP",
                    "vertices": component,
                    "neighbors": selected_neighbors,
                }
            )
            continue
        raise WeightFlowError(
            "Select non-branching chains, closed loops, or complete surface regions."
        )

    return {
        "selected": selected,
        "adjacency": adjacency,
        "components": tuple(specifications),
    }


def _weights_for_state(group_state):
    return {index: float(weight) for index, weight in group_state["weights"]}


def _participant_data(group_snapshot, participant_names, selected):
    by_name = _group_state_map(group_snapshot)
    if len(participant_names) != 2:
        raise WeightFlowError("Select exactly two visible Deform Pose bones.")
    first_state = by_name.get(participant_names[0])
    second_state = by_name.get(participant_names[1])
    if first_state is None or second_state is None:
        missing = [
            name for name in participant_names if by_name.get(name) is None
        ]
        raise WeightFlowError(
            "Weight Flow needs existing Vertex Groups for both bones: "
            + ", ".join(missing)
            + "."
        )
    if first_state["lock_weight"] or second_state["lock_weight"]:
        locked = [
            state["name"]
            for state in (first_state, second_state)
            if state["lock_weight"]
        ]
        raise WeightFlowError(
            "Unlock the participating Vertex Groups first: " + ", ".join(locked) + "."
        )

    first_weights = _weights_for_state(first_state)
    second_weights = _weights_for_state(second_state)
    budgets = {}
    ratios = {}
    for index in selected:
        first = first_weights.get(index, 0.0)
        second = second_weights.get(index, 0.0)
        budget = first + second
        if not math.isfinite(budget) or budget <= WEIGHT_EPSILON:
            raise WeightFlowError(
                "Every selected vertex needs weight from at least one participating bone."
            )
        budgets[index] = budget
        ratios[index] = second / budget
    return {
        "states": (first_state, second_state),
        "weights": (first_weights, second_weights),
        "budgets": budgets,
        "ratios": ratios,
    }


def _open_chain_targets(mesh, specification, ratios, profile):
    order = specification["order"]
    cumulative = [0.0]
    for first, second in zip(order, order[1:]):
        distance = (mesh.vertices[first].co - mesh.vertices[second].co).length
        cumulative.append(cumulative[-1] + max(float(distance), WEIGHT_EPSILON))
    total = cumulative[-1]
    first_ratio = ratios[order[0]]
    last_ratio = ratios[order[-1]]
    targets = {}
    for index, distance in zip(order, cumulative):
        parameter = distance / total if total > WEIGHT_EPSILON else 0.0
        shaped = _profile_value(parameter, profile)
        targets[index] = first_ratio + (last_ratio - first_ratio) * shaped
    return targets


def _closed_loop_targets(specification, weights, budgets):
    vertices = specification["vertices"]
    total_budget = sum(budgets[index] for index in vertices)
    if total_budget <= WEIGHT_EPSILON:
        raise WeightFlowError("The selected loop has no participating weight budget.")
    second_weights = weights[1]
    mean_ratio = sum(second_weights.get(index, 0.0) for index in vertices) / total_budget
    return {index: mean_ratio for index in vertices}


def _region_targets(
    specification,
    adjacency,
    ratios,
    profile,
    iterations,
):
    boundary = specification["boundary"]
    interior = specification["interior"]
    values = {index: ratios[index] for index in specification["vertices"]}

    for _iteration in range(iterations):
        previous = values.copy()
        for index in interior:
            weighted_sum = 0.0
            weight_sum = 0.0
            for neighbor in specification["neighbors"][index]:
                length = adjacency[index][neighbor]
                influence = 1.0 / max(length, WEIGHT_EPSILON)
                weighted_sum += previous[neighbor] * influence
                weight_sum += influence
            if weight_sum > WEIGHT_EPSILON:
                values[index] = weighted_sum / weight_sum

    lower = min(ratios[index] for index in boundary)
    upper = max(ratios[index] for index in boundary)
    span = upper - lower
    if span > WEIGHT_EPSILON:
        for index in interior:
            normalized = (values[index] - lower) / span
            values[index] = lower + span * _profile_value(normalized, profile)
    for index in boundary:
        values[index] = ratios[index]
    return values


def _compute_flow_weights(
    mesh,
    analysis,
    participant_data,
    *,
    profile,
    strength,
    iterations,
):
    if profile not in FLOW_PROFILES:
        raise WeightFlowError(f"Unsupported Weight Flow Profile: {profile}.")
    strength = min(1.0, max(0.0, float(strength)))
    iterations = max(1, int(iterations))
    budgets = participant_data["budgets"]
    ratios = participant_data["ratios"]
    targets = {}

    for specification in analysis["components"]:
        if specification["kind"] == "OPEN_CHAIN":
            component_targets = _open_chain_targets(
                mesh,
                specification,
                ratios,
                profile,
            )
        elif specification["kind"] == "CLOSED_LOOP":
            component_targets = _closed_loop_targets(
                specification,
                participant_data["weights"],
                budgets,
            )
        else:
            component_targets = _region_targets(
                specification,
                analysis["adjacency"],
                ratios,
                profile,
                iterations,
            )
        targets.update(component_targets)

    results = {}
    for index in analysis["selected"]:
        budget = budgets[index]
        original_ratio = ratios[index]
        target_ratio = targets[index]
        ratio = original_ratio + (target_ratio - original_ratio) * strength
        second_minimum = max(0.0, budget - 1.0)
        second_maximum = min(1.0, budget)
        second = min(second_maximum, max(second_minimum, budget * ratio))
        first = budget - second
        results[index] = (first, second)
    return results


def _write_group_weight(group, vertex_index, weight):
    if weight <= WEIGHT_EPSILON:
        group.remove((vertex_index,))
    else:
        group.add((vertex_index,), min(1.0, max(0.0, float(weight))), "REPLACE")


def _apply_results(mesh_obj, participant_names, results):
    groups = tuple(mesh_obj.vertex_groups.get(name) for name in participant_names)
    if any(group is None for group in groups):
        raise WeightFlowError("A participating Vertex Group disappeared during Preview.")
    for vertex_index, weights in results.items():
        _write_group_weight(groups[0], vertex_index, weights[0])
        _write_group_weight(groups[1], vertex_index, weights[1])
    mesh_obj.data.update()


def _capture_context_signature(context, mesh_obj, armature_obj):
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
        "active_group_index": mesh_obj.vertex_groups.active_index,
        "paint_mask": bool(mesh_obj.data.use_paint_mask),
        "vertex_mask": bool(mesh_obj.data.use_paint_mask_vertex),
        "vertex_selection": tuple(
            (vertex.index, bool(vertex.select), bool(vertex.hide))
            for vertex in mesh_obj.data.vertices
        ),
    }


def _verify_context_signature(context, mesh_obj, armature_obj, before):
    after = _capture_context_signature(context, mesh_obj, armature_obj)
    if after != before:
        raise WeightFlowError("Weight Flow changed the active mode or artist selection.")


def _preflight(context):
    if context.mode != "PAINT_WEIGHT":
        raise WeightFlowError("Use Weight Paint Mode for Weight Flow.")
    mesh_obj = context.view_layer.objects.active
    if mesh_obj is None or mesh_obj.type != "MESH":
        raise WeightFlowError("Make the bound Mesh active in Weight Paint Mode.")
    if mesh_obj.library is not None and mesh_obj.override_library is None:
        raise WeightFlowError("The linked Mesh is read-only.")
    if mesh_obj.data.library is not None and mesh_obj.data.override_library is None:
        raise WeightFlowError("The linked Mesh data is read-only.")
    if mesh_obj.data.users != 1:
        raise WeightFlowError(
            "The Mesh data is shared. Make it Single User before changing weights."
        )
    modifiers = _armature_modifiers(mesh_obj)
    if len(modifiers) != 1:
        raise WeightFlowError(
            "The Mesh needs exactly one Armature modifier with a valid rig."
        )
    armature_obj = modifiers[0].object
    if not mesh_obj.data.use_paint_mask_vertex:
        raise WeightFlowError(
            "Enable Vertex Selection Mask, then select a chain, loop, or region."
        )
    selected = tuple(
        vertex.index
        for vertex in mesh_obj.data.vertices
        if vertex.select and not vertex.hide
    )
    analysis = _analyze_selection(mesh_obj.data, selected)
    participant_names = _selected_pose_bone_names(armature_obj)
    if len(participant_names) != 2:
        raise WeightFlowError("Select exactly two visible Deform Pose bones.")
    _validate_mirrored_half_mesh_side(
        mesh_obj,
        armature_obj,
        modifiers[0],
        participant_names,
    )
    active_group = mesh_obj.vertex_groups.active
    if active_group is None or active_group.name not in participant_names:
        raise WeightFlowError(
            "Make one of the two selected bone Vertex Groups active for Preview."
        )

    group_snapshot = _capture_vertex_groups(mesh_obj)
    participant_data = _participant_data(
        group_snapshot,
        participant_names,
        analysis["selected"],
    )
    return {
        "mesh_obj": mesh_obj,
        "armature_obj": armature_obj,
        "participant_names": participant_names,
        "group_snapshot": group_snapshot,
        "structure_snapshot": _capture_structure(mesh_obj, armature_obj),
        "context_snapshot": _capture_context_signature(
            context,
            mesh_obj,
            armature_obj,
        ),
        "analysis": analysis,
        "participant_data": participant_data,
    }


def _verify_result(context, session):
    mesh_obj = session["mesh_obj"]
    armature_obj = session["armature_obj"]
    selected = session["analysis"]["selected"]
    participants = set(session["participant_names"])
    before_states = session["group_snapshot"]
    after_states = _capture_vertex_groups(mesh_obj)
    before_by_name = _group_state_map(before_states)
    after_by_name = _group_state_map(after_states)

    if tuple(state["name"] for state in before_states) != tuple(
        state["name"] for state in after_states
    ):
        raise WeightFlowError("Weight Flow changed Vertex Group definitions or order.")
    for name, before in before_by_name.items():
        after = after_by_name[name]
        if name not in participants:
            if after != before:
                raise WeightFlowError(
                    f'Weight Flow changed protected Vertex Group "{name}".'
                )
            continue
        if before["index"] != after["index"] or before["lock_weight"] != after["lock_weight"]:
            raise WeightFlowError(
                f'Weight Flow changed the definition of Vertex Group "{name}".'
            )
        before_weights = _weights_for_state(before)
        after_weights = _weights_for_state(after)
        for vertex in mesh_obj.data.vertices:
            if vertex.index in selected:
                continue
            before_marker = (
                vertex.index in before_weights,
                before_weights.get(vertex.index, 0.0),
            )
            after_marker = (
                vertex.index in after_weights,
                after_weights.get(vertex.index, 0.0),
            )
            if after_marker != before_marker:
                raise WeightFlowError(
                    f'Weight Flow changed unselected weights in "{name}".'
                )

    first_after = _weights_for_state(after_by_name[session["participant_names"][0]])
    second_after = _weights_for_state(after_by_name[session["participant_names"][1]])
    for index in selected:
        first = first_after.get(index, 0.0)
        second = second_after.get(index, 0.0)
        if not all(math.isfinite(value) and -WEIGHT_EPSILON <= value <= 1.0 + WEIGHT_EPSILON for value in (first, second)):
            raise WeightFlowError("Weight Flow produced an invalid weight value.")
        expected = session["participant_data"]["budgets"][index]
        if abs((first + second) - expected) > WEIGHT_TOLERANCE:
            raise WeightFlowError(
                "Weight Flow could not preserve the participating weight budget."
            )

    _verify_structure(mesh_obj, armature_obj, session["structure_snapshot"])
    _verify_context_signature(context, mesh_obj, armature_obj, session["context_snapshot"])


def _restore_session(session):
    mesh_obj = session.get("mesh_obj") if session else None
    snapshot = session.get("group_snapshot") if session else None
    if mesh_obj is None or snapshot is None:
        return
    _restore_vertex_groups(mesh_obj, snapshot)
    mesh_obj.data.update()


def _preview_property_updated(operator, context):
    if not getattr(operator, "_preview_active", False):
        return
    operator._refresh_preview(context)


class CHARACTERDESIGNER_OT_weight_flow(Operator):
    bl_idname = "character_designer.weight_flow"
    bl_label = "Weight Flow"
    bl_description = (
        "Preview a topology-aware weight transition between exactly two selected "
        "Deform bones while preserving every other Vertex Group"
    )
    bl_options = {"REGISTER", "UNDO"}

    profile: EnumProperty(
        name="Profile",
        description="Shape the transition without changing its fixed endpoints",
        items=(
            (FLOW_PROFILE_LINEAR, "Linear", "Even transition"),
            (FLOW_PROFILE_SMOOTH, "Smooth", "Smooth ease at both ends"),
            (FLOW_PROFILE_SHARP, "Sharp", "Keep more influence near each side"),
        ),
        default=FLOW_PROFILE_SMOOTH,
        update=_preview_property_updated,
    )
    strength: FloatProperty(
        name="Strength",
        description="Blend from the original weights to the Weight Flow result",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        update=_preview_property_updated,
    )
    iterations: IntProperty(
        name="Iterations",
        description="Relaxation passes for selected surface regions",
        default=24,
        min=1,
        max=128,
        update=_preview_property_updated,
    )

    @classmethod
    def poll(cls, context):
        if context.mode != "PAINT_WEIGHT":
            cls.poll_message_set("Use Weight Paint Mode.")
            return False
        active = context.view_layer.objects.active
        if active is None or active.type != "MESH":
            cls.poll_message_set("Make one bound Mesh active in Weight Paint Mode.")
            return False
        return True

    def _clear_runtime(self):
        global _ACTIVE_PREVIEW
        self._preview_active = False
        self._session = None
        if _ACTIVE_PREVIEW is self:
            _ACTIVE_PREVIEW = None

    def _rollback(self):
        session = getattr(self, "_session", None)
        try:
            _restore_session(session)
        finally:
            self._clear_runtime()

    def _refresh_preview(self, context):
        if not getattr(self, "_preview_active", False):
            return
        session = self._session
        try:
            results = _compute_flow_weights(
                session["mesh_obj"].data,
                session["analysis"],
                session["participant_data"],
                profile=self.profile,
                strength=self.strength,
                iterations=self.iterations,
            )
            _apply_results(
                session["mesh_obj"],
                session["participant_names"],
                results,
            )
            for area in context.screen.areas if context.screen is not None else ():
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        except Exception as exc:
            traceback.print_exc()
            try:
                self._rollback()
            except Exception:
                traceback.print_exc()
            self.report({"ERROR"}, f"Weight Flow Preview was restored: {exc}")

    def invoke(self, context, _event):
        global _ACTIVE_PREVIEW
        if _ACTIVE_PREVIEW is not None:
            self.report({"WARNING"}, "Finish the current Weight Flow Preview first.")
            return {"CANCELLED"}
        try:
            self._session = _preflight(context)
            self._preview_active = True
            _ACTIVE_PREVIEW = self
            self._refresh_preview(context)
            if not getattr(self, "_preview_active", False):
                return {"CANCELLED"}
            return context.window_manager.invoke_props_dialog(self, width=420)
        except WeightFlowError as exc:
            self._clear_runtime()
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            try:
                self._rollback()
            except Exception:
                traceback.print_exc()
            self.report({"ERROR"}, f"Weight Flow could not start: {exc}")
            return {"CANCELLED"}

    def execute(self, context):
        direct_execution = not getattr(self, "_preview_active", False)
        try:
            if direct_execution:
                self._session = _preflight(context)
                self._preview_active = True
            session = self._session
            results = _compute_flow_weights(
                session["mesh_obj"].data,
                session["analysis"],
                session["participant_data"],
                profile=self.profile,
                strength=self.strength,
                iterations=self.iterations,
            )
            _apply_results(
                session["mesh_obj"],
                session["participant_names"],
                results,
            )
            _verify_result(context, session)
            participant_names = session["participant_names"]
            vertex_count = len(session["analysis"]["selected"])
            self._clear_runtime()
            self.report(
                {"INFO"},
                f"Flowed {vertex_count} vertices between {participant_names[0]} and "
                f"{participant_names[1]}; all other Vertex Groups are unchanged.",
            )
            return {"FINISHED"}
        except WeightFlowError as exc:
            try:
                self._rollback()
            except Exception:
                traceback.print_exc()
                self.report({"ERROR"}, "Weight Flow rollback failed; inspect the Mesh.")
                return {"CANCELLED"}
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            try:
                self._rollback()
            except Exception:
                traceback.print_exc()
                self.report({"ERROR"}, "Weight Flow rollback failed; inspect the Mesh.")
                return {"CANCELLED"}
            self.report({"ERROR"}, f"Weight Flow was restored: {exc}")
            return {"CANCELLED"}

    def cancel(self, _context):
        try:
            self._rollback()
        except Exception:
            traceback.print_exc()
            self.report({"ERROR"}, "Weight Flow Preview rollback failed; inspect the Mesh.")

    def draw(self, _context):
        layout = self.layout
        session = getattr(self, "_session", None)
        if session:
            names = session["participant_names"]
            kinds = {item["kind"] for item in session["analysis"]["components"]}
            kind_labels = {
                "OPEN_CHAIN": "Open Chain",
                "CLOSED_LOOP": "Closed Loop",
                "REGION": "Surface Region",
            }
            topology = ", ".join(kind_labels[kind] for kind in sorted(kinds))
            layout.label(text=f"{names[0]}  ↔  {names[1]}", icon="BONE_DATA")
            layout.label(
                text=f"{len(session['analysis']['selected'])} vertices · {topology}",
                icon="MESH_DATA",
            )
        profile_row = layout.row()
        profile_row.enabled = not session or any(
            item["kind"] != "CLOSED_LOOP"
            for item in session["analysis"]["components"]
        )
        profile_row.prop(self, "profile", expand=True)
        if session and not profile_row.enabled:
            layout.label(text="Closed loops preserve their weighted mean.")
        layout.prop(self, "strength", slider=True)
        if session and any(
            item["kind"] == "REGION"
            for item in session["analysis"]["components"]
        ):
            layout.prop(self, "iterations")
        layout.label(text="OK applies · Esc restores", icon="INFO")


class CHARACTERDESIGNER_PT_weight_flow(Panel):
    bl_label = "Weight Flow"
    bl_idname = "CHARACTERDESIGNER_PT_weight_flow"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_WEIGHT

    def draw(self, context):
        layout = self.layout
        if context.mode == "PAINT_WEIGHT":
            layout.label(text="Select vertices and two Deform Pose bones.", icon="INFO")
        else:
            layout.label(text="Use Weight Paint Mode.", icon="INFO")
        layout.operator(
            "character_designer.weight_flow",
            text="Preview Weight Flow",
            icon="MOD_SMOOTH",
        )


def stop_weight_flow_runtime(restore=True):
    """Stop one live dialog preview before refresh or add-on unloading."""

    global _ACTIVE_PREVIEW
    active = _ACTIVE_PREVIEW
    _ACTIVE_PREVIEW = None
    if active is None:
        return
    try:
        if restore:
            _restore_session(getattr(active, "_session", None))
    except Exception:
        traceback.print_exc()
    finally:
        active._preview_active = False
        active._session = None


WEIGHT_FLOW_CLASSES = (
    CHARACTERDESIGNER_OT_weight_flow,
    CHARACTERDESIGNER_PT_weight_flow,
)
