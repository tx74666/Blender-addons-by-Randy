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
    guid, local_id = workspace.clip_identity(record)
    return guid + ':' + str(local_id)


def _fill_catalog(saved, record):
    previous = saved.catalog[saved.catalog_index].clip_key if 0 <= saved.catalog_index < len(saved.catalog) else ''
    saved.catalog.clear()
    for clip in record['clips']:
        entry = saved.catalog.add()
        entry.clip_key, entry.name = _clip_key(clip), clip['clipName']
        entry.source_name = clip.get('sourceName') or clip.get('sourceAsset') or ''
        entry.link_path = clip.get('linkManifest') or ''
        entry.ready = bool(not clip.get('needsRebuild') and entry.link_path and Path(entry.link_path).is_file())
    saved.catalog_index = next((i for i, entry in enumerate(saved.catalog) if entry.clip_key == previous),
                               0 if saved.catalog else -1)


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


def _fresh_workspace(saved):
    record = workspace.load_workspace(saved.workspace_path)
    if _identity(record) != saved.workspace_identity:
        raise ValueError('The Unity workspace model or character identity changed. Existing edits are kept; connect the new revision in another scene.')
    return record


def _prepared(saved, clip_key):
    record = _fresh_workspace(saved)
    clip = next((entry for entry in record['clips'] if _clip_key(entry) == clip_key), None)
    if clip is None:
        raise ValueError('This Controller slot is no longer in the Unity workspace.')
    path = clip.get('linkManifest') or ''
    if not path or clip.get('needsRebuild'):
        raise ValueError('Prepare this clip in Unity and refresh the library first.')
    link = links.load_link(path)
    if (link['targetGuid'].lower() != saved.target_guid or
            _clip_key(link) != clip_key):
        raise ValueError('The prepared Link belongs to another character or Controller slot.')
    model, digest = source._source_model(link, saved.model_file)
    if digest != saved.model_sha256:
        raise ValueError('The character model changed; prepare a fresh Unity workspace.')
    packet, export_name = source._packet(link)
    return record, clip, link, packet, export_name, model


def _clean_action(action):
    # Session pointers recursively pull rigs/scenes into a source-only library.
    # Worklist Actions have their own lifecycle and never own a legacy preview.
    for name in ('VERSION_KEY', 'TARGET_KEY', 'SCENE_KEY', 'PREVIOUS_ACTION_KEY',
                 'SESSION_KEY', 'CONTROL_KEYS_KEY', 'ORIGINAL_DATA_KEY',
                 'PREVIEW_DATA_KEY', 'ORIGINAL_REST_KEY'):
        key = getattr(ua, name)
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


def _editing_data(context, rig):
    if ua.active_preview(rig):
        ua.restore_preview(context, rig)
    data = rig.data.copy()
    data.name = rig.data.name + ' · Motion Worklist'
    ua._swap_armature_data(context, rig, data, allow_joint_translation=True)
    context.view_layer.update()


def _capture_rig(context, rig):
    action = rig.animation_data.action if rig.animation_data else None
    data = rig.data
    captured = dict(data=data, fake_user=data.use_fake_user, action=action,
                    snapshot=ua._snapshot(context, rig),
                    action_props=dict(action.items()) if action else {},
                    props={key: rig[key] for key in (links.LINK_KEY, links.ACTION_KEY,
                            links.RIG_ID_KEY, ua.ACTIVE_KEY) if key in rig})
    # Pin temporary legacy-preview data until adoption commits or rolls back.
    data.use_fake_user = True
    return captured


def _restore_rig(context, rig, captured):
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


def _copy_workspace(source_state, destination):
    for field in ('workspace_path', 'workspace_identity', 'workspace_id', 'target_guid',
                  'target_name', 'model_file', 'model_sha256', 'search', 'show_catalog'):
        setattr(destination, field, getattr(source_state, field))
    _fill_catalog(destination, workspace.load_workspace(destination.workspace_path))


def add(context, clip_key, *, activate_new=True, _collection=False):
    _idle(context, _collection=_collection)
    saved = state(context)
    existing = next((item for item in saved.items if item.clip_key == clip_key), None)
    if existing:
        if activate_new:
            activate(context, existing.item_id, 'CUSTOM', _collection=_collection)
        return existing
    _record, clip, link, packet, export_name, model = _prepared(saved, clip_key)
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
        if rig is None:
            rig = _find_owned_rig(context, saved)
            if rig is None:
                prepared_import = getattr(source, '_import_prepared_source', None)
                if prepared_import is None:
                    # A file-only update can precede a saved-session restart.
                    imported = source.import_source(context, link['_manifest_path'], str(model), start_frame=1)
                else:
                    imported = prepared_import(context, link, packet, export_name,
                                               model, saved.model_sha256, start_frame=1)
                rig, baseline = imported.rig, imported.action
                first_scene = True
                destination = state(context)
                _copy_workspace(saved, destination)
                saved = destination
                context.scene.name = 'Animation Worklist · ' + saved.target_name
            else:
                captured = _capture_rig(context, rig)
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
            _editing_data(context, rig)
        if rig.name not in context.scene.objects or rig.type != 'ARMATURE' or rig.library:
            raise ValueError('Return to this worklist\'s independent editing scene.')
        if baseline is None:
            if ua.active_preview(rig):
                raise ValueError('Restore the unrelated Unity test preview before adding a worklist clip.')
            imported = source._import_action(context, rig, packet, 1)
            baseline = imported.action
            ua.restore_preview(context, rig)
        _clean_action(baseline)
        custom = existing_edit or baseline.copy()
        _clean_action(custom)
        if existing_edit is None:
            custom.name = saved.target_name + ' · ' + clip['clipName'] + ' · Custom'
        item_id = uuid.uuid4().hex
        linked, cache_file, cache_hash = _linked_baseline(saved, baseline, item_id, clip['clipName'])
        # Re-check inputs before publishing a row; preparation may have changed
        # while the synchronous native bake was running.
        latest = links.load_link(link['_manifest_path'])
        if links._identity(latest) != links._identity(link) or links._sha256(model) != saved.model_sha256:
            raise ValueError('A linked input changed during import; the worklist entry was not added.')
        item = saved.items.add()
        item.item_id, item.clip_key, item.name = item_id, clip_key, clip['clipName']
        item.rig, item.source_action, item.custom_action = rig, linked, custom
        item.source_slot, item.custom_slot = _slot(linked).handle, _slot(custom).handle
        item.manifest_path, item.link_identity = link['_manifest_path'], _encoded(link)
        item.export_rig_name, item.model_file, item.model_sha256 = export_name, str(model), saved.model_sha256
        item.source_file, item.source_hash = cache_file, cache_hash
        saved.rig = rig
        rig['character_designer_animation_source_model_sha256'] = saved.model_sha256
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
        return item
    except Exception:
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
    _idle(context, _collection=_collection)
    if side not in {'SOURCE', 'CUSTOM'}:
        raise ValueError('Choose Source or Custom.')
    saved = state(context)
    item = _item(saved, item_id)
    rig = item.rig
    if rig.name not in context.scene.objects:
        raise ValueError('Return to the worklist editing scene first.')
    _check_baseline(item)
    action = item.source_action if side == 'SOURCE' else item.custom_action
    if action is None or (side == 'CUSTOM' and action.library is not None):
        raise ValueError('This entry lost its local editable Custom Action.')
    slot = _slot(action, item.source_slot if side == 'SOURCE' else item.custom_slot)
    if ua.active_preview(rig):
        raise ValueError('Restore the unrelated Unity test preview before switching worklist Actions.')
    association = _association_record(item) if side == 'CUSTOM' else None
    ua._set_playing(context, False)
    ad = rig.animation_data_create()
    ad.action, ad.action_slot = action, slot
    ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.0
    if side == 'CUSTOM':
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
    context.view_layer.update()
    saved.status, saved.has_error = item.name + ' · ' + ('Source (read-only)' if side == 'SOURCE' else 'Custom'), False
    return action


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
    _idle(context)
    saved = state(context)
    item = _item(saved, item_id)
    index = next(i for i, entry in enumerate(saved.items) if entry.item_id == item_id)
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
    _idle(context, _collection=_collection)
    saved = state(context)
    item = current(context)
    if item is None or item.side != 'CUSTOM':
        raise ValueError('Select a Custom worklist Action before syncing. Source is read-only.')
    _check_baseline(item)
    if not item.rig.animation_data or item.rig.animation_data.action is not item.custom_action:
        raise ValueError('The active Action changed; select this entry\'s Custom Action again.')
    catalog = _fresh_workspace(saved)
    clip = next((entry for entry in catalog['clips'] if _clip_key(entry) == item.clip_key), None)
    if (clip is None or clip.get('needsRebuild') or not clip.get('linkManifest')
            or Path(clip['linkManifest']).resolve() != Path(item.manifest_path).resolve()):
        raise ValueError('This Controller slot needs a freshly prepared Unity Link; existing edits are kept.')
    current_link = links.load_link(item.manifest_path)
    if links._identity(current_link) != links._identity(json.loads(item.link_identity)):
        raise ValueError('Unity rebuilt this clip Link. Existing edits are kept; import the new baseline as a separate worklist.')
    from .animation_worklist_collection import export_implementation, item_fingerprint
    # Collect before launching: a proof error must never strand a worker without
    # its poll/disposal owner. The snapshot freezes in the same event-loop turn.
    proof = item_fingerprint(context, item)
    implementation = export_implementation()
    if _collection_proof is not None:
        if not _collection or not _collection_proof(proof):
            raise ValueError('The collection launch was cancelled or lost its fingerprint owner.')
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
