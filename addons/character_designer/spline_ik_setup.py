"""Transactional Spline IK rig setup for a selected Armature bone chain."""

import json
import math
import textwrap
import uuid

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector

from .ui_constants import SIDEBAR_CATEGORY, rig_page_active


OWNER_KEY = "character_designer_owner"
OWNER_VALUE = "spline_ik_setup"
VERSION_KEY = "character_designer_spline_ik_version"
RIG_ID_KEY = "character_designer_spline_ik_rig_id"
ROLE_KEY = "character_designer_spline_ik_role"
CHAIN_KEY = "character_designer_spline_ik_chain"
ARMATURE_KEY = "character_designer_spline_ik_armature"
CONTROL_INDEX_KEY = "character_designer_spline_ik_control_index"
CONSTRAINT_REGISTRY_KEY = "character_designer_spline_ik_constraints"
HOOK_REGISTRY_KEY = "character_designer_spline_ik_hooks"
TILT_DRIVER_REGISTRY_KEY = "character_designer_spline_ik_tilt_drivers"
ROLL_DRIVER_REGISTRY_KEY = "character_designer_spline_ik_roll_drivers"
CONTROL_FRAME_REGISTRY_KEY = "character_designer_spline_ik_control_frames"
RIG_VERSION = 1
TILT_DRIVER_VERSION = 2
ROLL_DRIVER_VERSION = 2
LEGACY_DRIVER_VERSION = 1
CONTROL_FRAME_VERSION = 1
CONTROL_FRAME_AXIS_CONTRACT = "X_WIDTH_Y_TANGENT_Z_FRONT_NORMAL"
CONTROL_DRIVER_ROTATION_MODE = "YXZ"
LEGACY_DRIVER_ROTATION_MODE = "SWING_TWIST_Y"
HOOK_NAME_CURRENT = "CURRENT"
HOOK_NAME_LEGACY = "LEGACY"
TILT_DRIVER_VARIABLE = "twist"
EPSILON = 1.0e-8
CONTROL_FRAME_POINT_TOLERANCE = 1.0e-5
CONTROL_FRAME_CURVE_TOLERANCE = 2.0e-5


class SplineIKSetupError(ValueError):
    """An actionable rig-setup problem that can be shown to the artist."""


def _hook_display_name(index):
    """Return the short artist-facing Hook name for a zero-based point index."""

    return f"CD Spline IK Hook {index + 1:02d}"


def _legacy_hook_name(rig_id, index):
    """Return the pre-0.14.1 UUID-bearing Hook name."""

    return f"CDSplineIK_Hook_{rig_id}_{index + 1:02d}"


def _driver_rotation_mode(version):
    if version == LEGACY_DRIVER_VERSION:
        return LEGACY_DRIVER_ROTATION_MODE
    if version in {TILT_DRIVER_VERSION, ROLL_DRIVER_VERSION}:
        return CONTROL_DRIVER_ROTATION_MODE
    return None


def _armature_poll(_self, obj):
    return obj is not None and obj.type == "ARMATURE"


def _settings(context):
    manager = getattr(context, "window_manager", None)
    return getattr(manager, "character_designer_spline_ik", None)


def _set_status(settings, level, message):
    if settings is None:
        return
    settings.last_level = level
    settings.last_message = message


def _previous_capture_summary(settings):
    """Return the last still-valid capture without changing it."""
    if settings is None or settings.armature is None:
        return None
    try:
        names = _captured_names(settings)
        if not names:
            return None
        _chain_geometry(settings.armature, names)
    except (SplineIKSetupError, ReferenceError, RuntimeError, TypeError, ValueError):
        return None
    return settings.armature, names


def _canonical_chain_json(names):
    return json.dumps(list(names), ensure_ascii=False, separators=(",", ":"))


def _captured_names(settings):
    if settings is None or not settings.chain_names_json:
        return ()
    try:
        payload = json.loads(settings.chain_names_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SplineIKSetupError("The captured bone chain is corrupt; capture it again.") from exc
    if (
        not isinstance(payload, list)
        or len(payload) < 2
        or any(not isinstance(name, str) or not name for name in payload)
        or len(set(payload)) != len(payload)
    ):
        raise SplineIKSetupError("The captured bone chain is invalid; capture it again.")
    return tuple(payload)


def _selected_chain(context):
    armature = context.object
    if armature is None or armature.type != "ARMATURE":
        raise SplineIKSetupError("Select one Armature and enter Pose or Armature Edit Mode.")

    if context.mode == "POSE":
        selected = tuple(context.selected_pose_bones or ())
        selected_names = {pose_bone.name for pose_bone in selected}
        bones = armature.data.bones
    elif context.mode == "EDIT_ARMATURE":
        selected = tuple(context.selected_editable_bones or ())
        selected_names = {edit_bone.name for edit_bone in selected if edit_bone.select}
        bones = armature.data.edit_bones
    else:
        raise SplineIKSetupError("Enter Pose or Armature Edit Mode and select one bone chain.")

    if len(selected_names) < 2:
        raise SplineIKSetupError("Select at least two parent-child bones.")

    selected_bones = {name: bones.get(name) for name in selected_names}
    if any(bone is None for bone in selected_bones.values()):
        raise SplineIKSetupError("The selected bones could not be resolved on the active Armature.")

    roots = [
        bone
        for bone in selected_bones.values()
        if bone.parent is None or bone.parent.name not in selected_names
    ]
    if len(roots) != 1:
        raise SplineIKSetupError("Select one continuous parent-child chain with a single root.")

    ordered = []
    current = roots[0]
    while current is not None:
        ordered.append(current.name)
        selected_children = [
            child for child in current.children if child.name in selected_names
        ]
        if len(selected_children) > 1:
            raise SplineIKSetupError("The selected chain branches; select only one path.")
        current = selected_children[0] if selected_children else None

    if len(ordered) != len(selected_names):
        raise SplineIKSetupError("The selected bones contain a gap or a disconnected branch.")
    return armature, tuple(ordered)


def _chain_bones(armature, names):
    if armature is None or armature.type != "ARMATURE":
        raise SplineIKSetupError("The captured Armature is no longer available.")
    bones = armature.data.edit_bones if armature.mode == "EDIT" else armature.data.bones
    resolved = []
    for name in names:
        bone = bones.get(name)
        if bone is None:
            raise SplineIKSetupError(f"Bone '{name}' no longer exists; capture the chain again.")
        resolved.append(bone)
    for parent, child in zip(resolved, resolved[1:]):
        if child.parent is None or child.parent.name != parent.name:
            raise SplineIKSetupError("The captured parent-child chain changed; capture it again.")
    return tuple(resolved)


def _chain_geometry(armature, names):
    bones = _chain_bones(armature, names)
    # EditBone.head/tail are already in Armature object-local space. Bone.head/tail
    # are not the same contract (they are relative to the parent/rest hierarchy),
    # so Pose/Object Mode must use the explicit *_local coordinates. Mixing the
    # two used to collapse or offset an otherwise valid stored capture.
    if armature.mode == "EDIT":
        head = lambda bone: Vector(bone.head)
        tail = lambda bone: Vector(bone.tail)
    else:
        head = lambda bone: Vector(bone.head_local)
        tail = lambda bone: Vector(bone.tail_local)
    joints = [head(bones[0])]
    joints.extend(tail(bone) for bone in bones)
    total_bone_length = sum((tail(bone) - head(bone)).length for bone in bones)
    if total_bone_length <= EPSILON:
        raise SplineIKSetupError("The selected chain has no usable length.")
    if sum((right - left).length for left, right in zip(joints, joints[1:])) <= EPSILON:
        raise SplineIKSetupError("The selected chain joints collapse to one point.")
    return bones, tuple(joints), total_bone_length


def _sample_polyline(points, count):
    segments = []
    total = 0.0
    for left, right in zip(points, points[1:]):
        length = (right - left).length
        if length > EPSILON:
            segments.append((total, total + length, left, right))
            total += length
    if total <= EPSILON:
        raise SplineIKSetupError("The selected chain joints collapse to one point.")

    samples = []
    segment_index = 0
    for index in range(count):
        target = total * index / (count - 1)
        while segment_index < len(segments) - 1 and target > segments[segment_index][1]:
            segment_index += 1
        start_distance, end_distance, left, right = segments[segment_index]
        span = end_distance - start_distance
        factor = 0.0 if span <= EPSILON else (target - start_distance) / span
        samples.append(left.lerp(right, max(0.0, min(1.0, factor))))
    samples[0] = points[0].copy()
    samples[-1] = points[-1].copy()
    return tuple(samples)


def _point_tangent(points, index):
    if index == 0:
        tangent = points[1] - points[0]
    elif index == len(points) - 1:
        tangent = points[-1] - points[-2]
    else:
        tangent = points[index + 1] - points[index - 1]
    if tangent.length <= EPSILON:
        raise SplineIKSetupError("Two generated control points occupy the same position.")
    return tangent.normalized()


def _set_tags(target, rig_id, role, armature, chain_names, *, control_index=None):
    target[OWNER_KEY] = OWNER_VALUE
    target[VERSION_KEY] = RIG_VERSION
    target[RIG_ID_KEY] = rig_id
    target[ROLE_KEY] = role
    target[CHAIN_KEY] = _canonical_chain_json(chain_names)
    target[ARMATURE_KEY] = armature.name
    if control_index is not None:
        target[CONTROL_INDEX_KEY] = int(control_index)


def _tagged_chain(target):
    try:
        payload = json.loads(target.get(CHAIN_KEY, ""))
    except (AttributeError, ReferenceError, TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, list) or any(not isinstance(name, str) for name in payload):
        return ()
    return tuple(payload)


def _is_owned(
    target,
    *,
    rig_id=None,
    role=None,
    armature=None,
    chain_names=None,
):
    try:
        if target.get(OWNER_KEY) != OWNER_VALUE or target.get(VERSION_KEY) != RIG_VERSION:
            return False
        if rig_id is not None and target.get(RIG_ID_KEY) != rig_id:
            return False
        if role is not None and target.get(ROLE_KEY) != role:
            return False
        if armature is not None and target.get(ARMATURE_KEY) != armature.name:
            return False
        if chain_names is not None and _tagged_chain(target) != tuple(chain_names):
            return False
    except (AttributeError, ReferenceError):
        return False
    return True


def _scene_memberships(obj):
    return tuple(
        scene
        for scene in bpy.data.scenes
        if scene.objects.get(obj.name) is obj
    )


def _collection_scene_memberships(collection):
    memberships = []
    for scene in bpy.data.scenes:
        if scene.collection is collection or collection in scene.collection.children_recursive:
            memberships.append(scene)
    return tuple(memberships)


def _require_current_scene(context, armature):
    if armature is None or armature.type != "ARMATURE":
        raise SplineIKSetupError("The captured Armature is no longer available.")
    memberships = _scene_memberships(armature)
    if len(memberships) != 1 or memberships[0] is not context.scene:
        raise SplineIKSetupError(
            "The captured Armature must belong only to the current Scene."
        )
    if context.view_layer.objects.get(armature.name) is not armature:
        raise SplineIKSetupError(
            "The captured Armature is excluded from the current View Layer."
        )


def _require_generated_object_scene(context, obj):
    memberships = _scene_memberships(obj)
    if len(memberships) != 1 or memberships[0] is not context.scene:
        raise SplineIKSetupError(
            f"Generated object '{obj.name}' does not belong only to the current Scene."
        )
    for collection in obj.users_collection:
        collection_scenes = _collection_scene_memberships(collection)
        if len(collection_scenes) != 1 or collection_scenes[0] is not context.scene:
            raise SplineIKSetupError(
                f"Generated object '{obj.name}' is linked through a cross-Scene collection."
            )


def _constraint_registry(pose_bone, *, strict=False):
    raw = pose_bone.get(CONSTRAINT_REGISTRY_KEY, "")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise SplineIKSetupError(
                f"Spline IK ownership registry on bone '{pose_bone.name}' is corrupt."
            ) from exc
        return {}
    if not isinstance(payload, dict):
        if strict:
            raise SplineIKSetupError(
                f"Spline IK ownership registry on bone '{pose_bone.name}' is invalid."
            )
        return {}
    return payload


def _write_constraint_registry(pose_bone, registry):
    if registry:
        pose_bone[CONSTRAINT_REGISTRY_KEY] = json.dumps(
            registry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    elif CONSTRAINT_REGISTRY_KEY in pose_bone:
        del pose_bone[CONSTRAINT_REGISTRY_KEY]


def _constraint_name(rig_id):
    return f"CDSplineIK_{rig_id}"


def _tag_constraint(pose_bone, constraint, rig_id, armature, chain_names):
    # Blender 5.2 Constraint RNA does not support ID properties. Keep the
    # versioned custom-property ownership record on its owning PoseBone, keyed
    # by an unguessable exact constraint name, and cross-check the tagged target.
    # Never reinterpret or overwrite malformed artist/project data. A corrupt
    # registry must be repaired explicitly before any new owned entry is made.
    registry = _constraint_registry(pose_bone, strict=True)
    constraint.name = _constraint_name(rig_id)
    registry[constraint.name] = {
        "owner": OWNER_VALUE,
        "version": RIG_VERSION,
        "rig_id": rig_id,
        "role": "SPLINE_IK_CONSTRAINT",
        "armature": armature.name,
        "chain": list(chain_names),
    }
    _write_constraint_registry(pose_bone, registry)


def _constraint_record(pose_bone, constraint, armature, chain_names=None):
    record = _constraint_registry(pose_bone).get(constraint.name)
    if not isinstance(record, dict):
        return None
    rig_id = record.get("rig_id")
    if (
        constraint.type != "SPLINE_IK"
        or not isinstance(rig_id, str)
        or not rig_id
        or constraint.name != _constraint_name(rig_id)
        or record.get("owner") != OWNER_VALUE
        or record.get("version") != RIG_VERSION
        or record.get("role") != "SPLINE_IK_CONSTRAINT"
        or record.get("armature") != armature.name
        or not isinstance(record.get("chain"), list)
        or any(not isinstance(name, str) for name in record.get("chain", ()))
        or (chain_names is not None and tuple(record.get("chain", ())) != tuple(chain_names))
    ):
        return None
    target = constraint.target
    if (
        target is None
        or target.type != "CURVE"
        or target.parent is not armature
        or not _is_owned(
            target,
            rig_id=rig_id,
            role="CURVE",
            armature=armature,
            chain_names=record["chain"],
        )
        or target.data is None
        or not _is_owned(
            target.data,
            rig_id=rig_id,
            role="CURVE_DATA",
            armature=armature,
            chain_names=record["chain"],
        )
    ):
        return None
    return record


def _remove_constraint(pose_bone, constraint):
    name = constraint.name
    registry = _constraint_registry(pose_bone, strict=True)
    pose_bone.constraints.remove(constraint)
    registry.pop(name, None)
    _write_constraint_registry(pose_bone, registry)


def _owned_constraints(armature, *, rig_id=None, chain_names=None):
    matches = []
    pose = getattr(armature, "pose", None)
    if pose is None:
        return matches
    for pose_bone in pose.bones:
        for constraint in pose_bone.constraints:
            record = _constraint_record(pose_bone, constraint, armature, chain_names)
            if record is None:
                continue
            if rig_id is not None and record.get("rig_id") != rig_id:
                continue
            if chain_names is not None and tuple(record.get("chain", ())) != tuple(chain_names):
                continue
            matches.append((pose_bone, constraint))
    return matches


def _resolved_rig_id(settings, armature, names):
    requested = settings.active_rig_id if settings is not None else ""
    if requested:
        matches = _owned_constraints(armature, rig_id=requested, chain_names=names)
        if len(matches) == 1:
            return requested
    matches = _owned_constraints(armature, chain_names=names)
    rig_ids = {
        _constraint_record(pose_bone, constraint, armature, names).get("rig_id", "")
        for pose_bone, constraint in matches
    }
    rig_ids.discard("")
    return next(iter(rig_ids)) if len(rig_ids) == 1 else ""


def _target_collection(context, armature):
    candidates = []
    for collection in armature.users_collection:
        memberships = _collection_scene_memberships(collection)
        if len(memberships) == 1 and memberships[0] is context.scene:
            candidates.append(collection)
    active = getattr(context, "collection", None)
    if active in candidates:
        return active
    if candidates:
        return sorted(candidates, key=lambda collection: collection.name)[0]
    raise SplineIKSetupError(
        "No Armature collection belongs exclusively to the current Scene."
    )


def _local_controller_matrix(position, tangent):
    try:
        rotation = tangent.to_track_quat("Y", "Z").to_matrix().to_4x4()
    except ValueError as exc:
        raise SplineIKSetupError("A controller orientation could not be derived.") from exc
    return Matrix.Translation(position) @ rotation


def _tilt_data_path(index):
    return f"splines[0].bezier_points[{index}].tilt"


def _tilt_expression(baseline_tilt):
    return f"({format(float(baseline_tilt), '.17g')}) + {TILT_DRIVER_VARIABLE}"


def _curve_driver_fcurves(curve_data):
    animation_data = getattr(curve_data, "animation_data", None)
    if animation_data is None:
        return ()
    return tuple(animation_data.drivers)


def _driver_for_path(curve_data, data_path):
    matches = [
        fcurve
        for fcurve in _curve_driver_fcurves(curve_data)
        if fcurve.data_path == data_path and fcurve.array_index == 0
    ]
    return matches[0] if len(matches) == 1 else None


def _tilt_driver_registry(curve_data, *, strict=False):
    if TILT_DRIVER_REGISTRY_KEY not in curve_data:
        return None
    raw = curve_data.get(TILT_DRIVER_REGISTRY_KEY)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise SplineIKSetupError("The Rotation Tilt driver registry is corrupt.") from exc
        return False
    if not isinstance(payload, list):
        if strict:
            raise SplineIKSetupError("The Rotation Tilt driver registry is invalid.")
        return False
    return payload


def _roll_driver_registry(curve_data, *, strict=False):
    if ROLL_DRIVER_REGISTRY_KEY not in curve_data:
        return None
    raw = curve_data.get(ROLL_DRIVER_REGISTRY_KEY)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise SplineIKSetupError("The bone Roll driver registry is corrupt.") from exc
        return False
    if not isinstance(payload, list):
        if strict:
            raise SplineIKSetupError("The bone Roll driver registry is invalid.")
        return False
    return payload


def _control_frame_registry(curve_data, *, strict=False):
    if CONTROL_FRAME_REGISTRY_KEY not in curve_data:
        return None
    raw = curve_data.get(CONTROL_FRAME_REGISTRY_KEY)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise SplineIKSetupError("The control-axis frame registry is corrupt.") from exc
        return False
    if not isinstance(payload, list):
        if strict:
            raise SplineIKSetupError("The control-axis frame registry is invalid.")
        return False
    return payload


def _armature_driver_for_path(armature, data_path, array_index=1):
    animation_data = armature.animation_data
    if animation_data is None:
        return None
    matches = [
        fcurve
        for fcurve in animation_data.drivers
        if fcurve.data_path == data_path and fcurve.array_index == array_index
    ]
    return matches[0] if len(matches) == 1 else None


def _control_weight_map(parameter, control_count):
    scaled = max(0.0, min(1.0, float(parameter))) * (control_count - 1)
    left = min(control_count - 1, int(math.floor(scaled)))
    right = min(control_count - 1, left + 1)
    factor = scaled - left
    weights = {left: 1.0 - factor}
    if right != left and factor > EPSILON:
        weights[right] = factor
    return weights


def _bone_twist_coefficients(armature, chain_names, control_count):
    bones = _chain_bones(armature, chain_names)
    if armature.mode == "EDIT":
        head = lambda bone: Vector(bone.head)
        tail = lambda bone: Vector(bone.tail)
    else:
        head = lambda bone: Vector(bone.head_local)
        tail = lambda bone: Vector(bone.tail_local)
    lengths = tuple((tail(bone) - head(bone)).length for bone in bones)
    total = sum(lengths)
    if total <= EPSILON:
        raise SplineIKSetupError("The captured chain has no usable length for Roll drivers.")

    result = []
    previous_weights = {}
    distance = 0.0
    for bone, length in zip(bones, lengths):
        current_weights = _control_weight_map(
            (distance + length * 0.5) / total,
            control_count,
        )
        coefficients = {
            index: current_weights.get(index, 0.0) - previous_weights.get(index, 0.0)
            for index in set(current_weights) | set(previous_weights)
        }
        coefficients = {
            index: coefficient
            for index, coefficient in coefficients.items()
            if abs(coefficient) > EPSILON
        }
        result.append((bone.name, coefficients))
        previous_weights = current_weights
        distance += length
    return tuple(result)


def _roll_expression(baseline_y, coefficients):
    expression = f"({format(float(baseline_y), '.17g')})"
    for index, coefficient in sorted(coefficients.items()):
        expression += (
            f" + ({format(float(coefficient), '.17g')})*"
            f"{TILT_DRIVER_VARIABLE}_{index}"
        )
    return expression


def _matrix_to_json(matrix):
    return [float(value) for row in matrix for value in row]


def _matrix_from_json(values):
    if (
        not isinstance(values, list)
        or len(values) != 16
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for value in values
        )
    ):
        raise SplineIKSetupError("A stored bone Roll baseline matrix is invalid.")
    return Matrix(tuple(tuple(float(values[row * 4 + column]) for column in range(4)) for row in range(4)))


def _chain_rotation_paths(armature, chain_names):
    pose = getattr(armature, "pose", None)
    if pose is None:
        raise SplineIKSetupError("The captured Armature Pose is unavailable.")
    paths = []
    for name in chain_names:
        pose_bone = pose.bones.get(name)
        if pose_bone is None:
            raise SplineIKSetupError(f"Pose Bone '{name}' is unavailable for Roll drivers.")
        paths.append(pose_bone.path_from_id("rotation_euler"))
    return tuple(paths)


def _chain_all_rotation_paths(armature, chain_names):
    pose = getattr(armature, "pose", None)
    if pose is None:
        raise SplineIKSetupError("The captured Armature Pose is unavailable.")
    paths = set()
    for name in chain_names:
        pose_bone = pose.bones.get(name)
        if pose_bone is None:
            raise SplineIKSetupError(f"Pose Bone '{name}' is unavailable for Roll drivers.")
        for property_name in (
            "rotation_euler",
            "rotation_quaternion",
            "rotation_axis_angle",
        ):
            paths.add(pose_bone.path_from_id(property_name))
    return paths


def _action_may_animate_paths(action, paths):
    if action is None:
        return False
    fcurves = getattr(action, "fcurves", None)
    if fcurves is None:
        # Blender 5.2 layered Actions cannot always be inspected through the
        # legacy collection. Refuse rather than silently override artist data.
        return True
    return any(fcurve.data_path in paths for fcurve in fcurves)


def _armature_roll_animation_conflict(armature, paths):
    animation_data = armature.animation_data
    if animation_data is None:
        return False
    if _action_may_animate_paths(animation_data.action, paths):
        return True
    for track in animation_data.nla_tracks:
        for strip in track.strips:
            if _action_may_animate_paths(strip.action, paths):
                return True
    return False


def _armature_rotation_animation_conflict(armature, paths):
    animation_data = armature.animation_data
    if animation_data is None:
        return False
    if any(fcurve.data_path in paths for fcurve in animation_data.drivers):
        return True
    if _action_may_animate_paths(animation_data.action, paths):
        return True
    for track in animation_data.nla_tracks:
        for strip in track.strips:
            if _action_may_animate_paths(strip.action, paths):
                return True
    return False


def _curve_tilt_animation_conflict(curve_data, paths):
    animation_data = curve_data.animation_data
    if animation_data is None:
        return False
    if _action_may_animate_paths(animation_data.action, paths):
        return True
    for track in animation_data.nla_tracks:
        for strip in track.strips:
            if _action_may_animate_paths(strip.action, paths):
                return True
    return False


def _control_point_spline(curve_data, controls):
    if len(curve_data.splines) != 1:
        raise SplineIKSetupError(
            "Rotation Tilt requires the generated Curve to contain exactly one spline."
        )
    spline = curve_data.splines[0]
    if spline.type != "BEZIER" or len(spline.bezier_points) != len(controls):
        raise SplineIKSetupError(
            "Rotation Tilt requires one generated Bezier point per Hook control."
        )
    return spline


def _control_frame_state(curve_obj, controls, rig_id):
    """Return whether the Curve/strap-aware local controller frames are installed."""

    registry = _control_frame_registry(curve_obj.data)
    if registry is None:
        return "MISSING", "This setup still uses the legacy controller-axis frame."
    if registry is False or len(registry) != len(controls):
        return "INVALID", "The control-axis frame registry is incomplete or corrupt."
    for index, (record, control) in enumerate(zip(registry, controls)):
        if (
            not isinstance(record, dict)
            or record.get("owner") != OWNER_VALUE
            or record.get("version") != CONTROL_FRAME_VERSION
            or record.get("rig_id") != rig_id
            or record.get("role") != "CONTROL_FRAME"
            or record.get("control_index") != index
            or record.get("target") != control.name
            or record.get("axis_contract") != CONTROL_FRAME_AXIS_CONTRACT
        ):
            return "INVALID", "The control-axis frame registry no longer matches the rig."
    return "ENABLED", ""


def _signed_angle_about_axis(source, target, axis):
    source = Vector(source)
    target = Vector(target)
    axis = Vector(axis)
    if axis.length <= EPSILON:
        raise SplineIKSetupError("A control-frame tangent is degenerate.")
    axis.normalize()
    source -= axis * source.dot(axis)
    target -= axis * target.dot(axis)
    if source.length <= EPSILON or target.length <= EPSILON:
        raise SplineIKSetupError("A control-frame normal is parallel to its tangent.")
    source.normalize()
    target.normalize()
    return math.atan2(axis.dot(source.cross(target)), max(-1.0, min(1.0, source.dot(target))))


def _evaluated_pose_axis_samples(armature, chain_names, depsgraph):
    evaluated_armature = armature.evaluated_get(depsgraph)
    armature_world = evaluated_armature.matrix_world.copy()
    cumulative = 0.0
    midpoints = []
    for name in chain_names:
        pose_bone = evaluated_armature.pose.bones.get(name)
        if pose_bone is None:
            raise SplineIKSetupError(
                f"Pose Bone '{name}' is unavailable while deriving the strap frame."
            )
        head = armature_world @ Vector(pose_bone.head)
        tail = armature_world @ Vector(pose_bone.tail)
        tangent = tail - head
        length = tangent.length
        if length <= EPSILON:
            raise SplineIKSetupError(
                f"Pose Bone '{name}' has no evaluated length for control-axis alignment."
            )
        tangent.normalize()
        bone_world = armature_world @ pose_bone.matrix
        width = Vector(bone_world.col[0].xyz)
        front = Vector(bone_world.col[2].xyz)
        front -= tangent * front.dot(tangent)
        if front.length <= EPSILON:
            front = width.cross(tangent)
        if front.length <= EPSILON:
            raise SplineIKSetupError(
                f"Pose Bone '{name}' has no usable front normal for control-axis alignment."
            )
        front.normalize()
        resolved_width = tangent.cross(front)
        if resolved_width.length <= EPSILON:
            raise SplineIKSetupError(
                f"Pose Bone '{name}' has no usable width axis for control-axis alignment."
            )
        resolved_width.normalize()
        if width.length > EPSILON and resolved_width.dot(width.normalized()) < 0.0:
            resolved_width.negate()
            front.negate()
        midpoints.append((cumulative + length * 0.5, resolved_width, front))
        cumulative += length
    if not midpoints or cumulative <= EPSILON:
        raise SplineIKSetupError("The evaluated chain has no usable strap frame.")
    return (
        (0.0, midpoints[0][1].copy(), midpoints[0][2].copy()),
        *midpoints,
        (cumulative, midpoints[-1][1].copy(), midpoints[-1][2].copy()),
    )


def _interpolated_strap_frame(axis_samples, distance, tangent):
    tangent = Vector(tangent)
    if tangent.length <= EPSILON:
        raise SplineIKSetupError("A Curve control point has no usable tangent.")
    tangent.normalize()
    clamped = max(axis_samples[0][0], min(float(distance), axis_samples[-1][0]))
    right_index = 1
    while right_index < len(axis_samples) and clamped > axis_samples[right_index][0]:
        right_index += 1
    right_index = min(right_index, len(axis_samples) - 1)
    left_distance, left_width, left_front = axis_samples[right_index - 1]
    right_distance, right_width, right_front = axis_samples[right_index]
    span = right_distance - left_distance
    factor = 0.0 if span <= EPSILON else (clamped - left_distance) / span
    front = left_front.lerp(right_front, factor)
    width_hint = left_width.lerp(right_width, factor)
    front -= tangent * front.dot(tangent)
    if front.length <= EPSILON:
        front = width_hint.cross(tangent)
    if front.length <= EPSILON:
        raise SplineIKSetupError("The interpolated strap front is parallel to the Curve tangent.")
    front.normalize()
    width = tangent.cross(front)
    if width.length <= EPSILON:
        raise SplineIKSetupError("The interpolated strap width axis is degenerate.")
    width.normalize()
    if width_hint.length > EPSILON and width.dot(width_hint.normalized()) < 0.0:
        width.negate()
        front.negate()
    front = width.cross(tangent)
    front.normalize()
    return width, front


def _frame_world_matrix(position, width, tangent, front, source_matrix):
    source_linear = source_matrix.to_3x3()
    determinant = source_linear.determinant()
    scales = tuple(Vector(source_linear.col[index]).length for index in range(3))
    if determinant <= EPSILON or any(value <= EPSILON for value in scales):
        raise SplineIKSetupError(
            "A controller has a singular or mirrored world transform; control axes were not changed."
        )
    return Matrix(
        (
            (width.x * scales[0], tangent.x * scales[1], front.x * scales[2], position.x),
            (width.y * scales[0], tangent.y * scales[1], front.y * scales[2], position.y),
            (width.z * scales[0], tangent.z * scales[1], front.z * scales[2], position.z),
            (0.0, 0.0, 0.0, 1.0),
        )
    )


def _evaluated_curve_profile_normals(context, curve_obj, expected_count):
    """Return one real Curve cross-section direction per evaluated sample.

    A Curve converted without bevel has only loose edges.  Blender reports the
    edge tangent (and zero at some endpoints) through ``MeshVertex.normal``;
    that value is not the Curve's Minimum-twist normal and does not respond to
    Bezier-point Tilt.  Probe a temporary copy with a four-vertex
    round bevel instead.  The first vertex of each ring is a stable evaluated
    profile direction and already includes Hooks, Minimum twist, and Tilt.

    The probe is linked only long enough for Blender to evaluate the same Hook
    stack, is never selected, and is removed before this helper returns.  It
    cannot persist into a saved file.
    """

    probe_curve = None
    probe_object = None
    probe_mesh = None
    evaluated_probe = None
    try:
        probe_curve = curve_obj.data.copy()
        probe_curve.dimensions = "3D"
        probe_curve.bevel_mode = "ROUND"
        probe_curve.bevel_depth = 1.0e-3
        probe_curve.bevel_resolution = 0
        probe_curve.extrude = 0.0
        probe_curve.offset = 0.0
        probe_curve.taper_object = None
        probe_curve.bevel_object = None
        probe_curve.use_fill_caps = False
        probe_curve.bevel_factor_start = 0.0
        probe_curve.bevel_factor_end = 1.0
        probe_object = curve_obj.copy()
        probe_object.name = "CharacterDesigner_ControlFrameProbe"
        probe_object.data = probe_curve
        for key in tuple(probe_object.keys()):
            del probe_object[key]
        for key in tuple(probe_curve.keys()):
            del probe_curve[key]
        context.scene.collection.objects.link(probe_object)
        context.view_layer.update()
        evaluated_probe = probe_object.evaluated_get(context.evaluated_depsgraph_get())
        probe_mesh = evaluated_probe.to_mesh()
        ring_size = 4
        if len(probe_mesh.vertices) != expected_count * ring_size:
            raise SplineIKSetupError(
                "The generated Curve profile evaluation topology changed; "
                "control axes were not guessed."
            )
        curve_world = evaluated_probe.matrix_world.copy()
        curve_linear = curve_world.to_3x3()
        normals = []
        centers = []
        for sample_index in range(expected_count):
            start = sample_index * ring_size
            ring = tuple(
                Vector(probe_mesh.vertices[start + offset].co)
                for offset in range(ring_size)
            )
            center = sum(ring, Vector()) / ring_size
            direction = curve_linear @ (ring[0] - center)
            if direction.length <= EPSILON:
                raise SplineIKSetupError(
                    f"Curve sample {sample_index + 1} has no usable evaluated profile normal."
                )
            direction.normalize()
            centers.append(curve_world @ center)
            normals.append(direction)
        return tuple(normals), tuple(centers)
    finally:
        cleanup_errors = []
        if probe_object is not None:
            if probe_mesh is not None and evaluated_probe is not None:
                try:
                    evaluated_probe.to_mesh_clear()
                except (ReferenceError, RuntimeError) as exc:
                    cleanup_errors.append(exc)
            try:
                if bpy.data.objects.get(probe_object.name) is probe_object:
                    bpy.data.objects.remove(probe_object, do_unlink=True)
            except (ReferenceError, RuntimeError) as exc:
                cleanup_errors.append(exc)
        if (
            probe_curve is not None
            and bpy.data.curves.get(probe_curve.name) is probe_curve
        ):
            try:
                bpy.data.curves.remove(probe_curve)
            except (ReferenceError, RuntimeError) as exc:
                cleanup_errors.append(exc)
        try:
            context.view_layer.update()
        except (ReferenceError, RuntimeError) as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            raise SplineIKSetupError(
                "The temporary Curve-profile probe could not be cleaned up completely."
            ) from cleanup_errors[0]


def _evaluated_control_frame_plan(context, curve_obj, controls):
    """Capture Curve tangents plus the evaluated deform-chain width/front frame.

    PoseBone X/Z carry the generated strap's authored width/front orientation.
    A temporary four-vertex bevel probe supplies the Curve's actual evaluated
    Minimum-twist profile direction after Hooks and point Tilt.  The returned
    Tilt baselines rotate that real Curve direction onto the strap front while
    the Object matrices make the visible Empty axes express the same X/Y/Z
    contract.
    """

    spline = _control_point_spline(curve_obj.data, controls)
    if spline.use_cyclic_u:
        raise SplineIKSetupError("Control-axis alignment requires an open generated Curve.")
    resolution = int(spline.resolution_u)
    if resolution < 1:
        raise SplineIKSetupError("The generated Curve has no usable evaluation resolution.")
    expected_vertex_count = (len(controls) - 1) * resolution + 1
    depsgraph = context.evaluated_depsgraph_get()
    evaluated_curve = curve_obj.evaluated_get(depsgraph)
    mesh = evaluated_curve.to_mesh()
    try:
        if len(mesh.vertices) != expected_vertex_count:
            raise SplineIKSetupError(
                "The generated Curve evaluation topology changed; control axes were not guessed."
            )
        curve_world = evaluated_curve.matrix_world.copy()
        positions = tuple(curve_world @ Vector(vertex.co) for vertex in mesh.vertices)
        evaluated_tilts = tuple(
            float(point.tilt)
            for point in evaluated_curve.data.splines[0].bezier_points
        )
    finally:
        evaluated_curve.to_mesh_clear()
    profile_normals, profile_centers = _evaluated_curve_profile_normals(
        context,
        curve_obj,
        expected_vertex_count,
    )
    depsgraph = context.evaluated_depsgraph_get()
    cumulative = [0.0]
    for left, right in zip(positions, positions[1:]):
        cumulative.append(cumulative[-1] + (right - left).length)
    axis_samples = _evaluated_pose_axis_samples(
        curve_obj.parent,
        _tagged_chain(curve_obj),
        depsgraph,
    )
    frames = []
    neutral_tilts = []
    front_normals = []
    curve_normals = []
    for control_index, control in enumerate(controls):
            vertex_index = control_index * resolution
            if vertex_index == 0:
                sampled_tangent = positions[1] - positions[0]
            elif vertex_index == len(positions) - 1:
                sampled_tangent = positions[-1] - positions[-2]
            else:
                sampled_tangent = positions[vertex_index + 1] - positions[vertex_index - 1]
            if sampled_tangent.length <= EPSILON:
                raise SplineIKSetupError(
                    f"Curve point {control_index + 1} has no usable evaluated tangent."
                )
            sampled_tangent.normalize()
            # Each generated Hook binds the point and both Bezier handles to the
            # same Empty.  Its current world Y is therefore the exact derivative
            # direction at the control point; the evaluated polyline chord is
            # only an approximation and is several degrees off at curved ends.
            tangent = Vector(control.matrix_world.col[1].xyz)
            if tangent.length <= EPSILON:
                raise SplineIKSetupError(
                    f"Control '{control.name}' has no usable tangent axis."
                )
            tangent.normalize()
            # The evaluated chord is only a resolution-limited approximation
            # to the Hooked Bezier derivative; curved endpoints in a posed v1
            # rig can differ by several degrees while the control Y axis is
            # still the exact point/handle transform.
            if tangent.dot(sampled_tangent) < 0.95:
                raise SplineIKSetupError(
                    f"Control '{control.name}' no longer follows its Bezier tangent."
                )
            current_normal = Vector(profile_normals[vertex_index])
            current_normal -= tangent * current_normal.dot(tangent)
            if current_normal.length <= EPSILON:
                raise SplineIKSetupError(
                    f"Curve point {control_index + 1} has no usable evaluated normal."
                )
            current_normal.normalize()
            current_tilt = evaluated_tilts[control_index]
            width, front = _interpolated_strap_frame(
                axis_samples,
                cumulative[vertex_index],
                tangent,
            )
            curve_position = positions[vertex_index]
            control_position = control.matrix_world.translation.copy()
            position_scale = max(1.0, cumulative[-1])
            if (curve_position - control_position).length > (
                CONTROL_FRAME_POINT_TOLERANCE * position_scale
            ):
                raise SplineIKSetupError(
                    f"Control '{control.name}' no longer coincides with its hooked Curve point."
                )
            if (profile_centers[vertex_index] - curve_position).length > (
                CONTROL_FRAME_POINT_TOLERANCE * position_scale
            ):
                raise SplineIKSetupError(
                    "The temporary Curve profile no longer matches the evaluated centerline."
                )
            frames.append(
                _frame_world_matrix(
                    control_position,
                    width,
                    tangent,
                    front,
                    control.matrix_world,
                )
            )
            neutral_tilts.append(
                current_tilt
                + _signed_angle_about_axis(current_normal, front, tangent)
            )
            front_normals.append(front.copy())
            curve_normals.append(current_normal.copy())
    return {
        "frames": tuple(frames),
        "tilts": tuple(neutral_tilts),
        "front_normals": tuple(front_normals),
        "curve_normals": tuple(curve_normals),
        "curve_positions": positions,
        "control_vertex_indices": tuple(
            index * resolution for index in range(len(controls))
        ),
    }


def _curve_tilt_state(curve_obj, controls, rig_id):
    """Return the owned Curve-point Tilt driver state without mutating.

    Old version-1 rigs deliberately have no Rotation Tilt registry and are
    therefore MISSING rather than invalid.  A partial/foreign driver on one of
    the generated point Tilt paths is never adopted or overwritten.
    """

    try:
        curve_data = curve_obj.data
        spline = _control_point_spline(curve_data, controls)
        expected_paths = {
            _tilt_data_path(index) for index in range(len(spline.bezier_points))
        }
        path_drivers = [
            fcurve
            for fcurve in _curve_driver_fcurves(curve_data)
            if fcurve.data_path in expected_paths
        ]
        registry = _tilt_driver_registry(curve_data)
        if registry is None:
            if path_drivers:
                return "INVALID", "Bezier Tilt already has unregistered driver data."
            animation_data = curve_data.animation_data
            if animation_data is not None and (
                animation_data.action is not None or animation_data.nla_tracks
            ):
                return (
                    "INVALID",
                    "Curve Data already has artist animation; Rotation Tilt will not override it.",
                )
            return "MISSING", ""
        if registry is False or len(registry) != len(controls):
            return "INVALID", "The Rotation Tilt driver registry is incomplete or corrupt."

        seen_paths = set()
        for index, record in enumerate(registry):
            if not isinstance(record, dict):
                return "INVALID", "The Rotation Tilt registry contains an invalid entry."
            path = _tilt_data_path(index)
            control = controls[index]
            baseline = record.get("baseline_tilt")
            driver_version = record.get("version")
            driver_rotation_mode = _driver_rotation_mode(driver_version)
            if (
                record.get("data_path") != path
                or record.get("owner") != OWNER_VALUE
                or driver_version not in {LEGACY_DRIVER_VERSION, TILT_DRIVER_VERSION}
                or driver_rotation_mode is None
                or record.get("rig_id") != rig_id
                or record.get("role") != "ROTATION_TILT"
                or record.get("control_index") != index
                or record.get("target") != control.name
                or not isinstance(baseline, (int, float))
                or isinstance(baseline, bool)
                or not math.isfinite(float(baseline))
                or path in seen_paths
            ):
                return "INVALID", "The Rotation Tilt registry no longer matches the rig."
            seen_paths.add(path)

            fcurve = _driver_for_path(curve_data, path)
            if fcurve is None:
                return "INVALID", "A registered Rotation Tilt driver is missing or duplicated."
            driver = fcurve.driver
            variables = tuple(driver.variables)
            if (
                driver.type != "SCRIPTED"
                or driver.expression != _tilt_expression(baseline)
                or driver.use_self
                or len(variables) != 1
            ):
                return "INVALID", "A Rotation Tilt driver was edited outside Character Designer."
            variable = variables[0]
            targets = tuple(variable.targets)
            if (
                variable.name != TILT_DRIVER_VARIABLE
                or variable.type != "TRANSFORMS"
                or len(targets) != 1
                or targets[0].id is not control
                or targets[0].transform_type != "ROT_Y"
                or targets[0].transform_space != "LOCAL_SPACE"
                or targets[0].rotation_mode != driver_rotation_mode
            ):
                return "INVALID", "A Rotation Tilt driver target no longer matches its control."

        if len(path_drivers) != len(controls):
            return "INVALID", "The generated Tilt paths contain unregistered drivers."
        return "ENABLED", ""
    except (
        AttributeError,
        ReferenceError,
        RuntimeError,
        SplineIKSetupError,
        TypeError,
        ValueError,
    ) as exc:
        return "INVALID", str(exc)


def _bone_roll_state(curve_obj, controls, rig_id):
    """Return the interpolated PoseBone Roll driver state without mutating."""

    try:
        armature = curve_obj.parent
        chain_names = _tagged_chain(curve_obj)
        if armature is None or armature.type != "ARMATURE" or not chain_names:
            return "INVALID", "The generated Curve no longer resolves its Armature chain."
        expected = _bone_twist_coefficients(armature, chain_names, len(controls))
        paths = _chain_rotation_paths(armature, chain_names)
        path_drivers = [
            fcurve
            for fcurve in (
                tuple(armature.animation_data.drivers)
                if armature.animation_data is not None
                else ()
            )
            if fcurve.data_path in paths and fcurve.array_index == 1
        ]
        registry = _roll_driver_registry(curve_obj.data)
        if registry is None:
            if path_drivers:
                return "INVALID", "The captured bone Roll channels already have drivers."
            if _armature_roll_animation_conflict(armature, set(paths)):
                return "INVALID", "Artist animation already uses a captured bone Roll channel."
            return "MISSING", ""
        if registry is False or len(registry) != len(chain_names):
            return "INVALID", "The bone Roll driver registry is incomplete or corrupt."

        for bone_index, (record, (bone_name, expected_coefficients)) in enumerate(
            zip(registry, expected)
        ):
            if not isinstance(record, dict):
                return "INVALID", "The bone Roll registry contains an invalid entry."
            path = paths[bone_index]
            baseline = record.get("baseline_y")
            driver_version = record.get("version")
            driver_rotation_mode = _driver_rotation_mode(driver_version)
            coefficient_records = record.get("coefficients")
            if not isinstance(coefficient_records, list):
                return "INVALID", "A bone Roll coefficient record is invalid."
            coefficients = {}
            for coefficient_record in coefficient_records:
                if not isinstance(coefficient_record, dict):
                    return "INVALID", "A bone Roll coefficient record is invalid."
                control_index = coefficient_record.get("control_index")
                weight = coefficient_record.get("weight")
                if (
                    not isinstance(control_index, int)
                    or isinstance(control_index, bool)
                    or control_index in coefficients
                    or control_index < 0
                    or control_index >= len(controls)
                    or not isinstance(weight, (int, float))
                    or isinstance(weight, bool)
                    or not math.isfinite(float(weight))
                ):
                    return "INVALID", "A bone Roll coefficient record is invalid."
                coefficients[control_index] = float(weight)
            if set(coefficients) != set(expected_coefficients) or any(
                abs(coefficients[index] - expected_coefficients[index]) > 1.0e-8
                for index in coefficients
            ):
                return "INVALID", "The bone Roll interpolation no longer matches the chain."
            try:
                _matrix_from_json(record.get("baseline_matrix"))
            except SplineIKSetupError as exc:
                return "INVALID", str(exc)
            pose_bone = armature.pose.bones.get(bone_name)
            if (
                record.get("data_path") != path
                or record.get("owner") != OWNER_VALUE
                or driver_version not in {LEGACY_DRIVER_VERSION, ROLL_DRIVER_VERSION}
                or driver_rotation_mode is None
                or record.get("rig_id") != rig_id
                or record.get("role") != "BONE_ROLL"
                or record.get("bone_index") != bone_index
                or record.get("bone") != bone_name
                or not isinstance(record.get("original_rotation_mode"), str)
                or not isinstance(baseline, (int, float))
                or isinstance(baseline, bool)
                or not math.isfinite(float(baseline))
                or pose_bone is None
                or pose_bone.rotation_mode != "XYZ"
            ):
                return "INVALID", "The bone Roll registry no longer matches the rig."

            fcurve = _armature_driver_for_path(armature, path)
            if fcurve is None:
                return "INVALID", "A registered bone Roll driver is missing or duplicated."
            driver = fcurve.driver
            variables = tuple(driver.variables)
            sorted_coefficients = sorted(coefficients)
            if (
                driver.type != "SCRIPTED"
                or driver.expression != _roll_expression(baseline, coefficients)
                or driver.use_self
                or len(variables) != len(sorted_coefficients)
            ):
                return "INVALID", "A bone Roll driver was edited outside Character Designer."
            for variable, control_index in zip(variables, sorted_coefficients):
                targets = tuple(variable.targets)
                if (
                    variable.name != f"{TILT_DRIVER_VARIABLE}_{control_index}"
                    or variable.type != "TRANSFORMS"
                    or len(targets) != 1
                    or targets[0].id is not controls[control_index]
                    or targets[0].transform_type != "ROT_Y"
                    or targets[0].transform_space != "LOCAL_SPACE"
                    or targets[0].rotation_mode != driver_rotation_mode
                ):
                    return "INVALID", "A bone Roll driver target no longer matches its control."

        if len(path_drivers) != len(chain_names):
            return "INVALID", "The captured bone Roll channels have unregistered drivers."
        return "ENABLED", ""
    except (
        AttributeError,
        ReferenceError,
        RuntimeError,
        SplineIKSetupError,
        TypeError,
        ValueError,
    ) as exc:
        return "INVALID", str(exc)


def _rotation_tilt_state(curve_obj, controls, rig_id):
    """Return the complete Curve Tilt + interpolated bone Roll driver state."""

    curve_state, curve_detail = _curve_tilt_state(curve_obj, controls, rig_id)
    roll_state, roll_detail = _bone_roll_state(curve_obj, controls, rig_id)
    if curve_state == "ENABLED" and roll_state == "ENABLED":
        tilt_registry = _tilt_driver_registry(curve_obj.data)
        roll_registry = _roll_driver_registry(curve_obj.data)
        tilt_versions = {record.get("version") for record in tilt_registry}
        roll_versions = {record.get("version") for record in roll_registry}
        if (
            len(tilt_versions) != 1
            or len(roll_versions) != 1
            or tilt_versions != roll_versions
        ):
            return (
                "INVALID",
                "Curve Tilt and bone Roll driver versions are mixed; the rig was not changed.",
            )
        version = next(iter(tilt_versions))
        frame_state, frame_detail = _control_frame_state(curve_obj, controls, rig_id)
        if version == LEGACY_DRIVER_VERSION and frame_state != "MISSING":
            return (
                "INVALID",
                frame_detail
                or "Legacy Swing drivers conflict with v2 control-axis metadata.",
            )
        if version == TILT_DRIVER_VERSION and frame_state != "ENABLED":
            return (
                "INVALID",
                frame_detail
                or "The v2 Rotation Tilt control-axis metadata is missing.",
            )
        return "ENABLED", ""
    if curve_state == "MISSING" and roll_state == "MISSING":
        return "MISSING", ""
    if curve_state == "INVALID":
        return "INVALID", curve_detail
    if roll_state == "INVALID":
        return "INVALID", roll_detail
    return "INVALID", "Rotation Tilt is only partially installed on this generated setup."


def _rotation_tilt_contract_version(curve_data):
    """Return the one validated driver-contract version installed on a rig."""

    tilt_registry = _tilt_driver_registry(curve_data, strict=True)
    roll_registry = _roll_driver_registry(curve_data, strict=True)
    if not tilt_registry or not roll_registry:
        raise SplineIKSetupError("Rotation Tilt is not completely installed on this rig.")
    versions = {
        *(record.get("version") for record in tilt_registry),
        *(record.get("version") for record in roll_registry),
    }
    if len(versions) != 1:
        raise SplineIKSetupError(
            "Curve Tilt and bone Roll driver versions are mixed; the rig was not changed."
        )
    version = versions.pop()
    if version not in {LEGACY_DRIVER_VERSION, TILT_DRIVER_VERSION}:
        raise SplineIKSetupError("The Rotation Tilt driver version is unsupported.")
    return version


def _snapshot_control_transform(control):
    return {
        "matrix_parent_inverse": control.matrix_parent_inverse.copy(),
        "matrix_basis": control.matrix_basis.copy(),
        "matrix_world": control.matrix_world.copy(),
        "rotation_mode": control.rotation_mode,
        "delta_location": control.delta_location.copy(),
        "delta_rotation_euler": control.delta_rotation_euler.copy(),
        "delta_rotation_quaternion": control.delta_rotation_quaternion.copy(),
        "delta_scale": control.delta_scale.copy(),
        "show_axis": bool(control.show_axis),
    }


def _restore_control_transform(control, snapshot):
    control.rotation_mode = snapshot["rotation_mode"]
    control.delta_location = snapshot["delta_location"]
    control.delta_rotation_euler = snapshot["delta_rotation_euler"]
    control.delta_rotation_quaternion = snapshot["delta_rotation_quaternion"]
    control.delta_scale = snapshot["delta_scale"]
    control.show_axis = snapshot["show_axis"]
    control.matrix_parent_inverse = snapshot["matrix_parent_inverse"]
    control.matrix_basis = snapshot["matrix_basis"]


def _rebase_control_to_current_pose(control):
    """Make the visible current transform the control's zero-delta rest frame."""

    rest_matrix = control.matrix_parent_inverse @ control.matrix_basis
    if any(not math.isfinite(value) for row in rest_matrix for value in row):
        raise SplineIKSetupError(
            f"Control '{control.name}' has a non-finite transform and cannot drive Tilt."
        )
    if abs(rest_matrix.to_3x3().determinant()) <= EPSILON:
        raise SplineIKSetupError(
            f"Control '{control.name}' has a singular transform; remove zero scale first."
        )

    # matrix_parent_inverse participates in the visible local frame but is not
    # reported as an Object transform channel.  Folding the complete current
    # pose into it leaves matrix_world and every Hook result unchanged while
    # making local rotation Y start at exactly zero.
    control.delta_location = (0.0, 0.0, 0.0)
    control.delta_rotation_euler = (0.0, 0.0, 0.0)
    control.delta_rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    control.delta_scale = (1.0, 1.0, 1.0)
    control.rotation_mode = CONTROL_DRIVER_ROTATION_MODE
    control.matrix_parent_inverse = rest_matrix
    control.matrix_basis = Matrix.Identity(4)


def _control_has_legacy_upgrade_conflict(control):
    animation_data = control.animation_data
    if animation_data is not None and (
        animation_data.action is not None
        or animation_data.drivers
        or animation_data.nla_tracks
    ):
        return True
    if control.constraints:
        return True
    if control.delta_location.length > EPSILON:
        return True
    if any(abs(value) > EPSILON for value in control.delta_rotation_euler):
        return True
    if (
        abs(control.delta_rotation_quaternion.w - 1.0) > EPSILON
        or any(
            abs(value) > EPSILON
            for value in (
                control.delta_rotation_quaternion.x,
                control.delta_rotation_quaternion.y,
                control.delta_rotation_quaternion.z,
            )
        )
    ):
        return True
    return any(abs(value - 1.0) > EPSILON for value in control.delta_scale)


def _snapshot_pose_bone_rotation(pose_bone):
    return {
        "rotation_mode": pose_bone.rotation_mode,
        "matrix_basis": pose_bone.matrix_basis.copy(),
    }


def _restore_pose_bone_rotation(pose_bone, snapshot):
    pose_bone.rotation_mode = snapshot["rotation_mode"]
    pose_bone.matrix_basis = snapshot["matrix_basis"]


def _add_bone_roll_driver(
    pose_bone,
    controls,
    coefficients,
    baseline_y,
    *,
    rotation_mode=CONTROL_DRIVER_ROTATION_MODE,
):
    fcurve = pose_bone.driver_add("rotation_euler", 1)
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    driver.use_self = False
    for control_index in sorted(coefficients):
        variable = driver.variables.new()
        variable.name = f"{TILT_DRIVER_VARIABLE}_{control_index}"
        variable.type = "TRANSFORMS"
        target = variable.targets[0]
        target.id = controls[control_index]
        target.transform_type = "ROT_Y"
        target.transform_space = "LOCAL_SPACE"
        target.rotation_mode = rotation_mode
    driver.expression = _roll_expression(baseline_y, coefficients)
    return fcurve


def _add_rotation_tilt_driver(
    curve_data,
    control,
    index,
    baseline_tilt,
    *,
    rotation_mode=CONTROL_DRIVER_ROTATION_MODE,
):
    data_path = _tilt_data_path(index)
    fcurve = curve_data.driver_add(data_path)
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    driver.use_self = False
    variable = driver.variables.new()
    variable.name = TILT_DRIVER_VARIABLE
    variable.type = "TRANSFORMS"
    target = variable.targets[0]
    target.id = control
    target.transform_type = "ROT_Y"
    target.transform_space = "LOCAL_SPACE"
    target.rotation_mode = rotation_mode
    driver.expression = _tilt_expression(baseline_tilt)
    return fcurve


def _write_hook_registry(curve_obj, records):
    curve_obj[HOOK_REGISTRY_KEY] = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validated_hook_modifiers(curve_obj, controls, rig_id):
    """Resolve exact registered Hook pointers and their visible-name generation.

    Modifiers cannot carry ID properties in Blender 5.2, so ownership remains
    in the UUID-tagged Curve registry.  The UUID is deliberately not required
    in the artist-facing name: both the old exact UUID form and the new short
    form are accepted, but a mixed or hand-edited set is refused.
    """

    registry = _hook_registry(curve_obj)
    if len(registry) < 3 or len(registry) != len(controls):
        raise SplineIKSetupError("The generated control or Hook count no longer matches.")

    modifiers = []
    registry_names = []
    name_states = set()
    for index, (record, control) in enumerate(zip(registry, controls)):
        if not isinstance(record, dict):
            raise SplineIKSetupError("The Hook registry contains an invalid entry.")
        current_name = _hook_display_name(index)
        legacy_name = _legacy_hook_name(rig_id, index)
        registered_name = record.get("name")
        if registered_name == current_name:
            name_states.add(HOOK_NAME_CURRENT)
        elif registered_name == legacy_name:
            name_states.add(HOOK_NAME_LEGACY)
        else:
            raise SplineIKSetupError("The Hook registry no longer matches the generated setup.")
        if (
            record.get("owner") != OWNER_VALUE
            or record.get("version") != RIG_VERSION
            or record.get("rig_id") != rig_id
            or record.get("role") != "HOOK"
            or record.get("control_index") != index
            or record.get("target") != control.name
        ):
            raise SplineIKSetupError("The Hook registry no longer matches the generated setup.")
        modifier = curve_obj.modifiers.get(registered_name)
        if (
            modifier is None
            or modifier.type != "HOOK"
            or modifier.object is not control
            or tuple(modifier.vertex_indices)
            != (index * 3, index * 3 + 1, index * 3 + 2)
        ):
            raise SplineIKSetupError(
                f"Generated Hook '{registered_name}' no longer matches its registered control."
            )
        modifiers.append(modifier)
        registry_names.append(registered_name)

    if len(name_states) != 1:
        raise SplineIKSetupError(
            "The generated Hook names use mixed legacy and current formats; nothing was changed."
        )
    if [modifier.name for modifier in curve_obj.modifiers] != registry_names:
        raise SplineIKSetupError(
            "The generated Curve has unregistered, duplicated, or reordered modifiers."
        )
    return tuple(modifiers), name_states.pop(), registry


def _generated_hook_modifiers(curve_obj, controls, rig_id):
    modifiers, _name_state, _registry = _validated_hook_modifiers(
        curve_obj,
        controls,
        rig_id,
    )
    return modifiers


def _curve_positions_match(before, after):
    if len(before) != len(after):
        return False
    scale = max(
        1.0,
        max((point.length for point in before), default=0.0),
        max((point.length for point in after), default=0.0),
    )
    tolerance = CONTROL_FRAME_CURVE_TOLERANCE * scale
    return all((left - right).length <= tolerance for left, right in zip(before, after))


def _evaluated_curve_positions(context, curve_obj):
    evaluated = curve_obj.evaluated_get(context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        world = evaluated.matrix_world.copy()
        return tuple(world @ Vector(vertex.co) for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()


def _write_control_frame_registry(curve_data, controls, rig_id):
    curve_data[CONTROL_FRAME_REGISTRY_KEY] = json.dumps(
        [
            {
                "owner": OWNER_VALUE,
                "version": CONTROL_FRAME_VERSION,
                "rig_id": rig_id,
                "role": "CONTROL_FRAME",
                "control_index": index,
                "target": control.name,
                "axis_contract": CONTROL_FRAME_AXIS_CONTRACT,
            }
            for index, control in enumerate(controls)
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _apply_control_axes_and_compensate_hooks(
    context,
    curve_obj,
    controls,
    plan,
    hook_modifiers,
):
    """Install the X/Y/Z control frames without changing Hooked Curve points."""

    armature = curve_obj.parent
    try:
        parent_world_inverse = armature.matrix_world.inverted()
    except ValueError as exc:
        raise SplineIKSetupError(
            "The Armature has a singular transform; control axes were not changed."
        ) from exc
    old_world_matrices = tuple(control.matrix_world.copy() for control in controls)
    old_hook_inverses = tuple(modifier.matrix_inverse.copy() for modifier in hook_modifiers)

    for control, target_world in zip(controls, plan["frames"]):
        control.delta_location = (0.0, 0.0, 0.0)
        control.delta_rotation_euler = (0.0, 0.0, 0.0)
        control.delta_rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        control.delta_scale = (1.0, 1.0, 1.0)
        control.rotation_mode = CONTROL_DRIVER_ROTATION_MODE
        control.matrix_parent_inverse = parent_world_inverse @ target_world
        control.matrix_basis = Matrix.Identity(4)
        control.show_axis = True
    for modifier, old_world, old_inverse, target_world, control in zip(
        hook_modifiers,
        old_world_matrices,
        old_hook_inverses,
        plan["frames"],
        controls,
    ):
        try:
            modifier.matrix_inverse = (
                target_world.inverted() @ old_world @ old_inverse
            )
        except ValueError as exc:
            raise SplineIKSetupError(
                f"Control '{control.name}' became singular while compensating its Hook."
            ) from exc
    context.view_layer.update()

    after_positions = _evaluated_curve_positions(context, curve_obj)
    if not _curve_positions_match(plan["curve_positions"], after_positions):
        raise SplineIKSetupError(
            "Control-axis alignment changed the evaluated Curve; the operation was refused."
        )
    for target_frame, control in zip(plan["frames"], controls):
        actual = control.matrix_world.to_3x3()
        target = target_frame.to_3x3()
        for axis_index in range(3):
            actual_axis = Vector(actual.col[axis_index]).normalized()
            target_axis = Vector(target.col[axis_index]).normalized()
            if actual_axis.dot(target_axis) < 1.0 - 1.0e-5:
                raise SplineIKSetupError(
                    f"Control '{control.name}' could not adopt the Curve/strap local frame."
                )


def _apply_control_frame_plan(context, curve_obj, controls, rig_id, plan, hook_modifiers):
    for point, tilt in zip(curve_obj.data.splines[0].bezier_points, plan["tilts"]):
        if not math.isfinite(tilt):
            raise SplineIKSetupError("A derived neutral Curve Tilt is non-finite.")
        point.tilt = tilt
    curve_obj.data.update_tag()
    _apply_control_axes_and_compensate_hooks(
        context,
        curve_obj,
        controls,
        plan,
        hook_modifiers,
    )

    after_plan = None
    for _iteration in range(4):
        after_plan = _evaluated_control_frame_plan(context, curve_obj, controls)
        residuals = tuple(
            expected_tilt - float(point.tilt)
            for point, expected_tilt in zip(
                curve_obj.data.splines[0].bezier_points,
                after_plan["tilts"],
            )
        )
        if max((abs(value) for value in residuals), default=0.0) <= 1.0e-5:
            break
        for point, expected_tilt in zip(
            curve_obj.data.splines[0].bezier_points,
            after_plan["tilts"],
        ):
            point.tilt = expected_tilt
        curve_obj.data.update_tag()
        context.view_layer.update()
    else:
        raise SplineIKSetupError(
            "The Curve normals did not converge on the evaluated strap-front frame "
            f"(residuals {[round(math.degrees(value), 3) for value in residuals]}°)."
        )
    assert after_plan is not None
    if not _curve_positions_match(plan["curve_positions"], after_plan["curve_positions"]):
        raise SplineIKSetupError(
            "Control-axis alignment changed the evaluated Curve; the operation was refused."
        )
    for index, (
        point,
        expected_tilt,
        curve_normal,
        front_normal,
        target_frame,
        control,
    ) in enumerate(
        zip(
            curve_obj.data.splines[0].bezier_points,
            after_plan["tilts"],
            after_plan["curve_normals"],
            after_plan["front_normals"],
            after_plan["frames"],
            controls,
        )
    ):
        if abs(float(point.tilt) - expected_tilt) > 1.0e-5:
            raise SplineIKSetupError(
                f"Curve point {index + 1} could not align its normal to the strap front "
                f"(residual {math.degrees(expected_tilt - float(point.tilt)):.3f}°)."
            )
        if curve_normal.dot(front_normal) < 1.0 - 1.0e-5:
            raise SplineIKSetupError(
                f"Curve point {index + 1} did not align its evaluated profile normal "
                "to the strap front."
            )
        actual = control.matrix_world.to_3x3()
        target = target_frame.to_3x3()
        for axis_index in range(3):
            actual_axis = Vector(actual.col[axis_index]).normalized()
            target_axis = Vector(target.col[axis_index]).normalized()
            if actual_axis.dot(target_axis) < 1.0 - 1.0e-5:
                raise SplineIKSetupError(
                    f"Control '{control.name}' could not adopt the Curve/strap local frame."
                )
    _write_control_frame_registry(curve_obj.data, controls, rig_id)


def _upgrade_enabled_rotation_tilt_axes(context, curve_obj, controls, rig_id):
    """Migrate one complete v1/SWING rig to the v2 strap-axis contract in place."""

    state, detail = _rotation_tilt_state(curve_obj, controls, rig_id)
    if state != "ENABLED":
        raise SplineIKSetupError(detail or "Rotation Tilt is not safely enabled on this rig.")
    curve_data = curve_obj.data
    if _rotation_tilt_contract_version(curve_data) != LEGACY_DRIVER_VERSION:
        return False
    frame_state, frame_detail = _control_frame_state(curve_obj, controls, rig_id)
    if frame_state != "MISSING":
        raise SplineIKSetupError(
            frame_detail
            or "The legacy driver contract has conflicting control-axis metadata."
        )

    armature = curve_obj.parent
    chain_names = _tagged_chain(curve_obj)
    if armature is None or armature.type != "ARMATURE" or not chain_names:
        raise SplineIKSetupError("The generated Curve no longer resolves its Armature chain.")
    spline = _control_point_spline(curve_data, controls)
    tilt_registry = _tilt_driver_registry(curve_data, strict=True)
    roll_registry = _roll_driver_registry(curve_data, strict=True)
    pose_bones = tuple(armature.pose.bones[name] for name in chain_names)
    all_rotation_paths = _chain_all_rotation_paths(armature, chain_names)
    tilt_paths = {_tilt_data_path(index) for index in range(len(controls))}

    for control in controls:
        if _control_has_legacy_upgrade_conflict(control):
            raise SplineIKSetupError(
                f"Control '{control.name}' has animation, constraints, or Delta transforms. "
                "Clear those conflicts before aligning its Rotation Tilt axes."
            )
    if _curve_tilt_animation_conflict(curve_data, tilt_paths):
        raise SplineIKSetupError(
            "Artist animation already uses a generated Curve Tilt channel."
        )
    if _armature_roll_animation_conflict(armature, all_rotation_paths):
        raise SplineIKSetupError(
            "Artist animation already uses a captured bone rotation channel."
        )

    owned_roll_fcurves = tuple(
        _armature_driver_for_path(armature, record["data_path"])
        for record in roll_registry
    )
    animation_data = armature.animation_data
    if animation_data is not None and any(
        fcurve.data_path in all_rotation_paths and fcurve not in owned_roll_fcurves
        for fcurve in animation_data.drivers
    ):
        raise SplineIKSetupError(
            "A foreign driver already uses a captured bone rotation channel."
        )

    hook_modifiers = _generated_hook_modifiers(curve_obj, controls, rig_id)
    frame_plan = _evaluated_control_frame_plan(context, curve_obj, controls)
    context.view_layer.update()
    evaluated_curve = curve_obj.evaluated_get(context.evaluated_depsgraph_get())
    evaluated_tilts = tuple(
        float(point.tilt)
        for point in evaluated_curve.data.splines[0].bezier_points
    )
    if len(evaluated_tilts) != len(controls) or any(
        not math.isfinite(value) for value in evaluated_tilts
    ):
        raise SplineIKSetupError("The current evaluated Curve Tilt cannot be migrated safely.")
    current_bone_y = tuple(float(bone.rotation_euler.y) for bone in pose_bones)
    if any(not math.isfinite(value) for value in current_bone_y):
        raise SplineIKSetupError("A current evaluated bone Roll cannot be migrated safely.")

    control_snapshots = tuple(_snapshot_control_transform(control) for control in controls)
    hook_snapshots = tuple(modifier.matrix_inverse.copy() for modifier in hook_modifiers)
    bone_snapshots = tuple(_snapshot_pose_bone_rotation(bone) for bone in pose_bones)
    before_bones = tuple(
        armature.evaluated_get(context.evaluated_depsgraph_get())
        .pose.bones[name]
        .matrix.copy()
        for name in chain_names
    )
    previous_registries = {
        key: (key in curve_data, curve_data.get(key))
        for key in (
            TILT_DRIVER_REGISTRY_KEY,
            ROLL_DRIVER_REGISTRY_KEY,
            CONTROL_FRAME_REGISTRY_KEY,
        )
    }
    curve_driver_snapshots = []
    for record in tilt_registry:
        fcurve = _driver_for_path(curve_data, record["data_path"])
        curve_driver_snapshots.append(
            (
                fcurve,
                fcurve.driver.expression,
                tuple(
                    target.rotation_mode
                    for variable in fcurve.driver.variables
                    for target in variable.targets
                ),
            )
        )
    roll_driver_snapshots = []
    for record, fcurve in zip(roll_registry, owned_roll_fcurves):
        roll_driver_snapshots.append(
            (
                fcurve,
                fcurve.driver.expression,
                tuple(
                    target.rotation_mode
                    for variable in fcurve.driver.variables
                    for target in variable.targets
                ),
            )
        )

    try:
        _apply_control_axes_and_compensate_hooks(
            context,
            curve_obj,
            controls,
            frame_plan,
            hook_modifiers,
        )

        for record, baseline, (fcurve, _expression, _modes) in zip(
            tilt_registry,
            evaluated_tilts,
            curve_driver_snapshots,
        ):
            fcurve.driver.expression = _tilt_expression(baseline)
            fcurve.driver.variables[0].targets[0].rotation_mode = (
                CONTROL_DRIVER_ROTATION_MODE
            )
            record["version"] = TILT_DRIVER_VERSION
            record["baseline_tilt"] = baseline

        for record, baseline_y, bone_snapshot, (fcurve, _expression, _modes) in zip(
            roll_registry,
            current_bone_y,
            bone_snapshots,
            roll_driver_snapshots,
        ):
            coefficients = {
                int(item["control_index"]): float(item["weight"])
                for item in record["coefficients"]
            }
            fcurve.driver.expression = _roll_expression(baseline_y, coefficients)
            for variable in fcurve.driver.variables:
                variable.targets[0].rotation_mode = CONTROL_DRIVER_ROTATION_MODE
            record["version"] = ROLL_DRIVER_VERSION
            record["baseline_y"] = baseline_y
            record["baseline_matrix"] = _matrix_to_json(bone_snapshot["matrix_basis"])

        curve_data[TILT_DRIVER_REGISTRY_KEY] = json.dumps(
            tilt_registry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        curve_data[ROLL_DRIVER_REGISTRY_KEY] = json.dumps(
            roll_registry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        _write_control_frame_registry(curve_data, controls, rig_id)
        context.view_layer.update()

        final_state, final_detail = _rotation_tilt_state(curve_obj, controls, rig_id)
        final_frame_state, final_frame_detail = _control_frame_state(
            curve_obj,
            controls,
            rig_id,
        )
        if (
            final_state != "ENABLED"
            or _rotation_tilt_contract_version(curve_data) != TILT_DRIVER_VERSION
        ):
            raise SplineIKSetupError(
                final_detail or "The migrated Rotation Tilt drivers did not validate."
            )
        if final_frame_state != "ENABLED":
            raise SplineIKSetupError(
                final_frame_detail or "The migrated control-axis frame did not validate."
            )
        after_curve = curve_obj.evaluated_get(context.evaluated_depsgraph_get())
        after_tilts = tuple(
            float(point.tilt)
            for point in after_curve.data.splines[0].bezier_points
        )
        if any(
            abs(before - after) > 1.0e-6
            for before, after in zip(evaluated_tilts, after_tilts)
        ):
            raise SplineIKSetupError(
                "The v1-to-v2 migration could not preserve the current Curve Tilt."
            )
        if not _curve_positions_match(
            frame_plan["curve_positions"],
            _evaluated_curve_positions(context, curve_obj),
        ):
            raise SplineIKSetupError(
                "The v1-to-v2 migration changed the evaluated Curve path."
            )
        after_armature = armature.evaluated_get(context.evaluated_depsgraph_get())
        if max(
            abs(left - right)
            for name, before_matrix in zip(chain_names, before_bones)
            for old_row, new_row in zip(
                before_matrix,
                after_armature.pose.bones[name].matrix,
            )
            for left, right in zip(old_row, new_row)
        ) > 1.0e-4:
            raise SplineIKSetupError(
                "The v1-to-v2 migration changed the current evaluated bone pose."
            )
        return True
    except Exception as exc:
        rollback_error = None
        try:
            for fcurve, expression, modes in curve_driver_snapshots:
                fcurve.driver.expression = expression
                targets = tuple(
                    target
                    for variable in fcurve.driver.variables
                    for target in variable.targets
                )
                for target, mode in zip(targets, modes):
                    target.rotation_mode = mode
            for fcurve, expression, modes in roll_driver_snapshots:
                fcurve.driver.expression = expression
                targets = tuple(
                    target
                    for variable in fcurve.driver.variables
                    for target in variable.targets
                )
                for target, mode in zip(targets, modes):
                    target.rotation_mode = mode
            for key, (existed, value) in previous_registries.items():
                if existed:
                    curve_data[key] = value
                elif key in curve_data:
                    del curve_data[key]
            for pose_bone, snapshot in zip(pose_bones, bone_snapshots):
                _restore_pose_bone_rotation(pose_bone, snapshot)
            for control, snapshot in zip(controls, control_snapshots):
                _restore_control_transform(control, snapshot)
            for modifier, matrix_inverse in zip(hook_modifiers, hook_snapshots):
                modifier.matrix_inverse = matrix_inverse
            context.view_layer.update()
        except Exception as restore_exc:  # pragma: no cover - Blender recovery edge
            rollback_error = restore_exc
        if rollback_error is not None:
            raise SplineIKSetupError(
                f"Rotation Tilt axis migration failed: {exc}. "
                f"Recovery also failed: {rollback_error}"
            ) from rollback_error
        if isinstance(exc, SplineIKSetupError):
            raise
        raise SplineIKSetupError(
            "Rotation Tilt axes were not migrated and the rig was restored unchanged: "
            f"{exc}"
        ) from exc


def _enable_rotation_tilt(context, curve_obj, controls, rig_id, *, legacy_upgrade=False):
    """Attach Curve Tilt and interpolated bone Roll drivers atomically."""

    state, detail = _rotation_tilt_state(curve_obj, controls, rig_id)
    if state == "ENABLED":
        return False
    if state != "MISSING":
        raise SplineIKSetupError(detail or "Rotation Tilt cannot be enabled safely.")

    curve_data = curve_obj.data
    armature = curve_obj.parent
    chain_names = _tagged_chain(curve_obj)
    if armature is None or armature.type != "ARMATURE" or not chain_names:
        raise SplineIKSetupError("The generated Curve no longer resolves its Armature chain.")
    spline = _control_point_spline(curve_data, controls)
    original_tilts = tuple(float(point.tilt) for point in spline.bezier_points)
    if any(not math.isfinite(value) for value in original_tilts):
        raise SplineIKSetupError("A generated Bezier point has a non-finite Tilt value.")
    frame_state, frame_detail = _control_frame_state(curve_obj, controls, rig_id)
    if frame_state == "INVALID":
        raise SplineIKSetupError(frame_detail or "The control-axis frame is invalid.")
    frame_plan = (
        _evaluated_control_frame_plan(context, curve_obj, controls)
        if frame_state == "MISSING"
        else None
    )
    hook_modifiers = _generated_hook_modifiers(curve_obj, controls, rig_id)
    hook_inverse_snapshots = tuple(
        modifier.matrix_inverse.copy() for modifier in hook_modifiers
    )

    roll_plan = _bone_twist_coefficients(armature, chain_names, len(controls))
    pose_bones = tuple(armature.pose.bones[name] for name in chain_names)
    paths = _chain_rotation_paths(armature, chain_names)
    all_rotation_paths = _chain_all_rotation_paths(armature, chain_names)
    if legacy_upgrade:
        for control in controls:
            if _control_has_legacy_upgrade_conflict(control):
                raise SplineIKSetupError(
                    f"Control '{control.name}' has animation, constraints, or Delta transforms. "
                    "Clear those conflicts before enabling Rotation Tilt."
                )
    if _armature_rotation_animation_conflict(armature, all_rotation_paths):
        raise SplineIKSetupError(
            "Artist animation or a driver already uses a captured bone rotation channel."
        )
    control_snapshots = tuple(_snapshot_control_transform(control) for control in controls)
    bone_snapshots = tuple(_snapshot_pose_bone_rotation(bone) for bone in pose_bones)
    depsgraph = context.evaluated_depsgraph_get()
    before_evaluated_bones = tuple(
        armature.evaluated_get(depsgraph).pose.bones[name].matrix.copy()
        for name in chain_names
    )
    previous_registries = {
        key: (key in curve_data, curve_data.get(key))
        for key in (
            TILT_DRIVER_REGISTRY_KEY,
            ROLL_DRIVER_REGISTRY_KEY,
            CONTROL_FRAME_REGISTRY_KEY,
        )
    }
    had_curve_animation_data = curve_data.animation_data is not None
    had_armature_animation_data = armature.animation_data is not None
    created_curve_paths = []
    created_roll_paths = []
    try:
        # Preflight every current rest matrix before mutating the first control.
        for control in controls:
            rest_matrix = control.matrix_parent_inverse @ control.matrix_basis
            if any(not math.isfinite(value) for row in rest_matrix for value in row):
                raise SplineIKSetupError(
                    f"Control '{control.name}' has a non-finite transform and cannot drive Tilt."
                )
            if abs(rest_matrix.to_3x3().determinant()) <= EPSILON:
                raise SplineIKSetupError(
                    f"Control '{control.name}' has a singular transform; remove zero scale first."
                )
        if frame_plan is not None:
            _apply_control_frame_plan(
                context,
                curve_obj,
                controls,
                rig_id,
                frame_plan,
                hook_modifiers,
            )
        else:
            for control in controls:
                _rebase_control_to_current_pose(control)
            context.view_layer.update()
            for snapshot, control in zip(control_snapshots, controls):
                if max(
                    abs(left - right)
                    for old_row, new_row in zip(snapshot["matrix_world"], control.matrix_world)
                    for left, right in zip(old_row, new_row)
                ) > 1.0e-6:
                    raise SplineIKSetupError(
                        f"Control '{control.name}' could not preserve its visible pose."
                    )

        baselines = tuple(float(point.tilt) for point in spline.bezier_points)
        if any(not math.isfinite(value) for value in baselines):
            raise SplineIKSetupError("A neutral Curve Tilt baseline is non-finite.")

        tilt_registry = []
        for index, (control, baseline) in enumerate(zip(controls, baselines)):
            path = _tilt_data_path(index)
            created_curve_paths.append(path)
            _add_rotation_tilt_driver(curve_data, control, index, baseline)
            tilt_registry.append(
                {
                    "data_path": path,
                    "owner": OWNER_VALUE,
                    "version": TILT_DRIVER_VERSION,
                    "rig_id": rig_id,
                    "role": "ROTATION_TILT",
                    "control_index": index,
                    "target": control.name,
                    "baseline_tilt": baseline,
                }
            )
        curve_data[TILT_DRIVER_REGISTRY_KEY] = json.dumps(
            tilt_registry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        roll_registry = []
        for bone_index, (pose_bone, plan, path, snapshot) in enumerate(
            zip(pose_bones, roll_plan, paths, bone_snapshots)
        ):
            bone_name, coefficients = plan
            pose_bone.rotation_mode = "XYZ"
            pose_bone.matrix_basis = snapshot["matrix_basis"]
            baseline_y = float(pose_bone.rotation_euler.y)
            if not math.isfinite(baseline_y):
                raise SplineIKSetupError(
                    f"Pose Bone '{bone_name}' has a non-finite Roll baseline."
                )
            created_roll_paths.append(path)
            _add_bone_roll_driver(pose_bone, controls, coefficients, baseline_y)
            roll_registry.append(
                {
                    "data_path": path,
                    "owner": OWNER_VALUE,
                    "version": ROLL_DRIVER_VERSION,
                    "rig_id": rig_id,
                    "role": "BONE_ROLL",
                    "bone_index": bone_index,
                    "bone": bone_name,
                    "baseline_y": baseline_y,
                    "baseline_matrix": _matrix_to_json(snapshot["matrix_basis"]),
                    "original_rotation_mode": snapshot["rotation_mode"],
                    "coefficients": [
                        {"control_index": index, "weight": weight}
                        for index, weight in sorted(coefficients.items())
                    ],
                }
            )
        curve_data[ROLL_DRIVER_REGISTRY_KEY] = json.dumps(
            roll_registry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        context.view_layer.update()

        final_state, final_detail = _rotation_tilt_state(curve_obj, controls, rig_id)
        if final_state != "ENABLED":
            raise SplineIKSetupError(
                final_detail or "The completed Rotation Tilt setup did not validate."
            )
        final_frame_state, final_frame_detail = _control_frame_state(
            curve_obj,
            controls,
            rig_id,
        )
        if final_frame_state != "ENABLED":
            raise SplineIKSetupError(
                final_frame_detail or "The completed control-axis frame did not validate."
            )
        evaluated = curve_obj.evaluated_get(context.evaluated_depsgraph_get())
        evaluated_points = evaluated.data.splines[0].bezier_points
        if any(
            abs(float(evaluated_points[index].tilt) - baseline) > 1.0e-6
            for index, baseline in enumerate(baselines)
        ):
            raise SplineIKSetupError(
                "Rotation Tilt could not preserve the Curve's current Tilt baseline."
            )
        after_evaluated = armature.evaluated_get(context.evaluated_depsgraph_get())
        if max(
            abs(left - right)
            for name, before_matrix in zip(chain_names, before_evaluated_bones)
            for old_row, new_row in zip(before_matrix, after_evaluated.pose.bones[name].matrix)
            for left, right in zip(old_row, new_row)
        ) > 1.0e-4:
            raise SplineIKSetupError(
                "Rotation Tilt could not preserve the current evaluated bone pose."
            )
        return True
    except Exception as exc:
        rollback_error = None
        try:
            for data_path in reversed(created_roll_paths):
                armature.driver_remove(data_path, 1)
            for pose_bone, snapshot in zip(pose_bones, bone_snapshots):
                _restore_pose_bone_rotation(pose_bone, snapshot)
            armature_animation = armature.animation_data
            if (
                not had_armature_animation_data
                and armature_animation is not None
                and not armature_animation.drivers
                and armature_animation.action is None
                and not armature_animation.nla_tracks
            ):
                armature.animation_data_clear()
            for data_path in reversed(created_curve_paths):
                curve_data.driver_remove(data_path)
            curve_animation = curve_data.animation_data
            if (
                not had_curve_animation_data
                and curve_animation is not None
                and not curve_animation.drivers
                and curve_animation.action is None
                and not curve_animation.nla_tracks
            ):
                curve_data.animation_data_clear()
            for key, (existed, value) in previous_registries.items():
                if existed:
                    curve_data[key] = value
                elif key in curve_data:
                    del curve_data[key]
            for point, original_tilt in zip(spline.bezier_points, original_tilts):
                point.tilt = original_tilt
            for control, snapshot in zip(controls, control_snapshots):
                _restore_control_transform(control, snapshot)
            for modifier, matrix_inverse in zip(
                hook_modifiers,
                hook_inverse_snapshots,
            ):
                modifier.matrix_inverse = matrix_inverse
            context.view_layer.update()
        except Exception as restore_exc:  # pragma: no cover - Blender recovery edge
            rollback_error = restore_exc
        if rollback_error is not None:
            raise SplineIKSetupError(
                f"Rotation Tilt failed: {exc}. Recovery also failed: {rollback_error}"
            ) from rollback_error
        if isinstance(exc, SplineIKSetupError):
            raise
        raise SplineIKSetupError(
            f"Rotation Tilt was not enabled and the rig was restored unchanged: {exc}"
        ) from exc


def _retarget_registered_rotation_tilt(curve_data, controls, rig_id):
    """Retarget copied driver variables during transactional removal recovery."""

    registry = _tilt_driver_registry(curve_data, strict=True)
    if registry is None:
        return
    if len(registry) != len(controls):
        raise SplineIKSetupError("The copied Rotation Tilt registry is incomplete.")
    for index, (record, control) in enumerate(zip(registry, controls)):
        if (
            not isinstance(record, dict)
            or record.get("rig_id") != rig_id
            or record.get("control_index") != index
            or record.get("data_path") != _tilt_data_path(index)
        ):
            raise SplineIKSetupError("The copied Rotation Tilt registry is invalid.")
        fcurve = _driver_for_path(curve_data, record["data_path"])
        if fcurve is None or len(fcurve.driver.variables) != 1:
            raise SplineIKSetupError("A copied Rotation Tilt driver is missing.")
        fcurve.driver.variables[0].targets[0].id = control


def _snapshot_registered_bone_roll_channels(armature, curve_data):
    registry = _roll_driver_registry(curve_data, strict=True)
    if registry is None:
        return ()
    snapshots = []
    for record in registry:
        pose_bone = armature.pose.bones.get(record.get("bone", ""))
        if pose_bone is None or pose_bone.rotation_mode != "XYZ":
            raise SplineIKSetupError("A driven bone Roll channel cannot be snapshotted safely.")
        snapshots.append(
            {
                "bone": pose_bone.name,
                "location": tuple(float(value) for value in pose_bone.location),
                "scale": tuple(float(value) for value in pose_bone.scale),
                "rotation_x": float(pose_bone.rotation_euler.x),
                "rotation_z": float(pose_bone.rotation_euler.z),
            }
        )
    return tuple(snapshots)


def _restore_registered_bone_roll(
    armature,
    curve_data,
    controls,
    rig_id,
    *,
    channel_snapshots=(),
):
    registry = _roll_driver_registry(curve_data, strict=True)
    if registry is None:
        return
    chain_names = _tagged_chain(curve_data)
    if len(registry) != len(chain_names):
        raise SplineIKSetupError("The copied bone Roll registry is incomplete.")
    snapshots_by_bone = {
        snapshot["bone"]: snapshot for snapshot in channel_snapshots
    }
    for bone_index, (record, bone_name) in enumerate(zip(registry, chain_names)):
        if (
            not isinstance(record, dict)
            or record.get("rig_id") != rig_id
            or record.get("bone_index") != bone_index
            or record.get("bone") != bone_name
        ):
            raise SplineIKSetupError("The copied bone Roll registry is invalid.")
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            raise SplineIKSetupError(f"Pose Bone '{bone_name}' is unavailable during recovery.")
        path = record.get("data_path")
        existing = _armature_driver_for_path(armature, path)
        if existing is not None and not armature.driver_remove(path, 1):
            raise SplineIKSetupError(f"Bone Roll driver on '{bone_name}' could not be reset.")
        coefficients = {
            item["control_index"]: float(item["weight"])
            for item in record.get("coefficients", ())
        }
        pose_bone.rotation_mode = "XYZ"
        channel_snapshot = snapshots_by_bone.get(bone_name)
        if channel_snapshot is None:
            pose_bone.matrix_basis = _matrix_from_json(record.get("baseline_matrix"))
        else:
            pose_bone.location = channel_snapshot["location"]
            pose_bone.scale = channel_snapshot["scale"]
            pose_bone.rotation_euler.x = channel_snapshot["rotation_x"]
            pose_bone.rotation_euler.y = float(record.get("baseline_y"))
            pose_bone.rotation_euler.z = channel_snapshot["rotation_z"]
        _add_bone_roll_driver(
            pose_bone,
            controls,
            coefficients,
            float(record.get("baseline_y")),
            rotation_mode=_driver_rotation_mode(record.get("version")),
        )


def _remove_registered_bone_roll(
    armature,
    curve_data,
    *,
    restore_baseline=True,
    preserve_current_channels=True,
):
    registry = _roll_driver_registry(curve_data, strict=True)
    if registry is None:
        return 0
    chain_names = _tagged_chain(curve_data)
    if len(registry) != len(chain_names):
        raise SplineIKSetupError("The bone Roll registry is incomplete during removal.")
    removed = 0
    for bone_index, (record, bone_name) in enumerate(zip(registry, chain_names)):
        if (
            not isinstance(record, dict)
            or record.get("bone_index") != bone_index
            or record.get("bone") != bone_name
        ):
            raise SplineIKSetupError("The bone Roll registry is invalid during removal.")
        path = record.get("data_path")
        pose_bone = armature.pose.bones.get(bone_name)
        if pose_bone is None:
            raise SplineIKSetupError(
                f"Pose Bone '{bone_name}' is unavailable during Roll removal."
            )
        current_channels = None
        if restore_baseline and preserve_current_channels:
            if pose_bone.rotation_mode != "XYZ":
                raise SplineIKSetupError(
                    f"Pose Bone '{bone_name}' changed rotation mode; Roll removal was refused."
                )
            current_channels = {
                "location": pose_bone.location.copy(),
                "scale": pose_bone.scale.copy(),
                "rotation_x": float(pose_bone.rotation_euler.x),
                "rotation_z": float(pose_bone.rotation_euler.z),
            }
        if _armature_driver_for_path(armature, path) is None:
            raise SplineIKSetupError(f"Bone Roll driver on '{bone_name}' is missing.")
        if not armature.driver_remove(path, 1):
            raise SplineIKSetupError(f"Bone Roll driver on '{bone_name}' could not be removed.")
        removed += 1
        if restore_baseline:
            if current_channels is None:
                pose_bone.rotation_mode = record.get("original_rotation_mode")
                pose_bone.matrix_basis = _matrix_from_json(record.get("baseline_matrix"))
            else:
                pose_bone.rotation_mode = "XYZ"
                pose_bone.location = current_channels["location"]
                pose_bone.scale = current_channels["scale"]
                pose_bone.rotation_euler.x = current_channels["rotation_x"]
                pose_bone.rotation_euler.y = float(record.get("baseline_y"))
                pose_bone.rotation_euler.z = current_channels["rotation_z"]
                neutral_matrix = pose_bone.matrix_basis.copy()
                pose_bone.rotation_mode = record.get("original_rotation_mode")
                pose_bone.matrix_basis = neutral_matrix
                # Reassign artist-owned translation/scale last.  On connected
                # bones Blender may normalize those RNA channels while
                # decomposing ``matrix_basis`` during a rotation-mode change;
                # the generated Roll driver never owned either channel.
                pose_bone.location = current_channels["location"]
                pose_bone.scale = current_channels["scale"]
    return removed


def _create_curve_and_controls(
    context,
    armature,
    chain_names,
    samples,
    control_size,
    rig_id,
    created_objects,
    created_curves,
):
    base_name = f"{armature.name}_SplineIK"
    collection = _target_collection(context, armature)

    curve_data = bpy.data.curves.new(f"{base_name}_Curve", type="CURVE")
    created_curves.append(curve_data)
    curve_data.dimensions = "3D"
    curve_data.resolution_u = 12
    curve_data.render_resolution_u = 12
    curve_data.twist_mode = "MINIMUM"
    _set_tags(curve_data, rig_id, "CURVE_DATA", armature, chain_names)

    spline = curve_data.splines.new(type="BEZIER")
    spline.bezier_points.add(len(samples) - 1)
    spline.resolution_u = 12
    for index, (point, position) in enumerate(zip(spline.bezier_points, samples)):
        tangent = _point_tangent(samples, index)
        left_span = (
            (samples[index] - samples[index - 1]).length
            if index > 0
            else (samples[1] - samples[0]).length
        )
        right_span = (
            (samples[index + 1] - samples[index]).length
            if index < len(samples) - 1
            else (samples[-1] - samples[-2]).length
        )
        point.co = position
        point.handle_left_type = "FREE"
        point.handle_right_type = "FREE"
        point.handle_left = position - tangent * (left_span / 3.0)
        point.handle_right = position + tangent * (right_span / 3.0)

    curve_obj = bpy.data.objects.new(f"{base_name}_Curve", curve_data)
    created_objects.append(curve_obj)
    collection.objects.link(curve_obj)
    curve_obj.parent = armature
    curve_obj.matrix_parent_inverse = Matrix.Identity(4)
    curve_obj.matrix_basis = Matrix.Identity(4)
    curve_obj.display_type = "WIRE"
    curve_obj.show_in_front = True
    _set_tags(curve_obj, rig_id, "CURVE", armature, chain_names)

    controls = []
    for index, position in enumerate(samples):
        tangent = _point_tangent(samples, index)
        control = bpy.data.objects.new(f"{base_name}_CTRL_{index + 1:02d}", None)
        created_objects.append(control)
        collection.objects.link(control)
        control.parent = armature
        control.matrix_parent_inverse = Matrix.Identity(4)
        control.matrix_basis = _local_controller_matrix(position, tangent)
        control.empty_display_type = "CUBE" if index in {0, len(samples) - 1} else "SPHERE"
        control.empty_display_size = control_size
        control.display_type = "WIRE"
        control.show_in_front = True
        _set_tags(
            control,
            rig_id,
            "CONTROL",
            armature,
            chain_names,
            control_index=index,
        )
        controls.append(control)

    context.view_layer.update()
    for index, control in enumerate(controls):
        hook = curve_obj.modifiers.new(
            _hook_display_name(index),
            type="HOOK",
        )
        hook.object = control
        hook.falloff_type = "NONE"
        hook.strength = 1.0
        if not hasattr(hook, "vertex_indices_set"):
            raise SplineIKSetupError("This Blender build cannot bind Curve Hook indices.")
        hook.vertex_indices_set((index * 3, index * 3 + 1, index * 3 + 2))
        try:
            hook.matrix_inverse = control.matrix_world.inverted() @ curve_obj.matrix_world
        except ValueError as exc:
            raise SplineIKSetupError(
                "The Armature transform is singular; remove zero object scale before building."
            ) from exc
    # Modifiers, like Constraints, do not support ID custom properties in
    # Blender 5.2. Their short visible names and exact targets are registered
    # on the already UUID-tagged owning Curve object; the UUID stays internal.
    _write_hook_registry(
        curve_obj,
        [
            {
                "name": modifier.name,
                "owner": OWNER_VALUE,
                "version": RIG_VERSION,
                "rig_id": rig_id,
                "role": "HOOK",
                "control_index": index,
                "target": controls[index].name,
            }
            for index, modifier in enumerate(curve_obj.modifiers)
        ],
    )

    # New rigs ship ready to twist: every visible control keeps its Hook
    # position/orientation contract while local Y becomes a zero-based,
    # one-radian-to-one-radian Tilt channel for its matching Bezier point and
    # a smoothly interpolated Roll channel across the captured bone chain.
    _enable_rotation_tilt(context, curve_obj, tuple(controls), rig_id)

    return curve_obj, tuple(controls)


def _configure_constraint(constraint, curve_obj, chain_count):
    constraint.target = curve_obj
    constraint.chain_count = chain_count
    # NONE explicitly prevents the Spline IK constraint from introducing any
    # additional Y-axis scaling into the already-authored bone transforms.
    constraint.y_scale_mode = "NONE"
    constraint.xz_scale_mode = "NONE"
    constraint.use_curve_radius = False
    constraint.use_even_divisions = False
    constraint.use_chain_offset = False
    constraint.mute = False
    constraint.influence = 1.0


_SPLINE_IK_SNAPSHOT_PROPERTIES = (
    "owner_space",
    "target_space",
    "space_object",
    "space_subtarget",
    "mute",
    "show_expanded",
    "active",
    "influence",
    "target",
    "chain_count",
    "use_chain_offset",
    "use_even_divisions",
    "use_curve_radius",
    "xz_scale_mode",
    "y_scale_mode",
    "use_original_scale",
    "bulge",
    "use_bulge_min",
    "use_bulge_max",
    "bulge_min",
    "bulge_max",
    "bulge_smooth",
)


def _snapshot_foreign_constraint(pose_bone, constraint):
    values = {}
    for name in _SPLINE_IK_SNAPSHOT_PROPERTIES:
        if not hasattr(constraint, name):
            continue
        value = getattr(constraint, name)
        # Blender ID values such as ``target`` also expose ``copy()``, but
        # calling it creates a real duplicate Object/Data user.  Snapshots must
        # retain those exact external references; only value types (Matrix,
        # Vector, etc.) are copied defensively.
        values[name] = (
            value
            if isinstance(value, bpy.types.ID)
            else value.copy() if hasattr(value, "copy") else value
        )
    return {
        "pose_bone": pose_bone,
        "constraint": constraint,
        "index": tuple(pose_bone.constraints).index(constraint),
        "name": constraint.name,
        "values": values,
        "registry_raw": pose_bone.get(CONSTRAINT_REGISTRY_KEY, ""),
    }


def _restore_foreign_constraint(snapshot):
    pose_bone = snapshot["pose_bone"]
    constraint = pose_bone.constraints.new(type="SPLINE_IK")
    constraint.name = snapshot["name"]
    for name, value in snapshot["values"].items():
        try:
            setattr(constraint, name, value)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass
    registry_raw = snapshot["registry_raw"]
    if registry_raw:
        pose_bone[CONSTRAINT_REGISTRY_KEY] = registry_raw
    elif CONSTRAINT_REGISTRY_KEY in pose_bone:
        del pose_bone[CONSTRAINT_REGISTRY_KEY]
    constraints = pose_bone.constraints
    current_index = tuple(constraints).index(constraint)
    if current_index != snapshot["index"]:
        constraints.move(current_index, snapshot["index"])
    return constraint


def _clear_owner_tags(target):
    for key in (
        OWNER_KEY,
        VERSION_KEY,
        RIG_ID_KEY,
        ROLE_KEY,
        CHAIN_KEY,
        ARMATURE_KEY,
        CONTROL_INDEX_KEY,
    ):
        if key in target:
            del target[key]


def _delete_generated_constraint(pose_bone, constraint):
    """Fault-injection seam for the owned-constraint removal stage."""

    _remove_constraint(pose_bone, constraint)


def _delete_generated_object(obj):
    """Fault-injection seam for one exact generated Object deletion."""

    bpy.data.objects.remove(obj, do_unlink=True)


def _delete_generated_curve(curve_data):
    """Fault-injection seam for the exact generated Curve Data deletion."""

    bpy.data.curves.remove(curve_data)


def _discard_removal_backup(backup):
    for item in reversed(backup.get("objects", ())):
        obj = item["backup"]
        try:
            if bpy.data.objects.get(obj.name) is obj:
                bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass
    curve_data = backup.get("curve_data_backup")
    try:
        if (
            curve_data is not None
            and bpy.data.curves.get(curve_data.name) is curve_data
            and not curve_data.users
        ):
            bpy.data.curves.remove(curve_data)
    except (ReferenceError, RuntimeError):
        pass


def _copy_generated_curve_object(obj, curve_data_backup):
    """Clone the generated Curve Object without ever referencing its live data.

    ``Object.copy()`` is unsafe for this transaction in Blender 5.2: copying a
    Curve Object and then changing ``data`` can leave an untracked Object ID
    holding the original Curve Data as an extra user.  Construct the backup
    directly on the already-copied datablock and reproduce the small, fully
    owned generated-object contract explicitly.
    """

    clone = bpy.data.objects.new(f"{obj.name}_RemovalBackup", curve_data_backup)
    try:
        clone.parent = obj.parent
        clone.parent_type = obj.parent_type
        clone.parent_bone = obj.parent_bone
        clone.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
        clone.matrix_basis = obj.matrix_basis.copy()
        clone.rotation_mode = obj.rotation_mode
        clone.display_type = obj.display_type
        clone.show_in_front = obj.show_in_front
        clone.show_name = obj.show_name
        clone.show_axis = obj.show_axis
        clone.show_wire = obj.show_wire
        clone.show_all_edges = obj.show_all_edges
        clone.hide_select = obj.hide_select
        clone.color = tuple(obj.color)
        for key in obj.keys():
            clone[key] = obj[key]
        for source in obj.modifiers:
            if source.type != "HOOK":
                raise SplineIKSetupError(
                    "The generated Curve has an unexpected modifier during backup."
                )
            modifier = clone.modifiers.new(source.name, type="HOOK")
            modifier.object = source.object
            modifier.vertex_group = source.vertex_group
            modifier.strength = source.strength
            modifier.falloff_type = source.falloff_type
            modifier.falloff_radius = source.falloff_radius
            modifier.use_falloff_uniform = source.use_falloff_uniform
            modifier.matrix_inverse = source.matrix_inverse.copy()
            modifier.show_viewport = source.show_viewport
            modifier.show_render = source.show_render
            modifier.show_in_editmode = source.show_in_editmode
            modifier.show_on_cage = source.show_on_cage
            modifier.show_expanded = source.show_expanded
            modifier.vertex_indices_set(tuple(source.vertex_indices))
        return clone
    except Exception:
        if bpy.data.objects.get(clone.name) is clone:
            bpy.data.objects.remove(clone, do_unlink=True)
        raise


def _snapshot_removal_inventory(inventory, armature, chain_names, rig_id):
    backup = {"objects": (), "curve_data_backup": None}
    object_items = []
    try:
        curve_data_backup = inventory["curve_data"].copy()
        _clear_owner_tags(curve_data_backup)
        backup["curve_data_backup"] = curve_data_backup
        for obj in inventory["objects"]:
            if obj is inventory["curve_object"]:
                obj_backup = _copy_generated_curve_object(obj, curve_data_backup)
            else:
                obj_backup = obj.copy()
            _clear_owner_tags(obj_backup)
            object_items.append(
                {
                    "original": obj,
                    "backup": obj_backup,
                    "name": obj.name,
                    "collections": tuple(obj.users_collection),
                    "role": obj.get(ROLE_KEY),
                    "control_index": obj.get(CONTROL_INDEX_KEY),
                    "hide_viewport": bool(obj.hide_viewport),
                    "hide_render": bool(obj.hide_render),
                    "hide_get": bool(obj.hide_get()),
                    "selected": bool(obj.select_get()),
                }
            )
        backup.update(
            {
                "objects": tuple(object_items),
                "curve_data_name": inventory["curve_data"].name,
                "constraint": _snapshot_foreign_constraint(
                    inventory["constraint_owner"],
                    inventory["constraint"],
                ),
                "roll_bone_channels": _snapshot_registered_bone_roll_channels(
                    armature,
                    inventory["curve_data"],
                ),
                "armature": armature,
                "chain_names": tuple(chain_names),
                "rig_id": rig_id,
            }
        )
        return backup
    except Exception:
        _discard_removal_backup(backup)
        raise


def _restore_removal_backup(context, backup):
    armature = backup["armature"]
    chain_names = backup["chain_names"]
    rig_id = backup["rig_id"]
    owner = backup["constraint"]["pose_bone"]
    surviving_constraint = owner.constraints.get(backup["constraint"]["name"])
    if surviving_constraint is not None:
        _remove_constraint(owner, surviving_constraint)

    # Clear any exact surviving originals before re-linking the complete copy.
    for item in reversed(backup["objects"]):
        original = item["original"]
        try:
            if bpy.data.objects.get(item["name"]) is original:
                bpy.data.objects.remove(original, do_unlink=True)
        except ReferenceError:
            pass
    original_curve_data = bpy.data.curves.get(backup["curve_data_name"])
    curve_data_backup = backup["curve_data_backup"]
    if original_curve_data is not None and original_curve_data is not curve_data_backup:
        if original_curve_data.users:
            raise SplineIKSetupError(
                "Removal recovery found the original Curve Data still in unexpected use."
            )
        bpy.data.curves.remove(original_curve_data)

    restored = {}
    for item in backup["objects"]:
        obj = item["backup"]
        obj.name = item["name"]
        for collection in item["collections"]:
            collection.objects.link(obj)
        _set_tags(
            obj,
            rig_id,
            item["role"],
            armature,
            chain_names,
            control_index=(
                item["control_index"] if item["role"] == "CONTROL" else None
            ),
        )
        obj.hide_viewport = item["hide_viewport"]
        obj.hide_render = item["hide_render"]
        try:
            obj.hide_set(item["hide_get"])
        except RuntimeError:
            pass
        restored[item["role"], item["control_index"]] = obj

    curve_obj = restored["CURVE", None]
    curve_data_backup.name = backup["curve_data_name"]
    _set_tags(curve_data_backup, rig_id, "CURVE_DATA", armature, chain_names)
    controls = {
        index: obj
        for (role, index), obj in restored.items()
        if role == "CONTROL"
    }
    for index, modifier in enumerate(curve_obj.modifiers):
        modifier.object = controls[index]
    _retarget_registered_rotation_tilt(
        curve_data_backup,
        tuple(controls[index] for index in range(len(controls))),
        rig_id,
    )
    _restore_registered_bone_roll(
        armature,
        curve_data_backup,
        tuple(controls[index] for index in range(len(controls))),
        rig_id,
        channel_snapshots=backup.get("roll_bone_channels", ()),
    )
    context.view_layer.update()
    for item in backup["objects"]:
        obj = item["backup"]
        try:
            obj.select_set(item["selected"])
        except RuntimeError:
            pass

    backup["constraint"]["values"]["target"] = curve_obj
    _restore_foreign_constraint(backup["constraint"])
    inventory = _rig_inventory(context, armature, chain_names, rig_id)
    backup["objects"] = ()
    backup["curve_data_backup"] = None
    return inventory


def _hook_registry(curve_obj):
    raw = curve_obj.get(HOOK_REGISTRY_KEY, "")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SplineIKSetupError("The generated Hook registry is corrupt.") from exc
    if not isinstance(payload, list):
        raise SplineIKSetupError("The generated Hook registry is invalid.")
    return payload


def _hook_name_snapshot(curve_obj, controls, rig_id):
    modifiers, name_state, _registry = _validated_hook_modifiers(
        curve_obj,
        controls,
        rig_id,
    )
    return {
        "curve_object": curve_obj,
        "controls": tuple(controls),
        "rig_id": rig_id,
        "modifiers": modifiers,
        "names": tuple(modifier.name for modifier in modifiers),
        "registry": curve_obj[HOOK_REGISTRY_KEY],
        "name_state": name_state,
    }


def _restore_hook_name_snapshot(context, snapshot):
    curve_obj = snapshot["curve_object"]
    modifiers = snapshot["modifiers"]
    # Move every owned modifier through a unique temporary namespace so even a
    # future display-name scheme that swaps names can roll back without Blender
    # silently adding numeric suffixes.
    restore_token = uuid.uuid4().hex
    for index, modifier in enumerate(modifiers):
        modifier.name = f"__CD_HOOK_RESTORE_{restore_token}_{index:02d}"
    for modifier, old_name in zip(modifiers, snapshot["names"]):
        modifier.name = old_name
        if modifier.name != old_name:
            raise SplineIKSetupError(
                f"Could not restore generated Hook name '{old_name}'."
            )
    curve_obj[HOOK_REGISTRY_KEY] = snapshot["registry"]
    context.view_layer.update()
    restored, state, _registry = _validated_hook_modifiers(
        curve_obj,
        snapshot["controls"],
        snapshot["rig_id"],
    )
    if (
        tuple(modifier.as_pointer() for modifier in restored)
        != tuple(modifier.as_pointer() for modifier in modifiers)
        or state != snapshot["name_state"]
    ):
        raise SplineIKSetupError("The generated Hook names did not restore exactly.")


def _upgrade_legacy_hook_names(context, curve_obj, controls, rig_id):
    """Replace UUID-bearing modifier labels without replacing any modifier."""

    snapshot = _hook_name_snapshot(curve_obj, controls, rig_id)
    if snapshot["name_state"] == HOOK_NAME_CURRENT:
        return None
    if snapshot["name_state"] != HOOK_NAME_LEGACY:
        raise SplineIKSetupError("The generated Hook names cannot be upgraded safely.")

    before_curve = _evaluated_curve_positions(context, curve_obj)
    records = _hook_registry(curve_obj)
    try:
        for index, modifier in enumerate(snapshot["modifiers"]):
            target_name = _hook_display_name(index)
            collision = curve_obj.modifiers.get(target_name)
            if (
                collision is not None
                and collision.as_pointer() != modifier.as_pointer()
            ):
                raise SplineIKSetupError(
                    f"Hook modifier name '{target_name}' is already in use."
                )
        for index, modifier in enumerate(snapshot["modifiers"]):
            target_name = _hook_display_name(index)
            modifier.name = target_name
            if modifier.name != target_name:
                raise SplineIKSetupError(
                    f"Could not assign Hook modifier name '{target_name}'."
                )
            records[index] = dict(records[index])
            records[index]["name"] = target_name
        _write_hook_registry(curve_obj, records)
        context.view_layer.update()

        resolved, name_state, _registry = _validated_hook_modifiers(
            curve_obj,
            controls,
            rig_id,
        )
        if (
            tuple(modifier.as_pointer() for modifier in resolved)
            != tuple(modifier.as_pointer() for modifier in snapshot["modifiers"])
            or name_state != HOOK_NAME_CURRENT
        ):
            raise SplineIKSetupError("The readable Hook names did not validate.")
        if not _curve_positions_match(
            before_curve,
            _evaluated_curve_positions(context, curve_obj),
        ):
            raise SplineIKSetupError(
                "Renaming the Hook modifiers changed the evaluated Curve."
            )
        return snapshot
    except Exception as exc:
        try:
            _restore_hook_name_snapshot(context, snapshot)
        except Exception as recovery_exc:
            raise SplineIKSetupError(
                "Hook modifier naming failed. Recovery also failed: "
                f"{recovery_exc}"
            ) from exc
        if isinstance(exc, SplineIKSetupError):
            detail = str(exc)
        else:
            detail = f"{type(exc).__name__}: {exc}"
        raise SplineIKSetupError(
            "Hook modifier names were not changed and the rig was restored unchanged: "
            f"{detail}"
        ) from exc


def _rig_inventory(context, armature, chain_names, rig_id):
    _require_current_scene(context, armature)
    constraints = _owned_constraints(
        armature,
        rig_id=rig_id,
        chain_names=chain_names,
    )
    if len(constraints) != 1:
        raise SplineIKSetupError(
            "The generated setup must have exactly one owned Spline IK constraint."
        )
    tip_pose_bone, constraint = constraints[0]
    if tip_pose_bone.name != chain_names[-1]:
        raise SplineIKSetupError("The owned Spline IK is no longer on the captured tip bone.")

    curve_candidates = [
        obj
        for obj in bpy.data.objects
        if _is_owned(
            obj,
            rig_id=rig_id,
            role="CURVE",
            armature=armature,
            chain_names=chain_names,
        )
    ]
    if len(curve_candidates) != 1 or constraint.target is not curve_candidates[0]:
        raise SplineIKSetupError(
            "The generated setup has an ambiguous, duplicated, or mismatched Curve."
        )
    curve_obj = curve_candidates[0]
    _require_generated_object_scene(context, curve_obj)
    if curve_obj.type != "CURVE" or curve_obj.parent is not armature:
        raise SplineIKSetupError("The generated Curve parent or type no longer matches.")
    curve_data = curve_obj.data
    if (
        curve_data is None
        or curve_data.users != 1
        or not _is_owned(
            curve_data,
            rig_id=rig_id,
            role="CURVE_DATA",
            armature=armature,
            chain_names=chain_names,
        )
    ):
        raise SplineIKSetupError(
            "The generated Curve Data is missing, shared, copied, or mismatched."
        )

    controls = [
        obj
        for obj in bpy.data.objects
        if _is_owned(
            obj,
            rig_id=rig_id,
            role="CONTROL",
            armature=armature,
            chain_names=chain_names,
        )
    ]
    registry = _hook_registry(curve_obj)
    if len(registry) < 3 or len(controls) != len(registry):
        raise SplineIKSetupError("The generated control or Hook count no longer matches.")
    controls_by_index = {}
    for control in controls:
        _require_generated_object_scene(context, control)
        if control.type != "EMPTY" or control.parent is not armature:
            raise SplineIKSetupError(
                f"Generated control '{control.name}' has a mismatched type or parent."
            )
        index = control.get(CONTROL_INDEX_KEY)
        if not isinstance(index, int) or isinstance(index, bool) or index in controls_by_index:
            raise SplineIKSetupError("Generated control indices are missing or duplicated.")
        controls_by_index[index] = control
    expected_indices = set(range(len(registry)))
    if set(controls_by_index) != expected_indices:
        raise SplineIKSetupError("Generated control indices are not contiguous.")

    ordered_controls = tuple(
        controls_by_index[index] for index in range(len(registry))
    )
    hook_modifiers, hook_name_state, _registry = _validated_hook_modifiers(
        curve_obj,
        ordered_controls,
        rig_id,
    )

    all_tagged_objects = [
        obj
        for obj in bpy.data.objects
        if obj.get(RIG_ID_KEY) == rig_id and obj.get(OWNER_KEY) == OWNER_VALUE
    ]
    expected_objects = {curve_obj, *controls_by_index.values()}
    if set(all_tagged_objects) != expected_objects:
        raise SplineIKSetupError(
            "Duplicate or foreign copied objects share this generated rig identifier."
        )
    all_tagged_curves = [
        data
        for data in bpy.data.curves
        if data.get(RIG_ID_KEY) == rig_id and data.get(OWNER_KEY) == OWNER_VALUE
    ]
    if all_tagged_curves != [curve_data]:
        raise SplineIKSetupError(
            "Duplicate or foreign Curve Data shares this generated rig identifier."
        )
    tilt_state, tilt_detail = _rotation_tilt_state(
        curve_obj,
        ordered_controls,
        rig_id,
    )
    frame_state, frame_detail = _control_frame_state(
        curve_obj,
        ordered_controls,
        rig_id,
    )
    return {
        "constraint_owner": tip_pose_bone,
        "constraint": constraint,
        "curve_object": curve_obj,
        "curve_data": curve_data,
        "controls": ordered_controls,
        "hook_modifiers": hook_modifiers,
        "hook_name_state": hook_name_state,
        "objects": (curve_obj, *ordered_controls),
        "rotation_tilt_state": tilt_state,
        "rotation_tilt_detail": tilt_detail,
        "control_frame_state": frame_state,
        "control_frame_detail": frame_detail,
    }


def _remove_inventory(context, armature, chain_names, rig_id, inventory):
    # The complete inventory has already passed all ambiguity and sharing
    # checks. Delete exact resolved pointers only; never rescan by tag to delete.
    if inventory.get("rotation_tilt_state") == "INVALID":
        raise SplineIKSetupError(
            inventory.get("rotation_tilt_detail")
            or "Rotation Tilt ownership is invalid; generated removal was refused."
        )
    if inventory.get("control_frame_state") == "INVALID":
        raise SplineIKSetupError(
            inventory.get("control_frame_detail")
            or "Control-axis frame ownership is invalid; generated removal was refused."
        )
    curve_animation = inventory["curve_object"].animation_data
    if curve_animation is not None and (
        curve_animation.action is not None
        or curve_animation.drivers
        or curve_animation.nla_tracks
    ):
        raise SplineIKSetupError(
            "Remove Generated was refused because the generated Curve Object has "
            "animation, drivers, or NLA data. Remove that artist data explicitly first."
        )
    backup = _snapshot_removal_inventory(inventory, armature, chain_names, rig_id)
    object_count = len(inventory["objects"])
    try:
        _remove_registered_bone_roll(
            armature,
            inventory["curve_data"],
            restore_baseline=True,
        )
        _delete_generated_constraint(
            inventory["constraint_owner"],
            inventory["constraint"],
        )
        for obj in reversed(inventory["objects"]):
            if bpy.data.objects.get(obj.name) is not obj:
                raise SplineIKSetupError(
                    f"Generated object '{obj.name}' disappeared during removal."
                )
            _delete_generated_object(obj)
        curve_data = inventory["curve_data"]
        if bpy.data.curves.get(curve_data.name) is not curve_data or curve_data.users:
            raise SplineIKSetupError("Generated Curve Data could not be removed safely.")
        _delete_generated_curve(curve_data)
        if _owned_constraints(armature, rig_id=rig_id, chain_names=chain_names):
            raise SplineIKSetupError("The owned Spline IK constraint survived removal.")
        if any(
            obj.get(RIG_ID_KEY) == rig_id and obj.get(OWNER_KEY) == OWNER_VALUE
            for obj in bpy.data.objects
        ):
            raise SplineIKSetupError("Generated objects survived removal.")
    except Exception as exc:
        try:
            _restore_removal_backup(context, backup)
        except Exception as restore_exc:
            raise SplineIKSetupError(
                f"Generated setup removal failed: {exc}. Recovery also failed: {restore_exc}"
            ) from restore_exc
        raise SplineIKSetupError(
            f"Generated setup removal failed and was restored unchanged: {exc}"
        ) from exc
    _discard_removal_backup(backup)
    return {"constraints": 1, "objects": object_count, "curves": 1}


def _remove_rig(context, armature, chain_names, rig_id):
    inventory = _rig_inventory(context, armature, chain_names, rig_id)
    return _remove_inventory(context, armature, chain_names, rig_id, inventory)


def _rollback(created_constraints, created_objects, created_curves):
    for curve in reversed(created_curves):
        try:
            if ROLL_DRIVER_REGISTRY_KEY not in curve:
                continue
            armature = bpy.data.objects.get(curve.get(ARMATURE_KEY, ""))
            if armature is not None and armature.type == "ARMATURE":
                _remove_registered_bone_roll(
                    armature,
                    curve,
                    restore_baseline=True,
                    preserve_current_channels=False,
                )
        except (ReferenceError, RuntimeError, SplineIKSetupError, TypeError, ValueError):
            pass
    for pose_bone, constraint in reversed(created_constraints):
        try:
            _remove_constraint(pose_bone, constraint)
        except (ReferenceError, RuntimeError):
            pass
    for obj in reversed(created_objects):
        try:
            if bpy.data.objects.get(obj.name) is obj:
                bpy.data.objects.remove(obj, do_unlink=True)
        except (ReferenceError, RuntimeError):
            pass
    for curve in reversed(created_curves):
        try:
            if bpy.data.curves.get(curve.name) is curve and not curve.users:
                bpy.data.curves.remove(curve)
        except (ReferenceError, RuntimeError):
            pass


def _snapshot_edit_bone_state(armature):
    active = armature.data.edit_bones.active
    return {
        "bones": {
            bone.name: (
                bool(bone.select),
                bool(bone.select_head),
                bool(bone.select_tail),
            )
            for bone in armature.data.edit_bones
        },
        "active": active.name if active is not None else "",
    }


def _restore_edit_bone_state(context, armature, snapshot):
    context.view_layer.objects.active = armature
    armature.select_set(True)
    result = bpy.ops.object.mode_set(mode="EDIT")
    if result != {"FINISHED"} or context.mode != "EDIT_ARMATURE" or context.object is not armature:
        raise SplineIKSetupError("Blender could not restore the captured Armature Edit Mode.")
    edit_bones = armature.data.edit_bones
    if set(snapshot["bones"]) != {bone.name for bone in edit_bones}:
        raise SplineIKSetupError("The Armature bones changed while restoring Edit Mode.")
    for bone in edit_bones:
        select, select_head, select_tail = snapshot["bones"][bone.name]
        bone.select = select
        bone.select_head = select_head
        bone.select_tail = select_tail
    active_name = snapshot["active"]
    if active_name:
        active = edit_bones.get(active_name)
        if active is None:
            raise SplineIKSetupError("The active Edit Bone disappeared while restoring context.")
        edit_bones.active = active
    restored = _snapshot_edit_bone_state(armature)
    if restored != snapshot:
        raise SplineIKSetupError("Blender did not restore the Edit Bone selection exactly.")


def _current_rig(context, settings):
    armature = settings.armature if settings is not None else None
    names = _captured_names(settings)
    if armature is None or not names:
        raise SplineIKSetupError("Capture a bone chain first.")
    _require_current_scene(context, armature)
    _chain_bones(armature, names)
    rig_id = _resolved_rig_id(settings, armature, names)
    if not rig_id:
        raise SplineIKSetupError("No generated Spline IK setup matches the captured chain.")
    inventory = _rig_inventory(context, armature, names, rig_id)
    return armature, names, rig_id, inventory


def _window_region(area):
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _reveal_selected_controls(context, controls):
    """Select generated controls, frame them, and reveal the active one in Outliner."""
    if context.mode != "OBJECT":
        result = bpy.ops.object.mode_set(mode="OBJECT")
        if result != {"FINISHED"}:
            raise SplineIKSetupError("Blender could not enter Object Mode to select controls.")

    for obj in context.view_layer.objects:
        obj.select_set(False)

    selected = []
    for control in controls:
        if context.view_layer.objects.get(control.name) is not control:
            continue
        control.hide_viewport = False
        control.hide_select = False
        try:
            control.hide_set(False)
            control.select_set(True)
        except RuntimeError:
            continue
        if control.select_get(view_layer=context.view_layer):
            selected.append(control)

    if not selected:
        raise SplineIKSetupError(
            "The controls are hidden or excluded from the current View Layer."
        )

    active = selected[len(selected) // 2]
    context.view_layer.objects.active = active

    screen = getattr(context, "screen", None)
    if screen is not None:
        view3d_area = context.area if context.area and context.area.type == "VIEW_3D" else None
        if view3d_area is None:
            view3d_area = next((area for area in screen.areas if area.type == "VIEW_3D"), None)
        if view3d_area is not None:
            space = view3d_area.spaces.active
            overlay = getattr(space, "overlay", None)
            if overlay is not None:
                overlay.show_extras = True
            region = _window_region(view3d_area)
            if region is not None:
                try:
                    with context.temp_override(area=view3d_area, region=region):
                        bpy.ops.view3d.view_selected(use_all_regions=False)
                except RuntimeError:
                    pass

        outliner_area = next((area for area in screen.areas if area.type == "OUTLINER"), None)
        if outliner_area is not None:
            region = _window_region(outliner_area)
            if region is not None:
                try:
                    with context.temp_override(area=outliner_area, region=region):
                        bpy.ops.outliner.show_active()
                except RuntimeError:
                    pass

    return tuple(selected), active


class CharacterDesignerSplineIKState(PropertyGroup):
    armature: PointerProperty(
        name="Armature",
        type=bpy.types.Object,
        poll=_armature_poll,
        options={"SKIP_SAVE"},
    )
    point_count: IntProperty(
        name="Curve Points",
        description="Number of Bezier points and Hook controls sampled along the chain",
        default=5,
        min=3,
        max=12,
        options={"SKIP_SAVE"},
    )
    control_size_ratio: FloatProperty(
        name="Control Size Ratio",
        description="Wire control size relative to the selected chain's local total length",
        default=0.06,
        min=0.005,
        max=0.5,
        precision=3,
        options={"SKIP_SAVE"},
    )
    replace_existing: BoolProperty(
        name="Replace Existing Spline IK",
        description="Replace one unambiguous non-Character-Designer Spline IK on the tip bone",
        default=False,
        options={"SKIP_SAVE"},
    )
    chain_names_json: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    chain_count: IntProperty(default=0, min=0, options={"HIDDEN", "SKIP_SAVE"})
    active_rig_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    last_level: StringProperty(default="NONE", options={"HIDDEN", "SKIP_SAVE"})
    last_message: StringProperty(options={"HIDDEN", "SKIP_SAVE"})


class CHARACTERDESIGNER_OT_spline_ik_capture_chain(Operator):
    bl_idname = "character_designer.spline_ik_capture_chain"
    bl_label = "Capture Bone Chain"
    bl_description = "Capture one strictly continuous selected parent-child bone chain"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == "ARMATURE"
            and context.mode in {"POSE", "EDIT_ARMATURE"}
        )

    def execute(self, context):
        settings = _settings(context)
        try:
            if settings is None:
                raise SplineIKSetupError("Spline IK state is unavailable.")
            armature, names = _selected_chain(context)
            _chain_geometry(armature, names)
            settings.armature = armature
            settings.chain_names_json = _canonical_chain_json(names)
            settings.chain_count = len(names)
            settings.active_rig_id = _resolved_rig_id(settings, armature, names)
            message = f"Captured {len(names)} bones: {names[0]} -> {names[-1]}."
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (SplineIKSetupError, ReferenceError, RuntimeError) as exc:
            previous = _previous_capture_summary(settings)
            if previous is not None:
                _armature, names = previous
                message = (
                    f"No new chain captured; kept the previous {len(names)}-bone chain: "
                    f"{names[0]} -> {names[-1]}. {exc}"
                )
            else:
                message = str(exc)
            _set_status(settings, "WARNING", message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}


class CHARACTERDESIGNER_OT_spline_ik_build(Operator):
    bl_idname = "character_designer.spline_ik_build"
    bl_label = "Build Spline IK Setup"
    bl_description = "Build a non-stretching Spline IK Curve and wire Hook controls"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return bool(settings and settings.armature and settings.chain_names_json)

    def execute(self, context):
        settings = _settings(context)
        created_constraints = []
        created_objects = []
        created_curves = []
        armature = None
        restore_edit_mode = context.mode == "EDIT_ARMATURE"
        switched_captured_armature_to_pose = False
        edit_state_snapshot = None
        result = None
        deferred_error = None
        foreign_snapshot = None
        foreign_removed = False
        try:
            if settings is None:
                raise SplineIKSetupError("Spline IK state is unavailable.")
            armature = settings.armature
            names = _captured_names(settings)
            if context.mode not in {"OBJECT", "POSE", "EDIT_ARMATURE"}:
                raise SplineIKSetupError(
                    "Return to Object, Pose, or Armature Edit Mode before building."
                )
            if context.mode in {"POSE", "EDIT_ARMATURE"} and (
                context.object is not armature
                or context.view_layer.objects.active is not armature
            ):
                raise SplineIKSetupError(
                    "The captured Armature must be the active Armature while building from Pose "
                    "or Armature Edit Mode. Object Mode can build from the stored capture directly."
                )
            _require_current_scene(context, armature)
            _bones, joints, total_bone_length = _chain_geometry(armature, names)
            try:
                armature.matrix_world.inverted()
            except ValueError as exc:
                raise SplineIKSetupError(
                    "The Armature has a singular object transform; remove zero scale before building."
                ) from exc

            # Pose constraints cannot be created reliably while the Armature's
            # edit-bone table is live. Switch only for the transaction and
            # restore the artist's Edit Mode state in the finally block.
            if restore_edit_mode:
                edit_state_snapshot = _snapshot_edit_bone_state(armature)
                result = bpy.ops.object.mode_set(mode="POSE")
                if result != {"FINISHED"}:
                    raise SplineIKSetupError("Blender could not enter Pose Mode to build Spline IK.")
                switched_captured_armature_to_pose = True

            tip_pose_bone = armature.pose.bones.get(names[-1]) if armature.pose else None
            if tip_pose_bone is None:
                raise SplineIKSetupError("The captured tip Pose Bone is unavailable.")
            # This PoseBone is the only ID owner we may write below. Validate
            # its raw registry before creating any object or constraint so a
            # malformed value is neither overwritten nor left with residue.
            _constraint_registry(tip_pose_bone, strict=True)

            same_rig_matches = _owned_constraints(armature, chain_names=names)
            same_rig_ids = {
                _constraint_record(pose_bone, constraint, armature, names).get("rig_id", "")
                for pose_bone, constraint in same_rig_matches
            }
            same_rig_ids.discard("")
            if len(same_rig_ids) > 1:
                raise SplineIKSetupError(
                    "Multiple generated setups match this chain; remove them before rebuilding."
                )
            old_rig_id = next(iter(same_rig_ids)) if same_rig_ids else ""
            if old_rig_id:
                _rig_inventory(context, armature, names, old_rig_id)
                raise SplineIKSetupError(
                    "A generated setup already exists for this chain. "
                    "Use Remove Generated before building a replacement."
                )

            spline_constraints = [
                constraint
                for constraint in tip_pose_bone.constraints
                if constraint.type == "SPLINE_IK"
            ]
            foreign_constraints = [
                constraint
                for constraint in spline_constraints
                if _constraint_record(tip_pose_bone, constraint, armature) is None
            ]
            for foreign_constraint in foreign_constraints:
                target = foreign_constraint.target
                if target is not None and target.get(OWNER_KEY) == OWNER_VALUE:
                    raise SplineIKSetupError(
                        "A Spline IK targets Character Designer data but has an invalid "
                        "ownership registry; repair or remove it manually."
                    )
            owned_other = []
            for constraint in spline_constraints:
                record = _constraint_record(tip_pose_bone, constraint, armature)
                if record is not None and record.get("rig_id", "") != old_rig_id:
                    owned_other.append(constraint)
            if owned_other:
                raise SplineIKSetupError(
                    "The tip bone has another generated setup; remove it before building this chain."
                )
            if foreign_constraints and not settings.replace_existing:
                raise SplineIKSetupError(
                    "The tip bone already has a non-Character-Designer Spline IK. "
                    "Enable Replace Existing to replace one unambiguous constraint."
                )
            if len(foreign_constraints) > 1:
                raise SplineIKSetupError(
                    "The tip bone has multiple non-Character-Designer Spline IK constraints; "
                    "remove all but the intended one manually."
                )
            if foreign_constraints:
                foreign_snapshot = _snapshot_foreign_constraint(
                    tip_pose_bone,
                    foreign_constraints[0],
                )

            samples = _sample_polyline(joints, settings.point_count)
            control_size = total_bone_length * settings.control_size_ratio
            if control_size <= EPSILON:
                raise SplineIKSetupError("Control Size Ratio produces a zero-sized control.")

            new_rig_id = uuid.uuid4().hex
            curve_obj, controls = _create_curve_and_controls(
                context,
                armature,
                names,
                samples,
                control_size,
                new_rig_id,
                created_objects,
                created_curves,
            )
            constraint = tip_pose_bone.constraints.new(type="SPLINE_IK")
            created_constraints.append((tip_pose_bone, constraint))
            _configure_constraint(constraint, curve_obj, len(names))
            _tag_constraint(
                tip_pose_bone,
                constraint,
                new_rig_id,
                armature,
                names,
            )
            # Validate the complete newly-created ownership graph before any
            # irreversible replacement commit.
            _rig_inventory(context, armature, names, new_rig_id)

            # Commit only after the complete replacement exists. Removing a
            # foreign constraint never removes or edits its target Curve.
            if foreign_constraints:
                tip_pose_bone.constraints.remove(foreign_constraints[0])
                foreign_removed = True

            settings.active_rig_id = new_rig_id
            settings.chain_count = len(names)
            message = (
                f"Built {len(names)}-bone Spline IK with {len(controls)} Hook controls "
                "and no Y-axis stretch. Local control Y rotation now drives Curve Tilt "
                "and interpolated bone Roll. "
                "Use Select & Reveal Controls to start posing."
            )
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            result = {"FINISHED"}
        except (SplineIKSetupError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            transaction_started = bool(
                created_constraints or created_objects or created_curves or foreign_removed
            )
            _rollback(created_constraints, created_objects, created_curves)
            if foreign_removed and foreign_snapshot is not None:
                try:
                    _restore_foreign_constraint(foreign_snapshot)
                except (ReferenceError, RuntimeError, TypeError, ValueError) as restore_exc:
                    exc = SplineIKSetupError(
                        f"{exc} The replaced artist constraint could not be restored: {restore_exc}"
                    )
            level = "ERROR" if transaction_started else "WARNING"
            report_level = "ERROR" if transaction_started else "WARNING"
            _set_status(settings, level, str(exc))
            self.report({report_level}, str(exc))
            result = {"CANCELLED"}
        finally:
            if (
                restore_edit_mode
                and switched_captured_armature_to_pose
                and armature is not None
                and bpy.data.objects.get(armature.name) is armature
                and armature.mode != "EDIT"
            ):
                try:
                    _restore_edit_bone_state(context, armature, edit_state_snapshot)
                except (SplineIKSetupError, ReferenceError, RuntimeError) as restore_exc:
                    deferred_error = restore_exc
        if deferred_error is not None:
            message = f"Build result was cancelled because Edit Mode restoration failed: {deferred_error}"
            _set_status(settings, "ERROR", message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        return result or {"CANCELLED"}


class CHARACTERDESIGNER_OT_spline_ik_select_controls(Operator):
    bl_idname = "character_designer.spline_ik_select_controls"
    bl_label = "Select & Reveal Controls"
    bl_description = (
        "Select and frame this captured chain's Hook controls, enable Empty display, "
        "and reveal the active control in Outliner"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return bool(settings and settings.armature and settings.chain_names_json)

    def execute(self, context):
        settings = _settings(context)
        try:
            _armature, _names, _rig_id, inventory = _current_rig(context, settings)
            selected, active = _reveal_selected_controls(context, inventory["controls"])
            message = (
                f"Selected and revealed {len(selected)} Spline IK controls; "
                f"Outliner active: {active.name}."
            )
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (SplineIKSetupError, ReferenceError, RuntimeError) as exc:
            _set_status(settings, "WARNING", str(exc))
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


class CHARACTERDESIGNER_OT_spline_ik_enable_rotation_tilt(Operator):
    bl_idname = "character_designer.spline_ik_enable_rotation_tilt"
    bl_label = "Enable Rotation Tilt"
    bl_description = (
        "Update this generated setup in place: use readable Hook names, align each "
        "Empty to the Curve/strap frame, and enable local-Y Tilt and bone Roll"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return bool(settings and settings.armature and settings.chain_names_json)

    def execute(self, context):
        settings = _settings(context)
        hook_snapshot = None
        try:
            _armature, _names, rig_id, inventory = _current_rig(context, settings)
            state = inventory["rotation_tilt_state"]
            frame_state = inventory["control_frame_state"]

            if state == "ENABLED":
                version = _rotation_tilt_contract_version(inventory["curve_data"])
                if frame_state == "MISSING" and version == LEGACY_DRIVER_VERSION:
                    action = "ALIGN_AXES"
                elif frame_state == "ENABLED":
                    action = "CLEAN_NAMES"
                else:
                    raise SplineIKSetupError(
                        inventory["control_frame_detail"]
                        or "Rotation Tilt control-axis metadata is invalid."
                    )
            elif state == "MISSING":
                action = "ENABLE_TILT"
            else:
                raise SplineIKSetupError(
                    inventory["rotation_tilt_detail"]
                    or "Rotation Tilt cannot be enabled safely on this setup."
                )

            hook_names_cleaned = inventory["hook_name_state"] == HOOK_NAME_LEGACY
            if hook_names_cleaned:
                hook_snapshot = _upgrade_legacy_hook_names(
                    context,
                    inventory["curve_object"],
                    inventory["controls"],
                    rig_id,
                )

            if action == "ALIGN_AXES":
                _upgrade_enabled_rotation_tilt_axes(
                    context,
                    inventory["curve_object"],
                    inventory["controls"],
                    rig_id,
                )
                hook_snapshot = None
                message = (
                    f"Aligned {len(inventory['controls'])} existing Rotation Tilt controls "
                    "in place. Local axes are X=width, Y=tangent/Twist, Z=front normal."
                )
                if hook_names_cleaned:
                    message += " Hook modifier names are now short and readable."
                _set_status(settings, "SUCCESS", message)
                self.report({"INFO"}, message)
                return {"FINISHED"}

            if action == "CLEAN_NAMES":
                hook_snapshot = None
                if hook_names_cleaned:
                    message = (
                        f"Cleaned {len(inventory['controls'])} Hook modifier names without "
                        "changing their bindings or the internal rig ID."
                    )
                    level = "SUCCESS"
                else:
                    message = (
                        "Rotation Tilt, strap-relative axes, and readable Hook names "
                        "are already enabled."
                    )
                    level = "INFO"
                _set_status(settings, level, message)
                self.report({"INFO"}, message)
                return {"FINISHED"}

            _enable_rotation_tilt(
                context,
                inventory["curve_object"],
                inventory["controls"],
                rig_id,
                legacy_upgrade=True,
            )
            hook_snapshot = None
            message = (
                f"Enabled Rotation Tilt on {len(inventory['controls'])} controls without "
                "changing the current Curve or bone pose. Local axes are X=width, "
                "Y=tangent/Twist, Z=front normal."
            )
            if hook_names_cleaned:
                message += " Hook modifier names are now short and readable."
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (SplineIKSetupError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
            if hook_snapshot is not None:
                try:
                    _restore_hook_name_snapshot(context, hook_snapshot)
                except (SplineIKSetupError, ReferenceError, RuntimeError, TypeError, ValueError) as recovery_exc:
                    exc = SplineIKSetupError(
                        f"{exc} Recovery also failed while restoring Hook names: "
                        f"{recovery_exc}"
                    )
            message = str(exc)
            level = "ERROR" if "Recovery also failed" in message else "WARNING"
            _set_status(settings, level, message)
            self.report({"ERROR" if level == "ERROR" else "WARNING"}, message)
            return {"CANCELLED"}


class CHARACTERDESIGNER_OT_spline_ik_clear_status(Operator):
    bl_idname = "character_designer.spline_ik_clear_status"
    bl_label = "Dismiss Spline IK Status"
    bl_description = "Dismiss the current Spline IK message"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return bool(settings and settings.last_message)

    def execute(self, context):
        settings = _settings(context)
        _set_status(settings, "NONE", "")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_spline_ik_remove_generated(Operator):
    bl_idname = "character_designer.spline_ik_remove_generated"
    bl_label = "Remove Generated"
    bl_description = "Remove only the exactly tagged generated setup for this captured chain"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return bool(settings and settings.armature and settings.chain_names_json)

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        settings = _settings(context)
        try:
            armature, names, rig_id, inventory = _current_rig(context, settings)
            summary = _remove_inventory(
                context,
                armature,
                names,
                rig_id,
                inventory,
            )
            if not any(summary.values()):
                raise SplineIKSetupError("No exactly tagged generated setup was removed.")
            settings.active_rig_id = ""
            message = (
                f"Removed {summary['objects']} generated objects and "
                f"{summary['constraints']} generated constraint."
            )
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (SplineIKSetupError, ReferenceError, RuntimeError) as exc:
            message = str(exc)
            level = "WARNING" if message.startswith("Remove Generated was refused") else "ERROR"
            _set_status(settings, level, message)
            self.report({"WARNING" if level == "WARNING" else "ERROR"}, message)
            return {"CANCELLED"}


def _draw_status(layout, settings):
    if not settings.last_message:
        return
    icon = {
        "ERROR": "ERROR",
        "WARNING": "INFO",
        "SUCCESS": "CHECKMARK",
        "INFO": "INFO",
    }.get(settings.last_level, "INFO")
    lines = textwrap.wrap(
        settings.last_message,
        width=48,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [settings.last_message]
    status_box = layout.box()
    for index, line in enumerate(lines):
        row = status_box.row(align=True)
        row.alert = settings.last_level == "ERROR"
        row.label(text=line, icon=icon if index == 0 else "NONE")
        if index == 0:
            row.operator(
                "character_designer.spline_ik_clear_status",
                text="",
                icon="X",
                emboss=False,
            )


class CHARACTERDESIGNER_PT_spline_ik_setup(Panel):
    bl_label = "Spline IK Setup"
    bl_idname = "CHARACTERDESIGNER_PT_spline_ik_setup"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return rig_page_active(context, "BODY")

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        if settings is None:
            layout.label(text="Spline IK state is unavailable.", icon="ERROR")
            return

        layout.prop(settings, "armature")
        layout.operator(
            "character_designer.spline_ik_capture_chain",
            text="Capture Selected Chain",
            icon="BONE_DATA",
        )
        try:
            names = _captured_names(settings)
        except SplineIKSetupError:
            names = ()
        if names:
            layout.label(
                text=f"{len(names)} bones: {names[0]} -> {names[-1]}",
                icon="CONSTRAINT_BONE",
            )
            layout.label(
                text="Curve span: captured root Head -> captured tip Tail",
                icon="CURVE_DATA",
            )

        layout.prop(settings, "point_count")
        layout.prop(settings, "control_size_ratio")
        layout.prop(settings, "replace_existing")

        build_row = layout.row()
        build_row.enabled = bool(settings.armature and names)
        build_row.operator(
            "character_designer.spline_ik_build",
            text="Build Spline IK Setup",
            icon="CON_SPLINEIK",
        )

        has_valid_rig = False
        inventory = None
        if settings.armature and names:
            try:
                rig_id = _resolved_rig_id(settings, settings.armature, names)
                if rig_id:
                    inventory = _rig_inventory(context, settings.armature, names, rig_id)
                    has_valid_rig = True
            except (SplineIKSetupError, ReferenceError, RuntimeError):
                has_valid_rig = False
        if has_valid_rig:
            layout.label(
                text=(
                    f"Ready: {len(inventory['controls'])} controls · "
                    f"{inventory['curve_object'].name}"
                ),
                icon="CHECKMARK",
            )
            tilt_state = inventory["rotation_tilt_state"]
            frame_state = inventory["control_frame_state"]
            hook_name_state = inventory["hook_name_state"]
            if tilt_state == "ENABLED":
                layout.label(
                    text="Twist: local Y → Curve Tilt + Bone Roll",
                    icon="DRIVER",
                )
                if frame_state == "ENABLED":
                    layout.label(
                        text="Axes: X width · Y tangent · Z front",
                        icon="EMPTY_AXIS",
                    )
                    if hook_name_state == HOOK_NAME_LEGACY:
                        layout.operator(
                            "character_designer.spline_ik_enable_rotation_tilt",
                            text="Clean Hook Names",
                            icon="MOD_HOOK",
                        )
                elif frame_state == "MISSING":
                    layout.label(
                        text="This enabled rig still uses legacy control axes.",
                        icon="INFO",
                    )
                    layout.operator(
                        "character_designer.spline_ik_enable_rotation_tilt",
                        text="Align Rotation Tilt Axes",
                        icon="EMPTY_AXIS",
                    )
            elif tilt_state == "MISSING":
                layout.operator(
                    "character_designer.spline_ik_enable_rotation_tilt",
                    text="Enable Rotation Tilt",
                    icon="DRIVER",
                )
            else:
                layout.label(
                    text="Rotation Tilt data needs manual repair.",
                    icon="INFO",
                )
        else:
            layout.label(
                text="Controls are created after a successful Build.",
                icon="INFO",
            )

        action_row = layout.row(align=True)
        action_row.enabled = has_valid_rig
        action_row.operator(
            "character_designer.spline_ik_select_controls",
            text="Select & Reveal Controls",
            icon="EMPTY_AXIS",
        )
        remove_row = action_row.row(align=True)
        remove_row.alert = has_valid_rig
        remove_row.operator(
            "character_designer.spline_ik_remove_generated",
            text="Remove Generated",
            icon="TRASH",
        )

        _draw_status(layout, settings)


SPLINE_IK_SETUP_CLASSES = (
    CharacterDesignerSplineIKState,
    CHARACTERDESIGNER_OT_spline_ik_capture_chain,
    CHARACTERDESIGNER_OT_spline_ik_build,
    CHARACTERDESIGNER_OT_spline_ik_select_controls,
    CHARACTERDESIGNER_OT_spline_ik_enable_rotation_tilt,
    CHARACTERDESIGNER_OT_spline_ik_clear_status,
    CHARACTERDESIGNER_OT_spline_ik_remove_generated,
    CHARACTERDESIGNER_PT_spline_ik_setup,
)


__all__ = (
    "CharacterDesignerSplineIKState",
    "SPLINE_IK_SETUP_CLASSES",
)
