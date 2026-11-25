"""Restore an explicitly selected body to its original bind pose."""

import bpy


def _visible_selected(context, names):
    visible = getattr(context, 'visible_pose_bones', None)
    return bool(visible) and all(p.name in names for p in visible
                                 if p.id_data == context.object and not p.bone.hide_select)


def _selected_limb(rig, target, pole, chain, names):
    if {target.name, pole.name} <= names:
        return True
    return (rig.pose.bones[target.name].get('ik_fk') == 0.0 and target.hide and pole.hide
            and len(chain) == 3 and set(chain) <= names)


def _fk_body_selected(context, owner, names, limb):
    if not _visible_selected(context, names):
        return False
    from . import limb_control_selection
    rig = context.object
    tagged = {}
    for bone in rig.data.bones:
        if limb._owned(bone, owner):
            key = (bone.get(limb.KIND_KEY), bone.get(limb.SIDE_KEY), bone.get(limb.ROLE_KEY))
            if key[2] not in {'HAND_IK', 'FOOT_IK', 'POLE'}:
                continue
            if key in tagged:
                return False
            tagged[key] = bone
    for kind, target_role in (('ARM', 'HAND_IK'), ('LEG', 'FOOT_IK')):
        for side in ('L', 'R'):
            target = tagged.get((kind, side, target_role))
            pole = tagged.get((kind, side, 'POLE'))
            if target is None or pole is None:
                return False
            chain = limb_control_selection._chain(target.get(limb.CHAIN_KEY))
            if not chain or not _selected_limb(rig, target, pole, chain, names):
                return False
    return True


def body_selected(context, *, allow_auto_key=False):
    if context.mode != 'POSE':
        return False
    selected = context.selected_pose_bones or ()
    if len(selected) < 9:
        return False
    from . import foot_controls, limb_ik as limb, root_control
    rig = context.object
    if rig is None or rig.type != 'ARMATURE' or (context.scene.tool_settings.use_keyframe_insert_auto and not allow_auto_key):
        return False
    owner = rig.data.get(limb.ARMATURE_ID_KEY)
    if (not owner or rig.data.get(limb.SCHEMA_KEY) != limb.DIRECT_PREROLL_SCHEMA
            or not rig.data.get(foot_controls.RECORD_KEY) or any(obj != rig for obj in context.objects_in_mode)):
        return False
    root = False
    controls = set()
    for pb in selected:
        if pb.id_data != rig:
            return False
        bone = pb.bone
        if (bone.get(root_control.OWNER_KEY) == root_control.OWNER_VALUE
                and bone.get(root_control.ROLE_KEY) == 'MASTER'):
            root = True
        if limb._owned(bone, owner):
            controls.add((bone.get(limb.KIND_KEY), bone.get(limb.SIDE_KEY), bone.get(limb.ROLE_KEY)))
    complete = root and all((kind, side, role) in controls for side in ('L', 'R')
                            for kind, target in (('ARM', 'HAND_IK'), ('LEG', 'FOOT_IK'))
                            for role in (target, 'POLE'))
    if not complete and (not root or not _fk_body_selected(context, owner, {p.name for p in selected}, limb)):
        return False
    try:
        return set(foot_controls.records(rig)) == {'L', 'R'}
    except ValueError:
        # A damaged owned record belongs to the service preflight. It can
        # report the error to the artist before attempting a bind reset.
        return True


def _pose_inputs(rig):
    from . import control_pose_assets as poses
    return {pb.name: {key: tuple(getattr(pb, key)) if count > 1 else getattr(pb, key)
                      for key, count in poses._FIELDS.items()} for pb in rig.pose.bones}


def clear_body_rotation(context, wrists):
    """Calibrate the four limbs from Original Bones and keep the artist's other channels."""
    from . import controller_transform_clear as mixed, limb_ik_wrist_rotation as wrist
    settings = context.scene.tool_settings
    auto = settings.use_keyframe_insert_auto
    journal = other_keys = None
    if auto:
        selected = tuple(context.selected_pose_bones)
        plan = wrist.prepare_wrist_neutral(context, targets=wrists)
        bones = [(p, None) for p in selected]
        journal = mixed._key_snapshot(context, plan, bones)
        other_keys = mixed._key_snapshot(context, plan, bones, nonrotation=True)
    with mixed._preserve_empty_groups(context, journal, context.selected_pose_bones):
        try:
            if auto:
                settings.use_keyframe_insert_auto = False
            _clear_selected_rotations(context, other_keys)
        except Exception:
            if journal is not None:
                mixed._restore_keys(context.object, journal)
                mixed._restore_keys(context.object, other_keys)
            raise
        finally:
            if auto:
                settings.use_keyframe_insert_auto = True


def _clear_selected_rotations(context, other_keys):
    """Use the bind reference for Alt+R while preserving other channels."""
    from . import natural_bind_graph, natural_bind_mode, natural_limb_inventory, natural_limb_solve
    from . import natural_body_reference, limb_ik_fk, root_control
    if not body_selected(context):
        raise ValueError('Select the root and all four limb targets and poles in Pose Mode.')
    rig = context.object
    previous = natural_bind_mode.capture(context, rig)
    inventory = natural_limb_inventory.prepare(context, rig)
    selected = {pb.name for pb in context.selected_pose_bones}
    root = root_control.get_record(rig)
    if not root or root['master'] not in selected:
        raise ValueError('Select this rig’s root control to restore the whole body.')
    if not all({entry['target'].name, entry['pole'].name} <= selected for entry in inventory['rigs'].values()):
        if not _visible_selected(context, selected) or not all(
                _selected_limb(rig, entry['target'], entry['pole'], entry['chain'], selected)
                for entry in inventory['rigs'].values()):
            raise ValueError('Select the visible controls for all four limbs.')
    before = _pose_inputs(rig)
    graph = rig.get(natural_bind_graph.KEY)
    created = False
    restore_calibration = None
    try:
        if other_keys is not None:
            # Use Blender's rotation keying policy on the actual user selection.
            settings = context.scene.tool_settings
            settings.use_keyframe_insert_auto = True
            try:
                if bpy.ops.pose.rot_clear('EXEC_DEFAULT', False) != {'FINISHED'}:
                    raise ValueError('Blender could not key the selected rotations.')
            finally:
                settings.use_keyframe_insert_auto = False
            from . import controller_transform_clear as mixed
            mixed._restore_keys(rig, other_keys)
        for clear in (bpy.ops.pose.loc_clear, bpy.ops.pose.scale_clear, bpy.ops.pose.rot_clear):
            if clear('EXEC_DEFAULT', False) != {'FINISHED'}:
                raise ValueError('Blender could not clear the selected body controls.')
        natural_body_reference.prepare_body(context, rig, inventory)
        natural_limb_solve.reset_limbs(context, rig, inventory)
        if graph is None:
            natural_bind_graph.install(context, rig, inventory, solver='STABLE_MCH_ORI')
            created = True
        else:
            restore_calibration = natural_bind_graph.recalibrate(
                context, rig, inventory, solver='STABLE_MCH_ORI')
        if natural_bind_graph.solver_kind(rig) != 'STABLE_MCH_ORI':
            raise ValueError('The hidden Stable limb solver could not be activated.')
        natural_limb_solve.verify_bind(rig, inventory)
        from . import controller_transform_clear as mixed
        rotation_fields = set(mixed._ROTATION_FIELDS + mixed._BEND_FIELDS)
        for name in selected:
            pb = rig.pose.bones[name]
            for field, saved in before[name].items():
                if field in rotation_fields:
                    continue
                current = getattr(pb, field)
                if (tuple(current) if isinstance(saved, tuple) else current) != saved:
                    setattr(pb, field, saved)
        limb_ik_fk._update(context, rig)
        natural_bind_mode.begin_preview(context, rig, previous)
        natural_bind_mode.hold_control_channels(
            context, rig, context.selected_pose_bones, ('location', 'scale'))
        if other_keys is not None:
            natural_bind_mode.note_rotation_keys(context, rig, selected)
    except Exception:
        if created:
            natural_bind_graph.remove(context, rig)
        elif restore_calibration:
            restore_calibration()
        after = _pose_inputs(rig)
        for name, values in before.items():
            pb = rig.pose.bones[name]
            for field, saved in values.items():
                if saved != after[name][field]:
                    setattr(pb, field, saved)
        limb_ik_fk._update(context, rig)
        raise
