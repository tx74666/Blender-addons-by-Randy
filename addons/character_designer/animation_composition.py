"""Persistent A+B authoring session; C is never an authored/export Action.

Staged implementation. Native Full Stack and save/reopen/Undo acceptance are
required before deployment. Only explicit operators may write Action content.
"""
from contextlib import contextmanager
from copy import deepcopy
import base64
import importlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import uuid

import bpy

try:
    from . import animation_composition_contract as contract
except ImportError:
    import animation_composition_contract as contract

SESSION = 'character_designer_composition_session_v1'
REFS = 'character_designer_composition_refs_v1'
OWNER = 'character_designer_composition_owner_v1'
DRAFTS = 'character_designer_composition_drafts_v1'
VERSION = 1
REFERENCE_OWNER = 'character_designer_composition_reference_v1'
REFERENCE_RECEIPT_PREFIX = 'sha256_'
_POINT_FIELDS = ('co', 'handle_left', 'handle_right', 'handle_left_type',
    'handle_right_type', 'interpolation', 'easing', 'amplitude', 'back', 'period', 'type')
_CURVE_FIELDS = ('extrapolation', 'auto_smoothing', 'mute', 'lock', 'color_mode', 'color')
_STRIP_FIELDS = ('frame_start', 'frame_end', 'action_frame_start', 'action_frame_end',
    'scale', 'repeat', 'blend_type', 'influence', 'extrapolation', 'mute',
    'blend_in', 'blend_out', 'use_auto_blend', 'use_animated_influence',
    'use_animated_time', 'use_animated_time_cyclic', 'use_reverse', 'use_sync_length')


def _host(name):
    return importlib.import_module('character_designer.' + name)


def _service(name):
    return importlib.import_module((__package__ + '.' if __package__ else '') + name)


def active(rig):
    return rig is not None and SESSION in rig


def item_spec(item):
    """An explicit unknown composition never falls back to a legacy route."""
    raw = getattr(item, 'composition_json', '')
    outer = json.loads(item.link_identity) if getattr(item, 'link_identity', '') else {}
    proposed = outer.get('composition')
    if not raw and proposed is None:
        return None
    if not raw:
        raise ValueError('This explicit composition row has no authenticated local DTO. Refresh its prepared work item.')
    spec = contract.validate_item(json.loads(raw), item)
    if (contract.identity(spec) != getattr(item, 'composition_sha256', '')
            or proposed is None or contract.identity(proposed) != contract.identity(spec)
            or getattr(item, 'application_role', '') != contract.ROLE
            or getattr(item, 'edit_scope', '') != contract.SCOPE):
        raise ValueError('The composition identity or Upper Body role differs from its prepared work item.')
    return spec


def bind_item(item, record, *, workspace_path, workspace_identity):
    """Populate new fields only when publishing a newly validated row."""
    spec = record.get('composition')
    if spec is None:
        return
    spec = contract.validate_item(spec, item)
    if record.get('editScope') != contract.SCOPE:
        raise ValueError('An explicit composition needs its declared B role and upper-body scope.')
    item.composition_json = contract.canonical_bytes(spec).decode('utf-8')
    item.composition_sha256 = contract.identity(spec)
    item.application_role, item.edit_scope = contract.ROLE, contract.SCOPE
    item.workspace_path, item.workspace_identity = workspace_path, workspace_identity


def _require_native_action_timebase(action, rate):
    """Only explicit packet-clock imports authorize prepared Composition reuse."""
    if (type(rate) not in (int, float) or not math.isfinite(rate) or not 1 <= rate <= 240
            or action is None):
        raise ValueError('The prepared Composition Action has no valid native packet clock.')
    native = action.get(_host('unity_animation').NATIVE_TIMEBASE_KEY)
    actual = action.get('unity_sample_rate')
    if (type(native) not in (int, float) or native != rate
            or type(actual) not in (int, float) or actual != rate
            or type(action.get('unity_start_frame')) not in (int, float)
            or action.get('unity_start_frame') != 1.):
        raise ValueError('The prepared Composition Action has no proved native packet clock; prepare a separate work item.')


def _packet_for_item(item):
    links, ua = _host('animation_link'), _host('unity_animation')
    path = item.custom_action.get(links.PACKAGE_KEY, '') if item.custom_action else ''
    if not path:
        raise ValueError('B requires its own prepared bind-skeleton packet; A was not used as B provenance.')
    packet = ua.load_package(path)
    if item.custom_action.get(links.PACKAGE_HASH_KEY, '').lower() != packet['_sha256']:
        raise ValueError('The B source packet changed; its drafts were retained.')
    spec = item_spec(item)
    _service('animation_composition_controls').validate_packet_composition(spec, packet)
    if not spec['editNewActionId']:
        _require_native_action_timebase(item.custom_action, packet['sampleRate'])
        _require_native_action_timebase(item.source_action, packet['sampleRate'])
    return packet


def item_fingerprint(context, item, *, source=False):
    spec = item_spec(item)
    if spec is None:
        return None
    if source:
        _host('animation_worklist')._check_baseline(item)
        action, handle = item.source_action, item.source_slot
        if action is None:
            raise ValueError('This new B has no prepared Source baseline.')
    else:
        action, handle = item.custom_action, item.custom_slot
        if active(item.rig):
            session, selected, scope, _tracks = validate(context, item.rig)
            if selected.item_id == item.item_id:
                # Completing missing channels in a draft is an evaluation seed,
                # not an artist edit. Opening/seek/Solo never manufacture BEdited.
                if _motion_hash(session['edit_action'], session['edit_slot']) != session['edit_initial_hash']:
                    action, handle = session['edit_action'], session['edit_slot']
                return _service('animation_composition_export').effective_fingerprint(action, handle, scope, spec)
    packet = _packet_for_item(item)
    scope = _service('animation_composition_export').resolve_scope(context, item.rig, spec, packet)
    return _service('animation_composition_export').effective_fingerprint(action, handle, scope, spec)


def composition_metadata(context, item):
    spec = item_spec(item)
    if spec is None:
        return None
    if active(item.rig):
        session, selected, _scope, _tracks = validate(context, item.rig)
        if selected.item_id != item.item_id:
            raise ValueError('Finish the other composition draft before scanning this row.')
        reference_hash = session['base_hash']
    else:
        action, handle = find_reference(context, spec)
        reference_hash = _motion_hash(action, handle)
    return {'composition': spec, 'spec_sha256': contract.identity(spec),
        'reference_action_sha256': reference_hash,
        'reference_packet_sha256': spec['referencePacketSha256']}


def _library_state(context):
    saved, workspace = _host('animation_worklist').state(context), _host('animation_workspace')
    if not saved.composition_workspace_path:
        raise ValueError('Connect the explicit Composition Library prepared in Unity first.')
    record = workspace.load_workspace(saved.composition_workspace_path)
    worklist = _host('animation_worklist')
    if worklist._identity(record) != saved.composition_workspace_identity:
        raise ValueError('The composition library changed character/model identity; existing rows were retained.')
    return record, SimpleNamespace(workspace_path=record['_manifest_path'],
        workspace_identity=saved.composition_workspace_identity,
        workspace_id=record['workspaceId'], target_guid=record['targetGuid'].lower(),
        target_name=record['targetName'], model_file=record['modelFile'],
        model_sha256=record['modelSha256'].lower(), rig=saved.rig)


def connect_library(context, filepath):
    """Explicit separate library; legacy workspace and adoption identity stay intact."""
    worklist, workspace = _host('animation_worklist'), _host('animation_workspace')
    worklist._idle(context)
    saved = worklist.state(context)
    if active(saved.rig):
        raise ValueError('Close Composition before connecting another library; its draft was retained.')
    record = workspace.load_workspace(bpy.path.abspath(filepath))
    if (saved.rig is None or not _host('animation_worklist_target').is_adopted(saved.rig)
            or record['targetGuid'].lower() != saved.target_guid):
        raise ValueError('Use this character\'s existing Main Rig before connecting its separate Composition Library.')
    clips = [row for row in record['clips'] if row.get('composition') is not None]
    for row in clips:
        spec = contract.validate(row['composition'], prepared=bool(row.get('linkManifest') and not row.get('needsRebuild')))
        if (workspace.work_item_id(row) != uuid.UUID(spec['editWorkItemId']).hex
                or row.get('editScope') != contract.SCOPE):
            raise ValueError('A composition library row has contradictory B identity or scope.')
    if not clips:
        raise ValueError('This library has no explicitly prepared upper-body Composition work items.')
    # All metadata checks precede property writes; no packet/model is loaded by
    # a UI library connection. Selected Add owns exact Rest/map/model checks.
    saved.composition_workspace_path = record['_manifest_path']
    saved.composition_workspace_identity = worklist._identity(record)
    saved.composition_catalog.clear()
    for clip in clips:
        row = saved.composition_catalog.add()
        row.clip_key, row.name = workspace.row_key(clip), clip['clipName']
        row.work_item_id = workspace.work_item_id(clip)
        row.source_name = clip.get('sourceName') or clip.get('sourceAsset') or ''
        row.link_path = clip.get('linkManifest') or ''
        row.ready = bool(row.link_path and not clip.get('needsRebuild'))
    saved.composition_catalog_index = 0
    saved.status, saved.has_error = 'Composition Library connected. Existing Source/Custom rows are retained.', False
    return record


def refresh_configuration(context, item=None):
    """Accept an explicit new A/timing/weapon manifest without rewriting B curves."""
    worklist, links = _host('animation_worklist'), _host('animation_link')
    worklist._idle(context)
    item = item or worklist.current(context)
    if item is None or item_spec(item) is None:
        raise ValueError('Choose the explicit composition B work item.')
    if active(item.rig):
        raise ValueError('Close Composition before accepting another prepared configuration.')
    old, latest = json.loads(item.link_identity), links.load_link(item.manifest_path)
    # Only A/config fields may change on this row. B source, source Rest, mask,
    # native scope, model and all required outer identities remain exact.
    required = (*links.IDENTITY_FIELDS, *(k for k in links.OPTIONAL_IDENTITY_FIELDS if k != 'composition'))
    if any(old.get(k) != latest.get(k) for k in required):
        raise ValueError('The B source or model changed; keep this draft and prepare a separate work item.')
    before, after = item_spec(item), contract.validate_item(latest.get('composition'), item)
    if before != after:
        raise ValueError('This B packet seals its composition configuration. Prepare and add a separate variant before changing A, timing or weapon context.')
    for field in ('editWorkItemId', 'editNewActionId', 'applicationRole', 'sourceRestSha256',
                  'humanRoles', 'allowedBonePaths', 'editableControlIds', 'mask', 'blendMode', 'weight'):
        if before[field] != after[field]:
            raise ValueError('The B control scope or Rest changed; prepare a separate work item.')
    packet = _packet_for_item(item)
    _service('animation_composition_export').resolve_scope(context, item.rig, after, packet)
    # Read/authenticate the new A before publishing the config. No B motion is
    # imported or changed, and old read-only A caches remain recoverable.
    prepare_reference(context, item, after)
    item.link_identity = worklist._encoded(latest)
    bind_item(item, latest, workspace_path=item.workspace_path, workspace_identity=item.workspace_identity)
    saved = worklist.state(context)
    saved.status, saved.has_error = 'Composition reference/configuration updated. Overlay animation is unchanged.', False
    return after


def add_work_item(context, clip_key, *, activate_new=True, _collection=False):
    """Import exact prepared B on the existing rig with per-row provenance."""
    worklist, links = _host('animation_worklist'), _host('animation_link')
    source, target = _host('animation_link_source'), _host('animation_worklist_target')
    worklist._idle(context, _collection=_collection)
    saved = worklist.state(context)
    if active(saved.rig):
        raise ValueError('Close Composition before adding another B work item.')
    catalog, library = _library_state(context)
    _record, clip, link, packet, export_name, model = worklist._prepared(library, clip_key, existing_target=True)
    if any(record.get('compositionReferenceSource') is True or record.get('sourceOnly') is True
           for record in (clip, link)):
        raise ValueError('The prepared base reference is read-only. Create its Base Work Copy instead of adding a generic Custom.')
    if (clip.get('composition') is None or link.get('composition') is None
            or contract.identity(clip['composition']) != contract.identity(link['composition'])
            or clip.get('editScope') != contract.SCOPE or link.get('editScope') != contract.SCOPE):
        raise ValueError('The prepared B Link and Composition Library disagree; no row was added.')
    _service('animation_composition_controls').validate_packet_composition(link['composition'], packet)
    # New library model provenance must be current for Sync. This does not
    # rewrite any historical row or the original Main Rig adoption snapshot.
    source._source_model(link, library.model_file)
    rig = saved.rig
    if not target.is_adopted(rig):
        raise ValueError('Use the existing Main Rig before adding an explicit B work item.')
    prior_rows = [row for row in saved.items if row.clip_key == clip_key]
    if len(prior_rows) > 1:
        raise ValueError('This prepared B has ambiguous existing rows; no Action was activated.')
    if prior_rows:
        # Existing rows never acquire a changed contract as a side effect of Add.
        prior_row = prior_rows[0]
        worklist._fresh_workspace(saved, prior_row)
        worklist._check_baseline(prior_row)
        existing_packet = _packet_for_item(prior_row)
        if (item_spec(prior_row) != contract.validate(link['composition'])
                or links._identity(json.loads(prior_row.link_identity)) != links._identity(link)
                or Path(prior_row.workspace_path).resolve() != Path(library.workspace_path).resolve()
                or prior_row.workspace_identity != library.workspace_identity
                or prior_row.model_sha256 != library.model_sha256
                or existing_packet['_sha256'] != packet['_sha256']
                or Path(existing_packet['_path']).resolve() != Path(packet['_path']).resolve()):
            raise ValueError('The existing B row differs from its selected prepared Link/packet; its drafts were retained.')
        _service('animation_composition_export').resolve_scope(context, rig, link['composition'], existing_packet)
        target.ensure_active(context, rig)
        if activate_new:
            worklist.activate(context, prior_row.item_id, 'CUSTOM', _collection=_collection)
        return prior_row
    _service('animation_composition_export').resolve_scope(context, rig, link['composition'], packet)
    target.ensure_active(context, rig)
    previous_index = saved.active_index
    captured, old_context = worklist._capture_rig(context, rig), source._context_state(context)
    baseline = custom = linked = None
    cache_file = None
    item_id = uuid.uuid4().hex
    added = False
    try:
        baseline = source._import_action(context, rig, packet, 1, native_timebase=True).action
        if target.is_adopted(rig):
            target.restore_import_preview(context, rig, baseline)
        worklist._clean_action(baseline)
        custom = baseline.copy()
        worklist._clean_action(custom)
        custom.name = library.target_name + ' · ' + clip['clipName'] + ' · Custom'
        linked, cache_file, cache_hash = worklist._linked_baseline(library, baseline, item_id, clip['clipName'])
        latest = links.load_link(link['_manifest_path'])
        if (links._identity(latest) != links._identity(link)
                or contract.identity(latest.get('composition')) != contract.identity(link['composition'])):
            raise ValueError('The prepared B Link changed during import; all earlier rows were retained.')
        # Structural semantic/control whitelist is proven before row commit.
        _service('animation_composition_export').resolve_scope(context, rig, link['composition'], packet)
        target.attach_action_motion_data(rig, custom)
        item = saved.items.add()
        added = True
        item.item_id, item.clip_key, item.name = item_id, clip_key, clip['clipName']
        item.work_item_id = _host('animation_workspace').work_item_id(clip)
        item.rig, item.source_action, item.custom_action = rig, linked, custom
        item.source_slot, item.custom_slot = worklist._slot(linked).handle, worklist._slot(custom).handle
        item.manifest_path, item.link_identity = link['_manifest_path'], worklist._encoded(link)
        item.export_rig_name, item.model_file, item.model_sha256 = export_name, str(model), library.model_sha256
        item.source_file, item.source_hash = cache_file, cache_hash
        bind_item(item, link, workspace_path=library.workspace_path, workspace_identity=library.workspace_identity)
        prepare_reference(context, item, link['composition'], _collection=_collection)
        _host('animation_worklist_collection').remember_source_baseline(context, item)
        worklist._restore_rig(context, rig, captured)
        source._restore_context(context, old_context, playing=True)
        saved.active_index = len(saved.items) - 1 if activate_new else previous_index
        saved.status, saved.has_error = 'Added ' + item.name + '. Open Composition to view the base while editing the overlay.', False
        if activate_new:
            worklist.activate(context, item.item_id, 'CUSTOM', _collection=_collection)
        bpy.data.actions.remove(baseline)
        return item
    except Exception:
        worklist._restore_rig(context, rig, captured)
        source._restore_context(context, old_context, playing=True)
        if added:
            index = next((i for i, row in enumerate(saved.items) if row.item_id == item_id), None)
            if index is not None:
                saved.items.remove(index)
        for action in (custom, linked, baseline):
            if action is not None:
                action.use_fake_user = False if action.library is None else action.use_fake_user
                if action.users == 0:
                    bpy.data.actions.remove(action)
        if cache_file:
            Path(cache_file).unlink(missing_ok=True)
        saved.active_index = previous_index
        raise
    finally:
        captured['data'].use_fake_user = captured['fake_user']


def _prepared_reference_entry(context, spec, *, item=None):
    """Resolve a genuine ordinary A companion by exact source/packet identity."""
    worklist, workspace, links = (_host(name) for name in
        ('animation_worklist', 'animation_workspace', 'animation_link'))
    catalog, library = _library_state(context)
    source_spec = spec
    if item is not None and getattr(item, 'new_animation', False):
        source_spec = contract.validate(_packet_for_item(item).get('composition'))
    owners = [clip for clip in catalog['clips'] if clip.get('composition') is not None
        and workspace.work_item_id(clip) == uuid.UUID(source_spec['editWorkItemId']).hex]
    if len(owners) != 1 or contract.validate(owners[0]['composition']) != contract.validate(source_spec):
        raise ValueError('The exact prepared B source owner is missing or ambiguous; no A companion was substituted.')
    owner_guid = owners[0].get('linkGuid', '')
    if (not isinstance(owner_guid, str) or len(owner_guid) != 32
            or any(char not in '0123456789abcdefABCDEF' for char in owner_guid)):
        raise ValueError('The prepared B source lost its actual Unity Link asset GUID.')
    owner_guid = owner_guid.lower()
    expected = (spec['baseClipGuid'], spec['baseClipLocalId'])
    candidates = []
    for clip in catalog['clips']:
        if (clip.get('composition') is not None or clip.get('editScope') not in (None, '', 'All')
                or clip.get('compositionReferenceSource') is not True
                or clip.get('compositionOwnerLinkGuid', '').lower() != owner_guid
                or workspace.clip_identity(clip) != expected):
            continue
        path = clip.get('linkManifest')
        if not path or clip.get('needsRebuild'):
            continue
        link = links.load_link(path)
        if (link.get('composition') is None and link.get('editScope') in (None, '', 'All')
                and link.get('compositionReferenceSource') is True
                and link.get('compositionOwnerLinkGuid', '').lower() == owner_guid
                and link['sourcePackageSha256'].lower() == spec['referencePacketSha256']
                and Path(link['sourcePackage']).resolve() == Path(spec['referencePacketFile']).resolve()):
            candidates.append(clip)
    if len(candidates) != 1 or not workspace.work_item_id(candidates[0]):
        raise ValueError('Prepare one genuine ordinary A companion Link with its own Work Item ID and the exact reference packet before creating an A work copy.')
    key = workspace.row_key(candidates[0])
    record, clip, link, packet, export_name, model = worklist._prepared(library, key, existing_target=True)
    try:
        asset_guid = clip['linkGuid']
        if (not isinstance(asset_guid, str) or len(asset_guid) != 32
                or any(char not in '0123456789abcdefABCDEF' for char in asset_guid)
                or uuid.UUID(clip['linkId']) != uuid.UUID(link['linkId'])):
            raise ValueError('wrong Link')
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ValueError('The prepared A companion lost its actual Unity asset GUID or Link UUID.') from error
    if (packet['_sha256'] != spec['referencePacketSha256']
            or packet.get('clipGuid', '').lower() != spec['baseClipGuid']
            or packet.get('clipLocalId') != spec['baseClipLocalId']
            or packet.get('composition') is not None or packet.get('editScope') not in (None, '', 'All')):
        raise ValueError('The prepared A companion does not preserve the actual ordinary A packet.')
    return library, clip, link, packet, export_name, model


def create_reference_work_copy(context, b_item_id=None):
    """Create an explicit New A Action from the actual prepared A Source Link.

    The protected A cache and Source Link remain read-only. The New Action
    service authenticates this per-action origin before publishing fresh IDs;
    registration never substitutes the composition B template or syncs A.
    """
    worklist, source, target = (_host(name) for name in
        ('animation_worklist', 'animation_link_source', 'animation_worklist_target'))
    worklist._idle(context)
    saved = worklist.state(context)
    item = worklist._item(saved, b_item_id) if b_item_id else worklist.current(context)
    if item is None or item_spec(item) is None:
        raise ValueError('Choose the authenticated composition B work item whose A should have an explicit work copy.')
    if active(item.rig):
        raise ValueError('Close Composition before creating its separate A work copy; the current draft was retained.')
    spec = item_spec(item)
    library, clip, link, packet, _export_name, _model = _prepared_reference_entry(context, spec, item=item)
    source._source_model(link, library.model_file)
    target.ensure_active(context, item.rig)
    context.view_layer.update()
    _host('unity_animation')._mapping(item.rig, packet, context.scene.unit_settings.scale_length)
    protected, slot = prepare_reference(context, item, spec)
    captured, old_context = worklist._capture_rig(context, item.rig), source._context_state(context)
    previous_index = saved.active_index
    draft, registered = None, None
    try:
        draft = protected.copy()
        worklist._clean_action(draft)
        draft.pop(REFERENCE_OWNER, None)
        draft.name = library.target_name + ' · ' + clip['clipName'] + ' · Base Work Copy'
        ad = item.rig.animation_data_create()
        ad.action, ad.action_slot = draft, _slot(draft, slot)
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.
        origin = {'workspace_manifest': library.workspace_path,
            'workspace_identity': library.workspace_identity, 'clip_key': worklist._clip_key(clip),
            'link_manifest': link['_manifest_path'], 'source_packet': packet['_path'],
            'source_packet_sha256': packet['_sha256']}
        registered = _host('animation_worklist_new').register(context, origin_template=origin)
        if (registered.custom_action is not draft or not registered.new_animation
                or not registered.new_action_id or not registered.work_item_id
                or registered.source_action is not None or item_spec(registered) is not None
                or registered.custom_action.get(_host('animation_link').PACKAGE_HASH_KEY, '').lower() != packet['_sha256']):
            raise ValueError('The New A registration did not retain its genuine local identity and exact A packet.')
        worklist._restore_rig(context, item.rig, captured)
        source._restore_context(context, old_context, playing=True)
        saved.active_index = next(i for i, row in enumerate(saved.items) if row.item_id == item.item_id)
        saved.status, saved.has_error = 'Created ' + registered.name + '. Choose Edit Base; Sync and Unity Apply remain separate actions.', False
        return registered
    except Exception:
        worklist._restore_rig(context, item.rig, captured)
        source._restore_context(context, old_context, playing=True)
        saved.active_index = previous_index
        if draft is not None and registered is None:
            draft.use_fake_user = False
            if draft.users == 0:
                bpy.data.actions.remove(draft)
        raise
    finally:
        captured['data'].use_fake_user = captured['fake_user']


def reference_receipt_key(packet_sha256):
    """Encode all 256 digest bits within Blender's 63-byte property-key limit."""
    if (not isinstance(packet_sha256, str) or len(packet_sha256) != 64
            or any(char not in '0123456789abcdefABCDEF' for char in packet_sha256)):
        raise ValueError('A composition reference receipt requires the full SHA-256 digest.')
    encoded = base64.b32encode(bytes.fromhex(packet_sha256)).decode('ascii').rstrip('=')
    return REFERENCE_RECEIPT_PREFIX + encoded


def reference_receipt(rig, packet_sha256):
    """Read one exact encoded receipt; full SHA/schema remain in its provenance.

    Cache bytes, native linked Action and exact slot/motion are independently
    authenticated by find_reference. A malformed stored receipt is never a
    missing reference and never authorizes a replacement cache.
    """
    key = reference_receipt_key(packet_sha256)
    receipts = rig.get(REFERENCE_OWNER, {}) if rig is not None else {}
    if not hasattr(receipts, 'get'):
        raise ValueError('The protected composition reference receipt inventory is incomplete.')
    if key not in receipts:
        return None
    raw = receipts[key]
    try:
        receipt = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError('The protected composition reference receipt is incomplete.') from error
    if (not isinstance(receipt, dict) or receipt.get('schema') != 'cdesigner.composition-reference/1'
            or receipt.get('packet_sha256') != packet_sha256.lower()):
        raise ValueError('The protected composition reference receipt has another full SHA-256 or schema.')
    return receipt


def find_reference(context, spec):
    """Read-only lookup by packet ownership and exact native Action/slot proof."""
    spec = contract.validate(spec)
    rig = _host('animation_worklist').state(context).rig
    receipt = reference_receipt(rig, spec['referencePacketSha256'])
    candidates = []
    for action in bpy.data.actions:
        proof = action.get(REFERENCE_OWNER)
        if not proof:
            continue
        try:
            proof = json.loads(proof)
            if (proof['packet_sha256'] != spec['referencePacketSha256']
                    or Path(proof['packet_path']).resolve() != Path(spec['referencePacketFile']).resolve()):
                continue
            if (not receipt or any(receipt.get(k) != v for k, v in proof.items())
                    or proof.get('schema') != 'cdesigner.composition-reference/1'
                    or proof.get('native_timebase') is not True
                    or type(proof.get('native_sample_rate')) not in (int, float)
                    or not math.isfinite(proof['native_sample_rate'])
                    or not 1 <= proof['native_sample_rate'] <= 240
                    or action.library is None or not Path(receipt['cache']).is_file()
                    or _host('animation_link')._sha256(Path(receipt['cache'])) != receipt['cache_sha256']
                    or Path(bpy.path.abspath(action.library.filepath)).resolve() != Path(receipt['cache']).resolve()
                    or _motion_hash(action, proof['slot']) != proof['motion_sha256']):
                raise ValueError('The protected composition reference cache changed; no similarly named Action was substituted.')
            _require_native_action_timebase(action, proof['native_sample_rate'])
            candidates.append((action, proof['slot']))
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError('The protected composition reference ownership is incomplete.') from error
    if len(candidates) != 1:
        raise ValueError('Prepare the exact read-only A reference first; no ambiguous Action was chosen.')
    return candidates[0]


def _reconnect_reference(context, spec, receipt):
    """Explicitly link only the original retained cache; never prepare a replacement."""
    links, ua = _host('animation_link'), _host('unity_animation')
    try:
        for action in bpy.data.actions:
            raw = action.get(REFERENCE_OWNER)
            if raw:
                proof = json.loads(raw)
                if (not isinstance(proof, dict) or not isinstance(proof.get('packet_sha256'), str)
                        or proof['packet_sha256'].lower() == spec['referencePacketSha256']):
                    raise ValueError('An existing reference proof cannot authorize another linked Action.')
        packet = ua.load_package(spec['referencePacketFile'])
        cache = Path(receipt['cache'])
        motion = receipt['motion_sha256']
        if (receipt['schema'] != 'cdesigner.composition-reference/1'
                or receipt['packet_sha256'] != spec['referencePacketSha256']
                or Path(receipt['packet_path']).resolve() != Path(spec['referencePacketFile']).resolve()
                or packet['_sha256'] != spec['referencePacketSha256']
                or packet.get('clipGuid', '').lower() != spec['baseClipGuid']
                or packet.get('clipLocalId') != spec['baseClipLocalId']
                or receipt.get('native_timebase') is not True
                or type(receipt.get('native_sample_rate')) not in (int, float)
                or not math.isfinite(receipt['native_sample_rate'])
                or not 1 <= receipt['native_sample_rate'] <= 240
                or receipt['native_sample_rate'] != packet['sampleRate']
                or type(receipt.get('slot')) is not int or receipt['slot'] <= 0
                or not isinstance(motion, str) or len(motion) != 64
                or any(char not in '0123456789abcdefABCDEF' for char in motion)
                or not cache.is_absolute() or not cache.is_file() or cache.is_symlink()
                or links._sha256(cache) != receipt['cache_sha256']):
            raise ValueError('The original protected A cache/packet receipt changed; no replacement was prepared.')
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('The original protected A cache receipt is incomplete.') from error
    before = {action.as_pointer() for action in bpy.data.actions}
    try:
        with bpy.data.libraries.load(str(cache), link=True) as (available, selected):
            names = list(available.actions)
            if len(names) != 1 or not isinstance(names[0], str) or not names[0]:
                raise ValueError('The original A cache has no unique Action; no display-name substitute was chosen.')
            selected.actions = names
        linked = selected.actions[0]
        created = {action.as_pointer() for action in bpy.data.actions} - before
        if (linked is None or linked.library is None
                or linked.as_pointer() in before or created != {linked.as_pointer()}):
            raise ValueError('The A reconnect did not create exactly one new owned linked Action.')
        result = find_reference(context, spec)
        if result[0].as_pointer() != linked.as_pointer():
            raise ValueError('The reconnected A did not pass the original full reference proof.')
        return result
    except BaseException as error:
        recovery_errors = []
        for action in list(bpy.data.actions):
            if action.as_pointer() in before or action.library is None:
                continue
            if Path(bpy.path.abspath(action.library.filepath)).resolve() != cache.resolve():
                continue
            try:
                bpy.data.actions.remove(action)
            except Exception as recovery_error:
                recovery_errors.append(recovery_error)
        if recovery_errors:
            error.add_note('Exact A reconnect cleanup also failed: ' +
                '; '.join(str(recovery_error) for recovery_error in recovery_errors))
        raise


def prepare_reference(context, item, spec=None, *, _collection=False):
    """Bake only the declared A packet, then restore the artist and link Action-only."""
    worklist, ua, links = _host('animation_worklist'), _host('unity_animation'), _host('animation_link')
    worklist._idle(context, _collection=_collection)
    spec = spec or item_spec(item)
    spec = contract.validate_item(spec, item)
    if active(item.rig):
        raise ValueError('Close Composition before preparing its separate protected A reference.')
    # Validate stored ownership even if its linked Action has disappeared.
    # Only complete absence creates a cache; an orphan is never replaced.
    receipt = reference_receipt(item.rig, spec['referencePacketSha256'])
    matching = [a for a in bpy.data.actions if a.get(REFERENCE_OWNER) and
        json.loads(a[REFERENCE_OWNER]).get('packet_sha256') == spec['referencePacketSha256']]
    if receipt is not None or matching:
        if receipt is not None and not matching:
            return _reconnect_reference(context, spec, receipt)
        return find_reference(context, spec)
    packet = ua.load_package(spec['referencePacketFile'])
    if (packet['_sha256'] != spec['referencePacketSha256'] or packet.get('clipGuid', '').lower() != spec['baseClipGuid']
            or packet.get('clipLocalId') != spec['baseClipLocalId']):
        raise ValueError('The A clip/packet differs from its explicit composition identity.')
    rig, target, source = item.rig, _host('animation_worklist_target'), _host('animation_link_source')
    target.ensure_active(context, rig)
    prior_receipts_present = REFERENCE_OWNER in rig
    prior_inventory = rig.get(REFERENCE_OWNER, {})
    prior_receipts = deepcopy(prior_inventory.to_dict() if hasattr(prior_inventory, 'to_dict') else dict(prior_inventory))
    captured, old_context = worklist._capture_rig(context, rig), source._context_state(context)
    imported = linked = None
    cache = None
    receipt_write_attempted = False
    try:
        context.view_layer.update()
        ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
        imported = source._import_action(context, rig, packet, 1, native_timebase=True).action
        target.restore_import_preview(context, rig, imported)
        worklist._clean_action(imported)
        slot = worklist._slot(imported).handle
        imported.name = 'Reference · ' + str(packet.get('clipName') or 'Base')
        folder = Path(bpy.utils.user_resource('DATAFILES', path='character_designer/composition_references', create=True))
        folder.mkdir(parents=True, exist_ok=True)
        cache = folder / (spec['referencePacketSha256'][:16] + '-' + uuid.uuid4().hex[:12] + '.blend')
        proof = {'schema': 'cdesigner.composition-reference/1', 'packet_path': packet['_path'],
            'packet_sha256': packet['_sha256'], 'slot': slot, 'motion_sha256': _motion_hash(imported, slot),
            'native_timebase': True, 'native_sample_rate': packet['sampleRate']}
        # Native cache hash is recorded on the linked Action after writing; an
        # ID property on a linked Action cannot be edited. Therefore keep the
        # cache receipt on the rig independently of the read-only Action.
        imported[REFERENCE_OWNER] = json.dumps(proof, separators=(',', ':'))
        bpy.data.libraries.write(str(cache), {imported}, fake_user=True, compress=True)
        with bpy.data.libraries.load(str(cache), link=True) as (_available, selected):
            selected.actions = [imported.name]
        linked = selected.actions[0]
        if linked is None or linked.library is None:
            raise ValueError('Blender did not create the protected A reference Action.')
        receipt = {**proof, 'cache': str(cache), 'cache_sha256': links._sha256(cache)}
        receipts = deepcopy(prior_receipts)
        receipts[reference_receipt_key(spec['referencePacketSha256'])] = json.dumps(receipt, separators=(',', ':'))
        worklist._restore_rig(context, rig, captured)
        source._restore_context(context, old_context, playing=True)
        bpy.data.actions.remove(imported)
        imported = None  # Removed RNA must not be inspected if publication fails.
        captured['data'].use_fake_user = captured['fake_user']
        # Publish only after the artist/context and temporary Action are restored.
        receipt_write_attempted = True
        rig[REFERENCE_OWNER] = receipts
        return linked, slot
    except Exception as error:
        recovery_errors = []

        def recover(operation):
            try:
                operation()
            except Exception as recovery_error:
                recovery_errors.append(recovery_error)

        # A property assignment may fail after changing the inventory. Roll it
        # back before recovery calls which may themselves raise.
        def restore_receipt_inventory():
            if prior_receipts_present:
                rig[REFERENCE_OWNER] = deepcopy(prior_receipts)
            else:
                rig.pop(REFERENCE_OWNER, None)
        if receipt_write_attempted:
            recover(restore_receipt_inventory)
        recover(lambda: worklist._restore_rig(context, rig, captured))
        recover(lambda: source._restore_context(context, old_context, playing=True))
        for action in (linked, imported):
            def remove_owned_action(action=action):
                try:
                    if action is not None and action.users <= int(action.use_fake_user):
                        bpy.data.actions.remove(action)
                except ReferenceError:
                    # A failing remove may already have invalidated this RNA.
                    pass
            recover(remove_owned_action)
        if cache:
            recover(lambda: cache.unlink(missing_ok=True))
        recover(lambda: setattr(captured['data'], 'use_fake_user', captured['fake_user']))
        if recovery_errors:
            error.add_note('Composition reference recovery also failed: ' +
                '; '.join(str(recovery_error) for recovery_error in recovery_errors))
        raise


def _plain(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return list(value)


def _encode(value, references):
    if isinstance(value, bpy.types.ID):
        if isinstance(value, bpy.types.Armature):
            raise ValueError('Composition cannot add Armature ownership references to the protected Main Rig transaction.')
        for key, item in references.items():
            if item is value:
                return {'$id': key}
        key = str(len(references))
        references[key] = value
        return {'$id': key}
    if isinstance(value, dict):
        return {key: _encode(item, references) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item, references) for item in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError('Unsupported composition recovery field.')


def _decode(value, references, used=None):
    used = set() if used is None else used
    if isinstance(value, dict):
        if set(value) == {'$id'}:
            key = value['$id']
            if key not in references or not isinstance(references[key], bpy.types.ID):
                raise ValueError('A composition recovery datablock is missing.')
            used.add(key)
            return references[key]
        return {key: _decode(item, references, used) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item, references, used) for item in value]
    return value


def _store(rig, session):
    refs = {}
    encoded = _encode(session, refs)
    payload = json.dumps({'payload': encoded, 'sha256': contract.digest(encoded)},
        sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    old_refs = dict(rig.get(REFS, {})) if REFS in rig else None
    old_payload = rig.get(SESSION)
    try:
        rig[REFS], rig[SESSION] = refs, payload
    except Exception:
        if old_refs is None:
            rig.pop(REFS, None)
        else:
            rig[REFS] = old_refs
        if old_payload is None:
            rig.pop(SESSION, None)
        else:
            rig[SESSION] = old_payload
        raise


def _load(rig):
    try:
        envelope = json.loads(rig[SESSION])
        if (set(envelope) != {'payload', 'sha256'} or
                contract.digest(envelope['payload']) != envelope['sha256']):
            raise ValueError('The composition recovery record changed or was truncated.')
        used = set()
        session = _decode(envelope['payload'], rig.get(REFS, {}), used)
        if set(rig.get(REFS, {})) != used:
            raise ValueError('The composition reference table contains unowned references.')
        if any(isinstance(value, bpy.types.Armature) for value in rig.get(REFS, {}).values()):
            raise ValueError('The composition reference table cannot own Armature data.')
        if session.get('version') != VERSION or session.get('rig') is not rig:
            raise ValueError('This composition record belongs to another rig or version.')
        return session
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('The composition recovery record is unreadable; its draft was kept.') from error


def _slot(action, handle):
    return _host('animation_worklist')._slot(action, handle)


def _bag(action, handle):
    _slot(action, handle)
    if not action.is_action_layered or len(action.layers) != 1:
        raise ValueError('Composition editing currently requires one layered Action layer.')
    layer = action.layers[0]
    if len(layer.strips) != 1 or layer.strips[0].type != 'KEYFRAME':
        raise ValueError('Composition editing requires one Keyframe Action strip.')
    strip = layer.strips[0]
    bags = [bag for bag in strip.channelbags if bag.slot_handle == handle]
    if len(bags) != 1:
        raise ValueError('The exact composition Action slot has no unique channel bag.')
    return bags[0]


def _curves(action, handle):
    return list(_bag(action, handle).fcurves)


def _motion_hash(action, handle):
    proof = _host('animation_worklist_fingerprint').fingerprint_action(action, handle)
    if not proof['fingerprint_known']:
        raise ValueError('Composition Action cannot be authenticated: ' + proof['proof_reason'])
    return proof['action_fingerprint']


def _curve_payload(curve):
    # Refusing unimplemented animation features preserves them in the real B.
    if curve.modifiers or curve.sampled_points:
        raise ValueError('Composition direct editing does not rewrite F-Curve modifiers or sampled points.')
    values = {'path': curve.data_path, 'index': curve.array_index,
        'settings': {field: _plain(getattr(curve, field)) for field in _CURVE_FIELDS},
        'points': [{field: _plain(getattr(point, field)) for field in _POINT_FIELDS}
                   for point in curve.keyframe_points]}
    contract.canonical_bytes(values)
    return values


def _write_curve(bag, values):
    curve = bag.fcurves.new(data_path=values['path'], index=values['index'])
    for field, value in values['settings'].items():
        setattr(curve, field, value)
    curve.keyframe_points.add(len(values['points']))
    for point, row in zip(curve.keyframe_points, values['points']):
        for field in ('handle_left_type', 'handle_right_type'):
            setattr(point, field, row[field])
        for field, value in row.items():
            if field not in {'handle_left_type', 'handle_right_type'}:
                setattr(point, field, value)
    curve.update()
    return curve


def _constant_curve(bag, path, index, value, start, end):
    if not math.isfinite(value) or end <= start:
        raise ValueError('Hold Pose requires finite values over a positive duration.')
    curve = bag.fcurves.new(data_path=path, index=index)
    curve.keyframe_points.add(2)
    for point, frame in zip(curve.keyframe_points, (start, end)):
        point.co = frame, value
        point.interpolation = 'CONSTANT'
    curve.extrapolation = 'CONSTANT'
    curve.update()
    return curve


def _channel_rows(channels):
    rows = [list(channel) for channel in channels]
    if (not rows or len(rows) > 20000 or len(rows) != len({tuple(c) for c in rows})
            or any(len(c) != 2 or not isinstance(c[0], str) or type(c[1]) is not int or c[1] < 0 for c in rows)):
        raise ValueError('The verified composition channel scope is incomplete.')
    return sorted(rows)


def _frame_range(spec):
    # Shared contract times are seconds; the fixed authoring range starts at 1.
    return 1 + spec['sampleStart'] * spec['fps'], 1 + spec['sampleEnd'] * spec['fps']


def _following_hold(context, rig, spec, scope):
    if spec['timeMode'] != 'Raise':
        return None
    saved = _host('animation_worklist').state(context)
    matches = [item for item in saved.items if getattr(item, 'work_item_id', '')
        and uuid.UUID(item.work_item_id) == uuid.UUID(spec['holdWorkItemId'])]
    if len(matches) != 1:
        raise ValueError('Prepare and add the actual following Hold work item before opening Raise.')
    item = matches[0]
    hold = item_spec(item)
    if (item.rig is not rig or hold is None or hold['timeMode'] not in {'HoldPose', 'HoldLoop'}
            or hold['compositionId'] != spec['compositionId']
            or hold['editNewActionId'] != spec['holdNewActionId']):
        raise ValueError('Raise\'s following Hold identity or native rig differs from its prepared record.')
    for field in ('sourceRestSha256', 'allowedBonePaths', 'humanRoles', 'editableControlIds', 'mask', 'blendMode', 'weight'):
        if hold[field] != spec[field]:
            raise ValueError('Raise and its real following Hold must own the same verified upper-body scope.')
    _host('animation_worklist')._check_baseline(item)
    packet = _packet_for_item(item)
    other = _service('animation_composition_export').resolve_scope(context, rig, hold, packet)
    if tuple(other.channels) != tuple(scope.channels):
        raise ValueError('The following Hold resolved different effective input channels.')
    return {'item': item, 'spec': hold, 'packet': packet, 'scope': other,
        'custom_hash': _motion_hash(item.custom_action, item.custom_slot)}


def _assert_hold_constant(curves):
    for curve in curves:
        points = list(curve.keyframe_points)
        if not points:
            raise ValueError('Hold Pose needs a complete constant B expression.')
        value = points[0].co[1]
        if any(point.co[1] != value or point.interpolation == 'BEZIER' and
                (point.handle_left[1] != value or point.handle_right[1] != value)
                for point in points):
            raise ValueError('This B contains motion. Create an explicit Hold Pose work item or choose Hold Loop; no motion was silently frozen.')


def _editing_action(rig, item, spec, scope, owner):
    action = item.custom_action.copy()
    action.name = item.name + ' · Composition Edit'
    action.use_fake_user = True
    _host('animation_worklist')._clean_action(action)
    action[OWNER] = owner
    bag = _bag(action, item.custom_slot)
    channels = set(scope.channels)
    for curve in list(bag.fcurves):
        if (curve.data_path, curve.array_index) not in channels:
            bag.fcurves.remove(curve)
        else:
            _curve_payload(curve)
    # Empty unrelated slots stay in the draft only as slot identity; never edit
    # or copy their content to the real B.
    for layer in action.layers:
        for strip in layer.strips:
            for other in strip.channelbags:
                if other.slot_handle != item.custom_slot:
                    for curve in list(other.fcurves):
                        other.fcurves.remove(curve)
    start, end = _frame_range(spec)
    present = {(c.data_path, c.array_index) for c in bag.fcurves}
    for path, index in scope.channels:
        if (path, index) not in present:
            _constant_curve(bag, path, index, scope.defaults[(path, index)], start, end)
    if spec['timeMode'] == 'HoldPose':
        _assert_hold_constant(bag.fcurves)
    action.use_frame_range = True
    action.frame_start, action.frame_end = start, end
    return action


def _layout(rig):
    ad = rig.animation_data
    if not ad:
        return []
    return [{'name': track.name, 'mute': track.mute, 'solo': track.is_solo,
        'select': track.select, 'strips': [{'name': strip.name, 'action': strip.action,
            'slot': strip.action_slot_handle, 'select': strip.select,
            'fields': {field: _plain(getattr(strip, field)) for field in _STRIP_FIELDS}}
            for strip in track.strips]} for track in ad.nla_tracks]


def _pose_selection_owner(pb):
    """Use per-object Pose selection, or the supported legacy Bone API."""
    owner = pb if hasattr(pb, 'select') else pb.bone
    # An unknown API must fail before a write, rather than manufacture a
    # Python attribute that Blender would never use for native selection.
    owner.select
    return owner


def _prior(context, rig):
    ua, links = _host('unity_animation'), _host('animation_link')
    ad = rig.animation_data
    return {'snapshot': ua._snapshot(context, rig), 'action': ad.action if ad else None, 'rig_mode': rig.mode,
        'nla': _layout(rig), 'active_track': ad.nla_tracks.active.name if ad and ad.nla_tracks.active else '',
        'active': context.view_layer.objects.active, 'selected': list(context.selected_objects),
        'active_bone': rig.data.bones.active.name if rig.data.bones.active else '',
        'selected_bones': [pb.name for pb in rig.pose.bones if _pose_selection_owner(pb).select],
        'locks': {pb.name: {'location': list(pb.lock_location), 'rotation': list(pb.lock_rotation),
            'scale': list(pb.lock_scale), 'rotation_w': pb.lock_rotation_w,
            'rotation_4d': pb.lock_rotations_4d, 'select': pb.bone.hide_select} for pb in rig.pose.bones},
        'pb_properties': {pb.name: {key: pb[key] for key in pb.keys()
            if type(pb[key]) in (bool, int, float)} for pb in rig.pose.bones},
        'metadata': {key: rig[key] for key in (links.LINK_KEY, links.ACTION_KEY) if key in rig}}


@contextmanager
def _nla_context(context, rig):
    if not context.window or context.window.scene is not context.scene:
        raise ValueError('Composition Tweak editing requires the owning Blender window.')
    area = next((a for a in context.window.screen.areas if a.type == 'NLA_EDITOR'), None)
    area = area or context.area or next(iter(context.window.screen.areas), None)
    if area is None:
        raise ValueError('Open a visible editor before entering composition editing.')
    previous_type = area.type
    previous_selected = list(context.selected_objects)
    previous_active = context.view_layer.objects.active
    try:
        for obj in previous_selected:
            obj.select_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        if area.type != 'NLA_EDITOR':
            area.type = 'NLA_EDITOR'
        space = area.spaces.active
        selected_filter = space.dopesheet.show_only_selected
        space.dopesheet.show_only_selected = True
        try:
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with context.temp_override(window=context.window, area=area, region=region):
                yield
        finally:
            space.dopesheet.show_only_selected = selected_filter
    finally:
        if area.type != previous_type:
            area.type = previous_type
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in previous_selected:
            if context.view_layer.objects.get(obj.name) is obj:
                obj.select_set(True)
        context.view_layer.objects.active = previous_active


def _tweak(context, rig, track, *, enabled):
    ad = rig.animation_data
    if not enabled:
        if not ad.use_tweak_mode:
            return
        with _nla_context(context, rig):
            result = bpy.ops.nla.tweakmode_exit('EXEC_DEFAULT', False, isolate_action=False)
        if result != {'FINISHED'} or ad.use_tweak_mode:
            raise ValueError('Could not exit this composition Tweak session; its draft was kept.')
        return
    if ad.use_tweak_mode:
        raise ValueError('Another NLA Tweak session is active.')
    matches = [current for current in ad.nla_tracks if current == track]
    if len(matches) != 1 or len(track.strips) != 1 or track.strips[0].action is None:
        raise ValueError('Composition Tweak requires its exact owned track and single Action strip.')
    for current in ad.nla_tracks:
        # RNA collection iteration can wrap the same native track in another
        # Python object. RNA equality compares native data; Python identity does not.
        selected = current == track
        current.select = selected
        for strip in current.strips:
            strip.select = selected
    ad.nla_tracks.active = track
    if (ad.nla_tracks.active != track or any(
            current.select != (current == track) or any(
                strip.select != (current == track) for strip in current.strips)
            for current in ad.nla_tracks)):
        raise ValueError('Could not select the exact composition strip for native Tweak editing.')
    with _nla_context(context, rig):
        result = bpy.ops.nla.tweakmode_enter('EXEC_DEFAULT', False, isolate_action=False,
            use_upper_stack_evaluation=True)
    if (result != {'FINISHED'} or not ad.use_tweak_mode
            or ad.action != track.strips[0].action
            or ad.action_slot_handle != track.strips[0].action_slot_handle):
        raise ValueError('Native Full Stack Tweak could not start; no authored Action was changed.')


def _track(ad, name, action, slot, start, end, *, speed=1, phase=0, loop=False, view_end=None, scene_start=1):
    track = ad.nla_tracks.new()
    track.name = name
    strip = track.strips.new(name, math.floor(start), action)
    strip.action_slot = _slot(action, slot)
    strip.action_frame_start, strip.action_frame_end = start, end
    strip.blend_type, strip.influence, strip.extrapolation = 'REPLACE', 1.0, 'HOLD'
    strip.use_auto_blend = strip.use_animated_influence = strip.use_animated_time = False
    strip.use_reverse = strip.use_sync_length = False
    strip.blend_in = strip.blend_out = 0
    strip.scale = 1 / speed
    strip.frame_start = scene_start - phase * (end - start) / speed
    if loop:
        strip.repeat = max(1, ((view_end or end) - strip.frame_start) / ((end - start) / speed))
    return track


def _owned_tracks(rig, session):
    ad = rig.animation_data
    if ad is None:
        raise ValueError('Composition NLA data was removed; keep the saved draft.')
    result = {}
    for role, row in session['tracks'].items():
        track = ad.nla_tracks.get(row['name'])
        if (track is None or len(track.strips) != 1 or track.strips[0].action is not row['action']
                or track.strips[0].action_slot_handle != row['slot']):
            raise ValueError('An owned composition strip was removed or reassigned; no authored data was changed.')
        strip = track.strips[0]
        if any(_plain(getattr(strip, field)) != value for field, value in row['fields'].items()):
            raise ValueError('Composition strip timing/blending changed; return to its declared mapping before committing.')
        result[role] = track
        expected_mute = session['solo'] and (session['edit_target'] == 'B' if role == 'BASE' else session['edit_target'] == 'A')
        if track.mute != expected_mute or track.is_solo:
            raise ValueError('An owned composition track was muted or soloed outside its explicit view controls.')
    if set(t.name for t in ad.nla_tracks) != {row['name'] for row in session['prior']['nla']} | {t.name for t in result.values()}:
        raise ValueError('Unowned NLA tracks were added or removed during composition; the draft was kept.')
    for row in session['prior']['nla']:
        track = ad.nla_tracks.get(row['name'])
        if track is None or len(track.strips) != len(row['strips']) or not track.mute or track.is_solo:
            raise ValueError('An original NLA track changed during composition.')
        for strip, old in zip(track.strips, row['strips']):
            if (strip.name != old['name'] or strip.action is not old['action'] or
                    strip.action_slot_handle != old['slot'] or
                    any(_plain(getattr(strip, field)) != value for field, value in old['fields'].items())):
                raise ValueError('An original NLA strip changed during composition.')
    return result


def validate(context, rig=None):
    rig = rig or _host('animation_worklist').state(context).rig
    if not active(rig):
        raise ValueError('Open Composition for the editable B work item first.')
    session = _load(rig)
    saved = _host('animation_worklist').state(context)
    item = _host('animation_worklist')._item(saved, session['edit_item_id'])
    contract.validate_item(session['spec'], item)
    if (session['scene'] is not context.scene or session['view_layer'] != context.view_layer.name
            or item.rig is not rig or item.custom_action is not session['custom_action']
            or item.custom_slot != session['custom_slot'] or rig.data.name != session['data_name']):
        raise ValueError('Composition target, scene, data or exact B slot changed; its draft was kept.')
    target = _host('animation_worklist_target')
    if (not target.is_adopted(rig) or contract.digest(rig[target.SESSION]) != session['data_session_hash']
            or target._load(rig)['motion_data'] is not rig.data):
        raise ValueError('The exact adopted Armature transaction changed; composition left its recovery data untouched.')
    target.control_rest_view(rig)
    if _motion_hash(session['base_action'], session['base_slot']) != session['base_hash']:
        raise ValueError('The protected A reference changed during composition; B was kept.')
    if _motion_hash(item.custom_action, item.custom_slot) != session['custom_hash']:
        raise ValueError('B Custom was edited outside its composition draft; resolve both copies before committing.')
    if session['edit_action'].get(OWNER) != session['owner']:
        raise ValueError('The composition edit Action lost its native ownership record.')
    if session['base_edit_action']:
        base_item = _host('animation_worklist')._item(saved, session['base_edit_item_id'])
        if (base_item.custom_action is not session['base_custom_action']
                or base_item.custom_slot != session['base_edit_slot']
                or _motion_hash(base_item.custom_action, base_item.custom_slot) != session['base_custom_hash']
                or session['base_edit_action'].get(OWNER) != session['owner'] + ':base'):
            raise ValueError('The explicitly selected A work copy or slot changed outside composition; both drafts were kept.')
    if session.get('following_hold'):
        hold = session['following_hold']
        actual = _host('animation_worklist')._item(saved, hold['item_id'])
        if (actual.custom_action is not hold['custom_action'] or actual.custom_slot != hold['slot']
                or contract.identity(item_spec(actual)) != hold['spec_hash']
                or _motion_hash(actual.custom_action, actual.custom_slot) != hold['custom_hash']
                or _motion_hash(hold['action'], hold['slot']) != hold['action_hash']
                or hold['action'].get(OWNER) != session['owner'] + ':hold'):
            raise ValueError('The real following Hold changed; Raise was kept without silently changing its transition.')
    allowed = {tuple(c) for c in session['scope_channels']}
    if {(c.data_path, c.array_index) for c in _curves(session['edit_action'], session['edit_slot'])} != allowed:
        raise ValueError('The composition draft contains missing or out-of-scope channels; no data was dropped.')
    for curve in _curves(session['edit_action'], session['edit_slot']):
        _curve_payload(curve)
    packet = _host('unity_animation').load_package(session['packet_path'])
    if packet['_sha256'] != session['packet_sha256']:
        raise ValueError('The B packet changed; its composition draft was kept.')
    scope = _service('animation_composition_export').validate_scope(context, rig, session['spec'], packet)
    if _channel_rows(scope.channels) != session['scope_channels'] or contract.digest(scope.proof) != session['scope_proof']:
        raise ValueError('The validated B control scope changed; no channels were written.')
    tracks = _owned_tracks(rig, session)
    if rig.animation_data.use_tweak_mode:
        role = 'EDIT' if session['edit_target'] == 'B' else 'BASE'
        if role not in tracks or rig.animation_data.action is not session['tracks'][role]['action']:
            raise ValueError('Another Action is being tweaked; the composition draft was kept.')
    return session, item, scope, tracks


def enter(context, spec, item_id, base_action=None, base_slot=None, *, packet=None):
    worklist, ua = _host('animation_worklist'), _host('unity_animation')
    worklist._idle(context)
    saved, item = worklist.state(context), worklist._item(worklist.state(context), item_id)
    rig = item.rig
    spec = contract.validate_item(spec, item)
    current_spec = item_spec(item)
    if current_spec is None or spec != current_spec:
        raise ValueError('Open this work item\'s authenticated composition. Another A/configuration requires its separately prepared variant.')
    if (active(rig) or rig.mode == 'EDIT' or context.mode not in {'OBJECT', 'POSE'}
            or item.custom_action is None or item.custom_action.library
            or any(obj.animation_data and obj.animation_data.use_tweak_mode for obj in context.scene.objects)):
        raise ValueError('Finish the existing preview/Tweak/edit session before opening Composition.')
    target = _host('animation_worklist_target')
    target.ensure_active(context, rig)
    if not target.is_adopted(rig):
        raise ValueError('Use Main Rig first to retain the exact protected/current Armature identity for composition.')
    worklist._check_baseline(item)
    if base_action is None:
        base_action, base_slot = prepare_reference(context, item, spec)
    if base_action is None or base_action is item.custom_action:
        raise ValueError('Choose a separate protected A reference Action and exact slot.')
    links = _host('animation_link')
    if (base_action.get(links.PACKAGE_HASH_KEY, '').lower() != spec['referencePacketSha256']
            or Path(base_action.get(links.PACKAGE_KEY, '')).resolve() != Path(spec['referencePacketFile']).resolve()):
        raise ValueError('Choose the authenticated A Action from its exact reference packet, not a similarly named Action.')
    _bag(base_action, base_slot)
    _bag(item.custom_action, item.custom_slot)
    pinned_packet = _packet_for_item(item)
    if packet is not None and contract.digest(packet) != contract.digest(pinned_packet):
        raise ValueError('The supplied B packet differs from its authenticated native Action reference.')
    packet = pinned_packet
    reference = ua.load_package(spec['referencePacketFile'])
    if (reference['_sha256'] != spec['referencePacketSha256'] or
            reference.get('clipGuid', '').lower() != spec['baseClipGuid'] or
            reference.get('clipLocalId') != spec['baseClipLocalId']):
        raise ValueError('The selected protected A packet/version differs from Composition.')
    if ua.native_action_frame_rate(base_action) != reference['sampleRate']:
        raise ValueError('The selected protected A has no proved native packet clock.')
    context.view_layer.update()
    ua._mapping(rig, reference, context.scene.unit_settings.scale_length)
    scope = _service('animation_composition_export').resolve_scope(context, rig, spec, packet)
    following = _following_hold(context, rig, spec, scope)
    owner = str(uuid.uuid4())
    prior = _prior(context, rig)
    action = hold_action = None
    tracks = []
    try:
        action = _editing_action(rig, item, spec, scope, owner)
        session = {'version': VERSION, 'owner': owner, 'rig': rig, 'scene': context.scene,
            'view_layer': context.view_layer.name, 'data_name': rig.data.name, 'spec': spec,
            'data_session_hash': contract.digest(rig[target.SESSION]),
            'edit_item_id': item.item_id, 'base_action': base_action, 'base_slot': base_slot,
            'base_hash': _motion_hash(base_action, base_slot), 'custom_action': item.custom_action,
            'custom_slot': item.custom_slot, 'custom_hash': _motion_hash(item.custom_action, item.custom_slot),
            'edit_action': action, 'edit_slot': item.custom_slot, 'edit_initial_hash': _motion_hash(action, item.custom_slot),
            'scope_channels': _channel_rows(scope.channels),
            'scope_defaults': [[path, index, scope.defaults[(path, index)]] for path, index in scope.channels],
            'scope_proof': contract.digest(scope.proof), 'packet_path': packet['_path'],
            'packet_sha256': packet['_sha256'], 'prior': prior, 'tracks': {},
            'edit_target': 'B', 'solo': False, 'base_edit_item_id': '', 'base_edit_action': None,
            'base_edit_slot': 0, 'base_custom_action': None, 'base_custom_hash': '', 'base_edit_initial_hash': ''}
        session['following_hold'] = None
        if following:
            hold_action = _editing_action(rig, following['item'], following['spec'], following['scope'], owner + ':hold')
            session['following_hold'] = {'item_id': following['item'].item_id,
                'custom_action': following['item'].custom_action, 'slot': following['item'].custom_slot,
                'custom_hash': following['custom_hash'], 'spec_hash': contract.identity(following['spec']),
                'action': hold_action, 'action_hash': _motion_hash(hold_action, following['item'].custom_slot)}
        ua._set_playing(context, False)
        ad = rig.animation_data_create()
        for track in ad.nla_tracks:
            track.mute, track.is_solo = True, False
        ad.action, ad.use_nla = None, True
        astart, aend = map(float, base_action.frame_range)
        if aend <= astart:
            raise ValueError('Protected A requires a positive timeline range.')
        bstart, bend = _frame_range(spec)
        arate = base_action.get('unity_sample_rate')
        if type(arate) not in (int, float) or not math.isfinite(arate) or not 1 <= arate <= 240:
            raise ValueError('The exact A reference has no authenticated native sampling rate.')
        aspeed = spec['referenceSpeed'] * arate / spec['fps']
        view_end = max((aend - astart) / aspeed + 1, (bend - bstart) / spec['editSpeed'] + 1, prior['snapshot']['end'])
        scene_fps = context.scene.render.fps / context.scene.render.fps_base
        current_seconds = (prior['snapshot']['frame'] - 1) / scene_fps
        context.scene.render.fps = max(1, round(spec['fps']))
        context.scene.render.fps_base = context.scene.render.fps / spec['fps']
        context.scene.frame_start = context.scene.frame_preview_start = 1
        context.scene.frame_end = context.scene.frame_preview_end = math.ceil(view_end)
        context.scene.use_preview_range = True
        base = _track(ad, 'Composition · Base', base_action, base_slot, astart, aend,
            speed=aspeed, phase=spec['referencePhase'], loop=True, view_end=view_end)
        tracks.append(base)
        edit = _track(ad, 'Composition · Edit', action, item.custom_slot, bstart, bend,
            speed=spec['editSpeed'], phase=spec['editPhase'] if spec['timeMode'] != 'HoldPose' else 0,
            loop=spec['timeMode'] == 'HoldLoop', view_end=view_end)
        tracks.append(edit)
        roles = [('BASE', base), ('EDIT', edit)]
        if following:
            edit.strips[0].extrapolation = 'NOTHING'
            hspec, hi = following['spec'], following['item']
            hs, he = _frame_range(hspec)
            hspeed = hspec['editSpeed'] * hspec['fps'] / spec['fps']
            transition = edit.strips[0].frame_end
            phase = hspec['editPhase'] % 1 if hspec['timeMode'] == 'HoldLoop' else 0
            if phase:
                partial = _track(ad, 'Composition · Following Hold Phase', hold_action, hi.custom_slot,
                    hs + phase * (he - hs), he, speed=hspeed, scene_start=transition)
                partial.strips[0].extrapolation = 'NOTHING'
                tracks.append(partial)
                roles.append(('HOLD_PHASE', partial))
                transition = partial.strips[0].frame_end
            hold_track = _track(ad, 'Composition · Following Hold', hold_action, hi.custom_slot,
                hs, he, speed=hspeed, loop=hspec['timeMode'] == 'HoldLoop', scene_start=transition,
                view_end=max(view_end, transition + (he - hs) / hspeed))
            hold_track.strips[0].extrapolation = 'HOLD_FORWARD'
            tracks.append(hold_track)
            roles.append(('HOLD', hold_track))
        for role, track in roles:
            strip = track.strips[0]
            session['tracks'][role] = {'name': track.name, 'action': strip.action,
                'slot': strip.action_slot_handle, 'fields': {f: _plain(getattr(strip, f)) for f in _STRIP_FIELDS}}
        context.scene.tool_settings.use_keyframe_insert_auto = False
        _store(rig, session)
        ua._set_frame(context.scene, 1 + current_seconds * spec['fps'])
        _restrict_selection(rig, session, enabled=True)
        _tweak(context, rig, edit, enabled=True)
        saved.active_index = next(i for i, row in enumerate(saved.items) if row.item_id == item.item_id)
        item.side = 'CUSTOM'
        saved.status, saved.has_error = 'Composition · Base: ' + base_action.name + ' · Editing: ' + item.name, False
        context.view_layer.update()
        return action
    except Exception:
        if rig.animation_data and rig.animation_data.use_tweak_mode:
            _tweak(context, rig, None, enabled=False)
        if rig.animation_data:
            for track in tracks:
                rig.animation_data.nla_tracks.remove(track)
        _restore_prior(context, rig, prior)
        rig.pop(SESSION, None)
        rig.pop(REFS, None)
        for draft in (action, hold_action):
            if draft and draft.users <= 1:
                draft.use_fake_user = False
                if draft.users == 0:
                    bpy.data.actions.remove(draft)
        raise


def _restrict_selection(rig, session, *, enabled):
    channels = session['scope_channels']
    if session['edit_target'] == 'A':
        channels = [(curve.data_path, curve.array_index) for curve in
                    _curves(session['base_edit_action'], session['base_edit_slot'])
                    if session['solo'] or (curve.data_path, curve.array_index) not in set(map(tuple, channels))]
    for pb in rig.pose.bones:
        permitted = any(path.startswith(pb.path_from_id()) for path, _index in channels)
        if enabled and not permitted:
            pb.bone.hide_select = True
            _pose_selection_owner(pb).select = False
        elif enabled:
            pb.bone.hide_select = session['prior']['locks'][pb.name]['select']


def _restore_prior(context, rig, prior):
    ua, links = _host('unity_animation'), _host('animation_link')
    ad = rig.animation_data
    if ad:
        for row in prior['nla']:
            track = ad.nla_tracks.get(row['name'])
            if track is None:
                raise ValueError('An original NLA track is missing; recovery was not forced.')
            track.mute, track.is_solo, track.select = row['mute'], row['solo'], row['select']
            for strip, old in zip(track.strips, row['strips']):
                strip.select = old['select']
        if prior['active_track']:
            ad.nla_tracks.active = ad.nla_tracks.get(prior['active_track'])
    for pb in rig.pose.bones:
        row = prior['locks'][pb.name]
        pb.lock_location, pb.lock_rotation, pb.lock_scale = row['location'], row['rotation'], row['scale']
        pb.lock_rotation_w, pb.lock_rotations_4d = row['rotation_w'], row['rotation_4d']
        pb.bone.hide_select = row['select']
        for key, value in prior['pb_properties'][pb.name].items():
            pb[key] = value
    for key in (links.LINK_KEY, links.ACTION_KEY):
        if key in prior['metadata']:
            rig[key] = prior['metadata'][key]
        else:
            rig.pop(key, None)
    ua._restore_snapshot(context, rig, prior['snapshot'], prior['action'])
    if rig.mode != prior['rig_mode']:
        with context.temp_override(object=rig, active_object=rig,
                selected_objects=[rig], selected_editable_objects=[rig]):
            if bpy.ops.object.mode_set(mode=prior['rig_mode']) != {'FINISHED'}:
                raise ValueError('The original Pose/Object mode could not be restored; the native draft was retained.')
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in prior['selected']:
        if context.view_layer.objects.get(obj.name) is obj:
            obj.select_set(True)
    context.view_layer.objects.active = prior['active']
    # Active assignment and mode recovery can select a bone. Restore the
    # exact per-object flags last, including an active but deselected bone.
    rig.data.bones.active = rig.data.bones.get(prior['active_bone']) if prior['active_bone'] else None
    for pb in rig.pose.bones:
        _pose_selection_owner(pb).select = pb.name in prior['selected_bones']
    context.view_layer.update()


def commit(context, rig=None):
    """Preflight both actions, then replace only exact B-owned selected-slot curves."""
    session, item, scope, tracks = validate(context, rig)
    rig = session['rig']
    if session['edit_target'] != 'B':
        return _commit_base(context, session)
    current = _motion_hash(session['edit_action'], session['edit_slot'])
    if current == session['edit_initial_hash']:
        return False
    bag = _bag(item.custom_action, item.custom_slot)
    channels = set(scope.channels)
    if session['spec']['timeMode'] == 'HoldPose':
        _assert_hold_constant(_curves(session['edit_action'], session['edit_slot']))
    pending = [_curve_payload(curve) for curve in _curves(session['edit_action'], session['edit_slot'])]
    previous = [_curve_payload(curve) for curve in bag.fcurves if (curve.data_path, curve.array_index) in channels]
    outside = {(curve.data_path, curve.array_index): _curve_payload(curve) for curve in bag.fcurves
               if (curve.data_path, curve.array_index) not in channels}
    try:
        for curve in list(bag.fcurves):
            if (curve.data_path, curve.array_index) in channels:
                bag.fcurves.remove(curve)
        for row in pending:
            _write_curve(bag, row)
        if outside != {(curve.data_path, curve.array_index): _curve_payload(curve) for curve in bag.fcurves
                       if (curve.data_path, curve.array_index) not in channels}:
            raise ValueError('A B-domain commit touched protected out-of-scope curves.')
        if _motion_hash(session['base_action'], session['base_slot']) != session['base_hash']:
            raise ValueError('Protected A changed while writing B; the B draft was kept.')
        session['custom_hash'] = _motion_hash(item.custom_action, item.custom_slot)
        session['edit_initial_hash'] = current
        _store(rig, session)
        return True
    except Exception:
        for curve in list(bag.fcurves):
            if (curve.data_path, curve.array_index) in channels:
                bag.fcurves.remove(curve)
        for row in previous:
            _write_curve(bag, row)
        raise


def _commit_base(context, session):
    worklist = _host('animation_worklist')
    item = worklist._item(worklist.state(context), session['base_edit_item_id'])
    draft, handle = session['base_edit_action'], session['base_edit_slot']
    current = _motion_hash(draft, handle)
    if current == session['base_edit_initial_hash']:
        return False
    if item.custom_action is session['base_action']:
        raise ValueError('The protected A reference cannot become its own editable work copy.')
    bag = _bag(item.custom_action, handle)
    pending = [_curve_payload(c) for c in _curves(draft, handle)]
    previous = [_curve_payload(c) for c in bag.fcurves]
    try:
        for curve in list(bag.fcurves):
            bag.fcurves.remove(curve)
        for row in pending:
            _write_curve(bag, row)
        if _motion_hash(session['base_action'], session['base_slot']) != session['base_hash']:
            raise ValueError('A writeback changed the protected Source reference.')
        session['base_custom_hash'] = _motion_hash(item.custom_action, handle)
        session['base_edit_initial_hash'] = current
        _store(session['rig'], session)
        return True
    except Exception:
        for curve in list(bag.fcurves):
            bag.fcurves.remove(curve)
        for row in previous:
            _write_curve(bag, row)
        raise


def set_edit_target(context, target, *, base_item_id='', rig=None):
    """An explicit A work-copy identity is required; Source A is never edited."""
    if target not in {'A', 'B'}:
        raise ValueError('Choose the explicit A or B editing target.')
    session, item, scope, tracks = validate(context, rig)
    rig = session['rig']
    if target == session['edit_target']:
        return session['edit_action'] if target == 'B' else session['base_edit_action']
    worklist, links = _host('animation_worklist'), _host('animation_link')
    base_item = None
    if target == 'A':
        base_item = worklist._item(worklist.state(context), base_item_id or session['base_edit_item_id'])
        if (base_item.custom_action is None or base_item.custom_action.library
                or base_item.custom_action is session['base_action']
                or base_item.custom_action is item.custom_action
                or base_item.custom_action.get(links.PACKAGE_HASH_KEY, '').lower() != session['spec']['referencePacketSha256']):
            raise ValueError('Choose the separate local Custom work item for the exact protected A packet.')
        worklist._check_baseline(base_item)
        _bag(base_item.custom_action, base_item.custom_slot)
        for curve in _curves(base_item.custom_action, base_item.custom_slot):
            _curve_payload(curve)
        if tuple(map(float, base_item.custom_action.frame_range)) != tuple(map(float, session['base_action'].frame_range)):
            raise ValueError('The explicit A work copy changed its declared cycle duration; prepare that reference revision first.')
        if session['base_edit_action'] and base_item.item_id != session['base_edit_item_id']:
            raise ValueError('This composition already retained another A draft; finish it before choosing another A work copy.')
    commit(context, rig)
    session = _load(rig)
    previous = _load(rig)
    was_tweaked = rig.animation_data.use_tweak_mode
    created = None
    if target == 'A' and not session['base_edit_action']:
        draft = base_item.custom_action.copy()
        created = draft
        draft.name = base_item.name + ' · Composition Base Edit'
        draft.use_fake_user = True
        worklist._clean_action(draft)
        draft[OWNER] = session['owner'] + ':base'
        for curve in _curves(draft, base_item.custom_slot):
            _curve_payload(curve)
        session.update(base_edit_item_id=base_item.item_id, base_edit_action=draft,
            base_edit_slot=base_item.custom_slot, base_custom_action=base_item.custom_action,
            base_custom_hash=_motion_hash(base_item.custom_action, base_item.custom_slot),
            base_edit_initial_hash=_motion_hash(draft, base_item.custom_slot))
    try:
        _tweak(context, rig, None, enabled=False)
        strip = tracks['BASE'].strips[0]
        if session['base_edit_action']:
            strip.action = session['base_edit_action']
            strip.action_slot = _slot(session['base_edit_action'], session['base_edit_slot'])
            session['tracks']['BASE']['action'] = session['base_edit_action']
            session['tracks']['BASE']['slot'] = session['base_edit_slot']
            session['tracks']['BASE']['fields'] = {f: _plain(getattr(strip, f)) for f in _STRIP_FIELDS}
        session['edit_target'], session['solo'] = target, False
        for track in tracks.values():
            track.mute = False
        _store(rig, session)
        _restrict_selection(rig, session, enabled=True)
        _tweak(context, rig, tracks['BASE' if target == 'A' else 'EDIT'], enabled=True)
        return session['base_edit_action'] if target == 'A' else session['edit_action']
    except Exception:
        _tweak(context, rig, None, enabled=False)
        row = previous['tracks']['BASE']
        strip = tracks['BASE'].strips[0]
        strip.action, strip.action_slot = row['action'], _slot(row['action'], row['slot'])
        for field, value in row['fields'].items():
            setattr(strip, field, value)
        for role, track in tracks.items():
            track.mute = previous['solo'] and (previous['edit_target'] == 'B' if role == 'BASE' else previous['edit_target'] == 'A')
        _store(rig, previous)
        _restrict_selection(rig, previous, enabled=True)
        if was_tweaked:
            _tweak(context, rig, tracks['BASE' if previous['edit_target'] == 'A' else 'EDIT'], enabled=True)
        if created and created.users <= 1:
            created.use_fake_user = False
            if created.users == 0:
                bpy.data.actions.remove(created)
        raise


def capture_hold_pose(context, rig=None):
    """One explicit Undo operator writes the complete constant pose, never C matrices."""
    session, item, scope, tracks = validate(context, rig)
    if session['edit_target'] != 'B' or session['spec']['timeMode'] != 'HoldPose':
        raise ValueError('Capture Hold Pose is available only while editing the constant B pose.')
    rig, action = session['rig'], session['edit_action']
    if not rig.animation_data or not rig.animation_data.use_tweak_mode:
        raise ValueError('Resume Editing before saving or syncing this B pose.')
    # Read direct local input properties owned by B; parent/world results are not
    # animation data and are never subtracted or baked back into this draft.
    values = {}
    for path, index in scope.channels:
        value = rig.path_resolve(path)
        value = value[index] if hasattr(value, '__len__') else value
        if type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError('A selected Hold control has no finite B-owned local value.')
        values[path, index] = value
    bag, previous = _bag(action, session['edit_slot']), [_curve_payload(c) for c in _curves(action, session['edit_slot'])]
    start, end = _frame_range(session['spec'])
    try:
        for curve in list(bag.fcurves):
            bag.fcurves.remove(curve)
        for (path, index), value in values.items():
            _constant_curve(bag, path, index, value, start, end)
        context.view_layer.update()
        return action
    except Exception:
        for curve in list(bag.fcurves):
            bag.fcurves.remove(curve)
        for row in previous:
            _write_curve(bag, row)
        raise


def set_solo(context, enabled, rig=None):
    session, item, scope, tracks = validate(context, rig)
    if type(enabled) is not bool:
        raise ValueError('Composition Solo must be an explicit view choice.')
    for role, track in tracks.items():
        track.mute = enabled and (session['edit_target'] == 'B' if role == 'BASE' else session['edit_target'] == 'A')
    session['solo'] = enabled
    _store(session['rig'], session)
    _restrict_selection(session['rig'], session, enabled=True)
    context.view_layer.update()


def resume(context, rig=None):
    """Explicit recovery after load; no load handler starts playback or edits."""
    session, item, scope, tracks = validate(context, rig)
    if not session['rig'].animation_data.use_tweak_mode:
        role = 'EDIT' if session['edit_target'] == 'B' else 'BASE'
        _tweak(context, session['rig'], tracks[role], enabled=True)
    _restrict_selection(session['rig'], session, enabled=True)
    return session['edit_action'] if session['edit_target'] == 'B' else session['base_edit_action']


def exit(context, rig=None, *, writeback=True):
    session, item, scope, tracks = validate(context, rig)
    rig = session['rig']
    if writeback:
        commit(context, rig)
        session = _load(rig)
    recovery = _prior(context, rig)
    was_tweaked = rig.animation_data.use_tweak_mode
    old_drafts = dict(rig.get(DRAFTS, {}))
    _tweak(context, rig, None, enabled=False)
    try:
        # Restore before disposing native tracks so a failed context/mode
        # recovery still has the original authenticated NLA data to return to.
        _restore_prior(context, rig, session['prior'])
        for track in tracks.values():
            rig.animation_data.nla_tracks.remove(track)
        drafts = dict(old_drafts)
        drafts[session['owner']] = session['edit_action']
        if session['base_edit_action']:
            drafts[session['owner'] + ':base'] = session['base_edit_action']
        rig[DRAFTS] = drafts
        rig.pop(SESSION, None)
        rig.pop(REFS, None)
        context.view_layer.update()
        return item.custom_action
    except Exception:
        # Recreate only missing exact owned tracks; never repair an unowned
        # track. The saved session is republished before returning to Tweak.
        ad = rig.animation_data_create()
        restored = {}
        missing = any(ad.nla_tracks.get(row['name']) is None for row in session['tracks'].values())
        if missing:
            # Rebuild the exact role order after a partial removal. Keeping a
            # surviving Edit above a newly appended Base would invert C.
            for row in session['tracks'].values():
                track = ad.nla_tracks.get(row['name'])
                if track is not None:
                    if (len(track.strips) != 1 or track.strips[0].action is not row['action']
                            or track.strips[0].action_slot_handle != row['slot']
                            or any(_plain(getattr(track.strips[0], field)) != value
                                   for field, value in row['fields'].items())):
                        raise ValueError('An owned track changed during Close recovery; its saved draft was kept without forcing that track.')
            for row in session['tracks'].values():
                track = ad.nla_tracks.get(row['name'])
                if track is not None:
                    ad.nla_tracks.remove(track)
        for role, row in session['tracks'].items():
            track = ad.nla_tracks.get(row['name'])
            if track is None:
                fields = row['fields']
                track = _track(ad, row['name'], row['action'], row['slot'],
                    fields['action_frame_start'], fields['action_frame_end'],
                    speed=1. / fields['scale'], scene_start=fields['frame_start'])
                strip = track.strips[0]
                for field in _STRIP_FIELDS:
                    if field not in ('frame_start', 'frame_end'):
                        setattr(strip, field, fields[field])
                strip.frame_start, strip.frame_end = fields['frame_start'], fields['frame_end']
            restored[role] = track
        if old_drafts:
            rig[DRAFTS] = old_drafts
        else:
            rig.pop(DRAFTS, None)
        _store(rig, session)
        _restore_prior(context, rig, recovery)
        ad.action, ad.use_nla = None, True
        if was_tweaked:
            _tweak(context, rig, restored['BASE' if session['edit_target'] == 'A' else 'EDIT'], enabled=True)
        context.view_layer.update()
        raise


@contextmanager
def isolated_sync(context, rig=None):
    """Commit B, leave Tweak, then restore C on every worker launch outcome.

    The yielded isolated export inputs are owned by the export service, which
    must retain its snapshot until native worker completion/disposal.
    """
    session, item, scope, tracks = validate(context, rig)
    if session['edit_target'] != 'B':
        raise ValueError('Choose Edit B before syncing its isolated upper-body Action.')
    rig = session['rig']
    commit(context, rig)
    session = _load(rig)
    was_tweaked = rig.animation_data.use_tweak_mode
    _tweak(context, rig, None, enabled=False)
    try:
        with _service('animation_composition_export').isolate_export(context, session, item) as inputs:
            yield inputs
    finally:
        if was_tweaked and active(rig) and not rig.animation_data.use_tweak_mode:
            restored, _item, _scope, restored_tracks = validate(context, rig)
            role = 'EDIT' if restored['edit_target'] == 'B' else 'BASE'
            _tweak(context, rig, restored_tracks[role], enabled=True)
        context.view_layer.update()


def _export_session(context, item):
    if active(item.rig):
        session, selected, _scope, _tracks = validate(context, item.rig)
        if selected.item_id != item.item_id or session['edit_target'] != 'B':
            raise ValueError('Choose this composition\'s Edit B before syncing.')
        return session
    spec = item_spec(item)
    base, slot = prepare_reference(context, item, spec)
    packet = _packet_for_item(item)
    scope = _service('animation_composition_export').resolve_scope(context, item.rig, spec, packet)
    return {'rig': item.rig, 'scene': context.scene, 'spec': spec,
        'custom_action': item.custom_action, 'custom_slot': item.custom_slot,
        'base_action': base, 'base_slot': slot, 'base_hash': _motion_hash(base, slot),
        'packet_path': packet['_path'], 'packet_sha256': packet['_sha256'],
        'scope_proof': contract.digest(scope.proof), 'scope_channels': _channel_rows(scope.channels)}


def begin_sync(context, item=None, *, collection=False, collection_proof=None):
    """Freeze isolated B and config proof before launching a disposable worker."""
    worklist, links = _host('animation_worklist'), _host('animation_link')
    worklist._idle(context, _collection=collection)
    item = item or worklist.current(context)
    if item is None or item_spec(item) is None:
        raise ValueError('Choose an explicit prepared composition B work item.')
    worklist._check_baseline(item)
    saved = worklist.state(context)
    if active(item.rig):
        session, selected, _scope, _tracks = validate(context, item.rig)
        if selected.item_id != item.item_id or session['edit_target'] != 'B':
            raise ValueError('Choose this composition\'s Edit B before syncing.')
        if not collection and session['spec']['timeMode'] == 'HoldPose':
            capture_hold_pose(context, item.rig)
        commit(context, item.rig)
    catalog = worklist._fresh_workspace(saved, item)
    new = _host('animation_worklist_new') if item.new_animation else None
    if new:
        captured = new.validate(saved, item, catalog)
        directory = Path(item.manifest_path).parent / 'revisions'
    else:
        clip = next((entry for entry in catalog['clips'] if worklist._clip_key(entry) == item.clip_key), None)
        if clip is None or clip.get('needsRebuild') or not clip.get('linkManifest'):
            raise ValueError('Prepare the exact composition B Link in Unity before Sync; its edits were retained.')
        captured = links.load_link(item.manifest_path)
        worklist._validate_route(saved, clip, captured, item)
        directory = Path(item.manifest_path).parent / 'blender_revisions' / uuid.UUID(captured['linkId']).hex
    if contract.identity(captured.get('composition')) != contract.identity(item_spec(item)):
        raise ValueError('The prepared composition configuration changed; refresh explicitly before Sync.')
    source_model = Path(item.model_file)
    if not source_model.is_file() or links._sha256(source_model) != item.model_sha256:
        raise ValueError('The exact B model changed; no historical model identity was replaced to pass Sync.')
    session = _export_session(context, item)
    collector = _host('animation_worklist_collection')
    proof, implementation = collector.item_fingerprint(context, item), collector.export_implementation()
    if collection_proof is not None and (not collection or not collection_proof(proof)):
        raise ValueError('The composition launch lost its collection proof owner.')
    if not proof.get('fingerprint_known'):
        raise ValueError(proof.get('proof_reason') or 'Composition B change proof is incomplete.')
    if collector.unchanged_proof(item, captured, implementation, proof):
        raise ValueError('B and its composition configuration match the last successful publication.')
    original_scene, window_manager = context.scene, context.window_manager
    rig, action, slot = item.rig, item.custom_action, item.custom_slot
    source_action_name = action.name
    native_hash = _motion_hash(action, slot)
    link_identity = worklist._encoded(captured)
    blend_file = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ''
    lock = links._LinkLock(Path(item.manifest_path))
    job = None
    try:
        latest = new.load(item.manifest_path) if new else links.load_link(item.manifest_path)
        if worklist._encoded(latest) != link_identity:
            raise ValueError('The B publication changed before worker launch; retry explicitly.')
        directory.mkdir(parents=True, exist_ok=True)
        destination = _host('animation_worklist_new')._destination(directory)
        service = _service('animation_composition_export')
        launch_proof = {'_worklist_fingerprint': proof, '_worklist_composition': session['spec'],
            '_worklist_composition_metadata': proof['composition_metadata'],
            '_worklist_export_implementation': implementation}
        if active(rig):
            with isolated_sync(context, rig) as inputs:
                job = service.begin_isolated_export(context, inputs, destination, item.export_rig_name, launch_proof=launch_proof)
        else:
            with service.isolate_export(context, session, item) as inputs:
                job = service.begin_isolated_export(context, inputs, destination, item.export_rig_name, launch_proof=launch_proof)
    except Exception:
        if job is not None:
            _service('animation_composition_export').cancel_isolated_export(job)
        lock.release()
        raise

    def publish(_job, result):
        current = worklist._item(original_scene.character_designer_animation_worklist, item.item_id)
        if (current.rig is not rig or current.custom_action is not action or current.custom_slot != slot
                or _motion_hash(action, slot) != native_hash or contract.identity(item_spec(current)) != contract.identity(session['spec'])
                or _motion_hash(session['base_action'], session['base_slot']) != session['base_hash']):
            raise ValueError('B/reference/configuration changed during export; the immutable candidate was retained without advancing its publication.')
        if new:
            return new.publish(item.manifest_path, captured, result, blend_file=blend_file, source_action=source_action_name,
                composition_metadata=proof['composition_metadata'], composition_export=job['composition_export'])
        return links._revision_result(Path(item.manifest_path), captured, result, blend_file=blend_file,
            source_action=source_action_name, model_file=item.model_file, model_sha256=item.model_sha256)

    job['_publish_callback'], job['_dispose_callback'] = publish, lock.release
    job['_worklist_scene'], job['_worklist_item_id'] = original_scene, item.item_id
    job['_worklist_fingerprint'], job['_worklist_export_implementation'] = proof, implementation
    job['_worklist_composition'] = session['spec']
    job['_worklist_composition_export'] = job['composition_export']
    job['_worklist_composition_metadata'] = proof['composition_metadata']
    job['_composition_window_manager'] = window_manager
    bpy.app.timers.register(_poll_sync, first_interval=.25)
    saved.status, saved.has_error = 'Syncing overlay · ' + item.name + '. The result remains a preview.', False
    return job


def _poll_sync():
    job = _host('animation_export').active_job()
    if job is None or not job.get('composition_job'):
        return None
    scene = job.get('_worklist_scene')
    try:
        result = _service('animation_composition_export').poll_isolated_export(job)
        if result is None:
            return .25
        _host('animation_worklist_collection').export_finished(job, result=result)
        if scene in tuple(bpy.data.scenes):
            scene.character_designer_animation_worklist.status = 'Synced overlay. Preview and Apply its prepared composition in Unity.'
            scene.character_designer_animation_worklist.has_error = False
    except Exception as error:
        _host('animation_worklist_collection').export_finished(job, error=str(error))
        if scene in tuple(bpy.data.scenes):
            scene.character_designer_animation_worklist.status = str(error)
            scene.character_designer_animation_worklist.has_error = True
    return None


def cancel_sync(context):
    job = _host('animation_export').active_job()
    if job is None or not job.get('composition_job'):
        raise ValueError('No composition worker is active.')
    result = _service('animation_composition_export').cancel_isolated_export(job)
    _host('animation_worklist_collection').export_finished(job, error='Composition Sync cancelled. The overlay draft and earlier publication are retained.')
    return result


def load_weapon(context):
    session, _item, scope, _tracks = validate(context)
    packet = _host('unity_animation').load_package(session['packet_path'])
    if packet['_sha256'] != session['packet_sha256']:
        raise ValueError('The B bind packet changed before weapon context loading.')
    return _service('animation_composition_weapon').add_context(context, session['rig'], session['spec'], packet, scope)


def remove_weapon(context):
    rig = _host('animation_worklist').state(context).rig
    if rig is None:
        raise ValueError('Choose the owning Main Rig before removing its weapon context.')
    return _service('animation_composition_weapon').remove_context(context, rig)


def register_b(context, template_item_id=''):
    """Explicit UI creation from a prepared role/bind template and new local Action."""
    worklist = _host('animation_worklist')
    worklist._idle(context)
    saved = worklist.state(context)
    template = worklist._item(saved, template_item_id) if template_item_id else worklist.current(context)
    if template is None or item_spec(template) is None or active(saved.rig):
        raise ValueError('Choose a prepared B template and close Composition before registering a separate new Action.')
    worklist._check_baseline(template)
    spec = item_spec(template)
    packet = _packet_for_item(template)
    spec['editWorkItemId'], spec['editNewActionId'] = str(uuid.uuid4()), str(uuid.uuid4())
    if spec['timeMode'] != 'Raise':
        spec['compositionId'] = str(uuid.uuid4())
    # IDs become concrete in the new manifest/creation folder owned by this
    # explicit operation. A/source baseline is not invented for the new B.
    return _host('animation_worklist_new').register(context, composition=spec, packet_path=packet['_path'])


def register_runtime():
    # Native .blend persistence/Undo own the session. File loading must never
    # resume playback, reimport an Action, or automatically mutate artist data.
    return None


def unregister_runtime():
    if bpy.app.timers.is_registered(_poll_sync):
        bpy.app.timers.unregister(_poll_sync)
    job = _host('animation_export').active_job()
    if job and job.get('composition_job'):
        _service('animation_composition_export').cancel_isolated_export(job)
    # Persistent composition Actions/refs are deliberately retained.
