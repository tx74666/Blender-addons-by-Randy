"""Owned native IK/FK evaluation and transactional, no-pop pose matching.

The existing three source bones are the FK controls. The switch only blends
constraints which drive those bones; solver and display helpers stay enabled.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Quaternion, Vector


VERSION_KEY = "character_designer_limb_ik_fk_version"
VERSION = 1
PROPERTY = "ik_fk"
POSITION_TOLERANCE = 2.0e-4
ROTATION_TOLERANCE = 2.0e-3
SCALE_TOLERANCE = 2.0e-4


def _limb():
    from . import limb_ik
    return limb_ik


def _error(message):
    return _limb().LimbIKError(message)


def property_path(target):
    return target.path_from_id() + '["' + PROPERTY + '"]'


def is_switch_constraint(armature, pose_bone, constraint, record):
    """Whether an owned relation drives a source rather than a helper bone."""
    role = record.get("role")
    chain = record.get("chain", ())
    return (
        len(chain) == 3
        and pose_bone.name in chain
        and role in {"IK", "SOURCE_UPPER_ROTATION", "SOURCE_LOWER_ROTATION",
                     "END_ROTATION", "AUTO_OFFSET_ROTATION"}
    )


def _find_driver(armature, path):
    animation = armature.animation_data
    drivers = () if animation is None else animation.drivers
    matches = [curve for curve in drivers if curve.data_path == path]
    if len(matches) > 1:
        raise _error("IK/FK found duplicate drivers on " + path + ".")
    return matches[0] if matches else None


def validate_constraint_influence(armature, pose_bone, constraint, record):
    """Return True for a validated switch driver, False for a legacy relation.

    The inventory validator still owns all other relation validation and mute
    rules. Tampered or partly removed switch data raises an actionable error.
    """
    if not is_switch_constraint(armature, pose_bone, constraint, record):
        return False
    target = armature.pose.bones.get(record.get("target", ""))
    if target is None:
        return False  # The main inventory validator reports the missing bone.
    installed = target.bone.get(VERSION_KEY)
    path = constraint.path_from_id("influence")
    curve = _find_driver(armature, path)
    if installed is None:
        if curve is not None:
            raise _error(f"'{pose_bone.name}' has an unrecognized constraint driver.")
        return False
    if type(installed) is not int or installed != VERSION:
        raise _error(f"'{target.name}' has an unsupported IK/FK version.")
    value = target.get(PROPERTY)
    if type(value) not in {int, float} or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise _error(f"'{target.name}' has an invalid IK/FK value.")
    if _find_driver(armature, property_path(target)) is not None:
        raise _error(f"'{target.name}' IK/FK is controlled by another driver; remove that driver before matching.")
    if curve is None:
        raise _error(f"The IK/FK driver on '{pose_bone.name}' is missing.")
    driver = curve.driver
    valid = (
        curve.array_index == 0 and not curve.mute and not curve.modifiers
        and curve.extrapolation == "LINEAR" and len(curve.keyframe_points) == 2
        and all(
            tuple(point.co) == (float(index), float(index)) and point.interpolation == "LINEAR"
            for index, point in enumerate(curve.keyframe_points)
        )
        and driver.type == "SCRIPTED" and driver.expression == PROPERTY
        and len(driver.variables) == 1
    )
    if valid:
        variable = driver.variables[0]
        binding = variable.targets[0]
        valid = (
            variable.name == PROPERTY and variable.type == "SINGLE_PROP"
            and binding.id is armature and binding.data_path == property_path(target)
        )
    if not valid:
        raise _error(f"The owned IK/FK driver on '{pose_bone.name}' was edited.")
    return True


def owned_driver_paths(armature):
    result = set()
    for pb, constraint, record in _limb()._owned_constraint_records(armature):
        if validate_constraint_influence(armature, pb, constraint, record):
            result.add(constraint.path_from_id("influence"))
    return result


def mode_for_rig(armature, rig):
    target = armature.pose.bones[rig["target"].name]
    value = float(target.get(PROPERTY, 1.0))
    return "IK" if value >= 1.0 - 1.0e-6 else "FK" if value <= 1.0e-6 else "BLEND"


def _update(context, armature):
    armature.update_tag(refresh={"OBJECT"})
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()


def ensure_switching(armature, inventory=None, keys=None):
    """Install native drivers without changing a legacy rig's evaluated pose."""
    inventory = inventory if inventory is not None else _limb()._validate_inventory(armature)
    selected = set(keys) if keys is not None else set(inventory["rigs"])
    if selected and inventory["schema"] not in {
        _limb().ROLL_DECOUPLED_SCHEMA, _limb().DIRECT_PREROLL_SCHEMA,
    }:
        raise _error("Rebuild this older limb rig before adding IK/FK switching.")
    added = []
    try:
        for key in selected:
            rig = inventory["rigs"][key]
            target = armature.pose.bones[rig["target"].name]
            entries = [entry for entry in rig["entries"] if is_switch_constraint(armature, *entry)]
            if VERSION_KEY in target.bone:
                for entry in entries:
                    validate_constraint_influence(armature, *entry)
                continue
            if PROPERTY in target:
                raise _error(f"'{target.name}' already has an unrelated '{PROPERTY}' property.")
            for pb, constraint, record in entries:
                if _find_driver(armature, constraint.path_from_id("influence")) is not None:
                    raise _error(f"'{pb.name}' already has an influence driver.")
            added.append((target, []))
            target[PROPERTY] = 1.0
            target.id_properties_ui(PROPERTY).update(
                min=0.0, max=1.0, soft_min=0.0, soft_max=1.0,
                description="0 = FK, 1 = IK. Use Switch with Match to preserve the pose.",
                default=1.0,
            )
            target.bone[VERSION_KEY] = VERSION
            for pb, constraint, record in entries:
                curve = constraint.driver_add("influence")
                added[-1][1].append(constraint)
                for modifier in tuple(curve.modifiers):
                    curve.modifiers.remove(modifier)
                curve.keyframe_points.clear()
                curve.keyframe_points.add(2)
                for index, point in enumerate(curve.keyframe_points):
                    point.co = (float(index), float(index))
                    point.interpolation = "LINEAR"
                curve.extrapolation = "LINEAR"
                curve.update()
                driver = curve.driver
                driver.type = "SCRIPTED"
                driver.expression = PROPERTY
                variable = driver.variables.new()
                variable.name = PROPERTY
                variable.type = "SINGLE_PROP"
                variable.targets[0].id = armature
                variable.targets[0].data_path = property_path(target)
        armature.update_tag(refresh={"OBJECT"})
    except Exception:
        for target, constraints in reversed(added):
            for constraint in constraints:
                constraint.driver_remove("influence")
                constraint.influence = 1.0
            if PROPERTY in target:
                del target[PROPERTY]
            if VERSION_KEY in target.bone:
                del target.bone[VERSION_KEY]
        raise
    return len(added)


def remove_switching(armature, inventory=None):
    """Remove only recognized owned drivers before deleting generated bones."""
    inventory = inventory if inventory is not None else _limb()._validate_inventory(armature)
    owned_driver_paths(armature)  # Complete preflight before the first mutation.
    for rig in inventory["rigs"].values():
        target = armature.pose.bones[rig["target"].name]
        if VERSION_KEY not in target.bone:
            continue
        for entry in rig["entries"]:
            if is_switch_constraint(armature, *entry):
                entry[1].driver_remove("influence")
                entry[1].influence = 1.0
        del target[PROPERTY]
        del target.bone[VERSION_KEY]


def _matrices(armature, names):
    return {name: armature.pose.bones[name].matrix.copy() for name in names}


def _bone_name(value):
    return value if isinstance(value, str) else getattr(value, "name", "")


def pose_names(rig):
    """Native bones whose evaluated pose a limb switch must preserve."""
    names = list(rig["chain"])
    foot = rig.get("foot_controls")
    if foot:
        toe = _bone_name(foot.get("toe"))
        if not toe:
            raise _error("The Foot Controls inventory has no native Toe bone.")
        names.append(toe)
    return tuple(names)


def _match_toe(context, armature, rig, desired):
    foot = rig.get("foot_controls")
    if not foot:
        return set()
    toe = _bone_name(foot.get("toe"))
    control_name = _bone_name(foot.get("toe_control"))
    control = armature.pose.bones.get(control_name)
    if control is None or toe not in desired:
        raise _error("The Foot Controls inventory is missing its ToeBend control or saved Toe pose.")
    # ToeSpace changes its reference when the limb changes mode. Match only
    # after the native Foot is final, using the control's normal parent space.
    _set_matrix(context, armature, control, desired[toe])
    return {control.name}


def _rotation_error(actual, expected):
    delta = actual.to_quaternion().normalized().rotation_difference(expected.to_quaternion().normalized())
    return min(abs(delta.angle), abs(2.0 * math.pi - delta.angle))


def _pose_errors(armature, desired):
    position = rotation = scale = 0.0
    for name, matrix in desired.items():
        actual = armature.pose.bones[name].matrix
        position = max(position, (actual.translation - matrix.translation).length)
        rotation = max(rotation, _rotation_error(actual, matrix))
        scale = max(scale, (actual.to_scale() - matrix.to_scale()).length)
    return position, rotation, scale


def _verify(armature, desired):
    errors = _pose_errors(armature, desired)
    if any(value > limit for value, limit in zip(errors, (POSITION_TOLERANCE, ROTATION_TOLERANCE, SCALE_TOLERANCE))):
        raise _error(
            "IK/FK could not match this pose without a jump "
            f"(position {errors[0]:.4g}, rotation {errors[1]:.4g}, scale {errors[2]:.4g}). "
            "The switch was cancelled; check limb stretch, twist or additional constraints."
        )
    return errors


def _set_matrix(context, armature, pose_bone, matrix):
    pose_bone.matrix = matrix
    _update(context, armature)


def _pole_position(armature, rig, desired):
    a, b, c = (desired[name].translation for name in rig["chain"])
    axis = c - a
    if axis.length < 1.0e-8:
        raise _error("IK/FK cannot match a completely folded limb with coincident ends.")
    axis.normalize()
    radial = b - a - axis * (b - a).dot(axis)
    old_pole = armature.pose.bones[rig["pole"].name].matrix.translation
    if radial.length < 1.0e-6:
        radial = old_pole - b - axis * (old_pole - b).dot(axis)
    if radial.length < 1.0e-6:
        radial = Vector(rig["pole_direction"] or (0.0, -1.0, 0.0))
        radial -= axis * radial.dot(axis)
    if radial.length < 1.0e-6:
        raise _error("Choose a clear elbow/knee bend before matching this straight limb.")
    distance = max((old_pole - b).length, (b - a).length + (c - b).length)
    return b + radial.normalized() * distance


def _set_solver_position(context, armature, rig, desired_position):
    target = armature.pose.bones[rig["target"].name]
    solver = armature.pose.bones[rig["solver_target"].name]
    matrix = target.matrix.copy()
    matrix.translation += desired_position - solver.matrix.translation
    _set_matrix(context, armature, target, matrix)


def _match_pole_plane(context, armature, rig, desired):
    """Compensate the solver's FK/rest roll without changing its pole angle.

    Blender measures pole_angle from the current source frame in Direct rigs.
    A freshly authored FK upper rotation can therefore rotate the pole's
    effective plane even when its geometric position already looks correct.
    Rotate the animator's pole around the endpoint axis to recover that plane.
    """
    start = desired[rig["chain"][0]].translation
    joint = desired[rig["chain"][1]].translation
    end = desired[rig["chain"][2]].translation
    axis = (end - start).normalized()
    wanted = _limb()._project_perpendicular(joint - start, axis)
    if wanted.length < 1.0e-6:
        return
    pole = armature.pose.bones[rig["pole"].name]
    for _iteration in range(5):
        current_joint = armature.pose.bones[rig["chain"][1]].matrix.translation
        actual = _limb()._project_perpendicular(current_joint - start, axis)
        if actual.length < 1.0e-8:
            break
        angle = _limb()._signed_angle(actual, wanted, axis)
        if abs(angle) < 1.0e-6:
            break
        matrix = pole.matrix.copy()
        matrix.translation = start + Quaternion(axis, angle) @ (matrix.translation - start)
        _set_matrix(context, armature, pole, matrix)


def _match_fk(context, armature, rig, desired):
    target = armature.pose.bones[rig["target"].name]
    target[PROPERTY] = 0.0
    _update(context, armature)
    for name in rig["chain"]:
        _set_matrix(context, armature, armature.pose.bones[name], desired[name])
    return set(rig["chain"])


def _match_ik(context, armature, inventory, rig, desired):
    target = armature.pose.bones[rig["target"].name]
    pole = armature.pose.bones[rig["pole"].name]
    end = armature.pose.bones[rig["chain"][2]]
    changed = {target.name, pole.name}
    desired_end = desired[end.name]
    _set_solver_position(context, armature, rig, desired_end.translation)
    pole_matrix = pole.matrix.copy()
    pole_matrix.translation = _pole_position(armature, rig, desired)
    _set_matrix(context, armature, pole, pole_matrix)

    if inventory["schema"] == _limb().ROLL_DECOUPLED_SCHEMA:
        # The ORI frames carry the freely authored FK roll. Their existing
        # tracking constraints still aim toward the unchanged IK mechanism.
        for role, source in (("ori_upper", rig["chain"][0]), ("ori_lower", rig["chain"][1])):
            pb = armature.pose.bones[rig[role].name]
            _set_matrix(context, armature, pb, desired[source])
            changed.add(pb.name)

    target[PROPERTY] = 1.0
    _update(context, armature)
    _match_pole_plane(context, armature, rig, desired)
    end_constraint = next(con for _pb, con, record in rig["entries"] if record["role"] == "END_ROTATION")
    offset = rig.get("auto_offset_rotation")
    if rig.get("foot_controls"):
        # Reverse-foot owns both end-rotation paths in world space. Its fixed
        # pivots must keep the foot orientation, including in Auto display
        # mode; interpreting the solver as a local offset would double roll.
        solver = armature.pose.bones[rig["solver_target"].name]
        desired_world = armature.matrix_world @ desired_end
        solver_world = armature.matrix_world @ solver.matrix
        target_world = armature.matrix_world @ target.matrix
        delta = desired_world.to_quaternion().normalized() @ solver_world.to_quaternion().normalized().inverted()
        pivot = solver_world.translation.copy()
        transform = Matrix.Translation(pivot) @ delta.to_matrix().to_4x4() @ Matrix.Translation(-pivot)
        _set_matrix(context, armature, target, armature.matrix_world.inverted_safe() @ transform @ target_world)
    elif rig["auto_align"]:
        if offset is None:
            raise _error("Rebuild this rig to add the Auto Align rotation offset before matching IK.")
        # Keep the FK end's natural local transform and solve only the visible
        # target offset. It is usually identity after IK->FK baked that offset.
        old_mute = offset.mute
        offset.mute = True
        _update(context, armature)
        natural = end.matrix.copy()
        natural_local = armature.convert_space(pose_bone=end, matrix=natural, from_space="POSE", to_space="LOCAL")
        desired_local = armature.convert_space(pose_bone=end, matrix=desired_end, from_space="POSE", to_space="LOCAL")
        rotation = natural_local.to_quaternion().inverted() @ desired_local.to_quaternion()
        basis = target.matrix_basis.copy()
        target.matrix_basis = Matrix.LocRotScale(basis.translation, rotation.normalized(), basis.to_scale())
        offset.mute = old_mute
        _update(context, armature)
    elif end_constraint.target_space == "LOCAL_OWNER_ORIENT":
        desired_local = armature.convert_space(pose_bone=end, matrix=desired_end, from_space="POSE", to_space="LOCAL")
        basis = target.matrix_basis.copy()
        target.matrix_basis = Matrix.LocRotScale(basis.translation, desired_local.to_quaternion(), basis.to_scale())
        _update(context, armature)
        # LOCAL_OWNER_ORIENT additionally transports between the hand and
        # target's different rest axes. Correct its evaluated world residual,
        # using the same contract as the existing Auto Align handoff.
        wanted_world = armature.matrix_world @ desired_end
        for _iteration in range(8):
            current_world = armature.matrix_world @ end.matrix
            if _rotation_error(current_world, wanted_world) <= 3.0e-4:
                break
            correction = wanted_world.to_quaternion().normalized() @ current_world.to_quaternion().normalized().inverted()
            target_world = armature.matrix_world @ target.matrix
            corrected = Matrix.LocRotScale(target_world.translation, correction @ target_world.to_quaternion().normalized(), target_world.to_scale())
            _set_matrix(context, armature, target, armature.matrix_world.inverted_safe() @ corrected)
    else:
        solver = armature.pose.bones[rig["solver_target"].name]
        delta = desired_end.to_quaternion() @ solver.matrix.to_quaternion().inverted()
        pivot = solver.matrix.translation.copy()
        transform = Matrix.Translation(pivot) @ delta.to_matrix().to_4x4() @ Matrix.Translation(-pivot)
        _set_matrix(context, armature, target, transform @ target.matrix)
    _set_solver_position(context, armature, rig, desired_end.translation)
    return changed


def switch_limb(context, armature, key, mode, *, keyframe=None, desired_pose=None):
    """Switch one limb and match its evaluated pose; restore everything on error."""
    if mode not in {"IK", "FK"}:
        raise _error("Choose IK or FK.")
    if armature is None or armature.type != "ARMATURE" or armature.library or armature.data.library:
        raise _error("IK/FK needs a local editable Armature.")
    if context.mode not in {"POSE", "OBJECT"}:
        raise _error("Switch IK/FK in Pose Mode or Object Mode.")
    inventory = _limb()._validate_inventory(armature)
    if key not in inventory["rigs"]:
        raise _error("Build this limb's controls before switching IK/FK.")
    rig = inventory["rigs"][key]
    target = armature.pose.bones[rig["target"].name]
    old_mode = mode_for_rig(armature, rig)
    if old_mode == mode and desired_pose is None:
        return {"mode": mode, "changed": False, "keyed": False, "errors": (0.0, 0.0, 0.0)}
    _update(context, armature)
    desired = _matrices(armature, pose_names(rig)) if desired_pose is None else {
        name: matrix.copy() for name, matrix in desired_pose.items()
    }
    if any(name not in desired or armature.pose.bones.get(name) is None for name in pose_names(rig)):
        raise _error("The saved match pose is missing a native limb or Toe bone.")
    pose_before = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in armature.pose.bones}
    mute_before = [(con, con.mute) for _pb, con, _record in rig["entries"]]
    value_before = target.get(PROPERTY, 1.0)
    had_switching = VERSION_KEY in target.bone
    try:
        ensure_switching(armature, inventory, keys=(key,))
        _update(context, armature)
        affected = _match_fk(context, armature, rig, desired) if mode == "FK" else _match_ik(context, armature, inventory, rig, desired)
        affected.update(_match_toe(context, armature, rig, desired))
        # Removal of an optional extension restores its native Toe constraint
        # baseline first. The caller can include that Toe matrix here so it is
        # matched directly after the legacy Foot has been matched.
        if not rig.get("foot_controls"):
            extra_names = set(desired) - set(rig["chain"])
            for name in sorted(extra_names, key=lambda item: len(armature.pose.bones[item].parent_recursive)):
                _set_matrix(context, armature, armature.pose.bones[name], desired[name])
                affected.add(name)
        errors = _verify(armature, desired)
        _limb()._validate_inventory(armature)
        keyed = bool(keyframe if keyframe is not None else context.scene.tool_settings.use_keyframe_insert_auto)
        if keyed:
            _key_switch(context, armature, target, affected, value_before, pose_before)
    except Exception:
        for con, mute in mute_before:
            con.mute = mute
        target[PROPERTY] = value_before
        if not had_switching:
            for entry in rig["entries"]:
                if is_switch_constraint(armature, *entry):
                    entry[1].driver_remove("influence")
                    entry[1].influence = 1.0
            if PROPERTY in target:
                del target[PROPERTY]
            if VERSION_KEY in target.bone:
                del target.bone[VERSION_KEY]
        for name, (rotation_mode, basis) in pose_before.items():
            pb = armature.pose.bones.get(name)
            if pb is not None:
                pb.rotation_mode = rotation_mode
                pb.matrix_basis = basis
        _update(context, armature)
        raise
    return {"mode": mode, "changed": True, "keyed": keyed, "errors": errors}


def match_existing_pose(context, armature, key, desired_pose, *, keyframe=False):
    """Re-match a rebuilt attachment without changing this limb's IK/FK mode.

    Foot Controls uses this after restoring its previous native relations. The
    caller owns the attachment transaction; this service rolls back its own
    transform and key changes if the restored rig cannot reproduce the pose.
    """
    inventory = _limb()._validate_inventory(armature)
    if key not in inventory["rigs"]:
        raise _error("There is no generated limb to match after restoring the attachment.")
    mode = mode_for_rig(armature, inventory["rigs"][key])
    if mode == "BLEND":
        raise _error("Choose IK or FK before restoring the Foot Controls attachment.")
    return switch_limb(context, armature, key, mode, keyframe=keyframe, desired_pose=desired_pose)


def _curve_bookend(curve, frame):
    """Capture an exact cut through a channel before adding matching keys.

    Inserting an ordinary AUTO-handle key alters the preceding Bezier segment.
    Split its cubic instead, preserving the complete earlier curve rather than
    only its sampled value at the previous frame.
    """
    if curve.lock or curve.mute or any(not modifier.mute for modifier in curve.modifiers):
        raise _error("IK/FK cannot safely key a locked, muted or modifier-driven transform channel.")
    points = list(curve.keyframe_points)
    if not points:
        raise _error("IK/FK cannot match an empty animated transform channel.")
    value = float(curve.evaluate(frame))
    exact = next((point for point in points if abs(point.co.x - frame) < 1.0e-5), None)
    if exact is not None:
        return {"value": float(exact.co.y), "left": exact.handle_left.copy(), "existing": True}
    preceding = [point for point in points if point.co.x < frame]
    following = [point for point in points if point.co.x > frame]
    if not preceding:
        # This becomes the first key. Retain the old curve's extrapolation.
        slope = value - float(curve.evaluate(frame - 1.0))
        return {"value": value, "left": Vector((frame - 1.0, value - slope))}
    left = preceding[-1]
    result = {
        "value": value, "previous_frame": float(left.co.x),
        "previous_left": left.handle_left.copy(), "previous_right": left.handle_right.copy(),
        "previous_interpolation": left.interpolation,
    }
    if not following:
        # The old tail was extrapolated; preserve it exactly between its last
        # original key and the new previous-frame key.
        result["previous_interpolation"] = curve.extrapolation
        return result
    right = following[0]
    if left.interpolation in {"CONSTANT", "LINEAR"}:
        return result
    if left.interpolation != "BEZIER":
        raise _error("IK/FK matching keys currently support Constant, Linear or Bezier transform interpolation.")
    p0, p1 = left.co.copy(), left.handle_right.copy()
    p2, p3 = right.handle_left.copy(), right.co.copy()
    if not p0.x <= p1.x <= p2.x <= p3.x:
        raise _error("IK/FK cannot safely split crossed Bezier handles; straighten those transform handles before keying.")
    low, high = 0.0, 1.0
    for _iteration in range(48):
        t = (low + high) * 0.5
        x = (1.0-t)**3*p0.x + 3.0*(1.0-t)**2*t*p1.x + 3.0*(1.0-t)*t*t*p2.x + t**3*p3.x
        if x < frame:
            low = t
        else:
            high = t
    t = (low + high) * 0.5
    a, b, c = p0.lerp(p1, t), p1.lerp(p2, t), p2.lerp(p3, t)
    d, e = a.lerp(b, t), b.lerp(c, t)
    split = d.lerp(e, t)
    result.update({"value": float(split.y), "previous_right": a, "left": d})
    return result


def _restore_bookend(curve, frame, plan):
    previous = next((point for point in curve.keyframe_points if abs(point.co.x - frame) < 1.0e-5), None)
    if previous is None:
        raise _error("IK/FK could not preserve the animation before this switch.")
    previous.co.y = plan["value"]
    if "left" in plan:
        previous.handle_left_type = "FREE"
        previous.handle_left = plan["left"]
    if "previous_frame" in plan:
        original = next(point for point in curve.keyframe_points if abs(point.co.x - plan["previous_frame"]) < 1.0e-5)
        original.handle_left_type = original.handle_right_type = "FREE"
        original.handle_left = plan["previous_left"]
        original.handle_right = plan["previous_right"]
        original.interpolation = plan["previous_interpolation"]
    curve.update()


def _key_switch(context, armature, target, affected, old_value, pose_before):
    # Stage the key changes in a copy so an insertion failure cannot leave a
    # half-keyed switch. Preserve shared Actions by keeping the previous one.
    animation = armature.animation_data_create()
    previous_action = animation.action
    previous_slot = animation.action_slot
    if previous_action and previous_action.library:
        raise _error("Make the current Action local before keying an IK/FK switch.")
    staged = previous_action.copy() if previous_action else None
    if staged:
        animation.action = staged
        if previous_slot:
            for slot in staged.slots:
                if slot.identifier == previous_slot.identifier:
                    animation.action_slot = slot
                    break
    try:
        frame = context.scene.frame_current + context.scene.frame_subframe
        path = property_path(target)
        curves = _limb()._fcurves_for_action(animation.action) if animation.action else ()
        mode_curve = next((curve for curve in curves if curve.data_path == path), None)
        mode_plan = _curve_bookend(mode_curve, frame - 1.0) if mode_curve else None
        new_value = float(target[PROPERTY])
        if not mode_plan or not mode_plan.get("existing"):
            target[PROPERTY] = mode_plan["value"] if mode_plan else old_value
            target.keyframe_insert(data_path='["' + PROPERTY + '"]', frame=frame - 1.0, group="IK / FK")
            target[PROPERTY] = new_value
        target.keyframe_insert(data_path='["' + PROPERTY + '"]', frame=frame, group="IK / FK")
        for name in sorted(affected):
            pb = armature.pose.bones[name]
            rotation_path = "rotation_quaternion" if pb.rotation_mode == "QUATERNION" else "rotation_axis_angle" if pb.rotation_mode == "AXIS_ANGLE" else "rotation_euler"
            for transform_path in ("location", rotation_path, "scale"):
                full_path = pb.path_from_id(transform_path)
                current_curves = _limb()._fcurves_for_action(animation.action)
                plans = {
                    curve.array_index: _curve_bookend(curve, frame - 1.0)
                    for curve in current_curves if curve.data_path == full_path
                }
                existing_previous = {index for index, plan in plans.items() if plan.get("existing")}
                if len(existing_previous) < len(getattr(pb, transform_path)):
                    # A matching operation establishes the new control pose at
                    # this switch frame. Bookend its previous channels so that
                    # a later switch does not animate the earlier IK target or
                    # ORI roll baseline toward this new FK pose. Existing artist
                    # keys on the previous frame are retained per component.
                    current_basis = pb.matrix_basis.copy()
                    pb.matrix_basis = pose_before[name][1]
                    for index in range(len(getattr(pb, transform_path))):
                        if index not in existing_previous:
                            if index in plans:
                                getattr(pb, transform_path)[index] = plans[index]["value"]
                            pb.keyframe_insert(data_path=transform_path, index=index, frame=frame - 1.0, group=pb.name)
                    pb.matrix_basis = current_basis
                if not pb.keyframe_insert(data_path=transform_path, frame=frame, group=pb.name):
                    raise _error(f"Could not key '{pb.name}'.")
                for curve in _limb()._fcurves_for_action(animation.action):
                    if curve.data_path == full_path and curve.array_index in plans:
                        _restore_bookend(curve, frame - 1.0, plans[curve.array_index])
        for curve in _limb()._fcurves_for_action(animation.action):
            if curve.data_path == path:
                if mode_plan:
                    _restore_bookend(curve, frame - 1.0, mode_plan)
                for point in curve.keyframe_points:
                    if abs(point.co.x - frame) < 1.0e-5 or abs(point.co.x - (frame - 1.0)) < 1.0e-5:
                        point.interpolation = "CONSTANT"
                curve.update()
    except Exception:
        failed = animation.action
        animation.action = previous_action
        if previous_action and previous_slot:
            animation.action_slot = previous_slot
        if failed and failed is not previous_action and failed.users == 0:
            bpy.data.actions.remove(failed)
        raise
    if previous_action and previous_action.users == 0 and not previous_action.use_fake_user:
        old_name = previous_action.name
        bpy.data.actions.remove(previous_action)
        animation.action.name = old_name
