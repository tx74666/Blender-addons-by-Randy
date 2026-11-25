"""Match selected foot and toe rotations to the evaluated Original Bones' Edit axes."""
import math

from mathutils import Matrix, Quaternion, Vector

from . import foot_controls as feet, limb_ik as limb, limb_ik_fk as match
from . import limb_ik_wrist_keying as keying, limb_ik_wrist_owner_guard as owners
from . import limb_ik_wrist_rotation as rotation


def _role(rig, pb):
    bone = pb.bone
    if limb._owned(bone, rig.data.get(limb.ARMATURE_ID_KEY), role='FOOT_IK'):
        return 'target', bone.get(limb.SIDE_KEY)
    if bone.get(feet.OWNER_KEY) == feet.OWNER_VALUE:
        role = {'TOE_BEND': 'toe_control', 'FOOT_ROLL': 'roll'}.get(bone.get(feet.ROLE_KEY))
        if role:
            return role, bone.get(feet.SIDE_KEY)
    return None


def selected_feet(context):
    if context.mode != 'POSE':
        return ()
    rig = context.object
    if rig is None or rig.type != 'ARMATURE' or not rig.data.get(feet.RECORD_KEY):
        return ()
    return tuple(p for p in context.selected_pose_bones or () if p.id_data == rig and _role(rig, p))


def _turn(desired, actual):
    delta = desired @ actual.conjugated()
    if delta.w < 0.:
        delta.negate()
    axis = Vector((delta.x, delta.y, delta.z))
    length = axis.length
    return axis * (2. * math.atan2(length, delta.w) / length) if length else axis


def _orientation(pb):
    return pb.matrix.to_quaternion().normalized()


def _match_rotation(context, plan):
    rig, target, end = plan['rig'], plan['target'], plan['end']
    path = plan['rotation_path']
    desired = end.bone.matrix_local.to_quaternion().normalized()
    # The calibrated Stable graph often maps exact local zero to Bind.
    # The direct ankle can also inherit the native shin's animated twist.
    setattr(target, path, tuple(target.bl_rna.properties[path].default_array))
    match._update(context, rig)
    step = .003
    for _ in range(9):
        actual = _orientation(end)
        error = _turn(desired, actual)
        if error.length < 2e-6:
            return
        base = target.matrix_basis.to_quaternion().normalized()
        columns = []
        for axis in ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)):
            rotation._assign_rotation(target, base @ Quaternion(axis, step))
            match._update(context, rig)
            columns.append(_turn(_orientation(end), actual) / step)
        # Input axes are local to the public control. Measured columns are
        # armature-space turns of the actual deformer after its constraints.
        jacobian = Matrix(columns).transposed()
        try:
            delta = jacobian.inverted() @ error
        except ValueError as exc:
            raise ValueError(f'{target.name} cannot rotate the foot in its current IK mode.') from exc
        if not math.isfinite(delta.length) or delta.length == 0.:
            break
        rotation._assign_rotation(target, base @ Quaternion(delta.normalized(), min(delta.length, math.pi / 2)))
        match._update(context, rig)
    if _turn(desired, _orientation(end)).length >= 2e-6:
        raise ValueError(f'{target.name} could not match the Edit orientation with rotation alone.')


def _plans(context, rig, controls, records):
    requested = set()
    for p in controls:
        role = _role(rig, p)
        if not role or p.id_data != rig or records.get(role[1], {}).get(role[0]) != p.name:
            raise ValueError('Select generated foot controls on the current armature.')
        requested.add(role)
        if rig.pose.use_mirror_x:
            requested.add((role[0], 'R' if role[1] == 'L' else 'L'))
    owner = owners.prepare(context, rig)
    shared = {}
    plans = []
    for field in ('roll', 'target', 'toe_control'):
        for side in ('L', 'R'):
            if (field, side) not in requested:
                continue
            record = records[side]
            target = rig.pose.bones[record[field]]
            if _role(rig, target) != (field, side):
                raise ValueError('The generated foot control owner changed.')
            if field == 'target' and target.get(match.PROPERTY, 1.) <= 1e-6:
                raise ValueError('This foot is controlled by FK.')
            path = rotation._rotation_path(target.rotation_mode)
            locks_w = path != 'rotation_euler' and target.lock_rotations_4d and target.lock_rotation_w
            # reset_rotation has just validated every registered Foot constraint.
            # The heel-lift stop permits an exact local zero on its single input.
            hinge = field == 'roll' and record.get('roll_hinge') == 1
            if (field == 'roll' and path != 'rotation_euler'
                    or field != 'roll' and (any(target.lock_rotation) or locks_w)
                    or target.constraints and not hinge):
                raise ValueError(f'{target.name} has locked or constrained rotation.')
            if any(c.data_path == target.path_from_id(path) for c in getattr(rig.animation_data, 'drivers', ())):
                raise ValueError(f'{target.name} has driven rotation.')
            end = None if field == 'roll' else rig.pose.bones[record['chain'][-1] if field == 'target' else record['toe']]
            raw = tuple(getattr(target, path))
            plan = dict(rig=rig, target=target, end=end, rotation_path=path, raw=raw,
                        rotation_size=len(raw), action=owner['action'], slot=owner['slot'],
                        variant=owner, autokey=owner['autokey'], transaction=shared, frame=owner['frame'])
            if hinge:
                plan['key_indices'] = (0,)
            keying.prepare_keying(context, plan)
            plans.append(plan)
    return owner, plans


def reset_rotation(context, controls):
    """Reset the supplied generated foot controls in a single undoable caller."""
    from . import natural_bind_mode
    controls = tuple(controls)
    rig = context.object
    if (not controls or context.mode != 'POSE' or any(p not in (context.selected_pose_bones or ()) for p in controls)
            or any(obj != rig for obj in context.objects_in_mode)):
        raise ValueError('Choose foot controls on one posing armature.')
    feet.validate(rig, limb_pose_only=True)
    owner, plans = _plans(context, rig, controls, feet.records(rig))
    try:
        for plan in plans:
            if plan['end'] is None:
                target = plan['target']
                for index, locked in enumerate(target.lock_rotation):
                    if not locked:
                        target.rotation_euler[index] = 0.
                match._update(context, rig)
            else:
                _match_rotation(context, plan)
        owners.validate(context, owner)
        for plan in plans:
            keying.key_rotation(context, plan)
        for plan in plans:
            if plan['end'] and _turn(plan['end'].bone.matrix_local.to_quaternion().normalized(), _orientation(plan['end'])).length > 2e-6:
                raise ValueError(f"{plan['target'].name} lost the Edit orientation after evaluation.")
        names = tuple(plan['target'].name for plan in plans)
        if owner['autokey']:
            natural_bind_mode.note_rotation_keys(context, rig, names)
        for plan in plans:
            if plan.get('key_commit'):
                plan['key_commit']()
        return {'keyed': owner['autokey'], 'targets': names}
    except Exception:
        try:
            for plan in reversed(plans):
                if plan.get('key_restore'):
                    plan['key_restore']()
            for plan in plans:
                if plan.get('key_verify'):
                    plan['key_verify']()
        finally:
            for plan in plans:
                setattr(plan['target'], plan['rotation_path'], plan['raw'])
            match._update(context, rig)
        raise
