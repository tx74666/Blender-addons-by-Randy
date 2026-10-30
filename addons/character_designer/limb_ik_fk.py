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
    existing_direct_switches = (
        inventory['schema'] == _limb().LEGACY_DIRECT_PREROLL_SCHEMA
        and all(armature.data.bones[inventory['rigs'][key]['target'].name].get(VERSION_KEY) == VERSION
                for key in selected)
    )
    if selected and not existing_direct_switches and inventory["schema"] not in {
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
        # The installed-driver path is a read-only ownership check. Tag only
        # a real installation; forcing another solve here evaluates all bound
        # meshes even though none of their pose inputs have changed.
        if added:
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
    # acos(w) rounds small but meaningful rotations to zero in float32.
    return 2.0 * math.atan2(Vector((delta.x, delta.y, delta.z)).length, abs(delta.w))


def _pose_errors(armature, desired):
    position = rotation = scale = 0.0
    for name, matrix in desired.items():
        actual = armature.pose.bones[name].matrix
        if not all(_limb()._matrix_is_finite(value) for value in (actual, matrix)):
            return math.inf, math.inf, math.inf
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


def _match_pole_plane(context, armature, rig, desired, *, precise=False):
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
    near_straight = wanted.length < (end - start).length * 0.01
    pole = armature.pose.bones[rig["pole"].name]
    solved = {name: desired[name] for name in rig['chain'][:2]}
    for _iteration in range(5):
        # Near a straight limb, a tiny solver position residual makes the
        # geometric bend plane unstable. Keep an already matched rotation
        # instead of introducing visible roll to chase that residual.
        limits = (1e-6,1e-6,1e-6) if precise else (POSITION_TOLERANCE, ROTATION_TOLERANCE, SCALE_TOLERANCE)
        if all(error <= limit for error, limit in zip(_pose_errors(armature, solved),limits)):
            break
        if near_straight:
            # Use the upper bone's stable roll frame when elbow/knee position
            # has too little radial separation to define a reliable plane.
            actual_frame = armature.pose.bones[rig['chain'][0]].matrix.to_3x3()
            wanted_frame = desired[rig['chain'][0]].to_3x3()
            references = [(_limb()._project_perpendicular(actual_frame.col[i], axis),
                           _limb()._project_perpendicular(wanted_frame.col[i], axis)) for i in (0, 2)]
            actual, reference = max(references, key=lambda pair: min(pair[0].length, pair[1].length))
        else:
            current_joint = armature.pose.bones[rig["chain"][1]].matrix.translation
            actual = _limb()._project_perpendicular(current_joint - start, axis)
            reference = wanted
        if min(actual.length, reference.length) < 1.0e-8:
            break
        angle = _limb()._signed_angle(actual, reference, axis)
        if abs(angle) < 1.0e-6:
            break
        matrix = pole.matrix.copy()
        matrix.translation = start + Quaternion(axis, angle) @ (matrix.translation - start)
        _set_matrix(context, armature, pole, matrix)


def _fixed_pole_constraint(rig):
    constraints = [con for _pb, con, record in rig['entries'] if record['role'] == 'IK']
    if len(constraints) != 1 or constraints[0].type != 'IK':
        raise _error('Fixed Pole matching needs one verified native IK constraint.')
    return constraints[0]


def _leg_front_policy(inventory, rig):
    """Direct legs share one anatomical layout, independent of saved roll."""
    return (rig['pole'].get(_limb().KIND_KEY) == 'LEG'
            and inventory['schema'] in {_limb().LEGACY_DIRECT_PREROLL_SCHEMA,
                                        _limb().DIRECT_PREROLL_SCHEMA})


def _leg_front_direction(axis):
    front = _limb()._project_perpendicular(Vector((0.0, -1.0, 0.0)), axis)
    if front.length <= _limb().POLE_DIRECTION_PARALLEL_RATIO:
        raise _error('Anatomical front is parallel to this leg; keep its current controls unchanged.')
    return front.normalized()


def _leg_front_layout(armature, rig, desired):
    """Keep a healthy front layout; replace stale back/side compensation."""
    start, joint, end = (desired[name].translation for name in rig['chain'])
    axis = end - start
    total = (joint - start).length + (end - joint).length
    if axis.length <= _limb().EPSILON or total <= _limb().EPSILON:
        raise _error('Leg Pole matching needs distinct hip and ankle positions.')
    axis.normalize()
    front = _leg_front_direction(axis)
    point = armature.pose.bones[rig['pole'].name].matrix.translation.copy()
    offset = point - start
    radial = _limb()._project_perpendicular(offset, axis)
    if radial.length > _limb().EPSILON and radial.normalized().dot(front) >= 0.8:
        return point, False
    distance = radial.length if radial.length > _limb().EPSILON else total * 0.75
    point = start + axis * offset.dot(axis) + front * distance
    if any(not math.isfinite(value) for value in point):
        raise _error('The front Leg Pole layout is outside the supported coordinate range.')
    return point, True


def _position_leg_front_pole(context, armature, rig, desired):
    point, changed = _leg_front_layout(armature, rig, desired)
    if changed:
        pole = armature.pose.bones[rig['pole'].name]
        matrix = pole.matrix.copy()
        matrix.translation = point
        _set_matrix(context, armature, pole, matrix)
    return point


def _leg_pole_metadata(armature):
    limb = _limb()
    return {bone.name: {tag: list(bone[tag]) for tag in
                       (limb.POLE_DIRECTION_KEY, limb.POLE_CONFIGURED_DIRECTION_KEY) if tag in bone}
            for bone in armature.data.bones
            if bone.get(limb.OWNER_KEY) == limb.OWNER_VALUE
            and bone.get(limb.KIND_KEY) == 'LEG' and bone.get(limb.ROLE_KEY) == 'POLE'}


def _restore_leg_pole_metadata(armature, saved):
    limb = _limb()
    for name, values in saved.items():
        bone = armature.data.bones[name]
        for tag in (limb.POLE_DIRECTION_KEY, limb.POLE_CONFIGURED_DIRECTION_KEY):
            if tag in values:
                bone[tag] = values[tag]
            elif tag in bone:
                del bone[tag]


def _match_fixed_pole_angle(context, armature, rig, desired, *, precise=False):
    """Match the native pose by changing its IK angle, never the Pole layout.

    Direct legs and explicit regional matches use this instead of moving the
    animator's Pole around the limb to compensate its source-bone roll.
    """
    constraint = _fixed_pole_constraint(rig)
    pole = armature.pose.bones[rig['pole'].name]
    pole_before = pole.matrix.translation.copy()
    angle_before = float(constraint.pole_angle)
    wanted = {name: desired[name] for name in rig['chain'][:2]}
    inverse_rest = ({name: armature.data.bones[name].matrix_local.inverted() for name in wanted}
                    if precise else {})
    wanted_skin = {name: matrix @ inverse_rest[name] for name, matrix in wanted.items()} if precise else {}
    start, joint, end = (desired[name].translation for name in rig['chain'])
    length = (joint - start).length + (end - joint).length
    axis = end - start
    if length < 1e-8 or axis.length < 1e-8:
        raise _error('Fixed Pole matching needs distinct limb endpoints.')
    axis.normalize()
    minimum, maximum = _limb()._pole_angle_domain(constraint)
    best_angle, best_score, best_errors = angle_before, math.inf, (math.inf,) * 3
    best_skin_error = math.inf
    seen = []

    def evaluate(requested):
        nonlocal best_angle, best_score, best_errors, best_skin_error
        angle = math.remainder(float(requested), math.tau)
        angle = min(maximum, max(minimum, angle))
        constraint.pole_angle = angle
        actual = float(constraint.pole_angle)
        _update(context, armature)
        if (pole.matrix.translation - pole_before).length > max(length, 1.0) * 1e-6:
            raise _error('The fixed Pole moved during angle matching; preserve its input dependencies.')
        errors = _pose_errors(armature, wanted)
        if precise:
            # Short author bones amplify a small pose residual through their
            # inverse Rest. Fit the actual native deformation matrices rather
            # than accepting a rotation-only early exit that can lose skin.
            residuals = [float(actual - expected)
                         for name, expected_skin in wanted_skin.items()
                         for row, expected_row in zip(armature.pose.bones[name].matrix @ inverse_rest[name], expected_skin)
                         for actual, expected in zip(row, expected_row)]
            skin_error = max((abs(value) for value in residuals), default=0.0)
            score = (sum(value * value for value in residuals)
                     + (errors[0] / length) ** 2 + errors[1] ** 2 + errors[2] ** 2)
        else:
            skin_error = 0.0
            score = errors[0] / length + errors[1] + errors[2]
        if score < best_score:
            best_angle, best_score, best_errors, best_skin_error = actual, score, errors, skin_error
        seen.append(actual)
        return actual, score

    def matched():
        position_limit = length * 5e-7 if precise else POSITION_TOLERANCE
        rotation_limit = 1e-6 if precise else ROTATION_TOLERANCE
        scale_limit = 1e-6 if precise else SCALE_TOLERANCE
        return (best_errors[0] <= position_limit and best_errors[1] <= rotation_limit
                and best_errors[2] <= scale_limit and (not precise or best_skin_error <= 3e-6))

    try:
        evaluate(angle_before)
        if not matched():
            # A rotated bend plane normally supplies an exact correction in
            # two probes. Test both signs because the native solver's angle
            # convention depends on its source frame, including a 180° roll.
            reference = _limb()._project_perpendicular(joint - start, axis)
            current = armature.pose.bones[rig['chain'][1]].matrix.translation
            actual = _limb()._project_perpendicular(current - start, axis)
            if min(actual.length, reference.length) > length * 0.01:
                correction = _limb()._signed_angle(actual, reference, axis)
            else:
                actual_frame = armature.pose.bones[rig['chain'][0]].matrix.to_3x3()
                reference_frame = wanted[rig['chain'][0]].to_3x3()
                pairs = [(_limb()._project_perpendicular(actual_frame.col[index], axis),
                          _limb()._project_perpendicular(reference_frame.col[index], axis))
                         for index in (0, 2)]
                actual, reference = max(pairs, key=lambda pair: min(pair[0].length, pair[1].length))
                correction = (_limb()._signed_angle(actual, reference, axis)
                              if min(actual.length, reference.length) > 1e-8 else 0.0)
            for sign in (1.0, -1.0):
                evaluate(angle_before + sign * correction)
                if matched():
                    break

        if not matched():
            # Bounded fallback for unusual native roll/near-straight cases.
            # Search only the RNA angle domain; pose and surface verification
            # retains the caller's existing acceptance limits.
            step = (maximum - minimum) / 64
            for index in range(65):
                requested = minimum + index * step
                if any(abs(requested - angle) <= 1e-12 for angle in seen):
                    continue
                evaluate(requested)
            # The two RNA endpoints encode the same rotation. A minimum just
            # below +pi can otherwise get trapped in a refinement starting at
            # -pi. evaluate() wraps every candidate before the legal RNA write.
            periodic = maximum - minimum >= math.tau - 1e-6
            left = best_angle - step if periodic else max(minimum, best_angle - step)
            right = best_angle + step if periodic else min(maximum, best_angle + step)
            golden = (math.sqrt(5.0) - 1.0) * 0.5
            x1, x2 = right - golden * (right - left), left + golden * (right - left)
            a1, f1 = evaluate(x1)
            a2, f2 = evaluate(x2)
            for _iteration in range(32 if precise else 24):
                if f1 <= f2:
                    right, x2, a2, f2 = x2, x1, a1, f1
                    x1 = right - golden * (right - left)
                    a1, f1 = evaluate(x1)
                else:
                    left, x1, a1, f1 = x1, x2, a2, f2
                    x2 = left + golden * (right - left)
                    a2, f2 = evaluate(x2)
            if precise:
                # The RNA input and native solver are float32. Refinement can
                # settle on a numerical step; retain the best actually solved
                # nearby angle rather than an unevaluated analytic minimum.
                center = best_angle
                quantum = max(abs(center), 1.0) * 2.0 ** -23
                for offset in (-4, -3, -2, -1, 1, 2, 3, 4):
                    evaluate(center + offset * quantum)
        if not math.isfinite(best_score):
            raise _error('Fixed Pole matching could not evaluate a finite native pose.')
        constraint.pole_angle = best_angle
        _update(context, armature)
    except Exception:
        constraint.pole_angle = angle_before
        _update(context, armature)
        raise


def _fk_parent_dependencies(armature, parent, chain):
    """Prove that the external parent frame cannot read the matched chain.

    The caller has already run strict inventory validation. Only the optional
    Torso/Root relations from that inventory are understood here; an unrelated
    relation, even a currently muted one, keeps the sequential matcher.
    """
    from . import root_control, torso_controls

    records = (torso_controls.get_record(armature), root_control.get_record(armature))
    owned = {(entry['owner'], entry['name']): entry
             for record in records if record for entry in record['constraints']}
    dependencies, pending = {}, [parent] if parent else []
    while pending:
        pb = pending.pop()
        if pb.name in chain:
            return None
        if pb.name in dependencies:
            continue
        dependencies[pb.name] = pb
        if pb.parent:
            pending.append(pb.parent)
        for con in pb.constraints:
            entry = owned.get((pb.name, con.name))
            if (entry is None or con.type not in {'COPY_TRANSFORMS', 'COPY_ROTATION'}
                    or con.type != entry['type'] or con.target != armature
                    or con.subtarget != entry['fields'].get('subtarget')):
                return None
            target = armature.pose.bones.get(con.subtarget)
            if target is None:
                return None
            pending.append(target)

    root = records[1]
    root_scale = None
    if root and root['master'] in dependencies:
        master = dependencies[root['master']]
        root_control._validate_scale_drivers(armature, master)
        root_scale = master.path_from_id('scale')
    return dependencies, root_scale


def _native_fk_bases(armature, rig, desired):
    """Return three independently computable FK bases, or use the old path.

    Parent target matrices, rather than stale evaluated child frames, let
    Blender account for connected bones and its inheritance/local-location
    flags. This is deliberately limited to the strictly inventoried Direct
    chain; it does not batch constrained helpers or separate limbs.
    """
    chain = tuple(rig['chain'])
    if (not rig.get('direct_rest') or len(chain) != 3 or len(set(chain)) != 3
            or any(name not in desired for name in chain)
            or armature.parent is not None or armature.constraints
            or (armature.data.animation_data and armature.data.animation_data.drivers)):
        return None
    bones = [armature.pose.bones[name] for name in chain]
    if bones[1].parent != bones[0] or bones[2].parent != bones[1]:
        return None

    switch_paths = set()
    owned = {con.as_pointer(): (pb, con, record) for pb, con, record in rig['entries']}
    for pb in bones:
        for con in pb.constraints:
            entry = owned.get(con.as_pointer())
            if (entry is None or entry[0] != pb or not is_switch_constraint(armature, *entry)
                    or not validate_constraint_influence(armature, *entry)
                    or con.influence != 0.0):
                return None
            switch_paths.add(con.path_from_id('influence'))

    proof = _fk_parent_dependencies(armature, bones[0].parent, set(chain))
    if proof is None:
        return None
    dependencies, root_scale = proof
    prefixes = tuple(pb.path_from_id() for pb in (*bones, *dependencies.values()))
    animation = armature.animation_data
    for curve in animation.drivers if animation else ():
        path = curve.data_path
        if path in switch_paths or path == root_scale:
            continue
        if (any(path == prefix or path.startswith(prefix + '.') or path.startswith(prefix + '[')
                for prefix in prefixes)
                or path.startswith(('location', 'rotation_', 'scale', 'delta_', 'matrix_', 'constraints['))):
            return None

    bases = []
    for pb in bones:
        kwargs = ({'parent_matrix': desired[pb.parent.name] if pb.parent.name in chain else pb.parent.matrix.copy(),
                   'parent_matrix_local': pb.parent.bone.matrix_local}
                  if pb.parent else {})
        try:
            basis = pb.bone.convert_local_to_pose(desired[pb.name], pb.bone.matrix_local,
                                                   invert=True, **kwargs)
            if not _limb()._matrix_is_finite(basis) or basis.to_3x3().determinant() <= 1e-10:
                return None
            # RNA matrix_basis stores location/rotation/scale, not shear. Keep
            # the established sequential behavior if decomposition loses data.
            representable = Matrix.LocRotScale(*basis.decompose())
            if max(abs(basis[i][j] - representable[i][j]) for i in range(4) for j in range(4)) > 2e-6:
                return None
        except (ValueError, RuntimeError):
            return None
        bases.append((pb, basis))
    return bases


def _match_fk(context, armature, rig, desired):
    target = armature.pose.bones[rig["target"].name]
    target[PROPERTY] = 0.0
    _update(context, armature)
    bases = _native_fk_bases(armature, rig, desired)
    if bases is None:
        for name in rig["chain"]:
            _set_matrix(context, armature, armature.pose.bones[name], desired[name])
    else:
        for pb, basis in bases:
            pb.matrix_basis = basis
        _update(context, armature)
    return set(rig["chain"])


def _seed_ik(context, armature, inventory, rig, desired, solver_position, *, calibrated_rest=False):
    """Batch only independent Direct target/pole inputs; False never writes.

    Reverse-foot and Stable orientation helpers still need their established
    ordered evaluations. A supported Root parent is fixed during these writes;
    no other parent relation or controller transform driver is assumed safe.
    """
    from . import root_control

    if (inventory['schema'] not in {_limb().LEGACY_DIRECT_PREROLL_SCHEMA, _limb().DIRECT_PREROLL_SCHEMA}
            or rig.get('foot_controls') or rig['solver_target'].name != rig['target'].name
            or armature.parent is not None or armature.constraints
            or (armature.data.animation_data and armature.data.animation_data.drivers)):
        return False
    target = armature.pose.bones[rig['target'].name]
    pole = armature.pose.bones[rig['pole'].name]
    if target == pole or target.constraints or pole.constraints:
        return False
    root = root_control.get_record(armature)
    parents = {pb.parent.name: pb.parent for pb in (target, pole) if pb.parent}
    if parents and (root is None or set(parents) != {root['master']}):
        return False
    root_scale = None
    if parents:
        master = parents[root['master']]
        if master.parent is not None or master.constraints:
            return False
        root_control._validate_scale_drivers(armature, master)
        root_scale = master.path_from_id('scale')
    prefixes = tuple(pb.path_from_id() for pb in (target, pole, *parents.values()))
    animation = armature.animation_data
    for curve in animation.drivers if animation else ():
        path = curve.data_path
        if path == root_scale:
            continue
        if (any(path == prefix or path.startswith(prefix + '.') or path.startswith(prefix + '[')
                for prefix in prefixes)
                or path.startswith(('location', 'rotation_', 'scale', 'delta_', 'matrix_', 'constraints['))):
            return False

    target_matrix = target.matrix.copy()
    # Preserve the sequential seed's float32 arithmetic, including calibrated
    # almost-straight reach trials, rather than replacing the translation.
    target_matrix.translation += solver_position - target.matrix.translation
    pole_matrix = None
    if not calibrated_rest:
        pole_matrix = pole.matrix.copy()
        pole_matrix.translation = _pole_position(armature, rig, desired)
    target.matrix = target_matrix
    if pole_matrix is not None:
        pole.matrix = pole_matrix
    if not calibrated_rest or target.get(PROPERTY) != 1.0:
        target[PROPERTY] = 1.0
    _update(context, armature)
    return True


def _match_ik(context, armature, inventory, rig, desired, *, calibrated_rest=False, reach_offset=None,
              precise=False, fixed_pole=False):
    if _leg_front_policy(inventory, rig):
        _position_leg_front_pole(context, armature, rig, desired)
        fixed_pole = True
    target = armature.pose.bones[rig["target"].name]
    pole = armature.pose.bones[rig["pole"].name]
    end = armature.pose.bones[rig["chain"][2]]
    changed = {target.name, pole.name}
    desired_end = desired[end.name]
    solver_position = desired_end.translation + (reach_offset if reach_offset is not None else Vector())
    seeded = _seed_ik(context, armature, inventory, rig, desired, solver_position,
                      calibrated_rest=calibrated_rest or fixed_pole)
    if not seeded:
        _set_solver_position(context, armature, rig, solver_position)
        if not calibrated_rest and not fixed_pole:
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

    # Calibrated reach trials are already in IK. The solver-position write
    # above has just refreshed the graph; don't dirty it again for the same value.
    if not seeded and (not calibrated_rest or target.get(PROPERTY) != 1.0):
        target[PROPERTY] = 1.0
        _update(context, armature)
    if fixed_pole:
        _match_fixed_pole_angle(context, armature, rig, desired, precise=calibrated_rest or precise)
    else:
        _match_pole_plane(context, armature, rig, desired, precise=calibrated_rest or precise)
    end_constraint = next(con for _pb, con, record in rig["entries"] if record["role"] == "END_ROTATION")
    offset = rig.get("auto_offset_rotation")
    if rig.get("foot_controls") and rig['foot_controls'].get('auto_follow') == 1 and rig['auto_align']:
        from . import foot_controls
        foot_controls.match_auto_rotation(context, armature, rig, desired_end)
    elif rig.get("foot_controls"):
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
        if rig.get("auto_rotation_space", "LOCAL") == "PARENT_DELTA":
            _set_matrix(context, armature, target,
                        _limb()._auto_target_rotation_matrix(armature, target, natural, desired_end))
        else:
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
    _set_solver_position(context, armature, rig, solver_position)
    return changed


def _refine_calibrated_reach(context, armature, inventory, rig, desired, *, fixed_pole=False):
    """Fit float32 shallow-limb reach using generated controls only.

    A sub-micron target distance residual can amplify into a visible elbow
    residual on a nearly straight chain. Bound the search to a few parts per
    million of limb length; keep the best evaluated full pose, never relax the
    builder's native Rest, skin or surface checks.
    """
    fixed_pole = fixed_pole or _leg_front_policy(inventory, rig)
    start,joint,end=[desired[n].translation for n in rig['chain']]
    axis=end-start
    length=(joint-start).length+(end-joint).length
    if length<1e-8 or _limb()._project_perpendicular(joint-start,axis).length>length*.01:
        return
    axis.normalize()
    errors=_pose_errors(armature,desired)
    if errors[0]<=length*2e-6 and errors[1]<=1e-5:return
    controls=[armature.pose.bones[rig[k].name] for k in ('target','pole')]
    baseline=[p.matrix_basis.copy() for p in controls]
    best=[m.copy() for m in baseline]
    angle_constraint = _fixed_pole_constraint(rig) if fixed_pole else None
    baseline_angle = float(angle_constraint.pole_angle) if angle_constraint else None
    best_angle = baseline_angle
    def score():
        position,rotation,scale=_pose_errors(armature,desired)
        return position/length+rotation+scale
    best_score=score();best_offset=0.;bound=length*4e-6;step=bound/4
    evaluated_positions=set()
    for _level in range(3):
        center=best_offset
        for index in range(-4,5):
            offset=max(-bound,min(bound,center+step*index))
            # Levels overlap, and Blender's float32 position can round distinct
            # offsets to the same candidate. Each trial starts from the same
            # two control bases, so retain the first evaluation of that position.
            position=tuple(end+axis*offset)
            if position in evaluated_positions:continue
            evaluated_positions.add(position)
            for pb,matrix in zip(controls,baseline):pb.matrix_basis=matrix
            if angle_constraint is not None:
                angle_constraint.pole_angle = baseline_angle
            _update(context,armature)
            _match_ik(context,armature,inventory,rig,desired,calibrated_rest=True,reach_offset=axis*offset,
                      fixed_pole=fixed_pole)
            error=score()
            if error<best_score:
                best_score,best_offset=error,offset
                best=[p.matrix_basis.copy() for p in controls]
                if angle_constraint is not None:
                    best_angle = float(angle_constraint.pole_angle)
        step*=.25
    for pb,matrix in zip(controls,best):pb.matrix_basis=matrix
    if angle_constraint is not None:
        angle_constraint.pole_angle = best_angle
    _update(context,armature)


def switch_limb(context, armature, key, mode, *, keyframe=None, desired_pose=None,
                calibrated_rest=False, precise=False, sync_display=True, fixed_pole=False):
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
    leg_front = _leg_front_policy(inventory, rig)
    if type(fixed_pole) is not bool or (fixed_pole and (mode != 'IK' or inventory['schema'] not in {
            _limb().LEGACY_DIRECT_PREROLL_SCHEMA, _limb().DIRECT_PREROLL_SCHEMA})):
        raise _error('Fixed Pole matching requires an explicit Direct IK match.')
    from . import bone_collections, bone_display
    # Original.leave uses this matcher while its saved workspace is still
    # active. Its outer transaction owns the final display handoff.
    # Construction also defers display until its outer rig-layout transaction
    # has restored Body collections. Interactive callers retain strict checks.
    sync_display = sync_display and 'character_designer_body_original_mode_v1' not in armature
    target = armature.pose.bones[rig["target"].name]
    old_mode = mode_for_rig(armature, rig)
    if leg_front:
        _update(context, armature)
    layout_desired = (_matrices(armature, rig['chain']) if desired_pose is None else desired_pose)
    needs_front = leg_front and _leg_front_layout(armature, rig, layout_desired)[1]
    if old_mode == mode and desired_pose is None and not needs_front:
        display_before = bone_display._snapshot(armature)
        try:
            if sync_display:
                bone_collections.sync_limb_display(armature, inventory, keys=(key,), force_flags=True)
        except Exception:
            bone_display._restore(armature, display_before)
            bone_collections._FRAME_CACHE.pop(armature.as_pointer(), None)
            raise
        return {"mode": mode, "changed": False, "keyed": False, "errors": (0.0, 0.0, 0.0)}
    _update(context, armature)
    desired = _matrices(armature, pose_names(rig)) if desired_pose is None else {
        name: matrix.copy() for name, matrix in desired_pose.items()
    }
    if any(name not in desired or armature.pose.bones.get(name) is None for name in pose_names(rig)):
        raise _error("The saved match pose is missing a native limb or Toe bone.")
    pose_before = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in armature.pose.bones}
    mute_before = [(con, con.mute) for _pb, con, _record in rig["entries"]]
    match_fixed = fixed_pole or leg_front
    angle_before = [(con, float(con.pole_angle)) for _pb, con, record in rig['entries']
                    if match_fixed and record['role'] == 'IK']
    pole_before = (_leg_front_layout(armature, rig, desired)[0] if leg_front else
                   armature.pose.bones[rig['pole'].name].matrix.translation.copy() if fixed_pole else None)
    value_before = target.get(PROPERTY, 1.0)
    had_switching = VERSION_KEY in target.bone
    display_before = bone_display._snapshot(armature)
    try:
        installed_count = ensure_switching(armature, inventory, keys=(key,))
        if installed_count:
            _update(context, armature)
        if mode == 'FK':
            affected = _match_fk(context, armature, rig, desired)
            if leg_front:
                affected.update(_match_ik(context, armature, inventory, rig, desired,
                                         calibrated_rest=calibrated_rest, precise=precise,
                                         fixed_pole=True))
                affected.update(_match_fk(context, armature, rig, desired))
        else:
            affected = _match_ik(context, armature, inventory, rig, desired,
                                 calibrated_rest=calibrated_rest, precise=precise, fixed_pole=match_fixed)
        # Intentional Rest resync needs the same bounded precision as a
        # confirmed calibration, while still seeding its new desired Pole.
        if mode == 'IK' and (calibrated_rest or precise):
            _refine_calibrated_reach(context,armature,inventory,rig,desired,fixed_pole=match_fixed)
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
        if match_fixed and (armature.pose.bones[rig['pole'].name].matrix.translation - pole_before).length > 1e-6:
            raise _error('IK matching changed the saved Pole layout; the switch was cancelled.')
        final_inventory = _limb()._validate_inventory(armature)
        if sync_display:
            bone_collections.sync_limb_display(armature, final_inventory, keys=(key,), force_flags=True)
        keyed = bool(keyframe if keyframe is not None else context.scene.tool_settings.use_keyframe_insert_auto)
        if keyed:
            _key_switch(context, armature, target, affected, value_before, pose_before,
                        pole_angles=angle_before)
    except Exception:
        bone_display._restore(armature, display_before)
        bone_collections._FRAME_CACHE.pop(armature.as_pointer(), None)
        for con, mute in mute_before:
            con.mute = mute
        for con, angle in angle_before:
            con.pole_angle = angle
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


def repair_leg_poles(context, armature, *, desired_pose=None):
    """Repair existing Direct leg layouts atomically, without rebuilding bones.

    The default preserves the current native pose. A caller recovering a
    previously twisted pose must supply its explicitly selected native matrices;
    controller placement cannot infer a lost artist pose from source Rest.
    """
    from . import body_setup
    return _repair_leg_poles_with_transaction(context, armature, desired_pose,
                                              body_setup._atomic)


def _require_existing_switches(armature, inventory=None):
    """Read-only proof that pose matching cannot install missing IK/FK drivers."""
    inventory = inventory if inventory is not None else _limb()._validate_inventory(armature)
    for rig in inventory['rigs'].values():
        target = armature.pose.bones[rig['target'].name]
        installed = target.bone.get(VERSION_KEY)
        if type(installed) is not int or installed != VERSION:
            raise _error('Original pose return requires existing, validated IK/FK switching.')
        entries = [entry for entry in rig['entries'] if is_switch_constraint(armature, *entry)]
        if not entries or any(not validate_constraint_influence(armature, *entry) for entry in entries):
            raise _error('Original pose return cannot install or replace an IK/FK driver.')
    return inventory


def _repair_original_leg_poles(context, armature, *, desired_pose, _original_pose):
    """Join only Original's active, verified pose-return checkpoint.

    The ordinary repair still owns a complete Body transaction. This private
    route does the same repair under Original's already captured channels,
    Pole angles/metadata and Body/Dress/display rollback, without admitting
    retained Actions to any structural or Rest recovery operation.
    """
    from . import body_original_owner
    body_original_owner._check_pose_capability(_original_pose, armature, repair=True)
    _require_existing_switches(armature)

    def transaction(_context, candidate, operation):
        body_original_owner._check_pose_capability(_original_pose, candidate, repair=True)
        _require_existing_switches(candidate)
        result = operation()
        body_original_owner._check_pose_capability(_original_pose, candidate, repair=True)
        _require_existing_switches(candidate)
        return result

    result = _repair_leg_poles_with_transaction(context, armature, desired_pose, transaction)
    body_original_owner._check_pose_capability(_original_pose, armature, repair=True)
    _require_existing_switches(armature)
    return result


def _repair_leg_poles_with_transaction(context, armature, desired_pose, transaction):
    """Run the existing repair with the caller's explicit rollback boundary."""
    from . import body_setup, body_region, body_setup_removal, control_pose_assets
    inventory = _limb()._validate_inventory(armature)
    keys = sorted(key for key, data in inventory['rigs'].items()
                  if _leg_front_policy(inventory, data))
    if not keys:
        return {'repaired': [], 'changed': False}
    _update(context, armature)
    native = control_pose_assets.native_rest(armature)
    wanted = _matrices(armature, native)
    if desired_pose is not None:
        if any(name not in wanted or not _limb()._matrix_is_finite(matrix)
               for name, matrix in desired_pose.items()):
            raise _error('Leg Pole repair needs finite matrices for existing native bones only.')
        wanted.update({name: matrix.copy() for name, matrix in desired_pose.items()})
    modes = {key: mode_for_rig(armature, inventory['rigs'][key]) for key in keys}
    if any(mode not in {'IK', 'FK'} for mode in modes.values()):
        raise _error('Choose IK or FK before repairing existing leg Poles.')
    repair_keys = []
    for key in keys:
        data = inventory['rigs'][key]
        point, stale = _leg_front_layout(armature, data, wanted)
        start, _joint, end = (wanted[name].translation for name in data['chain'])
        front = _leg_front_direction(end - start)
        metadata_stale = False
        for tag in (_limb().POLE_DIRECTION_KEY, _limb().POLE_CONFIGURED_DIRECTION_KEY):
            stored = data['pole'].get(tag)
            existing = (_limb()._project_perpendicular(Vector(stored), end - start)
                        if stored is not None else Vector())
            metadata_stale |= existing.length <= _limb().EPSILON or existing.normalized().dot(front) < 0.8
        # Healthy Original -> Controls returns contain float32 bake noise.
        # Rewriting accepted controls to chase sub-tolerance differences loses
        # exact artist channels and needlessly enters the Body checkpoint.
        matrix_mismatch = max((abs(actual - expected)
                               for name in pose_names(data)
                               for actual_row, expected_row in zip(armature.pose.bones[name].matrix, wanted[name])
                               for actual, expected in zip(actual_row, expected_row)), default=0.0)
        if stale or metadata_stale or matrix_mismatch > 2e-4:
            repair_keys.append(key)
    if not repair_keys:
        return {'repaired': [], 'changed': False}

    def commit():
        excluded = set()
        for key in repair_keys:
            data = inventory['rigs'][key]
            excluded.update(body_region._native_scope(armature, data['chain']))
            excluded.update(b.name for b in inventory['bones']
                            if (b.get(_limb().KIND_KEY), b.get(_limb().SIDE_KEY)) == key)
            if data.get('foot_controls'):
                excluded.update(data['foot_controls']['bones'].values())
        protected = body_region._guard(armature, excluded)
        skin = {name: wanted[name] @ armature.data.bones[name].matrix_local.inverted()
                for name in body_setup._native_skin(armature)}
        result = {}
        for key in repair_keys:
            data = _limb()._validate_inventory(armature)['rigs'][key]
            result[key] = switch_limb(context, armature, key, modes[key], keyframe=False,
                                     desired_pose={name: wanted[name] for name in pose_names(data)},
                                     calibrated_rest=True, precise=True, sync_display=False)
        current = _limb()._validate_inventory(armature)
        for key in keys:
            data = current['rigs'][key]
            point, stale = _leg_front_layout(armature, data, wanted)
            if stale:
                raise _error('Leg Pole repair did not retain its anatomical front layout.')
            if key not in repair_keys:
                continue
            start, _joint, end = (wanted[name].translation for name in data['chain'])
            direction = _limb()._project_perpendicular(point - start, end - start).normalized()
            for tag in (_limb().POLE_DIRECTION_KEY, _limb().POLE_CONFIGURED_DIRECTION_KEY):
                stored = data['pole'].get(tag)
                existing = (_limb()._project_perpendicular(Vector(stored), end - start)
                            if stored is not None else Vector())
                if existing.length <= _limb().EPSILON or existing.normalized().dot(_leg_front_direction(end - start)) < 0.8:
                    data['pole'][tag] = list(direction)
        if control_pose_assets.native_rest(armature) != native:
            raise _error('Leg Pole repair changed native Rest; the repair was cancelled.')
        _verify(armature, wanted)
        body_setup._verify_skin(armature, skin, tolerance=body_setup_removal.SKIN_MATRIX_TOLERANCE)
        body_region._verify_guard(armature, protected, excluded)
        return {'repaired': [':'.join(key) for key in repair_keys], 'changed': True,
                'errors': {':'.join(key): value['errors'] for key, value in result.items()}}

    return transaction(context, armature, commit)


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
        # Blender's AUTO_CLAMPED right handle can realign the left even after
        # that left handle is FREE. This outgoing segment becomes CONSTANT;
        # freeze both handles before restoring its protected incoming curve.
        previous.handle_left_type = previous.handle_right_type = "FREE"
        previous.handle_left = plan["left"]
    if "previous_frame" in plan:
        original = next(point for point in curve.keyframe_points if abs(point.co.x - plan["previous_frame"]) < 1.0e-5)
        original.handle_left_type = original.handle_right_type = "FREE"
        original.handle_left = plan["previous_left"]
        original.handle_right = plan["previous_right"]
        original.interpolation = plan["previous_interpolation"]
    curve.update()


def _key_switch(context, armature, target, affected, old_value, pose_before, *, keep_previous=False,
                pole_angles=()):
    # Stage the key changes in a copy so an insertion failure cannot leave a
    # half-keyed switch. Preserve shared Actions by keeping the previous one.
    angle_inputs = [(constraint, float(previous), float(constraint.pole_angle))
                    for constraint, previous in pole_angles]
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
            if not target.keyframe_insert(data_path='["' + PROPERTY + '"]', frame=frame - 1.0, group="IK / FK"):
                raise _error("Could not key the previous IK/FK mode.")
            target[PROPERTY] = new_value
        if not target.keyframe_insert(data_path='["' + PROPERTY + '"]', frame=frame, group="IK / FK"):
            raise _error("Could not key the IK/FK mode.")
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
                            if not pb.keyframe_insert(data_path=transform_path, index=index, frame=frame - 1.0, group=pb.name):
                                raise _error(f"Could not key the previous transform of '{pb.name}'.")
                    pb.matrix_basis = current_basis
                if not pb.keyframe_insert(data_path=transform_path, frame=frame, group=pb.name):
                    raise _error(f"Could not key '{pb.name}'.")
                for curve in _limb()._fcurves_for_action(animation.action):
                    if curve.data_path == full_path:
                        if curve.array_index in plans:
                            _restore_bookend(curve, frame - 1.0, plans[curve.array_index])
                        # Native FK wrist input also contributes to Auto IK.
                        # Hold its old value until the mode cut so the matched
                        # FK rotation cannot ramp into the preceding IK pose.
                        for point in curve.keyframe_points:
                            if abs(point.co.x - (frame - 1.0)) < 1.0e-5:
                                point.interpolation = "CONSTANT"
                        curve.update()
        for curve in _limb()._fcurves_for_action(animation.action):
            if curve.data_path == path:
                if mode_plan:
                    _restore_bookend(curve, frame - 1.0, mode_plan)
                for point in curve.keyframe_points:
                    if abs(point.co.x - frame) < 1.0e-5 or abs(point.co.x - (frame - 1.0)) < 1.0e-5:
                        point.interpolation = "CONSTANT"
                curve.update()
        # A Direct leg's bend plane is compensated by the native IK angle.
        # It needs the same cut as the matched controls; otherwise a later FK
        # roll would replace the angle used by every earlier keyed IK frame.
        for constraint, previous_angle, new_angle in angle_inputs:
            angle_path = constraint.path_from_id('pole_angle')
            curves = _limb()._fcurves_for_action(animation.action)
            curve = next((curve for curve in curves if curve.data_path == angle_path), None)
            plan = _curve_bookend(curve, frame - 1.0) if curve else None
            if not plan or not plan.get('existing'):
                constraint.pole_angle = plan['value'] if plan else previous_angle
                if not constraint.keyframe_insert(data_path='pole_angle', frame=frame - 1.0, group='IK / FK'):
                    raise _error('Could not key the previous native IK Pole angle.')
            constraint.pole_angle = new_angle
            if not constraint.keyframe_insert(data_path='pole_angle', frame=frame, group='IK / FK'):
                raise _error('Could not key the matched native IK Pole angle.')
            curve = next(curve for curve in _limb()._fcurves_for_action(animation.action)
                         if curve.data_path == angle_path)
            if plan:
                _restore_bookend(curve, frame - 1.0, plan)
            for point in curve.keyframe_points:
                if abs(point.co.x - frame) < 1e-5 or abs(point.co.x - (frame - 1.0)) < 1e-5:
                    point.interpolation = 'CONSTANT'
            curve.update()
    except Exception:
        failed = animation.action
        animation.action = previous_action
        if previous_action and previous_slot:
            animation.action_slot = previous_slot
        if failed and failed is not previous_action:
            # A surrounding batch retains its previous Action until every limb
            # commits. The failed copy is transaction-owned, including an
            # inherited fake user; no real user may be discarded here.
            if keep_previous and failed.users == int(failed.use_fake_user):
                failed.use_fake_user = False
            if failed.users == 0:
                bpy.data.actions.remove(failed)
        for constraint, _previous, current in angle_inputs:
            constraint.pole_angle = current
        raise
    if not keep_previous and previous_action and previous_action.users == 0 and not previous_action.use_fake_user:
        old_name = previous_action.name
        bpy.data.actions.remove(previous_action)
        animation.action.name = old_name
