"""Versioned, scene-safe reference image sets for Character Designer.

The module deliberately treats a set as a generic spatial reference aid.  A set
can describe a prop, vehicle, room, character, or any other subject; no body or
height assumptions are built into either the manifest or the Blender objects.
"""

import copy
from datetime import datetime
import json
import math
import ntpath
import os
import re
import stat
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector

from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_REFERENCE, active_ui_page


REFERENCE_SCHEMA = "blackunity.cdesigner.reference-view-set"
REFERENCE_MANIFEST_VERSION = 1
REFERENCE_OBJECT_VERSION = 1
REFERENCE_GENERATOR_ID = "cdesigner_reference_views_v1"
REFERENCE_COLLECTION_NAME = "C Designer References"
REFERENCE_VIEW_DIRECTIONS = (
    "front",
    "back",
    "left",
    "right",
    "top",
    "bottom",
)

_GENERATOR_KEY = "cdesigner_reference_generator"
_VERSION_KEY = "cdesigner_reference_version"
_SET_ID_KEY = "cdesigner_reference_set_id"
_SET_NAME_KEY = "cdesigner_reference_set_name"
_KIND_KEY = "cdesigner_reference_kind"
_VIEW_KEY = "cdesigner_reference_view"
_ENABLED_KEY = "cdesigner_reference_enabled"
_MANIFEST_KEY = "cdesigner_reference_manifest"
_IMAGE_PATH_KEY = "cdesigner_reference_image"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_SCENE_SET_ENUM_CACHE = ()


class ReferenceViewError(ValueError):
    """A manifest or managed-scene problem safe to report to the artist."""


def _reject_json_constant(value):
    raise ReferenceViewError(f"Manifest contains non-finite JSON value {value!r}.")


def _finite_number(value, label, *, minimum=None, maximum=None, strict_minimum=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReferenceViewError(f"{label} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise ReferenceViewError(f"{label} must be finite.")
    if minimum is not None:
        invalid = result <= minimum if strict_minimum else result < minimum
        if invalid:
            comparison = "greater than" if strict_minimum else "at least"
            raise ReferenceViewError(f"{label} must be {comparison} {minimum}.")
    if maximum is not None and result > maximum:
        raise ReferenceViewError(f"{label} must be no more than {maximum}.")
    return result


def _optional_bool(payload, key, label, default):
    if key not in payload:
        return default
    value = payload[key]
    if not isinstance(value, bool):
        raise ReferenceViewError(f"{label} must be true or false.")
    return value


def _manifest_file_path(path):
    if not isinstance(path, str) or not path.strip():
        raise ReferenceViewError("Choose a Reference View Set manifest.")
    expanded = os.path.expanduser(path.strip())
    if expanded.startswith("//"):
        expanded = bpy.path.abspath(expanded)
    elif not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return os.path.realpath(expanded)


def _manifest_image_path(path, manifest_directory, label):
    """Resolve a manifest-relative image without allowing directory escape."""

    if not isinstance(path, str) or not path or path != path.strip():
        raise ReferenceViewError(f"{label}.file must be a non-empty POSIX relative path.")
    relative = path
    if "\\" in relative or ntpath.splitdrive(relative)[0]:
        raise ReferenceViewError(f"{label}.file must use a drive-free POSIX relative path.")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ReferenceViewError(
            f"{label}.file cannot be absolute or contain empty, '.' or '..' components."
        )
    candidate = os.path.realpath(
        os.path.abspath(os.path.join(manifest_directory, *parts))
    )
    if not _realpath_is_within(manifest_directory, candidate):
        raise ReferenceViewError(f"{label}.file cannot escape its manifest folder.")
    return candidate


def _normalized_path(path):
    if not path:
        return ""
    try:
        expanded = bpy.path.abspath(path) if str(path).startswith("//") else path
        return os.path.normcase(os.path.realpath(os.path.abspath(expanded)))
    except (OSError, TypeError, ValueError):
        return ""


def _realpath_is_within(root, candidate):
    root = os.path.normcase(os.path.realpath(os.path.abspath(root)))
    candidate = os.path.normcase(os.path.realpath(os.path.abspath(candidate)))
    try:
        return os.path.commonpath((root, candidate)) == root
    except ValueError:
        return False


def _is_reparse_directory(path):
    """Reject every directory alias that os.walk might traverse on Windows."""

    try:
        if os.path.islink(path):
            return True
        is_junction = getattr(os.path, "isjunction", None)
        if is_junction is not None and is_junction(path):
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        # An unreadable directory must not be treated as a safe traversal root.
        return True


def _portable_image_filepath(image_path, blend_filepath=None):
    """Prefer // storage only when the image genuinely belongs to the project."""

    absolute = os.path.realpath(os.path.abspath(image_path))
    blend_filepath = bpy.data.filepath if blend_filepath is None else blend_filepath
    if not blend_filepath:
        return absolute
    blend_directory = os.path.realpath(os.path.dirname(os.path.abspath(blend_filepath)))
    if not _realpath_is_within(blend_directory, absolute):
        return absolute
    relative = os.path.relpath(absolute, blend_directory).replace(os.sep, "/")
    return f"//{relative}"


def _existing_absolute_image_path(path):
    """Return one existing image path without trusting the current .blend base.

    Managed views persist their source path as an absolute custom property.  A
    Blender ``//`` path is still accepted when it resolves from the currently
    loaded file, but a plain relative path is deliberately rejected: resolving
    that through the process working directory could reconnect an unrelated
    image with the same name.
    """

    if not isinstance(path, str) or not path.strip():
        return ""
    candidate = os.path.expanduser(path.strip())
    try:
        if candidate.startswith("//"):
            candidate = bpy.path.abspath(candidate)
        elif not os.path.isabs(candidate):
            return ""
        candidate = os.path.realpath(os.path.abspath(candidate))
    except (OSError, TypeError, ValueError):
        return ""
    return candidate if os.path.isfile(candidate) else ""


def _managed_image_recovery_path(view_obj, image):
    """Find the exact existing file recorded for one owned managed Image."""

    candidates = (
        image.get(_IMAGE_PATH_KEY),
        view_obj.get(_IMAGE_PATH_KEY),
        image.filepath,
    )
    for candidate in candidates:
        recovered = _existing_absolute_image_path(candidate)
        if recovered:
            return recovered
    return ""


def reconcile_managed_reference_images():
    """Reconnect owned reference Images whose ``//`` base no longer exists.

    Blender resolves ``//`` from the file being opened.  That is normally the
    desired portable behavior, but recovery files such as ``quit.blend`` and
    ``*_autosave.blend`` live in the Temp directory.  Their project-relative
    image paths consequently resolve into Temp and appear magenta.  Each
    Character Designer view already stores its validated absolute source path;
    this reconciliation uses that path only when the current path is broken.

    The function is intentionally conservative.  It changes only an exact,
    current-version VIEW/IMAGE ownership pair and never touches packed, linked,
    user-owned, or merely same-named Images.
    """

    reconnected = 0
    reloaded = 0
    unresolved = []
    handled_images = set()
    objects = getattr(bpy.data, "objects", None)
    if objects is None:
        # addon_utils.enable() deliberately registers add-ons while bpy.data is
        # a _RestrictData proxy.  Registration schedules a normal-context retry
        # instead of treating that temporary API restriction as a load failure.
        return {
            "reconnected": 0,
            "reloaded": 0,
            "unresolved": (),
        }
    for view_obj in tuple(objects):
        if not _marker_matches(view_obj, "VIEW"):
            continue
        if _object_scene_count(view_obj) == 0:
            continue
        image = view_obj.data
        if not isinstance(image, bpy.types.Image):
            continue
        set_id = view_obj.get(_SET_ID_KEY)
        if not _marker_matches(image, "IMAGE", set_id):
            continue
        if image.library is not None or image.packed_file is not None:
            continue

        image_pointer = image.as_pointer()
        if image_pointer in handled_images:
            continue
        handled_images.add(image_pointer)

        current_path = _existing_absolute_image_path(image.filepath)
        if current_path:
            if not image.has_data:
                try:
                    image.reload()
                    reloaded += 1
                except RuntimeError:
                    unresolved.append(view_obj.name)
            continue

        recovered_path = _managed_image_recovery_path(view_obj, image)
        if not recovered_path:
            unresolved.append(view_obj.name)
            continue

        image.filepath = recovered_path
        image[_IMAGE_PATH_KEY] = recovered_path
        view_obj[_IMAGE_PATH_KEY] = recovered_path
        reconnected += 1
        try:
            image.reload()
            reloaded += 1
        except RuntimeError:
            unresolved.append(view_obj.name)

    return {
        "reconnected": reconnected,
        "reloaded": reloaded,
        "unresolved": tuple(sorted(set(unresolved))),
    }


@persistent
def _reference_images_load_post(_filepath):
    """Repair project-relative managed Images after a normal or recovery load."""

    try:
        reconcile_managed_reference_images()
    except Exception:
        # A recovery helper must never make opening an otherwise valid .blend
        # fail.  The explicit Create / Update action remains available for any
        # malformed managed data that cannot be reconciled automatically.
        pass


def _reference_images_reconcile_deferred():
    """Run current-file repair after add-on registration leaves _RestrictData."""

    if getattr(bpy.data, "objects", None) is None:
        return 0.1
    try:
        reconcile_managed_reference_images()
    except Exception:
        pass
    return None


def register_reference_view_handlers():
    if _reference_images_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_reference_images_load_post)
    # Also repair the current file when a user enables or refreshes the add-on
    # after the recovery .blend has already been opened. addon_utils wraps
    # register() in _RestrictData, so this must run on the next normal UI tick.
    if not bpy.app.timers.is_registered(_reference_images_reconcile_deferred):
        bpy.app.timers.register(
            _reference_images_reconcile_deferred,
            first_interval=0.0,
        )


def unregister_reference_view_handlers():
    if bpy.app.timers.is_registered(_reference_images_reconcile_deferred):
        bpy.app.timers.unregister(_reference_images_reconcile_deferred)
    if _reference_images_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_reference_images_load_post)


def _parse_origin(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ReferenceViewError("placement.origin must be a three-number JSON array.")
    return tuple(
        _finite_number(component, f"placement.origin[{index}]")
        for index, component in enumerate(value)
    )


def _parse_updated_at(value):
    if not isinstance(value, str) or not value.strip():
        raise ReferenceViewError("updatedAt must be an RFC 3339 timestamp string.")
    text = value.strip()
    normalized = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReferenceViewError("updatedAt must be a valid RFC 3339 timestamp.") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ReferenceViewError("updatedAt must include a timezone offset or Z.")
    try:
        epoch = timestamp.timestamp()
    except (OSError, OverflowError, ValueError) as exc:
        raise ReferenceViewError("updatedAt is outside the supported timestamp range.") from exc
    return text, epoch


def _nullable_override(payload, key, inherited, label, **number_options):
    value = payload.get(key)
    if value is None:
        return inherited
    return _finite_number(value, f"{label}.{key}", **number_options)


def discover_reference_view_manifests(project_root=None):
    """Find standard manifests below //References/CDesigner recursively."""

    if project_root is None:
        # An unsaved .blend has no stable project-relative // root. Falling back
        # to the process cwd could silently import an unrelated project's set.
        if not bpy.data.filepath:
            return ()
        project_root = os.path.dirname(bpy.data.filepath)
    project_root = os.path.realpath(os.path.abspath(project_root))
    lexical_scan_root = os.path.abspath(
        os.path.join(project_root, "References", "CDesigner")
    )
    if not os.path.isdir(lexical_scan_root) or _is_reparse_directory(lexical_scan_root):
        return ()
    scan_root = os.path.realpath(lexical_scan_root)
    if not _realpath_is_within(project_root, scan_root):
        return ()
    found = []
    for root, directory_names, file_names in os.walk(
        scan_root,
        topdown=True,
        followlinks=False,
    ):
        real_root = os.path.realpath(root)
        if not _realpath_is_within(scan_root, real_root):
            directory_names[:] = []
            continue
        safe_directories = []
        for name in sorted(directory_names):
            candidate = os.path.join(root, name)
            if name.startswith(".") or _is_reparse_directory(candidate):
                continue
            if not _realpath_is_within(scan_root, candidate):
                continue
            safe_directories.append(name)
        directory_names[:] = safe_directories
        if "reference-views.json" in file_names:
            manifest = os.path.realpath(os.path.join(root, "reference-views.json"))
            if _realpath_is_within(scan_root, manifest):
                found.append(manifest)
    return tuple(sorted(found, key=lambda value: os.path.normcase(value)))


def read_reference_view_manifest(path):
    """Read and fully validate one version-1 Reference View Set manifest."""

    manifest_path = _manifest_file_path(path)
    try:
        size = os.path.getsize(manifest_path)
    except OSError as exc:
        raise ReferenceViewError(f"Cannot read manifest: {exc}") from exc
    if size > _MAX_MANIFEST_BYTES:
        raise ReferenceViewError("Reference View Set manifest is unexpectedly large.")

    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle, parse_constant=_reject_json_constant)
    except ReferenceViewError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReferenceViewError(f"Invalid Reference View Set manifest: {exc}") from exc

    if not isinstance(payload, dict):
        raise ReferenceViewError("Manifest root must be a JSON object.")
    if payload.get("schema") != REFERENCE_SCHEMA:
        raise ReferenceViewError(f"Manifest schema must be {REFERENCE_SCHEMA!r}.")
    version = payload.get("version")
    if type(version) is not int or version != REFERENCE_MANIFEST_VERSION:
        raise ReferenceViewError(
            f"Unsupported manifest version {version!r}; expected {REFERENCE_MANIFEST_VERSION}."
        )

    raw_set_id = payload.get("setId")
    if not isinstance(raw_set_id, str):
        raise ReferenceViewError("setId must be a UUID string.")
    try:
        set_id = str(uuid.UUID(raw_set_id))
    except (ValueError, AttributeError) as exc:
        raise ReferenceViewError("setId must be a valid UUID string.") from exc
    set_name = payload.get("name")
    if not isinstance(set_name, str) or not set_name.strip():
        raise ReferenceViewError("name must be a non-empty string.")
    set_name = set_name.strip()
    if len(set_name) > 128:
        raise ReferenceViewError("name must be no longer than 128 characters.")
    updated_at, updated_at_epoch = _parse_updated_at(payload.get("updatedAt"))
    if payload.get("units") != "METERS":
        raise ReferenceViewError("units must be 'METERS'.")

    placement = payload.get("placement")
    if not isinstance(placement, dict):
        raise ReferenceViewError("placement must be a JSON object.")
    origin_meters = _parse_origin(placement.get("origin"))
    default_distance = _finite_number(
        placement.get("distanceMeters"),
        "placement.distanceMeters",
        minimum=0.0,
    )
    default_size = _finite_number(
        placement.get("displaySizeMeters"),
        "placement.displaySizeMeters",
        minimum=0.0,
        strict_minimum=True,
    )
    default_opacity = _finite_number(
        placement.get("opacity"),
        "placement.opacity",
        minimum=0.0,
        maximum=1.0,
    )
    show_in_front = placement.get("showInFront")
    if not isinstance(show_in_front, bool):
        raise ReferenceViewError("placement.showInFront must be true or false.")

    views_payload = payload.get("views")
    if not isinstance(views_payload, dict):
        raise ReferenceViewError("views must be a JSON object.")
    manifest_directory = os.path.dirname(manifest_path)
    views = []
    for direction in REFERENCE_VIEW_DIRECTIONS:
        raw_view = views_payload.get(direction, {})
        label = f"views.{direction}"
        if not isinstance(raw_view, dict):
            raise ReferenceViewError(f"{label} must be a JSON object.")
        requested_enabled = _optional_bool(
            raw_view,
            "enabled",
            f"{label}.enabled",
            True,
        )
        raw_image = raw_view.get("file")
        image_path = ""
        configured = raw_image is not None
        if raw_image is not None:
            image_path = _manifest_image_path(raw_image, manifest_directory, label)
        # Compatibility: early Console builds emitted enabled:true,file:null
        # for unused slots. A null file always means "unconfigured" and cannot
        # make an otherwise useful partial set fail.
        enabled = bool(configured and requested_enabled)

        distance = _nullable_override(
            raw_view,
            "distanceMeters",
            default_distance,
            label,
            minimum=0.0,
        )
        display_size = _nullable_override(
            raw_view,
            "displaySizeMeters",
            default_size,
            label,
            minimum=0.0,
            strict_minimum=True,
        )
        opacity = _nullable_override(
            raw_view,
            "opacity",
            default_opacity,
            label,
            minimum=0.0,
            maximum=1.0,
        )
        flip_horizontal = _optional_bool(
            raw_view,
            "flipHorizontal",
            f"{label}.flipHorizontal",
            False,
        )
        flip_vertical = _optional_bool(
            raw_view,
            "flipVertical",
            f"{label}.flipVertical",
            False,
        )
        views.append(
            {
                "direction": direction,
                "configured": configured,
                "enabled": enabled,
                "image_path": image_path,
                "distance_meters": distance,
                "size_meters": display_size,
                "opacity": opacity,
                "flip_horizontal": flip_horizontal,
                "flip_vertical": flip_vertical,
            }
        )

    for view in views:
        if view["enabled"] and not os.path.isfile(view["image_path"]):
            raise ReferenceViewError(
                f"{view['direction'].title()} image does not exist: {view['image_path']}"
            )

    return {
        "schema": REFERENCE_SCHEMA,
        "version": REFERENCE_MANIFEST_VERSION,
        "manifest_path": manifest_path,
        "set_id": set_id,
        "set_name": set_name,
        "updated_at": updated_at,
        "updated_at_epoch": updated_at_epoch,
        "units": "METERS",
        "origin_meters": origin_meters,
        "placement": {
            "distance_meters": default_distance,
            "size_meters": default_size,
            "opacity": default_opacity,
            "show_in_front": show_in_front,
        },
        "views": tuple(views),
    }


def _scene_collections(scene):
    result = []
    pending = [scene.collection]
    seen = set()
    while pending:
        collection = pending.pop()
        pointer = collection.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        result.append(collection)
        pending.extend(collection.children)
    return tuple(result)


def _collection_scene_count(collection):
    pointer = collection.as_pointer()
    return sum(
        1
        for scene in bpy.data.scenes
        if any(item.as_pointer() == pointer for item in _scene_collections(scene))
    )


def _object_scene_count(obj):
    count = 0
    for scene in bpy.data.scenes:
        try:
            if obj.name in scene.objects and scene.objects[obj.name] is obj:
                count += 1
        except (KeyError, ReferenceError):
            continue
    return count


def _marker_matches(data_block, kind=None, set_id=None):
    if data_block.get(_GENERATOR_KEY) != REFERENCE_GENERATOR_ID:
        return False
    if data_block.get(_VERSION_KEY) != REFERENCE_OBJECT_VERSION:
        return False
    if kind is not None and data_block.get(_KIND_KEY) != kind:
        return False
    if set_id is not None and data_block.get(_SET_ID_KEY) != set_id:
        return False
    return True


def _set_marker(data_block, kind, set_id=None):
    data_block[_GENERATOR_KEY] = REFERENCE_GENERATOR_ID
    data_block[_VERSION_KEY] = REFERENCE_OBJECT_VERSION
    data_block[_KIND_KEY] = kind
    if set_id is not None:
        data_block[_SET_ID_KEY] = set_id


def _managed_collection(scene):
    candidates = []
    for collection in _scene_collections(scene):
        if collection is scene.collection:
            continue
        if collection.get(_GENERATOR_KEY) != REFERENCE_GENERATOR_ID:
            continue
        if collection.get(_VERSION_KEY) != REFERENCE_OBJECT_VERSION:
            raise ReferenceViewError(
                f"Managed collection {collection.name!r} uses an unsupported version."
            )
        if collection.get(_KIND_KEY) != "COLLECTION":
            raise ReferenceViewError(
                f"Managed collection {collection.name!r} has invalid metadata."
            )
        if collection.library is not None:
            raise ReferenceViewError("The managed reference collection is linked read-only data.")
        if _collection_scene_count(collection) > 1:
            raise ReferenceViewError(
                "The managed reference collection is shared by multiple Scenes; "
                "make it single-scene before synchronizing."
            )
        candidates.append(collection)
    if len(candidates) > 1:
        raise ReferenceViewError("The scene contains multiple managed reference collections.")
    return candidates[0] if candidates else None


def _managed_set_objects(scene, set_id, *, reject_shared=False):
    roots = []
    views = {}
    for obj in scene.objects:
        if obj.get(_GENERATOR_KEY) != REFERENCE_GENERATOR_ID:
            continue
        if obj.get(_SET_ID_KEY) != set_id:
            continue
        if obj.get(_VERSION_KEY) != REFERENCE_OBJECT_VERSION:
            raise ReferenceViewError(
                f"Managed object {obj.name!r} uses an unsupported version."
            )
        if obj.library is not None:
            raise ReferenceViewError(f"Managed object {obj.name!r} is linked read-only data.")
        if reject_shared and _object_scene_count(obj) > 1:
            raise ReferenceViewError(
                f"Managed object {obj.name!r} is shared by multiple Scenes; "
                "make it single-scene before synchronizing."
            )
        kind = obj.get(_KIND_KEY)
        if kind == "ROOT":
            roots.append(obj)
        elif kind == "VIEW":
            direction = obj.get(_VIEW_KEY)
            if direction not in REFERENCE_VIEW_DIRECTIONS:
                raise ReferenceViewError(f"Managed view {obj.name!r} has invalid metadata.")
            if direction in views:
                raise ReferenceViewError(
                    f"The set contains more than one managed {direction.title()} view."
                )
            views[direction] = obj
        else:
            raise ReferenceViewError(f"Managed object {obj.name!r} has invalid metadata.")
    if len(roots) > 1:
        raise ReferenceViewError("The set contains more than one managed root Empty.")
    root = roots[0] if roots else None
    if root is not None and root.type != "EMPTY":
        raise ReferenceViewError("The managed set root is no longer an Empty.")
    for direction, obj in views.items():
        if obj.type != "EMPTY":
            raise ReferenceViewError(
                f"The managed {direction.title()} view is no longer an Empty."
            )
    return root, views


def _scene_managed_set_summaries(scene):
    """Enumerate recoverable current-version sets without trusting UI state."""

    if scene is None:
        return ()
    summaries = {}
    for obj in scene.objects:
        if not _marker_matches(obj) or obj.get(_KIND_KEY) not in {"ROOT", "VIEW"}:
            continue
        raw_set_id = obj.get(_SET_ID_KEY)
        try:
            set_id = str(uuid.UUID(raw_set_id))
        except (ValueError, AttributeError, TypeError):
            continue
        entry = summaries.setdefault(
            set_id,
            {
                "set_id": set_id,
                "set_name": "Reference Set",
                "root_count": 0,
                "views": set(),
                "enabled_views": set(),
            },
        )
        kind = obj.get(_KIND_KEY)
        if kind == "ROOT":
            entry["root_count"] += 1
            stored_name = obj.get(_SET_NAME_KEY)
            if isinstance(stored_name, str) and stored_name.strip():
                entry["set_name"] = stored_name.strip()
        else:
            direction = obj.get(_VIEW_KEY)
            if direction not in REFERENCE_VIEW_DIRECTIONS:
                continue
            entry["views"].add(direction)
            if obj.get(_ENABLED_KEY, True):
                entry["enabled_views"].add(direction)

    result = []
    for entry in summaries.values():
        if entry["root_count"] == 0 and not entry["views"]:
            continue
        result.append(
            {
                "set_id": entry["set_id"],
                "set_name": entry["set_name"],
                "root_count": entry["root_count"],
                "view_count": len(entry["views"]),
                "enabled_view_count": len(entry["enabled_views"]),
            }
        )
    return tuple(sorted(result, key=lambda item: (item["set_name"].casefold(), item["set_id"])))


def _scene_set_enum_items(_settings, context):
    global _SCENE_SET_ENUM_CACHE
    scene = getattr(context, "scene", None) if context is not None else None
    if scene is None:
        _SCENE_SET_ENUM_CACHE = ()
        return _SCENE_SET_ENUM_CACHE
    summaries = _scene_managed_set_summaries(scene)
    items = []
    if len(summaries) > 1:
        items.append(
            (
                "__SELECT__",
                "Choose a Scene Set",
                "Select one exact-tagged set before changing or clearing it",
                0,
            )
        )
    for index, summary in enumerate(summaries, start=len(items)):
        label = f"{summary['set_name']} - {summary['set_id'][:8]}"
        description = (
            f"{summary['view_count']} managed view"
            f"{'s' if summary['view_count'] != 1 else ''} in the current Scene"
        )
        items.append((summary["set_id"], label, description, index))
    # Blender's dynamic Enum callbacks retain references to these strings; keep
    # the tuple alive at module scope instead of returning temporary objects.
    _SCENE_SET_ENUM_CACHE = tuple(items)
    return _SCENE_SET_ENUM_CACHE


def _scene_set_choice_updated(settings, context):
    scene = getattr(context, "scene", None) if context is not None else None
    if scene is None:
        return
    choice = settings.scene_set_choice
    for summary in _scene_managed_set_summaries(scene):
        if summary["set_id"] != choice:
            continue
        settings.active_set_id = choice
        settings.active_set_name = summary["set_name"]
        settings.configured_view_count = summary["view_count"]
        settings.enabled_view_count = summary["enabled_view_count"]
        return


def _resolved_scene_set_summary(settings, scene):
    summaries = _scene_managed_set_summaries(scene)
    by_id = {summary["set_id"]: summary for summary in summaries}
    if settings is None:
        return summaries[0] if len(summaries) == 1 else None
    if settings.active_set_id in by_id:
        return by_id[settings.active_set_id]
    try:
        choice = settings.scene_set_choice
    except (AttributeError, TypeError):
        choice = ""
    if choice in by_id:
        return by_id[choice]
    return summaries[0] if len(summaries) == 1 else None


def _safe_name_component(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned[:48] or "set"


def _view_local_matrix(direction, distance, flip_horizontal=False, flip_vertical=False):
    # Each tuple is (position direction, image-right, image-up, outward normal).
    # This makes authored Front/Back/Left/Right/Top/Bottom images read naturally
    # from their named observation side rather than relying on negative distance.
    bases = {
        "front": ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
        "back": ((0.0, -1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
        "left": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (-1.0, 0.0, 0.0)),
        "right": ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        "top": ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "bottom": ((0.0, 0.0, 1.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)),
    }
    position_axis, image_right, image_up, normal = bases[direction]
    position = Vector(position_axis) * distance
    right = Vector(image_right) * (-1.0 if flip_horizontal else 1.0)
    up = Vector(image_up) * (-1.0 if flip_vertical else 1.0)
    normal = Vector(normal)
    return Matrix(
        (
            (right.x, up.x, normal.x, position.x),
            (right.y, up.y, normal.y, position.y),
            (right.z, up.z, normal.z, position.z),
            (0.0, 0.0, 0.0, 1.0),
        )
    )


def _copy_id_property(value):
    if hasattr(value, "to_list"):
        return value.to_list()
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _snapshot_object(obj):
    return {
        "object": obj,
        "data": obj.data,
        "parent": obj.parent,
        "matrix_parent_inverse": obj.matrix_parent_inverse.copy(),
        "matrix_world": obj.matrix_world.copy(),
        "collections": tuple(obj.users_collection),
        "empty_display_type": obj.empty_display_type,
        "empty_display_size": obj.empty_display_size,
        "empty_image_offset": tuple(obj.empty_image_offset),
        "color": tuple(obj.color),
        "use_empty_image_alpha": obj.use_empty_image_alpha,
        "empty_image_depth": obj.empty_image_depth,
        "empty_image_side": obj.empty_image_side,
        "show_in_front": obj.show_in_front,
        "display_type": obj.display_type,
        "hide_select": obj.hide_select,
        "hide_viewport": obj.hide_viewport,
        "hide_render": obj.hide_render,
        "properties": {key: _copy_id_property(obj[key]) for key in obj.keys()},
    }


def _restore_object(snapshot):
    obj = snapshot["object"]
    if bpy.data.objects.get(obj.name) is not obj:
        return
    for collection in tuple(obj.users_collection):
        if collection not in snapshot["collections"]:
            collection.objects.unlink(obj)
    for collection in snapshot["collections"]:
        if collection not in obj.users_collection:
            collection.objects.link(obj)

    obj.empty_display_type = snapshot["empty_display_type"]
    obj.data = snapshot["data"]
    obj.empty_display_size = snapshot["empty_display_size"]
    obj.empty_image_offset = snapshot["empty_image_offset"]
    obj.color = snapshot["color"]
    obj.use_empty_image_alpha = snapshot["use_empty_image_alpha"]
    obj.empty_image_depth = snapshot["empty_image_depth"]
    obj.empty_image_side = snapshot["empty_image_side"]
    obj.show_in_front = snapshot["show_in_front"]
    obj.display_type = snapshot["display_type"]
    obj.hide_select = snapshot["hide_select"]
    obj.hide_viewport = snapshot["hide_viewport"]
    obj.hide_render = snapshot["hide_render"]
    obj.parent = snapshot["parent"]
    obj.matrix_parent_inverse = snapshot["matrix_parent_inverse"]
    obj.matrix_world = snapshot["matrix_world"]
    for key in tuple(obj.keys()):
        del obj[key]
    for key, value in snapshot["properties"].items():
        obj[key] = value


def _interaction_snapshot(context):
    view_layer = context.view_layer
    active = view_layer.objects.active
    return {
        "mode": context.mode,
        "active": active,
        "selected": tuple(context.selected_objects),
    }


def _interaction_matches(context, snapshot):
    return (
        context.mode == snapshot["mode"]
        and context.view_layer.objects.active is snapshot["active"]
        and {obj.as_pointer() for obj in context.selected_objects}
        == {obj.as_pointer() for obj in snapshot["selected"]}
    )


def _restore_interaction(context, snapshot):
    selected = {obj.as_pointer() for obj in snapshot["selected"] if obj.name in bpy.data.objects}
    try:
        for obj in context.view_layer.objects:
            obj.select_set(obj.as_pointer() in selected)
        active = snapshot["active"]
        if active is None or active.name not in context.view_layer.objects:
            context.view_layer.objects.active = None
        else:
            context.view_layer.objects.active = active
    except (RuntimeError, ReferenceError):
        pass


def _load_dedicated_image(view, set_id, created_images):
    try:
        image = bpy.data.images.load(view["image_path"], check_existing=False)
    except RuntimeError as exc:
        raise ReferenceViewError(
            f"Blender could not load {view['direction'].title()} image: {exc}"
        ) from exc
    created_images.append(image)
    image.filepath = _portable_image_filepath(view["image_path"])
    _set_marker(image, "IMAGE", set_id)
    image[_VIEW_KEY] = view["direction"]
    image[_IMAGE_PATH_KEY] = view["image_path"]
    return image


def _view_image(view_obj, view, set_id, created_images):
    existing = view_obj.data
    if isinstance(existing, bpy.types.Image):
        if _normalized_path(existing.filepath) == _normalized_path(view["image_path"]):
            return existing
    return _load_dedicated_image(view, set_id, created_images)


def _blender_units_per_meter(scene):
    scale_length = _finite_number(
        scene.unit_settings.scale_length,
        "Scene unit scale_length",
        minimum=0.0,
        strict_minimum=True,
    )
    return 1.0 / scale_length


def _apply_root(root, manifest, blender_units_per_meter):
    root.parent = None
    root.matrix_world = Matrix.Translation(
        Vector(manifest["origin_meters"]) * blender_units_per_meter
    )
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = max(
        0.05,
        min(
            manifest["placement"]["size_meters"] * blender_units_per_meter * 0.1,
            1.0 * blender_units_per_meter,
        ),
    )
    root.show_in_front = manifest["placement"]["show_in_front"]
    root.display_type = "WIRE"
    root.hide_select = False
    root.hide_viewport = False
    root.hide_render = True
    _set_marker(root, "ROOT", manifest["set_id"])
    root[_SET_NAME_KEY] = manifest["set_name"]
    root[_MANIFEST_KEY] = manifest["manifest_path"]


def _apply_view_object(
    view_obj,
    root,
    view,
    image,
    manifest,
    blender_units_per_meter,
):
    view_obj.empty_display_type = "IMAGE"
    view_obj.data = image
    view_obj.parent = root
    view_obj.matrix_parent_inverse = Matrix.Identity(4)
    view_obj.matrix_basis = _view_local_matrix(
        view["direction"],
        view["distance_meters"] * blender_units_per_meter,
        view["flip_horizontal"],
        view["flip_vertical"],
    )
    view_obj.empty_display_size = view["size_meters"] * blender_units_per_meter
    view_obj.empty_image_offset = (-0.5, -0.5)
    view_obj.color = (1.0, 1.0, 1.0, view["opacity"])
    view_obj.use_empty_image_alpha = True
    view_obj.empty_image_depth = "DEFAULT"
    view_obj.empty_image_side = "FRONT"
    view_obj.show_in_front = manifest["placement"]["show_in_front"]
    view_obj.display_type = "TEXTURED"
    view_obj.hide_select = True
    view_obj.hide_viewport = not view["enabled"]
    view_obj.hide_render = True
    _set_marker(view_obj, "VIEW", manifest["set_id"])
    view_obj[_VIEW_KEY] = view["direction"]
    view_obj[_ENABLED_KEY] = view["enabled"]
    view_obj[_MANIFEST_KEY] = manifest["manifest_path"]
    view_obj[_IMAGE_PATH_KEY] = view["image_path"]


def sync_reference_view_set(context, manifest_path):
    """Create/update a manifest set without changing mode, active object, or selection."""

    manifest = read_reference_view_manifest(manifest_path)
    scene = context.scene
    if scene is None:
        raise ReferenceViewError("No active Scene is available.")
    blender_units_per_meter = _blender_units_per_meter(scene)
    collection = _managed_collection(scene)
    root, existing_views = _managed_set_objects(
        scene,
        manifest["set_id"],
        reject_shared=True,
    )
    interaction = _interaction_snapshot(context)

    created_collection = None
    created_objects = []
    created_images = []
    retired_images = set()
    snapshots = []
    snapshotted = set()

    def remember(obj):
        pointer = obj.as_pointer()
        if pointer not in snapshotted:
            snapshots.append(_snapshot_object(obj))
            snapshotted.add(pointer)

    try:
        if collection is None:
            collection = bpy.data.collections.new(REFERENCE_COLLECTION_NAME)
            created_collection = collection
            _set_marker(collection, "COLLECTION")
            scene.collection.children.link(collection)

        if root is None:
            base = _safe_name_component(manifest["set_id"])
            root = bpy.data.objects.new(f"CD_REF_{base}", None)
            created_objects.append(root)
            collection.objects.link(root)
        else:
            remember(root)
            if collection not in root.users_collection:
                collection.objects.link(root)
        _apply_root(root, manifest, blender_units_per_meter)

        created_count = 1 if root in created_objects else 0
        updated_count = 0 if root in created_objects else 1
        enabled_count = 0
        for view in manifest["views"]:
            direction = view["direction"]
            view_obj = existing_views.get(direction)
            if not view["enabled"] and view_obj is None:
                continue
            if view_obj is None:
                base = _safe_name_component(manifest["set_id"])
                view_obj = bpy.data.objects.new(f"CD_REF_{base}_{direction.upper()}", None)
                created_objects.append(view_obj)
                collection.objects.link(view_obj)
                created_count += 1
            else:
                remember(view_obj)
                if collection not in view_obj.users_collection:
                    collection.objects.link(view_obj)
                updated_count += 1

            if view["enabled"]:
                previous_image = view_obj.data
                image = _view_image(view_obj, view, manifest["set_id"], created_images)
                if isinstance(previous_image, bpy.types.Image) and previous_image is not image:
                    retired_images.add(previous_image)
                enabled_count += 1
            else:
                image = view_obj.data
                if not isinstance(image, bpy.types.Image):
                    raise ReferenceViewError(
                        f"Managed {direction.title()} view has no image to disable."
                    )
            _apply_view_object(
                view_obj,
                root,
                view,
                image,
                manifest,
                blender_units_per_meter,
            )

        if not _interaction_matches(context, interaction):
            raise RuntimeError("Reference sync changed Blender's mode, active object, or selection.")

        # Image replacement is committed at this point. Reclaim only orphaned
        # images that this exact set created; shared or user-owned data is never
        # removed. Cleanup failure is harmless and must not invalidate the set.
        for image in retired_images:
            try:
                if (
                    image.users == 0
                    and _marker_matches(image, "IMAGE", manifest["set_id"])
                    and bpy.data.images.get(image.name) is image
                ):
                    bpy.data.images.remove(image)
            except Exception:
                pass

        return {
            "set_id": manifest["set_id"],
            "set_name": manifest["set_name"],
            "configured_views": sum(
                1 for view in manifest["views"] if view["configured"]
            ),
            "enabled_views": enabled_count,
            "created_objects": created_count,
            "updated_objects": updated_count,
            "collection": collection,
            "root": root,
        }
    except Exception:
        for obj in reversed(created_objects):
            try:
                if bpy.data.objects.get(obj.name) is obj:
                    bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        # Root state is recorded before child state; restoring in that order
        # gives child world matrices their original parent transform again.
        for snapshot in snapshots:
            try:
                _restore_object(snapshot)
            except Exception:
                pass
        for image in reversed(created_images):
            try:
                if image.users == 0 and bpy.data.images.get(image.name) is image:
                    bpy.data.images.remove(image)
            except Exception:
                pass
        if created_collection is not None:
            try:
                if bpy.data.collections.get(created_collection.name) is created_collection:
                    bpy.data.collections.remove(created_collection)
            except Exception:
                pass
        _restore_interaction(context, interaction)
        raise


def clear_reference_view_set(scene, set_id):
    """Delete only exclusively-owned, exactly marked objects for one scene set."""

    try:
        canonical_set_id = str(uuid.UUID(set_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ReferenceViewError("No valid current Reference View Set is selected.")
    if canonical_set_id != set_id:
        set_id = canonical_set_id
    root, views = _managed_set_objects(scene, set_id)
    managed = set(views.values())
    if root is not None:
        managed.add(root)

    protected = set()
    for obj in managed:
        if _object_scene_count(obj) > 1:
            protected.add(obj)
            continue
        if any(child not in managed for child in obj.children):
            protected.add(obj)

    # A protected managed child also protects its parent from deletion.
    changed = True
    while changed:
        changed = False
        for obj in managed - protected:
            if any(child in protected for child in obj.children):
                protected.add(obj)
                changed = True

    image_candidates = set()
    deleted_objects = 0
    for obj in sorted(managed - protected, key=lambda item: item is root):
        if isinstance(obj.data, bpy.types.Image):
            image_candidates.add(obj.data)
        if bpy.data.objects.get(obj.name) is obj:
            bpy.data.objects.remove(obj, do_unlink=True)
            deleted_objects += 1

    deleted_images = 0
    for image in image_candidates:
        if (
            image.users == 0
            and _marker_matches(image, "IMAGE", set_id)
            and bpy.data.images.get(image.name) is image
        ):
            bpy.data.images.remove(image)
            deleted_images += 1

    return {
        "deleted_objects": deleted_objects,
        "deleted_images": deleted_images,
        "protected": tuple(sorted(obj.name for obj in protected)),
    }


def set_reference_view_visibility(scene, set_id, visible):
    _root, views = _managed_set_objects(scene, set_id, reject_shared=True)
    changed = 0
    for obj in views.values():
        should_hide = not bool(visible and obj.get(_ENABLED_KEY, True))
        if obj.hide_viewport != should_hide:
            obj.hide_viewport = should_hide
            changed += 1
    return changed


class CharacterDesignerReferenceState(PropertyGroup):
    manifest_path: StringProperty(
        name="Manifest",
        description="Versioned Reference View Set JSON manifest",
        subtype="FILE_PATH",
        options={"SKIP_SAVE"},
    )
    active_set_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    scene_set_choice: EnumProperty(
        name="Scene Set",
        description="Managed Reference View Set recovered from exact tags in the current Scene",
        items=_scene_set_enum_items,
        update=_scene_set_choice_updated,
        options={"SKIP_SAVE"},
    )
    active_set_name: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    configured_view_count: IntProperty(default=0, min=0, max=6, options={"SKIP_SAVE"})
    enabled_view_count: IntProperty(default=0, min=0, max=6, options={"SKIP_SAVE"})
    discovered_count: IntProperty(default=0, min=0, options={"SKIP_SAVE"})
    last_level: StringProperty(default="NONE", options={"HIDDEN", "SKIP_SAVE"})
    last_message: StringProperty(options={"HIDDEN", "SKIP_SAVE"})


def _reference_settings(context):
    manager = getattr(context, "window_manager", None)
    return getattr(manager, "character_designer_references", None)


def _set_ui_status(settings, level, message):
    if settings is None:
        return
    settings.last_level = level
    settings.last_message = message


class CHARACTERDESIGNER_OT_scan_reference_views(Operator):
    bl_idname = "character_designer.scan_reference_views"
    bl_label = "Scan Reference Views"
    bl_description = "Scan //References/CDesigner/**/reference-views.json and select the newest valid manifest"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        settings = _reference_settings(context)
        discovered = discover_reference_view_manifests()
        valid = []
        errors = []
        for path in discovered:
            try:
                manifest = read_reference_view_manifest(path)
                valid.append((manifest["updated_at_epoch"], os.path.getmtime(path), path))
            except (OSError, ReferenceViewError) as exc:
                errors.append(f"{os.path.basename(os.path.dirname(path))}: {exc}")
        settings.discovered_count = len(valid)
        if not valid:
            message = errors[0] if errors else "No standard Reference View Set manifests were found."
            _set_ui_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        valid_paths = {entry[2] for entry in valid}
        current = _manifest_file_path(settings.manifest_path) if settings.manifest_path else ""
        if current not in valid_paths:
            settings.manifest_path = max(valid)[2]
        message = f"Found {len(valid)} valid Reference View Set{'s' if len(valid) != 1 else ''}."
        if errors:
            message += f" Skipped {len(errors)} invalid."
        _set_ui_status(settings, "SUCCESS", message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_sync_reference_views(Operator):
    bl_idname = "character_designer.sync_reference_views"
    bl_label = "Create / Update Views"
    bl_description = "Validate the manifest, then safely create or update its reference Image Empties"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _reference_settings(context)
        return settings is not None and bool(settings.manifest_path.strip())

    def execute(self, context):
        settings = _reference_settings(context)
        try:
            summary = sync_reference_view_set(context, settings.manifest_path)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            _set_ui_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        settings.manifest_path = summary["root"][_MANIFEST_KEY]
        settings.active_set_id = summary["set_id"]
        settings.active_set_name = summary["set_name"]
        settings.configured_view_count = summary["configured_views"]
        settings.enabled_view_count = summary["enabled_views"]
        try:
            settings.scene_set_choice = summary["set_id"]
        except (TypeError, ValueError):
            # The dynamic Enum may be rebuilding during Scene changes. The
            # exact active id remains available and the next draw recovers it.
            pass
        message = (
            f"{summary['set_name']}: {summary['enabled_views']} enabled / "
            f"{summary['configured_views']} configured."
        )
        _set_ui_status(settings, "SUCCESS", message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_reference_visibility(Operator):
    bl_idname = "character_designer.reference_visibility"
    bl_label = "Set Reference Visibility"
    bl_description = "Show or hide the current managed Reference View Set"
    bl_options = {"REGISTER", "UNDO"}

    visible: BoolProperty(default=True, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        settings = _reference_settings(context)
        return _resolved_scene_set_summary(settings, context.scene) is not None

    def execute(self, context):
        settings = _reference_settings(context)
        active = _resolved_scene_set_summary(settings, context.scene)
        if active is None:
            self.report({"ERROR"}, "Choose one managed Scene set first.")
            return {"CANCELLED"}
        try:
            changed = set_reference_view_visibility(
                context.scene,
                active["set_id"],
                self.visible,
            )
        except ReferenceViewError as exc:
            _set_ui_status(settings, "ERROR", str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        verb = "Shown" if self.visible else "Hidden"
        message = f"{verb} {changed} reference view{'s' if changed != 1 else ''}."
        _set_ui_status(settings, "SUCCESS", message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_clear_reference_views(Operator):
    bl_idname = "character_designer.clear_reference_views"
    bl_label = "Clear Current Set"
    bl_description = "Delete only objects carrying this add-on's exact current-set ownership markers"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _reference_settings(context)
        return _resolved_scene_set_summary(settings, context.scene) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self,
            event=event,
            title="Clear Current Reference View Set?",
            message="Only this add-on's exactly marked objects will be removed.",
            confirm_text="Clear Set",
            icon="QUESTION",
        )

    def execute(self, context):
        settings = _reference_settings(context)
        active = _resolved_scene_set_summary(settings, context.scene)
        if active is None:
            self.report({"ERROR"}, "Choose one managed Scene set first.")
            return {"CANCELLED"}
        try:
            summary = clear_reference_view_set(context.scene, active["set_id"])
        except ReferenceViewError as exc:
            _set_ui_status(settings, "ERROR", str(exc))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if summary["protected"]:
            message = (
                f"Removed {summary['deleted_objects']} objects; kept "
                f"{len(summary['protected'])} with external links or children."
            )
            _set_ui_status(settings, "WARNING", message)
            self.report({"WARNING"}, message)
        else:
            message = f"Removed {summary['deleted_objects']} managed reference objects."
            _set_ui_status(settings, "SUCCESS", message)
            settings.active_set_id = ""
            settings.active_set_name = ""
            settings.configured_view_count = 0
            settings.enabled_view_count = 0
            try:
                settings.property_unset("scene_set_choice")
            except TypeError:
                pass
        return {"FINISHED"}


class CHARACTERDESIGNER_PT_reference_views(Panel):
    bl_label = "Reference Views"
    bl_idname = "CHARACTERDESIGNER_PT_reference_views"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_REFERENCE

    def draw(self, context):
        layout = self.layout
        settings = _reference_settings(context)
        if settings is None:
            layout.label(text="Reference View state is unavailable.", icon="ERROR")
            return

        layout.prop(settings, "manifest_path", text="Manifest")
        scan_row = layout.row(align=True)
        scan_row.operator(
            "character_designer.scan_reference_views",
            text="Scan Standard Folder",
            icon="VIEWZOOM",
        )
        if settings.discovered_count:
            scan_row.label(text=str(settings.discovered_count))
        sync_row = layout.row()
        sync_row.enabled = bool(settings.manifest_path.strip())
        sync_row.operator(
            "character_designer.sync_reference_views",
            text="Create / Update Views",
            icon="IMAGE_BACKGROUND",
        )

        scene_sets = _scene_managed_set_summaries(context.scene)
        if scene_sets:
            layout.separator()
            layout.label(text="Managed Scene Sets", icon="OUTLINER_COLLECTION")
            layout.prop(settings, "scene_set_choice", text="Scene Set")

        active = _resolved_scene_set_summary(settings, context.scene)
        if active is not None:
            box = layout.box()
            box.label(text=active["set_name"], icon="OUTLINER_COLLECTION")
            box.label(
                text=(
                    f"{active['enabled_view_count']} enabled / "
                    f"{active['view_count']} managed"
                )
            )
            row = box.row(align=True)
            show = row.operator(
                "character_designer.reference_visibility",
                text="Show",
                icon="HIDE_OFF",
            )
            show.visible = True
            hide = row.operator(
                "character_designer.reference_visibility",
                text="Hide",
                icon="HIDE_ON",
            )
            hide.visible = False
            clear_row = box.row()
            clear_row.alert = True
            clear_row.operator(
                "character_designer.clear_reference_views",
                text="Clear Current Set",
                icon="TRASH",
            )

        if settings.last_message:
            icon = {
                "ERROR": "ERROR",
                "WARNING": "ERROR",
                "SUCCESS": "CHECKMARK",
            }.get(settings.last_level, "INFO")
            layout.label(text=settings.last_message, icon=icon)


REFERENCE_VIEW_CLASSES = (
    CharacterDesignerReferenceState,
    CHARACTERDESIGNER_OT_scan_reference_views,
    CHARACTERDESIGNER_OT_sync_reference_views,
    CHARACTERDESIGNER_OT_reference_visibility,
    CHARACTERDESIGNER_OT_clear_reference_views,
    CHARACTERDESIGNER_PT_reference_views,
)
