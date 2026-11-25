"""Authenticate the current native limb pose and its editable control inputs."""
from . import animation_physics, body_pose_reset, limb_ik, limb_ik_fk
from . import limb_ik_wrist_owner_guard, limb_ik_wrist_rotation, weight_paint_entry


def _check_locks(bone, *, location=False, rotation=False, scale=False):
    if (location and any(bone.lock_location) or scale and any(bone.lock_scale)
            or rotation and (any(bone.lock_rotation) or bone.lock_rotations_4d
                            and bone.rotation_mode in {'AXIS_ANGLE', 'QUATERNION'} and bone.lock_rotation_w)):
        raise ValueError(f'{bone.name}: a transform needed for the bind pose is locked.')


def _validate_entry(rig, entry, owned):
    chain = entry['chain']
    armature = rig.data
    target, pole = entry['target'], entry['pole']
    for parent, child in zip(chain, chain[1:]):
        if armature.bones[child].parent != armature.bones[parent]:
            raise ValueError(f'{child}: the native limb ancestry changed.')
    foot = entry.get('foot_controls')
    allowed_drivers = set()
    checked = set(chain) | {target.name, pole.name}
    if foot:
        toe = armature.bones.get(foot['toe'])
        if (toe is None or toe.parent != armature.bones[chain[-1]]
                or foot['chain'] != list(chain) or foot['target'] != target.name
                or foot['toe_control'] != foot['bones']['TOE_BEND']
                or foot['roll'] != foot['bones']['FOOT_ROLL']
                or foot['solver'] != foot['bones']['ANKLE_SOLVER']):
            raise ValueError('Foot Controls does not resolve to its current foot and toe.')
        toe_relation = rig.pose.bones[toe.name].constraints.get('CD Toe Bend')
        if (toe_relation is None or toe_relation.type != 'COPY_TRANSFORMS' or toe_relation.target != rig
                or toe_relation.subtarget != foot['toe_control']):
            raise ValueError(f'{toe.name}: the Toe Bend constraint changed.')
        checked.update(foot['bones'].values())
        checked.add(toe.name)
        owned.update((item['owner'], item['name']) for item in foot['constraints'])
        allowed_drivers = {(item['path'], item['index']) for item in foot['drivers']}
        _check_locks(rig.pose.bones[foot['toe_control']], rotation=True)
    for name in (*limb_ik_fk.pose_names(entry), target.name):
        _check_locks(rig.pose.bones[name], location=True, rotation=True, scale=True)
    _check_locks(rig.pose.bones[pole.name], location=True)
    relations = []
    for name in checked:
        for constraint in rig.pose.bones[name].constraints:
            if (name, constraint.name) not in owned:
                raise ValueError(f'{name}: another constraint affects this limb.')
            relations.append(constraint.path_from_id() + '.')
    # The inventory has already verified each generated driver. A different
    # driver on that same constraint (pole angle, space, mute...) is independent.
    allowed_drivers.update((constraint.path_from_id('influence'), 0)
                           for bone, constraint, record in entry['entries']
                           if limb_ik_fk.is_switch_constraint(rig, bone, constraint, record))
    relation_paths = tuple(relations)
    if any(curve.data_path.startswith(relation_paths) and (curve.data_path, curve.array_index) not in allowed_drivers
           for curve in getattr(rig.animation_data, 'drivers', ())):
        raise ValueError('An artist driver controls a constraint on this limb.')
    body_pose_reset._guard_drivers(rig, checked, allowed_drivers)


def prepare(context, rig, keys=None):
    """Return registered limb entries for a synchronous, unkeyed pose edit."""
    if context.mode != 'POSE' or context.object != rig or any(obj != rig for obj in context.objects_in_mode):
        raise ValueError('Select the controls of one character in Pose Mode.')
    if rig.data.pose_position != 'POSE' or context.scene.tool_settings.use_keyframe_insert_auto:
        raise ValueError('Use Pose Position with Auto Key off to restore the limb bind pose.')
    if weight_paint_entry.owns_rig(rig) or weight_paint_entry.native_painting(context):
        raise ValueError('Return to Controls before restoring the limb bind pose.')
    animation = rig.animation_data
    if animation and animation.action is None and (
            animation.use_tweak_mode or animation.use_nla and any(not track.mute and track.strips
                                                                  for track in animation.nla_tracks)):
        raise ValueError('Finish NLA editing before restoring the limb bind pose.')
    limb_ik_wrist_owner_guard.prepare(context, rig)
    limb_ik_wrist_rotation._physics_idle(context, rig, animation_physics)
    inventory = limb_ik._validate_inventory(rig, limb_pose_only=True)
    entries = inventory['rigs']
    scope = set(entries) if keys is None else set(keys)
    if not scope or not scope.issubset(entries):
        raise ValueError('The selected limbs are missing from this rig.')
    inventory = dict(inventory, rigs={key: entry for key, entry in entries.items() if key in scope})
    limb_ik_fk._require_existing_switches(rig, inventory)
    owned = {(bone.name, constraint.name) for bone, constraint, _record in inventory['records']}
    from . import natural_bind_graph
    if natural_bind_graph.KEY in rig:
        rec = natural_bind_graph.validate(rig)
        owned.update((name, natural_bind_graph.COPY) for name in rec['frames'])
    for entry in inventory['rigs'].values():
        _validate_entry(rig, entry, owned)
    return inventory


def switch_neutral(context, rig, mode, *, key=None, keyframe=False):
    """Keep an unkeyed bind pose while changing which limb controls are displayed."""
    from . import natural_bind_graph as graph, natural_bind_mode as preview
    if (keyframe or context.mode != 'POSE' or context.scene.tool_settings.use_keyframe_insert_auto
            or rig is None or rig.type != 'ARMATURE' or graph.KEY not in rig or preview.KEY not in rig):
        return None
    record = rig[preview.KEY]
    if not preview._match(context.scene, rig, record):
        return None
    binding = graph.validate(rig)
    if not binding['enabled'] or record['token'] != binding['token']:
        return None
    from . import bone_collections, character_setup, natural_limb_solve
    import bpy
    if (character_setup.preferred_rig(context) not in (None, rig)
            or sum(s.objects.get(rig.name) == rig for s in bpy.data.scenes) != 1):
        raise ValueError('Choose the current scene’s character to switch IK/FK.')
    # A recovery Action can point to Armature data without binding an Object to it.
    # Display changes are safe only for the single Object binding, including unlinked objects.
    users = bpy.data.user_map(subset={rig.data}).get(rig.data, set())
    if (rig not in users or rig.user_of_id(rig.data) != 1
            or any(not isinstance(user, bpy.types.Action) for user in users if user != rig)
            or sum(user.user_of_id(rig.data) for user in users) + int(rig.data.use_fake_user) != rig.data.users):
        raise ValueError('The Armature data has another user. Make it single-user before switching IK/FK.')
    inventory = prepare(context, rig)
    if mode not in {'IK', 'FK'} or (key is not None and key not in inventory['rigs']):
        raise ValueError('Choose an existing limb and IK or FK.')
    native = {n for entry in inventory['rigs'].values() for n in limb_ik_fk.pose_names(entry)}
    held = {n: tuple(rig.pose.bones[n].scale) for n in native}
    try:
        graph._require_neutral(rig, inventory, held_scales=held)
        natural_limb_solve.verify_bind(rig, inventory)
    except ValueError:
        return None
    selected = (key,) if key is not None else tuple(inventory['rigs'])
    targets = [rig.pose.bones[inventory['rigs'][k]['target'].name] for k in selected]
    before = [(p, p[limb_ik_fk.PROPERTY]) for p in targets]
    value = float(mode == 'IK')
    try:
        for p, previous in before:
            if previous != value:
                p[limb_ik_fk.PROPERTY] = value
        limb_ik_fk._update(context, rig)
        natural_limb_solve.verify_bind(rig, inventory)
        bone_collections.sync_limb_display(rig, inventory, keys=selected, force_flags=True)
    except Exception:
        for p, previous in before:
            if p[limb_ik_fk.PROPERTY] != previous:
                p[limb_ik_fk.PROPERTY] = previous
        limb_ik_fk._update(context, rig)
        raise
    return {'mode': mode, 'keyed': False}
