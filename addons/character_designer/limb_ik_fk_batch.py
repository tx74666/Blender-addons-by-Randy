"""Atomic body IK/FK switches using the existing native bones as FK inputs.

The daily switch changes all installed arm/leg chains. Individual chains remain
available to Advanced callers via ``keys``. Hair and Dress keep their own mode;
leaving an explicitly active Original workspace uses its existing pose transfer.
"""
from __future__ import annotations

import bpy

from . import limb_ik, limb_ik_fk as match


LIMB_KEYS = (('ARM', 'L'), ('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R'))
_CHANNELS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')
_LOCKS = ('lock_rotation', 'lock_rotation_w', 'lock_rotations_4d')


def _keys(inventory, keys=None):
    requested = tuple(inventory['rigs']) if keys is None else tuple(keys)
    if not requested or len(set(requested)) != len(requested):
        raise limb_ik.LimbIKError('Choose at least one distinct installed arm or leg.')
    if any(key not in LIMB_KEYS or key not in inventory['rigs'] for key in requested):
        raise limb_ik.LimbIKError('Build the selected arm and leg controls before switching IK/FK.')
    return tuple(key for key in LIMB_KEYS if key in requested)


def mode_for_keys(armature, inventory=None, keys=None):
    """Return IK, FK or MIXED without changing the rig or its display."""
    from . import body_original_mode
    if body_original_mode.active(armature):
        return 'FK'
    inventory = inventory if inventory is not None else limb_ik._validate_inventory(armature)
    states = {match.mode_for_rig(armature, inventory['rigs'][key]) for key in _keys(inventory, keys)}
    return states.pop() if len(states) == 1 and 'BLEND' not in states else 'MIXED'


def _copy_value(value):
    if isinstance(value, bpy.types.ID):
        return value
    if hasattr(value, 'items'):
        return {key: _copy_value(item) for key, item in value.items()}
    if hasattr(value, 'to_list'):
        return [_copy_value(item) for item in value.to_list()]
    if isinstance(value, (list, tuple)):
        return [_copy_value(item) for item in value]
    return value


def _properties(owner):
    return {key: _copy_value(owner[key]) for key in owner.keys()}


def _restore_properties(owner, saved):
    for key in tuple(owner.keys()):
        if key not in saved:
            del owner[key]
    for key, value in saved.items():
        if key not in owner or _copy_value(owner[key]) != value:
            owner[key] = value


def _pose_checkpoint(armature):
    return {pb.name: {'mode': pb.rotation_mode,
                     'channels': {prop: tuple(getattr(pb, prop)) for prop in _CHANNELS},
                     'locks': {prop: tuple(value) if hasattr(value, '__len__') else value
                               for prop in _LOCKS for value in (getattr(pb, prop),)},
                     'basis': pb.matrix_basis.copy(), 'properties': _properties(pb)}
            for pb in armature.pose.bones}


def _restore_pose(armature, saved):
    for name, entry in saved.items():
        pb = armature.pose.bones[name]
        if pb.rotation_mode != entry['mode']:
            pb.rotation_mode = entry['mode']
        for prop, values in entry['channels'].items():
            if tuple(getattr(pb, prop)) != values:
                setattr(pb, prop, values)
        for prop, value in entry['locks'].items():
            current = getattr(pb, prop)
            current = tuple(current) if hasattr(current, '__len__') else current
            if current != value:
                setattr(pb, prop, value)
        _restore_properties(pb, entry['properties'])


def _checkpoint(context, armature, saved_original):
    from . import body_original_mode as original, body_rest_resync, bone_display, skirt_original_mode
    rigs = [armature]
    if saved_original is not None:
        rigs.extend(target for target, _view in original._extra_views(armature, saved_original))
    rigs = tuple(dict.fromkeys(rigs))
    states = []
    for rig in rigs:
        constraints = [(con, con.mute, con.influence,
                        con.pole_angle if con.type == 'IK' else None)
                       for pb in rig.pose.bones for con in pb.constraints]
        states.append({'rig': rig, 'channels': _pose_checkpoint(rig), 'constraints': constraints,
                       'display': bone_display._snapshot(rig),
                       'object_properties': _properties(rig) if saved_original is not None else None,
                       'data_properties': _properties(rig.data) if saved_original is not None else None,
                       'bone_properties': {bone.name: _properties(bone) for bone in rig.data.bones}})
    animation = armature.animation_data
    action = animation.action if animation else None
    return {'rigs': states, 'had_animation': animation is not None,
            'action': action, 'slot': animation.action_slot if animation else None,
            'fake_user': action.use_fake_user if action else None,
            'action_name': action.name if action else None, 'staged_actions': [],
            'drivers': {curve.as_pointer() for curve in animation.drivers} if animation else set(),
            'correctives': body_rest_resync._corrective_checkpoint(armature),
            'dress': skirt_original_mode.checkpoint(context, armature, saved_original['dress_edit'])
                     if saved_original is not None else (),
            'dress_desired': skirt_original_mode.capture(context, armature, saved_original['dress_edit'])
                             if saved_original is not None else None}


def _restore_animation(armature, saved):
    animation = armature.animation_data
    if animation is not None:
        failed = animation.action
        animation.action = saved['action']
        if saved['action']:
            animation.action_slot = saved['slot']
        if failed and failed != saved['action']:
            _remove_staged(failed)
    # Only actions directly produced by this batch's native key service belong
    # to the transaction. Do not delete unrelated new Actions by global diff.
    for action in saved['staged_actions']:
        try:
            if action != saved['action']:
                _remove_staged(action)
        except ReferenceError:
            pass
    if saved['action'] and saved['action'].name != saved['action_name']:
        saved['action'].name = saved['action_name']
    if not saved['had_animation'] and armature.animation_data is not None:
        if not armature.animation_data.drivers and armature.animation_data.action is None:
            armature.animation_data_clear()


def _remove_staged(action):
    """Dispose a transaction-owned Action only when it has no real users."""
    if action.users == int(action.use_fake_user):
        action.use_fake_user = False
        bpy.data.actions.remove(action)


def _rollback(context, armature, saved, installed):
    from . import body_rest_resync, bone_collections, bone_display, skirt_original_mode
    # Remove only switching drivers whose exact owned paths this transaction
    # added. Existing drivers are validated but are never edited by matching.
    for path in installed:
        curve = match._find_driver(armature, path)
        if curve is not None and curve.as_pointer() not in saved['drivers']:
            armature.animation_data.drivers.remove(curve)
    _restore_animation(armature, saved)
    for state in saved['rigs']:
        rig = state['rig']
        if state['object_properties'] is not None:
            _restore_properties(rig, state['object_properties'])
            _restore_properties(rig.data, state['data_properties'])
        for name, props in state['bone_properties'].items():
            _restore_properties(rig.data.bones[name], props)
        _restore_pose(rig, state['channels'])
        for con, muted, influence, pole_angle in state['constraints']:
            if con.mute != muted:
                con.mute = muted
            if con.influence != influence:
                con.influence = influence
            if pole_angle is not None and con.pole_angle != pole_angle:
                con.pole_angle = pole_angle
        bone_display._restore(rig, state['display'])
    skirt_original_mode.rollback(context, saved['dress'])
    bone_collections._FRAME_CACHE.pop(armature.as_pointer(), None)
    match._update(context, armature)
    # Runtime is paused in the caller. Restore owned outputs after the final
    # graph update so failure cannot leak generated-key metadata or positions.
    body_rest_resync.restore_correctives(saved)


def _commit_action(armature, saved):
    previous = saved['action']
    animation = armature.animation_data
    current = animation.action if animation else None
    if current is not None and current != previous:
        # Blender may reset this ID flag on Action.copy(). The final keyed ID
        # retains the artist's preference; intermediate copies remain owned.
        current.use_fake_user = bool(saved['fake_user'])
    for action in saved['staged_actions']:
        if action != current:
            _remove_staged(action)
    if (previous is not None and current != previous
            and previous.users == 0 and not previous.use_fake_user):
        # Rename before releasing the artist ID. A metadata failure remains
        # reversible while the original ID is still alive in the checkpoint.
        previous.name = saved['action_name'] + ' Previous IK FK'
        current.name = saved['action_name']
        return previous
    return None


def _require(context, armature):
    from . import body_original_mode, character_setup, forearm_twist
    if armature is None or getattr(armature, 'type', None) != 'ARMATURE':
        raise limb_ik.LimbIKError('Make the intended character Armature active first.')
    if context.mode not in {'OBJECT', 'POSE'}:
        raise limb_ik.LimbIKError('Switch IK/FK in Object or Pose Mode.')
    active = limb_ik._require_active_armature(context, limb_ik._settings(context), analyzed=False)
    if active != armature:
        raise limb_ik.LimbIKError('Make the intended character Armature active first.')
    preferred = character_setup.preferred_rig(context)
    if preferred is not None and preferred != armature:
        raise limb_ik.LimbIKError('Make the saved character Armature active before switching IK/FK.')
    preview = forearm_twist._SESSION
    if preview is not None and preview.get('armature') == armature:
        raise limb_ik.LimbIKError('Confirm or cancel the Forearm preview before switching IK/FK.')
    saved = body_original_mode._require(context, armature)
    return saved


def switch_all(context, armature, mode, *, keys=None, keyframe=None):
    """Match all selected native limbs and commit their mode or roll back all.

    Active Original is transferred first inside this same transaction. FK then
    uses the source upper/lower/end bones directly; no FK bones are generated.
    Auto Key follows the per-limb service's constant mode cuts and bookends.
    """
    from . import (body_original_mode as original, bone_collections,
                   forearm_original_inventory, forearm_twist, skirt_original_mode)
    if mode not in {'IK', 'FK'}:
        raise limb_ik.LimbIKError('Choose IK or FK.')
    saved_original = _require(context, armature)
    if saved_original is not None:
        # Prove the old graph with its exact saved source mutes. This is read
        # only and rejects edited drivers/influences before Original can leave.
        # Intentional Rest adaptation is proved by Original.leave itself.
        from . import body_rest_resync
        with forearm_twist.defer_runtime(context, flush_on_exit=False):
            try:
                inventory = forearm_original_inventory.validate(armature)
            except ValueError:
                plan = body_rest_resync.prepare(context, armature, saved_original)
                if plan is None:
                    raise
                inventory = forearm_original_inventory.validate(armature, original_rest=plan['view'])
    else:
        inventory = limb_ik._validate_inventory(armature)
    selected = _keys(inventory, keys)
    old_modes = {key: match.mode_for_rig(armature, inventory['rigs'][key]) for key in selected}
    if saved_original is None and all(value == mode for value in old_modes.values()):
        # A repeated mode click also repairs the managed display, without pose
        # matching or inserting animation keys. Keep display writes reversible.
        from . import bone_display
        display_before = bone_display._snapshot(armature)
        try:
            display = bone_collections.sync_limb_display(
                armature, inventory, keys=selected, force_flags=True)
        except Exception:
            bone_display._restore(armature, display_before)
            bone_collections._FRAME_CACHE.pop(armature.as_pointer(), None)
            raise
        return {'mode': mode, 'changed': False, 'changed_keys': (), 'keyed': False,
                'display_changed': display,
                'errors': {key: (0., 0., 0.) for key in selected}, 'left_original': False}
    keyed = bool(keyframe if keyframe is not None else context.scene.tool_settings.use_keyframe_insert_auto)
    animation = armature.animation_data
    if keyed and animation is not None:
        if animation.use_tweak_mode:
            raise limb_ik.LimbIKError('Leave NLA Tweak Mode before keying a body IK/FK switch.')
        if animation.action and (animation.action.library or not animation.action.is_editable):
            raise limb_ik.LimbIKError('Make the current Action local before keying a body IK/FK switch.')
        if animation.action:
            action = animation.action
            # The established key matcher owns a single Action channelbag.
            # Overlapping paths in another slot/layer must not be bookended by
            # this rig; refuse that ambiguity before Original or pose writes.
            if (len(action.slots) > 1 or len(action.layers) > 1
                    or any(len(layer.strips) > 1 for layer in action.layers)):
                raise limb_ik.LimbIKError('Body IK/FK Auto Key needs a single-slot, single-layer Action; preserve the other slots first.')
    with forearm_twist.defer_runtime(context, flush_on_exit=False) as refresh:
        match._update(context, armature)
        native_before = original._pose(armature)
        before = _checkpoint(context, armature, saved_original)
        pose_before = {name: (entry['mode'], entry['basis']) for name, entry in before['rigs'][0]['channels'].items()}
        installed = set()
        changed, errors, affected = [], {}, {}
        try:
            if saved_original is not None:
                original.leave(context, armature)
                inventory = limb_ik._validate_inventory(armature)
            value_before = {key: armature.pose.bones[inventory['rigs'][key]['target'].name].get(match.PROPERTY, 1.)
                            for key in selected}
            for key in selected:
                rig = inventory['rigs'][key]
                if match.VERSION_KEY not in armature.data.bones[rig['target'].name]:
                    installed.update(con.path_from_id('influence') for pb, con, record in rig['entries']
                                     if match.is_switch_constraint(armature, pb, con, record))
            installed_count = match.ensure_switching(armature, inventory, keys=selected)
            if installed_count:
                match._update(context, armature)
            for key in selected:
                rig = inventory['rigs'][key]
                desired = {name: native_before[name] for name in match.pose_names(rig)}
                if match.mode_for_rig(armature, rig) == mode:
                    errors[key] = match._verify(armature, desired)
                    continue
                affected[key] = (match._match_fk(context, armature, rig, desired) if mode == 'FK' else
                                 match._match_ik(context, armature, inventory, rig, desired))
                affected[key].update(match._match_toe(context, armature, rig, desired))
                errors[key] = match._verify(armature, desired)
                changed.append(key)
            match._verify(armature, native_before)
            limb_ik._validate_inventory(armature)
            if saved_original is not None:
                skirt_original_mode.verify(context, armature, saved_original['dress_edit'],
                                           desired=before['dress_desired'])
            if keyed:
                for key in changed:
                    target = armature.pose.bones[inventory['rigs'][key]['target'].name]
                    match._key_switch(context, armature, target, affected[key], value_before[key], pose_before,
                                      keep_previous=True)
                    before['staged_actions'].append(armature.animation_data.action)
            refresh()
            for state in before['correctives']:
                error = forearm_twist._ERRORS.get(state['object'].name)
                if error is not None and (not state['error_present'] or error != state['error']):
                    raise limb_ik.LimbIKError('The body switch could not refresh an owned Forearm correction: ' + error)
            match._update(context, armature)
            match._verify(armature, native_before)
            final_inventory = limb_ik._validate_inventory(armature)
            if saved_original is not None:
                skirt_original_mode.verify(context, armature, saved_original['dress_edit'],
                                           desired=before['dress_desired'])
            bone_collections.sync_limb_display(
                armature, final_inventory, keys=selected, force_flags=True)
            bone_collections._frame_visibility(context.scene, objects=(armature,),
                                               verified_inventory=final_inventory)
            disposable_previous = _commit_action(armature, before)
        except Exception:
            _rollback(context, armature, before, installed)
            raise
        # Terminal disposal: no fallible metadata or matching work follows it.
        if disposable_previous is not None:
            bpy.data.actions.remove(disposable_previous)
    return {'mode': mode, 'changed': bool(changed) or saved_original is not None,
            'changed_keys': tuple(changed), 'keyed': keyed and bool(changed), 'errors': errors,
            'left_original': saved_original is not None}
