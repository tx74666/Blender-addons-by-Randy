"""Unity target-evaluated motion as a reversible native-bone test Action.

This is deliberately not a retargeter. Bind positions register the two copies of
the same character, then evaluated deformation matrices transfer their motion.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from .animation_retarget import _new_channelbag, _write_curve


SCHEMA = "cdesigner.animation/1"
VERSION_KEY = "character_designer_unity_animation_version"
TARGET_KEY = "character_designer_unity_animation_target"
SCENE_KEY = "character_designer_unity_animation_scene"
PREVIOUS_ACTION_KEY = "character_designer_unity_animation_previous_action"
SESSION_KEY = "character_designer_unity_animation_session"
PACKAGE_KEY = "character_designer_unity_animation_package"
ACTIVE_KEY = "character_designer_unity_animation_active"
ORIGINAL_DATA_KEY = "character_designer_unity_animation_original_data"
PREVIEW_DATA_KEY = "character_designer_unity_animation_preview_data"
ORIGINAL_REST_KEY = "character_designer_unity_animation_original_rest"


class UnityAnimationError(ValueError):
    """An invalid package or a target which cannot safely preview this motion."""


def _matrix(values, label):
    if not isinstance(values, list) or len(values) != 16:
        raise UnityAnimationError(f"{label}: expected 16 row-major matrix values.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in values):
        raise UnityAnimationError(f"{label}: matrix contains invalid numbers.")
    result = Matrix([values[i:i + 4] for i in range(0, 16, 4)])
    if any(abs(result[3][i] - (1 if i == 3 else 0)) > 1e-5 for i in range(4)):
        raise UnityAnimationError(f"{label}: matrix is not affine.")
    if abs(result.to_3x3().determinant()) < 1e-10:
        raise UnityAnimationError(f"{label}: matrix is singular.")
    return result


def load_package(filepath):
    path = Path(bpy.path.abspath(str(filepath))).resolve()
    if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise UnityAnimationError("Choose a Unity animation package smaller than 256 MB.")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise UnityAnimationError(f"Cannot read Unity animation: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise UnityAnimationError("This is not a Character Designer Unity animation package.")
    if (data.get("units") != "metres" or data.get("coordinate") != "unity-lh-y-up"
            or data.get("matrixLayout") != "row-major"):
        raise UnityAnimationError("The package must declare Unity metres and row-major matrices.")
    bones, frames = data.get("bones"), data.get("frames")
    if not isinstance(bones, list) or not 4 <= len(bones) <= 1024:
        raise UnityAnimationError("The package needs a complete target skeleton.")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 20000:
        raise UnityAnimationError("The package needs between 1 and 20,000 samples.")
    for index, bone in enumerate(bones):
        if not isinstance(bone, dict) or not isinstance(bone.get("name"), str) or not bone["name"]:
            raise UnityAnimationError(f"Bone {index} has no valid name.")
        parent = bone.get("parent")
        if type(parent) is not int or not -1 <= parent < len(bones) or parent == index:
            raise UnityAnimationError(f"Invalid parent for '{bone['name']}'.")
        _matrix(bone.get("rest"), f"{bone['name']} bind pose")
        if bone.get("restSource") not in {"bindpose", "hierarchy"}:
            raise UnityAnimationError(f"'{bone['name']}' does not identify its bind-pose source.")
    for index in range(len(bones)):
        visited, current = set(), index
        while current != -1:
            if current in visited:
                raise UnityAnimationError("The package skeleton contains a parent cycle.")
            visited.add(current)
            current = bones[current]["parent"]
    previous = -1.0
    for index, frame in enumerate(frames):
        time = frame.get("time") if isinstance(frame, dict) else None
        if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(time):
            raise UnityAnimationError(f"Sample {index} has invalid time.")
        if time < 0 or (index and time <= previous):
            raise UnityAnimationError("Sample times must increase strictly from zero.")
        if index == 0 and abs(time) > 1e-6:
            raise UnityAnimationError("The first sample must be at zero seconds.")
        previous = time
        poses = frame.get("poses")
        if not isinstance(poses, list) or len(poses) != len(bones):
            raise UnityAnimationError(f"Sample {index} does not cover every exported bone.")
        _matrix(frame.get("root"), f"Sample {index} root")
        for bone, pose in zip(bones, poses):
            _matrix(pose.get("matrix") if isinstance(pose, dict) else None,
                    f"Sample {index}, {bone['name']}")
    duration = data.get("duration")
    rate = data.get("sampleRate")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration < 0 or abs(previous - duration) > 1e-4):
        raise UnityAnimationError("Duration does not match the last sample.")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 1 <= rate <= 240:
        raise UnityAnimationError("Sample rate must be between 1 and 240 Hz.")
    data["_path"] = str(path)
    return data


def validate_target(target, names):
    if target is None or target.type != "ARMATURE":
        raise UnityAnimationError("Choose the original character Armature.")
    if target.mode == "EDIT":
        raise UnityAnimationError("Leave Armature Edit Mode before importing animation.")
    if target.library or target.data.library or not target.is_editable:
        raise UnityAnimationError("The preview needs a locally editable character Armature.")
    if target.parent or target.constraints or target.data.pose_position != "POSE":
        raise UnityAnimationError("Use an unparented character in Pose Position without object constraints.")
    world = target.matrix_world
    axes = [world.to_3x3().col[i].copy() for i in range(3)]
    lengths = [axis.length for axis in axes]
    if (min(lengths) < 1e-8 or max(lengths) - min(lengths) > max(lengths) * 1e-5
            or world.to_3x3().determinant() <= 0
            or any(abs(axes[i].dot(axes[j])) > lengths[i] * lengths[j] * 1e-5
                   for i, j in ((0, 1), (0, 2), (1, 2)))):
        raise UnityAnimationError("The character needs a positive uniform object scale without shear.")
    names = set(names)
    for name in names:
        pb = target.pose.bones[name]
        if pb.constraints:
            raise UnityAnimationError(
                f"'{name}' is driven by constraints. Unity preview currently needs native FK bones; "
                "your Body Setup was left untouched.")
        if pb.parent and pb.parent.name not in names:
            raise UnityAnimationError(f"'{name}' has an unmapped parent; the original skeleton is required.")
        if not pb.bone.use_inherit_rotation or pb.bone.inherit_scale != "FULL":
            raise UnityAnimationError(f"'{name}' uses unsupported transform inheritance.")
    ad = target.animation_data
    if ad and ad.use_tweak_mode:
        raise UnityAnimationError("Leave NLA Tweak Mode before starting a test Action.")
    prefixes = tuple(target.pose.bones[name].path_from_id() for name in names)
    if ad and any(f.data_path.startswith(prefixes) or not f.data_path.startswith("pose.bones[")
                  for f in ad.drivers):
        raise UnityAnimationError("Drivers control the body/object transforms; preview did not replace them.")
    if target.data.animation_data:
        raise UnityAnimationError("Animated Armature data is not supported by this test preview.")


@dataclass
class Mapping:
    indices: dict
    conversion: Matrix
    error_metres: float
    rest_world: dict


def _mapping(target, data, unit_scale=1.0):
    import numpy as np

    candidates = {}
    for index, bone in enumerate(data["bones"]):
        if bone["name"] in target.data.bones:
            if bone["name"] in candidates:
                raise UnityAnimationError(f"Ambiguous exported bone '{bone['name']}'.")
            candidates[bone["name"]] = index
    if "Hips" not in candidates or len(candidates) < 4:
        raise UnityAnimationError("This package does not contain this character's named body bones.")
    validate_target(target, candidates)
    for name, index in candidates.items():
        parent = data["bones"][index]["parent"]
        while parent >= 0 and data["bones"][parent]["name"] not in candidates:
            parent = data["bones"][parent]["parent"]
        exported_parent = data["bones"][parent]["name"] if parent >= 0 else None
        actual_parent = target.data.bones[name].parent
        if exported_parent != (actual_parent.name if actual_parent else None):
            raise UnityAnimationError(f"'{name}' has a different parent in the exported character.")
    rests = {name: target.matrix_world @ target.data.bones[name].matrix_local for name in candidates}
    bound = [(name, index) for name, index in candidates.items()
             if data["bones"][index]["restSource"] == "bindpose"]
    if len(bound) < 4:
        raise UnityAnimationError("The package has too few verified skin bind poses.")
    source = np.asarray([tuple(_matrix(data["bones"][i]["rest"], name).translation)
                         for name, i in bound], dtype=np.float64)
    dest = np.asarray([tuple(rests[name].translation) for name, _ in bound], dtype=np.float64)
    sc, dc = source.mean(axis=0), dest.mean(axis=0)
    x, y = source - sc, dest - dc
    if np.linalg.matrix_rank(x, tol=1e-6) < 3 or np.linalg.matrix_rank(y, tol=1e-6) < 3:
        raise UnityAnimationError("Bind positions are too planar to verify the character's 3D coordinate mapping.")
    u, singular, vt = np.linalg.svd(x.T @ y)
    rotation = u @ vt  # row-vector matrix; handedness changes are intentional
    scale = float(singular.sum() / (x * x).sum())
    if not math.isfinite(scale) or abs(scale * unit_scale - 1.0) > 0.002:
        raise UnityAnimationError("Unity and Blender character sizes differ; re-export the same character first.")
    translation = dc - sc @ rotation * scale
    affine = np.eye(4)
    affine[:3, :3] = rotation.T * scale
    affine[:3, 3] = translation
    conversion = Matrix(affine.tolist())
    error = float(np.max(np.linalg.norm(source @ rotation * scale + translation - dest, axis=1))) * unit_scale
    if error > 0.0002:
        raise UnityAnimationError(f"Bind skeletons differ by {error * 1000:.3f} mm; use the matching character export.")
    for name, index in candidates.items():
        incoming = _matrix(data["bones"][index]["rest"], name)
        delta = conversion @ incoming.translation - rests[name].translation
        if delta.length * unit_scale > 0.0002:
            raise UnityAnimationError(f"'{name}' does not match this character's original bind position.")
    return Mapping(candidates, conversion, error, rests)


def expected_world_matrices(target, package, sample_index, *, unit_scale=1.0, mapping=None):
    """Reference matrices for independent same-time tests; does not change Blender."""
    data = load_package(package) if isinstance(package, (str, Path)) else package
    mapping = mapping or _mapping(target, data, unit_scale)
    c, ci = mapping.conversion, mapping.conversion.inverted()
    frame = data["frames"][sample_index]
    return {name: c @ _matrix(frame["poses"][index]["matrix"], name)
            @ _matrix(data["bones"][index]["rest"], name).inverted() @ ci @ mapping.rest_world[name]
            for name, index in mapping.indices.items()}


def _playing(context):
    return bool(context.screen and context.screen.is_animation_playing)


def _set_playing(context, playing):
    if not context.screen or _playing(context) == bool(playing):
        return
    if playing:
        if bpy.ops.screen.animation_play.poll():
            bpy.ops.screen.animation_play()
    elif bpy.ops.screen.animation_cancel.poll():
        bpy.ops.screen.animation_cancel(restore_frame=False)


def _set_frame(scene, value):
    base = math.floor(value)
    scene.frame_set(base, subframe=value - base)


def _swap_armature_data(context, target, data, *, allow_joint_translation=False):
    """Temporarily use a data copy; the rig Object and its skin bindings stay intact."""
    active = context.view_layer.objects.active
    selected = tuple(context.selected_objects)
    mode = active.mode if active else "OBJECT"
    if mode == "EDIT":
        raise UnityAnimationError("Leave Edit Mode before changing the animation preview.")
    try:
        if active and active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in context.selected_objects:
            obj.select_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
        target.data = data
        if allow_joint_translation:
            bpy.ops.object.mode_set(mode="EDIT")
            for bone in data.edit_bones:
                bone.use_connect = False
            bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        if target.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            if context.view_layer.objects.get(obj.name) is obj:
                obj.select_set(True)
        if active and context.view_layer.objects.get(active.name) is active:
            context.view_layer.objects.active = active
            if mode != "OBJECT":
                bpy.ops.object.mode_set(mode=mode)
        else:
            context.view_layer.objects.active = None


def _discard_preview_data(action, data):
    if data is None:
        return
    if action and action.get(PREVIEW_DATA_KEY) is data:
        del action[PREVIEW_DATA_KEY]
    if data and data.users == 0:
        bpy.data.armatures.remove(data)


def _same_rest_bone(original, preview):
    """Connected is the only structural flag changed by our disposable data copy."""
    if (original.parent.name if original.parent else None) != (preview.parent.name if preview.parent else None):
        return False
    if any(getattr(original, field) != getattr(preview, field) for field in
           ("use_deform", "use_inherit_rotation", "inherit_scale", "use_local_location", "use_relative_parent")):
        return False
    if (original.head_local - preview.head_local).length > 1e-6 or (original.tail_local - preview.tail_local).length > 1e-6:
        return False
    return max(abs(original.matrix_local[i][j] - preview.matrix_local[i][j])
               for i in range(4) for j in range(4)) <= 1e-6


def _rest_state(data):
    return {bone.name: {"parent": bone.parent.name if bone.parent else None,
                       "head": list(bone.head_local), "tail": list(bone.tail_local),
                       "matrix": [float(v) for row in bone.matrix_local for v in row],
                       **{field: getattr(bone, field) for field in
                          ("use_connect", "use_deform", "use_inherit_rotation", "inherit_scale",
                           "use_local_location", "use_relative_parent")}}
            for bone in data.bones}


def _matches_rest_state(data, saved):
    current = _rest_state(data)
    if not isinstance(saved, dict) or set(saved) != set(current):
        return False
    for name, fields in current.items():
        if not isinstance(saved[name], dict) or set(saved[name]) != set(fields):
            return False
        for field, value in fields.items():
            old = saved[name][field]
            if field in {"head", "tail", "matrix"}:
                if (not isinstance(old, list) or len(old) != len(value)
                        or any(not isinstance(x, (int, float)) or not math.isfinite(x)
                               or abs(x - y) > 1e-6 for x, y in zip(old, value))):
                    return False
            elif old != value:
                return False
    return True


def _snapshot(context, target):
    ad, scene = target.animation_data, context.scene
    return {
        "had_animation_data": ad is not None,
        "had_action": bool(ad and ad.action),
        "slot": ad.action_slot.handle if ad and ad.action_slot else 0,
        "use_nla": bool(ad.use_nla) if ad else True,
        "blend": ad.action_blend_type if ad else "REPLACE",
        "influence": ad.action_influence if ad else 1.0,
        "extrapolation": ad.action_extrapolation if ad else "HOLD",
        "pose": {pb.name: {field: list(getattr(pb, field)) for field in
                 ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")}
                 | {"rotation_mode": pb.rotation_mode} for pb in target.pose.bones},
        "fps": scene.render.fps, "fps_base": scene.render.fps_base,
        "frame": scene.frame_current + scene.frame_subframe,
        "start": scene.frame_start, "end": scene.frame_end,
        "preview_start": scene.frame_preview_start, "preview_end": scene.frame_preview_end,
        "use_preview": scene.use_preview_range,
        "autokey": scene.tool_settings.use_keyframe_insert_auto,
        "playing": _playing(context),
    }


def _restore_snapshot(context, target, snapshot, previous):
    _set_playing(context, False)
    scene, ad = context.scene, target.animation_data_create()
    ad.action = previous
    if previous:
        slot = next((s for s in previous.slots if s.handle == snapshot["slot"]), None)
        if slot:
            ad.action_slot = slot
    ad.use_nla = snapshot["use_nla"]
    ad.action_blend_type = snapshot["blend"]
    ad.action_influence = snapshot["influence"]
    ad.action_extrapolation = snapshot["extrapolation"]
    for name, fields in snapshot["pose"].items():
        pb = target.pose.bones.get(name)
        if pb:
            pb.rotation_mode = fields["rotation_mode"]
            for field, values in fields.items():
                if field != "rotation_mode":
                    setattr(pb, field, values)
    scene.render.fps, scene.render.fps_base = snapshot["fps"], snapshot["fps_base"]
    scene.frame_start, scene.frame_end = snapshot["start"], snapshot["end"]
    scene.frame_preview_start, scene.frame_preview_end = snapshot["preview_start"], snapshot["preview_end"]
    scene.use_preview_range = snapshot["use_preview"]
    scene.tool_settings.use_keyframe_insert_auto = snapshot["autokey"]
    _set_frame(scene, snapshot["frame"])
    context.view_layer.update()
    if (not snapshot["had_animation_data"] and previous is None and not ad.drivers
            and not ad.nla_tracks):
        target.animation_data_clear()
    _set_playing(context, snapshot["playing"])


def active_preview(target):
    action = target.get(ACTIVE_KEY) if target else None
    return action if (isinstance(action, bpy.types.Action) and action.get(VERSION_KEY) == 1
                      and action.get(TARGET_KEY) is target) else None


@dataclass
class PreviewResult:
    action: bpy.types.Action
    first_frame: float
    last_frame: float
    sample_count: int
    mapping_error: float


def import_test_action(context, target, filepath, *, start_frame=1):
    """Create and attach a unique test Action, with persistent complete session restore."""
    if active_preview(target):
        raise UnityAnimationError("Restore the current Unity test before importing another clip.")
    if context.object and context.object.mode == "EDIT":
        raise UnityAnimationError("Leave Edit Mode before importing a test Action.")
    data = load_package(filepath)
    unit_scale = context.scene.unit_settings.scale_length
    mapping = _mapping(target, data, unit_scale)
    if not math.isfinite(start_frame):
        raise UnityAnimationError("The test start frame must be finite.")
    snapshot = _snapshot(context, target)
    previous = target.animation_data.action if target.animation_data else None
    fps = snapshot["fps"] / snapshot["fps_base"]
    frames = [start_frame + frame["time"] * fps for frame in data["frames"]]
    names = sorted(mapping.indices, key=lambda n: len(target.data.bones[n].parent_recursive))
    world_inv = target.matrix_world.inverted()
    world_scale = target.matrix_world.to_3x3().col[0].length
    channels, quaternions, eulers = {}, {}, {}
    needs_joint_translation = False
    for index in range(len(frames)):
        desired_world = expected_world_matrices(target, data, index, mapping=mapping)
        desired = {name: world_inv @ mat for name, mat in desired_world.items()}
        for name in names:
            pb, bone = target.pose.bones[name], target.data.bones[name]
            kwargs = ({"parent_matrix": desired[bone.parent.name],
                       "parent_matrix_local": bone.parent.matrix_local} if bone.parent else {})
            basis = bone.convert_local_to_pose(desired[name], bone.matrix_local, invert=True, **kwargs)
            location, rotation, scale = basis.decompose()
            if bone.use_connect and location.length * world_scale * unit_scale > 1e-7:
                needs_joint_translation = True
            reconstructed = Matrix.LocRotScale(location, rotation, scale)
            if max(abs(basis[i][j] - reconstructed[i][j]) for i in range(4) for j in range(4)) > 2e-5:
                raise UnityAnimationError(f"'{name}' needs shear at sample {index}; the original rig was left untouched.")
            if name in quaternions and rotation.dot(quaternions[name]) < 0:
                rotation.negate()
            quaternions[name] = rotation.copy()
            mode = pb.rotation_mode
            if mode == "QUATERNION":
                rotation_path, components = "rotation_quaternion", rotation
            elif mode == "AXIS_ANGLE":
                axis, angle = rotation.to_axis_angle()
                rotation_path, components = "rotation_axis_angle", (angle, *axis)
            else:
                euler = rotation.to_euler(mode, eulers[name]) if name in eulers else rotation.to_euler(mode)
                eulers[name] = euler.copy()
                rotation_path, components = "rotation_euler", euler
            prefix = pb.path_from_id()
            for prop, values in (("location", location), (rotation_path, components), ("scale", scale)):
                for component, value in enumerate(values):
                    if not math.isfinite(value):
                        raise UnityAnimationError(f"Invalid output transform on '{name}'.")
                    channels.setdefault((prefix + "." + prop, component), []).append(value)
    action, preview_data = None, None
    original_data = target.data
    try:
        clip = str(data.get("clipName") or "Animation").strip()
        action = bpy.data.actions.new("Unity Test · " + clip)
        action.use_fake_user = True
        slot, bag = _new_channelbag(action, target)
        for (path, component), values in channels.items():
            _write_curve(bag, path, component, frames, values)
        action[VERSION_KEY], action[TARGET_KEY] = 1, target
        action[SCENE_KEY] = context.scene
        if previous:
            action[PREVIOUS_ACTION_KEY] = previous
        action[SESSION_KEY] = json.dumps(snapshot, separators=(",", ":"))
        action[PACKAGE_KEY] = data["_path"]
        action["unity_clip_name"] = clip
        action["unity_sample_rate"] = data["sampleRate"]
        action["unity_start_frame"] = float(start_frame)
        action["unity_duration"] = data["duration"]
        if needs_joint_translation:
            # Unity Humanoid can animate joint translations which Blender's
            # connected native bones suppress. Only this disposable data copy
            # relaxes that editing flag; all original rest matrices stay intact.
            preview_data = original_data.copy()
            preview_data.name = original_data.name + " · Unity Preview"
            preview_data.use_fake_user = False
            action[ORIGINAL_DATA_KEY] = original_data
            action[PREVIEW_DATA_KEY] = preview_data
            action[ORIGINAL_REST_KEY] = json.dumps(_rest_state(original_data), separators=(",", ":"))
            _swap_armature_data(context, target, preview_data, allow_joint_translation=True)
        _set_playing(context, False)
        ad = target.animation_data_create()
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, "REPLACE", 1.0
        ad.action, ad.action_slot = action, slot
        target[ACTIVE_KEY] = action
        scene = context.scene
        scene.frame_start = scene.frame_preview_start = math.floor(frames[0])
        scene.frame_end = scene.frame_preview_end = max(math.ceil(frames[-1]), scene.frame_start)
        scene.use_preview_range = True
        _set_frame(scene, frames[0])
        context.view_layer.update()
    except Exception:
        if target.get(ACTIVE_KEY) is action:
            del target[ACTIVE_KEY]
        if target.data is not original_data:
            _swap_armature_data(context, target, original_data)
        _restore_snapshot(context, target, snapshot, previous)
        _discard_preview_data(action, preview_data)
        if action is not None:
            bpy.data.actions.remove(action)
        raise
    return PreviewResult(action, frames[0], frames[-1], len(frames), mapping.error_metres)


def restore_preview(context, target):
    action = active_preview(target)
    if not action:
        raise UnityAnimationError("This character has no active Unity test preview.")
    if action.get(SCENE_KEY) is not context.scene:
        raise UnityAnimationError("Return to the scene where this Unity test was started before restoring it.")
    ad = target.animation_data
    if not ad or ad.action is not action:
        raise UnityAnimationError("The current Action changed during preview; it was left untouched.")
    try:
        snapshot = json.loads(action[SESSION_KEY])
        # Validate persisted fields before making any changes.
        for key in ("pose", "slot", "use_nla", "blend", "influence", "extrapolation", "fps",
                    "fps_base", "frame", "start", "end", "preview_start", "preview_end",
                    "use_preview", "autokey", "playing", "had_animation_data", "had_action"):
            if key not in snapshot:
                raise ValueError(key)
        if (not isinstance(snapshot["pose"], dict)
                or any(name not in target.pose.bones for name in snapshot["pose"])
                or type(snapshot["fps"]) is not int or not 1 <= snapshot["fps"] <= 32767
                or not 1e-5 <= snapshot["fps_base"] <= 1e6
                or snapshot["blend"] not in {"REPLACE", "ADD", "SUBTRACT", "MULTIPLY", "COMBINE"}
                or snapshot["extrapolation"] not in {"NOTHING", "HOLD", "HOLD_FORWARD"}
                or not 0 <= snapshot["influence"] <= 1):
            raise ValueError("Invalid saved animation settings")
        for name, fields in snapshot["pose"].items():
            if fields.get("rotation_mode") not in {"QUATERNION", "AXIS_ANGLE", "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}:
                raise ValueError(name)
            for field, count in (("location", 3), ("scale", 3), ("rotation_euler", 3),
                                 ("rotation_quaternion", 4), ("rotation_axis_angle", 4)):
                values = fields.get(field)
                if (not isinstance(values, list) or len(values) != count
                        or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)):
                    raise ValueError(name + " " + field)
        for key in ("frame", "start", "end", "preview_start", "preview_end"):
            if not isinstance(snapshot[key], (int, float)) or not math.isfinite(snapshot[key]):
                raise ValueError(key)
    except (KeyError, ValueError, TypeError) as exc:
        raise UnityAnimationError("The saved preview recovery record is incomplete.") from exc
    previous = action.get(PREVIOUS_ACTION_KEY)
    if snapshot["had_action"] and previous is None:
        raise UnityAnimationError("The previous Action was deleted; preview was left unchanged.")
    if previous and previous.is_action_layered and not any(s.handle == snapshot["slot"] for s in previous.slots):
        raise UnityAnimationError("The previous Action slot was removed; preview was left unchanged.")
    current = _snapshot(context, target)
    original_data = action.get(ORIGINAL_DATA_KEY)
    preview_data = action.get(PREVIEW_DATA_KEY)
    if preview_data is not None and original_data is None:
        raise UnityAnimationError("The original Armature data was deleted; preview was left unchanged.")
    if original_data:
        if not isinstance(original_data, bpy.types.Armature) or target.data is not preview_data:
            raise UnityAnimationError("The preview Armature data was replaced; it was left untouched.")
        if set(original_data.bones.keys()) != set(target.data.bones.keys()):
            raise UnityAnimationError("Preview bones changed; recovery did not replace those edits.")
        try:
            original_state = json.loads(action.get(ORIGINAL_REST_KEY, "null"))
        except (TypeError, ValueError):
            original_state = None
        if not _matches_rest_state(original_data, original_state):
            raise UnityAnimationError("The original rest skeleton changed during preview; recovery left both versions untouched.")
        if (preview_data.animation_data is not None
                or any(not _same_rest_bone(original_data.bones[name], bone)
                       for name, bone in target.data.bones.items())):
            raise UnityAnimationError("The preview rest skeleton was edited; recovery did not discard those edits.")
    try:
        if original_data:
            _swap_armature_data(context, target, original_data)
        _restore_snapshot(context, target, snapshot, previous)
    except Exception:
        if preview_data and target.data is not preview_data:
            _swap_armature_data(context, target, preview_data)
        _restore_snapshot(context, target, current, action)
        raise
    del target[ACTIVE_KEY]
    _discard_preview_data(action, preview_data)
    return previous


def preview_time_seconds(context, target):
    action = active_preview(target)
    if not action:
        return None
    fps = context.scene.render.fps / context.scene.render.fps_base
    return (context.scene.frame_current + context.scene.frame_subframe - action["unity_start_frame"]) / fps
