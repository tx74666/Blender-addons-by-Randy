"""Reference-layout authoring data for RR Helper.

This module intentionally owns only the Blender-side marker and export data.
It does not create helper objects, change parenting, or alter object origins.
"""

from math import isfinite

import bpy
from bpy.props import BoolProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Matrix


REFERENCE_LAYOUT_VERSION = 1
REFERENCE_MARK_PROP = "rr_reference_marked"
REFERENCE_STABLE_ID_PROP = "rr_reference_stable_id"


def _scene_settings(scene=None):
    scene = scene or bpy.context.scene
    return getattr(scene, "rr_builder_reference_layout", None)


def _active_candidate(context):
    active = getattr(getattr(context, "view_layer", None), "objects", None)
    active = getattr(active, "active", None)
    if active is not None:
        return active
    selected = list(getattr(context, "selected_objects", ()) or ())
    return selected[0] if selected else None


def _stable_id_for(obj):
    ensure = globals().get("ensure_export_identity")
    if not callable(ensure):
        try:
            import random_realm_builder_exporter as rr_main

            ensure = getattr(rr_main, "ensure_export_identity", None)
        except Exception:
            ensure = None
    if callable(ensure):
        return ensure(obj)[0]
    value = obj.get("rr_export_stable_id", "") if obj is not None else ""
    return str(value or "")


def _matrix_is_finite(matrix):
    try:
        return all(isfinite(float(value)) for row in matrix for value in row)
    except Exception:
        return False


def matrix_to_row_major(matrix):
    if matrix is None or not _matrix_is_finite(matrix):
        raise ValueError("Reference layout matrix contains non-finite values.")
    return [float(matrix[row][column]) for row in range(4) for column in range(4)]


def authoring_relative_matrix(reference, member):
    if reference is None or member is None:
        raise ValueError("A reference and member object are required.")
    reference_world = reference.matrix_world.copy()
    member_world = member.matrix_world.copy()
    if not _matrix_is_finite(reference_world) or not _matrix_is_finite(member_world):
        raise ValueError("Reference layout transform contains non-finite values.")
    if abs(reference_world.determinant()) < 1.0e-8:
        raise ValueError("Reference authoring transform is not invertible.")
    relative = reference_world.inverted() @ member_world
    if not _matrix_is_finite(relative):
        raise ValueError("Reference relative layout contains non-finite values.")
    return relative


def is_reference_object(obj):
    return bool(obj is not None and obj.get(REFERENCE_MARK_PROP, False))


def get_reference_object(scene=None):
    settings = _scene_settings(scene)
    candidate = getattr(settings, "reference_object", None) if settings else None
    if candidate is not None:
        return candidate
    scene = scene or bpy.context.scene
    for obj in getattr(scene, "objects", ()):
        if is_reference_object(obj):
            return obj
    return None


def include_reference_mesh(scene=None):
    settings = _scene_settings(scene)
    return bool(getattr(settings, "include_reference_mesh", False)) if settings else False


def reference_frame_version(scene=None):
    settings = _scene_settings(scene)
    return max(1, int(getattr(settings, "reference_frame_version", 1))) if settings else 1


def mark_reference_object(obj, scene=None):
    if obj is None:
        raise ValueError("Select the object to mark as the reference.")
    settings = _scene_settings(scene)
    previous = get_reference_object(scene)
    if previous is not None and previous != obj:
        previous.pop(REFERENCE_MARK_PROP, None)
        previous.pop(REFERENCE_STABLE_ID_PROP, None)
    stable_id = _stable_id_for(obj)
    if not stable_id:
        raise ValueError("The reference object has no export stable ID.")
    obj[REFERENCE_MARK_PROP] = True
    obj[REFERENCE_STABLE_ID_PROP] = stable_id
    if settings is not None:
        settings.reference_object = obj
        settings.status = f"Reference: {obj.name} ({stable_id})"
    export_settings = getattr(scene or bpy.context.scene, "rr_builder_export_settings", None)
    if export_settings is not None and not getattr(
        export_settings, "reference_layout_state_initialized", False
    ):
        export_settings.use_reference_layout = False
        export_settings.reference_layout_state_initialized = True
    return stable_id


def clear_reference_object(scene=None):
    settings = _scene_settings(scene)
    reference = get_reference_object(scene)
    if reference is not None:
        reference.pop(REFERENCE_MARK_PROP, None)
        reference.pop(REFERENCE_STABLE_ID_PROP, None)
    if settings is not None:
        settings.reference_object = None
        settings.status = "No reference object"


def build_reference_layout_for_export(root, scene=None, enabled=True):
    """Return the optional manifest block for one exported root.

    A missing reference is represented by ``None``.  The exporter may then
    write an ordinary manifest without inventing a virtual reference asset.
    """
    if not enabled:
        return None
    reference = get_reference_object(scene)
    if reference is None:
        return None
    reference_stable_id = str(
        reference.get(REFERENCE_STABLE_ID_PROP, "") or _stable_id_for(reference)
    )
    if not reference_stable_id:
        raise ValueError("The marked reference object has no stable ID.")
    root_stable_id = _stable_id_for(root)
    frame_version = reference_frame_version(scene)
    is_root_reference = root == reference
    relative = Matrix.Identity(4) if is_root_reference else authoring_relative_matrix(reference, root)
    return {
        "version": REFERENCE_LAYOUT_VERSION,
        "role": "reference" if is_root_reference else "member",
        "referenceStableId": reference_stable_id,
        "referenceFrameVersion": frame_version,
        "relativeAuthoringMatrix": matrix_to_row_major(relative),
        "authoringFrameInRoot": matrix_to_row_major(Matrix.Identity(4)),
        "referenceAuthoringFrameInRoot": matrix_to_row_major(Matrix.Identity(4)),
        "sourceStableId": root_stable_id,
    }


def draw_reference_layout_controls(layout, context, settings):
    reference = get_reference_object(context.scene)
    if reference is None:
        layout.label(text="Reference: not set", icon="INFO")
    else:
        layout.label(text=f"Reference: {reference.name}", icon="OBJECT_DATA")
        stable_id = reference.get(REFERENCE_STABLE_ID_PROP, "")
        if stable_id:
            layout.label(text=f"Stable ID: {stable_id}")
    row = layout.row(align=True)
    row.operator("rr_builder.mark_reference", text="Mark Active as Reference", icon="PINNED")
    row.operator("rr_builder.clear_reference", text="Clear", icon="X")
    layout.prop(settings, "include_reference_mesh", text="Include Reference Mesh")
    layout.label(text="No Empty, parenting, movement, or origin changes.")


def draw_reference_layout_box(layout, context, settings):
    box = layout.box()
    box.label(text="Reference Layout")
    draw_reference_layout_controls(box, context, settings)


class RRBuilderReferenceLayoutSettings(bpy.types.PropertyGroup):
    reference_object: PointerProperty(type=bpy.types.Object)
    include_reference_mesh: BoolProperty(
        name="Include Reference Mesh",
        description="Export the marked reference model when it is explicitly queued",
        default=False,
    )
    reference_frame_version: IntProperty(name="Reference Frame Version", default=1, min=1)
    status: StringProperty(name="Status", default="No reference object")


def migrate_reference_layout_usage_on_load(_dummy=None):
    """Preserve the old auto-use behavior only for files that already had a Reference."""
    for scene in getattr(bpy.data, "scenes", ()):
        export_settings = getattr(scene, "rr_builder_export_settings", None)
        if export_settings is None or getattr(
            export_settings, "reference_layout_state_initialized", False
        ):
            continue
        export_settings.use_reference_layout = get_reference_object(scene) is not None
        export_settings.reference_layout_state_initialized = True


class RR_OT_mark_reference(bpy.types.Operator):
    bl_idname = "rr_builder.mark_reference"
    bl_label = "Mark as Reference"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            mark_reference_object(_active_candidate(context), context.scene)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Reference object marked without changing hierarchy or origin.")
        return {"FINISHED"}


class RR_OT_clear_reference(bpy.types.Operator):
    bl_idname = "rr_builder.clear_reference"
    bl_label = "Clear Reference"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        clear_reference_object(context.scene)
        self.report({"INFO"}, "Reference marker cleared.")
        return {"FINISHED"}


def is_reference_only_export_root(obj, scene=None, enabled=None):
    if enabled is None:
        export_settings = getattr(scene or bpy.context.scene, "rr_builder_export_settings", None)
        enabled = bool(getattr(export_settings, "use_reference_layout", False))
    return bool(
        enabled and obj is not None and obj == get_reference_object(scene) and
        not include_reference_mesh(scene)
    )
