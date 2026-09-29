"""Reference-layout authoring data for RR Helper.

This module intentionally owns only the Blender-side marker and export data.
It does not create helper objects, change parenting, or alter object origins.
"""

from math import isfinite

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Matrix


REFERENCE_LAYOUT_VERSION = 1
CORE_UI_VERSION = 1
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


def _main_callback(name):
    callback = globals().get(name)
    if callable(callback):
        return callback
    try:
        import random_realm_builder_exporter as rr_main

        return getattr(rr_main, name, None)
    except ImportError:
        return None


def _object_in_scene(obj, scene):
    try:
        return bool(obj is not None and scene.objects.get(obj.name) == obj)
    except (AttributeError, ReferenceError):
        return False


def _editable_reference_markers(scene, keep=None):
    marked = [
        obj for obj in getattr(scene, "objects", ())
        if obj != keep and (REFERENCE_MARK_PROP in obj or REFERENCE_STABLE_ID_PROP in obj)
    ]
    for obj in marked:
        if getattr(obj, "library", None) is not None or not getattr(obj, "is_editable", True):
            raise ValueError(f"Make {obj.name} local before changing its saved Core marker.")
    return marked


def _stable_id_for(obj):
    ensure = _main_callback("ensure_export_identity")
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


def is_reference_object(obj, scene=None):
    return bool(
        obj is not None and obj == get_reference_object(scene)
        and reference_layout_is_active(scene)
    )


def get_reference_object(scene=None):
    scene = scene or bpy.context.scene
    settings = _scene_settings(scene)
    candidate = getattr(settings, "reference_object", None) if settings else None
    if _object_in_scene(candidate, scene):
        return candidate
    # Modern scenes use their saved pointer. A copied marker must not become
    # the Core merely because the real Core was deleted or moved elsewhere.
    if settings is not None and int(getattr(settings, "core_ui_version", 0)) >= CORE_UI_VERSION:
        return None
    for obj in getattr(scene, "objects", ()):
        if obj.get(REFERENCE_MARK_PROP, False):
            return obj
    return None


def legacy_reference_layout_pending(scene=None):
    """Read old disabled-layout state without changing a file during UI drawing."""
    scene = scene or bpy.context.scene
    settings = _scene_settings(scene)
    if settings is None or get_reference_object(scene) is None:
        return False
    if getattr(settings, "legacy_layout_pending", False):
        return True
    if int(getattr(settings, "core_ui_version", 0)) >= CORE_UI_VERSION:
        return False
    export_settings = getattr(scene, "rr_builder_export_settings", None)
    return bool(
        export_settings is not None
        and getattr(export_settings, "reference_layout_state_initialized", False)
        and not getattr(export_settings, "use_reference_layout", False)
    )


def reference_layout_is_active(scene=None):
    return bool(
        get_reference_object(scene) is not None
        and not legacy_reference_layout_pending(scene)
    )


def include_reference_mesh(scene=None):
    settings = _scene_settings(scene)
    return bool(getattr(settings, "include_reference_mesh", False)) if settings else False


def reference_frame_version(scene=None):
    settings = _scene_settings(scene)
    return max(1, int(getattr(settings, "reference_frame_version", 1))) if settings else 1


def mark_reference_object(obj, scene=None):
    scene = scene or bpy.context.scene
    if obj is None:
        raise ValueError("Select an object to mark as Core.")
    if not _object_in_scene(obj, scene):
        raise ValueError("The Core Object must belong to the current scene.")
    variant_group = _main_callback("object_manager_variant_group_root")
    if callable(variant_group) and variant_group(obj) is not None:
        raise ValueError("Choose a Core Object outside a Variant group; Variants must export together.")
    if getattr(obj, "library", None) is not None or not getattr(obj, "is_editable", True):
        raise ValueError("Make the object local before marking it as Core.")
    settings = _scene_settings(scene)
    previous_markers = _editable_reference_markers(scene, keep=obj)
    stable_id = _stable_id_for(obj)
    if not stable_id:
        raise ValueError("The Core Object has no export stable ID.")
    for previous in previous_markers:
        previous.pop(REFERENCE_MARK_PROP, None)
        previous.pop(REFERENCE_STABLE_ID_PROP, None)
    obj[REFERENCE_MARK_PROP] = True
    obj[REFERENCE_STABLE_ID_PROP] = stable_id
    if settings is not None:
        settings.reference_object = obj
        settings.status = f"Core Object: {obj.name}"
        settings.core_ui_version = CORE_UI_VERSION
        settings.legacy_layout_pending = False
    export_settings = getattr(scene, "rr_builder_export_settings", None)
    if export_settings is not None:
        export_settings.use_reference_layout = True
        export_settings.reference_layout_state_initialized = True
    return stable_id


def clear_reference_object(scene=None):
    scene = scene or bpy.context.scene
    settings = _scene_settings(scene)
    for reference in _editable_reference_markers(scene):
        reference.pop(REFERENCE_MARK_PROP, None)
        reference.pop(REFERENCE_STABLE_ID_PROP, None)
    if settings is not None:
        settings.reference_object = None
        settings.status = "No Core Object"
        settings.core_ui_version = CORE_UI_VERSION
        settings.legacy_layout_pending = False
    export_settings = getattr(scene, "rr_builder_export_settings", None)
    if export_settings is not None:
        export_settings.use_reference_layout = False
        export_settings.reference_layout_state_initialized = True


def build_reference_layout_for_export(root, scene=None, enabled=True):
    """Return the optional manifest block for one exported root.

    A missing reference is represented by ``None``.  The exporter may then
    write an ordinary manifest without inventing a virtual reference asset.
    """
    if not enabled or not reference_layout_is_active(scene):
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
    pending = legacy_reference_layout_pending(context.scene)
    if pending:
        row = layout.row(align=True)
        row.label(text=reference.name, icon="OBJECT_DATA")
        action = row.operator("rr_builder.toggle_core", text="Use as Core")
        action.object_name = reference.name
        row.operator("rr_builder.clear_reference", text="", icon="X")
        layout.label(text="Previous layout setting was off.", icon="INFO")
    export_row = layout.row()
    export_row.enabled = reference is not None and not pending
    export_row.prop(settings, "include_reference_mesh", text="Export Core Object")


def draw_reference_layout_box(layout, context, settings):
    box = layout.box()
    box.label(text="Core Object")
    draw_reference_layout_controls(box, context, settings)


class RRBuilderReferenceLayoutSettings(bpy.types.PropertyGroup):
    reference_object: PointerProperty(type=bpy.types.Object)
    include_reference_mesh: BoolProperty(
        name="Export Core Object",
        description="Export the Core Object in this batch. When disabled, it is still used for relative layout.",
        default=False,
    )
    core_ui_version: IntProperty(name="Core UI Version", default=0, options={"HIDDEN"})
    legacy_layout_pending: BoolProperty(name="Legacy Core Pending", default=False, options={"HIDDEN"})
    reference_frame_version: IntProperty(name="Reference Frame Version", default=1, min=1)
    status: StringProperty(name="Status", default="No Core Object")


def _reference_was_queued(reference, export_settings):
    resolve = _main_callback("queue_item_object")
    expand = _main_callback("expand_related_export_roots")
    for item in getattr(export_settings, "export_queue", ()):
        root = resolve(item) if callable(resolve) else bpy.data.objects.get(item.object_name)
        if root is None:
            continue
        candidates = expand([root]) if callable(expand) else [root]
        if reference in candidates:
            return True
    return False


def migrate_reference_layout_scene(scene):
    """Keep previous export choices until an explicit Core action changes them."""
    settings = _scene_settings(scene)
    export_settings = getattr(scene, "rr_builder_export_settings", None)
    if settings is None or export_settings is None or settings.core_ui_version >= CORE_UI_VERSION:
        return
    reference = get_reference_object(scene)
    pending = legacy_reference_layout_pending(scene)
    active = reference is not None and not pending
    if active and not settings.include_reference_mesh and _reference_was_queued(reference, export_settings):
        settings.include_reference_mesh = True
        settings.status = "Migrated: old queue included Core."
    elif pending:
        settings.status = "Previous layout setting was off. Use as Core to enable it."
    else:
        settings.status = f"Core Object: {reference.name}" if reference else "No Core Object"
    settings.reference_object = reference
    settings.legacy_layout_pending = pending
    settings.core_ui_version = CORE_UI_VERSION
    export_settings.use_reference_layout = active
    export_settings.reference_layout_state_initialized = True


@persistent
def migrate_reference_layout_usage_on_load(_dummy=None):
    """Migrate saved reference settings without silently enabling a disabled layout."""
    for scene in getattr(bpy.data, "scenes", ()):
        migrate_reference_layout_scene(scene)


class RR_OT_toggle_core(bpy.types.Operator):
    bl_idname = "rr_builder.toggle_core"
    bl_label = "Mark Core Object"
    bl_description = "Mark this object as Core for relative layout, replacing the previous Core. Click the current Core's star again to clear it."
    bl_options = {"REGISTER", "UNDO"}

    object_name: StringProperty(name="Object", default="", options={"HIDDEN"})

    def execute(self, context):
        obj = context.scene.objects.get(self.object_name) if self.object_name else _active_candidate(context)
        try:
            if obj is not None and is_reference_object(obj, context.scene):
                clear_reference_object(context.scene)
                self.report({"INFO"}, "Core marker cleared.")
            else:
                mark_reference_object(obj, context.scene)
                self.report({"INFO"}, f"Core Object: {obj.name}")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class RR_OT_mark_reference(bpy.types.Operator):
    bl_idname = "rr_builder.mark_reference"
    bl_label = "Mark as Core"
    bl_description = "Use the active object as Core for relative layout"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            mark_reference_object(_active_candidate(context), context.scene)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Core Object marked.")
        return {"FINISHED"}


class RR_OT_clear_reference(bpy.types.Operator):
    bl_idname = "rr_builder.clear_reference"
    bl_label = "Clear Core"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            clear_reference_object(context.scene)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Core marker cleared.")
        return {"FINISHED"}


def is_reference_only_export_root(obj, scene=None, enabled=None):
    if enabled is None:
        enabled = reference_layout_is_active(scene)
    return bool(
        enabled and obj is not None and obj == get_reference_object(scene) and
        not include_reference_mesh(scene)
    )
