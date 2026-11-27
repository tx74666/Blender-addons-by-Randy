"""Left-hand grip on the existing wrist target, or native two-bone IK for a plain rig."""

import bpy

from . import animation_weapon_profiles as profiles
from . import animation_composition_weapon as display

WEIGHT = 'character_designer_weapon_grip_weight'
PATH = '["' + WEIGHT + '"]'
BINDING = 'local_grip_constraints'


def constraints(rig, record):
    result = []
    for bone_name, constraint_name in record.get(BINDING, {}).items():
        bone = rig.pose.bones.get(bone_name)
        constraint = bone.constraints.get(constraint_name) if bone is not None else None
        if constraint is None or constraint.target != record['grip']:
            raise ValueError('The saved Left Grip constraint changed. Recover the binding before editing it.')
        result.append((bone, constraint))
    return result


def _add(rig, bone, kind, target, label):
    constraint = bone.constraints.new(kind)
    try:
        constraint.name = label
        constraint.target = target
        constraint.target_space = constraint.owner_space = 'WORLD'
        if kind == 'IK':
            constraint.chain_count = 2
            constraint.use_stretch = False
        driver = constraint.driver_add('influence').driver
        driver.type = 'AVERAGE'
        var = driver.variables.new()
        var.name, var.type = 'grip', 'SINGLE_PROP'
        var.targets[0].id, var.targets[0].data_path = rig, PATH
        return constraint
    except Exception:
        constraint.driver_remove('influence')
        bone.constraints.remove(constraint)
        raise


def enable(context, rig):
    from . import limb_ik

    profiles.require_idle(context, rig)
    record = profiles.attachment(rig)
    if record is None or not record[profiles.LOCAL].get('two_handed'):
        raise ValueError('Attach a two-handed Weapon Profile to enable Left Grip.')
    display.validate_owned(rig)
    if record.get(BINDING):
        return
    inventory = limb_ik._validate_inventory(rig, limb_pose_only=True)
    left = inventory['rigs'].get(('ARM', 'L'))
    chain = profiles.arm_chain(rig, 'L')
    if chain is None:
        raise ValueError('Left Grip needs an identifiable left upper arm, forearm and hand.')
    if record['hand'] in {*chain.names, *(b.name for b in rig.data.bones[chain.upper].children_recursive)}:
        raise ValueError('Mount the weapon on the right hand before enabling Left Grip.')
    if left:
        bone = rig.pose.bones[left['target'].name]
        if bone.name == record['hand']:
            raise ValueError('The weapon mount must be independent of the left wrist target.')
        if bone.constraints:
            raise ValueError('The left wrist target already has input constraints.')
        specs = ((bone, 'COPY_TRANSFORMS', 'Weapon · Left Wrist Target'),)
    else:
        upper, forearm, hand = (rig.pose.bones[name] for name in chain.names)
        if any(bone.constraints for bone in (upper, forearm, hand)):
            raise ValueError('The left arm already has constraints. Use its Character Designer IK target.')
        specs = ((forearm, 'IK', 'Weapon · Left Arm IK'),
                 (hand, 'COPY_ROTATION', 'Weapon · Left Wrist Rotation'))
    if WEIGHT in rig and type(rig[WEIGHT]) is not float:
        raise ValueError('The Left Grip weight property has an incompatible custom value.')
    created_property = WEIGHT not in rig
    made = []
    try:
        if created_property:
            rig[WEIGHT] = 0.0
            rig.id_properties_ui(WEIGHT).update(min=0., max=1., default=0.,
                                                description='Blend the left hand to its weapon grip')
        for bone, kind, label in specs:
            made.append((bone, _add(rig, bone, kind, record['grip'], label)))
        record[BINDING] = {bone.name: con.name for bone, con in made}
        rig.update_tag(refresh={'OBJECT'})
        context.view_layer.update()
    except Exception:
        for bone, constraint in reversed(made):
            constraint.driver_remove('influence')
            bone.constraints.remove(constraint)
        record.pop(BINDING, None)
        if created_property:
            del rig[WEIGHT]
        raise


def disable(rig, record):
    bindings = constraints(rig, record)
    for bone, constraint in bindings:
        constraint.driver_remove('influence')
        bone.constraints.remove(constraint)
    if bindings:
        del record[BINDING]


def overlay_values(rig, weight):
    from . import limb_ik_fk

    record = profiles.attachment(rig)
    bindings = constraints(rig, record) if record is not None else []
    if not bindings or not record[profiles.LOCAL].get('two_handed'):
        raise ValueError('Enable Left Grip on a two-handed profile before animating its weight.')
    values = {PATH: weight}
    bone, constraint = bindings[0]
    if weight > 0 and constraint.type == 'COPY_TRANSFORMS' and limb_ik_fk.PROPERTY in bone:
        values[limb_ik_fk.property_path(bone)] = 1.0
    return values
