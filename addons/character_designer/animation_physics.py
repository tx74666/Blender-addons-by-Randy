"""Character-owned physics permission and reversible native suspension.

The saved JSON is a permission, not proof that a solver is running. A Dress
choice is separate from its applied mode, owned by the existing services.
Suspending Cloth records
its exact native flags; suspending Wiggle mutes only the owned physics bones.
No frame handler, Action, keyframe, Rest, or geometry is created here.
"""
import hashlib
import importlib
import json
import time


STATE_KEY = 'character_designer_animation_physics_v1'
SKIRT_CHOICE = 'character_designer_skirt_motion_choice_v1'
SKIRT_RECORD = 'character_designer_skirt_v1'
SKIRT_RIG = 'character_designer_skirt_armature'
HAIR_RECORD = 'character_designer_hair_bones_v1'
HAIR_RIG = 'character_designer_hair_bones_armature'
HAIR_REGISTRY = 'character_designer_hair_strands_v1'
COMPONENTS = ('SKIRT', 'HAIR')
_LAST_ERRORS = {}


class AnimationPhysicsError(ValueError):
    """A physics permission or suspension cannot be changed safely."""


def _module(name):
    return importlib.import_module(__package__ + '.' + name)


def dependency_status(component, *, refresh=False):
    return _module('physics_dependencies').status(component, refresh=refresh)


def _error_key(rig, component, source=None):
    identity = lambda value: (getattr(value, 'session_uid', value.as_pointer()), value.name)
    return identity(rig), component, identity(source) if source is not None else None


def record_error(context, component, error, *, source_name=None):
    if component not in (*COMPONENTS, 'ALL', 'CONTROL'):
        return
    rig = _selected_rig(context)
    if rig is not None:
        source = context.scene.objects.get(source_name) if source_name else None
        _LAST_ERRORS[_error_key(rig, component, source)] = str(error)


def clear_error(context, component, *, source_name=None):
    rig = _selected_rig(context)
    if rig is not None:
        source = context.scene.objects.get(source_name) if source_name else None
        _LAST_ERRORS.pop(_error_key(rig, component, source), None)


def _last_error(rig, component, source=None):
    return (_LAST_ERRORS.get(_error_key(rig, component, source), '')
            or _LAST_ERRORS.get(_error_key(rig, component), ''))


def enable_dependency(context, component):
    _local(_selected_rig(context), context)
    try:
        return _module('physics_dependencies').enable(component)
    except (ValueError, RuntimeError) as error:
        record_error(context, component, error)
        raise


def _require(value, message):
    if not value:
        raise AnimationPhysicsError(message)


def _fresh():
    return {'version': 1, 'enabled': True, 'skirt_enabled': True,
            'hair_enabled': True, 'suspended': []}


def _read(rig):
    raw = rig.get(STATE_KEY)
    if raw is None:
        return _fresh()
    try:
        state = json.loads(raw)
        _require(set(state) == set(_fresh()) and type(state['version']) is int
                 and state['version'] == 1, 'Invalid physics permission version.')
        _require(all(type(state[key]) is bool for key in
                     ('enabled', 'skirt_enabled', 'hair_enabled'))
                 and type(state['suspended']) is list, 'Invalid physics permission values.')
        seen = set()
        for item in state['suspended']:
            _require(type(item) is dict and item.get('kind') in {'CLOTH', 'DIRECT', 'WIGGLE'}
                     and all(type(item.get(key)) is str and item[key] for key in
                             ('source', 'owner', 'record_sha256')),
                     'Invalid saved physics suspension.')
            _require(item['source'] not in seen, 'Duplicate saved physics suspension.')
            seen.add(item['source'])
            if item['kind'] in {'CLOTH', 'DIRECT'}:
                _require(type(item.get('flags')) is list and len(item['flags']) == 2
                         and all(type(flag) is bool for flag in item['flags'])
                         and all(type(item.get(key)) is str and item[key]
                                 for key in ('proxy', 'modifier')),
                         'Invalid saved Cloth suspension.')
        return state
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise AnimationPhysicsError('Saved Physics permissions need review; preserve this file.') from error


def _write(rig, state):
    rig[STATE_KEY] = json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _selected_rig(context):
    worklist = getattr(context.scene, 'character_designer_animation_worklist', None)
    if worklist is not None and getattr(worklist, 'workspace_path', ''):
        return worklist.rig
    setup = getattr(context.scene, 'character_designer_setup', None)
    return setup.rig if setup is not None else None


def _local(rig, context):
    _require(rig is not None and rig.type == 'ARMATURE', 'Choose Main Rig in Character Setup first.')
    _require(not rig.library and not rig.override_library and rig.is_editable
             and context.scene.objects.get(rig.name) == rig,
             'Physics controls require the local Main Rig in this scene.')


def _owner(source, component):
    key = HAIR_RIG if component == 'HAIR' else SKIRT_RIG
    rig = source.get(key) if source is not None else None
    if component == 'HAIR' and rig is not None and 'character_designer_hair_resegment_mesh' in rig:
        from . import hair_resegment
        return hair_resegment.physics_owner(source)
    if component == 'SKIRT' and rig is not None:
        try:
            record = json.loads(source[SKIRT_RECORD])
            owner = record['owner']
            if record.get('shared'):
                # The source's native ID reference and the Main Rig's own
                # source registry must agree; a parent alone is no authority.
                if rig.get('character_designer_shared_skirts_v1', {}).get(owner) != source:
                    return None
                return rig
            if (rig.get('character_designer_skirt_owner') != owner
                    or rig.get('character_designer_skirt_source') is not source
                    or rig.data.get('character_designer_skirt_owner') != owner):
                return None
        except (ValueError, TypeError, KeyError, AttributeError):
            return None
        parent = getattr(rig, 'parent', None)
        if (parent is not None and getattr(parent, 'type', None) == 'ARMATURE'
                and getattr(rig, 'parent_type', None) == 'BONE'
                and getattr(rig, 'parent_bone', '') in parent.data.bones):
            return parent
    return rig


def _component(source, component=None):
    if component is not None:
        _require(component in COMPONENTS, 'Choose Hair or Skirt physics.')
        return component
    if source is not None and HAIR_RECORD in source:
        return 'HAIR'
    return 'SKIRT'


def is_enabled(source, *, component=None):
    """Read a source's own character permission without selection fallback."""
    selected = _component(source, component)
    rig = _owner(source, selected)
    if rig is None:
        return True  # Existing source validation owns the missing-rig refusal.
    state = _read(rig)
    return state['enabled'] and state[selected.lower() + '_enabled']


def require_enabled(source, context=None, *, component=None):
    selected = _component(source, component)
    _require(is_enabled(source, component=selected),
             'Enable ' + selected.title() + ' Physics in Animation before starting simulation.')


def _raw_record(source):
    raw = source.get(SKIRT_RECORD)
    _require(type(raw) is str, 'The Dress ownership record is missing.')
    try:
        record = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise AnimationPhysicsError('The Dress ownership record needs review.') from error
    _require(type(record) is dict and type(record.get('owner')) is str and record['owner'],
             'The Dress ownership record needs review.')
    return raw, record


def _digest(raw):
    return hashlib.sha256(raw.encode('utf8')).hexdigest()


def _sources(context, rig, component):
    key = HAIR_RECORD if component == 'HAIR' else SKIRT_RECORD
    result = []
    for source in context.scene.objects:
        if getattr(source, 'type', None) != 'MESH' or key not in source:
            continue
        owner = _owner(source, component)
        target = source.get(HAIR_RIG if component == 'HAIR' else SKIRT_RIG)
        if target is rig or (target is not None and getattr(target, 'parent', None) is rig):
            _require(owner is not None, 'A character physics source has invalid attachment ownership.')
        if owner == rig:
            result.append(source)
    if component == 'HAIR' and any('character_designer_hair_resegment_v1' in item for item in result):
        from . import hair_resegment
        return hair_resegment.active_sources(context, rig, tuple(result))
    return tuple(result)


def _dependency_availability(dependency):
    # An unchecked backend may be proved by an explicit Start or Enable/Check.
    # This is permission to try, never evidence of a running preview. Drawing
    # reads this shared dependency cache without hashing or native registration.
    return {'available': dependency['state'] in {'READY', 'UNCHECKED'},
            'reason': dependency.get('message', '')}


def _cached_availability(module, cache_name, time_name):
    cached = getattr(module, cache_name, None)
    stamp = getattr(module, time_name, 0.0)
    if cached is not None and time.monotonic() - stamp < 1.0:
        return cached
    # Drawing must not scan/hash third-party files. A stale/absent cache is
    # unknown, and the explicit activation action runs its full native proof.
    return {'available': True, 'reason': 'Backend compatibility is checked when starting physics.'}


def _mode(source, record):
    if record.get('physics'):
        profiles = _module('skirt_motion_profiles')
        profile = profiles.read(source, record)
        if profile is not None:
            return profile['mode']
        rig_service = _module('skirt_rig')
        holder, _rig, _path = rig_service.physics_control(source)
        return 'AUTOMATIC' if holder.get('physics_influence', 0.0) > 0 else 'MANUAL'
    return _module('skirt_wiggle').mode(source)


def _skirt_choice(source, record, applied_mode=None):
    """Read desired mode without starting a solver or migrating saved files."""
    raw = source.get(SKIRT_CHOICE)
    if raw is None:
        applied_mode = _mode(source, record) if applied_mode is None else applied_mode
        persisted = bool(record.get('physics')) or source.get(_module('skirt_wiggle').STATE_KEY) is not None
        return {'version': 1, 'owner': record['owner'],
                'mode': applied_mode if persisted else 'AUTOMATIC', 'pending': False}
    try:
        choice = json.loads(raw)
        _require(type(choice) is dict and set(choice) == {'version', 'owner', 'mode', 'pending'}
                 and type(choice['version']) is int and choice['version'] == 1
                 and choice['owner'] == record['owner']
                 and choice['mode'] in {'AUTOMATIC', 'MANUAL'} and type(choice['pending']) is bool,
                 'The saved Dress mode choice changed ownership or needs review.')
        return choice
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise AnimationPhysicsError('The saved Dress mode choice needs review; preserve this file.') from error


def _write_skirt_choice(source, choice):
    source[SKIRT_CHOICE] = json.dumps(choice, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _recover_hair_peer_for_skirt(context, rig):
    """Stop only authenticated same-character Hair history before Skirt On.

    Native history can leave produced Hair flags after forgetting its Python
    lease. They are not a running foreign solver, but cannot be ignored. Prove
    every pending peer before the first recovery, then let its own adapter
    reauthenticate at the write boundary. Hair permission is never changed or
    started here. Completed recovery stays stopped if the later skirt fails.
    """
    _local(rig, context)
    hair = _module('hair_wiggle_adapter')
    current = hair.status(context)
    pending = []
    for source in _sources(context, rig, 'HAIR'):
        if (current.get('active') and current.get('source') == source.name
                and current.get('source_pointer') == source.as_pointer()):
            continue  # An existing live Hair owner keeps its exact lease.
        preview = hair.preview_state(source)
        if not (preview.get('pending_recovery') or preview.get('pending')):
            continue  # Unmarked native writers remain subject to foreign guards.
        token = hair.prepare_recovery(context, source)
        _require(token.get('source') is source and token.get('needed'),
                 'The saved Hair owner changed before skirt physics could recover it.')
        pending.append((source, token))
    for source, token in pending:
        receipt = hair.recover_saved(context, source, token=token)
        _require(receipt.get('stopped_confirmed', False),
                 receipt.get('reason') or 'Saved Hair physics could not be confirmed stopped.')
    return tuple(source.name for source, _token in pending)


def _apply_skirt_choice(context, rig, source, record):
    """Apply one explicit pending choice through the existing native guard."""
    _require(_owner(source, 'SKIRT') == rig, 'The selected Dress changed character.')
    _require(not source.library and not source.override_library and source.is_editable,
             'Choose a local editable Dress before changing its mode.')
    choice = _skirt_choice(source, record)
    if not choice['pending']:
        return False
    blocked, _requires_controls = _dress_context_block(context, source)
    if blocked or not is_enabled(source, component='SKIRT'):
        return False
    old_raw = source.get(SKIRT_CHOICE)
    try:
        # Publish before the guarded backend operation, so no additional
        # persistent write can fail after the backend has committed.
        _write_skirt_choice(source, dict(choice, pending=False))
        if record.get('physics'):
            _module('skirt_motion_tuning').apply(context, (source,), mode=choice['mode'])
        else:
            wiggle = _module('skirt_wiggle')
            if choice['mode'] == 'AUTOMATIC':
                _recover_hair_peer_for_skirt(context, rig)
                saved = wiggle._SESSIONS.get(source.as_pointer())
                if saved is not None:
                    _require(saved.get('source') is source and saved.get('scene') == context.scene,
                             'Another Dress session owns this preview; its state was preserved.')
                    _require(not wiggle._changed(saved),
                             'The owned Dress preview changed; preserve it before restarting physics.')
                preview = wiggle.preview_state(source)
                _require(not preview.get('pending_recovery') and not preview.get('cleanup_pending')
                         and getattr(wiggle, '_TEARDOWN_REASON', None) is None,
                         'Dress physics cleanup needs review before restarting its preview.')
            wiggle.set_mode(context, source, choice['mode'])
    except Exception:
        if old_raw is None:
            source.pop(SKIRT_CHOICE, None)
        else:
            source[SKIRT_CHOICE] = old_raw
        raise
    return True


def apply_pending_skirt_modes(context, rig=None, *, include_unset=False, reconcile=False):
    """After permission/Controls returns, try only explicitly queued choices.

    Each backend owns its native rollback. A refused Dress keeps its pending
    choice and source error; it cannot undo a completed Original return or an
    unrelated Dress's successful choice. An explicit Skirt On selects Automatic
    for every owned Dress via include_unset, including a saved Manual choice.
    Successful Controls return may reconcile a previously explicit choice
    changed by Original suspension.
    Drawing never calls this function.
    """
    rig = _selected_rig(context) if rig is None else rig
    result = {'applied': [], 'deferred': [], 'failed': []}
    try:
        _local(rig, context)
        sources = _sources(context, rig, 'SKIRT')
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        if rig is not None:
            try:
                _LAST_ERRORS[_error_key(rig, 'SKIRT')] = str(error)
            except (ReferenceError, AttributeError, TypeError):
                pass  # A removed/invalid ID cannot be given an error owner.
        result['failed'].append({'source': None, 'message': str(error)})
        return result
    for source in sources:
        try:
            _raw, record = _raw_record(source)
            if SKIRT_CHOICE not in source and not include_unset:
                continue
            choice = _skirt_choice(source, record)
            if include_unset:
                # The sole Skirt checkbox enables automatic physics, independent
                # of a prior Rig-side Manual choice. Existing guarded setters
                # own native application, reuse and rollback for every backend.
                if choice['mode'] != 'AUTOMATIC' or not choice['pending']:
                    _require(not source.library and not source.override_library and source.is_editable,
                             'Choose a local editable Dress before changing its mode.')
                    choice = dict(choice, mode='AUTOMATIC', pending=True)
                    _write_skirt_choice(source, choice)
            elif SKIRT_CHOICE not in source:
                continue
            if reconcile and not choice['pending'] and choice['mode'] != _mode(source, record):
                _write_skirt_choice(source, dict(choice, pending=True))
                choice = dict(choice, pending=True)
            if not choice['pending']:
                continue
            if _apply_skirt_choice(context, rig, source, record):
                result['applied'].append(source.name)
                _LAST_ERRORS.pop(_error_key(rig, 'SKIRT', source), None)
            else:
                result['deferred'].append(source.name)
        except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
            _LAST_ERRORS[_error_key(rig, 'SKIRT', source)] = str(error)
            result['failed'].append({'source': source.name, 'message': str(error)})
    return result


def _dress_context_block(context, source):
    if context.mode not in {'OBJECT', 'POSE'}:
        return 'Choose Object or Pose Mode before starting Dress physics.', False
    try:
        # This exact gate reads only saved Original ownership markers. Keep
        # its permission boundary; never finish/restore a session while drawing.
        _module('skirt_rig')._require_controls_for_setup(source)
    except (ValueError, RuntimeError, ReferenceError) as error:
        try:
            # A proved separate Wiggle layer can coexist with direct Original
            # posing. This reads only saved local ownership metadata; native
            # startup retains the complete Rest/constraints/writer proof.
            if _module('skirt_original_mode').wiggle_original_context(source) is not None:
                return '', False
        except (ValueError, RuntimeError, ReferenceError):
            pass
        return 'Dress physics needs Controls mode; finish Original mode in Bone Display first. ' + str(error), True
    return '', False


def _current_hair_source(current, sources):
    return next((source for source in sources
                 if source.name == current.get('source')
                 and source.as_pointer() == current.get('source_pointer')), None)


def control_mode(context, rig=None):
    """Read the actual bone editing/display mode without changing it."""
    rig = _selected_rig(context) if rig is None else rig
    if rig is None:
        return False
    _local(rig, context)
    return not (_module('body_original_mode').active(rig)
                or _module('bone_display').view_mode(rig))


def set_control_mode(context, enabled):
    """Use the existing guarded Original/Controls actions as one toggle."""
    _require(type(enabled) is bool, 'Control Mode must be on or off.')
    rig = _selected_rig(context)
    try:
        _local(rig, context)
        _require(context.mode in {'OBJECT', 'POSE'},
                 'Choose Object or Pose Mode before changing Control Mode.')
        original, display = _module('body_original_mode'), _module('bone_display')
        if enabled:
            if original.active(rig):
                original.leave(context, rig)
            elif display.view_mode(rig):
                display.restore_view(rig)
        elif not original.active(rig):
            # Original's own authenticated transaction handles an existing
            # native Hair/Dress display and preserves edited pose ownership.
            original.enter(context, rig)
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        record_error(context, 'CONTROL', error)
        raise
    clear_error(context, 'CONTROL')
    return status(context)


def status(context):
    """Lightweight saved-state drawing; never validate/evaluate a mesh."""
    rig = _selected_rig(context)
    result = dict(_fresh(), rig=rig, valid=False, dresses=[], hair={'sources': [], 'active': False,
                                                   'paused': False, 'running': False,
                                                   'available': False, 'message': ''}, message='',
                  control_mode=False, control_error='')
    if rig is None:
        result['message'] = 'Choose Main Rig in Character Setup first.'
        return result
    try:
        _local(rig, context)
        result.update(_read(rig))
        result['control_mode'] = control_mode(context, rig)
        result['control_error'] = _last_error(rig, 'CONTROL')
        result['playing'] = bool(getattr(getattr(context, 'screen', None), 'is_animation_playing', False))
        result['dependencies'] = {part: dependency_status(part) for part in COMPONENTS}
        for source in _sources(context, rig, 'SKIRT'):
            raw, record = _raw_record(source)
            cloth = bool(record.get('physics'))
            dependency = None if cloth else result['dependencies']['SKIRT']
            availability = ({'available': True, 'reason': ''} if cloth else
                            _dependency_availability(dependency))
            applied_mode = _mode(source, record)
            choice = _skirt_choice(source, record, applied_mode)
            mode = choice['mode']
            context_message, requires_controls = _dress_context_block(context, source)
            active, paused, running = False, False, False
            message = ''
            if not cloth:
                wiggle = _module('skirt_wiggle')
                preview = wiggle.preview_state(source)
                active, paused, running = (bool(preview.get(key, False)) for key in ('active', 'paused', 'running'))
                saved = wiggle._state(source) or {}
                reason = preview.get('message') or saved.get('reason', '')
                if not active and applied_mode == 'AUTOMATIC' and not reason:
                    reason = 'Automatic is stopped. Choose Automatic to start.'
                message = reason
            else:
                allowed = result['enabled'] and result['skirt_enabled']
                paused = not allowed
                # Cloth has no runtime lease. Read only its saved endpoint's
                # native visibility here; its action retains the full guard.
                proxy = context.scene.objects.get(record['physics'].get('proxy', ''))
                cloth_modifiers = [modifier for modifier in getattr(proxy, 'modifiers', ())
                                   if modifier.type == 'CLOTH']
                active = applied_mode == 'AUTOMATIC' and len(cloth_modifiers) == 1
                running = bool(active and allowed and cloth_modifiers[0].show_viewport)
            result['dresses'].append({'source': source.name, 'name': source.name,
                'owner': record['owner'], 'mode': mode, 'applied_mode': applied_mode,
                'mode_pending': choice['pending'], 'active': active,
                'paused': paused, 'running': running,
                'stopped_confirmed': bool(preview.get('stopped_confirmed', False)) if not cloth else not running,
                'pending_recovery': bool(preview.get('pending_recovery', False)) if not cloth else False,
                'backend': record['physics'].get('backend', 'LEGACY_CAGE') if cloth else 'WIGGLE',
                'available': availability['available'] and not context_message, 'message': message,
                'context_blocked': bool(context_message), 'context_message': context_message,
                'mode_blocked': bool(context_message), 'block_reason': context_message,
                'requires_controls': requires_controls,
                'can_change_mode': bool(not source.library and not source.override_library and source.is_editable),
                'dependency': dependency, 'last_error': _last_error(rig, 'SKIRT', source),
                'can_change_mode_paused': True})
        hairs = _sources(context, rig, 'HAIR')
        hair = _module('hair_wiggle_adapter')
        current = hair.status(context)
        dependency = result['dependencies']['HAIR']
        availability = _dependency_availability(dependency)
        active_source = _current_hair_source(current, hairs)
        active = bool(current.get('active') and active_source is not None)
        paused = bool(active and current.get('paused'))
        cleanup_pending = bool(active and current.get('cleanup_pending'))
        previews = [hair.preview_state(source) for source in hairs]
        hair_reason = current.get('reason', '') if current.get('source_pointer') in {
            source.as_pointer() for source in hairs} else ''
        result['hair'] = {'sources': [source.name for source in hairs],
            'active': active, 'paused': paused, 'running': active and not paused and not cleanup_pending,
            'cleanup_pending': cleanup_pending,
            'stopped_confirmed': all(preview.get('stopped_confirmed', False) for preview in previews),
            'pending_recovery': any(preview.get('pending_recovery', preview.get('pending', False)) for preview in previews),
            'available': availability['available'], 'message': hair_reason or ' '.join(dict.fromkeys(
                preview.get('message', preview.get('reason', '')) for preview in previews
                if preview.get('message', preview.get('reason', '')))),
            'backend': current.get('backend', 'WIGGLE2'), 'version': current.get('version'),
            'dependency': dependency, 'last_error': _last_error(rig, 'HAIR', hairs[0] if len(hairs) == 1 else None),
            'source_errors': {source.name: _last_error(rig, 'HAIR', source) for source in hairs}}
        if not result['enabled']:
            result['message'] = 'Physics permission is off. Saved Dress modes are retained.'
        if _last_error(rig, 'ALL'):
            result['message'] = _last_error(rig, 'ALL')
        result['valid'] = True
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError, KeyError) as error:
        result['message'] = str(error)
    return result


def _entry(source, record, kind, **extra):
    return dict(kind=kind, source=source.name, owner=record['owner'],
                record_sha256=_digest(source[SKIRT_RECORD]), **extra)


def return_to_controls(context):
    """User-invoked equivalent of Bone Display's existing Controls action."""
    try:
        rig = _selected_rig(context)
        _local(rig, context)
        _require(context.mode in {'OBJECT', 'POSE'}, 'Choose Object or Pose Mode before returning to Controls.')
        original, display = _module('body_original_mode'), _module('bone_display')
        if original.active(rig):
            # Keep run_return's complete ownership/Rest/rollback policy. This
            # action does not forge a session, restore history, or grant an
            # old Action new permissions.
            original.leave(context, rig)
        elif display.view_mode(rig):
            display.restore_view(rig)
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        record_error(context, 'SKIRT', error)
        raise
    clear_error(context, 'SKIRT')
    return status(context)


def _source_for_entry(context, rig, item):
    source = context.scene.objects.get(item['source'])
    _require(source is not None, 'A suspended Dress was removed.')
    raw, record = _raw_record(source)
    _require(record['owner'] == item['owner'] and _digest(raw) == item['record_sha256'],
             'A suspended Dress setup changed. Preserve it before resuming Physics.')
    _require(_owner(source, 'SKIRT') == rig,
             'A suspended Dress moved to another character.')
    return source, record


def _flags(modifier):
    return [bool(modifier.show_viewport), bool(modifier.show_render)]


def _set_flags(modifier, flags):
    modifier.show_viewport, modifier.show_render = flags
    _require(_flags(modifier) == list(flags), 'Blender did not apply the owned Cloth flags.')


def _recovery_action(context, source, component):
    adapter = _module('skirt_wiggle' if component == 'SKIRT' else 'hair_wiggle_adapter')
    token = adapter.prepare_recovery(context, source)
    pending = token.get('needed', token.get('changed', False))
    _require(pending or token.get('stopped_confirmed'),
             token.get('reason') or 'Saved physics cannot yet be confirmed stopped.')
    return [('RECOVER', source, {'component': component, 'token': token, 'receipt': None}, None, None)] if pending else []


def _wiggle_pause_action(source, saved, paused):
    wiggle = _module('skirt_wiggle')
    _require(not wiggle._changed(saved), 'The Dress simulation changed; choose its mode again first.')
    return ('WIGGLE', source, paused, bool(saved.get('paused', False)),
            (wiggle.STATE_KEY, source.get(wiggle.STATE_KEY)))


def _prepare_dress_pause(context, rig, state):
    actions, entries = [], []
    wiggle, physics = _module('skirt_wiggle'), _module('skirt_physics')
    for source in _sources(context, rig, 'SKIRT'):
        _raw, record = _raw_record(source)
        # Permission Off pauses owned physics without changing Dress setup or
        # leaving Original. Each native branch still proves its own ownership.
        if record.get('physics'):
            record, _target, proxy, cloth = physics.validate_physics(source)
            _require(not cloth.point_cache.is_baking, 'Finish the Dress bake before pausing Physics.')
            kind = 'DIRECT' if record['physics'].get('backend') == 'DIRECT_MAIN_CLOTH_V1' else 'CLOTH'
            item = _entry(source, record, kind, proxy=proxy.name,
                          modifier=cloth.name, flags=_flags(cloth))
            entries.append(item)
            if kind == 'DIRECT':
                actions.append(('DIRECT', source, record, None, None))
            else:
                actions.append(('CLOTH', cloth, [False, False], item['flags'], source))
        else:
            saved = wiggle._SESSIONS.get(source.as_pointer())
            if saved is not None:
                item = _entry(source, record, 'WIGGLE')
                entries.append(item)
                actions.append(_wiggle_pause_action(source, saved, True))
            else:
                actions.extend(_recovery_action(context, source, 'SKIRT'))
    return actions, entries


def _prepare_dress_off_recovery(context, rig, after):
    """An explicit Off also handles proved redo residue while already off."""
    actions = []
    for source in _sources(context, rig, 'SKIRT'):
        _raw, record = _raw_record(source)
        if record.get('physics'):
            continue  # Already-paused Cloth keeps its original suspension.
        saved = _module('skirt_wiggle')._SESSIONS.get(source.as_pointer())
        if saved is None:
            actions.extend(_recovery_action(context, source, 'SKIRT'))
        else:
            actions.append(_wiggle_pause_action(source, saved, True))
            if not any(item['source'] == source.name for item in after['suspended']):
                after['suspended'] = [*after['suspended'], _entry(source, record, 'WIGGLE')]
    return actions


def _prepare_dress_resume(context, rig, entries):
    actions = []
    for item in entries:
        source, record = _source_for_entry(context, rig, item)
        if item['kind'] == 'WIGGLE':
            wiggle = _module('skirt_wiggle')
            saved = wiggle._SESSIONS.get(source.as_pointer())
            # Save/Undo may end a preview. Restoring permission does not replay
            # that completed session or implicitly choose its motion mode.
            if saved is not None:
                actions.append(_wiggle_pause_action(source, saved, False))
            continue
        proxy = context.scene.objects.get(item['proxy'])
        cloth = proxy.modifiers.get(item['modifier']) if proxy is not None else None
        _require(cloth is not None and cloth.type == 'CLOTH' and _flags(cloth) == [False, False],
                 'A paused Cloth modifier changed; its external edit was preserved.')
        _require(not cloth.point_cache.is_baking, 'Finish the Dress bake before resuming Physics.')
        if item['kind'] == 'DIRECT':
            # Direct's runtime also owns its typed output socket. Validate the
            # old, gated endpoint, then synchronize all native endpoints after
            # publishing the new permission; never force stale Cloth flags.
            _module('skirt_physics').validate_physics(source)
            actions.append(('DIRECT', source, record, None, None))
        else:
            _paused_cloth_ownership(source, record, proxy, cloth)
            actions.append(('CLOTH', cloth, item['flags'], [False, False], source))
    return actions


def _paused_cloth_ownership(source, record, proxy, cloth):
    """Prove the exact owned native writer before any resume setter.

    The ordinary full graph validator still runs after restoring its expected
    visibility. This preflight excludes only those two deliberately paused
    Cloth flags; it does not borrow authority from the saved helper name.
    """
    physics = _module('skirt_physics')
    physics._owned_mesh(proxy, source, record)
    details = record['physics']
    _require(details.get('proxy') == proxy.name,
             'The suspended Cloth is not this Dress physics endpoint.')
    target = source.get(SKIRT_RIG)
    modifiers = list(proxy.modifiers)
    backend = details.get('backend', 'LEGACY_CAGE')
    if backend == 'ACTUAL_SURFACE_DELTA_V1':
        surface = details.get('surface', {})
        _require(surface.get('roles', {}).get('CLOTH_PROXY') == [proxy.name],
                 'The suspended Cloth no longer has its exact surface role.')
        service = _module('skirt_surface')
        _require(service._helper_contract(proxy, dynamic_pin=True)
                 == surface.get('contracts', {}).get(proxy.name),
                 'The suspended Cloth native binding was edited.')
        expected_types = ['ARMATURE', 'SURFACE_DEFORM', 'CLOTH']
    else:
        _require(backend == 'LEGACY_CAGE', 'Unknown suspended Cloth backend.')
        expected_types = ['ARMATURE', 'CLOTH']
    _require([modifier.type for modifier in modifiers] == expected_types
             and modifiers[-1] == cloth and sum(modifier.type == 'CLOTH' for modifier in modifiers) == 1
             and all(modifier.show_viewport and modifier.show_render for modifier in modifiers[:-1])
             and proxy.parent == target and proxy.parent_type == 'OBJECT' and not proxy.parent_bone,
             'The suspended Cloth modifier order or attachment changed.')
    armature = modifiers[0]
    _require(armature.object == target and armature.use_vertex_groups
             and not armature.use_bone_envelopes and not armature.vertex_group
             and not armature.use_multi_modifier and armature.show_viewport and armature.show_render
             and record['controls']['waist'] in target.data.bones,
             'The suspended Cloth native bone binding changed.')


def _hair_action(context, rig, pause):
    hair = _module('hair_wiggle_adapter')
    current = hair.status(context)
    sources = _sources(context, rig, 'HAIR')
    source = _current_hair_source(current, sources)
    if source is None or not current.get('active'):
        if pause:
            return [action for candidate in sources for action in _recovery_action(context, candidate, 'HAIR')]
        return []
    return [('HAIR', source, pause, bool(current.get('paused', False)), None)]


def _perform(context, action, *, reverse=False):
    kind, target, after, before, source = action
    if kind == 'CLOTH':
        _set_flags(target, before if reverse else after)
        if not reverse and after != [False, False]:
            _module('skirt_physics').validate_physics(source)
    elif kind == 'DIRECT':
        service = _module('skirt_surface_direct')
        service._sync(target, after, service._state(target, after))
        _module('skirt_physics').validate_physics(target)
    elif kind == 'WIGGLE':
        result = _module('skirt_wiggle').set_paused(context, target, before if reverse else after)
        if isinstance(result, dict):
            _require(not result.get('backend_error') and result.get('available') is not False,
                     result.get('backend_error') or result.get('reason') or result.get('message')
                     or 'The Wiggle backend cannot confirm the requested pause state.')
        if reverse:
            key, raw = source
            if raw is None:
                if key in target:
                    del target[key]
            else:
                target[key] = raw
    elif kind == 'HAIR':
        _module('hair_wiggle_adapter').set_preview_paused(context, target, before if reverse else after)
    elif kind == 'RECOVER':
        adapter = _module('skirt_wiggle' if after['component'] == 'SKIRT' else 'hair_wiggle_adapter')
        if reverse:
            if after['receipt'] is not None:
                adapter.rollback_recovery(context, after['receipt'])
        else:
            after['receipt'] = adapter.recover_saved(context, target, token=after['token'])
            _require(after['receipt'].get('stopped_confirmed', False),
                     after['receipt'].get('reason') or 'Saved physics could not be confirmed stopped.')


def _transition(context, rig, after, *, recover_parts=()):
    _local(rig, context)
    before = _read(rig)
    old_allowed = {part: before['enabled'] and before[part.lower() + '_enabled'] for part in COMPONENTS}
    new_allowed = {part: after['enabled'] and after[part.lower() + '_enabled'] for part in COMPONENTS}
    actions = []
    if old_allowed['SKIRT'] and not new_allowed['SKIRT']:
        _require(not before['suspended'], 'An earlier Dress suspension needs review.')
        dress_actions, entries = _prepare_dress_pause(context, rig, before)
        actions.extend(dress_actions)
        after['suspended'] = entries
    elif not old_allowed['SKIRT'] and new_allowed['SKIRT']:
        actions.extend(_prepare_dress_resume(context, rig, before['suspended']))
        after['suspended'] = []
    elif 'SKIRT' in recover_parts and not new_allowed['SKIRT']:
        actions.extend(_prepare_dress_off_recovery(context, rig, after))
    if old_allowed['HAIR'] != new_allowed['HAIR']:
        actions.extend(_hair_action(context, rig, not new_allowed['HAIR']))
    elif 'HAIR' in recover_parts:
        actions.extend(_hair_action(context, rig, not new_allowed['HAIR']))
    old_raw = rig.get(STATE_KEY)
    touched = []
    try:
        _write(rig, after)
        for action in actions:
            touched.append(action)
            _perform(context, action)
        context.view_layer.update()
    except Exception as error:
        if old_raw is None:
            if STATE_KEY in rig:
                del rig[STATE_KEY]
        else:
            rig[STATE_KEY] = old_raw
        failures = []
        for action in reversed(touched):
            try:
                _perform(context, action, reverse=True)
            except Exception as rollback_error:
                failures.append(str(rollback_error))
        if failures:
            raise AnimationPhysicsError('Physics change failed; rollback needs review: ' + '; '.join(failures)) from error
        raise
    return status(context)


def set_enabled(context, enabled):
    _require(type(enabled) is bool, 'Enable Physics must be checked or unchecked.')
    rig = _selected_rig(context)
    _local(rig, context)
    after = _read(rig)
    after['enabled'] = enabled
    try:
        result = _transition(context, rig, after, recover_parts=COMPONENTS if not enabled else ())
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        record_error(context, 'ALL', error)
        raise
    clear_error(context, 'ALL')
    if enabled:
        apply_pending_skirt_modes(context, rig)
        result = status(context)
    result['message'] = '' if enabled else 'Physics permission is off. Saved Dress modes are retained.'
    return result


def set_component(context, component, enabled):
    _require(component in COMPONENTS and type(enabled) is bool, 'Choose Hair or Skirt physics.')
    rig = _selected_rig(context)
    _local(rig, context)
    old_raw = rig.get(STATE_KEY)
    before = _read(rig)
    after = dict(before)
    after[component.lower() + '_enabled'] = enabled
    try:
        if component == 'HAIR' and enabled and after['enabled']:
            # Recover only this explicit Hair request before permission resume
            # can touch an already-released session. Completed authenticated
            # cleanup stays stopped if a later start/transition is refused.
            _recover_hair_for_start(context, rig)
        result = _transition(context, rig, after,
                             recover_parts=(component,) if component == 'HAIR' or not enabled else ())
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        record_error(context, component, error)
        raise
    if component == 'HAIR' and enabled and after['enabled']:
        if not result['hair']['active']:
            try:
                start_hair_preview(context)
            except Exception as error:
                # The existing adapter owns its start rollback. Restore only
                # this permission after a refused start, never stop a foreign
                # session or make the checkbox claim it started successfully.
                try:
                    _transition(context, rig, before)
                except Exception as rollback_error:
                    raise AnimationPhysicsError('Hair preview was refused; permission rollback needs review: '
                                                + str(rollback_error)) from error
                if old_raw is None:
                    if STATE_KEY in rig:
                        del rig[STATE_KEY]
                else:
                    rig[STATE_KEY] = old_raw
                raise
            result = status(context)
    clear_error(context, component)
    if component == 'SKIRT' and enabled:
        apply_pending_skirt_modes(context, rig, include_unset=True)
    return status(context)


def start_hair_preview(context):
    source_name = None
    try:
        rig = _selected_rig(context)
        if rig is not None:
            sources = _sources(context, rig, 'HAIR')
            source_name = sources[0].name if len(sources) == 1 else None
        result = _start_hair_preview(context)
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        record_error(context, 'HAIR', error, source_name=source_name)
        raise
    clear_error(context, 'HAIR', source_name=source_name)
    clear_error(context, 'HAIR')
    result['hair']['last_error'] = _last_error(_selected_rig(context), 'HAIR', sources[0] if len(sources) == 1 else None)
    return result


def _recover_hair_for_start(context, rig):
    """Finish only owned Hair cleanup/history recovery on an explicit On."""
    hair = _module('hair_wiggle_adapter')
    sources = _sources(context, rig, 'HAIR')
    current = hair.status(context)
    source = _current_hair_source(current, sources) if current.get('active') else None
    if current.get('active'):
        _require(source is not None,
                 'Another Hair preview is active; finish that preview first.')
        if not current.get('cleanup_pending'):
            return False
    else:
        _require(len(sources) == 1,
                 'Choose one Hair source in Rig before enabling Hair physics.')
        source = sources[0]
        preview = hair.preview_state(source)
        if not (preview.get('pending_recovery') or preview.get('pending')
                or not preview.get('stopped_confirmed', True)):
            return False
    # This action authenticates the exact source/registry/native ownership and
    # retries partial release through the existing adapter. Never resurrect a
    # history marker or released lease merely to undo a later failed restart.
    stop_hair_preview(context)
    return True


def _start_hair_preview(context):
    """Explicitly start the selected character's sole initialized Hair source."""
    rig = _selected_rig(context)
    _local(rig, context)
    state = _read(rig)
    _require(state['enabled'] and state['hair_enabled'],
             'Enable Hair Physics in Animation before starting its preview.')
    _recover_hair_for_start(context, rig)
    sources = _sources(context, rig, 'HAIR')
    _require(len(sources) == 1, 'Choose one Hair source in Rig to start its physics preview.')
    source = sources[0]
    hair = _module('hair_wiggle_adapter')
    current = hair.status(context)
    if current.get('active'):
        _require(_current_hair_source(current, sources) == source,
                 'Another Hair preview is active; finish that preview first.')
        hair.resume_preview(context, source)
        return status(context)
    registry = _module('hair_strand_registry').read(source, validate=True)
    _require(registry and registry.get('strands'), 'Initialize Hair strand motion before enabling its preview.')
    profiles = _module('hair_motion_profiles').effective_all(source, registry=registry)
    hair.start_preview(context, source, registry, profiles,
                       [item['strand_id'] for item in registry['strands']])
    return status(context)


def stop_hair_preview(context):
    """Explicitly stop this character's Hair owner without cancelling playback."""
    source_name = None
    try:
        rig = _selected_rig(context)
        _local(rig, context)
        sources = _sources(context, rig, 'HAIR')
        hair = _module('hair_wiggle_adapter')
        current = hair.status(context)
        source = _current_hair_source(current, sources) if current.get('active') else None
        if current.get('active'):
            _require(source is not None, 'Another Hair preview is active; its state was preserved.')
        else:
            _require(len(sources) == 1, 'Choose one Hair source before stopping its saved preview.')
            source = sources[0]
        source_name = source.name
        hair.stop_preview(context, source=source)
        _require(not hair.status(context).get('active'),
                 'Hair preview cleanup did not finish; retry Stop Hair Preview and review its details.')
        _require(hair.preview_state(source).get('stopped_confirmed', False),
                 'Hair physics cannot yet be confirmed stopped; preserve its settings and review the details.')
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        record_error(context, 'HAIR', error, source_name=source_name)
        raise
    clear_error(context, 'HAIR', source_name=source_name)
    clear_error(context, 'HAIR')
    return status(context)


def set_skirt_mode(context, source_name, mode, *, expected_owner=''):
    try:
        result = _set_skirt_mode(context, source_name, mode, expected_owner=expected_owner)
    except (ValueError, RuntimeError, ReferenceError, AttributeError, TypeError) as error:
        record_error(context, 'SKIRT', error, source_name=source_name)
        raise
    clear_error(context, 'SKIRT', source_name=source_name)
    clear_error(context, 'SKIRT')
    for row in result['dresses']:
        if row['source'] == source_name:
            row['last_error'] = _last_error(_selected_rig(context), 'SKIRT', context.scene.objects.get(source_name))
    return result


def _set_skirt_mode(context, source_name, mode, *, expected_owner=''):
    _require(mode in {'AUTOMATIC', 'MANUAL'}, 'Choose Automatic or Manual Dress motion.')
    rig = _selected_rig(context)
    _local(rig, context)
    source = context.scene.objects.get(source_name)
    _require(source is not None and _owner(source, 'SKIRT') == rig,
             'The selected Dress changed character.')
    _require(context.mode in {'OBJECT', 'POSE'}, 'Choose Object or Pose Mode before choosing Dress motion.')
    _require(not source.library and not source.override_library and source.is_editable,
             'Choose a local editable Dress before changing its mode.')
    _raw, record = _raw_record(source)
    _require(not expected_owner or record['owner'] == expected_owner, 'The selected Dress setup changed.')
    # A preference is always selectable. Original/permission only gate the
    # native application; they never force a Bone Display transition here.
    _skirt_choice(source, record)  # Refuse corrupt or foreign saved choices.
    old_raw = source.get(SKIRT_CHOICE)
    try:
        _write_skirt_choice(source, {'version': 1, 'owner': record['owner'], 'mode': mode, 'pending': True})
        _apply_skirt_choice(context, rig, source, record)
    except Exception:
        if old_raw is None:
            source.pop(SKIRT_CHOICE, None)
        else:
            source[SKIRT_CHOICE] = old_raw
        raise
    return status(context)
