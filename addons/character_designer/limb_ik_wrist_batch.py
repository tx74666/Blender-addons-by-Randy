"""Atomic wrist pairs: each hand reaches its own native/FK rotation neutral.

Pair ownership comes from the validated ARM inventory. No raw angle reflection,
FK writes, mirror toggle, selection transfer, mode switch or Action publication.
"""

PLAN_API = 2


def _service():
    from . import limb_ik_wrist_rotation
    return limb_ik_wrist_rotation


def _region(plan):
    return {plan['end'].name}


def _targets(context, target, targets):
    service = _service()
    service._require(target is None or targets is None, 'Choose one wrist or a wrist pair, not both inputs.')
    requested = (target if target is not None else getattr(context, 'active_pose_bone', None),) if targets is None else tuple(targets)
    service._require(len(requested) in (1, 2), 'Choose one or both authenticated IK wrists.')
    admission = service._admission(context, requested[0], mirror_allowed=True)
    _offset, _api, _physics, rig, first, _end, _parent, inventory, first_key, _entry, _variant = admission
    service._require(first_key in {('ARM', 'L'), ('ARM', 'R')}, 'The wrist pair needs authenticated left/right ARM records.')
    if len(requested) == 1 and not service._mirror_flags(rig)[0]:
        return (first,), admission
    other_key = ('ARM', 'R' if first_key[1] == 'L' else 'L')
    record = inventory['rigs'].get(other_key)
    counterpart_name = (record['target'].name if record is not None else
                        _api.limb.LIMB_SPEC['ARM']['target'].format(side=other_key[1]))
    counterpart = rig.pose.bones.get(counterpart_name)
    service._require(counterpart is not None, 'Pose Mirror needs both authenticated IK wrists; build the missing arm first.')
    # The name locates a candidate only. The selected-side validator must
    # authenticate its actual tag/record/native-constraint graph before use.
    opposite = service._resolve(context, rig, _api.limb, _api.match, counterpart, owner=_variant)
    service._require(opposite[4] == other_key, 'The opposite wrist target identity changed.')
    if len(requested) == 2:
        other = requested[1]
        if isinstance(other, str):
            other = rig.pose.bones.get(other)
        service._require(service._same(other, counterpart), 'Select only the authenticated left and right wrist pair.')
    # Stable order, independent of active side or native selection ordering.
    return ((first, counterpart) if first_key[1] == 'L' else (counterpart, first)), admission


def prepare(context, *, target=None, targets=None):
    service = _service()
    wrists, admission = _targets(context, target, targets)
    shared = service._transaction().prepare(context, admission, mirror_allowed=len(wrists) == 2)
    if len(wrists) == 1:
        return service._prepare_single(context, target=wrists[0], _shared=shared)
    sides = tuple(service._prepare_single(context, target=pb, _batch_side=True, _shared=shared) for pb in wrists)
    first, second = sides
    service._require(first['key'] == ('ARM', 'L') and second['key'] == ('ARM', 'R')
        and first['owner'] is second['owner'] and first['rig'] is second['rig'] and first['data'] is second['data']
        and first['action'] is second['action'] and first['slot'] == second['slot']
        and first['frame'] == second['frame'] and first['autokey'] == second['autokey']
        and first['mirror_flags'] == second['mirror_flags'] and not _region(first) & _region(second),
        'The two wrists need the same current owner and distinct authenticated native hands.')
    rotations = {side['target'].name: side['rotation_path'] for side in sides}
    paths = tuple(side['target'].path_from_id(side['rotation_path']) for side in sides)
    for side in sides:
        side['batch_rotations'] = rotations
        side['key_batch_paths'] = paths
    service._transaction().configure(shared, sides)
    desired = {name: matrix for side in sides for name, matrix in side['wanted_native'].items()}
    return dict(batch_api=PLAN_API, phase='prepared', context=context, rig=first['rig'],
        transaction=shared, sides=sides, before_raw=shared['before_raw'],
        mirror_flags=first['mirror_flags'], affected=shared['affected'], wanted_native=desired)


def _validate(context, plan):
    service = _service()
    service._require(type(plan) is dict and plan.get('batch_api') == PLAN_API
        and plan.get('phase') == 'prepared' and plan.get('context') is context,
        'Prepare this exact current wrist pair before applying it.')
    sides = plan['sides']
    service._require(len(sides) == 2 and tuple(side['key'] for side in sides) == (('ARM', 'L'), ('ARM', 'R')),
                     'The prepared wrist pair changed.')
    shared = plan['transaction']
    service._require(all(side['transaction'] is shared for side in sides), 'Prepared wrist transaction ownership changed.')
    service._transaction().validate(context, shared)
    for side in sides:
        service._validate_plan(context, side, _batch=True, _shared_checked=True)
    plan['wanted_native'] = {name: matrix for side in sides for name, matrix in side['wanted_native'].items()}


def _verify(context, plan):
    service = _service()
    return tuple(service._verify(context, side) for side in plan['sides'])


def _restore(context, plan):
    service = _service()
    errors = []
    for side in plan['sides']:
        side['had_key_recovery'] = side.get('key_restore') is not None
    plan['transaction']['had_key_recovery'] = any(side['had_key_recovery'] for side in plan['sides'])
    # Each side owns only its keyed rotation curves; restore both local domains
    # before their exact local proofs. No unrelated channel is read or replayed.
    for side in reversed(plan['sides']):
        try:
            if side.get('key_restore') is not None:
                side['key_restore']()
        except Exception as error:
            errors.append(side['target'].name + ' keys: ' + str(error))
    for side in plan['sides']:
        try:
            if side.get('key_verify') is not None:
                side['key_verify']()
        except Exception as error:
            errors.append(side['target'].name + ' key proof: ' + str(error))
    try:
        service._transaction().restore_rotations(context, plan['transaction'])
    except Exception as error:
        errors.append('target rotations: ' + str(error))
    try:
        service._transaction().verify_recovery(context, plan['transaction'])
    except Exception as error:
        errors.append('local proof: ' + str(error))
    plan['phase'] = 'recovery_needed' if errors else 'rolled_back'
    for side in plan['sides']:
        side['phase'] = plan['phase']
    service._require(not errors, 'Owned wrist-pair recovery needs review: ' + '; '.join(errors))


def apply(context, plan):
    service = _service()
    _validate(context, plan)
    service._transaction().begin(plan['transaction'])
    plan['phase'] = 'applying'
    first = plan['sides'][0]
    try:
        iterations = []
        for index, side in enumerate(plan['sides']):
            iterations.append(service._solve(context, side))
            service._fault('after_batch_first_rotation' if index == 0 else 'after_batch_second_rotation')
        measurements = _verify(context, plan)
        service._fault('after_batch_target_verification')
        for index, side in enumerate(plan['sides']):
            keyed = service._key_rotation(context, side)
            service._require(type(keyed) is bool and keyed == side['autokey'],
                             'Wrist-pair keying did not confirm the requested Auto Key policy.')
            service._fault('after_batch_first_key' if index == 0 else 'after_batch_second_key')
        measurements = _verify(context, plan)
        service._transaction().finish(context, plan['transaction'])
        for side in plan['sides']:
            if side.get('key_commit') is not None:
                side['key_commit']()
        service._transaction().commit(plan['transaction'])
        plan['phase'] = 'applied'
        for side in plan['sides']:
            side['phase'] = 'applied'
        return dict(targets=tuple(side['target'].name for side in plan['sides']),
            keyed=bool(first['autokey']), iterations=tuple(iterations), errors=measurements, phase='applied')
    except Exception as error:
        try:
            _restore(context, plan)
        except Exception as restore_error:
            raise RuntimeError(str(restore_error)) from error
        raise
