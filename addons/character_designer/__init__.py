bl_info = {
    "name": "Character Designer",
    "author": "Randy & Codex",
    "version": (0, 42, 1),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Character Designer",
    "description": "Personal modeling, rig-setup, and generic reference-view tools.",
    "category": "3D View",
}

import hashlib
import importlib
import json
import math
import os
import sys
import textwrap
import time
import traceback
from array import array
from collections import defaultdict, deque

import bmesh
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector

from .delta_symmetry import (
    DELTA_SYMMETRY_CLASSES,
    CharacterDesignerDeltaState,
    stop_delta_symmetry_runtime,
)
from .reference_views import (
    REFERENCE_VIEW_CLASSES,
    CharacterDesignerReferenceState,
    ReferenceViewError,
    clear_reference_view_set,
    discover_reference_view_manifests,
    read_reference_view_manifest,
    register_reference_view_handlers,
    set_reference_view_visibility,
    sync_reference_view_set,
    unregister_reference_view_handlers,
)
from .limb_ik import (
    LIMB_IK_CLASSES,
    CharacterDesignerLimbIKState,
    register_limb_ik_viewport_handler,
    unregister_limb_ik_viewport_handler,
)
from .spline_ik_setup import (
    SPLINE_IK_SETUP_CLASSES,
    CharacterDesignerSplineIKState,
)
from .selected_bone_weights import SELECTED_BONE_WEIGHT_CLASSES
from .hair_bones import HAIR_BONES_CLASSES, CharacterDesignerHairBonesState
from .skirt import SKIRT_CLASSES, CharacterDesignerSkirtState, stop_skirt_runtime
from .animation import (
    ANIMATION_CLASSES,
    CharacterDesignerAnimationState,
    register_animation_runtime,
    unregister_animation_runtime,
)
from .ui_constants import (
    SIDEBAR_CATEGORY,
    UI_PAGE_ANIMATION,
    UI_PAGE_DEFAULT,
    UI_PAGE_CLOTHING,
    UI_PAGE_HAIR,
    UI_PAGE_ITEMS,
    UI_PAGE_MISC,
    UI_PAGE_RIG,
    UI_PAGE_WEIGHT,
    UI_PAGES,
    active_ui_page,
)
from .weight_symmetry import WEIGHT_SYMMETRY_CLASSES
from .forearm_twist import (
    FOREARM_TWIST_CLASSES,
    CharacterDesignerForearmTwistState,
    register_forearm_twist_runtime,
    unregister_forearm_twist_runtime,
)


GENERATOR_ID = "character_designer_centerline_v1"
RECOVERY_GENERATOR_ID = "character_designer_applied_curve_v1"
RECOVERY_SIGNATURE_KEY = "character_designer_recovery_signature"
RECOVERY_PROFILE_KEY = "character_designer_recovery_profile"
RECOVERY_SOURCE_OBJECT_KEY = "character_designer_recovery_source_object"
RECOVERY_CURVE_MODE_KEY = "character_designer_recovery_curve_mode"
RECOVERY_CONTROL_POINTS_KEY = "character_designer_recovery_control_points"
RECOVERY_MODIFIER_STACK_KEY = "character_designer_recovery_modifier_stack"
RECOVERY_MODIFIER_OWNERSHIP_KEY = "character_designer_recovery_modifier_ownership"
RECOVERY_CURVE_MODE_EXACT = "EXACT"
RECOVERY_CURVE_MODE_BEZIER = "BEZIER"
RECOVERY_CURVE_MODES = frozenset(
    {RECOVERY_CURVE_MODE_EXACT, RECOVERY_CURVE_MODE_BEZIER}
)
RECOVERY_SOURCE_ACTION_ADD = "ADD"
RECOVERY_SOURCE_ACTION_REPLACE = "REPLACE_MESH"
RECOVERY_SOURCE_ACTIONS = frozenset(
    {RECOVERY_SOURCE_ACTION_ADD, RECOVERY_SOURCE_ACTION_REPLACE}
)
RECOVERY_SOURCE_REPLACED_KEY = "character_designer_recovery_source_replaced"
RECOVERY_CONTROL_POINTS_MIN = 3
RECOVERY_CONTROL_POINTS_MAX = 32
RECOVERY_CONTROL_POINTS_DEFAULT = 5
RECOVERY_BEZIER_RESOLUTION = 12
RECOVERY_SUPPORTED_MODIFIER_TYPES = frozenset({"MIRROR", "SUBSURF"})
METADATA_VERSION = 5
SUPPORTED_METADATA_VERSIONS = frozenset({2, 3, 4, METADATA_VERSION})
METADATA_SPACE = "SOURCE_OBJECT_LOCAL"
EPSILON = 1.0e-8
HAIR_PROFILE_TIP_RADIUS = 0.015
HAIR_PROFILE_BEVEL_RESOLUTION = 4
HAIR_PROFILE_WIDE_FACE_SEPARATION = 1.15
HAIR_FRONT_RIBBON_AREA_RELATIVE_MIN = 0.005
HAIR_FRONT_CURVATURE_MIN = 1.0e-4
HAIR_FRONT_CURVATURE_CONFIDENCE = 0.25
HAIR_ALIGNMENT_CENTERED = "CENTERED"
HAIR_ALIGNMENT_BLEND = "BLEND"
HAIR_ALIGNMENT_FRONT_FLUSH = "FRONT_FLUSH"
HAIR_BLEND_FACTOR_KEY = "character_designer_blend_factor"
HAIR_BLEND_DEFAULT = 0.5
HAIR_ALIGNMENTS = frozenset(
    {
        HAIR_ALIGNMENT_CENTERED,
        HAIR_ALIGNMENT_BLEND,
        HAIR_ALIGNMENT_FRONT_FLUSH,
    }
)
HAIR_FRONT_FLUSH_ITERATIONS = 12
RECOVERY_PROFILE_RELATIVE_TOLERANCE = 0.055
RECOVERY_PROFILE_ABSOLUTE_TOLERANCE = 1.0e-5

ADDON_SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))
ADDON_MODULE_NAME = (__package__ or os.path.basename(ADDON_SOURCE_ROOT)).split(".")[0]
ADDON_REFRESH_PENDING = False
ADDON_REFRESH_LAST_STATE = False
ADDON_REFRESH_LAST_ERROR = ""

WORKSPACE_FILTER_GUARD_INTERVAL = 1.0
WORKSPACE_FILTER_GUARD_FIRST_INTERVAL = 0.1
WORKSPACE_OWNER_BY_RENDER_ENGINE = {
    "CYCLES": "cycles",
}
_WORKSPACE_FILTER_GUARD_LAST_ERROR = ""

LIVE_PREVIEW_INTERVAL = 0.1
LIVE_PREVIEW_TOPOLOGY_DEBOUNCE = 0.35
LIVE_PREVIEW_SELECTION_AUDIT_INTERVAL = 0.5
LIVE_PREVIEW_COLOR = (1.0, 0.32, 0.04, 1.0)
_LIVE_PREVIEW_INPUT_SIGNATURE = None
_LIVE_PREVIEW_WORLD_POINTS = ()
_LIVE_PREVIEW_PATHS = ()
_LIVE_PREVIEW_HANDLE_POINTS = ()
_LIVE_PREVIEW_LAYERS = ()
_LIVE_PREVIEW_METADATA_JSON = ""
_LIVE_PREVIEW_SOURCE_KEY = None
_LIVE_PREVIEW_TRACKED_SOURCE_KEY = None
_LIVE_PREVIEW_DRAW_HANDLE = None
_LIVE_PREVIEW_SHADER = None
_LIVE_PREVIEW_LINE_BATCH = None
_LIVE_PREVIEW_POINT_BATCH = None
_LIVE_PREVIEW_HANDLE_BATCH = None
_LIVE_PREVIEW_BATCH_REVISION = -1
_LIVE_PREVIEW_DRAW_FAILED = False
_LIVE_PREVIEW_DRAW_RETRY_AT = 0.0
_LIVE_PREVIEW_DEPSGRAPH_REVISION = 0
_LIVE_PREVIEW_TOPOLOGY_SIGNATURE = ""
_LIVE_PREVIEW_TOPOLOGY_DIRTY = True
_LIVE_PREVIEW_TOPOLOGY_AUDIT_AT = 0.0
_LIVE_PREVIEW_TOPOLOGY_VERIFIED = False
_LIVE_PREVIEW_TOGGLE_GUARD = False
_RECOVERY_PREVIEW_TOGGLE_GUARD = False
_LIVE_PREVIEW_SELECTION_DIGEST = None
_LIVE_PREVIEW_SELECTION_AUDIT_AT = 0.0
_CENTERLINE_CONTROL_UPDATE_GUARD = False
_ACTIVE_HINT_DEFAULT = object()


class CenterlineError(ValueError):
    """A topology or selection problem that can be shown directly to the artist."""


class TopologyChangedError(CenterlineError):
    """The captured mesh connectivity is no longer the connectivity being edited."""


def _source_files():
    paths = []
    for root, directory_names, file_names in os.walk(ADDON_SOURCE_ROOT):
        directory_names[:] = [
            name
            for name in directory_names
            if name != "__pycache__" and not name.startswith(".")
        ]
        for file_name in file_names:
            if file_name.endswith(".py"):
                paths.append(os.path.join(root, file_name))
    return sorted(paths)


def _source_signature():
    signature = []
    for path in _source_files():
        try:
            stat = os.stat(path)
        except OSError:
            continue
        relative_path = os.path.relpath(path, ADDON_SOURCE_ROOT).replace(os.sep, "/")
        signature.append((relative_path, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


ADDON_LOADED_SIGNATURE = _source_signature()


def _source_changed():
    return _source_signature() != ADDON_LOADED_SIGNATURE


def _refresh_ui_visible():
    """Match RR Helper: show refresh only while an update needs attention."""

    return bool(
        ADDON_REFRESH_PENDING
        or ADDON_REFRESH_LAST_STATE
        or ADDON_REFRESH_LAST_ERROR
    )


def _ensure_workspace_owner_ids(workspace, render_engine=""):
    """Keep a filtered workspace useful without broadening its allow-list."""
    if workspace is None or not workspace.use_filter_by_owner:
        return 0

    required_owner_ids = {ADDON_MODULE_NAME}
    render_owner_id = WORKSPACE_OWNER_BY_RENDER_ENGINE.get(render_engine)
    if render_owner_id:
        required_owner_ids.add(render_owner_id)

    existing_owner_ids = {owner_id.name for owner_id in workspace.owner_ids}
    added = 0
    for owner_id in sorted(required_owner_ids - existing_owner_ids):
        workspace.owner_ids.new(owner_id)
        added += 1
    return added


def _ensure_workspace_owner_filters():
    """Repair only the essential entries of every enabled workspace filter."""
    scenes = getattr(bpy.data, "scenes", ())
    workspaces = getattr(bpy.data, "workspaces", ())
    if not workspaces:
        # Blender temporarily exposes restricted data while enabling add-ons.
        # The persistent timer retries as soon as normal data access returns.
        return 0

    render_engines = {
        scene.render.engine
        for scene in scenes
        if getattr(scene, "render", None) is not None
    }
    if not render_engines:
        render_engines = {""}

    added = 0
    for workspace in workspaces:
        for render_engine in render_engines:
            added += _ensure_workspace_owner_ids(workspace, render_engine)
    return added


def _workspace_filter_guard_deferred():
    global _WORKSPACE_FILTER_GUARD_LAST_ERROR

    try:
        _ensure_workspace_owner_filters()
        _WORKSPACE_FILTER_GUARD_LAST_ERROR = ""
    except Exception as exc:
        message = f"Workspace filter guard failed: {exc}"
        if message != _WORKSPACE_FILTER_GUARD_LAST_ERROR:
            print(f"[Character Designer] {message}")
        _WORKSPACE_FILTER_GUARD_LAST_ERROR = message
    return WORKSPACE_FILTER_GUARD_INTERVAL


def _register_workspace_filter_guard():
    _workspace_filter_guard_deferred()
    try:
        if not bpy.app.timers.is_registered(_workspace_filter_guard_deferred):
            bpy.app.timers.register(
                _workspace_filter_guard_deferred,
                first_interval=WORKSPACE_FILTER_GUARD_FIRST_INTERVAL,
                persistent=True,
            )
    except Exception as exc:
        print(f"[Character Designer] Could not start workspace filter guard: {exc}")
        return False
    return True


def _unregister_workspace_filter_guard():
    try:
        if bpy.app.timers.is_registered(_workspace_filter_guard_deferred):
            bpy.app.timers.unregister(_workspace_filter_guard_deferred)
    except Exception:
        pass


def _validate_source_files():
    for path in _source_files():
        with open(path, "rb") as handle:
            compile(handle.read(), path, "exec")


def _tag_view3d_redraw():
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _source_watch_deferred():
    global ADDON_REFRESH_LAST_STATE, ADDON_REFRESH_LAST_ERROR

    try:
        changed = _source_changed()
        if changed != ADDON_REFRESH_LAST_STATE:
            ADDON_REFRESH_LAST_STATE = changed
            _tag_view3d_redraw()
    except Exception as exc:
        ADDON_REFRESH_LAST_ERROR = f"Source watcher failed: {exc}"
        _tag_view3d_redraw()
    return 1.0


def _register_source_watch():
    global ADDON_REFRESH_LAST_STATE, ADDON_REFRESH_LAST_ERROR

    try:
        ADDON_REFRESH_LAST_STATE = _source_changed()
        if not bpy.app.timers.is_registered(_source_watch_deferred):
            bpy.app.timers.register(
                _source_watch_deferred,
                first_interval=1.0,
                persistent=True,
            )
    except Exception as exc:
        ADDON_REFRESH_LAST_ERROR = f"Source watcher failed: {exc}"
        print(f"[Character Designer] Could not start source watcher: {exc}")
        _tag_view3d_redraw()
        return False
    return True


def _unregister_source_watch():
    try:
        if bpy.app.timers.is_registered(_source_watch_deferred):
            bpy.app.timers.unregister(_source_watch_deferred)
    except Exception:
        pass


def _reload_addon_deferred():
    global ADDON_REFRESH_PENDING, ADDON_REFRESH_LAST_ERROR, ADDON_REFRESH_LAST_STATE

    import addon_utils

    module_name = ADDON_MODULE_NAME
    old_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == module_name or name.startswith(f"{module_name}.")
    }
    old_main = old_modules.get(module_name)
    persistent = bool(getattr(old_main, "__addon_persistent__", False)) if old_main else False

    try:
        _stop_live_preview(settings=_settings(bpy.context), clear_capture=True)
        stop_delta_symmetry_runtime(clear_capture=True)
        try:
            addon_utils.disable(module_name, default_set=False, refresh_handled=True)
        except TypeError:
            addon_utils.disable(module_name, default_set=False)

        for name in old_modules:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()

        try:
            reloaded = addon_utils.enable(
                module_name,
                default_set=False,
                persistent=persistent,
                refresh_handled=True,
            )
        except TypeError:
            reloaded = addon_utils.enable(
                module_name,
                default_set=False,
                persistent=persistent,
            )
        if reloaded is None:
            raise RuntimeError("Blender could not enable the refreshed add-on.")
        print("[Character Designer] Add-on refreshed successfully.")
    except Exception as exc:
        traceback.print_exc()
        for name in list(sys.modules):
            if name == module_name or name.startswith(f"{module_name}."):
                sys.modules.pop(name, None)
        sys.modules.update(old_modules)
        if old_main is not None:
            try:
                old_main.ADDON_REFRESH_PENDING = False
                old_main.ADDON_REFRESH_LAST_ERROR = str(exc)
                # register() is deliberately idempotent. If disable failed before
                # unregistering anything this is a no-op; if enable failed after a
                # successful disable it restores the old, known-good classes.
                old_main.register()
                old_main.__addon_enabled__ = True
            except Exception:
                traceback.print_exc()
        ADDON_REFRESH_PENDING = False
        ADDON_REFRESH_LAST_ERROR = str(exc)
    return None


def _mesh_object_poll(_self, obj):
    return obj is not None and obj.type == "MESH"


def _curve_object_poll(_self, obj):
    return obj is not None and obj.type == "CURVE"


def _live_preview_toggle_updated(settings, context):
    if _LIVE_PREVIEW_TOGGLE_GUARD:
        return
    if settings.live_preview_enabled:
        try:
            _set_recovery_preview_enabled(settings, False)
            _start_auto_live_preview(context, settings)
        except Exception as exc:
            message = f"Live Preview could not start: {_short_preview_message(exc)}"
            _set_live_preview_enabled(settings, False)
            _stop_live_preview(settings=settings, clear_capture=True)
            _set_status(settings, "ERROR", message)
            print(f"[Character Designer] {message}")
    else:
        _stop_live_preview(settings=settings, clear_capture=True)


def _set_live_preview_enabled(settings, enabled):
    """Synchronize the UI toggle without recursively invoking its callback."""

    global _LIVE_PREVIEW_TOGGLE_GUARD
    if settings is None:
        return
    _LIVE_PREVIEW_TOGGLE_GUARD = True
    try:
        settings.live_preview_enabled = bool(enabled)
    finally:
        _LIVE_PREVIEW_TOGGLE_GUARD = False


def _recovery_preview_toggle_updated(settings, context):
    if _RECOVERY_PREVIEW_TOGGLE_GUARD:
        return
    if settings.recovery_preview_enabled:
        try:
            _set_live_preview_enabled(settings, False)
            _start_recovery_live_preview(context, settings)
        except Exception as exc:
            message = f"Recovery Preview could not start: {_short_preview_message(exc)}"
            _set_recovery_preview_enabled(settings, False)
            _stop_live_preview(settings=settings, clear_capture=True)
            _set_status(settings, "ERROR", message)
            print(f"[Character Designer] {message}")
    elif settings.preview_mode == "RECOVERY":
        _stop_live_preview(settings=settings, clear_capture=True)


def _set_recovery_preview_enabled(settings, enabled):
    """Synchronize the recovery Preview toggle without callback recursion."""

    global _RECOVERY_PREVIEW_TOGGLE_GUARD
    if settings is None:
        return
    _RECOVERY_PREVIEW_TOGGLE_GUARD = True
    try:
        settings.recovery_preview_enabled = bool(enabled)
    finally:
        _RECOVERY_PREVIEW_TOGGLE_GUARD = False


def _recovery_preview_parameter_updated(settings, _context):
    """Let the timer rebuild a running recovery Preview after one UI edit."""

    global _LIVE_PREVIEW_INPUT_SIGNATURE
    if settings.recovery_preview_enabled and settings.preview_mode == "RECOVERY":
        _LIVE_PREVIEW_INPUT_SIGNATURE = None
        _tag_view3d_redraw()


def _legacy_align_front_surface_get(settings):
    """Expose the old two-state API without keeping a second placement state."""

    return settings.centerline_placement == HAIR_ALIGNMENT_FRONT_FLUSH


def _legacy_align_front_surface_set(settings, enabled):
    settings.centerline_placement = (
        HAIR_ALIGNMENT_FRONT_FLUSH
        if enabled
        else HAIR_ALIGNMENT_CENTERED
    )


def _validated_blend_factor(value):
    """Return one finite 0..1 Blend value without silently extrapolating."""

    if isinstance(value, bool):
        raise CenterlineError("Blend Factor must be a number from 0 to 1.")
    try:
        factor = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CenterlineError("Blend Factor must be a number from 0 to 1.") from exc
    if not math.isfinite(factor) or factor < 0.0 or factor > 1.0:
        raise CenterlineError("Blend Factor must stay between 0 and 1.")
    return factor


def _alignment_factor(alignment, blend_factor=HAIR_BLEND_DEFAULT):
    """Resolve the geometric interpolation factor for one placement mode."""

    if alignment == HAIR_ALIGNMENT_CENTERED:
        return 0.0
    if alignment == HAIR_ALIGNMENT_FRONT_FLUSH:
        return 1.0
    if alignment == HAIR_ALIGNMENT_BLEND:
        return _validated_blend_factor(blend_factor)
    raise CenterlineError(f"Unsupported centerline alignment: {alignment}.")


def _stored_blend_factor(curve_obj):
    """Read a Curve's last Blend value; old fixed-Blend Curves mean exactly 0.5."""

    if HAIR_BLEND_FACTOR_KEY not in curve_obj:
        return HAIR_BLEND_DEFAULT
    return _validated_blend_factor(curve_obj[HAIR_BLEND_FACTOR_KEY])


def _set_centerline_blend_factor_silently(settings, value):
    """Hydrate the UI slider without treating the assignment as an artist edit."""

    global _CENTERLINE_CONTROL_UPDATE_GUARD
    previous_guard = _CENTERLINE_CONTROL_UPDATE_GUARD
    _CENTERLINE_CONTROL_UPDATE_GUARD = True
    try:
        settings.centerline_blend_factor = _validated_blend_factor(value)
    finally:
        _CENTERLINE_CONTROL_UPDATE_GUARD = previous_guard


def _bind_centerline_controls(settings, target):
    """Bind the compact controls to one active generated Curve, or release them."""

    global _CENTERLINE_CONTROL_UPDATE_GUARD
    if target is not None and not _is_character_designer_centerline(target):
        target = None
    current = getattr(settings, "centerline_control_target", None)
    if current is target:
        return

    previous_guard = _CENTERLINE_CONTROL_UPDATE_GUARD
    _CENTERLINE_CONTROL_UPDATE_GUARD = True
    try:
        settings.centerline_control_target = target
        if target is None:
            return
        alignment = target.get(
            "character_designer_alignment",
            HAIR_ALIGNMENT_CENTERED,
        )
        if alignment not in HAIR_ALIGNMENTS:
            alignment = HAIR_ALIGNMENT_CENTERED
        settings.centerline_placement = alignment
        try:
            settings.centerline_blend_factor = _stored_blend_factor(target)
        except CenterlineError:
            settings.centerline_blend_factor = HAIR_BLEND_DEFAULT
    finally:
        _CENTERLINE_CONTROL_UPDATE_GUARD = previous_guard


def _active_centerline_control_target(settings, context):
    """Return only the generated Curve visibly bound to these controls."""

    if context is None or context.mode not in {"OBJECT", "EDIT_CURVE"}:
        return None
    target = getattr(settings, "centerline_control_target", None)
    active = context.active_object
    if target is None or active is None or target.as_pointer() != active.as_pointer():
        return None
    if context.mode == "EDIT_CURVE" and context.edit_object is not target:
        return None
    return target if _is_character_designer_centerline(target) else None


def _centerline_blend_factor_updated(settings, context):
    """Move the bound Blend Curve immediately as its 0..1 Mix is dragged."""

    if _CENTERLINE_CONTROL_UPDATE_GUARD:
        return
    target = _active_centerline_control_target(settings, context)
    if (
        target is None
        or target.get("character_designer_alignment") != HAIR_ALIGNMENT_BLEND
    ):
        return

    try:
        factor = _validated_blend_factor(settings.centerline_blend_factor)
        metadata = _curve_cross_section_metadata(target)
        layers = _stored_centerline_layers(target)
        if metadata is None or metadata.get("version") != METADATA_VERSION:
            raise CenterlineError("Refresh this centerline before changing Mix.")
        _commit_existing_centerline(
            target,
            None,
            layers,
            metadata,
            HAIR_ALIGNMENT_BLEND,
            blend_factor=factor,
        )
        settings.output_object = target
        settings.update_target_curve = target
    except CenterlineError as exc:
        try:
            _set_centerline_blend_factor_silently(
                settings,
                _stored_blend_factor(target),
            )
        except CenterlineError:
            _set_centerline_blend_factor_silently(settings, HAIR_BLEND_DEFAULT)
        _set_status(settings, "INFO", f"Mix unchanged: {exc}")
        _tag_view3d_redraw()
    except Exception as exc:
        traceback.print_exc()
        try:
            _set_centerline_blend_factor_silently(
                settings,
                _stored_blend_factor(target),
            )
        except CenterlineError:
            _set_centerline_blend_factor_silently(settings, HAIR_BLEND_DEFAULT)
        _set_status(settings, "ERROR", f"Mix update failed: {exc}")
        _tag_view3d_redraw()


class CharacterDesignerState(PropertyGroup):
    ui_page: EnumProperty(
        name="CDesigner Page",
        description="Choose which CDesigner tool family is visible",
        items=UI_PAGE_ITEMS,
        default=UI_PAGE_DEFAULT,
        options={"SKIP_SAVE"},
    )
    normalize_affected_deform_weights: BoolProperty(
        name="Full Auto Blend (Normalized)",
        description=(
            "Solve all current-rig Deform bones together, give selected solver "
            "weights first claim after locked weights, and normalize their old/new "
            "influence region from the full solver's local proportions; "
            "non-Deform and locked weights stay unchanged, and outside weights "
            "stay exact except invalid generated-side half-Mesh weights are "
            "cleared; disable for strict selected-only weighting without column "
            "normalization"
        ),
        default=True,
        options={"SKIP_SAVE"},
    )
    root_object: PointerProperty(
        name="Root Object",
        type=bpy.types.Object,
        poll=_mesh_object_poll,
        options={"SKIP_SAVE"},
    )
    root_mesh: PointerProperty(
        name="Root Mesh",
        type=bpy.types.Mesh,
        options={"SKIP_SAVE"},
    )
    root_indices_json: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    root_edges_json: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    root_ignored_indices_json: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    root_kind: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    root_capture_mode: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    root_vertex_count: IntProperty(default=0, min=0, options={"SKIP_SAVE"})
    root_ignored_count: IntProperty(default=0, min=0, options={"SKIP_SAVE"})
    topology_signature: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    output_object: PointerProperty(
        name="Output",
        type=bpy.types.Object,
        poll=_curve_object_poll,
        options={"SKIP_SAVE"},
    )
    update_target_curve: PointerProperty(
        name="Update Target",
        description="Existing Character Designer centerline to update from the current source",
        type=bpy.types.Object,
        poll=_curve_object_poll,
        options={"SKIP_SAVE"},
    )
    centerline_control_target: PointerProperty(
        name="Centerline Control Target",
        description="Generated Curve currently bound to the compact placement controls",
        type=bpy.types.Object,
        poll=_curve_object_poll,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    centerline_placement: EnumProperty(
        name="Placement",
        description=(
            "Choose the Curve-axis placement; the generated Half profile faces "
            "outward in every mode"
        ),
        items=(
            (
                HAIR_ALIGNMENT_CENTERED,
                "Centered",
                "Keep the Curve axis at the source cross-section centers so the "
                "Half profile protrudes outward",
            ),
            (
                HAIR_ALIGNMENT_FRONT_FLUSH,
                "Surface",
                "Move the Curve axis inward so the Half profile's visible front "
                "aligns with the authored outer surface",
            ),
            (
                HAIR_ALIGNMENT_BLEND,
                "Blend",
                "Use Mix to interpolate between Centered (0) and Surface (1) "
                "while keeping the profile facing outward",
            ),
        ),
        default=HAIR_ALIGNMENT_CENTERED,
        options={"SKIP_SAVE"},
    )
    centerline_blend_factor: FloatProperty(
        name="Mix",
        description=(
            "0 keeps the Curve centered; 1 aligns the visible front to the source "
            "surface; the profile faces outward throughout"
        ),
        default=HAIR_BLEND_DEFAULT,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        precision=3,
        update=_centerline_blend_factor_updated,
        options={"SKIP_SAVE"},
    )
    recovery_source_action: EnumProperty(
        name="Output",
        description=(
            "Add keeps the source Mesh; Replace Mesh removes only the source Object "
            "after every selected strand has been recovered successfully"
        ),
        items=(
            (
                RECOVERY_SOURCE_ACTION_ADD,
                "Add",
                "Create or update recovered Curves and keep the source Mesh",
            ),
            (
                RECOVERY_SOURCE_ACTION_REPLACE,
                "Replace Mesh",
                "Replace a fully recovered source Mesh Object with the resulting Curves",
            ),
        ),
        default=RECOVERY_SOURCE_ACTION_ADD,
        update=_recovery_preview_parameter_updated,
        options={"SKIP_SAVE"},
    )
    recovery_curve_mode: EnumProperty(
        name="Curve Type",
        description=(
            "Bezier creates a smaller editable control cage; Exact keeps one "
            "Poly point per recovered mesh cross-section"
        ),
        items=(
            (
                RECOVERY_CURVE_MODE_BEZIER,
                "Bezier",
                "Create a smooth Bezier Curve with the chosen number of control points",
            ),
            (
                RECOVERY_CURVE_MODE_EXACT,
                "Exact",
                "Keep one Poly control point for every recovered mesh cross-section",
            ),
        ),
        default=RECOVERY_CURVE_MODE_BEZIER,
        update=_recovery_preview_parameter_updated,
        options={"SKIP_SAVE"},
    )
    recovery_control_points: IntProperty(
        name="Control Points",
        description=(
            "Number of editable Bezier control points sampled along each recovered strand"
        ),
        default=RECOVERY_CONTROL_POINTS_DEFAULT,
        min=RECOVERY_CONTROL_POINTS_MIN,
        max=RECOVERY_CONTROL_POINTS_MAX,
        soft_min=RECOVERY_CONTROL_POINTS_MIN,
        soft_max=RECOVERY_CONTROL_POINTS_MAX,
        update=_recovery_preview_parameter_updated,
        options={"SKIP_SAVE"},
    )
    recovery_preview_enabled: BoolProperty(
        name="Preview",
        description=(
            "Preview the recovered Curve cage while changing Curve Type and Control Points; "
            "the source Mesh is never removed during Preview"
        ),
        default=False,
        update=_recovery_preview_toggle_updated,
        options={"SKIP_SAVE"},
    )
    align_front_surface: BoolProperty(
        name="Align Front Surface",
        description=(
            "Legacy two-state placement alias: enabled selects Surface and "
            "disabled selects Centered"
        ),
        get=_legacy_align_front_surface_get,
        set=_legacy_align_front_surface_set,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    last_level: StringProperty(default="NONE", options={"HIDDEN", "SKIP_SAVE"})
    last_message: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    live_preview_enabled: BoolProperty(
        name="Live Preview",
        description="Continuously preview a centerline from the current Edit Mode selection",
        default=False,
        update=_live_preview_toggle_updated,
        options={"SKIP_SAVE"},
    )
    preview_active: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    preview_mode: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    preview_level: StringProperty(default="WAITING", options={"HIDDEN", "SKIP_SAVE"})
    preview_valid: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    preview_confirmable: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    preview_layer_count: IntProperty(default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
    preview_message: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    preview_action_error: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    preview_revision: IntProperty(default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
    preview_root_span: FloatProperty(default=0.0, min=0.0, options={"HIDDEN", "SKIP_SAVE"})
    preview_max_span: FloatProperty(default=0.0, min=0.0, options={"HIDDEN", "SKIP_SAVE"})
    preview_tip_span: FloatProperty(default=0.0, min=0.0, options={"HIDDEN", "SKIP_SAVE"})
    preview_orientation_valid: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})


def _settings(context):
    window_manager = getattr(context, "window_manager", None)
    return getattr(window_manager, "character_designer", None)


def _set_status(settings, level, message):
    settings.last_level = level
    settings.last_message = message


def _clear_capture(settings):
    settings.root_object = None
    settings.root_mesh = None
    settings.root_indices_json = ""
    settings.root_edges_json = ""
    settings.root_ignored_indices_json = ""
    settings.root_kind = ""
    settings.root_capture_mode = ""
    settings.root_vertex_count = 0
    settings.root_ignored_count = 0
    settings.topology_signature = ""


def _edit_bmesh(context):
    obj = context.edit_object
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        raise CenterlineError("Enter Mesh Edit Mode on one hair object.")
    if context.object is not obj:
        raise CenterlineError("The edited hair object must be active.")

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    return obj, bm


def _topology_signature(bm):
    """Hash connectivity canonically while preserving polygon winding."""

    digest = hashlib.sha256()
    digest.update(f"v{len(bm.verts)}e{len(bm.edges)}f{len(bm.faces)}|".encode("ascii"))
    edge_keys = sorted(
        tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        for edge in bm.edges
    )
    edge_values = array("i")
    for first, second in edge_keys:
        edge_values.extend((first, second))
    face_keys = []
    for face in bm.faces:
        indices = tuple(vertex.index for vertex in face.verts)
        start = indices.index(min(indices))
        # Rotating a polygon's loop start is semantically irrelevant; reversing
        # it is not, because source winding is used for the saved normal frame.
        face_keys.append(indices[start:] + indices[:start])
    face_keys.sort()
    face_values = array("i")
    for face_key in face_keys:
        face_values.append(len(face_key))
        face_values.extend(face_key)
    digest.update(edge_values.tobytes())
    digest.update(b"|")
    digest.update(face_values.tobytes())
    return digest.hexdigest()


def _indices_from_settings(settings):
    try:
        values = json.loads(settings.root_indices_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CenterlineError("The captured root is invalid. Capture it again.") from exc
    if not isinstance(values, list) or not values:
        raise CenterlineError("Capture a root slice first.")
    try:
        indices = tuple(sorted({int(value) for value in values}))
    except (TypeError, ValueError) as exc:
        raise CenterlineError("The captured root is invalid. Capture it again.") from exc
    if len(indices) != len(values):
        raise CenterlineError("The captured root is invalid. Capture it again.")
    return indices


def _ignored_indices_from_settings(settings):
    if not settings.root_ignored_indices_json:
        return ()
    try:
        values = json.loads(settings.root_ignored_indices_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CenterlineError("The captured cap interior is invalid. Capture the root again.") from exc
    if not isinstance(values, list):
        raise CenterlineError("The captured cap interior is invalid. Capture the root again.")
    try:
        indices = tuple(sorted({int(value) for value in values}))
    except (TypeError, ValueError) as exc:
        raise CenterlineError("The captured cap interior is invalid. Capture the root again.") from exc
    if len(indices) != len(values):
        raise CenterlineError("The captured cap interior is invalid. Capture the root again.")
    return indices


def _root_edges_from_settings(settings):
    try:
        values = json.loads(settings.root_edges_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CenterlineError("The captured root edges are invalid. Capture the root again.") from exc
    if not isinstance(values, list):
        raise CenterlineError("The captured root edges are invalid. Capture the root again.")
    if not values:
        if settings.root_kind == "POINT":
            return ()
        raise CenterlineError("The captured root edges are invalid. Capture the root again.")

    edge_keys = []
    try:
        for value in values:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError
            first, second = sorted((int(value[0]), int(value[1])))
            if first == second:
                raise ValueError
            edge_keys.append((first, second))
    except (TypeError, ValueError) as exc:
        raise CenterlineError("The captured root edges are invalid. Capture the root again.") from exc
    if len(set(edge_keys)) != len(edge_keys):
        raise CenterlineError("The captured root edges are invalid. Capture the root again.")
    return tuple(sorted(edge_keys))


def _adjacency_from_edge_keys(vertex_indices, edge_keys):
    vertex_indices = set(vertex_indices)
    adjacency = {index: set() for index in vertex_indices}
    for first, second in edge_keys:
        if first in vertex_indices and second in vertex_indices:
            adjacency[first].add(second)
            adjacency[second].add(first)
    return adjacency


def _selected_edge_keys(bm, vertex_indices):
    vertex_indices = set(vertex_indices)
    edge_keys = set()
    for vertex_index in vertex_indices:
        for edge in bm.verts[vertex_index].link_edges:
            if not edge.select:
                continue
            first = edge.verts[0].index
            second = edge.verts[1].index
            if first in vertex_indices and second in vertex_indices:
                edge_keys.add(tuple(sorted((first, second))))
    return tuple(sorted(edge_keys))


def _contained_edge_keys(bm, vertex_indices):
    """Return source-topology edges whose endpoints are both in the vertex set.

    Live selection inference deliberately follows the selected vertices rather
    than Blender's current vertex/edge/face select mode.  BMesh normally flushes
    edge selection for us, but using the source connectivity here makes the
    result identical in all three modes.
    """

    vertex_indices = set(vertex_indices)
    edge_keys = set()
    for vertex_index in vertex_indices:
        for edge in bm.verts[vertex_index].link_edges:
            first = edge.verts[0].index
            second = edge.verts[1].index
            if first in vertex_indices and second in vertex_indices:
                edge_keys.add(tuple(sorted((first, second))))
    return tuple(sorted(edge_keys))


def _is_connected(indices, adjacency):
    indices = set(indices)
    if not indices:
        return False
    visited = set()
    queue = deque((next(iter(indices)),))
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        queue.extend((adjacency[current] & indices) - visited)
    return visited == indices


def _classify_slice(indices, adjacency, *, label):
    indices = set(indices)
    count = len(indices)
    if count == 1:
        return "POINT"
    if not _is_connected(indices, adjacency):
        raise CenterlineError(f"{label} is split into multiple pieces.")

    degrees = {index: len(adjacency[index] & indices) for index in indices}
    edge_count = sum(degrees.values()) // 2
    if count >= 3 and edge_count == count and all(value == 2 for value in degrees.values()):
        return "CLOSED"
    if edge_count == count - 1:
        end_count = sum(value == 1 for value in degrees.values())
        middle_ok = all(value in {1, 2} for value in degrees.values())
        if end_count == 2 and middle_ok:
            return "OPEN"
    raise CenterlineError(
        f"{label} branches or is not one simple row/loop. Select one clean cross-section."
    )


def _root_label(kind):
    return {
        "POINT": "point root",
        "CLOSED": "closed loop",
        "OPEN": "open row",
    }.get(kind, "cross-section")


def _captured_root_label(settings):
    if settings.root_capture_mode in {"CAP", "FAN_CAP", "TRI_CAP", "REGION_CAP"}:
        return "cap boundary"
    return _root_label(settings.root_kind)


def _slice_edge_keys(indices, adjacency):
    indices = set(indices)
    return {
        tuple(sorted((first, second)))
        for first in indices
        for second in adjacency[first] & indices
        if first < second
    }


def _validate_face_bands(bm, selected, layers, distances, adjacency):
    """Confirm that graph layers are backed by complete quad surface bands."""

    final_layer_index = len(layers) - 1
    layer_edge_keys = tuple(_slice_edge_keys(layer, adjacency) for layer in layers)
    band_faces = defaultdict(list)
    relevant_band_faces = set()
    candidate_faces = {
        face
        for vertex_index in selected
        for face in bm.verts[vertex_index].link_faces
    }
    for face in sorted(candidate_faces, key=lambda value: value.index):
        indices = tuple(vertex.index for vertex in face.verts)
        if not all(index in selected for index in indices):
            continue
        face_layers = {distances[index] for index in indices}
        if len(face_layers) == 1:
            layer_index = next(iter(face_layers))
            if layer_index not in {0, final_layer_index}:
                raise CenterlineError(
                    f"Layer {layer_index + 1} contains an internal cap face."
                )
            continue
        if len(face_layers) != 2:
            raise CenterlineError("A selected face crosses more than one layer.")

        lower_layer = min(face_layers)
        upper_layer = max(face_layers)
        if upper_layer != lower_layer + 1:
            raise CenterlineError("A selected face skips a centerline layer.")
        lower_vertices = tuple(index for index in indices if distances[index] == lower_layer)
        upper_vertices = tuple(index for index in indices if distances[index] == upper_layer)
        lower_is_point = len(layers[lower_layer]) == 1
        upper_is_point = len(layers[upper_layer]) == 1
        if lower_is_point:
            valid_counts = (
                not upper_is_point
                and len(face.verts) == 3
                and len(lower_vertices) == 1
                and len(upper_vertices) == 2
            )
            expected = "a 1+2 root triangle"
        elif upper_is_point:
            valid_counts = (
                len(face.verts) == 3
                and len(lower_vertices) == 2
                and len(upper_vertices) == 1
            )
            expected = "a 2+1 tip triangle"
        else:
            valid_counts = (
                len(face.verts) == 4
                and len(lower_vertices) == 2
                and len(upper_vertices) == 2
            )
            expected = "a 2+2 quad"
        if not valid_counts:
            raise CenterlineError(
                f"The band between layers {lower_layer + 1} and {upper_layer + 1} "
                f"contains a face that is not {expected}."
            )

        face_edges = {
            tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
            for edge in face.edges
        }
        lower_edge = None
        if not lower_is_point:
            lower_edge = tuple(sorted(lower_vertices))
            if lower_edge not in face_edges or lower_edge not in layer_edge_keys[lower_layer]:
                raise CenterlineError(
                    f"A face between layers {lower_layer + 1} and {upper_layer + 1} is twisted."
                )
        upper_edge = None
        if not upper_is_point:
            upper_edge = tuple(sorted(upper_vertices))
            if upper_edge not in face_edges or upper_edge not in layer_edge_keys[upper_layer]:
                raise CenterlineError(
                    f"A face between layers {lower_layer + 1} and {upper_layer + 1} is twisted."
                )
        band_faces[lower_layer].append((lower_edge, upper_edge))
        relevant_band_faces.add(face)

    # The undirected edge/face counts above cannot distinguish a coherent band
    # from one containing a flipped polygon.  Check every shared band edge with
    # BMesh's winding-aware manifold predicate before any normals are averaged
    # into persistent orientation metadata.
    relevant_band_edges = {
        edge for face in relevant_band_faces for edge in face.edges
    }
    for edge in sorted(relevant_band_edges, key=lambda value: value.index):
        linked_band_faces = [
            face for face in edge.link_faces if face in relevant_band_faces
        ]
        if len(linked_band_faces) > 2:
            first, second = sorted(vertex.index for vertex in edge.verts)
            raise CenterlineError(
                f"Selected side-band edge {first}-{second} is non-manifold."
            )
        if len(linked_band_faces) == 2:
            first, second = sorted(vertex.index for vertex in edge.verts)
            if not edge.is_manifold:
                raise CenterlineError(
                    f"Selected side-band edge {first}-{second} is non-manifold."
                )
            if not edge.is_contiguous:
                raise CenterlineError(
                    "Selected side-band faces have inconsistent winding across "
                    f"edge {first}-{second}."
                )

    for lower_layer in range(final_layer_index):
        upper_layer = lower_layer + 1
        actual_faces = band_faces.get(lower_layer, [])
        lower_is_point = len(layers[lower_layer]) == 1
        upper_is_point = len(layers[upper_layer]) == 1

        if lower_is_point:
            expected_upper_edges = layer_edge_keys[upper_layer]
            actual_upper_edges = [upper_edge for _lower_edge, upper_edge in actual_faces]
            if (
                len(actual_faces) != len(expected_upper_edges)
                or set(actual_upper_edges) != expected_upper_edges
                or len(set(actual_upper_edges)) != len(actual_upper_edges)
            ):
                raise CenterlineError(
                    f"Layers {lower_layer + 1} and {upper_layer + 1} do not have "
                    "one complete point-root triangle band."
                )
            continue

        expected_lower_edges = layer_edge_keys[lower_layer]
        actual_lower_edges = [lower_edge for lower_edge, _upper_edge in actual_faces]
        if (
            len(actual_faces) != len(expected_lower_edges)
            or set(actual_lower_edges) != expected_lower_edges
            or len(set(actual_lower_edges)) != len(actual_lower_edges)
        ):
            raise CenterlineError(
                f"Layers {lower_layer + 1} and {upper_layer + 1} do not have one complete face band."
            )

        if not upper_is_point:
            expected_upper_edges = layer_edge_keys[upper_layer]
            actual_upper_edges = [upper_edge for _lower_edge, upper_edge in actual_faces]
            if (
                set(actual_upper_edges) != expected_upper_edges
                or len(set(actual_upper_edges)) != len(actual_upper_edges)
            ):
                raise CenterlineError(
                    f"Layers {lower_layer + 1} and {upper_layer + 1} have mismatched quad faces."
                )


def _selection_counts(bm):
    return (
        sum(vertex.select for vertex in bm.verts),
        sum(edge.select for edge in bm.edges),
        sum(face.select for face in bm.faces),
    )


def _selected_faces_are_one_edge_component(selected_faces):
    face_set = set(selected_faces)
    visited = set()
    queue = deque((selected_faces[0],))
    while queue:
        face = queue.popleft()
        if face in visited:
            continue
        visited.add(face)
        for edge in face.edges:
            queue.extend(
                linked_face
                for linked_face in edge.link_faces
                if linked_face in face_set and linked_face not in visited
            )
    return visited == face_set


def _capture_selected_face_boundary(bm, selected_faces, *, allow_extra_selection=False):
    """Extract one cap boundary from a face-connected topological disk.

    The selected faces may use any polygon sizes, but their complete boundary
    must meet one provably regular, unselected side band.  That positive collar
    test is important: a whole card, a partial card, and one arbitrary side face
    can all be topological disks too, but none is the cap of a regular tube.
    """

    selected_faces = tuple(selected_faces)
    if not _selected_faces_are_one_edge_component(selected_faces):
        raise CenterlineError("The selected cap contains multiple disconnected face islands.")

    region_vertices = {vertex for face in selected_faces for vertex in face.verts}
    region_edges = {edge for face in selected_faces for edge in face.edges}
    extra_vertices = {vertex for vertex in bm.verts if vertex.select} - region_vertices
    extra_edges = {edge for edge in bm.edges if edge.select} - region_edges
    if not allow_extra_selection and (extra_vertices or extra_edges):
        raise CenterlineError("Do not mix a cap selection with extra vertices or edges.")

    face_incidence = defaultdict(int)
    for face in selected_faces:
        for edge in face.edges:
            face_incidence[edge] += 1
    if any(count > 2 for count in face_incidence.values()):
        raise CenterlineError("The selected cap contains a non-manifold shared edge.")
    if any(len(edge.link_faces) > 2 for edge in region_edges):
        raise CenterlineError("The selected cap touches non-manifold mesh geometry.")

    boundary_edges = {edge for edge, count in face_incidence.items() if count == 1}
    if not boundary_edges:
        raise CenterlineError("The selected cap has no open boundary.")
    boundary_vertices = {vertex for edge in boundary_edges for vertex in edge.verts}
    boundary_edge_keys = tuple(
        sorted(
            tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
            for edge in boundary_edges
        )
    )
    boundary_indices = tuple(sorted(vertex.index for vertex in boundary_vertices))
    boundary_adjacency = _adjacency_from_edge_keys(boundary_indices, boundary_edge_keys)
    try:
        boundary_kind = _classify_slice(
            boundary_indices,
            boundary_adjacency,
            label="The cap boundary",
        )
    except CenterlineError as exc:
        raise CenterlineError("The selected cap must have exactly one simple boundary loop.") from exc
    if boundary_kind != "CLOSED":
        raise CenterlineError("The selected cap boundary is not closed.")

    interior_vertices = region_vertices - boundary_vertices
    if len(region_vertices) - len(region_edges) + len(selected_faces) != 1:
        raise CenterlineError("The selected faces are not one topological disk.")

    # Every enclosed vertex must really be enclosed by the selected region.  An
    # unselected incident edge means that the artist selected only part of a
    # larger triangulated surface and the apparent boundary is misleading.
    if any(
        edge not in region_edges
        for vertex in interior_vertices
        for edge in vertex.link_edges
    ):
        raise CenterlineError(
            "A cap interior vertex is connected to geometry outside the selected region."
        )

    # A cap boundary vertex has exactly one longitudinal edge into the first
    # unselected side band.  This gives an explicit boundary -> next-layer map,
    # rather than trying to infer cap semantics from polygon sizes.
    next_vertex_by_boundary = {}
    leaving_edge_by_boundary = {}
    for vertex in boundary_vertices:
        leaving_edges = [edge for edge in vertex.link_edges if edge not in region_edges]
        if len(leaving_edges) != 1:
            raise CenterlineError(
                "The selected face region has no complete regular first collar: "
                "each cap boundary vertex needs exactly one outgoing side edge."
            )
        leaving_edge = leaving_edges[0]
        next_vertex = leaving_edge.other_vert(vertex)
        if next_vertex in region_vertices:
            raise CenterlineError(
                "The selected face region has an extra chord instead of a regular first collar."
            )
        leaving_edge_by_boundary[vertex] = leaving_edge
        next_vertex_by_boundary[vertex] = next_vertex

    next_vertices = set(next_vertex_by_boundary.values())
    normal_collar = len(next_vertices) == len(boundary_vertices)
    collapsed_collar = len(next_vertices) == 1
    if not normal_collar and not collapsed_collar:
        raise CenterlineError(
            "The selected cap collar branches between its boundary and the next layer."
        )

    # Every boundary edge must own exactly one outside band face.  For a normal
    # collar that face is the exact quad (root edge, two longitudinal edges,
    # outer edge); a uniformly collapsed collar uses one triangle and a shared
    # point tip.  Checking edge sets as well as vertices rejects twisted faces.
    selected_face_set = set(selected_faces)
    outside_face_use = defaultdict(int)
    outer_edge_keys = []
    for edge in boundary_edges:
        first, second = edge.verts
        next_first = next_vertex_by_boundary[first]
        next_second = next_vertex_by_boundary[second]
        outside_faces = [face for face in edge.link_faces if face not in selected_face_set]
        if len(outside_faces) != 1:
            raise CenterlineError(
                "The selected face region has no complete regular first collar: "
                "every cap edge must meet one unselected side face."
            )
        outside_face = outside_faces[0]
        outside_face_use[outside_face] += 1

        expected_vertices = {first, second, next_first, next_second}
        expected_edge_keys = {
            tuple(sorted((first.index, second.index))),
            tuple(sorted((first.index, next_first.index))),
            tuple(sorted((second.index, next_second.index))),
        }
        if normal_collar:
            expected_edge_keys.add(tuple(sorted((next_first.index, next_second.index))))
            expected_face_size = 4
        else:
            expected_face_size = 3
        actual_edge_keys = {
            tuple(sorted((face_edge.verts[0].index, face_edge.verts[1].index)))
            for face_edge in outside_face.edges
        }
        if (
            len(outside_face.verts) != expected_face_size
            or set(outside_face.verts) != expected_vertices
            or actual_edge_keys != expected_edge_keys
        ):
            expected_label = "quad" if normal_collar else "tip triangle"
            raise CenterlineError(
                f"The first collar needs one untwisted {expected_label} per cap boundary edge."
            )
        if leaving_edge_by_boundary[first] not in outside_face.edges or (
            leaving_edge_by_boundary[second] not in outside_face.edges
        ):
            raise CenterlineError("A first-collar face does not use its mapped side edges.")
        if normal_collar:
            outer_edge_keys.append(tuple(sorted((next_first.index, next_second.index))))

    if any(count != 1 for count in outside_face_use.values()):
        raise CenterlineError(
            "Each first-collar face must correspond to exactly one cap boundary edge."
        )

    if normal_collar:
        next_indices = tuple(sorted(vertex.index for vertex in next_vertices))
        outer_adjacency = _adjacency_from_edge_keys(next_indices, outer_edge_keys)
        try:
            outer_kind = _classify_slice(
                next_indices,
                outer_adjacency,
                label="The first collar outer edge",
            )
        except CenterlineError as exc:
            raise CenterlineError(
                "The first collar outer edges do not form one simple closed loop."
            ) from exc
        if outer_kind != "CLOSED":
            raise CenterlineError(
                "The first collar outer edges do not form one simple closed loop."
            )

    ignored_indices = tuple(sorted(vertex.index for vertex in interior_vertices))
    return boundary_indices, boundary_edge_keys, ignored_indices, "REGION_CAP"


def _capture_data(context):
    obj, bm = _edit_bmesh(context)
    selected_faces = tuple(face for face in bm.faces if face.select)
    if selected_faces:
        root_indices, edge_keys, ignored_indices, capture_mode = _capture_selected_face_boundary(
            bm,
            selected_faces,
        )
        return obj, bm, root_indices, edge_keys, "CLOSED", ignored_indices, capture_mode

    selected = {vertex.index for vertex in bm.verts if vertex.select}
    if not selected:
        raise CenterlineError(
            "Select one root point, one root edge row, or one root cap face, then capture."
        )

    edge_keys = _selected_edge_keys(bm, selected)
    adjacency = _adjacency_from_edge_keys(selected, edge_keys)
    kind = _classify_slice(selected, adjacency, label="The root selection")
    capture_mode = "POINT" if kind == "POINT" else "EDGES"
    return obj, bm, tuple(sorted(selected)), edge_keys, kind, (), capture_mode


def _validate_capture(context, settings, *, check_topology=True):
    obj, bm = _edit_bmesh(context)
    if settings.root_object is None:
        raise CenterlineError("Capture a root slice first.")
    if obj is not settings.root_object or obj.data is not settings.root_mesh:
        raise CenterlineError("Return to the captured hair object, or capture a new root.")
    if check_topology and _topology_signature(bm) != settings.topology_signature:
        raise TopologyChangedError("Hair topology changed after capture. Capture the root again.")

    root_indices = _indices_from_settings(settings)
    root_edge_keys = _root_edges_from_settings(settings)
    ignored_indices = _ignored_indices_from_settings(settings)
    if root_indices[-1] >= len(bm.verts) or (
        ignored_indices and ignored_indices[-1] >= len(bm.verts)
    ):
        raise TopologyChangedError("Hair topology changed after capture. Capture the root again.")
    root_set = set(root_indices)
    ignored_set = set(ignored_indices)
    if root_set & ignored_set:
        raise CenterlineError("The captured cap boundary overlaps its interior. Capture it again.")
    if any(first not in root_set or second not in root_set for first, second in root_edge_keys):
        raise CenterlineError("The captured root edges are invalid. Capture the root again.")
    adjacency = _adjacency_from_edge_keys(root_set, root_edge_keys)
    kind = _classify_slice(root_set, adjacency, label="The captured root")
    if kind != settings.root_kind:
        raise TopologyChangedError("The captured root topology changed. Capture it again.")
    return obj, bm, root_indices, root_edge_keys, ignored_indices


def _extract_layers_from_seed(
    obj,
    bm,
    selected,
    root_indices,
    root_edge_keys,
    *,
    selected_edge_keys=None,
    allow_coincident_endpoint_points=False,
):
    """Strictly validate and layer one selected strip from an explicit endpoint."""

    selected = set(selected)
    root_indices = tuple(sorted(root_indices))
    root_edge_keys = tuple(sorted(root_edge_keys))
    root_set = set(root_indices)
    missing = root_set - selected
    if missing:
        raise CenterlineError("Keep the captured root selected while growing toward the tip.")
    if selected == root_set:
        raise CenterlineError("Only the root is selected. Grow the selection toward the tip.")

    if selected_edge_keys is None:
        selected_edge_keys = _selected_edge_keys(bm, selected)
    selected_edge_keys = [
        edge_key
        for edge_key in selected_edge_keys
        if not (edge_key[0] in root_set and edge_key[1] in root_set)
    ]
    selected_edge_keys.extend(root_edge_keys)
    selected_edge_keys = tuple(sorted(set(selected_edge_keys)))
    adjacency = _adjacency_from_edge_keys(selected, selected_edge_keys)
    distances = {index: 0 for index in root_set}
    queue = deque(root_indices)
    while queue:
        current = queue.popleft()
        next_distance = distances[current] + 1
        for neighbor in adjacency[current]:
            if neighbor not in distances:
                distances[neighbor] = next_distance
                queue.append(neighbor)
    unreachable = selected - distances.keys()
    if unreachable:
        raise CenterlineError(
            f"The selection contains {len(unreachable)} vertex/vertices disconnected from the root."
        )

    grouped = defaultdict(list)
    for index, distance in distances.items():
        grouped[distance].append(index)
    layers = [tuple(sorted(grouped[distance])) for distance in range(max(grouped) + 1)]
    if set(layers[0]) != root_set:
        raise CenterlineError("The captured root could not be reconstructed. Capture it again.")

    regular_kind = None
    for layer_index, layer in enumerate(layers):
        kind = _classify_slice(
            layer,
            adjacency,
            label=f"Layer {layer_index + 1}",
        )
        is_last = layer_index == len(layers) - 1
        if kind == "POINT":
            if layer_index != 0 and not is_last:
                raise CenterlineError(
                    f"Layer {layer_index + 1} is a point between regular cross-sections. "
                    "A point is supported only at the root or tip."
                )
        elif regular_kind is None:
            regular_kind = kind
        elif kind != regular_kind:
            raise CenterlineError(
                f"Layer {layer_index + 1} changes from {_root_label(regular_kind)} "
                f"to {_root_label(kind)}. Check the selection."
            )

        if layer_index == 0:
            continue
        previous = set(layers[layer_index - 1])
        current = set(layer)
        cross_edges = []
        for previous_index in previous:
            for current_index in adjacency[previous_index] & current:
                cross_edges.append((previous_index, current_index))

        previous_degree = defaultdict(int)
        current_degree = defaultdict(int)
        for previous_index, current_index in cross_edges:
            previous_degree[previous_index] += 1
            current_degree[current_index] += 1

        previous_is_point = len(previous) == 1
        current_is_point = len(current) == 1
        if previous_is_point:
            # A point root expands through one complete triangle fan into the
            # first regular open row or closed loop. Every vertex in that first
            # section must connect directly and only once to the root point.
            root_index = next(iter(previous))
            complete = (
                layer_index == 1
                and not current_is_point
                and len(cross_edges) == len(current)
                and previous_degree[root_index] == len(current)
                and all(current_degree[index] == 1 for index in current)
            )
        elif current_is_point:
            # A collapsed point tip is valid only when every vertex in the last
            # regular slice connects directly to that one final vertex.
            complete = (
                is_last
                and len(cross_edges) == len(previous)
                and all(previous_degree[index] == 1 for index in previous)
                and current_degree[next(iter(current))] == len(previous)
            )
        else:
            # The artist's source is a regular quad strip/tube. One-to-one
            # longitudinal edges make each graph-distance layer unambiguous;
            # many-to-many links would let BFS hide a branch or triangulated
            # shortcut inside an apparently valid row.
            complete = (
                len(previous) == len(current) == len(cross_edges)
                and all(previous_degree[index] == 1 for index in previous)
                and all(current_degree[index] == 1 for index in current)
            )
        if not complete:
            band_label = (
                "one complete point-root triangle band"
                if previous_is_point
                else "one regular quad band"
            )
            raise CenterlineError(
                f"Layers {layer_index} and {layer_index + 1} do not form {band_label}."
            )

    if regular_kind is None:
        raise CenterlineError("The selection needs at least one regular cross-section after the point root.")

    _validate_face_bands(bm, selected, layers, distances, adjacency)

    _validate_layer_coordinates(bm, layers)
    _validate_source_matrix(obj)
    centers = []
    for layer_index, layer in enumerate(layers):
        center = sum((bm.verts[index].co for index in layer), Vector((0.0, 0.0, 0.0))) / len(layer)
        if not _vector_is_finite(center):
            raise CenterlineError(
                f"Layer {layer_index + 1} has a non-finite center point."
            )
        coincident_with_previous = centers and (center - centers[-1]).length <= EPSILON
        endpoint_point_pair = bool(
            allow_coincident_endpoint_points
            and coincident_with_previous
            and (
                (layer_index == 1 and len(layers[0]) == 1)
                or (layer_index == len(layers) - 1 and len(layer) == 1)
            )
        )
        if coincident_with_previous and not endpoint_point_pair:
            raise CenterlineError(
                f"Layers {layer_index} and {layer_index + 1} have the same center point."
            )
        centers.append(center.copy())
    return obj, bm, tuple(layers), tuple(centers)


def _extract_layers(
    context,
    settings,
    *,
    check_topology=True,
    validated_capture=None,
):
    if validated_capture is None:
        validated_capture = _validate_capture(
            context,
            settings,
            check_topology=check_topology,
        )
    obj, bm, root_indices, root_edge_keys, ignored_indices = validated_capture
    ignored_set = set(ignored_indices)
    selected = {
        vertex.index
        for vertex in bm.verts
        if vertex.select and vertex.index not in ignored_set
    }
    return _extract_layers_from_seed(
        obj,
        bm,
        selected,
        root_indices,
        root_edge_keys,
    )


def _regular_kind_from_layers(bm, layers):
    """Return the first non-point layer kind for a captured POINT root."""

    for layer_index, layer in enumerate(layers):
        if len(layer) == 1:
            continue
        edge_keys = _contained_edge_keys(bm, layer)
        adjacency = _adjacency_from_edge_keys(layer, edge_keys)
        kind = _classify_slice(
            layer,
            adjacency,
            label=f"Layer {layer_index + 1}",
        )
        if kind in {"OPEN", "CLOSED"}:
            return kind
    raise CenterlineError("A point root needs a following open row or closed loop.")


def _active_selection_hint(bm):
    """Return the active selected element as a small endpoint hint."""

    active = getattr(bm.select_history, "active", None)
    if active is None or not getattr(active, "select", False):
        return None
    if isinstance(active, bmesh.types.BMVert):
        return "VERT", frozenset((active.index,))
    if isinstance(active, bmesh.types.BMEdge):
        return "EDGE", frozenset(vertex.index for vertex in active.verts)
    if isinstance(active, bmesh.types.BMFace):
        return "FACE", frozenset(vertex.index for vertex in active.verts)
    return None


def _ordered_simple_cycle(indices, adjacency):
    """Return one deterministic traversal of a simple closed boundary."""

    indices = set(indices)
    if len(indices) < 3 or any(len(adjacency[index] & indices) != 2 for index in indices):
        return ()
    start = min(indices)
    ordered = [start]
    previous = None
    current = start
    while True:
        choices = sorted((adjacency[current] & indices) - ({previous} if previous is not None else set()))
        if not choices:
            return ()
        following = choices[0]
        if previous is None and len(choices) == 2:
            following = choices[0]
        if following == start:
            return tuple(ordered) if len(ordered) == len(indices) else ()
        if following in ordered:
            return ()
        ordered.append(following)
        previous, current = current, following


def _boundary_slice_candidates(bm, selected, selected_adjacency):
    """Infer endpoint rows/loops from the boundary of the selected face region."""

    selected = set(selected)
    contained_faces = [
        face
        for face in bm.faces
        if face.verts and all(vertex.index in selected for vertex in face.verts)
    ]
    face_incidence = defaultdict(int)
    for face in contained_faces:
        for edge in face.edges:
            face_incidence[edge] += 1
    boundary_edge_keys = tuple(
        sorted(
            tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
            for edge, count in face_incidence.items()
            if count == 1
        )
    )
    if not boundary_edge_keys:
        return ()

    boundary_vertices = {index for edge_key in boundary_edge_keys for index in edge_key}
    boundary_adjacency = _adjacency_from_edge_keys(boundary_vertices, boundary_edge_keys)
    components = []
    remaining = set(boundary_vertices)
    while remaining:
        seed = min(remaining)
        component = set()
        queue = deque((seed,))
        while queue:
            current = queue.popleft()
            if current in component:
                continue
            component.add(current)
            queue.extend((boundary_adjacency[current] & remaining) - component)
        remaining -= component
        components.append(component)

    candidates = []
    for component in components:
        try:
            boundary_kind = _classify_slice(
                component,
                boundary_adjacency,
                label="The selected-region boundary",
            )
        except CenterlineError:
            continue

        if boundary_kind == "OPEN":
            candidates.append(tuple(sorted(component)))
            continue

        # A closed-profile tube has a complete endpoint loop whose vertices
        # each also have one longitudinal edge (so none has selected degree 2).
        # A flat open strip instead has one perimeter cycle with four graph
        # corners. Split that perimeter at its corners into four possible rows;
        # the strict band validator, plus the active edge when needed, resolves
        # which pair is the intended cross-section direction.
        corners = sorted(
            index
            for index in component
            if len(selected_adjacency[index] & selected) == 2
        )
        if not corners:
            candidates.append(tuple(sorted(component)))
            continue
        if len(corners) != 4:
            continue
        ordered = _ordered_simple_cycle(component, boundary_adjacency)
        if not ordered:
            continue
        corner_positions = sorted(ordered.index(index) for index in corners)
        for position, start_position in enumerate(corner_positions):
            end_position = corner_positions[(position + 1) % len(corner_positions)]
            if end_position > start_position:
                row = ordered[start_position : end_position + 1]
            else:
                row = ordered[start_position:] + ordered[: end_position + 1]
            candidates.append(tuple(sorted(row)))
    return tuple(candidates)


def _faces_are_coplanar(reference, candidate):
    reference.normal_update()
    candidate.normal_update()
    reference_normal = _unit_vector_or_none(reference.normal)
    candidate_normal = _unit_vector_or_none(candidate.normal)
    if reference_normal is None or candidate_normal is None:
        return False
    if reference_normal.dot(candidate_normal) < 0.9995:
        return False
    origin = reference.verts[0].co
    scale = max(
        (edge.calc_length() for edge in (*reference.edges, *candidate.edges)),
        default=1.0,
    )
    tolerance = max(EPSILON * 100.0, scale * 1.0e-4)
    return all(
        abs((vertex.co - origin).dot(reference_normal)) <= tolerance
        for vertex in candidate.verts
    )


def _coplanar_selected_face_regions(bm, selected):
    """Group likely endpoint-cap faces without relying on face select mode."""

    selected = set(selected)
    candidates = {
        face
        for vertex_index in selected
        for face in bm.verts[vertex_index].link_faces
        if all(vertex.index in selected for vertex in face.verts)
    }
    regions = []
    remaining = set(candidates)
    while remaining:
        seed = min(remaining, key=lambda face: face.index)
        region = set()
        queue = deque((seed,))
        while queue:
            face = queue.popleft()
            if face in region or face not in remaining:
                continue
            if face is not seed and not _faces_are_coplanar(seed, face):
                continue
            region.add(face)
            for edge in face.edges:
                queue.extend(
                    linked
                    for linked in edge.link_faces
                    if linked in remaining and linked not in region
                )
        remaining -= region
        if region and (
            len(region) >= 2 or any(len(face.verts) != 4 for face in region)
        ):
            regions.append(tuple(sorted(region, key=lambda face: face.index)))
    return tuple(regions)


def _inferred_cap_results(obj, bm, selected):
    """Fallback for full selections whose endpoint cap contains chords/interior verts."""

    captures = []
    seen_boundaries = set()
    for region in _coplanar_selected_face_regions(bm, selected):
        try:
            (
                root_indices,
                root_edge_keys,
                ignored_indices,
                _capture_mode,
            ) = _capture_selected_face_boundary(
                bm,
                region,
                allow_extra_selection=True,
            )
        except CenterlineError:
            continue
        root_indices = tuple(root_indices)
        if root_indices in seen_boundaries:
            continue
        seen_boundaries.add(root_indices)
        captures.append((root_indices, tuple(root_edge_keys), tuple(ignored_indices)))

    if not captures:
        return ()

    all_ignored = {
        index
        for _root_indices, _root_edge_keys, ignored_indices in captures
        for index in ignored_indices
    }
    semantic_selected = set(selected) - all_ignored
    semantic_edge_keys = set(_contained_edge_keys(bm, semantic_selected))
    for root_indices, root_edge_keys, _ignored_indices in captures:
        root_set = set(root_indices)
        semantic_edge_keys = {
            edge_key
            for edge_key in semantic_edge_keys
            if not (edge_key[0] in root_set and edge_key[1] in root_set)
        }
        semantic_edge_keys.update(root_edge_keys)
    semantic_edge_keys = tuple(sorted(semantic_edge_keys))

    results = []
    for root_indices, root_edge_keys, _ignored_indices in captures:
        try:
            if semantic_selected == set(root_indices):
                _validate_layer_coordinates(bm, (root_indices,))
                _validate_source_matrix(obj)
                center = sum(
                    (bm.verts[index].co for index in root_indices),
                    Vector((0.0, 0.0, 0.0)),
                ) / len(root_indices)
                results.append(((tuple(root_indices),), (center.copy(),), True))
                continue
            _source_obj, _source_bm, layers, centers = _extract_layers_from_seed(
                obj,
                bm,
                semantic_selected,
                root_indices,
                root_edge_keys,
                selected_edge_keys=semantic_edge_keys,
                allow_coincident_endpoint_points=True,
            )
        except CenterlineError:
            continue
        results.append((layers, centers, True))
    return tuple(results)


def _hint_endpoint_side(hint, layers):
    if hint is None:
        return 0
    kind, indices = hint
    first = set(layers[0])
    last = set(layers[-1])
    if kind == "FACE" and len(layers) > 1:
        first_region = first | set(layers[1])
        last_region = last | set(layers[-2])
        first_matches = bool(indices & first) and indices.issubset(first_region)
        last_matches = bool(indices & last) and indices.issubset(last_region)
    else:
        first_matches = indices.issubset(first)
        last_matches = indices.issubset(last)
    if first_matches == last_matches:
        return 0
    return -1 if first_matches else 1


def _hint_matches_endpoint(hint, layers):
    return _hint_endpoint_side(hint, layers) != 0


def _orient_inferred_layers(layers, centers, active_hint):
    """Orient root-to-tip, preferring a collapsed point as the final tip."""

    first_is_point = len(layers[0]) == 1
    last_is_point = len(layers[-1]) == 1
    if first_is_point != last_is_point:
        if first_is_point:
            return tuple(reversed(layers)), tuple(reversed(centers)), True
        return layers, centers, True

    side = _hint_endpoint_side(active_hint, layers)
    if side == 1:
        return tuple(reversed(layers)), tuple(reversed(centers)), True
    if side == -1:
        return layers, centers, True

    # Reversal does not change the preview geometry. Keep a deterministic
    # ordering so the artist still sees the complete centerline, but require an
    # active endpoint before Confirm can commit directional metadata.
    if layers[-1] < layers[0]:
        return tuple(reversed(layers)), tuple(reversed(centers)), False
    return layers, centers, False


def _closed_layer_normal(bm, layer):
    edge_keys = _contained_edge_keys(bm, layer)
    adjacency = _adjacency_from_edge_keys(layer, edge_keys)
    ordered = _ordered_simple_cycle(layer, adjacency)
    if not ordered:
        return None
    center = sum(
        (bm.verts[index].co for index in ordered),
        Vector((0.0, 0.0, 0.0)),
    ) / len(ordered)
    normal = Vector((0.0, 0.0, 0.0))
    for position, index in enumerate(ordered):
        following = ordered[(position + 1) % len(ordered)]
        normal += (bm.verts[index].co - center).cross(
            bm.verts[following].co - center
        )
    return _unit_vector_or_none(normal)


def _collapsed_point_role(bm, layers, centers, regular_kind):
    """Distinguish a projected tip from a coplanar triangulated cap hub."""

    if len(layers) < 2 or len(layers[-1]) != 1:
        return "NONE"
    outgoing_delta = centers[-1] - centers[-2]
    if regular_kind == "CLOSED":
        profile_diameter, _pair, _vector = _layer_diameter(bm, layers[-2])
        if outgoing_delta.length <= max(EPSILON * 10.0, profile_diameter * 1.0e-4):
            return "CAP"
    outgoing = _unit_vector_or_none(outgoing_delta)
    if outgoing is None:
        # A point exactly at the center of a closed endpoint profile has no
        # usable outgoing segment and is the canonical triangulated-cap hub.
        return "CAP" if regular_kind == "CLOSED" else "AMBIGUOUS"
    if regular_kind == "OPEN":
        # A semantic polygon cap has a closed boundary. OPEN row -> POINT is a
        # genuine triangle-fan tip, including the minimal one-triangle case.
        return "TIP"

    continuation = None
    if len(layers) >= 3:
        incoming = _unit_vector_or_none(centers[-2] - centers[-3])
        if incoming is not None:
            continuation = float(incoming.dot(outgoing))

    plane_alignment = None
    if regular_kind == "CLOSED":
        layer_normal = _closed_layer_normal(bm, layers[-2])
        if layer_normal is not None:
            plane_alignment = abs(float(layer_normal.dot(outgoing)))

    # A real collapsed tip leaves the final profile primarily along its normal
    # and normally continues the previous centerline segment. A triangulated cap
    # hub lies inside (or very near) the endpoint profile plane instead. The
    # middle band remains deliberately ambiguous rather than silently choosing.
    if plane_alignment is not None:
        if plane_alignment >= 0.55 and (continuation is None or continuation > -0.10):
            return "TIP"
        if plane_alignment <= 0.25 and (continuation is None or continuation < 0.25):
            return "CAP"
    if continuation is not None and continuation >= 0.55:
        return "TIP"
    return "AMBIGUOUS"


def _candidate_needs_semantic_cap_scan(bm, layers, centers):
    point_at_start = len(layers[0]) == 1
    point_at_end = len(layers[-1]) == 1
    if point_at_start and point_at_end:
        return True
    if not (point_at_start or point_at_end):
        return False
    try:
        regular_kind = _regular_kind_from_layers(bm, layers)
    except CenterlineError:
        return True
    oriented_layers = layers
    oriented_centers = centers
    if point_at_start:
        oriented_layers = tuple(reversed(layers))
        oriented_centers = tuple(reversed(centers))
    return (
        _collapsed_point_role(
            bm,
            oriented_layers,
            oriented_centers,
            regular_kind,
        )
        != "TIP"
    )


def _degree_endpoint_candidates(selected, adjacency):
    """Find closed/open endpoint slices on a fully capped selected surface."""

    selected = set(selected)
    endpoint_vertices = {
        index for index in selected if len(adjacency[index] & selected) == 3
    }
    candidates = []
    remaining = set(endpoint_vertices)
    while remaining:
        seed = min(remaining)
        component = set()
        queue = deque((seed,))
        while queue:
            current = queue.popleft()
            if current in component:
                continue
            component.add(current)
            queue.extend((adjacency[current] & endpoint_vertices) - component)
        remaining -= component
        try:
            kind = _classify_slice(component, adjacency, label="A capped endpoint")
        except CenterlineError:
            continue
        if kind in {"OPEN", "CLOSED"}:
            candidates.append(tuple(sorted(component)))
    return tuple(candidates)


def _infer_selected_centerline(
    context,
    *,
    selected_override=None,
    selected_edge_keys_override=None,
    active_hint_override=_ACTIVE_HINT_DEFAULT,
):
    """Parse the current complete Edit Mode selection without a captured cap."""

    obj, bm = _edit_bmesh(context)
    selected = (
        {vertex.index for vertex in bm.verts if vertex.select}
        if selected_override is None
        else set(selected_override)
    )
    if not selected:
        raise CenterlineError("No mesh elements selected.")
    if min(selected) < 0 or max(selected) >= len(bm.verts):
        raise CenterlineError("The selected recovery component is no longer valid.")

    selected_edge_keys = (
        _contained_edge_keys(bm, selected)
        if selected_edge_keys_override is None
        else tuple(
            sorted(
                {
                    tuple(sorted((int(first), int(second))))
                    for first, second in selected_edge_keys_override
                    if first in selected and second in selected and first != second
                }
            )
        )
    )
    selected_adjacency = _adjacency_from_edge_keys(selected, selected_edge_keys)
    if not _is_connected(selected, selected_adjacency):
        raise CenterlineError("Selection contains multiple disconnected pieces.")

    # A single currently selected row/loop/point is still useful feedback: it
    # previews one center point, but Confirm remains disabled until a second
    # cross-section is present.
    try:
        single_kind = _classify_slice(
            selected,
            selected_adjacency,
            label="The selection",
        )
    except CenterlineError:
        single_kind = None
    single_result = None
    if single_kind is not None:
        layer = tuple(sorted(selected))
        _validate_layer_coordinates(bm, (layer,))
        _validate_source_matrix(obj)
        center = sum(
            (bm.verts[index].co for index in layer),
            Vector((0.0, 0.0, 0.0)),
        ) / len(layer)
        if not _vector_is_finite(center):
            raise CenterlineError("The selected cross-section has a non-finite center point.")
        single_result = (obj, bm, (layer,), (center.copy(),), single_kind, False)

    root_candidates = []
    for index in sorted(selected):
        neighbors = selected_adjacency[index] & selected
        if len(neighbors) < 2:
            continue
        try:
            neighbor_kind = _classify_slice(
                neighbors,
                selected_adjacency,
                label="A point endpoint collar",
            )
        except CenterlineError:
            continue
        if neighbor_kind in {"OPEN", "CLOSED"}:
            root_candidates.append((index,))
    boundary_candidates = _boundary_slice_candidates(bm, selected, selected_adjacency)
    root_candidates.extend(boundary_candidates)
    active_element = getattr(bm.select_history, "active", None)
    if isinstance(active_element, bmesh.types.BMEdge) and getattr(
        active_element,
        "select",
        False,
    ):
        active_edge_indices = tuple(sorted(vertex.index for vertex in active_element.verts))
        if (
            all(index in selected for index in active_edge_indices)
            and active_edge_indices in set(selected_edge_keys)
        ):
            root_candidates.append(active_edge_indices)
    if not root_candidates:
        root_candidates.extend(_degree_endpoint_candidates(selected, selected_adjacency))

    validated = []
    seen_roots = set()
    point_candidate_count = sum(len(candidate) == 1 for candidate in root_candidates)
    closed_partition_validated = False
    for root_indices in root_candidates:
        root_indices = tuple(sorted(root_indices))
        if not root_indices or root_indices in seen_roots:
            continue
        seen_roots.add(root_indices)
        root_edges = tuple(sorted(_slice_edge_keys(root_indices, selected_adjacency)))
        try:
            root_kind = _classify_slice(
                root_indices,
                selected_adjacency,
                label="An inferred endpoint",
            )
            if root_kind == "CLOSED" and closed_partition_validated:
                # Distinct selected-region boundary loops are the two ends of
                # the same closed-profile tube. Reversing a validated strict
                # partition is sufficient; do not re-run every face-band test.
                continue
            if root_kind == "POINT":
                root_edges = ()
            _source_obj, _source_bm, layers, centers = _extract_layers_from_seed(
                obj,
                bm,
                selected,
                root_indices,
                root_edges,
                selected_edge_keys=selected_edge_keys,
                allow_coincident_endpoint_points=True,
            )
        except CenterlineError:
            continue
        validated.append((layers, centers, False))
        if root_kind == "CLOSED":
            closed_partition_validated = True
        if root_kind == "POINT" and point_candidate_count == 1:
            break

    needs_semantic_cap_scan = not validated or any(
        _candidate_needs_semantic_cap_scan(bm, layers, centers)
        for layers, centers, _semantic_cap_root in validated
    )
    semantic_cap_results = (
        _inferred_cap_results(obj, bm, selected)
        if needs_semantic_cap_scan
        else ()
    )
    if semantic_cap_results:
        # A face region that positively passes the complete outside-collar test
        # is stronger evidence than a coincidental graph partition through its
        # internal triangulation. Prefer that semantic cap interpretation.
        validated = list(semantic_cap_results)
    if not validated:
        if single_result is not None:
            return single_result
        raise CenterlineError("Selection is not one complete regular hair strip.")

    groups = defaultdict(list)
    for layers, centers, semantic_cap_root in validated:
        reverse_layers = tuple(reversed(layers))
        partition_key = min(layers, reverse_layers)
        groups[partition_key].append((layers, centers, semantic_cap_root))

    active_hint = (
        _active_selection_hint(bm)
        if active_hint_override is _ACTIVE_HINT_DEFAULT
        else active_hint_override
    )
    if len(groups) > 1:
        matching_groups = [
            group
            for group in groups.values()
            if any(
                _hint_matches_endpoint(active_hint, layers)
                for layers, _centers, _semantic_cap_root in group
            )
        ]
        if len(matching_groups) == 1:
            group = matching_groups[0]
        elif selected_override is not None:
            scored_groups = sorted(
                (
                    (_recovery_partition_score(bm, candidate), candidate)
                    for candidate in groups.values()
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            best_score, group = scored_groups[0]
            second_score = scored_groups[1][0]
            if (
                best_score <= EPSILON
                or best_score < second_score * 1.35
                or math.isclose(best_score, second_score, rel_tol=0.08, abs_tol=EPSILON)
            ):
                if single_result is not None:
                    return single_result
                raise CenterlineError("Direction unclear - make one end edge active.")
        else:
            if single_result is not None:
                return single_result
            raise CenterlineError("Direction unclear - make one end edge active.")
    else:
        group = next(iter(groups.values()))

    semantic_items = [item for item in group if item[2]]
    if semantic_items:
        start_matches = [
            item
            for item in semantic_items
            if _hint_endpoint_side(active_hint, item[0]) == -1
        ]
        if len(start_matches) == 1:
            layers, centers, semantic_cap_root = start_matches[0]
            direction_confirmable = len(layers) >= 2
        elif len(semantic_items) == 1:
            layers, centers, semantic_cap_root = semantic_items[0]
            direction_confirmable = len(layers) >= 2
        else:
            layers, centers, semantic_cap_root = semantic_items[0]
            direction_confirmable = False
    else:
        layers, centers, semantic_cap_root = group[0]
        layers, centers, direction_confirmable = _orient_inferred_layers(
            layers,
            centers,
            active_hint,
        )
    regular_kind = "CLOSED" if semantic_cap_root else _regular_kind_from_layers(bm, layers)
    start_point_role = (
        _collapsed_point_role(
            bm,
            tuple(reversed(layers)),
            tuple(reversed(centers)),
            regular_kind,
        )
        if len(layers[0]) == 1
        else "NONE"
    )
    end_point_role = _collapsed_point_role(bm, layers, centers, regular_kind)
    start_is_cap = start_point_role == "CAP"
    end_is_cap = end_point_role == "CAP"
    if start_is_cap and end_is_cap:
        layers = layers[1:-1]
        centers = centers[1:-1]
        direction_confirmable = direction_confirmable and len(layers) >= 2
    elif start_is_cap:
        # A root fan hub is not a first centerline layer. Once removed, its
        # adjacent regular profile already points from cap/root toward the tip.
        layers = layers[1:]
        centers = centers[1:]
        direction_confirmable = len(layers) >= 2
    elif end_is_cap:
        # The cap was at the provisional far end. Remove its hub and reverse so
        # the cap-adjacent regular profile becomes Start.
        if semantic_cap_root:
            layers = layers[:-1]
            centers = centers[:-1]
            direction_confirmable = direction_confirmable and len(layers) >= 2
        else:
            layers = tuple(reversed(layers[:-1]))
            centers = tuple(reversed(centers[:-1]))
            direction_confirmable = len(layers) >= 2
    elif "AMBIGUOUS" in {start_point_role, end_point_role}:
        direction_confirmable = False
    return obj, bm, layers, centers, regular_kind, direction_confirmable


def _recovery_partition_score(bm, group):
    """Prefer a long path carrying compact cross-sections over a transverse partition."""

    layers, centers, _semantic_cap_root = group[0]
    if len(centers) < 2:
        return 0.0
    path_length = sum(
        (second - first).length for first, second in zip(centers, centers[1:])
    )
    spans = tuple(
        _layer_diameter(bm, layer)[0]
        for layer in layers
        if len(layer) > 1
    )
    if not spans:
        return 0.0
    profile_scale = sum(spans) / len(spans)
    if profile_scale <= EPSILON:
        return 0.0
    return float(path_length / profile_scale)


def _bm_edge_key(edge):
    return tuple(sorted((edge.verts[0].index, edge.verts[1].index)))


def _vertex_components(vertex_indices, edge_keys):
    """Split a selection by its selected-edge graph, deterministically."""

    vertex_indices = set(vertex_indices)
    adjacency = _adjacency_from_edge_keys(vertex_indices, edge_keys)
    components = []
    remaining = set(vertex_indices)
    while remaining:
        seed = min(remaining)
        component = set()
        queue = deque((seed,))
        while queue:
            current = queue.popleft()
            if current in component:
                continue
            component.add(current)
            queue.extend(sorted(adjacency[current] - component))
        remaining -= component
        components.append(frozenset(component))
    return tuple(components)


def _ordered_simple_edge_path(vertex_indices, edge_keys):
    """Return an open selected edge path, or an empty tuple for any other graph."""

    vertex_indices = set(vertex_indices)
    edge_keys = tuple(edge_keys)
    if len(vertex_indices) < 2 or len(edge_keys) != len(vertex_indices) - 1:
        return ()
    adjacency = _adjacency_from_edge_keys(vertex_indices, edge_keys)
    endpoints = sorted(index for index in vertex_indices if len(adjacency[index]) == 1)
    if len(endpoints) != 2 or any(len(adjacency[index]) not in {1, 2} for index in vertex_indices):
        return ()
    ordered = [endpoints[0]]
    previous = None
    current = endpoints[0]
    while current != endpoints[1]:
        choices = sorted(adjacency[current] - ({previous} if previous is not None else set()))
        if len(choices) != 1:
            return ()
        following = choices[0]
        if following in ordered:
            return ()
        ordered.append(following)
        previous, current = current, following
    return tuple(ordered) if len(ordered) == len(vertex_indices) else ()


def _quad_band_from_rail_edge(bm, edge_key):
    """Expand one longitudinal guide edge across its complete quad band."""

    edge_by_key = {_bm_edge_key(edge): edge for edge in bm.edges}
    start_edge = edge_by_key.get(tuple(sorted(edge_key)))
    if start_edge is None:
        raise CenterlineError("A selected guide edge no longer exists.")

    rail_edges = {start_edge}
    band_faces = set()
    queue = deque((start_edge,))
    while queue:
        rail_edge = queue.popleft()
        linked_faces = tuple(face for face in rail_edge.link_faces if len(face.verts) == 4)
        if not linked_faces or len(linked_faces) > 2 or len(linked_faces) != len(rail_edge.link_faces):
            raise CenterlineError(
                "A guide edge must run through one clean, non-branching quad strip."
            )
        for face in linked_faces:
            opposite = tuple(
                candidate
                for candidate in face.edges
                if not set(candidate.verts) & set(rail_edge.verts)
            )
            if len(opposite) != 1:
                raise CenterlineError("A guide edge crosses a malformed quad band.")
            opposite_edge = opposite[0]
            band_faces.add(face)
            if opposite_edge not in rail_edges:
                rail_edges.add(opposite_edge)
                queue.append(opposite_edge)

    band_vertices = {vertex.index for face in band_faces for vertex in face.verts}
    rail_edge_keys = {_bm_edge_key(edge) for edge in rail_edges}
    face_edge_keys = {_bm_edge_key(edge) for face in band_faces for edge in face.edges}
    cross_edge_keys = tuple(sorted(face_edge_keys - rail_edge_keys))
    cross_components = _vertex_components(band_vertices, cross_edge_keys)
    if len(cross_components) != 2:
        raise CenterlineError("The selected guide edge does not define two clean cross-sections.")

    first_index, second_index = tuple(vertex.index for vertex in start_edge.verts)
    first_matches = [component for component in cross_components if first_index in component]
    second_matches = [component for component in cross_components if second_index in component]
    if (
        len(first_matches) != 1
        or len(second_matches) != 1
        or first_matches[0] is second_matches[0]
    ):
        raise CenterlineError("The selected guide edge has an ambiguous quad band.")
    first_layer = tuple(sorted(first_matches[0]))
    second_layer = tuple(sorted(second_matches[0]))
    adjacency = _adjacency_from_edge_keys(band_vertices, cross_edge_keys)
    first_kind = _classify_slice(first_layer, adjacency, label="A guide cross-section")
    second_kind = _classify_slice(second_layer, adjacency, label="A guide cross-section")
    if first_kind != second_kind or first_kind not in {"OPEN", "CLOSED"}:
        raise CenterlineError("A guide edge changes cross-section type along the strand.")
    return first_layer, second_layer, tuple(sorted(face_edge_keys))


def _infer_guide_edge_centerline(context, obj, bm, component, edge_keys, active_hint):
    """Turn one selected longitudinal edge path into the usual layer contract."""

    path = _ordered_simple_edge_path(component, edge_keys)
    if not path:
        raise CenterlineError("Select complete hair bands, loops, or one simple guide edge path.")

    layers = []
    expanded_edge_keys = set()
    for first, second in zip(path, path[1:]):
        first_layer, second_layer, band_edge_keys = _quad_band_from_rail_edge(
            bm,
            (first, second),
        )
        if first not in first_layer:
            first_layer, second_layer = second_layer, first_layer
        if first not in first_layer or second not in second_layer:
            raise CenterlineError("The selected edge path turns across a cross-section.")
        if layers and tuple(layers[-1]) != tuple(first_layer):
            raise CenterlineError("The selected guide path changes rail inside the strand.")
        if not layers:
            layers.append(first_layer)
        layers.append(second_layer)
        expanded_edge_keys.update(band_edge_keys)

    expanded_vertices = {index for layer in layers for index in layer}
    root_edges = tuple(
        edge_key
        for edge_key in expanded_edge_keys
        if edge_key[0] in set(layers[0]) and edge_key[1] in set(layers[0])
    )
    _source, _source_bm, parsed_layers, centers = _extract_layers_from_seed(
        obj,
        bm,
        expanded_vertices,
        layers[0],
        root_edges,
        selected_edge_keys=tuple(sorted(expanded_edge_keys)),
        allow_coincident_endpoint_points=True,
    )
    regular_kind = _regular_kind_from_layers(bm, parsed_layers)
    parsed_layers, centers, direction_confirmable = _orient_inferred_layers(
        parsed_layers,
        centers,
        active_hint,
    )
    return {
        "layers": tuple(parsed_layers),
        "centers": tuple(centers),
        "regular_kind": regular_kind,
        "direction_confirmable": direction_confirmable,
        "selected_edge_keys": tuple(sorted(expanded_edge_keys)),
        "input_mode": "GUIDE_EDGES",
    }


def _infer_selected_recovery_components(context):
    """Preflight every selected component without changing Edit Mode selection."""

    obj, bm = _edit_bmesh(context)
    selected = {vertex.index for vertex in bm.verts if vertex.select}
    if not selected:
        raise CenterlineError("Select applied hair faces, loops, or one longitudinal guide path.")
    selected_edge_keys = _selected_edge_keys(bm, selected)
    if not selected_edge_keys:
        selected_edge_keys = _contained_edge_keys(bm, selected)
    components = _vertex_components(selected, selected_edge_keys)
    active_hint = _active_selection_hint(bm)
    records = []
    for component in components:
        component_edge_keys = tuple(
            edge_key
            for edge_key in selected_edge_keys
            if edge_key[0] in component and edge_key[1] in component
        )
        component_hint = active_hint
        if active_hint is not None and not active_hint[1].issubset(component):
            component_hint = None
        direct_error = None
        try:
            (
                _source,
                _source_bm,
                layers,
                centers,
                regular_kind,
                direction_confirmable,
            ) = _infer_selected_centerline(
                context,
                selected_override=component,
                selected_edge_keys_override=component_edge_keys,
                active_hint_override=component_hint,
            )
            if len(layers) >= 2:
                records.append(
                    {
                        "layers": tuple(layers),
                        "centers": tuple(centers),
                        "regular_kind": regular_kind,
                        "direction_confirmable": direction_confirmable,
                        "selected_edge_keys": tuple(component_edge_keys),
                        "input_mode": "FULL_BAND",
                    }
                )
                continue
            direct_error = CenterlineError("A recovery component needs at least two cross-sections.")
        except CenterlineError as exc:
            direct_error = exc

        try:
            records.append(
                _infer_guide_edge_centerline(
                    context,
                    obj,
                    bm,
                    component,
                    component_edge_keys,
                    component_hint,
                )
            )
        except CenterlineError:
            raise direct_error

    if not records:
        raise CenterlineError("No recoverable applied hair was found in the selection.")
    return obj, bm, tuple(records)


def _is_finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _vector_is_finite(value):
    try:
        return len(value) == 3 and all(_is_finite_number(component) for component in value)
    except TypeError:
        return False


def _validate_layer_coordinates(bm, layers):
    for layer_index, layer in enumerate(layers):
        for vertex_index in layer:
            if not _vector_is_finite(bm.verts[vertex_index].co):
                raise CenterlineError(
                    f"Layer {layer_index + 1} vertex {vertex_index} has non-finite coordinates."
                )


def _validate_source_matrix(source_obj):
    for row in range(4):
        for column in range(4):
            if not _is_finite_number(source_obj.matrix_world[row][column]):
                raise CenterlineError(
                    "The source object transform has a non-finite value at "
                    f"row {row + 1}, column {column + 1}."
                )


def _unit_vector_or_none(value):
    value = Vector(value)
    if value.length <= EPSILON:
        return None
    return value.normalized()


def _project_to_normal_plane(value, normal):
    value = Vector(value)
    normal = Vector(normal)
    return _unit_vector_or_none(value - normal * value.dot(normal))


def _vector_json(value):
    return [float(value.x), float(value.y), float(value.z)] if value is not None else None


def _layer_diameter(bm, layer):
    """Return the exact, deterministic maximum vertex chord for one layer."""

    if len(layer) == 1:
        return 0.0, None, None

    best_distance_squared = -1.0
    best_pair = None
    best_vector = None
    ordered = tuple(sorted(layer))
    for position, first in enumerate(ordered[:-1]):
        for second in ordered[position + 1 :]:
            chord = bm.verts[second].co - bm.verts[first].co
            distance_squared = chord.length_squared
            # Iteration is lexicographic, so an exact tie deliberately keeps
            # the first pair and produces the same result on every build.
            if distance_squared > best_distance_squared:
                best_distance_squared = distance_squared
                best_pair = (first, second)
                best_vector = chord.copy()
    return best_distance_squared ** 0.5, best_pair, best_vector


def _layer_profile_span(bm, layer, tangent):
    """Return the maximum vertex chord after projection normal to the tangent."""

    if len(layer) == 1:
        return 0.0, None, None

    best_distance_squared = -1.0
    best_pair = None
    best_vector = None
    ordered = tuple(sorted(layer))
    for position, first in enumerate(ordered[:-1]):
        for second in ordered[position + 1 :]:
            chord = bm.verts[second].co - bm.verts[first].co
            projected = chord - tangent * chord.dot(tangent)
            distance_squared = projected.length_squared
            if distance_squared > best_distance_squared:
                best_distance_squared = distance_squared
                best_pair = (first, second)
                best_vector = projected.copy()
    return best_distance_squared ** 0.5, best_pair, best_vector


def _layer_boundary_edge_span(bm, layer, tangent):
    """Return the longest real cross-section edge projected normal to the path.

    A closed four-vertex hair section usually has a longer front/back edge and
    a much shorter thickness edge.  The previous maximum-chord measurement can
    select a diagonal, which is lossless metadata but is not the artist's hair
    width.  This helper preserves the actual widest boundary edge instead.
    """

    if len(layer) == 1:
        return 0.0, None, None

    layer_set = set(layer)
    edge_keys = {
        tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        for vertex_index in layer
        for edge in bm.verts[vertex_index].link_edges
        if edge.verts[0].index in layer_set and edge.verts[1].index in layer_set
    }
    best_distance_squared = -1.0
    best_pair = None
    best_vector = None
    for first, second in sorted(edge_keys):
        edge_vector = bm.verts[second].co - bm.verts[first].co
        projected = edge_vector - tangent * edge_vector.dot(tangent)
        distance_squared = projected.length_squared
        if distance_squared > best_distance_squared:
            best_distance_squared = distance_squared
            best_pair = (first, second)
            best_vector = projected.copy()
    if best_pair is None:
        return 0.0, None, None
    return best_distance_squared ** 0.5, best_pair, best_vector


def _face_same_layer_edge(face, layer_set):
    matches = []
    for edge in face.edges:
        first = edge.verts[0].index
        second = edge.verts[1].index
        if first in layer_set and second in layer_set:
            matches.append((tuple(sorted((first, second))), edge.calc_length()))
    return matches[0] if len(matches) == 1 else None


def _is_closed_profile_layer(bm, layer):
    if len(layer) < 3:
        return False
    edge_keys = _contained_edge_keys(bm, layer)
    if len(edge_keys) != len(layer):
        return False
    degree = defaultdict(int)
    for first, second in edge_keys:
        degree[first] += 1
        degree[second] += 1
    return all(degree[index] == 2 for index in layer)


def _front_wide_band_pair(bm, first_layer, second_layer):
    """Return the two dominant opposite face ribbons for one closed band.

    A four-point rectangle and an eight-point rounded rectangle both expose two
    longitudinal faces whose same-layer edges are clearly wider than the rest.
    A regular octagon does not, so the separation test deliberately rejects it.
    Point endpoints are allowed because the adjacent regular loop still carries
    the identifying edge.
    """

    sizes = {len(first_layer), len(second_layer)}
    regular_sizes = {size for size in sizes if size > 1}
    if len(regular_sizes) != 1:
        return None
    regular_size = next(iter(regular_sizes))
    if regular_size < 4 or any(size not in {1, regular_size} for size in sizes):
        return None
    if any(
        len(layer) > 1 and not _is_closed_profile_layer(bm, layer)
        for layer in (first_layer, second_layer)
    ):
        return None

    first_set = set(first_layer)
    second_set = set(second_layer)
    combined = first_set | second_set
    candidate_faces = {
        face
        for vertex_index in combined
        for face in bm.verts[vertex_index].link_faces
        if {vertex.index for vertex in face.verts}.issubset(combined)
        and any(vertex.index in first_set for vertex in face.verts)
        and any(vertex.index in second_set for vertex in face.verts)
    }
    descriptors = []
    for face in candidate_faces:
        first_edge = _face_same_layer_edge(face, first_set) if len(first_set) > 1 else None
        second_edge = _face_same_layer_edge(face, second_set) if len(second_set) > 1 else None
        lengths = [entry[1] for entry in (first_edge, second_edge) if entry is not None]
        if not lengths:
            continue
        face.normal_update()
        area = face.calc_area()
        if face.normal.length <= EPSILON or area <= EPSILON:
            continue
        descriptors.append(
            {
                "face": face,
                "first_edge": first_edge,
                "second_edge": second_edge,
                "width_score": sum(lengths) / len(lengths),
                "area": area,
                "center": face.calc_center_median().copy(),
            }
        )
    descriptors.sort(key=lambda item: (-item["width_score"], item["face"].index))
    if len(descriptors) < 2:
        return None
    if len(descriptors) > 2 and (
        descriptors[1]["width_score"]
        < descriptors[2]["width_score"] * HAIR_PROFILE_WIDE_FACE_SEPARATION
    ):
        return None

    first, second = descriptors[:2]
    if (first["center"] - second["center"]).length <= EPSILON:
        return None
    return first, second


def _descriptor_edge_key(descriptor, edge_name):
    edge = descriptor.get(edge_name)
    return edge[0] if edge is not None else None


def _band_curve_outward(centers, band_index):
    segment_tangents = tuple(
        _unit_vector_or_none(second - first)
        for first, second in zip(centers, centers[1:])
    )
    if len(segment_tangents) < 2 or any(value is None for value in segment_tangents):
        return None
    if band_index == 0:
        outward = segment_tangents[0] - segment_tangents[1]
    elif band_index == len(segment_tangents) - 1:
        outward = segment_tangents[-2] - segment_tangents[-1]
    else:
        outward = segment_tangents[band_index - 1] - segment_tangents[band_index + 1]
    tangent = segment_tangents[band_index]
    outward = outward - tangent * outward.dot(tangent)
    if outward.length < HAIR_FRONT_CURVATURE_MIN:
        return None
    return outward


def _ribbon_front_choice(first_ribbon, second_ribbon, band_indices, centers):
    first_area = sum(descriptor["area"] for descriptor in first_ribbon)
    second_area = sum(descriptor["area"] for descriptor in second_ribbon)
    area_scale = max(first_area, second_area, EPSILON)
    relative_area_difference = abs(first_area - second_area) / area_scale
    area_choice = None
    if relative_area_difference >= HAIR_FRONT_RIBBON_AREA_RELATIVE_MIN:
        area_choice = 0 if first_area > second_area else 1

    curvature_score = 0.0
    curvature_weight = 0.0
    for first, second, band_index in zip(first_ribbon, second_ribbon, band_indices):
        outward = _band_curve_outward(centers, band_index)
        segment_tangent = _unit_vector_or_none(
            centers[band_index + 1] - centers[band_index]
        )
        if outward is None or segment_tangent is None:
            continue
        separation = _project_to_normal_plane(
            first["center"] - second["center"],
            segment_tangent,
        )
        if separation is None:
            continue
        weight = outward.length
        curvature_score += separation.normalized().dot(outward.normalized()) * weight
        curvature_weight += weight
    curvature_choice = None
    if curvature_weight > HAIR_FRONT_CURVATURE_MIN:
        confidence = curvature_score / curvature_weight
        if abs(confidence) >= HAIR_FRONT_CURVATURE_CONFIDENCE:
            curvature_choice = 0 if confidence > 0.0 else 1

    # A curved strip's outside ribbon is normally both longer/larger and opposite
    # the mathematical curvature vector. Use either strong geometric signal, but
    # refuse a conflict and fall through to the stable radial fallback.
    if area_choice is not None and curvature_choice is not None:
        if area_choice == curvature_choice:
            return area_choice
    elif curvature_choice is not None:
        return curvature_choice
    elif area_choice is not None:
        return area_choice

    radial_score = sum(
        (first["center"] - second["center"]).dot(
            (first["center"] + second["center"]) * 0.5
        )
        for first, second in zip(first_ribbon, second_ribbon)
    )
    radial_scale = sum(
        max(1.0, first["center"].length_squared, second["center"].length_squared)
        for first, second in zip(first_ribbon, second_ribbon)
    )
    if abs(radial_score) <= EPSILON * max(1.0, radial_scale):
        return None
    return 0 if radial_score > 0.0 else 1


def _front_wide_band_face(bm, first_layer, second_layer):
    """Compatibility helper for one band, using the final radial fallback."""

    pair = _front_wide_band_pair(bm, first_layer, second_layer)
    if pair is None:
        return None
    first, second = pair
    separation = first["center"] - second["center"]
    pair_midpoint = (first["center"] + second["center"]) * 0.5
    radial_difference = separation.dot(pair_midpoint)
    radial_scale = max(
        1.0,
        first["center"].length_squared,
        second["center"].length_squared,
    )
    if abs(radial_difference) <= EPSILON * radial_scale:
        return None
    front, back = (first, second) if radial_difference > 0.0 else (second, first)
    front["front_normal"] = (front["center"] - back["center"]).normalized()
    return front


def _front_band_descriptors(bm, layers, centers=None):
    """Choose one continuous outward broad-face ribbon for the selected strip."""

    if centers is None:
        centers = tuple(
            sum((bm.verts[index].co for index in layer), Vector()) / len(layer)
            for layer in layers
        )
    else:
        centers = tuple(Vector(value) for value in centers)
    pairs = tuple(
        _front_wide_band_pair(bm, first_layer, second_layer)
        for first_layer, second_layer in zip(layers, layers[1:])
    )
    results = [None] * len(pairs)
    band_index = 0
    while band_index < len(pairs):
        pair = pairs[band_index]
        if pair is None:
            band_index += 1
            continue

        run_indices = [band_index]
        first_ribbon = [pair[0]]
        second_ribbon = [pair[1]]
        next_index = band_index + 1
        while next_index < len(pairs) and pairs[next_index] is not None:
            next_first, next_second = pairs[next_index]
            first_end = _descriptor_edge_key(first_ribbon[-1], "second_edge")
            second_end = _descriptor_edge_key(second_ribbon[-1], "second_edge")
            direct = (
                first_end is not None
                and second_end is not None
                and first_end == _descriptor_edge_key(next_first, "first_edge")
                and second_end == _descriptor_edge_key(next_second, "first_edge")
            )
            crossed = (
                first_end is not None
                and second_end is not None
                and first_end == _descriptor_edge_key(next_second, "first_edge")
                and second_end == _descriptor_edge_key(next_first, "first_edge")
            )
            if not direct and not crossed:
                break
            if crossed:
                next_first, next_second = next_second, next_first
            first_ribbon.append(next_first)
            second_ribbon.append(next_second)
            run_indices.append(next_index)
            next_index += 1

        choice = _ribbon_front_choice(
            tuple(first_ribbon),
            tuple(second_ribbon),
            tuple(run_indices),
            centers,
        )
        if choice is not None:
            chosen = first_ribbon if choice == 0 else second_ribbon
            opposite = second_ribbon if choice == 0 else first_ribbon
            for current_index, front, back in zip(run_indices, chosen, opposite):
                front["front_normal"] = (front["center"] - back["center"]).normalized()
                results[current_index] = front
        band_index = next_index
    return tuple(results)


def _center_tangents(centers):
    if len(centers) < 2:
        raise CenterlineError("A center curve needs at least two points.")

    tangents = []
    final_index = len(centers) - 1
    for index, center in enumerate(centers):
        if index == 0:
            tangent = _unit_vector_or_none(centers[1] - center)
        elif index == final_index:
            tangent = _unit_vector_or_none(center - centers[index - 1])
        else:
            incoming = _unit_vector_or_none(center - centers[index - 1])
            outgoing = _unit_vector_or_none(centers[index + 1] - center)
            tangent = _unit_vector_or_none(incoming + outgoing)
            if tangent is None:
                # A perfect 180-degree reversal has no bisector. The outgoing
                # segment is deterministic and keeps the frame well defined.
                tangent = outgoing
        if tangent is None:
            raise CenterlineError(f"Center point {index + 1} has no usable tangent.")
        tangents.append(tangent)
    return tuple(tangents)


def _deterministic_perpendicular(tangent):
    basis = min(
        (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))),
        key=lambda axis: abs(axis.dot(tangent)),
    )
    result = _project_to_normal_plane(basis, tangent)
    if result is None:
        raise CenterlineError("Could not construct a stable orientation frame.")
    return result


def _transport_axis(axis, previous_tangent, tangent):
    try:
        transported = previous_tangent.rotation_difference(tangent) @ axis
    except ValueError:
        transported = axis
    transported = _project_to_normal_plane(transported, tangent)
    return transported if transported is not None else _deterministic_perpendicular(tangent)


def _track_layer_anchors(bm, layers, profile_span_pairs):
    neighbors = defaultdict(set)
    relevant = {index for layer in layers for index in layer}
    for vertex_index in relevant:
        for edge in bm.verts[vertex_index].link_edges:
            first = edge.verts[0].index
            second = edge.verts[1].index
            if first in relevant and second in relevant:
                neighbors[first].add(second)
                neighbors[second].add(first)

    anchors = []
    for layer_index, (layer, profile_span_pair) in enumerate(
        zip(layers, profile_span_pairs)
    ):
        if len(layer) == 1:
            anchors.append(None)
            continue
        if layer_index == 0 or anchors[-1] is None:
            anchors.append(profile_span_pair[0] if profile_span_pair else min(layer))
            continue

        candidates = sorted(neighbors[anchors[-1]] & set(layer))
        # Regular-band validation guarantees one longitudinal match. Keep a
        # deterministic fallback so metadata construction never invents a
        # random orientation if malformed data somehow reaches this helper.
        anchors.append(
            candidates[0]
            if len(candidates) == 1
            else (profile_span_pair[0] if profile_span_pair else min(layer))
        )
    return tuple(anchors)


def _layer_band_normals(bm, layers):
    """Area-weight adjacent selected-band normals into one local vector/layer."""

    layer_by_vertex = {
        vertex_index: layer_index
        for layer_index, layer in enumerate(layers)
        for vertex_index in layer
    }
    weighted_normals = [Vector((0.0, 0.0, 0.0)) for _layer in layers]
    total_areas = [0.0 for _layer in layers]
    face_counts = [0 for _layer in layers]

    candidate_faces = {
        face
        for vertex_index in layer_by_vertex
        for face in bm.verts[vertex_index].link_faces
    }
    for face in sorted(candidate_faces, key=lambda value: value.index):
        indices = tuple(vertex.index for vertex in face.verts)
        if any(index not in layer_by_vertex for index in indices):
            continue
        face_layers = {layer_by_vertex[index] for index in indices}
        if len(face_layers) != 2 or max(face_layers) - min(face_layers) != 1:
            # Same-layer cap faces do not describe profile roll.
            continue
        face.normal_update()
        area = face.calc_area()
        if area <= 0.0 or face.normal.length <= EPSILON:
            continue
        weighted = face.normal * area
        for layer_index in face_layers:
            weighted_normals[layer_index] += weighted
            total_areas[layer_index] += area
            face_counts[layer_index] += 1

    results = []
    for weighted, total_area, face_count in zip(weighted_normals, total_areas, face_counts):
        # A closed tube's radial side normals cancel. Relative cancellation is
        # intentional and signals that the tracked profile-span anchor must carry
        # roll instead of an unstable near-zero average.
        meaningful = total_area > 0.0 and weighted.length > total_area * 1.0e-6
        results.append(
            (
                weighted.normalized() if meaningful else None,
                face_count,
            )
        )
    return tuple(results)


def _build_cross_section_metadata(source_obj, bm, layers, centers, regular_kind):
    """Capture lossless sizing and a stable local frame without shaping the Curve."""

    _validate_source_matrix(source_obj)
    diameters = tuple(_layer_diameter(bm, layer) for layer in layers)
    widths = tuple(value[0] for value in diameters)
    diameter_pairs = tuple(value[1] for value in diameters)
    diameter_vectors = tuple(value[2] for value in diameters)
    tangents = _center_tangents(centers)
    profile_spans = tuple(
        _layer_profile_span(bm, layer, tangent)
        for layer, tangent in zip(layers, tangents)
    )
    profile_span_values = tuple(value[0] for value in profile_spans)
    profile_span_pairs = tuple(value[1] for value in profile_spans)
    profile_span_vectors = tuple(value[2] for value in profile_spans)
    boundary_spans = tuple(
        _layer_boundary_edge_span(bm, layer, tangent)
        for layer, tangent in zip(layers, tangents)
    )
    boundary_span_values = tuple(value[0] for value in boundary_spans)
    boundary_span_pairs = tuple(value[1] for value in boundary_spans)
    boundary_span_vectors = tuple(value[2] for value in boundary_spans)
    anchors = _track_layer_anchors(bm, layers, profile_span_pairs)
    band_normals = _layer_band_normals(bm, layers)
    front_bands = _front_band_descriptors(bm, layers, centers)

    sections = []
    previous_axis = None
    previous_tangent = None
    previous_shape_normal = None
    for layer_index, layer in enumerate(layers):
        tangent = tangents[layer_index]
        diameter_pair = diameter_pairs[layer_index]
        diameter_vector = diameter_vectors[layer_index]
        profile_span_pair = profile_span_pairs[layer_index]
        profile_span_vector = profile_span_vectors[layer_index]
        boundary_span_pair = boundary_span_pairs[layer_index]
        boundary_span_vector = boundary_span_vectors[layer_index]
        anchor = anchors[layer_index]
        mesh_normal, band_face_count = band_normals[layer_index]
        mesh_roll_normal = (
            _project_to_normal_plane(mesh_normal, tangent)
            if mesh_normal is not None
            else None
        )

        if len(layer) == 1:
            if layer_index == 0:
                axis = _deterministic_perpendicular(tangent)
                axis_source = "DETERMINISTIC"
                orientation_source = "DETERMINISTIC_ROOT"
            elif previous_axis is None or previous_tangent is None:
                axis = _deterministic_perpendicular(tangent)
                axis_source = "DETERMINISTIC"
                orientation_source = "TRANSPORTED_TIP"
            else:
                axis = _transport_axis(previous_axis, previous_tangent, tangent)
                axis_source = "TRANSPORTED"
                orientation_source = "TRANSPORTED_TIP"
            frame_normal = _unit_vector_or_none(tangent.cross(axis))
            kind = "POINT"
        else:
            axis = _project_to_normal_plane(profile_span_vector, tangent)
            axis_source = "PROFILE_SPAN"

            anchor_axis = (
                _project_to_normal_plane(bm.verts[anchor].co - centers[layer_index], tangent)
                if anchor is not None
                else None
            )
            if axis is None:
                axis = anchor_axis
                axis_source = "ANCHOR"
            if axis is None:
                axis = _deterministic_perpendicular(tangent)
                axis_source = "DETERMINISTIC"

            # Give the otherwise unoriented profile span a stable sign, first from
            # its tracked mesh column and then from the transported prior frame.
            if anchor_axis is not None and axis.dot(anchor_axis) < 0.0:
                axis.negate()
            if previous_axis is not None and previous_tangent is not None:
                transported = _transport_axis(previous_axis, previous_tangent, tangent)
                if axis.dot(transported) < 0.0:
                    axis.negate()

            frame_normal = _unit_vector_or_none(tangent.cross(axis))
            if frame_normal is None:
                axis = _deterministic_perpendicular(tangent)
                frame_normal = _unit_vector_or_none(tangent.cross(axis))
                axis_source = "DETERMINISTIC"

            if mesh_roll_normal is not None:
                # Preserve the source face winding when it supplies a useful
                # roll direction. The raw area-weighted normal is stored too.
                if frame_normal.dot(mesh_roll_normal) < 0.0:
                    axis.negate()
                    frame_normal.negate()
                orientation_source = "MESH_NORMAL"
            else:
                orientation_source = "PROFILE_ANCHOR"
            kind = regular_kind

        adjacent_front_bands = []
        if layer_index > 0 and front_bands[layer_index - 1] is not None:
            adjacent_front_bands.append(front_bands[layer_index - 1])
        if layer_index < len(front_bands) and front_bands[layer_index] is not None:
            adjacent_front_bands.append(front_bands[layer_index])
        front_normals = []
        for descriptor in adjacent_front_bands:
            projected = _project_to_normal_plane(descriptor["front_normal"], tangent)
            if projected is not None:
                if front_normals and projected.dot(front_normals[0]) < 0.0:
                    projected.negate()
                front_normals.append(projected)
        shape_normal = (
            _unit_vector_or_none(sum(front_normals, Vector((0.0, 0.0, 0.0))))
            if front_normals
            else None
        )
        if shape_normal is not None:
            shape_source = "FRONT_WIDE_FACE"
        else:
            shape_normal = frame_normal.copy()
            shape_source = "FRAME_NORMAL"
            if previous_shape_normal is not None and previous_tangent is not None:
                transported_shape = _transport_axis(
                    previous_shape_normal,
                    previous_tangent,
                    tangent,
                )
                if shape_normal.dot(transported_shape) < 0.0:
                    shape_normal.negate()
        shape_width_axis = _unit_vector_or_none(shape_normal.cross(tangent))
        if shape_width_axis is None:
            shape_width_axis = axis.copy()
            shape_source = "FRAME_NORMAL"
        # A four-sided closed strand uses its widest real boundary edge so the
        # bevel matches the authored front/back face rather than a diagonal.
        # For any other closed polygon, one boundary segment is only a fraction
        # of the complete profile, so retain the full tangent-plane span.
        uses_four_side_boundary_width = (
            regular_kind == "CLOSED"
            and len(layer) == 4
            and boundary_span_pairs[layer_index] is not None
        )
        shape_span = (
            0.0
            if len(layer) == 1
            else (
                boundary_span_values[layer_index]
                if uses_four_side_boundary_width
                else profile_span_values[layer_index]
            )
        )

        front_edge_midpoints = []
        if layer_index > 0:
            descriptor = front_bands[layer_index - 1]
            edge_descriptor = descriptor.get("second_edge") if descriptor else None
            if edge_descriptor is not None:
                first, second = edge_descriptor[0]
                front_edge_midpoints.append(
                    (bm.verts[first].co + bm.verts[second].co) * 0.5
                )
        if layer_index < len(front_bands):
            descriptor = front_bands[layer_index]
            edge_descriptor = descriptor.get("first_edge") if descriptor else None
            if edge_descriptor is not None:
                first, second = edge_descriptor[0]
                front_edge_midpoints.append(
                    (bm.verts[first].co + bm.verts[second].co) * 0.5
                )

        if len(layer) == 1:
            front_target = centers[layer_index].copy()
            front_target_source = "POINT_CENTER"
        elif front_edge_midpoints:
            front_target = sum(
                front_edge_midpoints,
                Vector((0.0, 0.0, 0.0)),
            ) / len(front_edge_midpoints)
            front_target_source = "FRONT_WIDE_FACE_EDGE"
        else:
            # When front/back cannot be proven topologically, keep the target
            # on the outermost source plane along the stable shape normal. This
            # never invents lateral motion and remains deterministic.
            maximum_projection = max(
                (bm.verts[index].co - centers[layer_index]).dot(shape_normal)
                for index in layer
            )
            front_target = centers[layer_index] + shape_normal * maximum_projection
            front_target_source = "MAX_NORMAL_PROJECTION"

        sections.append(
            {
                "kind": kind,
                "center_local": _vector_json(centers[layer_index]),
                "front_target_local": _vector_json(front_target),
                "front_target_source": front_target_source,
                "max_width_local": float(widths[layer_index]),
                "diameter_pair": list(diameter_pair) if diameter_pair is not None else None,
                "diameter_vector_local": _vector_json(diameter_vector),
                "max_profile_span_local": float(profile_span_values[layer_index]),
                "profile_span_pair": (
                    list(profile_span_pair) if profile_span_pair is not None else None
                ),
                "profile_span_vector_local": _vector_json(profile_span_vector),
                "boundary_edge_span_local": float(boundary_span_values[layer_index]),
                "boundary_edge_pair": (
                    list(boundary_span_pair) if boundary_span_pair is not None else None
                ),
                "boundary_edge_vector_local": _vector_json(boundary_span_vector),
                "anchor_vertex": int(anchor) if anchor is not None else None,
                "tangent_local": _vector_json(tangent),
                "width_axis_local": _vector_json(axis),
                "normal_local": _vector_json(frame_normal),
                "mesh_normal_local": _vector_json(mesh_normal),
                "band_face_count": int(band_face_count),
                "width_axis_source": axis_source,
                "orientation_source": orientation_source,
                "shape_span_local": float(shape_span),
                "shape_width_axis_local": _vector_json(shape_width_axis),
                "shape_normal_local": _vector_json(shape_normal),
                "shape_source": shape_source,
                "front_face_indices": [
                    int(descriptor["face"].index) for descriptor in adjacent_front_bands
                ],
            }
        )
        previous_axis = axis.copy()
        previous_tangent = tangent.copy()
        previous_shape_normal = shape_normal.copy()

    return {
        "version": METADATA_VERSION,
        "space": METADATA_SPACE,
        "point_count": len(centers),
        "profile_tip_radius": HAIR_PROFILE_TIP_RADIUS,
        "matrix_world_at_build": [
            float(source_obj.matrix_world[row][column])
            for row in range(4)
            for column in range(4)
        ],
        "sections": sections,
    }


def _reject_json_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def _metadata_pair_is_valid(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(index, int) and not isinstance(index, bool) and index >= 0 for index in value)
        and value[0] != value[1]
    )


def _metadata_vector_is_valid(value, *, allow_none=False, require_nonzero=False):
    if value is None:
        return allow_none
    if not isinstance(value, list) or not _vector_is_finite(value):
        return False
    if require_nonzero and not any(abs(float(component)) > EPSILON for component in value):
        return False
    return True


def _metadata_section_is_valid(section, *, is_first, is_last, metadata_version=METADATA_VERSION):
    if not isinstance(section, dict):
        return False

    kind = section.get("kind")
    if kind not in {"OPEN", "CLOSED", "POINT"} or (
        kind == "POINT" and not (is_first or is_last)
    ):
        return False
    for key in ("max_width_local", "max_profile_span_local"):
        value = section.get(key)
        if not _is_finite_number(value) or float(value) < 0.0:
            return False

    is_point = kind == "POINT"
    diameter_pair = section.get("diameter_pair")
    profile_span_pair = section.get("profile_span_pair")
    if is_point:
        if any(float(section[key]) != 0.0 for key in ("max_width_local", "max_profile_span_local")):
            return False
        expected_orientation = "DETERMINISTIC_ROOT" if is_first else "TRANSPORTED_TIP"
        if section.get("orientation_source") != expected_orientation:
            return False
        if diameter_pair is not None or profile_span_pair is not None:
            return False
        if section.get("diameter_vector_local") is not None or (
            section.get("profile_span_vector_local") is not None
        ):
            return False
        if section.get("anchor_vertex") is not None:
            return False
    else:
        if not _metadata_pair_is_valid(diameter_pair) or not _metadata_pair_is_valid(
            profile_span_pair
        ):
            return False
        if not _metadata_vector_is_valid(section.get("diameter_vector_local")) or not (
            _metadata_vector_is_valid(section.get("profile_span_vector_local"))
        ):
            return False
        anchor = section.get("anchor_vertex")
        if not isinstance(anchor, int) or isinstance(anchor, bool) or anchor < 0:
            return False

    for key in ("tangent_local", "width_axis_local", "normal_local"):
        if not _metadata_vector_is_valid(
            section.get(key),
            require_nonzero=True,
        ):
            return False
    if not _metadata_vector_is_valid(
        section.get("mesh_normal_local"),
        allow_none=True,
        require_nonzero=True,
    ):
        return False

    face_count = section.get("band_face_count")
    if not isinstance(face_count, int) or isinstance(face_count, bool) or face_count < 0:
        return False
    if section.get("width_axis_source") not in {
        "PROFILE_SPAN",
        "ANCHOR",
        "DETERMINISTIC",
        "TRANSPORTED",
    }:
        return False
    if section.get("orientation_source") not in {
        "MESH_NORMAL",
        "PROFILE_ANCHOR",
        "DETERMINISTIC_ROOT",
        "TRANSPORTED_TIP",
    }:
        return False
    if metadata_version >= 3:
        for key in ("boundary_edge_span_local", "shape_span_local"):
            value = section.get(key)
            if not _is_finite_number(value) or float(value) < 0.0:
                return False
        boundary_pair = section.get("boundary_edge_pair")
        boundary_vector = section.get("boundary_edge_vector_local")
        if is_point:
            if float(section["boundary_edge_span_local"]) != 0.0:
                return False
            if boundary_pair is not None or boundary_vector is not None:
                return False
            if float(section["shape_span_local"]) != 0.0:
                return False
        else:
            if not _metadata_pair_is_valid(boundary_pair):
                return False
            if not _metadata_vector_is_valid(boundary_vector, require_nonzero=True):
                return False
            if float(section["shape_span_local"]) <= 0.0:
                return False
        for key in ("shape_width_axis_local", "shape_normal_local"):
            if not _metadata_vector_is_valid(section.get(key), require_nonzero=True):
                return False
        if section.get("shape_source") not in {"FRONT_WIDE_FACE", "FRAME_NORMAL"}:
            return False
        front_faces = section.get("front_face_indices")
        if not isinstance(front_faces, list) or any(
            not isinstance(index, int) or isinstance(index, bool) or index < 0
            for index in front_faces
        ):
            return False
    if metadata_version >= 4:
        if not _metadata_vector_is_valid(section.get("center_local")):
            return False
        if not _metadata_vector_is_valid(section.get("front_target_local")):
            return False
        target_source = section.get("front_target_source")
        if target_source not in {
            "FRONT_WIDE_FACE_EDGE",
            "MAX_NORMAL_PROJECTION",
            "POINT_CENTER",
        }:
            return False
        if is_point and target_source != "POINT_CENTER":
            return False
        if not is_point and target_source == "POINT_CENTER":
            return False
    return True


def _curve_cross_section_metadata(curve_obj, *, validate_spline=True):
    if curve_obj is None or curve_obj.type != "CURVE":
        return None
    marker_version = curve_obj.get("character_designer_metadata_version")
    if marker_version not in SUPPORTED_METADATA_VERSIONS:
        return None
    try:
        metadata = json.loads(
            curve_obj.get("character_designer_cross_sections", ""),
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(metadata, dict)
        or metadata.get("version") != marker_version
        or marker_version not in SUPPORTED_METADATA_VERSIONS
        or metadata.get("space") != METADATA_SPACE
    ):
        return None

    sections = metadata.get("sections")
    point_count = metadata.get("point_count")
    if (
        not isinstance(sections, list)
        or not isinstance(point_count, int)
        or isinstance(point_count, bool)
        or point_count < 2
        or point_count != len(sections)
    ):
        return None
    matrix = metadata.get("matrix_world_at_build")
    if (
        not isinstance(matrix, list)
        or len(matrix) != 16
        or not all(_is_finite_number(value) for value in matrix)
    ):
        return None
    if marker_version >= 3:
        tip_radius = metadata.get("profile_tip_radius")
        if (
            not _is_finite_number(tip_radius)
            or float(tip_radius) <= 0.0
            or float(tip_radius) >= 1.0
        ):
            return None
    if any(
        not _metadata_section_is_valid(
            section,
            is_first=index == 0,
            is_last=index == point_count - 1,
            metadata_version=marker_version,
        )
        for index, section in enumerate(sections)
    ):
        return None

    if validate_spline:
        splines = curve_obj.data.splines
        if (
            len(splines) != 1
            or splines[0].type != "POLY"
            or len(splines[0].points) != point_count
        ):
            return None
    return metadata


def _metadata_summary(metadata):
    sections = metadata.get("sections", ()) if metadata else ()
    # A point tip correctly stores zero span, but including that zero in the
    # visible range hides the useful size range of the actual cross-sections.
    # Keep the lossless zero in JSON and summarize only non-point layers.
    span_key = "shape_span_local" if metadata and metadata.get("version", 0) >= 3 else (
        "max_profile_span_local"
    )
    profile_spans = [
        section.get(span_key)
        for section in sections
        if section.get("kind") != "POINT"
    ]
    profile_spans = [
        float(span) for span in profile_spans if isinstance(span, (int, float))
    ]
    if not profile_spans:
        return None

    mesh_count = sum(
        section.get("orientation_source") == "MESH_NORMAL"
        for section in sections
    )
    tip_count = sum(
        section.get("orientation_source") == "TRANSPORTED_TIP"
        for section in sections
    )
    fallback_count = len(sections) - mesh_count - tip_count
    return {
        "minimum_profile_span": min(profile_spans),
        "maximum_profile_span": max(profile_spans),
        "root_profile_span": float(sections[0][span_key]),
        "tip_profile_span": float(sections[-1][span_key]),
        "mesh_count": mesh_count,
        "tracked_count": fallback_count,
        "tip_count": tip_count,
    }


def _compact_number(value):
    return "0" if abs(value) <= EPSILON else f"{value:.5g}"


def _visible_collections(context):
    visible_collections = set()

    def visit(layer_collection, parent_visible=True):
        visible = (
            parent_visible
            and not layer_collection.exclude
            and not layer_collection.hide_viewport
            and not layer_collection.collection.hide_viewport
            and not layer_collection.collection.hide_select
        )
        if visible:
            visible_collections.add(layer_collection.collection)
        for child in layer_collection.children:
            visit(child, visible)

    visit(context.view_layer.layer_collection)
    return visible_collections


def _visible_source_collection(context, source_obj):
    visible_collections = _visible_collections(context)
    for collection in source_obj.users_collection:
        if collection in visible_collections:
            return collection
    return context.collection or context.scene.collection


def _output_is_selectable(context, output):
    if not (
        output is not None
        and output.name in bpy.data.objects
        and output.type == "CURVE"
        and context.view_layer.objects.get(output.name) is output
        and not output.hide_viewport
        and not output.hide_select
    ):
        return False
    visible_collections = _visible_collections(context)
    return any(collection in visible_collections for collection in output.users_collection)


def _rna_pointer(value):
    try:
        return int(value.as_pointer()) if value is not None else 0
    except (AttributeError, ReferenceError, RuntimeError):
        return 0


def _signature_float(value):
    try:
        return float(value).hex()
    except (OverflowError, TypeError, ValueError):
        return repr(value)


def _ordered_edit_topology_signature(bm):
    """Compatibility name for the full current Edit-BMesh topology digest."""

    return _topology_signature(bm)


def _preview_input_signature(context, settings, *, audit_topology=True):
    """Hash mutable inputs near the selection without walking the whole topology."""

    global _LIVE_PREVIEW_TOPOLOGY_SIGNATURE
    obj, bm = _edit_bmesh(context)
    root_object = settings.root_object
    root_mesh = settings.root_mesh
    digest = hashlib.sha256()
    header = (
        _rna_pointer(obj),
        _rna_pointer(obj.data),
        _rna_pointer(root_object),
        _rna_pointer(root_mesh),
        getattr(context, "mode", ""),
        getattr(obj, "mode", ""),
        settings.root_indices_json,
        settings.root_edges_json,
        settings.root_ignored_indices_json,
        settings.root_kind,
        settings.root_capture_mode,
        settings.topology_signature,
        _LIVE_PREVIEW_DEPSGRAPH_REVISION,
    )
    digest.update(repr(header).encode("utf-8", "backslashreplace"))
    if audit_topology or not _LIVE_PREVIEW_TOPOLOGY_SIGNATURE:
        _LIVE_PREVIEW_TOPOLOGY_SIGNATURE = _ordered_edit_topology_signature(bm)
    digest.update(_LIVE_PREVIEW_TOPOLOGY_SIGNATURE.encode("ascii"))

    for row in range(4):
        for column in range(4):
            digest.update(_signature_float(obj.matrix_world[row][column]).encode("ascii"))
            digest.update(b",")

    digest.update(f"v{len(bm.verts)}e{len(bm.edges)}f{len(bm.faces)}|".encode("ascii"))
    selected_vertices = tuple(vertex for vertex in bm.verts if vertex.select)
    incident_edges = {edge for vertex in selected_vertices for edge in vertex.link_edges}
    incident_faces = {face for vertex in selected_vertices for face in vertex.link_faces}

    for vertex in selected_vertices:
        digest.update(f"v{vertex.index}".encode("ascii"))
        for component in vertex.co:
            digest.update(b":")
            digest.update(_signature_float(component).encode("ascii"))
        digest.update(b";")

    for edge in sorted(incident_edges, key=lambda value: value.index):
        first, second = sorted((edge.verts[0].index, edge.verts[1].index))
        digest.update(f"e{edge.index}:{first}:{second}:{int(bool(edge.select))};".encode("ascii"))

    for face in sorted(incident_faces, key=lambda value: value.index):
        ordered_vertices = ",".join(str(vertex.index) for vertex in face.verts)
        digest.update(
            f"f{face.index}:{int(bool(face.select))}:{ordered_vertices};".encode("ascii")
        )
    return digest.hexdigest()


def _active_element_signature(bm):
    active = getattr(bm.select_history, "active", None)
    if active is None or not getattr(active, "select", False):
        return None
    if isinstance(active, bmesh.types.BMVert):
        return "VERT", active.index
    if isinstance(active, bmesh.types.BMEdge):
        return "EDGE", tuple(sorted(vertex.index for vertex in active.verts))
    if isinstance(active, bmesh.types.BMFace):
        indices = tuple(vertex.index for vertex in active.verts)
        if not indices:
            return "FACE", ()
        start = indices.index(min(indices))
        return "FACE", indices[start:] + indices[:start]
    return type(active).__name__, getattr(active, "index", -1)


def _auto_preview_input_signature(context):
    """Return an O(1) interaction signature for the timer's unchanged fast path.

    Actual inference strictly reads and validates the complete selected BMesh
    whenever this signature changes. Selection counts cover grow/shrink, active
    history covers endpoint changes, and the depsgraph revision covers mesh
    coordinate/connectivity updates. This avoids re-hashing a 100k-vertex hair
    selection ten times per second while the artist is idle.
    """

    obj = context.edit_object
    if (
        obj is None
        or obj.type != "MESH"
        or obj.mode != "EDIT"
        or context.object is not obj
    ):
        raise CenterlineError("Enter Mesh Edit Mode on one hair object.")
    bm = bmesh.from_edit_mesh(obj.data)
    matrix_signature = tuple(
        _signature_float(obj.matrix_world[row][column])
        for row in range(4)
        for column in range(4)
    )
    return (
        "AUTO",
        _rna_pointer(obj),
        _rna_pointer(obj.data),
        getattr(context, "mode", ""),
        getattr(obj, "mode", ""),
        len(bm.verts),
        len(bm.edges),
        len(bm.faces),
        int(obj.data.total_vert_sel),
        int(obj.data.total_edge_sel),
        int(obj.data.total_face_sel),
        len(bm.select_history),
        tuple(bool(value) for value in context.tool_settings.mesh_select_mode),
        _active_element_signature(bm),
        matrix_signature,
        _LIVE_PREVIEW_DEPSGRAPH_REVISION,
    )


def _selection_identity_digest(obj):
    """Hash only selected vertex identity for a sparse missed-event audit."""

    bm = bmesh.from_edit_mesh(obj.data)
    selected_ordinals = array("i")
    selected_ordinals.extend(
        ordinal for ordinal, vertex in enumerate(bm.verts) if vertex.select
    )
    digest = hashlib.blake2b(digest_size=16)
    digest.update(selected_ordinals.tobytes())
    return digest.digest()


def _auto_preview_fallback_signature(context, message):
    active_object = getattr(context, "object", None)
    values = (
        "AUTO_WAITING",
        _rna_pointer(active_object),
        _rna_pointer(getattr(active_object, "data", None)),
        getattr(context, "mode", ""),
        getattr(active_object, "mode", "") if active_object is not None else "",
        str(message),
    )
    return hashlib.sha256(repr(values).encode("utf-8", "backslashreplace")).hexdigest()


def _preview_fallback_signature(context, settings, message):
    root_object = getattr(settings, "root_object", None)
    root_mesh = getattr(settings, "root_mesh", None)
    active_object = getattr(context, "object", None)
    values = (
        "INVALID",
        _rna_pointer(root_object),
        _rna_pointer(root_mesh),
        _rna_pointer(active_object),
        getattr(context, "mode", ""),
        getattr(settings, "root_indices_json", ""),
        getattr(settings, "root_edges_json", ""),
        getattr(settings, "root_ignored_indices_json", ""),
        getattr(settings, "root_kind", ""),
        str(message),
    )
    return hashlib.sha256(repr(values).encode("utf-8", "backslashreplace")).hexdigest()


def _short_preview_message(error):
    message = " ".join(str(error).split()) or "Preview unavailable."
    return message if len(message) <= 112 else f"{message[:109]}..."


def _invalidate_live_preview_batches(*, clear_shader=False):
    global _LIVE_PREVIEW_SHADER
    global _LIVE_PREVIEW_LINE_BATCH, _LIVE_PREVIEW_POINT_BATCH
    global _LIVE_PREVIEW_HANDLE_BATCH
    global _LIVE_PREVIEW_BATCH_REVISION, _LIVE_PREVIEW_DRAW_FAILED
    global _LIVE_PREVIEW_DRAW_RETRY_AT

    _LIVE_PREVIEW_LINE_BATCH = None
    _LIVE_PREVIEW_POINT_BATCH = None
    _LIVE_PREVIEW_HANDLE_BATCH = None
    _LIVE_PREVIEW_BATCH_REVISION = -1
    _LIVE_PREVIEW_DRAW_FAILED = False
    _LIVE_PREVIEW_DRAW_RETRY_AT = 0.0
    if clear_shader:
        _LIVE_PREVIEW_SHADER = None


def _clear_live_preview_cache():
    global _LIVE_PREVIEW_INPUT_SIGNATURE
    global _LIVE_PREVIEW_WORLD_POINTS, _LIVE_PREVIEW_LAYERS
    global _LIVE_PREVIEW_PATHS, _LIVE_PREVIEW_HANDLE_POINTS
    global _LIVE_PREVIEW_METADATA_JSON, _LIVE_PREVIEW_SOURCE_KEY
    global _LIVE_PREVIEW_TRACKED_SOURCE_KEY
    global _LIVE_PREVIEW_DEPSGRAPH_REVISION
    global _LIVE_PREVIEW_TOPOLOGY_SIGNATURE, _LIVE_PREVIEW_TOPOLOGY_DIRTY
    global _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT
    global _LIVE_PREVIEW_TOPOLOGY_VERIFIED
    global _LIVE_PREVIEW_SELECTION_DIGEST, _LIVE_PREVIEW_SELECTION_AUDIT_AT

    _LIVE_PREVIEW_INPUT_SIGNATURE = None
    _LIVE_PREVIEW_WORLD_POINTS = ()
    _LIVE_PREVIEW_PATHS = ()
    _LIVE_PREVIEW_HANDLE_POINTS = ()
    _LIVE_PREVIEW_LAYERS = ()
    _LIVE_PREVIEW_METADATA_JSON = ""
    _LIVE_PREVIEW_SOURCE_KEY = None
    _LIVE_PREVIEW_TRACKED_SOURCE_KEY = None
    _LIVE_PREVIEW_DEPSGRAPH_REVISION = 0
    _LIVE_PREVIEW_TOPOLOGY_SIGNATURE = ""
    _LIVE_PREVIEW_TOPOLOGY_DIRTY = True
    _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT = 0.0
    _LIVE_PREVIEW_TOPOLOGY_VERIFIED = False
    _LIVE_PREVIEW_SELECTION_DIGEST = None
    _LIVE_PREVIEW_SELECTION_AUDIT_AT = 0.0
    _invalidate_live_preview_batches(clear_shader=True)


def _reset_preview_properties(settings):
    settings.preview_level = "WAITING"
    settings.preview_valid = False
    settings.preview_confirmable = False
    settings.preview_layer_count = 0
    settings.preview_message = ""
    settings.preview_action_error = ""
    settings.preview_root_span = 0.0
    settings.preview_max_span = 0.0
    settings.preview_tip_span = 0.0
    settings.preview_orientation_valid = False


def _publish_live_preview(
    settings,
    source_obj,
    layers,
    world_points,
    *,
    metadata=None,
    confirmable,
    message,
):
    global _LIVE_PREVIEW_WORLD_POINTS, _LIVE_PREVIEW_LAYERS
    global _LIVE_PREVIEW_PATHS, _LIVE_PREVIEW_HANDLE_POINTS
    global _LIVE_PREVIEW_METADATA_JSON, _LIVE_PREVIEW_SOURCE_KEY

    immutable_layers = tuple(tuple(int(index) for index in layer) for layer in layers)
    immutable_points = tuple(
        tuple(float(component) for component in point)
        for point in world_points
    )
    metadata_json = (
        json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if metadata is not None
        else ""
    )
    summary = _metadata_summary(metadata)

    _LIVE_PREVIEW_LAYERS = immutable_layers
    _LIVE_PREVIEW_WORLD_POINTS = immutable_points
    _LIVE_PREVIEW_PATHS = (immutable_points,) if immutable_points else ()
    _LIVE_PREVIEW_HANDLE_POINTS = ()
    _LIVE_PREVIEW_METADATA_JSON = metadata_json
    _LIVE_PREVIEW_SOURCE_KEY = (
        _rna_pointer(source_obj),
        _rna_pointer(source_obj.data),
    )
    _invalidate_live_preview_batches()

    settings.preview_valid = True
    settings.preview_level = "VALID"
    settings.preview_confirmable = bool(confirmable)
    settings.preview_layer_count = len(immutable_layers)
    settings.preview_message = message
    settings.preview_action_error = ""
    settings.preview_root_span = float(summary["root_profile_span"]) if summary else 0.0
    settings.preview_max_span = float(summary["maximum_profile_span"]) if summary else 0.0
    settings.preview_tip_span = float(summary["tip_profile_span"]) if summary else 0.0
    settings.preview_orientation_valid = metadata is not None
    settings.preview_revision += 1
    _tag_view3d_redraw()


def _publish_live_preview_invalid(settings, error):
    global _LIVE_PREVIEW_WORLD_POINTS, _LIVE_PREVIEW_LAYERS
    global _LIVE_PREVIEW_PATHS, _LIVE_PREVIEW_HANDLE_POINTS
    global _LIVE_PREVIEW_METADATA_JSON, _LIVE_PREVIEW_SOURCE_KEY
    global _LIVE_PREVIEW_TOPOLOGY_VERIFIED

    if isinstance(error, TopologyChangedError):
        _LIVE_PREVIEW_TOPOLOGY_VERIFIED = False

    _LIVE_PREVIEW_WORLD_POINTS = ()
    _LIVE_PREVIEW_PATHS = ()
    _LIVE_PREVIEW_HANDLE_POINTS = ()
    _LIVE_PREVIEW_LAYERS = ()
    _LIVE_PREVIEW_METADATA_JSON = ""
    _LIVE_PREVIEW_SOURCE_KEY = None
    _invalidate_live_preview_batches()
    _reset_preview_properties(settings)
    settings.preview_level = "ERROR"
    settings.preview_message = _short_preview_message(error)
    settings.preview_revision += 1
    _tag_view3d_redraw()


def _publish_live_preview_waiting(settings, message):
    """Clear stale geometry while keeping the Live Preview toggle running."""

    global _LIVE_PREVIEW_WORLD_POINTS, _LIVE_PREVIEW_LAYERS
    global _LIVE_PREVIEW_PATHS, _LIVE_PREVIEW_HANDLE_POINTS
    global _LIVE_PREVIEW_METADATA_JSON, _LIVE_PREVIEW_SOURCE_KEY

    _LIVE_PREVIEW_WORLD_POINTS = ()
    _LIVE_PREVIEW_PATHS = ()
    _LIVE_PREVIEW_HANDLE_POINTS = ()
    _LIVE_PREVIEW_LAYERS = ()
    _LIVE_PREVIEW_METADATA_JSON = ""
    _LIVE_PREVIEW_SOURCE_KEY = None
    _invalidate_live_preview_batches()
    _reset_preview_properties(settings)
    settings.preview_message = message
    settings.preview_revision += 1
    _tag_view3d_redraw()


def _world_preview_points(source_obj, centers):
    points = []
    for point_index, center in enumerate(centers):
        world = source_obj.matrix_world @ Vector(center)
        if not _vector_is_finite(world):
            raise CenterlineError(
                f"Preview point {point_index + 1} has non-finite world coordinates."
            )
        points.append((float(world.x), float(world.y), float(world.z)))
    return tuple(points)


def _update_live_preview(context, force=False, *, _timer_driven=False):
    """Recompute and atomically publish live preview data when its input changes."""

    global _LIVE_PREVIEW_INPUT_SIGNATURE, _LIVE_PREVIEW_TOPOLOGY_DIRTY
    global _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT
    global _LIVE_PREVIEW_TOPOLOGY_VERIFIED

    settings = _settings(context)
    if settings is None or not settings.preview_active:
        return False

    try:
        audit_topology = bool(
            force
            or not _timer_driven
            or not _LIVE_PREVIEW_TOPOLOGY_SIGNATURE
            or (
                _LIVE_PREVIEW_TOPOLOGY_DIRTY
                and time.monotonic() >= _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT
            )
        )
        signature = _preview_input_signature(
            context,
            settings,
            audit_topology=audit_topology,
        )
        if audit_topology:
            _LIVE_PREVIEW_TOPOLOGY_DIRTY = False
            _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT = 0.0
            if _LIVE_PREVIEW_TOPOLOGY_SIGNATURE != settings.topology_signature:
                _LIVE_PREVIEW_TOPOLOGY_VERIFIED = False
                raise TopologyChangedError("Hair topology changed after capture. Capture the root again.")
            _LIVE_PREVIEW_TOPOLOGY_VERIFIED = True
        elif not _LIVE_PREVIEW_TOPOLOGY_VERIFIED:
            # A failed full audit is a latch: a cheap local pass must not make
            # an unchanged, globally-invalid mesh look valid again. Only a later
            # successful full audit (normally after Undo) may release it.
            return False
    except Exception as exc:
        signature = _preview_fallback_signature(context, settings, exc)
        if not force and signature == _LIVE_PREVIEW_INPUT_SIGNATURE:
            return False
        _LIVE_PREVIEW_INPUT_SIGNATURE = signature
        _publish_live_preview_invalid(settings, exc)
        return True

    if not force and signature == _LIVE_PREVIEW_INPUT_SIGNATURE:
        return False
    _LIVE_PREVIEW_INPUT_SIGNATURE = signature

    try:
        validated_capture = _validate_capture(
            context,
            settings,
            check_topology=False,
        )
        source_obj, bm, root_indices, _root_edges, ignored_indices = validated_capture
        ignored_set = set(ignored_indices)
        selected = {
            vertex.index
            for vertex in bm.verts
            if vertex.select and vertex.index not in ignored_set
        }
        root_set = set(root_indices)
        missing = root_set - selected
        if missing:
            raise CenterlineError("Keep the captured root selected while growing toward the tip.")

        if selected == root_set:
            root_layer = tuple(root_indices)
            _validate_layer_coordinates(bm, (root_layer,))
            _validate_source_matrix(source_obj)
            center = sum(
                (bm.verts[index].co for index in root_layer),
                Vector((0.0, 0.0, 0.0)),
            ) / len(root_layer)
            if not _vector_is_finite(center):
                raise CenterlineError("The captured root has a non-finite center point.")
            _publish_live_preview(
                settings,
                source_obj,
                (root_layer,),
                _world_preview_points(source_obj, (center,)),
                metadata=None,
                confirmable=False,
                message="Grow the selection to continue the centerline.",
            )
            return True

        source_obj, bm, layers, centers = _extract_layers(
            context,
            settings,
            check_topology=False,
            validated_capture=validated_capture,
        )
        regular_kind = (
            _regular_kind_from_layers(bm, layers)
            if settings.root_kind == "POINT"
            else settings.root_kind
        )
        metadata = _build_cross_section_metadata(
            source_obj,
            bm,
            layers,
            centers,
            regular_kind,
        )
        _publish_live_preview(
            settings,
            source_obj,
            layers,
            _world_preview_points(source_obj, centers),
            metadata=metadata,
            confirmable=len(layers) >= 2,
            message="Live preview ready.",
        )
    except Exception as exc:
        _publish_live_preview_invalid(settings, exc)
    return True


def _store_auto_preview_source(settings, obj, bm, layers, regular_kind):
    """Keep only the lightweight source/endpoint state needed for drawing."""

    global _LIVE_PREVIEW_TRACKED_SOURCE_KEY

    root_indices = tuple(layers[0])
    root_edges = _contained_edge_keys(bm, root_indices)
    root_kind = "POINT" if len(root_indices) == 1 else regular_kind
    settings.root_object = obj
    settings.root_mesh = obj.data
    settings.root_indices_json = json.dumps(root_indices, separators=(",", ":"))
    settings.root_edges_json = json.dumps(root_edges, separators=(",", ":"))
    settings.root_ignored_indices_json = "[]"
    settings.root_kind = root_kind
    settings.root_capture_mode = "AUTO_SELECTION"
    settings.root_vertex_count = len(root_indices)
    settings.root_ignored_count = 0
    settings.topology_signature = ""
    _LIVE_PREVIEW_TRACKED_SOURCE_KEY = (_rna_pointer(obj), _rna_pointer(obj.data))


def _update_auto_live_preview(context, force=False):
    """Continuously infer a centerline from the complete current selection."""

    global _LIVE_PREVIEW_INPUT_SIGNATURE, _LIVE_PREVIEW_TRACKED_SOURCE_KEY
    global _LIVE_PREVIEW_SELECTION_DIGEST, _LIVE_PREVIEW_SELECTION_AUDIT_AT

    settings = _settings(context)
    if settings is None or not settings.preview_active or settings.preview_mode != "AUTO":
        return False

    obj = context.edit_object
    if (
        obj is None
        or obj.type != "MESH"
        or obj.mode != "EDIT"
        or context.object is not obj
    ):
        message = "Paused - enter Mesh Edit Mode."
        signature = _auto_preview_fallback_signature(context, message)
        if not force and signature == _LIVE_PREVIEW_INPUT_SIGNATURE:
            return False
        _LIVE_PREVIEW_INPUT_SIGNATURE = signature
        _publish_live_preview_waiting(settings, message)
        return True

    _LIVE_PREVIEW_TRACKED_SOURCE_KEY = (_rna_pointer(obj), _rna_pointer(obj.data))
    try:
        signature = _auto_preview_input_signature(context)
    except Exception as exc:
        signature = _auto_preview_fallback_signature(context, exc)
        if not force and signature == _LIVE_PREVIEW_INPUT_SIGNATURE:
            return False
        _LIVE_PREVIEW_INPUT_SIGNATURE = signature
        _publish_live_preview_invalid(settings, exc)
        return True
    now = time.monotonic()
    signature_changed = signature != _LIVE_PREVIEW_INPUT_SIGNATURE
    audit_due = now >= _LIVE_PREVIEW_SELECTION_AUDIT_AT
    if not force and not signature_changed and not audit_due:
        return False
    selection_digest = _selection_identity_digest(obj)
    _LIVE_PREVIEW_SELECTION_AUDIT_AT = now + LIVE_PREVIEW_SELECTION_AUDIT_INTERVAL
    if (
        not force
        and not signature_changed
        and selection_digest == _LIVE_PREVIEW_SELECTION_DIGEST
    ):
        return False
    _LIVE_PREVIEW_INPUT_SIGNATURE = signature
    _LIVE_PREVIEW_SELECTION_DIGEST = selection_digest

    if obj.data.total_vert_sel == 0:
        _clear_capture(settings)
        _LIVE_PREVIEW_TRACKED_SOURCE_KEY = (_rna_pointer(obj), _rna_pointer(obj.data))
        _publish_live_preview_waiting(settings, "No mesh elements selected.")
        return True

    try:
        (
            source_obj,
            bm,
            layers,
            centers,
            regular_kind,
            direction_confirmable,
        ) = _infer_selected_centerline(context)
        _store_auto_preview_source(settings, source_obj, bm, layers, regular_kind)
        if len(layers) == 1:
            _publish_live_preview(
                settings,
                source_obj,
                layers,
                _world_preview_points(source_obj, centers),
                metadata=None,
                confirmable=False,
                message="Select more connected cross-sections.",
            )
            return True
        metadata = _build_cross_section_metadata(
            source_obj,
            bm,
            layers,
            centers,
            regular_kind,
        )
        _publish_live_preview(
            settings,
            source_obj,
            layers,
            _world_preview_points(source_obj, centers),
            metadata=metadata,
            confirmable=direction_confirmable,
            message=(
                "Live preview ready."
                if direction_confirmable
                else "Direction unclear - select one end as Start."
            ),
        )
    except CenterlineError as exc:
        _publish_live_preview_invalid(settings, exc)
    except Exception as exc:
        _publish_live_preview_invalid(settings, exc)
    return True


def _start_auto_live_preview(context, settings):
    """Start the session toggle even when Blender is temporarily in Object Mode."""

    _set_recovery_preview_enabled(settings, False)
    _stop_live_preview(settings=settings, clear_capture=True)
    _reset_preview_properties(settings)
    settings.preview_mode = "AUTO"
    settings.preview_active = True
    _ensure_live_preview_runtime()
    _update_auto_live_preview(context, force=True)


def _sample_recovery_preview_path(profile, curve_mode):
    """Evaluate the same explicit Bezier cage used by the committed Curve."""

    coordinates = tuple(Vector(value) for value in profile["coordinates"])
    if curve_mode == RECOVERY_CURVE_MODE_EXACT:
        return coordinates, (), coordinates
    handles = _recovery_aligned_handles(coordinates)
    path = []
    for index in range(len(coordinates) - 1):
        point_0 = coordinates[index]
        point_1 = handles[index][1]
        point_2 = handles[index + 1][0]
        point_3 = coordinates[index + 1]
        for step in range(RECOVERY_BEZIER_RESOLUTION + 1):
            if index and step == 0:
                continue
            factor = step / RECOVERY_BEZIER_RESOLUTION
            inverse = 1.0 - factor
            path.append(
                point_0 * (inverse ** 3)
                + point_1 * (3.0 * inverse * inverse * factor)
                + point_2 * (3.0 * inverse * factor * factor)
                + point_3 * (factor ** 3)
            )
    handle_segments = tuple(
        segment_point
        for coordinate, (handle_left, handle_right) in zip(coordinates, handles)
        for segment_point in (
            coordinate,
            handle_left,
            coordinate,
            handle_right,
        )
    )
    return tuple(path), handle_segments, coordinates


def _publish_recovery_live_preview(settings, source_obj, plans, *, replace_source):
    global _LIVE_PREVIEW_WORLD_POINTS, _LIVE_PREVIEW_PATHS
    global _LIVE_PREVIEW_HANDLE_POINTS, _LIVE_PREVIEW_LAYERS
    global _LIVE_PREVIEW_METADATA_JSON, _LIVE_PREVIEW_SOURCE_KEY
    global _LIVE_PREVIEW_TRACKED_SOURCE_KEY

    matrix = source_obj.matrix_world
    world_paths = []
    world_controls = []
    world_handles = []
    layers = []
    for plan in plans:
        path, handles, controls = _sample_recovery_preview_path(
            plan["profile"],
            plan["curve_mode"],
        )
        world_path = tuple(matrix @ point for point in path)
        world_control = tuple(matrix @ point for point in controls)
        world_handle = tuple(matrix @ point for point in handles)
        if not all(
            _vector_is_finite(point)
            for values in (world_path, world_control, world_handle)
            for point in values
        ):
            raise CenterlineError("Recovery Preview contains non-finite coordinates.")
        world_paths.append(
            tuple(tuple(float(component) for component in point) for point in world_path)
        )
        world_controls.extend(
            tuple(float(component) for component in point) for point in world_control
        )
        world_handles.extend(
            tuple(float(component) for component in point) for point in world_handle
        )
        layers.extend(plan["layers"])

    _LIVE_PREVIEW_PATHS = tuple(world_paths)
    _LIVE_PREVIEW_WORLD_POINTS = tuple(world_controls)
    _LIVE_PREVIEW_HANDLE_POINTS = tuple(world_handles)
    _LIVE_PREVIEW_LAYERS = tuple(
        tuple(int(index) for index in layer) for layer in layers
    )
    _LIVE_PREVIEW_METADATA_JSON = ""
    _LIVE_PREVIEW_SOURCE_KEY = (
        _rna_pointer(source_obj),
        _rna_pointer(source_obj.data),
    )
    _LIVE_PREVIEW_TRACKED_SOURCE_KEY = _LIVE_PREVIEW_SOURCE_KEY
    _invalidate_live_preview_batches()

    settings.root_object = source_obj
    settings.root_mesh = source_obj.data
    settings.root_capture_mode = "RECOVERY_PREVIEW"
    settings.preview_valid = True
    settings.preview_level = "VALID"
    settings.preview_confirmable = True
    settings.preview_layer_count = len(_LIVE_PREVIEW_LAYERS)
    modifier_label = plans[0].get("modifier_label", "") if plans else ""
    modifier_message = (
        f" Preserves {modifier_label}." if modifier_label else ""
    )
    settings.preview_message = (
        (
            "Preview ready - Mesh is removed only after Recover."
            if replace_source
            else "Preview ready - source Mesh will be kept."
        )
        + modifier_message
    )
    settings.preview_action_error = ""
    settings.preview_orientation_valid = True
    settings.preview_revision += 1
    _tag_view3d_redraw()


def _update_recovery_live_preview(context, force=False):
    """Read the Edit Mesh and publish a non-destructive GPU-only recovery Preview."""

    global _LIVE_PREVIEW_INPUT_SIGNATURE, _LIVE_PREVIEW_TRACKED_SOURCE_KEY
    global _LIVE_PREVIEW_SELECTION_DIGEST, _LIVE_PREVIEW_SELECTION_AUDIT_AT

    settings = _settings(context)
    if settings is None or not settings.preview_active or settings.preview_mode != "RECOVERY":
        return False
    source_obj = context.edit_object
    if (
        source_obj is None
        or source_obj.type != "MESH"
        or source_obj.mode != "EDIT"
        or context.object is not source_obj
    ):
        message = "Paused - enter Mesh Edit Mode."
        signature = _auto_preview_fallback_signature(context, message)
        if not force and signature == _LIVE_PREVIEW_INPUT_SIGNATURE:
            return False
        _LIVE_PREVIEW_INPUT_SIGNATURE = signature
        _publish_live_preview_waiting(settings, message)
        return True

    _LIVE_PREVIEW_TRACKED_SOURCE_KEY = (
        _rna_pointer(source_obj),
        _rna_pointer(source_obj.data),
    )
    try:
        signature = _auto_preview_input_signature(context) + (
            "RECOVERY",
            settings.recovery_curve_mode,
            int(settings.recovery_control_points),
            settings.recovery_source_action,
            _recovery_modifier_stack_json(
                _capture_recovery_modifier_stack(
                    source_obj,
                    source_obj=source_obj,
                )
            ),
        )
    except Exception as exc:
        signature = _auto_preview_fallback_signature(context, exc)
        if not force and signature == _LIVE_PREVIEW_INPUT_SIGNATURE:
            return False
        _LIVE_PREVIEW_INPUT_SIGNATURE = signature
        _publish_live_preview_invalid(settings, exc)
        return True

    now = time.monotonic()
    signature_changed = signature != _LIVE_PREVIEW_INPUT_SIGNATURE
    audit_due = now >= _LIVE_PREVIEW_SELECTION_AUDIT_AT
    if not force and not signature_changed and not audit_due:
        return False
    selection_digest = _selection_identity_digest(source_obj)
    _LIVE_PREVIEW_SELECTION_AUDIT_AT = now + LIVE_PREVIEW_SELECTION_AUDIT_INTERVAL
    if (
        not force
        and not signature_changed
        and selection_digest == _LIVE_PREVIEW_SELECTION_DIGEST
    ):
        return False
    _LIVE_PREVIEW_INPUT_SIGNATURE = signature
    _LIVE_PREVIEW_SELECTION_DIGEST = selection_digest

    if source_obj.data.total_vert_sel == 0:
        _publish_live_preview_waiting(settings, "No mesh elements selected.")
        return True
    try:
        inferred_source, bm, records = _infer_selected_recovery_components(context)
        plans = _build_recovery_plans(
            context,
            inferred_source,
            bm,
            records,
            curve_mode=settings.recovery_curve_mode,
            control_points=settings.recovery_control_points,
        )
        replace_source = (
            settings.recovery_source_action == RECOVERY_SOURCE_ACTION_REPLACE
        )
        if replace_source:
            _validate_recovery_replacement(
                context,
                inferred_source,
                bm,
                plans,
            )
        _publish_recovery_live_preview(
            settings,
            inferred_source,
            plans,
            replace_source=replace_source,
        )
    except CenterlineError as exc:
        _publish_live_preview_invalid(settings, exc)
    except Exception as exc:
        _publish_live_preview_invalid(settings, exc)
    return True


def _start_recovery_live_preview(context, settings):
    """Start a GPU-only Preview; no temporary Object or Curve datablock is made."""

    _set_live_preview_enabled(settings, False)
    _stop_live_preview(settings=settings, clear_capture=True)
    _reset_preview_properties(settings)
    settings.preview_mode = "RECOVERY"
    settings.preview_active = True
    _ensure_live_preview_runtime()
    _update_recovery_live_preview(context, force=True)


def _draw_live_preview():
    global _LIVE_PREVIEW_SHADER
    global _LIVE_PREVIEW_LINE_BATCH, _LIVE_PREVIEW_POINT_BATCH
    global _LIVE_PREVIEW_HANDLE_BATCH
    global _LIVE_PREVIEW_BATCH_REVISION, _LIVE_PREVIEW_DRAW_FAILED
    global _LIVE_PREVIEW_DRAW_RETRY_AT

    if bpy.app.background or not _LIVE_PREVIEW_WORLD_POINTS:
        return
    if _LIVE_PREVIEW_DRAW_FAILED:
        if time.monotonic() < _LIVE_PREVIEW_DRAW_RETRY_AT:
            return
        # A viewport/context transition can make one draw fail transiently.
        # Retry with fresh GPU resources instead of disabling Preview forever.
        _LIVE_PREVIEW_DRAW_FAILED = False
        _LIVE_PREVIEW_SHADER = None
        _LIVE_PREVIEW_LINE_BATCH = None
        _LIVE_PREVIEW_POINT_BATCH = None
        _LIVE_PREVIEW_HANDLE_BATCH = None
        _LIVE_PREVIEW_BATCH_REVISION = -1
    try:
        context = bpy.context
        if context.area is None or context.area.type != "VIEW_3D":
            return
        settings = _settings(context)
        if settings is None or not settings.preview_active or not settings.preview_valid:
            return
        source_obj = settings.root_object
        if (
            source_obj is None
            or _LIVE_PREVIEW_SOURCE_KEY
            != (_rna_pointer(source_obj), _rna_pointer(source_obj.data))
            or context.scene.objects.get(source_obj.name) is not source_obj
            or context.view_layer.objects.get(source_obj.name) is not source_obj
            or not source_obj.visible_get(view_layer=context.view_layer)
        ):
            return

        import gpu
        from gpu_extras.batch import batch_for_shader

        if _LIVE_PREVIEW_SHADER is None:
            _LIVE_PREVIEW_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
        if _LIVE_PREVIEW_BATCH_REVISION != settings.preview_revision:
            line_vertices = tuple(
                point
                for path in _LIVE_PREVIEW_PATHS
                for first, second in zip(path, path[1:])
                for point in (first, second)
            )
            _LIVE_PREVIEW_POINT_BATCH = batch_for_shader(
                _LIVE_PREVIEW_SHADER,
                "POINTS",
                {"pos": _LIVE_PREVIEW_WORLD_POINTS},
            )
            _LIVE_PREVIEW_LINE_BATCH = (
                batch_for_shader(
                    _LIVE_PREVIEW_SHADER,
                    "LINES",
                    {"pos": line_vertices},
                )
                if line_vertices
                else None
            )
            _LIVE_PREVIEW_HANDLE_BATCH = (
                batch_for_shader(
                    _LIVE_PREVIEW_SHADER,
                    "LINES",
                    {"pos": _LIVE_PREVIEW_HANDLE_POINTS},
                )
                if _LIVE_PREVIEW_HANDLE_POINTS
                else None
            )
            _LIVE_PREVIEW_BATCH_REVISION = settings.preview_revision

        previous_depth = gpu.state.depth_test_get()
        previous_depth_mask = gpu.state.depth_mask_get()
        previous_blend = gpu.state.blend_get()
        previous_line_width = gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(3.0)
            gpu.state.point_size_set(7.0)
            _LIVE_PREVIEW_SHADER.bind()
            _LIVE_PREVIEW_SHADER.uniform_float("color", LIVE_PREVIEW_COLOR)
            if _LIVE_PREVIEW_LINE_BATCH is not None:
                _LIVE_PREVIEW_LINE_BATCH.draw(_LIVE_PREVIEW_SHADER)
            if _LIVE_PREVIEW_HANDLE_BATCH is not None:
                _LIVE_PREVIEW_SHADER.uniform_float(
                    "color",
                    (LIVE_PREVIEW_COLOR[0], LIVE_PREVIEW_COLOR[1], LIVE_PREVIEW_COLOR[2], 0.42),
                )
                _LIVE_PREVIEW_HANDLE_BATCH.draw(_LIVE_PREVIEW_SHADER)
                _LIVE_PREVIEW_SHADER.uniform_float("color", LIVE_PREVIEW_COLOR)
            if _LIVE_PREVIEW_POINT_BATCH is not None:
                _LIVE_PREVIEW_POINT_BATCH.draw(_LIVE_PREVIEW_SHADER)
        finally:
            for restore, value in (
                (gpu.state.point_size_set, 1.0),
                (gpu.state.line_width_set, previous_line_width),
                (gpu.state.blend_set, previous_blend),
                (gpu.state.depth_mask_set, previous_depth_mask),
                (gpu.state.depth_test_set, previous_depth),
            ):
                try:
                    restore(value)
                except Exception:
                    pass
    except Exception:
        _LIVE_PREVIEW_DRAW_FAILED = True
        _LIVE_PREVIEW_DRAW_RETRY_AT = time.monotonic() + 1.0
        _LIVE_PREVIEW_LINE_BATCH = None
        _LIVE_PREVIEW_POINT_BATCH = None
        _LIVE_PREVIEW_HANDLE_BATCH = None


def _remove_live_preview_draw_handler():
    global _LIVE_PREVIEW_DRAW_HANDLE

    handle = _LIVE_PREVIEW_DRAW_HANDLE
    _LIVE_PREVIEW_DRAW_HANDLE = None
    if handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass


def _live_preview_load_pre(_unused):
    # Do not mutate Blender's load_pre list while it is iterating callbacks;
    # removing this entry here can skip the next add-on's handler. Non-persistent
    # handlers are discarded by Blender as the new file loads.
    settings = _settings(bpy.context)
    _set_live_preview_enabled(settings, False)
    _set_recovery_preview_enabled(settings, False)
    _stop_live_preview(
        settings=settings,
        clear_capture=True,
        remove_load_handler=False,
    )


def _live_preview_depsgraph_update(_scene, depsgraph):
    """Mark non-selection scene/data edits for validation on the next tick."""

    global _LIVE_PREVIEW_DEPSGRAPH_REVISION, _LIVE_PREVIEW_TOPOLOGY_DIRTY
    global _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT
    if _LIVE_PREVIEW_TRACKED_SOURCE_KEY is None:
        return
    try:
        matched = False
        mesh_geometry_changed = False
        for update in depsgraph.updates:
            updated_id = getattr(update.id, "original", update.id)
            updated_pointer = _rna_pointer(updated_id)
            if updated_pointer not in _LIVE_PREVIEW_TRACKED_SOURCE_KEY:
                continue
            matched = True
            if (
                updated_pointer == _LIVE_PREVIEW_TRACKED_SOURCE_KEY[1]
                and getattr(update, "is_updated_geometry", False)
            ):
                mesh_geometry_changed = True
        if matched:
            _LIVE_PREVIEW_DEPSGRAPH_REVISION += 1
        if mesh_geometry_changed:
            # Blender reports both coordinate transforms and connectivity edits as
            # Mesh geometry updates. Keep the 0.1 s coordinate/selection preview
            # fast, then perform one full topology audit after interaction settles.
            _LIVE_PREVIEW_TOPOLOGY_DIRTY = True
            _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT = (
                time.monotonic() + LIVE_PREVIEW_TOPOLOGY_DEBOUNCE
            )
    except Exception:
        # If Blender invalidates an update wrapper during Undo/Redo, force one
        # conservative revalidation rather than risking a stale preview.
        _LIVE_PREVIEW_DEPSGRAPH_REVISION += 1
        _LIVE_PREVIEW_TOPOLOGY_DIRTY = True
        _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT = time.monotonic()


def _live_preview_history_post(_unused):
    """Force immediate revalidation after Undo/Redo, even if depsgraph coalesces updates."""

    global _LIVE_PREVIEW_DEPSGRAPH_REVISION, _LIVE_PREVIEW_TOPOLOGY_DIRTY
    global _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT
    settings = _settings(bpy.context)
    if (
        settings is None
        or not settings.preview_active
        or _LIVE_PREVIEW_TRACKED_SOURCE_KEY is None
    ):
        return
    _LIVE_PREVIEW_DEPSGRAPH_REVISION += 1
    _LIVE_PREVIEW_TOPOLOGY_DIRTY = True
    _LIVE_PREVIEW_TOPOLOGY_AUDIT_AT = time.monotonic()


def _ensure_live_preview_runtime():
    global _LIVE_PREVIEW_DRAW_HANDLE, _LIVE_PREVIEW_DRAW_FAILED
    global _LIVE_PREVIEW_TRACKED_SOURCE_KEY

    _LIVE_PREVIEW_DRAW_FAILED = False
    settings = _settings(bpy.context)
    if settings is not None and settings.root_object is not None and settings.root_mesh is not None:
        _LIVE_PREVIEW_TRACKED_SOURCE_KEY = (
            _rna_pointer(settings.root_object),
            _rna_pointer(settings.root_mesh),
        )
    if not bpy.app.timers.is_registered(_live_preview_tick):
        bpy.app.timers.register(
            _live_preview_tick,
            first_interval=LIVE_PREVIEW_INTERVAL,
            persistent=False,
        )
    if _LIVE_PREVIEW_DRAW_HANDLE is None:
        _LIVE_PREVIEW_DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            _draw_live_preview,
            (),
            "WINDOW",
            "POST_VIEW",
        )
    if _live_preview_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_live_preview_load_pre)
    if _live_preview_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_live_preview_depsgraph_update)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _live_preview_history_post not in handlers:
            handlers.append(_live_preview_history_post)


def _live_preview_tick():
    settings = _settings(bpy.context)
    if settings is None or not settings.preview_active:
        _remove_live_preview_draw_handler()
        try:
            if _live_preview_load_pre in bpy.app.handlers.load_pre:
                bpy.app.handlers.load_pre.remove(_live_preview_load_pre)
        except Exception:
            pass
        try:
            if _live_preview_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
                bpy.app.handlers.depsgraph_update_post.remove(_live_preview_depsgraph_update)
        except Exception:
            pass
        for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
            try:
                if _live_preview_history_post in handlers:
                    handlers.remove(_live_preview_history_post)
            except Exception:
                pass
        _clear_live_preview_cache()
        return None
    try:
        if settings.preview_mode == "RECOVERY":
            _update_recovery_live_preview(bpy.context, force=False)
        elif settings.preview_mode == "AUTO":
            _update_auto_live_preview(bpy.context, force=False)
        else:
            _update_live_preview(bpy.context, force=False, _timer_driven=True)
    except Exception as exc:
        _publish_live_preview_invalid(settings, exc)
    if _LIVE_PREVIEW_DRAW_FAILED and time.monotonic() >= _LIVE_PREVIEW_DRAW_RETRY_AT:
        _tag_view3d_redraw()
    return LIVE_PREVIEW_INTERVAL if settings.preview_active else None


def _stop_live_preview(settings=None, clear_capture=True, remove_load_handler=True):
    if settings is None:
        settings = _settings(bpy.context)
    if settings is not None:
        try:
            settings.preview_active = False
        except (AttributeError, ReferenceError):
            settings = None

    try:
        if bpy.app.timers.is_registered(_live_preview_tick):
            bpy.app.timers.unregister(_live_preview_tick)
    except Exception:
        pass
    _remove_live_preview_draw_handler()
    if remove_load_handler:
        try:
            if _live_preview_load_pre in bpy.app.handlers.load_pre:
                bpy.app.handlers.load_pre.remove(_live_preview_load_pre)
        except Exception:
            pass
    try:
        if _live_preview_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_live_preview_depsgraph_update)
    except Exception:
        pass
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        try:
            if _live_preview_history_post in handlers:
                handlers.remove(_live_preview_history_post)
        except Exception:
            pass
    _clear_live_preview_cache()

    if settings is not None:
        _reset_preview_properties(settings)
        settings.preview_mode = ""
        if clear_capture:
            _clear_capture(settings)
    _tag_view3d_redraw()


def _minimum_twist_baselines(sections):
    tangents = tuple(Vector(section["tangent_local"]).normalized() for section in sections)
    root = Vector((0.0, 0.0, 1.0)).cross(tangents[0])
    if root.length <= EPSILON:
        root = Vector((-1.0, 0.0, 0.0))
    else:
        root.normalize()
    baselines = [root]
    for previous_tangent, tangent in zip(tangents, tangents[1:]):
        baselines.append(_transport_axis(baselines[-1], previous_tangent, tangent))
    return tangents, tuple(baselines)


def _signed_angle_about_axis(source, target, axis):
    return math.atan2(axis.dot(source.cross(target)), source.dot(target))


def _unwrap_angle_near(angle, reference):
    while angle - reference > math.pi:
        angle -= math.tau
    while angle - reference < -math.pi:
        angle += math.tau
    return angle


def _curve_profile_parameters(metadata):
    sections = metadata.get("sections", ()) if metadata else ()
    if len(sections) < 2:
        raise CenterlineError("Hair profile metadata needs at least two sections.")
    shaped_metadata = metadata.get("version", 0) >= 3
    span_key = "shape_span_local" if shaped_metadata else "max_profile_span_local"
    normal_key = "shape_normal_local" if shaped_metadata else "normal_local"
    spans = tuple(float(section[span_key]) for section in sections)
    maximum_span = max(
        (span for span, section in zip(spans, sections) if section.get("kind") != "POINT"),
        default=0.0,
    )
    if maximum_span <= EPSILON:
        raise CenterlineError("The selected hair has no usable cross-section width.")

    # profile_tip_radius is a ratio against the nearest usable non-POINT
    # control-point Radius. This preserves a locally pointed transition at
    # either end instead of sizing every tip against the global widest loop.
    tip_ratio = float(metadata.get("profile_tip_radius", HAIR_PROFILE_TIP_RADIUS))
    regular_radii = tuple(
        span / maximum_span
        if section.get("kind") != "POINT" and span > EPSILON
        else None
        for span, section in zip(spans, sections)
    )
    radii = []
    for point_index, section in enumerate(sections):
        regular_radius = regular_radii[point_index]
        if section.get("kind") != "POINT":
            # A degenerate legacy non-POINT section remains safely visible.
            radii.append(regular_radius if regular_radius is not None else tip_ratio)
            continue

        nearest_regular = min(
            (
                (abs(candidate_index - point_index), candidate_index, candidate_radius)
                for candidate_index, candidate_radius in enumerate(regular_radii)
                if candidate_radius is not None
            ),
            default=None,
        )
        nearest_radius = nearest_regular[2] if nearest_regular is not None else 1.0
        radii.append(nearest_radius * tip_ratio)
    radii = tuple(radii)
    tangents, baselines = _minimum_twist_baselines(sections)
    tilts = _curve_profile_tilts(metadata, tangents, baselines)
    return maximum_span * 0.5, radii, tilts


def _curve_profile_tilts(metadata, tangents, baselines=None):
    sections = metadata.get("sections", ()) if metadata else ()
    if len(sections) != len(tangents):
        raise CenterlineError("Hair profile metadata does not match its Curve points.")
    shaped_metadata = metadata.get("version", 0) >= 3
    normal_key = "shape_normal_local" if shaped_metadata else "normal_local"
    if baselines is None:
        root = Vector((0.0, 0.0, 1.0)).cross(tangents[0])
        if root.length <= EPSILON:
            root = Vector((-1.0, 0.0, 0.0))
        else:
            root.normalize()
        computed = [root]
        for previous_tangent, tangent in zip(tangents, tangents[1:]):
            computed.append(_transport_axis(computed[-1], previous_tangent, tangent))
        baselines = tuple(computed)
    tilts = []
    for section, tangent, baseline in zip(sections, tangents, baselines):
        target = _project_to_normal_plane(Vector(section[normal_key]), tangent)
        if target is None:
            target = baseline
        angle = _signed_angle_about_axis(baseline, target, tangent)
        if tilts:
            angle = _unwrap_angle_near(angle, tilts[-1])
        tilts.append(angle)
    return tuple(tilts)


def _curve_profile_solution(
    metadata,
    alignment,
    centered_coordinates=None,
    blend_factor=HAIR_BLEND_DEFAULT,
):
    """Resolve Curve points/radii/tilts without mutating Blender data."""

    sections = metadata.get("sections", ()) if metadata else ()
    if centered_coordinates is None:
        if metadata.get("version", 0) < 4:
            raise CenterlineError("Refresh this legacy centerline from its source first.")
        centered_coordinates = tuple(Vector(section["center_local"]) for section in sections)
    else:
        centered_coordinates = tuple(Vector(value) for value in centered_coordinates)
    if len(centered_coordinates) != len(sections):
        raise CenterlineError("Hair profile metadata does not match its center points.")

    bevel_depth, radii, _legacy_tilts = _curve_profile_parameters(metadata)
    placement_factor = _alignment_factor(alignment, blend_factor)

    coordinates = tuple(value.copy() for value in centered_coordinates)
    if placement_factor > 0.0:
        if metadata.get("version", 0) < 4:
            raise CenterlineError("Front Surface alignment needs refreshed v4 metadata.")
        targets = tuple(Vector(section["front_target_local"]) for section in sections)
        source_normals = []
        for section in sections:
            source_normal = _unit_vector_or_none(section["shape_normal_local"])
            if source_normal is None:
                raise CenterlineError("A source front plane has no usable normal.")
            source_normals.append(source_normal)
        source_normals = tuple(source_normals)
        front_coordinates = coordinates
        for _iteration in range(HAIR_FRONT_FLUSH_ITERATIONS):
            tangents = _center_tangents(front_coordinates)
            effective_normals = []
            for source_normal, tangent in zip(source_normals, tangents):
                effective_normal = _project_to_normal_plane(source_normal, tangent)
                if effective_normal is None:
                    raise CenterlineError(
                        "A source front plane is parallel to the Curve tangent."
                    )
                effective_normals.append(effective_normal)
            updated = tuple(
                center
                + source_normal
                * (
                    (target - center).dot(source_normal)
                    - bevel_depth
                    * radius
                    * effective_normal.dot(source_normal)
                )
                for center, target, source_normal, effective_normal, radius in zip(
                    centered_coordinates,
                    targets,
                    source_normals,
                    effective_normals,
                    radii,
                )
            )
            delta = max(
                (new - old).length
                for new, old in zip(updated, front_coordinates)
            )
            front_coordinates = updated
            if delta <= 1.0e-7:
                break
        coordinates = tuple(
            center + (front - center) * placement_factor
            for center, front in zip(centered_coordinates, front_coordinates)
        )

    tangents = _center_tangents(coordinates)
    tilts = _curve_profile_tilts(metadata, tangents)
    return coordinates, bevel_depth, radii, tilts


def _create_centerline_object(
    context,
    source_obj,
    layers,
    centers,
    metadata=None,
    *,
    align_front_surface=False,
    alignment=None,
    blend_factor=HAIR_BLEND_DEFAULT,
):
    curve_data = None
    curve_obj = None
    try:
        curve_data = bpy.data.curves.new(f"{source_obj.name}_Centerline", type="CURVE")
        curve_data.dimensions = "3D"
        curve_data.resolution_u = 1
        curve_data.render_resolution_u = 1
        curve_data.twist_mode = "MINIMUM"
        curve_data.twist_smooth = 0.0
        curve_data.fill_mode = "HALF"
        curve_data.bevel_mode = "ROUND"
        curve_data.bevel_resolution = HAIR_PROFILE_BEVEL_RESOLUTION
        curve_data.use_fill_caps = False
        curve_data.extrude = 0.0
        curve_data.taper_object = None

        if metadata is None:
            raise ValueError("Cross-section metadata is required for a shaped centerline.")
        if alignment is None:
            alignment = (
                HAIR_ALIGNMENT_FRONT_FLUSH
                if align_front_surface
                else HAIR_ALIGNMENT_CENTERED
            )
        elif alignment not in HAIR_ALIGNMENTS:
            raise ValueError(f"Unsupported centerline alignment: {alignment}.")
        placement_factor = _alignment_factor(alignment, blend_factor)
        point_coordinates, bevel_depth, point_radii, point_tilts = (
            _curve_profile_solution(
                metadata,
                alignment,
                centers,
                blend_factor=placement_factor,
            )
        )
        curve_data.bevel_depth = bevel_depth

        spline = curve_data.splines.new(type="POLY")
        spline.points.add(len(centers) - 1)
        for point, center, radius, tilt in zip(
            spline.points,
            point_coordinates,
            point_radii,
            point_tilts,
        ):
            point.co = (*center, 1.0)
            point.radius = radius
            point.tilt = tilt
        spline.use_cyclic_u = False

        curve_obj = bpy.data.objects.new(f"{source_obj.name}_Centerline", curve_data)
        _visible_source_collection(context, source_obj).objects.link(curve_obj)
        curve_obj.matrix_world = source_obj.matrix_world.copy()
        curve_obj.show_in_front = True
        curve_obj["character_designer_generator"] = GENERATOR_ID
        curve_obj["character_designer_source"] = source_obj.name
        curve_obj["character_designer_profile"] = "ROUND_HALF_WIDTH_AND_FRONT"
        curve_obj["character_designer_profile_depth_local"] = float(bevel_depth)
        curve_obj["character_designer_profile_tip_radius"] = float(
            metadata.get("profile_tip_radius", HAIR_PROFILE_TIP_RADIUS)
        )
        curve_obj["character_designer_alignment"] = alignment
        if alignment == HAIR_ALIGNMENT_BLEND:
            curve_obj[HAIR_BLEND_FACTOR_KEY] = placement_factor
        curve_obj["character_designer_layer_vertices"] = json.dumps(layers, separators=(",", ":"))
        if metadata is not None:
            if metadata.get("point_count") != len(centers):
                raise ValueError("Cross-section metadata does not match the Curve point count.")
            curve_obj["character_designer_metadata_version"] = METADATA_VERSION
            curve_obj["character_designer_cross_sections"] = json.dumps(
                metadata,
                separators=(",", ":"),
                allow_nan=False,
            )
        return curve_obj
    except Exception:
        if curve_obj is not None and curve_obj.name in bpy.data.objects:
            bpy.data.objects.remove(curve_obj, do_unlink=True)
        if curve_data is not None and curve_data.name in bpy.data.curves:
            bpy.data.curves.remove(curve_data)
        raise


def _remove_created_curve(curve_obj):
    if curve_obj is None or curve_obj.name not in bpy.data.objects:
        return
    curve_data = curve_obj.data if curve_obj.type == "CURVE" else None
    bpy.data.objects.remove(curve_obj, do_unlink=True)
    if curve_data is not None and curve_data.users == 0:
        bpy.data.curves.remove(curve_data)


def _is_character_designer_centerline(obj):
    return bool(
        obj is not None
        and obj.type == "CURVE"
        and obj.get("character_designer_generator") == GENERATOR_ID
        and _curve_cross_section_metadata(obj) is not None
    )


def _stored_centerline_layers(curve_obj):
    try:
        layers = json.loads(curve_obj.get("character_designer_layer_vertices", ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CenterlineError("This centerline has no readable source-layer record.") from exc
    if (
        not isinstance(layers, list)
        or len(layers) < 2
        or any(
            not isinstance(layer, list)
            or not layer
            or any(
                not isinstance(index, int) or isinstance(index, bool) or index < 0
                for index in layer
            )
            for layer in layers
        )
    ):
        raise CenterlineError("This centerline has an invalid source-layer record.")
    normalized = tuple(tuple(sorted(layer)) for layer in layers)
    flattened = tuple(index for layer in normalized for index in layer)
    if len(flattened) != len(set(flattened)):
        raise CenterlineError("The source-layer record contains repeated vertices.")
    metadata = _curve_cross_section_metadata(curve_obj)
    if metadata is not None and len(normalized) != metadata.get("point_count"):
        raise CenterlineError("The source-layer record does not match the Curve point count.")
    return normalized


def _recorded_centerline_source(curve_obj):
    source_name = curve_obj.get("character_designer_source")
    source_obj = bpy.data.objects.get(source_name) if isinstance(source_name, str) else None
    if source_obj is None or source_obj.type != "MESH":
        raise CenterlineError("The recorded source mesh could not be found.")
    return source_obj


def _metadata_from_recorded_source(curve_obj):
    """Re-read a stored strip without relying on selection or changing mode."""

    source_obj = _recorded_centerline_source(curve_obj)
    stored_layers = _stored_centerline_layers(curve_obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(source_obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        maximum_index = max(index for layer in stored_layers for index in layer)
        if maximum_index >= len(bm.verts):
            raise CenterlineError("The source topology changed; update from a new selection.")
        selected = {index for layer in stored_layers for index in layer}
        root_edges = _contained_edge_keys(bm, stored_layers[0])
        _source, _bm, layers, centers = _extract_layers_from_seed(
            source_obj,
            bm,
            selected,
            stored_layers[0],
            root_edges,
            selected_edge_keys=_contained_edge_keys(bm, selected),
        )
        if tuple(tuple(layer) for layer in layers) != stored_layers:
            raise CenterlineError("The source topology changed; update from a new selection.")
        regular_kind = _regular_kind_from_layers(bm, layers)
        metadata = _build_cross_section_metadata(
            source_obj,
            bm,
            layers,
            centers,
            regular_kind,
        )
        return source_obj, layers, centers, metadata
    finally:
        bm.free()


def _configure_centerline_data(curve_data, solution):
    coordinates, bevel_depth, radii, tilts = solution
    material_index = (
        int(curve_data.splines[0].material_index)
        if len(curve_data.splines) == 1
        else 0
    )
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 1
    curve_data.render_resolution_u = 1
    curve_data.twist_mode = "MINIMUM"
    curve_data.twist_smooth = 0.0
    curve_data.fill_mode = "HALF"
    curve_data.bevel_mode = "ROUND"
    curve_data.bevel_resolution = HAIR_PROFILE_BEVEL_RESOLUTION
    curve_data.use_fill_caps = False
    curve_data.extrude = 0.0
    curve_data.taper_object = None
    curve_data.bevel_depth = bevel_depth
    curve_data.splines.clear()
    spline = curve_data.splines.new(type="POLY")
    spline.material_index = material_index
    spline.points.add(len(coordinates) - 1)
    for point, coordinate, radius, tilt in zip(
        spline.points,
        coordinates,
        radii,
        tilts,
    ):
        point.co = (*coordinate, 1.0)
        point.radius = radius
        point.tilt = tilt
    spline.use_cyclic_u = False


def _centerline_solution_matches_data(curve_data, solution, tolerance=1.0e-6):
    """Compare real Curve geometry as well as its placement metadata."""

    coordinates, bevel_depth, radii, tilts = solution
    if len(curve_data.splines) != 1:
        return False
    spline = curve_data.splines[0]
    if spline.type != "POLY" or len(spline.points) != len(coordinates):
        return False
    if not math.isclose(
        float(curve_data.bevel_depth),
        float(bevel_depth),
        rel_tol=0.0,
        abs_tol=tolerance,
    ):
        return False
    for point, coordinate, radius, tilt in zip(
        spline.points,
        coordinates,
        radii,
        tilts,
    ):
        if (point.co.xyz - coordinate).length > tolerance:
            return False
        if not math.isclose(
            float(point.radius),
            float(radius),
            rel_tol=0.0,
            abs_tol=tolerance,
        ):
            return False
        if not math.isclose(
            float(point.tilt),
            float(tilt),
            rel_tol=0.0,
            abs_tol=tolerance,
        ):
            return False
    return True


def _restore_centerline_edit_geometry(curve_data, snapshot):
    bevel_depth, point_values = snapshot
    curve_data.bevel_depth = bevel_depth
    spline = curve_data.splines[0]
    for point, (coordinate, radius, tilt) in zip(spline.points, point_values):
        point.co = coordinate
        point.radius = radius
        point.tilt = tilt
    curve_data.update_tag()


def _configure_centerline_edit_geometry(curve_obj, solution):
    """Apply placement in Curve Edit Mode without replacing its edited data."""

    if bpy.context.mode != "EDIT_CURVE" or bpy.context.edit_object is not curve_obj:
        raise CenterlineError("The generated Curve is not the active Edit Mode object.")
    curve_data = curve_obj.data
    if curve_data.users != 1:
        raise CenterlineError("Make this Curve data single-user before editing its placement.")
    if getattr(curve_data, "shape_keys", None) is not None:
        raise CenterlineError("Exit Edit Mode before changing placement on a shaped Curve.")
    coordinates, bevel_depth, radii, tilts = solution
    if (
        len(curve_data.splines) != 1
        or curve_data.splines[0].type != "POLY"
        or len(curve_data.splines[0].points) != len(coordinates)
    ):
        raise CenterlineError(
            "The edited Curve topology no longer matches its generated centerline."
        )
    finite_values = (
        float(bevel_depth),
        *(float(value) for coordinate in coordinates for value in coordinate),
        *(float(value) for value in radii),
        *(float(value) for value in tilts),
    )
    if not all(math.isfinite(value) for value in finite_values):
        raise CenterlineError("Centerline placement produced non-finite geometry.")

    spline = curve_data.splines[0]
    snapshot = (
        float(curve_data.bevel_depth),
        tuple(
            (point.co.copy(), float(point.radius), float(point.tilt))
            for point in spline.points
        ),
    )
    try:
        curve_data.bevel_depth = float(bevel_depth)
        for point, coordinate, radius, tilt in zip(
            spline.points,
            coordinates,
            radii,
            tilts,
        ):
            point.co = (*coordinate, 1.0)
            point.radius = float(radius)
            point.tilt = float(tilt)
        curve_data.update_tag()
        curve_obj.update_tag()
        if not _centerline_solution_matches_data(curve_data, solution):
            raise CenterlineError("Blender did not apply the edited Curve placement.")
    except Exception:
        _restore_centerline_edit_geometry(curve_data, snapshot)
        curve_obj.update_tag()
        raise
    _tag_view3d_redraw()
    return snapshot


def _commit_existing_centerline(
    curve_obj,
    source_obj,
    layers,
    metadata,
    alignment,
    *,
    blend_factor=HAIR_BLEND_DEFAULT,
    update_matrix=False,
):
    """Atomically update Curve geometry while preserving its object identity."""

    if not _is_character_designer_centerline(curve_obj):
        raise CenterlineError("Select one valid Character Designer centerline.")
    if metadata.get("version") != METADATA_VERSION:
        raise CenterlineError("Updated centerline metadata must use the current version.")
    source_name = source_obj.name if source_obj is not None else curve_obj.get(
        "character_designer_source"
    )
    if not isinstance(source_name, str) or not source_name:
        raise CenterlineError("The centerline has no recorded source mesh.")

    placement_factor = _alignment_factor(alignment, blend_factor)
    solution = _curve_profile_solution(
        metadata,
        alignment,
        blend_factor=placement_factor,
    )
    metadata_json = json.dumps(metadata, separators=(",", ":"), allow_nan=False)
    layers_json = json.dumps(layers, separators=(",", ":"))
    if len(layers) != metadata.get("point_count"):
        raise CenterlineError("Source layers do not match the updated point count.")
    flattened = tuple(index for layer in layers for index in layer)
    if len(flattened) != len(set(flattened)):
        raise CenterlineError("Source layers contain repeated vertices.")
    matrix_is_current = bool(
        not update_matrix
        or source_obj is not None
        and all(
            abs(float(curve_obj.matrix_world[row][column]) - float(source_obj.matrix_world[row][column]))
            <= 1.0e-9
            for row in range(4)
            for column in range(4)
        )
    )
    stored_factor = None
    if alignment == HAIR_ALIGNMENT_BLEND:
        try:
            stored_factor = _stored_blend_factor(curve_obj)
        except CenterlineError:
            stored_factor = None
    factor_is_current = bool(
        alignment != HAIR_ALIGNMENT_BLEND
        or HAIR_BLEND_FACTOR_KEY in curve_obj
        and stored_factor is not None
        and math.isclose(stored_factor, placement_factor, rel_tol=0.0, abs_tol=1.0e-9)
    )
    edit_in_place = bool(
        bpy.context.mode == "EDIT_CURVE"
        and bpy.context.edit_object is curve_obj
    )
    if edit_in_place and update_matrix:
        raise CenterlineError("Exit Curve Edit Mode before refreshing its source transform.")
    if (
        curve_obj.get("character_designer_alignment") == alignment
        and factor_is_current
        and curve_obj.get("character_designer_metadata_version") == METADATA_VERSION
        and curve_obj.get("character_designer_cross_sections") == metadata_json
        and curve_obj.get("character_designer_layer_vertices") == layers_json
        and matrix_is_current
        and _centerline_solution_matches_data(curve_obj.data, solution)
    ):
        return False
    old_matrix = curve_obj.matrix_world.copy()
    property_keys = (
        "character_designer_source",
        "character_designer_profile",
        "character_designer_profile_depth_local",
        "character_designer_profile_tip_radius",
        "character_designer_layer_vertices",
        "character_designer_metadata_version",
        "character_designer_cross_sections",
        "character_designer_alignment",
        HAIR_BLEND_FACTOR_KEY,
    )
    old_properties = {
        key: (key in curve_obj, curve_obj.get(key)) for key in property_keys
    }

    def write_properties(curve_data):
        curve_obj["character_designer_source"] = source_name
        curve_obj["character_designer_profile"] = "ROUND_HALF_WIDTH_AND_FRONT"
        curve_obj["character_designer_profile_depth_local"] = float(
            curve_data.bevel_depth
        )
        curve_obj["character_designer_profile_tip_radius"] = float(
            metadata.get("profile_tip_radius", HAIR_PROFILE_TIP_RADIUS)
        )
        curve_obj["character_designer_layer_vertices"] = layers_json
        curve_obj["character_designer_metadata_version"] = METADATA_VERSION
        curve_obj["character_designer_cross_sections"] = metadata_json
        curve_obj["character_designer_alignment"] = alignment
        if alignment == HAIR_ALIGNMENT_BLEND:
            curve_obj[HAIR_BLEND_FACTOR_KEY] = placement_factor

    def restore_properties():
        for key, (existed, value) in old_properties.items():
            if existed:
                curve_obj[key] = value
            elif key in curve_obj:
                del curve_obj[key]

    if edit_in_place:
        edit_snapshot = None
        try:
            edit_snapshot = _configure_centerline_edit_geometry(curve_obj, solution)
            write_properties(curve_obj.data)
            curve_obj.update_tag()
        except Exception:
            if edit_snapshot is not None:
                _restore_centerline_edit_geometry(curve_obj.data, edit_snapshot)
            restore_properties()
            curve_obj.update_tag()
            _tag_view3d_redraw()
            raise
        _tag_view3d_redraw()
        return True

    old_data = curve_obj.data
    new_data = old_data.copy()
    try:
        _configure_centerline_data(new_data, solution)
        curve_obj.data = new_data
        if curve_obj.data is not new_data:
            raise CenterlineError("Blender did not replace the Curve data in the current mode.")
        if update_matrix:
            if source_obj is None:
                raise CenterlineError("The recorded source mesh could not be found.")
            curve_obj.matrix_world = source_obj.matrix_world.copy()
        write_properties(new_data)
    except Exception:
        if curve_obj.data is new_data:
            curve_obj.data = old_data
        curve_obj.matrix_world = old_matrix
        restore_properties()
        if new_data.users == 0:
            bpy.data.curves.remove(new_data)
        raise
    if old_data.users == 0:
        bpy.data.curves.remove(old_data)
    return True


def _alignment_label(alignment):
    return {
        HAIR_ALIGNMENT_CENTERED: "Centered",
        HAIR_ALIGNMENT_BLEND: "Blend",
        HAIR_ALIGNMENT_FRONT_FLUSH: "Surface",
    }.get(alignment, "Unknown")


def _scene_centerlines_for_source(context, source_obj):
    related = tuple(
        obj
        for obj in context.scene.objects
        if obj.type == "CURVE"
        and obj.get("character_designer_generator") == GENERATOR_ID
        and obj.get("character_designer_source") == source_obj.name
    )
    invalid = tuple(obj for obj in related if not _is_character_designer_centerline(obj))
    if invalid:
        names = ", ".join(sorted(obj.name for obj in invalid))
        raise CenterlineError(
            f"Existing centerline data is unreadable ({names}); nothing was changed."
        )
    return related


def _automatic_centerline_target(context, source_obj, layers):
    """Resolve a safe in-scene overwrite target without exposing target state."""

    candidates = _scene_centerlines_for_source(context, source_obj)
    if not candidates:
        return None

    signature = tuple(tuple(sorted(layer)) for layer in layers)
    reversed_signature = tuple(reversed(signature))
    exact = []
    for candidate in candidates:
        try:
            candidate_signature = _stored_centerline_layers(candidate)
        except CenterlineError as exc:
            raise CenterlineError(
                f"{candidate.name} has an unreadable source record; nothing was changed."
            ) from exc
        if candidate_signature in {signature, reversed_signature}:
            exact.append(candidate)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise CenterlineError(
            "More than one in-scene centerline matches these loops; nothing was changed."
        )

    raise CenterlineError(
        "Existing centerline loops do not match this selection; nothing was changed."
    )


def _canonical_recovery_layers(layers):
    forward = tuple(tuple(sorted(int(index) for index in layer)) for layer in layers)
    reverse = tuple(reversed(forward))
    return min(forward, reverse)


def _recovery_component_signature(layers):
    payload = json.dumps(
        _canonical_recovery_layers(layers),
        separators=(",", ":"),
    ).encode("ascii")
    return "v1:" + hashlib.sha256(payload).hexdigest()


_RECOVERY_MODIFIER_EXCLUDED_PROPERTIES = frozenset(
    {
        "rna_type",
        "type",
        "name",
        "execution_time",
        "persistent_uid",
        "is_active",
        # Expanded/pinned state is editor presentation, not evaluated geometry.
        "show_expanded",
        "use_pin_to_last",
    }
)
_RECOVERY_MODIFIER_PROPERTY_TYPES = frozenset(
    {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM", "POINTER"}
)


def _capture_recovery_modifier_value(prop, value, source_obj):
    """Copy one supported RNA value without retaining an RNA array wrapper."""

    if prop.type == "POINTER":
        # A Mirror Object that points to its own source means that Object's
        # origin.  The source is removed by Replace, so None is the equivalent
        # and durable representation on the recovered Curve.
        if value is source_obj:
            value = None
        if value is not None and not isinstance(value, bpy.types.ID):
            raise CenterlineError(
                f"Modifier property {prop.identifier} uses an unsupported pointer."
            )
        return "POINTER", value
    if getattr(prop, "is_array", False):
        return "ARRAY", tuple(value)
    if isinstance(value, set):
        return "SET", tuple(sorted(value))
    if isinstance(value, (bool, int, float, str)):
        return "SCALAR", value
    raise CenterlineError(
        f"Modifier property {prop.identifier} cannot be preserved safely."
    )


def _capture_recovery_modifier_stack(obj, *, source_obj=None):
    """Capture a Curve-compatible Mirror/Subdivision stack in source order."""

    specs = []
    unsupported = tuple(
        modifier
        for modifier in obj.modifiers
        if modifier.type not in RECOVERY_SUPPORTED_MODIFIER_TYPES
    )
    if unsupported:
        names = ", ".join(
            f"{modifier.name} ({modifier.type.replace('_', ' ').title()})"
            for modifier in unsupported
        )
        raise CenterlineError(
            "Recover Applied Curve currently preserves only Mirror and "
            f"Subdivision modifiers; unsupported: {names}."
        )

    for modifier in obj.modifiers:
        properties = []
        for prop in modifier.bl_rna.properties:
            identifier = prop.identifier
            if (
                identifier in _RECOVERY_MODIFIER_EXCLUDED_PROPERTIES
                or prop.is_readonly
                or prop.type not in _RECOVERY_MODIFIER_PROPERTY_TYPES
            ):
                continue
            try:
                kind, value = _capture_recovery_modifier_value(
                    prop,
                    getattr(modifier, identifier),
                    source_obj,
                )
            except (AttributeError, ReferenceError) as exc:
                raise CenterlineError(
                    f"Could not read {modifier.name}.{identifier}: {exc}"
                ) from exc
            properties.append(
                {
                    "identifier": identifier,
                    "kind": kind,
                    "value": value,
                }
            )
        specs.append(
            {
                "type": modifier.type,
                "name": modifier.name,
                "properties": tuple(properties),
            }
        )
    return tuple(specs)


def _recovery_modifier_json_value(value):
    if value is None:
        return None
    if isinstance(value, bpy.types.ID):
        library = getattr(value, "library", None)
        return {
            "id_type": value.bl_rna.identifier,
            "name": value.name_full,
            "library": library.filepath if library is not None else "",
        }
    if isinstance(value, tuple):
        return tuple(_recovery_modifier_json_value(item) for item in value)
    if isinstance(value, (bool, int, float, str)):
        return value
    raise CenterlineError("A captured modifier value is not serializable.")


def _recovery_modifier_stack_json(specs):
    payload = []
    for spec in specs:
        payload.append(
            {
                "type": spec["type"],
                "name": spec["name"],
                "properties": [
                    {
                        "identifier": prop["identifier"],
                        "kind": prop["kind"],
                        "value": _recovery_modifier_json_value(prop["value"]),
                    }
                    for prop in spec["properties"]
                ],
            }
        )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _recovery_modifier_ownership_json(specs):
    return json.dumps(
        tuple((spec["name"], spec["type"]) for spec in specs),
        separators=(",", ":"),
    )


def _recovery_modifier_stack_label(specs):
    labels = []
    for spec in specs:
        if spec["type"] == "MIRROR":
            labels.append("Mirror")
            continue
        subdivision_type = next(
            (
                prop["value"]
                for prop in spec["properties"]
                if prop["identifier"] == "subdivision_type"
            ),
            "CATMULL_CLARK",
        )
        labels.append(
            "Subdivision (Simple)"
            if subdivision_type == "SIMPLE"
            else "Subdivision"
        )
    return " \u2192 ".join(labels)


def _apply_recovery_modifier_value(modifier, prop):
    value = prop["value"]
    if prop["kind"] == "SET":
        value = set(value)
    elif prop["kind"] == "ARRAY":
        value = tuple(value)
    setattr(modifier, prop["identifier"], value)


def _replace_recovery_modifier_stack(obj, specs):
    """Replace only a preflighted, plugin-owned modifier stack."""

    for modifier in tuple(obj.modifiers):
        obj.modifiers.remove(modifier)
    for spec in specs:
        try:
            modifier = obj.modifiers.new(name=spec["name"], type=spec["type"])
            for prop in spec["properties"]:
                _apply_recovery_modifier_value(modifier, prop)
        except Exception as exc:
            raise CenterlineError(
                f"Could not preserve {spec['name']} on the recovered Curve: {exc}"
            ) from exc


def _validate_recovery_modifier_ownership(target):
    """Refuse to remove modifier entries that may have been added by the artist."""

    stored = target.get(RECOVERY_MODIFIER_OWNERSHIP_KEY)
    actual = tuple((modifier.name, modifier.type) for modifier in target.modifiers)
    if stored is None:
        if actual:
            raise CenterlineError(
                f"{target.name} has unowned modifiers; nothing was overwritten."
            )
        return
    if not isinstance(stored, str):
        raise CenterlineError(
            f"{target.name} modifier ownership data is unreadable; nothing was overwritten."
        )
    try:
        expected = tuple(tuple(item) for item in json.loads(stored))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CenterlineError(
            f"{target.name} modifier ownership data is unreadable; nothing was overwritten."
        ) from exc
    if actual != expected:
        raise CenterlineError(
            f"{target.name} modifier stack was edited; nothing was overwritten."
        )


def _layer_selected_adjacency(bm, layer, selected_edge_keys):
    layer_set = set(layer)
    edge_keys = tuple(
        edge_key
        for edge_key in selected_edge_keys
        if edge_key[0] in layer_set and edge_key[1] in layer_set
    )
    if not edge_keys:
        edge_keys = _contained_edge_keys(bm, layer_set)
    return _adjacency_from_edge_keys(layer_set, edge_keys)


def _recovery_profile_tolerance(scale):
    return max(
        RECOVERY_PROFILE_ABSOLUTE_TOLERANCE,
        abs(float(scale)) * RECOVERY_PROFILE_RELATIVE_TOLERANCE,
    )


def _open_half_round_section(
    bm,
    layer,
    tangent,
    selected_edge_keys,
):
    adjacency = _layer_selected_adjacency(bm, layer, selected_edge_keys)
    if _classify_slice(layer, adjacency, label="An applied open profile") != "OPEN":
        raise CenterlineError("An open recovery layer is not one simple profile row.")
    endpoints = tuple(sorted(index for index in layer if len(adjacency[index]) == 1))
    if len(endpoints) != 2 or len(layer) < 4:
        raise CenterlineError(
            "An applied Half Round profile needs at least four vertices per cross-section."
        )
    first = bm.verts[endpoints[0]].co
    second = bm.verts[endpoints[1]].co
    origin = (first + second) * 0.5
    width_axis = _unit_vector_or_none(second - first)
    if width_axis is None:
        raise CenterlineError("An applied profile endpoint chord has no usable direction.")

    interior = tuple(index for index in layer if index not in endpoints)
    back_hint = sum(
        (bm.verts[index].co - origin for index in interior),
        Vector((0.0, 0.0, 0.0)),
    ) / len(interior)
    back_axis = back_hint - width_axis * back_hint.dot(width_axis)
    back_axis = _unit_vector_or_none(back_axis)
    if back_axis is None:
        raise CenterlineError("The applied open profile has no recoverable depth direction.")
    profile_tangent = _unit_vector_or_none(width_axis.cross(back_axis))
    if profile_tangent is None:
        raise CenterlineError("The applied open profile has no stable cross-section plane.")
    if profile_tangent.dot(tangent) < 0.0:
        profile_tangent.negate()

    coordinates = []
    tangent_deviation = 0.0
    for index in layer:
        delta = bm.verts[index].co - origin
        tangent_deviation = max(tangent_deviation, abs(delta.dot(profile_tangent)))
        coordinates.append((delta.dot(back_axis), delta.dot(width_axis)))
    depth = max(value[0] for value in coordinates)
    minimum_depth = min(value[0] for value in coordinates)
    half_width = max(abs(value[1]) for value in coordinates)
    scale = max(depth, half_width)
    tolerance = _recovery_profile_tolerance(scale)
    if depth <= tolerance or half_width + tolerance < depth or minimum_depth < -tolerance:
        raise CenterlineError("The selected open profile is not a recoverable Half Round shape.")
    if tangent_deviation > tolerance:
        raise CenterlineError("An applied profile is too skewed across its Curve tangent.")
    local_extrude = max(0.0, half_width - depth)
    for depth_value, width_value in coordinates:
        width_value = abs(width_value)
        if width_value <= local_extrude + tolerance:
            residual = abs(depth_value - depth)
        else:
            residual = abs(
                math.hypot(depth_value, width_value - local_extrude) - depth
            )
        if residual > tolerance:
            raise CenterlineError(
                "The selected open profile is not one consistent Half Round + Extrude shape."
            )
    return {
        "coordinate": origin.copy(),
        "axis": back_axis.copy(),
        "profile_tangent": profile_tangent.copy(),
        "depth": float(depth),
        "half_width": float(half_width),
        "vertex_count": len(layer),
    }


def _closed_profile_section(bm, layer, tangent, metadata_section):
    if len(layer) < 4:
        raise CenterlineError("A closed applied profile needs at least four vertices.")
    origin = sum(
        (bm.verts[index].co for index in layer),
        Vector((0.0, 0.0, 0.0)),
    ) / len(layer)
    profile_tangent = _closed_layer_normal(bm, layer)
    if profile_tangent is None:
        raise CenterlineError("The closed profile has no stable plane normal.")
    if profile_tangent.dot(tangent) < 0.0:
        profile_tangent.negate()
    hint = _project_to_normal_plane(
        Vector(metadata_section["shape_normal_local"]),
        profile_tangent,
    )
    if hint is None:
        hint = _deterministic_perpendicular(profile_tangent)
    layer_set = set(layer)
    boundary_edges = tuple(
        edge
        for index in layer
        for edge in bm.verts[index].link_edges
        if edge.verts[0].index in layer_set and edge.verts[1].index in layer_set
    )
    boundary_edges = tuple(set(boundary_edges))
    if not boundary_edges:
        raise CenterlineError("The closed profile has no boundary edges.")
    longest_edge = max(boundary_edges, key=lambda edge: edge.calc_length())
    long_axis = _project_to_normal_plane(
        longest_edge.verts[1].co - longest_edge.verts[0].co,
        profile_tangent,
    )
    if long_axis is None:
        raise CenterlineError("The closed profile boundary has no usable direction.")
    short_axis = _unit_vector_or_none(profile_tangent.cross(long_axis))
    if short_axis is None:
        raise CenterlineError("The closed profile has no stable orientation frame.")
    if short_axis.dot(hint) < 0.0:
        short_axis.negate()
        long_axis.negate()

    short_extent = max(
        abs((bm.verts[index].co - origin).dot(short_axis)) for index in layer
    )
    long_extent = max(
        abs((bm.verts[index].co - origin).dot(long_axis)) for index in layer
    )
    if short_extent > long_extent:
        short_axis, long_axis = long_axis, short_axis
        short_extent, long_extent = long_extent, short_extent
    depth = short_extent
    half_width = long_extent
    tolerance = _recovery_profile_tolerance(max(depth, half_width))
    if depth <= tolerance or half_width + tolerance < depth:
        raise CenterlineError("The closed applied profile has no usable width and depth.")

    coordinates = []
    tangent_deviation = 0.0
    for index in layer:
        delta = bm.verts[index].co - origin
        tangent_deviation = max(tangent_deviation, abs(delta.dot(tangent)))
        coordinates.append((delta.dot(short_axis), delta.dot(long_axis)))
    if tangent_deviation > tolerance:
        raise CenterlineError("A closed applied profile is too skewed across its tangent.")

    rectangle_matches = all(
        min(
            abs(abs(short_value) - depth),
            abs(abs(long_value) - half_width),
        )
        <= tolerance
        for short_value, long_value in coordinates
    )
    if rectangle_matches:
        profile_kind = "RECTANGLE"
    else:
        local_extrude = max(0.0, half_width - depth)
        for short_value, long_value in coordinates:
            long_value = abs(long_value)
            if long_value <= local_extrude + tolerance:
                residual = abs(abs(short_value) - depth)
            else:
                residual = abs(
                    math.hypot(short_value, long_value - local_extrude) - depth
                )
            if residual > tolerance:
                raise CenterlineError(
                    "The closed profile is neither a rectangle nor a Round + Extrude capsule."
                )
        profile_kind = "ROUND_FULL"
    return {
        "coordinate": origin.copy(),
        "axis": short_axis.copy(),
        "profile_tangent": profile_tangent.copy(),
        "depth": float(depth),
        "half_width": float(half_width),
        "vertex_count": len(layer),
        "profile_kind": profile_kind,
    }


def _transport_recovery_axes(coordinates, axes):
    tangents = _center_tangents(coordinates)
    axes = list(axes)
    regular_indices = [index for index, axis in enumerate(axes) if axis is not None]
    if not regular_indices:
        raise CenterlineError("The applied profile has no regular cross-section.")
    for index, axis in enumerate(axes):
        if axis is not None:
            projected = _project_to_normal_plane(axis, tangents[index])
            if projected is None:
                raise CenterlineError("A recovered profile axis is parallel to its path.")
            axes[index] = projected
            continue
        nearest = min(regular_indices, key=lambda candidate: abs(candidate - index))
        transported = axes[nearest].copy()
        if nearest < index:
            for step in range(nearest + 1, index + 1):
                transported = _transport_axis(
                    transported,
                    tangents[step - 1],
                    tangents[step],
                )
        else:
            for step in range(nearest - 1, index - 1, -1):
                transported = _transport_axis(
                    transported,
                    tangents[step + 1],
                    tangents[step],
                )
        axes[index] = transported
    _tangents, baselines = _minimum_twist_baselines(
        tuple({"tangent_local": tangent} for tangent in tangents)
    )
    tilts = []
    for axis, tangent, baseline in zip(axes, tangents, baselines):
        target = _project_to_normal_plane(axis, tangent)
        if target is None:
            target = baseline
        angle = _signed_angle_about_axis(baseline, target, tangent)
        if tilts:
            angle = _unwrap_angle_near(angle, tilts[-1])
        tilts.append(angle)
    return tuple(tangents), tuple(axes), tuple(tilts)


def _recovery_offset_objective(coordinates, axes, radii, profile_tangents, offset):
    candidate_coordinates = tuple(
        coordinate - axis * float(offset) * float(radius)
        for coordinate, axis, radius in zip(coordinates, axes, radii)
    )
    tangents = _center_tangents(candidate_coordinates)
    residuals = [
        1.0 - abs(float(tangent.dot(profile_tangent)))
        for tangent, profile_tangent in zip(tangents, profile_tangents)
        if profile_tangent is not None
    ]
    if len(residuals) < 3:
        return 0.0
    return sum(value * value for value in residuals) / len(residuals)


def _infer_equivalent_recovery_offset(
    coordinates,
    axes,
    radii,
    profile_tangents,
    profile_scale,
):
    """Choose the offset whose candidate path is most normal to source sections."""

    if len(coordinates) < 3 or profile_scale <= EPSILON:
        return 0.0
    extent = max(profile_scale * 3.0, RECOVERY_PROFILE_ABSOLUTE_TOLERANCE)
    sample_count = 81
    step = extent * 2.0 / (sample_count - 1)
    samples = tuple(-extent + index * step for index in range(sample_count))
    objective = lambda value: _recovery_offset_objective(
        coordinates,
        axes,
        radii,
        profile_tangents,
        value,
    )
    values = tuple(objective(value) for value in samples)
    best_index = min(range(sample_count), key=lambda index: values[index])
    zero_value = objective(0.0)
    if zero_value - values[best_index] <= max(1.0e-10, zero_value * 1.0e-4):
        return 0.0

    left = samples[max(0, best_index - 1)]
    right = samples[min(sample_count - 1, best_index + 1)]
    golden = (math.sqrt(5.0) - 1.0) * 0.5
    first = right - golden * (right - left)
    second = left + golden * (right - left)
    first_value = objective(first)
    second_value = objective(second)
    for _iteration in range(36):
        if first_value <= second_value:
            right = second
            second = first
            second_value = first_value
            first = right - golden * (right - left)
            first_value = objective(first)
        else:
            left = first
            first = second
            first_value = second_value
            second = left + golden * (right - left)
            second_value = objective(second)
    candidate = (left + right) * 0.5
    if abs(candidate) <= profile_scale * 1.0e-6:
        return 0.0
    return float(candidate)


def _consistent_recovery_extrude_ratio(measures):
    ratios = tuple(
        max(0.0, measure["half_width"] / measure["depth"] - 1.0)
        for measure in measures
    )
    ordered = sorted(ratios)
    median = ordered[len(ordered) // 2]
    tolerance = max(0.035, abs(median) * RECOVERY_PROFILE_RELATIVE_TOLERANCE)
    if max(abs(value - median) for value in ratios) > tolerance:
        raise CenterlineError(
            "The applied profile changes width/depth ratio; one Curve Radius cannot represent it exactly."
        )
    return sum(ratios) / len(ratios)


def _infer_recovery_bevel_resolution(profile_kind, measures, extrude_ratio):
    counts = {measure["vertex_count"] for measure in measures}
    if len(counts) != 1:
        raise CenterlineError("Applied profile resolution changes along this strand.")
    count = next(iter(counts))
    if profile_kind == "HALF_ROUND":
        if count < 4 or count % 2:
            raise CenterlineError("Half Round profile vertex count is not supported.")
        resolution = count // 2 - 2
        if 2 * (resolution + 2) != count:
            raise CenterlineError("Half Round profile resolution could not be recovered.")
        return max(0, resolution)
    if profile_kind == "ROUND_FULL":
        if extrude_ratio <= RECOVERY_PROFILE_RELATIVE_TOLERANCE:
            if count < 4 or count % 2:
                raise CenterlineError("Round profile vertex count is not supported.")
            resolution = count // 2 - 2
        else:
            if count < 6 or (count - 2) % 4:
                raise CenterlineError("Round + Extrude vertex count is not supported.")
            resolution = (count - 2) // 4 - 1
        if resolution < 0:
            raise CenterlineError("Round profile resolution could not be recovered.")
        return resolution
    if profile_kind == "RECTANGLE":
        if count < 6 or (count - 2) % 4:
            raise CenterlineError("Rectangle Curve Profile resolution is not supported.")
        resolution = (count - 2) // 4 - 1
        if resolution < 0:
            raise CenterlineError("Rectangle profile resolution could not be recovered.")
        return resolution
    return 0


def _recovery_profile_solution(bm, record, metadata):
    layers = record["layers"]
    regular_kind = record["regular_kind"]
    selected_edge_keys = record["selected_edge_keys"]
    arithmetic_centers = tuple(Vector(value) for value in record["centers"])

    preliminary_coordinates = []
    if regular_kind == "OPEN":
        for layer, center in zip(layers, arithmetic_centers):
            if len(layer) == 1:
                preliminary_coordinates.append(center.copy())
                continue
            adjacency = _layer_selected_adjacency(bm, layer, selected_edge_keys)
            endpoints = tuple(index for index in layer if len(adjacency[index]) == 1)
            if len(endpoints) != 2:
                raise CenterlineError("An open applied profile has no two stable endpoints.")
            preliminary_coordinates.append(
                (bm.verts[endpoints[0]].co + bm.verts[endpoints[1]].co) * 0.5
            )
    else:
        preliminary_coordinates = [value.copy() for value in arithmetic_centers]
    preliminary_coordinates = tuple(preliminary_coordinates)
    tangents = _center_tangents(preliminary_coordinates)

    measures = []
    axes = []
    profile_tangents = []
    profile_kind = None
    measure_by_index = {}
    for index, (layer, tangent, section) in enumerate(
        zip(layers, tangents, metadata["sections"])
    ):
        if len(layer) == 1:
            axes.append(None)
            profile_tangents.append(None)
            continue
        if regular_kind == "OPEN":
            measure = _open_half_round_section(
                bm,
                layer,
                tangent,
                selected_edge_keys,
            )
            current_kind = "HALF_ROUND"
        else:
            measure = _closed_profile_section(bm, layer, tangent, section)
            current_kind = measure["profile_kind"]
        if profile_kind is None:
            profile_kind = current_kind
        elif profile_kind != current_kind:
            raise CenterlineError("The applied profile type changes along this strand.")
        preliminary_coordinates = tuple(
            measure["coordinate"] if position == index else value
            for position, value in enumerate(preliminary_coordinates)
        )
        measures.append(measure)
        measure_by_index[index] = measure
        axes.append(measure["axis"])
        profile_tangents.append(measure.get("profile_tangent"))

    if profile_kind is None or not measures:
        raise CenterlineError("The applied strand has no recoverable regular profile.")
    extrude_ratio = _consistent_recovery_extrude_ratio(measures)
    base_depth = max(measure["depth"] for measure in measures)
    if base_depth <= EPSILON:
        raise CenterlineError("The applied strand has no recoverable profile depth.")
    base_extrude = base_depth * extrude_ratio
    radii = tuple(
        0.0
        if index not in measure_by_index
        else measure_by_index[index]["depth"] / base_depth
        for index in range(len(layers))
    )
    observed_coordinates = tuple(preliminary_coordinates)
    _observed_tangents, transported_axes, _observed_tilts = _transport_recovery_axes(
        observed_coordinates,
        axes,
    )
    equivalent_offset = (
        _infer_equivalent_recovery_offset(
            observed_coordinates,
            transported_axes,
            radii,
            tuple(profile_tangents),
            max(measure["half_width"] for measure in measures),
        )
        if profile_kind == "HALF_ROUND"
        else 0.0
    )
    coordinates = tuple(
        coordinate - axis * equivalent_offset * radius
        for coordinate, axis, radius in zip(
            observed_coordinates,
            transported_axes,
            radii,
        )
    )
    _tangents, _axes, tilts = _transport_recovery_axes(coordinates, axes)
    bevel_resolution = _infer_recovery_bevel_resolution(
        profile_kind,
        measures,
        extrude_ratio,
    )
    return {
        "coordinates": coordinates,
        "radii": radii,
        "tilts": tilts,
        "bevel_depth": float(base_depth),
        "extrude": float(base_extrude),
        "offset": float(equivalent_offset),
        "profile_kind": profile_kind,
        "bevel_mode": "PROFILE" if profile_kind == "RECTANGLE" else "ROUND",
        "fill_mode": "FULL" if profile_kind != "HALF_ROUND" else "HALF",
        "bevel_resolution": int(bevel_resolution),
    }


def _resample_recovery_profile(profile, point_count):
    """Resample the recovered control cage uniformly by centerline arc length."""

    if (
        not isinstance(point_count, int)
        or isinstance(point_count, bool)
        or point_count < RECOVERY_CONTROL_POINTS_MIN
        or point_count > RECOVERY_CONTROL_POINTS_MAX
    ):
        raise CenterlineError(
            f"Bezier control points must be between {RECOVERY_CONTROL_POINTS_MIN} "
            f"and {RECOVERY_CONTROL_POINTS_MAX}."
        )
    coordinates = tuple(Vector(value) for value in profile["coordinates"])
    radii = tuple(float(value) for value in profile["radii"])
    source_tilts = tuple(float(value) for value in profile["tilts"])
    if (
        len(coordinates) < 2
        or len(radii) != len(coordinates)
        or len(source_tilts) != len(coordinates)
    ):
        raise CenterlineError("The recovered control path is incomplete.")

    tilts = [source_tilts[0]]
    for value in source_tilts[1:]:
        tilts.append(_unwrap_angle_near(value, tilts[-1]))
    cumulative_lengths = [0.0]
    for first, second in zip(coordinates, coordinates[1:]):
        cumulative_lengths.append(cumulative_lengths[-1] + (second - first).length)
    total_length = cumulative_lengths[-1]
    if total_length <= EPSILON:
        raise CenterlineError("The recovered strand is too short for a Bezier control cage.")

    sampled_coordinates = []
    sampled_radii = []
    sampled_tilts = []
    segment_index = 0
    for index in range(point_count):
        target_length = total_length * index / (point_count - 1)
        while (
            segment_index < len(coordinates) - 2
            and cumulative_lengths[segment_index + 1] < target_length - EPSILON
        ):
            segment_index += 1
        segment_start = cumulative_lengths[segment_index]
        segment_end = cumulative_lengths[segment_index + 1]
        segment_length = segment_end - segment_start
        factor = (
            0.0
            if segment_length <= EPSILON
            else max(0.0, min(1.0, (target_length - segment_start) / segment_length))
        )
        sampled_coordinates.append(
            coordinates[segment_index].lerp(coordinates[segment_index + 1], factor)
        )
        sampled_radii.append(
            radii[segment_index]
            + (radii[segment_index + 1] - radii[segment_index]) * factor
        )
        sampled_tilts.append(
            tilts[segment_index]
            + (tilts[segment_index + 1] - tilts[segment_index]) * factor
        )

    result = dict(profile)
    result["coordinates"] = tuple(sampled_coordinates)
    result["radii"] = tuple(sampled_radii)
    result["tilts"] = tuple(sampled_tilts)
    return result


def _recovery_output_profile(profile, curve_mode, control_points):
    if curve_mode == RECOVERY_CURVE_MODE_EXACT:
        return profile
    if curve_mode == RECOVERY_CURVE_MODE_BEZIER:
        return _resample_recovery_profile(profile, control_points)
    raise CenterlineError("Choose Exact or Bezier recovery mode.")


def _recovery_layers_json(layers):
    return json.dumps(layers, separators=(",", ":"))


def _recovery_metadata_json(metadata):
    return json.dumps(metadata, separators=(",", ":"), allow_nan=False)


def _recovery_managed_values(plan):
    profile = plan["profile"]
    return {
        "character_designer_generator": RECOVERY_GENERATOR_ID,
        "character_designer_source": plan["source_obj"].name,
        "character_designer_profile": f"RECOVERED_{profile['profile_kind']}",
        "character_designer_profile_depth_local": float(profile["bevel_depth"]),
        "character_designer_profile_extrude_local": float(profile["extrude"]),
        "character_designer_profile_offset_local": float(profile["offset"]),
        "character_designer_layer_vertices": plan["layers_json"],
        "character_designer_metadata_version": METADATA_VERSION,
        "character_designer_cross_sections": plan["metadata_json"],
        RECOVERY_SIGNATURE_KEY: plan["signature"],
        RECOVERY_PROFILE_KEY: profile["profile_kind"],
        RECOVERY_SOURCE_OBJECT_KEY: plan["source_obj"],
        RECOVERY_SOURCE_REPLACED_KEY: False,
        RECOVERY_CURVE_MODE_KEY: plan["curve_mode"],
        RECOVERY_CONTROL_POINTS_KEY: int(plan["control_points"]),
        RECOVERY_MODIFIER_STACK_KEY: plan["modifier_stack_json"],
        RECOVERY_MODIFIER_OWNERSHIP_KEY: plan["modifier_ownership_json"],
    }


RECOVERY_MANAGED_KEYS = (
    "character_designer_generator",
    "character_designer_source",
    "character_designer_profile",
    "character_designer_profile_depth_local",
    "character_designer_profile_extrude_local",
    "character_designer_profile_offset_local",
    "character_designer_layer_vertices",
    "character_designer_metadata_version",
    "character_designer_cross_sections",
    RECOVERY_SIGNATURE_KEY,
    RECOVERY_PROFILE_KEY,
    RECOVERY_SOURCE_OBJECT_KEY,
    RECOVERY_SOURCE_REPLACED_KEY,
    RECOVERY_CURVE_MODE_KEY,
    RECOVERY_CONTROL_POINTS_KEY,
    RECOVERY_MODIFIER_STACK_KEY,
    RECOVERY_MODIFIER_OWNERSHIP_KEY,
)


def _write_recovery_properties(curve_obj, plan):
    for key, value in _recovery_managed_values(plan).items():
        curve_obj[key] = value


def _configure_rectangle_bevel_profile(curve_data):
    curve_data.bevel_mode = "PROFILE"
    profile = curve_data.bevel_profile
    while len(profile.points) > 2:
        profile.points.remove(profile.points[1])
    profile.points[0].location = (1.0, 0.0)
    profile.points[-1].location = (0.0, 1.0)
    profile.points[0].handle_type_1 = "AUTO"
    profile.points[0].handle_type_2 = "AUTO"
    profile.points[-1].handle_type_1 = "AUTO"
    profile.points[-1].handle_type_2 = "AUTO"
    corner = profile.points.add(1.0, 1.0)
    corner.handle_type_1 = "VECTOR"
    corner.handle_type_2 = "VECTOR"
    profile.update()


def _recovery_aligned_handles(coordinates):
    """Return deterministic, low-overshoot ALIGNED handles for one open cage."""

    points = tuple(Vector(coordinate) for coordinate in coordinates)
    if len(points) < 2:
        raise CenterlineError("A Bezier recovery needs at least two distinct points.")
    spans = tuple(
        (second - first).length for first, second in zip(points, points[1:])
    )
    if any(not math.isfinite(span) or span <= EPSILON for span in spans):
        raise CenterlineError(
            "The recovered Bezier cage contains adjacent duplicate points."
        )
    directions = tuple(
        (second - first) / span
        for first, second, span in zip(points, points[1:], spans)
    )
    handles = []
    last_index = len(points) - 1
    for index, point in enumerate(points):
        if index == 0:
            axis = directions[0]
            left_span = right_span = spans[0]
        elif index == last_index:
            axis = directions[-1]
            left_span = right_span = spans[-1]
        else:
            # Do not normalize this bisector. Its cos(turn/2) magnitude shortens
            # handles through sharper bends and avoids the usual AUTO overshoot.
            axis = (directions[index - 1] + directions[index]) * 0.5
            left_span = spans[index - 1]
            right_span = spans[index]
            if axis.length <= EPSILON:
                handles.append((point.copy(), point.copy()))
                continue
        left = point - axis * (left_span / 3.0)
        right = point + axis * (right_span / 3.0)
        if not _vector_is_finite(left) or not _vector_is_finite(right):
            raise CenterlineError("The recovered Bezier handles are not finite.")
        handles.append((left, right))
    return tuple(handles)


def _configure_recovered_curve_data(curve_data, profile, curve_mode):
    material_index = (
        int(curve_data.splines[0].material_index)
        if len(curve_data.splines) == 1
        else 0
    )
    curve_data.dimensions = "3D"
    resolution = (
        RECOVERY_BEZIER_RESOLUTION
        if curve_mode == RECOVERY_CURVE_MODE_BEZIER
        else 1
    )
    curve_data.resolution_u = resolution
    curve_data.render_resolution_u = resolution
    curve_data.twist_mode = "MINIMUM"
    curve_data.twist_smooth = 0.0
    curve_data.fill_mode = profile["fill_mode"]
    curve_data.bevel_mode = profile["bevel_mode"]
    curve_data.bevel_resolution = profile["bevel_resolution"]
    curve_data.use_fill_caps = False
    curve_data.extrude = profile["extrude"]
    curve_data.offset = profile["offset"]
    curve_data.bevel_depth = profile["bevel_depth"]
    curve_data.taper_object = None
    if profile["profile_kind"] == "RECTANGLE":
        _configure_rectangle_bevel_profile(curve_data)
    curve_data.splines.clear()
    spline_type = "BEZIER" if curve_mode == RECOVERY_CURVE_MODE_BEZIER else "POLY"
    spline = curve_data.splines.new(type=spline_type)
    spline.material_index = min(material_index, max(0, len(curve_data.materials) - 1))
    spline.resolution_u = resolution
    spline.radius_interpolation = "LINEAR"
    spline.tilt_interpolation = "LINEAR"
    if spline_type == "BEZIER":
        spline.bezier_points.add(len(profile["coordinates"]) - 1)
        handles = _recovery_aligned_handles(profile["coordinates"])
        for point in spline.bezier_points:
            point.handle_left_type = "FREE"
            point.handle_right_type = "FREE"
        for point, coordinate, radius, tilt in zip(
            spline.bezier_points,
            profile["coordinates"],
            profile["radii"],
            profile["tilts"],
        ):
            point.co = coordinate
            point.radius = float(radius)
            point.tilt = float(tilt)
        for point, (handle_left, handle_right) in zip(
            spline.bezier_points,
            handles,
        ):
            point.handle_left = handle_left
            point.handle_right = handle_right
        for point in spline.bezier_points:
            point.handle_left_type = "ALIGNED"
            point.handle_right_type = "ALIGNED"
    else:
        spline.points.add(len(profile["coordinates"]) - 1)
        for point, coordinate, radius, tilt in zip(
            spline.points,
            profile["coordinates"],
            profile["radii"],
            profile["tilts"],
        ):
            point.co = (*coordinate, 1.0)
            point.radius = float(radius)
            point.tilt = float(tilt)
    spline.use_cyclic_u = False


def _recovery_source_object(obj):
    try:
        source_obj = obj.get(RECOVERY_SOURCE_OBJECT_KEY) if obj is not None else None
        if not isinstance(source_obj, bpy.types.Object) or source_obj.type != "MESH":
            return None
    except ReferenceError:
        return None
    return source_obj


def _recovery_source_matches(obj, source_obj):
    stored_source = _recovery_source_object(obj)
    try:
        return bool(
            stored_source is not None
            and source_obj is not None
            and stored_source.as_pointer() == source_obj.as_pointer()
        )
    except ReferenceError:
        return False


def _recovery_curve_contract(obj, metadata):
    stored_mode = obj.get(RECOVERY_CURVE_MODE_KEY)
    stored_count = obj.get(RECOVERY_CONTROL_POINTS_KEY)
    if stored_mode is None and stored_count is None:
        curve_mode = RECOVERY_CURVE_MODE_EXACT
        control_points = metadata["point_count"]
    elif (
        isinstance(stored_mode, str)
        and stored_mode in RECOVERY_CURVE_MODES
        and isinstance(stored_count, int)
        and not isinstance(stored_count, bool)
        and stored_count >= 2
    ):
        curve_mode = stored_mode
        control_points = stored_count
    else:
        return None

    splines = obj.data.splines
    if len(splines) != 1:
        return None
    spline = splines[0]
    if curve_mode == RECOVERY_CURVE_MODE_EXACT:
        if (
            spline.type != "POLY"
            or control_points != metadata["point_count"]
            or len(spline.points) != control_points
        ):
            return None
    elif (
        spline.type != "BEZIER"
        or control_points < RECOVERY_CONTROL_POINTS_MIN
        or control_points > RECOVERY_CONTROL_POINTS_MAX
        or len(spline.bezier_points) != control_points
    ):
        return None
    return curve_mode, control_points


def _is_recovered_curve(obj):
    source_obj = _recovery_source_object(obj)
    stored_profile = obj.get(RECOVERY_PROFILE_KEY) if obj is not None else None
    metadata = (
        _curve_cross_section_metadata(obj, validate_spline=False)
        if obj is not None and obj.type == "CURVE"
        else None
    )
    return bool(
        obj is not None
        and obj.type == "CURVE"
        and obj.get("character_designer_generator") == RECOVERY_GENERATOR_ID
        and isinstance(source_obj, bpy.types.Object)
        and source_obj.type == "MESH"
        and isinstance(obj.get(RECOVERY_SIGNATURE_KEY), str)
        and isinstance(stored_profile, str)
        and stored_profile in {"HALF_ROUND", "ROUND_FULL", "RECTANGLE"}
        and metadata is not None
        and _recovery_curve_contract(obj, metadata) is not None
    )


def _scene_recovered_curves_for_source(context, source_obj):
    return tuple(
        obj
        for obj in context.scene.objects
        if obj.type == "CURVE"
        and obj.get("character_designer_generator") == RECOVERY_GENERATOR_ID
        and _recovery_source_matches(obj, source_obj)
    )


def _automatic_recovery_target(context, source_obj, signature):
    exact = tuple(
        obj
        for obj in _scene_recovered_curves_for_source(context, source_obj)
        if obj.get(RECOVERY_SIGNATURE_KEY) == signature
    )
    invalid = tuple(obj for obj in exact if not _is_recovered_curve(obj))
    if invalid:
        names = ", ".join(sorted(obj.name for obj in invalid))
        raise CenterlineError(
            f"Matching recovered Curve data is unreadable ({names}); nothing was changed."
        )
    exact = tuple(obj for obj in exact if _is_recovered_curve(obj))
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise CenterlineError(
            "More than one recovered Curve matches this strand; nothing was changed."
        )
    return None


def _curve_profile_matches_rectangle(curve_data, tolerance=1.0e-6):
    if curve_data.bevel_mode != "PROFILE":
        return False
    points = tuple(curve_data.bevel_profile.points)
    if len(points) != 3:
        return False
    expected = (
        ((1.0, 0.0), "AUTO", "AUTO"),
        ((1.0, 1.0), "VECTOR", "VECTOR"),
        ((0.0, 1.0), "AUTO", "AUTO"),
    )
    return all(
        abs(float(point.location[0]) - location[0]) <= tolerance
        and abs(float(point.location[1]) - location[1]) <= tolerance
        and point.handle_type_1 == handle_type_1
        and point.handle_type_2 == handle_type_2
        for point, (location, handle_type_1, handle_type_2) in zip(points, expected)
    )


def _recovery_curve_matches_plan(curve_obj, plan, tolerance=1.0e-6):
    if not _is_recovered_curve(curve_obj):
        return False
    expected_values = _recovery_managed_values(plan)
    for key, value in expected_values.items():
        actual = curve_obj.get(key)
        if key == RECOVERY_SOURCE_OBJECT_KEY:
            if not _recovery_source_matches(curve_obj, value):
                return False
        elif actual != value:
            return False
    try:
        actual_modifier_specs = _capture_recovery_modifier_stack(curve_obj)
        if (
            _recovery_modifier_stack_json(actual_modifier_specs)
            != plan["modifier_stack_json"]
            or _recovery_modifier_ownership_json(actual_modifier_specs)
            != plan["modifier_ownership_json"]
        ):
            return False
    except CenterlineError:
        return False
    source_obj = plan["source_obj"]
    if any(
        abs(float(curve_obj.matrix_world[row][column]) - float(source_obj.matrix_world[row][column]))
        > 1.0e-9
        for row in range(4)
        for column in range(4)
    ):
        return False
    data = curve_obj.data
    profile = plan["profile"]
    curve_mode = plan["curve_mode"]
    expected_resolution = (
        RECOVERY_BEZIER_RESOLUTION
        if curve_mode == RECOVERY_CURVE_MODE_BEZIER
        else 1
    )
    expected_spline_type = (
        "BEZIER" if curve_mode == RECOVERY_CURVE_MODE_BEZIER else "POLY"
    )
    scalar_values = (
        (data.bevel_depth, profile["bevel_depth"]),
        (data.extrude, profile["extrude"]),
        (data.offset, profile["offset"]),
    )
    if any(abs(float(actual) - float(expected)) > tolerance for actual, expected in scalar_values):
        return False
    if (
        data.dimensions != "3D"
        or int(data.resolution_u) != expected_resolution
        or int(data.render_resolution_u) != expected_resolution
        or data.twist_mode != "MINIMUM"
        or abs(float(data.twist_smooth)) > tolerance
        or bool(data.use_fill_caps)
        or data.taper_object is not None
        or data.bevel_mode != profile["bevel_mode"]
        or data.fill_mode != profile["fill_mode"]
        or int(data.bevel_resolution) != int(profile["bevel_resolution"])
        or len(data.splines) != 1
        or data.splines[0].type != expected_spline_type
        or int(data.splines[0].resolution_u) != expected_resolution
        or data.splines[0].radius_interpolation != "LINEAR"
        or data.splines[0].tilt_interpolation != "LINEAR"
        or bool(data.splines[0].use_cyclic_u)
    ):
        return False
    if profile["profile_kind"] == "RECTANGLE" and not _curve_profile_matches_rectangle(data):
        return False
    spline = data.splines[0]
    if curve_mode == RECOVERY_CURVE_MODE_BEZIER:
        if len(spline.bezier_points) != len(profile["coordinates"]):
            return False
        try:
            expected_handles = _recovery_aligned_handles(profile["coordinates"])
        except CenterlineError:
            return False
        for point, coordinate, radius, tilt, handles in zip(
            spline.bezier_points,
            profile["coordinates"],
            profile["radii"],
            profile["tilts"],
            expected_handles,
        ):
            expected_left, expected_right = handles
            if (
                (point.co - coordinate).length > tolerance
                or abs(float(point.radius) - float(radius)) > tolerance
                or abs(float(point.tilt) - float(tilt)) > tolerance
                or point.handle_left_type != "ALIGNED"
                or point.handle_right_type != "ALIGNED"
                or (point.handle_left - expected_left).length > tolerance
                or (point.handle_right - expected_right).length > tolerance
            ):
                return False
    else:
        if len(spline.points) != len(profile["coordinates"]):
            return False
        for point, coordinate, radius, tilt in zip(
            spline.points,
            profile["coordinates"],
            profile["radii"],
            profile["tilts"],
        ):
            if (
                (point.co.xyz - coordinate).length > tolerance
                or abs(float(point.co.w) - 1.0) > tolerance
                or abs(float(point.radius) - float(radius)) > tolerance
                or abs(float(point.tilt) - float(tilt)) > tolerance
            ):
                return False
    return True


def _validate_recovery_target(target):
    if target is None:
        return
    if target.library is not None or target.data.library is not None:
        raise CenterlineError(f"{target.name} is linked and cannot be updated safely.")
    if getattr(target.data, "shape_keys", None) is not None:
        raise CenterlineError(f"{target.name} has shape keys; nothing was overwritten.")
    if target.data.animation_data is not None:
        raise CenterlineError(f"{target.name} Curve data is animated; nothing was overwritten.")
    if target.animation_data is not None:
        raise CenterlineError(f"{target.name} Object data is animated; nothing was overwritten.")
    _validate_recovery_modifier_ownership(target)


def _build_recovery_plans(
    context,
    source_obj,
    bm,
    records,
    *,
    curve_mode=RECOVERY_CURVE_MODE_EXACT,
    control_points=RECOVERY_CONTROL_POINTS_DEFAULT,
):
    if not isinstance(curve_mode, str) or curve_mode not in RECOVERY_CURVE_MODES:
        raise CenterlineError("Choose Exact or Bezier recovery mode.")
    modifier_specs = _capture_recovery_modifier_stack(
        source_obj,
        source_obj=source_obj,
    )
    modifier_stack_json = _recovery_modifier_stack_json(modifier_specs)
    modifier_ownership_json = _recovery_modifier_ownership_json(modifier_specs)
    modifier_label = _recovery_modifier_stack_label(modifier_specs)
    plans = []
    signatures = set()
    targets = set()
    for record in records:
        layers = record["layers"]
        metadata = _build_cross_section_metadata(
            source_obj,
            bm,
            layers,
            record["centers"],
            record["regular_kind"],
        )
        source_profile = _recovery_profile_solution(bm, record, metadata)
        profile = _recovery_output_profile(
            source_profile,
            curve_mode,
            control_points,
        )
        signature = _recovery_component_signature(layers)
        if signature in signatures:
            raise CenterlineError(
                "The selection reaches the same strand more than once; nothing was changed."
            )
        signatures.add(signature)
        target = _automatic_recovery_target(context, source_obj, signature)
        _validate_recovery_target(target)
        if target is not None and target in targets:
            raise CenterlineError("Two recovery components resolved to one target; nothing was changed.")
        if target is not None:
            targets.add(target)
        plans.append(
            {
                "source_obj": source_obj,
                "layers": tuple(tuple(layer) for layer in layers),
                "layers_json": _recovery_layers_json(layers),
                "metadata": metadata,
                "metadata_json": _recovery_metadata_json(metadata),
                "profile": profile,
                "curve_mode": curve_mode,
                "control_points": len(profile["coordinates"]),
                "signature": signature,
                "target": target,
                "input_mode": record["input_mode"],
                "modifier_specs": modifier_specs,
                "modifier_stack_json": modifier_stack_json,
                "modifier_ownership_json": modifier_ownership_json,
                "modifier_label": modifier_label,
            }
        )
    return tuple(plans)


def _create_recovered_curve_object(context, plan):
    source_obj = plan["source_obj"]
    curve_data = None
    curve_obj = None
    try:
        curve_data = bpy.data.curves.new(f"{source_obj.name}_RecoveredCurve", type="CURVE")
        for material in source_obj.data.materials:
            if material is not None:
                curve_data.materials.append(material)
        _configure_recovered_curve_data(
            curve_data,
            plan["profile"],
            plan["curve_mode"],
        )
        curve_obj = bpy.data.objects.new(f"{source_obj.name}_RecoveredCurve", curve_data)
        _visible_source_collection(context, source_obj).objects.link(curve_obj)
        curve_obj.matrix_world = source_obj.matrix_world.copy()
        curve_obj.show_in_front = True
        _replace_recovery_modifier_stack(curve_obj, plan["modifier_specs"])
        _write_recovery_properties(curve_obj, plan)
        return curve_obj
    except Exception:
        if curve_obj is not None and curve_obj.name in bpy.data.objects:
            bpy.data.objects.remove(curve_obj, do_unlink=True)
        if curve_data is not None and curve_data.users == 0 and curve_data.name in bpy.data.curves:
            bpy.data.curves.remove(curve_data)
        raise


def _apply_recovered_curve_plan(context, plan):
    target = plan["target"]
    if target is None:
        return _create_recovered_curve_object(context, plan), "CREATED"
    if _recovery_curve_matches_plan(target, plan):
        return target, "UNCHANGED"
    old_data = target.data
    new_data = old_data.copy()
    try:
        _configure_recovered_curve_data(
            new_data,
            plan["profile"],
            plan["curve_mode"],
        )
        target.data = new_data
        target.matrix_world = plan["source_obj"].matrix_world.copy()
        _replace_recovery_modifier_stack(target, plan["modifier_specs"])
        _write_recovery_properties(target, plan)
        if target.data is not new_data:
            raise CenterlineError("Blender did not apply the recovered Curve update.")
    except Exception:
        if target.data is new_data:
            target.data = old_data
        if new_data.users == 0 and new_data.name in bpy.data.curves:
            bpy.data.curves.remove(new_data)
        raise
    return target, "UPDATED"


def _restore_id_properties(obj, snapshot):
    for key, (existed, value) in snapshot.items():
        if existed:
            obj[key] = value
        elif key in obj:
            del obj[key]


def _validate_recovery_replacement(context, source_obj, bm, plans):
    """Preflight every condition required before a source Mesh may be removed."""

    if source_obj is None or source_obj.type != "MESH":
        raise CenterlineError("Replace Mesh needs one local source Mesh Object.")
    if context.scene.objects.get(source_obj.name) is not source_obj:
        raise CenterlineError("The source Mesh is not in the current Scene.")
    objects_in_mode = tuple(getattr(context, "objects_in_mode", ()) or ())
    if any(obj is not source_obj for obj in objects_in_mode):
        raise CenterlineError("Replace Mesh supports only one Object in Edit Mode.")
    if source_obj.library is not None or source_obj.data.library is not None:
        raise CenterlineError("A linked source Mesh cannot be replaced safely.")
    if getattr(source_obj, "override_library", None) is not None:
        raise CenterlineError("A library override source Mesh cannot be replaced safely.")
    if source_obj.parent is not None or tuple(source_obj.children):
        raise CenterlineError("Remove parent/child relationships before Replace Mesh.")
    if len(source_obj.constraints):
        raise CenterlineError("Remove Object constraints before Replace Mesh.")
    if source_obj.animation_data is not None or source_obj.data.animation_data is not None:
        raise CenterlineError("Animated source data cannot be replaced safely.")
    if getattr(source_obj.data, "shape_keys", None) is not None:
        raise CenterlineError("A source Mesh with shape keys cannot be replaced safely.")
    if getattr(source_obj, "instance_collection", None) is not None:
        raise CenterlineError("A collection-instancing source cannot be replaced safely.")

    scenes = tuple(
        scene
        for scene in bpy.data.scenes
        if scene.objects.get(source_obj.name) is source_obj
    )
    if scenes != (context.scene,):
        raise CenterlineError("Replace Mesh requires a source used only by the current Scene.")
    source_collections = tuple(source_obj.users_collection)
    if not source_collections or any(
        collection.library is not None for collection in source_collections
    ):
        raise CenterlineError("Replace Mesh needs local source Collections.")

    bm.verts.ensure_lookup_table()
    covered = {
        int(vertex_index)
        for plan in plans
        for layer in plan["layers"]
        for vertex_index in layer
    }
    all_vertices = {vertex.index for vertex in bm.verts}
    if covered != all_vertices:
        raise CenterlineError(
            "Replace Mesh needs the complete source Mesh selected; use Add for a partial band."
        )
    if not plans or any(plan["source_obj"] is not source_obj for plan in plans):
        raise CenterlineError("Replace Mesh recovery plans do not share one source Mesh.")

    plan_targets = {plan["target"] for plan in plans if plan["target"] is not None}
    existing_targets = {
        obj
        for obj in bpy.data.objects
        if obj.type == "CURVE"
        and obj.get("character_designer_generator") == RECOVERY_GENERATOR_ID
        and _recovery_source_matches(obj, source_obj)
    }
    extras = existing_targets - plan_targets
    if extras:
        names = ", ".join(sorted(obj.name for obj in extras))
        raise CenterlineError(
            f"Existing partial recovery outputs would lose their source ({names}); use Add."
        )
    if any(target.hide_select for target in plan_targets):
        raise CenterlineError("A recovered target is not selectable; Replace Mesh was cancelled.")
    return source_collections


def _restore_recovery_edit_context(context, source_obj, selected_objects=()):
    """Best-effort restoration used only before the source Object is removed."""

    try:
        if bpy.data.objects.get(source_obj.name) is not source_obj:
            return
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        restorable = tuple(
            obj
            for obj in selected_objects
            if bpy.data.objects.get(obj.name) is obj
            and context.view_layer.objects.get(obj.name) is obj
        )
        for obj in restorable:
            obj.select_set(True)
        if not source_obj.select_get():
            source_obj.select_set(True)
        context.view_layer.objects.active = source_obj
        bpy.ops.object.mode_set(mode="EDIT")
    except Exception:
        traceback.print_exc()


def _commit_recovered_curve_batch(
    context,
    plans,
    *,
    replace_source=False,
    source_collections=(),
):
    """Apply every recovery plan as one all-or-nothing data transaction."""

    source_obj = plans[0]["source_obj"] if plans else None
    original_selected_objects = tuple(context.selected_objects)
    before_objects = set(bpy.data.objects)
    before_curves = set(bpy.data.curves)
    target_snapshots = {
        plan["target"]: {
            "data": plan["target"].data,
            "matrix": plan["target"].matrix_world.copy(),
            "properties": {
                key: (key in plan["target"], plan["target"].get(key))
                for key in RECOVERY_MANAGED_KEYS
            },
            "collections": tuple(plan["target"].users_collection),
            "hidden": bool(plan["target"].hide_get()),
            "modifiers": _capture_recovery_modifier_stack(plan["target"]),
        }
        for plan in plans
        if plan["target"] is not None
    }
    results = []
    source_removed = False
    try:
        for plan in plans:
            target, action = _apply_recovered_curve_plan(context, plan)
            results.append((target, action))
        if replace_source:
            if source_obj is None or not source_collections:
                raise CenterlineError("Replace Mesh did not receive a validated source.")
            for (target, _action), plan in zip(results, plans):
                if not _recovery_curve_matches_plan(target, plan):
                    raise CenterlineError(
                        f"Recovered Curve validation failed for {target.name}; source kept."
                    )
                for collection in source_collections:
                    if collection.objects.get(target.name) is not target:
                        collection.objects.link(target)
                target[RECOVERY_SOURCE_REPLACED_KEY] = True
                if RECOVERY_SOURCE_OBJECT_KEY in target:
                    del target[RECOVERY_SOURCE_OBJECT_KEY]

            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            for target, _action in results:
                target.hide_set(False)
                target.select_set(True)
            context.view_layer.objects.active = results[-1][0]
            expected_selected = {target.as_pointer() for target, _action in results}
            actual_selected = {obj.as_pointer() for obj in context.selected_objects}
            if actual_selected != expected_selected:
                raise CenterlineError(
                    "Blender could not select every replacement Curve; source kept."
                )
            bpy.data.objects.remove(source_obj, do_unlink=True)
            source_removed = True
    except Exception:
        if source_removed:
            raise
        for target, snapshot in target_snapshots.items():
            if target.name not in bpy.data.objects:
                continue
            current_data = target.data
            target.data = snapshot["data"]
            target.matrix_world = snapshot["matrix"]
            _replace_recovery_modifier_stack(target, snapshot["modifiers"])
            _restore_id_properties(target, snapshot["properties"])
            original_collections = set(snapshot["collections"])
            for collection in tuple(target.users_collection):
                if collection not in original_collections:
                    collection.objects.unlink(target)
            for collection in original_collections:
                if collection.objects.get(target.name) is not target:
                    collection.objects.link(target)
            target.hide_set(snapshot["hidden"])
            if current_data is not snapshot["data"] and current_data.users == 0:
                if current_data.name in bpy.data.curves:
                    bpy.data.curves.remove(current_data)
        for obj in tuple(set(bpy.data.objects) - before_objects):
            if obj.get("character_designer_generator") == RECOVERY_GENERATOR_ID:
                bpy.data.objects.remove(obj, do_unlink=True)
        for curve_data in tuple(set(bpy.data.curves) - before_curves):
            if curve_data.users == 0 and curve_data.name in bpy.data.curves:
                bpy.data.curves.remove(curve_data)
        if replace_source and source_obj is not None:
            _restore_recovery_edit_context(
                context,
                source_obj,
                original_selected_objects,
            )
        raise

    old_data_to_cleanup = []
    for snapshot in target_snapshots.values():
        old_data = snapshot["data"]
        if not any(old_data is existing for existing in old_data_to_cleanup):
            old_data_to_cleanup.append(old_data)
    for old_data in old_data_to_cleanup:
        try:
            if old_data.users == 0 and bpy.data.curves.get(old_data.name) is old_data:
                bpy.data.curves.remove(old_data)
        except Exception as exc:
            print(f"Character Designer: orphan Curve cleanup skipped: {exc}")
    return tuple(results)


def _store_root_capture(
    settings,
    obj,
    bm,
    root_indices,
    root_edge_keys,
    kind,
    ignored_indices,
    capture_mode,
    *,
    clear_output,
):
    settings.root_object = obj
    settings.root_mesh = obj.data
    settings.root_indices_json = json.dumps(root_indices, separators=(",", ":"))
    settings.root_edges_json = json.dumps(root_edge_keys, separators=(",", ":"))
    settings.root_ignored_indices_json = json.dumps(
        ignored_indices,
        separators=(",", ":"),
    )
    settings.root_kind = kind
    settings.root_capture_mode = capture_mode
    settings.root_vertex_count = len(root_indices)
    settings.root_ignored_count = len(ignored_indices)
    settings.topology_signature = _topology_signature(bm)
    if clear_output:
        settings.output_object = None


def _capture_status_message(root_indices, ignored_indices, kind, capture_mode):
    if capture_mode in {"FAN_CAP", "TRI_CAP", "REGION_CAP"}:
        return (
            f"Captured cap boundary with {len(root_indices)} vertices; "
            f"ignored {len(ignored_indices)} interior "
            f"{'vertex' if len(ignored_indices) == 1 else 'vertices'}."
        )
    return f"Captured {_root_label(kind)} with {len(root_indices)} vertices."


class CHARACTERDESIGNER_OT_preview_selected_root(Operator):
    bl_idname = "character_designer.preview_selected_root"
    bl_label = "Preview Selected Root"
    bl_description = "Capture the selected root and preview its centerline as the selection changes"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"ERROR"}, "Character Designer is not registered correctly.")
            return {"CANCELLED"}

        _stop_live_preview(settings, clear_capture=True)
        _set_live_preview_enabled(settings, True)
        try:
            (
                obj,
                bm,
                root_indices,
                root_edge_keys,
                kind,
                ignored_indices,
                capture_mode,
            ) = _capture_data(context)
            _store_root_capture(
                settings,
                obj,
                bm,
                root_indices,
                root_edge_keys,
                kind,
                ignored_indices,
                capture_mode,
                clear_output=False,
            )
            _reset_preview_properties(settings)
            settings.preview_mode = "CAPTURE"
            settings.preview_active = True
            _ensure_live_preview_runtime()
            _update_live_preview(context, force=True)
        except CenterlineError as exc:
            _set_live_preview_enabled(settings, False)
            _stop_live_preview(settings, clear_capture=True)
            _set_status(settings, "ERROR", str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            _set_live_preview_enabled(settings, False)
            _stop_live_preview(settings, clear_capture=True)
            message = f"Live preview could not start: {_short_preview_message(exc)}"
            _set_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        message = _capture_status_message(root_indices, ignored_indices, kind, capture_mode)
        _set_status(settings, "SUCCESS", message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_cancel_preview(Operator):
    bl_idname = "character_designer.cancel_preview"
    bl_label = "Cancel Preview"
    bl_description = "Stop the live preview without deleting any previously created curve"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        settings = _settings(context)
        _set_live_preview_enabled(settings, False)
        _set_recovery_preview_enabled(settings, False)
        _stop_live_preview(settings, clear_capture=True)
        if settings is not None:
            _set_status(settings, "INFO", "Live preview cancelled.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_capture_root(Operator):
    bl_idname = "character_designer.capture_root_slice"
    bl_label = "Capture Selected Root"
    bl_description = "Capture one selected edge row/loop or the boundary of a selected cap region"
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"ERROR"}, "Character Designer is not registered correctly.")
            return {"CANCELLED"}
        if settings.preview_active:
            _set_live_preview_enabled(settings, False)
            _stop_live_preview(settings, clear_capture=True)
        try:
            (
                obj,
                bm,
                root_indices,
                root_edge_keys,
                kind,
                ignored_indices,
                capture_mode,
            ) = _capture_data(context)
            _store_root_capture(
                settings,
                obj,
                bm,
                root_indices,
                root_edge_keys,
                kind,
                ignored_indices,
                capture_mode,
                clear_output=True,
            )
            _set_status(
                settings,
                "SUCCESS",
                _capture_status_message(root_indices, ignored_indices, kind, capture_mode),
            )
        except CenterlineError as exc:
            _set_status(settings, "ERROR", str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_clear_root(Operator):
    bl_idname = "character_designer.clear_root_slice"
    bl_label = "Clear Captured Root"
    bl_description = "Forget the captured root slice"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        settings = _settings(context)
        if settings is not None:
            _set_live_preview_enabled(settings, False)
            _stop_live_preview(settings, clear_capture=True)
            _set_status(settings, "INFO", "Root capture cleared.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_build_centerline(Operator):
    bl_idname = "character_designer.build_centerline"
    bl_label = "Build Center Curve"
    bl_description = "Create one exact Poly curve point at the arithmetic mean of every selected slice"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"ERROR"}, "Character Designer is not registered correctly.")
            return {"CANCELLED"}

        created = None
        try:
            source_obj, _bm, layers, centers = _extract_layers(context, settings)
            regular_kind = (
                _regular_kind_from_layers(_bm, layers)
                if settings.root_kind == "POINT"
                else settings.root_kind
            )
            metadata = _build_cross_section_metadata(
                source_obj,
                _bm,
                layers,
                centers,
                regular_kind,
            )
            created = _create_centerline_object(
                context,
                source_obj,
                layers,
                centers,
                metadata,
                alignment=settings.centerline_placement,
                blend_factor=settings.centerline_blend_factor,
            )
            settings.output_object = created
            summary = _metadata_summary(metadata)
            _set_status(
                settings,
                "SUCCESS",
                (
                    f"Built {created.name}: {len(centers)} points; local profile width "
                    f"{_compact_number(summary['minimum_profile_span'])}-"
                    f"{_compact_number(summary['maximum_profile_span'])} BU; Round/Half Depth "
                    f"{_compact_number(created.data.bevel_depth)} BU; tip "
                    f"{HAIR_PROFILE_TIP_RADIUS * 100.0:.1f}%."
                ),
            )
        except CenterlineError as exc:
            _set_status(settings, "ERROR", str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            _remove_created_curve(created)
            if settings.output_object is created:
                settings.output_object = None
            message = f"Center curve failed: {exc}"
            _set_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            traceback.print_exc()
            return {"CANCELLED"}

        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_confirm_centerline(Operator):
    bl_idname = "character_designer.confirm_centerline"
    bl_label = "Confirm Centerline"
    bl_description = "Revalidate the current mesh selection and create the exact center curve"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return bool(
            settings is not None
            and settings.preview_active
            and settings.preview_mode in {"AUTO", "CAPTURE"}
            and settings.preview_confirmable
            and context.edit_object is settings.root_object
        )

    def execute(self, context):
        settings = _settings(context)
        if settings is None or not settings.preview_active:
            self.report({"ERROR"}, "Start a live preview first.")
            return {"CANCELLED"}
        if not settings.preview_confirmable:
            self.report({"ERROR"}, "Grow a valid selection before confirming the centerline.")
            return {"CANCELLED"}

        created = None
        try:
            if settings.preview_mode == "AUTO":
                (
                    source_obj,
                    bm,
                    layers,
                    centers,
                    regular_kind,
                    direction_confirmable,
                ) = _infer_selected_centerline(context)
                if len(layers) < 2:
                    raise CenterlineError("Select at least two connected cross-sections.")
                if not direction_confirmable:
                    raise CenterlineError("Direction unclear - select one end as Start.")
            else:
                source_obj, bm, layers, centers = _extract_layers(context, settings)
                regular_kind = (
                    _regular_kind_from_layers(bm, layers)
                    if settings.root_kind == "POINT"
                    else settings.root_kind
                )
            metadata = _build_cross_section_metadata(
                source_obj,
                bm,
                layers,
                centers,
                regular_kind,
            )
            created = _create_centerline_object(
                context,
                source_obj,
                layers,
                centers,
                metadata,
                alignment=settings.centerline_placement,
                blend_factor=settings.centerline_blend_factor,
            )
        except CenterlineError as exc:
            _publish_live_preview_invalid(settings, exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            _remove_created_curve(created)
            message = f"Could not create Curve: {_short_preview_message(exc)}"
            settings.preview_action_error = message
            _tag_view3d_redraw()
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        settings.output_object = created
        success_message = (
            f"Created {created.name} · {len(centers)} points · Round/Half Depth "
            f"{_compact_number(created.data.bevel_depth)} BU."
        )
        _set_live_preview_enabled(settings, False)
        _stop_live_preview(settings, clear_capture=True)
        _set_status(settings, "SUCCESS", success_message)
        self.report({"INFO"}, success_message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_set_front_alignment(Operator):
    bl_idname = "character_designer.set_front_alignment"
    bl_label = "Set Centerline Alignment"
    bl_description = "Switch an existing Character Designer centerline in place"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="Alignment",
        items=(
            (HAIR_ALIGNMENT_CENTERED, "Centered", "Restore the Curve to source cross-section centers"),
            (HAIR_ALIGNMENT_FRONT_FLUSH, "Front Surface", "Align the Half profile front to the source"),
            (HAIR_ALIGNMENT_BLEND, "Blend", "Use the current 0..1 Mix value"),
        ),
        default=HAIR_ALIGNMENT_FRONT_FLUSH,
    )

    @classmethod
    def poll(cls, context):
        return _is_character_designer_centerline(context.active_object)

    def execute(self, context):
        settings = _settings(context)
        target = context.active_object
        try:
            metadata = _curve_cross_section_metadata(target)
            source_obj = None
            layers = _stored_centerline_layers(target)
            if metadata is None:
                raise CenterlineError("Select one valid Character Designer centerline.")
            if metadata.get("version", 0) < METADATA_VERSION:
                source_obj, layers, _centers, metadata = _metadata_from_recorded_source(target)
            blend_factor = (
                _stored_blend_factor(target)
                if self.mode == HAIR_ALIGNMENT_BLEND
                else HAIR_BLEND_DEFAULT
            )
            placement_factor = _alignment_factor(self.mode, blend_factor)
            _commit_existing_centerline(
                target,
                source_obj,
                layers,
                metadata,
                self.mode,
                blend_factor=placement_factor,
            )
            if settings is not None:
                settings.output_object = target
                settings.update_target_curve = target
                settings.centerline_placement = self.mode
                if self.mode == HAIR_ALIGNMENT_BLEND:
                    _set_centerline_blend_factor_silently(
                        settings,
                        placement_factor,
                    )
                _set_status(
                    settings,
                    "SUCCESS",
                    f"{target.name}: {_alignment_label(self.mode)} alignment.",
                )
        except CenterlineError as exc:
            if settings is not None:
                _set_status(settings, "ERROR", str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            message = f"Could not change centerline alignment: {exc}"
            if settings is not None:
                _set_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            traceback.print_exc()
            return {"CANCELLED"}
        self.report({"INFO"}, settings.last_message if settings else "Alignment updated.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_set_update_target(Operator):
    bl_idname = "character_designer.set_update_target"
    bl_label = "Use Selected as Update Target"
    bl_description = "Remember the active Character Designer centerline for selection-driven updates"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return _is_character_designer_centerline(context.active_object)

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"ERROR"}, "Character Designer is not registered correctly.")
            return {"CANCELLED"}
        settings.update_target_curve = context.active_object
        alignment = context.active_object.get(
            "character_designer_alignment",
            HAIR_ALIGNMENT_CENTERED,
        )
        if alignment not in HAIR_ALIGNMENTS:
            alignment = HAIR_ALIGNMENT_CENTERED
        settings.centerline_placement = alignment
        try:
            _set_centerline_blend_factor_silently(
                settings,
                _stored_blend_factor(context.active_object),
            )
        except CenterlineError:
            _set_centerline_blend_factor_silently(settings, HAIR_BLEND_DEFAULT)
        _set_status(settings, "INFO", f"Update target: {context.active_object.name}.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_update_existing_centerline(Operator):
    bl_idname = "character_designer.update_existing_centerline"
    bl_label = "Update Existing Centerline"
    bl_description = (
        "Update the remembered Curve from the current Edit selection, or refresh "
        "the active Curve from its recorded source layers"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        if context.mode == "EDIT_MESH":
            return bool(
                settings is not None
                and _is_character_designer_centerline(settings.update_target_curve)
                and context.edit_object is not None
            )
        return context.mode == "OBJECT" and _is_character_designer_centerline(
            context.active_object
        )

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"ERROR"}, "Character Designer is not registered correctly.")
            return {"CANCELLED"}
        try:
            if context.mode == "EDIT_MESH":
                target = settings.update_target_curve
                if not _is_character_designer_centerline(target):
                    raise CenterlineError("Choose one existing centerline as the update target first.")
                (
                    source_obj,
                    bm,
                    layers,
                    centers,
                    regular_kind,
                    direction_confirmable,
                ) = _infer_selected_centerline(context)
                if len(layers) < 2:
                    raise CenterlineError("Select at least two connected cross-sections.")
                if not direction_confirmable:
                    raise CenterlineError("Direction unclear - select one end as Start.")
                metadata = _build_cross_section_metadata(
                    source_obj,
                    bm,
                    layers,
                    centers,
                    regular_kind,
                )
            else:
                target = context.active_object
                source_obj, layers, _centers, metadata = _metadata_from_recorded_source(
                    target
                )

            if context.mode == "EDIT_MESH":
                alignment = settings.centerline_placement
                blend_factor = settings.centerline_blend_factor
            else:
                alignment = target.get(
                    "character_designer_alignment",
                    HAIR_ALIGNMENT_CENTERED,
                )
            if alignment not in HAIR_ALIGNMENTS:
                alignment = HAIR_ALIGNMENT_CENTERED
            if context.mode != "EDIT_MESH":
                blend_factor = (
                    _stored_blend_factor(target)
                    if alignment == HAIR_ALIGNMENT_BLEND
                    else settings.centerline_blend_factor
                )
            placement_factor = _alignment_factor(alignment, blend_factor)
            _commit_existing_centerline(
                target,
                source_obj,
                layers,
                metadata,
                alignment,
                blend_factor=placement_factor,
                update_matrix=True,
            )
            settings.output_object = target
            settings.update_target_curve = target
            settings.centerline_placement = alignment
            if alignment == HAIR_ALIGNMENT_BLEND:
                _set_centerline_blend_factor_silently(
                    settings,
                    placement_factor,
                )
            _set_status(
                settings,
                "SUCCESS",
                (
                    f"Updated {target.name}: {metadata['point_count']} points · "
                    f"{_alignment_label(alignment)}."
                ),
            )
        except CenterlineError as exc:
            _set_status(settings, "ERROR", str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            message = f"Could not update centerline: {exc}"
            _set_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            traceback.print_exc()
            return {"CANCELLED"}
        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_generate_or_update_centerline(Operator):
    bl_idname = "character_designer.generate_or_update_centerline"
    bl_label = "Generate / Update Centerline"
    bl_description = (
        "Create from the selected hair loops, overwrite the matching in-scene "
        "centerline, or refresh the active centerline from its recorded source"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode == "EDIT_MESH":
            return context.edit_object is not None and context.edit_object.type == "MESH"
        return context.mode == "OBJECT" and _is_character_designer_centerline(
            context.active_object
        )

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"ERROR"}, "Character Designer is not registered correctly.")
            return {"CANCELLED"}

        target = None
        created = False
        try:
            if context.mode == "EDIT_MESH":
                alignment = settings.centerline_placement
                if alignment not in HAIR_ALIGNMENTS:
                    alignment = HAIR_ALIGNMENT_CENTERED
                placement_factor = _alignment_factor(
                    alignment,
                    settings.centerline_blend_factor,
                )
                (
                    source_obj,
                    bm,
                    layers,
                    centers,
                    regular_kind,
                    direction_confirmable,
                ) = _infer_selected_centerline(context)
                if len(layers) < 2:
                    raise CenterlineError("Select at least two connected cross-sections.")
                if not direction_confirmable:
                    raise CenterlineError("Direction unclear - make one end edge active.")
                metadata = _build_cross_section_metadata(
                    source_obj,
                    bm,
                    layers,
                    centers,
                    regular_kind,
                )
                target = _automatic_centerline_target(
                    context,
                    source_obj,
                    layers,
                )
                if target is None:
                    target = _create_centerline_object(
                        context,
                        source_obj,
                        layers,
                        centers,
                        metadata,
                        alignment=alignment,
                        blend_factor=placement_factor,
                    )
                    created = True
                else:
                    _commit_existing_centerline(
                        target,
                        source_obj,
                        layers,
                        metadata,
                        alignment,
                        blend_factor=placement_factor,
                        update_matrix=True,
                    )
            else:
                target = context.active_object
                alignment = target.get(
                    "character_designer_alignment",
                    HAIR_ALIGNMENT_CENTERED,
                )
                if alignment not in HAIR_ALIGNMENTS:
                    alignment = HAIR_ALIGNMENT_CENTERED
                blend_factor = (
                    _stored_blend_factor(target)
                    if alignment == HAIR_ALIGNMENT_BLEND
                    else settings.centerline_blend_factor
                )
                placement_factor = _alignment_factor(alignment, blend_factor)
                source_obj, layers, _centers, metadata = _metadata_from_recorded_source(
                    target
                )
                _commit_existing_centerline(
                    target,
                    source_obj,
                    layers,
                    metadata,
                    alignment,
                    blend_factor=placement_factor,
                    update_matrix=True,
                )
        except CenterlineError as exc:
            message = str(exc)
            _set_status(settings, "INFO", message)
            self.report({"INFO"}, message)
            return {"CANCELLED"}
        except Exception as exc:
            message = f"Centerline update failed: {exc}"
            _set_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            traceback.print_exc()
            return {"CANCELLED"}

        settings.output_object = target
        settings.update_target_curve = target
        settings.centerline_placement = alignment
        if alignment == HAIR_ALIGNMENT_BLEND:
            _set_centerline_blend_factor_silently(
                settings,
                placement_factor,
            )
        _set_live_preview_enabled(settings, False)
        _stop_live_preview(settings, clear_capture=True)
        action = "Created" if created else "Updated"
        point_count = sum(
            len(spline.points) if spline.type == "POLY" else len(spline.bezier_points)
            for spline in target.data.splines
        )
        message = (
            f"{action} {target.name}: {point_count} points · "
            f"{_alignment_label(alignment)}."
        )
        _set_status(settings, "SUCCESS", message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_recover_applied_curve(Operator):
    bl_idname = "character_designer.recover_applied_curve"
    bl_label = "Recover Applied Curve"
    bl_description = (
        "Recover editable Curve points, Radius, Tilt, and a compatible profile "
        "from selected applied-hair bands or longitudinal guide edges while "
        "preserving Mirror and Subdivision modifiers in their original order"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(
            context.mode == "EDIT_MESH"
            and context.edit_object is not None
            and context.edit_object.type == "MESH"
        )

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"ERROR"}, "Character Designer is not registered correctly.")
            return {"CANCELLED"}
        source_action = settings.recovery_source_action
        try:
            if source_action not in RECOVERY_SOURCE_ACTIONS:
                raise CenterlineError("Choose Add or Replace Mesh output.")
            source_obj, bm, records = _infer_selected_recovery_components(context)
            plans = _build_recovery_plans(
                context,
                source_obj,
                bm,
                records,
                curve_mode=settings.recovery_curve_mode,
                control_points=settings.recovery_control_points,
            )
            source_collections = ()
            replace_source = source_action == RECOVERY_SOURCE_ACTION_REPLACE
            if replace_source:
                source_collections = _validate_recovery_replacement(
                    context,
                    source_obj,
                    bm,
                    plans,
                )
            _set_live_preview_enabled(settings, False)
            _set_recovery_preview_enabled(settings, False)
            _stop_live_preview(settings, clear_capture=True)
            results = _commit_recovered_curve_batch(
                context,
                plans,
                replace_source=replace_source,
                source_collections=source_collections,
            )
        except CenterlineError as exc:
            message = str(exc)
            _set_status(settings, "INFO", message)
            self.report({"INFO"}, message)
            return {"CANCELLED"}
        except Exception as exc:
            message = f"Applied Curve recovery failed: {exc}"
            _set_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            traceback.print_exc()
            return {"CANCELLED"}

        if not results:
            message = "No applied hair component was recovered."
            _set_status(settings, "INFO", message)
            self.report({"INFO"}, message)
            return {"CANCELLED"}
        settings.output_object = results[-1][0]
        created = sum(action == "CREATED" for _target, action in results)
        updated = sum(action == "UPDATED" for _target, action in results)
        unchanged = sum(action == "UNCHANGED" for _target, action in results)
        _set_live_preview_enabled(settings, False)
        _set_recovery_preview_enabled(settings, False)
        _stop_live_preview(settings, clear_capture=True)
        message = (
            f"Recovered {len(results)} strand{'s' if len(results) != 1 else ''}: "
            f"{created} created · {updated} updated · {unchanged} unchanged · "
            + (
                f"Bezier {settings.recovery_control_points} points."
                if settings.recovery_curve_mode == RECOVERY_CURVE_MODE_BEZIER
                else "Exact Poly."
            )
            + (
                " Source Mesh replaced."
                if source_action == RECOVERY_SOURCE_ACTION_REPLACE
                else " Source Mesh kept."
            )
            + (
                f" Preserved {plans[0]['modifier_label']}."
                if plans and plans[0].get("modifier_label")
                else ""
            )
        )
        _set_status(settings, "SUCCESS", message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_select_output(Operator):
    bl_idname = "character_designer.select_output"
    bl_label = "Select Output"
    bl_description = "Leave mesh Edit Mode and select the last generated center curve"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        obj = getattr(settings, "output_object", None) if settings else None
        return _output_is_selectable(context, obj)

    def execute(self, context):
        settings = _settings(context)
        output = settings.output_object
        if not _output_is_selectable(context, output):
            self.report({"WARNING"}, "The output curve is not visible/selectable in this View Layer.")
            return {"CANCELLED"}
        output.hide_set(False)
        if not output.visible_get(view_layer=context.view_layer):
            self.report({"WARNING"}, "The output curve is hidden by its collection or View Layer.")
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        output.hide_set(False)
        output.select_set(True)
        context.view_layer.objects.active = output
        if not output.select_get(view_layer=context.view_layer):
            self.report({"ERROR"}, "Blender could not select the output curve.")
            return {"CANCELLED"}
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_refresh_addon(Operator):
    bl_idname = "character_designer.refresh_addon"
    bl_label = "Refresh Add-on"
    bl_description = "Validate and reload Character Designer after its Python files change"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, _context):
        return bool(
            (ADDON_REFRESH_LAST_STATE or ADDON_REFRESH_LAST_ERROR)
            and not ADDON_REFRESH_PENDING
        )

    def execute(self, _context):
        global ADDON_REFRESH_PENDING, ADDON_REFRESH_LAST_ERROR, ADDON_REFRESH_LAST_STATE

        if not _source_changed():
            ADDON_REFRESH_LAST_STATE = False
            if not bpy.app.timers.is_registered(_source_watch_deferred):
                if not _register_source_watch():
                    self.report({"ERROR"}, ADDON_REFRESH_LAST_ERROR)
                    return {"CANCELLED"}
            ADDON_REFRESH_LAST_ERROR = ""
            _tag_view3d_redraw()
            self.report({"INFO"}, "Character Designer is already current.")
            return {"CANCELLED"}
        try:
            _validate_source_files()
        except Exception as exc:
            ADDON_REFRESH_LAST_ERROR = str(exc)
            self.report({"ERROR"}, f"Refresh blocked by a Python error: {exc}")
            return {"CANCELLED"}

        ADDON_REFRESH_PENDING = True
        ADDON_REFRESH_LAST_ERROR = ""
        try:
            if not bpy.app.timers.is_registered(_reload_addon_deferred):
                bpy.app.timers.register(_reload_addon_deferred, first_interval=0.1)
        except Exception as exc:
            ADDON_REFRESH_PENDING = False
            ADDON_REFRESH_LAST_ERROR = str(exc)
            self.report({"ERROR"}, f"Could not schedule refresh: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Refreshing Character Designer...")
        return {"FINISHED"}


def _draw_message(layout, level, message):
    if not message:
        return
    icon = {"ERROR": "ERROR", "SUCCESS": "CHECKMARK", "INFO": "INFO"}.get(level, "INFO")
    lines = textwrap.wrap(
        message,
        width=52,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [message]
    for index, line in enumerate(lines):
        row = layout.row()
        row.alert = level == "ERROR"
        if index == 0:
            row.label(text=line, icon=icon)
        else:
            row.label(text=line)


class CHARACTERDESIGNER_OT_set_ui_page(Operator):
    bl_idname = "character_designer.set_ui_page"
    bl_label = "Set CDesigner Page"
    bl_description = "Show one CDesigner tool family without changing the Scene"
    bl_options = {"INTERNAL"}

    page: EnumProperty(items=UI_PAGE_ITEMS, default=UI_PAGE_DEFAULT)

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"WARNING"}, "CDesigner state is unavailable.")
            return {"CANCELLED"}
        if self.page not in UI_PAGES:
            self.report({"WARNING"}, "Unknown CDesigner page.")
            return {"CANCELLED"}
        settings.ui_page = self.page
        return {"FINISHED"}


def _draw_page_tab(row, active_page, page, text, icon):
    action = row.operator(
        "character_designer.set_ui_page",
        text=text,
        icon=icon,
        depress=active_page == page,
    )
    action.page = page


def _draw_page_tabs(layout, active_page):
    tab_box = layout.box()
    first_row = tab_box.row(align=True)
    _draw_page_tab(first_row, active_page, UI_PAGE_HAIR, "Hair", "CURVE_DATA")
    _draw_page_tab(first_row, active_page, UI_PAGE_WEIGHT, "Weight", "MOD_ARMATURE")
    _draw_page_tab(first_row, active_page, UI_PAGE_RIG, "Rig", "CONSTRAINT_BONE")

    second_row = tab_box.row(align=True)
    _draw_page_tab(second_row, active_page, UI_PAGE_CLOTHING, "Clothing", "MOD_CLOTH")
    _draw_page_tab(second_row, active_page, UI_PAGE_ANIMATION, "Animation", "ACTION")
    _draw_page_tab(
        second_row,
        active_page,
        UI_PAGE_MISC,
        "Miscellaneous",
        "TOOL_SETTINGS",
    )


def _draw_refresh_action(layout):
    if not _refresh_ui_visible():
        return
    layout.separator()
    refresh_row = layout.row(align=True)
    refresh_row.alert = bool(ADDON_REFRESH_LAST_ERROR)
    refresh_row.enabled = not ADDON_REFRESH_PENDING
    refresh_row.operator(
        "character_designer.refresh_addon",
        text=(
            "Refreshing Character Designer..."
            if ADDON_REFRESH_PENDING
            else "Refresh Add-on"
        ),
        icon="ERROR" if ADDON_REFRESH_LAST_ERROR else "FILE_REFRESH",
    )


class CHARACTERDESIGNER_PT_main(Panel):
    bl_label = "Character Designer"
    bl_idname = "CHARACTERDESIGNER_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        if settings is None:
            layout.label(text="Add-on state unavailable", icon="ERROR")
            return

        page = active_ui_page(context)
        _draw_page_tabs(layout, page)
        if page != UI_PAGE_HAIR:
            _draw_refresh_action(layout)
            return

        layout.label(text="Hair Centerline", icon="CURVE_DATA")
        curve_control = bool(
            context.mode in {"OBJECT", "EDIT_CURVE"}
            and _is_character_designer_centerline(context.active_object)
            and (
                context.mode != "EDIT_CURVE"
                or context.edit_object is context.active_object
            )
        )
        object_refresh = bool(curve_control and context.mode == "OBJECT")
        _bind_centerline_controls(
            settings,
            context.active_object if curve_control else None,
        )
        if curve_control:
            stored_alignment = context.active_object.get(
                "character_designer_alignment",
                HAIR_ALIGNMENT_CENTERED,
            )
            if stored_alignment not in HAIR_ALIGNMENTS:
                stored_alignment = HAIR_ALIGNMENT_CENTERED
            placement_row = layout.row(align=True)
            for mode, label in (
                (HAIR_ALIGNMENT_CENTERED, "Centered"),
                (HAIR_ALIGNMENT_FRONT_FLUSH, "Surface"),
                (HAIR_ALIGNMENT_BLEND, "Blend"),
            ):
                action = placement_row.operator(
                    "character_designer.set_front_alignment",
                    text=label,
                    depress=stored_alignment == mode,
                )
                action.mode = mode
            if stored_alignment == HAIR_ALIGNMENT_BLEND:
                blend_row = layout.row(align=True)
                blend_row.prop(
                    settings,
                    "centerline_blend_factor",
                    text="Mix",
                    slider=True,
                )
        else:
            placement_row = layout.row(align=True)
            placement_row.prop(settings, "centerline_placement", expand=True)
            if settings.centerline_placement == HAIR_ALIGNMENT_BLEND:
                blend_row = layout.row(align=True)
                blend_row.prop(
                    settings,
                    "centerline_blend_factor",
                    text="Mix",
                    slider=True,
                )
        if context.mode == "EDIT_MESH" and context.edit_object is not None:
            layout.operator(
                "character_designer.generate_or_update_centerline",
                text="Generate / Update Centerline",
                icon="CURVE_DATA",
            )
            recovery_output_row = layout.row(align=True)
            recovery_output_row.prop(settings, "recovery_source_action", expand=True)
            recovery_mode_row = layout.row(align=True)
            recovery_mode_row.prop(settings, "recovery_curve_mode", expand=True)
            if settings.recovery_curve_mode == RECOVERY_CURVE_MODE_BEZIER:
                recovery_points_row = layout.row(align=True)
                recovery_points_row.prop(
                    settings,
                    "recovery_control_points",
                    text="Control Points",
                    slider=True,
                )
            recovery_preview_row = layout.row(align=True)
            recovery_preview_row.prop(
                settings,
                "recovery_preview_enabled",
                text="Preview",
                toggle=True,
                icon="HIDE_OFF" if settings.recovery_preview_enabled else "HIDE_ON",
            )
            if settings.preview_mode == "RECOVERY" and settings.preview_message:
                preview_message_row = layout.row()
                preview_message_row.label(
                    text=settings.preview_message,
                    icon="INFO",
                )
            layout.operator(
                "character_designer.recover_applied_curve",
                text="Recover Applied Curve",
                icon="MOD_CURVE",
            )
        elif object_refresh:
            layout.operator(
                "character_designer.generate_or_update_centerline",
                text="Refresh Centerline",
                icon="FILE_REFRESH",
            )
        elif not curve_control:
            layout.label(text="Select hair loops in Mesh Edit Mode.", icon="INFO")

        if settings.last_level == "ERROR" and settings.last_message:
            _draw_message(layout, "ERROR", settings.last_message)

        _draw_refresh_action(layout)


CLASSES = (
    CharacterDesignerState,
    CHARACTERDESIGNER_OT_preview_selected_root,
    CHARACTERDESIGNER_OT_cancel_preview,
    CHARACTERDESIGNER_OT_capture_root,
    CHARACTERDESIGNER_OT_clear_root,
    CHARACTERDESIGNER_OT_build_centerline,
    CHARACTERDESIGNER_OT_confirm_centerline,
    CHARACTERDESIGNER_OT_set_front_alignment,
    CHARACTERDESIGNER_OT_set_update_target,
    CHARACTERDESIGNER_OT_update_existing_centerline,
    CHARACTERDESIGNER_OT_generate_or_update_centerline,
    CHARACTERDESIGNER_OT_recover_applied_curve,
    CHARACTERDESIGNER_OT_select_output,
    CHARACTERDESIGNER_OT_refresh_addon,
    CHARACTERDESIGNER_OT_set_ui_page,
    CHARACTERDESIGNER_PT_main,
    *HAIR_BONES_CLASSES,
    *SKIRT_CLASSES,
    *ANIMATION_CLASSES,
    *SELECTED_BONE_WEIGHT_CLASSES,
    *WEIGHT_SYMMETRY_CLASSES,
    *DELTA_SYMMETRY_CLASSES,
    *LIMB_IK_CLASSES,
    *FOREARM_TWIST_CLASSES,
    *SPLINE_IK_SETUP_CLASSES,
    *REFERENCE_VIEW_CLASSES,
)


_WINDOW_MANAGER_POINTER_TYPES = (
    ("character_designer", CharacterDesignerState),
    ("character_designer_hair_bones", CharacterDesignerHairBonesState),
    ("character_designer_skirt", CharacterDesignerSkirtState),
    ("character_designer_animation", CharacterDesignerAnimationState),
    ("character_designer_delta", CharacterDesignerDeltaState),
    ("character_designer_limb_ik", CharacterDesignerLimbIKState),
    ("character_designer_forearm_twist", CharacterDesignerForearmTwistState),
    ("character_designer_spline_ik", CharacterDesignerSplineIKState),
    ("character_designer_references", CharacterDesignerReferenceState),
)


def _expected_rna_identifier(cls):
    """Return the identifier Blender registers for one of this add-on's classes."""

    if issubclass(cls, bpy.types.Operator):
        namespace, separator, name = cls.bl_idname.partition(".")
        if not separator or not namespace or not name:
            raise RuntimeError(f"Operator {cls.__name__} has an invalid bl_idname.")
        return f"{namespace.upper()}_OT_{name}"
    if issubclass(cls, bpy.types.Panel):
        return cls.bl_idname or cls.__name__
    if issubclass(cls, bpy.types.PropertyGroup):
        return cls.__name__
    raise RuntimeError(f"Unsupported Character Designer RNA class: {cls.__name__}.")


def _registered_rna_class(cls):
    """Resolve the live Python class behind an expected Blender RNA identifier."""

    identifier = _expected_rna_identifier(cls)
    for base in (bpy.types.Operator, bpy.types.Panel, bpy.types.PropertyGroup):
        if issubclass(cls, base):
            return base.bl_rna_get_subclass_py(identifier)
    return None


def _validate_registration_integrity():
    """Reject an idempotent-register fast path backed by stale RNA state."""

    errors = []
    for cls in CLASSES:
        identifier = _expected_rna_identifier(cls)
        registered = _registered_rna_class(cls)
        if registered is None:
            errors.append(f"missing RNA class {identifier}")
        elif registered is not cls:
            errors.append(f"stale RNA class {identifier}")

    window_manager_properties = bpy.types.WindowManager.bl_rna.properties
    for property_name, expected_type in _WINDOW_MANAGER_POINTER_TYPES:
        prop = window_manager_properties.get(property_name)
        if prop is None or prop.type != "POINTER":
            errors.append(f"missing PointerProperty {property_name}")
            continue
        fixed_type = getattr(prop, "fixed_type", None)
        expected_rna = getattr(expected_type, "bl_rna", None)
        if fixed_type is None or expected_rna is None or fixed_type != expected_rna:
            errors.append(
                f"PointerProperty {property_name} targets a stale or incorrect type"
            )

    if errors:
        details = "; ".join(errors)
        raise RuntimeError(f"Character Designer registration is inconsistent: {details}.")


def register():
    centerline_registered = hasattr(bpy.types.WindowManager, "character_designer")
    delta_registered = hasattr(
        bpy.types.WindowManager,
        "character_designer_delta",
    )
    references_registered = hasattr(
        bpy.types.WindowManager,
        "character_designer_references",
    )
    spline_ik_registered = hasattr(
        bpy.types.WindowManager,
        "character_designer_spline_ik",
    )
    limb_ik_registered = hasattr(
        bpy.types.WindowManager,
        "character_designer_limb_ik",
    )
    forearm_twist_registered = hasattr(bpy.types.WindowManager, "character_designer_forearm_twist")
    hair_bones_registered = hasattr(bpy.types.WindowManager, "character_designer_hair_bones")
    skirt_registered = hasattr(bpy.types.WindowManager, "character_designer_skirt")
    animation_registered = hasattr(bpy.types.WindowManager, "character_designer_animation")
    registration_state = (
        centerline_registered,
        delta_registered,
        limb_ik_registered,
        spline_ik_registered,
        references_registered,
        forearm_twist_registered,
        hair_bones_registered,
        skirt_registered,
        animation_registered,
    )
    if all(registration_state):
        _validate_registration_integrity()
        register_animation_runtime()
        register_reference_view_handlers()
        register_limb_ik_viewport_handler()
        register_forearm_twist_runtime()
        _register_workspace_filter_guard()
        _register_source_watch()
        return
    if any(registration_state):
        raise RuntimeError("Character Designer is only partially registered.")

    registered = []
    added_properties = []
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.WindowManager.character_designer = PointerProperty(
            type=CharacterDesignerState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer")
        bpy.types.WindowManager.character_designer_hair_bones = PointerProperty(
            type=CharacterDesignerHairBonesState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer_hair_bones")
        bpy.types.WindowManager.character_designer_skirt = PointerProperty(
            type=CharacterDesignerSkirtState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer_skirt")
        bpy.types.WindowManager.character_designer_animation = PointerProperty(
            type=CharacterDesignerAnimationState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer_animation")
        bpy.types.WindowManager.character_designer_delta = PointerProperty(
            type=CharacterDesignerDeltaState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer_delta")
        bpy.types.WindowManager.character_designer_limb_ik = PointerProperty(
            type=CharacterDesignerLimbIKState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer_limb_ik")
        bpy.types.WindowManager.character_designer_forearm_twist = PointerProperty(
            type=CharacterDesignerForearmTwistState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer_forearm_twist")
        bpy.types.WindowManager.character_designer_spline_ik = PointerProperty(
            type=CharacterDesignerSplineIKState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer_spline_ik")
        bpy.types.WindowManager.character_designer_references = PointerProperty(
            type=CharacterDesignerReferenceState,
            options={"SKIP_SAVE"},
        )
        added_properties.append("character_designer_references")
        register_animation_runtime()
        register_reference_view_handlers()
        register_limb_ik_viewport_handler()
        register_forearm_twist_runtime()
        _register_workspace_filter_guard()
        _register_source_watch()
    except Exception:
        _stop_live_preview(settings=_settings(bpy.context), clear_capture=True)
        unregister_animation_runtime()
        unregister_forearm_twist_runtime()
        unregister_limb_ik_viewport_handler()
        unregister_reference_view_handlers()
        _unregister_workspace_filter_guard()
        _unregister_source_watch()
        for property_name in reversed(added_properties):
            if hasattr(bpy.types.WindowManager, property_name):
                delattr(bpy.types.WindowManager, property_name)
        for cls in reversed(registered):
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
        raise


def unregister():
    unregister_animation_runtime()
    stop_skirt_runtime()
    unregister_forearm_twist_runtime()
    _stop_live_preview(settings=_settings(bpy.context), clear_capture=True)
    stop_delta_symmetry_runtime(clear_capture=True)
    unregister_limb_ik_viewport_handler()
    unregister_reference_view_handlers()
    _unregister_workspace_filter_guard()
    _unregister_source_watch()
    if hasattr(bpy.types.WindowManager, "character_designer_skirt"):
        del bpy.types.WindowManager.character_designer_skirt
    if hasattr(bpy.types.WindowManager, "character_designer_animation"):
        del bpy.types.WindowManager.character_designer_animation
    if hasattr(bpy.types.WindowManager, "character_designer_hair_bones"):
        del bpy.types.WindowManager.character_designer_hair_bones
    if hasattr(bpy.types.WindowManager, "character_designer_references"):
        del bpy.types.WindowManager.character_designer_references
    if hasattr(bpy.types.WindowManager, "character_designer_spline_ik"):
        del bpy.types.WindowManager.character_designer_spline_ik
    if hasattr(bpy.types.WindowManager, "character_designer_limb_ik"):
        del bpy.types.WindowManager.character_designer_limb_ik
    if hasattr(bpy.types.WindowManager, "character_designer_forearm_twist"):
        del bpy.types.WindowManager.character_designer_forearm_twist
    if hasattr(bpy.types.WindowManager, "character_designer_delta"):
        del bpy.types.WindowManager.character_designer_delta
    if hasattr(bpy.types.WindowManager, "character_designer"):
        del bpy.types.WindowManager.character_designer
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


if __name__ == "__main__":
    register()
