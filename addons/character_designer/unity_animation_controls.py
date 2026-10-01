"""Exact Unity motion through verified Character Designer controls.

This module never maps different characters. The caller supplies native-bone
matrices from unity_animation's strict bind-skeleton mapping. Solving happens on
a disposable copy; original constraints, drivers, rest data and Actions remain
untouched. The existing Unity-preview transaction owns attachment and recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math

import bpy
from mathutils import Matrix


MATRIX_TOLERANCE = 2.0e-4
SHEAR_TOLERANCE = 2.0e-5


def _error(message):
    from .unity_animation import UnityAnimationError
    return UnityAnimationError(message)


@dataclass
class ControlPlan:
    mapped_names: frozenset
    control_names: frozenset
    constraint_keys: frozenset
    driver_keys: frozenset
    redirects: dict
    switches: dict  # (pose-bone name, custom-property name) -> preview value
    neutral: frozenset
    eyes: dict  # native eye -> (target control, aim distance)


@dataclass
class BakeResult:
    channels: dict
    needs_joint_translation: bool
    maximum_error: float
    keyed_bones: tuple
    sample_count: int


def build_plan(target, mapped_names):
    """Read-only validation of the complete *target object's* control graph.

    Returns None for an unconstrained, undriven native skeleton. Every accepted
    constraint and driver must belong to a module whose recovery record and
    current resources validate; neither a CD prefix nor a bone name grants an
    exception. The caller still validates object transforms, native inheritance,
    complete native parent relationships and exact package bind positions.
    Only control_names may be skipped while checking mapped native ancestry.
    """
    from . import limb_ik, limb_ik_fk, root_control, torso_controls
    from . import foot_controls, spine_ik_fk, eye_controls

    names = frozenset(mapped_names)
    if target is None or target.type != 'ARMATURE':
        raise _error('Choose the original character Armature.')
    if any(name not in target.pose.bones for name in names):
        raise _error('The animation mapping contains missing native bones.')
    try:
        inventory = limb_ik._validate_inventory(target)
        accepted, drivers = set(), set()
        control_names = {bone.name for bone in inventory['bones']}
        redirects, switches, neutral, eyes, sources = {}, {}, set(), {}, set()
        for pb, constraint, record in inventory['records']:
            accepted.add((pb.name, constraint.name))
            if limb_ik_fk.validate_constraint_influence(target, pb, constraint, record):
                drivers.add((constraint.path_from_id('influence'), 0))
        for rig in inventory['rigs'].values():
            control = target.pose.bones[rig['target'].name]
            if control.bone.get(limb_ik_fk.VERSION_KEY) != limb_ik_fk.VERSION:
                raise _error('This limb has no validated FK switch; update Body Setup before importing motion.')
            switches[(control.name, limb_ik_fk.PROPERTY)] = 0.0
            sources.update(rig['chain'])
        if inventory.get('master'):
            neutral.add(inventory['master'].name)

        # These explicit validators also catch orphaned extension metadata on
        # otherwise empty inventories. Their detailed checks must not be bypassed.
        root = root_control.validate(target, inventory=inventory)
        torso = torso_controls.validate(target, inventory=inventory)
        spine = spine_ik_fk.validate(target, inventory=inventory)
        eye = eye_controls.validate(target, inventory=inventory)
        feet = foot_controls.validate(target, inventory=inventory)
        for record in (root, torso, spine, eye, *feet.values()):
            if not record:
                continue
            control_names.update(record['bones'].values())
            accepted.update((entry['owner'], entry['name']) for entry in record['constraints'])
        if root:
            sources.update(root['sources'])
            neutral.add(root['master'])
            switches[(root['master'], root_control.SCALE_PROPERTY)] = 1.0
            path = target.pose.bones[root['master']].path_from_id('scale')
            drivers.update((path, index) for index in range(3))
        if torso:
            sources.update(torso['sources'])
            redirects.update(torso['controls'])
            neutral.add(torso['bend'])
        if spine:
            sources.update(spine['sources'])
            switches[(spine['chest'], spine_ik_fk.PROPERTY)] = 0.0
            redirects.update(spine['fk_controls'])
            drivers.update((entry['path'], entry['index']) for entry in spine['drivers'])
        for record in feet.values():
            sources.add(record['toe'])
            redirects[record['toe']] = record['toe_control']
            neutral.add(record['roll'])
            drivers.update((entry['path'], entry['index']) for entry in record['drivers'])
        if eye:
            distance = eye['distance']
            if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) or distance <= 0:
                raise _error('Eye Controls has an invalid aim distance.')
            sources.update(eye['sources'])
            neutral.add(eye['master'])
            eyes.update((name, (eye['targets'][side], float(distance)))
                        for name, side in zip(eye['sources'], ('L', 'R')))

        for pb in target.pose.bones:
            for constraint in pb.constraints:
                if (pb.name, constraint.name) not in accepted:
                    raise _error(f"'{pb.name}' has an unrecognized constraint '{constraint.name}'; its rig was left untouched.")
        actual_drivers = [(curve.data_path, curve.array_index)
                          for curve in target.animation_data.drivers] if target.animation_data else []
        if len(actual_drivers) != len(set(actual_drivers)) or set(actual_drivers) != drivers:
            unexpected = set(actual_drivers) - drivers
            label = next(iter(sorted(unexpected)), ('missing owned driver', 0))
            raise _error(f'Unrecognized or incomplete rig drivers: {label}; no driver was replaced.')
        if not control_names:
            return None
        if names & control_names:
            raise _error('The Unity package contains generated controls; use the matching native-skeleton export.')
        missing = sources - names
        if missing:
            raise _error('The animation package omits controlled native bones: ' + ', '.join(sorted(missing)))
        return ControlPlan(names, frozenset(control_names), frozenset(accepted), frozenset(drivers),
                           redirects, switches, frozenset(neutral), eyes)
    except limb_ik.LimbIKError as exc:
        raise _error('Character controls did not pass ownership validation: ' + str(exc)) from exc


def snapshot_properties(target, plan):
    """JSON-safe snapshot of only the custom properties this Action will key."""
    state = {}
    for name, prop in plan.switches:
        state.setdefault(name, {})[prop] = target.pose.bones[name][prop]
    validate_properties(target, state)
    return state


def validate_properties(target, state):
    """Complete preflight; never creates properties or modifies a partial record."""
    if not isinstance(state, dict):
        raise _error('Saved preview control properties are incomplete.')
    for name, values in state.items():
        pb = target.pose.bones.get(name)
        if pb is None or not isinstance(values, dict) or not values:
            raise _error('A saved preview control was removed or its recovery record is incomplete.')
        for prop, value in values.items():
            if (not isinstance(prop, str) or prop not in pb or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise _error(f"Saved preview property '{name}.{prop}' is invalid; controls were left unchanged.")


def restore_properties(target, state):
    validate_properties(target, state)
    for name, values in state.items():
        for prop, value in values.items():
            target.pose.bones[name][prop] = value
    target.update_tag(refresh={'OBJECT'})


def _update(context, target):
    target.update_tag(refresh={'OBJECT'})
    context.view_layer.update()


def _difference(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def _basis_for_native(pb, desired, plan):
    bone = pb.bone
    parent = bone.parent
    if parent is None:
        kwargs = {}
    elif parent.name in desired:
        kwargs = {'parent_matrix': desired[parent.name], 'parent_matrix_local': parent.matrix_local}
    elif parent.name in plan.control_names:
        kwargs = {'parent_matrix': pb.parent.matrix, 'parent_matrix_local': parent.matrix_local}
    else:
        raise _error(f"'{pb.name}' has an unmapped native parent.")
    return bone.convert_local_to_pose(desired[pb.name], bone.matrix_local, invert=True, **kwargs)


def _capture_channels(target, clone, plan, keyed, channels, previous_q, previous_e):
    needs_translation = False
    for name in keyed:
        pb = clone.pose.bones[name]
        location, rotation, scale = pb.matrix_basis.decompose()
        if _difference(pb.matrix_basis, Matrix.LocRotScale(location, rotation, scale)) > SHEAR_TOLERANCE:
            raise _error(f"'{name}' requires shear; the original rig was left untouched.")
        if target.data.bones[name].use_connect and location.length > 1.0e-7:
            needs_translation = True
        if name in previous_q and rotation.dot(previous_q[name]) < 0:
            rotation.negate()
        previous_q[name] = rotation.copy()
        mode = target.pose.bones[name].rotation_mode
        if mode == 'QUATERNION':
            rotation_path, components = 'rotation_quaternion', rotation
        elif mode == 'AXIS_ANGLE':
            axis, angle = rotation.to_axis_angle()
            rotation_path, components = 'rotation_axis_angle', (angle, *axis)
        else:
            euler = rotation.to_euler(mode, previous_e[name]) if name in previous_e else rotation.to_euler(mode)
            previous_e[name] = euler.copy()
            rotation_path, components = 'rotation_euler', euler
        for prop, values in (('location', location), (rotation_path, components), ('scale', scale)):
            path = target.pose.bones[name].path_from_id(prop)
            for index, value in enumerate(values):
                if (path, index) in plan.driver_keys:
                    continue  # Root scale continues to use its owned uniform-scale driver.
                if not math.isfinite(value):
                    raise _error(f"'{name}' produced a non-finite animation channel.")
                channels.setdefault((path, index), []).append(float(value))
    for (name, prop), value in plan.switches.items():
        path = target.pose.bones[name].path_from_id() + '[' + json.dumps(prop) + ']'
        channels.setdefault((path, 0), []).append(float(value))
    return needs_translation


def bake_channels(context, target, plan, desired_samples):
    """Return original-target FCurve values for exact, armature-local samples.

    desired_samples is an iterable of {native_bone_name: Matrix}. It must come
    from the caller's verified same-character mapping, never an alias/proportion
    retarget. This function creates no Action and does not change time or the
    original target. Connected translation is evaluated on a disposable data
    copy; the caller must use its existing preview-data transaction when the
    returned needs_joint_translation flag is true.
    """
    from . import unity_animation as ua

    if plan is None:
        raise _error('A verified control plan is required for control-channel baking.')
    clone, data_copy = None, None
    channels, previous_q, previous_e = {}, {}, {}
    keyed = sorted((set(plan.mapped_names) - set(plan.redirects)) | set(plan.redirects.values())
                   | set(plan.neutral) | {value[0] for value in plan.eyes.values()})
    worst, count, needs_translation = 0.0, 0, False
    try:
        clone = target.copy()
        data_copy = target.data.copy()
        data_copy.use_fake_user = False
        clone.data = data_copy
        context.scene.collection.objects.link(clone)
        clone.hide_render = True
        # Object.copy retains independent AnimData and exact driver definitions.
        # Only the copy's active Action/NLA are detached; all owned drivers remain.
        if clone.animation_data:
            clone.animation_data.action = None
            clone.animation_data.use_nla = False
            for curve in clone.animation_data.drivers:
                for variable in curve.driver.variables:
                    for binding in variable.targets:
                        if binding.id == target:
                            binding.id = clone
        for pb in clone.pose.bones:
            for constraint in pb.constraints:
                for field in ('target', 'pole_target', 'space_object'):
                    if getattr(constraint, field, None) == target:
                        setattr(constraint, field, clone)
        ua._swap_armature_data(context, clone, data_copy, allow_joint_translation=True)
        for name in plan.neutral:
            clone.pose.bones[name].matrix_basis = Matrix.Identity(4)
        for (name, prop), value in plan.switches.items():
            clone.pose.bones[name][prop] = value
        _update(context, clone)

        order = sorted(plan.mapped_names, key=lambda name: len(target.data.bones[name].parent_recursive))
        for sample_index, desired in enumerate(desired_samples):
            if set(desired) != plan.mapped_names:
                raise _error('An exact motion sample does not cover the validated native skeleton.')
            if any(not math.isfinite(value) for matrix in desired.values() for row in matrix for value in row):
                raise _error('An exact motion sample contains non-finite values.')
            for name in order:
                if name in plan.redirects:
                    # The redirected FK/toe control may have a constrained helper
                    # parent. Resolve that parent after preceding native writes.
                    _update(context, clone)
                    clone.pose.bones[plan.redirects[name]].matrix = desired[name]
                    _update(context, clone)
                else:
                    clone.pose.bones[name].matrix_basis = _basis_for_native(clone.pose.bones[name], desired, plan)
            _update(context, clone)
            for name, (control, distance) in plan.eyes.items():
                # Writing the eye's full basis retains roll. Aim along that exact
                # final Y axis so the existing Damped Track adds no extra swing.
                axis = desired[name].to_3x3().col[1]
                if axis.length < 1.0e-8:
                    raise _error(f"'{name}' has no valid eye aiming axis.")
                pb = clone.pose.bones[control]
                matrix = pb.matrix.copy()
                matrix.translation = desired[name].translation + axis.normalized() * distance
                pb.matrix = matrix
            _update(context, clone)
            for name, matrix in desired.items():
                error = _difference(clone.pose.bones[name].matrix, matrix)
                worst = max(worst, error)
                if error > MATRIX_TOLERANCE:
                    raise _error(f"'{name}' cannot reproduce exact sample {sample_index} through its controls "
                                 f'(matrix error {error:.6g}); the original rig was left untouched.')
            needs_translation |= _capture_channels(target, clone, plan, keyed, channels, previous_q, previous_e)
            count += 1
        if count == 0:
            raise _error('The animation contains no exact pose samples.')
        if any(len(values) != count for values in channels.values()):
            raise _error('Control-channel baking produced incomplete sample arrays.')
        return BakeResult(channels, needs_translation, worst, tuple(keyed), count)
    finally:
        if clone is not None:
            bpy.data.objects.remove(clone, do_unlink=True)
        if data_copy is not None and data_copy.users == 0:
            bpy.data.armatures.remove(data_copy)
