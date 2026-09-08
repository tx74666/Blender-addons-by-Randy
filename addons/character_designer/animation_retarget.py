"""Conservative SOMA BVH to body-FK Action transfer, without rig surgery.

This is an original calibrated rest-delta retargeter.  It deliberately does not solve foot
contacts, change IK constraints, or transfer secondary motion.  The default is
an unattached Action; callers can apply it and restore the previous Action.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math

import bpy
from mathutils import Matrix, Vector


PREVIOUS_ACTION_KEY = "character_designer_animation_previous_action"
PREVIOUS_SLOT_KEY = "character_designer_animation_previous_slot"
TARGET_KEY = "character_designer_animation_target"
VERSION_KEY = "character_designer_animation_retarget_version"
PREVIOUS_POSE_KEY = "character_designer_animation_previous_pose"
CALIBRATION_KEY = "character_designer_animation_calibration"

# SOMA's two neck joints collapse to one target joint.  Global orientation
# sampling includes both Neck1 and Neck2 when driving the target Neck.
SOMA_BODY_MAP = {
    "Hips": "Hips", "Spine1": "spine", "Spine2": "Chest",
    "Chest": "UpperChest", "Neck2": "Neck", "Head": "Head",
    **{
        prefix + source: target + "." + side
        for prefix, side in (("Left", "L"), ("Right", "R"))
        for source, target in (
            ("Shoulder", "shoulder"), ("Arm", "upper_arm"),
            ("ForeArm", "forearm"), ("Hand", "hand"),
            ("Leg", "thigh"), ("Shin", "shin"),
            ("Foot", "foot"), ("ToeBase", "toe"),
        )
    },
}


class RetargetError(ValueError):
    """An unsupported or ambiguous body transfer, detected before applying."""


# Blender averages BVH child locations when constructing branching bone tails.
# Anatomical child heads avoid mistaking those display tails for body axes.
_SOURCE_LANDMARKS = {
    "Hips": "Spine1", "Spine1": "Spine2", "Spine2": "Chest",
    "Chest": "Neck1", "Neck2": "Head", "Head": "HeadEnd",
    **{prefix + bone: prefix + child for prefix in ("Left", "Right") for bone, child in (
        ("Shoulder", "Arm"), ("Arm", "ForeArm"), ("ForeArm", "Hand"),
        ("Hand", "HandMiddle1"), ("Leg", "Shin"), ("Shin", "Foot"),
        ("Foot", "ToeBase"), ("ToeBase", "ToeEnd"),
    )},
}
_TARGET_LANDMARKS = {
    "Hips": "spine", "spine": "Chest", "Chest": "UpperChest",
    "UpperChest": "Neck", "Neck": "Head",
    **{bone + "." + side: child + "." + side for side in ("L", "R") for bone, child in (
        ("shoulder", "upper_arm"), ("upper_arm", "forearm"), ("forearm", "hand"),
        ("hand", "f_middle.01"), ("thigh", "shin"), ("shin", "foot"), ("foot", "toe"),
    )},
}


def _normalized_name(name):
    # Namespace stripping is useful for imported files, but collisions fail.
    return "".join(c for c in name.rsplit(":", 1)[-1].casefold() if c.isalnum())


def _find_bone(armature, name):
    exact = armature.data.bones.get(name)
    if exact is not None:
        return exact.name
    matches = [b.name for b in armature.data.bones if _normalized_name(b.name) == _normalized_name(name)]
    if len(matches) != 1:
        raise RetargetError(f"'{armature.name}' needs one unambiguous body bone named '{name}'.")
    return matches[0]


def body_mapping(source, target):
    """Resolve the 22 SOMA-to-Cosha body joints; never guess accessory bones."""
    result = {_find_bone(source, s): _find_bone(target, t) for s, t in SOMA_BODY_MAP.items()}
    if len(set(result.values())) != len(result):
        raise RetargetError("Body mapping contains duplicate target bones.")
    return result


def _curves(action, slot=None):
    if action is None:
        return []
    result = []
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                if slot is None or bag.slot_handle == slot.handle:
                    result.extend(bag.fcurves)
    if not getattr(action, "is_action_layered", False):
        result.extend(getattr(action, "fcurves", ()))
    return result


def _uniform_world(armature):
    matrix = armature.matrix_world.copy()
    if not all(math.isfinite(v) for row in matrix for v in row):
        raise RetargetError(f"'{armature.name}' has non-finite object transforms.")
    axes = [matrix.to_3x3().col[i].copy() for i in range(3)]
    scales = [axis.length for axis in axes]
    if min(scales) < 1.0e-8 or matrix.to_3x3().determinant() <= 0:
        raise RetargetError(f"'{armature.name}' needs positive, nonzero object scale.")
    if max(scales) - min(scales) > max(scales) * 1.0e-5:
        raise RetargetError(f"'{armature.name}' needs uniform object scale for body transfer.")
    if any(abs(axes[i].dot(axes[j])) > scales[i] * scales[j] * 1.0e-5
           for i, j in ((0, 1), (0, 2), (1, 2))):
        raise RetargetError(f"'{armature.name}' has unsupported object shear.")
    return matrix


def _has_live_nla(animation_data):
    return bool(animation_data and animation_data.use_nla and any(
        not track.mute and any(not strip.mute for strip in track.strips)
        for track in animation_data.nla_tracks
    ))


def _check_armature(armature, label):
    if armature is None or armature.type != "ARMATURE":
        raise RetargetError(f"Choose a {label} Armature.")
    if armature.mode == "EDIT":
        raise RetargetError("Leave Armature Edit Mode before transferring animation.")
    if armature.parent is not None or armature.constraints:
        raise RetargetError(f"'{armature.name}' has object parenting/constraints; use a plain FK Armature.")
    if armature.data.pose_position != "POSE":
        raise RetargetError(f"Set '{armature.name}' to Pose Position first.")
    if _has_live_nla(armature.animation_data):
        raise RetargetError(f"'{armature.name}' has active NLA strips; mute them before body transfer.")
    _uniform_world(armature)


def _check_target(target, names):
    _check_armature(target, "target")
    if target.library or target.data.library or not target.is_editable:
        raise RetargetError("The target Armature must be locally editable.")
    names = set(names)
    ad = target.animation_data
    if ad and (ad.action_blend_type != "REPLACE" or abs(ad.action_influence - 1.0) > 1.0e-6):
        raise RetargetError("Use Replace blending and full Action influence on the target.")
    for name in names:
        pose_bone = target.pose.bones[name]
        bone = pose_bone.bone
        if pose_bone.constraints:
            raise RetargetError(f"'{name}' has constraints. Body transfer currently supports unconstrained FK bones.")
        if bone.parent is not None and bone.parent.name not in names:
            raise RetargetError(f"'{name}' has an unmapped parent; generated IK/control rigs are not supported.")
        if not bone.use_inherit_rotation or bone.inherit_scale != "FULL":
            raise RetargetError(f"'{name}' has unsupported transform inheritance.")
        if (any(pose_bone.lock_location) or any(pose_bone.lock_rotation) or any(pose_bone.lock_scale)
                or (pose_bone.lock_rotations_4d and pose_bone.lock_rotation_w)):
            raise RetargetError(f"Unlock the transform channels on '{name}' before body transfer.")
    prefixes = tuple(target.pose.bones[name].path_from_id() for name in names)
    for fcurve in ad.drivers if ad else ():
        if fcurve.data_path.startswith(prefixes) or not fcurve.data_path.startswith("pose.bones["):
            raise RetargetError("The target body/object has drivers; body transfer will not override them.")
    if target.data.animation_data and target.data.animation_data.drivers:
        raise RetargetError("The target Armature data has drivers; body transfer is unsupported.")


def _set_frame(scene, frame):
    base = math.floor(frame)
    scene.frame_set(base, subframe=frame - base)


def _leg_length(armature, hip, knee, ankle, world):
    points = [world @ armature.data.bones[name].head_local for name in (hip, knee, ankle)]
    return (points[1] - points[0]).length + (points[2] - points[1]).length


def _motion_scale(source, target, mapping, source_world, target_world):
    reverse = {t: s for s, t in mapping.items()}
    ratios = []
    for side in ("L", "R"):
        target_names = tuple(_find_bone(target, f"{part}.{side}") for part in ("thigh", "shin", "foot"))
        source_length = _leg_length(source, *(reverse[n] for n in target_names), source_world)
        target_length = _leg_length(target, *target_names, target_world)
        if min(source_length, target_length) <= 1.0e-6:
            raise RetargetError("Body leg lengths must be nonzero to scale root motion.")
        ratios.append(target_length / source_length)
    return sum(ratios) / len(ratios)


def _landmark_direction(armature, name, child_name, world):
    bone = armature.data.bones[name]
    endpoint = bone.tail_local
    if child_name:
        candidates = [b for b in armature.data.bones if _normalized_name(b.name) == _normalized_name(child_name)]
        if len(candidates) > 1:
            raise RetargetError(f"'{armature.name}' has ambiguous calibration landmark '{child_name}'.")
        if candidates and (candidates[0].head_local - bone.head_local).length > 1.0e-6:
            endpoint = candidates[0].head_local
    direction = world.to_3x3() @ (endpoint - bone.head_local)
    if direction.length <= 1.0e-6:
        raise RetargetError(f"'{name}' needs a nonzero anatomical direction for rest-pose calibration.")
    return direction.normalized()


def _calibrated_rest_rotations(source, target, mapping, source_world, target_world):
    """Swing target anatomical axes to source neutral axes, retaining bone roll.

    Applying a T-pose delta directly to an A-pose rest pose lowers the arms
    twice. This shortest-arc swing removes that neutral-pose difference. Child
    landmarks handle Hips, Chest and Hand without using BVH display-tail averages.
    """
    rotations, angles = {}, {}
    for canonical_source, canonical_target in SOMA_BODY_MAP.items():
        source_name = _find_bone(source, canonical_source)
        target_name = mapping[source_name]
        source_direction = _landmark_direction(source, source_name, _SOURCE_LANDMARKS.get(canonical_source), source_world)
        target_direction = _landmark_direction(target, target_name, _TARGET_LANDMARKS.get(canonical_target), target_world)
        swing = target_direction.rotation_difference(source_direction)
        rotations[target_name] = swing @ target_world.to_quaternion() @ target.data.bones[target_name].matrix_local.to_quaternion()
        angles[target_name] = math.degrees(swing.angle)
    return rotations, angles


def _new_channelbag(action, target):
    slot = action.slots.new(id_type="OBJECT", name=target.name)
    layer = action.layers.new("Body Motion")
    strip = layer.strips.new(type="KEYFRAME")
    return slot, strip.channelbags.new(slot)


def _write_curve(bag, path, index, frames, values):
    curve = bag.fcurves.new(data_path=path, index=index)
    curve.keyframe_points.add(len(frames))
    curve.keyframe_points.foreach_set("co", [value for pair in zip(frames, values) for value in pair])
    for point in curve.keyframe_points:
        point.interpolation = "LINEAR"
    curve.update()


def _pose_snapshot(target, names):
    return {name: {field: list(getattr(target.pose.bones[name], field))
                   for field in ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")}
            for name in names}


def _restore_pose(target, snapshot):
    for name, fields in snapshot.items():
        bone = target.pose.bones.get(name)
        if bone is not None:
            for field, values in fields.items():
                setattr(bone, field, values)


@dataclass
class RetargetResult:
    action: bpy.types.Action
    target: bpy.types.Object
    previous_action: bpy.types.Action | None
    previous_slot_handle: int
    mapping: dict
    motion_scale: float
    frame_start: float
    frame_end: float
    rotation_modes: dict
    calibration_angles: dict

    def apply(self, context):
        """Bind this preview only if the target still has its original Action."""
        _check_target(self.target, self.mapping.values())
        ad = self.target.animation_data
        if (ad.action if ad else None) is not self.previous_action:
            raise RetargetError("The target Action changed after preview. Create a new preview first.")
        if any(self.target.pose.bones[n].rotation_mode != mode for n, mode in self.rotation_modes.items()):
            raise RetargetError("Target rotation modes changed after preview. Create a new preview first.")
        ad = self.target.animation_data_create()
        ad.action = self.action
        ad.action_slot = self.action.slots[0]
        context.view_layer.update()
        return self.action

    def restore(self, context):
        """Restore the previous Action without deleting either Action."""
        return restore_previous_action(context, self.target, self.action)


def restore_previous_action(context, target, action=None):
    """Restore a generated Action's persistent previous-Action reference."""
    ad = target.animation_data
    action = action or (ad.action if ad else None)
    if action is None or action.get(VERSION_KEY) != 1 or action.get(TARGET_KEY) is not target:
        raise RetargetError("Choose a body Action made for this target by Character Designer.")
    if ad is None or ad.action is not action:
        raise RetargetError("The target is no longer using this body Action; its current Action was left untouched.")
    previous = action.get(PREVIOUS_ACTION_KEY)
    try:
        pose = json.loads(action.get(PREVIOUS_POSE_KEY, "{}"))
    except (ValueError, TypeError) as exc:
        raise RetargetError("This body Action's saved original pose is invalid.") from exc
    ad.action = previous
    if previous is not None:
        handle = action.get(PREVIOUS_SLOT_KEY, 0)
        slot = next((slot for slot in previous.slots if slot.handle == handle), None)
        if slot is not None:
            ad.action_slot = slot
    _restore_pose(target, pose)
    _set_frame(context.scene, context.scene.frame_current + context.scene.frame_subframe)
    context.view_layer.update()
    return previous


def retarget_action(context, source, target, start_frame=1, action_name="Kimodo Body", *, assign=False):
    """Bake the source's active Action to a new, uniquely named body Action.

    Source frames are sampled at one-frame intervals; import the BVH at the
    intended scene FPS first.  Anatomical-axis calibration compensates for
    different source/target neutral poses before applying global rest-space
    rotation deltas, retaining the target bone rolls. Root movement is relative
    to the first source sample, scaled by world-space leg lengths, and anchored
    to the target's current Hips position.  Uniform object scale is supported.

    No target transforms/rotation modes, constraints, scene ranges or existing
    Actions are edited.  Fingers, hair and clothing are not keyed.  This initial
    body transfer is FK-only and does not preserve source foot contacts across
    characters with different proportions.
    """
    _check_armature(source, "source")
    _check_armature(target, "target")
    if source is target or source.data is target.data:
        raise RetargetError("Source and target must be separate Armatures.")
    mapping = body_mapping(source, target)
    _check_target(target, mapping.values())
    source_ad = source.animation_data
    if source_ad is None or source_ad.action is None:
        raise RetargetError("The source Armature needs an active imported BVH Action.")
    if source_ad.action.is_action_layered and source_ad.action_slot is None:
        raise RetargetError("The source's imported Action needs an active Action slot.")
    if source_ad.drivers or source.data.animation_data:
        raise RetargetError("Use an imported BVH source without drivers or animated Armature data.")
    if any(pb.constraints for pb in source.pose.bones):
        raise RetargetError("Use an imported BVH source without pose constraints.")
    if source_ad.action_blend_type != "REPLACE" or abs(source_ad.action_influence - 1.0) > 1.0e-6:
        raise RetargetError("The source Action needs Replace blending and full influence.")
    curves = _curves(source_ad.action, source_ad.action_slot)
    keyed_frames = [float(p.co.x) for curve in curves for p in curve.keyframe_points]
    if not keyed_frames:
        raise RetargetError("The source's active Action slot has no keyframes.")
    first, last = min(keyed_frames), max(keyed_frames)
    if not all(math.isfinite(f) for f in (first, last, float(start_frame))):
        raise RetargetError("Animation frames must be finite.")
    count = math.ceil(last - first) + 1
    if count > 10000:
        raise RetargetError("Body transfer is limited to 10,000 frames per Action.")
    source_frames = [min(first + index, last) for index in range(count)]
    output_frames = [float(start_frame) + frame - first for frame in source_frames]
    source_world = _uniform_world(source)
    target_world = _uniform_world(target)
    ratio = _motion_scale(source, target, mapping, source_world, target_world)
    source_q = source_world.to_quaternion()
    target_q = target_world.to_quaternion()
    inv_target_q = target_q.inverted()
    root_name = _find_bone(target, "Hips")
    if target.data.bones[root_name].parent is not None:
        raise RetargetError("The target Hips must be the body root.")
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    initial_root = target_world @ target.evaluated_get(depsgraph).pose.bones[root_name].matrix.translation
    target_names = sorted(mapping.values(), key=lambda n: len(target.data.bones[n].parent_recursive))
    reverse = {t: s for s, t in mapping.items()}
    rest_source_q = {s: source_q @ source.data.bones[s].matrix_local.to_quaternion() for s in mapping}
    calibrated_target_q, calibration_angles = _calibrated_rest_rotations(source, target, mapping, source_world, target_world)
    rotation_modes = {n: target.pose.bones[n].rotation_mode for n in target_names}
    pose_before = _pose_snapshot(target, target_names)
    channels = {}
    previous_quats, previous_eulers = {}, {}
    frame_before = context.scene.frame_current + context.scene.frame_subframe
    action = None
    try:
        source_root_start = None
        for frame in source_frames:
            _set_frame(context.scene, frame)
            evaluated = source.evaluated_get(context.evaluated_depsgraph_get())
            world = _uniform_world(evaluated)
            world_q = world.to_quaternion()
            source_root = world @ evaluated.pose.bones[reverse[root_name]].matrix.translation
            if source_root_start is None:
                source_root_start = source_root.copy()
            root_position = target_world.inverted() @ (initial_root + (source_root - source_root_start) * ratio)
            pose_matrices = {}
            for name in target_names:
                bone = target.data.bones[name]
                source_name = reverse[name]
                posed_source_q = world_q @ evaluated.pose.bones[source_name].matrix.to_quaternion()
                delta = posed_source_q @ rest_source_q[source_name].inverted()
                desired_q = inv_target_q @ delta @ calibrated_target_q[name]
                kwargs = {}
                if bone.parent is not None:
                    kwargs = {"parent_matrix": pose_matrices[bone.parent.name], "parent_matrix_local": bone.parent.matrix_local}
                neutral = bone.convert_local_to_pose(Matrix.Identity(4), bone.matrix_local, **kwargs)
                position = root_position if name == root_name else neutral.translation
                desired = Matrix.LocRotScale(position, desired_q, Vector((1, 1, 1)))
                basis = bone.convert_local_to_pose(desired, bone.matrix_local, invert=True, **kwargs)
                location, rotation, scale = basis.decompose()
                if name != root_name:
                    location = Vector((0, 0, 0))
                scale = Vector((1, 1, 1))
                if name in previous_quats and rotation.dot(previous_quats[name]) < 0:
                    rotation.negate()
                previous_quats[name] = rotation.copy()
                mode = rotation_modes[name]
                if mode == "QUATERNION":
                    rotation_path, components = "rotation_quaternion", tuple(rotation)
                elif mode == "AXIS_ANGLE":
                    axis, angle = rotation.to_axis_angle()
                    rotation_path, components = "rotation_axis_angle", (angle, *axis)
                else:
                    euler = rotation.to_euler(mode, previous_eulers[name]) if name in previous_eulers else rotation.to_euler(mode)
                    previous_eulers[name] = euler.copy()
                    rotation_path, components = "rotation_euler", tuple(euler)
                prefix = target.pose.bones[name].path_from_id()
                for property_name, values in (("location", location), (rotation_path, components), ("scale", scale)):
                    for index, value in enumerate(values):
                        if not math.isfinite(value):
                            raise RetargetError(f"Non-finite motion on '{name}' at source frame {frame}.")
                        channels.setdefault((prefix + "." + property_name, index), []).append(value)
                local = Matrix.LocRotScale(location, rotation, scale)
                pose_matrices[name] = bone.convert_local_to_pose(local, bone.matrix_local, **kwargs)
        previous_ad = target.animation_data
        previous_action = previous_ad.action if previous_ad else None
        previous_slot = previous_ad.action_slot if previous_ad else None
        action = bpy.data.actions.new(action_name.strip() or "Kimodo Body")
        action.use_fake_user = True
        _slot, bag = _new_channelbag(action, target)
        for (path, index), values in channels.items():
            _write_curve(bag, path, index, output_frames, values)
        action[VERSION_KEY] = 1
        action[TARGET_KEY] = target
        if previous_action is not None:
            action[PREVIOUS_ACTION_KEY] = previous_action
        action[PREVIOUS_SLOT_KEY] = previous_slot.handle if previous_slot else 0
        action[PREVIOUS_POSE_KEY] = json.dumps(pose_before, separators=(",", ":"))
        action[CALIBRATION_KEY] = json.dumps({"method": "anatomical_landmark_swing_v1", "angles_degrees": calibration_angles}, separators=(",", ":"))
        result = RetargetResult(action, target, previous_action, previous_slot.handle if previous_slot else 0,
                                mapping, ratio, output_frames[0], output_frames[-1], rotation_modes, calibration_angles)
    except Exception:
        if action is not None:
            # Only our unfinished newly created Action can be discarded.
            bpy.data.actions.remove(action)
        raise
    finally:
        _set_frame(context.scene, frame_before)
    if assign:
        result.apply(context)
    return result
