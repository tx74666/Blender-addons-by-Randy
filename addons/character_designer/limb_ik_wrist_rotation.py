"""Explicit IK wrist neutral, preserving native FK inputs and the existing rig.

Only the visible target's active rotation representation is written. Raw target
zero is not redefined: this operation solves the compensation that makes the
native hand look like its own FK Alt-R at the current solved forearm pose.
"""
import math

PLAN_API = 2
MAX_ITERATIONS = 8
ROTATION_TOLERANCE = 3e-5
POSITION_TOLERANCE = 2e-5
SCALE_TOLERANCE = 2e-5
MATRIX_TOLERANCE = 4e-4
_TRANSFORMS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')
_EULER = {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}


def _require(value, message):
    if not value:
        raise ValueError(message)


def _backend():
    from types import SimpleNamespace
    import bpy
    from . import limb_ik, limb_ik_fk
    return None, SimpleNamespace(bpy=bpy, limb=limb_ik, match=limb_ik_fk), None


def _keying():
    from . import limb_ik_wrist_keying
    return limb_ik_wrist_keying


def _transaction():
    from . import limb_ik_wrist_transaction
    return limb_ik_wrist_transaction


def _owner_guard():
    from . import limb_ik_wrist_owner_guard
    return limb_ik_wrist_owner_guard


def _raw_one(target):
    return dict(mode=target.rotation_mode, ik_fk=target.get('ik_fk'),
                **{key: tuple(getattr(target, key)) for key in _TRANSFORMS})


def _difference(first, second):
    return max(abs(float(first[row][column])-float(second[row][column])) for row in range(4) for column in range(4))


def _refresh_reference(context, plan):
    rig, target, forearm = plan['rig'], plan['target'], plan['forearm']
    plan['wanted_native'] = _reference(plan['api'], rig, plan['end'])
    plan['target_position'] = target.matrix.translation.copy()
    plan['forearm_matrix'] = forearm.matrix.copy()
    plan['pole_matrix'] = rig.pose.bones[plan['limb']['pole'].name].matrix.copy()
    plan['world_matrix'] = rig.matrix_world.copy()


def _key_rotation(context, plan):
    """The key service owns its exact in-place curve transaction, never a row."""
    return _keying().key_rotation(context, plan)


def _fault(phase):
    """Inert seam for actual-write and post-key rollback validation."""
    return None


def _same(first, last):
    if first is last:
        return True
    return (first is not None and last is not None and hasattr(first, 'as_pointer')
            and hasattr(last, 'as_pointer') and first.as_pointer() == last.as_pointer())


def _rotation_path(mode):
    _require(mode in _EULER | {'QUATERNION', 'AXIS_ANGLE'}, 'Unsupported wrist rotation representation.')
    return 'rotation_quaternion' if mode == 'QUATERNION' else 'rotation_axis_angle' if mode == 'AXIS_ANGLE' else 'rotation_euler'


def _raw(rig):
    return {pb.name: dict(mode=pb.rotation_mode, ik_fk=pb.get('ik_fk'),
                         **{key: tuple(getattr(pb, key)) for key in _TRANSFORMS}) for pb in rig.pose.bones}


def _object_raw(rig):
    return (rig.rotation_mode, *(tuple(getattr(rig, key)) for key in _TRANSFORMS))


def _frame(context):
    return float(context.scene.frame_current + context.scene.frame_subframe)


def _physics_idle(context, rig, physics):
    state = physics.status(context)
    if state.get('rig') is rig:
        _require(state.get('valid'), 'Resolve the Physics error before resetting the wrist.')
    hair = state.get('hair', {})
    _require(not hair.get('running') and not hair.get('pending_recovery') and
             not any(row.get('running') or row.get('pending_recovery') for row in state.get('dresses', ())),
             'Pause Hair and Dress physics and finish their recovery before resetting the wrist.')


def _mirror_flags(rig):
    return bool(getattr(rig.pose, 'use_mirror_x', False)), bool(getattr(rig.data, 'use_mirror_x', False))


def _input_policy(context, rig, *, mirror_allowed=False):
    _require(mirror_allowed or not _mirror_flags(rig)[0],
             'Pose mirroring needs the complete authenticated wrist pair.')
    if context.scene.tool_settings.use_keyframe_insert_auto:
        _require(all(getattr(getattr(context.scene, name, None), 'active', None) is None
                     for name in ('keying_sets', 'keying_sets_all')),
                 'Use the default keying policy before resetting wrist rotation with Auto Key.')


def _resolve_local(context, rig, target, owner):
    """Authenticate only the selected ARM registry and native RNA endpoints."""
    api = _backend()[1]
    if target is None:
        target = getattr(context, 'active_pose_bone', None)
    if isinstance(target, str):
        target = rig.pose.bones.get(target)
    _require(target is not None and _same(rig.pose.bones.get(target.name), target),
             'Choose one owned hand IK target on the current rig.')
    inventory = owner.setdefault('_wrist_registry', {'rigs': {}})
    found = [(key, entry) for key, entry in inventory['rigs'].items() if _same(entry['target'], target.bone)]
    if found:
        key, entry = found[0]
    else:
        key, entry = _local_inventory(api.limb, api.match, rig, target)
        _require(key not in inventory['rigs'], 'The wrist side has ambiguous target ownership.')
        inventory['rigs'][key] = entry
    end, forearm = (rig.pose.bones[entry['chain'][index]] for index in (2, 1))
    stamp = _graph_stamp(rig, target, end, forearm, key, entry)
    previous = owner.setdefault('_wrist_graphs', {}).setdefault(target.name, stamp)
    _require(stamp == previous, 'The owned wrist graph or native Rest changed after preparation.')
    return target, end, forearm, inventory, key, entry


def _local_inventory(limb, match, rig, target):
    """Selected ARM validation; no global display, object or bone discovery.

    The existing registry decoder and per-constraint driver/parent-delta
    validators remain authoritative. Each expected record is located by its
    actual native owner/name/type; a prefix or tag alone never authorizes it.
    """
    from mathutils import Vector
    armature_id = rig.data.get(limb.ARMATURE_ID_KEY)
    bone = target.bone
    rig_id, side = bone.get(limb.RIG_ID_KEY), bone.get(limb.SIDE_KEY)
    _require(type(armature_id) is str and armature_id and type(rig_id) is str and rig_id
        and side in limb.SIDES and bone.get(limb.KIND_KEY) == 'ARM'
        and limb._owned(bone, armature_id, role='HAND_IK', rig_id=rig_id), 'The selected wrist ownership is invalid.')
    schema = rig.data.get(limb.SCHEMA_KEY, limb.LEGACY_SCHEMA)
    _require(schema in {limb.LEGACY_SCHEMA, limb.ENHANCED_SCHEMA, limb.ROLL_DECOUPLED_SCHEMA,
        limb.LEGACY_DIRECT_PREROLL_SCHEMA, limb.DIRECT_PREROLL_SCHEMA}
        and type(rig.data.get(limb.TARGET_ROTATION_VERSION_KEY)) is int
        and rig.data.get(limb.TARGET_ROTATION_VERSION_KEY) == limb.TARGET_ROTATION_VERSION,
        'Rebuild this wrist with the supported target-rotation contract.')
    chain = limb.validation_json.decode(bone.get(limb.CHAIN_KEY, ''))
    _require(type(chain) is list and len(chain) == 3 and len(set(chain)) == 3
        and all(type(name) is str and name and rig.pose.bones.get(name) is not None for name in chain),
        'The selected wrist source chain is invalid.')
    chain = tuple(chain)
    _require(rig.data.bones[chain[1]].parent == rig.data.bones[chain[0]]
        and rig.data.bones[chain[2]].parent == rig.data.bones[chain[1]], 'The native wrist chain parent changed.')
    end = rig.pose.bones[chain[2]]
    registry = limb._constraint_registry(end, strict=True)
    samples = [record for record in registry.values() if type(record) is dict
        and record.get('rig_id') == rig_id and record.get('role') == 'END_ROTATION']
    _require(len(samples) == 1, 'The wrist has no unique actual end-rotation record.')
    sample = samples[0]
    enhanced, roll, direct = (limb._is_enhanced_schema(schema), limb._is_roll_decoupled_schema(schema),
                              limb._is_direct_preroll_schema(schema))
    dynamic_display = limb._uses_dynamic_pole_display(schema)
    _require(sample.get('target') == target.name and sample.get('chain') == list(chain)
        and sample.get('kind') == 'ARM' and sample.get('side') == side, 'The wrist record targets another source chain.')
    pole_name = sample.get('pole')
    _require(type(pole_name) is str and pole_name and pole_name != target.name, 'The wrist pole identity is invalid.')
    pole = rig.data.bones.get(pole_name)
    _require(limb._owned(pole, armature_id, role='POLE', rig_id=rig_id), 'The actual wrist pole owner changed.')
    names = {name: sample.get(name, '') for name in ('line', 'display', 'heel', 'solver_target', 'master',
                                                   'mch_upper', 'mch_lower', 'ori_upper', 'ori_lower')}
    if enhanced:
        _require(all(type(names[name]) is str and names[name] for name in ('line', 'display', 'solver_target', 'master'))
            and names['solver_target'] == target.name and names['master'] == limb.MASTER_NAME and not names['heel'],
            'The enhanced wrist names are invalid.')
    if dynamic_display:
        _require(type(names['display']) is str and names['display'], 'The owned pole display reference is missing.')
    if roll:
        _require(all(type(names[name]) is str and names[name] for name in ('mch_upper', 'mch_lower', 'ori_upper', 'ori_lower'))
            and len({names[name] for name in ('mch_upper', 'mch_lower', 'ori_upper', 'ori_lower')}) == 4,
            'The wrist roll-decoupled mechanisms are invalid.')
    roles = {'IK': ('IK', names['mch_lower'] if roll else chain[1]),
             'END_ROTATION': ('COPY_ROTATION', chain[2]), 'AUTO_OFFSET_ROTATION': ('COPY_ROTATION', chain[2])}
    if enhanced: roles['POLE_LINE_STRETCH'] = ('STRETCH_TO', names['line'])
    if dynamic_display: roles['POLE_DISPLAY_TRACK'] = ('DAMPED_TRACK', names['display'])
    if roll:
        for segment, source in (('UPPER', chain[0]), ('LOWER', chain[1])):
            roles['ORI_' + segment + '_LOCATION'] = ('COPY_LOCATION', names['ori_' + segment.lower()])
            roles['ORI_' + segment + '_TRACK'] = ('DAMPED_TRACK', names['ori_' + segment.lower()])
            roles['SOURCE_' + segment + '_ROTATION'] = ('COPY_ROTATION', source)
    owners = {owner_name for _, owner_name in roles.values()}
    entries, by_role = [], {}
    common = ('armature_id', 'rig_id', 'kind', 'side', 'chain', 'target', 'pole')
    if enhanced: common += ('line', 'display', 'heel', 'solver_target', 'master')
    elif dynamic_display: common += ('display',)
    if roll: common += ('mch_upper', 'mch_lower', 'ori_upper', 'ori_lower')
    for owner_name in sorted(owners):
        pb = rig.pose.bones.get(owner_name)
        _require(pb is not None, 'A local wrist constraint owner is missing.')
        for constraint_name, record in limb._constraint_registry(pb, strict=True).items():
            if type(record) is not dict or record.get('rig_id') != rig_id:
                continue
            role = record.get('role')
            _require(role in roles and role not in by_role and roles[role][1] == pb.name
                and record.get('owner') == limb.OWNER_VALUE and record.get('version') == limb.RIG_VERSION
                and record.get('armature_id') == armature_id and all(record.get(field, '') == sample.get(field, '') for field in common)
                and constraint_name == limb._constraint_name(rig_id, role), 'The local wrist registry fingerprint is inconsistent.')
            actual = [con for con in pb.constraints if con.name == constraint_name]
            _require(len(actual) == 1 and actual[0].type == roles[role][0], 'The owned native wrist constraint is missing or changed type.')
            con = actual[0]
            switched = match.validate_constraint_influence(rig, pb, con, record)
            _require(switched or math.isfinite(float(con.influence)) and abs(float(con.influence)-1.) <= 1e-6,
                     'A local wrist constraint influence was edited.')
            auto = bone.get(limb.AUTO_ALIGN_KEY, False)
            _require(type(auto) is bool, 'The wrist Auto Align state is invalid.')
            expected_mute = auto if role == 'END_ROTATION' else not auto if role == 'AUTO_OFFSET_ROTATION' else False
            _require(bool(con.mute) == expected_mute, 'The local wrist mute state differs from its mode.')
            entry = (pb, con, record); entries.append(entry); by_role[role] = entry
    _require(set(by_role) == set(roles), 'The selected wrist graph is incomplete.')
    auto = bone.get(limb.AUTO_ALIGN_KEY, False)
    display_transform = target.custom_shape_transform
    _require((display_transform == end or display_transform is None and schema != limb.DIRECT_PREROLL_SCHEMA)
        if auto else display_transform is None, 'The wrist input display frame changed.')
    helpers = {field: rig.data.bones.get(names[field]) if names[field] else None
               for field in ('line', 'display', 'mch_upper', 'mch_lower', 'ori_upper', 'ori_lower')}
    if enhanced:
        master = rig.data.bones.get(limb.MASTER_NAME)
        _require(limb._owned(master, armature_id, role='MASTER') and master.parent is None
            and bone.parent == master and pole.parent == master, 'The enhanced wrist parent owner changed.')
        _require(limb._owned(helpers['line'], armature_id, role='POLE_LINE', rig_id=rig_id)
            and helpers['line'].parent == rig.data.bones[chain[0]], 'The local pole line owner changed.')
        _, stretch, _ = by_role['POLE_LINE_STRETCH']
        _require(stretch.target is rig and stretch.subtarget == pole.name
            and stretch.target_space == stretch.owner_space == 'WORLD', 'The local pole line relation changed.')
    elif direct:
        _require(not bone.use_connect and not pole.use_connect
            and _local_parent(limb, rig, bone) and _local_parent(limb, rig, pole), 'The direct wrist parent owner changed.')
    if dynamic_display:
        display = helpers['display']
        _require(limb._owned(display, armature_id, role='POLE_DISPLAY', rig_id=rig_id)
            and display.parent == pole and not display.use_connect and not display.use_deform,
            'The local pole display owner changed.')
        _, track, _ = by_role['POLE_DISPLAY_TRACK']
        _require(track.target is rig and track.subtarget == chain[1] and abs(float(track.head_tail)) <= 1e-6
            and track.track_axis == 'TRACK_NEGATIVE_Y' and track.target_space == track.owner_space == 'WORLD',
            'The local pole display relation changed.')
    if roll:
        source_upper, source_lower = rig.data.bones[chain[0]], rig.data.bones[chain[1]]
        anchor = source_upper.parent or rig.data.bones.get(limb.MASTER_NAME)
        for field, role in (('mch_upper', 'MCH_UPPER'), ('mch_lower', 'MCH_LOWER'), ('ori_upper', 'ORI_UPPER'), ('ori_lower', 'ORI_LOWER')):
            _require(limb._owned(helpers[field], armature_id, role=role, rig_id=rig_id), 'The roll mechanism ownership changed.')
        _require(helpers['mch_upper'].parent == anchor and helpers['mch_lower'].parent == helpers['mch_upper']
            and helpers['mch_lower'].use_connect and helpers['ori_upper'].parent == anchor
            and helpers['ori_lower'].parent == anchor and not helpers['ori_upper'].use_connect
            and not helpers['ori_lower'].use_connect, 'The roll mechanism parent changed.')
        for field, source in (('mch_upper', source_upper), ('mch_lower', source_lower), ('ori_upper', source_upper), ('ori_lower', source_lower)):
            helper = helpers[field]
            _require((Vector(helper.head_local)-Vector(source.head_local)).length <= 1e-6
                and (Vector(helper.tail_local)-Vector(source.tail_local)).length <= 1e-6
                and limb._rotation_error(helper.matrix_local, source.matrix_local) <= 1e-6,
                'The native roll rest frame changed.')
        for segment, source_name, mch, ori in (('UPPER', chain[0], helpers['mch_upper'], helpers['ori_upper']),
                                              ('LOWER', chain[1], helpers['mch_lower'], helpers['ori_lower'])):
            _, location, _ = by_role['ORI_' + segment + '_LOCATION']
            _, track, _ = by_role['ORI_' + segment + '_TRACK']
            _, rotation, _ = by_role['SOURCE_' + segment + '_ROTATION']
            _require(location.target is rig and location.subtarget == mch.name and abs(float(location.head_tail)) <= 1e-6
                and location.target_space == location.owner_space == 'WORLD', 'The local roll location relation changed.')
            _require(track.target is rig and track.subtarget == mch.name and abs(float(track.head_tail)-1.) <= 1e-6
                and track.track_axis == 'TRACK_Y' and track.target_space == track.owner_space == 'WORLD',
                'The local roll tracking relation changed.')
            _require(rotation.target is rig and rotation.subtarget == ori.name and rotation.target_space == rotation.owner_space == 'WORLD'
                and rotation.mix_mode == 'REPLACE', 'The local source rotation relation changed.')
    _, ik, _ = by_role['IK']; _, end_rotation, _ = by_role['END_ROTATION']
    _require(ik.target is rig and ik.subtarget == target.name and ik.pole_target is rig and ik.pole_subtarget == pole.name
        and ik.chain_count == 2 and ik.target_space == ik.owner_space == 'WORLD'
        and (not hasattr(ik, 'use_tail') or ik.use_tail) and (not hasattr(ik, 'use_stretch') or not ik.use_stretch)
        and (not hasattr(ik, 'use_rotation') or not ik.use_rotation), 'The actual wrist IK solver relation changed.')
    world = end_rotation.target_space == end_rotation.owner_space == 'WORLD'
    direct_space = direct and end_rotation.target_space == 'LOCAL_OWNER_ORIENT' and end_rotation.owner_space == 'LOCAL_WITH_PARENT'
    _require(end_rotation.target is rig and end_rotation.subtarget == target.name and (world or direct_space)
        and end_rotation.mix_mode == 'REPLACE' and all(getattr(end_rotation, field) for field in ('use_x', 'use_y', 'use_z'))
        and not any(getattr(end_rotation, field) for field in ('invert_x', 'invert_y', 'invert_z')),
        'The actual wrist end-rotation relation changed.')
    offset_pb, offset, offset_record = by_role['AUTO_OFFSET_ROTATION']
    rotation_space = offset_record.get('rotation_space', 'LOCAL')
    if rotation_space == 'PARENT_DELTA':
        limb._validate_parent_delta_rotation(rig, bone, offset_pb, offset, chain[2])
        _require(world, 'The parent-delta wrist Manual frame changed.')
    else:
        _require(rotation_space == 'LOCAL' and offset.target is rig and offset.subtarget == target.name
            and offset.target_space == offset.owner_space == 'LOCAL' and offset.mix_mode == 'AFTER'
            and all(getattr(offset, field) for field in ('use_x', 'use_y', 'use_z'))
            and not any(getattr(offset, field) for field in ('invert_x', 'invert_y', 'invert_z')),
            'The local wrist rotation offset relation changed.')
    direct_rest = None
    if direct:
        payload = limb.validation_json.decode(rig.data.get(limb.DIRECT_REST_KEY, ''))
        _require(type(payload) is dict and payload.get('version') == limb.DIRECT_PREROLL_RESULT_VERSION
            and type(payload.get('limbs')) is dict, 'The direct wrist Rest registry is invalid.')
        direct_rest = payload['limbs'].get(rig_id)
        _require(type(direct_rest) is dict and direct_rest.get('kind') == 'ARM' and direct_rest.get('side') == side
            and tuple(direct_rest.get('chain', ())) == chain and type(direct_rest.get('applied')) is dict,
            'The selected direct wrist Rest owner is invalid.')
        for name in chain[:2]:
            state = limb._direct_rest_state(direct_rest['applied'].get(name), 'Selected wrist ' + name)
            _require(limb._rest_state_matches(rig.data.bones[name], state), 'The selected native wrist Rest changed after generation.')
    return ('ARM', side), dict(rig_id=rig_id, target=bone, pole=pole, chain=chain, entries=entries,
        solver_target=bone, auto_align=auto, auto_offset_rotation=offset, auto_rotation_space=rotation_space,
        direct_rest=direct_rest, heel=None, pole_direction=None, configured_pole_direction=None, foot_controls=None, **helpers)


def _local_parent(limb, rig, bone):
    """Authenticate this direct control's optional Root parent, not all Root sources."""
    root = limb.root_control
    raw = rig.data.get(root.RECORD_KEY)
    if raw is None:
        return bone.parent is None
    record = limb.validation_json.decode(raw)
    _require(type(record) is dict and record.get('version') == root.VERSION
        and record.get('master') == root.ROOT_NAME and type(record.get('controls')) is list,
        'The local Root parent identity is invalid.')
    if bone.name not in record['controls']:
        return bone.parent is None
    master = rig.data.bones.get(record['master'])
    return root._owned(master, record, 'MASTER') and bone.parent == master


def _graph_stamp(rig, target, end, forearm, key, entry):
    def pointer(value):
        return None if value is None else value.as_pointer() if hasattr(value, 'as_pointer') else id(value)
    def freeze(value):
        if value is None or type(value) in (str, bool, int, float):
            return value
        if type(value) is dict:
            return tuple((name, freeze(item)) for name, item in sorted(value.items()))
        if type(value) in (list, tuple):
            return tuple(freeze(item) for item in value)
        return pointer(value)
    names = set(entry['chain'])
    for field in ('target', 'pole', 'solver_target', 'mch_upper', 'mch_lower', 'ori_upper', 'ori_lower', 'line', 'display', 'heel'):
        bone = entry.get(field)
        if bone is not None:
            names.add(bone.name)
    bones = []
    for name in sorted(names):
        bone = rig.data.bones[name]
        bones.append((name, pointer(bone), pointer(bone.parent), tuple(tuple(float(v) for v in row) for row in bone.matrix_local),
            bone.use_connect, bone.use_inherit_rotation, bone.use_local_location, bone.inherit_scale))
    constraints = []
    for pb, con, record in entry['entries']:
        fields = tuple((field, freeze(getattr(con, field))) for field in
            ('type', 'mute', 'influence', 'target', 'subtarget', 'owner_space', 'target_space', 'mix_mode',
             'pole_target', 'pole_subtarget', 'pole_angle', 'chain_count', 'use_offset', 'use_x', 'use_y', 'use_z',
             'invert_x', 'invert_y', 'invert_z', 'track_axis', 'up_axis') if hasattr(con, field))
        constraints.append((pointer(pb), pointer(con), freeze(record), fields))
    return (key, entry.get('rig_id'), entry['auto_align'], entry.get('auto_rotation_space'), tuple(bones), tuple(constraints))


def _resolve(context, rig, limb, match, target, *, inventory=None, owner=None):
    if owner is None:
        owner = _owner_guard().prepare(context, rig)
    target, end, forearm, inventory, key, entry = _owner_guard().resolve(context, rig, target, owner)
    _require(match.mode_for_rig(rig, entry) == 'IK'
             and abs(float(target.get(match.PROPERTY, 1.)) - 1.) <= 1e-6,
             'This wrist must already be fully IK; its FK/IK mode was kept.')
    _require(not target.constraints, 'The hand target has an unsupported constraint.')
    _rotation_path(target.rotation_mode)
    _require(not any(target.lock_rotation) and not target.lock_rotation_w,
             'Unlock the hand target rotation before resetting the wrist.')
    _require(_same(end.parent, forearm) and _same(entry['solver_target'], target.bone),
             'This wrist uses an unsupported solver or native parent.')
    rotations = [(pb, con, record) for pb, con, record in entry['entries'] if record['role'] == 'END_ROTATION']
    _require(len(rotations) == 1 and _same(rotations[0][0], end), 'The wrist end-rotation owner changed.')
    end_rotation, offset = rotations[0][1], entry.get('auto_offset_rotation')
    _require(offset is not None, 'This wrist needs its existing authenticated rotation offset.')
    owned = [con for pb, con, _record in entry['entries'] if _same(pb, end)]
    _require(all(any(_same(con, expected) for expected in owned) for con in end.constraints),
             'The native hand has an unsupported constraint.')
    if entry['auto_align']:
        _require(entry.get('auto_rotation_space') == 'PARENT_DELTA' and end_rotation.mute
                 and not offset.mute and abs(float(offset.influence) - 1.) <= 1e-6,
                 'Use the authenticated parent-delta Auto wrist contract.')
    else:
        _require(not end_rotation.mute and offset.mute and end_rotation.target_space == 'WORLD'
                 and end_rotation.owner_space == 'WORLD' and abs(float(end_rotation.influence) - 1.) <= 1e-6,
                 'Use the authenticated WORLD Manual wrist contract.')
    paths = {target.path_from_id(prop) for prop in _TRANSFORMS}
    paths.add(match.property_path(target))
    _require(not any(curve.data_path in paths for curve in getattr(rig.animation_data, 'drivers', ())),
             'A driver controls the hand target input; its values were kept.')
    return target, end, forearm, inventory, key, entry


def _admission(context, target=None, *, mirror_allowed=False):
    offset, api, physics = _backend()
    rig = getattr(context, 'object', None)
    _require(rig is not None and rig.type == 'ARMATURE', 'Choose the current local armature.')
    _input_policy(context, rig, mirror_allowed=mirror_allowed)
    # Permission and local current-row/slot ownership, never Source/Action
    # contents or full historical captures, belong to this scoped guard.
    owner = _owner_guard().prepare(context, rig)
    target, end, forearm, inventory, key, entry = _resolve(context, rig, api.limb, api.match, target, owner=owner)
    _require(owner['action'] is not None or not context.scene.tool_settings.use_keyframe_insert_auto,
             'Choose an editable Custom Action before resetting the wrist with Auto Key.')
    return offset, api, physics, rig, target, end, forearm, inventory, key, entry, owner


def _matrix_finite(matrix):
    determinant = matrix.determinant()
    return all(math.isfinite(value) for row in matrix for value in row) and math.isfinite(determinant) and determinant > 1e-12


def _reference(api, rig, end):
    from mathutils import Matrix, Quaternion, Vector
    location = Vector((0., 0., 0.)) if end.bone.use_connect else end.location.copy()
    basis = Matrix.LocRotScale(location, Quaternion(), end.scale.copy())
    wanted = end.bone.convert_local_to_pose(basis, end.bone.matrix_local,
        parent_matrix=end.parent.matrix, parent_matrix_local=end.parent.bone.matrix_local)
    _require(_matrix_finite(wanted), 'The wrist neutral reference is singular, reflected or non-finite.')
    return {end.name: wanted}


def _prepare_single(context, *, target=None, _batch_side=False, _shared=None):
    """Prepare only this target's local rotation and local keying domain."""
    if _shared is None:
        admission = _admission(context, target, mirror_allowed=_batch_side)
        shared = _transaction().prepare(context, admission, mirror_allowed=_batch_side)
    else:
        shared = _shared
        _require(shared['phase'] == 'prepared' and shared['context'] is context, 'Prepare a fresh current wrist transaction.')
    offset, api, rig = shared['offset'], shared['api'], shared['rig']
    target, end, forearm, inventory, key, entry = _resolve(
        context, rig, api.limb, api.match, target, owner=shared['owner'])
    _require(_matrix_finite(rig.matrix_world), 'The armature world transform is singular, reflected or non-finite.')
    path, raw = _rotation_path(target.rotation_mode), _raw_one(target)
    plan = dict(plan_api=PLAN_API, phase='prepared', context=context, api=api, offset=offset,
        transaction=shared, owner=shared['owner'], variant=shared['owner'], batch_side=bool(_batch_side),
        mirror_flags=shared['mirror_flags'], rig=rig, data=rig.data, target=target, target_bone=target.bone,
        end=end, forearm=forearm, key=key, inventory=inventory, limb=entry,
        frame=shared['frame'], autokey=shared['autokey'], action=shared['action'], slot=shared['slot'],
        rotation_mode=target.rotation_mode, rotation_path=path, rotation_size=3 if path == 'rotation_euler' else 4,
        target_raw=raw, before_raw={target.name: raw}, written_rotation=False, had_key_recovery=False,
        context_mode=shared['context_mode'], active=shared['active'], key_rollback=None, key_commit=None)
    _refresh_reference(context, plan)
    _keying().prepare_keying(context, plan)
    _unchanged_inputs(plan, allow_target_rotation=False)
    if not _batch_side:
        _transaction().configure(shared, (plan,))
    return plan


def prepare_wrist_neutral(context, *, target=None, targets=None):
    """Prepare one wrist, or the atomic pair required by selection/mirroring."""
    rig = getattr(context, 'object', None)
    if targets is not None or (rig is not None and getattr(getattr(rig, 'pose', None), 'use_mirror_x', False)):
        from . import limb_ik_wrist_batch
        return limb_ik_wrist_batch.prepare(context, target=target, targets=targets)
    return _prepare_single(context, target=target)


def _unchanged_inputs(plan, *, allow_target_rotation):
    rig, target = plan['rig'], plan['target']
    allowed = plan.get('batch_rotations', {target.name: plan['rotation_path']}) if allow_target_rotation else {}
    for name, expected in plan['before_raw'].items():
        pb = rig.pose.bones.get(name)
        _require(pb is not None, 'A written wrist target disappeared.')
        current = _raw_one(pb)
        _require(all(current[field] == value for field, value in expected.items() if field != allowed.get(name)),
                 'The wrist target input changed; the reset was cancelled.')


def _validate_plan(context, plan, *, _batch=False, _shared_checked=False):
    _require(type(plan) is dict and plan.get('plan_api') == PLAN_API and plan.get('phase') == 'prepared'
             and plan.get('context') is context, 'Prepare this exact current wrist before resetting it.')
    _require(not plan.get('batch_side') or _batch, 'Apply this wrist through its complete prepared pair.')
    shared = plan.get('transaction')
    _require(type(shared) is dict, 'Reprepare this wrist with the current local transaction service.')
    if not _shared_checked:
        _transaction().validate(context, shared)
    _require(shared['phase'] == 'validated', 'The local wrist transaction was not validated.')
    api, rig = shared['api'], shared['rig']
    target, end, forearm, _inventory, key, entry = _resolve(context, rig, api.limb, api.match,
        plan['target'], owner=shared['owner'])
    _require(rig is plan['rig'] and rig.data is plan['data'] and _same(target, plan['target'])
             and _same(target.bone, plan['target_bone']) and _same(end, plan['end'])
             and _same(forearm, plan['forearm']) and key == plan['key'],
             'The authenticated wrist or native parent changed after preparation.')
    _unchanged_inputs(plan, allow_target_rotation=False)
    _keying().validate_keying(context, plan)
    plan['limb'] = entry
    # Unwritten animation/Source edits are allowed. Recompute the current-frame
    # neutral reference without replaying any earlier FK or unrelated inputs.
    _refresh_reference(context, plan)


def _rotation_error(actual, expected):
    delta = actual.to_quaternion().normalized().conjugated() @ expected.to_quaternion().normalized()
    return 2*math.atan2(math.hypot(delta.x, delta.y, delta.z), abs(delta.w))


def _assign_rotation(target, rotation):
    mode = target.rotation_mode
    if mode == 'QUATERNION':
        if rotation.dot(target.rotation_quaternion) < 0:
            rotation.negate()
        target.rotation_quaternion = rotation
    elif mode == 'AXIS_ANGLE':
        axis, angle = rotation.to_axis_angle()
        target.rotation_axis_angle = (angle, *axis)
    else:
        target.rotation_euler = rotation.to_euler(mode, target.rotation_euler)


def _solve(context, plan):
    from mathutils import Matrix
    rig, target, end = plan['rig'], plan['target'], plan['end']
    desired = plan['wanted_native'][end.name]
    for iteration in range(MAX_ITERATIONS):
        _require(target.rotation_mode == plan['rotation_mode'], 'The wrist rotation representation changed during matching.')
        current = end.matrix.copy()
        _require(_matrix_finite(current) and _matrix_finite(target.matrix), 'The current wrist transform is invalid.')
        if _rotation_error(current, desired) <= 1e-6:
            return iteration
        delta = desired.to_quaternion().normalized() @ current.to_quaternion().normalized().inverted()
        posed = target.matrix.copy()
        wanted = Matrix.LocRotScale(posed.translation,
            (delta @ posed.to_quaternion().normalized()).normalized(), posed.to_scale())
        local = rig.convert_space(pose_bone=target, matrix=wanted, from_space='POSE', to_space='LOCAL')
        _require(_matrix_finite(local), 'The compensated target rotation cannot be represented safely.')
        plan['written_rotation'] = True
        _assign_rotation(target, local.to_quaternion())
        _fault('after_rotation_write')
        plan['api'].match._update(context, rig)
    return MAX_ITERATIONS


def _verify(context, plan):
    rig = plan['rig']
    _transaction()._current_context(context, plan['transaction'])
    _unchanged_inputs(plan, allow_target_rotation=True)
    _require(_difference(rig.matrix_world, plan['world_matrix']) <= 1e-7,
             'The armature world transform changed during wrist matching.')
    measurements = dict(position=0., rotation=0., scale=0., matrix=0.)
    wanted, current = plan['wanted_native'][plan['end'].name], plan['end'].matrix
    _require(_matrix_finite(current), 'The evaluated wrist pose is invalid.')
    measurements['matrix'] = _difference(current, wanted)
    actual_world, wanted_world = rig.matrix_world @ current, rig.matrix_world @ wanted
    _require(_matrix_finite(actual_world) and _matrix_finite(wanted_world), 'The world wrist pose is invalid.')
    measurements['position'] = math.hypot(*(a-b for a, b in zip(actual_world.translation, wanted_world.translation)))
    measurements['rotation'] = _rotation_error(actual_world, wanted_world)
    measurements['scale'] = math.hypot(*(a-b for a, b in zip(actual_world.to_scale(), wanted_world.to_scale())))
    _require(all(math.isfinite(value) for value in measurements.values())
             and measurements['matrix'] <= MATRIX_TOLERANCE and measurements['position'] <= POSITION_TOLERANCE
             and measurements['rotation'] <= ROTATION_TOLERANCE and measurements['scale'] <= SCALE_TOLERANCE,
             'The wrist cannot reach native FK neutral without another change; its original input was kept.')
    _require((plan['target'].matrix.translation - plan['target_position']).length <= POSITION_TOLERANCE
             and _difference(plan['forearm'].matrix, plan['forearm_matrix']) <= MATRIX_TOLERANCE
             and _difference(rig.pose.bones[plan['limb']['pole'].name].matrix, plan['pole_matrix']) <= MATRIX_TOLERANCE,
             'The wrist reset moved its target, forearm or Pole.')
    return measurements


def _rollback(context, plan):
    errors = []
    try:
        if plan.get('key_rollback') is not None:
            plan['had_key_recovery'] = True
            plan['transaction']['had_key_recovery'] = True
            plan['key_rollback']()
    except Exception as error:
        errors.append('keys: ' + str(error))
    try:
        _transaction().restore_rotations(context, plan['transaction'])
    except Exception as error:
        errors.append('rotation: ' + str(error))
    try:
        _transaction().verify_recovery(context, plan['transaction'])
    except Exception as error:
        errors.append('local integrity: ' + str(error))
    plan['phase'] = 'recovery_needed' if errors else 'rolled_back'
    _require(not errors, 'Owned wrist recovery needs review: ' + '; '.join(errors))


def apply_wrist_neutral(context, plan):
    if type(plan) is dict and plan.get('batch_api') is not None:
        from . import limb_ik_wrist_batch
        return limb_ik_wrist_batch.apply(context, plan)
    _validate_plan(context, plan)
    _transaction().begin(plan['transaction'])
    plan['phase'] = 'applying'
    try:
        iterations = _solve(context, plan)
        errors = _verify(context, plan)
        _fault('after_target_verification')
        keyed = _key_rotation(context, plan)
        _require(type(keyed) is bool and keyed == plan['autokey'], 'Wrist keying did not confirm the requested Auto Key policy.')
        _fault('after_key_rotation')
        errors = _verify(context, plan)
        _transaction().finish(context, plan['transaction'])
        if plan.get('key_commit') is not None:
            plan['key_commit']()
        _transaction().commit(plan['transaction'])
        plan['phase'] = 'applied'
        return dict(target=plan['target'].name, hand=plan['end'].name, key=plan['key'],
                    auto_align=bool(plan['limb']['auto_align']), keyed=keyed,
                    iterations=iterations, errors=errors, phase='applied')
    except Exception as error:
        try:
            _rollback(context, plan)
        except Exception as restore_error:
            raise RuntimeError(str(restore_error)) from error
        raise


def reset_wrist_neutral(context, *, target=None, targets=None):
    return apply_wrist_neutral(context, prepare_wrist_neutral(context, target=target, targets=targets))
