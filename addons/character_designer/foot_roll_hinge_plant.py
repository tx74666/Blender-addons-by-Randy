"""Rotate the current unrolled Auto foot about its actual forefoot."""
from contextlib import contextmanager

from . import foot_controls as feet


def new_record():
    return {'bones': {}, 'bone_states': {}, 'constraints': [], 'drivers': []}


@contextmanager
def editing(context, rig):
    from . import natural_stable_solver
    mirror = rig.data.use_mirror_x
    rig.data.use_mirror_x = False
    try:
        if context.object == rig:
            limb = feet._limb()
            saved = limb._capture_context(context, rig)
            try:
                limb._mode_set(context, rig, 'EDIT')
                yield rig.data.edit_bones
            finally:
                limb._restore_context(context, rig, saved)
        else:
            with natural_stable_solver._editing_copy(context, rig) as bones:
                yield bones
    finally:
        rig.data.use_mirror_x = mirror


def create(eb, added, role, name, src_bone, parent_name=None):
    if eb.get(name):
        raise feet._error(f'An existing bone already uses {name}.')
    bone = eb.new(name)
    added['bones'][role] = bone.name
    bone.head, bone.tail, bone.roll = src_bone.head.copy(), src_bone.tail.copy(), src_bone.roll
    bone.inherit_scale = src_bone.inherit_scale
    bone.use_inherit_rotation = src_bone.use_inherit_rotation
    bone.use_local_location = src_bone.use_local_location
    bone.use_deform = False
    bone.parent = eb[parent_name] if parent_name else None
    return bone


def register(rig, record, added, role):
    pb = rig.pose.bones[added['bones'][role]]
    bone = pb.bone
    feet._tag(bone, record, role)
    groups = list(rig.data.bones[record['solver']].collections)
    for group in list(bone.collections):
        if group not in groups:
            group.unassign(bone)
    for group in groups:
        group.assign(bone)
    bone.hide = bone.hide_select = True
    pb.lock_rotation = pb.lock_location = pb.lock_scale = (True, True, True)
    added['bone_states'][role] = dict(head=list(bone.head_local), tail=list(bone.tail_local),
                                      matrix=feet._matrix(bone.matrix_local),
                                      parent=bone.parent.name if bone.parent else None)
    return pb


def merge(record, added):
    for key in ('bones', 'bone_states'):
        record[key].update(added[key])
    for key in ('constraints', 'drivers'):
        record[key].extend(added[key])


def rollback(context, rig, added):
    ad = rig.animation_data
    for row in reversed(added['drivers']):
        curve = ad.drivers.find(row['path'], index=row['index']) if ad else None
        if curve:
            ad.drivers.remove(curve)
    for row in reversed(added['constraints']):
        pb = rig.pose.bones.get(row['owner'])
        con = pb.constraints.get(row['name']) if pb else None
        if con:
            pb.constraints.remove(con)
    if added['bones']:
        with editing(context, rig) as eb:
            for name in reversed(tuple(added['bones'].values())):
                bone = eb.get(name)
                if bone:
                    eb.remove(bone)


def _plant(context, rig, record, added, neutral_foot, neutral_toe, input_rig):
    from . import limb_ik_fk, natural_bind_graph
    side = record['side']
    frames = [('HEEL_RISE_ANCHOR', 'MCH_heel_anchor.' + side),
              ('HEEL_RISE_TURN', 'MCH_heel_turn.' + side),
              ('HEEL_RISE_OUTPUT', 'MCH_heel_foot.' + side)]
    with editing(context, rig) as eb:
        roll = eb[record['roll']]
        parent = roll.parent.name if roll.parent else None
        for role, name in frames:
            create(eb, added, role, name, roll, parent)
            parent = name
        create(eb, added, 'HEEL_RISE_GOAL', 'MCH_heel_goal.' + side, roll)
    anchor, turn, out = (register(rig, record, added, role) for role, _ in frames)
    goal = register(rig, record, added, 'HEEL_RISE_GOAL')
    turn.rotation_mode = 'XYZ'
    c = feet._add_constraint(added, anchor, 'COPY_LOCATION', 'CD Heel Lift Center', rig,
                             subtarget=neutral_toe, owner_space='POSE', target_space='POSE')
    raw = rig.pose.bones[record['roll']].path_from_id('rotation_euler') + '[0]'
    added['drivers'].append(feet._driver(rig, turn, 'rotation_euler', 0, 'min(roll,0)', {'roll': raw}))
    c = feet._add_constraint(added, out, 'COPY_TRANSFORMS', 'CD Heel Lift Rotated Foot', rig,
                             subtarget=neutral_foot, target_space='CUSTOM', space_subtarget=anchor.name,
                             owner_space='LOCAL', mix_mode='REPLACE')
    c.space_object = rig
    added['constraints'][-1]['custom_space'] = True
    # A blended or unreachable ankle is not located at its unrolled IK target.
    # Move that target by the requested heel displacement. Replacing its
    # absolute position with the blended native ankle would pop at zero.
    for name, source, offset, invert in (
            ('CD Heel Lift Base Target', added['bones']['UNROLLED_ANKLE'], False, False),
            ('CD Heel Lift Raised Ankle', out.name, True, False),
            ('CD Heel Lift Base Ankle', neutral_foot, True, True)):
        feet._add_constraint(added, goal, 'COPY_LOCATION', name, rig,
                             subtarget=source, target_space='POSE', owner_space='POSE',
                             use_offset=offset, invert_x=invert, invert_y=invert, invert_z=invert)
        if not offset:
            # An unreachable straight leg already uses the nearest endpoint
            # of its two-bone chain. Apply the displacement there so a lift
            # takes effect immediately, even with the raw target below ground.
            upper = rig.pose.bones[added['bones']['UNROLLED_UPPER']]
            lower = rig.pose.bones[added['bones']['UNROLLED_LOWER']]
            limit = feet._add_constraint(added, goal, 'LIMIT_DISTANCE', 'CD Heel Lift Reach', rig,
                                         subtarget=upper.name, target_space='POSE', owner_space='POSE',
                                         limit_mode='LIMITDIST_INSIDE')
            added['drivers'].append(feet._driver(rig, limit, 'distance', -1, 'upper+lower',
                                                 {'upper': upper.path_from_id('length'),
                                                  'lower': lower.path_from_id('length')}))
    target = input_rig.pose.bones[record['target']]
    inputs = {'roll': raw, 'ik_fk': limb_ik_fk.property_path(target),
              'auto': 'data.' + target.bone.path_from_id() + '["' + feet._limb().AUTO_ALIGN_KEY + '"]'}
    for owner, kind, source in ((record['solver'], 'COPY_LOCATION', goal.name),
                                (record['chain'][2], 'COPY_ROTATION', out.name),
                                (record['bones']['TOE_SPACE'], 'COPY_TRANSFORMS', neutral_toe)):
        pb = rig.pose.bones[owner]
        c = feet._add_constraint(added, pb, kind, 'CD Heel Lift Plant', rig,
                                 subtarget=source, target_space='POSE', owner_space='POSE')
        # The public bind outputs have their own fully evaluated private limbs.
        # Keep their final blend last in the public stack.
        index = pb.constraints.find(natural_bind_graph.COPY)
        if index >= 0:
            pb.constraints.move(pb.constraints.find(c.name), index)
        row = feet._driver(rig, c, 'influence', -1, 'ik_fk * auto if roll < 0 else 0', inputs)
        added['drivers'].append(row)
        if input_rig != rig:
            curve = rig.animation_data.drivers.find(row['path'], index=row['index'])
            for var in curve.driver.variables:
                if var.name != 'roll':
                    var.targets[0].id = input_rig


def install(context, rig, record, added, *, input_rig=None):
    from . import foot_roll_hinge_reference
    if record.get('auto_follow') != 1:
        return
    foot, toe = foot_roll_hinge_reference.build(context, rig, record, added)
    _plant(context, rig, record, added, foot, toe, input_rig or rig)
