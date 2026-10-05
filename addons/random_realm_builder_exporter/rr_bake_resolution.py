"""Estimate a square bake size from surface area and an adjustable density.

UV utilization is the fraction of the texture occupied by the baked surface.
The default 70% is an estimate, not a measurement of the object's UV islands.
Density presets are authoring targets rather than guarantees of visual quality.
This module has no Blender dependency and never changes scene or image data.
"""

import math


SUPPORTED_SIZES = (512, 1024, 2048, 4096)
DENSITY_PRESETS = {"LOW": 64.0, "BALANCED": 128.0, "HIGH": 256.0}
DEFAULT_UV_UTILIZATION = 0.7


def _positive_finite(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite positive number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{label} must be a finite positive number.") from error
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a finite positive number.")
    return number


def area_in_square_meters(world_area, meters_per_world_unit=1.0):
    """Convert area measured after world transforms, including scene unit scale.

    The caller supplies square world units and meters per one world unit. Object
    scale belongs in the measured world area; it must not be applied here again.
    """
    area = _positive_finite(world_area, "World surface area")
    scale = _positive_finite(meters_per_world_unit, "Meters per world unit")
    converted = area * scale * scale
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError("Surface area in square meters is outside the supported numeric range.")
    return converted


def _supported_sizes(values):
    try:
        sizes = tuple(values)
    except TypeError as error:
        raise ValueError("Provide at least one supported bake size.") from error
    if not sizes or any(isinstance(size, bool) or not isinstance(size, int)
                        or size not in SUPPORTED_SIZES for size in sizes):
        raise ValueError("Bake sizes must use the supported 512, 1024, 2048, or 4096 pixel tiers.")
    return tuple(sorted(set(sizes)))


def recommend_bake_resolution(area_m2, target_texels_per_meter=None, *, preset="BALANCED",
                              uv_utilization=DEFAULT_UV_UTILIZATION,
                              uv_utilization_estimated=True, supported_sizes=SUPPORTED_SIZES):
    """Return the next supported square size, or an explicit capped result.

    required_pixels = sqrt(area_m2 / UV_utilization) * target_texels_per_meter.
    achieved_density = size * sqrt(UV_utilization / area_m2).

    Explicit density overrides the preset. ``meets_target`` applies to this
    area/utilization model; uneven island scaling and viewing distance still
    affect appearance. No value in this result is a visual-quality guarantee.
    """
    area = _positive_finite(area_m2, "Baked surface area in square meters")
    utilization = _positive_finite(uv_utilization, "UV utilization")
    if utilization > 1:
        raise ValueError("UV utilization must be greater than zero and at most one.")
    if not isinstance(uv_utilization_estimated, bool):
        raise ValueError("UV utilization estimated must be True or False.")
    from_preset = target_texels_per_meter is None
    preset_name = str(preset).strip().upper() if from_preset else None
    if from_preset:
        if preset_name not in DENSITY_PRESETS:
            raise ValueError("Choose the LOW, BALANCED, or HIGH density preset, or supply a target density.")
        density = DENSITY_PRESETS[preset_name]
    else:
        density = _positive_finite(target_texels_per_meter, "Target texels per meter")
    sizes = _supported_sizes(supported_sizes)
    # Separate square roots avoid overflow in area / utilization for otherwise
    # finite inputs. The final dimension still needs a finite representable value.
    required_exact = math.sqrt(area) / math.sqrt(utilization) * density
    if not math.isfinite(required_exact) or required_exact <= 0:
        raise ValueError("Required bake size is outside the supported numeric range.")
    # Algebraically exact tier boundaries can differ by a few floating-point
    # ulps. Only normalize that rounding noise, not a meaningful shortfall.
    nearest = round(required_exact)
    if nearest > 0 and abs(required_exact - nearest) <= math.ulp(required_exact) * 8:
        required_exact = float(nearest)
    required = math.ceil(required_exact)
    recommended = next((size for size in sizes if size >= required), sizes[-1])
    capped = required > sizes[-1]
    achieved = recommended * math.sqrt(utilization) / math.sqrt(area)
    if not math.isfinite(achieved) or achieved <= 0:
        raise ValueError("Achieved texel density is outside the supported numeric range.")
    guidance = ""
    if capped:
        guidance = (
            f"The {recommended}px limit does not reach the target density. "
            "Split the surface across multiple bake textures or lower the target density."
        )
    return {
        "area_m2": area,
        "target_texels_per_meter": density,
        "density_preset": preset_name,
        "uv_utilization": utilization,
        "uv_utilization_estimated": uv_utilization_estimated,
        "required_resolution": required,
        "recommended_resolution": recommended,
        "achieved_texels_per_meter": achieved,
        "meets_target": not capped,
        "capped": capped,
        "supported_sizes": sizes,
        "guidance": guidance,
    }
