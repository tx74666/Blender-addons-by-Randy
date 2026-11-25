"""Evaluate the same IK leg at zero roll to find the moving forefoot contact."""
from . import foot_controls as feet, natural_bind_graph as graph


def _sources(rig, record):
    # The original input and pole stay shared. Only the portion whose result
    # changes when the reverse foot moves its ankle needs a second evaluation.
    b = record['bones']
    upper, lower, foot = record['chain']
    roles = dict(ANKLE=b['ANKLE_SOLVER'], UPPER=upper, LOWER=lower, FOOT=foot,
                 IK_TOE=b['IK_TOE_REF'], FK_TOE=b['FK_TOE_REF'], TOE=b['TOE_SPACE'])
    if record.get('auto_follow') == 1:
        roles.update(AUTO=b['AUTO_ROTATION_REF'], AUTO_TOE=b['AUTO_TOE_REF'])
    for segment, bone in (('UPPER', upper), ('LOWER', lower)):
        source = rig.pose.bones[bone]
        # Find the actual registered orientation and mechanism, including the
        # extra Stable branch on rigs initially constructed with Direct IK.
        for con in source.constraints:
            if (con.type != 'COPY_ROTATION' or con.target != rig
                    or not con.name.startswith('CDLimbIK_SOURCE_' + segment + '_ROTATION_')):
                continue
            ori = rig.pose.bones[con.subtarget]
            track, = [c for c in ori.constraints if c.type == 'DAMPED_TRACK' and c.target == rig]
            roles['ORI_' + segment] = ori.name
            roles['MCH_' + segment] = track.subtarget
    return roles


_CONSTRAINT_FIELDS = {
    'IK': ('chain_count', 'use_tail', 'use_stretch', 'use_location', 'use_rotation', 'weight',
           'orient_weight', 'iterations', 'pole_subtarget'),
    'COPY_ROTATION': ('mix_mode', 'euler_order', 'use_x', 'use_y', 'use_z',
                      'invert_x', 'invert_y', 'invert_z'),
    'COPY_LOCATION': ('head_tail', 'use_x', 'use_y', 'use_z', 'invert_x', 'invert_y', 'invert_z', 'use_offset'),
    'COPY_TRANSFORMS': ('mix_mode', 'head_tail', 'remove_target_shear'),
    'DAMPED_TRACK': ('head_tail', 'track_axis'),
}


def _copy_constraint(rig, added, names, source, dest, old):
    if old.name == graph.COPY or old.name.startswith('CD Heel Lift '):
        return
    if old.type not in _CONSTRAINT_FIELDS or old.target != rig:
        raise ValueError('The unrolled leg has an unregistered constraint: ' + source.name + ' / ' + old.name)
    fields = {field: getattr(old, field) for field in _CONSTRAINT_FIELDS[old.type]}
    fields.update(subtarget=names.get(old.subtarget, old.subtarget),
                  target_space=old.target_space, owner_space=old.owner_space)
    if old.space_object:
        if old.space_object != rig:
            raise ValueError('The unrolled leg uses an external transform space.')
        fields['space_subtarget'] = names.get(old.space_subtarget, old.space_subtarget)
    con = feet._add_constraint(added, dest, old.type, old.name, rig, **fields)
    entry = added['constraints'][-1]
    entry['hinge_reference'] = dict(owner=source.name, name=old.name)
    if old.space_object:
        con.space_object = rig
        entry['custom_space'] = True
    if old.type == 'IK':
        if old.pole_target != rig or old.ik_type != 'COPY_POSE':
            raise ValueError('The unrolled IK needs its registered local knee pole.')
        con.pole_target = rig
        con.pole_angle = old.pole_angle
    # Mirror scalar settings, including Auto/manual switching and the Stable
    # solver's calibrated knee plane. Read the original inputs, never its
    # final rolled bone matrix. The bind preview mutes the public native IK
    # only to route deformers via its own source; the reference IK stays on.
    for field in ('influence', 'mute', *(('pole_angle',) if old.type == 'IK' else ())):
        if field == 'mute' and old.type == 'IK' and graph.KEY in rig:
            import json
            rows = json.loads(rig[graph.KEY]['ik'])
            con.mute = next(bool(was_muted) for owner, name, was_muted in rows
                            if owner == source.name and name == old.name)
            entry['hinge_reference']['original_ik_mute'] = con.mute
        else:
            path = old.path_from_id(field)
            added['drivers'].append(feet._driver(rig, con, field, -1, 'value', {'value': path}))


def build(context, rig, record, added):
    """Return the zero-roll foot pose and toe control's unbent parent frame."""
    from . import foot_roll_hinge_plant as plant
    roles = _sources(rig, record)
    side = record['side']
    names = {old: 'MCH_foot_unrolled_' + role.lower() + '.' + side for role, old in roles.items()}
    with plant.editing(context, rig) as bones:
        for role, old in roles.items():
            source = bones[old]
            plant.create(bones, added, 'UNROLLED_' + role, names[old], source)
        for role, old in roles.items():
            source = bones[old]
            parent_name = record['target'] if role in {'ANKLE', 'IK_TOE'} else (
                source.parent.name if source.parent else None)
            parent_name = names.get(parent_name, parent_name)
            helper = bones[names[old]]
            helper.parent = bones.get(parent_name) if parent_name else None
            helper.use_connect = source.use_connect and role not in {'ANKLE', 'IK_TOE'}
    for role, old in roles.items():
        source, dest = rig.pose.bones[old], rig.pose.bones[names[old]]
        plant.register(rig, record, added, 'UNROLLED_' + role)
        dest.rotation_mode = source.rotation_mode
        dest.matrix_basis = source.matrix_basis
        if role in {'MCH_UPPER', 'MCH_LOWER', 'UPPER', 'LOWER', 'FOOT'}:
            for axis in 'xyz':
                for prefix in ('lock_ik_', 'use_ik_limit_', 'ik_min_', 'ik_max_', 'ik_stiffness_'):
                    field = prefix + axis
                    setattr(dest, field, getattr(source, field))
            dest.ik_stretch = source.ik_stretch
        if role in {'UPPER', 'LOWER', 'FOOT'}:
            rot = {'QUATERNION': 'rotation_quaternion', 'AXIS_ANGLE': 'rotation_axis_angle'}.get(
                source.rotation_mode, 'rotation_euler')
            for field in ('location', rot, 'scale'):
                for i in range(len(getattr(source, field))):
                    path = source.path_from_id(field) + f'[{i}]'
                    added['drivers'].append(feet._driver(rig, dest, field, i, 'value', {'value': path}))
        for con in source.constraints:
            _copy_constraint(rig, added, names, source, dest, con)
    return names[roles['FOOT']], names[roles['TOE']]
