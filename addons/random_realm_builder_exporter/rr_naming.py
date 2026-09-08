import re

try:
    from .rr_builder_constants import ICON_SIZES, PBR_BAKE_SIZES, SUPPORTED_TYPES
except ImportError:
    from rr_builder_constants import ICON_SIZES, PBR_BAKE_SIZES, SUPPORTED_TYPES


def sanitize_id(name):
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean or "BuilderAsset"


def sanitize_optional_id(name):
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", (name or "").strip())
    return re.sub(r"_+", "_", clean).strip("_")


def infer_asset_type(asset_id):
    lowered = asset_id.lower()
    if lowered.startswith("glasswall_"):
        return "InnerWall"
    for asset_type in SUPPORTED_TYPES:
        if lowered.startswith(asset_type.lower()):
            return asset_type
    return "Unknown"


def clamp_icon_size(size):
    try:
        size = int(size)
    except (TypeError, ValueError):
        return 512

    return min(ICON_SIZES, key=lambda value: abs(value - size))


def step_icon_size(size, direction):
    size = clamp_icon_size(size)
    index = ICON_SIZES.index(size)
    index = max(0, min(len(ICON_SIZES) - 1, index + direction))
    return ICON_SIZES[index]


def clamp_pbr_bake_size(size):
    try:
        size = int(size)
    except (TypeError, ValueError):
        return 1024

    return min(PBR_BAKE_SIZES, key=lambda value: abs(value - size))


def step_pbr_bake_size(size, direction):
    size = clamp_pbr_bake_size(size)
    index = PBR_BAKE_SIZES.index(size)
    index = max(0, min(len(PBR_BAKE_SIZES) - 1, index + direction))
    return PBR_BAKE_SIZES[index]


def strip_blender_copy_suffix(name):
    return re.sub(r"\.\d{3}$", "", name or "")


def normalized_name_key(name):
    value = strip_blender_copy_suffix(name)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return value.lower()


def is_collision_helper_name(name):
    value = strip_blender_copy_suffix(name)
    lowered = value.lower()
    if "collider" in lowered or "collision" in lowered:
        return True

    return re.search(r"(^|[_\-\s.])col($|[_\-\s.])", value, re.IGNORECASE) is not None


def collision_target_name_key(name):
    value = strip_blender_copy_suffix(name)
    value = re.sub(r"(^|[_\-\s.])(collider|collision|col)($|[_\-\s.])", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return value.lower()


__all__ = [name for name, value in globals().items() if callable(value) and not name.startswith("_")]
