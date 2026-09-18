"""Saved HDRI lighting for icons, independent of later viewport changes."""

from contextlib import contextmanager
import json
import math
import os

import bpy


PROFILE_KEY = "rr_icon_lighting_profile"
PROFILE_VERSION = 1


class ProfileError(RuntimeError):
    """The requested icon lighting cannot be reproduced safely."""


def _number(profile, key, *, minimum=None, maximum=None):
    value = profile.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"Icon lighting '{key}' must be a finite number.")
    try:
        value = float(value)
    except OverflowError as exception:
        raise ProfileError(f"Icon lighting '{key}' must be a finite number.") from exception
    if not math.isfinite(value):
        raise ProfileError(f"Icon lighting '{key}' must be a finite number.")
    if minimum is not None and value < minimum:
        raise ProfileError(f"Icon lighting '{key}' must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ProfileError(f"Icon lighting '{key}' must be at most {maximum}.")
    return value


def validate_profile(profile, *, check_file=True):
    """Return a new canonical snapshot; do not write any Blender data."""
    if not isinstance(profile, dict):
        raise ProfileError("Icon lighting must be a versioned profile object.")
    if type(profile.get("version")) is not int or profile["version"] != PROFILE_VERSION:
        raise ProfileError("Unsupported icon lighting profile version; capture the viewport again.")
    mode = profile.get("mode")
    if mode == "LEGACY":
        return {"version": PROFILE_VERSION, "mode": "LEGACY"}
    if mode != "HDRI":
        raise ProfileError("Icon lighting mode must be HDRI or LEGACY.")
    if profile.get("world_space") is not True:
        raise ProfileError(
            "Viewport HDRI capture currently requires World Space Lighting. "
            "Enable it in Material Preview and capture again."
        )
    path = profile.get("hdri_path")
    if not isinstance(path, str) or not path.strip() or "\x00" in path or not os.path.isabs(path):
        raise ProfileError("Icon HDRI must have an absolute image path; capture the viewport again.")
    path = os.path.normpath(path)
    name = profile.get("studio_name")
    if not isinstance(name, str) or not name.strip():
        raise ProfileError("Icon lighting is missing the captured studio light name.")
    # RNA accepts finite single-precision values for these settings.
    maximum_float = 3.4028234663852886e38
    normalized = {
        "version": PROFILE_VERSION,
        "mode": "HDRI",
        "studio_name": name,
        "hdri_path": path,
        "rotation_z": _number(profile, "rotation_z", minimum=-math.pi - 1e-6, maximum=math.pi + 1e-6),
        "intensity": _number(profile, "intensity", minimum=0.0, maximum=maximum_float),
        "world_space": True,
        "sun_threshold": _number(profile, "sun_threshold", minimum=0.0, maximum=maximum_float),
    }
    if check_file and not os.path.isfile(path):
        raise ProfileError(f"Captured icon HDRI is missing: {path}. Capture an available studio light again.")
    return normalized


def serialize_profile(profile):
    return json.dumps(validate_profile(profile), ensure_ascii=True, sort_keys=True, allow_nan=False)


def read_profile(root, scene=None, *, check_file=True):
    """Resolve explicit asset > scene default > legacy, without inspecting a viewport."""
    for owner in (root, scene):
        if owner is None or PROFILE_KEY not in owner:
            continue
        raw = owner[PROFILE_KEY]
        if not isinstance(raw, str):
            raise ProfileError("Saved icon lighting is not JSON text; capture the viewport again.")
        try:
            profile = json.loads(raw)
        except (TypeError, ValueError) as exception:
            raise ProfileError("Saved icon lighting is invalid JSON; capture the viewport again.") from exception
        return validate_profile(profile, check_file=check_file)
    return {"version": PROFILE_VERSION, "mode": "LEGACY"}


def write_profile(owner, profile):
    """Validate completely before replacing the owner's single saved snapshot."""
    normalized = validate_profile(profile)
    if owner is None or not getattr(owner, "is_editable", True):
        raise ProfileError("The selected icon lighting owner is read-only or unavailable.")
    payload = json.dumps(normalized, ensure_ascii=True, sort_keys=True, allow_nan=False)
    owner[PROFILE_KEY] = payload
    return normalized


def _material_view(context):
    candidates = []
    seen = set()

    def add(area, scene):
        if area is None or getattr(area, "type", None) != "VIEW_3D":
            return
        pointer = getattr(area, "as_pointer", None)
        key = pointer() if pointer is not None else id(area)
        if key in seen:
            return
        seen.add(key)
        space = area.spaces.active
        if getattr(getattr(space, "shading", None), "type", None) == "MATERIAL":
            candidates.append((area, scene, space.shading))

    active = getattr(context, "area", None)
    scene = getattr(context, "scene", None)
    add(active, scene)
    if candidates:
        return candidates[0]
    screen = getattr(context, "screen", None)
    for area in getattr(screen, "areas", ()):
        add(area, scene)
    manager = getattr(context, "window_manager", None)
    for window in getattr(manager, "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            add(area, getattr(window, "scene", scene))
    if not candidates:
        raise ProfileError("Open a 3D View in Material Preview, then capture its lighting.")
    return max(candidates, key=lambda entry: entry[0].width * entry[0].height)


def capture_profile(context):
    """Snapshot the active Material Preview, or the largest available Material Preview."""
    _area, scene, shading = _material_view(context)
    if shading.use_scene_world or shading.use_scene_lights:
        raise ProfileError(
            "Turn off Scene World and Scene Lights in Material Preview before capturing HDRI lighting."
        )
    if not shading.use_studiolight_view_rotation:
        raise ProfileError(
            "Enable World Space Lighting in Material Preview before capturing HDRI lighting."
        )
    studio = shading.selected_studio_light
    if studio is None or getattr(studio, "type", None) != "WORLD":
        raise ProfileError("Material Preview has no usable environment studio light.")
    path = getattr(studio, "path", "")
    if not path:
        raise ProfileError("The selected studio light has no image path.")
    intensity = float(shading.studiolight_intensity)
    source_world = getattr(scene, "world", None)
    threshold = (
        float(source_world.sun_threshold)
        if source_world is not None
        else float(bpy.types.World.bl_rna.properties["sun_threshold"].default)
    )
    return validate_profile({
        "version": PROFILE_VERSION,
        "mode": "HDRI",
        "studio_name": studio.name,
        "hdri_path": os.path.normpath(bpy.path.abspath(path)),
        "rotation_z": float(shading.studiolight_rotate_z),
        "intensity": intensity,
        "world_space": True,
        # EEVEE's Lookdev sun extraction scales the source threshold by HDRI intensity.
        "sun_threshold": threshold * intensity,
    })


@contextmanager
def temporary_world(scene, profile):
    """Apply one saved HDRI World; restore the original and release only owned data."""
    normalized = validate_profile(profile)
    if normalized["mode"] == "LEGACY":
        yield None
        return

    original_world = scene.world
    world = None
    image = None
    assigned = False
    operation_error = None
    try:
        # Never change or remove a user-owned image, even when it uses this same path.
        image = bpy.data.images.load(normalized["hdri_path"], check_existing=False)
        if image.size[0] <= 0 or image.size[1] <= 0:
            raise ProfileError(f"Captured icon HDRI could not be decoded: {normalized['hdri_path']}")
        image.alpha_mode = "NONE"
        # Blender decodes EXR/HDR as linear and assigns its color space from the file.
        world = bpy.data.worlds.new("RR_IconHDRI_Temporary")
        world.use_nodes = True
        world.sun_threshold = normalized["sun_threshold"]
        nodes = world.node_tree.nodes
        links = world.node_tree.links
        nodes.clear()
        coordinate = nodes.new("ShaderNodeTexCoord")
        rotate = nodes.new("ShaderNodeVectorRotate")
        rotate.rotation_type = "Z_AXIS"
        rotate.invert = False
        rotate.inputs["Angle"].default_value = normalized["rotation_z"]
        environment = nodes.new("ShaderNodeTexEnvironment")
        environment.image = image
        environment.projection = "EQUIRECTANGULAR"
        background = nodes.new("ShaderNodeBackground")
        background.inputs["Strength"].default_value = normalized["intensity"]
        output = nodes.new("ShaderNodeOutputWorld")
        links.new(coordinate.outputs["Generated"], rotate.inputs["Vector"])
        links.new(rotate.outputs["Vector"], environment.inputs["Vector"])
        links.new(environment.outputs["Color"], background.inputs["Color"])
        links.new(background.outputs["Background"], output.inputs["Surface"])
        # RNA's use_studiolight_view_rotation=True means world-fixed lighting.
        # Lookdev uses this positive Z angle for environment texture sampling.
        assigned = True
        scene.world = world
        yield world
    except BaseException as exception:
        operation_error = exception
        raise
    finally:
        cleanup_errors = []

        def attempt(action):
            try:
                action()
            except Exception as exception:
                cleanup_errors.append(exception)

        if assigned:
            attempt(lambda: setattr(scene, "world", original_world))

        def remove_world():
            if world.users:
                raise ProfileError("Temporary icon World is still in use after restoration.")
            bpy.data.worlds.remove(world)

        def remove_image():
            if image.users:
                raise ProfileError("Temporary icon HDRI is still in use after World cleanup.")
            bpy.data.images.remove(image)

        if world is not None:
            attempt(remove_world)
        if image is not None:
            attempt(remove_image)
        if cleanup_errors:
            message = "Icon HDRI cleanup failed: " + "; ".join(str(error) for error in cleanup_errors)
            if operation_error is not None:
                if hasattr(operation_error, "add_note"):
                    operation_error.add_note(message)
                print("[RR Helper] " + message)
            else:
                raise ProfileError(message) from cleanup_errors[0]
