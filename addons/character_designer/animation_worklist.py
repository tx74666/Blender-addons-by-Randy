"""Small, persistent per-character Source/Custom animation worklists.

The Unity index is metadata-only. Each prepared row retains its existing v1
Link; one independently imported model serves all rows. Baselines are linked
read-only Actions, while custom Actions remain local and editable.
"""

import json
import math
from pathlib import Path
import uuid

import bpy

from . import animation_link as links, animation_link_source as source
from . import animation_workspace as workspace, unity_animation as ua


def _rigging_module(rig=None, action=None, *, packet=None, owned_state=None):
    def tagged(value, key):
        keys = getattr(value, 'keys', None)
        return callable(keys) and key in keys()

    capture_key = 'character_designer_rigging_capture_v1'
    input_key = 'character_designer_rigging_input_action_v1'
    if tagged(action, input_key) and not tagged(action, capture_key):
        raise ValueError('The Custom Rigging input draft lost its authenticated capture.')
    if (owned_state is not None or packet is not None and 'rigging' in packet
            or tagged(rig, 'character_designer_rigging_session_v1')
            or tagged(action, capture_key)):
        from . import unity_animation_rigging
        return unity_animation_rigging
    return None


def state(context):
    return context.scene.character_designer_animation_worklist


def current(context):
    saved = state(context)
    return saved.items[saved.active_index] if 0 <= saved.active_index < len(saved.items) else None


def _idle(context, *, _collection=False):
    from .animation import _link_idle
    idle = _link_idle(include_collection=False) if _collection else _link_idle()
    if not idle:
        raise ValueError('Finish the current animation or model export first.')
    if context.object and context.object.mode == 'EDIT':
        raise ValueError('Leave Edit Mode before changing the animation worklist.')


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _identity(record):
    # Optional Unity provenance is part of this editing snapshot when present.
    # A changed Avatar or Controller must not silently reuse the old baseline.
    extra = {key: record[key] for key in ('projectId', 'baseControllerGuid', 'avatarGuid',
                                        'avatarHash') if key in record}
    return _encoded([workspace.workspace_identity(record), extra])


def _clip_key(record):
    return workspace.row_key(record)


def _validate_route(saved, clip, link, item=None):
    target_guid = saved.target_guid
    if item is not None and _item_workspace(item):
        record = _fresh_workspace(saved, item)
        actual = next((row for row in record['clips'] if _clip_key(row) == item.clip_key), None)
        if (actual is None or actual.get('needsRebuild') or not actual.get('linkManifest')
                or Path(actual['linkManifest']).resolve() != Path(item.manifest_path).resolve()
                or workspace.route_identity(actual) != workspace.route_identity(clip)
                or Path(bpy.path.abspath(record['modelFile'])).resolve() != Path(item.model_file).resolve()
                or record['modelSha256'].lower() != item.model_sha256):
            raise ValueError('The attached work item lost its own prepared workspace, route or model.')
        target_guid = record['targetGuid'].lower()
    if (link['targetGuid'].lower() != target_guid or
            workspace.route_identity(clip) != workspace.route_identity(link)):
        raise ValueError('The prepared Link belongs to another work item, source, character or destination slot.')
    if item is not None and (item.clip_key != _clip_key(link) or
            getattr(item, 'work_item_id', '') != workspace.work_item_id(link) or
            links._identity(link) != links._identity(json.loads(item.link_identity))):
        raise ValueError('Unity rebuilt or changed this work-item Link. Existing Custom edits are kept; prepare a separate baseline.')


def _fill_catalog(saved, record):
    previous = saved.catalog[saved.catalog_index].clip_key if 0 <= saved.catalog_index < len(saved.catalog) else ''
    saved.catalog.clear()
    for clip in record['clips']:
        if clip.get('compositionReferenceSource') is True or clip.get('sourceOnly') is True:
            continue  # Read-only A companions are consumed by Composition/Create A Work Copy.
        entry = saved.catalog.add()
        entry.clip_key, entry.name = _clip_key(clip), clip['clipName']
        entry.work_item_id = workspace.work_item_id(clip)
        entry.source_name = clip.get('sourceName') or clip.get('sourceAsset') or ''
        entry.link_path = clip.get('linkManifest') or ''
        entry.ready = bool(not clip.get('needsRebuild') and entry.link_path and Path(entry.link_path).is_file())
    saved.catalog_index = next((i for i, entry in enumerate(saved.catalog) if entry.clip_key == previous),
                               0 if saved.catalog else -1)
    request = workspace.prepare_request(record)
    saved.prepare_request_id = request[0] if request is not None else ''
    saved.prepared_work_item_ids = _encoded(request[1]) if request is not None else ''


def connect(context, filepath):
    _idle(context)
    record = workspace.load_workspace(bpy.path.abspath(filepath))
    saved = state(context)
    if (saved.items or saved.rig) and saved.workspace_identity != _identity(record):
        raise ValueError('This worklist uses another workspace or model revision. Open the new workspace in a separate scene; existing edits are kept.')
    saved.workspace_path = record['_manifest_path']
    saved.workspace_identity = _identity(record)
    saved.workspace_id, saved.target_guid = record['workspaceId'], record['targetGuid'].lower()
    saved.target_name = record['targetName']
    saved.model_file, saved.model_sha256 = record['modelFile'], record['modelSha256'].lower()
    _fill_catalog(saved, record)
    saved.status, saved.has_error = 'Library connected. Prepare a clip in Unity, then add it to the worklist.', False
    return saved


def refresh(context):
    saved = state(context)
    if not saved.workspace_path:
        raise ValueError('Connect a Unity animation workspace first.')
    return connect(context, saved.workspace_path)


def _fresh_workspace(saved, item=None):
    path = getattr(item, "workspace_path", "") or saved.workspace_path
    identity = getattr(item, "workspace_identity", "") or saved.workspace_identity
    record = workspace.load_workspace(path)
    if _identity(record) != identity:
        raise ValueError('The Unity workspace model or character identity changed. Existing edits are kept; connect the new revision in another scene.')
    return record


def _prepared(saved, clip_key, *, existing_target=False):
    record = _fresh_workspace(saved)
    clip = next((entry for entry in record['clips'] if _clip_key(entry) == clip_key), None)
    if clip is None:
        raise ValueError('This Controller slot is no longer in the Unity workspace.')
    path = clip.get('linkManifest') or ''
    if not path or clip.get('needsRebuild'):
        raise ValueError('Prepare this clip in Unity and refresh the library first.')
    link = links.load_link(path)
    _validate_route(saved, clip, link)
    if existing_target:
        # Main Rig receives the verified packet directly. The FBX remains
        # historical source provenance, not an import input. A rebuilt model
        # on disk does not authorize changing that provenance or Sync guards.
        model = Path(bpy.path.abspath(saved.model_file)).expanduser().resolve()
        if (Path(bpy.path.abspath(link.get('modelFile') or saved.model_file)).expanduser().resolve() != model
                or link.get('modelSha256', '').lower() != saved.model_sha256):
            raise ValueError('The prepared Link has another historical model revision; existing edits are kept.')
    else:
        model, digest = source._source_model(link, saved.model_file)
        if digest != saved.model_sha256:
            raise ValueError('The character model changed; prepare a fresh Unity workspace.')
    packet, export_name = source._packet(link)
    return record, clip, link, packet, export_name, model


def _item_workspace(item):
    path, identity = getattr(item, 'workspace_path', ''), getattr(item, 'workspace_identity', '')
    if bool(path) != bool(identity):
        raise ValueError('The attached workspace path and identity must remain paired.')
    return bool(path)


def _original_rigging_mapping(context, packet, model, model_sha256):
    from contextlib import nullcontext
    from . import animation_worklist_target as target
    saved = state(context)
    rig = saved.rig
    setup = getattr(context.scene, 'character_designer_setup', None)
    if rig is None or getattr(setup, 'rig', None) is not rig or not target.is_adopted(rig):
        raise ValueError('Attach prepared Rigging only to this scene\'s already adopted Main Rig.')
    if (Path(bpy.path.abspath(saved.model_file)).resolve() != Path(model).resolve()
            or saved.model_sha256 != model_sha256):
        raise ValueError('The attached work must use the same physical Main Rig model; no rebind or repair is allowed.')
    rigging = _rigging_module(rig)
    with (rigging.base_only(context, rig) if rigging else nullcontext()):
        target.ensure_active(context, rig)
        original = target._load(rig)
        mapping = ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
        if mapping.controls is None or frozenset(mapping.indices) != frozenset(original['mapped_names']):
            raise ValueError('The attached packet must match the original Main Rig\'s complete validated bone domain.')


def _attached_context(saved, workspace_path, work_item_id):
    record = workspace.load_workspace(bpy.path.abspath(workspace_path))
    request = workspace.prepare_request(record)
    chosen = workspace.work_item_id({'workItemId': work_item_id}) if work_item_id is not None else None
    if chosen == '' or chosen is None and (request is None or not request[1]):
        raise ValueError('Select an actual prepared WorkID or one unique current Rigging preparation request.')
    candidates = []
    for clip in record['clips']:
        work_id = workspace.work_item_id(clip)
        if (not work_id or chosen is not None and work_id != chosen
                or chosen is None and work_id not in request[1]
                or clip.get('needsRebuild') or not clip.get('linkManifest')):
            continue
        link = links.load_link(clip['linkManifest'])
        if (link['targetGuid'].lower() != record['targetGuid'].lower()
                or workspace.route_identity(clip) != workspace.route_identity(link)):
            raise ValueError('The prepared work file and typed Link disagree about their actual target or route.')
        packet, export_name = source._packet(link)
        if 'rigging' not in packet:
            continue
        if (link.get('composition') is not None or clip.get('composition') is not None
                or link.get('sourceOnly') is True or link.get('compositionReferenceSource') is True):
            raise ValueError('Attach needs an editable ordinary typed Rigging work, not a composition/reference route.')
        model, digest = source._source_model(link, record['modelFile'])
        if digest != record['modelSha256'].lower():
            raise ValueError('The prepared workspace model differs from its physical typed Link model.')
        candidates.append(dict(record=record, clip=clip, link=link, packet=packet,
            export_name=export_name, model=model, model_sha256=digest,
            workspace_path=record['_manifest_path'], workspace_identity=_identity(record),
            workspace_id=record['workspaceId'],
            workspace_sha256=links._sha256(Path(record['_manifest_path'])),
            link_sha256=links._sha256(Path(link['_manifest_path']))))
    if len(candidates) != 1:
        raise ValueError('The current prepared selection must identify exactly one ready Rigging work; none was guessed.')
    return candidates[0]


def _check_attached_context(context, prepared, *, mapping=True):
    if (links._sha256(Path(prepared['workspace_path'])) != prepared['workspace_sha256']
            or links._sha256(Path(prepared['link']['_manifest_path'])) != prepared['link_sha256']
            or links._sha256(Path(prepared['packet']['_path'])) != prepared['packet']['_sha256']
            or links._sha256(prepared['model']) != prepared['model_sha256']):
        raise ValueError('An attached preparation changed; no row or baseline was accepted.')
    if (workspace.load_workspace(prepared['workspace_path']) != prepared['record']
            or links.load_link(prepared['link']['_manifest_path']) != prepared['link']
            or prepared['workspace_identity'] != _identity(prepared['record'])
            or prepared['workspace_id'] != prepared['record']['workspaceId']
            or prepared['clip'] not in prepared['record']['clips']):
        raise ValueError('The attached context no longer matches its actual prepared files.')
    if mapping:
        _original_rigging_mapping(context, prepared['packet'], prepared['model'], prepared['model_sha256'])


def attach_prepared_rigging_work(context, workspace_path, work_item_id=None):
    """Explicitly add one real Rigging work without reconnecting the old library."""
    _idle(context)
    prepared = _attached_context(state(context), workspace_path, work_item_id)
    _check_attached_context(context, prepared)
    return add(context, _clip_key(prepared['clip']), _prepared_context=prepared)


def _rigging_entry_packet(context, item):
    saved = state(context)
    _item_workspace(item)
    record = _fresh_workspace(saved, item)
    clip = next((row for row in record['clips'] if _clip_key(row) == item.clip_key), None)
    if clip is None or clip.get('needsRebuild') or not clip.get('linkManifest'):
        raise ValueError('The Rigging work is no longer actually prepared.')
    link = links.load_link(item.manifest_path)
    _validate_route(saved, clip, link, item)
    packet, _name = source._packet(link)
    model, digest = source._source_model(link, item.model_file)
    if digest != item.model_sha256:
        raise ValueError('The Rigging row\'s physical model changed.')
    if 'rigging' in packet:
        if (item.custom_action is None or item.custom_action.library is not None
                or 'character_designer_rigging_capture_v1' not in item.custom_action
                or item.custom_action.get(links.PACKAGE_HASH_KEY) != packet['_sha256']
                or Path(item.custom_action.get(links.PACKAGE_KEY, '')).resolve() != Path(packet['_path']).resolve()):
            raise ValueError('The prepared Rigging work lost its local Custom base.')
        rigging = _rigging_module(item.rig, item.custom_action, packet=packet)
        capture = rigging.capture_for_action(item.custom_action, item.custom_slot, item.work_item_id)
        from .animation_rigging_contract import validate_capture
        if capture != validate_capture(packet['rigging'], packet=packet):
            raise ValueError('The Custom Rigging capture changed; no ordinary activation fallback is allowed.')
        if _item_workspace(item) and item.workspace_identity != saved.workspace_identity:
            _original_rigging_mapping(context, packet, model, digest)
    return link, packet


def validate_rigging_entry(context, item, session):
    """Reauthenticate the selected Custom before native Edit/Save controls."""
    saved = state(context)
    rows = (current(context), _item(saved, item.item_id))
    pointer = item.as_pointer()
    if (not pointer or item.side != 'CUSTOM' or any(row is None or row.as_pointer() != pointer
            or row.item_id != item.item_id or row.rig != item.rig or row.work_item_id != item.work_item_id
            or row.side != 'CUSTOM' for row in rows)):
        raise ValueError('Select the exact Custom Rigging row before editing or saving controls.')
    _check_baseline(item)
    link, packet = _rigging_entry_packet(context, item)
    if ('rigging' not in packet or session is None or session['rig'] is not item.rig
            or session['scene'] is not context.scene or session['source_read_only']
            or session['base_action'] is not item.custom_action or session['base_slot'] != item.custom_slot
            or session['work_item_id'] != workspace.work_item_id(link)
            or session['source_packet_sha256'] != packet['_sha256']
            or session['input_action'] is not item.custom_action.get('character_designer_rigging_input_action_v1')):
        raise ValueError('The current Rigging session lost its exact row, packet or native Action owner.')
    rigging = _rigging_module(item.rig, item.custom_action, packet=packet)
    proof = rigging.input_proof(item.rig, item.custom_action, item.custom_slot, item.work_item_id)
    if (proof['input_action'] != session['input_action'].name or proof['input_slot'] != session['input_slot']
            or session['capture'] != rigging.capture_for_action(item.custom_action, item.custom_slot, item.work_item_id)):
        raise ValueError('The native input Action/slot or original capture changed.')


def _clean_action(action):
    from . import animation_worklist_target as target
    # Session pointers recursively pull rigs/scenes into a source-only library.
    # Worklist Actions have their own lifecycle and never own a legacy preview.
    for name in ('VERSION_KEY', 'TARGET_KEY', 'SCENE_KEY', 'PREVIOUS_ACTION_KEY',
                 'SESSION_KEY', 'CONTROL_KEYS_KEY', 'ORIGINAL_DATA_KEY',
                 'PREVIEW_DATA_KEY', 'ORIGINAL_REST_KEY'):
        key = getattr(ua, name)
        if key in action:
            del action[key]
    for key in (target.ACTION_MOTION_KEY, target.ACTION_RIG_KEY):
        if key in action:
            del action[key]
    action.use_fake_user = True


def _slot(action, handle=None):
    slots = [slot for slot in action.slots if slot.target_id_type == 'OBJECT']
    if handle is not None:
        slots = [slot for slot in slots if slot.handle == handle]
    if len(slots) != 1:
        raise ValueError('The worklist Action needs its saved, unambiguous Object slot.')
    return slots[0]


def _linked_baseline(saved, action, item_id, clip_name):
    action.name = 'Source · ' + clip_name
    cache = Path(bpy.utils.user_resource('DATAFILES', path='character_designer/animation_sources', create=True))
    folder = cache / uuid.UUID(saved.workspace_id).hex / saved.model_sha256
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (item_id + '.blend')
    if path.exists():
        raise ValueError('The new Source cache already exists; no existing baseline was overwritten.')
    try:
        bpy.data.libraries.write(str(path), {action}, fake_user=True, compress=True)
        digest = links._sha256(path)
        with bpy.data.libraries.load(str(path), link=True) as (_available, selected):
            selected.actions = [action.name]
        linked = selected.actions[0]
        if linked is None or linked.library is None:
            raise ValueError('Blender did not create a read-only Source Action.')
    except Exception:
        # The UUID path did not exist before this operation. Never remove a
        # baseline belonging to an existing worklist row.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return linked, str(path), digest


def _find_owned_rig(context, saved):
    candidates = []
    for rig in context.scene.objects:
        if rig.type != 'ARMATURE' or rig.get('character_designer_animation_source_model_sha256') != saved.model_sha256:
            continue
        association = links.get_link(rig)
        if association and association['identity']['targetGuid'].lower() == saved.target_guid:
            candidates.append(rig)
    if len(candidates) > 1:
        raise ValueError('Several linked editing rigs match this workspace. Choose a separate scene instead of guessing.')
    return candidates[0] if candidates else None


def _editing_data(context, rig, *, packet=None):
    if ua.active_preview(rig):
        ua.restore_preview(context, rig)
    translated = None
    if packet is not None and 'rigging' in packet:
        mapping = ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
        if mapping.controls is None:
            raise ValueError('Rigging motion editing needs its validated native control plan.')
        translated = mapping.controls.mapped_names - mapping.controls.delegated_names
    data = rig.data.copy()
    data.name = rig.data.name + ' · Motion Worklist'
    if translated is None:
        ua._swap_armature_data(context, rig, data, allow_joint_translation=True)
    else:
        ua._swap_armature_data(context, rig, data, allow_joint_translation=True,
                              joint_translation_names=translated)
    context.view_layer.update()


def _capture_rig(context, rig):
    from . import animation_worklist_target as target
    action = rig.animation_data.action if rig.animation_data else None
    data = rig.data
    captured = dict(data=data, fake_user=data.use_fake_user, action=action,
                    snapshot=ua._snapshot(context, rig),
                    action_props=dict(action.items()) if action else {},
                    props={key: rig[key] for key in (links.LINK_KEY, links.ACTION_KEY,
                            links.RIG_ID_KEY, ua.ACTIVE_KEY) if key in rig},
                    target_session=target.capture_session_metadata(rig))
    rigging = _rigging_module(rig, action)
    captured['rigging'] = rigging._capture_owned(context, rig) if rigging else None
    # Pin temporary legacy-preview data until adoption commits or rolls back.
    data.use_fake_user = True
    return captured


def _restore_rig(context, rig, captured):
    from . import animation_worklist_target as target
    rigging = _rigging_module(rig, captured['action'], owned_state=captured.get('rigging'))
    if rigging:
        rigging.restore_owned(context, rig)
    if rig.data is not captured['data']:
        ua._swap_armature_data(context, rig, captured['data'])
    ua._restore_snapshot(context, rig, captured['snapshot'], captured['action'])
    action = captured['action']
    if action:
        for key in tuple(action.keys()):
            if key not in captured['action_props']:
                del action[key]
        for key, value in captured['action_props'].items():
            action[key] = value
    for key in (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY, ua.ACTIVE_KEY):
        if key in captured['props']:
            rig[key] = captured['props'][key]
        elif key in rig:
            del rig[key]
    captured['data'].use_fake_user = captured['fake_user']
    target.restore_session_metadata(rig, captured.get('target_session', {}))
    if rigging:
        rigging._restore_owned_state(context, rig, captured.get('rigging'))


def _finish_rig(context, rig, captured):
    token = captured.get('rigging') if captured is not None else None
    if token is not None:
        rigging = _rigging_module(owned_state=token)
        rigging._finish_owned_state(context, rig, token)


def adopt_main_rig(context):
    from .animation_worklist_target import adopt_main_rig as adopt
    rig = getattr(getattr(context.scene, 'character_designer_setup', None), 'rig', None)
    rigging = _rigging_module(rig)
    if rigging is None:
        return adopt(context)
    captured = rigging._capture_owned(context, rig)
    try:
        rigging.restore_owned(context, rig)
        result = adopt(context)
        rigging._finish_owned_state(context, rig, captured)
        return result
    except Exception:
        rigging._restore_owned_state(context, rig, captured)
        raise


def restore_main_rig(context):
    from . import animation_composition as composition
    if composition.active(state(context).rig):
        raise ValueError('Close Composition before restoring Main Rig; its native draft is retained.')
    from .animation_worklist_target import restore_main_rig as restore
    rig = state(context).rig
    rigging = _rigging_module(rig)
    if rigging is None:
        return restore(context)
    captured = rigging._capture_owned(context, rig)
    try:
        rigging.restore_owned(context, rig)
        result = restore(context)
        rigging._finish_owned_state(context, rig, captured)
        return result
    except Exception:
        rigging._restore_owned_state(context, rig, captured)
        raise


def _copy_workspace(source_state, destination):
    for field in ('workspace_path', 'workspace_identity', 'workspace_id', 'target_guid',
                  'target_name', 'model_file', 'model_sha256', 'search', 'show_catalog'):
        setattr(destination, field, getattr(source_state, field))
    _fill_catalog(destination, workspace.load_workspace(destination.workspace_path))


def _composition_add_route(context, saved, clip_key, existing, *, activate_new, _collection):
    """Dispatch explicit prepared B before a legacy import can mutate the rig."""
    from . import animation_composition as composition
    if existing is not None:
        raw_identity = getattr(existing, 'link_identity', '')
        outer = json.loads(raw_identity) if raw_identity else {}
        if outer.get('compositionReferenceSource') is True or outer.get('sourceOnly') is True:
            raise ValueError('This read-only A reference cannot be added or activated as an ordinary edit. Use Create A Work Copy in Composition.')
        is_b = (outer.get('composition') is not None
                or bool(getattr(existing, 'composition_json', ''))
                or bool(getattr(existing, 'application_role', ''))
                or getattr(existing, 'edit_scope', '') == 'UpperBodyOverride')
        if not is_b:
            return False, None  # Preserve ordinary existing-row activation semantics.
    else:
        record = _fresh_workspace(saved)
        clip = next((row for row in record['clips'] if _clip_key(row) == clip_key), None)
        if clip is None:
            raise ValueError('The selected work item is no longer in this workspace.')
        if clip.get('compositionReferenceSource') is True or clip.get('sourceOnly') is True:
            raise ValueError('This read-only A reference cannot be added as an ordinary edit. Use Create A Work Copy in Composition.')
        if clip.get('needsRebuild') or not clip.get('linkManifest'):
            raise ValueError('Prepare the selected work item in Unity before adding it.')
        link = links.load_link(clip['linkManifest'])
        _validate_route(saved, clip, link)
        if link.get('compositionReferenceSource') is True or link.get('sourceOnly') is True:
            raise ValueError('This read-only A reference cannot be added as an ordinary edit. Use Create A Work Copy in Composition.')
        is_b = clip.get('composition') is not None or clip.get('editScope') == 'UpperBodyOverride'
        if not is_b:
            return False, None
        if not saved.composition_workspace_path:
            raise ValueError('Connect Composition Library to this workspace, then Add Prepared; this B row was not imported as an ordinary Action.')
        library_record, library = composition._library_state(context)
        if (Path(library_record['_manifest_path']).resolve() != Path(record['_manifest_path']).resolve()
                or library.workspace_identity != _identity(record)):
            raise ValueError('Connect Composition Library to this workspace, then Add Prepared; the existing library was retained.')
    return True, composition.add_work_item(context, clip_key,
        activate_new=activate_new, _collection=_collection)


def add(context, clip_key, *, activate_new=True, _collection=False, _prepared_context=None):
    from . import animation_composition as composition
    if composition.active(state(context).rig):
        raise ValueError('Close Composition before switching or removing worklist Actions; both drafts are retained.')
    from . import animation_worklist_target as target
    _idle(context, _collection=_collection)
    saved = state(context)
    existing = next((item for item in saved.items if item.clip_key == clip_key), None)
    if _prepared_context is not None:
        _check_attached_context(context, _prepared_context)
        if clip_key != _clip_key(_prepared_context['clip']):
            raise ValueError('The attached work key differs from its actual prepared context.')
    else:
        handled, item = _composition_add_route(context, saved, clip_key, existing,
            activate_new=activate_new, _collection=_collection)
        if handled:
            return item
    if existing:
        if _prepared_context is not None:
            if (not _item_workspace(existing)
                    or existing.workspace_path != _prepared_context['workspace_path']
                    or existing.workspace_identity != _prepared_context['workspace_identity']):
                raise ValueError('This WorkID already belongs to another preserved row; it was not reassigned.')
            _rigging_entry_packet(context, existing)
        if activate_new:
            activate(context, existing.item_id, 'CUSTOM', _collection=_collection)
        return existing
    if _prepared_context is None:
        _record, clip, link, packet, export_name, model = _prepared(
            saved, clip_key, existing_target=target.is_adopted(saved.rig))
        cache_owner = saved
    else:
        from types import SimpleNamespace
        _record, clip, link, packet, export_name, model = (
            _prepared_context[key] for key in ('record', 'clip', 'link', 'packet', 'export_name', 'model'))
        cache_owner = SimpleNamespace(workspace_id=_prepared_context['workspace_id'],
            model_sha256=_prepared_context['model_sha256'])
    rigging = _rigging_module(saved.rig, packet=packet)
    old_context = source._context_state(context)
    previous_target = context.window_manager.character_designer_animation.target
    created_before = set(bpy.data.user_map())
    first_scene = False
    baseline = custom = linked = None
    item_id = ''
    cache_file = None
    rig = saved.rig
    captured = _capture_rig(context, rig) if rig else None
    previous_rig = saved.rig
    previous_index = saved.active_index
    existing_edit = None
    existing_edit_props = None
    existing_edit_fake_user = None
    try:
        if rig is not None:
            if rigging:
                rigging.restore_owned(context, rig)
            if target.managed(rig):
                target.ensure_active(context, rig)
        if rig is None:
            rig = _find_owned_rig(context, saved)
            if rig is None:
                prepared_import = getattr(source, '_import_prepared_source', None)
                if prepared_import is None:
                    if 'rigging' in packet:
                        raise ValueError('Refresh Character Designer before importing Rigging inputs on their native clock.')
                    # A file-only update can precede a saved-session restart.
                    imported = source.import_source(context, link['_manifest_path'], str(model), start_frame=1)
                else:
                    imported = (prepared_import(context, link, packet, export_name,
                        model, saved.model_sha256, start_frame=1, native_timebase=True)
                        if 'rigging' in packet else prepared_import(context, link, packet, export_name,
                            model, saved.model_sha256, start_frame=1))
                rig, baseline = imported.rig, imported.action
                first_scene = True
                destination = state(context)
                _copy_workspace(saved, destination)
                saved = destination
                context.scene.name = 'Animation Worklist · ' + saved.target_name
            else:
                captured = _capture_rig(context, rig)
                rigging = _rigging_module(rig, packet=packet)
                if rigging:
                    rigging.restore_owned(context, rig)
                associated = links.get_link(rig)
                if _clip_key(associated['identity']) == clip_key:
                    if links._identity(associated['identity']) != links._identity(link):
                        raise ValueError('Unity rebuilt this legacy clip Link. Keep its existing edits and open the new baseline in a separate scene.')
                    existing_edit = rig.get(links.ACTION_KEY)
                    if (existing_edit is None or existing_edit.library is not None
                            or existing_edit.get(links.PACKAGE_HASH_KEY) != link['sourcePackageSha256'].lower()
                            or Path(existing_edit.get(links.PACKAGE_KEY, '')).resolve() != Path(link['sourcePackage']).resolve()):
                        raise ValueError('The existing legacy Custom Action has another source revision; it was not adopted.')
                    existing_edit_props = dict(existing_edit.items())
                    existing_edit_fake_user = existing_edit.use_fake_user
            if target.managed(rig):
                target.ensure_active(context, rig)
            else:
                if 'rigging' in packet:
                    _editing_data(context, rig, packet=packet)
                else:
                    _editing_data(context, rig)
        if rig.name not in context.scene.objects or rig.type != 'ARMATURE' or rig.library:
            raise ValueError('Return to this worklist\'s independent editing scene.')
        if baseline is None:
            if ua.active_preview(rig):
                raise ValueError('Restore the unrelated Unity test preview before adding a worklist clip.')
            imported = (source._import_action(context, rig, packet, 1, native_timebase=True)
                        if 'rigging' in packet else source._import_action(context, rig, packet, 1))
            baseline = imported.action
            target.restore_import_preview(context, rig, baseline)
        _clean_action(baseline)
        custom = existing_edit or baseline.copy()
        _clean_action(custom)
        if existing_edit is None:
            custom.name = saved.target_name + ' · ' + clip['clipName'] + ' · Custom'
        item_id = uuid.uuid4().hex
        linked, cache_file, cache_hash = _linked_baseline(cache_owner, baseline, item_id, clip['clipName'])
        # Re-check inputs before publishing a row; preparation may have changed
        # while the synchronous native bake was running.
        latest = links.load_link(link['_manifest_path'])
        if (links._identity(latest) != links._identity(link)
                or (not target.is_adopted(rig) and links._sha256(model) != saved.model_sha256)):
            raise ValueError('A linked input changed during import; the worklist entry was not added.')
        target.attach_action_motion_data(rig, custom)
        if 'rigging' in packet:
            rigging.import_inputs(context, rig, packet, base_action=custom, base_slot=_slot(custom).handle,
                work_item_id=workspace.work_item_id(clip))
        if _prepared_context is not None:
            _check_attached_context(context, _prepared_context, mapping=False)
        item = saved.items.add()
        item.item_id, item.clip_key, item.name = item_id, clip_key, clip['clipName']
        item.work_item_id = workspace.work_item_id(clip)
        item.rig, item.source_action, item.custom_action = rig, linked, custom
        item.source_slot, item.custom_slot = _slot(linked).handle, _slot(custom).handle
        item.manifest_path, item.link_identity = link['_manifest_path'], _encoded(link)
        item.export_rig_name, item.model_file, item.model_sha256 = export_name, str(model), cache_owner.model_sha256
        if _prepared_context is not None:
            item.workspace_path = _prepared_context['workspace_path']
            item.workspace_identity = _prepared_context['workspace_identity']
            item.new_animation = False
        item.source_file, item.source_hash = cache_file, cache_hash
        saved.rig = rig
        rig['character_designer_animation_source_model_sha256'] = saved.model_sha256
        from .animation_worklist_collection import remember_source_baseline
        remember_source_baseline(context, item)
        if activate_new:
            activate(context, item.item_id, 'CUSTOM', _collection=_collection)
        elif captured:
            # Collection import never activates a new Custom over an existing
            # author Action. The native temporary bake has already finished.
            _restore_rig(context, rig, captured)
            source._restore_context(context, old_context, playing=True)
        else:
            saved.active_index = -1
        bpy.data.actions.remove(baseline)
        saved.status, saved.has_error = 'Added ' + item.name + '. Select Source to compare or Custom to edit.', False
        if captured:
            captured['data'].use_fake_user = captured['fake_user']
        rig.show_in_front = True
        _finish_rig(context, rig, captured)
        return item
    except Exception:
        if rig is not None and rigging:
            rigging.restore_owned(context, rig)
        # Activation owns a temporary WindowManager target as well as Scene
        # data. Restore it before removing a failed imported rig's IDs.
        context.window_manager.character_designer_animation.target = previous_target
        if first_scene:
            source._restore_context(context, old_context, playing=True)
            created = {value for value in set(bpy.data.user_map()) - created_before if not value.is_embedded_data}
            if created:
                bpy.data.batch_remove(ids=tuple(created))
        else:
            if captured:
                # Restore our captured owner directly. A failed import may
                # have switched away from its temporary preview Action, in
                # which case the preview's public restore guard would refuse.
                _restore_rig(context, rig, captured)
            elif rig and ua.active_preview(rig):
                ua.restore_preview(context, rig)
            if existing_edit is not None and existing_edit_props is not None:
                for key in tuple(existing_edit.keys()):
                    if key not in existing_edit_props:
                        del existing_edit[key]
                for key, value in existing_edit_props.items():
                    existing_edit[key] = value
                existing_edit.use_fake_user = existing_edit_fake_user
            added = next((i for i, item in enumerate(saved.items) if item.item_id == item_id), None)
            if added is not None:
                saved.items.remove(added)
            saved.rig, saved.active_index = previous_rig, previous_index
            created = {value for value in set(bpy.data.user_map()) - created_before if not value.is_embedded_data}
            if created:
                bpy.data.batch_remove(ids=tuple(created))
            source._restore_context(context, old_context, playing=True)
        if cache_file:
            try:
                Path(cache_file).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _item(saved, item_id):
    item = next((entry for entry in saved.items if entry.item_id == item_id), None)
    if item is None:
        raise ValueError('This worklist entry no longer exists.')
    if item.rig is None or item.rig is not saved.rig:
        raise ValueError('This worklist entry lost its editing rig.')
    return item


def _check_baseline(item):
    if getattr(item, 'new_animation', False):
        from .animation_worklist_new import identity
        identity(json.loads(item.link_identity))
        if item.source_action is not None or item.source_file or item.source_hash:
            raise ValueError('A new Action cannot claim an original Source baseline.')
        return
    if item.source_action is None or item.source_action.library is None:
        raise ValueError('The Source Action is missing or was made editable; its baseline is not trusted.')
    path = Path(item.source_file)
    if Path(bpy.path.abspath(item.source_action.library.filepath)).resolve() != path.resolve():
        raise ValueError('The Source Action no longer refers to its recorded baseline library.')
    if not path.is_file() or links._sha256(path) != item.source_hash:
        raise ValueError('The read-only Source cache changed or is missing. Existing Custom edits are kept.')


def _association_record(item):
    rig = item.rig
    identity = json.loads(item.link_identity)
    rig_id = rig.get(links.RIG_ID_KEY) or uuid.uuid4().hex
    record = {'version': 1, 'rig_id': rig_id, 'manifest_path': item.manifest_path,
              'identity': {key: identity[key] for key in (*links.IDENTITY_FIELDS, *links.OPTIONAL_IDENTITY_FIELDS) if key in identity},
              'action_slot': item.custom_slot, 'action_name': item.custom_action.name,
              'export_rig_name': item.export_rig_name, 'model_file': item.model_file,
              'model_sha256': item.model_sha256}
    links._validate_identity(identity)
    return rig_id, _encoded(record)


def _association(item, prepared=None):
    rig_id, encoded = prepared or _association_record(item)
    rig = item.rig
    rig[links.RIG_ID_KEY], rig[links.ACTION_KEY], rig[links.LINK_KEY] = rig_id, item.custom_action, encoded


def activate(context, item_id, side='CUSTOM', *, _collection=False):
    from . import animation_composition as composition
    if composition.active(state(context).rig):
        raise ValueError('Close Composition before switching or removing worklist Actions; both drafts are retained.')
    from . import animation_worklist_target as target
    _idle(context, _collection=_collection)
    if side not in {'SOURCE', 'CUSTOM'}:
        raise ValueError('Choose Source or Custom.')
    saved = state(context)
    item = _item(saved, item_id)
    if side == 'SOURCE' and getattr(item, 'new_animation', False):
        raise ValueError('This new Action has no Original or Source version.')
    if _item_workspace(item) and not getattr(item, 'new_animation', False):
        # File/route/capture admission precedes closing a preview or assigning
        # any Action. Missing captured Rigging never becomes ordinary editing.
        _check_baseline(item)
        _rigging_entry_packet(context, item)
    rig = item.rig
    rigging = _rigging_module(rig, item.custom_action)
    captured = _capture_rig(context, rig) if rigging else None
    previous_index, previous_side = saved.active_index, item.side
    previous_target = context.window_manager.character_designer_animation.target if rigging else None
    previous_context = source._context_state(context) if rigging and context.window is not None else None
    previous_active = context.view_layer.objects.active if rigging else None
    previous_selected = tuple(context.selected_objects) if rigging else ()
    previous_mode = previous_active.mode if previous_active else 'OBJECT'
    try:
        if rigging:
            rigging.restore_owned(context, rig)
        target.ensure_active(context, rig)
        if rig.name not in context.scene.objects:
            raise ValueError('Return to the worklist editing scene first.')
        _check_baseline(item)
        action = item.source_action if side == 'SOURCE' else item.custom_action
        if action is None or (side == 'CUSTOM' and action.library is not None):
            raise ValueError('This entry lost its local editable Custom Action.')
        slot = _slot(action, item.source_slot if side == 'SOURCE' else item.custom_slot)
        if ua.active_preview(rig):
            raise ValueError('Restore the unrelated Unity test preview before switching worklist Actions.')
        association = _association_record(item) if side == 'CUSTOM' and not getattr(item, 'new_animation', False) else None
        ua._set_playing(context, False)
        ad = rig.animation_data_create()
        ad.action, ad.action_slot = action, slot
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.0
        if association is not None:
            _association(item, association)
        saved.active_index = next(i for i, entry in enumerate(saved.items) if entry.item_id == item_id)
        item.side = side
        context.scene.tool_settings.use_keyframe_insert_auto = False
        start, end = map(float, action.frame_range)
        scene = context.scene
        scene.frame_start = scene.frame_preview_start = math.floor(start)
        scene.frame_end = scene.frame_preview_end = max(math.ceil(end), scene.frame_start)
        scene.use_preview_range = True
        ua._set_frame(scene, start)
        for selected in context.selected_objects:
            selected.select_set(False)
        rig.hide_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        context.window_manager.character_designer_animation.target = rig
        if rigging and 'character_designer_rigging_capture_v1' in item.custom_action:
            link = links.load_link(item.manifest_path)
            _validate_route(saved, json.loads(item.link_identity), link, item)
            packet, _export_name = source._packet(link)
            if 'rigging' not in packet:
                raise ValueError('The worklist Rigging inputs lost their authenticated source packet.')
            rigging.import_inputs(context, rig, packet, base_action=action, base_slot=slot.handle,
                work_item_id=item.work_item_id, source=side == 'SOURCE')
        context.view_layer.update()
        saved.status, saved.has_error = item.name + ' · ' + ('Source (read-only)' if side == 'SOURCE' else 'Custom'), False
        _finish_rig(context, rig, captured)
        return action
    except Exception:
        if captured is not None:
            _restore_rig(context, rig, captured)
            saved.active_index, item.side = previous_index, previous_side
            context.window_manager.character_designer_animation.target = previous_target
            if previous_context is not None:
                source._restore_context(context, previous_context, playing=True)
            else:
                for selected in context.selected_objects:
                    selected.select_set(False)
                for selected in previous_selected:
                    selected.select_set(True)
                context.view_layer.objects.active = previous_active
                if previous_active and previous_active.mode != previous_mode:
                    bpy.ops.object.mode_set(mode=previous_mode)
        raise
    finally:
        if captured is not None:
            captured['data'].use_fake_user = captured['fake_user']


def move(context, item_id, delta):
    _idle(context)
    saved = state(context)
    _item(saved, item_id)
    index = next(i for i, entry in enumerate(saved.items) if entry.item_id == item_id)
    if type(delta) is not int:
        raise ValueError('The worklist move must use an integer row offset.')
    destination = max(0, min(len(saved.items) - 1, index + delta))
    selected = current(context)
    selected_id = selected.item_id if selected else ''
    saved.items.move(index, destination)
    saved.active_index = next((i for i, entry in enumerate(saved.items) if entry.item_id == selected_id), -1)
    return destination


def remove(context, item_id):
    from . import animation_composition as composition
    if composition.active(state(context).rig):
        raise ValueError('Close Composition before switching or removing worklist Actions; both drafts are retained.')
    _idle(context)
    saved = state(context)
    item = _item(saved, item_id)
    index = next(i for i, entry in enumerate(saved.items) if entry.item_id == item_id)
    rigging = _rigging_module(item.rig)
    if rigging:
        rigging.restore_owned(context, item.rig)
    ad = item.rig.animation_data
    if ad and ad.action in {item.source_action, item.custom_action}:
        ua._set_playing(context, False)
        ad.action = None
    if item.rig.get(links.ACTION_KEY) is item.custom_action:
        for key in (links.LINK_KEY, links.ACTION_KEY):
            if key in item.rig:
                del item.rig[key]
    selected = current(context)
    selected_id = selected.item_id if selected and selected.item_id != item_id else ''
    saved.items.remove(index)
    saved.active_index = next((i for i, entry in enumerate(saved.items) if entry.item_id == selected_id), -1)
    saved.status, saved.has_error = 'Removed from the worklist. Source and Custom Actions are retained.', False


def sync(context, *, _collection=False, _collection_proof=None):
    from . import animation_worklist_target as target
    _idle(context, _collection=_collection)
    saved = state(context)
    item = current(context)
    if item is None or item.side != 'CUSTOM':
        raise ValueError('Select a Custom worklist Action before syncing. Source is read-only.')
    from . import animation_composition as composition
    if composition.item_spec(item) is not None:
        return composition.begin_sync(context, item, collection=_collection, collection_proof=_collection_proof)
    if _item_workspace(item) and not getattr(item, 'new_animation', False):
        _rigging_entry_packet(context, item)
    _check_baseline(item)
    from contextlib import nullcontext
    rigging = _rigging_module(item.rig, item.custom_action)
    with (rigging.base_only(context, item.rig, action=item.custom_action) if rigging else nullcontext()):
        target.ensure_active(context, item.rig)
        if not item.rig.animation_data or item.rig.animation_data.action is not item.custom_action:
            raise ValueError('The active Action changed; select this entry\'s Custom Action again.')
        if getattr(item, 'new_animation', False):
            from .animation_worklist_new import sync as sync_new
            return sync_new(context, item, collection=_collection, collection_proof=_collection_proof)
        catalog = _fresh_workspace(saved, item)
        clip = next((entry for entry in catalog['clips'] if _clip_key(entry) == item.clip_key), None)
        if (clip is None or clip.get('needsRebuild') or not clip.get('linkManifest')
                or Path(clip['linkManifest']).resolve() != Path(item.manifest_path).resolve()):
            raise ValueError('This Controller slot needs a freshly prepared Unity Link; existing edits are kept.')
        current_link = links.load_link(item.manifest_path)
        _validate_route(saved, clip, current_link, item)
        from .animation_worklist_collection import export_implementation, item_fingerprint, unchanged_proof
        # Collect before launching: a proof error must never strand a worker without
        # its poll/disposal owner. The snapshot freezes in the same event-loop turn.
        proof = item_fingerprint(context, item)
        implementation = export_implementation()
        if _collection_proof is not None:
            if not _collection or not _collection_proof(proof):
                raise ValueError('The collection launch was cancelled or lost its fingerprint owner.')
        if unchanged_proof(item, current_link, implementation, proof):
            raise ValueError('This Custom Action is unchanged; no duplicate animation was exported.')
        _association(item)
        job = links.begin_linked_export(context, item.rig)
        job['_worklist_scene'], job['_worklist_item_id'] = context.scene, item.item_id
        job['_worklist_fingerprint'] = proof
        job['_worklist_export_implementation'] = implementation
        from . import animation
        animation._export_window_manager = context.window_manager
        bpy.app.timers.register(animation._poll_action_export, first_interval=0.25)
        saved.status, saved.has_error = 'Syncing ' + item.name + '. Preview and Apply the candidate in Unity.', False
        return job
