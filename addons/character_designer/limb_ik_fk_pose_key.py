"""Selected-limb pose keys on a retained character controller Action and slot."""
import math
from contextlib import ExitStack, contextmanager

import bpy

from . import limb_ik_fk as match


_PENDING = {}
_TEMPORARY = {}


def _forget(*_args):
    _PENDING.clear()
    _TEMPORARY.clear()


def clear_pending():
    _forget()
    for handlers in (bpy.app.handlers.frame_change_post, bpy.app.handlers.load_pre,
                     bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _forget in handlers:
            handlers.remove(_forget)


def _remember(token, values):
    if token not in _PENDING:
        _PENDING[token] = values
    for handlers in (bpy.app.handlers.frame_change_post, bpy.app.handlers.load_pre,
                     bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if _forget not in handlers:
            handlers.append(_forget)


def active_record(context, rig):
    scene = context.scene
    setup = getattr(scene, 'character_designer_setup', None)
    ad = getattr(rig, 'animation_data', None)
    if setup is None or setup.rig != rig or not ad or not ad.action or not ad.action_slot:
        return None
    workspace = scene.character_designer_animation_worklist.workspace_identity
    matches = [r for r in scene.character_designer_controller_motions
               if r.state == 'READY' and r.workspace_identity == workspace and r.target == rig
               and r.controller_action == ad.action and r.controller_slot == ad.action_slot_handle]
    return matches[0] if len(matches) == 1 else None


def _transforms(rig, limb, mode):
    # Native wrist/orientation inputs can contribute in IK as well as target/pole.
    names = set(limb['chain'])
    if mode == 'IK':
        names.update(limb[k].name for k in ('target', 'pole', 'ori_upper', 'ori_lower', 'heel') if limb.get(k))
    foot = limb.get('foot_controls')
    if foot:
        names.add(match._bone_name(foot['toe_control']))
        if mode == 'IK':
            names.add(match._bone_name(foot['roll']))
    inputs = {}
    for name in sorted(names):
        bone = rig.pose.bones[name]
        rotation = {'QUATERNION': 'rotation_quaternion', 'AXIS_ANGLE': 'rotation_axis_angle'}.get(
            bone.rotation_mode, 'rotation_euler')
        for prop in ('location', rotation, 'scale'):
            path = bone.path_from_id(prop)
            for i, value in enumerate(getattr(bone, prop)):
                inputs[path, i] = (float(value), name)
    return inputs


def _values(rig, limb, mode, inventory):
    from . import elbow_pole_layout

    inputs = _transforms(rig, limb, mode)
    target = rig.pose.bones[limb['target'].name]
    mode_key = match.property_path(target), 0
    inputs[mode_key] = (float(target[match.PROPERTY]), 'IK / FK')
    if mode == 'IK' and (match._leg_front_policy(inventory, limb) or elbow_pole_layout.supports(inventory, limb)):
        con = match._fixed_pole_constraint(limb)
        inputs[con.path_from_id('pole_angle'), 0] = (float(con.pole_angle), 'IK / FK')
    return inputs, mode_key


def _key_channels(bag, frame, values, mode_key, original, *, preserve_fk_mode, action=None, limb=None):
    """Plan from the displayed pose and the curves as they were before native insertion."""
    from . import animation_cleanup, animation_preview_curves
    existing = {(c.data_path, c.array_index): c for c in bag.fcurves if (c.data_path, c.array_index) in values}
    values = {
        key: state for key, state in values.items()
        if (key in existing or not key[0].endswith('.scale')
            or abs(state[0] - 1.) > animation_cleanup.NEUTRAL_SCALE_TOLERANCE
            or abs(original[key][0] - 1.) > animation_cleanup.NEUTRAL_SCALE_TOLERANCE)
    }
    mode_curve = existing.get(mode_key)
    # Deleting all keys of an IK foot also removes its mode channel. The actual
    # pre-switch pose is the only known previous mode; do not reconstruct a
    # guessed historical mode from the Action's FK/IK version label.
    has_mode_keys = bool(mode_curve and mode_curve.keyframe_points)
    previous_mode = float(mode_curve.evaluate(frame)) if has_mode_keys else original[mode_key][0]
    changing = abs(previous_mode - values[mode_key][0]) > 1e-5
    span = {}
    if changing and action is not None and limb is not None:
        from . import knee_pole_keying
        mode = 'IK' if values[mode_key][0] >= .5 else 'FK'
        span = knee_pole_keying.range_values(action, bag, limb, mode, frame, mode_curve)
    if not changing and preserve_fk_mode and has_mode_keys:
        # A temporary IK pose committed to an already-FK section is a pure FK
        # pose edit. Leave the complete mode curve and its interpolation alone.
        values = {key: state for key, state in values.items() if key != mode_key}
        existing.pop(mode_key)
    for path, index in tuple(values):
        if index != 0 or not path.endswith('.rotation_quaternion'):
            continue
        components = [(path, i) for i in range(4)]
        if all(key in existing for key in components):
            dot = sum(values[key][0] * existing[key].evaluate(frame) for key in components)
            if dot < 0:
                for key in components:
                    value, group = values[key]
                    values[key] = -value, group
    cut = frame - 1.
    if changing:
        # A fractional or consecutive switch must respect nearer authored keys.
        for curve in existing.values():
            cut = max((p.co.x for p in curve.keyframe_points if cut < p.co.x < frame - 1e-5), default=cut)
    splits = {}
    first_loop_keys = set()
    for key, curve in existing.items():
        repeats = animation_preview_curves._repeat_mode(curve) == 'REPEAT'
        if (not animation_preview_curves._editable(curve)
                or (not repeats and any(not m.mute for m in curve.modifiers))):
            raise ValueError('Unlock the limb channels and use plain curves or Repeat Motion before keying.')
        if repeats:
            points = curve.keyframe_points
            if not points or not points[0].co.x <= frame <= points[-1].co.x:
                raise ValueError('Key the limb switch inside the original cycle of each repeating channel.')
            if cut < points[0].co.x:
                first_loop_keys.add(key)
        if changing and key not in first_loop_keys and curve.keyframe_points:
            splits[key] = match._curve_bookend(curve, cut, inside_repeat=repeats)
    return _write_channels(bag, frame, values, mode_key, original, changing, cut, splits, first_loop_keys, span)


@contextmanager
def _write_channels(bag, frame, values, mode_key, original, changing, cut, splits, first_loop_keys, span):
    from . import controller_animation_curves, limb_ik_wrist_keying as curves
    # Native I may have created a formerly missing selected-bone curve.
    existing = {(c.data_path, c.array_index): c for c in bag.fcurves if (c.data_path, c.array_index) in values}
    budget = [0]
    snapshots = {key: curves._point_snapshot(curve, budget) for key, curve in existing.items()}
    groups = {g.name for g in bag.groups}
    created = []
    try:
        for key, (value, group) in values.items():
            curve = existing.get(key)
            if curve is None:
                curve = bag.fcurves.new(key[0], index=key[1], group_name=group)
                created.append(curve)
            if changing and key not in first_loop_keys:
                plan = splits.get(key)
                before = plan['value'] if plan else original[key][0]
                curve.keyframe_points.insert(cut, before)
            point = curve.keyframe_points.insert(frame, value)
            if key == mode_key:
                point.interpolation = 'CONSTANT'
            if changing and key not in first_loop_keys:
                if plan:
                    match._restore_bookend(curve, cut, plan)
                cut_key = next(k for k in curve.keyframe_points if abs(k.co.x - cut) < 1e-5)
                cut_key.interpolation = 'CONSTANT'
            if key in span:
                indices = {float(p.co.x):i for i,p in enumerate(curve.keyframe_points)}
                additions = []
                for future,component,*hold in span[key]:
                    if future in indices:
                        existing_key = curve.keyframe_points[indices[future]]
                        existing_key.co_ui = (future,component)
                        if hold:
                            existing_key.interpolation = hold[0]
                    else:
                        additions.append((future,component,hold))
                for future,component,hold in additions:
                    next_key = curve.keyframe_points.insert(future,component,options={'FAST'})
                    next_key.interpolation = hold[0] if hold else 'LINEAR'
            curve.update()
            if key[0].endswith('.pole_angle'):
                # Keep artist interpolation outside the two adjacent segments.
                # An incoming constant bookend already protects a mode change.
                legs = [(p.co.x, p.co.y, p.interpolation) for p in curve.keyframe_points]
                for left, right in zip(legs, legs[1:]):
                    if not (left[0] <= frame <= right[0]) or left[2] == 'CONSTANT':
                        continue
                    if controller_animation_curves.pole_angle_interpolation(left[1], right[1]) == 'CONSTANT':
                        guards = controller_animation_curves.pole_angle_keys([left[:2], right[:2]])
                        for time, angle, interpolation in guards[:-1]:
                            key_point = curve.keyframe_points.insert(time, angle)
                            key_point.interpolation = interpolation
                curve.update()
            if not math.isclose(curve.evaluate(frame), value, abs_tol=2.e-5, rel_tol=2.e-6):
                raise ValueError('The current limb key could not reproduce its control value.')
        yield
    except Exception:
        for curve in created:
            bag.fcurves.remove(curve)
        for group in list(bag.groups):
            if group.name not in groups and not group.channels:
                bag.groups.remove(group)
        for key, saved in snapshots.items():
            points = saved['curve'].keyframe_points
            while len(points) > saved['count']:
                points.remove(points[-1], fast=True)
            curves._restore_curve(saved)
        raise


def _other_points(curve, frame):
    return tuple(tuple(p.co) for p in curve.keyframe_points if abs(p.co.x - frame) > 1e-5)


def _temporary_inputs(bag, channels, frame):
    from . import limb_ik_wrist_keying as curves
    budget = [0]
    saved = {(c.data_path, c.array_index): (curves._point_snapshot(c, budget), _other_points(c, frame))
             for c in bag.fcurves if (c.data_path, c.array_index) in channels}
    return channels, saved, {g.name for g in bag.groups}


def _restore_points(saved):
    from . import limb_ik_wrist_keying as curves
    points = saved['curve'].keyframe_points
    if len(points) < saved['count']:
        points.add(saved['count'] - len(points))
    while len(points) > saved['count']:
        points.remove(points[-1], fast=True)
    curves._restore_curve(saved)


@contextmanager
def _without_temporary_keys(bag, frame, temporary):
    # Native transform Auto Key may have keyed the target, pole or even the
    # custom mode property. Revert those edits to this frame when storing FK.
    if not temporary:
        yield
        return
    from . import limb_ik_wrist_keying as curves
    channels, original, groups = temporary
    restored, created, budget = [], [], [0]
    for c in bag.fcurves:
        key = c.data_path, c.array_index
        if key not in channels or not any(abs(p.co.x - frame) <= 1e-5 for p in c.keyframe_points):
            continue
        previous, outside = original.get(key, (None, ()))
        if _other_points(c, frame) != outside or (previous and c.as_pointer() != previous['pointer']):
            raise ValueError('The temporary IK channels also changed on another frame. Press I in the 3D Viewport to keep the IK pose.')
        if previous is None:
            created.append(c)
        else:
            restored.append((previous, curves._point_snapshot(c, budget)))
    try:
        for previous, _current in restored:
            _restore_points(previous)
        yield
    except Exception:
        for _previous, current in restored:
            _restore_points(current)
        raise
    for c in created:
        bag.fcurves.remove(c)
    for group in list(bag.groups):
        if group.name not in groups and not group.channels:
            bag.groups.remove(group)


def _editable_record(context, rig, record_key):
    from . import body_original_mode
    record = active_record(context, rig)
    if not record or context.mode != 'POSE' or context.view_layer.objects.active != rig:
        raise ValueError('Open the controller Action on its character and select a limb in Pose Mode.')
    ad = rig.animation_data
    if (ad.action == record.source_action or ad.action.library or ad.action.override_library
            or not ad.action.is_editable or ad.action.asset_data or ad.use_tweak_mode
            or ad.action_blend_type != 'REPLACE' or ad.action_influence != 1
            or (ad.use_nla and any(not t.mute and t.strips for t in ad.nla_tracks))
            or (context.screen and context.screen.is_animation_playing) or body_original_mode.active(rig)):
        raise ValueError('Stop playback and open the editable controller Action in Controls to key a limb.')
    if record_key:
        for owner in bpy.data.user_map(subset=[ad.action]).get(ad.action, ()):
            if owner not in (rig, context.scene):
                raise ValueError('This controller Action is shared with another owner. Make its edit independent first.')
        if any(r.source_action == ad.action or
               (r != record and ad.action in (r.controller_action, getattr(r, 'alternate_action', None)))
               for r in context.scene.character_designer_controller_motions):
            raise ValueError('This Action also belongs to another motion or a source clip.')
    return record


def _finish(tokens):
    for token in tokens:
        _PENDING.pop(token, None)
        _TEMPORARY.pop(token, None)
    if not _PENDING:
        clear_pending()


def selected_pending(context):
    if (not _PENDING or context.mode != 'POSE' or not context.area or context.area.type != 'VIEW_3D'
            or not context.region or context.region.type != 'WINDOW'):
        return ()
    rig = context.object
    record = active_record(context, rig)
    if not record:
        return ()
    scene = context.scene
    frame = scene.frame_current + scene.frame_subframe
    prefix = (scene.session_uid, rig.session_uid, record.controller_action.session_uid, record.controller_slot)
    selected = {bone.name for bone in context.selected_pose_bones or () if bone.id_data == rig}
    return tuple(token for token, inputs in _PENDING.items()
                 if token[:4] == prefix and token[-1] == frame
                 and any(group in selected for _, group in inputs.values()))


def insert_with_switches(context, tokens):
    """Commit the matched inputs and run Blender's selected/keying-set insertion in one undo step."""
    from . import control_pose_offset
    rig = context.object
    record = _editable_record(context, rig, True)
    inventory = match._limb()._validate_inventory(rig, limb_pose_only=True)
    action = record.controller_action
    bag = control_pose_offset._bag(action, record.controller_slot)
    plans = []
    for token in tokens:
        key, frame = token[-2:]
        limb = inventory['rigs'][key]
        mode = match.mode_for_rig(rig, limb)
        if mode not in ('FK', 'IK'):
            raise ValueError('Choose FK or IK before keying a blended limb.')
        values, mode_key = _values(rig, limb, mode, inventory)
        plans.append(_key_channels(bag, frame, values, mode_key, _PENDING[token],
                                   preserve_fk_mode=False, action=action, limb=limb))
    # Blender reads still-unkeyed edits (also on other selected controls) here.
    # Writing ANY Action curve first can reevaluate those edits away.
    if 'FINISHED' not in bpy.ops.anim.keyframe_insert('EXEC_DEFAULT', False):
        raise ValueError('Blender could not insert the selected keys. The limb switch is still pending.')
    try:
        with ExitStack() as changes:
            for plan in plans:
                changes.enter_context(plan)
    except Exception as exc:
        # Ordinary insertion succeeded. Retain its undo step and allow a retry;
        # the compound switch write has been rolled back for every limb.
        return f'Blender inserted the selected keys. The mode switch is still pending: {exc}'
    _finish(tokens)


def edit(context, rig, key, mode, *, record_key, temporary_ik=False, store_as_fk=False):
    """Capture the displayed pose, then switch and/or explicitly key this limb."""
    from . import bone_display, control_pose_offset, controller_animation, limb_fk_visuals
    record = _editable_record(context, rig, record_key)
    ad = rig.animation_data
    inventory = match._limb()._validate_inventory(rig, limb_pose_only=True)
    if key not in inventory['rigs']:
        raise ValueError('Build the selected limb controls first.')
    limb = inventory['rigs'][key]
    old_mode = match.mode_for_rig(rig, limb)
    output_mode = old_mode if mode == 'CURRENT' else mode
    if output_mode not in ('IK', 'FK'):
        raise ValueError('Choose IK or FK before recording a blended limb.')
    if output_mode == 'FK':
        limb_fk_visuals.ensure_clip_endpoints(context, rig, inventory, keys=(key,))
    before, mode_key = _values(rig, limb, output_mode, inventory)
    bag = control_pose_offset._bag(ad.action, record.controller_slot)
    # Capture raw inputs before matching so a failed key can also undo the pose switch.
    names = set(limb['chain']) | {group for _, group in before.values() if group != 'IK / FK'}
    names.update(group for _, group in _transforms(rig, limb, 'IK').values())
    raw = controller_animation._raw(rig, names) if record_key else None
    display = bone_display._snapshot(rig)
    frame = context.scene.frame_current + context.scene.frame_subframe
    token = (context.scene.session_uid, rig.session_uid, ad.action.session_uid, record.controller_slot, key, frame)
    temporary = None
    if temporary_ik and old_mode == 'FK' and output_mode == 'IK':
        fk_inputs = _transforms(rig, limb, 'FK')
        temporary = _temporary_inputs(bag, set(before) - set(fk_inputs), frame)
    if old_mode != output_mode and not record_key:
        # Snapshot missing, previously unanimated inputs before the artist drags in
        # the alternate mode. Existing channels always use their own original curves.
        before.update(_values(rig, limb, old_mode, inventory)[0])
    try:
        if old_mode != output_mode:
            match.switch_limb(context, rig, key, output_mode, keyframe=False, limb_pose_only=True)
        if record_key:
            values, mode_key = _values(rig, limb, output_mode, inventory)
            previous = before.copy()
            previous.update(_PENDING.get(token, {}))
            with ExitStack() as changes:
                changes.enter_context(_without_temporary_keys(bag, frame, _TEMPORARY.get(token) if output_mode == 'FK' else None))
                changes.enter_context(_key_channels(bag, frame, values, mode_key, previous,
                                      preserve_fk_mode=store_as_fk or (output_mode == 'FK' and token in _TEMPORARY),
                                      action=ad.action, limb=limb))
            _finish((token,))
        elif old_mode != output_mode:
            _remember(token, before)
            if temporary is not None:
                _TEMPORARY[token] = temporary
        return output_mode
    except Exception:
        if raw:
            controller_animation._restore_raw(rig, raw)
        bone_display._restore(rig, display)
        match._update(context, rig)
        raise


def edit_group(context, rig, keys, mode):
    """Match a set of limbs before writing any keys on the retained Action."""
    from . import control_pose_offset, forearm_twist, limb_fk_visuals, limb_ik_fk_batch as batch
    keyed = context.scene.tool_settings.use_keyframe_insert_auto
    record = _editable_record(context, rig, keyed)
    inventory = match._limb()._validate_inventory(rig, limb_pose_only=True)
    keys = batch._keys(inventory, keys)
    if mode == 'FK':
        limb_fk_visuals.ensure_clip_endpoints(context, rig, inventory, keys=keys)
    action = record.controller_action
    bag = control_pose_offset._bag(action, record.controller_slot)
    frame = context.scene.frame_current + context.scene.frame_subframe
    previous, desired, tokens = {}, {}, {}
    driver_paths = set()
    for key in keys:
        limb = inventory['rigs'][key]
        old_mode = match.mode_for_rig(rig, limb)
        token = (context.scene.session_uid, rig.session_uid, action.session_uid, record.controller_slot, key, frame)
        if old_mode == mode and token not in _PENDING:
            continue
        tokens[key] = token
        previous[key] = _values(rig, limb, mode, inventory)[0]
        previous[key].update(_values(rig, limb, old_mode, inventory)[0])
        previous[key].update(_PENDING.get(token, {}))
        if old_mode != mode:
            desired[key] = match._matrices(rig, match.pose_names(limb))
            driver_paths.update(con.path_from_id('influence') for pb, con, record_data in limb['entries']
                                if match.is_switch_constraint(rig, pb, con, record_data))
    if not tokens:
        return
    with forearm_twist.defer_runtime(context, flush_on_exit=False):
        saved = batch._checkpoint(context, rig, None)
        try:
            for key, pose in desired.items():
                match.switch_limb(context, rig, key, mode, keyframe=False, desired_pose=pose, limb_pose_only=True)
            if keyed:
                plans = []
                for key in tokens:
                    limb = inventory['rigs'][key]
                    values, mode_key = _values(rig, limb, mode, inventory)
                    plans.append(_key_channels(bag, frame, values, mode_key, previous[key],
                                               preserve_fk_mode=False, action=action, limb=limb))
                with ExitStack() as changes:
                    for plan in plans:
                        changes.enter_context(plan)
        except Exception:
            batch._rollback(context, rig, saved, driver_paths)
            raise
    if keyed:
        _finish(tokens.values())
    else:
        for key, token in tokens.items():
            _remember(token, previous[key])
            _TEMPORARY.pop(token, None)
    # The regular viewport I command now sees every matched target, even when
    # the group switch started from one selected bone or from the master.
    for bone in context.selected_pose_bones or ():
        bone.select = False
    destinations = [rig.pose.bones[limb['target'].name if mode == 'IK' else limb['chain'][-1]]
                    for limb in (inventory['rigs'][key] for key in keys)]
    for bone in destinations:
        bone.select = True
    rig.data.bones.active = destinations[0].bone


def draw(layout, context, *, advanced=False):
    from . import controller_animation_ui as ui, limb_ik, limb_ik_ui_state, limb_ik_fk_batch as batch
    rig = context.object
    if context.mode != 'POSE' or not active_record(context, rig):
        return
    try:
        inventory = limb_ik_ui_state.inventory(rig)
        selected = ui._selected_limb_keys(context, rig, inventory)
        settings = limb_ik._settings(context)
        scope = settings.animation_limb_scope
        if scope == 'ALL':
            keys = tuple(inventory['rigs'])
        elif scope == 'AUTO' and selected:
            keys = selected
        else:
            keys = (limb_ik.SELECTED_LIMBS.get(settings.selected_limb if scope == 'AUTO' else scope),)
        keys = tuple(key for key in keys if key in inventory['rigs'])
        if not keys:
            if not advanced:
                layout.prop(settings, 'animation_limb_scope', text='Limb')
            return
        if scope == 'ALL' or len(keys) > 1:
            if advanced:
                return
            limb_id, label = ('ALL', 'All Limbs') if scope == 'ALL' else ('SELECTION', 'Selected Limbs')
            mode = batch.mode_for_keys(rig, inventory, keys)
        else:
            key, = keys
            limb_id = next(k for k, v in limb_ik.SELECTED_LIMBS.items() if v == key)
            label = ui._LIMB_LABELS[key]
            mode = match.mode_for_rig(rig, inventory['rigs'][key])
    except (KeyError, RuntimeError, ValueError, AttributeError):
        return
    row = layout.row(align=True)
    if not advanced:
        row.prop_menu_enum(settings, 'animation_limb_scope', text=label + (' · Mixed' if mode == 'MIXED' else ''))
        for choice in ('FK', 'IK'):
            op = row.operator('character_designer.controller_limb_pose', text=choice, depress=mode == choice)
            op.operation, op.limb_id = choice, limb_id
        if mode in {'FK', 'MIXED'} and any(
            rig.pose.bones[inventory['rigs'][key]['chain'][2]].custom_shape is None
            for key in keys
        ):
            layout.operator('character_designer.limb_fk_visuals', text='Set Up FK Controls',
                            icon='MESH_CIRCLE').action = 'ENDPOINTS'
        return
    if mode == 'IK':
        op = row.operator('character_designer.controller_limb_pose', text='Key as FK')
        op.operation, op.limb_id = 'KEY_FK', limb_id
    elif mode == 'FK':
        op = row.operator('character_designer.controller_limb_pose', text='Pose in IK for FK')
        op.operation, op.limb_id = 'POSE_IK', limb_id
