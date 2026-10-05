"""Saved, source-owned Dress settings; no Blender dependency or pose writes.

Settings describe the native Cloth surface, rather than claiming equivalence
with another solver. Recovery is a soft waist-relative goal through the
native pin group. Connected distance, shear and bending remain Cloth springs.
"""
import copy
import json
import math

PROFILE_KEY = "character_designer_dress_motion_v1"
VERSION = 1
CAPABILITIES = ("PHYSICS", "MANUAL", "BOTH")
MODES = ("AUTOMATIC", "MANUAL")
DEFAULTS = {
    "quality": 8, "mass": 0.15, "stretch": 25.0, "shear": 10.0,
    "bend": 0.8, "damping": 5.0, "bend_damping": 1.0, "air_damping": 3.0,
    "gravity": 1.0, "waist_depth": 0.0, "transition": 0.35,
    "recovery": 0.0, "pin_stiffness": 1.0,
    "collision_quality": 4, "collision_margin": 0.008,
    "self_collision": False, "self_margin": 0.008, "self_friction": 5.0,
}
RANGES = {
    "quality": (1, 32), "mass": (0.001, 5.0), "stretch": (0.0, 100.0),
    "shear": (0.0, 100.0), "bend": (0.0, 10.0), "damping": (0.0, 50.0),
    "bend_damping": (0.0, 50.0), "air_damping": (0.0, 10.0),
    "gravity": (0.0, 2.0), "waist_depth": (0.0, 0.2), "transition": (0.0, 1.0),
    "recovery": (0.0, 1.0), "pin_stiffness": (0.0, 50.0),
    "collision_quality": (1, 16), "collision_margin": (0.0001, 0.05),
    "self_margin": (0.0001, 5.0), "self_friction": (0.0, 80.0),
}
INTEGERS = {"quality", "collision_quality"}


class DressMotionError(ValueError):
    pass


def settings(values, *, partial=False):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise DressMotionError("Unknown Dress tuning settings.")
    if not partial and set(values) != set(DEFAULTS):
        raise DressMotionError("The saved Dress tuning settings are incomplete.")
    result = {}
    for key, value in values.items():
        if key == "self_collision":
            if type(value) is not bool:
                raise DressMotionError("Self collision must be enabled or disabled.")
        else:
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not RANGES[key][0] <= value <= RANGES[key][1]
                    or (key in INTEGERS and type(value) is not int)):
                raise DressMotionError(f"Invalid Dress {key.replace('_', ' ')}.")
            value = int(value) if key in INTEGERS else float(value)
        result[key] = value
    return result


def native_settings(values):
    """Accept only roundoff at limits when reading Blender's float32 RNA."""
    values = dict(values)
    for key, limits in RANGES.items():
        value = values.get(key)
        if key in INTEGERS or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for bound in limits:
            if abs(value - bound) <= max(1.0e-12, abs(bound) * 2.0e-6):
                values[key] = bound
                break
    return settings(values)


def _identity(record):
    if (not isinstance(record, dict) or not isinstance(record.get("owner"), str)
            or not record["owner"] or type(record.get("chain_count")) is not int
            or type(record.get("segment_count")) is not int
            or not 3 <= record["chain_count"] <= 32 or not 2 <= record["segment_count"] <= 12):
        raise DressMotionError("Create a valid owned Dress setup first.")
    return {key: record[key] for key in ("owner", "chain_count", "segment_count")}


def validate(profile, record):
    expected = {"version", "identity", "capability", "mode", "settings"}
    if (not isinstance(profile, dict) or set(profile) != expected
            or type(profile["version"]) is not int or profile["version"] != VERSION
            or profile["identity"] != _identity(record)):
        raise DressMotionError("The saved Dress profile belongs to a different or changed setup.")
    if profile["capability"] not in CAPABILITIES or profile["mode"] not in MODES:
        raise DressMotionError("Unknown Dress generation or motion mode.")
    if ((profile["capability"] == "MANUAL" and profile["mode"] != "MANUAL")
            or (profile["capability"] == "PHYSICS" and profile["mode"] != "AUTOMATIC")):
        raise DressMotionError("The requested motion mode was not enabled for this Dress setup.")
    settings(profile["settings"])
    return copy.deepcopy(profile)


def fresh(record, *, capability="BOTH", mode="AUTOMATIC", values=None):
    return validate({"version": VERSION, "identity": _identity(record),
                     "capability": capability, "mode": mode,
                     "settings": settings(dict(DEFAULTS) if values is None else values)}, record)


def read(source, record):
    raw = source.get(PROFILE_KEY)
    if raw is None:
        return None
    try:
        return validate(json.loads(raw), record)
    except (ValueError, TypeError, KeyError) as error:
        raise DressMotionError("The saved Dress motion profile cannot be read; preserve its record.") from error


def write(source, profile, record):
    checked = validate(profile, record)
    raw = json.dumps(checked, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    old = source.get(PROFILE_KEY)
    try:
        source[PROFILE_KEY] = raw
        if source.get(PROFILE_KEY) != raw:
            raise DressMotionError("The Dress motion profile could not be saved.")
    except Exception:
        if old is None:
            if PROFILE_KEY in source:
                del source[PROFILE_KEY]
        else:
            source[PROFILE_KEY] = old
        raise
    return checked


def edited(profile, record, changes=None, *, mode=None, capability=None):
    updated = validate(profile, record)
    updated["settings"].update(settings(changes or {}, partial=True))
    if mode is not None:
        updated["mode"] = mode
    if capability is not None:
        updated["capability"] = capability
    return validate(updated, record)


def pin_weights(values, rows, columns):
    """Fixed waist plus a soft depth-decaying goal; seam columns agree exactly.

    Zero recovery and zero waist depth reproduce the original 1/.35/0 pin
    pattern. Increasing recovery adds weak native goals, never full pinning
    the moving hem. Goals follow only the waist, rather than thigh bones.
    """
    values = settings(values)
    if (type(rows) is not int or type(columns) is not int
            or rows < 3 or columns < 3):
        raise DressMotionError("Invalid Dress Cloth grid dimensions.")
    fixed_last = math.floor(values["waist_depth"] * (rows - 1))
    result = []
    for row in range(rows):
        if row <= fixed_last:
            weight = 1.0
        elif row == fixed_last + 1:
            weight = values["transition"]
        else:
            depth = (row - fixed_last) / (rows - 1 - fixed_last)
            weight = values["recovery"] * (0.005 + 0.075 * (1.0 - depth) ** 2)
        result.extend([weight] * columns)
    return result


def surface_pin_weights(values, fit, rows, columns):
    """Map the unchanged virtual pin grid onto saved exact raw ring indices.

    Ring progression and column order are generation evidence, not a nearest
    vertex correspondence. The source skin groups are never part of this map.
    """
    grid = pin_weights(values, rows, columns)
    if not isinstance(fit, dict) or not isinstance(fit.get("vertices"), (list, tuple)):
        raise DressMotionError("The saved Dress surface Basis is incomplete.")
    count = len(fit["vertices"])
    rings, progress = fit.get("rings"), fit.get("ring_t")
    if (not isinstance(rings, (list, tuple)) or not isinstance(progress, (list, tuple))
            or len(rings) < 3 or len(rings) != len(progress)
            or any(not isinstance(ring, (list, tuple)) or len(ring) < 3 for ring in rings)
            or any(type(index) is not int or not 0 <= index < count for ring in rings for index in ring)
            or sorted(index for ring in rings for index in ring) != list(range(count))
            or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
                   for value in progress)
            or progress[0] != 0 or progress[-1] != 1
            or any(a >= b for a, b in zip(progress, progress[1:]))):
        raise DressMotionError("The saved Dress surface rings do not prove an exact ordered raw-index map.")
    result = [None] * count
    for ring, t in zip(rings, progress):
        vertical = t * (rows - 1)
        lo = min(math.floor(vertical), rows - 1)
        hi = min(lo + 1, rows - 1)
        blend = vertical - lo
        for column, index in enumerate(ring):
            angular = column * columns / len(ring)
            left = math.floor(angular) % columns
            right, fraction = (left + 1) % columns, angular - math.floor(angular)
            lower = grid[lo * columns + left] * (1 - fraction) + grid[lo * columns + right] * fraction
            upper = grid[hi * columns + left] * (1 - fraction) + grid[hi * columns + right] * fraction
            result[index] = lower * (1 - blend) + upper * blend
    if (any(value is None or not math.isfinite(value) or not 0 <= value <= 1 for value in result)
            or any(result[index] != 1 for index in rings[0])):
        raise DressMotionError("The exact Dress surface pin map lost its fixed waist or valid weights.")
    return result
