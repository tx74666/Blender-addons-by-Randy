"""Keep the fitted sole outline in the coordinate system of its editable IK target."""
import bpy

KEY = 'character_designer_sole_target_frame_v1'


def current(bone):
    return bone.get(KEY) in (1, 2, 3)


def neutral_ready(bone):
    return bone.get(KEY) in (2, 3)


def valid_frame(target):
    donor = target.custom_shape_transform
    if target.bone.get(KEY) != 3:
        return donor is None
    from . import foot_controls as feet, limb_ik as limb
    return bool(donor and donor.bone.get(feet.OWNER_KEY) == feet.OWNER_VALUE
                and donor.bone.get(feet.ROLE_KEY) == 'SOLE_DISPLAY'
                and donor.bone.get(feet.SIDE_KEY) == target.bone.get(limb.SIDE_KEY))


def capture(target):
    from . import limb_ik as limb
    donor = target.custom_shape_transform
    return (donor.name if donor else None, limb._control_visual_state(target),
            target.bone.get(limb.CONTROL_VISUAL_DEFAULT_KEY), target.bone.get(KEY))


def restore(target, saved):
    from . import limb_ik as limb
    donor, state, default, marker = saved
    target.custom_shape_transform = target.id_data.pose.bones[donor] if donor else None
    limb._apply_control_visual_state(target, state)
    for field, value in ((limb.CONTROL_VISUAL_DEFAULT_KEY, default), (KEY, marker)):
        if value is None:
            target.bone.pop(field, None)
        else:
            target.bone[field] = value


def _guard_offsets(target):
    from . import limb_ik as limb
    fields = ('custom_shape_translation', 'custom_shape_rotation_euler', 'custom_shape_scale_xyz',
              'custom_shape_transform', 'use_custom_shape_bone_size')
    paths = {target.path_from_id(field) for field in fields}
    animation = target.id_data.animation_data
    if (any(f.data_path in paths for action in bpy.data.actions for f in limb._fcurves_for_action(action))
            or animation and any(f.data_path in paths for f in animation.drivers)):
        raise limb.LimbIKError(f'{target.name} has animated display offsets. Keep its current outline.')


def anchor(target, end):
    from . import limb_ik as limb
    donor = target.custom_shape_transform
    if target.bone.get(KEY) == 3 and valid_frame(target):
        return
    if donor is not None and donor != end:
        raise limb.LimbIKError(f'{target.name} uses another display reference. Keep that custom setup.')
    if donor is not None:
        _guard_offsets(target)
        # A cleared target uses the fitted Rest reference. Evaluated ankle
        # pitch from the migration frame belongs exclusively to that pose.
        limb._retarget_custom_shape_frame(target, None,
                                          current_reference_matrix=donor.bone.matrix_local,
                                          previous_reference_matrix=target.bone.matrix_local)
    if donor is not None or not current(target.bone):
        target.bone[KEY] = 2


def _level_in_rest(target):
    """Recover a flat neutral plane for old fits that only stored the posed frame."""
    from mathutils import Vector
    from . import floor_widgets, limb_ik as limb
    shape = target.custom_shape
    if not shape or shape.type != 'MESH' or shape.modifiers or shape.data.shape_keys:
        raise limb.LimbIKError(f'{target.name} needs a planar mesh outline to level its neutral fit.')
    plane = floor_widgets._plane(target)
    if plane is None:
        raise limb.LimbIKError(f'{target.name} uses a nonplanar outline. Keep its custom fit.')
    # Rotate around the rest ankle, in rig space. A lifted or rotated
    # character is never snapped to the scene floor.
    rest = target.bone.matrix_local.to_3x3()
    normal = (rest @ target.custom_shape_rotation_euler.to_matrix() @ Vector(plane['normal'])).normalized()
    up = Vector((0., 0., 1. if normal.z >= 0. else -1.))
    swing = normal.rotation_difference(up).to_matrix()
    return (rest.inverted() @ swing @ rest).to_4x4()


def _correct_fit(target, correction):
    from . import limb_ik as limb
    _guard_offsets(target)
    saved = target.bone.get(limb.CONTROL_VISUAL_DEFAULT_KEY)
    def corrected(state):
        matrix = correction @ limb._custom_shape_state_matrix(state)
        return limb._custom_shape_state_from_matrix(matrix, state['rotation'], target.name)
    fit = corrected(limb._control_visual_state(target))
    default = corrected(limb._parse_control_visual_state(saved, target.name)) if saved is not None else None
    limb._apply_control_visual_state(target, fit)
    if default is not None:
        target.bone[limb.CONTROL_VISUAL_DEFAULT_KEY] = limb._canonical_json(default)
    target.bone[KEY] = 2


def anchor_existing(context, target, end, *, repair=False, correction=None):
    """Correct only the display and protect all authored pose inputs on reevaluation."""
    if current(target.bone) and target.custom_shape_transform is None:
        if neutral_ready(target.bone) or not repair:
            return
    from . import foot_controls as feet, foot_roll_hinge as hinge
    rig = target.id_data
    names = tuple(rig.pose.bones.keys())
    inputs = hinge._held_inputs(rig, names)
    pose = {p.name: p.matrix.copy() for p in rig.pose.bones}
    saved = capture(target)
    try:
        if target.bone.get(KEY) == 1 and target.custom_shape_transform is None:
            _correct_fit(target, correction if correction is not None else _level_in_rest(target))
        else:
            anchor(target, end)
        feet._update(context, rig)
        hinge._restore_held_inputs(context, rig, inputs, names)
        feet._verify_pose(rig, pose)
    except Exception:
        restore(target, saved)
        feet._update(context, rig)
        hinge._restore_held_inputs(context, rig, inputs, names)
        feet._verify_pose(rig, pose)
        raise
