"""Clear a selected combination of IK end controls and poles in one rotation operation."""
from . import controller_transform_clear as native, foot_controls, limb_ik
from . import limb_ik_foot_rotation as foot, limb_ik_wrist_owner_guard as owners
from . import limb_ik_wrist_rotation as wrist, natural_bind_mode


def supports(context, wrists, feet):
    if not feet or context.mode != 'POSE':
        return False
    rig = context.object
    selected = tuple(context.selected_pose_bones or ())
    rest = [pb for pb in selected if pb not in wrists and pb not in feet]
    return bool((wrists or rest) and rig and all(pb.id_data == rig for pb in selected)
                and all(obj == rig for obj in context.objects_in_mode)
                and all(limb_ik._owned(pb.bone, rig.data.get(limb_ik.ARMATURE_ID_KEY), role='POLE')
                        for pb in rest))


def _foot_targets(rig, feet):
    targets = {p.name: p for p in feet}
    if rig.pose.use_mirror_x:
        records = foot_controls.records(rig)
        for pb in feet:
            field, side = foot._role(rig, pb)
            other = rig.pose.bones[records['R' if side == 'L' else 'L'][field]]
            targets[other.name] = other
    return targets.values()


def _rotation_row(pb):
    field = wrist._rotation_path(pb.rotation_mode)
    return pb, {field: tuple(getattr(pb, field))}


def reset(context, wrists, feet):
    """Use the same native pole clear and evaluated end-effector solves as single selections."""
    if not supports(context, wrists, feet):
        raise ValueError('Select a foot control with an IK wrist or a generated pole on this rig.')
    rig = context.object
    poles = [p for p in context.selected_pose_bones if p not in wrists and p not in feet]
    pole_rows = native._native_rotation_snapshot(rig, poles)
    rows = [*pole_rows, *map(_rotation_row, _foot_targets(rig, feet))]
    wrist_plan = None
    if wrists:
        wrist_plan = wrist.prepare_wrist_neutral(context, targets=wrists)
        rows.extend(_rotation_row(side['target']) for side in wrist_plan.get('sides', (wrist_plan,)))

    auto = context.scene.tool_settings.use_keyframe_insert_auto
    rotation_keys = other_keys = None
    if auto:
        owner = owners.prepare(context, rig)
        key_plan = dict(rig=rig, target=feet[0], variant=owner, action=owner['action'],
                        slot=owner['slot'], frame=owner['frame'])
        rotation_keys = native._key_snapshot(context, key_plan, rows)
        if poles:
            other_keys = native._key_snapshot(context, key_plan, pole_rows, nonrotation=True)
    # Each public solver rolls back its own unsuccessful operation. Keep the
    # shared preview witness as well until both successful solves are committed.
    record = rig.get(natural_bind_mode.KEY)
    preview = None
    if auto and record is not None:
        preview = (record.get('rotation_keys'), record['changes'], natural_bind_mode._preview_inputs[rig.session_uid])
    with native._preserve_empty_groups(context, rotation_keys, (pb for pb, _ in rows)):
        try:
            if poles:
                native._clear_native_companions(context, rig, poles)
                if other_keys is not None:
                    native._restore_keys(rig, other_keys)
            foot.reset_rotation(context, feet)
            if wrist_plan is not None:
                wrist.apply_wrist_neutral(context, wrist_plan)
            names = tuple(pb.name for pb, _ in rows)
            if auto:
                natural_bind_mode.note_rotation_keys(context, rig, names)
            return dict(targets=names, keyed=bool(auto))
        except Exception:
            try:
                if rotation_keys is not None:
                    native._restore_keys(rig, rotation_keys)
                if other_keys is not None:
                    native._restore_keys(rig, other_keys)
            finally:
                if preview is not None:
                    old_keys, changes, inputs = preview
                    record['changes'] = changes
                    natural_bind_mode._preview_inputs[rig.session_uid] = inputs
                    if old_keys is not None:
                        record['rotation_keys'] = old_keys
                    elif 'rotation_keys' in record:
                        del record['rotation_keys']
                native._restore_native_rotations(context, rows)
            raise
