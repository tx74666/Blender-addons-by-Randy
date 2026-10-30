"""Explicit Unity-list reconciliation on the artist's existing Main Rig.

Planning is read-only. A confirmation binds the complete managed membership
and every incoming file; a later change requires a new preview. Retiring keeps
the complete row and its native IDs, rather than deleting or rebinding drafts.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import bpy

from . import animation_link as links, animation_link_source as source
from . import animation_worklist as worklist, animation_workspace as workspace
from . import animation_worklist_target as target, unity_animation as ua
from .animation_worklist_fingerprint import fingerprint_action


_WORKSPACE_FIELDS = ('workspace_path', 'workspace_identity', 'workspace_id',
    'target_guid', 'target_name', 'model_file', 'model_sha256',
    'prepare_request_id', 'prepared_work_item_ids', 'connection_check_failed')


def _values(row):
    """Copy RNA values without serializing native ID pointers into names."""
    properties = getattr(getattr(row, 'bl_rna', None), 'properties', None)
    if properties is None:
        return dict(vars(row))  # Pure contract fixtures.
    return {prop.identifier: getattr(row, prop.identifier) for prop in properties
            if prop.identifier != 'rna_type' and not prop.is_readonly}


def _append(collection, values):
    row = collection.add()
    try:
        for key, value in values.items():
            setattr(row, key, value)
    except Exception:
        collection.remove(len(collection) - 1)
        raise
    return row


def _replace(collection, rows):
    collection.clear()
    for values in rows:
        _append(collection, values)


def _stable(value):
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, dict):
        return {str(key): _stable(entry) for key, entry in value.items()}
    if isinstance(value, (tuple, list)):
        return [_stable(entry) for entry in value]
    pointer = getattr(value, 'as_pointer', None)
    return {'pointer': int(pointer()) if callable(pointer) else id(value),
            'name': getattr(value, 'name_full', getattr(value, 'name', '')),
            'library': getattr(getattr(value, 'library', None), 'filepath', None)}


def _digest(value):
    return hashlib.sha256(json.dumps(_stable(value), ensure_ascii=False,
        allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _pin(path):
    path = Path(bpy.path.abspath(str(path))).expanduser().resolve()
    if not path.is_file():
        raise ValueError('A synchronization input is missing: ' + str(path))
    return str(path), links._sha256(path)


def _snapshot(context, saved, rig, *, include_custom_contents=True):
    result = {'workspace': {key: getattr(saved, key, None) for key in _WORKSPACE_FIELDS},
        'active_index': saved.active_index, 'rig': rig,
        'items': [_values(row) for row in saved.items],
        'retired': [_values(row) for row in saved.retired_items],
        'catalog': [_values(row) for row in saved.catalog],
        'scene': context.scene, 'view_layer': context.view_layer.name,
        'rig_data': rig.data, 'target_session': target.capture_session_metadata(rig),
        'active_action': getattr(getattr(rig, 'animation_data', None), 'action', None)}
    if include_custom_contents:
        result['custom_contents'] = [_custom_content(row) for row in saved.items]
    return result


def _custom_content(row):
    """Bind an explicit confirmation to authored curves, not just ID pointers."""
    action = row.custom_action
    if action is None:
        return {'item_id': row.item_id, 'missing': True}
    proof = fingerprint_action(action, row.custom_slot)
    if not proof.get('fingerprint_known'):
        if getattr(action, 'is_empty', False):
            # A genuinely empty new Action is also a complete content state;
            # its first authored curve changes this proof on confirmation.
            proof = {'empty': True}
        else:
            raise ValueError('Cannot verify the current Custom content for ' + row.name +
                             ': ' + proof.get('proof_reason', 'Incomplete Action proof.'))
    result = {'item_id': row.item_id, 'motion': proof}
    if ('character_designer_rigging_capture_v1' in action
            or 'character_designer_rigging_input_action_v1' in action):
        from . import unity_animation_rigging as rigging
        result['rigging'] = rigging.input_proof(row.rig, action, row.custom_slot,
                                               row.work_item_id, source=False)
    return result


def _main_rig(context, saved):
    rig = getattr(getattr(context.scene, 'character_designer_setup', None), 'rig', None)
    if (rig is None or rig.type != 'ARMATURE' or rig.library or rig.data.library
            or rig.override_library or getattr(rig.data, 'override_library', None)
            or context.scene.objects.get(rig.name) is not rig
            or context.view_layer.objects.get(rig.name) is not rig):
        raise ValueError('List synchronization needs this scene\'s local Main Rig; no model was imported.')
    if saved.rig is not None and saved.rig is not rig:
        raise ValueError('The current worklist and Main Rig are different objects. Their drafts were kept.')
    if any(row.rig is not rig for row in saved.items):
        raise ValueError('A current animation belongs to another rig; it was not reassigned.')
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Finish Edit Mode before synchronizing the animation list.')
    if ua.active_preview(rig):
        raise ValueError('Restore the unrelated Unity preview before synchronizing the list.')
    from . import animation_composition as composition
    if composition.active(rig):
        raise ValueError('Close Composition before synchronizing; its draft was kept.')
    return rig


def _same_row(row, clip, link, record):
    if getattr(row, 'new_animation', False):
        return False, 'A local new Action is not in the Unity list.'
    try:
        original = json.loads(row.link_identity)
        if (row.clip_key != worklist._clip_key(clip)
                or getattr(row, 'work_item_id', '') != workspace.work_item_id(clip)
                or workspace.route_identity(original) != workspace.route_identity(clip)):
            return False, 'Unity changed this animation\'s source or destination.'
        if (Path(row.manifest_path).resolve() != Path(link['_manifest_path']).resolve()
                or links._identity(original) != links._identity(link)
                or Path(row.model_file).resolve() != Path(record['modelFile']).resolve()
                or row.model_sha256.lower() != record['modelSha256'].lower()):
            return False, 'Unity rebuilt this Source or changed its model revision.'
        worklist._check_baseline(row)
        if row.custom_action is None or row.custom_action.library is not None:
            raise ValueError('The retained local Custom Action is missing.')
        worklist._slot(row.custom_action, row.custom_slot)
        return True, ''
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        return False, 'The saved source proof cannot be reused: ' + str(exc)


def _prepared(context, record, rig):
    """Validate actual files, bind geometry, controls and complete bone domain."""
    pins = dict([_pin(record['_manifest_path']), _pin(record['modelFile'])])
    if pins[str(Path(record['modelFile']).resolve())] != record['modelSha256'].lower():
        raise ValueError('The Unity list model hash differs from its physical model file.')
    adopted = target._load(rig) if target.is_adopted(rig) else None
    if adopted is not None:
        target.ensure_active(context, rig)
    expected_names = frozenset(adopted['mapped_names']) if adopted is not None else None
    prepared, blocked = {}, []
    for clip in record['clips']:
        if clip.get('compositionReferenceSource') is True or clip.get('sourceOnly') is True:
            continue
        name, key = clip['clipName'], worklist._clip_key(clip)
        try:
            if clip.get('needsRebuild') or not clip.get('linkManifest'):
                raise ValueError('Not ready in Unity; finish its preparation first.')
            link = links.load_link(clip['linkManifest'])
            if (link['targetGuid'].lower() != record['targetGuid'].lower()
                    or workspace.route_identity(link) != workspace.route_identity(clip)):
                raise ValueError('The prepared Link has another target, source or destination.')
            model, digest = source._source_model(link, record['modelFile'])
            if (model.resolve() != Path(record['modelFile']).resolve()
                    or digest != record['modelSha256'].lower()):
                raise ValueError('The prepared Link uses another physical model revision.')
            packet, _name = source._packet(link)
            mapping = ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
            names = frozenset(mapping.indices)
            if expected_names is not None and names != expected_names:
                raise ValueError('The prepared animation has a different Main Rig bone inventory.')
            expected_names = names
            if 'rigging' in packet and mapping.controls is None:
                raise ValueError('The Rigging animation has no validated native control plan.')
            if clip.get('composition') is not None or clip.get('editScope') == 'UpperBodyOverride':
                if (not getattr(context.scene.character_designer_animation_worklist,
                                'composition_workspace_path', '')
                        or context.scene.character_designer_animation_worklist.composition_workspace_identity
                                != worklist._identity(record)):
                    raise ValueError('Connect the matching Composition Library before synchronizing this B animation.')
            for path in (link['_manifest_path'], packet['_path']):
                pinned_path, pinned_hash = _pin(path)
                pins[pinned_path] = pinned_hash
            prepared[key] = (clip, link, packet)
        except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
            blocked.append({'name': name, 'reason': str(exc)})
    return prepared, pins, blocked


def _plan(context, workspace_path=None):
    worklist._idle(context)
    saved = worklist.state(context)
    if not hasattr(saved, 'retired_items'):
        raise ValueError('Refresh Character Designer to enable recoverable retired animations.')
    if any(getattr(row, 'current_rig_revision', '') for row in saved.items):
        raise ValueError('These local motions use the current rig. Prepare matching Unity sources before Sync List.')
    path = workspace_path or saved.workspace_path
    if not path:
        raise ValueError('Choose the Unity animation list before synchronizing.')
    record = workspace.load_workspace(bpy.path.abspath(path))
    rig = _main_rig(context, saved)
    if saved.target_guid and saved.target_guid.lower() != record['targetGuid'].lower():
        raise ValueError('The Unity list belongs to another character; Main Rig and its drafts were kept.')
    if saved.workspace_identity:
        old = json.loads(saved.workspace_identity)
        old_project = old[1].get('projectId') if isinstance(old, list) and len(old) == 2 else None
        if old_project and old_project != record.get('projectId'):
            raise ValueError('The Unity list belongs to another project; existing drafts were kept.')
    prepared, pins, blocked = _prepared(context, record, rig)
    retained, extras = [], []
    incoming = {worklist._clip_key(clip): clip for clip in record['clips']
                if clip.get('compositionReferenceSource') is not True and clip.get('sourceOnly') is not True}
    for row in saved.items:
        match = prepared.get(row.clip_key)
        if match is None:
            reason = ('This animation is absent from the Unity list.' if row.clip_key not in incoming
                      else 'Unity has no reusable prepared source for this animation.')
            same = False
        else:
            same, reason = _same_row(row, match[0], match[1], record)
        if same:
            retained.append(row.item_id)
        else:
            extras.append({'item_id': row.item_id, 'name': row.name, 'reason': reason})
    kept_keys = {row.clip_key for row in saved.items if row.item_id in retained}
    add_keys = [key for key in prepared if key not in kept_keys]
    public = {'workspace_path': record['_manifest_path'], 'incoming_identity': worklist._identity(record),
        'retained_ids': retained, 'add_keys': add_keys, 'extras': extras, 'blocked': blocked,
        'incoming_count': len(incoming)}
    public['token'] = _digest({'plan': public, 'files': pins, 'state': _snapshot(context, saved, rig)})
    return public, record, prepared, rig, pins


def plan(context, workspace_path=None):
    """Return a read-only, file-pinned preview for one explicit Sync List click."""
    return _plan(context, workspace_path)[0]


def _set_workspace(saved, record):
    saved.workspace_path, saved.workspace_identity = record['_manifest_path'], worklist._identity(record)
    saved.workspace_id, saved.target_guid = record['workspaceId'], record['targetGuid'].lower()
    saved.target_name = record['targetName']
    saved.model_file, saved.model_sha256 = record['modelFile'], record['modelSha256'].lower()
    worklist._fill_catalog(saved, record)
    saved.connection_check_failed = False


def _retire(saved, extras):
    reasons = {row['item_id']: row['reason'] for row in extras}
    for index in range(len(saved.items) - 1, -1, -1):
        row = saved.items[index]
        if row.item_id not in reasons:
            continue
        archived = _append(saved.retired_items, _values(row))
        if hasattr(archived, 'retirement_reason'):
            archived.retirement_reason = reasons[row.item_id]
        # Persistent PointerProperties retain both IDs. Do not even change
        # fake-user flags: retirement is membership, never an Action edit.
        saved.items.remove(index)


def begin(context, token, *, workspace_path=None, deprecate_extras=False, discard_extras=False):
    """Revalidate the exact preview, then synchronize only native Actions.

    The existing serial queue provides cancellation/retry. Completed additions
    and explicitly confirmed retirements remain recoverable on cancellation.
    No export, model import, scene switch, mesh edit or reweighting is performed.
    """
    if deprecate_extras and discard_extras:
        raise ValueError('Choose either Retire or Discard for the extra animations.')
    preview, record, prepared, rig, pins = _plan(context, workspace_path)
    if not isinstance(token, str) or token != preview['token']:
        raise ValueError('The Unity list or local animations changed. Review Sync List again before confirming.')
    if preview['blocked']:
        raise ValueError('List synchronization is blocked: ' + '; '.join(
            row['name'] + ': ' + row['reason'] for row in preview['blocked']))
    if preview['extras'] and not (deprecate_extras or discard_extras):
        raise ValueError('Review the extra animations and choose Retire or Back. No list changes were made.')
    saved = worklist.state(context)
    # _plan just checked current Custom contents for the confirmation token.
    # Rollback restores membership/session inputs and never consumes that proof.
    previous = _snapshot(context, saved, rig, include_custom_contents=False)
    previous_saved_rig = saved.rig
    previous_catalog_index = saved.catalog_index
    selected_id = worklist.current(context).item_id if worklist.current(context) else ''
    captured = worklist._capture_rig(context, rig)
    was_adopted = target.is_adopted(rig)
    queue = None
    try:
        _retire(saved, preview['extras'])
        # Every retained row has an exact immutable Link proof. Updating the
        # catalog identity does not replace its native Actions or old receipts.
        for row in saved.items:
            if row.item_id in preview['retained_ids'] and row.workspace_path:
                row.workspace_path, row.workspace_identity = record['_manifest_path'], worklist._identity(record)
        _set_workspace(saved, record)
        if target.is_adopted(rig):
            adopted = target._load(rig)
            adopted['workspace_identity'] = saved.workspace_identity
            target._store(rig, adopted)
            target.ensure_active(context, rig)
        elif prepared:
            # Existing adoption validates/restores its own artist capture; it
            # never imports the physical model or binds mesh weights.
            worklist.adopt_main_rig(context)
        saved.active_index = next((i for i, row in enumerate(saved.items)
                                   if row.item_id == selected_id), -1)
        from . import animation_worklist_collection as collection
        queue = collection.begin_add_ready(context, _clip_keys=tuple(preview['add_keys']),
                                            _main_rig=rig, _input_pins=pins)
        if not queue.busy:
            saved.status = saved.collection_status = ('Animation list synchronized. ' +
                str(len(preview['retained_ids'])) + ' existing draft(s) kept; ' +
                str(len(preview['extras'])) + ' retired.')
            saved.has_error = False
        saved.scan_completed = False
        worklist._finish_rig(context, rig, captured)
    except Exception:
        # begin() has not yielded to Blender's event loop. Only this pending
        # ADD can exist, and it must lose its timer before restoring old state.
        if queue is not None:
            collection.abort_pending_add(queue)
        try:
            if not was_adopted and target.is_adopted(rig):
                target.restore_main_rig(context)
        finally:
            for key, value in previous['workspace'].items():
                if value is not None:
                    setattr(saved, key, value)
            _replace(saved.items, previous['items'])
            _replace(saved.retired_items, previous['retired'])
            _replace(saved.catalog, previous['catalog'])
            saved.rig, saved.active_index = previous_saved_rig, previous['active_index']
            saved.catalog_index = previous_catalog_index
            try:
                worklist._restore_rig(context, rig, captured)
            finally:
                # _restore_rig currently restores this too; keep the explicit
                # transaction pin authoritative even if that helper changes.
                target.restore_session_metadata(rig, previous['target_session'])
        raise
    finally:
        captured['data'].use_fake_user = captured['fake_user']
    # The synchronous transaction and its rollback have finished. Explicit
    # Discard commits only after the new import queue was accepted; never delete
    # IDs that a failure above still needs for membership rollback.
    if discard_extras:
        result = _discard_rows(saved, (row['item_id'] for row in preview['extras']))
        message = 'Discarded ' + str(len(result['item_ids'])) + ' old animation(s); '
        message += str(len(result['removed_actions'])) + ' unused Action(s) removed.'
        if result['kept_actions']:
            message += ' Shared, edited or recovery Actions were kept.'
        saved.status, saved.has_error = message, False
    return queue


def restore_retired(context, item_id):
    """Restore managed membership without changing its historical provenance."""
    worklist._idle(context)
    saved = worklist.state(context)
    index = next((i for i, row in enumerate(saved.retired_items) if row.item_id == item_id), None)
    if index is None:
        raise ValueError('This retired animation no longer exists.')
    retired = saved.retired_items[index]
    if any(row.item_id == item_id or row.clip_key == retired.clip_key for row in saved.items):
        raise ValueError('The list already has this work item. Keep both drafts; retire its current row before restoring.')
    rig = _main_rig(context, saved)
    if retired.rig is not rig:
        raise ValueError('This retired animation belongs to another Main Rig; it was not reassigned.')
    worklist._check_baseline(retired)
    if retired.custom_action is None or retired.custom_action.library is not None:
        raise ValueError('This retired animation lost its editable Custom Action.')
    if retired.source_action is not None:
        worklist._slot(retired.source_action, retired.source_slot)
    worklist._slot(retired.custom_action, retired.custom_slot)
    row = _append(saved.items, _values(retired))
    if hasattr(row, 'retirement_reason'):
        row.retirement_reason = ''
    saved.retired_items.remove(index)
    saved.scan_completed = False
    saved.status, saved.has_error = ('Restored ' + row.name +
        '. Its Source and Custom are unchanged; export still requires the matching Unity source.'), False
    return row


def _discard_action(action, row, *, custom, protected=False):
    """Delete only an unreferenced, unauthored member of this exact old row."""
    name = action.name_full
    kept = lambda reason: {'name': name, 'reason': reason}
    if protected:
        return False, kept('Another active or retired work item still uses this Action.')
    if action.asset_data or getattr(action, 'override_library', None):
        return False, kept('Asset or override data was kept.')
    if action.users - int(action.use_fake_user):
        return False, kept('A binding, another work item or a recovery record still uses this Action.')
    # Some current-Rest receipts authenticate passive historical carriers by
    # name and property hash. They need not have incoming native ID users.
    if action.get(target.ACTION_RIG_KEY) is not None:
        return False, kept('This Action carries historical Main Rig recovery; its name and properties were kept.')
    slot_handle = row['custom_slot'] if custom else row['source_slot']
    if (len(action.slots) != 1 or action.slots[0].handle != slot_handle
            or action.slots[0].target_id_type != 'OBJECT'):
        return False, kept('This Action has another slot or an unverified slot.')
    if custom:
        if action.library is not None:
            return False, kept('The Custom Action is linked.')
        if tuple(action.pose_markers) or tuple(action.keys()):
            return False, kept('The Custom Action has markers or additional stored data.')
        original = row['source_action']
        if original is None or original is action or original.library is None:
            return False, kept('An independent prepared Source cannot prove this draft unchanged.')
        try:
            worklist._check_baseline(SimpleNamespace(**row))
        except Exception as exc:
            return False, kept('The unchanged draft has no verified physical Source cache: ' + str(exc))
        before = fingerprint_action(original, row['source_slot'])
        current = fingerprint_action(action, row['custom_slot'])
        if (before.get('fingerprint_known') is not True
                or current.get('fingerprint_known') is not True
                or not before.get('action_fingerprint')
                or before['action_fingerprint'] != current.get('action_fingerprint')):
            return False, kept('Custom edits differ from Source, or their complete motion proof is unknown.')
    else:
        if action.library is None:
            return False, kept('An editable Action cannot be discarded as a read-only Source cache.')
        try:
            worklist._check_baseline(SimpleNamespace(**row))
        except Exception as exc:
            return False, kept('The physical read-only Source cache could not be verified: ' + str(exc))
    if bpy.data.user_map(subset={action}).get(action, set()):
        return False, kept('A native data owner still references this Action.')
    fake_user = action.use_fake_user
    pointer = action.as_pointer()
    try:
        action.use_fake_user = False
        # Never unlink an unexpected user to make a deletion succeed.
        bpy.data.actions.remove(action, do_unlink=False)
    except Exception as exc:
        # A lookup by name can find a different library ID, and native RNA
        # lookups need not return the original Python wrapper.
        existing = next((value for value in bpy.data.actions if value.as_pointer() == pointer), None)
        if existing is None:
            return True, {'name': name, 'warning': 'The Action was removed; native cleanup reported: ' + str(exc)}
        existing.use_fake_user = fake_user
        return False, kept('Cleanup stopped and the Action was kept: ' + str(exc))
    return True, {'name': name}


def _discard_rows(saved, item_ids):
    """Commit exact retired membership first, then clean its own eligible IDs."""
    item_ids = tuple(item_ids)
    wanted = set(item_ids)
    rows = [_values(row) for row in saved.retired_items if row.item_id in wanted]
    if len(rows) != len(wanted):
        raise ValueError('A retired animation changed; no discard was applied.')
    candidates, visited = [], set()
    # Freeze identities while all IDs still exist. Every Custom proof must be
    # read before any Source candidate can be removed.
    for field in ('custom_action', 'source_action'):
        for row in rows:
            action = row[field]
            if action is not None and action.as_pointer() not in visited:
                visited.add(action.as_pointer())
                candidates.append((action, row, field == 'custom_action'))
    lists = [saved] + [getattr(scene, 'character_designer_animation_worklist', None)
                      for scene in getattr(bpy.data, 'scenes', ())]
    def identity(settings):
        pointer = getattr(settings, 'as_pointer', None)
        return int(pointer()) if callable(pointer) else id(settings)
    # Separate Python wrappers may refer to the same scene PropertyGroup.
    lists = list({identity(settings): settings for settings in lists if settings is not None}.values())
    saved_identity = identity(saved)
    protected = {action.as_pointer() for settings in lists if settings is not None
        for row in list(settings.items) + [entry for entry in getattr(settings, 'retired_items', ())
            if identity(settings) != saved_identity or entry.item_id not in wanted]
        for action in (row.source_action, row.custom_action) if action is not None}
    previous = [_values(row) for row in saved.retired_items]
    try:
        for index in range(len(saved.retired_items) - 1, -1, -1):
            if saved.retired_items[index].item_id in wanted:
                saved.retired_items.remove(index)
    except Exception:
        _replace(saved.retired_items, previous)
        raise
    removed, kept = [], []
    for action, row, custom in candidates:
        try:
            deleted, detail = _discard_action(action, row, custom=custom,
                protected=action.as_pointer() in protected)
        except Exception as exc:
            deleted, detail = False, {'name': action.name_full,
                'reason': 'Cleanup could not be verified; the Action was kept: ' + str(exc)}
        (removed if deleted else kept).append(detail)
    return {'item_ids': list(item_ids), 'removed_actions': removed, 'kept_actions': kept}


def discard_retired(context, item_id):
    """Discard one retired row; shared, authored and recovery data stay intact."""
    worklist._idle(context)
    saved = worklist.state(context)
    rig = _main_rig(context, saved)
    rows = [row for row in saved.retired_items if row.item_id == item_id]
    if len(rows) != 1:
        raise ValueError('This retired animation is missing or ambiguous; no discard was applied.')
    owner = rows[0].rig
    if owner is None or (owner is not rig and owner.as_pointer() != rig.as_pointer()):
        raise ValueError('This retired animation belongs to another Main Rig; its data was kept.')
    result = _discard_rows(saved, (item_id,))
    saved.scan_completed = False
    message = 'Discarded the retired animation; removed ' + str(len(result['removed_actions'])) + ' unused Action(s).'
    if result['kept_actions']:
        message += ' Kept ' + str(len(result['kept_actions'])) + ' shared, edited or recovery Action(s).'
    saved.status, saved.has_error = message, False
    return result
