"""Operation-local wrist writes; no animation/Source or full-rig snapshots.

Only authenticated target rotations and their explicitly keyed local curves are
owned by this transaction. Other content is neither fingerprinted nor replayed.
"""

PLAN_API = 2


def _service():
    from . import limb_ik_wrist_rotation
    return limb_ik_wrist_rotation


def prepare(context, admission, *, mirror_allowed=False):
    service = _service()
    offset, api, _physics, rig, target, _end, _forearm, inventory, _key, _entry, owner = admission
    service._require(type(owner) is dict, 'Authenticate the current local wrist owner before preparing it.')
    action, slot = owner['action'], owner['slot']
    return dict(transaction_api=PLAN_API, phase='prepared', context=context, api=api, offset=offset,
        rig=rig, data=rig.data, primary_target=target, inventory=inventory,
        mirror_allowed=bool(mirror_allowed), mirror_flags=service._mirror_flags(rig),
        owner=owner, variant=owner, action=action, slot=slot,
        frame=service._frame(context), autokey=bool(context.scene.tool_settings.use_keyframe_insert_auto),
        context_mode=context.mode, active=context.view_layer.objects.active,
        before_raw={}, affected=None, sides=(), had_key_recovery=False)


def configure(shared, sides):
    service = _service()
    service._require(shared['phase'] == 'prepared' and shared['affected'] is None
                     and len(sides) in (1, 2), 'Configure one fresh local wrist transaction.')
    service._require(all(side['transaction'] is shared for side in sides), 'Wrist transaction ownership differs.')
    shared['sides'] = tuple(sides)
    shared['before_raw'] = {side['target'].name: side['target_raw'] for side in sides}
    shared['affected'] = frozenset((side['target'].name, side['rotation_path'], index)
        for side in sides for index in range(side['rotation_size']))
    for side in sides:
        side['before_raw'] = shared['before_raw']


def _current_context(context, shared):
    service = _service()
    service._require(type(shared) is dict and shared.get('transaction_api') == PLAN_API
        and shared.get('context') is context, 'Prepare this exact current wrist operation first.')
    rig = shared['rig']
    service._require(service._same(getattr(context, 'object', None), rig) and rig.data is shared['data']
        and context.mode == shared['context_mode'] and service._same(context.view_layer.objects.active, shared['active']),
        'The current wrist rig, data or mode changed.')
    service._require(service._frame(context) == shared['frame']
        and bool(context.scene.tool_settings.use_keyframe_insert_auto) == shared['autokey']
        and service._mirror_flags(rig) == shared['mirror_flags'],
        'The frame, Auto Key or mirror options changed after wrist preparation.')


def validate(context, shared):
    service = _service()
    _current_context(context, shared)
    service._require(shared['phase'] == 'prepared' and shared['sides'], 'This wrist transaction is no longer prepared.')
    service._owner_guard().validate(context, shared['owner'])
    shared['owner'].pop('_wrist_registry', None)
    service._input_policy(context, shared['rig'], mirror_allowed=shared['mirror_allowed'])
    service._unchanged_inputs(shared['sides'][0], allow_target_rotation=False)
    shared['phase'] = 'validated'


def begin(shared):
    _service()._require(shared['phase'] == 'validated', 'Validate the current local wrist transaction before writing.')
    shared['phase'] = 'applying'


def finish(context, shared):
    service = _service()
    _current_context(context, shared)
    service._require(shared['phase'] == 'applying', 'This wrist transaction is not applying.')
    service._owner_guard().validate(context, shared['owner'])
    shared['owner'].pop('_wrist_registry', None)
    for side in shared['sides']:
        service._owner_guard().resolve(context, shared['rig'], side['target'], shared['owner'])
    service._unchanged_inputs(shared['sides'][0], allow_target_rotation=True)
    shared['phase'] = 'verified'


def commit(shared):
    _service()._require(shared['phase'] == 'verified', 'Complete the local wrist checks before committing.')
    shared['phase'] = 'complete'


def restore_rotations(context, shared):
    """Restore only written or locally keyed target rotations, never a pose."""
    service = _service()
    errors = []
    changed = False
    # Settle only the native evaluation invalidated by local key restoration
    # before reasserting the target rotations this operation wrote or keyed.
    # Key insertion/restoration can reevaluate an already-neutral target even
    # when the solver itself made no assignment. Only that armed side owns
    # this induced change; an unkeyed no-op side remains untouched.
    # No frame, binding, mode, permission or other pose input is replayed.
    if shared.pop('had_key_recovery', False):
        try:
            shared['api'].match._update(context, shared['rig'])
        except Exception as error:
            errors.append('restored-key native update: ' + str(error))
    for side in reversed(shared['sides']):
        if not (side['written_rotation'] or side.get('had_key_recovery', False)):
            continue
        try:
            target = side['target']
            service._require(shared['rig'].data is shared['data']
                and service._same(shared['rig'].pose.bones.get(target.name), target)
                and service._same(target.bone, side['target_bone'])
                and target.rotation_mode == side['rotation_mode'],
                'The written wrist target identity or rotation representation changed; keep current data for recovery.')
            setattr(target, side['rotation_path'], side['target_raw'][side['rotation_path']])
            side['written_rotation'] = False
            side['had_key_recovery'] = False
            changed = True
        except Exception as error:
            errors.append(side['target'].name + ': ' + str(error))
    if changed:
        try:
            shared['api'].match._update(context, shared['rig'])
        except Exception as error:
            errors.append('native update: ' + str(error))
    service._require(not errors, 'Local wrist rotation recovery needs review: ' + '; '.join(errors))


def verify_recovery(context, shared):
    service = _service()
    _current_context(context, shared)
    service._owner_guard().validate(context, shared['owner'])
    service._unchanged_inputs(shared['sides'][0], allow_target_rotation=False)
    shared['phase'] = 'rolled_back'
