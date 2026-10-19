"""Atomic foot-only helper rebuild after an intentional native foot edit.

The visible native pose becomes the rebuilt foot controls' new zero point.
Source bones, skin weights and the base leg IK are retained.  This deliberately
does not repair native leg roll or reinterpret a Pole angle.
"""
import copy

import bpy

from . import (body_region, body_setup, body_setup_transaction as transaction,
               body_setup_removal,
               control_colors, control_pose_assets, foot_controls as feet, limb_ik,
               limb_ik_fk)


_CHANNELS = {'location', 'rotation_euler', 'rotation_quaternion',
             'rotation_axis_angle', 'scale', 'matrix_basis'}
_TAGS = {feet.OWNER_KEY, feet.ID_KEY, feet.ROLE_KEY, feet.SIDE_KEY}


def _sides(values):
    values = tuple(values)
    if not values or len(set(values)) != len(values) or set(values) - {'L', 'R'}:
        raise ValueError('Choose the left foot, right foot, or both feet.')
    return tuple(side for side in ('L', 'R') if side in values)


def _local_properties(owner):
    values, ui = transaction._properties(owner)
    return ({key: value for key, value in values.items() if key not in _TAGS},
            {key: value for key, value in ui.items() if key not in _TAGS})


def _restore_properties(owner, state):
    # Recreated helpers and widgets have new authenticated Foot owner IDs.
    # Preserve artist properties without copying the discarded ownership tags.
    values, ui = state
    for key, value in values.items():
        owner[key] = transaction._decode(value, {})
        if key in ui:
            owner.id_properties_ui(key).update(**ui[key])


def _capture_display(rig, record):
    result = {}
    backups = []
    try:
        for role in ('FOOT_ROLL', 'TOE_BEND'):
            pb = rig.pose.bones[record['bones'][role]]
            obj = pb.custom_shape
            mesh = obj.data.copy()
            backups.append(mesh)
            mesh.name = '__CD_FOOT_REBUILD__' + obj.data.name
            result[role] = {
                'mesh': mesh, 'object_name': obj.name, 'mesh_name': obj.data.name,
                'object_rna': transaction._rna(obj, transaction._OBJECT_EXCLUDE),
                'object_properties': _local_properties(obj),
                'mesh_properties': _local_properties(obj.data),
                'materials': tuple(obj.data.materials),
                'pose_rna': transaction._rna(pb, transaction._POSE_EXCLUDE | _CHANNELS),
                'pose_properties': transaction._properties(pb),
                'bone_properties': _local_properties(pb.bone),
                'display': limb_ik._pose_shape_json_state(pb),
                'colors': control_colors.capture_bone(pb),
                'hidden': pb.bone.hide, 'hide_select': pb.bone.hide_select,
            }
        return result
    except Exception:
        for mesh in backups:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise


def _discard_display(state):
    for saved in state.values():
        mesh = saved['mesh']
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _restore_display(rig, record, state):
    for role, saved in state.items():
        pb = rig.pose.bones[record['bones'][role]]
        obj = pb.custom_shape
        # These are generated display resources; native Object/Mesh IDs are
        # never replaced. Old Foot widget IDs can be recreated with their names.
        obj.name, obj.data.name = saved['object_name'], saved['mesh_name']
        transaction._restore_mesh_geometry(obj.data, saved['mesh'])
        obj.data.materials.clear()
        for material in saved['materials']:
            obj.data.materials.append(material)
        transaction._set_rna(obj, saved['object_rna'], {})
        _restore_properties(obj, saved['object_properties'])
        _restore_properties(obj.data, saved['mesh_properties'])
        _restore_properties(pb.bone, saved['bone_properties'])
        transaction._set_properties(pb, saved['pose_properties'], {})
        transaction._set_rna(pb, saved['pose_rna'], {})
        display = saved['display']
        transform = display['custom_shape_transform']
        pb.custom_shape_transform = rig.pose.bones[transform] if transform else None
        pb.custom_shape_scale_xyz = display['scale']
        pb.custom_shape_translation = display['translation']
        pb.custom_shape_rotation_euler = display['rotation']
        pb.use_custom_shape_bone_size = display['use_bone_size']
        if display['wire_width'] is not None:
            pb.custom_shape_wire_width = display['wire_width']
        control_colors.restore_bone_state(pb, saved['colors'])
        pb.bone.hide, pb.bone.hide_select = saved['hidden'], saved['hide_select']
        record['widgets'][role] = {'object': obj.name, 'mesh': obj.data.name}


def _preflight(context, rig, sides):
    body_setup._require_rig(context, rig)
    from . import animation_worklist_target, body_original_mode
    if body_original_mode.active(rig) or animation_worklist_target.is_adopted(rig):
        raise ValueError('Return to the main character Controls before rebuilding feet.')
    if context.screen and context.screen.is_animation_playing:
        raise ValueError('Pause playback before rebuilding feet.')
    if rig.data.pose_position != 'POSE':
        raise ValueError('Choose Pose display and show the intended neutral foot pose first.')
    if body_region.RECORD_KEY in rig.data:
        raise ValueError('Finish the released body part before rebuilding foot controls.')
    inventory = limb_ik._validate_inventory(rig)
    records = feet.validate(rig, inventory)
    affected = set()
    for side in sides:
        record = records.get(side)
        if record is None:
            raise ValueError(f'The {side} foot has no existing Foot Controls to rebuild.')
        leg = inventory['rigs'][('LEG', side)]
        if abs(rig.pose.bones[record['target']].get(limb_ik_fk.PROPERTY, 1.0) - 1.0) > 1e-6:
            raise ValueError('Switch the selected feet to IK before rebuilding their controls.')
        feet._refuse_foreign_dependencies(rig, record)
        affected.update(record['chain'])
        affected.add(record['toe'])
        affected.update(record['bones'].values())
        affected.update(pb.name for pb, _con, _entry in leg['entries'])
        affected.update(b.name for b in rig.data.bones
                        if b.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                        and b.get(limb_ik.KIND_KEY) == 'LEG'
                        and b.get(limb_ik.SIDE_KEY) == side)
    own_paths = limb_ik_fk.owned_driver_paths(rig) | feet.owned_driver_paths(rig)
    body_region._animations(rig, affected, own_paths)
    # The Rest resync guard covers all affected input readers, native toe
    # descendants, Hook/Warp bone references, attachments and foreign constraints.
    from . import body_rest_resync
    body_rest_resync._refuse_dependencies(rig, inventory, {('LEG', s) for s in sides})
    own_constraints = body_rest_resync._owned_constraint_pointers(rig, inventory)
    for obj in bpy.data.objects:
        owners = [obj]
        if obj.type == 'ARMATURE' and obj.pose:
            owners.extend(obj.pose.bones)
        for owner in owners:
            for con in owner.constraints:
                if (obj.as_pointer(), owner.as_pointer(), con.as_pointer()) in own_constraints:
                    continue
                if (getattr(con, 'space_object', None) is rig
                        and getattr(con, 'space_subtarget', '') in affected):
                    raise ValueError('An external constraint uses the selected foot as its custom space; preserve that dependency first.')
    widgets = {bpy.data.objects[entry['object']] for side in sides
               for entry in records[side]['widgets'].values()}
    body_region._check_widget_dependencies(widgets)
    return inventory, {side: copy.deepcopy(records[side]) for side in sides}, affected


def _verify_anchors(rig, records):
    errors = {}
    for side, record in records.items():
        foot = rig.pose.bones[record['chain'][2]]
        toe = rig.pose.bones[record['toe']]
        refs = {'ANKLE_SOLVER': foot.head, 'BALL_PIVOT': toe.head, 'TOE_TIP_PIVOT': toe.tail}
        errors[side] = {}
        for role, point in refs.items():
            distance = (rig.matrix_world @ rig.pose.bones[record['bones'][role]].head
                        - rig.matrix_world @ point).length
            errors[side][role] = distance
            if distance > 3e-4:
                raise ValueError(f'The rebuilt {side} {role.lower()} did not align to the foot; changes were rolled back.')
    return errors


def rebuild(context, rig, sides=('L', 'R'), shoe=None):
    """Replace selected Foot helpers atomically, retaining native skin and shapes.

    This absorbs the current unkeyed Foot Roll/Toe Bend pose into new helper
    frames. It does not clear native bones or weights, nor regenerate a Pole.
    """
    sides = _sides(sides)
    inventory, old_records, affected = _preflight(context, rig, sides)
    owner_proof = body_region._ownership_seed(rig)
    native_rest = control_pose_assets.native_rest(rig)
    display = {}
    pole_channels = {}
    for side in sides:
        name = inventory['rigs'][('LEG', side)]['pole'].name
        pb = rig.pose.bones[name]
        pole_channels[name] = {'rotation_mode': pb.rotation_mode,
                              **{field: tuple(getattr(pb, field)) for field in _CHANNELS - {'matrix_basis'}}}

    def commit():
        try:
            protected = body_region._guard(rig, affected)
            skin = body_setup._native_skin(rig)
            surfaces = body_setup_removal._bound_surfaces(context, rig)
            for side in sides:
                display[side] = _capture_display(rig, old_records[side])
            for side in sides:
                feet.remove(context, rig, ('LEG', side))
                record = feet.build(context, rig, ('LEG', side),
                                    toe_name=old_records[side]['toe'], shoe=shoe)
                _restore_display(rig, record, display[side])
                for field in ('roll_visual_backup', 'roll_visual_fit'):
                    if field in old_records[side]:
                        record[field] = copy.deepcopy(old_records[side][field])
                    else:
                        record.pop(field, None)
                values = feet._all_records(rig)
                values[side] = record
                feet._write_records(rig, values)
            # Removing an extension uses the general no-pop matcher, which may
            # make a tiny Pole-plane correction. Foot-only rebuilding retains
            # the animator's Pole input; verify the final skin with it restored.
            for name, state in pole_channels.items():
                pb = rig.pose.bones[name]
                pb.rotation_mode = state['rotation_mode']
                for field, value in state.items():
                    if field != 'rotation_mode':
                        setattr(pb, field, value)
            feet._update(context, rig)
            if control_pose_assets.native_rest(rig) != native_rest:
                raise ValueError('Foot rebuild changed native Rest bones; changes were rolled back.')
            body_region._verify_guard(rig, protected, affected)
            body_setup._verify_skin(rig, skin, tolerance=body_setup_removal.SKIN_MATRIX_TOLERANCE)
            surface_error = body_setup_removal._check_surfaces(context, rig, surfaces)
            records = {side: feet.get_record(rig, side) for side in sides}
            errors = _verify_anchors(rig, records)
            limb_ik._validate_inventory(rig)
            body_region._finish_owner_revision(rig, owner_proof)
            return {'sides': sides, 'rebuilt_helpers': sum(len(r['bones']) for r in records.values()),
                    'native_pose_preserved': True, 'custom_shapes_preserved': True,
                    'anchor_errors_world': errors, 'surface_error': surface_error,
                    'pole_rebuilt': False}
        finally:
            for state in display.values():
                _discard_display(state)

    # Admission remains active during both commit and in-place rollback. Keep
    # historical animation owners intact while refreshing generated ownership.
    with body_region._ownership_operation(rig, owner_proof):
        return body_setup._atomic(context, rig, commit)
