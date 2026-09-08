import math

import bmesh
import bpy
from mathutils import Vector

try:
    from .rr_modeling_origin import (
        modeling_selection_origin_point,
        selected_curve_coordinates,
        selected_edit_objects_for_modeling_origin,
    )
except ImportError:
    from rr_modeling_origin import (
        modeling_selection_origin_point,
        selected_curve_coordinates,
        selected_edit_objects_for_modeling_origin,
    )


BOOKMARK_GROUP_ITEMS = (
    ("G1", "G1", "Use temporary point bookmark group G1"),
    ("G2", "G2", "Use temporary point bookmark group G2"),
    ("G3", "G3", "Use temporary point bookmark group G3"),
)

BOOKMARK_POINT_ITEMS = (
    ("P1", "P1", "Use point bookmark P1 in the active group"),
    ("P2", "P2", "Use point bookmark P2 in the active group"),
    ("P3", "P3", "Use point bookmark P3 in the active group"),
)

SOURCE_ITEMS = (
    ("SELECTION", "Selection", "Store the center of the current Edit Mode selection"),
    ("CURSOR", "3D Cursor", "Store the current 3D Cursor location"),
)

SPACE_ITEMS = (
    ("WORLD", "World", "Store a fixed world-space position"),
    (
        "LOCAL",
        "Active Object Local",
        "Store a position relative to the active object's transform",
    ),
)

_BOOKMARK_GROUP_IDS = frozenset(item[0] for item in BOOKMARK_GROUP_ITEMS)
_BOOKMARK_POINT_IDS = frozenset(item[0] for item in BOOKMARK_POINT_ITEMS)


class RRBuilderPointBookmarkSlot(bpy.types.PropertyGroup):
    is_set: bpy.props.BoolProperty(
        name="Stored",
        description="Whether this point bookmark contains a stored position",
        default=False,
    )
    alias: bpy.props.StringProperty(
        name="Name",
        description="Optional temporary name for this point bookmark",
        default="",
    )
    location: bpy.props.FloatVectorProperty(
        name="Location",
        description="Stored world-space or object-local position",
        size=3,
        subtype="TRANSLATION",
        default=(0.0, 0.0, 0.0),
    )
    space: bpy.props.EnumProperty(
        name="Space",
        description="Coordinate space used by this stored point",
        items=SPACE_ITEMS,
        default="WORLD",
    )
    source: bpy.props.EnumProperty(
        name="Source",
        description="Source used when this point was stored",
        items=SOURCE_ITEMS,
        default="SELECTION",
    )
    anchor: bpy.props.PointerProperty(
        name="Anchor",
        description="Object whose local space contains this stored point",
        type=bpy.types.Object,
    )
    anchor_name: bpy.props.StringProperty(
        name="Anchor Name",
        description="Last known anchor name, retained for missing-anchor diagnostics",
        default="",
    )


def point_bookmark_slot(settings, group, point):
    """Resolve one of the fixed G1..G3/P1..P3 slots without arbitrary getattr."""
    if settings is None or group not in _BOOKMARK_GROUP_IDS or point not in _BOOKMARK_POINT_IDS:
        return None
    return getattr(settings, f"point_{group.lower()}_{point.lower()}", None)


def _mean_world_points(points):
    if not points:
        return None
    total = Vector((0.0, 0.0, 0.0))
    for point in points:
        total += point
    return total / len(points)


def _selected_mesh_world_points(objects):
    vertex_points = []
    edge_points = []
    face_points = []

    for obj in objects:
        if obj is None or obj.type != "MESH" or obj.data is None or obj.mode != "EDIT":
            continue
        try:
            edit_mesh = bmesh.from_edit_mesh(obj.data)
        except (ReferenceError, RuntimeError, ValueError):
            continue

        matrix_world = obj.matrix_world
        vertex_points.extend(
            matrix_world @ vertex.co
            for vertex in edit_mesh.verts
            if vertex.select and not vertex.hide
        )
        edge_points.extend(
            matrix_world @ ((edge.verts[0].co + edge.verts[1].co) * 0.5)
            for edge in edit_mesh.edges
            if edge.select and not edge.hide
        )
        face_points.extend(
            matrix_world @ face.calc_center_median()
            for face in edit_mesh.faces
            if face.select and not face.hide
        )

    # Match Blender's selection-origin behavior: selected vertices are the most
    # precise representation, with edges and faces retained as defensive fallbacks.
    return vertex_points or edge_points or face_points


def _selected_curve_world_points(objects):
    points = []
    for obj in objects:
        if obj is None or obj.type != "CURVE" or obj.data is None or obj.mode != "EDIT":
            continue
        matrix_world = obj.matrix_world
        points.extend(matrix_world @ coordinate for coordinate in selected_curve_coordinates(obj))
    return points


def point_bookmark_selection_world_point(context):
    """Return the element-weighted world center of the current mesh/curve selection."""
    if context is None or getattr(context, "mode", "") not in {"EDIT_MESH", "EDIT_CURVE"}:
        return None

    objects = selected_edit_objects_for_modeling_origin(context)
    if not objects:
        return None

    # Keep the established RR Helper result for the common single-object case.
    if len(objects) == 1:
        point = modeling_selection_origin_point(objects[0])
        return Vector(point) if point is not None else None

    if context.mode == "EDIT_MESH":
        points = _selected_mesh_world_points(objects)
    else:
        points = _selected_curve_world_points(objects)
    return _mean_world_points(points)


def _operator_settings(context):
    scene = getattr(context, "scene", None)
    return scene, getattr(scene, "rr_builder_export_settings", None) if scene is not None else None


def _finite_vector(vector):
    return vector is not None and all(math.isfinite(float(component)) for component in vector)


def _bookmark_label(slot, group, point):
    alias = str(getattr(slot, "alias", "") or "").strip()
    return alias or f"{group} {point}"


class RR_OT_store_point_bookmark(bpy.types.Operator):
    bl_idname = "rr_builder.store_point_bookmark"
    bl_label = "Store Point Bookmark"
    bl_description = "Store the selected point or 3D Cursor in a temporary bookmark"
    bl_options = {"REGISTER", "UNDO"}

    group: bpy.props.EnumProperty(items=BOOKMARK_GROUP_ITEMS, default="G1", options={"SKIP_SAVE"})
    point: bpy.props.EnumProperty(items=BOOKMARK_POINT_ITEMS, default="P1", options={"SKIP_SAVE"})

    def execute(self, context):
        scene, settings = _operator_settings(context)
        slot = point_bookmark_slot(settings, self.group, self.point)
        if scene is None or settings is None or slot is None:
            self.report({"WARNING"}, "Point bookmark settings are unavailable.")
            return {"CANCELLED"}

        source = getattr(settings, "point_bookmark_source", "SELECTION")
        space = getattr(settings, "point_bookmark_space", "WORLD")
        if source == "SELECTION":
            world_point = point_bookmark_selection_world_point(context)
            if world_point is None:
                self.report({"WARNING"}, "Enter Mesh or Curve Edit Mode and select at least one element.")
                return {"CANCELLED"}
        elif source == "CURSOR":
            world_point = Vector(scene.cursor.location)
        else:
            self.report({"WARNING"}, f"Unsupported point source: {source}")
            return {"CANCELLED"}

        if not _finite_vector(world_point):
            self.report({"WARNING"}, "The source position is not finite.")
            return {"CANCELLED"}

        anchor = None
        anchor_name = ""
        if space == "WORLD":
            stored_point = Vector(world_point)
        elif space == "LOCAL":
            anchor = getattr(getattr(context, "view_layer", None), "objects", None)
            anchor = getattr(anchor, "active", None)
            if anchor is None:
                self.report({"WARNING"}, "Select an active object for local-space storage.")
                return {"CANCELLED"}
            try:
                stored_point = anchor.matrix_world.inverted() @ world_point
            except (ReferenceError, RuntimeError, ValueError, ZeroDivisionError):
                self.report({"WARNING"}, f"'{anchor.name}' has a non-invertible transform.")
                return {"CANCELLED"}
            if not _finite_vector(stored_point):
                self.report({"WARNING"}, f"'{anchor.name}' has an invalid transform.")
                return {"CANCELLED"}
            anchor_name = anchor.name
        else:
            self.report({"WARNING"}, f"Unsupported coordinate space: {space}")
            return {"CANCELLED"}

        # Do not touch the old slot until every source/space validation has passed.
        slot.location = tuple(stored_point)
        slot.space = space
        slot.source = source
        slot.anchor = anchor
        slot.anchor_name = anchor_name
        slot.is_set = True

        self.report({"INFO"}, f"Stored {_bookmark_label(slot, self.group, self.point)}.")
        return {"FINISHED"}


class RR_OT_point_bookmark_to_cursor(bpy.types.Operator):
    bl_idname = "rr_builder.point_bookmark_to_cursor"
    bl_label = "Point Bookmark to Cursor"
    bl_description = "Move only the 3D Cursor location to a stored point bookmark"
    bl_options = {"REGISTER", "UNDO"}

    group: bpy.props.EnumProperty(items=BOOKMARK_GROUP_ITEMS, default="G1", options={"SKIP_SAVE"})
    point: bpy.props.EnumProperty(items=BOOKMARK_POINT_ITEMS, default="P1", options={"SKIP_SAVE"})

    def execute(self, context):
        scene, settings = _operator_settings(context)
        slot = point_bookmark_slot(settings, self.group, self.point)
        if scene is None or settings is None or slot is None:
            self.report({"WARNING"}, "Point bookmark settings are unavailable.")
            return {"CANCELLED"}
        if not slot.is_set:
            self.report({"WARNING"}, f"{self.group} {self.point} is empty.")
            return {"CANCELLED"}

        if slot.space == "WORLD":
            world_point = Vector(slot.location)
        elif slot.space == "LOCAL":
            anchor = slot.anchor
            try:
                anchor_valid = anchor is not None and bpy.data.objects.get(anchor.name) is anchor
            except (ReferenceError, RuntimeError):
                anchor_valid = False
            if not anchor_valid:
                name = slot.anchor_name or "the saved anchor"
                self.report({"WARNING"}, f"Cannot recall point: '{name}' is missing.")
                return {"CANCELLED"}
            world_point = anchor.matrix_world @ Vector(slot.location)
        else:
            self.report({"WARNING"}, f"Unsupported stored coordinate space: {slot.space}")
            return {"CANCELLED"}

        if not _finite_vector(world_point):
            self.report({"WARNING"}, "The stored point does not resolve to a finite position.")
            return {"CANCELLED"}

        # Assigning location alone deliberately preserves cursor rotation as well
        # as object selection, active object, and the current interaction mode.
        scene.cursor.location = tuple(world_point)
        self.report({"INFO"}, f"Loaded {_bookmark_label(slot, self.group, self.point)}.")
        return {"FINISHED"}


class RR_OT_clear_point_bookmark(bpy.types.Operator):
    bl_idname = "rr_builder.clear_point_bookmark"
    bl_label = "Clear Point Bookmark"
    bl_description = "Clear a temporary point bookmark while preserving its custom name"
    bl_options = {"REGISTER", "UNDO"}

    group: bpy.props.EnumProperty(items=BOOKMARK_GROUP_ITEMS, default="G1", options={"SKIP_SAVE"})
    point: bpy.props.EnumProperty(items=BOOKMARK_POINT_ITEMS, default="P1", options={"SKIP_SAVE"})

    def execute(self, context):
        _scene, settings = _operator_settings(context)
        slot = point_bookmark_slot(settings, self.group, self.point)
        if settings is None or slot is None:
            self.report({"WARNING"}, "Point bookmark settings are unavailable.")
            return {"CANCELLED"}

        slot.is_set = False
        slot.location = (0.0, 0.0, 0.0)
        slot.space = "WORLD"
        slot.source = "SELECTION"
        slot.anchor = None
        slot.anchor_name = ""
        self.report({"INFO"}, f"Cleared {_bookmark_label(slot, self.group, self.point)}.")
        return {"FINISHED"}


__all__ = [
    "BOOKMARK_GROUP_ITEMS",
    "BOOKMARK_POINT_ITEMS",
    "SOURCE_ITEMS",
    "SPACE_ITEMS",
    "RRBuilderPointBookmarkSlot",
    "RR_OT_store_point_bookmark",
    "RR_OT_point_bookmark_to_cursor",
    "RR_OT_clear_point_bookmark",
    "point_bookmark_slot",
    "point_bookmark_selection_world_point",
]
