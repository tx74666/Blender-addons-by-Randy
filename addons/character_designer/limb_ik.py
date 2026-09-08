"""One-click, owner-scoped Arm/Leg IK controls for humanoid deform skeletons.

The module deliberately keeps analysis read-only (WindowManager state only) and
keeps generated controls inside the source Armature.  Constraint ownership is
recorded on PoseBones because Blender constraints do not support ID properties.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, replace

import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Euler, Matrix, Vector

from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_RIG, active_ui_page


OWNER_KEY = "character_designer_owner"
OWNER_VALUE = "limb_ik"
VERSION_KEY = "character_designer_limb_ik_version"
RIG_ID_KEY = "character_designer_limb_ik_rig_id"
ROLE_KEY = "character_designer_limb_ik_role"
KIND_KEY = "character_designer_limb_ik_kind"
SIDE_KEY = "character_designer_limb_ik_side"
CHAIN_KEY = "character_designer_limb_ik_chain"
ARMATURE_ID_KEY = "character_designer_limb_ik_armature_id"
CONSTRAINT_REGISTRY_KEY = "character_designer_limb_ik_constraints"
SCHEMA_KEY = "character_designer_limb_ik_schema"
POLE_DIRECTION_KEY = "character_designer_limb_ik_pole_direction"
POLE_CONFIGURED_DIRECTION_KEY = "character_designer_limb_ik_configured_pole_direction"
DIRECT_REST_KEY = "character_designer_limb_ik_direct_rest"
SOURCE_WIDGETS_KEY = "character_designer_limb_ik_source_widgets"
AUTO_ALIGN_KEY = "character_designer_limb_ik_auto_align"
TARGET_ROTATION_VERSION_KEY = "character_designer_limb_ik_target_rotation_version"
CONTROL_VISUAL_DEFAULT_KEY = "character_designer_limb_ik_control_visual_default"
CONTROL_SHAPE_STYLE_KEY = "character_designer_limb_ik_control_shape_style"
RIG_VERSION = 1
TARGET_ROTATION_VERSION = 1
LEGACY_SCHEMA = 1
ENHANCED_SCHEMA = 2
ROLL_DECOUPLED_SCHEMA = 3
LEGACY_DIRECT_PREROLL_SCHEMA = 4
DIRECT_PREROLL_SCHEMA = 5
CURRENT_SCHEMA = ROLL_DECOUPLED_SCHEMA
CONTROL_COLLECTION_NAME = "Randy Controls"
WIDGET_COLLECTION_NAME = "Randy_Rig_Widgets"
MASTER_NAME = "CTRL_master"
WIDGET_NAMES = {
    "HAND": "WGT_Randy_HandIK",
    "FOOT": "WGT_Randy_FootIK",
    "FOOT_L": "WGT_Randy_FootIK.L",
    "FOOT_R": "WGT_Randy_FootIK.R",
    "POLE": "WGT_Randy_Pole",
    "POLE_ARROW": "WGT_Randy_PoleArrow",
    "POLE_LINE": "WGT_Randy_PoleLine",
    "HEEL": "WGT_Randy_HeelRoll",
    "MASTER": "WGT_Randy_Master",
    "FINGER": "WGT_Randy_Finger",
    "SHOULDER": "WGT_Randy_Shoulder",
    "CONTROL_ARROW": "WGT_Randy_ControlArrow",
    "CONTROL_SPHERE": "WGT_Randy_ControlSphere",
}
EPSILON = 1.0e-8
POLE_DIRECTION_PARALLEL_RATIO = 1.0e-4
POLE_MIN_REACH_DEFICIT_RATIO = 1.0e-6
DIRECT_PREROLL_RESULT_VERSION = 1
LEGACY_SOURCE_WIDGETS_VERSION = 1
SOURCE_WIDGETS_VERSION = 2
DIRECT_PREROLL_ALIGNMENT_DEGREES = 5.0
FOOT_WIDGET_MARGIN_RATIO = 0.06
FOOT_WIDGET_SOLE_GAP_RATIO = 0.05
FOOT_WIDGET_WEIGHT_THRESHOLD = 0.01
POLE_GUIDE_HANDLER_KEY = "character_designer_limb_ik_pole_guide_handler"
CONTROL_VISUAL_DEFAULT_VERSION = 1
CONTROL_VISUAL_ROLES = frozenset({"MASTER", "HAND_IK", "FOOT_IK", "POLE", "HEEL_ROLL"})
CONTROL_SHAPE_STYLES = frozenset({"DEFAULT", "ARROW", "SPHERE"})
CONTROL_SHAPE_WIDGET_KINDS = frozenset({"CONTROL_ARROW", "CONTROL_SPHERE"})
CONTROL_SHAPE_STYLE_LABELS = {
    "DEFAULT": "Rig Default",
    "ARROW": "Arrow",
    "SPHERE": "Sphere Wire",
}

_POLE_GUIDE_DRAW_HANDLE = None
_POLE_GUIDE_SHADER = None

SIDES = ("L", "R")
KINDS = ("ARM", "LEG")
ROLES = ("upper", "lower", "end")
SELECTED_LIMBS = {
    "LEFT_ARM": ("ARM", "L"),
    "RIGHT_ARM": ("ARM", "R"),
    "LEFT_LEG": ("LEG", "L"),
    "RIGHT_LEG": ("LEG", "R"),
}
DEFAULT_POLE_DIRECTIONS = {
    # Character Designer's humanoid convention is Armature-local -Y forward:
    # elbows bend behind the character (+Y), while knees bend forward (-Y).
    ("ARM", "L"): (0.0, 1.0, 0.0),
    ("ARM", "R"): (0.0, 1.0, 0.0),
    ("LEG", "L"): (0.0, -1.0, 0.0),
    ("LEG", "R"): (0.0, -1.0, 0.0),
}

LIMB_SPEC = {
    "ARM": {
        "upper": (("upperarm", 8), ("arm", 5)),
        "lower": (("forearm", 8), ("lowerarm", 8)),
        "end": (("hand", 8), ("wrist", 7)),
        "target": "CTRL_hand_IK.{side}",
        "pole": "CTRL_elbow_pole.{side}",
        "line": "VIS_elbow_pole_line.{side}",
        "display": "MCH_elbow_pole_aim.{side}",
        "mch_upper": "MCH_upper_arm_IK.{side}",
        "mch_lower": "MCH_forearm_IK.{side}",
        "ori_upper": "ORI_upper_arm_IK.{side}",
        "ori_lower": "ORI_forearm_IK.{side}",
        "target_role": "HAND_IK",
    },
    "LEG": {
        "upper": (("thigh", 8), ("upperleg", 8)),
        "lower": (("shin", 8), ("calf", 8), ("lowerleg", 8)),
        "end": (("foot", 8), ("ankle", 7)),
        "target": "CTRL_foot_IK.{side}",
        "pole": "CTRL_knee_pole.{side}",
        "line": "VIS_knee_pole_line.{side}",
        "display": "MCH_knee_pole_aim.{side}",
        "mch_upper": "MCH_thigh_IK.{side}",
        "mch_lower": "MCH_shin_IK.{side}",
        "ori_upper": "ORI_thigh_IK.{side}",
        "ori_lower": "ORI_shin_IK.{side}",
        "heel": "CTRL_heel_roll.{side}",
        "solver_target": "MCH_foot_target.{side}",
        "target_role": "FOOT_IK",
    },
}

_EXCLUDED_TOKENS = frozenset(
    {"mch", "org", "ctrl", "vis", "wgt", "ik", "fk", "twist", "helper", "corrective", "tweak"}
)
_FINGER_TOKENS = frozenset({"thumb", "index", "middle", "ring", "pinky", "little"})
_SHOULDER_TOKENS = frozenset({"shoulder", "clavicle", "clav", "collar"})


class LimbIKError(ValueError):
    """An actionable, fail-closed limb-rig problem."""


@dataclass(frozen=True)
class LimbChain:
    kind: str
    side: str
    upper: str
    lower: str
    end: str
    score: float = 0.0
    warning: str = ""

    @property
    def names(self):
        return (self.upper, self.lower, self.end)


@dataclass
class LimbPlan:
    chain: LimbChain
    rig_id: str
    target_name: str
    pole_name: str
    start: Vector
    joint: Vector
    end: Vector
    target_head: Vector
    target_tail: Vector
    target_z: Vector
    pole_head: Vector
    pole_tail: Vector
    pole_angle: float
    pole_warning: str
    pole_direction: Vector
    pole_joint: Vector
    desired_upper_matrix: Matrix
    desired_lower_matrix: Matrix
    desired_end_matrix: Matrix
    target_basis: Matrix
    pole_basis: Matrix
    line_name: str = ""
    display_name: str = ""
    heel_name: str = ""
    solver_target_name: str = ""
    mch_upper_name: str = ""
    mch_lower_name: str = ""
    ori_upper_name: str = ""
    ori_lower_name: str = ""
    mechanism_rest: dict | None = None
    mechanism_pose: dict | None = None
    direct_rest: dict | None = None
    heel_head: Vector | None = None
    heel_tail: Vector | None = None
    heel_z: Vector | None = None
    line_head: Vector | None = None
    line_tail: Vector | None = None
    configured_pole_direction: Vector | None = None
    persist_pole_direction: bool = True
    preserve_pole_angle: bool = False
    auto_align: bool = False


def _is_enhanced_schema(schema):
    return schema in {ENHANCED_SCHEMA, ROLL_DECOUPLED_SCHEMA}


def _is_roll_decoupled_schema(schema):
    return schema == ROLL_DECOUPLED_SCHEMA


def _is_direct_preroll_schema(schema):
    return schema in {LEGACY_DIRECT_PREROLL_SCHEMA, DIRECT_PREROLL_SCHEMA}


def _uses_dynamic_pole_display(schema):
    """Return whether Pole geometry is oriented by an owned hidden helper."""

    return _is_enhanced_schema(schema) or schema == DIRECT_PREROLL_SCHEMA


def _stores_explicit_schema(schema):
    return schema != LEGACY_SCHEMA


def _default_target_rotation_version(schema):
    """Return the rotation feature level authored by a newly built schema."""

    return (
        TARGET_ROTATION_VERSION
        if schema in {CURRENT_SCHEMA, DIRECT_PREROLL_SCHEMA}
        else 0
    )


def _armature_poll(_self, obj):
    return obj is not None and obj.type == "ARMATURE"


def _settings(context):
    return getattr(getattr(context, "window_manager", None), "character_designer_limb_ik", None)


def _set_status(settings, level, message):
    if settings is not None:
        settings.last_level = level
        settings.last_message = message


def _field_name(kind, side, role):
    return f"{'left' if side == 'L' else 'right'}_{kind.lower()}_{role}"


def _pole_direction_field(kind, side):
    return f"{'left' if side == 'L' else 'right'}_{kind.lower()}_pole_direction"


def _finite_direction(value, label="Pole Direction"):
    try:
        components = tuple(float(component) for component in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LimbIKError(f"{label} must contain three finite Armature-local XYZ values.") from exc
    if len(components) != 3 or any(not math.isfinite(component) for component in components):
        raise LimbIKError(f"{label} must contain three finite Armature-local XYZ values.")
    direction = Vector(components)
    if direction.length <= EPSILON:
        raise LimbIKError(f"{label} cannot be zero; choose an Armature-local direction.")
    return direction


def _configured_pole_direction(settings, kind, side):
    label = f"{side} {kind.title()} Pole Direction"
    return _finite_direction(getattr(settings, _pole_direction_field(kind, side)), label)


def _rest_pole_direction(armature, kind, side, chain_names=()):
    """Return the fixed Armature-local anatomical default.

    A nearly straight source chain can contain an arbitrarily small sideways
    residual.  Normalizing that residual made it dominate the generated Pole:
    X's knees became mostly lateral and its elbows were sent to the front.  The
    modeled bend remains useful evidence for diagnostics, but it must not
    silently replace the explicit Default Direction contract.  The unused
    arguments remain accepted for compatibility with callers and tests that
    previously requested a rest-derived direction.
    """
    _ = armature, chain_names
    return Vector(DEFAULT_POLE_DIRECTIONS[(kind, side)]), False


def _hydrate_pole_directions(settings, armature, payload):
    """Load saved generated directions, modeled rest bends, or fallbacks.

    The WindowManager values deliberately remain an edit buffer.  Analyze calls
    this only when its Armature changes, so re-analysis never overwrites XYZ
    values the artist is currently tuning.
    """
    for kind in KINDS:
        for side in SIDES:
            result = payload["limbs"][kind][side]
            chain_names = tuple(result.get(role, "") for role in ROLES)
            value, _used_modeled_bend = _rest_pole_direction(armature, kind, side, chain_names)
            pole_name = LIMB_SPEC[kind]["pole"].format(side=side)
            pole = armature.data.bones.get(pole_name)
            if (
                pole is not None
                and pole.get(OWNER_KEY) == OWNER_VALUE
                and pole.get(KIND_KEY) == kind
                and pole.get(SIDE_KEY) == side
                and POLE_DIRECTION_KEY in pole
            ):
                try:
                    saved = _finite_direction(pole[POLE_DIRECTION_KEY], f"Saved {side} {kind.title()} Pole Direction")
                    saved.normalize()
                    value = saved
                except LimbIKError:
                    # Strict inventory validation will report malformed owned
                    # metadata before any Build/Rebuild mutation.  Analyze stays
                    # useful and read-only by presenting a repairable default.
                    pass
            setattr(settings, _pole_direction_field(kind, side), tuple(value))


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _armature_digest(armature):
    payload = []
    for bone in sorted(armature.data.bones, key=lambda item: item.name):
        if bone.get(OWNER_KEY) == OWNER_VALUE:
            continue
        payload.append(
            (
                bone.name,
                bone.parent.name if bone.parent else "",
                tuple(round(value, 7) for value in bone.head_local),
                tuple(round(value, 7) for value in bone.tail_local),
                tuple(round(float(value), 7) for row in bone.matrix_local for value in row),
                bool(bone.use_deform),
                bool(bone.use_connect),
            )
        )
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _side_from_name(name):
    match = re.search(r"(?:^|[._\-\s])(left|right|l|r)$", name, re.IGNORECASE)
    if match:
        return "L" if match.group(1).lower() in {"left", "l"} else "R"
    if re.match(r"^left(?=[A-Z_.\-\s]|$)", name, re.IGNORECASE):
        return "L"
    if re.match(r"^right(?=[A-Z_.\-\s]|$)", name, re.IGNORECASE):
        return "R"
    return ""


def _name_parts(name):
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", expanded.lower()) if token)
    base = "".join(token for token in tokens if token not in {"l", "r", "left", "right", "def"})
    return tokens, base


def _name_score(name, kind, role):
    tokens, base = _name_parts(name)
    if _EXCLUDED_TOKENS.intersection(tokens):
        return -1
    if any(marker in base for marker in ("twist", "helper", "corrective", "tweak")):
        return -1
    for keyword, score in LIMB_SPEC[kind][role]:
        if base == keyword:
            return score
        if base.startswith("def" + keyword) or base.endswith(keyword):
            return score - 1
    return -1


def _candidate_chains(armature, kind, side):
    candidates = []
    source_bones = [bone for bone in armature.data.bones if bone.get(OWNER_KEY) != OWNER_VALUE]
    center_x = sum(float(bone.head_local.x) for bone in source_bones) / max(1, len(source_bones))
    extent_x = max((abs(float(bone.head_local.x) - center_x) for bone in source_bones), default=1.0)
    for upper in armature.data.bones:
        upper_score = _name_score(upper.name, kind, "upper")
        if upper_score < 0 or upper.get(OWNER_KEY) == OWNER_VALUE or not upper.use_deform:
            continue
        for lower in upper.children:
            lower_score = _name_score(lower.name, kind, "lower")
            if lower_score < 0 or not lower.use_deform:
                continue
            for end in lower.children:
                end_score = _name_score(end.name, kind, "end")
                if end_score < 0 or not end.use_deform:
                    continue
                explicit = tuple(filter(None, (_side_from_name(b.name) for b in (upper, lower, end))))
                midpoint_x = sum(float(bone.head_local.x) for bone in (upper, lower, end)) / 3.0
                inferred_side = "L" if midpoint_x > center_x else "R"
                if explicit and any(item != side for item in explicit):
                    continue
                if not explicit and (abs(midpoint_x - center_x) < max(extent_x * 0.08, 1.0e-5) or inferred_side != side):
                    continue
                a = Vector(upper.head_local)
                b = Vector(lower.head_local)
                c = Vector(end.head_local)
                tolerance = max(upper.length, lower.length, end.length, 1.0) * 1.0e-4
                if (Vector(upper.tail_local) - b).length > tolerance or (Vector(lower.tail_local) - c).length > tolerance:
                    continue
                upper_len = (b - a).length
                lower_len = (c - b).length
                if min(upper_len, lower_len) <= EPSILON:
                    continue
                ratio = upper_len / lower_len
                if not 0.2 <= ratio <= 5.0:
                    continue
                delta = c - a
                geometry = 0.0
                if kind == "ARM" and math.hypot(delta.x, delta.y) >= abs(delta.z) * 0.45:
                    geometry += 1.0
                if kind == "LEG" and abs(delta.z) >= math.hypot(delta.x, delta.y) * 1.25:
                    geometry += 1.0
                if all(item == side for item in explicit) and len(explicit) == 3:
                    geometry += 1.0
                elif not explicit:
                    geometry += 0.25
                if end.length <= lower.length * 1.5:
                    geometry += 0.5
                candidates.append(
                    LimbChain(kind, side, upper.name, lower.name, end.name, upper_score + lower_score + end_score + geometry)
                )
    return sorted(candidates, key=lambda item: (-item.score, item.names))


def analyze_armature(armature):
    """Return a deterministic analysis without mutating the Armature datablock."""
    if armature is None or armature.type != "ARMATURE":
        raise LimbIKError("Make one Armature active before Analyze Rig.")
    scaffold_count = sum(bone.name.lower().startswith(("mch-", "org-", "ctrl-", "vis_")) for bone in armature.data.bones)
    if armature.get("rig_id") or armature.data.get("rig_id") or scaffold_count >= 12:
        raise LimbIKError("This looks like a generated control rig, not a clean humanoid Deform Skeleton; analyze the source/metarig instead.")
    results = {kind: {} for kind in KINDS}
    warnings = []
    for kind in KINDS:
        for side in SIDES:
            candidates = _candidate_chains(armature, kind, side)
            if not candidates:
                results[kind][side] = {"status": "MISSING", "warning": "No high-confidence direct chain was found."}
                warnings.append(f"{side} {kind.title()}: not identified")
                continue
            top = candidates[0]
            if top.score < 22.0:
                results[kind][side] = {"status": "LOW_CONFIDENCE", "warning": "Name, hierarchy, and position evidence did not reach the safe threshold."}
                warnings.append(f"{side} {kind.title()}: low confidence")
                continue
            if len(candidates) > 1 and top.score - candidates[1].score < 2.0:
                results[kind][side] = {"status": "AMBIGUOUS", "warning": "Two direct chains scored too closely; choose bones manually."}
                warnings.append(f"{side} {kind.title()}: ambiguous")
                continue
            results[kind][side] = {
                "status": "READY",
                "upper": top.upper,
                "lower": top.lower,
                "end": top.end,
                "score": top.score,
                "warning": "",
            }
    return {
        "version": RIG_VERSION,
        "armature": armature.name,
        "armature_data": armature.data.name,
        "digest": _armature_digest(armature),
        "limbs": results,
        "warnings": warnings,
    }


class CharacterDesignerLimbIKState(PropertyGroup):
    armature: PointerProperty(name="Armature", type=bpy.types.Object, poll=_armature_poll, options={"SKIP_SAVE"})
    analysis_json: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    pole_direction_armature_token: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    direct_preroll_json: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    pole_distance_ratio: FloatProperty(name="Pole Distance", default=0.75, min=0.25, max=2.0, subtype="FACTOR", options={"SKIP_SAVE"})
    selected_limb: EnumProperty(
        name="Limb",
        items=(
            ("LEFT_ARM", "Left Arm", "Edit the detected left arm chain"),
            ("RIGHT_ARM", "Right Arm", "Edit the detected right arm chain"),
            ("LEFT_LEG", "Left Leg", "Edit the detected left leg chain"),
            ("RIGHT_LEG", "Right Leg", "Edit the detected right leg chain"),
        ),
        default="LEFT_ARM",
        options={"SKIP_SAVE"},
    )
    build_method: EnumProperty(
        name="Build Method",
        description="Choose the stable helper rig or the minimal experimental Direct Pre-Roll rig",
        items=(
            (
                "ROLL_DECOUPLED",
                "Stable (MCH/ORI)",
                "Production-safe roll-decoupled IK; adds hidden mechanism/orientation bones",
            ),
            (
                "DIRECT_PREROLL",
                "Direct Pre-Roll (Minimal)",
                "Experimental direct IK; edits source Rest roll/plane and adds only Target plus Pole per limb",
            ),
        ),
        default="ROLL_DECOUPLED",
        options={"SKIP_SAVE"},
    )
    left_arm_pole_direction: FloatVectorProperty(
        name="Left Arm Pole Direction",
        description="Armature-local direction from the elbow toward its Pole control",
        size=3,
        subtype="XYZ",
        default=DEFAULT_POLE_DIRECTIONS[("ARM", "L")],
        options={"SKIP_SAVE"},
    )
    right_arm_pole_direction: FloatVectorProperty(
        name="Right Arm Pole Direction",
        description="Armature-local direction from the elbow toward its Pole control",
        size=3,
        subtype="XYZ",
        default=DEFAULT_POLE_DIRECTIONS[("ARM", "R")],
        options={"SKIP_SAVE"},
    )
    left_leg_pole_direction: FloatVectorProperty(
        name="Left Leg Pole Direction",
        description="Armature-local direction from the knee toward its Pole control",
        size=3,
        subtype="XYZ",
        default=DEFAULT_POLE_DIRECTIONS[("LEG", "L")],
        options={"SKIP_SAVE"},
    )
    right_leg_pole_direction: FloatVectorProperty(
        name="Right Leg Pole Direction",
        description="Armature-local direction from the knee toward its Pole control",
        size=3,
        subtype="XYZ",
        default=DEFAULT_POLE_DIRECTIONS[("LEG", "R")],
        options={"SKIP_SAVE"},
    )
    left_arm_upper: StringProperty(options={"SKIP_SAVE"})
    left_arm_lower: StringProperty(options={"SKIP_SAVE"})
    left_arm_end: StringProperty(options={"SKIP_SAVE"})
    right_arm_upper: StringProperty(options={"SKIP_SAVE"})
    right_arm_lower: StringProperty(options={"SKIP_SAVE"})
    right_arm_end: StringProperty(options={"SKIP_SAVE"})
    left_leg_upper: StringProperty(options={"SKIP_SAVE"})
    left_leg_lower: StringProperty(options={"SKIP_SAVE"})
    left_leg_end: StringProperty(options={"SKIP_SAVE"})
    right_leg_upper: StringProperty(options={"SKIP_SAVE"})
    right_leg_lower: StringProperty(options={"SKIP_SAVE"})
    right_leg_end: StringProperty(options={"SKIP_SAVE"})
    last_level: EnumProperty(items=(("NONE", "None", ""), ("INFO", "Info", ""), ("SUCCESS", "Success", ""), ("WARNING", "Warning", ""), ("ERROR", "Error", "")), default="NONE", options={"SKIP_SAVE"})
    last_message: StringProperty(default="", options={"SKIP_SAVE"})


class CHARACTERDESIGNER_OT_limb_ik_analyze(Operator):
    bl_idname = "character_designer.limb_ik_analyze"
    bl_label = "Analyze Rig"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE" and context.mode in {"OBJECT", "POSE"}

    def execute(self, context):
        settings = _settings(context)
        try:
            armature = context.object
            payload = analyze_armature(armature)
            armature_token = str(armature.data.as_pointer())
            armature_changed = settings.armature is not armature or settings.pole_direction_armature_token != armature_token
            if armature_changed:
                _hydrate_pole_directions(settings, armature, payload)
                settings.pole_direction_armature_token = armature_token
            existing_armature_id = armature.data.get(ARMATURE_ID_KEY, "")
            if isinstance(existing_armature_id, str) and existing_armature_id:
                existing_schema = armature.data.get(SCHEMA_KEY, LEGACY_SCHEMA)
                if _is_direct_preroll_schema(existing_schema):
                    settings.build_method = "DIRECT_PREROLL"
                elif existing_schema in {LEGACY_SCHEMA, ENHANCED_SCHEMA, ROLL_DECOUPLED_SCHEMA}:
                    settings.build_method = "ROLL_DECOUPLED"
            settings.armature = armature
            settings.analysis_json = _canonical_json(payload)
            settings.direct_preroll_json = ""
            for kind in KINDS:
                for side in SIDES:
                    result = payload["limbs"][kind][side]
                    for role in ROLES:
                        setattr(settings, _field_name(kind, side, role), result.get(role, ""))
            statuses = [payload["limbs"][kind][side]["status"] for kind in KINDS for side in SIDES]
            ready = sum(status in {"READY", "WARNING"} for status in statuses)
            incomplete = any(status not in {"READY", "WARNING"} for status in statuses)
            message = f"Analyze Rig found {ready}/4 high-confidence limbs."
            if incomplete:
                message += " Low-confidence sides were left blank."
            level = "WARNING" if incomplete else "SUCCESS"
            _set_status(settings, level, message)
            self.report({"WARNING" if level == "WARNING" else "INFO"}, message)
            return {"FINISHED"}
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            _set_status(settings, "WARNING", str(exc))
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


# Build/remove helpers and operators are intentionally below the analysis API so
# tests and future UI variants can use the detector independently.


def _require_active_armature(context, settings, *, analyzed=True):
    armature = context.object
    if armature is None or armature.type != "ARMATURE":
        raise LimbIKError("Make the intended Armature active first.")
    if settings is None:
        raise LimbIKError("Limb IK state is unavailable; reload Character Designer.")
    if analyzed and settings.armature is not armature:
        raise LimbIKError("The active Armature is not the analyzed Armature; run Analyze Rig again.")
    if armature.library is not None or armature.data.library is not None:
        raise LimbIKError("Linked Armatures are read-only; make a local copy before building Limb IK.")
    if armature.override_library is not None or armature.data.override_library is not None:
        raise LimbIKError("Library overrides are not modified by Limb IK; use a local Armature.")
    if armature.data.users != 1:
        raise LimbIKError("The active Armature Data is shared; make it single-user before building Limb IK.")
    memberships = [scene for scene in bpy.data.scenes if scene.objects.get(armature.name) is armature]
    if len(memberships) != 1 or memberships[0] is not context.scene:
        raise LimbIKError("The active Armature must belong only to the current Scene.")
    if context.view_layer.objects.get(armature.name) is not armature:
        raise LimbIKError("The active Armature is excluded from the current View Layer.")
    if abs(armature.matrix_world.to_3x3().determinant()) <= EPSILON:
        raise LimbIKError("The Armature transform is singular; apply a usable non-zero scale first.")
    if analyzed:
        try:
            payload = json.loads(settings.analysis_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LimbIKError("Analyze Rig data is missing or corrupt; run Analyze Rig again.") from exc
        if not isinstance(payload, dict) or payload.get("version") != RIG_VERSION:
            raise LimbIKError("Analyze Rig data is stale; run Analyze Rig again.")
        if payload.get("digest") != _armature_digest(armature):
            raise LimbIKError("Bone names, hierarchy, or rest pose changed; run Analyze Rig again.")
    return armature


def _chain_from_settings(settings, armature, kind, side):
    names = tuple(getattr(settings, _field_name(kind, side, role), "") for role in ROLES)
    if not any(names):
        return None
    if not all(names):
        raise LimbIKError(f"{side} {kind.title()} has an incomplete manual bone selection.")
    if len(set(names)) != 3:
        raise LimbIKError(f"{side} {kind.title()} must use three different bones.")
    bones = tuple(armature.data.bones.get(name) for name in names)
    if any(bone is None for bone in bones):
        raise LimbIKError(f"{side} {kind.title()} refers to a bone that no longer exists.")
    upper, lower, end = bones
    if lower.parent is None or lower.parent.name != upper.name or end.parent is None or end.parent.name != lower.name:
        raise LimbIKError(f"{side} {kind.title()} must be one direct upper → lower → end hierarchy.")
    if min((Vector(lower.head_local) - Vector(upper.head_local)).length, (Vector(end.head_local) - Vector(lower.head_local)).length) <= EPSILON:
        raise LimbIKError(f"{side} {kind.title()} contains a zero-length IK segment.")
    scale = max(upper.length, lower.length, end.length, 1.0)
    tolerance = max(scale * 1.0e-4, 1.0e-6)
    if (Vector(upper.tail_local) - Vector(lower.head_local)).length > tolerance or (Vector(lower.tail_local) - Vector(end.head_local)).length > tolerance:
        raise LimbIKError(f"{side} {kind.title()} joints are spatially disconnected.")
    if not all(bone.use_deform for bone in bones):
        raise LimbIKError(f"{side} {kind.title()} includes a non-Deform/scaffold bone.")
    if any(_EXCLUDED_TOKENS.intersection(_name_parts(bone.name)[0]) for bone in bones):
        raise LimbIKError(f"{side} {kind.title()} includes a helper, mechanism, or existing control bone.")
    explicit = tuple(filter(None, (_side_from_name(name) for name in names)))
    if explicit and any(value != side for value in explicit):
        raise LimbIKError(f"{side} {kind.title()} contains an explicit opposite-side bone name.")
    if any(bone.get(OWNER_KEY) == OWNER_VALUE for bone in bones):
        raise LimbIKError(f"{side} {kind.title()} cannot use generated control bones as deform inputs.")
    return LimbChain(kind, side, *names)


def _capture_context(context, armature):
    mode = context.mode
    if mode not in {"OBJECT", "POSE", "EDIT_ARMATURE"}:
        raise LimbIKError("Use Limb IK from Armature Object, Pose, or Edit Mode.")
    snapshot = {
        "mode": mode,
        "active": context.view_layer.objects.active,
        "selected": tuple(obj for obj in context.selected_objects),
        "bone_selection": {},
        "active_bone": "",
    }
    if mode == "EDIT_ARMATURE":
        snapshot["bone_selection"] = {
            bone.name: (bool(bone.select), bool(bone.select_head), bool(bone.select_tail))
            for bone in armature.data.edit_bones
        }
        active = armature.data.edit_bones.active
    else:
        snapshot["bone_selection"] = {bone.name: bool(bone.select) for bone in armature.pose.bones}
        active = armature.data.bones.active
    snapshot["active_bone"] = active.name if active else ""
    return snapshot


def _mode_set(context, armature, mode):
    active = context.view_layer.objects.active
    if context.mode != "OBJECT":
        if bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
            raise LimbIKError("Blender could not enter Object Mode for Limb IK.")
    for obj in context.view_layer.objects:
        obj.select_set(False)
    armature.select_set(True)
    context.view_layer.objects.active = armature
    if mode != "OBJECT" and bpy.ops.object.mode_set(mode=mode) != {"FINISHED"}:
        context.view_layer.objects.active = active
        raise LimbIKError(f"Blender could not enter {mode.title()} Mode for Limb IK.")


def _restore_context(context, armature, snapshot):
    if context.mode != "OBJECT" and bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
        raise LimbIKError("Blender could not restore Object Mode.")
    for obj in context.view_layer.objects:
        obj.select_set(False)
    selected = [obj for obj in snapshot["selected"] if bpy.data.objects.get(obj.name) is obj and context.view_layer.objects.get(obj.name) is obj]
    for obj in selected:
        obj.select_set(True)
    active = snapshot["active"]
    if active is not None and bpy.data.objects.get(active.name) is active and context.view_layer.objects.get(active.name) is active:
        context.view_layer.objects.active = active
    else:
        context.view_layer.objects.active = armature
        armature.select_set(True)
    original_mode = snapshot["mode"]
    if original_mode == "EDIT_ARMATURE":
        if context.view_layer.objects.active is not armature:
            raise LimbIKError("The original Edit Armature is no longer active.")
        if bpy.ops.object.mode_set(mode="EDIT") != {"FINISHED"}:
            raise LimbIKError("Blender could not restore Armature Edit Mode.")
        for bone in armature.data.edit_bones:
            values = snapshot["bone_selection"].get(bone.name, (False, False, False))
            bone.select, bone.select_head, bone.select_tail = values
        armature.data.edit_bones.active = armature.data.edit_bones.get(snapshot["active_bone"])
    elif original_mode == "POSE":
        if context.view_layer.objects.active is not armature:
            raise LimbIKError("The original Pose Armature is no longer active.")
        if bpy.ops.object.mode_set(mode="POSE") != {"FINISHED"}:
            raise LimbIKError("Blender could not restore Pose Mode.")
        for bone in armature.pose.bones:
            bone.select = snapshot["bone_selection"].get(bone.name, False)
        armature.data.bones.active = armature.data.bones.get(snapshot["active_bone"])
    elif original_mode == "OBJECT":
        for bone in armature.pose.bones:
            bone.select = snapshot["bone_selection"].get(bone.name, False)
        armature.data.bones.active = armature.data.bones.get(snapshot["active_bone"])


def _project_perpendicular(vector, axis):
    axis = Vector(axis)
    if axis.length <= EPSILON:
        return Vector()
    axis.normalize()
    return Vector(vector) - axis * Vector(vector).dot(axis)


def _signed_angle(reference, target, axis):
    reference = _project_perpendicular(reference, axis)
    target = _project_perpendicular(target, axis)
    if reference.length <= EPSILON or target.length <= EPSILON:
        raise LimbIKError("Pole Angle could not be derived from the source bone roll.")
    reference.normalize()
    target.normalize()
    axis = Vector(axis).normalized()
    return math.atan2(axis.dot(reference.cross(target)), max(-1.0, min(1.0, reference.dot(target))))


def _joint_on_direction_plane(start, joint, end, direction):
    start = Vector(start)
    joint = Vector(joint)
    end = Vector(end)
    chain = end - start
    distance = chain.length
    if distance <= EPSILON:
        raise LimbIKError("The limb root and IK endpoint occupy the same position.")
    upper_length = (joint - start).length
    lower_length = (end - joint).length
    along = (upper_length * upper_length - lower_length * lower_length + distance * distance) / (2.0 * distance)
    height_squared = upper_length * upper_length - along * along
    tolerance = max(upper_length * upper_length, lower_length * lower_length, 1.0) * 1.0e-7
    if height_squared < -tolerance:
        raise LimbIKError("The current limb lengths cannot form a reachable two-bone Pole plane.")
    height = math.sqrt(max(0.0, height_squared))
    return start + chain.normalized() * along + Vector(direction) * height


def _pole_solution(start, joint, end, upper_x, pole_direction, distance_ratio):
    chain_axis = Vector(end) - Vector(start)
    if chain_axis.length <= EPSILON:
        raise LimbIKError("The limb root and IK endpoint occupy the same position.")
    total = (Vector(joint) - Vector(start)).length + (Vector(end) - Vector(joint)).length
    if total <= EPSILON:
        raise LimbIKError("The limb has no usable length.")
    configured = _finite_direction(pole_direction)
    direction = _project_perpendicular(configured, chain_axis)
    if direction.length <= max(EPSILON, configured.length * POLE_DIRECTION_PARALLEL_RATIO):
        raise LimbIKError(
            "Pole Direction is parallel to the limb root-to-end axis; choose another Armature-local XYZ direction."
        )
    direction.normalize()
    desired_joint = _joint_on_direction_plane(start, joint, end, direction)
    pole_distance = total * float(distance_ratio)
    pole_head = desired_joint + direction * pole_distance
    plane_normal = chain_axis.cross(pole_head - Vector(start))
    projected_pole_axis = plane_normal.cross(desired_joint - Vector(start))
    pole_angle = -_signed_angle(upper_x, projected_pole_axis, desired_joint - Vector(start))
    return pole_head, pole_angle, direction


def _roll_for_axes(y_axis, z_axis):
    """Return Blender's absolute EditBone roll for an orthonormal Y/Z frame."""
    y_axis = Vector(y_axis)
    if y_axis.length <= EPSILON:
        raise LimbIKError("Direct Pre-Roll cannot use a zero-length bone axis.")
    y_axis.normalize()
    z_axis = _project_perpendicular(z_axis, y_axis)
    if z_axis.length <= EPSILON:
        raise LimbIKError("Direct Pre-Roll could not derive a transverse bone axis.")
    z_axis.normalize()
    x_axis = y_axis.cross(z_axis)
    if x_axis.length <= EPSILON:
        raise LimbIKError("Direct Pre-Roll could not derive an orthogonal bone frame.")
    x_axis.normalize()
    z_axis = x_axis.cross(y_axis).normalized()
    matrix = Matrix((x_axis, y_axis, z_axis)).transposed()
    resolved_axis, roll = bpy.types.Bone.AxisRollFromMatrix(matrix, axis=y_axis)
    if Vector(resolved_axis).normalized().dot(y_axis) < 1.0 - 1.0e-6:
        raise LimbIKError("Blender could not resolve the requested Direct Pre-Roll axis.")
    return float(roll)


def _wrap_degrees(value):
    wrapped = (float(value) + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped <= -180.0 + 1.0e-10 else wrapped


def _direct_preroll_analysis(armature, chain, pole_direction):
    """Analyze a source chain without changing bones, constraints, or the Blend.

    A direct two-bone IK can start without axial skin roll only when its modeled
    Rest bend already occupies the requested Pole plane.  EditBone roll alone
    cannot repair a plane mismatch: the same frame change appears in the
    Armature modifier's inverse Rest matrix.  This report therefore separates
    the plane rotation from the absolute rolls that would make sense *after* a
    Rest-joint re-plane.
    """
    upper = armature.data.bones[chain.upper]
    lower = armature.data.bones[chain.lower]
    start = Vector(upper.head_local)
    joint = Vector(lower.head_local)
    end = Vector(lower.tail_local)
    upper_length = (joint - start).length
    lower_length = (end - joint).length
    total = upper_length + lower_length
    chain_axis = end - start
    if min(upper_length, lower_length, total, chain_axis.length) <= EPSILON:
        raise LimbIKError(f"{chain.side} {chain.kind.title()} has no usable Direct Pre-Roll geometry.")

    configured = _finite_direction(pole_direction)
    desired = _project_perpendicular(configured, chain_axis)
    if desired.length <= max(EPSILON, configured.length * POLE_DIRECTION_PARALLEL_RATIO):
        raise LimbIKError(
            "Pole Direction is parallel to the limb root-to-end axis; choose another Armature-local XYZ direction."
        )
    desired.normalize()
    modeled = _project_perpendicular(joint - start, chain_axis)
    reach_deficit_ratio = max(0.0, (total - chain_axis.length) / total)
    bend_ratio = modeled.length / total
    stable = (
        modeled.length > max(EPSILON, total * POLE_DIRECTION_PARALLEL_RATIO)
        and reach_deficit_ratio > POLE_MIN_REACH_DEFICIT_RATIO
    )

    plane_offset_degrees = None
    alignment = None
    if modeled.length > EPSILON:
        modeled.normalize()
        alignment = max(-1.0, min(1.0, modeled.dot(desired)))
        plane_offset_degrees = _wrap_degrees(
            math.degrees(_signed_angle(modeled, desired, chain_axis))
        )

    proposed_joint = _joint_on_direction_plane(start, joint, end, desired)
    proposed_upper_axis = proposed_joint - start
    proposed_lower_axis = end - proposed_joint
    plane_normal = chain_axis.cross(desired)
    if plane_normal.length <= EPSILON:
        raise LimbIKError("Direct Pre-Roll could not derive the requested bend plane.")
    # Pole Angle zero uses the in-plane X cross-section; Blender's right-handed
    # bone frame therefore needs Z opposite the chord x Pole plane normal.
    proposed_z = -plane_normal.normalized()
    upper_roll = _roll_for_axes(proposed_upper_axis, proposed_z)
    lower_roll = _roll_for_axes(proposed_lower_axis, proposed_z)
    joint_shift = (proposed_joint - joint).length

    if not stable:
        status = "UNSTABLE"
    elif abs(plane_offset_degrees) <= DIRECT_PREROLL_ALIGNMENT_DEGREES:
        status = "ALIGNED"
    else:
        status = "REPLANE_REQUIRED"
    return {
        "version": DIRECT_PREROLL_RESULT_VERSION,
        "status": status,
        "kind": chain.kind,
        "side": chain.side,
        "chain": list(chain.names),
        "rest_plane_alignment": alignment,
        "rest_plane_offset_degrees": plane_offset_degrees,
        "bend_ratio": float(bend_ratio),
        "reach_deficit_ratio": float(reach_deficit_ratio),
        "joint_shift": float(joint_shift),
        "joint_shift_upper_ratio": float(joint_shift / upper_length),
        "proposed_joint": [float(component) for component in proposed_joint],
        "proposed_upper_roll_radians": float(upper_roll),
        "proposed_lower_roll_radians": float(lower_roll),
        "proposed_upper_roll_degrees": _wrap_degrees(math.degrees(upper_roll)),
        "proposed_lower_roll_degrees": _wrap_degrees(math.degrees(lower_roll)),
        "dynamic_guarantee": False,
    }


def _build_plan(armature, chain, distance_ratio, pole_direction):
    upper = armature.pose.bones[chain.upper]
    lower = armature.pose.bones[chain.lower]
    end_bone = armature.pose.bones[chain.end]
    start = Vector(upper.head)
    joint = Vector(lower.head)
    end = Vector(lower.tail)
    total = (joint - start).length + (end - joint).length
    reach = (end - start).length
    if total <= EPSILON:
        raise LimbIKError(f"{chain.side} {chain.kind.title()} has no usable length.")
    if total - reach <= max(EPSILON, total * POLE_MIN_REACH_DEFICIT_RATIO):
        bend_name = "elbow" if chain.kind == "ARM" else "knee"
        raise LimbIKError(
            f"{chain.side} {chain.kind.title()} is effectively straight, so Blender cannot keep a stable Pole plane. "
            f"Add a small bend at the {bend_name} in the current pose, then Build IK again."
        )
    configured_pole_direction = _finite_direction(pole_direction)
    configured_pole_direction.normalize()
    pole_head, pole_angle, effective_pole_direction = _pole_solution(
        start,
        joint,
        end,
        upper.x_axis,
        configured_pole_direction,
        distance_ratio,
    )
    pole_joint = _joint_on_direction_plane(start, joint, end, effective_pole_direction)
    size = max(total * 0.14, 1.0e-4)
    end_direction = Vector(end_bone.tail) - Vector(end_bone.head)
    if end_direction.length <= EPSILON:
        end_direction = end - joint
    end_direction.normalize()
    spec = LIMB_SPEC[chain.kind]
    heel_name = ""
    solver_target_name = spec["target"].format(side=chain.side)
    heel_head = None
    heel_tail = None
    heel_z = None
    if chain.kind == "LEG":
        # A lightweight heel pivot is inferred entirely from the selected Foot
        # bone.  It intentionally remains conservative: behind the ankle along
        # the Foot axis and slightly toward the Shin's down direction.  The
        # internal solver target keeps the visible Foot control free to move
        # this pivot before the artist applies roll/bank.
        foot_vector = Vector(end_bone.tail) - Vector(end_bone.head)
        if foot_vector.length <= EPSILON:
            raise LimbIKError(f"{chain.side} Leg Foot bone has no usable direction for a heel pivot.")
        foot_length = foot_vector.length
        foot_forward = foot_vector.normalized()
        shin_down = end - joint
        if shin_down.length <= EPSILON:
            raise LimbIKError(f"{chain.side} Leg Shin has no usable direction for a heel pivot.")
        shin_down.normalize()
        heel_head = Vector(end_bone.head) - foot_forward * foot_length * 0.22 + shin_down * foot_length * 0.08
        heel_tail = heel_head + foot_forward * max(size * 0.8, foot_length * 0.18)
        heel_z = Vector(end_bone.matrix.to_3x3().col[2])
        heel_name = spec["heel"].format(side=chain.side)
        solver_target_name = spec["solver_target"].format(side=chain.side)
    return LimbPlan(
        chain=chain,
        rig_id=uuid.uuid4().hex,
        target_name=spec["target"].format(side=chain.side),
        pole_name=spec["pole"].format(side=chain.side),
        start=start,
        joint=joint,
        end=end,
        target_head=end.copy(),
        target_tail=end + end_direction * size,
        target_z=Vector(end_bone.matrix.to_3x3().col[2]),
        pole_head=pole_head,
        # The Pole pivot is its head.  Keep the Edit bone short and detached at
        # that target, with its tail pointing farther along the actual bend
        # direction instead of inheriting an arbitrary upper-bone Z axis.
        pole_tail=pole_head + effective_pole_direction * size * 0.65,
        pole_angle=pole_angle,
        pole_warning="",
        pole_direction=effective_pole_direction,
        pole_joint=pole_joint,
        desired_upper_matrix=upper.matrix.copy(),
        desired_lower_matrix=lower.matrix.copy(),
        desired_end_matrix=end_bone.matrix.copy(),
        target_basis=Matrix.Identity(4),
        pole_basis=Matrix.Identity(4),
        line_name=spec["line"].format(side=chain.side),
        display_name=spec["display"].format(side=chain.side),
        heel_name=heel_name,
        solver_target_name=solver_target_name,
        mch_upper_name=spec["mch_upper"].format(side=chain.side),
        mch_lower_name=spec["mch_lower"].format(side=chain.side),
        ori_upper_name=spec["ori_upper"].format(side=chain.side),
        ori_lower_name=spec["ori_lower"].format(side=chain.side),
        heel_head=heel_head,
        heel_tail=heel_tail,
        heel_z=heel_z,
        configured_pole_direction=configured_pole_direction,
    )


def _direct_arm_target_frame(armature, plan):
    """Align a minimal Direct hand Target to the solved Forearm Rest frame.

    Direct Pre-Roll deliberately has no orientation/helper bone.  Its visible
    hand Target therefore needs to be authored in the final Forearm frame, not
    in the source Hand frame captured before the Rest re-plane.  The Hand keeps
    its modeled Rest offset through the local end-rotation constraint.
    """
    if plan.chain.kind != "ARM":
        return plan
    lower = armature.data.bones[plan.chain.lower]
    direction = Vector(lower.tail_local) - Vector(lower.head_local)
    if direction.length <= EPSILON:
        raise LimbIKError(
            f"{plan.chain.side} Arm Forearm has no usable direction for its Direct hand control."
        )
    direction.normalize()
    target_length = max((Vector(plan.target_tail) - Vector(plan.target_head)).length, 1.0e-4)
    target_head = Vector(lower.tail_local)
    return replace(
        plan,
        target_head=target_head,
        target_tail=target_head + direction * target_length,
        target_z=Vector(lower.matrix_local.to_3x3().col[2]),
    )


def _edit_rest_state(edit_bone):
    return {
        "head": [float(component) for component in edit_bone.head],
        "tail": [float(component) for component in edit_bone.tail],
        "roll": float(edit_bone.roll),
        "z": [float(component) for component in edit_bone.matrix.to_3x3().col[2]],
        "parent": edit_bone.parent.name if edit_bone.parent is not None else "",
        "use_connect": bool(edit_bone.use_connect),
    }


def _direct_rest_state(state, label):
    if not isinstance(state, dict):
        raise LimbIKError(f"{label} Rest snapshot is invalid.")
    try:
        head = tuple(float(value) for value in state["head"])
        tail = tuple(float(value) for value in state["tail"])
        z_axis = tuple(float(value) for value in state["z"])
        roll = float(state["roll"])
        parent = state["parent"]
        use_connect = state["use_connect"]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise LimbIKError(f"{label} Rest snapshot is incomplete.") from exc
    axis = Vector(tail) - Vector(head)
    z_vector = Vector(z_axis)
    if (
        len(head) != 3
        or len(tail) != 3
        or len(z_axis) != 3
        or any(not math.isfinite(value) for value in (*head, *tail, *z_axis, roll))
        or not isinstance(parent, str)
        or not isinstance(use_connect, bool)
        or axis.length <= EPSILON
        or z_vector.length <= EPSILON
        or abs(axis.normalized().dot(z_vector.normalized())) > 1.0e-5
        or abs(math.remainder(roll - _roll_for_axes(axis, z_vector), math.tau)) > 1.0e-5
    ):
        raise LimbIKError(f"{label} Rest snapshot contains invalid values.")
    return {
        "head": list(head),
        "tail": list(tail),
        "roll": roll,
        "z": list(z_axis),
        "parent": parent,
        "use_connect": use_connect,
    }


def _load_direct_rest_registry(armature, *, strict=True):
    raw = armature.data.get(DIRECT_REST_KEY, "")
    if not raw:
        return {"version": DIRECT_PREROLL_RESULT_VERSION, "limbs": {}}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise LimbIKError("Direct Pre-Roll Rest registry is corrupt.") from exc
        return {"version": DIRECT_PREROLL_RESULT_VERSION, "limbs": {}}
    if (
        not isinstance(payload, dict)
        or payload.get("version") != DIRECT_PREROLL_RESULT_VERSION
        or not isinstance(payload.get("limbs"), dict)
    ):
        if strict:
            raise LimbIKError("Direct Pre-Roll Rest registry is invalid.")
        return {"version": DIRECT_PREROLL_RESULT_VERSION, "limbs": {}}
    normalized = {"version": DIRECT_PREROLL_RESULT_VERSION, "limbs": {}}
    try:
        for rig_id, entry in payload["limbs"].items():
            if not isinstance(rig_id, str) or not rig_id or not isinstance(entry, dict):
                raise LimbIKError("Direct Pre-Roll Rest registry contains an invalid rig ID.")
            kind = entry.get("kind")
            side = entry.get("side")
            chain = entry.get("chain")
            if (
                kind not in KINDS
                or side not in SIDES
                or not isinstance(chain, list)
                or len(chain) != 3
                or any(not isinstance(name, str) or not name for name in chain)
            ):
                raise LimbIKError(f"Direct Pre-Roll Rest entry '{rig_id}' has an invalid chain.")
            originals = entry.get("original")
            applied = entry.get("applied")
            expected = set(chain[:2])
            if (
                not isinstance(originals, dict)
                or not isinstance(applied, dict)
                or set(originals) != expected
                or set(applied) != expected
            ):
                raise LimbIKError(f"Direct Pre-Roll Rest entry '{rig_id}' is incomplete.")
            original_states = {
                name: _direct_rest_state(originals[name], f"Direct Pre-Roll '{rig_id}:{name}' original")
                for name in chain[:2]
            }
            applied_states = {
                name: _direct_rest_state(applied[name], f"Direct Pre-Roll '{rig_id}:{name}' applied")
                for name in chain[:2]
            }
            for label, states in (("original", original_states), ("applied", applied_states)):
                upper_state = states[chain[0]]
                lower_state = states[chain[1]]
                tolerance = max(
                    (Vector(upper_state["tail"]) - Vector(upper_state["head"])).length,
                    (Vector(lower_state["tail"]) - Vector(lower_state["head"])).length,
                    1.0,
                ) * 1.0e-6
                if (
                    lower_state["parent"] != chain[0]
                    or (Vector(upper_state["tail"]) - Vector(lower_state["head"])).length > tolerance
                ):
                    raise LimbIKError(
                        f"Direct Pre-Roll Rest entry '{rig_id}' has a disconnected {label} chain."
                    )
            normalized["limbs"][rig_id] = {
                "kind": kind,
                "side": side,
                "chain": list(chain),
                "original": original_states,
                "applied": applied_states,
            }
    except LimbIKError:
        if strict:
            raise
        return {"version": DIRECT_PREROLL_RESULT_VERSION, "limbs": {}}
    return normalized


def _write_direct_rest_registry(armature, registry):
    limbs = registry.get("limbs", {}) if isinstance(registry, dict) else {}
    if limbs:
        armature.data[DIRECT_REST_KEY] = _canonical_json(
            {"version": DIRECT_PREROLL_RESULT_VERSION, "limbs": limbs}
        )
    elif DIRECT_REST_KEY in armature.data:
        del armature.data[DIRECT_REST_KEY]


def _rest_state_matches(data_bone, state):
    if data_bone is None:
        return False
    scale = max(float(data_bone.length), 1.0)
    tolerance = scale * 1.0e-6
    expected_z = Vector(state["z"])
    current_z = Vector(data_bone.matrix_local.to_3x3().col[2])
    if min(expected_z.length, current_z.length) <= EPSILON:
        return False
    return (
        (Vector(data_bone.head_local) - Vector(state["head"])).length <= tolerance
        and (Vector(data_bone.tail_local) - Vector(state["tail"])).length <= tolerance
        and expected_z.normalized().dot(current_z.normalized()) >= 1.0 - 1.0e-6
        and (data_bone.parent.name if data_bone.parent is not None else "") == state["parent"]
        and bool(data_bone.use_connect) == state["use_connect"]
    )


def _rest_state_mismatch_details(data_bone, state):
    if data_bone is None:
        return "bone missing"
    expected_z = Vector(state["z"]).normalized()
    current_z = Vector(data_bone.matrix_local.to_3x3().col[2]).normalized()
    return (
        f"head={(Vector(data_bone.head_local) - Vector(state['head'])).length:.3g}, "
        f"tail={(Vector(data_bone.tail_local) - Vector(state['tail'])).length:.3g}, "
        f"z={1.0 - expected_z.dot(current_z):.3g}, "
        f"parent={(data_bone.parent.name if data_bone.parent else '')!r}/{state['parent']!r}, "
        f"connect={bool(data_bone.use_connect)}/{state['use_connect']}"
    )


def _restore_edit_rest_states(context, armature, states):
    if not states:
        return
    mirror_x = bool(armature.data.use_mirror_x)
    try:
        armature.data.use_mirror_x = False
        _mode_set(context, armature, "EDIT")
        edit_bones = armature.data.edit_bones
        missing = [name for name in states if edit_bones.get(name) is None]
        if missing:
            raise LimbIKError(f"Direct Pre-Roll source bone '{missing[0]}' disappeared before Rest restoration.")
        for name in states:
            edit_bones[name].use_connect = False
        for name, state in states.items():
            bone = edit_bones[name]
            parent_name = state["parent"]
            parent = edit_bones.get(parent_name) if parent_name else None
            if parent_name and parent is None:
                raise LimbIKError(
                    f"Direct Pre-Roll source parent '{parent_name}' disappeared before Rest restoration."
                )
            bone.parent = parent
            bone.head = Vector(state["head"])
            bone.tail = Vector(state["tail"])
            bone.roll = float(state["roll"])
        for name, state in states.items():
            edit_bones[name].use_connect = bool(state["use_connect"])
        _mode_set(context, armature, "OBJECT")
        armature.data.update_tag()
        armature.update_tag(refresh={"OBJECT"})
        context.view_layer.update()
    finally:
        armature.data.use_mirror_x = mirror_x


def _restore_direct_registry_original(context, armature, registry):
    states = {}
    for entry in registry.get("limbs", {}).values():
        for name, state in entry.get("original", {}).items():
            existing = states.get(name)
            if existing is not None and _canonical_json(existing) != _canonical_json(state):
                raise LimbIKError(f"Direct Pre-Roll Rest registry disagrees about source bone '{name}'.")
            states[name] = state
    _restore_edit_rest_states(context, armature, states)
    for name, state in states.items():
        if not _rest_state_matches(armature.data.bones.get(name), state):
            raise LimbIKError(f"Direct Pre-Roll could not exactly restore source Rest bone '{name}'.")


def _matrix_basis_is_identity(pose_bone, tolerance=1.0e-6):
    return _matrix_is_identity(pose_bone.matrix_basis, tolerance=tolerance)


def _matrix_is_identity(matrix, tolerance=1.0e-6):
    identity = Matrix.Identity(4)
    return max(
        abs(float(matrix[row][column] - identity[row][column]))
        for row in range(4)
        for column in range(4)
    ) <= tolerance


def _pose_bone_is_at_rest(armature, name):
    pose_bone = armature.pose.bones[name]
    rest_bone = armature.data.bones[name]
    return (
        _matrix_basis_is_identity(pose_bone)
        and (pose_bone.matrix.translation - rest_bone.matrix_local.translation).length <= 1.0e-6
        and _rotation_error(pose_bone.matrix, rest_bone.matrix_local) <= 1.0e-6
        and not pose_bone.constraints
    )


def _direct_chain_has_animation(armature, names):
    for action in _actions_for_id(armature):
        if any(_path_mentions_bone(fcurve.data_path, names) for fcurve in _fcurves_for_action(action)):
            return True
    animation = armature.animation_data
    if animation is not None:
        for fcurve in animation.drivers:
            if _path_mentions_bone(fcurve.data_path, names):
                return True
    return False


def _apply_direct_preroll(context, armature, plans, transaction):
    """Re-plane clean source Rest chains, recording an exact reversible snapshot."""
    registry = _load_direct_rest_registry(armature, strict=True)
    raw_before = armature.data.get(DIRECT_REST_KEY, None)
    transaction["direct_rest_before"] = raw_before
    transaction["direct_rest_touched"] = True
    transaction["direct_original_states"] = {}

    computations = []
    affected = {
        name
        for entry in registry.get("limbs", {}).values()
        for name in entry.get("chain", ())[:2]
    }
    for plan in plans:
        key_names = set(plan.chain.names[:2])
        overlap = affected.intersection(key_names)
        if overlap:
            raise LimbIKError(f"Direct Pre-Roll build selections overlap on source bone '{sorted(overlap)[0]}'.")
        affected.update(key_names)
        saved_rest = plan.direct_rest if plan.preserve_pole_angle else None
        if saved_rest is not None:
            if (
                saved_rest.get("kind") != plan.chain.kind
                or saved_rest.get("side") != plan.chain.side
                or tuple(saved_rest.get("chain", ())) != plan.chain.names
            ):
                raise LimbIKError(
                    f"{plan.chain.side} {plan.chain.kind.title()} Direct recovery Rest snapshot is mismatched."
                )
            for name in plan.chain.names[:2]:
                if not _rest_state_matches(armature.data.bones.get(name), saved_rest["original"][name]):
                    raise LimbIKError(
                        f"Direct Pre-Roll recovery did not start from original Rest bone '{name}'."
                    )
            result = None
        else:
            if any(not _pose_bone_is_at_rest(armature, name) for name in plan.chain.names):
                raise LimbIKError(
                    f"{plan.chain.side} {plan.chain.kind.title()} is posed or constrained. Direct Pre-Roll changes Rest "
                    "axes, so clear the selected limb to its unconstrained Rest pose before building."
                )
            if _direct_chain_has_animation(armature, plan.chain.names):
                raise LimbIKError(
                    f"{plan.chain.side} {plan.chain.kind.title()} already has animation. Direct Pre-Roll would reinterpret "
                    "those keyed Rest axes; use Stable (MCH/ORI) or remove/retarget that animation first."
                )
            upper = armature.data.bones[plan.chain.upper]
            foreign_connected = [
                child.name
                for child in upper.children
                if child.name != plan.chain.lower and child.use_connect
            ]
            if foreign_connected:
                raise LimbIKError(
                    f"Direct Pre-Roll would move connected sibling '{foreign_connected[0]}' on '{upper.name}'. "
                    "Disconnect that sibling or use Stable (MCH/ORI)."
                )
            configured_direction = (
                plan.configured_pole_direction
                if plan.configured_pole_direction is not None
                else plan.pole_direction
            )
            result = _direct_preroll_analysis(
                armature,
                plan.chain,
                configured_direction,
            )
            if result["status"] == "UNSTABLE":
                bend_name = "elbow" if plan.chain.kind == "ARM" else "knee"
                raise LimbIKError(
                    f"{plan.chain.side} {plan.chain.kind.title()} is too straight for Direct Pre-Roll. "
                    f"Add a small Rest bend at the {bend_name}, then build again."
                )
        computations.append((plan, result, saved_rest))

    mirror_x = bool(armature.data.use_mirror_x)
    applied_by_rig = {}
    try:
        armature.data.use_mirror_x = False
        _mode_set(context, armature, "EDIT")
        for plan, result, saved_rest in computations:
            upper = armature.data.edit_bones[plan.chain.upper]
            lower = armature.data.edit_bones[plan.chain.lower]
            originals = {
                upper.name: _edit_rest_state(upper),
                lower.name: _edit_rest_state(lower),
            }
            transaction["direct_original_states"].update(originals)
            if saved_rest is None:
                lower_connected = bool(lower.use_connect)
                lower.use_connect = False
                proposed_joint = Vector(result["proposed_joint"])
                upper.tail = proposed_joint
                lower.head = proposed_joint
                upper.roll = float(result["proposed_upper_roll_radians"])
                lower.roll = float(result["proposed_lower_roll_radians"])
                lower.parent = upper
                lower.use_connect = lower_connected
                original_record = originals
            else:
                applied_states = saved_rest["applied"]
                for name in plan.chain.names[:2]:
                    armature.data.edit_bones[name].use_connect = False
                for name in plan.chain.names[:2]:
                    bone = armature.data.edit_bones[name]
                    state = applied_states[name]
                    parent_name = state["parent"]
                    bone.parent = armature.data.edit_bones.get(parent_name) if parent_name else None
                    bone.head = Vector(state["head"])
                    bone.tail = Vector(state["tail"])
                    bone.roll = float(state["roll"])
                for name in plan.chain.names[:2]:
                    armature.data.edit_bones[name].use_connect = bool(applied_states[name]["use_connect"])
                original_record = saved_rest["original"]
            applied_by_rig[plan.rig_id] = {
                "kind": plan.chain.kind,
                "side": plan.chain.side,
                "chain": list(plan.chain.names),
                "original": original_record,
                "applied": {
                    upper.name: _edit_rest_state(upper),
                    lower.name: _edit_rest_state(lower),
                },
            }
        _mode_set(context, armature, "OBJECT")
    finally:
        armature.data.use_mirror_x = mirror_x
    armature.data.update_tag()
    armature.update_tag(refresh={"OBJECT"})
    context.view_layer.update()
    _mode_set(context, armature, "POSE")
    context.view_layer.update()

    prepared = []
    for plan, _result, _saved_rest in computations:
        if plan.preserve_pole_angle:
            prepared_plan = plan
        else:
            configured_direction = (
                plan.configured_pole_direction
                if plan.configured_pole_direction is not None
                else plan.pole_direction
            )
            old_total = (plan.joint - plan.start).length + (plan.end - plan.joint).length
            distance_ratio = (plan.pole_head - plan.pole_joint).length / max(old_total, EPSILON)
            rebuilt = _build_plan(
                armature,
                plan.chain,
                max(distance_ratio, 0.25),
                configured_direction,
            )
            prepared_plan = replace(
                rebuilt,
                rig_id=plan.rig_id,
                auto_align=bool(plan.auto_align),
            )
        prepared.append(_direct_arm_target_frame(armature, prepared_plan))
        registry["limbs"][plan.rig_id] = applied_by_rig[plan.rig_id]
    _write_direct_rest_registry(armature, registry)
    return prepared


def _constraint_registry(pose_bone, *, strict=False):
    raw = pose_bone.get(CONSTRAINT_REGISTRY_KEY, "")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise LimbIKError(f"Limb IK registry on bone '{pose_bone.name}' is corrupt.") from exc
        return {}
    if not isinstance(payload, dict):
        if strict:
            raise LimbIKError(f"Limb IK registry on bone '{pose_bone.name}' is invalid.")
        return {}
    return payload


def _write_constraint_registry(pose_bone, registry):
    if registry:
        pose_bone[CONSTRAINT_REGISTRY_KEY] = _canonical_json(registry)
    elif CONSTRAINT_REGISTRY_KEY in pose_bone:
        del pose_bone[CONSTRAINT_REGISTRY_KEY]


def _constraint_name(rig_id, role):
    return f"CDLimbIK_{role}_{rig_id}"


def _tag(target, armature_id, *, role, rig_id="", kind="", side="", chain=()):
    target[OWNER_KEY] = OWNER_VALUE
    target[VERSION_KEY] = RIG_VERSION
    target[ARMATURE_ID_KEY] = armature_id
    target[ROLE_KEY] = role
    if rig_id:
        target[RIG_ID_KEY] = rig_id
    if kind:
        target[KIND_KEY] = kind
    if side:
        target[SIDE_KEY] = side
    if chain:
        target[CHAIN_KEY] = _canonical_json(list(chain))


def _owned(target, armature_id, *, role=None, rig_id=None):
    try:
        return (
            target.get(OWNER_KEY) == OWNER_VALUE
            and target.get(VERSION_KEY) == RIG_VERSION
            and target.get(ARMATURE_ID_KEY) == armature_id
            and (role is None or target.get(ROLE_KEY) == role)
            and (rig_id is None or target.get(RIG_ID_KEY) == rig_id)
        )
    except (AttributeError, ReferenceError):
        return False


def _custom_shape_anchor_world(armature, pose_bone):
    """Return the displayed Custom Shape origin, including visual offset."""

    transform = pose_bone.custom_shape_transform or pose_bone
    local_offset = Vector(pose_bone.custom_shape_translation)
    return armature.matrix_world @ (transform.matrix @ local_offset)


def _custom_shape_state_matrix(state):
    """Return one Custom Shape's display-only local TRS matrix."""

    return (
        Matrix.Translation(Vector(state["translation"]))
        @ Euler(tuple(state["rotation"]), "XYZ").to_matrix().to_4x4()
        @ Matrix.Diagonal((*tuple(state["scale"]), 1.0))
    )


def _custom_shape_state_from_matrix(matrix, compatible_rotation, label):
    """Decompose a finite, shear-free Custom Shape local display matrix."""

    if not _matrix_is_finite(matrix):
        raise LimbIKError(f"{label} cannot derive a finite Auto Align display frame.")
    translation, quaternion, scale = matrix.decompose()
    rotation = quaternion.normalized().to_euler(
        "XYZ",
        Euler(tuple(compatible_rotation), "XYZ"),
    )
    if any(
        not math.isfinite(float(value))
        for value in (*translation, *rotation, *scale)
    ):
        raise LimbIKError(f"{label} cannot derive a finite Auto Align visual offset.")
    state = {
        "version": CONTROL_VISUAL_DEFAULT_VERSION,
        "scale": [float(value) for value in scale],
        "translation": [float(value) for value in translation],
        "rotation": [float(value) for value in rotation],
    }
    rebuilt = _custom_shape_state_matrix(state)
    magnitude = max(
        1.0,
        *(abs(float(matrix[row][column])) for row in range(4) for column in range(4)),
    )
    residual = max(
        abs(float(matrix[row][column] - rebuilt[row][column]))
        for row in range(4)
        for column in range(4)
    )
    if residual > 2.0e-5 * magnitude:
        raise LimbIKError(
            f"{label} has display shear that Auto Align cannot preserve safely."
        )
    return state


def _retarget_custom_shape_frame(
    pose_bone,
    transform,
    *,
    previous_reference_matrix=None,
):
    """Change a display-only transform reference with an explicit handoff.

    The default path preserves the current displayed Armature-space frame.
    Auto Align enable instead supplies the evaluated end-bone matrix from
    immediately before the mode switch.  In that case the Custom Shape keeps
    its complete relation to the wrist/ankle and receives the same natural
    alignment delta as that end bone.
    """

    current_transform = pose_bone.custom_shape_transform or pose_bone
    next_transform = transform or pose_bone
    next_matrix = next_transform.matrix.copy()
    if (
        not _matrix_is_finite(next_matrix)
        or abs(float(next_matrix.to_3x3().determinant())) <= EPSILON
    ):
        raise LimbIKError(
            f"Control '{pose_bone.name}' cannot use a singular Auto Align display frame."
        )
    old_visual = _control_visual_state(pose_bone)
    current_frame = current_transform.matrix @ _custom_shape_state_matrix(old_visual)
    reference_matrix = (
        previous_reference_matrix.copy()
        if previous_reference_matrix is not None
        else next_matrix
    )
    if (
        not _matrix_is_finite(reference_matrix)
        or abs(float(reference_matrix.to_3x3().determinant())) <= EPSILON
    ):
        raise LimbIKError(
            f"Control '{pose_bone.name}' cannot use a singular prior Auto Align display frame."
        )
    next_local = reference_matrix.inverted() @ current_frame
    next_visual = _custom_shape_state_from_matrix(
        next_local,
        old_visual["rotation"],
        f"Control '{pose_bone.name}'",
    )

    old_transform = pose_bone.custom_shape_transform
    default_present = CONTROL_VISUAL_DEFAULT_KEY in pose_bone.bone
    default_raw = pose_bone.bone.get(CONTROL_VISUAL_DEFAULT_KEY, None)
    next_default = None
    if default_present:
        current_default = _parse_control_visual_state(
            default_raw,
            f"Control '{pose_bone.name}'",
        )
        default_frame = (
            current_transform.matrix
            @ _custom_shape_state_matrix(current_default)
        )
        next_default = _custom_shape_state_from_matrix(
            reference_matrix.inverted() @ default_frame,
            current_default["rotation"],
            f"Control '{pose_bone.name}' default",
        )
    try:
        pose_bone.custom_shape_transform = transform
        _apply_control_visual_state(pose_bone, next_visual)
        if next_default is not None:
            pose_bone.bone[CONTROL_VISUAL_DEFAULT_KEY] = _canonical_json(next_default)
        actual_frame = next_matrix @ _custom_shape_state_matrix(
            _control_visual_state(pose_bone)
        )
        expected_frame = (
            current_frame
            if previous_reference_matrix is None
            else next_matrix @ next_local
        )
        actual_residual = max(
            abs(float(expected_frame[row][column] - actual_frame[row][column]))
            for row in range(4)
            for column in range(4)
        )
        actual_magnitude = max(
            1.0,
            *(abs(float(expected_frame[row][column])) for row in range(4) for column in range(4)),
        )
        if actual_residual > 2.0e-5 * actual_magnitude:
            raise LimbIKError(
                f"Control '{pose_bone.name}' could not preserve its visible Auto Align frame."
            )
    except Exception:
        pose_bone.custom_shape_transform = old_transform
        _apply_control_visual_state(pose_bone, old_visual)
        if default_present:
            pose_bone.bone[CONTROL_VISUAL_DEFAULT_KEY] = default_raw
        elif CONTROL_VISUAL_DEFAULT_KEY in pose_bone.bone:
            del pose_bone.bone[CONTROL_VISUAL_DEFAULT_KEY]
        raise


def _theme_rgba(value, fallback):
    """Return one finite theme color as an opaque GPU RGBA tuple."""

    try:
        components = tuple(float(component) for component in value)
    except (TypeError, ValueError, OverflowError):
        components = ()
    if len(components) not in {3, 4} or any(
        not math.isfinite(component) for component in components
    ):
        return fallback
    return (*components[:3], 1.0)


def _pole_guide_color(context, armature, pole_data, pole_pose, state):
    """Resolve the same semantic color Blender uses for the Pole Custom Shape."""

    fallback = {
        "NORMAL": (0.0, 0.0, 0.0, 1.0),
        "SELECTED": (0.12, 0.78, 0.92, 1.0),
        "ACTIVE": (0.12, 0.78, 0.92, 1.0),
    }[state]
    try:
        theme = context.preferences.themes[0]
        color = pole_data.color
        palette = color.palette
        # When Armature bone colors are disabled, Blender falls back to its
        # ordinary pose/wire colors even if a per-bone palette is stored.
        if not bool(getattr(armature.data, "show_bone_colors", True)):
            palette = "DEFAULT"
        else:
            # Blender 4+ lets a PoseBone override its Armature Bone color.
            # DEFAULT on the PoseBone means "inherit the Bone color"; any
            # other palette is the effective color used by its Custom Shape.
            pose_color = getattr(pole_pose, "color", None)
            pose_palette = getattr(pose_color, "palette", "DEFAULT")
            if pose_palette != "DEFAULT":
                color = pose_color
                palette = pose_palette
        slot = {
            "NORMAL": "normal",
            "SELECTED": "select",
            "ACTIVE": "active",
        }[state]
        if palette == "CUSTOM":
            return _theme_rgba(getattr(color.custom, slot), fallback)
        if isinstance(palette, str) and palette.startswith("THEME"):
            index = int(palette[5:]) - 1
            color_set = theme.bone_color_sets[index]
            return _theme_rgba(getattr(color_set, slot), fallback)
        view_3d = theme.view_3d
        attribute = {
            "NORMAL": "wire",
            "SELECTED": "bone_pose",
            "ACTIVE": "bone_pose_active",
        }[state]
        return _theme_rgba(getattr(view_3d, attribute), fallback)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return fallback


def _direct_pole_guide_segments(context):
    """Return visible Direct-IK joint-to-Pole segments in world space.

    This is deliberately a tolerant, read-only viewport query rather than a
    full ownership audit.  A partially edited rig is skipped here and remains
    the strict operators' responsibility; drawing must never block the UI or
    mutate Blender data.
    """

    view_layer = getattr(context, "view_layer", None)
    if view_layer is None or getattr(context, "mode", "") != "POSE":
        return ()
    segments = []
    for armature in tuple(view_layer.objects):
        if (
            armature.type != "ARMATURE"
            or armature.pose is None
            or getattr(armature, "mode", "OBJECT") != "POSE"
        ):
            continue
        try:
            viewport = getattr(context, "space_data", None)
            if not armature.visible_get(view_layer=view_layer, viewport=viewport):
                continue
            armature_id = armature.data.get(ARMATURE_ID_KEY, "")
            if (
                not isinstance(armature_id, str)
                or not armature_id
                or not _is_direct_preroll_schema(
                    armature.data.get(SCHEMA_KEY, LEGACY_SCHEMA)
                )
            ):
                continue
            world = armature.matrix_world
            for pole_data in armature.data.bones:
                if (
                    not _owned(pole_data, armature_id, role="POLE")
                    or pole_data.hide
                    or _control_shape_style(pole_data, strict=False) == "SPHERE"
                ):
                    continue
                collections = tuple(getattr(pole_data, "collections", ()))
                if collections and not any(
                    bool(getattr(collection, "is_visible_effectively", True))
                    for collection in collections
                ):
                    continue
                raw_chain = pole_data.get(CHAIN_KEY, "")
                try:
                    chain = json.loads(raw_chain)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if (
                    not isinstance(chain, list)
                    or len(chain) != 3
                    or any(not isinstance(name, str) or not name for name in chain)
                ):
                    continue
                lower = armature.pose.bones.get(chain[1])
                pole = armature.pose.bones.get(pole_data.name)
                if lower is None or pole is None:
                    continue
                joint_world = world @ Vector(lower.head)
                pole_world = _custom_shape_anchor_world(armature, pole)
                if (
                    (pole_world - joint_world).length <= EPSILON
                    or any(
                        not math.isfinite(component)
                        for point in (joint_world, pole_world)
                        for component in point
                    )
                ):
                    continue
                selected = bool(getattr(pole, "select", False))
                active_pose_bone = getattr(context, "active_pose_bone", None)
                state = (
                    "ACTIVE"
                    if (
                        selected
                        and context.object is armature
                        and active_pose_bone is not None
                        and active_pose_bone.name == pole.name
                    )
                    else "SELECTED"
                    if selected
                    else "NORMAL"
                )
                segments.append(
                    (
                        pole_data.name,
                        joint_world,
                        pole_world,
                        _pole_guide_color(
                            context,
                            armature,
                            pole_data,
                            pole,
                            state,
                        ),
                    )
                )
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            continue
    segments.sort(key=lambda item: item[0])
    return tuple((joint, pole, color) for _name, joint, pole, color in segments)


def _pole_guide_line_vertices(segments):
    """Build only the always-visible joint-to-Pole shafts for Direct Poles.

    ``WGT_Randy_PoleArrow`` is already the single arrowhead at each Pole.
    The GPU overlay merely joins the bend joint to that displayed tip; it
    must not synthesize a second, view-facing head or create saved rig data.
    """

    vertices = []
    for segment in segments:
        if not isinstance(segment, (list, tuple)) or len(segment) < 2:
            continue
        joint_value, pole_value = segment[:2]
        try:
            joint = Vector(joint_value)
            pole = Vector(pole_value)
        except (TypeError, ValueError):
            continue
        shaft = pole - joint
        if (
            shaft.length <= EPSILON
            or any(not math.isfinite(float(value)) for point in (joint, pole) for value in point)
        ):
            continue
        vertices.extend((joint, pole))
    return tuple(vertices)


def _pole_guide_line_batches(segments):
    """Group valid Pole shafts by their matching Custom Shape color."""

    grouped = {}
    order = []
    for segment in segments:
        if not isinstance(segment, (list, tuple)) or len(segment) < 3:
            continue
        color = _theme_rgba(segment[2], (0.0, 0.0, 0.0, 1.0))
        vertices = _pole_guide_line_vertices((segment,))
        if not vertices:
            continue
        if color not in grouped:
            grouped[color] = []
            order.append(color)
        grouped[color].extend(vertices)
    return tuple((color, tuple(grouped[color])) for color in order)


def _draw_direct_pole_guides():
    global _POLE_GUIDE_SHADER

    if bpy.app.background:
        return
    try:
        context = bpy.context
        space = getattr(context, "space_data", None)
        overlay = getattr(space, "overlay", None)
        if (
            getattr(context, "area", None) is None
            or context.area.type != "VIEW_3D"
            or (overlay is not None and not overlay.show_overlays)
            or (overlay is not None and hasattr(overlay, "show_bones") and not overlay.show_bones)
        ):
            return
        segments = _direct_pole_guide_segments(context)
        if not segments:
            return

        import gpu
        from gpu_extras.batch import batch_for_shader

        if _POLE_GUIDE_SHADER is None:
            _POLE_GUIDE_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
        batches = _pole_guide_line_batches(segments)
        if not batches:
            return
        previous_depth = gpu.state.depth_test_get()
        previous_depth_mask = gpu.state.depth_mask_get()
        previous_blend = gpu.state.blend_get()
        previous_line_width = gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(2.0)
            for color, vertices in batches:
                batch = batch_for_shader(
                    _POLE_GUIDE_SHADER,
                    "LINES",
                    {"pos": vertices},
                )
                _POLE_GUIDE_SHADER.bind()
                _POLE_GUIDE_SHADER.uniform_float("color", color)
                batch.draw(_POLE_GUIDE_SHADER)
        finally:
            for restore, value in (
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
        # Viewport drawing is advisory.  A transient GPU/context failure must
        # never interfere with rig editing or leave scene data behind.
        _POLE_GUIDE_SHADER = None


def register_limb_ik_viewport_handler():
    """Install exactly one hot-reload-safe Direct Pole guide handler."""

    global _POLE_GUIDE_DRAW_HANDLE
    if bpy.app.background:
        return
    namespace = bpy.app.driver_namespace
    namespaced_handle = namespace.pop(POLE_GUIDE_HANDLER_KEY, None)
    handles = []
    for handle in (namespaced_handle, _POLE_GUIDE_DRAW_HANDLE):
        if handle is not None and all(handle is not existing for existing in handles):
            handles.append(handle)
    _POLE_GUIDE_DRAW_HANDLE = None
    for handle in handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass
    _POLE_GUIDE_DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
        _draw_direct_pole_guides,
        (),
        "WINDOW",
        "POST_VIEW",
    )
    namespace[POLE_GUIDE_HANDLER_KEY] = _POLE_GUIDE_DRAW_HANDLE


def unregister_limb_ik_viewport_handler():
    """Remove the Direct Pole guide handler without touching any rig data."""

    global _POLE_GUIDE_DRAW_HANDLE, _POLE_GUIDE_SHADER
    namespace = bpy.app.driver_namespace
    namespaced_handle = namespace.pop(POLE_GUIDE_HANDLER_KEY, None)
    handles = []
    for handle in (namespaced_handle, _POLE_GUIDE_DRAW_HANDLE):
        if handle is not None and all(handle is not existing for existing in handles):
            handles.append(handle)
    _POLE_GUIDE_DRAW_HANDLE = None
    _POLE_GUIDE_SHADER = None
    for handle in handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass


def _pose_shape_json_state(pose_bone):
    """Capture reversible display-only PoseBone state as JSON-safe values."""
    return {
        "custom_shape": pose_bone.custom_shape.name if pose_bone.custom_shape is not None else "",
        "custom_shape_transform": (
            pose_bone.custom_shape_transform.name
            if pose_bone.custom_shape_transform is not None
            else ""
        ),
        "use_bone_size": bool(pose_bone.use_custom_shape_bone_size),
        "scale": [float(value) for value in pose_bone.custom_shape_scale_xyz],
        "translation": [float(value) for value in pose_bone.custom_shape_translation],
        "rotation": [float(value) for value in pose_bone.custom_shape_rotation_euler],
        "wire_width": (
            float(pose_bone.custom_shape_wire_width)
            if hasattr(pose_bone, "custom_shape_wire_width")
            else None
        ),
    }


def _pose_shape_runtime_state(pose_bone):
    """Capture exact in-memory references for Build rollback."""
    state = _pose_shape_json_state(pose_bone)
    state["custom_shape"] = pose_bone.custom_shape
    state["custom_shape_transform"] = pose_bone.custom_shape_transform
    return state


def _shape_vector(state, key, label):
    value = state.get(key)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise LimbIKError(f"{label} has an invalid {key} vector.")
    try:
        components = tuple(float(component) for component in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LimbIKError(f"{label} has an invalid {key} vector.") from exc
    if any(not math.isfinite(component) for component in components):
        raise LimbIKError(f"{label} has a non-finite {key} vector.")
    return components


def _validate_shape_state(state, label, *, original=False):
    if not isinstance(state, dict):
        raise LimbIKError(f"{label} is not a valid Custom Shape state.")
    if original:
        # Character Designer never silently replaces a source bone that already
        # has a shape or transform.  Empty references make restoration immune
        # to unrelated Object/Bone renames elsewhere in the file.
        if state.get("custom_shape") != "" or state.get("custom_shape_transform") != "":
            raise LimbIKError(f"{label} claims a pre-existing Custom Shape that Character Designer must not own.")
    if not isinstance(state.get("use_bone_size"), bool):
        raise LimbIKError(f"{label} has an invalid bone-size flag.")
    _shape_vector(state, "scale", label)
    _shape_vector(state, "translation", label)
    _shape_vector(state, "rotation", label)
    wire_width = state.get("wire_width")
    if wire_width is not None:
        try:
            wire_width = float(wire_width)
        except (TypeError, ValueError, OverflowError) as exc:
            raise LimbIKError(f"{label} has an invalid wire width.") from exc
        if not math.isfinite(wire_width):
            raise LimbIKError(f"{label} has a non-finite wire width.")


def _control_visual_state(pose_bone):
    """Capture only the artist-editable Custom Shape display vectors."""

    return {
        "version": CONTROL_VISUAL_DEFAULT_VERSION,
        "scale": [float(value) for value in pose_bone.custom_shape_scale_xyz],
        "translation": [float(value) for value in pose_bone.custom_shape_translation],
        "rotation": [float(value) for value in pose_bone.custom_shape_rotation_euler],
    }


def _parse_control_visual_state(raw, label):
    try:
        state = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LimbIKError(f"{label} has invalid visual-default data.") from exc
    if not isinstance(state, dict) or state.get("version") != CONTROL_VISUAL_DEFAULT_VERSION:
        raise LimbIKError(f"{label} has unsupported visual-default data.")
    return {
        "version": CONTROL_VISUAL_DEFAULT_VERSION,
        "scale": list(_shape_vector(state, "scale", label)),
        "translation": list(_shape_vector(state, "translation", label)),
        "rotation": list(_shape_vector(state, "rotation", label)),
    }


def _write_control_visual_default(pose_bone):
    if pose_bone.bone.get(ROLE_KEY) not in CONTROL_VISUAL_ROLES:
        return
    pose_bone.bone[CONTROL_VISUAL_DEFAULT_KEY] = _canonical_json(
        _control_visual_state(pose_bone)
    )


def _apply_control_visual_state(pose_bone, state):
    parsed = _parse_control_visual_state(state, f"Control '{pose_bone.name}'")
    pose_bone.custom_shape_scale_xyz = parsed["scale"]
    pose_bone.custom_shape_translation = parsed["translation"]
    pose_bone.custom_shape_rotation_euler = parsed["rotation"]


def _control_shape_style(bone, *, strict=True):
    """Return a generated control's declared display style.

    DEFAULT is represented by an absent ID property so rigs made before this
    feature remain valid without a migration write.
    """

    raw = bone.get(CONTROL_SHAPE_STYLE_KEY, None)
    if raw is None:
        return "DEFAULT"
    if not isinstance(raw, str) or raw not in CONTROL_SHAPE_STYLES - {"DEFAULT"}:
        if strict:
            raise LimbIKError(
                f"Control '{bone.name}' has an invalid Custom Shape style declaration."
            )
        return "DEFAULT"
    return raw


def _control_pose_bones(armature, inventory):
    """Yield each generated animator control exactly once."""

    names = []
    master = inventory.get("master")
    if master is not None:
        names.append(master.name)
    for rig in inventory["rigs"].values():
        names.extend((rig["target"].name, rig["pole"].name))
        if rig["heel"] is not None:
            names.append(rig["heel"].name)
    return tuple(armature.pose.bones[name] for name in dict.fromkeys(names))


def _default_control_shape_kind(inventory, bone, available_kinds):
    """Resolve the canonical generated widget behind the DEFAULT choice."""

    role = bone.get(ROLE_KEY, "")
    if role == "MASTER":
        return "MASTER"
    if role == "HAND_IK":
        return "HAND"
    if role == "FOOT_IK":
        if _is_direct_preroll_schema(inventory["schema"]):
            side = bone.get(SIDE_KEY, "")
            sided = f"FOOT_{side}"
            if sided in available_kinds:
                return sided
            if "FOOT" in available_kinds:
                return "FOOT"
            raise LimbIKError(
                f"Control '{bone.name}' has no canonical Direct Foot widget."
            )
        return "FOOT"
    if role == "POLE":
        return "POLE" if inventory["schema"] == LEGACY_SCHEMA else "POLE_ARROW"
    if role == "HEEL_ROLL":
        return "HEEL"
    raise LimbIKError(f"Bone '{bone.name}' is not a supported animation control.")


def _expected_control_shape_kind(inventory, bone, available_kinds, *, style=None):
    style = _control_shape_style(bone) if style is None else style
    if style not in CONTROL_SHAPE_STYLES:
        raise LimbIKError(f"Control '{bone.name}' requested an unknown Custom Shape style.")
    if style == "DEFAULT":
        return _default_control_shape_kind(inventory, bone, available_kinds)
    if style == "SPHERE":
        return "CONTROL_SPHERE"
    # The Pole's existing tip-anchored Arrow is the arrowhead of its dynamic
    # line.  Reusing it avoids drawing a second shaft on top of the live one.
    if bone.get(ROLE_KEY) == "POLE" and inventory["schema"] != LEGACY_SCHEMA:
        return "POLE_ARROW"
    return "CONTROL_ARROW"


def _pole_connector_bone(inventory, pole_bone):
    """Return the Stable VIS connector owned by ``pole_bone``, if any."""

    if pole_bone.get(ROLE_KEY) != "POLE":
        return None
    matches = [
        rig
        for rig in inventory["rigs"].values()
        if rig["pole"].name == pole_bone.name
    ]
    if len(matches) != 1:
        raise LimbIKError(
            f"Control '{pole_bone.name}' has no unique generated Pole rig."
        )
    return matches[0].get("line")


def _sync_pole_connector_visibility(inventory, pole_bone, style):
    """Keep the separate Stable shaft consistent with the Pole shape choice.

    Direct Pre-Roll shafts are GPU overlays and are filtered by
    ``_direct_pole_guide_segments``. Stable rigs retain their constrained VIS
    helper, but hide only its display while the Pole uses Sphere Wire.
    """

    line = _pole_connector_bone(inventory, pole_bone)
    if line is not None:
        line.hide = style == "SPHERE"
    return line


def _restore_pose_shape_state(armature, pose_bone, state, *, runtime=False):
    if runtime:
        shape = state.get("custom_shape")
        transform = state.get("custom_shape_transform")
    else:
        _validate_shape_state(state, f"Saved source bone '{pose_bone.name}'", original=True)
        shape_name = state.get("custom_shape", "")
        transform_name = state.get("custom_shape_transform", "")
        shape = bpy.data.objects.get(shape_name) if shape_name else None
        transform = armature.pose.bones.get(transform_name) if transform_name else None
        if shape_name and shape is None:
            raise LimbIKError(f"Saved source Custom Shape '{shape_name}' no longer exists.")
        if transform_name and transform is None:
            raise LimbIKError(f"Saved source Custom Shape Transform '{transform_name}' no longer exists.")
    pose_bone.custom_shape = shape
    pose_bone.custom_shape_transform = transform
    pose_bone.use_custom_shape_bone_size = bool(state["use_bone_size"])
    pose_bone.custom_shape_scale_xyz = _shape_vector(state, "scale", f"Source bone '{pose_bone.name}'")
    pose_bone.custom_shape_translation = _shape_vector(state, "translation", f"Source bone '{pose_bone.name}'")
    pose_bone.custom_shape_rotation_euler = _shape_vector(state, "rotation", f"Source bone '{pose_bone.name}'")
    if hasattr(pose_bone, "custom_shape_wire_width") and state.get("wire_width") is not None:
        pose_bone.custom_shape_wire_width = float(state["wire_width"])


def _parse_source_widget_registry(armature, raw):
    if not isinstance(raw, str) or not raw:
        raise LimbIKError("The source-control Custom Shape registry is not valid JSON text.")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("version") not in {
        LEGACY_SOURCE_WIDGETS_VERSION,
        SOURCE_WIDGETS_VERSION,
    }:
        raise LimbIKError("The source-control Custom Shape registry version is unsupported.")
    armature_id = armature.data.get(ARMATURE_ID_KEY, "")
    if not isinstance(payload.get("armature_id"), str) or payload.get("armature_id") != armature_id:
        raise LimbIKError("The source-control Custom Shape registry belongs to another Armature ID.")
    records = payload.get("bones")
    if not isinstance(records, dict) or not records:
        raise LimbIKError("The source-control Custom Shape registry is empty or malformed.")
    for bone_name, record in records.items():
        if not isinstance(bone_name, str) or not bone_name or not isinstance(record, dict):
            raise LimbIKError("The source-control Custom Shape registry contains an invalid bone record.")
        bone = armature.data.bones.get(bone_name)
        if bone is None or bone.get(OWNER_KEY) == OWNER_VALUE:
            raise LimbIKError(f"Decorated source bone '{bone_name}' is missing or generated.")
        if record.get("kind") not in {"FINGER", "SHOULDER"} or record.get("side") not in SIDES:
            raise LimbIKError(f"Decorated source bone '{bone_name}' has invalid type metadata.")
        _validate_shape_state(record.get("original"), f"Saved source bone '{bone_name}'", original=True)
        _validate_shape_state(record.get("generated"), f"Generated source bone '{bone_name}'")
    return payload


def _load_source_widget_registry(armature, *, strict=True):
    raw = armature.data.get(SOURCE_WIDGETS_KEY, None)
    if raw is None:
        return None
    try:
        return _parse_source_widget_registry(armature, raw)
    except (LimbIKError, json.JSONDecodeError, TypeError, ValueError):
        if strict:
            raise
        return None


def _write_source_widget_registry(armature, registry):
    armature.data[SOURCE_WIDGETS_KEY] = _canonical_json(registry)


def _shape_values_match(actual, expected, tolerance=1.0e-6):
    return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(actual, expected))


def _validate_source_widget_assignments(armature, registry, rigs=None):
    if registry is None:
        return
    armature_id = registry["armature_id"]
    for bone_name, record in registry["bones"].items():
        if rigs is not None and ("ARM", record["side"]) not in rigs:
            raise LimbIKError(f"Decorated source bone '{bone_name}' has no matching generated Arm rig.")
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            raise LimbIKError(f"Decorated source pose bone '{bone_name}' is missing.")
        shape = pose_bone.custom_shape
        if not _owned(shape, armature_id, role="WIDGET") or shape.get(KIND_KEY) != record["kind"]:
            raise LimbIKError(f"Source bone '{bone_name}' Custom Shape assignment was edited.")
        generated = record["generated"]
        if (
            pose_bone.custom_shape_transform is not None
            or bool(pose_bone.use_custom_shape_bone_size) != bool(generated["use_bone_size"])
            or not _shape_values_match(pose_bone.custom_shape_scale_xyz, generated["scale"])
            or not _shape_values_match(pose_bone.custom_shape_translation, generated["translation"])
            or not _shape_values_match(pose_bone.custom_shape_rotation_euler, generated["rotation"])
            or (
                hasattr(pose_bone, "custom_shape_wire_width")
                and generated.get("wire_width") is not None
                and abs(float(pose_bone.custom_shape_wire_width) - float(generated["wire_width"])) > 1.0e-6
            )
        ):
            raise LimbIKError(f"Source bone '{bone_name}' Custom Shape display settings were edited.")


def _restore_source_widget_originals(armature, registry, *, strict_current=True):
    if registry is None:
        return
    if strict_current:
        _validate_source_widget_assignments(armature, registry)
    for bone_name, record in registry["bones"].items():
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is not None:
            _restore_pose_shape_state(armature, pose_bone, record["original"])


def _remember_source_widget_state(armature, pose_bone, transaction):
    if not transaction["source_widgets_touched"]:
        transaction["source_widgets_before"] = armature.data.get(SOURCE_WIDGETS_KEY, None)
        transaction["source_widgets_touched"] = True
    if pose_bone is not None and pose_bone.name not in transaction["source_shape_before"]:
        transaction["source_shape_before"][pose_bone.name] = _pose_shape_runtime_state(pose_bone)


def _source_widget_generated_state(armature, bone_name, kind, side):
    pose_bone = armature.pose.bones[bone_name]
    length = max(float(armature.data.bones[bone_name].length), 1.0e-5)
    if kind == "FINGER":
        scale = [length * 0.75] * 3
        if side == "R":
            scale[0] = -scale[0]
    else:
        scale = [length * 1.4875] * 3
    return {
        "use_bone_size": False,
        "scale": scale,
        # Finger widgets are authored around their local origin, so zero
        # translation places each ring on the source bone head (the joint).
        "translation": [0.0, 0.0, 0.0],
        "rotation": [0.0, 0.0, 0.0],
        "wire_width": 2.0 if hasattr(pose_bone, "custom_shape_wire_width") else None,
    }


def _apply_source_widget_generated_state(pose_bone, generated):
    pose_bone.custom_shape_transform = None
    pose_bone.use_custom_shape_bone_size = bool(generated["use_bone_size"])
    pose_bone.custom_shape_scale_xyz = _shape_vector(
        generated, "scale", f"Generated source bone '{pose_bone.name}'"
    )
    pose_bone.custom_shape_translation = _shape_vector(
        generated, "translation", f"Generated source bone '{pose_bone.name}'"
    )
    pose_bone.custom_shape_rotation_euler = _shape_vector(
        generated, "rotation", f"Generated source bone '{pose_bone.name}'"
    )
    if hasattr(pose_bone, "custom_shape_wire_width") and generated.get("wire_width") is not None:
        pose_bone.custom_shape_wire_width = float(generated["wire_width"])


def _migrate_source_widget_registry(armature, registry, transaction):
    if registry["version"] == SOURCE_WIDGETS_VERSION:
        return False
    if registry["version"] != LEGACY_SOURCE_WIDGETS_VERSION:
        raise LimbIKError("The source-control Custom Shape registry version is unsupported.")

    # A v1 registry placed Finger rings at the bone midpoint.  Treat its saved
    # generated state as an ownership contract: never move a live control that
    # the artist changed after Build.
    _validate_source_widget_assignments(armature, registry)
    finger_records = [
        (bone_name, record)
        for bone_name, record in registry["bones"].items()
        if record["kind"] == "FINGER"
    ]
    if finger_records:
        _remember_source_widget_state(armature, armature.pose.bones[finger_records[0][0]], transaction)
        for bone_name, _record in finger_records[1:]:
            _remember_source_widget_state(armature, armature.pose.bones[bone_name], transaction)
        for bone_name, record in finger_records:
            generated = record["generated"]
            generated["translation"] = [0.0, 0.0, 0.0]
            _apply_source_widget_generated_state(armature.pose.bones[bone_name], generated)
    elif not transaction["source_widgets_touched"]:
        transaction["source_widgets_before"] = armature.data.get(SOURCE_WIDGETS_KEY, None)
        transaction["source_widgets_touched"] = True
    registry["version"] = SOURCE_WIDGETS_VERSION
    return True


def _restore_source_widget_registry_snapshot(context, armature, raw, transaction, *, schema=CURRENT_SCHEMA):
    """Restore an exact saved source-widget registry during Rebuild recovery.

    Source-control eligibility can legitimately drift while a generated rig is
    alive: for example, the artist may clear an old third-party shape from an
    otherwise eligible Finger.  A recovery Build then discovers that extra
    source bone.  Restore the snapshot's exact assignments and registry rather
    than assuming that the newly discovered candidate set is identical.
    """
    if armature.data.get(SOURCE_WIDGETS_KEY, None) == raw:
        return
    saved = _parse_source_widget_registry(armature, raw)
    live = _load_source_widget_registry(armature, strict=False)
    if live is not None:
        _validate_source_widget_assignments(armature, live)
    live_records = live["bones"] if live is not None else {}
    saved_records = saved["bones"]

    # Undo controls discovered only by the recovery Build.  Their live record
    # contains the exact state that existed at the snapshot boundary.
    for bone_name, live_record in live_records.items():
        if bone_name in saved_records:
            continue
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            raise LimbIKError(f"Recovery-only source bone '{bone_name}' disappeared.")
        _remember_source_widget_state(armature, pose_bone, transaction)
        _restore_pose_shape_state(armature, pose_bone, live_record["original"])

    widget_collection = _ensure_widget_collection(
        context,
        saved["armature_id"],
        transaction,
    )
    widgets = {
        kind: _ensure_widget(
            context,
            saved["armature_id"],
            widget_collection,
            kind,
            transaction,
            schema=schema,
        )
        for kind in sorted({record["kind"] for record in saved_records.values()})
    }
    for bone_name, saved_record in saved["bones"].items():
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            raise LimbIKError(f"Saved source bone '{bone_name}' disappeared during recovery.")
        live_record = live_records.get(bone_name)
        if live_record is not None:
            if (
                saved_record["kind"] != live_record["kind"]
                or saved_record["side"] != live_record["side"]
                or _canonical_json(saved_record["original"])
                != _canonical_json(live_record["original"])
            ):
                raise LimbIKError(f"Source-control recovery for '{bone_name}' is mismatched.")
        elif _canonical_json(_pose_shape_json_state(pose_bone)) != _canonical_json(
            saved_record["original"]
        ):
            raise LimbIKError(
                f"Saved source bone '{bone_name}' no longer matches its original display state."
            )
        _remember_source_widget_state(armature, pose_bone, transaction)
        pose_bone.custom_shape = widgets[saved_record["kind"]]
        _apply_source_widget_generated_state(
            pose_bone,
            saved_record["generated"],
        )

    # If eligibility drift introduced an entirely new source-widget kind,
    # remove that now-unused owned object/data so inventory matches the exact
    # snapshot rather than retaining recovery-only residue.
    extra_kinds = {
        record["kind"] for record in live_records.values()
    } - {record["kind"] for record in saved_records.values()}
    for kind in sorted(extra_kinds):
        matches = [
            obj
            for obj in bpy.data.objects
            if _owned(obj, saved["armature_id"], role="WIDGET")
            and obj.get(KIND_KEY) == kind
        ]
        if len(matches) != 1:
            raise LimbIKError(f"Recovery-only {kind.title()} widget ownership is ambiguous.")
        shape = matches[0]
        if any(pose_bone.custom_shape is shape for pose_bone in armature.pose.bones):
            raise LimbIKError(f"Recovery-only {kind.title()} widget is still assigned.")
        mesh = shape.data
        bpy.data.objects.remove(shape, do_unlink=True)
        if (
            mesh is not None
            and bpy.data.meshes.get(mesh.name) is mesh
            and not mesh.users
        ):
            bpy.data.meshes.remove(mesh)
    armature.data[SOURCE_WIDGETS_KEY] = raw
    _validate_source_widget_assignments(armature, saved)


def _source_widget_candidates(armature, arm_chains):
    candidates = {"FINGER": {}, "SHOULDER": {}}
    for chain in arm_chains:
        if chain.kind != "ARM":
            continue
        upper = armature.data.bones.get(chain.upper)
        hand = armature.data.bones.get(chain.end)
        shoulder = upper.parent if upper is not None else None
        if shoulder is not None:
            tokens, _base = _name_parts(shoulder.name)
            if (
                shoulder.get(OWNER_KEY) != OWNER_VALUE
                and shoulder.use_deform
                and _SHOULDER_TOKENS.intersection(tokens)
                and _side_from_name(shoulder.name) == chain.side
            ):
                candidates["SHOULDER"][shoulder.name] = chain.side
        if hand is None:
            continue
        for bone in hand.children_recursive:
            tokens, _base = _name_parts(bone.name)
            if (
                bone.get(OWNER_KEY) == OWNER_VALUE
                or not bone.use_deform
                or _side_from_name(bone.name) != chain.side
                or not _FINGER_TOKENS.intersection(tokens)
                or _EXCLUDED_TOKENS.intersection(tokens)
            ):
                continue
            depth = 0
            cursor = bone
            valid_path = True
            while cursor is not None and cursor.name != hand.name:
                depth += 1
                if depth > 3:
                    valid_path = False
                    break
                cursor_tokens, _cursor_base = _name_parts(cursor.name)
                if not _FINGER_TOKENS.intersection(cursor_tokens):
                    valid_path = False
                    break
                cursor = cursor.parent
            if valid_path and cursor is not None and cursor.name == hand.name:
                candidates["FINGER"][bone.name] = chain.side
    return {
        kind: tuple((name, names[name]) for name in sorted(names))
        for kind, names in candidates.items()
    }


def _decorate_source_widgets(context, armature, armature_id, arm_chains, widget_collection, transaction, *, schema=CURRENT_SCHEMA):
    candidates = _source_widget_candidates(armature, arm_chains)
    registry = _load_source_widget_registry(armature, strict=True)
    had_registry = registry is not None
    if registry is None:
        registry = {"version": SOURCE_WIDGETS_VERSION, "armature_id": armature_id, "bones": {}}
    changed = _migrate_source_widget_registry(armature, registry, transaction) if had_registry else False
    widgets = {}
    for kind in ("FINGER", "SHOULDER"):
        for bone_name, side in candidates[kind]:
            if bone_name in registry["bones"]:
                continue
            pose_bone = armature.pose.bones[bone_name]
            # Existing artist/third-party controls remain wholly outside this
            # feature's ownership and lifecycle.
            if pose_bone.custom_shape is not None or pose_bone.custom_shape_transform is not None:
                continue
            if kind not in widgets:
                widgets[kind] = _ensure_widget(
                    context, armature_id, widget_collection, kind, transaction, schema=schema
                )
            _remember_source_widget_state(armature, pose_bone, transaction)
            generated = _source_widget_generated_state(armature, bone_name, kind, side)
            original = _pose_shape_json_state(pose_bone)
            pose_bone.custom_shape = widgets[kind]
            _apply_source_widget_generated_state(pose_bone, generated)
            registry["bones"][bone_name] = {
                "kind": kind,
                "side": side,
                "original": original,
                "generated": generated,
            }
            changed = True
    if changed:
        _write_source_widget_registry(armature, registry)
    elif had_registry:
        _validate_source_widget_assignments(armature, registry)
    return registry if (changed or had_registry) else None


def _smooth_foot_outline_geometry(samples=33):
    """Return a semantic, smooth single-cycle sole used by sided Foot widgets.

    Raw +Y is the toe direction and raw +X is the medial / big-toe edge.
    Fitted Direct widgets replace these normalized coordinates with an exact
    outline of their own visible shoe, but this also gives mesh-less builds a
    useful, side-independent fallback instead of the old shared Rain mesh.
    """
    inner = []
    outer = []
    for index in range(samples):
        t = -1.0 + 2.0 * index / (samples - 1)
        inner_cap = math.sqrt(max(0.0, 1.0 - abs(t) ** 6))
        outer_cap = math.sqrt(max(0.0, 1.0 - t * t))
        inner.append((0.24 * inner_cap, 0.5 * t, 0.0))
        outer.append((-0.34 * outer_cap * (1.0 + 0.08 * t), 0.5 * t, 0.0))
    # Both sides meet at the same heel/toe cap vertices.  Omitting the duplicate
    # outer endpoints leaves one degree-2 cycle with no zero-length edges.
    vertices = tuple(inner + list(reversed(outer[1:-1])))
    edges = tuple((index, (index + 1) % len(vertices)) for index in range(len(vertices)))
    return vertices, edges


def _widget_geometry(kind, *, schema=CURRENT_SCHEMA):
    """Return small wire widgets in Bone-local coordinates.

    Enhanced Hand/Master/Pole silhouettes are normalized from Rain's controls
    under the attribution and license recorded in ``ATTRIBUTION.md``;
    they are original mesh data recreated as simple line geometry, not linked or
    copied ID datablocks.  Finger and Shoulder silhouettes are assigned directly
    to existing source bones, so they improve selection without adding controls.
    """
    if schema == LEGACY_SCHEMA and kind not in {
        "FINGER",
        "SHOULDER",
        "CONTROL_ARROW",
        "CONTROL_SPHERE",
    }:
        if kind == "HAND":
            vertices = ((-0.75, 0, -0.55), (0.75, 0, -0.55), (0.9, 0, -0.35), (0.9, 0, 0.35), (0.75, 0, 0.55), (-0.75, 0, 0.55), (-0.9, 0, 0.35), (-0.9, 0, -0.35))
        elif kind == "FOOT":
            vertices = ((-0.65, -0.9, 0), (0.65, -0.9, 0), (0.82, 0.55, 0), (0.5, 1.0, 0), (-0.5, 1.0, 0), (-0.82, 0.55, 0))
        else:
            count = 12
            vertices = tuple((math.cos(index * math.tau / count), 0, math.sin(index * math.tau / count)) for index in range(count))
        edges = tuple((index, (index + 1) % len(vertices)) for index in range(len(vertices)))
        return vertices, edges

    if kind == "HAND":
        vertices = (
            (0.507616699, 0.071150608, -0.000000215), (0.497480452, 0.085770570, -0.097545192),
            (0.467504233, 0.127871066, -0.191341609), (0.418951690, 0.192710653, -0.277785063),
            (0.353838533, 0.273602426, -0.353553325), (0.274862766, 0.361968160, -0.415734798),
            (0.185380891, 0.445415586, -0.461939752), (0.089344025, 0.506406367, -0.490392625),
            (-0.009000459, 0.527022839, -0.5), (-0.105621733, 0.499436408, -0.490392625),
            (-0.197058186, 0.431743503, -0.461939752), (-0.280352741, 0.342119187, -0.415734798),
            (-0.352816731, 0.248339325, -0.353553295), (-0.411986977, 0.163004369, -0.277784944),
            (-0.455785364, 0.094863214, -0.191341534), (-0.482678682, 0.050729472, -0.097544953),
            (-0.491744846, 0.035423212, 0.000000268), (-0.482678592, 0.050729472, 0.097545460),
            (-0.455785334, 0.094863214, 0.191342041), (-0.411986917, 0.163004413, 0.277785450),
            (-0.352816433, 0.248339400, 0.353553653), (-0.280352324, 0.342119187, 0.415735006),
            (-0.197057813, 0.431743443, 0.461939901), (-0.105621301, 0.499436349, 0.490392774),
            (-0.008999976, 0.527022719, 0.5), (0.089344561, 0.506406069, 0.490392566),
            (0.185381427, 0.445415139, 0.461939573), (0.274863482, 0.361967742, 0.415734470),
            (0.353839159, 0.273601979, 0.353552878), (0.418952167, 0.192710176, 0.277784616),
            (0.467504442, 0.127870739, 0.191341296), (0.497480631, 0.085770361, 0.097544789),
        )
        edges = tuple((index, (index + 1) % len(vertices)) for index in range(len(vertices)))
    elif kind == "MASTER":
        vertices = (
            (-0.461178720, 0, -0.461178720), (0.461178720, 0, -0.461178720),
            (-0.461178720, 0, 0.461178720), (0.461178720, 0, 0.461178720),
            (-0.443159282, 0, 0.443159282), (-0.443159282, 0, -0.443159282),
            (0.443159282, 0, -0.443159282), (0.443159282, 0, 0.443159282),
            (0.480463743, 0, -0.265761197), (0.480463743, 0, 0.265761226),
            (0.5, 0, 0.250185013), (0.5, 0, -0.250184983),
            (-0.265761197, 0, -0.480463743), (0.265761197, 0, -0.480463743),
            (0.250185013, 0, -0.5), (-0.250184983, 0, -0.5),
            (-0.480463743, 0, 0.265761226), (-0.480463743, 0, -0.265761197),
            (-0.5, 0, -0.250185013), (-0.5, 0, 0.250185013),
            (0.265761197, 0, 0.480463743), (-0.265761197, 0, 0.480463743),
            (-0.250185013, 0, 0.5), (0.250184983, 0, 0.5),
        )
        edges = ((8, 11), (10, 9), (12, 15), (14, 13), (16, 19), (18, 17), (20, 23), (22, 21), (7, 9), (10, 3), (8, 6), (1, 11), (6, 13), (14, 1), (12, 5), (0, 15), (5, 17), (18, 0), (16, 4), (2, 19), (4, 21), (22, 2), (20, 7), (3, 23))
    elif kind == "POLE_ARROW":
        vertices = ((-0.474555194, -1, 0.474554807), (-0.474555194, -0.99999994, -0.474555612), (0.474555194, -0.99999994, -0.474555612), (0.474555194, -1, 0.474554807), (0, 0, -0.000000048))
        edges = ((0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4))
    elif kind == "POLE_LINE":
        vertices, edges = ((0, 0, 0), (0, 1, 0)), ((0, 1),)
    elif kind in {"FOOT_L", "FOOT_R"}:
        vertices, edges = _smooth_foot_outline_geometry()
    elif kind == "FOOT":
        vertices = (
            (0.099234894, 0.871687770, 0.000727093), (0.194205865, -0.071024314, 0.000438197),
            (-0.186365515, 0.825411975, 0.000631400), (-0.007368974, -0.099799521, 0.000472580),
            (0.194076076, 0.578032792, 0.000023564), (-0.278784126, 0.530485034, 0.000021460),
            (-0.159259334, 0.173546672, -0.000034734), (0.210402682, 0.207939595, -0.000031146),
            (0.189964861, 0.628364623, 0.000021308), (0.173066407, 0.732698798, -0.000003148),
            (0.132744923, 0.832533777, -0.000075472), (0.038417019, -0.114003643, -0.000292528),
            (0.101531841, -0.113783777, -0.000094001), (0.160142154, -0.095507771, -0.000294212),
            (-0.120430134, 0.072465695, -0.000068591), (-0.099163838, 0.022602918, -0.000097153),
            (-0.037944019, -0.073271163, 0.000075338), (0.039580546, 0.889435172, -0.000213846),
            (-0.038276959, 0.885206699, -0.000079284), (-0.119038858, 0.861792743, -0.000274932),
            (0.206765726, 0.260585546, -0.000018355), (0.202150643, 0.366447002, 0.000004608),
            (0.199428171, 0.467522144, 0.000018613), (-0.228298619, 0.778306067, -0.000147301),
            (-0.273770511, 0.677303314, -0.000016196), (-0.283078045, 0.578685939, 0.000017398),
            (-0.255540878, 0.424573123, 0.000017627), (-0.221513256, 0.327776104, 0.000002502),
            (-0.179988638, 0.225336537, -0.000021708), (0.211478710, -0.038747311, 0.000076016),
            (0.223457947, 0.059776247, -0.000092626), (0.220201626, 0.108500011, -0.000064606),
            (0.156389371, 0.783612728, -0.000081165), (-0.256016791, 0.727344453, -0.000117335),
            (0.182560578, 0.685647905, 0.000012276), (-0.281778038, 0.633144438, 0.000004574),
            (0.197160974, 0.524336100, 0.000022641), (-0.269585460, 0.479019910, 0.000021530),
            (0.200832501, 0.417848617, 0.000012667), (-0.239865437, 0.377017140, 0.000011302),
            (0.203929767, 0.312606871, -0.000006503), (-0.200427473, 0.276034266, -0.000009422),
            (-0.140166104, 0.122850493, -0.000049585), (0.215601921, 0.158007219, -0.000045798),
            (-0.073479183, -0.023025773, -0.000221944), (0.219749227, 0.007535274, -0.000183419),
        )
        edges = ((4, 8), (34, 9), (32, 10), (10, 0), (3, 11), (11, 12), (12, 13), (13, 1), (42, 14), (14, 15), (44, 16), (16, 3), (0, 17), (17, 18), (18, 19), (19, 2), (7, 20), (40, 21), (38, 22), (36, 4), (2, 23), (33, 24), (35, 25), (25, 5), (37, 26), (39, 27), (41, 28), (28, 6), (1, 29), (45, 30), (30, 31), (43, 7), (9, 32), (23, 33), (8, 34), (24, 35), (22, 36), (5, 37), (21, 38), (26, 39), (20, 40), (27, 41), (6, 42), (31, 43), (15, 44), (29, 45))
    elif kind == "HEEL":
        vertices = (
            (0, 0.499999821, -0.000000231), (0, 0.490392715, -0.097545438), (0, 0.461939812, -0.191342011),
            (0, 0.415734619, -0.277785212), (0, 0.353553355, -0.353553444), (0, 0.277785122, -0.415735066),
            (0, 0.191341475, -0.461939901), (0, 0.097545043, -0.490392804), (0, -0.000000121, -0.500000536),
            (0, -0.097545303, -0.490392804), (0, -0.191341788, -0.461939901), (0, -0.277785420, -0.415735066),
            (0, -0.353553712, -0.353553444), (0, -0.415734977, -0.277785212), (0, -0.461940259, -0.191341788),
            (0, -0.490393072, -0.097545020), (0, -0.500000179, 0.000000198), (0, -0.490393072, 0.097545415),
            (0, -0.461940259, 0.191341937), (0, -0.415734977, 0.277785450), (0, -0.353553712, 0.353553355),
            (0, -0.277785122, 0.415735513), (0, -0.191341430, 0.461940169), (0, 0.391893446, 0.391893089),
            (0, 0.353553981, 0.353552997), (0, 0.415735245, 0.277784556), (0, 0.461940438, 0.191340879),
            (0, 0.490392715, 0.097544335), (0, 0.389615744, -0.000000231), (0, 0.382129461, -0.076010473),
            (0, 0.359958023, -0.149099678), (0, 0.323953897, -0.216459095), (0, 0.275500059, -0.275500298),
            (0, 0.216458902, -0.323954105), (0, 0.149099439, -0.359958321), (0, 0.076010227, -0.382129550),
            (0, -0.000000095, -0.389616102), (0, -0.076010391, -0.382129550), (0, -0.149099603, -0.359958321),
            (0, -0.216459259, -0.323954105), (0, -0.275500417, -0.275500298), (0, -0.323953897, -0.216459095),
            (0, -0.359958380, -0.149099454), (0, -0.382129818, -0.076010257), (0, -0.389616102, 0.000000198),
            (0, -0.382129818, 0.076010436), (0, -0.359958380, 0.149099901), (0, -0.323953897, 0.216459066),
            (0, -0.275500000, 0.275500238), (0, -0.216458946, 0.323954046), (0, -0.149099261, 0.359958261),
            (0, 0.237160593, 0.237160176), (0, 0.275500357, 0.275499642), (0, 0.323953897, 0.216458440),
            (0, 0.359958380, 0.149098799), (0, 0.382129461, 0.076009601), (0, -0.170220375, 0.410949498),
            (0, 0.247122675, 0.369844228),
        )
        edges = ((1, 0), (2, 1), (3, 2), (4, 3), (5, 4), (6, 5), (7, 6), (8, 7), (9, 8), (10, 9), (11, 10), (12, 11), (13, 12), (14, 13), (15, 14), (16, 15), (17, 16), (18, 17), (19, 18), (20, 19), (21, 20), (22, 21), (24, 23), (25, 24), (26, 25), (27, 26), (0, 27), (29, 28), (30, 29), (31, 30), (32, 31), (33, 32), (34, 33), (35, 34), (36, 35), (37, 36), (38, 37), (39, 38), (40, 39), (41, 40), (42, 41), (43, 42), (44, 43), (45, 44), (46, 45), (47, 46), (48, 47), (49, 48), (50, 49), (52, 51), (53, 52), (54, 53), (55, 54), (28, 55), (56, 22), (57, 23), (51, 57), (50, 56))
    elif kind == "CONTROL_ARROW":
        # A complete, centered arrow for arbitrary animator controls.  The
        # dedicated Pole Arrow remains tip-anchored because its live GPU shaft
        # joins the bend joint to that control separately.
        vertices = (
            (0.0, -0.5, 0.0),
            (0.0, 0.14, 0.0),
            (-0.18, 0.14, -0.18),
            (-0.18, 0.14, 0.18),
            (0.18, 0.14, 0.18),
            (0.18, 0.14, -0.18),
            (0.0, 0.5, 0.0),
        )
        edges = (
            (0, 1),
            (2, 3), (3, 4), (4, 5), (5, 2),
            (2, 6), (3, 6), (4, 6), (5, 6),
        )
    elif kind == "CONTROL_SPHERE":
        points_per_ring = 24
        vertices = []
        edges = []
        for ring in range(3):
            start = len(vertices)
            for index in range(points_per_ring):
                angle = index * math.tau / points_per_ring
                cosine = 0.5 * math.cos(angle)
                sine = 0.5 * math.sin(angle)
                if ring == 0:
                    vertices.append((cosine, sine, 0.0))
                elif ring == 1:
                    vertices.append((cosine, 0.0, sine))
                else:
                    vertices.append((0.0, cosine, sine))
                edges.append(
                    (start + index, start + ((index + 1) % points_per_ring))
                )
        vertices = tuple(vertices)
        edges = tuple(edges)
    elif kind == "FINGER":
        points = []
        for index in range(32):
            angle = index * math.tau / 32
            x = -0.409725934 * math.sin(angle)
            z = 0.409725934 * math.cos(angle)
            if index == 8:
                x = -0.5
            elif index == 24:
                x = 0.5
            points.append((x, 0, z))
        vertices = tuple(points)
        edges = tuple((index, (index + 1) % len(vertices)) for index in range(len(vertices)))
    elif kind == "SHOULDER":
        lower = (
            (-.500000119, .233489260, .303807884), (-.452380955, .255385429, .379028291),
            (-.404761970, .272454798, .437666982), (-.357142985, .286127359, .484636575),
            (-.309523821, .297159553, .522535563), (-.261904776, .306003600, .552917778),
            (-.214285746, .312949568, .576779366), (-.166666701, .318189800, .594781280),
            (-.119047694, .321852505, .607363701), (-.071428604, .324019670, .614808619),
            (-.023809563, .324737132, .617273390), (.023809491, .324737132, .617273390),
            (.071428537, .324019670, .614808619), (.119047567, .321852505, .607363701),
            (.166666672, .318189800, .594781280), (.214285657, .312949568, .576779366),
            (.261904687, .306003600, .552917778), (.309523731, .297159553, .522535563),
            (.357142866, .286127388, .484636694), (.404761791, .272454798, .437666982),
            (.452380896, .255385429, .379028291), (.499999881, .233489260, .303807884),
        )
        upper = tuple((x, y + .35577549, z - .103564068) for x, y, z in lower)
        vertices = lower + upper
        edges = (
            tuple((index, index + 1) for index in range(21))
            + tuple((22 + index, 23 + index) for index in range(21))
            + ((0, 22), (21, 43))
        )
    else:
        raise LimbIKError(f"Unknown Limb IK widget kind '{kind}'.")
    return vertices, edges


def _collection_scene_memberships(collection):
    return tuple(scene for scene in bpy.data.scenes if collection is scene.collection or collection in scene.collection.children_recursive)


def _ensure_widget_collection(context, armature_id, transaction):
    matches = [collection for collection in bpy.data.collections if _owned(collection, armature_id, role="WIDGET_COLLECTION")]
    if len(matches) > 1:
        raise LimbIKError("Multiple owned Randy Rig widget collections were found; repair them manually.")
    if matches:
        collection = matches[0]
        if _collection_scene_memberships(collection) != (context.scene,):
            raise LimbIKError("The owned widget collection is linked across Scenes.")
        return collection
    foreign = bpy.data.collections.get(WIDGET_COLLECTION_NAME)
    if foreign is not None:
        raise LimbIKError(f"Collection '{WIDGET_COLLECTION_NAME}' already exists but is not owned by Character Designer.")
    collection = bpy.data.collections.new(WIDGET_COLLECTION_NAME)
    context.scene.collection.children.link(collection)
    _tag(collection, armature_id, role="WIDGET_COLLECTION")
    collection.hide_render = True
    collection.hide_viewport = True
    transaction["collections"].append(collection)
    return collection


def _ensure_widget(context, armature_id, collection, kind, transaction, *, schema=CURRENT_SCHEMA):
    matches = [obj for obj in bpy.data.objects if _owned(obj, armature_id, role="WIDGET") and obj.get(KIND_KEY) == kind]
    if len(matches) > 1:
        raise LimbIKError(f"Multiple owned {kind.title()} widgets were found.")
    if matches:
        obj = matches[0]
        if obj.type != "MESH" or not _owned(obj.data, armature_id, role="WIDGET_DATA") or obj.data.get(KIND_KEY) != kind:
            raise LimbIKError(f"Owned {kind.title()} widget data is invalid.")
        if tuple(obj.users_collection) != (collection,):
            raise LimbIKError(f"Owned {kind.title()} widget is linked outside its exact collection.")
        return obj
    name = WIDGET_NAMES[kind]
    if bpy.data.objects.get(name) is not None or bpy.data.meshes.get(name) is not None:
        raise LimbIKError(f"Widget name '{name}' is already used by non-owned data.")
    mesh = bpy.data.meshes.new(name)
    vertices, edges = _widget_geometry(kind, schema=schema)
    mesh.from_pydata(vertices, edges, ())
    mesh.update()
    _tag(mesh, armature_id, role="WIDGET_DATA", kind=kind)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    _tag(obj, armature_id, role="WIDGET", kind=kind)
    obj.hide_render = True
    obj.display_type = "WIRE"
    transaction["meshes"].append(mesh)
    transaction["objects"].append(obj)
    return obj


def _ensure_control_collection(armature, armature_id, transaction):
    matches = [collection for collection in armature.data.collections if _owned(collection, armature_id, role="CONTROL_COLLECTION")]
    if len(matches) > 1:
        raise LimbIKError("Multiple owned Randy Controls bone collections were found.")
    if matches:
        return matches[0]
    foreign = armature.data.collections.get(CONTROL_COLLECTION_NAME)
    if foreign is not None:
        raise LimbIKError(f"Bone Collection '{CONTROL_COLLECTION_NAME}' already exists but is not owned by Character Designer.")
    collection = armature.data.collections.new(CONTROL_COLLECTION_NAME)
    _tag(collection, armature_id, role="CONTROL_COLLECTION")
    transaction["bone_collections"].append(collection)
    return collection


def _owned_constraint_records(armature, *, strict=True):
    records = []
    pose = armature.pose
    if pose is None:
        return records
    for pose_bone in pose.bones:
        registry = _constraint_registry(pose_bone, strict=strict)
        for constraint_name, record in registry.items():
            if not isinstance(constraint_name, str) or not isinstance(record, dict):
                raise LimbIKError(f"Limb IK registry on bone '{pose_bone.name}' contains an invalid record.")
            rig_id = record.get("rig_id")
            role = record.get("role")
            role_types = {
                "IK": "IK",
                "END_ROTATION": "COPY_ROTATION",
                "AUTO_OFFSET_ROTATION": "COPY_ROTATION",
                "POLE_LINE_STRETCH": "STRETCH_TO",
                "POLE_DISPLAY_TRACK": "DAMPED_TRACK",
                "HEEL_LIMIT": "LIMIT_ROTATION",
                "MASTER_FOLLOW": "COPY_TRANSFORMS",
                "ORI_UPPER_LOCATION": "COPY_LOCATION",
                "ORI_UPPER_TRACK": "DAMPED_TRACK",
                "ORI_LOWER_LOCATION": "COPY_LOCATION",
                "ORI_LOWER_TRACK": "DAMPED_TRACK",
                "SOURCE_UPPER_ROTATION": "COPY_ROTATION",
                "SOURCE_LOWER_ROTATION": "COPY_ROTATION",
            }
            if (
                record.get("owner") != OWNER_VALUE
                or record.get("version") != RIG_VERSION
                or not isinstance(rig_id, str)
                or not rig_id
                or role not in role_types
                or constraint_name != _constraint_name(rig_id, role)
            ):
                raise LimbIKError(f"Limb IK registry on bone '{pose_bone.name}' has an unknown ownership fingerprint.")
            matches = [constraint for constraint in pose_bone.constraints if constraint.name == constraint_name]
            if len(matches) != 1:
                raise LimbIKError(f"Owned constraint '{constraint_name}' is missing or duplicated.")
            constraint = matches[0]
            expected_type = role_types[role]
            if constraint.type != expected_type:
                raise LimbIKError(f"Owned constraint '{constraint_name}' changed type.")
            records.append((pose_bone, constraint, record))
    return records


def _record_constraint(pose_bone, constraint, plan, armature_id, role, *, schema=CURRENT_SCHEMA):
    registry = _constraint_registry(pose_bone, strict=True)
    constraint.name = _constraint_name(plan.rig_id, role)
    if constraint.name in registry:
        raise LimbIKError(f"Constraint registry collision on '{pose_bone.name}'.")
    record = {
        "owner": OWNER_VALUE,
        "version": RIG_VERSION,
        "armature_id": armature_id,
        "rig_id": plan.rig_id,
        "role": role,
        "kind": plan.chain.kind,
        "side": plan.chain.side,
        "chain": list(plan.chain.names),
        "target": plan.target_name,
        "pole": plan.pole_name,
    }
    if _is_enhanced_schema(schema):
        record.update(
            {
                "line": plan.line_name,
                "display": plan.display_name,
                "heel": plan.heel_name,
                "solver_target": plan.solver_target_name,
                "master": MASTER_NAME,
            }
        )
    elif _uses_dynamic_pole_display(schema):
        record["display"] = plan.display_name
    if _is_roll_decoupled_schema(schema):
        record.update(
            {
                "mch_upper": plan.mch_upper_name,
                "mch_lower": plan.mch_lower_name,
                "ori_upper": plan.ori_upper_name,
                "ori_lower": plan.ori_lower_name,
            }
        )
    registry[constraint.name] = record
    _write_constraint_registry(pose_bone, registry)


def _record_master_constraint(pose_bone, constraint, armature_id):
    registry = _constraint_registry(pose_bone, strict=True)
    constraint.name = _constraint_name(armature_id, "MASTER_FOLLOW")
    if constraint.name in registry:
        raise LimbIKError(f"Constraint registry collision on '{pose_bone.name}'.")
    registry[constraint.name] = {
        "owner": OWNER_VALUE,
        "version": RIG_VERSION,
        "armature_id": armature_id,
        "rig_id": armature_id,
        "role": "MASTER_FOLLOW",
        "root": pose_bone.name,
        "target": MASTER_NAME,
    }
    _write_constraint_registry(pose_bone, registry)


def _validate_inventory(armature):
    armature_id = armature.data.get(ARMATURE_ID_KEY, "")
    generated = [bone for bone in armature.data.bones if bone.get(OWNER_KEY) == OWNER_VALUE]
    records = _owned_constraint_records(armature, strict=True)
    if not armature_id:
        if (
            generated
            or records
            or SCHEMA_KEY in armature.data
            or DIRECT_REST_KEY in armature.data
            or SOURCE_WIDGETS_KEY in armature.data
            or TARGET_ROTATION_VERSION_KEY in armature.data
        ):
            raise LimbIKError("Orphaned Limb IK ownership data was found on this Armature.")
        return {
            "armature_id": "",
            "schema": 0,
            "target_rotation_version": 0,
            "rigs": {},
            "records": [],
            "bones": [],
            "master": None,
            "master_records": [],
            "source_widgets": None,
        }
    if not isinstance(armature_id, str):
        raise LimbIKError("The Limb IK Armature ID is invalid.")
    schema = armature.data.get(SCHEMA_KEY, LEGACY_SCHEMA)
    if schema not in {
        LEGACY_SCHEMA,
        ENHANCED_SCHEMA,
        ROLL_DECOUPLED_SCHEMA,
        LEGACY_DIRECT_PREROLL_SCHEMA,
        DIRECT_PREROLL_SCHEMA,
    }:
        raise LimbIKError("The Limb IK schema version is unsupported.")
    raw_target_rotation_version = armature.data.get(TARGET_ROTATION_VERSION_KEY, None)
    if raw_target_rotation_version is None:
        target_rotation_version = 0
    elif type(raw_target_rotation_version) is not int or raw_target_rotation_version != TARGET_ROTATION_VERSION:
        raise LimbIKError("The Limb IK Target Rotation feature version is unsupported.")
    else:
        target_rotation_version = raw_target_rotation_version
    if not generated and not records:
        if (
            DIRECT_REST_KEY in armature.data
            or SOURCE_WIDGETS_KEY in armature.data
            or TARGET_ROTATION_VERSION_KEY in armature.data
        ):
            raise LimbIKError("Orphaned Limb IK Rest or source-control display data was found on this Armature.")
        return {
            "armature_id": armature_id,
            "schema": schema,
            "target_rotation_version": 0,
            "rigs": {},
            "records": [],
            "bones": [],
            "master": None,
            "master_records": [],
            "source_widgets": None,
        }
    if _is_direct_preroll_schema(schema):
        direct_registry = _load_direct_rest_registry(armature, strict=True)
    else:
        if DIRECT_REST_KEY in armature.data:
            raise LimbIKError("A non-Direct Limb IK rig contains unexpected Direct Pre-Roll Rest data.")
        direct_registry = {"version": DIRECT_PREROLL_RESULT_VERSION, "limbs": {}}
    for pose_bone, constraint, record in records:
        influence = float(constraint.influence)
        if not math.isfinite(influence) or abs(influence - 1.0) > 1.0e-6:
            raise LimbIKError(f"Owned constraint '{constraint.name}' on '{pose_bone.name}' was disabled or had its influence edited.")
        if record["role"] in {"END_ROTATION", "AUTO_OFFSET_ROTATION"}:
            target_bone = armature.data.bones.get(record.get("target", ""))
            auto_value = target_bone.get(AUTO_ALIGN_KEY, False) if target_bone is not None else False
            if type(auto_value) is not bool:
                raise LimbIKError(
                    f"Owned Target for '{constraint.name}' has invalid Auto Align state."
                )
            expected_mute = (
                auto_value
                if record["role"] == "END_ROTATION"
                else not auto_value
            )
            if bool(constraint.mute) != expected_mute:
                raise LimbIKError(
                    f"Owned Target rotation '{constraint.name}' does not match its Auto Align state."
                )
        elif constraint.mute:
            raise LimbIKError(f"Owned constraint '{constraint.name}' on '{pose_bone.name}' was disabled or had its influence edited.")
    rigs = {}
    by_rig = {}
    master_records = []
    for pose_bone, constraint, record in records:
        if record.get("armature_id") != armature_id:
            raise LimbIKError("A Limb IK constraint belongs to a different Armature ID.")
        if record["role"] == "MASTER_FOLLOW":
            master_records.append((pose_bone, constraint, record))
        else:
            rig_id = record["rig_id"]
            by_rig.setdefault(rig_id, []).append((pose_bone, constraint, record))
    expected_bones = set()
    for rig_id, entries in by_rig.items():
        sample = entries[0][2]
        key = (sample.get("kind"), sample.get("side"))
        if key[0] not in KINDS or key[1] not in SIDES or key in rigs:
            raise LimbIKError("Duplicate or invalid Limb IK side ownership was found.")
        limb_roles = {"IK", "END_ROTATION"}
        if target_rotation_version == TARGET_ROTATION_VERSION:
            limb_roles.add("AUTO_OFFSET_ROTATION")
        if _is_enhanced_schema(schema):
            limb_roles.update(("POLE_LINE_STRETCH", "POLE_DISPLAY_TRACK"))
            if key[0] == "LEG":
                limb_roles.add("HEEL_LIMIT")
        elif _uses_dynamic_pole_display(schema):
            limb_roles.add("POLE_DISPLAY_TRACK")
        if _is_roll_decoupled_schema(schema):
            limb_roles.update(
                (
                    "ORI_UPPER_LOCATION",
                    "ORI_UPPER_TRACK",
                    "ORI_LOWER_LOCATION",
                    "ORI_LOWER_TRACK",
                    "SOURCE_UPPER_ROTATION",
                    "SOURCE_LOWER_ROTATION",
                )
            )
        if len(entries) != len(limb_roles) or {record["role"] for _pb, _con, record in entries} != limb_roles:
            raise LimbIKError(f"Limb IK rig '{rig_id}' is incomplete.")
        chain_value = sample.get("chain")
        target_name = sample.get("target")
        pole_name = sample.get("pole")
        if (
            not isinstance(chain_value, (list, tuple))
            or len(chain_value) != 3
            or any(not isinstance(name, str) or not name for name in chain_value)
            or len(set(chain_value)) != 3
            or any(armature.data.bones.get(name) is None for name in chain_value)
            or not isinstance(target_name, str)
            or not target_name
            or not isinstance(pole_name, str)
            or not pole_name
            or target_name == pole_name
        ):
            raise LimbIKError(f"Limb IK rig '{rig_id}' registry has an invalid source/control bone schema.")
        chain = tuple(chain_value)
        enhanced_names = {}
        if _is_enhanced_schema(schema):
            enhanced_names = {
                key_name: sample.get(key_name, "")
                for key_name in ("line", "display", "heel", "solver_target", "master")
            }
            required = ("line", "display", "solver_target", "master")
            if any(not isinstance(enhanced_names[name], str) or not enhanced_names[name] for name in required):
                raise LimbIKError(f"Limb IK rig '{rig_id}' has incomplete enhanced controls.")
            if enhanced_names["master"] != MASTER_NAME:
                raise LimbIKError(f"Limb IK rig '{rig_id}' points at an unknown Master control.")
            if key[0] == "LEG" and (not isinstance(enhanced_names["heel"], str) or not enhanced_names["heel"]):
                raise LimbIKError(f"Limb IK rig '{rig_id}' has no Heel Roll control.")
            if key[0] == "ARM" and enhanced_names["heel"]:
                raise LimbIKError(f"Limb IK rig '{rig_id}' has an unexpected Heel Roll control.")
        display_name = (
            enhanced_names.get("display", "")
            if _is_enhanced_schema(schema)
            else sample.get("display", "")
            if _uses_dynamic_pole_display(schema)
            else ""
        )
        if _uses_dynamic_pole_display(schema) and (
            not isinstance(display_name, str) or not display_name
        ):
            raise LimbIKError(f"Limb IK rig '{rig_id}' has no Pole display helper.")
        roll_names = {}
        if _is_roll_decoupled_schema(schema):
            roll_names = {
                key_name: sample.get(key_name, "")
                for key_name in ("mch_upper", "mch_lower", "ori_upper", "ori_lower")
            }
            if any(not isinstance(name, str) or not name for name in roll_names.values()):
                raise LimbIKError(f"Limb IK rig '{rig_id}' has incomplete roll-decoupled mechanisms.")
            if len(set(roll_names.values())) != len(roll_names):
                raise LimbIKError(f"Limb IK rig '{rig_id}' reuses a roll-decoupled mechanism bone name.")
        if any(
            record.get("chain") != list(chain)
            or record.get("target") != target_name
            or record.get("pole") != pole_name
            or record.get("kind") != sample.get("kind")
            or record.get("side") != sample.get("side")
            or record.get("armature_id") != armature_id
            or (_is_enhanced_schema(schema) and any(record.get(name, "") != value for name, value in enhanced_names.items()))
            or (
                _uses_dynamic_pole_display(schema)
                and record.get("display", "") != display_name
            )
            or (_is_roll_decoupled_schema(schema) and any(record.get(name, "") != value for name, value in roll_names.items()))
            for _pb, _con, record in entries
        ):
            raise LimbIKError(f"Limb IK rig '{rig_id}' has inconsistent registry records.")
        target = armature.data.bones.get(target_name)
        pole = armature.data.bones.get(pole_name)
        if not _owned(target, armature_id, role=LIMB_SPEC[key[0]]["target_role"], rig_id=rig_id) or not _owned(pole, armature_id, role="POLE", rig_id=rig_id):
            raise LimbIKError(f"Limb IK rig '{rig_id}' control bone ownership is invalid.")
        auto_align = bool(target.get(AUTO_ALIGN_KEY, False))
        target_pose = armature.pose.bones.get(target_name)
        if target_pose is None:
            raise LimbIKError(f"Limb IK rig '{rig_id}' Target pose bone is missing.")
        target_display = target_pose.custom_shape_transform
        expected_end = armature.pose.bones.get(chain[2])
        if auto_align:
            # Schema 5 always authors the dynamic display pointer.  Older
            # schema-3/4 Auto rigs predate it, so tolerate None until their
            # next Rebuild or explicit global Auto operation upgrades it.
            if (
                target_display is None
                or expected_end is None
                or target_display.name != expected_end.name
            ) and not (
                target_display is None and schema != DIRECT_PREROLL_SCHEMA
            ):
                raise LimbIKError(
                    f"Limb IK rig '{rig_id}' Auto Align display was edited."
                )
        elif target_display is not None:
            raise LimbIKError(
                f"Limb IK rig '{rig_id}' manual Target has an unexpected display transform."
            )
        saved_pole_direction = None
        if POLE_DIRECTION_KEY in pole:
            saved_pole_direction = _finite_direction(
                pole[POLE_DIRECTION_KEY],
                f"Saved {key[1]} {key[0].title()} Pole Direction",
            )
            if abs(saved_pole_direction.length - 1.0) > 1.0e-5:
                raise LimbIKError(f"Limb IK rig '{rig_id}' has a non-normalized Pole Direction tag.")
            saved_pole_direction.normalize()
        configured_pole_direction = None
        if _is_roll_decoupled_schema(schema) or _is_direct_preroll_schema(schema):
            if POLE_CONFIGURED_DIRECTION_KEY not in pole:
                raise LimbIKError(f"Limb IK rig '{rig_id}' has no configured Pole Direction tag.")
            configured_pole_direction = _finite_direction(
                pole[POLE_CONFIGURED_DIRECTION_KEY],
                f"Configured {key[1]} {key[0].title()} Pole Direction",
            )
            if abs(configured_pole_direction.length - 1.0) > 1.0e-5:
                raise LimbIKError(f"Limb IK rig '{rig_id}' has a non-normalized configured Pole Direction tag.")
            configured_pole_direction.normalize()
        expected_bones.update((target.name, pole.name))
        ik_entry = next(entry for entry in entries if entry[2]["role"] == "IK")
        end_entry = next(entry for entry in entries if entry[2]["role"] == "END_ROTATION")
        auto_offset_entry = (
            next(
                entry
                for entry in entries
                if entry[2]["role"] == "AUTO_OFFSET_ROTATION"
            )
            if target_rotation_version == TARGET_ROTATION_VERSION
            else None
        )
        lower_pb, ik, _record = ik_entry
        end_pb, copy_rotation, _record = end_entry
        expected_ik_owner = roll_names["mch_lower"] if _is_roll_decoupled_schema(schema) else chain[1]
        if lower_pb.name != expected_ik_owner or end_pb.name != chain[2]:
            raise LimbIKError(f"Limb IK rig '{rig_id}' constraints moved to other bones.")
        solver_target = target
        line = display = heel = None
        mch_upper = mch_lower = ori_upper = ori_lower = None
        if _is_enhanced_schema(schema):
            line = armature.data.bones.get(enhanced_names["line"])
            display = armature.data.bones.get(enhanced_names["display"])
            solver_target = armature.data.bones.get(enhanced_names["solver_target"])
            heel = armature.data.bones.get(enhanced_names["heel"]) if enhanced_names["heel"] else None
            if not _owned(line, armature_id, role="POLE_LINE", rig_id=rig_id) or not _owned(display, armature_id, role="POLE_DISPLAY", rig_id=rig_id) or not _owned(solver_target, armature_id, role="FOOT_TARGET" if key[0] == "LEG" else "HAND_IK", rig_id=rig_id):
                raise LimbIKError(f"Limb IK rig '{rig_id}' enhanced bone ownership is invalid.")
            if key[0] == "ARM" and solver_target.name != target.name:
                raise LimbIKError(f"Limb IK rig '{rig_id}' has a separate Arm solver target.")
            if key[0] == "LEG" and not _owned(heel, armature_id, role="HEEL_ROLL", rig_id=rig_id):
                raise LimbIKError(f"Limb IK rig '{rig_id}' Heel Roll ownership is invalid.")
            expected_bones.update((line.name, display.name, solver_target.name))
            if heel is not None:
                expected_bones.add(heel.name)
            master = armature.data.bones.get(MASTER_NAME)
            if (
                master is None
                or target.parent is None or target.parent.name != master.name
                or pole.parent is None or pole.parent.name != master.name
                or line.parent is None or line.parent.name != chain[0]
                or display.parent is None or display.parent.name != pole.name
            ):
                raise LimbIKError(f"Limb IK rig '{rig_id}' generated hierarchy was edited.")
            if key[0] == "LEG" and (
                heel.parent is None or heel.parent.name != target.name
                or solver_target.parent is None or solver_target.parent.name != heel.name
            ):
                raise LimbIKError(f"Limb IK rig '{rig_id}' Foot/Heel hierarchy was edited.")
            line_entry = next(entry for entry in entries if entry[2]["role"] == "POLE_LINE_STRETCH")
            display_entry = next(entry for entry in entries if entry[2]["role"] == "POLE_DISPLAY_TRACK")
            line_pb, stretch, _record = line_entry
            display_pb, track, _record = display_entry
            if line_pb.name != line.name or stretch.target is not armature or stretch.subtarget != pole.name or stretch.target_space != "WORLD" or stretch.owner_space != "WORLD":
                raise LimbIKError(f"Limb IK rig '{rig_id}' Pole line mechanism was edited.")
            if display_pb.name != display.name or track.target is not armature or track.subtarget != chain[1] or abs(float(track.head_tail)) > 1.0e-6 or track.track_axis != "TRACK_NEGATIVE_Y" or track.target_space != "WORLD" or track.owner_space != "WORLD":
                raise LimbIKError(f"Limb IK rig '{rig_id}' Pole arrow mechanism was edited.")
            if key[0] == "LEG":
                heel_pb, heel_limit, _record = next(entry for entry in entries if entry[2]["role"] == "HEEL_LIMIT")
                if heel_pb.name != heel.name or heel_limit.owner_space != "LOCAL" or not heel_limit.use_limit_x or abs(heel_limit.min_x + math.pi * 0.5) > 1.0e-6 or abs(heel_limit.max_x - math.radians(130.0)) > 1.0e-6:
                    raise LimbIKError(f"Limb IK rig '{rig_id}' Heel Roll limits were edited.")
        elif _is_direct_preroll_schema(schema):
            if (
                target.parent is not None
                or pole.parent is not None
                or target.use_connect
                or pole.use_connect
            ):
                raise LimbIKError(f"Limb IK rig '{rig_id}' Direct control hierarchy was edited.")
            if _uses_dynamic_pole_display(schema):
                display = armature.data.bones.get(display_name)
                if not _owned(
                    display,
                    armature_id,
                    role="POLE_DISPLAY",
                    rig_id=rig_id,
                ):
                    raise LimbIKError(
                        f"Limb IK rig '{rig_id}' Pole display ownership is invalid."
                    )
                if (
                    display.parent is None
                    or display.parent.name != pole.name
                    or display.use_connect
                    or display.use_deform
                    or not display.hide
                    or not display.hide_select
                ):
                    raise LimbIKError(
                        f"Limb IK rig '{rig_id}' Pole display hierarchy was edited."
                    )
                expected_bones.add(display.name)
                display_pb, track, _record = next(
                    entry
                    for entry in entries
                    if entry[2]["role"] == "POLE_DISPLAY_TRACK"
                )
                if (
                    display_pb.name != display.name
                    or track.target is not armature
                    or track.subtarget != chain[1]
                    or abs(float(track.head_tail)) > 1.0e-6
                    or track.track_axis != "TRACK_NEGATIVE_Y"
                    or track.target_space != "WORLD"
                    or track.owner_space != "WORLD"
                ):
                    raise LimbIKError(
                        f"Limb IK rig '{rig_id}' Pole arrow mechanism was edited."
                    )
        if _is_roll_decoupled_schema(schema):
            mch_upper = armature.data.bones.get(roll_names["mch_upper"])
            mch_lower = armature.data.bones.get(roll_names["mch_lower"])
            ori_upper = armature.data.bones.get(roll_names["ori_upper"])
            ori_lower = armature.data.bones.get(roll_names["ori_lower"])
            mechanisms = (
                (mch_upper, "MCH_UPPER"),
                (mch_lower, "MCH_LOWER"),
                (ori_upper, "ORI_UPPER"),
                (ori_lower, "ORI_LOWER"),
            )
            if any(not _owned(bone, armature_id, role=role, rig_id=rig_id) for bone, role in mechanisms):
                raise LimbIKError(f"Limb IK rig '{rig_id}' roll-decoupled bone ownership is invalid.")
            expected_bones.update(bone.name for bone, _role in mechanisms)
            source_upper = armature.data.bones[chain[0]]
            source_lower = armature.data.bones[chain[1]]
            anchor = source_upper.parent if source_upper.parent is not None else armature.data.bones.get(MASTER_NAME)
            anchor_name = anchor.name if anchor is not None else ""
            if (
                (mch_upper.parent.name if mch_upper.parent is not None else "") != anchor_name
                or (mch_lower.parent.name if mch_lower.parent is not None else "") != mch_upper.name
                or not mch_lower.use_connect
                or (ori_upper.parent.name if ori_upper.parent is not None else "") != anchor_name
                or (ori_lower.parent.name if ori_lower.parent is not None else "") != anchor_name
                or ori_upper.use_connect
                or ori_lower.use_connect
            ):
                raise LimbIKError(f"Limb IK rig '{rig_id}' roll-decoupled hierarchy was edited.")
            for generated_bone, source_bone in (
                (mch_upper, source_upper),
                (mch_lower, source_lower),
                (ori_upper, source_upper),
                (ori_lower, source_lower),
            ):
                if (
                    (Vector(generated_bone.head_local) - Vector(source_bone.head_local)).length > 1.0e-6
                    or (Vector(generated_bone.tail_local) - Vector(source_bone.tail_local)).length > 1.0e-6
                    or _rotation_error(generated_bone.matrix_local, source_bone.matrix_local) > 1.0e-6
                ):
                    raise LimbIKError(f"Limb IK rig '{rig_id}' roll-decoupled rest frames were edited.")

            for segment, source_name, mch_bone, ori_bone in (
                ("UPPER", chain[0], mch_upper, ori_upper),
                ("LOWER", chain[1], mch_lower, ori_lower),
            ):
                location_pb, location, _record = next(
                    entry for entry in entries if entry[2]["role"] == f"ORI_{segment}_LOCATION"
                )
                track_pb, orientation_track, _record = next(
                    entry for entry in entries if entry[2]["role"] == f"ORI_{segment}_TRACK"
                )
                source_pb, source_rotation, _record = next(
                    entry for entry in entries if entry[2]["role"] == f"SOURCE_{segment}_ROTATION"
                )
                if (
                    location_pb.name != ori_bone.name
                    or location.target is not armature
                    or location.subtarget != mch_bone.name
                    or abs(float(location.head_tail)) > 1.0e-6
                    or location.target_space != "WORLD"
                    or location.owner_space != "WORLD"
                ):
                    raise LimbIKError(f"Limb IK rig '{rig_id}' {segment.title()} orientation location was edited.")
                if (
                    track_pb.name != ori_bone.name
                    or orientation_track.target is not armature
                    or orientation_track.subtarget != mch_bone.name
                    or abs(float(orientation_track.head_tail) - 1.0) > 1.0e-6
                    or orientation_track.track_axis != "TRACK_Y"
                    or orientation_track.target_space != "WORLD"
                    or orientation_track.owner_space != "WORLD"
                ):
                    raise LimbIKError(f"Limb IK rig '{rig_id}' {segment.title()} orientation tracking was edited.")
                if (
                    source_pb.name != source_name
                    or source_rotation.target is not armature
                    or source_rotation.subtarget != ori_bone.name
                    or source_rotation.target_space != "WORLD"
                    or source_rotation.owner_space != "WORLD"
                    or getattr(source_rotation, "mix_mode", "") != "REPLACE"
                ):
                    raise LimbIKError(f"Limb IK rig '{rig_id}' {segment.title()} source rotation was edited.")
        if (
            ik.target is not armature
            or ik.subtarget != solver_target.name
            or ik.pole_target is not armature
            or ik.pole_subtarget != pole.name
            or ik.chain_count != 2
            or ik.target_space != "WORLD"
            or ik.owner_space != "WORLD"
            or (hasattr(ik, "use_tail") and not ik.use_tail)
            or (hasattr(ik, "use_stretch") and ik.use_stretch)
            or (hasattr(ik, "use_rotation") and ik.use_rotation)
        ):
            raise LimbIKError(f"Limb IK rig '{rig_id}' IK settings were edited.")
        world_end_rotation = (
            copy_rotation.target_space == "WORLD"
            and copy_rotation.owner_space == "WORLD"
        )
        direct_arm_end_rotation = (
            copy_rotation.target_space == "LOCAL_OWNER_ORIENT"
            and copy_rotation.owner_space == "LOCAL_WITH_PARENT"
        )
        valid_end_rotation_space = world_end_rotation
        if _is_direct_preroll_schema(schema) and key[0] == "ARM":
            # Accept schema-4 rigs authored before 0.29.7 so Remove and an
            # untouched Rebuild can migrate them safely to the Forearm frame.
            valid_end_rotation_space = world_end_rotation or direct_arm_end_rotation
        if (
            copy_rotation.target is not armature
            or copy_rotation.subtarget != solver_target.name
            or not valid_end_rotation_space
            or getattr(copy_rotation, "mix_mode", "") != "REPLACE"
            or not all(getattr(copy_rotation, name, False) for name in ("use_x", "use_y", "use_z"))
            or any(getattr(copy_rotation, name, False) for name in ("invert_x", "invert_y", "invert_z"))
        ):
            raise LimbIKError(f"Limb IK rig '{rig_id}' end rotation settings were edited.")
        auto_offset_rotation = None
        if auto_offset_entry is not None:
            offset_pb, auto_offset_rotation, _record = auto_offset_entry
            if (
                offset_pb.name != chain[2]
                or auto_offset_rotation.target is not armature
                or auto_offset_rotation.subtarget != target.name
                or auto_offset_rotation.target_space != "LOCAL"
                or auto_offset_rotation.owner_space != "LOCAL"
                or getattr(auto_offset_rotation, "mix_mode", "") != "AFTER"
                or not all(
                    getattr(auto_offset_rotation, name, False)
                    for name in ("use_x", "use_y", "use_z")
                )
                or any(
                    getattr(auto_offset_rotation, name, False)
                    for name in ("invert_x", "invert_y", "invert_z")
                )
            ):
                raise LimbIKError(
                    f"Limb IK rig '{rig_id}' Auto Target rotation settings were edited."
                )
        direct_rest = None
        if _is_direct_preroll_schema(schema):
            direct_rest = direct_registry["limbs"].get(rig_id)
            if (
                direct_rest is None
                or direct_rest["kind"] != key[0]
                or direct_rest["side"] != key[1]
                or tuple(direct_rest["chain"]) != chain
            ):
                raise LimbIKError(f"Limb IK rig '{rig_id}' has missing or mismatched Direct Pre-Roll Rest data.")
            for name in chain[:2]:
                if not _rest_state_matches(armature.data.bones.get(name), direct_rest["applied"][name]):
                    raise LimbIKError(
                        f"Direct Pre-Roll source Rest bone '{name}' was edited after the rig was built "
                        f"({_rest_state_mismatch_details(armature.data.bones.get(name), direct_rest['applied'][name])})."
                    )
        rigs[key] = {
            "rig_id": rig_id,
            "entries": entries,
            "target": target,
            "pole": pole,
            "chain": chain,
            "line": line,
            "display": display,
            "heel": heel,
            "solver_target": solver_target,
            "pole_direction": saved_pole_direction,
            "configured_pole_direction": configured_pole_direction,
            "mch_upper": mch_upper,
            "mch_lower": mch_lower,
            "ori_upper": ori_upper,
            "ori_lower": ori_lower,
            "direct_rest": direct_rest,
            "auto_align": auto_align,
            "auto_offset_rotation": auto_offset_rotation,
        }
    if _is_direct_preroll_schema(schema) and set(direct_registry["limbs"]) != set(by_rig):
        raise LimbIKError("Direct Pre-Roll Rest registry contains missing or orphaned limb entries.")
    master = None
    if _is_enhanced_schema(schema):
        master = armature.data.bones.get(MASTER_NAME)
        if not _owned(master, armature_id, role="MASTER") or master.parent is not None:
            raise LimbIKError("The enhanced Limb IK Master control is missing or invalid.")
        expected_bones.add(master.name)
        source_roots = {bone.name for bone in armature.data.bones if bone.parent is None and bone.get(OWNER_KEY) != OWNER_VALUE}
        if {pose_bone.name for pose_bone, _constraint, _record in master_records} != source_roots or len(master_records) != len(source_roots):
            raise LimbIKError("Master does not own exactly one follow constraint for every source root.")
        for pose_bone, constraint, record in master_records:
            if record.get("root") != pose_bone.name or record.get("target") != MASTER_NAME:
                raise LimbIKError("A Master follow registry record is invalid.")
            if constraint.target is not armature or constraint.subtarget != MASTER_NAME or constraint.target_space != "POSE" or constraint.owner_space != "POSE" or getattr(constraint, "mix_mode", "") != "BEFORE":
                raise LimbIKError(f"Master follow on source root '{pose_bone.name}' was edited.")
    elif master_records:
        raise LimbIKError("A legacy Limb IK rig contains unexpected Master constraints.")
    if {bone.name for bone in generated} != expected_bones:
        raise LimbIKError("Orphaned or duplicate generated Limb IK control bones were found.")
    source_widgets = _load_source_widget_registry(armature, strict=True)
    _validate_source_widget_assignments(armature, source_widgets, rigs)
    return {
        "armature_id": armature_id,
        "schema": schema,
        "target_rotation_version": target_rotation_version,
        "rigs": rigs,
        "records": records,
        "bones": generated,
        "master": master,
        "master_records": master_records,
        "source_widgets": source_widgets,
    }


def _preflight_plans(context, armature, plans, inventory, *, schema=CURRENT_SCHEMA):
    if inventory["armature_id"] and inventory["schema"] != schema:
        raise LimbIKError(
            "This Armature already uses another Limb IK Build Method. "
            "Use Remove Generated Rig before switching between Stable and Direct Pre-Roll."
        )
    existing_keys = set(inventory["rigs"])
    for plan in plans:
        key = (plan.chain.kind, plan.chain.side)
        if key in existing_keys:
            if tuple(inventory["rigs"][key]["chain"]) != plan.chain.names:
                raise LimbIKError(f"Existing {plan.chain.side} {plan.chain.kind.title()} rig targets a different source chain; use Remove Generated Rig first.")
            continue
        names = {plan.target_name, plan.pole_name}
        if _is_enhanced_schema(schema):
            names.update((plan.line_name, plan.display_name, plan.solver_target_name))
            if plan.heel_name:
                names.add(plan.heel_name)
        elif _uses_dynamic_pole_display(schema):
            names.add(plan.display_name)
        if _is_roll_decoupled_schema(schema):
            names.update(
                (plan.mch_upper_name, plan.mch_lower_name, plan.ori_upper_name, plan.ori_lower_name)
            )
        for name in names:
            if armature.data.bones.get(name) is not None:
                raise LimbIKError(f"Bone '{name}' already exists and is not this exact generated control.")
        lower = armature.pose.bones[plan.chain.lower]
        upper = armature.pose.bones[plan.chain.upper]
        end = armature.pose.bones[plan.chain.end]
        if any(constraint.type == "IK" for constraint in lower.constraints):
            raise LimbIKError(f"Bone '{lower.name}' already has an IK constraint; Limb IK will not stack another one.")
        if any(constraint.type == "COPY_ROTATION" for constraint in end.constraints):
            raise LimbIKError(f"Bone '{end.name}' already has Copy Rotation; Limb IK will not stack another one.")
        if _is_roll_decoupled_schema(schema):
            for source in (upper, lower):
                if any(constraint.type == "COPY_ROTATION" for constraint in source.constraints):
                    raise LimbIKError(
                        f"Bone '{source.name}' already has Copy Rotation; Limb IK will not stack its roll-preserving rotation."
                    )
    if _is_direct_preroll_schema(schema):
        new_chains = [
            plan.chain.names
            for plan in plans
            if (plan.chain.kind, plan.chain.side) not in existing_keys
        ]
        owned_constraints = {
            (armature.as_pointer(), pose_bone.as_pointer(), constraint.as_pointer())
            for pose_bone, constraint, _record in inventory["records"]
        }
        direct_dependencies = _direct_source_dependency_problems(
            armature,
            new_chains,
            owned_constraints=owned_constraints,
        )
        if direct_dependencies:
            raise LimbIKError(f"Direct Pre-Roll build was refused: {direct_dependencies[0]}.")
    if not inventory["armature_id"]:
        if _is_enhanced_schema(schema) and armature.data.bones.get(MASTER_NAME) is not None:
            raise LimbIKError(f"Bone '{MASTER_NAME}' already exists and is not this exact generated control.")
        if armature.data.collections.get(CONTROL_COLLECTION_NAME) is not None:
            raise LimbIKError(f"Bone Collection '{CONTROL_COLLECTION_NAME}' is occupied by foreign data.")
        if bpy.data.collections.get(WIDGET_COLLECTION_NAME) is not None:
            raise LimbIKError(f"Collection '{WIDGET_COLLECTION_NAME}' is occupied by foreign data.")


def _new_transaction():
    return {
        "constraints": [],
        "registry_before": {},
        "bone_names": [],
        "objects": [],
        "meshes": [],
        "collections": [],
        "bone_collections": [],
        "new_armature_id": False,
        "new_schema": False,
        "target_rotation_version_touched": False,
        "target_rotation_version_present": False,
        "target_rotation_version_before": None,
        "direct_rest_before": None,
        "direct_rest_touched": False,
        "direct_original_states": {},
        "source_widgets_before": None,
        "source_widgets_touched": False,
        "source_shape_before": {},
    }


def _remember_registry(transaction, pose_bone):
    if pose_bone.name not in transaction["registry_before"]:
        transaction["registry_before"][pose_bone.name] = pose_bone.get(CONSTRAINT_REGISTRY_KEY, None)


def _rollback_build(context, armature, transaction):
    errors = []
    try:
        _mode_set(context, armature, "POSE")
        for pose_bone, constraint in reversed(transaction["constraints"]):
            try:
                # RNA wrapper identity is not stable across the Edit/Object/Pose
                # boundaries used while adding controls.  Resolve the exact
                # transaction-created UUID name again instead of comparing the
                # short-lived Python proxy with ``is``.
                live_owner = armature.pose.bones.get(pose_bone.name)
                live_constraint = live_owner.constraints.get(constraint.name) if live_owner is not None else None
                if live_constraint is not None:
                    live_owner.constraints.remove(live_constraint)
            except (ReferenceError, RuntimeError) as exc:
                errors.append(str(exc))
        for bone_name, raw in transaction["registry_before"].items():
            pose_bone = armature.pose.bones.get(bone_name)
            if pose_bone is None:
                continue
            try:
                if raw is None:
                    if CONSTRAINT_REGISTRY_KEY in pose_bone:
                        del pose_bone[CONSTRAINT_REGISTRY_KEY]
                else:
                    pose_bone[CONSTRAINT_REGISTRY_KEY] = raw
            except (ReferenceError, RuntimeError) as exc:
                errors.append(str(exc))
        _mode_set(context, armature, "OBJECT")
        armature.data.update_tag()
        armature.update_tag(refresh={"OBJECT"})
        context.view_layer.update()
        _mode_set(context, armature, "EDIT")
        for name in reversed(transaction["bone_names"]):
            bone = armature.data.edit_bones.get(name)
            if bone is not None:
                armature.data.edit_bones.remove(bone)
        _mode_set(context, armature, "OBJECT")
        armature.data.update_tag()
        armature.update_tag(refresh={"OBJECT"})
        context.view_layer.update()
    except (LimbIKError, ReferenceError, RuntimeError) as exc:
        errors.append(str(exc))
    if transaction.get("direct_original_states"):
        try:
            _restore_edit_rest_states(context, armature, transaction["direct_original_states"])
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    if transaction.get("direct_rest_touched"):
        try:
            raw = transaction.get("direct_rest_before")
            if raw is None:
                if DIRECT_REST_KEY in armature.data:
                    del armature.data[DIRECT_REST_KEY]
            else:
                armature.data[DIRECT_REST_KEY] = raw
        except (ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    if transaction.get("source_shape_before"):
        try:
            _mode_set(context, armature, "POSE")
            for bone_name, state in transaction["source_shape_before"].items():
                pose_bone = armature.pose.bones.get(bone_name)
                if pose_bone is not None:
                    _restore_pose_shape_state(armature, pose_bone, state, runtime=True)
            _mode_set(context, armature, "OBJECT")
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    if transaction.get("source_widgets_touched"):
        try:
            raw = transaction.get("source_widgets_before")
            if raw is None:
                if SOURCE_WIDGETS_KEY in armature.data:
                    del armature.data[SOURCE_WIDGETS_KEY]
            else:
                armature.data[SOURCE_WIDGETS_KEY] = raw
        except (ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    for obj in reversed(transaction["objects"]):
        try:
            if bpy.data.objects.get(obj.name) is obj:
                bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError) as exc:
            errors.append(str(exc))
    for mesh in reversed(transaction["meshes"]):
        try:
            if bpy.data.meshes.get(mesh.name) is mesh and not mesh.users:
                bpy.data.meshes.remove(mesh)
        except (ReferenceError, RuntimeError) as exc:
            errors.append(str(exc))
    for collection in reversed(transaction["collections"]):
        try:
            if bpy.data.collections.get(collection.name) is collection:
                bpy.data.collections.remove(collection)
        except (ReferenceError, RuntimeError) as exc:
            errors.append(str(exc))
    for collection in reversed(transaction["bone_collections"]):
        try:
            live = armature.data.collections.get(collection.name)
            if live is not None:
                armature.data.collections.remove(live)
        except (ReferenceError, RuntimeError) as exc:
            errors.append(str(exc))
    if transaction["new_armature_id"] and ARMATURE_ID_KEY in armature.data:
        try:
            del armature.data[ARMATURE_ID_KEY]
        except (ReferenceError, RuntimeError) as exc:
            errors.append(str(exc))
    if transaction["new_schema"] and SCHEMA_KEY in armature.data:
        try:
            del armature.data[SCHEMA_KEY]
        except (ReferenceError, RuntimeError) as exc:
            errors.append(str(exc))
    if transaction.get("target_rotation_version_touched"):
        try:
            if transaction.get("target_rotation_version_present"):
                armature.data[TARGET_ROTATION_VERSION_KEY] = transaction.get(
                    "target_rotation_version_before"
                )
            elif TARGET_ROTATION_VERSION_KEY in armature.data:
                del armature.data[TARGET_ROTATION_VERSION_KEY]
        except (ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    return errors


def _master_bone_size(armature):
    source = [bone for bone in armature.data.bones if bone.get(OWNER_KEY) != OWNER_VALUE]
    if not source:
        return 1.0
    points = [Vector(point) for bone in source for point in (bone.head_local, bone.tail_local)]
    span = max((max(point[index] for point in points) - min(point[index] for point in points) for index in range(3)), default=1.0)
    return max(span * 0.18, 0.1)


def _hand_widget_rotation(target, lower):
    """Return a display-only correction that presents the Hand shape in the Forearm frame."""
    target_rotation = target.matrix.to_3x3().normalized()
    lower_rotation = lower.matrix.to_3x3().normalized()
    return (target_rotation.inverted_safe() @ lower_rotation).to_euler("XYZ")


def _foot_seed_group_names(armature, plan):
    end = armature.data.bones.get(plan.chain.end)
    if end is None:
        return {plan.chain.end}
    return {end.name, *(child.name for child in end.children_recursive)}


def _foot_medial_sign(armature, plan, lateral):
    """Map the shared Foot widget's raw +X toward the body in Rest space."""
    fallback = -1.0 if plan.chain.side == "L" else 1.0
    source_foot = armature.data.bones.get(plan.chain.end)
    source_upper = armature.data.bones.get(plan.chain.upper)
    if source_foot is None or source_upper is None:
        return fallback
    anchor_bone = source_upper.parent
    if anchor_bone is not None:
        anchor = (Vector(anchor_bone.head_local) + Vector(anchor_bone.tail_local)) * 0.5
    else:
        roots = [
            bone
            for bone in armature.data.bones
            if bone.parent is None and bone.get(OWNER_KEY) != OWNER_VALUE
        ]
        if not roots:
            return fallback
        anchor = sum(
            ((Vector(bone.head_local) + Vector(bone.tail_local)) * 0.5 for bone in roots),
            Vector(),
        ) / len(roots)
    toward_body = anchor - Vector(source_foot.head_local)
    toward_body.z = 0.0
    score = float(lateral.dot(toward_body))
    return fallback if abs(score) <= EPSILON else (1.0 if score > 0.0 else -1.0)


def _visible_foot_mesh_samples(context, armature, plans):
    """Read evaluated visible geometry once and collect weighted Foot seeds.

    Shoes are often a separate, unbound Mesh (as in X), so deform weights only
    establish a trusted spatial seed.  Nearby visible geometry is absorbed in a
    second pass.  Hidden design/backup objects and generated widgets are never
    sampled.
    """
    leg_plans = tuple(plan for plan in plans if plan.chain.kind == "LEG")
    if not leg_plans:
        return (), {}
    depsgraph = context.evaluated_depsgraph_get()
    armature_inverse = armature.matrix_world.inverted_safe()
    group_names = {
        plan.rig_id: _foot_seed_group_names(armature, plan)
        for plan in leg_plans
    }
    seeds = {plan.rig_id: [] for plan in leg_plans}
    samples = []
    for obj in context.scene.objects:
        if obj.type != "MESH" or obj.get(OWNER_KEY) == OWNER_VALUE:
            continue
        try:
            if not obj.visible_get(view_layer=context.view_layer):
                continue
        except (ReferenceError, RuntimeError, TypeError):
            continue
        driven = any(
            modifier.type == "ARMATURE"
            and modifier.object is armature
            and modifier.show_viewport
            for modifier in obj.modifiers
        )
        indices = {}
        if driven:
            for plan in leg_plans:
                indices[plan.rig_id] = {
                    group.index
                    for name in group_names[plan.rig_id]
                    if (group := obj.vertex_groups.get(name)) is not None
                }
        evaluated = obj.evaluated_get(depsgraph)
        mesh = None
        try:
            mesh = evaluated.to_mesh()
            transform = armature_inverse @ evaluated.matrix_world
            points = []
            for vertex in mesh.vertices:
                point = transform @ vertex.co
                points.append(point)
                if driven:
                    weighted = {
                        assignment.group
                        for assignment in vertex.groups
                        if assignment.weight > FOOT_WIDGET_WEIGHT_THRESHOLD
                    }
                    if weighted:
                        for plan in leg_plans:
                            if weighted.intersection(indices[plan.rig_id]):
                                seeds[plan.rig_id].append(point)
            if points:
                samples.append((tuple(points), driven))
        except (ReferenceError, RuntimeError, TypeError, ValueError):
            continue
        finally:
            if mesh is not None:
                evaluated.to_mesh_clear()
    return tuple(samples), seeds


def _convex_hull_2d(points):
    """Return a deterministic counter-clockwise convex hull."""
    ordered = sorted({(float(point[0]), float(point[1])) for point in points})
    if len(ordered) < 3:
        return ()

    def cross(origin, left, right):
        return (
            (left[0] - origin[0]) * (right[1] - origin[1])
            - (left[1] - origin[1]) * (right[0] - origin[0])
        )

    lower = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= EPSILON:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= EPSILON:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


def _outset_convex_polygon_2d(polygon, margin):
    """Offset a counter-clockwise convex polygon outwards by ``margin``."""
    if len(polygon) < 3 or margin <= 0.0:
        return tuple(polygon)
    result = []
    count = len(polygon)
    for index, point in enumerate(polygon):
        previous = polygon[(index - 1) % count]
        following = polygon[(index + 1) % count]
        incoming = Vector((point[0] - previous[0], point[1] - previous[1]))
        outgoing = Vector((following[0] - point[0], following[1] - point[1]))
        if min(incoming.length, outgoing.length) <= EPSILON:
            return ()
        incoming.normalize()
        outgoing.normalize()
        # A CCW polygon has its interior to the left of each directed edge.
        incoming_normal = Vector((incoming.y, -incoming.x))
        outgoing_normal = Vector((outgoing.y, -outgoing.x))
        bisector = incoming_normal + outgoing_normal
        if bisector.length <= EPSILON:
            result.append((point[0] + incoming_normal.x * margin, point[1] + incoming_normal.y * margin))
            continue
        bisector.normalize()
        denominator = bisector.dot(incoming_normal)
        if denominator <= EPSILON:
            return ()
        distance = min(margin / denominator, margin * 4.0)
        result.append((point[0] + bisector.x * distance, point[1] + bisector.y * distance))
    return tuple(result)


def _resample_closed_polygon_2d(polygon, count=64):
    """Sample a closed polygon at deterministic equal perimeter intervals."""
    if len(polygon) < 3 or count < 3:
        return ()
    lengths = []
    total = 0.0
    for left, right in zip(polygon, polygon[1:] + polygon[:1]):
        length = math.hypot(right[0] - left[0], right[1] - left[1])
        if length <= EPSILON:
            continue
        lengths.append((left, right, total, length))
        total += length
    if len(lengths) < 3 or total <= EPSILON:
        return ()
    result = []
    edge_index = 0
    for index in range(count):
        distance = total * index / count
        while edge_index + 1 < len(lengths) and distance >= lengths[edge_index][2] + lengths[edge_index][3]:
            edge_index += 1
        left, right, start, length = lengths[edge_index]
        factor = min(max((distance - start) / length, 0.0), 1.0)
        result.append(
            (
                left[0] + (right[0] - left[0]) * factor,
                left[1] + (right[1] - left[1]) * factor,
            )
        )
    return tuple(result)


def _replace_foot_widget_geometry(shape, vertices, edges):
    if shape.type != "MESH" or not vertices or not edges:
        raise LimbIKError("A sided Foot widget produced invalid mesh geometry.")
    shape.data.clear_geometry()
    shape.data.from_pydata(vertices, edges, ())
    shape.data.update()


def _foot_widget_fits(context, armature, plans, shapes):
    """Fit each visible Foot outline around nearby evaluated footwear geometry."""
    leg_plans = tuple(plan for plan in plans if plan.chain.kind == "LEG")
    if not leg_plans:
        return {}
    samples, seeds = _visible_foot_mesh_samples(context, armature, leg_plans)
    if not samples:
        return {}
    roots = [
        bone
        for bone in armature.data.bones
        if bone.parent is None and bone.get(OWNER_KEY) != OWNER_VALUE
    ]
    center_x = (
        sum(float(bone.head_local.x) for bone in roots) / len(roots)
        if roots
        else 0.0
    )
    fits = {}
    for plan in leg_plans:
        shape = shapes.get(plan.rig_id)
        if shape is None:
            continue
        seed = seeds.get(plan.rig_id, ())
        if len(seed) < 4:
            continue
        foot = armature.pose.bones[plan.chain.end]
        target = armature.pose.bones[plan.target_name]
        foot_length = max((Vector(foot.tail) - Vector(foot.head)).length, 1.0e-4)
        chain_length = max(
            (plan.joint - plan.start).length + (plan.end - plan.joint).length,
            foot_length,
        )
        capture = max(foot_length * 0.9, chain_length * 0.04)
        seed_min = Vector(tuple(min(point[index] for point in seed) for index in range(3)))
        seed_max = Vector(tuple(max(point[index] for point in seed) for index in range(3)))
        side_sign = 1.0 if plan.chain.side == "L" else -1.0
        candidates = list(seed)
        for points, driven in samples:
            if driven:
                # Weighted vertices already provide the trusted body-foot seed.
                # Pulling every nearby vertex from the full character can catch
                # center ornaments or the opposite foot and over-widen the fit.
                continue
            side_points = [
                point
                for point in points
                if side_sign * (point.x - center_x) >= -EPSILON
            ]
            if not side_points:
                continue
            nearby = [
                point
                for point in side_points
                if all(
                    seed_min[index] - capture <= point[index] <= seed_max[index] + capture
                    for index in range(3)
                )
            ]
            if not nearby:
                continue
            # Separate footwear must have a meaningful fraction of its side
            # near the weighted seed; this keeps broad floors/reference meshes
            # from inflating the controller.
            if len(nearby) >= 4 and len(nearby) / len(side_points) >= 0.02:
                candidates.extend(nearby)
        if len(candidates) < 4:
            continue

        bone_forward = Vector(foot.tail) - Vector(foot.head)
        bone_forward.z = 0.0
        if bone_forward.length <= EPSILON:
            bone_forward = Vector((0.0, -1.0, 0.0))
        bone_forward.normalize()
        mean_x = sum(point.x for point in candidates) / len(candidates)
        mean_y = sum(point.y for point in candidates) / len(candidates)
        covariance_xx = sum((point.x - mean_x) ** 2 for point in candidates)
        covariance_yy = sum((point.y - mean_y) ** 2 for point in candidates)
        covariance_xy = sum(
            (point.x - mean_x) * (point.y - mean_y)
            for point in candidates
        )
        trace = covariance_xx + covariance_yy
        discriminant = math.hypot(covariance_xx - covariance_yy, 2.0 * covariance_xy)
        if trace > EPSILON and discriminant / trace >= 0.05:
            angle = 0.5 * math.atan2(
                2.0 * covariance_xy,
                covariance_xx - covariance_yy,
            )
            forward = Vector((math.cos(angle), math.sin(angle), 0.0))
            if forward.dot(bone_forward) < 0.0:
                forward.negate()
        else:
            forward = bone_forward
        lateral = Vector((-forward.y, forward.x, 0.0))
        lateral.normalize()
        down = Vector((0.0, 0.0, -1.0))
        u_values = [point.dot(lateral) for point in candidates]
        v_values = [point.dot(forward) for point in candidates]
        width = max(u_values) - min(u_values)
        length = max(v_values) - min(v_values)
        if min(width, length) <= EPSILON:
            continue
        margin = max(min(width, length) * FOOT_WIDGET_MARGIN_RATIO, chain_length * 0.004)
        desired_u_min = min(u_values) - margin
        desired_u_max = max(u_values) + margin
        desired_v_min = min(v_values) - margin
        desired_v_max = max(v_values) + margin

        # Rain's source Foot silhouette uses raw +Y for the toes and raw +X
        # for the medial / big-toe edge.  Map that semantic axis toward the
        # body centre instead of mirroring by the bone suffix.  The old suffix
        # mapping was exactly reversed on X (Left needs negative local X,
        # Right positive local X), putting the broad side by the little toe.
        medial_sign = _foot_medial_sign(armature, plan, lateral)
        target_rotation = target.matrix.to_3x3().normalized()
        display_rotation = Matrix((lateral, forward, down)).transposed()
        sole_gap = max(foot_length * FOOT_WIDGET_SOLE_GAP_RATIO, chain_length * 0.003)
        desired_top = min(point.z for point in candidates) - sole_gap

        if shape.get(KIND_KEY) in {"FOOT_L", "FOOT_R"}:
            hull = _convex_hull_2d(zip(u_values, v_values))
            outline = _resample_closed_polygon_2d(
                _outset_convex_polygon_2d(hull, margin),
                64,
            )
            if len(outline) != 64:
                continue
            origin_u = (min(point[0] for point in outline) + max(point[0] for point in outline)) * 0.5
            origin_v = (min(point[1] for point in outline) + max(point[1] for point in outline)) * 0.5
            raw_vertices = tuple(
                (medial_sign * (u - origin_u), v - origin_v, 0.0)
                for u, v in outline
            )
            edges = tuple((index, (index + 1) % len(raw_vertices)) for index in range(len(raw_vertices)))
            origin = lateral * origin_u + forward * origin_v
            origin.z = desired_top
            points = tuple(
                origin + lateral * (u - origin_u) + forward * (v - origin_v)
                for u, v in outline
            )
            center_gap = chain_length * 0.001
            if plan.chain.side == "L":
                violation = center_x + center_gap - min(point.x for point in points)
                if violation > 0.0:
                    origin.x += violation
            else:
                violation = max(point.x for point in points) - (center_x - center_gap)
                if violation > 0.0:
                    origin.x -= violation
            fits[plan.rig_id] = {
                "geometry": (raw_vertices, edges),
                "scale": (medial_sign, 1.0, 1.0),
                "translation": target_rotation.inverted_safe() @ (origin - Vector(target.head)),
                "rotation": (target_rotation.inverted_safe() @ display_rotation).to_euler("XYZ"),
            }
            continue

        raw_vertices = tuple(Vector(vertex.co) for vertex in shape.data.vertices)
        if not raw_vertices:
            continue
        raw_u = [medial_sign * vertex.x for vertex in raw_vertices]
        raw_v = [vertex.y for vertex in raw_vertices]
        raw_u_span = max(raw_u) - min(raw_u)
        raw_v_span = max(raw_v) - min(raw_v)
        if min(raw_u_span, raw_v_span) <= EPSILON:
            continue
        scale_x = (desired_u_max - desired_u_min) / raw_u_span
        scale_y = (desired_v_max - desired_v_min) / raw_v_span
        scale_z = min(scale_x, scale_y)
        origin_u = desired_u_min - scale_x * min(raw_u)
        origin_v = desired_v_min - scale_y * min(raw_v)
        origin = lateral * origin_u + forward * origin_v

        def display_points(display_origin):
            return tuple(
                display_origin
                + lateral * (medial_sign * scale_x * vertex.x)
                + forward * (scale_y * vertex.y)
                + down * (scale_z * vertex.z)
                for vertex in raw_vertices
            )

        origin.z = desired_top
        points = display_points(origin)
        origin.z -= max(point.z for point in points) - desired_top
        points = display_points(origin)

        center_gap = chain_length * 0.001
        if plan.chain.side == "L":
            violation = center_x + center_gap - min(point.x for point in points)
            if violation > 0.0:
                origin.x += violation
        else:
            violation = max(point.x for point in points) - (center_x - center_gap)
            if violation > 0.0:
                origin.x -= violation

        fits[plan.rig_id] = {
            "scale": (medial_sign * scale_x, scale_y, scale_z),
            "translation": target_rotation.inverted_safe() @ (origin - Vector(target.head)),
            "rotation": (target_rotation.inverted_safe() @ display_rotation).to_euler("XYZ"),
        }
    return fits


def _create_control_bones(context, armature, armature_id, plans, transaction, *, schema=CURRENT_SCHEMA, create_master=False):
    # Blender's X-Mirror edit option also mirrors programmatic EditBone writes.
    # On an asymmetric/rest-rolled production skeleton, creating the R control
    # after L can silently overwrite L's roll.  This temporary option change is
    # local to the edit transaction and is restored on every exit path.
    master_size = _master_bone_size(armature)
    line_rest_points = {}
    if _is_enhanced_schema(schema):
        for plan in plans:
            if plan.line_head is not None and plan.line_tail is not None:
                # Transactional recovery must restore an older rig's exact
                # helper geometry, even after this version shortens new lines.
                line_rest_points[plan.rig_id] = (Vector(plan.line_head), Vector(plan.line_tail))
                continue
            # EditBone coordinates are Armature-local, exactly like the Pole
            # coordinates that will be written below.  Always author the short
            # helper from the real Rest joint toward that real Pole.  Mapping
            # the target back through the current upper-bone pose made the two
            # endpoints use different coordinate spaces; on a second Rebuild
            # that could reverse the helper even though the Pole and IK plane
            # were still anatomically correct.
            line_head = Vector(armature.data.bones[plan.chain.lower].head_local)
            line_target = Vector(plan.pole_head)
            line_axis = line_target - line_head
            if line_axis.length <= EPSILON:
                line_axis = Vector((0.0, 1.0, 0.0))
            helper_length = max((plan.end - plan.start).length * 0.06, 0.01)
            line_rest_points[plan.rig_id] = (
                line_head,
                line_head + line_axis.normalized() * min(line_axis.length, helper_length),
            )
    mirror_x = bool(armature.data.use_mirror_x)
    try:
        armature.data.use_mirror_x = False
        _mode_set(context, armature, "EDIT")
        master = armature.data.edit_bones.get(MASTER_NAME)
        if _is_enhanced_schema(schema) and create_master:
            master = armature.data.edit_bones.new(MASTER_NAME)
            if master.name != MASTER_NAME:
                raise LimbIKError(f"Blender renamed generated bone '{MASTER_NAME}'; build was rolled back.")
            transaction["bone_names"].append(master.name)
            master.head = (0.0, 0.0, 0.0)
            master.tail = (0.0, master_size, 0.0)
            master.roll = 0.0
            master.parent = None
            master.use_connect = False
            master.use_deform = False
        for plan in plans:
            target = armature.data.edit_bones.new(plan.target_name)
            if target.name != plan.target_name:
                raise LimbIKError(f"Blender renamed generated bone '{plan.target_name}'; build was rolled back.")
            transaction["bone_names"].append(target.name)
            target.head = plan.target_head
            target.tail = plan.target_tail
            target.parent = None
            target.use_connect = False
            target.use_deform = False
            target.align_roll(plan.target_z)

            pole = armature.data.edit_bones.new(plan.pole_name)
            if pole.name != plan.pole_name:
                raise LimbIKError(f"Blender renamed generated bone '{plan.pole_name}'; build was rolled back.")
            transaction["bone_names"].append(pole.name)
            pole.head = plan.pole_head
            pole.tail = plan.pole_tail
            pole.parent = None
            pole.use_connect = False
            pole.use_deform = False

            if _uses_dynamic_pole_display(schema) and not _is_enhanced_schema(schema):
                display = armature.data.edit_bones.new(plan.display_name)
                if display.name != plan.display_name:
                    raise LimbIKError(
                        f"Blender renamed generated bone '{plan.display_name}'; build was rolled back."
                    )
                transaction["bone_names"].append(display.name)
                display.head = plan.pole_head
                aim = plan.pole_joint - plan.pole_head
                if aim.length <= EPSILON:
                    aim = Vector((0.0, 1.0, 0.0))
                # Local -Y starts aimed at the bend joint.  The Pose constraint
                # keeps this display-only frame aimed there as the Pole moves.
                display.tail = plan.pole_head - aim.normalized() * max(
                    (plan.end - plan.start).length * 0.06,
                    0.01,
                )
                display.parent = pole
                display.use_connect = False
                display.use_deform = False

            if _is_enhanced_schema(schema):
                target.parent = master
                pole.parent = master

                if _is_roll_decoupled_schema(schema):
                    source_upper = armature.data.edit_bones[plan.chain.upper]
                    source_lower = armature.data.edit_bones[plan.chain.lower]
                    anchor = source_upper.parent if source_upper.parent is not None else master
                    rest = plan.mechanism_rest or {}

                    def create_mechanism(name, source, role, parent, *, connected=False):
                        bone = armature.data.edit_bones.new(name)
                        if bone.name != name:
                            raise LimbIKError(f"Blender renamed generated bone '{name}'; build was rolled back.")
                        transaction["bone_names"].append(bone.name)
                        saved = rest.get(role, {})
                        bone.head = Vector(saved.get("head", source.head))
                        bone.tail = Vector(saved.get("tail", source.tail))
                        if "z" in saved:
                            bone.align_roll(Vector(saved["z"]))
                        else:
                            bone.roll = source.roll
                        bone.parent = parent
                        bone.use_connect = bool(connected)
                        bone.use_deform = False
                        return bone

                    mch_upper = create_mechanism(
                        plan.mch_upper_name,
                        source_upper,
                        "mch_upper",
                        anchor,
                    )
                    create_mechanism(
                        plan.mch_lower_name,
                        source_lower,
                        "mch_lower",
                        mch_upper,
                        connected=True,
                    )
                    # Both ORI bones share the same upstream anchor.  They are
                    # deliberately neither connected nor parented to each
                    # other: each Damped Track performs an independent shortest
                    # swing from its matching source rest frame.
                    create_mechanism(
                        plan.ori_upper_name,
                        source_upper,
                        "ori_upper",
                        anchor,
                    )
                    create_mechanism(
                        plan.ori_lower_name,
                        source_lower,
                        "ori_lower",
                        anchor,
                    )

                line = armature.data.edit_bones.new(plan.line_name)
                if line.name != plan.line_name:
                    raise LimbIKError(f"Blender renamed generated bone '{plan.line_name}'; build was rolled back.")
                transaction["bone_names"].append(line.name)
                line.head, line.tail = line_rest_points[plan.rig_id]
                if (Vector(line.tail) - Vector(line.head)).length <= EPSILON:
                    line.tail = Vector(line.head) + Vector((0.0, max((plan.end - plan.start).length * 0.06, 0.01), 0.0))
                line.parent = armature.data.edit_bones[plan.chain.upper]
                line.use_connect = False
                line.use_deform = False

                display = armature.data.edit_bones.new(plan.display_name)
                if display.name != plan.display_name:
                    raise LimbIKError(f"Blender renamed generated bone '{plan.display_name}'; build was rolled back.")
                transaction["bone_names"].append(display.name)
                display.head = plan.pole_head
                aim = plan.pole_joint - plan.pole_head
                if aim.length <= EPSILON:
                    aim = Vector((0.0, 1.0, 0.0))
                # Rain's helper is authored with local -Y aimed at the joint;
                # matching that rest orientation avoids a 180-degree Damped
                # Track singularity before the constraint is evaluated.
                display.tail = plan.pole_head - aim.normalized() * max((plan.end - plan.start).length * 0.06, 0.01)
                display.parent = pole
                display.use_connect = False
                display.use_deform = False

                if plan.chain.kind == "LEG":
                    heel = armature.data.edit_bones.new(plan.heel_name)
                    if heel.name != plan.heel_name:
                        raise LimbIKError(f"Blender renamed generated bone '{plan.heel_name}'; build was rolled back.")
                    transaction["bone_names"].append(heel.name)
                    heel.head = plan.heel_head
                    heel.tail = plan.heel_tail
                    heel.parent = target
                    heel.use_connect = False
                    heel.use_deform = False
                    heel.align_roll(plan.heel_z)

                    solver = armature.data.edit_bones.new(plan.solver_target_name)
                    if solver.name != plan.solver_target_name:
                        raise LimbIKError(f"Blender renamed generated bone '{plan.solver_target_name}'; build was rolled back.")
                    transaction["bone_names"].append(solver.name)
                    solver.head = plan.target_head
                    solver.tail = plan.target_tail
                    solver.parent = heel
                    solver.use_connect = False
                    solver.use_deform = False
                    solver.align_roll(plan.target_z)

        # Finish the topology edit before restoring the artist's symmetry flag;
        # restoring it while Edit Mode is live can itself mirror the last write.
        _mode_set(context, armature, "OBJECT")
    finally:
        armature.data.use_mirror_x = mirror_x

    # Commit the edited Armature topology through an evaluated Object-mode
    # boundary before PoseBone/constraint access.  Dense production rigs can
    # otherwise keep a stale depsgraph relation for the newly-created controls.
    armature.data.update_tag()
    armature.update_tag(refresh={"OBJECT"})
    context.view_layer.update()
    _mode_set(context, armature, "POSE")
    collection = _ensure_control_collection(armature, armature_id, transaction)
    if _is_enhanced_schema(schema):
        master = armature.data.bones[MASTER_NAME]
        master.use_deform = False
        _tag(master, armature_id, role="MASTER")
        collection.assign(master)
    for plan in plans:
        target = armature.data.bones[plan.target_name]
        pole = armature.data.bones[plan.pole_name]
        target.use_deform = False
        pole.use_deform = False
        _tag(target, armature_id, role=LIMB_SPEC[plan.chain.kind]["target_role"], rig_id=plan.rig_id, kind=plan.chain.kind, side=plan.chain.side, chain=plan.chain.names)
        target[AUTO_ALIGN_KEY] = bool(plan.auto_align)
        _tag(pole, armature_id, role="POLE", rig_id=plan.rig_id, kind=plan.chain.kind, side=plan.chain.side, chain=plan.chain.names)
        if plan.persist_pole_direction:
            pole[POLE_DIRECTION_KEY] = [float(component) for component in plan.pole_direction]
            if _is_roll_decoupled_schema(schema) or _is_direct_preroll_schema(schema):
                configured = (
                    plan.configured_pole_direction
                    if plan.configured_pole_direction is not None
                    else plan.pole_direction
                )
                pole[POLE_CONFIGURED_DIRECTION_KEY] = [float(component) for component in configured]
        collection.assign(target)
        collection.assign(pole)
        if _uses_dynamic_pole_display(schema) and not _is_enhanced_schema(schema):
            display = armature.data.bones[plan.display_name]
            _tag(
                display,
                armature_id,
                role="POLE_DISPLAY",
                rig_id=plan.rig_id,
                kind=plan.chain.kind,
                side=plan.chain.side,
                chain=plan.chain.names,
            )
            display.use_deform = False
            display.hide = True
            display.hide_select = True
            collection.assign(display)
        if _is_enhanced_schema(schema):
            line = armature.data.bones[plan.line_name]
            display = armature.data.bones[plan.display_name]
            _tag(line, armature_id, role="POLE_LINE", rig_id=plan.rig_id, kind=plan.chain.kind, side=plan.chain.side, chain=plan.chain.names)
            _tag(display, armature_id, role="POLE_DISPLAY", rig_id=plan.rig_id, kind=plan.chain.kind, side=plan.chain.side, chain=plan.chain.names)
            line.use_deform = display.use_deform = False
            line.hide_select = True
            display.hide = True
            display.hide_select = True
            collection.assign(line)
            collection.assign(display)
            if _is_roll_decoupled_schema(schema):
                for name, role in (
                    (plan.mch_upper_name, "MCH_UPPER"),
                    (plan.mch_lower_name, "MCH_LOWER"),
                    (plan.ori_upper_name, "ORI_UPPER"),
                    (plan.ori_lower_name, "ORI_LOWER"),
                ):
                    mechanism = armature.data.bones[name]
                    _tag(
                        mechanism,
                        armature_id,
                        role=role,
                        rig_id=plan.rig_id,
                        kind=plan.chain.kind,
                        side=plan.chain.side,
                        chain=plan.chain.names,
                    )
                    mechanism.use_deform = False
                    mechanism.hide = True
                    mechanism.hide_select = True
                    collection.assign(mechanism)
            if plan.chain.kind == "LEG":
                heel = armature.data.bones[plan.heel_name]
                solver = armature.data.bones[plan.solver_target_name]
                _tag(heel, armature_id, role="HEEL_ROLL", rig_id=plan.rig_id, kind=plan.chain.kind, side=plan.chain.side, chain=plan.chain.names)
                _tag(solver, armature_id, role="FOOT_TARGET", rig_id=plan.rig_id, kind=plan.chain.kind, side=plan.chain.side, chain=plan.chain.names)
                heel.use_deform = solver.use_deform = False
                solver.hide = True
                solver.hide_select = True
                collection.assign(heel)
                collection.assign(solver)
    # BoneCollection assignment and Bone ID-property writes can invalidate the
    # production depsgraph a second time.  Rebuild it at an Object boundary so
    # IK relation creation sees BONE_DONE nodes for every new control.
    _mode_set(context, armature, "OBJECT")
    armature.data.update_tag()
    armature.update_tag(refresh={"OBJECT"})
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()
    context.scene.frame_set(context.scene.frame_current)
    context.view_layer.update()
    _mode_set(context, armature, "POSE")
    context.view_layer.update()


def _create_constraint_shells(armature, armature_id, plans, transaction, *, schema=CURRENT_SCHEMA, create_master=False):
    """Create exact constraint references before their future control bones.

    Blender's dense-rig depsgraph only creates same-Armature BONE_DONE nodes
    reliably when those references already exist as Edit Mode commits the new
    bones.  No evaluation is requested until `_create_control_bones` completes.
    """
    for plan in plans:
        # Schema 3's IK owner is itself a newly-created MCH bone, so it cannot
        # have a pre-topology shell.  `_create_control_bones` commits all MCH,
        # ORI, and target references through an Object boundary before the
        # complete schema-3 constraint graph is authored below.
        if _is_roll_decoupled_schema(schema):
            continue
        lower = armature.pose.bones[plan.chain.lower]
        _remember_registry(transaction, lower)
        ik = lower.constraints.new(type="IK")
        ik.name = _constraint_name(plan.rig_id, "IK")
        ik.target = armature
        ik.subtarget = plan.solver_target_name if _is_enhanced_schema(schema) else plan.target_name
        ik.pole_target = armature
        ik.pole_subtarget = plan.pole_name
        ik.chain_count = 2
        ik.pole_angle = plan.pole_angle
        ik.target_space = "WORLD"
        ik.owner_space = "WORLD"
        if hasattr(ik, "use_tail"):
            ik.use_tail = True
        if hasattr(ik, "use_stretch"):
            ik.use_stretch = False
        if hasattr(ik, "use_rotation"):
            ik.use_rotation = False
        transaction["constraints"].append((lower, ik))

def _create_constraints_and_shapes(
    context,
    armature,
    armature_id,
    plans,
    transaction,
    *,
    schema=CURRENT_SCHEMA,
    create_master=False,
    source_widget_chains=(),
    decorate_sources=True,
    foot_widget_kinds=None,
    target_rotation_version=0,
):
    widget_collection = _ensure_widget_collection(context, armature_id, transaction)
    if schema == LEGACY_SCHEMA:
        widgets = {"POLE": _ensure_widget(context, armature_id, widget_collection, "POLE", transaction, schema=schema)}
    elif _is_direct_preroll_schema(schema):
        widgets = {
            "POLE_ARROW": _ensure_widget(
                context, armature_id, widget_collection, "POLE_ARROW", transaction, schema=schema
            )
        }
    else:
        widgets = {
            "POLE_ARROW": _ensure_widget(context, armature_id, widget_collection, "POLE_ARROW", transaction, schema=schema),
            "POLE_LINE": _ensure_widget(context, armature_id, widget_collection, "POLE_LINE", transaction, schema=schema),
            "MASTER": _ensure_widget(context, armature_id, widget_collection, "MASTER", transaction, schema=schema),
        }
    if any(plan.chain.kind == "ARM" for plan in plans):
        widgets["ARM"] = _ensure_widget(context, armature_id, widget_collection, "HAND", transaction, schema=schema)
    foot_widgets = {}
    leg_plans = tuple(plan for plan in plans if plan.chain.kind == "LEG")
    if leg_plans:
        for plan in leg_plans:
            if _is_direct_preroll_schema(schema):
                kind = (
                    foot_widget_kinds.get((plan.chain.kind, plan.chain.side), f"FOOT_{plan.chain.side}")
                    if foot_widget_kinds is not None
                    else f"FOOT_{plan.chain.side}"
                )
                if kind not in {"FOOT", f"FOOT_{plan.chain.side}"}:
                    raise LimbIKError(
                        f"Saved {plan.chain.side} Leg Foot widget ownership is invalid."
                    )
            else:
                kind = "FOOT"
            foot_widgets[plan.rig_id] = _ensure_widget(
                context, armature_id, widget_collection, kind, transaction, schema=schema
            )
        if not _is_direct_preroll_schema(schema):
            widgets["LEG"] = foot_widgets[leg_plans[0].rig_id]
        if _is_enhanced_schema(schema):
            widgets["HEEL"] = _ensure_widget(context, armature_id, widget_collection, "HEEL", transaction, schema=schema)

    pending_records = []
    runtime_constraints = {}
    runtime_roll_constraints = {}
    if _is_enhanced_schema(schema) and create_master:
        roots = [bone for bone in armature.data.bones if bone.parent is None and bone.get(OWNER_KEY) != OWNER_VALUE]
        if not roots:
            raise LimbIKError("No unparented source root is available for the Master control.")
        for root in roots:
            pose_root = armature.pose.bones[root.name]
            _remember_registry(transaction, pose_root)
            follow = pose_root.constraints.new(type="COPY_TRANSFORMS")
            follow.name = _constraint_name(armature_id, "MASTER_FOLLOW")
            follow.target = armature
            follow.subtarget = MASTER_NAME
            follow.target_space = "POSE"
            follow.owner_space = "POSE"
            if hasattr(follow, "mix_mode"):
                follow.mix_mode = "BEFORE"
            transaction["constraints"].append((pose_root, follow))
            pending_records.append((pose_root, follow, None, "MASTER_FOLLOW"))
    for plan in plans:
        target_pose = armature.pose.bones[plan.target_name]
        if target_rotation_version == TARGET_ROTATION_VERSION:
            # Generated hand/foot controls use animator-readable Local XYZ:
            # X bends, Y twists/banks, and Z swings/turns.  The rest frame is
            # authored by the selected build method; this only chooses the
            # channel representation and does not rotate the current pose.
            target_pose.rotation_mode = "XYZ"
        target_pose.matrix_basis = plan.target_basis
        armature.pose.bones[plan.pole_name].matrix_basis = plan.pole_basis
        lower = armature.pose.bones[plan.chain.lower]
        upper = armature.pose.bones[plan.chain.upper]
        end = armature.pose.bones[plan.chain.end]
        if _is_roll_decoupled_schema(schema):
            # Establish the minimum-swing reference from the actual pose at
            # Build/Rebuild time, not blindly from Edit-rest.  This preserves
            # deliberate pre-existing FK roll and lets a Rebuild establish a
            # fresh, pop-free orientation baseline for subsequent IK motion.
            mechanism_pose = plan.mechanism_pose or {}
            for role, name, desired in (
                ("mch_upper", plan.mch_upper_name, plan.desired_upper_matrix),
                ("mch_lower", plan.mch_lower_name, plan.desired_lower_matrix),
                ("ori_upper", plan.ori_upper_name, plan.desired_upper_matrix),
                ("ori_lower", plan.ori_lower_name, plan.desired_lower_matrix),
            ):
                pose_bone = armature.pose.bones[name]
                saved = mechanism_pose.get(role)
                if saved is None:
                    pose_bone.matrix = desired.copy()
                else:
                    pose_bone.rotation_mode = saved["rotation_mode"]
                    pose_bone.matrix_basis = saved["matrix_basis"].copy()
            context.view_layer.update()
        ik_name = _constraint_name(plan.rig_id, "IK")
        if _is_roll_decoupled_schema(schema):
            ik_owner = armature.pose.bones[plan.mch_lower_name]
            _remember_registry(transaction, ik_owner)
            ik = ik_owner.constraints.new(type="IK")
            ik.name = ik_name
            ik.target = armature
            ik.subtarget = plan.solver_target_name
            ik.pole_target = armature
            ik.pole_subtarget = plan.pole_name
            ik.chain_count = 2
            ik.pole_angle = plan.pole_angle
            ik.target_space = "WORLD"
            ik.owner_space = "WORLD"
            if hasattr(ik, "use_tail"):
                ik.use_tail = True
            if hasattr(ik, "use_stretch"):
                ik.use_stretch = False
            if hasattr(ik, "use_rotation"):
                ik.use_rotation = False
            transaction["constraints"].append((ik_owner, ik))
        else:
            ik_owner = lower
            ik = lower.constraints.get(ik_name)
            if ik is None or ik.type != "IK":
                raise LimbIKError(f"{plan.chain.side} {plan.chain.kind.title()} IK shell disappeared while creating controls.")
        _remember_registry(transaction, end)
        copy_rotation = end.constraints.new(type="COPY_ROTATION")
        copy_rotation.name = _constraint_name(plan.rig_id, "END_ROTATION")
        copy_rotation.target = armature
        solver_name = plan.solver_target_name if _is_enhanced_schema(schema) else plan.target_name
        copy_rotation.subtarget = solver_name
        if _is_direct_preroll_schema(schema) and plan.chain.kind == "ARM":
            # The minimal Direct Target is authored in the final Forearm frame.
            # Local Owner Orientation transports animator rotation into the
            # Hand's own Rest axes while Local With Parent preserves its modeled
            # wrist offset and lets translation-only IK follow the Forearm.
            copy_rotation.target_space = "LOCAL_OWNER_ORIENT"
            copy_rotation.owner_space = "LOCAL_WITH_PARENT"
        else:
            copy_rotation.target_space = "WORLD"
            copy_rotation.owner_space = "WORLD"
        if hasattr(copy_rotation, "mix_mode"):
            copy_rotation.mix_mode = "REPLACE"
        # Auto Align is a persistent evaluation mode, not a polling loop:
        # with this single owned constraint muted, the Hand/Foot continuously
        # inherits the solved Forearm/Shin orientation.  Turning Auto off
        # transactionally syncs the Target before this is unmuted.
        copy_rotation.mute = bool(plan.auto_align)
        transaction["constraints"].append((end, copy_rotation))
        auto_offset_rotation = None
        if target_rotation_version == TARGET_ROTATION_VERSION:
            # Auto Align supplies the natural Forearm/Shin frame.  This second
            # relation adds the visible Target's local Euler offset after that
            # natural rotation, so Auto stays live while animators retain all
            # three wrist/ankle rotation degrees of freedom.  It needs no
            # helper bone and is mutually exclusive with END_ROTATION.
            auto_offset_rotation = end.constraints.new(type="COPY_ROTATION")
            auto_offset_rotation.name = _constraint_name(
                plan.rig_id,
                "AUTO_OFFSET_ROTATION",
            )
            auto_offset_rotation.target = armature
            auto_offset_rotation.subtarget = plan.target_name
            auto_offset_rotation.target_space = "LOCAL"
            auto_offset_rotation.owner_space = "LOCAL"
            auto_offset_rotation.use_x = True
            auto_offset_rotation.use_y = True
            auto_offset_rotation.use_z = True
            auto_offset_rotation.invert_x = False
            auto_offset_rotation.invert_y = False
            auto_offset_rotation.invert_z = False
            if hasattr(auto_offset_rotation, "mix_mode"):
                auto_offset_rotation.mix_mode = "AFTER"
            auto_offset_rotation.mute = not bool(plan.auto_align)
            transaction["constraints"].append((end, auto_offset_rotation))
        roll_constraints = []
        if _is_roll_decoupled_schema(schema):
            for segment, source, mechanism_name, orientation_name in (
                ("UPPER", upper, plan.mch_upper_name, plan.ori_upper_name),
                ("LOWER", lower, plan.mch_lower_name, plan.ori_lower_name),
            ):
                orientation = armature.pose.bones[orientation_name]
                _remember_registry(transaction, orientation)
                location = orientation.constraints.new(type="COPY_LOCATION")
                location.name = _constraint_name(plan.rig_id, f"ORI_{segment}_LOCATION")
                location.target = armature
                location.subtarget = mechanism_name
                location.head_tail = 0.0
                location.target_space = "WORLD"
                location.owner_space = "WORLD"
                transaction["constraints"].append((orientation, location))

                track = orientation.constraints.new(type="DAMPED_TRACK")
                track.name = _constraint_name(plan.rig_id, f"ORI_{segment}_TRACK")
                track.target = armature
                track.subtarget = mechanism_name
                track.head_tail = 1.0
                track.track_axis = "TRACK_Y"
                track.target_space = "WORLD"
                track.owner_space = "WORLD"
                transaction["constraints"].append((orientation, track))

                _remember_registry(transaction, source)
                source_rotation = source.constraints.new(type="COPY_ROTATION")
                source_rotation.name = _constraint_name(plan.rig_id, f"SOURCE_{segment}_ROTATION")
                source_rotation.target = armature
                source_rotation.subtarget = orientation_name
                source_rotation.target_space = "WORLD"
                source_rotation.owner_space = "WORLD"
                if hasattr(source_rotation, "mix_mode"):
                    source_rotation.mix_mode = "REPLACE"
                transaction["constraints"].append((source, source_rotation))
                roll_constraints.extend(
                    (
                        (orientation, location, f"ORI_{segment}_LOCATION"),
                        (orientation, track, f"ORI_{segment}_TRACK"),
                        (source, source_rotation, f"SOURCE_{segment}_ROTATION"),
                    )
                )
        runtime_roll_constraints[plan.rig_id] = roll_constraints
        extras = []
        if _is_enhanced_schema(schema):
            line = armature.pose.bones[plan.line_name]
            _remember_registry(transaction, line)
            stretch = line.constraints.new(type="STRETCH_TO")
            stretch.name = _constraint_name(plan.rig_id, "POLE_LINE_STRETCH")
            stretch.target = armature
            stretch.subtarget = plan.pole_name
            stretch.target_space = "WORLD"
            stretch.owner_space = "WORLD"
            if hasattr(stretch, "volume"):
                stretch.volume = "NO_VOLUME"
            # Blender initializes this from the current target distance.  The
            # helper now has a deliberately short Edit-rest body, so preserve
            # that bone length as the constraint's rest length; Pose evaluation
            # can then stretch its tail all the way to the Pole as intended.
            if hasattr(stretch, "rest_length"):
                stretch.rest_length = float(line.bone.length)
            transaction["constraints"].append((line, stretch))
            extras.append(stretch)
        if _uses_dynamic_pole_display(schema):
            display = armature.pose.bones[plan.display_name]
            _remember_registry(transaction, display)
            track = display.constraints.new(type="DAMPED_TRACK")
            track.name = _constraint_name(plan.rig_id, "POLE_DISPLAY_TRACK")
            track.target = armature
            track.subtarget = plan.chain.lower
            track.track_axis = "TRACK_NEGATIVE_Y"
            track.target_space = "WORLD"
            track.owner_space = "WORLD"
            transaction["constraints"].append((display, track))
            extras.append(track)
        if _is_enhanced_schema(schema) and plan.chain.kind == "LEG":
            heel = armature.pose.bones[plan.heel_name]
            _remember_registry(transaction, heel)
            heel_limit = heel.constraints.new(type="LIMIT_ROTATION")
            heel_limit.name = _constraint_name(plan.rig_id, "HEEL_LIMIT")
            heel_limit.owner_space = "LOCAL"
            heel_limit.use_limit_x = True
            heel_limit.min_x = -math.pi * 0.5
            heel_limit.max_x = math.radians(130.0)
            transaction["constraints"].append((heel, heel_limit))
            extras.append(heel_limit)
        runtime_constraints[plan.rig_id] = (
            ik_owner,
            ik,
            copy_rotation,
            auto_offset_rotation,
            *extras,
        )

    # Configure every same-Armature relation first, then rebuild the relation
    # graph once.  Creating and evaluating one side at a time leaves later
    # controls without BONE_DONE nodes on dense production rigs.
    _mode_set(context, armature, "OBJECT")
    armature.data.update_tag()
    armature.update_tag(refresh={"OBJECT"})
    context.scene.update_tag()
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()
    _mode_set(context, armature, "POSE")
    context.view_layer.update()

    def set_shape(pose_bone, shape, scale, *, transform=None, translation=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0), mirror_x=False):
        pose_bone.custom_shape = shape
        pose_bone.custom_shape_transform = transform
        pose_bone.use_custom_shape_bone_size = False
        if isinstance(scale, (int, float)):
            components = (float(scale), float(scale), float(scale))
        else:
            components = tuple(float(component) for component in scale)
            if len(components) != 3:
                raise LimbIKError("A custom-shape scale must contain exactly three values.")
        if mirror_x:
            components = (-abs(components[0]), components[1], components[2])
        pose_bone.custom_shape_scale_xyz = components
        pose_bone.custom_shape_translation = translation
        pose_bone.custom_shape_rotation_euler = rotation
        if hasattr(pose_bone, "custom_shape_wire_width"):
            pose_bone.custom_shape_wire_width = 2.0
        _write_control_visual_default(pose_bone)

    if _is_enhanced_schema(schema) and create_master:
        master_pb = armature.pose.bones[MASTER_NAME]
        set_shape(master_pb, widgets["MASTER"], _master_bone_size(armature) * 2.8, rotation=(math.pi * 0.5, 0.0, 0.0))
    foot_fits = _foot_widget_fits(context, armature, plans, foot_widgets) if foot_widgets else {}

    def set_foot_shape(plan, target, chain_length):
        shape = foot_widgets[plan.rig_id]
        fit = foot_fits.get(plan.rig_id)
        if fit is not None:
            geometry = fit.get("geometry")
            if geometry is not None:
                _replace_foot_widget_geometry(shape, *geometry)
            set_shape(
                target,
                shape,
                fit["scale"],
                translation=fit["translation"],
                rotation=fit["rotation"],
            )
            return
        foot = armature.pose.bones[plan.chain.end]
        foot_vector = Vector(foot.tail) - Vector(foot.head)
        foot_length = max(
            foot_vector.length,
            chain_length * 0.12,
        )
        forward = foot_vector.copy()
        forward.z = 0.0
        if forward.length <= EPSILON:
            forward = Vector((0.0, -1.0, 0.0))
        forward.normalize()
        lateral = Vector((-forward.y, forward.x, 0.0))
        lateral.normalize()
        down = Vector((0.0, 0.0, -1.0))
        medial_sign = _foot_medial_sign(armature, plan, lateral)
        target_rotation = target.matrix.to_3x3().normalized()
        display_rotation = Matrix((lateral, forward, down)).transposed()
        plane_z = min(float(foot.head.z), float(foot.tail.z)) - foot_length * 0.08
        origin = Vector(target.head) + forward * (foot_length * 0.12)
        origin.z = plane_z
        set_shape(
            target,
            shape,
            (medial_sign * foot_length, foot_length, foot_length),
            translation=target_rotation.inverted_safe() @ (origin - Vector(target.head)),
            rotation=(target_rotation.inverted_safe() @ display_rotation).to_euler("XYZ"),
        )

    for plan in plans:
        lower = armature.pose.bones[plan.chain.lower]
        end = armature.pose.bones[plan.chain.end]
        target = armature.pose.bones[plan.target_name]
        pole = armature.pose.bones[plan.pole_name]
        chain_length = max((plan.joint - plan.start).length + (plan.end - plan.joint).length, 0.01)
        if schema == LEGACY_SCHEMA:
            target.custom_shape = widgets[plan.chain.kind]
            pole.custom_shape = widgets["POLE"]
            if hasattr(target, "custom_shape_wire_width"):
                target.custom_shape_wire_width = 2.0
                pole.custom_shape_wire_width = 2.0
            _write_control_visual_default(target)
            _write_control_visual_default(pole)
        elif _is_direct_preroll_schema(schema):
            display = (
                armature.pose.bones[plan.display_name]
                if _uses_dynamic_pole_display(schema)
                else None
            )
            set_shape(
                pole,
                widgets["POLE_ARROW"],
                chain_length * 0.11,
                transform=display,
            )
            pole.lock_rotation = (True, True, True)
            pole.lock_scale = (True, True, True)
            if display is not None:
                display.lock_location = display.lock_rotation = display.lock_scale = (
                    True,
                    True,
                    True,
                )
            if plan.chain.kind == "ARM":
                set_shape(
                    target,
                    widgets["ARM"],
                    chain_length * 0.24,
                    rotation=_hand_widget_rotation(target, lower),
                )
            else:
                set_foot_shape(plan, target, chain_length)
        else:
            line = armature.pose.bones[plan.line_name]
            display = armature.pose.bones[plan.display_name]
            set_shape(pole, widgets["POLE_ARROW"], chain_length * 0.11, transform=display)
            line.custom_shape = widgets["POLE_LINE"]
            line.use_custom_shape_bone_size = True
            line.custom_shape_scale_xyz = (1.0, 1.0, 1.0)
            if hasattr(line, "custom_shape_wire_width"):
                line.custom_shape_wire_width = 1.5
            pole.lock_rotation = (True, True, True)
            pole.lock_scale = (True, True, True)
            line.lock_location = line.lock_rotation = line.lock_scale = (True, True, True)
            display.lock_location = display.lock_rotation = display.lock_scale = (True, True, True)
            if plan.chain.kind == "ARM":
                set_shape(
                    target,
                    widgets["ARM"],
                    chain_length * 0.24,
                    rotation=_hand_widget_rotation(target, lower),
                )
            else:
                set_foot_shape(plan, target, chain_length)
                foot_length = max((Vector(armature.pose.bones[plan.chain.end].tail) - Vector(armature.pose.bones[plan.chain.end].head)).length, chain_length * 0.12)
                heel = armature.pose.bones[plan.heel_name]
                set_shape(heel, widgets["HEEL"], foot_length * 0.55)
                heel.rotation_mode = "XYZ"
                heel.lock_location = (True, True, True)
                heel.lock_scale = (True, True, True)
                heel.lock_rotation = (False, False, True)

        # Auto Align uses the live solved Forearm/Shin as the base orientation;
        # feature-v1 rigs can add the Target's local XYZ rotation afterward.
        # Use the evaluated end solely as the Custom Shape display frame so the
        # visible control follows its wrist/ankle normal without introducing a
        # Target -> solver -> end -> Target evaluation cycle.
        if plan.auto_align:
            _retarget_custom_shape_frame(target, end)

        (
            ik_owner,
            ik,
            copy_rotation,
            auto_offset_rotation,
            *extras,
        ) = runtime_constraints[plan.rig_id]
        live_ik_owner = armature.pose.bones.get(ik_owner.name)
        live_ik = live_ik_owner.constraints.get(ik.name) if live_ik_owner is not None else None
        if live_ik is None or live_ik.type != "IK":
            raise LimbIKError(f"{plan.chain.side} {plan.chain.kind.title()} IK disappeared while evaluating controls.")
        ik_owner, ik = live_ik_owner, live_ik
        ik_name = _constraint_name(plan.rig_id, "IK")
        for index, (owner, constraint) in enumerate(transaction["constraints"]):
            if constraint.name == ik_name:
                transaction["constraints"][index] = (ik_owner, ik)
                break
        if plan.preserve_pole_angle:
            # Transactional recovery must recreate the exact prior solver,
            # including pre-0.27 rigs that have no Pole Direction tag.
            ik.pole_angle = float(plan.pole_angle)
            context.view_layer.update()
        else:
            ik.pole_angle = _calibrate_pole_angle(context, armature, plan, ik)
        pending_records.append((ik_owner, ik, plan, "IK"))

        for owner, constraint, role in runtime_roll_constraints[plan.rig_id]:
            live_owner = armature.pose.bones.get(owner.name)
            live_constraint = live_owner.constraints.get(constraint.name) if live_owner is not None else None
            if live_constraint is None:
                raise LimbIKError(
                    f"{plan.chain.side} {plan.chain.kind.title()} roll-decoupling constraint '{role}' disappeared."
                )
            for index, (_old_owner, old_constraint) in enumerate(transaction["constraints"]):
                if old_constraint.name == live_constraint.name:
                    transaction["constraints"][index] = (live_owner, live_constraint)
                    break
            pending_records.append((live_owner, live_constraint, plan, role))

        copy_name = _constraint_name(plan.rig_id, "END_ROTATION")
        copy_rotation = end.constraints.get(copy_name)
        if copy_rotation is None or copy_rotation.type != "COPY_ROTATION":
            raise LimbIKError(
                f"{plan.chain.side} {plan.chain.kind.title()} end rotation disappeared while evaluating controls."
            )
        for index, (_owner, constraint) in enumerate(transaction["constraints"]):
            if constraint.name == copy_name:
                transaction["constraints"][index] = (end, copy_rotation)
                break
        context.view_layer.update()
        end_position_error = (Vector(end.head) - plan.desired_end_matrix.translation).length
        end_rotation_error = _rotation_error(end.matrix, plan.desired_end_matrix)
        # An intentional chain/Plane edit rebuilds a fresh plan
        # (preserve_pole_angle=False).  In persistent Auto mode the end is
        # supposed to inherit that newly solved limb orientation, so its
        # rotation may change even though the endpoint must remain fixed.
        # Unchanged-plan refreshes and every Manual build keep the strict
        # no-pop rotation check.
        allow_auto_replan_rotation = bool(plan.auto_align and not plan.preserve_pole_angle)
        if end_position_error > 2.0e-4 or (
            end_rotation_error > 2.0e-3 and not allow_auto_replan_rotation
        ):
            raise LimbIKError(
                f"{plan.chain.side} {plan.chain.kind.title()} end controller could not preserve "
                f"the current Hand/Foot pose (position {end_position_error:.4g}, rotation {end_rotation_error:.4g})."
            )
        pending_records.append((end, copy_rotation, plan, "END_ROTATION"))
        if target_rotation_version == TARGET_ROTATION_VERSION:
            offset_name = _constraint_name(plan.rig_id, "AUTO_OFFSET_ROTATION")
            auto_offset_rotation = end.constraints.get(offset_name)
            if (
                auto_offset_rotation is None
                or auto_offset_rotation.type != "COPY_ROTATION"
            ):
                raise LimbIKError(
                    f"{plan.chain.side} {plan.chain.kind.title()} Auto Target rotation disappeared while evaluating controls."
                )
            for index, (_owner, constraint) in enumerate(transaction["constraints"]):
                if constraint.name == offset_name:
                    transaction["constraints"][index] = (end, auto_offset_rotation)
                    break
            pending_records.append(
                (
                    end,
                    auto_offset_rotation,
                    plan,
                    "AUTO_OFFSET_ROTATION",
                )
            )
        if _is_enhanced_schema(schema):
            line = armature.pose.bones[plan.line_name]
            display = armature.pose.bones[plan.display_name]
            stretch = next(item for item in extras if item.type == "STRETCH_TO")
            pending_records.append((line, stretch, plan, "POLE_LINE_STRETCH"))
        if _uses_dynamic_pole_display(schema):
            display = armature.pose.bones[plan.display_name]
            track = next(item for item in extras if item.type == "DAMPED_TRACK")
            pending_records.append((display, track, plan, "POLE_DISPLAY_TRACK"))
        if _is_enhanced_schema(schema) and plan.chain.kind == "LEG":
            heel = armature.pose.bones[plan.heel_name]
            heel_limit = next(item for item in extras if item.type == "LIMIT_ROTATION")
            pending_records.append((heel, heel_limit, plan, "HEEL_LIMIT"))

    if decorate_sources and source_widget_chains:
        _decorate_source_widgets(
            context,
            armature,
            armature_id,
            source_widget_chains,
            widget_collection,
            transaction,
            schema=schema,
        )

    # PoseBone ID-property writes rebuild relation components on dense rigs.
    # Defer every registry write until all solver work and no-pop validation is
    # complete, then cross an Object boundary once before final inventory.
    for pose_bone, constraint, plan, role in pending_records:
        if role == "MASTER_FOLLOW":
            _record_master_constraint(pose_bone, constraint, armature_id)
        else:
            _record_constraint(pose_bone, constraint, plan, armature_id, role, schema=schema)
    _mode_set(context, armature, "OBJECT")
    armature.data.update_tag()
    armature.update_tag(refresh={"OBJECT"})
    context.scene.update_tag()
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()
    _mode_set(context, armature, "POSE")
    context.view_layer.update()


def _rotation_error(matrix, desired):
    angle = float(matrix.to_quaternion().rotation_difference(desired.to_quaternion()).angle)
    if not math.isfinite(angle):
        return float("inf")
    # q and -q encode the same 3D rotation.  Blender may report their
    # difference as 2*pi, so always fold the result to the shortest [0, pi]
    # representative before using it in validation or Pole calibration.
    angle = abs(angle) % math.tau
    return min(angle, math.tau - angle)


def _pole_alignment_measure(armature, plan):
    # Schema 3 calibrates the actual IK solver chain.  Reading the source chain
    # there would mix ORI/Copy-Rotation evaluation (and its near-180-degree
    # Damped-Track singularities) into an objective that should depend only on
    # the MCH joint and Pole.  Legacy schemas have no MCH pair and retain their
    # direct-source behavior.
    upper = armature.pose.bones.get(plan.mch_upper_name)
    lower = armature.pose.bones.get(plan.mch_lower_name)
    if upper is None or lower is None:
        upper = armature.pose.bones[plan.chain.upper]
        lower = armature.pose.bones[plan.chain.lower]
    pole = armature.pose.bones[plan.pole_name]
    start = Vector(upper.head)
    joint = Vector(lower.head)
    end = Vector(lower.tail)
    chain_axis = end - start
    if chain_axis.length <= EPSILON:
        return None
    projection = start + chain_axis * (joint - start).dot(chain_axis) / chain_axis.length_squared
    bend = joint - projection
    pole_projection = _project_perpendicular(Vector(pole.head) - projection, chain_axis)
    if min(bend.length, pole_projection.length) <= EPSILON:
        return None
    bend.normalize()
    pole_projection.normalize()
    return max(-1.0, min(1.0, bend.dot(pole_projection)))


def _pole_angle_domain(constraint):
    """Return the finite RNA hard range accepted by IK Pole Angle."""
    fallback = (-math.pi, math.pi)
    try:
        properties = constraint.bl_rna.properties
        try:
            prop = properties.get("pole_angle")
        except AttributeError:
            prop = properties["pole_angle"]
        lower = float(prop.hard_min)
        upper = float(prop.hard_max)
    except (AttributeError, KeyError, TypeError, ValueError):
        return fallback
    if not all(math.isfinite(value) for value in (lower, upper)) or lower >= upper:
        return fallback
    return lower, upper


def _calibrate_pole_angle(context, armature, plan, constraint):
    """Make Blender's evaluated joint bend toward the visible Pole control.

    Pole Angle still has to compensate arbitrary source-bone roll, but it must
    never compensate the control back to a different modeled bend plane.  The
    evaluated solver is therefore optimized for bend/Pole alignment itself.
    """
    lower_limit, upper_limit = _pole_angle_domain(constraint)

    def set_angle(angle):
        requested = max(lower_limit, min(upper_limit, float(angle)))
        constraint.pole_angle = requested
        actual = float(constraint.pole_angle)
        if not math.isfinite(actual):
            raise LimbIKError(f"{plan.chain.side} {plan.chain.kind.title()} Pole Angle became non-finite.")
        return actual

    analytic_angle = set_angle(plan.pole_angle)
    context.view_layer.update()
    analytic_alignment = _pole_alignment_measure(armature, plan)
    target = armature.pose.bones[plan.target_name]
    total = (plan.joint - plan.start).length + (plan.end - plan.joint).length
    target_reach = (Vector(target.head) - plan.start).length
    effectively_extended = total - target_reach <= max(EPSILON, total * 1.0e-6)
    if not effectively_extended and analytic_alignment is not None and analytic_alignment >= 0.999:
        return analytic_angle

    samples = 64
    step = (upper_limit - lower_limit) / samples
    best_angle = analytic_angle
    best_error = float("inf") if analytic_alignment is None else 1.0 - analytic_alignment
    target_basis = target.matrix_basis.copy()
    used_probe = False

    def evaluate(angle):
        actual = set_angle(angle)
        context.view_layer.update()
        alignment = _pole_alignment_measure(armature, plan)
        error = float("inf") if alignment is None else 1.0 - alignment
        return actual, error

    try:
        # A completely extended two-bone chain has no visible residual with
        # which Blender can expose its Pole convention.  Retract the target a
        # tiny amount only for calibration, then restore its exact Pose basis.
        set_angle(plan.pole_angle)
        context.view_layer.update()
        if effectively_extended or _pole_alignment_measure(armature, plan) is None:
            target_matrix = target.matrix.copy()
            toward_root = plan.start - Vector(target.head)
            if toward_root.length <= EPSILON or total <= EPSILON:
                raise LimbIKError(f"{plan.chain.side} {plan.chain.kind.title()} has no usable Pole calibration probe.")
            target_matrix.translation += toward_root.normalized() * total * 0.02
            target.matrix = target_matrix
            context.view_layer.update()
            used_probe = True
            # The objective is now measured on a deliberately different,
            # reachable pose.  Do not compare its candidates with an
            # alignment/error sampled before the temporary retraction.
            best_error = float("inf")

        # Pole Angle is a hard-clamped RNA property, not an unbounded periodic
        # scalar.  Always sample its complete legal interval.  A sweep centered
        # on the analytic seed can silently collapse out-of-range candidates to
        # one boundary and omit the opposite half of the valid domain.
        seen_angles = []
        for index in range(samples + 1):
            requested = lower_limit + index * step
            actual, error = evaluate(requested)
            if any(abs(actual - seen) <= 1.0e-12 for seen in seen_angles):
                continue
            seen_angles.append(actual)
            if error < best_error:
                best_angle, best_error = actual, error
        if not math.isfinite(best_error):
            raise LimbIKError(f"{plan.chain.side} {plan.chain.kind.title()} Pole direction could not be evaluated.")

        # Refine only inside the accepted RNA domain.  Keep the coarse result
        # unless an actually evaluated refinement is better.
        left = max(lower_limit, best_angle - step)
        right = min(upper_limit, best_angle + step)
        golden = (math.sqrt(5.0) - 1.0) * 0.5
        x1 = right - golden * (right - left)
        x2 = left + golden * (right - left)
        a1, f1 = evaluate(x1)
        a2, f2 = evaluate(x2)
        refined_angle, refined_error = best_angle, best_error
        if f1 < refined_error:
            refined_angle, refined_error = a1, f1
        if f2 < refined_error:
            refined_angle, refined_error = a2, f2
        for _index in range(22):
            if f1 <= f2:
                right, x2, a2, f2 = x2, x1, a1, f1
                x1 = right - golden * (right - left)
                a1, f1 = evaluate(x1)
                if f1 < refined_error:
                    refined_angle, refined_error = a1, f1
            else:
                left, x1, a1, f1 = x1, x2, a2, f2
                x2 = left + golden * (right - left)
                a2, f2 = evaluate(x2)
                if f2 < refined_error:
                    refined_angle, refined_error = a2, f2
        if refined_error < best_error:
            best_angle, best_error = refined_angle, refined_error
        set_angle(best_angle)
        calibrated_alignment = _pole_alignment_measure(armature, plan)
        if calibrated_alignment is None or calibrated_alignment < 0.999:
            alignment_label = (
                "undefined" if calibrated_alignment is None else f"{calibrated_alignment:.6f}"
            )
            raise LimbIKError(
                f"{plan.chain.side} {plan.chain.kind.title()} could not align its real joint bend to the "
                f"configured Pole direction (alignment {alignment_label}, analytic {analytic_alignment})."
            )
    finally:
        if used_probe:
            target.matrix_basis = target_basis
        context.view_layer.update()

    final_alignment = _pole_alignment_measure(armature, plan)
    # At exact full extension the restored chain has no meaningful bend plane;
    # tiny evaluated residuals can consequently point anywhere.  The temporary
    # probe was validated strictly above while its plane was measurable.
    if not used_probe and (final_alignment is None or final_alignment < 0.999):
        alignment_label = "undefined" if final_alignment is None else f"{final_alignment:.6f}"
        raise LimbIKError(
            f"{plan.chain.side} {plan.chain.kind.title()} could not align its real joint bend to the configured Pole "
            f"direction (alignment {alignment_label}, analytic {analytic_alignment})."
        )
    return float(constraint.pole_angle)


def _build_plans(
    context,
    armature,
    plans,
    *,
    schema=CURRENT_SCHEMA,
    desired_source_pose=None,
    decorate_source_widgets=True,
    foot_widget_kinds=None,
    target_rotation_version=None,
):
    inventory = _validate_inventory(armature)
    if target_rotation_version is None:
        requested_target_rotation_version = _default_target_rotation_version(schema)
    elif type(target_rotation_version) is int and target_rotation_version in {
        0,
        TARGET_ROTATION_VERSION,
    }:
        requested_target_rotation_version = target_rotation_version
    else:
        raise LimbIKError("The requested Target Rotation feature version is unsupported.")
    if inventory["rigs"] and (
        inventory["target_rotation_version"] != requested_target_rotation_version
    ):
        if target_rotation_version is None:
            raise LimbIKError(
                "This generated rig predates aligned Target rotation. Use Rebuild Rig once "
                "before adding another limb."
            )
        raise LimbIKError(
            "Cannot mix different Target Rotation feature versions on one Armature."
        )
    if inventory["rigs"]:
        # Incremental Build mutates the same ownership graph as Rebuild. Audit
        # every existing widget, collection, and assignment before the first
        # write so a damaged old side cannot be compounded by a new side.
        _removal_resources(context, armature, inventory)
    _preflight_plans(context, armature, plans, inventory, schema=schema)
    missing = [plan for plan in plans if (plan.chain.kind, plan.chain.side) not in inventory["rigs"]]
    if not missing:
        raise LimbIKError("The requested Limb IK rig already exists; use Rebuild Rig to replace it.")
    transaction = _new_transaction()
    armature_id = inventory["armature_id"]
    create_master = _is_enhanced_schema(schema) and inventory.get("master") is None
    source_widget_chains = tuple(
        [
            LimbChain(kind, side, *rig["chain"])
            for (kind, side), rig in inventory["rigs"].items()
            if kind == "ARM"
        ]
        + [plan.chain for plan in missing if plan.chain.kind == "ARM"]
    )
    source_pose_before = desired_source_pose or {
        bone.name: armature.pose.bones[bone.name].matrix.copy()
        for bone in armature.data.bones
        if bone.get(OWNER_KEY) != OWNER_VALUE
    }
    try:
        if not armature_id:
            armature_id = uuid.uuid4().hex
            armature.data[ARMATURE_ID_KEY] = armature_id
            transaction["new_armature_id"] = True
        if _stores_explicit_schema(schema) and armature.data.get(SCHEMA_KEY) != schema:
            armature.data[SCHEMA_KEY] = schema
            transaction["new_schema"] = True
        if not transaction["target_rotation_version_touched"]:
            transaction["target_rotation_version_touched"] = True
            transaction["target_rotation_version_present"] = (
                TARGET_ROTATION_VERSION_KEY in armature.data
            )
            transaction["target_rotation_version_before"] = armature.data.get(
                TARGET_ROTATION_VERSION_KEY,
                None,
            )
        if requested_target_rotation_version == TARGET_ROTATION_VERSION:
            armature.data[TARGET_ROTATION_VERSION_KEY] = TARGET_ROTATION_VERSION
        elif TARGET_ROTATION_VERSION_KEY in armature.data:
            del armature.data[TARGET_ROTATION_VERSION_KEY]
        if _is_direct_preroll_schema(schema):
            missing = _apply_direct_preroll(context, armature, missing, transaction)
        _create_constraint_shells(armature, armature_id, missing, transaction, schema=schema, create_master=create_master)
        _create_control_bones(context, armature, armature_id, missing, transaction, schema=schema, create_master=create_master)
        _create_constraints_and_shapes(
            context,
            armature,
            armature_id,
            missing,
            transaction,
            schema=schema,
            create_master=create_master,
            source_widget_chains=source_widget_chains,
            decorate_sources=decorate_source_widgets,
            foot_widget_kinds=foot_widget_kinds,
            target_rotation_version=requested_target_rotation_version,
        )
        verified = _validate_inventory(armature)
        if (
            verified["schema"] != schema
            or verified["target_rotation_version"]
            != requested_target_rotation_version
        ):
            raise LimbIKError("Generated Limb IK schema did not pass ownership verification.")
        for plan in missing:
            if (plan.chain.kind, plan.chain.side) not in verified["rigs"]:
                raise LimbIKError(f"Generated {plan.chain.side} {plan.chain.kind.title()} rig did not pass ownership verification.")
        context.view_layer.update()
        replanned_bones = {
            name
            for plan in missing
            if not plan.preserve_pole_angle
            for name in (plan.chain.upper, plan.chain.lower)
        }
        auto_replanned_ends = {
            plan.chain.end
            for plan in missing
            if plan.auto_align and not plan.preserve_pole_angle
        }
        for name, desired in source_pose_before.items():
            # An explicit Pole direction intentionally re-planes the two-bone
            # joint.  The Hand/Foot endpoint and every unrelated source bone
            # remain protected by the same strict validation as before.
            if name in replanned_bones:
                continue
            current = armature.pose.bones.get(name)
            if current is None:
                raise LimbIKError(f"Source bone '{name}' disappeared while building Limb IK.")
            position_error = (current.matrix.translation - desired.translation).length
            rotation_error = _rotation_error(current.matrix, desired)
            allow_auto_replan_rotation = name in auto_replanned_ends
            if position_error > 4.0e-4 or (
                rotation_error > 3.0e-3 and not allow_auto_replan_rotation
            ):
                raise LimbIKError(
                    f"Master/IK setup could not preserve source pose on '{name}' "
                    f"(position {position_error:.4g}, rotation {rotation_error:.4g})."
                )
        if _is_direct_preroll_schema(schema):
            for plan in missing:
                if plan.preserve_pole_angle:
                    continue
                for name in (plan.chain.upper, plan.chain.lower):
                    pose_matrix = armature.pose.bones[name].matrix
                    rest_matrix = armature.data.bones[name].matrix_local
                    position_error = (pose_matrix.translation - rest_matrix.translation).length
                    rotation_error = _rotation_error(pose_matrix, rest_matrix)
                    if position_error > 2.0e-5 or rotation_error > 2.0e-4:
                        raise LimbIKError(
                            f"Direct Pre-Roll could not establish a zero-twist start frame on '{name}' "
                            f"(position {position_error:.4g}, rotation {rotation_error:.4g})."
                        )
        return missing, transaction
    except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        rollback_errors = _rollback_build(context, armature, transaction)
        if rollback_errors:
            raise LimbIKError(f"{exc} Rollback also reported: {'; '.join(rollback_errors)}") from exc
        raise


def _plans_for_kind(context, armature, settings, kind):
    chains = []
    for side in SIDES:
        chain = _chain_from_settings(settings, armature, kind, side)
        if chain is not None:
            chains.append(chain)
    if not chains:
        raise LimbIKError(f"No complete high-confidence {kind.title()} chain is available; Analyze or choose bones manually.")
    _mode_set(context, armature, "POSE")
    return [
        _build_plan(
            armature,
            chain,
            settings.pole_distance_ratio,
            _configured_pole_direction(settings, chain.kind, chain.side),
        )
        for chain in chains
    ]


def _plans_for_selected(context, armature, settings):
    kind, side = SELECTED_LIMBS.get(settings.selected_limb, ("", ""))
    if kind not in KINDS or side not in SIDES:
        raise LimbIKError("Choose one Arm or Leg before building IK.")
    chain = _chain_from_settings(settings, armature, kind, side)
    if chain is None:
        raise LimbIKError(
            f"No complete {side} {kind.title()} chain is available; Analyze or choose all three bones manually."
        )
    _mode_set(context, armature, "POSE")
    return [
        _build_plan(
            armature,
            chain,
            settings.pole_distance_ratio,
            _configured_pole_direction(settings, chain.kind, chain.side),
        )
    ]


def _plans_for_all(context, armature, settings):
    """Plan every populated limb before the one atomic Build All mutation."""
    chains = []
    for kind in KINDS:
        for side in SIDES:
            # Fully blank, low-confidence chains remain intentionally absent.
            # A partly filled chain raises here, before _build_plans can mutate
            # any Armature data, so Build All always fails closed.
            chain = _chain_from_settings(settings, armature, kind, side)
            if chain is not None:
                chains.append(chain)
    if not chains:
        raise LimbIKError("No complete Arm or Leg chain is available; Analyze or choose bones manually.")
    _mode_set(context, armature, "POSE")
    return [
        _build_plan(
            armature,
            chain,
            settings.pole_distance_ratio,
            _configured_pole_direction(settings, chain.kind, chain.side),
        )
        for chain in chains
    ]


def _plans_for_exact_keys(context, armature, settings, keys):
    """Plan exactly the generated sides requested by Rebuild, never siblings."""
    chains = []
    for kind, side in keys:
        chain = _chain_from_settings(settings, armature, kind, side)
        if chain is None:
            raise LimbIKError(
                f"No complete {side} {kind.title()} chain is available for Rebuild; Analyze or choose all three bones."
            )
        chains.append(chain)
    if not chains:
        raise LimbIKError("No generated Limb IK side is available for Rebuild.")
    _mode_set(context, armature, "POSE")
    return [
        _build_plan(
            armature,
            chain,
            settings.pole_distance_ratio,
            _configured_pole_direction(settings, chain.kind, chain.side),
        )
        for chain in chains
    ]


def _validate_master_build_scale(scale):
    values = tuple(float(value) for value in scale)
    if len(values) != 3 or any(not math.isfinite(value) for value in values):
        raise LimbIKError("Master scale must be finite before adding or rebuilding Limb IK controls.")
    if any(value <= 1.0e-6 for value in values):
        raise LimbIKError("Use a positive, non-zero Master scale before adding or rebuilding Limb IK controls.")
    tolerance = 1.0e-5 * max(1.0, max(abs(value) for value in values))
    if max(values) - min(values) > tolerance:
        raise LimbIKError("Use uniform Master scale before adding or rebuilding Limb IK controls.")


def _neutralize_existing_master(context, armature, *, validate_scale=True):
    """Temporarily remove global transport while deriving new rest controls."""
    inventory = _validate_inventory(armature)
    if not _is_enhanced_schema(inventory.get("schema", 0)) or inventory.get("master") is None:
        return None
    pose_bone = armature.pose.bones[MASTER_NAME]
    if validate_scale:
        _validate_master_build_scale(pose_bone.scale)
    state = {"rotation_mode": pose_bone.rotation_mode, "matrix_basis": pose_bone.matrix_basis.copy()}
    pose_bone.matrix_basis = Matrix.Identity(4)
    context.view_layer.update()
    return state


def _restore_master_state(context, armature, state):
    if state is None:
        return
    pose_bone = armature.pose.bones.get(MASTER_NAME)
    if pose_bone is None:
        raise LimbIKError("The Master control disappeared while restoring its pose.")
    pose_bone.rotation_mode = state["rotation_mode"]
    pose_bone.matrix_basis = state["matrix_basis"]
    context.view_layer.update()


def _actions_for_id(id_block):
    animation = getattr(id_block, "animation_data", None)
    if animation is None:
        return ()
    actions = []
    if animation.action is not None:
        actions.append(animation.action)
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.action is not None:
                actions.append(strip.action)
    return tuple(dict.fromkeys(actions))


def _fcurves_for_action(action):
    if hasattr(action, "fcurves"):
        return tuple(action.fcurves)
    # Blender 4.4+ layered Actions expose channelbags instead of action.fcurves.
    result = []
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for channelbag in getattr(strip, "channelbags", ()):
                result.extend(channelbag.fcurves)
    return tuple(result)


def _path_mentions_bone(data_path, names):
    return any(f'pose.bones["{name.replace(chr(34), chr(92) + chr(34))}"]' in data_path for name in names)


def _rna_escape(name):
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _owned_constraint_path(pose_bone_name, constraint_name):
    return f'pose.bones["{_rna_escape(pose_bone_name)}"].constraints["{_rna_escape(constraint_name)}"]'


def _constraint_references_controls(constraint, armature, names):
    if getattr(constraint, "target", None) is armature and getattr(constraint, "subtarget", "") in names:
        return True
    if getattr(constraint, "pole_target", None) is armature and getattr(constraint, "pole_subtarget", "") in names:
        return True
    for target in getattr(constraint, "targets", ()):
        if getattr(target, "target", None) is armature and getattr(target, "subtarget", "") in names:
            return True
    return False


def _direct_source_dependency_problems(armature, chains, *, owned_constraints=()):
    """Find data that would be reinterpreted by changing Direct source Rest axes."""
    chains = tuple(tuple(chain) for chain in chains)
    rest_names = {name for chain in chains for name in chain[:2]}
    source_names = {name for chain in chains for name in chain}
    animated_names = source_names
    if not rest_names:
        return []
    allowed_children = {
        pair
        for chain in chains
        for pair in ((chain[0], chain[1]), (chain[1], chain[2]))
    }
    owned_constraints = set(owned_constraints)
    problems = []

    for bone in armature.data.bones:
        if (
            bone.parent is not None
            and bone.parent.name in rest_names
            and (bone.parent.name, bone.name) not in allowed_children
        ):
            problems.append(
                f"bone '{bone.name}' is parented to Direct Pre-Roll source bone '{bone.parent.name}'"
            )

    for rig_object in (obj for obj in bpy.data.objects if obj.type == "ARMATURE" and obj.pose is not None):
        for pose_bone in rig_object.pose.bones:
            for constraint in pose_bone.constraints:
                pointer_key = (
                    rig_object.as_pointer(),
                    pose_bone.as_pointer(),
                    constraint.as_pointer(),
                )
                if pointer_key in owned_constraints:
                    continue
                if rig_object is armature and pose_bone.name in source_names:
                    problems.append(
                        f"foreign constraint '{constraint.name}' edits Direct Pre-Roll source bone "
                        f"'{pose_bone.name}'"
                    )
                if _constraint_references_controls(constraint, armature, rest_names):
                    problems.append(
                        f"foreign constraint '{constraint.name}' on '{rig_object.name}:{pose_bone.name}' "
                        "references a Direct Pre-Roll source bone"
                    )
            transform = pose_bone.custom_shape_transform
            if rig_object is armature and transform is not None and transform.name in rest_names:
                problems.append(
                    f"bone '{pose_bone.name}' uses Direct Pre-Roll source bone '{transform.name}' "
                    "as Custom Shape Transform"
                )

    for obj in bpy.data.objects:
        if obj is not armature and obj.parent is armature and obj.parent_type == "BONE" and obj.parent_bone in rest_names:
            problems.append(
                f"Object '{obj.name}' is bone-parented to Direct Pre-Roll source bone '{obj.parent_bone}'"
            )
        for constraint in obj.constraints:
            if _constraint_references_controls(constraint, armature, rest_names):
                problems.append(
                    f"foreign Object constraint '{constraint.name}' on '{obj.name}' references a Direct Pre-Roll source bone"
                )
        for modifier in obj.modifiers:
            for object_field, bone_field in (
                ("object", "subtarget"),
                ("object_from", "bone_from"),
                ("object_to", "bone_to"),
            ):
                if (
                    getattr(modifier, object_field, None) is armature
                    and getattr(modifier, bone_field, "") in rest_names
                ):
                    problems.append(
                        f"modifier '{modifier.name}' on '{obj.name}' references Direct Pre-Roll source bone "
                        f"'{getattr(modifier, bone_field)}'"
                    )

    for id_block in (armature, armature.data):
        for action in _actions_for_id(id_block):
            if any(_path_mentions_bone(fcurve.data_path, animated_names) for fcurve in _fcurves_for_action(action)):
                problems.append(f"Action '{action.name}' animates a Direct Pre-Roll source chain")
        animation = getattr(id_block, "animation_data", None)
        for fcurve in getattr(animation, "drivers", ()) if animation else ():
            if _path_mentions_bone(fcurve.data_path, animated_names):
                problems.append("a driver writes to a Direct Pre-Roll source chain")
            for variable in fcurve.driver.variables:
                for target in variable.targets:
                    target_path = getattr(target, "data_path", "")
                    if target.id is armature and (
                        getattr(target, "bone_target", "") in rest_names
                        or _path_mentions_bone(target_path, rest_names)
                    ):
                        problems.append("a driver reads a Direct Pre-Roll source bone")

    for obj in bpy.data.objects:
        animation = obj.animation_data
        for fcurve in getattr(animation, "drivers", ()) if animation else ():
            for variable in fcurve.driver.variables:
                for target in variable.targets:
                    target_path = getattr(target, "data_path", "")
                    if target.id is armature and (
                        getattr(target, "bone_target", "") in rest_names
                        or _path_mentions_bone(target_path, rest_names)
                    ):
                        problems.append(
                            f"driver on '{obj.name}' reads a Direct Pre-Roll source bone"
                        )
    return sorted(set(problems))


def _foreign_dependency_problems(armature, inventory):
    names = {bone.name for bone in inventory["bones"]}
    if not names:
        return []
    owned_constraints = {(armature.as_pointer(), pose_bone.as_pointer(), constraint.as_pointer()) for pose_bone, constraint, _record in inventory["records"]}
    owned_constraint_paths = {
        _owned_constraint_path(pose_bone.name, constraint.name)
        for pose_bone, constraint, _record in inventory["records"]
    }
    problems = []
    allowed_source_widgets = set(
        (inventory.get("source_widgets") or {}).get("bones", {})
    )
    if _is_direct_preroll_schema(inventory["schema"]):
        problems.extend(
            _direct_source_dependency_problems(
                armature,
                (rig["chain"] for rig in inventory["rigs"].values()),
                owned_constraints=owned_constraints,
            )
        )
    for bone in armature.data.bones:
        if bone.name not in names and bone.parent is not None and bone.parent.name in names:
            problems.append(f"bone '{bone.name}' is parented to generated '{bone.parent.name}'")
    for rig_object in (obj for obj in bpy.data.objects if obj.type == "ARMATURE" and obj.pose is not None):
        for pose_bone in rig_object.pose.bones:
            for constraint in pose_bone.constraints:
                if (rig_object.as_pointer(), pose_bone.as_pointer(), constraint.as_pointer()) in owned_constraints:
                    continue
                references = _constraint_references_controls(constraint, armature, names)
                if references or (rig_object is armature and pose_bone.name in names):
                    problems.append(f"foreign constraint '{constraint.name}' on '{rig_object.name}:{pose_bone.name}' references/edits generated controls")
            allowed_source_assignment = (
                rig_object is armature and pose_bone.name in allowed_source_widgets
            )
            if (
                not (rig_object is armature and pose_bone.name in names)
                and not allowed_source_assignment
                and pose_bone.custom_shape is not None
                and _owned(pose_bone.custom_shape, inventory["armature_id"], role="WIDGET")
            ):
                problems.append(f"bone '{rig_object.name}:{pose_bone.name}' uses an owned widget as a foreign Custom Shape")
            if (
                rig_object is armature
                and pose_bone.name not in names
                and pose_bone.custom_shape_transform is not None
                and pose_bone.custom_shape_transform.name in names
            ):
                problems.append(f"bone '{rig_object.name}:{pose_bone.name}' uses a generated control as Custom Shape Transform")
    for obj in bpy.data.objects:
        if obj is not armature and obj.parent is armature and obj.parent_type == "BONE" and obj.parent_bone in names:
            problems.append(f"Object '{obj.name}' is bone-parented to generated control '{obj.parent_bone}'")
        for constraint in obj.constraints:
            if _constraint_references_controls(constraint, armature, names):
                problems.append(f"foreign Object constraint '{constraint.name}' on '{obj.name}' references a generated control")
    for id_block in (armature, armature.data):
        for action in _actions_for_id(id_block):
            for fcurve in _fcurves_for_action(action):
                if _path_mentions_bone(fcurve.data_path, names):
                    problems.append(f"Action '{action.name}' animates a generated control")
                    break
                if any(fcurve.data_path.startswith(path) for path in owned_constraint_paths):
                    problems.append(f"Action '{action.name}' animates an owned Limb IK constraint")
                    break
        animation = getattr(id_block, "animation_data", None)
        for fcurve in getattr(animation, "drivers", ()) if animation else ():
            if _path_mentions_bone(fcurve.data_path, names):
                problems.append("a driver writes to a generated control")
            if any(fcurve.data_path.startswith(path) for path in owned_constraint_paths):
                problems.append("a driver writes to an owned Limb IK constraint")
            for variable in fcurve.driver.variables:
                for target in variable.targets:
                    target_path = getattr(target, "data_path", "")
                    if target.id is armature and (
                        getattr(target, "bone_target", "") in names
                        or _path_mentions_bone(target_path, names)
                        or any(target_path.startswith(path) for path in owned_constraint_paths)
                    ):
                        problems.append("a driver reads a generated control")
    for obj in bpy.data.objects:
        animation = obj.animation_data
        for fcurve in getattr(animation, "drivers", ()) if animation else ():
            for variable in fcurve.driver.variables:
                for target in variable.targets:
                    target_path = getattr(target, "data_path", "")
                    if target.id is armature and (
                        getattr(target, "bone_target", "") in names
                        or _path_mentions_bone(target_path, names)
                        or any(target_path.startswith(path) for path in owned_constraint_paths)
                    ):
                        problems.append(f"driver on '{obj.name}' reads a generated control")
    control_collection = next((collection for collection in armature.data.collections if _owned(collection, inventory["armature_id"], role="CONTROL_COLLECTION")), None)
    if control_collection is not None:
        foreign_members = [bone.name for bone in control_collection.bones if bone.name not in names]
        if foreign_members:
            problems.append(f"Randy Controls contains foreign bones: {', '.join(foreign_members[:3])}")
    return sorted(set(problems))


def _removal_resources(context, armature, inventory):
    """Validate every deletion target before the first destructive operation."""
    armature_id = inventory["armature_id"]
    control_collections = [collection for collection in armature.data.collections if _owned(collection, armature_id, role="CONTROL_COLLECTION")]
    if len(control_collections) != 1:
        raise LimbIKError("Owned Randy Controls bone collection is missing or duplicated.")
    expected_bones = {bone.name for bone in inventory["bones"]}
    if {bone.name for bone in control_collections[0].bones} != expected_bones:
        raise LimbIKError("Randy Controls contains missing or foreign bones.")

    widget_collections = [collection for collection in bpy.data.collections if _owned(collection, armature_id, role="WIDGET_COLLECTION")]
    if len(widget_collections) != 1:
        raise LimbIKError("Owned widget collection is missing or duplicated.")
    widget_collection = widget_collections[0]
    if _collection_scene_memberships(widget_collection) != (context.scene,):
        raise LimbIKError("Owned widget collection is linked outside the current Scene.")
    if tuple(widget_collection.children):
        raise LimbIKError("Owned widget collection contains foreign child collections.")
    widget_objects = [obj for obj in bpy.data.objects if _owned(obj, armature_id, role="WIDGET")]
    if set(widget_collection.objects) != set(widget_objects):
        raise LimbIKError("Owned widget collection contains missing or foreign objects.")
    actual_kinds = {obj.get(KIND_KEY) for obj in widget_objects}
    if len(actual_kinds) != len(widget_objects):
        raise LimbIKError("Owned widget kinds are missing or duplicated.")
    if inventory["schema"] == LEGACY_SCHEMA:
        expected_kinds = {"POLE"}
    elif _is_direct_preroll_schema(inventory["schema"]):
        expected_kinds = {"POLE_ARROW"}
    else:
        expected_kinds = {"POLE_ARROW", "POLE_LINE", "MASTER"}
    if any(kind == "ARM" for kind, _side in inventory["rigs"]):
        expected_kinds.add("HAND")
    direct_foot_widgets = {}
    if any(kind == "LEG" for kind, _side in inventory["rigs"]):
        if _is_direct_preroll_schema(inventory["schema"]):
            for key, rig in inventory["rigs"].items():
                kind, side = key
                if kind != "LEG":
                    continue
                target_pose = armature.pose.bones[rig["target"].name]
                if _control_shape_style(target_pose.bone) == "DEFAULT":
                    shape = target_pose.custom_shape
                    shape_kind = (
                        shape.get(KIND_KEY)
                        if _owned(shape, armature_id, role="WIDGET")
                        else None
                    )
                    if shape_kind not in {"FOOT", f"FOOT_{side}"}:
                        raise LimbIKError(
                            f"Generated {side} Leg Foot Custom Shape ownership is invalid."
                        )
                else:
                    shape_kind = (
                        f"FOOT_{side}"
                        if f"FOOT_{side}" in actual_kinds
                        else "FOOT"
                        if "FOOT" in actual_kinds
                        else None
                    )
                    if shape_kind is None:
                        raise LimbIKError(
                            f"Generated {side} Leg has no canonical Foot Custom Shape."
                        )
                direct_foot_widgets[key] = shape_kind
                expected_kinds.add(shape_kind)
        else:
            expected_kinds.add("FOOT")
        if _is_enhanced_schema(inventory["schema"]):
            expected_kinds.add("HEEL")
    source_widgets = inventory.get("source_widgets")
    if source_widgets is not None:
        expected_kinds.update(record["kind"] for record in source_widgets["bones"].values())
    # Alternate control shapes are optional managed resources.  They may stay
    # cached after the last user returns to Rig Default; Remove/Rebuild owns
    # and cleans them just like the baseline widgets.
    expected_kinds.update(actual_kinds & CONTROL_SHAPE_WIDGET_KINDS)
    if actual_kinds != expected_kinds:
        raise LimbIKError("Owned widget object set does not match the generated Limb rigs.")
    widget_meshes = []
    for obj in widget_objects:
        if tuple(obj.users_collection) != (widget_collection,) or obj.type != "MESH":
            raise LimbIKError(f"Owned widget '{obj.name}' has foreign links or changed type.")
        kind = obj.get(KIND_KEY)
        canonical_name = WIDGET_NAMES.get(kind)
        if canonical_name is None or obj.name != canonical_name:
            raise LimbIKError(
                f"Owned {str(kind).title()} widget was renamed; restore "
                f"'{canonical_name or kind}' before Build, Rebuild, or Remove."
            )
        mesh = obj.data
        if not _owned(mesh, armature_id, role="WIDGET_DATA") or mesh.get(KIND_KEY) != obj.get(KIND_KEY) or mesh.users != 1:
            raise LimbIKError(f"Owned widget data '{mesh.name}' has invalid ownership or foreign users.")
        if mesh.name != canonical_name:
            raise LimbIKError(
                f"Owned {str(kind).title()} widget mesh was renamed; restore "
                f"'{canonical_name}' before Build, Rebuild, or Remove."
            )
        widget_meshes.append(mesh)
    widget_by_kind = {obj.get(KIND_KEY): obj for obj in widget_objects}
    for control in _control_pose_bones(armature, inventory):
        expected_kind = _expected_control_shape_kind(
            inventory,
            control.bone,
            actual_kinds,
        )
        if control.custom_shape is not widget_by_kind.get(expected_kind):
            raise LimbIKError(
                f"Generated control '{control.name}' Custom Shape assignment was edited."
            )
    for (kind, side), rig in inventory["rigs"].items():
        target_pose = armature.pose.bones[rig["target"].name]
        pole_pose = armature.pose.bones[rig["pole"].name]
        target_transform = target_pose.custom_shape_transform
        expected_end = armature.pose.bones[rig["chain"][2]]
        if rig["auto_align"]:
            if (
                target_transform is None
                or target_transform.name != expected_end.name
            ) and not (
                target_transform is None
                and inventory["schema"] != DIRECT_PREROLL_SCHEMA
            ):
                raise LimbIKError("Generated Auto Align display assignment was edited.")
        elif target_transform is not None:
            raise LimbIKError("Generated manual Target display assignment was edited.")
        if _is_enhanced_schema(inventory["schema"]):
            transform = pole_pose.custom_shape_transform
            if armature.pose.bones[rig["line"].name].custom_shape is not widget_by_kind["POLE_LINE"] or transform is None or transform.name != rig["display"].name:
                raise LimbIKError(f"Generated {kind.title()} Pole display assignment was edited.")
        elif _uses_dynamic_pole_display(inventory["schema"]):
            transform = pole_pose.custom_shape_transform
            if transform is None or transform.name != rig["display"].name:
                raise LimbIKError(f"Generated {kind.title()} Pole display assignment was edited.")
    return {
        "control_collection": control_collections[0],
        "widget_collection": widget_collection,
        "widget_objects": tuple(widget_objects),
        "widget_meshes": tuple(widget_meshes),
    }


def _snapshot_owned_rig(armature, inventory):
    plans = []
    control_pose = {}
    foot_widget_kinds = {}
    available_widget_kinds = {
        obj.get(KIND_KEY)
        for obj in bpy.data.objects
        if _owned(obj, inventory["armature_id"], role="WIDGET")
    }
    for (kind, side), rig in sorted(inventory["rigs"].items()):
        target = rig["target"]
        pole = rig["pole"]
        upper = armature.pose.bones[rig["chain"][0]]
        lower = armature.pose.bones[rig["chain"][1]]
        end_bone = armature.pose.bones[rig["chain"][2]]
        ik = next(constraint for _pose_bone, constraint, record in rig["entries"] if record["role"] == "IK")
        plans.append(
            LimbPlan(
                chain=LimbChain(kind, side, *rig["chain"]),
                rig_id=rig["rig_id"],
                target_name=target.name,
                pole_name=pole.name,
                start=Vector(upper.head),
                joint=Vector(lower.head),
                end=Vector(lower.tail),
                target_head=Vector(target.head_local),
                target_tail=Vector(target.tail_local),
                target_z=Vector(target.matrix_local.col[2][:3]),
                pole_head=Vector(pole.head_local),
                pole_tail=Vector(pole.tail_local),
                pole_angle=float(ik.pole_angle),
                pole_warning="",
                pole_direction=(
                    rig["pole_direction"].copy()
                    if rig["pole_direction"] is not None
                    else Vector(DEFAULT_POLE_DIRECTIONS[(kind, side)])
                ),
                pole_joint=Vector(lower.head),
                desired_upper_matrix=upper.matrix.copy(),
                desired_lower_matrix=lower.matrix.copy(),
                desired_end_matrix=end_bone.matrix.copy(),
                target_basis=armature.pose.bones[target.name].matrix_basis.copy(),
                pole_basis=armature.pose.bones[pole.name].matrix_basis.copy(),
                line_name=rig["line"].name if rig["line"] is not None else "",
                display_name=rig["display"].name if rig["display"] is not None else "",
                heel_name=rig["heel"].name if rig["heel"] is not None else "",
                solver_target_name=rig["solver_target"].name,
                mch_upper_name=rig["mch_upper"].name if rig["mch_upper"] is not None else "",
                mch_lower_name=rig["mch_lower"].name if rig["mch_lower"] is not None else "",
                ori_upper_name=rig["ori_upper"].name if rig["ori_upper"] is not None else "",
                ori_lower_name=rig["ori_lower"].name if rig["ori_lower"] is not None else "",
                mechanism_rest=(
                    {
                        role: {
                            "head": Vector(bone.head_local),
                            "tail": Vector(bone.tail_local),
                            "z": Vector(bone.matrix_local.col[2][:3]),
                        }
                        for role, bone in (
                            ("mch_upper", rig["mch_upper"]),
                            ("mch_lower", rig["mch_lower"]),
                            ("ori_upper", rig["ori_upper"]),
                            ("ori_lower", rig["ori_lower"]),
                        )
                    }
                    if _is_roll_decoupled_schema(inventory["schema"])
                    else None
                ),
                mechanism_pose=(
                    {
                        role: {
                            "matrix_basis": armature.pose.bones[bone.name].matrix_basis.copy(),
                            "rotation_mode": armature.pose.bones[bone.name].rotation_mode,
                        }
                        for role, bone in (
                            ("mch_upper", rig["mch_upper"]),
                            ("mch_lower", rig["mch_lower"]),
                            ("ori_upper", rig["ori_upper"]),
                            ("ori_lower", rig["ori_lower"]),
                        )
                    }
                    if _is_roll_decoupled_schema(inventory["schema"])
                    else None
                ),
                direct_rest=(
                    json.loads(_canonical_json(rig["direct_rest"]))
                    if _is_direct_preroll_schema(inventory["schema"])
                    and rig["direct_rest"] is not None
                    else None
                ),
                heel_head=Vector(rig["heel"].head_local) if rig["heel"] is not None else None,
                heel_tail=Vector(rig["heel"].tail_local) if rig["heel"] is not None else None,
                heel_z=Vector(rig["heel"].matrix_local.col[2][:3]) if rig["heel"] is not None else None,
                line_head=Vector(rig["line"].head_local) if rig["line"] is not None else None,
                line_tail=Vector(rig["line"].tail_local) if rig["line"] is not None else None,
                configured_pole_direction=(
                    rig["configured_pole_direction"].copy()
                    if (
                        _is_roll_decoupled_schema(inventory["schema"])
                        or _is_direct_preroll_schema(inventory["schema"])
                    )
                    and rig["configured_pole_direction"] is not None
                    else None
                ),
                persist_pole_direction=rig["pole_direction"] is not None,
                preserve_pole_angle=True,
                auto_align=rig["auto_align"],
            )
        )
        if kind == "LEG":
            foot_widget_kinds[(kind, side)] = _default_control_shape_kind(
                inventory,
                armature.data.bones[target.name],
                available_widget_kinds,
            )
    for bone in inventory["bones"]:
        pose_bone = armature.pose.bones[bone.name]
        control_pose[bone.name] = {
            "matrix_basis": pose_bone.matrix_basis.copy(),
            "rotation_mode": pose_bone.rotation_mode,
            "shape": _pose_shape_json_state(pose_bone),
            "visual_default_present": CONTROL_VISUAL_DEFAULT_KEY in bone,
            "visual_default_raw": bone.get(CONTROL_VISUAL_DEFAULT_KEY, None),
            "shape_style_present": CONTROL_SHAPE_STYLE_KEY in bone,
            "shape_style_raw": bone.get(CONTROL_SHAPE_STYLE_KEY, None),
            "auto_align_present": AUTO_ALIGN_KEY in bone,
            "auto_align_value": bone.get(AUTO_ALIGN_KEY, None),
        }
    widget_geometry = {}
    for obj in bpy.data.objects:
        if _owned(obj, inventory["armature_id"], role="WIDGET"):
            widget_geometry[obj.get(KIND_KEY)] = {
                "vertices": tuple(tuple(vertex.co) for vertex in obj.data.vertices),
                "edges": tuple(tuple(edge.vertices) for edge in obj.data.edges),
            }
    return {
        "armature_id": inventory["armature_id"],
        "schema": inventory["schema"],
        "target_rotation_version": inventory["target_rotation_version"],
        "plans": plans,
        "control_pose": control_pose,
        "widget_geometry": widget_geometry,
        "foot_widget_kinds": foot_widget_kinds,
        "constraint_names": {constraint.name for _pose_bone, constraint, _record in inventory["records"]},
        "direct_rest_raw": armature.data.get(DIRECT_REST_KEY, None),
        "source_widgets_raw": armature.data.get(SOURCE_WIDGETS_KEY, None),
        "source_pose": {
            bone.name: armature.pose.bones[bone.name].matrix.copy()
            for bone in armature.data.bones
            if bone.get(OWNER_KEY) != OWNER_VALUE
        },
    }


def _reapply_control_visual_overrides(armature, control_pose):
    """Carry display-only artist offsets across a successful Rebuild.

    A saved generated default lets a newer build refresh its fitted widget and
    still preserve the artist's translation/rotation/scale delta.  Pre-feature
    rigs have no baseline, so their exact live vectors are retained once.
    """

    for name, saved in control_pose.items():
        pose_bone = armature.pose.bones.get(name)
        bone = armature.data.bones.get(name)
        if (
            pose_bone is None
            or bone is None
            or bone.get(ROLE_KEY) not in CONTROL_VISUAL_ROLES
            or pose_bone.custom_shape is None
        ):
            continue
        shape_state = saved.get("shape")
        if not isinstance(shape_state, dict):
            continue
        old_scale = _shape_vector(shape_state, "scale", f"Saved control '{name}'")
        old_translation = _shape_vector(shape_state, "translation", f"Saved control '{name}'")
        old_rotation = _shape_vector(shape_state, "rotation", f"Saved control '{name}'")
        old_default_raw = saved.get("visual_default_raw") if saved.get("visual_default_present") else None
        new_default_raw = bone.get(CONTROL_VISUAL_DEFAULT_KEY, None)
        if old_default_raw is None or new_default_raw is None:
            pose_bone.custom_shape_scale_xyz = old_scale
            pose_bone.custom_shape_translation = old_translation
            pose_bone.custom_shape_rotation_euler = old_rotation
            continue
        old_default = _parse_control_visual_state(old_default_raw, f"Saved control '{name}'")
        new_default = _parse_control_visual_state(new_default_raw, f"Rebuilt control '{name}'")
        scale = []
        for current, old_base, new_base in zip(old_scale, old_default["scale"], new_default["scale"]):
            if abs(old_base) <= EPSILON:
                scale.append(new_base + (current - old_base))
            else:
                scale.append(new_base * (current / old_base))
        translation = [
            new_base + (current - old_base)
            for current, old_base, new_base in zip(
                old_translation,
                old_default["translation"],
                new_default["translation"],
            )
        ]
        old_default_rotation = Euler(tuple(old_default["rotation"]), "XYZ")
        old_current_rotation = Euler(tuple(old_rotation), "XYZ")
        new_default_rotation = Euler(tuple(new_default["rotation"]), "XYZ")
        local_delta = (
            old_default_rotation.to_quaternion().inverted()
            @ old_current_rotation.to_quaternion()
        )
        rebuilt_rotation = new_default_rotation.to_quaternion() @ local_delta
        compatibility = Euler(
            tuple(
                new_base + math.remainder(current - old_base, math.tau)
                for current, old_base, new_base in zip(
                    old_rotation,
                    old_default["rotation"],
                    new_default["rotation"],
                )
            ),
            "XYZ",
        )
        rotation = [
            float(value)
            for value in rebuilt_rotation.to_euler("XYZ", compatibility)
        ]
        if any(not math.isfinite(value) for value in (*scale, *translation, *rotation)):
            raise LimbIKError(f"Saved visual override for control '{name}' is not finite.")
        pose_bone.custom_shape_scale_xyz = scale
        pose_bone.custom_shape_translation = translation
        pose_bone.custom_shape_rotation_euler = rotation


def _saved_control_shape_style(saved, label):
    if not saved.get("shape_style_present"):
        return "DEFAULT"
    raw = saved.get("shape_style_raw")
    if not isinstance(raw, str) or raw not in CONTROL_SHAPE_STYLES - {"DEFAULT"}:
        raise LimbIKError(f"{label} has an invalid saved Custom Shape style.")
    return raw


def _reapply_control_shape_styles(
    context,
    armature,
    control_pose,
    widget_geometry,
    transaction,
):
    """Restore managed shape choices and optional widgets after Rebuild."""

    inventory = _validate_inventory(armature)
    armature_id = inventory["armature_id"]
    widget_collection = _ensure_widget_collection(
        context,
        armature_id,
        transaction,
    )
    requested_kinds = set(widget_geometry) & CONTROL_SHAPE_WIDGET_KINDS
    for pose_bone in _control_pose_bones(armature, inventory):
        saved = control_pose.get(pose_bone.name, {})
        style = _saved_control_shape_style(
            saved,
            f"Saved control '{pose_bone.name}'",
        )
        if style == "SPHERE":
            requested_kinds.add("CONTROL_SPHERE")
        elif style == "ARROW" and not (
            pose_bone.bone.get(ROLE_KEY) == "POLE"
            and inventory["schema"] != LEGACY_SCHEMA
        ):
            requested_kinds.add("CONTROL_ARROW")
    for kind in sorted(requested_kinds):
        _ensure_widget(
            context,
            armature_id,
            widget_collection,
            kind,
            transaction,
            schema=inventory["schema"],
        )
    widget_by_kind = {
        obj.get(KIND_KEY): obj
        for obj in bpy.data.objects
        if _owned(obj, armature_id, role="WIDGET")
    }
    available_kinds = set(widget_by_kind)
    for pose_bone in _control_pose_bones(armature, inventory):
        saved = control_pose.get(pose_bone.name, {})
        style = _saved_control_shape_style(
            saved,
            f"Saved control '{pose_bone.name}'",
        )
        expected_kind = _expected_control_shape_kind(
            inventory,
            pose_bone.bone,
            available_kinds,
            style=style,
        )
        shape = widget_by_kind.get(expected_kind)
        if shape is None:
            raise LimbIKError(
                f"Saved control '{pose_bone.name}' has no managed {expected_kind} widget."
            )
        pose_bone.custom_shape = shape
        if style == "DEFAULT":
            if CONTROL_SHAPE_STYLE_KEY in pose_bone.bone:
                del pose_bone.bone[CONTROL_SHAPE_STYLE_KEY]
        else:
            pose_bone.bone[CONTROL_SHAPE_STYLE_KEY] = style
        _sync_pole_connector_visibility(inventory, pose_bone.bone, style)


def _purge_snapshot_owned(context, armature, snapshot):
    """Best-effort exact cleanup used only before transactional recovery."""
    live_direct_registry = (
        _load_direct_rest_registry(armature, strict=False)
        if DIRECT_REST_KEY in armature.data
        else None
    )
    constraint_names = set(snapshot.get("constraint_names", ()))
    armature_id = snapshot["armature_id"]
    purge_ids = {armature_id}
    live_armature_id = armature.data.get(ARMATURE_ID_KEY, "")
    if isinstance(live_armature_id, str) and live_armature_id:
        purge_ids.add(live_armature_id)
    _mode_set(context, armature, "POSE")
    for pose_bone in armature.pose.bones:
        registry = _constraint_registry(pose_bone, strict=False)
        for constraint in tuple(pose_bone.constraints):
            record = registry.get(constraint.name, {})
            if constraint.name in constraint_names or (
                isinstance(record, dict)
                and record.get("owner") == OWNER_VALUE
                and record.get("armature_id") in purge_ids
            ):
                name = constraint.name
                pose_bone.constraints.remove(constraint)
                registry.pop(name, None)
        _write_constraint_registry(pose_bone, registry)
    _mode_set(context, armature, "EDIT")
    for bone in tuple(armature.data.edit_bones):
        if bone.get(OWNER_KEY) == OWNER_VALUE and bone.get(ARMATURE_ID_KEY) in purge_ids:
            armature.data.edit_bones.remove(bone)
    _mode_set(context, armature, "OBJECT")
    if live_direct_registry is not None and live_direct_registry.get("limbs"):
        _restore_direct_registry_original(context, armature, live_direct_registry)
    live_source_widgets = _load_source_widget_registry(armature, strict=False)
    if live_source_widgets is not None:
        _mode_set(context, armature, "POSE")
        _restore_source_widget_originals(armature, live_source_widgets, strict_current=False)
        _mode_set(context, armature, "OBJECT")
    for obj in tuple(bpy.data.objects):
        if obj.get(OWNER_KEY) == OWNER_VALUE and obj.get(ARMATURE_ID_KEY) in purge_ids and obj.get(ROLE_KEY) == "WIDGET":
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in tuple(bpy.data.meshes):
        if mesh.get(OWNER_KEY) == OWNER_VALUE and mesh.get(ARMATURE_ID_KEY) in purge_ids and mesh.get(ROLE_KEY) == "WIDGET_DATA" and not mesh.users:
            bpy.data.meshes.remove(mesh)
    for collection in tuple(bpy.data.collections):
        if collection.get(OWNER_KEY) == OWNER_VALUE and collection.get(ARMATURE_ID_KEY) in purge_ids and collection.get(ROLE_KEY) == "WIDGET_COLLECTION":
            bpy.data.collections.remove(collection)
    for collection in tuple(armature.data.collections):
        if collection.get(OWNER_KEY) == OWNER_VALUE and collection.get(ARMATURE_ID_KEY) in purge_ids and collection.get(ROLE_KEY) == "CONTROL_COLLECTION":
            armature.data.collections.remove(collection)
    if ARMATURE_ID_KEY in armature.data:
        del armature.data[ARMATURE_ID_KEY]
    if SCHEMA_KEY in armature.data:
        del armature.data[SCHEMA_KEY]
    if DIRECT_REST_KEY in armature.data:
        del armature.data[DIRECT_REST_KEY]
    if SOURCE_WIDGETS_KEY in armature.data:
        del armature.data[SOURCE_WIDGETS_KEY]
    if TARGET_ROTATION_VERSION_KEY in armature.data:
        del armature.data[TARGET_ROTATION_VERSION_KEY]


def _restore_owned_snapshot(context, armature, snapshot):
    _purge_snapshot_owned(context, armature, snapshot)
    direct_snapshot_registry = None
    if _is_direct_preroll_schema(snapshot["schema"]):
        raw = snapshot.get("direct_rest_raw")
        if not isinstance(raw, str) or not raw:
            raise LimbIKError("The saved Direct Pre-Roll recovery snapshot has no Rest registry.")
        armature.data[DIRECT_REST_KEY] = raw
        try:
            direct_snapshot_registry = _load_direct_rest_registry(armature, strict=True)
        finally:
            if DIRECT_REST_KEY in armature.data:
                del armature.data[DIRECT_REST_KEY]
        _restore_direct_registry_original(context, armature, direct_snapshot_registry)
    armature.data[ARMATURE_ID_KEY] = snapshot["armature_id"]
    if _stores_explicit_schema(snapshot["schema"]):
        armature.data[SCHEMA_KEY] = snapshot["schema"]
    transaction = None
    try:
        _mode_set(context, armature, "POSE")
        _built, transaction = _build_plans(
            context,
            armature,
            snapshot["plans"],
            schema=snapshot["schema"],
            desired_source_pose=snapshot.get("source_pose"),
            decorate_source_widgets=bool(snapshot.get("source_widgets_raw")),
            foot_widget_kinds=snapshot.get("foot_widget_kinds"),
            target_rotation_version=snapshot.get("target_rotation_version", 0),
        )
        _reapply_control_shape_styles(
            context,
            armature,
            snapshot["control_pose"],
            snapshot["widget_geometry"],
            transaction,
        )
        for name, state in snapshot["control_pose"].items():
            pose_bone = armature.pose.bones[name]
            pose_bone.rotation_mode = state["rotation_mode"]
            pose_bone.matrix_basis = state["matrix_basis"]
            shape_state = state.get("shape")
            if shape_state is not None:
                _validate_shape_state(shape_state, f"Saved generated bone '{name}'")
                shape_name = shape_state.get("custom_shape", "")
                transform_name = shape_state.get("custom_shape_transform", "")
                shape = bpy.data.objects.get(shape_name) if shape_name else None
                transform = armature.pose.bones.get(transform_name) if transform_name else None
                if shape_name and shape is None:
                    raise LimbIKError(f"Saved generated Custom Shape '{shape_name}' no longer exists.")
                if transform_name and transform is None:
                    raise LimbIKError(
                        f"Saved generated Custom Shape Transform '{transform_name}' no longer exists."
                    )
                pose_bone.custom_shape = shape
                pose_bone.custom_shape_transform = transform
                pose_bone.use_custom_shape_bone_size = bool(shape_state["use_bone_size"])
                pose_bone.custom_shape_scale_xyz = _shape_vector(
                    shape_state, "scale", f"Saved generated bone '{name}'"
                )
                pose_bone.custom_shape_translation = _shape_vector(
                    shape_state, "translation", f"Saved generated bone '{name}'"
                )
                pose_bone.custom_shape_rotation_euler = _shape_vector(
                    shape_state, "rotation", f"Saved generated bone '{name}'"
                )
                if (
                    hasattr(pose_bone, "custom_shape_wire_width")
                    and shape_state.get("wire_width") is not None
                ):
                    pose_bone.custom_shape_wire_width = float(shape_state["wire_width"])
            bone = pose_bone.bone
            if state.get("visual_default_present"):
                bone[CONTROL_VISUAL_DEFAULT_KEY] = state.get("visual_default_raw", "")
            elif CONTROL_VISUAL_DEFAULT_KEY in bone:
                del bone[CONTROL_VISUAL_DEFAULT_KEY]
            if state.get("shape_style_present"):
                bone[CONTROL_SHAPE_STYLE_KEY] = state.get("shape_style_raw")
            elif CONTROL_SHAPE_STYLE_KEY in bone:
                del bone[CONTROL_SHAPE_STYLE_KEY]
            if state.get("auto_align_present"):
                bone[AUTO_ALIGN_KEY] = state.get("auto_align_value")
            elif AUTO_ALIGN_KEY in bone:
                del bone[AUTO_ALIGN_KEY]
        for obj in bpy.data.objects:
            if not _owned(obj, snapshot["armature_id"], role="WIDGET"):
                continue
            geometry = snapshot["widget_geometry"].get(obj.get(KIND_KEY))
            if geometry is None:
                continue
            obj.data.clear_geometry()
            obj.data.from_pydata(geometry["vertices"], geometry["edges"], ())
            obj.data.update()
        if direct_snapshot_registry is not None:
            armature.data[DIRECT_REST_KEY] = snapshot["direct_rest_raw"]
        source_widgets_raw = snapshot.get("source_widgets_raw")
        if source_widgets_raw:
            _restore_source_widget_registry_snapshot(
                context,
                armature,
                source_widgets_raw,
                transaction,
                schema=snapshot["schema"],
            )
        elif SOURCE_WIDGETS_KEY in armature.data:
            raise LimbIKError("Legacy recovery unexpectedly created source-control Custom Shapes.")
        context.view_layer.update()
        recovered_inventory = _validate_inventory(armature)
        _removal_resources(context, armature, recovered_inventory)
        return transaction
    except Exception:
        if transaction is not None:
            _rollback_build(context, armature, transaction)
        if ARMATURE_ID_KEY in armature.data:
            del armature.data[ARMATURE_ID_KEY]
        if SCHEMA_KEY in armature.data:
            del armature.data[SCHEMA_KEY]
        if DIRECT_REST_KEY in armature.data:
            del armature.data[DIRECT_REST_KEY]
        if SOURCE_WIDGETS_KEY in armature.data:
            del armature.data[SOURCE_WIDGETS_KEY]
        if TARGET_ROTATION_VERSION_KEY in armature.data:
            del armature.data[TARGET_ROTATION_VERSION_KEY]
        raise


def _remove_owned(context, armature, *, refuse_dependencies=True):
    inventory = _validate_inventory(armature)
    if not inventory["rigs"]:
        raise LimbIKError("No exactly tagged Limb IK rig exists on the active Armature.")
    if refuse_dependencies:
        problems = _foreign_dependency_problems(armature, inventory)
        if problems:
            raise LimbIKError(f"Remove Generated Rig was refused: {problems[0]}.")
    resources = _removal_resources(context, armature, inventory)
    direct_registry = (
        _load_direct_rest_registry(armature, strict=True)
        if _is_direct_preroll_schema(inventory["schema"])
        else None
    )
    armature_id = inventory["armature_id"]
    removed_constraints = 0
    _mode_set(context, armature, "POSE")
    for pose_bone, constraint, _record in reversed(inventory["records"]):
        registry = _constraint_registry(pose_bone, strict=True)
        name = constraint.name
        pose_bone.constraints.remove(constraint)
        registry.pop(name, None)
        _write_constraint_registry(pose_bone, registry)
        removed_constraints += 1
    _mode_set(context, armature, "EDIT")
    removed_bones = 0
    for name in [bone.name for bone in inventory["bones"]]:
        edit_bone = armature.data.edit_bones.get(name)
        if edit_bone is not None:
            armature.data.edit_bones.remove(edit_bone)
            removed_bones += 1
    _mode_set(context, armature, "OBJECT")
    if direct_registry is not None:
        _restore_direct_registry_original(context, armature, direct_registry)

    source_widgets = inventory.get("source_widgets")
    if source_widgets is not None:
        _mode_set(context, armature, "POSE")
        _restore_source_widget_originals(armature, source_widgets, strict_current=True)
        _mode_set(context, armature, "OBJECT")

    widget_objects = resources["widget_objects"]
    widget_meshes = resources["widget_meshes"]
    for obj in widget_objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in widget_meshes:
        if not mesh.users:
            bpy.data.meshes.remove(mesh)
    bpy.data.collections.remove(resources["widget_collection"])
    armature.data.collections.remove(resources["control_collection"])
    if ARMATURE_ID_KEY in armature.data:
        del armature.data[ARMATURE_ID_KEY]
    if SCHEMA_KEY in armature.data:
        del armature.data[SCHEMA_KEY]
    if DIRECT_REST_KEY in armature.data:
        del armature.data[DIRECT_REST_KEY]
    if SOURCE_WIDGETS_KEY in armature.data:
        del armature.data[SOURCE_WIDGETS_KEY]
    if TARGET_ROTATION_VERSION_KEY in armature.data:
        del armature.data[TARGET_ROTATION_VERSION_KEY]
    return {"constraints": removed_constraints, "bones": removed_bones, "widgets": len(widget_objects)}


def _execute_build(operator, context, scope):
    settings = _settings(context)
    armature = None
    snapshot = None
    transaction = None
    built = ()
    error = None
    master_state = None
    schema = CURRENT_SCHEMA
    try:
        armature = _require_active_armature(context, settings)
        snapshot = _capture_context(context, armature)
        master_state = _neutralize_existing_master(context, armature)
        if scope == "SELECTED":
            plans = _plans_for_selected(context, armature, settings)
        elif scope == "ALL":
            plans = _plans_for_all(context, armature, settings)
        elif scope in KINDS:
            # Kept for the registered 0.25/0.26 compatibility operators.
            plans = _plans_for_kind(context, armature, settings, scope)
        else:
            raise LimbIKError("Unknown Limb IK build scope.")
        schema = (
            DIRECT_PREROLL_SCHEMA
            if settings.build_method == "DIRECT_PREROLL"
            else CURRENT_SCHEMA
        )
        built, transaction = _build_plans(context, armature, plans, schema=schema)
    except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        error = exc
    if master_state is not None and armature is not None:
        try:
            _restore_master_state(context, armature, master_state)
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as restore_master_exc:
            if transaction is not None:
                rollback_errors = _rollback_build(context, armature, transaction)
                transaction = None
                suffix = f" Rollback also reported: {'; '.join(rollback_errors)}" if rollback_errors else ""
                error = LimbIKError(f"Master pose restoration failed, so the generated rig was rolled back: {restore_master_exc}.{suffix}")
            elif error is None:
                error = restore_master_exc
    if snapshot is not None and armature is not None:
        try:
            _restore_context(context, armature, snapshot)
        except (LimbIKError, ReferenceError, RuntimeError) as restore_exc:
            if transaction is not None:
                rollback_errors = _rollback_build(context, armature, transaction)
                try:
                    _restore_context(context, armature, snapshot)
                except (LimbIKError, ReferenceError, RuntimeError) as second_restore:
                    rollback_errors.append(str(second_restore))
                suffix = f" Rollback also reported: {'; '.join(rollback_errors)}" if rollback_errors else ""
                error = LimbIKError(f"Context restoration failed, so the generated rig was rolled back: {restore_exc}.{suffix}")
            elif error is None:
                error = LimbIKError(f"Limb IK context restoration failed: {restore_exc}")
    if error is not None:
        message = str(error)
        level = "ERROR" if "rollback" in message.lower() or "restoration" in message.lower() else "WARNING"
        _set_status(settings, level, message)
        operator.report({"ERROR" if level == "ERROR" else "WARNING"}, message)
        return {"CANCELLED"}
    if _is_direct_preroll_schema(schema):
        try:
            payload = json.loads(settings.analysis_json)
            payload["digest"] = _armature_digest(armature)
            settings.analysis_json = _canonical_json(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            settings.analysis_json = ""
        settings.direct_preroll_json = ""
    warnings = [plan.pole_warning for plan in built if plan.pole_warning]
    labels = {"SELECTED": "selected", "ALL": "available"}
    label = labels.get(scope, scope.title())
    if _is_direct_preroll_schema(schema):
        message = (
            f"Built {len(built)} {label} Direct Pre-Roll IK side{'s' if len(built) != 1 else ''}; "
            "Target + Pole are the only visible controls; one hidden display aim keeps the Pole arrow aligned."
        )
    else:
        message = f"Built {len(built)} {label} IK side{'s' if len(built) != 1 else ''}; targets, Poles, IK(2), and end rotation are ready."
    if warnings:
        message += " " + " ".join(warnings)
    _set_status(settings, "WARNING" if warnings else "SUCCESS", message)
    operator.report({"WARNING" if warnings else "INFO"}, message)
    return {"FINISHED"}


class CHARACTERDESIGNER_OT_limb_ik_build_arm(Operator):
    bl_idname = "character_designer.limb_ik_build_arm"
    bl_label = "Build Arm Rig"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return context.object is not None and context.object.type == "ARMATURE" and settings is not None

    def execute(self, context):
        return _execute_build(self, context, "ARM")


class CHARACTERDESIGNER_OT_limb_ik_build_leg(Operator):
    bl_idname = "character_designer.limb_ik_build_leg"
    bl_label = "Build Leg Rig"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return context.object is not None and context.object.type == "ARMATURE" and settings is not None

    def execute(self, context):
        return _execute_build(self, context, "LEG")


class CHARACTERDESIGNER_OT_limb_ik_build_selected(Operator):
    bl_idname = "character_designer.limb_ik_build_selected"
    bl_label = "Build IK"
    bl_description = "Build only the Arm or Leg currently selected in the dropdown"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return context.object is not None and context.object.type == "ARMATURE" and settings is not None

    def execute(self, context):
        return _execute_build(self, context, "SELECTED")


class CHARACTERDESIGNER_OT_limb_ik_build_all(Operator):
    bl_idname = "character_designer.limb_ik_build_all"
    bl_label = "Build All"
    bl_description = "Build every complete analyzed Arm and Leg in one atomic Undo step"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return context.object is not None and context.object.type == "ARMATURE" and settings is not None

    def execute(self, context):
        return _execute_build(self, context, "ALL")


class CHARACTERDESIGNER_OT_limb_ik_remove(Operator):
    bl_idname = "character_designer.limb_ik_remove"
    bl_label = "Remove Generated Rig"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        settings = _settings(context)
        armature = None
        context_snapshot = None
        rig_snapshot = None
        summary = None
        error = None
        removal_attempted = False
        master_state = None
        try:
            armature = _require_active_armature(context, settings, analyzed=False)
            context_snapshot = _capture_context(context, armature)
            inventory = _validate_inventory(armature)
            _removal_resources(context, armature, inventory)
            problems = _foreign_dependency_problems(armature, inventory)
            if problems:
                raise LimbIKError(f"Remove Generated Rig was refused: {problems[0]}.")
            master_state = _neutralize_existing_master(context, armature, validate_scale=False)
            inventory = _validate_inventory(armature)
            rig_snapshot = _snapshot_owned_rig(armature, inventory)
            if master_state is not None and MASTER_NAME in rig_snapshot["control_pose"]:
                rig_snapshot["control_pose"][MASTER_NAME].update(
                    {
                        "rotation_mode": master_state["rotation_mode"],
                        "matrix_basis": master_state["matrix_basis"].copy(),
                    }
                )
            removal_attempted = True
            summary = _remove_owned(context, armature, refuse_dependencies=False)
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            error = exc
            if removal_attempted and rig_snapshot is not None:
                try:
                    _restore_owned_snapshot(context, armature, rig_snapshot)
                    master_state = None
                except Exception as recovery_exc:
                    error = LimbIKError(f"{exc} Recovery also failed: {recovery_exc}")
            elif master_state is not None and armature is not None:
                try:
                    _restore_master_state(context, armature, master_state)
                    master_state = None
                except Exception as recovery_exc:
                    error = LimbIKError(f"{exc} Master pose recovery also failed: {recovery_exc}")
        if context_snapshot is not None and armature is not None:
            try:
                _restore_context(context, armature, context_snapshot)
            except (LimbIKError, ReferenceError, RuntimeError) as restore_exc:
                if error is None and rig_snapshot is not None:
                    try:
                        _restore_owned_snapshot(context, armature, rig_snapshot)
                        _restore_context(context, armature, context_snapshot)
                        error = LimbIKError(f"Remove was rolled back because context restoration failed: {restore_exc}")
                    except Exception as recovery_exc:
                        error = LimbIKError(f"Context restoration failed after Remove, and rig recovery also failed: {recovery_exc}")
                elif error is None:
                    error = restore_exc
        if error is not None:
            message = str(error)
            level = "ERROR" if "Recovery also failed" in message or "recovery also failed" in message else "WARNING"
            _set_status(settings, level, message)
            self.report({"ERROR" if level == "ERROR" else "WARNING"}, message)
            return {"CANCELLED"}
        settings.analysis_json = ""
        message = f"Removed {summary['bones']} controls, {summary['constraints']} constraints, and {summary['widgets']} widgets. Analyze again before rebuilding."
        _set_status(settings, "SUCCESS", message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_limb_ik_rebuild(Operator):
    bl_idname = "character_designer.limb_ik_rebuild"
    bl_label = "Rebuild Rig"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = _settings(context)
        armature = None
        context_snapshot = None
        rig_snapshot = None
        new_transaction = None
        rebuilt = ()
        error = None
        removal_attempted = False
        master_state = None
        try:
            armature = _require_active_armature(context, settings)
            context_snapshot = _capture_context(context, armature)
            inventory = _validate_inventory(armature)
            rig_keys = sorted(inventory["rigs"])
            previous_schema = inventory["schema"]
            if not rig_keys:
                raise LimbIKError("No generated Limb IK rig exists to rebuild.")
            _removal_resources(context, armature, inventory)
            problems = _foreign_dependency_problems(armature, inventory)
            if problems:
                raise LimbIKError(f"Rebuild Rig was refused: {problems[0]}.")
            master_state = _neutralize_existing_master(context, armature)
            inventory = _validate_inventory(armature)
            rig_snapshot = _snapshot_owned_rig(armature, inventory)
            if master_state is not None and MASTER_NAME in rig_snapshot["control_pose"]:
                rig_snapshot["control_pose"][MASTER_NAME].update(
                    {
                        "rotation_mode": master_state["rotation_mode"],
                        "matrix_basis": master_state["matrix_basis"].copy(),
                    }
                )
            plans = _plans_for_exact_keys(context, armature, settings, rig_keys)
            # Auto Align is a rig-wide animator mode, not part of the
            # chain/direction planning inputs.  Preserve each saved Target
            # value (including legacy mixed state) across Rebuild; the global
            # toggle will unify every generated limb on its next use.
            prior_auto_align = {
                (plan.chain.kind, plan.chain.side): bool(plan.auto_align)
                for plan in rig_snapshot["plans"]
            }
            plans = [
                replace(
                    plan,
                    auto_align=prior_auto_align.get(
                        (plan.chain.kind, plan.chain.side),
                        bool(plan.auto_align),
                    ),
                )
                for plan in plans
            ]
            if previous_schema == CURRENT_SCHEMA:
                # A same-schema Rebuild is also the transactional refresh path.
                # Preserve live Target/Pole controls and the established ORI
                # baseline when the artist did not change chain selection or
                # the explicit Pole Direction.  Otherwise retain the freshly
                # planned data so Rebuild still applies intentional edits.
                prior_plans = {
                    (plan.chain.kind, plan.chain.side): plan
                    for plan in rig_snapshot["plans"]
                }
                preserved = []
                for plan in plans:
                    old = prior_plans.get((plan.chain.kind, plan.chain.side))
                    old_direction = (
                        old.configured_pole_direction
                        if old is not None and old.configured_pole_direction is not None
                        else (old.pole_direction if old is not None else None)
                    )
                    new_direction = (
                        plan.configured_pole_direction
                        if plan.configured_pole_direction is not None
                        else plan.pole_direction
                    )
                    same_direction = (
                        old_direction is not None
                        and old_direction.length > EPSILON
                        and new_direction.length > EPSILON
                        and old_direction.normalized().dot(new_direction.normalized()) >= 1.0 - 1.0e-8
                    )
                    if old is not None and old.chain.names == plan.chain.names and same_direction:
                        # Keep the exact MCH solver inputs so the current joint
                        # and endpoint do not pop, but deliberately rebaseline
                        # each independent ORI helper from the preserved source
                        # output.  A subsequent control move then performs its
                        # minimum swing relative to this Rebuild pose, not the
                        # rig's earlier Build pose.
                        mch_pose = {
                            role: state
                            for role, state in (old.mechanism_pose or {}).items()
                            if role in {"mch_upper", "mch_lower"}
                        }
                        preserved.append(
                            replace(
                                old,
                                rig_id=uuid.uuid4().hex,
                                mechanism_pose=mch_pose,
                            )
                        )
                    else:
                        preserved.append(plan)
                plans = preserved
            elif _is_direct_preroll_schema(previous_schema):
                prior_plans = {
                    (plan.chain.kind, plan.chain.side): plan
                    for plan in rig_snapshot["plans"]
                }
                preserved = []
                for plan in plans:
                    old = prior_plans.get((plan.chain.kind, plan.chain.side))
                    old_direction = (
                        old.configured_pole_direction
                        if old is not None and old.configured_pole_direction is not None
                        else (old.pole_direction if old is not None else None)
                    )
                    new_direction = (
                        plan.configured_pole_direction
                        if plan.configured_pole_direction is not None
                        else plan.pole_direction
                    )
                    same_direction = (
                        old_direction is not None
                        and old_direction.length > EPSILON
                        and new_direction.length > EPSILON
                        and old_direction.normalized().dot(new_direction.normalized()) >= 1.0 - 1.0e-8
                    )
                    if old is not None and old.chain.names == plan.chain.names and same_direction:
                        old_end_rotation = next(
                            constraint
                            for _pose_bone, constraint, record in inventory["rigs"][(plan.chain.kind, plan.chain.side)]["entries"]
                            if record["role"] == "END_ROTATION"
                        )
                        legacy_direct_arm = (
                            plan.chain.kind == "ARM"
                            and old_end_rotation.target_space == "WORLD"
                            and old_end_rotation.owner_space == "WORLD"
                        )
                        if legacy_direct_arm and not _matrix_is_identity(old.target_basis):
                            raise LimbIKError(
                                f"Return the {old.chain.side} Arm Direct Target to its Rest transform before "
                                "upgrading its wrist frame with Rebuild Rig."
                            )
                        preserved.append(
                            replace(
                                old,
                                rig_id=uuid.uuid4().hex,
                                # Schema 4 had no display helper, so its exact
                                # snapshot carries an empty name.  Rebuild is
                                # the explicit migration path to schema 5.
                                display_name=plan.display_name,
                            )
                        )
                    else:
                        if old is not None and (
                            not _matrix_is_identity(old.target_basis)
                            or not _matrix_is_identity(old.pole_basis)
                        ):
                            raise LimbIKError(
                                f"Return the {old.chain.side} {old.chain.kind.title()} Direct Target and Pole "
                                "to their Rest transforms before changing its chain or Pole Direction."
                            )
                        preserved.append(plan)
                plans = preserved
            removal_attempted = True
            _remove_owned(context, armature)
            _mode_set(context, armature, "POSE")
            context.view_layer.update()
            for plan in plans:
                if plan.mechanism_pose or (
                    _is_direct_preroll_schema(previous_schema)
                    and plan.preserve_pole_angle
                    and plan.direct_rest is not None
                ):
                    # Same-schema, unchanged-direction plans carry the old MCH
                    # input basis, while Direct plans carry their exact applied
                    # Rest snapshot.  Both intentionally take an exact refresh.
                    continue
                # Schema 1/2 put IK directly on deform bones, and a schema-3
                # direction edit also leaves the old evaluated solution on the
                # source until removal.  In both cases that evaluated frame can
                # contain the Pole-induced roll we must not bake into ORI.
                # Read the clean artist-authored inputs only now, after the old
                # graph is gone, while retaining the pre-remove endpoint/control
                # geometry encoded by the plan.
                upper_matrix = armature.pose.bones[plan.chain.upper].matrix.copy()
                lower_matrix = armature.pose.bones[plan.chain.lower].matrix.copy()
                plan.desired_upper_matrix = upper_matrix
                plan.desired_lower_matrix = lower_matrix
                plan.mechanism_pose = None
                direction = (
                    plan.configured_pole_direction
                    if plan.configured_pole_direction is not None
                    else plan.pole_direction
                )
                pole_head, pole_angle, effective_direction = _pole_solution(
                    plan.start,
                    plan.joint,
                    plan.end,
                    Vector(upper_matrix.to_3x3().col[0]),
                    direction,
                    settings.pole_distance_ratio,
                )
                old_tail_length = max((plan.pole_tail - plan.pole_head).length, 1.0e-4)
                plan.pole_head = pole_head
                plan.pole_tail = pole_head + effective_direction * old_tail_length
                plan.pole_direction = effective_direction
                plan.pole_joint = _joint_on_direction_plane(
                    plan.start,
                    plan.joint,
                    plan.end,
                    effective_direction,
                )
                plan.pole_angle = pole_angle
            rebuilt, new_transaction = _build_plans(
                context,
                armature,
                plans,
                schema=(
                    DIRECT_PREROLL_SCHEMA
                    if _is_direct_preroll_schema(previous_schema)
                    else CURRENT_SCHEMA
                ),
                desired_source_pose=rig_snapshot["source_pose"],
            )
            _reapply_control_shape_styles(
                context,
                armature,
                rig_snapshot["control_pose"],
                rig_snapshot["widget_geometry"],
                new_transaction,
            )
            _reapply_control_visual_overrides(
                armature,
                rig_snapshot["control_pose"],
            )
            _removal_resources(context, armature, _validate_inventory(armature))
            _restore_master_state(context, armature, master_state)
            master_state = None
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            error = exc
            if new_transaction is not None:
                _rollback_build(context, armature, new_transaction)
                new_transaction = None
            if removal_attempted and rig_snapshot is not None:
                try:
                    _restore_owned_snapshot(context, armature, rig_snapshot)
                    master_state = None
                except Exception as recovery_exc:
                    error = LimbIKError(f"{exc} Old-rig recovery also failed: {recovery_exc}")
            elif master_state is not None and armature is not None:
                try:
                    _restore_master_state(context, armature, master_state)
                    master_state = None
                except Exception as recovery_exc:
                    error = LimbIKError(f"{exc} Master pose recovery also failed: {recovery_exc}")
        if context_snapshot is not None and armature is not None:
            try:
                _restore_context(context, armature, context_snapshot)
            except (LimbIKError, ReferenceError, RuntimeError) as restore_exc:
                if error is None and rig_snapshot is not None:
                    try:
                        if new_transaction is not None:
                            _rollback_build(context, armature, new_transaction)
                            new_transaction = None
                        _restore_owned_snapshot(context, armature, rig_snapshot)
                        _restore_context(context, armature, context_snapshot)
                        error = LimbIKError(f"Rebuild was rolled back because context restoration failed: {restore_exc}")
                    except Exception as recovery_exc:
                        error = LimbIKError(f"Rebuild context restoration failed, and old-rig recovery also failed: {recovery_exc}")
                elif error is None:
                    error = restore_exc
        if error is not None:
            message = str(error)
            _set_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        message = f"Rebuilt {len(rebuilt)} Limb IK sides from the current analyzed chains."
        _set_status(settings, "SUCCESS", message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_limb_ik_default_pole_direction(Operator):
    bl_idname = "character_designer.limb_ik_default_pole_direction"
    bl_label = "Default Pole Direction"
    bl_description = "Reset only the selected limb to its Armature-local anatomical Pole direction"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _settings(context) is not None

    def execute(self, context):
        settings = _settings(context)
        kind, side = SELECTED_LIMBS.get(settings.selected_limb, ("", ""))
        if kind not in KINDS or side not in SIDES:
            message = "Choose one Arm or Leg before resetting its Pole Direction."
            _set_status(settings, "WARNING", message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        chain_names = tuple(getattr(settings, _field_name(kind, side, role)) for role in ROLES)
        direction, _used_modeled_bend = _rest_pole_direction(settings.armature, kind, side, chain_names)
        setattr(settings, _pole_direction_field(kind, side), tuple(direction))
        message = f"Reset {side} {kind.title()} Pole Direction to its fixed anatomical axis."
        _set_status(settings, "SUCCESS", message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


def _matrix_is_finite(matrix):
    return all(
        math.isfinite(float(matrix[row][column]))
        for row in range(4)
        for column in range(4)
    )


def _target_transform_has_driver(armature, target_name):
    animation = armature.animation_data
    if animation is None:
        return False
    return any(
        _path_mentions_bone(fcurve.data_path, {target_name})
        for fcurve in getattr(animation, "drivers", ())
    )


def _target_transform_has_keyed_animation(armature, target_name):
    return any(
        _path_mentions_bone(fcurve.data_path, {target_name})
        for action in _actions_for_id(armature)
        for fcurve in _fcurves_for_action(action)
    )


def _constraint_has_animation_or_driver(armature, owner_name, constraint_name):
    path = _owned_constraint_path(owner_name, constraint_name)
    if any(
        fcurve.data_path.startswith(path)
        for action in _actions_for_id(armature)
        for fcurve in _fcurves_for_action(action)
    ):
        return True
    animation = armature.animation_data
    return any(
        fcurve.data_path.startswith(path)
        for fcurve in getattr(animation, "drivers", ()) if animation is not None
    )


def _solve_auto_align_target(
    armature,
    inventory,
    rig,
    target,
    solver,
    free_end,
    end_rotation,
    auto_offset_rotation=None,
):
    """Calculate the Auto-to-Manual handoff transform from the natural end pose.

    Direct Arms intentionally use a local-owner-orient Copy Rotation contract;
    every other current schema uses World-to-World. Keeping those paths
    separate avoids reinterpreting a Hand's modeled Rest offset.
    """

    local_owner_orient = (
        end_rotation.target_space == "LOCAL_OWNER_ORIENT"
        and end_rotation.owner_space == "LOCAL_WITH_PARENT"
    )
    world_to_world = (
        end_rotation.target_space == "WORLD"
        and end_rotation.owner_space == "WORLD"
    )
    if local_owner_orient:
        old_basis = target.matrix_basis.copy()
        free_basis = free_end.matrix_basis.copy()
        desired_rotation = free_basis.to_quaternion().normalized()
        if auto_offset_rotation is not None and not auto_offset_rotation.mute:
            # LOCAL/LOCAL + AFTER evaluates the natural owner basis followed by
            # the Target's local offset.  Manual END_ROTATION replaces the
            # owner's local rotation, so bake that exact product into the
            # Target for a no-pop Auto -> Manual handoff.
            desired_rotation = (
                desired_rotation
                @ old_basis.to_quaternion().normalized()
            )
            desired_rotation.normalize()
        result = Matrix.LocRotScale(
            old_basis.to_translation(),
            desired_rotation,
            old_basis.to_scale(),
        )
        if not _matrix_is_finite(result):
            raise LimbIKError("Auto Align produced a non-finite Direct Arm transform.")
        return "BASIS", result

    if not world_to_world:
        raise LimbIKError("Auto Align does not support the generated end rotation's current spaces.")

    armature_world = armature.matrix_world
    target_world = armature_world @ target.matrix
    solver_world = armature_world @ solver.matrix
    end_world = armature_world @ free_end.matrix
    if not all(_matrix_is_finite(matrix) for matrix in (target_world, solver_world, end_world)):
        raise LimbIKError("Auto Align cannot use a non-finite Target, solver, or end transform.")
    solver_rotation = solver_world.to_quaternion().normalized()
    desired_rotation = end_world.to_quaternion().normalized()
    delta_rotation = desired_rotation @ solver_rotation.inverted()
    delta_rotation.normalize()
    pivot = solver_world.to_translation()
    delta = (
        Matrix.Translation(pivot)
        @ delta_rotation.to_matrix().to_4x4()
        @ Matrix.Translation(-pivot)
    )
    result = armature_world.inverted_safe() @ delta @ target_world
    if not _matrix_is_finite(result):
        raise LimbIKError("Auto Align produced a non-finite Target transform.")
    return "MATRIX", result


def _set_auto_align_selected_target(context, armature, settings, enabled=None):
    """Transactionally switch one limb between natural and manual end rotation.

    Auto mode mutes the absolute END_ROTATION and, on feature-v1 rigs, enables
    a local AFTER offset from the same visible Target.  Hand/Foot therefore
    follows the live solved chain while retaining all three animator rotation
    channels, without polling or extra bones.  Returning to Manual bakes the
    evaluated end orientation into the Target before swapping constraints.
    """

    kind, side = SELECTED_LIMBS.get(settings.selected_limb, ("", ""))
    if kind not in KINDS or side not in SIDES:
        raise LimbIKError("Choose one generated Arm or Leg before changing Auto Align.")
    inventory = _validate_inventory(armature)
    rig = inventory["rigs"].get((kind, side))
    if rig is None:
        raise LimbIKError(f"Build the {side} {kind.title()} IK before changing Auto Align.")
    # Preserve the long-standing inventory representation; these two fields
    # are a local convenience for the matrix solver only.
    rig = dict(rig, kind=kind, side=side)
    target = armature.pose.bones[rig["target"].name]
    solver = armature.pose.bones[rig["solver_target"].name]
    end = armature.pose.bones[rig["chain"][2]]
    end_entries = [
        (pose_bone, constraint, record)
        for pose_bone, constraint, record in rig["entries"]
        if record["role"] == "END_ROTATION"
    ]
    if len(end_entries) != 1:
        raise LimbIKError("The selected limb has no unique generated end-rotation constraint.")
    owner, end_rotation, _record = end_entries[0]
    if owner.name != end.name or end_rotation.type != "COPY_ROTATION":
        raise LimbIKError("The selected limb's end-rotation ownership is invalid.")
    auto_offset_rotation = rig.get("auto_offset_rotation")
    if (
        inventory["target_rotation_version"] == TARGET_ROTATION_VERSION
        and auto_offset_rotation is None
    ):
        raise LimbIKError(
            "The selected limb has no generated Auto Target rotation constraint."
        )
    current_enabled = bool(rig["auto_align"])
    desired_enabled = (not current_enabled) if enabled is None else bool(enabled)
    if desired_enabled == current_enabled:
        if desired_enabled and target.custom_shape_transform is None:
            # Upgrade a pre-0.32 Auto rig's display contract without touching
            # any animator transform channel or solver input.
            _retarget_custom_shape_frame(target, end)
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()
            return target.name, current_enabled, True
        return target.name, current_enabled, False
    if (
        abs(float(end_rotation.influence) - 1.0) > 1.0e-6
        or not all(getattr(end_rotation, name, False) for name in ("use_x", "use_y", "use_z"))
        or any(getattr(end_rotation, name, False) for name in ("invert_x", "invert_y", "invert_z"))
    ):
        raise LimbIKError("Auto Align requires the generated end rotation to use all axes at full influence.")
    if _constraint_has_animation_or_driver(armature, owner.name, end_rotation.name):
        raise LimbIKError("Auto Align will not override an animated/driven end rotation.")
    if auto_offset_rotation is not None and _constraint_has_animation_or_driver(
        armature,
        owner.name,
        auto_offset_rotation.name,
    ):
        raise LimbIKError(
            "Auto Align will not override an animated/driven Target rotation offset."
        )
    world_to_world = (
        end_rotation.target_space == "WORLD"
        and end_rotation.owner_space == "WORLD"
    )
    if not desired_enabled:
        if any(target.lock_rotation):
            raise LimbIKError(f"Unlock the {target.name} rotation channels before Manual rotation.")
        if bool(getattr(target, "lock_rotation_w", False)):
            raise LimbIKError(f"Unlock the {target.name} W rotation channel before Manual rotation.")
        if _target_transform_has_driver(armature, target.name):
            raise LimbIKError(f"Manual rotation setup will not overwrite a driver on {target.name}.")
        if _target_transform_has_keyed_animation(armature, target.name):
            raise LimbIKError(
                f"Manual rotation setup will not overwrite keyed/NLA animation on {target.name}; "
                "choose the mode before animation or remove those channels first."
            )
        if world_to_world and solver.name != target.name and any(target.lock_location):
            raise LimbIKError(
                f"Unlock the {target.name} location channels before Manual rotation; "
                "this Foot setup must rotate the visible Target around its internal solver pivot."
            )

    target_basis_before = target.matrix_basis.copy()
    target_display_before = target.custom_shape_transform
    target_visual_before = _control_visual_state(target)
    auto_key_present = AUTO_ALIGN_KEY in target.bone
    auto_key_before = target.bone.get(AUTO_ALIGN_KEY, None)
    constraint_mute_before = bool(end_rotation.mute)
    offset_mute_before = (
        bool(auto_offset_rotation.mute)
        if auto_offset_rotation is not None
        else None
    )
    solver_world_before = armature.matrix_world @ solver.matrix
    end_matrix_before = end.matrix.copy()
    end_world_before = armature.matrix_world @ end_matrix_before
    try:
        if desired_enabled:
            end_rotation.mute = True
            if auto_offset_rotation is not None:
                auto_offset_rotation.mute = False
            target.bone[AUTO_ALIGN_KEY] = True
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()
            if not end_rotation.mute:
                raise LimbIKError("Auto Align could not disable manual end rotation.")
            _retarget_custom_shape_frame(
                target,
                end,
                previous_reference_matrix=end_matrix_before,
            )
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()
        else:
            # The end is already evaluating naturally while Auto is enabled.
            # Capture that exact pose and solve the visible Target before
            # restoring its manual rotation relationship.
            free_end_matrix = end.matrix.copy()
            mode, value = _solve_auto_align_target(
                armature,
                inventory,
                rig,
                target,
                solver,
                end,
                end_rotation,
                auto_offset_rotation,
            )
            if auto_offset_rotation is not None:
                auto_offset_rotation.mute = True
                armature.update_tag(refresh={"OBJECT"})
                context.view_layer.update()
            if mode == "BASIS":
                target.matrix_basis = value
            elif mode == "MATRIX":
                target.matrix = value
            else:
                raise LimbIKError("Auto Align returned an unknown transform mode.")
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()
            end_rotation.mute = False
            target.bone[AUTO_ALIGN_KEY] = False
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()
            _retarget_custom_shape_frame(target, None)
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()

            if (
                end_rotation.target_space == "LOCAL_OWNER_ORIENT"
                and end_rotation.owner_space == "LOCAL_WITH_PARENT"
            ):
                # LOCAL_OWNER_ORIENT can leave a small evaluated residual after
                # a translated Target. Correct rotation only; keep the IK point
                # and display scale untouched.
                desired_end_world = armature.matrix_world @ free_end_matrix
                for _iteration in range(4):
                    current_end_world = armature.matrix_world @ end.matrix
                    if _rotation_error(current_end_world, desired_end_world) <= 3.0e-4:
                        break
                    correction = (
                        desired_end_world.to_quaternion().normalized()
                        @ current_end_world.to_quaternion().normalized().inverted()
                    )
                    target_world = armature.matrix_world @ target.matrix
                    corrected_world = Matrix.LocRotScale(
                        target_world.to_translation(),
                        correction @ target_world.to_quaternion().normalized(),
                        target_world.to_scale(),
                    )
                    target.matrix = armature.matrix_world.inverted_safe() @ corrected_world
                    armature.update_tag(refresh={"OBJECT"})
                    context.view_layer.update()

        solver_world_after = armature.matrix_world @ solver.matrix
        end_world_after = armature.matrix_world @ end.matrix
        position_error = (
            solver_world_after.to_translation() - solver_world_before.to_translation()
        ).length
        rotation_error = (
            _rotation_error(end_world_after, end_world_before)
            if not desired_enabled
            else 0.0
        )
        if (
            position_error > 2.0e-4
            or rotation_error > 3.0e-3
            or not _matrix_is_finite(end_world_after)
        ):
            raise LimbIKError(
                "Auto Align mode change could not preserve the IK target and current end pose "
                f"(position {position_error:.4g}, rotation {rotation_error:.4g})."
            )
        checked = _validate_inventory(armature)["rigs"].get((kind, side))
        if checked is None or checked["auto_align"] != desired_enabled:
            raise LimbIKError("Auto Align mode did not commit its owned state.")
    except Exception as original_error:
        recovery_errors = []
        try:
            target.matrix_basis = target_basis_before
        except Exception as exc:
            recovery_errors.append(f"Target restore failed: {exc}")
        try:
            target.custom_shape_transform = target_display_before
            _apply_control_visual_state(target, target_visual_before)
        except Exception as exc:
            recovery_errors.append(f"Target display restore failed: {exc}")
        # Keep this independent from the Target setter: even a malformed or
        # externally locked control must never leave END_ROTATION muted.
        try:
            end_rotation.mute = constraint_mute_before
        except Exception as exc:
            recovery_errors.append(f"end rotation restore failed: {exc}")
        if auto_offset_rotation is not None:
            try:
                auto_offset_rotation.mute = offset_mute_before
            except Exception as exc:
                recovery_errors.append(f"Target rotation offset restore failed: {exc}")
        try:
            if auto_key_present:
                target.bone[AUTO_ALIGN_KEY] = auto_key_before
            elif AUTO_ALIGN_KEY in target.bone:
                del target.bone[AUTO_ALIGN_KEY]
        except Exception as exc:
            recovery_errors.append(f"Auto Align state restore failed: {exc}")
        try:
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()
        except Exception as exc:
            recovery_errors.append(f"evaluation restore failed: {exc}")
        if recovery_errors:
            raise LimbIKError(
                "Auto Align failed and could not completely restore its temporary state: "
                + "; ".join(recovery_errors)
            ) from original_error
        raise
    return target.name, desired_enabled, True


def _set_auto_align_all_targets(context, armature, settings, enabled=None):
    """Atomically switch every generated limb to one global Auto state."""

    inventory = _validate_inventory(armature)
    if not inventory["rigs"]:
        raise LimbIKError("Build at least one Arm or Leg IK before changing Auto Align.")
    current_values = [bool(rig["auto_align"]) for rig in inventory["rigs"].values()]
    desired_enabled = (
        not all(current_values)
        if enabled is None
        else bool(enabled)
    )
    selected_before = settings.selected_limb
    enum_by_key = {value: name for name, value in SELECTED_LIMBS.items()}
    snapshots = []
    for key, rig in sorted(inventory["rigs"].items()):
        target = armature.pose.bones[rig["target"].name]
        end_entries = [
            (owner, constraint)
            for owner, constraint, record in rig["entries"]
            if record["role"] == "END_ROTATION"
        ]
        if len(end_entries) != 1:
            raise LimbIKError(
                f"The {key[1]} {key[0].title()} has no unique generated end rotation."
            )
        owner, end_rotation = end_entries[0]
        auto_offset_rotation = rig.get("auto_offset_rotation")
        if (
            inventory["target_rotation_version"] == TARGET_ROTATION_VERSION
            and auto_offset_rotation is None
        ):
            raise LimbIKError(
                f"The {key[1]} {key[0].title()} has no generated Auto Target rotation."
            )
        snapshots.append(
            {
                "key": key,
                "target": target,
                "target_basis": target.matrix_basis.copy(),
                "target_display": target.custom_shape_transform,
                "target_visual": _control_visual_state(target),
                "auto_present": AUTO_ALIGN_KEY in target.bone,
                "auto_value": target.bone.get(AUTO_ALIGN_KEY, None),
                "owner": owner,
                "constraint": end_rotation,
                "mute": bool(end_rotation.mute),
                "offset_constraint": auto_offset_rotation,
                "offset_mute": (
                    bool(auto_offset_rotation.mute)
                    if auto_offset_rotation is not None
                    else None
                ),
            }
        )

    changed_names = []
    try:
        for state in snapshots:
            key = state["key"]
            selected = enum_by_key.get(key)
            if selected is None:
                raise LimbIKError(
                    f"Auto Align cannot address the generated {key[1]} {key[0].title()}."
                )
            settings.selected_limb = selected
            target_name, _enabled, changed = _set_auto_align_selected_target(
                context,
                armature,
                settings,
                desired_enabled,
            )
            if changed:
                changed_names.append(target_name)
        checked = _validate_inventory(armature)
        if any(
            bool(rig["auto_align"]) != desired_enabled
            for rig in checked["rigs"].values()
        ):
            raise LimbIKError("Global Auto Align did not commit the same state to every limb.")
    except Exception as original_error:
        recovery_errors = []
        for state in snapshots:
            target = state["target"]
            try:
                target.matrix_basis = state["target_basis"]
                target.custom_shape_transform = state["target_display"]
                _apply_control_visual_state(target, state["target_visual"])
            except Exception as exc:
                recovery_errors.append(f"{target.name} Target restore failed: {exc}")
            try:
                state["constraint"].mute = state["mute"]
            except Exception as exc:
                recovery_errors.append(
                    f"{state['constraint'].name} restore failed: {exc}"
                )
            if state["offset_constraint"] is not None:
                try:
                    state["offset_constraint"].mute = state["offset_mute"]
                except Exception as exc:
                    recovery_errors.append(
                        f"{state['offset_constraint'].name} restore failed: {exc}"
                    )
            try:
                if state["auto_present"]:
                    target.bone[AUTO_ALIGN_KEY] = state["auto_value"]
                elif AUTO_ALIGN_KEY in target.bone:
                    del target.bone[AUTO_ALIGN_KEY]
            except Exception as exc:
                recovery_errors.append(f"{target.name} Auto state restore failed: {exc}")
        try:
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()
        except Exception as exc:
            recovery_errors.append(f"global evaluation restore failed: {exc}")
        if recovery_errors:
            raise LimbIKError(
                "Global Auto Align failed and could not completely restore every limb: "
                + "; ".join(recovery_errors)
            ) from original_error
        raise
    finally:
        settings.selected_limb = selected_before
    return tuple(changed_names), desired_enabled, len(snapshots)


class CHARACTERDESIGNER_OT_limb_ik_auto_align_target(Operator):
    bl_idname = "character_designer.limb_ik_auto_align_target"
    bl_label = "Auto Align Target"
    bl_description = "Toggle continuous natural alignment while keeping local Target rotation offsets"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        items=(
            ("TOGGLE", "Toggle", "Switch the current Auto Align state"),
            ("ENABLE", "Enable", "Use continuous natural limb orientation"),
            ("DISABLE", "Disable", "Return to manual Target rotation without a pose jump"),
        ),
        default="TOGGLE",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE" and context.mode in {"OBJECT", "POSE"}

    def execute(self, context):
        settings = _settings(context)
        try:
            armature = _require_active_armature(context, settings, analyzed=False)
            desired = {"ENABLE": True, "DISABLE": False}.get(self.action)
            changed_names, enabled, limb_count = _set_auto_align_all_targets(
                context,
                armature,
                settings,
                desired,
            )
            state = "Auto" if enabled else "Manual"
            message = f"All {limb_count} generated limb target(s) are in {state} rotation mode."
            if not changed_names:
                message += " No change was needed."
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            message = str(exc)
            _set_status(settings, "WARNING", message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}


class CHARACTERDESIGNER_OT_limb_ik_direct_preroll_check(Operator):
    bl_idname = "character_designer.limb_ik_direct_preroll_check"
    bl_label = "Check Pre-Roll"
    bl_description = "Calculate Direct IK Rest-plane and roll values without changing any bone"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return context.object is not None and context.object.type == "ARMATURE" and settings is not None

    def execute(self, context):
        settings = _settings(context)
        settings.direct_preroll_json = ""
        try:
            armature = _require_active_armature(context, settings)
            kind, side = SELECTED_LIMBS.get(settings.selected_limb, ("", ""))
            if kind not in KINDS or side not in SIDES:
                raise LimbIKError("Choose one Arm or Leg before checking Direct Pre-Roll.")
            chain = _chain_from_settings(settings, armature, kind, side)
            if chain is None:
                raise LimbIKError(
                    f"No complete {side} {kind.title()} chain is available; Analyze or choose all three bones."
                )
            result = _direct_preroll_analysis(
                armature,
                chain,
                _configured_pole_direction(settings, kind, side),
            )
            payload = {
                "version": DIRECT_PREROLL_RESULT_VERSION,
                "armature": armature.name,
                "armature_data": armature.data.name,
                "armature_token": str(armature.data.as_pointer()),
                "digest": _armature_digest(armature),
                "selected_limb": settings.selected_limb,
                "result": result,
            }
            settings.direct_preroll_json = _canonical_json(payload)
            offset = result["rest_plane_offset_degrees"]
            if result["status"] == "UNSTABLE":
                message = "Pre-Roll check: the Rest limb is effectively straight; add a measurable pre-bend first. No bones changed."
                level = "WARNING"
            elif result["status"] == "ALIGNED":
                message = (
                    f"Pre-Roll check: Rest plane is aligned ({abs(offset):.2f} deg). "
                    "Direct IK is a start-pose candidate; no bones changed."
                )
                level = "SUCCESS"
            else:
                message = (
                    f"Pre-Roll check: Rest plane is offset {abs(offset):.2f} deg; "
                    f"a {result['joint_shift']:.5g} BU Rest-joint re-plane is required. No bones changed."
                )
                level = "WARNING"
            _set_status(settings, level, message)
            self.report({"WARNING" if level == "WARNING" else "INFO"}, message)
            return {"FINISHED"}
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            _set_status(settings, "WARNING", str(exc))
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


class CHARACTERDESIGNER_OT_limb_ik_flip_pole(Operator):
    bl_idname = "character_designer.limb_ik_flip_pole"
    bl_label = "Flip Pole"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(items=(("ARM", "Arm", ""), ("LEG", "Leg", "")))
    side: EnumProperty(items=(("L", "Left", ""), ("R", "Right", "")))

    def execute(self, context):
        settings = _settings(context)
        try:
            armature = _require_active_armature(context, settings, analyzed=False)
            rig = _validate_inventory(armature)["rigs"].get((self.kind, self.side))
            if rig is None:
                raise LimbIKError(f"No generated {self.side} {self.kind.title()} rig exists.")
            ik = next(constraint for _pb, constraint, record in rig["entries"] if record["role"] == "IK")
            ik.pole_angle = math.remainder(ik.pole_angle + math.pi, math.tau)
            message = f"Flipped {self.side} {self.kind.title()} Pole solution by 180°."
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            _set_status(settings, "WARNING", str(exc))
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


def _draw_limb_fields(layout, settings, armature, kind, side):
    column = layout.column(align=True)
    for role in ROLES:
        property_name = _field_name(kind, side, role)
        if armature is not None:
            column.prop_search(settings, property_name, armature.data, "bones", text=role.title())
        else:
            column.prop(settings, property_name, text=role.title())


def _active_has_owned_side_rig(context):
    armature = context.object
    if armature is None or armature.type != "ARMATURE":
        return False
    try:
        armature_id = armature.data.get(ARMATURE_ID_KEY, "")
        if not isinstance(armature_id, str) or not armature_id:
            return False
        return any(
            bone.get(OWNER_KEY) == OWNER_VALUE
            and bone.get(ARMATURE_ID_KEY) == armature_id
            and isinstance(bone.get(RIG_ID_KEY), str)
            and bool(bone.get(RIG_ID_KEY))
            for bone in armature.data.bones
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        # Panel drawing must remain cheap and resilient.  The operator itself
        # still performs the strict inventory/dependency validation before it
        # removes anything.
        return False


def _global_auto_align_ui_state(context, settings):
    """Return availability and the all-limbs Auto state for panel drawing."""

    # State lives on generated Targets, whereas Analyze settings are transient.
    # Resolve every owned Target directly: the dropdown is deliberately not an
    # input because Auto Align is a rig-wide animator mode.
    armature = getattr(context, "object", None) if settings is not None else None
    if (
        armature is None
        or armature.type != "ARMATURE"
    ):
        return False, False
    try:
        armature_id = armature.data.get(ARMATURE_ID_KEY, "")
        targets = [
            bone
            for bone in armature.data.bones
            if _owned(bone, armature_id)
            and bone.get(ROLE_KEY) in {"HAND_IK", "FOOT_IK"}
            and bone.get(KIND_KEY) in KINDS
            and bone.get(SIDE_KEY) in SIDES
        ]
        if not targets:
            return False, False
        values = [target.get(AUTO_ALIGN_KEY, False) for target in targets]
        if any(type(value) is not bool for value in values):
            return False, False
        return True, all(values)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False, False


def _selected_auto_align_ui_state(context, settings):
    """Compatibility alias for the 0.31 panel helper; state is now global."""

    return _global_auto_align_ui_state(context, settings)


def _active_control_visual(context, *, strict=False):
    """Resolve one selected generated animator control, never a helper/source."""

    armature = getattr(context, "object", None)
    pose_bone = getattr(context, "active_pose_bone", None)
    if (
        armature is None
        or armature.type != "ARMATURE"
        or getattr(context, "mode", "") != "POSE"
        or pose_bone is None
        or not bool(getattr(pose_bone, "select", False))
    ):
        if strict:
            raise LimbIKError("Select one generated animation control in Pose Mode.")
        return None
    try:
        armature_id = armature.data.get(ARMATURE_ID_KEY, "")
        role = pose_bone.bone.get(ROLE_KEY, "")
        shape = pose_bone.custom_shape
        if (
            role not in CONTROL_VISUAL_ROLES
            or not _owned(pose_bone.bone, armature_id, role=role)
            or shape is None
            or not _owned(shape, armature_id, role="WIDGET")
        ):
            if strict:
                raise LimbIKError("The active bone is not a Character Designer animation control.")
            return None
        if strict:
            inventory = _validate_inventory(armature)
            _removal_resources(context, armature, inventory)
            expected = set()
            if inventory["master"] is not None:
                expected.add(inventory["master"].name)
            for rig in inventory["rigs"].values():
                expected.update((rig["target"].name, rig["pole"].name))
                if rig["heel"] is not None:
                    expected.add(rig["heel"].name)
            if pose_bone.name not in expected:
                raise LimbIKError("The active bone is a helper, not an animation control.")
        return armature, pose_bone
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        if strict:
            raise
        return None


def _active_limb_target(context, *, strict=False):
    """Resolve the selected visible Hand/Foot Target and its owned rig."""

    resolved = _active_control_visual(context, strict=strict)
    if resolved is None:
        return None
    armature, pose_bone = resolved
    if pose_bone.bone.get(ROLE_KEY) not in {"HAND_IK", "FOOT_IK"}:
        if strict:
            raise LimbIKError(
                "Select one generated Hand or Foot IK Target in Pose Mode."
            )
        return None
    try:
        inventory = _validate_inventory(armature)
        matching = [
            rig
            for rig in inventory["rigs"].values()
            if rig["target"].name == pose_bone.name
        ]
        if len(matching) != 1:
            raise LimbIKError(
                "The active Hand/Foot Target has no unique generated limb rig."
            )
        return armature, pose_bone, inventory, matching[0]
    except (AttributeError, KeyError, LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError):
        if strict:
            raise
        return None


def _target_rotation_has_driver(armature, target_name):
    animation = armature.animation_data
    if animation is None:
        return False
    prefix = f'pose.bones["{_rna_escape(target_name)}"].'
    paths = {
        prefix + "rotation_euler",
        prefix + "rotation_quaternion",
        prefix + "rotation_axis_angle",
    }
    return any(
        fcurve.data_path in paths
        for fcurve in getattr(animation, "drivers", ())
    )


class CHARACTERDESIGNER_OT_limb_ik_reset_target_rotation(Operator):
    bl_idname = "character_designer.limb_ik_reset_target_rotation"
    bl_label = "Reset Target Rotation"
    bl_description = "Zero only the active Hand/Foot Target's local rotation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_limb_target(context) is not None

    def execute(self, context):
        settings = _settings(context)
        target = None
        before_mode = None
        before_basis = None
        try:
            armature, target, inventory, _rig = _active_limb_target(
                context,
                strict=True,
            )
            if inventory["target_rotation_version"] != TARGET_ROTATION_VERSION:
                raise LimbIKError(
                    "Rebuild Rig once before using aligned Target rotation."
                )
            if any(target.lock_rotation) or bool(
                getattr(target, "lock_rotation_w", False)
            ):
                raise LimbIKError(
                    f"Unlock the {target.name} rotation channels before resetting them."
                )
            if _target_rotation_has_driver(armature, target.name):
                raise LimbIKError(
                    f"Reset Rotation will not override a driver on {target.name}."
                )
            before_mode = target.rotation_mode
            before_basis = target.matrix_basis.copy()
            target.rotation_mode = "XYZ"
            target.rotation_euler = (0.0, 0.0, 0.0)
            armature.update_tag(refresh={"OBJECT"})
            context.view_layer.update()
            if max(abs(float(angle)) for angle in target.rotation_euler) > 1.0e-6:
                raise LimbIKError(
                    f"{target.name} rotation could not be reset cleanly."
                )
            message = f"Reset local XYZ rotation on '{target.name}'."
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            if target is not None and before_basis is not None:
                try:
                    target.rotation_mode = before_mode
                    target.matrix_basis = before_basis
                    context.view_layer.update()
                except Exception:
                    pass
            message = str(exc)
            _set_status(settings, "WARNING", message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}


def _discard_control_shape_widget_transaction(transaction):
    if transaction is None:
        return
    for obj in reversed(transaction["objects"]):
        try:
            if bpy.data.objects.get(obj.name) is obj:
                bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass
    for mesh in reversed(transaction["meshes"]):
        try:
            if bpy.data.meshes.get(mesh.name) is mesh and not mesh.users:
                bpy.data.meshes.remove(mesh)
        except (ReferenceError, RuntimeError):
            pass


def _set_active_control_shape(operator, context, style):
    settings = _settings(context)
    pose_bone = None
    previous_shape = None
    previous_style_present = False
    previous_style = None
    connector = None
    previous_connector_hidden = None
    transaction = None
    try:
        if style not in CONTROL_SHAPE_STYLES:
            raise LimbIKError("Choose a supported control shape.")
        armature, pose_bone = _active_control_visual(context, strict=True)
        inventory = _validate_inventory(armature)
        resources = _removal_resources(context, armature, inventory)
        connector = _pole_connector_bone(inventory, pose_bone.bone)
        if connector is not None:
            previous_connector_hidden = bool(connector.hide)
        previous_shape = pose_bone.custom_shape
        previous_style_present = CONTROL_SHAPE_STYLE_KEY in pose_bone.bone
        previous_style = pose_bone.bone.get(CONTROL_SHAPE_STYLE_KEY, None)
        transaction = _new_transaction()
        available_kinds = {
            obj.get(KIND_KEY) for obj in resources["widget_objects"]
        }
        requested_kind = _expected_control_shape_kind(
            inventory,
            pose_bone.bone,
            available_kinds,
            style=style,
        )
        if requested_kind in CONTROL_SHAPE_WIDGET_KINDS:
            _ensure_widget(
                context,
                inventory["armature_id"],
                resources["widget_collection"],
                requested_kind,
                transaction,
                schema=inventory["schema"],
            )
        shape = next(
            (
                obj
                for obj in bpy.data.objects
                if _owned(obj, inventory["armature_id"], role="WIDGET")
                and obj.get(KIND_KEY) == requested_kind
            ),
            None,
        )
        if shape is None:
            raise LimbIKError(
                f"The managed {requested_kind.title()} Custom Shape is unavailable."
            )
        pose_bone.custom_shape = shape
        if style == "DEFAULT":
            if CONTROL_SHAPE_STYLE_KEY in pose_bone.bone:
                del pose_bone.bone[CONTROL_SHAPE_STYLE_KEY]
        else:
            pose_bone.bone[CONTROL_SHAPE_STYLE_KEY] = style
        _sync_pole_connector_visibility(inventory, pose_bone.bone, style)
        context.view_layer.update()
        _removal_resources(context, armature, _validate_inventory(armature))
        label = CONTROL_SHAPE_STYLE_LABELS[style]
        message = f"Set '{pose_bone.name}' display shape to {label}."
        _set_status(settings, "SUCCESS", message)
        operator.report({"INFO"}, message)
        return {"FINISHED"}
    except (KeyError, LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        if pose_bone is not None:
            try:
                pose_bone.custom_shape = previous_shape
                if previous_style_present:
                    pose_bone.bone[CONTROL_SHAPE_STYLE_KEY] = previous_style
                elif CONTROL_SHAPE_STYLE_KEY in pose_bone.bone:
                    del pose_bone.bone[CONTROL_SHAPE_STYLE_KEY]
                if connector is not None and previous_connector_hidden is not None:
                    connector.hide = previous_connector_hidden
            except Exception:
                pass
        _discard_control_shape_widget_transaction(transaction)
        message = str(exc)
        _set_status(settings, "WARNING", message)
        operator.report({"WARNING"}, message)
        return {"CANCELLED"}


class CHARACTERDESIGNER_OT_limb_ik_set_control_shape(Operator):
    bl_idname = "character_designer.limb_ik_set_control_shape"
    bl_label = "Set Control Shape"
    bl_description = "Change only the active generated control's display shape"
    bl_options = {"REGISTER", "UNDO"}

    style: EnumProperty(
        name="Shape",
        items=(
            ("DEFAULT", "Rig Default", "Restore the generated shape", "LOOP_BACK", 0),
            ("ARROW", "Arrow", "Use a managed wire arrow", "EMPTY_SINGLE_ARROW", 1),
            ("SPHERE", "Sphere Wire", "Use a managed three-ring sphere", "MESH_UVSPHERE", 2),
        ),
        default="DEFAULT",
    )

    @classmethod
    def poll(cls, context):
        return _active_control_visual(context) is not None

    def execute(self, context):
        return _set_active_control_shape(self, context, self.style)


class CHARACTERDESIGNER_OT_limb_ik_reset_control_visual(Operator):
    bl_idname = "character_designer.limb_ik_reset_control_visual"
    bl_label = "Reset Control Visual"
    bl_description = "Reset only the selected control's display offset, rotation, and scale"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_control_visual(context) is not None

    def execute(self, context):
        settings = _settings(context)
        pose_bone = None
        before = None
        before_transform = None
        try:
            _armature, pose_bone = _active_control_visual(context, strict=True)
            raw = pose_bone.bone.get(CONTROL_VISUAL_DEFAULT_KEY, None)
            if raw is None:
                raise LimbIKError(
                    "This control predates visual defaults; Rebuild Rig once before using Reset."
                )
            default = _parse_control_visual_state(raw, f"Control '{pose_bone.name}'")
            before = _control_visual_state(pose_bone)
            before_transform = pose_bone.custom_shape_transform
            # Visual defaults are stored in the control's active display
            # reference.  Auto Align retargets both the visible vectors and
            # this default together, so Reset never needs to reinterpret a
            # Target-local value in a live Hand/Foot frame.
            _apply_control_visual_state(pose_bone, default)
            message = f"Reset display-only visual settings for '{pose_bone.name}'."
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (LimbIKError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            if pose_bone is not None and before is not None:
                try:
                    pose_bone.custom_shape_transform = before_transform
                    _apply_control_visual_state(pose_bone, before)
                except Exception:
                    pass
            message = str(exc)
            _set_status(settings, "WARNING", message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}


class CHARACTERDESIGNER_PT_limb_ik(Panel):
    bl_label = "Limb IK"
    bl_idname = "CHARACTERDESIGNER_PT_limb_ik"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_RIG

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        if settings is None:
            layout.label(text="Limb IK state is unavailable.", icon="ERROR")
            return
        layout.operator("character_designer.limb_ik_analyze", text="Analyze Rig", icon="VIEWZOOM")
        layout.prop(settings, "selected_limb", text="")
        layout.prop(settings, "build_method", text="Build Method")
        armature = settings.armature
        kind, side = SELECTED_LIMBS.get(settings.selected_limb, ("ARM", "L"))
        _draw_limb_fields(layout, settings, armature, kind, side)
        layout.prop(
            settings,
            _pole_direction_field(kind, side),
            text="Pole Direction (Local)",
        )
        layout.operator(
            "character_designer.limb_ik_default_pole_direction",
            text="Default Direction",
            icon="LOOP_BACK",
        )
        row = layout.row(align=True)
        row.operator("character_designer.limb_ik_build_selected", text="Build IK", icon="CON_KINEMATIC")
        row.operator("character_designer.limb_ik_build_all", text="Build All", icon="ARMATURE_DATA")
        auto_available, auto_enabled = _global_auto_align_ui_state(context, settings)
        auto_row = layout.row()
        auto_row.enabled = auto_available
        auto_operator = auto_row.operator(
            "character_designer.limb_ik_auto_align_target",
            text="Auto Align",
            icon="CON_ROTLIKE",
            depress=auto_enabled,
        )
        auto_operator.action = "DISABLE" if auto_enabled else "ENABLE"
        row = layout.row(align=True)
        row.operator("character_designer.limb_ik_rebuild", text="Rebuild Rig", icon="FILE_REFRESH")
        remove = row.row(align=True)
        remove.alert = _active_has_owned_side_rig(context)
        remove.operator("character_designer.limb_ik_remove", text="Remove Generated Rig", icon="TRASH")


class CHARACTERDESIGNER_PT_limb_ik_target_rotation(Panel):
    bl_label = "Target Rotation"
    bl_idname = "CHARACTERDESIGNER_PT_limb_ik_target_rotation"
    bl_parent_id = "CHARACTERDESIGNER_PT_limb_ik"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY

    @classmethod
    def poll(cls, context):
        return (
            active_ui_page(context) == UI_PAGE_RIG
            and _active_limb_target(context) is not None
        )

    def draw(self, context):
        resolved = _active_limb_target(context)
        if resolved is None:
            return
        _armature, target, inventory, rig = resolved
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = True
        layout.label(text=target.name, icon="CON_ROTLIKE")
        if inventory["target_rotation_version"] != TARGET_ROTATION_VERSION:
            layout.label(
                text="Rebuild Rig once to enable aligned Target rotation",
                icon="INFO",
            )
            return
        if target.rotation_mode != "XYZ":
            layout.label(text="Rotation mode must be XYZ", icon="ERROR")
            return
        is_arm = rig["target"].get(KIND_KEY) == "ARM"
        labels = (
            ("Flex / Extend (X)", "Twist (Y)", "Side / Wave (Z)")
            if is_arm
            else ("Toe Up / Down (X)", "Bank (Y)", "Turn (Z)")
        )
        for index, label in enumerate(labels):
            layout.prop(target, "rotation_euler", index=index, text=label)
            if is_arm and index == 1:
                from . import forearm_twist
                active, message = forearm_twist.controller_status(_armature, target.name)
                layout.label(text=message, icon="CHECKMARK" if active else "INFO")
        layout.operator(
            "character_designer.limb_ik_reset_target_rotation",
            text="Reset Rotation",
            icon="LOOP_BACK",
        )


class CHARACTERDESIGNER_PT_limb_ik_control_visual(Panel):
    bl_label = "Control Visual"
    bl_idname = "CHARACTERDESIGNER_PT_limb_ik_control_visual"
    bl_parent_id = "CHARACTERDESIGNER_PT_limb_ik"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_RIG and _active_control_visual(context) is not None

    def draw(self, context):
        resolved = _active_control_visual(context)
        if resolved is None:
            return
        _armature, pose_bone = resolved
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.label(text=pose_bone.name, icon="BONE_DATA")
        shape_style = _control_shape_style(pose_bone.bone, strict=False)
        shape_row = layout.row(align=True)
        shape_row.label(text="Shape")
        shape_row.operator_menu_enum(
            "character_designer.limb_ik_set_control_shape",
            "style",
            text=CONTROL_SHAPE_STYLE_LABELS[shape_style],
            icon="MESH_UVSPHERE" if shape_style == "SPHERE" else "EMPTY_SINGLE_ARROW",
        )
        layout.prop(pose_bone, "custom_shape_translation", text="Offset")
        layout.prop(pose_bone, "custom_shape_rotation_euler", text="Rotation")
        layout.prop(pose_bone, "custom_shape_scale_xyz", text="Scale")
        reset_row = layout.row()
        reset_row.enabled = CONTROL_VISUAL_DEFAULT_KEY in pose_bone.bone
        reset_row.operator(
            "character_designer.limb_ik_reset_control_visual",
            text="Reset Visual",
            icon="LOOP_BACK",
        )


class CHARACTERDESIGNER_PT_limb_ik_direct_preroll(Panel):
    bl_label = "Direct Pre-Roll Lab"
    bl_idname = "CHARACTERDESIGNER_PT_limb_ik_direct_preroll"
    bl_parent_id = "CHARACTERDESIGNER_PT_limb_ik"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return (
            active_ui_page(context) == UI_PAGE_RIG
            and settings is not None
            and settings.build_method == "DIRECT_PREROLL"
        )

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        if settings is None:
            return
        layout.operator(
            "character_designer.limb_ik_direct_preroll_check",
            text="Check Selected Limb",
            icon="VIEWZOOM",
        )
        if not settings.direct_preroll_json:
            return
        try:
            payload = json.loads(settings.direct_preroll_json)
            result = payload["result"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return
        armature = context.object
        if (
            armature is None
            or armature.type != "ARMATURE"
            or payload.get("armature_token") != str(armature.data.as_pointer())
            or payload.get("digest") != _armature_digest(armature)
            or payload.get("selected_limb") != settings.selected_limb
        ):
            layout.label(text="Run Check again")
            return
        status_labels = {
            "ALIGNED": "Start Plane: Aligned",
            "REPLANE_REQUIRED": "Start Plane: Re-plane",
            "UNSTABLE": "Start Plane: Too Straight",
        }
        layout.label(text=status_labels.get(result.get("status"), "Start Plane: Unknown"))
        offset = result.get("rest_plane_offset_degrees")
        if offset is not None:
            layout.label(text=f"Plane Offset  {abs(float(offset)):.2f} deg")
        layout.label(text=f"Joint Shift  {float(result.get('joint_shift', 0.0)):.5g} BU")
        layout.label(
            text=(
                f"After Re-plane U/L  {float(result.get('proposed_upper_roll_degrees', 0.0)):.1f} / "
                f"{float(result.get('proposed_lower_roll_degrees', 0.0)):.1f} deg"
            )
        )


LIMB_IK_CLASSES = (
    CharacterDesignerLimbIKState,
    CHARACTERDESIGNER_OT_limb_ik_analyze,
    CHARACTERDESIGNER_OT_limb_ik_build_arm,
    CHARACTERDESIGNER_OT_limb_ik_build_leg,
    CHARACTERDESIGNER_OT_limb_ik_build_selected,
    CHARACTERDESIGNER_OT_limb_ik_build_all,
    CHARACTERDESIGNER_OT_limb_ik_remove,
    CHARACTERDESIGNER_OT_limb_ik_rebuild,
    CHARACTERDESIGNER_OT_limb_ik_default_pole_direction,
    CHARACTERDESIGNER_OT_limb_ik_auto_align_target,
    CHARACTERDESIGNER_OT_limb_ik_reset_target_rotation,
    CHARACTERDESIGNER_OT_limb_ik_set_control_shape,
    CHARACTERDESIGNER_OT_limb_ik_reset_control_visual,
    CHARACTERDESIGNER_OT_limb_ik_direct_preroll_check,
    CHARACTERDESIGNER_OT_limb_ik_flip_pole,
    CHARACTERDESIGNER_PT_limb_ik,
    CHARACTERDESIGNER_PT_limb_ik_target_rotation,
    CHARACTERDESIGNER_PT_limb_ik_control_visual,
    CHARACTERDESIGNER_PT_limb_ik_direct_preroll,
)


__all__ = (
    "CharacterDesignerLimbIKState",
    "LIMB_IK_CLASSES",
    "LimbIKError",
    "analyze_armature",
    "register_limb_ik_viewport_handler",
    "unregister_limb_ik_viewport_handler",
)
