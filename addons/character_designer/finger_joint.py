"""Conservative first-pass geometry for turning one finger loop into three."""

from __future__ import annotations

import json

import bmesh
import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from .ui_constants import UI_PAGE_MISC, active_ui_page, rig_page_active


EPSILON = 1.0e-8
OWNER_KEY = "character_designer_owner"
OWNER_VALUE = "finger_joint"
MARKER_COLLECTION_NAME = "Character Designer | Finger Joints"
MARKER_PREFIX = "CD_FingerJoint"


class FingerJointError(ValueError):
    """An artist-facing validation or geometry error."""


class CharacterDesignerFingerJointState(PropertyGroup):
    """UI-only state; it is deliberately not saved into the blend file."""

    side_a_ratio: FloatProperty(
        name="Side A",
        description="Position of the new ring from the selected loop toward side A",
        default=0.35,
        min=0.05,
        max=0.95,
        precision=3,
        subtype="FACTOR",
        options={"SKIP_SAVE"},
    )
    side_b_ratio: FloatProperty(
        name="Side B",
        description="Position of the new ring from the selected loop toward side B",
        default=0.35,
        min=0.05,
        max=0.95,
        precision=3,
        subtype="FACTOR",
        options={"SKIP_SAVE"},
    )
    marked_object: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    marked_vertex_indices: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    marked_signature: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    last_message: StringProperty(options={"SKIP_SAVE"})


def _settings(context):
    window_manager = getattr(context, "window_manager", None)
    return getattr(window_manager, "character_designer_finger_joint", None)


def _edit_bmesh(context):
    if context.mode != "EDIT_MESH":
        raise FingerJointError("Enter Mesh Edit Mode and select one closed edge loop.")
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or context.object is not obj:
        raise FingerJointError("Make one Mesh active in Edit Mode.")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    return obj, bm


def _selected_seed_edges(bm):
    selected_edges = [edge for edge in bm.edges if edge.select and not edge.hide]
    if not selected_edges:
        selected_vertices = {vertex for vertex in bm.verts if vertex.select and not vertex.hide}
        selected_edges = [
            edge for edge in bm.edges
            if not edge.hide and all(vertex in selected_vertices for vertex in edge.verts)
        ]
    if not selected_edges:
        raise FingerJointError("Select one closed edge loop first.")
    edge_vertices = {vertex for edge in selected_edges for vertex in edge.verts}
    if any(
        vertex.select and not vertex.hide and vertex not in edge_vertices
        for vertex in bm.verts
    ):
        raise FingerJointError("The selection contains vertices outside the chosen loop.")
    return tuple(sorted(selected_edges, key=lambda edge: edge.index))


def _marked_seed_edges(obj, bm, settings):
    if not settings.marked_signature:
        return None
    if settings.marked_object != obj.name:
        raise FingerJointError(
            f"The marked center loop belongs to '{settings.marked_object}'; make that mesh active or mark a new loop."
        )
    try:
        indices = tuple(int(index) for index in json.loads(settings.marked_vertex_indices))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise FingerJointError("The marked center loop data is invalid; mark the loop again.")
    bm.verts.index_update()
    vertices = []
    for index in indices:
        if index < 0 or index >= len(bm.verts):
            raise FingerJointError("The marked center loop changed; mark it again.")
        vertices.append(bm.verts[index])
    if _source_signature(vertices) != settings.marked_signature:
        raise FingerJointError("The marked center loop changed; mark it again.")
    vertex_set = set(vertices)
    edges = tuple(
        sorted(
            (edge for edge in bm.edges if all(vertex in vertex_set for vertex in edge.verts)),
            key=lambda edge: edge.index,
        )
    )
    if len(edges) != len(vertices):
        raise FingerJointError("The marked center loop is no longer a closed loop; mark it again.")
    return edges


def _ordered_loop(edges):
    adjacency = {}
    for edge in edges:
        for vertex in edge.verts:
            adjacency.setdefault(vertex, []).append(edge)
    if len(adjacency) < 4:
        raise FingerJointError("The selected loop needs at least four vertices.")
    if any(len(linked) != 2 for linked in adjacency.values()):
        raise FingerJointError("The selected edge loop branches or is open.")

    start = min(adjacency, key=lambda vertex: vertex.index)
    first_edge = min(adjacency[start], key=lambda edge: edge.index)
    ordered = [start]
    used_edges = {first_edge}
    current = first_edge.other_vert(start)
    while current is not start:
        if current in ordered:
            raise FingerJointError("The selected loop intersects itself.")
        ordered.append(current)
        next_edges = [edge for edge in adjacency[current] if edge not in used_edges]
        if len(next_edges) != 1:
            raise FingerJointError("The selected loop could not be ordered as one cycle.")
        next_edge = next_edges[0]
        used_edges.add(next_edge)
        current = next_edge.other_vert(current)
    if len(ordered) != len(adjacency) or len(used_edges) != len(edges):
        raise FingerJointError("Select exactly one connected closed edge loop.")
    return ordered


def _side_descriptors(loop_edges, loop_vertices):
    """Find the two one-face-deep quad bands directly beside the loop."""

    loop_vertex_set = set(loop_vertices)
    face_connectors = {}
    candidate_faces = set()
    for edge in loop_edges:
        if len(edge.link_faces) != 2:
            raise FingerJointError("Every loop edge must have exactly two manifold faces.")
        for face in edge.link_faces:
            if len(face.verts) != 4:
                raise FingerJointError(
                    "The selected loop touches a non-quad face; this prototype only supports quad bands."
                )
            if face in face_connectors:
                raise FingerJointError("The selected loop meets a branched face layout.")
            outside = [vertex for vertex in face.verts if vertex not in loop_vertex_set]
            if len(outside) != 2:
                raise FingerJointError(
                    "Each neighboring face must contain exactly two vertices outside the selected loop."
                )
            connectors = tuple(
                face_edge for face_edge in face.edges
                if sum(vertex in loop_vertex_set for vertex in face_edge.verts) == 1
            )
            opposite = tuple(
                face_edge for face_edge in face.edges
                if all(vertex not in loop_vertex_set for vertex in face_edge.verts)
            )
            if len(connectors) != 2 or len(opposite) != 1:
                raise FingerJointError("The selected loop is not bordered by a regular quad strip.")
            face_connectors[face] = connectors
            candidate_faces.add(face)

    graph = {face: set() for face in candidate_faces}
    for face, connectors in face_connectors.items():
        for connector in connectors:
            linked = [
                other for other in connector.link_faces
                if other in candidate_faces and other is not face
            ]
            if len(linked) != 1:
                raise FingerJointError(
                    "A neighboring quad strip is interrupted; select a simpler finger section."
                )
            graph[face].add(linked[0])
            graph[linked[0]].add(face)

    components = []
    unseen = set(candidate_faces)
    while unseen:
        start = min(unseen, key=lambda face: face.index)
        component = {start}
        stack = [start]
        unseen.remove(start)
        while stack:
            face = stack.pop()
            for neighbor in graph[face]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    if len(components) != 2 or any(len(component) != len(loop_edges) for component in components):
        raise FingerJointError(
            "The selected loop does not have exactly two continuous neighboring quad bands."
        )

    descriptors = []
    for component in components:
        connectors = {
            connector
            for face in component
            for connector in face_connectors[face]
        }
        if len(connectors) != len(loop_vertices):
            raise FingerJointError("A neighboring band does not have one connector per loop vertex.")
        for connector in connectors:
            if len(connector.link_faces) != 2:
                raise FingerJointError("A neighboring connector is not manifold.")
            if sum(vertex in loop_vertex_set for vertex in connector.verts) != 1:
                raise FingerJointError("A neighboring connector does not cross the selected loop boundary.")
        descriptors.append({"faces": tuple(component), "edges": tuple(connectors)})
    return tuple(descriptors)


def _loop_normal(loop_vertices):
    normal = Vector((0.0, 0.0, 0.0))
    for index, vertex in enumerate(loop_vertices):
        normal += vertex.co.cross(loop_vertices[(index + 1) % len(loop_vertices)].co)
    if normal.length <= EPSILON:
        raise FingerJointError("The selected loop has no stable cross-section normal.")
    return normal.normalized()


def _source_signature(loop_vertices):
    values = sorted(
        tuple(round(float(component), 6) for component in vertex.co)
        for vertex in loop_vertices
    )
    return json.dumps(values, separators=(",", ":"))


def _existing_marker(obj, signature):
    for marker in bpy.data.objects:
        if (
            marker.get(OWNER_KEY) == OWNER_VALUE
            and marker.get("mesh_object") == obj.name
            and marker.get("source_signature") == signature
        ):
            return marker
    return None


def build_finger_joint_plan(context, *, use_marked=True):
    obj, bm = _edit_bmesh(context)
    settings = _settings(context)
    loop_edges = _marked_seed_edges(obj, bm, settings) if use_marked and settings else None
    if loop_edges is None:
        loop_edges = _selected_seed_edges(bm)
    loop_vertices = _ordered_loop(loop_edges)
    descriptors = _side_descriptors(loop_edges, loop_vertices)
    center = sum((vertex.co for vertex in loop_vertices), Vector()) / len(loop_vertices)
    normal = _loop_normal(loop_vertices)
    radius = sum((vertex.co - center).length for vertex in loop_vertices) / len(loop_vertices)
    loop_indices = tuple(vertex.index for vertex in loop_vertices)
    signature = _source_signature(loop_vertices)
    if _existing_marker(obj, signature) is not None:
        raise FingerJointError(
            "This selected loop already has a Character Designer joint marker; refusing to add rings again."
        )
    side_a_ratio = float(getattr(settings, "side_a_ratio", 0.35))
    side_b_ratio = float(getattr(settings, "side_b_ratio", 0.35))
    if not 0.0 < side_a_ratio < 1.0 or not 0.0 < side_b_ratio < 1.0:
        raise FingerJointError("Ring spacing ratios must be between 0 and 1.")
    return {
        "obj": obj,
        "bm": bm,
        "loop_edges": tuple(loop_edges),
        "loop_vertices": tuple(loop_vertices),
        "descriptors": descriptors,
        "center": center.copy(),
        "normal": normal.copy(),
        "radius": radius,
        "loop_indices": loop_indices,
        "signature": signature,
        "side_a_ratio": side_a_ratio,
        "side_b_ratio": side_b_ratio,
    }


def _subdivide_band(bm, descriptor, loop_vertices, ratio):
    """Insert one ring by splitting the band's connector edges."""

    loop_vertex_set = set(loop_vertices)
    edges = tuple(descriptor["edges"])
    bm.verts.index_update()
    bm.edges.index_update()
    before_vertex_indices = {vertex.index for vertex in bm.verts}
    before_edge_indices = {edge.index for edge in bm.edges}
    edge_percents = {
        edge: (ratio if edge.verts[0] in loop_vertex_set else 1.0 - ratio)
        for edge in edges
    }
    bmesh.ops.subdivide_edges(
        bm,
        edges=list(edges),
        cuts=1,
        edge_percents=edge_percents,
        use_grid_fill=False,
        use_only_quads=False,
    )
    bm.verts.index_update()
    bm.edges.index_update()
    new_vertices = {
        vertex for vertex in bm.verts
        if vertex.index not in before_vertex_indices
    }
    new_edges = {
        edge for edge in bm.edges
        if edge.index not in before_edge_indices
    }
    if len(new_vertices) != len(loop_vertices):
        raise FingerJointError(
            f"The quad band produced {len(new_vertices)} new vertices for a "
            f"{len(loop_vertices)}-vertex loop from {len(edges)} connector edges."
        )
    ring_edges = tuple(
        edge for edge in new_edges
        if all(vertex in new_vertices for vertex in edge.verts)
    )
    if len(ring_edges) != len(loop_vertices):
        raise FingerJointError("The quad band did not produce one complete new ring.")
    ring_adjacency = {vertex: [] for vertex in new_vertices}
    for edge in ring_edges:
        for vertex in edge.verts:
            ring_adjacency[vertex].append(edge)
    if any(len(edges_for_vertex) != 2 for edges_for_vertex in ring_adjacency.values()):
        raise FingerJointError("The generated vertices do not form a closed ring.")
    return ring_edges, new_vertices


def _select_generated_rings(bm, loop_edges, generated_edges):
    selected = set(loop_edges)
    selected.update(edge for ring in generated_edges for edge in ring)
    for edge in bm.edges:
        edge.select_set(edge in selected)
    bm.select_flush_mode()


def _marker_collection(scene):
    collection = bpy.data.collections.get(MARKER_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(MARKER_COLLECTION_NAME)
        scene.collection.children.link(collection)
    elif scene.collection.children.get(collection.name) is None:
        scene.collection.children.link(collection)
    return collection


def _create_marker(plan):
    obj = plan["obj"]
    collection = _marker_collection(bpy.context.scene)
    base_name = f"{MARKER_PREFIX}_{obj.name}"
    name = base_name
    suffix = 1
    while bpy.data.objects.get(name) is not None:
        suffix += 1
        name = f"{base_name}_{suffix:02d}"
    marker = bpy.data.objects.new(name, None)
    collection.objects.link(marker)
    marker.empty_display_type = "SPHERE"
    marker.empty_display_size = max(plan["radius"] * 0.30, 0.005)
    marker.location = obj.matrix_world @ plan["center"]
    marker.rotation_mode = "QUATERNION"
    marker.rotation_quaternion = plan["normal"].to_track_quat("Z", "Y")
    marker[OWNER_KEY] = OWNER_VALUE
    marker["mesh_object"] = obj.name
    marker["source_signature"] = plan["signature"]
    marker["source_vertices"] = json.dumps(
        plan["loop_indices"], separators=(",", ":")
    )
    marker["side_a_ratio"] = plan["side_a_ratio"]
    marker["side_b_ratio"] = plan["side_b_ratio"]
    marker["status"] = "three_rings_created"
    return marker


def apply_finger_joint_plan(plan):
    obj = plan["obj"]
    bm = plan["bm"]
    if bpy.context.object is not obj or obj.mode != "EDIT":
        raise FingerJointError("The active Mesh changed; run the check again.")
    generated = [
        _subdivide_band(bm, plan["descriptors"][0], plan["loop_vertices"], plan["side_a_ratio"]),
        _subdivide_band(bm, plan["descriptors"][1], plan["loop_vertices"], plan["side_b_ratio"]),
    ]
    _select_generated_rings(bm, plan["loop_edges"], [item[0] for item in generated])
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    obj.data.update()

    bpy.ops.object.mode_set(mode="OBJECT")
    try:
        marker = _create_marker(plan)
    finally:
        bpy.ops.object.mode_set(mode="EDIT")
    return {
        "marker": marker,
        "ring_count": len(plan["loop_vertices"]),
        "added_vertices": sum(len(vertices) for _edges, vertices in generated),
    }


def _mark_center_loop(context):
    settings = _settings(context)
    plan = build_finger_joint_plan(context, use_marked=False)
    settings.marked_object = plan["obj"].name
    settings.marked_vertex_indices = json.dumps(
        plan["loop_indices"], separators=(",", ":")
    )
    settings.marked_signature = plan["signature"]
    message = (
        f"Center loop locked: {len(plan['loop_vertices'])} vertices on {plan['obj'].name}. "
        "Set Side A/B, then Check and Create 3 Rings."
    )
    settings.last_message = message
    return message


def _clear_marked_loop(settings):
    if settings is None:
        return
    settings.marked_object = ""
    settings.marked_vertex_indices = ""
    settings.marked_signature = ""


class CHARACTERDESIGNER_OT_finger_joint(Operator):
    bl_idname = "character_designer.finger_joint"
    bl_label = "Finger Joint Rings"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        items=(
            ("MARK", "Mark Center Loop", "Lock the currently selected closed loop as the center loop."),
            ("CHECK", "Check", "Validate the selected loop and its two neighboring quad bands."),
            ("CREATE", "Create", "Insert one ring on each side and create a joint marker."),
        ),
        default="CREATE",
    )

    @classmethod
    def poll(cls, context):
        return (
            (active_ui_page(context) == UI_PAGE_MISC or rig_page_active(context, "BODY"))
            and context.mode == "EDIT_MESH"
        )

    def execute(self, context):
        settings = _settings(context)
        try:
            if self.action == "MARK":
                message = _mark_center_loop(context)
                self.report({"INFO"}, message)
                return {"FINISHED"}
            plan = build_finger_joint_plan(context)
            if self.action == "CHECK":
                message = (
                    f"Valid closed loop: {len(plan['loop_vertices'])} vertices; "
                    f"two quad bands ready. Ratios {plan['side_a_ratio']:.2f}/{plan['side_b_ratio']:.2f}."
                )
                if settings is not None:
                    settings.last_message = message
                self.report({"INFO"}, message)
                return {"FINISHED"}
            result = apply_finger_joint_plan(plan)
            message = (
                f"Created three rings with {result['ring_count']} vertices per ring; "
                f"added {result['added_vertices']} vertices and marker '{result['marker'].name}'."
            )
            if settings is not None:
                settings.last_message = message
            _clear_marked_loop(settings)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (FingerJointError, RuntimeError, ValueError, ReferenceError, TypeError) as exc:
            if settings is not None:
                settings.last_message = str(exc)
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


def draw_finger_joint_controls(layout, context):
    """Draw the topology part inside the unified Fingers panel."""

    settings = _settings(context)
    box = layout.box()
    box.label(text="Joint Topology", icon="MESH_GRID")
    box.label(text="1. Alt-click one closed center loop.", icon="INFO")
    box.label(text="2. Lock it, then Check and Create 3 Rings.", icon="INFO")
    if settings is None:
        box.label(text="Finger Joint state unavailable.", icon="ERROR")
        return
    box.prop(settings, "side_a_ratio", text="Side A")
    box.prop(settings, "side_b_ratio", text="Side B")
    mark_row = box.row(align=True)
    mark_row.enabled = context.mode == "EDIT_MESH"
    mark_row.operator(
        "character_designer.finger_joint",
        text="Re-lock Center Loop" if settings.marked_signature else "Lock Center Loop",
        icon="CHECKMARK" if settings.marked_signature else "KEYFRAME",
    ).action = "MARK"
    if settings.marked_signature:
        box.label(text=f"Locked: {settings.marked_object}; this becomes the middle ring.", icon="CHECKMARK")
    row = box.row(align=True)
    row.enabled = context.mode == "EDIT_MESH"
    row.operator("character_designer.finger_joint", text="Check", icon="VIEWZOOM").action = "CHECK"
    row.operator("character_designer.finger_joint", text="Create 3 Rings", icon="LOOP_CUT_AND_SLIDE").action = "CREATE"
    if settings.last_message:
        box.label(text=settings.last_message, icon="CHECKMARK")


FINGER_JOINT_CLASSES = (
    CharacterDesignerFingerJointState,
    CHARACTERDESIGNER_OT_finger_joint,
)
