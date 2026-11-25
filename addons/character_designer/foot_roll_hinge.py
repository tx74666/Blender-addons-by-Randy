"""One local pitch channel for the visible handle and the ball-of-foot pivot."""
import math

import bpy

from . import foot_controls as feet


NAME = 'CD Foot Roll Hinge'
ANGLE = 'CD Foot Roll Angle'
_EXPRESSIONS = ('0', '0', 'min(roll,0)', '0')
_LIMIT_FIELDS = dict(owner_space='LOCAL', use_transform_limit=True, euler_order='XYZ',
                     use_limit_x=True, use_limit_y=True, use_limit_z=True,
                     min_x=-math.pi, max_x=0., min_y=0., max_y=0., min_z=0., max_z=0.)


def driver_changes(rig, record):
    natural = record.get('rotation_direction') == 'NATURAL'
    roll = rig.pose.bones[record['roll']]
    by_path = {(e['path'], e['index']): e for e in record['drivers']}
    changes = []
    for (role, axis, old, var), new in zip(feet._rotation_driver_plan(record['side'], natural=natural),
                                          _EXPRESSIONS):
        path = rig.pose.bones[record['bones'][role]].path_from_id('rotation_euler')
        expected = roll.path_from_id('rotation_euler') + ('[0]' if var == 'roll' else '[1]')
        entry = by_path.get((path, axis))
        if entry is None or entry['variables'] != {var: expected} or entry['expression'] != old:
            raise feet._error('An existing Foot Roll driver changed. Its setup has been kept.')
        feet._validate_driver(rig, entry)
        curve = rig.animation_data.drivers.find(path, index=axis)
        changes.append((entry, curve, new))
    return changes


def center(roll):
    """The stock arrow points toward +X; turn its face to indicate heel lift (-X)."""
    roll.custom_shape_transform = None
    roll.custom_shape_translation = (0., 0., 0.)
    roll.custom_shape_rotation_euler = (0., 0., math.pi)
    roll.lock_location = roll.lock_scale = (True, True, True)
    roll.lock_rotation = (False, True, True)


def _install(rig, record, changes):
    roll = rig.pose.bones[record['roll']]
    for entry, curve, expression in changes:
        entry['expression'] = curve.driver.expression = expression
    center(roll)
    con = roll.constraints.new('LIMIT_ROTATION')
    con.name = NAME
    for key, value in _LIMIT_FIELDS.items():
        setattr(con, key, value)
    record['constraints'].append(dict(name=con.name, owner=roll.name, type=con.type,
                                      fields={k: getattr(con, k) for k in _LIMIT_FIELDS}))
    # Interactive transforms use the native stop. The signed Euler channel
    # driving the ball retains complete turns, so display its evaluated angle
    # on the dial, including numeric and keyed rotations past half a turn.
    feet._add_constraint(record, roll, 'COPY_ROTATION', ANGLE, rig, subtarget=record['bones']['BALL_PIVOT'],
                         owner_space='LOCAL', target_space='LOCAL', mix_mode='REPLACE',
                         use_x=True, use_y=False, use_z=False)
    record['roll_hinge'] = 1
    record['rotation_direction'] = 'NATURAL'


def install_new(rig, record):
    _install(rig, record, driver_changes(rig, record))


def _compatible_curve(curve, low, high):
    if curve.modifiers or curve.extrapolation != 'CONSTANT':
        return False
    values = [p.co.y for p in curve.sampled_points]
    points = curve.keyframe_points
    values.extend(p.co.y for p in points)
    for left, right in zip(points, list(points)[1:]):
        if left.interpolation == 'BEZIER':
            values.extend((left.handle_right.y, right.handle_left.y))
        elif left.interpolation not in {'LINEAR', 'CONSTANT'}:
            return False
    return all(low - 1e-6 <= y <= high + 1e-6 for y in values)


def _input_guard(rig, roll, record, stable):
    natural = record.get('rotation_direction') == 'NATURAL'
    low = -math.pi / 4 if natural and not (stable and stable.cancels_old_roll) else 0.
    if (roll.rotation_mode != 'XYZ' or not low - 1e-6 <= roll.rotation_euler.x <= 1e-6
            or max(abs(v) for v in roll.rotation_euler[1:]) > 1e-6
            or max(abs(v) for v in roll.location) > 1e-5
            or max(abs(v - 1.) for v in roll.scale) > 1e-5):
        raise feet._error(f'{roll.name} is using a legacy pose. Return that control to its compatible range before updating.')
    paths = {roll.path_from_id(k): k for k in
             ('rotation_euler', 'location', 'scale', 'rotation_axis_angle', 'rotation_quaternion',
              'rotation_mode', *feet._VISUAL_FIELDS, 'custom_shape_transform')}
    if rig.animation_data and any(c.data_path in paths for c in rig.animation_data.drivers):
        raise feet._error(f'A driver writes {roll.name}. The original setup was kept.')
    # Detached clips are selectable again in Motions. Check their channels too;
    # no Action, F-curve, slot or key is rewritten.
    nonzero = set()
    for action in bpy.data.actions:
        for curve in feet._limb()._fcurves_for_action(action):
            field = paths.get(curve.data_path)
            if not field:
                continue
            if field == 'rotation_euler':
                bounds = (low if curve.array_index == 0 else 0., 0.)
            elif field in {'location', 'scale'}:
                bounds = (1., 1.) if field == 'scale' else (0., 0.)
            else:
                raise feet._error(f'{action.name} animates {roll.name} {field}. The original setup was kept.')
            if not _compatible_curve(curve, *bounds):
                raise feet._error(f'{action.name} uses {roll.name} outside the heel-lift range. The original setup was kept.')
            if field == 'rotation_euler' and curve.array_index == 0 and not _compatible_curve(curve, 0., 0.):
                nonzero.add(action)
    if rig.animation_data and nonzero and any(
            not track.mute and any(strip.type == 'META' or (strip.action in nonzero and strip.blend_type != 'REPLACE')
                                   for strip in track.strips) for track in rig.animation_data.nla_tracks):
        raise feet._error('The Foot Roll has blended NLA inputs. Preserve that mix before updating.')
    if roll.children:
        raise feet._error(f'Another bone follows {roll.name}. Its existing setup was kept.')
    names = {roll.name}
    owned = {(e['path'], e['index']) for e in record['drivers']}
    for owner in feet._driver_owners():
        if stable and owner in stable.owners:
            continue
        ad = getattr(owner, 'animation_data', None)
        for curve in ad.drivers if ad else ():
            if owner == rig and (curve.data_path, curve.array_index) in owned:
                continue
            for var in curve.driver.variables:
                if any(t.id == rig and (t.bone_target == roll.name
                           or feet._limb()._path_mentions_bone(t.data_path, names)) for t in var.targets):
                    raise feet._error(f'A driver on {owner.name} reads {roll.name}. The original setup was kept.')
    for obj in bpy.data.objects:
        if obj.parent == rig and obj.parent_type == 'BONE' and obj.parent_bone == roll.name:
            raise feet._error(f'{obj.name} follows {roll.name}. The original setup was kept.')
        owners = [obj, *(obj.pose.bones if obj.pose else ())]
        for owner in owners:
            if any(feet._limb()._constraint_references_controls(c, rig, names) for c in owner.constraints):
                raise feet._error(f'A constraint on {owner.name} follows {roll.name}. The original setup was kept.')


def _held_inputs(rig, names):
    from . import controller_animation
    raw = controller_animation._raw(rig, names)
    # A foot graph operation adds owned constraints and bones. Keep only the
    # pre-existing authors' input channels; graph validation owns constraints.
    return dict(object=raw['object'], pose=raw['pose'], constraints=[])


def _authored_input_target(rig, saved, current, names):
    # Driven components belong to their current driver. Restore an authored
    # axis independently when another axis of the same vector is driven.
    desired = dict(object=dict(saved['object']), pose={k:dict(v) for k,v in saved['pose'].items()},
                   constraints=[])
    blocks = [(rig, desired['object'], current['object'])]
    blocks.extend((rig.pose.bones[n], desired['pose'][n], current['pose'][n]) for n in names)
    by_path = {}
    for curve in getattr(rig.animation_data, 'drivers', ()):
        by_path.setdefault(curve.data_path, set()).add(curve.array_index)
    for block, old, now in blocks:
        for field in ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale'):
            if old[field] != now[field]:
                driven = by_path.get(block.path_from_id(field), ())
                old[field] = tuple(b if i in driven else a for i,(a,b) in enumerate(zip(old[field], now[field])))
        for group in ('properties', 'settings'):
            if old[group] != now[group]:
                old[group] = dict(old[group])
                for field in old[group]:
                    path = (block.path_from_id() + '["' + bpy.utils.escape_identifier(field) + '"]'
                            if group == 'properties' else block.path_from_id(field))
                    if path in by_path:
                        old[group][field] = now[group][field]
    return desired


def _restore_held_inputs(context, rig, saved, names):
    from . import animation_controller_edit, natural_bind_mode
    current = _held_inputs(rig, names)
    if current == saved:
        return
    desired = _authored_input_target(rig, saved, current, names)
    # Reapplying a keyed bind offset during this update would reinterpret the
    # saved local pose. Hold only this rig until Blender has evaluated it.
    with natural_bind_mode.defer_expiry(rig) as hold:
        hold.hold_current_inputs = True
        animation_controller_edit._restore_changed_inputs(rig, desired, current)
        context.view_layer.update()
        natural_bind_mode.keep_restored_inputs(context.scene, rig, current['pose'], desired['pose'])
    context.view_layer.update()
    actual = _held_inputs(rig, names)
    # Driven outputs can change because their original artist inputs were
    # just restored. Compare only the independently authored components here.
    desired = _authored_input_target(rig, saved, actual, names)
    if actual != desired:
        for name in names:
            for field, value in desired['pose'][name].items():
                if actual['pose'][name][field] != value:
                    raise feet._error(f'{name} {field} did not survive the Foot Roll update.')
        raise feet._error('The character Object inputs did not survive the Foot Roll update.')


def upgrade(context, armature, key):
    from . import animation_pose_test, body_original_mode, foot_roll_hinge_plant, foot_roll_hinge_stable, foot_sole_display
    if (context.object != armature or armature is None or armature.mode not in {'POSE', 'OBJECT'}
            or not animation_pose_test._pose_owner(armature) or context.scene.objects.get(armature.name) != armature):
        raise feet._error('Select the local character armature in Pose or Object Mode.')
    if context.screen and context.screen.is_animation_playing:
        raise feet._error('Pause playback before updating Foot Roll.')
    if body_original_mode.active(armature):
        raise feet._error('Return to Controls before updating Foot Roll.')
    inv = feet._limb()._validate_inventory(armature, limb_pose_only=True)
    record = feet.get_record(armature, key)
    if not record:
        raise feet._error('Add Foot Controls to this leg first.')
    feet.validate(armature, inv, limb_pose_only=True)
    if record.get('roll_hinge') == 1:
        from . import footwear_display
        if not footwear_display.fit(context, armature, record, footwear_display.reference(context, armature, record)):
            foot_sole_display.anchor_existing(context, armature.pose.bones[record['target']], armature.pose.bones[record['chain'][2]], repair=True)
        return feet.get_record(armature, key)
    roll = armature.pose.bones[record['roll']]
    if roll.constraints.get(NAME) or roll.constraints.get(ANGLE):
        raise feet._error('Another constraint already uses the Foot Roll hinge name.')
    stable = foot_roll_hinge_stable.prepare(armature, record)
    _input_guard(armature, roll, record, stable)
    changes = driver_changes(armature, record)
    old_expr = [(curve, curve.driver.expression) for _entry, curve, _new in changes]
    old_state = feet._roll_visual_state(roll)
    old_locks = tuple(tuple(v) for v in (roll.lock_location, roll.lock_rotation, roll.lock_scale))
    raw = armature.data[feet.RECORD_KEY]
    values = feet._all_records(armature)
    names = tuple(armature.pose.bones.keys())
    held = _held_inputs(armature, names)
    desired = {name: armature.pose.bones[name].matrix.copy() for name in names}
    plant = foot_roll_hinge_plant.new_record()
    sole = foot_sole_display.capture(armature.pose.bones[record['target']])
    try:
        _install(armature, record, changes)
        if stable:
            stable.apply(context)
        foot_roll_hinge_plant.install(context, armature, record, plant)
        foot_roll_hinge_plant.merge(record, plant)
        feet._write_records(armature, dict(values, **{record['side']: record}))
        feet._update(context, armature)
        _restore_held_inputs(context, armature, held, names)
        feet._verify_pose(armature, desired)
        foot_sole_display.anchor(armature.pose.bones[record['target']], armature.pose.bones[record['chain'][2]])
        feet._limb()._validate_inventory(armature, limb_pose_only=True)
        from . import footwear_display
        footwear_display.fit(context, armature, record, footwear_display.reference(context, armature, record))
    except Exception:
        foot_sole_display.restore(armature.pose.bones[record['target']], sole)
        foot_roll_hinge_plant.rollback(context, armature, plant)
        if stable:
            stable.rollback()
        roll = armature.pose.bones[record['roll']]
        for name in (ANGLE, NAME):
            con = roll.constraints.get(name)
            if con:
                roll.constraints.remove(con)
        for curve, expression in old_expr:
            curve.driver.expression = expression
        feet._set_roll_visual(armature, roll, old_state)
        roll.lock_location, roll.lock_rotation, roll.lock_scale = old_locks
        armature.data[feet.RECORD_KEY] = raw
        feet._update(context, armature)
        _restore_held_inputs(context, armature, held, names)
        feet._verify_pose(armature, desired)
        raise
    return feet.get_record(armature, key)
