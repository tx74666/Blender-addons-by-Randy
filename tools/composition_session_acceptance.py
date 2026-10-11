"""Native composition UI/session checks run by the isolated serial driver.

This module starts no process, opens no artist file, and imports Blender only
when ``run`` is explicitly called inside the parent's dedicated test copy.
Failures write their phase and traceback before being raised to the driver.
"""
from contextlib import contextmanager
import hashlib
import importlib
import json
import math
from pathlib import Path
import traceback


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require(value, message):
    if not value:
        raise AssertionError(message)


def _write(path, report):
    temporary = path.with_suffix('.tmp.json')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def _evidence(config):
    directory = Path(config['evidenceDirectory']).resolve()
    _require(directory.is_absolute() and directory.is_dir(), 'Create the dedicated evidence directory before session checks.')
    paths = {name: directory / filename for name, filename in (
        ('report', 'session_acceptance_report.json'),
        ('active', 'composition_active_session.blend'),
        ('closed', 'composition_closed_session.blend'),
        ('a_edited', 'composition_A_workcopy_edited.blend'))}
    _require(all(path.is_relative_to(directory) and len(str(path)) < 260 for path in paths.values()),
             'Session evidence paths must remain inside the dedicated directory and below Windows MAX_PATH.')
    return paths


def _modules(config):
    package = config.get('addonModule', 'character_designer')
    return {name: importlib.import_module(package + '.' + name) for name in (
        'animation_composition', 'animation_worklist', 'unity_animation', 'animation_link',
        'animation_worklist_new', 'animation_composition_export', 'animation_composition_weapon')}


def _current(item_id, modules):
    import bpy
    context = bpy.context
    saved = modules['animation_worklist'].state(context)
    _require(sum(row.item_id == item_id for row in saved.items) == 1,
             'The exact native B row is missing or ambiguous after collection growth or Undo/reopen.')
    item = modules['animation_worklist']._item(saved, item_id)
    _require(item.rig is saved.rig and item.rig.name in context.scene.objects,
             'The exact B row and Main Rig must belong to the current saved Scene.')
    saved.active_index = next(i for i, row in enumerate(saved.items) if row.item_id == item_id)
    return context, saved, item


_B_ROW_FIELDS = ('item_id', 'work_item_id', 'clip_key', 'manifest_path', 'link_identity',
                 'model_file', 'model_sha256', 'workspace_path', 'workspace_identity',
                 'source_file', 'source_hash', 'composition_json', 'composition_sha256',
                 'application_role', 'edit_scope')


def _pose(context, rig, names):
    context.view_layer.update()
    evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
    result = {}
    for name in names:
        bone = evaluated.pose.bones[name]
        local = bone.parent.matrix.inverted() @ bone.matrix if bone.parent else bone.matrix
        values = [float(value) for row in local for value in row]
        _require(all(math.isfinite(value) for value in values), 'Nonfinite evaluated native local transform: ' + name)
        result[name] = values
    return result


def _error(left, right, names):
    _require(set(names) <= set(left) & set(right), 'An expected native pose was not sampled.')
    return max((abs(a - b) for name in names for a, b in zip(left[name], right[name])), default=0.)


def _a_only(context, session, tracks, names, frame, modules):
    """Read the exact protected A alone, restoring owned native state in finally."""
    backend, ua = modules['animation_composition'], modules['unity_animation']
    rig, ad = session['rig'], session['rig'].animation_data
    was_tweaked = ad.use_tweak_mode
    old = {role: track.mute for role, track in tracks.items()}
    backend._tweak(context, rig, None, enabled=False)
    base_strip = tracks['BASE'].strips[0]
    previous_action, previous_slot = base_strip.action, base_strip.action_slot_handle
    previous_fields = {field: backend._plain(getattr(base_strip, field)) for field in backend._STRIP_FIELDS}
    try:
        if base_strip.action is not session['base_action']:
            _require(backend._motion_hash(base_strip.action, previous_slot) == session['base_hash'],
                     'The explicit unmodified A work copy differs from its protected reference during native composition acceptance.')
            base_strip.action, base_strip.action_slot = session['base_action'], backend._slot(session['base_action'], session['base_slot'])
        for role, track in tracks.items():
            track.mute = role != 'BASE'
        ad.action, ad.use_nla = None, True
        ua._set_frame(context.scene, frame)
        return _pose(context, rig, names)
    finally:
        base_strip.action, base_strip.action_slot = previous_action, backend._slot(previous_action, previous_slot)
        for field, value in previous_fields.items():
            setattr(base_strip, field, value)
        for role, track in tracks.items():
            track.mute = old[role]
        if was_tweaked:
            backend._tweak(context, rig, tracks['EDIT'], enabled=True)
        context.view_layer.update()


def _full_stack(context, item, modules, seconds, tolerance):
    backend, ua = modules['animation_composition'], modules['unity_animation']
    session, _item, scope, tracks = backend.validate(context, item.rig)
    _require(session['edit_target'] == 'B' and not session['solo'], 'Native C comparison begins in Edit B / C view.')
    delegated = set(getattr(scope.plan, 'delegated_names', ())) if scope.plan else set()
    names = tuple(sorted(set(scope.mapping.indices) - delegated))
    upper, lower = set(scope.native_names), set(names) - set(scope.native_names)
    _require(upper and lower and upper <= set(names), 'The strict native packet must contain separate upper and lower domains.')
    before = backend.item_fingerprint(context, item)
    _require(before.get('fingerprint_known'), 'The authored B proof must be known before native view tests.')
    hashes = (backend._motion_hash(session['base_action'], session['base_slot']),
              backend._motion_hash(item.custom_action, item.custom_slot),
              backend._motion_hash(session['edit_action'], session['edit_slot']))
    samples = []
    for seconds_value in seconds:
        frame = 1. + seconds_value * session['spec']['fps']
        ua._set_frame(context.scene, frame)
        composed = _pose(context, item.rig, names)
        reference = _a_only(context, session, tracks, names, frame, modules)
        backend.set_solo(context, True, item.rig)
        ua._set_frame(context.scene, frame)
        only_b = _pose(context, item.rig, names)
        backend.set_solo(context, False, item.rig)
        ua._set_frame(context.scene, frame)
        recovered = _pose(context, item.rig, names)
        lower_error, upper_error, recovered_error = (_error(composed, reference, lower),
            _error(composed, only_b, upper), _error(composed, recovered, names))
        _require(max(lower_error, upper_error, recovered_error) <= tolerance,
                 'Native Full Stack differs from A lower/B upper or changes after Solo: ' + str((lower_error, upper_error, recovered_error)))
        _require(backend.item_fingerprint(context, item) == before, 'Seeking/Solo changed B authored/effective proof.')
        samples.append({'seconds': seconds_value, 'frame': frame, 'lower_vs_A': lower_error,
                        'upper_vs_B': upper_error, 'C_after_solo': recovered_error})
    _require(hashes == (backend._motion_hash(session['base_action'], session['base_slot']),
        backend._motion_hash(item.custom_action, item.custom_slot),
        backend._motion_hash(session['edit_action'], session['edit_slot'])), 'Native view tests modified an Action.')
    return {'sample_count': len(samples), 'native_count': len(names), 'upper_count': len(upper),
            'lower_count': len(lower), 'delegated_count': len(delegated), 'tolerance': tolerance, 'samples': samples,
            'b_authored_sha256': before['authored_b_sha256'], 'b_effective_input_sha256': before['effective_input_sha256']}


def _c_native_domains(scope):
    delegated = set(getattr(scope.plan, 'delegated_names', ())) if scope.plan else set()
    names = tuple(sorted(set(scope.mapping.indices) - delegated))
    upper, lower = set(scope.native_names), set(names) - set(scope.native_names)
    _require(upper and lower and upper <= set(names), 'C pose witnesses require exact nondelegated upper and lower native domains.')
    return names, upper, lower, delegated


@contextmanager
def _edit_a_c_witness(context, item, a_item_id, modules, seconds, tolerance):
    """Compare actual C before/through Edit A and after returning to Edit B."""
    backend, ua, worklist = (modules[name] for name in
        ('animation_composition', 'unity_animation', 'animation_worklist'))
    _require(type(tolerance) in (int, float) and math.isfinite(tolerance) and 0 < tolerance <= .0002,
             'Edit A C-pose tolerance must be finite and at most 0.2 mm.')
    _require(isinstance(seconds, list) and len(seconds) >= 2 and len(set(seconds)) >= 2
        and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in seconds),
        'Edit A C witnesses require multiple distinct finite native seek times.')
    session, _item, scope, tracks = backend.validate(context, item.rig)
    names, upper, lower, delegated = _c_native_domains(scope)
    rig, scene, owner, item_id = item.rig, context.scene, session['owner'], str(item.item_id)
    a_item = worklist._item(worklist.state(context), a_item_id)
    actions = {'B_draft': (session['edit_action'], session['edit_slot']),
        'B_Custom': (item.custom_action, item.custom_slot),
        'A_workcopy': (a_item.custom_action, a_item.custom_slot),
        'readonly_A': (session['base_action'], session['base_slot'])}
    before_hashes = {key: backend._motion_hash(action, slot) for key, (action, slot) in actions.items()}
    _require(before_hashes['A_workcopy'] == before_hashes['readonly_A'],
             'The Edit A C-switch witness requires an unmodified exact reference work copy.')

    def strict_state(target):
        current = worklist._item(worklist.state(context), item_id)
        current_a = worklist._item(worklist.state(context), a_item_id)
        state, actual, current_scope, current_tracks = backend.validate(context, rig)
        _require(actual.item_id == item_id and state['owner'] == owner and state['edit_target'] == target
            and not state['solo'] and rig.animation_data.use_tweak_mode
            and rig.animation_data.action is state['edit_action' if target == 'B' else 'base_edit_action']
            and all(not track.mute and not track.is_solo for track in current_tracks.values())
            and _c_native_domains(current_scope) == (names, upper, lower, delegated)
            and state['edit_action'] is actions['B_draft'][0] and state['edit_slot'] == actions['B_draft'][1]
            and current.custom_action is actions['B_Custom'][0] and current.custom_slot == actions['B_Custom'][1]
            and current_a.custom_action is actions['A_workcopy'][0] and current_a.custom_slot == actions['A_workcopy'][1]
            and state['base_action'] is actions['readonly_A'][0] and state['base_slot'] == actions['readonly_A'][1],
            'Editing target no longer evaluates the exact owned full C stack and native Action slots.')
        if target == 'A':
            _require(state['base_edit_item_id'] == a_item_id
                and state['base_custom_action'] is current_a.custom_action
                and state['base_edit_action'] is not state['base_action']
                and backend._motion_hash(state['base_edit_action'], state['base_edit_slot']) == before_hashes['A_workcopy'],
                'Edit A must retain the exact separate unmodified native work-copy draft.')
        actual_hashes = {key: backend._motion_hash(action, slot) for key, (action, slot) in actions.items()}
        _require(actual_hashes == before_hashes, 'Switching/seeking C modified B draft/Custom, A work copy or readonly Source A.')
        return state, actual_hashes

    strict_state('B')
    baseline = []
    for value in seconds:
        ua._set_frame(context.scene, 1. + value * session['spec']['fps'])
        strict_state('B')
        baseline.append(_pose(context, rig, names))
    proof = {'completed': False, 'sample_count': len(seconds), 'native_count': len(names),
        'upper_count': len(upper), 'lower_count': len(lower), 'delegated_count': len(delegated),
        'tolerance': tolerance, 'A_workcopy_item_id': a_item_id, 'action_hashes_before': before_hashes,
        'samples': [{'seconds': value, 'frame': 1. + value * session['spec']['fps']} for value in seconds]}

    def sample(target, label):
        for value, expected, row in zip(seconds, baseline, proof['samples']):
            ua._set_frame(context.scene, 1. + value * session['spec']['fps'])
            strict_state(target)
            actual = _pose(context, rig, names)
            errors = {'full': _error(expected, actual, names), 'upper': _error(expected, actual, upper),
                      'lower': _error(expected, actual, lower)}
            _require(max(errors.values()) <= tolerance,
                     'Native C changed during ' + label + ': ' + str(errors))
            row[label] = errors

    backend.set_edit_target(context, 'A', base_item_id=a_item_id)
    strict_state('A')
    sample('A', 'EditA')
    backend.set_solo(context, True)
    ua._set_frame(context.scene, 1. + seconds[-1] * session['spec']['fps'])
    backend.set_solo(context, False)
    sample('A', 'SoloA_restored_C')
    try:
        # Headful callers can capture actual Edit A/C here before return to B.
        yield proof
    finally:
        # The checkpoint returns to Blender's event loop. Reacquire context
        # before touching retained IDs; unscripted Undo/load must not commit
        # a different native session or dereference removed RNA.
        current_context, _saved, current = _current(item_id, modules)
        _require(current_context.scene is scene and current.rig is rig,
                 'The original native Scene/Rig changed during the Edit A checkpoint; refuse to commit or switch.')
        context = current_context
        # Refuse unexpected edits before set_edit_target would commit them.
        strict_state('A')
        backend.set_edit_target(context, 'B')
        sample('B', 'ReturnB')
        _state, after_hashes = strict_state('B')
        proof.update(completed=True, C_view_retained_while_editing_A=True,
            C_view_restored_after_Solo_A=True, C_view_retained_after_return_to_B=True,
            native_Full_Stack_Tweak_verified=True, checkpoint_native_IDs_retained=True, action_hashes_after=after_hashes,
            B_draft_unchanged=True, B_Custom_unchanged=True, A_workcopy_unchanged=True, readonly_A_unchanged=True)


@contextmanager
def _view3d(context, rig):
    """Obtain a real native transform context without retaining editor changes."""
    import bpy
    _require(context.window is not None and context.window.scene is context.scene, 'A native Blender window is required for UI acceptance.')
    area = next((area for area in context.window.screen.areas if area.type == 'VIEW_3D'), None)
    area = area or context.area or next(iter(context.window.screen.areas), None)
    _require(area is not None, 'No native editor exists for the transform/Undo acceptance check.')
    previous_type = area.type
    area_pointer = area.as_pointer()
    area_index = next(index for index, candidate in enumerate(context.window.screen.areas) if candidate.as_pointer() == area_pointer)
    previous_selected = [obj.name for obj in context.selected_objects]
    previous_active = context.view_layer.objects.active.name if context.view_layer.objects.active else ''
    try:
        for obj in context.selected_objects:
            obj.select_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        if area.type != 'VIEW_3D':
            area.type = 'VIEW_3D'
        region = next(region for region in area.regions if region.type == 'WINDOW')
        with context.temp_override(window=context.window, area=area, region=region):
            if rig.mode != 'POSE':
                _require(bpy.ops.object.mode_set(mode='POSE') == {'FINISHED'}, 'Could not enter the native Pose context.')
            yield bpy.context
    finally:
        # Undo may replace native IDs, including the Screen. Reacquire the
        # current test window before recovering its editor/selection state.
        current_context = bpy.context
        current_area = next((candidate for candidate in current_context.window.screen.areas
                             if candidate.as_pointer() == area_pointer), None)
        if current_area is None and area_index < len(current_context.window.screen.areas):
            current_area = current_context.window.screen.areas[area_index]
        if current_area is not None and current_area.type != previous_type:
            current_area.type = previous_type
        for obj in current_context.selected_objects:
            obj.select_set(False)
        for name in previous_selected:
            obj = current_context.view_layer.objects.get(name)
            if obj is not None:
                obj.select_set(True)
        current_context.view_layer.objects.active = current_context.view_layer.objects.get(previous_active)


def _work_copy(context, item, config, modules):
    backend, links, worklist = (modules[name] for name in ('animation_composition', 'animation_link', 'animation_worklist'))
    spec = backend.item_spec(item)
    candidates = [row for row in worklist.state(context).items if row.custom_action is not None
        and row.new_animation and row.source_action is None and backend.item_spec(row) is None
        and row.custom_action.library is None and row.custom_action is not item.custom_action
        and row.custom_action.get(links.PACKAGE_HASH_KEY, '').lower() == spec['referencePacketSha256']]
    requested = config.get('aWorkItemId', '')
    if requested:
        candidates = [row for row in candidates if row.item_id == requested or row.work_item_id == requested]
    if not candidates and config.get('createAWorkCopy', True):
        _require(hasattr(backend, 'create_reference_work_copy'), 'Prepare the genuine A companion Link before Create A Work Copy acceptance.')
        candidates = [backend.create_reference_work_copy(context, item.item_id)]
    _require(len(candidates) == 1, 'Supply one explicit exact-packet A Custom work item; name matching is not permitted.')
    row = candidates[0]
    record = modules['animation_worklist_new'].validate(worklist.state(context), row,
                                                       worklist._fresh_workspace(worklist.state(context), row))
    _require(record.get('composition') is None and record.get('editScope') == 'All'
        and not record.get('compositionReferenceSource') and not record.get('sourceOnly'),
        'The editable NewAction A must retain ordinary All scope without Source readonly flags.')
    return row


def _drain(iterator):
    """Keep the existing synchronous API: checkpoints are ignored by default."""
    while True:
        try:
            next(iterator)
        except StopIteration as result:
            return result.value


def _checkpoint(context, item, modules, name, classification, *, reference=None):
    """Record actual native identities; no drawing or fabricated pixel content."""
    backend = modules['animation_composition']
    validated = backend.validate(context, item.rig) if backend.active(item.rig) else None
    session = validated[0] if validated else None
    base = session['base_action'] if session else reference
    slot = session['base_slot'] if session else modules['animation_worklist']._slot(base).handle if base else 0
    proof = {'name': name, 'classification': classification, 'scene': context.scene.name,
        'rig': item.rig.name, 'item_id': item.item_id, 'work_item_id': item.work_item_id,
        'frame': context.scene.frame_current_final,
        'playing': bool(context.window and context.window.screen.is_animation_playing),
        'editing': session['edit_target'] if session else '',
        'time_mode': session['spec']['timeMode'] if session else backend.item_spec(item)['timeMode'],
        'solo': bool(session and session['solo']),
        'native_tweak': bool(item.rig.animation_data and item.rig.animation_data.use_tweak_mode),
        'readonly_A_name': base.name if base else '',
        'readonly_A_library': base.library.filepath if base and base.library else '',
        'readonly_A_sha256': backend._motion_hash(base, slot) if base else '',
        'B_custom_sha256': backend._motion_hash(item.custom_action, item.custom_slot)}
    spec = session['spec'] if session else backend.item_spec(item)
    proof['public_work_item_id'], proof['public_new_action_id'] = spec['editWorkItemId'], spec['editNewActionId']
    if session:
        scope = validated[2]
        proof['scope_proof_sha256'] = backend.contract.digest(scope.proof)
        proof['scope_native_names'] = list(scope.native_names)
        proof['scope_input_channels'] = [[path, index] for path, index in scope.channels]
        proof['draft_sha256'] = backend._motion_hash(session['edit_action'], session['edit_slot'])
        proof['A_workcopy_item_id'] = session.get('base_edit_item_id', '')
        hold = session.get('following_hold') or {}
        proof['following_hold_native_item_id'] = hold.get('item_id', '')
        if hold.get('item_id'):
            hold_item = modules['animation_worklist']._item(modules['animation_worklist'].state(context), hold['item_id'])
            proof['following_hold_work_item_id'] = hold_item.work_item_id
            proof['public_following_hold_work_item_id'] = session['spec']['holdWorkItemId']
    _require(base is not None and base.library is not None, 'Visual checkpoints require the actual linked readonly A Source.')
    return proof


def visual_guard(context, item_id, config):
    """Authenticate native Source/cache/packet state even on timeout or Cancel."""
    import bpy
    modules = _modules(config)
    context, saved, item = _current(item_id, modules)
    worklist, backend = modules['animation_worklist'], modules['animation_composition']
    catalog = worklist._fresh_workspace(saved, item)
    packet = backend._packet_for_item(item)
    links = modules['animation_link']
    _require(Path(item.model_file).is_file() and links._sha256(Path(item.model_file)) == item.model_sha256,
             'The exact current-model file changed during visual checkpoint acceptance.')
    if item.new_animation:
        modules['animation_worklist_new'].validate(saved, item, catalog)
        modules['animation_worklist_new'].authenticate_composition_bind_template(
            context, item.rig, backend.item_spec(item), packet)
    for scene in bpy.data.scenes:
        rows = getattr(scene, 'character_designer_animation_worklist', None)
        for row in rows.items if rows is not None else ():
            if row.source_action is not None:
                worklist._check_baseline(row)
    if backend.active(item.rig):
        session = backend.validate(context, item.rig)[0]
        _require(backend._motion_hash(session['base_action'], session['base_slot']) == session['base_hash'],
                 'Readonly A changed during visual checkpoint acceptance.')
        receipt = backend.reference_receipt(item.rig, session['spec']['referencePacketSha256'])
        _require(receipt is not None, 'The protected readonly A reference receipt is missing during visual checkpoint acceptance.')
        _require(Path(receipt['cache']).is_file() and links._sha256(Path(receipt['cache'])) == receipt['cache_sha256']
                 and Path(bpy.path.abspath(session['base_action'].library.filepath)).resolve() == Path(receipt['cache']).resolve(),
                 'The protected readonly A cache changed during visual checkpoint acceptance.')


def checkpoint_state(context, item_id, config, checkpoint):
    """Reacquire only the actual native state; never retain Undo/load pointers."""
    modules = _modules(config)
    context, _saved, item = _current(item_id, modules)
    backend = modules['animation_composition']
    base = None if backend.active(item.rig) else backend.prepare_reference(context, item)[0]
    return _checkpoint(context, item, modules, checkpoint['name'], checkpoint['classification'], reference=base)


def _pose_hidden(pb):
    """Read the actual Pose visibility API, or its supported legacy owner."""
    owner = pb if hasattr(pb, 'hide') else pb.bone
    hidden = owner.hide
    _require(type(hidden) is bool, 'The actual Pose visibility API must return a boolean.')
    return hidden


def _show_control(context, item, scope, *, backend=None):
    """Select one exact allowed FK control in the owned QA Pose viewport."""
    import bpy
    names = sorted(set(scope.input_by_native.values()))
    names = [name for name in names if not _pose_hidden(item.rig.pose.bones[name])
             and not item.rig.pose.bones[name].bone.hide_select]
    _require(names, 'No actual visible allowed B control exists for the native screen checkpoint.')
    backend = backend or importlib.import_module('character_designer.animation_composition')
    selections = [(pb, backend._pose_selection_owner(pb)) for pb in item.rig.pose.bones]
    context.view_layer.objects.active = item.rig
    item.rig.select_set(True)
    if item.rig.mode != 'POSE':
        _require(bpy.ops.object.mode_set(mode='POSE') == {'FINISHED'}, 'Could not show the actual QA Pose controls.')
    item.rig.data.bones.active = item.rig.data.bones[names[0]]
    for pb, owner in selections:
        owner.select = pb.name == names[0]


def _weapon_acceptance_steps(context, item, modules, *, checkpoints=False):
    """Use the actual UI import/removal and authenticate every new native ID."""
    import bpy
    backend, weapon, exporter = (modules[name] for name in
        ('animation_composition', 'animation_composition_weapon', 'animation_composition_export'))
    session, _item, _scope, _tracks = backend.validate(context, item.rig)
    _require(weapon.RECORD not in item.rig, 'Weapon acceptance requires a clean isolated weapon-context baseline.')
    tables = ('objects', 'collections', *weapon._ASSET_TABLES)
    before = {key: set(getattr(bpy.data, key)) for key in tables}
    action_inventory = {action.as_pointer():tuple((slot.as_pointer(),slot.handle) for slot in action.slots)
        for action in before['actions']}
    pairs = [(session['base_action'],session['base_slot']),
        (session['edit_action'],session['edit_slot']), (item.custom_action,item.custom_slot),
        (item.source_action,item.source_slot)]
    if session.get('base_edit_action') is not None:
        pairs.append((session['base_edit_action'],session['base_edit_slot']))
    if session.get('base_custom_action') is not None:
        pairs.append((session['base_custom_action'],session['base_edit_slot']))
    ad = item.rig.animation_data
    if ad is not None:
        if ad.action is not None:
            pairs.append((ad.action,ad.action_slot_handle))
        pairs.extend((strip.action,strip.action_slot_handle) for track in ad.nla_tracks for strip in track.strips
            if strip.type == 'CLIP' and strip.action is not None)
    action_pairs = {(action.as_pointer(),handle):(action,handle) for action,handle in pairs if action is not None}
    action_proofs = [(action,handle,exporter._action_hash(action,handle)) for action,handle in action_pairs.values()]
    del ad, pairs, action_pairs
    rig_state = exporter._artist_state(item.rig, context.scene)
    layout = backend._layout(item.rig)
    _require(bpy.ops.character_designer.composition_weapon('EXEC_DEFAULT', True, remove=False) == {'FINISHED'},
             'The actual UI Load Weapon operator did not finish.')
    record, objects = weapon.validate_owned(item.rig)
    proof = json.loads(record['proof_json'])
    spec = session['spec']
    document, _expected_surface = weapon.validate_context(spec)
    captured = [row for row in document['meshes'] if row['enabled'] and row['activeInPrefab']]
    displayed = [obj for obj in objects if obj.type == 'MESH']
    captured_counts = {'captured_renderer_count':len(captured),
        'captured_vertex_count':sum(row['vertexCount'] for row in captured),
        'captured_triangle_count':sum(len(row['triangles']) // 3 for row in captured)}
    native_counts = {'captured_renderer_count':len(displayed),
        'captured_vertex_count':sum(len(obj.data.vertices) for obj in displayed),
        'captured_triangle_count':sum(len(obj.data.polygons) for obj in displayed)}
    _require(captured and displayed and all(isinstance(obj.get('captured_renderer_path'), str) for obj in displayed)
        and all(len(face.vertices) == 3 for obj in displayed for face in obj.data.polygons)
        and len({obj.data for obj in displayed}) == len(displayed)
        and {obj.data for obj in displayed} == set(record['assets'].get('meshes', {}).values())
        and sorted((obj['captured_renderer_path'],len(obj.data.vertices),len(obj.data.polygons)) for obj in displayed)
            == sorted((row['rendererPath'],row['vertexCount'],len(row['triangles']) // 3) for row in captured),
        'Owned native weapon meshes differ from the authenticated active captured renderers.')
    _require(proof.get('captured_unity_geometry') is True and proof.get('actual_fbx_import') is False
        and all(type(proof.get(key)) is int and proof[key] > 0
                and proof[key] == value == native_counts[key] for key,value in captured_counts.items())
        and proof.get('prefab_scale_applied_once') is True
        and proof.get('context_sha256') == spec['weaponContextSha256']
        and proof.get('source_sha256') == spec['weaponModelSha256']
        and proof.get('placed_surface_proved') is True
        and all(type(proof.get(key)) in (int, float) and math.isfinite(proof[key]) and 0 <= proof[key] <= 2e-5
                for key in ('placed_surface_error_metres','placed_bounds_error_metres'))
        and type(proof.get('placed_distinct_vertices')) is int
        and 4 <= proof['placed_distinct_vertices'] <= captured_counts['captured_vertex_count']
        and proof.get('automatic_hand_ik') is False
        and bool(proof.get('grip_limitation')) and proof.get('authored_grip_count') == len(spec['gripTargets']),
        'Captured weapon geometry/context/counts/scale or its placed surface proof was not authenticated.')
    del document, _expected_surface, captured, displayed
    grips = [obj for obj in objects if obj.get('authored_grip_id')]
    _require({obj['authored_grip_id']: obj['authored_control_id'] for obj in grips}
        == {grip['id']: grip['controlId'] for grip in spec['gripTargets']},
        'The display invented or dropped an authored named grip target.')
    after = {key: set(getattr(bpy.data, key)) for key in tables}
    owned = {'objects': set(objects), 'collections': {record['collection']},
        **{key: set(record.get('assets', {}).get(key, {}).values()) for key in weapon._ASSET_TABLES}}
    _require(all(before[key] <= after[key] and after[key] - before[key] == owned[key] for key in tables),
             'Loading the weapon deleted a foreign ID or introduced an unowned native ID.')
    _require(exporter._artist_state(item.rig, context.scene) == rig_state and backend._layout(item.rig) == layout
        and {action.as_pointer():tuple((slot.as_pointer(),slot.handle) for slot in action.slots)
             for action in bpy.data.actions} == action_inventory
        and all(exporter._action_hash(action, slot) == digest for action, slot, digest in action_proofs),
        'Loading the weapon changed the Rig, Actions, native NLA or Scene units.')
    added_counts = {key: len(owned[key]) for key in tables}
    if checkpoints:
        scope = backend.validate(context, item.rig)[2]
        _show_control(context, item, scope, backend=backend)
        yield {**_checkpoint(context, item, modules, 'c_edit_b_weapon', 'C view · Editing B · Upper Body Override'),
               'weapon_proof': proof, 'allowed_control_names': sorted(set(scope.input_by_native.values()))}
    _require(bpy.ops.character_designer.composition_weapon('EXEC_DEFAULT', True, remove=True) == {'FINISHED'},
             'The actual UI Remove Weapon operator did not finish.')
    _require(weapon.RECORD not in item.rig and
        all(set(getattr(bpy.data, key)) == before[key] for key in tables),
        'Removing the owned weapon retained imported assets or deleted a foreign ID.')
    _require(exporter._artist_state(item.rig, context.scene) == rig_state and backend._layout(item.rig) == layout
        and {action.as_pointer():tuple((slot.as_pointer(),slot.handle) for slot in action.slots)
             for action in bpy.data.actions} == action_inventory
        and all(exporter._action_hash(action, slot) == digest for action, slot, digest in action_proofs),
        'Removing the weapon changed character controls, Actions or Scene units.')
    backend.validate(context, item.rig)
    return {'actual_UI_load_remove': True, 'owned_ID_counts': added_counts,
        'all_prior_IDs_retained': True, 'owned_assets_removed': True, 'Rig_Actions_units_unchanged': True,
        'geometry_proof': proof, 'automatic_hand_ik': False,
        'grip_limitation': proof['grip_limitation']}


def _weapon_acceptance(context, item, modules):
    return _drain(_weapon_acceptance_steps(context, item, modules))


def _positive_a_edit(context, item, a_item_id, modules, config, seconds):
    """Key an exact full-A input in C, committing its authentic NewAction only."""
    import bpy
    backend, ua, worklist, new = (modules[name] for name in
        ('animation_composition', 'unity_animation', 'animation_worklist', 'animation_worklist_new'))
    item_id = str(item.item_id)
    saved = worklist.state(context)
    a_item = worklist._item(saved, a_item_id)
    _require(a_item.new_animation and a_item.source_action is None and backend.item_spec(a_item) is None,
             'Positive A editing requires the separate genuine ordinary NewAction origin.')
    origin_before, manifest_before = new.validate(saved, a_item, worklist._fresh_workspace(saved, a_item)), _sha(a_item.manifest_path)
    b_before, a_before = backend._motion_hash(item.custom_action, item.custom_slot), backend._motion_hash(a_item.custom_action, a_item.custom_slot)
    prior_snapshot, prior_mode = ua._snapshot(context, item.rig), item.rig.mode
    backend.enter(context, backend.item_spec(item), item.item_id)
    backend.set_edit_target(context, 'A', base_item_id=a_item_id)
    session, _item, scope, _tracks = backend.validate(context, item.rig)
    source_hash = backend._motion_hash(session['base_action'], session['base_slot'])
    protected_a = session['base_action']
    protected_slot = session['base_slot']
    names, upper, lower, _delegated = _c_native_domains(scope)
    tolerance = config.get('poseTolerance', 2e-5)
    _require(type(tolerance) in (int, float) and math.isfinite(tolerance) and 0 < tolerance <= .0002,
             'Positive A upper-B preservation requires finite tolerance at most 0.2 mm.')
    _require(session['edit_target'] == 'A' and not session['solo'] and item.rig.animation_data.use_tweak_mode
        and item.rig.animation_data.action is session['base_edit_action'],
        'Positive A editing must remain in the exact native C Full Stack Tweak.')
    curves = {(curve.data_path, curve.array_index) for curve in backend._curves(session['base_edit_action'], session['base_edit_slot'])}
    candidates = []
    for name in sorted(lower):
        bone = item.rig.pose.bones[name]
        rotation_path = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                         'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        if (any(path == bone.path_from_id() + '.' + rotation_path for path, _index in curves)
                and not _pose_hidden(bone) and not bone.bone.hide_select and not bone.lock_rotation[0]):
            candidates.append((name, rotation_path))
    requested = config.get('aRotationControl')
    if requested:
        candidates = [candidate for candidate in candidates if candidate[0] == requested]
    _require(candidates, 'No exact visible, keyed lower-body full-A rotation input is available for positive A editing.')
    name, rotation_path = candidates[0]
    frame = 1. + seconds[-1] * session['spec']['fps']
    ua._set_frame(context.scene, frame)
    before_pose = _pose(context, item.rig, names)
    draft_before = backend._motion_hash(session['base_edit_action'], session['base_edit_slot'])
    with _view3d(context, item.rig):
        selections = [(pb, backend._pose_selection_owner(pb)) for pb in item.rig.pose.bones]
        item.rig.data.bones.active = item.rig.data.bones[name]
        for pb, owner in selections:
            owner.select = pb.name == name
        _require(bpy.ops.transform.rotate('EXEC_DEFAULT', True, value=.041, orient_axis='X', orient_type='LOCAL') == {'FINISHED'},
                 'The native full-A Pose transform did not finish.')
        _require(backend._motion_hash(session['base_edit_action'], session['base_edit_slot']) == draft_before,
                 'A drag silently keyed before the explicit A key operation.')
        _require(item.rig.pose.bones[name].keyframe_insert(data_path=rotation_path, frame=frame, group=name),
                 'The native full-A rotation key operation failed.')
    context, saved, item = _current(item_id, modules)
    a_item = worklist._item(saved, a_item_id)
    session, _item, _scope, _tracks = backend.validate(context, item.rig)
    _require(backend._motion_hash(session['base_edit_action'], session['base_edit_slot']) != draft_before,
             'The explicit A key did not change the selected native A draft.')
    _require(backend._motion_hash(a_item.custom_action, a_item.custom_slot) == a_before,
             'The A key wrote the Custom before explicit commit.')
    _require(backend.commit(context, item.rig), 'Explicit A commit did not publish the keyed A draft.')
    committed_session, _actual, committed_scope, committed_tracks = backend.validate(context, item.rig)
    _require(committed_session['edit_target'] == 'A' and not committed_session['solo']
        and item.rig.animation_data.use_tweak_mode
        and item.rig.animation_data.action is committed_session['base_edit_action']
        and all(not track.mute and not track.is_solo for track in committed_tracks.values())
        and _c_native_domains(committed_scope) == (names, upper, lower, _delegated),
        'The committed A draft no longer evaluates the original native full C stack.')
    a_after = backend._motion_hash(a_item.custom_action, a_item.custom_slot)
    ua._set_frame(context.scene, frame)
    after_pose = _pose(context, item.rig, names)
    lower_error, upper_error = _error(before_pose, after_pose, lower), _error(before_pose, after_pose, upper)
    _require(lower_error > 1e-7, 'The keyed A input did not change the evaluated native lower-body C pose.')
    _require(upper_error <= tolerance, 'A-only editing changed the evaluated upper-body B in C: ' + str(upper_error))
    _require(a_after != a_before and backend._motion_hash(item.custom_action, item.custom_slot) == b_before
        and backend._motion_hash(protected_a, protected_slot) == source_hash,
        'A-only commit failed to preserve B and protected Source A.')
    _require(new.validate(saved, a_item, worklist._fresh_workspace(saved, a_item)) == origin_before
        and _sha(a_item.manifest_path) == manifest_before, 'A editing changed its authenticated origin/NewAction manifest.')
    _require(bpy.ops.character_designer.composition_close('EXEC_DEFAULT', True) == {'FINISHED'}, 'Positive A editing could not close safely.')
    _require(ua._snapshot(context, item.rig) == prior_snapshot and item.rig.mode == prior_mode
        and backend._motion_hash(item.custom_action, item.custom_slot) == b_before
        and backend._motion_hash(a_item.custom_action, a_item.custom_slot) == a_after
        and backend._motion_hash(protected_a, protected_slot) == source_hash,
        'A Close did not restore prior artist state or changed independent Actions.')
    return {'native_transform_key_commit': True, 'full_A_input': name, 'C_seconds': seconds[-1],
        'A_before_sha256': a_before, 'A_after_sha256': a_after, 'B_unchanged': True,
        'protected_Source_A_unchanged': True, 'origin_NewAction_manifest_unchanged': True,
        'C_lower_changed': True, 'C_lower_error': lower_error, 'C_upper_error': upper_error,
        'C_upper_B_unchanged': True, 'C_Full_Stack_Tweak_verified': True,
        'native_upper_count': len(upper), 'native_lower_count': len(lower),
        'pose_tolerance': tolerance, 'prior_state_restored': True}


def run_steps(context, item_id, config):
    """Verify native session behavior on the caller's isolated copy, no subprocess.

    Required ``evidenceDirectory`` is a pre-created dedicated directory. Optional
    ``aWorkItemId`` is an actual local row/work-item ID, ``createAWorkCopy`` uses
    the supported ordinary-A-origin API, ``seekSeconds`` contains two or more
    arbitrary C times, and ``rotationControl`` is an exact validated FK input.
    The driver invokes this once after its unchanged-B/two-A isolation checks.
    """
    import bpy
    paths, modules = _evidence(config), _modules(config)
    backend, ua = modules['animation_composition'], modules['unity_animation']
    report = {'schema': 'x.validation.character-composition-session/1', 'complete': False,
              'blender': bpy.app.version_string, 'phases': [], 'item_id': item_id}
    phase = 'preflight'
    prior_global_undo = context.preferences.edit.use_global_undo
    try:
        context, saved, item = _current(item_id, modules)
        _require(not backend.active(item.rig), 'Run session acceptance after the two-A driver has closed its read-only view.')
        spec = backend.item_spec(item)
        _require(spec['timeMode'] == 'HoldPose', 'The editing/constant-capture fixture must be an actual positive-duration Hold Pose.')
        seconds = config.get('seekSeconds', [.137, .419])
        _require(isinstance(seconds, list) and len(seconds) >= 2 and all(type(value) in (int, float)
            and math.isfinite(value) and value >= 0 for value in seconds), 'Provide at least two finite native C seek times.')
        tolerance = config.get('poseTolerance', 2e-5)
        _require(type(tolerance) in (int, float) and 0 < tolerance <= .0002, 'Native pose tolerance must be explicit and at most 0.2 mm.')
        headful = bool(config.get('headfulCheckpoints', {}).get('enabled'))
        if headful:
            base, _base_slot = backend.prepare_reference(context, item)
            yield _checkpoint(context, item, modules, 'readonly_a_workcopy_entry',
                              'Readonly A Source · explicit Create A Work Copy entry', reference=base)
            context, saved, item = _current(item_id, modules)
        prior_b_action, prior_b_slot = item.custom_action, item.custom_slot
        prior_b_source, prior_b_source_slot = item.source_action, item.source_slot
        prior_b_row = {key: getattr(item, key) for key in _B_ROW_FIELDS}
        b_custom_before = backend._motion_hash(prior_b_action, prior_b_slot)
        a_item = _work_copy(context, item, config, modules)
        a_item_id = str(a_item.item_id)
        # Create A Work Copy may grow the native RNA collection and invalidate
        # every previously held row wrapper; retain plain IDs then reacquire.
        context, saved, item = _current(item_id, modules)
        a_item = modules['animation_worklist']._item(saved, a_item_id)
        _require(item.custom_action is prior_b_action and item.custom_slot == prior_b_slot
                 and item.source_action is prior_b_source and item.source_slot == prior_b_source_slot
                 and {key: getattr(item, key) for key in _B_ROW_FIELDS} == prior_b_row
                 and backend.item_spec(item) == spec
                 and backend._motion_hash(item.custom_action, item.custom_slot) == b_custom_before,
                 'Create A Work Copy replaced or changed the original B Action, slot, content or sealed spec.')
        a_custom_before = backend._motion_hash(a_item.custom_action, a_item.custom_slot)
        prior_snapshot = ua._snapshot(context, item.rig)
        prior_mode = item.rig.mode
        prior_action = item.rig.animation_data.action if item.rig.animation_data else None
        prior_action_name = prior_action.name if prior_action else ''
        # Native ID pointers in a layout are proved by validate/exit; record a
        # portable field-only digest here rather than stringify datablocks.
        before_actions = {(action.name, action.library.filepath if action.library else '') for action in bpy.data.actions}
        backend.enter(context, spec, item.item_id)
        session, _item, scope, _tracks = backend.validate(context, item.rig)
        a_source_hash, owner = session['base_hash'], session['owner']
        _require(item.rig.animation_data.use_tweak_mode and item.rig.animation_data.action is session['edit_action'],
                 'Blender did not enter the exact B Action in native Full Stack Tweak.')
        report['phases'].append({'phase': phase, 'owner': owner, 'protected_A_sha256': a_source_hash,
            'A_workcopy_item_id': a_item_id, 'native_tweak': True, 'autokey': context.scene.tool_settings.use_keyframe_insert_auto,
            'new_actions': sorted(name for name, library in
                {(action.name, action.library.filepath if action.library else '') for action in bpy.data.actions} - before_actions)})
        _require(not context.scene.tool_settings.use_keyframe_insert_auto, 'A constant Hold must not silently auto-key one sampled C frame.')
        _write(paths['report'], report)

        phase = 'actual_weapon_UI_load_remove'
        report['phases'].append({'phase': phase, **(yield from _weapon_acceptance_steps(context, item, modules, checkpoints=headful))})
        _write(paths['report'], report)

        phase = 'full_stack_seek_solo'
        view_proof = _full_stack(context, item, modules, seconds, tolerance)
        report['phases'].append({'phase': phase, **view_proof})
        _write(paths['report'], report)
        if headful:
            session, _item, scope, _tracks = backend.validate(context, item.rig)
            _show_control(context, item, scope, backend=backend)
            # The generator returns to the event loop while the actual screen
            # plays; no synchronous sleeping or simulated frame advancement.
            _require(not context.window.screen.is_animation_playing, 'Begin the visual playback check paused.')
            _require(bpy.ops.screen.animation_play() == {'FINISHED'} and context.window.screen.is_animation_playing,
                     'The actual native playback operator did not start.')
            yield _checkpoint(context, item, modules, 'c_playing', 'C view · actual native playback running')
            context, saved, item = _current(item_id, modules)
            _require(context.window.screen.is_animation_playing, 'The native playback checkpoint was paused before review.')
            _require(bpy.ops.screen.animation_cancel(restore_frame=False) == {'FINISHED'}
                     and not context.window.screen.is_animation_playing, 'The actual native pause operator did not finish.')
            for index, value in enumerate(seconds):
                ua._set_frame(context.scene, 1 + value * spec['fps'])
                yield _checkpoint(context, item, modules, 'c_paused_seek_%d' % (index + 1),
                                  'C view · paused native timeline seek')
                context, saved, item = _current(item_id, modules)

        phase = 'explicit_A_target'
        with _edit_a_c_witness(context, item, a_item_id, modules, seconds, tolerance) as c_witness:
            if headful:
                yield _checkpoint(context, item, modules, 'explicit_a_workcopy', 'C view · Editing genuine separate A Work Copy')
                context, saved, item = _current(item_id, modules)
        session, _item, _scope, _tracks = backend.validate(context, item.rig)
        a_item = modules['animation_worklist']._item(saved, a_item_id)
        _require(backend._motion_hash(a_item.custom_action, a_item.custom_slot) == a_custom_before,
                 'Selecting/seeking unmodified Edit A wrote its local Custom.')
        _require(backend._motion_hash(item.custom_action, item.custom_slot) == b_custom_before,
                 'Selecting Edit A wrote the independent B Custom.')
        report['phases'].append({'phase': phase, 'A_workcopy_unchanged': True, 'B_unchanged': True,
            'protected_A_unchanged': backend._motion_hash(session['base_action'], session['base_slot']) == a_source_hash,
            'C_switch_witness': c_witness})
        _write(paths['report'], report)
        phase = 'native_drag_capture_hold'
        session, _item, scope, _tracks = backend.validate(context, item.rig)
        candidates = sorted(set(scope.input_by_native.values()))
        requested = config.get('rotationControl')
        if requested:
            _require(requested in candidates, 'rotationControl is not an exact validated B FK input.')
            candidates = [requested]
        candidates = [name for name in candidates if not _pose_hidden(item.rig.pose.bones[name])
            and not item.rig.pose.bones[name].bone.hide_select and not item.rig.pose.bones[name].lock_rotation[0]]
        _require(candidates, 'No visible/selectable unlocked exact B control exists for a real native drag.')
        bone_name = candidates[0]
        before_edit = backend._motion_hash(session['edit_action'], session['edit_slot'])
        ua._set_frame(context.scene, 1 + seconds[-1] * spec['fps'])
        context.preferences.edit.use_global_undo = True
        with _view3d(context, item.rig) as ui_context:
            selections = [(pb, backend._pose_selection_owner(pb)) for pb in item.rig.pose.bones]
            item.rig.data.bones.active = item.rig.data.bones[bone_name]
            for pb, selection_owner in selections:
                selection_owner.select = pb.name == bone_name
            before_rotation = tuple(item.rig.pose.bones[bone_name].matrix_basis.to_quaternion())
            result = bpy.ops.transform.rotate('EXEC_DEFAULT', True, value=.037, orient_axis='X', orient_type='LOCAL')
            _require(result == {'FINISHED'}, 'The real native B Pose transform operator did not finish.')
            after_rotation = tuple(item.rig.pose.bones[bone_name].matrix_basis.to_quaternion())
            _require(max(abs(a - b) for a, b in zip(before_rotation, after_rotation)) > 1e-7,
                     'The selected native B control did not actually rotate.')
            _require(backend._motion_hash(session['edit_action'], session['edit_slot']) == before_edit,
                     'A native drag at arbitrary C time silently auto-keyed a moving Hold.')
            _require(bpy.ops.ed.undo_push(message='Composition acceptance before Capture Hold') == {'FINISHED'},
                     'Native Undo baseline could not be established.')
            _require(bpy.ops.character_designer.composition_capture_hold('EXEC_DEFAULT', True) == {'FINISHED'},
                     'The actual UI Capture Hold operator did not finish.')
        context, saved, item = _current(item_id, modules)
        session, _item, scope, _tracks = backend.validate(context, item.rig)
        captured = backend._motion_hash(session['edit_action'], session['edit_slot'])
        _require(captured != before_edit, 'Capturing the genuinely rotated native control did not change the B draft.')
        backend._assert_hold_constant(backend._curves(session['edit_action'], session['edit_slot']))
        b_after_capture = backend.item_fingerprint(context, item)
        _require(b_after_capture['authored_b_sha256'] != view_proof['b_authored_sha256'],
                 'A real Hold capture was not detected as authored B change.')
        _require(backend._motion_hash(item.custom_action, item.custom_slot) == b_custom_before,
                 'Capture Hold wrote the Custom before explicit commit.')
        report['phases'].append({'phase': phase, 'control': bone_name, 'C_seconds': seconds[-1],
            'native_transform': True, 'draft_sha256': captured, 'constant_positive_duration': True, 'BEdited': True})
        _write(paths['report'], report)
        if headful:
            yield _checkpoint(context, item, modules, 'b_rotated_capture_hold', 'Editing B · actual native rotation captured as constant Hold')
            context, saved, item = _current(item_id, modules)

        phase = 'native_undo_redo'
        with _view3d(context, item.rig):
            _require(bpy.ops.ed.undo() == {'FINISHED'}, 'Native Undo did not finish.')
        context, saved, item = _current(item_id, modules)
        session, _item, _scope, _tracks = backend.validate(context, item.rig)
        _require(session['owner'] == owner and backend._motion_hash(session['edit_action'], session['edit_slot']) == before_edit,
                 'Native Undo did not recover the exact pre-capture draft and ownership record.')
        with _view3d(context, item.rig):
            _require(bpy.ops.ed.redo() == {'FINISHED'}, 'Native Redo did not finish.')
        context, saved, item = _current(item_id, modules)
        session, _item, _scope, _tracks = backend.validate(context, item.rig)
        _require(session['owner'] == owner and backend._motion_hash(session['edit_action'], session['edit_slot']) == captured,
                 'Native Redo did not recover the captured constant B draft.')
        _require(backend._motion_hash(session['base_action'], session['base_slot']) == a_source_hash,
                 'Undo/Redo changed the protected A reference.')
        report['phases'].append({'phase': phase, 'undo_draft_sha256': before_edit, 'redo_draft_sha256': captured,
                                 'owner_recovered': True, 'protected_A_unchanged': True})
        _write(paths['report'], report)
        phase = 'active_save_reopen'
        _require(bpy.ops.wm.save_as_mainfile(filepath=str(paths['active']), check_existing=False) == {'FINISHED'},
                 'Blender did not confirm the isolated active-session save.')
        _require(Path(bpy.data.filepath).resolve() == paths['active'] and paths['active'].is_file(), 'Saved active fixture path was not confirmed.')
        active_sha = _sha(paths['active'])
        if headful:
            reopen = {'schema': 'x.validation.character-composition-ui-reopen-boundary/1',
                'phase': phase, 'path': str(paths['active']), 'sha256': active_sha,
                'sceneName': str(context.scene.name)}
            # The controller must exit the old owned-scene override before
            # loading. Only plain proof survives; _current/validate below
            # reacquire the new native IDs on the next owned-scene tick.
            del context, saved, item, a_item, session, _item, scope, _scope, _tracks
            del base, _base_slot, prior_b_action, prior_b_source, prior_action
            del ui_context, selections, pb, selection_owner
            yield reopen
        else:
            _require(bpy.ops.wm.open_mainfile(filepath=str(paths['active']), load_ui=False) == {'FINISHED'}, 'The saved active fixture did not reopen.')
        context, saved, item = _current(item_id, modules)
        session, _item, scope, _tracks = backend.validate(context, item.rig)
        _require(session['owner'] == owner and backend._motion_hash(session['edit_action'], session['edit_slot']) == captured,
                 'Save/reopen lost the exact native B draft or ownership.')
        _require(backend._motion_hash(item.custom_action, item.custom_slot) == b_custom_before
            and backend._motion_hash(session['base_action'], session['base_slot']) == a_source_hash,
            'Save/reopen changed protected A or wrote an uncommitted B draft.')
        _require(bpy.ops.character_designer.composition_resume('EXEC_DEFAULT', True) == {'FINISHED'}, 'The saved native session could not explicitly resume.')
        context, saved, item = _current(item_id, modules)
        report['phases'].append({'phase': phase, 'filepath': str(paths['active']), 'sha256': active_sha,
            'owner_recovered': True, 'draft_recovered': True, 'resume_explicit': True,
            'sampled': _full_stack(context, item, modules, seconds, tolerance)})
        _write(paths['report'], report)
        if headful:
            yield _checkpoint(context, item, modules, 'active_saved_reopened', 'Saved/reopened · explicitly resumed B draft')
            context, saved, item = _current(item_id, modules)

        phase = 'commit_close_and_closed_reopen'
        session, _item, _scope, _tracks = backend.validate(context, item.rig)
        _require(bpy.ops.character_designer.composition_close('EXEC_DEFAULT', True) == {'FINISHED'}, 'The actual UI Close operator did not finish.')
        context, saved, item = _current(item_id, modules)
        _require(not backend.active(item.rig) and not item.rig.animation_data.use_tweak_mode,
                 'Close left an active composition/native Tweak session.')
        a_item = modules['animation_worklist']._item(saved, a_item_id)
        committed = backend._motion_hash(item.custom_action, item.custom_slot)
        _require(committed != b_custom_before, 'Close did not commit the real captured B change.')
        _require(backend._motion_hash(a_item.custom_action, a_item.custom_slot) == a_custom_before,
                 'B Close wrote the unmodified explicit A work copy.')
        _require(owner in item.rig.get(backend.DRAFTS, {}) and owner + ':base' in item.rig.get(backend.DRAFTS, {}),
                 'Close discarded a retained native B or A draft.')
        restored_snapshot = ua._snapshot(context, item.rig)
        _require(restored_snapshot == prior_snapshot and
                 item.rig.mode == prior_mode and
                 (item.rig.animation_data.action.name if item.rig.animation_data.action else '') == prior_action_name,
                 'Close did not recover the exact prior artist Action/slot/pose/time settings.')
        _require(bpy.ops.wm.save_as_mainfile(filepath=str(paths['closed']), check_existing=False) == {'FINISHED'},
                 'Blender did not confirm the isolated closed-session save.')
        closed_sha = _sha(paths['closed'])
        if headful:
            reopen = {'schema': 'x.validation.character-composition-ui-reopen-boundary/1',
                'phase': phase, 'path': str(paths['closed']), 'sha256': closed_sha,
                'sceneName': str(context.scene.name)}
            del context, saved, item, a_item, session, _item, scope, _scope, _tracks
            yield reopen
        else:
            _require(bpy.ops.wm.open_mainfile(filepath=str(paths['closed']), load_ui=False) == {'FINISHED'}, 'The closed fixture did not reopen.')
        context, saved, item = _current(item_id, modules)
        _require(not backend.active(item.rig) and backend._motion_hash(item.custom_action, item.custom_slot) == committed,
                 'Closed save/reopen lost B or resumed a composition implicitly.')
        drafts = item.rig.get(backend.DRAFTS, {})
        _require(owner in drafts and backend._motion_hash(drafts[owner], item.custom_slot) == captured,
                 'Closed save/reopen lost the retained exact B draft.')
        report['phases'].append({'phase': phase, 'filepath': str(paths['closed']), 'sha256': closed_sha,
            'B_committed': True, 'A_workcopy_unchanged': True, 'prior_state_restored': True,
            'retained_B_and_A_drafts': True, 'implicit_resume': False})
        _write(paths['report'], report)

        phase = 'positive_A_only_edit_commit'
        report['phases'].append({'phase': phase, **_positive_a_edit(context, item, a_item_id, modules, config, seconds)})
        _require(bpy.ops.wm.save_as_mainfile(filepath=str(paths['a_edited']), check_existing=False) == {'FINISHED'}
            and Path(bpy.data.filepath).resolve() == paths['a_edited'] and paths['a_edited'].is_file(),
            'Blender did not confirm the isolated positive-A-edit save.')
        report['phases'][-1].update(filepath=str(paths['a_edited']), sha256=_sha(paths['a_edited']))
        report['complete'] = True
        _write(paths['report'], report)
        return report
    except Exception:
        report['failure_phase'], report['traceback'] = phase, traceback.format_exc()
        _write(paths['report'], report)
        raise
    finally:
        # This is the same root-owned isolated process, not the user's prefs.
        bpy.context.preferences.edit.use_global_undo = prior_global_undo


def run(context, item_id, config):
    """Original synchronous entry; no timer/UI checkpoint unless opted in."""
    _require(not config.get('headfulCheckpoints'), 'Use the admitted standalone timer runner for opt-in headful checkpoints.')
    return _drain(run_steps(context, item_id, config))
