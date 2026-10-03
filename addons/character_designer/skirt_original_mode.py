"""Direct Dress posing with persistent native local correction channels.

The generated manual Copy Transforms uses native Before Full mixing so the
artist's PoseBone basis becomes a local correction. Existing spline/physics
evaluation and shear remain intact, and physics still evaluates exactly once.
No rest bones, helper objects or per-frame Python pose handlers are added.
"""
import json
import math

from mathutils import Matrix
from . import skirt_rig as skirt, limb_ik

CORRECTIONS = 'character_designer_skirt_pose_corrections_v1'
_CHANNELS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')
_LOCKS = ('lock_rotation', 'lock_rotation_w', 'lock_rotations_4d')


def _difference(a, b):
    return max(abs(x - y) for ar, br in zip(a, b) for x, y in zip(ar, br))


def _values(matrix):
    return [list(row) for row in matrix]


def _channels(pb):
    return {'mode': pb.rotation_mode,
            'channels': {key: list(getattr(pb, key)) for key in _CHANNELS}}


def _restore(pb, state):
    pb.rotation_mode = state['mode']
    for key, value in state['channels'].items():
        if tuple(getattr(pb, key)) != tuple(value):
            setattr(pb, key, value)


def _locks(pb):
    return {key: list(value) if hasattr(value, '__len__') else value
            for key in _LOCKS for value in (getattr(pb, key),)}


def _restore_locks(pb, state):
    for key, value in state.items():
        setattr(pb, key, value)


def _update(context, targets):
    for target in targets:
        target.update_tag(refresh={'OBJECT'})
    context.view_layer.update()


def _inventory(context, main):
    from . import bone_display
    result = {}
    for target, record in bone_display.dress_rigs(context, main):
        source = (target.data.bones[record['controls']['waist']].get(skirt.SOURCE_KEY)
                  if skirt.is_shared(record) else target.get(skirt.SOURCE_KEY))
        if source is None or source.get(skirt.RIG_KEY) != target or record['owner'] in result:
            raise ValueError('Restore the exact Dress source ownership before direct posing.')
        result[record['owner']] = (target, source, record)
    return result


def _actions(target):
    """Include actions inside nested NLA meta strips in the safety preflight."""
    animation = target.animation_data
    if not animation:
        return ()
    actions = {animation.action} if animation.action else set()
    pending = [strip for track in animation.nla_tracks for strip in track.strips]
    while pending:
        strip = pending.pop()
        if strip.action is not None:
            actions.add(strip.action)
        pending.extend(getattr(strip, 'strips', ()))
    return tuple(actions)


def _animated(target, names):
    animation = target.animation_data
    if not animation:
        return
    prefixes = tuple(target.pose.bones[name].path_from_id() + '.' + prop
                     for name in names for prop in _CHANNELS + ('rotation_mode',))
    curves = list(animation.drivers)
    curves.extend(curve for action in _actions(target)
                  for curve in limb_ik._fcurves_for_action(action))
    if any(curve.data_path.startswith(prefixes) for curve in curves):
        raise ValueError('Dress originals have animated or driven transform channels. Preserve that animation before direct posing.')


def _constraint_animation(target, source, names, physics):
    animation = target.animation_data
    _control, driver_id, path = skirt.physics_control(source)
    curves = list(animation.drivers) if animation else []
    prefixes = tuple(target.pose.bones[name].path_from_id() + '.constraints' for name in names)
    if any(curve.data_path.startswith(prefixes) for action in _actions(target)
           for curve in limb_ik._fcurves_for_action(action)):
        raise ValueError('Dress original constraints have animation. Preserve it before direct posing.')
    allowed = set()
    for rotation in physics:
        influence = rotation.path_from_id() + '.influence'
        candidates = [curve for curve in curves if curve.data_path == influence]
        if len(candidates) != 1:
            raise ValueError('Restore the generated Dress physics influence driver before direct posing.')
        curve = candidates[0]
        driver = curve.driver
        variables = tuple(driver.variables)
        if (curve.mute or driver.type != 'AVERAGE' or len(variables) != 1
                or variables[0].type != 'SINGLE_PROP' or len(variables[0].targets) != 1
                or variables[0].targets[0].id != driver_id or variables[0].targets[0].data_path != path):
            raise ValueError('The Dress physics influence driver was edited. Preserve it before direct posing.')
        allowed.add(influence)
    if any(curve.data_path.startswith(prefixes) and curve.data_path not in allowed for curve in curves):
        raise ValueError('A custom driver controls a Dress original constraint. Preserve it before direct posing.')


def _rest(target, names):
    return {name: {'matrix': _values(bone.matrix_local), 'head': list(bone.head_local),
                   'tail': list(bone.tail_local), 'connected': bone.use_connect,
                   'inherit_scale': bone.inherit_scale, 'inherit_rotation': bone.use_inherit_rotation,
                   'local_location': bone.use_local_location, 'deform': bone.use_deform}
            for name in names for bone in (target.data.bones[name],)}


def _corrections(source, record):
    raw = source.get(CORRECTIONS)
    if raw is None:
        return {'version': 1, 'owner': record['owner'], 'bones': {}}
    try:
        value = json.loads(raw)
        names = {name for chain in record['chains'] for name in chain['def']}
        if (not isinstance(value, dict) or not isinstance(value.get('bones'), dict)
                or value['version'] != 1 or value['owner'] != record['owner']
                or set(value['bones']) - names):
            raise ValueError()
        for entry in value['bones'].values():
            channels = entry['channels']['channels']
            if (entry['mix_mode'] != 'REPLACE' or set(channels) != set(_CHANNELS)
                    or entry['channels']['mode'] not in {'QUATERNION', 'AXIS_ANGLE', 'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
                    or any(len(channels[key]) != (4 if key in {'rotation_quaternion', 'rotation_axis_angle'} else 3)
                           for key in _CHANNELS)
                    or any(not math.isfinite(float(number)) for values in channels.values() for number in values)):
                raise ValueError()
        return value
    except (TypeError, KeyError, ValueError, AttributeError):
        raise ValueError('The saved Dress correction record is incomplete; restore its saved file.') from None


def _relations(target, source, record, *, working=False):
    correction = _corrections(source, record)
    result = []
    for chain in record['chains']:
        for name, manual, physics in zip(chain['def'], chain['manual'], chain['phys']):
            pb = target.pose.bones[name]
            copy = pb.constraints.get('Skirt manual pose')
            rotation = pb.constraints.get('Skirt physics delta')
            if (copy is None or rotation is None or copy.type != 'COPY_TRANSFORMS'
                    or rotation.type != 'COPY_ROTATION' or copy.target != target
                    or rotation.target != target or copy.subtarget != manual
                    or rotation.subtarget != physics
                    or copy.owner_space != 'LOCAL' or copy.target_space != 'LOCAL'
                    or rotation.owner_space != 'LOCAL' or rotation.target_space != 'LOCAL'
                    or rotation.mix_mode != 'BEFORE' or abs(copy.influence - 1.0) > 1e-7
                    or copy.remove_target_shear
                    or not all((rotation.use_x, rotation.use_y, rotation.use_z))
                    or any((rotation.invert_x, rotation.invert_y, rotation.invert_z))
                    or rotation.euler_order != 'AUTO'
                    or list(pb.constraints).index(copy) > list(pb.constraints).index(rotation)
                    or copy.mix_mode not in ({'BEFORE_FULL', 'REPLACE'} if working
                                             else {'BEFORE_FULL'} if name in correction['bones'] else {'REPLACE'})
                    or any(not con.mute for con in pb.constraints if con not in (copy, rotation))):
                raise ValueError('A Dress original has an edited or extra pose constraint. Preserve that constraint before direct posing.')
            result.append((pb, copy, rotation))
    waist = target.pose.bones[record['controls']['waist']]
    if any(not con.mute for con in waist.constraints):
        raise ValueError('The Dress waist has an extra constraint. Preserve it before direct posing.')
    _animated(target, [pb.name for pb, _copy, _rotation in result] + [waist.name])
    _constraint_animation(target, source, [pb.name for pb, _copy, _rotation in result] + [waist.name],
                          [rotation for _pb, _copy, rotation in result])
    return result


def _resolve(context, main, entries):
    inventory = _inventory(context, main)
    result = []
    for entry in entries:
        item = inventory.get(entry['owner'])
        if item is None:
            raise ValueError('An Original Dress setup was removed or reassigned; undo that edit before returning.')
        target, source, record = item
        names = {name for chain in record['chains'] for name in chain['def']} | {record['controls']['waist']}
        rest = _rest(target, names)
        parents = {name: target.data.bones[name].parent.name if target.data.bones[name].parent else '' for name in names}
        if set(entry['names']) != names or entry['rest'] != rest or entry['parents'] != parents:
            raise ValueError('The Dress originals were structurally edited; undo that edit before returning.')
        relations = _relations(target, source, record, working=True)
        result.append((entry, target, source, record, relations))
    if set(inventory) != {entry['owner'] for entry in entries}:
        raise ValueError('The Dress setup inventory changed in Original; undo that edit before returning.')
    return result


def validate_active(context, main, entries):
    """Refuse edits to isolated setup state before returning to Controls."""
    for entry, _target, _source, _record, relations in _resolve(context, main, entries):
        for pb, copy, rotation in relations:
            state = entry['constraints'][pb.name]
            if (copy.mix_mode != 'BEFORE_FULL' or copy.mute != state['manual_mute']
                    or rotation.mute != state['physics_mute']):
                raise ValueError('A Dress pose constraint changed in Original; undo that edit before returning.')


def prepare(context, main):
    """Read-only exact ownership/animation preflight before any pose mutation."""
    entries = []
    for owner, (target, source, record) in _inventory(context, main).items():
        from . import bone_display
        bone_display._editable(target)
        relations = _relations(target, source, record)
        evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
        names = sorted({pb.name for pb, _copy, _rot in relations} | {record['controls']['waist']})
        entries.append({'owner': owner, 'names': names,
                        'rest': _rest(target, names),
                        'parents': {name: target.data.bones[name].parent.name if target.data.bones[name].parent else '' for name in names},
                        'channels': {name: _channels(target.pose.bones[name]) for name in names},
                        'locks': {name: _locks(target.pose.bones[name]) for name in names},
                        'constraints': {pb.name: {'manual_mute': copy.mute, 'physics_mute': rot.mute,
                                                  'mix_mode': copy.mix_mode}
                                        for pb, copy, rot in relations},
                        'pose': {name: _values(evaluated.pose.bones[name].matrix) for name in names}})
    return entries


def checkpoint(context, main, entries):
    return [{'entry': entry, 'target': target, 'source': source,
             'channels': {name: _channels(target.pose.bones[name]) for name in entry['names']},
             'locks': {name: _locks(target.pose.bones[name]) for name in entry['names']},
             'constraints': [(copy, copy.mute, copy.mix_mode, rot, rot.mute) for _pb, copy, rot in relations],
             'corrections': source.get(CORRECTIONS)}
            for entry, target, source, _record, relations in _resolve(context, main, entries)]


def rollback(context, state):
    for saved in state:
        for name, value in saved['channels'].items():
            _restore(saved['target'].pose.bones[name], value)
            _restore_locks(saved['target'].pose.bones[name], saved['locks'][name])
        for copy, muted, mode, rotation, physics_mute in saved['constraints']:
            copy.mix_mode, copy.mute, rotation.mute = mode, muted, physics_mute
        if saved['corrections'] is None:
            saved['source'].pop(CORRECTIONS, None)
        else:
            saved['source'][CORRECTIONS] = saved['corrections']
    _update(context, {saved['target'] for saved in state})


def verify(context, main, entries, desired=None):
    worst = 0.0
    for entry, target, _source, _record, _relations in _resolve(context, main, entries):
        expected = desired.get(entry['owner']) if desired is not None else {name: Matrix(value) for name, value in entry['pose'].items()}
        evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
        for name, matrix in expected.items():
            actual = evaluated.pose.bones[name].matrix
            error = _difference(matrix, actual)
            if not all(math.isfinite(value) for row in actual for value in row) or error > 4e-5:
                raise ValueError(f'Dress pose could not be preserved at {name} ({error:.6g}); the switch was rolled back.')
            worst = max(worst, error)
    return worst


def capture(context, main, entries):
    """Capture final evaluated Dress pose before any Body transfer changes."""
    result = {}
    for entry, target, _source, _record, _relations in _resolve(context, main, entries):
        evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
        result[entry['owner']] = {name: evaluated.pose.bones[name].matrix.copy() for name in entry['names']}
    return result


def _local(pb, matrix, parents):
    kwargs = {'parent_matrix': parents.get(pb.parent.name, pb.parent.matrix),
              'parent_matrix_local': pb.parent.bone.matrix_local} if pb.parent else {}
    return pb.bone.convert_local_to_pose(matrix, pb.bone.matrix_local, invert=True, **kwargs)


def _preserve(context, main, entries, wanted):
    """Correct a transfer's small solver change using native local increments.

    The target result retains its existing spline-induced shear. We multiply
    only the relative change into the artist's TRS basis; any result that cannot
    be expressed by those native channels fails strict final validation.
    """
    targets = _resolve(context, main, entries)
    for _attempt in range(3):
        actual = capture(context, main, entries)
        if max((_difference(matrix, actual[owner][name]) for owner, names in wanted.items()
                for name, matrix in names.items()), default=0.0) <= 2e-6:
            break
        assignments = []
        for entry, target, _source, _record, relations in targets:
            evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
            for pb, copy, _rotation in relations:
                epb = evaluated.pose.bones[pb.name]
                current = _local(epb, actual[entry['owner']][pb.name], actual[entry['owner']])
                desired = _local(epb, wanted[entry['owner']][pb.name], wanted[entry['owner']])
                base = (Matrix.Identity(4) if copy.mix_mode == 'REPLACE' and not copy.mute
                        else pb.matrix_basis.copy())
                proposal = base @ current.inverted() @ desired
                if _difference(proposal, base) > 1e-7:
                    assignments.append((pb, copy, proposal))
        if not assignments:
            break
        for pb, copy, proposal in assignments:
            copy.mix_mode = 'BEFORE_FULL'
            pb.matrix_basis = proposal
        _update(context, {target for _entry, target, _source, _record, _relations in targets})


def enter(context, main, entries):
    targets = _resolve(context, main, entries)
    for entry, target, _source, _record, relations in targets:
        correction = _corrections(_source, _record)
        for pb, copy, rotation in relations:
            # REPLACE previously discarded these dormant channels. Identity is
            # the exact neutral native correction; baking final TRS here would
            # lose spline-induced shear and move connected child bones.
            if pb.name not in correction['bones']:
                pb.matrix_basis = Matrix.Identity(4)
            copy.mix_mode = 'BEFORE_FULL'
        for name in entry['names']:
            pb = target.pose.bones[name]
            pb.lock_rotation = (False, False, False)
            pb.lock_rotation_w = pb.lock_rotations_4d = False
    _update(context, {target for _entry, target, _source, _record, _relations in targets})
    _preserve(context, main, entries,
              {entry['owner']: {name: Matrix(value) for name, value in entry['pose'].items()} for entry in entries})
    verify(context, main, entries)
    for entry, target, _source, _record, _relations in targets:
        entry['entered_channels'] = {name: _channels(target.pose.bones[name]) for name in entry['names']}
        entry['entered_basis'] = {pb.name: _values(pb.matrix_basis) for pb, _copy, _rot in _relations}


def leave(context, main, entries, desired=None):
    targets = _resolve(context, main, entries)
    desired = desired if desired is not None else capture(context, main, entries)
    count = 0
    for entry, target, source, record, relations in targets:
        correction = _corrections(source, record)
        for name in entry['names']:
            _restore_locks(target.pose.bones[name], entry['locks'][name])
        for pb, copy, rotation in relations:
            state = entry['constraints'][pb.name]
            copy.mute, rotation.mute = state['manual_mute'], state['physics_mute']
            changed = _difference(pb.matrix_basis, Matrix(entry['entered_basis'][pb.name])) > 1e-7
            if not changed:
                copy.mix_mode = state['mix_mode']
                _restore(pb, entry['channels'][pb.name])
                continue
            if pb.name not in correction['bones']:
                correction['bones'][pb.name] = {'mix_mode': state['mix_mode'],
                                               'channels': entry['channels'][pb.name]}
            copy.mix_mode = 'BEFORE_FULL'
            count += 1
        if correction['bones']:
            source[CORRECTIONS] = json.dumps(correction, separators=(',', ':'))
    _update(context, {target for _entry, target, _source, _record, _relations in targets})
    _preserve(context, main, entries, desired)
    # A transfer compensation is a native local correction too. Preserve its
    # original dormant channels so Clear returns to the original controls.
    for entry, target, source, record, relations in targets:
        correction = _corrections(source, record)
        for pb, copy, _rotation in relations:
            if copy.mix_mode == 'BEFORE_FULL' and pb.name not in correction['bones']:
                correction['bones'][pb.name] = {'mix_mode': entry['constraints'][pb.name]['mix_mode'],
                                               'channels': entry['channels'][pb.name]}
        if correction['bones']:
            source[CORRECTIONS] = json.dumps(correction, separators=(',', ':'))
    verify(context, main, entries, desired)
    return count


def clear(context, main, selected_only=True):
    """Remove saved DEF corrections without changing curve/physics controls."""
    from . import body_original_mode
    if body_original_mode.active(main):
        raise ValueError('Return to Controls before clearing Dress corrections.')
    entries = prepare(context, main)
    state = checkpoint(context, main, entries)
    count = 0
    try:
        for entry, target, source, record, relations in _resolve(context, main, entries):
            correction = _corrections(source, record)
            for pb, copy, _rotation in relations:
                value = correction['bones'].get(pb.name)
                if value is None or selected_only and not (pb.select if hasattr(pb, 'select') else pb.bone.select):
                    continue
                copy.mix_mode = value['mix_mode']
                _restore(pb, value['channels'])
                correction['bones'].pop(pb.name)
                count += 1
            if correction['bones']:
                source[CORRECTIONS] = json.dumps(correction, separators=(',', ':'))
            else:
                source.pop(CORRECTIONS, None)
        _update(context, {saved['target'] for saved in state})
    except Exception:
        rollback(context, state)
        raise
    return count
