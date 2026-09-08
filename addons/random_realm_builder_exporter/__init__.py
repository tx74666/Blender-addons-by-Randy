bl_info = {
    "name": "RR Helper",
    "author": "RandomRealm",
    "version": (0, 2, 5),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > RandomRealm",
    "description": "RandomRealm helper tools for Unity handoff, builder assets, and animation sync.",
    "category": "RandomRealm",
}

import json
import filecmp
import hashlib
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector

try:
    from . import rr_unity_uv_export as rr_unity_uv_export_contract
    from .rr_builder_constants import *
    from .rr_layout_snapshot import *
    from .rr_modeling_origin import *
    from .rr_point_bookmarks import *
    from .rr_naming import *
    from .rr_pbr_runtime import (
        pbr_bake_runtime_state_is_stale,
        request_pbr_bake_runtime_state_reset,
        reset_pbr_bake_runtime_state,
        reset_pbr_bake_runtime_state_deferred,
        reset_pbr_bake_runtime_state_on_load,
        set_pbr_bake_runtime_active,
        set_pbr_bake_runtime_pending,
    )
except ImportError:
    import rr_unity_uv_export as rr_unity_uv_export_contract
    from rr_builder_constants import *
    from rr_layout_snapshot import *
    from rr_modeling_origin import *
    from rr_point_bookmarks import *
    from rr_naming import *
    from rr_pbr_runtime import (
        pbr_bake_runtime_state_is_stale,
        request_pbr_bake_runtime_state_reset,
        reset_pbr_bake_runtime_state,
        reset_pbr_bake_runtime_state_deferred,
        reset_pbr_bake_runtime_state_on_load,
        set_pbr_bake_runtime_active,
        set_pbr_bake_runtime_pending,
    )


SYNCING_QUEUE_SETTINGS = False
SYNCING_QUEUE_ACTIVE_INDEX = False
SYNCING_ICON_LIGHT_EDITOR = False
SCENE_SELECTION_QUEUE_SYNC_ENABLED = False
LAST_SCENE_SELECTION_KEY = None
OBJECT_MANAGER_SELECTION_SYNCING = False
OBJECT_MANAGER_GROUP_NAME_SYNCING = False
OBJECT_MANAGER_GROUP_TYPE_SYNCING = False
OBJECT_MANAGER_REPAIR_ALLOWED = True
ICON_LIGHT_AUTOSAVE_SIGNATURES = {}
LAST_OBJECT_MANAGER_SELECTION_KEY = None
OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME = None
OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = None
OBJECT_MANAGER_DETAIL_SELECTION_KEY = None
OBJECT_MANAGER_LAST_ROW_CLICK_KEY = ""
OBJECT_MANAGER_LAST_ROW_CLICK_TIME = 0.0
OBJECT_MANAGER_RUNTIME_OBJECT_UIDS = set()
OBJECT_MANAGER_DUPLICATE_GUARD_READY = False
OBJECT_MANAGER_DUPLICATE_KEYMAPS = []
PREVIEW_COLLECTIONS = {}
RR_ANIMATION_BLEND_PATH = r"D:\Blender\Projects\Character\Animation\Animation.blend"
RR_ANIMATION_SOURCE_FOLDER = os.path.dirname(RR_ANIMATION_BLEND_PATH)
RR_UNITY_ANIMATION_IMPORT_FOLDER = os.path.join(UNITY_PROJECT_ROOT, "Assets", "Art", "Animation", "Character", "BlenderSync")
RR_ANIMATION_SYNC_TEMP = os.path.join(UNITY_PROJECT_ROOT, "Temp", "RandomRealmBlenderAnimationSync")
RR_ANIMATION_IMPORT_REQUEST_PATH = os.path.join(RR_ANIMATION_SYNC_TEMP, "blender_to_unity_import_request.json")
RR_ADDON_SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))
RR_ADDON_MODULE_NAME = (__package__ or os.path.basename(RR_ADDON_SOURCE_ROOT)).split(".")[0]
RR_ADDON_REFRESH_PENDING = False
RR_ADDON_REFRESH_LAST_STATE = False
RR_ADDON_REFRESH_LAST_ERROR = ""
UNITY_EXPORT_UV_LAYER_NAME = "RR_UnityExportUV"
UNITY_UV_EXPORT_CONTRACT_VERSION = rr_unity_uv_export_contract.CONTRACT_VERSION


def rr_addon_source_files():
    paths = []
    for root, directory_names, file_names in os.walk(RR_ADDON_SOURCE_ROOT):
        directory_names[:] = [name for name in directory_names if name != "__pycache__" and not name.startswith(".")]
        for file_name in file_names:
            if file_name.endswith(".py"):
                paths.append(os.path.join(root, file_name))
    return sorted(paths)


def rr_addon_source_signature():
    signature = []
    for path in rr_addon_source_files():
        try:
            stat = os.stat(path)
        except OSError:
            continue
        relative_path = os.path.relpath(path, RR_ADDON_SOURCE_ROOT).replace(os.sep, "/")
        signature.append((relative_path, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


RR_ADDON_LOADED_SIGNATURE = rr_addon_source_signature()


def rr_addon_source_changed():
    return rr_addon_source_signature() != RR_ADDON_LOADED_SIGNATURE


def validate_rr_addon_sources():
    for path in rr_addon_source_files():
        with open(path, "rb") as handle:
            compile(handle.read(), path, "exec")


def tag_rr_addon_view3d_redraw():
    try:
        window_manager = bpy.context.window_manager
    except Exception:
        return
    if window_manager is None:
        return
    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def reset_stale_icon_framing_state_deferred():
    scenes = getattr(bpy.data, "scenes", None)
    screens = getattr(bpy.data, "screens", None)
    if scenes is None or screens is None:
        return 0.1

    stale_framing = False
    for scene in scenes:
        settings = getattr(scene, "rr_builder_export_settings", None)
        if settings is None:
            continue
        stale_framing = stale_framing or settings.icon_framing_adjusting or settings.icon_framing_confirm_requested
        settings.icon_framing_adjusting = False
        settings.icon_framing_confirm_requested = False
    if stale_framing:
        for screen in screens:
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    area.header_text_set(None)
                    area.tag_redraw()
    return None


def rr_addon_change_watch_deferred():
    global RR_ADDON_REFRESH_LAST_STATE

    changed = rr_addon_source_changed()
    if changed != RR_ADDON_REFRESH_LAST_STATE:
        RR_ADDON_REFRESH_LAST_STATE = changed
        tag_rr_addon_view3d_redraw()
    return 1.0


def register_rr_addon_change_watch():
    global RR_ADDON_REFRESH_LAST_STATE

    RR_ADDON_REFRESH_LAST_STATE = rr_addon_source_changed()
    try:
        if not bpy.app.timers.is_registered(rr_addon_change_watch_deferred):
            bpy.app.timers.register(rr_addon_change_watch_deferred, first_interval=1.0, persistent=True)
    except Exception as exc:
        print(f"[RR Helper] Could not start add-on change watcher: {exc}")


def unregister_rr_addon_change_watch():
    try:
        if bpy.app.timers.is_registered(rr_addon_change_watch_deferred):
            bpy.app.timers.unregister(rr_addon_change_watch_deferred)
    except Exception:
        pass


def rr_addon_reload_deferred():
    global RR_ADDON_REFRESH_PENDING, RR_ADDON_REFRESH_LAST_ERROR

    import addon_utils
    import importlib
    import sys
    import traceback

    module_name = RR_ADDON_MODULE_NAME
    old_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == module_name or name.startswith(f"{module_name}.")
    }
    old_main = old_modules.get(module_name)
    persistent = bool(getattr(old_main, "__addon_persistent__", False)) if old_main is not None else False

    try:
        addon_utils.disable(module_name, default_set=False, refresh_handled=True)
        for name in old_modules:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        reloaded = addon_utils.enable(
            module_name,
            default_set=False,
            persistent=persistent,
            refresh_handled=True,
        )
        if reloaded is None:
            raise RuntimeError("Blender could not enable the refreshed RR Helper module.")
        print("[RR Helper] Add-on refreshed successfully.")
    except Exception as exc:
        traceback.print_exc()
        for name in list(sys.modules):
            if name == module_name or name.startswith(f"{module_name}."):
                sys.modules.pop(name, None)
        sys.modules.update(old_modules)
        if old_main is not None:
            try:
                old_main.RR_ADDON_REFRESH_PENDING = False
                old_main.RR_ADDON_REFRESH_LAST_ERROR = str(exc)
                old_main.register()
                old_main.__addon_enabled__ = True
            except Exception:
                traceback.print_exc()
        RR_ADDON_REFRESH_PENDING = False
        RR_ADDON_REFRESH_LAST_ERROR = str(exc)
    return None


def rr_helper_open_path(path):
    if not path:
        return False
    if os.name == "nt":
        os.startfile(path)
        return True
    bpy.ops.wm.path_open(filepath=path)
    return True


def rr_helper_reveal_folder(path):
    if not path:
        return False
    os.makedirs(path, exist_ok=True)
    return rr_helper_open_path(path)


def infer_export_asset_type(root, asset_id):
    if str(asset_id or "").lower().startswith("glasswall_"):
        return "InnerWall"

    asset_type = infer_asset_type(asset_id)
    if asset_type != "Unknown":
        return asset_type

    if is_object_manager_assembly_root(root) and "door" in (asset_id or "").lower():
        return "Wall"

    return asset_type


def infer_asset_category(root, asset_type):
    if object_manager_variant_group_root(root):
        if asset_type == "Door" or asset_type in PROP_EXPORT_TYPES:
            return "Props"
        return ""

    if is_object_manager_assembly_root(root):
        return "Props"

    if asset_type == "Door" or asset_type in PROP_EXPORT_TYPES:
        return "Props"

    return ""


def collider_name_matches_root(obj, root):
    if obj is None or root is None or not is_collision_helper_name(obj.name):
        return False

    return collision_target_name_key(obj.name) == normalized_name_key(root.name)


def is_collision_helper(obj):
    return (
        bool(obj.get("rr_builder_collision_helper"))
        or bool(obj.get("rr_collider_target"))
        or is_collision_helper_name(obj.name)
    )


def is_object_manager_assembly_root(obj):
    return obj is not None and bool(obj.get(OBJECT_MANAGER_ASSEMBLY_ROOT_PROP))


def normalize_object_manager_assembly_type(value):
    value = str(value or "").strip().upper()
    allowed = {item[0] for item in OBJECT_MANAGER_ASSEMBLY_TYPE_ITEMS}
    return value if value in allowed else OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT


def object_manager_assembly_type(root):
    if root is None:
        return OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT
    return normalize_object_manager_assembly_type(root.get(OBJECT_MANAGER_ASSEMBLY_TYPE_PROP, OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT))


def object_manager_assembly_type_label(root):
    group_type = object_manager_assembly_type(root)
    for identifier, label, _description in OBJECT_MANAGER_ASSEMBLY_TYPE_ITEMS:
        if identifier == group_type:
            return label
    return "Assembly"


def is_object_manager_variant_group(root):
    group_root = object_manager_assembly_root_for_object(root) or root
    return is_object_manager_assembly_root(group_root) and object_manager_assembly_type(group_root) == "VARIANTS"


def set_object_manager_assembly_type(root, group_type):
    if root is None:
        return OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT

    group_type = normalize_object_manager_assembly_type(group_type)
    root[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = group_type
    assembly_id = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
    if assembly_id:
        for obj in bpy.data.objects:
            if obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id:
                obj[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = group_type
    return group_type


def repair_object_manager_assembly_root(root, source=None):
    if root is None or root.type not in {"EMPTY", "MESH"}:
        return None

    root[OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    assembly_id = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) or (source.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) if source is not None else "")
    if not assembly_id:
        assembly_id = f"assembly_recovered_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    root[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = assembly_id

    display_name = sanitize_optional_id(root.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP, ""))
    if not display_name and source is not None:
        display_name = sanitize_optional_id(source.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP, ""))
    if not display_name:
        display_name = sanitize_id(root.name)
    root[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name

    group_type = normalize_object_manager_assembly_type(root.get(OBJECT_MANAGER_ASSEMBLY_TYPE_PROP, ""))
    if source is not None:
        group_type = normalize_object_manager_assembly_type(source.get(OBJECT_MANAGER_ASSEMBLY_TYPE_PROP, group_type))
    root[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = group_type

    for obj in bpy.data.objects:
        if obj is None or obj.type not in {"EMPTY", "MESH"}:
            continue
        same_id = obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id
        same_root_name = obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP) == root.name
        same_display = (
            display_name
            and obj.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP) == display_name
            and (
                obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
                or obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP)
            )
        )
        if same_id or same_root_name or same_display or obj == root:
            obj[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = assembly_id
            obj[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
            obj[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = group_type
            if obj != root:
                obj[OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = root.name

    return root


def object_manager_display_name(root):
    if root is None:
        return ""
    if is_object_manager_assembly_root(root):
        stored = sanitize_optional_id(root.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP, ""))
        if stored:
            return stored
    return sanitize_id(root.name)


def export_asset_id(root):
    if root is None:
        return ""
    return object_manager_display_name(root)


def export_previous_ids(root):
    if root is None:
        return []

    raw = root.get(EXPORT_PREVIOUS_IDS_PROP, "")
    if isinstance(raw, str):
        try:
            values = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            values = [raw]
    elif isinstance(raw, (list, tuple)):
        values = raw
    else:
        values = []

    previous = []
    seen = set()
    for value in values:
        clean = sanitize_optional_id(str(value or ""))
        key = clean.lower()
        if not clean or key in seen:
            continue
        previous.append(clean)
        seen.add(key)
    return previous


def ensure_export_identity(root, current_id=None):
    if root is None:
        return "", []

    current_id = sanitize_id(current_id or export_asset_id(root))
    stable_id = str(root.get(EXPORT_STABLE_ID_PROP, "") or "").strip()
    if not stable_id:
        stable_id = f"rr_asset_{uuid.uuid4().hex}"
        root[EXPORT_STABLE_ID_PROP] = stable_id

    previous = export_previous_ids(root)
    previous_keys = {value.lower() for value in previous}
    last_id = sanitize_optional_id(str(root.get(EXPORT_LAST_ID_PROP, "") or ""))
    if last_id and last_id.lower() != current_id.lower() and last_id.lower() not in previous_keys:
        previous.append(last_id)

    previous = [value for value in previous if value.lower() != current_id.lower()]
    root[EXPORT_LAST_ID_PROP] = current_id
    root[EXPORT_PREVIOUS_IDS_PROP] = json.dumps(previous, ensure_ascii=True)
    return stable_id, previous


def clear_export_identity(root):
    if root is None:
        return
    for prop in (EXPORT_STABLE_ID_PROP, EXPORT_LAST_ID_PROP, EXPORT_PREVIOUS_IDS_PROP):
        if prop in root:
            del root[prop]


def export_identity_root_label(root):
    if root is None:
        return "<missing>"

    object_name = str(getattr(root, "name", "") or "<unnamed>")
    asset_id = export_asset_id(root)
    if not asset_id or asset_id == object_name:
        return f"'{object_name}'"
    return f"'{asset_id}' (object '{object_name}')"


_EXPORT_IDENTITY_LOOKUP_CACHE = None


def _memoize_export_identity_lookup(function):
    """Reuse pure lookups only inside one repair-disabled read-only scope.

    No values survive the scope. In particular, later identity checks still
    discover edits made by exports, renders, callbacks, and asset renames.
    Lists are copied so callers cannot mutate another lookup's cached result.
    """
    def lookup(*args, **kwargs):
        cache = _EXPORT_IDENTITY_LOOKUP_CACHE
        if cache is None or OBJECT_MANAGER_REPAIR_ALLOWED:
            return function(*args, **kwargs)
        key = (function.__name__, args, tuple(sorted(kwargs.items())))
        if key not in cache:
            value = function(*args, **kwargs)
            cache[key] = list(value) if isinstance(value, list) else value
        value = cache[key]
        return list(value) if isinstance(value, list) else value

    lookup.__name__ = function.__name__
    lookup.__doc__ = function.__doc__
    lookup.__wrapped__ = function
    return lookup


def live_export_identity_roots(extra_roots=None):
    """Return actual asset roots without repairing or changing Object Manager data."""
    global OBJECT_MANAGER_REPAIR_ALLOWED, _EXPORT_IDENTITY_LOOKUP_CACHE

    previous_repair_state = OBJECT_MANAGER_REPAIR_ALLOWED
    previous_lookup_cache = _EXPORT_IDENTITY_LOOKUP_CACHE
    OBJECT_MANAGER_REPAIR_ALLOWED = False
    _EXPORT_IDENTITY_LOOKUP_CACHE = {}
    try:
        candidates = get_export_roots(list(bpy.data.objects))
        candidates.extend(root for root in (extra_roots or []) if root is not None)
        expanded = expand_related_export_roots(candidates)
    finally:
        _EXPORT_IDENTITY_LOOKUP_CACHE = previous_lookup_cache
        OBJECT_MANAGER_REPAIR_ALLOWED = previous_repair_state

    roots = []
    seen = set()
    for candidate in expanded:
        if (
            candidate is None
            or candidate in seen
            or candidate.type not in {"EMPTY", "MESH"}
            or is_collision_helper(candidate)
            or not get_export_asset_meshes(candidate)
        ):
            continue
        roots.append(candidate)
        seen.add(candidate)
    return roots


def collect_export_identity_conflicts(root, candidate_roots=None):
    """Collect identity aliases that would let one live asset overwrite another."""
    if root is None:
        return []

    target_roots = expand_related_export_roots([root]) or [root]
    roots = live_export_identity_roots(
        list(candidate_roots or []) + target_roots
        if candidate_roots is not None
        else target_roots
    )
    target_set = set(target_roots)

    stable_id_roots = {}
    current_id_roots = {}
    for candidate in roots:
        stable_id = str(candidate.get(EXPORT_STABLE_ID_PROP, "") or "").strip()
        if stable_id:
            stable_id_roots.setdefault(stable_id.casefold(), []).append(candidate)

        current_id = sanitize_optional_id(export_asset_id(candidate))
        if current_id:
            current_id_roots.setdefault(current_id.casefold(), []).append(candidate)

    conflicts = []
    for shared_roots in stable_id_roots.values():
        unique_roots = list(dict.fromkeys(shared_roots))
        if len(unique_roots) < 2 or not target_set.intersection(unique_roots):
            continue
        unique_roots.sort(key=lambda item: (export_asset_id(item).casefold(), item.name.casefold()))
        stable_id = str(unique_roots[0].get(EXPORT_STABLE_ID_PROP, "") or "").strip()
        labels = ", ".join(export_identity_root_label(item) for item in unique_roots)
        conflicts.append(
            f"stable ID '{stable_id}' is shared by live export roots: {labels}"
        )

    for current_id, shared_roots in current_id_roots.items():
        unique_roots = list(dict.fromkeys(shared_roots))
        if len(unique_roots) < 2 or not target_set.intersection(unique_roots):
            continue
        unique_roots.sort(key=lambda item: (export_asset_id(item).casefold(), item.name.casefold()))
        labels = ", ".join(export_identity_root_label(item) for item in unique_roots)
        conflicts.append(
            f"current export ID '{current_id}' is shared by live export roots: {labels}"
        )

    for owner in roots:
        for previous_id in export_previous_ids(owner):
            holders = [
                holder
                for holder in current_id_roots.get(previous_id.casefold(), [])
                if holder is not owner
            ]
            if not holders or (owner not in target_set and not target_set.intersection(holders)):
                continue
            holders.sort(key=lambda item: (export_asset_id(item).casefold(), item.name.casefold()))
            labels = ", ".join(export_identity_root_label(item) for item in holders)
            conflicts.append(
                f"previous ID '{previous_id}' on {export_identity_root_label(owner)} "
                f"still belongs to live export root(s): {labels}"
            )

    return list(dict.fromkeys(conflicts))


def validate_export_identity(root, candidate_roots=None):
    conflicts = collect_export_identity_conflicts(root, candidate_roots)
    if conflicts:
        details = "\n- ".join(conflicts)
        raise RuntimeError(
            "Export identity conflict. Use Duplicate Variant for a new asset identity; "
            f"no files were written.\n- {details}"
        )
    return True


def snapshot_export_identity(root):
    if root is None:
        return "", []
    return ensure_export_identity(root, export_asset_id(root))


def rename_export_asset_preserving_identity(root, new_name):
    global OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME

    if root is None:
        return ""

    snapshot_export_identity(root)
    if is_object_manager_assembly_root(root):
        return set_object_manager_assembly_display_name(root, new_name)

    old_name = root.name
    desired_name = sanitize_id(new_name)
    if desired_name != old_name and desired_name in bpy.data.objects:
        desired_name = unique_object_name(desired_name)
    root.name = desired_name
    if root.type == "MESH" and root.data is not None:
        root.data.name = unique_datablock_name(bpy.data.meshes, f"{root.name}_Mesh")

    if root.name != old_name:
        for candidate in bpy.data.objects:
            if candidate.get(OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP) == old_name:
                candidate[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = root.name
            if candidate.get(OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP) == old_name:
                candidate[OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP] = root.name
            if candidate.get("rr_collider_target") == old_name:
                candidate["rr_collider_target"] = root.name
        for scene in bpy.data.scenes:
            settings = getattr(scene, "rr_builder_export_settings", None)
            if settings is None:
                continue
            for item in settings.export_queue:
                if item.object_name == old_name:
                    item.object_name = root.name
                if item.icon_preview_root_name == old_name:
                    item.icon_preview_root_name = root.name
        if OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME == old_name:
            OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME = root.name
        if OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME == old_name:
            OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = root.name
    return root.name


def object_manager_row_requests_rename(target_name, event):
    """Treat a native or closely repeated row click as a rename gesture."""
    global OBJECT_MANAGER_LAST_ROW_CLICK_KEY, OBJECT_MANAGER_LAST_ROW_CLICK_TIME

    now = time.monotonic()
    native_double_click = event is not None and getattr(event, "value", "") == "DOUBLE_CLICK"
    timed_double_click = (
        target_name == OBJECT_MANAGER_LAST_ROW_CLICK_KEY
        and now - OBJECT_MANAGER_LAST_ROW_CLICK_TIME <= 0.45
    )
    if native_double_click or timed_double_click:
        OBJECT_MANAGER_LAST_ROW_CLICK_KEY = ""
        OBJECT_MANAGER_LAST_ROW_CLICK_TIME = 0.0
        return True

    OBJECT_MANAGER_LAST_ROW_CLICK_KEY = target_name
    OBJECT_MANAGER_LAST_ROW_CLICK_TIME = now
    return False


def unique_export_group_name(base_name, ignore_root=None):
    base_name = sanitize_id(base_name)
    used = {
        object_manager_display_name(obj).lower()
        for obj in bpy.data.objects
        if obj is not ignore_root and is_object_manager_assembly_root(obj)
    }
    if base_name.lower() not in used:
        return base_name

    index = 1
    while True:
        candidate = f"{base_name}_{index:03d}"
        if candidate.lower() not in used:
            return candidate
        index += 1


def set_object_manager_assembly_display_name(root, name):
    if root is None:
        return ""
    display_name = unique_export_group_name(name, root)
    root[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
    assembly_id = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
    if assembly_id:
        for obj in bpy.data.objects:
            if obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id:
                obj[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
    return display_name


def ensure_object_manager_assembly_display_name_prop(root):
    if root is None:
        return ""

    display_name = object_manager_display_name(root)
    if not sanitize_optional_id(root.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP, "")):
        root[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
    return display_name


def ensure_object_manager_panel_display_name(root):
    if root is None:
        return ""
    if not sanitize_optional_id(root.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP, "")):
        fallback = unique_export_group_name("Unsigned_001", root)
        return set_object_manager_assembly_display_name(root, fallback)
    return ensure_object_manager_assembly_display_name_prop(root)


@_memoize_export_identity_lookup
def direct_object_manager_member_objects(root):
    if root is None:
        return []

    members = []
    seen = set()
    assembly_id = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)

    def add(obj):
        if obj is None or obj == root or obj in seen:
            return
        if obj.type not in {"EMPTY", "MESH"}:
            return
        members.append(obj)
        seen.add(obj)

    for obj in list(root.children_recursive):
        add(obj)

    for obj in bpy.data.objects:
        if obj.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP) == root.name:
            add(obj)
            continue
        if obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP) == root.name:
            add(obj)
            continue
        if assembly_id and obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id and not is_object_manager_assembly_root(obj):
            add(obj)

    return members


@_memoize_export_identity_lookup
def object_manager_direct_child_objects(root):
    if root is None:
        return []

    children = []
    seen = set()
    assembly_id = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)

    def add(obj):
        if obj is None or obj == root or obj in seen:
            return
        if obj.type not in {"EMPTY", "MESH"}:
            return
        children.append(obj)
        seen.add(obj)

    for obj in list(root.children):
        add(obj)

    for obj in bpy.data.objects:
        if obj == root:
            continue
        if obj.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP) == root.name:
            add(obj)
            continue
        if obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP) == root.name:
            add(obj)
            continue
        if assembly_id and obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id and not is_object_manager_assembly_root(obj):
            add(obj)

    child_set = set(children)
    direct = []
    for obj in children:
        parent = obj.parent
        nested = False
        while parent is not None and parent != root:
            if parent in child_set:
                nested = True
                break
            parent = parent.parent
        if not nested:
            direct.append(obj)
    return direct


@_memoize_export_identity_lookup
def object_manager_parent_group_roots(obj):
    if obj is None:
        return []

    roots = []
    seen = set()

    def add(root):
        if root is None or root == obj or root in seen:
            return
        if not is_object_manager_assembly_root(root):
            return
        roots.append(root)
        seen.add(root)

    parent_name = obj.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP)
    if parent_name:
        add(bpy.data.objects.get(parent_name))

    member_root_name = obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP)
    if member_root_name:
        add(bpy.data.objects.get(member_root_name))

    if obj.parent is not None:
        add(obj.parent)

    # Explicit root links and the direct Blender parent were already added.
    # Every remaining direct-child match must come from the shared assembly ID;
    # avoid rebuilding every unrelated group's child list for each scene object.
    assembly_id = obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
    if not assembly_id or obj.type not in {"EMPTY", "MESH"} or is_object_manager_assembly_root(obj):
        return roots

    for root in bpy.data.objects:
        if root is obj or root in seen or not is_object_manager_assembly_root(root):
            continue
        if root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) != assembly_id:
            continue
        if obj in object_manager_direct_child_objects(root):
            add(root)

    return roots


def object_manager_top_group_root(root):
    current = root
    seen = set()
    while current is not None and current not in seen:
        seen.add(current)
        parents = object_manager_parent_group_roots(current)
        parents = [parent for parent in parents if parent is not None and parent != current]
        if not parents:
            return current
        current = parents[0]
    return root


@_memoize_export_identity_lookup
def object_manager_member_candidate_objects(root):
    candidates = []
    seen = set()

    def add(obj):
        if obj is None or obj in seen:
            return
        candidates.append(obj)
        seen.add(obj)

    if is_object_manager_assembly_root(root) and root.type == "MESH" and not is_collision_helper(root):
        add(root)
        for child in list(root.children_recursive):
            add(child)

    for obj in direct_object_manager_member_objects(root):
        add(obj)
        for child in list(obj.children_recursive):
            add(child)

    return candidates


@_memoize_export_identity_lookup
def object_manager_assembly_root_for_object(obj):
    if obj is None:
        return None

    if is_object_manager_assembly_root(obj):
        return obj

    current = obj
    while current is not None:
        root_name = current.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP)
        if root_name:
            root = bpy.data.objects.get(root_name)
            if is_object_manager_assembly_root(root):
                return root
            if root is not None and (
                root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
                or root.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP)
                or current.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
            ):
                if not OBJECT_MANAGER_REPAIR_ALLOWED:
                    return None
                return repair_object_manager_assembly_root(root, current)

        assembly_id = current.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
        if assembly_id:
            fallback = None
            for candidate in bpy.data.objects:
                if (
                    candidate is not current
                    and is_object_manager_assembly_root(candidate)
                    and candidate.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id
                ):
                    return candidate
                if (
                    fallback is None
                    and candidate is not current
                    and candidate.type in {"EMPTY", "MESH"}
                    and candidate.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id
                ):
                    fallback = candidate
            if fallback is not None:
                if not OBJECT_MANAGER_REPAIR_ALLOWED:
                    return None
                return repair_object_manager_assembly_root(fallback, current)

        display_name = current.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP)
        if display_name:
            fallback = None
            match_count = 0
            for candidate in bpy.data.objects:
                if (
                    candidate.type not in {"EMPTY", "MESH"}
                    or candidate.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP) != display_name
                ):
                    continue
                if not (
                    candidate.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
                    or candidate.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP)
                    or is_object_manager_assembly_root(candidate)
                ):
                    continue
                match_count += 1
                if is_object_manager_assembly_root(candidate):
                    return candidate
                if fallback is None:
                    fallback = candidate
            if fallback is not None and match_count > 1:
                if not OBJECT_MANAGER_REPAIR_ALLOWED:
                    return None
                return repair_object_manager_assembly_root(fallback, current)

        parent = current.parent
        if is_object_manager_assembly_root(parent):
            return parent
        current = parent

    return None


def selected_object_manager_assembly_root(context):
    if context is None:
        return None

    selected = set(context.selected_objects or [])
    detail_root = bpy.data.objects.get(OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME or "")
    if detail_root is not None and is_object_manager_assembly_root(detail_root):
        detail_members = set(object_manager_selection_objects(detail_root))
        detail_members.add(detail_root)
        if not selected or selected.issubset(detail_members):
            return detail_root

    remembered_root = bpy.data.objects.get(OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME or "")
    if (
        remembered_root is not None
        and is_object_manager_assembly_root(remembered_root)
        and len(selected) > 1
    ):
        remembered_members = set(object_manager_selection_objects(remembered_root))
        remembered_members.add(remembered_root)
        if selected.issubset(remembered_members):
            return remembered_root

    active = context.view_layer.objects.active if context.view_layer else context.object
    if active is not None and (not selected or active in selected):
        root = object_manager_assembly_root_for_object(active)
        if root is not None:
            return root

    for obj in context.selected_objects or []:
        root = object_manager_assembly_root_for_object(obj)
        if root is not None:
            return root

    return None


def object_manager_member_objects(root):
    if root is None:
        return []

    members = []
    seen = set()

    def add(obj):
        if obj is None or obj in seen:
            return
        members.append(obj)
        seen.add(obj)

    if is_object_manager_assembly_root(root) and root.type == "MESH" and not is_collision_helper(root):
        add(root)

    for obj in direct_object_manager_member_objects(root):
        add(obj)

    for obj in get_collision_meshes(root):
        add(obj)

    return members


def object_manager_selection_objects(root):
    """Return selectable leaf objects for a group, including nested groups."""
    members = []
    seen_objects = set()
    seen_roots = set()

    def add(obj):
        if obj is None or obj in seen_objects:
            return
        if obj.type not in {"EMPTY", "MESH"} or obj.hide_select or obj.hide_get():
            return
        members.append(obj)
        seen_objects.add(obj)

    def visit(group_root):
        if group_root is None or group_root in seen_roots:
            return
        seen_roots.add(group_root)

        if group_root.type == "MESH" and not is_collision_helper(group_root):
            add(group_root)

        for member in object_manager_member_objects(group_root):
            if is_object_manager_assembly_root(member):
                visit(member)
            else:
                add(member)

    visit(root)
    return members


def select_object_manager_assembly(context, root):
    global OBJECT_MANAGER_SELECTION_SYNCING
    global OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_KEY

    if context is None or root is None:
        return False

    OBJECT_MANAGER_SELECTION_SYNCING = True
    try:
        bpy.ops.object.select_all(action="DESELECT")
        members = object_manager_selection_objects(root)
        for obj in members:
            obj.select_set(True)
        active = None
        active_member_name = root.get(OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP)
        if active_member_name:
            active = bpy.data.objects.get(active_member_name)
        if active not in members:
            active = members[0] if members else None
        if active is not None:
            context.view_layer.objects.active = active
        elif not root.hide_get() and not root.hide_select:
            root.select_set(True)
            context.view_layer.objects.active = root
    finally:
        OBJECT_MANAGER_SELECTION_SYNCING = False

    OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME = root.name
    OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = None
    OBJECT_MANAGER_DETAIL_SELECTION_KEY = None
    return True


def deselect_object_manager_assembly(context, root):
    global OBJECT_MANAGER_SELECTION_SYNCING

    if context is None or root is None:
        return False

    OBJECT_MANAGER_SELECTION_SYNCING = True
    try:
        for obj in object_manager_selection_objects(root):
            if obj.select_get():
                obj.select_set(False)
        if root.select_get():
            root.select_set(False)
    finally:
        OBJECT_MANAGER_SELECTION_SYNCING = False

    remember_object_manager_detail_selection(context, root)
    return True


def select_object_manager_entry_for_rename(context, target, root=None):
    """Keep the double-clicked tree entry selected while its rename dialog is open."""
    global OBJECT_MANAGER_SELECTION_SYNCING

    if context is None or target is None:
        return False
    if is_object_manager_assembly_root(target):
        return select_object_manager_assembly(context, target)

    if root is None or not is_object_manager_assembly_root(root):
        root = object_manager_selection_owner_root(target)

    OBJECT_MANAGER_SELECTION_SYNCING = True
    try:
        if root is not None:
            scope = set(object_manager_member_objects(root))
            scope.add(root)
            for selected in list(context.selected_objects):
                if selected not in scope:
                    selected.select_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
    finally:
        OBJECT_MANAGER_SELECTION_SYNCING = False

    if root is not None:
        remember_object_manager_detail_selection(context, root)
    return True


def unique_object_name(base_name):
    base_name = sanitize_id(base_name) or "Assembly"
    if base_name not in bpy.data.objects:
        return base_name

    index = 1
    while True:
        candidate = f"{base_name}.{index:03d}"
        if candidate not in bpy.data.objects:
            return candidate
        index += 1


def unique_datablock_name(datablocks, base_name):
    base_name = sanitize_id(base_name) or "Data"
    if base_name not in datablocks:
        return base_name

    index = 1
    while True:
        candidate = f"{base_name}.{index:03d}"
        if candidate not in datablocks:
            return candidate
        index += 1


@_memoize_export_identity_lookup
def get_asset_meshes(root):
    if root is None:
        return []

    candidates = []
    if is_object_manager_assembly_root(root):
        candidates.extend(object_manager_member_candidate_objects(root))
    else:
        candidates.append(root)
        candidates.extend(list(root.children_recursive))

    meshes = []
    seen = set()
    for obj in candidates:
        if obj in seen:
            continue
        seen.add(obj)
        if obj.type == "MESH" and not is_collision_helper(obj):
            meshes.append(obj)
    return meshes


@_memoize_export_identity_lookup
def get_standalone_asset_meshes(root):
    if root is None:
        return []

    candidates = [root]
    candidates.extend(list(root.children_recursive))
    meshes = []
    seen = set()
    for obj in candidates:
        if obj in seen:
            continue
        seen.add(obj)
        if obj.type == "MESH" and not is_collision_helper(obj):
            meshes.append(obj)
    return meshes


@_memoize_export_identity_lookup
def get_export_asset_meshes(root):
    if root is None:
        return []
    if object_manager_variant_group_root(root) == root:
        return get_standalone_asset_meshes(root)
    return get_asset_meshes(root)


def get_collision_meshes(root):
    if root is None:
        return []

    meshes = []
    seen = set()
    asset_meshes = get_asset_meshes(root)
    asset_mesh_names = {obj.name for obj in asset_meshes}

    def add_collision_mesh(obj):
        if obj is None or obj.type != "MESH" or obj in seen or not is_collision_helper(obj):
            return
        meshes.append(obj)
        seen.add(obj)

    if is_object_manager_assembly_root(root):
        collision_candidates = object_manager_member_candidate_objects(root)
    else:
        collision_candidates = list(root.children_recursive)

    for obj in collision_candidates:
        add_collision_mesh(obj)

    for obj in bpy.data.objects:
        collider_target = obj.get("rr_collider_target")
        if collider_target:
            if collider_target == root.name or collider_target in asset_mesh_names:
                add_collision_mesh(obj)
            continue

        if collider_name_matches_root(obj, root):
            add_collision_mesh(obj)

    return meshes


def has_candidate_parent(obj, candidates):
    parent = obj.parent
    while parent is not None:
        if parent in candidates:
            return True
        parent = parent.parent
    return False


def get_export_roots(objects):
    roots = []
    seen = set()
    for obj in objects:
        root = object_manager_assembly_root_for_object(obj) or obj
        if root is None or root in seen:
            continue
        roots.append(root)
        seen.add(root)

    candidates = [
        obj for obj in roots
        if obj is not None
        and obj.type in {"EMPTY", "MESH"}
        and not is_collision_helper(obj)
        and get_asset_meshes(obj)
    ]
    return [
        obj for obj in candidates
        if not has_candidate_parent(obj, candidates)
    ]


def get_context_export_roots(context):
    if context is None:
        return []

    candidates = []
    seen = set()

    def add(obj):
        if obj is not None and obj not in seen:
            candidates.append(obj)
            seen.add(obj)

    for obj in getattr(context, "selected_objects", None) or []:
        add(obj)
    add(getattr(context, "edit_object", None))
    add(getattr(context, "object", None))
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        active = view_layer.objects.active
        add(active)
        if active is not None and active.type == "LIGHT":
            add(icon_light_root_from_light(active))
    return get_export_roots(candidates)


def ensure_object_mode(context):
    if context is None or getattr(context, "mode", "OBJECT") == "OBJECT":
        return True
    active = getattr(context, "object", None)
    if active is None:
        return False
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        return False
    return getattr(context, "mode", "OBJECT") == "OBJECT"


def selected_objects_for_object_manager_assembly(context):
    if context is None:
        return []

    candidates = [
        obj for obj in context.selected_objects
        if obj is not None
        and obj.type in {"EMPTY", "MESH"}
        and not is_collision_helper(obj)
    ]
    candidate_set = set(candidates)
    members = [
        obj for obj in candidates
        if not has_candidate_parent(obj, candidate_set)
    ]
    return members


def object_manager_bounds_min_max(objects):
    corners = []
    seen = set()
    for obj in objects:
        for mesh in get_asset_meshes(obj):
            if mesh in seen:
                continue
            seen.add(mesh)
            corners.extend(mesh.matrix_world @ Vector(corner) for corner in mesh.bound_box)

    if not corners:
        raise RuntimeError("Selected objects have no exportable mesh bounds.")

    min_v = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    max_v = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
    return min_v, max_v


def object_manager_world_bounds(objects):
    min_v, max_v = object_manager_bounds_min_max(objects)
    center = (min_v + max_v) * 0.5
    size = max_v - min_v
    return center, size


def dimensions_cm_from_size(size):
    return (
        max(1, int(round(size.x * 100.0))),
        max(1, int(round(size.y * 100.0))),
        max(1, int(round(size.z * 100.0))),
    )


def export_name_dimensions_for_objects(objects):
    _center, size = object_manager_world_bounds(objects)
    return dimensions_cm_from_size(size)


def shared_copy_stripped_base(objects):
    bases = []
    for obj in objects:
        if obj is None:
            continue
        bases.append(sanitize_optional_id(strip_blender_copy_suffix(obj.name)))
    unique = [base for base in dict.fromkeys(bases) if base]
    return unique[0] if len(unique) == 1 else ""


def object_manager_group_base_name(members, active, name="", group_type=OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT):
    group_type = normalize_object_manager_assembly_type(group_type)
    base = (name or "").strip()
    if group_type == "VARIANTS":
        if base:
            clean_base = sanitize_optional_id(strip_blender_copy_suffix(base))
        else:
            clean_base = shared_copy_stripped_base(members)
            if not clean_base:
                source = active.name if active in members else members[0].name
                clean_base = sanitize_optional_id(strip_blender_copy_suffix(source))
        if clean_base.lower().endswith("_variants"):
            return sanitize_id(clean_base)
        return sanitize_id(f"{clean_base}_Variants")

    if base:
        clean_base = sanitize_optional_id(strip_dimension_suffix(base))
        if clean_base:
            width, depth, height = export_name_dimensions_for_objects(members)
            return sanitize_id(f"{clean_base}_{width}x{depth}x{height}")

    base_source = active.name if active in members else members[0].name
    return f"{strip_blender_copy_suffix(base_source)}_ExportGroup"


def link_object_to_context_collection(context, obj, source_objects=()):
    collection = None
    active = context.view_layer.objects.active if context and context.view_layer else None
    if active is not None and active.users_collection:
        collection = active.users_collection[0]
    if collection is None:
        for source in source_objects or ():
            if source is not None and source.users_collection:
                collection = source.users_collection[0]
                break
    if collection is None and context is not None and context.scene is not None:
        collection = context.scene.collection
    if collection is not None and obj.name not in collection.objects:
        try:
            collection.objects.link(obj)
        except RuntimeError:
            pass
    return collection


def parent_object_keep_world(obj, parent):
    if obj is None:
        return
    matrix = obj.matrix_world.copy()
    obj.parent = parent
    obj.matrix_parent_inverse = parent.matrix_world.inverted_safe()
    obj.matrix_world = matrix


def create_object_manager_container_root(context, base, members, anchor=None):
    root = bpy.data.objects.new(unique_object_name(base), None)
    root.empty_display_type = "PLAIN_AXES"
    try:
        center, size = object_manager_world_bounds(members)
        root.empty_display_size = max(0.25, min(max(size.x, size.y, size.z) * 0.25, 3.0))
    except Exception:
        center = Vector((0.0, 0.0, 0.0))
        root.empty_display_size = 0.5
    root.location = anchor.matrix_world.translation.copy() if anchor is not None else center
    link_object_to_context_collection(context, root, members)
    return root


def create_object_manager_assembly(context, name="", group_type=OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT):
    members = selected_objects_for_object_manager_assembly(context)
    if len(members) < 2:
        raise RuntimeError("Select two or more objects to make a group.")

    active = context.view_layer.objects.active if context.view_layer else None
    anchor = active if active in (context.selected_objects or []) else members[0]
    group_type = normalize_object_manager_assembly_type(group_type)
    base = object_manager_group_base_name(members, active, name, group_type)
    existing_member_roots = {obj for obj in members if is_object_manager_assembly_root(obj)}
    member_world_matrices = {obj: obj.matrix_world.copy() for obj in members}

    root = create_object_manager_container_root(context, base, members, anchor)
    display_name = unique_export_group_name(base)
    root[OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    root[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = f"assembly_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    root[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
    root[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = group_type
    root[OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP] = datetime.now(timezone.utc).isoformat()
    root[OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP] = True

    for obj in members:
        if obj in existing_member_roots:
            obj[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = root.name
            obj[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = root[OBJECT_MANAGER_ASSEMBLY_ID_PROP]
            parent_object_keep_world(obj, root)
            continue
        obj[OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = root.name
        obj[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = root[OBJECT_MANAGER_ASSEMBLY_ID_PROP]
        obj[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
        obj[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = group_type
        parent_object_keep_world(obj, root)

    if context.view_layer is not None:
        context.view_layer.update()
    for obj, matrix in member_world_matrices.items():
        obj.matrix_world = matrix
    if context.view_layer is not None:
        context.view_layer.update()

    snapshot_export_identity(root)
    for obj in members:
        snapshot_export_identity(obj)

    selectable_members = object_manager_selection_objects(root)
    root[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = active.name if active in selectable_members else root.name

    select_object_manager_assembly(context, root)
    return root, members


def dissolve_object_manager_assembly(context, root):
    if root is None or not is_object_manager_assembly_root(root):
        raise RuntimeError("Select a group first.")

    members = object_manager_member_objects(root)
    assembly_id = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
    parent_root_name = root.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP, "")
    parent_root = bpy.data.objects.get(parent_root_name) if parent_root_name else None
    for obj in members:
        matrix = obj.matrix_world.copy()
        if obj.parent == root:
            obj.parent = None
            obj.matrix_world = matrix
        if obj.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP) == root.name:
            del obj[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP]
        if assembly_id and obj.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP) == assembly_id:
            del obj[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP]
        if obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP) == root.name:
            del obj[OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP]
        if assembly_id and obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id:
            del obj[OBJECT_MANAGER_ASSEMBLY_ID_PROP]
            if OBJECT_MANAGER_ASSEMBLY_TYPE_PROP in obj:
                del obj[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP]
        if assembly_id and obj.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP) == root.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP):
            del obj[OBJECT_MANAGER_ASSEMBLY_NAME_PROP]

    root_world = root.matrix_world.copy()
    if parent_root is not None and root.parent == parent_root:
        root.parent = None
        root.matrix_world = root_world
    if parent_root is not None and parent_root.get(OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP) == root.name:
        parent_root[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = parent_root.name

    for prop in (
        OBJECT_MANAGER_ASSEMBLY_ROOT_PROP,
        OBJECT_MANAGER_ASSEMBLY_ID_PROP,
        OBJECT_MANAGER_ASSEMBLY_NAME_PROP,
        OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP,
        OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP,
        OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP,
        OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP,
        OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP,
        OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP,
        OBJECT_MANAGER_ASSEMBLY_TYPE_PROP,
        OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP,
        OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP,
    ):
        if prop in root:
            del root[prop]

    remove_empty_root = root.type == "EMPTY" and root.name in bpy.data.objects
    if remove_empty_root:
        bpy.data.objects.remove(root, do_unlink=True)

    bpy.ops.object.select_all(action="DESELECT")
    for obj in members:
        if obj.name in bpy.data.objects:
            obj.select_set(True)
    if members:
        context.view_layer.objects.active = members[0]
    return members


@_memoize_export_identity_lookup
def object_manager_group_entries(root):
    if root is None:
        return []

    entries = []
    if root.type == "MESH" and not is_collision_helper(root):
        entries.append(root)
    for obj in object_manager_direct_child_objects(root):
        if obj not in entries:
            entries.append(obj)
    return entries


def object_manager_selection_owner_root(obj, fallback_root=None):
    if obj is None:
        return fallback_root
    if is_object_manager_assembly_root(obj):
        parents = object_manager_parent_group_roots(obj)
        return parents[0] if parents else obj
    return object_manager_assembly_root_for_object(obj) or fallback_root


def detach_object_manager_member(root, obj):
    if root is None or obj is None or obj == root:
        return False

    matrix = obj.matrix_world.copy()
    if obj.parent == root:
        obj.parent = None
        obj.matrix_world = matrix

    if obj.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP) == root.name:
        del obj[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP]
        if OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP in obj:
            del obj[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP]
        return True

    if obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP) != root.name:
        return False

    assembly_id = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP)
    del obj[OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP]
    if assembly_id and obj.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) == assembly_id:
        del obj[OBJECT_MANAGER_ASSEMBLY_ID_PROP]
        if OBJECT_MANAGER_ASSEMBLY_NAME_PROP in obj:
            del obj[OBJECT_MANAGER_ASSEMBLY_NAME_PROP]
        if OBJECT_MANAGER_ASSEMBLY_TYPE_PROP in obj:
            del obj[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP]
    return True


def promote_object_manager_assembly_root(root, new_root, remaining_entries):
    if root is None or new_root is None or root == new_root:
        raise RuntimeError("Could not preserve the remaining group.")
    if is_object_manager_assembly_root(new_root):
        raise RuntimeError("Select the nested group itself before removing this group root.")

    root_world = root.matrix_world.copy()
    new_root_world = new_root.matrix_world.copy()
    root_parent = root.parent
    parent_root_name = root.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP, "")
    parent_root_id = root.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP, "")
    assembly_id = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP, "")
    display_name = root.get(OBJECT_MANAGER_ASSEMBLY_NAME_PROP, object_manager_display_name(root))
    group_type = object_manager_assembly_type(root)
    created_at = root.get(OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP, datetime.now(timezone.utc).isoformat())
    non_destructive = bool(root.get(OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP, True))

    clear_object_manager_props(new_root)
    new_root[OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    new_root[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = assembly_id
    new_root[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
    new_root[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = group_type
    new_root[OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP] = created_at
    new_root[OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP] = non_destructive
    new_root[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = new_root.name
    if parent_root_name:
        new_root[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = parent_root_name
    if parent_root_id:
        new_root[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = parent_root_id

    if new_root.parent != root_parent:
        new_root.parent = root_parent
        new_root.matrix_world = new_root_world

    remaining_set = set(remaining_entries)
    for obj in bpy.data.objects:
        if obj in {root, new_root}:
            continue
        if obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP) == root.name:
            obj[OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = new_root.name
        if obj.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP) == root.name:
            obj[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = new_root.name
        if obj in remaining_set and obj.parent == root:
            parent_object_keep_world(obj, new_root)

    parent_root = bpy.data.objects.get(parent_root_name) if parent_root_name else None
    if parent_root is not None and parent_root.get(OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP) == root.name:
        parent_root[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = new_root.name

    clear_object_manager_props(root)
    if parent_root_name and root.parent is not None:
        root.parent = None
        root.matrix_world = root_world
    return new_root


def object_manager_direct_entry_for_object(root, obj):
    if root is None or obj is None:
        return None
    entries = object_manager_group_entries(root)
    if obj in entries:
        return obj
    for entry in entries:
        if is_object_manager_assembly_root(entry):
            if obj in object_manager_selection_objects(entry):
                return entry
            continue
        if obj in set(entry.children_recursive):
            return entry
    return None


def selected_object_manager_group_entries(context, root):
    selected_entries = []
    seen = set()
    for selected in context.selected_objects or []:
        entry = object_manager_direct_entry_for_object(root, selected)
        if entry is None or entry in seen:
            continue
        selected_entries.append(entry)
        seen.add(entry)
    return selected_entries


def selected_object_manager_add_candidates(context, root):
    candidates = []
    seen = set()
    for selected in selected_objects_for_object_manager_assembly(context):
        if selected is None or selected == root or is_collision_helper(selected):
            continue
        if object_manager_direct_entry_for_object(root, selected) is not None:
            continue
        candidate = selected
        if candidate == root or candidate in seen:
            continue
        candidates.append(candidate)
        seen.add(candidate)
    return candidates


def add_selected_object_manager_members(context, root):
    if root is None or not is_object_manager_assembly_root(root):
        raise RuntimeError("Choose a target group first.")

    candidates = selected_object_manager_add_candidates(context, root)
    if not candidates:
        raise RuntimeError("Select one or more objects outside this group first.")

    for candidate in candidates:
        if root in object_manager_subtree_objects(candidate):
            raise RuntimeError(f"Cannot add '{candidate.name}' because that would create a group cycle.")
        parent_groups = [
            parent for parent in object_manager_parent_group_roots(candidate)
            if parent is not None and parent != root
        ]
        if parent_groups:
            raise RuntimeError(
                f"'{object_manager_display_name(candidate)}' is already in "
                f"'{object_manager_display_name(parent_groups[0])}'. Remove it there first."
            )

    assembly_id = str(root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP, "") or "")
    display_name = object_manager_display_name(root)
    group_type = object_manager_assembly_type(root)
    world_matrices = {candidate: candidate.matrix_world.copy() for candidate in candidates}
    for candidate in candidates:
        if is_object_manager_assembly_root(candidate):
            candidate[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = root.name
            candidate[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = assembly_id
        else:
            candidate[OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = root.name
            candidate[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = assembly_id
            candidate[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
            candidate[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = group_type
        parent_object_keep_world(candidate, root)
        candidate.matrix_world = world_matrices[candidate]
        snapshot_export_identity(candidate)

    active = context.view_layer.objects.active if context.view_layer else None
    active_candidate = next(
        (candidate for candidate in candidates if candidate == active or active in candidate.children_recursive),
        candidates[0],
    )
    root[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = active_candidate.name
    remember_object_manager_detail_selection(context, root)
    remember_object_manager_runtime_objects(candidates)
    tag_rr_addon_view3d_redraw()
    return candidates


def ungroup_selected_object_manager_members(context, root):
    if root is None or not is_object_manager_assembly_root(root):
        raise RuntimeError("Select a group first.")

    entries = object_manager_group_entries(root)
    selected_entries = selected_object_manager_group_entries(context, root)
    if not selected_entries:
        raise RuntimeError("Select one or more members in the Members list first.")

    if len(selected_entries) == len(entries):
        return dissolve_object_manager_assembly(context, root), None, True

    remaining_entries = [obj for obj in entries if obj not in selected_entries]
    if root in selected_entries:
        promotable = [obj for obj in remaining_entries if not is_object_manager_assembly_root(obj)]
        if not promotable:
            raise RuntimeError("The group root cannot be removed while only nested groups remain.")

    detached = []
    for obj in selected_entries:
        if obj == root:
            continue
        if detach_object_manager_member(root, obj):
            detached.append(obj)

    current_root = root
    if root in selected_entries:
        current_root = promote_object_manager_assembly_root(root, promotable[0], remaining_entries)
        detached.append(root)

    bpy.ops.object.select_all(action="DESELECT")
    for obj in detached:
        if obj.name in bpy.data.objects and not obj.hide_select:
            obj.select_set(True)
    if detached:
        context.view_layer.objects.active = detached[0]
    return detached, current_root, False


def clear_object_manager_props(obj):
    for prop in (
        OBJECT_MANAGER_ASSEMBLY_ROOT_PROP,
        OBJECT_MANAGER_ASSEMBLY_ID_PROP,
        OBJECT_MANAGER_ASSEMBLY_NAME_PROP,
        OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP,
        OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP,
        OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP,
        OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP,
        OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP,
        OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP,
        OBJECT_MANAGER_ASSEMBLY_TYPE_PROP,
        OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP,
        OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP,
    ):
        if prop in obj:
            del obj[prop]


def object_manager_runtime_object_uid(obj):
    """Return an identity that changes when Blender creates a new Object ID."""
    if obj is None:
        return None
    session_uid = getattr(obj, "session_uid", None)
    if session_uid is not None:
        try:
            return int(session_uid)
        except (TypeError, ValueError):
            pass
    try:
        return int(obj.as_pointer())
    except Exception:
        return id(obj)


def remember_object_manager_runtime_objects(objects=None):
    """Mark intentionally created objects so the native-duplicate guard keeps them."""
    global OBJECT_MANAGER_RUNTIME_OBJECT_UIDS, OBJECT_MANAGER_DUPLICATE_GUARD_READY

    data_objects = getattr(bpy.data, "objects", None)
    if objects is None and data_objects is None:
        OBJECT_MANAGER_DUPLICATE_GUARD_READY = False
        return False
    if not OBJECT_MANAGER_DUPLICATE_GUARD_READY and data_objects is not None:
        reset_object_manager_duplicate_guard()
    source = list(data_objects) if objects is None else list(objects)
    for obj in source:
        uid = object_manager_runtime_object_uid(obj)
        if uid is not None:
            OBJECT_MANAGER_RUNTIME_OBJECT_UIDS.add(uid)
    OBJECT_MANAGER_DUPLICATE_GUARD_READY = True
    return True


def reset_object_manager_duplicate_guard():
    """Treat every object already in the file as intentional membership state."""
    global OBJECT_MANAGER_RUNTIME_OBJECT_UIDS, OBJECT_MANAGER_DUPLICATE_GUARD_READY

    data_objects = getattr(bpy.data, "objects", None)
    if data_objects is None:
        OBJECT_MANAGER_RUNTIME_OBJECT_UIDS = set()
        OBJECT_MANAGER_DUPLICATE_GUARD_READY = False
        return False

    OBJECT_MANAGER_RUNTIME_OBJECT_UIDS = {
        uid
        for uid in (object_manager_runtime_object_uid(obj) for obj in data_objects)
        if uid is not None
    }
    OBJECT_MANAGER_DUPLICATE_GUARD_READY = True
    return True


def object_has_inherited_rr_identity(obj):
    if obj is None:
        return False
    return any(
        prop in obj
        for prop in (
            OBJECT_MANAGER_ASSEMBLY_ROOT_PROP,
            OBJECT_MANAGER_ASSEMBLY_ID_PROP,
            OBJECT_MANAGER_ASSEMBLY_NAME_PROP,
            OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP,
            OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP,
            OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP,
            OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP,
            OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP,
            OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP,
            OBJECT_MANAGER_ASSEMBLY_TYPE_PROP,
            OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP,
            OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP,
            EXPORT_STABLE_ID_PROP,
            EXPORT_LAST_ID_PROP,
            EXPORT_PREVIOUS_IDS_PROP,
        )
    )


def clear_inherited_rr_identity_from_objects(objects):
    """Turn duplicated RR objects into ordinary Blender objects without deleting geometry."""
    new_objects = list(dict.fromkeys(obj for obj in objects if obj is not None))
    new_set = set(new_objects)
    cleaned = []
    for obj in new_objects:
        if not object_has_inherited_rr_identity(obj):
            continue

        # Preserve parent relationships wholly contained in the copied selection,
        # but sever the inherited link back into an existing RR group.
        if obj.parent is not None and obj.parent not in new_set:
            has_group_membership = bool(
                obj.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP)
                or obj.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP)
            )
            ancestor = obj.parent
            has_external_group_ancestor = False
            while ancestor is not None:
                if is_object_manager_assembly_root(ancestor):
                    has_external_group_ancestor = True
                    break
                ancestor = ancestor.parent
            if has_group_membership or has_external_group_ancestor:
                matrix = obj.matrix_world.copy()
                obj.parent = None
                obj.matrix_world = matrix

        clear_object_manager_props(obj)
        clear_export_identity(obj)
        cleaned.append(obj)

    if cleaned:
        remember_object_manager_runtime_objects(new_objects)
        tag_rr_addon_view3d_redraw()
        print(
            "[RR Helper] Native duplicate kept outside RR groups: "
            + ", ".join(obj.name for obj in cleaned)
        )
    return cleaned


def clear_inherited_rr_identity_from_native_duplicates():
    """Save-time fallback for native copy paths outside the managed Shift+D macro."""
    global OBJECT_MANAGER_RUNTIME_OBJECT_UIDS, OBJECT_MANAGER_DUPLICATE_GUARD_READY

    current_objects = list(bpy.data.objects)
    current_uids = {
        object_manager_runtime_object_uid(obj): obj
        for obj in current_objects
        if object_manager_runtime_object_uid(obj) is not None
    }
    if not OBJECT_MANAGER_DUPLICATE_GUARD_READY:
        reset_object_manager_duplicate_guard()
        return []

    new_objects = [
        obj for uid, obj in current_uids.items()
        if uid not in OBJECT_MANAGER_RUNTIME_OBJECT_UIDS
    ]
    OBJECT_MANAGER_RUNTIME_OBJECT_UIDS = set(current_uids)
    return clear_inherited_rr_identity_from_objects(new_objects)


@persistent
def reset_object_manager_duplicate_guard_on_load(_dummy):
    reset_object_manager_duplicate_guard()


@persistent
def clear_inherited_rr_identity_before_save(_dummy):
    try:
        clear_inherited_rr_identity_from_native_duplicates()
    except Exception as exc:
        print(f"[RR Helper] Could not apply native duplicate guard before save: {exc}")


def object_manager_subtree_objects(root):
    if root is None:
        return []

    ordered = []
    seen = set()

    def add(obj):
        if obj is None or obj in seen or obj.type not in {"EMPTY", "MESH"}:
            return
        ordered.append(obj)
        seen.add(obj)
        for child in object_manager_direct_child_objects(obj):
            add(child)
        for child in list(obj.children):
            add(child)

    add(root)
    return ordered


def root_copy_display_name(original_root, copied_root, suffix="Variant"):
    base = sanitize_optional_id(strip_blender_copy_suffix(object_manager_display_name(original_root))) or "Group"
    if not base.lower().endswith(f"_{suffix.lower()}"):
        base = f"{base}_{suffix}"
    return unique_export_group_name(base, copied_root)


def duplicate_object_manager_group(context, root):
    if root is None or not is_object_manager_assembly_root(root):
        raise RuntimeError("Select a group first.")

    originals = object_manager_subtree_objects(root)
    if not originals:
        raise RuntimeError("Selected group has nothing to duplicate.")

    original_world = {obj: obj.matrix_world.copy() for obj in originals}
    copies = {}
    for original in originals:
        copied = original.copy()
        copied.name = unique_object_name(f"{strip_blender_copy_suffix(original.name)}_Variant")
        if original.type == "MESH" and original.data is not None:
            copied.data = original.data.copy()
            copied.data.name = unique_datablock_name(bpy.data.meshes, f"{copied.name}_Mesh")
        linked = False
        for collection in list(original.users_collection):
            try:
                collection.objects.link(copied)
                linked = True
            except RuntimeError:
                pass
        if not linked:
            link_object_to_context_collection(context, copied, (original,))
        copies[original] = copied

    for original, copied in copies.items():
        parent = original.parent
        copied.parent = copies.get(parent, parent)
        copied.matrix_world = original_world[original]

    original_roots = [obj for obj in originals if is_object_manager_assembly_root(obj)]
    if root not in original_roots:
        original_roots.insert(0, root)

    root_meta = {}
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    for index, original_root in enumerate(original_roots):
        copied_root = copies.get(original_root)
        if copied_root is None:
            continue
        group_type = object_manager_assembly_type(original_root)
        display_name = root_copy_display_name(original_root, copied_root)
        assembly_id = f"assembly_{timestamp}_{index:02d}"
        root_meta[original_root] = {
            "copy": copied_root,
            "old_id": original_root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP, ""),
            "new_id": assembly_id,
            "display_name": display_name,
            "type": group_type,
        }

    old_name_to_root = {original_root.name: original_root for original_root in root_meta}

    for copied in copies.values():
        clear_object_manager_props(copied)
        clear_export_identity(copied)

    for original_root, meta in root_meta.items():
        copied_root = meta["copy"]
        copied_root[OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        copied_root[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = meta["new_id"]
        copied_root[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = meta["display_name"]
        copied_root[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = meta["type"]
        copied_root[OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP] = datetime.now(timezone.utc).isoformat()
        copied_root[OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP] = True
        copied_root[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = copied_root.name

    for original, copied in copies.items():
        if original in root_meta:
            parent_name = original.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP, "")
            parent_root = old_name_to_root.get(parent_name)
            if parent_root is not None:
                parent_meta = root_meta[parent_root]
                copied[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = parent_meta["copy"].name
                copied[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = parent_meta["new_id"]
            elif parent_name:
                copied[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = parent_name
                parent_id = original.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP, "")
                if parent_id:
                    copied[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = parent_id
            continue

        assigned = False
        original_member_root = original.get(OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP, "")
        original_assembly_id = original.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP, "")
        for original_root, meta in root_meta.items():
            if original_member_root == original_root.name or (meta["old_id"] and original_assembly_id == meta["old_id"]):
                copied[OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = meta["copy"].name
                copied[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = meta["new_id"]
                copied[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = meta["display_name"]
                copied[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = meta["type"]
                assigned = True
                break
        if assigned:
            continue

        parent = original.parent
        while parent is not None:
            if parent in root_meta:
                meta = root_meta[parent]
                copied[OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = meta["copy"].name
                copied[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = meta["new_id"]
                copied[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = meta["display_name"]
                copied[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = meta["type"]
                break
            parent = parent.parent

    new_root = copies[root]
    bpy.ops.object.select_all(action="DESELECT")
    move_entries = object_manager_group_entries(root)
    selected = [copies[original] for original in move_entries if original in copies]
    if not selected:
        selected = [new_root]
    for obj in selected:
        if obj.name in bpy.data.objects and not obj.hide_select:
            obj.select_set(True)
    active = new_root if new_root in selected else selected[0]
    context.view_layer.objects.active = active
    new_root[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = active.name
    remember_object_manager_detail_selection(context, new_root)
    return new_root, selected


def object_manager_duplicate_source_for_context(context, root):
    if root is None or object_manager_assembly_type(root) != "VARIANTS":
        return root

    active = context.view_layer.objects.active if context and context.view_layer else None
    active_root = object_manager_assembly_root_for_object(active) if active is not None else None
    direct_children = object_manager_direct_child_objects(root)
    if active_root is not None and active_root != root and active_root in direct_children:
        return active_root

    for child in direct_children:
        if is_object_manager_assembly_root(child):
            return child
    return root


def object_manager_variants_parent_base_name(root):
    display_name = object_manager_display_name(root) if root is not None else "Variant"
    base = sanitize_optional_id(strip_dimension_suffix(display_name)) or "Variant"
    if base.lower().endswith("_variant"):
        base = base[:-8]
    if base.lower().endswith("_variants"):
        return sanitize_id(base)
    return sanitize_id(f"{base}_Variants")


def ensure_object_manager_variants_parent(context, original_root, new_root):
    if original_root is None or new_root is None:
        raise RuntimeError("Could not build the Variants group.")

    parent_roots = object_manager_parent_group_roots(original_root)
    variants_parent = next(
        (parent for parent in parent_roots if object_manager_assembly_type(parent) == "VARIANTS"),
        None,
    )
    if variants_parent is not None:
        new_root[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = variants_parent.name
        new_root[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = variants_parent.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP, "")
        parent_object_keep_world(new_root, variants_parent)
        variants_parent[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = new_root.name
        remember_object_manager_detail_selection(context, variants_parent)
        return variants_parent, False

    outer_parent = parent_roots[0] if parent_roots else None
    base = object_manager_variants_parent_base_name(original_root)
    variants_parent = create_object_manager_container_root(context, base, (original_root, new_root))
    display_name = unique_export_group_name(base)
    assembly_id = f"assembly_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    variants_parent[OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    variants_parent[OBJECT_MANAGER_ASSEMBLY_ID_PROP] = assembly_id
    variants_parent[OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = display_name
    variants_parent[OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
    variants_parent[OBJECT_MANAGER_ASSEMBLY_CREATED_AT_PROP] = datetime.now(timezone.utc).isoformat()
    variants_parent[OBJECT_MANAGER_ASSEMBLY_NON_DESTRUCTIVE_PROP] = True
    variants_parent[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = new_root.name

    if outer_parent is not None:
        variants_parent[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = outer_parent.name
        variants_parent[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = outer_parent.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP, "")
        parent_object_keep_world(variants_parent, outer_parent)
        if outer_parent.get(OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP) in {original_root.name, new_root.name}:
            outer_parent[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = variants_parent.name

    for child_root in (original_root, new_root):
        child_root[OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = variants_parent.name
        child_root[OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = assembly_id
        parent_object_keep_world(child_root, variants_parent)

    remember_object_manager_detail_selection(context, variants_parent)
    return variants_parent, True


INNERWALL_VARIANT_RE = re.compile(
    r"^InnerWall_(?P<style>.+)_(?P<kind>Low|Full)_200x10x(?P<height>190|200)$",
    re.IGNORECASE,
)


def innerwall_variant_match(root):
    if root is None:
        return None
    return INNERWALL_VARIANT_RE.match(strip_blender_copy_suffix(object_manager_display_name(root)))


def mesh_objects_have_export_geometry(meshes):
    """Keep points/edges and generated geometry; reject only confirmed empty meshes."""
    meshes = list(meshes)
    if any(mesh.data is not None and len(mesh.data.vertices) for mesh in meshes):
        return True
    if not meshes:
        return False

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for mesh in meshes:
            evaluated = mesh.evaluated_get(depsgraph)
            if evaluated.data is not None and len(evaluated.data.vertices):
                return True
        mesh_set = set(meshes)
        return any(
            instance.is_instance
            and instance.parent is not None
            and instance.parent.original in mesh_set
            and instance.object.type == "MESH"
            and instance.object.data is not None
            and len(instance.object.data.vertices)
            for instance in depsgraph.object_instances
        )
    except (RuntimeError, ReferenceError, AttributeError) as exception:
        raise RuntimeError(
            "Could not verify generated export geometry; check the evaluated meshes and retry."
        ) from exception


@_memoize_export_identity_lookup
def is_innerwall_root(root):
    if root is None or infer_asset_type(object_manager_display_name(root)) != "InnerWall":
        return False

    try:
        return mesh_objects_have_export_geometry(get_asset_meshes(root))
    except RuntimeError:
        # Discovery must not drop an unevaluated generated asset. Export preflight
        # will report the evaluation failure before creating any output files.
        return True


@_memoize_export_identity_lookup
def innerwall_kind(root):
    match = innerwall_variant_match(root)
    if match:
        return match.group("kind").title()

    try:
        _center, size = mesh_world_bounds(root)
    except Exception:
        return ""
    return "Low" if size.z < 1.95 else "Full"


def innerwall_material_style_key(root):
    parts = []
    for mesh in get_asset_meshes(root):
        for slot in mesh.material_slots:
            if slot.material is not None:
                parts.append(slot.material.name)

    value = "_".join(parts) or strip_blender_copy_suffix(root.name)
    value = re.sub(
        r"(buildermat|innerwall|material|mat|low|full|indoor|outdoor|pbr)",
        "_",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"200x10x(190|200)", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return value.lower()


@_memoize_export_identity_lookup
def innerwall_style_key(root):
    match = innerwall_variant_match(root)
    if match:
        return normalized_name_key(match.group("style"))
    return innerwall_material_style_key(root)


@_memoize_export_identity_lookup
def innerwall_row_key(root):
    try:
        center, _size = mesh_world_bounds(root)
    except Exception:
        return None
    return round(center.y, 2)


@_memoize_export_identity_lookup
def all_innerwall_roots():
    return [obj for obj in bpy.data.objects if is_innerwall_root(obj)]


@_memoize_export_identity_lookup
def find_innerwall_pair(root):
    if not is_innerwall_root(root):
        return None

    kind = innerwall_kind(root)
    if kind not in {"Low", "Full"}:
        return None

    opposite_kind = "Full" if kind == "Low" else "Low"
    style_key = innerwall_style_key(root)
    row_key = innerwall_row_key(root)
    candidates = []
    for candidate in all_innerwall_roots():
        if candidate == root or innerwall_kind(candidate) != opposite_kind:
            continue

        candidate_row_key = innerwall_row_key(candidate)
        if style_key and innerwall_style_key(candidate) == style_key:
            score = 0.0
        elif row_key is not None and candidate_row_key == row_key:
            score = 10.0
        else:
            continue

        if row_key is not None and candidate_row_key is not None:
            score += abs(candidate_row_key - row_key)
        candidates.append((score, candidate.name, candidate))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


@_memoize_export_identity_lookup
def ordered_innerwall_pair(root):
    if not is_innerwall_root(root):
        return [root]

    pair = find_innerwall_pair(root)
    roots = [root]
    if pair is not None:
        roots.append(pair)
    roots.sort(key=lambda obj: 0 if innerwall_kind(obj) == "Low" else 1)
    return roots


def expand_innerwall_pairs(roots):
    expanded = []
    seen = set()
    for root in roots:
        for candidate in ordered_innerwall_pair(root):
            if candidate is None or candidate.name in seen:
                continue
            expanded.append(candidate)
            seen.add(candidate.name)
    return expanded


@_memoize_export_identity_lookup
def object_manager_variant_group_root(root):
    current = object_manager_assembly_root_for_object(root) or root
    seen = set()
    while current is not None and current not in seen:
        seen.add(current)
        if is_object_manager_assembly_root(current) and object_manager_assembly_type(current) == "VARIANTS":
            return current

        parent_name = current.get(OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP, "")
        parent = bpy.data.objects.get(parent_name) if parent_name else None
        if parent is None or not is_object_manager_assembly_root(parent):
            parents = object_manager_parent_group_roots(current)
            parent = parents[0] if parents else None
        current = parent
    return None


@_memoize_export_identity_lookup
def object_manager_variant_member_roots(root):
    group_root = object_manager_variant_group_root(root)
    if group_root is None:
        return [root] if root is not None else []

    members = []
    seen = set()
    for member in object_manager_group_entries(group_root):
        if member is None or member.name in seen or is_collision_helper(member):
            continue
        if not get_asset_meshes(member):
            continue
        members.append(member)
        seen.add(member.name)
    return members


def expand_related_export_roots(roots):
    expanded = []
    seen = set()
    for root in roots:
        for candidate in expand_innerwall_pairs([root]):
            for variant in object_manager_variant_member_roots(candidate):
                for final_root in expand_innerwall_pairs([variant]):
                    if final_root is None or final_root.name in seen:
                        continue
                    expanded.append(final_root)
                    seen.add(final_root.name)
    return expanded


def queue_roots_for_export_roots(roots):
    queued = []
    seen = set()
    for root in roots:
        for candidate in expand_innerwall_pairs([root]):
            group_root = object_manager_variant_group_root(candidate)
            queue_root = group_root or candidate
            if queue_root is None or queue_root.name in seen:
                continue
            queued.append(queue_root)
            seen.add(queue_root.name)
    return queued


def shared_innerwall_icon_root(root):
    if not is_innerwall_root(root):
        return root

    for candidate in ordered_innerwall_pair(root):
        if innerwall_kind(candidate) == "Low":
            return candidate
    return root


def object_manager_variant_icon_source_root(root):
    group_root = object_manager_variant_group_root(root)
    if group_root is None:
        return None

    members = object_manager_variant_member_roots(group_root)
    if not members:
        return None

    stable_id = str(group_root.get(OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP, "") or "").strip()
    if stable_id:
        for member in members:
            if str(member.get(EXPORT_STABLE_ID_PROP, "") or "").strip() == stable_id:
                return member

    member_name = str(group_root.get(OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP, "") or "").strip()
    if member_name:
        named_member = bpy.data.objects.get(member_name)
        if named_member in members:
            return named_member

    active_member_name = str(group_root.get(OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP, "") or "").strip()
    if active_member_name:
        active_member = bpy.data.objects.get(active_member_name)
        if active_member in members:
            return active_member

    return members[0]


def remember_object_manager_variant_icon_source(root, source_root):
    group_root = object_manager_variant_group_root(root)
    members = object_manager_variant_member_roots(group_root) if group_root is not None else []
    if group_root is None or source_root not in members:
        return None

    validate_export_identity(source_root)
    stable_id, _previous_ids = ensure_export_identity(source_root, export_asset_id(source_root))
    group_root[OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP] = source_root.name
    group_root[OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP] = stable_id
    return source_root


def shared_builder_icon_root(root):
    variant_source = object_manager_variant_icon_source_root(root)
    if variant_source is not None:
        return variant_source
    return shared_innerwall_icon_root(root)


def queue_index_for_root(settings, root):
    if settings is None or root is None:
        return -1

    for index, item in enumerate(settings.export_queue):
        if item.object_name == root.name:
            return index
    return -1


def queue_roots(settings):
    if settings is None:
        return []

    roots = []
    for item in settings.export_queue:
        root = queue_item_object(item)
        if root is not None:
            roots.append(root)
    return roots


def queue_root_contains_object(root, obj):
    if root is None or obj is None:
        return False
    if obj == root:
        return True
    if object_manager_assembly_root_for_object(obj) == root:
        return True
    if obj in root.children_recursive:
        return True
    if obj in get_asset_meshes(root):
        return True
    if obj in get_collision_meshes(root):
        return True
    return False


def queue_root_for_scene_object(settings, obj):
    if settings is None or obj is None:
        return None

    assembly_root = object_manager_assembly_root_for_object(obj)
    if assembly_root is not None and queue_index_for_root(settings, assembly_root) >= 0:
        return assembly_root

    current = obj
    while current is not None:
        collider_target = current.get("rr_collider_target")
        if collider_target:
            target = bpy.data.objects.get(collider_target)
            if queue_index_for_root(settings, target) >= 0:
                return target

        if queue_index_for_root(settings, current) >= 0:
            return current

        current = current.parent

    for root in queue_roots(settings):
        if queue_root_contains_object(root, obj):
            return root

    for root in get_export_roots([obj]):
        if queue_index_for_root(settings, root) >= 0:
            return root

    return None


def scene_selection_key(context):
    if context is None:
        return None

    active = context.view_layer.objects.active if context.view_layer else None
    active_name = active.name if active is not None else ""
    selected_names = tuple(sorted(obj.name for obj in context.selected_objects if obj is not None))
    scene_name = context.scene.name if context.scene is not None else ""
    return scene_name, active_name, selected_names


def queue_root_from_scene_selection(settings, context):
    if settings is None or context is None:
        return None

    active = context.view_layer.objects.active if context.view_layer else None
    root = queue_root_for_scene_object(settings, active)
    if root is not None:
        return root

    for obj in context.selected_objects:
        root = queue_root_for_scene_object(settings, obj)
        if root is not None:
            return root

    return None


def sync_queue_active_index_to_scene_selection(context):
    global LAST_SCENE_SELECTION_KEY

    if context is None or context.scene is None:
        return False

    settings = getattr(context.scene, "rr_builder_export_settings", None)
    if settings is None or len(settings.export_queue) == 0:
        LAST_SCENE_SELECTION_KEY = scene_selection_key(context)
        return False

    root = queue_root_from_scene_selection(settings, context)
    new_index = queue_index_for_root(settings, root)
    key = scene_selection_key(context)
    if key == LAST_SCENE_SELECTION_KEY and (new_index < 0 or new_index == settings.queue_active_index):
        return False

    LAST_SCENE_SELECTION_KEY = key
    if new_index < 0 or new_index == settings.queue_active_index:
        return False

    current_item = get_active_queue_item(settings)
    if current_item is not None:
        copy_settings_to_queue_item(settings, current_item)

    settings.queue_active_index = new_index
    return True


def object_manager_selection_key(context):
    if context is None:
        return None

    active = context.view_layer.objects.active if context.view_layer else None
    active_name = active.name if active is not None else ""
    selected_names = tuple(sorted(obj.name for obj in context.selected_objects if obj is not None))
    scene_name = context.scene.name if context.scene is not None else ""
    return scene_name, active_name, selected_names


def remember_object_manager_detail_selection(context, root):
    global LAST_OBJECT_MANAGER_SELECTION_KEY
    global OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_KEY

    OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME = None
    OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = root.name if root is not None else None
    OBJECT_MANAGER_DETAIL_SELECTION_KEY = object_manager_selection_key(context)
    LAST_OBJECT_MANAGER_SELECTION_KEY = OBJECT_MANAGER_DETAIL_SELECTION_KEY


def sync_object_manager_selection(context):
    global LAST_OBJECT_MANAGER_SELECTION_KEY
    global OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_KEY

    if OBJECT_MANAGER_SELECTION_SYNCING or context is None or context.scene is None:
        return False
    if context.mode != "OBJECT":
        return False

    settings = getattr(context.scene, "rr_builder_export_settings", None)
    if settings is None or not getattr(settings, "object_manager_auto_select_assembly", False):
        return False
    if getattr(settings, "icon_framing_adjusting", False):
        return False

    active = context.view_layer.objects.active if context.view_layer else None
    key = object_manager_selection_key(context)
    selected = {obj for obj in context.selected_objects if obj is not None}
    detail_root = bpy.data.objects.get(OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME or "")
    if detail_root is not None and is_object_manager_assembly_root(detail_root):
        detail_scope = set(object_manager_selection_objects(detail_root))
        detail_scope.add(detail_root)
        if not selected or selected.issubset(detail_scope):
            LAST_OBJECT_MANAGER_SELECTION_KEY = key
            OBJECT_MANAGER_DETAIL_SELECTION_KEY = key
            return False
    OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = None
    OBJECT_MANAGER_DETAIL_SELECTION_KEY = None

    root = object_manager_assembly_root_for_object(active)
    if root is None:
        LAST_OBJECT_MANAGER_SELECTION_KEY = key
        OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME = None
        OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = None
        OBJECT_MANAGER_DETAIL_SELECTION_KEY = None
        return False

    desired = {root}
    desired.update(object_manager_selection_objects(root))
    if selected and not selected.issubset(desired):
        OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME = None
        OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = None
        OBJECT_MANAGER_DETAIL_SELECTION_KEY = None
        return False
    if active == root and desired.issubset(selected):
        LAST_OBJECT_MANAGER_SELECTION_KEY = key
        OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME = root.name
        OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = None
        OBJECT_MANAGER_DETAIL_SELECTION_KEY = None
        return False

    if key == LAST_OBJECT_MANAGER_SELECTION_KEY:
        return False

    LAST_OBJECT_MANAGER_SELECTION_KEY = key
    return select_object_manager_assembly(context, root)


def scene_selection_queue_sync_timer():
    if not SCENE_SELECTION_QUEUE_SYNC_ENABLED:
        return None

    if not OBJECT_MANAGER_DUPLICATE_GUARD_READY:
        reset_object_manager_duplicate_guard()

    try:
        sync_queue_active_index_to_scene_selection(bpy.context)
    except Exception as exc:
        print(f"[RandomRealm Builder Exporter] Selection queue sync failed: {exc}")

    try:
        autosave_icon_preview_lights()
    except Exception as exc:
        print(f"[RR Helper] Preview light autosave failed: {exc}")

    try:
        context = bpy.context
        settings = getattr(context.scene, "rr_builder_export_settings", None) if context.scene is not None else None
        if settings is not None:
            sync_object_manager_current_group_name(
                settings,
                selected_object_manager_assembly_root(context),
                preserve_existing=bool(context.selected_objects),
            )
    except Exception as exc:
        print(f"[RandomRealm Builder Exporter] Group name sync failed: {exc}")

    return 0.2


def register_scene_selection_queue_sync():
    global SCENE_SELECTION_QUEUE_SYNC_ENABLED, LAST_SCENE_SELECTION_KEY, LAST_OBJECT_MANAGER_SELECTION_KEY
    global OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME, OBJECT_MANAGER_DETAIL_SELECTION_KEY
    global ICON_LIGHT_AUTOSAVE_SIGNATURES

    SCENE_SELECTION_QUEUE_SYNC_ENABLED = True
    LAST_SCENE_SELECTION_KEY = None
    LAST_OBJECT_MANAGER_SELECTION_KEY = None
    OBJECT_MANAGER_WHOLE_SELECTION_ROOT_NAME = None
    OBJECT_MANAGER_DETAIL_SELECTION_ROOT_NAME = None
    OBJECT_MANAGER_DETAIL_SELECTION_KEY = None
    ICON_LIGHT_AUTOSAVE_SIGNATURES = {}
    reset_object_manager_duplicate_guard()
    try:
        if not bpy.app.timers.is_registered(scene_selection_queue_sync_timer):
            bpy.app.timers.register(scene_selection_queue_sync_timer, first_interval=0.2, persistent=True)
    except Exception as exc:
        print(f"[RandomRealm Builder Exporter] Could not start selection queue sync: {exc}")


def unregister_scene_selection_queue_sync():
    global SCENE_SELECTION_QUEUE_SYNC_ENABLED, LAST_SCENE_SELECTION_KEY, ICON_LIGHT_AUTOSAVE_SIGNATURES

    SCENE_SELECTION_QUEUE_SYNC_ENABLED = False
    LAST_SCENE_SELECTION_KEY = None
    ICON_LIGHT_AUTOSAVE_SIGNATURES = {}
    try:
        if bpy.app.timers.is_registered(scene_selection_queue_sync_timer):
            bpy.app.timers.unregister(scene_selection_queue_sync_timer)
    except Exception:
        pass


def get_active_queue_item(settings):
    if settings is None or len(settings.export_queue) == 0:
        return None

    index = max(0, min(settings.queue_active_index, len(settings.export_queue) - 1))
    return settings.export_queue[index]


def queue_item_object(item):
    if item is None or not item.object_name:
        return None
    return bpy.data.objects.get(item.object_name)


def queue_item_icon_preview_root(item, context=None, remember=False):
    queue_root = queue_item_object(item)
    if queue_root is None:
        return None

    if object_manager_variant_group_root(queue_root) == queue_root:
        active_preview_root = active_variant_preview_root(queue_root, context)
        if active_preview_root is not None:
            if remember:
                item.icon_preview_root_name = active_preview_root.name
            return active_preview_root

    preview_name = str(getattr(item, "icon_preview_root_name", "") or "")
    preview_root = bpy.data.objects.get(preview_name) if preview_name else None
    if preview_root is not None:
        preview_root = resolve_icon_framing_root(preview_root, context)
        if preview_root is not None and get_export_asset_meshes(preview_root):
            group_root = object_manager_variant_group_root(preview_root)
            if preview_root == queue_root or group_root == queue_root:
                if remember:
                    item.icon_preview_root_name = preview_root.name
                return preview_root

    preview_root = resolve_icon_framing_root(queue_root, context)
    if remember and preview_root is not None:
        item.icon_preview_root_name = preview_root.name
    return preview_root


def set_queue_active_index_without_preview_sync(settings, index):
    global SYNCING_QUEUE_ACTIVE_INDEX

    previous_syncing = SYNCING_QUEUE_ACTIVE_INDEX
    SYNCING_QUEUE_ACTIVE_INDEX = True
    try:
        settings.queue_active_index = index
    finally:
        SYNCING_QUEUE_ACTIVE_INDEX = previous_syncing


def copy_settings_to_queue_item(settings, item):
    if settings is None or item is None:
        return

    item.icon_zoom = settings.icon_zoom
    item.icon_offset_x = settings.icon_offset_x
    item.icon_offset_y = settings.icon_offset_y
    item.icon_view_yaw = settings.icon_view_yaw
    item.icon_view_pitch = settings.icon_view_pitch
    item.icon_light_brightness = settings.icon_light_brightness
    item.icon_key_light_ratio = settings.icon_key_light_ratio
    item.icon_fill_light_ratio = settings.icon_fill_light_ratio
    item.icon_back_light_ratio = settings.icon_back_light_ratio
    item.icon_outline_enabled = settings.icon_outline_enabled
    item.icon_outline_color = tuple(settings.icon_outline_color)
    item.icon_outline_pixels = settings.icon_outline_pixels
    item.framing_initialized = True


def initialize_queue_item_framing(item, root, settings):
    if item is None or settings is None:
        return
    if root is None or not root.get("rr_icon_framing_initialized"):
        copy_settings_to_queue_item(settings, item)
        return

    item.icon_zoom = float(root.get("rr_icon_zoom", 1.0))
    item.icon_offset_x = float(root.get("rr_icon_offset_x", 0.0))
    item.icon_offset_y = float(root.get("rr_icon_offset_y", 0.0))
    item.icon_view_yaw = float(root.get("rr_icon_view_yaw", 0.0))
    item.icon_view_pitch = float(root.get("rr_icon_view_pitch", 0.0))
    item.icon_light_brightness = float(root.get("rr_icon_light_brightness", ICON_LIGHT_BRIGHTNESS_DEFAULT))
    item.icon_key_light_ratio = float(root.get("rr_icon_key_light_ratio", ICON_KEY_LIGHT_RATIO_DEFAULT))
    item.icon_fill_light_ratio = float(root.get("rr_icon_fill_light_ratio", ICON_FILL_LIGHT_RATIO_DEFAULT))
    item.icon_back_light_ratio = float(root.get("rr_icon_back_light_ratio", ICON_BACK_LIGHT_RATIO_DEFAULT))
    item.icon_outline_enabled = bool(root.get("rr_icon_outline_enabled", settings.icon_outline_enabled))
    item.icon_outline_color = tuple(root.get("rr_icon_outline_color", settings.icon_outline_color))
    item.icon_outline_pixels = clamp_icon_outline_pixels(root.get("rr_icon_outline_pixels", settings.icon_outline_pixels))
    item.framing_initialized = True


def copy_queue_item_to_settings(item, settings):
    global SYNCING_QUEUE_SETTINGS

    if settings is None or item is None:
        return

    SYNCING_QUEUE_SETTINGS = True
    try:
        settings.icon_zoom = item.icon_zoom
        settings.icon_offset_x = item.icon_offset_x
        settings.icon_offset_y = item.icon_offset_y
        settings.icon_view_yaw = item.icon_view_yaw
        settings.icon_view_pitch = item.icon_view_pitch
        settings.icon_light_brightness = item.icon_light_brightness
        settings.icon_key_light_ratio = item.icon_key_light_ratio
        settings.icon_fill_light_ratio = item.icon_fill_light_ratio
        settings.icon_back_light_ratio = item.icon_back_light_ratio
        settings.icon_outline_enabled = item.icon_outline_enabled
        settings.icon_outline_color = tuple(item.icon_outline_color)
        settings.icon_outline_pixels = item.icon_outline_pixels
    finally:
        SYNCING_QUEUE_SETTINGS = False


def save_icon_framing_to_object(root, settings):
    if root is None or settings is None:
        return

    root["rr_icon_framing_initialized"] = True
    root["rr_icon_zoom"] = float(settings.icon_zoom)
    root["rr_icon_offset_x"] = float(settings.icon_offset_x)
    root["rr_icon_offset_y"] = float(settings.icon_offset_y)
    root["rr_icon_view_yaw"] = float(settings.icon_view_yaw)
    root["rr_icon_view_pitch"] = float(settings.icon_view_pitch)
    root["rr_icon_light_brightness"] = float(settings.icon_light_brightness)
    root["rr_icon_key_light_ratio"] = float(settings.icon_key_light_ratio)
    root["rr_icon_fill_light_ratio"] = float(settings.icon_fill_light_ratio)
    root["rr_icon_back_light_ratio"] = float(settings.icon_back_light_ratio)
    root["rr_icon_outline_enabled"] = bool(settings.icon_outline_enabled)
    root["rr_icon_outline_color"] = [float(channel) for channel in settings.icon_outline_color]
    root["rr_icon_outline_pixels"] = int(settings.icon_outline_pixels)


def load_icon_framing_from_object(root, settings):
    global SYNCING_QUEUE_SETTINGS

    if root is None or settings is None or not root.get("rr_icon_framing_initialized"):
        return False

    SYNCING_QUEUE_SETTINGS = True
    try:
        settings.icon_zoom = float(root.get("rr_icon_zoom", 1.0))
        settings.icon_offset_x = float(root.get("rr_icon_offset_x", 0.0))
        settings.icon_offset_y = float(root.get("rr_icon_offset_y", 0.0))
        settings.icon_view_yaw = float(root.get("rr_icon_view_yaw", 0.0))
        settings.icon_view_pitch = float(root.get("rr_icon_view_pitch", 0.0))
        settings.icon_light_brightness = float(root.get("rr_icon_light_brightness", ICON_LIGHT_BRIGHTNESS_DEFAULT))
        settings.icon_key_light_ratio = float(root.get("rr_icon_key_light_ratio", ICON_KEY_LIGHT_RATIO_DEFAULT))
        settings.icon_fill_light_ratio = float(root.get("rr_icon_fill_light_ratio", ICON_FILL_LIGHT_RATIO_DEFAULT))
        settings.icon_back_light_ratio = float(root.get("rr_icon_back_light_ratio", ICON_BACK_LIGHT_RATIO_DEFAULT))
        settings.icon_outline_enabled = bool(root.get("rr_icon_outline_enabled", True))
        settings.icon_outline_color = tuple(root.get("rr_icon_outline_color", (1.0, 1.0, 1.0, 1.0)))
        settings.icon_outline_pixels = clamp_icon_outline_pixels(root.get("rr_icon_outline_pixels", 2))
    finally:
        SYNCING_QUEUE_SETTINGS = False
    return True


def prepare_framing_for_root(root, settings, item=None, reset_missing=True):
    if root is None or settings is None:
        return

    if item is not None and item.framing_initialized:
        copy_queue_item_to_settings(item, settings)
        return

    if not load_icon_framing_from_object(root, settings) and reset_missing:
        reset_icon_framing(settings)

    if item is not None:
        copy_settings_to_queue_item(settings, item)


def select_queue_item_object(context, item):
    root = queue_item_object(item)
    if root is None:
        return False

    set_active_export_root(root)
    return True


def on_icon_framing_update(settings, context):
    if SYNCING_QUEUE_SETTINGS:
        return

    item = get_active_queue_item(settings)
    if item is not None:
        copy_settings_to_queue_item(settings, item)

    if context is None:
        return

    root = queue_item_icon_preview_root(item, context) if item is not None else None
    if root is None:
        roots = get_context_export_roots(context)
        root = next(
            (
                resolved
                for candidate in roots
                if (resolved := resolve_icon_framing_root(candidate, context)) is not None
            ),
            None,
        )

    preview_camera = bpy.data.objects.get(ICON_PREVIEW_CAMERA_NAME)
    if (
        root is not None
        and preview_camera is not None
        and context.scene.camera is not None
        and context.scene.camera.name == ICON_PREVIEW_CAMERA_NAME
    ):
        ensure_icon_preview_objects(root, settings, context.scene)


def on_icon_light_editor_update(settings, context):
    if SYNCING_ICON_LIGHT_EDITOR:
        return
    apply_icon_light_editor_settings(settings, context)


def on_queue_active_index_update(settings, context):
    if SYNCING_QUEUE_ACTIVE_INDEX:
        return

    if context is not None:
        autosave_icon_preview_lights(context.scene)

    if getattr(settings, "icon_light_edit_role", "NONE") != "NONE":
        if context is not None:
            capture_icon_light_editor_scene_state(settings, context)
        settings.icon_light_edit_role = "NONE"
        settings.icon_light_edit_root_name = ""

    item = get_active_queue_item(settings)
    if item is None:
        return

    if context is not None:
        queue_root = queue_item_object(item)
        root = queue_item_icon_preview_root(item, context, remember=True)
        if root is None:
            root = queue_root
        prepare_framing_for_root(root, settings, item)
        select_queue_item_object(context, item)
        if root is not None:
            sync_icon_preview_to_root(root, settings, context)
        refresh_selected_preview_image(settings, context)
    else:
        copy_queue_item_to_settings(item, settings)


def apply_icon_render_resolution(scene, settings):
    if scene is None or settings is None:
        return

    size = clamp_icon_size(getattr(settings, "icon_resolution", 512))
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100


def normalized_path(path):
    if not path:
        return ""

    return os.path.normcase(os.path.abspath(bpy.path.abspath(path))).rstrip("\\/")


def ensure_directory(path):
    if not path:
        return False

    try:
        os.makedirs(bpy.path.abspath(path), exist_ok=True)
        return True
    except OSError as exc:
        print(f"[RandomRealm Builder Exporter] Could not create directory {path}: {exc}")
        return False


def use_unity_temp_output(settings):
    if settings is None:
        return

    ensure_directory(UNITY_TEMP_OUTPUT_ROOT)
    settings.output_root = UNITY_TEMP_OUTPUT_ROOT


def migrate_legacy_output_root(settings):
    if settings is None:
        return

    current = normalized_path(getattr(settings, "output_root", ""))
    if not current or current == normalized_path(LEGACY_OUTPUT_ROOT):
        use_unity_temp_output(settings)


def on_icon_resolution_update(settings, context):
    if context is not None:
        apply_icon_render_resolution(context.scene, settings)


def clamp_icon_outline_pixels(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 2
    return max(1, min(8, value))


def icon_outline_color(settings):
    value = getattr(settings, "icon_outline_color", (1.0, 1.0, 1.0, 1.0)) if settings is not None else (1.0, 1.0, 1.0, 1.0)
    color = list(value[:4]) if hasattr(value, "__iter__") else [1.0, 1.0, 1.0, 1.0]
    while len(color) < 4:
        color.append(1.0)
    color = [max(0.0, min(1.0, float(channel))) for channel in color[:4]]
    if color[3] <= 0.0:
        color[3] = 1.0
    return color


def rectangle_mask_sum(integral, stride, x_min, y_min, x_max, y_max):
    return (
        integral[y_max * stride + x_max]
        - integral[y_min * stride + x_max]
        - integral[y_max * stride + x_min]
        + integral[y_min * stride + x_min]
    )


def build_mask_integral(mask, width, height):
    import array

    stride = width + 1
    integral = array.array("I", [0]) * ((height + 1) * stride)
    for y in range(height):
        row_sum = 0
        mask_offset = y * width
        integral_row = (y + 1) * stride
        previous_row = y * stride
        for x in range(width):
            row_sum += 1 if mask[mask_offset + x] else 0
            integral[integral_row + x + 1] = integral[previous_row + x + 1] + row_sum
    return integral


def icon_outline_source_path(icon_path):
    absolute_path = bpy.path.abspath(icon_path)
    if not absolute_path:
        return ""

    icon_directory = os.path.dirname(absolute_path)
    cache_directory = UNITY_BUILDER_ICON_SOURCE_CACHE
    label = f"{os.path.basename(icon_directory)}_{os.path.splitext(os.path.basename(absolute_path))[0]}"
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("._") or "icon"
    path_key = os.path.normcase(os.path.normpath(absolute_path)).encode("utf-8", errors="surrogatepass")
    digest = hashlib.sha256(path_key).hexdigest()[:12]
    return os.path.join(cache_directory, f"{label}_{digest}.png")


def remember_icon_outline_source(icon_path, overwrite=False):
    absolute_path = bpy.path.abspath(icon_path)
    source_path = icon_outline_source_path(absolute_path)
    if not absolute_path or not source_path or not os.path.exists(absolute_path):
        return ""
    if not overwrite and os.path.exists(source_path):
        return source_path

    os.makedirs(os.path.dirname(source_path), exist_ok=True)
    temporary_path = f"{source_path}.tmp"
    try:
        shutil.copyfile(absolute_path, temporary_path)
        os.replace(temporary_path, source_path)
    except OSError:
        return ""
    finally:
        if os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass
    return source_path


def copy_icon_outline_source(source_icon_path, target_icon_path):
    source_path = icon_outline_source_path(source_icon_path)
    target_path = icon_outline_source_path(target_icon_path)
    if not source_path or not target_path or not os.path.exists(source_path):
        return False

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    shutil.copyfile(source_path, target_path)
    return True


def apply_icon_outline_to_png(icon_path, settings=None):
    if settings is None or not getattr(settings, "icon_outline_enabled", False):
        return False

    absolute_path = bpy.path.abspath(icon_path)
    if not absolute_path or not os.path.exists(absolute_path):
        return False

    source_path = remember_icon_outline_source(absolute_path)
    if not source_path:
        return False

    thickness = clamp_icon_outline_pixels(getattr(settings, "icon_outline_pixels", 2))
    image = None
    try:
        import array

        image = bpy.data.images.load(source_path, check_existing=False)
        width, height = int(image.size[0]), int(image.size[1])
        if width <= 0 or height <= 0:
            return False

        pixel_count = width * height
        channel_count = pixel_count * 4
        pixels = array.array("f", [0.0]) * channel_count
        image.pixels.foreach_get(pixels)

        alpha_threshold = 8.0 / 255.0
        object_mask = bytearray(pixel_count)
        object_count = 0
        for index in range(pixel_count):
            if pixels[index * 4 + 3] > alpha_threshold:
                object_mask[index] = 1
                object_count += 1

        if object_count <= 0 or object_count >= pixel_count:
            return False

        integral = build_mask_integral(object_mask, width, height)
        stride = width + 1
        outline = bytearray(pixel_count)
        for y in range(height):
            y_min = max(0, y - thickness)
            y_max = min(height, y + thickness + 1)
            row_offset = y * width
            for x in range(width):
                index = row_offset + x
                if object_mask[index]:
                    continue
                x_min = max(0, x - thickness)
                x_max = min(width, x + thickness + 1)
                if rectangle_mask_sum(integral, stride, x_min, y_min, x_max, y_max) > 0:
                    outline[index] = 1

        color = icon_outline_color(settings)
        changed = False
        for index, outlined in enumerate(outline):
            if not outlined:
                continue
            offset = index * 4
            pixels[offset] = color[0]
            pixels[offset + 1] = color[1]
            pixels[offset + 2] = color[2]
            pixels[offset + 3] = color[3]
            changed = True

        if not changed:
            return False

        image.pixels.foreach_set(pixels)
        image.filepath_raw = absolute_path
        image.file_format = "PNG"
        image.save()
        return True
    finally:
        if image is not None and image.name in bpy.data.images:
            bpy.data.images.remove(image)


def on_use_unity_reference_icons_update(settings, context):
    if getattr(settings, "use_unity_reference_icons", False):
        refresh_unity_reference_icons(settings, context)
    else:
        remove_unity_reference_icons(settings)


def object_manager_group_from_settings(settings):
    if settings is None:
        return None
    root_key = str(getattr(settings, "object_manager_current_group_root", "") or "").strip()
    if not root_key:
        return None
    for candidate in bpy.data.objects:
        if not is_object_manager_assembly_root(candidate):
            continue
        if candidate.name == root_key or str(candidate.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP, "") or "") == root_key:
            return candidate
    return None


def on_object_manager_current_group_name_update(settings, context):
    global OBJECT_MANAGER_GROUP_NAME_SYNCING

    if OBJECT_MANAGER_GROUP_NAME_SYNCING or context is None:
        return

    root = selected_object_manager_assembly_root(context) or object_manager_group_from_settings(settings)
    if root is None:
        return

    desired_name = sanitize_optional_id(getattr(settings, "object_manager_current_group_name", ""))
    if not desired_name:
        return

    applied_name = set_object_manager_assembly_display_name(root, desired_name)
    root[EXPORT_NAME_HINT_DISMISSED_PROP] = False
    root_key = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) or root.name
    if (
        getattr(settings, "object_manager_current_group_name", "") == applied_name
        and getattr(settings, "object_manager_current_group_root", "") == root_key
    ):
        return

    OBJECT_MANAGER_GROUP_NAME_SYNCING = True
    try:
        settings.object_manager_current_group_root = root_key
        settings.object_manager_current_group_name = applied_name
    finally:
        OBJECT_MANAGER_GROUP_NAME_SYNCING = False


def on_object_manager_assembly_type_update(settings, context):
    global OBJECT_MANAGER_GROUP_TYPE_SYNCING

    if OBJECT_MANAGER_GROUP_TYPE_SYNCING or context is None:
        return

    root = selected_object_manager_assembly_root(context) or object_manager_group_from_settings(settings)
    if root is None:
        return

    selected = {obj for obj in (context.selected_objects or []) if obj is not None}
    root_scope = set(object_manager_selection_objects(root))
    root_scope.add(root)
    if len(selected) >= 2 and not selected.issubset(root_scope):
        # The dropdown is choosing the type of a new parent group. Do not
        # mutate one of the selected child groups before Make runs.
        return

    set_object_manager_assembly_type(root, getattr(settings, "object_manager_assembly_type", OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT))


def sync_object_manager_current_group_name(settings, root, preserve_existing=False):
    global OBJECT_MANAGER_GROUP_NAME_SYNCING, OBJECT_MANAGER_GROUP_TYPE_SYNCING

    if settings is None:
        return

    if root is None:
        if preserve_existing and object_manager_group_from_settings(settings) is not None:
            return
        if getattr(settings, "object_manager_current_group_root", ""):
            OBJECT_MANAGER_GROUP_NAME_SYNCING = True
            try:
                settings.object_manager_current_group_root = ""
                settings.object_manager_current_group_name = ""
            finally:
                OBJECT_MANAGER_GROUP_NAME_SYNCING = False
        return

    display_name = ensure_object_manager_panel_display_name(root)
    group_type = object_manager_assembly_type(root)
    root_key = root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) or root.name
    if (
        getattr(settings, "object_manager_current_group_root", "") == root_key
        and getattr(settings, "object_manager_current_group_name", "") == display_name
        and getattr(settings, "object_manager_assembly_type", OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT) == group_type
    ):
        return

    OBJECT_MANAGER_GROUP_NAME_SYNCING = True
    OBJECT_MANAGER_GROUP_TYPE_SYNCING = True
    try:
        settings.object_manager_current_group_root = root_key
        settings.object_manager_current_group_name = display_name
        settings.object_manager_assembly_type = group_type
    finally:
        OBJECT_MANAGER_GROUP_NAME_SYNCING = False
        OBJECT_MANAGER_GROUP_TYPE_SYNCING = False


def apply_icon_render_resolution_deferred():
    try:
        scene = bpy.context.scene
    except AttributeError:
        return 0.1

    if scene is not None and hasattr(scene, "rr_builder_export_settings"):
        settings = scene.rr_builder_export_settings
        apply_icon_render_resolution(scene, settings)
        migrate_legacy_output_root(settings)
    return None


def mesh_world_bounds(root):
    meshes = get_export_asset_meshes(root)
    if not meshes:
        raise RuntimeError(f"{root.name} has no exportable mesh objects.")

    corners = []
    for obj in meshes:
        corners.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)

    min_v = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    max_v = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
    center = (min_v + max_v) * 0.5
    size = max_v - min_v
    return center, size


def export_name_dimensions(root):
    _center, size = mesh_world_bounds(root)
    return dimensions_cm_from_size(size)


def strip_dimension_suffix(name):
    return re.sub(r"_\d+x\d+x\d+$", "", strip_blender_copy_suffix(name or ""), flags=re.IGNORECASE)


def infer_export_name_type(root):
    if root is None:
        return "Asset"

    base = strip_dimension_suffix(object_manager_display_name(root))
    clean_base = sanitize_optional_id(base)
    prefix = clean_base.split("_")[0] if clean_base else ""
    for asset_type in SUPPORTED_TYPES:
        if prefix.lower() == asset_type.lower():
            return asset_type
    return prefix or "Asset"


def infer_export_name_label(root, asset_type):
    if root is None:
        return ""

    base = sanitize_optional_id(strip_dimension_suffix(object_manager_display_name(root)))
    if not base:
        return ""

    prefix = sanitize_optional_id(asset_type)
    if prefix and base.lower().startswith(prefix.lower() + "_"):
        return base[len(prefix) + 1:]
    if base.lower() == prefix.lower():
        return ""
    return base


def recommended_export_name(root, settings, members=None):
    if members is not None:
        width, depth, height = export_name_dimensions_for_objects(members)
    else:
        width, depth, height = export_name_dimensions(root)

    manual_base = sanitize_optional_id(getattr(settings, "object_manager_assembly_name", ""))
    if manual_base:
        base = strip_dimension_suffix(manual_base)
        return sanitize_id(f"{base}_{width}x{depth}x{height}")

    asset_type = sanitize_optional_id(getattr(settings, "export_name_type", ""))
    if not asset_type:
        asset_type = infer_export_name_type(root)

    label = sanitize_optional_id(getattr(settings, "export_name_label", ""))
    if not label:
        label = infer_export_name_label(root, asset_type)

    parts = [asset_type]
    if label:
        parts.append(label)
    parts.append(f"{width}x{depth}x{height}")
    return sanitize_id("_".join(parts))


def export_name_is_reasonable(name):
    return EXPORT_NAME_PATTERN.match(sanitize_id(name or "")) is not None


def export_name_root_from_context(context):
    if context is None:
        return None

    selected_root = selected_object_manager_assembly_root(context)
    if selected_root is not None and get_asset_meshes(selected_root):
        return selected_root

    active = context.view_layer.objects.active if context.view_layer else context.object
    if active is not None:
        root = object_manager_assembly_root_for_object(active) or active
        if root is not None and get_asset_meshes(root):
            return root

    roots = get_context_export_roots(context)
    return roots[0] if roots else None


def short_export_part_base(obj, fallback_base, mesh_index):
    clean_fallback = sanitize_optional_id(fallback_base) or "Part"
    clean_name = sanitize_optional_id(strip_dimension_suffix(getattr(obj, "name", "")))
    lowered = clean_name.lower()
    if "frame" in lowered:
        return "Frame"
    return f"{clean_fallback}{mesh_index:02d}"


def rename_export_group_members(root, fallback_base="Part"):
    members = object_manager_member_objects(root) if is_object_manager_assembly_root(root) else []
    if not members:
        return 0

    renamed = 0
    mesh_index = 1
    empty_index = 1
    for obj in members:
        if obj == root:
            continue
        if obj.type == "MESH":
            part_name = short_export_part_base(obj, fallback_base, mesh_index)
            obj.name = unique_object_name(part_name)
            obj.data.name = unique_datablock_name(bpy.data.meshes, f"{part_name}_Mesh")
            mesh_index += 1
            renamed += 1
        elif obj.type == "EMPTY":
            clean_fallback = sanitize_optional_id(fallback_base) or "Part"
            obj.name = unique_object_name(f"{clean_fallback}Empty{empty_index:02d}")
            empty_index += 1
            renamed += 1
    return renamed


def ensure_scene_collection(name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)

    scene = bpy.context.scene
    if scene is not None and not any(child == collection for child in scene.collection.children):
        try:
            scene.collection.children.link(collection)
        except RuntimeError:
            pass
    return collection


def ensure_collider_material():
    material = bpy.data.materials.get(COLLIDER_BOX_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(COLLIDER_BOX_MATERIAL_NAME)
    material.diffuse_color = (0.2, 1.0, 0.35, 0.35)
    return material


def generated_collider_name(root):
    return f"{sanitize_id(root.name)}_Collider"


def find_generated_collider(root):
    if root is None:
        return None

    preferred_name = root.get("rr_generated_collider") or generated_collider_name(root)
    preferred = bpy.data.objects.get(preferred_name)
    if preferred is not None and preferred.get("rr_collider_target") == root.name:
        return preferred

    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.get("rr_collider_target") == root.name:
            return obj
    return None


def box_vertices_from_bounds(min_v, max_v):
    return [
        (min_v.x, min_v.y, min_v.z),
        (max_v.x, min_v.y, min_v.z),
        (max_v.x, max_v.y, min_v.z),
        (min_v.x, max_v.y, min_v.z),
        (min_v.x, min_v.y, max_v.z),
        (max_v.x, min_v.y, max_v.z),
        (max_v.x, max_v.y, max_v.z),
        (min_v.x, max_v.y, max_v.z),
    ]


def create_or_update_bounding_box_collider(root):
    if root is None:
        raise RuntimeError("Select an exportable object first.")

    center, size = mesh_world_bounds(root)
    if min(size.x, size.y, size.z) <= 0.0:
        raise RuntimeError(f"{root.name} has invalid zero-size bounds.")

    min_v = center - size * 0.5
    max_v = center + size * 0.5
    origin = root.matrix_world.translation.copy()
    local_min = min_v - origin
    local_max = max_v - origin

    mesh_name = f"{generated_collider_name(root)}_Mesh"
    mesh = bpy.data.meshes.new(mesh_name)
    mesh.from_pydata(
        box_vertices_from_bounds(local_min, local_max),
        [],
        [
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (3, 7, 6, 2),
            (0, 4, 7, 3),
            (1, 2, 6, 5),
        ],
    )
    mesh.validate(clean_customdata=False)
    mesh.update(calc_edges=True)

    collider = find_generated_collider(root)
    created = collider is None
    if collider is None:
        collider = bpy.data.objects.new(generated_collider_name(root), mesh)
    else:
        old_mesh = collider.data
        collider.data = mesh
        if old_mesh is not None and old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)

    collider.matrix_world = Matrix.Translation(origin)
    collider.display_type = "WIRE"
    if hasattr(collider, "show_wire"):
        collider.show_wire = True
    collider.show_in_front = True
    collider.hide_render = True
    collider.hide_viewport = False
    collider.hide_select = False
    collider["rr_builder_collision_helper"] = True
    collider["rr_collider_target"] = root.name
    collider["rr_collider_kind"] = "bounding_box"
    collider["rr_collider_bounds_center"] = [float(center.x), float(center.y), float(center.z)]
    collider["rr_collider_bounds_size"] = [float(size.x), float(size.y), float(size.z)]
    collider["rr_collider_generated_at"] = datetime.now(timezone.utc).isoformat()

    material = ensure_collider_material()
    collider.data.materials.clear()
    collider.data.materials.append(material)

    collection = ensure_scene_collection(COLLIDER_HELPER_COLLECTION_NAME)
    if collider.name not in collection.objects:
        collection.objects.link(collider)
    for user_collection in list(collider.users_collection):
        if user_collection != collection:
            user_collection.objects.unlink(collider)

    root["rr_generated_collider"] = collider.name
    root["rr_generated_collider_kind"] = "bounding_box"
    root["rr_generated_collider_bounds_center"] = [float(center.x), float(center.y), float(center.z)]
    root["rr_generated_collider_bounds_size"] = [float(size.x), float(size.y), float(size.z)]
    root["rr_generated_collider_updated_at"] = collider["rr_collider_generated_at"]

    bpy.context.view_layer.update()
    return collider, created


def clamp_float(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def get_icon_view_dir(settings=None):
    yaw = math.radians(float(getattr(settings, "icon_view_yaw", 0.0)))
    pitch = math.radians(
        clamp_float(float(getattr(settings, "icon_view_pitch", 0.0)), ICON_PITCH_MIN, ICON_PITCH_MAX)
    )
    view_dir = Matrix.Rotation(yaw, 4, "Z") @ ICON_VIEW_DIR
    right = view_dir.cross(Vector((0.0, 0.0, 1.0)))
    if right.length < 0.0001:
        right = Vector((1.0, 0.0, 0.0))
    view_dir = Matrix.Rotation(pitch, 4, right.normalized()) @ view_dir
    return view_dir.normalized()


def get_icon_camera_state(root, settings=None):
    center, size = mesh_world_bounds(root)
    radius = max(size.x, size.y, size.z, 0.5)
    zoom = clamp_float(float(getattr(settings, "icon_zoom", 1.0)), ICON_ZOOM_MIN, ICON_ZOOM_MAX)
    offset_x = float(getattr(settings, "icon_offset_x", 0.0))
    offset_y = float(getattr(settings, "icon_offset_y", 0.0))
    view_dir = get_icon_view_dir(settings)
    rotation = (-view_dir).to_track_quat("-Z", "Y").to_euler()
    basis = rotation.to_matrix()
    right = basis.col[0].normalized()
    up = basis.col[1].normalized()
    offset = right * offset_x + up * offset_y
    view_span = (radius * ICON_BASE_SCALE) / zoom
    camera_angle = math.radians(ICON_CAMERA_FOV_DEGREES)
    camera_distance = max(view_span / (2.0 * math.tan(camera_angle * 0.5)), radius * 0.75)
    location = center + offset + view_dir * camera_distance
    return {
        "center": center,
        "radius": radius,
        "view_dir": view_dir,
        "rotation": rotation,
        "location": location,
        "offset": offset,
        "ortho_scale": view_span,
        "camera_angle": camera_angle,
        "camera_distance": camera_distance,
    }


def configure_icon_camera(camera_obj, root, settings=None):
    state = get_icon_camera_state(root, settings)
    camera_obj.location = state["location"]
    camera_obj.rotation_euler = state["rotation"]
    camera_obj.data.type = "PERSP"
    camera_obj.data.angle = state["camera_angle"]
    camera_obj.data.clip_end = max(camera_obj.data.clip_end, state["camera_distance"] * 8.0)
    return state


def icon_preview_light_names():
    return {name for name in (*ICON_PREVIEW_LIGHT_NAMES, *LEGACY_ICON_PREVIEW_LIGHT_NAMES)}


ICON_PREVIEW_HELPER_MARKER = "rr_icon_preview_helper"
ICON_PREVIEW_PREVIOUS_CAMERA_SET = "rr_icon_preview_previous_camera_set"
ICON_PREVIEW_PREVIOUS_CAMERA_NAME = "rr_icon_preview_previous_camera_name"


def is_owned_icon_preview_camera(obj):
    if obj is None or obj.type != "CAMERA":
        return False
    data = getattr(obj, "data", None)
    return bool(
        obj.get(ICON_PREVIEW_HELPER_MARKER)
        or (data is not None and data.get(ICON_PREVIEW_HELPER_MARKER))
        or (
            obj.name == ICON_PREVIEW_CAMERA_NAME
            and data is not None
            and data.name == ICON_PREVIEW_CAMERA_NAME
        )
    )


def is_owned_icon_preview_light(obj):
    if obj is None or obj.type != "LIGHT":
        return False
    data = getattr(obj, "data", None)
    legacy_data_names = {f"{name}_Data" for name in icon_preview_light_names()}
    return bool(
        obj.get("rr_icon_preview_light")
        or obj.get(ICON_PREVIEW_HELPER_MARKER)
        or (data is not None and data.get(ICON_PREVIEW_HELPER_MARKER))
        or (
            obj.name in icon_preview_light_names()
            and data is not None
            and data.name in legacy_data_names
        )
    )


def collection_contains_collection(root, target):
    if root == target:
        return True
    for child in root.children:
        if collection_contains_collection(child, target):
            return True
    return False


def get_or_create_icon_light_collection(scene):
    collection = bpy.data.collections.get(ICON_PREVIEW_LIGHT_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(ICON_PREVIEW_LIGHT_COLLECTION_NAME)
    collection[ICON_PREVIEW_HELPER_MARKER] = True
    if not collection_contains_collection(scene.collection, collection):
        scene.collection.children.link(collection)
    return collection


def move_icon_light_to_collection(light, scene):
    collection = get_or_create_icon_light_collection(scene)
    if light.name not in collection.objects:
        collection.objects.link(light)
    for owner in list(light.users_collection):
        if owner != collection:
            try:
                owner.objects.unlink(light)
            except RuntimeError:
                pass
    return collection


def get_or_create_icon_light(scene, spec):
    name = spec["name"]
    light = bpy.data.objects.get(name)
    existing_light = light is not None
    if light is None:
        for legacy_name in spec.get("legacy_names", ()):
            legacy = bpy.data.objects.get(legacy_name)
            if legacy is not None and legacy.type == "LIGHT":
                light = legacy
                existing_light = True
                light.name = name
                light.data.name = f"{name}_Data"
                break

    if light is None or light.type != "LIGHT":
        if light is not None:
            bpy.data.objects.remove(light, do_unlink=True)
        light_data = bpy.data.lights.new(f"{name}_Data", "SPOT")
        light = bpy.data.objects.new(name, light_data)
    move_icon_light_to_collection(light, scene)

    if light.data.type != "SPOT":
        light.data.type = "SPOT"
    if existing_light and not light.get("rr_icon_preview_transform_initialized"):
        light["rr_icon_preview_transform_initialized"] = True
    if existing_light and not light.data.get("rr_icon_preview_defaults_initialized"):
        light.data["rr_icon_preview_defaults_initialized"] = True
    light["rr_icon_preview_light"] = True
    light["rr_icon_preview_light_label"] = spec["label"]
    light[ICON_PREVIEW_HELPER_MARKER] = True
    light.data[ICON_PREVIEW_HELPER_MARKER] = True
    return light


def icon_light_default_location(state, spec):
    basis = state["rotation"].to_matrix()
    right = basis.col[0].normalized()
    up = basis.col[1].normalized()
    front = state["view_dir"].normalized()
    distance = max(state["camera_distance"] * 0.85, state["radius"] * ICON_CAMERA_DISTANCE)
    direction = (
        front * float(spec.get("front", 0.0))
        + right * float(spec.get("right", 0.0))
        + up * float(spec.get("up", 0.0))
    )
    if direction.length < 0.0001:
        direction = ICON_SPOTLIGHT_OFFSET
    return state["center"] + state["offset"] + direction.normalized() * distance


def icon_light_energy(spec, settings=None):
    if settings is None:
        return float(spec.get("energy", ICON_LIGHT_BASE_ENERGY))

    brightness = max(
        float(getattr(settings, "icon_light_brightness", ICON_LIGHT_BRIGHTNESS_DEFAULT)),
        ICON_LIGHT_BRIGHTNESS_MIN,
    )
    ratio_attr = spec.get("ratio_attr", "")
    ratio = clamp_float(
        float(getattr(settings, ratio_attr, spec.get("default_ratio", 1.0))),
        ICON_LIGHT_RATIO_MIN,
        ICON_LIGHT_RATIO_MAX,
    )
    return ICON_LIGHT_BASE_ENERGY * brightness * ratio


def icon_light_token(spec):
    ratio_attr = str(spec.get("ratio_attr", "icon_light_ratio"))
    return ratio_attr.removeprefix("icon_").removesuffix("_light_ratio").removesuffix("_ratio")


def icon_light_role(spec):
    return icon_light_token(spec).upper()


def icon_light_spec_for_role(role):
    role = str(role or "").upper()
    return next((spec for spec in ICON_PREVIEW_LIGHT_SPECS if icon_light_role(spec) == role), None)


def icon_light_for_role(role):
    spec = icon_light_spec_for_role(role)
    return bpy.data.objects.get(spec["name"]) if spec is not None else None


def icon_light_state_property(spec, suffix):
    return f"rr_icon_{icon_light_token(spec)}_light_{suffix}"


def icon_light_transform_property(spec):
    return icon_light_state_property(spec, "matrix")


def icon_light_focus_property(spec):
    return icon_light_state_property(spec, "focus")


def get_icon_light_focus(light):
    if light is None:
        return None
    values = light.get("rr_icon_preview_focus")
    try:
        valid_length = values is not None and len(values) == 3
    except TypeError:
        valid_length = False
    if not valid_length:
        return None
    try:
        return Vector(tuple(float(value) for value in values))
    except (TypeError, ValueError):
        return None


def set_icon_light_focus(light, focus):
    if light is None or focus is None:
        return
    focus = Vector(focus)
    light["rr_icon_preview_focus"] = [float(focus.x), float(focus.y), float(focus.z)]


def icon_light_focus_from_orientation(light, distance):
    direction = light.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    if direction.length < 0.0001:
        direction = Vector((0.0, 0.0, -1.0))
    return light.matrix_world.translation + direction.normalized() * max(float(distance), 0.001)


def ensure_icon_light_focus(light, state):
    focus = get_icon_light_focus(light)
    if focus is not None:
        return focus
    reference = state["center"] + state["offset"]
    distance = max((reference - light.matrix_world.translation).length, state["radius"], 0.5)
    focus = icon_light_focus_from_orientation(light, distance)
    set_icon_light_focus(light, focus)
    return focus


def save_icon_light_transform_to_object(root, light, spec):
    if root is None or light is None:
        return False
    if light.get("rr_icon_preview_light_target") != root.name:
        return False

    root[icon_light_transform_property(spec)] = [
        float(value)
        for row in light.matrix_world
        for value in row
    ]
    focus = get_icon_light_focus(light)
    if focus is None:
        focus = icon_light_focus_from_orientation(light, max(float(light.data.cutoff_distance) * 0.5, 1.0))
        set_icon_light_focus(light, focus)
    root[icon_light_focus_property(spec)] = [float(focus.x), float(focus.y), float(focus.z)]
    root[icon_light_state_property(spec, "settings_initialized")] = True
    root[icon_light_state_property(spec, "use_custom_distance")] = bool(light.data.use_custom_distance)
    root[icon_light_state_property(spec, "cutoff_distance")] = float(light.data.cutoff_distance)
    root[icon_light_state_property(spec, "spot_size")] = float(light.data.spot_size)
    root[icon_light_state_property(spec, "spot_blend")] = float(light.data.spot_blend)
    root[icon_light_state_property(spec, "shadow_soft_size")] = float(light.data.shadow_soft_size)
    return True


def save_icon_light_transforms_to_object(root, scene=None):
    if root is None:
        return False

    saved = False
    for spec in ICON_PREVIEW_LIGHT_SPECS:
        light = bpy.data.objects.get(spec["name"])
        if light is None or light.type != "LIGHT":
            continue
        if scene is not None and light.name not in scene.objects:
            continue
        saved = save_icon_light_transform_to_object(root, light, spec) or saved
    return saved


def icon_preview_light_autosave_signature(light):
    focus = get_icon_light_focus(light)
    data = light.data
    return (
        str(light.get("rr_icon_preview_light_target", "") or ""),
        *(float(value) for row in light.matrix_world for value in row),
        *(tuple(float(value) for value in focus) if focus is not None else ()),
        bool(data.use_custom_distance),
        float(data.cutoff_distance),
        float(data.spot_size),
        float(data.spot_blend),
        float(data.shadow_soft_size),
    )


def autosave_icon_preview_lights(scene=None):
    global ICON_LIGHT_AUTOSAVE_SIGNATURES

    objects = getattr(getattr(bpy, "data", None), "objects", None)
    if objects is None:
        return 0

    saved = 0
    live_names = set()
    for spec in ICON_PREVIEW_LIGHT_SPECS:
        light = objects.get(spec["name"])
        if light is None or light.type != "LIGHT":
            continue
        if scene is not None and light.name not in scene.objects:
            continue

        live_names.add(light.name)
        target_name = str(light.get("rr_icon_preview_light_target", "") or "")
        target_root = objects.get(target_name) if target_name else None
        signature = icon_preview_light_autosave_signature(light)
        if target_root is None or ICON_LIGHT_AUTOSAVE_SIGNATURES.get(light.name) == signature:
            ICON_LIGHT_AUTOSAVE_SIGNATURES[light.name] = signature
            continue

        if save_icon_light_transform_to_object(target_root, light, spec):
            saved += 1
        ICON_LIGHT_AUTOSAVE_SIGNATURES[light.name] = icon_preview_light_autosave_signature(light)

    ICON_LIGHT_AUTOSAVE_SIGNATURES = {
        name: signature
        for name, signature in ICON_LIGHT_AUTOSAVE_SIGNATURES.items()
        if name in live_names
    }
    return saved


def load_icon_light_transform_from_object(root, light, spec):
    if root is None or light is None:
        return False

    values = root.get(icon_light_transform_property(spec))
    if values is None or len(values) != 16:
        return False
    try:
        light.matrix_world = Matrix(
            tuple(
                tuple(float(values[row * 4 + column]) for column in range(4))
                for row in range(4)
            )
        )
    except (TypeError, ValueError):
        return False
    return True


def load_icon_light_settings_from_object(root, light, spec):
    if root is None or light is None:
        return False
    if not root.get(icon_light_state_property(spec, "settings_initialized")):
        return False

    focus_values = root.get(icon_light_focus_property(spec))
    try:
        valid_focus = focus_values is not None and len(focus_values) == 3
    except TypeError:
        valid_focus = False
    if valid_focus:
        try:
            set_icon_light_focus(light, Vector(tuple(float(value) for value in focus_values)))
        except (TypeError, ValueError):
            pass

    data = light.data
    data.use_custom_distance = bool(root.get(icon_light_state_property(spec, "use_custom_distance"), False))
    data.cutoff_distance = max(float(root.get(icon_light_state_property(spec, "cutoff_distance"), data.cutoff_distance)), 0.01)
    data.spot_size = clamp_float(
        float(root.get(icon_light_state_property(spec, "spot_size"), data.spot_size)),
        math.radians(1.0),
        math.pi,
    )
    data.spot_blend = clamp_float(
        float(root.get(icon_light_state_property(spec, "spot_blend"), data.spot_blend)),
        0.0,
        1.0,
    )
    data.shadow_soft_size = max(
        float(root.get(icon_light_state_property(spec, "shadow_soft_size"), data.shadow_soft_size)),
        0.0,
    )
    return True


def set_default_icon_light_transform(light, state, spec):
    light.location = icon_light_default_location(state, spec)
    target = state["center"] + state["offset"]
    direction = target - light.location
    if direction.length < 0.0001:
        direction = -state["view_dir"]
    light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    set_icon_light_focus(light, target)


def set_default_icon_light_settings(light, state, spec):
    focus = ensure_icon_light_focus(light, state)
    focus_distance = max((focus - light.matrix_world.translation).length, state["radius"], 0.5)
    light.data.use_custom_distance = False
    light.data.cutoff_distance = max(focus_distance + state["radius"] * 2.0, state["radius"] * 4.0, 1.0)
    light.data.spot_size = float(spec.get("spot_size", math.radians(45.0)))
    light.data.spot_blend = float(spec.get("spot_blend", 0.45))
    light.data.shadow_soft_size = float(spec.get("shadow_soft_size", max(state["radius"] * 0.08, 0.08)))


def configure_icon_light(light, state, spec, target_root=None, settings=None):
    target_name = target_root.name if target_root is not None else ""
    previous_target_name = str(light.get("rr_icon_preview_light_target", ""))
    target_changed = target_name != previous_target_name

    if previous_target_name and target_changed:
        previous_root = bpy.data.objects.get(previous_target_name)
        save_icon_light_transform_to_object(previous_root, light, spec)

    if target_changed:
        if not load_icon_light_transform_from_object(target_root, light, spec):
            set_default_icon_light_transform(light, state, spec)
        if not load_icon_light_settings_from_object(target_root, light, spec):
            set_default_icon_light_settings(light, state, spec)
    elif not light.get("rr_icon_preview_transform_initialized"):
        set_default_icon_light_transform(light, state, spec)
        set_default_icon_light_settings(light, state, spec)

    ensure_icon_light_focus(light, state)

    if target_name:
        light["rr_icon_preview_light_target"] = target_name
    light["rr_icon_preview_transform_initialized"] = True

    light.data.energy = icon_light_energy(spec, settings)
    light.data["rr_icon_preview_defaults_initialized"] = True
    return light


def ensure_icon_preview_lights(scene, state, target_root=None, settings=None):
    lights = []
    for spec in ICON_PREVIEW_LIGHT_SPECS:
        light = get_or_create_icon_light(scene, spec)
        lights.append(configure_icon_light(light, state, spec, target_root, settings))
    return lights


def get_icon_preview_lights(scene):
    return [
        obj for obj in scene.objects
        if obj.type == "LIGHT" and (obj.name in icon_preview_light_names() or obj.get("rr_icon_preview_light"))
    ]


def capture_icon_preview_light_states(scene):
    states = []
    for light in get_icon_preview_lights(scene):
        target_property = "rr_icon_preview_light_target"
        initialized_property = "rr_icon_preview_transform_initialized"
        focus_property = "rr_icon_preview_focus"
        light_data = light.data
        focus_values = light.get(focus_property)
        try:
            focus_present = focus_values is not None and len(focus_values) == 3
        except TypeError:
            focus_present = False
        states.append(
            {
                "name": light.name,
                "matrix_world": light.matrix_world.copy(),
                "target_present": target_property in light.keys(),
                "target": light.get(target_property, ""),
                "initialized_present": initialized_property in light.keys(),
                "initialized": bool(light.get(initialized_property, False)),
                "focus_present": focus_present,
                "focus": tuple(float(value) for value in focus_values) if focus_present else (),
                "energy": float(light_data.energy),
                "color": tuple(float(channel) for channel in light_data.color),
                "use_custom_distance": bool(light_data.use_custom_distance),
                "cutoff_distance": float(light_data.cutoff_distance),
                "spot_size": float(light_data.spot_size),
                "spot_blend": float(light_data.spot_blend),
                "shadow_soft_size": float(light_data.shadow_soft_size),
            }
        )
    return states


def restore_icon_preview_light_states(scene, states):
    target_property = "rr_icon_preview_light_target"
    initialized_property = "rr_icon_preview_transform_initialized"
    focus_property = "rr_icon_preview_focus"
    for state in states:
        light = bpy.data.objects.get(state["name"])
        if light is None or light.type != "LIGHT" or light.name not in scene.objects:
            continue

        light.matrix_world = state["matrix_world"]
        if state["target_present"]:
            light[target_property] = state["target"]
        elif target_property in light.keys():
            del light[target_property]
        if state["initialized_present"]:
            light[initialized_property] = state["initialized"]
        elif initialized_property in light.keys():
            del light[initialized_property]
        if state["focus_present"]:
            light[focus_property] = list(state["focus"])
        elif focus_property in light.keys():
            del light[focus_property]

        light.data.energy = state["energy"]
        light.data.color = state["color"]
        light.data.use_custom_distance = state["use_custom_distance"]
        light.data.cutoff_distance = state["cutoff_distance"]
        light.data.spot_size = state["spot_size"]
        light.data.spot_blend = state["spot_blend"]
        light.data.shadow_soft_size = state["shadow_soft_size"]

    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()


def icon_light_editor_root(settings):
    root_name = str(getattr(settings, "icon_light_edit_root_name", "") or "") if settings is not None else ""
    root = bpy.data.objects.get(root_name) if root_name else None
    return root if root is not None and get_export_asset_meshes(root) else None


def icon_light_root_from_light(light):
    if light is None:
        return None
    target_name = str(light.get("rr_icon_preview_light_target", "") or "")
    root = bpy.data.objects.get(target_name) if target_name else None
    return root if root is not None and get_export_asset_meshes(root) else None


def sync_icon_light_editor_from_light(settings, light, root=None, state=None):
    global SYNCING_ICON_LIGHT_EDITOR

    if settings is None or light is None or light.type != "LIGHT":
        return False
    if state is None and root is not None:
        state = get_icon_camera_state(root, settings)

    focus = get_icon_light_focus(light)
    if focus is None:
        if state is not None:
            focus = ensure_icon_light_focus(light, state)
        else:
            focus = icon_light_focus_from_orientation(light, max(float(light.data.cutoff_distance) * 0.5, 1.0))
            set_icon_light_focus(light, focus)

    previous_syncing = SYNCING_ICON_LIGHT_EDITOR
    SYNCING_ICON_LIGHT_EDITOR = True
    try:
        if root is not None:
            settings.icon_light_edit_root_name = root.name
        settings.icon_light_position = tuple(light.matrix_world.translation)
        settings.icon_light_focus = tuple(focus)
        settings.icon_light_use_custom_distance = bool(light.data.use_custom_distance)
        settings.icon_light_range = float(light.data.cutoff_distance)
        settings.icon_light_spot_size = float(light.data.spot_size)
        settings.icon_light_spot_blend = float(light.data.spot_blend)
        settings.icon_light_radius = float(light.data.shadow_soft_size)
    finally:
        SYNCING_ICON_LIGHT_EDITOR = previous_syncing
    return True


def apply_icon_light_editor_settings(settings, context):
    if settings is None or context is None:
        return False
    spec = icon_light_spec_for_role(getattr(settings, "icon_light_edit_role", "NONE"))
    light = bpy.data.objects.get(spec["name"]) if spec is not None else None
    root = icon_light_editor_root(settings) or icon_light_root_from_light(light)
    if spec is None or light is None or light.type != "LIGHT" or root is None:
        return False

    position = Vector(settings.icon_light_position)
    focus = Vector(settings.icon_light_focus)
    direction = focus - position
    if direction.length >= 0.0001:
        matrix = direction.to_track_quat("-Z", "Y").to_matrix().to_4x4()
        matrix.translation = position
        light.matrix_world = matrix
    else:
        matrix = light.matrix_world.copy()
        matrix.translation = position
        light.matrix_world = matrix
    set_icon_light_focus(light, focus)

    light.data.use_custom_distance = bool(settings.icon_light_use_custom_distance)
    light.data.cutoff_distance = max(float(settings.icon_light_range), 0.01)
    light.data.spot_size = clamp_float(float(settings.icon_light_spot_size), math.radians(1.0), math.pi)
    light.data.spot_blend = clamp_float(float(settings.icon_light_spot_blend), 0.0, 1.0)
    light.data.shadow_soft_size = max(float(settings.icon_light_radius), 0.0)
    light["rr_icon_preview_light_target"] = root.name
    light["rr_icon_preview_transform_initialized"] = True
    save_icon_light_transform_to_object(root, light, spec)
    context.view_layer.update()
    return True


def capture_icon_light_editor_scene_state(settings, context):
    if settings is None or context is None:
        return False
    spec = icon_light_spec_for_role(getattr(settings, "icon_light_edit_role", "NONE"))
    light = bpy.data.objects.get(spec["name"]) if spec is not None else None
    root = icon_light_editor_root(settings) or icon_light_root_from_light(light)
    if spec is None or light is None or light.type != "LIGHT" or root is None:
        return False

    editor_position = Vector(settings.icon_light_position)
    editor_focus = Vector(settings.icon_light_focus)
    focus_distance = max((editor_focus - editor_position).length, 0.5)
    focus = icon_light_focus_from_orientation(light, focus_distance)
    set_icon_light_focus(light, focus)
    light["rr_icon_preview_light_target"] = root.name
    light["rr_icon_preview_transform_initialized"] = True
    save_icon_light_transform_to_object(root, light, spec)
    return sync_icon_light_editor_from_light(settings, light, root)


def get_icon_render_lights(scene, preview_lights=None):
    lights = set(preview_lights or [])
    for obj in scene.objects:
        if obj.type == "LIGHT" and not obj.hide_render:
            lights.add(obj)
    return lights


def get_or_create_icon_spotlight(scene):
    return get_or_create_icon_light(scene, ICON_PREVIEW_LIGHT_SPECS[0])


def configure_icon_spotlight(light, state):
    return configure_icon_light(light, state, ICON_PREVIEW_LIGHT_SPECS[0])


def ensure_icon_preview_objects(root, settings, scene):
    root = resolve_icon_framing_root(root, bpy.context)
    if root is None:
        raise RuntimeError("The selected preview item has no renderable mesh variant.")

    camera = bpy.data.objects.get(ICON_PREVIEW_CAMERA_NAME)
    if camera is None or camera.type != "CAMERA":
        if camera is not None:
            bpy.data.objects.remove(camera, do_unlink=True)
        camera_data = bpy.data.cameras.new(ICON_PREVIEW_CAMERA_NAME)
        camera = bpy.data.objects.new(ICON_PREVIEW_CAMERA_NAME, camera_data)
        scene.collection.objects.link(camera)
    elif camera.name not in scene.objects:
        scene.collection.objects.link(camera)

    camera[ICON_PREVIEW_HELPER_MARKER] = True
    camera.data[ICON_PREVIEW_HELPER_MARKER] = True
    state = configure_icon_camera(camera, root, settings)

    lights = ensure_icon_preview_lights(scene, state, root, settings)
    if scene.camera != camera:
        scene[ICON_PREVIEW_PREVIOUS_CAMERA_SET] = True
        scene[ICON_PREVIEW_PREVIOUS_CAMERA_NAME] = scene.camera.name if scene.camera is not None else ""
    scene.camera = camera
    return camera, lights, state


def prepare_icon_light_editor_objects(root, settings, scene, selected_spec):
    state = get_icon_camera_state(root, settings)
    selected_light = bpy.data.objects.get(selected_spec["name"])
    selected_target_name = str(selected_light.get("rr_icon_preview_light_target", "")) if selected_light is not None else ""
    selected_target = bpy.data.objects.get(selected_target_name) if selected_target_name else None
    target_changed = (
        selected_target is not None
        and get_export_asset_meshes(selected_target)
        and selected_target != root
    )

    lights = [bpy.data.objects.get(spec["name"]) for spec in ICON_PREVIEW_LIGHT_SPECS]
    lights_ready = all(
        light is not None and light.type == "LIGHT" and light.name in scene.objects
        for light in lights
    )
    if not lights_ready or target_changed:
        return ensure_icon_preview_objects(root, settings, scene)

    # Opening the editor for the current target adopts exactly what is visible.
    # It must never run the target-switch configuration path or move a light.
    for spec, light in zip(ICON_PREVIEW_LIGHT_SPECS, lights):
        light["rr_icon_preview_light_target"] = root.name
        light["rr_icon_preview_transform_initialized"] = True
        ensure_icon_light_focus(light, state)
        save_icon_light_transform_to_object(root, light, spec)
    return bpy.data.objects.get(ICON_PREVIEW_CAMERA_NAME), lights, state


def cleanup_icon_preview_helpers():
    data_root = getattr(bpy, "data", None)
    objects = getattr(data_root, "objects", None)
    if objects is None:
        return {"cameras": 0, "lights": 0, "collections": 0}

    cameras = [obj for obj in list(objects) if is_owned_icon_preview_camera(obj)]
    lights = [obj for obj in list(objects) if is_owned_icon_preview_light(obj)]

    for light in lights:
        spec = next(
            (
                item for item in ICON_PREVIEW_LIGHT_SPECS
                if light.name == item["name"] or light.name in item.get("legacy_names", ())
            ),
            None,
        )
        target_name = str(light.get("rr_icon_preview_light_target", "") or "")
        target_root = objects.get(target_name) if target_name else None
        if spec is not None and target_root is not None:
            try:
                save_icon_light_transform_to_object(target_root, light, spec)
            except Exception as exc:
                print(f"[RR Helper] Could not preserve {light.name} while disabling: {exc}")

    camera_set = set(cameras)
    window_manager = getattr(getattr(bpy, "context", None), "window_manager", None)
    for window in list(getattr(window_manager, "windows", ()) or ()):
        scene = getattr(window, "scene", None)
        if scene is None or scene.camera not in camera_set:
            continue
        screen = getattr(window, "screen", None)
        for area in list(getattr(screen, "areas", ()) or ()):
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D" and space.region_3d is not None:
                    if space.region_3d.view_perspective == "CAMERA":
                        space.region_3d.view_perspective = "PERSP"

    scenes = getattr(data_root, "scenes", None)
    if scenes is not None:
        for scene in list(scenes):
            if scene.camera in camera_set:
                previous_name = (
                    str(scene.get(ICON_PREVIEW_PREVIOUS_CAMERA_NAME, "") or "")
                    if scene.get(ICON_PREVIEW_PREVIOUS_CAMERA_SET, False)
                    else ""
                )
                previous_camera = objects.get(previous_name) if previous_name else None
                scene.camera = (
                    previous_camera
                    if previous_camera is not None
                    and previous_camera.type == "CAMERA"
                    and previous_camera not in camera_set
                    else None
                )
            for key in (ICON_PREVIEW_PREVIOUS_CAMERA_SET, ICON_PREVIEW_PREVIOUS_CAMERA_NAME):
                if key in scene.keys():
                    del scene[key]

    removed_cameras = 0
    removed_lights = 0
    for obj in [*lights, *cameras]:
        datablock = getattr(obj, "data", None)
        obj_type = obj.type
        try:
            objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            continue

        if obj_type == "LIGHT":
            removed_lights += 1
            datablocks = getattr(data_root, "lights", None)
        else:
            removed_cameras += 1
            datablocks = getattr(data_root, "cameras", None)
        if datablock is not None and datablocks is not None and datablock.users == 0:
            try:
                datablocks.remove(datablock)
            except (ReferenceError, RuntimeError):
                pass

    removed_collections = 0
    collections = getattr(data_root, "collections", None)
    collection = collections.get(ICON_PREVIEW_LIGHT_COLLECTION_NAME) if collections is not None else None
    if (
        collection is not None
        and not collection.objects
        and not collection.children
        and (collection.get(ICON_PREVIEW_HELPER_MARKER) or collection.name == ICON_PREVIEW_LIGHT_COLLECTION_NAME)
    ):
        try:
            collections.remove(collection)
            removed_collections = 1
        except (ReferenceError, RuntimeError):
            pass

    return {
        "cameras": removed_cameras,
        "lights": removed_lights,
        "collections": removed_collections,
    }


def sync_icon_preview_to_root(root, settings, context, switch_to_camera=False):
    if root is None or settings is None or context is None:
        return False

    apply_icon_render_resolution(context.scene, settings)
    ensure_icon_preview_objects(root, settings, context.scene)
    if switch_to_camera:
        set_view3d_to_camera(context)

    if context.screen is not None:
        for area in context.screen.areas:
            area.tag_redraw()
    return True


def set_view3d_to_camera(context):
    if context.screen is None:
        return

    for area in context.screen.areas:
        if area.type != "VIEW_3D":
            continue

        for space in area.spaces:
            if space.type == "VIEW_3D" and space.region_3d is not None:
                space.region_3d.view_perspective = "CAMERA"
        area.tag_redraw()


def set_view3d_to_perspective(context):
    if context.screen is None:
        return

    for area in context.screen.areas:
        if area.type != "VIEW_3D":
            continue

        for space in area.spaces:
            if space.type == "VIEW_3D" and space.region_3d is not None:
                space.region_3d.view_perspective = "PERSP"
        area.tag_redraw()


def show_image_in_editor(context, image):
    if image is None or context.screen is None:
        return False

    for area in context.screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue

        for space in area.spaces:
            if space.type == "IMAGE_EDITOR":
                space.image = image
                area.tag_redraw()
                return True

    return False


def capture_mesh_hide_states(scene):
    return [(obj.name, obj.hide_get(), obj.hide_render) for obj in scene.objects]


def isolate_preview_meshes(root, scene, context=None):
    resolved_root = resolve_icon_framing_root(root, context)
    if resolved_root is not None:
        root = resolved_root

    preview_names = {ICON_PREVIEW_CAMERA_NAME, *icon_preview_light_names()}
    asset_meshes = set(get_export_asset_meshes(root))
    visible_objects = {root}
    for mesh in asset_meshes:
        visible_objects.add(mesh)
        parent = mesh.parent
        while parent is not None:
            visible_objects.add(parent)
            if parent == root:
                break
            parent = parent.parent

    for obj in scene.objects:
        visible_in_preview = obj in visible_objects or obj.name in preview_names or obj.type == "LIGHT"
        obj.hide_set(not visible_in_preview)
        obj.hide_render = not (obj in asset_meshes or obj.name in preview_names or obj.type == "LIGHT")


def restore_mesh_hide_states(hide_states):
    for name, hidden, hide_render in hide_states:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.hide_set(hidden)
            obj.hide_render = hide_render


def set_active_export_root(root):
    bpy.ops.object.select_all(action="DESELECT")
    root.select_set(True)
    for obj in get_export_asset_meshes(root):
        obj.select_set(True)
    for obj in get_collision_meshes(root):
        obj.select_set(True)
    bpy.context.view_layer.objects.active = root


def find_principled_node(material):
    if material is None or material.node_tree is None:
        return None

    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeBsdfPrincipled":
            return node

    return None


def directly_connected_principled_node(material):
    output = active_material_output_node(material)
    if output is None:
        return None

    surface = output.inputs.get("Surface")
    if surface is None:
        return None

    links = list(surface.links)
    if len(links) != 1:
        return None

    node = links[0].from_node
    return node if node is not None and node.bl_idname == "ShaderNodeBsdfPrincipled" else None


def unlinked_socket_default(node, *socket_names):
    if node is None:
        return None

    for socket_name in socket_names:
        socket = node.inputs.get(socket_name)
        if socket is None:
            continue
        if socket.links or not hasattr(socket, "default_value"):
            return None
        return socket.default_value

    return None


def rounded_manifest_float(value):
    return round(float(value), 6)


def build_material_surface_contract(material):
    principled = directly_connected_principled_node(material)
    if principled is None:
        return None

    base_color_socket = principled.inputs.get("Base Color")
    base_color = (
        base_color_socket.default_value
        if (
            base_color_socket is not None
            and not base_color_socket.links
            and hasattr(base_color_socket, "default_value")
        )
        else None
    )
    metallic = unlinked_socket_default(principled, "Metallic")
    roughness = unlinked_socket_default(principled, "Roughness")
    ior = unlinked_socket_default(principled, "IOR")
    transmission = unlinked_socket_default(principled, "Transmission Weight", "Transmission")
    alpha = unlinked_socket_default(principled, "Alpha")
    if (
        metallic is None
        or roughness is None
        or ior is None
        or transmission is None
        or alpha is None
    ):
        return None

    metallic = rounded_manifest_float(metallic)
    roughness = rounded_manifest_float(roughness)
    ior = rounded_manifest_float(ior)
    transmission = rounded_manifest_float(transmission)
    alpha = rounded_manifest_float(alpha)
    contract = {
        "contractVersion": 1,
        "metallic": metallic,
        "roughness": roughness,
        "ior": ior,
        "transmissionWeight": transmission,
        "alpha": alpha,
        "transparent": alpha < 0.999 or transmission > 0.001,
        "surfaceRenderMethod": str(getattr(material, "surface_render_method", "") or ""),
    }
    if base_color is not None:
        color = list(base_color)
        if len(color) >= 3:
            while len(color) < 4:
                color.append(1.0)
            contract["baseColor"] = [
                rounded_manifest_float(channel)
                for channel in color[:4]
            ]
    return contract


def build_material_surface_contracts(root):
    contracts = {}
    seen_materials = set()
    for obj in get_export_asset_meshes(root):
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.name in seen_materials:
                continue
            seen_materials.add(material.name)
            contract = build_material_surface_contract(material)
            if contract is not None:
                contracts[material.name] = contract
    return contracts


def material_needs_pbr_bake(material):
    if material is None:
        return False

    if not getattr(material, "use_nodes", False) or material.node_tree is None:
        return False

    principled = find_principled_node(material)
    output = active_material_output_node(material)
    if principled is None or output is None:
        return True

    surface = output.inputs.get("Surface")
    if surface is None:
        return True

    surface_links = list(surface.links)
    if len(surface_links) != 1 or surface_links[0].from_node != principled:
        return True

    important_sockets = (
        "Base Color",
        "Metallic",
        "Roughness",
        "Alpha",
        "Normal",
        "Emission Color",
        "Emission Strength",
    )
    for socket_name in important_sockets:
        socket = principled.inputs.get(socket_name)
        if socket is not None and socket.links:
            return True

    return False


def image_node_matches(node, keywords):
    if node is None or node.bl_idname != "ShaderNodeTexImage" or node.image is None:
        return False

    haystack = " ".join(
        value.lower()
        for value in (
            node.name,
            node.label,
            node.image.name,
            getattr(node.image, "filepath", ""),
        )
        if value
    )
    return any(keyword in haystack for keyword in keywords)


def find_image_node(material, keywords):
    if material is None or material.node_tree is None:
        return None

    for node in material.node_tree.nodes:
        if image_node_matches(node, keywords):
            return node

    return None


def normalized_lookup_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def project_root_dir():
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    return os.getcwd()


def project_textures_dir():
    return os.path.join(project_root_dir(), "textures")


def texture_package_root_dir():
    return os.path.join(project_root_dir(), TEXTURE_PACKAGE_DIR)


def normalize_abs_path(path):
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.abspath(bpy.path.abspath(path)))
    except Exception:
        return os.path.normcase(os.path.abspath(path))


def paths_match(left, right):
    left_path = normalize_abs_path(left)
    right_path = normalize_abs_path(right)
    return bool(left_path and right_path and left_path == right_path)


def safe_read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def parse_manifest_time(manifest, package_dir):
    created = manifest.get("createdUtc") or manifest.get("createdAt") or manifest.get("timestamp")
    if created:
        try:
            return datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    try:
        return os.path.getmtime(package_dir)
    except OSError:
        return 0.0


def canonical_texture_role(role, role_label=""):
    text = normalized_lookup_key(role_label or role)
    if any(token in text for token in ("basecolor", "albedo", "diffuse", "color")):
        return {
            "key": "basecolor",
            "label": "Base Color",
            "socket": "Base Color",
            "keywords": ("basecolor", "base_color", "albedo", "diffuse", "diff", "color"),
            "colorspace": "sRGB",
        }
    if "rough" in text:
        return {
            "key": "roughness",
            "label": "Roughness",
            "socket": "Roughness",
            "keywords": ("roughness", "rough"),
            "colorspace": "Non-Color",
        }
    if "metal" in text:
        return {
            "key": "metallic",
            "label": "Metallic",
            "socket": "Metallic",
            "keywords": ("metallic", "metalness", "metal"),
            "colorspace": "Non-Color",
        }
    if "normal" in text or text in {"nor", "nrm"}:
        return {
            "key": "normal",
            "label": "Normal",
            "socket": None,
            "keywords": ("normal", "nor_gl", "nor", "nrm"),
            "colorspace": "Non-Color",
        }
    if any(token in text for token in ("height", "displacement", "bump")):
        return {
            "key": "height",
            "label": "Height",
            "socket": None,
            "keywords": ("height", "disp", "displacement", "bump"),
            "colorspace": "Non-Color",
        }
    if any(token in text for token in ("alpha", "opacity")):
        return {
            "key": "alpha",
            "label": "Alpha",
            "socket": "Alpha",
            "keywords": ("alpha", "opacity", "mask"),
            "colorspace": "Non-Color",
        }
    if any(token in text for token in ("emission", "emissive", "emit")):
        return {
            "key": "emission",
            "label": "Emission",
            "socket": "Emission Color",
            "socket_names": ("Emission Color", "Emission"),
            "keywords": ("emission", "emissive", "emit"),
            "colorspace": "sRGB",
        }
    if text in {"ambientocclusion", "occlusion", "ao"}:
        return {
            "key": "ambientocclusion",
            "label": "Ambient Occlusion",
            "socket": None,
            "keywords": ("ambientocclusion", "ambient_occlusion", "occlusion", "ao"),
            "colorspace": "Non-Color",
        }
    return {
        "key": text or "texture",
        "label": role_label or role or "Texture",
        "socket": None,
        "keywords": (text or "texture",),
        "colorspace": "sRGB",
    }


def resolve_package_texture(package_dir, manifest):
    candidates = []
    for key in ("newTexture", "texture", "image", "sourceTexture"):
        value = manifest.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    relative = manifest.get("newTextureRelative")
    if isinstance(relative, str) and relative:
        candidates.insert(0, os.path.join(package_dir, relative))

    for candidate in candidates:
        path = candidate if os.path.isabs(candidate) else os.path.join(package_dir, candidate)
        if os.path.exists(path):
            return path

    new_dir = os.path.join(package_dir, "new")
    if os.path.isdir(new_dir):
        for name in sorted(os.listdir(new_dir)):
            if os.path.splitext(name)[1].lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr"}:
                return os.path.join(new_dir, name)
    return ""


def manifest_requests_texture_removal(manifest):
    operation = str(manifest.get("operation") or manifest.get("action") or "").strip().lower()
    return operation in {"remove-texture", "delete-texture", "remove"} or bool(manifest.get("removeTexture"))


def iter_texture_package_manifests(root_dir):
    if not os.path.isdir(root_dir):
        return

    for entry in os.scandir(root_dir):
        if not entry.is_dir():
            continue
        manifest_path = os.path.join(entry.path, "manifest.json")
        manifest = safe_read_json(manifest_path)
        if not manifest:
            continue
        source = resolve_package_texture(entry.path, manifest)
        if not source and not manifest_requests_texture_removal(manifest):
            continue
        yield {
            "package_dir": entry.path,
            "manifest_path": manifest_path,
            "manifest": manifest,
            "source": source,
            "created": parse_manifest_time(manifest, entry.path),
        }


def latest_texture_packages(root_dir):
    latest = {}
    for package in iter_texture_package_manifests(root_dir) or []:
        manifest = package["manifest"]
        material_name = manifest.get("material") or manifest.get("materialName") or ""
        role = canonical_texture_role(manifest.get("role", ""), manifest.get("roleLabel", ""))
        key = (normalized_lookup_key(material_name), role["key"])
        if not key[0]:
            continue
        previous = latest.get(key)
        if previous is None or package["created"] >= previous["created"]:
            package["role"] = role
            latest[key] = package
    return sorted(latest.values(), key=lambda item: item["created"])


def find_material_by_manifest(manifest):
    material_name = manifest.get("material") or manifest.get("materialName") or ""
    if material_name and material_name in bpy.data.materials:
        return bpy.data.materials[material_name]

    target_key = normalized_lookup_key(material_name)
    if target_key:
        for material in bpy.data.materials:
            if normalized_lookup_key(material.name) == target_key:
                return material
        return None

    old_texture = manifest.get("oldTexture") or ""
    if old_texture:
        for material in bpy.data.materials:
            if material.node_tree is None:
                continue
            for node in material.node_tree.nodes:
                if node.bl_idname == "ShaderNodeTexImage" and paths_match(image_source_path(node.image), old_texture):
                    return material

    object_name = manifest.get("object") or manifest.get("objectName") or ""
    root = bpy.data.objects.get(object_name)
    if root is not None:
        for obj in get_asset_meshes(root):
            for slot in obj.material_slots:
                material = slot.material
                if material is not None and (not target_key or normalized_lookup_key(material.name) == target_key):
                    return material

    return None


def package_destination_path(manifest, role, source_path):
    old_texture = manifest.get("oldTexture") or ""
    if old_texture:
        old_texture = bpy.path.abspath(old_texture)
        parent = os.path.dirname(old_texture)
        if parent and os.path.isdir(parent):
            return old_texture

    os.makedirs(project_textures_dir(), exist_ok=True)
    material_name = manifest.get("material") or "Material"
    ext = os.path.splitext(source_path)[1] or ".png"
    filename = f"{sanitize_id(material_name)}_{role['key']}{ext}"
    return os.path.join(project_textures_dir(), filename)


def backup_existing_texture(destination):
    if not os.path.exists(destination):
        return ""
    backup_dir = os.path.join(texture_package_root_dir(), TEXTURE_APPLIED_BACKUP_DIR)
    os.makedirs(backup_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(destination))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(backup_dir, f"{stem}.backup-{stamp}{ext}")
    shutil.copy2(destination, backup_path)
    return backup_path


def copy_package_texture_to_destination(source_path, destination):
    source_path = bpy.path.abspath(source_path)
    destination = bpy.path.abspath(destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)

    if os.path.exists(destination) and filecmp.cmp(source_path, destination, shallow=False):
        return False, ""

    backup_path = backup_existing_texture(destination)
    temporary = f"{destination}.tmp"
    shutil.copy2(source_path, temporary)
    os.replace(temporary, destination)
    return True, backup_path


def load_or_reload_image(path, colorspace):
    image = bpy.data.images.load(path, check_existing=True)
    image.filepath = bpy.path.relpath(path) if bpy.data.filepath else path
    try:
        image.colorspace_settings.name = colorspace
    except Exception:
        pass
    try:
        image.reload()
    except Exception:
        pass
    return image


def direct_image_node_from_socket(socket):
    if socket is None:
        return None
    for link in socket.links:
        if link.from_node.bl_idname == "ShaderNodeTexImage":
            return link.from_node
    return None


def role_principled_socket(principled, role):
    names = []
    if role.get("socket"):
        names.append(role["socket"])
    names.extend(role.get("socket_names", ()))
    for name in names:
        socket = principled.inputs.get(name)
        if socket is not None:
            return socket
    return None


def find_node_by_type(material, bl_idname, keywords=()):
    if material is None or material.node_tree is None:
        return None
    for node in material.node_tree.nodes:
        if node.bl_idname != bl_idname:
            continue
        if not keywords:
            return node
        haystack = " ".join(str(value or "").lower() for value in (node.name, node.label))
        if any(keyword in haystack for keyword in keywords):
            return node
    return None


def material_uv_map_name(material, manifest=None):
    object_name = ""
    if isinstance(manifest, dict):
        object_name = manifest.get("object") or manifest.get("objectName") or ""

    candidates = []
    root = bpy.data.objects.get(object_name) if object_name else None
    if root is not None:
        candidates.extend(get_asset_meshes(root))

    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj in candidates:
            continue
        if any(slot.material == material for slot in obj.material_slots):
            candidates.append(obj)

    for obj in candidates:
        mesh = getattr(obj, "data", None)
        uv_layers = getattr(mesh, "uv_layers", None)
        if not uv_layers or len(uv_layers) == 0:
            continue
        active = uv_layers.active or uv_layers[0]
        if active is not None and active.name:
            return active.name

    return ""


def find_or_create_uv_map_node(material, uv_map_name="", near_node=None):
    node_tree = material.node_tree
    fallback = None
    for node in node_tree.nodes:
        if node.bl_idname != "ShaderNodeUVMap":
            continue
        if uv_map_name and getattr(node, "uv_map", "") == uv_map_name:
            return node
        if fallback is None:
            fallback = node

    if fallback is not None:
        if uv_map_name and not getattr(fallback, "uv_map", ""):
            fallback.uv_map = uv_map_name
        return fallback

    uv_node = node_tree.nodes.new("ShaderNodeUVMap")
    uv_node.name = "RR UV Map"
    uv_node.label = "UV Map"
    if uv_map_name:
        uv_node.uv_map = uv_map_name
    if near_node is not None:
        uv_node.location = (near_node.location.x - 260, near_node.location.y)
    return uv_node


def ensure_image_node_uv_vector(material, image_node, uv_map_name=""):
    if material is None or material.node_tree is None or image_node is None:
        return False
    if image_node.bl_idname != "ShaderNodeTexImage":
        return False

    vector_input = image_node.inputs.get("Vector")
    if vector_input is None:
        return False

    if vector_input.links:
        for link in vector_input.links:
            if link.from_node.bl_idname == "ShaderNodeUVMap" and uv_map_name and not getattr(link.from_node, "uv_map", ""):
                link.from_node.uv_map = uv_map_name
        return False

    uv_node = find_or_create_uv_map_node(material, uv_map_name, image_node)
    output = uv_node.outputs.get("UV")
    if output is None:
        return False
    material.node_tree.links.new(output, vector_input)
    return True


def ensure_material_image_uv_vectors(material, uv_map_name=""):
    if material is None or material.node_tree is None:
        return 0

    connected = 0
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeTexImage" and ensure_image_node_uv_vector(material, node, uv_map_name):
            connected += 1
    return connected


def find_manifest_image_node(material, manifest):
    if material is None or material.node_tree is None:
        return None

    old_node = manifest.get("oldNode") or ""
    if old_node:
        node = material.node_tree.nodes.get(old_node)
        if node is not None and node.bl_idname == "ShaderNodeTexImage":
            return node
        for node in material.node_tree.nodes:
            if node.bl_idname == "ShaderNodeTexImage" and node.label == old_node:
                return node

    old_texture = manifest.get("oldTexture") or ""
    if old_texture:
        for node in material.node_tree.nodes:
            if node.bl_idname == "ShaderNodeTexImage" and paths_match(image_source_path(node.image), old_texture):
                return node

    return None


def find_or_create_image_node_for_role(material, principled, role, preferred_node=None):
    node_tree = material.node_tree
    socket = role_principled_socket(principled, role)
    if preferred_node is not None:
        return preferred_node
    node = direct_image_node_from_socket(socket)
    if node is None:
        node = find_image_node(material, role["keywords"])
    if node is not None:
        return node

    node = node_tree.nodes.new("ShaderNodeTexImage")
    node.name = f"RR {role['label']} Texture"
    node.label = role["label"]
    node.location = (principled.location.x - 420, principled.location.y + 120)
    return node


def link_replace(node_tree, from_socket, to_socket):
    if node_tree is None or from_socket is None or to_socket is None:
        return False
    for link in list(to_socket.links):
        node_tree.links.remove(link)
    node_tree.links.new(from_socket, to_socket)
    return True


def configure_normal_map_node_for_unity(node):
    if node is None or getattr(node, "bl_idname", "") != "ShaderNodeNormalMap":
        return

    for attr, value in (
        ("space", "TANGENT"),
        ("convention", "OPENGL"),
        ("base", "DISPLACED"),
    ):
        try:
            setattr(node, attr, value)
        except Exception:
            pass


def connect_normal_texture(material, principled, image_node):
    node_tree = material.node_tree
    normal_map = find_node_by_type(material, "ShaderNodeNormalMap")
    if normal_map is None:
        normal_map = node_tree.nodes.new("ShaderNodeNormalMap")
        normal_map.name = "RR Normal Map"
        normal_map.label = "Normal Map"
        normal_map.location = (image_node.location.x + 220, image_node.location.y - 120)

    configure_normal_map_node_for_unity(normal_map)
    link_replace(node_tree, image_node.outputs.get("Color"), normal_map.inputs.get("Color"))
    normal_socket = principled.inputs.get("Normal")
    bump = find_node_by_type(material, "ShaderNodeBump")
    if bump is not None and bump.inputs.get("Height") and bump.inputs["Height"].links:
        link_replace(node_tree, normal_map.outputs.get("Normal"), bump.inputs.get("Normal"))
        link_replace(node_tree, bump.outputs.get("Normal"), normal_socket)
    else:
        link_replace(node_tree, normal_map.outputs.get("Normal"), normal_socket)


def connect_height_texture(material, principled, image_node):
    node_tree = material.node_tree
    bump = find_node_by_type(material, "ShaderNodeBump")
    if bump is None:
        bump = node_tree.nodes.new("ShaderNodeBump")
        bump.name = "RR Height Bump"
        bump.label = "Height / Bump"
        bump.location = (image_node.location.x + 220, image_node.location.y - 120)
        strength = bump.inputs.get("Strength")
        if strength is not None:
            strength.default_value = 0.1

    link_replace(node_tree, image_node.outputs.get("Color"), bump.inputs.get("Height"))
    normal_socket = principled.inputs.get("Normal")
    current_normal_link = normal_socket.links[0] if normal_socket is not None and normal_socket.links else None
    if (
        current_normal_link is not None
        and current_normal_link.from_node.bl_idname == "ShaderNodeNormalMap"
        and bump.inputs.get("Normal") is not None
    ):
        link_replace(node_tree, current_normal_link.from_node.outputs.get("Normal"), bump.inputs.get("Normal"))
    link_replace(node_tree, bump.outputs.get("Normal"), normal_socket)


def connect_texture_role(material, principled, role, image_node):
    if role["key"] == "normal":
        connect_normal_texture(material, principled, image_node)
        return
    if role["key"] == "height":
        connect_height_texture(material, principled, image_node)
        return

    socket = role_principled_socket(principled, role)
    if socket is not None:
        output = image_node.outputs.get("Alpha") if role["key"] == "alpha" else image_node.outputs.get("Color")
        link_replace(material.node_tree, output, socket)
        if role["key"] == "alpha":
            material.blend_method = "BLEND"


PBR_BAKE_ROLES = (
    {
        "key": "BaseColor",
        "label": "Base Color",
        "socket": "Base Color",
        "bake_type": "EMIT",
        "pass_filter": None,
        "colorspace": "sRGB",
    },
    {
        "key": "Roughness",
        "label": "Roughness",
        "socket": "Roughness",
        "bake_type": "EMIT",
        "pass_filter": None,
        "colorspace": "Non-Color",
    },
    {
        "key": "Metallic",
        "label": "Metallic",
        "socket": "Metallic",
        "bake_type": "EMIT",
        "pass_filter": None,
        "colorspace": "Non-Color",
    },
    {
        "key": "Normal",
        "label": "Normal",
        "socket": "Normal",
        "bake_type": "NORMAL",
        "pass_filter": None,
        "colorspace": "Non-Color",
    },
)
PBR_FRAMEWORK_KIND_PROP = "rr_pbr_framework_kind"
PBR_FRAMEWORK_ROLE_PROP = "rr_pbr_framework_role"
PBR_FRAMEWORK_NATIVE_BAKE_TYPES = {
    "BaseColor": "DIFFUSE",
    "Roughness": "ROUGHNESS",
    "Metallic": "EMIT",
    "Normal": "NORMAL",
}


def pbr_bake_default_output_root():
    return bpy.path.abspath(os.path.join("//", PBR_BAKE_OUTPUT_DIR))


def pbr_bake_output_root(settings):
    configured = getattr(settings, "pbr_bake_output_root", "") if settings is not None else ""
    path = configured.strip() if configured else os.path.join("//", PBR_BAKE_OUTPUT_DIR)
    absolute = bpy.path.abspath(path)
    if paths_match(absolute, project_textures_dir()):
        return pbr_bake_default_output_root()
    return absolute


def pbr_bake_material_output_dir(output_root, material, batch_name=""):
    material_id = sanitize_id(material.name)
    parts = [output_root]
    clean_batch_name = sanitize_id(batch_name) if batch_name else ""
    if clean_batch_name:
        parts.append(clean_batch_name)
    parts.append(material_id)
    return os.path.join(*parts)


def pbr_bake_disabled_material_names(settings):
    if settings is None:
        return set()
    value = getattr(settings, "pbr_bake_disabled_materials", "") or ""
    return {line.strip() for line in value.splitlines() if line.strip()}


def set_pbr_bake_disabled_material_names(settings, names):
    if settings is None:
        return
    clean_names = sorted({str(name).strip() for name in names if str(name).strip()})
    settings.pbr_bake_disabled_materials = "\n".join(clean_names)


def unique_materials_for_meshes(mesh_objects):
    materials = []
    seen = set()
    for obj in mesh_objects:
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material in seen:
                continue
            materials.append(material)
            seen.add(material)
    return materials


def pbr_bake_candidate_materials_for_context(context):
    selected = list(getattr(context, "selected_objects", []) or [])
    source_roots = get_export_roots(selected)
    roots = expand_related_export_roots(source_roots)
    if not roots:
        return [], []

    mesh_objects = []
    seen_meshes = set()
    for root in roots:
        for mesh in get_asset_meshes(root):
            if mesh in seen_meshes:
                continue
            mesh_objects.append(mesh)
            seen_meshes.add(mesh)

    all_materials = unique_materials_for_meshes(mesh_objects)
    bakeable_materials = [material for material in all_materials if material_needs_pbr_bake(material)]
    simple_materials = [material for material in all_materials if material not in bakeable_materials]
    return bakeable_materials, simple_materials


def material_for_polygon(obj, polygon):
    if obj is None or polygon is None:
        return None
    material_index = getattr(polygon, "material_index", 0)
    if material_index < 0 or material_index >= len(obj.material_slots):
        return None
    return obj.material_slots[material_index].material


def uv_layer_has_unit_bake_faces(obj, uv_layer, bake_material_set):
    if obj is None or uv_layer is None:
        return False

    found_face = False
    for polygon in obj.data.polygons:
        if material_for_polygon(obj, polygon) not in bake_material_set:
            continue
        found_face = True
        for loop_index in polygon.loop_indices:
            uv = uv_layer.data[loop_index].uv
            if uv.x < -0.001 or uv.x > 1.001 or uv.y < -0.001 or uv.y > 1.001:
                return False
    return found_face


def choose_bake_uv_layer(obj, bake_material_set):
    uv_layers = getattr(obj.data, "uv_layers", None)
    if not uv_layers:
        return None

    active = uv_layers.active
    if active is not None and uv_layer_has_unit_bake_faces(obj, active, bake_material_set):
        return active

    named = uv_layers.get("UVMap")
    if named is not None and uv_layer_has_unit_bake_faces(obj, named, bake_material_set):
        return named

    for uv_layer in uv_layers:
        if uv_layer_has_unit_bake_faces(obj, uv_layer, bake_material_set):
            return uv_layer

    return active


def capture_mesh_uv_state(mesh_objects):
    states = []
    for obj in mesh_objects:
        uv_layers = getattr(obj.data, "uv_layers", None)
        if not uv_layers:
            continue
        active_name = uv_layers.active.name if uv_layers.active is not None else ""
        render_name = ""
        for uv_layer in uv_layers:
            if getattr(uv_layer, "active_render", False):
                render_name = uv_layer.name
                break
        states.append((obj.name, active_name, render_name))
    return states


def apply_bake_uv_layers(mesh_objects, bake_material_set):
    for obj in mesh_objects:
        uv_layers = getattr(obj.data, "uv_layers", None)
        if not uv_layers:
            continue
        uv_layer = choose_bake_uv_layer(obj, bake_material_set)
        if uv_layer is None:
            continue
        uv_layers.active = uv_layer
        try:
            uv_layer.active_render = True
        except Exception:
            pass


def bake_uv_map_names_by_material(mesh_objects, bake_material_set):
    names_by_material = {}
    for obj in mesh_objects:
        uv_layer = choose_bake_uv_layer(obj, bake_material_set)
        if uv_layer is None:
            continue

        for polygon in obj.data.polygons:
            material = material_for_polygon(obj, polygon)
            if material not in bake_material_set:
                continue
            names_by_material.setdefault(material, []).append(uv_layer.name)

    chosen = {}
    for material, names in names_by_material.items():
        if not names:
            continue
        if "UVMap" in names:
            chosen[material] = "UVMap"
            continue
        counts = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        chosen[material] = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    return chosen


def restore_mesh_uv_state(states):
    for obj_name, active_name, render_name in states:
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            continue
        uv_layers = getattr(obj.data, "uv_layers", None)
        if not uv_layers:
            continue
        active = uv_layers.get(active_name) if active_name else None
        if active is not None:
            uv_layers.active = active
        render = uv_layers.get(render_name) if render_name else None
        if render is not None:
            try:
                render.active_render = True
            except Exception:
                pass


def active_material_output_node(material):
    if material is None or material.node_tree is None:
        return None

    fallback = None
    for node in material.node_tree.nodes:
        if node.bl_idname != "ShaderNodeOutputMaterial":
            continue
        if getattr(node, "is_active_output", False):
            return node
        if fallback is None:
            fallback = node
    return fallback


def find_or_create_material_output_node(material):
    if material is None:
        return None

    material.use_nodes = True
    output = active_material_output_node(material)
    if output is not None:
        return output

    output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Material Output"
    output.label = "Material Output"
    output.location = (720, 0)
    try:
        output.is_active_output = True
    except AttributeError:
        pass
    return output


def find_or_create_principled_node(material):
    if material is None:
        return None

    material.use_nodes = True
    principled = find_principled_node(material)
    if principled is not None:
        return principled

    principled = material.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = f"{PBR_BAKE_NODE_PREFIX}Principled"
    principled.label = "Principled BSDF"
    principled.location = (260, 0)
    return principled


def set_image_colorspace(image, colorspace_name):
    try:
        image.colorspace_settings.name = colorspace_name
    except TypeError:
        pass


def create_bake_image(material, batch_name, role, resolution, output_root):
    material_id = sanitize_id(material.name)
    directory = pbr_bake_material_output_dir(output_root, material, batch_name)
    os.makedirs(directory, exist_ok=True)
    filename = f"{material_id}_{role['key']}.png"
    path = os.path.join(directory, filename)
    image_name = f"{material_id}_{role['key']}"
    image = bpy.data.images.new(image_name, width=resolution, height=resolution, alpha=role["key"] == "BaseColor")
    image.file_format = "PNG"
    image.filepath_raw = path
    set_image_colorspace(image, role["colorspace"])
    return image, path


def pbr_bake_display_path(path):
    if not path:
        return ""
    absolute = bpy.path.abspath(path)
    blend_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.getcwd()
    try:
        relative = os.path.relpath(absolute, blend_dir)
        if not relative.startswith(".."):
            return relative
    except ValueError:
        pass
    return absolute


def verify_saved_bake_image(path):
    if not os.path.isfile(path):
        raise RuntimeError(f"Bake image was not written: {pbr_bake_display_path(path)}")
    if os.path.getsize(path) <= 0:
        raise RuntimeError(f"Bake image is empty: {pbr_bake_display_path(path)}")
    return True


def find_or_create_pbr_bake_image_node(material, role):
    node_tree = material.node_tree
    node_name = f"{PBR_BAKE_NODE_PREFIX}{role['key']}"
    node = node_tree.nodes.get(node_name)
    if node is not None and node.bl_idname != "ShaderNodeTexImage":
        node = None
    if node is None:
        node = find_reusable_pbr_framework_image_node(material, role)
    if node is None:
        node = node_tree.nodes.new("ShaderNodeTexImage")
        node.name = node_name
        node.label = role["label"]
    elif not getattr(node, "label", ""):
        node.label = role["label"]
    mark_pbr_framework_node(node, "image", role["key"])
    return node


def activate_bake_image_nodes(material_images, role):
    for material, image_by_role in material_images.items():
        if material.node_tree is None:
            continue
        node = find_or_create_pbr_bake_image_node(material, role)
        node.image = image_by_role[role["key"]][0]
        material.node_tree.nodes.active = node
        for other in material.node_tree.nodes:
            other.select = False
        node.select = True


def value_as_emission_color(value, fallback=(0.0, 0.0, 0.0, 1.0)):
    if isinstance(value, (int, float)):
        scalar = float(value)
        return (scalar, scalar, scalar, 1.0)

    try:
        values = list(value)
    except TypeError:
        return fallback

    if len(values) >= 3:
        alpha = values[3] if len(values) >= 4 else 1.0
        return (float(values[0]), float(values[1]), float(values[2]), float(alpha))
    if len(values) == 1:
        scalar = float(values[0])
        return (scalar, scalar, scalar, 1.0)
    return fallback


def role_default_emission_color(socket_name):
    if socket_name == "Roughness":
        return (0.5, 0.5, 0.5, 1.0)
    if socket_name == "Base Color":
        return (1.0, 1.0, 1.0, 1.0)
    return (0.0, 0.0, 0.0, 1.0)


def connect_socket_to_emission_color(node_tree, source_socket, color_input, socket_name):
    if color_input is None:
        return

    if source_socket is not None and source_socket.links:
        node_tree.links.new(source_socket.links[0].from_socket, color_input)
        return

    if source_socket is not None and hasattr(source_socket, "default_value"):
        color_input.default_value = value_as_emission_color(
            source_socket.default_value,
            role_default_emission_color(socket_name),
        )
        return

    color_input.default_value = role_default_emission_color(socket_name)


def shader_role_input_socket(shader_node, socket_name):
    if shader_node is None:
        return None

    if shader_node.bl_idname == "ShaderNodeBsdfPrincipled":
        return shader_node.inputs.get(socket_name)

    if shader_node.bl_idname == "ShaderNodeBsdfDiffuse":
        if socket_name == "Base Color":
            return shader_node.inputs.get("Color")
        if socket_name in {"Roughness", "Normal"}:
            return shader_node.inputs.get(socket_name)

    return None


def find_shader_node_from_shader_socket(shader_socket, visited=None):
    if shader_socket is None:
        return None

    if visited is None:
        visited = set()

    node = shader_socket.node
    if node in visited:
        return None
    visited.add(node)

    if node.bl_idname in {"ShaderNodeBsdfPrincipled", "ShaderNodeBsdfDiffuse"}:
        return node

    for input_socket in node.inputs:
        if input_socket.type != "SHADER":
            continue
        for link in input_socket.links:
            found = find_shader_node_from_shader_socket(link.from_socket, visited)
            if found is not None:
                return found

    return None


def material_source_frame(material):
    if material is None or material.node_tree is None:
        return None

    for node in material.node_tree.nodes:
        if node.bl_idname == "NodeFrame" and node.label == PBR_BAKE_FRAME_LABEL:
            return node
    return None


def preferred_material_shader_output(material):
    if material is None or material.node_tree is None:
        return None

    frame = material_source_frame(material)
    if frame is not None:
        for node in material.node_tree.nodes:
            if node.parent != frame or node.name.startswith(PBR_BAKE_NODE_PREFIX):
                continue
            for output in node.outputs:
                if output.type == "SHADER":
                    return output

    output = active_material_output_node(material)
    if output is None:
        return None
    surface = output.inputs.get("Surface")
    if surface is None or not surface.links:
        return None
    return surface.links[0].from_socket


def group_output_input_for_socket(group_node, group_output_socket):
    if group_node is None or group_node.bl_idname != "ShaderNodeGroup" or group_node.node_tree is None:
        return None

    fallback = None
    for node in group_node.node_tree.nodes:
        if node.bl_idname != "NodeGroupOutput":
            continue
        socket = node.inputs.get(group_output_socket.name)
        if socket is not None:
            return socket
        if fallback is None:
            fallback = next((input_socket for input_socket in node.inputs if input_socket.type == "SHADER"), None)
    return fallback


def prepare_group_emit_socket_bake(group_node, group_output_socket, socket_name):
    output_input = group_output_input_for_socket(group_node, group_output_socket)
    if output_input is None or group_node.node_tree is None:
        return None

    node_tree = group_node.node_tree
    shader_socket = output_input.links[0].from_socket if output_input.links else None
    shader_node = find_shader_node_from_shader_socket(shader_socket)
    if shader_node is None:
        return None

    source_socket = shader_role_input_socket(shader_node, socket_name)
    old_links = [(link.from_socket, link.to_socket) for link in output_input.links]
    emission = node_tree.nodes.new("ShaderNodeEmission")
    emission.name = f"RR Bake {socket_name} Emit"
    emission.label = f"Bake {socket_name}"
    emission.location = (shader_node.location.x + 260, shader_node.location.y - 360)
    strength_input = emission.inputs.get("Strength")
    if strength_input is not None:
        strength_input.default_value = 1.0
    connect_socket_to_emission_color(node_tree, source_socket, emission.inputs.get("Color"), socket_name)

    for link in list(output_input.links):
        node_tree.links.remove(link)
    node_tree.links.new(emission.outputs.get("Emission"), output_input)

    def restore(node_tree=node_tree, emission=emission, old_links=old_links, output_input=output_input):
        for link in list(output_input.links):
            node_tree.links.remove(link)
        for from_socket, to_socket in old_links:
            try:
                node_tree.links.new(from_socket, to_socket)
            except RuntimeError:
                pass
        if emission.name in node_tree.nodes:
            node_tree.nodes.remove(emission)

    return restore


def prepare_emit_socket_bake(materials, socket_name):
    restore_actions = []
    for material in materials:
        output = active_material_output_node(material)
        if output is None or material.node_tree is None:
            continue

        surface = output.inputs.get("Surface")
        shader_output = preferred_material_shader_output(material)
        if surface is None or shader_output is None:
            continue

        node_tree = material.node_tree
        old_links = [(link.from_socket, link.to_socket) for link in surface.links]

        group_restore = None
        emission = None
        if shader_output.node.bl_idname == "ShaderNodeGroup":
            group_restore = prepare_group_emit_socket_bake(shader_output.node, shader_output, socket_name)
            if group_restore is None:
                continue
        else:
            shader_node = find_shader_node_from_shader_socket(shader_output)
            if shader_node is None:
                continue
            source_socket = shader_role_input_socket(shader_node, socket_name)
            emission = node_tree.nodes.new("ShaderNodeEmission")
            emission.name = f"RR Bake {socket_name} Emit"
            emission.label = f"Bake {socket_name}"
            emission.location = (shader_node.location.x + 260, shader_node.location.y - 360)
            strength_input = emission.inputs.get("Strength")
            if strength_input is not None:
                strength_input.default_value = 1.0
            connect_socket_to_emission_color(node_tree, source_socket, emission.inputs.get("Color"), socket_name)

        for link in list(surface.links):
            node_tree.links.remove(link)
        if emission is not None:
            node_tree.links.new(emission.outputs.get("Emission"), surface)
        else:
            node_tree.links.new(shader_output, surface)

        def restore(
            material=material,
            emission=emission,
            group_restore=group_restore,
            old_links=old_links,
            surface=surface,
        ):
            node_tree = material.node_tree
            if node_tree is None:
                return
            for link in list(surface.links):
                node_tree.links.remove(link)
            for from_socket, to_socket in old_links:
                try:
                    node_tree.links.new(from_socket, to_socket)
                except RuntimeError:
                    pass
            if group_restore is not None:
                group_restore()
            if emission is not None and emission.name in node_tree.nodes:
                node_tree.nodes.remove(emission)

        restore_actions.append(restore)
    return restore_actions


def restore_actions(actions):
    for action in reversed(actions):
        action()


def refresh_rr_helper_ui(context):
    screen = None
    if context is not None:
        screen = getattr(context, "screen", None)
        if screen is None and getattr(context, "window", None) is not None:
            screen = getattr(context.window, "screen", None)

    if screen is not None:
        for area in screen.areas:
            area.tag_redraw()

    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except Exception:
        pass


def refresh_material_node_ui(material, context=None):
    if material is not None:
        try:
            material.update_tag()
        except Exception:
            pass
        node_tree = getattr(material, "node_tree", None)
        if node_tree is not None:
            try:
                node_tree.update_tag()
            except Exception:
                pass

    try:
        bpy.context.view_layer.update()
    except Exception:
        pass

    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None and context is not None and getattr(context, "window", None) is not None:
        screen = getattr(context.window, "screen", None)
    if screen is not None:
        for area in screen.areas:
            if area.type in {"NODE_EDITOR", "VIEW_3D"}:
                area.tag_redraw()

    refresh_rr_helper_ui(context)


def bake_active_meshes(role, settings):
    kwargs = {
        "type": role["bake_type"],
        "margin": getattr(settings, "pbr_bake_margin", 16),
        "use_clear": True,
    }
    if role["pass_filter"] is not None:
        kwargs["pass_filter"] = role["pass_filter"]
    if role["bake_type"] == "NORMAL":
        kwargs["normal_space"] = "TANGENT"
    bpy.ops.object.bake(**kwargs)


def find_or_create_named_node(node_tree, name, bl_idname):
    node = node_tree.nodes.get(name)
    if node is None or node.bl_idname != bl_idname:
        node = node_tree.nodes.new(bl_idname)
        node.name = name
    return node


def node_custom_prop(node, prop_name, default=""):
    try:
        return node.get(prop_name, default)
    except Exception:
        return default


def node_name_starts_pbr_framework_prefix(node):
    return str(getattr(node, "name", "") or "").startswith(PBR_BAKE_NODE_PREFIX)


def mark_pbr_framework_node(node, kind="", role_key=""):
    if node is None:
        return
    try:
        if kind:
            node[PBR_FRAMEWORK_KIND_PROP] = kind
        if role_key:
            node[PBR_FRAMEWORK_ROLE_PROP] = role_key
    except Exception:
        pass


def pbr_framework_parent_score(node, expected_frame_name):
    parent = getattr(node, "parent", None)
    if parent is None:
        return 0
    if getattr(parent, "name", "") == expected_frame_name:
        return 80
    if getattr(parent, "name", "").startswith(PBR_BAKE_NODE_PREFIX):
        return 40
    return 0


def find_reusable_pbr_framework_image_node(material, role):
    if material is None or material.node_tree is None:
        return None

    expected_name = f"{PBR_BAKE_NODE_PREFIX}{role['key']}"
    node = material.node_tree.nodes.get(expected_name)
    if node is not None and node.bl_idname == "ShaderNodeTexImage":
        return node

    for node in material.node_tree.nodes:
        if node.bl_idname != "ShaderNodeTexImage":
            continue
        if not node_name_starts_pbr_framework_prefix(node):
            continue
        if node_custom_prop(node, PBR_FRAMEWORK_ROLE_PROP) == role["key"]:
            return node
    return None


def role_key_for_framework_image_node(node):
    role_key = node_custom_prop(node, PBR_FRAMEWORK_ROLE_PROP)
    if role_key in pbr_framework_role_keys() and node_name_starts_pbr_framework_prefix(node):
        return role_key

    for role in PBR_BAKE_ROLES:
        if getattr(node, "name", "") == f"{PBR_BAKE_NODE_PREFIX}{role['key']}":
            return role["key"]
    return ""


def node_is_pbr_framework_owned(node):
    if node is None:
        return False
    if node_name_starts_pbr_framework_prefix(node):
        return True
    parent = getattr(node, "parent", None)
    return parent is not None and getattr(parent, "name", "").startswith(PBR_BAKE_NODE_PREFIX)


def node_is_rr_normal_map(node):
    if node is None or getattr(node, "bl_idname", "") != "ShaderNodeNormalMap":
        return False
    return str(getattr(node, "name", "") or "").startswith(f"{PBR_BAKE_NODE_PREFIX}NormalMap")


def repair_rr_normal_map_nodes():
    repaired = 0
    try:
        materials = tuple(bpy.data.materials)
    except Exception:
        return 0

    for material in materials:
        node_tree = getattr(material, "node_tree", None)
        if node_tree is None:
            continue
        for node in node_tree.nodes:
            if not node_is_rr_normal_map(node):
                continue
            before = (
                getattr(node, "space", None),
                getattr(node, "convention", None),
                getattr(node, "base", None),
            )
            configure_normal_map_node_for_unity(node)
            after = (
                getattr(node, "space", None),
                getattr(node, "convention", None),
                getattr(node, "base", None),
            )
            if after != before:
                repaired += 1
    if repaired:
        print(f"[RandomRealm Builder Exporter] Set {repaired} RR Normal Map node(s) to OpenGL convention.")
    return repaired


@persistent
def repair_rr_normal_map_nodes_on_load(_dummy):
    try:
        repair_rr_normal_map_nodes()
    except Exception as exc:
        print("[RandomRealm Builder Exporter] Could not repair RR Normal Map nodes:", repr(exc))


def repair_rr_normal_map_nodes_deferred():
    repair_rr_normal_map_nodes()
    return None


def remove_node_if_present(node_tree, node):
    if node_tree is None or node is None:
        return False
    try:
        node_tree.nodes.remove(node)
        return True
    except Exception:
        return False


def remove_pbr_framework_links_from_socket(node_tree, socket):
    if node_tree is None or socket is None:
        return
    for link in list(getattr(socket, "links", []) or []):
        if node_is_pbr_framework_owned(getattr(link, "from_node", None)):
            try:
                node_tree.links.remove(link)
            except Exception:
                pass


def prune_manual_pbr_framework_roles(material, selected_role_keys):
    if material is None or material.node_tree is None:
        return 0

    node_tree = material.node_tree
    selected_role_keys = set(selected_role_keys or set())
    removed = 0
    for node in list(node_tree.nodes):
        if node.bl_idname != "ShaderNodeTexImage":
            continue
        role_key = role_key_for_framework_image_node(node)
        if role_key and role_key not in selected_role_keys and remove_node_if_present(node_tree, node):
            removed += 1

    if "Normal" not in selected_role_keys:
        for node in list(node_tree.nodes):
            if node.bl_idname != "ShaderNodeNormalMap":
                continue
            if node_name_starts_pbr_framework_prefix(node) and node_custom_prop(node, PBR_FRAMEWORK_KIND_PROP) == "normal_map":
                if remove_node_if_present(node_tree, node):
                    removed += 1

    principled = material.node_tree.nodes.get(f"{PBR_BAKE_NODE_PREFIX}Principled")
    if principled is not None:
        for role in PBR_BAKE_ROLES:
            if role["key"] in selected_role_keys:
                continue
            remove_pbr_framework_links_from_socket(node_tree, principled.inputs.get(role["socket"]))
    return removed


def find_or_create_pbr_framework_typed_node(node_tree, name, bl_idname, kind, label, expected_frame_name="", aliases=(), allow_unique_type=False):
    node = node_tree.nodes.get(name)
    if node is not None and node.bl_idname == bl_idname:
        mark_pbr_framework_node(node, kind)
        if label and not getattr(node, "label", ""):
            node.label = label
        return node

    type_nodes = [candidate for candidate in node_tree.nodes if candidate.bl_idname == bl_idname]
    best_node = None
    best_score = 0
    for candidate in type_nodes:
        if not node_name_starts_pbr_framework_prefix(candidate):
            continue
        score = 0
        if node_custom_prop(candidate, PBR_FRAMEWORK_KIND_PROP) == kind:
            score += 1000
        score += pbr_framework_parent_score(candidate, expected_frame_name)
        score += 40
        if score > best_score:
            best_node = candidate
            best_score = score

    if best_node is None:
        best_node = node_tree.nodes.new(bl_idname)
        best_node.name = name
        best_node.label = label
    elif label and not getattr(best_node, "label", ""):
        best_node.label = label

    mark_pbr_framework_node(best_node, kind)
    return best_node


def ensure_input_link_if_empty_or_same(node_tree, output_socket, input_socket):
    if node_tree is None or output_socket is None or input_socket is None:
        return False
    for link in getattr(input_socket, "links", []) or []:
        if link.from_socket == output_socket:
            return True
    if getattr(input_socket, "links", []) and len(input_socket.links) > 0:
        return False
    node_tree.links.new(output_socket, input_socket)
    return True


def find_or_create_named_frame(node_tree, name, label):
    frame = node_tree.nodes.get(name)
    if frame is None or frame.bl_idname != "NodeFrame":
        frame = node_tree.nodes.new("NodeFrame")
        frame.name = name
    frame.label = label
    return frame


def set_node_parent_and_location(node, parent, location):
    if node is None:
        return
    node.parent = parent
    node.location = location


def node_layout_width(node):
    if node is None:
        return 240.0

    dimensions = getattr(node, "dimensions", None)
    dimension_width = float(getattr(dimensions, "x", 0.0) or 0.0)
    node_width = float(getattr(node, "width", 0.0) or 0.0)
    return max(dimension_width, node_width, 220.0)


def node_layout_height(node):
    if node is None:
        return 160.0

    dimensions = getattr(node, "dimensions", None)
    dimension_height = float(getattr(dimensions, "y", 0.0) or 0.0)
    socket_count = max(len(getattr(node, "inputs", []) or []), len(getattr(node, "outputs", []) or []))
    estimated_height = 70.0 + socket_count * 22.0
    return max(dimension_height, estimated_height, 100.0)


def source_frame_layout_size(frame):
    if frame is None or getattr(frame, "id_data", None) is None:
        return 320.0, 260.0

    child_bounds = []
    for node in frame.id_data.nodes:
        if node.parent != frame:
            continue
        left = float(node.location.x)
        top = float(node.location.y)
        right = left + node_layout_width(node)
        bottom = top - node_layout_height(node)
        child_bounds.append((left, bottom, right, top))

    if not child_bounds:
        return max(node_layout_width(frame), 320.0), max(node_layout_height(frame), 260.0)

    min_x = min(bounds[0] for bounds in child_bounds)
    min_y = min(bounds[1] for bounds in child_bounds)
    max_x = max(bounds[2] for bounds in child_bounds)
    max_y = max(bounds[3] for bounds in child_bounds)
    return max(max_x - min_x + 80.0, 320.0), max(max_y - min_y + 90.0, 260.0)


def node_world_location(node):
    if node is None:
        return 0.0, 0.0

    location = getattr(node, "location", None)
    x = float(getattr(location, "x", 0.0) or 0.0)
    y = float(getattr(location, "y", 0.0) or 0.0)
    parent = getattr(node, "parent", None)
    visited = set()
    while parent is not None and id(parent) not in visited:
        visited.add(id(parent))
        parent_location = getattr(parent, "location", None)
        x += float(getattr(parent_location, "x", 0.0) or 0.0)
        y += float(getattr(parent_location, "y", 0.0) or 0.0)
        parent = getattr(parent, "parent", None)
    return x, y


def node_visual_bounds(node):
    if node is None:
        return None

    if getattr(node, "bl_idname", "") == "NodeFrame" and getattr(node, "id_data", None) is not None:
        child_bounds = [
            bounds for bounds in
            (node_visual_bounds(child) for child in node.id_data.nodes if child.parent == node)
            if bounds is not None
        ]
        if child_bounds:
            return (
                min(bounds[0] for bounds in child_bounds) - 35.0,
                min(bounds[1] for bounds in child_bounds) - 40.0,
                max(bounds[2] for bounds in child_bounds) + 35.0,
                max(bounds[3] for bounds in child_bounds) + 55.0,
            )

    x, y = node_world_location(node)
    return x, y - node_layout_height(node), x + node_layout_width(node), y


def node_collection_visual_bounds(nodes):
    bounds_list = [bounds for bounds in (node_visual_bounds(node) for node in nodes or []) if bounds is not None]
    if not bounds_list:
        return None
    return (
        min(bounds[0] for bounds in bounds_list),
        min(bounds[1] for bounds in bounds_list),
        max(bounds[2] for bounds in bounds_list),
        max(bounds[3] for bounds in bounds_list),
    )


def place_source_frame_near_principled(frame, principled):
    if frame is None or principled is None:
        return

    source_width, source_height = source_frame_layout_size(frame)
    principled_width = node_layout_width(principled)
    gap = 120.0
    frame.location.x = principled.location.x + (principled_width - source_width) * 0.5
    frame.location.y = principled.location.y + source_height + gap


def place_original_frame_above_pbr_group(frame, pbr_nodes):
    if frame is None:
        return False

    pbr_bounds = node_collection_visual_bounds(node for node in pbr_nodes or [] if node is not None and node != frame)
    frame_bounds = node_visual_bounds(frame)
    if pbr_bounds is None or frame_bounds is None:
        return False

    gap = 80.0
    pbr_center_x = (pbr_bounds[0] + pbr_bounds[2]) * 0.5
    frame_center_x = (frame_bounds[0] + frame_bounds[2]) * 0.5
    desired_bottom = pbr_bounds[3] + gap
    frame.location.x += pbr_center_x - frame_center_x
    frame.location.y += desired_bottom - frame_bounds[1]
    return True


def find_or_create_original_frame(node_tree):
    if node_tree is None:
        return None

    for node in node_tree.nodes:
        if node.bl_idname == "NodeFrame" and node.label in {"Original", PBR_BAKE_FRAME_LABEL}:
            node.label = "Original"
            return node

    frame = node_tree.nodes.new("NodeFrame")
    frame.name = unique_datablock_name(node_tree.nodes, "Original")
    frame.label = "Original"
    return frame


def frame_manual_pbr_original_nodes(material, keep_nodes, anchor_node=None):
    if material is None or material.node_tree is None:
        return None

    node_tree = material.node_tree
    frame = find_or_create_original_frame(node_tree)
    if frame is None:
        return None

    keep_nodes = set(keep_nodes or set())
    pbr_nodes = set(keep_nodes)
    keep_nodes.add(frame)
    for node in node_tree.nodes:
        if node in keep_nodes or node.name.startswith(PBR_BAKE_NODE_PREFIX):
            continue
        if node.parent is None:
            node.parent = frame

    if not place_original_frame_above_pbr_group(frame, pbr_nodes):
        place_source_frame_near_principled(frame, anchor_node)
    return frame


def arrange_pbr_bake_node_tree(material, principled, output, role_nodes, uv_map_name=""):
    if material is None or material.node_tree is None or principled is None or output is None:
        return set(role_nodes.values())

    node_tree = material.node_tree
    mapping_frame = find_or_create_named_frame(node_tree, PBR_BAKE_MAPPING_FRAME_NAME, "Mapping")
    textures_frame = find_or_create_named_frame(node_tree, PBR_BAKE_TEXTURE_FRAME_NAME, "Textures")
    uv_source = None
    texcoord = None
    if uv_map_name:
        uv_source = find_or_create_named_node(node_tree, PBR_BAKE_UVMAP_NODE_NAME, "ShaderNodeUVMap")
        try:
            uv_source.uv_map = uv_map_name
        except Exception:
            pass
        uv_source.label = f"UV: {uv_map_name}"
        stale_texcoord = node_tree.nodes.get(PBR_BAKE_TEXCOORD_NODE_NAME)
        if stale_texcoord is not None:
            node_tree.nodes.remove(stale_texcoord)
    else:
        texcoord = find_or_create_named_node(node_tree, PBR_BAKE_TEXCOORD_NODE_NAME, "ShaderNodeTexCoord")
        uv_source = texcoord
        stale_uvmap = node_tree.nodes.get(PBR_BAKE_UVMAP_NODE_NAME)
        if stale_uvmap is not None:
            node_tree.nodes.remove(stale_uvmap)
    mapping = find_or_create_named_node(node_tree, PBR_BAKE_MAPPING_NODE_NAME, "ShaderNodeMapping")

    set_node_parent_and_location(mapping_frame, None, (-980, 220))
    set_node_parent_and_location(textures_frame, None, (-560, 300))
    set_node_parent_and_location(uv_source, mapping_frame, (30, -70))
    set_node_parent_and_location(mapping, mapping_frame, (260, -70))
    set_node_parent_and_location(principled, None, (260, 40))
    set_node_parent_and_location(output, None, (650, 40))

    try:
        mapping.vector_type = "POINT"
    except Exception:
        pass

    vector_output = uv_source.outputs.get("UV") or uv_source.outputs.get("Generated") or uv_source.outputs.get("Object")
    link_replace(node_tree, vector_output, mapping.inputs.get("Vector"))
    mapped_vector = mapping.outputs.get("Vector")

    for index, role in enumerate(PBR_BAKE_ROLES):
        node = role_nodes.get(role["key"])
        if node is None:
            continue
        set_node_parent_and_location(node, textures_frame, (40, -40 - index * 205))
        link_replace(node_tree, mapped_vector, node.inputs.get("Vector"))

    normal = role_nodes.get("Normal")
    normal_map = node_tree.nodes.get(f"{PBR_BAKE_NODE_PREFIX}NormalMap")
    if normal_map is not None:
        normal_y = -380
        if normal is not None:
            normal_y = normal.location.y + textures_frame.location.y
        set_node_parent_and_location(normal_map, None, (20, normal_y))

    link_replace(node_tree, principled.outputs.get("BSDF"), output.inputs.get("Surface"))

    keep_nodes = {mapping_frame, textures_frame, uv_source, mapping, principled, output, *role_nodes.values()}
    if normal_map is not None:
        keep_nodes.add(normal_map)
    return keep_nodes


def frame_previous_procedural_nodes(material, keep_nodes, anchor_node=None):
    if material is None or material.node_tree is None:
        return None

    node_tree = material.node_tree
    frame = None
    for node in node_tree.nodes:
        if node.bl_idname == "NodeFrame" and node.label == PBR_BAKE_FRAME_LABEL:
            frame = node
            break
    if frame is None:
        frame = node_tree.nodes.new("NodeFrame")
        frame.name = unique_datablock_name(node_tree.nodes, PBR_BAKE_FRAME_LABEL)
    frame.label = PBR_BAKE_FRAME_LABEL
    for node in node_tree.nodes:
        if node == frame or node in keep_nodes:
            continue
        if node.parent is None:
            node.parent = frame
    place_source_frame_near_principled(frame, anchor_node)
    return frame


def connect_pbr_bake_nodes(material, role_nodes):
    principled = find_or_create_principled_node(material)
    if principled is None or material.node_tree is None:
        return False

    node_tree = material.node_tree
    base = role_nodes.get("BaseColor")
    if base is not None:
        link_replace(node_tree, base.outputs.get("Color"), principled.inputs.get("Base Color"))

    roughness = role_nodes.get("Roughness")
    if roughness is not None:
        link_replace(node_tree, roughness.outputs.get("Color"), principled.inputs.get("Roughness"))

    metallic = role_nodes.get("Metallic")
    if metallic is not None:
        link_replace(node_tree, metallic.outputs.get("Color"), principled.inputs.get("Metallic"))

    normal = role_nodes.get("Normal")
    if normal is not None:
        normal_map = material.node_tree.nodes.get(f"{PBR_BAKE_NODE_PREFIX}NormalMap")
        if normal_map is None or normal_map.bl_idname != "ShaderNodeNormalMap":
            normal_map = material.node_tree.nodes.new("ShaderNodeNormalMap")
            normal_map.name = f"{PBR_BAKE_NODE_PREFIX}NormalMap"
        configure_normal_map_node_for_unity(normal_map)
        normal_map.label = "Normal Map"
        normal_map.location = (normal.location.x + 240, normal.location.y)
        link_replace(material.node_tree, normal.outputs.get("Color"), normal_map.inputs.get("Color"))
        link_replace(material.node_tree, normal_map.outputs.get("Normal"), principled.inputs.get("Normal"))

    return True


def relink_material_to_pbr_images(material, image_by_role, uv_map_name=""):
    if material is None or material.node_tree is None:
        return False

    principled = find_or_create_principled_node(material)
    output = find_or_create_material_output_node(material)
    if principled is None or output is None:
        return False

    role_nodes = {}
    for index, role in enumerate(PBR_BAKE_ROLES):
        node = find_or_create_pbr_bake_image_node(material, role)
        node.image = image_by_role[role["key"]][0]
        set_image_colorspace(node.image, role["colorspace"])
        role_nodes[role["key"]] = node

    connected = connect_pbr_bake_nodes(material, role_nodes)
    keep_nodes = arrange_pbr_bake_node_tree(material, principled, output, role_nodes, uv_map_name)
    frame_previous_procedural_nodes(material, keep_nodes, principled)
    refresh_material_node_ui(material)
    return connected


def pbr_framework_materials_for_context(context):
    selected = list(getattr(context, "selected_objects", []) or [])
    active = getattr(context, "object", None)
    if active is not None and active not in selected:
        selected.append(active)

    mesh_objects = []
    seen_meshes = set()
    for obj in selected:
        candidates = [obj] if getattr(obj, "type", None) == "MESH" else get_asset_meshes(obj)
        for mesh in candidates:
            if getattr(mesh, "type", None) != "MESH" or mesh in seen_meshes:
                continue
            mesh_objects.append(mesh)
            seen_meshes.add(mesh)

    materials = unique_materials_for_meshes(mesh_objects)
    if materials:
        return materials

    active_material = getattr(active, "active_material", None)
    return [active_material] if active_material is not None else []


def pbr_framework_selected_material_names(settings):
    if settings is None:
        return set()
    value = getattr(settings, "pbr_framework_selected_materials", "") or ""
    return {line.strip() for line in value.splitlines() if line.strip()}


def set_pbr_framework_selected_material_names(settings, names):
    if settings is None:
        return
    clean_names = sorted({str(name).strip() for name in names if str(name).strip()})
    settings.pbr_framework_selected_materials = "\n".join(clean_names)


def pbr_framework_filtered_materials_for_context(context, settings, materials=None):
    materials = pbr_framework_materials_for_context(context) if materials is None else materials
    if settings is None or not getattr(settings, "pbr_framework_use_material_filter", False):
        return materials
    selected_names = pbr_framework_selected_material_names(settings)
    return [material for material in materials if material.name in selected_names]


def pbr_framework_role_keys():
    return {role["key"] for role in PBR_BAKE_ROLES}


def pbr_framework_selected_role_keys(settings):
    all_keys = pbr_framework_role_keys()
    if settings is None or not getattr(settings, "pbr_framework_use_role_filter", False):
        return set(all_keys)
    value = getattr(settings, "pbr_framework_selected_roles", "") or ""
    return {line.strip() for line in value.splitlines() if line.strip()} & all_keys


def set_pbr_framework_selected_role_keys(settings, keys):
    if settings is None:
        return
    clean_keys = sorted({str(key).strip() for key in keys if str(key).strip() in pbr_framework_role_keys()})
    settings.pbr_framework_selected_roles = "\n".join(clean_keys)


def pbr_framework_image_name(material, role):
    material_id = sanitize_id(material.name)
    return f"{material_id}_{role['key']}"


def configure_pbr_framework_image(material, role, image, output_root):
    if image is None:
        return None
    directory = pbr_bake_material_output_dir(output_root, material)
    os.makedirs(directory, exist_ok=True)
    image.file_format = "PNG"
    image.filepath_raw = os.path.join(directory, f"{pbr_framework_image_name(material, role)}.png")
    set_image_colorspace(image, role["colorspace"])
    return image


def pbr_framework_default_image_color(role):
    role_key = role.get("key", "")
    if role_key == "Normal":
        return (0.5, 0.5, 1.0, 1.0)
    if role_key == "Roughness":
        return (1.0, 1.0, 1.0, 1.0)
    if role_key == "BaseColor":
        return (0.0, 0.0, 0.0, 0.0)
    return (0.0, 0.0, 0.0, 1.0)


def initialize_pbr_framework_image(image, role):
    if image is None:
        return None
    color = pbr_framework_default_image_color(role)
    try:
        image.generated_type = "BLANK"
    except Exception:
        pass
    try:
        image.generated_color = color
    except Exception:
        pass
    try:
        image.update()
    except Exception:
        pass
    return image


def pbr_framework_image_is_bake_ready(image):
    if image is None:
        return False
    try:
        width, height = int(image.size[0]), int(image.size[1])
    except Exception:
        return False
    if width <= 0 or height <= 0:
        return False
    try:
        return len(image.pixels) >= width * height * 4
    except Exception:
        return False


def create_unique_pbr_framework_image(material, role, resolution, output_root):
    image_name = pbr_framework_image_name(material, role)
    image = bpy.data.images.new(
        unique_datablock_name(bpy.data.images, image_name),
        width=resolution,
        height=resolution,
        alpha=role["key"] == "BaseColor",
        is_data=role["colorspace"] == "Non-Color",
    )
    initialize_pbr_framework_image(image, role)
    return configure_pbr_framework_image(material, role, image, output_root)


def find_or_create_pbr_framework_image(material, role, resolution, output_root):
    return create_unique_pbr_framework_image(material, role, resolution, output_root)


def ensure_unique_pbr_framework_node_image(material, role, node, resolution, output_root):
    image = getattr(node, "image", None) if node is not None else None
    if image is None or not pbr_framework_image_is_bake_ready(image):
        image = find_or_create_pbr_framework_image(material, role, resolution, output_root)
    else:
        image = configure_pbr_framework_image(material, role, image, output_root)
    if node is not None:
        node.image = image
    return image


def pbr_framework_image_target_path(material, role, output_root):
    material_id = sanitize_id(material.name)
    directory = pbr_bake_material_output_dir(output_root, material)
    return os.path.join(directory, f"{material_id}_{role['key']}.png")


def save_pbr_framework_image(material, role, image, output_root):
    if image is None:
        raise RuntimeError(f"{material.name}: {role['label']} has no image.")

    target_path = pbr_framework_image_target_path(material, role, output_root)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    original_filepath = getattr(image, "filepath_raw", "") or getattr(image, "filepath", "")
    try:
        write_image_pixels_to_png(image, target_path)
        verify_saved_bake_image(target_path)
        image.filepath_raw = target_path
    except Exception:
        if original_filepath:
            try:
                image.filepath_raw = original_filepath
            except Exception:
                pass
        raise
    return target_path


def write_image_pixels_to_png(image, path):
    import array
    import struct
    import zlib

    width, height = int(image.size[0]), int(image.size[1])
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Image '{image.name}' has no size.")

    pixel_total = width * height * 4
    pixels = array.array("f", [0.0]) * pixel_total
    try:
        image.pixels.foreach_get(pixels)
    except Exception as exc:
        raise RuntimeError(f"Could not read image pixels for '{image.name}': {exc}") from exc

    def channel_to_byte(value):
        return max(0, min(255, int(round(float(value) * 255.0))))

    raw = bytearray()
    for y in range(height - 1, -1, -1):
        raw.append(0)
        row_start = y * width * 4
        for offset in range(row_start, row_start + width * 4, 4):
            raw.append(channel_to_byte(pixels[offset]))
            raw.append(channel_to_byte(pixels[offset + 1]))
            raw.append(channel_to_byte(pixels[offset + 2]))
            raw.append(channel_to_byte(pixels[offset + 3]))

    def png_chunk(chunk_type, data):
        crc = zlib.crc32(chunk_type)
        crc = zlib.crc32(data, crc)
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(png_chunk(b"IHDR", header))
    png.extend(png_chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
    png.extend(png_chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(png)


def find_or_create_pbr_framework_principled_node(material):
    if material is None or material.node_tree is None:
        return None
    node = material.node_tree.nodes.get(f"{PBR_BAKE_NODE_PREFIX}Principled")
    if node is None or node.bl_idname != "ShaderNodeBsdfPrincipled":
        node = material.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        node.name = f"{PBR_BAKE_NODE_PREFIX}Principled"
    node.label = "Principled BSDF"
    return node


def find_or_create_pbr_framework_output_node(material):
    if material is None or material.node_tree is None:
        return None
    node = material.node_tree.nodes.get(f"{PBR_BAKE_NODE_PREFIX}Output")
    if node is None or node.bl_idname != "ShaderNodeOutputMaterial":
        node = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
        node.name = f"{PBR_BAKE_NODE_PREFIX}Output"
    node.label = "Material Output"
    try:
        node.is_active_output = False
    except Exception:
        pass
    return node


def connect_manual_pbr_framework_nodes(material, principled, output, role_nodes, normal_map):
    if material is None or material.node_tree is None or principled is None:
        return

    node_tree = material.node_tree
    base = role_nodes.get("BaseColor")
    if base is not None:
        link_replace(node_tree, base.outputs.get("Color"), principled.inputs.get("Base Color"))

    roughness = role_nodes.get("Roughness")
    if roughness is not None:
        link_replace(node_tree, roughness.outputs.get("Color"), principled.inputs.get("Roughness"))

    metallic = role_nodes.get("Metallic")
    if metallic is not None:
        link_replace(node_tree, metallic.outputs.get("Color"), principled.inputs.get("Metallic"))

    normal = role_nodes.get("Normal")
    if normal is not None and normal_map is not None:
        link_replace(node_tree, normal.outputs.get("Color"), normal_map.inputs.get("Color"))
        link_replace(node_tree, normal_map.outputs.get("Normal"), principled.inputs.get("Normal"))

    if output is not None:
        link_replace(node_tree, principled.outputs.get("BSDF"), output.inputs.get("Surface"))


def arrange_manual_pbr_framework_nodes(material, role_nodes, normal_map, principled=None, output=None):
    if material is None or material.node_tree is None:
        return set(role_nodes.values())

    node_tree = material.node_tree
    anchor = active_material_output_node(material) or output or principled
    anchor_x = float(getattr(getattr(anchor, "location", None), "x", 260.0) or 260.0)
    anchor_y = float(getattr(getattr(anchor, "location", None), "y", 40.0) or 40.0)
    role_order = [role for role in PBR_BAKE_ROLES if role["key"] in role_nodes]
    texture_stack_count = max(len(role_order), 1)
    textures_y = anchor_y + 120.0 + (texture_stack_count - 1) * 70.0

    mapping_frame = find_or_create_named_frame(node_tree, PBR_BAKE_MAPPING_FRAME_NAME, "Mapping")
    textures_frame = find_or_create_named_frame(node_tree, PBR_BAKE_TEXTURE_FRAME_NAME, "Textures")
    texcoord = find_or_create_pbr_framework_typed_node(
        node_tree,
        PBR_BAKE_TEXCOORD_NODE_NAME,
        "ShaderNodeTexCoord",
        "texcoord",
        "Texture Coordinate",
        PBR_BAKE_MAPPING_FRAME_NAME,
        {"Texture Coordinate", "TexCoord", "Coordinates"},
        True,
    )
    mapping = find_or_create_pbr_framework_typed_node(
        node_tree,
        PBR_BAKE_MAPPING_NODE_NAME,
        "ShaderNodeMapping",
        "mapping",
        "Mapping",
        PBR_BAKE_MAPPING_FRAME_NAME,
        {"Mapping"},
        True,
    )

    set_node_parent_and_location(mapping_frame, None, (anchor_x - 1420.0, textures_y - 80.0))
    set_node_parent_and_location(textures_frame, None, (anchor_x - 900.0, textures_y))
    set_node_parent_and_location(principled, None, (anchor_x - 300.0, anchor_y + 40.0))
    set_node_parent_and_location(output, None, (anchor_x + 90.0, anchor_y + 40.0))
    set_node_parent_and_location(texcoord, mapping_frame, (30.0, -70.0))
    set_node_parent_and_location(mapping, mapping_frame, (260.0, -70.0))

    try:
        mapping.vector_type = "POINT"
    except Exception:
        pass

    vector_output = texcoord.outputs.get("UV") or texcoord.outputs.get("Generated") or texcoord.outputs.get("Object")
    ensure_input_link_if_empty_or_same(node_tree, vector_output, mapping.inputs.get("Vector"))
    mapped_vector = mapping.outputs.get("Vector")

    for index, role in enumerate(role_order):
        node = role_nodes.get(role["key"])
        if node is None:
            continue
        set_node_parent_and_location(node, textures_frame, (40.0, -40.0 - index * 205.0))
        ensure_input_link_if_empty_or_same(node_tree, mapped_vector, node.inputs.get("Vector"))

    normal = role_nodes.get("Normal")
    if normal is not None and normal_map is not None:
        set_node_parent_and_location(normal_map, None, (anchor_x - 540.0, normal.location.y + textures_frame.location.y))
        link_replace(node_tree, normal.outputs.get("Color"), normal_map.inputs.get("Color"))

    keep_nodes = {mapping_frame, textures_frame, texcoord, mapping, *role_nodes.values()}
    if normal_map is not None:
        keep_nodes.add(normal_map)
    if principled is not None:
        keep_nodes.add(principled)
    if output is not None:
        keep_nodes.add(output)
    return keep_nodes


def ensure_manual_pbr_framework_for_material(material, settings, active_role_key="", connect_maps=False, role_keys=None):
    if material is None:
        return None

    material.use_nodes = True
    if material.node_tree is None:
        return None

    resolution = clamp_pbr_bake_size(getattr(settings, "pbr_bake_resolution", 1024))
    output_root = pbr_bake_output_root(settings)
    role_nodes = {}
    active_node = None
    selected_role_keys = set(role_keys) if role_keys is not None else pbr_framework_selected_role_keys(settings)
    prune_manual_pbr_framework_roles(material, selected_role_keys)
    principled = find_or_create_pbr_framework_principled_node(material)
    output = find_or_create_pbr_framework_output_node(material)

    for role in PBR_BAKE_ROLES:
        if role["key"] not in selected_role_keys:
            continue
        node = find_or_create_pbr_bake_image_node(material, role)
        ensure_unique_pbr_framework_node_image(material, role, node, resolution, output_root)
        role_nodes[role["key"]] = node
        if role["key"] == active_role_key:
            active_node = node

    normal_map = None
    if "Normal" in role_nodes:
        normal_map = find_or_create_pbr_framework_typed_node(
            material.node_tree,
            f"{PBR_BAKE_NODE_PREFIX}NormalMap",
            "ShaderNodeNormalMap",
            "normal_map",
            "Normal Map",
            "",
            {"Normal Map", "NormalMap"},
            False,
        )
        configure_normal_map_node_for_unity(normal_map)

    keep_nodes = arrange_manual_pbr_framework_nodes(material, role_nodes, normal_map, principled, output)
    connect_manual_pbr_framework_nodes(material, principled, output, role_nodes, normal_map)
    frame_manual_pbr_original_nodes(material, keep_nodes, principled)

    if connect_maps:
        principled = find_or_create_principled_node(material)
        output = find_or_create_material_output_node(material)
        connect_pbr_bake_nodes(material, role_nodes)
        if principled is not None and output is not None:
            link_replace(material.node_tree, principled.outputs.get("BSDF"), output.inputs.get("Surface"))

    if active_node is not None:
        for node in material.node_tree.nodes:
            node.select = False
        active_node.select = True
        material.node_tree.nodes.active = active_node

    refresh_material_node_ui(material)
    return active_node


def create_manual_pbr_framework(context, settings, active_role_key="", connect_maps=False):
    materials = pbr_framework_filtered_materials_for_context(context, settings)
    if not materials:
        raise RuntimeError("Choose at least one material.")
    role_keys = pbr_framework_selected_role_keys(settings)
    if not role_keys:
        raise RuntimeError("Choose at least one texture map.")

    active_count = 0
    for material in materials:
        active_node = ensure_manual_pbr_framework_for_material(
            material,
            settings,
            active_role_key=active_role_key,
            connect_maps=connect_maps,
            role_keys=role_keys,
        )
        if active_node is not None:
            active_count += 1

    return {
        "material_count": len(materials),
        "role_count": len(role_keys),
        "active_count": active_count,
    }


def save_manual_pbr_framework_images(context, settings):
    materials = pbr_framework_filtered_materials_for_context(context, settings)
    if not materials:
        raise RuntimeError("Choose at least one material.")
    role_keys = pbr_framework_selected_role_keys(settings)
    if not role_keys:
        raise RuntimeError("Choose at least one texture map.")

    output_root = pbr_bake_output_root(settings)
    saved_files = []
    skipped = []
    errors = []
    for material in materials:
        for role in PBR_BAKE_ROLES:
            if role["key"] not in role_keys:
                continue
            node = find_pbr_framework_target_image_node(material, role)
            image = getattr(node, "image", None) if node is not None else None
            if image is None:
                skipped.append(f"{material.name}: {role['label']}")
                continue
            try:
                saved_files.append(save_pbr_framework_image(material, role, image, output_root))
            except Exception as exc:
                errors.append(f"{material.name} {role['label']}: {exc}")

    if errors:
        raise RuntimeError("; ".join(errors[:3]))
    if not saved_files:
        raise RuntimeError("No PBR target images to save.")

    return {
        "material_count": len(materials),
        "role_count": len(role_keys),
        "saved_count": len(saved_files),
        "skipped_count": len(skipped),
        "files": saved_files,
        "skipped": skipped,
        "output_root": output_root,
    }


def pbr_framework_selected_roles_in_order(settings):
    selected_role_keys = pbr_framework_selected_role_keys(settings)
    return [role for role in PBR_BAKE_ROLES if role["key"] in selected_role_keys]


def find_pbr_framework_target_image_node(material, role):
    if material is None or material.node_tree is None:
        return None

    expected_name = f"{PBR_BAKE_NODE_PREFIX}{role['key']}"
    node = material.node_tree.nodes.get(expected_name)
    if node is not None and node.bl_idname == "ShaderNodeTexImage":
        return node

    for candidate in material.node_tree.nodes:
        if candidate.bl_idname != "ShaderNodeTexImage":
            continue
        if not node_name_starts_pbr_framework_prefix(candidate):
            continue
        if node_custom_prop(candidate, PBR_FRAMEWORK_ROLE_PROP) == role["key"]:
            return candidate
    return None


def image_has_pbr_bake_content(image):
    if image is None:
        return False
    try:
        if bool(image.is_dirty):
            return True
    except Exception:
        pass

    try:
        width, height = int(image.size[0]), int(image.size[1])
    except Exception:
        return False
    if width <= 0 or height <= 0:
        return False

    try:
        default = tuple(float(value) for value in getattr(image, "generated_color", (0.0, 0.0, 0.0, 1.0)))
    except Exception:
        default = (0.0, 0.0, 0.0, 1.0)
    if len(default) < 4:
        default = tuple(default) + (1.0,) * (4 - len(default))

    pixel_count = width * height
    sample_count = min(pixel_count, 256)
    step = max(1, pixel_count // max(1, sample_count))
    tolerance = 1.0 / 255.0
    try:
        pixels = image.pixels
        for pixel_index in range(0, pixel_count, step):
            offset = pixel_index * 4
            if any(abs(float(pixels[offset + channel]) - default[channel]) > tolerance for channel in range(4)):
                return True
    except Exception:
        return False
    return False


def select_pbr_framework_image_node(material, node):
    if material is None or material.node_tree is None or node is None:
        return False
    for candidate in material.node_tree.nodes:
        candidate.select = False
    node.select = True
    material.node_tree.nodes.active = node
    refresh_material_node_ui(material)
    return True


def activate_material_on_selected_object(context, material):
    if material is None:
        return None

    active = getattr(context.view_layer.objects, "active", None)
    candidates = []
    if active is not None:
        candidates.append(active)
    candidates.extend(obj for obj in getattr(context, "selected_objects", []) or [] if obj not in candidates)

    for obj in candidates:
        if getattr(obj, "type", None) != "MESH":
            continue
        for index, slot in enumerate(getattr(obj, "material_slots", []) or []):
            if slot.material != material:
                continue
            try:
                obj.select_set(True)
                context.view_layer.objects.active = obj
                obj.active_material_index = index
            except Exception:
                pass
            return obj
    return None


def set_image_editor_image(context, image):
    if image is None:
        return
    for area in getattr(context.screen, "areas", []) or []:
        if area.type != "IMAGE_EDITOR":
            continue
        for space in area.spaces:
            if space.type == "IMAGE_EDITOR":
                space.image = image
        area.tag_redraw()


def configure_native_bake_for_pbr_role(context, role):
    scene = context.scene
    try:
        scene.render.engine = "CYCLES"
    except Exception:
        pass

    bake_type = PBR_FRAMEWORK_NATIVE_BAKE_TYPES.get(role["key"], role.get("bake_type", "EMIT"))
    try:
        scene.cycles.bake_type = bake_type
    except Exception:
        pass

    bake_settings = getattr(scene.render, "bake", None)
    if bake_settings is not None:
        try:
            bake_settings.target = "IMAGE_TEXTURES"
        except Exception:
            pass
        try:
            bake_settings.save_mode = "INTERNAL"
        except Exception:
            pass
        try:
            bake_settings.use_clear = True
        except Exception:
            pass

        if role["key"] == "BaseColor":
            for attr, value in (
                ("use_pass_direct", False),
                ("use_pass_indirect", False),
                ("use_pass_color", True),
                ("use_pass_diffuse", True),
            ):
                try:
                    setattr(bake_settings, attr, value)
                except Exception:
                    pass
        elif role["key"] == "Normal":
            for attr, value in (
                ("normal_space", "TANGENT"),
                ("normal_r", "POS_X"),
                ("normal_g", "POS_Y"),
                ("normal_b", "POS_Z"),
            ):
                try:
                    setattr(bake_settings, attr, value)
                except Exception:
                    pass
    return bake_type


def prepare_next_pbr_framework_bake_target(context, settings):
    materials = pbr_framework_filtered_materials_for_context(context, settings)
    if not materials:
        raise RuntimeError("Choose at least one material.")
    roles = pbr_framework_selected_roles_in_order(settings)
    if not roles:
        raise RuntimeError("Choose at least one texture map.")

    resolution = clamp_pbr_bake_size(getattr(settings, "pbr_bake_resolution", 1024))
    output_root = pbr_bake_output_root(settings)
    entries_by_role = {}
    missing = []
    for role in roles:
        entries = []
        for material in materials:
            node = find_pbr_framework_target_image_node(material, role)
            image = getattr(node, "image", None) if node is not None else None
            if node is None or image is None:
                missing.append(f"{material.name}: {role['label']}")
                continue
            if not pbr_framework_image_is_bake_ready(image):
                image = find_or_create_pbr_framework_image(material, role, resolution, output_root)
                node.image = image
            entries.append((material, node, image))
        entries_by_role[role["key"]] = entries

    if missing:
        raise RuntimeError(f"Run Create Targets first; missing {', '.join(missing[:3])}.")

    chosen_role = None
    all_targets_have_content = False
    for role in roles:
        entries = entries_by_role.get(role["key"], [])
        if any(not image_has_pbr_bake_content(image) for _material, _node, image in entries):
            chosen_role = role
            break
    if chosen_role is None:
        chosen_role = roles[0]
        all_targets_have_content = True

    entries = entries_by_role[chosen_role["key"]]
    for material, node, _image in entries:
        select_pbr_framework_image_node(material, node)

    primary_material, _primary_node, primary_image = entries[0]
    active_object = activate_material_on_selected_object(context, primary_material)
    set_image_editor_image(context, primary_image)
    bake_type = configure_native_bake_for_pbr_role(context, chosen_role)

    return {
        "role": chosen_role,
        "role_label": chosen_role["label"],
        "bake_type": bake_type,
        "material_count": len(materials),
        "image": primary_image.name,
        "active_object": active_object.name if active_object is not None else "",
        "all_targets_have_content": all_targets_have_content,
    }


def pbr_bake_progress_begin(context, total_steps):
    window_manager = getattr(context, "window_manager", None)
    settings = getattr(getattr(context, "scene", None), "rr_builder_export_settings", None)
    if settings is not None:
        settings.pbr_bake_running = True
        settings.pbr_bake_progress = 0.0
        settings.pbr_bake_status = "Preparing PBR bake"

    if window_manager is None:
        refresh_rr_helper_ui(context)
        return None

    try:
        window_manager.progress_begin(0, max(1, total_steps))
        window_manager.progress_update(0)
        refresh_rr_helper_ui(context)
        return window_manager
    except Exception:
        refresh_rr_helper_ui(context)
        return None


def pbr_bake_progress_text(step, total_steps, message):
    max_steps = max(1.0, float(total_steps))
    percent = int(round(min(max(0.0, float(step) / max_steps), 1.0) * 100.0))
    return f"PBR Bake {percent}% - {message}"


def pbr_bake_progress_update(context, window_manager, step, total_steps, message, reporter=None):
    max_steps = max(1, total_steps)
    progress = min(max(0.0, float(step) / float(max_steps)), 1.0)
    progress_message = pbr_bake_progress_text(step, total_steps, message)
    settings = getattr(getattr(context, "scene", None), "rr_builder_export_settings", None)
    if settings is not None:
        settings.pbr_bake_progress = progress
        settings.pbr_bake_status = progress_message

    if window_manager is not None:
        try:
            window_manager.progress_update(min(max(0, int(round(step))), max_steps))
        except Exception:
            pass

    workspace = getattr(context, "workspace", None)
    status_text_set = getattr(workspace, "status_text_set", None)
    if callable(status_text_set):
        try:
            status_text_set(progress_message)
        except Exception:
            pass
    if callable(reporter):
        try:
            reporter({"INFO"}, progress_message)
        except Exception:
            pass
    refresh_rr_helper_ui(context)


def pbr_bake_progress_end(context, window_manager):
    if window_manager is not None:
        try:
            window_manager.progress_end()
        except Exception:
            pass

    settings = getattr(getattr(context, "scene", None), "rr_builder_export_settings", None)
    if settings is not None:
        settings.pbr_bake_running = False

    workspace = getattr(context, "workspace", None)
    status_text_set = getattr(workspace, "status_text_set", None)
    if callable(status_text_set):
        try:
            status_text_set(None)
        except Exception:
            pass
    refresh_rr_helper_ui(context)


def bake_selected_to_pbr(context, settings, reporter=None):
    source_roots = get_context_export_roots(context)
    roots = expand_related_export_roots(source_roots)
    if not roots:
        raise RuntimeError("Select one or more mesh assets or group members before baking.")

    mesh_objects = []
    seen_meshes = set()
    for root in roots:
        for mesh in get_asset_meshes(root):
            if mesh in seen_meshes:
                continue
            mesh_objects.append(mesh)
            seen_meshes.add(mesh)

    if not mesh_objects:
        raise RuntimeError("Selected assets have no mesh objects to bake.")

    all_materials = unique_materials_for_meshes(mesh_objects)
    if not all_materials:
        raise RuntimeError("Selected meshes have no materials to bake.")

    bakeable_materials = [material for material in all_materials if material_needs_pbr_bake(material)]
    skipped_materials = [material.name for material in all_materials if material not in bakeable_materials]
    disabled_material_names = pbr_bake_disabled_material_names(settings)
    disabled_materials = [material.name for material in bakeable_materials if material.name in disabled_material_names]
    materials = [material for material in bakeable_materials if material.name not in disabled_material_names]
    if not materials:
        output_root = pbr_bake_output_root(settings)
        return {
            "roots": roots,
            "mesh_count": len(mesh_objects),
            "material_count": 0,
            "skipped_material_count": len(skipped_materials),
            "skipped_materials": skipped_materials,
            "disabled_material_count": len(disabled_materials),
            "disabled_materials": disabled_materials,
            "image_count": 0,
            "relinked_count": 0,
            "output_root": output_root,
            "output_dir": output_root,
            "files": [],
        }
    bake_material_set = set(materials)
    bake_mesh_objects = [
        mesh
        for mesh in mesh_objects
        if any(slot.material in bake_material_set for slot in mesh.material_slots)
    ]
    if not bake_mesh_objects:
        raise RuntimeError("Selected meshes have no procedural or linked materials to bake.")
    bake_uv_map_names = bake_uv_map_names_by_material(bake_mesh_objects, bake_material_set)

    resolution = clamp_pbr_bake_size(getattr(settings, "pbr_bake_resolution", 1024))
    output_root = pbr_bake_output_root(settings)
    batch_name = object_manager_display_name(source_roots[0]) if len(source_roots) == 1 else "Selected"
    material_images = {}
    for material in materials:
        material_images[material] = {
            role["key"]: create_bake_image(material, batch_name, role, resolution, output_root)
            for role in PBR_BAKE_ROLES
        }
    output_files = [
        path
        for images_by_role in material_images.values()
        for _image, path in images_by_role.values()
    ]
    output_dir = os.path.dirname(output_files[0]) if output_files else output_root

    scene = context.scene
    original_engine = scene.render.engine
    original_samples = getattr(scene.cycles, "samples", None) if hasattr(scene, "cycles") else None
    original_active = context.view_layer.objects.active
    original_selection = list(context.selected_objects)
    original_uv_state = capture_mesh_uv_state(bake_mesh_objects)
    progress_total = len(PBR_BAKE_ROLES) + len(materials) + 1
    progress_step = 0
    progress_window_manager = pbr_bake_progress_begin(context, progress_total)

    try:
        pbr_bake_progress_update(
            context,
            progress_window_manager,
            progress_step,
            progress_total,
            "Preparing PBR bake",
            reporter,
        )
        scene.render.engine = "CYCLES"
        if hasattr(scene, "cycles"):
            scene.cycles.samples = max(1, int(getattr(settings, "pbr_bake_samples", 32)))

        bpy.ops.object.select_all(action="DESELECT")
        for mesh in bake_mesh_objects:
            mesh.select_set(True)
        context.view_layer.objects.active = bake_mesh_objects[0]
        apply_bake_uv_layers(bake_mesh_objects, bake_material_set)

        for role in PBR_BAKE_ROLES:
            pbr_bake_progress_update(
                context,
                progress_window_manager,
                progress_step + 0.5,
                progress_total,
                f"Baking {role['label']}",
                reporter,
            )
            activate_bake_image_nodes(material_images, role)
            emit_restores = []
            try:
                if role["bake_type"] == "EMIT":
                    emit_restores = prepare_emit_socket_bake(materials, role["socket"])
                bake_active_meshes(role, settings)
            finally:
                restore_actions(emit_restores)
            for image, path in (images[role["key"]] for images in material_images.values()):
                image.save()
                verify_saved_bake_image(path)
            progress_step += 1
            pbr_bake_progress_update(
                context,
                progress_window_manager,
                progress_step,
                progress_total,
                f"Saved {role['label']}",
                reporter,
            )

        relinked = 0
        for material, image_by_role in material_images.items():
            pbr_bake_progress_update(
                context,
                progress_window_manager,
                progress_step + 0.5,
                progress_total,
                f"Linking {material.name}",
                reporter,
            )
            if relink_material_to_pbr_images(material, image_by_role, bake_uv_map_names.get(material, "")):
                relinked += 1
                material["rr_pbr_baked_at_utc"] = datetime.now(timezone.utc).isoformat()
                material["rr_pbr_bake_output_root"] = output_root
                refresh_material_node_ui(material, context)
            progress_step += 1
        if relinked != len(materials):
            raise RuntimeError(f"Baked files were saved, but only relinked {relinked}/{len(materials)} material(s).")
        pbr_bake_progress_update(
            context,
            progress_window_manager,
            progress_total,
            progress_total,
            "PBR bake complete",
            reporter,
        )
    finally:
        pbr_bake_progress_end(context, progress_window_manager)
        restore_mesh_uv_state(original_uv_state)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in original_selection:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if original_active is not None and original_active.name in bpy.data.objects:
            context.view_layer.objects.active = original_active
        scene.render.engine = original_engine
        if original_samples is not None and hasattr(scene, "cycles"):
            scene.cycles.samples = original_samples
    return {
        "roots": roots,
        "mesh_count": len(mesh_objects),
        "material_count": len(materials),
        "skipped_material_count": len(skipped_materials),
        "skipped_materials": skipped_materials,
        "disabled_material_count": len(disabled_materials),
        "disabled_materials": disabled_materials,
        "image_count": len(materials) * len(PBR_BAKE_ROLES),
        "relinked_count": relinked,
        "output_root": output_root,
        "output_dir": output_dir,
        "files": output_files,
    }


def cleanup_unlinked_texture_helper_nodes(material):
    if material is None or material.node_tree is None:
        return []
    removed = []
    changed = True
    while changed:
        changed = False
        for node in list(material.node_tree.nodes):
            if node.bl_idname not in {"ShaderNodeNormalMap", "ShaderNodeBump"}:
                continue
            if any(output.links for output in node.outputs):
                continue
            removed.append(node.name)
            material.node_tree.nodes.remove(node)
            changed = True
    return removed


def remove_texture_package(package):
    manifest = package["manifest"]
    role = package["role"]
    material = find_material_by_manifest(manifest)
    if material is None:
        raise RuntimeError(f"Material not found: {manifest.get('material', '<unnamed>')}")
    if material.node_tree is None:
        raise RuntimeError(f"Material has no node tree: {material.name}")

    node = find_manifest_image_node(material, manifest)
    if node is None:
        raise RuntimeError(f"Texture node not found: {manifest.get('oldNode') or manifest.get('oldTexture') or role['label']}")

    source_path = image_source_path(node.image)
    node_name = node.name
    material.node_tree.nodes.remove(node)
    cleanup_removed = cleanup_unlinked_texture_helper_nodes(material)
    material["rr_last_texture_package"] = package["package_dir"]
    material["rr_last_texture_package_applied_utc"] = datetime.now(timezone.utc).isoformat()
    return {
        "material": material.name,
        "role": role["label"],
        "source": source_path,
        "destination": "",
        "changed": True,
        "backup": "",
        "operation": "removed",
        "node": node_name,
        "cleanupRemoved": cleanup_removed,
    }


def apply_texture_package(package):
    manifest = package["manifest"]
    role = package["role"]
    if manifest_requests_texture_removal(manifest):
        return remove_texture_package(package)

    material = find_material_by_manifest(manifest)
    if material is None:
        raise RuntimeError(f"Material not found: {manifest.get('material', '<unnamed>')}")

    material.use_nodes = True
    principled = find_principled_node(material)
    if principled is None or material.node_tree is None:
        raise RuntimeError(f"Material has no Principled BSDF: {material.name}")

    destination = package_destination_path(manifest, role, package["source"])
    changed, backup_path = copy_package_texture_to_destination(package["source"], destination)
    image = load_or_reload_image(destination, role["colorspace"])

    preferred_node = find_manifest_image_node(material, manifest)
    node = find_or_create_image_node_for_role(material, principled, role, preferred_node)
    node.image = image
    if manifest.get("oldNode"):
        node.name = manifest["oldNode"]
    node.label = role["label"]
    uv_map_name = material_uv_map_name(material, manifest)
    ensure_image_node_uv_vector(material, node, uv_map_name)
    connect_texture_role(material, principled, role, node)
    repaired_uv_links = ensure_material_image_uv_vectors(material, uv_map_name)

    material["rr_last_texture_package"] = package["package_dir"]
    material["rr_last_texture_package_applied_utc"] = datetime.now(timezone.utc).isoformat()
    return {
        "material": material.name,
        "role": role["label"],
        "source": package["source"],
        "destination": destination,
        "changed": changed,
        "backup": backup_path,
        "uvLinksRepaired": repaired_uv_links,
    }


def write_texture_package_applied_marker(package, result):
    marker_path = os.path.join(package["package_dir"], TEXTURE_PACKAGE_APPLIED_FILENAME)
    payload = {
        "applied": True,
        "appliedUtc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "package": package["package_dir"],
        "manifest": package.get("manifest_path", ""),
        "result": result,
    }
    try:
        with open(marker_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except OSError as exc:
        print("[RandomRealm Builder Exporter]", f"Failed to write applied marker: {exc}")


def socket_has_direct_image(socket):
    if socket is None:
        return False

    return any(link.from_node.bl_idname == "ShaderNodeTexImage" for link in socket.links)


def replace_socket_links_temporarily(node_tree, socket, from_socket, restore_actions, label):
    if node_tree is None or socket is None or from_socket is None:
        return False

    original_links = [(link.from_socket, link.to_socket) for link in socket.links]
    if len(original_links) == 1 and original_links[0][0] == from_socket:
        return False

    def restore():
        for link in list(socket.links):
            node_tree.links.remove(link)
        for original_from, original_to in original_links:
            node_tree.links.new(original_from, original_to)

    # Register the undo before the first graph mutation. If removing an old
    # link or creating the temporary link fails, the caller can still restore
    # the exact original network.
    restore_actions.append(restore)
    for link in list(socket.links):
        node_tree.links.remove(link)

    node_tree.links.new(from_socket, socket)
    return True


def prepare_material_maps_for_unity(root):
    warnings = []
    restore_actions = []
    seen_materials = set()

    try:
        for obj in get_export_asset_meshes(root):
            for slot in obj.material_slots:
                material = slot.material
                if material is None or material.name in seen_materials:
                    continue

                seen_materials.add(material.name)
                principled = find_principled_node(material)
                if principled is None or material.node_tree is None:
                    continue

                base_socket = principled.inputs.get("Base Color")
                base_node = find_image_node(material, ("basecolor", "base_color", "albedo", "diffuse", "diff"))
                if (
                    base_socket is not None
                    and base_node is not None
                    and not socket_has_direct_image(base_socket)
                    and replace_socket_links_temporarily(
                        material.node_tree,
                        base_socket,
                        base_node.outputs.get("Color"),
                        restore_actions,
                        "Base Color",
                    )
                ):
                    warnings.append(
                        f"{root.name}: material '{material.name}' Base Color was not directly linked to an image; "
                        f"temporarily exporting '{base_node.image.name}' for Unity."
                    )

                rough_socket = principled.inputs.get("Roughness")
                rough_node = find_image_node(material, ("roughness", "rough"))
                if (
                    rough_socket is not None
                    and rough_node is not None
                    and not socket_has_direct_image(rough_socket)
                    and replace_socket_links_temporarily(
                        material.node_tree,
                        rough_socket,
                        rough_node.outputs.get("Color"),
                        restore_actions,
                        "Roughness",
                    )
                ):
                    warnings.append(
                        f"{root.name}: material '{material.name}' Roughness was not directly linked to an image; "
                        f"temporarily exporting '{rough_node.image.name}' for Unity."
                    )

                if base_node is None and base_socket is not None and base_socket.is_linked:
                    warnings.append(
                        f"{root.name}: material '{material.name}' has a linked Base Color network but no exportable basecolor/diffuse image map."
                    )

        return restore_actions, warnings
    except Exception:
        rr_unity_uv_export_contract.restore_actions_best_effort(
            restore_actions,
            f"{getattr(root, 'name', '<asset>')} material-map prepare",
        )
        raise


def principled_base_image_node(material):
    principled = find_principled_node(material)
    if principled is not None:
        base_socket = principled.inputs.get("Base Color")
        direct = direct_image_node_from_socket(base_socket)
        if direct is not None:
            return direct

    return find_image_node(material, ("basecolor", "base_color", "albedo", "diffuse", "diff", "color"))


def mapping_input_vector(mapping_node):
    if mapping_node is None or mapping_node.bl_idname != "ShaderNodeMapping":
        return None

    vector_input = mapping_node.inputs.get("Vector")
    if vector_input is None or not vector_input.links:
        return None

    return vector_input.links[0].from_node, vector_input.links[0].from_socket


def trace_image_texture_vector_source(image_node):
    if image_node is None or image_node.bl_idname != "ShaderNodeTexImage":
        return "UV", "", [], False

    vector_input = image_node.inputs.get("Vector")
    if vector_input is None or not vector_input.links:
        return "Generated", "", [], False

    mappings = []
    from_node = vector_input.links[0].from_node
    from_socket = vector_input.links[0].from_socket
    for _ in range(8):
        if from_node is None:
            break

        if from_node.bl_idname == "ShaderNodeMapping":
            mappings.append(from_node)
            next_link = mapping_input_vector(from_node)
            if next_link is None:
                return "Generated", "", list(reversed(mappings)), False
            from_node, from_socket = next_link
            continue

        if from_node.bl_idname == "ShaderNodeUVMap":
            return "UV", getattr(from_node, "uv_map", "") or "", list(reversed(mappings)), False

        if from_node.bl_idname == "ShaderNodeTexCoord":
            socket_name = getattr(from_socket, "name", "") or "UV"
            if socket_name in {"UV", "Generated", "Object"}:
                return socket_name, "", list(reversed(mappings)), False
            return "UV", "", list(reversed(mappings)), True

        return "UV", "", list(reversed(mappings)), True

    return "UV", "", list(reversed(mappings)), True


def mapping_node_values(mapping_node):
    location = mapping_node.inputs.get("Location")
    rotation = mapping_node.inputs.get("Rotation")
    scale = mapping_node.inputs.get("Scale")
    return (
        Vector(location.default_value if location is not None else (0.0, 0.0, 0.0)),
        Vector(rotation.default_value if rotation is not None else (0.0, 0.0, 0.0)),
        Vector(scale.default_value if scale is not None else (1.0, 1.0, 1.0)),
    )


def is_mapping_node_identity(mapping_node):
    if mapping_node is None or mapping_node.bl_idname != "ShaderNodeMapping":
        return True

    location_value, rotation_value, scale_value = mapping_node_values(mapping_node)
    epsilon = 0.00001
    return (
        all(abs(value) <= epsilon for value in location_value)
        and all(abs(value) <= epsilon for value in rotation_value)
        and all(abs(value - 1.0) <= epsilon for value in scale_value)
    )


def apply_mapping_node_to_vector(mapping_node, vector):
    if mapping_node is None or mapping_node.bl_idname != "ShaderNodeMapping":
        return vector

    location_value, rotation_value, scale_value = mapping_node_values(mapping_node)
    mapped = Vector((
        vector.x * scale_value.x,
        vector.y * scale_value.y,
        vector.z * scale_value.z,
    ))
    mapped = Matrix.Rotation(rotation_value.x, 4, "X") @ mapped
    mapped = Matrix.Rotation(rotation_value.y, 4, "Y") @ mapped
    mapped = Matrix.Rotation(rotation_value.z, 4, "Z") @ mapped
    if getattr(mapping_node, "vector_type", "POINT") != "VECTOR":
        mapped += location_value
    return mapped


def mesh_local_bounds(mesh):
    if mesh is None or len(mesh.vertices) == 0:
        return Vector((0.0, 0.0, 0.0)), Vector((1.0, 1.0, 1.0))

    min_v = Vector((
        min(vertex.co.x for vertex in mesh.vertices),
        min(vertex.co.y for vertex in mesh.vertices),
        min(vertex.co.z for vertex in mesh.vertices),
    ))
    max_v = Vector((
        max(vertex.co.x for vertex in mesh.vertices),
        max(vertex.co.y for vertex in mesh.vertices),
        max(vertex.co.z for vertex in mesh.vertices),
    ))
    return min_v, max_v - min_v


def generated_coordinate_for_vertex(mesh, vertex_index, min_v, size):
    co = mesh.vertices[vertex_index].co
    return Vector((
        0.0 if abs(size.x) <= 0.00001 else (co.x - min_v.x) / size.x,
        0.0 if abs(size.y) <= 0.00001 else (co.y - min_v.y) / size.y,
        0.0 if abs(size.z) <= 0.00001 else (co.z - min_v.z) / size.z,
    ))


def object_coordinate_for_vertex(mesh, vertex_index):
    co = mesh.vertices[vertex_index].co
    return Vector((co.x, co.y, co.z))


def material_unity_uv_recipe(material, mesh):
    image_node = principled_base_image_node(material)
    source_type, uv_map_name, mappings, unsupported = trace_image_texture_vector_source(image_node)
    active_name = mesh.uv_layers.active.name if mesh.uv_layers and mesh.uv_layers.active else ""
    source_layer = None
    if source_type == "UV" and mesh.uv_layers:
        source_layer = mesh.uv_layers.get(uv_map_name) if uv_map_name else mesh.uv_layers.active
        if source_layer is None:
            source_layer = mesh.uv_layers.active or mesh.uv_layers[0]

    unsupported = unsupported or any(
        getattr(mapping, "vector_type", "POINT") not in {"POINT", "VECTOR"}
        for mapping in mappings
    )
    needs_bake = (
        unsupported
        or source_type != "UV"
        or any(not is_mapping_node_identity(mapping) for mapping in mappings)
        or (source_layer is not None and source_layer.name != active_name)
    )

    return {
        "source_type": source_type,
        "source_layer": source_layer,
        "source_layer_name": source_layer.name if source_layer is not None else "",
        "mappings": mappings,
        "unsupported": unsupported,
        "needs_bake": needs_bake,
    }


def evaluate_unity_export_uv(mesh, loop_index, vertex_index, recipe, fallback_layer, min_v, size):
    source_type = recipe.get("source_type", "UV") if recipe else "UV"
    source_layer = recipe.get("source_layer") if recipe else None

    if source_type == "Generated":
        vector = generated_coordinate_for_vertex(mesh, vertex_index, min_v, size)
    elif source_type == "Object":
        vector = object_coordinate_for_vertex(mesh, vertex_index)
    else:
        layer = source_layer or fallback_layer
        if layer is None or loop_index >= len(layer.data):
            vector = Vector((0.0, 0.0, 0.0))
        else:
            uv = layer.data[loop_index].uv
            vector = Vector((uv.x, uv.y, 0.0))

    for mapping in recipe.get("mappings", ()) if recipe else ():
        vector = apply_mapping_node_to_vector(mapping, vector)
    return vector.to_2d()


def prepare_material_uvs_for_unity(root):
    warnings = []
    restore_actions = []
    mapping_states = {}

    for obj in get_export_asset_meshes(root):
        mesh = obj.data
        uv_layers = mesh.uv_layers
        if len(uv_layers) == 0:
            continue

        fallback_layer = uv_layers.active or uv_layers[0]
        recipes = {}
        needs_bake = False
        unsupported_materials = []
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.name in recipes:
                continue

            recipe = material_unity_uv_recipe(material, mesh)
            recipes[material.name] = recipe
            needs_bake = needs_bake or recipe["needs_bake"]
            if recipe["unsupported"]:
                unsupported_materials.append(material.name)
            for mapping in recipe["mappings"]:
                if not is_mapping_node_identity(mapping) and mapping not in mapping_states:
                    mapping_states[mapping] = mapping_node_values(mapping)

        if not needs_bake:
            continue

        previous_active_name = fallback_layer.name
        export_layer = uv_layers.new(name=UNITY_EXPORT_UV_LAYER_NAME)
        min_v, size = mesh_local_bounds(mesh)

        for polygon in mesh.polygons:
            material = None
            if polygon.material_index < len(obj.material_slots):
                material = obj.material_slots[polygon.material_index].material
            recipe = recipes.get(material.name) if material is not None else None
            for loop_index in polygon.loop_indices:
                export_layer.data[loop_index].uv = evaluate_unity_export_uv(
                    mesh,
                    loop_index,
                    mesh.loops[loop_index].vertex_index,
                    recipe,
                    fallback_layer,
                    min_v,
                    size,
                )

        uv_layers.active = export_layer

        def restore_export_uv(mesh=mesh, layer_name=export_layer.name, active_name=previous_active_name):
            layer = mesh.uv_layers.get(layer_name)
            if layer is not None:
                mesh.uv_layers.remove(layer)
            active = mesh.uv_layers.get(active_name)
            if active is not None:
                mesh.uv_layers.active = active

        restore_actions.append(restore_export_uv)
        detail_parts = []
        for material_name, recipe in recipes.items():
            if not recipe["needs_bake"]:
                continue
            source = recipe["source_type"]
            if recipe["source_layer_name"]:
                source += f"({recipe['source_layer_name']})"
            if recipe["mappings"]:
                source += "+Mapping"
            detail_parts.append(f"{material_name}:{source}")

        print(
            "[RR Helper]",
            f"{root.name}: mesh '{obj.name}' baked material texture coordinates into '{export_layer.name}' for Unity FBX export"
            + (f" ({', '.join(detail_parts)})." if detail_parts else "."),
        )
        for material_name in unsupported_materials:
            warnings.append(
                f"{root.name}: material '{material_name}' uses texture coordinates that cannot be fully represented in Unity; exported the closest UV fallback."
            )

    for mapping, (location_value, rotation_value, scale_value) in mapping_states.items():
        location = mapping.inputs.get("Location")
        rotation = mapping.inputs.get("Rotation")
        scale = mapping.inputs.get("Scale")
        if location is not None:
            location.default_value = (0.0, 0.0, 0.0)
        if rotation is not None:
            rotation.default_value = (0.0, 0.0, 0.0)
        if scale is not None:
            scale.default_value = (1.0, 1.0, 1.0)

        def restore_mapping_node(
            mapping=mapping,
            location_value=location_value.copy(),
            rotation_value=rotation_value.copy(),
            scale_value=scale_value.copy(),
        ):
            location = mapping.inputs.get("Location")
            rotation = mapping.inputs.get("Rotation")
            scale = mapping.inputs.get("Scale")
            if location is not None:
                location.default_value = location_value
            if rotation is not None:
                rotation.default_value = rotation_value
            if scale is not None:
                scale.default_value = scale_value

        restore_actions.append(restore_mapping_node)

    return restore_actions, warnings


def prepare_uv_maps_for_unity(root):
    warnings = []
    restore_actions = []

    for obj in get_export_asset_meshes(root):
        mesh = obj.data
        uv_layers = mesh.uv_layers
        if len(uv_layers) == 0 or uv_layers.active is None:
            warnings.append(f"{root.name}: mesh '{obj.name}' has no active UV Map.")
            continue

        active = uv_layers.active
        first = uv_layers[0]
        if active == first:
            continue

        if len(active.data) != len(first.data):
            warnings.append(
                f"{root.name}: mesh '{obj.name}' active UV Map '{active.name}' cannot be copied to UV0 because loop counts differ."
            )
            continue

        original = [loop.uv.copy() for loop in first.data]
        for index, loop in enumerate(first.data):
            loop.uv = active.data[index].uv

        def restore_first_uv(mesh=mesh, first_name=first.name, original_values=original):
            target = mesh.uv_layers.get(first_name)
            if target is None:
                return
            for index, uv in enumerate(original_values):
                if index < len(target.data):
                    target.data[index].uv = uv

        restore_actions.append(restore_first_uv)
        message = f"{root.name}: active UV Map '{active.name}' was not UV0; copied it into '{first.name}' for Unity FBX export."
        if active.name == UNITY_EXPORT_UV_LAYER_NAME:
            print("[RR Helper]", message)
        else:
            warnings.append(message)

    return restore_actions, warnings


def prepare_unity_export_maps(root):
    restore_actions = []
    warnings = []

    try:
        # Blender Mapping nodes are shader-graph state, not a portable FBX
        # material contract.  The shared exporter evaluates them
        # transactionally into UV0 without creating an RR helper layer, so
        # Unity receives the authored appearance and lightmap UV1 remains
        # available.
        uv_restores, uv_warnings = rr_unity_uv_export_contract.prepare_unity_uvs_for_export(
            root,
            get_export_asset_meshes(root),
            principled_base_image_node,
        )
        restore_actions.extend(uv_restores)
        warnings.extend(uv_warnings)

        material_restores, material_warnings = prepare_material_maps_for_unity(root)
        restore_actions.extend(material_restores)
        warnings.extend(material_warnings)
        return restore_actions, warnings
    except Exception:
        rr_unity_uv_export_contract.restore_actions_best_effort(
            restore_actions,
            f"{getattr(root, 'name', '<asset>')} map prepare",
        )
        raise


def image_source_path(image):
    if image is None:
        return ""

    filepath = getattr(image, "filepath", "")
    if not filepath:
        return ""

    return bpy.path.abspath(filepath)


def unique_texture_filename(used_names, material, map_name, image):
    source_path = image_source_path(image)
    source_name = os.path.basename(source_path) if source_path else image.name
    stem, ext = os.path.splitext(source_name)
    if not ext:
        ext = ".png"

    base_name = sanitize_id(f"{material.name}_{map_name}_{stem}") + ext.lower()
    candidate = base_name
    index = 2
    while candidate.lower() in used_names:
        candidate = sanitize_id(f"{material.name}_{map_name}_{stem}_{index}") + ext.lower()
        index += 1

    used_names.add(candidate.lower())
    return candidate


def copy_image_for_manifest(root_name, material, map_name, image, texture_dir, used_names, warnings):
    if image is None:
        return ""

    os.makedirs(texture_dir, exist_ok=True)
    filename = unique_texture_filename(used_names, material, map_name, image)
    destination = os.path.join(texture_dir, filename)
    source_path = image_source_path(image)

    if source_path and os.path.exists(source_path):
        shutil.copy2(source_path, destination)
    elif getattr(image, "packed_file", None) is not None:
        original_filepath = image.filepath_raw
        try:
            image.filepath_raw = destination
            image.save()
        finally:
            image.filepath_raw = original_filepath
    else:
        warnings.append(
            f"{root_name}: material '{material.name}' {map_name} map '{image.name}' has no readable file path."
        )
        return ""

    return "textures/" + filename


def build_material_map_manifest(root, asset_dir, surface_contracts=None):
    texture_dir = os.path.join(asset_dir, "textures")
    if os.path.isdir(texture_dir):
        shutil.rmtree(texture_dir)

    if surface_contracts is None:
        surface_contracts = build_material_surface_contracts(root)

    warnings = []
    material_maps = []
    used_names = set()
    seen_materials = set()

    for obj in get_export_asset_meshes(root):
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.name in seen_materials:
                continue

            seen_materials.add(material.name)
            base_node = find_image_node(material, ("basecolor", "base_color", "albedo", "diffuse", "diff"))
            rough_node = find_image_node(material, ("roughness", "rough"))
            normal_node = find_image_node(material, ("normal", "nor_gl", "nor", "nrm"))
            if normal_node is not None and image_node_matches(normal_node, ("height", "disp", "displacement", "bump")):
                warnings.append(
                    f"{root.name}: material '{material.name}' image '{normal_node.image.name}' looks like height/bump, not normal; skipped normal export."
                )
                normal_node = None

            entry = {"material": material.name}
            surface = surface_contracts.get(material.name)
            if surface is not None:
                entry["surface"] = surface

            if base_node is not None and base_node.image is not None:
                entry["baseColor"] = copy_image_for_manifest(
                    root.name, material, "BaseColor", base_node.image, texture_dir, used_names, warnings
                )

            if rough_node is not None and rough_node.image is not None:
                entry["roughness"] = copy_image_for_manifest(
                    root.name, material, "Roughness", rough_node.image, texture_dir, used_names, warnings
                )

            if normal_node is not None and normal_node.image is not None:
                entry["normal"] = copy_image_for_manifest(
                    root.name, material, "Normal", normal_node.image, texture_dir, used_names, warnings
                )

            entry = {key: value for key, value in entry.items() if value is not None and value != ""}
            if len(entry) > 1:
                material_maps.append(entry)

    return material_maps, warnings


def export_fbx(root, model_path):
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    selection_states = [(obj, obj.select_get()) for obj in view_layer.objects]
    export_objects = []
    linked_to_scene_root = []
    hide_states = []
    restore_actions = []
    warnings = []
    export_exception = None
    uv_cleanup_errors = []
    try:
        export_objects = [root]
        export_objects.extend(get_export_asset_meshes(root))
        export_objects.extend(get_collision_meshes(root))
        export_objects = [obj for obj in dict.fromkeys(export_objects) if obj is not None]

        root_collection_members = set(scene.collection.objects)
        for obj in export_objects:
            if obj not in root_collection_members:
                scene.collection.objects.link(obj)
                linked_to_scene_root.append(obj)

        hide_states = [
            (obj, obj.hide_get(), obj.hide_viewport, obj.hide_render, obj.hide_select)
            for obj in export_objects
        ]
        for obj in export_objects:
            obj.hide_set(False)
            obj.hide_viewport = False
            obj.hide_render = False
            obj.hide_select = False

        view_layer.update()
        set_active_export_root(root)
        restore_actions, warnings = prepare_unity_export_maps(root)
        bpy.ops.export_scene.fbx(
            filepath=model_path,
            use_selection=True,
            use_visible=False,
            use_active_collection=False,
            collection="",
            global_scale=1.0,
            object_types={"EMPTY", "MESH"},
            apply_unit_scale=True,
            apply_scale_options="FBX_SCALE_NONE",
            use_space_transform=True,
            bake_space_transform=True,
            axis_forward="-Z",
            axis_up="Y",
            use_mesh_modifiers=True,
            use_mesh_modifiers_render=True,
            mesh_smooth_type="OFF",
            colors_type="SRGB",
            prioritize_active_color=False,
            use_subsurf=False,
            use_mesh_edges=False,
            use_tspace=False,
            use_triangles=False,
            use_custom_props=False,
            add_leaf_bones=True,
            primary_bone_axis="Y",
            secondary_bone_axis="X",
            use_armature_deform_only=False,
            armature_nodetype="NULL",
            bake_anim=False,
            bake_anim_use_all_bones=True,
            bake_anim_use_nla_strips=True,
            bake_anim_use_all_actions=True,
            bake_anim_force_startend_keying=True,
            bake_anim_step=1.0,
            bake_anim_simplify_factor=1.0,
            path_mode="COPY",
            embed_textures=True,
            batch_mode="OFF",
            use_batch_own_dir=True,
        )
    except BaseException as exception:
        export_exception = exception
        raise
    finally:
        uv_cleanup_errors = rr_unity_uv_export_contract.restore_actions_best_effort(
            restore_actions,
            f"{getattr(root, 'name', '<asset>')} FBX export",
        )
        for obj, hidden, hide_viewport, hide_render, hide_select in hide_states:
            if obj.name in bpy.data.objects:
                try:
                    obj.hide_set(hidden)
                    obj.hide_viewport = hide_viewport
                    obj.hide_render = hide_render
                    obj.hide_select = hide_select
                except Exception as exception:
                    print(f"[RR Helper] Failed to restore visibility for '{obj.name}': {exception}")
        for obj in linked_to_scene_root:
            try:
                if obj.name in bpy.data.objects and obj.name in scene.collection.objects:
                    scene.collection.objects.unlink(obj)
            except Exception as exception:
                print(f"[RR Helper] Failed to restore collection link for '{obj.name}': {exception}")

        for obj in list(view_layer.objects):
            try:
                obj.select_set(False)
            except Exception:
                pass
        for obj, selected in selection_states:
            if selected and obj.name in bpy.data.objects:
                try:
                    obj.select_set(True)
                except Exception:
                    pass
        if previous_active is not None and previous_active.name in view_layer.objects:
            view_layer.objects.active = previous_active

        if uv_cleanup_errors:
            cleanup_message = (
                f"{getattr(root, 'name', '<asset>')}: FBX was written, but Blender UV/Mapping "
                f"state could not be fully restored ({len(uv_cleanup_errors)} cleanup error(s)). "
                "The export is rejected; inspect the source before saving."
            )
            if export_exception is not None and hasattr(export_exception, "add_note"):
                export_exception.add_note(cleanup_message)
            elif export_exception is None:
                raise rr_unity_uv_export_contract.UVExportContractError(
                    cleanup_message
                ) from uv_cleanup_errors[0]

    return warnings


def render_icon(root, icon_path, resolution, settings=None):
    scene = bpy.context.scene
    original_camera = scene.camera
    render = scene.render
    image_settings = render.image_settings
    original_render_state = {
        "filepath": render.filepath,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "film_transparent": render.film_transparent,
    }
    # Restore the format first: it controls which color modes/depths Blender accepts.
    original_image_state = {
        "file_format": image_settings.file_format,
        "color_mode": image_settings.color_mode,
        "color_depth": image_settings.color_depth,
    }
    hide_states = [(item, item.hide_render) for item in scene.objects]
    preview_light_states = capture_icon_preview_light_states(scene)
    temp_camera = None
    camera_data = None
    preview_lights = []
    render_exception = None

    try:
        asset_meshes = set(get_export_asset_meshes(root))
        camera_data = bpy.data.cameras.new("RR_IconCamera")
        temp_camera = bpy.data.objects.new("RR_IconCamera", camera_data)
        scene.collection.objects.link(temp_camera)
        state = configure_icon_camera(temp_camera, root, settings)
        scene.camera = temp_camera

        preview_lights = ensure_icon_preview_lights(scene, state, root, settings)
        save_icon_light_transforms_to_object(root, scene)
        visible_for_render = {temp_camera, *get_icon_render_lights(scene, preview_lights), *asset_meshes}
        for item in scene.objects:
            item.hide_render = item not in visible_for_render

        render.filepath = icon_path
        render.resolution_x = resolution
        render.resolution_y = resolution
        render.resolution_percentage = 100
        render.film_transparent = True
        image_settings.file_format = "PNG"
        image_settings.color_mode = "RGBA"
        image_settings.color_depth = "8"
        bpy.ops.render.render(write_still=True)
        remember_icon_outline_source(icon_path, overwrite=True)
        apply_icon_outline_to_png(icon_path, settings)
    except BaseException as exception:
        render_exception = exception
        raise
    finally:
        cleanup_errors = []

        def restore(action):
            try:
                action()
            except Exception as exception:
                cleanup_errors.append(exception)
                print(f"[RR Helper] Icon state cleanup failed: {exception}")

        for item, hide_render in hide_states:
            restore(lambda item=item, hidden=hide_render: setattr(item, "hide_render", hidden))
        restore(lambda: setattr(scene, "camera", original_camera))
        for key, value in original_render_state.items():
            restore(lambda key=key, value=value: setattr(render, key, value))
        for key, value in original_image_state.items():
            restore(lambda key=key, value=value: setattr(image_settings, key, value))
        if temp_camera is not None:
            restore(lambda: bpy.data.objects.remove(temp_camera, do_unlink=True))
        if camera_data is not None:
            restore(lambda: bpy.data.cameras.remove(camera_data) if camera_data.users == 0 else None)
        restore(lambda: restore_icon_preview_light_states(scene, preview_light_states))
        if cleanup_errors:
            message = (
                f"{getattr(root, 'name', '<asset>')}: icon render state could not be fully "
                f"restored ({len(cleanup_errors)} cleanup error(s)); export rejected."
            )
            if render_exception is not None and hasattr(render_exception, "add_note"):
                render_exception.add_note(message)
            elif render_exception is None:
                raise RuntimeError(message) from cleanup_errors[0]


def variant_membership_revision(member_identity_pairs):
    """Hash the authoritative current Variant membership in a cross-runtime format."""
    canonical_lines = []
    for member_id, stable_id in member_identity_pairs or []:
        raw_id = str(member_id or "").strip()
        raw_stable_id = str(stable_id or "").strip()
        if (
            not raw_id
            or not raw_stable_id
            or VARIANT_MEMBERSHIP_IDENTIFIER_PATTERN.fullmatch(raw_id) is None
            or VARIANT_MEMBERSHIP_IDENTIFIER_PATTERN.fullmatch(raw_stable_id) is None
        ):
            raise RuntimeError(
                "Variant membership requires non-empty ASCII [A-Za-z0-9_] member IDs and stable IDs."
            )
        normalized_id = raw_id.lower()
        normalized_stable_id = raw_stable_id.lower()
        canonical_lines.append(f"{normalized_id}\t{normalized_stable_id}")

    canonical_payload = "\n".join(sorted(canonical_lines))
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def build_group_manifest(root, icon_source_root=None):
    validate_export_identity(root)
    asset_root = object_manager_assembly_root_for_object(root) or root
    group_root = object_manager_variant_group_root(asset_root) or asset_root
    if group_root is None or not is_object_manager_assembly_root(group_root):
        return None

    group_type = object_manager_assembly_type(group_root)
    if group_type == "VARIANTS":
        members = object_manager_variant_member_roots(group_root)
    else:
        members = [
            member
            for member in object_manager_member_objects(group_root)
            if member is not None and get_asset_meshes(member)
        ]
    member_ids = [export_asset_id(member) for member in members]
    group_id = group_root.get(OBJECT_MANAGER_ASSEMBLY_ID_PROP) or group_root.name
    manifest = {
        "id": group_id,
        "name": object_manager_display_name(group_root),
        "type": group_type.lower(),
        "rootObject": group_root.name,
        "members": member_ids,
    }

    if group_type == "VARIANTS":
        member_identity_pairs = []
        for member in members:
            validate_export_identity(member, members)
            stable_id, _previous_ids = ensure_export_identity(member, export_asset_id(member))
            member_identity_pairs.append((export_asset_id(member), stable_id))

        icon_source = icon_source_root or object_manager_variant_icon_source_root(group_root)
        if icon_source not in members:
            icon_source = members[0] if members else None
        try:
            variant_index = members.index(root)
        except ValueError:
            variant_index = 0
        manifest.update(
            {
                "role": "variant",
                "variantGroupId": group_id,
                "variantGroupName": object_manager_display_name(group_root),
                "variantIndex": variant_index,
                "variantCount": len(member_ids),
                "membershipContractVersion": VARIANT_MEMBERSHIP_CONTRACT_VERSION,
                "membershipRevision": variant_membership_revision(member_identity_pairs),
                "membershipComplete": True,
                "iconSourceMemberId": export_asset_id(icon_source) if icon_source is not None else "",
                "iconSourceStableId": str(icon_source.get(EXPORT_STABLE_ID_PROP, "") or "") if icon_source is not None else "",
            }
        )
    elif root == group_root:
        manifest["role"] = "root"
    else:
        manifest["role"] = "member"

    return manifest


def write_manifest(
    root,
    manifest_path,
    asset_id,
    asset_type,
    asset_category,
    profile_name,
    model_file,
    icon_file,
    exported_resources,
    warnings=None,
    material_maps=None,
    group_manifest=None,
    uv_export_contract=None,
    bounds_override=None,
):
    validate_export_identity(root)
    if isinstance(bounds_override, dict):
        bounds = dict(bounds_override)
    else:
        center, size = mesh_world_bounds(root)
        bounds = {
            "center": [round(center.x, 5), round(center.y, 5), round(center.z, 5)],
            "size": [round(size.x, 5), round(size.y, 5), round(size.z, 5)],
        }
    stable_id, previous_ids = ensure_export_identity(root, asset_id)
    manifest = {
        "schemaVersion": 1,
        "profile": profile_name or "Default",
        "id": asset_id,
        "stableId": stable_id,
        "previousIds": previous_ids,
        "displayName": asset_id.replace("_", " "),
        "type": asset_type,
        "category": asset_category or "",
        "sourceBlend": bpy.data.filepath,
        "sourceObject": root.name,
        "exportedAtUtc": datetime.now(timezone.utc).isoformat(),
        "modelFile": model_file or "",
        "iconFile": icon_file or "",
        "exportedResources": exported_resources or [],
        "bounds": bounds,
        "warnings": warnings or [],
        "materialMaps": material_maps or [],
    }
    if isinstance(uv_export_contract, dict):
        manifest["uvExport"] = dict(uv_export_contract)
    if group_manifest:
        manifest["group"] = group_manifest
        if group_manifest.get("type") == "variants":
            manifest["variantGroupId"] = group_manifest.get("variantGroupId", "")
            manifest["variantGroupName"] = group_manifest.get("variantGroupName", "")
            manifest["variantIndex"] = group_manifest.get("variantIndex", 0)
            manifest["variantCount"] = group_manifest.get("variantCount", 0)
            manifest["variantMembers"] = group_manifest.get("members", [])

    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
    temporary_path = f"{manifest_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")
        os.replace(temporary_path, manifest_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def read_existing_manifest(manifest_path):
    if not manifest_path or not os.path.exists(manifest_path):
        return {}

    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    return manifest if isinstance(manifest, dict) else {}


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def current_uv_export_contract(model_path):
    if not model_path or not os.path.isfile(model_path):
        raise RuntimeError(f"Cannot create a UV export contract without the exported FBX: {model_path}")
    return {
        "contractVersion": UNITY_UV_EXPORT_CONTRACT_VERSION,
        "mappingPolicy": rr_unity_uv_export_contract.MAPPING_POLICY,
        "temporaryUvLayersExported": False,
        "modelSha256": file_sha256(model_path),
    }


def existing_uv_export_contract(manifest, model_path):
    if not isinstance(manifest, dict) or not model_path or not os.path.isfile(model_path):
        return None
    contract = manifest.get("uvExport")
    return dict(contract) if isinstance(contract, dict) else None


def uv_export_contract_matches_model(manifest, model_path):
    contract = existing_uv_export_contract(manifest, model_path)
    if contract is None:
        return False
    try:
        version = int(contract.get("contractVersion", 0))
    except (TypeError, ValueError):
        return False
    if version < UNITY_UV_EXPORT_CONTRACT_VERSION:
        return False
    if contract.get("mappingPolicy") != rr_unity_uv_export_contract.MAPPING_POLICY:
        return False
    if bool(contract.get("temporaryUvLayersExported", False)):
        return False
    expected_hash = str(contract.get("modelSha256", "") or "").strip().lower()
    if len(expected_hash) != 64:
        return False
    try:
        return expected_hash == file_sha256(model_path).lower()
    except OSError:
        return False


def requested_export_resources(export_model, include_icon):
    resources = []
    if export_model:
        resources.append("model")
    if include_icon:
        resources.append("icon")
    return resources


def queue_unity_builder_import(
    manifest_paths,
    request_path=UNITY_BUILDER_IMPORT_REQUEST,
    bridge_root=UNITY_TEMP_OUTPUT_ROOT,
):
    """Atomically merge completed Builder packages into Unity's import queue."""
    if not request_path or not bridge_root:
        return []

    normalized_bridge_root = os.path.normcase(os.path.abspath(bridge_root))
    queued = []
    for manifest_path in manifest_paths or []:
        if not manifest_path:
            continue

        normalized_manifest = os.path.abspath(manifest_path)
        package_root = os.path.dirname(normalized_manifest)
        try:
            inside_bridge = (
                os.path.commonpath(
                    [normalized_bridge_root, os.path.normcase(package_root)]
                )
                == normalized_bridge_root
            )
        except ValueError:
            inside_bridge = False

        package_name = os.path.basename(package_root)
        if (
            not inside_bridge
            or os.path.normcase(package_root) == normalized_bridge_root
            or package_name.startswith("_")
            or os.path.basename(normalized_manifest).lower() != "manifest.json"
            or not os.path.isfile(normalized_manifest)
        ):
            continue

        queued.append(os.path.normpath(normalized_manifest))

    queued = sorted(set(queued), key=str.casefold)
    if not queued:
        return []

    existing = []
    if os.path.isfile(request_path):
        try:
            with open(request_path, "r", encoding="utf-8") as handle:
                existing = [line.strip() for line in handle if line.strip()]
        except OSError:
            existing = []

    merged = sorted(set(existing + queued), key=str.casefold)
    request_folder = os.path.dirname(os.path.abspath(request_path))
    os.makedirs(request_folder, exist_ok=True)
    temporary_path = f"{request_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(merged))
            handle.write("\n")
        os.replace(temporary_path, request_path)
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)

    return queued


def normalized_material_surface_contracts(material_maps):
    normalized = {}
    if not isinstance(material_maps, list):
        return normalized

    for entry in material_maps:
        if not isinstance(entry, dict):
            continue
        material_name = str(entry.get("material", "") or "").strip().lower()
        surface = entry.get("surface")
        if material_name and isinstance(surface, dict):
            normalized[material_name] = surface
    return normalized


def material_surface_contracts_match_manifest(manifest, expected_surface_contracts):
    if expected_surface_contracts is None:
        return True

    expected = {
        str(name or "").strip().lower(): contract
        for name, contract in expected_surface_contracts.items()
        if name and isinstance(contract, dict)
    }
    existing = normalized_material_surface_contracts(manifest.get("materialMaps", []))
    return existing == expected


def output_has_requested_resources(
    asset_dir,
    export_model,
    include_icon,
    group_manifest=None,
    expected_surface_contracts=None,
    require_exported_resource_declaration=True,
):
    manifest_path = os.path.join(asset_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return False

    if export_model and not os.path.exists(os.path.join(asset_dir, "model.fbx")):
        return False
    if include_icon and not os.path.exists(os.path.join(asset_dir, "icon.png")):
        return False

    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False

    if export_model and not uv_export_contract_matches_model(
        manifest,
        os.path.join(asset_dir, manifest.get("modelFile") or "model.fbx"),
    ):
        return False
    if export_model and not material_surface_contracts_match_manifest(
        manifest,
        expected_surface_contracts,
    ):
        return False

    if group_manifest:
        existing_group = manifest.get("group") if isinstance(manifest.get("group"), dict) else {}
        if existing_group.get("id") != group_manifest.get("id"):
            return False
        if existing_group.get("type") != group_manifest.get("type"):
            return False
        if group_manifest.get("type") == "variants":
            if manifest.get("variantGroupId") != group_manifest.get("variantGroupId"):
                return False
            if manifest.get("variantCount") != group_manifest.get("variantCount"):
                return False

    exported_resources = manifest.get("exportedResources") or []
    if not exported_resources or not require_exported_resource_declaration:
        return True

    exported = {str(resource).lower() for resource in exported_resources}
    return all(resource in exported for resource in requested_export_resources(export_model, include_icon))


def asset_icon_path(settings, root):
    return os.path.join(settings.output_root, export_asset_id(root), "icon.png")


def shared_icon_requires_refresh(root, settings, shared_icon_root):
    if root is None or shared_icon_root is None or shared_icon_root == root:
        return False

    icon_path = asset_icon_path(settings, root)
    shared_icon_path = asset_icon_path(settings, shared_icon_root)
    if not os.path.exists(icon_path) or not os.path.exists(shared_icon_path):
        return True

    try:
        return not filecmp.cmp(icon_path, shared_icon_path, shallow=False)
    except OSError:
        return True


def render_or_copy_shared_icon(
    root,
    settings,
    icon_path,
    shared_icon_root=None,
    shared_icon_path=None,
    force_render=False,
    icon_render_cache=None,
):
    render_root = shared_icon_root or shared_builder_icon_root(root)
    if render_root is None:
        render_root = root

    if shared_icon_path is None and render_root is not root:
        shared_icon_path = asset_icon_path(settings, render_root)

    cache_key = None
    reuse_render = False
    if icon_render_cache is not None:
        source_icon_path = icon_path if render_root == root else shared_icon_path
        cache_key = (render_root, normalized_path(source_icon_path))
        reuse_render = (
            icon_render_cache.get(cache_key, False)
            and os.path.isfile(source_icon_path)
            and os.path.isfile(icon_outline_source_path(source_icon_path))
        )
        if not reuse_render:
            icon_render_cache.pop(cache_key, None)
        # Each batch refreshes its shared source once, even when old files exist.
        force_render = not reuse_render

    resolution = clamp_icon_size(settings.icon_resolution)
    if render_root == root:
        if not reuse_render:
            render_icon(root, icon_path, resolution, settings)
            save_icon_framing_to_object(root, settings)
        if cache_key is not None:
            icon_render_cache[cache_key] = True
        return icon_path

    os.makedirs(os.path.dirname(shared_icon_path), exist_ok=True)
    if force_render or not os.path.exists(shared_icon_path) or not os.path.exists(icon_outline_source_path(shared_icon_path)):
        render_icon(render_root, shared_icon_path, resolution, settings)
        save_icon_framing_to_object(render_root, settings)

    os.makedirs(os.path.dirname(icon_path), exist_ok=True)
    if os.path.abspath(shared_icon_path) != os.path.abspath(icon_path):
        shutil.copyfile(shared_icon_path, icon_path)
        copy_icon_outline_source(shared_icon_path, icon_path)
    if cache_key is not None:
        icon_render_cache[cache_key] = True
    return icon_path


def export_builder_asset(
    obj,
    settings,
    export_model=None,
    include_icon=None,
    shared_icon_root=None,
    queue_import=True,
    icon_render_cache=None,
):
    if obj is None:
        raise RuntimeError("Select a mesh object or an asset root object to export.")

    if not mesh_objects_have_export_geometry(get_export_asset_meshes(obj)):
        raise RuntimeError(f"{obj.name} has no exportable mesh geometry.")

    validate_export_identity(obj)
    asset_id = export_asset_id(obj)
    asset_type = infer_export_asset_type(obj, asset_id)
    asset_dir = os.path.join(settings.output_root, asset_id)
    os.makedirs(asset_dir, exist_ok=True)

    model_path = os.path.join(asset_dir, "model.fbx")
    icon_path = os.path.join(asset_dir, "icon.png")
    manifest_path = os.path.join(asset_dir, "manifest.json")
    existing_manifest = read_existing_manifest(manifest_path)
    if export_model is None:
        export_model = getattr(settings, "include_model_with_export", True)
    if include_icon is None:
        include_icon = getattr(settings, "include_icon_with_export", True)
    if not export_model and not include_icon:
        raise RuntimeError("Enable Model, Icon, or both before exporting.")
    if include_icon and shared_icon_root is None:
        shared_icon_root = shared_builder_icon_root(obj)
    group_manifest = build_group_manifest(obj)
    surface_contracts = build_material_surface_contracts(obj) if export_model else None

    skip_existing_models = getattr(settings, "skip_existing_exports", True)
    can_reuse_existing_model = (
        skip_existing_models
        and export_model
        and output_has_requested_resources(
            asset_dir,
            export_model=True,
            include_icon=False,
            group_manifest=group_manifest,
            expected_surface_contracts=surface_contracts,
            require_exported_resource_declaration=False,
        )
    )
    if can_reuse_existing_model and not include_icon:
        # A preceding icon refresh intentionally leaves an icon-only manifest.
        # Normalize the queued request back to model-only so Unity imports the
        # verified FBX requested by this export instead of replaying the icon.
        existing_warnings = existing_manifest.get("warnings", [])
        warnings = list(existing_warnings) if isinstance(existing_warnings, list) else []
        existing_material_maps = existing_manifest.get("materialMaps", [])
        material_maps = list(existing_material_maps) if isinstance(existing_material_maps, list) else []
        write_manifest(
            obj,
            manifest_path,
            asset_id,
            asset_type,
            infer_asset_category(obj, asset_type),
            settings.profile_name,
            "model.fbx",
            "icon.png" if os.path.exists(icon_path) else "",
            ["model"],
            warnings,
            material_maps,
            group_manifest,
            existing_uv_export_contract(existing_manifest, model_path),
            existing_manifest.get("bounds"),
        )
        if queue_import:
            queue_unity_builder_import([manifest_path])
        return asset_id, asset_type, "skipped"

    # Skip Existing is intentionally model-only. Icon framing, lighting, outline,
    # and the render itself are not represented by the old manifest, so an
    # icon request must always produce fresh pixels. Reuse the verified model
    # and emit an icon-only manifest so Unity does not rebuild model prefabs.
    if can_reuse_existing_model and include_icon:
        export_model = False

    warnings = []
    material_maps = []
    exported_resources = []
    uv_export_contract = None

    if not export_model:
        existing_warnings = existing_manifest.get("warnings", [])
        warnings = list(existing_warnings) if isinstance(existing_warnings, list) else []
        existing_material_maps = existing_manifest.get("materialMaps", [])
        material_maps = list(existing_material_maps) if isinstance(existing_material_maps, list) else []

    if export_model:
        warnings = export_fbx(obj, model_path)
        material_maps, texture_warnings = build_material_map_manifest(
            obj,
            asset_dir,
            surface_contracts,
        )
        warnings.extend(texture_warnings)
        exported_resources.append("model")
        uv_export_contract = current_uv_export_contract(model_path)

    for warning in warnings:
        print("[RandomRealm Builder Exporter]", warning)

    if include_icon:
        icon_variant_group = object_manager_variant_group_root(obj)
        if icon_variant_group is not None and shared_icon_root is not None:
            load_icon_framing_from_object(shared_icon_root, settings)
        render_or_copy_shared_icon(
            obj,
            settings,
            icon_path,
            shared_icon_root=shared_icon_root,
            force_render=True,
            # Ordinary InnerWall pairs can have different per-item framing.
            icon_render_cache=icon_render_cache if icon_variant_group is not None else None,
        )
        exported_resources.append("icon")

    model_file = "model.fbx" if os.path.exists(model_path) else ""
    if not model_file:
        existing_model_file = existing_manifest.get("modelFile", "")
        existing_model_path = (
            os.path.join(asset_dir, existing_model_file)
            if isinstance(existing_model_file, str) and existing_model_file
            else ""
        )
        if existing_model_path and os.path.isfile(existing_model_path):
            model_file = existing_model_file
    icon_file = "icon.png" if os.path.exists(icon_path) else ""
    if not export_model and model_file:
        uv_export_contract = existing_uv_export_contract(
            existing_manifest,
            os.path.join(asset_dir, model_file),
        )
    write_manifest(
        obj,
        manifest_path,
        asset_id,
        asset_type,
        infer_asset_category(obj, asset_type),
        settings.profile_name,
        model_file,
        icon_file,
        exported_resources,
        warnings,
        material_maps,
        group_manifest,
        uv_export_contract,
        existing_manifest.get("bounds") if not export_model else None,
    )
    if queue_import:
        queue_unity_builder_import([manifest_path])

    return asset_id, asset_type, "exported"


class ExportSettingsOutputRootProxy:
    __slots__ = ("_source", "output_root")

    def __init__(self, source, output_root):
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "output_root", output_root)

    def __getattr__(self, name):
        return getattr(self._source, name)

    def __setattr__(self, name, value):
        if name in {"_source", "output_root"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._source, name, value)


def validate_export_batch_identities(export_roots):
    roots = [root for root in export_roots or [] if root is not None]
    for root in roots:
        validate_export_identity(root, roots)
    return roots


def variant_export_transaction_parent(output_root):
    output_root = os.path.abspath(output_root)
    unity_assets_root = os.path.abspath(os.path.join(UNITY_PROJECT_ROOT, "Assets"))
    try:
        is_unity_assets_output = (
            os.path.commonpath(
                [os.path.normcase(output_root), os.path.normcase(unity_assets_root)]
            )
            == os.path.normcase(unity_assets_root)
        )
    except ValueError:
        is_unity_assets_output = False

    if is_unity_assets_output:
        return os.path.join(UNITY_BUILDER_CACHE_ROOT, "ExportTransactions")
    return os.path.join(os.path.dirname(output_root), ".rr_builder_export_transactions")


def variant_publish_group_token(group_id):
    raw_group_id = str(group_id or "").strip()
    if VARIANT_MEMBERSHIP_IDENTIFIER_PATTERN.fullmatch(raw_group_id) is None:
        raise RuntimeError(
            "Variant publish leases require a non-empty ASCII [A-Za-z0-9_] group ID."
        )
    return hashlib.sha256(raw_group_id.lower().encode("utf-8")).hexdigest()


def variant_publish_claim_root():
    return os.path.join(UNITY_BUILDER_CACHE_ROOT, "ExportTransactions", "Claims")


def variant_publish_claim_path(_output_root, group_id):
    group_token = variant_publish_group_token(group_id)
    return os.path.join(
        variant_publish_claim_root(),
        f"{group_token}.claim.json",
    )


def variant_publish_gate_path(_output_root, group_id):
    return os.path.join(
        variant_publish_claim_root(),
        f"{variant_publish_group_token(group_id)}.gate.lock",
    )


def variant_publish_reader_directory(_output_root, group_id):
    return os.path.join(
        variant_publish_claim_root(),
        f"{variant_publish_group_token(group_id)}.readers",
    )


def variant_publish_reader_lease_path(_output_root, group_id, reader_lease_id):
    raw_reader_lease_id = str(reader_lease_id or "").strip()
    if VARIANT_MEMBERSHIP_IDENTIFIER_PATTERN.fullmatch(raw_reader_lease_id) is None:
        raise RuntimeError(
            "Variant reader leases require a non-empty ASCII [A-Za-z0-9_] lease ID."
        )
    return os.path.join(
        variant_publish_reader_directory(_output_root, group_id),
        f"{raw_reader_lease_id.lower()}.reader.json",
    )


def write_exclusive_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = None
    created = False
    try:
        descriptor = os.open(path, flags)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if created and os.path.exists(path):
            os.remove(path)
        raise
    return path


def acquire_variant_publish_gate(output_root, group_id):
    gate_path = variant_publish_gate_path(output_root, group_id)
    gate_lease_id = uuid.uuid4().hex
    write_exclusive_json(
        gate_path,
        {
            "gateContractVersion": VARIANT_PUBLISH_GATE_CONTRACT_VERSION,
            "role": "writer",
            "groupId": group_id,
            "gateLeaseId": gate_lease_id,
            "processId": os.getpid(),
        },
    )
    return gate_path, gate_lease_id


def release_variant_publish_gate(gate_path, gate_lease_id):
    gate = read_existing_manifest(gate_path)
    if not gate or gate.get("gateLeaseId") != gate_lease_id:
        raise RuntimeError("Variant publish gate ownership changed; the gate was not released.")
    os.remove(gate_path)


def existing_variant_reader_leases(output_root, group_id):
    reader_directory = variant_publish_reader_directory(output_root, group_id)
    if not os.path.isdir(reader_directory):
        return []
    return sorted(
        [
            os.path.join(reader_directory, name)
            for name in os.listdir(reader_directory)
        ],
        key=str.casefold,
    )


def create_variant_publish_claim(output_root, group_id, revision, member_ids):
    gate_path, gate_lease_id = acquire_variant_publish_gate(output_root, group_id)
    try:
        claim_path = variant_publish_claim_path(output_root, group_id)
        if os.path.exists(claim_path):
            raise FileExistsError(f"Variant writer claim already exists: {claim_path}")
        reader_leases = existing_variant_reader_leases(output_root, group_id)
        if reader_leases:
            raise RuntimeError(
                "Variant publish is blocked by active or stale reader lease(s): "
                + ", ".join(reader_leases)
            )
        writer_lease_id = uuid.uuid4().hex
        write_exclusive_json(
            claim_path,
            {
                "claimContractVersion": VARIANT_PUBLISH_CLAIM_CONTRACT_VERSION,
                "state": "incomplete",
                "groupId": group_id,
                "membershipRevision": revision,
                "members": list(member_ids or []),
                "writerLeaseId": writer_lease_id,
                "processId": os.getpid(),
            },
        )
        return claim_path, writer_lease_id
    finally:
        release_variant_publish_gate(gate_path, gate_lease_id)


def remove_variant_publish_claim(output_root, group_id, claim_path, writer_lease_id):
    gate_path, gate_lease_id = acquire_variant_publish_gate(output_root, group_id)
    try:
        reader_leases = existing_variant_reader_leases(output_root, group_id)
        if reader_leases:
            raise RuntimeError(
                "Variant writer claim cannot be released while reader lease(s) exist: "
                + ", ".join(reader_leases)
            )
        claim = read_existing_manifest(claim_path)
        if (
            not claim
            or claim.get("groupId") != group_id
            or claim.get("writerLeaseId") != writer_lease_id
        ):
            raise RuntimeError(
                "Variant writer claim ownership changed; another lease was not removed."
            )
        os.remove(claim_path)
    finally:
        release_variant_publish_gate(gate_path, gate_lease_id)


def prepare_variant_export_transactions(export_roots, settings):
    roots = validate_export_batch_identities(export_roots)
    attempted_names = {root.name for root in roots}
    transactions = []
    transactions_by_member = {}
    failures = []
    visited_groups = set()

    for root in roots:
        group_root = object_manager_variant_group_root(root)
        if group_root is None or group_root.name in visited_groups:
            continue
        visited_groups.add(group_root.name)

        members = object_manager_variant_member_roots(group_root)
        expected_names = {member.name for member in members}
        transaction = {
            "group_root": group_root,
            "members": members,
            "expected_names": expected_names,
            "staging_root": "",
            "settings": None,
            "output_root": settings.output_root,
        }
        transactions.append(transaction)
        for member in members:
            transactions_by_member[member.name] = transaction

        missing_names = sorted(expected_names - attempted_names, key=str.casefold)
        if not members or missing_names:
            missing_label = ", ".join(missing_names) if missing_names else "all members"
            failures.append(
                f"{group_root.name}: Variant transaction is missing {missing_label}; no output was published"
            )
            continue

        try:
            transaction_parent = variant_export_transaction_parent(settings.output_root)
            os.makedirs(transaction_parent, exist_ok=True)
            staging_root = tempfile.mkdtemp(
                prefix=f"{sanitize_id(group_root.name)}_",
                dir=transaction_parent,
            )
            transaction["staging_root"] = staging_root
            transaction["settings"] = ExportSettingsOutputRootProxy(settings, staging_root)
            for member in members:
                member_id = export_asset_id(member)
                source_folder = os.path.join(settings.output_root, member_id)
                staged_folder = os.path.join(staging_root, member_id)
                if os.path.isdir(source_folder):
                    shutil.copytree(source_folder, staged_folder, copy_function=shutil.copy2)
        except Exception as exc:
            staging_root = transaction.get("staging_root", "")
            if staging_root:
                shutil.rmtree(staging_root, ignore_errors=True)
            transaction["staging_root"] = ""
            transaction["settings"] = None
            failures.append(f"{group_root.name}: could not create Variant staging transaction: {exc}")

    return transactions, transactions_by_member, failures


def verify_variant_membership_package(package_root, members, expected_revision=None):
    members = list(members or [])
    if not members:
        raise RuntimeError("Variant membership package has no current members.")

    expected_ids = [export_asset_id(member) for member in members]
    expected_group_id = None
    revision = None
    identity_pairs = []
    manifest_paths = []
    for member, member_id in zip(members, expected_ids):
        manifest_path = os.path.join(package_root, member_id, "manifest.json")
        manifest = read_existing_manifest(manifest_path)
        if not manifest:
            raise RuntimeError(f"Variant member '{member_id}' has no readable staged manifest.")
        if manifest.get("id") != member_id:
            raise RuntimeError(f"Variant member '{member_id}' staged manifest ID does not match.")

        stable_id = str(manifest.get("stableId", "") or "").strip()
        group = manifest.get("group") if isinstance(manifest.get("group"), dict) else {}
        group_id = str(group.get("id", "") or "").strip()
        group_revision = str(group.get("membershipRevision", "") or "").strip().lower()
        if group.get("type") != "variants":
            raise RuntimeError(f"Variant member '{member_id}' is not certified as a Variant.")
        if group.get("membershipContractVersion") != VARIANT_MEMBERSHIP_CONTRACT_VERSION:
            raise RuntimeError(f"Variant member '{member_id}' has an unsupported membership contract.")
        if group.get("membershipComplete") is not True:
            raise RuntimeError(f"Variant member '{member_id}' does not declare complete membership.")
        if group.get("members") != expected_ids or group.get("variantCount") != len(expected_ids):
            raise RuntimeError(f"Variant member '{member_id}' declares a different current member set.")
        if not group_id or not group_revision:
            raise RuntimeError(f"Variant member '{member_id}' has an incomplete membership certificate.")
        if expected_group_id is None:
            expected_group_id = group_id
            revision = group_revision
        elif group_id != expected_group_id or group_revision != revision:
            raise RuntimeError("Variant staged manifests do not share one group ID and revision.")

        identity_pairs.append((member_id, stable_id))
        manifest_paths.append(os.path.normpath(manifest_path))

    recomputed_revision = variant_membership_revision(identity_pairs)
    if recomputed_revision != revision:
        raise RuntimeError("Variant membership revision does not match its current member identities.")
    if expected_revision is not None and revision != expected_revision:
        raise RuntimeError("Published Variant membership revision changed during commit.")
    return sorted(manifest_paths, key=str.casefold), revision, expected_group_id


def remove_export_path(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def publish_staged_variant_group(
    staging_root,
    output_root,
    members,
    queue_callback=None,
    after_member_published=None,
):
    members = list(members or [])
    _staged_manifest_paths, revision, group_id = verify_variant_membership_package(
        staging_root,
        members,
    )
    transaction_parent = os.path.dirname(os.path.abspath(staging_root))
    member_ids = [export_asset_id(member) for member in members]
    try:
        claim_path, writer_lease_id = create_variant_publish_claim(
            output_root,
            group_id,
            revision,
            member_ids,
        )
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    backup_root = ""
    backups = {}
    published_targets = []
    formal_committed = False
    try:
        backup_root = tempfile.mkdtemp(prefix="variant_publish_backup_", dir=transaction_parent)
        os.makedirs(output_root, exist_ok=True)
        for index, member in enumerate(members):
            member_id = export_asset_id(member)
            staged_folder = os.path.join(staging_root, member_id)
            target_folder = os.path.join(output_root, member_id)
            backup_folder = os.path.join(backup_root, member_id)
            if not os.path.isdir(staged_folder):
                raise RuntimeError(f"Variant member '{member_id}' staging folder is missing.")
            if os.path.exists(target_folder):
                os.replace(target_folder, backup_folder)
                backups[target_folder] = backup_folder
            else:
                backups[target_folder] = ""
            os.replace(staged_folder, target_folder)
            published_targets.append(target_folder)
            if after_member_published is not None:
                after_member_published(index, member_id, target_folder)

        manifest_paths, published_revision, published_group_id = verify_variant_membership_package(
            output_root,
            members,
            expected_revision=revision,
        )
        if published_revision != revision or published_group_id != group_id:
            raise RuntimeError("Published Variant revision does not match staged revision.")
        remove_variant_publish_claim(
            output_root,
            group_id,
            claim_path,
            writer_lease_id,
        )
        formal_committed = True
        callback = queue_callback or queue_unity_builder_import
        callback(manifest_paths)
        return manifest_paths, revision
    finally:
        if not formal_committed:
            for target_folder in reversed(published_targets):
                if os.path.exists(target_folder):
                    remove_export_path(target_folder)
            for target_folder, backup_folder in reversed(list(backups.items())):
                if backup_folder and os.path.exists(backup_folder):
                    if os.path.exists(target_folder):
                        remove_export_path(target_folder)
                    os.replace(backup_folder, target_folder)
            remove_variant_publish_claim(
                output_root,
                group_id,
                claim_path,
                writer_lease_id,
            )
        if backup_root:
            shutil.rmtree(backup_root, ignore_errors=True)
        shutil.rmtree(staging_root, ignore_errors=True)


def finalize_variant_export_transactions(transactions, successful_root_names):
    successful_names = set(successful_root_names or [])
    published_names = set()
    failures = []
    for transaction in transactions or []:
        staging_root = transaction.get("staging_root", "")
        expected_names = transaction.get("expected_names", set())
        if not staging_root:
            continue
        if not expected_names.issubset(successful_names):
            shutil.rmtree(staging_root, ignore_errors=True)
            continue
        try:
            publish_staged_variant_group(
                staging_root,
                transaction["output_root"],
                transaction["members"],
            )
            published_names.update(expected_names)
        except Exception as exc:
            failures.append(
                f"{transaction['group_root'].name}: Variant publish transaction failed: {exc}"
            )
    return published_names, failures


def completed_ordinary_export_publication(export_roots, successful_root_names, output_root):
    successful_names = set(successful_root_names or [])
    manifest_paths = []
    published_names = set()
    for root in export_roots or []:
        if object_manager_variant_group_root(root) is not None or root.name not in successful_names:
            continue
        manifest_path = os.path.join(output_root, export_asset_id(root), "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        manifest_paths.append(os.path.normpath(manifest_path))
        published_names.add(root.name)
    return sorted(set(manifest_paths), key=str.casefold), published_names


class RRBuilderExportQueueItem(bpy.types.PropertyGroup):
    object_name: bpy.props.StringProperty(name="Object")
    icon_preview_root_name: bpy.props.StringProperty(
        name="Icon Preview Root",
        description="Renderable member used to frame a grouped queue item",
        options={"HIDDEN"},
    )
    framing_initialized: bpy.props.BoolProperty(name="Framing Initialized", default=False)
    icon_zoom: bpy.props.FloatProperty(
        name="Icon Zoom",
        default=1.0,
        min=ICON_ZOOM_MIN,
        max=ICON_ZOOM_MAX,
        precision=2,
    )
    icon_offset_x: bpy.props.FloatProperty(name="Icon X", default=0.0, precision=3)
    icon_offset_y: bpy.props.FloatProperty(name="Icon Y", default=0.0, precision=3)
    icon_view_yaw: bpy.props.FloatProperty(name="Yaw", default=0.0, precision=1)
    icon_view_pitch: bpy.props.FloatProperty(
        name="Pitch",
        default=0.0,
        min=ICON_PITCH_MIN,
        max=ICON_PITCH_MAX,
        precision=1,
    )
    icon_light_brightness: bpy.props.FloatProperty(
        name="Brightness",
        default=ICON_LIGHT_BRIGHTNESS_DEFAULT,
        min=ICON_LIGHT_BRIGHTNESS_MIN,
        soft_min=ICON_LIGHT_BRIGHTNESS_MIN,
        soft_max=ICON_LIGHT_BRIGHTNESS_SOFT_MAX,
        precision=2,
    )
    icon_key_light_ratio: bpy.props.FloatProperty(
        name="Key",
        default=ICON_KEY_LIGHT_RATIO_DEFAULT,
        min=ICON_LIGHT_RATIO_MIN,
        max=ICON_LIGHT_RATIO_MAX,
        precision=2,
    )
    icon_fill_light_ratio: bpy.props.FloatProperty(
        name="Fill",
        default=ICON_FILL_LIGHT_RATIO_DEFAULT,
        min=ICON_LIGHT_RATIO_MIN,
        max=ICON_LIGHT_RATIO_MAX,
        precision=2,
    )
    icon_back_light_ratio: bpy.props.FloatProperty(
        name="Back",
        default=ICON_BACK_LIGHT_RATIO_DEFAULT,
        min=ICON_LIGHT_RATIO_MIN,
        max=ICON_LIGHT_RATIO_MAX,
        precision=2,
    )
    icon_outline_enabled: bpy.props.BoolProperty(name="Outline", default=True)
    icon_outline_color: bpy.props.FloatVectorProperty(
        name="Outline Color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    icon_outline_pixels: bpy.props.IntProperty(
        name="Outline Pixels",
        default=2,
        min=1,
        max=8,
    )
    preview_path: bpy.props.StringProperty(name="Preview", subtype="FILE_PATH")


class RRBuilderReferenceItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name")
    filepath: bpy.props.StringProperty(name="Image", subtype="FILE_PATH")
    source: bpy.props.StringProperty(name="Source", default="Manual")
    asset_id: bpy.props.StringProperty(name="Asset ID", default="")


class RRBuilderExportSettings(bpy.types.PropertyGroup):
    export_queue: bpy.props.CollectionProperty(type=RRBuilderExportQueueItem)
    queue_active_index: bpy.props.IntProperty(
        name="Queue Item",
        default=0,
        update=on_queue_active_index_update,
    )
    references: bpy.props.CollectionProperty(type=RRBuilderReferenceItem)
    reference_active_index: bpy.props.IntProperty(name="Reference", default=0)
    show_references: bpy.props.BoolProperty(name="Show References", default=False)
    use_unity_reference_icons: bpy.props.BoolProperty(
        name="Unity Icons",
        description="Collect Unity builder icon.png files into References",
        default=False,
        update=on_use_unity_reference_icons_update,
    )
    preview_image: bpy.props.PointerProperty(type=bpy.types.Image)
    preview_image_path: bpy.props.StringProperty(name="Preview Image", subtype="FILE_PATH")
    output_root: bpy.props.StringProperty(
        name="Output Root",
        subtype="DIR_PATH",
        default=DEFAULT_OUTPUT_ROOT,
    )
    ui_page: bpy.props.EnumProperty(
        name="Page",
        description="Active RandomRealm panel page",
        items=(
            ("EXPORTER", "Export", "Export queue, output, and export actions"),
            ("BAKE", "Bake", "Bake selected procedural materials to PBR texture maps"),
            ("MODELING", "Modeling", "Modeling helpers for origins and edit selections"),
            ("ICON", "Icon", "Thumbnail framing and reference images"),
            ("LAYOUT", "Layout", "Layout snapshot tools"),
            ("ANIMATION", "Animation", "Character animation sync with Unity"),
            ("TEXTURES", "Textures", "Texture package tools"),
            ("OBJECTS", "Objects", "Legacy export-group page"),
        ),
        default="EXPORTER",
    )
    export_sections_expanded: bpy.props.BoolProperty(
        name="Sections",
        description="Show Export page section toggles",
        default=True,
    )
    export_active_section: bpy.props.EnumProperty(
        name="Section",
        description="Active Export page section",
        items=(
            ("GROUP", "Group", "Show group controls"),
            ("QUEUE", "Queue", "Show export queue controls"),
            ("ICON", "Icon", "Show icon rendering controls"),
            ("TEXTURES", "Textures", "Show texture package controls"),
        ),
        default="QUEUE",
    )
    modeling_origin_mode: bpy.props.EnumProperty(
        name="Mode",
        description="How to choose the point used for object origins",
        items=(
            ("SELECTION", "Selection", "Use the center of selected mesh elements or curve points in Edit Mode"),
            ("BOTTOM", "Bottom", "Use the center of the lowest downward-facing face"),
        ),
        default="SELECTION",
    )
    modeling_show_origin_rules: bpy.props.BoolProperty(
        name="Rules",
        description="Show automatic origin placement rules",
        default=False,
    )
    point_bookmark_group: bpy.props.EnumProperty(
        name="Group",
        description="Active temporary point bookmark group",
        items=BOOKMARK_GROUP_ITEMS,
        default="G1",
    )
    point_bookmark_source: bpy.props.EnumProperty(
        name="Source",
        description="Position source used when storing a point bookmark",
        items=SOURCE_ITEMS,
        default="SELECTION",
    )
    point_bookmark_space: bpy.props.EnumProperty(
        name="Space",
        description="Coordinate space used when storing a point bookmark",
        items=SPACE_ITEMS,
        default="WORLD",
    )
    point_g1_p1: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    point_g1_p2: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    point_g1_p3: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    point_g2_p1: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    point_g2_p2: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    point_g2_p3: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    point_g3_p1: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    point_g3_p2: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    point_g3_p3: bpy.props.PointerProperty(type=RRBuilderPointBookmarkSlot)
    show_export_textures_section: bpy.props.BoolProperty(
        name="Textures",
        description="Show texture package controls",
        default=True,
    )
    show_export_group_section: bpy.props.BoolProperty(
        name="Group",
        description="Show group controls",
        default=True,
    )
    show_export_queue_section: bpy.props.BoolProperty(
        name="Queue",
        description="Show export queue controls",
        default=True,
    )
    show_export_icon_section: bpy.props.BoolProperty(
        name="Icon",
        description="Show icon rendering controls",
        default=False,
    )
    export_group_members_expanded: bpy.props.BoolProperty(
        name="Members",
        description="Show group member objects",
        default=False,
    )
    export_group_tree_expanded: bpy.props.BoolProperty(
        name="Tree",
        description="Show nested group structure",
        default=True,
    )
    profile_name: bpy.props.StringProperty(
        name="Profile",
        default="Default",
    )
    icon_resolution: bpy.props.IntProperty(
        name="Icon Size",
        default=512,
        min=128,
        max=2048,
        update=on_icon_resolution_update,
    )
    include_icon_with_export: bpy.props.BoolProperty(
        name="Icon",
        description="Render icon.png together with model.fbx",
        default=True,
    )
    icon_outline_enabled: bpy.props.BoolProperty(
        name="Outline",
        description="Use the manually adjusted outline settings when rendering or applying an outline",
        default=True,
        update=on_icon_framing_update,
    )
    icon_outline_color: bpy.props.FloatVectorProperty(
        name="Outline Color",
        description="Color used for the icon outline",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        update=on_icon_framing_update,
    )
    icon_outline_pixels: bpy.props.IntProperty(
        name="Outline Pixels",
        description="Icon outline thickness in pixels",
        default=2,
        min=1,
        max=8,
        update=on_icon_framing_update,
    )
    include_model_with_export: bpy.props.BoolProperty(
        name="Model",
        description="Export model.fbx and material texture references",
        default=True,
    )
    skip_existing_exports: bpy.props.BoolProperty(
        name="Skip Existing Models",
        description="Reuse verified model output while always rendering a newly requested icon",
        default=True,
    )
    pbr_bake_resolution: bpy.props.IntProperty(
        name="Size",
        description="Texture size for baked PBR maps",
        default=1024,
        min=512,
        max=4096,
    )
    pbr_bake_samples: bpy.props.IntProperty(
        name="Samples",
        description="Cycles samples used by the PBR bake",
        default=32,
        min=1,
        max=512,
        options={"HIDDEN"},
    )
    pbr_bake_margin: bpy.props.IntProperty(
        name="Margin",
        description="Pixel margin used by the PBR bake",
        default=16,
        min=0,
        max=128,
        options={"HIDDEN"},
    )
    pbr_bake_output_root: bpy.props.StringProperty(
        name="Output",
        description="Folder for baked PBR maps",
        subtype="DIR_PATH",
        default=os.path.join("//", PBR_BAKE_OUTPUT_DIR),
        options={"HIDDEN"},
    )
    pbr_bake_last_summary: bpy.props.StringProperty(
        name="Last Bake",
        description="Last PBR bake result",
        default="",
        options={"HIDDEN"},
    )
    pbr_bake_last_output_dir: bpy.props.StringProperty(
        name="Last Output",
        description="Folder that received the last baked PBR maps",
        default="",
        options={"HIDDEN"},
    )
    pbr_bake_last_files: bpy.props.StringProperty(
        name="Last Files",
        description="Files written by the last PBR bake",
        default="",
        options={"HIDDEN"},
    )
    pbr_bake_running: bpy.props.BoolProperty(
        name="Baking",
        description="Whether a PBR bake is currently running",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    pbr_bake_status: bpy.props.StringProperty(
        name="Bake Status",
        description="Current PBR bake status",
        default="",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    pbr_bake_progress: bpy.props.FloatProperty(
        name="Progress",
        description="Current PBR bake progress",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    pbr_bake_disabled_materials: bpy.props.StringProperty(
        name="Disabled Materials",
        description="Bakeable material names that are unchecked in the PBR bake filter",
        default="",
        options={"HIDDEN"},
    )
    pbr_framework_use_material_filter: bpy.props.BoolProperty(
        name="Filter Materials",
        description="Only create PBR targets for explicitly selected materials",
        default=False,
        options={"HIDDEN"},
    )
    pbr_framework_selected_materials: bpy.props.StringProperty(
        name="Selected Materials",
        description="Manual PBR framework material names",
        default="",
        options={"HIDDEN"},
    )
    pbr_framework_use_role_filter: bpy.props.BoolProperty(
        name="Filter PBR Maps",
        description="Only create selected PBR map targets",
        default=False,
        options={"HIDDEN"},
    )
    pbr_framework_selected_roles: bpy.props.StringProperty(
        name="Selected PBR Maps",
        description="Manual PBR framework map keys",
        default="",
        options={"HIDDEN"},
    )
    pbr_framework_materials_expanded: bpy.props.BoolProperty(
        name="Materials",
        description="Show manual PBR material buttons",
        default=True,
    )
    pbr_framework_status: bpy.props.StringProperty(
        name="PBR Framework",
        description="Last manual PBR framework operation",
        default="",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    object_manager_auto_select_assembly: bpy.props.BoolProperty(
        name="Auto Select",
        description="Legacy option; groups no longer change normal Blender click selection",
        default=False,
    )
    object_manager_assembly_name: bpy.props.StringProperty(
        name="Group Name",
        description="Optional name for the next group",
        default="",
    )
    object_manager_assembly_type: bpy.props.EnumProperty(
        name="Type",
        description="How this group should be exported and interpreted",
        items=OBJECT_MANAGER_ASSEMBLY_TYPE_ITEMS,
        default=OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT,
        update=on_object_manager_assembly_type_update,
    )
    object_manager_current_group_name: bpy.props.StringProperty(
        name="Group Name",
        description="Name of the selected group",
        default="",
        update=on_object_manager_current_group_name_update,
    )
    object_manager_current_group_root: bpy.props.StringProperty(
        name="Current Group Root",
        description="Internal selected group name binding",
        default="",
        options={"HIDDEN"},
    )
    export_name_type: bpy.props.StringProperty(
        name="Type",
        description="Asset type prefix for the recommended export name",
        default="",
    )
    export_name_label: bpy.props.StringProperty(
        name="Label",
        description="Optional label for the recommended export name",
        default="",
    )
    export_name_show_part_names: bpy.props.BoolProperty(
        name="Part Names",
        description="Show optional short names for objects inside the group",
        default=False,
    )
    export_name_rename_members: bpy.props.BoolProperty(
        name="Rename Parts",
        description="Also give group child objects short names",
        default=False,
    )
    export_name_part_base: bpy.props.StringProperty(
        name="Part Base",
        description="Short base name for child objects when part renaming is enabled",
        default="Part",
    )
    layout_snapshot_saved_at: bpy.props.StringProperty(
        name="Layout Snapshot",
        description="Last saved layout snapshot time",
        default="",
    )
    layout_snapshot_count: bpy.props.IntProperty(
        name="Layout Snapshot Count",
        description="Number of objects stored in the layout snapshot",
        default=0,
        min=0,
    )
    icon_zoom: bpy.props.FloatProperty(
        name="Icon Zoom",
        description="Thumbnail framing zoom. Higher values make the asset larger in the icon",
        default=1.0,
        min=ICON_ZOOM_MIN,
        max=ICON_ZOOM_MAX,
        precision=2,
        update=on_icon_framing_update,
    )
    icon_offset_x: bpy.props.FloatProperty(
        name="Icon X",
        description="Thumbnail horizontal framing offset",
        default=0.0,
        precision=3,
        update=on_icon_framing_update,
    )
    icon_offset_y: bpy.props.FloatProperty(
        name="Icon Y",
        description="Thumbnail vertical framing offset",
        default=0.0,
        precision=3,
        update=on_icon_framing_update,
    )
    icon_view_yaw: bpy.props.FloatProperty(
        name="Yaw",
        description="Thumbnail camera horizontal rotation",
        default=0.0,
        precision=1,
        update=on_icon_framing_update,
    )
    icon_view_pitch: bpy.props.FloatProperty(
        name="Pitch",
        description="Thumbnail camera vertical rotation",
        default=0.0,
        min=ICON_PITCH_MIN,
        max=ICON_PITCH_MAX,
        precision=1,
        update=on_icon_framing_update,
    )
    icon_light_brightness: bpy.props.FloatProperty(
        name="Brightness",
        description="Overall thumbnail three-point light brightness",
        default=ICON_LIGHT_BRIGHTNESS_DEFAULT,
        min=ICON_LIGHT_BRIGHTNESS_MIN,
        soft_min=ICON_LIGHT_BRIGHTNESS_MIN,
        soft_max=ICON_LIGHT_BRIGHTNESS_SOFT_MAX,
        precision=2,
        update=on_icon_framing_update,
    )
    icon_key_light_ratio: bpy.props.FloatProperty(
        name="Key",
        description="Key light ratio, relative to the thumbnail brightness",
        default=ICON_KEY_LIGHT_RATIO_DEFAULT,
        min=ICON_LIGHT_RATIO_MIN,
        max=ICON_LIGHT_RATIO_MAX,
        precision=2,
        update=on_icon_framing_update,
    )
    icon_fill_light_ratio: bpy.props.FloatProperty(
        name="Fill",
        description="Fill light ratio, relative to the thumbnail brightness",
        default=ICON_FILL_LIGHT_RATIO_DEFAULT,
        min=ICON_LIGHT_RATIO_MIN,
        max=ICON_LIGHT_RATIO_MAX,
        precision=2,
        update=on_icon_framing_update,
    )
    icon_back_light_ratio: bpy.props.FloatProperty(
        name="Back",
        description="Back light ratio, relative to the thumbnail brightness",
        default=ICON_BACK_LIGHT_RATIO_DEFAULT,
        min=ICON_LIGHT_RATIO_MIN,
        max=ICON_LIGHT_RATIO_MAX,
        precision=2,
        update=on_icon_framing_update,
    )
    icon_light_edit_role: bpy.props.EnumProperty(
        name="Light Editor",
        description="Preview light currently open for detailed editing",
        items=(
            ("NONE", "None", "No preview light editor is open"),
            ("KEY", "Key", "Edit the key preview light"),
            ("FILL", "Fill", "Edit the fill preview light"),
            ("BACK", "Back", "Edit the back preview light"),
        ),
        default="NONE",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    icon_light_edit_root_name: bpy.props.StringProperty(
        name="Light Editor Asset",
        default="",
        options={"HIDDEN", "SKIP_SAVE"},
    )
    icon_light_position: bpy.props.FloatVectorProperty(
        name="Position",
        description="World position of the selected preview light",
        size=3,
        subtype="TRANSLATION",
        precision=3,
        options={"HIDDEN", "SKIP_SAVE"},
        update=on_icon_light_editor_update,
    )
    icon_light_focus: bpy.props.FloatVectorProperty(
        name="Focus",
        description="World point aimed at by the selected preview light",
        size=3,
        subtype="TRANSLATION",
        precision=3,
        options={"HIDDEN", "SKIP_SAVE"},
        update=on_icon_light_editor_update,
    )
    icon_light_use_custom_distance: bpy.props.BoolProperty(
        name="Use Range",
        description="Limit this preview light to a custom range",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
        update=on_icon_light_editor_update,
    )
    icon_light_range: bpy.props.FloatProperty(
        name="Range",
        description="Maximum influence distance of the selected preview light",
        default=40.0,
        min=0.01,
        soft_max=100.0,
        subtype="DISTANCE",
        precision=2,
        options={"HIDDEN", "SKIP_SAVE"},
        update=on_icon_light_editor_update,
    )
    icon_light_spot_size: bpy.props.FloatProperty(
        name="Beam",
        description="Cone angle of the selected preview light",
        default=math.radians(45.0),
        min=math.radians(1.0),
        max=math.pi,
        subtype="ANGLE",
        options={"HIDDEN", "SKIP_SAVE"},
        update=on_icon_light_editor_update,
    )
    icon_light_spot_blend: bpy.props.FloatProperty(
        name="Blend",
        description="Softness of the selected preview light cone edge",
        default=0.45,
        min=0.0,
        max=1.0,
        precision=2,
        options={"HIDDEN", "SKIP_SAVE"},
        update=on_icon_light_editor_update,
    )
    icon_light_radius: bpy.props.FloatProperty(
        name="Radius",
        description="Shadow softness radius of the selected preview light",
        default=0.16,
        min=0.0,
        soft_max=10.0,
        subtype="DISTANCE",
        precision=3,
        options={"HIDDEN", "SKIP_SAVE"},
        update=on_icon_light_editor_update,
    )
    icon_framing_adjusting: bpy.props.BoolProperty(
        name="Thumbnail Framing Adjusting",
        description="True while the thumbnail camera framing modal is active",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )
    icon_framing_confirm_requested: bpy.props.BoolProperty(
        name="Thumbnail Framing Confirm Requested",
        description="Internal request flag used by the Confirm button while framing is active",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )


def active_variant_preview_root(group_root, context=None):
    if group_root is None or object_manager_variant_group_root(group_root) != group_root:
        return None

    members = [member for member in object_manager_variant_member_roots(group_root) if get_export_asset_meshes(member)]
    if not members or context is None:
        return None

    view_layer_objects = getattr(getattr(context, "view_layer", None), "objects", None)
    active = view_layer_objects.active if view_layer_objects is not None else None
    if active is None:
        return None

    if is_owned_icon_preview_light(active):
        light_target = icon_light_root_from_light(active)
        if light_target in members:
            return light_target

    if active in members:
        return active

    active_root = object_manager_assembly_root_for_object(active)
    if active_root in members:
        return active_root

    return next((member for member in members if active in get_export_asset_meshes(member)), None)


def requested_variant_icon_source_root(root, context=None):
    group_root = object_manager_variant_group_root(root)
    if group_root is None:
        return None
    return active_variant_preview_root(group_root, context) or object_manager_variant_icon_source_root(group_root)


def resolve_icon_framing_root(root, context=None):
    if root is None:
        return None

    group_root = object_manager_variant_group_root(root)
    if group_root == root:
        members = [member for member in object_manager_variant_member_roots(root) if get_export_asset_meshes(member)]
        if not members:
            return None
        return active_variant_preview_root(root, context) or members[0]

    return root if get_export_asset_meshes(root) else None


def get_current_framing_root(context, settings):
    active = getattr(getattr(context, "view_layer", None), "objects", None) if context is not None else None
    active = active.active if active is not None else None
    if is_owned_icon_preview_light(active):
        active_light_target = icon_light_root_from_light(active)
        if active_light_target is not None:
            return active_light_target

    root = icon_light_editor_root(settings)
    if root is not None:
        return root

    item = get_active_queue_item(settings)
    root = queue_item_icon_preview_root(item, context, remember=True)
    if root is not None:
        prepare_framing_for_root(root, settings, item)
        return root

    roots = get_context_export_roots(context)
    root = next((resolved for candidate in roots if (resolved := resolve_icon_framing_root(candidate, context)) is not None), None)
    if root is not None:
        prepare_framing_for_root(root, settings, None)
    return root


def reset_icon_framing(settings):
    settings.icon_zoom = 1.0
    settings.icon_offset_x = 0.0
    settings.icon_offset_y = 0.0
    settings.icon_view_yaw = 0.0
    settings.icon_view_pitch = 0.0


def current_preview_path(settings, root):
    asset_id = export_asset_id(root)
    preview_dir = os.path.join(settings.output_root, "_icon_previews")
    os.makedirs(preview_dir, exist_ok=True)
    return os.path.join(preview_dir, f"{asset_id}_icon_preview.png")


def preview_cache_path(settings, root):
    asset_id = export_asset_id(root)
    return os.path.join(settings.output_root, "_icon_previews", f"{asset_id}_icon_preview.png")


def exported_icon_path(settings, root):
    asset_id = export_asset_id(root)
    return os.path.join(settings.output_root, asset_id, "icon.png")


def existing_preview_path(settings, root, item=None):
    candidates = []
    if item is not None and item.preview_path:
        candidates.append(item.preview_path)
    if root is not None:
        candidates.append(exported_icon_path(settings, root))
        candidates.append(preview_cache_path(settings, root))

    for path in candidates:
        absolute = bpy.path.abspath(path)
        if absolute and os.path.exists(absolute):
            return absolute
    return ""


def load_image_for_preview(settings, context, image_path):
    settings.preview_image_path = image_path
    if bpy.app.background or context is None or context.screen is None:
        return None

    image = bpy.data.images.load(image_path, check_existing=True)
    image.reload()
    settings.preview_image = image
    show_image_in_editor(context, image)
    return image


def show_builder_popup(context, message, title="RR Helper", icon="INFO"):
    if bpy.app.background or context is None or context.window_manager is None:
        print(f"[{title}] {message}")
        return

    context.window_manager.popup_menu(
        lambda self, _context: self.layout.label(text=message),
        title=title,
        icon=icon,
    )


def store_preview_path_for_current_item(settings, image_path):
    item = get_active_queue_item(settings)
    if item is not None:
        item.preview_path = image_path


def queue_item_for_root(settings, root):
    if settings is None or root is None:
        return None

    for item in settings.export_queue:
        if item.object_name == root.name:
            return item
    return None


def refresh_selected_preview_image(settings, context):
    if settings is None or context is None:
        return False

    item = get_active_queue_item(settings)
    root = queue_item_object(item) if item is not None else None
    if root is None:
        roots = get_context_export_roots(context)
        root = roots[0] if roots else None

    image_path = existing_preview_path(settings, root, item)
    if not image_path:
        return False

    try:
        load_image_for_preview(settings, context, image_path)
    except Exception:
        return False
    return True


def get_preview_collection():
    collection = PREVIEW_COLLECTIONS.get("queue_icons")
    if collection is None:
        import bpy.utils.previews

        collection = bpy.utils.previews.new()
        PREVIEW_COLLECTIONS["queue_icons"] = collection
    return collection


def clear_preview_collections():
    import bpy.utils.previews

    for collection in PREVIEW_COLLECTIONS.values():
        bpy.utils.previews.remove(collection)
    PREVIEW_COLLECTIONS.clear()


def get_preview_icon_id(image_path):
    if not image_path:
        return 0

    path = bpy.path.abspath(image_path)
    if not os.path.exists(path):
        return 0

    collection = get_preview_collection()
    try:
        key = f"{path}|{os.path.getmtime(path)}"
        preview = collection.get(key)
        if preview is None:
            preview = collection.load(key, path, "IMAGE")
        return preview.icon_id
    except Exception:
        return 0


def draw_square_preview(layout, image_path, label="", scale=5.0):
    icon_id = get_preview_icon_id(image_path)
    if not icon_id:
        return False

    col = layout.column(align=True)
    col.alignment = "CENTER"
    row = col.row(align=True)
    row.alignment = "CENTER"
    row.template_icon(icon_value=icon_id, scale=scale)
    if label:
        col.label(text=label)
    return True


def active_reference_path(settings):
    if settings is None or len(settings.references) == 0:
        return ""

    index = max(0, min(settings.reference_active_index, len(settings.references) - 1))
    path = bpy.path.abspath(settings.references[index].filepath)
    return path if os.path.exists(path) else ""


def normalized_file_path(path):
    return os.path.normcase(os.path.abspath(bpy.path.abspath(path))).rstrip("\\/")


def add_reference_path(settings, filepath, name=None, source="Manual", asset_id=""):
    if settings is None or not filepath:
        return False

    absolute_path = bpy.path.abspath(filepath)
    if not os.path.exists(absolute_path):
        return False

    target = normalized_file_path(absolute_path)
    for index, item in enumerate(settings.references):
        if normalized_file_path(item.filepath) == target:
            settings.reference_active_index = index
            return False

    item = settings.references.add()
    item.filepath = absolute_path
    item.name = name or os.path.basename(absolute_path)
    item.source = source or "Manual"
    item.asset_id = asset_id or ""
    settings.reference_active_index = len(settings.references) - 1
    return True


def remove_unity_reference_icons(settings):
    if settings is None:
        return 0

    removed = 0
    for index in range(len(settings.references) - 1, -1, -1):
        if settings.references[index].source == "Unity":
            settings.references.remove(index)
            removed += 1

    settings.reference_active_index = max(0, min(settings.reference_active_index, len(settings.references) - 1))
    return removed


def unity_asset_path_to_file_path(asset_path):
    if not asset_path:
        return ""

    normalized = str(asset_path).replace("\\", "/").strip()
    if not normalized:
        return ""

    if normalized.lower() == "assets" or normalized.lower().startswith("assets/"):
        return os.path.join(UNITY_PROJECT_ROOT, *normalized.split("/"))

    return bpy.path.abspath(normalized)


def iter_dashboard_reference_icon_pool(seen_ids):
    index_path = bpy.path.abspath(UNITY_BUILDER_REFERENCE_INDEX)
    if not os.path.isfile(index_path):
        return

    try:
        with open(index_path, "r", encoding="utf-8") as handle:
            index_data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return

    entries = index_data.get("entries", []) if isinstance(index_data, dict) else []
    matches = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        asset_id = entry.get("id") or ""
        if not asset_id or asset_id in seen_ids:
            continue

        icon_path = entry.get("iconFile") or unity_asset_path_to_file_path(entry.get("iconPath"))
        if not icon_path:
            continue

        icon_path = bpy.path.abspath(icon_path)
        if not icon_path or not os.path.isfile(icon_path):
            continue

        display_name = entry.get("displayName") or asset_id
        category = entry.get("category") or ""
        if category.strip().lower() != "simple":
            continue
        active = "Active" if entry.get("active") else "Held"
        details = f"{display_name} - {category} - {active}" if category else f"{display_name} - {active}"
        matches.append((details, asset_id, icon_path))

    for details, asset_id, icon_path in sorted(matches, key=lambda item: item[0].lower()):
        seen_ids.add(asset_id)
        yield "Dashboard", asset_id, icon_path, details


def iter_unity_reference_icon_pool(settings=None):
    seen_ids = set()

    for source_name, asset_id, icon_path, display_name in iter_dashboard_reference_icon_pool(seen_ids):
        yield source_name, asset_id, icon_path, display_name


def refresh_unity_reference_icons(settings, context=None):
    remove_unity_reference_icons(settings)

    found = 0
    added = 0
    last_path = ""
    for source_name, asset_id, icon_path, display_name in iter_unity_reference_icon_pool(settings):
        found += 1
        label = display_name or asset_id
        if add_reference_path(settings, icon_path, f"{label} ({source_name})", "Unity", asset_id):
            added += 1
        last_path = icon_path

    if found > 0:
        settings.show_references = True

    if context is not None and last_path:
        load_image_for_preview(settings, context, last_path)

    return added, found


def current_reference_roots(context, settings):
    item = get_active_queue_item(settings)
    root = queue_item_object(item)
    if root is not None:
        return [root]

    roots = get_context_export_roots(context)
    if roots:
        return roots

    queue_roots = []
    for item in settings.export_queue:
        root = queue_item_object(item)
        if root is not None:
            queue_roots.append(root)
    return queue_roots


def unity_reference_icon_candidates(asset_id, settings=None):
    candidates = [
        ("Unity", os.path.join(UNITY_GENERATED_BUILD_ART_ROOT, asset_id, "icon.png")),
        ("Temp", os.path.join(UNITY_TEMP_OUTPUT_ROOT, asset_id, "icon.png")),
    ]
    if settings is not None:
        candidates.append(("Output", os.path.join(settings.output_root, asset_id, "icon.png")))
    return candidates


def current_preview_display_path(context, settings):
    item = get_active_queue_item(settings)
    root = queue_item_object(item) if item is not None else None
    if root is None:
        roots = get_context_export_roots(context)
        root = roots[0] if roots else None

    path = existing_preview_path(settings, root, item)
    if path:
        return path

    if settings.preview_image_path and os.path.exists(bpy.path.abspath(settings.preview_image_path)):
        return bpy.path.abspath(settings.preview_image_path)

    return ""


def draw_preview_reference_pair(layout, context, settings):
    compare_box = layout.box()
    compare_box.label(text="Preview / Reference")
    split = compare_box.split(factor=0.5, align=True)

    left = split.column(align=True)
    left.label(text="Selected")
    selected_path = current_preview_display_path(context, settings)
    if not draw_square_preview(left, selected_path, os.path.basename(selected_path), scale=4.2):
        left.label(text="No preview yet.")

    right = split.column(align=True)
    right.label(text="Reference")
    reference_path = active_reference_path(settings)
    if not draw_square_preview(right, reference_path, os.path.basename(reference_path), scale=4.2):
        right.label(text="No reference.")


class RR_UL_export_queue_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        root = queue_item_object(item)
        row = layout.row(align=True)
        if root and object_manager_variant_group_root(root):
            label = f"{object_manager_display_name(root)}  Variants ({len(object_manager_variant_member_roots(root))})"
            icon_name = "OUTLINER_OB_GROUP_INSTANCE"
        else:
            label = object_manager_display_name(root) if root else item.object_name or "<missing>"
            icon_name = "OBJECT_DATA" if root else "ERROR"
        row.label(text=label, icon=icon_name)
        row.label(text=f"{item.icon_zoom:.2f}x")


class RR_UL_reference_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name or os.path.basename(item.filepath) or "<reference>", icon="IMAGE_DATA")
        if item.source:
            row.label(text=item.source)


class RR_OT_queue_selected(bpy.types.Operator):
    bl_idname = "rr_builder.queue_selected"
    bl_label = "Add Selection to Queue"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        autosave_icon_preview_lights(context.scene)
        roots = queue_roots_for_export_roots(get_context_export_roots(context))
        if not roots:
            active_name = context.object.name if context.object else "None"
            self.report({"ERROR"}, f"No exportable selected mesh roots. Active object is {active_name}.")
            return {"CANCELLED"}

        existing = {
            item.object_name: (index, item)
            for index, item in enumerate(settings.export_queue)
        }
        added = 0
        target_index = None
        for root in roots:
            snapshot_export_identity(root)
            for export_root in expand_related_export_roots([root]):
                snapshot_export_identity(export_root)
            preview_root = resolve_icon_framing_root(root, context)
            existing_entry = existing.get(root.name)
            if existing_entry is not None:
                target_index, existing_item = existing_entry
                if preview_root is not None:
                    existing_item.icon_preview_root_name = preview_root.name
                continue
            item = settings.export_queue.add()
            item.object_name = root.name
            item.icon_preview_root_name = preview_root.name if preview_root is not None else ""
            initialize_queue_item_framing(item, preview_root or root, settings)
            target_index = len(settings.export_queue) - 1
            existing[root.name] = (target_index, item)
            added += 1

        if target_index is not None:
            set_queue_active_index_without_preview_sync(settings, target_index)

        if added == 0:
            self.report({"INFO"}, "Selected roots are already in the export queue.")
        else:
            self.report({"INFO"}, f"Queued {added} item(s).")
        return {"FINISHED"}


class RR_OT_remove_queue_item(bpy.types.Operator):
    bl_idname = "rr_builder.remove_queue_item"
    bl_label = "Remove Queue Item"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if len(settings.export_queue) == 0:
            return {"CANCELLED"}

        index = max(0, min(settings.queue_active_index, len(settings.export_queue) - 1))
        settings.export_queue.remove(index)
        settings.queue_active_index = max(0, min(index, len(settings.export_queue) - 1))
        return {"FINISHED"}


class RR_OT_clear_queue(bpy.types.Operator):
    bl_idname = "rr_builder.clear_queue"
    bl_label = "Clear Export Queue"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        settings.export_queue.clear()
        settings.queue_active_index = 0
        return {"FINISHED"}


class RR_OT_step_queue_item(bpy.types.Operator):
    bl_idname = "rr_builder.step_queue_item"
    bl_label = "Step Export Queue"
    direction: bpy.props.IntProperty(default=1)

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if len(settings.export_queue) == 0:
            return {"CANCELLED"}

        count = len(settings.export_queue)
        settings.queue_active_index = (settings.queue_active_index + self.direction) % count
        return {"FINISHED"}


class RR_OT_select_queue_item(bpy.types.Operator):
    bl_idname = "rr_builder.select_queue_item"
    bl_label = "Select Queue Item"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        item = get_active_queue_item(settings)
        root = queue_item_object(item)
        if root is None or not select_queue_item_object(context, item):
            self.report({"ERROR"}, "Queue item object is missing.")
            return {"CANCELLED"}

        copy_queue_item_to_settings(item, settings)
        sync_icon_preview_to_root(root, settings, context)
        refresh_selected_preview_image(settings, context)
        return {"FINISHED"}


class RR_OT_preview_icon(bpy.types.Operator):
    bl_idname = "rr_builder.preview_icon"
    bl_label = "Preview Icon"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        root = get_current_framing_root(context, settings)
        if root is None:
            active_name = context.object.name if context.object else "None"
            self.report({"ERROR"}, f"No queue item or exportable selected root. Active object is {active_name}.")
            return {"CANCELLED"}

        copy_settings_to_queue_item(settings, get_active_queue_item(settings))
        apply_icon_render_resolution(context.scene, settings)
        image_path = current_preview_path(settings, root)
        shared_root = shared_builder_icon_root(root)
        shared_preview_path = current_preview_path(settings, shared_root) if shared_root is not root else image_path
        render_or_copy_shared_icon(
            root,
            settings,
            image_path,
            shared_icon_root=shared_root,
            shared_icon_path=shared_preview_path,
            force_render=True,
        )
        load_image_for_preview(settings, context, image_path)
        store_preview_path_for_current_item(settings, image_path)
        self.report({"INFO"}, f"Preview rendered: {os.path.basename(image_path)}")
        return {"FINISHED"}


class RR_OT_preview_queue_icons(bpy.types.Operator):
    bl_idname = "rr_builder.preview_queue_icons"
    bl_label = "Preview Queue Icons"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if len(settings.export_queue) == 0:
            self.report({"ERROR"}, "Export queue is empty.")
            return {"CANCELLED"}

        original_index = settings.queue_active_index
        original_framing = (
            settings.icon_zoom,
            settings.icon_offset_x,
            settings.icon_offset_y,
            settings.icon_view_yaw,
            settings.icon_view_pitch,
        )
        rendered = []
        failed = []

        try:
            apply_icon_render_resolution(context.scene, settings)
            for index, item in enumerate(settings.export_queue):
                root = queue_item_object(item)
                if root is None:
                    failed.append(f"{item.object_name}: missing object")
                    continue

                settings.queue_active_index = index
                prepare_framing_for_root(root, settings, item)
                image_path = current_preview_path(settings, root)
                try:
                    shared_root = shared_builder_icon_root(root)
                    shared_preview_path = current_preview_path(settings, shared_root) if shared_root is not root else image_path
                    render_or_copy_shared_icon(
                        root,
                        settings,
                        image_path,
                        shared_icon_root=shared_root,
                        shared_icon_path=shared_preview_path,
                        force_render=True,
                    )
                    item.preview_path = image_path
                    rendered.append(root.name)
                except Exception as exc:
                    failed.append(f"{root.name}: {exc}")

            if rendered:
                last_path = settings.export_queue[settings.queue_active_index].preview_path
                if last_path:
                    load_image_for_preview(settings, context, last_path)
        finally:
            if len(settings.export_queue) > 0:
                settings.queue_active_index = max(0, min(original_index, len(settings.export_queue) - 1))
            (
                settings.icon_zoom,
                settings.icon_offset_x,
                settings.icon_offset_y,
                settings.icon_view_yaw,
                settings.icon_view_pitch,
            ) = original_framing

        for item in failed:
            print("[Builder Icon Preview]", item)

        if failed:
            self.report({"WARNING"}, f"Rendered {len(rendered)} queue previews; failed {len(failed)}. See console.")
            return {"CANCELLED" if not rendered else "FINISHED"}

        self.report({"INFO"}, f"Rendered {len(rendered)} queue previews.")
        return {"FINISHED"}


class RR_OT_export_queue(bpy.types.Operator):
    bl_idname = "rr_builder.export_queue"
    bl_label = "Export Queue"
    include_model: bpy.props.BoolProperty(name="Model", default=True)
    include_icon: bpy.props.BoolProperty(name="With Icons", default=True)

    def execute(self, context):
        export_started = time.perf_counter()
        if not ensure_object_mode(context):
            self.report({"ERROR"}, "Could not leave Edit Mode for export.")
            return {"CANCELLED"}

        settings = context.scene.rr_builder_export_settings
        if len(settings.export_queue) == 0:
            self.report({"ERROR"}, "Export queue is empty.")
            return {"CANCELLED"}
        if not self.include_model and not self.include_icon:
            self.report({"ERROR"}, "Enable Model, Icon, or both before exporting.")
            return {"CANCELLED"}

        roots = []
        seen_export_roots = set()
        source_names_by_index = {}
        queued_item_by_name = {}
        failed = []
        original_index = settings.queue_active_index
        original_framing = (
            settings.icon_zoom,
            settings.icon_offset_x,
            settings.icon_offset_y,
            settings.icon_view_yaw,
            settings.icon_view_pitch,
        )

        for index, item in enumerate(settings.export_queue):
            root = queue_item_object(item)
            if root is None:
                failed.append(f"{item.object_name}: missing object")
                continue

            queued_item_by_name[root.name] = item
            expanded = expand_related_export_roots([root])
            if not expanded:
                failed.append(
                    f"{object_manager_display_name(root)}: no exportable mesh variants or assembly members"
                )
            source_names_by_index[index] = {candidate.name for candidate in expanded}
            for candidate in expanded:
                if candidate.name in seen_export_roots:
                    continue
                roots.append(candidate)
                seen_export_roots.add(candidate.name)

        exported = []
        skipped = []
        successful_root_names = set()
        icon_render_cache = {}
        try:
            variant_transactions, variant_transactions_by_member, transaction_failures = (
                prepare_variant_export_transactions(roots, settings)
            )
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        failed.extend(transaction_failures)
        try:
            for root in roots:
                try:
                    transaction = variant_transactions_by_member.get(root.name)
                    if transaction is not None and transaction.get("settings") is None:
                        continue
                    export_settings = transaction["settings"] if transaction is not None else settings
                    item = queue_item_for_root(settings, root) or queued_item_by_name.get(root.name)
                    index = queue_index_for_root(settings, root)
                    if index < 0:
                        for source_index, source_names in source_names_by_index.items():
                            if root.name in source_names:
                                index = source_index
                                item = item or settings.export_queue[source_index]
                                break
                    if item is not None and index >= 0:
                        settings.queue_active_index = index
                    prepare_framing_for_root(root, settings, item)
                    asset_id, asset_type, status = export_builder_asset(
                        root,
                        export_settings,
                        self.include_model,
                        self.include_icon,
                        shared_icon_root=shared_builder_icon_root(root),
                        queue_import=False,
                        icon_render_cache=icon_render_cache,
                    )
                    successful_root_names.add(root.name)
                    if status == "skipped":
                        skipped.append(f"{asset_id} ({asset_type})")
                    else:
                        exported.append(f"{asset_id} ({asset_type})")
                except Exception as exc:
                    failed.append(f"{root.name}: {exc}")
        finally:
            if len(settings.export_queue) > 0:
                settings.queue_active_index = max(0, min(original_index, len(settings.export_queue) - 1))
            (
                settings.icon_zoom,
                settings.icon_offset_x,
                settings.icon_offset_y,
                settings.icon_view_yaw,
                settings.icon_view_pitch,
            ) = original_framing

        published_root_names, variant_publish_failures = finalize_variant_export_transactions(
            variant_transactions,
            successful_root_names,
        )
        failed.extend(variant_publish_failures)
        ordinary_paths, ordinary_published_names = completed_ordinary_export_publication(
            roots,
            successful_root_names,
            settings.output_root,
        )
        if ordinary_paths:
            try:
                queue_unity_builder_import(ordinary_paths)
                published_root_names.update(ordinary_published_names)
            except Exception as exc:
                failed.append(f"Unity import queue: {exc}")

        exported_indices = {
            index
            for index, source_names in source_names_by_index.items()
            if source_names and source_names.issubset(published_root_names)
        }
        for index in sorted(exported_indices, reverse=True):
            if index < len(settings.export_queue):
                settings.export_queue.remove(index)

        if len(settings.export_queue) > 0:
            settings.queue_active_index = max(0, min(original_index, len(settings.export_queue) - 1))
        else:
            settings.queue_active_index = 0

        for item in failed:
            print("[RandomRealm Builder Exporter]", item)

        cleared_count = len(exported_indices)
        elapsed = time.perf_counter() - export_started
        if failed:
            self.report({"WARNING"}, f"Exported {len(exported)}, skipped {len(skipped)}, cleared {cleared_count}; {len(failed)} failed items remain ({elapsed:.1f}s). See console.")
            return {"CANCELLED" if not exported and not skipped else "FINISHED"}

        self.report({"INFO"}, f"Exported {len(exported)}, skipped {len(skipped)}, cleared {cleared_count} queued assets in {elapsed:.1f}s.")
        return {"FINISHED"}


class RR_OT_add_reference_image(bpy.types.Operator):
    bl_idname = "rr_builder.add_reference_image"
    bl_label = "Add Reference Image"
    filepath: bpy.props.StringProperty(name="Image", subtype="FILE_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath:
            return {"CANCELLED"}

        settings = context.scene.rr_builder_export_settings
        add_reference_path(settings, self.filepath)
        try:
            load_image_for_preview(settings, context, self.filepath)
        except Exception as exc:
            self.report({"WARNING"}, f"Reference added, but Blender could not preview it: {exc}")
        return {"FINISHED"}


class RR_OT_refresh_unity_reference_icons(bpy.types.Operator):
    bl_idname = "rr_builder.refresh_unity_reference_icons"
    bl_label = "Refresh Unity Reference Icons"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        added, found = refresh_unity_reference_icons(settings, context)
        if found == 0:
            self.report({"ERROR"}, "No matching Unity icon.png files found.")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Loaded {found} Unity icon reference(s); {added} newly added.")
        return {"FINISHED"}


class RR_OT_remove_reference_image(bpy.types.Operator):
    bl_idname = "rr_builder.remove_reference_image"
    bl_label = "Remove Reference Image"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if len(settings.references) == 0:
            return {"CANCELLED"}

        index = max(0, min(settings.reference_active_index, len(settings.references) - 1))
        settings.references.remove(index)
        settings.reference_active_index = max(0, min(index, len(settings.references) - 1))
        return {"FINISHED"}


class RR_OT_show_reference_image(bpy.types.Operator):
    bl_idname = "rr_builder.show_reference_image"
    bl_label = "Show Reference Image"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if len(settings.references) == 0:
            return {"CANCELLED"}

        index = max(0, min(settings.reference_active_index, len(settings.references) - 1))
        item = settings.references[index]
        if not os.path.exists(bpy.path.abspath(item.filepath)):
            self.report({"ERROR"}, "Reference image file is missing.")
            return {"CANCELLED"}

        load_image_for_preview(settings, context, bpy.path.abspath(item.filepath))
        return {"FINISHED"}


class RR_OT_create_object_assembly(bpy.types.Operator):
    bl_idname = "rr_builder.create_object_assembly"
    bl_label = "Make Group"
    bl_description = "Mark selected objects as one group"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        try:
            root, members = create_object_manager_assembly(
                context,
                getattr(settings, "object_manager_assembly_name", ""),
                getattr(settings, "object_manager_assembly_type", OBJECT_MANAGER_ASSEMBLY_TYPE_DEFAULT),
            )
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        remember_object_manager_runtime_objects()
        settings.object_manager_assembly_name = ""
        sync_object_manager_current_group_name(settings, root)
        self.report({"INFO"}, f"Made {object_manager_assembly_type_label(root)} group '{object_manager_display_name(root)}' with {len(members)} object(s).")
        return {"FINISHED"}


class RR_OT_rename_object_manager_entry(bpy.types.Operator):
    bl_idname = "rr_builder.rename_object_manager_entry"
    bl_label = "Rename"
    bl_description = "Rename this group or member while preserving its export identity"
    bl_options = {"REGISTER", "UNDO"}

    target_name: bpy.props.StringProperty(options={"HIDDEN"})
    root_name: bpy.props.StringProperty(options={"HIDDEN"})
    entry_name: bpy.props.StringProperty(name="Name")

    def invoke(self, context, event):
        target = bpy.data.objects.get(self.target_name)
        if target is None:
            self.report({"ERROR"}, "The group entry is no longer available.")
            return {"CANCELLED"}

        root = bpy.data.objects.get(self.root_name) if self.root_name else None
        select_object_manager_entry_for_rename(context, target, root)
        self.entry_name = object_manager_display_name(target) if is_object_manager_assembly_root(target) else target.name
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(self, "entry_name", text="")

    def execute(self, context):
        target = bpy.data.objects.get(self.target_name)
        if target is None:
            self.report({"ERROR"}, "The group entry is no longer available.")
            return {"CANCELLED"}

        desired_name = sanitize_optional_id(self.entry_name)
        if not desired_name:
            self.report({"ERROR"}, "Enter a name.")
            return {"CANCELLED"}

        old_name = object_manager_display_name(target) if is_object_manager_assembly_root(target) else target.name
        applied_name = rename_export_asset_preserving_identity(target, desired_name)
        settings = context.scene.rr_builder_export_settings
        current_root = selected_object_manager_assembly_root(context)
        if current_root is not None:
            sync_object_manager_current_group_name(settings, current_root)
        tag_rr_addon_view3d_redraw()
        self.report({"INFO"}, f"Renamed '{old_name}' to '{applied_name}'.")
        return {"FINISHED"}


class RR_OT_select_object_assembly(bpy.types.Operator):
    bl_idname = "rr_builder.select_object_assembly"
    bl_label = "Select Group"
    bl_description = "Click to select this group; double-click to rename it"
    bl_options = {"REGISTER", "UNDO"}

    root_name: bpy.props.StringProperty(options={"HIDDEN"})
    toggle: bpy.props.BoolProperty(default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        if self.root_name and object_manager_row_requests_rename(self.root_name, event):
            return bpy.ops.rr_builder.rename_object_manager_entry(
                "INVOKE_DEFAULT",
                target_name=self.root_name,
                root_name=self.root_name,
            )
        return self.execute(context)

    def execute(self, context):
        root = bpy.data.objects.get(self.root_name) if self.root_name else None
        if root is None or not is_object_manager_assembly_root(root):
            root = selected_object_manager_assembly_root(context)
        if root is None:
            roots = get_context_export_roots(context)
            root = roots[0] if roots and is_object_manager_assembly_root(roots[0]) else None
        if root is None:
            self.report({"ERROR"}, "Selection is not in a group.")
            return {"CANCELLED"}

        settings = context.scene.rr_builder_export_settings
        sync_object_manager_current_group_name(settings, root)
        members = object_manager_selection_objects(root)
        if self.toggle and members and all(member.select_get() for member in members):
            deselect_object_manager_assembly(context, root)
            self.report({"INFO"}, f"Deselected group '{object_manager_display_name(root)}'.")
            return {"FINISHED"}

        if not select_object_manager_assembly(context, root):
            self.report({"ERROR"}, "Selection is not in a group.")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Selected group '{object_manager_display_name(root)}'.")
        return {"FINISHED"}


class RR_OT_toggle_object_assembly_member_selection(bpy.types.Operator):
    bl_idname = "rr_builder.toggle_object_assembly_member_selection"
    bl_label = "Select Member"
    bl_description = "Click to toggle this member; double-click to rename it"
    bl_options = {"REGISTER", "UNDO"}

    object_name: bpy.props.StringProperty(options={"HIDDEN"})
    root_name: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        if self.object_name and object_manager_row_requests_rename(self.object_name, event):
            return bpy.ops.rr_builder.rename_object_manager_entry(
                "INVOKE_DEFAULT",
                target_name=self.object_name,
                root_name=self.root_name,
            )
        return self.execute(context)

    def execute(self, context):
        global OBJECT_MANAGER_SELECTION_SYNCING

        obj = bpy.data.objects.get(self.object_name)
        root = bpy.data.objects.get(self.root_name)
        if obj is None or root is None or not is_object_manager_assembly_root(root):
            self.report({"ERROR"}, "The group member is no longer available.")
            return {"CANCELLED"}

        scope = set(object_manager_member_objects(root))
        scope.add(root)
        if obj not in scope:
            self.report({"ERROR"}, "The object is not a member of this group.")
            return {"CANCELLED"}

        OBJECT_MANAGER_SELECTION_SYNCING = True
        try:
            for selected in list(context.selected_objects):
                if selected not in scope:
                    selected.select_set(False)
            new_state = not obj.select_get()
            obj.select_set(new_state)
            if new_state:
                context.view_layer.objects.active = obj
            elif context.view_layer.objects.active == obj:
                remaining = [candidate for candidate in context.selected_objects if candidate in scope]
                context.view_layer.objects.active = remaining[0] if remaining else root
        finally:
            OBJECT_MANAGER_SELECTION_SYNCING = False

        remember_object_manager_detail_selection(context, root)
        return {"FINISHED"}


class RR_OT_clear_native_duplicate_rr_identity(bpy.types.Operator):
    bl_idname = "rr_builder.clear_native_duplicate_rr_identity"
    bl_label = "Prepare Independent Duplicate"
    bl_description = "Keep native Blender duplicates outside RR groups"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        clear_inherited_rr_identity_from_objects(context.selected_objects or [])
        return {"FINISHED"}


class RR_OT_duplicate_move_without_group_membership(bpy.types.Macro):
    bl_idname = "rr_builder.duplicate_move_without_group_membership"
    bl_label = "Duplicate Independent"
    bl_description = "Duplicate for ordinary Blender use without adding the copy to an RR group"
    bl_options = {"REGISTER", "UNDO"}


class RR_OT_duplicate_object_assembly_variant(bpy.types.Operator):
    bl_idname = "rr_builder.duplicate_object_assembly_variant"
    bl_label = "Duplicate Variant"
    bl_description = "Duplicate the active group and select its movable members"
    bl_options = {"REGISTER", "UNDO"}

    root_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        root = bpy.data.objects.get(self.root_name) if self.root_name else None
        if root is None or not is_object_manager_assembly_root(root):
            root = selected_object_manager_assembly_root(context)
        if root is None:
            roots = get_context_export_roots(context)
            root = roots[0] if roots and is_object_manager_assembly_root(roots[0]) else None
        root = object_manager_duplicate_source_for_context(context, root)
        try:
            new_root, selected = duplicate_object_manager_group(context, root)
            variants_parent, created_parent = ensure_object_manager_variants_parent(context, root, new_root)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        remember_object_manager_runtime_objects()
        settings = context.scene.rr_builder_export_settings
        sync_object_manager_current_group_name(settings, variants_parent)
        parent_note = f" in new group '{object_manager_display_name(variants_parent)}'" if created_parent else ""
        self.report(
            {"INFO"},
            f"Added variant '{object_manager_display_name(new_root)}'{parent_note} and selected {len(selected)} object(s).",
        )
        return {"FINISHED"}


class RR_OT_add_selected_object_assembly_members(bpy.types.Operator):
    bl_idname = "rr_builder.add_selected_object_assembly_members"
    bl_label = "Add Selected to Group"
    bl_description = "Add the selected outside object(s) to this group"
    bl_options = {"REGISTER", "UNDO"}

    root_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        root = bpy.data.objects.get(self.root_name) if self.root_name else None
        if root is None or not is_object_manager_assembly_root(root):
            root = object_manager_group_from_settings(settings) or selected_object_manager_assembly_root(context)
        try:
            added = add_selected_object_manager_members(context, root)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        sync_object_manager_current_group_name(settings, root)
        self.report(
            {"INFO"},
            f"Added {len(added)} selected object(s) to '{object_manager_display_name(root)}'.",
        )
        return {"FINISHED"}


class RR_OT_dissolve_object_assembly(bpy.types.Operator):
    bl_idname = "rr_builder.dissolve_object_assembly"
    bl_label = "Remove Selected from Group"
    bl_description = "Remove selected members from this group without deleting the objects"
    bl_options = {"REGISTER", "UNDO"}

    root_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        root = bpy.data.objects.get(self.root_name) if self.root_name else None
        if root is None or not is_object_manager_assembly_root(root):
            root = selected_object_manager_assembly_root(context)
        if root is None:
            roots = get_context_export_roots(context)
            root = roots[0] if roots and is_object_manager_assembly_root(roots[0]) else None
        try:
            members, current_root, dissolved = ungroup_selected_object_manager_members(context, root)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        settings = context.scene.rr_builder_export_settings
        remember_object_manager_detail_selection(context, None)
        if current_root is not None:
            remaining = object_manager_group_entries(current_root)
            active_name = str(current_root.get(OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP, "") or "")
            if active_name not in {member.name for member in remaining}:
                current_root[OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = remaining[0].name if remaining else current_root.name
            if object_manager_assembly_type(current_root) == "VARIANTS":
                variant_members = object_manager_variant_member_roots(current_root)
                if variant_members:
                    remember_object_manager_variant_icon_source(current_root, variant_members[0])
        sync_object_manager_current_group_name(settings, current_root)
        if dissolved:
            self.report({"INFO"}, f"Removed all {len(members)} member(s); the empty group was dissolved.")
        else:
            self.report({"INFO"}, f"Removed {len(members)} selected member(s) from the group.")
        return {"FINISHED"}


class RR_OT_apply_recommended_export_name(bpy.types.Operator):
    bl_idname = "rr_builder.apply_recommended_export_name"
    bl_label = "Apply Name"
    bl_description = "Apply the recommended export name to the current export root"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        root = export_name_root_from_context(context)
        if root is None:
            self.report({"ERROR"}, "Select an exportable object or group first.")
            return {"CANCELLED"}

        desired_name = recommended_export_name(root, settings)
        old_name = object_manager_display_name(root)
        desired_name = rename_export_asset_preserving_identity(root, desired_name)
        root[EXPORT_NAME_HINT_DISMISSED_PROP] = False

        renamed_parts = 0
        if (
            getattr(settings, "export_name_show_part_names", False)
            and getattr(settings, "export_name_rename_members", False)
        ):
            renamed_parts = rename_export_group_members(root, getattr(settings, "export_name_part_base", "Part"))

        if renamed_parts:
            self.report({"INFO"}, f"Renamed '{old_name}' to '{object_manager_display_name(root)}' and {renamed_parts} part(s).")
        else:
            self.report({"INFO"}, f"Renamed '{old_name}' to '{object_manager_display_name(root)}'.")
        return {"FINISHED"}


class RR_OT_dismiss_export_name_hint(bpy.types.Operator):
    bl_idname = "rr_builder.dismiss_export_name_hint"
    bl_label = "Dismiss Name Hint"
    bl_description = "Hide the export-name recommendation for this object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = export_name_root_from_context(context)
        if root is None:
            return {"CANCELLED"}

        root[EXPORT_NAME_HINT_DISMISSED_PROP] = True
        return {"FINISHED"}


class RR_OT_create_bounding_box_collider(bpy.types.Operator):
    bl_idname = "rr_builder.create_bounding_box_collider"
    bl_label = "Create Collider"
    bl_description = "Create or update a Bounding Box collider object for the selected or queued asset"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings

        try:
            if context.object is not None and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

        root = None
        active = context.view_layer.objects.active or context.object
        if active is not None:
            assembly_root = object_manager_assembly_root_for_object(active)
            target = collider_target_object(active)
            if assembly_root is not None:
                root = assembly_root
            elif target is not None:
                root = target
            elif active.type in {"EMPTY", "MESH"} and not is_collision_helper(active) and get_asset_meshes(active):
                root = active

        if root is None:
            roots = get_context_export_roots(context)
            root = roots[0] if roots else None

        if root is None:
            item = get_active_queue_item(settings)
            root = queue_item_object(item) if item is not None else None

        if root is None:
            self.report({"ERROR"}, "Select or queue an exportable object first.")
            return {"CANCELLED"}

        try:
            collider, created = create_or_update_bounding_box_collider(root)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        bpy.ops.object.select_all(action="DESELECT")
        root.select_set(True)
        collider.select_set(True)
        context.view_layer.objects.active = collider

        action = "Created" if created else "Updated"
        self.report({"INFO"}, f"{action} collider '{collider.name}' for {root.name}.")
        return {"FINISHED"}


class RR_OT_validate_selected(bpy.types.Operator):
    bl_idname = "rr_builder.validate_selected"
    bl_label = "Builder Validate Asset"

    def execute(self, context):
        roots = get_context_export_roots(context)
        if not roots:
            active_name = context.object.name if context.object else "None"
            self.report({"ERROR"}, f"No exportable selected mesh roots. Active object is {active_name}.")
            return {"CANCELLED"}

        unknown = []
        for obj in roots:
            asset_id = export_asset_id(obj)
            if infer_asset_type(asset_id) == "Unknown":
                unknown.append(asset_id)

        if unknown:
            self.report({"WARNING"}, f"{len(roots)} exportable roots; unknown type: {', '.join(unknown)}")
            return {"FINISHED"}

        self.report({"INFO"}, f"{len(roots)} selected asset roots look exportable.")
        return {"FINISHED"}


class RR_OT_export_selected(bpy.types.Operator):
    bl_idname = "rr_builder.export_selected"
    bl_label = "Builder Export Selected Assets"
    include_model: bpy.props.BoolProperty(name="Model", default=True)
    include_icon: bpy.props.BoolProperty(name="With Icons", default=True)

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if not self.include_model and not self.include_icon:
            self.report({"ERROR"}, "Enable Model, Icon, or both before exporting.")
            return {"CANCELLED"}

        settings.include_model_with_export = self.include_model
        settings.include_icon_with_export = self.include_icon
        mesh_objects = get_context_export_roots(context)

        if not mesh_objects:
            active_name = context.object.name if context.object else "None"
            self.report({"ERROR"}, f"No exportable selected mesh roots. Active object is {active_name}.")
            return {"CANCELLED"}

        return export_objects(mesh_objects, settings, context, "selected", self.include_model, self.include_icon)


class RR_OT_export_collection(bpy.types.Operator):
    bl_idname = "rr_builder.export_collection"
    bl_label = "Builder Export Collection"
    include_model: bpy.props.BoolProperty(name="Model", default=True)
    include_icon: bpy.props.BoolProperty(name="With Icons", default=True)

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if not self.include_model and not self.include_icon:
            self.report({"ERROR"}, "Enable Model, Icon, or both before exporting.")
            return {"CANCELLED"}

        settings.include_model_with_export = self.include_model
        settings.include_icon_with_export = self.include_icon
        collection = context.collection
        mesh_objects = get_export_roots(collection.objects)

        if not mesh_objects:
            self.report({"ERROR"}, "Active collection has no exportable mesh objects.")
            return {"CANCELLED"}

        return export_objects(mesh_objects, settings, context, "collection", self.include_model, self.include_icon)


class RR_OT_render_selected_icons(bpy.types.Operator):
    bl_idname = "rr_builder.render_selected_icons"
    bl_label = "Builder Render Selected Icons"

    def execute(self, context):
        if not ensure_object_mode(context):
            self.report({"ERROR"}, "Could not leave Edit Mode for icon rendering.")
            return {"CANCELLED"}

        settings = context.scene.rr_builder_export_settings
        mesh_objects = get_context_export_roots(context)
        if not mesh_objects:
            item = get_active_queue_item(settings)
            root = queue_item_object(item) if item is not None else None
            if root is not None:
                mesh_objects = [root]

        if not mesh_objects:
            active_name = context.object.name if context.object else "None"
            self.report({"ERROR"}, f"No exportable selected mesh roots. Active object is {active_name}.")
            return {"CANCELLED"}

        return render_icon_objects(mesh_objects, settings, context, "selected")


class RR_OT_apply_icon_outline(bpy.types.Operator):
    bl_idname = "rr_builder.apply_icon_outline"
    bl_label = "Apply Outline to Current Icon"
    bl_description = "Apply the current outline color and width to the existing icon.png"

    def execute(self, context):
        if not ensure_object_mode(context):
            self.report({"ERROR"}, "Could not leave Edit Mode before applying the outline.")
            return {"CANCELLED"}

        settings = context.scene.rr_builder_export_settings
        if not settings.icon_outline_enabled:
            self.report({"ERROR"}, "Enable Outline first.")
            return {"CANCELLED"}

        roots = get_context_export_roots(context)
        if not roots:
            item = get_active_queue_item(settings)
            root = queue_item_object(item) if item is not None else None
            roots = [root] if root is not None else []
        roots = expand_related_export_roots(roots)
        if not roots:
            self.report({"ERROR"}, "Select an exportable object or queue item first.")
            return {"CANCELLED"}

        applied = []
        missing = []
        last_icon_path = ""
        for root in roots:
            icon_path = asset_icon_path(settings, root)
            if not os.path.exists(icon_path):
                missing.append(export_asset_id(root))
                continue
            source_path = icon_outline_source_path(icon_path)
            if not os.path.exists(source_path):
                render_icon(root, icon_path, clamp_icon_size(settings.icon_resolution), settings)
                applied.append(export_asset_id(root))
                last_icon_path = icon_path
                save_icon_framing_to_object(root, settings)
                continue
            if apply_icon_outline_to_png(icon_path, settings):
                applied.append(export_asset_id(root))
                last_icon_path = icon_path
                save_icon_framing_to_object(root, settings)

        if last_icon_path:
            load_image_for_preview(settings, context, last_icon_path)

        if not applied:
            reason = "Render / Update Icon first." if missing else "The current icon needs a transparent background."
            self.report({"ERROR"}, f"No outline was applied. {reason}")
            return {"CANCELLED"}

        suffix = f"; {len(missing)} icon(s) missing" if missing else ""
        self.report({"INFO"}, f"Applied outline to {len(applied)} icon(s){suffix}.")
        return {"FINISHED"}


class RR_OT_use_unity_temp_output(bpy.types.Operator):
    bl_idname = "rr_builder.use_unity_temp_output"
    bl_label = "Use Unity ~Temp"
    bl_description = "Set Output Root to the RandomRealm Unity Assets/~Temp/BlenderBridge folder"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        use_unity_temp_output(settings)
        self.report({"INFO"}, f"Output Root: {settings.output_root}")
        return {"FINISHED"}


class RR_OT_save_layout_snapshot(bpy.types.Operator):
    bl_idname = "rr_builder.save_layout_snapshot"
    bl_label = "Save Layout"
    bl_description = "Store the current world transform of every object in this blend file"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        saved = snapshot_layout(settings)
        self.report({"INFO"}, f"Saved layout snapshot for {saved} objects.")
        return {"FINISHED"}


class RR_OT_restore_layout_snapshot(bpy.types.Operator):
    bl_idname = "rr_builder.restore_layout_snapshot"
    bl_label = "Restore Layout"
    bl_description = "Restore all saved object transforms as one undoable action"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        restored, skipped = restore_layout_snapshot()
        for name in skipped:
            print(f"[RandomRealm Builder Exporter] Could not restore layout snapshot for {name}.")

        if skipped:
            self.report({"WARNING"}, f"Restored {restored} objects; skipped {len(skipped)}. See console.")
            return {"CANCELLED" if restored == 0 else "FINISHED"}

        self.report({"INFO"}, f"Restored layout snapshot for {restored} objects.")
        return {"FINISHED"}


class RR_OT_clear_layout_snapshot(bpy.types.Operator):
    bl_idname = "rr_builder.clear_layout_snapshot"
    bl_label = "Clear Layout"
    bl_description = "Remove saved layout snapshot data from all objects"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        cleared = clear_layout_snapshot(settings)
        self.report({"INFO"}, f"Cleared layout snapshot from {cleared} objects.")
        return {"FINISHED"}


class RR_OT_step_icon_size(bpy.types.Operator):
    bl_idname = "rr_builder.step_icon_size"
    bl_label = "Builder Step Icon Size"
    direction: bpy.props.IntProperty(default=1)

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        settings.icon_resolution = step_icon_size(settings.icon_resolution, self.direction)
        apply_icon_render_resolution(context.scene, settings)
        return {"FINISHED"}


class RR_OT_step_pbr_bake_size(bpy.types.Operator):
    bl_idname = "rr_builder.step_pbr_bake_size"
    bl_label = "Builder Step PBR Bake Size"
    direction: bpy.props.IntProperty(default=1)

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        settings.pbr_bake_resolution = step_pbr_bake_size(settings.pbr_bake_resolution, self.direction)
        return {"FINISHED"}


class RR_OT_toggle_pbr_bake_material(bpy.types.Operator):
    bl_idname = "rr_builder.toggle_pbr_bake_material"
    bl_label = "Toggle Bake Material"
    bl_description = "Include or exclude this material from the next PBR bake"

    material_name: bpy.props.StringProperty(name="Material")

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        name = self.material_name.strip()
        if not name:
            return {"CANCELLED"}

        disabled = pbr_bake_disabled_material_names(settings)
        if name in disabled:
            disabled.remove(name)
        else:
            disabled.add(name)
        set_pbr_bake_disabled_material_names(settings, disabled)
        refresh_rr_helper_ui(context)
        return {"FINISHED"}


class RR_OT_set_pbr_bake_materials(bpy.types.Operator):
    bl_idname = "rr_builder.set_pbr_bake_materials"
    bl_label = "Set Bake Materials"
    bl_description = "Enable or disable all visible PBR bake materials"

    action: bpy.props.EnumProperty(
        name="Action",
        items=(
            ("ALL", "All", "Bake all visible materials"),
            ("NONE", "None", "Bake no visible materials"),
        ),
        default="ALL",
    )

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        materials, _simple_materials = pbr_bake_candidate_materials_for_context(context)
        names = {material.name for material in materials}
        if not names:
            self.report({"WARNING"}, "No bakeable materials in the current selection.")
            return {"CANCELLED"}

        disabled = pbr_bake_disabled_material_names(settings)
        if self.action == "ALL":
            disabled -= names
        else:
            disabled |= names
        set_pbr_bake_disabled_material_names(settings, disabled)
        refresh_rr_helper_ui(context)
        return {"FINISHED"}


class RR_OT_edit_icon_preview_light(bpy.types.Operator):
    bl_idname = "rr_builder.edit_icon_preview_light"
    bl_label = "Edit Preview Light"
    bl_description = "Select this preview light and toggle its detailed controls"

    role: bpy.props.EnumProperty(
        items=(
            ("KEY", "Key", "Edit the key light"),
            ("FILL", "Fill", "Edit the fill light"),
            ("BACK", "Back", "Edit the back light"),
        ),
        default="KEY",
    )

    def execute(self, context):
        if not ensure_object_mode(context):
            self.report({"ERROR"}, "Exit Edit Mode before editing preview lights.")
            return {"CANCELLED"}

        settings = context.scene.rr_builder_export_settings
        spec = icon_light_spec_for_role(self.role)
        if spec is None:
            self.report({"ERROR"}, "Unknown preview light.")
            return {"CANCELLED"}

        previous_role = settings.icon_light_edit_role
        previous_root = icon_light_editor_root(settings)
        if previous_role != "NONE":
            try:
                capture_icon_light_editor_scene_state(settings, context)
            except Exception as exc:
                print(f"[RR Helper] Could not capture the open light editor while toggling: {exc}")
        if previous_role == self.role and previous_root is not None:
            settings.icon_light_edit_role = "NONE"
            settings.icon_light_edit_root_name = ""
            self.report({"INFO"}, f"{spec['label']} controls closed; changes saved.")
            return {"FINISHED"}

        root = previous_root or get_current_framing_root(context, settings)
        if root is None:
            active_name = context.object.name if context.object else "None"
            self.report({"ERROR"}, f"No queue item or exportable selected root. Active object is {active_name}.")
            return {"CANCELLED"}

        if settings.icon_framing_adjusting:
            copy_settings_to_queue_item(settings, get_active_queue_item(settings))
            save_icon_framing_to_object(root, settings)
            save_icon_light_transforms_to_object(root, context.scene)
            settings.icon_framing_confirm_requested = False
            settings.icon_framing_adjusting = False
            set_view3d_to_perspective(context)

        try:
            _camera, _lights, state = prepare_icon_light_editor_objects(root, settings, context.scene, spec)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not prepare preview lights: {exc}")
            return {"CANCELLED"}

        light = bpy.data.objects.get(spec["name"])
        if light is None or light.type != "LIGHT":
            self.report({"ERROR"}, f"Could not find {spec['label']}.")
            return {"CANCELLED"}

        settings.icon_light_edit_role = self.role
        settings.icon_light_edit_root_name = root.name
        ensure_icon_light_focus(light, state)
        sync_icon_light_editor_from_light(settings, light, root, state)

        bpy.ops.object.select_all(action="DESELECT")
        light.hide_select = False
        light.hide_viewport = False
        light.select_set(True)
        context.view_layer.objects.active = light
        context.view_layer.update()
        self.report({"INFO"}, f"Editing {spec['label']} for {export_asset_id(root)}.")
        return {"FINISHED"}


class RR_OT_save_icon_preview_light(bpy.types.Operator):
    bl_idname = "rr_builder.save_icon_preview_light"
    bl_label = "Save Preview Light"
    bl_description = "Capture viewport moves and save this light for the current asset"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        spec = icon_light_spec_for_role(settings.icon_light_edit_role)
        if spec is None or not capture_icon_light_editor_scene_state(settings, context):
            self.report({"ERROR"}, "No preview light is open for editing.")
            return {"CANCELLED"}
        root = icon_light_editor_root(settings)
        self.report({"INFO"}, f"Saved {spec['label']} for {export_asset_id(root)}.")
        return {"FINISHED"}


class RR_OT_reset_icon_preview_light(bpy.types.Operator):
    bl_idname = "rr_builder.reset_icon_preview_light"
    bl_label = "Reset Preview Light"
    bl_description = "Reset only this preview light for the current asset"
    bl_options = {"UNDO"}

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        spec = icon_light_spec_for_role(settings.icon_light_edit_role)
        light = bpy.data.objects.get(spec["name"]) if spec is not None else None
        root = icon_light_editor_root(settings) or icon_light_root_from_light(light)
        if spec is None or light is None or root is None:
            self.report({"ERROR"}, "No preview light is open for editing.")
            return {"CANCELLED"}

        try:
            state = get_icon_camera_state(root, settings)
            set_default_icon_light_transform(light, state, spec)
            set_default_icon_light_settings(light, state, spec)
            light.data.energy = icon_light_energy(spec, settings)
            light["rr_icon_preview_light_target"] = root.name
            light["rr_icon_preview_transform_initialized"] = True
            save_icon_light_transform_to_object(root, light, spec)
            sync_icon_light_editor_from_light(settings, light, root, state)
            context.view_layer.update()
        except Exception as exc:
            self.report({"ERROR"}, f"Could not reset {spec['label']}: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Reset {spec['label']} for {export_asset_id(root)}.")
        return {"FINISHED"}


class RR_OT_reset_icon_framing(bpy.types.Operator):
    bl_idname = "rr_builder.reset_icon_framing"
    bl_label = "Reset Thumbnail Framing"
    bl_description = "Reset the thumbnail camera framing while preserving custom preview light positions"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        item = get_active_queue_item(settings)
        root = get_current_framing_root(context, settings)
        if root is None:
            active_name = context.object.name if context.object else "None"
            self.report({"ERROR"}, f"No queue item or exportable selected root. Active object is {active_name}.")
            return {"CANCELLED"}

        reset_icon_framing(settings)
        copy_settings_to_queue_item(settings, item)
        try:
            save_icon_framing_to_object(root, settings)
            ensure_icon_preview_objects(root, settings, context.scene)
            save_icon_light_transforms_to_object(root, context.scene)
            set_view3d_to_camera(context)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not reset thumbnail framing: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, "Thumbnail camera framing reset; custom lights preserved.")
        return {"FINISHED"}


class RR_OT_confirm_icon_framing(bpy.types.Operator):
    bl_idname = "rr_builder.confirm_icon_framing"
    bl_label = "Confirm Thumbnail Framing"
    bl_description = "Save the current thumbnail camera framing and exit Adjust mode without rendering"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        item = get_active_queue_item(settings)
        root = get_current_framing_root(context, settings)
        if root is not None:
            copy_settings_to_queue_item(settings, item)
            save_icon_framing_to_object(root, settings)
            save_icon_light_transforms_to_object(root, context.scene)
        settings.icon_framing_confirm_requested = False
        settings.icon_framing_adjusting = False
        set_view3d_to_perspective(context)
        if context.area is not None:
            context.area.header_text_set(None)
        if context.screen is not None:
            for area in context.screen.areas:
                area.tag_redraw()
        self.report({"INFO"}, "Thumbnail framing confirmed.")
        return {"FINISHED"}


class RR_OT_adjust_icon_framing(bpy.types.Operator):
    bl_idname = "rr_builder.adjust_icon_framing"
    bl_label = "Adjust Thumbnail Framing"
    bl_description = "Mouse wheel zooms; middle-mouse drag rotates the thumbnail camera; Shift + middle-mouse drag pans. Enter/Esc/Numpad 0/right-click confirms"

    _root_name = ""
    _last_mouse = None
    _drag_mode = ""
    _original_camera_name = ""
    _original_zoom = 1.0
    _original_offset_x = 0.0
    _original_offset_y = 0.0
    _original_yaw = 0.0
    _original_pitch = 0.0
    _hide_states = None

    def _selected_root(self):
        return bpy.data.objects.get(self._root_name)

    def _update_preview(self, context):
        root = self._selected_root()
        if root is None:
            return False

        settings = context.scene.rr_builder_export_settings
        apply_icon_render_resolution(context.scene, settings)
        ensure_icon_preview_objects(root, settings, context.scene)
        set_view3d_to_camera(context)
        return True

    def _restore_view(self, context):
        settings = context.scene.rr_builder_export_settings
        settings.icon_framing_adjusting = False
        settings.icon_framing_confirm_requested = False
        if self._hide_states is not None:
            restore_mesh_hide_states(self._hide_states)
            self._hide_states = None
        original_camera = bpy.data.objects.get(self._original_camera_name)
        if original_camera is not None:
            context.scene.camera = original_camera
        set_view3d_to_perspective(context)
        if context.area is not None:
            context.area.header_text_set(None)
        if context.screen is not None:
            for area in context.screen.areas:
                area.tag_redraw()

    def _confirm(self, context):
        settings = context.scene.rr_builder_export_settings
        copy_settings_to_queue_item(settings, get_active_queue_item(settings))
        root = self._selected_root()
        save_icon_framing_to_object(root, settings)
        save_icon_light_transforms_to_object(root, context.scene)
        self._restore_view(context)
        self.report({"INFO"}, "Thumbnail framing confirmed.")
        return {"FINISHED"}

    @staticmethod
    def _region_contains_mouse(region, mouse_x, mouse_y):
        return region.x <= mouse_x < region.x + region.width and region.y <= mouse_y < region.y + region.height

    @classmethod
    def _view3d_region_under_mouse(cls, context, event):
        if context.window is None or context.window.screen is None:
            return None

        mouse_x = event.mouse_x
        mouse_y = event.mouse_y
        for area in context.window.screen.areas:
            if area.type != "VIEW_3D":
                continue

            if not (area.x <= mouse_x < area.x + area.width and area.y <= mouse_y < area.y + area.height):
                continue

            for region in area.regions:
                if region.type == "WINDOW":
                    continue
                if cls._region_contains_mouse(region, mouse_x, mouse_y):
                    return region

            for region in area.regions:
                if region.type != "WINDOW":
                    continue
                if cls._region_contains_mouse(region, mouse_x, mouse_y):
                    return region
        return None

    @classmethod
    def _view3d_window_region_under_mouse(cls, context, event):
        region = cls._view3d_region_under_mouse(context, event)
        return region if region is not None and region.type == "WINDOW" else None

    @classmethod
    def _event_is_in_view3d(cls, context, event):
        return cls._view3d_window_region_under_mouse(context, event) is not None

    def invoke(self, context, event):
        if not ensure_object_mode(context):
            self.report({"ERROR"}, "Exit Edit Mode before adjusting thumbnail framing.")
            return {"CANCELLED"}

        settings = context.scene.rr_builder_export_settings
        autosave_icon_preview_lights(context.scene)
        root = get_current_framing_root(context, settings)
        if root is None:
            active_name = context.object.name if context.object else "None"
            self.report({"ERROR"}, f"No exportable selected mesh roots. Active object is {active_name}.")
            return {"CANCELLED"}

        self._root_name = root.name
        self._original_camera_name = context.scene.camera.name if context.scene.camera else ""
        self._original_zoom = settings.icon_zoom
        self._original_offset_x = settings.icon_offset_x
        self._original_offset_y = settings.icon_offset_y
        self._original_yaw = settings.icon_view_yaw
        self._original_pitch = settings.icon_view_pitch
        self._last_mouse = (event.mouse_region_x, event.mouse_region_y)
        self._drag_mode = ""
        self._hide_states = capture_mesh_hide_states(context.scene)
        isolate_preview_meshes(root, context.scene, context)
        apply_icon_render_resolution(context.scene, settings)

        preview_error = ""
        try:
            preview_ready = self._update_preview(context)
        except Exception as exc:
            preview_ready = False
            preview_error = str(exc)
            self.report({"ERROR"}, f"Could not create thumbnail preview: {preview_error}")
        if not preview_ready:
            if not preview_error:
                self.report({"ERROR"}, "Could not create thumbnail preview camera.")
            self._restore_view(context)
            return {"CANCELLED"}

        if context.area is not None:
            context.area.header_text_set("Thumbnail framing: wheel zooms, MMB rotates, Shift+MMB pans, Enter/Esc/Numpad 0/right-click confirms")

        settings.icon_framing_adjusting = True
        settings.icon_framing_confirm_requested = False
        if context.screen is not None:
            for area in context.screen.areas:
                area.tag_redraw()

        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        settings = context.scene.rr_builder_export_settings

        if not settings.icon_framing_adjusting:
            self._restore_view(context)
            return {"FINISHED"}

        if settings.icon_framing_confirm_requested:
            settings.icon_framing_confirm_requested = False
            return self._confirm(context)

        if event.type in {"ESC", "RIGHTMOUSE"}:
            return self._confirm(context)

        if event.type in {"RET", "NUMPAD_ENTER", "NUMPAD_0"} and event.value == "PRESS":
            return self._confirm(context)

        if not self._event_is_in_view3d(context, event):
            if self._drag_mode and event.type in {"MOUSEMOVE", "MIDDLEMOUSE"}:
                self._drag_mode = ""
            return {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE":
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE" and not self._drag_mode:
            return {"PASS_THROUGH"}

        if event.type in {"LEFT_ARROW", "RIGHT_ARROW"} and event.value == "PRESS" and len(settings.export_queue) > 0:
            current_item = get_active_queue_item(settings)
            copy_settings_to_queue_item(settings, current_item)
            current_root = self._selected_root()
            save_icon_framing_to_object(current_root, settings)
            save_icon_light_transforms_to_object(current_root, context.scene)
            direction = -1 if event.type == "LEFT_ARROW" else 1
            settings.queue_active_index = (settings.queue_active_index + direction) % len(settings.export_queue)
            item = get_active_queue_item(settings)
            root = queue_item_icon_preview_root(item, context, remember=True)
            if root is not None:
                prepare_framing_for_root(root, settings, item)
                self._root_name = root.name
                isolate_preview_meshes(root, context.scene, context)
                self._update_preview(context)
            return {"RUNNING_MODAL"}

        if event.type == "WHEELUPMOUSE":
            settings.icon_zoom = clamp_float(settings.icon_zoom * 1.1, ICON_ZOOM_MIN, ICON_ZOOM_MAX)
            self._update_preview(context)
            return {"RUNNING_MODAL"}

        if event.type == "WHEELDOWNMOUSE":
            settings.icon_zoom = clamp_float(settings.icon_zoom / 1.1, ICON_ZOOM_MIN, ICON_ZOOM_MAX)
            self._update_preview(context)
            return {"RUNNING_MODAL"}

        if event.type == "MIDDLEMOUSE":
            if event.value == "PRESS":
                if getattr(event, "shift", False):
                    self._drag_mode = "PAN"
                else:
                    self._drag_mode = "ROTATE"
                self._last_mouse = (event.mouse_region_x, event.mouse_region_y)
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                self._drag_mode = ""
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._drag_mode and self._last_mouse is not None:
            root = self._selected_root()
            if root is None:
                self._restore_view(context)
                return {"CANCELLED"}

            last_x, last_y = self._last_mouse
            dx = event.mouse_region_x - last_x
            dy = event.mouse_region_y - last_y
            self._last_mouse = (event.mouse_region_x, event.mouse_region_y)

            if self._drag_mode == "ROTATE":
                settings.icon_view_yaw -= dx * 0.3
                settings.icon_view_pitch = clamp_float(settings.icon_view_pitch - dy * 0.3, ICON_PITCH_MIN, ICON_PITCH_MAX)
                self._update_preview(context)
                return {"RUNNING_MODAL"}

            state = get_icon_camera_state(root, settings)
            region = self._view3d_window_region_under_mouse(context, event) or context.region
            width = max(getattr(region, "width", 1), 1)
            height = max(getattr(region, "height", 1), 1)
            settings.icon_offset_x -= (dx / width) * state["ortho_scale"]
            settings.icon_offset_y -= (dy / height) * state["ortho_scale"]
            self._update_preview(context)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}


class RR_OT_apply_latest_texture_packages(bpy.types.Operator):
    bl_idname = "rr_builder.apply_latest_texture_packages"
    bl_label = "Apply Latest Texture Packages"
    bl_description = "Apply the newest package for each material texture slot from textures/_codex_packages"

    def execute(self, context):
        root_dir = texture_package_root_dir()
        packages = latest_texture_packages(root_dir)
        if not packages:
            self.report({"WARNING"}, "No texture packages found in textures/_codex_packages.")
            return {"CANCELLED"}

        applied = []
        failed = []
        for package in packages:
            try:
                result = apply_texture_package(package)
                write_texture_package_applied_marker(package, result)
                applied.append(result)
            except Exception as exc:
                manifest = package.get("manifest", {})
                material_name = manifest.get("material", "<unknown material>")
                failed.append(f"{material_name}: {exc}")

        for item in applied:
            action = "updated" if item["changed"] else "already current"
            print(
                "[RandomRealm Builder Exporter]",
                f"{item['material']} {item['role']} {action}: {item['destination']}",
            )
        for item in failed:
            print("[RandomRealm Builder Exporter]", item)

        if failed:
            self.report({"ERROR"}, f"Applied {len(applied)} texture package(s), failed {len(failed)}. See console.")
            return {"CANCELLED"} if not applied else {"FINISHED"}

        self.report({"INFO"}, f"Applied {len(applied)} latest texture package(s).")
        return {"FINISHED"}


PBR_FRAMEWORK_ROLE_ITEMS = tuple(
    (role["key"], role["label"], f"Select {role['label']} image texture as the native bake target")
    for role in PBR_BAKE_ROLES
)


class RR_OT_create_pbr_framework(bpy.types.Operator):
    bl_idname = "rr_builder.create_pbr_framework"
    bl_label = "Create PBR Targets"
    bl_description = "Create manual PBR image texture targets without baking"

    connect_maps: bpy.props.BoolProperty(default=False, options={"HIDDEN"})

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        try:
            result = create_manual_pbr_framework(context, settings, connect_maps=self.connect_maps)
        except Exception as exc:
            settings.pbr_framework_status = f"Failed: {exc}"
            self.report({"ERROR"}, settings.pbr_framework_status)
            return {"CANCELLED"}

        if self.connect_maps:
            settings.pbr_framework_status = f"Connected {result['material_count']} material(s)."
        else:
            settings.pbr_framework_status = f"Created {result['role_count']} target(s) for {result['material_count']} material(s)."
        self.report({"INFO"}, settings.pbr_framework_status)
        return {"FINISHED"}


class RR_OT_save_pbr_framework_images(bpy.types.Operator):
    bl_idname = "rr_builder.save_pbr_framework_images"
    bl_label = "Save PBR Images"
    bl_description = "Save selected PBR target images into organized material folders"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        try:
            result = save_manual_pbr_framework_images(context, settings)
        except Exception as exc:
            settings.pbr_framework_status = f"Failed: {exc}"
            self.report({"ERROR"}, settings.pbr_framework_status)
            return {"CANCELLED"}

        message = f"Saved {result['saved_count']} image(s)"
        if result["skipped_count"] > 0:
            message += f"; skipped {result['skipped_count']} missing"
        settings.pbr_framework_status = message
        self.report({"INFO"}, settings.pbr_framework_status)
        return {"FINISHED"}


class RR_OT_prepare_pbr_framework_bake_target(bpy.types.Operator):
    bl_idname = "rr_builder.prepare_pbr_framework_bake_target"
    bl_label = "Prepare Bake Target"
    bl_description = "Select the next unbaked PBR image target and configure Blender's native Bake settings"

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        try:
            result = prepare_next_pbr_framework_bake_target(context, settings)
        except Exception as exc:
            settings.pbr_framework_status = f"Failed: {exc}"
            self.report({"ERROR"}, settings.pbr_framework_status)
            return {"CANCELLED"}

        message = (
            f"Prepared {result['role_label']} ({result['bake_type']}) "
            f"for {result['material_count']} material(s)."
        )
        if result["all_targets_have_content"]:
            message = f"All selected targets have content; {message}"
        settings.pbr_framework_status = message
        self.report({"INFO"}, settings.pbr_framework_status)
        return {"FINISHED"}


class RR_OT_set_pbr_framework_material_selection(bpy.types.Operator):
    bl_idname = "rr_builder.set_pbr_framework_material_selection"
    bl_label = "Set PBR Material Selection"
    bl_description = "Choose which materials the manual PBR target tools operate on"

    material_name: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        material_name = (self.material_name or "").strip()
        available_names = {material.name for material in pbr_framework_materials_for_context(context)}
        if not material_name or material_name not in available_names:
            settings.pbr_framework_status = "Failed: Select a material."
            self.report({"ERROR"}, settings.pbr_framework_status)
            return {"CANCELLED"}

        if getattr(settings, "pbr_framework_use_material_filter", False):
            names = pbr_framework_selected_material_names(settings) & available_names
        else:
            names = set(available_names)

        if material_name in names:
            names.discard(material_name)
        else:
            names.add(material_name)
        settings.pbr_framework_use_material_filter = True
        set_pbr_framework_selected_material_names(settings, names)
        settings.pbr_framework_status = f"Selected {len(names)}/{len(available_names)} material(s)."
        return {"FINISHED"}


class RR_OT_select_pbr_bake_target(bpy.types.Operator):
    bl_idname = "rr_builder.select_pbr_bake_target"
    bl_label = "Toggle PBR Target"
    bl_description = "Toggle one PBR image texture target for manual frame creation"

    role_key: bpy.props.EnumProperty(items=PBR_FRAMEWORK_ROLE_ITEMS)

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if getattr(settings, "pbr_framework_use_role_filter", False):
            keys = pbr_framework_selected_role_keys(settings)
        else:
            keys = pbr_framework_role_keys()
        if self.role_key in keys:
            keys.discard(self.role_key)
        else:
            keys.add(self.role_key)
        settings.pbr_framework_use_role_filter = True
        set_pbr_framework_selected_role_keys(settings, keys)
        role_label = next((role["label"] for role in PBR_BAKE_ROLES if role["key"] == self.role_key), self.role_key)
        action = "Enabled" if self.role_key in keys else "Disabled"
        settings.pbr_framework_status = f"{action} {role_label}; {len(keys)}/{len(PBR_BAKE_ROLES)} map(s)."
        return {"FINISHED"}


class RR_OT_bake_selected_pbr(bpy.types.Operator):
    bl_idname = "rr_builder.bake_selected_pbr"
    bl_label = "Bake PBR"
    bl_description = "Bake selected procedural materials to Base Color, Roughness, Metallic, and Normal maps"

    _timer = None
    _started = False

    def run_bake(self, context):
        settings = context.scene.rr_builder_export_settings
        set_pbr_bake_runtime_pending(False)
        set_pbr_bake_runtime_active(True)
        settings.pbr_bake_running = True
        settings.pbr_bake_progress = 0.0
        settings.pbr_bake_status = "PBR Bake 0% - Starting"
        refresh_rr_helper_ui(context)
        try:
            result = bake_selected_to_pbr(context, settings, self.report)
        except Exception as exc:
            settings.pbr_bake_last_summary = f"Failed: {exc}"
            settings.pbr_bake_last_output_dir = ""
            settings.pbr_bake_last_files = ""
            settings.pbr_bake_status = settings.pbr_bake_last_summary
            settings.pbr_bake_progress = 0.0
            settings.pbr_bake_running = False
            set_pbr_bake_runtime_pending(False)
            set_pbr_bake_runtime_active(False)
            refresh_rr_helper_ui(context)
            self.report({"ERROR"}, settings.pbr_bake_last_summary)
            return {"CANCELLED"}

        skipped = int(result.get("skipped_material_count", 0))
        disabled = int(result.get("disabled_material_count", 0))
        if result["material_count"] <= 0 and disabled > 0:
            settings.pbr_bake_last_summary = "No checked material(s) to bake."
        elif result["material_count"] <= 0 and skipped > 0:
            settings.pbr_bake_last_summary = f"Skipped {skipped} simple material(s)."
        else:
            settings.pbr_bake_last_summary = (
                f"Baked {result['material_count']} material(s), "
                f"{result['image_count']} map(s)"
            )
            if skipped > 0:
                settings.pbr_bake_last_summary += f"; skipped {skipped} simple"
            if disabled > 0:
                settings.pbr_bake_last_summary += f"; skipped {disabled} unchecked"
        output_dir = result.get("output_dir", "") or result.get("output_root", "")
        output_files = list(result.get("files", []) or [])
        settings.pbr_bake_last_output_dir = output_dir
        settings.pbr_bake_last_files = "\n".join(output_files)
        settings.pbr_bake_status = settings.pbr_bake_last_summary
        settings.pbr_bake_progress = 1.0
        settings.pbr_bake_running = False
        set_pbr_bake_runtime_pending(False)
        set_pbr_bake_runtime_active(False)
        refresh_rr_helper_ui(context)
        self.report({"INFO"}, settings.pbr_bake_last_summary)
        return {"FINISHED"}

    def invoke(self, context, event):
        settings = context.scene.rr_builder_export_settings
        if settings.pbr_bake_running:
            self.report({"WARNING"}, "PBR bake is already running.")
            return {"CANCELLED"}

        set_pbr_bake_runtime_pending(True)
        settings.pbr_bake_running = True
        settings.pbr_bake_progress = 0.0
        settings.pbr_bake_status = "PBR Bake 0% - Starting"
        refresh_rr_helper_ui(context)
        self.report({"INFO"}, settings.pbr_bake_status)

        self._started = False
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"} and not self._started:
            self.cancel(context)
            return {"CANCELLED"}

        if event.type != "TIMER" or (self._timer is not None and getattr(event, "timer", None) != self._timer):
            return {"PASS_THROUGH"}

        self._started = True
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

        return self.run_bake(context)

    def execute(self, context):
        return self.run_bake(context)

    def cancel(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        settings = context.scene.rr_builder_export_settings
        settings.pbr_bake_running = False
        settings.pbr_bake_status = "Bake cancelled"
        settings.pbr_bake_progress = 0.0
        set_pbr_bake_runtime_pending(False)
        set_pbr_bake_runtime_active(False)
        refresh_rr_helper_ui(context)


class RR_OT_apply_modeling_origin_point(bpy.types.Operator):
    bl_idname = "rr_builder.apply_modeling_origin_point"
    bl_label = "Apply Origin"
    bl_description = "Move mesh or curve origins to selected elements, or mesh origins to automatic points, without moving visible geometry"
    bl_options = {"REGISTER", "UNDO"}

    mode: bpy.props.EnumProperty(
        name="Mode",
        items=(
            ("SELECTION", "Selection", "Use the center of selected mesh elements or curve points in Edit Mode"),
            ("BOTTOM", "Bottom", "Use the center of the lowest downward-facing face"),
        ),
        default="SELECTION",
        options={"SKIP_SAVE"},
    )

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        try:
            applied = apply_modeling_origin(context, settings, mode=self.mode)
        except Exception as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Applied origin to {applied} object(s).")
        return {"FINISHED"}


class RR_OT_refresh_addon(bpy.types.Operator):
    bl_idname = "rr_helper.refresh_addon"
    bl_label = "Refresh Add-on"
    bl_description = "Reload RR Helper after its Python files have changed"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        settings = getattr(getattr(context, "scene", None), "rr_builder_export_settings", None)
        adjusting = bool(getattr(settings, "icon_framing_adjusting", False)) if settings is not None else False
        return RR_ADDON_REFRESH_LAST_STATE and not RR_ADDON_REFRESH_PENDING and not adjusting

    def execute(self, context):
        global RR_ADDON_REFRESH_PENDING, RR_ADDON_REFRESH_LAST_ERROR

        settings = getattr(getattr(context, "scene", None), "rr_builder_export_settings", None)
        if settings is not None and settings.icon_framing_adjusting:
            self.report({"WARNING"}, "Confirm thumbnail framing before refreshing RR Helper.")
            return {"CANCELLED"}

        if not rr_addon_source_changed():
            self.report({"INFO"}, "RR Helper is already current.")
            return {"CANCELLED"}
        try:
            validate_rr_addon_sources()
        except Exception as exc:
            RR_ADDON_REFRESH_LAST_ERROR = str(exc)
            self.report({"ERROR"}, f"Refresh blocked by a Python error: {exc}")
            return {"CANCELLED"}

        RR_ADDON_REFRESH_PENDING = True
        RR_ADDON_REFRESH_LAST_ERROR = ""
        try:
            if not bpy.app.timers.is_registered(rr_addon_reload_deferred):
                bpy.app.timers.register(rr_addon_reload_deferred, first_interval=0.1)
        except Exception as exc:
            RR_ADDON_REFRESH_PENDING = False
            RR_ADDON_REFRESH_LAST_ERROR = str(exc)
            self.report({"ERROR"}, f"Could not schedule refresh: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, "Refreshing RR Helper...")
        return {"FINISHED"}


class RR_OT_set_ui_page(bpy.types.Operator):
    bl_idname = "rr_builder.set_ui_page"
    bl_label = "Set RandomRealm Page"
    bl_description = "Switch the RandomRealm builder panel page"
    bl_options = {"INTERNAL"}

    page: bpy.props.EnumProperty(
        items=(
            ("EXPORTER", "Export", "Export queue, output, and export actions"),
            ("BAKE", "Bake", "Bake selected procedural materials to PBR maps"),
            ("MODELING", "Modeling", "Modeling helpers for origins and edit selections"),
            ("ICON", "Icon", "Thumbnail framing and reference images"),
            ("LAYOUT", "Layout", "Layout snapshot tools"),
            ("ANIMATION", "Animation", "Character animation sync with Unity"),
            ("TEXTURES", "Textures", "Texture package tools"),
            ("OBJECTS", "Objects", "Legacy export-group page"),
        ),
        default="EXPORTER",
    )

    def execute(self, context):
        context.scene.rr_builder_export_settings.ui_page = self.page
        return {"FINISHED"}


class RR_OT_toggle_ui_flag(bpy.types.Operator):
    bl_idname = "rr_builder.toggle_ui_flag"
    bl_label = "Toggle UI Section"
    bl_description = "Expand or collapse a UI section"
    bl_options = {"INTERNAL"}

    property_name: bpy.props.StringProperty()

    def execute(self, context):
        settings = context.scene.rr_builder_export_settings
        if not self.property_name or not hasattr(settings, self.property_name):
            return {"CANCELLED"}
        setattr(settings, self.property_name, not bool(getattr(settings, self.property_name)))
        return {"FINISHED"}


class RR_OT_set_export_section(bpy.types.Operator):
    bl_idname = "rr_builder.set_export_section"
    bl_label = "Set Export Section"
    bl_description = "Switch the active Export section"
    bl_options = {"INTERNAL"}

    section: bpy.props.EnumProperty(
        items=(
            ("GROUP", "Group", "Show group controls"),
            ("QUEUE", "Queue", "Show export queue controls"),
            ("ICON", "Icon", "Show icon rendering controls"),
            ("TEXTURES", "Textures", "Show texture package controls"),
        ),
        default="QUEUE",
    )

    def execute(self, context):
        context.scene.rr_builder_export_settings.export_active_section = self.section
        return {"FINISHED"}


class RR_OT_open_animation_blend(bpy.types.Operator):
    bl_idname = "rr_helper.open_animation_blend"
    bl_label = "Open Animation.blend"
    bl_description = "Open the RandomRealm character Animation.blend source file"

    def execute(self, _context):
        if not os.path.exists(RR_ANIMATION_BLEND_PATH):
            self.report({"ERROR"}, f"Animation.blend was not found: {RR_ANIMATION_BLEND_PATH}")
            return {"CANCELLED"}

        rr_helper_open_path(RR_ANIMATION_BLEND_PATH)
        return {"FINISHED"}


class RR_OT_open_unity_animation_import_folder(bpy.types.Operator):
    bl_idname = "rr_helper.open_unity_animation_import_folder"
    bl_label = "Open Unity Animation Folder"
    bl_description = "Open the Unity folder that receives Blender animation sync imports"

    def execute(self, _context):
        rr_helper_reveal_folder(RR_UNITY_ANIMATION_IMPORT_FOLDER)
        return {"FINISHED"}


class RR_OT_request_unity_animation_import(bpy.types.Operator):
    bl_idname = "rr_helper.request_unity_animation_import"
    bl_label = "Request Unity Import"
    bl_description = "Ask the Unity editor to import or refresh Animation.blend"

    def execute(self, _context):
        if not os.path.exists(RR_ANIMATION_BLEND_PATH):
            self.report({"ERROR"}, f"Animation.blend was not found: {RR_ANIMATION_BLEND_PATH}")
            return {"CANCELLED"}

        os.makedirs(RR_ANIMATION_SYNC_TEMP, exist_ok=True)
        payload = {
            "version": 1,
            "createdUtc": datetime.now(timezone.utc).isoformat(),
            "sourcePath": RR_ANIMATION_BLEND_PATH,
            "sourceFolder": RR_ANIMATION_SOURCE_FOLDER,
            "unityProjectRoot": UNITY_PROJECT_ROOT,
            "note": "Unity watches this file and refreshes the Blender animation import.",
        }
        with open(RR_ANIMATION_IMPORT_REQUEST_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=4)

        self.report({"INFO"}, "Unity animation import requested.")
        return {"FINISHED"}


class RR_PT_builder_exporter(bpy.types.Panel):
    bl_label = "RR Helper"
    bl_idname = "RR_PT_builder_exporter"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RandomRealm"

    def draw(self, context):
        global OBJECT_MANAGER_REPAIR_ALLOWED, _EXPORT_IDENTITY_LOOKUP_CACHE

        previous_repair_state = OBJECT_MANAGER_REPAIR_ALLOWED
        previous_lookup_cache = _EXPORT_IDENTITY_LOOKUP_CACHE
        OBJECT_MANAGER_REPAIR_ALLOWED = False
        # Drawing is read-only; reuse lookups only for this draw, never across frames.
        _EXPORT_IDENTITY_LOOKUP_CACHE = {}
        try:
            settings = context.scene.rr_builder_export_settings
            layout = self.layout

            active_page = settings.ui_page if settings.ui_page in {"EXPORTER", "BAKE", "MODELING", "LAYOUT", "ANIMATION"} else "EXPORTER"
            self.draw_page_tabs(layout, active_page)

            if active_page == "EXPORTER":
                self.draw_exporter_page(layout, context, settings)
            elif active_page == "BAKE":
                self.draw_bake_page(layout, context, settings)
            elif active_page == "MODELING":
                self.draw_modeling_page(layout, context, settings)
            elif active_page == "LAYOUT":
                self.draw_layout_page(layout, settings)
            elif active_page == "ANIMATION":
                self.draw_animation_page(layout)
        finally:
            _EXPORT_IDENTITY_LOOKUP_CACHE = previous_lookup_cache
            OBJECT_MANAGER_REPAIR_ALLOWED = previous_repair_state

    def draw_animation_page(self, layout):
        box = layout.box()
        box.label(text="Animation Sync")
        animation_ready = os.path.exists(RR_ANIMATION_BLEND_PATH)
        state_icon = "CHECKMARK" if animation_ready else "ERROR"
        state_text = "Animation.blend ready" if animation_ready else "Animation.blend missing"
        box.label(text=state_text, icon=state_icon)
        row = box.row(align=True)
        row.operator("rr_helper.open_animation_blend", text="Open Blend", icon="FILE_BLEND")
        row.operator("rr_helper.request_unity_animation_import", text="Import in Unity", icon="IMPORT")
        box.operator("rr_helper.open_unity_animation_import_folder", text="Open Unity Import Folder", icon="FILE_FOLDER")

    def draw_page_tabs(self, layout, active_page):
        tab_box = layout.box()
        first_row = tab_box.row(align=True)
        self.draw_page_tab(first_row, active_page, "EXPORTER", "Export", "EXPORT")
        self.draw_page_tab(first_row, active_page, "BAKE", "Bake", "RENDER_RESULT")
        self.draw_page_tab(first_row, active_page, "MODELING", "Modeling", "MESH_DATA")
        second_row = tab_box.row(align=True)
        self.draw_page_tab(second_row, active_page, "LAYOUT", "Layout", "FILE_TICK")
        self.draw_page_tab(second_row, active_page, "ANIMATION", "Animation", "ACTION")
        if RR_ADDON_REFRESH_PENDING or RR_ADDON_REFRESH_LAST_STATE:
            refresh_row = tab_box.row(align=True)
            refresh_row.alert = bool(RR_ADDON_REFRESH_LAST_ERROR)
            refresh_row.enabled = not RR_ADDON_REFRESH_PENDING
            refresh_row.operator(
                "rr_helper.refresh_addon",
                text="Refreshing RR Helper..." if RR_ADDON_REFRESH_PENDING else "Refresh Add-on",
                icon="FILE_REFRESH" if not RR_ADDON_REFRESH_LAST_ERROR else "ERROR",
            )

    def draw_page_tab(self, row, active_page, page, text, icon):
        operator = row.operator(
            "rr_builder.set_ui_page",
            text=text,
            icon=icon,
            depress=active_page == page,
        )
        operator.page = page

    def draw_fold_panel(self, layout, settings, property_name, text):
        panel_prop = getattr(layout, "panel_prop", None)
        if callable(panel_prop):
            try:
                header, panel = panel_prop(settings, property_name)
                if header is not None:
                    try:
                        header.alignment = "LEFT"
                    except Exception:
                        pass
                    header.label(text=text)
                return panel
            except Exception:
                pass

        expanded = bool(getattr(settings, property_name, False))
        row = layout.row(align=True)
        try:
            row.alignment = "LEFT"
        except Exception:
            pass
        operator = row.operator(
            "rr_builder.toggle_ui_flag",
            text=text,
            icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
            emboss=False,
        )
        operator.property_name = property_name
        return layout.column(align=False) if expanded else None

    def draw_exporter_page(self, layout, context, settings):
        self.draw_export_section_filter(layout, settings)

        if settings.show_export_queue_section:
            self.draw_export_queue_box(layout, settings)

        if settings.show_export_group_section:
            assembly_root = selected_object_manager_assembly_root(context)
            if assembly_root is None and context.selected_objects:
                assembly_root = object_manager_group_from_settings(settings)
            create_members = selected_objects_for_object_manager_assembly(context)
            if assembly_root is not None or len(create_members) >= 2:
                self.draw_export_group_box(layout, context, settings, assembly_root, create_members)

        if settings.show_export_icon_section:
            self.draw_icon_page(layout, context, settings)

        if settings.show_export_textures_section:
            self.draw_texture_page(layout)

    def draw_export_section_filter(self, layout, settings):
        panel = self.draw_fold_panel(layout, settings, "export_sections_expanded", "Sections")
        if panel is None:
            return

        row = panel.row(align=True)
        self.draw_export_section_toggle(row, settings, "show_export_queue_section", "Queue")
        self.draw_export_section_toggle(row, settings, "show_export_group_section", "Group")
        row = panel.row(align=True)
        self.draw_export_section_toggle(row, settings, "show_export_icon_section", "Icon")
        self.draw_export_section_toggle(row, settings, "show_export_textures_section", "Textures")

    def draw_export_section_tab(self, row, settings, section, text):
        operator = row.operator(
            "rr_builder.set_export_section",
            text=text,
            depress=settings.export_active_section == section,
        )
        operator.section = section

    def draw_export_section_toggle(self, row, settings, property_name, text):
        operator = row.operator(
            "rr_builder.toggle_ui_flag",
            text=text,
            depress=bool(getattr(settings, property_name, False)),
        )
        operator.property_name = property_name

    def draw_export_output_row(self, layout, settings):
        row = layout.row(align=True)
        row.prop(settings, "output_root", text="")

    def draw_export_queue_box(self, layout, settings):
        queue_box = layout.box()
        queue_box.label(text="Export Queue")
        row = queue_box.row(align=True)
        row.operator("rr_builder.queue_selected", text="Add Selection to Queue", icon="ADD")
        row.operator("rr_builder.remove_queue_item", text="", icon="REMOVE")
        row.operator("rr_builder.clear_queue", text="", icon="TRASH")
        queue_box.template_list(
            "RR_UL_export_queue_items",
            "",
            settings,
            "export_queue",
            settings,
            "queue_active_index",
            rows=4,
        )
        row = queue_box.row(align=True)
        prev_item = row.operator("rr_builder.step_queue_item", text="", icon="TRIA_LEFT")
        prev_item.direction = -1
        row.operator("rr_builder.select_queue_item", text="Select", icon="RESTRICT_SELECT_OFF")
        next_item = row.operator("rr_builder.step_queue_item", text="", icon="TRIA_RIGHT")
        next_item.direction = 1
        resource_row = queue_box.row(align=True)
        resource_row.prop(settings, "include_model_with_export", text="Model")
        resource_row.prop(settings, "include_icon_with_export", text="Icon")
        queue_box.prop(settings, "skip_existing_exports", text="Skip Existing Models")
        queue_box.operator("rr_builder.create_bounding_box_collider", text="Create Collider", icon="MESH_CUBE")
        export_queue = queue_box.operator("rr_builder.export_queue", text="Export Queued Items", icon="EXPORT")
        export_queue.include_model = settings.include_model_with_export
        export_queue.include_icon = settings.include_icon_with_export
        self.draw_export_output_row(queue_box, settings)

    def draw_object_manager_tree_row(self, layout, obj, selected_objects, depth, active_group_root, label_suffix=""):
        row = layout.row(align=True)
        for _index in range(max(0, depth)):
            row.label(text="", icon="BLANK1")
        if is_object_manager_assembly_root(obj):
            icon = "OUTLINER_OB_GROUP_INSTANCE" if object_manager_assembly_type(obj) == "VARIANTS" else "OBJECT_DATA"
            label = f"{object_manager_display_name(obj)} [{object_manager_assembly_type_label(obj)}]"
        else:
            icon = "MESH_DATA" if obj.type == "MESH" else "EMPTY_DATA"
            label = obj.name
        if is_object_manager_assembly_root(obj):
            members = object_manager_selection_objects(obj)
            select_group = row.operator(
                "rr_builder.select_object_assembly",
                text=f"{label}{label_suffix}",
                icon=icon,
                depress=bool(members) and all(member in selected_objects for member in members),
            )
            select_group.root_name = obj.name
            select_group.toggle = obj == active_group_root
            return
        owner_root = object_manager_selection_owner_root(obj, active_group_root)
        if owner_root is None:
            row.label(text=f"{label}{label_suffix}", icon=icon)
            return
        select_member = row.operator(
            "rr_builder.toggle_object_assembly_member_selection",
            text=f"{label}{label_suffix}",
            icon=icon,
            depress=obj in selected_objects,
        )
        select_member.object_name = obj.name
        select_member.root_name = owner_root.name

    def draw_object_manager_object_branch(self, layout, obj, selected_objects, active_group_root, depth, seen):
        if obj in seen:
            self.draw_object_manager_tree_row(layout, obj, selected_objects, depth, active_group_root, "  (cycle)")
            return
        seen.add(obj)
        self.draw_object_manager_tree_row(layout, obj, selected_objects, depth, active_group_root)
        for child in list(obj.children):
            if child.type not in {"EMPTY", "MESH"}:
                continue
            if is_object_manager_assembly_root(child):
                self.draw_object_manager_tree(layout, child, selected_objects, active_group_root, depth + 1, seen)
            else:
                self.draw_object_manager_object_branch(
                    layout,
                    child,
                    selected_objects,
                    active_group_root,
                    depth + 1,
                    seen,
                )

    def draw_object_manager_tree(self, layout, root, selected_objects, active_group_root, depth=0, seen=None):
        if root is None:
            return
        if seen is None:
            seen = set()
        if root in seen:
            self.draw_object_manager_tree_row(layout, root, selected_objects, depth, active_group_root, "  (cycle)")
            return

        seen.add(root)
        self.draw_object_manager_tree_row(layout, root, selected_objects, depth, active_group_root)
        children = object_manager_direct_child_objects(root)
        for child in children:
            if is_object_manager_assembly_root(child):
                self.draw_object_manager_tree(layout, child, selected_objects, active_group_root, depth + 1, seen)
            else:
                self.draw_object_manager_object_branch(
                    layout,
                    child,
                    selected_objects,
                    active_group_root,
                    depth + 1,
                    seen,
                )

    def draw_export_group_box(self, layout, context, settings, assembly_root, create_members):
        can_create = len(create_members) >= 2
        if assembly_root is None and not can_create:
            return

        group_box = layout.box()
        group_box.label(text="Group")
        type_row = group_box.row(align=True)
        type_row.prop(settings, "object_manager_assembly_type", text="")
        if assembly_root is not None:
            # Panel drawing must stay read-only. Blender 5.1 rejects Scene or
            # data-block writes while a panel is being drawn.
            display_name = object_manager_display_name(assembly_root)
            name_reasonable = (
                object_manager_assembly_type(assembly_root) == "VARIANTS"
                and bool(sanitize_optional_id(display_name))
            ) or export_name_is_reasonable(display_name)
            icon = (
                "CHECKMARK"
                if name_reasonable
                else ("MESH_DATA" if assembly_root.type == "MESH" else "OUTLINER_OB_EMPTY")
            )
            members = object_manager_group_entries(assembly_root)
            name_row = group_box.row(align=True)
            name_row.prop(settings, "object_manager_current_group_name", text="")
            name_row.label(text="", icon=icon)
            if object_manager_assembly_type(assembly_root) == "VARIANTS":
                group_box.label(text=f"Variants ({len([member for member in members if get_asset_meshes(member)])})", icon="OUTLINER_OB_GROUP_INSTANCE")
            self.draw_export_name_hint(group_box, settings, assembly_root)
            tree_root = object_manager_top_group_root(assembly_root)
            members_panel = self.draw_fold_panel(
                group_box,
                settings,
                "export_group_tree_expanded",
                f"Members ({len(members)})",
            )
            if members_panel is not None:
                self.draw_object_manager_tree(
                    members_panel,
                    tree_root,
                    set(context.selected_objects or []),
                    assembly_root,
                )

        if assembly_root is None:
            group_box.label(text=f"{len(create_members)} selected", icon="CHECKMARK")

        action_row = group_box.row(align=True)
        create_row = action_row.row(align=True)
        create_row.enabled = can_create
        create_row.operator("rr_builder.create_object_assembly", text="Make", icon="OBJECT_DATA")
        duplicate_row = action_row.row(align=True)
        duplicate_row.enabled = assembly_root is not None
        duplicate_variant = duplicate_row.operator(
            "rr_builder.duplicate_object_assembly_variant",
            text="Duplicate Variant",
            icon="DUPLICATE",
        )
        duplicate_variant.root_name = assembly_root.name if assembly_root is not None else ""
        add_candidates = selected_object_manager_add_candidates(context, assembly_root) if assembly_root is not None else []
        add_row = action_row.row(align=True)
        add_row.enabled = bool(add_candidates)
        add_selected = add_row.operator(
            "rr_builder.add_selected_object_assembly_members",
            text="",
            icon="ADD",
        )
        add_selected.root_name = assembly_root.name if assembly_root is not None else ""
        selected_entries = selected_object_manager_group_entries(context, assembly_root) if assembly_root is not None else []
        remove_row = action_row.row(align=True)
        remove_row.enabled = bool(selected_entries)
        remove_selected = remove_row.operator(
            "rr_builder.dissolve_object_assembly",
            text="",
            icon="REMOVE",
        )
        remove_selected.root_name = assembly_root.name if assembly_root is not None else ""

    def draw_export_name_hint(self, layout, settings, root):
        if root is None or not is_object_manager_assembly_root(root):
            return
        if object_manager_assembly_type(root) == "VARIANTS":
            return

        current_name = object_manager_display_name(root)
        if export_name_is_reasonable(current_name) or bool(root.get(EXPORT_NAME_HINT_DISMISSED_PROP)):
            return

        try:
            recommendation = recommended_export_name(root, settings)
        except Exception as exc:
            layout.label(text=f"Name check failed: {exc}", icon="ERROR")
            return

        hint_row = layout.row(align=True)
        hint_row.label(text=f"Suggest: {recommendation}", icon="INFO")
        if root is not None:
            hint_row.operator("rr_builder.apply_recommended_export_name", text="", icon="FILE_TICK")
            hint_row.operator("rr_builder.dismiss_export_name_hint", text="", icon="X")

            parts_panel = self.draw_fold_panel(layout, settings, "export_name_show_part_names", "Part Names")
            if parts_panel is not None:
                part_row = parts_panel.row(align=True)
                part_row.prop(settings, "export_name_rename_members", text="Rename")
                part_row.prop(settings, "export_name_part_base", text="Base")

    def draw_layout_page(self, layout, settings):
        layout_box = layout.box()
        layout_box.label(text="Layout Snapshot")
        row = layout_box.row(align=True)
        row.operator("rr_builder.save_layout_snapshot", text="Save Layout", icon="FILE_TICK")
        row.operator("rr_builder.restore_layout_snapshot", text="Restore", icon="LOOP_BACK")
        row.operator("rr_builder.clear_layout_snapshot", text="", icon="TRASH")
        if settings.layout_snapshot_count > 0:
            layout_box.label(text=f"{settings.layout_snapshot_count} objects saved")
            if settings.layout_snapshot_saved_at:
                layout_box.label(text=settings.layout_snapshot_saved_at)
        else:
            layout_box.label(text="No layout saved")

    def draw_texture_page(self, layout):
        texture_box = layout.box()
        texture_box.label(text="Texture Packages")
        texture_box.operator(
            "rr_builder.apply_latest_texture_packages",
            text="Apply Latest Packages",
            icon="FILE_REFRESH",
        )
        texture_box.label(text="Inbox: textures/_codex_packages")

    def draw_bake_page(self, layout, context, settings):
        stale_bake_runtime = pbr_bake_runtime_state_is_stale(settings)
        if stale_bake_runtime:
            request_pbr_bake_runtime_state_reset()
        bake_running = bool(settings.pbr_bake_running) and not stale_bake_runtime
        bake_status = "" if stale_bake_runtime else (settings.pbr_bake_status or "")
        bake_progress = 0.0 if stale_bake_runtime else float(settings.pbr_bake_progress)

        bake_box = layout.box()
        bake_box.label(text="PBR Framework")
        row = bake_box.row(align=True)
        down = row.operator("rr_builder.step_pbr_bake_size", text="", icon="TRIA_LEFT")
        down.direction = -1
        row.label(text=f"Texture Size: {clamp_pbr_bake_size(settings.pbr_bake_resolution)}")
        up = row.operator("rr_builder.step_pbr_bake_size", text="", icon="TRIA_RIGHT")
        up.direction = 1

        available_materials = pbr_framework_materials_for_context(context)
        materials = pbr_framework_filtered_materials_for_context(context, settings, available_materials)
        material_box = self.draw_fold_panel(
            bake_box,
            settings,
            "pbr_framework_materials_expanded",
            f"Materials ({len(materials)}/{len(available_materials)})",
        )
        if material_box is not None:
            if available_materials:
                selected_names = pbr_framework_selected_material_names(settings)
                filter_enabled = bool(settings.pbr_framework_use_material_filter)
                for material in available_materials:
                    selected = (not filter_enabled) or (material.name in selected_names)
                    op = material_box.operator(
                        "rr_builder.set_pbr_framework_material_selection",
                        text=material.name,
                        depress=selected,
                    )
                    op.material_name = material.name
            else:
                material_box.label(text="Select mesh or group", icon="INFO")

        selected_role_keys = pbr_framework_selected_role_keys(settings)
        target_row = bake_box.row(align=True)
        target_row.enabled = bool(materials) and bool(selected_role_keys) and not bake_running
        target_row.operator("rr_builder.create_pbr_framework", text="Create Targets", icon="NODETREE")
        target_row.operator("rr_builder.prepare_pbr_framework_bake_target", text="Prepare Bake", icon="RENDER_STILL")
        save_row = bake_box.row(align=True)
        save_row.enabled = bool(materials) and bool(selected_role_keys) and not bake_running
        save_row.operator("rr_builder.save_pbr_framework_images", text="Save Images", icon="IMAGE_DATA")

        role_row = None
        for index, role in enumerate(PBR_BAKE_ROLES):
            if index % 2 == 0:
                role_row = bake_box.row(align=True)
                role_row.enabled = bool(materials) and not bake_running
            op = role_row.operator(
                "rr_builder.select_pbr_bake_target",
                text=role["label"],
                depress=role["key"] in selected_role_keys,
            )
            op.role_key = role["key"]

        if settings.pbr_framework_status:
            icon = "ERROR" if settings.pbr_framework_status.startswith("Failed:") else "CHECKMARK"
            bake_box.label(text=settings.pbr_framework_status, icon=icon)
        if bake_status:
            icon = "TIME" if bake_running else ("ERROR" if bake_status.startswith("Failed:") else "CHECKMARK")
            bake_box.label(text=bake_status, icon=icon)
            if bake_running:
                bake_box.label(text=f"Progress: {int(round(bake_progress * 100.0))}%")
        bake_box.label(text=f"Output: {pbr_bake_display_path(pbr_bake_output_root(settings))}")

    def draw_modeling_page(self, layout, context, settings):
        origin_box = layout.box()
        origin_box.label(text="Origin", icon="PIVOT_CURSOR")
        selected_meshes = selected_mesh_objects_for_modeling_origin(context)
        selection_label = modeling_origin_selection_label(context)

        selection_row = origin_box.row(align=True)
        selection_row.enabled = bool(selection_label)
        selection_op = selection_row.operator(
            "rr_builder.apply_modeling_origin_point",
            text="Apply to Selection",
            icon="OBJECT_ORIGIN",
        )
        selection_op.mode = "SELECTION"

        special_row = origin_box.row(align=True)
        special_row.alignment = "LEFT"
        special_row.prop(
            settings,
            "modeling_show_origin_rules",
            text="Rules",
            icon="TRIA_DOWN" if settings.modeling_show_origin_rules else "TRIA_RIGHT",
            emboss=False,
        )
        if settings.modeling_show_origin_rules:
            bottom_row = origin_box.row(align=True)
            bottom_row.enabled = bool(selected_meshes)
            bottom_op = bottom_row.operator(
                "rr_builder.apply_modeling_origin_point",
                text="Bottom",
                icon="TRIA_DOWN",
            )
            bottom_op.mode = "BOTTOM"

        bookmarks_box = layout.box()
        bookmarks_box.label(text="Point Bookmarks", icon="DOT")

        group_row = bookmarks_box.row(align=True)
        group_row.prop(settings, "point_bookmark_group", expand=True)

        source_row = bookmarks_box.row(align=True)
        source_row.prop(settings, "point_bookmark_source", expand=True)
        space_row = bookmarks_box.row(align=True)
        space_row.prop(settings, "point_bookmark_space", expand=True)

        group = settings.point_bookmark_group
        can_store = (
            settings.point_bookmark_source == "CURSOR"
            or bool(selection_label)
        )
        for point in ("P1", "P2", "P3"):
            slot = point_bookmark_slot(settings, group, point)
            if slot is None:
                continue

            row = bookmarks_box.row(align=True)
            row.label(text=point, icon="CHECKMARK" if slot.is_set else "RADIOBUT_OFF")
            row.prop(slot, "alias", text="")

            store_column = row.column(align=True)
            store_column.enabled = can_store
            store_op = store_column.operator(
                "rr_builder.store_point_bookmark",
                text="",
                icon="REC",
            )
            store_op.group = group
            store_op.point = point

            recall_column = row.column(align=True)
            recall_column.enabled = slot.is_set
            recall_op = recall_column.operator(
                "rr_builder.point_bookmark_to_cursor",
                text="",
                icon="PIVOT_CURSOR",
            )
            recall_op.group = group
            recall_op.point = point

            clear_column = row.column(align=True)
            clear_column.enabled = slot.is_set
            clear_op = clear_column.operator(
                "rr_builder.clear_point_bookmark",
                text="",
                icon="X",
            )
            clear_op.group = group
            clear_op.point = point

    def draw_icon_page(self, layout, context, settings):
        row = layout.row(align=True)
        down = row.operator("rr_builder.step_icon_size", text="", icon="TRIA_LEFT")
        down.direction = -1
        row.label(text=f"Icon Size: {clamp_icon_size(settings.icon_resolution)}")
        up = row.operator("rr_builder.step_icon_size", text="", icon="TRIA_RIGHT")
        up.direction = 1

        box = layout.box()
        box.label(text="Thumbnail Framing")
        option_row = box.row(align=True)
        option_row.prop(settings, "include_icon_with_export", text="Include Icon")
        option_row.prop(settings, "icon_outline_enabled", text="Outline")
        if settings.icon_outline_enabled:
            outline_controls = box.row(align=True)
            outline_controls.prop(settings, "icon_outline_color", text="")
            outline_controls.prop(settings, "icon_outline_pixels", text="Pixels")
            outline_controls.operator(
                "rr_builder.apply_icon_outline",
                text="",
                icon="CHECKMARK",
            )
        box.prop(settings, "icon_zoom", text="Size", slider=True)
        box.prop(settings, "icon_light_brightness", text="Brightness", slider=True)
        ratio_box = box.column(align=True)
        ratio_box.label(text="Light Ratio")
        for role, spec in ((icon_light_role(spec), spec) for spec in ICON_PREVIEW_LIGHT_SPECS):
            light_row = ratio_box.row(align=True)
            light_row.prop(settings, spec["ratio_attr"], text=role.title(), slider=True)
            edit_light = light_row.operator(
                "rr_builder.edit_icon_preview_light",
                text="",
                icon="MODIFIER",
                depress=settings.icon_light_edit_role == role,
            )
            edit_light.role = role

        editor_spec = icon_light_spec_for_role(settings.icon_light_edit_role)
        editor_light = bpy.data.objects.get(editor_spec["name"]) if editor_spec is not None else None
        if editor_spec is not None and editor_light is not None:
            ratio_box.separator(factor=0.35)
            header = ratio_box.row(align=True)
            header.label(text=editor_spec["label"], icon="LIGHT_SPOT")
            header.operator("rr_builder.save_icon_preview_light", text="", icon="CHECKMARK")
            header.operator("rr_builder.reset_icon_preview_light", text="", icon="LOOP_BACK")
            close_editor = header.operator("rr_builder.edit_icon_preview_light", text="", icon="X")
            close_editor.role = settings.icon_light_edit_role

            ratio_box.prop(settings, "icon_light_position", text="Position")
            ratio_box.prop(settings, "icon_light_focus", text="Focus")
            range_row = ratio_box.row(align=True)
            range_row.prop(settings, "icon_light_use_custom_distance", text="")
            range_value = range_row.row(align=True)
            range_value.enabled = settings.icon_light_use_custom_distance
            range_value.prop(settings, "icon_light_range", text="Range")
            beam_row = ratio_box.row(align=True)
            beam_row.prop(settings, "icon_light_spot_size", text="Beam")
            beam_row.prop(settings, "icon_light_spot_blend", text="Blend", slider=True)
            ratio_box.prop(settings, "icon_light_radius", text="Radius")
        row = box.row(align=True)
        if settings.icon_framing_adjusting:
            row.operator("rr_builder.confirm_icon_framing", text="Confirm", icon="CHECKMARK")
        else:
            row.operator("rr_builder.adjust_icon_framing", text="Adjust", icon="VIEW_CAMERA")
        row.operator("rr_builder.reset_icon_framing", text="Reset", icon="LOOP_BACK")
        row = box.row(align=True)
        row.operator("rr_builder.render_selected_icons", text="Render / Update Icon", icon="RENDER_STILL")
        draw_preview_reference_pair(box, context, settings)

        row = box.row(align=True)
        row.prop(settings, "show_references", text="References", icon="IMAGE_DATA")
        row.prop(settings, "use_unity_reference_icons", text="Unity Icons", toggle=True)
        row.operator("rr_builder.add_reference_image", text="", icon="ADD")
        row.operator("rr_builder.refresh_unity_reference_icons", text="", icon="FILE_REFRESH")
        row.operator("rr_builder.show_reference_image", text="", icon="HIDE_OFF")
        row.operator("rr_builder.remove_reference_image", text="", icon="REMOVE")
        if settings.show_references:
            box.template_list(
                "RR_UL_reference_items",
                "",
                settings,
                "references",
                settings,
                "reference_active_index",
                rows=2,
            )


def export_objects(mesh_objects, settings, context, source_label, export_model, include_icon):
    export_started = time.perf_counter()
    mesh_objects = expand_related_export_roots(mesh_objects)
    exported = []
    skipped = []
    failed = []
    successful_root_names = set()
    icon_render_cache = {}
    original_active = context.view_layer.objects.active
    original_selection = list(context.selected_objects)

    try:
        variant_transactions, variant_transactions_by_member, transaction_failures = (
            prepare_variant_export_transactions(mesh_objects, settings)
        )
    except Exception as exc:
        print("[RandomRealm Builder Exporter]", exc)
        show_builder_popup(context, str(exc), title="RR Helper", icon="ERROR")
        return {"CANCELLED"}
    failed.extend(transaction_failures)

    try:
        for obj in mesh_objects:
            try:
                transaction = variant_transactions_by_member.get(obj.name)
                if transaction is not None and transaction.get("settings") is None:
                    continue
                export_settings = transaction["settings"] if transaction is not None else settings
                asset_id, asset_type, status = export_builder_asset(
                    obj,
                    export_settings,
                    export_model,
                    include_icon,
                    shared_icon_root=shared_builder_icon_root(obj),
                    queue_import=False,
                    icon_render_cache=icon_render_cache,
                )
                successful_root_names.add(obj.name)
                if status == "skipped":
                    skipped.append(f"{asset_id} ({asset_type})")
                else:
                    exported.append(f"{asset_id} ({asset_type})")
            except Exception as exc:
                failed.append(f"{obj.name}: {exc}")
    finally:
        for selected in list(context.selected_objects):
            if selected.name in bpy.data.objects:
                selected.select_set(False)
        for obj in original_selection:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if original_active is not None and original_active.name in bpy.data.objects:
            context.view_layer.objects.active = original_active

    _published_variant_names, variant_publish_failures = finalize_variant_export_transactions(
        variant_transactions,
        successful_root_names,
    )
    failed.extend(variant_publish_failures)
    ordinary_paths, _published_ordinary_names = completed_ordinary_export_publication(
        mesh_objects,
        successful_root_names,
        settings.output_root,
    )
    if ordinary_paths:
        try:
            queue_unity_builder_import(ordinary_paths)
        except Exception as exc:
            failed.append(f"Unity import queue: {exc}")

    elapsed = time.perf_counter() - export_started
    if failed:
        message = f"Exported {len(exported)}, skipped {len(skipped)} from {source_label}; failed {len(failed)} ({elapsed:.1f}s). See console."
        for item in failed:
            print("[RandomRealm Builder Exporter]", item)
        if exported or skipped:
            context.window_manager.popup_menu(
                lambda self, _context: self.layout.label(text=message),
                title="RR Helper",
                icon="ERROR",
            )
        return {"CANCELLED"}

    if export_model and include_icon:
        resource_text = "model + icon"
    elif export_model:
        resource_text = "model only"
    else:
        resource_text = "icon only"
    context.window_manager.popup_menu(
        lambda self, _context: self.layout.label(text=f"Exported {len(exported)}, skipped {len(skipped)} {source_label} assets ({resource_text}) in {elapsed:.1f}s."),
        title="RR Helper",
        icon="INFO",
    )
    return {"FINISHED"}


def snapshot_file_contents(paths):
    snapshot = {}
    for path in paths:
        absolute_path = os.path.abspath(path)
        if absolute_path in snapshot:
            continue
        if os.path.isfile(absolute_path):
            with open(absolute_path, "rb") as handle:
                snapshot[absolute_path] = handle.read()
        else:
            snapshot[absolute_path] = None
    return snapshot


def restore_file_contents(snapshot):
    for path, content in snapshot.items():
        if content is None:
            if os.path.exists(path):
                os.remove(path)
            continue

        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.rollback"
        try:
            with open(temporary_path, "wb") as handle:
                handle.write(content)
            os.replace(temporary_path, path)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)


def render_icon_objects(mesh_objects, settings, context, source_label):
    requested_roots = list(mesh_objects)
    variant_sources = {}
    active_variant_group_name = ""
    for requested_root in requested_roots:
        group_root = object_manager_variant_group_root(requested_root)
        if group_root is None:
            continue
        source_root = requested_variant_icon_source_root(group_root, context)
        if source_root is None:
            continue
        variant_sources[group_root.name] = source_root
        if active_variant_preview_root(group_root, context) == source_root:
            active_variant_group_name = group_root.name

    mesh_objects = expand_related_export_roots(requested_roots)
    rendered = []
    failed = []
    last_icon_path = ""
    original_active = context.view_layer.objects.active
    original_selection = list(context.selected_objects)
    rendered_variant_sources = set()
    variant_expected = {}
    variant_success = {}
    variant_asset_ids = {}
    variant_source_icon_paths = {}
    ordinary_manifest_paths = []

    for obj in mesh_objects:
        group_root = object_manager_variant_group_root(obj)
        if group_root is not None:
            variant_expected.setdefault(group_root.name, set()).add(obj.name)

    try:
        variant_transactions, variant_transactions_by_member, transaction_failures = (
            prepare_variant_export_transactions(mesh_objects, settings)
        )
    except Exception as exc:
        show_builder_popup(
            context,
            str(exc),
            title="Builder Icons",
            icon="ERROR",
        )
        return {"CANCELLED"}
    failed.extend(transaction_failures)

    try:
        for obj in mesh_objects:
            try:
                transaction = variant_transactions_by_member.get(obj.name)
                if transaction is not None and transaction.get("settings") is None:
                    continue
                export_settings = transaction["settings"] if transaction is not None else settings
                validate_export_identity(obj)
                asset_id = export_asset_id(obj)
                asset_dir = os.path.join(export_settings.output_root, asset_id)
                os.makedirs(asset_dir, exist_ok=True)
                icon_path = os.path.join(asset_dir, "icon.png")
                model_path = os.path.join(asset_dir, "model.fbx")
                manifest_path = os.path.join(asset_dir, "manifest.json")
                existing_manifest = read_existing_manifest(manifest_path)
                existing_model_file = existing_manifest.get("modelFile", "")
                existing_model_path = os.path.join(asset_dir, existing_model_file) if existing_model_file else ""
                model_file = ""
                if os.path.exists(model_path):
                    model_file = "model.fbx"
                elif existing_model_path and os.path.exists(existing_model_path):
                    model_file = existing_model_file

                uv_export_contract = existing_uv_export_contract(
                    existing_manifest,
                    os.path.join(asset_dir, model_file) if model_file else "",
                )
                material_maps = existing_manifest.get("materialMaps", []) if model_file else []
                if not isinstance(material_maps, list):
                    material_maps = []
                warnings = existing_manifest.get("warnings", []) if model_file else []
                if not isinstance(warnings, list):
                    warnings = []

                group_root = object_manager_variant_group_root(obj)
                group_name = group_root.name if group_root is not None else ""
                shared_icon_root = variant_sources.get(group_name) or shared_builder_icon_root(obj)
                force_render = False
                if group_root is not None:
                    force_render = group_name not in rendered_variant_sources
                    if force_render:
                        if group_name == active_variant_group_name:
                            save_icon_framing_to_object(shared_icon_root, settings)
                        else:
                            load_icon_framing_from_object(shared_icon_root, settings)

                source_already_rendered_to_destination = (
                    group_root is not None
                    and not force_render
                    and obj == shared_icon_root
                    and os.path.isfile(icon_path)
                )
                if not source_already_rendered_to_destination:
                    render_or_copy_shared_icon(
                        obj,
                        export_settings,
                        icon_path,
                        shared_icon_root=shared_icon_root,
                        force_render=force_render,
                    )
                if group_root is not None:
                    rendered_variant_sources.add(group_name)
                write_manifest(
                    obj,
                    manifest_path,
                    asset_id,
                    infer_export_asset_type(obj, asset_id),
                    existing_manifest.get("category") or infer_asset_category(obj, infer_export_asset_type(obj, asset_id)),
                    settings.profile_name,
                    model_file,
                    "icon.png",
                    ["icon"],
                    warnings,
                    material_maps,
                    build_group_manifest(obj, icon_source_root=shared_icon_root),
                    uv_export_contract,
                    existing_manifest.get("bounds") if model_file else None,
                )
                if group_root is None:
                    ordinary_manifest_paths.append(manifest_path)
                    rendered.append(asset_id)
                    last_icon_path = icon_path
                    queue_item = queue_item_for_root(settings, obj)
                    if queue_item is not None:
                        queue_item.preview_path = icon_path
                else:
                    variant_success.setdefault(group_name, set()).add(obj.name)
                    variant_asset_ids.setdefault(group_name, []).append(asset_id)
                    source_asset_id = export_asset_id(shared_icon_root)
                    variant_source_icon_paths[group_name] = os.path.join(
                        settings.output_root,
                        source_asset_id,
                        "icon.png",
                    )
            except Exception as exc:
                failed.append(f"{obj.name}: {exc}")

        successful_variant_names = {
            name
            for successful_names in variant_success.values()
            for name in successful_names
        }
        published_variant_names, variant_publish_failures = finalize_variant_export_transactions(
            variant_transactions,
            successful_variant_names,
        )
        failed.extend(variant_publish_failures)
        completed_variant_groups = []
        for group_name, expected_names in variant_expected.items():
            if expected_names.issubset(published_variant_names):
                completed_variant_groups.append(group_name)

        if ordinary_manifest_paths:
            try:
                queue_unity_builder_import(ordinary_manifest_paths)
            except Exception as exc:
                failed.append(f"Unity import queue: {exc}")

        for group_name in completed_variant_groups:
            group_root = bpy.data.objects.get(group_name)
            source_root = variant_sources.get(group_name) or object_manager_variant_icon_source_root(group_root)
            remember_object_manager_variant_icon_source(group_root, source_root)
            source_icon_path = variant_source_icon_paths.get(group_name, "")
            queue_item = queue_item_for_root(settings, group_root)
            if queue_item is not None and source_icon_path:
                queue_item.preview_path = source_icon_path
                queue_item.icon_preview_root_name = source_root.name
            rendered.extend(variant_asset_ids.get(group_name, []))
            if source_icon_path:
                last_icon_path = source_icon_path
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in original_selection:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if original_active is not None and original_active.name in bpy.data.objects:
            context.view_layer.objects.active = original_active

    if last_icon_path:
        try:
            load_image_for_preview(settings, context, last_icon_path)
        except Exception as exc:
            print(f"[Builder Icon Render] Could not load preview image: {exc}")

    if failed:
        message = f"Rendered {len(rendered)} icons from {source_label}; failed {len(failed)}. See console."
        for item in failed:
            print("[Builder Icon Render]", item)
        if rendered:
            show_builder_popup(context, message, title="Builder Icons", icon="ERROR")
        return {"CANCELLED"}

    show_builder_popup(context, f"Rendered {len(rendered)} {source_label} icons.", title="Builder Icons", icon="INFO")
    return {"FINISHED"}


def draw_file_export_menu(self, context):
    layout = self.layout
    layout.separator()
    with_icons = layout.operator("rr_builder.export_selected", text="Builder Export")
    with_icons.include_model = True
    with_icons.include_icon = True
    fbx_only = layout.operator("rr_builder.export_selected", text="Builder Export FBX Only")
    fbx_only.include_model = True
    fbx_only.include_icon = False
    icon_only = layout.operator("rr_builder.export_selected", text="Builder Export Icon Only")
    icon_only.include_model = False
    icon_only.include_icon = True
    collection_with_icons = layout.operator("rr_builder.export_collection", text="Builder Collection")
    collection_with_icons.include_model = True
    collection_with_icons.include_icon = True


CLASSES = (
    RRBuilderExportQueueItem,
    RRBuilderReferenceItem,
    RRBuilderPointBookmarkSlot,
    RRBuilderExportSettings,
    RR_UL_export_queue_items,
    RR_UL_reference_items,
    RR_OT_queue_selected,
    RR_OT_remove_queue_item,
    RR_OT_clear_queue,
    RR_OT_step_queue_item,
    RR_OT_select_queue_item,
    RR_OT_preview_icon,
    RR_OT_preview_queue_icons,
    RR_OT_export_queue,
    RR_OT_add_reference_image,
    RR_OT_refresh_unity_reference_icons,
    RR_OT_remove_reference_image,
    RR_OT_show_reference_image,
    RR_OT_create_object_assembly,
    RR_OT_rename_object_manager_entry,
    RR_OT_select_object_assembly,
    RR_OT_toggle_object_assembly_member_selection,
    RR_OT_clear_native_duplicate_rr_identity,
    RR_OT_duplicate_move_without_group_membership,
    RR_OT_duplicate_object_assembly_variant,
    RR_OT_add_selected_object_assembly_members,
    RR_OT_dissolve_object_assembly,
    RR_OT_apply_recommended_export_name,
    RR_OT_dismiss_export_name_hint,
    RR_OT_create_bounding_box_collider,
    RR_OT_validate_selected,
    RR_OT_export_selected,
    RR_OT_export_collection,
    RR_OT_render_selected_icons,
    RR_OT_apply_icon_outline,
    RR_OT_use_unity_temp_output,
    RR_OT_save_layout_snapshot,
    RR_OT_restore_layout_snapshot,
    RR_OT_clear_layout_snapshot,
    RR_OT_step_icon_size,
    RR_OT_step_pbr_bake_size,
    RR_OT_toggle_pbr_bake_material,
    RR_OT_set_pbr_bake_materials,
    RR_OT_edit_icon_preview_light,
    RR_OT_save_icon_preview_light,
    RR_OT_reset_icon_preview_light,
    RR_OT_reset_icon_framing,
    RR_OT_confirm_icon_framing,
    RR_OT_adjust_icon_framing,
    RR_OT_apply_latest_texture_packages,
    RR_OT_create_pbr_framework,
    RR_OT_save_pbr_framework_images,
    RR_OT_prepare_pbr_framework_bake_target,
    RR_OT_set_pbr_framework_material_selection,
    RR_OT_select_pbr_bake_target,
    RR_OT_bake_selected_pbr,
    RR_OT_apply_modeling_origin_point,
    RR_OT_store_point_bookmark,
    RR_OT_point_bookmark_to_cursor,
    RR_OT_clear_point_bookmark,
    RR_OT_refresh_addon,
    RR_OT_set_ui_page,
    RR_OT_toggle_ui_flag,
    RR_OT_set_export_section,
    RR_OT_open_animation_blend,
    RR_OT_open_unity_animation_import_folder,
    RR_OT_request_unity_animation_import,
    RR_PT_builder_exporter,
)


RR_STARTUP_DEFERRED_TIMERS = (
    (repair_rr_normal_map_nodes_deferred, 0.2),
    (apply_icon_render_resolution_deferred, 0.1),
    (reset_pbr_bake_runtime_state_deferred, 0.1),
    (reset_stale_icon_framing_state_deferred, 0.1),
)


def register_rr_startup_deferred_timers():
    for callback, first_interval in RR_STARTUP_DEFERRED_TIMERS:
        try:
            if not bpy.app.timers.is_registered(callback):
                bpy.app.timers.register(callback, first_interval=first_interval)
        except Exception as exc:
            print(f"[RR Helper] Could not schedule {callback.__name__}: {exc}")


def unregister_rr_startup_deferred_timers():
    for callback, _first_interval in RR_STARTUP_DEFERRED_TIMERS:
        try:
            if bpy.app.timers.is_registered(callback):
                bpy.app.timers.unregister(callback)
        except Exception:
            pass


def configure_object_manager_duplicate_macro():
    duplicate_step = RR_OT_duplicate_move_without_group_membership.define("OBJECT_OT_duplicate")
    duplicate_step.properties.linked = False
    duplicate_step.properties.mode = "TRANSLATION"
    RR_OT_duplicate_move_without_group_membership.define("RR_BUILDER_OT_clear_native_duplicate_rr_identity")
    translate_step = RR_OT_duplicate_move_without_group_membership.define("TRANSFORM_OT_translate")
    translate_step.properties.use_proportional_edit = False


def register_object_manager_duplicate_keymap():
    global OBJECT_MANAGER_DUPLICATE_KEYMAPS
    OBJECT_MANAGER_DUPLICATE_KEYMAPS = []
    window_manager = getattr(bpy.context, "window_manager", None)
    keyconfigs = getattr(window_manager, "keyconfigs", None) if window_manager is not None else None
    addon_keyconfig = getattr(keyconfigs, "addon", None) if keyconfigs is not None else None
    if addon_keyconfig is None:
        return
    keymap = addon_keyconfig.keymaps.new(name="Object Mode", space_type="EMPTY")
    try:
        item = keymap.keymap_items.new(
            "rr_builder.duplicate_move_without_group_membership",
            "D",
            "PRESS",
            shift=True,
            head=True,
        )
    except TypeError:
        item = keymap.keymap_items.new(
            "rr_builder.duplicate_move_without_group_membership",
            "D",
            "PRESS",
            shift=True,
        )
    OBJECT_MANAGER_DUPLICATE_KEYMAPS.append((keymap, item))


def unregister_object_manager_duplicate_keymap():
    global OBJECT_MANAGER_DUPLICATE_KEYMAPS
    for keymap, item in reversed(OBJECT_MANAGER_DUPLICATE_KEYMAPS):
        try:
            keymap.keymap_items.remove(item)
        except Exception:
            pass
    OBJECT_MANAGER_DUPLICATE_KEYMAPS = []


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    configure_object_manager_duplicate_macro()
    register_object_manager_duplicate_keymap()
    bpy.types.Scene.rr_builder_export_settings = bpy.props.PointerProperty(type=RRBuilderExportSettings)
    reset_pbr_bake_runtime_state()
    bpy.types.TOPBAR_MT_file_export.append(draw_file_export_menu)
    register_scene_selection_queue_sync()
    register_rr_addon_change_watch()
    if reset_pbr_bake_runtime_state_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(reset_pbr_bake_runtime_state_on_load)
    if repair_rr_normal_map_nodes_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(repair_rr_normal_map_nodes_on_load)
    if reset_object_manager_duplicate_guard_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(reset_object_manager_duplicate_guard_on_load)
    if clear_inherited_rr_identity_before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(clear_inherited_rr_identity_before_save)
    register_rr_startup_deferred_timers()

def unregister():
    unregister_object_manager_duplicate_keymap()
    unregister_rr_addon_change_watch()
    unregister_scene_selection_queue_sync()
    unregister_rr_startup_deferred_timers()
    try:
        bpy.app.handlers.load_post.remove(reset_pbr_bake_runtime_state_on_load)
    except Exception:
        pass
    try:
        bpy.app.handlers.load_post.remove(repair_rr_normal_map_nodes_on_load)
    except Exception:
        pass
    try:
        bpy.app.handlers.load_post.remove(reset_object_manager_duplicate_guard_on_load)
    except Exception:
        pass
    try:
        bpy.app.handlers.save_pre.remove(clear_inherited_rr_identity_before_save)
    except Exception:
        pass
    try:
        bpy.types.TOPBAR_MT_file_export.remove(draw_file_export_menu)
    except Exception:
        pass
    try:
        cleanup_icon_preview_helpers()
    except Exception as exc:
        print(f"[RR Helper] Could not fully clean preview helpers while disabling: {exc}")
    clear_preview_collections()
    if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
        del bpy.types.Scene.rr_builder_export_settings
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()




