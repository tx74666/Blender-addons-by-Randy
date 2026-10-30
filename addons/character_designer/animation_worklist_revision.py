"""Local current-Rig work revision prototype; no historical restore/retarget.

A separate checkpoint owns old row references and a fresh current capture. An
Armature reference is never stored on a new Action: current data is resolved
only through the unchanged historical Rig reference table. Ordinary worklist
adoption and old-row validation remain unchanged. UI integration must reject
Unity Sync/registration for these local-only rows.
"""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import uuid

import bpy

RECORD = 'character_designer_worklist_current_checkpoint_v1'
REFS = 'character_designer_worklist_current_refs_v1'
CHECKPOINT = 'character_designer_worklist_current_checkpoint_action_v1'
TOKEN = 'character_designer_worklist_current_token_v1'
SCHEMA = 'character-designer.local-worklist-current-revision/1'
# Editor/scan state and the readable row name are not ownership claims.
_IDENTITY_FIELDS = frozenset({'current_rig_revision', 'item_id', 'work_item_id',
    'clip_key', 'rig', 'custom_action', 'custom_slot', 'source_action', 'source_slot',
    'source_file', 'source_hash', 'new_animation', 'new_action_id', 'manifest_path',
    'link_identity', 'model_file', 'model_sha256', 'export_rig_name', 'workspace_path',
    'workspace_identity', 'composition_json', 'composition_sha256', 'application_role',
    'edit_scope', 'source_data', 'custom_data'})


def _require(value, message):
    if not value:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _modules():
    from . import animation_worklist, animation_worklist_target, unity_animation
    from . import animation_pose_test, body_rest_adoption, body_region
    from . import animation_worklist_fingerprint, animation_composition, body_original_mode
    return SimpleNamespace(worklist=animation_worklist, target=animation_worklist_target,
        ua=unity_animation, pose=animation_pose_test, adoption=body_rest_adoption,
        body=body_region, fingerprint=animation_worklist_fingerprint,
        composition=animation_composition, original=body_original_mode)


def _encode(value, references, history, rig, *, custom=None, token=None):
    if isinstance(value, bpy.types.ID):
        if value is custom:
            return {'$custom': token}
        if isinstance(value, bpy.types.Armature):
            keys = sorted(key for key, item in history.items() if item is value)
            _require(value is rig.data and keys,
                     'An Armature reference has no exact existing historical owner; nothing was rebased.')
            return {'$history_ref': keys[0]}
        for key, item in references.items():
            if item is value:
                return {'$id': key}
        key = 'id_' + str(len(references))
        references[key] = value
        return {'$id': key}
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        _require(math.isfinite(value), 'A current revision value is not finite.')
        return value
    if hasattr(value, 'items'):
        _require(all(type(key) is str and not key.startswith('$') for key in value),
                 'Unsupported current-revision property keys; original data was kept.')
        return {key: _encode(item, references, history, rig, custom=custom, token=token)
                for key, item in value.items()}
    if not isinstance(value, (str, bytes)) and hasattr(value, '__iter__'):
        return [_encode(item, references, history, rig, custom=custom, token=token) for item in value]
    raise ValueError('Unsupported current-revision value: ' + type(value).__name__)


def _decode(value, references, history, rig, *, custom=None, token=None):
    if isinstance(value, dict):
        if set(value) == {'$history_ref'}:
            result = history.get(value['$history_ref'])
            _require(result is rig.data and isinstance(result, bpy.types.Armature),
                     'The exact historical current-data reference changed.')
            return result
        if set(value) == {'$custom'}:
            _require(custom is not None and value['$custom'] == token,
                     'The new Custom Action token changed.')
            return custom
        if set(value) == {'$id'}:
            result = references.get(value['$id'])
            _require(isinstance(result, bpy.types.ID) and not isinstance(result, bpy.types.Armature),
                     'A new checkpoint reference is missing or refers directly to Armature data.')
            return result
        _require(not any(key.startswith('$') for key in value), 'Unknown revision reference tag.')
        return {key: _decode(item, references, history, rig, custom=custom, token=token)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item, references, history, rig, custom=custom, token=token) for item in value]
    _require(value is None or type(value) in (str, bool, int, float), 'Unknown revision primitive.')
    _require(type(value) is not float or math.isfinite(value), 'Nonfinite revision primitive.')
    return value


def _row(item):
    result = {}
    for prop in item.bl_rna.properties:
        name = prop.identifier
        if name == 'rna_type' or prop.is_readonly:
            continue
        _require(prop.type != 'COLLECTION', 'Nested work-item collections cannot be archived by this prototype.')
        value = getattr(item, name)
        result[name] = list(value) if getattr(prop, 'is_array', False) else value
    _require('current_rig_revision' in result, 'Refresh the current-revision worklist UI first.')
    return result


def _replace_rows(saved, rows):
    saved.items.clear()
    for values in rows:
        item = saved.items.add()
        for key, value in values.items():
            setattr(item, key, value)


def _action_hash(action, handle, api):
    proof = api.fingerprint.fingerprint_action(action, handle)
    _require(proof.get('fingerprint_known') is True and proof.get('action_fingerprint'),
             'This Action cannot be copied with a complete curve proof: ' + proof.get('proof_reason', 'unknown'))
    return proof['action_fingerprint']


def _no_action_ID_properties(value):
    if isinstance(value, bpy.types.ID):
        raise ValueError('An unknown Action ID property would add dependencies; the original Action was kept.')
    if hasattr(value, 'items'):
        for item in value.values():
            _no_action_ID_properties(item)
    elif not isinstance(value, (str, bytes)) and hasattr(value, '__iter__'):
        for item in value:
            _no_action_ID_properties(item)
    else:
        _require(value is None or type(value) in (str, bool, int, float), 'Unknown authored Action property.')
        _require(type(value) is not float or math.isfinite(value), 'Nonfinite authored Action property.')


def _admit(context, rig, api):
    api.worklist._idle(context)
    api.pose._admission(context, rig, _purpose='ANIMATION')
    _require(not api.pose.active(rig), 'Preserve or finish the old Base Pose test before creating a current revision.')
    _require(not api.original.active(rig) and not api.composition.active(rig)
             and not api.ua.active_preview(rig), 'Close the unrelated preview before using a current revision.')
    _require('character_designer_rigging_session_v1' not in rig,
             'Rigging input sessions are outside the local current-revision prototype.')
    _require(api.adoption.verified_revision(rig), 'The current Body Rest/control revision did not validate.')
    return api.adoption.recovery_owners(rig)


def _store(checkpoint, value, references):
    checkpoint[RECORD] = _json({'payload': value, 'sha256': _digest(value)})
    checkpoint[REFS] = references
    checkpoint.use_fake_user = True


def _read(context, item, api):
    rig, custom = item.rig, item.custom_action
    token = item.current_rig_revision
    _require(type(token) is str and uuid.UUID(hex=token).hex == token, 'Invalid current-revision row token.')
    _require(custom is not None and custom.library is None and custom.get(TOKEN) == token,
             'The current local Custom Action identity changed.')
    checkpoint = custom.get(CHECKPOINT)
    _require(isinstance(checkpoint, bpy.types.Action) and not checkpoint.library
             and checkpoint.use_fake_user, 'The independent current checkpoint is unavailable.')
    envelope = json.loads(checkpoint[RECORD])
    _require(set(envelope) == {'payload', 'sha256'}
             and envelope['sha256'] == _digest(envelope['payload']), 'The current checkpoint changed.')
    references = dict(checkpoint[REFS])
    _require(all(isinstance(value, bpy.types.ID) and not isinstance(value, bpy.types.Armature)
                 and value is not checkpoint for value in references.values()),
             'The checkpoint has an unsupported direct or self reference.')
    history = dict(rig[api.target.REFS])
    value = _decode(envelope['payload'], references, history, rig, custom=custom, token=token)
    _require(value['schema'] == SCHEMA and value['rig'] is rig
             and value['scene'] is context.scene and value['view_layer'] == context.view_layer.name,
             'Return to the exact current-revision scene, rig and view layer.')
    _require(rig[api.target.SESSION] == value['history_raw']
             and set(history) == set(value['history_refs'])
             and all(history[key] is owner for key, owner in value['history_refs'].items()),
             'The immutable historical Main Rig record or actual ID references changed.')
    _require(_admit(context, rig, api) == value['historical_owners'],
             'Historical Armature owners changed; no old record was rebased.')
    _require(api.ua._rest_state(rig.data) == value['current_rest']
             and api.pose._structure(rig) == value['current_structure'],
             'The new revision Rest or control graph changed; its record was kept.')
    api.target._validate_capture(context, value['capture'])
    matches = [row for row in value['rows'] if row['item_id'] == item.item_id
               and row['current_rig_revision'] == token and row['custom_action'] is custom]
    _require(len(matches) == 1, 'The exact new row token/Action is not owned by this checkpoint.')
    expected = dict(matches[0])
    actual = _row(item)
    _require({key: value for key, value in actual.items() if key in _IDENTITY_FIELDS}
             == {key: value for key, value in expected.items() if key in _IDENTITY_FIELDS}, 'The new work-item fields or ID references changed.')
    _require(not any(key in custom for key in api.target._action_motion_keys())
             and not api.target._data_reference_keys(custom, rig.data)
             and not api.target._data_reference_keys(checkpoint, rig.data),
             'A new Action gained historical recovery permissions or current-data ownership.')
    custom_props = {key: value for key, value in custom.items() if key not in {TOKEN, CHECKPOINT}}
    _no_action_ID_properties(custom_props)
    _require(item.source_action is expected['source_action'] and item.source_action.library is not None
             and item.source_action is not custom, 'The independent read-only Source identity changed.')
    api.worklist._check_baseline(item)
    _require(_action_hash(item.source_action, item.source_slot, api) == value['source_curve_hashes'][item.item_id],
             'The new local Source baseline curve content changed.')
    api.worklist._slot(custom, item.custom_slot)
    _validate_paths(rig, custom, item.custom_slot, api, value['capture'])
    return value


def validate(context, item_id):
    """Authorize only this newly captured local row; never an old work item."""
    api = _modules()
    item = api.worklist._item(api.worklist.state(context), item_id)
    _require(bool(getattr(item, 'current_rig_revision', '')), 'This is an ordinary historical work item.')
    return _read(context, item, api)


def _no_native_dependencies(action):
    # Reverse Blender's actual ID user map, including non-IDProperty pointers
    # such as TimelineMarker.camera. Do not infer serialization from IDprops.
    pointer = action.as_pointer()
    dependencies = [value for value, owners in bpy.data.user_map().items()
                    if any(owner.as_pointer() == pointer for owner in owners)]
    _require(not dependencies, 'The local Source has native ID dependencies: '
             + ', '.join(value.name_full for value in dependencies))


def _remove_action(action):
    try:
        pointer = action.as_pointer()
    except ReferenceError:
        return
    action = next((value for value in bpy.data.actions if value.as_pointer() == pointer), None)
    if action is None:
        return
    _require(action.users - int(action.use_fake_user) == 0,
             'A transaction Action gained a foreign user; it and its cache were kept.')
    fake = action.use_fake_user
    try:
        action.use_fake_user = False
        bpy.data.actions.remove(action, do_unlink=False)
    except Exception:
        # A native failure can leave the same ID present. Restore only that ID,
        # including when another RNA wrapper or a same-name linked ID exists.
        retained = next((value for value in bpy.data.actions if value.as_pointer() == pointer), None)
        if retained is not None:
            retained.use_fake_user = fake
        raise


def _remove_library(library):
    pointer = library.as_pointer()
    current = next((value for value in bpy.data.libraries if value.as_pointer() == pointer), None)
    if current is None:
        return
    _require(current.users == 0,
             'A transaction Library gained a foreign user; it and its cache were kept.')
    bpy.data.libraries.remove(current, do_unlink=False)


def _validate_paths(rig, action, handle, api, capture):
    """Only raw Pose transforms/numeric properties covered by fresh rollback."""
    entry = next(value for value in capture['targets'] if value['target'] is rig)
    supported = {}
    for name, saved in entry['snapshot']['pose'].items():
        prefix = rig.pose.bones[name].path_from_id()
        for field in ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale'):
            supported[prefix + '.' + field] = len(saved[field])
        for key in entry['properties'][name]:
            supported[prefix + '[' + json.dumps(key, ensure_ascii=False) + ']'] = 1
    for curve in api.fingerprint._selected_curves(action, handle):
        path, index = curve.data_path, curve.array_index
        _require(path in supported, 'This local revision cannot fully roll back animation channel: ' + path)
        try:
            value = rig.path_resolve(path)
        except (ValueError, KeyError, AttributeError) as exc:
            raise ValueError('The current Rig has no animation channel: ' + path) from exc
        width = len(value) if (not isinstance(value, (str, bytes, bpy.types.ID))
                               and hasattr(value, '__len__')) else 1
        _require(width == supported[path] and type(index) is int and 0 <= index < width,
                 'The current Rig has no animation array component: ' + path + '[' + str(index) + ']')


def _restore_current(context, rig, capture, api):
    """Replay raw overrides after frame_set evaluates the fresh current Action."""
    autokey = capture['targets'][0]['snapshot'].get(
        'autokey', context.scene.tool_settings.use_keyframe_insert_auto)
    apply_error = None
    context.scene.tool_settings.use_keyframe_insert_auto = False
    try:
        try:
            # Its native restore helper also writes snapshot.autokey before
            # frame_set. Use a detached execution view, keeping the original
            # fresh snapshot unchanged and Auto Key off for that whole call.
            execution = dict(capture, targets=[dict(entry, snapshot=dict(
                entry['snapshot'], autokey=False)) for entry in capture['targets']])
            api.target._apply_capture(context, rig, execution)
        except Exception as error:
            apply_error = error
        # _apply_capture can restore Auto Key and then fail during frame_set.
        # Even in that case, reassert the raw current override without keying.
        context.scene.tool_settings.use_keyframe_insert_auto = False
        for entry in capture['targets']:
            owner = entry['target']
            for name, saved in entry['snapshot']['pose'].items():
                pb = owner.pose.bones[name]
                pb.rotation_mode = saved['rotation_mode']
                for field in ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale'):
                    setattr(pb, field, saved[field])
                for key, value in entry['properties'][name].items():
                    pb[key] = value
                for key, value in entry['locks'][name].items():
                    setattr(pb, key, value)
                for name, values in entry['constraints'][name].items():
                    constraint = pb.constraints[name]
                    for key, value in values.items():
                        if key != 'type':
                            setattr(constraint, key, value)
        context.view_layer.update()
    finally:
        context.scene.tool_settings.use_keyframe_insert_auto = autokey
    if apply_error is not None:
        raise RuntimeError('Fresh current-state restore needs review: ' + str(apply_error)) from apply_error


def create(context, item_id, *, activate_new=False):
    """Copy all active Customs to one independent, current-Rest checkpoint.

    item_id selects the returned/optionally activated new row. Complete old
    membership remains in the checkpoint, never Retired. Rollback reconstructs
    row fields/ID bindings rather than CollectionProperty wrapper pointers.
    No packet import, historical pose restore or Unity sidecar is involved.
    """
    _require(type(activate_new) is bool, 'activate_new must be a boolean.')
    api = _modules()
    saved = api.worklist.state(context)
    selected = api.worklist._item(saved, item_id)
    rig = selected.rig
    owners = _admit(context, rig, api)
    _require(rig.animation_data is not None and rig.animation_data.action is None,
             'Create the current revision immediately after preserving the stale scratch binding.')
    capture = api.target._capture(context, rig)
    api.target._validate_capture(context, capture)
    old_rows = [_row(item) for item in saved.items]
    _require(old_rows and all(not row['current_rig_revision'] and row['rig'] is rig for row in old_rows)
             and len({row['item_id'] for row in old_rows}) == len(old_rows),
             'Mixed rigs or revision rows cannot be silently reclassified.')
    originals = []
    for row in old_rows:
        original = row['custom_action']
        _require(original is not None and not original.library and not original.asset_data,
                 'Every preserved row needs a local editable Custom Action.')
        _require(not any(key in original for key in ('character_designer_rigging_capture_v1',
                 'character_designer_rigging_input_action_v1', RECORD, TOKEN, CHECKPOINT)),
                 'Rigging or already-revised Actions need a separate explicit workflow.')
        _require(not any(row.get(key) for key in ('composition_json', 'composition_sha256', 'application_role')),
                 'Composition work is outside this local current-Rig revision.')
        slot = api.worklist._slot(original, row['custom_slot'])
        _validate_paths(rig, original, slot.handle, api, capture)
        originals.append(dict(action=original, slot=slot.handle,
            curves=_action_hash(original, slot.handle, api), name=original.name_full,
            fake=original.use_fake_user, props=api.body._hash(dict(original.items()))))
    history_raw, history = rig[api.target.SESSION], dict(rig[api.target.REFS])
    _require(isinstance(history_raw, str) and any(value is rig.data for value in history.values()),
             'No existing exact historical current-data reference is available.')
    full_rest, structure = api.ua._rest_state(rig.data), api.pose._structure(rig)
    references = {}
    _encode(dict(rig=rig, scene=context.scene, history_refs=history,
                 capture=capture, old_rows=old_rows), references, history, rig)
    previous_index, previous_status, previous_error = saved.active_index, saved.status, saved.has_error
    before_actions = {action.as_pointer() for action in bpy.data.actions}
    before_libraries = {library.as_pointer() for library in bpy.data.libraries}
    checkpoint, rows, receipts, temporaries, customs = None, [], [], [], []
    curve_hashes, selected_id = {}, None
    replaced = activation_attempted = False
    try:
        for old_row, before in zip(old_rows, originals):
            original = before['action']
            token, new_id, work_id = (uuid.uuid4().hex for _ in range(3))
            custom = original.copy()
            customs.append(custom)
            custom.name = original.name + ' · Current Rig'
            for key in api.target._action_motion_keys():
                custom.pop(key, None)
            # No arbitrary ID reference may pull the artist into the cache.
            _no_action_ID_properties(dict(custom.items()))
            custom.use_fake_user = True
            custom_slot = api.worklist._slot(custom, before['slot'])
            _require(_action_hash(custom, custom_slot.handle, api) == before['curves'],
                     'A new Custom did not retain the original complete curve content.')
            _require(api.adoption.recovery_owners(rig) == owners,
                     'A new Custom unexpectedly changed the Armature owner audit.')
            temporary = custom.copy()
            temporaries.append(temporary)
            temporary.use_fake_user = False
            _no_action_ID_properties(dict(temporary.items()))
            _no_native_dependencies(temporary)
            receipt = {}
            receipts.append(receipt)
            source, path, cache_hash = api.worklist._linked_baseline(
                saved, temporary, new_id, original.name + ' · Current Rig', _ownership=receipt)
            source_slot = api.worklist._slot(source, before['slot'])
            _require(_action_hash(source, source_slot.handle, api) == before['curves'],
                     'A new read-only Source did not retain complete curve content.')
            row = dict(old_row)
            row.update(item_id=new_id, work_item_id=work_id, clip_key='local-current:' + new_id,
                name=old_row['name'], current_rig_revision=token,
                source_action=source, custom_action=custom, source_slot=source_slot.handle,
                custom_slot=custom_slot.handle, side='CUSTOM', source_file=path,
                source_hash=cache_hash, new_animation=False, new_action_id='', manifest_path='',
                link_identity=_json({'schema': SCHEMA, 'token': token, 'local_only': True,
                                     'historical_item_id': old_row['item_id']}))
            for key in ('source_baseline_receipt', 'last_synced_receipt', 'scan_reason', 'retirement_reason'):
                if key in row:
                    row[key] = ''
            if 'scan_state' in row:
                row['scan_state'] = 'NOT_SCANNED'
            for key in ('sync_selected', 'b_edited', 'composition_changed', 'effective_inputs_changed', 'export_needed'):
                if key in row:
                    row[key] = False
            for key in ('source_data', 'custom_data'):
                if key in row:
                    row[key] = None
            rows.append(row)
            curve_hashes[new_id] = before['curves']
            if old_row['item_id'] == item_id:
                selected_id = new_id
        # Dispose our source-writing copies while failure is still transactional.
        for temporary in temporaries:
            _remove_action(temporary)
        checkpoint = bpy.data.actions.new('Current Rig Work Checkpoint')
        payload = dict(schema=SCHEMA, revision_id=uuid.uuid4().hex, rig=rig, scene=context.scene,
            view_layer=context.view_layer.name, history_raw=history_raw, history_refs=history,
            historical_owners=owners, capture=capture, current_rest=full_rest,
            current_structure=structure, old_rows=old_rows, previous_index=previous_index,
            source_curve_hashes=curve_hashes, rows=rows,
            semantic_scope='current-Rest channel copies; old world motion equivalence not established')
        references = {}
        encoded = _encode(payload, references, history, rig)
        _store(checkpoint, encoded, references)
        for row in rows:
            row['custom_action'][TOKEN] = row['current_rig_revision']
            row['custom_action'][CHECKPOINT] = checkpoint
            probe = SimpleNamespace(**row)
            probe.bl_rna = selected.bl_rna
            _require(_read(context, probe, api)['old_rows'] == old_rows,
                     'The complete original row archive did not read back exactly.')
        for before in originals:
            original = before['action']
            _require(original.name_full == before['name'] and original.use_fake_user == before['fake']
                     and api.body._hash(dict(original.items())) == before['props']
                     and _action_hash(original, before['slot'], api) == before['curves'],
                     'A preserved original Custom changed during revision creation.')
        replaced = True
        _replace_rows(saved, rows)
        saved.active_index = next(i for i, row in enumerate(saved.items) if row.item_id == selected_id)
        for item in saved.items:
            _read(context, item, api)
        _require(api.adoption.recovery_owners(rig) == owners,
                 'Data ownership changed after current rows were installed.')
        if activate_new:
            activation_attempted = True
            activate(context, selected_id, 'CUSTOM')
        saved.status, saved.has_error = 'Local current-Rig versions created; historical rows retained in checkpoint.', False
        return next(row for row in saved.items if row.item_id == selected_id)
    except Exception as original_error:
        cleanup_errors = []
        def attempt(call):
            try:
                call()
            except Exception as error:
                cleanup_errors.append(str(error))
        if activation_attempted:
            # Only the actual fresh current snapshot, never old session data.
            attempt(lambda: _restore_current(context, rig, capture, api))
        if replaced:
            attempt(lambda: _replace_rows(saved, old_rows))
        saved.active_index, saved.status, saved.has_error = previous_index, previous_status, previous_error
        for custom in customs:
            attempt(lambda custom=custom: custom.pop(CHECKPOINT, None))
            attempt(lambda custom=custom: custom.pop(TOKEN, None))
        if checkpoint is not None:
            attempt(lambda: checkpoint.pop(REFS, None))
        owned_actions = [action for action in (checkpoint, *customs, *temporaries) if action is not None]
        owned_libraries = []
        for receipt in receipts:
            candidates = [receipt.get('action')]
            loaded = receipt.get('load_result')
            if loaded is not None:
                candidates.extend(getattr(loaded, 'actions', ()))
            for action in candidates:
                if (isinstance(action, bpy.types.Action)
                        and action.as_pointer() not in before_actions
                        and not any(value is action for value in owned_actions)):
                    owned_actions.append(action)
                    library = action.library
                    if library is not None and library.as_pointer() not in before_libraries:
                        if str(Path(bpy.path.abspath(library.filepath)).resolve()) == str(Path(receipt['path']).resolve()):
                            if not any(value is library for value in owned_libraries):
                                owned_libraries.append(library)
            library = receipt.get('library')
            if (library is not None and library.as_pointer() not in before_libraries
                    and str(Path(bpy.path.abspath(library.filepath)).resolve()) == str(Path(receipt['path']).resolve())
                    and not any(value is library for value in owned_libraries)):
                owned_libraries.append(library)
        for action in owned_actions:
            attempt(lambda action=action: _remove_action(action))
        for library in owned_libraries:
            attempt(lambda library=library: _remove_library(library))
        # An unexpected foreign user keeps both the new ID and its cache file.
        if not cleanup_errors:
            for receipt in receipts:
                if receipt.get('path'):
                    attempt(lambda receipt=receipt: Path(receipt['path']).unlink(missing_ok=True))
        attempt(lambda: _require(rig[api.target.SESSION] == history_raw
            and dict(rig[api.target.REFS]) == history
            and api.adoption.recovery_owners(rig) == owners,
            'Rollback could not re-prove the preserved historical audit.'))
        if cleanup_errors:
            raise RuntimeError('Current revision failed; rollback needs review: ' + '; '.join(cleanup_errors)) from original_error
        raise


def activate(context, item_id, side='CUSTOM'):
    """Bind only the exact new local row on its validated current Rest graph."""
    _require(side in {'CUSTOM', 'SOURCE'}, 'Choose Source or Custom.')
    api = _modules()
    value = validate(context, item_id)
    saved = api.worklist.state(context)
    item = api.worklist._item(saved, item_id)
    rig = item.rig
    attempt = api.target._capture(context, rig)
    api.target._validate_capture(context, attempt)
    previous_index, previous_side = saved.active_index, item.side
    previous_status, previous_error = saved.status, saved.has_error
    previous_target = context.window_manager.character_designer_animation.target
    try:
        action = item.custom_action if side == 'CUSTOM' else item.source_action
        slot = api.worklist._slot(action, item.custom_slot if side == 'CUSTOM' else item.source_slot)
        start, end = map(float, action.frame_range)
        _require(math.isfinite(start) and math.isfinite(end) and end >= start, 'Invalid local Action range.')
        api.ua._set_playing(context, False)
        ad = rig.animation_data_create()
        ad.action, ad.action_slot = action, slot
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.0
        context.scene.tool_settings.use_keyframe_insert_auto = False
        context.scene.frame_start = context.scene.frame_preview_start = math.floor(start)
        context.scene.frame_end = context.scene.frame_preview_end = max(math.ceil(end), math.floor(start))
        context.scene.use_preview_range = True
        api.ua._set_frame(context.scene, start)
        for selected in tuple(context.selected_objects):
            selected.select_set(False)
        rig.hide_set(False); rig.select_set(True)
        context.view_layer.objects.active = rig
        context.window_manager.character_designer_animation.target = rig
        context.view_layer.update()
        item.side = side
        saved.active_index = next(i for i, row in enumerate(saved.items) if row.item_id == item_id)
        _require(api.adoption.recovery_owners(rig) == value['historical_owners']
                 and api.ua._rest_state(rig.data) == value['current_rest']
                 and api.pose._structure(rig) == value['current_structure'],
                 'Native activation changed protected Rest, graph or historical owners.')
        saved.status, saved.has_error = 'Local current-Rig ' + side + '; Unity handoff not prepared.', False
        return action
    except Exception as original_error:
        # This is the fresh current checkpoint, never the old historical capture.
        rollback_error = None
        try:
            _restore_current(context, rig, attempt, api)
        except Exception as error:
            rollback_error = error
        finally:
            saved.active_index, item.side = previous_index, previous_side
            saved.status, saved.has_error = previous_status, previous_error
            context.window_manager.character_designer_animation.target = previous_target
        if rollback_error is not None:
            raise RuntimeError('Local activation failed; rollback needs review: '
                               + str(rollback_error)) from original_error
        raise
